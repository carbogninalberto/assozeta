"""Native delete_users_with_request worker on actual Django-created current schema.

Private PostgreSQL and a private Redis broker. Each seeded fixture is first
processed by the actual Django ``delete_users_with_request`` task, publishing
to the private broker like production (default queue ``celery``); the native Go
worker must then leave the identical complete managed-model state on the same
reseeded rows (audit ids and timestamps normalized) and publish the same
``send_email_text`` messages (protocol 2 envelope checked byte for byte except
the random ids, origin and publish stamp). Explicit fixture producers and
ownership are not installed activation.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_user_deletion_worker'
KIND = 'delete_users_with_request'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
EXPECTED = {
    'default Django owner does not claim or mutate users',
    'native worker reproduces the Django task state and notices',
    'unsupported version payload and tenant fail before business effects',
    'completed job replay leaves later deletion requests untouched',
    'failed save after the notice keeps the audit entry and completes the job',
}


class Contract:
    def require(self, condition, label):
        assert condition, label


class DeletionFixture:
    """Users on both sides of every filter, with and without an association."""
    def __init__(self):
        from application.models import User, SportAssociation
        self.base = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
        self.users = [UUID(int=83001 + i) for i in range(9)]
        self.associations = [UUID(int=83101 + i) for i in range(2)]
        User.original_objects.bulk_create([User(user_id=pk, username=f'deletion-{i}', role=1 if i < 3 else 2,
            password='!private-fixture', date_joined=self.base) for i, pk in enumerate(self.users)])
        # user 0 owns an active association, user 1 a deleted one (one per user).
        owners = [(0, False), (1, True)]
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=self.associations[i],
            user_id=self.users[owner], denomination='Private deletion fixture', tax_code='00000000000', deleted=deleted)
            for i, (owner, deleted) in enumerate(owners)])

    def controls(self, today):
        # first name, email, delete_on, is_active, deleted
        return [
            ('Anna', 'anna@example.test', today - timedelta(days=3), True, False),
            ("Zoë O'Neil \"quoted\"", 'zoe@example.test', today, True, False),
            ('', 'Mixed.Case@Example.Test', today - timedelta(days=30), True, False),
            ('Later', 'later@example.test', today + timedelta(days=1), True, False),
            ('None', 'none@example.test', None, True, False),
            ('Inactive', 'inactive@example.test', today - timedelta(days=1), False, False),
            ('Deleted', 'deleted@example.test', today - timedelta(days=1), True, True),
            ('Nobody', '', today - timedelta(days=2), True, False),
            ('Spare', 'spare@example.test', None, True, False),
        ]

    def seed(self, *, conflict=False):
        from application.models import User
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        today = datetime.now(timezone.utc).date()
        with disable_auditlog():
            AuditLogIndex.objects.all().delete()
            LogEntry.objects.all().delete()
            for pk, (first, email, delete_on, active, deleted) in zip(self.users, self.controls(today)):
                values = dict(first_name=first, email=email, delete_on=delete_on, is_active=active, deleted=deleted,
                              username=f'deletion-{pk.int - 83001}')
                if conflict:
                    # Only user 0 is due; the spare user already holds its suffixed username.
                    values.update(delete_on=today if pk == self.users[0] else None)
                    if pk == self.users[8]:
                        values.update(username='deletion-0__deleted')
                User.original_objects.filter(pk=pk).update(**values)
        return self.snapshot()

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    @staticmethod
    def normalized(state, since, until):
        """Audit ids, index ids and timestamps vary by run; everything else must match."""
        state = dict(state)
        entries = state['auditlog.LogEntry']
        keys = {}
        rows = []
        for entry in entries:
            entry = dict(entry)
            assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
            keys[entry['id']] = (entry['object_pk'], entry['action'])
            entry.pop('id'); entry['timestamp'] = 'run'
            rows.append(entry)
        state['auditlog.LogEntry'] = sorted(rows, key=lambda row: (row['object_pk'], row['action']))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id'); index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: row['log_entry_id'])
        return state

    def expected_messages(self, *, conflict=False):
        today = datetime.now(timezone.utc).date()
        due = [(first, email) for pk, (first, email, delete_on, active, deleted) in zip(self.users, self.controls(today))
               if active and not deleted and delete_on is not None and delete_on <= today]
        if conflict:
            due = due[:1]
        return sorted(({'task': 'send_email_text', 'args': [], 'kwargs': [
            ['subject', f'{BRAND} | Il tuo account è stato eliminato'],
            ['message', f'Ciao {first}, Il tuo account è stato eliminato dal sistema perché hai richiesto la cancellazione 30 giorni fa. '],
            ['recipient_list', [email]]]} for first, email in due), key=lambda spec: spec['kwargs'][2][1][0])


class Broker:
    def __init__(self, port):
        import redis
        self.client = redis.Redis(host='127.0.0.1', port=port, db=0, password=REDIS_PASSWORD,
                                  socket_timeout=5, socket_connect_timeout=5)

    def messages(self, expected, since, until):
        from TESTS.adapters.django.family_harness import check_celery_message, CELERY_BINDING
        raw = [item.decode() for item in reversed(self.client.lrange('celery', 0, -1))]
        self.client.delete('celery')
        assert len(raw) == len(expected), f'published {len(raw)} messages, expected {len(expected)}'
        recipients = sorted(raw, key=lambda item: self.recipient(item))
        checked = [check_celery_message(Contract(), item, spec, since=since, until=until, where=f':{i}')
                   for i, (item, spec) in enumerate(zip(recipients, expected))]
        if expected:
            key, member = CELERY_BINDING
            assert {m.decode() for m in self.client.smembers(key)} == {member}, 'celery.binding'
        return checked

    @staticmethod
    def recipient(raw):
        import base64
        message = json.loads(raw)
        return json.loads(base64.b64decode(message['body']))[1]['recipient_list'][0]


class DeletionRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, redis_port):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT=str(redis_port), REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD,
            WHITELABEL_NAME=BRAND, DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.broker = Broker(redis_port)
        self.fixture = DeletionFixture()
        self.django = {conflict: self.django_expected(redis_port, conflict=conflict) for conflict in (False, True)}

    def django_expected(self, redis_port, *, conflict):
        """Run the actual Django task on a fresh seed with the production broker settings."""
        from contextlib import redirect_stdout
        import io
        from django.test import override_settings
        from core.celery import app
        from TESTS.adapters.django.family_harness import _reset_celery_app
        from application import tasks
        before = self.fixture.seed(conflict=conflict)
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{redis_port}', 'celery', None))))
        _reset_celery_app(app)
        since = datetime.now(timezone.utc)
        started = time.time()
        brand = tasks.WHITELABEL_NAME
        try:
            # The task module binds core.settings.WHITELABEL_NAME at import.
            tasks.WHITELABEL_NAME = BRAND
            with override_settings(WHITELABEL_NAME=BRAND), redirect_stdout(io.StringIO()):
                assert tasks.delete_users_with_request.run() is None
        finally:
            tasks.WHITELABEL_NAME = brand
            app.conf.update(saved)
            _reset_celery_app(app)
        until = datetime.now(timezone.utc)
        messages = self.broker.messages(self.fixture.expected_messages(conflict=conflict), started, time.time())
        after = self.fixture.snapshot()
        assert before != after
        # Authored checks that the oracle exercised the intended paths.
        users = {row['user_id']: row for row in after['application.User']}
        if conflict:
            assert len(after['auditlog.LogEntry']) == 1 and users[self.fixture.users[0]]['deleted'] is False
        else:
            deleted = {pk for pk, row in users.items() if row['username'].endswith('__deleted')}
            assert deleted == {self.fixture.users[i] for i in (0, 1, 2, 7)}, deleted
            assert len(after['application.AuditLogIndex']) == 1
        return self.fixture.normalized(after, since, until), [m['body'] for m in messages]

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-deletion-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND, conflict=False):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(conflict=conflict)
        self.broker.client.delete('celery')
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, kind, version, identifier, payload, attempts))
        self.started, self.since = time.time(), datetime.now(timezone.utc)
        return identifier, before

    def deletion_complete(self, identifier, attempts=1, conflict=False):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered deletion worker durable completion')
        assert self.job(identifier)[:3] == ('completed', attempts, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        state, bodies = self.django[conflict]
        until = datetime.now(timezone.utc)
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, until)
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / 'difference.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, 'Go deletion differs from the Django task on the same seed'
        messages = self.broker.messages(self.fixture.expected_messages(conflict=conflict), self.started, time.time())
        assert [m['body'] for m in messages] == bodies, 'Go notices differ from the Django task'

    def quiet(self, process, identifier, before):
        await_condition(self.observed_claim, 'actual registered worker queue query')
        assert process.poll() is None
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == before and self.broker.client.llen('celery') == 0

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.case('default Django owner does not claim or mutate users')

        self.handoff('go')
        process = self.worker()
        self.deletion_complete(identifier)
        self.stop(process)
        self.case('native worker reproduces the Django task state and notices')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(self.fixture.associations[0])}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid deletion envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before and self.broker.client.llen('celery') == 0
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_job()
        process = self.worker(); self.deletion_complete(identifier); self.stop(process)
        from application.models import User
        User.original_objects.filter(pk=self.fixture.users[3]).update(delete_on=datetime.now(timezone.utc).date())
        later = self.fixture.snapshot()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2); self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == later
        assert self.broker.client.llen('celery') == 0
        self.case('completed job replay leaves later deletion requests untouched')

        identifier, before = self.seed_job(conflict=True)
        process = self.worker(); self.deletion_complete(identifier, conflict=True); self.stop(process)
        self.case('failed save after the notice keeps the audit entry and completes the job')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def close(self):
        try:
            super().close()
        finally:
            self.broker.client.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/user-deletion-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result)+'\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-deletion-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None; redis_process = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1',
                '--requirepass', REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                stdout=log, stderr=subprocess.STDOUT)
            run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = DeletionRehearsal(work, output, environment, binaries, redis_port)
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
                        if redis_process:
                            redis_process.terminate(); redis_process.wait(timeout=10)
                        if attempted:
                            try: run([binaries/'pg_ctl', '-D', work/'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work/'postgres/postmaster.pid').exists(): raise
                    finally:
                        if (work/'postgres.log').exists(): shutil.copyfile(work/'postgres.log', output/'postgres.log')
                        report.write_text(json.dumps(result, indent=2)+'\n')
    paths = ['TESTS/adapters/go/user_deletion_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/user_deletion_requests.go',
             'BE_GO/internal/repository/user_deletion_requests.go', 'BE_GO/internal/domain/user_deletion_requests.go',
             'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
