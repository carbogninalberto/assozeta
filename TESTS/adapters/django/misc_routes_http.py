"""Parent-only misc-routes HTTP runner; imports are inert.

Seeds courses and attendance registries (calendar export), associates,
subscriptions, subscription tokens and the foreign association's membership
card configurations (audit disabled), then checks every case against the
complete model state:
- calendar export: no change;
- send/post: exactly one new Message (INSIDE_APP, the posted text, no subject)
  with its audited create and direct index row, and one new MessageTransaction
  for it whose recipient is the association id;
- card GET: the first own-association card creates the association's
  membership card configuration (defaults, audited create, direct index row,
  no actor for anonymous requests); an expired token is deleted with an
  auditlog delete (index through the actor's association, none for anonymous
  requests);
- card POST: one new SubscriptionToken for the subscription, equal to the
  answered token, expiring three days after the request, with its audited
  create indexed through the subscription.
Rows the run creates are removed before the fixture cleanup compares the state.
"""
from datetime import date, datetime, timedelta, timezone
import ipaddress
import re
import time

from TESTS.contracts import test_misc_routes_http as contract
from TESTS.adapters.django import bakney_public_links as links
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/misc_routes_http.json', 'TESTS/contracts/test_misc_routes_http.py',
           'TESTS/adapters/django/misc_routes_http.py', 'BE/application/views/attendee_views.py',
           'BE/application/views/subscriptions_views.py', 'BE/communications/views.py', 'BE/communications/serializers.py',
           'BE/communications/models.py', 'BE/communications/urls.py', 'BE/application/models/attendee_models.py',
           'BE/application/models/courses_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/permissions.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation.py',
           'BE/application/impersonation_scope.py', 'BE/application/mixin.py', 'BE/application/utils/api_utils.py',
           'BE/instance/sso/public_links.py', 'BE/instance/sso/service.py', 'BE/instance/sso/models.py',
           'TESTS/adapters/django/bakney_public_links.py', 'TESTS/adapters/django/sso_pairing_http.py', 'BE/instance/sso/protocol.py', 'BE/application/urls.py') + harness.SOURCES
MESSAGE, DELIVERY = 'communications.Message', 'communications.MessageTransaction'
CONFIGURATION, TOKEN = 'application.SportAssociationMembershipCardConfiguration', 'application.SubscriptionToken'
MANAGED = (MESSAGE, DELIVERY, CONFIGURATION, TOKEN, harness.AUDIT, harness.INDEX)
# auditlog renders datetimes as naive UTC text.
NAIVE = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{6})?\Z')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def models():
    from application.models import AttendanceRegistry, Associate, Course, Subscription
    from application.models.subscriptions_models import SubscriptionToken
    from application.models.user_models import SportAssociationMembershipCardConfiguration
    from communications.models import Message, MessageTransaction
    return (AttendanceRegistry, Associate, Course, Subscription, SubscriptionToken, SportAssociationMembershipCardConfiguration,
            Message, MessageTransaction)


def seed_rows(source, created):
    from decimal import Decimal
    (AttendanceRegistry, Associate, Course, Subscription, SubscriptionToken, CardConfiguration, _, _) = models()
    ids = source['ids']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], course_type=1, title=row['title'],
                              fee=Decimal('50.00'), deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None))
    for row in source['registries'].values():
        insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=row['id'], course_id=source['courses'][row['course']]['id'],
                                                      events=row['events'], status=2))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], sex='M', is_minor=False))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None, status_flag=4,
            subscription_number=row['number'], subscription_type=row['type'],
            end_date=date.fromisoformat(row['end']) if row['end'] else None, start_date=date(2026, 1, 1),
            creation_date=moment('2026-01-10T09:00:00Z'), group_id=ids[row['group']] if row['group'] else None,
            deleted=row['deleted']))
    for row in source['tokens'].values():
        insert(SubscriptionToken, SubscriptionToken(
            subscription_token_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'], token=row['token'],
            creation_date=moment(row['created']), expiration_date=moment(row['expires'])))
    for row in source['configurations'].values():
        insert(CardConfiguration, CardConfiguration(
            sport_association_membership_card_configuration_id=row['id'], sport_association_id=row['association'],
            emit_only_on_approval=row['emit_only_on_approval'], customized_template=row['customized_template']))


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(
    lambda: models()[4], lambda: models()[5], lambda: models()[6], lambda: models()[7]))


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def text(value):
    return None if value is None else str(plain(value))


