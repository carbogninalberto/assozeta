"""Parent-only attendance mark-absent HTTP runner; imports are inert.

Seeds courses, registries, days, subscriptions and course subscriptions
(audit disabled) and verifies each case against the complete model state:
only the day's attendee lists may change, with one exact auditlog update
entry indexed through the attendance_day resolver.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_attendance_mark_absent_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/attendance_day_delete_http.json', 'TESTS/contracts/test_attendance_mark_absent_http.py',
           'TESTS/adapters/django/attendance_day_delete_http.py', 'BE/application/views/attendee_views.py',
           'BE/application/models/attendee_models.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')
DAY, AUDIT, INDEX = 'application.AttendanceDay', 'auditlog.LogEntry', 'application.AuditLogIndex'


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import AttendanceDay, AttendanceRegistry, Course, SportAssociation, User
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for label, course in source['courses'].items():
            Course._base_manager.bulk_create([Course(course_id=course['id'], sport_association_id=course['association'],
                title=f'Corso {label}', description='', fee=Decimal('50.00'), status_flag=Course.ACTIVE)])
            fixture['created'].append((Course, course['id']))
        for registry in source['registries'].values():
            AttendanceRegistry._base_manager.bulk_create([AttendanceRegistry(attendance_registry_id=registry['id'],
                course_id=source['courses'][registry['course']]['id'])])
            fixture['created'].append((AttendanceRegistry, registry['id']))
        for day in source['days'].values():
            AttendanceDay._base_manager.bulk_create([AttendanceDay(attendance_day_id=day['id'],
                attendance_registry_id=source['registries'][day['registry']]['id'], title=day['title'], date=moment(day['date']),
                attendees=day['attendees'], expected_absences=day['expected_absences'], auto_marked=day['auto_marked'],
                associated_event=day['associated_event'])])
            fixture['created'].append((AttendanceDay, day['id']))
        from application.models import CourseSubscription
        from application.tests.fixtures.factories import create_test_subscription
        ids = source['ids']
        for row in source['subscriptions'].values():
            subscription = create_test_subscription(sport_association=SportAssociation._base_manager.get(pk=ids[row['association']]),
                user=User._base_manager.get(pk=ids[row['user']]), subscription_id=row['id'])
            fixture['created'].append((type(subscription.associate), subscription.associate_id))
            fixture['created'].append((type(subscription), subscription.pk))
        for row in source['course_subscriptions'].values():
            CourseSubscription._base_manager.bulk_create([CourseSubscription(course_subscription_id=row['id'],
                course_id=source['courses'][row['course']]['id'], subscription_id=source['subscriptions'][row['subscription']]['id'])])
            fixture['created'].append((CourseSubscription, row['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=original_audit).delete()
                if fixture is not None:
                    shared.delete_created(fixture)
            contract.require(before == scopes.snapshot_state(apps), 'state.changed')
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, sessions):
    managed = (DAY, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_d, new_d = keyed(before[DAY], 'attendance_day_id'), keyed(after[DAY], 'attendance_day_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    if effect is None:
        contract.require(old_d == new_d and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    target = source['days']['day']['id']
    row = new_d[target]
    contract.require(set(new_d) == set(old_d) and all(new_d[k] == old_d[k] for k in old_d if k != target)
                     and row['attendees'] == effect['attendees'] and row['expected_absences'] == effect['expected_absences']
                     and {k: v for k, v in row.items() if k not in ('attendees', 'expected_absences')}
                     == {k: v for k, v in old_d[target].items() if k not in ('attendees', 'expected_absences')}, 'state.day')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event, ids = added[0], source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'attendanceday']
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f'AttendanceDay object ({target})', 'audit.identity')
    contract.require(event['changes'] == effect['changes'], 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['impersonation']:
        additional = {'impersonation_session': sessions[effect['impersonation']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': ids['own_association']}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id'] and added_i[0]['sport_association_id'] == ids['own_association']
                     and added_i[0]['resolution_path'] == 'attendance_registry.course.sport_association', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'state.changed')
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture['sessions'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
