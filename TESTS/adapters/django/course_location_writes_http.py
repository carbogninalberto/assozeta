"""Parent-only course location writes HTTP runner; imports are inert.

Seeds the course-locations list rows and documents (audit disabled) and
verifies each case against the complete model state: location creates,
updates and deletes with one auditlog entry each (none for an unchanged
update), document links removed only with their location and documents
kept. New locations are removed before the fixture cleanup compares the
model state.
"""
from TESTS.contracts import test_course_location_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import course_locations_list_http as locations
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_location_writes_http.json', 'TESTS/contracts/test_course_location_writes_http.py',
           'TESTS/adapters/django/course_location_writes_http.py', 'TESTS/adapters/django/course_locations_list_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py') + harness.SOURCES
LOCATION, LINK = 'application.CourseLocation', 'application.CourseLocation_documents'
MANAGED = (LOCATION, LINK, harness.AUDIT, harness.INDEX)
FIELDS = ('title', 'description', 'address')


def location_model():
    from application.models.courses_models import CourseLocation
    return CourseLocation


def deleted_rows(source):
    """auditlog's delete diffs of the seeded locations, captured before each case."""
    model = location_model()
    return {str(row.pk): replace.deleted_changes(model, row)
            for row in model._base_manager.filter(pk__in=[row['id'] for row in source['locations'].values()])}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_l, new_l = keyed(before[LOCATION], 'course_location_id'), keyed(after[LOCATION], 'course_location_id')
    stored = lambda row: {field: row[field] for field in FIELDS}
    if effect['kind'] == 'add':
        keys = set(new_l) - set(old_l)
        contract.require(len(keys) == 1 and all(new_l[k] == old_l[k] for k in old_l), 'state.created')
        key = keys.pop()
        contract.require(effect['id'] == '$new' or key == effect['id'], 'state.created-id')
        contract.require(stored(new_l[key]) == effect['stored'] and str(new_l[key]['sport_association_id']) == own, 'state.location')
        contract.require(after[LINK] == before[LINK], 'state.links')
        action, path = 0, 'direct'
        changes = topup.created_changes(location_model(), location_model()._base_manager.get(pk=key), {})
    elif effect['kind'] == 'update':
        key = source['locations'][effect['label']]['id']
        contract.require(set(new_l) == set(old_l) and stored(new_l[key]) == effect['stored']
                         and str(new_l[key]['sport_association_id']) == own
                         and all(new_l[k] == old_l[k] for k in old_l if k != key), 'state.location')
        contract.require(after[LINK] == before[LINK], 'state.links')
        action, path, changes = 1, 'direct', effect['changes']
    else:
        key = source['locations'][effect['label']]['id']
        contract.require(set(new_l) == set(old_l) - {key}, 'state.deleted')
        kept = {pair for pair in harness.pairs(before[LINK], 'courselocation_id', 'document_id') if pair[0] != key}
        contract.require(harness.pairs(after[LINK], 'courselocation_id', 'document_id') == kept and len(after[LINK]) == len(kept),
                         'state.links')
        action, path, changes = 2, 'changes_json.sport_association', context['prepared'][key]
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    for event in added:
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='CourseLocation', key=key,
                                  action=action, changes=changes, actor=effect['actor'], association=own, path=path, context=context)


fixture_context = harness.fixture_context_for(contract, locations.seed_rows, purge=(location_model,))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
