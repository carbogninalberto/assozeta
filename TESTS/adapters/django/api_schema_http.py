"""Parent-only schema/, schema/swagger-ui/ and schema/redoc/ HTTP runner; imports are inert.

Nothing is seeded: the views read no rows. Every case must leave the complete
model state unchanged (the CSRF cookie lives in the response only).
"""
from TESTS.contracts import test_api_schema_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/api_schema_http.json', 'TESTS/contracts/test_api_schema_http.py',
           'TESTS/adapters/django/api_schema_http.py', 'TESTS/adapters/django/api_schema.py',
           'BE_GO/internal/apischema/schema.yaml', 'BE_GO/internal/apischema/schema.json',
           'BE_GO/internal/apischema/catalog.json', 'BE_GO/internal/apischema/testdata/catalog_keys.json',
           'BE/core/urls.py', 'BE/core/settings.py', 'BE/core/authentication.py', 'BE/application/impersonation.py',
           'BE/application/permissions_registry.py') + harness.SOURCES


def seed_rows(source, created):
    pass


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
