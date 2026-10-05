"""Exercise the operator command with a simulated Docker CLI, without services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ManualSyncCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='manual-sync-cli-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'config').mkdir()
        shutil.copyfile(ROOT / 'selfhost/config/required.env', self.root / 'config/required.env')
        (self.root / '.env').write_text((ROOT / 'selfhost/.env.example').read_text().replace('replace-me', 'test-only'))
        self.calls = self.root / 'calls.jsonl'
        docker = self.root / 'docker'
        docker.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['MANUAL_TEST_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['compose', 'version']:
    print('2.39.1')
if 'sync_manuale_corpus' in args:
    sys.exit(int(os.environ.get('MANUAL_TEST_EXIT', '0')))
''')
        docker.chmod(0o755)
        self.environment = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'ASSOZETA_INSTALL_ROOT': str(self.root), 'ASSOZETA_ENV_FILE': str(self.root / '.env'),
            'MANUAL_TEST_CALLS': str(self.calls)}

    def invoke(self, *arguments):
        return subprocess.run([str(ROOT / 'selfhost/bin/assozeta'), 'sync-manual', *arguments],
            env=self.environment, capture_output=True, text=True)

    def test_sync_uses_configured_api_image_without_starting_dependencies_or_seed(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        run = next(call for call in calls if 'sync_manuale_corpus' in call)
        self.assertEqual(run[-7:], ['run', '--rm', '--no-deps', 'api', 'python', 'manage.py', 'sync_manuale_corpus'])
        self.assertIn(str(self.root / '.env'), run)
        self.assertIn(str(self.root / 'compose.yml'), run)
        self.assertFalse(any('up' in call or 'migrate' in call or 'seed_selfhost' in call for call in calls))
        self.assertFalse((self.root / '.lifecycle.lock').exists())

    def test_failed_sync_is_nonzero_and_releases_lifecycle_lock(self):
        self.environment['MANUAL_TEST_EXIT'] = '23'
        self.assertEqual(self.invoke().returncode, 23)
        self.assertFalse((self.root / '.lifecycle.lock').exists())

    def test_arguments_cannot_override_the_trusted_corpus_source(self):
        result = self.invoke('https://untrusted.invalid')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not accept arguments', result.stderr)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
