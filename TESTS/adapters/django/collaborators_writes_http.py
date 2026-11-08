"""Parent-only collaborators/<str:uid>/update HTTP runner; imports are inert.

Seeds collaborator users connected to the owner (one association-role user
with its own association), a foreign, a deleted and an unconnected user (audit
disabled), and verifies each case against the complete model state: only the
authored user's collaborator_role and collaborator_permissions may change, with
at most one exact User update entry and its index row.
"""
import ipaddress

from TESTS.contracts import test_collaborators_writes_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/collaborators_writes_http.json', 'TESTS/contracts/test_collaborators_writes_http.py',
           'TESTS/adapters/django/collaborators_writes_http.py', 'BE/application/views/collaborator_views.py',
           'BE/application/models/user_models.py', 'BE/application/permissions.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py',
           'BE/application/impersonation.py') + harness.SOURCES
USER = 'application.User'
MANAGED = (USER, harness.AUDIT, harness.INDEX)


def seed_rows(source, created):
    from application.models import SportAssociation, User
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for label, row in source['users'].items():
        insert(User, User(user_id=row['id'], username=f'collaborators-writes-{label}', email=f'collaborators-writes-{label}@example.test',
                          password='!synthetic', role=row['role'], first_name=row['first'], last_name='Fixture',
                          connected_user_id=ids[row['connected']] if row['connected'] else None,
                          collaborator_role=row['collaborator_role'], collaborator_permissions=row['permissions'], deleted=row['deleted']))
    insert(SportAssociation, SportAssociation(sport_association_id=ids['owning_association'], user_id=source['users']['owning']['id'],
                                              denomination='Collaborators writes owning', tax_code='79600000001'))


fixture_context = harness.fixture_context_for(contract, seed_rows)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect, ids = case['effect'], source['ids']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    target = source['users'][effect['user']]['id']
    old_u, new_u = keyed(before[USER], 'user_id'), keyed(after[USER], 'user_id')
    fields = ('collaborator_role', 'collaborator_permissions')
    contract.require(set(old_u) == set(new_u) and all(old_u[k] == new_u[k] for k in old_u if k != target), 'state.other-users')
    contract.require({k: v for k, v in new_u[target].items() if k not in fields} == {k: v for k, v in old_u[target].items() if k not in fields},
                     'state.target-fields')
    contract.require(new_u[target]['collaborator_role'] == effect['role'], 'state.role')
    contract.require(new_u[target]['collaborator_permissions'] == effect['permissions'], 'state.permissions')
    entries, indexes = harness.audit_delta(contract, before, after)
    audit = effect['audit']
    if audit is None:
        contract.require(not entries and not indexes, 'audit.none')
        return
    contract.require(len(entries) == 1, 'audit.one-entry')
    event = entries[0]
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'user']
    row = old_u[target]
    contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['object_pk'] == target and event['action'] == 1
                     and event['object_repr'] == f"name: {row['first_name']} {row['last_name']}", 'audit.identity')
    contract.require(event['changes'] == audit['changes'], 'audit.changes')
    actor = ids[audit['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == old_u[actor]['email'], 'audit.actor')
    additional = None
    if audit['impersonation']:
        additional = {'impersonation_session': context['fixture']['sessions'][audit['impersonation']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    if audit['index'] is None:
        contract.require(index is None and not indexes, 'index.none')
    else:
        contract.require(len(indexes) == 1 and index is not None and index['sport_association_id'] == ids[audit['index']['association']]
                         and index['resolution_path'] == audit['index']['path'], 'index.fields')


run_workload = harness.run_workload_for(contract, assert_effect)
