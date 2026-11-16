"""Offline checks for overlapping the MCP/agent evaluation in `manuale.evaluate`.

No Docker: management/Compose and browser commands are recorded instead of
executed. The buffered evaluation log is exercised for real with python3 -c,
either directly or behind a fake subprocess that waits for the embedded run.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'manuale.py'
spec = importlib.util.spec_from_file_location('manuale_evaluate_parallel', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

EVALUATION = 'Exercise manual retrieval and cited answers through existing MCP and agent'
EMBEDDED = 'Verify sidebar, header, embedded steps and screenshots against the real backend'
ASSOCIATION = 'fictional-association'
THREAD = 'manuale-mcp-evaluation'


class EvaluateParallelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.application = self.root / 'application'
        self.application.mkdir()
        self.state = {'run': str(self.run), 'application': str(self.application), 'status': 'running'}
        (self.run / 'run.json').write_text(json.dumps(self.state))
        (self.run / 'browser-input.json').write_text(json.dumps({'association_id': ASSOCIATION}))
        self.commands = []
        self.failures = {}
        self.hooks = {}
        self.evaluation_command = ['manage', 'evaluate_manuale', '--association-id', ASSOCIATION,
                                   '--output', '/manual-run/evaluation.json']

    def fake_execute(self, command, run, label, *, cwd=None, env=None, input=None):
        self.commands.append((label, list(command), env, cwd))
        hook = self.hooks.get(label)
        if hook is not None:
            hook()
        if label in self.failures:
            raise self.failures[label]

    def fake_management(self, state, *args, reuse=False):
        return list(self.evaluation_command)

    def labels(self):
        return [label for label, _, _, _ in self.commands]

    def command_for(self, label):
        return [command for name, command, _, _ in self.commands if name == label][0]

    def environment_for(self, label):
        return [environment for name, _, environment, _ in self.commands if name == label][0]

    def status(self):
        path = self.run / 'run.json'
        return json.loads(path.read_text())['status'] if path.exists() else None

    def timings(self):
        return json.loads((self.run / 'timings.json').read_text())['events']

    def execution_log(self):
        return (self.run / 'execution.log').read_text()

    def evaluation_threads(self):
        return [thread for thread in threading.enumerate() if thread.name == THREAD]

    def run_evaluate(self, *, parallel=True, extra_patches=()):
        environment = {key: value for key, value in os.environ.items() if key != 'MANUALE_PARALLEL_EVALUATION'}
        if parallel is not None:
            environment['MANUALE_PARALLEL_EVALUATION'] = '1' if parallel else '0'
        patches = [
            patch.dict(os.environ, environment, clear=True),
            patch.object(runner, 'execute', self.fake_execute),
            patch.object(runner, 'management', self.fake_management),
            *extra_patches,
        ]
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                runner.evaluate(self.state)
        return output.getvalue()

    def test_default_is_serial(self):
        self.run_evaluate(parallel=None)
        self.assertEqual(self.labels(), [EVALUATION, EMBEDDED])
        self.assertEqual(self.evaluation_threads(), [])
        self.assertEqual(self.status(), 'evaluated')

    def test_serial_mode_keeps_the_historical_command_order(self):
        self.run_evaluate(parallel=False)
        self.assertEqual(self.labels(), [EVALUATION, EMBEDDED])
        self.assertEqual(self.command_for(EVALUATION), self.evaluation_command)
        self.assertEqual(self.command_for(EMBEDDED)[1:], [
            str(self.application / 'selfhost/tests/browser/manuale/embedded.mjs')])
        self.assertEqual(self.environment_for(EMBEDDED)['ASSOZETA_MANUAL_RUN'], str(self.run))
        self.assertEqual(self.evaluation_threads(), [])
        # Nothing was buffered: the historical path writes no private logs.
        self.assertFalse((self.run / 'timings.json').exists())
        self.assertFalse((self.run / 'execution.log').exists())
        self.assertFalse(list(self.run.glob('.buffered-*.log')))
        self.assertEqual(self.status(), 'evaluated')

    def test_parallel_mode_overlaps_the_evaluation_with_the_embedded_run(self):
        evaluation_started, embedded_done, order = threading.Event(), threading.Event(), []

        def fake_run(command, cwd=None, env=None, stdout=None, stderr=None, input=None):
            self.assertEqual(list(command), self.evaluation_command)
            if embedded_done.is_set():
                raise AssertionError('the evaluation started after the embedded run finished')
            order.append('evaluation-start')
            self.assertNotEqual(self.status(), 'evaluated')
            stdout.write(b'mcp evaluation output\n')
            evaluation_started.set()
            if not embedded_done.wait(5):
                raise AssertionError('the embedded run never overlapped the evaluation')
            order.append('evaluation-end')
            self.assertNotEqual(self.status(), 'evaluated')
            return SimpleNamespace(returncode=0)

        def embedded():
            order.append('embedded-start')
            self.assertNotEqual(self.status(), 'evaluated')
            # A serial implementation never starts the evaluation here; time out instead of hanging.
            if not evaluation_started.wait(5):
                raise AssertionError('the evaluation did not run while the embedded check was running')
            embedded_done.set()

        self.hooks[EMBEDDED] = embedded
        with patch.object(subprocess, 'run', fake_run):
            output = self.run_evaluate()

        self.assertEqual(self.labels(), [EMBEDDED])
        self.assertIn('embedded-start', order)
        self.assertLess(order.index('evaluation-start'), order.index('evaluation-end'))
        self.assertEqual(output.count(EVALUATION), 1)
        self.assertEqual(self.evaluation_threads(), [])
        self.assertFalse(list(self.run.glob('.buffered-*.log')))
        # status is published only after both checks have finished.
        self.assertEqual(self.status(), 'evaluated')

    def test_evaluation_failure_raises_after_the_embedded_run(self):
        self.evaluation_command = [sys.executable, '-c', 'print("evaluation boom"); raise SystemExit(7)']
        with self.assertRaisesRegex(RuntimeError, rf'{EVALUATION} failed \(7\); inspect .*execution\.log'):
            self.run_evaluate()
        self.assertEqual(self.labels(), [EMBEDDED])
        self.assertEqual(self.evaluation_threads(), [])
        self.assertNotEqual(self.status(), 'evaluated')
        log = self.execution_log()
        self.assertEqual(log.count(EVALUATION), 1)
        self.assertIn('evaluation boom', log)
        self.assertEqual([(event['name'], event['status']) for event in self.timings()], [(EVALUATION, 'failed')])
        self.assertFalse(list(self.run.glob('.buffered-*.log')))

    def test_embedded_failure_wins_and_joins_the_evaluation(self):
        self.evaluation_command = [sys.executable, '-c', 'import time; time.sleep(0.4); print("late evaluation")']
        self.failures[EMBEDDED] = RuntimeError('embedded boom')
        with self.assertRaisesRegex(RuntimeError, '^embedded boom$'):
            self.run_evaluate()
        self.assertEqual(self.evaluation_threads(), [])
        self.assertNotEqual(self.status(), 'evaluated')
        # The published log proves evaluate waited for the evaluation before raising.
        self.assertIn('late evaluation', self.execution_log())
        self.assertEqual([(event['name'], event['status']) for event in self.timings()], [(EVALUATION, 'passed')])
        self.assertFalse(list(self.run.glob('.buffered-*.log')))

    def test_evaluation_log_and_timing_are_published_exactly_once(self):
        self.evaluation_command = [sys.executable, '-c', 'print("evaluation ok")']
        self.run_evaluate()
        log = self.execution_log()
        self.assertEqual(log.count(EVALUATION), 1)
        self.assertEqual(log.count('evaluation ok'), 1)
        events = self.timings()
        self.assertEqual([event['name'] for event in events], [EVALUATION])
        self.assertEqual(events[0]['kind'], 'command')
        self.assertEqual(events[0]['status'], 'passed')
        self.assertIsInstance(events[0]['elapsed_seconds'], float)
        self.assertFalse(list(self.run.glob('.buffered-*.log')))
        self.assertEqual(self.status(), 'evaluated')


if __name__ == '__main__':
    unittest.main()
