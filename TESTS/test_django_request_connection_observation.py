"""Offline synthetic controls; these tests supply no live PostgreSQL evidence."""
import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from asgiref.sync import sync_to_async
from django.dispatch import Signal

from TESTS.adapters.django.request_connection_observation import RequestConnectionObserver

RUN = 'e7e3ee59-0012-4a51-9ebc-006fc39fe001'


class OfflineLoop(asyncio.SelectorEventLoop):
    """Test scheduler only: no asyncio socketpair self-pipe or network I/O.

    Bounded polling services thread-safe asgiref callbacks without wakeup sockets.
    The production adapter neither creates nor replaces the parent's event loop.
    """
    def _make_self_pipe(self):
        pass

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        pass

    def _run_once(self):
        self.call_later(0.001, lambda: None)
        super()._run_once()


def run(coroutine):
    with patch('socket.socket', side_effect=AssertionError('offline socket forbidden')), \
            patch('socket.socketpair', side_effect=AssertionError('offline socketpair forbidden')), \
            asyncio.Runner(loop_factory=OfflineLoop) as runner:
        return runner.run(coroutine)


class Native:
    def __init__(self, pid=431):
        self._pid = pid
        self.lookups = 0
        self.closed = 0

    @property
    def info(self):
        self.lookups += 1
        return SimpleNamespace(backend_pid=self._pid)


class Connection:
    vendor = 'postgresql'

    def __init__(self, native=None):
        self.connection = native if native is not None else Native()
        self.execute_wrappers = []

    def execute(self, operation=lambda: 'original-result', *, sql='PRIVATE SQL', params=('SECRET',)):
        context = {'connection': self}
        def execute(sql, params, many, context):
            return operation()
        for wrapper in reversed(self.execute_wrappers):
            previous = execute
            execute = lambda s, p, m, c, w=wrapper, e=previous: w(e, s, p, m, c)
        return execute(sql, params, False, context)


@contextmanager
def installation(observer, *connections):
    signal = Signal()
    with patch('django.db.connections', SimpleNamespace(all=lambda: list(connections))), \
            patch('django.db.backends.signals.connection_created', signal):
        with observer.installed():
            yield signal


async def receive():
    return {'type': 'http.request', 'body': b'', 'more_body': False}


async def response(send, headers=()):
    await send({'type': 'http.response.start', 'status': 204, 'headers': list(headers)})
    await send({'type': 'http.response.body', 'body': b''})


async def call(app, *, headers=(), kind='http', scope=None):
    sent = []
    async def send(message):
        sent.append(message)
    await app(scope or {'type': kind, 'path': '/PRIVATE', 'headers': list(headers)}, receive, send)
    return sent


class FailingDetach(list):
    """Synthetic registry that records attempts and can fail until repaired."""
    def __init__(self, failure, attempts, name, values=()):
        super().__init__(values)
        self.failure, self.attempts, self.name = failure, attempts, name

    def __delitem__(self, index):
        self.attempts.append(self.name)
        if self.failure is not None:
            raise self.failure
        super().__delitem__(index)


