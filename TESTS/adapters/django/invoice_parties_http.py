"""Parent-only customer and supplier invoice list, add and update HTTP runner; imports are inert.

Seeds documents, suppliers, bank accounts, payment categories, payments (one
with an attachment), supplier invoices and customer invoices with every field
explicit (audit disabled). Reads must leave the complete model state
unchanged. Each write case declares the payment, supplier-invoice and
customer-invoice rows it changes or creates and the exact auditlog entries in
order (with their index rows); every other model and row must be unchanged.
'$now' accepts a value taken during the request, '$before' the stored value
before the case, '$payment'/'$invoice' the created primary keys, and a
'$created' audit is auditlog's create diff of the stored row with the named
relations still unset. Created rows are removed before the fixture cleanup.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re

from TESTS.contracts import test_invoice_parties_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/invoice_parties_http.json', 'TESTS/contracts/test_invoice_parties_http.py',
           'TESTS/adapters/django/invoice_parties_http.py', 'BE/application/views/invoice_views.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/balance_sheet_models.py',
           'BE/application/models/user_models.py', 'BE/application/mixin.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/signals.py', 'BE/application/utils/api_utils.py',
           'BE/docmanager/models.py') + harness.SOURCES
MODELS = {'payment': 'application.Payment', 'invoicesuppliers': 'application.InvoiceSuppliers',
          'customerinvoice': 'application.CustomerInvoice'}
MANAGED = (*MODELS.values(), harness.AUDIT, harness.INDEX)
DESCRIPTION = re.compile(r'Fattura fornitore \((.*)\) generata automaticamente dal sistema in data (\d\d/\d\d/\d{4} alle \d\d:\d\d)\.')


def model_class(name):
    from application.models import CustomerInvoice, InvoiceSuppliers, Payment
    return {'payment': Payment, 'invoicesuppliers': InvoiceSuppliers, 'customerinvoice': CustomerInvoice}[name]


def field_values(model, row):
    values = {}
    for field in model._meta.concrete_fields:
        if field.name not in row:
            continue
        value = row[field.name]
        kind = field.get_internal_type()
        if value is not None and kind == 'DateTimeField':
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif value is not None and kind == 'DateField':
            value = date.fromisoformat(value)
        elif value is not None and kind == 'DecimalField':
            value = Decimal(value)
        values[field.attname] = value
    return values


def seed_rows(source, created):
    from application.models import CustomAccounts, CustomerInvoice, InvoiceSuppliers, Payment, PaymentCategory, SupplierAndCustomers
    from docmanager.models import Document

    def seed(model, row):
        instance = model(**field_values(model, row))
        contract.require(not model._base_manager.filter(pk=instance.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([instance])
        created.append((model, instance.pk))

    for model, rows in ((Document, source['documents']), (SupplierAndCustomers, source['suppliers']), (CustomAccounts, source['accounts']),
                        (PaymentCategory, source['categories']), (Payment, source['payments']),
                        (InvoiceSuppliers, source['supplier_invoices']), (CustomerInvoice, source['customer_invoices'])):
        for row in rows.values():
            seed(model, row)
    through = Payment.attachments.through
    for payment, document in source['attachments']:
        link = through(payment_id=source['payments'][payment]['payment_id'], document_id=source['documents'][document]['document_id'])
        through.objects.bulk_create([link])
        created.append((through, through.objects.get(payment_id=link.payment_id, document_id=link.document_id).pk))


def render(value):
    """A stored attribute in the generator's JSON form."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is not None and type(value).__name__ == 'UUID':
        return str(value)
    return value


def audit_value(field, value):
    """auditlog's str() of one attribute (DateTimeFields naive UTC, JSON sorted dumps)."""
    kind = field.get_internal_type()
    if value is None:
        return None
    if kind == 'DateTimeField':
        return str(value.astimezone(timezone.utc).replace(tzinfo=None))
    if kind == 'JSONField':
        return json.dumps(value, sort_keys=True)
    return str(value)


def created_changes(model, instance, unset):
    changes = {}
    for field in model._meta.concrete_fields:
        value = None if field.name in unset else getattr(instance, field.attname)
        if value is None:
            if field.is_relation or field.get_internal_type() == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif field.get_internal_type() == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        changes[field.name] = ['None', audit_value(field, value)]
    return changes


def model_field(model, name):
    for field in model._meta.concrete_fields:
        if name in (field.name, field.attname):
            return field
    raise KeyError(name)


def within(window, text):
    moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return window[0] <= moment <= window[1]


