#!/usr/bin/env python3
"""Discover and check the whole-instance runtime handoff inventory."""
import argparse
import ast
from bisect import bisect_right
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import sys

from go_tasks_inventory import CLEANUP, django_tasks
from TESTS.adapters.django.scheduler_authority import declarations_text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.inventory import catalog

INVENTORY = Path('docs/architecture/go-migration/handoff-inventory.json')
FIXTURES = Path('TESTS/handoff/fixtures/manifest.json')
LEDGER = Path('docs/architecture/go-migration/ledger.json')
GATED = ('route', 'task', 'schedule', 'service', 'publisher')
OTHER = ('internal_caller', 'side_effect', 'schema_step', 'release_artifact', 'rollback_source', 'client')
TASK_FIELDS = ('producers', 'children', 'handler', 'payload_versions', 'tenant_scope', 'priority',
               'eta_expiry', 'retry_ack', 'result', 'effect_identity', 'mounts_network',
               'schedule_owner', 'reverse_mapping', 'legacy_fixture')
BUILTIN_SITE = 'celery.app.builtins.add_backend_cleanup_task:1'
PYTHON_PUBLICATIONS = {'delay', 'apply_async', 'send_task', 'retry'}
PLACEHOLDERS = {'todo', 'tbd', 'unknown', '?', '-', 'n/a'}

