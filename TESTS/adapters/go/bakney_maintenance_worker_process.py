"""Native maintain_bakney_pairings worker on actual Django-created current schema.

Private PostgreSQL and a private Bakney authority stub (a local HTTP server
recording every request and answering from the variant's script). Each seed
is first processed by the actual Django task (``instance.sso.tasks``) with the
same settings the worker reads (development mode so the stub's HTTP API base
is accepted, APP_URL http://localhost, placeholder UI origin, the worker's
SECRET_KEY); the native Go worker must then leave the identical complete
managed-model state on the same reseeded rows (timestamps written during the
run normalized) and send byte-identical authority requests with the same
headers. Seeds cover revocation delivery (revoked, other answers, 401,
unavailable, no remote id, undecryptable secret, created_at order), the
expiry cleanups (challenges, nonces after five minutes, logins) and the
synchronization (verified, 401 disconnect, pending with another generation,
no pending pairing). Explicit fixture producers and ownership are not
installed activation.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_bakney_worker'
KIND = 'instance.sso.tasks.maintain_bakney_pairings'
SECRET_KEY = 'private-worker-secret-canary'
VERSION = 'bakney-pairing-v1'
ORIGIN, UI = 'http://localhost', 'https://app.bakney.example'
VARIANTS = ('main', 'rejected', 'idle', 'generation')
EXPECTED = {
    'default Django owner does not claim or contact the authority',
    'main: revocations, cleanups and a verified synchronization match Django',
    'rejected: a 401 status disconnects and queues the revocation like Django',
    'idle: without a pending or paired pairing only the deliveries and cleanups run',
    'generation: a pending answer for another generation leaves the pairing unchanged',
    'unsupported version payload and tenant fail before business effects',
    'completed job replay leaves the state and the authority untouched',
}
STUB = {'lock': threading.Lock(), 'log': [], 'script': []}


def U(n):
    return UUID(int=0xB7000000 + n)


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'path': self.path, 'headers': sorted((k.lower(), v) for k, v in self.headers.items()), 'body': body})
            number = len(STUB['log'])
            reply = STUB['script'][number - 1] if number <= len(STUB['script']) else (500, {'error': 'unexpected'})
        status, payload = reply
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def status_reply(status='verified', forwarding=False, generation=None, remote=None):
    return {'protocol': VERSION, 'pairing_id': str(remote or U(3)), 'association_id': str(U(11)),
            'instance_id': None if status == 'pending' else str(U(1)), 'origin': ORIGIN, 'callback_uri': ORIGIN + '/bakney/v1/callback',
            'generation': str(generation or U(4)), 'status': status, 'forwarding': forwarding}


def disconnect_reply(remote, status='revoked'):
    return {'protocol': VERSION, 'pairing_id': str(remote), 'status': status}


class MaintenanceFixture:
    """Configuration, pairing, revocations, challenges, nonces and logins.

    Times are relative to one base instant; encrypted secrets are computed once
    so every reseed is identical.
    """

    def __init__(self, authority):
        from django.test.utils import override_settings
        from instance.sso.protocol import encrypt
        from application.models import User, SportAssociation
        self.authority = authority
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.seeding = (self.base, self.base)
        with override_settings(SECRET_KEY=SECRET_KEY):
            self.secret = encrypt('S' * 43)
            self.revocation_secret = encrypt('R' * 43)
        User.objects.bulk_create([User(user_id=U(10), username='bakney-worker-owner', email='bakney-worker-owner@example.test',
                                       password='!synthetic', role=User.ASSOCIATION)])
        SportAssociation.objects.bulk_create([SportAssociation(sport_association_id=U(11), user_id=U(10),
                                                               denomination='Associazione manutenzione', tax_code='71000009001')])

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding

        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    @staticmethod
    def normalized(state, since, until):
        def value(item):
            return 'run' if isinstance(item, datetime) and since <= item <= until else item
        return {label: [{k: value(v) for k, v in row.items()} for row in rows] for label, rows in state.items()}

    def seed(self, variant):
        from django.db import connection
        from instance.models import (InstanceConfiguration, BakneyPairing, BakneyRevocation, BakneyChallenge, BakneyNonce,
                                     BakneyLogin)
        started = datetime.now(timezone.utc)
        now, minute = self.base, timedelta(minutes=1)
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE instance_bakneypairing, instance_bakneyrevocation, instance_bakneychallenge,
              instance_bakneynonce, instance_bakneylogin, instance_instanceconfiguration RESTART IDENTITY CASCADE''')
        InstanceConfiguration.objects.bulk_create([InstanceConfiguration(domain='bakney-worker.example.test', name='Worker fixture',
                                                                         self_hosted=True, primary_association_id=U(11))])
        config = InstanceConfiguration.objects.get()
        state = {'main': 'paired', 'rejected': 'paired', 'idle': 'generated', 'generation': 'pending'}[variant]
        BakneyPairing.objects.bulk_create([BakneyPairing(
            config=config, instance_id=U(1), pairing_id=U(2), secret_encrypted=self.secret, authority=self.authority, ui_origin=UI,
            remote_pairing_id=None if state == 'generated' else U(3), remote_generation=None if state == 'generated' else U(4),
            origin=ORIGIN, association_id=U(11), association_name='Associazione manutenzione' if state == 'paired' else '',
            state=state, forwarding_enabled=state == 'paired', checked_at=now - 30 * minute if state == 'paired' else None)])
        revocations = [(21, 31, self.revocation_secret), (22, 32, self.revocation_secret), (23, 33, self.revocation_secret),
                       (24, 34, self.revocation_secret), (25, None, self.revocation_secret), (26, 36, 'gAAAAA-not-a-token')]
        if variant != 'main':
            revocations = revocations[:1]
        BakneyRevocation.objects.bulk_create([BakneyRevocation(pairing_id=U(p), remote_pairing_id=U(r) if r else None,
                                                               authority=self.authority, secret_encrypted=s) for p, r, s in revocations])
        # created_at order is the reverse of the primary keys.
        for index, (p, *_rest) in enumerate(revocations):
            BakneyRevocation.objects.filter(pk=U(p)).update(created_at=now - (index + 1) * minute)
        BakneyChallenge.objects.bulk_create([BakneyChallenge(digest='c' * 63 + str(n), pairing_id=U(2), expires_at=now + offset * minute)
                                             for n, offset in ((1, -1), (2, 30))])
        BakneyNonce.objects.bulk_create([BakneyNonce(digest='n' * 63 + str(n), expires_at=now + offset * minute)
                                         for n, offset in ((1, -10), (2, -1), (3, 1))])
        BakneyLogin.objects.bulk_create([BakneyLogin(digest='l' * 63 + str(n), pairing_id=U(pairing), state='s' * 43, stage='started',
                                                     expires_at=now + offset * minute) for n, pairing, offset in ((1, 2, -1), (2, 2, 30), (3, 9, 30))])
        self.seeding = (started, datetime.now(timezone.utc))
        return self.snapshot()

    def script(self, variant):
        disconnect = lambda remote: {'pairing_id': str(remote), 'secret': 'R' * 43, 'action': 'disconnect'}
        if variant == 'main':
            # created_at order: 26 (no request: undecryptable), 25 (no remote id), 24, 23, 22, 21.
            exchanges = [(disconnect(U(34)), (503, 'busy')), (disconnect(U(33)), (401, '')),
                         (disconnect(U(32)), (200, disconnect_reply(U(32), 'pending'))),
                         (disconnect(U(31)), (200, disconnect_reply(U(31))))]
        else:
            exchanges = [(disconnect(U(31)), (200, disconnect_reply(U(99))))]
        status = {'pairing_id': str(U(3)), 'secret': 'S' * 43, 'action': 'status'}
        if variant == 'main':
            exchanges.append((status, (200, status_reply(forwarding=False))))
        elif variant == 'rejected':
            exchanges.append((status, (401, '')))
        elif variant == 'generation':
            exchanges.append((status, (200, status_reply('pending', generation=U(5)))))
        return exchanges


class MaintenanceRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.server = _StubServer(('127.0.0.1', 0), _StubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.authority = f'http://127.0.0.1:{self.server.server_address[1]}/api'
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY=SECRET_KEY, DEBUG='True', ASSOZETA_DEPLOYMENT_MODE='development',
            APP_URL=ORIGIN, BAKNEY_SSO_API_BASE=self.authority, BAKNEY_SSO_UI_ORIGIN=UI, CORS_ORIGIN_ALLOW_ALL='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT='1', REDIS_USERNAME='default', REDIS_PASSWORD='unused',
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.fixture = MaintenanceFixture(self.authority)
        self.django = {variant: self.django_expected(variant) for variant in VARIANTS}

    def settings(self):
        from django.test.utils import override_settings
        return override_settings(SECRET_KEY=SECRET_KEY, DEBUG=True, ASSOZETA_DEPLOYMENT_MODE='development', APP_URL=ORIGIN,
                                 BAKNEY_SSO_API_BASE=self.authority, BAKNEY_SSO_UI_ORIGIN=UI)

    def arm(self, variant):
        exchanges = self.fixture.script(variant)
        with STUB['lock']:
            STUB['log'], STUB['script'] = [], [reply for _request, reply in exchanges]
        return exchanges

    def requests(self, exchanges):
        with STUB['lock']:
            log = list(STUB['log'])
        assert [entry['path'] for entry in log] == ['/api/pairing/v1/status'] * len(exchanges), [entry['path'] for entry in log]
        assert [entry['body'] for entry in log] == [json.dumps(body).encode() for body, _reply in exchanges], 'authority request bodies'
        return log

    def django_expected(self, variant):
        """Run the actual Django task on a fresh seed with the worker's settings."""
        from instance.sso import tasks
        from instance.models import BakneyPairing, BakneyRevocation, BakneyChallenge, BakneyNonce, BakneyLogin
        before = self.fixture.seed(variant)
        exchanges = self.arm(variant)
        since = datetime.now(timezone.utc)
        with self.settings():
            assert tasks.maintain_bakney_pairings.run() is None
        until = datetime.now(timezone.utc)
        after = self.fixture.snapshot()
        assert before != after
        log = self.requests(exchanges)
        pairing = BakneyPairing.objects.get()
        revocations = set(BakneyRevocation.objects.values_list('pairing_id', flat=True))
        # Authored checks that the oracle exercised the intended paths.
        challenges = set(BakneyChallenge.objects.values_list('digest', flat=True))
        assert challenges == (set() if variant == 'rejected' else {'c' * 63 + '2'}), (variant, challenges)
        assert set(BakneyNonce.objects.values_list('digest', flat=True)) == {'n' * 63 + '2', 'n' * 63 + '3'}
        if variant == 'main':
            assert revocations == {U(22), U(24), U(25), U(26)}, revocations
            assert pairing.state == 'paired' and pairing.forwarding_enabled is False and since <= pairing.checked_at <= until
            assert set(BakneyLogin.objects.values_list('digest', flat=True)) == {'l' * 63 + '2', 'l' * 63 + '3'}
        elif variant == 'rejected':
            assert revocations == {U(21), U(2)} and pairing.state == 'disconnected' and pairing.secret_encrypted == ''
            assert set(BakneyLogin.objects.values_list('digest', flat=True)) == {'l' * 63 + '3'}
        else:
            assert revocations == {U(21)} and after['instance.BakneyPairing'] == before['instance.BakneyPairing']
        self.django_log = getattr(self, 'django_log', {})
        self.django_log[variant] = [(entry['headers'], entry['body']) for entry in log]
        return self.fixture.normalized(after, since, until)

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-bakney-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND, variant='main'):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant)
        exchanges = self.arm(variant)
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, kind, version, identifier, payload, attempts))
        self.since = datetime.now(timezone.utc)
        return identifier, before, exchanges

    def matches_django(self, identifier, variant, exchanges):
        try:
            await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered Bakney maintenance worker durable completion')
        except AssertionError:
            print('JOB', self.sql('SELECT state,attempts,error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)), STUB['log'], flush=True)
            raise
        assert self.job(identifier)[:3] == ('completed', 1, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        expected = self.django[variant]
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, datetime.now(timezone.utc))
        if actual != expected:
            differences = {label: {'django': expected.get(label), 'go': actual.get(label)}
                           for label in set(expected) | set(actual) if expected.get(label) != actual.get(label)}
            (self.output / f'difference-{variant}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == expected, f'Go maintenance differs from the Django task on the {variant} seed'
        log = self.requests(exchanges)
        go_log = [(entry['headers'], entry['body']) for entry in log]
        normalize = lambda entries: [([(k, v) for k, v in headers if k != 'host'], body) for headers, body in entries]
        assert normalize(go_log) == normalize(self.django_log[variant]), f'authority requests differ on the {variant} seed'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before, _ = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        assert process.poll() is None
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        with STUB['lock']:
            assert STUB['log'] == []
        self.stop(process)
        self.case('default Django owner does not claim or contact the authority')

        self.handoff('go')
        labels = {'main': 'main: revocations, cleanups and a verified synchronization match Django',
                  'rejected': 'rejected: a 401 status disconnects and queues the revocation like Django',
                  'idle': 'idle: without a pending or paired pairing only the deliveries and cleanups run',
                  'generation': 'generation: a pending answer for another generation leaves the pairing unchanged'}
        for variant in VARIANTS:
            identifier, _, exchanges = self.seed_job(variant=variant)
            process = self.worker()
            self.matches_django(identifier, variant, exchanges)
            self.stop(process)
            self.case(labels[variant])

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(U(11))}, 'task_failed')]:
            identifier, before, _ = self.seed_job(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid maintenance envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
            with STUB['lock']:
                assert STUB['log'] == []
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, _, exchanges = self.seed_job(variant='idle')
        process = self.worker(); self.matches_django(identifier, 'idle', exchanges); self.stop(process)
        later = self.fixture.snapshot()
        with STUB['lock']:
            STUB['log'] = []
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2); self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == later
        with STUB['lock']:
            assert STUB['log'] == []
        self.case('completed job replay leaves the state and the authority untouched')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        finally:
            super().close()


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/bakney-maintenance-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-bakney-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = MaintenanceRehearsal(work, output, environment, binaries)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if attempted:
                            try: run([binaries/'pg_ctl', '-D', work/'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work/'postgres/postmaster.pid').exists(): raise
                    finally:
                        if (work/'postgres.log').exists(): shutil.copyfile(work/'postgres.log', output/'postgres.log')
                        report.write_text(json.dumps(result, indent=2)+'\n')
    paths = ['TESTS/adapters/go/bakney_maintenance_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/cmd/worker/bakney_sso.go', 'BE_GO/internal/service/bakney_sso.go',
             'BE_GO/internal/service/bakney_sso_protocol.go', 'BE_GO/internal/repository/bakney_sso.go',
             'BE_GO/internal/domain/bakney_sso.go', 'BE/instance/sso/tasks.py', 'BE/instance/sso/service.py',
             'BE/instance/sso/protocol.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