def prepare(source):
    return {'started': datetime.now(timezone.utc)}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    spec = case['effect']
    if spec is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    window = (context['prepared']['started'], datetime.now(timezone.utc))
    keys = {'payment': 'payment_id', 'invoicesuppliers': 'invoice_supplier_id', 'customerinvoice': 'customer_invoice_id'}
    created = {}
    for name, label in MODELS.items():
        old, new = keyed(before[label], keys[name]), keyed(after[label], keys[name])
        added = set(new) - set(old)
        contract.require(set(old) <= set(new), 'state.kept:' + name)
        expected_new = spec['new'].get(name)
        contract.require(len(added) == (1 if expected_new else 0), 'state.created:' + name)
        if added:
            created[name] = added.pop()
        touched = spec['rows'].get(name, {})
        contract.require(all(new[k] == old[k] for k in old if k not in touched), 'state.untouched:' + name)
    substitutions = {'$payment': created.get('payment'), '$invoice': created.get('invoicesuppliers')}

    def expect(value, actual, previous=None):
        if value == '$now':
            return isinstance(actual, str) and within(window, actual)
        if value == '$before':
            return actual == previous
        if isinstance(value, str) and value.startswith('$description:'):
            match = DESCRIPTION.fullmatch(actual or '')
            if not match or match.group(1) != value.split(':', 1)[1]:
                return False
            moment = datetime.strptime(match.group(2), '%d/%m/%Y alle %H:%M').replace(tzinfo=timezone.utc)
            return window[0].replace(second=0, microsecond=0) <= moment <= window[1]
        return substitutions.get(value, value) == actual

    for name, rows in spec['rows'].items():
        model = model_class(name)
        for key, fields in rows.items():
            instance = model._base_manager.get(pk=key)
            for field, value in fields.items():
                contract.require(expect(value, render(getattr(instance, model_field(model, field).attname))), f'state.row:{name}.{field}')
    for name, fields in spec['new'].items():
        model = model_class(name)
        instance = model._base_manager.get(pk=created[name])
        for field, value in fields.items():
            contract.require(expect(value, render(getattr(instance, model_field(model, field).attname))), f'state.new:{name}.{field}')
    # The payment attachment links never change.
    contract.require(before['application.Payment_attachments'] == after['application.Payment_attachments'], 'state.attachments')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: event['id'])
    contract.require(len(added) == len(spec['audits']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = source['ids'][spec['actor']]
    for event, wanted in zip(added, spec['audits']):
        model = model_class(wanted['model'])
        key = substitutions.get(wanted['key'], wanted['key'])
        contract.require(event['content_type_id'] == types[wanted['model']] and event['action'] == wanted['action']
                         and event['object_pk'] == key and event['object_repr'] == f'{model.__name__} object ({key})', 'audit.identity')
        changes = wanted['changes']
        if '$created' in changes:
            expected = created_changes(model, model._base_manager.get(pk=key), changes['$created'])
            contract.require(event['changes'] == expected, 'audit.changes-created')
        else:
            old_rows = keyed(before[MODELS[wanted['model']]], keys[wanted['model']])
            actual = event['changes']
            contract.require(set(actual) == set(changes), 'audit.changes-fields')
            for field, (old_value, new_value) in changes.items():
                previous = None
                if old_value == '$before':
                    stored = old_rows[key][model_field(model, field).attname]
                    if isinstance(stored, dict) and stored.get('type') == 'datetime':
                        stored = stored['value']
                    previous = audit_value(model_field(model, field), datetime.fromisoformat(stored.replace('Z', '+00:00'))) \
                        if isinstance(stored, str) and model_field(model, field).get_internal_type() == 'DateTimeField' else stored
                contract.require(expect(old_value, actual[field][0], previous), 'audit.changes-old:' + field)
                contract.require(expect(new_value, actual[field][1]), 'audit.changes-new:' + field)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        context['remote'].setdefault('addr', event['remote_addr'])
        contract.require(context['remote']['addr'] == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if wanted['association'] is None:
            contract.require(index is None, 'index.absent')
        else:
            contract.require(index is not None and index['sport_association_id'] == wanted['association']
                             and index['resolution_path'] == wanted['path'], 'index.fields')
    contract.require(len(indexes) == sum(1 for wanted in spec['audits'] if wanted['association']), 'index.count')


def purge_payment():
    return model_class('payment')


def purge_invoice():
    return model_class('invoicesuppliers')


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(purge_invoice, purge_payment))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
