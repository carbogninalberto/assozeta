"""Category HTTP/durable contracts against an existing immutable Go image.

Owns disposable PostgreSQL, Redis and server containers with loopback fixture
ports. The unchanged oracle first verifies Django, then the packaged executable
and both Caddy routes against the same database. This is current-schema image
evidence, not installation, migration-history or activation/rollback acceptance.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.http_client import Client, poll

DATABASE = 'assozeta_contract_category_image'
LABEL = 'org.assozeta.category-image-proof'


class ImageFixture:
    def __init__(self, image, revision, work, output, log):
        self.requested_image, self.revision = image, revision
        self.work, self.output, self.log = work, output, log
        self.identifier = 'az-category-image-' + uuid4().hex
        self.network = self.identifier + '-net'
        self.network_created = False
        self.containers = []
        self.metadata = {}
        self.cleanup_verified = False
        self.private_values = set()

    @staticmethod
    def docker(*args, check=True, timeout=90):
        try:
            result = subprocess.run(['docker', *map(str, args)], capture_output=True,
                                    text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Docker arguments can contain private fixture credentials. Neither
            # the exception message nor its chained traceback may export them.
            raise RuntimeError('Private Docker fixture operation timed out') from None
        if check and result.returncode:
            # Never include environment files or private signing keys in errors.
            raise RuntimeError('Docker category fixture operation failed: ' + str(args[0]))
        return result

    def inspect(self, name):
        return json.loads(self.docker('inspect', name).stdout)[0]

    def image(self, requested):
        value = json.loads(self.docker('image', 'inspect', requested).stdout)[0]
        assert re.fullmatch(r'sha256:[0-9a-f]{64}', value['Id'])
        return value

    def run_container(self, suffix, image, *args):
        name = self.identifier + '-' + suffix
        self.containers.append(name)
        self.docker('run', '--detach', '--pull', 'never', '--name', name,
                    '--label', LABEL + '=' + self.identifier,
                    '--network', self.network, *args, image)
        return name

    def published_port(self, container, port):
        bindings = self.inspect(container)['NetworkSettings']['Ports'][str(port) + '/tcp']
        assert len(bindings) == 1 and bindings[0]['HostIp'] == '127.0.0.1'
        return int(bindings[0]['HostPort'])

    def start_services(self, postgres, redis):
        image = self.image(self.requested_image)
        assert image['Os'] == 'linux' and image['Architecture'] in ('amd64', 'arm64')
        config = image['Config']
        assert config['User'] == '10001:10001'
        assert config['Entrypoint'] == ['/app/server']
        assert not config.get('Volumes'), 'Unexpected implicit image volumes'
        assert '/readyz' in ' '.join(config['Healthcheck']['Test'])
        self.image_id = image['Id']
        self.metadata = {'immutable_id': self.image_id, 'requested_image': self.requested_image,
                         'platform': image['Os'] + '/' + image['Architecture'],
                         'user': config['User'], 'entrypoint': config['Entrypoint']}
        postgres_id, redis_id = self.image(postgres)['Id'], self.image(redis)['Id']
        self.docker('network', 'create', '--label', LABEL + '=' + self.identifier, self.network)
        self.network_created = True
        self.postgres = self.run_container('postgres', postgres_id,
            '--network-alias', 'postgres', '--publish', '127.0.0.1::5432',
            '--tmpfs', '/var/lib/postgresql/data', '--env', 'POSTGRES_USER=contracts',
            '--env', 'POSTGRES_DB=' + DATABASE, '--env', 'POSTGRES_HOST_AUTH_METHOD=trust')
        poll(lambda: self.docker('exec', self.postgres, 'pg_isready', '-U', 'contracts',
                                '-d', DATABASE, check=False).returncode == 0, bool, timeout=40)
        self.postgres_port = self.published_port(self.postgres, 5432)
        # Redis entrypoint accepts arguments after the image, so create it explicitly.
        self.redis = self.identifier + '-redis'
        self.containers.append(self.redis)
        self.docker('run', '--detach', '--pull', 'never', '--name', self.redis,
            '--label', LABEL + '=' + self.identifier, '--network', self.network,
            '--network-alias', 'redis', '--publish', '127.0.0.1::6379', '--tmpfs', '/data',
            redis_id, 'redis-server', '--save', '', '--appendonly', 'no',
            '--requirepass', 'synthetic-identity-only')
        poll(lambda: self.docker('exec', self.redis, 'redis-cli', '-a', 'synthetic-identity-only',
                                'ping', check=False).stdout.strip() == 'PONG', bool, timeout=30)
        self.redis_port = self.published_port(self.redis, 6379)
        self.metadata.update(postgres_image=postgres_id, redis_image=redis_id,
                             fixture_ports='ephemeral loopback only')

    @contextmanager
    def server(self, work, log, tool_env, address, *, versioned=False):
        from django.conf import settings

        assert address == f'127.0.0.1:{self.redis_port}'
        values = {
            'DBTYPE': 'POSTGRES', 'DBHOST': 'postgres', 'DBPORT': '5432',
            'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'synthetic-only',
            'REDIS_HOST': 'redis', 'REDIS_PORT': '6379', 'REDIS_USERNAME': 'default',
            'REDIS_PASSWORD': 'synthetic-identity-only', 'GO_LISTEN_ADDR': ':8000',
            'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
            'DATA_RESTORE_LOCK_PATH': '/operations/lifecycle.lock',
            **{name: getattr(settings, name) for name in (
                'SECRET_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL',
                'AWS_LOCATION', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},
            **{name + '_B64': base64.b64encode(getattr(settings, name).encode()).decode()
               for name in ('JWT_SECRET_KEY', 'JWT_PUBLIC_KEY')},
        }
        assert all('\n' not in str(value) and '\r' not in str(value) for value in values.values())
        self.private_values.update(str(value) for key, value in values.items()
            if any(marker in key for marker in ('SECRET', 'PASSWORD', 'PRIVATE')) and len(str(value)) >= 8)
        self.private_values.add(settings.JWT_SECRET_KEY)
        environment = work / 'image.env'
        environment.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        environment.chmod(0o600)
        operations = work / 'image-operations'
        operations.mkdir(mode=0o777)
        operations.chmod(0o777)
        container = self.run_container('server', self.image_id,
            '--network-alias', 'category-api', '--publish', '127.0.0.1::8000',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m', '--env-file', environment,
            '--mount', f'type=bind,source={operations},target=/operations')
        try:
            info = self.inspect(container)
            assert info['Image'] == self.image_id and info['HostConfig']['ReadonlyRootfs']
            assert self.docker('exec', container, 'id', '-u').stdout.strip() == '10001'
            revision = self.docker('exec', container, 'cat', '/app/CODE_REVISION').stdout.strip()
            assert revision == self.revision, 'Packaged CODE_REVISION differs from expected revision'
            digest = self.docker('exec', container, 'sha256sum', '/app/server').stdout.split()[0]
            assert re.fullmatch(r'[0-9a-f]{64}', digest)
            version = self.docker('exec', container, 'cat', '/app/VERSION').stdout.strip()
            assert version, 'Packaged version is missing'
            self.metadata.update(revision=revision, version=version, server_sha256=digest, readonly_root=True,
                                 uid=10001, mounted_executables=False)
            port = self.published_port(container, 8000)
            base = f'http://127.0.0.1:{port}'

            def ready():
                assert self.inspect(container)['State']['Running'], 'Packaged server exited'
                try:
                    return Client(base, timeout=2).request('GET', '/readyz')[0] == 200
                except OSError:
                    return False

            poll(ready, bool, timeout=30)
            status, health = Client(base).request('GET', '/healthz')
            assert status == 200 and health == {'status': 'ok', 'version': version}
            self.metadata['health_version_matches_package'] = True
            yield {'base_url': base, 'proxy_upstream': 'category-api:8000',
                   'network': self.network, 'metadata': dict(self.metadata)}
        finally:
            # Finish HTTP admission before fixture cleanup. Whole-fixture cleanup
            # below verifies removal even when a response or metadata check fails.
            self.docker('stop', '--time', '20', container, check=False, timeout=30)

    def safe_log(self, path, contents):
        found = False
        for value in self.private_values:
            if value and value in contents:
                contents = contents.replace(value, '<redacted fixture secret>')
                found = True
        path.write_text(contents)
        return found

    def cleanup(self):
        failures = []
        for name in reversed(self.containers):
            try:
                found = self.docker('inspect', name, check=False)
                if found.returncode:
                    continue
                info = json.loads(found.stdout)[0]
                if info['Config']['Labels'].get(LABEL) != self.identifier:
                    failures.append('resource ownership mismatch')
                    continue
                try:
                    logs = self.docker('logs', name, check=False)
                    if self.safe_log(self.output / (name.rsplit('-', 1)[-1] + '.log'), logs.stdout + logs.stderr):
                        failures.append('fixture secret detected and redacted in container log')
                except Exception:
                    failures.append('container log collection failed')
                if self.docker('rm', '--force', name, check=False).returncode:
                    failures.append('container cleanup failed')
            except Exception:
                failures.append('container inspection or removal failed')
        if self.network_created:
            try:
                network = json.loads(self.docker('network', 'inspect', self.network).stdout)[0]
                if network.get('Labels', {}).get(LABEL) != self.identifier:
                    failures.append('network ownership mismatch')
                elif self.docker('network', 'rm', self.network, check=False).returncode:
                    failures.append('network cleanup failed')
            except Exception:
                failures.append('network inspection or removal failed')
        try:
            remaining = self.docker('ps', '--all', '--quiet', '--filter', 'label=' + LABEL + '=' + self.identifier)
            networks = self.docker('network', 'ls', '--quiet', '--filter', 'label=' + LABEL + '=' + self.identifier)
            if remaining.stdout.strip() or networks.stdout.strip():
                failures.append('owned resources remain')
        except Exception:
            failures.append('cannot verify cleanup')
        self.log.flush()
        if self.safe_log(self.output / 'services.log', (self.output / 'services.log').read_text()):
            failures.append('fixture secret detected and redacted in service log')
        self.cleanup_verified = not failures
        if failures:
            raise AssertionError('; '.join(failures))


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/payment-category-image')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'scope': 'current-schema packaged category HTTP, not installed lifecycle',
              'complete_payment_category_parity': False, 'cleanup_verified': False}
    report_path = args.output / 'results.json'
    report_path.write_text(json.dumps(report) + '\n')
    tool_env = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-category-image-', dir='/tmp') as directory, \
            (args.output / 'services.log').open('w') as log:
        work = Path(directory)
        proof = ImageFixture(args.image, args.expected_revision, work, args.output, log)
        try:
            proof.start_services(args.postgres, args.redis)
            initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
            from django.test import override_settings
            from auditlog.context import disable_auditlog
            from application.models import User
            from TESTS.adapters.django.payment_categories import run, uid
            with disable_auditlog():
                User.original_objects.create(user_id=uid(1), username='category-image-admin',
                    password='!synthetic-only', role=1, is_superuser=True)
            os.environ.update(TESTS_PAYMENT_CATEGORIES_GO='1', TESTS_PAYMENT_CATEGORIES_VERSIONED='1')
            cache = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
                'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                            'PASSWORD': 'synthetic-identity-only'}}}
            with override_settings(CACHES=cache):
                result = run(work, log, tool_env, f'127.0.0.1:{proof.redis_port}',
                             go_server_factory=proof.server)
            assert result['success'] and result['http_cases'] >= 188
            assert result['go']['http_cases'] == result['http_cases']
            assert len(result['go']['fault_probes']) == 18
            assert set(result['versioned']['routes']) == {'/api/v1', '/api/v2'}
            report.update(oracle=result, checks_passed=True)
        finally:
            if 'django.db' in sys.modules:
                from django.db import connections
                connections.close_all()
            try:
                proof.cleanup()
            finally:
                report.update(image=proof.metadata, cleanup_verified=proof.cleanup_verified)
                report['success'] = bool(report.get('checks_passed') and proof.cleanup_verified)
                report['source_sha256'] = {
                    path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                    for path in ('TESTS/adapters/go/payment_category_image.py',
                                 'TESTS/adapters/django/payment_categories.py',
                                 'TESTS/contracts/test_payment_categories.py', 'TESTS/adapters/caddy.py')}
                report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'success': report['success'], 'image': proof.metadata,
                      'http_cases_per_backend': result['http_cases'], 'cleanup_verified': proof.cleanup_verified}))


if __name__ == '__main__':
    main()
