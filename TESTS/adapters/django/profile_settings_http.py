"""Parent-only profile/settings and profile/integrations HTTP runner; imports are inert.

Seeds four payment categories (own, foreign, global and an archived own one),
grants the restricted collaborator other.settings.read and gives the foreign
association review settings (audit disabled). Each case is checked against
the complete model state: the membership card configuration created on the
first request of an association (create audit), the owner's 30 settings after
a POST (update audit of the changed fields, none when nothing changed), the
configuration after a POST (its update audit can exist without the row change
when the model rejects the value) and the association's review settings
after a PATCH (likewise). Created configurations are removed before the
fixture cleanup compares the model state.
"""

from TESTS.contracts import test_profile_settings_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/profile_settings_http.json', 'TESTS/contracts/test_profile_settings_http.py',
           'TESTS/adapters/django/profile_settings_http.py', 'BE/application/views/profile_views.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/application/utils/api_utils.py',
           'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
USER, ASSOCIATION = 'application.User', 'application.SportAssociation'
CARD = 'application.SportAssociationMembershipCardConfiguration'
MANAGED = (USER, ASSOCIATION, CARD, harness.AUDIT, harness.INDEX)
CARD_ID = 'sport_association_membership_card_configuration_id'
RELATIONS = ('default_payment_category', 'default_payment_category_courses')


def card_model():
    from application.models.user_models import SportAssociationMembershipCardConfiguration
    return SportAssociationMembershipCardConfiguration


def seed_rows(source, created):
    from application.models import SportAssociation, User
    from application.models.payment_models import PaymentCategory
    ids, categories = source['ids'], source['categories']
    rows = [PaymentCategory(pk=categories['own'], name='Quota sociale', sport_association_id=ids['own_association']),
            PaymentCategory(pk=categories['foreign'], name='Estera', sport_association_id=ids['foreign_association']),
            PaymentCategory(pk=categories['global'], name='Globale', sport_association_id=None),
            PaymentCategory(pk=categories['own_archived'], name='Archiviata', sport_association_id=ids['own_association'],
                            archived=True, deleted=True)]
    contract.require(not PaymentCategory._base_manager.filter(pk__in=[row.pk for row in rows]).exists(), 'fixture.collision')
    PaymentCategory._base_manager.bulk_create(rows)
    created.extend((PaymentCategory, row.pk) for row in rows)
    contract.require(not card_model()._base_manager.filter(
        sport_association_id__in=[ids['own_association'], ids['foreign_association']]).exists(), 'fixture.cards')
    restricted = User._base_manager.get(pk=ids['restricted_collaborator'])
    User._base_manager.filter(pk=restricted.pk).update(
        collaborator_permissions=[*(restricted.collaborator_permissions or []), 'other.settings.read'])
    SportAssociation._base_manager.filter(pk=ids['foreign_association']).update(**source['foreign_review'])


