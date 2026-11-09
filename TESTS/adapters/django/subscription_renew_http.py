"""Parent-only subscription/renew and associates-draft approve HTTP runner; imports are inert.

service_context points Django's channel layer (GoMirroredRedisChannelLayer)
at the run's private Redis database 0, as subscription_create_http does.
seed_rows applies the association/owner settings and inserts (audit disabled)
associates (one soft-deleted, two in another association), payments (one
referenced by the previous subscription), subscriptions, tags, a certificate,
a user named by e-mail, the default bank account, import drafts and import
statuses. A case's `setup` updates the settings and draft `valid` flags first.

Every case is checked against the complete model state:
- new rows (matched by authored columns, bound to names) equal the model's
  defaults overlaid with the authored columns; changed rows equal their
  previous state plus the authored columns; deleted rows are exactly the
  authored ones; new subscription tag links are the authored pairs;
- the auditlog entries are exactly the authored ones in order: creates are
  every stored field against None with authored overrides, deletes are
  auditlog's diff of the row as loaded before the request (the approved
  draft's data as its authored str(dict)), the import status created and
  deleted in one request is checked against its own create entry;
- the Celery messages on the broker and the notifications (Redis store
  bytes, user_broadcasts cache, notification_push messages and mirrored
  envelopes, one per created subscription, in order).
Failures leave all of it unchanged.
"""
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone
import json
import os
import pickle
import time
import uuid

from TESTS.contracts import test_subscription_renew_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import subscription_create_http as create
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_renew_http.json', 'TESTS/contracts/test_subscription_renew_http.py',
           'TESTS/adapters/django/subscription_renew_http.py', 'TESTS/adapters/django/subscription_create_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/exceptions.py', 'BE/application/signals.py', 'BE/application/tasks.py', 'BE/application/utils/api_utils.py',
           'BE/application/utils/notification_utils.py', 'BE/notifications/services.py', 'BE/notifications/utils.py',
           'BE/notifications/models.py', 'BE/notifications/realtime_layer.py', 'BE/application/audit_resolvers.py',
           'BE/application/mixin.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/core/celery.py', 'BE/core/settings.py') + harness.SOURCES
TTL = create.TTL
LABELS = {
    'associate': 'application.Associate', 'payment': 'application.Payment', 'subscription': 'application.Subscription',
    'membership': 'application.SubscriptionMembership', 'medical': 'application.MedicalCertificate',
    'broadcast': 'notifications.NotificationsBroadcast', 'user_broadcast': 'notifications.UserNotificationsBroadcast',
    'draft': 'application.AssociateImportDraft', 'status': 'application.AssociateImportDraftStatus', 'link': 'application.Subscription_tags',
}
MANAGED = (*LABELS.values(), harness.AUDIT, harness.INDEX)
KEYS = {'application.Associate': 'associate_id', 'application.Payment': 'payment_id', 'application.Subscription': 'subscription_id',
        'application.SubscriptionMembership': 'subscription_membership_id', 'application.MedicalCertificate': 'medical_id',
        'notifications.NotificationsBroadcast': 'broadcast_id', 'notifications.UserNotificationsBroadcast': 'user_broadcast_id',
        'application.AssociateImportDraft': 'associate_import_draft_id',
        'application.AssociateImportDraftStatus': 'associate_import_draft_status_id', 'application.Subscription_tags': 'id'}
# Seed sections by row label (for references by name).
SECTIONS = {'associate': 'associates', 'payment': 'payments', 'subscription': 'subscriptions', 'medical': 'certificates',
            'draft': 'drafts', 'status': 'statuses', 'tag': 'tags'}
AUDIT_MODELS = {'associate': 'associate', 'payment': 'payment', 'subscription': 'subscription', 'subscriptionmembership': 'membership',
                'medicalcertificate': 'medical', 'associateimportdraft': 'draft', 'associateimportdraftstatus': 'status',
                'notificationsbroadcast': 'broadcast', 'usernotificationsbroadcast': 'user_broadcast'}
STATE = {}


def models():
    from django.apps import apps
    return {label: apps.get_model(label) for label in LABELS.values()}


def model_for(label):
    return models()[LABELS[label]]


service_context = create.service_context


def today():
    return datetime.now(timezone.utc).date()


