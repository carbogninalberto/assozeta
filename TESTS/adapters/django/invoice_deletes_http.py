"""Parent-only invoice delete HTTP runner; imports are inert.

Seeds a supplier plus supplier and customer invoices with every field
explicit (audit disabled) and verifies each case against the complete model
state: only the authored invoice row may disappear, with one exact auditlog
delete entry and its changes_json index row.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_invoice_deletes_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/invoice_deletes_http.json', 'TESTS/contracts/test_invoice_deletes_http.py',
           'TESTS/adapters/django/invoice_deletes_http.py', 'BE/application/views/invoice_views.py',
           'BE/application/models/invoices_models.py', 'BE/application/audit_resolvers.py')
MODELS = {'invoicesuppliers': 'application.InvoiceSuppliers', 'customerinvoice': 'application.CustomerInvoice'}
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'


def state_unchanged(before, after):
    # Seeded DateField values are date objects, which the shared JSON digest cannot encode.
    contract.require(before == after, 'state.changed')


def field_values(model, row):
    values = {}
    for field in model._meta.fields:
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


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import CustomerInvoice, InvoiceSuppliers, SupplierAndCustomers
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        supplier = source['supplier']
        SupplierAndCustomers._base_manager.bulk_create([SupplierAndCustomers(
            supplier_id=supplier['id'], sport_association_id=supplier['association'], name=supplier['name'])])
        fixture['created'].append((SupplierAndCustomers, supplier['id']))
        for row in source['seed'].values():
            model = InvoiceSuppliers if 'invoice_supplier_id' in row else CustomerInvoice
            instance = model(**field_values(model, row))
            model._base_manager.bulk_create([instance])
            fixture['created'].append((model, instance.pk))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=original_audit).delete()
                if fixture is not None:
                    shared.delete_created(fixture)
            state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote):
    managed = (*MODELS.values(), AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    effect = case['effect']
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    for model, label in MODELS.items():
        key = 'invoice_supplier_id' if model == 'invoicesuppliers' else 'customer_invoice_id'
        old_rows, new_rows = keyed(before[label], key), keyed(after[label], key)
        if effect and effect['model'] == model:
            gone = effect['id']
            contract.require(gone in old_rows and {k: v for k, v in old_rows.items() if k != gone} == new_rows, 'state.deleted')
        else:
            contract.require(old_rows == new_rows, 'state.no-delete')
    if not effect:
        contract.require(old_a == new_a and old_i == new_i, 'audit.none')
        return
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = added[0]
    ids = source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == effect['model']]
    name = 'InvoiceSuppliers' if effect['model'] == 'invoicesuppliers' else 'CustomerInvoice'
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == effect['id'] and event['action'] == 2
                     and event['object_repr'] == f'{name} object ({effect["id"]})', 'audit.identity')
    contract.require(event['changes'] == effect['changes'], 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    contract.require(event['actor_id'] == ids['owner'] and event['actor_email'] == users[ids['owner']]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id'] and added_i[0]['sport_association_id'] == ids['own_association']
                     and added_i[0]['resolution_path'] == 'changes_json.sport_association', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
