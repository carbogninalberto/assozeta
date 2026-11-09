"""Parent-only DefaultRouter documents HTTP runner; imports are inert.

Reuses the documents-list seed (folders, documents and archive rows) and
checks every case against the complete model state: destroy removes only
the archive row, bulk_delete the archive rows and their Documents, each with
one exact auditlog delete entry (the documents bulk-delete effect check); the
update that saves before its 500 changes the archive row's stored values with
one update entry indexed directly; everything else changes nothing.
"""
import ipaddress

from TESTS.contracts import test_router_documents_http as contract
from TESTS.adapters.django import documents_bulk_delete_http as bulk
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/router_documents_http.json', 'TESTS/contracts/test_router_documents_http.py', 'TESTS/contracts/router_stock.py',
           'TESTS/contracts/http_family.py', 'TESTS/contracts/test_documents_delete_http.py', 'TESTS/adapters/django/router_documents_http.py',
           'TESTS/adapters/django/documents_bulk_delete_http.py', 'TESTS/adapters/django/documents_list_http.py',
           'TESTS/fixtures/documents_list_http.json', 'BE/application/urls.py', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py', 'BE/docmanager/models.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py')
fixture_context = bulk.fixture_context


def assert_update(case, before, after, source, remote):
    managed = (bulk.ARCHIVE, bulk.AUDIT, bulk.INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed},
                     'state.other-models')
    effect = case['effect']
    key = 'sport_association_documents_archive_id'
    old_r, new_r = keyed(before[bulk.ARCHIVE], key), keyed(after[bulk.ARCHIVE], key)
    target = effect['archive']
    stored = {name: str(new_r[target][name]) for name in effect['stored']}
    contract.require(set(old_r) == set(new_r) and stored == effect['stored']
                     and all(new_r[k] == old_r[k] for k in old_r if k != target), 'state.archive')
    old_a, new_a = keyed(before[bulk.AUDIT], 'id'), keyed(after[bulk.AUDIT], 'id')
    old_i, new_i = keyed(before[bulk.INDEX], 'audit_log_index_id'), keyed(after[bulk.INDEX], 'audit_log_index_id')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event, ids = added[0], source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'sportassociationdocumentsarchive']
    contract.require(event['content_type_id'] == types[0] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f'SportAssociationDocumentsArchive object ({target})', 'audit.identity')
    contract.require(event['changes'] == effect['changes'], 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None, 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id'] and added_i[0]['sport_association_id'] == ids['own_association']
                     and added_i[0]['resolution_path'] == 'direct', 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], folders=fixture['folders'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], contract.bind_path(case['path'], fixture['folders']), status,
                                      observer.delta(cursor), case['name'])
            if case['effect'] and case['effect']['kind'] == 'update':
                assert_update(case, before, after, source, remote)
            else:
                bulk.assert_effect(case, before, after, fixture, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
