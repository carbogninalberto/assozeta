"""Packaged age worker on actual Django current-model tables in private Docker PG.

Consumes an existing immutable image. Fixture-only queue intent and ownership
changes do not establish Celery handoff, scheduler publication or activation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_image import Proof, LABEL, wait

DATABASE = 'assozeta_contract_age_worker_image'
KIND = 'update_age_of_associates'
EXPECTED = {
    'packaged worker retains nonroot read-only configuration and default server entrypoint',
    'default Django ownership leaves age job and actual Django rows unchanged',
    'Linux exclusive lifecycle lease defers age claims without spending attempts',
    'two packaged workers complete one age job with exact promotion audit and indexes',
    'handled age SQL failure rolls back effects and completes once without automatic retry',
}


class AgeImage(Proof):
    def sql(self, statement):
        return self.docker('exec', '-i', self.database, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                           '-U', 'contracts', '-d', DATABASE, input=statement).stdout.strip()

    def enqueue(self):
        identifier = str(uuid4())
        self.sql("INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at) "
                 f"VALUES('{identifier}','00000000-0000-0000-0000-000000000000','{KIND}',1,'{identifier}','{{}}',3,clock_timestamp());")
        return identifier

    def run(self):
        self.image = self.docker('image', 'inspect', '--format', '{{.Id}}', self.image).stdout.strip()
        config = json.loads(self.docker('image', 'inspect', '--format', '{{json .Config}}', self.image).stdout)
        assert config['User'] == '10001:10001' and config['Entrypoint'] == ['/app/server']
        assert 'readyz' in ' '.join(config['Healthcheck']['Test'])
        self.go('presence', '/app/worker', ['--help'], environment=False)
        architecture = self.docker('image', 'inspect', '--format', '{{.Architecture}}', self.image).stdout.strip()
        assert architecture in ('arm64', 'amd64')
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        subprocess.run(['go', 'build', '-o', str(self.probe), str(ROOT / 'TESTS/adapters/go/testdata/worker_image_probe.go')],
                       cwd=ROOT / 'BE_GO', env={**env, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': architecture},
                       check=True, capture_output=True, timeout=120)
        self.probe.chmod(0o755)
        values = dict(DBTYPE='POSTGRES', DBHOST='postgres', DBPORT='5432', DBNAME=DATABASE, DBUSER='contracts',
                      DBPASSWORD='private-image-password-canary', DEBUG='False', SECRET_KEY='private-image-session-canary',
                      JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
                      SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
                      AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
                      AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
                      DATA_RESTORE_LOCK_PATH='/operations/lifecycle.lock')
        self.environment.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
        self.environment.chmod(0o600)
        self.docker('image', 'inspect', self.postgres)
        self.docker('network', 'create', '--label', LABEL + '=' + self.identifier, self.network)
        self.network_created = True
        self.database = self.identifier + '-postgres'
        self.containers.append(self.database)
        self.docker('run', '--detach', '--name', self.database, '--label', LABEL + '=' + self.identifier,
                    '--network', self.network, '--network-alias', 'postgres', '--publish', '127.0.0.1::5432',
                    '--tmpfs', '/var/lib/postgresql/data', '--env', 'POSTGRES_USER=contracts',
                    '--env', 'POSTGRES_DB=' + DATABASE, '--env', 'POSTGRES_HOST_AUTH_METHOD=trust', self.postgres,
                    '-c', 'log_statement=all', '-c', 'log_line_prefix=%m [%p] app=%a ')
        wait(lambda: self.docker('exec', self.database, 'pg_isready', '-U', 'contracts', '-d', DATABASE,
                                success=False).returncode == 0, 'private PostgreSQL')
        binding = json.loads(self.inspect(self.database, '{{json .NetworkSettings.Ports}}'))['5432/tcp'][0]
        assert binding['HostIp'] == '127.0.0.1'
        from TESTS.adapters.go.age_worker_fixture import initialize_current_schema, AgeFixture
        with (self.output / 'schema.log').open('w') as log:
            initialize_current_schema(self.work, '127.0.0.1', binding['HostPort'], DATABASE, log)
        fixture = AgeFixture()
        before = fixture.seed()
        gate = self.barrier('migration-lock', 'hold-exclusive')
        self.go('migrate', '/app/migrate', network=self.network)
        self.stop(gate)
        assert self.sql('SELECT runtime FROM assozeta_go_job_ownership;') == 'django'
        identifier = self.enqueue()
        worker = self.worker('django-owner')
        assert self.docker('exec', worker, 'id', '-u').stdout.strip() == '10001'
        self.docker('exec', worker, 'sh', '-c', 'test -w /operations/lifecycle.lock')
        self.case('packaged worker retains nonroot read-only configuration and default server entrypoint')
        wait(self.observed_claim, 'default owner claim observation')
        assert self.job(identifier)[:2] == ['pending', 0]
        assert fixture.snapshot() == before
        self.stop(worker)
        self.case('default Django ownership leaves age job and actual Django rows unchanged')
        gate = self.barrier('maintenance-lock', 'hold-exclusive')
        self.sql("UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1;")
        workers = [self.worker('age-first'), self.worker('age-second')]
        time.sleep(.4)
        assert self.job(identifier)[:2] == ['pending', 0] and fixture.snapshot() == before
        self.stop(gate)
        self.case('Linux exclusive lifecycle lease defers age claims without spending attempts')
        try:
            wait(lambda: self.job(identifier)[0] == 'completed', 'registered age task completion')
        except AssertionError:
            (self.output / 'pending-observation.json').write_text(json.dumps({
                'job_state': self.job(identifier)[:2], 'business_unchanged': fixture.snapshot() == before,
                'runtime': self.sql('SELECT runtime FROM assozeta_go_job_ownership;'),
                'image_id': self.image}) + '\n')
            raise
        for worker in workers:
            self.stop(worker)
        assert self.job(identifier)[:3] == ['completed', 1, '']
        fixture.assert_effect(before)
        assert self.sql('SELECT result IS NULL FROM assozeta_go_jobs WHERE id=\'' + identifier + '\';') == 't'
        assert self.exclusive()
        self.case('two packaged workers complete one age job with exact promotion audit and indexes')
        before = fixture.seed()
        identifier = self.enqueue()
        self.sql("CREATE FUNCTION fixture_age_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private-age-sql-canary'; END $$; "
                 "CREATE TRIGGER fixture_age_failure BEFORE UPDATE ON application_associate FOR EACH STATEMENT EXECUTE FUNCTION fixture_age_failure();")
        worker = self.worker('handled-error')
        wait(lambda: self.job(identifier)[0] == 'completed', 'handled failure completion')
        time.sleep(.4)
        self.stop(worker)
        assert self.job(identifier)[:3] == ['completed', 1, '']
        assert self.sql('SELECT result IS NULL FROM assozeta_go_jobs WHERE id=\'' + identifier + '\';') == 't'
        assert fixture.snapshot() == before
        logs = self.logs(worker)
        assert 'associate_age_update_failed' in logs and 'private-age-sql-canary' not in logs
        self.sql('DROP TRIGGER fixture_age_failure ON application_associate; DROP FUNCTION fixture_age_failure();')
        self.case('handled age SQL failure rolls back effects and completes once without automatic retry')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--postgres-image', default='postgres:16-alpine')
    parser.add_argument('--output', default=str(ROOT / 'quality-reports/go-migration/age-worker-image'))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'packaged age task on current Django models; no historical migrations or Celery handoff'}
    report = output / 'results.json'
    report.write_text(json.dumps(result) + '\n')
    with tempfile.TemporaryDirectory(prefix='az-age-image-', dir='/tmp') as directory:
        work = Path(directory)
        work.chmod(0o755)
        proof = AgeImage(args.image, 'unused', args.postgres_image, work, output)
        try:
            proof.run()
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    proof.cleanup()
                    for command in (['ps', '-aq'], ['network', 'ls', '-q']):
                        assert proof.docker(*command, '--filter', 'label=' + LABEL + '=' + proof.identifier).stdout.strip() == ''
                    result['private_resources_removed'] = True
                finally:
                    result.update(scenarios=proof.completed)
                    report.write_text(json.dumps(result, indent=2) + '\n')
        paths = ['TESTS/adapters/go/age_worker_image.py', 'TESTS/adapters/go/age_worker_fixture.py',
                 'TESTS/adapters/go/worker_image.py',
                 'BE_GO/cmd/worker/main.go', 'BE_GO/internal/repository/associate_age_update.go',
                 'BE_GO/internal/service/associate_age_update.go']
        result.update(success=True, image_id=proof.image, scenario_count=len(proof.completed), uid=10001,
                      read_only=True, worker_http_health_disabled=True, private_resources_removed=True,
                      source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
        report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
