"""Exercise the shipped shell CLI against disposable configuration and Docker stubs."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import read_env, update_eligibility_reason

CLI = ROOT / 'selfhost/bin/assozeta'


class UpgradePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '.updater').mkdir()
        self.env = self.root / '.env'
        self.calls = self.root / 'docker-calls'
        self.values = {'ASSOZETA_VERSION': '1.0.5', **{
            f'ASSOZETA_{name}_IMAGE': f'ghcr.io/carbogninalberto/assozeta-{name.lower()}'
            for name in ('BACKEND', 'WEB', 'RENDERER')}}
        self.docker = self.root / 'docker'
        self.docker.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
with open(os.environ['TEST_DOCKER_CALLS'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'ps':
    sys.exit(0)
sys.exit(98)
''')
        self.docker.chmod(0o755)

    def write_env(self, values):
        self.env.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))

    def run_cli(self, **extra):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
        environment.update(PATH=f'{self.root}:{os.environ["PATH"]}', ASSOZETA_INSTALL_ROOT=str(self.root),
                           ASSOZETA_ENV_FILE=str(self.env), TEST_DOCKER_CALLS=str(self.calls), **extra)
        return subprocess.run([str(CLI), 'upgrade', '1.0.6'], env=environment, text=True, capture_output=True)

    def test_custom_image_with_pins_rejects_before_any_mutation(self):
        values = self.values | {'ASSOZETA_WEB_IMAGE': 'assozeta-web-form-fix-20260918',
                               'ASSOZETA_WEB_REF': 'ghcr.io/carbogninalberto/assozeta-web@sha256:' + 'a' * 64}
        for runner in (True, False):
            with self.subTest(runner=runner):
                self.write_env(values)
                before = self.env.read_bytes()
                self.calls.unlink(missing_ok=True)
                result = self.run_cli(**({'ASSOZETA_RUNNER_ACTIVE': '1', 'ASSOZETA_EXPECTED_SOURCE': 'v1.0.5'} if runner else {}))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('managed update unavailable' if runner else 'ASSOZETA_WEB_REF pins an image', result.stderr)
                self.assertEqual(self.env.read_bytes(), before)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertTrue(calls)
                self.assertTrue(all(call[0] == 'ps' for call in calls), calls)
                self.assertNotIn('@@ASSOZETA_STAGE backup', result.stdout)

    def test_python_and_cli_eligibility_agree(self):
        script = CLI.read_text().split('\ncommand=${1:-help}\n')[0] + '\nsupports_self_update\n'
        for changes in ({}, {'ASSOZETA_WEB_IMAGE': 'custom'}, {'ASSOZETA_RENDERER_IMAGE': ''},
                        {'ASSOZETA_BACKEND_IMAGE': 'custom'}, {'ASSOZETA_VERSION': 'latest'},
                        {'ASSOZETA_VERSION': '01.0.5'}, {'ASSOZETA_VERSION': 'v1.0.5'},
                        {'ASSOZETA_VERSION': '1.0.5-rc.1'}):
            with self.subTest(changes=changes):
                self.write_env(self.values | changes)
                result = subprocess.run(['sh', '-c', script], env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env)}, capture_output=True)
                self.assertEqual(result.returncode == 0, update_eligibility_reason(read_env(self.env)) is None)

    def test_managed_and_unpinned_custom_flows_remain_reachable(self):
        # Stop at the first downstream boundary, before backup or any real Docker use.
        definitions = CLI.read_text().split('\ncommand=${1:-help}\n')[0]
        for custom in (True, False):
            with self.subTest(custom=custom):
                self.write_env(self.values | ({'ASSOZETA_WEB_IMAGE': 'custom'} if custom else {}))
                script = definitions + '''
ensure_configuration_values() { :; }
docker() { printf 'managed-image-pull\\n'; exit 43; }
backup_prod() { printf 'custom-backup-boundary\\n'; exit 44; }
upgrade_prod 1.0.6
'''
                environment = {key: value for key, value in os.environ.items() if not key.startswith('ASSOZETA_')}
                result = subprocess.run(['sh', '-c', script], env={**environment, 'ASSOZETA_ENV_FILE': str(self.env)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 44 if custom else 43, result.stderr)
                self.assertIn('custom-backup-boundary' if custom else 'managed-image-pull', result.stdout)


if __name__ == '__main__':
    unittest.main()
