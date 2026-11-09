"""Author the WebSocket consumer sessions (websockets.md).

Django serves ``ws/health/``, ``ws/updates/`` and ``ws/notifications/`` through
``JWTAuthMiddleware`` and Channels' ``URLRouter`` under uvicorn 0.35 with the
websockets 14 legacy protocol. These sessions pin, in Django's order:
- the uvicorn/websockets handshake: request checks (400 bodies), a consumer
  close before accept (HTTP 403 with an empty body), an exception before accept
  (uvicorn's 500), no subprotocol selection and permessage-deflate negotiation;
- the middleware: token from ``?token=`` (first non-blank value) else the
  ``BKN_AUTH`` cookie, an access token whose ``iss``/``aud`` equal the issued
  values, an active non-deleted user, and ``?impersonation=`` sessions resolved
  through ``resolve_target`` (4003 before accept on failure, then rechecked on
  every received frame and every send);
- frames: Python ``json.dumps`` text, close echo, ping/pong, fragmentation,
  1007/1002 failures, and an application exception (TCP closed without a close
  frame);
- the consumers' message handling and the Redis notification store (JSON in
  Redis database 0, Django's ``json.dumps`` writes, ``user_broadcasts`` cache);
- group events produced by Django code (``group_send``, staff-board publication
  and the Go outbox relay) and the consumer filters applied on delivery.
Run with ``python3 -I``.
"""
import json
import pickle
import time
from pathlib import Path

