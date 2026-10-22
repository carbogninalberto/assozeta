"""CLI must drain cooperating Go transactions before taking a database snapshot."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]


class GoTransactionDrainTests(unittest.TestCase):
    def run_cli(self, operation, *, golang=True, failure=False, lease=True):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('DBUSER=contracts\nDBNAME=fixture\n'
                                      'AWS_STORAGE_BUCKET_NAME=fixture\nAWS_LOCATION=media\n')
            harness = r'''
preflight() { :; }
ensure_configuration_values() { :; }
prod_app_services_running() { return 1; }
prod_compose() {
    case "$*" in
        'config --services'|'--profile tools config --services')
            printf 'api\nworker\nbeat\n'
            if [ "$TEST_GO" = true ]; then printf 'api-go\nmigrate-go\n'; fi
            return 0 ;;
    esac
    printf '%s\n' "$*" >> "$TEST_CALLS"
    case "$*" in
        *'postgres psql '*) [ "$TEST_FAILURE" = false ] || return 19 ;;
        *pg_dump*) printf 'private snapshot\n' ;;
    esac
    return 0
}
prod_operations_lock_held=$TEST_LEASE
'''
            result = subprocess.run(['sh', '-c', DEFINITIONS + harness + '\n' + operation],
                env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                     'TEST_GO': str(golang).lower(), 'TEST_FAILURE': str(failure).lower(),
                     'TEST_LEASE': str(lease).lower(), 'TEST_CALLS': str(root / 'calls')},
                capture_output=True, text=True, timeout=10)
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            archives = list((root / 'backups').glob('*.tar.gz'))
            return result, calls, len(archives)

    def test_backup_drains_after_stop_before_snapshot(self):
        result, calls, archives = self.run_cli('backup_prod "$SELFHOST_DIR/backups"')
        self.assertEqual(result.returncode, 0, result.stderr)
        fence = next(i for i, call in enumerate(calls) if 'postgres psql ' in call)
        snapshot = next(i for i, call in enumerate(calls) if 'pg_dump' in call)
        stop = next(i for i, call in enumerate(calls) if call.startswith('stop '))
        self.assertLess(stop, fence)
        self.assertLess(fence, snapshot)
        self.assertEqual(archives, 1)

    def test_failed_drain_never_creates_snapshot_or_archive(self):
        result, calls, archives = self.run_cli('backup_prod "$SELFHOST_DIR/backups"', failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('pg_dump' in call for call in calls))
        self.assertEqual(archives, 0)

    def test_migrations_start_only_after_bounded_transaction_drain(self):
        result, calls, _ = self.run_cli('migrate_prod_schemas')
        self.assertEqual(result.returncode, 0, result.stderr)
        fence = next(i for i, call in enumerate(calls) if 'postgres psql ' in call)
        django = calls.index('--profile tools run --rm --no-deps migrate')
        self.assertLess(fence, django)
        statement = calls[fence]
        for required in ('-X', 'ON_ERROR_STOP=1', 'BEGIN', 'SET LOCAL statement_timeout',
                         'pg_advisory_xact_lock(1095981639,1381192786)', 'COMMIT'):
            self.assertIn(required, statement)

    def test_failed_drain_prevents_both_migrations(self):
        result, calls, _ = self.run_cli('migrate_prod_schemas', failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('run --rm --no-deps migrate' in call for call in calls))

    def test_historical_django_only_backup_needs_no_go_protocol(self):
        result, calls, archives = self.run_cli('backup_prod "$SELFHOST_DIR/backups"', golang=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('postgres psql ' in call for call in calls))
        self.assertTrue(any('pg_dump' in call for call in calls))
        self.assertEqual(archives, 1)

    def test_current_backup_requires_actual_caller_file_lease(self):
        result, calls, archives = self.run_cli('backup_prod "$SELFHOST_DIR/backups"', lease=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('pg_dump' in call for call in calls))
        self.assertEqual(archives, 0)


if __name__ == '__main__':
    unittest.main()
