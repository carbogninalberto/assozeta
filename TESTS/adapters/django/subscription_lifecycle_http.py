"""Parent-only subscription/<uid>/reject, archive and delete HTTP runner; imports are inert.

Seeds associates, a course, tags, payments, subscriptions (with tag links),
course subscriptions and installments (audit disabled) and verifies each case
against the complete model state: exactly the authored subscription, payment,
course subscription and installment rows change or disappear, many-to-many
rows of deleted rows go, SET_NULL payment references of deleted payments are
cleared, and the authored auditlog entries are appended in order (delete
entries rendered from the rows before the request plus the authored in-memory
differences) with their index rows.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import ipaddress
import re

from TESTS.contracts import test_subscription_lifecycle_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_lifecycle_http.json', 'TESTS/contracts/test_subscription_lifecycle_http.py',
           'TESTS/adapters/django/subscription_lifecycle_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/services/subscription_service.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/signals.py', 'BE/application/mixin.py', 'BE/application/permissions.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/utils/api_utils.py') + harness.SOURCES
SUB, PAYMENT, CS, INSTALLMENT = ('application.Subscription', 'application.Payment', 'application.CourseSubscription',
                                 'application.CourseSubscriptionInstallment')
TAG_LINK = 'application.Subscription_tags'
MEMBERSHIP_LINK = 'application.CourseSubscription_membership_payments'
CARNET_LINK = 'application.CarnetSubscription_course_subscription'
ATTACHMENT_LINK = 'application.Payment_attachments'
MANAGED = (SUB, PAYMENT, CS, INSTALLMENT, TAG_LINK, MEMBERSHIP_LINK, CARNET_LINK, ATTACHMENT_LINK, harness.AUDIT, harness.INDEX)
TABLES = {'subscriptions': (SUB, 'subscription_id', 'subscriptions'), 'payments': (PAYMENT, 'payment_id', 'payments'),
          'cs': (CS, 'course_subscription_id', 'course_subscriptions'),
          'installments': (INSTALLMENT, 'course_subscription_installment_id', 'installments')}
MODELS = {'subscription': ('Subscription', 'subscriptions'), 'payment': ('Payment', 'payments'),
          'coursesubscription': ('CourseSubscription', 'course_subscriptions'),
          'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', 'installments')}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk)


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Payment, PaymentCategory,
                                    Subscription, Tags)
    ids = source['ids']
    group = lambda label: ids[label] if label else None

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category'], name='Iscrizioni fixture', sport_association_id=ids['own_association']))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'], last_name=row['last'],
                                    sex='M', is_minor=False))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], course_type=1, title=row['title'], fee=Decimal('50.00')))
    for row in source['tags'].values():
        insert(Tags, Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=ids['own_association'], creation_date=moment('2026-01-01T00:00:00Z')))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
                                paid=row['paid'], archived=row['archived'], deleted=row['deleted'], group_id=group(row['group']),
                                associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None,
                                payment_category_id=source['category'] if row['category'] else None, creation_date=moment(row['created']),
                                description=row['description']))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
                                          associate_id=source['associates'][row['associate']]['id'], status_flag=row['status'],
                                          acceptance_date=day(row['acceptance']), start_date=day(row['start']), end_date=day(row['end']),
                                          creation_date=moment(row['created']), group_id=group(row['group']), archived=row['archived'],
                                          deleted=row['deleted'], payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None))
        Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=row['id'], tags_id=source['tags'][t]['id'])
                                                       for t in row['tags']])
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'], paid=row['paid'], creation_date=moment(row['created']),
            subscription_id=source['subscriptions'][row['subscription']]['id'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, multi_payments=row['multi_payments'],
            one_fee_payment=row['one_fee_payment'], archived=row['archived'], type=row['type'], multiple_quote=row['multiple_quote'],
            billed_frequency=row['billed_frequency'], billed_from=moment(row['billed_from']), billed_until=moment(row['billed_until']),
            billed_from_day_of_month=row['billed_from_day_of_month'], membership_active=row['membership_active'],
            membership_fee=Decimal(row['membership_fee']), auto_renewal=row['auto_renewal'], deleted=row['deleted']))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=row['number'],
            amount=Decimal(row['amount']), paid=False, payment_date=moment(row['payment_date']), archived=row['archived'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, group_id=group(row['group']), deleted=row['deleted']))


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        fixture['default_category'] = default_category()
        yield fixture


def captured(source):
    """auditlog's delete diffs of the seeded rows, captured before each case."""
    from application.models import CourseSubscription, CourseSubscriptionInstallment, Payment, Subscription
    rows = {}
    for model, key in ((Subscription, 'subscriptions'), (Payment, 'payments'), (CourseSubscriptionInstallment, 'installments'),
                       (CourseSubscription, 'course_subscriptions')):
        for row in model._base_manager.filter(pk__in=[r['id'] for r in source[key].values()]):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    return rows


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def bind(value, bindings):
    if isinstance(value, dict):
        return {key: bind(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, bindings) for item in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    bindings = {'$default_category': fixture['default_category']}
    gone = {name: {source[table][label]['id'] for label in effect[name]['deleted']} for name, (_, _, table) in TABLES.items()}
    for name, (model, key, table) in TABLES.items():
        changes = {source[table][label]['id']: bind(columns, bindings) for label, columns in effect[name]['changed'].items()}
        expected = []
        for row in before[model]:
            row = flat(row)
            ident = str(row[key])
            if ident in gone[name]:
                continue
            row = {**row, **changes.get(ident, {})}
            if row.get('payment_id') is not None and str(row['payment_id']) in gone['payments']:
                row['payment_id'] = None
            expected.append(row)
        contract.require([flat(r) for r in after[model]] == expected, 'state.' + name)
    for link, columns in ((TAG_LINK, (('subscription_id', 'subscriptions'),)),
                          (MEMBERSHIP_LINK, (('coursesubscription_id', 'cs'), ('payment_id', 'payments'))),
                          (CARNET_LINK, (('coursesubscription_id', 'cs'),)), (ATTACHMENT_LINK, (('payment_id', 'payments'),))):
        kept = [flat(r) for r in before[link] if not any(str(flat(r)[column]) in gone[name] for column, name in columns)]
        contract.require([flat(r) for r in after[link]] == kept, 'state.' + link)

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, entry) in enumerate(zip(added, effect['audit'])):
        name, table = MODELS[entry['model']]
        key = source[table][entry['label']]['id']
        where = f":{position}:{entry['model']}:{entry['label']}"
        # CourseSubscription.__str__ is str(self.__dict__): only the ModelState address is not reproducible.
        representation = re.sub(r' object at 0x[0-9a-f]+>', ' object at 0x?>', event['object_repr']) if entry['repr'] else event['object_repr']
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and representation == (entry['repr'] or f'{name} object ({key})'), 'audit.identity' + where)
        changes = entry['changes']
        if changes is None:
            changes = {**context['prepared'][key], **entry['overrides']}
        contract.require(event['changes'] == bind(changes, bindings), 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
