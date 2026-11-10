"""Parent-only API v2 handoff HTTP runner; imports are inert.

Seeds the personas-reads, course-list and workflow-update rows (their own seed
functions; the ids do not overlap) and runs Go with DJANGO_HANDOFF_URL set to
the native Django of the run (GO_DJANGO_HANDOFF). Read cases must leave the
complete state unchanged; workflow cases are checked by workflow-update's effect
check (the authored row and audit entries, actor, correlation id and no
published task), so a handed-off write must have exactly Django's effect.

HANDOFF_FAMILY=off runs the same sequence with the handoff disabled: Go then
answers the refused cases with its 500 page (the RED receipt of handoff.md).
"""
import os

from TESTS.contracts import test_django_handoff_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import course_list_http as course_list
from TESTS.adapters.django import personas_reads_http as personas_reads
from TESTS.adapters.django import workflow_update_http as workflow_update

SOURCES = ('TESTS/fixtures/django_handoff_http.json', 'TESTS/contracts/test_django_handoff_http.py', 'TESTS/adapters/django/django_handoff_http.py',
           'BE_GO/internal/handoff/handoff.go', 'BE_GO/internal/handoff/effects.go', 'BE_GO/internal/refusal/refusal.go') \
    + tuple(dict.fromkeys(personas_reads.SOURCES + course_list.SOURCES + workflow_update.SOURCES))
FAMILIES = {'personas-reads': personas_reads, 'course-list': course_list, 'workflow-update': workflow_update}
GO_DJANGO_HANDOFF = os.environ.get('HANDOFF_FAMILY', 'on') != 'off'


def seed_rows(source, created):
    for name in source['sources']:
        family = FAMILIES[name]
        family.seed_rows(family.contract.load_source(), created)


def assert_effect(case, before, after, source, context):
    if case['family'] == 'workflow-update':
        workflow_update.assert_effect(case, before, after, workflow_update.contract.load_source(), context)
        return
    contract.require(case['effect'] is None and before == after, 'state.read-unchanged')


def handoff(case, backend):
    return GO_DJANGO_HANDOFF and contract.handed_off(case, backend)


# workflow-update's effect check also reads the Celery broker (these cases publish nothing).
fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=workflow_update.prepare, handoff=handoff)
