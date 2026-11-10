"""Parent-only payment list, payment stats and subscription payments HTTP runner; imports are inert.

Seeds a payment category, two accounts, four documents, five associates (two
tutor relations), an instructor, a supplier, two courses, a carnet, an invoice,
five subscriptions, the authored payments (one attachment), course
subscriptions, installments, a carnet holding and an instructor-hours row with
its document (audit disabled). For the athlete list it also seeds two further
associations with their owners and a group, three athletes (extra_users),
their associates, subscriptions, courses, course subscriptions, installments,
a carnet holding, a camp period, invoices and payments, an access token for the
first owner and an association-scoped impersonation session of that owner for
one athlete. Every case must leave the complete model state unchanged, except
the athlete list's receipts.

The athlete list calls document_invoice in-process for listed paid payments
whose invoice has no PDF. document-render's renderer stub (its
service_context) and the private Celery broker run for the whole run: each
case's renderer requests must be exactly its authored 'renders', and its
'generated' receipts are checked against the complete model state: one new
Document each (the authored filename, a fresh uuid4 id and token), the invoice
linked to it unless the renderer failed, the auditlog entries with their index
rows in Django's order, and the Celery messages (save_to_storage, then the
receipt send_mail_async). '$document:<invoice>' in an authored response is the
Document a previous case generated for that invoice.

A CURRENT_HOST print stub (invoice-sync-print's service_context) runs for the
whole run: each case's outbound requests must be exactly its authored 'prints'
(payment_list prints the listed compensations whose hours have no document).

A Stripe API stub (stripe-routes' handler) also runs for the whole run, with
stripe.api_base and the Go server's STRIPE_API_BASE pointed at it. A case with
'stripe_setup' has, only during its request, a self-hosted InstanceConfiguration
whose Stripe integration holds the fixture keys (enabled or not) and, when
authored, the Django cache entry of the range written with cache.set; its Stripe
requests must be exactly the authored ones and, after it, cache.get of the key
must equal the authored value (expiring within 300 seconds); the key and the
configuration are then removed. Every other case makes no Stripe request.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import base64
import ipaddress
import time
import types
import uuid

from TESTS.contracts import test_payment_reads_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import stripe_routes_http as stripe_routes
from TESTS.adapters.django import document_render_http as renderer
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_reads_http.json', 'TESTS/contracts/test_payment_reads_http.py',
           'TESTS/adapters/django/payment_reads_http.py', 'BE/application/views/payment_views.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/personas_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/stripe_utils.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py',
           'BE/docmanager/models.py', 'TESTS/adapters/django/invoice_sync_print_http.py', 'BE/application/printing_tasks.py',
           'BE/instance/restore/task_base.py', 'TESTS/adapters/django/stripe_routes_http.py',
           'BE/instance/integration_configuration.py', 'BE/instance/models.py',
           'TESTS/adapters/django/document_render_http.py', 'TESTS/contracts/test_document_render_http.py',
           'BE/docmanager/views/printing_views.py', 'BE/application/utils/printing.py', 'BE/docmanager/tasks.py', 'BE/core/tasks.py',
           'BE/application/impersonation.py', 'BE/application/audit_resolvers.py') + harness.SOURCES
DOCUMENT, INVOICE = 'docmanager.Document', 'application.Invoice'
MANAGED = (DOCUMENT, INVOICE, harness.AUDIT, harness.INDEX)
# Tokens and sessions seed_rows creates beyond the shared fixture's; Documents the run generated, by invoice label.
EXTRA = {'tokens': {}, 'sessions': {}}
GENERATED = {}

ASSOCIATE_FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap',
                    'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
                    'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role',
                    'id_number', 'migrated', 'notes')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (Associate, AssociateTutorRelation, Course, CourseSubscription, CourseSubscriptionInstallment,
                                    Instructor, InstructorHours, Invoice, Payment, PaymentCategory, Subscription,
                                    SupplierAndCustomers)
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    documents = source['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], filepath=row['filepath'],
                                  file_size_bytes=row['size'], creation_date=moment(row['created'])))
    category = source['category']
    insert(PaymentCategory, PaymentCategory(payment_category_id=category['id'], name=category['name'], sport_association_id=own))
    accounts = source['accounts']
    for row in accounts.values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], name=row['name'], initial_balance=Decimal('0.00'),
                                              account_type=row['type'], account_code=row['code'], sport_association_id=own))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], user_id=row['user'], sport_association_id=row['association'],
                                    group_id=row['group'], family_id=row['family'], creation_date=moment(row['created']),
                                    born_date=date.fromisoformat(row['born_date']) if row['born_date'] else None,
                                    **{key: row[key] for key in ASSOCIATE_FIELDS}))
    for associate, tutors in source['tutors'].items():
        for tutor, primary in tutors:
            relation = AssociateTutorRelation._base_manager.create(associate_id=associate, tutor_id=tutor, is_primary=primary)
            created.append((AssociateTutorRelation, relation.pk))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'],
                                  last_name=teacher['last_name'], email=teacher['email'], tax_code=teacher['tax_code'],
                                  phone=teacher['phone'], address=teacher['address'],
                                  default_hourly_billing=Decimal(teacher['hourly'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], address=vendor['address'], city=vendor['city'],
                                                      tax_code=vendor['tax_code'], sport_association_id=own))
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, course_type=1, title=row['title'], fee=Decimal('50.00'),
                              deleted=row['deleted']))
    carnet = source['carnet']
    insert(Carnet, Carnet(carnet_id=carnet['id'], title=carnet['title'], sport_association_id=own, fee=Decimal('1.00')))
    bill = source['invoice']
    insert(Invoice, Invoice(invoice_id=bill['id'], document_pdf_id=documents[bill['document']]['id'], sport_association_id=own,
                            creation_date=moment(bill['created']), membership_fee=Decimal(bill['membership_fee']),
                            activity_fee=Decimal(bill['activity_fee']), description=bill['description'], number=bill['number'],
                            meta=bill['meta']))
    through = Invoice.attached_documents.through
    through.objects.bulk_create([through(invoice_id=bill['id'], document_id=documents[bill['extra']]['id'])])
    payments = source['payments']
    for row in payments.values():
        insert(Payment, Payment(
            payment_id=row['id'], type=row['type'], subject=row['subject'], amount=Decimal(row['amount']),
            creation_date=moment(row['created']), payment_date=moment(row['paid_on']), paid=row['paid'], expense=row['expense'],
            description=row['description'], payment_category_id=category['id'] if row['category'] else None,
            meta_payment_categories=row['meta_payment_categories'], invoice_id=bill['id'] if row['invoice'] else None,
            user_id=owner if row['user'] else None, associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            supplier_id=vendor['id'] if row['supplier'] else None, instructor_id=teacher['id'] if row['instructor'] else None,
            sport_association_id=row['association'], payment_intent_id=row['payment_intent_id'], archived=row['archived'],
            invoice_fiscal_code=row['invoice_fiscal_code'], meta=row['meta'], notes=row['notes'],
            custom_accounts_id=accounts[row['account']]['id'] if row['account'] else None, deleted=row['deleted'],
            group_id=row['group'], course=row['course']))
        if row['attachment']:
            link = Payment.attachments.through
            link.objects.bulk_create([link(payment_id=row['id'], document_id=documents[row['attachment']]['id'])])
    subscriptions = source['subscriptions']
    for row in subscriptions.values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, creation_date=moment(row['created']),
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted'],
            status_flag=Subscription.ACCEPTED))
    course_subscriptions = source['course_subscriptions']
    for row in course_subscriptions.values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=courses[row['course']]['id'],
            subscription_id=subscriptions[row['subscription']]['id'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted']))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=course_subscriptions[row['cs']]['id'],
            id=row['number'], amount=Decimal('40.00'), paid=True, payment_date=moment('2024-04-15T09:30:00Z'),
            payment_id=payments[row['payment']]['id'], deleted=row['deleted']))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(
            carnet_subscription_id=row['id'], carnet_id_id=carnet['id'], subscription_id=subscriptions[row['subscription']]['id'],
            payment_id=payments[row['payment']]['id']))
    for row in source['hours'].values():
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=row['id'], instructor_id=teacher['id'], hours=Decimal('2.00'),
            payment_id=payments[row['payment']]['id'], document_id=documents[row['document']]['id'] if row['document'] else None))
    seed_athlete_rows(source, insert, created)


def seed_athlete_rows(source, insert, created):
    """The athlete list's rows: two associations of their own, so no other case lists them."""
    from django.core.cache import cache
    from rest_framework_simplejwt.tokens import AccessToken
    from application.impersonation import session_key, SESSION_TTL
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Group, Invoice, Payment,
                                    SportAssociation, Subscription, User)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.courses_models import (CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsSubscription,
                                                   CampsAndRetreatsSubscriptionPeriod)
    from docmanager.models import Document
    data = source['athlete']
    users = {label: row['id'] for label, row in source['extra_users'].items()}
    EXTRA['tokens'].clear()
    EXTRA['sessions'].clear()
    for label, row in data['owners'].items():
        insert(User, User(user_id=row['id'], username=row['username'], email=row['email'], password='!synthetic', role=User.ASSOCIATION,
                          first_name='Payment', last_name=label, show_zero_payments=row['show_zero_payments'],
                          online_payments=row['online_payments']))
        insert(SportAssociation, SportAssociation(sport_association_id=row['association'], user_id=row['id'],
                                                  denomination=row['denomination'], tax_code=row['tax_code']))
        users[label] = row['id']
        token = AccessToken()
        token['user_id'] = row['id']
        EXTRA['tokens'][label] = str(token)
    group = data['group']
    insert(Group, Group(group_id=group['id'], name=group['name'], sport_association_id=group['association']))
    associates = data['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first_name'],
                                    last_name=row['last_name'], email=row['email'], tax_code=row['tax_code'], deleted=row['deleted'],
                                    is_minor=False, creation_date=moment('2024-01-01T08:00:00Z')))
    documents = data['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'],
                                  creation_date=moment('2024-01-02T08:00:00Z')))
    invoices = data['invoices']
    for row in invoices.values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=row['association'], membership_fee=Decimal('0.00'),
                                activity_fee=Decimal('10.00'), number=1, description='Ricevuta', deleted=row['deleted'],
                                creation_date=moment('2024-01-03T08:00:00Z'),
                                document_pdf_id=documents[row['document']]['id'] if row['document'] else None))
    payments = data['payments']
    for row in payments.values():
        insert(Payment, Payment(
            payment_id=row['id'], sport_association_id=row['association'], type=row['type'], subject=row['subject'],
            amount=Decimal(row['amount']), creation_date=moment(row['created']), payment_date=moment(row['paid_on']), paid=row['paid'],
            expense=row['expense'], description=row['description'], notes=row['notes'], user_id=users[row['user']] if row['user'] else None,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            invoice_id=invoices[row['invoice']]['id'] if row['invoice'] else None, deleted=row['deleted'], archived=row['archived'],
            group_id=row['group'], course=row['course']))
    subscriptions = data['subscriptions']
    for row in subscriptions.values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=users[row['user']],
            associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted'],
            start_date=date.fromisoformat(row['start']) if row['start'] else None,
            end_date=date.fromisoformat(row['end']) if row['end'] else None, creation_date=moment('2024-01-04T08:00:00Z'),
            status_flag=Subscription.ACCEPTED))
    courses = data['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], course_type=1, title=row['title'],
                              fee=Decimal('50.00'), multi_payments=row['multi'], one_fee_payment=row['one_fee'], events=row['events']))
    course_subscriptions = data['course_subscriptions']
    for row in course_subscriptions.values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=courses[row['course']]['id'],
            subscription_id=subscriptions[row['subscription']]['id'], multi_payments=row['multi'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted']))
    for row in data['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=course_subscriptions[row['cs']]['id'],
            id=row['number'], amount=Decimal(row['amount']), paid=row['paid'], payment_date=moment(row['date']),
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted']))
    carnet = data['carnet']
    insert(Carnet, Carnet(carnet_id=carnet['id'], title=carnet['title'], sport_association_id=carnet['association'], fee=Decimal('1.00')))
    for row in data['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(
            carnet_subscription_id=row['id'], carnet_id_id=carnet['id'], subscription_id=subscriptions[row['subscription']]['id'],
            payment_id=payments[row['payment']]['id']))
        through = CarnetSubscription.course_subscription.through
        through.objects.bulk_create([through(carnetsubscription_id=row['id'], coursesubscription_id=course_subscriptions[label]['id'])
                                     for label in row['course_subscriptions']])
    camp = data['camp']
    insert(CampsAndRetreats, CampsAndRetreats(camps_and_retreats_id=camp['id'], title=camp['title'],
                                              sport_association_id=camp['association']))
    period = camp['period']
    insert(CampsAndRetreatsPeriod, CampsAndRetreatsPeriod(
        camps_and_retreats_period_id=period['id'], camps_and_retreats_id=camp['id'], title=period['title'],
        start_date=moment(period['start']), end_date=moment(period['end']), created_at=moment('2024-01-05T08:00:00Z')))
    insert(CampsAndRetreatsSubscription, CampsAndRetreatsSubscription(
        camps_and_retreats_subscription_id=camp['subscription']['id'], camps_and_retreats_id=camp['id'],
        subscription_id=subscriptions[camp['subscription']['subscription']]['id'], created_at=moment('2024-01-05T08:00:00Z')))
    insert(CampsAndRetreatsSubscriptionPeriod, CampsAndRetreatsSubscriptionPeriod(
        camps_and_retreats_subscription_period_id=camp['link']['id'], camps_and_retreats_period_id=period['id'],
        camps_and_retreats_subscription_id=camp['subscription']['id'], payment_id=payments[camp['link']['payment']]['id']))
    # The association-scoped session of an owner for one of its athletes (application.impersonation.begin's record).
    session = data['session']
    identifier = str(uuid.uuid4())
    record = {'actor_id': users[session['actor']], 'target_id': users[session['target']], 'association_id': session['association']}
    contract.require(cache.get(session_key(identifier)) is None, 'fixture.session-collision')
    cache.set(session_key(identifier), record, SESSION_TTL)
    contract.require(cache.get(session_key(identifier)) == record, 'fixture.session')
    EXTRA['sessions'][session['label']] = identifier


@contextmanager
def service_context(report, log):
    """The print stub, the PDF renderer stub and a private Stripe API stub, all visible to Django and the Go server."""
    import os
    import stripe
    import threading
    from http.server import ThreadingHTTPServer
    with printer.service_context(report, log), renderer.renderer_context(report, log):
        server = ThreadingHTTPServer(('127.0.0.1', 0), stripe_routes._StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        stripe_routes.STUB.update(lock=threading.Lock(), log=[], script=[])
        base = f'http://127.0.0.1:{server.server_address[1]}'
        saved_base, saved_env = stripe.api_base, os.environ.get('STRIPE_API_BASE')
        report['service_cleanup']['stripe_stub'] = 'running'
        thread.start()
        try:
            stripe.api_base = base
            os.environ['STRIPE_API_BASE'] = base
            yield
        finally:
            stripe.api_base = saved_base
            if saved_env is None:
                os.environ.pop('STRIPE_API_BASE', None)
            else:
                os.environ['STRIPE_API_BASE'] = saved_env
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            report['service_cleanup']['stripe_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
            stripe_routes.STUB.clear()


@contextmanager
def _stripe_configuration(setup):
    """The case's Stripe integration and cache entry, present only during its request."""
    if setup is None:
        yield
        return
    from django.core.cache import cache
    from instance.integration_configuration import encrypt_secret
    from instance.models import InstanceConfiguration
    keys = base_contract.load_source()['stripe']
    key = setup['cache_key']
    contract.require(not InstanceConfiguration._base_manager.exists(), 'stripe.instance-exists')
    contract.require(cache.get(key) is None, 'stripe.cache-clean')
    row = InstanceConfiguration._base_manager.create(
        domain='payment-reads.example.test', name='Payment reads fixture', self_hosted=True,
        integration_settings={'stripe': {'revision': 1, 'settings': {
            'enabled': setup['enabled'], 'public_key': keys['public_key'], 'secret_key': encrypt_secret(keys['secret_key']),
            'webhook_secret': ''}}})
    try:
        if setup['cache_before'] is not None:
            cache.set(key, setup['cache_before'], 300)
        yield
        value, expected = cache.get(key), setup['cache_after']
        contract.require(value == expected and (expected is None or [type(v) for v in value.values()] == [float, float]
                                                and list(value) == ['total', 'stripe_fee']), 'stripe.cache-value')
        if expected is not None:
            remaining = cache.ttl(key)
            contract.require(remaining is not None and 0 < remaining <= 300, 'stripe.cache-expiry')
    finally:
        cache.delete(key)
        InstanceConfiguration._base_manager.filter(pk=row.pk).delete()


def _generated(value):
    """The authored value with '$document:<invoice>' replaced by the Document generated for that invoice."""
    if isinstance(value, dict):
        return {key: _generated(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_generated(item) for item in value]
    if isinstance(value, str) and value.startswith('$document:'):
        contract.require(value[len('$document:'):] in GENERATED, 'fixture.document-not-generated')
        return GENERATED[value[len('$document:'):]]
    return value


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print, renderer and Stripe stubs scripted for it and their outbound requests checked after."""
    with renderer.STUB['lock']:
        renderer.STUB['log'] = []
        renderer.STUB['script'] = [item['reply'] for item in case['renders']]
    renderer.STUB['token'] = token
    case = {**case, 'response': _generated(case['response'])}
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    with stripe_routes.STUB['lock']:
        stripe_routes.STUB['log'] = []
        stripe_routes.STUB['script'] = case['stripe']
    with _stripe_configuration(case['stripe_setup']):
        status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
        printer.check_prints(case, None)
        stripe_routes.check_stripe(case, base_contract.load_source())
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case)


def _documents():
    from docmanager.models import Document
    return Document


_fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=(_documents,)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    """The shared fixture plus the token and the session seed_athlete_rows created."""
    with _fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        contract.require(len(EXTRA['tokens']) == 2 and len(EXTRA['sessions']) == 1, 'fixture.athlete-identities')
        contract.require(not set(EXTRA['tokens']) & set(fixture['tokens']) and not set(EXTRA['sessions']) & set(fixture['sessions']),
                         'fixture.identity-collision')
        fixture['tokens'] = {**fixture['tokens'], **EXTRA['tokens']}
        fixture['sessions'] = {**fixture['sessions'], **EXTRA['sessions']}
        GENERATED.clear()
        yield fixture


def _uuid4(value):
    contract.require(isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4, 'state.uuid4')
    return value


def assert_effect(case, before, after, source, context):
    """No change at all, or exactly the receipts the athlete list generated."""
    from django.conf import settings
    require = contract.require
    renderer.check_renderer(case)
    fixture, since = context['fixture'], context['prepared']['started']
    generated = case['generated']
    if not generated:
        require(before == after, 'state.changed')
        harness.celery_published(contract, fixture, [], since=since, until=time.time())
        return
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    data, audit = source['athlete'], case['audit']
    actors = {**source['ids'], **{label: row['id'] for label, row in source['extra_users'].items()},
              **{label: row['id'] for label, row in data['owners'].items()}}
    actor = actors[audit['actor']]
    users = keyed(before['application.User'], 'user_id')
    types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    additional = None
    session = case['headers'].get('X-Impersonation-Id')
    if session:
        additional = {'impersonation_session': fixture['sessions'][session.split(':', 1)[1]], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': audit['session_association']}
    old_documents, new_documents = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    require(all(new_documents.get(key) == row for key, row in old_documents.items()), 'state.documents-kept')
    added = {key: row for key, row in new_documents.items() if key not in old_documents}
    require(len(added) == len(generated), 'state.document-count')
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: row['id'])
    require(len(events) == sum(2 if item['linked'] else 1 for item in generated), 'audit.count')

    def check_event(event, app, model, name, key, action, changes, index):
        require(event['content_type_id'] == types_[(app, model)] and event['action'] == action and event['object_pk'] == key
                and event['object_repr'] == f'{name} object ({key})', 'audit.identity:' + model)
        require(event['changes'] == changes, 'audit.changes:' + model)
        require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor:' + model)
        require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                'audit.context:' + model)
        if event['remote_addr'] is not None:
            ipaddress.ip_address(event['remote_addr'])
        found = indexes.get(str(event['id']))
        if index is None:
            require(found is None, 'index.none:' + model)
        else:
            require(found is not None and found['sport_association_id'] == index[0] and found['resolution_path'] == index[1],
                    'index.fields:' + model)

    position, links, published, seen = 0, {}, [], set()
    for item in generated:
        invoice = data['invoices'][item['invoice']]
        doc_id = events[position]['object_pk']
        require(doc_id in added and doc_id not in seen, 'state.document-order')
        seen.add(doc_id)
        document = added[doc_id]
        created = renderer.parse(document['creation_date'])
        require(document['filename'] == item['document'] and document['filepath'] is None and document['file_size_bytes'] is None
                and renderer.recent(created), 'state.document')
        token = _uuid4(document['token'])
        check_event(events[position], 'docmanager', 'document', 'Document', _uuid4(doc_id), 0,
                    {'document_id': ['None', doc_id], 'creation_date': ['None', renderer.naive(created)],
                     'filename': ['None', item['document']], 'token': ['None', token],
                     'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']},
                    (audit['association'], 'actor.sport_association') if audit['association'] else None)
        position += 1
        if not item['linked']:
            require(item['mail'] is None, 'fixture.mail-without-link')
            continue
        check_event(events[position], 'application', 'invoice', 'Invoice', invoice['id'], 1, {'document_pdf': ['None', doc_id]},
                    (invoice['association'], 'direct'))
        position += 1
        links[invoice['id']] = doc_id
        GENERATED[item['invoice']] = doc_id
        stored = f'{created.timestamp()}/{doc_id}/{item["document"]}'
        published.append({'task': 'save_to_storage', 'args': [stored, base64.b64encode(renderer.PDF_BYTES).decode()], 'kwargs': []})
        if item['mail']:
            mail = item['mail']
            message = mail['message'].replace('$app_url', settings.APP_URL).replace('$document', doc_id).replace('$token', token)
            published.append({'task': 'core.tasks.send_mail_async', 'args': [], 'kwargs': [
                ['subject', mail['subject']], ['message', message], ['from_email', settings.DEFAULT_TEAM_EMAIL],
                ['reply_to', [settings.DEFAULT_SUPPORT_EMAIL]], ['recipient_list', [mail['recipient']]], ['html_message', None],
                ['fail_silently', False]]})
    old_invoices, new_invoices = keyed(before[INVOICE], 'invoice_id'), keyed(after[INVOICE], 'invoice_id')
    require(set(old_invoices) == set(new_invoices), 'state.invoice-rows')
    for key, row in old_invoices.items():
        require(new_invoices[key] == ({**row, 'document_pdf_id': links[key]} if key in links else row), 'state.invoice-link')
    harness.celery_published(contract, fixture, published, since=since, until=time.time())


run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})
