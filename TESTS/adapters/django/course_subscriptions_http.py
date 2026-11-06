"""Parent-only course-subscriptions list, update, delete and bulk-delete HTTP runner; imports are inert.

Seeds associates, medical certificates, subscriptions, courses, payments (one
with an attachment), course subscriptions with their membership payments,
installments and carnet holdings (audit disabled), and verifies each case
against the complete model state: reads change nothing; writes change exactly
the authored course subscription, installment and payment rows, remove the
many-to-many rows of deleted rows, clear SET_NULL references to deleted
payments and append the authored auditlog entries in order (delete entries
rendered from the rows before the request) with their index rows.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import ipaddress
import re

from TESTS.contracts import test_course_subscriptions_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_subscriptions_http.json', 'TESTS/contracts/test_course_subscriptions_http.py',
           'TESTS/adapters/django/course_subscriptions_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/signals.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/utils/printing.py',
           'BE/docmanager/models.py') + harness.SOURCES
CS, INSTALLMENT, PAYMENT = 'application.CourseSubscription', 'application.CourseSubscriptionInstallment', 'application.Payment'
MEMBERSHIP_LINK = 'application.CourseSubscription_membership_payments'
CARNET_LINK = 'application.CarnetSubscription_course_subscription'
ATTACHMENT_LINK = 'application.Payment_attachments'
MANAGED = (CS, INSTALLMENT, PAYMENT, MEMBERSHIP_LINK, CARNET_LINK, ATTACHMENT_LINK, harness.AUDIT, harness.INDEX)
CS_COLUMNS = {'deleted': 'deleted', 'paid': 'paid', 'multiple_quote': 'multiple_quote', 'billed_frequency': 'billed_frequency',
              'created': 'creation_date', 'membership_fee': 'membership_fee', 'subscription': 'subscription_id', 'course': 'course_id',
              'billed_from': 'billed_from', 'billed_until': 'billed_until', 'one_fee_payment': 'one_fee_payment'}
PAYMENT_COLUMNS = {'amount': 'amount', 'meta': 'meta', 'category': 'payment_category_id', 'created': 'creation_date',
                   'payment_date': 'payment_date', 'description': 'description'}
MODELS = {'payment': ('Payment', PAYMENT, 'payments'), 'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', INSTALLMENT, 'installments'),
          'coursesubscription': ('CourseSubscription', CS, 'course_subscriptions')}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk), row.name


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, MedicalCertificate, Payment,
                                    PaymentCategory, Subscription)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    group = lambda label: ids[label] if label else None

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    document = source['document']
    insert(Document, Document(document_id=document['id'], token=document['token'], filename=document['filename'], filepath=document['filepath'],
                              file_size_bytes=document['size'], creation_date=moment(document['created'])))
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category'], name='Corsi fixture', sport_association_id=ids['own_association']))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'], last_name=row['last'],
                                    sex=row['sex'], tax_code=row['tax'], born_date=day(row['born']), email=row['email'], is_minor=False))
    for row in source['medicals'].values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], expiration_date=day(row['expiration']),
                                                      competitive_medical_certificate=row['competitive']))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
                                          associate_id=source['associates'][row['associate']]['id'],
                                          medical_id=source['medicals'][row['medical']]['id'] if row['medical'] else None,
                                          start_date=day(row['start']), end_date=day(row['end']), creation_date=moment(row['created']),
                                          group_id=group(row['group']), status_flag=Subscription.ACCEPTED))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], course_type=row['type'], title=row['title'],
                              deleted=row['deleted'], group_id=group(row['group']), fee=Decimal('50.00')))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
                                paid=row['paid'], meta=row['meta'], associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None,
                                user_id=row['user'], group_id=group(row['group']), deleted=row['deleted'],
                                payment_category_id=row['category'], creation_date=moment(row['created']), payment_date=moment(row['payment_date']),
                                description=row['description']))
        if row['attachment']:
            Payment.attachments.through.objects.bulk_create([Payment.attachments.through(payment_id=row['id'], document_id=document['id'])])
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'], paid=row['paid'], creation_date=moment(row['created']),
            subscription_id=source['subscriptions'][row['subscription']]['id'] if row['subscription'] else None,
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, multi_payments=row['multi_payments'],
            one_fee_payment=row['one_fee_payment'], archived=row['archived'], type=row['type'], multiple_quote=row['multiple_quote'],
            billed_frequency=row['billed_frequency'], billed_from=moment(row['billed_from']), billed_until=moment(row['billed_until']),
            billed_from_day_of_month=row['billed_from_day_of_month'], membership_active=row['membership_active'],
            membership_fee=Decimal(row['membership_fee']), auto_renewal=row['auto_renewal'], deleted=row['deleted']))
        CourseSubscription.membership_payments.through.objects.bulk_create([CourseSubscription.membership_payments.through(
            coursesubscription_id=row['id'], payment_id=source['payments'][p]['id']) for p in row['membership_payments']])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=row['number'],
            amount=Decimal(row['amount']), paid=False, payment_date=moment(row['payment_date']),
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, group_id=group(row['group']), deleted=row['deleted']))
    for row in source['carnets'].values():
        insert(Carnet, Carnet(carnet_id=row['id'], title=row['title'], lessons_number=row['lessons'], sport_association_id=ids['own_association'],
                              fee=Decimal('1.00')))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnets'][row['carnet']]['id'],
                                                      disabled=row['disabled'], meta=row['meta']))
        # auto_now_add overrides the value on insert.
        CarnetSubscription._base_manager.filter(pk=row['id']).update(creation_date=moment(row['created']))
        CarnetSubscription.course_subscription.through.objects.bulk_create([CarnetSubscription.course_subscription.through(
            carnetsubscription_id=row['id'], coursesubscription_id=source['course_subscriptions'][cs]['id']) for cs in row['cs']])


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'] = category
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        yield fixture


def captured(source):
    """auditlog's delete diffs of the seeded rows, captured before each case."""
    from application.models import CourseSubscription, CourseSubscriptionInstallment, Payment
    rows = {}
    for model, key in ((Payment, 'payments'), (CourseSubscriptionInstallment, 'installments'), (CourseSubscription, 'course_subscriptions')):
        for row in model._base_manager.filter(pk__in=[r['id'] for r in source[key].values()]):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    return rows


