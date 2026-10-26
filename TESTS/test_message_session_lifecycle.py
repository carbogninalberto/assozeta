"""Offline controls only: no services, sockets, credentials, or live acceptance."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from TESTS.contracts import test_message_session_lifecycle as contract
from TESTS.adapters.django import message_session_lifecycle as adapter
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import historical_read_cache as cache


SESSION = 'b1000000-0000-4000-8000-000000000001'
KEY = b':1:impersonation:v1:' + SESSION.encode()
OTHER = b'other-session-private'
ENTRY = cache.HistoricalReadCacheEntry(b'string', b'original-private-dump', 4000000000000)


class OfflineRedis:
    def __init__(self):
        self.entries = {
            KEY: ENTRY, OTHER: ENTRY,
            cache.PERSISTENT_CONTRAST_KEY: cache.HistoricalReadCacheEntry(b'string', b'persistent', -1),
            cache.EXPIRING_CONTRAST_KEY: cache.HistoricalReadCacheEntry(b'string', b'expiring', 4000000000000),
        }
        self.commands = []
        self.transition_hook = None
        self.restore_error = False

    def eval(self, script, keys, *args):
        return [[key, [entry.redis_type, entry.serialized_value, entry.expires_at_ms]]
                for key, entry in self.entries.items()]

    def delete(self, key):
        self.commands.append(('DEL', key))
        return self._remove(key)

    def _remove(self, key):
        existed = self.entries.pop(key, None) is not None
        if self.transition_hook:
            self.transition_hook(self)
        return int(existed)

    def pexpireat(self, key, when):
        self.commands.append(('PEXPIREAT', key, when))
        return bool(self._remove(key))

    def restore(self, key, ttl, value, *, replace, absttl=False):
        self.commands.append(('RESTORE', key, ttl, value, replace, absttl))
        if self.restore_error:
            raise RuntimeError('private restore failure')
        self.entries[key] = cache.HistoricalReadCacheEntry(b'string', value, ttl if absttl else -1)


class Observer:
    def __init__(self):
        self.events = []

    def cursor(self):
        return len(self.events)

    def delta(self, cursor):
        return self.events[cursor:]


class LifecycleControls(unittest.TestCase):
    def setUp(self):
        self.raw = OfflineRedis()
        self.source = contract.load_source()
        self.state = {f'model.{index}': [] for index in range(126)}
        self.state.update({'auditlog.LogEntry': [], 'application.AuditLogIndex': []})
        self.baseline = copy.deepcopy(self.state)
        self.observer = Observer()
        self.process = {'pid': 123, 'instance': 456, 'running': True}
        reference = scopes.contract.load_source()
        self.fixture = {
            'tokens': {case['actor']: 'private-token-' + case['actor'] for case in reference['cases']},
            'sessions': {label: 'b1000000-0000-4000-8000-' + f'{index:012d}'
                         for index, label in enumerate(scopes.SESSION_SPECS, 1)},
            'baseline_state': self.baseline,
            'baseline_cache': cache.snapshot_historical_read_cache(self.raw),
            'contrasts': cache.HistoricalReadCacheContrasts(cache.PERSISTENT_CONTRAST_KEY,
                cache.EXPIRING_CONTRAST_KEY, 4000000000000, b'persistent', b'expiring'),
            'message_session_lifecycle_process': lambda: dict(self.process),
        }
        self.fixture['runtime'] = scopes.runtime_manifest(reference, self.fixture['tokens'], self.fixture['sessions'])
        self.calls = []
        self.request_hook = None
        self.backend = 'go'

    def request(self, client, method, path, **kwargs):
        headers = kwargs['headers']
        self.calls.append((method, path, client.token, dict(headers), id(self.observer)))
        phase = next(phase for scenario in self.source['scenarios'] for phase in scenario['phases']
                     if phase['name'] == headers['X-Correlation-ID'])
        self.assertEqual(KEY in self.raw.entries, phase['status'] == 200)
        response = (phase['status'], copy.deepcopy(phase['response']), {
            'Content-Language': 'en', 'Content-Type': 'application/json', 'Allow': 'GET, OPTIONS'})
        if self.request_hook:
            response = self.request_hook(phase, response)
        if self.backend == 'django':
            self.observer.events.append({'seq': len(self.observer.events), 'method': method,
                'path': path, 'cid': phase['name'], 'status': response[0], 'error_type': None})
        return response

    def run_offline(self, backend='go', label=None):
        self.backend = backend
        def request(client, *args, **kwargs):
            return self.request(client, *args, **kwargs)
        with patch('socket.socket', side_effect=AssertionError('socket forbidden')), \
             patch.object(adapter, '_session_cache_key', return_value=KEY), \
             patch.object(scopes, 'snapshot_state', side_effect=lambda apps: copy.deepcopy(self.state)), \
             patch.object(contract.Client, 'request', new=request):
            return adapter.run_workload(label or backend + '-native', backend, 'http://offline.invalid',
                self.fixture, None, self.raw, self.observer)

    def assert_restored(self, report, *, state=True):
        self.assertEqual(cache.snapshot_historical_read_cache(self.raw).entries,
                         self.fixture['baseline_cache'].entries)
        self.assertTrue(report['restoration']['checks']['cache'])
        self.assertEqual(report['restoration']['checks']['state'], state)

    def test_valid_synthetic_sequences_require_all_four_reads_and_restore_exact_expiry(self):
        for backend in ('django', 'go'):
            with self.subTest(backend=backend):
                report = self.run_offline(backend)
                self.assertTrue(adapter.acceptance(report))  # Synthetic shape control, never live evidence.
                self.assertEqual(len(self.calls), 4)
                for first, second in zip(self.calls[::2], self.calls[1::2]):
                    self.assertEqual(first[:3], second[:3])
                    self.assertEqual(first[3]['User-Id'], second[3]['User-Id'])
                    self.assertEqual(first[3]['X-Impersonation-Id'], second[3]['X-Impersonation-Id'])
                self.assert_restored(report)
                restores = [command for command in self.raw.commands if command[0] == 'RESTORE']
                self.assertTrue(any(command[1:] == (KEY, ENTRY.expires_at_ms,
                    ENTRY.serialized_value, True, True) for command in restores))
                self.assertTrue(any(command[0] == 'PEXPIREAT' and command[2] < 4000000000000
                                    for command in self.raw.commands))
                self.assertEqual([command[0] for command in self.raw.commands
                                  if command[0] in ('DEL', 'PEXPIREAT')], ['DEL', 'PEXPIREAT'])
                self.calls.clear()
                self.observer.events.clear()
                self.raw.commands.clear()

    def test_stale_warm_success_after_either_transition_is_rejected(self):
        for transition in ('revoke', 'expire'):
            def stale(phase, response):
                if phase['name'] == f'message-session-{transition}-' + ('list' if transition == 'revoke' else 'detail') + '-denied':
                    warm = self.source['scenarios'][0 if transition == 'revoke' else 1]['phases'][0]
                    return 200, warm['response'], response[2]
                return response
            self.request_hook = stale
            report = self.run_offline()
            self.assertFalse(adapter.acceptance(report))
            self.assertEqual(report['failure'], {'check': 'response.status', 'status': 200})
            self.assert_restored(report)

    def test_http_failure_is_never_a_denial_receipt(self):
        for scenario in contract.SCENARIOS:
            for suffix in ('warm', 'denied'):
                for error in (OSError('private token SQL'), KeyboardInterrupt('private')):
                    def failure(phase, response):
                        if phase['name'] == scenario + '-' + suffix:
                            raise error
                        return response
                    self.request_hook = failure
                    with self.subTest(scenario=scenario, suffix=suffix, error=type(error).__name__):
                        report = self.run_offline()
                        self.assertFalse(report['success'])
                        self.assertEqual(report['failure'], {'check': 'unknown'})
                        self.assert_restored(report)

    def test_wrong_denial_body_headers_and_status_are_rejected(self):
        for field in ('body', 'status', 'language', 'type', 'allow', 'credentials'):
            def corrupt(phase, response):
                if phase['status'] != 403:
                    return response
                status, body, headers = response
                if field == 'body': body = {'detail': 'private-error'}
                if field == 'status': status = 500
                if field == 'language': headers['Content-Language'] = 'it'
                if field == 'type': headers['Content-Type'] = 'text/plain'
                if field == 'allow': headers['Allow'] = 'POST'
                if field == 'credentials': headers['Set-Cookie'] = 'private'
                return status, body, headers
            self.request_hook = corrupt
            with self.subTest(field=field):
                report = self.run_offline()
                self.assertFalse(adapter.acceptance(report))
                self.assertEqual(report['failure']['check'], 'response.' + field)
                self.assert_restored(report)

    def test_warm_failure_prevents_transition_and_still_verifies_restoration(self):
        self.request_hook = lambda phase, response: (500, {}, response[2])
        report = self.run_offline()
        self.assertFalse(report['success'])
        self.assertFalse(any(command[0] in ('DEL', 'PEXPIREAT') for command in self.raw.commands))
        self.assert_restored(report)

    def test_transition_rejects_wrong_session_extra_key_type_dump_and_expiry(self):
        baseline = dict(self.raw.entries)
        for mutation in ('wrong', 'extra', 'type', 'dump', 'expiry'):
            def mutate(raw):
                raw.transition_hook = None
                if mutation == 'wrong':
                    raw.entries[KEY] = ENTRY
                    raw.entries.pop(OTHER)
                elif mutation == 'extra': raw.entries[b'extra-private-key'] = ENTRY
                else:
                    raw.entries[OTHER] = cache.HistoricalReadCacheEntry(
                        b'hash' if mutation == 'type' else b'string',
                        b'changed' if mutation == 'dump' else ENTRY.serialized_value,
                        ENTRY.expires_at_ms + (1 if mutation == 'expiry' else 0))
            self.raw.entries = dict(baseline)
            self.raw.transition_hook = mutate
            with self.subTest(mutation=mutation):
                report = self.run_offline()
                self.assertFalse(adapter.acceptance(report))
                self.assertEqual(report['failure']['check'], 'cache')
                self.assert_restored(report)

    def test_all_models_and_both_audit_tables_are_checked(self):
        for model in ('model.0', 'auditlog.LogEntry', 'application.AuditLogIndex'):
            for during in ('warm', 'transition', 'denied'):
                self.state = copy.deepcopy(self.baseline)
                def change(phase, response):
                    if phase['name'].endswith(during): self.state[model].append({'private': 'row'})
                    return response
                self.request_hook = change
                if during == 'transition':
                    def transition(raw):
                        raw.transition_hook = None
                        self.state[model].append({'private': 'row'})
                    self.raw.transition_hook = transition
                with self.subTest(model=model, during=during):
                    report = self.run_offline()
                    self.assertFalse(adapter.acceptance(report))
                    self.assertEqual(report['failure']['check'], 'state')
                    self.assert_restored(report, state=False)

    def test_read_cache_side_effects_are_rejected_at_both_phases(self):
        for during in ('warm', 'denied'):
            def mutate(phase, response):
                if phase['name'].endswith(during): self.raw.entries[b'read-side-effect'] = ENTRY
                return response
            self.request_hook = mutate
            report = self.run_offline()
            self.assertFalse(adapter.acceptance(report))
            self.assert_restored(report)

    def test_partial_transition_failure_restores_original_session(self):
        for transition_index in (1, 2):
            transitions = []
            def fail_after_delete(raw):
                transitions.append(1)
                if len(transitions) == transition_index:
                    raw.transition_hook = None
                    raise RuntimeError('private partial transition')
            self.raw.transition_hook = fail_after_delete
            with self.subTest(transition_index=transition_index):
                report = self.run_offline()
                self.assertFalse(adapter.acceptance(report))
                self.assert_restored(report)

    def test_cleanup_failure_does_not_skip_state_or_cache_verification(self):
        self.raw.restore_error = True
        report = self.run_offline()
        self.assertFalse(adapter.acceptance(report))
        self.assertFalse(report['restoration']['verified'])
        self.assertFalse(report['restoration']['checks']['cache'])
        self.assertTrue(report['restoration']['checks']['state'])

    def test_snapshot_outage_still_restores_known_session_without_claiming_verification(self):
        for transition_index in (1, 2):
            with self.subTest(transition_index=transition_index):
                self.raw = OfflineRedis()
                transitions = []
                def transition(raw):
                    transitions.append(1)
                self.raw.transition_hook = transition
                observe = self.raw.eval
                def snapshot(*args):
                    if len(transitions) >= transition_index:
                        raise RuntimeError('private whole-cache observation failure')
                    return observe(*args)
                with patch.object(self.raw, 'eval', side_effect=snapshot):
                    report = self.run_offline()
                self.assertEqual(report['failure'], {'check': 'cache'})
                self.assertFalse(adapter.acceptance(report))
                self.assertEqual(self.raw.entries[KEY], ENTRY)
                restores = [command for command in self.raw.commands if command[0] == 'RESTORE']
                self.assertTrue(restores)
                self.assertTrue(all(command[1:] == (KEY, ENTRY.expires_at_ms,
                    ENTRY.serialized_value, True, True) for command in restores))
                for receipt in (report['scenarios'][-1]['restoration'], report['restoration']):
                    self.assertFalse(receipt['verified'])
                    self.assertEqual(receipt['checks'], {'session': False, 'cache': False, 'state': True})
                    self.assertIsNone(receipt['cache_sha256'])
                self.assertEqual([command[0] for command in self.raw.commands
                                  if command[0] in ('DEL', 'PEXPIREAT')],
                                 ['DEL', 'PEXPIREAT'][:transition_index])

    def test_snapshot_recovery_during_restore_does_not_hide_failed_boundary(self):
        unavailable = False
        def transition(raw):
            nonlocal unavailable
            unavailable = True
        self.raw.transition_hook = transition
        observe, restore = self.raw.eval, self.raw.restore
        def snapshot(*args):
            if unavailable:
                raise RuntimeError('private whole-cache observation failure')
            return observe(*args)
        def recover(*args, **kwargs):
            nonlocal unavailable
            restore(*args, **kwargs)
            unavailable = False
        with patch.object(self.raw, 'eval', side_effect=snapshot), \
             patch.object(self.raw, 'restore', side_effect=recover):
            report = self.run_offline()
        self.assertEqual(report['failure'], {'check': 'cache'})
        self.assertFalse(adapter.acceptance(report))
        self.assertTrue(report['scenarios'][0]['restoration']['verified'])
        self.assertTrue(report['restoration']['verified'])
        self.assert_restored(report)

    def test_boundary_attempts_all_observations_after_failure(self):
        events = []
        def state(*args):
            events.append('state')
            raise scopes.StateFailure('private-state')
        with patch.object(adapter, '_state', side_effect=state), \
             patch.object(adapter, '_cache', side_effect=lambda *args: events.append('cache')), \
             patch.object(adapter, '_same_process', side_effect=lambda *args: events.append('process')):
            with self.assertRaises(scopes.StateFailure):
                adapter._boundary(None, self.raw, self.baseline, self.fixture['baseline_cache'],
                    self.fixture['contrasts'], self.fixture['message_session_lifecycle_process'], self.process)
        self.assertEqual(events, ['state', 'cache', 'process'])

    def test_authored_denial_request_failures_still_check_observer_state_and_cache(self):
        self.request_hook = lambda phase, response: (_ for _ in ()).throw(OSError('private'))
        with patch.object(scopes, 'verify_read', wraps=scopes.verify_read) as verify:
            report = self.run_offline()
        self.assertFalse(adapter.acceptance(report))
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs['observed'], [])
        self.assertIsNone(verify.call_args.kwargs['status'])
        self.assert_restored(report)

    def test_process_restart_death_missing_guard_and_wrong_backend_fail_closed(self):
        for mutation in ('restart', 'death', 'missing', 'wrong-label'):
            def mutate(phase, response):
                if mutation == 'restart': self.process['instance'] += 1
                if mutation == 'death': self.process['running'] = False
                return response
            self.process = {'pid': 123, 'instance': 456, 'running': True}
            self.request_hook = mutate
            self.fixture['message_session_lifecycle_process'] = None if mutation == 'missing' else lambda: dict(self.process)
            report = self.run_offline(label='django-native' if mutation == 'wrong-label' else 'go-native')
            self.assertFalse(adapter.acceptance(report))
            self.assert_restored(report)

    def test_real_workload_pair_helpers_preserve_baseline_between_all_four_offline_labels(self):
        from TESTS.adapters.django import message_read_scopes_runtime as runtime
        from TESTS.adapters.django import message_reads_runtime as shared_runtime
        report = {'workloads': [], 'lifecycle_workloads': []}
        cases = {case['name']: case for case in self.fixture['runtime']['cases']}
        scopes_calls = []
        def request(client, method, path, **kwargs):
            cid = kwargs['headers']['X-Correlation-ID']
            if cid not in cases:
                return self.request(client, method, path, **kwargs)
            # Before each original scope read the full baseline must be present.
            self.assertEqual(cache.snapshot_historical_read_cache(self.raw).entries,
                             self.fixture['baseline_cache'].entries)
            case = cases[cid]
            self.assertEqual(path, case['path'])
            self.assertEqual(client.token, case['token'])
            scopes_calls.append(cid)
            if self.backend == 'django':
                self.observer.events.append({'seq': len(self.observer.events), 'method': method,
                    'path': path.split('?', 1)[0], 'cid': cid, 'status': case['status'], 'error_type': None})
            return case['status'], copy.deepcopy(case['response']), {
                'Content-Language': 'en', 'Content-Type': 'application/json', 'Allow': 'GET, OPTIONS'}
        server = SimpleNamespace(started=True, should_exit=False, force_exit=False,
            config=SimpleNamespace(app=self.observer))
        thread = SimpleNamespace(is_alive=lambda: True)
        process = SimpleNamespace(pid=123, poll=lambda: None)
        guards = {'django': shared_runtime.django_liveness_guard(server, thread, self.observer),
                  'go': runtime.go_liveness_guard(process)}
        container = {'Id': 'a' * 64, 'State': {'Running': True, 'StartedAt': '2026-10-26T01:02:03Z'},
                     'RestartCount': 0}
        with patch('socket.socket', side_effect=AssertionError('socket forbidden')), \
             patch.object(adapter, '_session_cache_key', return_value=KEY), \
             patch.object(scopes, 'snapshot_state', side_effect=lambda apps: copy.deepcopy(self.state)), \
             patch.object(contract.Client, 'request', new=request):
            for label, backend in contract.LABELS.items():
                self.backend = backend
                guard = guards[backend]
                if label.startswith('caddy'):
                    guard = runtime.caddy_liveness_guard(guard, container['Id'],
                        inspector=lambda _: copy.deepcopy(container))
                runtime.run_workloads(label, backend, 'http://offline.invalid', self.fixture,
                                      None, self.raw, self.observer, guard, report)
                self.assertEqual(cache.snapshot_historical_read_cache(self.raw).entries,
                                 self.fixture['baseline_cache'].entries)
                self.assertEqual(self.state, self.baseline)
        self.assertEqual([r['label'] for r in report['workloads']], list(contract.LABELS))
        self.assertEqual([r['label'] for r in report['lifecycle_workloads']], list(contract.LABELS))
        self.assertTrue(all(adapter.acceptance(r) for r in report['lifecycle_workloads']))
        self.assertTrue(all(r['success'] and len(r['cases']) == 38 for r in report['workloads']))
        self.assertEqual(len(scopes_calls), 4 * 38)
        self.assertEqual(len(self.calls), 4 * 4)

    def test_integrated_handle_and_gateway_guards_reject_changes_and_restore_cache(self):
        from TESTS.adapters.django import message_reads_runtime as shared_runtime
        from TESTS.adapters.django import message_read_scopes_runtime as scopes_runtime
        for label, backend in contract.LABELS.items():
            for phase in ('warm', 'denied'):
                for change in (('backend', 'gateway') if label.startswith('caddy') else ('backend',)):
                    self.observer.events.clear()
                    server = SimpleNamespace(started=True, should_exit=False, force_exit=False,
                        config=SimpleNamespace(app=self.observer))
                    thread = SimpleNamespace(alive=True)
                    thread.is_alive = lambda: thread.alive
                    process = SimpleNamespace(pid=123, returncode=None)
                    process.poll = lambda: process.returncode
                    guard = (shared_runtime.django_liveness_guard(server, thread, self.observer)
                             if backend == 'django' else scopes_runtime.go_liveness_guard(process))
                    container = {'Id': 'a' * 64, 'State': {'Running': True,
                        'StartedAt': '2026-10-26T01:02:03Z'}, 'RestartCount': 0}
                    if label.startswith('caddy'):
                        guard = scopes_runtime.caddy_liveness_guard(guard, container['Id'],
                            inspector=lambda _: copy.deepcopy(container))
                    self.fixture['message_session_lifecycle_process'] = guard
                    def mutate(authored, response):
                        if authored['name'].endswith('-' + phase):
                            if change == 'gateway':
                                container['RestartCount'] += 1
                            else:
                                thread.alive, process.returncode = False, 0
                        return response
                    self.request_hook = mutate
                    with self.subTest(label=label, phase=phase, change=change):
                        report = self.run_offline(backend=backend, label=label)
                        self.assertFalse(adapter.acceptance(report))
                        self.assertEqual(report['failure'], {'check': 'process'})
                        self.assert_restored(report)

    def test_process_probe_defensively_copies_the_private_guard_identity(self):
        value = {'pid': 123, 'instance': 456, 'running': True}
        original = adapter._process(lambda: value)
        value['instance'] = 999
        self.assertNotEqual(original, value)

    def test_actual_observer_delta_rejects_go_to_django_misroute(self):
        def misroute(phase, response):
            self.observer.events.append({'seq': len(self.observer.events), 'method': phase['method'],
                'path': phase['path'], 'cid': phase['name'], 'status': response[0], 'error_type': None})
            return response
        self.request_hook = misroute
        report = self.run_offline()
        self.assertEqual(report['failure']['check'], 'attribution')
        self.assertFalse(adapter.acceptance(report))
        self.assert_restored(report)

    def test_runtime_projection_cannot_change_authored_requests_or_credential_binding(self):
        runtime = adapter.runtime_manifest(self.source, self.fixture['tokens'], self.fixture['sessions'])
        contract.validate_runtime(runtime)
        for mutation in ('missing', 'path', 'method', 'status', 'response', 'token', 'session', 'extra', 'source'):
            changed = copy.deepcopy(runtime)
            phase = changed['scenarios'][0]['phases'][1]
            if mutation == 'missing': changed['scenarios'][0]['phases'].pop(0)
            elif mutation == 'token': phase['token'] = 'another-private-token'
            elif mutation == 'session': phase['headers']['X-Impersonation-Id'] = 'b1000000-0000-4000-8000-999999999999'
            elif mutation == 'extra': phase['extra'] = True
            elif mutation == 'source': changed['source_sha256'] = '0' * 64
            else: phase[mutation] = {'path': '/other', 'method': 'POST', 'status': 200, 'response': {}}[mutation]
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                contract.validate_runtime(changed)

    def test_reference_fixture_binding_cannot_select_another_session_or_actor(self):
        for field in ('tokens', 'sessions'):
            changed = copy.deepcopy(self.fixture[field])
            self.fixture[field]['admin' if field == 'tokens' else 'admin_owner'] = (
                'wrong-private-token' if field == 'tokens' else 'b1000000-0000-4000-8000-999999999999')
            report = self.run_offline()
            self.assertFalse(adapter.acceptance(report))
            self.assertFalse(self.calls)
            self.fixture[field] = changed

    def test_acceptance_rejects_omissions_tampering_and_unrecognized_private_fields(self):
        report = self.run_offline()
        self.assertTrue(adapter.acceptance(report))
        for mutation in ('missing-warm', 'duplicate', 'order', 'status', 'state', 'cache', 'process',
                         'transition', 'restoration', 'response', 'attribution', 'source', 'private'):
            changed = copy.deepcopy(report)
            scenario = changed['scenarios'][0]
            if mutation == 'missing-warm': scenario['phases'].pop(0)
            if mutation == 'duplicate': changed['scenarios'][1] = scenario
            if mutation == 'order': scenario['phases'].reverse()
            if mutation == 'status': scenario['phases'][1]['status'] = 200
            if mutation == 'state': scenario['phases'][1]['state_sha256'] = '0' * 64
            if mutation == 'cache': scenario['phases'][1]['cache_sha256'] = report['baseline']['cache_sha256']
            if mutation == 'process': scenario['process_sha256'] = '0' * 64
            if mutation == 'transition': scenario['transition']['removed_keys'] = 2
            if mutation == 'restoration': changed['restoration']['verified'] = False
            if mutation == 'response': scenario['phases'][1]['authored_sha256'] = '0' * 64
            if mutation == 'attribution': scenario['phases'][1]['checks']['attribution'] = False
            if mutation == 'source': changed['source_sha256'][next(iter(changed['source_sha256']))] = '0' * 64
            if mutation == 'private': scenario['private-token'] = 'must-not-cross-boundary'
            with self.subTest(mutation=mutation): self.assertFalse(adapter.acceptance(changed))

    def test_restoration_receipt_requires_exact_boolean_and_count_types(self):
        report = self.run_offline()
        for location in ('workload', 'scenario'):
            for field in ('verified', 'checks', 'models'):
                changed = copy.deepcopy(report)
                receipt = changed['restoration'] if location == 'workload' else changed['scenarios'][0]['restoration']
                if field == 'verified': receipt[field] = 1
                if field == 'checks': receipt[field]['session'] = 1
                if field == 'models': receipt[field] = 128.0
                with self.subTest(location=location, field=field):
                    self.assertFalse(adapter.acceptance(changed))

    def test_both_scenarios_must_observe_the_same_removed_cache(self):
        report = self.run_offline()
        self.assertTrue(adapter.acceptance(report))
        self.assertEqual(report['scenarios'][0]['transition']['cache_sha256'],
                         report['scenarios'][1]['transition']['cache_sha256'])
        changed = copy.deepcopy(report)
        second = changed['scenarios'][1]
        contradictory = '0' * 64
        self.assertNotIn(contradictory, (report['baseline']['cache_sha256'],
            report['scenarios'][0]['transition']['cache_sha256']))
        second['transition']['cache_sha256'] = contradictory
        second['phases'][1]['cache_sha256'] = contradictory
        self.assertFalse(adapter.acceptance(changed))

    def test_both_caddy_labels_use_the_same_existing_backend_attribution(self):
        for label, backend in (('caddy-v1', 'django'), ('caddy-v2', 'go')):
            self.observer.events.clear()
            report = self.run_offline(backend=backend, label=label)
            self.assertTrue(adapter.acceptance(report))

    def test_safe_receipts_never_include_private_inputs_or_exception_text(self):
        self.request_hook = lambda phase, response: (_ for _ in ()).throw(RuntimeError(
            'private-token-admin original-private-dump private SQL actor body ' + SESSION))
        report = self.run_offline()
        encoded = json.dumps(report)
        for secret in ('private-token', 'original-private-dump', 'private SQL', SESSION, KEY.decode(),
                       'first@example.test', 'Original actor private body', 'User-Id', 'X-Impersonation-Id'):
            self.assertNotIn(secret, encoded)
        self.assert_restored(report)

    def test_portable_contract_imports_without_any_backend_modules_or_sockets(self):
        module_path = Path(contract.__file__)
        spec = importlib.util.spec_from_file_location('portable_lifecycle_control', module_path)
        with patch.dict(sys.modules, {'django': None, 'redis': None, 'application': None,
                'TESTS.adapters.django.message_session_lifecycle': None}), \
             patch('socket.socket', side_effect=AssertionError('socket forbidden')):
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(len(module.load_source()['scenarios']), 2)


if __name__ == '__main__':
    unittest.main()
