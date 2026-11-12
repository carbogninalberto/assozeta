"""Offline phase2 controls. Doubles replace only fixture/HTTP boundaries."""
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import json
import runpy
import sys
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_list_spool_faults as faults


class Socket:
    def __init__(self):
        self.timeouts = []
        self.closed = False

    def settimeout(self, value):
        if self.closed:
            raise OSError('closed socket')
        self.timeouts.append(value)

    def fileno(self):
        return -1 if self.closed else 42


class Response:
    def __init__(self, status=200, body=b'', content_type='application/json'):
        self.status, self.content_type = status, content_type
        self.stream, self.closed = BytesIO(body), False
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=Socket()))
        self.reads = []

    def getheader(self, name, default=None):
        return {'Content-Type': self.content_type}.get(name, default)

    def read(self, amount):
        self.reads.append(amount)
        return self.stream.read(amount)

    def read1(self, amount):
        return self.read(amount)

    def close(self):
        self.closed = True
        self.fp.raw._sock.closed = True

    def isclosed(self):
        return self.closed


class Proof:
    """Model the fixed path only; never execute a command or start a service."""
    def __init__(self):
        self.mode, self.uid, self.gid = 0o700, 10001, 10001
        self.inode, self.start = 321, 1234
        self.live = 0
        self.commands, self.chmods = [], []
        self.fail_chmod_after_change = False
        self.fail_restore = False

    def volume_info(self):
        return {'private': True}

    def exec(self, *args):
        self.commands.append(args)
        script = args[-1]
        if 'chmod 0500 ' in script:
            self.mode = 0o500
            self.chmods.append(0o500)
            if self.fail_chmod_after_change:
                raise RuntimeError('private path and secret')
        if 'chmod 0700 ' in script:
            self.chmods.append(0o700)
            if self.fail_restore:
                raise RuntimeError('private restoration failure')
            self.mode = 0o700
        # Exact protocol returned by the production fixed probe script.
        return (f'{self.uid}\n{self.gid}\n10001:10001:{self.mode:o}:99:{self.inode}\n'
                f'10001 10001 10001 10001\n10001 10001 10001 10001\n'
                f'{self.start}\n/app/server')


class Boundary:
    ROUTE = '/communications/messages/list'
    def __init__(self, proof):
        self.proof = proof
        self.responses, self.connections = [], []
        self.failure, self.fail_wait = None, False
        self.error_body = faults.GENERIC_ERROR
        self.error_status = 500
        self.error_type = 'text/html; charset=utf-8'
        self.verified = 0
        self.waits = 0
        self.fail_close_at = None

    @contextmanager
    def held_list(self, base, token):
        response = Response()
        self.responses.append(response)
        response_index = len(self.responses)
        self.proof.live = 1
        try:
            yield response
        finally:
            response.close()
            self.proof.live = 0
            if self.fail_close_at == response_index:
                raise OSError('private close failure')

    def observe_spools(self, proof, count):
        if self.failure == 'observe':
            raise RuntimeError('private observation')
        if proof.live != count:
            raise RuntimeError('descriptor mismatch')
        return [(70 * 1024 * 1024, 123, 99)] if count else []

    def wait_empty(self, proof):
        self.waits += 1
        if self.fail_wait or proof.live:
            raise RuntimeError('private descriptor leak')

    def verify_list(self, response, association):
        self.verified += 1
        if self.failure == f'recovery-{self.verified}':
            raise RuntimeError('private row and token')
        response.read(1)
        return {'rows': 1152, 'bytes': 70 * 1024 * 1024,
                'wire_sha256': 'a' * 64, 'content_sha256': 'b' * 64,
                'expected_sha256': 'b' * 64}

    def connection(self, *args, **kwargs):
        boundary = self

        class Connection:
            def __init__(self):
                self.closed, self.sock = False, Socket()
                self.response = None

            def request(self, method, route, headers):
                if boundary.failure == 'request':
                    raise OSError('secret request failure')
                if boundary.proof.mode != 0o500:
                    raise RuntimeError('permission not injected')
                if method != 'GET' or route != '/communications/messages/list':
                    raise RuntimeError('wrong request')

            def getresponse(self):
                if boundary.failure == 'headers':
                    raise OSError('secret response failure')
                if boundary.failure == 'interrupt':
                    raise KeyboardInterrupt('private interruption')
                self.response = Response(boundary.error_status, boundary.error_body,
                                         boundary.error_type)
                if boundary.failure == 'body':
                    self.response.read1 = lambda _: (_ for _ in ()).throw(OSError('private body'))
                return self.response

            def close(self):
                self.closed, self.sock = True, None
                if boundary.failure == 'connection-close':
                    raise OSError('private connection close')

        connection = Connection()
        self.connections.append(connection)
        return connection


