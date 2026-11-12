"""Parent-only folders/<pk>/move HTTP runner; imports are inert.

Creates the authored MPTT folders through the ORM (audit disabled) and
verifies each case against the complete model state: only Folder rows may
change, and after a move every folder carries the authored tree id, lft,
rght, level and parent.
"""
from contextlib import contextmanager
from pathlib import Path

from TESTS.contracts import test_folders_move_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/folders_delete_http.json', 'TESTS/contracts/test_folders_move_http.py',
           'TESTS/adapters/django/folders_move_http.py', 'BE/application/views/archive_views.py', 'BE/application/models/user_models.py')
FOLDER = 'application.Folder'


def state_unchanged(before, after):
    contract.require(before == after, 'state.changed')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Folder
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        folders = {}
        for label, parent, association in source['folders']:
            folders[label] = Folder.objects.create(name=label, parent_id=folders.get(parent), sport_association_id=association).pk
        fixture['folders'] = folders
        fixture['tree_offset'] = Folder._base_manager.get(pk=folders['a']).tree_id - 1
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
    contract.require({k: v for k, v in before.items() if k != FOLDER} == {k: v for k, v in after.items() if k != FOLDER}, 'state.other-models')
    old_f, new_f = keyed(before[FOLDER], 'id'), keyed(after[FOLDER], 'id')
    if case['after'] is None:
        contract.require(old_f == new_f, 'state.no-effect')
        return
    ids = fixture['folders']
    offset = fixture['tree_offset']
    contract.require(set(new_f) == {str(ids[label]) for label in case['after']} | (set(new_f) - {str(i) for i in ids.values()}), 'state.folders')
    for label, (tree, left, right, level, parent) in case['after'].items():
        row = new_f[str(ids[label])]
        contract.require([row['tree_id'], row['lft'], row['rght'], row['level'], row['parent_id']]
                         == [tree + offset, left, right, level, ids[parent] if parent else None], 'state.tree-' + label)
        contract.require({k: v for k, v in row.items() if k not in ('tree_id', 'lft', 'rght', 'level', 'parent_id')}
                         == {k: v for k, v in old_f[str(ids[label])].items() if k not in ('tree_id', 'lft', 'rght', 'level', 'parent_id')},
                         'state.folder-fields')


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
