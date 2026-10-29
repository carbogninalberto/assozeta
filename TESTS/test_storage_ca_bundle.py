"""Failure controls for the cross-runtime storage CA artifact guard."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from TESTS import check_storage_ca_bundle as guard


class StorageCABundleGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='az-ca-guard-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / 'BE').mkdir()
        shutil.copyfile(guard.ROOT / 'BE/requirements.txt', root / 'BE/requirements.txt')
        self.source = root / 'certifi'
        shutil.copytree(guard.SOURCE, self.source)
        for name, value in (('ROOT', root), ('SOURCE', self.source)):
            replacement = patch.object(guard, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_unmodified_copy_matches_installed_python(self):
        guard.main()

    def test_modified_bundle_cannot_self_attest_with_a_new_hash(self):
        bundle = self.source / 'cacert.crt'
        bundle.write_bytes(bundle.read_bytes() + b'\n# unreviewed bundle alteration\n')
        path = self.source / 'UPSTREAM.json'
        manifest = json.loads(path.read_text())
        manifest['files_sha256']['cacert.crt'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(AssertionError, 'Python/Go trust artifact differs'):
            guard.main()

    def test_removing_python_pin_is_rejected(self):
        path = guard.ROOT / 'BE/requirements.txt'
        path.write_text('\n'.join(line for line in path.read_text().splitlines() if not line.startswith('certifi==')))
        with self.assertRaises(AssertionError):
            guard.main()

    def test_missing_license_is_rejected(self):
        (self.source / 'LICENSE').unlink()
        with self.assertRaises(AssertionError):
            guard.main()


if __name__ == '__main__':
    unittest.main()
