"""Parent-only balance-sheet/archived HTTP runner; imports are inert.

Seeds balance sheets (audit disabled). The embedded association serializer must be
identical across backends. The routes are reads: every case must leave
the complete model state unchanged.
"""
from contextlib import contextmanager
from pathlib import Path

from TESTS.contracts import test_balance_archived_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/balance_archived_http.json', 'TESTS/contracts/test_balance_archived_http.py',
           'TESTS/adapters/django/balance_archived_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/balance_sheet_views.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/permissions.py')
PROFILES = {}


def seed_rows(source, created):
    from datetime import datetime
    from application.models.balance_sheet_models import BalanceSheet
    for row in source['sheets'].values():
        BalanceSheet._base_manager.bulk_create([BalanceSheet(balance_sheet_id=row['id'], sport_association_id=row['association'],
            status_flag=row['status'], year=row['year'], archived=row['archived'], data=row['data'],
            creation_date=datetime.fromisoformat(row['created'].replace('Z', '+00:00')))])
        created.append((BalanceSheet, row['id']))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        seed_rows(contract.load_source(), fixture['created'])
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
    original_audit = [row['id'] for row in before['auditlog.LogEntry']]
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
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
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
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            if case['name'] in contract.LAST_PROFILE:
                profile = contract.LAST_PROFILE.pop(case['name'])
                contract.require(PROFILES.setdefault(case['name'], profile) == profile, 'response.association-differs')
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
