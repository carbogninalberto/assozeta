"""Parent-only runner for the AI agent sessions (ws/agent/) and run_agent; imports are inert.

Django serves Channels' complete ASGI application (``core.asgi``) under uvicorn;
the Go server and the Go ``run-agent`` command run natively. Both read the AI
configuration from the seeded InstanceConfiguration row, whose base URL is a
per-run stub endpoint: it answers each request with the case's next scripted
response and records method, path, headers and body. The verified manual is a
sealed runtime index on private paths (Django settings and Go environment).
After every case the recorded requests, the agent cache entries
(``ws_throttle``, ``ws_concurrent``, ``agent_save_info`` in Django's cache),
the SavedReport rows and the complete model state are checked; the authored
report changes are then undone.
"""
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import pickle
import subprocess
import tempfile
import threading
import time

from TESTS.contracts import test_ws_agent_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import websockets_http as websockets

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('TESTS/fixtures/ws_agent_http.json', 'TESTS/fixtures/generate_ws_agent_http.py', 'TESTS/contracts/test_ws_agent_http.py',
           'TESTS/adapters/django/ws_agent_http.py', 'TESTS/contracts/test_websockets_http.py', 'TESTS/ws_client.py',
           'BE/application/chat/consumers.py', 'BE/application/chat/throttling.py', 'BE/application/chat/routing.py',
           'BE/application/agent/core.py', 'BE/application/agent/routing.py', 'BE/application/agent/prompts.py',
           'BE/application/agent/callbacks.py', 'BE/application/agent/providers/ai_provider.py',
           'BE/application/agent/providers/base.py', 'BE/application/management/commands/run_agent.py',
           'BE/application/manuale/answers.py', 'BE/application/manuale/tools.py', 'BE/application/mcp_server/server.py',
           'BE/instance/integration_configuration.py', 'BE/instance/restore/locking.py', 'BE/notifications/middleware.py',
           'BE/core/asgi.py') + harness.SOURCES + tuple(
    path for path in ('BE_GO/internal/handler/websocket.go', 'BE_GO/internal/handler/websocket_agent.go',
                      'BE_GO/internal/service/agent.go', 'BE_GO/internal/service/agent_routing.go',
                      'BE_GO/internal/service/agent_tools.go', 'BE_GO/internal/agentllm/client.go',
                      'BE_GO/internal/repository/agent_cache.go', 'BE_GO/cmd/run-agent/main.go', 'BE_GO/cmd/server/main.go')
    if (ROOT / path).is_file())
AGENT_KEYS = (b':1:ws_throttle:*', b':1:ws_concurrent:*', b':1:agent_save_info:*')
REVISION = 'f' * 40
asgi_application = websockets.asgi_application


# ---------------------------------------------------------------------------
# The stub OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

class Stub:
    def __init__(self):
        self.lock = threading.Lock()
        self.script, self.recorded, self.unexpected = [], [], 0
        self.on_disable = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length)
                with stub.lock:
                    stub.recorded.append({'method': 'POST', 'path': self.path, 'headers': list(self.headers.items()), 'body': body})
                    spec = stub.script.pop(0) if stub.script else None
                    if spec is None:
                        stub.unexpected += 1
                if spec is None:
                    spec = {'status': 500, 'headers': [['x-should-retry', 'false']], 'body': 'unexpected request', 'delay': 0}
                if spec.get('disable_ai') and stub.on_disable:
                    stub.on_disable()
                if spec.get('delay'):
                    time.sleep(spec['delay'])
                data = spec['body'].encode()
                try:
                    self.send_response(spec['status'])
                    for name, value in spec['headers']:
                        self.send_header(name, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    self.wfile.flush()
                except OSError:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}'

    def reset(self, script):
        with self.lock:
            self.script, self.recorded, self.unexpected = [dict(item) for item in script], [], 0

    def wait(self, count, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.recorded) >= count:
                    return
            time.sleep(0.01)
        raise contract.ContractFailure('stub.wait-requests')


STATE = {}


def manual_files(base, release):
    from application.manuale.index import EMBEDDING, FORMAT, canonical, digest
    source = contract.load_source()
    png = bytes.fromhex(source['png_hex'])
    section = json.loads(json.dumps(source['section']))
    section['screenshots'][0]['sha256'] = digest(png)
    value = {'format': FORMAT, 'embedding': EMBEDDING, 'dependencies': {}, 'chunks': [section], 'content_identity': 'd' * 64,
             'gaps': [], 'semantics': {'vocabulary': ['tag'], 'idf': [1.0], 'basis': [[1.0]], 'vectors': [[1.0]]},
             'metadata': {'application_revision': REVISION, 'release': release, 'publication_status': 'published',
                          'code_state': 'committed', 'manual_revision': 'c' * 40, 'manual_state': 'committed',
                          'verification': {'corpus_identity': 'd' * 64}}}
    value['identity'] = digest(canonical(value))
    (base / 'cache').mkdir(parents=True)
    (base / 'development').mkdir()
    (base / 'assets/images').mkdir(parents=True)
    (base / 'assets/images/tag.png').write_bytes(png)
    index = base / 'cache/index.json'
    index.write_text(canonical(value) + '\n')
    return index, {'png_sha256': digest(png), 'release': release, 'revision': REVISION, 'corpus_identity': value['identity']}


