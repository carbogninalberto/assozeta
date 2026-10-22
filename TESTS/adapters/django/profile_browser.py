"""Unchanged built frontend against real Django/Go personal-profile HTTP roots.

This private proxy selects the API implementation only for the named workflow.
Canonical logo/manifest/export-denial resources and root WebSockets remain Django.
No installed alias switch, Celery handoff, full UI parity or historical migration
is claimed. Browser authentication always starts with a real password login.
"""
import argparse
import base64
import contextlib
from datetime import timedelta
import hashlib
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
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
DATABASE = 'assozeta_contract_profile_browser'
SELECTED_ROUTES = ('instance/status', 'instance/config', 'instance/access', 'oauth2/login',
                   'oauth2/refresh-token', 'oauth2/check/username', 'profile/info', 'profile/update')
COEXISTENCE_ROUTES = ('instance/manifest.json', 'instance/logo.png', 'association/export/active')
WORKFLOW_SOURCES = (
    'src/App.svelte', 'src/routes/login/Login.svelte', 'src/routes/profile/Profile.svelte',
    'src/routes/profile/ProfileMenu.svelte', 'src/routes/profile/sections/Account.svelte',
    'src/store/instanceStore.js', 'src/utils/NotificationService.js', 'src/utils/NotificationWebSocket.js',
    'src/utils/HealthWebSocket.js', 'src/utils/loginSession.js', 'src/utils/ApiMiddleware.js',
    'src/routes/dashboard/EmptyDasbhoard.svelte', 'src/routes.js',
)


def prepare_ui(image, artifact, receipt):
    """Extract one existing image without starting it; verify scoped source maps."""
    assert not artifact.exists(), 'Use a new artifact directory; never replace an existing frontend'
    assert not receipt.exists(), 'Use a new provenance receipt'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    def docker(*args, check=True):
        return subprocess.run(['docker', *map(str, args)], check=check, capture_output=True, text=True, timeout=90)
    inspected = json.loads(docker('image', 'inspect', image).stdout)[0]
    image_id = inspected['Id']
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)
    assert not inspected['Config'].get('Volumes'), 'Extraction must not create anonymous volumes'
    name = 'az-profile-ui-' + uuid.uuid4().hex
    result = {'success': False, 'image_id': image_id, 'artifact': str(artifact),
        'extraction_container_never_started': False, 'extraction_container_removed': False,
        'limits': ['Workflow source maps verify only the named source files, not every build input or dependency.',
                   'Both API runs reuse these exact compiled bytes; this preparation never rebuilds or patches the UI.']}
    try:
        docker('create', '--pull', 'never', '--name', name, image_id)
        assert json.loads(docker('inspect', name).stdout)[0]['State']['Status'] == 'created'
        result['extraction_container_never_started'] = True
        artifact.mkdir()
        docker('cp', name+':/srv/.', artifact)
        assert (artifact/'index.html').is_file()
        matches = {source: [] for source in WORKFLOW_SOURCES}
        for source_map in sorted(artifact.rglob('*.map')):
            data = json.loads(source_map.read_text())
            for source, content in zip(data.get('sources', []), data.get('sourcesContent', [])):
                for expected in WORKFLOW_SOURCES:
                    if source.endswith('/'+expected) or source == expected:
                        assert isinstance(content, str), 'Required embedded workflow source missing'
                        actual = (ROOT/'UI'/expected).read_bytes()
                        assert content.encode() == actual, 'Frontend workflow source differs: '+expected
                        matches[expected].append({'map': str(source_map.relative_to(artifact)), 'source': source,
                            'equal': True, 'embedded_sha256': hashlib.sha256(content.encode()).hexdigest(),
                            'current_sha256': hashlib.sha256(actual).hexdigest()})
        assert all(matches.values()), 'Frontend does not include every required workflow source map'
        result.update(workflow_source_maps=matches, files=fingerprints(artifact))
        result['artifact_file_count'] = len(result['files'])
    finally:
        docker('rm', '-f', name, check=False)
        remaining = docker('container', 'ls', '--all', '--format', '{{.Names}}', '--filter', 'name='+name).stdout.splitlines()
        assert name not in remaining, 'UI extraction container cleanup failed'
        result['extraction_container_removed'] = True
        receipt.write_text(json.dumps(result, indent=2)+'\n')
    result['success'] = True
    receipt.write_text(json.dumps(result, indent=2)+'\n')
    print('PASS: unchanged UI extracted; workflow source maps matched; temporary container removed')



