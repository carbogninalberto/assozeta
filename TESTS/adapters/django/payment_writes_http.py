"""Parent-only payment add, update and bulk add HTTP runner; imports are inert.

Seeds payment categories (own, global, foreign), accounts (the own default
bank, an own cash account, a foreign bank), associates (own, grouped, deleted,
foreign), a subscription, an instructor, a supplier and the authored payments
(audit disabled). Each case is checked against the complete model state: only
payments and audit rows change; created payments match the authored columns
(bulk rows by associate), changed payments only the authored columns, every
other payment is unchanged, and the new auditlog entries match the authored
ones in order with their index rows. A '$now' column must be recent and, when
the response renders it, equal to the stored value; the response's bound
payment id is the created row. Payments the run creates are removed before
the fixture cleanup compares the model state.

A CURRENT_HOST print stub (invoice-sync-print's service_context) runs for the
whole run: each case's outbound requests must be exactly its authored
'prints' (the update of an invoiced payment prints the invoice).

A private MinIO (instance-logo's) backs Django's default storage (S3Boto3Storage)
for the whole run; the Go server receives the same AWS_* settings. Each case's
new Documents are bound to their create entries in write order and must match
its authored 'documents' (filename, payment link, the stored object under
django-storages' safe_join(AWS_LOCATION, clean_name(<timestamp>/<id>/<filename>))
with the exact bytes and guessed ContentType); response '$bind:doc<n>' values are
those rows. Verified objects are removed, so the bucket ends at its sentinel.

A case's 'owner_settings' (auto_paid_payment, the invoice date and numbering
settings) are set on the owner only around its request and restored after it
(queryset update, no audit). The effect's 'invoice' is the one invoice the case
creates (payment_paid_flow.check_invoice; none otherwise): the response's
'$bind:invoice', the payment's '$invoice' column, the '$invoice' print path and
the invoice audit entries are that row.
"""
from contextlib import ExitStack, contextmanager
import base64
import mimetypes
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress

import types

from TESTS.contracts import test_payment_writes_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.workflow_writes_http import keyed
from TESTS.adapters.django import default_payment_category
from TESTS.adapters.django import payment_paid_flow as paid

SOURCES = ('TESTS/fixtures/payment_writes_http.json', 'TESTS/contracts/test_payment_writes_http.py',
           'TESTS/adapters/django/payment_writes_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/payment_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/tenant_scope.py',
           'BE/application/permissions_registry.py', 'BE/application/utils/api_utils.py', 'BE/docmanager/models.py',
           'TESTS/adapters/django/invoice_sync_print_http.py', 'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/docmanager/models.py', 'TESTS/adapters/django/instance_logo.py',
           'BE/application/services/invoice_service.py', 'BE/application/utils/payments_utils.py',
           'TESTS/adapters/django/payment_paid_flow.py') + harness.SOURCES + default_payment_category.SOURCES
PAYMENT = 'application.Payment'
DOCUMENT = 'docmanager.Document'
BUCKET = 'payment-writes-fixture'
SENTINEL = ('payment-writes/sentinel/keep.txt', b'sentinel')
STORAGE = {}


@contextmanager
def service_context(report, log):
    """The CURRENT_HOST print stub and the run's MinIO as Django's default storage."""
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    location = base_contract.load_source()['location']
    with ExitStack() as stack:
        stack.enter_context(printer.service_context(report, log))
        endpoint, access, secret = stack.enter_context(minio(log, dict(os.environ), report))
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret, region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, location=location)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=location, AWS_S3_ADDRESSING_STYLE='path'):
                yield
        finally:
            # Each case removes the objects it verified ('storage.keys' fails on any other); a failed case may leave its own.
            left = sorted(listing() - {SENTINEL[0]})
            report['service_cleanup']['payment_writes_objects'] = 'clean' if not left else f'removed {len(left)} after a failure'
            for key in left:
                client.delete_object(Bucket=BUCKET, Key=key)
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def object_key(created, document_id, filename):
    from storages.utils import clean_name, safe_join
    return safe_join(STORAGE['location'], clean_name(os.path.join('', str(created.timestamp()), document_id, filename)))


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print stub scripted for it and its outbound requests checked after."""
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    with owner_settings(case.get('owner_settings')):
        status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    invoice = base_contract.BINDINGS.get('invoice')
    for item in case['prints']:
        base_contract.require('$invoice' not in item['path'] or invoice is not None, 'print.invoice-bound')
    printer.check_prints({**case, 'prints': [{**item, 'path': item['path'].replace('$invoice', str(invoice))} for item in case['prints']]}, None)
    return status


@contextmanager
def owner_settings(settings):
    """The owner's settings of one case, only around its request."""
    if not settings:
        yield
        return
    from application.models import User
    owner = User._base_manager.filter(pk=base_contract.load_source()['ids']['owner'])
    saved = owner.values(*settings).get()
    base_contract.require(owner.update(**settings) == 1, 'fixture.owner-settings')
    try:
        yield
    finally:
        base_contract.require(owner.update(**saved) == 1, 'fixture.owner-settings-restored')


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 BINDINGS=base_contract.BINDINGS,
                                 NOW=base_contract.NOW)
