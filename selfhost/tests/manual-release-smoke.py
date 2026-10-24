"""Rehearse an evaluated bundle in disposable production images and databases.

No release is published and no existing installation is selected. By default
the previous release uses the immutable images in its published update manifest.
An optional independently verified package supports additional synthetic tests.
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
import time
from urllib.request import urlopen
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from TESTS.http_client import Client
from application.manuale.bundle import load_bundle
from distribution import validate_manifest, download, unpack, digest


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def build_manual_backends(work, package, release, revision, image, *, prefix=''):
    """Build both current backend artifacts with one independently verified corpus."""
    built = {}
    for key, directory, name in (('backend', 'BE', 'bundled'), ('backend-go', 'BE_GO', 'go-bundled')):
        context = work / directory
        destination = context / 'release-manual'
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(package, destination)
        target = ('--target', 'production') if key == 'backend' else ()
        built[key] = image(prefix + name, context, *target,
                           '--build-arg', 'VERSION=' + release,
                           '--build-arg', 'CODE_REVISION=' + revision,
                           '--build-arg', 'REQUIRE_MANUAL=true')
    return built


def verified_historical_runtime(content, manifest):
    """Authenticate the entire published archive before selecting runtime files."""
    if digest(content) != manifest['bundle_sha256']:
        raise ValueError('Historical archive digest differs from published manifest')
    files = unpack(content)
    if {name: digest(value[0]) for name, value in files.items()} != manifest['files']:
        raise ValueError('Historical file manifest differs from published archive')
    return {name: value for name, value in files.items()
            if name == 'compose.yml' or name.startswith(('caddy/', 'config/'))}


def install_historical_runtime(installation, files):
    # Retain the current CLI's candidate-manual gates. This is a published Django
    # schema/manual transition, not a managed distribution/Go rollout rehearsal.
    for name, (content, mode) in files.items():
        target = installation / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode)
    return {name: digest(value[0]) for name, value in files.items()}



def cleanup_registry(registry_name, volume_ids, log, *, data_directory=None):
    """Remove only the owned registry and prove its anonymous storage is gone."""
    result = {'container': registry_name, 'volume_ids': volume_ids, 'verified': False}
    try:
        removal = subprocess.run(['docker', 'rm', '-f', '-v', registry_name], stdout=log, stderr=log)
        listing = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'],
                                 capture_output=True, text=True)
        remaining = sorted(set(volume_ids).intersection(listing.stdout.splitlines()))
        result.update(removal_exit_code=removal.returncode, listing_exit_code=listing.returncode,
                      remaining_volume_ids=remaining,
                      verified=removal.returncode == 0 and listing.returncode == 0 and not remaining)
        if data_directory is not None:
            result['bind_directory'] = str(data_directory)
            if removal.returncode == 0 and data_directory.exists():
                shutil.rmtree(data_directory)
            result['bind_directory_removed'] = not data_directory.exists()
            result['verified'] = result['verified'] and result['bind_directory_removed']
    except OSError as exc:
        result['verified'] = False
        result['error_type'] = type(exc).__name__
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('release')
    parser.add_argument('--previous-package', type=Path,
                        help='Optional independently verified synthetic rollback package')
    parser.add_argument('--previous-release', default='1.0.17',
                        help='Actual published source version (default: last release without a bundled manual)')
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    bundle = load_bundle(args.package, revision=revision, release=args.release,
                         required=True, verify_assets=True)
    previous_bundle = (load_bundle(args.previous_package, revision=revision, release=args.previous_release,
                                  required=True, verify_assets=True) if args.previous_package else None)
    if args.previous_release == args.release:
        raise ValueError('Upgrade rehearsal requires two distinct versions')
    output = ROOT / 'quality-reports/manual-release'
    output.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'application_revision': revision, 'release': args.release,
              'corpus_identity': bundle.value['identity'], 'checks': [], 'timings': [],
              'proof_scope': {'current_distribution': 'Django default plus required Go manual artifact and HTTP/assets',
                  'historical': ('independently verified synthetic package' if previous_bundle else
                      'published Django schema and frozen Compose/config/Caddy; current CLI manual gates'),
                  'full_managed_go_historical_transition': False},
              'runner_sha256': digest(Path(__file__).read_bytes())}
    started = last_checkpoint = time.monotonic()
    project = 'assozeta-manual-release-' + uuid4().hex[:10]
    images = []
    with tempfile.TemporaryDirectory(prefix=project + '-') as temporary:
        work = Path(temporary)
        log = (work / 'execution.log').open('w')
        env_file = work / 'instance.env'
        installation = work / 'selfhost'
        cli = installation / 'bin/assozeta'
        registry_name = project + '-registry'
        registry_created = False
        registry_volumes = []
        registry_data = work / 'registry-data'
        registry_user = f'{os.getuid()}:{os.getgid()}'
        env = {**os.environ, 'ASSOZETA_ENV_FILE': str(env_file)}
        def command(argv, **kwargs):
            return subprocess.run([str(arg) for arg in argv], check=True, stdout=log,
                                  stderr=log, **kwargs)
        def compose(*argv):
            return ['docker', 'compose', '--env-file', env_file, '-f', installation / 'compose.yml', *argv]
        def set_env(values):
            lines = env_file.read_text().splitlines()
            found = set()
            for i, line in enumerate(lines):
                key = line.split('=', 1)[0]
                if key in values:
                    lines[i] = key + '=' + str(values[key]); found.add(key)
            lines += [key + '=' + str(value) for key, value in values.items() if key not in found]
            env_file.write_text('\n'.join(lines) + '\n')
        def image(name, context, *build_args):
            tag = project + '-' + name + ':test'
            command(['docker', 'build', '-t', tag, *build_args, context])
            images.append(tag)
            return tag
        port = free_port()
        origin = f'http://localhost:{port}'
        public = Client(origin + '/api')
        def ready():
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    if public.request('GET', '/readyz')[0] == 200:
                        return
                except OSError:
                    pass
                time.sleep(1)
            raise RuntimeError('Disposable release application failed readiness')
        def application_containers():
            containers = {}
            for service in ('api', 'api-go', 'worker', 'beat'):
                identifier = subprocess.check_output([str(arg) for arg in
                    compose('ps', '--status', 'running', '-q', service)], env=env, text=True).strip()
                if not identifier:
                    raise RuntimeError('Application service is not running: ' + service)
                containers[service] = identifier
            return containers
        def record(check):
            nonlocal last_checkpoint
            now = time.monotonic()
            result['checks'].append(check)
            result['timings'].append({'phase': check, 'elapsed_seconds': round(now - last_checkpoint, 3)})
            last_checkpoint = now
            print('Verified: ' + check, flush=True)
        try:
            # Copy committed source only; credentials/build output never enter
            # this build context. The runtime package is the sole generated input.
            archive = work / 'backend.tar'
            with archive.open('wb') as stream:
                subprocess.run(['git', 'archive', 'HEAD', 'BE', 'BE_GO', 'selfhost'], cwd=ROOT, stdout=stream, check=True)
            command(['tar', '-xf', archive, '-C', work])
            backends = build_manual_backends(work, args.package, args.release, revision, image)
            backend = backends['backend']
            web = image('web', ROOT / 'UI', '--target', 'production', '--build-arg', 'VERSION=' + args.release)
            renderer = image('renderer', ROOT / 'selfhost/renderer')
            if previous_bundle:
                previous_backends = build_manual_backends(work, args.previous_package,
                    args.previous_release, revision, image, prefix='previous-')
                previous_images = {**previous_backends, 'web': web, 'renderer': renderer}
                result['previous_source'] = 'independently verified synthetic package'
            else:
                tag = 'v' + args.previous_release.removeprefix('v')
                url = f'https://github.com/carbogninalberto/assozeta/releases/download/{tag}/assozeta-update.json'
                with urlopen(url, timeout=60) as response:
                    previous_manifest = validate_manifest(json.load(response), tag)
                previous_images = {name: previous_manifest['images'][name] for name in ('backend', 'web', 'renderer')}
                archive_url = f'https://github.com/carbogninalberto/assozeta/releases/download/{tag}/assozeta-selfhost-{tag}.tar.gz'
                historical_runtime = verified_historical_runtime(download(archive_url), previous_manifest)
                result['previous_source'] = {'release': tag, 'manifest': url, 'images': previous_images,
                    'archive': archive_url, 'bundle_sha256': previous_manifest['bundle_sha256'],
                    'runtime_files': {name: digest(value[0]) for name, value in historical_runtime.items()}}
                for reference in previous_images.values():
                    command(['docker', 'pull', reference])
            registry_port = free_port()
            registry_data.mkdir(mode=0o700)
            # Docker may create the container then fail to bind its port. The
            # unique name is already owned, so finally must clean that partial start.
            registry_created = True
            result['registry_resources'] = {'container': registry_name, 'launch_attempted': True,
                                            'bind_directory': str(registry_data)}
            command(['docker', 'run', '-d', '--name', registry_name,
                     '--user', registry_user,
                     '--mount', f'type=bind,source={registry_data},target=/var/lib/registry',
                     '-p', f'127.0.0.1:{registry_port}:5000', 'registry:2.8.3'])
            registry_state = json.loads(subprocess.check_output(['docker', 'inspect', registry_name], text=True))[0]
            registry_volumes = [mount['Name'] for mount in registry_state['Mounts'] if mount['Type'] == 'volume']
            storage_mounts = [mount for mount in registry_state['Mounts'] if mount['Destination'] == '/var/lib/registry']
            if (registry_state['Config']['User'] != registry_user
                    or len(storage_mounts) != 1 or storage_mounts[0]['Type'] != 'bind'
                    or Path(storage_mounts[0]['Source']).resolve() != registry_data.resolve()):
                raise RuntimeError('Registry storage is not the exact owned host directory')
            result['registry_resources'] = {'container': registry_name, 'container_id': registry_state['Id'],
                                            'volume_ids': registry_volumes, 'storage_mount': storage_mounts[0],
                                            'user': registry_user, 'host_uid': os.getuid(), 'host_gid': os.getgid()}
            registry = f'localhost:{registry_port}'
            deadline = time.monotonic() + 60
            while True:
                try:
                    if Client('http://' + registry, timeout=1).request('GET', '/v2/')[0] == 200:
                        break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Disposable image registry failed readiness')
                time.sleep(.5)
            target_images = {**backends, 'web': web, 'renderer': renderer}
            repositories = {name: registry + '/' + project + '-' + name for name in target_images}
            for version, version_images in ((args.release, target_images),
                                            (args.previous_release, previous_images)):
                for name, source in version_images.items():
                    target = repositories[name] + ':' + version
                    command(['docker', 'tag', source, target]); images.append(target)
                    command(['docker', 'push', target])
            result['target_images'] = {name: json.loads(subprocess.check_output(
                ['docker', 'image', 'inspect', repositories[name] + ':' + args.release], text=True))[0]['Id']
                for name in repositories}
            record('production images built and historical image digests resolved')
            command([cli, 'configure', '--domain', f'localhost:{port}', '--version', args.release], env=env)
            set_env({'COMPOSE_PROJECT_NAME': project,
                     'ASSOZETA_BACKEND_IMAGE': repositories['backend'],
                     'ASSOZETA_BACKEND_GO_IMAGE': repositories['backend-go'],
                     'ASSOZETA_WEB_IMAGE': repositories['web'],
                     'ASSOZETA_RENDERER_IMAGE': repositories['renderer'],
                     'HTTPS_PORT': free_port()})
            # No manual-specific configuration exists in the rehearsal, even
            # when configure retains optional legacy download settings.
            env_file.write_text('\n'.join(line for line in env_file.read_text().splitlines()
                                          if not line.startswith('MANUAL_')) + '\n')
            command([cli, 'start'], env=env)
            ready()
            account = {'username': 'manual-owner@example.invalid', 'password': 'ManualRehearsal123!'}
            def initialize_association():
                setup_token = next(line.split('=', 1)[1] for line in env_file.read_text().splitlines()
                                   if line.startswith('INSTANCE_SETUP_TOKEN='))
                status, response = public.request('POST', '/instance/configure', {
                    'domain': f'localhost:{port}', 'oem': {'name': 'Manual rehearsal', 'abbreviation': 'MR',
                    'primaryColor': '#351DC2', 'supportEmail': account['username'], 'logo': ''},
                    'oauth': {}, 'stripe': {}, 'initialization': {'type': 'fresh', 'associationName': 'Manual rehearsal',
                    'ownerEmail': account['username'], 'ownerPassword': account['password']}},
                    headers={'X-Setup-Token': setup_token})
                if status != 200:
                    raise RuntimeError(f'Disposable setup failed: HTTP {status}')
                owner, login = public.login(account)
                association_id = login['user_data']['sport_association']['sport_association_id']
                status, _ = owner.request('PATCH', '/onboarding/update', {'membership_data': {
                    'season': {'day': 1, 'month': 9}, 'fiscal': {'day': 1, 'month': 1},
                    'subscription_fee': '20.00', 'membership_fee': '20.00'}})
                if status != 200 or owner.request('GET', '/profile/info')[1]['user_data']['requires_welcome']:
                    raise RuntimeError('Disposable association onboarding did not complete through the public API')
                return owner, association_id
            owner, association_id = initialize_association()
            # Select a supported query from the existing shared reader plan.
            planned = subprocess.check_output(['node', '--input-type=module', '-e',
                "import fs from 'node:fs'; import {readerPlan} from './selfhost/tests/browser/manuale/reader-plan.mjs'; "
                "const p=readerPlan(JSON.parse(fs.readFileSync(process.argv[1])).chunks); "
                "console.log(JSON.stringify({query:p.query,id:p.section.id,title:p.section.title,sections:p.sections.length}));",
                str(args.package / 'index.json')], cwd=ROOT, text=True)
            plan = json.loads(planned)
            fixture = {'accounts': {'missing': account}, 'session': {'access_token': owner.token}, 'manual': {'release': args.release,
                'application_revision': revision, 'corpus_identity': bundle.value['identity'],
                'public_sections': plan['sections'], 'query': plan['query'], 'section_id': plan['id'],
                'section_title': plan['title']}}
            fixture_path = work / 'fixture.json'
            fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
            def contracts(phase, browser=False, api_prefix='/api'):
                command([ROOT / 'TESTS/run', 'manual', '--base-url', origin + api_prefix, '--fixture', fixture_path,
                         '--output', output / phase], cwd=ROOT)
                if browser:
                    mcp_configuration = {'command': 'python', 'args': ['manage.py', 'run_mcp_server',
                        '--association-id', association_id, '--transport', 'stdio'],
                        'manual': fixture['manual'], 'course_count': 0}
                    mcp = subprocess.run([str(arg) for arg in compose('exec', '-T', 'api', 'python', '-',
                        json.dumps(mcp_configuration))], input=(ROOT / 'TESTS/mcp/manual.py').read_bytes(),
                        stdout=subprocess.PIPE, stderr=log, timeout=120, env=env, check=True)
                    (output / phase / 'mcp.json').write_bytes(mcp.stdout)
                    command(['node', ROOT / 'TESTS/browser/manual-release.mjs'], cwd=ROOT,
                            env={**os.environ, 'TESTS_ORIGIN': origin, 'TESTS_FIXTURE': str(fixture_path),
                                 'TESTS_OUTPUT': str(output / phase / 'browser')})
            contracts('fresh', browser=True)
            record('fresh installation: reader, assets, search and real browser assistance without AI or subscription')
            go_container = subprocess.check_output([str(arg) for arg in compose('ps', '-q', 'api-go')],
                                                  env=env, text=True).strip()
            go_state = json.loads(subprocess.check_output(['docker', 'inspect', go_container], text=True))[0]
            if (go_state['Image'] != result['target_images']['backend-go']
                    or go_state['State'].get('Health', {}).get('Status') != 'healthy'):
                raise RuntimeError('Current-distribution Go image is not the expected healthy artifact')
            result['current_go_image'] = go_state['Image']
            # A real stopped upstream must fail on v2 while the Django alias still works.
            command(compose('stop', 'api-go'), env=env)
            go_owner = Client(origin + '/api/v2', owner.token)
            first_asset = next(image['path'] for chunk in bundle.value['chunks'] for image in chunk['screenshots'])
            asset_path = '/manuale/assets/' + quote(first_asset, safe='/')
            controls = {
                'go_health': Client(origin + '/api/v2').request('GET', '/healthz')[0],
                'django_health': public.request('GET', '/healthz')[0],
                'go_sections': go_owner.request('GET', '/manuale/sections')[0],
                'django_sections': owner.request('GET', '/manuale/sections')[0],
                'go_asset': go_owner.request('GET', asset_path, raw=True)[0],
                'django_asset': owner.request('GET', asset_path, raw=True)[0],
            }
            result['go_upstream_negative_control'] = controls
            if controls != {'go_health': 502, 'django_health': 200, 'go_sections': 502,
                            'django_sections': 200, 'go_asset': 502, 'django_asset': 200}:
                raise RuntimeError('Go manual route negative control did not isolate the real upstream')
            command(compose('up', '-d', '--wait', 'api-go'), env=env)
            contracts('fresh-go', api_prefix='/api/v2')
            record('current distribution: exact healthy Go artifact serves manual HTTP/assets; stopped-Go v2 rejects fallback while Django remains available')
            before_restart = application_containers()
            # The shipped CLI handles workers with disabled healthchecks and
            # waits for public API readiness, including older Compose versions.
            command([cli, 'restart'], env=env)
            after_restart = application_containers()
            if any(after_restart[service] == identifier for service, identifier in before_restart.items()):
                raise RuntimeError('CLI restart did not recreate every application container')
            ready(); contracts('recreated')
            contracts('recreated-go', api_prefix='/api/v2')
            record('container recreation retains the image-matched manual')
            # Stop at the real create/start boundary, as if activation had been
            # interrupted after replacing the container but before starting it.
            command(compose('up', '--no-start', '--no-deps', '--force-recreate', 'api'), env=env)
            candidate = subprocess.check_output([str(arg) for arg in compose('ps', '-a', '-q', 'api')],
                                                env=env, text=True).strip()
            state = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Status}}', candidate],
                                            text=True).strip()
            if state != 'created':
                raise RuntimeError('Activation interruption did not leave an unstarted candidate')
            command([cli, 'start'], env=env)
            ready(); contracts('interrupted-start-recovery')
            contracts('interrupted-start-recovery-go', api_prefix='/api/v2')
            record('normal CLI startup recovers activation interrupted between container creation and startup')
            # Exercise the shipped upgrade error path: backup stops services,
            # the missing candidate fails to pull, and the CLI restores them.
            unavailable = subprocess.run([str(cli), 'upgrade', 'manual-unavailable-candidate'],
                                         env=env, stdout=log, stderr=log)
            configured = next(line.split('=', 1)[1] for line in env_file.read_text().splitlines()
                              if line.startswith('ASSOZETA_VERSION='))
            if unavailable.returncode == 0 or configured != args.release:
                raise RuntimeError('Failed candidate download did not retain the installed release')
            ready(); contracts('failed-upgrade-recovery')
            contracts('failed-upgrade-recovery-go', api_prefix='/api/v2')
            record('failed CLI candidate download restores services with the retained compatible manual')
            if previous_bundle:
                previous_expected = {**fixture['manual'], 'release': args.previous_release,
                                     'corpus_identity': previous_bundle.value['identity']}
                original_expected = fixture['manual']
                def select_expected(expected):
                    fixture['manual'] = expected
                    fixture_path.write_text(json.dumps(fixture))
                command([cli, 'upgrade', args.previous_release], env=env); ready()
                select_expected(previous_expected); contracts('previous-compatible')
                command([cli, 'upgrade', args.release], env=env); ready()
                select_expected(original_expected); contracts('upgrade-compatible')
                record('upgrade from a different verified package selects the new version')
                command([cli, 'upgrade', args.previous_release], env=env); ready()
                select_expected(previous_expected); contracts('rollback-compatible', browser=True)
                record('rollback selects the previous version with its own compatible manual')
                command([cli, 'upgrade', args.release], env=env); ready(); select_expected(original_expected)
            # Reject corrupt candidate image startup while the valid deployment
            # and its package remain untouched, then recreate the original image.
            first_image = next(image['path'] for chunk in bundle.value['chunks'] for image in chunk['screenshots'])
            # Supply fault commands as JSON exec-form RUN, avoiding shell path interpolation.
            faults = {
                'missing-index': "from pathlib import Path; Path('/app/release-manual/index.json').unlink()",
                'corrupt-index': "from pathlib import Path; Path('/app/release-manual/index.json').write_text('{}')",
                'wrong-version': "from pathlib import Path; Path('/app/VERSION').write_text('incompatible')",
                'missing-image': 'from pathlib import Path; Path(' + repr('/app/release-manual/' + first_image) + ').unlink()',
                'corrupt-image': 'from pathlib import Path; Path(' + repr('/app/release-manual/' + first_image) + ").write_bytes(b'corrupt')",
            }
            corrupt_images = {}
            for name, mutation in faults.items():
                damaged = work / name
                damaged.mkdir()
                (damaged / 'Dockerfile').write_text(f'FROM {backend}\nUSER root\nRUN ' +
                    json.dumps(['python', '-c', mutation]) + '\nUSER assozeta\n')
                corrupt = image(name, damaged)
                corrupt_images[name] = corrupt
                attempt = subprocess.run(['docker', 'run', '--rm', corrupt], capture_output=True, text=True, timeout=60)
                log.write(attempt.stdout + attempt.stderr)
                expected_error = {
                    'missing-index': 'FileNotFoundError', 'missing-image': 'FileNotFoundError',
                    'corrupt-index': 'Invalid or unsupported manual index',
                    'wrong-version': 'No verified manual for the running application',
                    'corrupt-image': 'Bundled screenshot differs from verified evidence',
                }[name]
                if (attempt.returncode == 0 or expected_error not in attempt.stderr
                        or 'Starting gunicorn' in attempt.stderr or 'ImproperlyConfigured' in attempt.stderr):
                    raise RuntimeError(name + ' did not reject candidate startup at the manual validation gate')
            record('missing/corrupt index and PNGs and mismatched version reject candidate startup')
            contracts('retained-after-failure')
            contracts('retained-after-failure-go', api_prefix='/api/v2')
            command(compose('up', '-d', '--wait', '--force-recreate', 'api'), env=env)
            ready(); contracts('rollback')
            contracts('rollback-go', api_prefix='/api/v2')
            record('failed candidate leaves valid package intact; original image recreates with compatible manual')
            if not previous_bundle:
                # Start the real historical schema in empty, owned volumes.
                # The published runtime has no Go service. Keep that real
                # composition for the bounded Django schema/manual transition;
                # current-distribution Go delivery was verified independently above.
                command(compose('down', '-v', '--remove-orphans'), env=env)
                result['historical_runtime_installed'] = install_historical_runtime(installation, historical_runtime)
                historical_services = subprocess.check_output([str(arg) for arg in compose('config', '--services')],
                                                             env=env, text=True).splitlines()
                if 'api-go' in historical_services or 'migrate-go' in historical_services:
                    raise RuntimeError('Published pre-Go runtime unexpectedly contains Go services')
                set_env({'ASSOZETA_VERSION': args.previous_release})
                command([cli, 'start'], env=env); ready()
                # The frozen published Compose migration job already ran its
                # seed_selfhost command against the actual historical image.
                owner, association_id = initialize_association()
                if public.request('GET', '/instance/status')[1].get('manual_enabled', False):
                    raise RuntimeError('Historical transition expects a release without a manual')
                before_profile = owner.request('GET', '/profile/info')[1]['user_data']
                # Persist the legacy setting in actual historical configuration,
                # rather than relying solely on the browser interception case.
                command(compose('exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c',
                    "from instance.models import InstanceConfiguration; "
                    "c=InstanceConfiguration.objects.get(); "
                    "c.display_settings={**c.display_settings,'sidebar':{**c.display_settings.get('sidebar',{}),'showManual':False}}; "
                    "c.save(update_fields=['display_settings'])"), env=env)
                command([cli, 'backup'], env=env)
                historical_backup = max((installation / 'backups').glob('*.tar.gz'), key=lambda path: path.stat().st_mtime_ns)
                historical_environment = env_file.read_bytes()
                target_tag = repositories['backend'] + ':' + args.release
                for fault, corrupt in corrupt_images.items():
                    command(['docker', 'tag', corrupt, target_tag])
                    command(['docker', 'push', target_tag])
                    attempt = subprocess.run([str(cli), 'upgrade', args.release], env=env, stdout=log, stderr=log)
                    if attempt.returncode == 0:
                        raise RuntimeError(fault + ' was accepted by the ordinary upgrade command')
                    configured = next(line.split('=', 1)[1] for line in env_file.read_text().splitlines()
                                      if line.startswith('ASSOZETA_VERSION='))
                    if configured != args.previous_release:
                        raise RuntimeError(fault + ' changed the installed version before validation')
                    ready()
                    owner, _ = public.login(account)
                    if owner.request('GET', '/profile/info')[1]['user_data']['sport_association'] != before_profile['sport_association']:
                        raise RuntimeError(fault + ' changed historical business data')
                    if public.request('GET', '/instance/status')[1].get('manual_enabled', False):
                        raise RuntimeError(fault + ' activated a candidate manual')
                command(['docker', 'tag', backend, target_tag])
                command(['docker', 'push', target_tag])
                record('ordinary CLI rejects each missing/corrupt manual candidate before migrations or activation and restores the working historical release')
                command([cli, 'upgrade', args.release], env=env); ready()
                owner, _ = public.login(account)
                fixture['session']['access_token'] = owner.token
                fixture_path.write_text(json.dumps(fixture))
                after_profile = owner.request('GET', '/profile/info')[1]['user_data']
                for key in ('sport_association', 'requires_welcome'):
                    if before_profile[key] != after_profile[key]:
                        raise RuntimeError('Historical upgrade changed association data: ' + key)
                contracts('upgrade-historical', browser=True)
                record('real published ' + args.previous_release + ': upgrade preserves association data and delivers the manual with legacy branding and empty download volume')
                # Restore the old schema and its data before selecting old code.
                command([cli, 'stop'], env=env)
                command([cli, 'restore', historical_backup, '--yes'], env=env)
                env_file.write_bytes(historical_environment)
                command([cli, 'start'], env=env); ready()
                owner, _ = public.login(account)
                restored = owner.request('GET', '/profile/info')[1]['user_data']
                if restored['sport_association'] != before_profile['sport_association']:
                    raise RuntimeError('Historical rollback did not preserve association data')
                if public.request('GET', '/instance/status')[1].get('manual_enabled', False):
                    raise RuntimeError('Historical rollback exposed an incompatible newer manual')
                record('historical backup rollback restores the original application/data pair and its honest absence of a manual')
            result['status'] = 'passed'
        finally:
            if env_file.exists():
                if result['status'] != 'passed':
                    subprocess.run(compose('logs', '--no-color', '--tail', '200'),
                                   stdout=log, stderr=log, env=env)
                subprocess.run(compose('down', '-v', '--remove-orphans'), stdout=log, stderr=log, env=env)
            if registry_created:
                result['registry_cleanup'] = cleanup_registry(registry_name, registry_volumes, log, data_directory=registry_data)
                if not result['registry_cleanup']['verified']:
                    result['status'] = 'failed'
            for tag in reversed(images):
                subprocess.run(['docker', 'image', 'rm', tag], stdout=log, stderr=log)
            log.close()
            # Raw logs and credentials remain private; publish only the bounded
            # outcome and HTTP/browser artifacts over synthetic fixture data.
            if result['status'] != 'passed':
                private = output / 'private-execution.log'
                shutil.copyfile(work / 'execution.log', private); private.chmod(0o600)
                result['status'] = 'failed'
            result['elapsed_seconds'] = round(time.monotonic() - started, 3)
            (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    if result['status'] != 'passed':
        raise RuntimeError('Manual release rehearsal or owned registry cleanup failed')


if __name__ == '__main__':
    main()
