"""Deterministic offline cancellation controls; no actual signals/services/sockets."""
import json
from pathlib import Path
import runpy
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.go import message_image_execution as execution


class ExecutionControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'runner'
        self.handlers = {}
        self.old = {sig: object() for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM)}
        def install(sig, handler):
            self.handlers[sig] = handler
        for target, options in (
            ('signal', {'side_effect': install}),
            ('getsignal', {'side_effect': self.old.__getitem__}),
            ('getitimer', {'return_value': (0.0, 0.0)}),
            ('setitimer', {}),
        ):
            mocked = patch.object(execution.signal, target, **options)
            setattr(self, target, mocked.start())
            self.addCleanup(mocked.stop)

    def argv(self):
        return ['--output', str(self.output)]

    def runner(self, interrupt=None, cleanup_failure=False, swallow=False):
        def run(argv):
            self.output.mkdir()
            report = {'success': False, 'cleanup_verified': False, 'errors': [], 'cleanup_errors': []}
            path = self.output / 'results.json'
            path.write_text(json.dumps(report))
            try:
                if interrupt is not None:
                    try:
                        self.handlers[interrupt](interrupt, None)
                    except execution.ExecutionInterrupted:
                        if not swallow:
                            raise
                report['success'] = True
                return 0
            finally:
                # Repeated cancellation must not interrupt existing cleanup.
                if interrupt is not None:
                    self.handlers[signal.SIGTERM](signal.SIGTERM, None)
                report['cleanup_verified'] = not cleanup_failure
                if cleanup_failure:
                    report['cleanup_errors'].append('fixture.cleanup-failed')
                path.write_text(json.dumps(report))
                self.cleaned = True
        return run

    def test_normal_invocation_preserves_runner_args_receipt_and_restores_handlers(self):
        runner = Mock(wraps=self.runner())
        self.assertEqual(execution.run_packaged(self.argv(), runner=runner), 0)
        runner.assert_called_once_with(self.argv())
        self.assertTrue(self.cleaned)
        self.assertTrue(json.loads((self.output / 'results.json').read_text())['success'])
        for sig, previous in self.old.items():
            self.assertIs(self.handlers[sig], previous)
        self.assertIn(unittest.mock.call(signal.ITIMER_REAL, 1500), self.setitimer.call_args_list)
        self.assertEqual(self.setitimer.call_args_list[-1].args, (signal.ITIMER_REAL, 0))

    def test_deadline_sigint_sigterm_unwind_cleanup_and_force_failure_even_if_swallowed(self):
        for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
            for swallow in (False, True):
                with self.subTest(sig=sig, swallow=swallow):
                    self.output = Path(self.directory.name) / f'runner-{sig}-{swallow}'
                    self.assertEqual(execution.run_packaged(self.argv(), runner=self.runner(sig, swallow=swallow)), 1)
                    self.assertTrue(self.cleaned)
                    receipt = json.loads((self.output / 'results.json').read_text())
                    self.assertFalse(receipt['success'])
                    self.assertTrue(receipt['cleanup_verified'])
                    self.assertFalse(receipt['execution']['escalated'])
                    self.assertIn(unittest.mock.call(signal.ITIMER_REAL, 180), self.setitimer.call_args_list)

    def test_cleanup_failure_remains_failure(self):
        self.assertEqual(execution.run_packaged(self.argv(), runner=self.runner(signal.SIGTERM, True)), 1)
        receipt = json.loads((self.output / 'results.json').read_text())
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['cleanup_verified'])
        self.assertEqual(receipt['cleanup_errors'], ['fixture.cleanup-failed'])

    def test_grace_expiry_marks_cleanup_unverified_before_hard_exit(self):
        def hung(argv):
            self.output.mkdir()
            (self.output / 'results.json').write_text(json.dumps({'success': True, 'cleanup_verified': True}))
            try:
                self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            finally:
                self.handlers[signal.SIGALRM](signal.SIGALRM, None)
        with patch.object(execution.os, '_exit', side_effect=SystemExit(124)) as hard_exit:
            self.assertEqual(execution.run_packaged(self.argv(), runner=hung), 1)
        hard_exit.assert_called_once_with(124)
        receipt = json.loads((self.output / 'results.json').read_text())
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['cleanup_verified'])
        self.assertTrue(receipt['execution']['escalated'])

    def test_runner_failure_exception_and_stale_output_never_become_success(self):
        self.assertEqual(execution.run_packaged(self.argv(), runner=Mock(return_value=7)), 1)
        self.assertEqual(execution.run_packaged(self.argv(), runner=Mock(side_effect=RuntimeError('private'))), 1)
        self.output.mkdir()
        receipt = self.output / 'results.json'
        receipt.write_bytes(b'prior-receipt')
        runner = Mock()
        with self.assertRaises(FileExistsError):
            execution.run_packaged(self.argv(), runner=runner)
        runner.assert_not_called()
        self.assertEqual(receipt.read_bytes(), b'prior-receipt')

    def test_invalid_or_unbounded_deadlines_and_existing_alarm_refused(self):
        for value in (0, -1, float('inf'), float('nan'), 3601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                execution.run_packaged(self.argv(), deadline=value, runner=Mock())
        self.getitimer.return_value = (1.0, 0.0)
        with self.assertRaisesRegex(RuntimeError, 'execution.alarm-in-use'):
            execution.run_packaged(self.argv(), runner=Mock())
        self.signal.assert_not_called()

    def test_short_ownership_transition_defers_delivery_and_keeps_grace_bounded(self):
        control = execution.ExecutionDeadline(20, 7)
        assigned = False
        with control:
            with self.assertRaises(execution.ExecutionInterrupted):
                with execution.defer_interruption():
                    self.handlers[signal.SIGTERM](signal.SIGTERM, None)
                    assigned = True
                    self.handlers[signal.SIGINT](signal.SIGINT, None)
            self.assertTrue(assigned)
            self.assertTrue(control.interrupted)
            self.assertEqual(control.reason, 'sigterm')
        self.assertEqual(self.setitimer.call_args_list,
                         [unittest.mock.call(signal.ITIMER_REAL, 20),
                          unittest.mock.call(signal.ITIMER_REAL, 7),
                          unittest.mock.call(signal.ITIMER_REAL, 0)])

    def test_normal_cleanup_has_its_own_deadline_and_defers_first_term(self):
        escalated = Mock()
        control = execution.ExecutionDeadline(20, 7, on_escalation=escalated)
        with control:
            control.begin_cleanup()
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(control.interrupted)
            self.handlers[signal.SIGINT](signal.SIGINT, None)
            self.assertEqual(control.reason, 'sigterm')
            with patch.object(execution.os, '_exit', side_effect=SystemExit(124)), self.assertRaises(SystemExit):
                self.handlers[signal.SIGALRM](signal.SIGALRM, None)
        self.assertTrue(control.escalated)
        escalated.assert_called_once_with(control)

    def test_runner_cleanup_handoff_is_inert_without_an_active_controller(self):
        execution.begin_cleanup()
        self.signal.assert_not_called()
        self.setitimer.assert_not_called()

    def test_runner_cleanup_retains_deadline_then_uses_one_grace_for_all_signals(self):
        for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                self.setitimer.reset_mock()
                escalated = Mock()
                control = execution.ExecutionDeadline(20, 7, on_escalation=escalated)
                with control:
                    execution.begin_cleanup()
                    execution.begin_cleanup()
                    self.setitimer.assert_called_once_with(signal.ITIMER_REAL, 20)
                    with execution.defer_interruption():
                        self.handlers[sig](sig, None)
                    self.assertTrue(control.interrupted)
                    self.assertTrue(control.delivered)
                    for _ in range(3):
                        execution.begin_cleanup()
                        self.handlers[signal.SIGTERM](signal.SIGTERM, None)
                        self.handlers[signal.SIGINT](signal.SIGINT, None)
                    # Includes the wrapper's final handoff; none restarts grace.
                    control.begin_cleanup()
                    self.assertEqual(self.setitimer.call_args_list,
                        [unittest.mock.call(signal.ITIMER_REAL, 20),
                         unittest.mock.call(signal.ITIMER_REAL, 7)])
                    with patch.object(execution.os, '_exit', side_effect=SystemExit(124)) as hard_exit, \
                         patch.object(execution.os, 'write'), self.assertRaises(SystemExit):
                        self.handlers[signal.SIGALRM](signal.SIGALRM, None)
                    hard_exit.assert_called_once_with(124)
                self.assertTrue(control.escalated)
                escalated.assert_called_once_with(control)
                self.assertEqual(self.setitimer.call_args_list[-1].args, (signal.ITIMER_REAL, 0))

    def test_runner_cleanup_without_cancellation_does_not_replace_execution_budget(self):
        with execution.ExecutionDeadline(20, 7) as control:
            execution.begin_cleanup()
            execution.begin_cleanup()
            self.assertFalse(control.interrupted)
        self.assertEqual(self.setitimer.call_args_list,
                         [unittest.mock.call(signal.ITIMER_REAL, 20),
                          unittest.mock.call(signal.ITIMER_REAL, 0)])

    def test_module_entry_and_runner_import_share_the_cleanup_controller(self):
        from TESTS.adapters.go import message_read_scopes_image as image
        finished = []

        def runner(argv):
            self.output.mkdir()
            (self.output / 'results.json').write_text(json.dumps({'success': True}))
            execution.begin_cleanup()
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            finished.append('cleanup')
            return 0

        with patch.object(image, 'main', new=runner), \
             patch.object(sys, 'argv', ['message_image_execution', '--', *self.argv()]), \
             self.assertRaises(SystemExit) as stopped:
            # Like -m, execute in __main__ separately from the imported module.
            runpy.run_path(execution.__file__, run_name='__main__')
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(finished, ['cleanup'])
        self.assertFalse(json.loads((self.output / 'results.json').read_text())['success'])

    def test_cli_forwards_only_runner_arguments_and_propagates_failure(self):
        with patch.object(execution, 'run_packaged', return_value=1) as run:
            self.assertEqual(execution.main(['--deadline', '12', '--cleanup-grace', '8', '--',
                                             '--image', 'fixture', *self.argv()]), 1)
        run.assert_called_once_with(['--image', 'fixture', *self.argv()], deadline=12, cleanup_grace=8)


if __name__ == '__main__':
    unittest.main()
