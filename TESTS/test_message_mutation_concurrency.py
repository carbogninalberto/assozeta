"""Explicitly synthetic offline controls; these never establish live evidence."""
import copy
import io
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from TESTS.adapters.django import message_mutation_concurrency as probe
from TESTS.adapters.django import historical_read_cache as cache
from TESTS.adapters.django import message_reads as shared
from TESTS.contracts import test_message_mutations as contract


def synthetic_fixture():
    source = contract.load_source()
    runtime = copy.deepcopy(source)
    sessions = {name: f'00000000-0000-4000-8000-{number:012d}'
                for number, name in enumerate(source['sessions'], 1)}
    tokens = {actor: 'SYNTHETIC-NOT-SIGNED-' + actor for actor in source['actors']}
    runtime['source_sha256'] = contract.source_digest(source)
    for case in runtime['cases']:
        actor = case.pop('actor')
        case['token'] = None if actor == 'anonymous' else tokens[actor]
        case['headers'] = contract.substitute(case['headers'], sessions)
        case['audit_additional'] = contract.substitute(case['audit_additional'], sessions)
    return dict(authored=source, runtime=runtime, tokens=tokens)


def synthetic_control():
    observer = SimpleNamespace(condition=threading.Condition(), active=0,
        active_websockets=0, websocket_errors=0, websocket_started=0, websocket_completed=0,
        wait_idle=Mock(), delta=Mock(return_value=[]))
    control = probe._Control(backend='go', apps=None, database=Mock(), raw=None,
        fixture=synthetic_fixture(), base_url='http://127.0.0.1:8100',
        process_guard=lambda: dict(pid=12, instance='SYNTHETIC', running=True),
        observer=observer, client_ip='127.0.0.2', private_evidence={}, bounds=probe.Bounds(),
        portable=None, contract=contract)
    return control


def identity_locks(pid, xid):
    """Synthetic PostgreSQL self-locks, including pre-permanent-XID shape."""
    common = dict(relation=None, page=None, item=None, virtualtransaction=f'{pid}/1')
    result = [dict(common, kind='virtualxid', granted=True, mode='ExclusiveLock',
                   xid=None, virtualxid=f'{pid}/1')]
    if xid is not None:
        result.append(dict(common, kind='transactionid', granted=True, mode='ExclusiveLock',
                           xid=xid, virtualxid=None))
    return result


class LockControls(unittest.TestCase):
    def setUp(self):
        self.blocker = probe.Blocker(10, '100', 'blocker-backend', {
            'message': {(55, 3, 7)}, 'delivery': {(56, 4, 1), (56, 4, 2)},
            'owner': {(57, 5, 1)}}, 'target', ('delivery-one', 'delivery-two'), 'root-xact', '10/1', 'association')

    def root(self):
        return dict(pid=10, xid='100', backend='blocker-backend', xact='root-xact',
            state='idle in transaction', wait=None, query='SYNTHETIC blocker', blockers=[],
            locks=identity_locks(10, '100'))

    def row(self, pid=20, *, backend='go', parent=10, tuple_row=(55, 3, 7)):
        return dict(pid=pid, xid=str(pid * 10), xact='xact-' + str(pid),
            backend='backend-' + str(pid), blockers=[parent], state='active', wait='Lock',
            query=probe.GO_MESSAGE_QUERY if backend == 'go' else
                'DELETE FROM "communications_messagetransaction" WHERE '
                '"communications_messagetransaction"."message_transaction_id" IN '
                "('delivery-one', 'delivery-two')",
            locks=[dict(kind='tuple', granted=True, relation=tuple_row[0], page=tuple_row[1],
                        item=tuple_row[2], xid=None, mode='AccessExclusiveLock',
                        virtualtransaction=f'{pid}/1', virtualxid=None),
                   dict(kind='transactionid', granted=False, xid=str(parent * 10),
                        mode='ShareLock', relation=None, page=None, item=None,
                        virtualtransaction=f'{pid}/1', virtualxid=None),
                   *identity_locks(pid, str(pid * 10))])

    def pair(self, backend='go'):
        first = self.row(backend=backend, tuple_row=(55, 3, 7) if backend == 'go' else (56, 4, 1))
        second = self.row(21, parent=20, backend=backend,
                          tuple_row=(57, 5, 1) if backend == 'go' else (56, 4, 1))
        if backend == 'go':
            second['query'] = probe.GO_OWNER_QUERY
        else:
            second['locks'] = [dict(second['locks'][0], granted=False), *second['locks'][2:]]
        return [first, second]

    def test_actual_dependency_shapes_synthetic_go_owner_and_django_delivery(self):
        for backend in ('go', 'django'):
            with self.subTest(backend=backend):
                proof = probe._overlap(self.pair(backend), self.blocker, backend)
                self.assertEqual(len(proof), 2)
                self.assertNotEqual(proof[0].xid, proof[1].xid)

    def test_django_fast_cascade_by_canonical_message_fk_and_exact_delivery_tuple(self):
        rows = self.pair('django')
        for row in rows:
            row['query'] = ('DELETE FROM "communications_messagetransaction" WHERE '
                            '"communications_messagetransaction"."message_id" IN (\'target\'::uuid)')
        self.assertEqual(len(probe._overlap(rows, self.blocker, 'django')), 2)
        rows[0]['query'] = rows[0]['query'].replace('target', 'foreign')
        with self.assertRaisesRegex(AssertionError, 'foreign-lock-sql'):
            probe._overlap(rows, self.blocker, 'django')

    def test_missing_second_is_incomplete_and_sequential_lost_wait_is_rejected(self):
        first, second = self.pair()
        observed = probe._overlap([first], self.blocker, 'go')
        self.assertEqual(len(observed), 1)
        self.assertEqual(probe._overlap([], self.blocker, 'go'), ())
        with self.assertRaisesRegex(AssertionError, 'lost-wait'):
            probe._overlap([second], self.blocker, 'go', previous=observed)

    def test_wrong_foreign_unknown_ambiguous_and_inactive_waits(self):
        for name in ('foreign-tuple', 'wrong-root-xid', 'unknown-sql', 'wrong-root',
                     'inactive', 'no-xid', 'no-backend', 'duplicate-xid', 'extra-blocker',
                     'no-tuple', 'wrong-actor-row', 'extra-waiter'):
            rows = self.pair()
            if name == 'foreign-tuple': rows[0]['locks'][0]['item'] = 99
            if name == 'wrong-root-xid': rows[0]['locks'][1]['xid'] = '999'
            if name == 'unknown-sql': rows[0]['query'] = 'SELECT 1'
            if name == 'wrong-root': rows[0]['blockers'] = [999]
            if name == 'inactive': rows[0]['state'] = 'idle in transaction'
            if name == 'no-xid': rows[0]['xid'] = None
            if name == 'no-backend': rows[0]['backend'] = None
            if name == 'duplicate-xid': rows[1]['xid'] = rows[0]['xid']
            if name == 'extra-blocker': rows[0]['blockers'].append(999)
            if name == 'no-tuple': rows[0]['locks'].pop(0)
            if name == 'wrong-actor-row': rows[1]['locks'][0]['item'] = 99
            if name == 'extra-waiter': rows.append(self.row(22))
            with self.subTest(name=name), self.assertRaises(AssertionError):
                probe._overlap(rows, self.blocker, 'go', previous=probe._overlap(self.pair(), self.blocker, 'go'))

    def test_transient_wait_or_reused_backend_transaction_rejected(self):
        rows = self.pair()
        proof = probe._overlap(rows, self.blocker, 'go')
        for field, value in (('xact', 'new'), ('backend', 'new'), ('xid', '999'), ('wait', None)):
            changed = copy.deepcopy(rows)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                probe._overlap(changed, self.blocker, 'go', previous=proof)

    def test_django_foreign_literal_and_unmatched_tuple_edge_rejected(self):
        rows = self.pair('django')
        rows[0]['query'] = rows[0]['query'].replace('delivery-one', 'foreign')
        with self.assertRaises(AssertionError): probe._overlap(rows, self.blocker, 'django')
        rows = self.pair('django')
        rows[1]['locks'][0]['item'] = 2
        with self.assertRaises(AssertionError): probe._overlap(rows, self.blocker, 'django')


# Exact safe template from parent's message-delete-corrected-sql-observation.json;
# private original SHA256 a3d32f67905bfaa1329e9e185fc77bfc8ee263ac5e9424c584b704d8273ffd72.
OBSERVED_DJANGO_SELECT = (
    'SELECT "communications_message"."message_id", "communications_message"."sport_association_id", '
    '"communications_message"."type", "communications_message"."message", '
    '"communications_message"."subject", "communications_message"."created_at" '
    'FROM "communications_message" WHERE ("communications_message"."message_id" = '
    "'<TARGET>'::uuid AND \"communications_message\".\"sport_association_id\" = "
    "'<ASSOCIATION>'::uuid) ORDER BY \"communications_message\".\"message_id\" ASC LIMIT 1 FOR UPDATE")


