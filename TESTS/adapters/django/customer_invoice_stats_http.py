"""Parent-only customer-invoice stats HTTP runner; imports are inert.

Seeds the shared identity fixture plus customer invoices whose due dates are
anchored at the seed-time date (audit disabled), and checks that no case
changes any model state.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from TESTS.contracts import test_customer_invoice_stats_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/supplier_invoice_stats_http.json', 'TESTS/contracts/test_customer_invoice_stats_http.py',
           'TESTS/adapters/django/supplier_invoice_stats_http.py', 'BE/application/views/invoice_views.py',
           'BE/application/models/invoices_models.py')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import CustomerInvoice
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    today = datetime.now(timezone.utc).date()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        rows = {}
        for label, row in source['invoices'].items():
            due = today + timedelta(days=row['expire'])
            CustomerInvoice._base_manager.bulk_create([CustomerInvoice(
                customer_invoice_id=row['id'], sport_association_id=row['association'],
                payment_total_amount=Decimal(row['amount']), paid=row['paid'], payment_expiry_date=due,
                prefix=row['prefix'], number=row['number'], fiscal_year=row['fiscal_year'])])
            fixture['created'].append((CustomerInvoice, row['id']))
            rows[label] = {'payment_expiry_date': due.isoformat(), 'payment_total_amount': float(Decimal(row['amount'])),
                           'prefix': row['prefix'], 'number': row['number'], 'fiscal_year': row['fiscal_year']}
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
