"""Offline controls only: never authorize a real runtime receipt from doubles."""
import copy
import importlib.util
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_list_query_cancellation as cancel

CANARY = 'secret-canary postgres://private:token@host/db /private/fixture token-canary'
QUERY = 'SELECT real_query_from_inspected_source'
START = object()


class Cursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def __enter__(self):
        self.db.open_cursors += 1
        return self

    def __exit__(self, *args):
        self.db.open_cursors -= 1
        if self.db.world.fault == 'cursor-close':
            raise RuntimeError(CANARY)

    def execute(self, query, params=()):
        w = self.db.world
        if query == 'LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE':
            w.events.append('lock')
            w.locked = True
        elif 'pg_locks' in query:
            held = w.locked and not (w.fault == 'early-release' and w.disconnected)
            self.rows = [(held,)]
            w.events.append('held')
        elif 'query=%s' in query:
            w.events.append('wait')
            if not w.started or w.fault == 'missing-wait':
                self.rows = []
            else:
                pid = 101 if w.fault == 'wrong-backend' else 303
                sql = 'wrong' if w.fault == 'wrong-query' else QUERY
                blockers = [999] if w.fault == 'wrong-blocker' else [101]
                self.rows = [(pid, START, 'active', w.fault == 'no-transaction', sql,
                              'IO' if w.fault == 'wrong-wait' else 'Lock', blockers,
                              'other' if w.fault == 'wrong-user' else 'contracts',
                              'autovacuum worker' if w.fault == 'wrong-kind' else 'client backend')]
                if w.fault == 'duplicate':
                    self.rows *= 2
        elif 'WHERE pid=%s' in query:
            w.events.append('backend')
            state = 'active' if (w.fault == 'backend-leak'
                                 or w.fault == 'unblocked-only' and w.locked) else 'idle'
            start = object() if w.fault == 'backend-replaced' else START
            self.rows = [(start, state, state == 'idle' and w.fault != 'transaction-leak',
                          state == 'idle')]
        else:
            raise AssertionError(query)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class DB:
    def __init__(self, world, role):
        self.world, self.role = world, role
        self.closed = False
        self.open_cursors = 0
        self.autocommit = True

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.world.events.append('rollback')
        self.world.locked = False
        if self.world.fault == 'rollback':
            raise RuntimeError(CANARY)

    def close(self):
        self.closed = True
        if self.role == 'blocker':
            self.world.locked = False
        if self.world.fault == 'db-close':
            raise RuntimeError(CANARY)


class Request:
    def __init__(self, world):
        self.world = world
        self.closed = False
        self.headers = False
        self.outcome = 'waiting'

    def start(self):
        self.world.events.append('request')
        self.world.started = True

    def pending(self):
        return self.outcome == 'waiting' and not self.headers

    def disconnect(self):
        self.world.events.append('disconnect')
        self.world.disconnected = True
        self.outcome = 'failed' if self.world.fault == 'thread' else 'disconnected'
        if self.world.fault == 'headers':
            self.headers = True
        return self.outcome == 'disconnected' and not self.headers

    def close(self):
        self.closed = True
        if self.world.fault == 'socket-close':
            raise RuntimeError(CANARY)


