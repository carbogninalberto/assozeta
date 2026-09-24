"""Rehearse a real published release on an isolated Docker installation.

No fixture transport, registry substitution, or production installation path is
accepted. This command creates and removes its own installation and volumes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import atomic_write, read_env, update_env
from distribution import digest, download, resolve_tag, unpack, validate_manifest
from release_catalog import RELEASES_URL, image_version, read_json, version_tuple
from quality_support import docker_host, write_evidence, assert_project_removed


def free_port():
    with socket.socket() as sock:
        sock.bind(('0.0.0.0', 0))
        return sock.getsockname()[1]


def rehearse(source, target, revision, browser=False):
    if version_tuple(source) is None or version_tuple(target) is None or version_tuple(source) >= version_tuple(target):
        raise ValueError('Source and target must be exact stable versions in ascending order')
    if len(revision) != 40 or any(char not in '0123456789abcdef' for char in revision):
        raise ValueError('The expected release commit must be a complete Git SHA')
    # Resolve and verify the real distribution before provisioning any services.
    source_tag, target_tag = resolve_tag(source), resolve_tag(target)
    manifest = validate_manifest(read_json(f'{RELEASES_URL}/download/{target_tag}/assozeta-update.json'), target_tag)
    bundle = download(f'{RELEASES_URL}/download/{target_tag}/assozeta-selfhost-{target_tag}.tar.gz')
    files = unpack(bundle)
    if digest(bundle) != manifest['bundle_sha256'] or {name: digest(value[0]) for name, value in files.items()} != manifest['files']:
        raise ValueError('The published bundle does not match its manifest')
    source_bundle = download(f'{RELEASES_URL}/download/{source_tag}/assozeta-selfhost-{source_tag}.tar.gz')
    directory = Path(tempfile.mkdtemp(prefix='assozeta-release-quality-')).resolve()
    installation = directory / 'selfhost'
    installation.mkdir()
    project = f'assozeta-release-quality-{uuid4().hex[:10]}'
    env_file = installation / '.env'
    environment = {
        **os.environ,
        'ASSOZETA_ENV_FILE': str(env_file),
        'ASSOZETA_SELFHOST_DIR': str(installation),
        # The updater Compose file declares these as required external-volume
        # variables. The lifecycle CLI normally exports them; this rehearsal
        # invokes Compose directly for its image-identity assertion and cleanup.
        'ASSOZETA_UPDATER_API_VOLUME': f'{project}_updater_api',
        'ASSOZETA_UPDATER_STATUS_VOLUME': f'{project}_updater_status',
    }
    log = directory / 'execution.log'
    log.touch(mode=0o600)
    host, port = docker_host(), free_port()
    origin = f'http://{host}:{port}'

    def command(args, capture=False):
        with log.open('a') as output:
            result = subprocess.run(args, env=environment, check=True, stdout=subprocess.PIPE if capture else output, stderr=output, text=True)
            return result.stdout.strip() if capture else None

    def compose(*args):
        return ['docker', 'compose', '--env-file', str(env_file), '-f', str(installation / 'compose.yml'), *args]

    evidence = None
    try:
        for name, (content, mode) in unpack(source_bundle).items():
            atomic_write(installation / name, content, mode)
        # Pull exactly the verified public image digests, and bind the rehearsal
        # to the source commit recorded by the image publishing workflow.
        image_ids = {}
        for name, reference in manifest['images'].items():
            command(['docker', 'pull', reference])
            metadata = json.loads(command(['docker', 'image', 'inspect', reference], capture=True))[0]
            if metadata['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != revision:
                raise ValueError(f'The published {name} image does not match the expected commit')
            image_ids[name] = metadata['Id']
        cli = str(installation / 'bin/assozeta')
        command([cli, 'configure', '--domain', origin, '--email', 'quality@example.invalid', '--version', image_version(source)])
        update_env(env_file, {'COMPOSE_PROJECT_NAME': project, 'HTTPS_PORT': str(free_port()), 'QUALITY_SENTINEL': 'preserve-me'})
        print('Installing the real source release in a disposable project.', flush=True)
        # Historical CLIs unconditionally repull immutable dependencies. Allow
        # the fork-built pinned storage already prepared by CI to remain in
        # cache without changing the released distribution or image references.
        legacy_storage = {
            'MINIO_IMAGE': 'quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z',
            'MINIO_CLIENT_IMAGE': 'quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z',
        }
        source_environment = read_env(env_file)
        if any(source_environment.get(key) == value for key, value in legacy_storage.items()):
            adapter = directory / 'source-install-tools'
            adapter.mkdir()
            shutil.copy2(ROOT / 'selfhost/tests/fixtures/cached-storage-docker', adapter / 'docker')
            environment['ASSOZETA_REHEARSAL_DOCKER'] = shutil.which('docker')
            original_path = environment['PATH']
            environment['PATH'] = str(adapter) + os.pathsep + original_path
            try:
                command([cli, 'install'])
            finally:
                environment['PATH'] = original_path
                environment.pop('ASSOZETA_REHEARSAL_DOCKER', None)
        else:
            command([cli, 'install'])
        seed = '''
from django.db import connection
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from application.models import User, SportAssociation
from instance.models import InstanceConfiguration
from rest_framework_simplejwt.tokens import RefreshToken
user = User.objects.create_user(username='release-quality-owner', role=User.ASSOCIATION)
association = SportAssociation.objects.create(user=user, denomination='Release Quality Sentinel')
logo = default_storage.save('instance/quality-logo.png', ContentFile(__import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAEElEQVR4nGMwVJ0CQQxwFgA0mgV9TDUSVgAAAABJRU5ErkJggg==')))
InstanceConfiguration.objects.create(domain='quality.invalid', name='Release Quality Sentinel', primary_association=association, logo_path=logo, setup_provenance='import')
with connection.cursor() as cursor:
    cursor.execute('CREATE TABLE release_quality_sentinel (value text)')
    cursor.execute("INSERT INTO release_quality_sentinel VALUES ('preserved')")
refresh = RefreshToken.for_user(user)
print('QUALITY_TOKEN=' + str(refresh.access_token))
print('QUALITY_REFRESH=' + str(refresh))
'''
        seeded = command(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', seed.replace('quality.invalid', host)), capture=True)
        token = next(line.removeprefix('QUALITY_TOKEN=') for line in seeded.splitlines() if line.startswith('QUALITY_TOKEN='))
        refresh_token = next(line.removeprefix('QUALITY_REFRESH=') for line in seeded.splitlines() if line.startswith('QUALITY_REFRESH='))
        before = read_env(env_file)
        volumes = command(['docker', 'volume', 'inspect', f'{project}_postgres_data', f'{project}_minio_data'], capture=True)
        bootstrap = directory / 'update'
        atomic_write(bootstrap, files['bin/update'][0], 0o700)
        print('Running the published bootstrap with production download and verification paths.', flush=True)
        command([str(bootstrap), '--directory', str(installation), '--version', image_version(target)])
        installed = json.loads((installation / '.updater/target-manifest.json').read_text())
        if installed != manifest:
            raise ValueError('The release manifest changed during the rehearsal')
        after = read_env(env_file)
        for key, value in before.items():
            if key != 'ASSOZETA_VERSION' and after.get(key) != value:
                raise ValueError(f'Configuration was not preserved: {key}')
        if command(['docker', 'volume', 'inspect', f'{project}_postgres_data', f'{project}_minio_data'], capture=True) != volumes:
            raise ValueError('Existing data volumes were replaced')
        command(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', '''
from django.db import connection
from django.core.files.storage import default_storage
from instance.models import InstanceConfiguration
with connection.cursor() as cursor:
    cursor.execute('SELECT value FROM release_quality_sentinel')
    assert cursor.fetchone()[0] == 'preserved'
instance = InstanceConfiguration.objects.get()
assert instance.name == 'Release Quality Sentinel'
with default_storage.open(instance.logo_path, 'rb') as logo:
    assert logo.read() == __import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAEElEQVR4nGMwVJ0CQQxwFgA0mgV9TDUSVgAAAABJRU5ErkJggg==')
'''))
        for service, image in {'api': 'backend', 'worker': 'backend', 'beat': 'backend', 'web': 'web', 'renderer': 'renderer'}.items():
            container = command(compose('ps', '-q', service), capture=True)
            if command(['docker', 'inspect', '--format', '{{.Image}}', container], capture=True) != image_ids[image]:
                raise ValueError(f'{service} is not running the verified release image')
        updater = command(['docker', 'compose', '--env-file', str(env_file), '-f', str(installation / 'compose.updater.yml'),
            '--project-name', project + '-updater', 'ps', '-q', 'updater'], capture=True)
        if command(['docker', 'inspect', '--format', '{{.Image}}', updater], capture=True) != image_ids['updater']:
            raise ValueError('The updater is not running the verified release image')
        for endpoint in ('admin', 'admin/updates'):
            request = Request(f'{origin}/api/instance/{endpoint}', headers={'Authorization': f'Bearer {token}'})
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if endpoint == 'admin' and image_version(payload['running_version']) != image_version(target):
                raise ValueError('The API does not report the intended version')
            if endpoint == 'admin/updates' and not (payload.get('available') and payload.get('can_update')):
                raise ValueError('The provisioned updater is not ready')
        if browser:
            report_dir = Path(os.environ['ASSOZETA_QUALITY_REPORT_DIR']).resolve() / 'published-browser'
            report_dir.mkdir(parents=True, exist_ok=True)
            browser_input = directory / 'browser.json'
            atomic_write(browser_input, json.dumps({'origin': origin, 'host': f'{host}:{port}', 'token': token,
                'refreshTokens': {token: refresh_token}, 'target': image_version(target)}))
            with log.open('a') as output:
                subprocess.run(['npm', 'test', '--prefix', str(ROOT / 'selfhost/tests/browser')], check=True, stdout=output, stderr=output,
                    env={**environment, 'ASSOZETA_BROWSER_INPUT': str(browser_input), 'ASSOZETA_BROWSER_PHASE': 'readiness', 'ASSOZETA_BROWSER_OUTPUT': str(report_dir)})
        evidence = {'source': source_tag, 'target': target_tag, 'release_commit': revision,
            'bundle_sha256': manifest['bundle_sha256'], 'images': manifest['images'], 'image_ids': image_ids, 'browser': browser}
        print('PASS: real release bootstrap, immutable images, commit identity, data preservation and updater readiness.', flush=True)
    finally:
        # Never upload raw logs, backups, .env, browser credentials or updater state.
        # Failure diagnostics stay private until this disposable runner is removed.
        if env_file.exists():
            with log.open('a') as output:
                for filename, name in (('compose.updater.yml', f'{project}-updater'), ('compose.yml', project)):
                    if (installation / filename).exists():
                        subprocess.run(['docker', 'compose', '--env-file', str(env_file), '-f', str(installation / filename),
                            '--project-name', name, 'down', '--volumes', '--remove-orphans'], env=environment, stdout=output, stderr=output)
                subprocess.run(['docker', 'volume', 'rm', f'{project}_updater_api', f'{project}_updater_status'], stdout=output, stderr=output)
        assert_project_removed(project)
        if evidence is not None:
            write_evidence('published-release', evidence)
        shutil.rmtree(directory)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--browser', action='store_true')
    arguments = parser.parse_args()
    rehearse(arguments.source, arguments.target, arguments.commit, arguments.browser)
