"""Synthetic consumer controls only: no PG/HTTP/live acceptance evidence."""
import copy
import unittest
from dataclasses import replace

from TESTS.adapters.django import message_connection_attribution as attribution
from TESTS.adapters.django.message_mutation_concurrency import WaitProof

RUN = '00000000-0000-4000-8000-000000000123'


class Stream:
    """Independent small producer-shaped fixture, not parser-generated counters."""
    def __init__(self, backend):
        self.backend = backend
        common = ('request_starts request_finishes finish_calls active_requests query_starts '
                  'query_ends active_queries active_bindings query_errors connection_violations post_close_events')
        django = 'unattributed_queries request_errors'
        go = ('active_driver_operations unbound_starts unbound_ends violations late_starts late_ends '
              'duplicate_ends duplicate_finishes reused_contexts missing_contexts foreign_contexts '
              'context_mismatches driver_starts driver_ends driver_errors driver_unbound_starts '
              'driver_unbound_ends connect_starts connect_ends connect_errors reset_starts reset_ends '
              'reset_errors ping_starts ping_ends ping_errors reset_probes driver_violations '
              'unknown_driver_kinds invalid_reset_probes')
        self.counts = dict.fromkeys((common + ' ' + (django if backend == 'django' else go)).split(), 0)
        self.events = []
        self.add('header')

    def add(self, kind, request=None, query=None, pid=None):
        c = self.counts
        if kind == 'request_begin':
            c['request_starts'] += 1
            c['active_requests'] += 1
        if kind == 'request_finish':
            c['request_finishes'] += 1
            c['finish_calls'] += 1
            c['active_requests'] -= 1
        if kind == 'query_start':
            c['query_starts'] += 1
            c['active_queries'] += 1
            c['active_bindings'] += 1
            if self.backend == 'django' and request is None:
                c['unattributed_queries'] += 1
        if kind == 'query_end':
            c['query_ends'] += 1
            c['active_queries'] -= 1
            c['active_bindings'] -= 1
        event = dict(stream='query_connection', version=1, run_id=RUN,
                     sequence=len(self.events) + 1, type=kind, error=False,
                     complete=kind != 'header', **c)
        if self.backend == 'django':
            event.update(request_id=None, query_id=None)
        if request is not None:
            event['request_id'] = f'{RUN}-{request}'
        if query is not None:
            event['query_id'] = f'{RUN}-q{query}'
        if pid is not None:
            event['backend_pid'] = pid
        self.events.append(event)
        return self

    def snapshot(self):
        return copy.deepcopy(dict(backend=self.backend, run_id=RUN, events=self.events))


def workload(backend='go'):
    s = Stream(backend)
    s.add('request_begin', 1).add('request_finish', 1)  # known readiness baseline
    baseline = s.snapshot()
    s.add('request_begin', 2).add('request_begin', 3)
    s.add('query_start', 2, 1, 21).add('query_start', 3, 2, 22)
    blocked = s.snapshot()
    proofs = (WaitProof(21, '201', 'xact-a', 'backend-a', '21/1'),
              WaitProof(22, None, 'xact-b', 'backend-b', '22/1'))
    s.add('query_end', 3, 2, 22).add('request_finish', 3)
    s.add('query_end', 2, 1, 21).add('request_finish', 2)
    post = s.snapshot()
    s.add('footer')
    return baseline, blocked, proofs, post, s.snapshot()


