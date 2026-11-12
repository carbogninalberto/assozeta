"""Parent-only instructor writes and lesson-hours HTTP runner; imports are inert.

Seeds documents, instructors (with their document links), payments, instructor
hours, reminders, a subscription, courses and attendance registries with
calendar events (audit disabled). Each case is checked against the complete
model state: only the authored Instructor, InstructorHours, Payment,
Subscription and Reminders rows and instructor document links change (a new
row is matched as the one new key, or the authored key), and every new auditlog
entry matches an authored event, in order, with its authored index row (or
none). Rows the run creates are removed before the fixture cleanup compares the
model state.
"""
import ipaddress
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from TESTS.contracts import test_instructor_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instructor_writes_http.json', 'TESTS/contracts/test_instructor_writes_http.py',
           'TESTS/adapters/django/instructor_writes_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/instructor_views.py', 'BE/application/utils/instructors_utils.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/attendee_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/balance_sheet_models.py',
           'BE/application/models/courses_models.py', 'BE/docmanager/models.py', 'BE/application/mixin.py',
           'BE/application/utils/api_utils.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/impersonation.py', 'BE/application/permissions_registry.py', 'BE/application/signals.py',
           'TESTS/fixtures/generate_instructor_writes_http.py', 'TESTS/contracts/xlsx.py') + harness.SOURCES
# Payment first: hour columns refer to the new payment's key.
LABELS = {'Payment': 'payments', 'Instructor': 'instructors', 'InstructorHours': 'hours', 'Subscription': 'subscriptions',
          'Reminders': 'reminders'}
THROUGH = 'application.Instructor_documents'
MANAGED = tuple(f'application.{model}' for model in LABELS) + (THROUGH, harness.AUDIT, harness.INDEX)
CATEGORY = 'Compensi e Rimborsi Spese'


def model(name):
    from django.apps import apps
    return apps.get_model('application', name)


def when(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (AttendanceRegistry, Course, Instructor, InstructorHours, Payment, Reminders, Subscription)
    from docmanager.models import Document
    ids = source['ids']

    def insert(kind, row):
        kind._base_manager.bulk_create([row])
        created.append((kind, row.pk))

    for label, pk in source['documents'].items():
        insert(Document, Document(document_id=pk, filename=f'{label}.pdf'))
    day = lambda text: date.fromisoformat(text) if text else None
    instructors = source['instructors']
    for row in instructors.values():
        values = {k: row[k] for k in ('first_name', 'last_name', 'email', 'born_city', 'born_province', 'tax_code', 'address_city', 'address',
                                      'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3', 'phone_4',
                                      'draft', 'associated_user_id', 'is_volunteer')}
        insert(Instructor, Instructor(instructor_id=row['id'], user_id=ids[row['owner']], born_date=day(row['born_date']),
                                      stipulated_contract_in=day(row['stipulated_contract_in']),
                                      default_hourly_billing=Decimal(row['default_hourly_billing']),
                                      default_percentage_billing=Decimal(row['default_percentage_billing'])
                                      if row['default_percentage_billing'] else None, **values))
        Instructor.documents.through.objects.bulk_create([Instructor.documents.through(instructor_id=row['id'], document_id=source['documents'][d])
                                                         for d in row['documents']])
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=ids['own_association'], subject=Payment.OTHER,
                                amount=Decimal(row['amount']), instructor_id=instructors[row['instructor']]['id'],
                                creation_date=when('2026-08-01T08:00:00Z')))
    payments = source['payments']
    for row in source['hours'].values():
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=row['id'], instructor_id=instructors[row['instructor']]['id'], creation_date=when(row['created']),
            date=date.fromisoformat(row['date']), compensation_type=row['compensation_type'], hours=Decimal(row['hours']),
            hourly_billing=Decimal(row['hourly_billing']),
            percentage_billing=Decimal(row['percentage_billing']) if row['percentage_billing'] is not None else None,
            amount=Decimal(row['amount']), paid=row['paid'], payment_id=payments[row['payment']]['id'] if row['payment'] else None,
            notes=row['notes'], calculation_data=row['calculation_data']))
    for row in source['reminders'].values():
        insert(Reminders, Reminders(reminders_id=row['id'], event_id=row['id'], event_title=row['title'], send_at=when(row['send_at']),
                                    user_id=ids['owner'], instructor_id=instructors[row['instructor']]['id'],
                                    sport_association_id=row['association']))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=ids['own_association'], user_id=ids['athlete'],
                                          instructor_id=instructors[row['instructor']]['id'], status_flag=Subscription.ACCEPTED))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'], course_type=1,
                              fee=Decimal('50.00'), status_flag=Course.ACTIVE))
    for row in source['registries'].values():
        insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      events=row['events'], status=row['status'], creation_date=when('2026-08-01T08:00:00Z')))
    # instructor/report: attendance days and the course subscriptions their attendees name.
    from application.models import AttendanceDay, Associate, CourseSubscription
    for row in source['days'].values():
        insert(AttendanceDay, AttendanceDay(attendance_day_id=row['id'], attendance_registry_id=source['registries'][row['registry']]['id'],
                                            title=row['title'], date=when(row['date']), attendees=row['attendees'],
                                            expected_absences=row['expected_absences'],
                                            associated_event=row['event']))
    people = {}
    for label, row in source['people'].items():
        insert(Associate, Associate(associate_id=row['id'], first_name=row['first_name'], last_name=row['last_name'],
                                    sport_association_id=ids['own_association']))
        people[label] = Subscription(sport_association_id=ids['own_association'], user_id=ids['athlete'], associate_id=row['id'],
                                     status_flag=Subscription.ACCEPTED)
        insert(Subscription, people[label])
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['courses']['ca']['id'],
                                                      subscription_id=people[row['person']].pk if row['person'] else None,
                                                      deleted=row['deleted']))


