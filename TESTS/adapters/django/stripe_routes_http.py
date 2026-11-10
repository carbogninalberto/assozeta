"""Parent-only stripe/* HTTP runner; imports are inert.

service_context owns a private Stripe API stub for the whole run: a local
HTTP server that records every request and answers with the replies authored
on the current case (nothing else is ever contacted). Before either server
starts, Django's SDK is pointed at it (stripe.api_base) and the Go server
receives STRIPE_API_BASE. Each case's outbound requests must be exactly the
authored ones: method, path, form body, Authorization (the fixture key),
Stripe-Version, User-Agent, the X-Stripe-Client-User-Agent fields (the
runtime's lang_version, platform and uname are only checked as strings),
Idempotency-Key (a fresh uuid4 on every POST, reused by a retry, none on GET),
Content-Type, no Stripe-Account/Stripe-Context, and X-Stripe-Client-Telemetry
naming the previous answered request of the same HTTP request (duration only
checked as a non-negative integer).

seed_rows inserts (audit disabled) a self-hosted InstanceConfiguration whose
Stripe integration holds the placeholder keys encrypted like instance
administration stores them, two extra association owners (online payments off;
no stored Stripe methods) with their associations, a payment category, an
associate with a subscription, a supplier, invoices, the authored payments and
supplier invoices. Each case is checked against the complete model state:
only payment_intent_id (and Payment.save()'s default category) of the authored
payments and paid/payment_date of the authored supplier invoices change, and
the new auditlog entries match the authored ones in order, with their index
rows. A '$now' payment date must be recent and equal in the row and the entry.

The paid flow (an unpaid payment without an invoice) is checked with
payment_paid_flow: the created invoice, the payment's paid/payment_date/invoice/type,
the course subscription or installment marked paid, the broadcast rows, the
owner's notification and the print_document_invoice messages on the run's
private Celery broker ('$bypass' is DOCUMENT_BYPASS_TOKEN); CommitObserver
records, when each message reaches the broker, the committed invoice and its
payments' paid/type, which must equal the authored 'committed' values. A course, a carnet,
course subscriptions, an installment and a carnet subscription are seeded for it.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import threading
import time
import types

from TESTS.contracts import test_stripe_routes_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import payment_paid_flow as paid
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/stripe_routes_http.json', 'TESTS/contracts/test_stripe_routes_http.py',
           'TESTS/adapters/django/stripe_routes_http.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/application/views/stripe_views.py', 'BE/application/utils/stripe_utils.py', 'BE/instance/integration_configuration.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/models/payment_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/user_models.py', 'BE/application/impersonation_scope.py',
           'BE/application/impersonation.py', 'BE/application/mixin.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py', 'BE/core/settings.py',
           'BE/core/middleware.py', 'BE/application/models/courses_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/views/payment_views.py') + paid.SOURCES + harness.SOURCES
PAYMENT, SUPPLIER_INVOICE = 'application.Payment', 'application.InvoiceSuppliers'
COURSE_SUBSCRIPTION, INSTALLMENT = 'application.CourseSubscription', 'application.CourseSubscriptionInstallment'
MANAGED = (PAYMENT, SUPPLIER_INVOICE, COURSE_SUBSCRIPTION, INSTALLMENT, *paid.MANAGED, harness.AUDIT, harness.INDEX)
STATE = {}
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
CLIENT_AGENT_KEYS = ['bindings_version', 'lang', 'publisher', 'httplib', 'lang_version', 'platform', 'uname']
STUB = {}


# ---------------------------------------------------------------------------
# Stripe API stub
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': {k: v for k, v in self.headers.items()},
                                'header_names': [k.lower() for k in self.headers.keys()], 'body': body})
            number = len(STUB['log'])
            script = STUB['script']
            reply = script[number - 1] if number <= len(script) else None
        if reply is None:
            status, payload = 500, {'error': {'type': 'api_error', 'message': 'unexpected request'}}
        else:
            status, payload = reply['status'], reply['response']
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Request-Id', f'req_stub_{number}')
        if reply is None:
            self.send_header('Stripe-Should-Retry', 'false')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _answer


@contextmanager
def service_context(report, log):
    import stripe
    server = ThreadingHTTPServer(('127.0.0.1', 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    STUB.update(lock=threading.Lock(), log=[], script=[])
    base = f'http://127.0.0.1:{server.server_address[1]}'
    saved_base, saved_env = stripe.api_base, os.environ.get('STRIPE_API_BASE')
    report['service_cleanup']['stripe_stub'] = 'running'
    thread.start()
    try:
        stripe.api_base = base
        os.environ['STRIPE_API_BASE'] = base
        with paid.channel_layers():
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
        STUB.clear()


def _script(case):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = case['stripe']


def _assert_case(case, base_url, *, token, sessions):
    _script(case)
    return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


# The harness drives this proxy: the stub is scripted with the case's replies before each request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_stripe(case, source):
    require = base_contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['stripe']
    require(len(log) == len(expected), 'stripe.request-count')
    settings = source['stripe']
    keys = []
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        headers = {k.lower(): v for k, v in sent['headers'].items()}
        require(sent['method'] == spec['method'] and sent['path'] == spec['path'], 'stripe.request-line' + where)
        require(sent['body'] == spec['body'].encode(), 'stripe.body' + where)
        require(headers.get('authorization') == 'Bearer ' + settings['secret_key'], 'stripe.authorization' + where)
        require(headers.get('stripe-version') == settings['api_version'], 'stripe.version' + where)
        require(headers.get('user-agent') == settings['user_agent'], 'stripe.user-agent' + where)
        require('stripe-account' not in headers and 'stripe-context' not in headers, 'stripe.account' + where)
        require(sent['header_names'].count('idempotency-key') <= 1, 'stripe.single-key' + where)
        try:
            agent = json.loads(headers.get('x-stripe-client-user-agent', ''))
        except ValueError:
            agent = None
        require(isinstance(agent, dict) and list(agent) == CLIENT_AGENT_KEYS
                and json.dumps(agent) == headers['x-stripe-client-user-agent']
                and [agent[k] for k in CLIENT_AGENT_KEYS[:4]] == ['11.6.0', 'python', 'stripe', 'requests']
                and all(isinstance(agent[k], str) for k in CLIENT_AGENT_KEYS[4:]), 'stripe.client-agent' + where)
        key = headers.get('idempotency-key')
        if spec['method'] == 'POST':
            require(headers.get('content-type') == 'application/x-www-form-urlencoded', 'stripe.content-type' + where)
            require(isinstance(key, str) and UUID4.match(key) is not None, 'stripe.idempotency-key' + where)
            if spec['same_key_as_previous']:
                require(keys and keys[-1] == key, 'stripe.retry-key' + where)
            else:
                require(key not in keys, 'stripe.fresh-key' + where)
        else:
            require(key is None and 'content-type' not in headers, 'stripe.get-headers' + where)
        keys.append(key)
        telemetry = headers.get('x-stripe-client-telemetry')
        if spec['telemetry'] is None:
            require(telemetry is None, 'stripe.no-telemetry' + where)
        else:
            try:
                metrics = json.loads(telemetry or '')
            except ValueError:
                metrics = None
            inner = metrics.get('last_request_metrics') if isinstance(metrics, dict) else None
            require(isinstance(inner, dict) and list(metrics) == ['last_request_metrics']
                    and list(inner) == ['request_id', 'request_duration_ms'] and inner['request_id'] == spec['telemetry']
                    and type(inner['request_duration_ms']) is int and inner['request_duration_ms'] >= 0
                    and json.dumps(metrics) == telemetry, 'stripe.telemetry' + where)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Invoice, InvoiceSuppliers, Payment,
                                    PaymentCategory, SportAssociation, SupplierAndCustomers, Subscription, User)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from instance.integration_configuration import encrypt_secret
    from instance.models import InstanceConfiguration
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        contract.require(row.pk is None or not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    stripe = source['stripe']
    insert(InstanceConfiguration, InstanceConfiguration(
        domain='stripe-routes.example.test', name='Stripe routes fixture', self_hosted=True,
        integration_settings={'stripe': {'revision': 1, 'settings': {
            'enabled': True, 'public_key': stripe['public_key'], 'secret_key': encrypt_secret(stripe['secret_key']),
            'webhook_secret': encrypt_secret(stripe['webhook_secret'])}}}))
    for label, spec in source['associations'].items():
        insert(User, User(user_id=spec['user'], username=f'stripe-routes-{label}', email=f'stripe-routes-{label}@example.test',
                          password='!synthetic', role=User.ASSOCIATION, first_name='Stripe', last_name=label, online_payments=spec['online']))
        insert(SportAssociation, SportAssociation(sport_association_id=spec['association'], user_id=spec['user'],
                                                  denomination=spec['denomination'], tax_code='7152000000' + str(len(label)),
                                                  stripe_available_methods=spec['methods'], checkout_info=spec['checkout']))
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category'], name=source['category_name'], sport_association_id=own))
    person = source['associate']
    insert(Associate, Associate(associate_id=person['id'], sport_association_id=own, first_name=person['first'], last_name=person['last'],
                                sex=person['sex'], tax_code=person['tax'], born_date=datetime.fromisoformat(person['born']).date(),
                                email=person['email'], is_minor=False))
    insert(Subscription, Subscription(subscription_id=source['subscription']['id'], sport_association_id=own, user_id=owner,
                                      associate_id=person['id'], creation_date=moment(source['subscription']['created']),
                                      status_flag=Subscription.ACCEPTED))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    payments = source['payments']
    for label, row in source['invoices'].items():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=payments[label]['association'], membership_fee=Decimal('0.00'),
                                activity_fee=Decimal(payments[label]['amount']), number=row['number'], description='Ricevuta Stripe',
                                creation_date=moment('2026-09-01T08:00:00Z')))
    for label, row in payments.items():
        insert(Payment, Payment(
            payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
            paid=row['paid'], associate_id=person['id'] if row['associate'] else None, user_id=owner if row['user'] else None,
            payment_intent_id=row['intent'], invoice_id=source['invoices'][label]['id'] if row['invoice'] else None,
            group_id=ids[row['group']] if row['group'] else None, deleted=row['deleted'], payment_category_id=row['category'],
            creation_date=moment(row['created']), payment_date=moment(row['payment_date']), description=row['description'],
            meta_payment_categories=row.get('meta_categories'), meta=row.get('meta'), type=row['type']))
    for label, row in source['supplier_invoices'].items():
        insert(InvoiceSuppliers, InvoiceSuppliers(invoice_supplier_id=row['id'], invoice_identifier=row['identifier'],
                                                  amount=Decimal(row['amount']), sport_association_id=payments[label]['association'],
                                                  supplier_id=vendor['id'], payment_id=payments[label]['id']))
    plain = source['associations']['plain']['association']
    insert(Course, Course(course_id=source['course']['id'], sport_association_id=plain, course_type=1, title=source['course']['title'],
                          fee=Decimal('50.00')))
    insert(Carnet, Carnet(carnet_id=source['carnet']['id'], title=source['carnet']['title'], sport_association_id=plain, fee=Decimal('1.00')))
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['course']['id'],
                                                      subscription_id=source['subscription']['id'],
                                                      payment_id=payments[row['payment']]['id'] if row['payment'] else None))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=1,
            amount=Decimal('20.00'), paid=False, payment_date=moment('2026-09-15T08:30:00Z'), payment_id=payments[row['payment']]['id']))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnet']['id'],
                                                      subscription_id=source['subscription']['id'], payment_id=payments[row['payment']]['id']))


def invoices():
    from application.models import Invoice
    return Invoice


def broadcasts():
    from notifications.models import NotificationsBroadcast
    return NotificationsBroadcast


def user_broadcasts():
    from notifications.models import UserNotificationsBroadcast
    return UserNotificationsBroadcast


base_fixture_context = paid.with_notifications(harness.with_celery_broker(harness.fixture_context_for(
    contract, seed_rows, purge=(invoices, user_broadcasts, broadcasts))), lambda fixture: {'owner': base_contract.load_source()['ids']['owner']})


class CommitObserver:
    """Reads the database when each Celery message reaches the broker.

    Redis keyspace notifications for the queue key wake a thread that, for
    every message it has not seen, reads (on its own connection, so only
    committed rows) whether the invoice named by print_document_invoice's first
    argument exists and the paid/type of the payments linked to it. The
    observation is keyed by task id.
    """

    def __init__(self, broker):
        self.broker, self.seen, self.lock = broker, {}, threading.Lock()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.saved = broker.config_get('notify-keyspace-events').get('notify-keyspace-events', '')
        broker.config_set('notify-keyspace-events', 'Kl')
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        contract.require(self.ready.wait(10), 'celery.observer-ready')

    def loop(self):
        from django.db import connection
        pubsub = self.broker.pubsub()
        try:
            pubsub.subscribe(f'__keyspace@0__:{harness.CELERY_QUEUE}')
            self.ready.set()
            while not self.stop.is_set():
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
                if message is not None:
                    self.observe()
        finally:
            pubsub.close()
            connection.close()

    def observe(self):
        import base64
        from application.models import Invoice, Payment
        for raw in self.broker.lrange(harness.CELERY_QUEUE, 0, -1):
            envelope = json.loads(raw)
            task = envelope['headers']['id']
            with self.lock:
                if task in self.seen:
                    continue
            body = json.loads(base64.b64decode(envelope['body']))
            invoice = body[0][0] if body and body[0] else None
            payments = list(Payment._base_manager.filter(invoice_id=invoice).values_list('paid', 'type')) if isinstance(invoice, str) else []
            observed = {'invoice': isinstance(invoice, str) and Invoice._base_manager.filter(pk=invoice).exists(),
                        'payments': [list(item) for item in payments]}
            with self.lock:
                self.seen.setdefault(task, observed)

    def observation(self, task):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lock:
                if task in self.seen:
                    return self.seen[task]
            time.sleep(0.02)
        return None

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)
        self.broker.config_set('notify-keyspace-events', self.saved)
        contract.require(not self.thread.is_alive(), 'celery.observer-stopped')


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        fixture['default_category'] = default_category()
        STATE['fixture'] = fixture
        fixture['commit_observer'] = CommitObserver(fixture['celery_broker'])
        try:
            yield fixture
        finally:
            fixture.pop('commit_observer').close()
            STATE.pop('fixture', None)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def value(item):
    if isinstance(item, dict) and set(item) == {'type', 'value'}:
        return moment(item['value']) if item['type'] == 'datetime' else item['value']
    return item


def flat(row):
    return {key: value(item) for key, item in row.items()}


def audit_text(when):
    """auditlog's str() of a DateTimeField: the naive UTC value."""
    return str(when.astimezone(timezone.utc).replace(tzinfo=None))