def audit_event(case, event, indexes, *, before, ids, model, representation, key, action, changes, actor, association, path, context):
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before[USER], 'user_id')
    actor_id = ids[actor]
    contract.require(event['content_type_id'] == types[model] and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == representation, 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes[str(event['id'])]
    contract.require(index['sport_association_id'] == association and index['resolution_path'] == path, 'index.fields')


def stored_settings(row, fields):
    values = {field: row[field] for field in fields if field not in RELATIONS}
    values.update({field: None if row[field + '_id'] is None else str(row[field + '_id']) for field in RELATIONS})
    return values


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect'] or {}
    ids = source['ids']
    own = ids['own_association']
    old_u, new_u = keyed(before[USER], 'user_id'), keyed(after[USER], 'user_id')
    old_s, new_s = keyed(before[ASSOCIATION], 'sport_association_id'), keyed(after[ASSOCIATION], 'sport_association_id')
    old_c, new_c = keyed(before[CARD], CARD_ID), keyed(after[CARD], CARD_ID)
    added, indexes = harness.audit_delta(contract, before, after)
    by_model = {}
    types = {ct['id']: ct['model'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    for event in added:
        by_model.setdefault(types.get(event['content_type_id']), []).append(event)
    contract.require(len(indexes) == len(added), 'index.count')
    common = dict(before=before, ids=ids, context=context)

    # Membership card configurations: at most one created, otherwise the own one updated.
    created = set(new_c) - set(old_c)
    contract.require(set(old_c) <= set(new_c), 'state.cards-kept')
    cards = by_model.pop('sportassociationmembershipcardconfiguration', [])
    if 'card_create' in effect:
        spec = effect['card_create']
        association = ids[spec['association']]
        contract.require(len(created) == 1, 'state.card-created')
        key = created.pop()
        row = new_c[key]
        contract.require(str(row['sport_association_id']) == association and row['emit_only_on_approval'] is False
                         and row['customized_template'] is None, 'state.card')
        contract.require(len(cards) == 1, 'audit.card-count')
        audit_event(case, cards[0], indexes, model='sportassociationmembershipcardconfiguration',
                    representation=f'SportAssociationMembershipCardConfiguration object ({key})', key=key, action=0,
                    changes={CARD_ID: ['None', key], 'sport_association': ['None', association],
                             'emit_only_on_approval': ['None', 'False'], 'customized_template': ['None', 'null']},
                    actor=spec['actor'], association=association, path='direct', **common)
        cards = []
    else:
        contract.require(not created, 'state.no-card-created')
    spec = effect.get('card')
    changed = {k for k in old_c if old_c[k] != new_c[k]}
    if spec is None:
        contract.require(not changed and not cards, 'state.card-unchanged')
    else:
        own_cards = sorted(k for k, row in old_c.items() if str(row['sport_association_id']) == own)
        key = own_cards[0]
        expected = spec['stored'] if spec['stored'] is not None else {
            'emit_only_on_approval': old_c[key]['emit_only_on_approval'], 'customized_template': old_c[key]['customized_template']}
        contract.require(changed <= {key} and new_c[key]['emit_only_on_approval'] == expected['emit_only_on_approval']
                         and new_c[key]['customized_template'] == expected['customized_template'], 'state.card-row')
        contract.require(len(cards) == (1 if spec['changes'] else 0), 'audit.card-count')
        for event in cards:
            audit_event(case, event, indexes, model='sportassociationmembershipcardconfiguration',
                        representation=f'SportAssociationMembershipCardConfiguration object ({key})', key=key, action=1,
                        changes=spec['changes'], actor=spec['actor'], association=own, path='direct', **common)

    # The owner's settings.
    users = by_model.pop('user', [])
    spec = effect.get('user')
    owner = ids['owner']
    if spec is None:
        contract.require(old_u == new_u and not users, 'state.user-unchanged')
    else:
        fields = source['fields']
        contract.require(set(old_u) == set(new_u) and all(old_u[k] == new_u[k] for k in old_u if k != owner), 'state.users-other')
        settled = set(fields) | {f + '_id' for f in RELATIONS}
        contract.require({k: v for k, v in old_u[owner].items() if k not in settled}
                         == {k: v for k, v in new_u[owner].items() if k not in settled}, 'state.user-other-fields')
        contract.require(stored_settings(new_u[owner], fields) == spec['stored'], 'state.user-settings')
        contract.require(len(users) == (1 if spec['changes'] else 0), 'audit.user-count')
        name = f"name: {old_u[owner]['first_name']} {old_u[owner]['last_name']}"
        for event in users:
            audit_event(case, event, indexes, model='user', representation=name, key=owner, action=1, changes=spec['changes'],
                        actor=spec['actor'], association=own, path='user.sport_association', **common)

    # The association's review settings.
    associations = by_model.pop('sportassociation', [])
    spec = effect.get('association')
    if spec is None:
        contract.require(old_s == new_s and not associations, 'state.association-unchanged')
    else:
        contract.require(set(old_s) == set(new_s) and all(old_s[k] == new_s[k] for k in old_s if k != own), 'state.associations-other')
        expected = spec['stored'] or {'review_url': old_s[own]['review_url'], 'review_url_enabled': old_s[own]['review_url_enabled']}
        rest = lambda row: {k: v for k, v in row.items() if k not in ('review_url', 'review_url_enabled')}
        contract.require(rest(old_s[own]) == rest(new_s[own]) and new_s[own]['review_url'] == expected['review_url']
                         and new_s[own]['review_url_enabled'] == expected['review_url_enabled'], 'state.association-review')
        contract.require(len(associations) == (1 if spec['changes'] else 0), 'audit.association-count')
        for event in associations:
            audit_event(case, event, indexes, model='sportassociation', representation=f'SportAssociation object ({own})', key=own,
                        action=1, changes=spec['changes'], actor=spec['actor'], association=own, path='self', **common)
    contract.require(not by_model, 'audit.other-models')


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(card_model,))
run_workload = harness.run_workload_for(contract, assert_effect)