def prepared(source):
    """The delete diffs of every instructor, hour and reminder, captured before each case."""
    result = {}
    for name in ('Instructor', 'InstructorHours', 'Reminders'):
        kind = model(name)
        result.update({str(row.pk): replace.deleted_changes(kind, row) for row in kind._base_manager.all()})
    return result


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime', 'decimal'):
        return value['value']
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def rows_of(state, name):
    kind = model(name)
    return {k: plain(row) for k, row in keyed(state[f'application.{name}'], kind._meta.pk.attname).items()}


def created_changes(kind, row, overrides):
    """auditlog's create diff of the stored row, with the authored in-memory overrides (None: the field was None
    in memory, as a later save filled it)."""
    changes = {}
    for field in kind._meta.concrete_fields:
        if field.name in overrides and overrides[field.name] is None:
            continue
        value = getattr(row, field.attname)
        internal = field.get_internal_type()
        if value is None:
            if field.is_relation or internal == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif internal == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        if internal == 'DateTimeField':
            value = str(value.astimezone(timezone.utc).replace(tzinfo=None))
        elif internal == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = ['None', overrides.get(field.name, str(value))]
    return changes


def recent(text):
    return text is not None and abs(datetime.now(timezone.utc) - when(text)) < timedelta(minutes=10)


def placeholders(context):
    from application.models import CustomAccounts, PaymentCategory
    category = PaymentCategory.objects.filter(name=CATEGORY).first()
    account = CustomAccounts.objects.filter(sport_association_id=context['ids']['own_association'], editable=False, enabled=True,
                                            account_type=CustomAccounts.BANK).first()
    return {'$compensation-category': str(category.pk) if category else None, '$default-bank-account': str(account.pk) if account else None}


def check_columns(row, columns, context, code):
    for column, value in columns.items():
        if value == '$now':
            contract.require(recent(row[column]), code + '-now')
            continue
        if value == '$new':
            value = context['keys'].get('Payment')
        value = context['values'].get(value, value) if isinstance(value, str) else value
        contract.require(row[column] == value and type(row[column]) is type(value), code)


