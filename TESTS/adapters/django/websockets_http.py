"""Parent-only WebSocket consumer runner; imports are inert.

Django serves Channels' complete ASGI application (``core.asgi``) with the
Channels Redis layer on a private Redis database, so group messages produced
in this process reach Django sockets through Channels and Go sockets through
the layer's mirrored pub/sub envelope. The shared identity fixture is extended
with two unusable accounts, notification broadcasts and subscriptions; the
Redis notification store (database 0) is seeded per endpoint. Every case must
leave the complete model state unchanged, the store equal to the authored
value and exactly the authored ``user_broadcasts`` cache entries.
"""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from TESTS.contracts import test_websockets_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes

SOURCES = ('TESTS/fixtures/websockets_http.json', 'TESTS/contracts/test_websockets_http.py',
           'TESTS/adapters/django/websockets_http.py', 'TESTS/ws_client.py',
           'BE/core/asgi.py', 'BE/application/routing.py', 'BE/application/consumers.py', 'BE/application/health_consumer.py',
           'BE/notifications/routing.py', 'BE/notifications/consumers.py', 'BE/notifications/middleware.py',
           'BE/notifications/services.py', 'BE/notifications/utils.py', 'BE/notifications/models.py',
           'BE/notifications/realtime_layer.py', 'BE/notifications/go_realtime.py', 'BE/communications/staff_board.py',
           'BE/application/impersonation.py') + harness.SOURCES + tuple(
    # The Go implementation, once it exists (a RED run predates it).
    path for path in ('BE_GO/internal/wsproto/conn.go', 'BE_GO/internal/wsproto/handshake.go',
                      'BE_GO/internal/handler/websocket.go', 'BE_GO/internal/handler/websocket_consumers.go',
                      'BE_GO/internal/service/websocket_identity.go', 'BE_GO/internal/service/realtime_hub.go',
                      'BE_GO/internal/service/notification_consumer.go', 'BE_GO/cmd/server/main.go',
                      'BE_GO/internal/repository/notification_store.go', 'BE_GO/internal/dto/websocket_json.go')
    if (Path(__file__).resolve().parents[3] / path).is_file())
CHANNELS_DB = 2
STORE_DB = 0
CACHE_PATTERN = b':1:user_broadcasts:*'


def asgi_application():
    from core.asgi import application
    return application


@contextmanager
def service_context(report, log):
    """Channels Redis layer on a private database for both servers' lifetime."""
    import os
    from django.test.utils import override_settings
    layers = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer', 'CONFIG': {'hosts': [{
        'address': f'redis://127.0.0.1:{os.environ["REDIS_PORT"]}/{CHANNELS_DB}', 'password': os.environ['REDIS_PASSWORD']}]}}}
    with override_settings(CHANNEL_LAYERS=layers):
        yield


def redis_db(raw, db):
    import redis
    options = raw.connection_pool.connection_kwargs
    return redis.Redis(host=options['host'], port=options['port'], db=db, password=options.get('password'),
                       decode_responses=False, socket_timeout=5, socket_connect_timeout=5)


def seed_rows(source, created):
    from application.models import User
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast
    ids = source['ids']
    for label, role in (('inactive', User.ASSOCIATION), ('deleted', User.ASSOCIATION)):
        contract.require(not User._base_manager.filter(pk=ids[label]).exists(), 'fixture.collision')
        row = User(pk=ids[label], username=f'websockets-{label}', email=f'websockets-{label}@example.test', role=role,
                   password='!synthetic', is_active=label != 'inactive', deleted=label == 'deleted')
        User._base_manager.bulk_create([row])
        created.append((User, row.pk))
    broadcasts = {}
    for name in source['store']:
        row = NotificationsBroadcast._base_manager.filter(broadcast_name=name).first()
        if row is None:
            row = NotificationsBroadcast(broadcast_name=name)
            NotificationsBroadcast._base_manager.bulk_create([row])
            created.append((NotificationsBroadcast, row.pk))
        broadcasts[name] = row
    rows = [UserNotificationsBroadcast(user_id=ids[user], broadcast=broadcasts[name]) for user, name in source['subscriptions']]
    UserNotificationsBroadcast._base_manager.bulk_create(rows)
    created.extend((UserNotificationsBroadcast, row.pk) for row in rows)


_identity_context = harness.fixture_context_for(contract, seed_rows)


