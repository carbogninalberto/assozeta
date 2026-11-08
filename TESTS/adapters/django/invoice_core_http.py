"""Parent-only invoice list, archived list, bulk archive, bulk delete and delete HTTP runner; imports are inert.

Seeds documents, associates, a supplier, a payment category, invoices (with
attached-document links and invoice rows) and payments with every field
explicit (audit disabled); the payment dated '$current_year' is seeded one
hour after the owner's balance-year start. Reads must leave the complete
model state unchanged. Each write case declares the invoice and payment
columns it changes, the invoices and invoice rows it deletes (their
attached-document links go with them) and its auditlog entries in order with
their index rows; delete entries are rendered from the rows captured before
the request. Every other model and row must be unchanged.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import json

from TESTS.contracts import test_invoice_core_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/invoice_core_http.json', 'TESTS/contracts/test_invoice_core_http.py',
           'TESTS/adapters/django/invoice_core_http.py', 'BE/application/views/invoice_views.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/personas_serializers.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/permissions.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/utils/api_utils.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/permissions_registry.py', 'BE/docmanager/models.py', 'BE/core/settings.py') + harness.SOURCES
INVOICE, ROWS, PAYMENT, LINKS = ('application.Invoice', 'application.InvoiceRows', 'application.Payment',
                                 'application.Invoice_attached_documents')
MANAGED = (INVOICE, ROWS, PAYMENT, LINKS, harness.AUDIT, harness.INDEX)
LABELS = {'invoice': INVOICE, 'invoicerows': ROWS, 'payment': PAYMENT}
KEYS = {INVOICE: 'invoice_id', ROWS: 'invoice_row_id', PAYMENT: 'payment_id'}
NAMES = {'invoice': 'Invoice', 'invoicerows': 'InvoiceRows', 'payment': 'Payment'}


def current_year_moment():
    from django.utils import timezone as django_timezone
    from application.models import User
    from application.utils.api_utils import BalanceSheetData
    owner = User.objects.get(pk=contract.load_source()['ids']['owner'])
    start, _ = BalanceSheetData.get_range_from_year_and_starting_date(
        date=django_timezone.now(), starting_day=owner.balance_sheet_start_day, starting_month=owner.balance_sheet_start_month)
    return start + timedelta(hours=1)


def field_values(model, row):
    values = {}
    for field in model._meta.concrete_fields:
        if field.name not in row:
            continue
        value = row[field.name]
        kind = field.get_internal_type()
        if value == '$current_year':
            value = current_year_moment()
        elif value is not None and kind == 'DateTimeField':
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif value is not None and kind == 'DateField':
            value = date.fromisoformat(value)
        elif value is not None and kind == 'DecimalField':
            value = Decimal(value)
        values[field.attname] = value
    return values


def seed_rows(source, created):
    from application.models import Associate, Invoice, InvoiceRows, Payment, PaymentCategory, SupplierAndCustomers
    from docmanager.models import Document

    def seed(model, row):
        instance = model(**field_values(model, row))
        contract.require(not model._base_manager.filter(pk=instance.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([instance])
        created.append((model, instance.pk))

    for model, rows in ((Document, source['documents'].values()), (Associate, source['associates'].values()),
                        (SupplierAndCustomers, [source['supplier']]), (PaymentCategory, [source['category']]),
                        (Invoice, source['invoices'].values()), (InvoiceRows, source['rows'].values()), (Payment, source['payments'].values())):
        for row in rows:
            seed(model, row)
    for through, owner, items, table in ((Invoice.attached_documents.through, 'invoice_id', source['links'], source['invoices']),
                                         (Payment.attachments.through, 'payment_id', source['attachments'], source['payments'])):
        for label, document in items:
            key = table[label]['invoice_id' if owner == 'invoice_id' else 'payment_id']
            link = through(**{owner: key, 'document_id': source['documents'][document]['document_id']})
            through.objects.bulk_create([link])
            created.append((through, through.objects.get(**{owner: key, 'document_id': link.document_id}).pk))


def auditlog_moment(value):
    return str(value.astimezone(timezone.utc).replace(tzinfo=None))


def deleted_changes(model, row):
    """auditlog's delete diff: every stored field against None, None-valued
    fields kept only for foreign keys and DateTimeFields ('None') and JSON
    fields ('null')."""
    changes = {}
    for field in model._meta.concrete_fields:
        value = getattr(row, field.attname)
        if value is None:
            if field.is_relation or field.get_internal_type() == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif field.get_internal_type() == 'JSONField':
                changes[field.name] = ['null', 'None']
            continue
        if field.get_internal_type() == 'DateTimeField':
            value = auditlog_moment(value)
        elif field.get_internal_type() == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = [str(value), 'None']
    return changes


def captured(source):
    """Delete diffs of the seeded invoices and invoice rows still stored, captured before each case."""
    from application.models import Invoice, InvoiceRows
    rows = {}
    for model, key, seeded in ((Invoice, 'invoice_id', source['invoices']), (InvoiceRows, 'invoice_row_id', source['rows'])):
        for instance in model._base_manager.filter(pk__in=[r[key] for r in seeded.values()]):
            rows[str(instance.pk)] = deleted_changes(model, instance)
    return rows


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    for name, label in LABELS.items():
        old, new = keyed(before[label], KEYS[label]), keyed(after[label], KEYS[label])
        gone = set(effect['deleted'].get(name, []))
        changes = effect['changed'].get(name, {})
        expected = {key: {**row, **changes.get(key, {})} for key, row in old.items() if key not in gone}
        contract.require(gone <= set(old), 'state.deleted-seeded:' + name)
        contract.require(new == expected, 'state.rows:' + name)
    removed = set(effect['deleted'].get('invoice', []))
    kept = [row for row in before[LINKS] if str(scalar(row['invoice_id'])) not in removed]
    contract.require(after[LINKS] == kept, 'state.links')

    entries, indexes = harness.audit_delta(contract, before, after)
    entries.sort(key=lambda row: int(row['id']))
    contract.require(len(entries) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    ids = source['ids']
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, entry) in enumerate(zip(entries, effect['audit'])):
        key, where = entry['key'], f":{position}:{entry['model']}"
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and event['object_repr'] == f"{NAMES[entry['model']]} object ({key})", 'audit.identity' + where)
        changes = entry['changes'] if entry['changes'] is not None else context['prepared'][key]
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
