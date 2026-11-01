"""Parent-only course calendar and full events calendar HTTP runner; imports are inert.

Seeds instructors, courses of three associations, attendance registries, days
and reminders (audit disabled), the owner's Google credentials, the own
association's logo and the restricted collaborator's permissions. Each case
with an authored effect must leave the four calendar models in exactly the
authored state and add exactly the authored auditlog entries with their index
rows; rows created by the run are named '$new:<tag>' and bound to their
generated keys on first sight. Every other case leaves the state unchanged.
"""
import json
from datetime import datetime
from decimal import Decimal

from TESTS.contracts import test_course_calendar_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import audit_time, keyed, scalar

SOURCES = ('TESTS/fixtures/course_calendar_http.json', 'TESTS/contracts/test_course_calendar_http.py',
           'TESTS/adapters/django/course_calendar_http.py', 'BE/application/views/attendee_views.py',
           'BE/application/models/attendee_models.py', 'BE/application/utils/global_calendar.py',
           'BE/application/utils/attendance_utils.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions.py',
           'BE/application/permissions_registry.py', 'BE/application/impersonation.py', 'BE/application/impersonation_scope.py',
           'BE/application/mixin.py', 'BE/application/models/courses_models.py', 'BE/application/models/user_models.py',
           'BE/application/audit_resolvers.py', 'BE/application/signals.py', 'BE/core/middleware.py',
           'BE/core/authentication.py') + harness.SOURCES
MODELS = {'registries': 'application.AttendanceRegistry', 'days': 'application.AttendanceDay',
          'reminders': 'application.Reminders', 'globals': 'application.GlobalCalendarEvents'}
KEYS = {'registries': 'attendance_registry_id', 'days': 'attendance_day_id', 'reminders': 'reminders_id', 'globals': 'global_calendar_id'}
CLASSES = {'attendanceregistry': 'AttendanceRegistry', 'attendanceday': 'AttendanceDay', 'reminders': 'Reminders',
           'globalcalendarevents': 'GlobalCalendarEvents'}
MANAGED = tuple(MODELS.values()) + (harness.AUDIT, harness.INDEX)


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_rows(source, created):
    from application.models import AttendanceDay, AttendanceRegistry, Course, Instructor, Reminders, SportAssociation, User
    ids, seed = source['ids'], source['seed']
    User._base_manager.filter(pk=ids['owner']).update(integration_google_credentials=seed['credentials'])
    User._base_manager.filter(pk=ids['restricted_collaborator']).update(collaborator_permissions=seed['restricted_permissions'])
    SportAssociation._base_manager.filter(pk=ids['own_association']).update(logo=seed['logo'])
    for row in seed['instructors'].values():
        Instructor._base_manager.bulk_create([Instructor(instructor_id=row['id'], user_id=row['user'], first_name=row['first_name'],
                                                         last_name=row['last_name'], associated_user_id=row['associated_user'])])
        created.append((Instructor, row['id']))
    for row in seed['courses'].values():
        Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'],
            description='', fee=Decimal('10.00'), status_flag=Course.ACTIVE, deleted=row['deleted'], google_background_color='#123456',
            google_calendar_id=row['calendar'], group_id=ids[row['group']] if row['group'] else None)])
        created.append((Course, row['id']))
    for pk, row in seed['registries'].items():
        AttendanceRegistry._base_manager.bulk_create([AttendanceRegistry(attendance_registry_id=pk, course_id=row['course'],
            creation_date=moment(row['creation_date']), events=row['events'], status=row['status'])])
        created.append((AttendanceRegistry, pk))
    for pk, row in seed['days'].items():
        AttendanceDay._base_manager.bulk_create([AttendanceDay(attendance_day_id=pk, attendance_registry_id=row['registry'],
            title=row['title'], date=moment(row['date']), attendees=row['attendees'], expected_absences=row['expected_absences'],
            auto_marked=row['auto_marked'], associated_event=row['associated_event'])])
        created.append((AttendanceDay, pk))
    for pk, row in seed['reminders'].items():
        Reminders._base_manager.bulk_create([Reminders(reminders_id=pk, event_id=row['event_id'], event_title=row['event_title'],
            event_description=row['event_description'], event_course_title=row['event_course_title'],
            event_reminder_text=row['event_reminder_text'], send_at=moment(row['send_at']), user_id=row['user'],
            instructor_id=row['instructor'], completed=row['completed'], sport_association_id=row['sport_association'])])
        created.append((Reminders, pk))


