# WebSockets — implementing; session parity proven on four transports

Django serves four Channels routes from `BE/core/asgi.py`: `JWTAuthMiddleware`
(`BE/notifications/middleware.py`) wraps one `URLRouter` with `ws/updates/`
(`UpdateConsumer`), `ws/health/` (`HealthConsumer`), `ws/agent/`
(`AgentConsumer`) and `ws/notifications/` (`NotificationConsumer`). Production
runs them under gunicorn's `UvicornWorker` (uvicorn 0.35, websockets 14.1 legacy
protocol). Go now serves the first three with the same handshake, middleware,
frames and messages, and receives Django-originated group messages. `ws/agent/`
is not ported. Django remains the execution owner: Caddy still sends `/ws/*` to
Django; Go is reachable at `/api/v2/ws/...` and natively.

## Behaviour (characterized and pinned)

**Transport (uvicorn + websockets).** uvicorn hands a request to the WebSocket
protocol when its last `Connection` header lists `upgrade` and its last
`Upgrade` header is `websocket`; nothing of Django's HTTP stack runs. websockets
then requires GET, exactly one `Upgrade: websocket`, one `Sec-WebSocket-Key`
that strictly decodes to 16 bytes and `Sec-WebSocket-Version: 13`; failures are
400 `text/plain` bodies `Failed to open a WebSocket connection: <reason>.` where
the reason carries the exception chain (for example `missing
Sec-WebSocket-Key header; 'sec-websocket-key'`, `invalid Sec-WebSocket-Key
header: multiple values; 'Sec-WebSocket-Key'`, binascii messages for bad keys).
A consumer `close()` before `accept()` is HTTP 403 with an empty body (the
close code, for example 4001 or 4003, never reaches the client). An exception
before accept is uvicorn's plain `500 Internal Server Error`. No subprotocol is
ever selected. `permessage-deflate` is negotiated by
`ServerPerMessageDeflateFactory()` with no configured parameters (a bare
`client_max_window_bits` offer is accepted and not echoed; the first valid offer
wins; unknown extensions are skipped). Frames: client frames must be masked
(1002 otherwise), reserved bits and opcodes are checked (1002), invalid UTF-8
text fails with 1007, a message over 16 MiB with 1009, all with an empty reason
and a TCP half-close; a received close payload is echoed; pings are answered;
fragmented messages are joined. An exception after accept closes the TCP
connection with no close frame. Keepalive pings every 20 s with a 20 s timeout
(1011 `keepalive ping timeout`).

**Middleware (every path).** The token is the first non-blank `?token=` value
(`parse_qs`), else the `BKN_AUTH=` item of the last `Cookie` header. It must be
a valid SimpleJWT access token whose `iss` is `https://bakney.com` and `aud` is
`bakney` (exact strings; tokens without these claims, as `AccessToken()` mints
them, are anonymous) and whose truthy `user_id` names an active, non-deleted
user; every failure is the anonymous user. A non-blank `?impersonation=`
session goes through `resolve_target` with the authenticated actor; a missing
actor or `PermissionDenied` closes with 4003 before accept (HTTP 403) on every
path, including unrouted ones (the middleware runs before the router). While
impersonating, every received data frame and every send (including the accept)
re-resolves the session; a revoked session closes with 4003 and further sends
are dropped. Unrouted paths, including a path without its trailing slash, are
the router's exception (500).

**Consumers.**
- `ws/health/`: accepts and sends `{"type": "health", "status": "ok", "msg": "I
  am good", "version": RUNNING_VERSION}`; `ping` answers `pong` with the
  message's `timestamp` (or null), `check` resends health, anything else is
  `Unknown message type: <str(type)>`. Binary or empty frames, invalid JSON and
  non-object JSON raise.
- `ws/updates/`: echoes `{"message": <message>}`; a missing key, non-object JSON
  or a binary frame raises.
