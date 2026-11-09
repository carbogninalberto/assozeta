"""Shared checks of the "mark payment as paid" flow; imports are inert.

payment_approve (unpaid income payment with an associate, supplier or
instructor) and mark_payment_as_paid (unpaid payment without an invoice)
create an invoice, mark course subscriptions paid and send a notification
inside their transaction (NotificationService.send_notification). This module
checks, for one case:
- at most one created invoice with the authored stored values (its key is the
  response's '$invoice' when the response renders it);
- the broadcast rows validate_broadcast creates on the first notification of
  a user (both uuid4, bound for the audit check);
- the Redis DB 0 broadcast_<user> list (one appended item: uuid1 id, type
  'payment', the authored message and association, expiry 30 days after the
  request), the user_broadcasts cache entry, the notification_push a Channels
  consumer of notifications_user_<user> receives and the mirrored envelope;
  every other probed user's list, cache and channel stay untouched.
The cache entries are removed after every case (each notification reads the
broadcasts from the database), and the lists are restored after the run.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import pickle
import time
import uuid

from TESTS.adapters.django import subscription_transfer_http as transfer

SOURCES = ('TESTS/adapters/django/payment_paid_flow.py', 'TESTS/adapters/django/subscription_transfer_http.py',
           'BE/notifications/services.py', 'BE/notifications/utils.py', 'BE/notifications/models.py', 'BE/notifications/realtime_layer.py',
           'BE/application/utils/notification_utils.py', 'BE/application/services/invoice_service.py',
           'BE/application/utils/payments_utils.py', 'BE/application/printing_tasks.py', 'BE/core/celery.py')
TTL = 60 * 60 * 24 * 30
BROADCAST, USER_BROADCAST = 'notifications.NotificationsBroadcast', 'notifications.UserNotificationsBroadcast'
INVOICE = 'application.Invoice'
MANAGED = (INVOICE, BROADCAST, USER_BROADCAST)


@contextmanager
def channel_layers():
    """Django's channel layer on the run's Redis database 0, where the Go server writes."""
    from django.test.utils import override_settings
    layers = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer', 'CONFIG': {'hosts': [{
        'address': f'redis://127.0.0.1:{os.environ["REDIS_PORT"]}/0', 'password': os.environ['REDIS_PASSWORD']}]}}}
    with override_settings(CHANNEL_LAYERS=layers):
        yield


def probe(label):
    return f'payment-paid-flow-probe-{label}'


def with_notifications(fixture_context, recipients):
    """Wrap a fixture_context: recipients(source) -> {label: user_id} are probed."""
    @contextmanager
    def wrapped(apps, private_work, raw, *, cleanup_receipt=None):
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from notifications.services import NotificationService
        store = transfer.redis_db(raw, 0)
        mirror = layer = users = original = None
        # The manager's first use creates the 'all_notifications' broadcast: before the fixture's snapshots.
        NotificationService.get_manager()
        try:
            with fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
                users = recipients(fixture)
                original = {user: (store.dump(transfer.store_key(user)), store.pttl(transfer.store_key(user))) for user in users.values()}
                for user in users.values():
                    store.delete(transfer.store_key(user))
                    raw.delete(transfer.cache_key(user))
                layer = get_channel_layer()
                for label, user in users.items():
                    async_to_sync(layer.group_add)(transfer.group_name(user), probe(label))
                mirror = transfer.Mirror(store)
                time.sleep(0.2)
                fixture['notify'] = {'store': store, 'layer': layer, 'mirror': mirror, 'raw': raw, 'users': users}
                yield fixture
                for label, user in users.items():
                    async_to_sync(layer.group_discard)(transfer.group_name(user), probe(label))
        finally:
            if mirror is not None:
                mirror.close()
            for user, (dumped, ttl) in (original or {}).items():
                store.delete(transfer.store_key(user))
                if dumped is not None:
                    store.restore(transfer.store_key(user), max(ttl, 0), dumped, replace=True)
                raw.delete(transfer.cache_key(user))
            store.close()

    return wrapped


def prepare(fixture):
    notify = fixture['notify']
    return {'started': time.time(),
            'store': {label: notify['store'].get(transfer.store_key(user)) for label, user in notify['users'].items()}}


def _plain(value):
    return transfer.plain(value)


def _rows(state, label, key):
    return {str(_plain(row[key])): _plain(row) for row in state[label]}


def check_broadcasts(contract, before, after, notification, created, binding):
    """validate_broadcast's rows: both new (uuid4) exactly when `created`."""
    broadcasts = [r for k, r in _rows(after, BROADCAST, 'broadcast_id').items() if k not in _rows(before, BROADCAST, 'broadcast_id')]
    subscribed = [r for k, r in _rows(after, USER_BROADCAST, 'user_broadcast_id').items()
                  if k not in _rows(before, USER_BROADCAST, 'user_broadcast_id')]
    contract.require(_rows(before, BROADCAST, 'broadcast_id').items() <= _rows(after, BROADCAST, 'broadcast_id').items()
                     and _rows(before, USER_BROADCAST, 'user_broadcast_id').items() <= _rows(after, USER_BROADCAST, 'user_broadcast_id').items(),
                     'state.broadcast-kept')
    if not created:
        contract.require(not broadcasts and not subscribed, 'state.broadcast-created')
        return
    user = notification['user_id']
    contract.require(len(broadcasts) == 1 and broadcasts[0]['broadcast_name'] == transfer.store_key(user)
                     and transfer.uuid4_text(broadcasts[0]['broadcast_id']), 'state.broadcast')
    contract.require(len(subscribed) == 1 and subscribed[0]['user_id'] == user and subscribed[0]['broadcast_id'] == broadcasts[0]['broadcast_id']
                     and transfer.uuid4_text(subscribed[0]['user_broadcast_id']), 'state.user-broadcast')
    binding['$notificationsbroadcast'] = broadcasts[0]['broadcast_id']
    binding['$usernotificationsbroadcast'] = subscribed[0]['user_broadcast_id']


