"""Parent-only documents/bulk-delete HTTP runner; imports are inert.

Reuses the documents-list seed and verifies each case against the complete
model state: only the authored archive rows and their Documents may
disappear, each with one exact auditlog delete entry and index row.
"""
from contextlib import contextmanager
import ipaddress

from TESTS.contracts import test_documents_bulk_delete_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import documents_list_http as base
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/documents_bulk_delete_http.json', 'TESTS/contracts/test_documents_bulk_delete_http.py',
           'TESTS/adapters/django/documents_bulk_delete_http.py', 'TESTS/adapters/django/documents_list_http.py',
           'TESTS/fixtures/documents_list_http.json', 'BE/application/views/archive_views.py', 'BE/application/models/user_models.py',
           'BE/docmanager/models.py', 'BE/application/audit_resolvers.py')
ARCHIVE, DOCUMENT, AUDIT, INDEX = ('application.SportAssociationDocumentsArchive', 'docmanager.Document', 'auditlog.LogEntry',
                                   'application.AuditLogIndex')


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
    managed = (ARCHIVE, DOCUMENT, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    old_r, new_r = keyed(before[ARCHIVE], 'sport_association_documents_archive_id'), keyed(after[ARCHIVE], 'sport_association_documents_archive_id')
    old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    effect = case['effect']
    if effect is None:
        contract.require(old_r == new_r and old_d == new_d and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    contract.require(set(new_r) == set(old_r) - set(effect['archives']) and all(new_r[k] == old_r[k] for k in new_r), 'state.archives')
    contract.require(set(new_d) == set(old_d) - set(effect['documents']) and all(new_d[k] == old_d[k] for k in new_d), 'state.documents')
    added = [new_a[k] for k in set(new_a) - set(old_a)]
    contract.require(len(added) == len(effect['archives']) + len(effect['documents']) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    indexes = {new_i[k]['log_entry_id']: new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(all(new_i[k] == old_i[k] for k in old_i), 'index.existing')
    ids, folders = source['ids'], fixture['folders']
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType']
             if (ct['app_label'], ct['model']) in (('application', 'sportassociationdocumentsarchive'), ('docmanager', 'document'))}
    users = keyed(before['application.User'], 'user_id')
    expected = [('sportassociationdocumentsarchive', key, 'SportAssociationDocumentsArchive', changes, 'changes_json.sport_association')
                for key, changes in effect['archive_changes'].items()]
    expected += [('document', key, 'Document', changes, 'actor.sport_association') for key, changes in effect['document_changes'].items()]
    for model, key, name, changes, path in expected:
        matches = [e for e in added if e['content_type_id'] == types[model] and e['object_pk'] == key]
        contract.require(len(matches) == 1, 'audit.identity')
        event = matches[0]
        rendered = {field: [str(folders[v[8:]]) if v.startswith('$folder:') else v for v in pair] for field, pair in changes.items()}
        contract.require(event['action'] == 2 and event['object_repr'] == f'{name} object ({key})', 'audit.repr')
        contract.require(event['changes'] == rendered, 'audit.changes')
        contract.require(event['actor_id'] == ids[case['actor']] and event['actor_email'] == users[ids[case['actor']]]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None, 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(event['id'])
        contract.require(index is not None and index['sport_association_id'] == ids['own_association'] and index['resolution_path'] == path,
                         'index.fields')
    contract.require(len(indexes) == len(expected), 'index.rows')


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
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, fixture, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
