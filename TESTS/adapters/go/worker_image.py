"""Prove packaged worker/migrate effects in disposable, isolated Docker containers.

The only mounted executable is a test barrier/key helper. Worker, migration and
server binaries are taken from the supplied image. No image is built or retagged,
no ports are published, and no installed service or customer database is touched.
Requires Docker, Go and Python standard library; missing dependencies fail.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
KIND = 'reset_communication_daily_email_balance'
NIL = '00000000-0000-0000-0000-000000000000'
LABEL = 'org.assozeta.worker-image-proof'
REQUIRED = {
    'packaged commands preserve nonroot image server and HTTP health contract',
    'forward migration preserves Django history and starts with Django ownership',
    'packaged worker observes default Django without claims or effects',
    'container lifecycle lock defers Go claims without spending attempts',
    'two packaged workers commit exactly one effect and completion',
    'SIGTERM retains the lifecycle lease until the admitted transaction commits',
    'SIGKILL releases the lifecycle lease and recovers one durable completion',
}


def wait(predicate, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Timed out: ' + description)


class Proof:
    def __init__(self, image, version, postgres, work, output):
        self.image, self.version, self.postgres = image, version, postgres
        self.work, self.output = work, output
        self.identifier = 'az-worker-image-' + uuid4().hex
        self.network = self.identifier + '-net'
        self.network_created = False
        self.containers, self.completed = [], []
        self.environment = work / 'fixture.env'
        self.operations = work / 'operations'
        self.operations.mkdir(mode=0o777)
        self.operations.chmod(0o777)
        self.probe = work / 'probe'
        self.database = None

    @staticmethod
    def docker(*arguments, input=None, timeout=30, success=True):
        result = subprocess.run(['docker', *map(str, arguments)], input=input,
                                capture_output=True, text=True, timeout=timeout)
        if success and result.returncode:
            # Docker/SQL errors may embed configuration; never dump raw stderr.
            raise AssertionError('Docker fixture operation failed: ' + str(arguments[0]))
        return result

    def inspect(self, name, template):
        return self.docker('inspect', '--format', template, name).stdout.strip()

    def go(self, suffix, entrypoint=None, arguments=(), *, detached=False, network=None, environment=True):
        name = self.identifier + '-' + suffix
        self.containers.append(name)
        command = ['run', '--name', name, '--label', LABEL + '=' + self.identifier,
                   '--network', network or 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m']
        if entrypoint is not None:
            # Image HTTP health is inappropriate for worker/migration/test helpers.
            command += ['--no-healthcheck', '--entrypoint', entrypoint]
        if environment:
            command += ['--env-file', self.environment,
                        '--mount', f'type=bind,source={self.operations},target=/operations']
        if self.probe.exists():
            command += ['--mount', f'type=bind,source={self.probe},target=/proof/probe,readonly']
        if detached:
            command += ['--detach', '--init']
        else:
            command += ['--rm']
        result = self.docker(*command, self.image, *arguments, timeout=40)
        return name if detached else result.stdout.strip()

    def case(self, name):
        assert name not in self.completed, 'Duplicate proof scenario'
        self.completed.append(name)
        print('PASS: ' + name, flush=True)

    def logs(self, name):
        result = self.docker('logs', name)
        return result.stdout + result.stderr

    def running(self, name):
        return self.inspect(name, '{{.State.Running}}') == 'true'

    def exited(self, name, expected=0):
        wait(lambda: not self.running(name), 'container exit')
        assert int(self.inspect(name, '{{.State.ExitCode}}')) == expected, 'Unexpected container exit status'

    def stop(self, name):
        if self.running(name):
            self.docker('kill', '--signal', 'TERM', name)
        self.exited(name)

    def sql(self, statement):
        return self.docker('exec', '-i', self.database, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                           '-U', 'contracts', '-d', 'worker_contracts', input=statement).stdout.strip()

    def observed_claim(self):
        # Claim now ends with COMMIT, so idle pg_stat_activity no longer retains
        # its candidates query. Observe the actual worker statement durably in
        # this private PostgreSQL fixture instead of sampling a brief execution.
        records = re.split(r'(?m)^(?=\d{4}-\d{2}-\d{2} .* app=)', self.logs(self.database))
        return any('app=assozeta-go-worker LOG:  execute ' in record.split('\n', 1)[0]
                   and '), candidates AS (' in record and 'UPDATE public.assozeta_go_jobs' in record
                   for record in records)

    def value(self, query):
        return json.loads(self.sql(query))

    def job(self, identifier):
        return self.value("SELECT json_build_array(state,attempts,lease_owner,ownership_generation) FROM assozeta_go_jobs WHERE id='" + identifier + "';")

    def effects(self):
        return self.value('SELECT COALESCE(json_agg(row_to_json(e)),\'[]\'::json) FROM (SELECT old_balance,new_balance FROM fixture_effects ORDER BY old_balance,new_balance) e;')

    def seed(self):
        identifier = str(uuid4())
        self.sql("DELETE FROM assozeta_go_jobs; TRUNCATE communications_communicationconfiguration,fixture_effects; "
                 "INSERT INTO communications_communicationconfiguration VALUES(1,9),(2,0),(3,-1); "
                 "INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at) "
                 f"VALUES('{identifier}','{NIL}','{KIND}',1,'{identifier}','{{}}',4,clock_timestamp());")
        return identifier

    def complete(self, identifier, attempts=1):
        wait(lambda: self.job(identifier)[0] == 'completed', 'atomic packaged completion')
        state = self.job(identifier)
        assert state[:3] == ['completed', attempts, ''], 'Unexpected attempt count or retained lease'
        generation = int(self.sql('SELECT generation FROM assozeta_go_job_ownership;'))
        assert state[3] == generation
        assert self.value('SELECT json_agg(daily_email_balance ORDER BY id) FROM communications_communicationconfiguration;') == [0, 0, -1]
        assert self.effects() == [{'old_balance': 9, 'new_balance': 0}], 'Business effect duplicated or missing'

    def barrier(self, suffix, mode):
        name = self.go(suffix, '/proof/probe', [mode], detached=True, network=self.network)
        marker = '"business_locked":true' if mode == 'hold-business' else '"exclusive":true'
        wait(lambda: marker in self.logs(name) or not self.running(name), 'fixture barrier admission')
        assert self.running(name), 'Fixture barrier exited before admission'
        return name

    def exclusive(self):
        raw = self.go('lock-check-' + uuid4().hex[:8], '/proof/probe', ['try-exclusive'])
        return json.loads(raw)['exclusive']

    def worker(self, suffix):
        arguments = ['--concurrency', '1', '--poll-interval', '100ms', '--maintenance-interval', '100ms',
                     '--lease', '3s', '--renew-interval', '300ms', '--store-timeout', '1s',
                     '--task-timeout', '20s', '--drain-timeout', '8s']
        name = self.go(suffix, '/app/worker', arguments, detached=True, network=self.network)
        wait(lambda: 'worker ready' in self.logs(name) or not self.running(name), 'packaged worker readiness')
        assert self.running(name), 'Packaged worker exited during startup'
        assert self.inspect(name, '{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .Config.Healthcheck.Test}}') == '10001:10001|true|["NONE"]'
        return name

    def effect_blocked(self):
        return self.sql("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='assozeta-go-worker' AND query LIKE 'UPDATE communications_communicationconfiguration%' AND wait_event_type='Lock');") == 't'

    def run(self):
        # Resolve once so a concurrent tag update cannot mix different binaries
        # across this proof. The final artifact records the inspected image ID.
        self.image = self.docker('image', 'inspect', '--format', '{{.Id}}', self.image).stdout.strip()
        image_config = json.loads(self.docker('image', 'inspect', '--format', '{{json .Config}}', self.image).stdout)
        assert image_config['User'] == '10001:10001' and image_config['Entrypoint'] == ['/app/server']
        assert 'readyz' in ' '.join(image_config.get('Healthcheck', {}).get('Test', [])), 'Default HTTP healthcheck was removed'
        # First red on the old image is the missing packaged executable, before PG creation.
        self.go('worker-presence', '/app/worker', ['--help'], environment=False)
        self.go('migration-presence', '/app/migrate', ['--help'], environment=False)
        architecture = self.docker('image', 'inspect', '--format', '{{.Architecture}}', self.image).stdout.strip()
        assert architecture in ('amd64', 'arm64'), 'Unsupported image architecture'
        tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        subprocess.run(['go', 'build', '-o', str(self.probe), str(ROOT / 'TESTS/adapters/go/testdata/worker_image_probe.go')],
                       cwd=ROOT / 'BE_GO', env={**tool_env, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': architecture},
                       capture_output=True, check=True, timeout=120)
        self.probe.chmod(0o755)
        keys = json.loads(self.go('keys', '/proof/probe', ['keys'], environment=False))
        values = {'DBTYPE': 'POSTGRES', 'DBHOST': 'postgres', 'DBPORT': '5432', 'DBNAME': 'worker_contracts',
                  'DBUSER': 'contracts', 'DBPASSWORD': 'private-image-password-canary', 'DEBUG': 'False',
                  'SECRET_KEY': 'private-image-session-canary', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': '6379',
                  'REDIS_PASSWORD': 'synthetic-only', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '',
                  'AWS_ACCESS_KEY_ID': 'fixture', 'AWS_SECRET_ACCESS_KEY': 'fixture', 'AWS_STORAGE_BUCKET_NAME': 'fixture',
                  'AWS_S3_REGION_NAME': 'us-east-1', 'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9000', 'AWS_LOCATION': 'private',
                  'DATA_RESTORE_LOCK_PATH': '/operations/lifecycle.lock', **keys}
        self.environment.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.environment.chmod(0o600)
        server = self.go('server-default', detached=True)
        def health():
            result = self.docker('exec', server, 'wget', '-qO-', 'http://127.0.0.1:8000/healthz', success=False)
            return result.returncode == 0 and json.loads(result.stdout) == {'status': 'ok', 'version': self.version}
        wait(health, 'unchanged default server HTTP health')
        self.stop(server)
        self.case('packaged commands preserve nonroot image server and HTTP health contract')

        self.docker('image', 'inspect', self.postgres)
        self.docker('network', 'create', '--internal', '--label', LABEL + '=' + self.identifier, self.network)
        self.network_created = True
        self.database = self.identifier + '-postgres'
        self.containers.append(self.database)
        self.docker('run', '--detach', '--name', self.database, '--label', LABEL + '=' + self.identifier,
                    '--network', self.network, '--network-alias', 'postgres', '--tmpfs', '/var/lib/postgresql/data',
                    '--env', 'POSTGRES_USER=contracts', '--env', 'POSTGRES_DB=worker_contracts',
                    '--env', 'POSTGRES_HOST_AUTH_METHOD=trust', self.postgres,
                    '-c', 'log_statement=all', '-c', 'log_line_prefix=%m [%p] app=%a ')
        wait(lambda: self.docker('exec', self.database, 'pg_isready', '-h', '127.0.0.1', '-U', 'contracts', '-d', 'worker_contracts', success=False).returncode == 0, 'private PostgreSQL startup')
        self.sql("""CREATE TABLE django_migrations(id integer PRIMARY KEY,app text,name text); INSERT INTO django_migrations VALUES(7,'fixture','unchanged');
 CREATE TABLE communications_communicationconfiguration(id integer PRIMARY KEY,daily_email_balance integer);
 CREATE TABLE instance_datarestore(state text,completed_at timestamptz);
 CREATE TABLE fixture_effects(old_balance integer,new_balance integer);
 CREATE FUNCTION fixture_effect() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO fixture_effects VALUES(OLD.daily_email_balance,NEW.daily_email_balance); RETURN NEW; END $$;
 CREATE TRIGGER fixture_effect AFTER UPDATE ON communications_communicationconfiguration FOR EACH ROW WHEN(OLD.daily_email_balance IS DISTINCT FROM NEW.daily_email_balance) EXECUTE FUNCTION fixture_effect();""")
        migration_lock = self.barrier('migration-lock', 'hold-exclusive')
        self.go('migrate', '/app/migrate', network=self.network)
        self.go('migrate-repeat', '/app/migrate', network=self.network)
        self.stop(migration_lock)
        assert self.value('SELECT json_build_array(runtime,generation) FROM assozeta_go_job_ownership;') == ['django', 1]
        assert self.value('SELECT json_build_array(version,dirty) FROM assozeta_go_schema_migrations;') == [3, False]
        assert self.value('SELECT json_build_array(id,app,name) FROM django_migrations;') == [7, 'fixture', 'unchanged']
        self.case('forward migration preserves Django history and starts with Django ownership')

        identifier = self.seed()
        worker = self.worker('django-owner')
        wait(self.observed_claim, 'actual inactive claim SQL')
        assert self.job(identifier)[:2] == ['pending', 0] and not self.effects()
        self.stop(worker)
        self.case('packaged worker observes default Django without claims or effects')

        held = self.barrier('maintenance', 'hold-exclusive')
        # Only fixture processes exist; there are no Django consumers or Beat here.
        self.sql("UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1 AND runtime='django';")
        worker = self.worker('maintenance-worker')
        time.sleep(0.4)
        assert self.running(worker) and self.job(identifier)[:2] == ['pending', 0] and not self.effects()
        self.stop(held); self.complete(identifier); self.stop(worker)
        self.case('container lifecycle lock defers Go claims without spending attempts')

        identifier = self.seed(); held = self.barrier('two-worker-barrier', 'hold-business')
        first, second = self.worker('first-worker'), self.worker('second-worker')
        wait(self.effect_blocked, 'packaged worker effect barrier')
        assert self.job(identifier)[:2] == ['running', 1] and not self.effects() and not self.exclusive()
        self.stop(held); self.complete(identifier); self.stop(first); self.stop(second)
        self.case('two packaged workers commit exactly one effect and completion')

        identifier = self.seed(); held = self.barrier('drain-barrier', 'hold-business'); worker = self.worker('drain-worker')
        wait(self.effect_blocked, 'effect before SIGTERM')
        assert not self.exclusive()
        self.docker('kill', '--signal', 'TERM', worker)
        assert self.running(worker) and not self.exclusive()
        self.stop(held); self.exited(worker); self.complete(identifier)
        assert self.exclusive()
        self.case('SIGTERM retains the lifecycle lease until the admitted transaction commits')

        identifier = self.seed(); held = self.barrier('crash-barrier', 'hold-business'); worker = self.worker('crash-worker')
        wait(self.effect_blocked, 'effect before SIGKILL')
        assert not self.exclusive()
        self.docker('kill', '--signal', 'KILL', worker); self.exited(worker, expected=137)
        assert self.exclusive() and self.job(identifier)[:2] == ['running', 1] and not self.effects()
        self.stop(held)
        replacement = self.worker('replacement-worker'); self.complete(identifier, attempts=2); self.stop(replacement)
        self.case('SIGKILL releases the lifecycle lease and recovers one durable completion')
        assert set(self.completed) == REQUIRED and len(self.completed) == len(REQUIRED), 'Missing packaged proof scenario'

    def cleanup(self):
        failures, logs = [], []
        for name in reversed(self.containers):
            try:
                owner = self.docker('inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', name, success=False)
                if owner.returncode:
                    continue
                if owner.stdout.strip() != self.identifier:
                    failures.append('container ownership mismatch')
                    continue
                record = self.docker('logs', name, success=False)
                logs.append((name, record.stdout + record.stderr))
                removed = self.docker('rm', '--force', name, success=False)
                if removed.returncode:
                    failures.append('container cleanup failed')
            except subprocess.SubprocessError:
                failures.append('container cleanup timed out')
        if self.network_created:
            try:
                removed = self.docker('network', 'rm', self.network, success=False)
                if removed.returncode:
                    failures.append('private network cleanup failed')
            except subprocess.SubprocessError:
                failures.append('private network cleanup timed out')
        for name, content in logs:
            if any(private in content for private in ('private-image-password-canary', 'private-image-session-canary')):
                failures.append('private fixture settings leaked in container logs')
            else:
                (self.output / (name.removeprefix(self.identifier + '-') + '.log')).write_text(content)
        if failures:
            raise AssertionError('; '.join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-version', default='v0.0.0')
    parser.add_argument('--postgres-image', default='postgres:16-alpine')
    args = parser.parse_args()
    output = ROOT / 'quality-reports/go-migration/worker-image'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'packaged binaries and synthetic database effects; no installed ownership transition', 'image': args.image}
    (output / 'results.json').write_text(json.dumps(result) + '\n')
    with tempfile.TemporaryDirectory(prefix='az-worker-image-', dir='/tmp') as directory:
        work = Path(directory); work.chmod(0o755)
        proof = Proof(args.image, args.expected_version, args.postgres_image, work, output)
        try:
            proof.run()
        finally:
            try:
                proof.cleanup()
            finally:
                result['scenarios'] = proof.completed
                (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        result.update(success=True, image_id=proof.image, scenarios=proof.completed, scenario_count=len(proof.completed),
                      uid=10001, read_only=True, worker_http_health_disabled=True, lifecycle_flock_kernel='Docker Linux',
                      no_published_ports=True, no_django_consumers_or_scheduler=True)
        (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
