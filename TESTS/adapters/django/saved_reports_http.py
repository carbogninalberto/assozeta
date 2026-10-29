"""Parent-only saved-reports HTTP runner; imports are inert.

Seeds the shared identity fixture plus saved reports (fixed timestamps) and
verifies each case against the complete model state: only the authored
SavedReport row may be created, changed or deleted, with server-written
timestamps inside the request window and echoed exactly where listed.
"""
from contextlib import contextmanager
from datetime import datetime
import time
from pathlib import Path

from TESTS.contracts import test_saved_reports_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/saved_reports_http.json', 'TESTS/contracts/test_saved_reports_http.py',
           'TESTS/adapters/django/saved_reports_http.py', 'BE/application/views/report_views.py', 'BE/application/models/report_models.py',
           'BE/core/middleware.py')
REPORT = 'application.SavedReport'


def moment(value):
    if isinstance(value, dict):
        value = value.get('value')
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models.report_models import SavedReport
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for row in source['seed'].values():
            SavedReport._base_manager.bulk_create([SavedReport(saved_report_id=row['id'], sport_association_id=row['association'],
                created_by_id=ids[row['creator']], name=row['name'], description=row['description'], tool_name=row['tool'],
                params=row['params'], ui_config=row['ui'], created_at=moment(row['created']))])
            SavedReport._base_manager.filter(pk=row['id']).update(updated_at=moment(row['updated']))
            fixture['created'].append((SavedReport, row['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from application.models.report_models import SavedReport
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
            SavedReport._base_manager.exclude(pk__in=list(keyed(before[REPORT], 'saved_report_id'))).delete()
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


def assert_effect(case, before, after, bindings, window, source, response):
    contract.require({k: v for k, v in before.items() if k != REPORT} == {k: v for k, v in after.items() if k != REPORT}, 'state.other-models')
    old_r, new_r = keyed(before[REPORT], 'saved_report_id'), keyed(after[REPORT], 'saved_report_id')
    effect, ids, seed = case['effect'], source['ids'], source['seed']
    inside = lambda value: window[0] - 0.001 <= moment(value).timestamp() <= window[1] + 0.001
    if effect is None:
        contract.require(old_r == new_r, 'state.no-effect')
    elif effect['kind'] == 'create':
        added = set(new_r) - set(old_r)
        contract.require(len(added) == 1 and set(old_r) <= set(new_r) and all(new_r[k] == old_r[k] for k in old_r), 'state.one-report')
        identifier = added.pop()
        contract.require(identifier == bindings[effect['label']], 'state.created-id')
        row = new_r[identifier]
        contract.require(row['sport_association_id'] == ids['own_association'] and row['created_by_id'] == ids['owner']
                         and all(row[k] == v for k, v in effect['values'].items()), 'state.created-values')
        contract.require(inside(row['created_at']) and inside(row['updated_at']), 'state.created-times')
    elif effect['kind'] == 'update':
        target = seed[effect['label']]['id']
        row = new_r[target]
        contract.require(set(new_r) == set(old_r) and all(new_r[k] == old_r[k] for k in old_r if k != target), 'state.update-only')
        contract.require(all(row[k] == v for k, v in effect['values'].items()) and inside(row['updated_at'])
                         and row['created_at'] == old_r[target]['created_at'], 'state.updated-values')
    else:
        target = seed[effect['label']]['id']
        contract.require(target in old_r and set(new_r) == set(old_r) - {target} and all(new_r[k] == old_r[k] for k in new_r), 'state.deleted')
    if case['name'].endswith('list-after-writes'):
        for item in response['data']:
            row = new_r[item['saved_report_id']]
            contract.require(moment(item['created_at']) == moment(row['created_at']) and moment(item['updated_at']) == moment(row['updated_at']),
                             'response.listed-times')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    from TESTS.http_client import Client
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, bindings = [], {}
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
            response = None
            if case['name'].endswith('list-after-writes'):
                _, response = Client(base_url, token).request('GET', case['path'], None, {'Accept': 'application/json'})
            assert_effect(case, before, after, bindings, (started, finished), source, response)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