class CorrectedDjangoSqlControls(unittest.TestCase):
    def setUp(self):
        self.controls = LockControls()
        self.controls.setUp()
        ids = contract.load_source()['ids']
        self.target, self.association = ids['delete_email'], ids['own_association']
        self.blocker = probe.Blocker(**dict(vars(self.controls.blocker),
            target=self.target, association=self.association))
        self.query = OBSERVED_DJANGO_SELECT.replace('<TARGET>', self.target).replace(
            '<ASSOCIATION>', self.association)

    def pair(self):
        rows = self.controls.pair('django')
        for row in rows:
            row['query'] = self.query
            row['locks'][0].update(relation=55, page=3, item=7)
        return rows

    def test_observed_template_binds_exact_message_tuple_and_distinct_transactions(self):
        self.assertEqual(probe._sql_rows(self.query, self.blocker, 'django'), {(55, 3, 7)})
        rows = self.pair()
        proof = probe._overlap(rows, self.blocker, 'django')
        self.assertEqual([(p.pid, p.xid, p.vxid) for p in proof],
                         [(20, '200', '20/1'), (21, '210', '21/1')])
        self.assertEqual(probe._overlap(rows, self.blocker, 'django', previous=proof), proof)

    def test_foreign_missing_and_malformed_scope_rejected(self):
        for literal in (self.target, self.association):
            for replacement in ('a1000000-0000-4000-8000-000000999999', '', 'not-a-uuid',
                                literal[:-1], literal.upper(), literal + "' OR '1'='1"):
                with self.subTest(literal=literal, replacement=replacement), self.assertRaises(AssertionError):
                    probe._sql_rows(self.query.replace(literal, replacement), self.blocker, 'django')
        for predicate in (
            f'"communications_message"."message_id" = \'{self.target}\'::uuid AND ',
            f' AND "communications_message"."sport_association_id" = \'{self.association}\'::uuid',
        ):
            with self.subTest(predicate=predicate), self.assertRaises(AssertionError):
                probe._sql_rows(self.query.replace(predicate, ''), self.blocker, 'django')

    def test_unobserved_columns_table_predicates_order_limit_and_lock_options_rejected(self):
        queries = [
            self.query.replace('SELECT ', 'SELECT 1, ', 1),
            self.query.replace(', "communications_message"."created_at"', ''),
            self.query.replace('"type", "communications_message"."message"',
                               '"message", "communications_message"."type"'),
            self.query.replace('FROM "communications_message"', 'FROM "foreign_message"'),
            self.query.replace(') ORDER', ' AND true) ORDER'),
            self.query.replace(' = ', ' <> ', 1),
            self.query.replace('::uuid', ''),
            self.query.replace(' ASC', ' DESC'), self.query.replace(' ASC', ''),
            self.query.replace('ORDER BY "communications_message"."message_id" ASC ', ''),
            self.query.replace('ORDER BY "communications_message"."message_id"',
                               'ORDER BY "communications_message"."created_at"'),
            self.query.replace('LIMIT 1', 'LIMIT 2'), self.query.replace('LIMIT 1', 'LIMIT 01'),
            self.query.replace('LIMIT 1 ', ''), self.query.replace(' FOR UPDATE', ''),
            self.query.replace(' FOR UPDATE', ' FOR SHARE'),
            self.query + ' NOWAIT', self.query + ' SKIP LOCKED',
            self.query + ' /* observed */', self.query + ' -- observed',
            self.query.replace(' WHERE ', ' /* observed */ WHERE '),
            self.query.replace(' WHERE ', ' JOIN "bakney_user" ON true WHERE '),
            self.query + '; SELECT 1', self.query + '\n',
        ]
        # Same valid predicates in a different order are also outside the observation.
        target = f'"communications_message"."message_id" = \'{self.target}\'::uuid'
        association = f'"communications_message"."sport_association_id" = \'{self.association}\'::uuid'
        queries.append(self.query.replace(target + ' AND ' + association, association + ' AND ' + target))
        for query in queries:
            with self.subTest(query=query), self.assertRaises(AssertionError):
                probe._sql_rows(query, self.blocker, 'django')

    def test_message_tuple_witness_cannot_be_missing_delivery_foreign_or_extra(self):
        for mode in ('missing', 'delivery', 'foreign', 'extra'):
            rows = self.pair()
            if mode == 'missing': rows[0]['locks'].pop(0)
            if mode == 'delivery': rows[0]['locks'][0].update(relation=56, page=4, item=1)
            if mode == 'foreign': rows[0]['locks'][0]['item'] = 99
            if mode == 'extra': rows[0]['locks'].append(dict(rows[0]['locks'][0], item=99))
            with self.subTest(mode=mode), self.assertRaisesRegex(AssertionError, 'foreign-tuple'):
                probe._overlap(rows, self.blocker, 'django')

    def test_scoped_select_preserves_virtual_identity_lifetime_and_dependency_checks(self):
        rows = self.pair()
        rows[1]['xid'] = None
        rows[1]['locks'] = [lock for lock in rows[1]['locks'] if not
                            (lock['kind'] == 'transactionid' and lock['granted'])]
        proof = probe._overlap(rows, self.blocker, 'django')
        self.assertIsNone(proof[1].xid)
        self.assertEqual(proof[1].vxid, '21/1')
        for mode in ('backend', 'xact', 'vxid', 'edge'):
            changed = copy.deepcopy(rows)
            if mode in ('backend', 'xact'): changed[1][mode] = 'changed'
            if mode == 'vxid':
                for lock in changed[1]['locks']:
                    lock['virtualtransaction'] = '21/2'
                    if lock['kind'] == 'virtualxid': lock['virtualxid'] = '21/2'
            if mode == 'edge': changed[0]['locks'][1]['xid'] = '999'
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                probe._overlap(changed, self.blocker, 'django', previous=proof)

    def test_historical_message_collector_query_still_uses_exact_message_tuple(self):
        query = ('DELETE FROM "communications_message" WHERE '
                 f'"communications_message"."message_id" IN (\'{self.target}\'::uuid)')
        self.assertEqual(probe._sql_rows(query, self.blocker, 'django'), {(55, 3, 7)})
        with self.assertRaisesRegex(AssertionError, 'foreign-lock-sql'):
            probe._sql_rows(query.replace(self.target, self.association), self.blocker, 'django')

    def test_corrected_select_polling_still_withholds_full_acceptance(self):
        polling = synthetic_control()
        polling.backend = 'django'
        polling.cases = probe._cases(contract, polling.fixture, 'django')
        polling.blocker, polling.cursor = self.blocker, 0
        polling.threads = [Mock(is_alive=Mock(return_value=True)) for _ in range(2)]
        polling.responses = [dict(sent=True, complete=False, status=None) for _ in range(2)]
        polling.activity = Mock(return_value=[self.controls.root(), *self.pair()])
        polling.asgi.delta.return_value = [dict(seq=i, method='DELETE', path=case['path'],
            cid=case['name'], status=None, error_type=None) for i, case in enumerate(polling.cases)]
        with patch.object(probe.time, 'sleep'):
            polling.observe_overlap()
        self.assertEqual(polling.activity.call_count, 2)
        self.assertTrue(polling.catalog_overlap_proved)
        self.assertEqual(polling.catalog_waits, dict(validated=2, pre_xid=0, permanent_xid=2))
        completed, fixture = CancellationAndAcceptanceControls().completed_control()
        completed.catalog_overlap_proved = polling.catalog_overlap_proved
        completed.catalog_waits = polling.catalog_waits
        with self.assertRaises(probe.ConcurrencyFailure) as caught:
            CancellationAndAcceptanceControls().run_control(completed, fixture, {})
        result = caught.exception.diagnostics
        self.assertTrue(result['catalog_overlap_verified'])
        self.assertEqual(result['checks_failed'], ['request-transaction-attribution'])
        for field in ('request_transaction_attribution_verified', 'full_concurrency_acceptance',
                      'overlap_verified', 'success'):
            self.assertIs(result[field], False)