OUTPUT = Path(__file__).with_name('websockets_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FULL, RESTRICTED, ATHLETE = IDS['owner'], IDS['full_collaborator'], IDS['restricted_collaborator'], IDS['athlete']
ADMIN = 'a1000000-0000-4000-8000-000000710001'
ids = iter(f'a1000000-0000-4000-8000-{760000 + n:012d}' for n in range(1, 50))
INACTIVE, DELETED = next(ids), next(ids)
TTL = 60 * 60 * 24 * 30
OWN_BROADCAST = f'broadcast_{OWNER}'
CLUB, BROKEN = 'ws-club-news', 'ws-broken-feed'
VERSION = 'v0.0.0'
cases = []


def dumps(value):
    return json.dumps(value)


def ctime(seconds):
    # time.ctime() in Django's process zone (TIME_ZONE = 'UTC').
    t = time.gmtime(seconds)
    days, months = 'Mon Tue Wed Thu Fri Sat Sun'.split(), 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
    return f'{days[t.tm_wday]} {months[t.tm_mon - 1]}{t.tm_mday:3d} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} {t.tm_year}'


def note(identifier, msg, issued, association, users=(), kind='info', *, legacy=False):
    value = {'id': identifier, 'type': kind, 'msg': msg, 'association_id': association, 'users': list(users),
             'expires': issued + TTL}
    if legacy:
        del value['type'], value['association_id']
    return value


N = {
    'own1': note('a1000000-0000-4000-8000-000000761001', 'Iscrizione aggiornata', 1767225600, OWN),
    'own2': note('a1000000-0000-4000-8000-000000761002', 'Pagamento registrato è ok ✓ \U0001f600', 1767225600, OWN, [OWNER], 'success'),
    'foreign': note('a1000000-0000-4000-8000-000000761003', 'Altra associazione', 1767312000, FOREIGN),
    'legacy': note('a1000000-0000-4000-8000-000000761004', 'Avviso storico', 1767139200, None, legacy=True),
    'club_own': note('a1000000-0000-4000-8000-000000761005', 'Club news own', 1767484800, OWN, [], 'warning'),
    'club_foreign': note('a1000000-0000-4000-8000-000000761006', 'Club news foreign', 1767571200, FOREIGN),
    'global': note('a1000000-0000-4000-8000-000000761007', 'Manutenzione programmata', 1767398400, None),
}
STORE = {
    OWN_BROADCAST: {'messages': [N['own1'], N['own2'], N['foreign'], N['legacy']]},
    CLUB: {'messages': [N['club_own'], N['club_foreign']]},
    'all_notifications': {'messages': [N['global']]},
}
# Raw store values; the broken feed is not JSON.
store = {name: dumps(value) for name, value in STORE.items()}
store[BROKEN] = 'not json'
# Seeded subscriptions, in insertion order (the broadcasts query has no ORDER BY).
SUBSCRIPTIONS = [('owner', OWN_BROADCAST), ('owner', CLUB), ('full_collaborator', CLUB), ('athlete', BROKEN)]
USERS = {'owner': OWNER, 'full_collaborator': FULL, 'restricted_collaborator': RESTRICTED, 'athlete': ATHLETE,
         'admin': ADMIN, 'foreign_owner': IDS['foreign_owner'], 'owner_without_association': IDS['owner_without_association']}


def broadcasts(user):
    return [name for label, name in SUBSCRIPTIONS if label == user] + ['all_notifications']


def parse(name):
    raw = store[name]
    value = json.loads(raw)
    return [] if value['messages'] == '' else value['messages']


def built(item, user_id):
    issued = item['expires'] - TTL
    return {'id': item['id'], 'type': item.get('type', 'info'), 'read': user_id in item['users'], 'msg': item['msg'],
            'association_id': item.get('association_id'), 'issued': issued, 'date': ctime(issued)}


def listing(user):
    user_id = USERS[user]
    results = [built(item, user_id) for name in broadcasts(user) for item in parse(name)]
    results.sort(key=lambda item: item['issued'], reverse=True)
    return results


def visible(item, scope):
    return not scope or str(item.get('association_id')) == str(scope)


def fetch_frame(user, scope=None):
    results = [item for item in listing(user) if visible(item, scope)]
    unread = sum(not item['read'] for item in results)
    return dumps({'type': 'notifications', 'data': results, 'unread': unread})


def mark(user, identifier, scope=None):
    user_id = USERS[user]
    if scope and not any(str(item['id']) == str(identifier) and visible(item, scope) for item in listing(user)):
        return
    for name in broadcasts(user):
        items = parse(name)
        for item in items:
            if user_id not in item['users'] and item['id'] == identifier:
                item['users'].append(user_id)
                store[name] = dumps({'messages': items})
                break


def mark_all(user, scope=None):
    user_id = USERS[user]
    if scope:
        for item in listing(user):
            if visible(item, scope):
                mark(user, item['id'])
        return
    for name in broadcasts(user):
        items = parse(name)
        changed = False
        for item in items:
            if user_id not in item['users']:
                item['users'].append(user_id)
                changed = True
        if changed:
            store[name] = dumps({'messages': items})


def cache_value(users):
    return {USERS[user]: pickle.dumps(broadcasts(user), 4).hex() for user in users}


def text(value):
    return {'expect': {'text': value if isinstance(value, str) else dumps(value)}}


def send(value):
    return {'send_text': value if isinstance(value, str) else dumps(value)}


ACCEPTED = {'status': 101, 'protocol': None, 'extensions': None}
FORBIDDEN = {'status': 403, 'reason': 'Forbidden', 'body': '', 'content_type': 'text/plain', 'content_length': '0'}
SERVER = {'status': 500, 'reason': 'Internal Server Error', 'body': 'Internal Server Error',
          'content_type': 'text/plain; charset=utf-8', 'content_length': '21'}
CLOSE_ECHO = [{'expect': {'close': 1000, 'reason': ''}}, {'expect': {'eof': True}}]
CRASH = [{'expect': {'eof': True}}]
HEALTH = {'type': 'health', 'status': 'ok', 'msg': 'I am good', 'version': VERSION}


def bad_request(message):
    body = f'Failed to open a WebSocket connection: {message}.\n'
    return {'status': 400, 'reason': 'Bad Request', 'body': body, 'content_type': 'text/plain', 'content_length': str(len(body))}


def add(name, path, handshake, steps=(), *, query='', headers=(), request=None, cache_users=(), cache_seed=None):
    case = {'name': f'websockets-{name}', 'path': path, 'query': query, 'headers': [list(item) for item in headers],
            'request': request or {}, 'handshake': handshake, 'steps': list(steps),
            'store': dict(store), 'cache': cache_value(cache_users)}
    if cache_seed is not None:
        case['cache_seed'] = cache_seed
        case['cache'] = {**{USERS[user]: pickle.dumps(value, 4).hex() for user, value in cache_seed.items()}, **case['cache']}
    cases.append(case)


def finish(close=1000):
    return [{'close': close}, *CLOSE_ECHO] if close == 1000 else [{'close': close}, {'expect': {'close': close, 'reason': ''}}, {'expect': {'eof': True}}]


# ---------------------------------------------------------------------------
# Handshake (uvicorn + websockets legacy), shared by every path
# ---------------------------------------------------------------------------
add('health-connect', '/ws/health/', ACCEPTED, [text(HEALTH), *finish()])
add('health-subprotocol-not-selected', '/ws/health/', ACCEPTED, [text(HEALTH), *finish()],
    headers=[('Sec-WebSocket-Protocol', 'chat, superchat')])
add('health-deflate-negotiated', '/ws/health/', {**ACCEPTED, 'extensions': 'permessage-deflate'},
    [text(HEALTH), {'send_text': dumps({'type': 'ping', 'timestamp': 'zipped'}), 'compress': True},
     text({'type': 'pong', 'timestamp': 'zipped'}), {'send_text': dumps({'type': 'check'}), 'compress': True}, text(HEALTH), *finish()],
    headers=[('Sec-WebSocket-Extensions', 'permessage-deflate; client_max_window_bits')])
add('health-deflate-parameters', '/ws/health/',
    {**ACCEPTED, 'extensions': 'permessage-deflate; server_no_context_takeover; client_no_context_takeover; server_max_window_bits=10'},
    [text(HEALTH), {'send_text': dumps({'type': 'check'}), 'compress': True}, text(HEALTH),
     {'send_text': dumps({'type': 'check'}), 'compress': True}, text(HEALTH), *finish()],
    headers=[('Sec-WebSocket-Extensions', 'x-unknown, permessage-deflate; server_max_window_bits=10; server_no_context_takeover; client_no_context_takeover')])
add('handshake-missing-key', '/ws/health/', bad_request("missing Sec-WebSocket-Key header; 'sec-websocket-key'"), request={'omit': ['Sec-WebSocket-Key']})
add('handshake-short-key', '/ws/health/', bad_request('invalid Sec-WebSocket-Key header: c2hvcnQ='), request={'key': 'c2hvcnQ='})
add('handshake-version', '/ws/health/', bad_request('invalid Sec-WebSocket-Version header: 8'), request={'version': '8'})
add('handshake-missing-version', '/ws/health/', bad_request("missing Sec-WebSocket-Version header; 'sec-websocket-version'"), request={'omit': ['Sec-WebSocket-Version']})
add('handshake-duplicate-key', '/ws/notifications/', bad_request("invalid Sec-WebSocket-Key header: multiple values; 'Sec-WebSocket-Key'"),
    headers=[('Sec-WebSocket-Key', 'YXNzb3pldGEtd3MtdGVzdA==')], query='token={token:owner}')
# URLRouter raises before accept for an unrouted path: uvicorn's plain 500.
add('unrouted-path', '/ws/nope/', SERVER)
add('unrouted-without-slash', '/ws/health', SERVER)
add('unrouted-similar-path', '/ws/healthz/', SERVER, query='token={token:owner}')
# The middleware runs before routing: a failed impersonation closes first.
add('unrouted-impersonation-denied', '/ws/nope/', FORBIDDEN, query='token={token:owner}&impersonation={session:forged}')

# ---------------------------------------------------------------------------
# Frames on the health consumer (AsyncJsonWebsocketConsumer)
# ---------------------------------------------------------------------------
add('health-ping-pong', '/ws/health/', ACCEPTED, [
    text(HEALTH),
    send({'type': 'ping', 'timestamp': 1730000000123}), text({'type': 'pong', 'timestamp': 1730000000123}),
    send('{"type": "ping"}'), text({'type': 'pong', 'timestamp': None}),
    send('{"timestamp": 1.5e3, "type": "ping", "type": "ping"}'), text('{"type": "pong", "timestamp": 1500.0}'),
    send('{"type":"ping","timestamp":{"b":[1,2.50,"\\u00e8\\ud83d\\ude00",true,null],"a":-0}}'),
    text('{"type": "pong", "timestamp": {"b": [1, 2.5, "\\u00e8\\ud83d\\ude00", true, null], "a": 0}}'),
    send('{"type": "ping", "timestamp": "\\ud800"}'), text('{"type": "pong", "timestamp": "\\ud800"}'),
    send({'type': 'check'}), text(HEALTH),
    *finish()])
add('health-unknown-types', '/ws/health/', ACCEPTED, [
    text(HEALTH),
    send({'type': 'status'}), text({'type': 'error', 'message': 'Unknown message type: status'}),
    send({}), text({'type': 'error', 'message': 'Unknown message type: None'}),
    send({'type': 5}), text({'type': 'error', 'message': 'Unknown message type: 5'}),
    send({'type': 2.0}), text({'type': 'error', 'message': 'Unknown message type: 2.0'}),
    send({'type': True}), text({'type': 'error', 'message': 'Unknown message type: True'}),
    send('{"type": [1, "a", null, {"k": "it\'s"}]}'),
    text({'type': 'error', 'message': "Unknown message type: [1, 'a', None, {'k': \"it's\"}]"}),
    send({'type': 'PING'}), text({'type': 'error', 'message': 'Unknown message type: PING'}),
    *finish()])
add('health-control-frames', '/ws/health/', ACCEPTED, [
    text(HEALTH),
    {'ping': '01020304'}, {'expect': {'pong': '01020304'}},
    {'frames': [{'opcode': 1, 'payload': '{"type": "pi', 'fin': False}, {'opcode': 9, 'payload_hex': 'aa'},
                {'opcode': 0, 'payload': 'ng", "timestamp": 7}', 'fin': True}]},
    {'expect': {'pong': 'aa'}}, text({'type': 'pong', 'timestamp': 7}),
    {'frames': [{'opcode': 10, 'payload_hex': 'bb'}]},
    send({'type': 'check'}), text(HEALTH),
    *finish()])
add('health-close-reason-echo', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'close': 4000, 'reason': 'bye now'}, {'expect': {'close': 4000, 'reason': 'bye now'}}, {'expect': {'eof': True}}])
add('health-close-empty-echo', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'close_raw': ''}, {'expect': {'close': None, 'reason': ''}}, {'expect': {'eof': True}}])
add('health-binary-crash', '/ws/health/', ACCEPTED, [text(HEALTH), {'send_binary': '7b7d'}, *CRASH])
add('health-invalid-json-crash', '/ws/health/', ACCEPTED, [text(HEALTH), send('{"type": '), *CRASH])
add('health-array-crash', '/ws/health/', ACCEPTED, [text(HEALTH), send('[{"type": "check"}]'), *CRASH])
add('health-empty-text-crash', '/ws/health/', ACCEPTED, [text(HEALTH), send(''), *CRASH])
add('health-nan-literal', '/ws/health/', ACCEPTED, [
    text(HEALTH), send('{"type": "ping", "timestamp": [NaN, Infinity, -Infinity]}'),
    text('{"type": "pong", "timestamp": [NaN, Infinity, -Infinity]}'), *finish()])
