"""Check that the promoted runtime browser suite runs in both UI CI jobs."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RuntimeSettingsCI(unittest.TestCase):
    def test_runtime_project_runs_in_handoff_and_operational_ui_jobs(self):
        project = 'runtime-settings.config.js'
        browser = ROOT / 'selfhost/tests/browser'
        self.assertTrue((browser / 'runtime-settings.spec.js').is_file())
        self.assertIn("testMatch: 'runtime-settings.spec.js'", (browser / project).read_text())
        for workflow in ['handoff-contracts', 'selfhost-quality']:
            with self.subTest(workflow=workflow):
                ci = ROOT / f'.github/workflows/{workflow}.yml'
                self.assertIn(f'playwright test --config {project}', ci.read_text())
