"""evaluate_manuale: the actual manage.py command versus `manuale evaluate`.

Private PostgreSQL with the current Django schema and the rows of
mcp_server_seed.py, plus one association without courses and, per case, the
instance configuration that makes the owner the instance maintainer (or
none). Every case seals a runtime manual index from
TESTS/fixtures/evaluate-manuale-corpus.json (published manual sections),
fits its semantics with Django's fit_semantics and gives both runtimes the
same paths, revision, release, association and arguments:

- Django: `manage.py evaluate_manuale`, which starts `manage.py
  run_mcp_server` over stdio and drives application.agent.core.Agent with
  NoModel (and with no provider for the manual-only check).
- Go: the race-built `manuale evaluate`, which starts the race-built Go
  `mcp-server` next to it and drives service.Agent the same way.

Compared per case: exit status, stdout, the CommandError/argparse message
(Django's tracebacks against Go's single failure line), stderr without
Django's system-check warnings (the MCP server's start line included; not
for traceback and argparse paths), the report file
bytes (or its absence), the rows of every managed model (unchanged), and
the requests received by a per-run model endpoint stub that both runtimes
are configured with (AI_BASE_URL, AI_API_KEY) and that answers a scripted
completion: neither command may ask a model, so both lists must be empty.
Django must first meet each authored expectation.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.mcp_server_seed import ASSOCIATION, SECOND, seed

DATABASE = 'assozeta_contract_evaluate_manuale'
REVISION = 'e' * 40
RELEASE = 'evaluate-fixture-1'
EMPTY_ASSOCIATION, EMPTY_OWNER = UUID(int=96201), UUID(int=96202)
PASSED = 'Existing MCP and agent manual evaluation passed.\n'
TRACEBACK = 'traceback'
FIXTURE = json.loads((ROOT / 'TESTS/fixtures/evaluate-manuale-corpus.json').read_text())


def case(name, *, args=None, corpus='full', owner='maintainer', status=1, message=None, report=None, association=ASSOCIATION):
    """report: None (no file) or (status, error, number of supported cases)."""
    return {'name': name, 'args': args, 'corpus': corpus, 'owner': owner, 'status': status, 'message': message,
            'report': report, 'association': str(association)}


TRUSTED = 'Trusted owner diagnostics did not use the existing agent tool path'
CASES = [
    case('full corpus with the instance maintainer passes', status=0, message=None, report=('passed', None, 'all')),
    case('catalog summary without a maintainer fails the owner diagnostics', owner='other', report=('failed', TRUSTED, None),
         message=TRUSTED),
    case('corpus without catalog passes without a maintainer', corpus='no-catalog', owner='other', status=0,
         report=('passed', None, 'all')),
    case('without an instance configuration the self-hosted sections are hidden', corpus='no-catalog', owner=None,
         message='Supported paraphrase abstained: Manuale piani di abbonamento installazione self-hosted',
         report=('failed', 'Supported paraphrase abstained: Manuale piani di abbonamento installazione self-hosted', 'partial')),
    case('one supported query into a nested output directory', args=['--query', 'Come posso creare un carnet?'],
         status=0, report=('passed', None, 1)),
    case('empty query keeps the default cases', args=['--query', ''], corpus='scenarios-only', status=0,
         report=('passed', None, 'all')),
    case('unsupported query', args=['--query', 'Manuale teletrasporto satellitare'],
         message='Supported question did not retrieve verified evidence',
         report=('failed', 'Supported question did not retrieve verified evidence', None)),
    case('verified query routed to the model raises NoModel', args=['--query', 'carnet'],
         message='Explicit manual questions should use verified extractive answers, not a model',
         report=('failed', 'Explicit manual questions should use verified extractive answers, not a model', 0)),
    case('corpus for another release abstains', corpus='other-release',
         message='Supported question did not retrieve verified evidence',
         report=('failed', 'Supported question did not retrieve verified evidence', None)),
    case('missing expected section fails the default cases', corpus='without-search-section',
         message='Incorrect supporting section for: Manuale ricerca Giulia',
         report=('failed', 'Incorrect supporting section for: Manuale ricerca Giulia', 2)),
    case('association without courses', corpus='no-catalog', association=EMPTY_ASSOCIATION,
         message='Existing data tool no longer reads the seeded association',
         report=('failed', 'Existing data tool no longer reads the seeded association', None)),
    case('corpus without evaluation cases', corpus='unrelated', message='No evaluation cases for the verified corpus'),
    case('unknown association', association=UUID(int=1), message=TRACEBACK),
    case('malformed association id', association='not-a-uuid', message=TRACEBACK),
    case('missing manual index', corpus='missing', message=TRACEBACK),
    case('required arguments', args=[], status=2, message='the following arguments are required: --association-id, --output'),
]


class Model(BaseHTTPRequestHandler):
    """A scripted OpenAI-compatible completion; every request is recorded."""
    requests = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        Model.requests.append({'path': self.path, 'body': body.decode(errors='replace')})
        chunk = {'id': 'scripted', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'scripted',
                 'choices': [{'index': 0, 'delta': {'content': 'SCRIPTED MODEL ANSWER'}, 'finish_reason': 'stop'}]}
        payload = f'data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n'.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST

    def log_message(self, *args):
        pass


def corpus_files(base, variant):
    """A sealed runtime index of the fixture chunks for one variant."""
    from application.manuale.index import EMBEDDING, FORMAT, canonical, fit_semantics, seal_index
    chunks = [dict(chunk, screenshots=[], evidence=[], runtime_dependencies={}) for chunk in FIXTURE['chunks']]
    release = RELEASE
    catalog = {'summary': FIXTURE['catalog_summary'], 'pages': [], 'domains': {}, 'screenshot_backlog': [], 'files': {}}
    if variant == 'unrelated':
        chunks = [dict(chunk, scenario_ids=['unrelated-scenario'], id='docs/altro#' + chunk['section'])
                  for chunk in chunks[:5]]
    elif variant == 'without-search-section':
        chunks = [chunk for chunk in chunks if chunk['id'] != 'faq/come-usare-ricerca-filtri-atleti#la-barra-di-ricerca']
        chunks = [dict(chunk, scenario_ids=['members-search']) if chunk['id'] == 'docs/libro-soci#anagrafica-smart' else chunk
                  for chunk in chunks]
    elif variant == 'scenarios-only':
        # Only the scenario-gated cases: the section-gated ones are left out.
        chunks = [chunk for chunk in chunks if chunk['scenario_ids']]
    elif variant == 'other-release':
        release = 'evaluate-fixture-0'
    value = {'format': FORMAT, 'embedding': EMBEDDING, 'dependencies': {}, 'chunks': chunks, 'gaps': FIXTURE['gaps'],
             'semantics': fit_semantics(chunks),
             'metadata': {'application_revision': REVISION, 'release': release, 'manual_revision': 'm' * 40,
                          'publication_status': 'published', 'code_state': 'committed', 'manual_state': 'committed'}}
    if variant not in ('no-catalog',):
        value['catalog'] = catalog
    value = seal_index(value)
    index = base / 'cache/index.json'
    index.parent.mkdir(parents=True, exist_ok=True)
    (base / 'assets').mkdir(exist_ok=True)
    (base / 'development').mkdir(exist_ok=True)
    if variant != 'missing':
        index.write_text(canonical(value) + '\n')
    return index, value


def set_owner(owner):
    """'maintainer': self-hosted with the evaluated association as primary;
    'other': self-hosted with another primary association; None: no row."""
    from django.apps import apps
    InstanceConfiguration = apps.get_model('instance', 'InstanceConfiguration')
    InstanceConfiguration._base_manager.all().delete()
    if owner:
        InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(
            domain='evaluate.example.invalid', name='Evaluate Fixture', self_hosted=True,
            primary_association_id=ASSOCIATION if owner == 'maintainer' else SECOND)])


def seed_empty_association():
    from django.apps import apps
    from django.contrib.auth import get_user_model
    from auditlog.context import disable_auditlog
    SportAssociation = apps.get_model('application', 'SportAssociation')
    with disable_auditlog():
        get_user_model()._base_manager.bulk_create([get_user_model()(user_id=EMPTY_OWNER, username='EVALUATE-EMPTY', role=1,
                                                                   password='!private-fixture')])
        SportAssociation._base_manager.bulk_create([SportAssociation(sport_association_id=EMPTY_ASSOCIATION, user_id=EMPTY_OWNER,
                                                                     denomination='Senza corsi', tax_code='00000000000')])


def snapshot():
    from django.apps import apps
    return json.dumps({m._meta.label: list(m._base_manager.order_by('pk').values()) for m in apps.get_models()
                       if m._meta.managed and not m._meta.proxy}, default=str, sort_keys=True)


def message(runtime, completed):
    if completed.returncode == 0:
        return None
    lines = [line for line in completed.stderr.splitlines() if line]
    if completed.returncode == 2:
        error = [line for line in lines if ': error: ' in line]
        return error[-1].split(': error: ', 1)[1] if error else lines
    command = [line for line in lines if line.startswith('CommandError: ')]
    if command:
        return command[-1][len('CommandError: '):]
    if runtime == 'django':
        return TRACEBACK if any(line.startswith('Traceback (most recent call last)') for line in lines) else lines
    return TRACEBACK if lines and lines[-1] == 'evaluate failed' else lines


SYSTEM_CHECK = re.compile(r'^(System check identified some issues:|WARNINGS:|\?: \(staticfiles\.W004\) .*|)$')


def stderr_text(runtime, completed, outcome):
    """stderr without Django's system-check warnings (host static directories);
    traceback paths are compared by their outcome only."""
    if outcome == TRACEBACK or completed.returncode == 2:
        return None
    lines = completed.stderr.splitlines()
    if runtime == 'django':
        lines = [line for line in lines if not SYSTEM_CHECK.match(line)]
    return lines


def run_case(item, binaries, env, work):
    base = work / 'manual' / item['name'].replace(' ', '-')
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    variant = item['corpus']
    index, value = corpus_files(base, variant)
    set_owner(item['owner'])
    rows = snapshot()
    output = work / 'out' / ('nested/deeper' if item['args'] and '--query' in item['args'] and item['status'] == 0 else '') / 'evaluation.json'
    args = item['args']
    if args is None or args:
        args = ['--association-id', item['association'], '--output', str(output), *(args or [])]
    environment = dict(env, MANUAL_INDEX_PATH=str(index), MANUAL_ASSET_ROOT=str(base / 'assets'),
                       MANUAL_DEVELOPMENT_ROOT=str(base / 'development'))
    results = {}
    for runtime in ('django', 'go'):
        if runtime == 'go' and binaries is None:
            return {'case': item['name'], 'django': 'pass', 'go': 'missing command'}
        shutil.rmtree(work / 'out', ignore_errors=True)
        Model.requests = []
        argv = ([sys.executable, 'manage.py', 'evaluate_manuale', *args] if runtime == 'django'
                else [str(binaries / 'manuale'), 'evaluate', *args])
        completed = subprocess.run(argv, env=environment, cwd=ROOT / 'BE', capture_output=True, text=True, timeout=900,
                                   stdin=subprocess.DEVNULL)
        report = output.read_bytes() if output.is_file() else None
        outcome = message(runtime, completed)
        results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout, 'message': outcome,
                            'stderr': stderr_text(runtime, completed, outcome),
                            'report': report.decode() if report is not None else None, 'model_requests': list(Model.requests)}
        assert snapshot() == rows, f'{item["name"]}: {runtime} changed rows'
        (work / f'{runtime}.stderr').write_text(completed.stderr)
        receipts = work / 'receipts' / item['name'].replace(' ', '-')
        receipts.mkdir(parents=True, exist_ok=True)
        (receipts / f'{runtime}.stderr').write_text(completed.stderr)
        if report is not None:
            (receipts / f'{runtime}-evaluation.json').write_bytes(report)
        if runtime == 'django':
            written = json.loads(report) if report is not None else None
            authored = (completed.returncode, completed.stdout, results['django']['message'])
            want = (item['status'], PASSED if item['status'] == 0 else '', item['message'])
            assert authored == want, f'{item["name"]}: Django differs from the authored case: {authored} {completed.stderr[-1500:]}'
            if item['report'] is None:
                assert written is None, f'{item["name"]}: Django wrote a report'
            else:
                state, error, count = item['report']
                assert written is not None and written['status'] == state and written.get('error') == error, \
                    f'{item["name"]}: report {written and (written["status"], written.get("error"))}'
                assert list(written)[:4] == ['status', 'transport', 'query', 'checks'], item['name']
                if count == 'all':
                    expected = expected_cases(value)
                    assert [case['query'] for case in written['cases']] == expected, f'{item["name"]}: supported cases'
                    assert written['corpus_identity'] == value['identity'] and written['version'] == RELEASE
                    assert len(written['unsupported_cases']) == 5 and len(written['adversarial_cases']) == 3
                    assert written['manual_only_without_provider'] is True
                elif count == 'partial':
                    queries = [case['query'] for case in written['cases']]
                    assert queries == expected_cases(value)[:len(queries)], f'{item["name"]}: supported cases'
                elif count is not None:
                    assert len(written['cases']) == count, f'{item["name"]}: {len(written["cases"])} cases'
            assert results['django']['model_requests'] == [], f'{item["name"]}: Django asked the model'
    go = 'pass'
    if results['django'] != results['go']:
        (work / 'difference.json').write_text(json.dumps({'case': item['name'], **results}, indent=1) + '\n')
        go = 'differs'
    return {'case': item['name'], 'status': item['status'], 'django': 'pass', 'go': go,
            'report_sha256': hashlib.sha256((results['django']['report'] or '').encode()).hexdigest()
            if results['django']['report'] is not None else None,
            'supported_cases': len(json.loads(results['django']['report']).get('cases', []))
            if results['django']['report'] else None}


def expected_cases(value):
    """The queries Command.handle selects for this corpus, authored from its conditions."""
    scenarios = {s for chunk in value['chunks'] for s in chunk['scenario_ids']}
    sections = {chunk['id'] for chunk in value['chunks']}
    source = (ROOT / 'BE/application/management/commands/evaluate_manuale.py').read_text()
    queries = []
    blocks = re.findall(r"        if (.*?):\n            cases \+= \[(.*?)\]\n", source, re.S)
    for condition, body in blocks:
        names = re.findall(r"'([a-z-]+)' in scenario_ids", condition)
        subset = re.findall(r"'((?:docs|faq|tutorials)/[^']+)'", condition)
        if any(name in scenarios for name in names) or (subset and set(subset) <= sections):
            queries += [q.strip('\'"') for q in re.findall(r"\(\s*('(?:[^'\\]|\\.)*'|\"[^\"]*\")\s*,", body)]
    loop = source.split('for query, section in [', 1)[1].split(']:', 1)[0]
    for query, section in re.findall(r"\('([^']*)', '([^']*)'\)", loop):
        if section in sections:
            queries.append(query)
    return queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='evaluate-manuale')
    parser.add_argument('--case')
    parser.add_argument('--mutate-file')
    parser.add_argument('--old')
    parser.add_argument('--new')
    parser.add_argument('--go-root', help='another BE_GO tree (the RED receipt builds the base revision)')
    args = parser.parse_args()
    go_root = Path(args.go_root).resolve() if args.go_root else ROOT / 'BE_GO'
    require_production_python()
    output = ROOT / 'quality-reports/go-migration' / args.label
    output.mkdir(parents=True, exist_ok=True)
    (output / 'difference.json').unlink(missing_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    cases = [item for item in CASES if not args.case or args.case in item['name']]
    mutated = original = None
    if args.mutate_file:
        mutated = ROOT / args.mutate_file
        original = mutated.read_bytes()
        assert original.decode().count(args.old) == 1, 'mutation text must occur exactly once'
        mutated.write_bytes(original.decode().replace(args.old, args.new).encode())
        result['mutation'] = {'file': args.mutate_file, 'old': args.old, 'new': args.new}
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    tools = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    attempted = False
    try:
        with tempfile.TemporaryDirectory(prefix='az-evaluate-manuale-', dir='/tmp') as directory, \
                (output / 'services.log').open('w') as log:
            work = Path(directory).resolve()

            def run(argv):
                return subprocess.run([str(a) for a in argv], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            binaries = work / 'bin'
            for command in ('manuale', 'mcp-server'):
                built = subprocess.run(['go', 'build', '-race', '-o', str(binaries / command), './cmd/' + command], cwd=go_root,
                                       env=base, stdout=log, stderr=subprocess.STDOUT, timeout=900)
                if built.returncode:
                    binaries = None  # RED: Django still meets every authored case.
                    break
            if mutated:
                mutated.write_bytes(original)
            run([tools / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([tools / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([tools / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            seed()
            seed_empty_association()
            env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'BE'), PYTHONUNBUFFERED='1',
                       DJANGO_SETTINGS_MODULE='TESTS.adapters.django.evaluate_manuale_settings', PYTHONWARNINGS='ignore',
                       MANUAL_APPLICATION_REVISION=REVISION, RUNNING_VERSION=RELEASE, MANUAL_CORPUS_BASE_URL='',
                       MANUAL_SOURCE_ROOT='', ASSOZETA_MANUAL_RUN_ID='', EVALUATE_MANUALE_ABSENT_BUNDLE=str(work / 'absent-bundle'),
                       AI_API_KEY='placeholder-model-key', AI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
                       DATA_RESTORE_LOCK_PATH=str(work / 'contract-lifecycle.lock'))
            result['cases'], failures = [], []
            for item in cases:
                try:
                    outcome = run_case(item, binaries, env, work)
                    result['cases'].append(outcome)
                    print(f"{'PASS' if outcome['go'] == 'pass' else 'GO-FAIL'}: {item['name']} ({outcome['go']})", flush=True)
                    if outcome['go'] != 'pass':
                        failures.append({'case': item['name'], 'error': 'Go differs from the Django command'})
                        if (work / 'difference.json').exists() and not (output / 'difference.json').exists():
                            shutil.copy(work / 'difference.json', output / 'difference.json')
                except AssertionError as error:
                    # Django missed an authored expectation (or a runtime changed rows).
                    for name in ('django.stderr', 'go.stderr'):
                        if (work / name).exists():
                            shutil.copy(work / name, output / name)
                    print(f'FAIL: {item["name"]}: {error}', flush=True)
                    raise
            result['failures'] = failures
            if (work / 'receipts').exists():
                shutil.copytree(work / 'receipts', output / 'cases', dirs_exist_ok=True)
            result['django_passed'] = sum(c['django'] == 'pass' for c in result['cases'])
            result['go_passed'] = sum(c['go'] == 'pass' for c in result['cases'])
            summary = {'django': f"{result['django_passed']}/{len(cases)}", 'go': f"{result['go_passed']}/{len(cases)}"}
            if mutated:
                summary['mutation'] = 'CAUGHT' if failures else 'MISSED'
                result['caught'] = bool(failures)
            print(json.dumps(summary), flush=True)
            if not mutated:
                assert binaries is not None and result['go_passed'] == len(cases), 'Go command missing or failing'
            result['success'] = not mutated
    finally:
        if mutated:
            mutated.write_bytes(original)
        server.shutdown()
        try:
            from django.db import connections
            connections.close_all()
        except Exception:
            pass
        if attempted:
            subprocess.run([str(tools / 'pg_ctl'), '-D', str(work / 'postgres'), '-m', 'immediate', '-w', 'stop'],
                           capture_output=True, timeout=60)
        report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/evaluate_manuale.py', 'TESTS/adapters/django/evaluate_manuale_settings.py',
             'TESTS/fixtures/evaluate-manuale-corpus.json', 'BE/application/management/commands/evaluate_manuale.py',
             'BE/application/management/commands/run_mcp_server.py', 'BE/application/agent/core.py',
             'BE/application/agent/routing.py', 'BE/application/manuale/answers.py', 'BE/application/manuale/tools.py',
             'BE/application/manuale/index.py', 'BE_GO/cmd/manuale/main.go', 'BE_GO/cmd/manuale/evaluate.go',
             'BE_GO/cmd/mcp-server/main.go', 'BE_GO/internal/service/evaluate_manuale.go', 'BE_GO/internal/service/agent.go',
             'BE_GO/internal/service/agent_manual.go', 'BE_GO/internal/service/agent_routing.go']
    result.update(case_count=len(cases), race_instrumented=True, go_root='BE_GO' if go_root == ROOT / 'BE_GO' else 'other tree',
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': result['success'], 'case_count': len(cases)}))


if __name__ == '__main__':
    main()
