"""Conservative AST discovery for the Go migration ledger (standard library only).

Declarations are NOT resolved endpoints or supported-behavior counts. Includes,
conditional/dead code, migration schedules and internal entrypoints stay visible
until runtime resolution and caller/side-effect review classify them explicitly.
"""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / 'docs/architecture/go-migration/ledger.json'
STATUSES = {'uncharacterized', 'characterized', 'implementing', 'needs_review',
            'verified', 'deprecated', 'source_removed'}


def expression(node):
    return ast.unparse(node) if node is not None else None


def discover(root):
    entries = []
    for path in sorted((root / 'BE').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        if any(part in {'tests', '__pycache__'} for part in path.relative_to(root).parts) or relative.startswith('BE/manuale/') or path.name.startswith('test_'):
            continue
        source = path.read_text()
        tree = ast.parse(source, filename=relative)
        digest = hashlib.sha256(source.encode()).hexdigest()
        occurrences = {}

        def add(kind, node, symbol, **declaration):
            key = f'{kind}:{relative}:{symbol}'
            occurrences[key] = occurrences.get(key, 0) + 1
            identifier = key if occurrences[key] == 1 else f'{key}#{occurrences[key]}'
            entries.append({'id': identifier, 'kind': kind, 'django': {
                'file': relative, 'line': node.lineno, 'symbol': symbol, 'sha256': digest},
                'source_present': True, 'declaration': declaration,
                'status': 'uncharacterized', 'go': [], 'dependencies': [],
                'contract': {'methods': None, 'external_paths': None, 'request': None,
                    'response': None, 'errors': None, 'authentication': None,
                    'permissions': None, 'resource_scope': None, 'side_effects': None,
                    'transaction': None, 'execution_owner': 'django'},
                'tests': [], 'evidence': [], 'notes': []})

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = expression(node.func)
                if function.split('.')[-1] in {'path', 're_path'} and len(node.args) >= 2:
                    route = node.args[0].value if isinstance(node.args[0], ast.Constant) else expression(node.args[0])
                    target = expression(node.args[1])
                    kind = 'mount' if isinstance(node.args[1], ast.Call) and expression(node.args[1].func).endswith('include') else 'route'
                    if path.name == 'routing.py':
                        kind = 'websocket'
                    add(kind, node, str(route) + ' -> ' + target, path=route, target=target, syntax=function)
                if 'PeriodicTask.objects.' in function or 'CrontabSchedule.objects.' in function or 'IntervalSchedule.objects.' in function:
                    add('schedule_mutation', node, function, call=expression(node))
                if function.endswith('.connect') and node.args and any(s in function for s in ('save', 'delete', 'signal', 'publish')):
                    add('signal_connection', node, function + ':' + expression(node.args[0]), call=expression(node))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    call = decorator if isinstance(decorator, ast.Call) else None
                    function = expression(call.func if call else decorator)
                    if function.split('.')[-1] in {'shared_task', 'task'}:
                        keywords = {k.arg: expression(k.value) for k in call.keywords} if call else {}
                        name = next((k.value.value for k in call.keywords if k.arg == 'name' and isinstance(k.value, ast.Constant)), node.name) if call else node.name
                        add('task', node, node.name, name=name, arguments=expression(node.args), options=keywords)
                    elif function.split('.')[-1] == 'receiver':
                        add('signal', node, node.name, decorator=expression(decorator), arguments=expression(node.args))
                    elif function.endswith(('.tool', '.list_tools', '.call_tool')):
                        add('mcp_tool', node, node.name, arguments=expression(node.args), decorator=expression(decorator))
            if isinstance(node, ast.ClassDef):
                if '/models/' in relative or path.name == 'models.py' or '/serializers/' in relative:
                    for method in node.body:
                        if isinstance(method, ast.FunctionDef) and method.name in {'save', 'delete', 'clean', 'full_clean'}:
                            add('serializer_hook' if '/serializers/' in relative else 'model_hook', method, node.name + '.' + method.name, arguments=expression(method.args))
                if '/management/commands/' in relative and node.name == 'Command':
                    add('command', node, path.stem, bases=[expression(base) for base in node.bases])
            if isinstance(node, ast.Assign):
                targets = [expression(target) for target in node.targets]
                if any(target.endswith('TOOL_DEFINITIONS') for target in targets) and isinstance(node.value, ast.List):
                    for definition in node.value.elts:
                        if isinstance(definition, ast.Dict):
                            name = next((value.value for key, value in zip(definition.keys, definition.values)
                                         if isinstance(key, ast.Constant) and key.value == 'name' and isinstance(value, ast.Constant)), None)
                            if name:
                                add('mcp_definition', definition, name, name=name, schema=expression(definition))
                if any(target.endswith(('beat_schedule', 'CELERY_BEAT_SCHEDULE')) for target in targets) and isinstance(node.value, ast.Dict):
                    for key, value in zip(node.value.keys, node.value.values):
                        name = key.value if isinstance(key, ast.Constant) else expression(key)
                        add('schedule', value, str(name), schedule=expression(value), assignment=targets)
    return sorted(entries, key=lambda entry: entry['id'])


def reconcile(previous, current):
    old = {entry['id']: entry for entry in previous}
    result = []
    for entry in current:
        before = old.pop(entry['id'], None)
        if before:
            for field in ('status', 'go', 'dependencies', 'contract', 'tests', 'evidence', 'notes'):
                entry[field] = copy.deepcopy(before[field])
            if before['django'] != entry['django'] or not before['source_present']:
                entry['status'] = 'needs_review'
        result.append(entry)
    for removed in old.values():
        removed = copy.deepcopy(removed)
        removed.update(status='source_removed', source_present=False)
        result.append(removed)
    return sorted(result, key=lambda entry: entry['id'])


def validate(entries):
    seen = set()
    for entry in entries:
        identifier = entry['id']
        if identifier in seen:
            raise ValueError(f'duplicate id: {identifier}')
        seen.add(identifier)
        if entry['status'] not in STATUSES:
            raise ValueError(f'unknown status: {identifier}')
        if entry['status'] == 'verified':
            required = ('authentication', 'permissions', 'resource_scope', 'side_effects', 'transaction')
            if (not entry['go'] or not entry['tests'] or not entry['evidence'] or
                    not entry['source_present'] or any(entry['contract'][key] is None for key in required)):
                raise ValueError(f'verified entry lacks implementation, contract or executed evidence: {identifier}')
            for evidence in entry['evidence']:
                if not isinstance(evidence, dict) or any(not evidence.get(key) for key in ('command', 'revision', 'result', 'artifact')) or evidence['result'] != 'passed':
                    raise ValueError(f'verified entry has invalid evidence: {identifier}')
        if entry['status'] == 'deprecated' and not entry['notes']:
            raise ValueError(f'deprecated entry needs rationale: {identifier}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()
    previous = json.loads(args.ledger.read_text()) if args.ledger.exists() else {'entries': []}
    validate(previous['entries'])
    entries = reconcile(previous['entries'], discover(ROOT))
    validate(entries)
    document = {'format': 1, 'scope': 'Static declarations; runtime resolution and behavioral characterization required',
                'kuayle_revision': '5025d2a6bc966d0d08c49832ff9c7c86184207cd', 'entries': entries}
    if args.check:
        if document != previous:
            raise SystemExit('Ledger is stale; regenerate and review changed/removed declarations.')
    else:
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        args.ledger.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    from collections import Counter
    print(json.dumps({'entries': len(entries), 'kinds': dict(Counter(i['kind'] for i in entries)),
                      'status': dict(Counter(i['status'] for i in entries))}, sort_keys=True))


if __name__ == '__main__':
    main()
