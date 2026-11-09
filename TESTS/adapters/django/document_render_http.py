"""Parent-only document-render HTTP runner; imports are inert.

service_context owns a private renderer stub (the Puppeteer FastRender
service) for the whole run: a local HTTP server that records every request and
answers with the replies authored on the current case. Before either server
starts, Django's FastRender.render_url is pointed at it and the Go server
receives PUPPETEER_HOST/PUPPETEER_PORT (with CURRENT_HOST_PUPPETEER and
DOCUMENT_BYPASS_TOKEN from the private environment). Each case's renderer
requests must be exactly the authored ones: POST /render, requests' default
headers, Content-Type application/json and the body bytes of
json.dumps(FastRender payload) naming the view URL and the request's
Authorization header.

seed_rows inserts (audit disabled) two extra associations with their owners,
associates, documents, subscriptions, invoices with their payments,
compensation payments with instructors and hours, customer invoices, module
templates, folders and medical appointments. Each case is checked against the
complete model state: at most one new Document (the authored filename, a
fresh uuid4 id and token, no filepath or size), the authored FK links to it,
the archive and subscription-file rows the template route creates, every
new auditlog entry with its index row, and the Celery messages
(save_to_storage with '<timestamp>/<id>/<filename>' and the base64 PDF, then
the receipt send_mail_async).
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import ipaddress
import os
import threading
import time
import types
import uuid

from TESTS.contracts import test_document_render_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/document_render_http.json', 'TESTS/contracts/test_document_render_http.py',
           'TESTS/adapters/django/document_render_http.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/docmanager/views/printing_views.py', 'BE/docmanager/views/utils.py', 'BE/docmanager/models.py',
           'BE/docmanager/tasks.py', 'BE/application/utils/printing.py', 'BE/core/tasks.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/impersonation.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/permissions_registry.py', 'BE/core/settings.py',
           'BE/core/middleware.py') + harness.SOURCES
DOCUMENT, ARCHIVE, SUBSCRIPTION_FILE = 'docmanager.Document', 'application.SportAssociationDocumentsArchive', 'application.SubscriptionFile'
LINKED = {'subscription': ('application.Subscription', 'subscription_id', 'document_pdf_id', 'document_pdf'),
          'invoice': ('application.Invoice', 'invoice_id', 'document_pdf_id', 'document_pdf'),
          'customerinvoice': ('application.CustomerInvoice', 'customer_invoice_id', 'pdf_id', 'pdf'),
          'instructorhours': ('application.InstructorHours', 'instructor_hours_id', 'document_id', 'document'),
          'medicalappointments': ('application.MedicalAppointments', 'medical_appointments_id', 'document_id', 'document')}
MANAGED = (DOCUMENT, ARCHIVE, SUBSCRIPTION_FILE, harness.AUDIT, harness.INDEX) + tuple(item[0] for item in LINKED.values())
PDF_BYTES = b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Type /Catalog >> endobj\n' + bytes(range(256)) + b'\n%%EOF\n'
STUB = {}
FOLDERS = {}


# ---------------------------------------------------------------------------
# Renderer stub
# ---------------------------------------------------------------------------

class _RendererHandler(BaseHTTPRequestHandler):
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
            status, content_type, data = 500, 'text/plain', b'unexpected request'
        else:
            status, content_type = reply['status'], reply['content_type']
            data = PDF_BYTES if reply['body'] == '$pdf' else reply['body'].encode()
        self.send_response(status)
        if content_type:
            self.send_header('Content-Type', content_type)
        if status != 304:
            self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if status != 304:
            self.wfile.write(data)

    do_GET = do_POST = _answer


@contextmanager
def service_context(report, log):
    from application.utils import printing
    server = ThreadingHTTPServer(('127.0.0.1', 0), _RendererHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    STUB.update(lock=threading.Lock(), log=[], script=[])
    port = str(server.server_address[1])
    saved_url = printing.FastRender.render_url
    saved_env = {name: os.environ.get(name) for name in ('PUPPETEER_HOST', 'PUPPETEER_PORT')}
    report['service_cleanup']['renderer_stub'] = 'running'
    thread.start()
    try:
        os.environ.update(PUPPETEER_HOST='127.0.0.1', PUPPETEER_PORT=port)
        printing.FastRender.render_url = f'http://127.0.0.1:{port}/render'
        yield
    finally:
        printing.FastRender.render_url = saved_url
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        report['service_cleanup']['renderer_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        STUB.clear()


def _assert_case(case, base_url, *, token, sessions):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = [item['reply'] for item in case['renders']]
    STUB['token'] = token
    return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


# The harness drives this proxy: the stub is scripted with the case's replies before each request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_renderer(case):
    import requests
    from django.conf import settings
    require = contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    require(len(log) == len(case['renders']), 'renderer.request-count')
    for index, (sent, spec) in enumerate(zip(log, case['renders'])):
        where = f':{index}'
        headers = {}
        for name, value in sent['headers']:
            require(name.lower() not in headers, 'renderer.single-header' + where)
            headers[name.lower()] = value
        require(sent['method'] == 'POST' and sent['path'] == '/render', 'renderer.request-line' + where)
        require(set(headers) == {'host', 'user-agent', 'accept-encoding', 'accept', 'connection', 'content-type', 'content-length'},
                'renderer.header-names' + where)
        require(headers['user-agent'] == f'python-requests/{requests.__version__}' and headers['accept-encoding'] == 'gzip, deflate'
                and headers['accept'] == '*/*' and headers['connection'] == 'keep-alive'
                and headers['content-type'] == 'application/json' and headers['content-length'] == str(len(sent['body'])),
                'renderer.headers' + where)
        authorization = spec['authorization']
        if authorization == '$bearer':
            authorization = 'Bearer ' + STUB['token']
        elif authorization == '$bypass':
            authorization = settings.DOCUMENT_BYPASS_TOKEN
        expected = base_contract.renderer_payload(settings.CURRENT_HOST_PUPPETEER, spec['url'], authorization)
        require(sent['body'] == expected, 'renderer.body' + where)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def moment(text):
    return datetime.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, CustomerInvoice, Folder, Instructor, InstructorHours, Invoice, Payment, SportAssociation,
                                    Subscription, User)
    from application.models.subscriptions_models import MedicalAppointments
    from application.models.user_models import SportAssociationModuleTemplates
    from docmanager.models import Document
    ids = source['ids']
    associations = source['association_ids']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    def document(key):
        return source['documents'][key]['id'] if key else None

    seeded = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    for label, spec in source['associations'].items():
        insert(User, User(user_id=spec['user'], username=f'document-render-{label}', email=f'document-render-{label}@example.test',
                          password='!synthetic', role=User.ASSOCIATION, first_name='Render', last_name=label))
        insert(SportAssociation, SportAssociation(sport_association_id=spec['association'], user_id=spec['user'],
                                                  denomination=spec['denomination'], tax_code='7153000000' + str(len(label))))
    for label, row in source['associates'].items():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=associations[row['association']], first_name=row['first'],
                                    last_name=row['last'], email=row['email'], is_minor=False))
    for row in source['documents'].values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=seeded))
    associate_ids = {**{k: v['id'] for k, v in source['associates'].items()}, 'athlete': 'a1000000-0000-4000-8000-000000700101'}
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=associations[row['association']],
                                          user_id=ids[row['user']], associate_id=associate_ids[row['associate']] if row['associate'] else None,
                                          group_id=ids[row['group']] if row['group'] else None, document_pdf_id=document(row['document']),
                                          creation_date=seeded, status_flag=Subscription.ACCEPTED))
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=associations[row['association']], membership_fee=Decimal('0.00'),
                                activity_fee=Decimal('10.00'), number=1, description='Ricevuta', creation_date=seeded,
                                document_pdf_id=document(row['document']), deleted=row['deleted'],
                                group_id=ids[row['group']] if row['group'] else None))
    for row in source['instructors'].values():
        insert(Instructor, Instructor(instructor_id=row['id'], user_id=ids[row['user']], first_name=row['first'], last_name=row['last']))
    for row in source['payments'].values():
        insert(Payment, Payment(
            payment_id=row['id'], sport_association_id=associations[row['association']], subject=0, amount=Decimal(row['amount']),
            paid=True, associate_id=associate_ids[row['associate']] if row['associate'] else None,
            user_id=ids[row['user']] if row['user'] else None,
            instructor_id=source['instructors'][row['instructor']]['id'] if row['instructor'] else None,
            invoice_id=source['invoices'][row['invoice']]['id'] if row['invoice'] else None, group_id=ids[row['group']] if row['group'] else None,
            deleted=row['deleted'], creation_date=seeded, payment_date=moment(row['date']), description='Pagamento'))
    for row in source['hours'].values():
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=row['id'], instructor_id=source['instructors'][row['instructor']]['id'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, date=date.fromisoformat(row['date']),
            hours=Decimal('1.50'), creation_date=seeded))
    for row in source['customer_invoices'].values():
        insert(CustomerInvoice, CustomerInvoice(customer_invoice_id=row['id'], sport_association_id=associations[row['association']],
                                                prefix=row['prefix'], number=row['number'], fiscal_year=row['year'],
                                                pdf_id=document(row['document']), creation_date=seeded, payment_expiry_date=date(2026, 1, 2)))
    for row in source['templates'].values():
        insert(SportAssociationModuleTemplates, SportAssociationModuleTemplates(
            sport_association_module_templates_id=row['id'], sport_association_id=associations[row['association']], name=row['name'],
            template='<p>Modulo</p>'))
    FOLDERS.clear()
    for label, association in source['folders'].items():
        FOLDERS[label] = Folder.objects.create(name=f'document-render {label}', sport_association_id=associations[association]).pk
        created.append((Folder, FOLDERS[label]))
    for row in source['medical'].values():
        insert(MedicalAppointments, MedicalAppointments(
            medical_appointments_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'], region=row['region'],
            date=date.fromisoformat(row['date']) if row['date'] else None, document_id=document(row['document']), creation_date=seeded,
            meta={}))


def _purged():
    from application.models.subscriptions_models import SubscriptionFile
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    return SubscriptionFile, SportAssociationDocumentsArchive, Document


fixture_context = harness.with_celery_broker(harness.fixture_context_for(
    contract, seed_rows, purge=tuple((lambda index=index: _purged()[index]) for index in range(3))))


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def recent(when):
    return isinstance(when, datetime) and abs(datetime.now(timezone.utc) - when) < timedelta(minutes=10)


def parse(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def naive(when):
    """auditlog's str() of a DateTimeField: the naive UTC value."""
    return str(when.astimezone(timezone.utc).replace(tzinfo=None))


