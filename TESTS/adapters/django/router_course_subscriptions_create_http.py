"""Parent-only DefaultRouter course-subscriptions create HTTP runner; imports are inert.

Reuses the course enrolment seed (associates, subscriptions, courses,
payments, course subscriptions, installments, a payment category and the
default bank account) and its effect check: created rows matched by their
authored columns, seeded rows unchanged unless authored, membership links,
and the exact auditlog entries in order with their index rows.
"""
from TESTS.contracts import test_router_course_subscriptions_create_http as contract
from TESTS.adapters.django import course_enrolment_http as enrolment
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/router_course_subscriptions_create_http.json', 'TESTS/contracts/test_router_course_subscriptions_create_http.py',
           'TESTS/contracts/router_stock.py', 'TESTS/adapters/django/router_course_subscriptions_create_http.py',
           'BE/application/urls.py') + tuple(source for source in enrolment.SOURCES if source not in harness.SOURCES) + harness.SOURCES
fixture_context = enrolment.fixture_context
run_workload = harness.run_workload_for(contract, enrolment.assert_effect)
