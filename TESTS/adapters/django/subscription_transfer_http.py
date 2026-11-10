"""Parent-only subscription/<uid>/transfer HTTP runner; imports are inert.

service_context points Django's channel layer (GoMirroredRedisChannelLayer)
at the run's private Redis database 0, where the Go server writes. seed_rows
inserts (audit disabled) associates, tutor relations, subscriptions and
existing transfers (expired ones in 2020, live ones in 2099).

Every case is checked against the complete model state:
- transfers: with `expire`, every existing transfer whose expiry is before the
  request has status 4, the rest is unchanged; an accepted transfer is one new
  row (status 2, requester, recipient, created and expiring within the request
  window, the response's token and expires_at);
- the subscription's, associate's and tutor's user become the recipient, a
  relation made primary, nothing else changes;
- a new account: username = the authored prefix + 8 upper-case letters or
  digits, the authored e-mail and names, role athlete, model defaults, a
  usable password of the default hasher (checked against the password the
  welcome e-mail carries, when one is sent);
- the auditlog entries exactly the authored ones, in order (creates are
  auditlog's diff of the stored row; the transfer's create renders the
  pre-acceptance status and expiry its update entry reports);
- the Celery welcome e-mail (send_mail_async kwargs, rendered here with
  render_to_string for the stored account and the e-mail's password);
- the recipient's notification: broadcast rows when first notified, the Redis
  DB 0 broadcast_<recipient> list, the user_broadcasts cache, the
  notification_push a Channels consumer receives (fixture users) and the
  mirrored envelope.
Failures leave all of it unchanged (except the transfer expiry, which is not
atomic with the rest).
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
import pickle
import re
import threading
import time
import uuid

from TESTS.contracts import test_subscription_transfer_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_transfer_http.json', 'TESTS/contracts/test_subscription_transfer_http.py',
           'TESTS/adapters/django/subscription_transfer_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/views/auth_views.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/notification_utils.py', 'BE/notifications/services.py',
           'BE/notifications/utils.py', 'BE/notifications/models.py', 'BE/notifications/realtime_layer.py',
           'BE/application/audit_resolvers.py', 'BE/application/signals.py', 'BE/application/mixin.py',
           'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/application/templatetags/instance_branding.py', 'BE/templates/email/_base.html',
           'BE/templates/email/account/email_welcome_password_message.html', 'BE/core/tasks.py', 'BE/core/celery.py',
           'BE/core/settings.py') + harness.SOURCES
TTL = 60 * 60 * 24 * 30
REALTIME = 'assozeta:realtime:v1'
SEND_MAIL = 'core.tasks.send_mail_async'
LABELS = {
    'associate': 'application.Associate', 'relation': 'application.AssociateTutorRelation', 'subscription': 'application.Subscription',
    'transfer': 'application.SubscriptionTransfer', 'user': 'application.User', 'broadcast': 'notifications.NotificationsBroadcast',
    'user_broadcast': 'notifications.UserNotificationsBroadcast',
}
MANAGED = (*LABELS.values(), harness.AUDIT, harness.INDEX)
KEYS = {'application.Associate': 'associate_id', 'application.AssociateTutorRelation': 'id', 'application.Subscription': 'subscription_id',
        'application.SubscriptionTransfer': 'subscription_transfer_id', 'application.User': 'user_id',
        'notifications.NotificationsBroadcast': 'broadcast_id', 'notifications.UserNotificationsBroadcast': 'user_broadcast_id'}
PROBED = ('owner', 'athlete')
PAST, FUTURE = datetime(2020, 1, 1, 12, tzinfo=timezone.utc), datetime(2099, 1, 1, 12, tzinfo=timezone.utc)
STATE = {}


def models():
    from application.models import Associate, Subscription, User
    from application.models.subscriptions_models import SubscriptionTransfer
    from application.models.user_models import AssociateTutorRelation
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast
    return {'application.Associate': Associate, 'application.AssociateTutorRelation': AssociateTutorRelation,
            'application.Subscription': Subscription, 'application.SubscriptionTransfer': SubscriptionTransfer, 'application.User': User,
            'notifications.NotificationsBroadcast': NotificationsBroadcast,
            'notifications.UserNotificationsBroadcast': UserNotificationsBroadcast}


@contextmanager
def service_context(report, log):
    """The channel layer on DB 0 and Django's default password hashers (the Go server writes PBKDF2-SHA256)."""
    from django.conf import global_settings
    from django.test.utils import override_settings
    layers = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer', 'CONFIG': {'hosts': [{
        'address': f'redis://127.0.0.1:{os.environ["REDIS_PORT"]}/0', 'password': os.environ['REDIS_PASSWORD']}]}}}
    with override_settings(CHANNEL_LAYERS=layers, PASSWORD_HASHERS=global_settings.PASSWORD_HASHERS):
        yield


