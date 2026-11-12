"""Real Go HTTP rollback with healthy transactions and deferred COMMIT errors.

These private-database stimuli are distinct from Django's injected ORM index
constructor exception. No application response or persistence operation is mocked.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import time

from TESTS.http_client import Client


def run(base, *, owner_id, association_id, token, snapshot, django_requests,
        evidence_path, transport, expected_remote_addr='198.51.100.93'):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.db import connection
    from application.models import PaymentCategory, VatManagement, User
    from application.models.audit_models import AuditLogIndex

    assert transport in ('native-go', 'caddy-v2'), transport
    owner_id, association_id = str(owner_id), str(association_id)
    original = snapshot()
    client = Client(base, token, timeout=30)
    fixed = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    prefix = 'payment-category-transaction-' + transport
    report = {'success': False, 'transport': transport, 'backend': 'Go', 'cases': [],
              'cleanup_verified': False, 'expected_scenarios': 6,
              'scope': 'Application-driven rollback and deferred COMMIT failure; not Django ORM constructor injection',
              'complete_payment_category_parity': False}
    started = time.monotonic()

    def save():
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(report, indent=2) + '\n')
        evidence_path.chmod(0o600)

    def request(method, path, body, cid):
        result = client.request(method, path, body, {
            'X-Correlation-ID': cid, 'X-Forwarded-For': '198.51.100.93',
        }, include_headers=True)
        assert not django_requests(cid), (cid, 'Go request reached Django', django_requests(cid))
        assert result[2].get('Set-Cookie') is None, (cid, 'unexpected session mutation')
        return result

    @contextmanager
    def missing_content_type():
        # Retain the PK and all existing audit/FK rows. Only the runtime lookup
        # label is absent. INSERT ... SELECT returns zero rows without aborting
        # the SQL transaction; Go must act on its application error and roll back.
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM django_content_type WHERE app_label='application' AND model='paymentcategory'")
            rows = cursor.fetchall()
            assert len(rows) == 1, rows
            content_type = rows[0][0]
            cursor.execute("SELECT count(*) FROM django_content_type WHERE app_label='application' AND model='transaction_fixture_paymentcategory'")
            assert cursor.fetchone()[0] == 0, 'content type fixture collision'
            cursor.execute("UPDATE django_content_type SET model='transaction_fixture_paymentcategory' WHERE id=%s", [content_type])
            assert cursor.rowcount == 1
        try:
            yield {'kind': 'missing-content-type', 'content_type_id': content_type,
                   'sql_transaction_aborted_by_stimulus': False,
                   'application_error': 'audit model content type missing'}
        finally:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE django_content_type SET model='paymentcategory' WHERE id=%s AND model='transaction_fixture_paymentcategory'", [content_type])
                assert cursor.rowcount == 1, 'owned content type label changed unexpectedly'

    def deferred_failure(method, path, body, cid, category_id, number, case):
        function = 'payment_category_deferred_transaction_failure'
        trigger = function
        lock_key = 197000 + number
        lock_held = False
        installed = False
        future = None
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    blocker = cursor.fetchone()[0]
                    cursor.execute('SELECT to_regprocedure(%s)', [function + '()'])
                    assert cursor.fetchone()[0] is None, 'deferred fixture function collision'
                    cursor.execute(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN
                          IF EXISTS (SELECT 1 FROM auditlog_logentry
                                     WHERE id=NEW.log_entry_id AND object_pk='{category_id}') THEN
                            PERFORM pg_advisory_xact_lock(19701970, {lock_key});
                            RAISE EXCEPTION 'synthetic deferred category COMMIT failure';
                          END IF;
                          RETURN NEW;
                        END $$''')
                    installed = True
                    cursor.execute(f'''CREATE CONSTRAINT TRIGGER {trigger}
                        AFTER INSERT ON application_auditlogindex
                        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function}()''')
                    cursor.execute('''SELECT tgdeferrable,tginitdeferred,tgconstraint<>0,
                        pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname=%s''', [trigger])
                    definition = cursor.fetchone()
                    assert definition[:3] == (True, True, True), definition
                    cursor.execute('SELECT pg_advisory_lock(19701970,%s)', [lock_key])
                    lock_held = True
                case['deferred_trigger'] = {'deferrable': definition[0], 'initially_deferred': definition[1],
                                            'constraint_trigger': definition[2], 'definition': definition[3]}
                future = executor.submit(request, method, path, body, cid)
                deadline = time.monotonic() + 10
                observed = []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_stat_clear_snapshot()')
                        cursor.execute('''SELECT pid,query,state,wait_event_type,xact_start IS NOT NULL,
                            pg_blocking_pids(pid) FROM pg_stat_activity
                            WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))''', [blocker])
                        observed = cursor.fetchall()
                    commits = [row for row in observed if row[1].strip().rstrip(';').upper() == 'COMMIT'
                               and row[2] == 'active' and row[3] == 'Lock' and row[4]]
                    if len(commits) == 1:
                        row = commits[0]
                        case['observed_commit'] = dict(zip(('pid', 'query', 'state', 'wait', 'transaction', 'blockers'), row))
                        case['blocker_pid'] = blocker
                        assert not future.done(), 'HTTP response completed while its COMMIT remained blocked'
                        break
                    if future.done():
                        raise AssertionError(('request ended before deferred COMMIT barrier', future.result()[:2], observed))
                    time.sleep(.01)
                else:
                    raise AssertionError(('no actual blocked COMMIT observed', observed))
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(19701970,%s)', [lock_key])
                    assert cursor.fetchone()[0]
                    lock_held = False
                result = future.result(timeout=10)
                # The response must not leave a failed/active transaction behind.
                deadline = time.monotonic() + 5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_stat_clear_snapshot()')
                        cursor.execute('''SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=%s
                            AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL))''', [case['observed_commit']['pid']])
                        ended = cursor.fetchone()[0]
                    if ended:
                        case['backend_transaction_ended'] = True
                        break
                    assert time.monotonic() < deadline, 'COMMIT failure left its backend in a transaction'
                    time.sleep(.01)
                return result
            finally:
                if lock_held:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_unlock(19701970,%s)', [lock_key])
                        assert cursor.fetchone()[0]
                try:
                    if future is not None:
                        future.result(timeout=35)
                finally:
                    if installed:
                        with connection.cursor() as cursor:
                            cursor.execute(f'DROP FUNCTION {function}() CASCADE')

    def recovery(before, method, path, body, cid, category_id, old_vat_id, new_vat_id):
        status, response, _ = request(method, path, body, cid)
        assert status == 200, ('recovery status', method, status, response)
        expected_name = 'Transaction original' if method == 'DELETE' else 'Transaction changed'
        expected_vat = old_vat_id if method == 'DELETE' else new_vat_id
        row = PaymentCategory._base_manager.get(pk=category_id)
        assert (row.name, str(row.sport_association_id), str(row.vat_management_id), row.deleted) == (
            expected_name, association_id, expected_vat, method == 'DELETE')
        assert row.creation_date == fixed and not row.expense and not row.tax_deductible and not row.archived and row.type == 1
        assert list(VatManagement.objects.filter(pk=old_vat_id).values_list('vat', 'deduction')) == [(7, 1)]
        if method != 'DELETE':
            assert list(VatManagement.objects.filter(pk=new_vat_id).values_list('vat', 'deduction')) == [(8, 2)]
        expected_changes = {'deleted': ['False', 'True']} if method == 'DELETE' else {
            'name': ['Transaction original', 'Transaction changed'], 'vat_management': [old_vat_id, new_vat_id]}
        if method == 'POST':
            expected_changes = {key: ['None', value] for key, value in {
                'payment_category_id': category_id, 'name': 'Transaction changed',
                'creation_date': '2026-10-24 12:00:00', 'sport_association': association_id,
                'expense': 'False', 'tax_deductible': 'False', 'archived': 'False', 'type': '1',
                'vat_management': new_vat_id, 'deleted': 'False',
                'users_default_category': 'application.User.None',
                'users_default_category_courses': 'application.User.None',
            }.items()}
        events = list(LogEntry.objects.filter(cid=cid).order_by('id'))
        expected_objects = {category_id} if method == 'DELETE' else {category_id, new_vat_id}
        assert {event.object_pk for event in events} == expected_objects and len(events) == len(expected_objects)
        for event in events:
            is_category = event.object_pk == category_id
            assert event.content_type.model == ('paymentcategory' if is_category else 'vatmanagement')
            assert event.action == (1 if is_category and method != 'POST' else 0)
            assert event.changes_dict == (expected_changes if is_category else {
                'vat_management_id': ['None', new_vat_id], 'vat': ['None', '8'], 'deduction': ['None', '2']})
            assert str(event.actor_id) == owner_id and event.actor_email == User.original_objects.get(pk=owner_id).email
            assert event.additional_data is None and event.remote_addr == expected_remote_addr
            assert event.object_repr == f'{"PaymentCategory" if is_category else "VatManagement"} object ({event.object_pk})'
            indexes = list(AuditLogIndex.objects.filter(log_entry_id=event.pk))
            assert len(indexes) == 1 and str(indexes[0].sport_association_id) == association_id
            assert indexes[0].resolution_path == ('direct' if is_category else 'actor.sport_association')
            assert indexes[0].created_at == event.timestamp
        after = snapshot()
        ids = {category_id, old_vat_id, new_vat_id}
        event_ids = {event.pk for event in events}
        for model, prior in before.items():
            actual = after[model]
            if model == 'application.PaymentCategory':
                assert [r for r in actual if r['payment_category_id'] not in ids] == [r for r in prior if r['payment_category_id'] not in ids]
                assert len(actual) == len(prior) + (method == 'POST')
            elif model == 'application.VatManagement':
                assert [r for r in actual if r['vat_management_id'] not in ids] == [r for r in prior if r['vat_management_id'] not in ids]
                assert len(actual) == len(prior) + (method != 'DELETE')
            elif model == 'auditlog.LogEntry':
                assert [r for r in actual if r['id'] not in event_ids] == prior
            elif model == 'application.AuditLogIndex':
                assert [r for r in actual if r['log_entry_id'] not in event_ids] == prior
            else:
                assert actual == prior, ('recovery changed unrelated managed state', model)
        return {'status': status, 'audits': len(events), 'exact_changes_actor_indexes': True,
                'unrelated_managed_rows_unchanged': True}

    try:
        for number, (kind, method) in enumerate((kind, method) for kind in ('application-error', 'deferred-commit') for method in ('POST', 'PATCH', 'DELETE')):
            category_id, old_vat_id, new_vat_id = [f'00000000-0000-4000-8000-{197000 + number * 3 + offset:012d}' for offset in range(3)]
            cid = f'{prefix}-{kind}-{method.lower()}'
            case = {'name': cid, 'kind': kind, 'method': method, 'success': False}
            report['cases'].append(case)
            prior = snapshot()
            assert not PaymentCategory._base_manager.filter(pk=category_id).exists()
            assert not VatManagement.objects.filter(pk__in=[old_vat_id, new_vat_id]).exists()
            assert not LogEntry.objects.filter(cid__startswith=cid).exists()
            assert not LogEntry.objects.filter(object_pk__in=[category_id, old_vat_id, new_vat_id]).exists()
            try:
                with disable_auditlog():
                    VatManagement.objects.bulk_create([VatManagement(vat_management_id=old_vat_id, vat=7, deduction=1)])
                    if method != 'POST':
                        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=category_id,
                            sport_association_id=association_id, name='Transaction original',
                            creation_date=fixed, vat_management_id=old_vat_id)])
                path = '/payment/category/add' if method == 'POST' else f'/payment/category/{category_id}/{"delete" if method == "DELETE" else "update"}'
                body = None if method == 'DELETE' else {'name': 'Transaction changed',
                    'vat_management': {'vat_management_id': new_vat_id, 'vat': 8, 'deduction': 2}}
                if method == 'POST':
                    body.update(payment_category_id=category_id, creation_date='2026-10-24T12:00:00Z')
                before = snapshot()
                if kind == 'application-error':
                    with missing_content_type() as stimulus:
                        case['stimulus'] = stimulus
                        fault_before = snapshot()
                        status, response, headers = request(method, path, body, cid)
                        case['status'] = status
                        case['exact_managed_rollback'] = snapshot() == fault_before
                    case['content_type_restored'] = True  # Context exit verified the exact owned UPDATE.
                    case['managed_state_after_content_type_restore_equal'] = snapshot() == before
                else:
                    status, response, headers = deferred_failure(method, path, body, cid, category_id, number, case)
                    case['status'] = status
                    case['exact_managed_rollback'] = snapshot() == before
                assert status == 500, ('transaction failure was acknowledged as success', case, response)
                assert 'text/html' in headers.get('Content-Type', '') and 'Server Error' in response
                assert case['exact_managed_rollback'], ('transaction fault retained managed changes', case)
                assert snapshot() == before, ('fault fixture restoration or rollback failed', case)
                case['recovery'] = recovery(before, method, path, body, cid + '-recovery', category_id, old_vat_id, new_vat_id)
                case['success'] = True
            except Exception as error:
                case['failure'] = str(error)
                raise
            finally:
                with disable_auditlog():
                    LogEntry.objects.filter(object_pk__in=[category_id, old_vat_id, new_vat_id]).delete()
                    PaymentCategory._base_manager.filter(pk=category_id).delete()
                    VatManagement.objects.filter(pk__in=[old_vat_id, new_vat_id]).delete()
                case['cleanup_verified'] = snapshot() == prior
                case['success'] = bool(case['success'] and case['cleanup_verified'])
                save()
                assert case['cleanup_verified'], ('transaction fixture altered unrelated rows', case)
        report.update(success=True, application_error_scenarios=3, deferred_commit_scenarios=3, recovery_requests=6)
        return report
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        report['cleanup_verified'] = snapshot() == original
        report['success'] = bool(report['success'] and report['cleanup_verified'])
        save()
        assert report['cleanup_verified'], 'transaction failure helper cleanup altered pre-existing managed rows'
