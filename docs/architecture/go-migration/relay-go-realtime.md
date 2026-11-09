# relay_go_realtime → `BE_GO/cmd/relay-go-realtime`

Django: `BE/notifications/management/commands/relay_go_realtime.py` with
`notifications.go_realtime.relay_once` (run by the `selfhost/compose.yml` relay
service). Earlier this command stayed Django-only because Go did not write the
Channels encoding; `internal/channels` now reproduces
`GoMirroredRedisChannelLayer.group_send` byte for byte, so the relay is ported.
The compose service still runs the Django command; switching it is a deployment
decision outside this port.

## Behaviour (Django, reproduced in Go)

- `relay_once`: `to_regclass('public.assozeta_go_realtime_events') IS NOT NULL`;
  an absent table returns zero counts without a transaction. Otherwise one
  transaction claims `delivered_at IS NULL AND failed_code IS NULL` rows
  `ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED` and handles them in order:
  - version 1 and kind `staff_board_changed`: `group_send('staff_board_<target>',
    {'type': 'staff_board_changed'})` (channels_redis group trim, channel queues
    with capacity 100 and expiry 60, then the `assozeta:realtime:v1` mirror);
    success sets `attempts+1, delivered_at=clock_timestamp()`;
  - another version or kind: `attempts+1, failed_code='unsupported_event'`;
  - a layer error (Redis unreachable, authentication refused): logged; when
    `attempts+1 >= 30` the row gets `failed_code='delivery_exhausted'`,
    otherwise only `attempts+1`.
- `--once` prints `str()` of `{'delivered': n, 'retry': n, 'failed': n}` and exits 0.
- Without `--once`: `LISTEN assozeta_go_realtime`, drain while a batch delivers
  or fails something without retries, then wait up to `--poll-seconds`
  (default 2.0) or until a notification (several notifications wake once).
- Layer: `CHANNEL_LAYERS` host `REDIS_HOST:REDIS_PORT` DB 0 with `REDIS_PASSWORD`
  and no username; TLS under `REDIS_SSLAUTH` with the shared root-certificate option.

Go: `cmd/relay-go-realtime/main.go` (options, configuration, LISTEN connection),
`internal/service/relay_go_realtime.go` (relay_once, loop),
`internal/repository/relay_go_realtime.go` (SQL), `internal/domain/relay_go_realtime.go`.
The binary is in the Go image (`/app/relay-go-realtime`).

## Not ported

- Django's global management options and argparse prefix abbreviations; argparse
  error texts (Go prints its usage and exits 2).
- `--poll-seconds` negative, NaN or infinite: Django fails at its first `select()`
  after one relay pass; Go refuses the value before any work (exit 2).
- `relay_once`'s branch for a missing channel layer (`layer is None` -> retry):
  the shipped settings always configure the layer, and Go always has one.
- Database errors: Django ends with a traceback (exit 1); Go prints a generic
  line (exit 1). A lost LISTEN connection ends both.
- The loop mode is covered by unit tests (`relay_go_realtime_test.go`: drain
  while progressing, wait after a retry, wake on a notification), not by the
  process differential.

## Evidence

`TESTS/adapters/go/relay_go_realtime_command.py`: private PostgreSQL (Django
schema plus `BE_GO/migrations/000004_realtime_events.up.sql`) and a private
password-protected redis-server. Each case recreates the outbox rows, flushes
Redis and seeds group memberships (two channels, one channel, only an expired
member), runs the actual `manage.py relay_go_realtime` with the shipped
`CHANNEL_LAYERS`, then the race-built Go binary on the same reset state: exit
status, stdout, every outbox row, every Redis key (channel queues decoded from
the random prefix and msgpack, group sets, TTLs) and the mirror envelopes seen
by a subscriber must be identical; Django must also meet the authored counts,
row outcomes and mirror groups. 9 cases: mixed outcomes (pending, delivered,
failed, unknown kind, last attempt, a group with only an expired member),
`--poll-seconds=0.5 --once`, nothing pending, missing table, envelope version 2
(check constraint dropped), a row locked by another transaction (skipped), the
100-row limit, Redis unreachable (retry and exhaustion), Redis refusing the
password.

- RED (`quality-reports/go-migration/relay-go-realtime/red-r3`): Django 9/9, Go command absent.
- GREEN (`green-r3`): 9/9.
- Mutation (`mutation-r2`): exhaustion at `attempts+1 > 30` instead of `>= 30`
  fails Go on "channel layer unreachable: retry, then exhausted".
