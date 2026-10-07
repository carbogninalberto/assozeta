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

    def test_failure_keeps_script_lines_matchers_and_routes_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'instructor-compensation.json').write_text(json.dumps({
                'status': 'failed',
                'error': 'Error: SECRET label\n\nexpect(received).toBe(expected)\nExpected: "SECRET"\nReceived: "SECRET2"',
                'error_stack': 'Error: SECRET\n    at fn (file:///home/runner/SECRET/application/'
                               'selfhost/tests/browser/manuale/instructor-compensation.mjs:257:31)\n'
                               '    at scenario (/tmp/SECRET/selfhost/tests/browser/manuale/scenario.mjs:84:9)',
                'browser_errors': ['SECRET'],
                'failed_responses': [
                    {'path': '/api/instructor/0f3c2a10-aaaa-4bbb-8ccc-ddddeeeeffff/hours/add', 'status': 403,
                     'method': 'POST', 'identity': 'reader'},
                    {'path': '/api/document/retrieve/abcdefSECRET?token=SECRET', 'status': 404,
                     'method': 'SECRET', 'identity': 'SECRET USER'}],
                'screenshots': []}))
            failure = module.summarize(run)['reports'][0]['failure']
            self.assertNotIn('SECRET', json.dumps(failure))
            self.assertEqual(failure['locations'], [
                {'script': 'selfhost/tests/browser/manuale/instructor-compensation.mjs', 'line': 257},
                {'script': 'selfhost/tests/browser/manuale/scenario.mjs', 'line': 84}])
            self.assertEqual(failure['error'], {'timeout': False, 'matcher': 'toBe'})
            self.assertEqual(failure['failed_responses'][0],
                             {'method': 'POST', 'route': '/api/instructor/:value/hours/add', 'status': 403, 'identity': 'reader'})
            self.assertEqual(failure['failed_responses'][1],
                             {'method': '<other>', 'route': '/api/document/retrieve/:value', 'status': 404, 'identity': '<other>'})
            self.assertEqual(failure['browser_error_count'], 1)

    def test_timeout_action_is_retained_without_selector(self):
        kind = module.error_kind('TimeoutError: locator.click: Timeout 45000ms exceeded.\nwaiting for SECRET')
        self.assertEqual(kind, {'timeout': True, 'action': 'locator.click'})

    def test_transport_error_code_is_retained_without_url(self):
        kind = module.error_kind('Error: apiRequestContext.fetch: read ECONNRESET\nCall log:\n  - GET http://SECRET/api/x')
        self.assertEqual(kind, {'timeout': False, 'action': 'apiRequestContext.fetch', 'transport': 'ECONNRESET'})
        self.assertEqual(module.error_kind('apiRequestContext.fetch: socket hang up')['transport'], 'socket hang up')
