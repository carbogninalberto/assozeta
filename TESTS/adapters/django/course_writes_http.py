"""Parent-only course add, update and delete and course-overview update and delete HTTP runner; imports are inert.

Seeds associates, subscriptions, locations, a tag, courses with their
location and tag links, payments, course subscriptions with their membership
payment links, installments and an attendance registry with one day (audit
disabled), and verifies each case
against the complete model state:
- seeded rows of the courses, course subscriptions, installments, payments,
  registries and days change exactly by the authored columns or disappear;
- created rows (authored with every column; '@name' binds a generated key,
  '$now' a timestamp of the request, '$color' Course's random colour,
  '$category'/'$bank' Payment.save()'s default category and the default bank
  account) must be exactly the new rows;
- location and tag links change by the authored pairs;
- the new auditlog entries match the authored sequence in order, with their
  index rows; '$stored' changes are auditlog's create/delete diff of the
  stored row (after the case / captured before it) with in-memory overrides.
Rows the run creates are removed before the fixture cleanup compares the state.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import re

from TESTS.contracts import test_course_writes_http as contract
from TESTS.adapters.django import camps_periods_http as periods
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_writes_http.json', 'TESTS/contracts/test_course_writes_http.py',
           'TESTS/adapters/django/course_writes_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/camps_periods_http.py',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/attendee_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py',
           'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py') + harness.SOURCES
COURSE, CSM, CSI, PAYMENT = 'application.Course', 'application.CourseSubscription', 'application.CourseSubscriptionInstallment', 'application.Payment'
AR, AD = 'application.AttendanceRegistry', 'application.AttendanceDay'
LINKS = {'application.Course_locations': ('course_id', 'courselocation_id'), 'application.Course_tags': ('course_id', 'coursetags_id'),
         'application.CourseSubscription_membership_payments': ('coursesubscription_id', 'payment_id'),
         'application.CarnetSubscription_course_subscription': ('carnetsubscription_id', 'coursesubscription_id'),
         'application.Payment_attachments': ('payment_id', 'document_id')}
KEYS = {COURSE: 'course_id', CSM: 'course_subscription_id', CSI: 'course_subscription_installment_id', PAYMENT: 'payment_id',
        AR: 'attendance_registry_id', AD: 'attendance_day_id'}
ROWS = tuple(KEYS)
MANAGED = ROWS + tuple(LINKS) + (harness.AUDIT, harness.INDEX)
NAMES = {'course': ('Course', COURSE), 'coursesubscription': ('CourseSubscription', CSM),
         'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', CSI), 'payment': ('Payment', PAYMENT),
         'attendanceregistry': ('AttendanceRegistry', AR), 'attendanceday': ('AttendanceDay', AD)}
COLOR = re.compile(r'#[0-9a-f]{6}\Z')


def models():
    from application.models import AttendanceDay, AttendanceRegistry, Course, CourseSubscription, CourseSubscriptionInstallment, Payment
    return {COURSE: Course, CSM: CourseSubscription, CSI: CourseSubscriptionInstallment, PAYMENT: Payment, AR: AttendanceRegistry,
            AD: AttendanceDay}


def seed_rows(source, created):
    from application.models import (Associate, AttendanceDay, AttendanceRegistry, Course, CourseLocation, CourseSubscription,
                                    CourseSubscriptionInstallment, CourseTags, Payment, Subscription)

    def insert(model, rows, **extra):
        for row in rows:
            model._base_manager.bulk_create([model(**row, **extra)])
            created.append((model, row[model._meta.pk.attname]))

    insert(Associate, source['associates'].values())
    insert(Subscription, source['subscriptions'].values(), status_flag=Subscription.ACCEPTED)
    insert(CourseLocation, source['locations'].values())
    insert(CourseTags, source['tags'].values())
    insert(Course, source['courses'].values())
    Course.locations.through.objects.bulk_create([Course.locations.through(course_id=c, courselocation_id=l) for c, l in source['course_locations']])
    Course.tags.through.objects.bulk_create([Course.tags.through(course_id=c, coursetags_id=t) for c, t in source['course_tags']])
    insert(Payment, source['payments'].values())
    insert(CourseSubscription, source['course_subscriptions'].values())
    CourseSubscription.membership_payments.through.objects.bulk_create([
        CourseSubscription.membership_payments.through(coursesubscription_id=c, payment_id=p) for c, p in source.get('membership_payments', [])])
    insert(CourseSubscriptionInstallment, source['installments'].values())
    insert(AttendanceRegistry, source['registries'].values())
    insert(AttendanceDay, source['days'].values())


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple((lambda label=label: models()[label])
                                                                                    for label in (COURSE, CSM, CSI, PAYMENT)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from application.models import Payment, PaymentCategory
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        source = contract.load_source()
        category = PaymentCategory.objects.filter(name__iexact=source['category_name']).first()
        contract.require(category is not None, 'fixture.default-category')
        bank = Payment.get_default_bank_account(source['ids']['own_association'])
        fixture['tokens_course_writes'] = {'$category': str(category.pk), '$bank': str(bank.pk) if bank else None}
        yield fixture


def captured(source):
    """auditlog's delete diffs of every row of the managed models, captured before each case."""
    return {str(row.pk): replace.deleted_changes(model, row) for model in models().values() for row in model._base_manager.all()}


def created_changes(model, row):
    """auditlog's create diff: the mirror of the delete diff."""
    return {name: [pair[1], pair[0]] for name, pair in replace.deleted_changes(model, row).items()}