class ParserControls(unittest.TestCase):
    def test_django_first_unassigned_prefix_matches_actual_producer(self):
        s = Stream('django').add('query_start', query=1, pid=11)
        parsed = attribution.parse_observation(s.snapshot())
        self.assertEqual(parsed.active_queries, {f'{RUN}-q1': (None, 11)})
        self.assertEqual(parsed.events[-1]['unattributed_queries'], 1)
        with self.assertRaises(attribution.AttributionError):
            attribution.require_baseline(parsed)

    def test_real_shaped_driver_totals_can_advance_between_connection_records(self):
        snapshot = workload()[4]
        for event in snapshot['events'][4:]:
            event.update(driver_starts=3, driver_ends=3, connect_starts=1, connect_ends=1,
                         reset_starts=1, reset_ends=1, reset_probes=1, ping_starts=1, ping_ends=1)
        self.assertTrue(attribution.parse_observation(snapshot, final=True).finalized)
        # An in-progress driver operation is permitted in a provisional prefix.
        prefix = copy.deepcopy(snapshot)
        prefix['events'] = prefix['events'][:7]
        for event in prefix['events'][4:]:
            event.update(driver_ends=2, ping_ends=0, active_driver_operations=1)
        self.assertFalse(attribution.parse_observation(prefix).finalized)
        # A previously counted driver operation cannot disappear later.
        snapshot['events'][6]['driver_starts'] = 0
        with self.assertRaises(attribution.AttributionError):
            attribution.parse_observation(snapshot, final=True)

    def test_valid_prefixes_final_and_completed_baseline_for_both_backends(self):
        for backend in ('go', 'django'):
            baseline, blocked, proofs, post, final = workload(backend)
            before = attribution.parse_observation(blocked)
            self.assertEqual(set(before.active_queries), {f'{RUN}-q1', f'{RUN}-q2'})
            self.assertFalse(before.finalized)
            self.assertTrue(attribution.parse_observation(final, final=True).finalized)
            self.assertEqual(len(attribution.bind_waits(
                attribution.parse_observation(baseline), before, before, proofs)), 2)
            attribution.extend_observation(before, post)

    def test_schema_sequence_ids_types_errors_and_counters_fail_closed(self):
        for backend in ('go', 'django'):
            original = workload(backend)[4]
            mutations = [lambda e: e[0].update(version=True),
                         lambda e: e[0].update(stream='other'),
                         lambda e: e[2].update(sequence=7),
                         lambda e: e.insert(2, copy.deepcopy(e[1])),
                         lambda e: e[2].update(run_id='00000000-0000-4000-8000-000000000999'),
                         lambda e: e[3].update(request_id=f'{RUN}-99'),
                         lambda e: e[5].update(query_id=f'{RUN}-q99'),
                         lambda e: e[5].update(backend_pid=True),
                         lambda e: e[7].update(backend_pid=23),
                         lambda e: e[4].update(type='unknown'),
                         lambda e: e[6].update(error=True),
                         lambda e: e[6].update(connection_violations=1),
                         lambda e: e[6].update(complete=False),
                         lambda e: e[6].update(success=True),
                         lambda e: e[6].pop('active_requests'),
                         lambda e: e[6].update(active_queries=99),
                         lambda e: e[6].update(query_starts=-1),
                         lambda e: e[0].update(request_id='spoof'),
                         lambda e: e[6].update(query_errors=1)]
            for mutate in mutations:
                with self.subTest(backend=backend, mutation=mutations.index(mutate)):
                    snapshot = copy.deepcopy(original)
                    mutate(snapshot['events'])
                    with self.assertRaises(attribution.AttributionError):
                        attribution.parse_observation(snapshot, final=True)

    def test_prefix_rewrite_truncation_missing_duplicate_and_late_footer(self):
        baseline, blocked, _, post, final = workload()
        previous = attribution.parse_observation(blocked)
        for snapshot in (baseline, dict(post, events=post['events'][1:]),
                         dict(post, events=[dict(post['events'][0], complete=True), *post['events'][1:]])):
            with self.assertRaises(attribution.AttributionError):
                attribution.extend_observation(previous, snapshot)
        for events in (post['events'], final['events'] + [final['events'][-1]],
                       [*final['events'], dict(final['events'][-1], type='invalidated')],
                       final['events'][:-2] + final['events'][-1:]):
            with self.assertRaises(attribution.AttributionError):
                attribution.parse_observation(dict(final, events=events), final=True)
        self.assertFalse(attribution.parse_observation(post).finalized)

    def test_native_pid_and_lifetime_must_match_and_no_overlapping_pid(self):
        for backend in ('go', 'django'):
            final = workload(backend)[4]
            for index, changes in ((6, {'backend_pid': 21}), (7, {'request_id': f'{RUN}-2'}),
                                   (7, {'query_id': f'{RUN}-q1'}), (7, {'type': 'request_finish'})):
                bad = copy.deepcopy(final)
                bad['events'][index].update(changes)
                with self.assertRaises(attribution.AttributionError):
                    attribution.parse_observation(bad, final=True)

    def test_driver_counters_sticky_errors_and_unbalanced_footer_rejected(self):
        final = workload()[4]
        for key, value in (('driver_errors', 1), ('unbound_starts', 1), ('driver_starts', 1),
                           ('active_driver_operations', 1), ('reset_probes', 1),
                           ('ping_ends', 1), ('post_close_events', 1)):
            bad = copy.deepcopy(final)
            bad['events'][-1][key] = value
            with self.assertRaises(attribution.AttributionError):
                attribution.parse_observation(bad, final=True)

    def test_snapshot_wrapper_flags_or_foreign_run_are_not_trusted(self):
        snapshot = workload()[1]
        for change in ({'success': True}, {'backend': 'other'}, {'run_id': RUN.upper()},
                       {'events': 'partial JSON'}, {'run_id': '00000000-0000-0000-0000-000000000000'}):
            # RUN has no hex letters, so use an explicitly foreign canonical run.
            if change == {'run_id': RUN.upper()}:
                change = {'run_id': '00000000-0000-4000-8000-000000000999'}
            with self.assertRaises(attribution.AttributionError):
                attribution.parse_observation(dict(snapshot, **change))


