"""Parent-only folders/add and folders/<pk>/update HTTP runner; imports are inert.

Seeds the authored folders through the ORM with fixed ids (MPTT computes their
trees; audit disabled) and moves the folder id sequence so new folders get
the authored ids. Every case is checked against the complete model state:
nothing but Folder rows may change (Folder is not audited, so no auditlog
entries or index rows either), folders outside the fixture never change, and
after an effect case every fixture folder carries exactly the authored name,
parent, association, created_at, relative tree id, lft, rght and level. A
server-time created_at must fall within the request. New folders are removed
(by their authored ids) before the fixture cleanup compares the model state.
"""
from datetime import datetime, timedelta, timezone

from TESTS.contracts import test_folder_writes_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/folder_writes_http.json', 'TESTS/contracts/test_folder_writes_http.py',
           'TESTS/adapters/django/folder_writes_http.py', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py') + harness.SOURCES
FOLDER = 'application.Folder'


def folder_model():
    from application.models import Folder
    return Folder


def moment(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def seed_rows(source, created):
    from django.db import connection
    Folder = folder_model()
    contract.require(not Folder._base_manager.filter(pk__gte=source['first_id']).exists(), 'fixture.folder-ids-free')
    for row in source['folders']:
        Folder.objects.create(id=row['id'], name=row['name'], parent_id=row['parent'], sport_association_id=row['association'],
                              created_at=moment(row['created_at']))
        created.append((Folder, row['id']))
    # The authored new folders, deleted first at cleanup (the harness purge
    # only handles UUID keys).
    created.extend((Folder, ident) for ident in source['new_ids'])
    with connection.cursor() as cursor:
        cursor.execute("SELECT setval(pg_get_serial_sequence('application_folder', 'id'), %s, true)", [source['next_id'] - 1])


def request_time(source):
    return datetime.now(timezone.utc)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, (FOLDER,))
    # Folder is not registered with auditlog: no entry and no index row.
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(not added and not indexes, 'audit.none')
    first = source['first_id']
    old, new = keyed(before[FOLDER], 'id'), keyed(after[FOLDER], 'id')
    outside = lambda rows: {key: row for key, row in rows.items() if int(key) < first}
    mine = lambda rows: {key: row for key, row in rows.items() if int(key) >= first}
    contract.require(outside(old) == outside(new), 'state.other-folders')
    if 'offset' not in context:
        # The first case runs on the seeded trees; the first seeded folder is a root.
        context['offset'] = old[str(source['folders'][0]['id'])]['tree_id'] - 1
    if case['state'] is None:
        contract.require(mine(old) == mine(new), 'state.no-effect')
        return
    rows, expected = mine(new), case['state']
    contract.require(set(rows) == set(expected), 'state.folders')
    finished = datetime.now(timezone.utc)
    for key, want in expected.items():
        row = rows[key]
        contract.require([row['name'], row['parent_id'], str(row['sport_association_id']), row['tree_id'] - context['offset'], row['lft'],
                          row['rght'], row['level']] == [want['name'], want['parent'], want['association'], want['tree'], want['lft'],
                                                         want['rght'], want['level']], 'state.folder-' + key)
        stored = moment(row['created_at'])
        if want['created_at'] != '$now':
            contract.require(stored == moment(want['created_at']), 'state.created-at-' + key)
        elif key in old:
            contract.require(row['created_at'] == old[key]['created_at'], 'state.created-at-' + key)
        else:
            started = context['prepared']
            contract.require(started - timedelta(seconds=1) <= stored <= finished + timedelta(seconds=1), 'state.created-now-' + key)


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect, prepare=request_time)
