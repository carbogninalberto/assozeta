"""Offline fault controls; this suite starts no service, database or socket."""
import copy
from contextlib import contextmanager
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from TESTS.adapters.go import message_mutation_restore as restore
from TESTS.adapters.go import message_mutation_lifecycle as lifecycle
from TESTS import test_message_mutation_lifecycle as existing
from TESTS.contracts import test_message_mutations as contract


class Control:
    """Orchestration boundary only, never a substitute for a container/process."""
    def __init__(self, probes=(True, False, True), failure=None):
        self.events, self.answers, self.failure = [], iter(probes), failure
        self.pending, self.row, self.locked = False, False, False

    def capture(self):
        self.events.append('capture')
        return ('with-row' if self.row else 'baseline', 'cache')

    def probe(self, expected):
        self.events.append('probe-' + str(expected))
        restore._probe(lambda **kwargs: next(self.answers), expected, 1)

    @contextmanager
    def hold_owner(self):
        self.locked = True
        self.events.append('lock')
        try:
            yield lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        finally:
            self.locked = False
            self.events.append('unlock')

    @contextmanager
    def running_row(self):
        self.row = True
        self.events.append('running-row')
        try:
            yield
        finally:
            self.row = False
            self.events.append('remove-row')

    def open_request(self, case):
        self.pending = True
        self.events.append('request')

    def wait_blocked(self, owner):
        self.events.append('blocked')
        if self.failure == 'missing-proof':
            raise AssertionError('missing-proof')
        return lifecycle.WaitProof(20, 'transaction', 'backend')

    def confirm_busy(self, owner, proof):
        self.events.append('confirm-busy')
        if self.failure == 'lost-proof':
            raise AssertionError('lost-proof')
        self.probe(False)

    def unchanged(self, before):
        self.events.append('unchanged-' + before[0])
        if self.failure == 'side-effect':
            raise AssertionError('side-effect')

    def wait_finished(self, proof):
        self.events.append('finished')
        if self.failure == 'unfinished':
            raise AssertionError('unfinished')

    def response(self, case):
        self.events.append('response')
        self.pending = False
        if self.failure == 'response':
            raise AssertionError('response')

    def verify(self, case, before, started, finished):
        self.events.append('effects')
        if self.failure == 'effects':
            raise AssertionError('effects')
        return dict(models=128, exact_effects=True, full_redis=True)

    def assert_alive(self): self.events.append('guard')

    def settle(self, case):
        self.events.append('settle')
        assert not self.locked, 'cleanup must release blocker before draining HTTP'
        if self.pending:
            self.response(case)

    def close_request(self): self.events.append('close')