def normalize(state):
    """The calendar models as authored: {kind: {pk: fields}}."""
    rows = {kind: keyed(state[label], KEYS[kind]) for kind, label in MODELS.items()}
    return {
        'registries': {pk: {'course': r['course_id'], 'status': r['status'], 'events': r['events'], 'creation_date': r['creation_date']}
                       for pk, r in rows['registries'].items()},
        'days': {pk: {'registry': r['attendance_registry_id'], 'title': r['title'], 'date': r['date'], 'associated_event': r['associated_event'],
                      'attendees': r['attendees'], 'expected_absences': r['expected_absences'], 'auto_marked': r['auto_marked']}
                 for pk, r in rows['days'].items()},
        'reminders': {pk: {'event_id': r['event_id'], 'event_title': r['event_title'], 'event_description': r['event_description'],
                           'event_course_title': r['event_course_title'], 'event_reminder_text': r['event_reminder_text'],
                           'send_at': r['send_at'], 'user': r['user_id'], 'instructor': r['instructor_id'], 'completed': r['completed'],
                           'sport_association': r['sport_association_id']} for pk, r in rows['reminders'].items()},
        'globals': {pk: {'sport_association': r['sport_association_id'], 'events': r['events']} for pk, r in rows['globals'].items()},
    }


def bound(value):
    return contract.BOUND.get(value, value) if isinstance(value, str) else value


def same(expected, actual):
    for key, value in expected.items():
        if key == 'ref':
            continue
        if value == '$any':
            if not isinstance(actual.get(key), str):
                return False
        elif bound(value) != actual.get(key):
            return False
    return set(actual) == set(expected) - {'ref'}


def check_state(effect, after, seeded):
    actual = normalize(after)
    for kind in ('registries', 'days', 'reminders', 'globals'):
        rows, claimed = actual[kind], set()
        expected = effect['state'][kind]
        for row in expected:
            ref = row['ref']
            if ref.startswith('$new:') and ref not in contract.BOUND:
                taken = seeded | set(contract.BOUND.values())
                candidates = [pk for pk, values in rows.items() if pk not in taken and pk not in claimed and same(row, values)]
                contract.require(len(candidates) == 1, f'state.{kind}-new-row')
                contract.BOUND[ref] = candidates[0]
            claimed.add(bound(ref))
        contract.require(set(rows) == claimed, f'state.{kind}-rows')
        for row in expected:
            contract.require(same(row, rows[bound(row['ref'])]), f'state.{kind}-values')
    return actual


def resolve_changes(changes, rows):
    def value(text):
        if text.startswith('$created:'):
            pk = contract.BOUND['$new:' + text[len('$created:'):]]
            return audit_time(rows[pk]['creation_date'])
        return bound(text)
    return {field: [value(pair[0]), value(pair[1])] for field, pair in changes.items()}


def check_audit(case, effect, before, after, source, context):
    ids = source['ids']
    added, indexes = harness.audit_delta(contract, before, after)
    types = {ct['id']: ct['model'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    registries = {**normalize(before)['registries'], **normalize(after)['registries']}
    actual = []
    for event in added:
        model = types.get(event['content_type_id'])
        contract.require(model in CLASSES and event['object_repr'] == f"{CLASSES[model]} object ({event['object_pk']})", 'audit.identity')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        contract.require(event['actor_email'] == users[str(scalar(event['actor_id']))]['email'], 'audit.actor-email')
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        actual.append({'model': model, 'action': event['action'], 'key': event['object_pk'], 'changes': event['changes'],
                       'actor': str(scalar(event['actor_id'])),
                       'index': None if index is None else [str(scalar(index['sport_association_id'])), index['resolution_path']]})
    contract.require(len(indexes) == sum(1 for row in actual if row['index'] is not None), 'audit.index-count')
    expected = [{'model': row['model'], 'action': row['action'], 'key': bound(row['key']),
                 'changes': resolve_changes(row['changes'], registries), 'actor': ids[row['actor']], 'index': row['index']}
                for row in effect['audit']]
    canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
    contract.require(len(actual) == len(expected), 'audit.count')
    contract.require(canonical(actual) == canonical(expected), 'audit.entries')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    seed = source['seed']
    seeded = set(seed['registries']) | set(seed['days']) | set(seed['reminders'])
    check_state(effect, after, seeded)
    check_audit(case, effect, before, after, source, context)


def _models():
    from application.models import AttendanceDay, AttendanceRegistry, GlobalCalendarEvents, Reminders
    return AttendanceRegistry, AttendanceDay, Reminders, GlobalCalendarEvents


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple((lambda i=i: _models()[i]) for i in range(4)))
_run_workload = harness.run_workload_for(contract, assert_effect)


def run_workload(*args, **kwargs):
    contract.BOUND.clear()
    return _run_workload(*args, **kwargs)
