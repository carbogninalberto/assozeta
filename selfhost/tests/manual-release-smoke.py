"""Rehearse an evaluated bundle in disposable production images and databases.

No release is published and no existing installation is selected. The empty
baseline reproduces the old deployment's manual state using current schema;
it does not claim to execute historical backend code.
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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
from TESTS.http_client import Client
from application.manuale.bundle import load_bundle


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('release')
    parser.add_argument('--previous-package', type=Path, required=True)
    parser.add_argument('--previous-release', required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    bundle = load_bundle(args.package, revision=revision, release=args.release,
                         required=True, verify_assets=True)
    previous_bundle = load_bundle(args.previous_package, revision=revision, release=args.previous_release,
                                  required=True, verify_assets=True)
    if args.previous_release == args.release:
        raise ValueError('Upgrade rehearsal requires two distinct, independently evaluated versions')
    output = ROOT / 'quality-reports/manual-release'
    output.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'application_revision': revision, 'release': args.release,
              'corpus_identity': bundle.value['identity'], 'checks': []}
    project = 'assozeta-manual-release-' + uuid4().hex[:10]
    images = []
    with tempfile.TemporaryDirectory(prefix=project + '-') as temporary:
        work = Path(temporary)
        log = (work / 'execution.log').open('w')
        env_file = work / 'instance.env'
        installation = work / 'selfhost'
        cli = installation / 'bin/assozeta'
        registry_name = project + '-registry'
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
            for service in ('api', 'worker', 'beat'):
                identifier = subprocess.check_output([str(arg) for arg in
                    compose('ps', '--status', 'running', '-q', service)], env=env, text=True).strip()
                if not identifier:
                    raise RuntimeError('Application service is not running: ' + service)
                containers[service] = identifier
            return containers
        def record(check):
            result['checks'].append(check)
            print('Verified: ' + check, flush=True)
        try:
            # Copy committed source only; credentials/build output never enter
            # this build context. The runtime package is the sole generated input.
            archive = work / 'backend.tar'
            with archive.open('wb') as stream:
                subprocess.run(['git', 'archive', 'HEAD', 'BE', 'selfhost'], cwd=ROOT, stdout=stream, check=True)
            command(['tar', '-xf', archive, '-C', work])
            context = work / 'BE'
            empty = image('empty', context, '--target', 'production', '--build-arg', 'VERSION=manual-empty-baseline',
                          '--build-arg', 'CODE_REVISION=' + revision)
            shutil.copytree(args.package, context / 'release-manual', dirs_exist_ok=True)
            backend = image('bundled', context, '--target', 'production', '--build-arg', 'VERSION=' + args.release,
                            '--build-arg', 'CODE_REVISION=' + revision, '--build-arg', 'REQUIRE_MANUAL=true')
            shutil.rmtree(context / 'release-manual')
            shutil.copytree(args.previous_package, context / 'release-manual')
            previous_backend = image('previous', context, '--target', 'production',
                '--build-arg', 'VERSION=' + args.previous_release, '--build-arg', 'CODE_REVISION=' + revision,
                '--build-arg', 'REQUIRE_MANUAL=true')
            web = image('web', ROOT / 'UI', '--target', 'production', '--build-arg', 'VERSION=' + args.release)
            renderer = image('renderer', ROOT / 'selfhost/renderer')
            registry_port = free_port()
            command(['docker', 'run', '-d', '--name', registry_name,
                     '-p', f'127.0.0.1:{registry_port}:5000', 'registry:2.8.3'])
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
            repositories = {name: registry + '/' + project + '-' + name for name in ('backend', 'web', 'renderer')}
            for version, application_image in ((args.release, backend), (args.previous_release, previous_backend),
                                                ('manual-empty-baseline', empty)):
                for name, source in (('backend', application_image), ('web', web), ('renderer', renderer)):
                    target = repositories[name] + ':' + version
                    command(['docker', 'tag', source, target]); images.append(target)
                    command(['docker', 'push', target])
            command([cli, 'configure', '--domain', f'localhost:{port}', '--version', args.release], env=env)
            set_env({'COMPOSE_PROJECT_NAME': project,
                     'ASSOZETA_BACKEND_IMAGE': repositories['backend'],
                     'ASSOZETA_WEB_IMAGE': repositories['web'],
                     'ASSOZETA_RENDERER_IMAGE': repositories['renderer'],
                     'HTTPS_PORT': free_port(), 'MANUAL_CORPUS_BASE_URL': ''})
            command([cli, 'start'], env=env)
            ready()
            setup_token = next(line.split('=', 1)[1] for line in env_file.read_text().splitlines()
                               if line.startswith('INSTANCE_SETUP_TOKEN='))
            account = {'username': 'manual-owner@example.invalid', 'password': 'ManualRehearsal123!'}
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
            def contracts(phase, browser=False):
                command([ROOT / 'TESTS/run', 'manual', '--base-url', origin + '/api', '--fixture', fixture_path,
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
            before_restart = application_containers()
            # The shipped CLI handles workers with disabled healthchecks and
            # waits for public API readiness, including older Compose versions.
            command([cli, 'restart'], env=env)
            after_restart = application_containers()
            if any(after_restart[service] == identifier for service, identifier in before_restart.items()):
                raise RuntimeError('CLI restart did not recreate every application container')
            ready(); contracts('recreated')
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
            record('failed CLI candidate download restores services with the retained compatible manual')
            # Empty persistent manual volume, already configured association:
            # the observed manual state of v1.0.17, without historical code claims.
            command([cli, 'upgrade', 'manual-empty-baseline'], env=env); ready()
            if public.request('GET', '/instance/status')[1]['manual_enabled']:
                raise RuntimeError('Empty baseline unexpectedly advertises a manual')
            command([cli, 'upgrade', args.release], env=env); ready(); contracts('upgrade-empty', browser=True)
            record('CLI upgrade from a configured installation with an empty manual volume delivers the release manual')
            # Both packages have independently evaluated version bindings. The
            # source commit is intentionally identical to isolate selection from
            # unrelated schema migrations in this release-delivery rehearsal.
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
            for name, mutation in faults.items():
                damaged = work / name
                damaged.mkdir()
                (damaged / 'Dockerfile').write_text(f'FROM {backend}\nUSER root\nRUN ' +
                    json.dumps(['python', '-c', mutation]) + '\nUSER assozeta\n')
                corrupt = image(name, damaged)
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
            command(compose('up', '-d', '--wait', '--force-recreate', 'api'), env=env)
            ready(); contracts('rollback')
            record('failed candidate leaves valid package intact; original image recreates with compatible manual')
            result['status'] = 'passed'
        finally:
            if env_file.exists():
                if result['status'] != 'passed':
                    subprocess.run(compose('logs', '--no-color', '--tail', '200'),
                                   stdout=log, stderr=log, env=env)
                subprocess.run(compose('down', '-v', '--remove-orphans'), stdout=log, stderr=log, env=env)
            subprocess.run(['docker', 'rm', '-f', registry_name], stdout=log, stderr=log)
            for tag in reversed(images):
                subprocess.run(['docker', 'image', 'rm', tag], stdout=log, stderr=log)
            log.close()
            # Raw logs and credentials remain private; publish only the bounded
            # outcome and HTTP/browser artifacts over synthetic fixture data.
            if result['status'] != 'passed':
                private = output / 'private-execution.log'
                shutil.copyfile(work / 'execution.log', private); private.chmod(0o600)
                result['status'] = 'failed'
            (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
