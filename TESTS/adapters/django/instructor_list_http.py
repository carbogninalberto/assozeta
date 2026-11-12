"""Parent-only instructor/list HTTP runner; imports are inert.

Seeds instructors and their documents (audit disabled). The routes are reads: every case must leave
the complete model state unchanged.
"""
from contextlib import contextmanager
from pathlib import Path

from TESTS.contracts import test_instructor_list_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/instructor_list_http.json', 'TESTS/contracts/test_instructor_list_http.py',
           'TESTS/adapters/django/instructor_list_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/instructor_views.py', 'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py')


def seed_rows(source, created):
    from datetime import date
    from decimal import Decimal
    from application.models import Instructor
    from docmanager.models import Document
    ids = source['ids']
    for label, pk in source['documents'].items():
        Document._base_manager.bulk_create([Document(document_id=pk, filename=f'{label}.pdf')])
        created.append((Document, pk))
    day = lambda text: date.fromisoformat(text) if text else None
    for row in source['instructors'].values():
        values = {k: row[k] for k in ('first_name', 'last_name', 'email', 'born_city', 'born_province', 'tax_code', 'address_city', 'address',
                                      'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3', 'phone_4',
                                      'draft', 'associated_user_id', 'is_volunteer')}
        Instructor._base_manager.bulk_create([Instructor(instructor_id=row['id'], user_id=ids[row['owner']], born_date=day(row['born_date']),
            stipulated_contract_in=day(row['stipulated_contract_in']), default_hourly_billing=Decimal(row['default_hourly_billing']),
            default_percentage_billing=Decimal(row['default_percentage_billing']) if row['default_percentage_billing'] else None, **values)])
        created.append((Instructor, row['id']))
        Instructor.documents.through.objects.bulk_create([Instructor.documents.through(instructor_id=row['id'], document_id=source['documents'][d])
                                                         for d in row['documents']])


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
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
