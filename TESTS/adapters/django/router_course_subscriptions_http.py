"""Parent-only DefaultRouter course-subscriptions read, update and delete HTTP runner; imports are inert.

Seeds the course-subscriptions rows (associates, medicals, subscriptions,
courses, payments, course subscriptions, installments, carnets; audit
disabled) and checks every case with the course-subscriptions effect check:
partial updates and soft deletes (installments, their unpaid payments, the
row's deleted flag) with their exact auditlog entries; everything else
leaves the state unchanged.
"""
from contextlib import contextmanager

from TESTS.contracts import test_router_course_subscriptions_http as contract
from TESTS.adapters.django import course_subscriptions_http as subscriptions
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/router_course_subscriptions_http.json', 'TESTS/fixtures/generate_course_subscriptions_http.py',
           'TESTS/contracts/test_router_course_subscriptions_http.py', 'TESTS/contracts/router_stock.py',
           'TESTS/contracts/test_course_subscriptions_http.py', 'TESTS/adapters/django/router_course_subscriptions_http.py',
           'BE/application/urls.py') + tuple(source for source in subscriptions.SOURCES if source not in harness.SOURCES
                                             and 'course_subscriptions_http' not in source) + harness.SOURCES \
    + ('TESTS/adapters/django/course_subscriptions_http.py',)
base_fixture_context = harness.fixture_context_for(contract, subscriptions.seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = subscriptions.default_category()
        fixture['default_category'] = category
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        yield fixture


run_workload = harness.run_workload_for(contract, subscriptions.assert_effect, prepare=subscriptions.captured)
