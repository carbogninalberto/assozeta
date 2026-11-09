"""Parent-only subscription/add HTTP runner; imports are inert.

service_context points Django's channel layer (GoMirroredRedisChannelLayer)
at the run's private Redis database 0, where production keeps it and where
the Go server writes. seed_rows applies the association/owner settings and
inserts (audit disabled) associates, certificates, subscriptions, a payment,
the default bank account and, when the database has none, the default
payment category. A case's `setup` updates the settings before the case.

Every case is checked against the complete model state:
- new and changed associates, tutor relations, payments, subscriptions,
  memberships, certificates and notification broadcasts are exact (new rows
  are the model's field defaults overlaid with the authored columns; keys are
  uuid4 and creation dates fall in the request window);
- the auditlog entries are exactly the authored ones in order, creates being
  every stored field against None with the authored in-memory renderings;
- the Celery messages on the broker (check_workflows_trigger with UUIDs,
  print_document_subscription with the Authorization header);
- the notification store (Redis DB 0 broadcast_<owner>: Python json.dumps
  of the previous list plus the new item with a uuid1 id and expires within
  the window plus 30 days), the user_broadcasts cache entry, the
  notification_push message a Channels consumer of notifications_user_<owner>
  receives, and the mirrored envelope on assozeta:realtime:v1.
Failures leave all of it unchanged.
"""
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone
import json
import os
import pickle
import threading
import time
import uuid

from TESTS.contracts import test_subscription_create_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_create_http.json', 'TESTS/contracts/test_subscription_create_http.py',
           'TESTS/adapters/django/subscription_create_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/utils/subscriptions_utils.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/serializers/subscriptions_serializers.py', 'BE/application/exceptions.py',
           'BE/application/signals.py', 'BE/application/tasks.py', 'BE/application/utils/api_utils.py',
           'BE/application/utils/notification_utils.py', 'BE/notifications/services.py', 'BE/notifications/utils.py',
           'BE/notifications/models.py', 'BE/notifications/realtime_layer.py', 'BE/application/audit_resolvers.py',
           'BE/application/mixin.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/core/celery.py', 'BE/core/settings.py') + harness.SOURCES
TTL = 60 * 60 * 24 * 30
REALTIME = 'assozeta:realtime:v1'
CATEGORY_NAME = 'entrate e proventi da attività tipiche'
LABELS = {
    'associate': 'application.Associate', 'relation': 'application.AssociateTutorRelation', 'payment': 'application.Payment',
    'subscription': 'application.Subscription', 'membership': 'application.SubscriptionMembership',
    'medical': 'application.MedicalCertificate', 'broadcast': 'notifications.NotificationsBroadcast',
    'user_broadcast': 'notifications.UserNotificationsBroadcast',
}
MANAGED = (*LABELS.values(), harness.AUDIT, harness.INDEX)
KEYS = {'application.Associate': 'associate_id', 'application.AssociateTutorRelation': 'id', 'application.Payment': 'payment_id',
        'application.Subscription': 'subscription_id', 'application.SubscriptionMembership': 'subscription_membership_id',
        'application.MedicalCertificate': 'medical_id', 'notifications.NotificationsBroadcast': 'broadcast_id',
        'notifications.UserNotificationsBroadcast': 'user_broadcast_id'}
STATE = {}


def models():
    from application.models import Associate, Payment, Subscription
    from application.models.subscriptions_models import MedicalCertificate, SubscriptionMembership
    from application.models.user_models import AssociateTutorRelation
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast
    return {'application.Associate': Associate, 'application.AssociateTutorRelation': AssociateTutorRelation,
            'application.Payment': Payment, 'application.Subscription': Subscription,
            'application.SubscriptionMembership': SubscriptionMembership, 'application.MedicalCertificate': MedicalCertificate,
            'notifications.NotificationsBroadcast': NotificationsBroadcast,
            'notifications.UserNotificationsBroadcast': UserNotificationsBroadcast}


