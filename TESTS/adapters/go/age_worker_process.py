"""Native age worker on actual Django-created current schema in private PostgreSQL.

Explicit fixture producers/ownership are not installed activation. The existing
task-body oracle separately compares the historical Django task. This rehearsal
proves the native command composition, durable attempts, actual audit/index
effects and OS/process lifetime; it does not migrate historical application data.
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
from uuid import uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import AgeFixture, KIND, initialize_current_schema

DATABASE = 'assozeta_contract_age_worker'
EXPECTED = {
    'default Django and missing owner do not claim or mutate actual Django rows',
    'two native workers complete one age job with exact scope audit and index effects',
    'unsupported version payload and tenant fail before business effects',
    'completed job replay preserves later operator corrections',
    'exclusive maintenance and pending restore defer age claims',
    'killed admitted worker rolls back then a new worker reclaims exactly once',
    'expired final attempt exhausts without an age effect',
    'TERM drain cancellation joins rollback and releases the file lease',
    'handled audit failure rolls back effects and records successful null completion',
}


class AgeRehearsal(Rehearsal):
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
        self.fixture = AgeFixture()

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-age-' + name)
        self.connections.append(connection)
        return connection

    def seed_age(self, *, version=1, payload='{}', association=NIL, attempts=3, kind=KIND):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, kind, version, identifier, payload, attempts))
        return identifier, before

    def age_complete(self, identifier, before, attempts=1):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered age worker durable completion')
        assert self.job(identifier)[:3] == ('completed', attempts, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        self.fixture.assert_effect(before)

    def block_effect(self):
        gate = self.connect('effect-row')
        with gate.cursor() as cursor:
            cursor.execute('SELECT associate_id FROM application_associate WHERE associate_id=%s FOR UPDATE',
                           (str(self.fixture.records[2][0]),))
        return gate

    def effect_is_blocked(self):
        return bool(self.sql("""SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-worker'
          AND query LIKE 'WITH changed AS (%%' AND query LIKE '%%UPDATE application_associate%%'
          AND wait_event_type='Lock'"""))

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
        identifier, before = self.seed_age()
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql('DELETE FROM assozeta_go_job_ownership')
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.sql("INSERT INTO assozeta_go_job_ownership VALUES (1,'django',1)")
        self.case('default Django and missing owner do not claim or mutate actual Django rows')

        # This is the decisive pre-registration RED: the old worker runs its
        # real queue loop but cannot claim the existing supported age envelope.
        self.handoff('go')
        first, second = self.worker(), self.worker()
        self.age_complete(identifier, before)
        self.stop(first); self.stop(second)
        self.case('two native workers complete one age job with exact scope audit and index effects')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(self.fixture.associations[0])}, 'task_failed')]:
            identifier, before = self.seed_age(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid age envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        identifier, before = self.seed_age(kind='unregistered_age_fixture')
        process = self.worker(); self.quiet(process, identifier, before); self.stop(process)
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_age()
        process = self.worker(); self.age_complete(identifier, before); self.stop(process)
        from application.models import Associate
        Associate._base_manager.filter(pk=self.fixture.records[2][0]).update(is_minor=True, notes='later operator correction')
        corrected = self.fixture.snapshot()
        process = self.worker(); self.ready_claim(process); time.sleep(.2); self.stop(process)
        assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == corrected
        self.case('completed job replay preserves later operator corrections')

        identifier, before = self.seed_age()
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
        process = self.worker(); self.age_complete(identifier, before); self.stop(process)
        self.case('exclusive maintenance and pending restore defer age claims')

        identifier, before = self.seed_age()
        gate = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'age effect row lock')
        self.assert_file_lease(True)
        process.kill(); assert process.wait(timeout=8) == -signal.SIGKILL
        self.assert_file_lease(False)
        gate.rollback()
        await_condition(lambda: not self.effect_is_blocked(), 'killed worker server effect rollback')
        assert self.fixture.snapshot() == before and self.job(identifier)[:2] == ('running', 1)
        process = self.worker(); self.age_complete(identifier, before, attempts=2); self.stop(process)
        self.case('killed admitted worker rolls back then a new worker reclaims exactly once')

        identifier, before = self.seed_age(attempts=1)
        gate = self.block_effect(); process = self.worker()
        await_condition(self.effect_is_blocked, 'final attempt admitted')
        process.kill(); assert process.wait(timeout=8) == -signal.SIGKILL
        gate.rollback(); self.assert_file_lease(False)
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'expired final attempt exhausted')
        self.stop(process)
        assert self.job(identifier)[:2] == ('failed', 1) and self.fixture.snapshot() == before
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('lease_expired',)]
        self.case('expired final attempt exhausts without an age effect')

        identifier, before = self.seed_age()
        gate = self.block_effect(); process = self.worker('--drain-timeout', '250ms')
        await_condition(self.effect_is_blocked, 'cancellation effect row lock')
        process.send_signal(signal.SIGTERM)
        self.assert_file_lease(True)
        assert process.wait(timeout=8) == 1
        self.assert_file_lease(False)
        assert not self.effect_is_blocked() and self.fixture.snapshot() == before
        assert self.job(identifier)[:2] == ('pending', 1)
        gate.rollback()
        process = self.worker(); self.age_complete(identifier, before, attempts=2); self.stop(process)
        self.case('TERM drain cancellation joins rollback and releases the file lease')

        identifier, before = self.seed_age()
        self.sql("CREATE FUNCTION fixture_age_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private-worker-password-canary'; END $$")
        self.sql('CREATE TRIGGER fixture_age_audit_failure BEFORE INSERT ON application_auditlogindex FOR EACH STATEMENT EXECUTE FUNCTION fixture_age_audit_failure()')
        try:
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'completed', 'handled audit failure completion')
            self.stop(process)
            assert self.job(identifier)[:2] == ('completed', 1) and self.fixture.snapshot() == before
            assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        finally:
            self.sql('DROP TRIGGER fixture_age_audit_failure ON application_auditlogindex')
            self.sql('DROP FUNCTION fixture_age_audit_failure()')
        self.case('handled audit failure rolls back effects and records successful null completion')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/age-worker-process'
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
    with tempfile.TemporaryDirectory(prefix='az-age-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
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
            rehearsal = AgeRehearsal(work, output, environment, binaries)
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
    paths = ['TESTS/adapters/go/age_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/associate_age_update.go',
             'BE_GO/internal/repository/associate_age_update.go', 'BE_GO/internal/domain/associate_age_update.go']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
