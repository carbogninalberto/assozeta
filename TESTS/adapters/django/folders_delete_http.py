"""Parent-only folders/<pk>/delete HTTP runner; imports are inert.

Creates the authored MPTT folders, a document and an archive row filed in a
folder through the ORM (audit disabled), and verifies each case against the
complete model state: only the authored folders may disappear, the remaining
own folders must carry the authored (lft, rght), and only the authored archive
rows may lose their folder.
"""
from contextlib import contextmanager
from pathlib import Path

from TESTS.contracts import test_folders_delete_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/folders_delete_http.json', 'TESTS/contracts/test_folders_delete_http.py',
           'TESTS/adapters/django/folders_delete_http.py', 'BE/application/views/archive_views.py', 'BE/application/models/user_models.py')
FOLDER, ARCHIVE = 'application.Folder', 'application.SportAssociationDocumentsArchive'


def state_unchanged(before, after):
    contract.require(before == after, 'state.changed')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Folder, SportAssociationDocumentsArchive
    from docmanager.models import Document
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        folders = {}
        for label, parent, association in source['folders']:
            folders[label] = Folder.objects.create(name=label, parent_id=folders.get(parent), sport_association_id=association).pk
        archive = source['archive']
        Document.objects.create(document_id=archive['document'], filename='archivio.pdf')
        fixture['created'].append((Document, archive['document']))
        SportAssociationDocumentsArchive.objects.create(sport_association_documents_archive_id=archive['id'],
            sport_association_id=archive['association'], document_id=archive['document'], folder_id=folders[archive['folder']])
        fixture['created'].append((SportAssociationDocumentsArchive, archive['id']))
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
                    Folder._base_manager.filter(pk__in=list(fixture['folders'].values())).delete()
                shared.delete_created(fixture)
            state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, fixture):
    managed = (FOLDER, ARCHIVE)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_f, new_f = keyed(before[FOLDER], 'id'), keyed(after[FOLDER], 'id')
    old_a, new_a = keyed(before[ARCHIVE], 'sport_association_documents_archive_id'), keyed(after[ARCHIVE], 'sport_association_documents_archive_id')
    effect = case['effect']
    if effect is None:
        contract.require(old_f == new_f and old_a == new_a, 'state.no-effect')
        return
    folders = {str(identifier): label for label, identifier in fixture['folders'].items()}
    gone = {str(fixture['folders'][label]) for label in effect['deleted']}
    contract.require(gone <= set(old_f) and not gone & set(new_f) and set(new_f) == set(old_f) - gone, 'state.deleted')
    for key, row in new_f.items():
        label = folders.get(key)
        if label in effect['tree']:
            contract.require([row['lft'], row['rght']] == effect['tree'][label], 'state.tree-' + label)
            contract.require({k: v for k, v in row.items() if k not in ('lft', 'rght')} == {k: v for k, v in old_f[key].items() if k not in ('lft', 'rght')},
                             'state.folder-fields')
        else:
            contract.require(row == old_f[key], 'state.other-folders')
    for key, row in new_a.items():
        if key in effect['detached']:
            contract.require(row['folder_id'] is None and {k: v for k, v in row.items() if k != 'folder_id'}
                             == {k: v for k, v in old_a[key].items() if k != 'folder_id'}, 'state.detached')
        else:
            contract.require(row == old_a[key], 'state.archive')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], folders=fixture['folders'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], contract.bind_path(case['path'], fixture['folders']), status,
                                      observer.delta(cursor), case['name'])
            assert_effect(case, before, after, fixture)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