add('health-huge-integer-crash', '/ws/health/', ACCEPTED, [
    text(HEALTH), send('{"type": "ping", "timestamp": ' + '9' * 4301 + '}'), *CRASH])
add('health-invalid-utf8', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'frames': [{'opcode': 1, 'payload_hex': '7b22c328227d'}]},
    {'expect': {'close': 1007, 'reason': ''}}, {'expect': {'eof': True}}])
add('health-unmasked-frame', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'frames': [{'opcode': 1, 'payload': '{"type": "check"}', 'mask': False}]},
    {'expect': {'close': 1002, 'reason': ''}}, {'expect': {'eof': True}}])
add('health-oversized-frame', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'frames': [{'opcode': 1, 'payload': '', 'length': 16 * 1024 * 1024 + 1}]},
    {'expect': {'close': 1009, 'reason': ''}}, {'expect': {'eof': True}}])
add('health-blank-token-query', '/ws/health/', ACCEPTED, [text(HEALTH), *finish()], query='token=&impersonation=')

# ---------------------------------------------------------------------------
# Update consumer (AsyncWebsocketConsumer echo)
# ---------------------------------------------------------------------------
add('updates-echo', '/ws/updates/', ACCEPTED, [
    send({'message': 'ciao'}), text({'message': 'ciao'}),
    send('{"message": {"z": [1, 2.50, 1e400], "a": "\\u00e8"}, "extra": 1}'),
    text('{"message": {"z": [1, 2.5, Infinity], "a": "\\u00e8"}}'),
    send('{"message": null}'), text({'message': None}),
    *finish()])