class VirtualIdentityControls(unittest.TestCase):
    def setUp(self):
        self.controls = LockControls()
        self.controls.setUp()
        self.rows = self.controls.pair()
        owner = self.rows[1]
        owner['xid'] = None
        owner['locks'] = [lock for lock in owner['locks'] if not
                          (lock['kind'] == 'transactionid' and lock['granted'])]

    def overlap(self, rows=None, previous=()):
        return probe._overlap(self.rows if rows is None else rows, self.controls.blocker,
                              'go', previous=previous)

    def test_active_pre_xid_owner_has_measured_virtual_identity(self):
        first, second = self.overlap()
        self.assertEqual((first.xid, first.vxid, second.xid, second.vxid),
                         ('200', '20/1', None, '21/1'))
        self.assertEqual((second.pid, second.xact, second.backend), (21, 'xact-21', 'backend-21'))
        self.assertEqual(self.overlap(previous=(first, second)), (first, second))

    def test_permanent_xid_assignment_preserves_identity_then_cannot_change_or_disappear(self):
        before = self.overlap()
        self.rows[1]['xid'] = '210'
        self.rows[1]['locks'].append(identity_locks(21, '210')[1])
        after = self.overlap(previous=before)
        self.assertEqual((after[1].vxid, after[1].xid), ('21/1', '210'))
        for xid in (None, '211'):
            changed = copy.deepcopy(self.rows)
            changed[1]['xid'] = xid
            changed[1]['locks'] = [lock for lock in changed[1]['locks'] if not
                                   (lock['kind'] == 'transactionid' and lock['granted'])]
            if xid is not None:
                changed[1]['locks'].append(identity_locks(21, xid)[1])
            with self.subTest(xid=xid), self.assertRaisesRegex(AssertionError, 'lost-wait'):
                self.overlap(changed, previous=after)

    def test_missing_ambiguous_conflicting_and_unowned_virtual_identity_fail(self):
        for mode in ('missing', 'duplicate', 'ungranted', 'share', 'different-target',
                     'different-owner', 'foreign-lock-owner', 'missing-lock-owner', 'wrong-kind'):
            rows = copy.deepcopy(self.rows)
            owner = rows[1]
            own = next(lock for lock in owner['locks'] if lock['kind'] == 'virtualxid')
            if mode == 'missing': owner['locks'].remove(own)
            if mode == 'duplicate': owner['locks'].append(copy.deepcopy(own))
            if mode == 'ungranted': own['granted'] = False
            if mode == 'share': own['mode'] = 'ShareLock'
            if mode == 'different-target': own['virtualxid'] = '99/1'
            if mode == 'different-owner': own['virtualtransaction'] = '99/1'
            if mode == 'foreign-lock-owner': owner['locks'][0]['virtualtransaction'] = '99/1'
            if mode == 'missing-lock-owner': owner['locks'][0]['virtualtransaction'] = None
            if mode == 'wrong-kind': own['kind'] = 'transactionid'
            with self.subTest(mode=mode), self.assertRaises(AssertionError): self.overlap(rows)

    def test_pre_xid_lifetime_pid_incarnation_start_and_virtual_id_reuse_fail(self):
        before = self.overlap()
        for mode in ('pid', 'backend', 'xact', 'virtual', 'disappeared', 'inactive',
                     'not-waiting', 'no-xact', 'no-backend'):
            rows = copy.deepcopy(self.rows)
            owner = rows[1]
            if mode == 'pid': owner['pid'] = 22
            if mode == 'backend': owner['backend'] = 'new-incarnation'
            if mode == 'xact': owner['xact'] = 'new-transaction'
            if mode == 'virtual':
                for lock in owner['locks']:
                    lock['virtualtransaction'] = '21/2'
                    if lock['kind'] == 'virtualxid': lock['virtualxid'] = '21/2'
            if mode == 'disappeared': rows.pop()
            if mode == 'inactive': owner['state'] = 'idle in transaction'
            if mode == 'not-waiting': owner['wait'] = None
            if mode == 'no-xact': owner['xact'] = None
            if mode == 'no-backend': owner['backend'] = None
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                self.overlap(rows, previous=before)

    def test_unique_virtual_and_permanent_ids_include_blocker(self):
        for mode in ('same-virtual', 'blocker-virtual', 'blocker-xid', 'same-xid', 'same-backend'):
            rows = copy.deepcopy(self.rows)
            if mode in ('same-virtual', 'blocker-virtual'):
                vxid = '20/1' if mode == 'same-virtual' else '10/1'
                for lock in rows[1]['locks']:
                    lock['virtualtransaction'] = vxid
                    if lock['kind'] == 'virtualxid': lock['virtualxid'] = vxid
            if mode in ('blocker-xid', 'same-xid'):
                xid = '100' if mode == 'blocker-xid' else '200'
                rows[1]['xid'] = xid
                rows[1]['locks'].append(identity_locks(21, xid)[1])
            if mode == 'same-backend': rows[1]['backend'] = rows[0]['backend']
            with self.subTest(mode=mode), self.assertRaises(AssertionError): self.overlap(rows)

    def test_permanent_catalog_xid_requires_exact_self_lock_and_virtual_identity(self):
        for mode in ('missing', 'different', 'duplicate', 'pre-xid-with-permanent', 'bad-xid'):
            rows = copy.deepcopy(self.rows)
            own = rows[0]['locks'][-1]
            if mode == 'missing': rows[0]['locks'].remove(own)
            if mode == 'different': own['xid'] = '999'
            if mode == 'duplicate': rows[0]['locks'].append(copy.deepcopy(own))
            if mode == 'pre-xid-with-permanent': rows[1]['locks'].append(identity_locks(21, '210')[1])
            if mode == 'bad-xid': rows[0]['xid'] = 'None'
            with self.subTest(mode=mode), self.assertRaises(AssertionError): self.overlap(rows)

    def test_two_pre_xid_transactions_still_require_distinct_measured_virtual_ids(self):
        rows = self.controls.pair('django')
        for row in rows:
            row['xid'] = None
            row['locks'] = [lock for lock in row['locks'] if not
                            (lock['kind'] == 'transactionid' and lock['granted'])]
        proof = probe._overlap(rows, self.controls.blocker, 'django')
        self.assertEqual([item.xid for item in proof], [None, None])
        self.assertEqual([item.vxid for item in proof], ['20/1', '21/1'])
        for lock in rows[1]['locks']:
            lock['virtualtransaction'] = '20/1'
            if lock['kind'] == 'virtualxid': lock['virtualxid'] = '20/1'
        with self.assertRaisesRegex(AssertionError, 'ambiguous-transactions'):
            probe._overlap(rows, self.controls.blocker, 'django')

    def test_virtual_identity_does_not_replace_exact_dependency_sql_or_tuple(self):
        before = self.overlap()
        for mode in ('wrong-root', 'foreign-tuple', 'unknown-sql', 'virtual-wait', 'lost-parent'):
            rows = copy.deepcopy(self.rows)
            if mode == 'wrong-root': rows[0]['locks'][1]['xid'] = '999'
            if mode == 'foreign-tuple': rows[1]['locks'][0]['item'] = 99
            if mode == 'unknown-sql': rows[1]['query'] += ' /* unknown */'
            if mode == 'virtual-wait':
                rows[1]['locks'][1].update(kind='virtualxid', xid=None, virtualxid='20/1')
            if mode == 'lost-parent': rows.pop(0)
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                self.overlap(rows, previous=before)


class ResponseControls(unittest.TestCase):
    def response(self, status=200):
        return dict(status=status, complete=True, body=b'{"msg":"message deleted."}' if status == 200
            else b'{"msg":"message not found."}', response_headers=[('Content-Type', 'application/json'),
            ('Content-Language', 'en'), ('Allow', 'DELETE, OPTIONS')])

    def test_both_orderings_and_exact_bodies(self):
        for statuses in ((200, 404), (404, 200)):
            self.assertEqual(probe._responses([self.response(s) for s in statuses]), statuses.index(200))

    def test_wrong_status_body_framing_or_headers(self):
        for name in ('duplicate-success', 'server-error', 'body', 'partial', 'cookie', 'language', 'allow'):
            pair = [self.response(), self.response(404)]
            if name == 'duplicate-success': pair[1] = self.response()
            if name == 'server-error': pair[1]['status'] = 500
            if name == 'body': pair[1]['body'] = b'{"msg":"message deleted."}'
            if name == 'partial': pair[1]['complete'] = False
            if name == 'cookie': pair[1]['response_headers'].append(('Set-Cookie', 'private'))
            if name == 'language': pair[1]['response_headers'][1] = ('Content-Language', 'it')
            if name == 'allow': pair[1]['response_headers'][2] = ('Allow', 'POST, OPTIONS')
            with self.subTest(name=name), self.assertRaises(AssertionError): probe._responses(pair)

    def test_ambiguous_duplicate_json_keys_are_rejected(self):
        pair = [self.response(), self.response(404)]
        pair[0]['body'] = b'{"msg":"wrong","msg":"message deleted."}'
        with self.assertRaises(AssertionError): probe._responses(pair)


class LockRepresentationControls(unittest.TestCase):
    """Ordinary synthetic JSON strings only; no psycopg typecaster invocation."""
    def activity(self, value):
        control = synthetic_control()
        control.monitor = MagicMock()
        control.monitor.cursor.return_value.__enter__.return_value.fetchall.return_value = [(
            20, '200', 'xact', 'backend', [10], 'active', 'Lock', probe.GO_MESSAGE_QUERY, value)]
        return control.activity()[0]['locks']

    def test_raw_django_jsonb_text_and_already_decoded_catalog_shape(self):
        controls = LockControls()
        controls.setUp()
        locks = controls.row()['locks']
        self.assertEqual(self.activity(json.dumps(locks)), locks)
        self.assertEqual(self.activity(locks), locks)
        self.assertEqual(self.activity('[]'), [])

    def test_malformed_ambiguous_or_unknown_lock_shapes_rejected(self):
        controls = LockControls()
        controls.setUp()
        valid = controls.row()['locks'][0]
        for value in ('null', '{}', '[', '[{"kind":"tuple","kind":"transactionid"}]',
                      json.dumps([dict(valid, granted='true')]), json.dumps([dict(valid, page=True)]),
                      json.dumps([dict(valid, item=0)]), json.dumps([dict(valid, kind='unknown')]),
                      json.dumps([dict(valid, relation=None)]), json.dumps([dict(valid, extra='unknown')]),
                      json.dumps([dict(valid, xid='100')]), json.dumps([dict(valid, mode='unknown')]),
                      json.dumps([None]), b'[]', [valid] * 65):
            with self.subTest(value=value), self.assertRaises((AssertionError, ValueError)):
                self.activity(value)

    def test_virtual_lock_projection_is_strict_and_keeps_original_driver_text(self):
        locks = identity_locks(21, None)
        self.assertEqual(self.activity(json.dumps(locks)), locks)
        self.assertEqual(self.activity(locks), locks)
        for field in ('virtualtransaction', 'virtualxid'):
            for value in (None, 21, True, '0/1', '21/0', '21/01', '01/1', '21', '21/1/2',
                          '21/-1', '4294967296/1', '21/4294967296', '21/1\n'):
                changed = [dict(locks[0], **{field: value})]
                with self.subTest(field=field, value=value), self.assertRaises(AssertionError):
                    self.activity(json.dumps(changed))
        for change in (dict(xid='210'), dict(relation=55), dict(page=1), dict(item=1),
                       dict(mode='AccessExclusiveLock')):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                self.activity([dict(locks[0], **change)])
        for field in ('virtualtransaction', 'virtualxid'):
            changed = copy.deepcopy(locks)
            del changed[0][field]
            with self.subTest(missing=field), self.assertRaises(AssertionError): self.activity(changed)