def recent(when):
    return isinstance(when, datetime) and abs(datetime.now(timezone.utc) - when) < timedelta(minutes=10)


def side_channels(case, effect, fixture, context, binding, until):
    """The Celery messages and the notification of the case."""
    from django.conf import settings
    since = context['prepared']['started']
    values = {**binding, '$bypass': settings.DOCUMENT_BYPASS_TOKEN,
              '$authorization': None if case['actor'] == 'anonymous' else 'Bearer ' + fixture['tokens'][case['actor']]}
    specs = effect.get('celery', [])
    messages = harness.drain_celery(fixture)
    contract.require(len(messages) == len(specs), 'celery.count')
    for index, (raw, spec) in enumerate(zip(messages, specs)):
        args = [values.get(item, item) if isinstance(item, str) else item for item in spec['args']]
        harness.check_celery_message(contract, raw, {**spec, 'args': args}, since=since, until=until, where=f':{index}')
        if 'committed' in spec:
            # What a worker reading the database sees when the message arrives.
            seen = fixture['commit_observer'].observation(json.loads(raw)['headers']['id'])
            committed = spec['committed']
            contract.require(seen == {'invoice': committed['invoice'], 'payments': [[committed['paid'], committed['type']]]},
                             f'celery.committed:{index}')
    if specs:
        key, member = harness.CELERY_BINDING
        contract.require({m.decode() for m in fixture['celery_broker'].smembers(key)} == {member}, 'celery.binding')
    created = any(item['model'] == 'notificationsbroadcast' for item in effect['audit'])
    paid.check_notification(contract, effect.get('notification'), fixture, context['prepared'], created, until)


