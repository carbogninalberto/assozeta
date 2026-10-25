"""Native worker/migration boundary on private PostgreSQL, never application data.

The tiny business table and trigger are synthetic transaction sentinels, not a
Django schema or historical task-parity oracle. Ownership changes and producers
exist only in this trusted fixture. No installed service or scheduler is started.
Run with the existing Django test Python environment (psycopg2 is required).
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
KIND = 'reset_communication_daily_email_balance'
NIL = '00000000-0000-0000-0000-000000000000'


def process_source_hashes():
    """Bind native process builds and their actual Django/fixture inputs."""
    paths = {path for path in (ROOT / 'BE_GO').rglob('*')
             if path.is_file() and not any(part in ('quality-reports', '.git')
                 for part in path.relative_to(ROOT).parts)}
    paths.update((ROOT / 'BE').rglob('*.py'))
    paths.update((ROOT / 'TESTS/adapters').rglob('*.py'))
    paths.add(ROOT / 'selfhost/.env.example')
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def await_condition(predicate, description, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError('Timed out: ' + description)


class Rehearsal:
    def __init__(self, work, output, environment, binaries):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = {'DBTYPE': 'POSTGRES', 'DBNAME': 'worker_contracts', 'DBUSER': 'contracts',
                  'DBPASSWORD': 'private-worker-password-canary', 'DBHOST': str(work), 'DBPORT': '5432',
                  'DEBUG': 'False', 'SECRET_KEY': 'private-worker-secret-canary',
                  'JWT_SECRET_KEY': 'private-fixture-key', 'JWT_PUBLIC_KEY': 'private-fixture-public',
                  'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '',
                  'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture',
                  'AWS_STORAGE_BUCKET_NAME': 'fixture', 'AWS_S3_REGION_NAME': 'us-east-1',
                  'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9000', 'AWS_LOCATION': 'private',
                  'DATA_RESTORE_LOCK_PATH': str(work / 'operations.lock')}
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.sql('CREATE TABLE django_migrations (id integer PRIMARY KEY, app text, name text)')
        self.sql("INSERT INTO django_migrations VALUES (7,'fixture','unchanged')")
        self.sql('CREATE TABLE communications_communicationconfiguration (id integer PRIMARY KEY, daily_email_balance integer)')
        self.sql('CREATE TABLE instance_datarestore (state text, completed_at timestamptz)')
        self.sql('CREATE TABLE fixture_effects (old_balance integer, new_balance integer)')
        self.sql('''CREATE FUNCTION fixture_record_effect() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN INSERT INTO fixture_effects VALUES (OLD.daily_email_balance,NEW.daily_email_balance); RETURN NEW; END $$''')
        self.sql('''CREATE TRIGGER fixture_effect AFTER UPDATE ON communications_communicationconfiguration
          FOR EACH ROW WHEN (OLD.daily_email_balance IS DISTINCT FROM NEW.daily_email_balance)
          EXECUTE FUNCTION fixture_record_effect()''')

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname='worker_contracts',
                                     application_name='fixture-' + name)
        self.connections.append(connection)
        return connection

    def sql(self, statement, parameters=()):
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters or None)
            return cursor.fetchall() if cursor.description else None

    def case(self, name):
        self.completed.append(name)
        print('PASS: ' + name, flush=True)

    def command(self, binary, *arguments, success=True, env_file=None, timeout=35):
        completed = subprocess.run([str(self.work / binary), '--env-file', str(env_file or self.env_file), *arguments],
                                   env=self.env, capture_output=True, text=True, timeout=timeout)
        self.safe_output(completed.stdout + completed.stderr)
        assert (completed.returncode == 0) == success, f'{binary} unexpected exit status {completed.returncode}'
        return completed

    @staticmethod
    def safe_output(text):
        assert 'WARNING: DATA RACE' not in text, 'Native race detector reported a data race'
        for private in ('private-worker-password-canary', 'private-worker-secret-canary', 'private_missing_database_canary'):
            assert private not in text, 'Native command leaked private configuration'
        assert 'postgres://' not in text and 'postgresql://' not in text, 'Native command leaked a database URL'

    def worker(self, *arguments):
        log = self.output / f'worker-{len(self.children) + 1}.log'
        stream = log.open('w')
        command = [str(self.work / 'worker'), '--env-file', str(self.env_file),
                   '--concurrency', '1', '--poll-interval', '50ms', '--maintenance-interval', '50ms',
                   '--lease', '2s', '--renew-interval', '200ms', '--store-timeout', '500ms',
                   '--task-timeout', '10s', '--drain-timeout', '1s', *arguments]
        process = subprocess.Popen(command, env=self.env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        await_condition(lambda: 'worker ready' in log.read_text() or process.poll() is not None, 'worker startup')
        assert process.poll() is None, 'Worker exited during startup'
        return process

    def stop(self, process, expected=0):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=8) == expected, 'Unexpected worker shutdown status'

    def observed_claim(self, count=1):
        return self.sql("""SELECT count(*) FROM pg_stat_activity WHERE application_name='assozeta-go-worker'
          AND query LIKE '%%candidates%%' AND query LIKE '%%assozeta_go_jobs%%'""")[0][0] >= count

    def job(self, identifier):
        return self.sql('SELECT state,attempts,lease_owner,ownership_generation FROM assozeta_go_jobs WHERE id=%s', (identifier,))[0]

    def effects(self):
        return self.sql('SELECT * FROM fixture_effects ORDER BY old_balance,new_balance')

    def seed(self, kind=KIND):
        self.sql('DELETE FROM assozeta_go_jobs')
        self.sql('TRUNCATE communications_communicationconfiguration,fixture_effects')
        self.sql('INSERT INTO communications_communicationconfiguration VALUES (1,9),(2,0),(3,-1)')
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,1,%s,'{}',4,clock_timestamp())''', (identifier, NIL, kind, identifier))
        return identifier

    def handoff(self, runtime):
        # Test fixture only: production handoff must coordinate Celery producers,
        # queued tasks and scheduler ownership before changing this database fence.
        self.sql('UPDATE assozeta_go_job_ownership SET runtime=%s,generation=generation+1 WHERE singleton=1', (runtime,))

    def block_effect(self):
        connection = self.connect('block-business-row')
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM communications_communicationconfiguration WHERE id=1 FOR UPDATE')
        return connection

    def effect_is_blocked(self):
        return bool(self.sql("""SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-worker'
          AND query LIKE 'UPDATE communications_communicationconfiguration%%' AND wait_event_type='Lock'"""))

    def assert_file_lease(self, held):
        with (self.work / 'operations.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                assert held, 'Exited worker retained the operations file lease'
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
                assert not held, 'Admitted worker released the operations lease before its effect ended'

    def complete(self, identifier, attempts=1):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'durable completion')
        state = self.job(identifier)
        assert state[:3] == ('completed', attempts, ''), 'Unexpected attempt count or retained lease'
        assert state[3] == self.sql('SELECT generation FROM assozeta_go_job_ownership')[0][0]
        assert self.sql('SELECT daily_email_balance FROM communications_communicationconfiguration ORDER BY id') == [(0,), (0,), (-1,)]
        assert self.effects() == [(9, 0)], 'Effect duplicated or lost relative to completion'

    def run(self):
        failed = self.command('worker', success=False)
        assert 'worker' in failed.stderr.lower()
        assert self.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'assozeta_go_%'") == [(0,)]
        with (self.work / 'operations.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            failed = self.command('worker', success=False, timeout=3)
            assert 'worker ready' not in failed.stderr
        self.case('missing migration refuses execution without creating operational tables')
        self.command('migrate')
        assert self.sql('SELECT singleton,runtime,generation FROM assozeta_go_job_ownership') == [(1, 'django', 1)]
        self.command('migrate')
        assert self.sql('SELECT * FROM django_migrations') == [(7, 'fixture', 'unchanged')]
        assert self.sql('SELECT version,dirty FROM assozeta_go_schema_migrations') == [(3, False)]
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        self.command('migrate', '--down', success=False)
        self.case('explicit forward migration is idempotent and preserves Django history and default ownership')

        # Existing selfhost .env files receive this path through Compose, not
        # necessarily in the file itself. Native invocation must not silently
        # disable both the OS lease and the active-restore database barrier.
        self.handoff('go')
        self.sql("INSERT INTO instance_datarestore VALUES ('running',NULL)")
        for label, value in (('missing', None), ('empty', ''), ('blank', '   ')):
            identifier = self.seed()
            invalid = self.work / f'{label}-lock.env'
            lines = [line for line in self.env_file.read_text().splitlines()
                     if not line.startswith('DATA_RESTORE_LOCK_PATH=')]
            if value is not None:
                lines.append('DATA_RESTORE_LOCK_PATH=' + value)
            invalid.write_text('\n'.join(lines) + '\n')
            invalid.chmod(0o600)
            log = self.output / f'{label}-lock.log'
            stream = log.open('w')
            process = subprocess.Popen([str(self.work / 'worker'), '--env-file', str(invalid)],
                                       env=self.env, stdout=stream, stderr=subprocess.STDOUT)
            self.children.append((process, stream, log))
            await_condition(lambda: process.poll() is not None or 'worker ready' in log.read_text(),
                            'rejection of ' + label + ' coordination path')
            assert process.poll() is not None, 'Worker admitted without its existing coordination path: ' + label
            assert process.wait(timeout=3) == 1, 'Missing coordination must fail closed'
            output = log.read_text()
            self.safe_output(output)
            assert 'worker ready' not in output
            assert self.job(identifier)[:2] == ('pending', 0) and not self.effects()
            assert self.sql('SELECT daily_email_balance FROM communications_communicationconfiguration ORDER BY id') == [(9,), (0,), (-1,)]
            assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('go', 2)]
            assert self.sql('SELECT version,dirty FROM assozeta_go_schema_migrations') == [(3, False)]
        self.sql('DELETE FROM instance_datarestore')
        self.sql('DELETE FROM assozeta_go_jobs')
        self.sql("UPDATE assozeta_go_job_ownership SET runtime='django',generation=1")
        self.case('missing empty or blank coordination path refuses readiness without changing jobs effects or ownership')

        pooled = self.work / 'pooled.env'
        pooled.write_text(self.env_file.read_text() + f'USE_PGBOUNCER=True\nPGBOUNCER_HOST={self.work / "missing-pool"}\nPGBOUNCER_PORT=6432\n')
        self.command('migrate', env_file=pooled)
        self.command('worker', env_file=pooled, success=False)
        self.case('migration uses direct PostgreSQL while worker retains configured pooler routing')

        with (self.work / 'operations.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for version, dirty in [(3, True), (4, False)]:
                self.sql('UPDATE assozeta_go_schema_migrations SET version=%s,dirty=%s', (version, dirty))
                failed = self.command('worker', success=False, timeout=3)
                assert 'worker ready' not in failed.stderr
            self.sql('UPDATE assozeta_go_schema_migrations SET version=3,dirty=false')
        self.case('dirty or unknown migration history refuses readiness even during maintenance')
        self.sql('ALTER TABLE assozeta_go_periodic_occurrences RENAME TO fixture_hidden_occurrences')
        try:
            failed = self.command('worker', success=False, timeout=3)
            assert 'worker ready' not in failed.stderr
        finally:
            self.sql('ALTER TABLE fixture_hidden_occurrences RENAME TO assozeta_go_periodic_occurrences')
        self.case('missing occurrence receipts refuse readiness despite a clean current migration version')


        for arguments in [('--concurrency', '0'), ('--concurrency', '33'),
                          ('--lease', '1s', '--renew-interval', '800ms', '--store-timeout', '500ms'),
                          ('--poll-interval', '0s'), ('--task-timeout', '0s')]:
            self.command('worker', *arguments, success=False)
        assert self.sql('SELECT count(*) FROM assozeta_go_jobs') == [(0,)]
        bad = self.work / 'bad.env'
        bad.write_text(self.env_file.read_text().replace('DBNAME=worker_contracts', 'DBNAME=private_missing_database_canary'))
        self.command('migrate', env_file=bad, success=False)
        self.command('worker', env_file=bad, success=False)
        self.case('invalid worker bounds and database failures fail with redacted diagnostics')

        identifier = self.seed()
        for absent in (False, True):
            if absent:
                self.sql('DELETE FROM assozeta_go_job_ownership')
            process = self.worker()
            await_condition(self.observed_claim, 'an actual claim SQL observation')
            assert self.job(identifier)[:2] == ('pending', 0) and self.effects() == []
            assert self.sql('SELECT daily_email_balance FROM communications_communicationconfiguration WHERE id=1') == [(9,)]
            self.stop(process)
        self.sql("INSERT INTO assozeta_go_job_ownership VALUES (1,'django',1)")
        self.case('default Django and missing owner remain inert after observed claim queries')

        process = self.worker()
        await_condition(self.observed_claim, 'claim before maintenance')
        with (self.work / 'operations.lock').open('a') as lock:
            def acquire():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except BlockingIOError:
                    return False
            await_condition(acquire, 'maintenance exclusive file lease')
            self.handoff('go')
            time.sleep(0.3)  # Six configured polling intervals under the held real file lease.
            assert process.poll() is None and self.job(identifier)[:2] == ('pending', 0) and not self.effects()
            fcntl.flock(lock, fcntl.LOCK_UN)
        self.complete(identifier)
        self.stop(process)
        self.case('maintenance file lease defers claims without consuming attempts')

        identifier = self.seed()
        blocker = self.block_effect()
        first, second = self.worker(), self.worker()
        await_condition(self.effect_is_blocked, 'one admitted effect waiting for the fixture row')
        assert self.job(identifier)[:2] == ('running', 1)
        assert not self.effects()
        blocker.rollback()
        self.complete(identifier)
        self.stop(first)
        self.stop(second)
        unknown = self.seed('future_unregistered_task')
        process = self.worker()
        await_condition(self.observed_claim, 'known-kind claim query')
        assert self.job(unknown)[:2] == ('pending', 0) and not self.effects()
        self.stop(process)
        self.case('two native workers commit one effect and completion and never claim unregistered kinds')

        identifier = self.seed()
        blocker = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'admitted effect before graceful stop')
        self.assert_file_lease(held=True)
        process.send_signal(signal.SIGTERM)
        time.sleep(0.1)
        assert process.poll() is None and self.job(identifier)[0] == 'running'
        self.assert_file_lease(held=True)
        blocker.rollback()
        assert process.wait(timeout=5) == 0
        self.assert_file_lease(held=False)
        self.complete(identifier)
        self.case('SIGTERM drains the admitted transaction before process exit')

        identifier = self.seed()
        blocker = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'admitted effect before drain expiry')
        self.assert_file_lease(held=True)
        self.stop(process, expected=1)
        self.assert_file_lease(held=False)
        assert self.job(identifier)[:2] == ('pending', 1) and not self.effects()
        blocker.rollback()
        recovery = self.worker()
        self.complete(identifier, attempts=2)
        self.stop(recovery)
        self.case('expired SIGTERM drain rolls back effects and leaves recoverable intent')

        identifier = self.seed()
        blocker = self.block_effect()
        process = self.worker()
        await_condition(self.effect_is_blocked, 'admitted effect before SIGKILL')
        self.assert_file_lease(held=True)
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        self.assert_file_lease(held=False)
        assert not self.effects() and self.job(identifier)[:2] == ('running', 1)
        blocker.rollback()
        recovery = self.worker()
        self.complete(identifier, attempts=2)
        self.stop(recovery)
        self.case('SIGKILL rolls back uncommitted effects and lease expiry recovers exactly one completion')

        self.ownership_loss()
        assert self.sql('SELECT * FROM django_migrations') == [(7, 'fixture', 'unchanged')]

    def ownership_loss(self):
        identifier = self.seed()
        self.sql('''CREATE FUNCTION fixture_pause_claim() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.state='running' AND OLD.state='pending' THEN PERFORM pg_advisory_xact_lock(893712); END IF;
          RETURN NEW; END $$''')
        self.sql('CREATE TRIGGER fixture_pause_claim BEFORE UPDATE ON assozeta_go_jobs FOR EACH ROW EXECUTE FUNCTION fixture_pause_claim()')
        barrier = self.connect('claim-barrier')
        with barrier.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(893712)')
        process = self.worker('--store-timeout', '1s', '--lease', '3s')
        await_condition(lambda: bool(self.sql("SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-worker' AND wait_event='advisory'")), 'claim holding ownership before commit')
        transition = self.connect('ownership-transition')
        errors = []
        def change_owner():
            try:
                with transition.cursor() as cursor:
                    cursor.execute("UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1")
                transition.commit()
            except Exception as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=change_owner, daemon=True)
        thread.start()
        await_condition(lambda: bool(self.sql("SELECT 1 FROM pg_stat_activity WHERE application_name='fixture-ownership-transition' AND wait_event_type='Lock'")), 'queued ownership transition')
        with barrier.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(893712)')
        thread.join(timeout=5)
        assert not thread.is_alive() and not errors
        assert process.wait(timeout=5) == 1
        assert self.job(identifier)[:2] == ('running', 1) and not self.effects()
        self.sql('DROP TRIGGER fixture_pause_claim ON assozeta_go_jobs')
        self.handoff('go')
        recovery = self.worker()
        self.complete(identifier, attempts=2)
        self.stop(recovery)
        self.case('ownership loss between claim and business transaction fences effects until a new generation recovers')

    def close(self):
        logs, failures = [], []
        for process, stream, log in self.children:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=8)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(type(exc).__name__)
            finally:
                stream.close()
            logs.append(log)
        for connection in self.connections:
            try:
                connection.close()
            except psycopg2.Error as exc:
                failures.append(type(exc).__name__)
        for log in logs:
            self.safe_output(log.read_text())
        assert not failures, 'Private fixture cleanup failed: ' + ', '.join(failures)


def main():
    output = ROOT / 'quality-reports/go-migration/worker-process'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'native binary and synthetic transaction boundary; not installed activation or historical task parity'}
    result['source_hashes'] = process_source_hashes()
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('go'):
        raise RuntimeError('PostgreSQL and Go required; process rehearsal cannot be skipped')
    binaries = Path(postgres).parent
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-worker-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        started, rehearsal = False, None
        def run(arguments):
            return subprocess.run([str(arg) for arg in arguments], env=environment, check=True,
                                  stdout=log, stderr=subprocess.STDOUT, timeout=90)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work / name), './cmd/' + name],
                               cwd=ROOT / 'BE_GO', env=environment, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=120)
            result['binary_sha256'] = {name: hashlib.sha256((work / name).read_bytes()).hexdigest()
                                       for name in ('worker', 'migrate')}
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                 '-o', f"-k {work} -h '' -F", '-w', 'start'])
            started = True
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'worker_contracts'])
            rehearsal = Rehearsal(work, output, environment, binaries)
            rehearsal.run()
            result.update(private_postgres=True, race_instrumented_native_binaries=True,
                          scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
        finally:
            try:
                if rehearsal:
                    rehearsal.close()
            finally:
                try:
                    if started:
                        run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                finally:
                    if (work / 'postgres.log').exists():
                        shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    result['source_stable'] = process_source_hashes() == result['source_hashes']
    assert result['source_stable'], 'Process acceptance source changed during execution'
    result['success'] = True
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