class BlockerAndAdmissionControls(unittest.TestCase):
    def scoped_control(self):
        control = synthetic_control()
        control.blocking = MagicMock()
        control.deliveries = ('owned-one', 'owned-two')
        cursor = control.blocking.cursor.return_value.__enter__.return_value
        ids = control.fixture['authored']['ids']
        message = (control.cases[0]['effect']['message_id'], ids['own_association'], 55, '(3,7)')
        cursor.fetchall.side_effect = [[message],
            [('owned-one', 56, '(4,1)'), ('owned-two', 56, '(4,2)')],
            [(ids['owner'], 57, '(5,1)')]]
        cursor.fetchone.return_value = (10, '100', 'blocker-backend', 'root-xact', '10/1')
        locks = LockControls()
        locks.setUp()
        control.activity = Mock(return_value=[locks.root()])
        return control, cursor, message

    def test_blocker_scope_is_read_from_exact_held_row_and_matches_canonical_fixture(self):
        control, cursor, message = self.scoped_control()
        control.hold()
        self.assertEqual(control.blocker.association, message[1])
        self.assertEqual(control.blocker.target, message[0])
        self.assertEqual(control.blocker.rows['message'], {(55, 3, 7)})
        self.assertEqual((control.blocker.xid, control.blocker.xact, control.blocker.vxid),
                         ('100', 'root-xact', '10/1'))
        sql, params = cursor.execute.call_args_list[0].args
        self.assertTrue(sql.startswith('SELECT message_id::text, sport_association_id::text, '))
        self.assertEqual(params, [message[0], message[1]])

    def test_held_row_with_foreign_missing_or_malformed_stored_scope_is_rejected(self):
        for scope in (contract.load_source()['ids']['foreign_association'], None, '', 'not-a-uuid'):
            control, cursor, message = self.scoped_control()
            cursor.fetchall.side_effect = [[(message[0], scope, *message[2:])]]
            with self.subTest(scope=scope), self.assertRaisesRegex(AssertionError, 'exact-target-blocker'):
                control.hold()
            self.assertIsNone(control.blocker)
            self.assertEqual(cursor.execute.call_count, 1)
            control.release()
            control.blocking.rollback.assert_called_once()
        control, cursor, message = self.scoped_control()
        cursor.fetchall.side_effect = [[(message[0], *message[2:])]]
        with self.assertRaisesRegex(AssertionError, 'exact-target-blocker'): control.hold()

    def test_actual_blocker_selection_rejects_wrong_missing_and_ambiguous_rows(self):
        control = synthetic_control()
        target = control.cases[0]['effect']['message_id']
        valid = (target, control.fixture['authored']['ids']['own_association'], 55, '(3,7)')
        for messages in ([], [('FOREIGN', valid[1], 55, '(3,7)')], [valid, valid]):
            control = synthetic_control()
            control.blocking = MagicMock()
            cursor = control.blocking.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = messages
            with self.subTest(messages=messages), self.assertRaisesRegex(AssertionError, 'exact-target-blocker'):
                control.hold()
            control.release()
            control.blocking.rollback.assert_called_once()
        control = synthetic_control()
        control.blocking = MagicMock()
        control.deliveries = ('owned-one', 'owned-two')
        cursor = control.blocking.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[valid], [('foreign-child', 56, '(1,1)')]]
        with self.assertRaisesRegex(AssertionError, 'exact-delivery-blockers'): control.hold()
        control.release()
        control.blocking.rollback.assert_called_once()

    def test_missing_ambiguous_and_wrong_actual_blocker_rejected(self):
        blocker = LockControls()
        blocker.setUp()
        root = blocker.root()
        probe._root([root], blocker.blocker)
        for rows in ([], [root, root], [{**root, 'xid': '999'}], [{**root, 'backend': 'restarted'}],
                     [{**root, 'xact': 'later-transaction'}], [{**root, 'state': 'idle'}],
                     [{**root, 'locks': []}], [{**root, 'locks': identity_locks(11, '100')}]):
            with self.subTest(rows=rows), self.assertRaises(AssertionError):
                probe._root(rows, blocker.blocker)

    def test_canonical_fixture_cannot_be_jointly_rewritten(self):
        fixture = synthetic_fixture()
        self.assertEqual(len(probe._cases(contract, fixture, 'go')), 2)
        for source in ('authored', 'runtime'):
            next(case for case in fixture[source]['cases'] if case['name'] ==
                 'message-mutations-delete-email')['effect']['delivery_count'] = 0
        fixture['runtime']['source_sha256'] = contract.source_digest(fixture['authored'])
        with self.assertRaisesRegex(AssertionError, 'runtime.digest'):
            probe._cases(contract, fixture, 'go')

    def test_canonical_target_and_association_cannot_be_rebound_in_both_manifests(self):
        for identifier in ('delete_email', 'own_association'):
            fixture = synthetic_fixture()
            original = fixture['authored']['ids'][identifier]
            foreign = fixture['authored']['ids']['foreign_association' if identifier ==
                       'own_association' else 'foreign_email']
            for document in ('authored', 'runtime'):
                fixture[document] = json.loads(json.dumps(fixture[document]).replace(original, foreign))
            fixture['runtime']['source_sha256'] = contract.source_digest(fixture['authored'])
            with self.subTest(identifier=identifier), self.assertRaisesRegex(AssertionError, 'runtime.digest'):
                probe._cases(contract, fixture, 'django')

    def test_exact_attribution_and_reused_extra_or_wrong_version_correlations(self):
        control = synthetic_control()
        cases = control.cases
        responses = [dict(status=200), dict(status=404)]
        events = [dict(seq=i, method='DELETE', path=case['path'], cid=case['name'],
                       status=responses[i]['status'], error_type=None) for i, case in enumerate(cases)]
        probe._window('django', events, cases, responses)
        probe._window('go', [], cases, responses)
        for change in ('duplicate-cid', 'extra', 'wrong-path', 'wrong-method', 'wrong-status', 'wrong-version'):
            changed = copy.deepcopy(events)
            if change == 'duplicate-cid': changed[1]['cid'] = changed[0]['cid']
            if change == 'extra': changed.append(copy.deepcopy(events[0]))
            if change == 'wrong-path': changed[0]['path'] += '/extra'
            if change == 'wrong-method': changed[0]['method'] = 'POST'
            if change == 'wrong-status': changed[0]['status'] = 500
            if change == 'wrong-version': changed[0]['cid'] = probe._correlations('django')[0]
            with self.subTest(change=change), self.assertRaises(AssertionError):
                probe._window('django', changed, cases, responses)
        with self.assertRaises(AssertionError): probe._window('go', events, cases, responses)

    def test_outstanding_threads_and_observed_transactions_both_required(self):
        control = synthetic_control()
        control.threads = [Mock(is_alive=Mock(return_value=True)) for _ in range(2)]
        control.responses = [dict(sent=True, status=None, complete=False) for _ in range(2)]
        control.outstanding()
        control.threads[1].is_alive.return_value = False
        with self.assertRaises(AssertionError): control.outstanding()
        control.threads[1].is_alive.return_value = True
        control.responses[1]['status'] = 200
        with self.assertRaises(AssertionError): control.outstanding()