def owned_connections(cursor):
    return {role: dict(native_pid=pid, catalogs=[(pid, f'owned-{pid}')] * 2,
                       end_cursor=cursor)
            for role, pid in zip(('default', 'blocker', 'monitor'), (11, 12, 13))}


def unassigned_workload():
    s = Stream('django')
    s.add('query_start', query=1, pid=11).add('query_end', query=1, pid=11)
    s.add('query_start', query=2, pid=12).add('query_end', query=2, pid=12)
    baseline = s.snapshot()
    s.add('request_begin', 1).add('request_begin', 2)
    s.add('query_start', 1, 3, 21).add('query_start', 2, 4, 22)
    s.add('query_start', query=5, pid=13)
    during = s.snapshot()
    s.add('query_end', query=5, pid=13)
    blocked = s.snapshot()
    s.add('query_end', 1, 3, 21).add('request_finish', 1)
    s.add('query_end', 2, 4, 22).add('request_finish', 2)
    s.add('query_start', query=6, pid=11).add('query_end', query=6, pid=11)
    s.add('query_start', query=7, pid=13).add('query_end', query=7, pid=13)
    post = s.snapshot()
    s.add('footer')
    return baseline, during, blocked, post, s.snapshot()


class UnassignedControls(unittest.TestCase):
    def test_baseline_monitor_and_cleanup_queries_reconcile_full_lifecycle(self):
        baseline, during, blocked, post, final = unassigned_workload()
        b, d, p = map(attribution.parse_observation, (baseline, during, post))
        attribution.require_baseline(b)
        attribution.require_window(b, d)
        with self.assertRaises(attribution.AttributionError):
            attribution.require_window(b, d, completed=True)
        attribution.require_window(b, p, completed=True)
        parsed = attribution.parse_observation(final, final=True)
        attribution.require_owned_queries(parsed, owned_connections(len(post['events'])), completed=True)
        self.assertEqual(parsed.events[-1]['query_starts'], 7)
        self.assertEqual(parsed.events[-1]['query_ends'], 7)
        self.assertEqual(parsed.events[-1]['unattributed_queries'], 5)
        proofs = workload()[2]
        cursor = len(blocked['events'])
        result = attribution.verify_lifecycle(b, p, [(cursor, cursor, proofs)] * 2,
            [dict(response_headers=[('X-Query-Observation-ID', f'{RUN}-{i}')]) for i in (1, 2)],
            final_observation=final)
        self.assertTrue(result['stream_finalized'])

    def test_unassigned_counter_and_end_request_pid_query_mismatches_reject(self):
        final = unassigned_workload()[-1]
        for index, changes in ((1, {'unattributed_queries': 0}),
                               (2, {'unattributed_queries': 2}),
                               (2, {'request_id': f'{RUN}-1'}),
                               (2, {'backend_pid': 12}), (2, {'query_id': f'{RUN}-q2'}),
                               (-1, {'query_starts': 6}), (-1, {'unattributed_queries': 4})):
            bad = copy.deepcopy(final)
            bad['events'][index].update(changes)
            with self.subTest(index=index, changes=changes), self.assertRaises(attribution.AttributionError):
                attribution.parse_observation(bad, final=True)

    def test_go_null_request_still_rejected(self):
        s = Stream('go').add('query_start', query=1, pid=11)
        s.events[-1]['request_id'] = None
        with self.assertRaises(attribution.AttributionError):
            attribution.parse_observation(s.snapshot())

    def test_unassigned_never_binds_http_waiter_even_on_matching_pid(self):
        s = Stream('django')
        b = attribution.parse_observation(s.snapshot())
        s.add('request_begin', 1).add('query_start', query=1, pid=21)
        p = attribution.parse_observation(s.snapshot())
        with self.assertRaises(attribution.AttributionError):
            attribution.bind_waits(b, p, p, workload()[2][:1])

    def test_unowned_ambiguous_recycled_and_retired_pids_reject(self):
        parsed = attribution.parse_observation(unassigned_workload()[-1], final=True)
        owners = owned_connections(len(parsed.events) - 1)
        mutations = [lambda o: o['default'].update(native_pid=99, catalogs=[(99, 'other')] * 2),
                     lambda o: o['monitor'].update(native_pid=11, catalogs=[(11, 'owned-11')] * 2),
                     lambda o: o['default']['catalogs'].__setitem__(1, (11, 'recycled')),
                     lambda o: o['default'].update(catalogs=[(12, 'owned-11')] * 2),
                     lambda o: o['default'].update(catalogs=[(11, 'owned-11')]),
                     lambda o: o['default'].update(end_cursor=2),
                     lambda o: o.update(application=dict(native_pid=99, catalogs=[(99, 'x')] * 2,
                                                         end_cursor=len(parsed.events)))]
        for mutate in mutations:
            bad = copy.deepcopy(owners)
            mutate(bad)
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(attribution.AttributionError):
                attribution.require_owned_queries(parsed, bad, completed=True)
        with self.assertRaises(attribution.AttributionError):
            attribution.require_owned_queries(parsed, owners, completed=True,
                activity=[dict(pid=12, backend='recycled')])

    def test_same_pid_owned_http_and_unassigned_cannot_be_confused(self):
        final = unassigned_workload()[-1]
        for event in final['events']:
            if event.get('backend_pid') == 21:
                event['backend_pid'] = 11
        parsed = attribution.parse_observation(final, final=True)
        with self.assertRaises(attribution.AttributionError):
            attribution.require_owned_queries(parsed, owned_connections(len(parsed.events)), completed=True)