@contextmanager
def service_context(report, log):
    from django.test.utils import override_settings
    layers = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer', 'CONFIG': {'hosts': [{
        'address': f'redis://127.0.0.1:{os.environ["REDIS_PORT"]}/0', 'password': os.environ['REDIS_PASSWORD']}]}}}
    with override_settings(CHANNEL_LAYERS=layers):
        yield


def today():
    return datetime.now(timezone.utc).date()


def season():
    year = today().year
    return date(year, 1, 1), date(year, 12, 31)


def resolve_date(text):
    if not isinstance(text, str) or not text.startswith('$'):
        return date.fromisoformat(text) if isinstance(text, str) else text
    start, end = season()
    values = {'$today': today(), '$season_start': start, '$season_end': end,
              '$today_next_year': today().replace(year=today().year + 1) if (today().month, today().day) != (2, 29)
              else date(today().year + 1, 2, 28),
              '$season_start_next_year_eve': start.replace(year=start.year + 1) - timedelta(days=1)}
    return values[text]


def apply_settings(ids, settings):
    from auditlog.context import disable_auditlog
    from application.models import SportAssociation, User
    with disable_auditlog():
        if settings.get('association'):
            SportAssociation._base_manager.filter(pk=ids['own_association']).update(**settings['association'])
        if settings.get('owner'):
            User._base_manager.filter(pk=ids['owner']).update(**settings['owner'])


def seed_rows(source, created):
    from application.models import Associate, Payment, PaymentCategory, Subscription
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.subscriptions_models import MedicalCertificate
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    apply_settings(ids, source['settings'])
    category = PaymentCategory.objects.filter(name__iexact=CATEGORY_NAME).first()
    if category is None:
        category = insert(PaymentCategory, PaymentCategory(name=CATEGORY_NAME))
    STATE['category'] = str(category.pk)
    insert(CustomAccounts, CustomAccounts(custom_account_id=source['account'], name='Banca fixture', initial_balance=0,
                                          account_type=CustomAccounts.BANK, account_code='IT00X', editable=False, enabled=True,
                                          sport_association_id=ids['own_association']))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], tax_code=row['tax_code'], born_date=date.fromisoformat(row['born']),
                                    user_id=ids[row['user']] if row['user'] else None, email=row['email']))
    for row in source['certificates'].values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], user_id=ids[row['user']] if row['user'] else None))
    for row in source['subscriptions'].values():
        association = row.get('association', ids['own_association'])
        owner = ids['owner'] if association == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=association, user_id=owner,
            associate_id=source['associates'][row['associate']]['id'], start_date=resolve_date(row['start']),
            end_date=resolve_date(row['end']), archived=row['archived'], subscription_number=row['number'], status_flag=4))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], associate_id=source['associates'][row['associate']]['id'],
                                sport_association_id=ids['own_association'], user_id=ids['owner'], subject=Payment.SUBSCRIPTION,
                                amount=row['amount'], paid=row['paid'], description='Iscrizione', payment_category_id=STATE['category'],
                                payment_date=datetime.combine(resolve_date(row['date']), day_time(), tzinfo=timezone.utc)))


def redis_db(raw, db):
    import redis
    options = raw.connection_pool.connection_kwargs
    return redis.Redis(host=options['host'], port=options['port'], db=db, password=options.get('password'),
                       decode_responses=False, socket_timeout=5, socket_connect_timeout=5)


def store_key(ids):
    return f"broadcast_{ids['owner']}"


def cache_key(ids):
    return f":1:user_broadcasts:{ids['owner']}".encode()


