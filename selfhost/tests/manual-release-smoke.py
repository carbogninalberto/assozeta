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
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    bundle = load_bundle(args.package, revision=revision, release=args.release,
                         required=True, verify_assets=True)
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
        cli = ROOT / 'selfhost/bin/assozeta'
        env = {**os.environ, 'ASSOZETA_ENV_FILE': str(env_file)}
        def command(argv, **kwargs):
            return subprocess.run([str(arg) for arg in argv], check=True, stdout=log,
                                  stderr=log, **kwargs)
        def compose(*argv):
            return ['docker', 'compose', '--env-file', env_file, '-f', ROOT / 'selfhost/compose.yml', *argv]
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
        def record(check):
            result['checks'].append(check)
            print('Verified: ' + check, flush=True)
        try:
            # Copy committed source only; credentials/build output never enter
            # this build context. The runtime package is the sole generated input.
            archive = work / 'backend.tar'
            with archive.open('wb') as stream:
                subprocess.run(['git', 'archive', 'HEAD', 'BE'], cwd=ROOT, stdout=stream, check=True)
            command(['tar', '-xf', archive, '-C', work])
            context = work / 'BE'
            empty = image('empty', context, '--target', 'production', '--build-arg', 'VERSION=manual-empty-baseline',
                          '--build-arg', 'CODE_REVISION=' + revision)
            shutil.copytree(args.package, context / 'release-manual', dirs_exist_ok=True)
            backend = image('bundled', context, '--target', 'production', '--build-arg', 'VERSION=' + args.release,
                            '--build-arg', 'CODE_REVISION=' + revision, '--build-arg', 'REQUIRE_MANUAL=true')
            web = image('web', ROOT / 'UI', '--target', 'production', '--build-arg', 'VERSION=' + args.release)
            renderer = image('renderer', ROOT / 'selfhost/renderer')
            command([cli, 'configure', '--domain', f'localhost:{port}', '--version', 'test'], env=env)
            set_env({'COMPOSE_PROJECT_NAME': project, 'ASSOZETA_BACKEND_REF': backend,
                     'ASSOZETA_WEB_REF': web, 'ASSOZETA_RENDERER_REF': renderer,
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
            # Select a supported query from the existing shared reader plan.
            planned = subprocess.check_output(['node', '--input-type=module', '-e',
                "import fs from 'node:fs'; import {readerPlan} from './selfhost/tests/browser/manuale/reader-plan.mjs'; "
                "const p=readerPlan(JSON.parse(fs.readFileSync(process.argv[1])).chunks); "
                "console.log(JSON.stringify({query:p.query,id:p.section.id,title:p.section.title,sections:p.sections.length}));",
                str(args.package / 'index.json')], cwd=ROOT, text=True)
            plan = json.loads(planned)
            fixture = {'accounts': {'missing': account}, 'manual': {'release': args.release,
                'application_revision': revision, 'corpus_identity': bundle.value['identity'],
                'public_sections': plan['sections'], 'query': plan['query'], 'section_id': plan['id'],
                'section_title': plan['title']}}
            fixture_path = work / 'fixture.json'
            fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
            def contracts(phase, browser=False):
                command([ROOT / 'TESTS/run', 'manual', '--base-url', origin + '/api', '--fixture', fixture_path,
                         '--output', output / phase], cwd=ROOT)
                if browser:
                    command(['node', ROOT / 'TESTS/browser/manual-release.mjs'], cwd=ROOT,
                            env={**os.environ, 'TESTS_ORIGIN': origin, 'TESTS_FIXTURE': str(fixture_path),
                                 'TESTS_OUTPUT': str(output / phase / 'browser')})
            contracts('fresh', browser=True)
            record('fresh installation: reader, assets, search and real browser assistance without AI or subscription')
            command(compose('up', '-d', '--wait', '--force-recreate', 'api', 'worker', 'beat'), env=env)
            ready(); contracts('recreated')
            record('container recreation retains the image-matched manual')
            # Empty persistent manual volume, already configured association:
            # the observed manual state of v1.0.17, without historical code claims.
            set_env({'ASSOZETA_BACKEND_REF': empty})
            command([cli, 'start'], env=env); ready()
            if public.request('GET', '/instance/status')[1]['manual_enabled']:
                raise RuntimeError('Empty baseline unexpectedly advertises a manual')
            set_env({'ASSOZETA_BACKEND_REF': backend})
            command([cli, 'start'], env=env); ready(); contracts('upgrade-empty', browser=True)
            record('configured installation with empty manual volume acquires the release manual on startup')
            # Reject corrupt candidate image startup while the valid deployment
            # and its package remain untouched, then recreate the original image.
            damaged = work / 'damaged'
            damaged.mkdir()
            (damaged / 'Dockerfile').write_text(f'FROM {backend}\nUSER root\nRUN rm /app/release-manual/index.json\nUSER assozeta\n')
            corrupt = image('corrupt', damaged)
            attempt = subprocess.run(['docker', 'run', '--rm', corrupt], stdout=log, stderr=log, timeout=60)
            if attempt.returncode == 0:
                raise RuntimeError('Missing required manual did not reject candidate startup')
            contracts('retained-after-failure')
            command(compose('up', '-d', '--wait', '--force-recreate', 'api'), env=env)
            ready(); contracts('rollback')
            record('failed candidate leaves valid package intact; original image recreates with compatible manual')
            result['status'] = 'passed'
        finally:
            if env_file.exists():
                subprocess.run(compose('down', '-v', '--remove-orphans'), stdout=log, stderr=log, env=env)
            for tag in images:
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