def assert_effect(case, before, after, source, context):
    effect = case['effect']
    fixture = context['fixture']
    until = datetime.now(timezone.utc).timestamp()
    binding = {}
    try:
        check_state(case, effect, before, after, source, context, binding)
    except BaseException:
        try:  # drain the side channels, keeping the first failure
            side_channels(case, effect, fixture, context, binding, until)
        except BaseException:
            pass
        raise
    side_channels(case, effect, fixture, context, binding, until)


def check_state(case, effect, before, after, source, context, binding):
    require = contract.require
    check_stripe(case, source)
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    notification = effect.get('notification')
    if notification is not None:
        notification = {**notification, 'user_id': ids[notification['user']]}
    created_broadcast = any(item['model'] == 'notificationsbroadcast' for item in effect['audit'])
    paid.check_broadcasts(contract, before, after, notification, created_broadcast, binding)
    invoice_now = paid.check_invoice(contract, before, after, effect.get('invoice'), [], binding)
    bindings = {'$default_category': fixture['default_category'], **binding}

    after_payments = {str(row['payment_id']): row for row in map(flat, after[PAYMENT])}
    now_text = {}
    expected_payments, changed = [], effect['payments_changed']
    by_id = {row['id']: label for label, row in source['payments'].items()}
    for row in map(flat, before[PAYMENT]):
        label = by_id.get(str(row['payment_id']))
        if label in changed:
            spec = changed[label]
            if 'intent' in spec:
                row = {**row, 'payment_intent_id': spec['intent']}
                if source['payments'][label]['category'] is None and row['payment_category_id'] is None:
                    row['payment_category_id'] = bindings['$default_category']
            if spec.get('paid'):
                stored = after_payments.get(str(row['payment_id']), {}).get('payment_date')
                require(recent(stored), 'state.payment-date-now')
                now_text[label] = audit_text(stored)
                row = {**row, 'paid': True, 'payment_date': stored, 'invoice_id': bindings.get(spec['invoice'], spec['invoice'])}
            if 'type' in spec:
                row = {**row, 'type': spec['type']}
            if 'category' in spec:
                row = {**row, 'payment_category_id': bindings[spec['category']]}
            if 'description' in spec:
                row = {**row, 'description': spec['description']}
        expected_payments.append(row)
    actual_payments = [flat(row) for row in after[PAYMENT]]
    normalize = lambda rows: [{**row, 'payment_category_id': None if row['payment_category_id'] is None else str(row['payment_category_id']),
                               'invoice_id': None if row['invoice_id'] is None else str(row['invoice_id'])} for row in rows]
    require(normalize(actual_payments) == normalize(expected_payments), 'state.payments')

    by_invoice = {row['id']: label for label, row in source['supplier_invoices'].items()}
    after_invoices = {str(row['invoice_supplier_id']): row for row in map(flat, after[SUPPLIER_INVOICE])}
    expected_invoices = []
    for row in map(flat, before[SUPPLIER_INVOICE]):
        label = by_invoice.get(str(row['invoice_supplier_id']))
        if label in effect['supplier_invoices_changed']:
            stored = after_invoices.get(str(row['invoice_supplier_id']), {}).get('payment_date')
            require(recent(stored), 'state.supplier-invoice-now')
            now_text['si:' + label] = audit_text(stored)
            row = {**row, 'paid': True, 'payment_date': stored}
        expected_invoices.append(row)
    require([flat(row) for row in after[SUPPLIER_INVOICE]] == expected_invoices, 'state.supplier-invoices')
    for table, key, spec_key, field in ((COURSE_SUBSCRIPTION, 'course_subscription_id', 'course_subscriptions', 'course_subscriptions_paid'),
                                        (INSTALLMENT, 'course_subscription_installment_id', 'installments', 'installments_paid')):
        marked = {source[spec_key][label]['id'] for label in effect.get(field, [])}
        require([flat(row) for row in after[table]] == [{**row, 'paid': True} if str(row[key]) in marked else row
                                                        for row in map(flat, before[table])], 'state.' + spec_key)

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: row['id'])
    require(len(added) == len(effect['audit']), 'audit.count')
    types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    keys = {'payment': lambda label: source['payments'][label]['id'],
            'invoicesuppliers': lambda label: source['supplier_invoices'][label]['id'],
            'coursesubscription': lambda label: source['course_subscriptions'][label]['id'],
            'coursesubscriptioninstallment': lambda label: source['installments'][label]['id']}
    names = {'payment': 'Payment', 'invoicesuppliers': 'InvoiceSuppliers', 'invoice': 'Invoice',
             'coursesubscriptioninstallment': 'CourseSubscriptionInstallment'}
    created_now = []
    for event, spec in zip(added, effect['audit']):
        where = f":{spec['model']}:{spec['label']}"
        app = 'notifications' if spec['model'] in ('notificationsbroadcast', 'usernotificationsbroadcast') else 'application'
        key = keys[spec['model']](spec['label']) if spec['model'] in keys else binding['$' + spec['model']]
        representation = f"{names.get(spec['model'])} object ({key})"
        if spec['changes'] == '$created':
            changes, representation = paid.broadcast_changes(spec['model'], key)
        else:
            changes = {}
            for name, pair in spec['changes'].items():
                items = []
                for item in pair:
                    if item == '$now' and spec['model'] == 'invoice':
                        value = event['changes'].get(name, [None, None])[len(items)]
                        require(isinstance(value, str) and recent(datetime.fromisoformat(value).replace(tzinfo=timezone.utc)), 'audit.now' + where)
                        created_now.append(value)
                        items.append(value)
                    elif item == '$now':
                        now_key = ('si:' if spec['model'] == 'invoicesuppliers' else '') + spec['label']
                        items.append(now_text.get(now_key, '<missing now>'))
                    else:
                        items.append(bindings.get(item, item))
                changes[name] = items
        if spec['model'] == 'coursesubscription':
            # CourseSubscription.__str__ is str(self.__dict__) (the ModelState address varies).
            text = event['object_repr']
            require(text.startswith("{'_state': <django.db.models.base.ModelState object at 0x")
                    and f"'course_subscription_id': UUID('{key}')" in text and "'paid': True" in text, 'audit.repr' + where)
            representation = text
        require(event['content_type_id'] == types_[(app, spec['model'])] and event['action'] == spec['action'] and event['object_pk'] == key
                and event['object_repr'] == representation, 'audit.identity' + where)
        require(event['changes'] == changes, 'audit.changes' + where + repr((event['changes'], changes)))
        actor = None if spec['actor'] == 'anonymous' else ids[spec['actor']]
        require(event['actor_id'] == actor and event['actor_email'] == (users[actor]['email'] if actor else None), 'audit.actor')
        require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            require(index is None, 'index.absent' + where)
        else:
            require(index is not None and index['sport_association_id'] == spec['index'][0]
                    and index['resolution_path'] == spec['index'][1], 'index.fields' + where)
    require(len(set(created_now)) <= 1, 'audit.now-consistent')
    if created_now and invoice_now is not None:
        require(created_now[0] == invoice_now, 'audit.now-stored')


def prepare(source):
    return paid.prepare(STATE['fixture'])


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
