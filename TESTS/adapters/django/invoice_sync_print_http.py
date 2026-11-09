"""Parent-only invoice/<uid>/update and invoice-customers/add HTTP runner; imports are inert.

service_context owns a private print stub for the whole run: a local HTTP
server standing in for CURRENT_HOST that records every request and answers
with the replies authored on the current case. Before either server starts,
Django's printing_tasks.CURRENT_HOST is pointed at it and the Go server
receives CURRENT_HOST (GO_FIXTURE_ENVIRONMENT). Each case's outbound requests
must be exactly the authored ones: GET, the path and query, requests' default
headers (python-requests User-Agent, 'gzip, deflate', '*/*', keep-alive), the
request's Authorization header, Host, no body; header order is not compared.

A case's 'restore' row (instance.DataRestore, state and completed_at) exists
only while its request runs: a completed restore makes the eager
CoordinatedTask stale (no outbound request).

seed_rows inserts (audit disabled) a payment category, two documents (one
attached to an invoice), a supplier, an instructor, associates, invoices,
payments, subscriptions, a membership, courses with a course subscription, an
installment and a carnet subscription. Each case is checked against the
complete model state: only payments, invoices, customer invoices and audit
rows change; changed rows only in the authored columns; at most one created
customer invoice with the authored stored values, its XML and JSON equal to
generate_invoice_xml/json of the authored in-memory values with
ProgressivoInvio normalized (each value checked as base-43 of the request
time << 16 plus a per-second sequence, the JSON's following the XML's); the
new auditlog entries in order with their index rows.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import threading
import time
import types

from TESTS.contracts import test_invoice_sync_print_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.course_subscriptions_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/invoice_sync_print_http.json', 'TESTS/contracts/test_invoice_sync_print_http.py',
           'TESTS/adapters/django/invoice_sync_print_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/course_subscriptions_http.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/application/views/invoice_views.py', 'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py',
           'BE/instance/restore/locking.py', 'BE/instance/models.py', 'BE/core/celery.py', 'BE/application/utils/payments_utils.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/application/models/payment_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/courses_models.py', 'BE/application/models/carnet_models.py', 'BE/application/models/user_models.py',
           'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py', 'BE/application/utils/api_utils.py', 'BE/docmanager/models.py',
           'BE/core/settings.py') + harness.SOURCES
PAYMENT, INVOICE, CUSTOMER = 'application.Payment', 'application.Invoice', 'application.CustomerInvoice'
MANAGED = (PAYMENT, INVOICE, CUSTOMER, harness.AUDIT, harness.INDEX)
USER_AGENT = 'python-requests/2.34.2'
PROGRESSIVO_XML = re.compile(r'<ProgressivoInvio>([^<]*)</ProgressivoInvio>')
PROGRESSIVO_JSON = re.compile(r'"progressivo_invio": "([^"]*)"')
PROGRESSIVO_CHARS = '+-./0123456789=@ABCDEFGHIJKLMNOPQRSTUVWXYZ_'
STUB = {}


# ---------------------------------------------------------------------------
# CURRENT_HOST stub
# ---------------------------------------------------------------------------

class _PrintHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': list(self.headers.items()), 'body': body})
            number = len(STUB['log'])
            script = STUB['script']
            reply = script[number - 1] if number <= len(script) else None
        if reply is None:
            reply = {'status': 500, 'body': 'unexpected request'}
        data = reply.get('body', '').encode()
        self.send_response(reply['status'])
        if 'location' in reply:
            self.send_header('Location', reply['location'])
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer


@contextmanager
def service_context(report, log):
    from application import printing_tasks
    server = ThreadingHTTPServer(('127.0.0.1', 0), _PrintHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    STUB.update(lock=threading.Lock(), log=[], script=[])
    host = f'http://127.0.0.1:{server.server_address[1]}'
    STUB['host'] = host.removeprefix('http://')
    saved_host, saved_env = printing_tasks.CURRENT_HOST, os.environ.get('GO_FIXTURE_ENVIRONMENT')
    report['service_cleanup']['print_stub'] = 'running'
    thread.start()
    try:
        printing_tasks.CURRENT_HOST = host
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**json.loads(saved_env or '{}'), 'CURRENT_HOST': host})
        yield
    finally:
        printing_tasks.CURRENT_HOST = saved_host
        if saved_env is None:
            os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
        else:
            os.environ['GO_FIXTURE_ENVIRONMENT'] = saved_env
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        report['service_cleanup']['print_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        STUB.clear()


@contextmanager
def _restore_row(spec):
    """The case's DataRestore receipt, present only during the request."""
    if spec is None:
        yield
        return
    from auditlog.context import disable_auditlog
    from instance.models import DataRestore
    source = base_contract.load_source()
    completed = datetime.fromisoformat(spec['completed_at'].replace('Z', '+00:00')) if spec['completed_at'] else None
    with disable_auditlog():
        row = DataRestore(owner_id=source['ids']['owner'], association_id=source['ids']['own_association'], state=spec['state'],
                          stage='finished', completed_at=completed, sha256='0' * 64)
        DataRestore._base_manager.bulk_create([row])
    try:
        yield
    finally:
        with disable_auditlog():
            DataRestore._base_manager.filter(pk=row.pk).delete()
        base_contract.require(not DataRestore._base_manager.exists(), 'fixture.restore-removed')


