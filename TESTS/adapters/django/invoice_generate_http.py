"""Parent-only payment generate-invoice and invoiced approve dispatch HTTP runner; imports are inert.

Seeds a second association owner whose invoice date follows the payment date
(with a token), a payment category, a document, a supplier, associates,
invoices, the authored payments, subscriptions, a membership, courses with a
course subscription, an installment and a carnet subscription (audit
disabled); '$year:' / '$lastyear:' dates are resolved at seed time like the
contract does. Each case is checked against the complete model state: only
payments, invoices and audit rows change; changed payments only in the
authored columns; at most one created invoice with the authored stored
values; the new auditlog entries in order with their index rows; and the
Celery messages on the run's private broker are exactly the authored ones
('$invoice' is the created invoice, '$authorization' the request's header).
Invoices the run creates are removed before the fixture cleanup.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import time

from TESTS.contracts import test_invoice_generate_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.course_subscriptions_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/invoice_generate_http.json', 'TESTS/contracts/test_invoice_generate_http.py',
           'TESTS/adapters/django/invoice_generate_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/payment_views.py', 'BE/application/utils/payments_utils.py', 'BE/application/services/invoice_service.py',
           'BE/application/printing_tasks.py', 'BE/application/utils/api_utils.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/models/user_models.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py',
           'BE/core/celery.py', 'BE/instance/restore/task_base.py', 'BE/docmanager/models.py') + harness.SOURCES
PAYMENT, INVOICE = 'application.Payment', 'application.Invoice'
MANAGED = (PAYMENT, INVOICE, harness.AUDIT, harness.INDEX)


def moment(text):
    return datetime.fromisoformat(contract.dated(text).replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Invoice, Payment, PaymentCategory,
                                    SportAssociation, Subscription, SubscriptionMembership, SupplierAndCustomers, User)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    flag = source['flag_owner']
    insert(User, User(user_id=flag['id'], username=flag['username'], email=flag['email'], password='!synthetic', role=User.ASSOCIATION,
                      first_name=flag['first'], last_name=flag['last'], payment_date_equal_invoice_date=True,
                      starting_number_invoices=flag['starting_number']))
    insert(SportAssociation, SportAssociation(sport_association_id=flag['association'], user_id=flag['id'], denomination=flag['denomination'],
                                              tax_code='79900000002'))
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category']['id'], name=source['category']['name'],
                                            sport_association_id=ids['own_association'], creation_date=moment('2026-01-01T00:00:00Z')))
    doc = source['document']
    insert(Document, Document(document_id=doc['id'], token=doc['token'], filename=doc['filename'], filepath=doc['filepath'],
                              file_size_bytes=doc['size'], creation_date=moment(doc['created'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=ids['own_association']))
    owners = {ids['own_association']: ids['owner'], ids['foreign_association']: ids['foreign_owner'], flag['association']: flag['id']}
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=owners[row['association']],
                                    first_name=row['first'], last_name=row['last'], sex='M', born_date=date(1990, 5, 10), is_minor=False,
                                    group_id=ids[row['group']] if row['group'] else None))
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=row['association'], creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership_fee']), activity_fee=Decimal(row['activity_fee']),
                                description=row['description'], number=row['number'], archived=row['archived'], deleted=row['deleted'],
                                group_id=row['group'], document_pdf_id=doc['id'] if row['document'] else None))
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in ('creation_date', 'payment_date'):
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                          associate_id=row['associate'], payment_id=row['payment'], start_date=day(row['start']),
                                          end_date=day(row['end']), group_id=row['group'], creation_date=moment(row['created']),
                                          status_flag=Subscription.ACCEPTED))
    for row in source['memberships'].values():
        insert(SubscriptionMembership, SubscriptionMembership(subscription_membership_id=row['id'], subscription_id=row['subscription'],
                                                              sport_association_id=ids['own_association'], start_date=day(row['start']),
                                                              end_date=day(row['end']), membership_type=row['type']))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=ids['own_association'], title=row['title'], description='',
                              fee=Decimal('50.00'), status_flag=Course.ACTIVE))
    payments, subs = source['payments'], source['subscriptions']
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      subscription_id=subs[row['subscription']]['id'],
                                                      payment_id=payments[row['payment']]['id'] if row['payment'] else None))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['course_subscription']]['id'],
            amount=Decimal('20.00'), payment_date=moment('2026-02-01T00:00:00Z'), payment_id=payments[row['payment']]['id']))
    for row in source['carnets'].values():
        insert(Carnet, Carnet(carnet_id=row['id'], sport_association_id=ids['own_association'], title=row['title'], description='',
                              lessons_number=10, fee=Decimal('60.00'), public=False, meta=None))
    for row in source['carnet_subscriptions'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnets'][row['carnet']]['id'],
                                                      user_id_id=ids['owner'], subscription_id=subs[row['subscription']]['id'],
                                                      payment_id=payments[row['payment']]['id'], disabled=False, meta=None))


def invoices():
    from application.models import Invoice
    return Invoice


base_fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=(invoices,)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from rest_framework_simplejwt.tokens import AccessToken
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'] = category
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        token = AccessToken()
        token['user_id'] = contract.load_source()['flag_owner']['id']
        fixture['tokens'] = {**fixture['tokens'], 'flag_owner': str(token)}
        yield fixture


def prepare(source):
    return {'started': time.time()}


def recent(value):
    return value is not None and abs(datetime.now(timezone.utc) - value) < timedelta(minutes=10)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture = context['fixture']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    old_invoices, new_invoices = keyed(before[INVOICE], 'invoice_id'), keyed(after[INVOICE], 'invoice_id')
    fresh = sorted(set(new_invoices) - set(old_invoices))
    created = effect['invoice'] if effect else None
    contract.require(len(fresh) == (1 if created else 0) and set(old_invoices) <= set(new_invoices), 'state.invoice-count')
    invoice_id = fresh[0] if fresh else None
    if invoice_id:
        contract.require(contract.INVOICE and set(contract.INVOICE) == {invoice_id}, 'response.invoice-id')
    values = {'$invoice': invoice_id, '$default_category': fixture['default_category']}
    if case['actor'] != 'anonymous':
        values['$authorization'] = 'Bearer ' + fixture['tokens'][case['actor']]

    def resolve(item):
        if isinstance(item, list):
            return [resolve(x) for x in item]
        return values.get(item, item) if isinstance(item, str) else item

    celery = [{**spec, 'args': resolve(spec['args'])} for spec in effect['celery']] if effect else []
    harness.celery_published(contract, fixture, celery, since=since, until=until)
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    for key, row in old_invoices.items():
        contract.require(plain(new_invoices[key]) == plain(row), 'state.invoice-unchanged')
    if created:
        stored = plain(new_invoices[invoice_id])
        expected = {**created, 'invoice_id': invoice_id, 'document_pdf_id': None, 'archived': False, 'cancelled': False, 'selected_tutor_id': None,
                    'meta': None, 'imported_from_associami': False, 'deleted': False}
        for column, value in expected.items():
            actual = stored.get(column)
            if column == 'creation_date':
                if value == '$now':
                    contract.require(recent(stamp(actual)), 'state.invoice-now')
                    if contract.NOW:
                        contract.require(set(contract.NOW) == {actual}, 'response.now-stored')
                else:
                    contract.require(stamp(actual) == stamp(value), 'state.invoice-date')
            else:
                contract.require(actual == value, 'state.invoice-column:' + column)
        contract.require(set(stored) == set(expected), 'state.invoice-columns')
    labels = {row['id']: label for label, row in source['payments'].items()}
    old, new = keyed(before[PAYMENT], 'payment_id'), keyed(after[PAYMENT], 'payment_id')
    contract.require(set(old) == set(new), 'state.payment-count')
    for key, row in old.items():
        expected = plain(dict(row))
        for column, value in effect['changed'].get(labels.get(key), {}).items():
            expected[column] = resolve(value)
        contract.require(plain(new[key]) == expected, 'state.payment:' + labels.get(key, key))
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: int(row['id']))
    contract.require(len(events) == len(effect['audit']) and len(indexes) == len(events), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    ids = {**source['ids'], 'flag_owner': source['flag_owner']['id']}
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    created_now = []

    def audit_value(item):
        if item == '$now':
            return item
        return resolve(item)

    for event, spec in zip(events, effect['audit']):
        key = invoice_id if spec['key'] == '$invoice' else source['payments'][spec['key']]['id']
        where = f":{spec['model']}:{spec['action']}"
        changes = {column: [audit_value(item) for item in pair] for column, pair in spec['changes'].items()}
        actual = dict(event['changes'])
        for column, pair in changes.items():
            for index, item in enumerate(pair):
                if item == '$now':
                    value = actual.get(column, [None, None])[index]
                    contract.require(isinstance(value, str) and recent(datetime.fromisoformat(value).replace(tzinfo=timezone.utc)),
                                     'audit.now' + where)
                    created_now.append(value)
                    pair[index] = value
        contract.require(actual == changes, 'audit.changes' + where)
        representation = 'Invoice' if spec['model'] == 'invoice' else 'Payment'
        contract.require(event['content_type_id'] == types[spec['model']] and event['action'] == spec['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{representation} object ({key})', 'audit.identity' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                         and index['resolution_path'] == spec['index'][1], 'index.fields' + where)
    contract.require(len(set(created_now)) <= 1, 'audit.now-consistent')
    if created_now and created and created['creation_date'] == '$now':
        contract.require(created_now[0] == auditlog_moment(stamp(plain(new_invoices[invoice_id])['creation_date'])), 'audit.now-stored')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
