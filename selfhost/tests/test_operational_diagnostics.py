"""Host checks against disposable directories only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import time

spec = importlib.util.spec_from_file_location('host_diagnostics', Path(__file__).resolve().parents[1] / 'updater' / 'diagnostics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupReadinessTests(unittest.TestCase):
    def test_absent_corrupt_recent_and_stale_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(module.backup_readiness(root)['backup']['status'], 'warning')
            backups = root / 'backups'
            backups.mkdir()
            archive = backups / 'assozeta-fixture.tar.gz'
            archive.write_bytes(b'bad archive')
            self.assertEqual(module.backup_readiness(root)['backup']['status'], 'failed')
            archive.write_bytes(b'\x1f\x8bfixture')
            value = module.backup_readiness(root)['backup']
            self.assertEqual(value['status'], 'passed')
            self.assertEqual(value['level'], 'configuration')
            self.assertIn('ripristino non verificati', value['message'])
            self.assertNotIn(str(root), value['message'])
            stamp = time.time() - 9 * 86400
            os.utime(archive, (stamp, stamp))
            self.assertEqual(module.backup_readiness(root)['backup']['status'], 'warning')

    def test_disk_pressure_unreadable_path_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(module.shutil, 'disk_usage') as disk:
                disk.return_value.free = 1
                self.assertEqual(module.backup_readiness(root)['backup']['status'], 'warning')
            with patch.object(module.os, 'access', return_value=False):
                self.assertEqual(module.backup_readiness(root)['backup']['status'], 'failed')
            (root / 'backups').symlink_to(root, target_is_directory=True)
            self.assertEqual(module.backup_readiness(root)['backup']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
