"""Native token cleanup on actual Django-created current schema in private PostgreSQL.

The fixture queue intents and ownership changes are not installed activation.
This proves native command composition and durable process behavior; the
separate Django adapter remains the historical task-body oracle.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL

DATABASE = 'assozeta_contract_token_worker'
KIND = 'clear_expired_subscription_tokens'
EXPECTED = {
    'default Django and missing owner do not claim or mutate actual Django rows',
    'two native workers complete one cleanup with exact cutoff audit and scope',
    'unsupported version payload and tenant fail before business effects',
    'completed replay preserves a later expired token and audit history',
    'exclusive maintenance and pending restore defer cleanup claims',
    'killed admitted worker rolls back then a new worker reclaims exactly once',
    'expired final attempt exhausts without a token effect',
    'TERM drain cancellation joins rollback and releases the file lease',
    'handled audit failure rolls back tokens and records successful null completion',
}


class TokenFixture:
    """Seven explicit expiry controls plus unrelated owned sentinels."""

    def __init__(self):
        from application.models import Subscription, SubscriptionToken, SportAssociation, User
        from application.models.audit_models import AuditLogIndex
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from django.contrib.contenttypes.models import ContentType

        self.ct = ContentType.objects.get_for_model(SubscriptionToken)
        self.owners = [UUID(int=91001 + i) for i in range(2)]
        self.associations = [UUID(int=91011 + i) for i in range(2)]
        self.subscriptions = [UUID(int=91021 + i) for i in range(2)]
        with disable_auditlog():
            User.original_objects.bulk_create([
                User(user_id=pk, username=f'token-worker-{i}', role=1, password='!private-fixture')
                for i, pk in enumerate(self.owners)
            ])
            SportAssociation.original_objects.bulk_create([
                SportAssociation(sport_association_id=pk, user_id=self.owners[i],
                    denomination='Private token worker fixture', tax_code='00000000000', deleted=bool(i))
                for i, pk in enumerate(self.associations)
            ])
            Subscription.objects.bulk_create([
                Subscription(subscription_id=pk, sport_association_id=self.associations[i],
                    user_id=self.owners[i], deleted=bool(i))
                for i, pk in enumerate(self.subscriptions)
            ])
            self.sentinel = LogEntry.objects.bulk_create([LogEntry(
                content_type=ContentType.objects.get_for_model(User),
                object_pk=str(self.owners[0]), object_repr='Unrelated token worker sentinel',
                action=LogEntry.Action.UPDATE, changes={})])[0]
            AuditLogIndex.objects.create(
                log_entry=self.sentinel, sport_association_id=self.associations[0],
                resolution_path='sentinel', created_at=datetime.now(timezone.utc))

    def seed(self):
        from application.models import SubscriptionToken
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry

        self.today = datetime.now(timezone.utc).date()
        self.cutoff = datetime.combine(self.today, datetime_time.min, tzinfo=timezone.utc)
        self.started = datetime.now(timezone.utc)
        created = self.cutoff - timedelta(days=31, seconds=7, microseconds=123456)
        expiries = [
            self.cutoff - timedelta(days=30),
            self.cutoff - timedelta(microseconds=1),
            self.cutoff - timedelta(hours=2),
            self.cutoff,
            self.cutoff + timedelta(microseconds=1),
            self.cutoff + timedelta(hours=12),
            self.cutoff + timedelta(days=1),
        ]
        self.tokens = []
        for i, expiry in enumerate(expiries):
            self.tokens.append(dict(
                subscription_token_id=UUID(int=91201 + i),
                subscription_id=self.subscriptions[i % 2],
                token=UUID(int=91301 + i),
                creation_date=created,
                expiration_date=expiry,
                deleted=i < 3,
            ))
        with disable_auditlog():
            SubscriptionToken.objects.all().delete()
            LogEntry.objects.filter(content_type=self.ct).delete()
            SubscriptionToken.objects.bulk_create([
                SubscriptionToken(
                    subscription_token_id=row['subscription_token_id'],
                    subscription_id=row['subscription_id'],
                    token=row['token'],
                    creation_date=row['creation_date'],
                    expiration_date=row['expiration_date'],
                )
                for row in self.tokens
            ])
        return self.snapshot()

    def insert_expired(self):
        from application.models import SubscriptionToken
        from auditlog.context import disable_auditlog

        row = self.tokens[0]
        inserted = SubscriptionToken(
            subscription_token_id=uuid4(),
            subscription_id=row['subscription_id'],
            token=uuid4(),
            creation_date=row['creation_date'],
            expiration_date=self.cutoff - timedelta(days=1),
        )
        with disable_auditlog():
            inserted.save(force_insert=True)
        return inserted.subscription_token_id

    def snapshot(self):
        from django.apps import apps

        return {
            model._meta.label: list(model._base_manager.order_by('pk').values())
            for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy
        }

    def assert_effect(self, before):
        from application.models import SubscriptionToken
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex

        assert datetime.now(timezone.utc).date() == self.today, \
            'Fixture crossed UTC date boundary; recapture required'
        after = self.snapshot()
        retained = {row['subscription_token_id'] for row in self.tokens if not row['deleted']}
        assert {
            row['subscription_token_id']
            for row in after['application.SubscriptionToken']
        } == retained
        logs = list(LogEntry.objects.filter(content_type=self.ct))
        assert len(logs) == 3
        deleted = {row['subscription_token_id']: row for row in self.tokens if row['deleted']}
        assert {UUID(entry.object_pk): entry for entry in logs}.keys() == deleted.keys()
        for entry in logs:
            row = deleted[UUID(entry.object_pk)]
            expected = {
                'subscription_token_id': [self.stamp(row['subscription_token_id']), 'None'],
                'subscription': [self.stamp(row['subscription_id']), 'None'],
                'token': [self.stamp(row['token']), 'None'],
                'creation_date': [self.stamp(row['creation_date']), 'None'],
                'expiration_date': [self.stamp(row['expiration_date']), 'None'],
            }
            assert entry.action == LogEntry.Action.DELETE
            assert entry.object_repr == f'SubscriptionToken object ({row["subscription_token_id"]})'
            assert entry.changes == expected
            assert entry.changes_text == ''
            assert self.started <= entry.timestamp <= datetime.now(timezone.utc)
            assert all(getattr(entry, field) is None for field in (
                'actor_id', 'actor_email', 'object_id', 'serialized_data',
                'additional_data', 'remote_addr', 'remote_port', 'cid',
            ))
        assert not AuditLogIndex.objects.filter(log_entry__content_type=self.ct).exists()
        for model in before:
            if model in ('application.SubscriptionToken', 'auditlog.LogEntry'):
                continue
            assert before[model] == after[model], 'Unrelated Django model changed: ' + model
        assert [
            row for row in before['auditlog.LogEntry']
            if row['content_type_id'] != self.ct.pk
        ] == [
            row for row in after['auditlog.LogEntry']
            if row['content_type_id'] != self.ct.pk
        ]

    @staticmethod
    def stamp(value):
        if not isinstance(value, UUID):
            value = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=' ')
        else:
            value = str(value)
        return value


class TokenRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(
            DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work),
            DBPORT='5432', DBPASSWORD='private-worker-password-canary',
            SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture',
            SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='',
            AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'),
        )
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.fixture = TokenFixture()

    def connect(self, name):
        connection = psycopg2.connect(
            host=str(self.work), user='contracts', dbname=DATABASE,
            application_name='fixture-token-' + name)
        self.connections.append(connection)
        return connection

    def seed_token(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed()
        identifier = str(uuid4())
        self.sql(
            '''INSERT INTO assozeta_go_jobs(
              id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
              VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
            (identifier, association, kind, version, identifier, payload, attempts),
        )
        return identifier, before

    def token_complete(self, identifier, before, attempts=1):
        await_condition(
            lambda: self.job(identifier)[0] == 'completed',
            'registered token cleanup durable completion',
        )
        assert self.job(identifier)[:3] == ('completed', attempts, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        self.fixture.assert_effect(before)

    def block_effect(self):
        gate = self.connect('effect-row')
        with gate.cursor() as cursor:
            cursor.execute(
                'SELECT subscription_token_id FROM application_subscriptiontoken '
                'WHERE expiration_date<%s ORDER BY expiration_date LIMIT 1 FOR UPDATE',
                (self.fixture.cutoff,),
            )
            assert cursor.fetchone()
        return gate

    def effect_is_blocked(self):
        return bool(self.sql(
            """SELECT 1 FROM pg_stat_activity
               WHERE application_name='assozeta-go-worker'
               AND query LIKE '%%WITH removed AS%%'
               AND query LIKE '%%DELETE FROM application_subscriptiontoken%%'
               AND wait_event_type='Lock'"""
        ))

    def ready_claim(self, process):
        await_condition(self.observed_claim, 'actual registered worker queue query')
        assert process.poll() is None

    def quiet(self, process, identifier, before):
        self.ready_claim(process)
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == before

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_token()
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql('DELETE FROM assozeta_go_job_ownership')
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql("INSERT INTO assozeta_go_job_ownership VALUES (1,'django',1)")
        self.case('default Django and missing owner do not claim or mutate actual Django rows')

        self.handoff('go')
        first, second = self.worker(), self.worker()
        self.token_complete(identifier, before)
        self.stop(first)
        self.stop(second)
        self.case('two native workers complete one cleanup with exact cutoff audit and scope')

        for kwargs, code in [
            ({'version': 2}, 'unsupported_version'),
            ({'payload': '{"cutoff":"2099-01-01"}'}, 'task_failed'),
            ({'association': str(self.fixture.associations[0])}, 'task_failed'),
        ]:
            identifier, before = self.seed_token(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid token envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        identifier, before = self.seed_token(kind='unregistered_token_fixture')
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_token()
        process = self.worker()
        self.token_complete(identifier, before)
        self.stop(process)
        self.fixture.insert_expired()
        with_new_token = self.fixture.snapshot()
        process = self.worker()
        self.ready_claim(process)
        time.sleep(.2)
        self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1)
        assert self.fixture.snapshot() == with_new_token
        self.case('completed replay preserves a later expired token and audit history')

        identifier, before = self.seed_token()
        with (self.work / 'operations.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.worker()
            time.sleep(.3)
            assert self.job(identifier)[:2] == ('pending', 0)
            assert self.fixture.snapshot() == before
            self.stop(process)
        from instance.models import DataRestore
        restore = DataRestore.objects.create(
            state='running', owner_id=self.fixture.owners[0],
            association_id=self.fixture.associations[0], sha256='0' * 64)
        held = self.fixture.snapshot()
        process = self.worker()
        time.sleep(.3)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == held
        self.stop(process)
        restore.delete()
        process = self.worker()
        self.token_complete(identifier, before)
        self.stop(process)
        self.case('exclusive maintenance and pending restore defer cleanup claims')

        identifier, before = self.seed_token()
        gate = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'token cleanup row lock')
        self.assert_file_lease(True)
        process.kill()
        assert process.wait(timeout=8) == -signal.SIGKILL
        self.assert_file_lease(False)
        gate.rollback()
        await_condition(lambda: not self.effect_is_blocked(), 'killed worker cleanup rollback')
        assert self.fixture.snapshot() == before
        assert self.job(identifier)[:2] == ('running', 1)
        process = self.worker()
        self.token_complete(identifier, before, attempts=2)
        self.stop(process)
        self.case('killed admitted worker rolls back then a new worker reclaims exactly once')

        identifier, before = self.seed_token(attempts=1)
        gate = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'final cleanup attempt admitted')
        process.kill()
        assert process.wait(timeout=8) == -signal.SIGKILL
        gate.rollback()
        self.assert_file_lease(False)
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'expired final cleanup attempt exhausted')
        self.stop(process)
        assert self.job(identifier)[:2] == ('failed', 1)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('lease_expired',)]
        assert self.fixture.snapshot() == before
        self.case('expired final attempt exhausts without a token effect')

        identifier, before = self.seed_token()
        gate = self.block_effect()
        process = self.worker('--drain-timeout', '250ms')
        await_condition(self.effect_is_blocked, 'cancellation token cleanup row lock')
        process.send_signal(signal.SIGTERM)
        self.assert_file_lease(True)
        assert process.wait(timeout=8) == 1
        self.assert_file_lease(False)
        assert not self.effect_is_blocked()
        assert self.fixture.snapshot() == before
        assert self.job(identifier)[:2] == ('pending', 1)
        gate.rollback()
        process = self.worker()
        self.token_complete(identifier, before, attempts=2)
        self.stop(process)
        self.case('TERM drain cancellation joins rollback and releases the file lease')

        identifier, before = self.seed_token()
        self.sql(
            "CREATE FUNCTION fixture_token_audit_failure() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'private-worker-password-canary'; END $$")
        self.sql(
            'CREATE TRIGGER fixture_token_audit_failure BEFORE INSERT ON auditlog_logentry '
            'FOR EACH STATEMENT EXECUTE FUNCTION fixture_token_audit_failure()')
        try:
            process = self.worker()
            await_condition(
                lambda: self.job(identifier)[0] == 'completed',
                'handled token audit failure completion',
            )
            self.stop(process)
            assert self.job(identifier)[:2] == ('completed', 1)
            assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
            assert self.fixture.snapshot() == before
            logs = ''.join(log.read_text() for _, _, log in self.children)
            assert 'subscription_token_cleanup_failed' in logs
            assert 'private-worker-password-canary' not in logs
        finally:
            self.sql('DROP TRIGGER fixture_token_audit_failure ON auditlog_logentry')
            self.sql('DROP FUNCTION fixture_token_audit_failure()')
        self.case('handled audit failure rolls back tokens and records successful null completion')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/token-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {
        'success': False,
        'scope': __doc__.strip(),
        'schema': 'actual Django current models; no historical migration proof',
    }
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    environment = {
        key: value for key, value in os.environ.items()
        if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')
    }
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-token-worker-', dir='/tmp') as directory, \
            (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False
        rehearsal = None

        def run(args):
            return subprocess.run(
                [str(arg) for arg in args], env=environment, stdout=log,
                stderr=subprocess.STDOUT, check=True, timeout=120)

        try:
            for name in ('worker', 'migrate'):
                subprocess.run(
                    ['go', 'build', '-race', '-o', str(work / name), './cmd/' + name],
                    cwd=ROOT / 'BE_GO', env=environment, stdout=log,
                    stderr=subprocess.STDOUT, check=True, timeout=120)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                 '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                 '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = TokenRehearsal(work, output, environment, binaries)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(
                        scenarios=rehearsal.completed,
                        scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if attempted:
                            try:
                                run([binaries / 'pg_ctl', '-D', work / 'postgres',
                                     '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work / 'postgres/postmaster.pid').exists():
                                    raise
                    finally:
                        if (work / 'postgres.log').exists():
                            shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                        report.write_text(json.dumps(result, indent=2) + '\n')
    paths = [
        'TESTS/adapters/go/token_worker_process.py',
        'BE_GO/cmd/worker/main.go',
        'BE_GO/internal/domain/subscription_token_cleanup.go',
        'BE_GO/internal/repository/subscription_token_cleanup.go',
        'BE_GO/internal/service/subscription_token_cleanup.go',
    ]
    result.update(
        success=True,
        private_postgres_stopped=True,
        race_instrumented=True,
        source_hashes={
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in paths
        },
    )
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
