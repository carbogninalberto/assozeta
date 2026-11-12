"""Offline controls only. No service, socket or database is started by this suite."""
import copy
from contextlib import contextmanager
import errno
import io
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.go import message_mutation_lifecycle as lifecycle
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import historical_read_cache as cache


def portable_contract():
    # The isolated worker tree intentionally lacks the parent's new portable files.
    # An explicit read-only dependency path permits testing their actual assertions.
    path = Path(os.environ.get('MESSAGE_MUTATIONS_PORTABLE_CONTRACT',
        Path(__file__).parent / 'contracts/test_message_mutations.py'))
    if not path.is_file():
        raise RuntimeError('Explicit MESSAGE_MUTATIONS_PORTABLE_CONTRACT required in isolated tree')
    spec = importlib.util.spec_from_file_location('lifecycle_portable_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WaitControls(unittest.TestCase):
    def setUp(self):
        self.owner = lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        self.row = dict(pid=20, blockers=[10], state='active', wait='Lock',
            xact='transaction-one', backend='backend-one',
            query='SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE',
            tuples=[(55, 3, 7)])

    def test_exact_owner_wait(self):
        proof = lifecycle._blocked([self.row], self.owner)
        self.assertEqual((proof.pid, proof.xact), (20, 'transaction-one'))

    def test_missing_or_wrong_wait_fails_closed(self):
        for change in ({'wait': None}, {'state': 'idle in transaction'}, {'blockers': [11]},
                       {'xact': None}, {'tuples': [(55, 3, 8)]},
                       {'query': 'SELECT * FROM communications_message FOR UPDATE'}):
            with self.subTest(change=change):
                self.assertIsNone(lifecycle._blocked([{**self.row, **change}], self.owner))
        self.assertIsNone(lifecycle._blocked([], self.owner))
        with self.assertRaisesRegex(AssertionError, 'ambiguous'):
            lifecycle._blocked([self.row, {**self.row, 'pid': 21}], self.owner)

    def test_same_pid_new_transaction_or_backend_is_not_survival(self):
        proof = lifecycle._blocked([self.row], self.owner)
        for change in ({'xact': 'transaction-two'}, {'backend': 'backend-two'}, {'wait': None}):
            with self.subTest(change=change), self.assertRaisesRegex(AssertionError, 'transaction-lost'):
                lifecycle._same_wait([{**self.row, **change}], self.owner, proof)


class FakeControl:
    """Unit boundary only; never accepted by public run()."""
    def __init__(self, *, missing_wait=False, early_exit=False, lost_wait=False):
        self.events = []
        self.missing_wait, self.early_exit, self.lost_wait = missing_wait, early_exit, lost_wait
        self.released = False

    def capture(self):
        self.events.append('capture')
        return 'state', 'cache'

    @contextmanager
    def hold_owner(self):
        self.events.append('lock')
        try:
            yield lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        finally:
            self.events.append('release')
            self.released = True

    def open_request(self, case): self.events.append('request')
    def assert_alive(self):
        self.events.append('alive')
        if self.early_exit and 'signal' in self.events:
            raise AssertionError('process-exited')
    def wait_blocked(self, owner):
        self.events.append('wait')
        if self.missing_wait:
            raise AssertionError('lock-wait-timeout')
        return lifecycle.WaitProof(20, 'transaction-one', 'backend-one')
    def unchanged(self, before): self.events.append('unchanged')
    def disconnect(self): self.events.append('disconnect')
    def signal(self): self.events.append('signal')
    def listener_stopped(self): self.events.append('listener-stopped')
    def confirm_wait(self, owner, proof):
        self.events.append('confirm')
        self.assert_alive()
        if self.lost_wait: raise AssertionError('transaction-lost')
    def wait_finished(self, proof): self.events.append('finished')
    def response(self, case): self.events.append('response')
    def verify(self, case, before, started, finished):
        self.events.append('exact-effects')
        return {'models': 128}
    def clean_exit(self): self.events.append('clean-exit')
    def close_request(self): self.events.append('close')


class SequenceControls(unittest.TestCase):
    def test_disconnect_requires_wait_before_close_and_commit_after_release(self):
        control = FakeControl()
        lifecycle._run_case(control, {'name': 'create'}, 'disconnect')
        events = control.events
        for first, second in [('wait', 'disconnect'), ('disconnect', 'confirm'),
                              ('confirm', 'release'), ('release', 'finished'),
                              ('finished', 'exact-effects'), ('exact-effects', 'close')]:
            self.assertLess(events.index(first), events.index(second))
        self.assertNotIn('signal', events)

    def test_missing_wait_never_disconnects_or_signals_and_releases_lock(self):
        for mode in ('disconnect', 'drain'):
            control = FakeControl(missing_wait=True)
            with self.subTest(mode=mode), self.assertRaisesRegex(AssertionError, 'lock-wait-timeout'):
                lifecycle._run_case(control, {'name': 'create'}, mode)
            self.assertTrue(control.released)
            self.assertNotIn('disconnect', control.events)
            self.assertNotIn('signal', control.events)
            self.assertNotIn('exact-effects', control.events)
            self.assertEqual(control.events[-1], 'close')

    def test_drain_waits_for_listener_and_transaction_then_commits_and_exits(self):
        control = FakeControl()
        lifecycle._run_case(control, {'name': 'create'}, 'drain')
        events = control.events
        for first, second in [('wait', 'signal'), ('signal', 'listener-stopped'),
                              ('listener-stopped', 'confirm'), ('confirm', 'release'),
                              ('release', 'response'), ('response', 'clean-exit'),
                              ('clean-exit', 'exact-effects')]:
            self.assertLess(events.index(first), events.index(second))

    def test_early_exit_or_lost_transaction_cannot_pass(self):
        for control in (FakeControl(early_exit=True), FakeControl(lost_wait=True)):
            with self.subTest(control=control), self.assertRaises(AssertionError):
                lifecycle._run_case(control, {'name': 'create'}, 'drain')
            self.assertTrue(control.released)
            self.assertNotIn('exact-effects', control.events)
            self.assertNotIn('clean-exit', control.events)
            self.assertEqual(control.events[-1], 'close')


class ResourceControls(unittest.TestCase):
    def control(self):
        process = Mock(pid=501)
        process.poll.return_value = None
        guard = dict(pid=501, running=True, instance='owned-native-instance')
        return lifecycle._LiveControl(apps=None, database=Mock(), raw=None, fixture=None,
            base_url='http://127.0.0.1:8100', process=process, process_guard=lambda: guard.copy(),
            client_ip='192.0.2.7', portable=None, bounds=lifecycle.Bounds())

    def test_signal_uses_only_supplied_handle_after_exact_guard(self):
        control = self.control()
        control.signal()
        control.process.send_signal.assert_called_once_with(signal.SIGTERM)
        control.process.kill.assert_not_called()
        control.process.terminate.assert_not_called()
        with self.assertRaisesRegex(AssertionError, 'signal-pid-mismatch'):
            control.signal()
        control = self.control()
        control.process.pid = 502
        with self.assertRaisesRegex(AssertionError, 'signal-pid-mismatch'):
            control.signal()
        control.process.send_signal.assert_not_called()

    def test_lost_guard_prevents_signal(self):
        control = self.control()
        control.process_guard = lambda: dict(pid=501, running=True, instance='replaced')
        with self.assertRaisesRegex(AssertionError, 'guard-changed'):
            control.signal()
        control.process.send_signal.assert_not_called()

    def test_teardown_reaps_signaled_process_and_attempts_all_connections(self):
        control = self.control()
        control.blocker, control.observer = Mock(), Mock()
        control.blocker.rollback.side_effect = RuntimeError('rollback failure')
        control.original_statement_timeout = '0'
        control.database.cursor.return_value.__enter__ = Mock(return_value=Mock())
        control.database.cursor.return_value.__exit__ = Mock(return_value=False)
        control.signaled = True
        control.process.wait.side_effect = [subprocess.TimeoutExpired('owned', 1), 0]
        control.process.poll.return_value = 0
        with self.assertRaises(BaseExceptionGroup):
            control.teardown()
        control.blocker.close.assert_called_once()
        control.observer.close.assert_called_once()
        control.process.kill.assert_called_once()
        self.assertEqual(control.process.wait.call_count, 2)
        control.database.cursor.assert_called_once()

    def test_un_signaled_process_remains_parent_owned_on_failure(self):
        control = self.control()
        control.teardown()
        control.process.wait.assert_not_called()
        control.process.kill.assert_not_called()
        control.process.send_signal.assert_not_called()

    def test_drain_requires_connection_refusal_not_network_failure(self):
        control = self.control()
        with patch.object(lifecycle.socket, 'create_connection', side_effect=TimeoutError('network timeout')):
            with self.assertRaises(TimeoutError):
                control.listener_stopped()
        with patch.object(lifecycle.socket, 'create_connection', side_effect=ConnectionRefusedError(errno.ECONNREFUSED, 'refused')):
            control.listener_stopped()
        control.process.poll.return_value = 0
        with self.assertRaisesRegex(AssertionError, 'process-exited'):
            control.listener_stopped()

    def test_clean_exit_requires_zero_and_bounded_wait(self):
        for result in (1, -signal.SIGTERM, subprocess.TimeoutExpired('owned', 1)):
            control = self.control()
            if isinstance(result, Exception):
                control.process.wait.side_effect = result
            else:
                control.process.wait.return_value = result
            with self.subTest(result=result), self.assertRaises(AssertionError):
                control.clean_exit()
            control.process.wait.assert_called_once_with(timeout=control.bounds.exit)

    def test_invalid_bounds_fail_before_runtime(self):
        for bounds in (lifecycle.Bounds(lock_wait=0), lifecycle.Bounds(io=float('inf')),
                       lifecycle.Bounds(survival=.001), lifecycle.Bounds(listener_stop=30)):
            with self.subTest(bounds=bounds), self.assertRaises(AssertionError):
                bounds.validate()

    def test_raw_drain_response_requires_exact_success_and_headers(self):
        class Stream:
            def __init__(self, payload): self.data = io.BytesIO(payload)
            def recv_into(self, buffer): return self.data.readinto(buffer)
            def settimeout(self, timeout):
                if timeout <= 0: raise AssertionError('unbounded response timeout')

        control = self.control()
        document = b'{"msg":"message added."}'
        headers = (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Language: en\r\n'
            b'Allow: POST, OPTIONS\r\nContent-Length: ' + str(len(document)).encode() + b'\r\n\r\n')
        case = dict(status=200, response={'msg': 'message added.'})
        control.request = Stream(headers + document)
        control.response(case)
        for payload in ((headers + document).replace(b'200 OK', b'500 Error'),
                        (headers + document).replace(b'POST, OPTIONS', b'GET'),
                        (headers + document).replace(b'message added.', b'message wrong.')):
            control.request = Stream(payload)
            with self.subTest(payload=payload), self.assertRaises(AssertionError):
                control.response(case)
        stream = lifecycle._ResponseSocket(Stream(headers + document), 1)
        with patch.object(lifecycle.time, 'monotonic', return_value=stream.deadline + .1):
            with self.assertRaisesRegex(AssertionError, 'response-read-deadline'):
                stream.makefile('rb').read(1)

    def test_complete_bounded_response_framing(self):
        class Stream:
            def __init__(self, payload): self.data = io.BytesIO(payload)
            def recv_into(self, buffer): return self.data.readinto(buffer)
            def settimeout(self, timeout): pass

        control = self.control()
        document = b'{"msg":"message added."}'
        headers = (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                   b'Content-Language: en\r\nAllow: POST, OPTIONS\r\n')
        case = dict(status=200, response={'msg': 'message added.'})
        chunk = hex(len(document))[2:].encode() + b'\r\n' + document + b'\r\n'
        for framing, body in ((b'Content-Length: 24\r\n', document),
                              (b'Transfer-Encoding: chunked\r\n', chunk + b'0\r\n\r\n'),
                              (b'Transfer-Encoding: chunked\r\n', chunk + b'0\r\nX-Trailer: valid\r\n\r\n'),
                              (b'', document)):
            with self.subTest(valid=framing):
                control.request = Stream(headers + framing + b'\r\n' + body)
                control.response(case)
        for label, framing, body in (
            ('short-length', b'Content-Length: 1000\r\n', document),
            ('oversized-length', b'Content-Length: 65537\r\n', document),
            ('missing-last-chunk', b'Transfer-Encoding: chunked\r\n', chunk),
            ('truncated-chunk', b'Transfer-Encoding: chunked\r\n', b'100\r\n' + document),
            ('missing-trailer-terminator', b'Transfer-Encoding: chunked\r\n', chunk + b'0\r\n'),
            ('invalid-chunk-delimiter', b'Transfer-Encoding: chunked\r\n', chunk[:-2] + b'xx0\r\n\r\n'),
            ('negative-chunk-size', b'Transfer-Encoding: chunked\r\n', b'-1\r\n' + document + b'\r\n0\r\n\r\n'),
            ('oversized-chunk', b'Transfer-Encoding: chunked\r\n', b'10001\r\n' + document),
            ('unbounded-framing', b'Transfer-Encoding: chunked\r\n', chunk + b'0\r\n' + b'X-Trailer: x\r\n' * 6000 + b'\r\n'),
            ('conflicting-lengths', b'Content-Length: 24\r\nContent-Length: 1000\r\n', document),
            ('ambiguous-framing', b'Content-Length: 24\r\nTransfer-Encoding: chunked\r\n', chunk + b'0\r\n\r\n'),
            ('invalid-length', b'Content-Length: invalid\r\n', document),
            ('unsupported-encoding', b'Transfer-Encoding: gzip\r\n', document),
            ('oversized-body', b'', b' ' * 65537 + document),
        ):
            with self.subTest(invalid=label):
                control.request = Stream(headers + framing + b'\r\n' + body)
                with self.assertRaises((AssertionError, lifecycle.http.client.HTTPException)):
                    control.response(case)

    def test_deadline_checked_after_blocking_success_observations(self):
        owner = lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        proof = lifecycle.WaitProof(20, 'transaction-one', 'backend-one')
        row = dict(pid=20, blockers=[10], state='active', wait='Lock',
                   xact=proof.xact, backend=proof.backend, query=lifecycle.OWNER_QUERY,
                   tuples=[(55, 3, 7)])
        for operation, code, duration in (
            ('lock', 'lock-wait-timeout', lifecycle.Bounds().lock_wait),
            ('completion', 'transaction-did-not-finish', lifecycle.Bounds().completion),
            ('listener', 'listener-did-not-stop', lifecycle.Bounds().listener_stop),
            ('survival', 'survival-observation-timeout', lifecycle.Bounds().survival + lifecycle.Bounds().query),
            ('exit', 'process-did-not-exit-cleanly', lifecycle.Bounds().exit),
        ):
            for lateness in (None, 0, .1):
                with self.subTest(operation=operation, lateness=lateness):
                    control, now = self.control(), [0.0]
                    observations = [0]

                    def observe():
                        observations[0] += 1
                        if operation != 'survival' or observations[0] >= 2:
                            now[0] = duration + lateness if lateness is not None else (
                                control.bounds.survival if operation == 'survival' else duration / 2)
                        return [] if operation == 'completion' else [row]

                    def refused(*args, **kwargs):
                        observe()
                        raise ConnectionRefusedError(errno.ECONNREFUSED, 'refused')

                    def exited(**kwargs):
                        observe()
                        return 0

                    control.activity = observe
                    control.process.wait.side_effect = exited
                    calls = dict(lock=lambda: control.wait_blocked(owner),
                        completion=lambda: control.wait_finished(proof),
                        listener=control.listener_stopped,
                        survival=lambda: control.confirm_wait(owner, proof), exit=control.clean_exit)
                    with patch.object(lifecycle.time, 'monotonic', side_effect=lambda: now[0]), \
                         patch.object(lifecycle.time, 'sleep'), \
                         patch.object(lifecycle.socket, 'create_connection', side_effect=refused):
                        if lateness is not None:
                            with self.assertRaisesRegex(AssertionError, code): calls[operation]()
                        else:
                            calls[operation]()

    def test_snapshot_and_response_reads_reject_late_success(self):
        for phase in ('models', 'cache', 'response'):
            for lateness in (None, 0, .1):
                with self.subTest(phase=phase, lateness=lateness):
                    control, now = self.control(), [0.0]

                    def observed(value):
                        limit = control.bounds.io if phase == 'response' else control.bounds.completion
                        now[0] = limit + lateness if lateness is not None else limit / 2
                        return value

                    models = {str(i): [] for i in range(128)}
                    control.portable = SimpleNamespace(
                        snapshot_state=lambda apps: observed(models) if phase == 'models' else models,
                        snapshot_cache=lambda raw: observed('cache'))
                    control.database.execute_wrapper.return_value = io.StringIO()

                    class Stream:
                        def settimeout(self, timeout): pass
                        def recv_into(self, buffer):
                            buffer[0] = 120
                            return observed(1)

                    with patch.object(lifecycle.time, 'monotonic', side_effect=lambda: now[0]):
                        if phase == 'response':
                            operation = lambda: lifecycle._ResponseSocket(Stream(), control.bounds.io).makefile('rb').read(1)
                        else:
                            operation = control.capture
                        if lateness is not None:
                            with self.assertRaisesRegex(AssertionError, 'deadline'): operation()
                        else:
                            operation()


class FixtureControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.contract = portable_contract()

    def fixture(self):
        source = self.contract.load_source()
        runtime = copy.deepcopy(source)
        sessions = {value[9:]: '00000000-0000-4000-8000-' + str(i).zfill(12)
            for i, value in enumerate(sorted({v for case in source['cases']
                for v in case['headers'].values() if v.startswith('$session:')}), 1)}
        runtime['source_sha256'] = self.contract.source_digest(source)
        for case in runtime['cases']:
            actor = case.pop('actor')
            case['token'] = None if actor == 'anonymous' else 'offline-token-' + actor
            for field in ('headers', 'audit_additional'):
                case[field] = self.contract.substitute(case[field], sessions)
        return dict(authored=source, runtime=runtime, baseline_state='state', baseline_cache='cache', contrasts=None)

    def run_fixture(self, fixture):
        database = SimpleNamespace(connection=object(), vendor='postgresql', settings_dict={
            'NAME': 'assozeta_contract_offline', 'USER': 'offline', 'HOST': 'offline'})
        process = Mock(spec=subprocess.Popen, pid=501, returncode=0)
        portable = lifecycle.PortableAssertions(self.contract.validate_runtime, Mock(), Mock(), Mock(), Mock(), Mock(), Mock())
        control = Mock(guard=dict(pid=501, running=True, instance='offline'))
        control.capture.return_value = ('state', 'cache')
        with patch.dict(sys.modules, {'django.db': SimpleNamespace(connections={'default': database})}), \
             patch.object(lifecycle, '_LiveControl', return_value=control), \
             patch.object(lifecycle, '_run_case', return_value=dict(success=True)):
            return lifecycle.run(apps=SimpleNamespace(ready=True), database=database,
                raw=SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs=dict(
                    db=1, socket_timeout=1, socket_connect_timeout=1))), fixture=fixture,
                base_url='http://127.0.0.1:8100', process=process,
                process_guard=lambda: control.guard.copy(), client_ip='192.0.2.7', portable=portable)

    def test_canonical_fixture_and_lifecycle_selection(self):
        fixture = self.fixture()
        self.assertTrue(self.run_fixture(fixture)['success'])
        selected = lifecycle._cases(fixture)
        self.assertEqual([case['name'] for case in selected],
            ['message-mutations-lifecycle-' + suffix for suffix in lifecycle.CASE_ORDER])
        self.assertEqual(selected[1]['effect']['delivery_count'], 2)

    def test_jointly_modified_authored_runtime_and_digest_cannot_self_validate(self):
        fixture = self.fixture()
        for source in (fixture['authored'], fixture['runtime']):
            case = next(case for case in source['cases'] if case['name'] == 'message-mutations-delete-email')
            case['effect']['delivery_count'] = 0
        fixture['runtime']['source_sha256'] = self.contract.source_digest(fixture['authored'])
        # Demonstrate the old, mutually supplied validation accepts this fixture.
        self.contract.validate_runtime(fixture['runtime'], fixture['authored'])
        self.assertEqual(lifecycle._cases(fixture)[1]['effect']['delivery_count'], 0)
        with self.assertRaisesRegex(AssertionError, 'runtime.digest'):
            self.run_fixture(fixture)

    def test_individually_modified_authored_or_runtime_cannot_pass(self):
        for field in ('authored', 'runtime'):
            with self.subTest(field=field):
                fixture = self.fixture()
                fixture[field]['ids']['owner'] = '00000000-0000-4000-8000-000000000999'
                with self.assertRaisesRegex(AssertionError, 'runtime.(digest|authored-projection)'):
                    self.run_fixture(fixture)


class ExactEffectControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.contract = portable_contract()

    def states(self):
        identifier = '00000000-0000-4000-8000-000000000001'
        before = {self.contract.MESSAGE: [], self.contract.DELIVERY: [], self.contract.AUDIT: [],
            self.contract.INDEX: [], 'contenttypes.ContentType': [dict(id=30, app_label='communications', model='message')],
            'application.User': [dict(user_id='actor', email='actor@example.test')]}
        before.update({f'foreign.Model{i}': [dict(id=1, value='preserve')] for i in range(122)})
        after = copy.deepcopy(before)
        after[self.contract.MESSAGE] = [dict(message_id=identifier, type='EMAIL', message='Saved',
            subject='Subject', sport_association_id='own', created_at='2026-10-26T10:00:00Z')]
        changes = {field: ['None', value] for field, value in dict(message_id=identifier, type='EMAIL',
            message='Saved', subject='Subject', sport_association='own', created_at='2026-10-26 10:00:00').items()}
        after[self.contract.AUDIT] = [dict(id=1, content_type_id=30, object_pk=identifier, object_id=None,
            object_repr=f'Message object ({identifier})', serialized_data=None, action=0, changes_text='',
            changes=changes, actor_id='actor', actor_email='actor@example.test', cid='case', remote_addr='192.0.2.7',
            remote_port=None, timestamp='2026-10-26T10:00:00Z', additional_data=None)]
        after[self.contract.INDEX] = [dict(audit_log_index_id='00000000-0000-4000-8000-000000000002',
            log_entry_id=1, sport_association_id='own', resolution_path='direct', created_at='2026-10-26T10:00:00Z')]
        case = dict(effect=dict(kind='create', values=dict(type='EMAIL', message='Saved', subject='Subject',
            sport_association_id='own')), audit_actor='actor', audit_additional=None, name='case')
        return case, before, after

    def cache_state(self):
        contrasts = cache.HistoricalReadCacheContrasts(cache.PERSISTENT_CONTRAST_KEY,
            cache.EXPIRING_CONTRAST_KEY, 2000000000000, b'persistent-dump', b'expiring-dump')
        entries = {b'foreign': cache.HistoricalReadCacheEntry(b'string', b'dump', -1),
            contrasts.persistent_key: cache.HistoricalReadCacheEntry(b'string', b'persistent-dump', -1),
            contrasts.expiring_key: cache.HistoricalReadCacheEntry(b'string', b'expiring-dump', contrasts.expires_at_ms)}
        return cache.HistoricalReadCacheSnapshot(entries), contrasts

    def verify(self, case, before, after, prior=None, current=None):
        portable = lifecycle.PortableAssertions(
            validate_runtime=self.contract.validate_runtime, assert_effect=self.contract.assert_effect,
            snapshot_state=None, assert_state_unchanged=shared.assert_state_unchanged,
            assert_cache_unchanged=shared.assert_cache_unchanged, snapshot_cache=None, require_private_database=None)
        baseline, contrasts = self.cache_state()
        prior = baseline if prior is None else prior
        current = prior if current is None else current
        return lifecycle._verify_effect(portable, case, (before, prior), (after, current), contrasts,
            started=1793008800, finished=1793008801, client_ip='192.0.2.7')

    def test_exact_create_positive_control(self):
        self.verify(*self.states())

    def test_drain_final_effects_include_shutdown_model_and_cache_writes(self):
        for write in ('none', 'model', 'cache'):
            with self.subTest(write=write):
                case, before, after = self.states()
                prior, _ = self.cache_state()
                current = copy.deepcopy(prior)
                verify = self.verify

                class ShutdownControl(FakeControl):
                    def verify(self, case, baseline, started, finished):
                        return verify(case, before, after, prior, current)

                    def clean_exit(self):
                        super().clean_exit()
                        if write == 'model': after['foreign.Model0'][0]['value'] = 'shutdown write'
                        if write == 'cache': current.entries[b'shutdown'] = cache.HistoricalReadCacheEntry(b'string', b'unexpected', -1)

                if write == 'none':
                    self.assertTrue(lifecycle._run_case(ShutdownControl(), case, 'drain')['success'])
                else:
                    with self.assertRaises(AssertionError):
                        lifecycle._run_case(ShutdownControl(), case, 'drain')

    def test_no_effect_partial_audit_foreign_state_and_wrong_client_ip_rejected(self):
        case, before, after = self.states()
        for label, mutation in (
            ('no-effect', lambda target: target.update(copy.deepcopy(before))),
            ('audit-missing', lambda target: target[self.contract.AUDIT].clear()),
            ('index-missing', lambda target: target[self.contract.INDEX].clear()),
            ('partial-audit', lambda target: target[self.contract.AUDIT][0].update(changes={})),
            ('foreign-state', lambda target: target['foreign.Model0'][0].update(value='changed')),
            ('client-ip', lambda target: target[self.contract.AUDIT][0].update(remote_addr='127.0.0.1'))):
            changed = copy.deepcopy(after)
            mutation(changed)
            with self.subTest(label=label), self.assertRaises(AssertionError):
                self.verify(case, before, changed)

    def test_cache_value_type_expiry_added_and_deleted_keys_rejected(self):
        case, before, after = self.states()
        prior, _ = self.cache_state()
        deleted = dict(prior.entries)
        del deleted[b'foreign']
        for entries in (deleted,
            {**prior.entries, b'foreign': cache.HistoricalReadCacheEntry(b'hash', b'dump', -1)},
            {**prior.entries, b'foreign': cache.HistoricalReadCacheEntry(b'string', b'changed', -1)},
            {**prior.entries, b'foreign': cache.HistoricalReadCacheEntry(b'string', b'dump', 2000000000001)},
            {**prior.entries, b'extra': cache.HistoricalReadCacheEntry(b'string', b'extra', -1)}):
            with self.subTest(entries=entries), self.assertRaises(AssertionError):
                self.verify(case, before, after, prior, cache.HistoricalReadCacheSnapshot(entries))

    def test_all_128_models_required_even_when_otherwise_exact(self):
        case, before, after = self.states()
        for state in (before, after): del state['foreign.Model121']
        with self.assertRaisesRegex(AssertionError, '128'):
            self.verify(case, before, after)

    def delete_states(self):
        case, _, created = self.states()
        before = copy.deepcopy(created)
        identifier = before[self.contract.MESSAGE][0]['message_id']
        history = copy.deepcopy(before[self.contract.AUDIT][0])
        history.update(id=2, action=1, cid='historical-update', changes={'message': ['old', 'Saved']})
        before[self.contract.AUDIT].append(history)
        index = copy.deepcopy(before[self.contract.INDEX][0])
        index.update(audit_log_index_id='00000000-0000-4000-8000-000000000003', log_entry_id=2)
        before[self.contract.INDEX].append(index)
        before[self.contract.DELIVERY] = [dict(message_transaction_id='owned', message_id=identifier),
            dict(message_transaction_id='foreign', message_id='foreign-message')]
        after = copy.deepcopy(before)
        after[self.contract.MESSAGE] = []
        after[self.contract.DELIVERY] = [copy.deepcopy(before[self.contract.DELIVERY][1])]
        event = copy.deepcopy(before[self.contract.AUDIT][0])
        event.update(id=3, action=2, changes={key: list(reversed(value))
            for key, value in event['changes'].items()})
        after[self.contract.AUDIT].append(event)
        index = copy.deepcopy(index)
        index.update(audit_log_index_id='00000000-0000-4000-8000-000000000004', log_entry_id=3,
            resolution_path='changes_json.sport_association')
        after[self.contract.INDEX].append(index)
        case['effect'] = dict(kind='delete', message_id=identifier, delivery_count=1)
        return case, before, after

    def test_exact_delete_and_foreign_deliveries_and_history_preserved(self):
        case, before, after = self.delete_states()
        self.verify(case, before, after)
        for label, mutation in (
            ('no-delete', lambda target: target.update(copy.deepcopy(before))),
            ('owned-delivery-retained', lambda target: target[self.contract.DELIVERY].insert(0, before[self.contract.DELIVERY][0])),
            ('foreign-delivery-deleted', lambda target: target[self.contract.DELIVERY].clear()),
            ('history-removed', lambda target: target[self.contract.AUDIT].pop(0)),
            ('partial-index', lambda target: target[self.contract.INDEX][-1].update(resolution_path='direct'))):
            changed = copy.deepcopy(after)
            mutation(changed)
            with self.subTest(label=label), self.assertRaises(AssertionError):
                self.verify(case, before, changed)


if __name__ == '__main__':
    unittest.main()