class World:
    def __init__(self, fault=None):
        self.fault = fault
        self.locked = self.started = self.disconnected = False
        self.events = []
        self.observer, self.blocker = DB(self, 'observer'), DB(self, 'blocker')
        self.request = Request(self)
        self.proof = types.SimpleNamespace()
        self.recovery = dict(rows=1152, bytes=75_500_000,
                             wire_sha256='1' * 64, content_sha256='2' * 64,
                             expected_sha256='2' * 64)

    def spools(self, proof, count):
        self.events.append('spool-' + str(count))
        if count == 1:
            if self.fault == 'missing-spool':
                raise cancel._Failure()
            return [(0, 12, 34)]
        if self.disconnected and self.fault == 'spool-leak':
            raise cancel._SpoolPresent()
        return []

    def identity(self, proof):
        self.events.append('identity')
        if self.disconnected and self.fault == 'restart':
            return ('restarted',)
        if self.disconnected and self.fault == 'ownership':
            raise RuntimeError(CANARY)
        return ('same',)

    @contextmanager
    def held(self, base, token):
        self.events.append('recovery')
        if self.locked:
            raise AssertionError('recovered before rollback')
        if self.fault == 'recovery':
            raise RuntimeError(CANARY)
        yield object()
        if self.fault == 'recovery-close':
            raise RuntimeError(CANARY)

    def run(self):
        phase1 = types.SimpleNamespace(held_list=self.held,
                                      verify_list=lambda *args: self.recovery)
        with patch.multiple(cancel, _phase1=lambda: phase1, _list_sql=lambda: QUERY,
                            _open_databases=lambda proof: (self.observer, self.blocker, 202, 101),
                            _identity=self.identity, _spools=self.spools,
                            _request=lambda *args: self.request,
                            WAIT_SECONDS=0.01, RELEASE_SECONDS=0.01, POLL_SECONDS=0.001):
            return cancel.run_query_cancellation(self.proof, 'base', 'token', 'association')


class OrderingControls(unittest.TestCase):
    def test_success_requires_release_before_rollback_and_recovery(self):
        world = World()
        evidence = world.run()
        self.assertTrue(cancel.validate_query_cancellation(evidence), evidence)
        events = world.events
        self.assertLess(events.index('lock'), events.index('request'))
        self.assertLess(events.index('wait', events.index('request')), events.index('disconnect'))
        self.assertLess(events.index('spool-1'), events.index('disconnect'))
        self.assertLess(events.index('disconnect'), events.index('backend'))
        self.assertLess(events.index('backend'), events.index('rollback'))
        self.assertIn('held', events[events.index('backend'):events.index('rollback')])
        self.assertIn('spool-0', events[events.index('disconnect'):events.index('rollback')])
        self.assertLess(events.index('rollback'), events.index('recovery'))
        self.assertTrue(world.observer.closed and world.blocker.closed and world.request.closed)
        self.assertEqual(world.observer.open_cursors + world.blocker.open_cursors, 0)

    def test_adversarial_failures_are_closed_safe_and_cleanup_is_attempted(self):
        for fault in ('missing-wait', 'wrong-backend', 'wrong-query', 'wrong-blocker',
                      'duplicate', 'early-release', 'backend-leak', 'backend-replaced',
                      'spool-leak', 'thread', 'headers', 'restart', 'ownership',
                      'recovery', 'rollback', 'db-close', 'socket-close', 'cursor-close',
                      'wrong-user', 'wrong-kind', 'no-transaction', 'wrong-wait',
                      'transaction-leak', 'missing-spool', 'unblocked-only', 'recovery-close'):
            with self.subTest(fault=fault):
                world = World(fault)
                result = world.run()
                self.assertFalse(cancel.validate_query_cancellation(result), result)
                self.assertNotIn(CANARY, json.dumps(result))
                self.assertTrue(world.observer.closed and world.blocker.closed)
                self.assertIn('rollback', world.events)
                self.assertFalse(world.locked)
                if world.started:
                    self.assertTrue(world.request.closed)
                if fault in ('missing-wait', 'wrong-backend', 'wrong-query', 'wrong-blocker',
                             'early-release', 'backend-leak', 'spool-leak', 'thread'):
                    self.assertNotIn('recovery', world.events)

    def test_closed_schema_rejects_extra_fields_false_flags_bad_counts_and_hashes(self):
        evidence = World().run()
        self.assertTrue(cancel.validate_query_cancellation(evidence))
        for key, value in evidence.items():
            changed = copy.deepcopy(evidence)
            if type(value) is bool:
                changed[key] = not value
            elif type(value) is int:
                changed[key] = -1
            elif type(value) is str:
                changed[key] = CANARY
            else:
                continue
            with self.subTest(key=key):
                self.assertFalse(cancel.validate_query_cancellation(changed))
        changed = copy.deepcopy(evidence)
        changed['extra'] = CANARY
        self.assertFalse(cancel.validate_query_cancellation(changed))
        for value in (None, [], {}, True):
            self.assertFalse(cancel.validate_query_cancellation(value))

    def test_slow_release_observation_cannot_pass_after_deadline(self):
        world = World()
        original = world.spools

        def slow(proof, count):
            if world.disconnected and world.locked and count == 0:
                time.sleep(0.02)  # The double's release deadline is 0.01 seconds.
            return original(proof, count)

        world.spools = slow
        self.assertFalse(cancel.validate_query_cancellation(world.run()))
        self.assertNotIn('recovery', world.events)
        self.assertTrue(world.observer.closed and world.blocker.closed)

    def test_secret_canary_in_recovery_record_is_never_copied_to_evidence(self):
        for field in ('extra', 'wire_sha256', 'rows', 'bytes'):
            world = World()
            world.recovery[field] = CANARY
            evidence = world.run()
            self.assertFalse(cancel.validate_query_cancellation(evidence))
            self.assertNotIn(CANARY, json.dumps(evidence))


