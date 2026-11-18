"""The realtime relay stays on Django unless the operator opts in to Go."""
from pathlib import Path
import json
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'selfhost/bin/assozeta'
COMPOSE = ROOT / 'selfhost/compose.yml'


class RealtimeRelayBackendTests(unittest.TestCase):
    """bin/assozeta accepts django or go and derives one replica for the chosen relay."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = Path(directory.name) / '.env'

    def configure(self):
        return subprocess.run(['sh', str(CLI), 'configure', '--domain', 'localhost', '--version', 'test'],
                              capture_output=True, text=True,
                              env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env)})

    def values(self):
        return dict(line.split('=', 1) for line in self.env.read_text().splitlines()
                    if '=' in line and not line.startswith('#'))

    def rewrite(self, key, value):
        lines = [f'{key}={value}' if line.startswith(f'{key}=') else line
                 for line in self.env.read_text().splitlines()]
        self.env.write_text('\n'.join(lines) + '\n')

    def replicas(self):
        values = self.values()
        return values['ASSOZETA_DJANGO_RELAY_REPLICAS'], values['ASSOZETA_GO_RELAY_REPLICAS']

    def test_generated_configuration_keeps_the_relay_on_django(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.values()['ASSOZETA_REALTIME_RELAY_BACKEND'], 'django')
        self.assertEqual(self.replicas(), ('1', '0'))

    def test_go_backend_moves_the_replica_and_the_default_returns(self):
        self.assertEqual(self.configure().returncode, 0)
        for backend, replicas in (('go', ('0', '1')), ('django', ('1', '0'))):
            with self.subTest(backend=backend):
                self.rewrite('ASSOZETA_REALTIME_RELAY_BACKEND', backend)
                result = self.configure()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.replicas(), replicas)

    def test_unknown_backend_is_rejected_without_touching_the_replicas(self):
        self.assertEqual(self.configure().returncode, 0)
        self.rewrite('ASSOZETA_REALTIME_RELAY_BACKEND', 'rust')
        original = self.env.read_text()
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid ASSOZETA_REALTIME_RELAY_BACKEND', result.stderr)
        self.assertIn('use django or go', result.stderr)
        self.assertEqual(self.env.read_text(), original)

    def test_an_existing_installation_without_the_setting_is_left_alone(self):
        # An .env written by an older release has none of the three keys: the
        # file stays unchanged and Compose's defaults run the Django relay.
        self.assertEqual(self.configure().returncode, 0)
        kept = [line for line in self.env.read_text().splitlines()
                if not line.startswith(('ASSOZETA_REALTIME_RELAY_BACKEND=', 'ASSOZETA_DJANGO_RELAY_REPLICAS=',
                                        'ASSOZETA_GO_RELAY_REPLICAS='))]
        self.env.write_text('\n'.join(kept) + '\n')
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.values()
        for key in ('ASSOZETA_REALTIME_RELAY_BACKEND', 'ASSOZETA_DJANGO_RELAY_REPLICAS', 'ASSOZETA_GO_RELAY_REPLICAS'):
            self.assertNotIn(key, values)

    @unittest.skipUnless(shutil.which('docker'), 'docker compose is required')
    def test_compose_runs_exactly_the_chosen_relay(self):
        self.assertEqual(self.configure().returncode, 0)
        for backend, django, go in (('django', 1, 0), ('go', 0, 1)):
            with self.subTest(backend=backend):
                self.rewrite('ASSOZETA_REALTIME_RELAY_BACKEND', backend)
                self.assertEqual(self.configure().returncode, 0)
                config = json.loads(subprocess.check_output(
                    ['docker', 'compose', '--env-file', str(self.env), '-f', str(COMPOSE),
                     'config', '--format', 'json'], text=True,
                    env={**os.environ, 'ASSOZETA_ENV_FILE': str(self.env)}))['services']
                relay, relay_go = config['realtime-relay'], config['realtime-relay-go']
                self.assertEqual(relay['command'], ['python', 'manage.py', 'relay_go_realtime'])
                self.assertEqual(relay['deploy']['replicas'], django)
                self.assertEqual(relay_go['entrypoint'], ['/app/relay-go-realtime'])
                self.assertEqual(relay_go['deploy']['replicas'], go)
                self.assertEqual(relay_go['image'], config['api-go']['image'])
                self.assertEqual(relay_go['restart'], 'unless-stopped')
                self.assertNotIn('profiles', relay_go)
                self.assertTrue(all('docker.sock' not in mount['target'] for mount in relay_go.get('volumes', [])))


if __name__ == '__main__':
    unittest.main()
