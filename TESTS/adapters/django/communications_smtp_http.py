"""Parent-only communications/settings/smtp info, update and verify HTTP runner; imports are inert.

Seeds CommunicationConfiguration rows for the foreign association (two, in
reverse key order) and the soft-deleted association (audit disabled); the own
association starts without one. Each case is checked against the complete
model state: only CommunicationConfiguration rows and auditlog entries with
their index rows may change. info's create must add exactly one row for the own
association (its key is bound for later cases); updates must leave every other
row and column unchanged; each new auditlog entry matches the authored one
(object, action, changes without the password, actor, impersonation data,
correlation id, direct index row). Rows the run creates are removed before the
fixture cleanup compares the model state.
"""
import ipaddress

from TESTS.contracts import test_communications_smtp_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/communications_smtp_http.json', 'TESTS/contracts/test_communications_smtp_http.py',
           'TESTS/adapters/django/communications_smtp_http.py', 'BE/communications/views.py', 'BE/communications/serializers.py',
           'BE/communications/models.py', 'BE/communications/urls.py', 'BE/application/models/user_models.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation.py',
           'BE/application/impersonation_scope.py') + harness.SOURCES
CONFIGURATION = 'communications.CommunicationConfiguration'
MANAGED = (CONFIGURATION, harness.AUDIT, harness.INDEX)
COLUMNS = ('email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_password', 'email_sender_name', 'email_encryption',
           'daily_email_limit', 'daily_email_balance')


def configuration_model():
    from communications.models import CommunicationConfiguration
    return CommunicationConfiguration


def seed_rows(source, created):
    model = configuration_model()
    for row in source['configurations'].values():
        model._base_manager.bulk_create([model(communication_configuration_id=row['id'], sport_association_id=row['association'],
                                               **{column: row[column] for column in COLUMNS})])
        created.append((model, row['id']))


def check_event(case, event, indexes, before, source, key, spec, context):
    effect = case['effect']
    ids = source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType']
             if ct['app_label'] == 'communications' and ct['model'] == 'communicationconfiguration']
    contract.require(len(types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == types[0] and event['action'] == spec['action'] and event['object_pk'] == key
                     and event['object_repr'] == f'CommunicationConfiguration object ({key})', 'audit.identity')
    changes = {field: [key if value == '$own' else value for value in pair] for field, pair in spec['changes'].items()}
    contract.require(event['changes'] == changes, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': context['fixture']['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                     'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    contract.require(index is not None and index['sport_association_id'] == ids['own_association']
                     and index['resolution_path'] == 'direct', 'index.fields')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    old, new = keyed(before[CONFIGURATION], 'communication_configuration_id'), keyed(after[CONFIGURATION], 'communication_configuration_id')
    if effect['kind'] == 'create':
        fresh = set(new) - set(old)
        contract.require(len(fresh) == 1 and set(old) <= set(new), 'state.created')
        key = fresh.pop()
        context['own'] = key
        contract.require(new[key]['sport_association_id'] == source['ids']['own_association'], 'state.created-association')
    else:
        key = context.get('own')
        contract.require(key is not None and set(new) == set(old), 'state.updated')
    row = new[key]
    for column, value in effect['row'].items():
        contract.require(row[column] == value and type(row[column]) is type(value), 'state.row-' + column)
    contract.require(all(new[k] == old[k] for k in old if k != key), 'state.others')
    if effect['kind'] == 'update':
        contract.require({c: v for c, v in row.items() if c not in COLUMNS} == {c: v for c, v in old[key].items() if c not in COLUMNS},
                         'state.updated-keys')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']) and len(indexes) == len(added), 'audit.count')
    for event, spec in zip(sorted(added, key=lambda event: event['id']), effect['audit']):
        check_event(case, event, indexes, before, source, key, spec, context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(configuration_model,))
run_workload = harness.run_workload_for(contract, assert_effect)
