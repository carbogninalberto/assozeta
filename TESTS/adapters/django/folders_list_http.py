"""Parent-only folders/list HTTP runner; imports are inert.

Creates the authored MPTT folders through the ORM (audit disabled) and checks
that no case changes any model state.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from TESTS.contracts import test_folders_list_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/folders_list_http.json', 'TESTS/contracts/test_folders_list_http.py',
           'TESTS/adapters/django/folders_list_http.py', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Folder
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        folders = {}
        for label, parent, association, created in source['folders']:
            folder = Folder.objects.create(name=source['names'][label], parent_id=folders.get(parent), sport_association_id=association,
                                           created_at=datetime.fromisoformat(created.replace('Z', '+00:00')))
            folders[label] = folder.pk
            fixture['created'].append((Folder, folder.pk))
        fixture['folders'] = folders
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
            from auditlog.context import disable_auditlog
            from application.models import Folder
            if fixture is not None:
                with disable_auditlog():
                    # Delete trees from the roots; MPTT cascades to descendants.
                    Folder._base_manager.filter(pk__in=list(fixture.get('folders', {}).values()), parent__isnull=True).delete()
                fixture['created'] = [entry for entry in fixture['created'] if entry[0] is not Folder]
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
    results = []
    for case in source['cases']:
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], folders=fixture['folders'])
            shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
            path = contract.bind_path(case['path'], fixture['folders'])
            shared.assert_attribution(backend, case['method'], path, status, observer.delta(cursor), case['name'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
