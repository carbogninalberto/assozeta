"""Parent-only email-logs HTTP runner; imports are inert.

Seeds the shared identity fixture plus email logs anchored at seed time, runs
the authored sequence against one endpoint, and requires every case to leave
the complete model state and historical cache unchanged with exact upstream
attribution. A Django-only run is reference evidence, not parity.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from TESTS.contracts import test_email_logs_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations


def seed_fixture(apps, private_work, raw):
    from django.db import transaction
    from application.models import EmailLog
    source = contract.load_source()
    with transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        anchor = datetime.now(timezone.utc)
        for row in source['rows'].values():
            contract.require(not EmailLog.objects.filter(pk=row['id']).exists(), 'fixture.row-collision')
            EmailLog.objects.create(email_log_id=row['id'], sport_association_id=row['association'],
                                    recipient=row['recipient'], subject=row['subject'], result=row['result'])
            EmailLog.objects.filter(pk=row['id']).update(sent_at=anchor - timedelta(seconds=row['offset']))
            fixture['created'].append((EmailLog, row['id']))
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        fixture['anchor'] = anchor
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


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
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
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
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'],
                                          source=source, anchor=fixture['anchor'])
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
