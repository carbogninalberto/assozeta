"""Authored manifest contracts on Django, an immutable Go image and Caddy v1/v2.

Owns current-schema PostgreSQL/Redis and all runtime resources. This does not
replace native repository bounds/cancellation, installed lifecycle or PWA tests.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.payment_category_image import DATABASE, ImageFixture
from TESTS.contracts.test_instance_bootstrap import validate_report
from TESTS.http_client import Client, poll

SOURCES = (
    'TESTS/adapters/go/instance_manifest_image.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/django/instance_bootstrap.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/fixtures/instance-bootstrap.json',
    'TESTS/contracts/test_instance_bootstrap.py',
    'TESTS/http_client.py', 'TESTS/adapters/caddy.py',
    'selfhost/caddy/Caddyfile', 'UI/Dockerfile',
)


def source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}


class ManifestImageFixture(ImageFixture):
    def run_container(self, suffix, image, *args):
        if suffix == 'server':
            # Explicit startup /readyz and /healthz still run. Suppress only the
            # timer's unrelated SELECTs during per-request SQL measurement.
            args += ('--no-healthcheck', '--env',
                     'CORS_ALLOWED_ORIGINS=https://allowed.example.test')
        container = super().run_container(suffix, image, *args)
        if suffix == 'server':
            self.server_container = container
            assert self.inspect(container)['Config']['Healthcheck']['Test'] == ['NONE']
            self.metadata['periodic_healthcheck'] = 'disabled for isolated SQL measurement; startup probes required'
        return container

    def safe_log(self, path, contents):
        if path.name == 'postgres.log':
            # SQL contains synthetic session/user/configuration data. Export
            # observations, never the raw database statement log.
            path.write_text('Private SQL log omitted; see per-request observations in results.json.\n')
            return False
        return super().safe_log(path, contents)

    def sql_marker(self):
        from django.db import connection
        marker = 'manifest_measure_' + uuid4().hex
        with connection.cursor() as cursor:
            cursor.execute('SELECT %s', [marker])
            assert cursor.fetchone() == (marker,)
        return marker

    def statements_between(self, start, end):
        recorded = ''
        def ready():
            nonlocal recorded
            result = self.docker('logs', self.postgres)
            recorded = result.stdout + result.stderr
            return start in recorded and end in recorded
        # poll includes its last value on timeout: return only a boolean so a
        # logging failure cannot print private SQL/session data into CI output.
        poll(ready, bool, timeout=10, interval=.025)
        records = re.split(r'(?m)^(?=\d{4}-\d{2}-\d{2} .* manifest-sql )', recorded)
        statements = []
        active = finished = False
        for record in records:
            if start in record:
                assert not active, 'SQL start delimiter repeated'
                active = True
                continue
            if end in record:
                assert active, 'SQL end delimiter precedes start'
                finished = True
                break
            if active:
                match = re.search(r'LOG:  (?:execute [^:]+|statement): (.*)', record, re.S)
                if match:
                    sql = match.group(1).split('\n20', 1)[0]
                    statements.append(re.sub(r'(?m)^\s*--[^\n]*(?:\n|$)', '', sql).strip())
        assert active and finished, 'SQL capture delimiters missing'
        return [sql for sql in statements if sql]

    @contextlib.contextmanager
    def container_restore_lock(self):
        # Host and Docker may have different kernels. Hold the real lock in each
        # runtime separately; this does not prove cross-kernel lock sharing.
        process = subprocess.Popen([
            'docker', 'exec', '-i', self.server_container, 'flock', '-x',
            '/operations/lifecycle.lock', 'sh', '-c', 'echo locked; read release'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready and process.stdout.readline() == b'locked\n', 'Container lock was not acquired'
            probe = self.docker('exec', self.server_container, 'flock', '-n', '-x',
                               '/operations/lifecycle.lock', 'true', check=False)
            assert probe.returncode == 1, 'Independent contender did not observe held lock'
            yield
        finally:
            try:
                if process.poll() is None:
                    process.stdin.write(b'release\n')
                    process.stdin.flush()
                process.wait(timeout=10)
                assert process.returncode == 0, 'Container lock holder failed'
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                process.stdin.close()
                process.stdout.close()


def require_upstream(item, django):
    assert item['django_requests'] == int(django), ('upstream_mismatch', item['django_requests'], django)


@contextlib.contextmanager
def runtime_stack(report):
    stack = contextlib.ExitStack()
    try:
        yield stack
    finally:
        stack.close()
        report['proxy_cleanup_verified'] = True


def run_matrix(proof, work, log, tool_env, report):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connection, connections, transaction
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation
    from instance.models import InstanceConfiguration
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.adapters.caddy import proxy
    from TESTS.adapters.django.instance_bootstrap import REQUEST_COUNTS, QUERY_RECORDS, uid
    import redis
    import uvicorn

    fixture = json.loads((ROOT / 'TESTS/fixtures/instance-bootstrap.json').read_text())
    cases = {name: spec for name, spec in fixture['cases'].items()
             if spec['request']['path'].split('?')[0] == '/instance/manifest.json'}
    assert len(cases) == 42, 'Review new manifest cases before updating image acceptance'
    assert all(set(spec['setup']) <= {'config', 'second', 'actor_state', 'token', 'cookie', 'lock'}
               and spec['request']['actor'] in ('anonymous', 'owner') for spec in cases.values())
    models = [model for model in apps.get_models(include_auto_created=True)
              if model._meta.managed and not model._meta.proxy]

    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values()) for model in models}

    initial = snapshot()
    report['managed_models_including_auto_created'] = len(models)
    raw = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1,
                      password='synthetic-identity-only', socket_timeout=2)
    cache_settings = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'synthetic-identity-only'}}}
    server = thread = owner = association = None
    report['workloads'] = {name: {'schema_version': 1, 'cases': {}} for name in ('django', 'image', 'v1', 'v2')}

    def observe(client, path, case_name, spec, headers):
        before = snapshot()
        cache_before = {key: raw.get(key) for key in raw.scan_iter()}
        REQUEST_COUNTS.pop(case_name, None)
        QUERY_RECORDS.pop(case_name, None)
        with contextlib.ExitStack() as locks:
            if spec['setup'].get('lock'):
                lease = locks.enter_context(open(settings.DATA_RESTORE_LOCK_PATH, 'a'))
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locks.enter_context(proof.container_restore_lock())
            start = proof.sql_marker()
            status, wire, response_headers = client.request(spec['request']['method'], path,
                headers={**headers, 'X-Contract-Case': case_name}, raw=True, include_headers=True)
            end = proof.sql_marker()
        sql = proof.statements_between(start, end)
        decoded = wire
        encoding = response_headers.get('Content-Encoding')
        if encoding:
            assert encoding.lower() == 'gzip', 'Unexpected content encoding'
            if wire:
                decoded = gzip.decompress(wire)
            else:
                assert spec['request']['method'] == 'HEAD', 'Empty gzip body'
        try:
            body = json.loads(decoded)
        except (ValueError, UnicodeDecodeError):
            body = decoded.decode(errors='replace')
        vary = response_headers.get_all('Vary', [])
        observed = {'status': status, 'body': body, 'database_unchanged': before == snapshot(),
            'write_statements': sum(not statement.upper().startswith(
                ('SELECT', 'SHOW', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE')) for statement in sql),
            'selects': sum(statement.upper().startswith('SELECT') for statement in sql),
            'sql_statement_count': len(sql),
            'django_requests': REQUEST_COUNTS.pop(case_name, 0),
            'vary_headers': vary,
            'vary_tokens': sorted({token.strip().lower() for line in vary for token in line.split(',') if token.strip()}),
            'wire_body_bytes': len(wire), 'decoded_body_bytes': len(decoded),
            'gzip_decoded': bool(encoding and wire),
            'headers': {key: response_headers.get(key) for key in (
                'Allow', 'Content-Type', 'Content-Language', 'WWW-Authenticate', 'Retry-After', 'Content-Encoding')},
            'secret_free': all(value not in json.dumps(body, ensure_ascii=False) for value in (
                'private-email-canary', 'private-legacy-canary', 'private-diagnostic-canary',
                'private-ai-canary', 'private-stripe-canary', 'private-manual-canary', 'corrupt-secret-canary'))}
        observed['headers']['Vary'] = ', '.join(vary) if vary else None
        cache_after = {key: raw.get(key) for key in raw.scan_iter()}
        if spec['setup'].get('cookie') == 'stale':
            assert not cache_after and 'sessionid=' in (response_headers.get('Set-Cookie') or ''), 'Stale session not expired'
        else:
            assert cache_before == cache_after, 'Unexpected cache mutation'
        observed['cache_semantics_verified'] = True
        return observed

    try:
        with disable_auditlog():
            owner = User.original_objects.create(user_id=uid(810000), username='OWNER',
                email='owner@example.test', password='!fixture', role=1)
            association = SportAssociation.original_objects.create(sport_association_id=uid(820001),
                user=owner, denomination='Primary à ASD')
        token = AccessToken()
        token['user_id'] = str(owner.pk)
        owner_token = str(token)
        with connection.cursor() as cursor:
            cursor.execute("ALTER SYSTEM SET log_line_prefix = '%m [%p] manifest-sql '")
            cursor.execute("ALTER SYSTEM SET log_statement = 'all'")
            cursor.execute('SELECT pg_reload_conf()')
            assert cursor.fetchone() == (True,)
        def logging_ready():
            with connection.cursor() as cursor:
                cursor.execute('SHOW log_statement')
                return cursor.fetchone()[0] == 'all'
        poll(logging_ready, bool, timeout=10, interval=.025)
        start = proof.sql_marker()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 12345')
            assert cursor.fetchone() == (12345,)
        statements = proof.statements_between(start, proof.sql_marker())
        assert statements == ['SELECT 12345'], ('SQL observer failed', statements)
        report['sql_observer_control'] = {'success': True, 'expected_selects': 1, 'observed_selects': 1}
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE manifest_sql_probe (value integer)')
        start = proof.sql_marker()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO manifest_sql_probe VALUES (12345)')
            transaction.set_rollback(True)
        statements = proof.statements_between(start, proof.sql_marker())
        assert statements == ['BEGIN', 'INSERT INTO manifest_sql_probe VALUES (12345)', 'ROLLBACK'], \
            'SQL observer failed to capture rolled-back mutation'
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM manifest_sql_probe')
            assert cursor.fetchone() == (0,)
            cursor.execute('DROP TABLE manifest_sql_probe')
        report['sql_observer_rollback_control'] = {'success': True, 'observed_inserts': 1, 'durable_rows': 0}
        middleware = ['TESTS.adapters.django.instance_bootstrap.ObserveQueries', *settings.MIDDLEWARE]
        with override_settings(CACHES=cache_settings, MIDDLEWARE=middleware, CORS_ORIGIN_ALLOW_ALL=False,
                CORS_ALLOWED_ORIGINS=['https://allowed.example.test']), runtime_stack(report) as stack:
            server = uvicorn.Server(uvicorn.Config(get_asgi_application(), host='0.0.0.0', port=0,
                lifespan='off', log_level='critical'))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            poll(lambda: server.started, bool, timeout=20, interval=.01)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            django_upstream = f'host.docker.internal:{django_port}'
            image_runtime = stack.enter_context(proof.server(work, log, tool_env, f'127.0.0.1:{proof.redis_port}'))
            gateway, caddy_image = stack.enter_context(proxy(django_upstream, image_runtime['proxy_upstream'],
                log, network=proof.network))
            report['caddy_image'] = caddy_image
            clients = {'django': Client(f'http://127.0.0.1:{django_port}'),
                       'image': Client(image_runtime['base_url']), 'v1': Client(gateway), 'v2': Client(gateway)}
            for name, spec in cases.items():
                setup, request = spec['setup'], spec['request']
                with disable_auditlog():
                    InstanceConfiguration.objects.all().delete()
                    User.original_objects.filter(pk=owner.pk).update(is_active=True, deleted=False)
                    if setup.get('actor_state'):
                        User.original_objects.filter(pk=owner.pk).update(**setup['actor_state'])
                    if setup.get('config', {}) is not None:
                        fields = dict(domain='fixture.example.test', name='Fixture à ASD', abbreviation='Fà',
                            support_email='support@example.test', email_password_encrypted='private-email-canary',
                            stripe_secret_key='private-legacy-canary', diagnostic_results={'secret': 'private-diagnostic-canary'})
                        fields.update(setup.get('config', {}))
                        config = InstanceConfiguration.objects.create(**fields)
                        InstanceConfiguration.objects.filter(pk=config.pk).update(
                            updated_at=datetime(2026, 10, 22, 12, 34, 56, 123456, tzinfo=timezone.utc))
                        if setup.get('second'):
                            InstanceConfiguration.objects.bulk_create([
                                InstanceConfiguration(domain='second.example.test', name='Wrong second')])
                headers = dict(request['headers'])
                if request['actor'] == 'owner':
                    headers['Authorization'] = 'Bearer ' + owner_token
                if setup.get('token'):
                    assert setup['token'] == 'expired'
                    expired = AccessToken()
                    expired['user_id'], expired['exp'] = str(owner.pk), 1
                    headers['Authorization'] = 'Bearer ' + str(expired)

                def seed_cache():
                    raw.flushdb()
                    if setup.get('cookie'):
                        cache.set('django.contrib.sessions.cachebootstrap-session', {
                            '_auth_user_id': str(owner.pk), '_auth_user_backend': 'django.contrib.auth.backends.ModelBackend',
                            '_auth_user_hash': owner.get_session_auth_hash() if setup['cookie'] == 'valid' else 'invalid-hash'}, 300)
                        headers['Cookie'] = 'sessionid=bootstrap-session'

                for workload, client in clients.items():
                    seed_cache()
                    versioned = workload in ('v1', 'v2')
                    path = ('/api/' + workload if versioned else '') + request['path']
                    item = observe(client, path, workload + '-' + name, spec, headers)
                    report['workloads'][workload]['cases'][name] = item
                    override = spec.get('versioned_expected', {}) if versioned else {}
                    expected = {**spec['expected'], **override,
                        'headers': {**spec['expected'].get('headers', {}), **override.get('headers', {})}}
                    validate_report({'schema_version': 1, 'cases': {name: item}},
                                    {'cases': {name: {**spec, 'expected': expected}}})
                    require_upstream(item, workload in ('django', 'v1'))
                print(f'PASS {name}: Django, image, v1, v2', flush=True)
                if name == 'manifest-default':
                    seed_cache()
                    with proxy(django_upstream, django_upstream, log, network=proof.network) as (wrong, _):
                        item = observe(Client(wrong), '/api/v2' + request['path'], 'misrouted-' + name, spec, headers)
                    validate_report({'schema_version': 1, 'cases': {name: item}}, {'cases': {name: spec}})
                    rejected = False
                    try:
                        require_upstream(item, False)
                    except AssertionError as error:
                        rejected = 'upstream_mismatch' in str(error)
                    assert rejected and item['status'] == 200 and item['django_requests'] == 1
                    report['routing_negative_control'] = {'success': True, 'status': 200,
                        'django_requests': 1, 'rejected_reason': 'upstream_mismatch'}
            assert all(set(value['cases']) == set(cases) for value in report['workloads'].values())
        report['proxy_cleanup_verified'] = True
        report['http_cases_per_workload'] = len(cases)
        report['django_request_counts'] = {name: sum(case['django_requests'] for case in data['cases'].values())
                                         for name, data in report['workloads'].items()}
        report['restore_lock_scope'] = 'Real host and container locks held separately; cross-kernel interoperability not tested'
    finally:
        errors = []
        try:
            if server:
                server.should_exit = True
                if thread:
                    thread.join(timeout=20)
                    assert not thread.is_alive(), 'ASGI thread did not stop'
            report['asgi_stopped'] = True
        except Exception as error:
            errors.append({'stage': 'asgi', 'error_type': type(error).__name__})
        try:
            with disable_auditlog():
                InstanceConfiguration.objects.all().delete()
                if association:
                    SportAssociation.original_objects.filter(pk=association.pk).delete()
                if owner:
                    User.original_objects.filter(pk=owner.pk).delete()
            assert snapshot() == initial, 'Managed rows did not return to initial state'
            report['managed_cleanup_verified'] = True
        except Exception as error:
            errors.append({'stage': 'managed_rows', 'error_type': type(error).__name__})
        try:
            raw.flushdb()
            raw.close()
            connections.close_all()
        except Exception as error:
            errors.append({'stage': 'connections', 'error_type': type(error).__name__})
        report['oracle_cleanup_errors'] = errors
        if errors:
            raise RuntimeError('Manifest oracle cleanup failed; see results.json')


def main():
    require_production_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/instance-manifest-image')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.expected_revision):
        parser.error('--expected-revision must be a full Git revision')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {'success': False, 'scope': __doc__, 'cleanup_verified': False,
        'native_repository_groups': {'status': 'not_run', 'required_separately': True},
        'source_sha256': source_hashes(), 'started_at': datetime.now(timezone.utc).isoformat()}
    result_path = args.output / 'results.json'
    result_path.write_text(json.dumps(report, indent=2) + '\n')
    tool_env = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-manifest-image-', dir='/tmp') as directory, \
            (args.output / 'services.log').open('w') as log:
        work = Path(directory)
        proof = ManifestImageFixture(args.image, args.expected_revision, work, args.output, log)
        try:
            proof.start_services(args.postgres, args.redis)
            initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
            run_matrix(proof, work, log, tool_env, report)
            report['checks_passed'] = True
        except Exception as error:
            report['error_type'] = type(error).__name__
            raise
        finally:
            errors = []
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            except Exception as error:
                errors.append({'stage': 'database_connections', 'error_type': type(error).__name__})
            try:
                proof.cleanup()
            except Exception as error:
                errors.append({'stage': 'docker_resources', 'error_type': type(error).__name__})
            report.update(image=proof.metadata, cleanup_verified=proof.cleanup_verified,
                cleanup_errors=errors, source_stable=report['source_sha256'] == source_hashes(),
                elapsed_seconds=time.monotonic() - started, finished_at=datetime.now(timezone.utc).isoformat())
            report['success'] = bool(report.get('checks_passed') and proof.cleanup_verified
                and report.get('managed_cleanup_verified') and report.get('asgi_stopped')
                and report.get('proxy_cleanup_verified') and report['source_stable'] and not errors)
            result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
            if errors:
                raise RuntimeError('Packaged manifest cleanup failed; see results.json')
    assert report['success'], 'Manifest image acceptance incomplete'
    print(json.dumps({'success': True, 'cases_per_workload': report['http_cases_per_workload'],
        'django_request_counts': report['django_request_counts'], 'image': proof.metadata}))


if __name__ == '__main__':
    main()