def resolve_date(text):
    year = today().year
    values = {'$today': today(), '$season_start': date(year, 1, 1), '$season_end': date(year, 12, 31),
              '$last_season_start': date(year - 1, 1, 1), '$last_season_end': date(year - 1, 12, 31)}
    return values[text]


def apply_settings(ids, settings):
    from auditlog.context import disable_auditlog
    from application.models.subscriptions_models import AssociateImportDraft
    create.apply_settings(ids, settings)
    with disable_auditlog():
        for label, valid in (settings.get('drafts') or {}).items():
            AssociateImportDraft._base_manager.filter(pk=STATE['source']['drafts'][label]['id']).update(valid=valid)


def seed_rows(source, created):
    from application.models import Associate, Payment, PaymentCategory, Subscription, User
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.subscriptions_models import AssociateImportDraft, AssociateImportDraftStatus, MedicalCertificate, Tags
    ids = source['ids']
    STATE['source'] = source

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    create.apply_settings(ids, source['settings'])
    category = PaymentCategory.objects.filter(name__iexact=create.CATEGORY_NAME).first()
    if category is None:
        category = insert(PaymentCategory, PaymentCategory(name=create.CATEGORY_NAME))
    STATE['category'] = str(category.pk)
    insert(CustomAccounts, CustomAccounts(custom_account_id=source['account'], name='Banca fixture', initial_balance=0,
                                          account_type=CustomAccounts.BANK, account_code='IT00X', editable=False, enabled=True,
                                          sport_association_id=ids['own_association']))
    target = source['target_user']
    insert(User, User(user_id=target['id'], username=target['username'], email=target['email'], role=User.ATHLETE, password='!synthetic'))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last'], tax_code=row['tax_code'], born_date=date.fromisoformat(row['born']),
                                    user_id=ids[row['user']] if row['user'] else None, email=row['email'], deleted=row['deleted']))
    for row in source['certificates'].values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], user_id=ids[row['user']] if row['user'] else None))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], associate_id=source['associates'][row['associate']]['id'],
                                sport_association_id=ids['own_association'], user_id=ids['owner'], subject=row['subject'],
                                amount=row['amount'], paid=row['paid'], description='Iscrizione', payment_category_id=STATE['category'],
                                payment_date=datetime.combine(resolve_date(row['date']), day_time(), tzinfo=timezone.utc)))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=ids['own_association'], user_id=ids['owner'],
            associate_id=source['associates'][row['associate']]['id'], start_date=resolve_date(row['start']),
            end_date=resolve_date(row['end']), archived=row['archived'], subscription_number=row['number'], status_flag=4,
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None))
    for row in source['tags'].values():
        insert(Tags, Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=row['association']))
    for row in source['drafts'].values():
        insert(AssociateImportDraft, AssociateImportDraft(associate_import_draft_id=row['id'], sport_association_id=row['association'],
                                                          data=row['data'], valid=row['valid']))
    for row in source['statuses'].values():
        insert(AssociateImportDraftStatus, AssociateImportDraftStatus(
            associate_import_draft_status_id=row['id'], sport_association_id=row['association'], doing=row['doing'],
            status_type=row['type']))


_identity_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=tuple(
    (lambda label=label: models()[label]) for label in (
        'application.Associate', 'application.Payment', 'application.Subscription', 'application.SubscriptionMembership',
        'notifications.NotificationsBroadcast', 'notifications.UserNotificationsBroadcast', 'application.AssociateImportDraftStatus'))))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from notifications.services import NotificationService
    source = contract.load_source()
    ids = source['ids']
    store = create.redis_db(raw, 0)
    key = create.store_key(ids)
    original = (store.dump(key), store.pttl(key))
    raw.delete(create.cache_key(ids))
    mirror = None
    try:
        with _identity_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
            NotificationService.get_manager()
            store.delete(key)
            layer = get_channel_layer()
            channel = 'subscription-renew-probe'
            async_to_sync(layer.group_add)(create.group_name(ids), channel)
            mirror = create.Mirror(store)
            time.sleep(0.2)
            fixture.update(store=store, layer=layer, channel=channel, mirror=mirror)
            yield fixture
            async_to_sync(layer.group_discard)(create.group_name(ids), channel)
    finally:
        if mirror is not None:
            mirror.close()
        store.delete(key)
        if original[0] is not None:
            store.restore(key, max(original[1], 0), original[0], replace=True)
        raw.delete(create.cache_key(ids))
        store.close()


plain = create.plain


