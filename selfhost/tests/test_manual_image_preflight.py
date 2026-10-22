"""Execute the candidate-image check with isolated image filesystem fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / 'selfhost/bin/check-manual-image'


class ManualImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        self.calls = self.root / 'calls.json'
        docker = self.root / 'docker'
        docker.write_text(f'#!{sys.executable}\n' + '''import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ['CALLS']).write_text(json.dumps(args))
command = args[args.index('-ec') + 1].replace('/app/', os.environ['APP'] + '/')
sys.exit(subprocess.run(['sh', '-ec', command, 'fixture', args[-1]]).returncode)
''')
        docker.chmod(0o755)
        # The bundle itself is covered by its real integrity tests. Record that
        # this shell boundary invokes it with the actual image metadata.
        python = self.root / 'python'
        python.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
Path(os.environ['VALIDATOR']).write_text(json.dumps(sys.argv[1:]))
sys.exit(int(os.environ.get('VALIDATOR_EXIT', '0')))
''')
        python.chmod(0o755)
        self.validator = self.root / 'validator.json'
        self.environment = {**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}',
                            'APP': str(self.app), 'CALLS': str(self.calls),
                            'VALIDATOR': str(self.validator)}

    def check(self, version, required=None, **environment):
        (self.app / 'VERSION').write_text(version)
        (self.app / 'CODE_REVISION').write_text('a' * 40)
        if required is not None:
            (self.app / 'MANUAL_REQUIRED').write_text(required)
        return subprocess.run(['sh', str(CHECK), 'candidate@sha256:abc', version],
                              env=self.environment | environment, capture_output=True, text=True)

    def test_required_candidate_is_validated_without_installation_access(self):
        result = self.check('1.0.18', 'true')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(self.calls.read_text())
        self.assertIn('--read-only', calls)
        self.assertEqual(calls[calls.index('--network') + 1], 'none')
        self.assertNotIn('--volume', calls)
        self.assertNotIn('--env-file', calls)
        validator = json.loads(self.validator.read_text())
        self.assertEqual(validator[:2], ['-m', 'application.manuale.bundle'])
        self.assertEqual(validator[-4:], ['--release', '1.0.18', '--required', 'true'])

    def test_corrupt_bundle_rejects_candidate(self):
        self.assertNotEqual(self.check('1.0.18', 'true', VALIDATOR_EXIT='1').returncode, 0)

    def test_new_images_cannot_omit_or_corrupt_contract_marker(self):
        for required in (None, 'invalid'):
            with self.subTest(required=required):
                self.assertNotEqual(self.check('1.0.18', required).returncode, 0)
                self.assertFalse(self.validator.exists())

    def test_development_image_uses_its_explicit_optional_contract(self):
        self.assertEqual(self.check('2.0.1', 'false').returncode, 0)
        self.assertEqual(json.loads(self.validator.read_text())[-2:], ['--required', 'false'])

    def test_historical_image_without_manual_keeps_its_behavior(self):
        self.assertEqual(self.check('v1.0.17').returncode, 0)
        self.assertFalse(self.validator.exists())

    def test_historical_image_with_contract_still_validates_it(self):
        self.assertNotEqual(self.check('1.0.17', 'true', VALIDATOR_EXIT='1').returncode, 0)

    def test_wrong_image_version_is_rejected(self):
        self.check('1.0.18', 'true')
        self.validator.unlink()
        result = subprocess.run(['sh', str(CHECK), 'candidate', '1.0.19'],
                                env=self.environment, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.validator.exists())


if __name__ == '__main__':
    unittest.main()
