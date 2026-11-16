"""The /bakney/v1/* backend switch stays on Django unless the operator opts in."""
from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'selfhost/bin/assozeta'


class BakneyProtocolBackendTests(unittest.TestCase):
    """bin/assozeta accepts django or go and derives the Caddy upstream."""

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

    def test_generated_configuration_keeps_the_protocol_on_django(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.values()
        self.assertEqual(values['ASSOZETA_BAKNEY_PROTOCOL_BACKEND'], 'django')
        self.assertEqual(values['ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM'], 'api:8000')
        self.assertIn('reverse_proxy {$ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM:api:8000}',
                      (ROOT / 'selfhost/caddy/Caddyfile').read_text())

    def test_go_backend_rewrites_the_upstream_and_the_default_returns(self):
        self.assertEqual(self.configure().returncode, 0)
        for backend, upstream in (('go', 'api-go:8000'), ('django', 'api:8000')):
            with self.subTest(backend=backend):
                self.rewrite('ASSOZETA_BAKNEY_PROTOCOL_BACKEND', backend)
                result = self.configure()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.values()['ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM'], upstream)

    def test_unknown_backend_is_rejected_without_touching_the_upstream(self):
        self.assertEqual(self.configure().returncode, 0)
        self.rewrite('ASSOZETA_BAKNEY_PROTOCOL_BACKEND', 'rust')
        original = self.env.read_text()
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid ASSOZETA_BAKNEY_PROTOCOL_BACKEND', result.stderr)
        self.assertIn('use django or go', result.stderr)
        self.assertEqual(self.env.read_text(), original)

    def test_an_unset_backend_leaves_an_explicit_upstream_alone(self):
        self.assertEqual(self.configure().returncode, 0)
        self.env.write_text(self.env.read_text().replace(
            'ASSOZETA_BAKNEY_PROTOCOL_BACKEND=django\n', ''))
        self.rewrite('ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM', 'api-go:8000')
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.values()['ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM'], 'api-go:8000')

    def test_compose_passes_the_upstream_to_the_web_service(self):
        compose = (ROOT / 'selfhost/compose.yml').read_text()
        self.assertIn('ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM: ${ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM:-api:8000}', compose)


if __name__ == '__main__':
    unittest.main()