def uuid4_text(value):
    contract.require(isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4, 'state.uuid4')
    return value


def actor_association(actor, ids):
    """The index association of auditlog's actor fallback: SportAssociation.objects.filter(user=actor).first()."""
    return {'owner': ids['own_association'], 'admin': ids['admin_association']}.get(actor)


def assert_effect(case, before, after, source, context):
    from django.conf import settings
    require = contract.require
    check_renderer(case)
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids, spec = source['ids'], case['effect']
    old_documents = keyed(before[DOCUMENT], 'document_id')
    new_documents = keyed(after[DOCUMENT], 'document_id')
    require(all(new_documents.get(key) == row for key, row in old_documents.items()), 'state.documents-kept')
    added = [row for key, row in new_documents.items() if key not in old_documents]
    require(len(added) == (1 if spec else 0), 'state.document-count')
    fixture = context['fixture']
    if not spec:
        for label in (ARCHIVE, SUBSCRIPTION_FILE) + tuple(item[0] for item in LINKED.values()):
            require(before[label] == after[label], 'state.unchanged:' + label)
        require(len(harness.audit_delta(contract, before, after)[0]) == 0, 'audit.none')
        harness.celery_published(contract, fixture, [], since=context['prepared']['started'], until=time.time())
        return
    document = added[0]
    created = parse(document['creation_date'])
    filename = spec['document'].replace('$today', datetime.now(timezone.utc).strftime('%d/%m/%Y'))
    require(document['filename'] == filename and document['filepath'] is None and document['file_size_bytes'] is None
            and recent(created), 'state.document')
    doc_id, token = uuid4_text(document['document_id']), uuid4_text(document['token'])

    # Links: the authored rows point at the new document; every other row is unchanged.
    links = {}
    for model, key in spec['links']:
        label, pk, column, field = LINKED[model]
        row_id = {'subscription': source['subscriptions'], 'invoice': source['invoices'], 'customerinvoice': source['customer_invoices'],
                  'instructorhours': source['hours'], 'medicalappointments': source['medical']}[model][key]['id']
        links.setdefault(model, []).append(row_id)
    for model, (label, pk, column, field) in LINKED.items():
        old_rows, new_rows = keyed(before[label], pk), keyed(after[label], pk)
        require(set(old_rows) == set(new_rows), 'state.rows:' + label)
        for key, row in old_rows.items():
            expected = {**row, column: doc_id} if key in links.get(model, []) else row
            require(new_rows[key] == expected, 'state.link:' + label)

    # Rows the template route creates.
    def new_rows(label, pk):
        old = keyed(before[label], pk)
        return [row for key, row in keyed(after[label], pk).items() if key not in old]
    archives, files = new_rows(ARCHIVE, 'sport_association_documents_archive_id'), new_rows(SUBSCRIPTION_FILE, 'subscription_file_id')
    wanted_archives = [item for item in spec['creates'] if item['model'] == 'sportassociationdocumentsarchive']
    wanted_files = [item for item in spec['creates'] if item['model'] == 'subscriptionfile']
    require(len(archives) == len(wanted_archives) and len(files) == len(wanted_files), 'state.created-count')
    for row, item in zip(archives, wanted_archives):
        uuid4_text(row['sport_association_documents_archive_id'])
        require(row['sport_association_id'] == ids['own_association'] and row['document_id'] == doc_id
                and row['folder_id'] == FOLDERS[item['folder']] and row['date'] == datetime.now(timezone.utc).date(), 'state.archive')
    for row, item in zip(files, wanted_files):
        uuid4_text(row['subscription_file_id'])
        require(row['subscription_id'] == source['subscriptions'][item['subscription']]['id'] and row['document_id'] == doc_id
                and recent(parse(row['creation_date'])), 'state.subscription-file')

    # Audit entries, in Django's order.
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: row['id'])
    types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[spec['actor']] if spec['actor'] else None
    own_actor = actor_association(spec['actor'], ids)
    additional = None
    session = case['headers'].get('X-Impersonation-Id')
    if session:
        label = session.split(':', 1)[1]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': ids['own_association'] if label.startswith('owner_') else None}
    expected = [('docmanager', 'document', 'Document', doc_id, 0,
                 {'document_id': ['None', doc_id], 'creation_date': ['None', naive(created)], 'filename': ['None', filename],
                  'token': ['None', token], 'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']},
                 (own_actor, 'actor.sport_association') if own_actor else None)]
    for row, item in zip(archives, wanted_archives):
        key = row['sport_association_documents_archive_id']
        expected.append(('application', 'sportassociationdocumentsarchive', 'SportAssociationDocumentsArchive', key, 0,
                         {'sport_association_documents_archive_id': ['None', key], 'sport_association': ['None', ids['own_association']],
                          'document': ['None', doc_id], 'date': ['None', '$aware-now'], 'folder': ['None', str(FOLDERS[item['folder']])]},
                         (ids['own_association'], 'direct')))
    for row, item in zip(files, wanted_files):
        key = row['subscription_file_id']
        subscription = source['subscriptions'][item['subscription']]
        expected.append(('application', 'subscriptionfile', 'SubscriptionFile', key, 0,
                         {'subscription_file_id': ['None', key], 'subscription': ['None', subscription['id']], 'document': ['None', doc_id],
                          'creation_date': ['None', naive(parse(row['creation_date']))]},
                         (source['association_ids'][subscription['association']], 'subscription.sport_association')))
    names = {'subscription': 'Subscription', 'invoice': 'Invoice', 'customerinvoice': 'CustomerInvoice', 'instructorhours': 'InstructorHours',
             'medicalappointments': 'MedicalAppointments'}
    unordered_hours = []
    for model, key in spec['links']:
        label, pk, column, field = LINKED[model]
        table = {'subscription': source['subscriptions'], 'invoice': source['invoices'], 'customerinvoice': source['customer_invoices'],
                 'instructorhours': source['hours'], 'medicalappointments': source['medical']}[model]
        row_id = table[key]['id']
        old = keyed(before[label], pk)[row_id][column]
        if model == 'instructorhours':
            owner = source['instructors'][table[key]['instructor']]['user']
            index = ({'owner': ids['own_association'], 'foreign_owner': ids['foreign_association']}[owner], 'instructor.user.sport_association')
        elif model == 'medicalappointments':
            subscription = source['subscriptions'][table[key]['subscription']]
            index = (source['association_ids'][subscription['association']], 'subscription.sport_association')
        else:
            index = (source['association_ids'][table[key]['association']], 'direct')
        entry = ('application', model, names[model], row_id, 1, {field: [str(old) if old else 'None', doc_id]}, index)
        (unordered_hours if model == 'instructorhours' else expected).append(entry)
    actual_hours = [event for event in events[len(expected):]]
    require(len(events) == len(expected) + len(unordered_hours), 'audit.count')
    unordered_hours.sort(key=lambda item: item[3])
    actual_hours.sort(key=lambda event: event['object_pk'])
    for event, (app, model, name, key, action, changes, index) in zip(events[:len(expected)] + actual_hours, expected + unordered_hours):
        require(event['content_type_id'] == types_[(app, model)] and event['action'] == action and event['object_pk'] == key
                and event['object_repr'] == f'{name} object ({key})', 'audit.identity:' + model)
        if changes.get('date') == ['None', '$aware-now']:
            stamp = event['changes'].get('date', [None, ''])[1]
            require(recent(datetime.fromisoformat(stamp)) and stamp.endswith('+00:00'), 'audit.archive-date')
            changes = {**changes, 'date': ['None', stamp]}
        require(event['changes'] == changes, 'audit.changes:' + model)
        require(event['actor_id'] == actor and event['actor_email'] == (users[actor]['email'] if actor else None), 'audit.actor:' + model)
        require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context:' + model)
        if event['remote_addr'] is not None:
            ipaddress.ip_address(event['remote_addr'])
        found = indexes.get(str(event['id']))
        if index is None:
            require(found is None, 'index.none:' + model)
        else:
            require(found is not None and found['sport_association_id'] == index[0] and found['resolution_path'] == index[1],
                    'index.fields:' + model)

    # Celery: the storage task, then the receipt e-mail.
    published = []
    if spec['storage_task']:
        stored = f'{created.timestamp()}/{doc_id}/{filename}'
        published.append({'task': 'save_to_storage', 'args': [stored, base64.b64encode(PDF_BYTES).decode()], 'kwargs': []})
    if spec['mail']:
        mail = spec['mail']
        message = (mail['message'].replace('$app_url', settings.APP_URL).replace('$document', doc_id).replace('$token', token))
        published.append({'task': 'core.tasks.send_mail_async', 'args': [], 'kwargs': [
            ['subject', mail['subject']], ['message', message], ['from_email', settings.DEFAULT_TEAM_EMAIL],
            ['reply_to', [settings.DEFAULT_SUPPORT_EMAIL]], ['recipient_list', [mail['recipient']]], ['html_message', None],
            ['fail_silently', False]]})
    harness.celery_published(contract, fixture, published, since=context['prepared']['started'], until=time.time())


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from django.conf import settings
    base_contract.RUNTIME.clear()
    base_contract.RUNTIME.update(bypass=settings.DOCUMENT_BYPASS_TOKEN, folders=dict(FOLDERS), pdf=PDF_BYTES)
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        base_contract.RUNTIME.clear()