class OrderingControls(unittest.TestCase):
    def test_lease_acquired_idle_busy_during_actual_wait_then_after_exact_completion(self):
        control = Control()
        result = restore._run_lease(control, dict(name='create-lease'))
        self.assertTrue(result['success'])
        events = control.events
        for left, right in (('probe-True', 'request'), ('blocked', 'confirm-busy'),
                            ('confirm-busy', 'unlock'), ('unlock', 'finished'),
                            ('finished', 'response'), ('response', 'effects')):
            self.assertLess(events.index(left), events.index(right))
        self.assertGreater(events.index('probe-True', 1), events.index('effects'))
        self.assertEqual(events[-1], 'close')

    def test_released_lease_before_blocked_mutation_is_detected(self):
        control = Control(probes=(True, True))
        with self.assertRaisesRegex(AssertionError, 'exclusive-probe'):
            restore._run_lease(control, dict(name='create-lease'))
        self.assertFalse(control.locked)
        self.assertFalse(control.pending)
        self.assertNotIn('effects', control.events)

    def test_always_false_probe_fails_before_request(self):
        control = Control(probes=(False,))
        with self.assertRaisesRegex(AssertionError, 'exclusive-probe'):
            restore._run_lease(control, dict(name='delete-lease'))
        self.assertNotIn('request', control.events)

    def test_never_released_lease_fails_after_effects(self):
        control = Control(probes=(True, False, False))
        with self.assertRaisesRegex(AssertionError, 'exclusive-probe'):
            restore._run_lease(control, dict(name='delete-lease'))
        self.assertIn('effects', control.events)

    def test_missing_proof_wrong_effect_or_response_cannot_succeed(self):
        for failure in ('missing-proof', 'lost-proof', 'unfinished', 'response', 'effects', 'side-effect'):
            control = Control(failure=failure)
            with self.subTest(failure=failure), self.assertRaises((AssertionError, BaseExceptionGroup)):
                restore._run_lease(control, dict(name='create-lease'))
            self.assertFalse(control.locked)
            self.assertEqual(control.events[-1], 'close')

    def test_running_row_removed_and_whole_state_rechecked(self):
        control = Control(probes=(True, True))
        self.assertTrue(restore._run_running(control, dict(name='create-running'))['success'])
        self.assertIn('unchanged-with-row', control.events)
        self.assertLess(control.events.index('response'), control.events.index('remove-row'))
        self.assertIn('unchanged-baseline', control.events)
        self.assertFalse(control.row)

    def test_running_row_and_request_cleaned_on_rejection_mismatch_or_interruption(self):
        for failure in ('response', 'side-effect'):
            control = Control(probes=(True, True), failure=failure)
            with self.subTest(failure=failure), self.assertRaises((AssertionError, BaseExceptionGroup)):
                restore._run_running(control, dict(name='delete-running'))
            self.assertFalse(control.row)
            self.assertEqual(control.events[-1], 'close')
        control = Control(probes=(True, True))
        control.response = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises((KeyboardInterrupt, BaseExceptionGroup)):
            restore._run_running(control, dict(name='delete-running'))
        self.assertFalse(control.row)
        self.assertEqual(control.events[-1], 'close')


class ProbeControls(unittest.TestCase):
    def test_bool_only_and_errors_propagate(self):
        for value in (None, 0, 1, 'busy', {}, []):
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, 'probe-result'):
                restore._probe(lambda **kwargs: value, False, 1)
        for error in (OSError('container missing'), TimeoutError('exec timed out'), KeyboardInterrupt()):
            def fail(**kwargs): raise error
            with self.subTest(error=error), self.assertRaises(type(error)):
                restore._probe(fail, False, 1)

    def test_late_probe_success_is_rejected(self):
        for result in (True, False):
            with self.subTest(result=result), patch.object(restore.time, 'monotonic', side_effect=[0, 1]), \
                 self.assertRaisesRegex(AssertionError, 'probe-deadline'):
                restore._probe(lambda **kwargs: result, result, 1)

    def test_busy_probe_is_bracketed_by_same_transaction_and_exact_tuple(self):
        owner = lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        row = dict(pid=20, blockers=[10], state='active', wait='Lock', xact='xact',
                   backend='backend', query=lifecycle.OWNER_QUERY, tuples=[(55, 3, 7)])
        proof = lifecycle._blocked([row], owner)
        for mutation in ({'xact': 'new'}, {'backend': 'new'}, {'blockers': []},
                         {'tuples': [(55, 3, 8)]}, {'wait': None}):
            control = object.__new__(restore._LiveControl)
            control.bounds = restore.Bounds()
            control.assert_alive = Mock()
            control.check_request_deadline = Mock()
            control.activity = Mock(side_effect=[[row], [{**row, **mutation}]])
            control.probe = Mock()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(AssertionError, 'transaction-lost'):
                control.confirm_busy(owner, proof)
            control.probe.assert_called_once_with(False)

    def test_missing_pre_probe_proof_prevents_exclusive_probe(self):
        control = object.__new__(restore._LiveControl)
        control.bounds = restore.Bounds()
        control.assert_alive = Mock()
        control.check_request_deadline = Mock()
        control.activity = Mock(return_value=[])
        control.probe = Mock()
        with self.assertRaisesRegex(AssertionError, 'transaction-lost'):
            control.confirm_busy(lifecycle.OwnerRow(10, 'owner', 55, 3, 7), lifecycle.WaitProof(20, 'x', 'b'))
        control.probe.assert_not_called()


