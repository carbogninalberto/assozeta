"""Read-only backup inventory and archive access, against disposable directories."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

UPDATER = Path(__file__).resolve().parents[1] / 'updater'
sys.path[:0] = [str(UPDATER), str(UPDATER.parents[1] / 'BE/instance')]
spec = importlib.util.spec_from_file_location('host_backups', UPDATER / 'backups.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.32\n')

    def archive(self, day, size=10, **meta):
        name = f'assozeta-202611{day:02d}T120000Z.tar.gz'
        (self.backups / name).write_bytes(b'x' * size)
        if meta:
            (self.backups / (name + '.meta')).write_text(''.join(f'{key}={value}\n' for key, value in meta.items()))
        return name

    def test_lists_newest_first_with_metadata_and_legacy_defaults(self):
        legacy = self.archive(1, size=5)
        manual = self.archive(2, size=7, origin='manual', created_at='20261102T120001Z', version='1.0.31')
        old_upgrade = self.archive(3, origin='pre-upgrade', upgrade='completed')
        upgrade = self.archive(4, origin='pre-upgrade', upgrade='completed', keep='true')
        odd = self.archive(5, origin='something-else')
        (self.backups / 'notes.txt').write_text('x')
        (self.backups / 'assozeta-20261106T120000Z.tar.gz.meta.tmp.1').write_text('origin=manual\n')
        result = module.inventory(self.root, self.env)
        rows = {row['name']: row for row in result['archives']}
        self.assertEqual([row['name'] for row in result['archives']], [odd, upgrade, old_upgrade, manual, legacy])
        self.assertEqual(rows[legacy], {
            'name': legacy, 'size': 5, 'created_at': '2026-11-01T12:00:00+00:00', 'version': None,
            'origin': 'unknown', 'keep': False, 'pending_upgrade': False, 'location': 'local', 'last_upgrade': False})
        self.assertEqual(rows[manual]['created_at'], '2026-11-02T12:00:01+00:00')
        self.assertEqual(rows[manual]['version'], '1.0.31')
        self.assertEqual(rows[odd]['origin'], 'unknown')
        self.assertTrue(rows[upgrade]['keep'])
        self.assertTrue(rows[upgrade]['last_upgrade'])
        self.assertFalse(rows[old_upgrade]['last_upgrade'])
        self.assertEqual(result['disk']['backups'], 5 + 7 + 10 * 3)
        self.assertGreater(result['disk']['total'], 0)
        self.assertEqual(result['retention'], {'automatic': 3})
        self.assertEqual(result['space_margin_mb'], 1024)

    def test_pending_upgrade_retention_setting_and_missing_directory(self):
        name = self.archive(1, origin='pre-upgrade')
        pending = self.root / '.updater/pending-distribution'
        pending.mkdir(parents=True)
        (pending / 'transaction.json').write_text(f'{{"backup": {{"path": "{self.backups / name}"}}}}')
        self.env.write_text('ASSOZETA_BACKUP_KEEP=5\nASSOZETA_BACKUP_SPACE_MARGIN_MB=2048\n')
        result = module.inventory(self.root, self.env)
        self.assertEqual(result['space_margin_mb'], 2048)
        self.assertTrue(result['archives'][0]['pending_upgrade'])
        self.assertEqual(result['retention'], {'automatic': 5})
        self.env.write_text('ASSOZETA_BACKUP_KEEP=x\n')
        self.assertEqual(module.inventory(self.root, self.env)['retention'], {'automatic': None})
        for path in self.backups.iterdir():
            path.unlink()
        self.backups.rmdir()
        self.assertEqual(module.inventory(self.root, self.env)['archives'], [])

    def test_symlinks_are_never_listed_or_opened(self):
        outside = self.root / 'secret.tar.gz'
        outside.write_bytes(b'secret')
        (self.backups / 'assozeta-20261101T120000Z.tar.gz').symlink_to(outside)
        self.assertEqual(module.inventory(self.root, self.env)['archives'], [])
        with self.assertRaises(module.BackupNotFound):
            module.open_archive(self.root, 'assozeta-20261101T120000Z.tar.gz')
        linked = self.root / 'linked'
        self.backups.rename(linked)
        self.backups.symlink_to(linked, target_is_directory=True)
        with self.assertRaises(OSError):
            module.inventory(self.root, self.env)

    def test_open_archive_accepts_only_archive_names(self):
        name = self.archive(1, size=3, origin='manual')
        stream, size = module.open_archive(self.root, name)
        with stream:
            self.assertEqual((stream.read(), size), (b'xxx', 3))
        for invalid in ('../.env', name + '.meta', 'assozeta-x.tar.gz', f'../backups/{name}', '', None):
            with self.assertRaises(module.BackupNotFound):
                module.open_archive(self.root, invalid)
        (self.backups / 'assozeta-20261109T120000Z.tar.gz').mkdir()
        with self.assertRaises(module.BackupNotFound):
            module.open_archive(self.root, 'assozeta-20261109T120000Z.tar.gz')
        self.assertEqual([row['name'] for row in module.inventory(self.root, self.env)['archives']], [name])


if __name__ == '__main__':
    unittest.main()