@contextmanager
def service_context(report, log):
    """The stub endpoint, the manual corpus and the Go run-agent command for both servers' lifetime."""
    from django.conf import settings
    from django.test.utils import override_settings
    stub = Stub()
    stub.thread.start()
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    with tempfile.TemporaryDirectory(prefix='az-ws-agent-', dir='/tmp') as directory:
        base = Path(directory)
        index, manual_values = manual_files(base / 'manual', settings.RUNNING_VERSION)
        manual = {'MANUAL_INDEX_PATH': str(index), 'MANUAL_ASSET_ROOT': str(base / 'manual/assets'),
                  'MANUAL_APPLICATION_REVISION': REVISION, 'MANUAL_DEVELOPMENT_ROOT': str(base / 'manual/development'),
                  'MANUAL_CORPUS_BASE_URL': '', 'MANUAL_SOURCE_ROOT': ''}
        environment = {**manual, 'ASSOZETA_MANUAL_RUN_ID': '', 'RUNNING_VERSION': settings.RUNNING_VERSION}
        command = None
        if (ROOT / 'BE_GO/cmd/run-agent').is_dir():
            command = base / 'run-agent'
            subprocess.run(['go', 'build', '-o', str(command), './cmd/run-agent'], cwd=ROOT / 'BE_GO', check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=600)
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**json.loads(saved or '{}'), **environment})
        STATE.update(stub=stub, index=index, manual_values=manual_values, manual_env=environment, go_command=command)
        try:
            with override_settings(MANUAL_INDEX_PATH=str(index), MANUAL_ASSET_ROOT=str(base / 'manual/assets'),
                                   MANUAL_APPLICATION_REVISION=REVISION, MANUAL_DEVELOPMENT_ROOT=str(base / 'manual/development'),
                                   MANUAL_CORPUS_BASE_URL='', MANUAL_SOURCE_ROOT='', MANUAL_RUN_ID='',
                                   MANUAL_BUNDLE_PATH=str(base / 'manual/absent'), MANUAL_BUNDLE_REQUIRED=False):
                with websockets.service_context(report, log):
                    contract.require(settings.DATA_RESTORE_LOCK_PATH, 'settings.restore-lock-path')
                    yield
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            stub.server.shutdown()
            stub.server.server_close()
            STATE.clear()


# ---------------------------------------------------------------------------
# Fixture rows
# ---------------------------------------------------------------------------

