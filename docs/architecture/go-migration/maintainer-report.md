# Maintainer report — incomplete Go migration

This draft starts the full replacement from `main@f115d1e` in an isolated branch.
It is **not merge-ready as a backend replacement**. All requirements in the
[acceptance matrix](README.md#required-acceptance-matrix) and original
[request](requirements.md) remain binding.

The module follows the inspected Kuayle Echo → service → repository construction
with sqlx/pgx. Public routes currently expose monitoring only. Authentication
primitives, account reads, collaborator policy and identity selection are implemented
separately; complete HTTP admission and business resource authorization are not.
Django retains business-schema ownership. Deployment selection, release images,
the separate manual worktree and customer installations have not been changed.

Discovery records 514 declarations, 408 resolved HTTP patterns, four WebSocket
paths, 42 tasks, 15 MCP schemas and 112 model metadata records. Two retired tasks
and four in-progress health/auth routes are classified; 508 declarations still
need characterization. Effective versus declared Celery schedules remain unresolved.

Local evidence includes Go formatting/vet/race tests, 12 inventory tests, 18
independent Django HTTP contracts, three shared Go health contracts, real
PostgreSQL/Redis failure/recovery and idle shutdown. Self-host/workflow checks and
a foundation non-root image rehearsal also passed. These are scoped checks, not
the full acceptance matrix.

Credential tests exercise 20 Django-to-Go token vectors and 37 password vectors,
plus two Go token pairs and three password hashes read by Django. Historical
Argon2 retains upstream provenance/license. Cost census and aggregate login
resource limits remain open. Actor admission adds 18 real-token/account cases.
The collaborator route gate passes 38 explicit cases and declaration checks for
269 rules and 28 exemptions. Cache transport tests exchange impersonation records
in both directions with TTL, expiry and revocation.

Identity selection now passes 46 explicit Django/Go cases using the same private
PostgreSQL and Redis. Eight state/configuration changes test loss of eligibility;
32 concurrent resolutions use a four-connection pool. Actor, effective person,
data owner and association constraint remain distinct. Django readback confirms
users, associations, member links, instance configuration and audit rows are
unchanged after test-only transitions are restored. These cases contain no nested
resource IDs and use current-model schema creation, not migration history.

The resource repository adds 67 explicitly expected Django/Go visibility sets
for association, family and group predicates across 16 resource kinds. It retains
the differing deletion filters of existing managers and constrains both parents
of course/carnet subscriptions. Family/payment changes revoke visibility inside
transactions in both runtimes; fixtures are rolled back and every seeded table
is checked unchanged. Batches of 1–512 distinct tag references use one query;
32 concurrent reads use four connections. These predicates are not yet connected
to request admission or business handlers.

Missing work includes business endpoints, scoped operation/group/instructor/family
and nested-resource authorization, login writes and impersonation audit workflows,
durable workers/scheduler, versioned routing and coordinated switching,
realtime/integrations/documents/manual delivery, Go backup/restore, the complete
fixture adapter, query/performance evidence and release/updater rehearsals.
Health routes also remain uncertified for manual metadata, version-file precedence,
global middleware, error-body and proxy parity. Keep the PR draft; the foundation
checks do not authorize activation or release.
