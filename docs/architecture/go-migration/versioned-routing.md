# Versioned proxy routes — implementation in progress

The self-host Caddy configuration now distinguishes fixed backend versions before
matching the compatibility alias. This is a routing increment within the complete
[migration requirements](requirements.md), not a completed backend switch.

| External path | Upstream | Upstream path |
| --- | --- | --- |
| `/api/v1/...` | Django, `api:8000` | `/...` |
| `/api/v2/...` | Go, `api-go:8000` | `/...` |
| `/api/...` | Django, current default | `/...` |
| `/bakney/v1/...` | Django, current default | unchanged |
| `/ws/...` | Django, current default | unchanged |

Exact `/api/v1` and `/api/v2` requests reach the corresponding upstream root
without redirecting or changing the method, body or query. Similar prefixes such
as `/api/v10/...` continue through the compatibility alias. The fixed versions
also carry root protocols: `/api/v1/bakney/v1/...` and
`/api/v2/bakney/v1/...` retain the Bakney protocol version upstream;
`/api/v1/ws/...` and `/api/v2/ws/...` become `/ws/...` and support upgrades.
Pairing and Bakney protocol responses retain `no-store` and `no-referrer` on
every alias. Caddy does not translate authentication or application payloads.

The three API readiness paths remain accessible during maintenance. Other new
requests, including versioned root protocols and WebSockets, receive the existing
maintenance response. A response stream admitted before maintenance can finish
after admission closes. This does not establish coordinated backend switching or
draining of workers and existing WebSocket sessions.

**Current deployment limit:** the ordinary Compose/release/updater path does not
yet deliver and start `api-go`. Without that upstream, `/api/v2` returns a gateway
error; it does not fall back to Django. The existing API alias and root protocols
continue using Django. No operator activation command or active-backend setting
is claimed by this increment. Delivery, validated runtime selection, recovery and
single worker/scheduler ownership remain required before activation.

## Executed contracts

Run the independent transport fixture with Docker available:

```sh
python3 selfhost/tests/versioned-routing.py
```

It starts the exact Caddy image pinned by `UI/Dockerfile` (currently
`caddy:2.11.7-alpine`) using the real self-host configuration. Only upstream
addresses are replaced, and a diagnostic header on the disposable proxy's own
health response observes the actual client IP independently of application audit
rows. Synthetic upstreams expose transport observations; they do not implement
Django or Go business behavior. No existing containers, configuration or data are
used. Temporary listeners use generated ports and synthetic data.

Portable contracts cover methods, exact version roots, encoded paths and query
strings, binary and chunked uploads, request headers, response/error status codes,
multiple cookies, redirects, binary range downloads, HEAD, streaming before
upstream completion, and WebSocket handshake/data/close frames. They also verify
maintenance admission, in-flight stream completion and Go outage isolation.
Read-only probes establish maintenance visibility before attempting a mutation;
host-side flag creation alone is not treated as an observed admission barrier.

The actual application-root rehearsal is included in:

```sh
quality-reports/go-migration/python31316/bin/python \
  TESTS/adapters/django/accounts.py --only profile-tables
```

It now additionally runs the same 62 preference contracts, three locked-row races
and three health contracts against each versioned API through Caddy. PostgreSQL
readback verifies exact preference changes, atomic rollback, audit/index effects
and preserved unrelated records. Audit IPs match Caddy's independently observed
client address, rather than the client's forged forwarded address. The direct
root checks and required query-count/cancellation integration suite also pass.
This fixture requires Docker and briefly listens on generated host ports so the
proxy container can reach the disposable Django/Go servers. All identities,
databases, Redis state and keys are synthetic.

Evidence is under ignored `quality-reports/go-migration/versioned-routing/`:
`red.log` records 39 failures before versioned routing; `expanded-red.log` exposes
the exact-root mismatch; `maintenance-red.log` exposes blocked readiness.
`transport-final.log` records the fixture's incorrect assumption of immediate
flag visibility. `transport-complete.log` records the corrected transport suite;
`real-roots-health.log` and `real-roots-results.json` record application-root and
PostgreSQL checks. `selfhost-validation.log` covers 87 existing self-host tests and
configuration checks; `browser.log` records eleven existing reload/browser tests
behind Caddy. These browser tests retain their synthetic API fixture and do not
prove the complete frontend against Go.

CI runs the transport fixture for relevant self-host changes and runs real-root
contracts for Caddy, backend or adapter changes. Final generated links, real SSO
and OAuth callbacks, private-file authorization, complete realtime behavior,
TLS/HTTP2/HTTP3, all business contracts, normal image delivery and coordinated
backend/worker/scheduler switching remain acceptance gaps.
