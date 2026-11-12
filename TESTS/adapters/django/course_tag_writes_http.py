"""Parent-only course tag writes HTTP runner; imports are inert.

Seeds the course-tag list rows plus courses and their tag links (audit
disabled) and verifies each case against the complete model state: tag
creates, renames and deletes with one auditlog entry each, and link changes
on the course M2M without audit. New tags are removed before the fixture
cleanup compares the model state.
"""
from contextlib import contextmanager
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_course_tag_writes_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import course_tags_list_http as tags
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_tag_writes_http.json', 'TESTS/contracts/test_course_tag_writes_http.py',
           'TESTS/adapters/django/course_tag_writes_http.py', 'TESTS/adapters/django/course_tags_list_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/course_views.py', 'BE/application/models/courses_models.py', 'BE/application/models/user_models.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py')
TAG, LINK, AUDIT, INDEX = 'application.CourseTags', 'application.Course_tags', 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (TAG, LINK, AUDIT, INDEX)


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Course
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        tags.seed_rows(source, fixture['created'])
        for row in source['courses'].values():
            Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'],
                description='', fee=Decimal('10.00'), status_flag=Course.ACTIVE, deleted=row['deleted'],
                group_id=ids[row['group']] if row['group'] else None)])
            Course.tags.through.objects.bulk_create([Course.tags.through(course_id=row['id'], coursetags_id=source['tags'][label]['id'])
                                                     for label in row['tags']])
            fixture['created'].append((Course, row['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models.courses_models import CourseTags
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    original = list(CourseTags._base_manager.values_list('pk', flat=True))
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
                    CourseTags._base_manager.exclude(pk__in=[*original, *seeded]).delete()
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
    return {(str(r['coursetags_id']), str(r['course_id'])) for r in (keyed([row], 'id')[str(topup.plain(row['id']))] for row in rows)}


def assert_effect(case, before, after, source, remote, fixture, rows):
    from application.models.courses_models import CourseTags
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    pair = lambda labels: (source['tags'][labels[0]]['id'], source['courses'][labels[1]]['id'])
    expected_links = (link_pairs(before[LINK]) | {pair(p) for p in effect.get('add', [])}) - {pair(p) for p in effect.get('remove', [])}
    contract.require(link_pairs(after[LINK]) == expected_links and len(after[LINK]) == len(expected_links), 'state.links')
    old_t, new_t = keyed(before[TAG], 'tag_id'), keyed(after[TAG], 'tag_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    if effect['kind'] == 'link':
        contract.require(new_t == old_t and not added and not indexes, 'state.link-only')
        return
    if effect['kind'] == 'add':
        keys = set(new_t) - set(old_t)
        contract.require(len(keys) == 1 and all(new_t[k] == old_t[k] for k in old_t), 'state.created')
        key = keys.pop()
        row = CourseTags._base_manager.get(pk=key)
        contract.require(row.tag_name == effect['stored'] and str(row.sport_association_id) == own, 'state.tag')
        action, path, changes = 0, 'direct', topup.created_changes(CourseTags, row, {'creation_date': topup.auditlog_moment(row.creation_date)})
    elif effect['kind'] == 'update':
        key = source['tags'][effect['tag']]['id']
        changes = effect['changes']
        expected = dict(old_t[key], tag_name=changes['tag_name'][1]) if changes else old_t[key]
        contract.require(set(new_t) == set(old_t) and new_t[key] == expected and all(new_t[k] == old_t[k] for k in old_t if k != key), 'state.tag')
        action, path = 1, 'direct'
    else:
        key = source['tags'][effect['tag']]['id']
        contract.require(set(new_t) == set(old_t) - {key}, 'state.deleted')
        action, path, changes = 2, 'changes_json.sport_association', rows[key]
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    for event in added:
        contract.require(event['content_type_id'] == types['coursetags'] and event['action'] == action and event['object_pk'] == key
                         and event['object_repr'] == f'CourseTags object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes[str(event['id'])]
        contract.require(index['sport_association_id'] == own and index['resolution_path'] == path, 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from application.models.courses_models import CourseTags
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        rows = {str(r.pk): replace.deleted_changes(CourseTags, r) for r in CourseTags._base_manager.filter(
            pk__in=[t['id'] for t in source['tags'].values()])}
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture, rows)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
