"""Prerequisite failure must stop before worktrees and preserve retained proof."""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manuale_prerequisite_runner',
    Path(__file__).resolve().parents[1] / 'manuale.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ManualPrerequisiteTests(unittest.TestCase):
    def test_blocked_run_never_prepares_or_changes_retained_state(self):
        report = {'status': 'failed', 'checks': [{'id': 'docker-engine', 'status': 'failed', 'hint': 'Denied'}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            retained = root / 'retained'
            retained.mkdir()
            (retained / 'run.json').write_text('{"status":"evaluated"}\n')
            (retained / 'index.json').write_bytes(b'last-valid-corpus')
            before = {p.name: p.read_bytes() for p in retained.iterdir()}
            for arguments in (['--output', str(root / 'new-run')], ['--run', str(retained)]):
                with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['manuale.py', 'run', *arguments]), \
                        patch.object(runner, 'runtime_check', return_value=report), \
                        patch.object(runner, 'prepare') as prepare, patch.object(runner, 'cleanup') as cleanup, \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        runner.main()
                    self.assertEqual(stopped.exception.code, 2)
                    prepare.assert_not_called()
                    cleanup.assert_not_called()
                    self.assertFalse((root / 'new-run').exists())
                    self.assertEqual({p.name: p.read_bytes() for p in retained.iterdir()}, before)

    def test_probe_timeout_and_permissions_are_collected_without_sensitive_output(self):
        sensitive = 'daemon://credential@example.test'
        results = [subprocess.CompletedProcess([], 0, 'git version 2.50.0', ''),
                   subprocess.CompletedProcess([], 0, '2.5.3', ''),
                   subprocess.CompletedProcess([], 0, 'v22.17.0', ''),
                   subprocess.CompletedProcess([], 1, sensitive, sensitive),
                   subprocess.TimeoutExpired(['docker'], 5, output=sensitive)]
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(runner.subprocess, 'run', side_effect=results), \
                patch.object(runner.socket, 'socket', side_effect=PermissionError('Denied')):
            report = runner.runtime_check(temporary)
        self.assertEqual(report['status'], 'failed')
        by_id = {item['id']: item for item in report['checks']}
        self.assertEqual(by_id['docker-engine']['reason'], 'timeout')
        self.assertEqual(by_id['host-ports']['reason'], 'cannot-listen')
        self.assertEqual(by_id['chromium']['reason'], 'prerequisite-missing')
        self.assertNotIn(sensitive, json.dumps(report))

    def test_doctor_writes_diagnostic_without_creating_a_run(self):
        report = {'status': 'passed', 'purpose': 'runtime-prerequisites-not-workflow-evidence',
                  'checks': [{'id': 'docker-engine', 'status': 'passed'}]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'reports' / 'doctor.json'
            with patch.object(sys, 'argv', ['manuale.py', 'doctor', '--output', str(output)]), \
                    patch.object(runner, 'runtime_check', return_value=report), \
                    patch.object(runner, 'prepare') as prepare, contextlib.redirect_stdout(io.StringIO()):
                runner.main()
            self.assertEqual(json.loads(output.read_text()), report)
            prepare.assert_not_called()
            self.assertFalse((output.parent / 'run.json').exists())


if __name__ == '__main__':
    unittest.main()
