"""Parent-only unmatched-URL HTTP runner; imports are inert.

Nothing is seeded: no case resolves a view, so the complete model state and
the cache must stay unchanged.
"""
from TESTS.contracts import test_unknown_paths_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/unknown_paths_http.json', 'TESTS/contracts/test_unknown_paths_http.py',
           'TESTS/adapters/django/unknown_paths_http.py', 'BE/core/urls.py', 'BE/core/middleware.py', 'BE/core/settings.py',
           'BE/application/urls.py', 'BE/application/chat/urls.py', 'BE/docmanager/urls.py', 'BE/communications/urls.py',
           'BE/instance/urls.py', 'BE/instance/sso/urls.py', 'BE/instance/restore/locking.py') + harness.SOURCES


def seed_rows(source, created):
    pass


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
