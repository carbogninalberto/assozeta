"""Parent-only workflow list/details HTTP runner; imports are inert.

Seeds the shared identity fixture plus workflows with fixed timestamps; every
case must leave the complete state and historical cache unchanged with exact
upstream attribution.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from TESTS.contracts import test_workflows_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations


SOURCES = ('TESTS/fixtures/workflows_http.json', 'TESTS/contracts/test_workflows_http.py',
           'TESTS/adapters/django/workflows_http.py', 'BE/communications/views.py', 'BE/communications/serializers.py',
           'BE/communications/models.py', 'BE_GO/internal/handler/automation_workflows.go',
           'BE_GO/internal/service/automation_workflows.go', 'BE_GO/internal/repository/automation_workflows.go',
           'BE_GO/internal/dto/automation_workflows.go')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from communications.models import AutomationWorkflow
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for values in source['seed'].values():
            contract.require(not AutomationWorkflow._base_manager.filter(pk=values['id']).exists(), 'fixture.row-collision')
            AutomationWorkflow._base_manager.bulk_create([AutomationWorkflow(automation_workflow_id=values['id'],
                sport_association_id=values['association'], name=values['name'], description=values['description'],
                automation_tree=values['tree'], enabled=values['enabled'])])
            parse = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00'))
            AutomationWorkflow._base_manager.filter(pk=values['id']).update(created_at=parse(values['created_at']),
                                                                            updated_at=parse(values['updated_at']))
            fixture['created'].append((AutomationWorkflow, values['id']))
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            from auditlog.context import disable_auditlog
            with disable_auditlog():
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


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
