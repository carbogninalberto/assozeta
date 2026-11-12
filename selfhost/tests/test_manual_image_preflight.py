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
sys.exit(subprocess.run(['sh', '-ec', command, *args[args.index('-ec') + 2:]]).returncode)
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
        (self.app / 'check-manual').write_bytes(python.read_bytes())
        (self.app / 'check-manual').chmod(0o755)
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

    def check_go(self, version='1.0.18', required='true', **environment):
        (self.app / 'VERSION').write_text(version)
        (self.app / 'CODE_REVISION').write_text('a' * 40)
        if required is not None:
            (self.app / 'MANUAL_REQUIRED').write_text(required)
        return subprocess.run(['sh', str(CHECK), 'go-candidate@sha256:abc', version, 'go'],
                              env=self.environment | environment, capture_output=True, text=True)

    def test_go_dispatch_uses_packaged_checker_and_image_metadata(self):
        result = self.check_go()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.validator.read_text()), [
            '--root', str(self.app / 'release-manual'), '--revision', 'a' * 40,
            '--release', '1.0.18', '--required', 'true'])
        calls = json.loads(self.calls.read_text())
        self.assertEqual(calls[calls.index('--network') + 1], 'none')
        self.assertIn('--read-only', calls)
        self.assertNotIn('--volume', calls)
        self.assertNotIn('--env-file', calls)

    def test_go_optional_image_still_invokes_checker(self):
        result = self.check_go(required='false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.validator.read_text())[-2:], ['--required', 'false'])

    def test_go_cannot_use_historical_django_marker_exemption(self):
        self.assertNotEqual(self.check_go(version='1.0.17', required=None).returncode, 0)
        self.assertFalse(self.validator.exists())

    def test_go_missing_checker_and_invalid_package_fail(self):
        result = self.check_go(VALIDATOR_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.validator.exists(), 'must reach actual checker boundary')
        self.validator.unlink()
        (self.app / 'check-manual').unlink()
        self.assertNotEqual(self.check_go(required='false').returncode, 0)
        self.assertFalse(self.validator.exists())

    def test_unknown_backend_is_rejected_before_docker(self):
        result = subprocess.run(['sh', str(CHECK), 'candidate', '1.0.18', 'unknown'],
                                env=self.environment, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