def check_invoice(contract, before, after, expected, response_invoices, binding):
    """At most one created invoice; its stored columns are the authored ones
    ('$now' creation dates must be recent and are returned for the audit check)."""
    old, new = _rows(before, INVOICE, 'invoice_id'), _rows(after, INVOICE, 'invoice_id')
    fresh = sorted(set(new) - set(old))
    contract.require(set(old) <= set(new) and all(new[k] == v for k, v in old.items()), 'state.invoice-kept')
    contract.require(len(fresh) == (1 if expected else 0), 'state.invoice-count')
    if not expected:
        return None
    key = fresh[0]
    contract.require(transfer.uuid4_text(key) and set(response_invoices) <= {key}, 'response.invoice-id')
    binding['$invoice'] = key
    stored = new[key]
    full = {'invoice_id': key, 'document_pdf_id': None, 'archived': False, 'cancelled': False, 'selected_tutor_id': None, 'meta': None,
            'imported_from_associami': False, 'deleted': False, **expected}
    contract.require(set(stored) == set(full), 'state.invoice-columns')
    created = None
    for column, value in full.items():
        actual = stored[column]
        if column == 'creation_date':
            moment = datetime.fromisoformat(actual.replace('Z', '+00:00')) if isinstance(actual, str) else actual
            if value == '$now':
                contract.require(abs(datetime.now(timezone.utc) - moment).total_seconds() < 600, 'state.invoice-now')
                created = str(moment.astimezone(timezone.utc).replace(tzinfo=None))
            else:
                contract.require(moment == datetime.fromisoformat(value.replace('Z', '+00:00')), 'state.invoice-date')
        else:
            contract.require(actual == value, 'state.invoice-column:' + column)
    return created


def check_notification(contract, notification, fixture, prepared, created, until):
    """The Redis list, cache, Channels push and mirror of one case (notification None: nothing)."""
    notify = fixture['notify']
    store, layer, raw = notify['store'], notify['layer'], notify['raw']
    since = prepared['started']
    received = {}
    for label in notify['users']:
        received[label] = []
        while True:
            expect = notification is not None and notification['user'] == label and not received[label]
            message = transfer.channel_receive(layer, probe(label), 3 if expect else 0.3)
            if message is None:
                break
            received[label].append(message)
    time.sleep(0.3)
    envelopes = notify['mirror'].drain()
    try:
        for label, user in notify['users'].items():
            if notification is None or notification['user'] != label:
                contract.require(store.get(transfer.store_key(user)) == prepared['store'][label], 'notification.store-unchanged:' + label)
                contract.require(raw.get(transfer.cache_key(user)) is None, 'notification.cache-unchanged:' + label)
                contract.require(not received[label], 'notification.no-push:' + label)
        if notification is None:
            contract.require(not envelopes, 'notification.no-mirror')
            return
        label = notification['user']
        user = notify['users'][label]
        raw_value = store.get(transfer.store_key(user))
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
        expected_item = {'id': identifier, 'type': 'payment', 'msg': notification['msg'], 'association_id': notification['association'],
                         'users': [], 'expires': expires}
        contract.require(raw_value == json.dumps({'messages': previous + [expected_item]}).encode(), 'notification.store-bytes')
        cached = raw.get(transfer.cache_key(user))
        contract.require(cached is not None and pickle.loads(cached) == [transfer.store_key(user), 'all_notifications']
                         and 0 < raw.ttl(transfer.cache_key(user)) <= 300, 'notification.cache')
        issued = expires - TTL
        pushed = {'id': identifier, 'type': 'payment', 'read': False, 'msg': notification['msg'], 'association_id': notification['association'],
                  'issued': issued, 'date': time.ctime(issued)}
        contract.require(len(received[label]) == 1, f'notification.push-count:{len(received[label])}')
        message = received[label][0]
        contract.require(message == {'type': 'notification_push', 'notification': pushed, '__asgi_channel__': message.get('__asgi_channel__')}
                         or message == {'type': 'notification_push', 'notification': pushed}, 'notification.push')
        contract.require(len(envelopes) == 1, f'notification.mirror-count:{len(envelopes)}')
        contract.require(envelopes[0] == json.dumps({'version': 1, 'group': transfer.group_name(user),
                                                     'message': {'type': 'notification_push', 'notification': pushed}}).encode(),
                         'notification.mirror')
    finally:
        for user in notify['users'].values():
            raw.delete(transfer.cache_key(user))


def broadcast_changes(model, pk):
    """auditlog's create diff of a broadcast row."""
    from auditlog.diff import model_instance_diff
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast
    cls = {'notificationsbroadcast': NotificationsBroadcast, 'usernotificationsbroadcast': UserNotificationsBroadcast}[model]
    instance = cls._base_manager.get(pk=pk)
    return {k: list(v) for k, v in model_instance_diff(None, instance).items()}, str(instance)