class ObservationTests(unittest.TestCase):
    def _assert_failed_cleanup(self, observer):
        snapshot = observer.snapshot()
        self.assertTrue(snapshot['closed'])
        self.assertTrue(snapshot['invalidated'])
        self.assertFalse(snapshot['valid'])
        self.assertFalse(snapshot['complete'])
        footers = [event for event in snapshot['events'] if event['type'] == 'footer']
        self.assertEqual(len(footers), 1)
        self.assertFalse(footers[0]['complete'])
        self.assertNotIn('SECRET', json.dumps(snapshot))
        return snapshot

    def test_signal_cleanup_control_preserves_identity_and_retries_without_new_footer(self):
        for kind in (KeyboardInterrupt, SystemExit, asyncio.CancelledError, BaseException):
            with self.subTest(kind=kind.__name__):
                observer, connection = RequestConnectionObserver(RUN), Connection()
                failure = kind('SECRET signal cancellation', 17)
                expected_args = failure.args
                sibling = object()
                connection.execute_wrappers.append(sibling)
                with installation(observer, connection) as signal:
                    with patch.object(signal, 'disconnect', side_effect=failure) as disconnect:
                        for _ in range(2):
                            with self.assertRaises(kind) as raised:
                                observer.close()
                            self.assertIs(raised.exception, failure)
                            self.assertEqual(raised.exception.args, expected_args)
                            self.assertEqual(connection.execute_wrappers, [sibling])
                            self.assertEqual(observer.snapshot()['attached_connections'], 0)
                            self._assert_failed_cleanup(observer)
                        self.assertEqual(disconnect.call_count, 2)
                    snapshot = observer.close()
                    self.assertEqual(snapshot, self._assert_failed_cleanup(observer))
                    self.assertEqual(signal.receivers, [])

    def test_wrapper_cleanup_control_preserves_identity_and_other_wrapper_order(self):
        for kind in (KeyboardInterrupt, SystemExit, asyncio.CancelledError, BaseException):
            with self.subTest(kind=kind.__name__):
                observer, first, second = RequestConnectionObserver(RUN), Connection(), Connection()
                failure, attempts = kind('SECRET wrapper cancellation', 23), []
                expected_args = failure.args
                before, after = object(), object()
                first.execute_wrappers = FailingDetach(failure, attempts, 'first', [before])
                second.execute_wrappers = FailingDetach(None, attempts, 'second')
                with installation(observer, first, second) as signal:
                    first.execute_wrappers.append(after)
                    with self.assertRaises(kind) as raised:
                        observer.close()
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(raised.exception.args, expected_args)
                    self.assertEqual(attempts, ['first', 'second'])
                    self.assertEqual(signal.receivers, [])
                    self.assertEqual(second.execute_wrappers, [])
                    self.assertEqual(self._assert_failed_cleanup(observer)['attached_connections'], 1)
                    first.execute_wrappers.failure = None
                    observer.close()
                    self.assertEqual(attempts, ['first', 'second', 'first'])
                    self.assertEqual(first.execute_wrappers, [before, after])
                    self.assertEqual(self._assert_failed_cleanup(observer)['attached_connections'], 0)

    def test_multiple_cleanup_faults_attempt_every_detachment_and_raise_first_control(self):
        for kinds in ((KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                      (OSError, SystemExit, asyncio.CancelledError),
                      (None, OSError, asyncio.CancelledError)):
            with self.subTest(kinds=kinds):
                failures = [kind('SECRET cleanup fault', index) if kind else None
                            for index, kind in enumerate(kinds)]
                expected = next(error for error in failures
                                if error is not None and not isinstance(error, Exception))
                expected_args = expected.args
                observer = RequestConnectionObserver(RUN)
                connections, attempts = [Connection() for _ in range(3)], []
                for index, connection in enumerate(connections):
                    connection.execute_wrappers = FailingDetach(
                        failures[index + 1] if index < 2 else None, attempts, index)
                with installation(observer, *connections) as signal:
                    original_disconnect = signal.disconnect
                    def disconnect(*args, **kwargs):
                        attempts.append('signal')
                        if failures[0] is not None:
                            raise failures[0]
                        return original_disconnect(*args, **kwargs)
                    with patch.object(signal, 'disconnect', side_effect=disconnect):
                        with self.assertRaises(type(expected)) as raised:
                            observer.close()
                    self.assertIs(raised.exception, expected)
                    self.assertEqual(raised.exception.args, expected_args)
                    self.assertEqual(attempts, ['signal', 0, 1, 2])
                    self.assertEqual(connections[2].execute_wrappers, [])
                    self.assertEqual(self._assert_failed_cleanup(observer)['attached_connections'], 2)
                    for connection in connections:
                        connection.execute_wrappers.failure = None
                    observer.close()
                    self.assertEqual(attempts, ['signal', 0, 1, 2, 0, 1])
                    self.assertEqual(signal.receivers, [])
                    self.assertEqual(self._assert_failed_cleanup(observer)['attached_connections'], 0)

    def test_installed_normal_exit_propagates_cleanup_control(self):
        for location in ('signal', 'wrapper'):
            for kind in (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                with self.subTest(location=location, kind=kind.__name__):
                    observer, connection, signal = RequestConnectionObserver(RUN), Connection(), Signal()
                    failure, attempts = kind('SECRET exit cancellation', 29), []
                    expected_args = failure.args
                    connection.execute_wrappers = FailingDetach(
                        failure if location == 'wrapper' else None, attempts, 'wrapper')
                    disconnect = signal.disconnect
                    with patch('django.db.connections', SimpleNamespace(all=lambda: [connection])), \
                            patch('django.db.backends.signals.connection_created', signal), \
                            patch.object(signal, 'disconnect', side_effect=(
                                failure if location == 'signal' else disconnect)):
                        with self.assertRaises(kind) as raised:
                            with observer.installed():
                                pass
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(raised.exception.args, expected_args)
                    self.assertEqual(attempts, ['wrapper'])
                    self._assert_failed_cleanup(observer)
                    connection.execute_wrappers.failure = None
                    observer.close()
                    self.assertEqual(signal.receivers, [])
                    self.assertEqual(connection.execute_wrappers, [])
                    self._assert_failed_cleanup(observer)

    def test_installed_cleanup_preserves_original_application_and_execute_failures(self):
        for source in ('application', 'execute'):
            for original_kind in (RuntimeError, asyncio.CancelledError):
                for cleanup_kind in (OSError, KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                    with self.subTest(source=source, original=original_kind.__name__,
                                      cleanup=cleanup_kind.__name__):
                        observer, connection, signal = RequestConnectionObserver(RUN), Connection(), Signal()
                        original = original_kind('SECRET original failure', 31)
                        expected_args = original.args
                        cleanup, attempts = cleanup_kind('SECRET cleanup', 37), []
                        connection.execute_wrappers = FailingDetach(cleanup, attempts, 'wrapper')
                        def execute():
                            raise original
                        async def app(scope, receive, send):
                            if source == 'execute':
                                connection.execute(execute)
                            raise original
                        with patch('django.db.connections', SimpleNamespace(all=lambda: [connection])), \
                                patch('django.db.backends.signals.connection_created', signal), \
                                patch.object(signal, 'disconnect', side_effect=cleanup) as disconnect:
                            with self.assertRaises(original_kind) as raised:
                                with observer.installed():
                                    run(call(observer.wrap(app)))
                            self.assertEqual(disconnect.call_count, 1)
                        self.assertIs(raised.exception, original)
                        self.assertEqual(raised.exception.args, expected_args)
                        self.assertEqual(attempts, ['wrapper'])
                        snapshot = self._assert_failed_cleanup(observer)
                        self.assertEqual(snapshot['counters']['request_errors'], 1)
                        self.assertEqual(snapshot['counters']['query_errors'], int(source == 'execute'))
                        connection.execute_wrappers.failure = None
                        observer.close()
                        self.assertEqual(signal.receivers, [])
                        self.assertEqual(connection.execute_wrappers, [])
                        self._assert_failed_cleanup(observer)

    def test_installed_cleanup_preserves_original_body_control(self):
        for kind in (KeyboardInterrupt, SystemExit, BaseException):
            with self.subTest(kind=kind.__name__):
                observer, connection, signal = RequestConnectionObserver(RUN), Connection(), Signal()
                original, attempts = kind('SECRET original control', 41), []
                expected_args = original.args
                connection.execute_wrappers = FailingDetach(SystemExit('SECRET wrapper'), attempts, 'wrapper')
                with patch('django.db.connections', SimpleNamespace(all=lambda: [connection])), \
                        patch('django.db.backends.signals.connection_created', signal), \
                        patch.object(signal, 'disconnect', side_effect=KeyboardInterrupt('SECRET signal')):
                    with self.assertRaises(kind) as raised:
                        with observer.installed():
                            raise original
                self.assertIs(raised.exception, original)
                self.assertEqual(raised.exception.args, expected_args)
                self.assertEqual(attempts, ['wrapper'])
                self._assert_failed_cleanup(observer)
                connection.execute_wrappers.failure = None
                observer.close()
                self.assertEqual(signal.receivers, [])
                self.assertEqual(connection.execute_wrappers, [])
                self._assert_failed_cleanup(observer)

    def test_partial_install_preserves_original_failure_despite_cleanup_control(self):
        for phase in ('connect', 'scan'):
            for kind in (RuntimeError, KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                with self.subTest(phase=phase, kind=kind.__name__):
                    observer, connection, signal = RequestConnectionObserver(RUN), Connection(), Signal()
                    original, attempts = kind('SECRET partial installation', 43), []
                    expected_args = original.args
                    connection.execute_wrappers = FailingDetach(SystemExit('SECRET wrapper'), attempts, 'wrapper')
                    original_connect = signal.connect
                    def connect(*args, **kwargs):
                        original_connect(*args, **kwargs)
                        if phase == 'connect':
                            raise original
                    def scan():
                        yield connection
                        raise original
                    with patch('django.db.connections', SimpleNamespace(all=scan)), \
                            patch('django.db.backends.signals.connection_created', signal), \
                            patch.object(signal, 'connect', side_effect=connect), \
                            patch.object(signal, 'disconnect', side_effect=asyncio.CancelledError('SECRET signal')) as disconnect:
                        with self.assertRaises(kind) as raised:
                            with observer.installed():
                                self.fail('partial installation must fail before the body')
                        self.assertEqual(disconnect.call_count, 1)
                    self.assertIs(raised.exception, original)
                    self.assertEqual(raised.exception.args, expected_args)
                    self.assertEqual(attempts, ['wrapper'] if phase == 'scan' else [])
                    self._assert_failed_cleanup(observer)
                    connection.execute_wrappers.failure = None
                    observer.close()
                    self.assertEqual(signal.receivers, [])
                    self.assertEqual(connection.execute_wrappers, [])
                    self._assert_failed_cleanup(observer)

    def test_ordinary_cleanup_errors_return_sticky_failed_snapshot(self):
        observer, first, second = RequestConnectionObserver(RUN), Connection(), Connection()
        attempts = []
        first.execute_wrappers = FailingDetach(OSError('SECRET wrapper'), attempts, 'first')
        second.execute_wrappers = FailingDetach(None, attempts, 'second')
        with installation(observer, first, second) as signal:
            with patch.object(signal, 'disconnect', side_effect=RuntimeError('SECRET signal')):
                snapshot = observer.close()
            self.assertEqual(snapshot, self._assert_failed_cleanup(observer))
            self.assertEqual(attempts, ['first', 'second'])
            self.assertEqual(snapshot['attached_connections'], 1)
            first.execute_wrappers.failure = None
            snapshot = observer.close()
            self.assertEqual(snapshot, self._assert_failed_cleanup(observer))
            self.assertEqual(attempts, ['first', 'second', 'first'])
            self.assertEqual(signal.receivers, [])
            self.assertEqual(first.execute_wrappers, [])

    def test_binding_visible_before_execute_return_and_thread_context_propagates(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        original = lambda *args: args[0](args[1], args[2], args[3], args[4])
        connection.execute_wrappers.append(original)
        captured = []
        def blocked():
            captured.append(observer.snapshot())
            return 'original-result'
        async def app(scope, receive, send):
            self.assertEqual(await sync_to_async(connection.execute)(blocked), 'original-result')
            await response(send)
        with installation(observer, connection):
            messages = run(call(observer.wrap(app), headers=[(b'x-correlation-id', b'SECRET')]))
            snap = observer.snapshot()
            self.assertTrue(snap['valid'])
            self.assertEqual(snap['counters']['active_queries'], 0)
            start = next(e for e in captured[0]['events'] if e['type'] == 'query_start')
            self.assertEqual(start['backend_pid'], 431)
            self.assertEqual(start['request_id'], RUN + '-1')
            self.assertEqual(captured[0]['counters']['active_requests'], 1)
            self.assertEqual(captured[0]['counters']['active_queries'], 1)
            self.assertIn((b'x-query-observation-id', (RUN + '-1').encode()), messages[0]['headers'])
            self.assertEqual(connection.execute_wrappers[0], original)
        self.assertEqual(connection.execute_wrappers, [original])
        self.assertTrue(observer.snapshot()['complete'])
        self.assertGreaterEqual(connection.connection.lookups, 2)
        self.assertNotIn('SECRET', json.dumps(observer.snapshot()))
        self.assertNotIn('PRIVATE', json.dumps(observer.snapshot()))

    def test_extra_requests_and_unattributed_queries_remain_visible(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        async def app(scope, receive, send):
            connection.execute()
            await response(send)
        with installation(observer, connection):
            connection.execute()
            cursor = observer.cursor()
            for _ in range(2):
                run(call(observer.wrap(app)))
            events = observer.delta(cursor)
            self.assertEqual(len([e for e in events if e['type'] == 'request_begin']), 2)
            self.assertEqual(observer.snapshot()['counters']['unattributed_queries'], 1)
            self.assertIsNone(next(e for e in observer.snapshot()['events'] if e['type'] == 'query_start')['request_id'])
        self.assertTrue(observer.snapshot()['complete'])

    def test_response_spoof_and_caller_spoof_are_never_trusted(self):
        for incoming, outgoing in (([(b'X-Query-Observation-ID', b'pretend')], []),
                ([], [(b'X-Query-Observation-ID', b'pretend'), (b'x-query-observation-id', b'again')])):
            with self.subTest(incoming=incoming, outgoing=outgoing):
                observer = RequestConnectionObserver(RUN)
                async def app(scope, receive, send):
                    await response(send, outgoing)
                with installation(observer):
                    messages = run(call(observer.wrap(app), headers=incoming))
                    self.assertFalse(observer.snapshot()['valid'])
                    values = [v for k, v in messages[0]['headers'] if k.lower() == b'x-query-observation-id']
                    self.assertEqual(values, [(RUN + '-1').encode()])
                self.assertNotIn('pretend', json.dumps(observer.snapshot()))

    def test_native_lookup_never_uses_wrapper_or_caller_pid_and_reuse_is_allowed(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        connection.backend_pid = 999
        connection.get_backend_pid = lambda: 998
        async def app(scope, receive, send):
            connection.execute()
            await response(send)
        with installation(observer, connection):
            for _ in range(3):
                run(call(observer.wrap(app)))
        snapshot = observer.snapshot()
        self.assertTrue(snapshot['complete'])
        self.assertEqual({e['backend_pid'] for e in snapshot['events'] if 'backend_pid' in e}, {431})
        self.assertEqual(len({e['query_id'] for e in snapshot['events'] if e['type'] == 'query_start'}), 3)
        self.assertEqual(observer._bindings, {})
        self.assertEqual(observer._queries, {})
        self.assertEqual(observer._requests, {})

    def test_zero_missing_closed_unsupported_and_throwing_native_info_are_invalid(self):
        class BrokenInfo(Native):
            @property
            def info(self):
                raise RuntimeError('PRIVATE info error')
        natives = [Native(0), Native(-1), Native(True), Native(None), BrokenInfo(),
                   SimpleNamespace(closed=0), SimpleNamespace(closed=1, info=SimpleNamespace(backend_pid=8))]
        for native in natives:
            with self.subTest(native=type(native).__name__):
                observer, connection = RequestConnectionObserver(RUN), Connection(native)
                with installation(observer, connection):
                    self.assertEqual(connection.execute(), 'original-result')
                    self.assertFalse(observer.snapshot()['valid'])
                self.assertFalse(observer.snapshot()['complete'])
                self.assertFalse(any('backend_pid' in e for e in observer.snapshot()['events']))
                self.assertNotIn('PRIVATE', json.dumps(observer.snapshot()))
        for missing, vendor in ((True, 'postgresql'), (False, 'sqlite')):
            observer, connection = RequestConnectionObserver(RUN), Connection()
            connection.vendor = vendor
            if missing:
                connection.connection = None
            with installation(observer, connection):
                connection.execute()
            self.assertFalse(observer.snapshot()['complete'])

    def test_native_mismatch_pid_change_and_connection_loss_cannot_complete(self):
        for mutation in (lambda c: setattr(c, 'connection', Native(431)),
                         lambda c: setattr(c.connection, '_pid', 432),
                         lambda c: setattr(c.connection, 'closed', 1),
                         lambda c: setattr(c, 'connection', None)):
            observer, connection = RequestConnectionObserver(RUN), Connection()
            with installation(observer, connection):
                connection.execute(lambda: mutation(connection))
            events = observer.snapshot()['events']
            self.assertEqual(next(e for e in events if e['type'] == 'query_start')['backend_pid'], 431)
            self.assertNotIn('backend_pid', next(e for e in events if e['type'] == 'query_end'))
            self.assertFalse(observer.snapshot()['complete'])
            self.assertEqual(observer._bindings, {})

    def test_context_connection_replacement_and_unregistered_callback_fail_closed(self):
        observer, connection, other = RequestConnectionObserver(RUN), Connection(), Connection(Native(432))
        context = {'connection': connection}
        def execute(sql, params, many, ctx):
            self.assertIs(ctx, context)
            ctx['connection'] = other
            return 17
        with installation(observer, connection):
            self.assertEqual(observer._wrapper(execute, 'PRIVATE', ('SECRET',), False, context), 17)
        self.assertFalse(observer.snapshot()['complete'])
        second = RequestConnectionObserver(RUN)
        with installation(second):
            second._wrapper(lambda *args: 19, 'PRIVATE', None, False, {'connection': other})
        self.assertFalse(second.snapshot()['complete'])
        self.assertFalse(any('backend_pid' in e for e in second.snapshot()['events']))

    def test_original_execute_arguments_return_exception_and_cancellation_preserved(self):
        for failure in (None, RuntimeError('SECRET SQL ERROR'), asyncio.CancelledError('SECRET cancellation')):
            observer, connection = RequestConnectionObserver(RUN), Connection()
            sql, params, context, result = object(), object(), {'connection': connection}, object()
            def execute(s, p, m, c):
                self.assertIs(s, sql)
                self.assertIs(p, params)
                self.assertTrue(m)
                self.assertIs(c, context)
                if failure is not None:
                    raise failure
                return result
            with installation(observer, connection):
                try:
                    value = observer._wrapper(execute, sql, params, True, context)
                except BaseException as error:
                    self.assertIs(error, failure)
                else:
                    self.assertIsNone(failure)
                    self.assertIs(value, result)
            self.assertEqual(observer.snapshot()['complete'], failure is None)
            self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
            self.assertEqual(observer.snapshot()['counters']['query_errors'], int(failure is not None))
            self.assertNotIn('SECRET', json.dumps(observer.snapshot()))

    def test_nested_queries_and_simultaneous_native_reuse_invalidate(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        with installation(observer, connection):
            connection.execute(lambda: connection.execute())
        self.assertFalse(observer.snapshot()['complete'])
        self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
        self.assertEqual(observer.snapshot()['counters']['active_bindings'], 0)
        self.assertGreater(observer.snapshot()['counters']['connection_violations'], 0)

    def test_independent_threads_cannot_bind_overlapping_native_object_or_pid(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        for same_object in (False, True):
            observer = RequestConnectionObserver(RUN)
            first = Connection()
            second = Connection(first.connection if same_object else Native(431))
            entered, release = threading.Barrier(3), threading.Event()
            def operation():
                entered.wait(timeout=3)
                if not release.wait(3):
                    raise AssertionError('test release timeout')
                return 'unchanged'
            with installation(observer, first, second), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(c.execute, operation) for c in (first, second)]
                try:
                    entered.wait(timeout=3)
                    self.assertFalse(observer.snapshot()['valid'])
                    self.assertEqual(observer.snapshot()['counters']['active_queries'], 2)
                finally:
                    release.set()
                self.assertEqual([f.result(timeout=3) for f in futures], ['unchanged', 'unchanged'])
            self.assertFalse(observer.snapshot()['complete'])
            self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
            self.assertEqual(observer.snapshot()['counters']['active_bindings'], 0)

    def test_request_lives_until_application_return_after_final_body(self):
        observer = RequestConnectionObserver(RUN)
        async def app(scope, receive, send):
            await response(send)
            snapshot = observer.snapshot()
            self.assertEqual(snapshot['counters']['active_requests'], 1)
            self.assertEqual(snapshot['counters']['request_finishes'], 0)
        with installation(observer):
            run(call(observer.wrap(app)))
        self.assertTrue(observer.snapshot()['complete'])

    def test_outstanding_real_wrapper_lifetime_at_request_return_stays_invalid(self):
        import threading
        observer, connection = RequestConnectionObserver(RUN), Connection()
        entered, release = threading.Event(), threading.Event()
        async def exercise():
            tasks = []
            def operation():
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test release timeout')
            async def app(scope, receive, send):
                tasks.append(asyncio.create_task(sync_to_async(
                    connection.execute, thread_sensitive=False)(operation)))
                self.assertTrue(await sync_to_async(entered.wait, thread_sensitive=False)(3))
                await response(send)
            try:
                await call(observer.wrap(app))
                self.assertFalse(observer.snapshot()['valid'])
                self.assertEqual(observer.snapshot()['counters']['active_requests'], 0)
                self.assertEqual(observer.snapshot()['counters']['active_queries'], 1)
            finally:
                release.set()
                await asyncio.gather(*tasks)
        with installation(observer, connection):
            run(exercise())
        self.assertFalse(observer.snapshot()['complete'])
        self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)

    def test_non_http_nested_scope_clears_outer_http_attribution(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        async def child(scope, receive, send):
            connection.execute()
            await send({'type': 'websocket.close'})
        async def app(scope, receive, send):
            await call(observer.wrap(child), kind='websocket')
            connection.execute()
            await response(send)
        with installation(observer, connection):
            run(call(observer.wrap(app)))
        self.assertTrue(observer.snapshot()['complete'])
        starts = [e for e in observer.snapshot()['events'] if e['type'] == 'query_start']
        self.assertEqual([e['request_id'] for e in starts], [None, RUN + '-1'])

    def test_concurrent_distinct_requests_visible_before_release_with_same_caller_cid(self):
        import threading
        observer = RequestConnectionObserver(RUN)
        connections = [Connection(Native(431)), Connection(Native(432))]
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()
        async def exercise():
            async def app(scope, receive, send):
                index = scope['index']
                def operation():
                    entered[index].set()
                    if not release.wait(3):
                        raise AssertionError('test release timeout')
                await sync_to_async(connections[index].execute, thread_sensitive=False)(operation)
                await response(send)
            tasks = [asyncio.create_task(call(observer.wrap(app), scope={
                'type': 'http', 'index': i, 'headers': [(b'x-correlation-id', b'same')]}
            )) for i in range(2)]
            try:
                for event in entered:
                    self.assertTrue(await sync_to_async(event.wait, thread_sensitive=False)(3))
                snapshot = observer.snapshot()
                self.assertTrue(snapshot['valid'])
                self.assertEqual(snapshot['counters']['active_requests'], 2)
                self.assertEqual(snapshot['counters']['active_queries'], 2)
                starts = [e for e in snapshot['events'] if e['type'] == 'query_start']
                self.assertEqual({e['request_id'] for e in starts}, {RUN + '-1', RUN + '-2'})
                self.assertEqual({e['backend_pid'] for e in starts}, {431, 432})
            finally:
                release.set()
                await asyncio.gather(*tasks)
        with installation(observer, *connections):
            run(exercise())
        self.assertTrue(observer.snapshot()['complete'])

    def test_nested_and_cross_observer_requests_invalidate_both(self):
        for cross in (False, True):
            outer = RequestConnectionObserver(RUN)
            inner = RequestConnectionObserver('e7e3ee59-0012-4a51-9ebc-006fc39fe002') if cross else outer
            async def leaf(scope, receive, send):
                await response(send)
            async def app(scope, receive, send):
                await call(inner.wrap(leaf))
                await response(send)
            with installation(outer):
                if cross:
                    with installation(inner):
                        run(call(outer.wrap(app)))
                else:
                    run(call(outer.wrap(app)))
            self.assertFalse(outer.snapshot()['complete'])
            self.assertFalse(inner.snapshot()['complete'])

    def test_late_copied_context_query_and_late_send_invalidate(self):
        from contextvars import copy_context
        observer, connection, captured = RequestConnectionObserver(RUN), Connection(), []
        async def app(scope, receive, send):
            captured.extend([copy_context(), send])
            await response(send)
        with installation(observer, connection):
            run(call(observer.wrap(app)))
            captured[0].run(connection.execute)
            self.assertFalse(observer.snapshot()['valid'])
        second = RequestConnectionObserver(RUN)
        captured.clear()
        with installation(second):
            run(call(second.wrap(app)))
            run(captured[1]({'type': 'http.response.body', 'body': b'SECRET'}))
        self.assertFalse(second.snapshot()['complete'])

    def test_duplicate_and_cross_context_query_ends_are_sticky_without_counter_underflow(self):
        from contextvars import Context
        from TESTS.adapters.django import request_connection_observation as module
        for cross in (False, True):
            observer, connection, queries = RequestConnectionObserver(RUN), Connection(), []
            context = {'connection': connection}
            def execute(*args):
                query = module._QUERY.get()
                queries.append(query)
                if cross:
                    Context().run(observer._end_query, query, context, False)
            with installation(observer, connection):
                observer._wrapper(execute, '', None, False, context)
                if not cross:
                    observer._end_query(queries[0], context, False)
            self.assertFalse(observer.snapshot()['complete'])
            self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
            self.assertEqual(observer._bindings, {})
            self.assertIsNone(queries[0].native)
            self.assertIsNone(queries[0].connection)

    def test_copied_execution_context_or_replaced_django_context_cannot_end_live_query(self):
        from contextvars import copy_context
        from TESTS.adapters.django import request_connection_observation as module
        for copied in (False, True):
            observer, connection = RequestConnectionObserver(RUN), Connection()
            context = {'connection': connection}
            def execute(*args):
                query = module._QUERY.get()
                if copied:
                    # Same values and same thread, but not the context that began
                    # the execute lifetime. This is not asgiref's valid request
                    # propagation BEFORE the synchronous execute wrapper starts.
                    copy_context().run(observer._end_query, query, context, False)
                else:
                    observer._end_query(query, dict(context), False)
                self.assertFalse(observer.snapshot()['valid'])
                self.assertEqual(observer.snapshot()['counters']['active_queries'], 1)
                self.assertIs(query.native, connection.connection)
                return 'original-outcome'
            with installation(observer, connection):
                self.assertEqual(observer._wrapper(execute, '', None, False, context), 'original-outcome')
            self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
            self.assertFalse(observer.snapshot()['complete'])

    def test_duplicate_request_finish_and_missing_query_end_are_invalid(self):
        from TESTS.adapters.django import request_connection_observation as module
        observer, captured = RequestConnectionObserver(RUN), []
        async def app(scope, receive, send):
            captured.append(module._REQUEST.get())
            await response(send)
        with installation(observer):
            run(call(observer.wrap(app)))
            observer._end_request(captured[0], captured[0].request_id, False)
        self.assertFalse(observer.snapshot()['complete'])
        self.assertEqual(observer.snapshot()['counters']['active_requests'], 0)
        missing, connection = RequestConnectionObserver(RUN), Connection()
        context = {'connection': connection}
        with installation(missing, connection):
            query = missing._begin_query(context)  # synthetic missing callback
        self.assertFalse(missing.snapshot()['complete'])
        self.assertEqual(missing.snapshot()['counters']['active_queries'], 1)
        token = module._QUERY.set(query)
        try:
            missing._end_query(query, context, False)
        finally:
            module._QUERY.reset(token)
        self.assertFalse(missing.snapshot()['complete'])
        self.assertGreater(missing.snapshot()['counters']['post_close_events'], 0)

    def test_copied_request_context_cannot_finish_live_http_lifetime(self):
        from contextvars import copy_context
        from TESTS.adapters.django import request_connection_observation as module
        observer = RequestConnectionObserver(RUN)
        async def app(scope, receive, send):
            request = module._REQUEST.get()
            copy_context().run(observer._end_request, request, request.request_id, False)
            self.assertFalse(observer.snapshot()['valid'])
            self.assertEqual(observer.snapshot()['counters']['active_requests'], 1)
            await response(send)
        with installation(observer):
            run(call(observer.wrap(app)))
        self.assertFalse(observer.snapshot()['complete'])
        self.assertEqual(observer.snapshot()['counters']['active_requests'], 0)

    def test_partial_install_failure_preserves_error_and_detaches_signal(self):
        from django.db.backends import signals
        signal = Signal()
        observer = RequestConnectionObserver(RUN)
        error = RuntimeError('SECRET installation')
        with patch('django.db.connections', SimpleNamespace(all=unittest.mock.Mock(side_effect=error))), \
                patch.object(signals, 'connection_created', signal):
            with self.assertRaises(RuntimeError) as raised:
                with observer.installed():
                    self.fail('installation must fail before body')
        self.assertIs(raised.exception, error)
        self.assertEqual(signal.receivers, [])
        self.assertFalse(observer.snapshot()['complete'])
        self.assertNotIn('SECRET', json.dumps(observer.snapshot()))

    def test_cleanup_failure_cannot_replace_original_execute_exception(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        failure = RuntimeError('SECRET original')
        def execute():
            observer.close()
            raise failure
        with installation(observer, connection) as signal:
            with patch.object(signal, 'disconnect', side_effect=OSError('SECRET cleanup')):
                with self.assertRaises(RuntimeError) as raised:
                    connection.execute(execute)
            self.assertIs(raised.exception, failure)
            self.assertEqual(connection.execute_wrappers, [])
        self.assertEqual(signal.receivers, [])
        self.assertFalse(observer.snapshot()['complete'])
        self.assertNotIn('SECRET', json.dumps(observer.snapshot()))

    def test_missing_duplicate_and_out_of_order_response_events_are_invalid(self):
        bad = [[], [{'type': 'http.response.start', 'status': 200}],
               [{'type': 'http.response.body', 'body': b''}],
               [{'type': 'http.response.start', 'status': 200}] * 2,
               [{'type': 'http.response.start', 'status': 200},
                {'type': 'http.response.body'}, {'type': 'http.response.body'}]]
        for messages in bad:
            observer = RequestConnectionObserver(RUN)
            async def app(scope, receive, send):
                for message in messages:
                    await send(message)
            with installation(observer):
                run(call(observer.wrap(app)))
            self.assertFalse(observer.snapshot()['complete'])

    def test_original_application_and_send_failures_and_cancellation_preserved(self):
        for failure in (RuntimeError('SECRET app'), asyncio.CancelledError('SECRET cancel')):
            observer = RequestConnectionObserver(RUN)
            async def app(scope, receive, send):
                raise failure
            with installation(observer):
                with self.assertRaises(type(failure)) as raised:
                    run(call(observer.wrap(app)))
                self.assertIs(raised.exception, failure)
            self.assertFalse(observer.snapshot()['complete'])
            self.assertEqual(observer.snapshot()['counters']['active_requests'], 0)
            self.assertEqual(observer.snapshot()['counters']['request_errors'], 1)
            self.assertNotIn('SECRET', json.dumps(observer.snapshot()))
        observer = RequestConnectionObserver(RUN)
        failure = OSError('SECRET send')
        async def app(scope, receive, send):
            await response(send)
        async def send(message):
            raise failure
        with installation(observer):
            with self.assertRaises(OSError) as raised:
                run(observer.wrap(app)({'type': 'http'}, receive, send))
            self.assertIs(raised.exception, failure)
        self.assertFalse(observer.snapshot()['complete'])

    def test_caught_send_failures_remain_errors_with_or_without_retry(self):
        for stage, retry in ((stage, retry) for stage in ('start', 'streaming_body', 'final_body')
                             for retry in (False, True)):
            for failure in (OSError('SECRET send'), asyncio.CancelledError('SECRET cancel')):
                with self.subTest(stage=stage, retry=retry, failure=type(failure).__name__):
                    observer, caught, sent = RequestConnectionObserver(RUN), [], []
                    messages = [
                        {'type': 'http.response.start', 'status': 204, 'headers': []},
                        {'type': 'http.response.body', 'body': b'PRIVATE', 'more_body': True},
                        {'type': 'http.response.body', 'body': b''},
                    ]
                    target = ('start', 'streaming_body', 'final_body').index(stage)
                    attempts = 0
                    async def send(message):
                        nonlocal attempts
                        attempts += 1
                        if attempts == target + 1:
                            raise failure
                        sent.append(message)
                    async def app(scope, receive, send):
                        for message in messages:
                            try:
                                await send(message)
                            except BaseException as error:
                                caught.append(error)
                                if retry:
                                    await send(message)
                        return 'original-result'
                    with installation(observer):
                        result = run(observer.wrap(app)({'type': 'http'}, receive, send))
                    snapshot = observer.snapshot()
                    self.assertEqual(result, 'original-result')
                    self.assertEqual(len(sent), 3 if retry else 2)
                    self.assertIs(caught[0], failure)
                    self.assertFalse(snapshot['valid'])
                    self.assertTrue(snapshot['invalidated'])
                    self.assertFalse(snapshot['complete'])
                    self.assertEqual(snapshot['counters']['request_errors'], 1)
                    finish = next(e for e in snapshot['events'] if e['type'] == 'request_finish')
                    self.assertTrue(finish['error'])
                    self.assertFalse(finish['complete'])
                    self.assertFalse(snapshot['events'][-1]['complete'])
                    self.assertNotIn('SECRET', json.dumps(snapshot))

    def test_caught_task_cancellation_during_send_remains_invalid(self):
        observer, caught = RequestConnectionObserver(RUN), []
        async def exercise():
            entered = asyncio.Event()
            async def send(message):
                if message['type'] == 'http.response.body':
                    entered.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError as error:
                        caught.append(error)
                        raise
            async def app(scope, receive, send):
                try:
                    await response(send)
                except asyncio.CancelledError as error:
                    caught.append(error)
                return 'caught-cancellation'
            with installation(observer):
                task = asyncio.create_task(observer.wrap(app)({'type': 'http'}, receive, send))
                await entered.wait()
                task.cancel('SECRET cancellation')
                return await task
        self.assertEqual(run(exercise()), 'caught-cancellation')
        self.assertEqual(len(caught), 2)
        self.assertIs(caught[0], caught[1])
        snapshot = observer.snapshot()
        self.assertFalse(snapshot['valid'])
        self.assertFalse(snapshot['complete'])
        self.assertTrue(snapshot['invalidated'])
        self.assertEqual(snapshot['counters']['request_errors'], 1)
        self.assertEqual(observer._active_sends, 0)
        self.assertNotIn('SECRET', json.dumps(snapshot))

    def test_escaping_final_send_failure_counts_request_once_and_preserves_exception(self):
        for failure in (OSError('SECRET final'), asyncio.CancelledError('SECRET cancel')):
            with self.subTest(failure=type(failure).__name__):
                observer = RequestConnectionObserver(RUN)
                async def send(message):
                    if message['type'] == 'http.response.body':
                        raise failure
                async def app(scope, receive, send):
                    await response(send)
                with installation(observer):
                    with self.assertRaises(type(failure)) as raised:
                        run(observer.wrap(app)({'type': 'http'}, receive, send))
                    self.assertIs(raised.exception, failure)
                snapshot = observer.snapshot()
                self.assertEqual(snapshot['counters']['request_errors'], 1)
                self.assertEqual(snapshot['counters']['active_requests'], 0)
                self.assertFalse(snapshot['complete'])
                self.assertTrue(snapshot['invalidated'])
                self.assertNotIn('SECRET', json.dumps(snapshot))

    def test_multiple_caught_send_errors_do_not_mask_original_application_error(self):
        observer, caught = RequestConnectionObserver(RUN), []
        failures = [OSError('SECRET first'), asyncio.CancelledError('SECRET second')]
        original = RuntimeError('SECRET original app')
        async def send(message):
            if message['type'] == 'http.response.body':
                raise failures[len(caught)]
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 204})
            for _ in failures:
                try:
                    await send({'type': 'http.response.body'})
                except BaseException as error:
                    caught.append(error)
            raise original
        with installation(observer):
            with self.assertRaises(RuntimeError) as raised:
                run(observer.wrap(app)({'type': 'http'}, receive, send))
            self.assertIs(raised.exception, original)
        self.assertTrue(all(actual is expected for actual, expected in zip(caught, failures)))
        self.assertEqual(len(caught), 2)
        snapshot = observer.snapshot()
        self.assertEqual(snapshot['counters']['request_errors'], 1)
        self.assertFalse(snapshot['complete'])
        self.assertTrue(snapshot['invalidated'])
        self.assertNotIn('SECRET', json.dumps(snapshot))

    def test_send_pending_at_application_return_stays_invalid_after_settlement(self):
        for failure in (None, OSError('SECRET pending'), asyncio.CancelledError('SECRET cancel')):
            with self.subTest(failure=type(failure).__name__):
                observer = RequestConnectionObserver(RUN)
                async def exercise():
                    entered, release = asyncio.Event(), asyncio.Event()
                    tasks, caught = [], []
                    async def send(message):
                        if message['type'] == 'http.response.body':
                            entered.set()
                            await release.wait()
                            if failure is not None:
                                raise failure
                    async def app(scope, receive, send):
                        await send({'type': 'http.response.start', 'status': 204})
                        tasks.append(asyncio.create_task(send({'type': 'http.response.body'})))
                        await entered.wait()
                        return 'returned-with-pending-send'
                    with installation(observer):
                        result = await observer.wrap(app)({'type': 'http'}, receive, send)
                        returned = observer.snapshot()
                        closed = observer.close()
                        release.set()
                        try:
                            await tasks[0]
                        except BaseException as error:
                            caught.append(error)
                    return result, returned, closed, caught
                result, returned, closed, caught = run(exercise())
                self.assertEqual(result, 'returned-with-pending-send')
                for snapshot in (returned, closed, observer.snapshot()):
                    self.assertFalse(snapshot['valid'])
                    self.assertTrue(snapshot['invalidated'])
                    self.assertFalse(snapshot['complete'])
                    self.assertEqual(snapshot['counters']['active_requests'], 0)
                    self.assertNotIn('SECRET', json.dumps(snapshot))
                if failure is not None:
                    self.assertIs(caught[0], failure)
                else:
                    self.assertEqual(caught, [])
                self.assertEqual(observer.snapshot()['counters']['request_errors'], int(failure is not None))
                self.assertFalse(closed['events'][-1]['complete'])
                self.assertEqual(observer._active_sends, 0)
                self.assertEqual(observer._requests, {})

    def test_send_pending_at_close_cannot_complete_even_when_application_awaits_it(self):
        observer = RequestConnectionObserver(RUN)
        async def exercise():
            async def send(message):
                if message['type'] == 'http.response.body':
                    observer.close()
            async def app(scope, receive, send):
                await response(send)
            with installation(observer):
                await observer.wrap(app)({'type': 'http'}, receive, send)
        run(exercise())
        snapshot = observer.snapshot()
        self.assertFalse(snapshot['complete'])
        self.assertTrue(snapshot['invalidated'])
        self.assertEqual(snapshot['counters']['active_requests'], 0)
        self.assertEqual(snapshot['counters']['request_errors'], 0)
        self.assertEqual(observer._active_sends, 0)

    def test_send_awaited_in_child_task_before_return_is_valid(self):
        observer, states, sent = RequestConnectionObserver(RUN), [], []
        async def send(message):
            await asyncio.sleep(0)
            states.append(observer.snapshot())
            sent.append(message)
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 204})
            await asyncio.create_task(send({'type': 'http.response.body', 'more_body': True}))
            await asyncio.create_task(send({'type': 'http.response.body'}))
            return 'original-result'
        with installation(observer):
            result = run(observer.wrap(app)({'type': 'http'}, receive, send))
        self.assertEqual(result, 'original-result')
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(s['valid'] and not s['complete'] for s in states))
        self.assertTrue(all(s['counters']['active_requests'] == 1 for s in states))
        self.assertTrue(observer.snapshot()['complete'])
        self.assertEqual(observer.snapshot()['counters']['request_errors'], 0)

    def test_overlapping_response_sends_are_rejected_even_if_both_succeed(self):
        observer = RequestConnectionObserver(RUN)
        async def exercise():
            entered, release = asyncio.Event(), asyncio.Event()
            async def send(message):
                if message.get('more_body'):
                    entered.set()
                    await release.wait()
            async def app(scope, receive, send):
                await send({'type': 'http.response.start', 'status': 204})
                task = asyncio.create_task(send({'type': 'http.response.body', 'more_body': True}))
                try:
                    await entered.wait()
                    await send({'type': 'http.response.body'})
                finally:
                    release.set()
                    await task
            with installation(observer):
                await observer.wrap(app)({'type': 'http'}, receive, send)
        run(exercise())
        snapshot = observer.snapshot()
        self.assertFalse(snapshot['valid'])
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['counters']['request_errors'], 0)
        self.assertEqual(observer._active_sends, 0)

    def test_repeated_caught_send_failures_release_request_bookkeeping(self):
        observer = RequestConnectionObserver(RUN, capacity=8)
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('SECRET transport')
        async def app(scope, receive, send):
            try:
                await response(send)
            except OSError:
                pass
        async def exercise():
            with installation(observer):
                observed = observer.wrap(app)
                for _ in range(100):
                    await observed({'type': 'http'}, receive, send)
                    self.assertEqual(observer._requests, {})
                    self.assertEqual(observer._active_sends, 0)
        run(exercise())
        snapshot = observer.snapshot()
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['counters']['request_starts'], 100)
        self.assertEqual(snapshot['counters']['request_finishes'], 100)
        self.assertEqual(snapshot['counters']['request_errors'], 100)
        self.assertEqual(snapshot['counters']['active_requests'], 0)
        self.assertLessEqual(len(snapshot['events']), observer.capacity)
        self.assertEqual(sum(e['type'] == 'invalidated' for e in snapshot['events']), 1)
        self.assertNotIn('SECRET', json.dumps(snapshot))

    def test_non_http_scopes_preserve_behavior_and_queries_are_unattributed(self):
        for kind in ('websocket', 'lifespan'):
            observer, connection = RequestConnectionObserver(RUN), Connection()
            message = {'type': kind + '.test', 'headers': [(b'x-query-observation-id', b'app')]}
            async def app(scope, receive, send):
                connection.execute()
                await send(message)
                return 72
            with installation(observer, connection):
                sent = run(call(observer.wrap(app), kind=kind))
                self.assertIs(sent[0], message)
                self.assertEqual(observer.snapshot()['counters']['request_starts'], 0)
                self.assertEqual(observer.snapshot()['counters']['unattributed_queries'], 1)
            self.assertTrue(observer.snapshot()['complete'])

    def test_new_connections_signal_and_querytrace_counters_survive_detach(self):
        from TESTS.adapters.django.message_reads import QueryTrace
        observer, connection = RequestConnectionObserver(RUN), Connection()
        extra = Connection(Native(432))
        with installation(observer, connection) as signal:
            signal.send(sender=Connection, connection=extra)
            signal.send(sender=Connection, connection=extra)
            self.assertEqual(sum(w is observer._wrapper for w in extra.execute_wrappers), 1)
            with patch('django.db.connections', SimpleNamespace(all=lambda: [connection])), \
                    patch('django.db.backends.signals.connection_created', signal):
                trace = QueryTrace()
            try:
                async def app(scope, receive, send):
                    token = trace.begin('private-fixture-label')
                    try:
                        connection.execute(sql='SELECT * FROM "communications_message"')
                    finally:
                        trace.end(token)
                    await response(send)
                run(call(observer.wrap(app)))
                self.assertEqual(trace.delta('private-fixture-label')['total'], 1)
                self.assertEqual(trace.delta('private-fixture-label')['messages'], 1)
                observer.close()
                self.assertIn(trace._trace, connection.execute_wrappers)
            finally:
                trace.close()
        self.assertEqual(extra.execute_wrappers, [])
        self.assertEqual(connection.execute_wrappers, [])
        self.assertEqual(signal.receivers, [])
        self.assertTrue(observer.snapshot()['complete'])

    def test_cleanup_attempts_signal_and_all_wrappers_independently_and_retries(self):
        class FailingList(list):
            fail = True
            def __delitem__(self, index):
                if self.fail:
                    self.fail = False
                    raise RuntimeError('SECRET cleanup')
                return super().__delitem__(index)
        observer, first, second = RequestConnectionObserver(RUN), Connection(), Connection(Native(432))
        sibling = object()
        first.execute_wrappers = FailingList([sibling])
        with installation(observer, first, second) as signal:
            with patch.object(signal, 'disconnect', side_effect=RuntimeError('SECRET disconnect')):
                observer.close()
            self.assertEqual(second.execute_wrappers, [])
            self.assertIn(sibling, first.execute_wrappers)
            self.assertFalse(observer.snapshot()['complete'])
            observer.close()
        self.assertEqual(first.execute_wrappers, [sibling])
        self.assertEqual(signal.receivers, [])
        self.assertNotIn('SECRET', json.dumps(observer.snapshot()))

    def test_close_before_drain_and_saved_wrapper_after_close_stay_invalid(self):
        observer, connection = RequestConnectionObserver(RUN), Connection()
        with installation(observer, connection):
            connection.execute(observer.close)
        snapshot = observer.snapshot()
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['counters']['active_queries'], 0)
        self.assertEqual(connection.execute_wrappers, [])
        clean = RequestConnectionObserver(RUN)
        with installation(clean, connection):
            saved = clean._wrapper
        self.assertTrue(clean.snapshot()['complete'])
        self.assertEqual(saved(lambda *args: 81, '', None, False, {'connection': connection}), 81)
        self.assertFalse(clean.snapshot()['complete'])
        self.assertGreater(clean.snapshot()['counters']['post_close_events'], 0)
        self.assertEqual(clean.snapshot()['events'][-1]['type'], 'invalidated')
        async def app(scope, receive, send):
            await response(send)
        run(call(clean.wrap(app)))
        self.assertEqual(clean.snapshot()['counters']['request_starts'], 1)
        self.assertEqual(clean.snapshot()['counters']['request_finishes'], 1)

    def test_event_byte_active_and_registration_bounds_are_sticky(self):
        for options in ({'capacity': 6}, {'capacity': 10000, 'max_bytes': 8192}):
            observer, connection = RequestConnectionObserver(RUN, **options), Connection()
            async def app(scope, receive, send):
                connection.execute()
                await response(send)
            with installation(observer, connection):
                for _ in range(100):
                    run(call(observer.wrap(app)))
            snapshot = observer.snapshot()
            self.assertTrue(snapshot['overflow'])
            self.assertFalse(snapshot['complete'])
            self.assertEqual(snapshot['counters']['request_starts'], 100)
            self.assertEqual(snapshot['counters']['request_finishes'], 100)
            self.assertLessEqual(len(snapshot['events']), observer.capacity)
            self.assertLessEqual(len(json.dumps(snapshot['events'], separators=(',', ':'))), observer.max_bytes)
            self.assertEqual(observer._requests, {})
            self.assertEqual(observer._queries, {})
            self.assertEqual(snapshot['events'][-1]['type'], 'footer')
            self.assertEqual(sum(e['type'] == 'invalidated' for e in snapshot['events']), 1)
        limited, connection = RequestConnectionObserver(RUN, max_active=1), Connection()
        with installation(limited, connection) as signal:
            extra = Connection()
            signal.send(sender=Connection, connection=extra)
            self.assertEqual(extra.execute_wrappers, [])
            self.assertEqual(limited.snapshot()['attached_connections'], 1)
            async def app(scope, receive, send):
                connection.execute()
                await response(send)
            run(call(limited.wrap(app)))
        self.assertTrue(limited.snapshot()['overflow'])
        self.assertEqual(limited.snapshot()['counters']['active_queries'], 0)

    def test_copies_cursor_config_single_install_and_duplicate_registration(self):
        observer, connection = RequestConnectionObserver(UUID(RUN)), Connection()
        with installation(observer, connection):
            snapshot = observer.snapshot()
            snapshot['counters']['active_queries'] = 999
            snapshot['events'][0]['type'] = 'forged'
            self.assertEqual(observer.snapshot()['events'][0]['type'], 'header')
            self.assertEqual(observer.snapshot()['counters']['active_queries'], 0)
            self.assertEqual(observer.delta(observer.cursor()), ())
            with self.assertRaises(AttributeError):
                observer.run_id = 'forged'
            connection.execute_wrappers.append(observer._wrapper)
        self.assertFalse(observer.snapshot()['complete'])
        self.assertEqual(connection.execute_wrappers, [])
        with self.assertRaises(RuntimeError):
            with observer.installed():
                pass
        for options in ({'capacity': 3}, {'max_bytes': 100}, {'max_active': 0},
                        {'capacity': True}, {'max_bytes': True}):
            with self.assertRaises(ValueError):
                RequestConnectionObserver(RUN, **options)
        for run in ('not-a-uuid', RUN.upper(), 431):
            with self.assertRaises(ValueError):
                RequestConnectionObserver(run)
        invalid = RequestConnectionObserver(RUN)
        with self.assertRaises(ValueError):
            invalid.delta(-1)
        self.assertFalse(invalid.snapshot()['valid'])

    def test_import_and_default_unwrapped_app_are_inert(self):
        import builtins
        import importlib
        from TESTS.adapters.django import request_connection_observation as module
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('django', 'psycopg', 'socket')):
                raise AssertionError('adapter import attempted runtime dependency')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', guarded):
            importlib.reload(module)
        async def app(scope, receive, send):
            await response(send)
        self.assertEqual(run(call(app))[0]['headers'], [])


if __name__ == '__main__':
    unittest.main()
