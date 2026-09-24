"""Verify the actual CLI and app share a cross-process POSIX lease, without Docker."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DataRestoreLockTests(unittest.TestCase):
    def test_cli_waits_for_application_and_excludes_new_application_jobs(self):
        self.exercise_shared_lock()

    def test_container_owned_writable_lock_still_serializes_host_commands(self):
        self.exercise_shared_lock(chmod_denied=True)

    def test_chmod_failure_on_restrictive_lock_stops_command(self):
        self.exercise_shared_lock(chmod_denied=True, permissions=0o600)

    def exercise_shared_lock(self, *, chmod_denied=False, permissions=0o666):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = root / '.operations'
            operations.mkdir()
            lock = operations / 'lifecycle.lock'
            # Provide Linux flock semantics on macOS as well. The child's inherited
            # descriptor refers to the same open file description held by the shell.
            flock = root / 'flock'
            flock.write_text(f'#!{sys.executable}\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)\n')
            flock.chmod(0o755)
            check = root / 'check.py'
            check.write_text('''import fcntl,os
with open(os.environ['TEST_OPERATION_LOCK'], 'a') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        print('shared-lease-excluded', flush=True)
    else:
        raise SystemExit('CLI did not hold the application lease')
''')
            definitions = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
            script = definitions + '''
require_no_surviving_lifecycle_helpers() { :; }
backup_prod() { "$TEST_PYTHON" "$TEST_CHECK"; }
run_locked_prod_command backup
'''
            if chmod_denied:
                script = script.replace('run_locked_prod_command backup', '''
chmod() {
    case "$2" in
        "$SELFHOST_DIR/.operations/lifecycle.lock") return 1 ;;
        *) command chmod "$@" ;;
    esac
}
run_locked_prod_command backup
''')
            env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                   'ASSOZETA_INSTALL_ROOT': str(root), 'TEST_PYTHON': sys.executable,
                   'TEST_CHECK': str(check), 'TEST_OPERATION_LOCK': str(lock)}
            with lock.open('a') as handle:
                lock.chmod(permissions)
                inode = lock.stat().st_ino
                fcntl.flock(handle, fcntl.LOCK_EX)
                process = subprocess.Popen(['sh', '-c', script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    if chmod_denied and permissions != 0o666:
                        stdout, stderr = process.communicate(timeout=10)
                        self.assertNotEqual(process.returncode, 0)
                        self.assertNotIn('shared-lease-excluded', stdout)
                        self.assertIn('shared lifecycle lock must be', stderr)
                        return
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.communicate(timeout=0.2)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertIn('shared-lease-excluded', stdout)
                    self.assertEqual(lock.stat().st_ino, inode)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            # Normal completion releases the application lease.
            with lock.open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == '__main__':
    unittest.main()
