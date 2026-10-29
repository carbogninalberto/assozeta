"""Parent-only supplier-invoice stats HTTP runner; imports are inert.

Seeds the shared identity fixture plus a supplier and invoices whose expiry
dates are anchored at seed time (whole seconds, audit disabled), and checks
that no case changes any model state.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from TESTS.contracts import test_supplier_invoice_stats_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/supplier_invoice_stats_http.json', 'TESTS/contracts/test_supplier_invoice_stats_http.py',
           'TESTS/adapters/django/supplier_invoice_stats_http.py', 'BE/application/views/invoice_views.py',
           'BE/application/models/invoices_models.py')


def drf_datetime(value):
    return value.isoformat().replace('+00:00', 'Z')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import InvoiceSuppliers, SupplierAndCustomers
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        supplier = source['supplier']
        SupplierAndCustomers._base_manager.bulk_create([SupplierAndCustomers(
            supplier_id=supplier['id'], sport_association_id=supplier['association'], name=supplier['name'])])
        # delete_created removes in reverse order: invoices before their supplier.
        fixture['created'].append((SupplierAndCustomers, supplier['id']))
        rows = {}
        for label, row in source['invoices'].items():
            expire = None if row['expire'] is None else anchor + timedelta(days=row['expire'])
            InvoiceSuppliers._base_manager.bulk_create([InvoiceSuppliers(
                invoice_supplier_id=row['id'], sport_association_id=row['association'], amount=Decimal(row['amount']),
                paid=row['paid'], expire_date=expire, invoice_identifier=row['identifier'],
                supplier_id=supplier['id'] if row['supplier'] else None)])
            fixture['created'].append((InvoiceSuppliers, row['id']))
            rows[label] = {'expire_date': None if expire is None else drf_datetime(expire), 'amount': float(Decimal(row['amount'])),
                           'invoice_identifier': row['identifier'], 'supplier__name': supplier['name'] if row['supplier'] else None}
        fixture['rows'] = rows
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


def state_unchanged(before, after):
    contract.require(before == after, 'state.changed')


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
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


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], rows=fixture['rows'])
            state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
