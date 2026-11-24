"""Exercise configure and adoption with two configurations sharing a distribution."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SELFHOST = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SELFHOST / 'updater'), str(SELFHOST.parent / 'BE/instance')]
import runtime_adoption


class InstallMarkerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'distribution'
        self.root.mkdir()
        self.external = Path(directory.name) / 'fresh.env'

    def preflight(self, env):
        with patch('runtime_adoption.job_handoff.compose_store') as store, \
                patch('runtime_adoption.job_handoff.compose_broker'), \
                patch('runtime_adoption.job_handoff._items', return_value=()), \
                patch('runtime_adoption.subprocess.check_output', return_value=''):
            store.return_value.rows.return_value = [{'tablename': 'instance_instanceconfiguration'}]
            store.return_value.scalar.return_value = 'v2'
            return runtime_adoption.preflight(self.root, env, report_inventory=False)

    def test_configuring_an_external_env_preserves_adoption_for_another_installation(self):
        subprocess.run([SELFHOST / 'bin/assozeta', 'configure', '--domain', 'localhost', '--version', 'test'],
                       env={**os.environ, 'ASSOZETA_INSTALL_ROOT': str(self.root),
                            'ASSOZETA_ENV_FILE': str(self.external)}, stdout=subprocess.DEVNULL, check=True)
        self.assertFalse(self.preflight(self.external)['adoption_required'])
        for legacy in (self.external.with_name('legacy.env'), self.root / '.env'):
            with self.subTest(env=legacy.name):
                legacy.touch()
                report = self.preflight(legacy)
                self.assertTrue(report['adoption_required'])
                self.assertEqual(report['requested_runtime'], 'go')

    def test_existing_installation_still_recognizes_its_old_marker(self):
        marker = self.root / '.operations/exclusive-install'
        marker.parent.mkdir()
        marker.touch()
        env = self.root / '.env'
        env.touch()
        self.assertFalse(self.preflight(env)['adoption_required'])