def _assert_case(case, base_url, *, token, sessions):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = [item['reply'] for item in case['prints']]
    STUB['token'] = token
    with _restore_row(case['restore']):
        return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


# The harness drives this proxy: the stub is scripted and the restore row exists for the request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_prints(case, customer):
    import requests
    require = base_contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['prints']
    require(len(log) == len(expected), 'print.request-count')
    require(requests.utils.default_user_agent() == USER_AGENT, 'print.requests-version')
    headers = [('host', STUB['host']), ('user-agent', USER_AGENT), ('accept-encoding', 'gzip, deflate'), ('accept', '*/*'),
               ('connection', 'keep-alive')]
    if STUB['token'] is not None:
        headers.append(('authorization', 'Bearer ' + STUB['token']))
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        require(sent['method'] == 'GET' and sent['path'] == spec['path'].replace('$customer', str(customer)), 'print.request-line' + where)
        require(sorted((name.lower(), value) for name, value in sent['headers']) == sorted(headers), 'print.headers' + where)
        require(sent['body'] == b'', 'print.body' + where)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, Invoice, Payment,
                                    PaymentCategory, Subscription, SubscriptionMembership, SupplierAndCustomers)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    own = ids['own_association']

    def insert(model, row):
        base_contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category']['id'], name=source['category']['name'],
                                            sport_association_id=own, creation_date=moment('2026-01-01T00:00:00Z')))
    for doc in source['documents'].values():
        insert(Document, Document(document_id=doc['id'], token=doc['token'], filename=doc['filename'], filepath=doc['filepath'],
                                  file_size_bytes=doc['size'], creation_date=moment(doc['created'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=ids['owner'], first_name=teacher['first'], last_name=teacher['last']))
    owners = {own: ids['owner'], ids['foreign_association']: ids['foreign_owner']}
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=owners[row['association']],
                                    first_name=row['first'], last_name=row['last'], sex='M', born_date=date(1990, 5, 10), is_minor=False,
                                    group_id=row['group'], deleted=row['deleted']))
    associates = source['associates']
    pdf, attached = source['documents']['pdf'], source['documents']['attached']
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=row['association'], creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership_fee']), activity_fee=Decimal(row['activity_fee']),
                                description=row['description'], number=row['number'], archived=row['archived'], deleted=row['deleted'],
                                cancelled=row['cancelled'], group_id=row['group'], document_pdf_id=pdf['id'] if row['document'] else None,
                                selected_tutor_id=associates[row['tutor']]['id'] if row['tutor'] else None))
        if row['attached']:
            through = Invoice.attached_documents.through
            through.objects.bulk_create([through(invoice_id=row['id'], document_id=attached['id'])])
            created.append((through, through.objects.get(invoice_id=row['id'], document_id=attached['id']).pk))
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in ('creation_date', 'payment_date'):
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))
    payments, subs = source['payments'], source['subscriptions']
    for row in subs.values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                          associate_id=row['associate'], payment_id=row['payment'], start_date=day(row['start']),
                                          end_date=day(row['end']), group_id=row['group'], creation_date=moment(row['created']),
                                          status_flag=Subscription.ACCEPTED))
    for row in source['memberships'].values():
        insert(SubscriptionMembership, SubscriptionMembership(subscription_membership_id=row['id'], subscription_id=row['subscription'],
                                                              sport_association_id=own, start_date=day(row['start']),
                                                              end_date=day(row['end']), membership_type=row['type']))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, title=row['title'], description='', fee=Decimal('50.00'),
                              status_flag=Course.ACTIVE))
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      subscription_id=subs[row['subscription']]['id'],
                                                      payment_id=payments[row['payment']]['id'] if row['payment'] else None))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['course_subscription']]['id'],
            amount=Decimal('20.00'), payment_date=moment('2026-02-01T00:00:00Z'), payment_id=payments[row['payment']]['id']))
    for row in source['carnets'].values():
        insert(Carnet, Carnet(carnet_id=row['id'], sport_association_id=own, title=row['title'], description='', lessons_number=10,
                              fee=Decimal('60.00'), public=False, meta=None))
    for row in source['carnet_subscriptions'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnets'][row['carnet']]['id'],
                                                      user_id_id=ids['owner'], subscription_id=subs[row['subscription']]['id'],
                                                      payment_id=payments[row['payment']]['id'], disabled=False, meta=None))