class OriginalWireControls(unittest.TestCase):
    """Memory streams only; socket creation is always patched in this class."""
    @staticmethod
    def wire(body=b'{"msg":"message deleted."}'):
        return (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Language: en\r\n'
                b'Allow: DELETE, OPTIONS\r\nContent-Length: ' + str(len(body)).encode() +
                b'\r\nConnection: close\r\n\r\n' + body)

    class SyntheticSocket:
        def __init__(self, wire, *, failure=None):
            self.buffer, self.failure = io.BytesIO(wire), failure
            self.closed = False
        def getpeername(self): return ('127.0.0.1', 8100)
        def getsockname(self): return ('127.0.0.2', 12345)
        def settimeout(self, value): pass
        def sendall(self, payload):
            if self.failure == 'send': raise OSError('PRIVATE partial request')
            self.sent = payload
        def recv_into(self, buffer):
            if self.failure == 'read': raise TimeoutError('PRIVATE read')
            data = self.buffer.read(len(buffer))
            buffer[:len(data)] = data
            return len(data)
        def recv(self, size): return self.buffer.read(size)
        def close(self): self.closed = True

    def request(self, wire, failure=None):
        control = synthetic_control()
        stream = self.SyntheticSocket(wire, failure=failure)
        with patch.object(probe.socket, 'create_connection', return_value=stream) as connect:
            control._request(0, control.cases[0])
        connect.assert_called_once()
        self.assertTrue(stream.closed)
        self.assertIsNone(control.clients[0])
        return control.responses[0], stream

    def test_complete_original_wire_preserved_and_actual_request_is_delete(self):
        wire = self.wire()
        record, stream = self.request(wire)
        self.assertTrue(record['complete'])
        self.assertEqual(record['wire'], wire)
        self.assertEqual(record['status'], 200)
        self.assertEqual(stream.sent.count(b'X-Correlation-ID:'), 1)
        self.assertTrue(stream.sent.startswith(b'DELETE /communications/messages/'))
        self.assertNotIn(b'Forwarded', stream.sent)

    def test_separately_arriving_tail_is_rejected_and_consumed_byte_retained(self):
        frame = self.wire()
        stream = self.SyntheticSocket(frame + b'J')
        original_read = stream.recv_into
        # First read delivers exactly the complete frame; the extra byte only
        # arrives at the subsequent raw-socket EOF check, beyond HTTP buffering.
        stream.recv_into = lambda buffer: original_read(memoryview(buffer)[:len(frame)])
        control = synthetic_control()
        with patch.object(probe.socket, 'create_connection', return_value=stream):
            control._request(0, control.cases[0])
        record = control.responses[0]
        self.assertFalse(record['complete'])
        self.assertTrue(record['transport_failed'])
        self.assertEqual(record['wire'], frame + b'J')
        self.assertTrue(stream.closed)

    def test_short_conflicting_duplicate_and_trailing_frames_rejected(self):
        wire = self.wire()
        variants = [wire[:-1], wire + b'JUNK',
            wire.replace(b'Content-Length:', b'Transfer-Encoding: chunked\r\nContent-Length:'),
            wire.replace(b'Content-Length:', b'Content-Length: 25\r\nContent-Length:')]
        for value in variants:
            with self.subTest(value=value):
                record, _ = self.request(value)
                self.assertFalse(record['complete'])
                self.assertTrue(record['transport_failed'])
                self.assertEqual(record['status'], 200)
                self.assertEqual(record['wire'], value)

    def test_chunked_and_close_delimited_complete_frames(self):
        body = b'{"msg":"message deleted."}'
        chunked = self.wire(body).split(b'Content-Length:')[0] + b'Transfer-Encoding: chunked\r\n\r\n' + (
            f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n')
        closed = b'HTTP/1.0 200 OK\r\n\r\n' + body
        for wire in (chunked, closed):
            self.assertTrue(self.request(wire)[0]['complete'])
        for tail in (b'0\r\n', b'0\r\nX: trailer\r\n', b'0\r\n\r\nJUNK'):
            self.assertFalse(self.request(chunked.rsplit(b'0\r\n\r\n', 1)[0] + tail)[0]['complete'])

    def test_connect_read_and_partial_send_failures_close_owned_clients(self):
        for phase in ('send', 'read'):
            record, _ = self.request(self.wire(), failure=phase)
            self.assertFalse(record['complete'])
            self.assertTrue(record['transport_failed'])
            self.assertNotIn('PRIVATE', repr(record))
        control = synthetic_control()
        with patch.object(probe.socket, 'create_connection', side_effect=TimeoutError('PRIVATE')):
            control._request(0, control.cases[0])
        self.assertTrue(control.responses[0]['transport_failed'])
        self.assertIsNone(control.clients[0])


class ExactEffectControls(unittest.TestCase):
    def states(self):
        fixture = synthetic_fixture()
        case = probe._cases(contract, fixture, 'go')[0]
        target, owner = case['effect']['message_id'], case['audit_actor']
        own, foreign = fixture['authored']['ids']['own_association'], fixture['authored']['ids']['foreign_association']
        before = {f'foreign.Model{i}': [dict(id=1, value='preserve')] for i in range(122)}
        message = dict(message_id=target, sport_association_id=own, type='EMAIL', message='Saved',
                       subject='Subject', created_at='2026-10-26T10:00:00Z')
        before.update({contract.MESSAGE: [message, dict(message, message_id=fixture['authored']['ids']['foreign_email'],
            sport_association_id=foreign)], contract.DELIVERY: [dict(message_transaction_id=f'delivery-{i}',
            message_id=target if i < 2 else 'foreign-message') for i in range(3)],
            contract.AUDIT: [], contract.INDEX: [],
            'contenttypes.ContentType': [dict(id=30, app_label='communications', model='message')],
            'application.User': [dict(user_id=owner, email='actor@example.test')]})
        changes = {('sport_association' if field == 'sport_association_id' else field):
            [value if field != 'created_at' else '2026-10-26 10:00:00', 'None'] for field, value in message.items()}
        event = dict(id=3, content_type_id=30, object_pk=target, object_id=None,
            object_repr=f'Message object ({target})', serialized_data=None, action=2, changes_text='',
            changes=changes, actor_id=owner, actor_email='actor@example.test', cid=case['name'],
            remote_addr='127.0.0.2', remote_port=None, timestamp='2026-10-26T10:00:00Z', additional_data=None)
        index = dict(audit_log_index_id='00000000-0000-4000-8000-000000000003', log_entry_id=3,
                     sport_association_id=own, resolution_path='changes_json.sport_association', created_at=event['timestamp'])
        for number in (1, 2):
            before[contract.AUDIT].append(dict(event, id=number, action=number - 1, cid='historical'))
            before[contract.INDEX].append(dict(index, audit_log_index_id=f'00000000-0000-4000-8000-{number:012d}',
                                               log_entry_id=number))
        after = copy.deepcopy(before)
        after[contract.MESSAGE] = after[contract.MESSAGE][1:]
        after[contract.DELIVERY] = after[contract.DELIVERY][2:]
        after[contract.AUDIT].append(event)
        after[contract.INDEX].append(index)
        contrasts = cache.HistoricalReadCacheContrasts(cache.PERSISTENT_CONTRAST_KEY,
            cache.EXPIRING_CONTRAST_KEY, 2000000000000, b'persistent', b'expiring')
        entries = {contrasts.persistent_key: cache.HistoricalReadCacheEntry(b'string', b'persistent', -1),
            contrasts.expiring_key: cache.HistoricalReadCacheEntry(b'string', b'expiring', contrasts.expires_at_ms),
            b'foreign': cache.HistoricalReadCacheEntry(b'string', b'foreign', -1)}
        prior = cache.HistoricalReadCacheSnapshot(entries)
        return fixture, before, after, prior, contrasts

    def verify(self, fixture, before, after, prior, contrasts, current=None, responses=None):
        portable = probe.PortableAssertions(None, contract.assert_effect, None, shared.assert_state_unchanged,
            shared.assert_cache_unchanged, None, None)
        response = ResponseControls().response
        return probe._verify(portable, probe._cases(contract, fixture, 'go'),
            responses or [response(), response(404)], (before, prior), (after, current or prior), contrasts,
            started=1793008800, finished=1793008801, client_ip='127.0.0.2')

    def test_exact_single_delete_full_128_models_raw_db1_and_sentinels(self):
        args = self.states()
        self.assertEqual(self.verify(*args), [])
        fixture, before, _, prior, _ = args
        self.assertEqual(len(probe._fresh(contract, probe._cases(contract, fixture, 'go')[0],
            (before, prior), fixture['authored']['ids'])), 2)

    def test_duplicate_audit_index_wrong_actor_attribution_and_foreign_state(self):
        fixture, before, after, prior, contrasts = self.states()
        for change in ('duplicate-audit', 'duplicate-index', 'wrong-actor', 'wrong-cid', 'wrong-ip',
                       'foreign-state', 'foreign-delivery', 'history', 'retained-target', 'partial-delivery', 'models'):
            current = copy.deepcopy(after)
            if change == 'duplicate-audit': current[contract.AUDIT].append(dict(current[contract.AUDIT][-1], id=4))
            if change == 'duplicate-index': current[contract.INDEX].append(dict(current[contract.INDEX][-1],
                audit_log_index_id='00000000-0000-4000-8000-000000000004'))
            if change == 'wrong-actor': current[contract.AUDIT][-1]['actor_id'] = 'wrong'
            if change == 'wrong-cid': current[contract.AUDIT][-1]['cid'] = probe._correlations('django')[0]
            if change == 'wrong-ip': current[contract.AUDIT][-1]['remote_addr'] = '127.0.0.1'
            if change == 'foreign-state': current['foreign.Model0'][0]['value'] = 'changed'
            if change == 'foreign-delivery': current[contract.DELIVERY].clear()
            if change == 'history': current[contract.AUDIT].pop(0)
            if change == 'retained-target': current[contract.MESSAGE].insert(0, before[contract.MESSAGE][0])
            if change == 'partial-delivery': current[contract.DELIVERY].insert(0, before[contract.DELIVERY][0])
            if change == 'models': del current['foreign.Model0']
            with self.subTest(change=change):
                self.assertIn('effects', self.verify(fixture, before, current, prior, contrasts))

    def test_changed_cache_type_value_expiry_missing_or_extra_key(self):
        args = self.states()
        for entry in (None, cache.HistoricalReadCacheEntry(b'hash', b'foreign', -1),
                      cache.HistoricalReadCacheEntry(b'string', b'changed', -1),
                      cache.HistoricalReadCacheEntry(b'string', b'foreign', 2000000000001)):
            entries = dict(args[3].entries)
            if entry is None: del entries[b'foreign']
            else: entries[b'foreign'] = entry
            self.assertIn('effects', self.verify(*args, current=cache.HistoricalReadCacheSnapshot(entries)))
        self.assertIn('effects', self.verify(*args, current=cache.HistoricalReadCacheSnapshot(
            {**args[3].entries, b'extra': cache.HistoricalReadCacheEntry(b'string', b'extra', -1)})))

    def test_duplicate_success_statuses_and_audits_preserved_for_safe_summary(self):
        args = self.states()
        response = ResponseControls().response
        self.assertEqual(self.verify(*args, responses=[response(), response()]), ['http'])
        args[2][contract.AUDIT].append(dict(args[2][contract.AUDIT][-1], id=4))
        self.assertEqual(self.verify(*args, responses=[response(), response()]), ['http', 'effects'])
        counts = probe._summary(contract, probe._cases(contract, args[0], 'go')[0],
                                (args[1], args[3]), (args[2], args[3]))
        self.assertEqual(counts['audit_delta'], 2)
        self.assertEqual(counts['index_delta'], 1)


class CleanupControls(unittest.TestCase):
    def test_copied_connections_have_finite_io_and_exact_actual_server_identity(self):
        for wrong_server in (False, True):
            control = synthetic_control()
            control.database.settings_dict = dict(NAME='assozeta_contract_synthetic', USER='synthetic')
            control.database.connection.info = SimpleNamespace(host='/SYNTHETIC', port=5432)
            connection = MagicMock()
            connection.settings_dict = dict(OPTIONS=dict(connect_timeout=99))
            connection.connection.info = SimpleNamespace(host='/FOREIGN' if wrong_server else '/SYNTHETIC', port=5432)
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                'assozeta_contract_synthetic', 'synthetic')
            control.database.copy.return_value = connection
            if wrong_server:
                with self.assertRaisesRegex(AssertionError, 'server-identity'):
                    control._connection('synthetic')
                connection.close.assert_called_once()
            else:
                self.assertIs(control._connection('synthetic'), connection)
                self.assertEqual(connection.settings_dict['OPTIONS']['connect_timeout'], 2)
                configured = connection.cursor.return_value.__enter__.return_value.execute.call_args_list[0]
                self.assertEqual(configured.args[1][:2], ['1000', '1000'])

    def control(self):
        control = synthetic_control()
        control.monitor = Mock(connection=None)
        control.blocking = Mock(connection=None)
        control.released = False
        control.activity = Mock(return_value=[])
        control.websockets = (0, 0)
        control.cursor = 0
        return control

    def test_owned_blocker_release_clients_threads_connections_and_guard(self):
        control = self.control()
        receipt = control.cleanup()
        self.assertTrue(receipt['reset_safe'])
        self.assertTrue(receipt['cleanup_complete'])
        control.blocking.rollback.assert_called_once()
        control.blocking.close.assert_called_once()
        control.monitor.close.assert_called_once()
        self.assertEqual(receipt['server_owner'], 'parent')

    def test_partial_unobserved_send_cannot_claim_reset_safety_on_idle_catalog(self):
        control = self.control()
        control.responses[0]['attempted_send'] = True
        receipt = control.cleanup()
        self.assertFalse(receipt['reset_safe'])
        self.assertFalse(receipt['admission_accounted'])
        control = self.control()
        control.responses[0]['attempted_send'] = True
        control.catalog_overlap_proved = True
        self.assertFalse(control.cleanup()['reset_safe'])

    def test_cleanup_failure_attempts_all_owned_resources_and_withholds_reset(self):
        for phase in ('rollback', 'thread', 'asgi', 'database', 'guard', 'close'):
            control = self.control()
            if phase == 'rollback': control.blocking.rollback.side_effect = OSError('PRIVATE')
            if phase == 'thread': control.threads = [Mock(is_alive=Mock(return_value=True))]
            if phase == 'asgi': control.asgi.wait_idle.side_effect = TimeoutError('PRIVATE')
            if phase == 'database': control.idle = Mock(side_effect=TimeoutError('PRIVATE'))
            if phase == 'guard': control.process_guard = lambda: dict(running=False)
            if phase == 'close': control.monitor.close.side_effect = OSError('PRIVATE')
            with self.subTest(phase=phase):
                receipt = control.cleanup()
                self.assertFalse(receipt['reset_safe'])
                self.assertNotIn('PRIVATE', repr(receipt))
                control.blocking.close.assert_called_once()
                control.monitor.close.assert_called_once()

    def test_bounds_reject_unbounded_and_overlapping_deadlines(self):
        for change in (dict(query=0), dict(read=float('inf')), dict(connect=100),
                       dict(read=3), dict(cleanup=2), dict(poll=1), dict(connect=.5)):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                probe.Bounds(**change).validate()