def seed_rows(source, created):
    from application.models import Associate, Subscription
    from application.models.subscriptions_models import SubscriptionTransfer
    from application.models.user_models import AssociateTutorRelation
    ids = source['ids']
    year = datetime.now(timezone.utc).year

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    season = source.get('season')
    if season:
        from application.models import SportAssociation, User
        insert(User, User(user_id=season['owner'], username='subscription-transfer-season', password='!synthetic', role=1,
                          email='subscription-transfer-season@example.test', first_name='Stagione', last_name='Fixture',
                          subscription_start_day=season['start_day'], subscription_start_month=season['start_month']))
        insert(SportAssociation, SportAssociation(sport_association_id=season['association'], user_id=season['owner'],
                                                  denomination=season['denomination'], tax_code='79600000003'))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row.get('association', ids['own_association']),
                                    first_name=row['first'], last_name=row['last'], born_date=date(1990, 1, 1),
                                    user_id=ids[row['user']] if row['user'] else None))
    for row in source['relations'].values():
        insert(AssociateTutorRelation, AssociateTutorRelation(associate_id=source['associates'][row['associate']]['id'],
                                                              tutor_id=source['associates'][row['tutor']]['id'], is_primary=row['primary']))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row.get('association', ids['own_association']), user_id=ids[row['user']],
            associate_id=source['associates'][row['associate']]['id'], start_date=date(year, 1, 1), end_date=date(year, 12, 31),
            subscription_number='1', status_flag=4))
    for row in source['transfers'].values():
        insert(SubscriptionTransfer, SubscriptionTransfer(
            subscription_transfer_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'],
            requester_id=ids['owner'], recipient_id=ids[row['recipient']] if row['recipient'] else None, status=row['status'],
            expires_at=PAST if row['expires'] == 'past' else FUTURE, token=uuid.uuid5(uuid.NAMESPACE_URL, row['id'])))


def redis_db(raw, db):
    import redis
    options = raw.connection_pool.connection_kwargs
    return redis.Redis(host=options['host'], port=options['port'], db=db, password=options.get('password'),
                       decode_responses=False, socket_timeout=5, socket_connect_timeout=5)


def store_key(user):
    return f'broadcast_{user}'


def cache_key(user):
    return f':1:user_broadcasts:{user}'.encode()


def group_name(user):
    return f'notifications_user_{user}'


class Mirror:
    """Collects the envelopes published on the realtime pub/sub channel."""

    def __init__(self, client):
        self.pubsub = client.pubsub(ignore_subscribe_messages=True)
        self.pubsub.subscribe(REALTIME)
        self.received, self.lock = [], threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.running = True
        self.thread.start()

    def run(self):
        while self.running:
            message = self.pubsub.get_message(timeout=0.2)
            if message and message['type'] == 'message':
                with self.lock:
                    self.received.append(message['data'])

    def drain(self):
        with self.lock:
            items, self.received = self.received, []
        return items

    def close(self):
        self.running = False
        self.thread.join(5)
        self.pubsub.close()


def channel_receive(layer, channel, timeout):
    from asgiref.sync import async_to_sync
    import asyncio

    async def receive():
        try:
            return await asyncio.wait_for(layer.receive(channel), timeout)
        except asyncio.TimeoutError:
            return None
    return async_to_sync(receive)()


def probe_channel(label):
    return f'subscription-transfer-probe-{label}'