class BindingControls(unittest.TestCase):
    def test_two_waiters_must_belong_to_distinct_requests(self):
        s = Stream('go').add('request_begin', 1).add('request_finish', 1)
        baseline = attribution.parse_observation(s.snapshot())
        s.add('request_begin', 2).add('request_begin', 3)
        s.add('query_start', 2, 1, 21).add('query_start', 2, 2, 22)
        blocked = attribution.parse_observation(s.snapshot())
        proofs = workload()[2]
        with self.assertRaises(attribution.AttributionError):
            attribution.bind_waits(baseline, blocked, blocked, proofs)

    def test_bracketing_excludes_start_after_sample_or_end_before_sample(self):
        baseline, blocked, proofs, post, _ = workload()
        b, p = attribution.parse_observation(baseline), attribution.parse_observation(blocked)
        for before, after in ((b, p), (p, attribution.parse_observation(post))):
            with self.assertRaises(attribution.AttributionError):
                attribution.bind_waits(b, before, after, proofs)

    def test_extra_query_free_request_or_unknown_active_baseline_rejected(self):
        baseline, blocked, proofs, _, _ = workload()
        s = Stream('go')
        s.add('request_begin', 1)
        with self.assertRaises(attribution.AttributionError):
            attribution.require_baseline(attribution.parse_observation(s.snapshot()))
        s = Stream('go').add('request_begin', 1).add('request_finish', 1)
        s.add('request_begin', 2).add('request_begin', 3)
        s.add('query_start', 2, 1, 21).add('query_start', 3, 2, 22)
        s.add('request_begin', 4).add('request_finish', 4)
        p = attribution.parse_observation(s.snapshot())
        with self.assertRaises(attribution.AttributionError):
            attribution.bind_waits(attribution.parse_observation(baseline), p, p, proofs)

    def test_wrong_waiter_pid_or_duplicate_request_binding_rejected(self):
        baseline, blocked, proofs, _, _ = workload()
        b, p = attribution.parse_observation(baseline), attribution.parse_observation(blocked)
        for pair in ((proofs[0], replace(proofs[1], pid=23)), (proofs[0], proofs[0])):
            with self.assertRaises(attribution.AttributionError):
                attribution.bind_waits(b, p, p, pair)

