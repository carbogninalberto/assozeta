"""Web UI API version switch in a browser, behind the shipped Caddyfile.

The built UI is served by the shipped selfhost Caddyfile (``/api`` and
``/api/v1`` to a real Django ASGI server, ``/api/v2`` to the real Go server),
both on one private PostgreSQL and Redis. TESTS/browser/ui-api-version.mjs
logs in with a password, opens core pages, switches the setting on from the
Self Instance page, opens a new browser, uses the ``?api=v1`` override,
simulates a failing readiness probe and switches the setting off. This runner
then checks the stored setting, the two audit entries and, from an independent
observer on the Django server, that no API request reached Django while the
browser was on v2 (only the canonical manifest/logo URLs and WebSockets).

Usage (migration interpreter, pinned Go on PATH, Docker running):
  python TESTS/adapters/django/ui_api_version_browser.py --ui-dir UI-BUILD/dist/public
"""
import argparse
import contextlib
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
DATABASE = 'assozeta_contract_ui_api_version_browser'
CACHE_PASSWORD = 'private-ui-api-browser-cache'
DJANGO_ON_V2 = ('/instance/manifest.json', '/instance/logo.png')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class DjangoObserver:
    """Records (time in ms, path, protocol) of every request the Django server receives."""

    def __init__(self, application):
        self.application = application
        self.lock = threading.Lock()
        self.seen = []

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            with self.lock:
                self.seen.append((time.time() * 1000, scope['path'], scope['type']))
        await self.application(scope, receive, send)

    def between(self, start, end):
        with self.lock:
            return [(path, kind) for at, path, kind in self.seen if start <= at <= end]


@contextlib.contextmanager
def shipped_caddy(ui_dir, django_port, go_port, log):
    """The selfhost Caddyfile with only the two upstream names and the site address replaced."""
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    name = 'az-ui-api-version-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='az-ui-api-caddy-') as directory:
        work = Path(directory).resolve()
        (work / 'operations').mkdir()
        configuration = (ROOT / 'selfhost/caddy/Caddyfile').read_text()
        configuration = re.sub(r'api-go:8000|api:8000', lambda match: f'host.docker.internal:{go_port}'
                               if match[0] == 'api-go:8000' else f'host.docker.internal:{django_port}', configuration)
        assert configuration.count(f'host.docker.internal:{go_port}') >= 2, 'Go upstream missing from Caddyfile'
        (work / 'Caddyfile').write_text(configuration)

        def docker(*args, check=True):
            return subprocess.run(['docker', *map(str, args)], check=check, stdout=subprocess.PIPE, stderr=log,
                                  timeout=120, text=True)
        try:
            docker('run', '--pull=never', '--rm', '-d', '--name', name, '--add-host', 'host.docker.internal:host-gateway',
                   '-p', '127.0.0.1::80', '-e', 'SITE_ADDRESS=:80', '-e', 'ACME_EMAIL=fixture@example.invalid',
                   '-v', f'{work}/Caddyfile:/etc/caddy/Caddyfile:ro', '-v', f'{ui_dir}:/srv:ro',
                   '-v', f'{work}/operations:/run/assozeta-operations:ro', image)
            port = int(docker('port', name, '80/tcp').stdout.strip().rsplit(':', 1)[1])
            origin = f'http://127.0.0.1:{port}'
            from TESTS.http_client import Client, poll

            def ready():
                try:
                    return Client(origin).request('GET', '/healthz')[0] == 200
                except OSError:
                    return False
            poll(ready, bool, timeout=30)
            yield origin, image
        finally:
            docker('rm', '-f', name, check=False)