def seed_rows(source, created):
    from application.models import SavedReport, SportAssociation, Tags
    from instance.models import InstanceConfiguration
    contract.require(not InstanceConfiguration.objects.exists(), 'fixture.instance-configuration')
    config = InstanceConfiguration(domain='ws-agent.example.test', name='WS Agent', self_hosted=True, integration_settings={},
                                   display_settings={}, primary_association=None)
    InstanceConfiguration.objects.bulk_create([config])
    created.append((InstanceConfiguration, config.pk))
    SportAssociation._base_manager.filter(pk=source['ids']['own_association']).update(denomination=source['denomination'])
    insert_reports(source['reports'], created)
    for tag in source['tags']:
        row = Tags(tag_id=tag['tag_id'], tag_name=tag['tag_name'], sport_association_id=source['ids']['own_association'],
                   creation_date=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        Tags.objects.bulk_create([row])
        created.append((Tags, row.pk))


def insert_reports(reports, created=None):
    from application.models import SavedReport
    for report in reports:
        row = SavedReport(saved_report_id=report['saved_report_id'], sport_association_id=report['association'],
                          created_by_id=report['user'], name=report['name'], description=report['description'],
                          tool_name=report['tool_name'], params=report['params'], ui_config=report['ui_config'],
                          created_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        SavedReport.objects.bulk_create([row])
        SavedReport.objects.filter(pk=row.pk).update(updated_at=datetime.fromisoformat(report['updated_at']))
        if created is not None:
            created.append((SavedReport, row.pk))


_identity_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from rest_framework_simplejwt.tokens import AccessToken
    with _identity_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        ids = contract.load_source()['ids']
        tokens = {'garbage': 'not-a-jwt'}
        for label in ('owner', 'full_collaborator', 'restricted_collaborator', 'athlete', 'admin', 'owner_without_association'):
            token = AccessToken()
            token['user_id'] = ids[label]
            token['iss'], token['aud'] = 'https://bakney.com', 'bakney'
            tokens[label] = str(token)
        fixture['ws_tokens'] = tokens
        yield fixture


# ---------------------------------------------------------------------------
# Per-case configuration and checks
# ---------------------------------------------------------------------------

def undecryptable():
    from cryptography.fernet import Fernet
    return Fernet(Fernet.generate_key()).encrypt(b'not-this-instance').decode()


def configure_ai(source, ai, stub):
    from instance.integration_configuration import encrypt_secret
    from instance.models import InstanceConfiguration
    STATE['revision'] = STATE.get('revision', 0) + 1
    integration = {}
    if ai is not None:
        settings = {**source['ai_defaults'], 'api_key': encrypt_secret(source['api_key']), 'base_url': stub.url + '/v1'}
        for name, value in ai.items():
            settings[name] = undecryptable() if value == '<<undecryptable>>' else value
        integration = {'ai': {'settings': settings, 'revision': STATE['revision']}}
    InstanceConfiguration.objects.update(integration_settings=integration)


def set_manual(available):
    index = STATE['index']
    hidden = index.with_name('index.json.off')
    if available and not index.exists():
        hidden.rename(index)
    elif not available and index.exists():
        index.rename(hidden)


def agent_keys(raw):
    return sorted(key for pattern in AGENT_KEYS for key in raw.keys(pattern))


def run_values(source):
    from datetime import date
    from openai import AsyncOpenAI
    headers = AsyncOpenAI(api_key='x', base_url='http://127.0.0.1/v1').default_headers
    return {'stub_host': contract.endpoint_host(STATE['stub'].url), 'os': headers['X-Stainless-OS'],
            'arch': headers['X-Stainless-Arch'], 'today_dmy': date.today().strftime('%d/%m/%Y'), **STATE['manual_values']}


def schema_text(source):
    from application.mcp_server.server import tool_get_schema
    text = json.dumps(tool_get_schema(source['ids']['own_association']), default=str, ensure_ascii=False)
    if len(text) > 15000:
        text = text[:15000] + '\n\n... [risultato troncato, usa filtri più specifici o limit per ridurre i dati]'
    return text


def producer(fixture, source, case):
    from instance.models import DataRestore
    stub = STATE['stub']

    def produce(action):
        if 'ai' in action:
            configure_ai(source, action['ai'], stub)
        elif 'restore' in action:
            if action['restore']:
                DataRestore.objects.create(owner_id=source['ids']['owner'], association_id=source['ids']['own_association'],
                                           state=action['restore'], sha256='0' * 64)
            else:
                DataRestore.objects.all().delete()
        else:
            raise contract.ContractFailure('produce.unknown')
    return produce


def check_cache(raw, case, seeded, values, captures):
    expected = {f':1:{name}'.encode(): spec for name, spec in case['cache'].items()}
    concurrent = sorted(key for key in expected if key.startswith(b':1:ws_concurrent:'))
    deadline = time.monotonic() + 10
    while sorted(raw.keys(b':1:ws_concurrent:*')) != concurrent and time.monotonic() < deadline:
        time.sleep(0.02)
    keys = agent_keys(raw)
    contract.require(keys == sorted(expected), f'cache.keys:{[key.decode() for key in keys]}')
    for key, spec in expected.items():
        value, ttl = raw.get(key), raw.pttl(key)
        contract.require(0 < ttl <= spec['ttl'] * 1000, f'cache.ttl:{key.decode()}:{ttl}')
        if 'value' in spec:
            want = spec['value'].encode()
        elif 'pickle' in spec:
            want = seeded[key]
        else:
            document = json.loads(contract.render(spec['pickle_export'], values, captures, ascii_only=False))
            want = pickle.dumps({'save_info': document['save_info'], 'filename': document.get('filename', 'export')}, 4)
        contract.require(value == want, f'cache.value:{key.decode()}')


def check_reports(source, case, captures):
    from application.models import SavedReport
    effect = case['reports'] or {'deleted': [], 'created': []}
    seeded = {report['saved_report_id'] for report in source['reports']}
    present = set(str(pk) for pk in SavedReport.objects.values_list('pk', flat=True))
    contract.require(seeded - present == set(effect['deleted']), f'reports.deleted:{sorted(seeded - present)}')
    created = {}
    for row in SavedReport.objects.exclude(pk__in=seeded).values():
        created[str(row['saved_report_id'])] = row
    want = {}
    for item in effect['created']:
        identifier = contract.render(item['saved_report_id'], {}, captures)
        want[identifier] = item
    contract.require(sorted(created) == sorted(want), f'reports.created:{sorted(created)}')
    for identifier, item in want.items():
        row = created[identifier]
        actual = {'sport_association_id': str(row['sport_association_id']), 'created_by_id': str(row['created_by_id']),
                  'name': row['name'], 'description': row['description'], 'tool_name': row['tool_name'],
                  'params': row['params'], 'ui_config': row['ui_config']}
        expected = {'sport_association_id': source['ids']['own_association'], 'created_by_id': item['user'], 'name': item['name'],
                    'description': item['description'], 'tool_name': item['tool_name'], 'params': item['params'],
                    'ui_config': item['ui_config']}
        contract.require(actual == expected, f'reports.row:{identifier}')
        contract.require(row['created_at'] <= row['updated_at'] and abs((row['updated_at'] - datetime.now(dt_timezone.utc)).total_seconds()) < 600,
                         'reports.timestamps')
    # Undo the authored changes for the next case.
    SavedReport.objects.exclude(pk__in=seeded).delete()
    insert_reports([report for report in source['reports'] if report['saved_report_id'] in set(effect['deleted'])])


def cli_command(backend, source):
    import sys
    from django.conf import settings
    env = {**os.environ, **STATE['manual_env'], 'PYTHONUNBUFFERED': '0'}
    if backend == 'django':
        # System checks and interpreter warnings depend on the host (static directories,
        # app-initialisation queries), not on run_agent: they are skipped and silenced.
        env.update(PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'BE'), DJANGO_SETTINGS_MODULE='core.settings',
                   PYTHONWARNINGS='ignore')
        return [sys.executable, str(ROOT / 'BE/manage.py'), 'run_agent', '--skip-checks'], env
    contract.require(STATE['go_command'] is not None, 'go.run-agent-missing')
    database = settings.DATABASES['default']
    env.update({'DBTYPE': 'POSTGRES', 'DBNAME': database['NAME'], 'DBUSER': database['USER'], 'DBPASSWORD': database['PASSWORD'],
                'DBHOST': database['HOST'], 'DBPORT': str(database['PORT']), 'DEBUG': 'False',
                'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
                **{name: getattr(settings, name) for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY')}})
    return [str(STATE['go_command'])], env


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from django.core.cache import cache
    source = contract.load_source()
    stub = STATE['stub']
    harness.carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    substitute = websockets.substitution(fixture)
    native = not label.startswith('caddy')
    results = []
    stub.on_disable = lambda: configure_ai(source, {'enabled': False}, stub)
    schema = None
    try:
        for case in source['cases']:
            if case['kind'] == 'cli' and not native:
                continue
            try:
                for key in agent_keys(raw):
                    raw.delete(key)
                seeded = {}
                for name, spec in case['cache_seed'].items():
                    if 'value' in spec:
                        raw.set(f':1:{name}', spec['value'].encode(), px=spec['ttl'] * 1000)
                    else:
                        cache.set(name, spec['pickle'], spec['ttl'])
                    seeded[f':1:{name}'.encode()] = raw.get(f':1:{name}')
                configure_ai(source, case['ai'], stub)
                set_manual(case['manual'])
                stub.reset(case['llm'])
                values = run_values(source)
                if any('<<schema>>' in request['body'] for request in case['requests']):
                    schema = schema if schema is not None else schema_text(source)
                    values['schema'] = schema
                captures = {}
                cursor = observer.cursor()
                if case['kind'] == 'ws':
                    log = contract.run_ws_case(case, base_url, values=values, captures=captures, substitute=substitute,
                                               produce=producer(fixture, source, case), wait_requests=stub.wait)
                else:
                    command, env = cli_command(backend, source)
                    result = contract.run_cli_case(case, command, env)
                    contract.check_cli(case, result, values, captures)
                    log = [result[0]]
                time.sleep(0.05)
                with stub.lock:
                    recorded, unexpected = list(stub.recorded), stub.unexpected
                contract.require(unexpected == 0, f'stub.unexpected:{unexpected}')
                contract.check_requests(case, recorded, values, captures)
                contract.require(observer.delta(cursor) == [], 'attribution.http-requests')
                check_cache(raw, case, seeded, values, captures)
                check_reports(source, case, captures)
                configure_ai(source, None, stub)
                from instance.models import DataRestore, InstanceConfiguration
                DataRestore.objects.all().delete()
                InstanceConfiguration.objects.update(integration_settings={})
                set_manual(True)
                harness.carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            except BaseException as error:
                error.case = f'{label}:{case["name"]}'
                raise
            results.append({'case': case['name'], 'events': len(log)})
    finally:
        for key in agent_keys(raw):
            raw.delete(key)
        stub.on_disable = None
    authored = sum(1 for case in source['cases'] if native or case['kind'] == 'ws')
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': authored}