THROUGH = 'application.Payment_attachments'
MANAGED = (PAYMENT, DOCUMENT, THROUGH, paid.INVOICE, harness.AUDIT, harness.INDEX)
TIMES = ('creation_date', 'payment_date')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import Associate, Instructor, Payment, PaymentCategory, SupplierAndCustomers, Subscription
    from application.models.balance_sheet_models import CustomAccounts
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['categories'].values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], sport_association_id=row['association'],
                                                creation_date=moment('2026-01-01T00:00:00Z')))
    for row in source['accounts'].values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], name=row['name'], initial_balance=Decimal('0.00'),
                                              account_type=row['type'], account_code=row['code'], sport_association_id=row['association'],
                                              editable=row['editable'], enabled=True))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=owner, first_name=row['first'],
                                    last_name=row['last'], sex=row['sex'], tax_code=row['tax'], born_date=date.fromisoformat(row['born']),
                                    email=row['email'], is_minor=False, deleted=row['deleted'],
                                    group_id=ids[row['group']] if row['group'] else None))
    associates = source['associates']
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
                                          associate_id=associates[row['associate']]['id'], creation_date=moment(row['created']),
                                          status_flag=Subscription.ACCEPTED))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'], last_name=teacher['last_name'],
                                  email=teacher['email'], tax_code=teacher['tax_code'],
                                  default_hourly_billing=Decimal(teacher['default_hourly_billing'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    foreign = source['foreign_parties']
    subscription = foreign['subscription']
    insert(Subscription, Subscription(subscription_id=subscription['id'], sport_association_id=subscription['association'],
                                      user_id=ids['foreign_owner'], associate_id=associates[subscription['associate']]['id'],
                                      creation_date=moment(subscription['created']), status_flag=Subscription.ACCEPTED))
    teacher = foreign['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=teacher['user'], first_name=teacher['first_name'],
                                  last_name=teacher['last_name']))
    vendor = foreign['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=vendor['association']))
    from application.models import Invoice
    bill = source['invoice']
    insert(Invoice, Invoice(invoice_id=bill['id'], sport_association_id=own, creation_date=moment(bill['created']),
                            membership_fee=Decimal(bill['membership_fee']), activity_fee=Decimal(bill['activity_fee']),
                            description=bill['description'], number=bill['number']))
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in TIMES:
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))


def payments():
    from application.models import Payment
    return Payment


def documents():
    from docmanager.models import Document
    return Document


def invoices():
    from application.models import Invoice
    return Invoice


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(payments, documents, invoices))


def prepare(source):
    return {'objects': listing()}


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        row = default_payment_category.default_category(base_contract.load_source()['ids']['own_association'])
        base_contract.require(row is not None, 'fixture.default-category')
        category, name = str(row.pk), row.name
        fixture['default_category'], fixture['default_category_name'] = category, name
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        yield fixture


def rows_of(state):
    return {key: plain(row) for key, row in keyed(state[PAYMENT], 'payment_id').items()}


def recent(text):
    stamp = moment(text) if isinstance(text, str) else None
    return stamp is not None and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10)


def check_row(stored, authored, fixture, where, invoice=None):
    """A created row against its authored columns ('$now' recent and, when rendered, the response's value)."""
    bindings = {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name'],
                '$invoice': invoice}
    for column, value in authored.items():
        actual = stored.get(column)
        if value == '$now':
            contract.require(recent(actual), 'state.now:' + column + where)
            if contract.NOW and column == 'creation_date':
                contract.require(actual in contract.NOW, 'response.now-stored' + where)
        else:
            contract.require(actual == bindings.get(value, value) if isinstance(value, str) else actual == value,
                             'state.created-column:' + column + where)
    contract.require(set(stored) - {'payment_id'} == set(authored), 'state.created-columns' + where)


