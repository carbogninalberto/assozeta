# Maintainer report — incomplete Go migration

This draft starts the full replacement from `main@f115d1e` in an isolated branch.
It is **not merge-ready as a backend replacement**. All requirements in the
[acceptance matrix](README.md#required-acceptance-matrix) and original
[request](requirements.md) remain binding.

The module follows the inspected Kuayle Echo → service → repository construction
with sqlx/pgx. Public routes currently expose monitoring and token refresh. Authentication
primitives, account reads, collaborator policy and identity selection are implemented
separately; association-bound reference admission is now composed before
collaborator grants. Complete HTTP admission and business resource authorization
remain unfinished.
Django retains business-schema ownership. Deployment selection, release images,
the separate manual worktree and customer installations have not been changed.

Discovery records 514 declarations, 408 resolved HTTP patterns, four WebSocket
paths, 42 tasks, 15 MCP schemas and 112 model metadata records. Two retired tasks
and four in-progress health/auth routes plus the user-audit index connection are
classified; 507 declarations still
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
32 concurrent reads use four connections. These predicates now support the
identity resolver's association-bound request guard; business handlers remain open.

The guard passes 132 Django/Go cases, including all 44 athlete operation names and
six composed resolver cases proving denial ordering before collaborator grants.
Ordered nested references retain integer UUID precision and dictionary order.
Medical-upload cache grants retain Django's raw-key spelling, TTL and revocation.
One thousand twenty-five interleaved tag/course references require four SQL
queries; 32 concurrent guard calls use four connections. Dependency failure and
missing trusted route metadata return no usable identity. This is not public HTTP
authentication or authorization of subsequent business writes.

The registered refresh handler passes 26 portable contracts against both actual
HTTP servers. It retains cookie, optional-Bearer/scoped admission, non-rotation,
null-constructor and parser/error behavior. Django verifies Go signatures and
accepts a Go refresh credential over HTTP; all managed model tables remain
unchanged. Global middleware, transport edge cases, proxy/routing and deployment
evidence are still incomplete, so the ledger row remains implementing.

Password credential selection now passes 21 explicit actual-Django-view versus Go
service cases. Compatible rehashes are written transactionally and read back by
Django, including ambiguous and inactive outcomes. This is not an authenticated
session: final response construction and subsequent login effects remain open.
The independent OTP gate passes 47 actual-Django-view versus Go decisions covering
clock boundaries, Unicode, malformed inputs/secrets and multipart metadata. Denied
Django requests leave last-login unchanged; Go performs no writes in this gate.

Password admission now composes credentials, transactional username/audit preparation
and OTP. Twenty actual Django login/audit-middleware cases pass, with exact audit
readback and unchanged managed tables after fixture restoration. Real PostgreSQL
checks prove rollback, one/five-query paths, concurrent deduplication and retry
after a forced cross-actor deadlock. Go's atomic rollback intentionally differs
from Django's orphan pre-save audit after username collision; this is documented,
not claimed as identical failure-side-effect parity. The fixture audit registry
is now initialized after schema creation, matching ordinary server startup.

The base login profile now matches 46 explicit Django-rendered JSON cases with
all 37 fields. Welcome/setup rules, Google JSON presence and duplicate onboarding
are preserved. One/two SELECTs remain bounded through 512 onboarding rows; 32
concurrent association reads release the four-connection pool. All managed tables
remain unchanged after fixture restoration. A real onboarding SELECT permission
failure now preserves the base profile with null onboarding in both runtimes;
Go rolls back the aborted read transaction before pool reuse.

The association profile adds 23 explicit cases covering all 50 serializer fields,
exact decimal text, PostgreSQL scale rounding, nullable/scalar JSON, the reviewed
boundary and scoped groups. One/two SELECTs remain bounded through 512 groups;
32 concurrent reads use 64 SELECTs. Association deletion immediately revokes
serialization. Required group-query failures return no partial profile, matching
Django. All managed tables match after the declared fixture transition. Full
response augmentation, configuration creation and final writes remain open.

Missing work includes business endpoints, complete HTTP admission, endpoint-level
group/instructor/family authorization, login writes and impersonation audit workflows,
durable workers/scheduler, versioned routing and coordinated switching,
realtime/integrations/documents/manual delivery, Go backup/restore, the complete
fixture adapter, query/performance evidence and release/updater rehearsals.
Health routes also remain uncertified for manual metadata, version-file precedence,
global middleware, error-body and proxy parity. Keep the PR draft; the foundation
checks do not authorize activation or release.
