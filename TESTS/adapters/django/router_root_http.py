"""Parent-only DefaultRouter API root HTTP runner; imports are inert.

Nothing is seeded beyond the shared identity fixture; every case is a read
and must leave the complete model state unchanged.
"""
from TESTS.contracts import test_router_root_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/router_root_http.json', 'TESTS/contracts/test_router_root_http.py', 'TESTS/contracts/router_stock.py',
           'TESTS/adapters/django/router_root_http.py', 'BE/application/urls.py', 'BE/core/urls.py', 'BE/core/settings.py') + harness.SOURCES


def seed_rows(source, created):
    pass


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
