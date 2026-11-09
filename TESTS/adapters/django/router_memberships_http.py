"""Parent-only DefaultRouter subscription/memberships HTTP runner; imports are inert.

Seeds the subscription memberships rows (documents, associates,
subscriptions, payments, memberships and links; audit disabled) and checks
each case with the subscription memberships effect check: creates, partial
updates and deletes with one auditlog entry each, document links removed
only with their membership. Everything else must leave the state unchanged.
"""
from TESTS.contracts import test_router_memberships_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import subscription_memberships_http as memberships

SOURCES = ('TESTS/fixtures/router_memberships_http.json', 'TESTS/contracts/test_router_memberships_http.py',
           'TESTS/contracts/router_stock.py', 'TESTS/adapters/django/router_memberships_http.py',
           'BE/application/urls.py') + tuple(source for source in memberships.SOURCES if source not in harness.SOURCES
                                             and 'subscription_memberships_http' not in source) + harness.SOURCES

fixture_context = harness.fixture_context_for(contract, memberships.seed_rows, purge=(memberships.membership_model,))
run_workload = harness.run_workload_for(contract, memberships.assert_effect, prepare=memberships.deleted_rows)