def rows(state, label):
    model = LABELS[label]
    return {str(plain(row[KEYS[model]])): plain(row) for row in state[model]}


def seed_id(label, ref):
    return STATE['source'][SECTIONS[label]][ref]['id']


def resolve(value, binding):
    if isinstance(value, str) and value.startswith('$'):
        if value in binding:
            return binding[value]
        if value == '$default_membership_type':
            return create.membership_type_default()
        if value == '$today_midnight':
            return datetime.combine(today(), day_time()).isoformat() + 'Z'
        return resolve_date(value).isoformat()
    if isinstance(value, dict):
        return {k: resolve(v, binding) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, binding) for v in value]
    return value


def ref_id(label, ref, binding):
    return binding[ref] if ref.startswith('$') else seed_id(label, ref)


def check_rows(effect, before, after, ids, since, until, binding):
    defaults = {label: create.field_defaults(model) for label, model in models().items() if label != LABELS['link']}
    new, olds, currents = {}, {}, {}
    for label in LABELS:
        olds[label], currents[label] = rows(before, label), rows(after, label)
        new[label] = {k: v for k, v in currents[label].items() if k not in olds[label]}
    # Deleted rows: exactly the authored ones.
    for label in LABELS:
        gone = {k for k in olds[label] if k not in currents[label]}
        expected = {seed_id(label, ref) for ref in effect['deleted'].get(label, [])}
        contract.require(gone == expected, 'state.deleted:' + label + json.dumps(sorted(gone)))
    # New rows: each authored spec matches exactly one new row (in spec order, so bindings resolve).
    claimed = {label: set() for label in LABELS}
    for spec in effect['new']:
        label = spec['label']
        match = resolve(spec['match'], binding)
        candidates = [k for k, row in new[label].items() if k not in claimed[label] and all(row.get(c) == v for c, v in match.items())]
        contract.require(len(candidates) == 1, f'state.new-match:{label}:{spec["bind"]}:{len(candidates)}')
        key = candidates[0]
        claimed[label].add(key)
        binding[spec['bind']] = key
        row = new[label][key]
        model = LABELS[label]
        expected = {**defaults[model], **match, **resolve(spec['columns'], binding)}
        contract.require(create.uuid4_text(key) or key == spec['match'].get(KEYS[model]), 'state.key:' + label)
        expected[KEYS[model]] = row[KEYS[model]]
        if 'creation_date' in expected:
            contract.require(create.in_window(row['creation_date'], since, until), 'state.created:' + label)
            expected['creation_date'] = row['creation_date']
        contract.require(row == expected, 'state.row:' + label + json.dumps(
            {k: [expected.get(k), row.get(k)] for k in set(row) | set(expected) if expected.get(k) != row.get(k)}, default=str))
    # Notification broadcast rows (created the first time the owner is notified).
    created_broadcast = any(spec['model'] == 'notificationsbroadcast' for spec in effect['audit'])
    broadcasts, subscriptions = list(new['broadcast'].values()), list(new['user_broadcast'].values())
    if created_broadcast:
        contract.require(len(broadcasts) == 1 and broadcasts[0]['broadcast_name'] == create.store_key(ids)
                         and create.uuid4_text(broadcasts[0]['broadcast_id']), 'state.broadcast')
        contract.require(len(subscriptions) == 1 and subscriptions[0]['user_id'] == ids['owner']
                         and subscriptions[0]['broadcast_id'] == broadcasts[0]['broadcast_id'], 'state.user-broadcast')
        binding['$notificationsbroadcast'] = broadcasts[0]['broadcast_id']
        binding['$usernotificationsbroadcast'] = subscriptions[0]['user_broadcast_id']
        claimed['broadcast'].add(broadcasts[0]['broadcast_id'])
        claimed['user_broadcast'].add(subscriptions[0]['user_broadcast_id'])
    # Tag links: exactly the authored pairs.
    links = {(row['subscription_id'], row['tags_id']) for row in new['link'].values()}
    expected_links = {(binding[sub], seed_id('tag', tag)) for sub, tag in effect['tags']}
    contract.require(links == expected_links, 'state.tags')
    claimed['link'] = set(new['link'])
    for label in LABELS:
        contract.require(set(new[label]) == claimed[label], 'state.unexpected-new:' + label)
    # Changed rows: the authored columns; every other surviving row unchanged.
    for label in LABELS:
        changes = {ref_id(label, ref, binding): columns for ref, columns in effect['changed'].get(label, {}).items()}
        for key, row in olds[label].items():
            if key not in currents[label]:
                continue
            expected = {**row, **resolve(changes.get(key, {}), binding)}
            contract.require(currents[label][key] == expected, 'state.changed:' + label + json.dumps(
                {k: [expected.get(k), currents[label][key].get(k)] for k in expected if expected.get(k) != currents[label][key].get(k)},
                default=str))
    return binding