class EndpointControls(unittest.TestCase):
    def environment(self, fault=None):
        config = dict(ENGINE='django.db.backends.postgresql', NAME=cancel.DATABASE,
                      USER='contracts', PASSWORD=CANARY, HOST='127.0.0.1', PORT='54321',
                      OPTIONS={'service': CANARY})
        acquired, parameters = [], []

        class Raw:
            def __init__(self, pid):
                self.pid = pid
                self.closed = False
                self.rolled_back = False
                self.autocommit = False

            @contextmanager
            def cursor(self):
                cursor = types.SimpleNamespace(
                    execute=lambda *args: None,
                    fetchall=lambda: [(cancel.DATABASE if fault != 'identity' else 'wrong',
                                       'contracts', self.pid)])
                yield cursor

            def close(self):
                self.closed = True
                if fault == 'close':
                    raise RuntimeError(CANARY)

            def rollback(self):
                self.rolled_back = True

        def connect(**params):
            parameters.append(params)
            if len(parameters) == 2 and fault == 'partial':
                raise RuntimeError(CANARY)
            connection = Raw(100 + len(parameters))
            acquired.append(connection)
            return connection

        template = types.SimpleNamespace(vendor='postgresql', in_atomic_block=False,
                                         settings_dict=config,
                                         Database=types.SimpleNamespace(connect=connect))
        module = types.ModuleType('django.db')
        module.connections = {'default': template}
        proof = types.SimpleNamespace(postgres='owned', postgres_port=54321,
                                      published_port=lambda *args: 54321)
        return module, proof, acquired, parameters

    def test_private_settings_are_copied_without_global_changes_or_libpq_redirects(self):
        module, proof, acquired, parameters = self.environment()
        before = copy.deepcopy(module.connections['default'].settings_dict)
        with patch.dict(sys.modules, {'django.db': module}):
            observer, blocker, observer_pid, blocker_pid = cancel._open_databases(proof)
        try:
            self.assertIsNot(observer, blocker)
            self.assertNotEqual(observer_pid, blocker_pid)
            self.assertTrue(observer.autocommit)
            self.assertFalse(blocker.autocommit)
            self.assertEqual(module.connections['default'].settings_dict, before)
            for params in parameters:
                self.assertEqual(params['host'], '127.0.0.1')
                self.assertEqual(params['hostaddr'], '127.0.0.1')
                self.assertEqual(params['port'], proof.postgres_port)
                self.assertEqual(params['dbname'], cancel.DATABASE)
                self.assertEqual(params['connect_timeout'], cancel.IO_SECONDS)
                self.assertEqual(params['options'], '-c statement_timeout=2000 -c lock_timeout=2000')
                self.assertNotIn('service', params)
        finally:
            for connection in acquired:
                connection.rollback()
                connection.close()

    def test_wrong_endpoint_or_database_never_opens_any_connection(self):
        for key, value in (('HOST', 'remote'), ('HOST', '/tmp/socket'),
                           ('PORT', '5432'), ('PORT', True), ('NAME', 'postgres'),
                           ('NAME', CANARY), ('USER', 'other'), ('ENGINE', 'sqlite')):
            with self.subTest(key=key, value=value):
                module, proof, acquired, _ = self.environment()
                module.connections['default'].settings_dict[key] = value
                with patch.dict(sys.modules, {'django.db': module}):
                    with self.assertRaises(cancel._Failure):
                        cancel._open_databases(proof)
                self.assertEqual(acquired, [])

    def test_partial_database_setup_is_rolled_back_closed_and_sanitized(self):
        for fault in ('partial', 'identity', 'close'):
            module, proof, acquired, _ = self.environment(fault)
            if fault == 'close':
                module, proof, acquired, _ = self.environment('identity')
                original_connect = module.connections['default'].Database.connect

                def connect(**params):
                    connection = original_connect(**params)
                    connection.close = lambda: (_ for _ in ()).throw(RuntimeError(CANARY))
                    return connection
                module.connections['default'].Database.connect = connect
            with patch.dict(sys.modules, {'django.db': module}):
                with self.assertRaises(cancel._Failure) as caught:
                    cancel._open_databases(proof)
            self.assertNotIn(CANARY, str(caught.exception))
            self.assertTrue(all(connection.rolled_back for connection in acquired))
            if fault == 'close':
                self.assertEqual(caught.exception.cleanup_errors, 1)
            else:
                self.assertTrue(all(connection.closed for connection in acquired))