class PollingControls(unittest.TestCase):
    def control(self):
        controls = LockControls()
        controls.setUp()
        control = synthetic_control()
        control.blocker = controls.blocker
        control.released = False
        control.cursor = 0
        control.threads = [Mock(is_alive=Mock(return_value=True)) for _ in range(2)]
        control.responses = [dict(sent=True, complete=False, status=None) for _ in range(2)]
        root = controls.root()
        return control, controls, root

    def test_two_validated_observations_precede_release(self):
        control, locks, root = self.control()
        rows = [root, *locks.pair()]
        control.activity = Mock(return_value=rows)
        with patch.object(probe.time, 'sleep'):
            control.observe_overlap()
        self.assertTrue(control.catalog_overlap_proved)
        self.assertFalse(control.released)
        self.assertEqual(control.activity.call_count, 2)

    def test_sequential_transient_and_missing_second_never_count_as_overlap(self):
        for mode in ('sequential', 'transient', 'missing-second'):
            control, locks, root = self.control()
            first, second = locks.pair()
            sequential = locks.row(21)
            observations = ([[root, first], [root, sequential]] if mode == 'sequential' else
                [[root, first, second], [root, first]] if mode == 'transient' else [[root, first]] * 20)
            control.activity = Mock(side_effect=observations)
            now = [0]
            control.end = 30
            with self.subTest(mode=mode), \
                 patch.object(probe.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(probe.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + 1)), \
                 self.assertRaisesRegex(AssertionError, 'lost-wait|deadline'):
                control.observe_overlap()
            self.assertFalse(control.catalog_overlap_proved)

    def test_no_complete_response_or_guard_drift_allowed_while_blocked(self):
        control, locks, root = self.control()
        control.activity = Mock(return_value=[root, *locks.pair()])
        control.responses[1]['complete'] = True
        with self.assertRaises(AssertionError): control.observe_overlap()
        control, _, _ = self.control()
        control.process_guard = lambda: dict(running=True, pid=12, instance='DIFFERENT')
        with self.assertRaisesRegex(AssertionError, 'guard-changed'): control.observe_overlap()

    def test_pre_xid_pair_counts_are_safe_and_inactive_has_separate_failure(self):
        control, locks, root = self.control()
        virtual = VirtualIdentityControls()
        virtual.setUp()
        control.activity = Mock(return_value=[root, *virtual.rows])
        with patch.object(probe.time, 'sleep'):
            control.observe_overlap()
        self.assertEqual(control.catalog_waits, dict(validated=2, pre_xid=1, permanent_xid=1))
        self.assertIsNone(control.catalog_wait_failure)
        control, locks, root = self.control()
        rows = locks.pair()
        rows[1]['state'] = 'idle in transaction'
        control.activity = Mock(return_value=[root, *rows])
        with self.assertRaisesRegex(AssertionError, 'inactive-wait'):
            control.observe_overlap()
        self.assertEqual(control.catalog_wait_failure, 'inactive-wait')
        self.assertFalse(control.catalog_overlap_proved)


class PublicFailureControls(unittest.TestCase):
    def test_import_is_inert(self):
        name = 'SYNTHETIC_concurrency_import'
        spec = importlib.util.spec_from_file_location(name, Path(probe.__file__))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {name: module}), \
             patch.object(probe.socket, 'create_connection') as connect, \
             patch.object(threading.Thread, 'start') as start:
            spec.loader.exec_module(module)
        connect.assert_not_called()
        start.assert_not_called()

    def test_preflight_failure_is_fixed_and_never_opens_http(self):
        with patch.object(probe, '_inputs', side_effect=ValueError('PRIVATE token SQL')), \
             patch.object(probe.socket, 'create_connection') as connect:
            with self.assertRaises(probe.ConcurrencyFailure) as caught:
                probe.run(backend='go', apps=None, database=None, raw=None, fixture=None,
                    base_url='ignored', process_guard=None, observer=None, client_ip='ignored', private_evidence={})
        connect.assert_not_called()
        self.assertEqual(str(caught.exception), 'message-concurrency-failed')
        self.assertNotIn('PRIVATE', repr(caught.exception.diagnostics))

        self.assertTrue(caught.exception.diagnostics['cleanup']['reset_safe'])

    def test_missing_inputs_fail_before_django_or_services(self):
        with patch.object(probe.socket, 'create_connection') as connect:
            with self.assertRaises(probe.ConcurrencyFailure) as caught:
                probe.run(backend='unknown', apps=None, database=None, raw=None, fixture=None,
                    base_url='ignored', process_guard=lambda: {}, observer=None, client_ip='ignored', private_evidence={})
        self.assertEqual(caught.exception.diagnostics['stage'], 'inputs')
        connect.assert_not_called()

    def test_runtime_failure_preserves_statuses_counts_and_cleanup(self):
        fixture, before, after, prior, contrasts = ExactEffectControls().states()
        control = synthetic_control()
        control.before, control.after = (before, prior), (after, prior)
        control.private['started'] = 1793008800
        control.portable = probe.PortableAssertions(None, contract.assert_effect, None,
            shared.assert_state_unchanged, shared.assert_cache_unchanged, None, None)
        response = ResponseControls().response
        control.responses = [response(), response()]
        control.setup = Mock()
        control.hold = Mock()
        control.open_requests = Mock()
        control.observe_overlap = Mock(side_effect=RuntimeError('PRIVATE SQL token'))
        cleanup = dict(cleanup_complete=True, reset_safe=True, fixture_reset_owner='parent')
        control.cleanup = Mock(return_value=cleanup)
        fixture['contrasts'] = contrasts
        with patch.object(probe, '_inputs', return_value=(control.portable, contract, control.guard)), \
             patch.object(probe, '_Control', return_value=control), \
             patch.object(probe.time, 'time', return_value=1793008800):
            with self.assertRaises(probe.ConcurrencyFailure) as caught:
                probe.run(backend='go', apps=None, database=None, raw=None, fixture=fixture,
                    base_url='ignored', process_guard=lambda: {}, observer=None, client_ip='127.0.0.2', private_evidence={})
        control.cleanup.assert_called_once()
        self.assertEqual(caught.exception.diagnostics['statuses'], [200, 200])
        self.assertEqual(caught.exception.diagnostics['counts']['audit_delta'], 1)
        self.assertEqual(caught.exception.diagnostics['checks_failed'], ['http', 'request-transaction-attribution'])
        self.assertNotIn('PRIVATE', repr(caught.exception.diagnostics))


