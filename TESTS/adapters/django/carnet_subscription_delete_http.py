"""Parent-only carnet-subscription course-link delete HTTP runner; imports are inert.

Seeds the carnet-list rows (audit disabled) and verifies each case against the
complete model state: only the authored course links of the target carnet
subscription may disappear, without audit entries.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_carnet_subscription_delete_http as contract
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/carnet_subscription_delete_http.json', 'TESTS/contracts/test_carnet_subscription_delete_http.py',
           'TESTS/adapters/django/carnet_subscription_delete_http.py', 'TESTS/adapters/django/carnet_list_http.py',
           'BE/application/views/carnet_views.py', 'BE/application/models/carnet_models.py', 'BE/application/signals.py',
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
    effect = case['effect']
    others = {k: v for k, v in before.items() if k != LINK}
    contract.require(others == {k: v for k, v in after.items() if k != LINK}, 'state.other-models')
    if effect is None:
        contract.require(before[LINK] == after[LINK], 'state.no-effect')
        return
    plain = lambda value: value.get('value') if isinstance(value, dict) else value
    holding = source['carnet_subscriptions'][effect['holding']]['id']
    removed = {source['course_subscriptions'][label]['id'] for label in effect['removed']}
    contract.require(after[LINK] == [r for r in before[LINK] if not (plain(r['carnetsubscription_id']) == holding
                                                                    and plain(r['coursesubscription_id']) in removed)], 'state.links')
    contract.require(len(before[LINK]) - len(after[LINK]) == len(removed), 'state.removed-count')


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
