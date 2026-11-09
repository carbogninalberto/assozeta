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
import types

from TESTS.contracts import test_stripe_routes_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/stripe_routes_http.json', 'TESTS/contracts/test_stripe_routes_http.py',
           'TESTS/adapters/django/stripe_routes_http.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/application/views/stripe_views.py', 'BE/application/utils/stripe_utils.py', 'BE/instance/integration_configuration.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/models/payment_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/user_models.py', 'BE/application/impersonation_scope.py',
           'BE/application/impersonation.py', 'BE/application/mixin.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py', 'BE/core/settings.py',
           'BE/core/middleware.py') + harness.SOURCES
PAYMENT, SUPPLIER_INVOICE = 'application.Payment', 'application.InvoiceSuppliers'
MANAGED = (PAYMENT, SUPPLIER_INVOICE, harness.AUDIT, harness.INDEX)
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
    from application.models import (Associate, Invoice, InvoiceSuppliers, Payment, PaymentCategory, SportAssociation, SupplierAndCustomers,
                                    Subscription, User)
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
            creation_date=moment(row['created']), payment_date=moment(row['payment_date']), description=row['description']))
    for label, row in source['supplier_invoices'].items():
        insert(InvoiceSuppliers, InvoiceSuppliers(invoice_supplier_id=row['id'], invoice_identifier=row['identifier'],
                                                  amount=Decimal(row['amount']), sport_association_id=payments[label]['association'],
                                                  supplier_id=vendor['id'], payment_id=payments[label]['id']))


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        fixture['default_category'] = default_category()
        yield fixture


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


def assert_effect(case, before, after, source, context):
    require = contract.require
    check_stripe(case, source)
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    fixture, ids = context['fixture'], source['ids']
    bindings = {'$default_category': fixture['default_category']}

    expected_payments, changed = [], effect['payments_changed']
    by_id = {row['id']: label for label, row in source['payments'].items()}
    for row in map(flat, before[PAYMENT]):
        label = by_id.get(str(row['payment_id']))
        if label in changed:
            row = {**row, 'payment_intent_id': changed[label]['intent']}
            if source['payments'][label]['category'] is None and row['payment_category_id'] is None:
                row['payment_category_id'] = bindings['$default_category']
        expected_payments.append(row)
    actual_payments = [flat(row) for row in after[PAYMENT]]
    normalize = lambda rows: [{**row, 'payment_category_id': None if row['payment_category_id'] is None else str(row['payment_category_id'])}
                              for row in rows]
    require(normalize(actual_payments) == normalize(expected_payments), 'state.payments')

    now_text = {}
    by_invoice = {row['id']: label for label, row in source['supplier_invoices'].items()}
    after_invoices = {str(row['invoice_supplier_id']): row for row in map(flat, after[SUPPLIER_INVOICE])}
    expected_invoices = []
    for row in map(flat, before[SUPPLIER_INVOICE]):
        label = by_invoice.get(str(row['invoice_supplier_id']))
        if label in effect['supplier_invoices_changed']:
            stored = after_invoices.get(str(row['invoice_supplier_id']), {}).get('payment_date')
            require(recent(stored), 'state.supplier-invoice-now')
            now_text[label] = audit_text(stored)
            row = {**row, 'paid': True, 'payment_date': stored}
        expected_invoices.append(row)
    require([flat(row) for row in after[SUPPLIER_INVOICE]] == expected_invoices, 'state.supplier-invoices')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: row['id'])
    require(len(added) == len(effect['audit']), 'audit.count')
    types_ = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    keys = {'payment': lambda label: source['payments'][label]['id'],
            'invoicesuppliers': lambda label: source['supplier_invoices'][label]['id']}
    names = {'payment': 'Payment', 'invoicesuppliers': 'InvoiceSuppliers'}
    for event, spec in zip(added, effect['audit']):
        key = keys[spec['model']](spec['label'])
        require(event['content_type_id'] == types_[spec['model']] and event['action'] == spec['action'] and event['object_pk'] == key
                and event['object_repr'] == f"{names[spec['model']]} object ({key})", 'audit.identity')
        changes = {name: [now_text.get(spec['label'], '<missing now>') if item == '$now' else bindings.get(item, item) for item in pair]
                   for name, pair in spec['changes'].items()}
        require(event['changes'] == changes, 'audit.changes')
        actor = None if spec['actor'] == 'anonymous' else ids[spec['actor']]
        require(event['actor_id'] == actor and event['actor_email'] == (users[actor]['email'] if actor else None), 'audit.actor')
        require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        index = indexes[str(event['id'])]
        require(index['sport_association_id'] == spec['index'][0] and index['resolution_path'] == spec['index'][1], 'index.fields')


run_workload = harness.run_workload_for(contract, assert_effect)