# Tutor relations (integer keys) go with their associates.
_identity_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=tuple(
    (lambda label=label: models()[label]) for label in LABELS.values() if label != LABELS['relation'])))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from notifications.services import NotificationService
    source = contract.load_source()
    ids = source['ids']
    store = redis_db(raw, 0)
    users = [ids[label] for label in PROBED]
    original = {user: (store.dump(store_key(user)), store.pttl(store_key(user))) for user in users}
    STATE['new_users'] = []
    for user in users:
        raw.delete(cache_key(user))
    mirror, layer = None, None
    known = set(str(pk) for pk in models()[LABELS['user']]._base_manager.values_list('pk', flat=True))
    try:
        with _identity_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
            if source.get('season'):
                from rest_framework_simplejwt.tokens import AccessToken
                token = AccessToken()
                token['user_id'] = source['season']['owner']
                fixture['tokens'] = {**fixture['tokens'], 'season_owner': str(token)}
            NotificationService.get_manager()
            for user in users:
                store.delete(store_key(user))
            layer = get_channel_layer()
            for label in PROBED:
                async_to_sync(layer.group_add)(group_name(ids[label]), probe_channel(label))
            mirror = Mirror(store)
            time.sleep(0.2)
            fixture.update(store=store, layer=layer, mirror=mirror, raw=raw)
            try:
                yield fixture
                for label in PROBED:
                    async_to_sync(layer.group_discard)(group_name(ids[label]), probe_channel(label))
            finally:
                release_new_users(source, known)
    finally:
        if mirror is not None:
            mirror.close()
        for user in users:
            store.delete(store_key(user))
            if original[user][0] is not None:
                store.restore(store_key(user), max(original[user][1], 0), original[user][0], replace=True)
            raw.delete(cache_key(user))
        for user in STATE['new_users']:
            store.delete(store_key(user))
            raw.delete(cache_key(user))
        store.close()


def release_new_users(source, known):
    """Point the seeded rows that reference accounts the run created back to their
    seeded users, so the harness can purge those accounts."""
    from auditlog.context import disable_auditlog
    from application.models import Associate, Subscription, User
    ids = source['ids']
    created = [pk for pk in User._base_manager.values_list('pk', flat=True) if str(pk) not in known]
    STATE['new_users'] = sorted(set(STATE.get('new_users', [])) | {str(pk) for pk in created})
    with disable_auditlog():
        for row in source['associates'].values():
            Associate._base_manager.filter(pk=row['id']).update(user_id=ids[row['user']] if row['user'] else None)
        for row in source['subscriptions'].values():
            Subscription._base_manager.filter(pk=row['id']).update(user_id=ids[row['user']])


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def rows(state, label):
    return {str(plain(row[KEYS[label]])): plain(row) for row in state[label]}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def in_window(when, since, until, offset=0):
    return since - 1 + offset <= when.timestamp() <= until + 1 + offset


def uuid4_text(text):
    try:
        return uuid.UUID(text).version == 4 and str(uuid.UUID(text)) == text
    except (TypeError, ValueError, AttributeError):
        return False


def naive(value):
    return str(value.astimezone(timezone.utc).replace(tzinfo=None))


