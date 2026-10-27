"""Parent-only workflow add/delete HTTP runner; imports are inert.

Seeds the shared identity fixture plus workflows (audit disabled), runs the
authored sequence, and verifies each case against the complete model state:
only the authored workflow row, one exact audit entry and one audit index row
may change. Remote addresses are checked as valid and consistent per workload.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import time
from pathlib import Path

from TESTS.contracts import test_workflow_writes_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations

SOURCES = ('TESTS/fixtures/workflow_writes_http.json', 'TESTS/contracts/test_workflow_writes_http.py',
           'TESTS/adapters/django/workflow_writes_http.py', 'BE/communications/views.py', 'BE/communications/serializers.py',
           'BE/communications/models.py', 'BE/application/audit_resolvers.py')
WORKFLOW, AUDIT, INDEX = 'communications.AutomationWorkflow', 'auditlog.LogEntry', 'application.AuditLogIndex'


def scalar(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime'):
        return value['value']
    return value


def keyed(rows, key):
    return {str(scalar(row[key])): {k: scalar(v) for k, v in row.items()} for row in rows}


def parse(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def audit_time(text):
    """auditlog's str(datetime) of the naive UTC value."""
    return str(parse(text).astimezone(timezone.utc).replace(tzinfo=None))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from communications.models import AutomationWorkflow
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for values in source['seed'].values():
            AutomationWorkflow._base_manager.bulk_create([AutomationWorkflow(automation_workflow_id=values['id'],
                sport_association_id=values['association'], name=values['name'], description=values['description'],
                automation_tree=values['tree'], enabled=values['enabled'])])
            AutomationWorkflow._base_manager.filter(pk=values['id']).update(created_at=parse(values['created_at']),
                                                                            updated_at=parse(values['updated_at']))
            fixture['created'].append((AutomationWorkflow, values['id']))
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from communications.models import AutomationWorkflow
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
                AutomationWorkflow._base_manager.exclude(pk__in=list(keyed(before[WORKFLOW], 'automation_workflow_id'))).delete()
                if fixture is not None:
                    shared.delete_created(fixture)
                LogEntry.objects.exclude(pk__in=original_audit).delete()
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, bindings, window, source, remote):
    managed = (WORKFLOW, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    effect = case['effect']
    old_w, new_w = keyed(before[WORKFLOW], 'automation_workflow_id'), keyed(after[WORKFLOW], 'automation_workflow_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    if effect['kind'] == 'none':
        contract.require(old_w == new_w and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    if effect['kind'] == 'create':
        added = set(new_w) - set(old_w)
        contract.require(len(added) == 1 and set(old_w) <= set(new_w), 'state.one-workflow')
        identifier = added.pop()
        contract.require(identifier == effect.get('fixed_id', bindings.get(effect['label'])), 'state.created-id')
        row = new_w[identifier]
        for key, value in effect['values'].items():
            contract.require(row[key] == value, 'state.created-' + key)
        moments = [parse(row[k]).timestamp() for k in ('created_at', 'updated_at')]
        contract.require(all(window[0] - 0.001 <= m <= window[1] + 0.001 for m in moments), 'state.created-times')
        contract.require({k: v for k, v in new_w.items() if k != identifier} == old_w, 'state.create-only')
        stamps = {'$now-audit:created_at': audit_time(row['created_at']), '$now-audit:updated_at': audit_time(row['updated_at'])}
    else:
        identifier = effect['row']
        contract.require(identifier in old_w and identifier not in new_w, 'state.deleted')
        contract.require({k: v for k, v in old_w.items() if k != identifier} == new_w, 'state.delete-only')
        stamps = {}
    added_a = set(new_a) - set(old_a)
    contract.require(len(added_a) == 1 and set(old_a) <= set(new_a) and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = new_a[added_a.pop()]
    expected_changes = {}
    for field, (left, right) in effect['audit']['changes'].items():
        resolve = lambda value: identifier if value.startswith('$new:') else stamps.get('$now-audit:' + field, value) if value == '$now-audit' else value
        expected_changes[field] = [resolve(left), resolve(right)]
    users = {str(scalar(u['user_id'])): u for u in before['application.User']}
    actor = source['ids'][effect['audit']['actor']]
    content_types = [ct for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'communications' and ct['model'] == 'automationworkflow']
    contract.require(len(content_types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == content_types[0]['id'] and event['object_pk'] == identifier
                     and event['object_repr'] == f'AutomationWorkflow object ({identifier})' and event['action'] == effect['audit']['action'],
                     'audit.identity')
    contract.require(event['changes'] == expected_changes, 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = set(new_i) - set(old_i)
    contract.require(len(added_i) == 1 and all(new_i[k] == old_i[k] for k in old_i), 'index.one-row')
    index = new_i[added_i.pop()]
    contract.require(index['log_entry_id'] == event['id'] and index['sport_association_id'] == source['ids']['own_association']
                     and index['resolution_path'] == source['index_path'], 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, bindings, remote = [], {}, {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            started = time.time()
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], bindings=bindings)
            finished = time.time()
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, bindings, (started, finished), source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
