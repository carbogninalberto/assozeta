"""Parent-only profile-misc HTTP runner; imports are inert.

Seeds (audit disabled) seven associates, eight subscriptions (creation dates
inside or before the current default balance year, computed at seed time),
four courses and four course subscriptions for the profile/associates reads;
the owner's avatar for profile/image; the owner's UsersOnboarding row, a fresh
self-hosted InstanceConfiguration whose primary association is the foreign
one, and an impossible balance start date for the owner without association.

Read cases must leave the complete model state unchanged. Each onboarding
case states the complete expected values of the six onboarding association
fields of both associations, the nine onboarding user fields of both owners,
the owner's onboarding flags and whether the instance onboarding is complete,
plus the ordered auditlog entries with their index rows (the UsersOnboarding
index resolves through the actor's association, so a collaborator's entry has
no index row). Every other model and every other field is unchanged.
"""
from datetime import datetime, timedelta, timezone

from TESTS.contracts import test_profile_misc_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/profile_misc_http.json', 'TESTS/contracts/test_profile_misc_http.py',
           'TESTS/adapters/django/profile_misc_http.py', 'BE/application/views/profile_views.py',
           'BE/application/views/onboarding_views.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/user_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/mixin.py', 'BE/application/utils/api_utils.py',
           'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/instance/models.py') + harness.SOURCES
USER, ASSOCIATION, ONBOARDING = 'application.User', 'application.SportAssociation', 'application.UsersOnboarding'
INSTANCE = 'instance.InstanceConfiguration'
MANAGED = (USER, ASSOCIATION, ONBOARDING, INSTANCE, harness.AUDIT, harness.INDEX)
ASSOCIATION_FIELDS = ('denomination', 'tax_code', 'logo', 'sport', 'subscription_fee', 'membership_fee')
USER_FIELDS = ('balance_sheet_year', 'subscription_start_day', 'subscription_start_month', 'balance_sheet_start_day',
               'balance_sheet_start_month', 'lead_sport_association_role', 'lead_sport_association_size',
               'lead_sport_market_channel', 'phone')
FLAG_FIELDS = ('show', 'create_membership', 'view_membership', 'approve_payment', 'download_invoice', 'view_collaborators',
               'view_settings')
ASSOCIATIONS = {'own': 'own_association', 'foreign': 'foreign_association'}


def balance_start(now):
    """The default (1 January) balance year start containing now, UTC."""
    return datetime(now.year, 1, 1, tzinfo=timezone.utc)


def seed_rows(source, created):
    from application.models import Associate, Course, CourseSubscription, Subscription, User
    from application.models.user_models import UsersOnboarding
    from instance.models import InstanceConfiguration
    ids = source['ids']
    start = balance_start(datetime.now(timezone.utc))
    moments = {'inside': start + timedelta(hours=1), 'before': start - timedelta(days=1)}
    association = lambda label: ids[ASSOCIATIONS[label]]

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], user_id=ids[row['user']], sport_association_id=association(row['association']),
                                    first_name=row['first'], last_name=row['last'], is_minor=row['minor'], tax_code=row['tax'],
                                    deleted=row.get('deleted', False), disabled=row.get('disabled', False),
                                    group_id=ids[row['group']] if row.get('group') else None))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], associate_id=source['associates'][row['associate']]['id'],
                                          user_id=ids[row['user']], sport_association_id=association(row['association']),
                                          creation_date=moments[row['created']], status_flag=Subscription.ACCEPTED,
                                          group_id=ids[row['group']] if row.get('group') else None))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=association(row['association']), title=row['title'],
                              deleted=row.get('deleted', False), group_id=ids[row['group']] if row.get('group') else None))
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      subscription_id=source['subscriptions'][row['subscription']]['id'],
                                                      deleted=row.get('deleted', False)))
    contract.require(not UsersOnboarding._base_manager.filter(user_id__in=[ids['owner'], ids['foreign_owner']]).exists(),
                     'fixture.onboarding')
    insert(UsersOnboarding, UsersOnboarding(users_onboarding_id=source['onboarding_id'], user_id=ids['owner']))
    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec = dict(source['instance'])
    spec['primary_association_id'] = ids[spec.pop('primary_association')]
    insert(InstanceConfiguration, InstanceConfiguration(**spec))
    User._base_manager.filter(pk=ids['owner']).update(avatar_image=source['avatar'])
    User._base_manager.filter(pk=ids['owner_without_association']).update(**source['no_association_balance'])


