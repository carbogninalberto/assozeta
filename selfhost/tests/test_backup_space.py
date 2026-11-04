"""Backup free-space preflight, archive staging and retention, without live Docker."""
import os
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
GIB = 1024 ** 3

# Stubs for every Docker-backed step of backup_prod. TEST_* values select the
# estimated sizes, free space and tar flavour.
STUBS = '''
preflight() { :; }
ensure_configuration_values() { :; }
prod_app_services_running() { return 1; }
stop_prod_data_services() { :; }
start_prod_app_services() { :; }
release_prod_lock() { :; }
prod_compose() {
    case "$*" in
        *pg_database_size*) [ -n "$TEST_DATABASE_BYTES" ] && printf '%s\\n' "$TEST_DATABASE_BYTES" ;;
        *"mc du"*) printf '{"status":"success","prefix":"bucket","size":%s,"objects":3}\\n' "$TEST_OBJECT_BYTES" ;;
        *pg_dump*) printf 'dump' ;;
        *"mc mirror"*) printf '%s\\n' "$*" >> "$TEST_LOG" ;;
    esac
}
df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/x 1 1 %s 1%% /\\n' "$TEST_AVAILABLE_KIB"; }
tar() {
    if [ "$1" = --version ]; then
        [ "$TEST_GNU_TAR" = 1 ] && echo 'tar (GNU tar) 1.35'
        return 0
    fi
    printf 'tar %s\\n' "$*" >> "$TEST_LOG"
    command tar "$@" 2>/dev/null || command tar -C "$2" -czf "$5" .
}
'''


class BackupSpaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / '.env'
        self.env.write_text('ASSOZETA_VERSION=1.0.3\nDBUSER=u\nDBNAME=d\nAWS_STORAGE_BUCKET_NAME=bucket\n')
        self.backups = self.root / 'backups'
        self.backups.mkdir()
        self.log = self.root / 'commands.log'

    def run_cli(self, body, available=100 * GIB, database=GIB, objects=2 * GIB, gnu_tar='0', **env):
        return subprocess.run(['sh', '-c', DEFINITIONS + STUBS + body], capture_output=True, text=True, env={
            **os.environ, 'ASSOZETA_INSTALL_ROOT': str(self.root), 'ASSOZETA_ENV_FILE': str(self.env),
            'TEST_LOG': str(self.log), 'TEST_AVAILABLE_KIB': str(available // 1024),
            'TEST_DATABASE_BYTES': str(database) if database is not None else '',
            'TEST_OBJECT_BYTES': str(objects), 'TEST_GNU_TAR': gnu_tar, **env,
        })

    def test_insufficient_space_stops_before_services_and_is_reported_to_runner(self):
        # 3 GiB of data staged plus an uncompressed-size archive and 1 GiB margin.
        result = self.run_cli('backup_prod', available=6 * GIB)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('@@ASSOZETA_ERROR Spazio su disco insufficiente per il backup', result.stdout)
        self.assertIn('7168 MB required, 6144 MB available', result.stderr)
        self.assertNotIn('stopping application services', result.stdout)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_upgrade_reserves_space_for_new_images(self):
        result = self.run_cli('backup_extra_space_mb=2048; backup_prod', available=8 * GIB)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('9216 MB required', result.stderr)

    def test_gnu_tar_halves_the_requirement_and_deletes_staged_files_while_archiving(self):
        result = self.run_cli('backup_prod', available=5 * GIB, gnu_tar='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text()
        self.assertIn('mc mirror --quiet', commands)
        self.assertIn('--remove-files', commands)
        archives = list(self.backups.glob('assozeta-*.tar.gz'))
        self.assertEqual(len(archives), 1)
        with tarfile.open(archives[0]) as archive:
            names = {name.removeprefix('./') for name in archive.getnames()}
        self.assertTrue({'postgres.dump', 'manifest.txt', 'config.env'} <= names)
        self.assertEqual([path.name for path in self.backups.iterdir()], [archives[0].name])

    def test_unknown_size_keeps_previous_behaviour(self):
        result = self.run_cli('backup_prod', available=GIB, database=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('free disk space was not checked', result.stdout)

    def test_retention_keeps_newest_and_pending_upgrade_archives(self):
        names = [f'assozeta-2026100{day}T000000Z.tar.gz' for day in range(1, 6)]
        for name in names + ['notes.txt']:
            (self.backups / name).write_text('x')
        pending = self.root / '.updater/pending-distribution'
        pending.mkdir(parents=True)
        (pending / 'transaction.json').write_text(f'{{"backup": {{"path": "{self.backups / names[0]}"}}}}')
        result = self.run_cli(f'destination="{self.backups}"; backup_prune')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in self.backups.iterdir()),
                         sorted([names[0], names[3], names[4], 'notes.txt']))

    def test_retention_is_configurable_and_skips_custom_destinations(self):
        names = [f'assozeta-2026100{day}T000000Z.tar.gz' for day in range(1, 4)]
        for name in names:
            (self.backups / name).write_text('x')
        custom = self.root / 'custom'
        custom.mkdir()
        for name in names:
            (custom / name).write_text('x')
        self.assertEqual(self.run_cli(f'destination="{custom}"; backup_prune').returncode, 0)
        self.assertEqual(len(list(custom.iterdir())), 3)
        self.assertEqual(self.run_cli(f'destination="{self.backups}"; backup_prune',
                                      ASSOZETA_BACKUP_KEEP='0').returncode, 0)
        self.assertEqual(len(list(self.backups.iterdir())), 3)
        self.env.write_text(self.env.read_text() + 'ASSOZETA_BACKUP_KEEP=1\n')
        self.assertEqual(self.run_cli(f'destination="{self.backups}"; backup_prune').returncode, 0)
        self.assertEqual([path.name for path in self.backups.iterdir()], [names[2]])


if __name__ == '__main__':
    unittest.main()