def group_name(ids):
    return f"notifications_user_{ids['owner']}"


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
    key = store_key(ids)
    original = (store.dump(key), store.pttl(key))
    raw.delete(cache_key(ids))
    mirror = None
    try:
        with _identity_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
            # The process-wide manager bootstraps its default broadcast once; do it before any endpoint.
            NotificationService.get_manager()
            store.delete(key)
            layer = get_channel_layer()
            # A plain (not process-specific) channel: received without the layer's per-loop queues.
            channel = 'subscription-create-probe'
            async_to_sync(layer.group_add)(group_name(ids), channel)
            mirror = Mirror(store)
            time.sleep(0.2)
            fixture.update(store=store, layer=layer, channel=channel, mirror=mirror)
            yield fixture
            async_to_sync(layer.group_discard)(group_name(ids), channel)
    finally:
        if mirror is not None:
            mirror.close()
        store.delete(key)
        if original[0] is not None:
            store.restore(key, max(original[1], 0), original[0], replace=True)
        raw.delete(cache_key(ids))
        store.close()


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


def field_defaults(model):
    """The model's own field defaults (an unsaved instance), as snapshot values."""
    instance = model()
    values = {}
    for field in model._meta.concrete_fields:
        values[field.attname] = plain(shared._stable(getattr(instance, field.attname)))
    return values


def in_window(text, since, until):
    moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return since - 1 <= moment.timestamp() <= until + 1


def uuid4_text(text):
    try:
        return uuid.UUID(text).version == 4 and str(uuid.UUID(text)) == text
    except (TypeError, ValueError):
        return False


def created_changes(instance, overrides):
    """auditlog's create diff of a stored row: every field against None (None kept only
    for foreign keys, DateTimeFields and JSON fields), DateTimeFields naive UTC,
    JSON with sort_keys; overrides are the in-memory renderings that differ."""
    from django.db.models import DateTimeField, JSONField
    changes = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if value is None:
            if field.is_relation or isinstance(field, DateTimeField):
                changes[field.name] = ['None', 'None']
            elif isinstance(field, JSONField):
                changes[field.name] = ['None', 'null']
            continue
        if isinstance(field, DateTimeField):
            text = str(value.astimezone(timezone.utc).replace(tzinfo=None))
        elif isinstance(field, JSONField):
            text = json.dumps(value, sort_keys=True)
        else:
            text = str(value)
        changes[field.name] = ['None', text]
    if instance._meta.label == 'application.Associate':
        # auditlog renders the reverse tutor relation accessors of a new associate.
        changes['tutor_relations'] = ['None', 'application.AssociateTutorRelation.None']
        changes['tutored_relations'] = ['None', 'application.AssociateTutorRelation.None']
    for name, pair in overrides.items():
        changes[name] = pair
    return changes


def resolve(value, binding):
    if isinstance(value, str) and value.startswith('$'):
        if value in binding:
            return binding[value]
        return resolve_date(value).isoformat()
    if isinstance(value, dict):
        if set(value) == {'uuid'}:
            return value
        return {k: resolve(v, binding) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, binding) for v in value]
    return value


def membership_type_default():
    start, end = season()
    return f'Tesseramento {start.year}/{end.year}'


