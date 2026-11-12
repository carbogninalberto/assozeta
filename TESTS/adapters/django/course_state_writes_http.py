"""Parent-only course enable, disable and pin HTTP runner; imports are inert.

Seeds courses of both associations (audit disabled) and verifies each case
against the complete model state: a successful case changes exactly one
course field (status_flag or pinned) with one auditlog update entry; every
other case leaves the state unchanged.
"""
from decimal import Decimal

from TESTS.contracts import test_course_state_writes_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_state_writes_http.json', 'TESTS/contracts/test_course_state_writes_http.py',
           'TESTS/adapters/django/course_state_writes_http.py', 'BE/application/views/course_views.py',
           'BE/application/models/courses_models.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py') + harness.SOURCES
COURSE = 'application.Course'
MANAGED = (COURSE, harness.AUDIT, harness.INDEX)


def seed_rows(source, created):
    from application.models import Course
    ids = source['ids']
    for row in source['courses'].values():
        Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'],
            description='', fee=Decimal('10.00'), status_flag=row['status'], pinned=row['pinned'], deleted=row['deleted'],
            google_background_color='#123456', group_id=ids[row['group']] if row['group'] else None)])
        created.append((Course, row['id']))


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    key = source['courses'][effect['label']]['id']
    old_c, new_c = keyed(before[COURSE], 'course_id'), keyed(after[COURSE], 'course_id')
    contract.require(old_c[key][effect['field']] == effect['from'], 'state.precondition')
    contract.require(set(new_c) == set(old_c) and new_c[key] == dict(old_c[key], **{effect['field']: effect['to']})
                     and all(new_c[k] == old_c[k] for k in old_c if k != key), 'state.course')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == 1 and len(indexes) == 1, 'audit.count')
    harness.check_audit_event(contract, case, added[0], indexes, before=before, ids=ids, model='Course', key=key, action=1,
                              changes={effect['field']: [str(effect['from']), str(effect['to'])]}, actor=effect['actor'],
                              association=ids['own_association'], path='direct', context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)