def check_transfers(effect, before, after, since, until, binding):
    old, current = rows(before, LABELS['transfer']), rows(after, LABELS['transfer'])
    contract.require(set(old) <= set(current), 'state.transfer-deleted')
    for key, row in old.items():
        expected = dict(row)
        if effect and effect.get('expire') and moment(row['expires_at']).timestamp() < since:
            expected['status'] = 4
        contract.require(current[key] == expected, 'state.transfer-changed:' + key)
    new = [current[k] for k in current if k not in old]
    spec = (effect or {}).get('transfer')
    contract.require(len(new) == (1 if spec else 0), 'state.transfer-count')
    if not spec:
        return
    row = new[0]
    body = contract.LAST.get('body') or {}
    source, ids = STATE['source'], STATE['source']['ids']
    if spec.get('status') == 1:
        # Created, then the request failed before the acceptance: no response body to bind.
        contract.require(uuid4_text(row['subscription_transfer_id']) and uuid4_text(row['token'])
                         and in_window(moment(row['created_at']), since, until)
                         and in_window(moment(row['expires_at']), since, until, TTL), 'state.transfer-pending-dates')
        contract.require(row == {'subscription_transfer_id': row['subscription_transfer_id'],
                                 'subscription_id': source['subscriptions'][spec['subscription']]['id'], 'created_at': row['created_at'],
                                 'expires_at': row['expires_at'], 'requester_id': ids[spec['requester']],
                                 'recipient_id': binding['$recipient'], 'token': row['token'], 'status': 1}, 'state.transfer-pending-row')
        binding['$transfer'] = row['subscription_transfer_id']
        return
    contract.require(uuid4_text(row['subscription_transfer_id']) and uuid4_text(row['token']) and body.get('token') == row['token'],
                     'state.transfer-keys')
    expires = moment(row['expires_at'])
    contract.require(in_window(expires, since, until) and in_window(moment(row['created_at']), since, until)
                     and moment(row['created_at']) <= expires and moment(body.get('expires_at', '')) == expires
                     and body['expires_at'] == expires.isoformat().replace('+00:00', 'Z'), 'state.transfer-dates')
    contract.require(row == {'subscription_transfer_id': row['subscription_transfer_id'],
                             'subscription_id': source['subscriptions'][spec['subscription']]['id'], 'created_at': row['created_at'],
                             'expires_at': row['expires_at'], 'requester_id': ids[spec['requester']], 'recipient_id': binding['$recipient'],
                             'token': row['token'], 'status': 2}, 'state.transfer-row')
    binding['$transfer'] = row['subscription_transfer_id']


def check_new_user(effect, before, after, since, until, binding, welcome):
    """At most one new account; returns its password when the welcome e-mail carries it."""
    from django.contrib.auth.hashers import check_password, get_hashers, identify_hasher
    from application.models import User
    old, current = rows(before, LABELS['user']), rows(after, LABELS['user'])
    contract.require(set(old) <= set(current) and all(current[k] == v for k, v in old.items()), 'state.users-changed')
    new = [k for k in current if k not in old]
    spec = effect.get('new_user') if effect else None
    contract.require(len(new) == (1 if spec else 0), 'state.user-count')
    if not spec:
        return None
    key = new[0]
    STATE['new_users'].append(key)
    user = User._base_manager.get(pk=key)
    binding['$new_user'] = key
    contract.require(uuid4_text(key) and re.fullmatch(re.escape(spec['prefix']) + r'[A-Z0-9]{8}', user.username) is not None
                     and user.email == spec['email'] and user.first_name == spec['first_name'] and user.last_name == spec['last_name']
                     and user.role == User.ATHLETE, 'state.user-fields')
    contract.require(user.is_active is True and user.is_staff is False and user.is_superuser is False and user.last_login is None
                     and in_window(user.date_joined, since, until), 'state.user-flags')
    for field in User._meta.concrete_fields:
        if field.attname in {'user_id', 'password', 'date_joined', 'is_active', 'is_staff', 'is_superuser', 'last_login', 'username', 'email',
                             'first_name', 'last_name', 'role'}:
            continue
        contract.require(getattr(user, field.attname) == field.get_default(), 'state.user-default:' + field.attname)
    algorithm, iterations, salt, digest = user.password.split('$')
    default = get_hashers()[0]
    contract.require(identify_hasher(user.password).algorithm == default.algorithm and iterations == str(default.iterations)
                     and len(salt) >= 22 and salt.isalnum() and len(digest) == 44, 'state.user-password-format')
    if welcome is None:
        return None
    match = re.search(r'password: <b>([A-Za-z0-9]{8})</b>', welcome)
    contract.require(match is not None and check_password(match.group(1), user.password), 'state.user-password')
    return match.group(1)