add('updates-missing-message-crash', '/ws/updates/', ACCEPTED, [send({'msg': 'x'}), *CRASH])
add('updates-string-crash', '/ws/updates/', ACCEPTED, [send('"message"'), *CRASH])
add('updates-binary-crash', '/ws/updates/', ACCEPTED, [{'send_binary': '7b7d'}, *CRASH])
add('updates-anonymous-impersonation', '/ws/updates/', FORBIDDEN, query='impersonation={session:admin_owner}')

# ---------------------------------------------------------------------------
# Notification consumer: authentication before accept
# ---------------------------------------------------------------------------
for label, token in [('anonymous', None), ('token-without-issuer', 'owner/plain'), ('wrong-issuer', 'owner/wrong-iss'),
                     ('list-audience', 'owner/list-aud'), ('refresh-token', 'owner/refresh'), ('expired-token', 'owner/expired'),
                     ('garbage-token', 'garbage'), ('inactive-user', 'inactive'), ('deleted-user', 'deleted')]:
    add(f'notifications-{label}', '/ws/notifications/', FORBIDDEN, query='' if token is None else f'token={{token:{token}}}')
add('notifications-query-before-cookie', '/ws/notifications/', FORBIDDEN, query='token={token:garbage}',
    headers=[('Cookie', 'BKN_AUTH={token:owner}')])