def check_rows(case, effect, before, after, ids, since, until, binding):
    """New and changed rows of the managed models; returns the new rows by label."""
    new, bound = {}, {}
    for label in LABELS.values():
        old, current = rows(before, label), rows(after, label)
        contract.require(set(old) <= set(current), 'state.deleted:' + label)
        new[label] = [current[k] for k in current if k not in old]
    associates_old, associates = rows(before, LABELS['associate']), rows(after, LABELS['associate'])
    defaults = {label: field_defaults(model) for label, model in models().items()}
    owner = ids['owner']

    def expect_new(label, columns, row):
        expected = {**defaults[label], **resolve(columns, binding)}
        key = KEYS[label]
        contract.require(uuid4_text(row[key]) or label == LABELS['relation'], 'state.key:' + label)
        expected[key] = row[key]
        if 'creation_date' in expected:
            contract.require(in_window(row['creation_date'], since, until), 'state.created:' + label)
            expected['creation_date'] = row['creation_date']
        contract.require(row == expected, 'state.row:' + label + json.dumps(
            {k: [expected.get(k), row.get(k)] for k in set(row) | set(expected) if expected.get(k) != row.get(k)}, default=str))

    # Associates: the authored new ones, matched by name; the rest unchanged except the authored updates.
    new_associates = {(row['first_name'], row['last_name']): row for row in new[LABELS['associate']]}
    expected_new = [label for label in ('associate', 'tutor') if effect.get(label) and effect[label]['match'] == 'new']
    contract.require(len(new_associates) == len(expected_new) == len(new[LABELS['associate']]), 'state.associate-count')
    changed = {}
    for label in ('associate', 'tutor'):
        spec = effect.get(label)
        if not spec:
            continue
        if spec['match'] == 'new':
            row = new_associates.get((spec['columns']['first_name'], spec['columns']['last_name']))
            contract.require(row is not None, 'state.associate-missing:' + label)
            bound['$' + label] = row['associate_id']
            binding['$' + label] = row['associate_id']
            expect_new(LABELS['associate'], spec['columns'], row)
        else:
            identifier = STATE['source']['associates'][spec['match']]['id']
            bound['$' + label] = identifier
            binding['$' + label] = identifier
            changed[identifier] = spec['columns']
    for identifier, row in associates_old.items():
        expected = {**row, **resolve(changed.get(identifier, {}), binding)}
        contract.require(associates[identifier] == expected, 'state.associate-changed')
    # Tutor relation.
    relations = new[LABELS['relation']]
    contract.require(len(relations) == (1 if effect.get('relation') else 0), 'state.relation-count')
    if relations:
        row = relations[0]
        contract.require(row == {'id': row['id'], 'associate_id': bound['$associate'], 'tutor_id': bound['$tutor'], 'is_primary': True},
                         'state.relation')
        binding['$relation'] = str(row['id'])
    # Certificates.
    certificates_old, certificates = rows(before, LABELS['medical']), rows(after, LABELS['medical'])
    medical = effect.get('medical') or {}
    for identifier, row in certificates_old.items():
        updates = medical.get('columns', {}) if medical and STATE['source']['certificates'][medical['label']]['id'] == identifier else {}
        contract.require(certificates[identifier] == {**row, **resolve(updates, binding)}, 'state.certificate')
    contract.require(not new[LABELS['medical']], 'state.certificate-created')
    # Payment.
    payment = effect.get('payment')
    payments_old, payments = rows(before, LABELS['payment']), rows(after, LABELS['payment'])
    contract.require(all(payments[k] == v for k, v in payments_old.items()), 'state.payment-changed')
    if payment and payment['match'] == 'new':
        contract.require(len(new[LABELS['payment']]) == 1, 'state.payment-count')
        row = new[LABELS['payment']][0]
        binding['$payment'] = row['payment_id']
        columns = {'subject': 1, 'description': 'Iscrizione', 'payment_category_id': STATE['category'], 'user_id': owner,
                   'associate_id': bound['$associate'], 'sport_association_id': ids['own_association'],
                   'custom_accounts_id': STATE['source']['account'],
                   'payment_date': datetime.combine(today(), day_time()).isoformat() + 'Z', **payment['columns']}
        if columns['meta_payment_categories']:
            columns['meta_payment_categories'] = [{**item, 'payment_category_id': STATE['category']}
                                                  for item in columns['meta_payment_categories']]
        expect_new(LABELS['payment'], columns, row)
    else:
        contract.require(not new[LABELS['payment']], 'state.payment-created')
        binding['$payment'] = STATE['source']['payments'][payment['match']]['id'] if payment else None
    # Subscription and membership.
    contract.require(len(new[LABELS['subscription']]) == 1, 'state.subscription-count')
    contract.require(all(rows(after, LABELS['subscription'])[k] == v for k, v in rows(before, LABELS['subscription']).items()),
                     'state.subscription-changed')
    row = new[LABELS['subscription']][0]
    binding['$subscription'] = row['subscription_id']
    expect_new(LABELS['subscription'], {'sport_association_id': ids['own_association'], 'associate_id': bound['$associate'],
                                        'user_id': owner, 'payment_id': binding['$payment'], **effect['subscription']}, row)
    memberships = new[LABELS['membership']]
    contract.require(len(memberships) == (1 if effect.get('membership') else 0), 'state.membership-count')
    if memberships:
        row = memberships[0]
        binding['$membership'] = row['subscription_membership_id']
        columns = {'subscription_id': binding['$subscription'], 'sport_association_id': ids['own_association'],
                   'associate_id': bound['$associate'], 'payment_id': binding['$payment'], **effect['membership']}
        if columns.get('membership_type') == '$default_membership_type':
            columns['membership_type'] = membership_type_default()
        expect_new(LABELS['membership'], columns, row)
    # Notification broadcast rows.
    broadcasts, subscriptions = new[LABELS['broadcast']], new[LABELS['user_broadcast']]
    if effect['broadcast_created']:
        contract.require(len(broadcasts) == 1 and broadcasts[0]['broadcast_name'] == store_key(ids)
                         and uuid4_text(broadcasts[0]['broadcast_id']), 'state.broadcast')
        contract.require(len(subscriptions) == 1 and subscriptions[0]['user_id'] == owner
                         and subscriptions[0]['broadcast_id'] == broadcasts[0]['broadcast_id']
                         and uuid4_text(subscriptions[0]['user_broadcast_id']), 'state.user-broadcast')
        binding['$notificationsbroadcast'] = broadcasts[0]['broadcast_id']
        binding['$usernotificationsbroadcast'] = subscriptions[0]['user_broadcast_id']
    else:
        contract.require(not broadcasts and not subscriptions, 'state.broadcast-created')
    return binding


