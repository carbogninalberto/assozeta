"""Native audit retention worker on actual Django-created current schema in private PostgreSQL.

Each seeded fixture is first processed by the actual Django ``delete_old_audit_logs``
task; the native Go worker must then leave the identical complete managed-model
state on the same reseeded rows. Explicit fixture producers/ownership are not
installed activation. Boundary-exact cutoff, collector races and statement costs
are covered by the separate task-body oracle (TESTS/adapters/django/audit_retention.py).
"""
import fcntl
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import signal
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

DATABASE = 'assozeta_contract_audit_worker'
KIND = 'delete_old_audit_logs'
EXPECTED = {
    'default Django and missing owner do not claim or mutate audit rows',
    'two native workers complete one retention job with the Django task effect',
    'unsupported version payload and tenant fail before business effects',
    'completed job replay preserves later audit records',
    'exclusive maintenance and pending restore defer retention claims',
    'killed admitted worker rolls back then a new worker reclaims exactly once',
    'expired final attempt exhausts without a retention effect',
    'TERM drain cancellation joins rollback and releases the file lease',
    'handled deletion failure rolls back effects and records successful null completion',
}


class RetentionFixture:
    """Audit rows on both sides of the 365-day cutoff, with and without indexes."""
    def __init__(self):
        from application.models import User, SportAssociation, Associate
        from django.contrib.contenttypes.models import ContentType
        self.owners = [UUID(int=81001 + i) for i in range(2)]
        self.associations = [UUID(int=81011 + i) for i in range(2)]
        User.original_objects.bulk_create([User(user_id=pk, username=f'audit-worker-{i}', role=1,
            password='!private-fixture') for i, pk in enumerate(self.owners)])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk,
            user_id=self.owners[i], denomination='Private retention fixture', tax_code='00000000000',
            deleted=bool(i)) for i, pk in enumerate(self.associations)])
        self.content_types = [ContentType.objects.get_for_model(model).pk for model in (User, Associate)]
        # One frozen base keeps every reseed identical. Each age is at least a day
        # from the cutoff, so Django and Go clocks minutes apart select the same rows.
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        # days before base, indexed, association, expected deletion
        self.controls = [(900, True, 0, True), (400, False, 1, True), (366, True, 1, True),
                         (364, True, 0, False), (30, False, 0, False), (0, True, 1, False), (-1, False, 0, False)]

    def rows(self):
        return [dict(id=810100 + i, stamp=self.base - timedelta(days=days), indexed=indexed,
                     association=self.associations[association], expired=expired)
                for i, (days, indexed, association, expired) in enumerate(self.controls)]

    def seed(self):
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        with disable_auditlog():
            LogEntry.objects.all().delete()
        records = self.rows()
        LogEntry.objects.bulk_create([LogEntry(id=r['id'], content_type_id=self.content_types[r['id'] % 2],
            object_pk=f"retention-{r['id']}", object_repr='Retention worker fixture', action=LogEntry.Action.UPDATE,
            timestamp=r['stamp'], actor_id=self.owners[r['id'] % 2], changes={'kept': ['old', 'new']},
            cid='synthetic-retention-worker') for r in records])
        AuditLogIndex.objects.bulk_create([AuditLogIndex(audit_log_index_id=UUID(int=820000 + r['id']),
            log_entry_id=r['id'], sport_association_id=r['association'], resolution_path='fixture',
            created_at=self.base) for r in records if r['indexed']])
        return self.snapshot()

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def django_expected(self):
        """Run the actual Django task once on a fresh seed and return its state."""
        from contextlib import redirect_stdout
        import io
        from application import tasks
        before = self.seed()
        with redirect_stdout(io.StringIO()):
            assert tasks.delete_old_audit_logs.run() is None
        after = self.snapshot()
        expired = {r['id'] for r in self.rows() if r['expired']}
        # Independent authored check that the oracle exercised both sides of the cutoff.
        assert [r['id'] for r in after['auditlog.LogEntry']] == [r['id'] for r in before['auditlog.LogEntry'] if r['id'] not in expired]
        assert [r['log_entry_id'] for r in after['application.AuditLogIndex']] == [
            r['log_entry_id'] for r in before['application.AuditLogIndex'] if r['log_entry_id'] not in expired]
        assert len(before['auditlog.LogEntry']) - len(after['auditlog.LogEntry']) == 3
        assert len(before['application.AuditLogIndex']) - len(after['application.AuditLogIndex']) == 2
        return after


class RetentionRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries):
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
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.fixture = RetentionFixture()
        self.django_after = self.fixture.django_expected()

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-retention-' + name)
        self.connections.append(connection)
        return connection

    def seed_retention(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, kind, version, identifier, payload, attempts))
        return identifier, before

    def retention_complete(self, identifier, attempts=1):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered retention worker durable completion')
        assert self.job(identifier)[:3] == ('completed', attempts, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        assert self.fixture.snapshot() == self.django_after, 'Go retention differs from the Django task on the same seed'

    def block_effect(self):
        gate = self.connect('effect-row')
        with gate.cursor() as cursor:
            cursor.execute('SELECT id FROM auditlog_logentry WHERE id=%s FOR UPDATE', (810100,))
        return gate

    def effect_is_blocked(self):
        return bool(self.sql("""SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-worker'
          AND query LIKE 'WITH expired_audits AS (%%' AND wait_event_type='Lock'"""))

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
        identifier, before = self.seed_retention()
        assert before != self.django_after
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql('DELETE FROM assozeta_go_job_ownership')
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql("INSERT INTO assozeta_go_job_ownership VALUES (1,'django',1)")
        self.case('default Django and missing owner do not claim or mutate audit rows')

        # Decisive pre-registration RED: a worker without the registration runs
        # its real queue loop but cannot claim the supported retention envelope.
        self.handoff('go')
        first, second = self.worker(), self.worker()
        self.retention_complete(identifier)
        self.stop(first); self.stop(second)
        self.case('two native workers complete one retention job with the Django task effect')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(self.fixture.associations[0])}, 'task_failed')]:
            identifier, before = self.seed_retention(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid retention envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        identifier, before = self.seed_retention(kind='unregistered_retention_fixture')
        process = self.worker(); self.quiet(process, identifier, before); self.stop(process)
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_retention()
        process = self.worker(); self.retention_complete(identifier); self.stop(process)
        from auditlog.models import LogEntry
        LogEntry.objects.bulk_create([LogEntry(id=810900, content_type_id=self.fixture.content_types[0],
            object_pk='retention-late', object_repr='Later expired record', action=LogEntry.Action.UPDATE,
            timestamp=self.fixture.base - timedelta(days=800), changes={})])
        later = self.fixture.snapshot()
        process = self.worker(); self.ready_claim(process); time.sleep(.2); self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == later
        self.case('completed job replay preserves later audit records')

        identifier, before = self.seed_retention()
        with (self.work / 'operations.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.worker(); time.sleep(.3)
            assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
            self.stop(process)
        from instance.models import DataRestore
        restore = DataRestore.objects.create(state='running', owner_id=self.fixture.owners[0],
            association_id=self.fixture.associations[0], sha256='0' * 64)
        held = self.fixture.snapshot()
        process = self.worker(); time.sleep(.3)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == held
        self.stop(process); restore.delete()
        process = self.worker(); self.retention_complete(identifier); self.stop(process)
        self.case('exclusive maintenance and pending restore defer retention claims')

        identifier, before = self.seed_retention()
        gate = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'retention effect row lock')
        self.assert_file_lease(True)
        process.kill(); assert process.wait(timeout=8) == -signal.SIGKILL
        self.assert_file_lease(False)
        gate.rollback()
        await_condition(lambda: not self.effect_is_blocked(), 'killed worker server effect rollback')
        assert self.fixture.snapshot() == before and self.job(identifier)[:2] == ('running', 1)
        process = self.worker(); self.retention_complete(identifier, attempts=2); self.stop(process)
        self.case('killed admitted worker rolls back then a new worker reclaims exactly once')

        identifier, before = self.seed_retention(attempts=1)
        gate = self.block_effect(); process = self.worker()
        await_condition(self.effect_is_blocked, 'final attempt admitted')
        process.kill(); assert process.wait(timeout=8) == -signal.SIGKILL
        gate.rollback(); self.assert_file_lease(False)
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'expired final attempt exhausted')
        self.stop(process)
        assert self.job(identifier)[:2] == ('failed', 1) and self.fixture.snapshot() == before
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('lease_expired',)]
        self.case('expired final attempt exhausts without a retention effect')

        identifier, before = self.seed_retention()
        gate = self.block_effect(); process = self.worker('--drain-timeout', '250ms')
        await_condition(self.effect_is_blocked, 'cancellation effect row lock')
        process.send_signal(signal.SIGTERM)
        self.assert_file_lease(True)
        assert process.wait(timeout=8) == 1
        self.assert_file_lease(False)
        assert not self.effect_is_blocked() and self.fixture.snapshot() == before
        assert self.job(identifier)[:2] == ('pending', 1)
        gate.rollback()
        process = self.worker(); self.retention_complete(identifier, attempts=2); self.stop(process)
        self.case('TERM drain cancellation joins rollback and releases the file lease')

        identifier, before = self.seed_retention()
        self.sql("CREATE FUNCTION fixture_retention_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private-worker-password-canary'; END $$")
        self.sql('CREATE TRIGGER fixture_retention_failure BEFORE DELETE ON application_auditlogindex FOR EACH STATEMENT EXECUTE FUNCTION fixture_retention_failure()')
        try:
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'completed', 'handled deletion failure completion')
            self.stop(process)
            assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == before
            assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        finally:
            self.sql('DROP TRIGGER fixture_retention_failure ON application_auditlogindex')
            self.sql('DROP FUNCTION fixture_retention_failure()')
        self.case('handled deletion failure rolls back effects and records successful null completion')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/audit-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result)+'\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-audit-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = RetentionRehearsal(work, output, environment, binaries)
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
    paths = ['TESTS/adapters/go/audit_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/audit_retention.go',
             'BE_GO/internal/repository/audit_retention.go', 'BE_GO/internal/domain/audit_retention.go',
             'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