for label in ('forged', 'expired', 'revoked', 'ineligible', 'association_mismatch', 'owner_owner'):
    add(f'notifications-session-{label.replace("_", "-")}', '/ws/notifications/', FORBIDDEN,
        query=f'token={{token:{"owner" if label.startswith(("association", "owner")) else "admin"}}}&impersonation={{session:{label}}}')
add('notifications-session-wrong-actor', '/ws/notifications/', FORBIDDEN, query='token={token:owner}&impersonation={session:admin_owner}')

# ---------------------------------------------------------------------------
# Notification consumer: accepted sessions, store reads and writes
# ---------------------------------------------------------------------------
add('notifications-cookie-fetch', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text(fetch_frame('owner')),
    send({'type': 'ping', 'timestamp': 'n'}), text({'type': 'pong', 'timestamp': 'n'}), *finish()],
    query='token=&other=1', headers=[('Cookie', 'theme=dark; BKN_AUTH={token:owner} ; BKN_AUTH=ignored')], cache_users=['owner'])
add('notifications-last-cookie-header', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text(fetch_frame('owner')), *finish()],
    headers=[('Cookie', 'BKN_AUTH={token:garbage}'), ('Cookie', 'BKN_AUTH={token:owner}')], cache_users=['owner'])
add('notifications-first-query-token', '/ws/notifications/', ACCEPTED, [*finish()],
    query='token={token:owner}&token={token:garbage}', cache_users=['owner'])
add('notifications-stale-broadcast-cache', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}),
    text(dumps({'type': 'notifications', 'data': sorted([built(item, OWNER) for item in parse(CLUB)], key=lambda x: x['issued'], reverse=True),
                'unread': 2})), *finish()],
    query='token={token:owner}', cache_seed={'owner': [CLUB]})