class LifecycleControls(unittest.TestCase):
    def test_same_pid_same_transaction_cannot_switch_generated_query_between_samples(self):
        baseline, blocked, proofs, _, _ = workload()
        s = Stream('go')
        s.events = copy.deepcopy(blocked['events'])
        s.counts = {key: blocked['events'][-1][key] for key in s.counts}
        first_cursor = len(s.events)
        s.add('query_end', 2, 1, 21).add('query_start', 2, 3, 21)
        second_cursor = len(s.events)
        s.add('query_end', 2, 3, 21).add('query_end', 3, 2, 22)
        s.add('request_finish', 2).add('request_finish', 3)
        captured = attribution.parse_observation(s.snapshot())
        responses = [dict(response_headers=[('X-Query-Observation-ID', f'{RUN}-{i}')]) for i in (2, 3)]
        with self.assertRaises(attribution.AttributionError):
            attribution.verify_lifecycle(attribution.parse_observation(baseline), captured,
                [(first_cursor, first_cursor, proofs), (second_cursor, second_cursor, proofs)], responses)

    def verify(self, baseline, blocked, proofs, post, final=None, responses=None):
        b, p = attribution.parse_observation(baseline), attribution.parse_observation(post)
        cursor = len(blocked['events'])
        return attribution.verify_lifecycle(b, p, [(cursor, cursor, proofs)] * 2,
            responses or [{'response_headers': [('X-Query-Observation-ID', f'{RUN}-{i}')]} for i in (3, 2)],
            final_observation=final)

    def test_valid_two_request_ends_response_reordering_and_final_footer(self):
        for backend in ('django', 'go'):
            baseline, blocked, proofs, post, final = workload(backend)
            result = self.verify(baseline, blocked, proofs, post)
            self.assertFalse(result['stream_finalized'])
            self.assertTrue(self.verify(baseline, blocked, proofs, post, final)['stream_finalized'])

    def test_original_header_missing_duplicate_spoof_or_same_id_rejected(self):
        baseline, blocked, proofs, post, final = workload()
        originals = [[('X-Query-Observation-ID', f'{RUN}-2')],
                     [('X-Query-Observation-ID', f'{RUN}-3')]]
        for headers in ([], originals[0] * 2, [('x-query-observation-id', 'supplied-client-label')],
                        [('x-query-observation-id', f'{RUN}-3')]):
            with self.assertRaises(attribution.AttributionError):
                self.verify(baseline, blocked, proofs, post, final,
                    responses=[dict(response_headers=headers), dict(response_headers=originals[1])])

    def test_changed_backend_start_xact_vxid_and_permanent_xid_rejected(self):
        baseline, blocked, proofs, post, _ = workload()
        b, p = attribution.parse_observation(baseline), attribution.parse_observation(post)
        cursor = len(blocked['events'])
        responses = [dict(response_headers=[('X-Query-Observation-ID', f'{RUN}-{i}')]) for i in (2, 3)]
        for changes in ({'backend': 'recycled'}, {'xact': 'new'}, {'vxid': '21/2'},
                        {'xid': '999'}, {'xid': None}):
            changed = (replace(proofs[0], **changes), proofs[1])
            with self.assertRaises(attribution.AttributionError):
                attribution.verify_lifecycle(b, p, [(cursor, cursor, proofs), (cursor, cursor, changed)], responses)
        # Real PostgreSQL may acquire its first permanent XID during the wait.
        assigned = (proofs[0], replace(proofs[1], xid='202'))
        self.assertEqual(attribution.verify_lifecycle(b, p,
            [(cursor, cursor, proofs), (cursor, cursor, assigned)], responses)['waiters'], 2)

    def test_late_extra_query_free_request_and_incomplete_ends_rejected(self):
        baseline, blocked, proofs, post, final = workload()
        for bad in (blocked, dict(post, events=post['events'][:-1])):
            with self.assertRaises(attribution.AttributionError):
                self.verify(baseline, blocked, proofs, bad)
        s = Stream('go')
        s.events, s.counts = copy.deepcopy(post['events']), dict(
            (k, post['events'][-1][k]) for k in s.counts)
        s.add('request_begin', 4).add('request_finish', 4).add('footer')
        with self.assertRaises(attribution.AttributionError):
            self.verify(baseline, blocked, proofs, post, s.snapshot())

    def test_balanced_nonzero_header_cannot_invent_driver_history(self):
        snapshot = Stream('go').snapshot()
        snapshot['events'][0].update(driver_starts=1, driver_ends=1, connect_starts=1, connect_ends=1)
        with self.assertRaises(attribution.AttributionError):
            attribution.parse_observation(snapshot)


