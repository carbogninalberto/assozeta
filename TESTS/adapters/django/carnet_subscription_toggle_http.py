"""Parent-only carnet-subscription enable/disable HTTP runner; imports are inert.

Seeds the carnet-list rows (audit disabled) and verifies each case against the
complete model state: only the target carnet subscription's disabled flag may
change, with one exact auditlog update entry (none without a change) indexed
through its subscription.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_carnet_subscription_toggle_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_subscription_toggle_http.json', 'TESTS/contracts/test_carnet_subscription_toggle_http.py',
           'TESTS/adapters/django/carnet_subscription_toggle_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/carnet_views.py', 'BE/application/serializers/carnet_serializers.py', 'BE/application/models/carnet_models.py', 'BE/application/signals.py',
           'BE/application/audit_resolvers.py')
CARNET, HOLDING, LINK = 'application.Carnet', 'application.CarnetSubscription', 'application.CarnetSubscription_course_subscription'
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        carnets.seed_rows(contract.load_source(), fixture['created'])
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
            contract.require(before == scopes.snapshot_state(apps), 'state.changed')
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def assert_effect(case, before, after, source, remote, fixture):
    managed = (HOLDING, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_h, new_h = keyed(before[HOLDING], 'carnet_subscription_id'), keyed(after[HOLDING], 'carnet_subscription_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    if effect is None:
        contract.require(old_h == new_h and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    row = source['carnet_subscriptions'][effect['holding']]
    target = row['id']
    contract.require(set(new_h) == set(old_h) and all(new_h[k] == old_h[k] for k in old_h if k != target)
                     and new_h[target] == dict(old_h[target], disabled=effect['disabled']), 'state.holding')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    if effect['changes'] is None:
        contract.require(not added and old_i == new_i, 'audit.none')
        return
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = added[0]
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    contract.require(event['content_type_id'] == types['carnetsubscription'] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f'CarnetSubscription object ({target})', 'audit.identity')
    contract.require(event['changes'] == effect['changes'], 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = source['ids'][effect['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    association = source['subscriptions'][row['subscription']]['association']
    contract.require(len(added_i) == 1 and str(added_i[0]['log_entry_id']) == str(event['id'])
                     and added_i[0]['sport_association_id'] == association
                     and added_i[0]['resolution_path'] == 'subscription.sport_association', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'state.changed')
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
