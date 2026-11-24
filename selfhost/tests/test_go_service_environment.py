"""Start the packaged Go entry points against their rendered production environments.

An unsupported PostgreSQL SSL mode stops each binary just after configuration
loading, before any database or Redis connection. No images or daemon needed.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SELFHOST = ROOT / 'selfhost'


class GoServiceEnvironmentTests(unittest.TestCase):
    def test_binaries_accept_the_environment_passed_by_compose(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            env_file = work / '.env'
            environment = {'PATH': os.environ['PATH'], 'ASSOZETA_ENV_FILE': str(env_file)}
            subprocess.run([SELFHOST / 'bin/assozeta', 'configure', '--domain', 'localhost', '--version', 'test'],
                           env=environment, capture_output=True, check=True)
            with env_file.open('a') as stream:
                stream.write('\nDBSSLMODE=invalid-test-mode\nDBSSLROOTCERT=unused\n')
            model = subprocess.check_output(['docker', 'compose', '--env-file', env_file, '-f', SELFHOST / 'compose.yml',
                                             '--profile', '*', 'config', '--format', 'json'], env=environment)
            services = json.loads(model)['services']
            cases = [('api-go', 'server', 'invalid database configuration'),
                     # The default path migrates the business schema first (Phase 6 B2).
                     ('migrate-go', 'migrate', 'invalid business migration database configuration'),
                     ('worker-go', 'celery-consumer', 'invalid celery consumer database configuration'),
                     ('job-worker-go', 'worker', 'invalid worker database configuration'),
                     ('scheduler-go', 'scheduler', 'invalid scheduler database configuration'),
                     ('realtime-relay-go', 'relay-go-realtime', 'invalid database configuration'),
                     ('api-go', 'create-instance-superuser', 'invalid database configuration')]
            subprocess.run(['go', 'build', '-p', '4', '-o', str(work) + os.sep,
                            *['./cmd/' + binary for _, binary, _ in cases]], cwd=ROOT / 'BE_GO', check=True)
            for service, binary, expected in cases:
                with self.subTest(service=service, binary=binary):
                    definition = services[service]
                    env = dict(definition['environment'])
                    if service != 'api-go':
                        self.assertEqual(definition['entrypoint'], ['/app/' + binary])
                    if service == 'realtime-relay-go':
                        self.assertFalse(any(key.startswith('SOCIAL_AUTH_') for key in env))
                    if 'DATA_RESTORE_LOCK_PATH' in env:
                        env['DATA_RESTORE_LOCK_PATH'] = str(work / 'lifecycle.lock')
                    result = subprocess.run([work / binary], env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)

            for key in ('SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET'):
                with self.subTest(missing_api_key=key):
                    env = dict(services['api-go']['environment'])
                    del env[key]
                    result = subprocess.run([work / 'server'], env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(key + ' is required', result.stderr)
