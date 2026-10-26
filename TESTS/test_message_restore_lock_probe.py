"""Offline controls only; no Docker, service, network, or Go acceptance."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_restore_lock_probe as probe


CONTAINER = 'a' * 64
IMAGE = 'sha256:3a5e37ac67f74c37fa9f0e7228d2e3f17ef33f5fcc032dd271e6d2119b779022'
IDENTITY = (
    {'id': CONTAINER, 'image': IMAGE, 'pid': 42,
     'started': '2026-10-26T12:00:00.123Z', 'restarts': 0},
    {'pid': 42, 'instance': 123, 'running': True},
    (1, 234),
)
ACQUIRED = b'RESTORE_LOCK_V1\nOPEN\nACQUIRED\nUNLOCKED\nCLOSED\n'
CONTENDED = b'RESTORE_LOCK_V1\nOPEN\nCONTENDED\nCLOSED\n'


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Commands:
    """Existing Commands' bytes-or-exception interface, with a virtual clock."""
    def __init__(self, clock):
        self.clock = clock
        self.cleanup_verified = True
        self.output = ACQUIRED
        self.error = None
        self.duration = .25
        self.calls = []

    def __call__(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        self.clock.now += self.duration
        if self.error is not None:
            raise self.error
        return self.output


class ProbeControls(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.timer = patch.object(probe.time, 'monotonic', self.clock)
        self.timer.start()
        self.addCleanup(self.timer.stop)
        self.commands = Commands(self.clock)
        self.guard_calls = []
        self.identities = []
        self.guard_error = None

        def guard(*, timeout):
            self.guard_calls.append(timeout)
            self.clock.now += .125
            if self.guard_error is not None:
                raise self.guard_error
            return copy.deepcopy(self.identities.pop(0) if self.identities else IDENTITY)

        self.guard = guard
        self.exclusive = probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER, expected_image=IMAGE,
            identity_guard=guard)

    def fails(self, action):
        with self.assertRaises(probe.ProbeError) as caught:
            action()
        self.assertRegex(str(caught.exception), r'^message-restore-lock\.[a-z-]+$')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        return caught.exception

    def test_factory_is_inert(self):
        self.assertEqual(self.commands.calls, [])
        self.assertEqual(self.guard_calls, [])

    def test_acquired_released_and_closed(self):
        self.assertIs(self.exclusive(timeout=2), True)
        argv, timeout = self.commands.calls[0]
        self.assertEqual(argv, ['docker', 'exec', CONTAINER, '/bin/sh', '-c', probe.PROBE_SCRIPT])
        self.assertEqual(timeout, 1.875)
        self.assertEqual(self.guard_calls, [2, 1.625])
        self.assertTrue(self.commands.cleanup_verified)

    def test_idle_busy_twice_then_completed(self):
        for output, expected in ((ACQUIRED, True), (CONTENDED, False),
                                 (CONTENDED, False), (ACQUIRED, True)):
            self.commands.output = output
            self.assertIs(self.exclusive(timeout=2), expected)
        self.assertEqual(len(self.guard_calls), 8)

    def test_invalid_budget_does_no_io(self):
        for value in (None, True, False, '2', 0, -1, float('inf'),
                      float('-inf'), float('nan'), 21, 10**1000):
            with self.subTest(value=repr(value)[:40]):
                self.fails(lambda: self.exclusive(timeout=value))
        self.assertEqual(self.guard_calls, [])
        self.assertEqual(self.commands.calls, [])

    def test_invalid_factory_inputs(self):
        for container in ('server-name', 'a' * 12, 'A' * 64, CONTAINER + '\n', None):
            self.fails(lambda: probe.make_exclusive_probe(
                commands=self.commands, container=container, expected_image=IMAGE,
                identity_guard=self.guard))
        self.fails(lambda: probe.make_exclusive_probe(
            commands=None, container=CONTAINER, expected_image=IMAGE,
            identity_guard=self.guard))
        self.fails(lambda: probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER, expected_image=IMAGE,
            identity_guard=None))

    def test_malformed_outputs_never_mean_contention(self):
        for output in (b'', b'CONTENDED\n', CONTENDED + b'PRIVATE', ACQUIRED[:-1],
                       b'RESTORE_LOCK_V1\nOPEN\nACQUIRED\nCLOSED\n',
                       b'RESTORE_LOCK_V1\nOPEN\nCONTENDED\n', ACQUIRED.decode(), None):
            with self.subTest(output=output):
                self.commands.cleanup_verified = True
                self.commands.output = output
                self.fails(lambda: self.exclusive(timeout=2))
                self.assertFalse(self.commands.cleanup_verified)
        self.assertEqual(len(self.guard_calls), 16)

    def test_arbitrary_command_errors_are_sanitized_and_postchecked(self):
        for error in (RuntimeError('build.command-failed'), OSError('PRIVATE path'),
                      subprocess.TimeoutExpired(['PRIVATE'], 2), KeyboardInterrupt('PRIVATE')):
            with self.subTest(error=type(error)):
                self.commands.cleanup_verified = True
                self.commands.error = error
                before = len(self.guard_calls)
                self.fails(lambda: self.exclusive(timeout=2))
                self.assertEqual(len(self.guard_calls), before + 2)
                self.assertFalse(self.commands.cleanup_verified)

    def test_identity_failures_are_sanitized(self):
        self.guard_error = RuntimeError('PRIVATE raw inspect')
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertEqual(self.commands.calls, [])

    def test_different_explicit_parent_image_is_accepted_when_guard_matches(self):
        expected_image = 'sha256:' + 'b' * 64
        identity = copy.deepcopy(IDENTITY)
        identity[0]['image'] = expected_image
        self.identities = [identity, identity]
        exclusive = probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER, expected_image=expected_image,
            identity_guard=self.guard)
        self.assertEqual(self.commands.calls, [])
        self.assertEqual(self.guard_calls, [])
        self.assertIs(exclusive(timeout=2), True)
        self.assertEqual(len(self.commands.calls), 1)
        self.assertEqual(len(self.guard_calls), 2)

    def test_actual_image_must_match_explicit_parent_image_before_exec(self):
        exclusive = probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER,
            expected_image='sha256:' + 'b' * 64, identity_guard=self.guard)
        self.fails(lambda: exclusive(timeout=2))
        self.assertEqual(len(self.guard_calls), 1)
        self.assertEqual(self.commands.calls, [])

    def test_malformed_or_mutable_parent_image_is_rejected_without_io(self):
        for expected_image in (None, True, 1, b'sha256:' + b'b' * 64,
                               '', 'latest', 'image:latest', 'b' * 64,
                               'sha256:' + 'b' * 63, 'sha256:' + 'b' * 65,
                               'sha256:' + 'B' * 64, 'sha256:' + 'g' * 64,
                               'sha512:' + 'b' * 64, IMAGE + '\n',
                               ' ' + IMAGE, 'image@' + IMAGE):
            with self.subTest(expected_image=expected_image):
                self.fails(lambda: probe.make_exclusive_probe(
                    commands=self.commands, container=CONTAINER,
                    expected_image=expected_image, identity_guard=self.guard))
        self.assertEqual(self.guard_calls, [])
        self.assertEqual(self.commands.calls, [])

    def test_other_immutable_image_cannot_replace_pinned_parent_image(self):
        identity = copy.deepcopy(IDENTITY)
        identity[0]['image'] = 'sha256:' + 'b' * 64
        self.identities = [identity]
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertEqual(self.commands.calls, [])

    def test_zero_argument_guard_has_no_unbounded_fallback(self):
        exclusive = probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER, expected_image=IMAGE,
            identity_guard=lambda: IDENTITY)
        self.fails(lambda: exclusive(timeout=2))
        self.assertEqual(self.commands.calls, [])

    def test_missing_or_malformed_identity_prevents_exec(self):
        invalid = [None, (), (None, None, None), list(IDENTITY)]
        for path, value in ((('id',), 'c' * 64), (('image',), 'mutable:tag'),
                            (('pid',), 0), (('pid',), True), (('started',), ''),
                            (('restarts',), 1)):
            item = copy.deepcopy(IDENTITY)
            item[0][path[0]] = value
            invalid.append(item)
        for field, value in (('running', False), ('pid', 100), ('instance', 0)):
            item = copy.deepcopy(IDENTITY)
            item[1][field] = value
            invalid.append(item)
        invalid += [(IDENTITY[0], IDENTITY[1], inode)
                    for inode in ((1, 0), (True, 2), ('1', 2), [1, 2])]
        for identity in invalid:
            with self.subTest(identity=identity):
                self.identities = [identity]
                self.fails(lambda: self.exclusive(timeout=2))
                self.assertEqual(self.commands.calls, [])

    def test_identity_change_during_exec_invalidates_result(self):
        for position, field, value in ((0, 'id', 'c' * 64), (0, 'image', 'sha256:' + 'd' * 64),
                                       (0, 'started', '2026-10-26T13:00:00Z'),
                                       (1, 'instance', 456)):
            with self.subTest(field=field):
                changed = copy.deepcopy(IDENTITY)
                changed[position][field] = value
                self.identities = [IDENTITY, changed]
                self.commands.cleanup_verified = True
                self.fails(lambda: self.exclusive(timeout=2))
                self.assertFalse(self.commands.cleanup_verified)

    def test_changed_inode_between_calls_cannot_repin(self):
        self.assertTrue(self.exclusive(timeout=2))
        self.identities = [(IDENTITY[0], IDENTITY[1], (1, 235))]
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertEqual(len(self.commands.calls), 1)

    def test_pinned_identity_is_a_defensive_snapshot(self):
        identity = copy.deepcopy(IDENTITY)

        def guard(*, timeout):
            return identity

        exclusive = probe.make_exclusive_probe(
            commands=self.commands, container=CONTAINER, expected_image=IMAGE,
            identity_guard=guard)
        self.assertTrue(exclusive(timeout=2))
        identity[1]['instance'] += 1
        self.fails(lambda: exclusive(timeout=2))
        self.assertEqual(len(self.commands.calls), 1)

    def test_pre_guard_exhausting_budget_never_executes(self):
        self.fails(lambda: self.exclusive(timeout=.125))
        self.assertEqual(self.commands.calls, [])

    def test_exec_exhausting_budget_never_returns_success(self):
        self.commands.duration = 2
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertFalse(self.commands.cleanup_verified)

    def test_post_guard_time_is_included(self):
        self.commands.duration = 1.75
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertEqual(self.guard_calls, [2, .125])
        self.assertFalse(self.commands.cleanup_verified)

    def test_existing_cleanup_failure_is_preserved(self):
        self.commands.cleanup_verified = False
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertEqual(self.commands.calls, [])
        self.assertFalse(self.commands.cleanup_verified)

    def test_post_identity_error_invalidates_contention(self):
        self.commands.output = CONTENDED
        self.identities = [IDENTITY, None]
        self.fails(lambda: self.exclusive(timeout=2))
        self.assertFalse(self.commands.cleanup_verified)