def fixture():
    source = contract.load_source()
    runtime = copy.deepcopy(source)
    sessions = {value[9:]: '00000000-0000-4000-8000-' + str(i).zfill(12)
        for i, value in enumerate(sorted({value for case in source['cases']
            for value in case['headers'].values() if value.startswith('$session:')}), 1)}
    runtime['source_sha256'] = contract.source_digest(source)
    for case in runtime['cases']:
        case['token'] = None if case['actor'] == 'anonymous' else 'offline-' + case['actor']
        del case['actor']
        for field in ('headers', 'audit_additional'):
            case[field] = contract.substitute(case[field], sessions)
    return dict(authored=source, runtime=runtime)


class ContractControls(unittest.TestCase):
    def test_case_selection_keeps_portable_expectations_and_authenticated_requests(self):
        selected = fixture()
        original = copy.deepcopy(selected)
        for name in restore.CASE_NAMES:
            case = restore._case(selected, name, contract.validate_runtime)
            self.assertEqual(case['name'], 'message-mutations-restore-' + name)
            self.assertTrue(case['token'])
            self.assertEqual(case['effect']['kind'], 'none' if name.endswith('running') else name.split('-')[0])
            if name.endswith('running'):
                self.assertEqual(case['status'], 503)
                self.assertEqual(case['response'], {'error': 'Ripristino dei dati in corso. Le modifiche sono temporaneamente sospese.'})
        self.assertEqual(selected, original)

    def test_joint_fixture_expectation_rewrite_is_rejected(self):
        selected = fixture()
        for domain in ('runtime', 'authored'):
            next(c for c in selected[domain]['cases'] if c['name'] == 'message-mutations-restore-pending-owner-create')['response'] = {'error': 'self-defined'}
        selected['runtime']['source_sha256'] = contract.source_digest(selected['authored'])
        with self.assertRaisesRegex(AssertionError, 'runtime.digest'):
            restore._case(selected, 'create-running', contract.validate_runtime)

    def test_exact_portable_effects_reject_no_mutation_partial_audit_index_and_foreign_writes(self):
        fixtures = existing.ExactEffectControls()
        fixtures.contract = contract
        for case, before, after in (fixtures.states(), fixtures.delete_states()):
            portable = lifecycle.PortableAssertions(None, contract.assert_effect, None,
                existing.shared.assert_state_unchanged, existing.shared.assert_cache_unchanged, None, None)
            cached, contrasts = fixtures.cache_state()
            def verify(target, current=cached):
                return restore._verify_effect(portable, case, (before, cached), (target, current), contrasts,
                    started=1793008800, finished=1793008801, client_ip='192.0.2.7')
            self.assertTrue(verify(after)['exact_effects'])
            for change in (lambda state: state.update(copy.deepcopy(before)),
                           lambda state: state[contract.AUDIT].pop(),
                           lambda state: state[contract.INDEX].pop(),
                           lambda state: state['foreign.Model0'][0].update(value='unexpected'),
                           lambda state: state[contract.AUDIT][-1].update(remote_addr='127.0.0.1')):
                changed = copy.deepcopy(after)
                change(changed)
                with self.assertRaises(AssertionError): verify(changed)
            changed_cache = copy.deepcopy(cached)
            changed_cache.entries[b'extra'] = existing.cache.HistoricalReadCacheEntry(b'string', b'write', -1)
            with self.assertRaises(AssertionError): verify(after, changed_cache)

    def test_running_denial_cannot_hide_any_model_or_cache_write(self):
        fixtures = existing.ExactEffectControls()
        fixtures.contract = contract
        case, state, _ = fixtures.states()
        case['effect'] = dict(kind='none')
        cached, contrasts = fixtures.cache_state()
        portable = lifecycle.PortableAssertions(None, contract.assert_effect, None,
            existing.shared.assert_state_unchanged, existing.shared.assert_cache_unchanged, None, None)
        def verify(after, current=cached):
            return restore._verify_effect(portable, case, (state, cached), (after, current), contrasts,
                started=1793008800, finished=1793008801, client_ip='192.0.2.7')
        self.assertTrue(verify(copy.deepcopy(state))['exact_effects'])
        for label in state:
            changed = copy.deepcopy(state)
            changed[label].append(dict(unexpected='write'))
            with self.subTest(model=label), self.assertRaises(AssertionError): verify(changed)
        changed = copy.deepcopy(cached)
        del changed.entries[b'foreign']
        with self.assertRaises(AssertionError): verify(state, changed)