def check_rows(effect, before, after, binding):
    source, ids = STATE['source'], STATE['source']['ids']
    effect = effect or {}
    recipient = binding.get('$recipient')
    for label, spec_key, changed in (('subscription', 'subscriptions', [effect['subscription']] if effect.get('subscription') else []),
                                     ('associate', 'associates', effect.get('associates') or [])):
        old, current = rows(before, LABELS[label]), rows(after, LABELS[label])
        contract.require(set(old) == set(current), 'state.count:' + label)
        changed_ids = {source[spec_key][name]['id'] for name in changed}
        for key, row in old.items():
            expected = {**row, 'user_id': recipient} if key in changed_ids else row
            contract.require(current[key] == expected, f'state.{label}-changed:{key}')
    old, current = rows(before, LABELS['relation']), rows(after, LABELS['relation'])
    contract.require(set(old) == set(current), 'state.relation-count')
    relation = effect.get('relation')
    target = None
    if relation:
        spec = source['relations'][relation]
        pair = (source['associates'][spec['associate']]['id'], source['associates'][spec['tutor']]['id'])
        target = next(k for k, row in old.items() if (row['associate_id'], row['tutor_id']) == pair)
        binding['$relation:' + relation] = int(target)
    for key, row in old.items():
        expected = {**row, 'is_primary': True} if key == target else row
        contract.require(current[key] == expected, 'state.relation-changed')
    broadcasts = [r for k, r in rows(after, LABELS['broadcast']).items() if k not in rows(before, LABELS['broadcast'])]
    subscribed = [r for k, r in rows(after, LABELS['user_broadcast']).items() if k not in rows(before, LABELS['user_broadcast'])]
    expected_new = any(item['model'] == 'notificationsbroadcast' for item in effect.get('audit', []))
    if expected_new:
        contract.require(len(broadcasts) == 1 and broadcasts[0]['broadcast_name'] == store_key(recipient)
                         and uuid4_text(broadcasts[0]['broadcast_id']), 'state.broadcast')
        contract.require(len(subscribed) == 1 and subscribed[0]['user_id'] == recipient
                         and subscribed[0]['broadcast_id'] == broadcasts[0]['broadcast_id']
                         and uuid4_text(subscribed[0]['user_broadcast_id']), 'state.user-broadcast')
        binding['$notificationsbroadcast'] = broadcasts[0]['broadcast_id']
        binding['$usernotificationsbroadcast'] = subscribed[0]['user_broadcast_id']
    else:
        contract.require(not broadcasts and not subscribed, 'state.broadcast-created')


def resolve(value, binding):
    if isinstance(value, str) and value in binding:
        return binding[value]
    if isinstance(value, dict):
        return {k: resolve(v, binding) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, binding) for v in value]
    return value


def check_audit(case, effect, before, after, fixture, context, binding, since, until):
    from auditlog.diff import model_instance_diff
    from application.models import Associate, Subscription, User
    from application.models.subscriptions_models import SubscriptionTransfer
    from application.models.user_models import AssociateTutorRelation
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast
    added, indexes = harness.audit_delta(contract, before, after)
    added = sorted(added, key=lambda event: int(event['id']))
    authored = (effect or {}).get('audit', [])
    contract.require(len(added) == len(authored), f'audit.count:{len(added)}')
    source, ids = STATE['source'], STATE['source']['ids']
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    if not authored:
        return
    actor = ids[case['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    classes = {'associate': Associate, 'associatetutorrelation': AssociateTutorRelation, 'subscription': Subscription,
               'subscriptiontransfer': SubscriptionTransfer, 'user': User, 'notificationsbroadcast': NotificationsBroadcast,
               'usernotificationsbroadcast': UserNotificationsBroadcast}
    accepted = next((event for event, spec in zip(added, authored) if spec['changes'] == '$accepted'), None)
    for position, (event, spec) in enumerate(zip(added, authored)):
        where = f':{position}:{spec["model"]}'
        app = spec.get('app', 'application')
        ref = spec['ref']
        if ref in binding:
            pk = binding[ref]
        elif spec['model'] == 'associatetutorrelation':
            pk = binding['$relation:' + ref]
        elif spec['model'] == 'associate':
            pk = source['associates'][ref]['id']
        else:
            pk = source['subscriptions'][ref]['id']
        model = classes[spec['model']]
        instance = model._base_manager.get(pk=pk)
        contract.require(event['content_type_id'] == types[(app, spec['model'])] and event['action'] == spec['action']
                         and event['object_pk'] == str(pk) and event['object_repr'] == str(instance)
                         and event['object_id'] == (pk if isinstance(pk, int) else None), 'audit.identity' + where)
        if spec['changes'] == '$accepted':
            final = naive(instance.expires_at)
            old = event['changes'].get('expires_at', [None])[0]
            contract.require(set(event['changes']) == {'status', 'expires_at'} and event['changes']['status'] == ['1', '2']
                             and event['changes']['expires_at'][1] == final and isinstance(old, str)
                             and in_window(datetime.fromisoformat(old).replace(tzinfo=timezone.utc), since, until, TTL), 'audit.accepted' + where)
            expected = event['changes']
        elif spec['changes'] == '$created':
            if model is SubscriptionTransfer and accepted is not None:
                # The create entry renders the instance before its acceptance.
                original = datetime.fromisoformat(accepted['changes']['expires_at'][0]).replace(tzinfo=timezone.utc)
                instance.status, instance.expires_at = SubscriptionTransfer.PENDING, original
            expected = {k: list(v) for k, v in model_instance_diff(None, instance).items()}
        else:
            expected = resolve(spec['changes'], binding)
        contract.require(event['changes'] == expected, 'audit.changes' + where + json.dumps(
            {k: [expected.get(k), event['changes'].get(k)] for k in set(expected) | set(event['changes'])
             if expected.get(k) != event['changes'].get(k)}))
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where + json.dumps([event['actor_id'], event['actor_email'], actor]))
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote' + where)
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