- `ws/notifications/`: anonymous closes with 4001 (HTTP 403). It joins
  `notifications_user_<id>`, `staff_board_<association>` when
  `readable_association_id` allows it, and `notifications_broadcast_<name>` for
  every subscribed broadcast (plus `all_notifications`), then accepts. `fetch`,
  `mark_read` (truthy `id`, otherwise `Missing 'id' for mark_read`; the id is
  echoed as sent) and `mark_all_read` read and write NotificationManager's JSON
  lists in Redis database 0 (Django's `json.dumps` text), with the broadcast
  list cached for 300 s in the Django cache (`user_broadcasts:<id>`, pickled).
  Store failures inside a handler send `Internal server error`, except
  `mark_all_read` without an association scope, which swallows them and
  confirms. Under an owner's impersonation (association scope) only that
  association's notifications are listed, read or pushed, and restore/export
  progress events are suppressed; an administrator's session has no scope.
- Group events: `notification_push` (scope filter), `staff_board_changed`
  (permission recheck on every delivery), `restore_progress`,
  `export_progress`, `export_completed`, `export_failed` (`{"type": name,
  **payload}`; a payload `type` replaces the value in place). A type without a
  handler, or a missing `notification`/`payload`, raises.

Text is always Python `json.dumps` output (ASCII escapes including lone
surrogates, `", "`/`": "` separators, float `repr`, `NaN`/`Infinity`), and input
is Python `json.loads` (NaN/Infinity literals, last duplicate key wins in first
position, more than 4300 integer digits raise).

## Realtime interop design

Channels' Redis encoding is not a Go protocol (see `staff-board.md`). Two
directions exist:
- **Go writes → any socket.** Unchanged: Go appends `assozeta_go_realtime_events`
  in the business transaction; `python manage.py relay_go_realtime`
  (`BE/notifications/go_realtime.py`) claims the row and calls the channel
  layer's `group_send`. The relay stays Django-owned by design (it must speak
  through Django's channel layer), so `relay_go_realtime` is not ported.
- **Django `group_send` → Go sockets.** New
  `BE/notifications/realtime_layer.py` `GoMirroredRedisChannelLayer` (now the
  configured backend) performs the normal Channels delivery and then publishes
  `{"version": 1, "group": ..., "message": ...}` (Python `json.dumps`) on the
  Redis pub/sub channel `assozeta:realtime:v1`. Publication failures are logged
  and never fail the sender. The Go server subscribes once at startup (waits up
  to 5 s, then keeps retrying), keeps process-local group membership
  (`internal/service/realtime_hub.go`, Channels' 100-message per-channel
  capacity, at most once) and dispatches to the connection's consumer loop in
  arrival order with its own frames. Because the relay calls `group_send`, Go
  outbox events reach Go sockets through the same mirror.

## Go implementation

- `BE_GO/internal/wsproto`: handshake checks with websockets' exact bodies
  (strict base64 messages ported from CPython's `a2b_base64`, RFC 7230 list and
  extension parsing), permessage-deflate negotiation, the frame reader (masking,
  RSV/opcode checks, control-frame rules, fragmentation, incremental UTF-8,
  16 MiB, inflate with context takeover), close handshake, half-close and
  keepalive. Outgoing frames are never compressed (a permessage-deflate sender
  may leave RSV1 clear); clients see the same decoded messages.
- `BE_GO/internal/handler/websocket.go`: upgrade detection before echo (no HTTP
  middleware, as in uvicorn), the middleware (`parse_qs`, cookie, token,
  impersonation, rechecks), routing and the per-connection loop.
  `websocket_consumers.go`: the three consumers.
- `BE_GO/internal/service/websocket_identity.go` (token rules and
  `selected_user()` through the existing `resolveTarget`),
  `notification_consumer.go` (NotificationManager reads/writes, ctime in UTC,
  stable newest-first sort), `realtime_hub.go` (groups, Channels group-name
  validation).
- `BE_GO/internal/repository/notification_store.go`: broadcast query (Django's
  SQL, no ORDER BY), the `user_broadcasts` cache with django-redis protocol-4
  pickle encoding byte for byte, the Redis-0 JSON lists, `readable_association_id`
  and the pub/sub subscription. `BE_GO/internal/dto/websocket_json.go`:
  Python `json.loads`.
- `cmd/server/main.go`: Redis database 0 clients, the subscription and the
  handler in front of echo.

## Not ported or not characterized

- `ws/agent/` (`AgentConsumer`): its `user_message` path runs the agent loop,
  which calls the configured LLM provider whenever AI is enabled; the other
  paths (welcome, ping, saved reports, manual-only answers, cancel) sit on the
  same agent core, manual search, throttle, concurrency guard and AI
  integration configuration, none of which is ported, so a partial consumer
  would be a silent partial port. Go answers `ws/agent/` like any unrouted path
  (500 before accept, after the middleware, including its 4003 agent-path rule
  for scoped athletes and restricted collaborators). Caddy must keep routing it
  to Django.
- NotificationManager's per-process bootstrap (creating the `all_notifications`
  broadcast row, audited, and its Redis key when missing) is not performed by
  Go; any Django process does it on first notification use.
- Channels resolves any consumer attribute named by a group message type
  (`send_json`, `websocket.disconnect`, ...); Go dispatches only the declared
  event handlers and raises for the rest. Django code sends only declared types.
- Other malformed-frame cases (1002 for unexpected continuation, bad close
  codes) follow websockets' rules but are not in the authored sessions; the
  JSON nesting limit (Go 10000) and timestamp years outside 1–9999 are
  not characterized; broadcast lists of 64 KiB or more (several pickle frames)
  are served but not cached by Go.
- Server shutdown: uvicorn fails open sockets with 1012; Go's HTTP drain does
  not close hijacked WebSocket connections.
- `Server`/`Date` headers on the 101 and error responses, and uvicorn's access
  log lines, are not reproduced.

## Evidence (TDD)

`TESTS/fixtures/websockets_http.json` (generator alongside) authors 75 sessions
run by `TESTS/contracts/test_websockets_http.py` with the raw RFC 6455 client
`TESTS/ws_client.py`; `TESTS/adapters/django/websockets_http.py` serves Channels'
real ASGI application under uvicorn with the mirrored Redis layer, seeds the
identity fixture, broadcasts, subscriptions and the Redis store, produces group
messages from Django code (`group_send`, `publish_staff_board_change`, the
outbox relay), revokes sessions and permissions mid-session, and after every
case requires the complete model state unchanged, the store bytes and the
`user_broadcasts` cache bytes as authored, and no HTTP requests. The driver
(`staff_board_http_acceptance.py`) gained an optional family
`asgi_application()`; nothing else changed for HTTP families.

The first Django run corrected two authored handshake bodies (websockets
appends the `KeyError`/`MultipleValuesError` text, with different key casing);
every other expectation held as authored.

- RED (`websockets-http-red-r1`, Go files removed and `main.go` at its parent
  revision): Django 75/75; Go 404 on the first handshake.
- GREEN (`websockets-http-versioned-r1`): 75/75 on native Django, native Go,
  Caddy `/api/v1` and `/api/v2`.
- Mutation (`websockets-http-mutation-r1`): treating every connection as
  unscoped in `notificationConsumer.visible()` fails Go on
  `websockets-notifications-scoped-messages` (CAUGHT, restored).
- Go unit tests pin the strict base64 messages, extension negotiation, the
  pickle bytes and `parse_qs` against CPython 3.13.16 output.
- Families sharing touched code re-run green: `staff-board` (32/32, realtime
  outbox and the driver) and `email-logs` (13/13, `main.go` handler wrapper).