class DescriptorControls(unittest.TestCase):
    def proof(self, *, descriptor=None, entries='0', mount=None):
        device = 2049  # Linux dev_t for 8:1, independent of the observer OS.
        if descriptor is None:
            descriptor = f'0 99 {device} {0o100600:x} 10001 10001 0\n42'
        mount = mount or f'42 1 8:1 / {cancel.SPOOL} rw - ext4 /dev/fixture rw'

        def execute(*args):
            if args == ('cat', '/proc/1/mountinfo'):
                return mount
            if args[-1] == cancel.FD_SCRIPT:
                return descriptor
            return entries
        return types.SimpleNamespace(exec=execute)

    def test_zero_small_live_unlinked_regular_file_on_exact_mount(self):
        result = cancel._spools(self.proof(), 1)
        self.assertEqual(result[0][0], 0)
        self.assertEqual(cancel._spools(self.proof(descriptor=''), 0), [])
        with self.assertRaises(cancel._SpoolPresent):
            cancel._spools(self.proof(), 0)

    def test_metadata_wrong_mount_named_file_or_output_overflow_fails(self):
        device = 2049
        valid = f'0 99 {device} {0o100600:x} 10001 10001 0\n42'
        for descriptor in ('invalid', valid.replace('\n42', '\n43'),
                           valid.replace('10001 10001 0', '10001 10001 1'),
                           valid.replace('10001 10001 0', '0 0 0'),
                           valid.replace(f'{0o100600:x}', f'{0o100644:x}'),
                           valid.replace(f'{0o100600:x}', f'{0o040600:x}'),
                           valid.replace('0 99 ', '4097 99 '), valid + '\n' + valid,
                           'x' * 4097):
            with self.subTest(descriptor=descriptor):
                with self.assertRaises((cancel._Failure, ValueError)):
                    cancel._spools(self.proof(descriptor=descriptor), 1)
        with self.assertRaises(cancel._Failure):
            cancel._spools(self.proof(entries='1'), 1)
        with self.assertRaises(cancel._Failure):
            cancel._spools(self.proof(mount=f'42 1 8:2 / {cancel.SPOOL} rw - ext4 disk rw'), 1)

    def test_device_encoding_is_linux_even_on_a_macos_parent(self):
        self.assertEqual(cancel._linux_device(2049), '8:1')
        # Exercise Linux's high major/minor bits, not the host os.makedev.
        encoded = 0x123459abcde678f0
        self.assertEqual(cancel._linux_device(encoded), '305419896:2596069104')


