"""Failure artifacts must not publish secrets, error strings or private downloads."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('manual_diagnostics', ROOT / 'docs/scripts/manuale-diagnostics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ManualDiagnosticsTests(unittest.TestCase):
    def test_summary_preserves_failure_status_without_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'tags-create-assign.json').write_text(json.dumps({
                'status': 'failed', 'error': 'token=SECRET', 'password': 'SECRET',
                'screenshots': [{'caption': 'SECRET', 'path': 'private/SECRET.png'}]}))
            (run / 'browser-input.json').write_text('{"token":"SECRET"}')
            (run / 'private-export.zip').write_bytes(b'SECRET')
            result = module.summarize(run)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(result['reports'][0]['status'], 'failed')
            self.assertEqual(result['reports'][0]['checkpoint_count'], 1)
            self.assertEqual(len(result['reports'][0]['report_sha256']), 64)

    def test_symlinked_report_and_malformed_report_do_not_leak_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / 'run'
            run.mkdir()
            private = Path(directory) / 'private.json'
            private.write_text('{"status":"failed","error":"SECRET"}')
            (run / 'render.json').symlink_to(private)
            (run / 'evaluation.json').write_text('SECRET malformed data')
            result = module.summarize(run)
            self.assertEqual([item['id'] for item in result['reports']], ['evaluation'])
            self.assertEqual(result['reports'][0]['status'], 'invalid-report')
            self.assertNotIn('SECRET', json.dumps(result))