class Binder:
    def __init__(self, tokens, now):
        self.binds, self.tokens, self.now = {}, tokens, now

    def resolve(self, value):
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        if isinstance(value, str):
            if value.startswith('@'):
                return self.binds.get(value, value)
            return self.tokens.get(value, value)
        return value

    def matches(self, expected, actual, final):
        if isinstance(expected, str) and expected.startswith('@') and expected not in self.binds:
            return not final
        if expected == '$now':
            if not isinstance(actual, str):
                return False
            moment = datetime.fromisoformat(actual.replace('Z', '+00:00'))
            return abs((self.now - moment).total_seconds()) < 600
        if expected == '$color':
            return isinstance(actual, str) and COLOR.match(actual) is not None
        return self.resolve(expected) == actual

    def text(self, template, created_times):
        for sym in sorted(self.binds, key=len, reverse=True):
            if '{created:' + sym + '}' in template:
                template = template.replace('{created:' + sym + '}', repr(created_times[self.binds[sym]]))
            template = template.replace(sym, self.binds[sym])
        return template


def rows_of(state, label):
    return {str(periods.plain(row[KEYS[label]])): periods.plain(row) for row in state[label]}


def match_created(effect, before, after, binder):
    pending = []
    for label in ROWS:
        old, new = rows_of(before, label), rows_of(after, label)
        fresh = [key for key in new if key not in old]
        specs = effect['created'].get(label, [])
        contract.require(len(fresh) == len(specs), 'state.created-count:' + label)
        pending += [(label, spec, new) for spec in specs]
    free = {label: [key for key in rows_of(after, label) if key not in rows_of(before, label)] for label in ROWS}
    while pending:
        progress = False
        for item in list(pending):
            label, spec, new = item
            candidates = [key for key in free[label] if set(new[key]) == set(spec) - {'$sym'}
                          and all(binder.matches(value, new[key][column], False) for column, value in spec.items() if column != '$sym')]
            if len(candidates) == 1:
                binder.binds[spec['$sym']] = candidates[0]
                free[label].remove(candidates[0])
                pending.remove(item)
                progress = True
        contract.require(progress, 'state.created-unmatched')
    for label in ROWS:
        new = rows_of(after, label)
        for spec in effect['created'].get(label, []):
            row = new[binder.binds[spec['$sym']]]
            contract.require(all(binder.matches(value, row[column], True) for column, value in spec.items() if column != '$sym'),
                             'state.created-row:' + label)


def check_rows(label, before, after, authored, binder):
    old, new = rows_of(before, label), rows_of(after, label)
    for key, row in old.items():
        if key in authored and authored[key] is None:
            contract.require(key not in new, 'state.deleted:' + label)
        elif key in authored:
            changes = binder.resolve(authored[key])
            contract.require(key in new and all(new[key][c] == v for c, v in changes.items())
                             and all(new[key][c] == row[c] for c in row if c not in changes), 'state.updated:' + label)
        else:
            contract.require(new.get(key) == row, 'state.unchanged:' + label)


def link_pairs(state, label):
    left, right = LINKS[label]
    return {(str(periods.plain(row[left])), str(periods.plain(row[right]))) for row in state[label]}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    binder = Binder(fixture['tokens_course_writes'], datetime.now(timezone.utc))
    match_created(effect, before, after, binder)
    for label in ROWS:
        check_rows(label, before, after, effect['rows'].get(label, {}), binder)
    for label in LINKS:
        authored = effect['links'].get(label, {})
        expected = (link_pairs(before, label) - {tuple(binder.resolve(p)) for p in authored.get('remove', [])}) \
            | {tuple(binder.resolve(p)) for p in authored.get('add', [])}
        contract.require(link_pairs(after, label) == expected, 'state.links:' + label)

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    additional = impersonation_additional(case, context, ids, actor)
    created_times = {str(row.pk): row.creation_date.astimezone(timezone.utc)
                     for row in models()[CSM]._base_manager.filter(pk__in=list(binder.binds.values()))}
    for position, (event, spec) in enumerate(zip(added, effect['audit'])):
        name, label = NAMES[spec['model']]
        key = binder.resolve(spec['key'])
        where = f":{position}:{spec['model']}:{key}"
        representation = re.sub(r' object at 0x[0-9a-f]+>', ' object at 0x?>', event['object_repr'])
        expected_repr = binder.text(spec['repr'], created_times) if spec['repr'] else f'{name} object ({key})'
        contract.require(event['content_type_id'] == types[spec['model']] and event['action'] == spec['action'] and event['object_pk'] == key,
                         'audit.identity' + where)
        contract.require(representation == expected_repr, 'audit.repr' + where)
        changes = spec['changes']
        if '$stored' in changes:
            if changes['$stored'] == 'create':
                model = models()[label]
                base = created_changes(model, model._base_manager.get(pk=key))
            else:
                base = context['prepared'][key]
            changes = {**base, **binder.resolve(changes['overrides'])}
        else:
            changes = binder.resolve(changes)
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


def impersonation_additional(case, context, ids, actor):
    """auditlog's additional_data for a request in an impersonation session (None otherwise)."""
    session = case['headers'].get('X-Impersonation-Id', '')
    if not session.startswith('$session:'):
        return None
    label = session.removeprefix('$session:')
    return {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
            'association_id': None if label.startswith('admin_') else ids['own_association']}


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