def exercise(work, output, log, options, tool_env):
    import redis
    import uvicorn
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.db import connections
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration, PaymentCategory, VatManagement
    from instance.models import InstanceConfiguration
    from TESTS.http_client import poll
    from TESTS.adapters.django.login_jpeg import build_codec

    cache_port = options.cache_port
    redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(cache_port), '--save', '',
                                      '--appendonly', 'no', '--requirepass', CACHE_PASSWORD, '--dir', str(work)],
                                     stdout=log, stderr=subprocess.STDOUT)
    cache_client = redis.Redis(host='127.0.0.1', port=cache_port, db=1, password=CACHE_PASSWORD)
    server = thread = go_process = None
    runtime = dict(RUNNING_VERSION='ui-api-browser-fixture', AI_API_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
                   SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', SOCIAL_AUTH_APPLE_ID_CLIENT='', STRIPE_KEY='', STRIPE_PUBLIC_KEY='',
                   STRIPE_WEBHOOK_SECRET='', MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='', MANUAL_SOURCE_ROOT='',
                   MANUAL_BUNDLE_PATH=str(work / 'absent-manual'), MANUAL_BUNDLE_REQUIRED=False,
                   MANUAL_INDEX_PATH=str(work / 'absent-index.json'), MANUAL_DEVELOPMENT_ROOT=str(work / 'absent-development'),
                   FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False, MULTIPLE_REGISTRATION_ENABLED=False)
    caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{cache_port}/1',
                          'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': CACHE_PASSWORD}}}
    layers = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {
        'hosts': [f'redis://:{CACHE_PASSWORD}@127.0.0.1:{cache_port}/2']}}}
    password = 'private-ui-api-browser-password'
    try:
        def cache_ready():
            try:
                return cache_client.ping()
            except redis.ConnectionError:
                return False
        poll(cache_ready, bool, timeout=15)
        with override_settings(**runtime, CACHES=caches, CHANNEL_LAYERS=layers, PASSWORD_HASHERS=PASSWORD_HASHERS):
            from core.asgi import application
            observer = DjangoObserver(application)
            server = uvicorn.Server(uvicorn.Config(observer, host='0.0.0.0', port=0, lifespan='off', log_level='critical',
                                                   access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            build_codec(work / 'image-codec', log, tool_env)
            subprocess.run(['go', 'build', '-o', str(work / 'ui-api-go'), './cmd/server'], cwd=ROOT / 'BE_GO', env=tool_env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            go_port = free_port()
            environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only',
                           'DBHOST': str(work), 'DBPORT': '5432', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(cache_port),
                           'REDIS_PASSWORD': CACHE_PASSWORD, 'GO_LISTEN_ADDR': f'0.0.0.0:{go_port}',
                           'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False', 'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
                           **{key: str(value) for key, value in runtime.items()},
                           **{key: getattr(settings, key) for key in (
                               'SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                               'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')}}
            go_process = subprocess.Popen([str(work / 'ui-api-go')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)

            def go_ready():
                if go_process.poll() is not None:
                    raise AssertionError('Private Go server exited')
                try:
                    with socket.create_connection(('127.0.0.1', go_port), timeout=.2):
                        return True
                except OSError:
                    return False
            poll(go_ready, bool, timeout=30)
            encoded = make_password(password)
            assert encoded.startswith('pbkdf2_sha256$')
            with disable_auditlog():
                owner = User.original_objects.create(username='UIAPI-OWNER', email='uiapi-owner@example.test', role=User.ASSOCIATION,
                                                     password=encoded, first_name='Owner', last_name='Fixture',
                                                     date_joined=timezone.now() - timedelta(days=60), auto_paid_payment=False)
                association = SportAssociation.original_objects.create(user=owner, denomination='UI API Browser ASD',
                                                                       tax_code='12345678901', additional_sections=[])
                SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
                config = InstanceConfiguration.objects.create(domain='ui-api.example.test', name='UI API Fixture',
                                                              primary_association=association, support_email='support@example.test',
                                                              setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_IMPORT)
                PaymentCategory.objects.create(name='Browser baseline category', sport_association=association,
                                               vat_management=VatManagement.objects.create(vat=1, deduction=0))
            assert config.ui_api_version == 'v1'
            audit_before = set(LogEntry.objects.values_list('pk', flat=True))
            with shipped_caddy(options.ui_dir, django_port, go_port, log) as (origin, image):
                from TESTS.http_client import Client
                # Password login through Caddy on both versions before the browser runs.
                logins = {}
                for prefix in ('/api', '/api/v2'):
                    status, body, headers = Client(origin).request('POST', prefix + '/oauth2/login',
                        {'username': owner.username, 'password': password}, headers={'Accept': 'application/json'},
                        include_headers=True)
                    logins[prefix] = {'status': status, 'headers': sorted(headers.keys())}
                    assert status == 200 and isinstance(body, dict) and body.get('access_token'), (prefix, status, logins)
                (output / 'http-logins.json').write_text(json.dumps(logins, indent=1) + '\n')
                if options.hold:
                    (output / 'origin.txt').write_text(origin + '\n')
                    hold_until = time.time() + options.hold
                    while time.time() < hold_until and not (output / 'release').exists():
                        time.sleep(1)
                fixture = dict(origin=origin, username=owner.username, password=password,
                               playwright_module=str(options.playwright_module))
                fixture_path = work / 'browser.json'
                fixture_path.write_text(json.dumps(fixture))
                fixture_path.chmod(0o600)
                try:
                    run = subprocess.run(['node', str(ROOT / 'TESTS/browser/ui-api-version.mjs')], cwd=ROOT,
                                         env={**tool_env, 'TESTS_FIXTURE': str(fixture_path), 'TESTS_OUTPUT': str(output)},
                                         stdout=log, stderr=subprocess.STDOUT, timeout=480)
                finally:
                    fixture_path.unlink(missing_ok=True)
            browser = json.loads((output / 'browser.json').read_text())
            assert run.returncode == 0 and browser['success'], browser['failures']
            phases = {item['name']: item for item in browser['phases']}
            expected = ['v1-default', 'enable', 'v2-same-session', 'v2-new-browser', 'override-v1', 'probe-failure-fallback',
                        'disable', 'v1-after-disable']
            assert list(phases) == expected, list(phases)
            django = {}
            for name, item in phases.items():
                seen = observer.between(item['start'], item['end'])
                http = [path for path, kind in seen if kind == 'http']
                django[name] = {'http': len(http), 'websocket': sum(1 for _, kind in seen if kind == 'websocket'),
                                'http_paths': sorted(set(http))}
                if name.startswith('v2-'):
                    allowed = DJANGO_ON_V2 + (('/instance/status', '/instance/config') if name == 'v2-new-browser' else ())
                    unexpected = sorted({path for path in http if path not in allowed})
                    assert unexpected == [], (name, 'API requests reached Django', unexpected)
                elif name in ('v1-default', 'override-v1', 'probe-failure-fallback', 'v1-after-disable'):
                    assert len(http) >= 5 and '/instance/status' in http, (name, django[name])
            config.refresh_from_db()
            assert config.ui_api_version == 'v1', 'setting not switched off'
            entries = list(LogEntry.objects.exclude(pk__in=audit_before).filter(
                content_type__app_label='instance', content_type__model='instanceconfiguration').order_by('id'))
            assert [entry.changes for entry in entries] == [{'ui_api_version': ['v1', 'v2']}, {'ui_api_version': ['v2', 'v1']}], \
                [entry.changes for entry in entries]
            assert all(entry.actor_id == owner.pk for entry in entries)
            return {'success': True, 'shipped_caddy_image': image, 'browser_checks': browser['checks'], 'known_v2_gaps': browser['known_gaps'],
                    'django_observer_per_phase': django, 'audit_entries': [entry.changes for entry in entries],
                    'final_ui_api_version': config.ui_api_version}
    finally:
        try:
            if go_process:
                go_process.terminate()
                try:
                    go_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    go_process.kill()
                    go_process.wait(timeout=10)
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=30)
        finally:
            try:
                connections.close_all()
            finally:
                redis_process.terminate()
                try:
                    redis_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    redis_process.kill()
                    redis_process.wait(timeout=10)


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ui-dir', type=Path, required=True, help='Built UI dist/public directory')
    parser.add_argument('--output', type=Path, default=ROOT / 'quality-reports/go-migration/ui-api-version-browser')
    parser.add_argument('--playwright-module', type=Path,
                        default=ROOT / 'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    parser.add_argument('--hold', type=int, default=0, help='Debugging: keep the stack up this many seconds before the browser')
    options = parser.parse_args()
    options.ui_dir = options.ui_dir.resolve()
    options.playwright_module = options.playwright_module.resolve()
    require_production_python()
    assert (options.ui_dir / 'index.html').is_file(), 'Built UI required (index.html missing)'
    assert options.playwright_module.is_file(), 'Playwright installation required'
    options.cache_port = free_port()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': __doc__.strip().splitlines()[0]}
    report = output / 'results.json'
    report.write_text(json.dumps(result, indent=2) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    assert postgres and shutil.which('redis-server'), 'Private PostgreSQL and Redis required'
    binaries = Path(postgres).parent
    tool_env = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'PLAYWRIGHT_BROWSERS_PATH')}
    tool_env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-ui-api-browser-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        started = False

        def command(args):
            return subprocess.run([str(x) for x in args], env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            command([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            started = True
            command([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            command([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            key = Ed25519PrivateKey.generate()
            import environ
            os.environ.clear()
            os.environ.update(tool_env)
            environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
            os.environ.update(
                DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()).decode(),
                JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                             serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE=DATABASE, TESTS_WORKDIR=str(work),
                DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432', DBNAME=DATABASE, DBUSER='contracts', DBPASSWORD='test-only',
                USE_PGBOUNCER='False', STORE_ON_DISK='True', REDIS_HOST='127.0.0.1', REDIS_PORT=str(options.cache_port),
                REDIS_PASSWORD=CACHE_PASSWORD, REDIS_USERNAME='', REDIS_SSLAUTH='')
            import django
            with patch('environ.Env.read_env'):
                django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
            call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=log, stderr=log)
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            result.update(exercise(work, output, log, options, tool_env))
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                if started:
                    subprocess.run([str(binaries / 'pg_ctl'), '-D', str(work / 'postgres'), '-m', 'immediate', '-w', 'stop'],
                                   stdout=log, stderr=subprocess.STDOUT, timeout=30)
                report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'success': result['success'], 'checks': len(result.get('browser_checks', []))}))


if __name__ == '__main__':
    main()