def check_documents(case, before, after, source, fresh, events, types):
    """New Documents bound to their create entries in order; their payment links and stored objects. Returns the bindings."""
    effect = case['effect']
    authored = effect.get('documents', []) if effect else []
    objects = listing() - STORAGE['prepared']
    old_docs, new_docs = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    contract.require(all(new_docs.get(k) == v for k, v in old_docs.items()), 'state.documents-kept')
    doc_type = next(ct['id'] for ct in before['contenttypes.ContentType'] if (ct['app_label'], ct['model']) == ('docmanager', 'document'))
    doc_events = [e for e in events if e['content_type_id'] == doc_type and e['action'] == 0]
    added = [k for k in new_docs if k not in old_docs]
    contract.require(len(added) == len(authored) == len(doc_events), 'state.document-count')
    link = lambda r: (str(plain(r)['payment_id']), str(plain(r)['document_id']))
    links_before, links_after = {link(r) for r in before[THROUGH]}, {link(r) for r in after[THROUGH]}
    contract.require(links_before <= links_after, 'state.links-kept')
    refs, expected_links, expected_objects = {}, set(), {}
    for index, (event, spec) in enumerate(zip(doc_events, authored)):
        key = event['object_pk']
        contract.require(key in new_docs, 'state.document-bound')
        row = plain(new_docs[key])
        stamp = moment(row['creation_date'])
        contract.require(row['filename'] == spec['filename'] and row['filepath'] is None and row['file_size_bytes'] is None
                         and recent(row['creation_date']), 'state.document:' + str(index))
        refs.update({f'$doc{index}': key, f'$doc{index}_token': str(row['token']), f'$doc{index}_created': auditlog_moment(stamp),
                     f'doc{index}': key, f'doc{index}_token': str(row['token'])})
        payment = fresh[0] if spec['payment'] == '$id' else source['payments'][spec['payment']]['id']
        expected_links.add((payment, key))
        expected_objects[object_key(stamp, key, spec['filename'])] = spec
    contract.require(links_after - links_before == expected_links, 'state.links')
    client = STORAGE['client']
    contract.require(objects == set(expected_objects), 'storage.keys')
    for key, spec in expected_objects.items():
        stored = client.get_object(Bucket=BUCKET, Key=key)
        contract.require(stored['Body'].read() == base64.b64decode(spec['body_b64']), 'storage.bytes')
        kind, encoding = mimetypes.guess_type(key)
        contract.require(stored['ContentType'] == (kind or 'application/octet-stream') and stored.get('ContentEncoding') == encoding,
                         'storage.metadata')
    for key in expected_objects:
        client.delete_object(Bucket=BUCKET, Key=key)
    return refs


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    STORAGE['prepared'] = context['prepared']['objects']
    if effect is None:
        contract.require(not (listing() - STORAGE['prepared']), 'storage.no-effect')
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    old, new = rows_of(before), rows_of(after)
    fresh = sorted(set(new) - set(old))
    all_events, _ = harness.audit_delta(contract, before, after)
    all_types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    refs = check_documents(case, before, after, source, fresh, all_events, all_types)
    bound = {}
    response_invoice = contract.BINDINGS.get('invoice')
    invoice_now = paid.check_invoice(contract, before, after, effect.get('invoice'), [response_invoice] if response_invoice else [], bound)
    invoice = bound.get('$invoice')
    contract.require(response_invoice == invoice, 'response.binding:invoice')
    if invoice is not None:
        refs['$invoice'] = invoice
    authored = effect['created']
    contract.require(len(fresh) == len(authored) and set(old) <= set(new), 'state.created-count')
    created = {}
    if len(authored) == 1:
        created[fresh[0]] = authored[0]
    else:
        by_associate = {new[key]['associate_id']: key for key in fresh}
        for spec in authored:
            contract.require(spec['associate_id'] in by_associate, 'state.created-associate')
            created[by_associate[spec['associate_id']]] = spec
    for key, spec in created.items():
        check_row(new[key], spec, fixture, ':' + key, invoice)
    for name, value in contract.BINDINGS.items():
        if name == 'invoice':
            continue
        if name.startswith('doc'):
            contract.require(refs.get(name) == value, 'response.binding:' + name)
        else:
            contract.require(len(fresh) == 1 and value == fresh[0], 'response.binding:' + name)
    labels = {row['id']: label for label, row in source['payments'].items()}
    bindings = {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name']}
    for key, row in old.items():
        changes = effect['changed'].get(labels.get(key), {})
        expected = {**row, **{column: bindings.get(value, value) if isinstance(value, str) else value for column, value in changes.items()}}
        contract.require(new[key] == expected, 'state.payment:' + labels.get(key, key))
    # Audit entries and their index rows.
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: int(row['id']))
    contract.require(len(events) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    doc_type = next(ct['id'] for ct in before['contenttypes.ContentType'] if (ct['app_label'], ct['model']) == ('docmanager', 'document'))
    invoice_moments = []
    for event, spec in zip(events, effect['audit']):
        model = spec.get('model', 'payment')
        if model in ('document', 'invoice'):
            key = refs[spec['key']]
        else:
            key = fresh[0] if spec['key'] == '$id' else source['payments'][spec['key']]['id']
        stored = new.get(key, {})

        def value(item, column):
            if item == '$id':
                return key
            if item == '$now' and model == 'invoice':
                # The invoice's first creation date (it may be saved again with the payment date).
                text = event['changes'].get(column, [None, None])[0 if event['action'] else 1]
                contract.require(isinstance(text, str) and recent(text.replace(' ', 'T') + '+00:00'), 'audit.invoice-now')
                invoice_moments.append(text)
                return text
            if item == '$now':
                return auditlog_moment(moment(stored[column]))
            if item in refs:
                return refs[item]
            return bindings.get(item, item)
        changes = {column: [value(item, column) for item in pair] for column, pair in spec['changes'].items()}
        where = f":{spec['key']}:{spec['action']}"
        content_type = doc_type if model == 'document' else types[model]
        contract.require(event['content_type_id'] == content_type and event['action'] == spec['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{model.capitalize()} object ({key})', 'audit.identity' + where)
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)
    contract.require(len(set(invoice_moments)) <= 1, 'audit.invoice-now-consistent')
    if invoice_moments and invoice_now is not None:
        contract.require(invoice_moments[0] == invoice_now, 'audit.invoice-now-stored')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
