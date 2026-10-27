"""Parent-only dashboard-layout HTTP runner; imports are inert.

Seeds the shared identity fixture with every layout null (audit disabled),
runs the authored sequence and verifies each case against the complete model
state: only the authored user's dashboard_layout, one exact User audit entry
and at most one index row may change.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_dashboard_layout_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/dashboard_layout_http.json', 'TESTS/contracts/test_dashboard_layout_http.py',
           'TESTS/adapters/django/dashboard_layout_http.py', 'BE/application/views/statistic_views.py',
           'BE/application/impersonation.py', 'BE/application/models/user_models.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py')
USER, AUDIT, INDEX = 'application.User', 'auditlog.LogEntry', 'application.AuditLogIndex'


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import User
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        User._base_manager.update(dashboard_layout=None)
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    layouts = {key: row['dashboard_layout'] for key, row in keyed(before[USER], 'user_id').items()}
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
                for key, value in layouts.items():
                    User._base_manager.filter(pk=key).update(dashboard_layout=value)
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


def assert_effect(case, before, after, source, remote, sessions):
    managed = (USER, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    ids = source['ids']
    effect = case['effect']
    old_u, new_u = keyed(before[USER], 'user_id'), keyed(after[USER], 'user_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    target = ids[effect['user']] if effect else None
    for key in set(old_u) | set(new_u):
        if key == target:
            contract.require({k: v for k, v in new_u[key].items() if k != 'dashboard_layout'}
                             == {k: v for k, v in old_u[key].items() if k != 'dashboard_layout'}, 'state.user-layout-only')
            contract.require(new_u[key]['dashboard_layout'] == effect['value'], 'state.layout-value')
        else:
            contract.require(old_u.get(key) == new_u.get(key), 'state.other-users')
    audit = effect['audit'] if effect else None
    if audit is None:
        contract.require(old_a == new_a and old_i == new_i, 'audit.none')
        return
    added_a = set(new_a) - set(old_a)
    contract.require(len(added_a) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = new_a[added_a.pop()]
    user = old_u[target]
    actor = ids[audit['actor']]
    content_types = [ct for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'user']
    contract.require(len(content_types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == content_types[0]['id'] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f"name: {user['first_name']} {user['last_name']}", 'audit.identity')
    contract.require(event['changes'] == audit['changes'], 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == old_u[actor]['email'], 'audit.actor')
    additional = None
    if audit['impersonation']:
        additional = {'impersonation_session': sessions[audit['impersonation']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(all(new_i[k] == old_i[k] for k in old_i), 'index.existing')
    if audit['index'] is None:
        contract.require(not added_i, 'index.none')
    else:
        contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id']
                         and added_i[0]['sport_association_id'] == ids[audit['index']['association']]
                         and added_i[0]['resolution_path'] == audit['index']['path'], 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], bindings={})
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture['sessions'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