def prepare_deletes(case):
    """auditlog's delete diff and repr of the seeded rows the case deletes, as loaded before the request."""
    from auditlog.diff import model_instance_diff
    prepared = {}
    for spec in (case['effect'] or {}).get('audit', []):
        if spec['action'] != 2 or spec['ref'].startswith('$'):
            continue
        label = AUDIT_MODELS[spec['model']]
        instance = model_for(label)._base_manager.get(pk=seed_id(label, spec['ref']))
        changes = {k: list(v) for k, v in model_instance_diff(instance, None).items()}
        prepared[spec['ref']] = (changes, str(instance))
    return prepared


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
    statuses = {}
    for position, (event, spec) in enumerate(zip(added, authored)):
        where = f':{position}:{spec["model"]}'
        label = AUDIT_MODELS[spec['model']]
        model = model_for(label)
        name = model.__name__
        if spec.get('status'):
            # The import status created (and deleted) by the request.
            if spec['status'] == 'create':
                pk = event['object_pk']
                statuses[pk] = event['changes']
                contract.require(create.uuid4_text(pk), 'audit.status-key' + where)
                created_at = event['changes'].get('creation_date', [None, ''])[1]
                expected = {'associate_import_draft_status_id': ['None', pk], 'sport_association': ['None', ids['own_association']],
                            'doing': ['None', 'True'], 'status_type': ['None', '2'], 'creation_date': ['None', created_at]}
                contract.require(create.in_window(created_at + 'Z', context['prepared']['started'], time.time()), 'audit.status-created' + where)
                binding['$status'] = pk
            else:
                pk = binding['$status']
                expected = {k: [v[1], 'None'] for k, v in statuses[pk].items()}
            representation = f'{name} object ({pk})'
        else:
            if spec['action'] == 2 and not spec['ref'].startswith('$'):
                pk = seed_id(label, spec['ref'])
                changes, representation = context['deletes'][spec['ref']]
                expected = {**changes, **resolve(spec['changes'], binding)}
            else:
                pk = ref_id(label, spec['ref'], binding) if spec['ref'].startswith('$') or label in SECTIONS else binding['$' + spec['ref']]
                instance = model._base_manager.get(pk=pk)
                representation = str(instance)
                if spec['action'] == 0 or spec.get('created_style'):
                    expected = create.created_changes(instance, resolve(spec['changes'], binding))
                else:
                    expected = resolve(spec['changes'], binding)
        contract.require(event['content_type_id'] == types[(spec['app'], spec['model'])] and event['action'] == spec['action']
                         and event['object_pk'] == str(pk) and event['object_repr'] == representation, 'audit.identity' + where + json.dumps(
                             [event['action'], event['object_pk'], str(pk), event['object_repr'], representation]))
        contract.require(event['changes'] == expected, 'audit.changes' + where + json.dumps(
            {k: [expected.get(k), event['changes'].get(k)] for k in set(expected) | set(event['changes'])
             if expected.get(k) != event['changes'].get(k)}, ensure_ascii=False))
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote' + where)
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


