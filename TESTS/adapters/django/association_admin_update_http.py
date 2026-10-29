"""Parent-only association admin-update HTTP runner; imports are inert.

Reuses the association-list seed and verifies each case against the complete
model state: only the first billing subscription of the own owner, the own
association's notes, the authored audit entries and their index rows may change.
"""
from contextlib import contextmanager
from datetime import datetime
import ipaddress
from pathlib import Path

from TESTS.contracts import test_association_admin_update_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.association_list_http import seed_fixture
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/association_admin_update_http.json', 'TESTS/contracts/test_association_admin_update_http.py',
           'TESTS/adapters/django/association_admin_update_http.py', 'TESTS/adapters/django/association_list_http.py',
           'TESTS/fixtures/association_list_http.json', 'BE/application/views/profile_views.py',
           'BE/application/models/billing_models.py', 'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py')
BILLING, ASSOCIATION, AUDIT, INDEX = 'application.BillingSubscription', 'application.SportAssociation', 'auditlog.LogEntry', 'application.AuditLogIndex'


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
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def moment(text):
    return None if text is None else datetime.fromisoformat(text.replace('Z', '+00:00'))


def assert_effect(case, before, after, source, remote):
    managed = (BILLING, ASSOCIATION, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    ids, effect = source['ids'], case['effect']
    billing_id, own = source['billing_id'], ids['own_association']
    old_b, new_b = keyed(before[BILLING], 'billing_subscription_id'), keyed(after[BILLING], 'billing_subscription_id')
    contract.require({k: v for k, v in new_b.items() if k != billing_id} == {k: v for k, v in old_b.items() if k != billing_id}, 'state.other-billing')
    row, expected = new_b[billing_id], effect['billing']
    contract.require(row['auto_renewal'] == expected['auto_renewal'] and row['renewal_type'] == expected['renewal_type']
                     and moment(row['ends_on']) == moment(expected['ends_on']) and row['billing_plan_id'] == expected['billing_plan'],
                     'state.billing-values')
    old_s, new_s = keyed(before[ASSOCIATION], 'sport_association_id'), keyed(after[ASSOCIATION], 'sport_association_id')
    contract.require({k: v for k, v in new_s.items() if k != own} == {k: v for k, v in old_s.items() if k != own}
                     and {k: v for k, v in new_s[own].items() if k != 'notes'} == {k: v for k, v in old_s[own].items() if k != 'notes'}
                     and new_s[own]['notes'] == effect['notes'], 'state.notes')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == len(effect['audits']) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    indexes = {new_i[k]['log_entry_id']: new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(len(indexes) == len(added) and all(new_i[k] == old_i[k] for k in old_i), 'index.rows')
    users = keyed(before['application.User'], 'user_id')
    for audit in effect['audits']:
        types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == audit['model']]
        matches = [e for e in added if e['content_type_id'] in types and e['object_pk'] == audit['id']]
        contract.require(len(types) == 1 and len(matches) == 1, 'audit.identity')
        event = matches[0]
        contract.require(event['object_repr'] == audit['repr'] and event['action'] == 1, 'audit.repr')
        contract.require(event['changes'] == audit['changes'], 'audit.changes')
        contract.require(event['actor_id'] == ids['admin'] and event['actor_email'] == users[ids['admin']]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(event['id'])
        contract.require(index is not None and index['sport_association_id'] == ids[audit['index']['association']]
                         and index['resolution_path'] == audit['index']['path'], 'index.fields')


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
