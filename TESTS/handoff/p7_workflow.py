"""Phase 7 harness: read GitHub Actions workflows and simulate which jobs and steps run (CONTRACT P7 v1).

A small evaluator for the expression subset the repository's workflows use (contexts, literals,
! && || == != < <= > >=, parentheses, status functions, startsWith/endsWith/contains/format/
toJSON/fromJSON) with GitHub's loose equality (case-insensitive strings, numeric coercion otherwise)
and GitHub's implicit success() for a job condition without a status function.

simulate(workflow, context, overrides) walks the job graph in dependency order: a job whose
condition holds 'succeeds', otherwise it is 'skipped'; overrides force a result (failure,
cancelled, skipped) to model a job that ran and failed. Steps are evaluated with step_enabled().
"""
import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github/workflows'
STATUS_FUNCTIONS = ('success', 'failure', 'always', 'cancelled')


def load(name):
    document = yaml.safe_load((WORKFLOWS / name).read_text())
    if True in document and 'on' not in document:  # YAML 1.1 reads the bare key `on` as true
        document['on'] = document.pop(True)
    return document


def triggers(workflow):
    on = workflow.get('on') or {}
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {name: {} for name in on}
    return {name: (value or {}) for name, value in on.items()}


# -- expressions -----------------------------------------------------------------------------------------------
TOKEN = re.compile(r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<number>-?\d+(?:\.\d+)?)|(?P<op>==|!=|<=|>=|&&|\|\||[!<>(),\[\]])"
                   r"|(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_*][A-Za-z0-9_\-]*)*))")


def tokenize(text):
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise ValueError(f'cannot parse expression at {text[position:position + 30]!r}')
        position = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    return tokens


def strip(expression):
    text = str(expression).strip()
    if text.startswith('${{') and text.endswith('}}') and text.count('${{') == 1:
        text = text[3:-2]
    return text


def has_status_function(expression):
    return bool(re.search(r'\b(success|failure|always|cancelled)\s*\(', strip(expression)))


def to_number(value):
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value.strip() == '':
            return 0.0
        try:
            return float(value)
        except ValueError:
            return float('nan')
    return float('nan')