def check_rows(before, after, effect, source, context):
    for name, table in LABELS.items():
        old, new = rows_of(before, name), rows_of(after, name)
        labels = source[table]
        authored = {labels[label]['id'] if label in labels else label: columns for label, columns in effect['rows'].get(name, {}).items()}
        fresh = set(new) - set(old)
        expected_new = [key for key in authored if key == '$new' or key not in old]
        contract.require(len(fresh) == len(expected_new), f'state.{name}.created-count')
        for key in expected_new:
            actual = key
            if key == '$new':
                candidates = fresh - set(authored)
                contract.require(len(candidates) == 1, f'state.{name}.created')
                actual = candidates.pop()
                context['keys'][name] = actual
            contract.require(actual in fresh, f'state.{name}.created-key')
            row, columns = new[actual], authored[key]
            if 'creation_date' in row:
                contract.require(recent(row['creation_date']), f'state.{name}.created-now')
            check_columns(row, columns, context, f'state.{name}.created-row')
        for key, row in old.items():
            if key in authored and authored[key] is None:
                contract.require(key not in new, f'state.{name}.deleted')
            elif key in authored:
                contract.require(key in new, f'state.{name}.updated')
                check_columns(new[key], authored[key], context, f'state.{name}.updated-row')
                contract.require({c: v for c, v in new[key].items() if c not in authored[key]} ==
                                 {c: v for c, v in row.items() if c not in authored[key]}, f'state.{name}.updated-others')
            else:
                contract.require(new.get(key) == row, f'state.{name}.unchanged')


def check_documents(before, after, effect, source):
    old, new = harness.pairs(before[THROUGH], 'instructor_id', 'document_id'), harness.pairs(after[THROUGH], 'instructor_id', 'document_id')
    touched = {source['instructors'][label]['id']: [source['documents'][d] for d in docs] for label, docs in effect['documents'].items()}
    deleted = {source['instructors'][label]['id'] for label, columns in effect['rows'].get('Instructor', {}).items()
               if columns is None and label in source['instructors']}
    expected = {pair for pair in old if pair[0] not in touched and pair[0] not in deleted}
    expected |= {(instructor, document) for instructor, documents in touched.items() for document in documents}
    contract.require(new == expected, 'state.documents')


def check_event(case, event, indexes, *, before, ids, spec, key, changes, context):
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['effect']['actor']]
    contract.require(event['content_type_id'] == types[spec['model'].lower()] and event['action'] == spec['action']
                     and event['object_pk'] == key and event['object_repr'] == f'{spec["model"]} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    session = case['headers'].get('X-Impersonation-Id', '')
    if session.startswith('$session:'):
        additional = {'impersonation_session': context['fixture']['sessions'][session.removeprefix('$session:')], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    if spec['index'] is None:
        contract.require(index is None, 'index.absent')
    else:
        contract.require(index is not None and (index['sport_association_id'], index['resolution_path']) ==
                         (spec['index']['association'], spec['index']['path']), 'index.fields')


def substituted(changes, context):
    return {field: [context['keys'].get('Payment') if value == '$new' else value for value in pair] for field, pair in changes.items()}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    context['keys'], context['ids'] = {}, source['ids']
    context['values'] = placeholders(context)
    check_rows(before, after, effect, source, context)
    check_documents(before, after, effect, source)
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']), 'audit.count')
    remaining = sorted(added, key=lambda event: event['id'])
    for spec in effect['audit']:
        key = context['keys'].get(spec['model'], spec['key']) if spec['key'] == '$new' else spec['key']
        event = remaining.pop(0)
        contract.require(event['object_pk'] == key and event['action'] == spec['action'], 'audit.order')
        if spec['changes'] == '$deleted':
            changes = context['prepared'][key]
        elif spec['changes'] == '$created':
            kind = model(spec['model'])
            changes = created_changes(kind, kind._base_manager.get(pk=key), spec['overrides'])
        else:
            changes = substituted(spec['changes'], context)
        check_event(case, event, indexes, before=before, ids=source['ids'], spec=spec, key=key, changes=changes, context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(lambda: model('Instructor'), lambda: model('Payment')))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepared)