class FinalizationControls(unittest.TestCase):
    def test_terminal_open_transaction_cannot_be_hidden_by_cleanup_flags(self):
        from TESTS import test_message_mutation_concurrency as existing
        private, final = self.private()
        lock = existing.LockControls()
        lock.setUp()
        rows = [lock.root(), lock.row()]
        for row in rows: row['locks_raw'] = copy.deepcopy(row['locks'])
        private['activity'].append(rows)
        self.assertTrue(all(private['cleanup'][key] for key in
                            ('database_quiescent', 'reset_safe', 'cleanup_complete')))
        self.assertFalse(existing.probe.finalize_attribution(private, final)['full_concurrency_acceptance'])

    def test_terminal_boundaries_catalog_admission_and_asgi_are_recomputed(self):
        from TESTS import test_message_mutation_concurrency as existing
        changes = ('missing', 'cursor', 'appended-idle', 'open-xact', 'active', 'xid',
                   'lock', 'blocker', 'missing-blocker', 'duplicate', 'asgi', 'sent',
                   'attempted', 'incomplete', 'pending-wire', 'busy-extra')
        for name in changes:
            private, final = self.private()
            row = private['activity'][-1][0]
            if name == 'missing': del private['terminal_observation']
            if name == 'cursor': private['terminal_observation']['activity_cursor'] -= 1
            if name == 'appended-idle': private['activity'].append(copy.deepcopy(private['activity'][-1]))
            if name == 'open-xact': row['xact'] = 'still-open'
            if name == 'active': row['state'] = 'active'
            if name == 'xid': row['xid'] = '100'
            if name == 'lock': row['locks'] = row['locks_raw'] = existing.identity_locks(10, None)
            if name == 'blocker': row['backend'] = 'recycled'
            if name == 'missing-blocker': row['pid'] = 99
            if name == 'duplicate': private['activity'][-1].append(copy.deepcopy(row))
            if name == 'asgi': private['terminal_observation']['asgi'] = [{'unexpected': True}]
            if name == 'sent': private['responses'][0]['sent'] = False
            if name == 'attempted': private['responses'][0]['attempted_send'] = False
            if name == 'incomplete': private['responses'][0]['complete'] = False
            if name == 'pending-wire': private['responses'][0]['wire'] = b''
            if name == 'busy-extra':
                private['activity'][-1].append(dict(row, pid=99, backend='extra', xact='open'))
            with self.subTest(name=name):
                self.assertFalse(existing.probe.finalize_attribution(private, final)['full_concurrency_acceptance'])

    def django_private(self):
        from TESTS import test_message_mutation_concurrency as existing
        private, _ = self.private()
        baseline, _, blocked, captured, final = unassigned_workload()
        for snapshot in (baseline, blocked, captured, final):
            for event in snapshot['events']:
                if 'backend_pid' in event:
                    event['backend_pid'] = {12: 10, 21: 20, 22: 21}.get(
                        event['backend_pid'], event['backend_pid'])
        private['connection_attribution'].update(captured=captured, baseline_cursor=len(baseline['events']))
        for sample in private['connection_attribution']['samples']:
            sample.update(before_cursor=len(blocked['events']), after_cursor=len(blocked['events']))
        owners = owned_connections(len(captured['events']))
        owners['blocker'] = dict(native_pid=10, catalogs=[(10, 'blocker-backend')] * 2,
                                 end_cursor=len(captured['events']))
        private['connection_owners'] = owners
        fixture = private['attribution_verification']['fixture']
        cases = existing.probe._cases(existing.contract, fixture, 'django')
        private['after'][0][existing.contract.AUDIT][-1]['cid'] = cases[0]['name']
        private['asgi'] = [dict(seq=i + 1, method='DELETE', path=case['path'], cid=case['name'],
                                status=private['responses'][i]['status'], error_type=None)
                           for i, case in enumerate(cases)]
        for i, response in enumerate(private['responses']):
            wire = response['wire'].replace(f'{RUN}-{i + 2}'.encode(), f'{RUN}-{i + 1}'.encode())
            private['responses'][i], _ = existing.OriginalWireControls().request(wire)
        lock = existing.LockControls()
        lock.setUp()
        rows = [lock.root(), *lock.pair('django')]
        for row in rows[1:]:
            row['query'] = existing.probe.DJANGO_MESSAGE_QUERY.format(
                target=private['blocker'].target, association=private['blocker'].association)
            for item in row['locks']:
                if item['kind'] == 'tuple': item.update(relation=55, page=3, item=7)
        for row in rows: row['locks_raw'] = copy.deepcopy(row['locks'])
        terminal_rows = private['activity'][-1]
        private['activity'] = [copy.deepcopy(rows), copy.deepcopy(rows), terminal_rows]
        private['terminal_observation']['asgi'] = copy.deepcopy(private['asgi'])
        return private, final

    def test_django_owned_sql_is_revalidated_in_pure_finalizer(self):
        from unittest.mock import patch
        from TESTS.adapters.django import message_mutation_concurrency as probe
        private, final = self.django_private()
        snapshot = copy.deepcopy(private)
        with patch('builtins.open', side_effect=AssertionError('no files')), \
             patch.object(probe.socket, 'socket', side_effect=AssertionError('no network')):
            result = probe.finalize_attribution(private, final)
        self.assertTrue(result['full_concurrency_acceptance'], result)
        self.assertEqual(private, snapshot)

    def test_finalizer_rejects_unowned_recycled_ambiguous_and_postseal_sql(self):
        from TESTS.adapters.django import message_mutation_concurrency as probe
        for name in ('missing', 'unowned', 'recycled', 'catalog', 'blocker', 'ambiguous', 'postseal'):
            private, final = self.django_private()
            owners = private['connection_owners']
            if name == 'missing': del private['connection_owners']
            if name == 'unowned':
                owners['default'].update(native_pid=99, catalogs=[(99, 'foreign')] * 2)
            if name == 'recycled': owners['monitor']['catalogs'][-1] = (13, 'recycled')
            if name == 'catalog': private['activity'][0][0]['backend'] = 'recycled'
            if name == 'blocker': owners['blocker']['catalogs'] = [(10, 'foreign')] * 2
            if name == 'ambiguous': owners['monitor'] = copy.deepcopy(owners['default'])
            if name == 'postseal':
                s = Stream('django')
                s.events = copy.deepcopy(final['events'][:-1])
                s.counts = {key: s.events[-1][key] for key in s.counts}
                s.add('query_start', query=8, pid=11).add('query_end', query=8, pid=11).add('footer')
                final = s.snapshot()
            with self.subTest(name=name):
                self.assertFalse(probe.finalize_attribution(private, final)['full_concurrency_acceptance'])

    def test_generated_header_binding_is_checked_on_original_matching_wire(self):
        from TESTS import test_message_mutation_concurrency as existing
        for header in (b'',
                       f'X-Query-Observation-ID: {RUN}-2\r\nx-query-observation-id: {RUN}-2\r\n'.encode(),
                       b'X-Query-Observation-ID: supplied-client-CID\r\n',
                       f'X-Query-Observation-ID: {RUN}-q1\r\n'.encode(),
                       f'X-Query-Observation-ID: {RUN}-3\r\n'.encode(),
                       f'X-Query-Observation-ID: {RUN}-2 \r\n'.encode()):
            private, final = self.private()
            original = f'X-Query-Observation-ID: {RUN}-2\r\n'.encode()
            wire = private['responses'][0]['wire'].replace(original, header)
            response, _ = existing.OriginalWireControls().request(wire)
            self.assertTrue(response['complete'])
            existing.probe._validate_wire(response)  # altered wire and headers agree
            private['responses'][0] = response
            self.assertFalse(existing.probe.finalize_attribution(private, final)['full_concurrency_acceptance'])

    def private(self):
        from TESTS import test_message_mutation_concurrency as existing
        fixture, before, after, cache, contrasts = existing.ExactEffectControls().states()
        fixture.update(baseline_state=copy.deepcopy(before), baseline_cache=cache, contrasts=contrasts)
        lock = existing.LockControls()
        lock.setUp()
        case = existing.probe._cases(existing.contract, fixture, 'go')[0]
        blocker = replace(lock.blocker, target=case['effect']['message_id'],
            association=fixture['authored']['ids']['own_association'],
            deliveries=tuple(row['message_transaction_id'] for row in before[existing.contract.DELIVERY]
                             if row['message_id'] == case['effect']['message_id']))
        s = Stream('go').add('request_begin', 1).add('request_finish', 1)
        baseline = len(s.events)
        s.add('request_begin', 2).add('request_begin', 3)
        s.add('query_start', 2, 1, 20).add('query_start', 3, 2, 21)
        cursor = len(s.events)
        s.add('query_end', 3, 2, 21).add('request_finish', 3)
        s.add('query_end', 2, 1, 20).add('request_finish', 2)
        captured = s.snapshot()
        s.add('footer')
        responses = []
        for status, request in ((200, 2), (404, 3)):
            body = b'{"msg":"message deleted."}' if status == 200 else b'{"msg":"message not found."}'
            wire = (f'HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Language: en\r\n'
                f'Allow: DELETE, OPTIONS\r\nX-Query-Observation-ID: {RUN}-{request}\r\n'
                f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body
            record, _ = existing.OriginalWireControls().request(wire)
            responses.append(record)
        cleanup = dict.fromkeys(('clients_closed threads_joined blocker_released connections_closed '
            'database_quiescent asgi_quiescent guard_stable admission_accounted reset_safe cleanup_complete').split(), True)
        cleanup.update(failures=[], fixture_reset_owner='parent', server_owner='parent')
        rows = [lock.root(), *lock.pair()]
        for row in rows:
            row['locks_raw'] = copy.deepcopy(row['locks'])
        private = dict(responses=responses, before=(before, cache), after=(after, cache), asgi=[],
            blocker=blocker, activity=[copy.deepcopy(rows), copy.deepcopy(rows)], cleanup=cleanup,
            guard_before={'pid': 123, 'instance': 'synthetic', 'running': True},
            guard_last={'pid': 123, 'instance': 'synthetic', 'running': True},
            connection_attribution=dict(captured=captured, baseline_cursor=baseline, samples=[
                dict(before_cursor=cursor, after_cursor=cursor, activity_index=i) for i in range(2)]),
            attribution_verification=dict(fixture=fixture, started=1793008800, finished=1793008801,
                canonical_source=existing.contract.load_source(),
                client_ip='127.0.0.2', execution_failed=False))
        terminal_row = dict(lock.root(), xid=None, xact=None, state='idle', wait='Client',
                            locks=[], locks_raw=[])
        private['activity'].append([terminal_row])
        private['terminal_observation'] = dict(activity_cursor=len(private['activity']), asgi=[])
        return private, s.snapshot()

    def test_finalization_recomputes_and_copies_without_promoting_caller_flags(self):
        from TESTS.adapters.django import message_mutation_concurrency as probe
        private, final = self.private()
        private['diagnostics'] = dict(success=False, full_concurrency_acceptance=False)
        snapshot = copy.deepcopy(private)
        result = probe.finalize_attribution(private, final)
        self.assertTrue(result['full_concurrency_acceptance'], result)
        self.assertEqual(private, snapshot)
        result['cleanup']['reset_safe'] = False
        self.assertTrue(private['cleanup']['reset_safe'])

    def test_finalization_rejects_each_missing_or_changed_actual_domain(self):
        from TESTS.adapters.django import message_mutation_concurrency as probe
        for name in ('footer', 'prefix', 'catalog', 'wire', 'header', 'effects', 'cleanup',
                     'execution', 'asgi', 'canonical', 'extra', 'guard', 'sample-reuse'):
            private, final = self.private()
            if name == 'footer': final['events'].pop()
            if name == 'prefix': final['events'][2]['complete'] = False
            if name == 'catalog': private['activity'][1][1]['backend'] = 'same-PID-recycled'
            if name == 'wire': private['responses'][0]['wire'] += b'extra'
            if name == 'header': private['responses'][0]['response_headers'].append(('X-Query-Observation-ID', 'spoof'))
            if name == 'effects': private['after'][0]['foreign.Model1'][0]['value'] = 'changed'
            if name == 'cleanup': private['cleanup']['clients_closed'] = False
            if name == 'execution': private['attribution_verification']['execution_failed'] = True
            if name == 'asgi': private['asgi'] = [{'unexpected': True}]
            if name == 'canonical': private['blocker'] = replace(private['blocker'], target='foreign')
            if name == 'extra': final['events'][-1]['request_starts'] += 1
            if name == 'guard': private['guard_last']['pid'] += 1
            if name == 'sample-reuse': private['connection_attribution']['samples'][1]['activity_index'] = 0
            # A caller assertion of success repairs none of these failures.
            private['diagnostics'] = dict(success=True, full_concurrency_acceptance=True,
                                          request_transaction_attribution_verified=True)
            with self.subTest(name=name):
                result = probe.finalize_attribution(private, final)
                self.assertFalse(result['full_concurrency_acceptance'])
                self.assertEqual(result['checks_failed'], ['connection-observation-finalization'])

    def test_safe_output_does_not_copy_arbitrary_caller_cleanup_fields(self):
        from TESTS.adapters.django import message_mutation_concurrency as probe
        private, final = self.private()
        private['cleanup']['private_secret'] = 'NEVER-RETURN-THIS'
        result = probe.finalize_attribution(private, final)
        self.assertTrue(result['full_concurrency_acceptance'])
        self.assertNotIn('NEVER-RETURN-THIS', repr(result))

    def test_final_verifier_is_pure_and_uses_retained_independent_canonical_source(self):
        from pathlib import Path
        from unittest.mock import patch
        from TESTS.adapters.django import message_mutation_concurrency as probe
        private, final = self.private()
        with patch.object(Path, 'read_text', side_effect=AssertionError('no file reads')), \
             patch('builtins.open', side_effect=AssertionError('no file opens')), \
             patch.object(probe.socket, 'socket', side_effect=AssertionError('no sockets')):
            self.assertTrue(probe.finalize_attribution(private, final)['full_concurrency_acceptance'])


if __name__ == '__main__':
    unittest.main()