GO_STRING = r'"(?:\\.|[^"\\])*"|`[^`]*`'
GO_TRIVIA = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|' + GO_STRING + r"|'(?:\\.|[^'\\])*'")
GO_FUNCTION = re.compile(r'^func\s+(?:\(\s*(?:\w+\s+)?\*?(?P<receiver>\w+)(?:\[[^\]]+\])?\s*\)\s+)?'
                         r'(?P<name>\w+)(?:\[[^\]]+\])?\s*\(', re.M)
GO_SELECTOR = r'\b[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*'
GO_CALL = re.compile(rf'(?P<callee>{GO_SELECTOR}\s*\.\s*(?:Publish|Enqueue))\s*\(')
GO_TIMER = re.compile(r'\btime\s*\.\s*(?:NewTicker|Tick|AfterFunc)\s*\(')
GO_ROUTE = re.compile(r'\b(?P<router>\w+)\s*\.\s*(?P<method>Any|GET|POST|PUT|PATCH|DELETE|'
                      r'HEAD|OPTIONS|CONNECT|TRACE|Group|Add|Match|Static(?:FS)?|File(?:FS)?)\s*\(')
GO_HTTP_METHOD = rf'{GO_STRING}|http\.Method[A-Za-z]+'
GO_ECHO = re.compile(r'\b(\w+)\s*:?=\s*echo\s*\.\s*New\s*\(')
GO_ECHO_PARAM = re.compile(r'(?:^|,)\s*(\w+(?:\s*,\s*\w+)*)\s+\*?\s*echo\s*\.\s*Echo\b')
GO_RANGE = re.compile(r'\bfor\s+\w+\s*,\s*(\w+)\s*:=\s*range\s+')
GO_STRING_CASE = re.compile(rf'\s*((?:{GO_STRING})(?:\s*,\s*(?:{GO_STRING}))*)\s*:')
GO_ROUTER_PACKAGES = {('BE_GO', 'cmd', 'server'), ('BE_GO', 'internal', 'handler')}
COMPOSE_SERVICE = re.compile(r'''^( +)(?:["']?)([\w.-]+)(?:["']?):(?:\s|$)''')
COMPOSE_RUN = re.compile(r'^[ \t]*(?:(?:if\s+)?!\s+)?(?:\w+=\$\()?prod_compose\s+([^\n]+)', re.M)
SHELL_VALUE_OPTIONS = {'--entrypoint', '--name', '--user', '--workdir', '--env', '--volume',
                       '--label', '--publish', '--pull', '-e', '-v', '-l', '-p', '-u', '-w'}


class GoSource:
    """Keep offsets aligned while hiding Go comments, strings and rune literals."""

    def __init__(self, source):
        self.source = source
        self.code = GO_TRIVIA.sub(lambda match: re.sub(r'[^\n]', ' ', match[0]), source)
        self.with_strings = GO_TRIVIA.sub(
            lambda match: re.sub(r'[^\n]', ' ', match[0]) if match[0].startswith('/') else match[0], source)
        self.functions = list(GO_FUNCTION.finditer(self.code))
        self.offsets = [function.start() for function in self.functions]
        self.newlines = [match.start() for match in re.finditer('\n', source)]

    def line(self, offset):
        return bisect_right(self.newlines, offset) + 1

    def function_index(self, offset):
        return bisect_right(self.offsets, offset) - 1

    def function(self, offset):
        index = self.function_index(offset)
        if index < 0:
            return '<package>'
        function = self.functions[index]
        receiver, name = function.group('receiver', 'name')
        return f'{receiver}.{name}' if receiver else name

    def closing(self, opening):
        head = self.code[opening]
        tail = {'{': '}', '(': ')'}[head]
        depth = 1
        delimiters = re.compile(f'[{re.escape(head + tail)}]')
        for delimiter in delimiters.finditer(self.code, opening + 1):
            depth += 1 if delimiter[0] == head else -1
            if depth == 0:
                return delimiter.start()
        raise ValueError(f'unclosed Go delimiter at line {self.line(opening)}')


def go_string(value):
    return value[1:-1] if value.startswith('`') else ast.literal_eval(value)


def stock_routes(root, source, receiver, offset):
    """Expand the explicit DefaultRouter registrations feeding Routes()."""
    handlers = root / 'BE_GO/internal/handler'
    implementation = GoSource((handlers / 'router_stock.go').read_text())
    routes = next((function for function in implementation.functions
                   if function.group('name') == 'Routes' and function.group('receiver') == 'RouterStockHandler'), None)
    if routes is None:
        raise ValueError('cannot locate RouterStockHandler.Routes')
    start = implementation.code.index('{', routes.end())
    body = implementation.with_strings[start:implementation.closing(start)]
    static = re.search(r'paths\s*:=\s*\[\]string\s*\{([^}]*)\}', body)
    append = re.search(r'\bappend\(paths,([^\n]+)\)', body)
    if static is None or append is None:
        raise ValueError('cannot expand RouterStockHandler.Routes')
    expanded = [go_string(item) for item in re.findall(GO_STRING, static[1])]
    templates = re.findall(rf'({GO_STRING})\s*\+\s*viewset\.prefix\s*\+\s*({GO_STRING})', append[1])
    methods = {}
    for file in sorted(handlers.glob('router_stock_*.go')):
        if file.name.endswith('_test.go'):
            continue
        handler = GoSource(file.read_text())
        for function in handler.functions:
            if function.group('receiver') != 'RouterStockHandler':
                continue
            start = handler.code.index('{', function.end())
            end = handler.closing(start)
            prefix = re.search(rf'\bprefix:\s*({GO_STRING})', handler.with_strings[start:end])
            if prefix:
                methods[function.group('name')] = go_string(prefix[1])
    for registration in re.finditer(rf'\b{re.escape(receiver)}\.(Register\w+)\(', source.code[:offset]):
        prefix = methods[registration[1]]
        expanded.extend(go_string(head) + prefix + go_string(tail) for head, tail in templates)
    return expanded


def go_route_arguments(method, arguments, line):
    if method in {'Add', 'Match'}:
        pattern = (rf'\s*({GO_HTTP_METHOD})\s*,\s*' if method == 'Add' else
                   r'\s*\[\]\s*string\s*\{(.*?)\}\s*,\s*')
        declaration = re.match(pattern, arguments, re.S)
        if declaration is None:
            raise ValueError(f'unresolved Echo method at line {line}')
        members = re.findall(GO_HTTP_METHOD, declaration[1])
        if re.sub(GO_HTTP_METHOD, '', declaration[1]).strip(' ,\r\n\t'):
            raise ValueError(f'unresolved Echo method in {declaration[1]} at line {line}')
        methods = [go_string(member) if member.startswith(('"', '`')) else member.removeprefix('http.Method').upper()
                   for member in members]
        arguments = arguments[declaration.end():]
    else:
        methods = ['GET'] if method.startswith(('Static', 'File')) else [method.upper()]
    path = re.match(rf'\s*((?:{GO_STRING}|\w+)(?:\s*\+\s*(?:{GO_STRING}|\w+))*)\s*[,)]', arguments)
    if path is None:
        raise ValueError(f'unresolved Echo path at line {line}')
    parts = re.findall(rf'{GO_STRING}|\w+', path[1])
    if method.startswith('Static'):
        parts.append('"*"')
    return methods, parts


def go_routes(root, source):
    routers = {(source.function_index(match.start()), match[1]): ['']
               for match in GO_ECHO.finditer(source.code)}
    for index, function in enumerate(source.functions):
        parameters = source.code[function.end():source.closing(function.end() - 1)]
        for match in GO_ECHO_PARAM.finditer(parameters):
            for name in match[1].split(','):
                routers[index, name.strip()] = ['']
    if not routers:
        return

    ranges = []
    for match in GO_RANGE.finditer(source.code):
        expression = source.code[match.end():]
        literal = re.match(r'\[\]\s*string\s*\{', expression)
        stock = re.match(r'(\w+)\.Routes\(\)\s*\{', expression)
        if literal:
            start = match.end() + literal.end() - 1
            end = source.closing(start)
            values = list(map(go_string, re.findall(GO_STRING, source.with_strings[start + 1:end])))
        elif stock and (root / 'BE_GO/internal/handler/router_stock.go').is_file():
            end = match.end() + stock.end() - 2
            values = stock_routes(root, source, stock[1], match.start())
        else:
            continue
        body = source.code.index('{', end + 1)
        ranges.append((body, source.closing(body), match[1], values))

    for call in GO_ROUTE.finditer(source.code):
        index = source.function_index(call.start())
        prefixes = routers.get((index, call['router']))
        if prefixes is None:
            continue
        line = source.line(call.start())
        methods, parts = go_route_arguments(call['method'], source.with_strings[call.end():], line)
        paths = ['']
        for part in parts:
            if part.startswith(('"', '`')):
                values = [go_string(part)]
            else:
                values = next((values for start, end, name, values in ranges
                               if name == part and start < call.start() < end), None)
                if values is None:
                    raise ValueError(f'unresolved Echo path {part} at line {source.line(call.start())}')
            paths = [prefix + value for prefix in paths for value in values]
        paths = [prefix + path for prefix in prefixes for path in paths]
        if call['method'] == 'Group':
            target = re.search(r'\b(\w+)\s*:?=\s*$', source.code[:call.start()])
            if target:
                routers[index, target[1]] = paths
        else:
            for path in paths:
                for method in methods:
                    yield f'{method} {path}', line


def go_websocket_routes(source):
    for case in re.finditer(r'\bcase\b', source.code):
        declaration = GO_STRING_CASE.match(source.with_strings, case.end())
        if declaration:
            for literal in re.finditer(GO_STRING, declaration[1]):
                path = go_string(literal[0])
                if path.startswith('/ws/'):
                    yield f'WS {path}', source.line(declaration.start(1) + literal.start())


def periodic_jobs(source, constants):
    mapping = re.search(r'\bperiodicJobKinds\s*=\s*map\[string\]string\s*\{', source.code)
    if not mapping:
        return
    end = source.closing(mapping.end() - 1)
    body = source.with_strings[mapping.end():end]
    for entry in re.finditer(rf'({GO_STRING}|\w+)\s*:\s*(?:{GO_STRING}|\w+)\s*,?', body):
        key = entry[1]
        line = source.line(mapping.end() + entry.start())
        if key.startswith(('"', '`')):
            yield go_string(key), line
        elif key in constants:
            yield constants[key], line
        else:
            raise ValueError(f'unresolved periodic job name {key} at line {line}')


class PythonPublications(ast.NodeVisitor):
    def __init__(self):
        self.scope = []
        self.calls = []

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in PYTHON_PUBLICATIONS:
            self.calls.append((node.lineno, node.col_offset, '.'.join(self.scope) or '<module>',
                               ast.unparse(node.func)))
        self.generic_visit(node)


def compose_services(text):
    lines = iter(enumerate(text.splitlines(), 1))
    for _, line in lines:
        if re.fullmatch(r'services:\s*(?:#.*)?', line):
            break
    candidates = []
    for number, line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line.startswith(' '):
            break
        match = COMPOSE_SERVICE.match(line)
        if match:
            candidates.append((len(match[1]), match[2], number))
    if candidates:
        indent = min(item[0] for item in candidates)
        yield from ((name, number) for level, name, number in candidates if level == indent)


def cli_services(text):
    # Fold shell continuations without losing the line numbers used in evidence.
    commands = re.sub(r'\\\n', ' \t', text)
    newlines = [match.start() for match in re.finditer(r'\\?\n', text)]
    variables = dict(re.findall(r'^\s*(\w+)=\$\((\w+)\)', text, re.M))
    literals = {}
    for function in re.finditer(r'^([\w]+)\(\)\s*\{\n([\s\S]*?)^\}', text, re.M):
        outputs = re.findall(r"""^\s*printf\s+['"]([\w.-]+)\\n['"]\s*$""", function[2], re.M)
        if len(outputs) == 1:
            literals[function[1]] = outputs[0]

    for match in COMPOSE_RUN.finditer(commands):
        arguments = shlex.shlex(match[1], posix=True)
        arguments.whitespace_split = True
        if not any(argument == 'run' for argument in arguments):
            continue
        for argument in arguments:
            if argument == '--':
                argument = next(arguments, '')
                break
            if argument in SHELL_VALUE_OPTIONS:
                next(arguments, None)
            elif not argument.startswith('-'):
                break
        else:
            continue
        if argument.startswith('$'):
            argument = literals.get(variables.get(argument[1:].strip('{}')), argument)
        if argument:
            yield argument, bisect_right(newlines, match.start()) + 1


def production_python(path):
    return not ({'tests', '__pycache__'} & set(path.parts) or path.name.startswith('test_'))


def discover(root):
    result = {kind: {} for kind in GATED}
    publications = Counter()

    def add(kind, key, site):
        sources = result[kind].setdefault(key, {'sources': []})['sources']
        if site not in sources:
            sources.append(site)

    def publish(key, site):
        publications[key] += 1
        occurrence = publications[key]
        add('publisher', key if occurrence == 1 else f'{key}#{occurrence}', site)

    schedule_files = {'BE/core/celery.py': 'app.conf.beat_schedule', 'BE/core/settings.py': 'CELERY_BEAT_SCHEDULE'}
    beat_names = {file: declarations_text((root / file).read_text(), assignment, file, required=False)
                  for file, assignment in schedule_files.items() if (root / file).is_file()}
    for row in catalog.discover(root):
        kind = row['kind']
        file, line = (row['django'][field] for field in ('file', 'line'))
        site = f'{file}:{line}'
        if kind in {'route', 'mount', 'websocket'}:
            add('route', f'django:{row["id"]}', site)
        elif kind == 'schedule_mutation':
            add('schedule', f'django-mutation:{row["id"]}', site)
        elif (kind == 'schedule' and schedule_files.get(file) in row['declaration']['assignment']
              and row['django']['symbol'] in beat_names.get(file, {})):
            add('schedule', f'beat:{row["django"]["symbol"]}', site)

    for name, (site, _) in django_tasks(root).items():
        add('task', name, site)
    add('task', CLEANUP, BUILTIN_SITE)
    add('schedule', 'beat:' + CLEANUP, 'django_celery_beat.schedulers.DatabaseScheduler.install_default_entries:1')

    for path in sorted((root / 'BE').rglob('*.py')):
        file = path.relative_to(root)
        if not production_python(file):
            continue
        visitor = PythonPublications()
        visitor.visit(ast.parse(path.read_bytes(), filename=str(file)))
        for line, _, scope, callee in sorted(visitor.calls):
            publish(f'py:{file.as_posix()}:{scope}:{callee}', f'{file.as_posix()}:{line}')

    constants = {}
    periodic_source = None
    for path in sorted((root / 'BE_GO').rglob('*.go')):
        relative = path.relative_to(root)
        if path.name.endswith('_test.go') or 'testdata' in relative.parts:
            continue
        file = relative.as_posix()
        source = GoSource(path.read_text())
        if relative.parts[:3] in GO_ROUTER_PACKAGES:
            for key, line in go_routes(root, source):
                add('route', f'go:{key}', f'{file}:{line}')
        if file == 'BE_GO/internal/handler/websocket.go':
            for key, line in go_websocket_routes(source):
                add('route', f'go:{key}', f'{file}:{line}')
        if path.parent == root / 'BE_GO/internal/domain':
            constants.update((match[1], go_string(match[2])) for match in re.finditer(
                rf'^\s*(?:const\s+)?(\w+)(?:\s+string)?\s*=\s*({GO_STRING})\s*$', source.with_strings, re.M))
            if path.name == 'schedule.go':
                periodic_source = source
        for call in GO_TIMER.finditer(source.code):
            add('schedule', f'go-loop:{file}:{source.function(call.start())}', f'{file}:{source.line(call.start())}')
        for call in GO_CALL.finditer(source.code):
            callee = re.sub(r'\s+', '', call['callee'])
            publish(f'go:{file}:{source.function(call.start())}:{callee}', f'{file}:{source.line(call.start())}')

    if periodic_source is not None:
        for name, line in periodic_jobs(periodic_source, constants):
            add('schedule', f'go-periodic:{name}', f'BE_GO/internal/domain/schedule.go:{line}')

    for path in sorted((root / 'BE_GO/cmd').glob('*/main.go')):
        add('service', f'go-cmd:{path.parent.name}', f'{path.relative_to(root).as_posix()}:1')

    for file, prefix in (('selfhost/compose.yml', 'compose'), ('selfhost/compose.updater.yml', 'compose-updater')):
        path = root / file
        if path.is_file():
            for service, line in compose_services(path.read_text()):
                add('service', f'{prefix}:{service}', f'{file}:{line}')

    file = 'selfhost/bin/assozeta'
    if (root / file).is_file():
        for service, line in cli_services((root / file).read_text()):
            add('service', f'cli-run:{service}', f'{file}:{line}')

    file = 'selfhost/caddy/Caddyfile'
    if (root / file).is_file():
        for number, line in enumerate((root / file).read_text().splitlines(), 1):
            match = re.match(r'''^\s*handle(?:_path)?\s+["']?(/[^"'\s]*)''', line)
            if match:
                add('route', f'caddy:{match[1]}', f'{file}:{number}')
    return result


def nonblank(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in PLACEHOLDERS


def nonempty(value):
    if isinstance(value, str):
        return nonblank(value)
    if isinstance(value, dict):
        return bool(value) and all(nonblank(key) and nonempty(item) for key, item in value.items())
    return isinstance(value, list) and bool(value) and all(nonempty(item) for item in value)


def valid_owner(owner, mode):
    if not isinstance(owner, dict) or owner.get('runtime') not in (mode, 'shared', 'none'):
        return False
    status, phase = owner.get('status'), owner.get('phase')
    if status not in ('present', 'planned') or (status == 'planned' and
                                               (type(phase) is not int or not 1 <= phase <= 7)):
        return False
    if owner['runtime'] != 'none':
        return nonblank(owner.get('component'))
    disposition = owner.get('disposition')
    return nonblank(disposition) and bool(re.match(
        r'^(?:stopped|retired|refused|not-applicable|test-only)\b[\s:–—-]+\S', disposition))


def string_list(value):
    return isinstance(value, list) and all(nonblank(item) for item in value)


def validate_entry(entry, fixtures):
    fixture = entry.get('fixture')
    choices = [fixture] if isinstance(fixture, str) else fixture
    if not string_list(choices) or not choices or any(choice not in fixtures for choice in choices):
        yield 'fixture must name an installation in the manifest'
    if not nonblank(entry.get('effect')):
        yield 'effect is required'
    for optional in ('refs', 'notes'):
        if optional in entry and not string_list(entry[optional]):
            yield f'{optional} must be a list of strings'
    owners = entry.get('owner')
    for mode in ('django', 'go'):
        if not isinstance(owners, dict) or not valid_owner(owners.get(mode), mode):
            yield f'invalid {mode} owner'
    if entry['kind'] == 'task':
        family = entry.get('task')
        if not isinstance(family, dict):
            yield 'task family is required'
        else:
            for field in TASK_FIELDS:
                if not nonempty(family.get(field)):
                    yield f'task.{field} is required'


def validate_document(document, ledger):
    baseline = document.get('baseline')
    if not isinstance(baseline, dict) or not (
        isinstance(baseline.get('revision'), str) and re.fullmatch(r'[0-9a-f]{40}', baseline['revision'])
        and isinstance(baseline.get('images'), dict) and all(map(nonblank, baseline['images'].values()))
        and isinstance(baseline.get('controllers'), dict) and all(map(nonblank, baseline['controllers'].values()))
        and string_list(baseline.get('upgrade_from')) and string_list(baseline.get('rollback_to'))
    ):
        yield 'invalid inventory baseline: expected revision, images, upgrade/rollback versions and controllers'

    handoff = document.get('django_handoff')
    if not isinstance(handoff, list) or any(
        not isinstance(row, dict) or not all(nonblank(row.get(key)) for key in ('shape', 'route', 'evidence'))
        for row in handoff
    ):
        yield 'invalid inventory django_handoff: expected shape, route and evidence'

    reconciliation = document.get('ledger_reconciliation')
    if not isinstance(reconciliation, list):
        yield 'invalid inventory ledger_reconciliation: expected a list'
        return
    seen = set()
    for row in reconciliation:
        if not isinstance(row, dict) or not (
            all(nonblank(row.get(key)) for key in ('ledger_id', 'status', 'finding'))
            and all(type(row.get(key)) is bool for key in ('executable_gap', 'evidence_gap'))
        ):
            yield 'invalid inventory ledger_reconciliation: expected id, status, finding and gap flags'
            continue
        identifier = row['ledger_id']
        if identifier in seen:
            yield f'duplicate ledger_reconciliation {identifier}'
        seen.add(identifier)
        if ledger is not None and ledger.get(identifier) != row['status']:
            yield f'stale ledger_reconciliation {identifier}: row is closed, missing, or its status changed'
    if ledger is not None:
        for identifier in sorted(ledger.keys() - seen):
            yield f'unclassified ledger_reconciliation {identifier}'


def check(document, manifest, discovered, ledger):
    if not isinstance(document, dict) or type(document.get('format')) is not int or document['format'] != 1:
        yield 'invalid inventory format: expected format 1'
        return
    if not isinstance(manifest, dict) or type(manifest.get('format')) is not int or manifest['format'] != 1:
        yield 'invalid fixtures manifest: expected format 1'
        return
    fixtures = manifest.get('fixtures')
    if not isinstance(fixtures, dict):
        yield 'invalid fixtures manifest: expected a fixtures object'
        return
    entries = document.get('entries')
    if not isinstance(entries, list):
        yield 'invalid inventory entries: expected a list'
        return

    yield from validate_document(document, ledger)
    identifiers = set()
    classified = {kind: set() for kind in GATED + OTHER}
    for entry in entries:
        if not isinstance(entry, dict):
            yield 'invalid inventory entry: expected an object'
            continue
        kind, identifier = entry.get('kind'), entry.get('id')
        kind = kind if nonblank(kind) else 'inventory'
        identifier = identifier if nonblank(identifier) else '<entry>'
        if kind not in classified:
            yield f'invalid {kind} {identifier}: unknown kind'
            continue
        if identifier in identifiers or not identifier.startswith(f'{kind}:') or not nonblank(identifier.split(':', 1)[1]):
            yield f'invalid {kind} {identifier}: id must be unique and start with {kind}:'
        identifiers.add(identifier)
        for problem in validate_entry(entry, fixtures):
            yield f'invalid {kind} {identifier}: {problem}'
        items = entry.get('items')
        if not string_list(items) or not items:
            yield f'invalid {kind} {identifier}: items must be a nonempty list of keys'
            continue
        for item in items:
            if item in classified[kind]:
                yield f'duplicate {kind} {item}: {identifier}'
            classified[kind].add(item)
            if kind in discovered and item not in discovered[kind]:
                yield f'stale {kind} {item}: {identifier}'
    for kind, keys in discovered.items():
        for key in sorted(keys.keys() - classified[kind]):
            yield f'unclassified {kind} {key}'


def read_json(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('discover', 'check'):
        subparser = commands.add_parser(command)
        subparser.add_argument('--root', type=Path, default=ROOT)
        if command == 'check':
            subparser.add_argument('--inventory', type=Path, default=INVENTORY)
            subparser.add_argument('--fixtures', type=Path, default=FIXTURES)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        found = discover(root)
        if args.command == 'discover':
            print(json.dumps(found, indent=2, sort_keys=True))
            return 0
        document = read_json(root / args.inventory)
        fixtures = read_json(root / args.fixtures)
        ledger = None
        if (root / LEDGER).is_file():
            ledger = {row['id']: row['status'] for row in read_json(root / LEDGER)['entries']
                      if row['status'] not in ('verified', 'deprecated')}
        problems = list(check(document, fixtures, found, ledger))
    except (OSError, SyntaxError, ValueError, KeyError, TypeError) as error:
        problems = [f'invalid inventory {args.command}: {error}']
    if problems:
        for problem in problems:
            print(problem.replace('\n', ' '), file=sys.stderr)
        return 1
    print(json.dumps({'discovered': {kind: len(keys) for kind, keys in found.items()},
                      'entries': dict(Counter(entry['kind'] for entry in document['entries']))}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