class CancellationAndAcceptanceControls(unittest.TestCase):
    class ExecutionInterrupted(BaseException):
        """Synthetic control exception, never an actual harness cancellation."""

    def run_control(self, control, fixture, private):
        with patch.object(probe, '_inputs', return_value=(control.portable, contract, control.guard)), \
             patch.object(probe, '_Control', return_value=control), \
             patch.object(probe.time, 'time', return_value=1793008800):
            return probe.run(backend='go', apps=None, database=None, raw=None, fixture=fixture,
                base_url='ignored', process_guard=lambda: {}, observer=None,
                client_ip='127.0.0.2', private_evidence=private)

    def completed_control(self):
        fixture, before, after, prior, contrasts = ExactEffectControls().states()
        control = synthetic_control()
        control.before, control.after = (before, prior), (after, prior)
        control.portable = probe.PortableAssertions(None, contract.assert_effect, None,
            shared.assert_state_unchanged, shared.assert_cache_unchanged, None, None)
        response = ResponseControls().response
        control.responses = [response(), response(404)]
        for operation in ('setup', 'hold', 'open_requests', 'observe_overlap', 'finish'):
            setattr(control, operation, Mock())
        control.catalog_overlap_proved = True
        control.cleanup = Mock(return_value=dict(cleanup_complete=True, reset_safe=True))
        fixture['contrasts'] = contrasts
        return control, fixture

    def test_unbound_catalog_pair_and_perfect_effects_never_grant_full_acceptance(self):
        polling, locks, root = PollingControls().control()
        # This exact Go owner wait also belongs to other mutations. Catalogs
        # cannot distinguish that negative control from the intended DELETE.
        polling.activity = Mock(return_value=[root, *locks.pair()])
        with patch.object(probe.time, 'sleep'):
            polling.observe_overlap()
        control, fixture = self.completed_control()
        control.catalog_overlap_proved = polling.catalog_overlap_proved
        private = {}
        with self.assertRaises(probe.ConcurrencyFailure) as caught:
            self.run_control(control, fixture, private)
        result = caught.exception.diagnostics
        self.assertTrue(result['catalog_overlap_verified'])
        self.assertFalse(result['request_transaction_attribution_verified'])
        self.assertFalse(result['overlap_verified'])
        self.assertFalse(result['full_concurrency_acceptance'])
        self.assertFalse(result['success'])
        self.assertEqual(result['checks_failed'], ['request-transaction-attribution'])
        self.assertEqual(result['statuses'], [200, 404])
        self.assertEqual(result['counts']['audit_delta'], 1)
        self.assertEqual(private['diagnostics'], result)

    def test_pre_xid_catalog_pair_public_diagnostics_withhold_request_attribution(self):
        polling, _, root = PollingControls().control()
        virtual = VirtualIdentityControls()
        virtual.setUp()
        polling.activity = Mock(return_value=[root, *virtual.rows])
        with patch.object(probe.time, 'sleep'):
            polling.observe_overlap()
        control, fixture = self.completed_control()
        control.catalog_waits = polling.catalog_waits
        control.catalog_wait_failure = polling.catalog_wait_failure
        private = {}
        with self.assertRaises(probe.ConcurrencyFailure) as caught:
            self.run_control(control, fixture, private)
        result = caught.exception.diagnostics
        self.assertEqual(result['catalog_waits'], dict(validated=2, pre_xid=1, permanent_xid=1))
        self.assertIsNone(result['catalog_wait_failure'])
        self.assertTrue(result['catalog_overlap_verified'])
        for key in ('success', 'request_transaction_attribution_verified',
                    'full_concurrency_acceptance', 'overlap_verified'):
            self.assertIs(result[key], False)
        self.assertIn('request-transaction-attribution', result['checks_failed'])
        for private_value in ('21/1', 'backend-21', 'xact-21', probe.GO_OWNER_QUERY):
            self.assertNotIn(private_value, repr(result))

    def test_cancellation_identity_survives_each_stage_cleanup_and_final_verification(self):
        for kind in (self.ExecutionInterrupted, KeyboardInterrupt, SystemExit):
            for stage in ('inputs', 'setup', 'observe_overlap', 'cleanup', 'verification', 'summary', 'window'):
                with self.subTest(kind=kind.__name__, stage=stage):
                    control, fixture = self.completed_control()
                    private = {}
                    interruption = kind('PRIVATE cancellation token SQL')
                    def invoke():
                        if stage == 'inputs':
                            with patch.object(probe, '_inputs', side_effect=interruption):
                                return probe.run(backend='go', apps=None, database=None, raw=None,
                                    fixture=None, base_url='ignored', process_guard=None, observer=None,
                                    client_ip='ignored', private_evidence=private)
                        if stage in ('setup', 'observe_overlap', 'cleanup'):
                            getattr(control, stage).side_effect = interruption
                        target = {'verification': '_verify', 'summary': '_summary', 'window': '_window'}.get(stage)
                        if stage == 'window': control.cursor = 0
                        if target:
                            with patch.object(probe, target, side_effect=interruption):
                                return self.run_control(control, fixture, private)
                        return self.run_control(control, fixture, private)
                    with self.assertRaises(kind) as caught:
                        invoke()
                    self.assertIs(caught.exception, interruption)
                    if stage != 'inputs': control.cleanup.assert_called_once()
                    diagnostics = private['diagnostics']
                    self.assertIs(interruption.diagnostics, diagnostics)
                    self.assertFalse(diagnostics['success'])
                    self.assertNotIn('PRIVATE', repr(diagnostics))
                    if stage == 'cleanup': self.assertFalse(diagnostics['cleanup']['reset_safe'])

    def test_cleanup_control_exceptions_attempt_remaining_resources_then_reraise(self):
        for kind in (self.ExecutionInterrupted, KeyboardInterrupt, SystemExit):
            for stage in ('rollback', 'asgi', 'close', 'guard'):
                with self.subTest(kind=kind.__name__, stage=stage):
                    control = CleanupControls().control()
                    interruption = kind('PRIVATE cleanup')
                    target = {'rollback': control.blocking.rollback, 'asgi': control.asgi.wait_idle,
                              'close': control.blocking.close, 'guard': control.process_guard}
                    if stage == 'guard': control.process_guard = Mock(side_effect=interruption)
                    else: target[stage].side_effect = interruption
                    with self.assertRaises(kind) as caught:
                        control.cleanup()
                    self.assertIs(caught.exception, interruption)
                    control.blocking.close.assert_called_once()
                    control.monitor.close.assert_called_once()
                    self.assertFalse(control.private['cleanup']['reset_safe'])
                    self.assertNotIn('PRIVATE', repr(control.private['cleanup']))

    def test_catalog_waits_do_not_account_for_unattributed_pending_admission(self):
        control = CleanupControls().control()
        control.catalog_overlap_proved = True
        control.responses[0]['attempted_send'] = True
        receipt = control.cleanup()
        self.assertFalse(receipt['admission_accounted'])
        self.assertFalse(receipt['reset_safe'])

    def test_worker_cancellation_transfers_exact_identity_before_later_cleanup_interrupt(self):
        control, fixture = self.completed_control()
        interruption = self.ExecutionInterrupted('PRIVATE worker')
        # No real thread/socket: exercise the worker's actual exception path.
        with patch.object(probe.socket, 'create_connection', side_effect=interruption):
            control._request(0, control.cases[0])
        self.assertIs(control.interruption, interruption)
        self.assertTrue(control.responses[0]['transport_failed'])
        self.assertNotIn('PRIVATE', repr(control.responses))
        control.cleanup.side_effect = KeyboardInterrupt('PRIVATE later cleanup')
        private = {}
        with self.assertRaises(self.ExecutionInterrupted) as caught:
            self.run_control(control, fixture, private)
        self.assertIs(caught.exception, interruption)
        self.assertFalse(private['diagnostics']['cleanup']['reset_safe'])

    def test_connection_close_does_not_replace_original_control_exception(self):
        control = synthetic_control()
        connection = MagicMock()
        control.database.copy.return_value = connection
        interruption = self.ExecutionInterrupted('PRIVATE original')
        connection.ensure_connection.side_effect = interruption
        connection.close.side_effect = SystemExit('PRIVATE later close')
        with self.assertRaises(self.ExecutionInterrupted) as caught:
            control._connection('synthetic')
        self.assertIs(caught.exception, interruption)
        self.assertEqual(control.unclosed_connections, [connection])

    def test_worker_close_interrupt_transfers_and_withholds_reset_safety(self):
        control = synthetic_control()
        stream = OriginalWireControls.SyntheticSocket(OriginalWireControls.wire())
        interruption = SystemExit('PRIVATE close')
        stream.close = Mock(side_effect=interruption)
        with patch.object(probe.socket, 'create_connection', return_value=stream):
            control._request(0, control.cases[0])
        self.assertIs(control.interruption, interruption)
        self.assertTrue(control.responses[0]['close_failed'])
        self.assertIsNone(control.clients[0])
        self.assertNotIn('PRIVATE', repr(control.responses))
        self.assertFalse(control.cleanup()['reset_safe'])
        with self.assertRaises(SystemExit) as caught:
            control.check_interruption()
        self.assertIs(caught.exception, interruption)


class RedisRetryControls(unittest.TestCase):
    def test_installed_retry_and_constructor_default_are_zero_without_socket(self):
        from redis.backoff import NoBackoff
        from redis.connection import Connection
        from redis.retry import Retry
        with patch('socket.socket') as socket_open:
            for retry in (None, Retry(NoBackoff(), 0)):
                options = dict(retry=retry, retry_on_timeout=False, retry_on_error=[])
                probe._nonretrying_redis(options)
                connection = Connection(**options)
                self.assertIs(type(connection.retry), Retry)
                self.assertIs(type(connection.retry._retries), int)
                self.assertEqual(connection.retry._retries, 0)
                self.assertIsNone(connection._sock)
                probe._nonretrying_redis(dict(options, retry=connection.retry))
        socket_open.assert_not_called()

    def test_unknown_infinite_positive_or_coerced_retry_policies_fail_closed(self):
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        policies = [Retry(NoBackoff(), value) for value in (-1, 1, True, 0.0, '0', None)]
        policies += [SimpleNamespace(_retries=0), SimpleNamespace(get_retries=lambda: 0)]
        for policy in policies:
            with self.subTest(policy=type(policy).__name__), self.assertRaises(AssertionError):
                probe._nonretrying_redis(dict(retry=policy))
        for change in (dict(retry_on_timeout=True), dict(retry_on_error=[TimeoutError])):
            with self.assertRaises(AssertionError): probe._nonretrying_redis(change)


