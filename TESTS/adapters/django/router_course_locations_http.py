"""Parent-only DefaultRouter course-locations HTTP runner; imports are inert.

Seeds the course-locations list rows and documents (audit disabled) and checks
each case against the complete model state with the course-location writes
effect check: creates, partial updates and deletes with one auditlog entry
each (none for an unchanged update), document links removed only with their
location. Reads and refused requests leave every model unchanged.
"""
from TESTS.contracts import test_router_course_locations_http as contract
from TESTS.adapters.django import course_location_writes_http as writes
from TESTS.adapters.django import course_locations_list_http as locations
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/router_course_locations_http.json', 'TESTS/contracts/test_router_course_locations_http.py',
           'TESTS/contracts/router_stock.py', 'TESTS/adapters/django/router_course_locations_http.py',
           'BE/application/urls.py') + tuple(source for source in writes.SOURCES if source not in harness.SOURCES
                                             and 'course_location_writes_http' not in source) + harness.SOURCES

fixture_context = harness.fixture_context_for(contract, locations.seed_rows, purge=(writes.location_model,))
run_workload = harness.run_workload_for(contract, writes.assert_effect, prepare=writes.deleted_rows)
