#!/usr/bin/env python3
"""Go migration family tool: inspect, scaffold, run, mutate and record one HTTP parity family.

  family.py status                      ledger completion and uncharacterized routes by prefix
  family.py inspect PATTERN             ledger entry, URL line, registry permissions and view source
  family.py scaffold FAMILY             generator, contract and adapter skeletons (never overwrites)
  family.py run FAMILY [--versioned] [--label L]
  family.py mutation FAMILY --file F --old TEXT --new TEXT [--label L]
  family.py record FAMILY --spec SPEC.json [--from-worktree PATH]

Runs use the parent-only acceptance driver (TESTS.adapters.django.staff_board_http_acceptance)
with the migration interpreter (GO_MIGRATION_PYTHON, default
quality-reports/go-migration/python31316/bin/python) and the pinned Go toolchain on PATH
(GO_MIGRATION_GO_BIN). Reports go to quality-reports/go-migration/<family>-http-<label>/.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs/architecture/go-migration/ledger.json'
REPORTS = ROOT / 'quality-reports/go-migration'
PYTHON = Path(os.environ.get('GO_MIGRATION_PYTHON', ROOT / 'quality-reports/go-migration/python31316/bin/python'))
GO_BIN = os.environ.get('GO_MIGRATION_GO_BIN', str(Path.home() / 'go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin'))
FAMILY = re.compile(r'[a-z]+(-[a-z]+)*\Z')


def module_name(family):
    return family.replace('-', '_') + '_http'


def load_ledger():
    return json.loads(LEDGER.read_text())


def save_ledger(ledger):
    # The ledger's own format: two-space indent, UTF-8, trailing newline.
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')


def route_path(entry):
    return entry.get('declaration', {}).get('path', '')


# ---------------------------------------------------------------------------
# status / inspect
# ---------------------------------------------------------------------------

def status(_args):
    entries = load_ledger()['entries']
    counts = collections.Counter(entry['status'] for entry in entries)
    active = len(entries) - counts['deprecated']
    print(f"implementing {counts['implementing']}/{active} ({100 * counts['implementing'] / active:.1f}%)", dict(counts))
    prefixes = collections.Counter(route_path(entry).split('/')[0] for entry in entries
                                   if entry['kind'] == 'route' and entry['status'] == 'uncharacterized')
    print('uncharacterized routes by prefix:', ', '.join(f'{name}={count}' for name, count in prefixes.most_common()))


def view_source(target):
    """The source of a view function or ViewSet class named in a route target."""
    symbol = re.match(r'([A-Za-z_][A-Za-z0-9_]*)', target.replace('views.', '').split('.')[-1] if '.as_view' not in target
                      else target.split('.as_view')[0].split('.')[-1])
    if not symbol:
        return None, ''
    name = symbol.group(1)
    for path in sorted((ROOT / 'BE').rglob('*.py')):
        if '/tests/' in str(path) or '/migrations/' in str(path):
            continue
        text = path.read_text(errors='ignore')
        match = re.search(rf'^(?:@[^\n]*\n)*(?:def|class) {re.escape(name)}\b[^\n]*\n', text, re.M)
        if not match:
            continue
        start = text.rfind('\n\n', 0, match.start()) + 2 if '@' in match.group(0) else match.start()
        rest = text[match.end():]
        end = re.search(r'^(?:@|def |class |[A-Za-z_])', rest, re.M)
        return path.relative_to(ROOT), text[start:match.end() + (end.start() if end else len(rest))].rstrip()
    return None, ''


def inspect(args):
    entries = [entry for entry in load_ledger()['entries'] if args.pattern in entry['id']]
    if not entries:
        sys.exit('No ledger entry matches ' + args.pattern)
    registry = (ROOT / 'BE/application/permissions_registry.py').read_text()
    shown = set()
    for entry in entries:
        print(f"== {entry['id']}\n   status: {entry['status']}; go: {', '.join(entry.get('go') or []) or '-'}")
        declaration = entry.get('declaration', {})
        path = declaration.get('path', '')
        if path:
            pattern = re.sub(r'<[^>]+>', '*', path).rstrip('/')
            for line in registry.splitlines():
                if f"'{pattern}'" in line or f"'{pattern}/'" in line:
                    print('   registry:', line.strip())
        target = declaration.get('target', '')
        if target and target not in shown:
            shown.add(target)
            location, source = view_source(target)
            if location:
                print(f'--- {location}\n{source}\n')


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

GENERATOR = '''"""Author the {route} HTTP sequence ({doc}).