def check_notifications(effect, fixture, ids, since, until, prepared):
    store, layer = fixture['store'], fixture['layer']
    raw_value = store.get(create.store_key(ids))
    expected = (effect or {}).get('notifications', [])
    received = []
    while True:
        message = create.channel_receive(layer, fixture['channel'], 3 if len(received) < len(expected) else 0.3)
        if message is None:
            break
        received.append(message)
    time.sleep(0.3)
    envelopes = fixture['mirror'].drain()
    if not expected:
        contract.require(raw_value == prepared['store'], 'notification.store-unchanged')
        contract.require(not received and not envelopes, 'notification.no-push')
        contract.require(fixture['raw'].get(create.cache_key(ids)) == prepared['cache'], 'notification.cache-unchanged')
        return
    created_broadcast = any(spec['model'] == 'notificationsbroadcast' for spec in effect['audit'])
    previous = [] if created_broadcast or prepared['store'] is None else json.loads(prepared['store'])['messages']
    stored = json.loads(raw_value)
    contract.require(isinstance(stored, dict) and list(stored) == ['messages'] and isinstance(stored['messages'], list)
                     and len(stored['messages']) == len(previous) + len(expected), 'notification.store-shape')
    items = stored['messages'][len(previous):]
    expected_items, pushes = [], []
    for item, notification in zip(items, expected):
        identifier = item.get('id')
        contract.require(isinstance(identifier, str) and uuid.UUID(identifier).version == 1 and str(uuid.UUID(identifier)) == identifier,
                         'notification.id')
        expires = item.get('expires')
        contract.require(type(expires) is int and int(since) - 1 + TTL <= expires <= int(until) + 1 + TTL, 'notification.expires')
        expected_items.append({'id': identifier, 'type': 'subscription', 'msg': notification['msg'],
                               'association_id': notification['association'], 'users': [], 'expires': expires})
        issued = expires - TTL
        pushes.append({'id': identifier, 'type': 'subscription', 'read': False, 'msg': notification['msg'],
                       'association_id': notification['association'], 'issued': issued, 'date': time.ctime(issued)})
    contract.require(raw_value == json.dumps({'messages': previous + expected_items}).encode(), 'notification.store-bytes')
    cached = fixture['raw'].get(create.cache_key(ids))
    contract.require(cached is not None and pickle.loads(cached) == [create.store_key(ids), 'all_notifications']
                     and 0 < fixture['raw'].ttl(create.cache_key(ids)) <= 300, 'notification.cache')
    if prepared['cache'] is not None:
        contract.require(cached == prepared['cache'], 'notification.cache-kept')
    contract.require(len(received) == len(pushes), f'notification.push-count:{len(received)}')
    for message, pushed in zip(received, pushes):
        contract.require(message == {'type': 'notification_push', 'notification': pushed, '__asgi_channel__': message.get('__asgi_channel__')}
                         or message == {'type': 'notification_push', 'notification': pushed}, 'notification.push')
    contract.require(len(envelopes) == len(pushes), f'notification.mirror-count:{len(envelopes)}')
    for envelope, pushed in zip(envelopes, pushes):
        contract.require(envelope == json.dumps({'version': 1, 'group': create.group_name(ids),
                                                 'message': {'type': 'notification_push', 'notification': pushed}}).encode(),
                         'notification.mirror')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    prepared = context['prepared']
    since, until = prepared['started'], time.time()
    effect = case['effect']
    token = fixture['tokens'].get(case['actor'])
    binding = {'$authorization': f'Bearer {token}' if token else None, '$category': STATE['category'], '$owner': ids['owner']}
    try:
        if effect is None:
            harness.celery_published(contract, fixture, [], since=since, until=until)
            contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        else:
            check_rows(effect, before, after, ids, since, until, binding)
            body = contract.LAST.get('body')
            subscriptions = [spec for spec in effect['new'] if spec['label'] == 'subscription']
            if isinstance(body, dict) and 'payment_id' in body:
                contract.require(len(subscriptions) == 1 and body['payment_id'] == resolve(subscriptions[0]['columns']['payment_id'], binding),
                                 'response.payment-bound')
            celery = [{**task, 'args': resolve(task['args'], binding),
                       'kwargs': [[k, {'uuid': resolve(v['uuid'], binding)} if isinstance(v, dict) else resolve(v, binding)]
                                  for k, v in task['kwargs']]} for task in effect['celery']]
            harness.celery_published(contract, fixture, celery, since=since, until=until)
            check_audit(case, effect, before, after, fixture, ids, context, binding)
    finally:
        check_notifications(effect, fixture, ids, since, until, prepared)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """run_workload_for with the case's setup applied before its state snapshot."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    STATE['source'] = source
    ids = source['ids']
    fixture['raw'] = raw
    results, context = [], {'remote': {}, 'fixture': fixture}
    for case in source['cases']:
        if case.get('setup'):
            apply_settings(ids, case['setup'])
        context['deletes'] = prepare_deletes(case)
        before = scopes.snapshot_state(apps)
        context['prepared'] = {'started': time.time(), 'store': fixture['store'].get(create.store_key(ids)),
                               'cache': raw.get(create.cache_key(ids))}
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
    raw.delete(create.cache_key(ids))
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
