"""The local registry adapter must preserve validation of Go delivery metadata."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).with_name('fixtures') / 'manifest_transport.py'


class GoFixtureManifestTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('go_fixture_manifest', PATH)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.metadata = {'images': {'backend': 'sha256:backend', 'updater': 'sha256:updater'},
                         'extensions': {'go_backend': {'schema_version': 1, 'image': 'sha256:go'}}}
        self.allowed = {'sha256:backend', 'sha256:updater', 'sha256:go'}

    def test_forwards_required_validation_and_preserves_input(self):
        before = copy.deepcopy(self.metadata)
        calls = []
        def validator(manifest, tag, **kwargs):
            calls.append((manifest, tag, kwargs))
        result = self.helper.validate_local_manifest(self.metadata, 'v2.0.2', self.allowed, validator,
                                                     require_go_backend=True)
        self.assertIs(result, self.metadata)
        self.assertEqual(self.metadata, before)
        canonical, tag, kwargs = calls.pop()
        self.assertEqual(tag, 'v2.0.2')
        self.assertEqual(kwargs, {'require_go_backend': True})
        self.assertEqual(canonical['extensions']['go_backend'], {'schema_version': 1,
                         'image': 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'a' * 64})
        self.assertEqual(canonical['images']['backend'], 'ghcr.io/carbogninalberto/assozeta-backend@sha256:' + 'a' * 64)

    def test_rejects_unlisted_go_and_core_images_before_validator(self):
        def forbidden(*args, **kwargs):
            self.fail('Unlisted actual image reached validator')
        for section in ('go', 'core'):
            with self.subTest(section=section):
                data = copy.deepcopy(self.metadata)
                if section == 'go': data['extensions']['go_backend']['image'] = 'sha256:outside'
                else: data['images']['backend'] = 'sha256:outside'
                with self.assertRaisesRegex(ValueError, 'outside this test'):
                    self.helper.validate_local_manifest(data, 'v2.0.2', self.allowed, forbidden)

    def test_does_not_hide_missing_or_invalid_extension_schema(self):
        for change in ({}, {'go_backend': None}, {'go_backend': {'schema_version': 99, 'image': 'sha256:go'}}):
            with self.subTest(change=change):
                data = {**self.metadata, 'extensions': change}
                def validator(canonical, tag, **kwargs):
                    expected = copy.deepcopy(change)
                    if isinstance(expected.get('go_backend'), dict) and 'image' in expected['go_backend']:
                        expected['go_backend']['image'] = 'ghcr.io/carbogninalberto/assozeta-backend-go@sha256:' + 'a' * 64
                    self.assertEqual(canonical['extensions'], expected)
                    raise ValueError('production rejected invalid extension')
                with self.assertRaisesRegex(ValueError, 'production rejected'):
                    self.helper.validate_local_manifest(data, 'v2.0.2', self.allowed, validator, require_go_backend=True)

    def test_actual_target_validator_and_direct_engine_reference_share_safe_adapter(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'images.json').write_text(json.dumps({name: 'sha256:' + name
                for name in ('backend', 'web', 'renderer', 'updater', 'go')}))
            script = """
import distribution
manifest = {'schema_version':1,'version':'2.0.2','bundle_sha256':'a'*64,
    'files':{'compose.yml':'b'*64},
    'images':{name:'sha256:'+name for name in ('backend','web','renderer','updater')},
    'extensions':{'go_backend':{'schema_version':1,'image':'sha256:go'}}}
assert distribution.validate_manifest(manifest,'v2.0.2',require_go_backend=True) is manifest
assert distribution.go_backend_reference(manifest,required=True) == 'sha256:go'
manifest['extensions']['go_backend']['image']='sha256:outside'
for check in (lambda: distribution.validate_manifest(manifest,'v2.0.2',require_go_backend=True),
              lambda: distribution.go_backend_reference(manifest,required=True)):
    try: check()
    except ValueError: pass
    else: raise AssertionError('Unlisted actual image accepted')
"""
            environment = {**os.environ, 'ASSOZETA_TEST_FIXTURES': str(work),
                'PYTHONPATH': os.pathsep.join(map(str, (PATH.parent, root / 'selfhost/updater', root / 'BE/instance')))}
            result = subprocess.run([sys.executable, '-c', script], env=environment,
                                    cwd=work, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_historical_validator_receives_no_new_keyword_unless_requested(self):
        data = {'images': self.metadata['images']}
        def old_validator(canonical, tag):
            self.assertNotIn('extensions', canonical)
        self.helper.validate_local_manifest(data, 'v2.0.1', self.allowed, old_validator)


if __name__ == '__main__': unittest.main()