def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def fingerprints(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


@contextlib.contextmanager
def gateway(work, log, ui, django_port, go_port, version):
    """Exact allowlist routing; unknown API paths fail instead of falling back."""
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    name = 'az-profile-browser-' + uuid.uuid4().hex
    target = django_port if version == 'v1' else go_port
    label = 'django-v1' if version == 'v1' else 'go-v2'
    config = '''{
 admin off
}
:80 {
 root * /srv
 route {
  handle /fixture-health {
   respond 200
  }
  @selected path SELECTED
  handle @selected {
   header X-Fixture-Backend LABEL
   uri strip_prefix /api
   reverse_proxy host.docker.internal:TARGET
  }
  @coexistence path COEXISTENCE
  handle @coexistence {
   header X-Fixture-Backend django-coexistence
   uri strip_prefix /api
   reverse_proxy host.docker.internal:DJANGO
  }
  handle /ws/* {
   header X-Fixture-Backend django-coexistence
   reverse_proxy host.docker.internal:DJANGO
  }
  @unknown path /api/* /instance-*
  handle @unknown {
   header X-Fixture-Backend unsupported-fixture-route
   respond 502
  }
  handle {
   header X-Fixture-Backend ui-static
   header Cache-Control no-cache
   try_files {path} /index.html
   file_server
  }
 }
}
'''.replace('SELECTED', ' '.join('/api/' + p for p in SELECTED_ROUTES)).replace('COEXISTENCE',
    ' '.join('/api/' + p for p in COEXISTENCE_ROUTES)).replace('LABEL', label).replace('TARGET', str(target)).replace('DJANGO', str(django_port))
    path = work / (version + '.Caddyfile'); path.write_text(config)
    def docker(*args, check=True):
        return subprocess.run(['docker', *map(str, args)], check=check, capture_output=True, text=True, timeout=60)
    # Missing preexisting image fails; this runner never builds or pulls images.
    image_id = json.loads(docker('image', 'inspect', image).stdout)[0]['Id']
    try:
        docker('run', '--detach', '--name', name, '--label', 'org.assozeta.profile-browser=true',
               '--add-host', 'host.docker.internal:host-gateway', '--publish', '127.0.0.1::80',
               '--mount', f'type=bind,source={path},target=/etc/caddy/Caddyfile,readonly',
               '--mount', f'type=bind,source={ui},target=/srv,readonly', image_id)
        port = int(docker('port', name, '80/tcp').stdout.strip().rsplit(':', 1)[1])
        from TESTS.http_client import Client, poll
        origin = f'http://127.0.0.1:{port}'
        def ready():
            try: return Client(origin).request('GET', '/fixture-health')[0] == 200
            except OSError: return False
        poll(ready, bool, timeout=30)
        docker('exec', name, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
        yield origin, {'image_id': image_id, 'configuration_sha256': hashlib.sha256(config.encode()).hexdigest(),
                       'selected_backend': label, 'canonical_api_alias_is_private_test_routing': True}
    finally:
        # No access log is enabled: URLs containing JWT query parameters and
        # Authorization/cookie headers must not enter persistent proof output.
        docker('rm', '-f', name, check=False)
        remaining = docker('container', 'ls', '--all', '--format', '{{.Names}}', '--filter', 'name=' + name).stdout.splitlines()
        assert name not in remaining, 'Private proxy cleanup failed'


def exercise(work, output, log, options, tool_env):
    import redis
    import uvicorn
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration
    from application.models.audit_models import AuditLogIndex
    from instance.models import InstanceConfiguration
    from TESTS.http_client import poll
    from TESTS.adapters.django.login_jpeg import build_codec

    cache_port = options.cache_port
    redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(cache_port),
        '--save', '', '--appendonly', 'no', '--requirepass', 'private-profile-browser-cache', '--dir', str(work)],
        stdout=log, stderr=subprocess.STDOUT)
    cache_client = redis.Redis(host='127.0.0.1', port=cache_port, db=1, password='private-profile-browser-cache')
    server = thread = go_process = None
    runtime = dict(RUNNING_VERSION='profile-browser-fixture', AI_API_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', SOCIAL_AUTH_APPLE_ID_CLIENT='', STRIPE_KEY='', STRIPE_PUBLIC_KEY='',
        STRIPE_WEBHOOK_SECRET='', MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='', MANUAL_SOURCE_ROOT='',
        MANUAL_BUNDLE_PATH=str(work/'absent-manual'), MANUAL_BUNDLE_REQUIRED=False,
        MANUAL_INDEX_PATH=str(work/'absent-index.json'), MANUAL_DEVELOPMENT_ROOT=str(work/'absent-development'),
        FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False, MULTIPLE_REGISTRATION_ENABLED=False)
    caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{cache_port}/1',
                         'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'private-profile-browser-cache'}}}
    layers = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {
        'hosts': [f'redis://:private-profile-browser-cache@127.0.0.1:{cache_port}/2']}}}
    password = 'private-profile-browser-password'
    reports = {}
    try:
        def cache_ready():
            try: return cache_client.ping()
            except redis.ConnectionError: return False
        poll(cache_ready, bool, timeout=15)
        with override_settings(**runtime, CACHES=caches, CHANNEL_LAYERS=layers, PASSWORD_HASHERS=PASSWORD_HASHERS):
            # Import the actual protocol router, including health and authenticated
            # notification consumers, after the private settings are in force.
            from core.asgi import application
            server = uvicorn.Server(uvicorn.Config(application, host='0.0.0.0', port=0, lifespan='off', log_level='critical', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            # Reuse the native codec fixture only when its exact source is the
            # same as the selected Go tree; never silently mix prior/current code.
            codec_path = Path('pkg/storage/loginimage/native')
            assert fingerprints(options.go_source_root/codec_path) == fingerprints(ROOT/'BE_GO'/codec_path)
            build_codec(work/'image-codec', log, tool_env)
            subprocess.run(['go', 'build', '-o', str(work/'profile-browser-go'), './cmd/server'],
                cwd=options.go_source_root, env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            go_port = free_port()
            environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only',
                'DBHOST': str(work), 'DBPORT': '5432', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(cache_port),
                'REDIS_PASSWORD': 'private-profile-browser-cache', 'GO_LISTEN_ADDR': f'0.0.0.0:{go_port}',
                'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False', 'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
                **{key: str(value) for key, value in runtime.items()},
                **{key: getattr(settings, key) for key in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
                    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME',
                    'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')}}
            go_process = subprocess.Popen([str(work/'profile-browser-go')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
            def go_ready():
                if go_process.poll() is not None: raise AssertionError('Private Go server exited')
                try:
                    with socket.create_connection(('127.0.0.1', go_port), timeout=.2): return True
                except OSError: return False
            poll(go_ready, bool, timeout=20)
            with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS): encoded = make_password(password)
            assert encoded.startswith('pbkdf2_sha256$'), 'Browser fixture requires actual supported production password hashing'
            with disable_auditlog():
                owner = User.original_objects.create(username='BROWSER-OWNER', email='browser-owner@example.test',
                    role=User.ASSOCIATION, password='!unused', first_name='Owner', last_name='Unchanged', date_joined=timezone.now()-timedelta(days=60))
                association = SportAssociation.original_objects.create(user=owner, denomination='Browser Fixture ASD',
                    tax_code='12345678901', additional_sections=[])
                SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
                actor = User.original_objects.create(username='BROWSER-COLLABORATOR', email='browser-collaborator@example.test',
                    role=User.COLLABORATOR, password=encoded, first_name='Initial', last_name='Person', connected_user=owner,
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions=['other.settings.read', 'other.settings.update'], date_joined=timezone.now()-timedelta(days=60))
                InstanceConfiguration.objects.create(domain='profile-browser.example.test', name='Private Profile Fixture',
                    primary_association=association, support_email='support@example.test', logo_path='/api/instance/logo.png')
            logo = Path(settings.MEDIA_ROOT)/'instance/logo.png'; logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3EwMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'))
            for version in ('v1', 'v2'):
                User.original_objects.filter(pk=actor.pk).update(first_name='Initial', last_name='Person')
                cache.clear()
                owner_before = User.original_objects.filter(pk=owner.pk).values().get()
                association_before = SportAssociation.original_objects.filter(pk=association.pk).values().get()
                audit_before = set(LogEntry.objects.values_list('pk', flat=True))
                with gateway(work, log, options.ui_dir, django_port, go_port, version) as (origin, routing):
                    target = output/version; target.mkdir(exist_ok=True)
                    for stale in ('browser.json', 'validation.json'): (target/stale).unlink(missing_ok=True)
                    fixture = dict(origin=origin, version=version, username=actor.username, password=password, user_id=str(actor.pk),
                        initial_first_name='Initial', initial_last_name='Person', changed_first_name='Browser '+version,
                        changed_last_name='Persisted', playwright_module=str(options.playwright_module))
                    fixture_path = work/(version+'-browser.json'); fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
                    try:
                        subprocess.run(['node', str(ROOT/'TESTS/browser/profile-personal.mjs')], cwd=ROOT,
                            env={**tool_env, 'TESTS_FIXTURE': str(fixture_path), 'TESTS_OUTPUT': str(target)},
                            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
                    finally: fixture_path.unlink(missing_ok=True)
                    browser = json.loads((target/'browser.json').read_text()); assert browser['success']
                    actor.refresh_from_db()
                    assert (actor.first_name, actor.last_name) == ('Browser '+version, 'Persisted')
                    assert actor.password == encoded and actor.role == User.COLLABORATOR and actor.connected_user_id == owner.pk
                    assert User.original_objects.filter(pk=owner.pk).values().get() == owner_before
                    assert SportAssociation.original_objects.filter(pk=association.pk).values().get() == association_before
                    events = list(LogEntry.objects.exclude(pk__in=audit_before).filter(object_pk=str(actor.pk)))
                    changes = [event for event in events if 'first_name' in event.changes_dict or 'last_name' in event.changes_dict]
                    assert len(changes) == 1, 'Duplicate or missing personal profile audit'
                    event = changes[0]
                    assert event.changes_dict == {'first_name': ['Initial', 'Browser '+version], 'last_name': ['Person', 'Persisted']}
                    assert event.actor_id == actor.pk and event.action == 1 and event.content_type.model == 'user'
                    assert not AuditLogIndex.objects.filter(log_entry_id=event.pk).exists(), 'Collaborator audit acquired an unrelated association index'
                    reports[version] = {'success': True, 'routing': routing, 'real_password_login': True,
                        'database_and_exact_audit_readback': True, 'owner_and_association_unchanged': True,
                        'no_collaborator_association_index': True, 'browser_report': str((target/'browser.json').relative_to(output))}
                    (target/'validation.json').write_text(json.dumps(reports[version], indent=2)+'\n')
                    print('PASS: ' + version + ' fresh login, personal edit, reload and database audit readback', flush=True)
    finally:
        try:
            if go_process:
                go_process.terminate()
                try: go_process.wait(timeout=30)
                except subprocess.TimeoutExpired: go_process.kill(); go_process.wait(timeout=10)
            if server: server.should_exit = True
            if thread:
                thread.join(timeout=30)
                assert not thread.is_alive(), 'Django ASGI thread did not stop'
        finally:
            try: connections.close_all()
            finally:
                redis_process.terminate()
                try: redis_process.wait(timeout=10)
                except subprocess.TimeoutExpired: redis_process.kill(); redis_process.wait(timeout=10)
    return reports


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True, help='Existing unmodified built UI dist/public directory')
    parser.add_argument('--ui-provenance', type=Path, required=True, help='Immutable-image artifact hash manifest')
    parser.add_argument('--prepare-ui-image', help='Preparation only: extract /srv from an existing image, verify workflow source maps and write provenance')
    parser.add_argument('--go-source-root', type=Path, default=ROOT/'BE_GO', help='Explicit Go module source tree; supports a pinned pre-bootstrap RED')
    parser.add_argument('--output', type=Path, default=ROOT/'quality-reports/go-migration/profile-browser')
    parser.add_argument('--playwright-module', type=Path, default=ROOT/'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    options = parser.parse_args(); options.ui_dir = options.ui_dir.resolve(); options.playwright_module = options.playwright_module.resolve()
    if options.prepare_ui_image:
        prepare_ui(options.prepare_ui_image, options.ui_dir, options.ui_provenance.resolve())
        return
    require_production_python()
    options.go_source_root = options.go_source_root.resolve()
    options.cache_port = free_port()
    assert (options.ui_dir/'index.html').is_file() and options.playwright_module.is_file(), 'Existing UI and pinned Playwright installation required'
    output = options.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    provenance_bytes = options.ui_provenance.read_bytes()
    provenance = json.loads(provenance_bytes)
    ui_hashes = fingerprints(options.ui_dir)
    assert provenance['success'] and provenance['extraction_container_never_started'] and provenance['extraction_container_removed']
    assert provenance['files'] == ui_hashes, 'Frontend does not match its reviewed immutable artifact'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', provenance['image_id']), 'Immutable frontend image required'
    result = {'success': False, 'scope': __doc__.strip(), 'ui_artifact_hashes': ui_hashes,
        'ui_image_id': provenance['image_id'], 'ui_provenance_sha256': hashlib.sha256(provenance_bytes).hexdigest(),
        'go_source_root': str(options.go_source_root), 'go_source_hashes': fingerprints(options.go_source_root)}
    report = output/'results.json'; report.write_text(json.dumps(result, indent=2)+'\n')
    postgres = shutil.which('postgres') or '/opt/homebrew/opt/postgresql@16/bin/postgres'
    assert Path(postgres).is_file() and shutil.which('redis-server'), 'Private PostgreSQL and Redis required'
    binaries = Path(postgres).parent
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'PLAYWRIGHT_BROWSERS_PATH')}
    tool_env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-profile-browser-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); started = False
        def command(args): return subprocess.run([str(x) for x in args], env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            command([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            started = True
            command([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            command([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            key = Ed25519PrivateKey.generate()
            import environ
            os.environ.clear(); os.environ.update(tool_env); environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
            os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
                JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE=DATABASE, TESTS_WORKDIR=str(work),
                DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432', DBNAME=DATABASE, DBUSER='contracts', DBPASSWORD='test-only',
                USE_PGBOUNCER='False', STORE_ON_DISK='True',
                REDIS_HOST='127.0.0.1', REDIS_PORT=str(options.cache_port), REDIS_PASSWORD='private-profile-browser-cache',
                REDIS_USERNAME='', REDIS_SSLAUTH='')
            import django
            with patch('environ.Env.read_env'): django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
            call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=log, stderr=log)
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            result['versions'] = exercise(work, output, log, options, tool_env)
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    if started:
                        stopped = subprocess.run([str(binaries/'pg_ctl'), '-D', str(work/'postgres'), '-m', 'immediate', '-w', 'stop'], stdout=log, stderr=subprocess.STDOUT, timeout=30)
                        assert stopped.returncode == 0 or not (work/'postgres/postmaster.pid').exists(), 'Private PostgreSQL cleanup failed'
                finally: report.write_text(json.dumps(result, indent=2)+'\n')
    assert fingerprints(options.ui_dir) == result['ui_artifact_hashes'], 'Built frontend changed during proof'
    assert fingerprints(options.go_source_root) == result['go_source_hashes'], 'Selected Go source changed during proof'
    result.update(success=True, private_services_stopped=True, current_model_schema_only=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
            'TESTS/adapters/django/profile_browser.py', 'TESTS/browser/profile-personal.mjs')})
    report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'success': True, 'versions': list(result['versions']), 'private_services_stopped': True}))


if __name__ == '__main__':
    main()
