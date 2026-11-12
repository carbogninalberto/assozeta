"""Parent-only profile-template HTTP runner; imports are inert.

Seeds (audit disabled) the owner's avatar, which every owner response
compresses. Each case states either no effect (every model unchanged) or the
complete expected values of the fields the view assigns on the owner's
association, plus the ordered auditlog entries (with the impersonation
session record for an administrator session) and their index rows. Every
other row and field must stay unchanged. JSON fields compare through
json.dumps(sort_keys=True), so 0 and 0.0 differ.
"""
import json

from TESTS.contracts import test_profile_template_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/profile_template_http.json', 'TESTS/contracts/test_profile_template_http.py',
           'TESTS/adapters/django/profile_template_http.py', 'BE/application/views/profile_views.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/utils.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/instance/registration_features.py') + harness.SOURCES
USER, ASSOCIATION = 'application.User', 'application.SportAssociation'
MANAGED = (ASSOCIATION, harness.AUDIT, harness.INDEX)


def seed_rows(source, created):
    from application.models import User
    User._base_manager.filter(pk=source['ids']['owner']).update(avatar_image=source['avatar'])


def scalar(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    return value


def stable(value):
    return json.dumps(value, sort_keys=True)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[label] == after[label] for label in MANAGED), 'state.unchanged')
        return
    ids = source['ids']
    own = ids['own_association']
    written = set(source['written_fields'])
    old, new = keyed(before[ASSOCIATION], 'sport_association_id'), keyed(after[ASSOCIATION], 'sport_association_id')
    contract.require(set(old) == set(new), 'state.association-rows')
    for key in old:
        rest = lambda row: {k: v for k, v in row.items() if not (key == own and k in written)}
        contract.require(rest(old[key]) == rest(new[key]), 'state.association-other')
    for field, expected in effect['association'].items():
        contract.require(stable(scalar(new[own][field])) == stable(expected), 'state.association-' + field)
    contract.require(set(effect['association']) == written, 'state.association-fields')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: event['id'])
    contract.require(len(added) == len(effect['audits']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before[USER], 'user_id')
    for event, spec in zip(added, effect['audits']):
        actor = ids[spec['actor']]
        contract.require(event['content_type_id'] == types['sportassociation'] and event['action'] == 1 and event['object_pk'] == own
                         and event['object_repr'] == f'SportAssociation object ({own})', 'audit.identity')
        contract.require(event['changes'] == spec['changes'], 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        additional = None
        if spec.get('impersonation'):
            additional = {'impersonation_session': context['fixture']['sessions'][spec['impersonation']], 'actor_id': actor,
                          'target_user_id': case['headers']['User-Id'], 'association_id': None}
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context')
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        contract.require(index is not None and index['sport_association_id'] == own and index['resolution_path'] == 'self',
                         'index.fields')
    contract.require(len(indexes) == len(added), 'index.count')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)