def customers():
    from application.models import CustomerInvoice
    return CustomerInvoice


base_fixture_context = harness.fixture_context_for(base_contract, seed_rows, purge=(customers,))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'], fixture['default_category_name'] = category, name
        yield fixture


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def prepare(source):
    return {'started': time.time()}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value


def recent(value, window):
    return value is not None and window[0] - 1 <= value.timestamp() <= window[1] + 1


def render(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is not None and type(value).__name__ == 'UUID':
        return str(value)
    return value


def progressivo(text):
    require = base_contract.require
    require(isinstance(text, str) and 0 < len(text) <= 10 and all(char in PROGRESSIVO_CHARS for char in text), 'customer.progressivo-format')
    value = 0
    for char in text:
        value = value * 43 + PROGRESSIVO_CHARS.index(char)
    return value >> 16, value & 0xFFFF


def normalized_outputs(xml, document, window):
    """(xml, json) with ProgressivoInvio replaced; checks its derivation."""
    require = base_contract.require
    xml_match, json_match = PROGRESSIVO_XML.findall(xml), PROGRESSIVO_JSON.findall(document)
    require(len(xml_match) == 1 and len(json_match) == 1, 'customer.progressivo-present')
    (xml_ts, xml_seq), (json_ts, json_seq) = progressivo(xml_match[0]), progressivo(json_match[0])
    require(window[0] - 1 <= xml_ts <= json_ts <= window[1] + 1, 'customer.progressivo-time')
    require((json_ts == xml_ts and json_seq == xml_seq + 1) or (json_ts > xml_ts and json_seq == 0), 'customer.progressivo-sequence')
    return (PROGRESSIVO_XML.sub('<ProgressivoInvio>#</ProgressivoInvio>', xml),
            PROGRESSIVO_JSON.sub('"progressivo_invio": "#"', document))


def expected_outputs(memory, key, today):
    """generate_invoice_xml/json of the authored in-memory instance (ProgressivoInvio normalized)."""
    from application.models import CustomerInvoice
    from application.serializers.invoice_serializers import generate_invoice_json, generate_invoice_xml
    values = {}
    for field in CustomerInvoice._meta.concrete_fields:
        value = memory[field.name]
        if value == '$customer':
            value = key
        elif value == '$today':
            value = today
        elif value == '$now':
            value = datetime.now(timezone.utc)
        elif value is not None and field.get_internal_type() == 'DateField':
            value = date.fromisoformat(value)
        elif value is not None and field.get_internal_type() == 'DateTimeField':
            value = stamp(value)
        elif field.get_internal_type() == 'DecimalField':
            value = Decimal(str(value)) if not isinstance(value, int) else value
        values[field.attname] = value
    instance = CustomerInvoice(**values)
    xml, document = generate_invoice_xml(instance), generate_invoice_json(instance)
    return (PROGRESSIVO_XML.sub('<ProgressivoInvio>#</ProgressivoInvio>', xml),
            PROGRESSIVO_JSON.sub('"progressivo_invio": "#"', document))


def check_customer(spec, before, after, window):
    """The created customer invoice; returns the substitutions for its audit entries."""
    from application.models import CustomerInvoice
    require = base_contract.require
    old, new = keyed(before[CUSTOMER], 'customer_invoice_id'), keyed(after[CUSTOMER], 'customer_invoice_id')
    fresh = sorted(set(new) - set(old))
    require(set(old) <= set(new) and all(plain(new[k]) == plain(old[k]) for k in old), 'state.customer-kept')
    require(len(fresh) == (1 if spec else 0), 'state.customer-count')
    if not spec:
        return {}
    key = fresh[0]
    instance = CustomerInvoice._base_manager.get(pk=key)
    today = datetime.now(timezone.utc).date()
    stored = spec['stored']
    substitutions = {'$customer': str(key), '$today': today.isoformat()}
    if stored['xml'] == '$xml':
        xml, document = normalized_outputs(instance.xml, instance.json, window)
        expected_xml, expected_json = expected_outputs(spec['memory'], key, today)
        require(xml == expected_xml, 'customer.xml')
        require(document == expected_json, 'customer.json')
        substitutions.update({'$xml': instance.xml, '$json': instance.json, '$json_audit': json.dumps(instance.json)})
    for field in CustomerInvoice._meta.concrete_fields:
        actual = render(getattr(instance, field.attname))
        wanted = stored[field.name]
        if wanted == '$now':
            require(recent(stamp(actual), window), 'customer.now:' + field.name)
            substitutions['$now'] = str(stamp(actual).astimezone(timezone.utc).replace(tzinfo=None))
            continue
        if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and field.get_internal_type() == 'DecimalField':
            wanted = f'{Decimal(wanted):.2f}'
        require(actual == substitutions.get(wanted, wanted) if isinstance(wanted, str) else actual == wanted, 'customer.field:' + field.name)
    return substitutions


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(base_contract, before, after, MANAGED)
    fixture = context['fixture']
    window = (context['prepared']['started'], time.time())
    effect = case['effect']
    substitutions = check_customer(effect and effect['customer'], before, after, window)
    check_prints(case, substitutions.get('$customer'))
    require = base_contract.require
    if effect is None:
        require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    values = {'$default_category': fixture['default_category'], **substitutions}

    def resolve(item):
        return values.get(item, item) if isinstance(item, str) else item

    labels = {row['id']: label for label, row in source['invoices'].items()}
    old, new = keyed(before[INVOICE], 'invoice_id'), keyed(after[INVOICE], 'invoice_id')
    require(set(old) == set(new), 'state.invoice-count')
    for key, row in old.items():
        expected = plain(dict(row))
        expected.update(effect['invoices'].get(labels.get(key), {}))
        require(plain(new[key]) == expected, 'state.invoice:' + labels.get(key, key))
    labels = {row['id']: label for label, row in source['payments'].items()}
    old, new = keyed(before[PAYMENT], 'payment_id'), keyed(after[PAYMENT], 'payment_id')
    require(set(old) == set(new), 'state.payment-count')
    for key, row in old.items():
        expected = plain(dict(row))
        for column, value in effect['payments'].get(labels.get(key), {}).items():
            expected[column] = resolve(value)
        actual = plain(new[key])
        if expected.get('payment_date') != actual.get('payment_date'):
            require(stamp(expected['payment_date']) == stamp(actual['payment_date']), 'state.payment-date:' + labels.get(key, key))
            expected['payment_date'] = actual['payment_date']
        require(actual == expected, 'state.payment:' + labels.get(key, key))
    events, indexes = harness.audit_delta(base_contract, before, after)
    events.sort(key=lambda row: int(row['id']))
    require(len(events) == len(effect['audit']) and len(indexes) == len(events), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = source['ids'][effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else source['ids']['own_association']}
    representations = {'payment': 'Payment', 'invoice': 'Invoice', 'customerinvoice': 'CustomerInvoice'}
    for event, spec in zip(events, effect['audit']):
        key = resolve(spec['key'])
        where = f":{spec['model']}:{spec['action']}"
        changes = {column: [resolve(item) for item in pair] for column, pair in spec['changes'].items()}
        require(event['changes'] == changes, 'audit.changes' + where)
        require(event['content_type_id'] == types[spec['model']] and event['action'] == spec['action'] and event['object_pk'] == key
                and event['object_repr'] == f"{representations[spec['model']]} object ({key})", 'audit.identity' + where)
        require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        require(index is not None and index['sport_association_id'] == spec['index'][0] and index['resolution_path'] == spec['index'][1],
                'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
