"""Maintenance stays active through failed startup and public readiness checks."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]


class MaintenanceLifecycleTests(unittest.TestCase):
    def test_marker_covers_stop_start_and_is_retained_on_failure(self):
        for failure in ('none', 'stop', 'start', 'background', 'readiness'):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                script = DEFINITIONS + r'''
prod_compose() {
    test -f "$MAINTENANCE_FLAG" || exit 90
    printf 'compose %s\n' "$*"
    case "$TEST_FAILURE:$1" in stop:stop|start:up) return 7 ;; esac
}
require_background_services_running() {
    test -f "$MAINTENANCE_FLAG" || exit 91
    printf 'background\n'
    test "$TEST_FAILURE" != background
}
wait_public_readiness() {
    test -f "$MAINTENANCE_FLAG" || exit 92
    printf 'readiness\n'
    test "$TEST_FAILURE" != readiness
}
if stop_prod_data_services; then
    start_prod_app_services
else
    exit 7
fi
'''
                result = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                    env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory, 'TEST_FAILURE': failure})
                marker = Path(directory) / '.operations/maintenance.flag'
                self.assertEqual(result.returncode == 0, failure == 'none', result.stderr)
                self.assertEqual(marker.exists(), failure != 'none')
                self.assertIn('compose stop api worker beat', result.stdout)
                self.assertNotIn('stop web', result.stdout)
                if failure == 'none':
                    self.assertTrue(result.stdout.endswith('background\nreadiness\n'))

    def test_upgrade_reports_health_stage_before_public_readiness_failure(self):
        with TemporaryDirectory() as directory:
            script = DEFINITIONS + r'''
prod_compose() { return 0; }
require_background_services_running() { return 0; }
wait_public_readiness() { printf 'readiness failed\n'; return 1; }
stop_prod_data_services
start_prod_app_services false true
'''
            result = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '@@ASSOZETA_STAGE health_check\nreadiness failed\n')
            self.assertTrue((Path(directory) / '.operations/maintenance.flag').exists())


if __name__ == '__main__':
    unittest.main()
