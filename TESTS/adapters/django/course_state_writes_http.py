"""Parent-only course enable, disable and pin HTTP runner; imports are inert.

Seeds courses of both associations (audit disabled) and verifies each case
against the complete model state: a successful case changes exactly one
course field (status_flag or pinned) with one auditlog update entry; every
other case leaves the state unchanged.
"""
from contextlib import contextmanager
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_course_state_writes_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/course_state_writes_http.json', 'TESTS/contracts/test_course_state_writes_http.py',
           'TESTS/adapters/django/course_state_writes_http.py', 'BE/application/views/course_views.py',
           'BE/application/models/courses_models.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py')
COURSE, AUDIT, INDEX = 'application.Course', 'auditlog.LogEntry', 'application.AuditLogIndex'
MANAGED = (COURSE, AUDIT, INDEX)
FIELDS = {'status_flag': 'status', 'pinned': 'pinned'}


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import Course
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for row in source['courses'].values():
            Course._base_manager.bulk_create([Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'],
                description='', fee=Decimal('10.00'), status_flag=row['status'], pinned=row['pinned'], deleted=row['deleted'],
                google_background_color='#123456', group_id=ids[row['group']] if row['group'] else None)])
            fixture['created'].append((Course, row['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
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
                    shared.delete_created(fixture)
            carnets.state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote):
    contract.require({k: v for k, v in before.items() if k not in MANAGED} == {k: v for k, v in after.items() if k not in MANAGED},
                     'state.other-models')
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    key = source['courses'][effect['label']]['id']
    old_c, new_c = keyed(before[COURSE], 'course_id'), keyed(after[COURSE], 'course_id')
    expected = dict(old_c[key], **{effect['field']: effect['to']})
    contract.require(old_c[key][effect['field']] == effect['from'], 'state.precondition')
    contract.require(set(new_c) == set(old_c) and new_c[key] == expected and all(new_c[k] == old_c[k] for k in old_c if k != key),
                     'state.course')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    indexes = {str(new_i[k]['log_entry_id']): new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_a[k] == old_a[k] for k in old_a) and all(new_i[k] == old_i[k] for k in old_i), 'audit.kept')
    contract.require(len(added) == 1 and len(indexes) == 1, 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    event = added[0]
    contract.require(event['content_type_id'] == types['course'] and event['action'] == 1 and event['object_pk'] == key
                     and event['object_repr'] == f'Course object ({key})', 'audit.identity')
    contract.require(event['changes'] == {effect['field']: [str(effect['from']), str(effect['to'])]}, 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes[str(event['id'])]
    contract.require(index['sport_association_id'] == own and index['resolution_path'] == 'direct', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