def welcome_spec(user_id, password):
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.utils import translation
    from application.models import SportAssociation, User
    user = User._base_manager.get(pk=user_id)
    data = {'first_name': user.first_name, 'last_name': user.last_name, 'username': user.username, 'email': user.email,
            'password': password, 'sport_association': SportAssociation._base_manager.get(pk=STATE['source']['ids']['own_association']),
            'app_host': settings.APP_URL, 'settings': {'WHITELABEL_NAME': settings.WHITELABEL_NAME, 'IS_WHITELABEL': settings.IS_WHITELABEL}}
    with translation.override('en'):
        message = render_to_string('email/account/email_welcome_password_message.html', data)
    return {'task': SEND_MAIL, 'args': [], 'kwargs': [['subject', f'{settings.WHITELABEL_NAME} | Benvenuto'], ['message', message],
                                                     ['from_email', settings.DEFAULT_FROM_EMAIL], ['recipient_list', [user.email]],
                                                     ['html_message', message], ['fail_silently', False]]}


def check_notification(effect, fixture, binding, since, until, prepared):
    store, layer, raw = fixture['store'], fixture['layer'], fixture['raw']
    ids = STATE['source']['ids']
    notification = (effect or {}).get('notification')
    received = {}
    for label in PROBED:
        received[label] = []
        while True:
            expect = notification is not None and notification['user'] == label and not received[label]
            message = channel_receive(layer, probe_channel(label), 3 if expect else 0.3)
            if message is None:
                break
            received[label].append(message)
    time.sleep(0.3)
    envelopes = fixture['mirror'].drain()
    for label in PROBED:
        user = ids[label]
        if notification is None or notification['user'] != label:
            contract.require(store.get(store_key(user)) == prepared['store'][label], 'notification.store-unchanged:' + label)
            contract.require(raw.get(cache_key(user)) == prepared['cache'][label], 'notification.cache-unchanged:' + label)
            contract.require(not received[label], 'notification.no-push:' + label)
    if notification is None:
        contract.require(not envelopes, 'notification.no-mirror')
        return
    label = notification['user']
    user = binding.get('$recipient')
    contract.require(user is not None, 'notification.recipient-unbound')
    created = any(item['model'] == 'notificationsbroadcast' for item in effect['audit'])
    raw_value = store.get(store_key(user))
    before = prepared['store'].get(label)
    previous = [] if created or before is None else json.loads(before)['messages']
    stored = json.loads(raw_value)
    contract.require(isinstance(stored, dict) and list(stored) == ['messages'] and isinstance(stored['messages'], list)
                     and len(stored['messages']) == len(previous) + 1, 'notification.store-shape')
    item = stored['messages'][-1]
    identifier = item.get('id')
    contract.require(isinstance(identifier, str) and uuid.UUID(identifier).version == 1 and str(uuid.UUID(identifier)) == identifier,
                     'notification.id')
    expires = item.get('expires')
    contract.require(type(expires) is int and int(since) - 1 + TTL <= expires <= int(until) + 1 + TTL, 'notification.expires')
    expected_item = {'id': identifier, 'type': 'subscription', 'msg': notification['msg'], 'association_id': notification['association'],
                     'users': [], 'expires': expires}
    contract.require(raw_value == json.dumps({'messages': previous + [expected_item]}).encode(), 'notification.store-bytes')
    cached = raw.get(cache_key(user))
    contract.require(cached is not None and pickle.loads(cached) == [store_key(user), 'all_notifications']
                     and 0 < raw.ttl(cache_key(user)) <= 300, 'notification.cache')
    if prepared['cache'].get(label) is not None:
        contract.require(cached == prepared['cache'][label], 'notification.cache-kept')
    issued = expires - TTL
    pushed = {'id': identifier, 'type': 'subscription', 'read': False, 'msg': notification['msg'],
              'association_id': notification['association'], 'issued': issued, 'date': time.ctime(issued)}
    if label in PROBED:
        contract.require(len(received[label]) == 1, f'notification.push-count:{len(received[label])}')
        message = received[label][0]
        contract.require(message == {'type': 'notification_push', 'notification': pushed, '__asgi_channel__': message.get('__asgi_channel__')}
                         or message == {'type': 'notification_push', 'notification': pushed}, 'notification.push')
    contract.require(len(envelopes) == 1, f'notification.mirror-count:{len(envelopes)}')
    contract.require(envelopes[0] == json.dumps({'version': 1, 'group': group_name(user),
                                                 'message': {'type': 'notification_push', 'notification': pushed}}).encode(),
                     'notification.mirror')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    prepared = context['prepared']
    since, until = prepared['started'], time.time()
    effect = case['effect']
    binding = {}
    try:
        messages = harness.drain_celery(fixture)
        welcome = None
        if effect and effect.get('welcome'):
            contract.require(len(messages) == 1, 'celery.count')
            welcome = json.loads(messages[0])
            from kombu.utils.json import loads
            import base64
            welcome = loads(base64.b64decode(welcome['body']))[1].get('message')
        else:
            contract.require(not messages, 'celery.count')
        password = check_new_user(effect, before, after, since, until, binding, welcome)
        transfer = (effect or {}).get('transfer')
        if transfer:
            binding['$recipient'] = binding['$new_user'] if transfer['recipient'] == '$new_user' else ids[transfer['recipient']]
            if transfer.get('status') != 1:
                body = contract.LAST.get('body') or {}
                contract.require(body.get('recipient') == binding['$recipient'], 'response.recipient-bound')
        check_transfers(effect, before, after, since, until, binding)
        check_rows(effect, before, after, binding)
        if welcome is not None:
            spec = welcome_spec(binding['$new_user'], password)
            for index, raw in enumerate(messages):
                harness.check_celery_message(contract, raw, spec, since=since, until=until, where=f':{index}')
            key, member = harness.CELERY_BINDING
            contract.require({m.decode() for m in fixture['celery_broker'].smembers(key)} == {member}, 'celery.binding')
        check_audit(case, effect, before, after, fixture, context, binding, since, until)
    except BaseException:
        try:  # drain the notification side channels, keeping the first failure
            check_notification(effect, fixture, binding, since, until, prepared)
        except BaseException:
            pass
        raise
    check_notification(effect, fixture, binding, since, until, prepared)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    STATE['source'] = source
    ids = source['ids']
    results, context = [], {'remote': {}, 'fixture': fixture}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        context['prepared'] = {'started': time.time(),
                               'store': {label: fixture['store'].get(store_key(ids[label])) for label in PROBED},
                               'cache': {label: raw.get(cache_key(ids[label])) for label in PROBED}}
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, context)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    # The notifications' user_broadcasts entries were checked after every case; the rest of the cache must be untouched.
    for user in [ids[label] for label in PROBED] + STATE['new_users']:
        raw.delete(cache_key(user))
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}

