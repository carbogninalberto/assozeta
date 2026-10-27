"""Parent-only documents/<pk>/delete HTTP runner; imports are inert.

Reuses the documents-list seed and verifies each case against the complete
model state: only the authored archive row may disappear (its Document
stays), with one exact auditlog delete entry indexed through changes_json.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_documents_delete_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django import documents_list_http as base
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/documents_move_http.json', 'TESTS/contracts/test_documents_delete_http.py',
           'TESTS/adapters/django/documents_move_http.py', 'TESTS/adapters/django/documents_list_http.py',
           'TESTS/fixtures/documents_list_http.json', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py')
ARCHIVE, AUDIT, INDEX = 'application.SportAssociationDocumentsArchive', 'auditlog.LogEntry', 'application.AuditLogIndex'


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    original = [row['id'] for row in scopes.snapshot_state(apps)[AUDIT]]
    with base.fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        try:
            yield fixture
        finally:
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=original).delete()


def assert_effect(case, before, after, fixture, source, remote):
    managed = (ARCHIVE, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    key = 'sport_association_documents_archive_id'
    old_r, new_r = keyed(before[ARCHIVE], key), keyed(after[ARCHIVE], key)
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    folders = fixture['folders']
    if effect is None:
        contract.require(old_r == new_r and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    target = effect['archive']
    contract.require(target in old_r and target not in new_r and {k: v for k, v in old_r.items() if k != target} == new_r, 'state.deleted')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == 1 and all(new_a[k] == old_a[k] for k in old_a), 'audit.one-entry')
    event = added[0]
    ids = source['ids']
    expected = {name: [str(folders[v[8:]]) if v.startswith('$folder:') else v for v in pair] for name, pair in effect['changes'].items()}
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'sportassociationdocumentsarchive']
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == target and event['action'] == 2
                     and event['object_repr'] == f'SportAssociationDocumentsArchive object ({target})', 'audit.identity')
    contract.require(event['changes'] == expected, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    added_i = [new_i[k] for k in set(new_i) - set(old_i)]
    contract.require(len(added_i) == 1 and added_i[0]['log_entry_id'] == event['id'] and added_i[0]['sport_association_id'] == ids['own_association']
                     and added_i[0]['resolution_path'] == 'changes_json.sport_association', 'index.fields')


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
            assert_effect(case, before, after, fixture, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
