"""Parent-only DefaultRouter folders HTTP runner; imports are inert.

Seeds the authored folders with fixed ids and moves the folder sequence, as
folder-writes does, and checks every case with its effect check: nothing but
Folder rows may change (Folder is not audited), and after an effect case the
fixture folders carry exactly the authored names, parents, associations,
created_at, relative tree ids and nested-set numbers.
"""
from TESTS.contracts import test_router_folders_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import folder_writes_http as writes

SOURCES = ('TESTS/fixtures/router_folders_http.json', 'TESTS/contracts/test_router_folders_http.py',
           'TESTS/contracts/router_stock.py', 'TESTS/adapters/django/router_folders_http.py',
           'BE/application/urls.py') + tuple(source for source in writes.SOURCES if source not in harness.SOURCES
                                             and 'folder_writes_http' not in source) + harness.SOURCES + ('TESTS/adapters/django/folder_writes_http.py',)


def seed_rows(source, created):
    writes.seed_rows(source, created)


def assert_effect(case, before, after, source, context):
    writes.assert_effect(case, before, after, source, context)


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect, prepare=writes.request_time)