fetch_owner = fetch_frame('owner')
add('notifications-owner-messages', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text(fetch_owner),
    send({'type': 'mark_read'}), text({'type': 'error', 'message': "Missing 'id' for mark_read"}),
    send({'type': 'mark_read', 'id': 0}), text({'type': 'error', 'message': "Missing 'id' for mark_read"}),
    send({'type': 'mark_read', 'id': ''}), text({'type': 'error', 'message': "Missing 'id' for mark_read"}),
    send({'type': 'mark_read', 'id': 123}), text({'type': 'read_confirmed', 'id': 123}),
    send('{"type": "mark_read", "id": {"x": [1.0]}}'), text('{"type": "read_confirmed", "id": {"x": [1.0]}}'),
    send({'type': 'mark_read', 'id': IDS['missing']}), text({'type': 'read_confirmed', 'id': IDS['missing']}),
    send({'type': 'nope'}), text({'type': 'error', 'message': 'Unknown message type: nope'}),
    *finish()], query='token={token:owner}', cache_users=['owner'])
mark('owner', N['own1']['id'])
add('notifications-owner-mark-read', '/ws/notifications/', ACCEPTED, [
    send({'type': 'mark_read', 'id': N['own1']['id']}), text({'type': 'read_confirmed', 'id': N['own1']['id']}),
    send({'type': 'mark_read', 'id': N['own2']['id']}), text({'type': 'read_confirmed', 'id': N['own2']['id']}),
    send({'type': 'fetch'}), text(fetch_frame('owner')), *finish()], query='token={token:owner}', cache_users=['owner'])
mark_all('owner')
add('notifications-owner-mark-all', '/ws/notifications/', ACCEPTED, [
    send({'type': 'mark_all_read'}), text({'type': 'all_read_confirmed'}),
    send({'type': 'fetch'}), text(fetch_frame('owner')), *finish()], query='token={token:owner}', cache_users=['owner'])
add('notifications-athlete-store-errors', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text({'type': 'error', 'message': 'Internal server error'}),
    send({'type': 'mark_read', 'id': N['global']['id']}), text({'type': 'error', 'message': 'Internal server error'}),
    send({'type': 'mark_all_read'}), text({'type': 'all_read_confirmed'}),
    *finish()], query='token={token:athlete}', cache_users=['athlete'])
add('notifications-invalid-json-crash', '/ws/notifications/', ACCEPTED, [send('{'), *CRASH],
    query='token={token:owner}', cache_users=['owner'])
add('notifications-array-crash', '/ws/notifications/', ACCEPTED, [send('[]'), *CRASH],
    query='token={token:owner}', cache_users=['owner'])
add('notifications-binary-crash', '/ws/notifications/', ACCEPTED, [{'send_binary': '00'}, *CRASH],
    query='token={token:owner}', cache_users=['owner'])

# Impersonation scoped to the association (owner acting as the full collaborator).
scope_fetch = fetch_frame('full_collaborator', OWN)
mark('full_collaborator', N['club_own']['id'], OWN)
add('notifications-scoped-messages', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text(scope_fetch),
    send({'type': 'mark_read', 'id': N['club_foreign']['id']}), text({'type': 'read_confirmed', 'id': N['club_foreign']['id']}),
    send({'type': 'mark_read', 'id': N['club_own']['id']}), text({'type': 'read_confirmed', 'id': N['club_own']['id']}),
    send({'type': 'fetch'}), text(fetch_frame('full_collaborator', OWN)),
    *finish()], query='token={token:owner}&impersonation={session:owner_full}', cache_users=['full_collaborator'])
mark_all('full_collaborator', OWN)
add('notifications-scoped-mark-all', '/ws/notifications/', ACCEPTED, [
    send({'type': 'mark_all_read'}), text({'type': 'all_read_confirmed'}),
    send({'type': 'fetch'}), text(fetch_frame('full_collaborator', OWN)),
    *finish()], query='token={token:owner}&impersonation={session:owner_full}', cache_users=['full_collaborator'])
add('notifications-admin-session-unscoped', '/ws/notifications/', ACCEPTED, [
    send({'type': 'fetch'}), text(fetch_frame('owner')), *finish()],
    query='token={token:admin}&impersonation={session:admin_owner}', cache_users=['owner'])