def ws_tokens(fixture, ids):
    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

    def mint(user_id, kind=AccessToken, **claims):
        token = kind()
        token['user_id'] = user_id
        token['iss'], token['aud'] = 'https://bakney.com', 'bakney'
        for name, value in claims.items():
            token[name] = value
        return token

    tokens = {label: str(mint(ids[label])) for label in (
        'owner', 'full_collaborator', 'restricted_collaborator', 'athlete', 'admin', 'foreign_owner',
        'owner_without_association', 'inactive', 'deleted')}
    expired = mint(ids['owner'])
    expired.set_exp(lifetime=-timedelta(minutes=5))
    tokens.update({'owner/plain': fixture['tokens']['owner'], 'owner/wrong-iss': str(mint(ids['owner'], iss='https://example.test')),
                   'owner/list-aud': str(mint(ids['owner'], aud=['bakney'])), 'owner/refresh': str(mint(ids['owner'], RefreshToken)),
                   'owner/expired': str(expired), 'garbage': 'not-a-jwt'})
    return tokens


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from notifications.services import NotificationService
    store, channels = redis_db(raw, STORE_DB), redis_db(raw, CHANNELS_DB)
    original = {key: (store.dump(key), store.pttl(key)) for key in store.keys('*')}
    channels_before = set(channels.keys('*'))
    source = contract.load_source()
    try:
        with _identity_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
            # The process-wide manager bootstraps its default broadcast once; do it
            # before any endpoint so neither server performs that write.
            NotificationService.get_manager()
            for name, value in source['store'].items():
                store.set(name, value.encode())
            fixture['ws_tokens'] = ws_tokens(fixture, source['ids'])
            fixture['outbox'] = []
            yield fixture
            for identifier in fixture['outbox']:
                delete_outbox(identifier)
    finally:
        for key in set(store.keys('*')) - set(original):
            store.delete(key)
        for key, (value, ttl) in original.items():
            store.restore(key, max(ttl, 0), value, replace=True)
        for key in set(channels.keys('*')) - channels_before:
            channels.delete(key)
        store.close()
        channels.close()


def delete_outbox(identifier):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('DELETE FROM public.assozeta_go_realtime_events WHERE id = %s', [identifier])


def producer(fixture, source):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django.core.cache import cache
    from django.db import connection
    from application.impersonation import SESSION_TTL, session_key
    from application.models import User
    from communications.staff_board import publish_staff_board_change
    from notifications.go_realtime import relay_once
    ids = source['ids']

    def session_record(label):
        actor, target, boundary = scopes.SESSION_SPECS[label]
        return {'actor_id': ids[actor], 'target_id': ids[target], 'association_id': ids[boundary] if boundary else None}

    def produce(action):
        if 'group_send' in action:
            async_to_sync(get_channel_layer().group_send)(action['group_send'], action['message'])
        elif 'staff_board' in action:
            publish_staff_board_change(action['staff_board'])
        elif 'relay' in action:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO public.assozeta_go_realtime_events(envelope_version, kind, target_id) '
                               'VALUES (1, %s, %s) RETURNING id', [action['relay']['kind'], action['relay']['target']])
                fixture['outbox'].append(cursor.fetchone()[0])
            contract.require(relay_once() == {'delivered': 1, 'retry': 0, 'failed': 0}, 'produce.relay')
        elif 'revoke_session' in action:
            cache.delete(session_key(fixture['sessions'][action['revoke_session']]))
        elif 'restore_session' in action:
            label = action['restore_session']
            cache.set(session_key(fixture['sessions'][label]), session_record(label), SESSION_TTL)
        elif 'set_permissions' in action:
            User._base_manager.filter(pk=ids[action['set_permissions']]).update(collaborator_permissions=action['permissions'])
        else:
            raise contract.ContractFailure('produce.unknown')
    return produce


def substitution(fixture):
    import re

    def replace(match):
        kind, label = match.group(1), match.group(2)
        return fixture['ws_tokens'][label] if kind == 'token' else fixture['sessions'][label]
    return lambda text: re.sub(r'\{(token|session):([\w/-]+)\}', replace, text)


def broadcast_cache(raw):
    entries = {}
    for key in raw.keys(CACHE_PATTERN):
        ttl = raw.ttl(key)
        contract.require(0 < ttl <= 300, 'cache.ttl')
        entries[key.decode().rsplit(':', 1)[1]] = raw.get(key).hex()
    return entries


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    import pickle
    from django.core.cache import cache
    source = contract.load_source()
    store = redis_db(raw, STORE_DB)
    produce, substitute = producer(fixture, source), substitution(fixture)
    harness.carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results = []
    try:
        for case in source['cases']:
            try:
                for key in raw.keys(CACHE_PATTERN):
                    raw.delete(key)
                for user, value in (case.get('cache_seed') or {}).items():
                    cache.set(f'user_broadcasts:{source["ids"][user]}', value, 300)
                for action in (case.get('request') or {}).get('before', []):
                    produce(action)
                cursor = observer.cursor()
                log = contract.run_case(case, base_url, substitute=substitute, produce=produce)
                contract.require(observer.delta(cursor) == [], 'attribution.http-requests')
                harness.carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
                stored = {name: (store.get(name) or b'').decode() for name in source['store']}
                contract.require(stored == case['store'], 'store.values')
                cached = broadcast_cache(raw)
                contract.require(cached == case['cache'], f'cache.user-broadcasts:{sorted(cached)}')
                contract.require(all(pickle.loads(bytes.fromhex(value)) is not None for value in cached.values()), 'cache.decodable')
            except BaseException as error:
                error.case = f'{label}:{case["name"]}'
                raise
            results.append({'case': case['name'], 'events': len(log)})
    finally:
        for key in raw.keys(CACHE_PATTERN):
            raw.delete(key)
        store.close()
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