def loose_equal(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return left.lower() == right.lower()
    if type(left) is type(right) and not isinstance(left, (int, float)):
        return left == right
    return to_number(left) == to_number(right)


def truthy(value):
    if isinstance(value, float) and value != value:
        return False
    return value not in (None, False, 0, 0.0, '')


def to_string(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


class Evaluator:
    def __init__(self, context, status):
        self.context, self.status = context, status

    def evaluate(self, expression):
        self.tokens, self.index = tokenize(strip(expression)), 0
        value = self.disjunction()
        if self.index != len(self.tokens):
            raise ValueError(f'trailing tokens in {expression!r}')
        return value

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else (None, None)

    def take(self, value=None):
        token = self.peek()
        if value is not None and token[1] != value:
            raise ValueError(f'expected {value!r}, got {token[1]!r}')
        self.index += 1
        return token

    def disjunction(self):
        value = self.conjunction()
        while self.peek()[1] == '||':
            self.take()
            right = self.conjunction()
            value = value if truthy(value) else right
        return value

    def conjunction(self):
        value = self.comparison()
        while self.peek()[1] == '&&':
            self.take()
            right = self.comparison()
            value = right if truthy(value) else value
        return value

    def comparison(self):
        value = self.unary()
        while self.peek()[1] in ('==', '!=', '<', '<=', '>', '>='):
            operator = self.take()[1]
            right = self.unary()
            if operator == '==':
                value = loose_equal(value, right)
            elif operator == '!=':
                value = not loose_equal(value, right)
            else:
                a, b = to_number(value), to_number(right)
                value = {'<': a < b, '<=': a <= b, '>': a > b, '>=': a >= b}[operator]
        return value

    def unary(self):
        if self.peek()[1] == '!':
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.take()
        if value == '(':
            result = self.disjunction()
            self.take(')')
            return self.postfix(result)
        if kind == 'string':
            return value[1:-1].replace("''", "'")
        if kind == 'number':
            return float(value)
        if kind != 'name':
            raise ValueError(f'unexpected token {value!r}')
        if value in ('true', 'false'):
            return value == 'true'
        if value == 'null':
            return None
        if self.peek()[1] == '(':
            self.take('(')
            arguments = []
            while self.peek()[1] != ')':
                arguments.append(self.disjunction())
                if self.peek()[1] == ',':
                    self.take(',')
            self.take(')')
            return self.postfix(self.call(value, arguments))
        return self.postfix(self.lookup(value.split('.')))

    def postfix(self, value):
        while self.peek()[1] == '[':
            self.take('[')
            key = self.disjunction()
            self.take(']')
            value = value.get(key) if isinstance(value, dict) else None
        return value

    def lookup(self, parts):
        value = self.context
        for part in parts:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def call(self, name, arguments):
        if name in STATUS_FUNCTIONS:
            return self.status(name)
        lowered = [to_string(a).lower() for a in arguments]
        if name == 'startsWith':
            return lowered[0].startswith(lowered[1])
        if name == 'endsWith':
            return lowered[0].endswith(lowered[1])
        if name == 'contains':
            if isinstance(arguments[0], list):
                return any(loose_equal(item, arguments[1]) for item in arguments[0])
            return lowered[1] in lowered[0]
        if name == 'format':
            text = to_string(arguments[0])
            for index, argument in enumerate(arguments[1:]):
                text = text.replace('{%d}' % index, to_string(argument))
            return text
        if name == 'toJSON':
            return json.dumps(arguments[0])
        if name == 'fromJSON':
            return json.loads(arguments[0])
        raise ValueError(f'unsupported function {name}')


def evaluate(expression, context, status=lambda name: name != 'failure' and name != 'cancelled'):
    if isinstance(expression, bool):
        return expression
    return Evaluator(context, status).evaluate(expression)


def interpolate(value, context):
    """Resolve ${{ }} placeholders in a string (used for workflow/job env)."""
    if not isinstance(value, str):
        return value
    if value.strip().startswith('${{') and value.strip().endswith('}}') and value.count('${{') == 1:
        return evaluate(value, context)
    return re.sub(r'\$\{\{(.*?)\}\}', lambda m: to_string(evaluate(m.group(1), context)), value)


# -- contexts ---------------------------------------------------------------------------------------------------
def dispatch_inputs(workflow, given):
    declared = triggers(workflow).get('workflow_dispatch', {}).get('inputs') or {}
    values = {name: spec.get('default') if isinstance(spec, dict) else None for name, spec in declared.items()}
    values = {name: ('' if value is None and (declared[name] or {}).get('type', 'string') == 'string' else value)
              for name, value in values.items()}
    values.update(given)
    return values


def release_contexts(workflow, tag='v9.9.9'):
    """Every trigger of this workflow that can publish a release, as an evaluation context."""
    contexts, on = {}, triggers(workflow)
    if 'release' in on:
        contexts['release'] = {'github': {'event_name': 'release', 'ref': f'refs/tags/{tag}', 'sha': 'a' * 40,
                                          'event': {'release': {'tag_name': tag, 'prerelease': False}}},
                               'inputs': {}}
    if 'workflow_dispatch' in on:
        inputs = (on['workflow_dispatch'] or {}).get('inputs') or {}
        tag_input = next((name for name in ('release_tag', 'release', 'tag') if name in inputs), None)
        if tag_input:
            for flags in ({}, *({name: True} for name, spec in inputs.items()
                                if (spec or {}).get('type') == 'boolean')):
                label = 'workflow_dispatch' + ''.join(f'+{name}' for name in flags)
                contexts[label] = {'github': {'event_name': 'workflow_dispatch', 'ref': f'refs/tags/{tag}',
                                              'sha': 'a' * 40, 'event': {'inputs': {}}},
                                   'inputs': dispatch_inputs(workflow, {tag_input: tag, **flags})}
    push = on.get('push') or {}
    if isinstance(push, dict) and push.get('tags'):
        contexts['push-tag'] = {'github': {'event_name': 'push', 'ref': f'refs/tags/{tag}', 'ref_name': tag,
                                           'ref_type': 'tag', 'sha': 'a' * 40, 'event': {}}, 'inputs': {}}
    return contexts


def with_env(workflow, context):
    context = json.loads(json.dumps(context))
    context['env'] = {}
    for key, value in (workflow.get('env') or {}).items():
        context['env'][key] = interpolate(value, context)
    return context


# -- job graph --------------------------------------------------------------------------------------------------
def needs_of(job):
    needs = job.get('needs') or []
    return [needs] if isinstance(needs, str) else list(needs)


def order(jobs):
    done, ordered = set(), []

    def visit(name, path=()):
        if name in done:
            return
        if name in path:
            raise ValueError(f'job cycle at {name}')
        for need in needs_of(jobs[name]):
            visit(need, path + (name,))
        done.add(name)
        ordered.append(name)
    for name in jobs:
        visit(name)
    return ordered


def simulate(workflow, context, overrides=None):
    """{job: 'success'|'skipped'|forced result} for one trigger context."""
    overrides, jobs, results = overrides or {}, workflow['jobs'], {}
    context = with_env(workflow, context)
    for name in order(jobs):
        job = jobs[name]
        needs = needs_of(job)
        scope = dict(context, needs={need: {'result': results[need], 'outputs': {}} for need in needs})

        def status(function, needs=needs):
            states = [results[need] for need in needs]
            if function == 'always':
                return True
            if function == 'cancelled':
                return False
            if function == 'failure':
                return any(state == 'failure' for state in states)
            return all(state == 'success' for state in states)

        condition = job.get('if', True)
        if condition is True:
            runs = status('success')
        elif has_status_function(condition):
            runs = truthy(evaluate(condition, scope, status))
        else:
            runs = status('success') and truthy(evaluate(condition, scope, status))
        results[name] = overrides.get(name, 'success' if runs else 'skipped')
    return results


def upstream(jobs, name):
    seen, stack = set(), list(needs_of(jobs[name]))
    while stack:
        need = stack.pop()
        if need not in seen:
            seen.add(need)
            stack.extend(needs_of(jobs[need]))
    return seen


def matrix_entries(job):
    matrix = (job.get('strategy') or {}).get('matrix') or {}
    include = matrix.get('include')
    return include if isinstance(include, list) and include else [{}]


def step_enabled(step, job, context, results, entry):
    """Whether a step runs in a job that runs (needs results known), for one matrix entry."""
    scope = with_env({'env': {}}, context)
    scope['env'].update(context.get('env', {}))
    scope.update(needs={need: {'result': results[need], 'outputs': {}} for need in needs_of(job)}, matrix=entry,
                 steps={}, runner={'temp': '/runner-temp'})
    for key, value in (job.get('env') or {}).items():
        scope['env'][key] = interpolate(value, scope)
    condition = step.get('if', True)
    if condition is True:
        return True
    status = (lambda name: name not in ('failure', 'cancelled'))
    return truthy(evaluate(condition, scope, status))


def step_text(step):
    return '\n'.join(str(part) for part in (step.get('uses', ''), step.get('run', ''),
                                            json.dumps(step.get('with') or {}, sort_keys=True)))