TODO: the observed Django semantics this sequence pins, in Django's order.
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('{module}.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{{TODO_BASE + n:012d}}' for n in range(50))
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {{'name': f'{family}-{{name}}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {{}},
            'status': status, 'response': response, 'effect': effect}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# TODO: authorization cases (anonymous, admin without session, restricted collaborator,
# owner scoped to an athlete), method/OPTIONS cases, validation, scoping and effects.

assert len({{c['name'] for c in cases}}) == len(cases)
OUTPUT.write_text(json.dumps({{'matrix_version': 1, 'scope': 'TODO', 'ids': IDS, 'cases': cases}}, ensure_ascii=False, indent=1) + '\\n')
print(len(cases), 'cases')
'''

CONTRACT = '''"""Independently authored {route} HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/{module}.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    # TODO: the exact Allow set per route; new=/now=/unordered= when needed.
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {{'GET', 'OPTIONS'}})
'''

ADAPTER = '''"""Parent-only {route} HTTP runner; imports are inert.

TODO: what is seeded and what each case must change.
"""
from TESTS.contracts import test_{module} as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/{module}.json', 'TESTS/contracts/test_{module}.py',
           'TESTS/adapters/django/{module}.py') + harness.SOURCES  # TODO: the Django sources this family pins


def seed_rows(source, created):
    # TODO: bulk_create the family rows (audit is disabled) and append (Model, pk) to created.
    pass


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)  # a read family; pass an effect check for writes
'''


def scaffold(args):
    family = args.family
    if not FAMILY.fullmatch(family):
        sys.exit('Family names are lowercase words joined by "-"')
    module = module_name(family)
    values = {'family': family, 'module': module, 'route': args.route or 'TODO route', 'doc': args.doc or 'TODO.md'}
    files = {ROOT / f'TESTS/fixtures/generate_{module}.py': GENERATOR, ROOT / f'TESTS/contracts/test_{module}.py': CONTRACT,
             ROOT / f'TESTS/adapters/django/{module}.py': ADAPTER}
    existing = [path for path in files if path.exists()]
    if existing:
        sys.exit('Refusing to overwrite: ' + ', '.join(str(path.relative_to(ROOT)) for path in existing))
    for path, template in files.items():
        path.write_text(template.format(**values))
        print('created', path.relative_to(ROOT))


# ---------------------------------------------------------------------------
# run / mutation
# ---------------------------------------------------------------------------

def next_label(family, kind):
    taken = {path.name for path in REPORTS.glob(f'{family}-http-{kind}-r*')}
    number = 1
    while f'{family}-http-{kind}-r{number}' in taken:
        number += 1
    return f'{kind}-r{number}'


def summarize(path, log):
    text = path.read_text() if path.is_file() else (log.read_text().strip().splitlines() or ['{}'])[-1]
    try:
        report = json.loads(text)
    except ValueError:
        return {'success': False, 'errors': ['unreadable report; see ' + str(log)], 'workloads': []}
    return {'success': report.get('success'), 'errors': report.get('errors'),
            'workloads': [(item['label'], len(item['cases'])) for item in report.get('workloads', [])]}


def execute(family, label, versioned):
    output = REPORTS / f'{family}-http-{label}'
    if output.exists():
        sys.exit('Report directory exists: ' + str(output.relative_to(ROOT)))
    log = REPORTS / f'{family}-http-{label}.log'
    environment = {**os.environ, 'PATH': GO_BIN + os.pathsep + os.environ.get('PATH', '')}
    command = [str(PYTHON), '-m', 'TESTS.adapters.django.staff_board_http_acceptance', '--family', family, '--output', str(output)]
    if versioned:
        command.append('--versioned')
    with log.open('w') as stream:
        code = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT).returncode
    summary = summarize(output / 'results.json', log)
    print(json.dumps({'label': label, 'returncode': code, **summary}))
    return code, summary


def run(args):
    label = args.label or next_label(args.family, 'versioned' if args.versioned else 'native')
    code, _ = execute(args.family, label, args.versioned)
    sys.exit(code)


def mutation(args):
    """Apply one exact replacement, run natively, require a Go failure, restore byte for byte."""
    target = (ROOT / args.file).resolve()
    original = target.read_bytes()
    text = original.decode()
    if text.count(args.old) != 1:
        sys.exit(f'--old must occur exactly once in {args.file} (found {text.count(args.old)})')
    digest = hashlib.sha256(original).hexdigest()
    try:
        target.write_text(text.replace(args.old, args.new))
        if target.suffix == '.go':
            subprocess.run([str(Path(GO_BIN) / 'go'), 'build', './...'], cwd=ROOT / 'BE_GO', check=True)
        _, summary = execute(args.family, args.label or next_label(args.family, 'mutation'), False)
    finally:
        target.write_bytes(original)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        sys.exit('RESTORE FAILED for ' + args.file)
    errors = summary.get('errors') or []
    caught = summary['success'] is False and any(str(error.get('case', '')).startswith('go-') for error in errors)
    django = [count for label, count in summary['workloads'] if label == 'django-native']
    print('mutation', 'CAUGHT' if caught and django else 'NOT CAUGHT', '(restored)')
    sys.exit(0 if caught and django else 1)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def receipt(label, family):
    path = REPORTS / f'{family}-http-{label}/results.json'
    report = json.loads(path.read_text())
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), report


def record(args):
    """Mark the spec's routes implementing with the family's files, receipts and contract.

    Spec JSON: {"doc": "docs/architecture/go-migration/x.md", "go": [...], "dependencies": [...],
    "notes": "...", "red": "native-r1", "green": "versioned-r1", "green_note": " with exact effects" (optional),
    "mutation": "mutation-r1",
    "mutation_change": "committing change without ...", "mutation_result": "fails Go on ...",
    "routes": {"<declaration path>": {"methods": [...], "request": ..., "response": ..., "errors": ...,
    "permissions": ..., "resource_scope": ..., "side_effects": ..., "transaction": ...}}}
    """
    family = args.family
    module = module_name(family)
    if args.from_worktree:
        import filecmp
        import shutil
        source = Path(args.from_worktree).resolve() / 'quality-reports/go-migration'
        for directory in sorted(source.glob(f'{family}-http-*')):
            if not directory.is_dir():
                continue
            target = REPORTS / directory.name
            if target.exists():
                if filecmp.dircmp(directory, target).diff_files:
                    sys.exit('Differing report already present: ' + directory.name)
                continue
            shutil.copytree(directory, target)
            print('copied', directory.name)
    spec = json.loads(Path(args.spec).read_text())
    red_path, red_sha, red = receipt(spec['red'], family)
    green_path, green_sha, green = receipt(spec['green'], family)
    mutation_path, mutation_sha, mutated = receipt(spec['mutation'], family)
    labels = [item['label'] for item in green.get('workloads', [])]
    count = len(json.loads((ROOT / f'TESTS/fixtures/{module}.json').read_text())['cases'])
    if not (green.get('success') and labels == ['django-native', 'go-native', 'caddy-v1', 'caddy-v2']):
        sys.exit('GREEN must be a successful versioned run on all four transports')
    if red.get('success') or [item['label'] for item in red.get('workloads', [])] != ['django-native']:
        sys.exit('RED must pass Django and fail Go')
    if mutated.get('success'):
        sys.exit('The mutation run must fail')
    tests = [f'TESTS/fixtures/{module}.json', f'TESTS/fixtures/generate_{module}.py', f'TESTS/contracts/test_{module}.py',
             f'TESTS/adapters/django/{module}.py']
    for relative in [spec['doc'], *spec['go'], *tests]:
        if not (ROOT / relative).is_file():
            sys.exit('Missing file: ' + relative)
    relative = lambda path: str(path.relative_to(ROOT))
    first = (red.get('errors') or [{}])[0]
    failure = str(first.get('actual_status')) if first.get('actual_status') else 'fails ' + str(first.get('check', ''))
    evidence = [spec['doc'],
                {'revision': 'committing change, before Go code', 'result': f'RED: Django {count}/{count}; Go {failure} on the first case.',
                 'artifact': relative(red_path), 'receipt_sha256': red_sha},
                {'revision': 'committing change',
                 'result': f'GREEN: {count}/{count} on native Django, native Go, Caddy /api/v1 and /api/v2' + spec.get('green_note', '') + '.',
                 'artifact': relative(green_path), 'receipt_sha256': green_sha},
                {'revision': spec['mutation_change'], 'result': 'Mutation control: ' + spec['mutation_result'],
                 'artifact': relative(mutation_path), 'receipt_sha256': mutation_sha}]
    ledger = load_ledger()
    routes = dict(spec['routes'])
    for entry in ledger['entries']:
        path = route_path(entry)
        if entry['kind'] != 'route' or path not in routes:
            continue
        contract = routes.pop(path)
        external = re.sub(r'<(?:\w+:)?(\w+)>', r'<\1>', path)
        entry.update(status='implementing', go=spec['go'], tests=tests, evidence=evidence, dependencies=spec['dependencies'],
                     notes=['Implementing: ' + spec['notes']])
        entry['contract'] = {'methods': contract['methods'],
                             'external_paths': [f'/api/v1/{external}', f'/api/v2/{external}', f'/api/{external}'],
                             'request': contract['request'], 'response': contract['response'], 'errors': contract['errors'],
                             'authentication': contract.get('authentication', 'DRF IsAuthenticated (JWT/session).'),
                             'permissions': contract['permissions'], 'resource_scope': contract['resource_scope'],
                             'side_effects': contract['side_effects'], 'transaction': contract['transaction'],
                             'execution_owner': 'django'}
    if routes:
        sys.exit('Unknown route declarations: ' + ', '.join(routes))
    save_ledger(ledger)
    check = subprocess.run([str(PYTHON), '-m', 'pytest', '-q', 'TESTS/inventory'], cwd=ROOT, capture_output=True, text=True)
    print(check.stdout.strip().splitlines()[-1] if check.stdout else check.stderr[-500:])
    status(None)
    sys.exit(check.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status').set_defaults(func=status)
    command = commands.add_parser('inspect')
    command.add_argument('pattern')
    command.set_defaults(func=inspect)
    command = commands.add_parser('scaffold')
    command.add_argument('family')
    command.add_argument('--route')
    command.add_argument('--doc')
    command.set_defaults(func=scaffold)
    command = commands.add_parser('run')
    command.add_argument('family')
    command.add_argument('--versioned', action='store_true')
    command.add_argument('--label')
    command.set_defaults(func=run)
    command = commands.add_parser('mutation')
    command.add_argument('family')
    command.add_argument('--file', required=True)
    command.add_argument('--old', required=True)
    command.add_argument('--new', required=True)
    command.add_argument('--label')
    command.set_defaults(func=mutation)
    command = commands.add_parser('record')
    command.add_argument('family')
    command.add_argument('--spec', required=True)
    command.add_argument('--from-worktree', help="copy this family's reports from another worktree first")
    command.set_defaults(func=record)
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