class ConnectionAttributionIntegrationControls(unittest.TestCase):
    def owned_control(self):
        control = synthetic_control()
        control.backend = 'django'
        from TESTS.test_message_connection_attribution import unassigned_workload
        snapshots = unassigned_workload()
        control.connection_observation = Mock(return_value=snapshots[0])
        handles = []
        for role, pid in zip(('default', 'blocker', 'monitor'), (11, 12, 13)):
            native = SimpleNamespace(info=SimpleNamespace(backend_pid=pid))
            wrapper = MagicMock(connection=native)
            wrapper.cursor.return_value.__enter__.return_value.fetchone.return_value = (pid, f'owned-{pid}')
            control._measure_owned(role, wrapper)
            handles.append(wrapper)
        return control, handles, snapshots

    def test_owned_identity_comes_from_native_handle_and_self_catalog(self):
        control, handles, snapshots = self.owned_control()
        for role, handle in zip(('default', 'blocker', 'monitor'), handles):
            control._measure_owned(role, handle)
            record = control.private['connection_owners'][role]
            self.assertEqual(record['native_pid'], handle.connection.info.backend_pid)
            self.assertEqual(len(record['catalogs']), 2)
            query = handle.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
            self.assertIn('pg_backend_pid()', query)
            self.assertIn('backend_start', query)
        control.start_connection_window()
        control.connection_observation.return_value = snapshots[3]
        control._seal_owned(('default', 'blocker', 'monitor'))
        self.assertEqual({r['end_cursor'] for r in control.private['connection_owners'].values()},
                         {len(snapshots[3]['events'])})

    def test_replaced_native_catalog_pid_and_incarnation_reject(self):
        for change in ('native', 'pid', 'catalog', 'recycled', 'ambiguous'):
            control, handles, _ = self.owned_control()
            handle = handles[0]
            if change == 'native':
                handle.connection = SimpleNamespace(info=SimpleNamespace(backend_pid=11))
            if change == 'pid': handle.connection.info.backend_pid = 99
            if change == 'catalog':
                handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (99, 'owned-11')
            if change == 'recycled':
                handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (11, 'new-start')
            if change == 'ambiguous':
                handles[1].connection.info.backend_pid = 11
                handle = handles[1]
            with self.subTest(change=change), self.assertRaises(AssertionError):
                control._measure_owned('blocker' if change == 'ambiguous' else 'default', handle)

    def test_snapshot_validates_ownership_without_changing_callback_envelope(self):
        control, _, snapshots = self.owned_control()
        control.start_connection_window()
        bad = copy.deepcopy(snapshots[2])
        for event in bad['events']:
            if event.get('backend_pid') == 13: event['backend_pid'] = 99
        control.connection_observation.return_value = bad
        with self.assertRaises(probe.attribution.AttributionError): control.connection_snapshot()
        self.assertTrue(control.connection_failed)

    def test_owned_measurement_control_cancellation_is_not_swallowed(self):
        control, handles, _ = self.owned_control()
        interruption = KeyboardInterrupt('PRIVATE')
        handles[0].cursor.return_value.__enter__.return_value.execute.side_effect = interruption
        with self.assertRaises(KeyboardInterrupt) as caught:
            control._measure_owned('default', handles[0])
        self.assertIs(caught.exception, interruption)

    def test_cleanup_records_terminal_catalog_before_close_and_seals_all_owned_sql(self):
        from dataclasses import replace
        control, handles, snapshots = self.owned_control()
        control.database, control.blocking, control.monitor = handles
        control.start_connection_window()
        control.connection_observation.return_value = snapshots[3]
        control.cursor, control.websockets = 0, (0, 0)
        lock = LockControls()
        lock.setUp()
        control.blocker = replace(lock.blocker, pid=12, backend='owned-12')
        root = dict(lock.root(), pid=12, backend='owned-12', state='idle', xid=None, xact=None,
                    locks=[], locks_raw=[], blockers=[])
        def catalog():
            rows = [copy.deepcopy(root)]
            control.private['activity'].append(rows)
            return rows
        control.activity = Mock(side_effect=catalog)
        order = []
        for role, handle in zip(('blocker', 'monitor'), handles[1:]):
            def close(h=handle, role=role):
                order.append(role)
                self.assertIsNotNone(control.private['connection_owners'][role]['end_cursor'])
                h.connection = None
            handle.close.side_effect = close
        control.original_timeout = '0'
        receipt = control.cleanup()
        self.assertTrue(receipt['reset_safe'], receipt)
        self.assertEqual(order, ['blocker', 'monitor'])
        terminal = control.private['terminal_observation']
        self.assertEqual(terminal['activity_cursor'], len(control.private['activity']))
        self.assertEqual(terminal['asgi'], [])
        default_queries = [call.args[0] for call in
                           handles[0].cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertIn('backend_start', default_queries[-2])
        self.assertIn("set_config('statement_timeout'", default_queries[-1])
        for record in control.private['connection_owners'].values():
            self.assertEqual(len(record['catalogs']), 2)
            self.assertEqual(record['end_cursor'], len(snapshots[3]['events']))

    def test_catalog_reads_are_bracketed_before_release_and_samples_retained(self):
        from TESTS.test_message_connection_attribution import Stream
        control, locks, root = PollingControls().control()
        stream = Stream('go').add('request_begin', 1).add('request_finish', 1)
        baseline = stream.snapshot()
        stream.add('request_begin', 2).add('request_begin', 3)
        stream.add('query_start', 2, 1, 20).add('query_start', 3, 2, 21)
        blocked = stream.snapshot()
        order = []
        snapshots = iter([baseline] + [blocked] * 4)
        def observation():
            order.append('stream')
            return next(snapshots)
        def catalog():
            order.append('catalog')
            rows = [root, *locks.pair()]
            control.private['activity'].append(rows)
            return rows
        control.connection_observation = observation
        control.activity = Mock(side_effect=catalog)
        control.start_connection_window()
        with patch.object(probe.time, 'sleep'):
            control.observe_overlap()
        self.assertEqual(order, ['stream', 'stream', 'catalog', 'stream', 'stream', 'catalog', 'stream'])
        self.assertFalse(control.released)
        self.assertEqual([s['activity_index'] for s in control.connection_samples], [0, 1])
        self.assertEqual(len(control.connection_prefix.active_queries), 2)
        # Snapshot copies cannot be rewritten through the parent's reused object.
        blocked['events'][0]['stream'] = 'rewritten'
        self.assertEqual(control.connection_prefix.events[0]['stream'], 'query_connection')

    def run_control(self, control, fixture, private):
        with patch.object(probe, '_inputs', return_value=(control.portable, contract, control.guard)), \
             patch.object(probe, '_Control', return_value=control), \
             patch.object(probe.time, 'time', return_value=1793008800):
            return probe.run(backend='go', apps=None, database=None, raw=None, fixture=fixture,
                base_url='ignored', process_guard=lambda: {}, observer=None,
                client_ip='127.0.0.2', private_evidence=private,
                connection_observation=control.connection_observation)

    def test_no_footer_is_provisional_and_finalization_is_separate(self):
        from TESTS.test_message_connection_attribution import FinalizationControls
        private, final = FinalizationControls().private()
        control, fixture = CancellationAndAcceptanceControls().completed_control()
        control.private = private
        control.before, control.after = private['before'], private['after']
        control.responses = private['responses']
        control.blocker = private['blocker']
        evidence = private['connection_attribution']
        captured = evidence['captured']
        # Simulate the lifecycle operations, while using the actual parser and
        # final verifier against synthetic catalog, wire and effect witnesses.
        control.start_connection_window = Mock()
        control.connection_samples = evidence['samples']
        control.connection_prefix = probe.attribution.parse_observation(captured)
        control.connection_baseline = probe.attribution.prefix(control.connection_prefix, evidence['baseline_cursor'])
        control.connection_observation = Mock(return_value=captured)
        control.cleanup = Mock(return_value=private['cleanup'])
        fixture.update(baseline_state=private['before'][0], baseline_cache=private['before'][1])
        from types import MappingProxyType
        fixture['baseline_cache'] = cache.HistoricalReadCacheSnapshot(
            MappingProxyType(dict(fixture['baseline_cache'].entries)))
        # The actual fixture also owns model instances/classes and paths, which
        # must never be copied into this pure finalization projection.
        class NotEvidence:
            def __deepcopy__(self, memo):
                raise AssertionError('unrelated fixture ownership copied')
        fixture['created'] = NotEvidence()
        # Match the exact timestamps used by the existing effect fixture.
        private['started'] = 1793008800
        with self.assertRaises(probe.ConcurrencyFailure) as caught:
            self.run_control(control, fixture, private)
        result = caught.exception.diagnostics
        self.assertTrue(result['request_transaction_attribution_verified'], result)
        self.assertFalse(result['full_concurrency_acceptance'])
        self.assertEqual(result['pending_reason'], probe.ATTRIBUTION_PENDING)
        self.assertEqual(result['checks_failed'], [probe.ATTRIBUTION_PENDING])
        self.assertFalse(result['attribution_finalized'])
        self.assertTrue(probe.finalize_attribution(private, final)['full_concurrency_acceptance'])
        self.assertFalse(private['diagnostics']['full_concurrency_acceptance'])

    def test_callback_exception_and_original_cancellation_always_cleanup(self):
        import asyncio
        from TESTS.test_message_connection_attribution import Stream
        for failure in (RuntimeError('PRIVATE callback detail'), KeyboardInterrupt('PRIVATE control'),
                        asyncio.CancelledError('PRIVATE task cancellation')):
            for stage in ('baseline', 'sample', 'post'):
                with self.subTest(failure=type(failure).__name__, stage=stage):
                    control, fixture = CancellationAndAcceptanceControls().completed_control()
                    private = control.private
                    baseline = Stream('go').snapshot()
                    if stage == 'baseline':
                        control.connection_observation = Mock(side_effect=failure)
                    elif stage == 'sample':
                        control.connection_observation = Mock(side_effect=[baseline, failure])
                        control.observe_overlap = control.connection_snapshot
                    else:
                        control.connection_observation = Mock(side_effect=[baseline, failure])
                    if isinstance(failure, Exception):
                        expected = probe.ConcurrencyFailure
                    else:
                        expected = type(failure)
                    with self.assertRaises(expected) as caught:
                        self.run_control(control, fixture, private)
                    if not isinstance(failure, Exception):
                        self.assertIs(caught.exception, failure)
                    result = caught.exception.diagnostics
                    self.assertFalse(result['request_transaction_attribution_verified'])
                    self.assertFalse(result['full_concurrency_acceptance'])
                    self.assertNotIn('PRIVATE', repr(result))
                    control.cleanup.assert_called_once()

    def test_malformed_callback_and_cross_snapshot_rewrite_are_sticky(self):
        from TESTS.test_message_connection_attribution import Stream
        for malformed in ({'success': True}, Stream('django').snapshot(),
                          dict(Stream('go').snapshot(), valid=True)):
            control = synthetic_control()
            control.connection_observation = Mock(return_value=malformed)
            with self.assertRaises((AssertionError, probe.attribution.AttributionError)):
                control.start_connection_window()
            self.assertTrue(control.connection_failed)
            self.assertFalse(control.catalog_overlap_proved)
        control = synthetic_control()
        first = Stream('go').snapshot()
        control.connection_observation = Mock(return_value=first)
        control.start_connection_window()
        first['events'][0]['complete'] = True
        with self.assertRaises(probe.attribution.AttributionError):
            control.connection_snapshot()
        control.connection_observation.return_value = Stream('go').snapshot()
        with self.assertRaises(AssertionError):
            control.connection_snapshot()


if __name__ == '__main__':
    unittest.main()