def check_audit(case, effect, before, after, fixture, ids, context, binding):
    added, indexes = harness.audit_delta(contract, before, after)
    added = sorted(added, key=lambda event: int(event['id']))
    authored = effect['audit']
    contract.require(len(added) == len(authored), f'audit.count:{len(added)}')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    classes = {'associate': 'application.Associate', 'associatetutorrelation': 'application.AssociateTutorRelation',
               'payment': 'application.Payment', 'subscription': 'application.Subscription',
               'subscriptionmembership': 'application.SubscriptionMembership', 'medicalcertificate': 'application.MedicalCertificate',
               'notificationsbroadcast': 'notifications.NotificationsBroadcast',
               'usernotificationsbroadcast': 'notifications.UserNotificationsBroadcast'}
    for position, (event, spec) in enumerate(zip(added, authored)):
        where = f':{position}:{spec["model"]}'
        app = spec.get('app', 'application')
        ref = spec['ref']
        if ref in STATE['source']['certificates']:
            pk = STATE['source']['certificates'][ref]['id']
        else:
            pk = binding['$' + ref]
        model = models()[classes[spec['model']]]
        instance = model._base_manager.get(pk=pk)
        contract.require(event['content_type_id'] == types[(app, spec['model'])] and event['action'] == spec['action']
                         and event['object_pk'] == str(pk) and event['object_repr'] == str(instance), 'audit.identity' + where + json.dumps(
                             [event['content_type_id'], types[(app, spec['model'])], event['action'], event['object_pk'], str(pk),
                              event['object_repr'], str(instance)]))
        if spec['action'] == 0:
            expected = created_changes(instance, resolve(spec['changes'], binding))
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


