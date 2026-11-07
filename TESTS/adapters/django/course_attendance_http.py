"""Parent-only course attendance HTTP runner; imports are inert.

Seeds an instructor, payments, a carnet, associates (one with a competitive
certificate), subscriptions, courses, course subscriptions (one deleted, one
without subscription), registries with their events, attendance days ('{today}'
days dated on the Europe/Rome date at seeding) and carnet holdings with their
course links (audit disabled), and verifies each case against the complete
model state: only the authored attendance days and carnet holdings may change,
with exactly the authored auditlog entries, in order, and their index rows.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import ipaddress

from TESTS.contracts import test_course_attendance_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_attendance_http.json', 'TESTS/contracts/test_course_attendance_http.py',
           'TESTS/contracts/test_course_subscriptions_http.py', 'TESTS/adapters/django/course_attendance_http.py',
           'BE/application/views/attendee_views.py', 'BE/application/views/statistic_views.py',
           'BE/application/utils/attendance_utils.py', 'BE/application/models/attendee_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/permissions.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
DAY, HOLDING = 'application.AttendanceDay', 'application.CarnetSubscription'
MANAGED = (DAY, HOLDING, harness.AUDIT, harness.INDEX)
TODAY = {}


def moment(text):
    return datetime.fromisoformat(text.replace('{today}', TODAY['value']).replace('Z', '+00:00'))


def day(text):
    return date.fromisoformat(text)


def seed_rows(source, created):
    from application.models import (Associate, AttendanceDay, AttendanceRegistry, Course, CourseSubscription, Instructor,
                                    MedicalCertificate, Payment, Subscription)
    from application.models.carnet_models import Carnet, CarnetSubscription
    ids = source['ids']
    TODAY['value'] = contract.today()

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    instructor = source['instructor']
    insert(Instructor, Instructor(instructor_id=instructor['id'], user_id=ids['owner'], first_name=instructor['first'],
                                  last_name=instructor['last']))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=ids['own_association'], amount=Decimal('10.00'), paid=row['paid']))
    insert(Carnet, Carnet(carnet_id=source['carnet'], title='Carnet dieci', lessons_number=10, sport_association_id=ids['own_association'],
                          fee=Decimal('1.00')))
    medical = source['medical']
    insert(MedicalCertificate, MedicalCertificate(medical_id=medical['id'], expiration_date=day(medical['expiration']),
                                                  competitive_medical_certificate=medical['competitive']))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], sex='M', tax_code=row['tax'], born_date=day(row['born']), is_minor=False))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']],
                                          associate_id=source['associates'][row['associate']]['id'], medical_id=row['medical'],
                                          creation_date=moment(row['created']), status_flag=Subscription.ACCEPTED))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'], deleted=row['deleted'],
                              fee=Decimal('50.00')))
    for row in source['course_subscriptions'].values():
        subscription = source['subscriptions'][row['subscription']]['id'] if row['subscription'] else None
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      subscription_id=subscription, deleted=row['deleted']))
    for row in source['registries'].values():
        insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      events=row['events'], status=AttendanceRegistry.PUBLISHED))
    for row in source['days'].values():
        insert(AttendanceDay, AttendanceDay(attendance_day_id=row['id'], attendance_registry_id=source['registries'][row['registry']]['id'],
                                            title=row['title'], date=moment(row['date']), attendees=row['attendees'],
                                            expected_absences=row['expected_absences'], associated_event=row['associated_event']))
    for row in source['holdings'].values():
        payment = source['payments'][row['payment']]['id'] if row['payment'] else None
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnet'], user_id_id=ids['owner'],
                                                      subscription_id=source['subscriptions'][row['subscription']]['id'], payment_id=payment,
                                                      meta=row['meta']))
        # auto_now_add overrides the value on insert.
        CarnetSubscription._base_manager.filter(pk=row['id']).update(creation_date=moment(row['created']))
        CarnetSubscription.course_subscription.through.objects.bulk_create([CarnetSubscription.course_subscription.through(
            carnetsubscription_id=row['id'], coursesubscription_id=source['course_subscriptions'][cs]['id']) for cs in row['cs']])


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        yield fixture


def expected_rows(before, key, changes):
    return {ident: {**row, **changes.get(ident, {})} for ident, row in keyed(before, key).items()}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    today = TODAY['value']
    fill = lambda value: contract.with_today(value, today)
    ids = source['ids']
    days = {source['days'][label]['id']: fill(fields) for label, fields in effect['days'].items()}
    holdings = {source['holdings'][label]['id']: {'meta': fill(meta)} for label, meta in effect['holdings'].items()}
    contract.require(keyed(after[DAY], 'attendance_day_id') == expected_rows(before[DAY], 'attendance_day_id', days), 'state.days')
    contract.require(keyed(after[HOLDING], 'carnet_subscription_id') == expected_rows(before[HOLDING], 'carnet_subscription_id', holdings),
                     'state.holdings')
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == len(added), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    tables = {'attendanceday': ('AttendanceDay', 'days'), 'carnetsubscription': ('CarnetSubscription', 'holdings')}
    for event, entry in zip(added, effect['audit']):
        model, table = tables[entry['model']]
        key = source[table][entry['label']]['id']
        where = f":{entry['model']}:{entry['label']}"
        actor = ids[entry['actor']]
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == 1 and event['object_pk'] == key
                         and event['object_repr'] == f'{model} object ({key})', 'audit.identity' + where)
        contract.require(event['changes'] == fill(entry['changes']), 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes[str(event['id'])]
        contract.require(index['sport_association_id'] == ids[entry['index'][0]] and index['resolution_path'] == entry['index'][1],
                         'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect)
