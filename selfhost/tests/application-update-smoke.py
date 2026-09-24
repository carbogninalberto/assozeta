"""Real disposable application upgrade using test-only release/registry transport.

Prerequisites: assozeta-{backend,web,updater}:goal-test and assozeta-renderer:test.
Pass --legacy to install the immutable v1.0.2 source first, bootstrap the feature,
then exercise a subsequent owner-authorized update. No production install is used.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import read_env, update_env
from distribution import digest, snapshot_managed
from quality_support import docker_host, write_evidence, assert_project_removed


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run(legacy=False, browser=False, operations_only=False):
    host = docker_host()
    directory = Path(tempfile.mkdtemp(prefix='assozeta-application-update-')).resolve()
    installation = directory / 'selfhost'
    fixtures = installation / '.fixtures'
    project = f'assozeta-update-test-{uuid4().hex[:10]}'
    image_tags = []
    log = directory / 'execution.log'
    log.touch(mode=0o600)
    env_file = installation / '.env'
    environment = {**os.environ, 'ASSOZETA_INSTALL_ROOT': str(installation),
                   'ASSOZETA_SELFHOST_DIR': str(installation), 'ASSOZETA_ENV_FILE': str(env_file),
                   'ASSOZETA_UPDATER_API_VOLUME': f'{project}_updater_api', 'ASSOZETA_UPDATER_STATUS_VOLUME': f'{project}_updater_status'}
    success = False
    competing_backup = None

    def command(args, **kwargs):
        with log.open('a') as output:
            return subprocess.run(args, check=True, stdout=output, stderr=output, **kwargs)

    def capture(args, **kwargs):
        return subprocess.check_output(args, text=True, **kwargs).strip()

    def compose(*args):
        return ['docker', 'compose', '--env-file', str(env_file), '-f', str(installation / 'compose.yml'), *args]

    def build(dockerfile, tag):
        (directory / 'Dockerfile').write_text(dockerfile)
        command(['docker', 'build', '-t', tag, str(directory)])
        image_tags.append(tag)
        return capture(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])

    try:
        shutil.copytree(ROOT / 'selfhost', installation, ignore=shutil.ignore_patterns(
            '.env', '.env.dev', '.updater', '.lifecycle*', '.restore-*', 'backups', '__pycache__', 'node_modules', 'test-results'))
        fixtures.mkdir()
        shutil.copytree(ROOT / 'selfhost/tests/fixtures', directory / 'transport')
        # Add fixture transport data mounts only to this disposable distribution.
        composition = (installation / 'compose.yml').read_text().replace(
            '    - django_static:/app/staticfiles\n',
            f'    - django_static:/app/staticfiles\n    - {fixtures}:/fixtures:rw\n', 1)
        composition = composition.replace('      - updater_api:/run/assozeta-updater:ro\n',
            f'      - updater_api:/run/assozeta-updater:ro\n      - {fixtures}:/fixtures:ro\n')
        (installation / 'compose.yml').write_text(composition)
        build(f'''FROM assozeta-updater:goal-test
COPY transport /test-transport
RUN mv /usr/local/bin/docker /usr/local/bin/docker-real && cp /test-transport/docker /usr/local/bin/docker && chmod +x /usr/local/bin/docker
ENV PYTHONPATH=/test-transport:/runner ASSOZETA_TEST_FIXTURES={fixtures} ASSOZETA_READINESS_ATTEMPTS=5 ASSOZETA_READINESS_DELAY=1
ENTRYPOINT ["python3", "/test-transport/entrypoint.py"]
''', f'{project}-updater:fixture')
        updaters = {number: build(f'FROM {project}-updater:fixture\nENV RUNNER_VERSION=2.0.{number}\n', f'{project}-updater:2.0.{number}')
                    for number in (1, 2, 3, 4)}
        updater = updaters[1]
        images = {f'ghcr.io/carbogninalberto/assozeta-updater:2.0.{number}': updaters[number] for number in (1, 2, 3, 4)}
        backend = {}
        for number in (1, 2, 3, 4):
            backend[number] = build(f'''FROM assozeta-backend:goal-test
USER root
COPY transport /test-transport
RUN printf '2.0.{number}\\n' > /app/VERSION
ENV PYTHONPATH=/test-transport:/app ASSOZETA_TEST_FIXTURES=/fixtures
USER assozeta
''', f'{project}-backend:2.0.{number}')
            images[f'fixture-backend-{number}'] = backend[number]
        web = capture(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'assozeta-web:goal-test'])
        renderer = capture(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'assozeta-renderer:test'])
        images.update({'fixture-web': web, 'fixture-renderer': renderer})
        (fixtures / 'images.json').write_text(json.dumps(images))
        (fixtures / 'fail-migration.py').write_text('''
import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django
django.setup()
from django.db import connection
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from instance.models import InstanceConfiguration
with connection.cursor() as cursor:
    cursor.execute('ALTER TABLE upgrade_sentinel ADD COLUMN failed_release integer DEFAULT 3')
    cursor.execute("UPDATE upgrade_sentinel SET value = 'changed-by-failed-migration'")
InstanceConfiguration.objects.update(name='Changed by failed migration')
default_storage.delete('instance/logo.png')
default_storage.save('instance/logo.png', ContentFile(b'changed-by-failed-migration'))
from pathlib import Path
import time
fixtures = Path('/fixtures')
if (fixtures / 'hold-migration').exists():
    (fixtures / 'migration-blocked').write_text('Partial migration is still running')
    while (fixtures / 'hold-migration').exists():
        time.sleep(0.1)
sys.exit(42)
''')
        port = free_port()
        cli = str(installation / 'bin/assozeta')
        command([cli, 'configure', '--domain', f'http://{host}:{port}', '--email', 'owner@example.invalid', '--version', '2.0.1'], env=environment)
        update_env(env_file, {'COMPOSE_PROJECT_NAME': project, 'HTTPS_PORT': str(free_port()),
            'ASSOZETA_BACKEND_REF': backend[1], 'ASSOZETA_WEB_REF': web, 'ASSOZETA_RENDERER_REF': renderer,
            'ASSOZETA_UPDATER_REF': updater, 'CUSTOM_SETTING': 'preserved-through-upgrade'})

        # Publish two local release archives, one healthy and one failing migration.
        managed = snapshot_managed(installation)
        catalog = []
        for number in (1, 2) if legacy else (1, 2, 3, 4):
            tag = f'v2.0.{number}'
            destination = fixtures / tag
            destination.mkdir()
            files = {name: (installation / name).read_bytes() for name in managed}
            if number == 3:
                files['compose.yml'] = files['compose.yml'].replace(b'python manage.py migrate --noinput &&', b'python /fixtures/fail-migration.py &&')
            if number == 4:
                files['caddy/Caddyfile'] = files['caddy/Caddyfile'].replace(b'respond 200', b'respond 503')
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                for name, content in files.items():
                    member = tarfile.TarInfo(f'selfhost/{name}')
                    member.size = len(content)
                    member.mode = (installation / name).stat().st_mode & 0o777
                    archive.addfile(member, io.BytesIO(content))
            bundle = stream.getvalue()
            bundle_name = f'assozeta-selfhost-{tag}.tar.gz'
            (destination / bundle_name).write_bytes(bundle)
            metadata = {'schema_version': 1, 'version': f'2.0.{number}', 'bundle_sha256': digest(bundle),
                'images': {'backend': backend[number], 'web': web, 'renderer': renderer, 'updater': updaters[number]},
                'files': {name: digest(content) for name, content in files.items()}}
            (destination / 'assozeta-update.json').write_text(json.dumps(metadata))
            catalog.append({'id': number, 'tag_name': tag, 'name': tag, 'published_at': '2026-09-01T00:00:00Z',
                'body': f'Complete fixture release {number}\n' * 1000, 'draft': False, 'prerelease': False,
                'assets': [{'name': name, 'state': 'uploaded', 'size': (destination / name).stat().st_size}
                           for name in (bundle_name, 'assozeta-update.json')]})
        (fixtures / 'releases.json').write_text(json.dumps(catalog))
        helper = ['docker', 'run', '--rm', '-v', f'{installation}:{installation}',
                  '-v', '/var/run/docker.sock:/var/run/docker.sock', updater]
        legacy_files = None
        if legacy:
            # Registry availability is external to the old release. Preload the
            # same pinned storage versions without changing its files or config.
            command([str(ROOT / 'selfhost/bin/prepare-storage'), '--legacy'])
            # Preserve the actual old distribution; the test registry adapter
            # selects local images without editing its Compose or image settings.
            source = directory / 'legacy-source'
            source.mkdir()
            content = subprocess.check_output(['git', 'archive', 'v1.0.2', 'BE', 'UI', 'selfhost'], cwd=ROOT)
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                archive.extractall(source, filter='data')
            for name in ('BE', 'UI'):
                tag = f'{project}-legacy-{name.lower()}:1.0.2'
                command(['docker', 'build', '--build-arg', 'VERSION=1.0.2', '-t', tag, str(source / name)])
                image_tags.append(tag)
            legacy_files = snapshot_managed(source / 'selfhost')
            for name in managed:
                (installation / name).unlink(missing_ok=True)
            shutil.copytree(source / 'selfhost', installation, dirs_exist_ok=True)
            env_file.unlink()
            command([cli, 'configure', '--domain', f'http://{host}:{port}',
                     '--email', 'owner@example.invalid', '--version', '1.0.2'], env=environment)
            renderer_tag = f'{project}-legacy-renderer:1.0.2'
            command(['docker', 'tag', renderer, renderer_tag])
            image_tags.append(renderer_tag)
            update_env(env_file, {'COMPOSE_PROJECT_NAME': project, 'HTTPS_PORT': str(free_port()),
                'CUSTOM_SETTING': 'preserved-through-upgrade'})
            (fixtures / 'legacy-images.json').write_text(json.dumps({'services': {
                **{service: {'image': f'{project}-legacy-be:1.0.2'} for service in ('migrate', 'api', 'worker', 'beat')},
                'web': {'image': f'{project}-legacy-ui:1.0.2'}, 'renderer': {'image': renderer_tag},
            }}))
            # The baseline transport serves the exact released files, without
            # adding a runner or updater configuration to the source install.
            destination = fixtures / 'v1.0.2'
            destination.mkdir()
            with tarfile.open(destination / 'assozeta-selfhost-v1.0.2.tar.gz', 'w:gz') as archive:
                for name in legacy_files:
                    archive.add(source / 'selfhost' / name, arcname=f'selfhost/{name}')
            catalog.append({'id': 102, 'tag_name': 'v1.0.2', 'draft': False, 'prerelease': False,
                            'published_at': '2026-01-01T00:00:00Z', 'body': 'Legacy release', 'assets': []})
            (fixtures / 'releases.json').write_text(json.dumps(catalog))
            assert snapshot_managed(installation) == legacy_files
            print('Installing released v1.0.2 CLI, Compose, backend and web without updater configuration.', flush=True)
        else:
            print('Installing disposable application with automatic runner provisioning.', flush=True)
        command(helper + ['-c', 'import subprocess,sys; subprocess.run([sys.argv[1],"install"],check=True)', cli])

        seed = '''
from application.models import User, SportAssociation
from instance.models import InstanceConfiguration
from rest_framework_simplejwt.tokens import RefreshToken
user = User.objects.create_user(username='upgrade-owner', email='owner@example.invalid', role=User.ASSOCIATION)
association = SportAssociation.objects.create(user=user, denomination='Preserved Club')
InstanceConfiguration.objects.create(domain='host.docker.internal', name='Preserved Club', abbreviation='PC', primary_association=association, setup_provenance='import')
import json
owner_refresh = RefreshToken.for_user(user)
owner_access = str(owner_refresh.access_token)
refresh_tokens = {owner_access: str(owner_refresh)}
print('OWNER_TOKEN=' + owner_access)
collaborator = User.objects.create_user(username='upgrade-collaborator', role=User.COLLABORATOR, connected_user=user)
other = User.objects.create_user(username='upgrade-other', role=User.ASSOCIATION)
superuser = User.objects.create_user(username='upgrade-superuser', role=User.ASSOCIATION, is_superuser=True)
for denied in (collaborator, other, superuser):
    refresh = RefreshToken.for_user(denied)
    access = str(refresh.access_token)
    refresh_tokens[access] = str(refresh)
    print('DENIED_TOKEN=' + access)
print('BROWSER_REFRESH=' + json.dumps(refresh_tokens))
print('OWNER_ID=' + str(user.pk))
'''
        seeded = capture(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', seed.replace('host.docker.internal', host)), env=environment)
        token = next(line.removeprefix('OWNER_TOKEN=') for line in seeded.splitlines() if line.startswith('OWNER_TOKEN='))
        denied_tokens = [line.removeprefix('DENIED_TOKEN=') for line in seeded.splitlines() if line.startswith('DENIED_TOKEN=')]
        refresh_tokens = json.loads(next(line.removeprefix('BROWSER_REFRESH=') for line in seeded.splitlines() if line.startswith('BROWSER_REFRESH=')))
        owner_id = next(line.removeprefix('OWNER_ID=') for line in seeded.splitlines() if line.startswith('OWNER_ID='))
        if legacy:
            assert not (installation / '.updater').exists()
            assert snapshot_managed(installation) == legacy_files
            assert not any(key.startswith('ASSOZETA_UPDATER') for key in read_env(env_file))
            for volume in (f'{project}_updater_api', f'{project}_updater_status'):
                assert not capture(['docker', 'volume', 'ls', '-q', '--filter', f'name=^{volume}$'])
            assert not capture(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}-updater'])
            legacy_volumes = capture(['docker', 'volume', 'inspect', f'{project}_postgres_data', f'{project}_minio_data'])
            # Seed data and object storage through the actual legacy backend.
            command(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', '''
from django.db import connection
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from instance.models import InstanceConfiguration
with connection.cursor() as cursor:
    cursor.execute("CREATE TABLE legacy_sentinel (value text)")
    cursor.execute("INSERT INTO legacy_sentinel VALUES ('preserved-from-v1.0.2')")
path = default_storage.save('instance/legacy-logo.png', ContentFile(b'legacy-logo-content'))
InstanceConfiguration.objects.update(logo_path=path)
'''), env=environment)
            legacy_environment = read_env(env_file)
            bootstrap = fixtures / 'bootstrap-update'
            shutil.copy(ROOT / 'selfhost/bin/update', bootstrap)
            print('Running the one-time bootstrap against unchanged legacy deployment files.', flush=True)
            command(helper + ['-c', 'import subprocess,sys; subprocess.run(sys.argv[1:],check=True)',
                              'sh', str(bootstrap), '--directory', str(installation), '--version', '2.0.1'])
            assert json.loads((installation / '.updater/provisioning.json').read_text())['schema_version'] == 2
            current_environment = read_env(env_file)
            for key, value in legacy_environment.items():
                if key != 'ASSOZETA_VERSION':
                    assert current_environment[key] == value, f'Legacy environment changed: {key}'
            command(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', '''
from django.db import connection
from django.core.files.storage import default_storage
from instance.models import InstanceConfiguration
with connection.cursor() as cursor:
    cursor.execute('SELECT value FROM legacy_sentinel')
    assert cursor.fetchone()[0] == 'preserved-from-v1.0.2'
instance = InstanceConfiguration.objects.get()
assert instance.name == 'Preserved Club'
with default_storage.open(instance.logo_path, 'rb') as logo:
    assert logo.read() == b'legacy-logo-content'
'''), env=environment)
            fingerprints = helper[:-1] + ['-v', f'{project}_updater_api:/run/assozeta-updater:ro', helper[-1],
                '-c', "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/run/assozeta-updater/token').read_bytes()).hexdigest())"]
            before = capture(fingerprints)
            command(helper + ['/runner/provision.py', str(installation), str(env_file)])
            assert capture(fingerprints) == before
            assert read_env(env_file) == current_environment
            assert capture(['docker', 'volume', 'inspect', f'{project}_postgres_data', f'{project}_minio_data']) == legacy_volumes
            assert len(capture(['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}-updater']).splitlines()) == 1
            print('Legacy bootstrap provisioned one runner; data, branding, configuration and repeated-provisioning credentials preserved.', flush=True)

        def independent_status(bearer=token, expected=200):
            request = Request(f'http://127.0.0.1:{port}/instance-update-status', data=b'{}',
                headers={'Host': f'{host}:{port}', 'Authorization': f'Bearer {bearer}',
                         'Content-Type': 'application/json', 'USER_ID': owner_id})
            try:
                with urlopen(request, timeout=20) as response:
                    assert response.status == expected
                    assert response.headers['Cache-Control'] == 'no-store'
                    return json.load(response)
            except HTTPError as error:
                assert error.code == expected, f'Unexpected status response: {error.code}'
                assert 'history' not in error.read().decode()

        def assert_independent_access():
            assert independent_status()['is_owner'] is True
            for denied in ['', 'invalid-token', *denied_tokens]:
                independent_status(denied, expected=403)

        def assert_static_web():
            request = Request(f'http://127.0.0.1:{port}/', headers={'Host': f'{host}:{port}'})
            with urlopen(request, timeout=10) as response:
                assert response.status == 200 and b'<html' in response.read().lower()

        def api(path, body=None):
            request = Request(f'http://127.0.0.1:{port}/api/instance/{path}',
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Host': f'{host}:{port}', 'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
            with urlopen(request, timeout=10) as response:
                return json.load(response)

        def runner_status(payload=None):
            code = '''
import http.client,json,pathlib,socket,sys
c=http.client.HTTPConnection('localhost',timeout=5)
c.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
c.sock.connect('/run/assozeta-updater/runner.sock')
c.request('POST' if len(sys.argv)>1 else 'GET','/updates' if len(sys.argv)>1 else '/status',body=sys.argv[1] if len(sys.argv)>1 else None,headers={'Authorization':'Bearer '+pathlib.Path('/run/assozeta-updater/token').read_text().strip()})
response=c.getresponse()
print(json.dumps({'http_status':response.status,**json.loads(response.read())}))
'''
            return json.loads(capture(['docker', 'run', '--rm', '-v', f'{project}_updater_api:/run/assozeta-updater:ro', updater, '-c', code,
                                      *([json.dumps(payload)] if payload else [])], stderr=subprocess.PIPE))

        def kill_and_restart_runner():
            container = capture(['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}-updater'])
            assert container and '\n' not in container
            command(['docker', 'kill', '--signal', 'KILL', container])
            command(['docker', 'start', container])
            return await_runner_version('2.0.2')

        def wait_marker(name):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if (fixtures / name).exists() and (fixtures / name).stat().st_size:
                    return
                time.sleep(0.2)
            raise AssertionError(f'Update did not reach {name}')

        def assert_interrupted_gate(operation):
            status = runner_status()
            assert not status['can_update'] and status['active'] is None
            interrupted = next(item for item in status['history'] if item['id'] == operation['id'])
            assert interrupted['status'] == 'recovery_required' and interrupted['interrupted_at']
            duplicate = {key: operation[key] for key in ('release_id', 'tag', 'request_id', 'actor_id', 'source_version')}
            assert runner_status(duplicate)['operation']['id'] == operation['id']
            assert runner_status({**duplicate, 'request_id': str(uuid4())})['http_status'] == 409
            return interrupted

        def await_operation(operation_id):
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                try:
                    status = runner_status()
                except subprocess.CalledProcessError:
                    time.sleep(1)
                    continue
                for record in ([status['active']] if status['active'] else []) + status['history']:
                    if record['id'] == operation_id and record['status'] not in ('queued', 'running'):
                        return record
                time.sleep(3)
            raise AssertionError('Update did not reach a terminal state within 15 minutes')

        def await_runner_version(version):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    status = runner_status()
                    if status['available'] and status['runner_version'] == version:
                        return status
                except subprocess.CalledProcessError:
                    pass
                time.sleep(1)
            raise AssertionError('Replacement runner did not become ready')

        assert api('access')['is_owner']
        assert api('admin/updates')['available']
        if legacy:
            releases = api('admin/releases')
            assert releases['latest']['tag'] == 'v2.0.2' and releases['latest']['artifacts_ready']
            assert len(releases['latest']['notes']) > 20000
        assert_independent_access()
        def png_chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        logo = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!IIBBBBB', 3, 2, 8, 2, 0, 0, 0))
        logo += png_chunk(b'IDAT', zlib.compress((b'\x00' + b'\x31\x25\x94' * 3) * 2)) + png_chunk(b'IEND', b'')
        boundary = f'fixture-{uuid4().hex}'
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.png"\r\n'
                     'Content-Type: image/png\r\n\r\n').encode() + logo + f'\r\n--{boundary}--\r\n'.encode()
        upload = Request(f'http://127.0.0.1:{port}/api/instance/admin/logo', data=multipart,
            headers={'Host': f'{host}:{port}', 'Authorization': f'Bearer {token}',
                     'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urlopen(upload, timeout=15) as response:
            logo_url = json.load(response)['logo_url']

        def assert_logo():
            request = Request(f'http://127.0.0.1:{port}{logo_url}', headers={'Host': f'{host}:{port}'})
            with urlopen(request, timeout=15) as response:
                assert response.read() == logo
        assert_logo()
        browser_input = fixtures / 'browser-input.json'
        browser_operation = fixtures / 'browser-operation.json'
        browser_logo = fixtures / 'browser-logo.png'
        browser_logo.write_bytes(logo)
        browser_input.write_text(json.dumps({'origin': f'http://{host}:{port}', 'requestOrigin': f'http://127.0.0.1:{port}' if host == 'host.docker.internal' else f'http://{host}:{port}', 'host': f'{host}:{port}',
            'token': token, 'refreshTokens': refresh_tokens, 'deniedTokens': denied_tokens, 'logo': str(browser_logo), 'operation': str(browser_operation)}))
        browser_input.chmod(0o600)

        def browser_check(phase):
            if not browser:
                return
            output = Path(os.environ.get('ASSOZETA_QUALITY_REPORT_DIR', str(directory / 'reports'))) / ('legacy-browser' if legacy else 'browser') / phase
            output.mkdir(parents=True, exist_ok=True)
            command(['npm', 'test', '--prefix', str(ROOT / 'selfhost/tests/browser')], env={**os.environ,
                'ASSOZETA_BROWSER_INPUT': str(browser_input), 'ASSOZETA_BROWSER_PHASE': phase,
                'ASSOZETA_BROWSER_OUTPUT': str(output.resolve())})

        if browser:
            # Present one available upgrade to the real UI; later controlled
            # failures are published only after this browser-driven success.
            (fixtures / 'releases.json').write_text(json.dumps([release for release in catalog if release['id'] in (1, 2, 102)]))
            browser_check('branding')
            browser_check('operations')
            if operations_only:
                success = True
                print('PASS: real owner settings, saved email configuration, all ten diagnostics, service evidence, result persistence, and non-owner rejection on desktop/mobile.', flush=True)
                return
        original_env = read_env(env_file)
        database = ['exec', '-T', 'postgres', 'psql', '-U', original_env['DBUSER'], '-d', original_env['DBNAME'], '-At', '-c']
        command(compose(*database, "CREATE TABLE upgrade_sentinel (value text); INSERT INTO upgrade_sentinel VALUES ('preserved');"), env=environment)
        print('Starting owner-authorized update to fixture 2.0.2.', flush=True)
        existing_archives = set((installation / 'backups').glob('*.tar.gz'))
        (fixtures / 'hold-backup').touch()
        payload = {'release_id': 2, 'tag': 'v2.0.2', 'request_id': str(uuid4())}
        if browser:
            browser_check('start')
            operation = json.loads(browser_operation.read_text())
            payload = {key: operation[key] for key in ('release_id', 'tag', 'request_id')}
        else:
            operation = api('admin/updates', payload)['operation']
        deadline = time.monotonic() + 60
        while not (fixtures / 'backup-blocked').exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert (fixtures / 'backup-blocked').exists(), 'Update did not reach its database backup'
        assert not capture(compose('ps', '--status', 'running', '--services', 'api'), env=environment)
        assert_static_web()
        assert_independent_access()
        assert independent_status()['active']['id'] == operation['id']
        with log.open('a') as output:
            competing_helper = helper[:-1] + ['--name', f'{project}-competing-backup', helper[-1]]
            competing_backup = subprocess.Popen(competing_helper + ['-c', 'import subprocess,sys; subprocess.run([sys.argv[1],"backup"],check=True)', cli],
                                                 stdout=output, stderr=output)
        time.sleep(3)
        assert competing_backup.poll() is None, 'Competing CLI backup should wait for the update lock'
        assert (installation / '.lifecycle.lock/command').read_text().strip() == 'upgrade'
        assert runner_status()['active']['stage'] == 'backup'
        assert set((installation / 'backups').glob('*.tar.gz')) == existing_archives
        browser_check('during')
        (fixtures / 'hold-backup').unlink()
        result = await_operation(operation['id'])
        assert result['status'] == 'succeeded', result
        assert result['verified_at']
        assert competing_backup.wait(timeout=180) == 0
        competing_backup = None
        await_runner_version('2.0.2')
        retried = api('admin/updates', payload)['operation']
        assert retried['id'] == operation['id'] and retried['status'] == 'succeeded'
        assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
        assert api('admin')['config']['oem']['name'] == 'Preserved Club'
        assert_logo()
        assert capture(compose(*database, 'SELECT value FROM upgrade_sentinel'), env=environment) == 'preserved'
        upgraded_env = read_env(env_file)
        for name in ('SECRET_KEY', 'DBPASSWORD', 'JWT_SECRET_KEY_B64', 'CUSTOM_SETTING'):
            assert original_env[name] == upgraded_env[name], f'Configuration changed: {name}'
        archives = sorted((installation / 'backups').glob('*.tar.gz'))
        assert archives
        with tarfile.open(archives[-1]) as backup:
            stored_logo = next(member for member in backup if member.name.endswith('/instance/logo.png'))
            assert backup.extractfile(stored_logo).read() == logo
        if legacy:
            browser_check('completed')
            assert capture(fingerprints) == before
            assert capture(['docker', 'volume', 'inspect', f'{project}_postgres_data', f'{project}_minio_data']) == legacy_volumes
            assert capture(compose(*database, 'SELECT value FROM legacy_sentinel'), env=environment) == 'preserved-from-v1.0.2'
            success = True
            print('PASS: released v1.0.2 installation → automatic bootstrap to 2.0.1 → owner API update to 2.0.2; preserved legacy database/object data, branding, environment, credentials and volumes, competing CLI lock and durable retries.', flush=True)
            return
        browser_check('completed')
        (fixtures / 'releases.json').write_text(json.dumps(catalog))
        prior_failures = {}
        for failure, stage in (('fail-backup', 'backup'), ('fail-download', 'downloading')):
            print(f'Testing controlled {stage} failure.', flush=True)
            (fixtures / failure).touch()
            failed = api('admin/updates', {'release_id': 3, 'tag': 'v2.0.3', 'request_id': str(uuid4())})['operation']
            failed = await_operation(failed['id'])
            assert failed['status'] == 'failed' and failed['failed_stage'] == stage, failed
            prior_failures[failed['id']] = stage
            assert read_env(env_file) == upgraded_env
            assert not (installation / '.updater/pending-distribution').exists()
            assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
            assert_logo()
        print('Starting controlled migration failure to fixture 2.0.3.', flush=True)
        operation = api('admin/updates', {'release_id': 3, 'tag': 'v2.0.3', 'request_id': str(uuid4())})['operation']
        result = await_operation(operation['id'])
        assert result['status'] == 'recovery_required', result
        assert result['recovery']
        assert (installation / '.updater/pending-distribution/transaction.json').exists()
        assert capture(compose(*database, 'SELECT value FROM upgrade_sentinel'), env=environment) == 'changed-by-failed-migration'
        assert not runner_status()['can_update']
        assert_static_web()
        assert_independent_access()
        independent = independent_status()
        assert not independent['can_update']
        failure = next(item for item in independent['history'] if item['id'] == operation['id'])
        assert failure['status'] == 'recovery_required' and failure['recovery']
        browser_check('recovery')
        # Authorization observes current ownership, even with Django offline.
        command(compose(*database, "UPDATE bakney_user SET is_active = FALSE WHERE username = 'upgrade-owner'"), env=environment)
        independent_status(expected=403)
        command(compose(*database, "UPDATE bakney_user SET is_active = TRUE, is_superuser = TRUE WHERE username = 'upgrade-owner'"), env=environment)
        assert independent_status()['is_owner']
        command(compose(*database, "UPDATE bakney_user SET is_superuser = FALSE WHERE username = 'upgrade-owner'"), env=environment)
        command(compose(*database, "UPDATE instance_instanceconfiguration SET self_hosted = FALSE"), env=environment)
        independent_status(expected=403)
        command(compose(*database, "UPDATE instance_instanceconfiguration SET self_hosted = TRUE"), env=environment)
        transaction = json.loads((installation / '.updater/pending-distribution/transaction.json').read_text())
        assert transaction['operation_id'] == operation['id']
        recovery_archive = transaction['backup']['path']
        print('Recovering the previous distribution, database schema, object data and configuration.', flush=True)
        recover = helper + ['-c', 'import subprocess,sys; subprocess.run([sys.argv[1],"recover-upgrade",sys.argv[2],"--yes"],check=True)', cli]
        # A different backup must fail before replacing the failed distribution.
        with log.open('a') as output:
            rejected = subprocess.run(recover + [str(archives[0])], stdout=output, stderr=output)
        assert rejected.returncode != 0
        assert read_env(env_file)['ASSOZETA_VERSION'] == '2.0.3'
        # Fail the first data-restore attempt after the source files are back.
        (fixtures / 'fail-restore').touch()
        with log.open('a') as output:
            failed_restore = subprocess.run(recover + [recovery_archive], stdout=output, stderr=output)
        assert failed_restore.returncode != 0
        assert not capture(compose('ps', '--status', 'running', '--services', 'api', 'worker', 'beat'), env=environment)
        assert not runner_status()['can_update']
        assert_static_web()
        assert independent_status()['is_owner']
        assert capture(compose(*database, 'SELECT value FROM upgrade_sentinel'), env=environment) == 'changed-by-failed-migration'
        resume = installation / '.updater/recover-upgrade'
        assert resume.exists()
        command(helper + ['-c', 'import subprocess,sys; subprocess.run(sys.argv[1:],check=True)', str(resume), recovery_archive, '--yes'])
        assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
        assert api('admin')['config']['oem']['name'] == 'Preserved Club'
        assert_logo()
        assert capture(compose(*database, 'SELECT value FROM upgrade_sentinel'), env=environment) == 'preserved'
        assert capture(compose(*database, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'upgrade_sentinel' AND column_name = 'failed_release'"), env=environment) == '0'
        assert read_env(env_file) == upgraded_env
        assert not (installation / '.updater/pending-distribution').exists()
        status = api('admin/updates')
        assert status['can_update']
        assert next(item for item in status['history'] if item['id'] == operation['id'])['status'] == 'recovered'
        for operation_id, stage in prior_failures.items():
            previous = next(item for item in status['history'] if item['id'] == operation_id)
            assert previous['status'] == 'failed' and previous['failed_stage'] == stage
        print('Testing public health-check failure and recovery.', flush=True)
        unhealthy = api('admin/updates', {'release_id': 4, 'tag': 'v2.0.4', 'request_id': str(uuid4())})['operation']
        unhealthy = await_operation(unhealthy['id'])
        assert unhealthy['status'] == 'recovery_required' and unhealthy['failed_stage'] == 'health_check', unhealthy
        transaction = json.loads((installation / '.updater/pending-distribution/transaction.json').read_text())
        command(recover + [transaction['backup']['path']])
        assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
        assert read_env(env_file) == upgraded_env
        assert_logo()

        print('Testing abrupt runner interruption with a surviving partial migration.', flush=True)
        (fixtures / 'migration-blocked').write_text('')
        (fixtures / 'migration-blocked').chmod(0o666)
        (fixtures / 'hold-migration').touch()
        interrupted = api('admin/updates', {'release_id': 3, 'tag': 'v2.0.3', 'request_id': str(uuid4())})['operation']
        wait_marker('migration-blocked')
        kill_and_restart_runner()
        assert assert_interrupted_gate(interrupted)['failed_stage'] == 'migrating'
        assert not independent_status()['can_update']
        transaction = json.loads((installation / '.updater/pending-distribution/transaction.json').read_text())
        running_migrations = ['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}',
                              '--filter', 'label=com.docker.compose.service=migrate', '--filter', 'label=com.docker.compose.oneoff=True']
        assert capture(running_migrations), 'The migration must survive termination of the runner'
        with log.open('a') as output:
            rejected = subprocess.run(recover + [transaction['backup']['path']], stdout=output, stderr=output)
        assert rejected.returncode != 0, 'Recovery must refuse an overlapping migration'
        assert read_env(env_file)['ASSOZETA_VERSION'] == '2.0.3'
        (fixtures / 'hold-migration').unlink()
        deadline = time.monotonic() + 60
        while capture(running_migrations) and time.monotonic() < deadline:
            time.sleep(1)
        assert not capture(running_migrations)
        command(recover + [transaction['backup']['path']])
        assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
        assert runner_status()['can_update']
        assert next(item for item in runner_status()['history'] if item['id'] == interrupted['id'])['status'] == 'recovered'
        assert_logo()

        print('Testing abrupt runner interruption before the recovery snapshot exists.', flush=True)
        (fixtures / 'backup-blocked').unlink(missing_ok=True)
        (fixtures / 'hold-backup').touch()
        interrupted = api('admin/updates', {'release_id': 3, 'tag': 'v2.0.3', 'request_id': str(uuid4())})['operation']
        wait_marker('backup-blocked')
        kill_and_restart_runner()
        assert assert_interrupted_gate(interrupted)['failed_stage'] == 'backup'
        assert not (installation / '.updater/pending-distribution').exists()
        (fixtures / 'hold-backup').unlink()
        reconcile = helper + ['-c', 'import subprocess,sys; subprocess.run([sys.argv[1],"reconcile-updates"],check=True)', cli]
        with log.open('a') as output:
            rejected = subprocess.run(reconcile, stdout=output, stderr=output)
        assert rejected.returncode != 0, 'Stopped services must not pass reconciliation'
        assert not runner_status()['can_update']
        command(helper + ['-c', 'import subprocess,sys; subprocess.run([sys.argv[1],"start"],check=True)', cli])
        command(reconcile)
        assert runner_status()['can_update']
        assert next(item for item in runner_status()['history'] if item['id'] == interrupted['id'])['status'] == 'recovered'
        assert api('admin')['running_version'].removeprefix('v') == '2.0.2'
        assert read_env(env_file) == upgraded_env
        assert_logo()
        success = True
        print('PASS: automatic provisioning, owner API update, competing CLI backup serialization, verified 2.0.2, runner replacement, preserved data/branding/secrets, backup/download/partial-migration/health failures, rejected mismatched backup, safely failed data restore, resumed recovery, abrupt runner interruption during migration and backup, overlap prevention, idempotent retries, and verified source reconciliation.', flush=True)
    finally:
        (fixtures / 'hold-backup').unlink(missing_ok=True)
        (fixtures / 'hold-migration').unlink(missing_ok=True)
        if competing_backup is not None:
            try:
                competing_backup.wait(timeout=180)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker', 'rm', '--force', f'{project}-competing-backup'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                competing_backup.wait(timeout=30)
        if env_file.exists():
            for filename in ('compose.updater.yml', 'compose.yml'):
                with log.open('a') as output:
                    project_args = ['--project-name', f'{project}-updater'] if filename == 'compose.updater.yml' else []
                    subprocess.run(['docker', 'compose', '--env-file', str(env_file), '-f', str(installation / filename), *project_args, 'down', '--volumes', '--remove-orphans'],
                                   env=environment, stdout=output, stderr=output)
            subprocess.run(['docker', 'volume', 'rm', f'{project}_updater_api', f'{project}_updater_status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for tag in reversed(image_tags):
            subprocess.run(['docker', 'image', 'rm', tag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert_project_removed(project)
        if success:
            write_evidence('operations' if operations_only else 'legacy-upgrade' if legacy else 'update-recovery', {'images': {'backend': backend, 'web': web, 'renderer': renderer, 'updater': updaters}, 'browser': browser})
            shutil.rmtree(directory)
        else:
            print(f'Failure artifacts retained privately at {directory}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy', action='store_true', help='Verify v1.0.2 bootstrap and subsequent owner update')
    parser.add_argument('--browser', action='store_true', help='Run CI browser checks against the disposable instance')
    parser.add_argument('--operations-only', action='store_true', help='Verify real owner settings/diagnostics in a disposable app, then clean up without running upgrades')
    arguments = parser.parse_args()
    run(legacy=arguments.legacy, browser=arguments.browser or arguments.operations_only, operations_only=arguments.operations_only)