def check_notification(effect, fixture, ids, since, until, prepared):
    store, layer = fixture['store'], fixture['layer']
    raw_value = store.get(store_key(ids))
    received = []
    while True:
        message = channel_receive(layer, fixture['channel'], 3 if effect and not received else 0.3)
        if message is None:
            break
        received.append(message)
    time.sleep(0.3)
    envelopes = fixture['mirror'].drain()
    if effect is None:
        contract.require(raw_value == prepared['store'], 'notification.store-unchanged')
        contract.require(not received and not envelopes, 'notification.no-push')
        contract.require(fixture['raw'].get(cache_key(ids)) == prepared['cache'], 'notification.cache-unchanged')
        return
    previous = [] if effect['broadcast_created'] or prepared['store'] is None else json.loads(prepared['store'])['messages']
    stored = json.loads(raw_value)
    contract.require(isinstance(stored, dict) and list(stored) == ['messages'] and isinstance(stored['messages'], list)
                     and len(stored['messages']) == len(previous) + 1, 'notification.store-shape')
    item = stored['messages'][-1]
    identifier = item.get('id')
    contract.require(isinstance(identifier, str) and uuid.UUID(identifier).version == 1 and str(uuid.UUID(identifier)) == identifier,
                     'notification.id')
    expires = item.get('expires')
    contract.require(type(expires) is int and int(since) - 1 + TTL <= expires <= int(until) + 1 + TTL, 'notification.expires')
    notification = effect['notification']
    expected_item = {'id': identifier, 'type': 'subscription', 'msg': notification['msg'], 'association_id': notification['association'],
                     'users': [], 'expires': expires}
    contract.require(raw_value == json.dumps({'messages': previous + [expected_item]}).encode(), 'notification.store-bytes')
    cached = fixture['raw'].get(cache_key(ids))
    contract.require(cached is not None and pickle.loads(cached) == [store_key(ids), 'all_notifications']
                     and 0 < fixture['raw'].ttl(cache_key(ids)) <= 300, 'notification.cache')
    if prepared['cache'] is not None:
        contract.require(cached == prepared['cache'], 'notification.cache-kept')
    issued = expires - TTL
    pushed = {'id': identifier, 'type': 'subscription', 'read': False, 'msg': notification['msg'],
              'association_id': notification['association'], 'issued': issued, 'date': time.ctime(issued)}
    contract.require(len(received) == 1, f'notification.push-count:{len(received)}')
    message = received[0]
    contract.require(message == {'type': 'notification_push', 'notification': pushed, '__asgi_channel__': message.get('__asgi_channel__')}
                     or message == {'type': 'notification_push', 'notification': pushed}, 'notification.push')
    contract.require(len(envelopes) == 1, f'notification.mirror-count:{len(envelopes)}')
    contract.require(envelopes[0] == json.dumps({'version': 1, 'group': group_name(ids),
                                                 'message': {'type': 'notification_push', 'notification': pushed}}).encode(),
                     'notification.mirror')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    prepared = context['prepared']
    since, until = prepared['started'], time.time()
    effect = case['effect']
    token = fixture['tokens'].get(case['actor'])
    binding = {'$authorization': f'Bearer {token}' if token else None, '$category': STATE['category']}
    try:
        if effect is None:
            harness.celery_published(contract, fixture, [], since=since, until=until)
            contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        else:
            check_rows(case, effect, before, after, ids, since, until, binding)
            body = contract.LAST.get('body')
            contract.require(isinstance(body, dict) and body.get('payment_id') == binding['$payment'], 'response.payment-bound')
            binding['$subscription_uuid'] = {'uuid': binding['$subscription']}
            harness.celery_published(contract, fixture, resolve(effect['celery'], binding), since=since, until=until)
            check_audit(case, effect, before, after, fixture, ids, context, binding)
    finally:
        check_notification(effect, fixture, ids, since, until, prepared)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """run_workload_for with the case's settings applied before its state snapshot."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    STATE['source'] = source
    ids = source['ids']
    fixture['raw'] = raw
    results, context = [], {'remote': {}, 'fixture': fixture}
    for case in source['cases']:
        if case.get('setup'):
            apply_settings(ids, case['setup'])
        before = scopes.snapshot_state(apps)
        context['prepared'] = {'started': time.time(), 'store': fixture['store'].get(store_key(ids)), 'cache': raw.get(cache_key(ids))}
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'].split('?')[0], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, context)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    # The notifications' user_broadcasts entry was checked after every case; the rest of the cache must be untouched.
    raw.delete(cache_key(ids))
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
