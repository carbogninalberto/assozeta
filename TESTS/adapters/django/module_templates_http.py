"""Parent-only module-template HTTP runner; imports are inert.

Seeds the shared identity fixture plus templates (audit disabled), runs the
authored sequence, and verifies each case against the complete model state:
only the authored template rows, their exact audit entries and index rows
may change. Remote addresses are checked as valid and consistent.
"""
from contextlib import contextmanager
import ipaddress
from pathlib import Path

from TESTS.contracts import test_module_templates_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/module_templates_http.json', 'TESTS/contracts/test_module_templates_http.py',
           'TESTS/adapters/django/module_templates_http.py', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py')
TEMPLATE, AUDIT, INDEX = 'application.SportAssociationModuleTemplates', 'auditlog.LogEntry', 'application.AuditLogIndex'
FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import SportAssociationModuleTemplates
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        for values in source['seed'].values():
            SportAssociationModuleTemplates._base_manager.bulk_create([SportAssociationModuleTemplates(
                sport_association_module_templates_id=values['id'], sport_association_id=values['association'],
                **{field: values[field] for field in FIELDS})])
            fixture['created'].append((SportAssociationModuleTemplates, values['id']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import SportAssociationModuleTemplates
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
                SportAssociationModuleTemplates._base_manager.exclude(
                    pk__in=list(keyed(before[TEMPLATE], 'sport_association_module_templates_id'))).delete()
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


def assert_effect(case, before, after, bindings, source, remote):
    managed = (TEMPLATE, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    effect = case['effect']
    key = 'sport_association_module_templates_id'
    old_t, new_t = keyed(before[TEMPLATE], key), keyed(after[TEMPLATE], key)
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    if effect['kind'] == 'none':
        contract.require(old_t == new_t and old_a == new_a and old_i == new_i, 'state.no-effect')
        return
    if effect['kind'] == 'create':
        added = set(new_t) - set(old_t)
        contract.require(len(added) == 1 and set(old_t) <= set(new_t), 'state.one-template')
        identifier = added.pop()
        contract.require(identifier == (effect['fixed_id'] or bindings.get(effect['label'])), 'state.created-id')
        row = new_t[identifier]
        contract.require(row['sport_association_id'] == effect['row']['association']
                         and all(row[field] == effect['row'][field] for field in FIELDS), 'state.created-values')
        contract.require({k: v for k, v in new_t.items() if k != identifier} == old_t, 'state.create-only')
        audits = [{'id': identifier, 'action': 0, 'path': effect['index_path'],
                   'changes': {f: [l, identifier if r == '$new' else r] for f, (l, r) in effect['audit']['changes'].items()}}]
        actor = effect['audit']['actor']
    else:
        gone = {entry['id'] for entry in effect['audits']}
        contract.require(gone <= set(old_t) and not gone & set(new_t) and {k: v for k, v in old_t.items() if k not in gone} == new_t,
                         'state.deleted')
        audits = [{'id': entry['id'], 'action': 2, 'path': 'changes_json.sport_association', 'changes': entry['changes']}
                  for entry in effect['audits']]
        actor = effect['actor']
    added_a = set(new_a) - set(old_a)
    contract.require(len(added_a) == len(audits) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    users = {str(scalar(u['user_id'])): u for u in before['application.User']}
    actor_id = source['ids'][actor]
    content_types = [ct for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'sportassociationmoduletemplates']
    contract.require(len(content_types) == 1, 'audit.content-type')
    events = {new_a[k]['object_pk']: new_a[k] for k in added_a}
    added_i = {new_i[k]['log_entry_id']: new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(len(added_i) == len(audits) and all(new_i[k] == old_i[k] for k in old_i), 'index.rows')
    for expected in audits:
        event = events.get(expected['id'])
        contract.require(event is not None and event['content_type_id'] == content_types[0]['id']
                         and event['object_repr'] == f'SportAssociationModuleTemplates object ({expected["id"]})'
                         and event['action'] == expected['action'], 'audit.identity')
        contract.require(event['changes'] == expected['changes'], 'audit.changes')
        contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = added_i.get(event['id'])
        contract.require(index is not None and index['sport_association_id'] == source['ids']['own_association']
                         and index['resolution_path'] == expected['path'], 'index.fields')


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
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], bindings=bindings)
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, bindings, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