def scalar(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    return value


def text(value):
    value = scalar(value)
    return None if value is None else str(value)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[label] == after[label] for label in MANAGED), 'state.unchanged')
        return
    ids = source['ids']
    old_u, new_u = keyed(before[USER], 'user_id'), keyed(after[USER], 'user_id')
    old_s, new_s = keyed(before[ASSOCIATION], 'sport_association_id'), keyed(after[ASSOCIATION], 'sport_association_id')
    old_o, new_o = keyed(before[ONBOARDING], 'users_onboarding_id'), keyed(after[ONBOARDING], 'users_onboarding_id')

    def unchanged_except(old, new, keys, fields, code):
        contract.require(set(old) == set(new), code + '-rows')
        for key in old:
            rest = lambda row: {k: v for k, v in row.items() if not (key in keys and k in fields)}
            contract.require(rest(old[key]) == rest(new[key]), code + '-other')

    association_keys = {ids[ASSOCIATIONS[label]]: label for label in ASSOCIATIONS}
    unchanged_except(old_s, new_s, association_keys, ASSOCIATION_FIELDS, 'state.association')
    for key, label in association_keys.items():
        contract.require({f: text(new_s[key][f]) for f in ASSOCIATION_FIELDS} == effect['associations'][label], 'state.association-' + label)
    user_keys = {ids[label]: label for label in effect['users']}
    unchanged_except(old_u, new_u, user_keys, USER_FIELDS, 'state.user')
    for key, label in user_keys.items():
        expected = {f: text(v) for f, v in effect['users'][label].items()}
        contract.require({f: text(new_u[key][f]) for f in USER_FIELDS} == expected, 'state.user-' + label)
    onboarding = source['onboarding_id']
    unchanged_except(old_o, new_o, {onboarding}, FLAG_FIELDS, 'state.onboarding')
    contract.require({f: new_o[onboarding][f] for f in FLAG_FIELDS} == effect['onboarding'], 'state.onboarding-flags')

    old_i, new_i = before[INSTANCE], after[INSTANCE]
    contract.require(len(old_i) == len(new_i) == 1, 'state.instance-rows')
    moving = ('onboarding_completed_at', 'updated_at')
    contract.require({k: v for k, v in old_i[0].items() if k not in moving} == {k: v for k, v in new_i[0].items() if k not in moving},
                     'state.instance-other')
    if effect['instance_completed'] and old_i[0]['onboarding_completed_at'] is None:
        moment = lambda value: None if scalar(value) is None else datetime.fromisoformat(scalar(value).replace('Z', '+00:00'))
        completed, updated = moment(new_i[0]['onboarding_completed_at']), moment(new_i[0]['updated_at'])
        contract.require(completed is not None and updated is not None and updated >= completed >= moment(old_i[0]['updated_at']),
                         'state.instance-completed')
    else:
        contract.require(old_i[0] == new_i[0], 'state.instance-unchanged')
        contract.require((old_i[0]['onboarding_completed_at'] is not None) == effect['instance_completed'], 'state.instance-flag')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: event['id'])
    contract.require(len(added) == len(effect['audits']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before[USER], 'user_id')
    indexed = 0
    for event, spec in zip(added, effect['audits']):
        model = spec['model']
        if model == 'sportassociation':
            key = ids[ASSOCIATIONS[spec['key']]]
            representation, association, path = f'SportAssociation object ({key})', key, 'self'
        elif model == 'user':
            key = ids[spec['key']]
            representation = f"name: {users[key]['first_name']} {users[key]['last_name']}"
            association = ids['own_association' if spec['key'] == 'owner' else 'foreign_association']
            path = 'user.sport_association'
        else:
            key = onboarding
            representation = f'UsersOnboarding object ({key})'
            association = ids['own_association'] if spec['actor'] == 'owner' else None
            path = 'actor.sport_association'
        actor = ids[spec['actor']]
        contract.require(event['content_type_id'] == types[model] and event['action'] == 1 and event['object_pk'] == key
                         and event['object_repr'] == representation, 'audit.identity')
        contract.require(event['changes'] == spec['changes'], 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if association is None:
            contract.require(index is None, 'index.absent')
            continue
        indexed += 1
        contract.require(index is not None and index['sport_association_id'] == association and index['resolution_path'] == path,
                         'index.fields')
    contract.require(len(indexes) == indexed, 'index.count')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)
