"""Real PostgreSQL DELETE overlap regression; parent runs RED before view patch.

The controller's row lock is released only after pg_stat_activity and
pg_blocking_pids prove that BOTH actual requests are waiting on its lock chain.
No request transaction, model deletion, cascade or audit signal is mocked.
"""
from copy import deepcopy
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from auditlog.context import disable_auditlog, set_actor
from auditlog.models import LogEntry
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from application.models.audit_models import AuditLogIndex
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from communications.models import Message, MessageTransaction


@override_settings(AUDITLOG_CID_HEADER='X-Correlation-ID', AUDITLOG_CID_GETTER=None)
class MessageDeleteConcurrencyTests(TransactionTestCase):
    ready_seconds = 8
    overlap_seconds = 8
    join_seconds = 40

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.fail('DELETE concurrency requires actual PostgreSQL, not a skipped SQLite test')
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.in_atomic_block)
        self.workers = []
        with disable_auditlog():
            self.owner = create_test_user(role=User.ASSOCIATION)
            self.association = create_test_sport_association(user=self.owner)
            self.target = Message.objects.create(sport_association=self.association,
                                                 type=Message.EMAIL, message='Concurrent target')
            self.deliveries = [MessageTransaction.objects.create(
                message=self.target, recipient=f'target-{index}@example.test') for index in range(2)]
            self.foreign = Message.objects.create(sport_association=create_test_sport_association(),
                                                  type=Message.EMAIL, message='Foreign sentinel')
            MessageTransaction.objects.create(message=self.foreign, recipient='foreign@example.test')
        # Preserve a real unrelated historical event/index as well as a live message.
        with set_actor(self.owner):
            self.history = Message.objects.create(sport_association=self.association,
                                                  type=Message.INSIDE_APP, message='History sentinel')
        MessageTransaction.objects.create(message=self.history, recipient='history@example.test')
        event = LogEntry.objects.get(object_pk=str(self.history.pk), action=LogEntry.Action.CREATE)
        self.assertTrue(AuditLogIndex.objects.filter(log_entry=event).exists())

    def _fixture_teardown(self):
        # Never flush/reset fixtures while an unfinished request could mutate them.
        active = [worker.name for worker in getattr(self, 'workers', ()) if worker.is_alive()]
        if active:
            raise RuntimeError(f'Refusing fixture reset with active DELETE workers: {active}')
        super()._fixture_teardown()

    def snapshot(self):
        return {model: list(model._base_manager.order_by('pk').values()) for model in
                (Message, MessageTransaction, LogEntry, AuditLogIndex, User, SportAssociation)}

    def _wait_for_overlap(self, pids, controller_pid):
        deadline = monotonic() + self.overlap_seconds
        pause = Event()
        last = {}
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                # The controller is in a transaction; discard its statistics snapshot.
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute('SELECT pid, state, wait_event_type, xact_start IS NOT NULL, '
                               'query, pg_blocking_pids(pid) FROM pg_stat_activity '
                               'WHERE datname = current_database() AND pid = ANY(%s)',
                               [list(pids) + [controller_pid]])
                last = {row[0]: row[1:] for row in cursor.fetchall()}

            def reaches_controller(pid, visited):
                if pid == controller_pid:
                    return True
                if pid in visited or pid not in last:
                    return False
                return any(reaches_controller(blocker, visited | {pid}) for blocker in last[pid][4])

            if all(pid in last and last[pid][0:3] == ('active', 'Lock', True)
                   and any(table in last[pid][3] for table in
                           ('"communications_message"', '"communications_messagetransaction"'))
                   and reaches_controller(pid, set()) for pid in pids):
                return last
            # Polling cadence is not overlap evidence; the catalog predicate above is.
            pause.wait(0.02)
        self.fail(f'Both DELETE requests did not reach the held row-lock chain: {last}')

    def test_same_message_concurrent_delete_has_one_success_and_one_audit(self):
        before = self.snapshot()
        # DRF constructs Allow from a set, so its order can vary by process.
        # Capture an actual absent response before contention as the header baseline.
        baseline_client = APIClient()
        baseline_client.force_authenticate(user=self.owner)
        baseline = baseline_client.delete(f'/communications/messages/{uuid4()}/delete',
                                          HTTP_X_CORRELATION_ID=f'delete-absent-{uuid4().hex}')
        self.assertEqual((baseline.status_code, baseline.content),
                         (404, b'{"msg":"message not found."}'))
        self.assertEqual(set(baseline['Allow'].split(', ')), {'DELETE', 'OPTIONS'})
        target_id = self.target.pk
        ready, outcomes = Queue(), Queue()
        stop = Event()
        cids = [f'delete-concurrency-{uuid4().hex}' for _ in range(2)]
        pids = {}

        def request(cid):
            db = connections['default']
            selections = []
            response, error = None, None
            try:
                # Only this thread's wrapper changes. Bound native connects and queries,
                # including cleanup failures; never depend on an unbounded driver wait.
                db.settings_dict = deepcopy(db.settings_dict)
                db.settings_dict['CONN_MAX_AGE'] = None
                options = db.settings_dict.setdefault('OPTIONS', {})
                options['connect_timeout'] = 5
                options['options'] = (options.get('options', '') +
                    ' -c statement_timeout=35000 -c lock_timeout=30000'
                    ' -c idle_in_transaction_session_timeout=60000')
                with db.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid(), current_setting(\'transaction_isolation\')')
                    pid, isolation = cursor.fetchone()
                if isolation != 'read committed' or not db.get_autocommit() or db.in_atomic_block:
                    raise AssertionError('Request requires READ COMMITTED and no test outer transaction')
                ready.put((cid, pid))

                def observe(execute, sql, params, many, context):
                    if stop.is_set():
                        raise RuntimeError('DELETE test cleanup forbids further request SQL')
                    native = context['connection'].connection
                    actual_pid = native.info.backend_pid
                    if actual_pid != pid:
                        raise AssertionError('Request reconnected outside its measured backend identity')
                    if sql.startswith('SELECT') and 'FROM "communications_message"' in sql:
                        selections.append((sql, db.in_atomic_block))
                    return execute(sql, params, many, context)

                client = APIClient()
                client.raise_request_exception = False
                # A fresh ORM instance per connection; normal middleware/permissions
                # still resolve actor/association. No set_actor around the request.
                actor = User.objects.get(pk=self.owner.pk)
                client.force_authenticate(user=actor)
                with db.execute_wrapper(observe):
                    response = client.delete(f'/communications/messages/{target_id}/delete',
                                             HTTP_X_CORRELATION_ID=cid)
                if not db.get_autocommit() or db.in_atomic_block:
                    raise AssertionError('Request did not close its mutation transaction')
            except BaseException as failure:
                error = repr(failure)
            finally:
                try:
                    connections.close_all()
                except BaseException as failure:
                    error = f'{error}; connection cleanup: {failure!r}'
                outcomes.put((cid, response, selections, error))

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '3s'")
                    cursor.execute("SET LOCAL lock_timeout = '3s'")
                    cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    controller_pid = cursor.fetchone()[0]
                Message.objects.select_for_update().get(pk=target_id)
                for cid in cids:
                    worker = Thread(target=request, args=(cid,), name=cid, daemon=True)
                    self.workers.append(worker)
                    worker.start()
                deadline = monotonic() + self.ready_seconds
                for _ in cids:
                    try:
                        cid, pid = ready.get(timeout=max(0.01, deadline - monotonic()))
                    except Empty:
                        self.fail('DELETE connection setup exceeded its finite readiness deadline')
                    pids[cid] = pid
                self.assertEqual(len(set(pids.values()) | {controller_pid}), 3)
                overlap = self._wait_for_overlap(pids.values(), controller_pid)
            # Leaving atomic releases the real row lock, allowing the requests to finish.
        finally:
            # Lock release (including rollback on assertion) precedes independent joins.
            deadline = monotonic() + self.join_seconds
            for worker in self.workers:
                worker.join(timeout=max(0, deadline - monotonic()))
            stop.set()
            cleanup_errors = []
            try:
                for worker in self.workers:
                    if worker.is_alive() and worker.name in pids:
                        try:
                            with transaction.atomic(), connection.cursor() as cursor:
                                cursor.execute("SET LOCAL statement_timeout = '3s'")
                                cursor.execute('SELECT pg_terminate_backend(%s)', [pids[worker.name]])
                        except Exception as failure:
                            cleanup_errors.append(repr(failure))
            finally:
                # Join even when backend termination fails; teardown refuses a live worker.
                deadline = monotonic() + 6
                for worker in self.workers:
                    worker.join(timeout=max(0, deadline - monotonic()))
        self.assertFalse(any(worker.is_alive() for worker in self.workers), 'DELETE workers did not drain')
        self.assertEqual(cleanup_errors, [], 'Request backend cleanup failed')
        results = [outcomes.get_nowait() for _ in cids]
        self.assertEqual({result[0] for result in results}, set(cids))
        self.assertEqual([result[3] for result in results], [None, None], results)

        after = self.snapshot()
        old_logs = {row['id'] for row in before[LogEntry]}
        old_indexes = {row['audit_log_index_id'] for row in before[AuditLogIndex]}
        new_logs = [row for row in after[LogEntry] if row['id'] not in old_logs]
        new_indexes = [row for row in after[AuditLogIndex] if row['audit_log_index_id'] not in old_indexes]
        actual = (sorted(result[1].status_code for result in results), len(new_logs), len(new_indexes))
        self.assertEqual(actual, ([200, 404], 1, 1),
                         f'One physical deletion needs one success/audit/index; overlap={overlap}')
        winner = next(cid for cid, response, _, _ in results if response.status_code == 200)
        event, index = new_logs[0], new_indexes[0]
        self.assertEqual((event['object_pk'], event['action'], event['actor_id'], event['cid']),
                         (str(target_id), LogEntry.Action.DELETE, self.owner.pk, winner))
        self.assertEqual((index['log_entry_id'], index['sport_association_id'], index['resolution_path']),
                         (event['id'], self.association.pk, 'changes_json.sport_association'))

        for cid, response, selections, _ in results:
            self.assertTrue(selections, f'{cid}: no actual message selection observed')
            self.assertTrue(selections[0][1], f'{cid}: selection was outside the mutation transaction')
            self.assertIn('FOR UPDATE', selections[0][0])
            body = b'{"msg":"message deleted."}' if response.status_code == 200 else b'{"msg":"message not found."}'
            self.assertEqual(response.content, body)
            self.assertEqual(response['Content-Type'], 'application/json')
            self.assertEqual(response['Content-Type'], baseline['Content-Type'])
            self.assertEqual(response['Allow'], baseline['Allow'])
            self.assertEqual(response['Content-Length'], str(len(body)))

        # Exact existing rows/all fields: target and its two children are the only
        # removals; a single event/index are the only additions in these six models.
        expected = deepcopy(before)
        expected[Message] = [row for row in before[Message] if row['message_id'] != target_id]
        delivery_ids = {delivery.pk for delivery in self.deliveries}
        expected[MessageTransaction] = [row for row in before[MessageTransaction]
                                        if row['message_transaction_id'] not in delivery_ids]
        expected[LogEntry] = sorted(before[LogEntry] + new_logs, key=lambda row: row['id'])
        expected[AuditLogIndex] = sorted(before[AuditLogIndex] + new_indexes,
                                        key=lambda row: row['audit_log_index_id'])
        self.assertEqual(after, expected)