# Rechecks on receive and send while impersonating.
add('notifications-session-revoked-on-receive', '/ws/notifications/', ACCEPTED, [
    send({'type': 'ping', 'timestamp': 1}), text({'type': 'pong', 'timestamp': 1}),
    {'produce': {'revoke_session': 'owner_full'}},
    send({'type': 'fetch'}), {'expect': {'close': 4003, 'reason': ''}}, {'expect': {'eof': True}},
    {'produce': {'restore_session': 'owner_full'}}],
    query='token={token:owner}&impersonation={session:owner_full}', cache_users=['full_collaborator'])
add('notifications-session-revoked-on-push', '/ws/notifications/', ACCEPTED, [
    {'produce': {'revoke_session': 'admin_owner'}},
    {'produce': {'group_send': f'notifications_user_{OWNER}', 'message': {'type': 'notification_push', 'notification': {'id': 'p0', 'association_id': OWN}}}},
    {'expect': {'close': 4003, 'reason': ''}}, {'expect': {'eof': True}},
    {'produce': {'restore_session': 'admin_owner'}}],
    query='token={token:admin}&impersonation={session:admin_owner}', cache_users=['owner'])
add('notifications-health-session-revoked', '/ws/health/', ACCEPTED, [
    text(HEALTH), {'produce': {'revoke_session': 'owner_full'}},
    send({'type': 'check'}), {'expect': {'close': 4003, 'reason': ''}}, {'expect': {'eof': True}},
    {'produce': {'restore_session': 'owner_full'}}],
    query='token={token:owner}&impersonation={session:owner_full}')

# ---------------------------------------------------------------------------
# Group events produced by Django code
# ---------------------------------------------------------------------------
push_own = {'id': 'a1000000-0000-4000-8000-000000762001', 'type': 'info', 'read': False, 'msg': 'Nuovo è \U0001f600',
            'association_id': OWN, 'issued': 1767225600, 'date': ctime(1767225600), 'ratio': 0.1}
push_foreign = {**push_own, 'id': 'a1000000-0000-4000-8000-000000762002', 'association_id': FOREIGN}
push_none = {**push_own, 'id': 'a1000000-0000-4000-8000-000000762003', 'association_id': None}
progress = {'sequence': 4, 'phase': 'copy', 'updated_at': '2026-11-09T10:00:00.123456+00:00', 'completed': 3, 'total': 7,
            'unit': 'tables', 'detail': None, 'percent': 43, 'elapsed_seconds': 12, 'eta_seconds': 15.5}


def push(user, notification):
    return {'produce': {'group_send': f'notifications_user_{USERS[user]}', 'message': {'type': 'notification_push', 'notification': notification}}}


def pushed(notification):
    return text({'type': 'notification_push', 'notification': notification})


def event(user, kind, payload):
    return {'produce': {'group_send': f'notifications_user_{USERS[user]}', 'message': {'type': kind, 'payload': payload}}}


add('notifications-owner-events', '/ws/notifications/', ACCEPTED, [
    push('owner', push_own), pushed(push_own), push('owner', push_none), pushed(push_none),
    {'produce': {'staff_board': OWN}}, text({'type': 'staff_board_changed'}),
    {'produce': {'staff_board': FOREIGN}}, push('owner', push_foreign), pushed(push_foreign),
    {'produce': {'relay': {'kind': 'staff_board_changed', 'target': OWN}}}, text({'type': 'staff_board_changed'}),
    event('owner', 'restore_progress', progress), text({'type': 'restore_progress', **progress}),
    event('owner', 'export_progress', {'percent': 10, 'type': 'shadow'}), text({'type': 'shadow', 'percent': 10}),
    event('owner', 'export_completed', {'download': 'x.zip'}), text({'type': 'export_completed', 'download': 'x.zip'}),
    event('owner', 'export_failed', {}), text({'type': 'export_failed'}),
    {'produce': {'group_send': f'notifications_broadcast_{CLUB}', 'message': {'type': 'export_failed', 'payload': {'via': 'broadcast'}}}},
    text({'type': 'export_failed', 'via': 'broadcast'}),
    {'produce': {'group_send': 'notifications_broadcast_all_notifications', 'message': {'type': 'staff_board_changed'}}},
    text({'type': 'staff_board_changed'}),
    *finish()], query='token={token:owner}', cache_users=['owner'])