class FaultControls(unittest.TestCase):
    def setUp(self):
        self.proof = Proof()
        self.boundary = Boundary(self.proof)
        self.stack = self.enterContext(patch.object(faults, '_phase1', return_value=self.boundary))
        self.enterContext(patch.object(faults.http.client, 'HTTPConnection', self.boundary.connection))

    def run_faults(self):
        return faults.run_faults(self.proof, 'http://127.0.0.1:12345',
                                 'private-token', 'private-association')

    def assert_clean(self):
        self.assertEqual(self.proof.mode, 0o700)
        self.assertEqual(self.proof.live, 0)
        self.assertTrue(all(response.closed for response in self.boundary.responses))
        for connection in self.boundary.connections:
            self.assertTrue(connection.closed)
            self.assertIsNone(connection.sock)
            if connection.response:
                self.assertTrue(connection.response.closed)

    def test_narrow_success_and_safe_closed_receipt(self):
        evidence = self.run_faults()
        self.assertTrue(faults.validate_faults(evidence))
        self.assertFalse(evidence['complete_acceptance'])
        self.assertEqual(self.proof.chmods, [0o500, 0o700])
        self.assertEqual(self.boundary.responses[0].reads, [])
        self.assertEqual(self.boundary.verified, 2)
        self.assert_clean()
        raw = json.dumps(evidence, allow_nan=False)
        self.assertLess(len(raw), 8192)
        for private in ('private-token', 'private-association', '/var/', 'Server Error'):
            self.assertNotIn(private, raw)

    def test_false_or_extended_evidence_is_rejected(self):
        valid = self.run_faults()
        cases = [None, {}, True, [], {'success': True}]
        for path, replacement in (
            (('faults_accepted',), False), (('complete_acceptance',), True),
            (('format',), True), (('runtime', 'uid'), 0),
            (('runtime', 'same_process'), False),
            (('runtime', 'exclusive_spool_verified'), False),
            (('cancellation', 'kind'), 'database-cancellation'),
            (('cancellation', 'status'), 500),
            (('cancellation', 'body_bytes_read'), 1),
            (('cancellation', 'live_unlinked_spools'), 0),
            (('cancellation', 'response_closed'), False),
            (('cancellation', 'socket_closed'), False),
            (('cancellation', 'empty_after'), False),
            (('cancellation', 'cleanup_seconds'), float('nan')),
            (('cancellation', 'cleanup_seconds'), 16),
            (('cancellation', 'staged_bytes'), 1),
            (('storage', 'status'), 200), (('storage', 'restored'), False),
            (('storage', 'denied_mode'), 0o777),
            (('storage', 'restored_mode'), 0o500),
            (('storage', 'error_sha256'), 'c' * 64),
            (('storage', 'error_bytes'), 0),
            (('storage', 'no_private_data'), False),
            (('storage', 'empty_after_failure'), False),
            (('storage', 'recovery', 'rows'), True),
            (('storage', 'recovery', 'rows'), 0),
            (('storage', 'recovery', 'expected_sha256'), 'c' * 64),
            (('storage', 'recovery', 'wire_sha256'), 'c' * 64),
            (('remaining',), []),
        ):
            changed = deepcopy(valid)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            cases.append(changed)
        # Every dictionary is closed, including recovery observations.
        for path in ((), ('runtime',), ('cancellation',), ('storage',),
                     ('storage', 'recovery'), ('cancellation', 'recovery')):
            changed = deepcopy(valid)
            target = changed
            for key in path:
                target = target[key]
            target['private-path'] = 'secret'
            cases.append(changed)
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(faults.validate_faults(value))

    def test_every_required_boolean_and_missing_key_rejected(self):
        evidence = self.run_faults()

        def variants(value):
            for key, child in value.items():
                missing = deepcopy(value)
                del missing[key]
                yield missing
                if child is True:
                    changed = deepcopy(value)
                    changed[key] = False
                    yield changed
                    changed[key] = 1
                    yield changed
                if type(child) is dict:
                    for variant in variants(child):
                        changed = deepcopy(value)
                        changed[key] = variant
                        yield changed

        for value in variants(evidence):
            self.assertFalse(faults.validate_faults(value))

    def test_all_operational_errors_close_and_restore_and_redact(self):
        for failure in ('observe', 'recovery-1', 'request', 'headers', 'body',
                        'interrupt', 'connection-close', 'recovery-2'):
            with self.subTest(failure=failure):
                self.boundary.failure, self.boundary.verified = failure, 0
                with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$') as error:
                    self.run_faults()
                self.assertIsNone(error.exception.__cause__)
                self.assertIsNone(error.exception.__context__)
                self.assert_clean()

    def test_partial_chmod_failure_still_restores(self):
        self.proof.fail_chmod_after_change = True
        with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
            self.run_faults()
        self.assertEqual(self.proof.chmods, [0o500, 0o700])
        self.assert_clean()

    def test_held_response_close_errors_never_accept_and_leave_no_live_spool(self):
        for position in (1, 2, 3):
            with self.subTest(position=position):
                self.boundary.fail_close_at = len(self.boundary.responses) + position
                with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                    self.run_faults()
                self.assert_clean()

    def test_restoration_failure_cannot_return_evidence(self):
        self.proof.fail_restore = True
        with self.assertRaisesRegex(RuntimeError, '^faults.restoration-failed$'):
            self.run_faults()
        self.assertEqual(self.proof.chmods, [0o500, 0o700])
        self.assertEqual(self.boundary.verified, 1)
        self.assertEqual(self.proof.live, 0)

    def test_cleanup_still_attempted_when_restoration_and_request_both_fail(self):
        self.proof.fail_restore = True
        self.boundary.failure = 'headers'
        with self.assertRaisesRegex(RuntimeError, '^faults.restoration-failed$'):
            self.run_faults()
        self.assertGreaterEqual(self.boundary.waits, 4)
        self.assertTrue(all(connection.closed for connection in self.boundary.connections))

    def test_cleanup_failure_after_permission_error_still_restores_mode(self):
        original = self.boundary.wait_empty

        def failing_wait(proof):
            if self.proof.chmods:
                raise RuntimeError('private cleanup error')
            return original(proof)

        with patch.object(self.boundary, 'wait_empty', side_effect=failing_wait):
            with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                self.run_faults()
        self.assertEqual(self.proof.chmods, [0o500, 0o700])
        self.assert_clean()

    def test_failed_mutation_receipt_and_failed_restore_never_accept(self):
        self.proof.fail_chmod_after_change = self.proof.fail_restore = True
        with self.assertRaisesRegex(RuntimeError, '^faults.restoration-failed$'):
            self.run_faults()
        self.assertEqual(self.proof.chmods, [0o500, 0o700])
        self.assertEqual(self.boundary.connections, [])

    def test_bad_error_contract_is_bounded_closed_redacted_and_restored(self):
        for status, content_type, body in (
            (200, 'application/json', b'private row'),
            (403, 'text/html; charset=utf-8', faults.GENERIC_ERROR),
            (500, 'application/json', faults.GENERIC_ERROR),
            (500, 'text/html; charset=utf-8', b'private path'),
            (500, 'text/html; charset=utf-8', faults.GENERIC_ERROR + b'private'),
            (500, 'text/html; charset=utf-8', b'x' * 10000),
        ):
            with self.subTest(status=status, size=len(body)):
                self.boundary.error_status, self.boundary.error_type = status, content_type
                self.boundary.error_body, self.boundary.verified = body, 0
                with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                    self.run_faults()
                self.assert_clean()
                response = self.boundary.connections[-1].response
                self.assertTrue(all(0 < n <= faults.ERROR_LIMIT + 1 for n in response.reads))

    def test_descriptor_cleanup_failure_is_fail_closed(self):
        self.boundary.fail_wait = True
        with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
            self.run_faults()
        self.assert_clean()

    def test_runtime_uid_or_mode_mismatch_never_mutates(self):
        for attribute, value in (('uid', 0), ('gid', 0), ('mode', 0o777)):
            original = getattr(self.proof, attribute)
            setattr(self.proof, attribute, value)
            with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                self.run_faults()
            self.assertEqual(self.proof.chmods, [])
            setattr(self.proof, attribute, original)

    def test_mutation_is_guarded_and_only_targets_fixed_spool(self):
        self.run_faults()
        mutations = [args[-1] for args in self.proof.commands if 'chmod ' in args[-1]]
        self.assertEqual(len(mutations), 2)
        for script, mode in zip(mutations, ('0500', '0700')):
            self.assertTrue(script.endswith(f'chmod {mode} {faults.SPOOL}'))
            self.assertIn('test "$(id -u)" = 10001', script)
            self.assertIn('test "$(id -g)" = 10001', script)
            self.assertIn(f'test ! -L {faults.SPOOL}', script)
            self.assertIn('10001:10001:99:321', script)
            self.assertNotIn('chown', script)
        count = len(self.proof.commands)
        with self.assertRaises(RuntimeError):
            faults._change_mode(self.proof, ('99; id', '321', '1234'), 0o500)
        self.assertEqual(len(self.proof.commands), count)

    def test_import_and_validator_are_inert_without_phase1(self):
        with patch.dict(sys.modules, {'TESTS.adapters.go.message_list_spool_image': None}):
            with patch.object(faults.http.client, 'HTTPConnection', side_effect=AssertionError):
                namespace = runpy.run_path(faults.__file__)
                self.assertFalse(namespace['validate_faults']({}))

    def test_deadline_response_rejects_elapsed_total_and_bounds_read(self):
        response = Response(body=b'abc')
        with patch.object(faults.time, 'monotonic', side_effect=[0, 0, 6]):
            bounded = faults.DeadlineResponse(response, 5)
            with self.assertRaises(TimeoutError):
                bounded.read(1)
        self.assertEqual(response.reads, [1])
        self.assertEqual(response.fp.raw._sock.timeouts, [5])

    def test_recovery_uses_read1_and_trickle_cannot_extend_total_deadline(self):
        response = Response(body=b'abc')
        with patch.object(response, 'read1', side_effect=[b'a', b'b']) as read1:
            with patch.object(faults.time, 'monotonic', side_effect=[0, 1, 2, 3, 6]):
                bounded = faults.DeadlineResponse(response, 5)
                self.assertEqual(bounded.read(65536), b'a')
                with self.assertRaises(TimeoutError):
                    bounded.read(65536)
        self.assertEqual(read1.call_count, 2)
        self.assertEqual(response.reads, [])

    def test_expired_deadline_does_not_attempt_read_and_oversized_read_is_rejected(self):
        response = Response()
        with patch.object(faults.time, 'monotonic', side_effect=[0, 6]):
            bounded = faults.DeadlineResponse(response, 5)
            with self.assertRaises(TimeoutError):
                bounded.read(1)
        self.assertEqual(response.reads, [])
        with patch.object(response, 'read1', return_value=b'xx'):
            with self.assertRaises(RuntimeError):
                faults.DeadlineResponse(response, 5).read(1)

    def test_content_length_completion_can_close_socket_before_final_eof_read(self):
        response = Response()

        def complete(amount):
            # HTTPResponse closes fp when its Content-Length reaches zero.
            response.close()
            return b'abc'

        with patch.object(response, 'read1', side_effect=complete):
            bounded = faults.DeadlineResponse(response, 5)
            self.assertEqual(bounded.read(3), b'abc')
        with patch.object(response, 'read1', return_value=b''):
            self.assertEqual(bounded.read(3), b'')

    def test_elapsed_cleanup_cannot_be_accepted_even_if_poll_returns(self):
        with patch.object(faults.time, 'monotonic', side_effect=[0, 16]):
            with self.assertRaises(RuntimeError):
                faults._empty(self.boundary, self.proof)

    def test_process_restart_or_directory_replacement_is_rejected(self):
        original = self.boundary.verify_list
        for attribute in ('start', 'inode'):
            with self.subTest(attribute=attribute):
                self.boundary.verified = 0

                def replaced(response, association):
                    value = original(response, association)
                    setattr(self.proof, attribute, getattr(self.proof, attribute) + 1)
                    return value

                with patch.object(self.boundary, 'verify_list', side_effect=replaced):
                    with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                        self.run_faults()
                self.assertEqual(self.proof.chmods, [])
                self.assert_clean()

    def test_bad_endpoint_is_rejected_before_any_boundary_operation(self):
        for base in ('http://example.com:80', 'http://127.0.0.1:0',
                     'http://127.0.0.1:65536', 'http://127.0.0.1:12/path'):
            with self.assertRaisesRegex(RuntimeError, '^faults.execution-failed$'):
                faults.run_faults(self.proof, base, 'token', 'association')
        self.assertEqual(self.proof.commands, [])
        self.assertEqual(self.boundary.responses, [])


if __name__ == '__main__':
    unittest.main()