class ShellProtocolControls(unittest.TestCase):
    """Run the real fixed shell logic with a LOCAL fake flock, never Docker.

    Only two constant paths are replaced in this test harness. Production has
    no path/executable override. This checks shell branches, not Linux flock.
    """
    def run_shell(self, *, acquire=0, acquire_output='', unlock=0,
                  unlock_output='', missing=False, symlink=False, directory=False,
                  denied=False, mismatched=False, close_error=False, executable_missing=False,
                  replace_after_acquire=False):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            lock = root / 'lock'
            if directory:
                lock.mkdir()
            elif symlink:
                target = root / 'target'
                target.write_bytes(b'unchanged')
                lock.symlink_to(target)
            elif not missing:
                lock.write_bytes(b'unchanged')
                if denied:
                    lock.chmod(0)
            flock = root / 'flock'
            replacement = root / 'replacement'
            replacement.write_bytes(b'unchanged')
            # Values are authored test constants, not production caller input.
            flock.write_text('#!/bin/sh\n'
                'if [ "$1" = -u ]; then\n'
                f"  printf '%s' '{unlock_output}'\n  exit {unlock}\n"
                'fi\n'
                + (f'mv "{replacement}" "{lock}"\n' if replace_after_acquire else '')
                + f"printf '%s' '{acquire_output}' >&2\nexit {acquire}\n")
            flock.chmod(0o700)
            stat = root / 'stat'
            stat.write_text(f'#!{sys.executable}\nimport os, sys\n'
                            'path = sys.argv[-1]\n'
                            'value = os.fstat(9) if path == "/dev/fd/9" else os.stat(path)\n'
                            'inode = value.st_ino\n'
                            + ('if path == "/dev/fd/9": inode += 1\n' if mismatched else '')
                            + 'print(str(value.st_dev) + ":" + str(inode))\n')
            stat.chmod(0o700)
            script = probe.PROBE_SCRIPT.replace('/operations/lifecycle.lock', str(lock))
            script = script.replace('/usr/bin/flock', str(flock))
            script = script.replace('stat -L -c', str(stat) + ' -L -c')
            script = script.replace('/proc/self/fd/9', '/dev/fd/9')
            if close_error:
                script = script.replace('exec 9<&- || fail', 'false || fail')
            if executable_missing:
                flock.unlink()
            result = subprocess.run(['/bin/sh', '-c', script], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)
            self.assertNotIn(b'PRIVATE', result.stdout)
            if missing:
                self.assertFalse(lock.exists())
            elif not symlink and not directory:
                if denied:
                    lock.chmod(0o600)
                self.assertEqual(lock.read_bytes(), b'unchanged')
            return result

    def test_success_requires_acquire_unlock_close(self):
        result = self.run_shell()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, ACQUIRED, b''))

    def test_busybox_contention_is_explicit_zero_exit_protocol(self):
        result = self.run_shell(acquire=1)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, CONTENDED, b''))

    def test_noncontention_statuses_and_any_diagnostics_fail(self):
        for status, output in ((1, 'PRIVATE denied'), (1, '\n'), (1, '\n\n'),
                               (2, ''), (127, ''), (0, 'PRIVATE warning')):
            with self.subTest(status=status, output=output):
                result = self.run_shell(acquire=status, acquire_output=output)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(result.stdout, (ACQUIRED, CONTENDED))

    def test_unlock_failure_never_reports_success_or_contention(self):
        for status, output in ((1, ''), (0, 'PRIVATE warning'), (0, '\n')):
            result = self.run_shell(unlock=status, unlock_output=output)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(result.stdout, (ACQUIRED, CONTENDED))

    def test_missing_path_is_never_created(self):
        self.assertNotEqual(self.run_shell(missing=True).returncode, 0)

    def test_symlink_and_directory_are_rejected(self):
        self.assertNotEqual(self.run_shell(symlink=True).returncode, 0)
        self.assertNotEqual(self.run_shell(directory=True).returncode, 0)

    def test_permission_error_is_not_contention(self):
        self.assertNotEqual(self.run_shell(denied=True).returncode, 0)

    def test_descriptor_must_match_actual_path_inode(self):
        self.assertNotEqual(self.run_shell(mismatched=True).returncode, 0)

    def test_inode_replacement_after_flock_invalidates_both_outcomes(self):
        for status in (0, 1):
            result = self.run_shell(acquire=status, replace_after_acquire=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(result.stdout, (ACQUIRED, CONTENDED))

    def test_close_failure_never_reports_complete_protocol(self):
        result = self.run_shell(close_error=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(result.stdout, (ACQUIRED, CONTENDED))

    def test_missing_flock_is_not_contention(self):
        self.assertNotEqual(self.run_shell(executable_missing=True).returncode, 0)


class CommandsDelegationControls(unittest.TestCase):
    """Exercise real Commands locally; Docker argv is intercepted in test only."""
    def exercise(self, child):
        from TESTS.adapters.go.message_image_build import Commands as RealCommands
        with tempfile.TemporaryDirectory() as work:
            commands = RealCommands(work)
            stop_calls = []
            stop = commands.stop_group

            def stop_group(process):
                stop_calls.append(process.pid)
                return stop(process)

            commands.stop_group = stop_group

            class LocalOnly:
                @property
                def cleanup_verified(self):
                    return commands.cleanup_verified

                @cleanup_verified.setter
                def cleanup_verified(self, value):
                    commands.cleanup_verified = value

                def __call__(self, argv, *, timeout):
                    if argv[:3] != ['docker', 'exec', CONTAINER]:
                        raise AssertionError('test.unexpected-command')
                    return commands([sys.executable, '-B', '-c', child], timeout=min(timeout, .1))

            guarded = []

            def guard(*, timeout):
                guarded.append(timeout)
                return copy.deepcopy(IDENTITY)

            exclusive = probe.make_exclusive_probe(
                commands=LocalOnly(), container=CONTAINER, expected_image=IMAGE,
                identity_guard=guard)
            with self.assertRaises(probe.ProbeError):
                exclusive(timeout=2)
            self.assertEqual(len(guarded), 2)
            self.assertEqual(len(stop_calls), 1)
            self.assertFalse(commands.group_exists(stop_calls[0]))
            self.assertFalse(commands.cleanup_verified)  # Remote lifetime is uncertain.

    def test_real_nonzero_exit_uses_commands_cleanup(self):
        self.exercise('import sys; sys.stderr.write("PRIVATE diagnostic"); sys.exit(1)')

    def test_real_timeout_reaps_owned_child_group(self):
        self.exercise('import time; time.sleep(5)')


if __name__ == '__main__':
    unittest.main()
