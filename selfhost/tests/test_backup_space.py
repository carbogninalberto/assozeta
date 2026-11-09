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
id() { printf '%s\n' "$TEST_UID"; }
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

    def run_cli(self, body, available=100 * GIB, database=GIB, objects=2 * GIB, gnu_tar='0', uid='0', **env):
        return subprocess.run(['sh', '-c', DEFINITIONS + STUBS + body], capture_output=True, text=True, env={
            **os.environ, 'ASSOZETA_INSTALL_ROOT': str(self.root), 'ASSOZETA_ENV_FILE': str(self.env),
            'TEST_LOG': str(self.log), 'TEST_AVAILABLE_KIB': str(available // 1024),
            'TEST_DATABASE_BYTES': str(database) if database is not None else '',
            'TEST_OBJECT_BYTES': str(objects), 'TEST_GNU_TAR': gnu_tar, 'TEST_UID': uid, **env,
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
        self.assertEqual(sorted(path.name for path in self.backups.iterdir()),
                         [archives[0].name, archives[0].name + '.meta'])

    def test_non_root_users_cannot_delete_staged_objects_and_need_two_copies(self):
        result = self.run_cli('backup_prod', available=5 * GIB, gnu_tar='1', uid='1000')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('7168 MB required', result.stderr)

    def test_unknown_size_keeps_previous_behaviour(self):
        result = self.run_cli('backup_prod', available=GIB, database=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('free disk space was not checked', result.stdout)

    def archives(self, origins, directory=None, extra=None):
        """Create one archive per day, with a sidecar unless the origin is None."""
        directory = directory or self.backups
        names = []
        for day, origin in enumerate(origins, start=1):
            name = f'assozeta-202610{day:02d}T000000Z.tar.gz'
            (directory / name).write_text('x')
            if origin is not None:
                fields = {'origin': origin, **(extra or {}).get(day, {})}
                (directory / (name + '.meta')).write_text(
                    ''.join(f'{key}={value}\n' for key, value in fields.items()))
            names.append(name)
        return names

    def remaining(self, directory=None):
        return sorted(path.name for path in (directory or self.backups).glob('*.tar.gz'))

    def prune(self, **env):
        result = self.run_cli(f'destination="{self.backups}"; backup_prune', **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_backup_records_its_origin(self):
        result = self.run_cli('backup_prod')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli('backup_origin=pre-upgrade; backup_prod')
        self.assertEqual(result.returncode, 0, result.stderr)
        metas = sorted(self.backups.glob('assozeta-*.tar.gz.meta'))
        self.assertEqual(len(metas), 2)
        self.assertEqual(sorted(meta.read_text().splitlines()[0] for meta in metas),
                         ['origin=manual', 'origin=pre-upgrade'])
        self.assertIn('version=1.0.3\n', metas[0].read_text())
        self.assertEqual(metas[0].stat().st_mode & 0o777, 0o600)

    def test_rotation_keeps_newest_automatic_and_never_manual_or_legacy(self):
        origins = [None, 'manual', 'pre-upgrade', 'scheduled', 'pre-upgrade', 'manual',
                   'pre-restore', 'scheduled', 'pre-upgrade', 'scheduled']
        names = self.archives(origins)
        (self.backups / 'notes.txt').write_text('x')
        result = self.prune()
        self.assertEqual(self.remaining(), sorted(names[i] for i in (0, 1, 5, 6, 7, 8, 9)))
        self.assertFalse((self.backups / (names[2] + '.meta')).exists())
        self.assertTrue((self.backups / 'notes.txt').exists())
        self.assertIn('removed old automatic backup ' + names[4], result.stdout)

    def test_manual_backup_survives_repeated_upgrade_attempts(self):
        names = self.archives(['manual'] + ['pre-upgrade'] * 6)
        self.prune()
        self.assertEqual(self.remaining(), sorted([names[0], *names[4:]]))

    def test_kept_last_successful_and_pending_upgrade_archives_are_protected(self):
        names = self.archives(['pre-upgrade'] * 7, extra={
            1: {'keep': 'true'}, 2: {'upgrade': 'completed'}, 3: {'upgrade': 'completed'}})
        pending = self.root / '.updater/pending-distribution'
        pending.mkdir(parents=True)
        (pending / 'transaction.json').write_text(f'{{"backup": {{"path": "{self.backups / names[3]}"}}}}')
        self.prune()
        # Day 1 is kept and uses no automatic slot, day 3 is the last
        # successful upgrade and day 4 belongs to the pending transaction.
        self.assertEqual(self.remaining(), sorted([names[0], *names[2:]]))
        self.assertFalse((self.backups / names[1]).exists())

    def test_successful_upgrade_marks_its_backup_and_moves_the_protection(self):
        names = self.archives(['pre-upgrade'] * 5, extra={1: {'upgrade': 'completed'}})
        result = self.run_cli(f'destination="{self.backups}"; '
                              f'backup_mark_upgrade_completed "{self.backups / names[4]}"; backup_prune')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('upgrade=completed\n', (self.backups / (names[4] + '.meta')).read_text())
        self.assertEqual(self.remaining(), sorted(names[2:]))

    def test_retention_is_configurable_and_skips_custom_destinations(self):
        custom = self.root / 'custom'
        custom.mkdir()
        self.archives(['scheduled'] * 5, directory=custom)
        names = self.archives(['scheduled'] * 5)
        self.assertEqual(self.run_cli(f'destination="{custom}"; backup_prune').returncode, 0)
        self.assertEqual(len(self.remaining(custom)), 5)
        self.prune(ASSOZETA_BACKUP_KEEP='0')
        self.assertEqual(len(self.remaining()), 5)
        self.prune(ASSOZETA_BACKUP_KEEP='x')
        self.assertEqual(len(self.remaining()), 5)
        self.env.write_text(self.env.read_text() + 'ASSOZETA_BACKUP_KEEP=1\n')
        self.prune()
        self.assertEqual(self.remaining(), [names[4]])

    # Free space grows by TEST_EACH_KIB for every archive removed since the start.
    SPACE = """
df() {
    count=$(ls "$destination" | grep -c '\\.tar\\.gz$')
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/x 1 1 %s 1%% /\\n' $(( TEST_BASE_KIB + (TEST_INITIAL - count) * TEST_EACH_KIB ))
}
"""

    def free(self, needed_gib, base_gib, each_gib, initial):
        return self.run_cli(self.SPACE + f'destination="{self.backups}"; backup_free_space {needed_gib * 1024 * 1024}; df',
                            TEST_BASE_KIB=str(base_gib * 1024 * 1024), TEST_EACH_KIB=str(each_gib * 1024 * 1024),
                            TEST_INITIAL=str(initial))

    def test_scheduled_backups_record_their_origin_and_rotate(self):
        self.archives(['scheduled'] * 3)
        result = self.run_cli('backup_prod --scheduled', ASSOZETA_BACKUP_KEEP='2')
        self.assertEqual(result.returncode, 0, result.stderr)
        newest = self.remaining()[-1]
        self.assertTrue((self.backups / (newest + '.meta')).read_text().startswith('origin=scheduled\n'))
        self.assertEqual(len(self.remaining()), 2)
        self.assertNotEqual(self.run_cli('backup_prod --weekly').returncode, 0)

    def test_pre_upgrade_backup_waits_for_the_upgrade_before_rotating(self):
        self.archives(['scheduled'] * 4)
        result = self.run_cli('backup_origin=pre-upgrade; backup_prod', ASSOZETA_BACKUP_KEEP='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.remaining()), 5)

    def test_runner_stages_are_printed_only_when_requested(self):
        quiet = self.run_cli('backup_prod')
        self.assertNotIn('@@ASSOZETA_STAGE', quiet.stdout)
        staged = self.run_cli('backup_prod', ASSOZETA_BACKUP_STAGES='1')
        self.assertIn('@@ASSOZETA_STAGE backup\n', staged.stdout)
        self.assertIn('@@ASSOZETA_STAGE restarting\n', staged.stdout)

    def test_low_space_removes_uploaded_copies_then_oldest_automatic_backups(self):
        # 1 uploaded manual, 2 scheduled, 3 kept scheduled, 4 manual, 5 last upgrade, 6 scheduled, 7 newest scheduled.
        names = self.archives(['manual', 'scheduled', 'scheduled', 'manual', 'pre-upgrade', 'scheduled', 'scheduled'],
                              extra={1: {'remote': 'uploaded'}, 3: {'keep': 'true'}, 5: {'upgrade': 'completed'}})
        result = self.free(needed_gib=3, base_gib=0, each_gib=1, initial=7)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The uploaded copy goes first, then the oldest unprotected automatic
        # archives (days 2 and 6): kept day 3, last upgrade day 5 and the newest stay.
        self.assertEqual(self.remaining(), sorted(names[i] for i in (2, 3, 4, 6)))
        self.assertFalse((self.backups / (names[0] + '.meta')).exists())
        self.assertIn('removed ' + names[0], result.stdout)

    def test_low_space_never_removes_protected_archives(self):
        names = self.archives(['manual', 'scheduled', 'pre-upgrade', 'scheduled'],
                              extra={2: {'keep': 'true'}, 3: {'upgrade': 'completed'}})
        result = self.free(needed_gib=100, base_gib=0, each_gib=1, initial=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Manual, kept, last upgrade and the newest archive all stay.
        self.assertEqual(self.remaining(), names)

    def test_backup_fails_with_a_clear_reason_when_cleanup_is_not_enough(self):
        self.archives(['scheduled', 'scheduled'])
        result = self.run_cli(self.SPACE + f'destination="{self.backups}"; backup_prod',
                              TEST_BASE_KIB=str(2 * 1024 * 1024), TEST_EACH_KIB=str(1024 * 1024), TEST_INITIAL='2')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('anche dopo aver eliminato i backup automatici più vecchi', result.stdout)
        # The oldest automatic archive went; the newest always stays.
        self.assertEqual(len(self.remaining()), 1)

    # restore_prod itself needs Docker: record how it is called instead.
    RESTORE = """
restore_prod() { printf 'restore_prod %s safety=%s\\n' "$*" "${restore_safety_stopped:-}"; }
prod_app_services_running() { return 0; }
"""

    def test_restore_takes_a_pre_restore_backup_first_and_keeps_its_source(self):
        names = self.archives(['scheduled'] * 3)
        source = self.backups / names[0]
        result = self.run_cli(self.RESTORE + f'restore_command_prod "{source}" --yes', ASSOZETA_BACKUP_KEEP='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith(('@@', 'restore_prod'))]
        safety = sorted(set(self.remaining()) - set(names))
        self.assertEqual(len(safety), 1)
        self.assertEqual(lines, ['@@ASSOZETA_STAGE checking', '@@ASSOZETA_STAGE backup', f'@@ASSOZETA_BACKUP {safety[0]}',
                                 '@@ASSOZETA_STAGE restoring', f'restore_prod {source} --yes safety=true'])
        self.assertTrue((self.backups / (safety[0] + '.meta')).read_text().startswith('origin=pre-restore\n'))
        # No rotation right before the restore: the archive to restore stays.
        self.assertEqual(sorted(set(self.remaining()) & set(names)), names)

    def test_restore_space_check_never_removes_the_archive_to_restore(self):
        names = self.archives(['scheduled', 'scheduled', 'manual'])
        result = self.run_cli(self.RESTORE + self.SPACE + f'destination="{self.backups}"; backup_protected={names[0]}; backup_free_space {50 * 1024 * 1024}',
                              TEST_BASE_KIB='0', TEST_EACH_KIB=str(1024 * 1024), TEST_INITIAL='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remaining(), [names[0], names[2]])

    def test_restore_without_backup_or_archive(self):
        names = self.archives(['manual'])
        result = self.run_cli(self.RESTORE + f'restore_command_prod "{self.backups / names[0]}" --yes --no-backup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('@@ASSOZETA_STAGE backup', result.stdout)
        self.assertEqual(self.remaining(), names)
        self.assertNotEqual(self.run_cli(self.RESTORE + 'restore_command_prod --yes').returncode, 0)
        self.assertNotEqual(self.run_cli(self.RESTORE + f'restore_command_prod "{self.root}/missing.tar.gz" --yes').returncode, 0)
        self.assertNotEqual(self.run_cli(self.RESTORE + f'restore_command_prod "{self.backups / names[0]}" --force').returncode, 0)


if __name__ == '__main__':
    unittest.main()