add('notifications-scoped-events', '/ws/notifications/', ACCEPTED, [
    push('full_collaborator', push_foreign), push('full_collaborator', push_none),
    event('full_collaborator', 'restore_progress', progress), event('full_collaborator', 'export_progress', {'percent': 1}),
    event('full_collaborator', 'export_completed', {}), event('full_collaborator', 'export_failed', {}),
    push('full_collaborator', push_own), pushed(push_own),
    {'produce': {'staff_board': OWN}}, text({'type': 'staff_board_changed'}),
    *finish()], query='token={token:owner}&impersonation={session:owner_full}', cache_users=['full_collaborator'])
add('notifications-admin-session-events', '/ws/notifications/', ACCEPTED, [
    push('owner', push_foreign), pushed(push_foreign),
    event('owner', 'export_progress', {'percent': 2}), text({'type': 'export_progress', 'percent': 2}),
    {'produce': {'staff_board': OWN}}, text({'type': 'staff_board_changed'}),
    *finish()], query='token={token:admin}&impersonation={session:admin_owner}', cache_users=['owner'])
# The restricted collaborator holds messages.read in the shared fixture; losing
# it after connecting stops delivery (rechecked per event), restoring resumes it.
add('notifications-staff-board-recheck', '/ws/notifications/', ACCEPTED, [
    {'produce': {'staff_board': OWN}}, text({'type': 'staff_board_changed'}),
    {'produce': {'set_permissions': 'restricted_collaborator', 'permissions': ['association.payments.read']}},
    {'produce': {'staff_board': OWN}}, push('restricted_collaborator', push_own), pushed(push_own),
    {'produce': {'set_permissions': 'restricted_collaborator', 'permissions': ['association.communication.messages.read']}},
    {'produce': {'relay': {'kind': 'staff_board_changed', 'target': OWN}}}, text({'type': 'staff_board_changed'}),
    *finish()], query='token={token:restricted_collaborator}', cache_users=['restricted_collaborator'])
add('notifications-staff-board-not-joined', '/ws/notifications/', ACCEPTED, [
    {'produce': {'staff_board': OWN}}, push('athlete', push_own), pushed(push_own), *finish()],
    query='token={token:athlete}', cache_users=['athlete'])
add('notifications-not-joined-after-grant', '/ws/notifications/', ACCEPTED, [
    {'produce': {'set_permissions': 'restricted_collaborator', 'permissions': ['association.communication.messages.read']}},
    {'produce': {'staff_board': OWN}}, push('restricted_collaborator', push_none), pushed(push_none), *finish()],
    query='token={token:restricted_collaborator}', cache_users=['restricted_collaborator'],
    request={'before': [{'set_permissions': 'restricted_collaborator', 'permissions': []}]})
add('notifications-unknown-event-crash', '/ws/notifications/', ACCEPTED, [
    {'produce': {'group_send': f'notifications_user_{OWNER}', 'message': {'type': 'invoice_ready'}}}, *CRASH],
    query='token={token:owner}', cache_users=['owner'])
add('notifications-push-without-notification-crash', '/ws/notifications/', ACCEPTED, [
    {'produce': {'group_send': f'notifications_user_{OWNER}', 'message': {'type': 'notification_push'}}}, *CRASH],
    query='token={token:owner}', cache_users=['owner'])

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'WebSocket consumers: handshake, middleware, frames, store and group events',
                              'ids': {**IDS, 'inactive': INACTIVE, 'deleted': DELETED, 'admin': ADMIN},
                              'subscriptions': SUBSCRIPTIONS, 'store': {name: dumps(value) for name, value in STORE.items()} | {BROKEN: 'not json'},
                              'version': VERSION, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
