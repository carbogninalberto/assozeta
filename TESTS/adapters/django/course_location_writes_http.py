"""Parent-only course location writes HTTP runner; imports are inert.

Seeds the course-locations list rows and documents (audit disabled) and
verifies each case against the complete model state: location creates,
updates and deletes with one auditlog entry each (none for an unchanged
update), document links removed only with their location and documents
kept. New locations are removed before the fixture cleanup compares the
model state.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_course_location_writes_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import course_locations_list_http as locations
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/course_location_writes_http.json', 'TESTS/contracts/test_course_location_writes_http.py',
           'TESTS/adapters/django/course_location_writes_http.py', 'TESTS/adapters/django/course_locations_list_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py')
LOCATION, LINK, AUDIT, INDEX = ('application.CourseLocation', 'application.CourseLocation_documents', 'auditlog.LogEntry',
                                'application.AuditLogIndex')
MANAGED = (LOCATION, LINK, AUDIT, INDEX)
FIELDS = ('title', 'description', 'address')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        locations.seed_rows(contract.load_source(), fixture['created'])
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models.courses_models import CourseLocation
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    original = list(CourseLocation._base_manager.values_list('pk', flat=True))
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=original_audit).delete()
                if fixture is not None:
                    seeded = [pk for model, pk in fixture['created']]
                    CourseLocation._base_manager.exclude(pk__in=[*original, *seeded]).delete()
                    shared.delete_created(fixture)
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def link_pairs(rows):
    return {(str(scalar(row['courselocation_id'])), str(scalar(row['document_id']))) for row in rows}


def assert_effect(case, before, after, source, remote, rows):
    from application.models.courses_models import CourseLocation
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_l, new_l = keyed(before[LOCATION], 'course_location_id'), keyed(after[LOCATION], 'course_location_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    stored = lambda row: {field: row[field] for field in FIELDS}
    if effect['kind'] == 'add':
        keys = set(new_l) - set(old_l)
        contract.require(len(keys) == 1 and all(new_l[k] == old_l[k] for k in old_l), 'state.created')
        key = keys.pop()
        contract.require(effect['id'] == '$new' or key == effect['id'], 'state.created-id')
        contract.require(stored(new_l[key]) == effect['stored'] and str(new_l[key]['sport_association_id']) == own, 'state.location')
        contract.require(after[LINK] == before[LINK], 'state.links')
        action, path = 0, 'direct'
        changes = topup.created_changes(CourseLocation, CourseLocation._base_manager.get(pk=key), {})
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
        kept = {pair for pair in link_pairs(before[LINK]) if pair[0] != key}
        contract.require(link_pairs(after[LINK]) == kept and len(after[LINK]) == len(kept), 'state.links')
        action, path, changes = 2, 'changes_json.sport_association', rows[key]
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    for event in added:
        contract.require(event['content_type_id'] == types['courselocation'] and event['action'] == action and event['object_pk'] == key
                         and event['object_repr'] == f'CourseLocation object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes[str(event['id'])]
        contract.require(index['sport_association_id'] == own and index['resolution_path'] == path, 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models.courses_models import CourseLocation
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): replace.deleted_changes(CourseLocation, r) for r in CourseLocation._base_manager.filter(
            pk__in=[row['id'] for row in source['locations'].values()])}
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, rows)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