class ParentInterfaceControls(unittest.TestCase):
    def inputs(self, name):
        database = SimpleNamespace(connection=object(), vendor='postgresql', in_atomic_block=False,
            get_autocommit=lambda: True, settings_dict=dict(NAME='assozeta_contract_offline', USER='offline', HOST='offline'))
        guard = dict(pid=501, running=True, instance='owned-packaged-go')
        selected = fixture()
        selected.update(baseline_state='state', baseline_cache='cache')
        portable = restore.PortableAssertions(contract.validate_runtime, Mock(), Mock(), Mock(), Mock(), Mock(), Mock())
        return dict(case=name, apps=SimpleNamespace(ready=True), database=database,
            raw=SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs=dict(
                db=1, socket_timeout=1, socket_connect_timeout=1))), fixture=selected,
            base_url='http://127.0.0.1:8100', process_guard=lambda: guard.copy(), client_ip='192.0.2.7',
            portable=portable, exclusive_probe=Mock(return_value=True))

    def test_explicit_packaged_process_guard_without_popen_runs_one_case(self):
        for name in restore.CASE_NAMES:
            inputs = self.inputs(name)
            control = Mock(guard=inputs['process_guard']())
            with self.subTest(case=name), \
                 patch.dict(sys.modules, {'django.db': SimpleNamespace(connections={'default': inputs['database']})}), \
                 patch.object(restore, '_LiveControl', return_value=control), \
                 patch.object(restore, '_run_lease', return_value=dict(success=True)) as lease, \
                 patch.object(restore, '_run_running', return_value=dict(success=True)) as running:
                self.assertEqual(restore.run(**inputs)['backend'], 'go')
                selected = lease if name.endswith('lease') else running
                other = running if name.endswith('lease') else lease
                selected.assert_called_once()
                other.assert_not_called()
                control.teardown.assert_called_once()

    def test_consumed_fixture_or_partial_setup_stops_work_and_tears_down(self):
        for phase in ('setup', 'unchanged'):
            inputs = self.inputs('delete-lease')
            control = Mock(guard=inputs['process_guard']())
            getattr(control, phase).side_effect = AssertionError('unfresh or setup failure')
            with self.subTest(phase=phase), \
                 patch.dict(sys.modules, {'django.db': SimpleNamespace(connections={'default': inputs['database']})}), \
                 patch.object(restore, '_LiveControl', return_value=control), \
                 patch.object(restore, '_run_lease') as work, \
                 self.assertRaisesRegex(AssertionError, 'unfresh or setup failure'):
                restore.run(**inputs)
            work.assert_not_called()
            control.teardown.assert_called_once()

    def test_missing_independent_address_or_parent_callable_is_rejected_before_setup(self):
        for change in (dict(client_ip=None), dict(exclusive_probe=None), dict(process_guard=None),
                       dict(base_url='http://service-name:8100'), dict(case='unknown')):
            inputs = self.inputs('create-lease')
            inputs.update(change)
            with self.subTest(change=change), patch.object(restore, '_LiveControl') as live, \
                 self.assertRaises((AssertionError, ValueError)):
                restore.run(**inputs)
            live.assert_not_called()


class Stream:
    def __init__(self, body): self.body = io.BytesIO(body)
    def settimeout(self, timeout): pass
    def recv_into(self, buffer): return self.body.readinto(buffer)


