"""Portable controls for owned Caddy container cleanup; Docker is never launched."""
import io
import subprocess
import unittest
from unittest.mock import patch

from TESTS.adapters import caddy


class CaddyCleanupControls(unittest.TestCase):
    def exercise(self, *, fail_logs=False, body_error=None):
        calls = []
        caught = None

        def run(args, **kwargs):
            calls.append((list(args), kwargs))
            if args[1] == 'logs' and fail_logs:
                raise subprocess.TimeoutExpired(args, timeout=15)
            stdout = b''
            if args[1] == 'run':
                stdout = b'container-id'
            elif args[1] == 'port':
                stdout = b'127.0.0.1:43210'
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b'')

        with patch.object(caddy.subprocess, 'run', side_effect=run), \
                patch.object(caddy, 'poll', return_value=True):
            try:
                with caddy.proxy(
                        'django:8000', 'go:8000', io.StringIO()) as runtime:
                    self.assertEqual(runtime[0], 'http://127.0.0.1:43210')
                    if body_error is not None:
                        raise body_error
            except BaseException as error:
                caught = error
        return calls, caught

    def cleanup_positions(self, calls):
        commands = [args for args, _kwargs in calls]
        self.assertEqual(sum(args[1] == 'logs' for args in commands), 1)
        self.assertEqual(sum(args[1] == 'rm' for args in commands), 1)
        log_index = next(
            index for index, args in enumerate(commands) if args[1] == 'logs')
        remove_index = next(
            index for index, args in enumerate(commands) if args[1] == 'rm')
        self.assertEqual(commands[remove_index][:3], ['docker', 'rm', '-f'])
        self.assertIs(calls[remove_index][1].get('check'), True)
        self.assertEqual(calls[remove_index][1].get('timeout'), 30)
        self.assertLess(log_index, remove_index)
        name = commands[log_index][2]
        self.assertEqual(commands[remove_index][3], name)
        return commands

    def test_normal_exit_attempts_logs_then_owned_removal(self):
        calls, caught = self.exercise()
        self.assertIsNone(caught)
        commands = self.cleanup_positions(calls)
        self.assertEqual(commands[-2][:2], ['docker', 'logs'])
        self.assertEqual(commands[-1][:3], ['docker', 'rm', '-f'])

    def test_log_timeout_still_attempts_owned_removal(self):
        calls, caught = self.exercise(fail_logs=True)
        self.assertIsInstance(caught, subprocess.TimeoutExpired)
        self.cleanup_positions(calls)

    def test_body_exception_still_attempts_logs_and_owned_removal(self):
        error = RuntimeError('body failed')
        calls, caught = self.exercise(body_error=error)
        self.assertIs(caught, error)
        self.cleanup_positions(calls)


if __name__ == '__main__':
    unittest.main()
