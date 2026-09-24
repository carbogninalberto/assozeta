"""Offline contracts for owned storage images and unchanged legacy installations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SERVER = 'quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z'
LEGACY_CLIENT = 'quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z'
DEFAULTS = dict(line.split('=', 1) for line in (ROOT / 'selfhost/.env.example').read_text().splitlines()
                if '=' in line and not line.startswith('#'))
SERVER = DEFAULTS['MINIO_IMAGE']
CLIENT = DEFAULTS['MINIO_CLIENT_IMAGE']


class StorageImageTests(unittest.TestCase):
    def fake_docker(self, root):
        docker = root / 'docker'
        docker.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as output:
    output.write(json.dumps({'args': args, 'server': os.environ.get('MINIO_IMAGE'),
                            'client': os.environ.get('MINIO_CLIENT_IMAGE')}) + '\\n')
if args[:2] == ['compose', 'version']:
    print('2.39.1')
if args and args[0] == 'pull' and args[1] == os.environ.get('FAIL_PULL'):
    sys.exit(29)
if args and args[0] == 'build':
    sys.exit('Installation must never build storage')
''')
        docker.chmod(0o755)
        return {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(root / 'calls')}

    def read_calls(self, root):
        return [json.loads(line) for line in (root / 'calls').read_text().splitlines()]

    def exercise_cli(self, values, *, production=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self.fake_docker(root)
            installation = root / 'old-installation'
            installation.mkdir()
            # Bootstrap extracts only the verified target CLI. No companion
            # config helper or Dockerfile exists in this historical installation.
            cli = root / 'bootstrap-cli'
            shutil.copy2(ROOT / 'selfhost/bin/assozeta', cli)
            env_file = installation / '.env'
            content = ('COMPOSE_PROJECT_NAME=legacy-test\nSTRIPE_PUBLIC_KEY=\n'
                       'STRIPE_WEBHOOK_SECRET=\nINSTANCE_SETUP_TOKEN=test-token\n'
                       'GOOGLE_CALENDAR_REDIRECT_URL=http://localhost/callback\n')
            content += ''.join(f'{key}={value}\n' for key, value in values.items())
            env_file.write_text(content)
            environment.update({'ASSOZETA_INSTALL_ROOT': str(installation),
                                'ASSOZETA_ENV_FILE': str(env_file),
                                'ASSOZETA_DEV_ENV_FILE': str(env_file),
                                # Saved/default values must win over host shell.
                                'MINIO_IMAGE': 'host.invalid/server:wrong',
                                'MINIO_CLIENT_IMAGE': 'host.invalid/client:wrong'})
            args = ['logs', 'minio'] if production else ['dev-compose', 'config', '--images']
            result = subprocess.run([str(cli), *args], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_file.read_text(), content)
            self.assertFalse((installation / 'config/storage-images.sh').exists())
            calls = self.read_calls(root)
            self.assertTrue(all(call['args'][0] not in ('pull', 'build', 'tag') for call in calls))
            compose = calls[-1]
            self.assertEqual(compose['args'][:3], ['compose', '--env-file', str(env_file)])
            self.assertIn(str(installation / ('compose.yml' if production else 'compose.dev.yml')), compose['args'])
            return compose

    def test_bootstrap_cli_maps_exact_legacy_defaults_without_companion_files(self):
        for production in (False, True):
            with self.subTest(production=production):
                call = self.exercise_cli({'MINIO_IMAGE': LEGACY_SERVER, 'MINIO_CLIENT_IMAGE': LEGACY_CLIENT},
                                         production=production)
                self.assertEqual((call['server'], call['client']), (SERVER, CLIENT))

    def test_missing_and_empty_storage_settings_use_owned_defaults(self):
        for values in ({}, {'MINIO_IMAGE': '', 'MINIO_CLIENT_IMAGE': ''}):
            with self.subTest(values=values):
                call = self.exercise_cli(values)
                self.assertEqual((call['server'], call['client']), (SERVER, CLIENT))

    def test_custom_and_mixed_images_are_preserved(self):
        for server, client in [('custom.invalid/minio:1', LEGACY_CLIENT),
                               (LEGACY_SERVER, 'custom.invalid/mc@sha256:' + '1' * 64),
                               ('quay.io/minio/minio:another-release', 'custom.invalid/mc:2'),
                               (SERVER, CLIENT)]:
            with self.subTest(server=server, client=client):
                call = self.exercise_cli({'MINIO_IMAGE': server, 'MINIO_CLIENT_IMAGE': client})
                self.assertEqual(call['server'], SERVER if server == LEGACY_SERVER else server)
                self.assertEqual(call['client'], CLIENT if client == LEGACY_CLIENT else client)

    def prepare(self, *args, fail_pull=''):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self.fake_docker(root)
            environment['FAIL_PULL'] = fail_pull
            result = subprocess.run([str(ROOT / 'selfhost/bin/prepare-storage'), *args],
                                    env=environment, text=True, capture_output=True)
            return result, [call['args'] for call in self.read_calls(root)]

    def test_prepare_only_pulls_owned_images(self):
        result, calls = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [['pull', SERVER], ['pull', CLIENT]])

    def test_legacy_preparation_aliases_only_after_both_pulls_succeed(self):
        result, calls = self.prepare('--legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [['pull', SERVER], ['pull', CLIENT],
                                ['tag', SERVER, LEGACY_SERVER], ['tag', CLIENT, LEGACY_CLIENT]])

    def test_pull_failure_never_creates_legacy_aliases_or_builds(self):
        for failure, expected in [(SERVER, [['pull', SERVER]]),
                                  (CLIENT, [['pull', SERVER], ['pull', CLIENT]])]:
            with self.subTest(failure=failure):
                result, calls = self.prepare('--legacy', fail_pull=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, expected)

    def test_historical_installer_reuses_cache_without_rewriting_image_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self.fake_docker(root)
            environment['ASSOZETA_REHEARSAL_DOCKER'] = str(root / 'docker')
            adapter = ROOT / 'selfhost/tests/fixtures/cached-storage-docker'
            for args in [['compose', '--env-file', '/tmp/release.env', 'pull'],
                         ['pull', LEGACY_SERVER], ['compose', 'up', '-d']]:
                subprocess.run([sys.executable, str(adapter), *args], check=True, env=environment)
                expected = args + ['--policy', 'missing'] if args[-1] == 'pull' else args
                self.assertEqual(self.read_calls(root)[-1]['args'], expected)