class ResponseControls(unittest.TestCase):
    def wire(self, case, framing=None, body=None, extra=b''):
        document = json.dumps(case['response']).encode() if body is None else body
        code = str(case['status']).encode()
        headers = b'HTTP/1.1 ' + code + b' Response\r\nContent-Type: application/json\r\nContent-Language: en\r\n'
        headers += b'Retry-After: 10\r\n' if case['status'] == 503 else (
            b'Allow: POST, OPTIONS\r\n' if case['path'].endswith('/add') else b'Allow: DELETE, OPTIONS\r\n')
        framing = b'Content-Length: ' + str(len(document)).encode() + b'\r\n' if framing is None else framing
        return Stream(headers + extra + framing + b'\r\n' + document)

    def test_complete_success_and_running_denial_frames(self):
        for name in restore.CASE_NAMES:
            case = restore._case(fixture(), name, contract.validate_runtime)
            restore._response(self.wire(case), case, 1)
            document = json.dumps(case['response']).encode()
            chunked = hex(len(document))[2:].encode() + b'\r\n' + document + b'\r\n0\r\n\r\n'
            restore._response(self.wire(case, b'Transfer-Encoding: chunked\r\n', chunked), case, 1)

    def test_truncated_body_ambiguous_framing_wrong_admission_and_success_headers_fail(self):
        for name in ('delete-lease', 'create-running'):
            case = restore._case(fixture(), name, contract.validate_runtime)
            invalid = (
                self.wire(case, b'Content-Length: 10000\r\n'),
                self.wire(case, b'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n'),
                self.wire(case, body=b'{"error":"invented"}'),
                self.wire(case, extra=b'Set-Cookie: unexpected\r\n'),
                self.wire(case, extra=b'WWW-Authenticate: Bearer\r\n'),
                self.wire(case, extra=b'Allow: BAD\r\n'),
            )
            for stream in invalid:
                with self.subTest(name=name), self.assertRaises((AssertionError, lifecycle.http.client.HTTPException)):
                    restore._response(stream, case, 1)