class SocketControls(unittest.TestCase):
    def request(self, fault=None):
        class Socket:
            def __init__(self):
                self.stopped = threading.Event()
                self.sent = b''
                self.closed = False

            def settimeout(self, timeout):
                if timeout <= 0:
                    raise AssertionError('unbounded socket')

            def connect(self, endpoint):
                if endpoint != ('127.0.0.1', 12345):
                    raise AssertionError('wrong endpoint')
                if fault == 'connect':
                    raise RuntimeError(CANARY)

            def sendall(self, data):
                self.sent = data
                if fault == 'send':
                    raise RuntimeError(CANARY)

            def recv(self, size):
                if size != 1:
                    raise AssertionError('body read')
                self.stopped.wait(1)
                if fault == 'recv':
                    raise RuntimeError(CANARY)
                return b'H' if fault == 'headers' else b''

            def shutdown(self, direction):
                if direction != cancel.socket.SHUT_RDWR:
                    raise AssertionError('not full shutdown')
                self.stopped.set()

            def close(self):
                self.closed = True
                self.stopped.set()

        sock = Socket()
        proof = types.SimpleNamespace(server_name='owned', published_port=lambda *args: 12345)
        return cancel._Request(proof, 'http://127.0.0.1:12345', 'synthetic-token'), sock

    def test_actual_request_thread_joins_after_socket_shutdown_before_headers(self):
        request, sock = self.request()
        with patch.object(cancel.socket, 'socket', return_value=sock), \
             patch.object(cancel.select, 'select', return_value=([], [], [])):
            try:
                request.start()
                self.assertTrue(request.pending())
                self.assertTrue(request.disconnect())
                self.assertFalse(request.thread.is_alive())
                self.assertIn(b'GET /communications/messages/list HTTP/1.1', sock.sent)
                self.assertIn(b'Authorization: Bearer synthetic-token', sock.sent)
            finally:
                request.close()
        self.assertTrue(sock.closed)

    def test_connection_send_receive_and_early_headers_fail_and_close(self):
        for fault in ('connect', 'send', 'recv', 'headers'):
            with self.subTest(fault=fault):
                request, sock = self.request(fault)
                with patch.object(cancel.socket, 'socket', return_value=sock), \
                     patch.object(cancel.select, 'select', return_value=([], [], [])), \
                     patch.object(cancel, 'IO_SECONDS', 0.02):
                    try:
                        try:
                            request.start()
                            self.assertFalse(request.disconnect())
                        except (cancel._Failure, RuntimeError):
                            pass
                    finally:
                        request.close()
                self.assertTrue(sock.closed)
                self.assertTrue(request.thread is None or not request.thread.is_alive())

    def test_only_exact_parent_loopback_http_endpoint_is_accepted(self):
        proof = types.SimpleNamespace(server_name='owned', published_port=lambda *args: 12345)
        for base in ('http://127.0.0.1:12346', 'http://localhost:12345',
                     'http://127.0.0.1:12345/path', 'https://127.0.0.1:12345',
                     'http://private:12345', 'http://127.0.0.1:0'):
            with self.assertRaises(cancel._Failure):
                cancel._Request(proof, base, 'token')


class ImportControls(unittest.TestCase):
    def test_import_does_not_load_phase1_django_or_open_resources(self):
        path = Path(cancel.__file__)
        spec = importlib.util.spec_from_file_location('isolated_query_cancel', path)
        module = importlib.util.module_from_spec(spec)
        import builtins
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.startswith(('django', 'psycopg', 'TESTS.adapters.go.message_list_spool_image')):
                raise AssertionError('eager runtime dependency')
            return original(name, *args, **kwargs)

        with patch.object(builtins, '__import__', side_effect=guarded), \
             patch.object(cancel.socket, 'socket', side_effect=AssertionError('eager socket')):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.run_query_cancellation))
        self.assertTrue(callable(module.validate_query_cancellation))


if __name__ == '__main__':
    unittest.main()