def instant(value):
    value = plain(value)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def naive(value):
    """auditlog's delete rendering of a stored datetime: naive UTC text."""
    return str(instant(value).astimezone(timezone.utc).replace(tzinfo=None))


def prepare(source):
    return {'started': time.time()}


def check_event(case, event, indexes, before, context, *, app, model, key, action, changes, actor, session, index):
    ids = context['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == app and ct['model'] == model.lower()]
    contract.require(len(types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == types[0] and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    if actor is None:
        contract.require(event['actor_id'] is None and event['actor_email'] is None, 'audit.actor')
    else:
        users = keyed(before['application.User'], 'user_id')
        contract.require(event['actor_id'] == ids[actor] and event['actor_email'] == users[ids[actor]]['email'], 'audit.actor')
    additional = None
    if session:
        additional = {'impersonation_session': context['fixture']['sessions'][session], 'actor_id': ids[actor],
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                     'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    row = indexes.get(str(event['id']))
    if index is None:
        contract.require(row is None, 'index.absent')
    else:
        contract.require(row is not None and row['sport_association_id'] == index[0] and row['resolution_path'] == index[1],
                         'index.fields')


def in_window(value, since, until, shift=timedelta(0)):
    moment_ = instant(value) - shift
    return since - 1 <= moment_.timestamp() <= until + 1


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    context['ids'] = source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == 1, 'audit.count')
    event = added[0]
    unchanged = [k for k in (MESSAGE, DELIVERY, CONFIGURATION, TOKEN)
                 if not (effect['kind'] == 'post' and k in (MESSAGE, DELIVERY))
                 and not (effect['kind'] == 'card_configuration' and k == CONFIGURATION)
                 and not (effect['kind'] in ('token_created', 'token_deleted') and k == TOKEN)]
    contract.require(all(before[k] == after[k] for k in unchanged), 'state.others')
    kind = effect['kind']
    if kind == 'post':
        old, new = keyed(before[MESSAGE], 'message_id'), keyed(after[MESSAGE], 'message_id')
        fresh = set(new) - set(old)
        contract.require(len(fresh) == 1 and all(new[k] == old[k] for k in old), 'state.message-created')
        key = fresh.pop()
        row = flat(new[key])
        contract.require(str(row['sport_association_id']) == effect['association'] and row['type'] == 'INSIDE_APP'
                         and row['message'] == effect['message'] and row['subject'] is None, 'state.message-row')
        contract.require(in_window(row['created_at'], since, until), 'state.message-created-at')
        old_d, new_d = keyed(before[DELIVERY], 'message_transaction_id'), keyed(after[DELIVERY], 'message_transaction_id')
        fresh_d = set(new_d) - set(old_d)
        contract.require(len(fresh_d) == 1 and all(new_d[k] == old_d[k] for k in old_d), 'state.delivery-created')
        delivery = flat(new_d[fresh_d.pop()])
        contract.require(str(delivery['message_id']) == key and delivery['recipient'] == effect['association']
                         and all(delivery[c] is None for c in ('delivered_on', 'opened_on', 'clicked_on', 'bounced_on', 'bounced_reason',
                                                               'bounced_type', 'bounced_status')), 'state.delivery-row')
        contract.require(in_window(delivery['sent_on'], since, until), 'state.delivery-sent-on')
        created = event['changes'].get('created_at', [None, ''])[1]
        contract.require(NAIVE.match(created or '') is not None
                         and instant(created).replace(tzinfo=timezone.utc) == instant(row['created_at']), 'audit.created-at')
        changes = {'message_id': ['None', key], 'sport_association': ['None', effect['association']], 'type': ['None', 'INSIDE_APP'],
                   'message': ['None', effect['message']], 'created_at': ['None', created]}
        check_event(case, event, indexes, before, context, app='communications', model='Message', key=key, action=0, changes=changes,
                    actor=effect['actor'], session=effect['session'], index=[effect['association'], 'direct'])
    elif kind == 'card_configuration':
        old, new = (keyed(before[CONFIGURATION], 'sport_association_membership_card_configuration_id'),
                    keyed(after[CONFIGURATION], 'sport_association_membership_card_configuration_id'))
        fresh = set(new) - set(old)
        contract.require(len(fresh) == 1 and all(new[k] == old[k] for k in old), 'state.configuration-created')
        key = fresh.pop()
        row = flat(new[key])
        contract.require(str(row['sport_association_id']) == effect['association'] and row['emit_only_on_approval'] is False
                         and row['customized_template'] is None, 'state.configuration-row')
        changes = {'sport_association_membership_card_configuration_id': ['None', key], 'sport_association': ['None', effect['association']],
                   'emit_only_on_approval': ['None', 'False'], 'customized_template': ['None', 'null']}
        check_event(case, event, indexes, before, context, app='application', model='SportAssociationMembershipCardConfiguration',
                    key=key, action=0, changes=changes, actor=None, session=None, index=[effect['association'], 'direct'])
    elif kind == 'token_deleted':
        spec = source['tokens'][effect['token']]
        old, new = keyed(before[TOKEN], 'subscription_token_id'), keyed(after[TOKEN], 'subscription_token_id')
        contract.require(set(old) - set(new) == {spec['id']} and set(new) <= set(old) and all(new[k] == old[k] for k in new),
                         'state.token-deleted')
        row = flat(old[spec['id']])
        changes = {'subscription_token_id': [spec['id'], 'None'],
                   'subscription': [source['subscriptions'][spec['subscription']]['id'], 'None'],
                   'token': [spec['token'], 'None'], 'creation_date': [naive(row['creation_date']), 'None'],
                   'expiration_date': [naive(row['expiration_date']), 'None']}
        check_event(case, event, indexes, before, context, app='application', model='SubscriptionToken', key=spec['id'], action=2,
                    changes=changes, actor=effect['actor'], session=None, index=effect['index'])
    elif kind == 'token_created':
        subscription = source['subscriptions'][effect['subscription']]['id']
        old, new = keyed(before[TOKEN], 'subscription_token_id'), keyed(after[TOKEN], 'subscription_token_id')
        fresh = set(new) - set(old)
        contract.require(len(fresh) == 1 and all(new[k] == old[k] for k in old), 'state.token-created')
        key = fresh.pop()
        row = flat(new[key])
        contract.require(str(row['subscription_id']) == subscription and str(row['token']) == (contract.ANSWERED.get('body') or {}).get('token'),
                         'state.token-row')
        created, expires = instant(row['creation_date']), instant(row['expiration_date'])
        contract.require(in_window(created, since, until) and timedelta(0) <= created - (expires - timedelta(days=3)) < timedelta(seconds=1),
                         'state.token-dates')
        audit_created = event['changes'].get('creation_date', [None, ''])[1]
        audit_expires = event['changes'].get('expiration_date', [None, ''])[1]
        contract.require(audit_created == naive(created) and audit_expires == naive(expires), 'audit.token-dates')
        changes = {'subscription_token_id': ['None', key], 'subscription': ['None', subscription], 'token': ['None', str(row['token'])],
                   'creation_date': ['None', audit_created], 'expiration_date': ['None', audit_expires]}
        check_event(case, event, indexes, before, context, app='application', model='SubscriptionToken', key=key, action=0,
                    changes=changes, actor=effect['actor'], session=effect['session'], index=effect['index'])
    else:
        contract.require(False, 'effect.kind')


run_workload = harness.run_workload_for(links.Contract(contract, 'own_association'), assert_effect, prepare=prepare)
# The paired-server stub of the cases with a 'pairing' entry (public_links.source_token).
service_context = links.service_context
