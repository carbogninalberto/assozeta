"""Live payment-category lifecycle races in the oracle's private database.

All barriers are observed PostgreSQL lock waits. The fixture restores only its
owned rows and checks the caller's complete managed-model snapshot afterward.
Failures remain explicit evidence; this helper never changes the Django/Go
contract to make a failed lifecycle invariant pass.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import socket
import time
from urllib.parse import urlsplit

from TESTS.http_client import Client


ASSOCIATION_FIELDS = (
    'denomination', 'tax_code', 'address', 'address_cap', 'address_city',
    'enable_quotes_management', 'configuration', 'federation', 'enroll_number',
    'sport', 'president_first_name', 'president_last_name', 'stripe_available_methods',
    'invoice_template', 'subscription_template', 'extra_text_invoices', 'checkout_info',
)


def run(base, backend, *, owner_id, association_id, token, snapshot, evidence_path):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.db import connection, transaction
    from application.models import User, SportAssociation, PaymentCategory
    from application.models.audit_models import AuditLogIndex

    owner_id, association_id = str(owner_id), str(association_id)
    prefix = 'payment-category-lifecycle-' + backend.lower()
    client = Client(base, token, timeout=25)
    evidence = {
        'backend': backend, 'cases': [], 'success': False,
        'disconnect_semantics_match': False,
        'complete_payment_category_parity': False,
        'remaining_parity_gap': 'Go cancels a blocked mutation on client disconnect; admitted Django sync requests continue and may commit.',
    }

    def save_evidence():
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2) + '\n')
        evidence_path.chmod(0o600)

    def backend_pid():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            return cursor.fetchone()[0]

    def activity():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute("""SELECT pid, pg_blocking_pids(pid), state, wait_event_type,
                xact_start IS NOT NULL FROM pg_stat_activity
                WHERE datname=current_database() AND pid <> pg_backend_pid()""")
            return [dict(zip(('pid', 'blockers', 'state', 'wait', 'transaction'), row)) for row in cursor.fetchall()]

    def wait_chain(blocker, minimum):
        deadline = time.monotonic() + 10
        latest = []
        while time.monotonic() < deadline:
            latest = activity()
            chain = {blocker}
            for _ in latest:
                chain.update(row['pid'] for row in latest if row['wait'] == 'Lock' and any(pid in chain for pid in row['blockers']))
            waiting = sorted(chain - {blocker})
            if len(waiting) >= minimum:
                return waiting
            time.sleep(.01)
        raise AssertionError(f'expected {minimum} blocked requests behind {blocker}: {latest}')

    def transaction_ended(pid, timeout):
        deadline = time.monotonic() + timeout
        while True:
            rows = [row for row in activity() if row['pid'] == pid]
            if not rows or all(not row['transaction'] and row['state'] != 'active' and row['wait'] != 'Lock' for row in rows):
                return True, rows
            if time.monotonic() >= deadline:
                return False, rows
            time.sleep(.01)

    @contextmanager
    def fixture(label, number):
        original = snapshot()
        original_user = User.original_objects.filter(pk=owner_id).values('first_name', 'is_active', 'deleted').get()
        original_association = SportAssociation.original_objects.filter(pk=association_id).values('denomination', 'checkout_info').get()
        category_id = f'00000000-0000-4000-8000-{196000 + number:012d}'
        cid = prefix + '-' + label
        assert not PaymentCategory._base_manager.filter(pk=category_id).exists(), 'lifecycle category fixture collision'
        assert not LogEntry.objects.filter(cid__startswith=cid).exists(), 'lifecycle audit fixture collision'
        with disable_auditlog():
            PaymentCategory.objects.create(payment_category_id=category_id, sport_association_id=association_id,
                                           name='Lifecycle original')
        try:
            yield category_id, cid, original_user, original_association
        finally:
            with disable_auditlog():
                User.original_objects.filter(pk=owner_id).update(**original_user)
                SportAssociation.original_objects.filter(pk=association_id).update(**original_association)
                LogEntry.objects.filter(cid__startswith=cid).delete()
                PaymentCategory._base_manager.filter(pk=category_id).delete()
            assert snapshot() == original, label + ': lifecycle cleanup altered unrelated managed state'

    def audit_rows(cid):
        return list(LogEntry.objects.filter(cid__startswith=cid).order_by('id'))

    def assert_audit(events, expected):
        actual = [(event.cid, event.content_type.model, event.object_pk, event.changes_dict) for event in events]
        assert sorted(actual, key=lambda row: row[0:3]) == sorted(expected, key=lambda row: row[0:3]), (actual, expected)
        for event in events:
            assert str(event.actor_id) == owner_id, ('wrong actor', event.cid, event.actor_id)
            assert event.action == 1, ('wrong action', event.cid, event.action)
            assert event.additional_data is None, ('unexpected impersonation', event.cid)
            indexes = list(AuditLogIndex.objects.filter(log_entry=event))
            assert len(indexes) == 1 and str(indexes[0].sport_association_id) == association_id, ('audit index scope', event.cid)

    def overlap(profile_first, number, category_method='PATCH'):
        label = 'profile-first' if profile_first else 'category-first'
        if category_method == 'DELETE':
            label += '-delete'
        with fixture(label, number) as (category_id, cid, old_user, old_association):
            association_data = {field: None for field in ASSOCIATION_FIELDS}
            association_data.update(denomination='Lifecycle association', checkout_info=old_association['checkout_info'])
            profile_body = {'user_data': {'first_name': 'Lifecycle owner', 'last_name': None,
                                         'username': None, 'avatar_image': None, 'sport_association': association_data}}
            profile = ('PATCH', '/profile/update', profile_body, {'X-Correlation-ID': cid + '-profile'})
            category = (category_method, f'/payment/category/{category_id}/{"delete" if category_method == "DELETE" else "update"}',
                        None if category_method == 'DELETE' else {'name': 'Lifecycle category'},
                        {'X-Correlation-ID': cid + '-category'})
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    if profile_first:
                        SportAssociation.original_objects.select_for_update().get(pk=association_id)
                    else:
                        PaymentCategory._base_manager.select_for_update().get(pk=category_id)
                    blocker = backend_pid()
                    first = pool.submit(client.request, *(profile if profile_first else category))
                    first_waiter = wait_chain(blocker, 1)
                    second = pool.submit(client.request, *(category if profile_first else profile))
                    both_waiters = wait_chain(blocker, 2)
                results = [first.result(timeout=20), second.result(timeout=20)]
            assert [status for status, _ in results] == [200, 200], results
            assert User.original_objects.get(pk=owner_id).first_name == 'Lifecycle owner'
            assert SportAssociation.original_objects.get(pk=association_id).denomination == 'Lifecycle association'
            category_row = PaymentCategory._base_manager.get(pk=category_id)
            if category_method == 'DELETE':
                assert category_row.deleted and category_row.name == 'Lifecycle original'
                category_changes = {'deleted': ['False', 'True']}
            else:
                assert category_row.name == 'Lifecycle category' and not category_row.deleted
                category_changes = {'name': ['Lifecycle original', 'Lifecycle category']}
            events = audit_rows(cid)
            assert_audit(events, [
                (cid + '-profile', 'user', owner_id, {'first_name': [old_user['first_name'], 'Lifecycle owner']}),
                (cid + '-profile', 'sportassociation', association_id, {'denomination': [old_association['denomination'], 'Lifecycle association']}),
                (cid + '-category', 'paymentcategory', category_id, category_changes),
            ])
            return {'name': label, 'statuses': [200, 200], 'first_blocked_backend': first_waiter,
                    'both_blocked_backends': both_waiters, 'category_method': category_method,
                    'audit_rows': len(events), 'success': True}

    def owner_state(field, value, number, method='PATCH'):
        label = 'owner-deactivation' if field == 'is_active' else 'owner-soft-deletion'
        if method == 'DELETE':
            label += '-delete'
        with fixture(label, number) as (category_id, cid, old_user, _):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    User.original_objects.select_for_update().get(pk=owner_id)
                    blocker = backend_pid()
                    route = f'/payment/category/{category_id}/{"delete" if method == "DELETE" else "update"}'
                    pending = pool.submit(client.request, method, route,
                                          None if method == 'DELETE' else {'name': 'Forbidden stale owner'},
                                          {'X-Correlation-ID': cid + '-denied'})
                    waiting = wait_chain(blocker, 1)
                    # Authentication observed the prior committed active owner;
                    # the mutation must revalidate after acquiring this lock.
                    User.original_objects.filter(pk=owner_id).update(**{field: value})
                    expected = snapshot()
                status, response = pending.result(timeout=20)
            assert (status, response) == (403, {'error': 'user not authorized'}), (label, status, response)
            assert snapshot() == expected, label + ': stale admitted owner mutated managed state'
            assert not audit_rows(cid), label + ': denied mutation emitted audit'
            User.original_objects.filter(pk=owner_id).update(**{field: old_user[field]})
            recovery_status, recovery = client.request(method, route,
                                                       None if method == 'DELETE' else {'name': 'Recovered owner'},
                                                       {'X-Correlation-ID': cid + '-recovery'})
            if method == 'DELETE':
                assert (recovery_status, recovery) == (200, {'msg': 'payment category deleted'}), recovery
                assert PaymentCategory._base_manager.get(pk=category_id).deleted
                recovery_changes = {'deleted': ['False', 'True']}
            else:
                assert recovery_status == 200 and recovery['data']['name'] == 'Recovered owner', recovery
                recovery_changes = {'name': ['Lifecycle original', 'Recovered owner']}
            assert_audit(audit_rows(cid), [(cid + '-recovery', 'paymentcategory', category_id,
                                           recovery_changes)])
            return {'name': label, 'blocked_backend': waiting, 'status': status, 'response': response,
                    'method': method, 'exact_denial_snapshot': True, 'audited_recovery': True, 'success': True}

    def disconnect(number):
        label = 'http-disconnect-blocked-delete'
        with fixture(label, number) as (category_id, cid, _, _):
            before = snapshot()
            target = urlsplit(base)
            assert target.scheme == 'http', 'private lifecycle client requires HTTP loopback transport'
            transport = socket.create_connection((target.hostname, target.port or 80), timeout=10)
            try:
                with transaction.atomic():
                    PaymentCategory._base_manager.select_for_update().get(pk=category_id)
                    blocker = backend_pid()
                    route = target.path.rstrip('/') + f'/payment/category/{category_id}/delete'
                    request = (f'DELETE {route} HTTP/1.1\r\nHost: {target.netloc}\r\n'
                               f'Authorization: Bearer {token}\r\nX-Correlation-ID: {cid}-abandoned\r\n'
                               'Content-Length: 0\r\nConnection: close\r\n\r\n')
                    transport.sendall(request.encode('ascii'))
                    waiting = wait_chain(blocker, 1)
                    assert len(waiting) == 1, waiting
                    waiter = waiting[0]
                    transport.shutdown(socket.SHUT_RDWR)
                    transport.close()
                    ended, final_activity = transaction_ended(waiter, 3)
                    before_release_unchanged = snapshot() == before
                drained, after_release_activity = transaction_ended(waiter, 10)
                assert drained, ('disconnected request did not drain after barrier release', after_release_activity)
                row = PaymentCategory._base_manager.get(pk=category_id)
                observed = {'name': label, 'blocked_backend': waiter,
                            'transaction_ended_before_blocker_release': ended,
                            'activity_before_release': final_activity,
                            'unchanged_before_release': before_release_unchanged,
                            'deleted_after_release': row.deleted,
                            'abandoned_audits': [event.changes_dict for event in audit_rows(cid)]}
                if backend == 'Django':
                    # An admitted synchronous Django view is not canceled with
                    # its ASGI caller. Characterize that existing behavior; do
                    # not claim it matches Go's request-context cancellation.
                    assert not ended and before_release_unchanged, observed
                    assert row.deleted and row.name == 'Lifecycle original', observed
                    assert_audit(audit_rows(cid), [(cid + '-abandoned', 'paymentcategory', category_id,
                                                   {'deleted': ['False', 'True']})])
                    after = snapshot()
                    changed = {label for label in before if before[label] != after[label]}
                    assert changed == {'application.PaymentCategory', 'auditlog.LogEntry', 'application.AuditLogIndex'}, changed
                    expected_categories = [dict(record) for record in before['application.PaymentCategory']]
                    for record in expected_categories:
                        if str(record['payment_category_id']) == category_id:
                            record['deleted'] = True
                    assert after['application.PaymentCategory'] == expected_categories
                    for model in ('auditlog.LogEntry', 'application.AuditLogIndex'):
                        assert len(after[model]) == len(before[model]) + 1
                        assert all(record in after[model] for record in before[model])
                    status, response = client.request('PATCH', f'/payment/category/{category_id}/update',
                                                       {'name': 'Recovered after committed disconnect'},
                                                       {'X-Correlation-ID': cid + '-recovery'})
                    assert status == 200 and response['data']['name'] == 'Recovered after committed disconnect', response
                    assert_audit(audit_rows(cid), [
                        (cid + '-abandoned', 'paymentcategory', category_id, {'deleted': ['False', 'True']}),
                        (cid + '-recovery', 'paymentcategory', category_id,
                         {'name': ['Lifecycle original', 'Recovered after committed disconnect']}),
                    ])
                    observed.update(success=True, audited_recovery=True, disconnect_semantics_match=False,
                                    expected_outcome='admitted_sync_request_commits_once_after_barrier_release')
                    return observed
                assert backend == 'Go', ('unsupported disconnect runtime', backend)
                if not ended or not before_release_unchanged or snapshot() != before:
                    observed.update(success=False, failure='HTTP disconnect did not end the blocked mutation and roll back before barrier release')
                    return observed
                status, response = client.request('DELETE', f'/payment/category/{category_id}/delete',
                                                  headers={'X-Correlation-ID': cid + '-recovery'})
                assert (status, response) == (200, {'msg': 'payment category deleted'}), (status, response)
                assert PaymentCategory._base_manager.get(pk=category_id).deleted
                assert_audit(audit_rows(cid), [(cid + '-recovery', 'paymentcategory', category_id,
                                               {'deleted': ['False', 'True']})])
                observed.update(success=True, audited_recovery=True, disconnect_semantics_match=False,
                                expected_outcome='request_context_cancels_and_rolls_back_before_barrier_release')
                return observed
            finally:
                transport.close()

    probes = (
        ('profile-first', lambda: overlap(True, 1)),
        ('category-first', lambda: overlap(False, 2)),
        ('profile-first-delete', lambda: overlap(True, 6, 'DELETE')),
        ('category-first-delete', lambda: overlap(False, 7, 'DELETE')),
        ('owner-deactivation', lambda: owner_state('is_active', False, 3)),
        ('owner-soft-deletion', lambda: owner_state('deleted', True, 4)),
        ('owner-deactivation-delete', lambda: owner_state('is_active', False, 8, 'DELETE')),
        ('owner-soft-deletion-delete', lambda: owner_state('deleted', True, 9, 'DELETE')),
        ('http-disconnect-blocked-delete', lambda: disconnect(5)),
    )
    for name, probe in probes:
        try:
            result = probe()
        except Exception as failure:
            result = {'name': name, 'success': False, 'failure': f'{type(failure).__name__}: {failure}'}
        evidence['cases'].append(result)
        save_evidence()
        distinction = ' (runtime-specific expectation; disconnect parity remains open)' if name == 'http-disconnect-blocked-delete' else ''
        print(f'{backend} lifecycle {name}: {"PASS" if result["success"] else "FAIL"}{distinction}', flush=True)
    evidence['success'] = all(case['success'] for case in evidence['cases'])
    save_evidence()
    return evidence