def flat(row):
    return {key: (value['value'] if isinstance(value, dict) and set(value) == {'type', 'value'} else value) for key, value in row.items()}


def bind(value, bindings):
    if isinstance(value, dict):
        return {key: bind(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, bindings) for item in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def expected_rows(before, key, removed, changes, cleared):
    """before rows without removed keys, with column changes and SET_NULL payment references cleared."""
    result = []
    for row in before:
        row = flat(row)
        ident = str(row[key])
        if ident in removed:
            continue
        row = {**row, **changes.get(ident, {})}
        if 'payment_id' in row and row['payment_id'] is not None and str(row['payment_id']) in cleared:
            row['payment_id'] = None
        result.append(row)
    return result


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    bindings = {'$default_category': fixture['default_category']}
    cs_id = lambda label: source['course_subscriptions'][label]['id']
    pay_id = lambda label: source['payments'][label]['id']
    gone_cs = {cs_id(label) for label in effect['cs_deleted']}
    gone_installments = {source['installments'][label]['id'] for label in effect['installments_deleted']}
    gone_payments = {pay_id(label) for label in effect['payments_deleted']}

    cs_changes = {}
    for label, fields in effect['cs_changed'].items():
        values = {}
        for name, value in fields.items():
            if name == 'subscription':
                value = source['subscriptions'][value]['id'] if value else None
            elif name == 'course':
                value = source['courses'][value]['id']
            values[CS_COLUMNS[name]] = value
        cs_changes[cs_id(label)] = values
    contract.require([flat(r) for r in after[CS]] == expected_rows(before[CS], 'course_subscription_id', gone_cs, cs_changes, gone_payments),
                     'state.course-subscriptions')
    contract.require([flat(r) for r in after[INSTALLMENT]] == expected_rows(before[INSTALLMENT], 'course_subscription_installment_id',
                                                                             gone_installments, {}, gone_payments), 'state.installments')
    payment_changes = {pay_id(label): {PAYMENT_COLUMNS[name]: bind(value, bindings) for name, value in fields.items()}
                       for label, fields in effect['payments_changed'].items()}
    contract.require([flat(r) for r in after[PAYMENT]] == expected_rows(before[PAYMENT], 'payment_id', gone_payments, payment_changes, set()),
                     'state.payments')
    kept = lambda rows, left, right, gone_left, gone_right: [flat(r) for r in rows if str(flat(r)[left]) not in gone_left
                                                             and str(flat(r)[right]) not in gone_right]
    contract.require([flat(r) for r in after[MEMBERSHIP_LINK]] == kept(before[MEMBERSHIP_LINK], 'coursesubscription_id', 'payment_id', gone_cs,
                                                                         gone_payments), 'state.membership-links')
    contract.require([flat(r) for r in after[CARNET_LINK]] == kept(before[CARNET_LINK], 'coursesubscription_id', 'carnetsubscription_id', gone_cs,
                                                                     set()), 'state.carnet-links')
    contract.require([flat(r) for r in after[ATTACHMENT_LINK]] == kept(before[ATTACHMENT_LINK], 'payment_id', 'document_id', gone_payments,
                                                                         set()), 'state.attachment-links')

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
    for event, entry in zip(added, effect['audit']):
        name, _, table = MODELS[entry['model']]
        key = source[table][entry['label']]['id']
        where = f":{entry['model']}:{entry['label']}"
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