class ResourceControls(unittest.TestCase):
    def control(self):
        guard = dict(pid=501, running=True, instance='owned-packaged-go')
        return restore._LiveControl(apps=Mock(), database=MagicMock(), raw=Mock(),
            fixture=dict(authored=dict(ids=dict(owner='owner', own_association='association')), contrasts='contrasts'),
            base_url='http://127.0.0.1:8100', process_guard=lambda: guard.copy(),
            client_ip='192.0.2.7', portable=Mock(), exclusive_probe=Mock(return_value=True), bounds=restore.Bounds())

    def test_guard_change_prevents_probe(self):
        control = self.control()
        control.process_guard = lambda: dict(pid=501, running=True, instance='other-container')
        with self.assertRaisesRegex(AssertionError, 'process-guard-changed'):
            control.probe(True)
        control.exclusive_probe.assert_not_called()

    def test_failed_blocker_rollback_closes_connection_before_cleanup(self):
        control = self.control()
        control.blocker = MagicMock()
        cursor = control.blocker.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = [10]
        cursor.fetchall.return_value = [('owner', 55, '(3,7)')]
        control.blocker.rollback.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            with control.hold_owner(): pass
        control.blocker.close.assert_called_once()

    def test_running_row_insert_interruption_removes_only_helper_receipt(self):
        control = self.control()
        model = Mock(ACTIVE=('queued', 'running'))
        model.return_value = SimpleNamespace(pk='receipt')
        query = model.objects.filter.return_value
        query.exists.return_value = False
        model.objects.bulk_create.side_effect = KeyboardInterrupt
        control.apps.get_model.return_value = model
        with self.assertRaises(KeyboardInterrupt):
            with control.running_row(): self.fail('interrupted INSERT must not yield')
        query.delete.assert_called_once()
        self.assertEqual(model.objects.filter.call_args_list[-1].kwargs, dict(pk='receipt'))

    def test_running_row_removed_after_body_failure_and_existing_active_row_untouched(self):
        control = self.control()
        model = Mock(ACTIVE=('queued', 'running'))
        model.return_value = SimpleNamespace(pk='receipt')
        query = model.objects.filter.return_value
        query.exists.return_value = False
        query.count.return_value = 1
        control.apps.get_model.return_value = model
        with self.assertRaises(KeyboardInterrupt):
            with control.running_row(): raise KeyboardInterrupt
        query.delete.assert_called_once()
        model.reset_mock()
        query.exists.return_value = True
        with self.assertRaisesRegex(AssertionError, 'existing-active-restore'):
            with control.running_row(): pass
        model.objects.bulk_create.assert_not_called()
        query.delete.assert_not_called()

    def test_teardown_attempts_every_cleanup_even_after_failure(self):
        control = self.control()
        control.blocker, control.observer, control.request = Mock(), Mock(), Mock()
        control.blocker.rollback.side_effect = RuntimeError('rollback failed')
        control.observer.close.side_effect = RuntimeError('close failed')
        control.original_timeouts = '17s', '4s'
        with self.assertRaises(BaseExceptionGroup): control.teardown()
        control.blocker.close.assert_called_once()
        control.observer.close.assert_called_once()
        self.assertIsNone(control.request)
        cursor = control.database.cursor.return_value.__enter__.return_value
        self.assertEqual(cursor.execute.call_args.args[1], ['17s', '4s'])

    def test_complete_but_wrong_response_is_not_read_twice_during_cleanup(self):
        control = self.control()
        control.request = Mock()
        control.request_deadline = restore.time.monotonic() + control.bounds.request
        def incorrect(request, case, timeout, *, on_complete):
            on_complete()
            raise AssertionError('wrong response')
        with patch.object(restore, '_response', side_effect=incorrect) as response:
            with self.assertRaisesRegex(AssertionError, 'wrong response'): control.response({})
            control.settle({})
        response.assert_called_once()

    def test_cleanup_drains_after_interruption_and_observes_transaction_end(self):
        control = self.control()
        control.request, control.proof = Mock(), lifecycle.WaitProof(20, 'x', 'b')
        control.request_deadline = -1
        control.response, control.wait_finished = Mock(), Mock()
        control.settle({})
        control.response.assert_called_once_with({})
        control.wait_finished.assert_called_once_with(control.proof)
        self.assertEqual(control.request_deadline, -1)

    def test_busy_window_requires_repeated_proof_and_contention(self):
        control, now = self.control(), [0.0]
        control.activity = Mock(return_value=[])
        control.probe = Mock()
        proof, owner = lifecycle.WaitProof(20, 'x', 'b'), lifecycle.OwnerRow(10, 'owner', 55, 3, 7)
        def observed(*args): now[0] += .1
        with patch.object(restore, '_same_wait', side_effect=observed) as same, \
             patch.object(restore.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(restore.time, 'sleep'):
            control.confirm_busy(owner, proof)
        self.assertEqual(control.probe.call_count, 2)
        self.assertEqual(same.call_count, 4)

    def test_late_blocking_snapshot_and_response_success_rejected(self):
        for phase in ('wait', 'finish', 'snapshot', 'response'):
            control, now = self.control(), [0.0]
            control.database.execute_wrapper.return_value = io.StringIO()
            control.portable.snapshot_state.return_value = {str(i): [] for i in range(128)}
            control.portable.snapshot_cache.side_effect = lambda raw: observed('cache')
            control.request, control.request_deadline = Mock(), control.bounds.request
            def observed(value):
                now[0] = control.bounds.request
                return value
            control.activity = lambda: observed([])
            operation = dict(wait=lambda: control.wait_blocked(None),
                finish=lambda: control.wait_finished(lifecycle.WaitProof(20, 'x', 'b')),
                snapshot=control.capture, response=lambda: control.response({}))[phase]
            with self.subTest(phase=phase), \
                 patch.object(restore.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(restore, '_blocked', return_value=None), \
                 patch.object(restore, '_response', side_effect=lambda *a, **k: observed(None)), \
                 self.assertRaisesRegex(AssertionError, 'deadline'):
                operation()


if __name__ == '__main__':
    unittest.main()
