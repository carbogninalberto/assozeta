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
and four in-progress health/auth routes plus the partial audit-index connection are
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
response composition remains open; configuration creation is described below.

Membership-card response configuration now has 20 actual Django-builder/audit
cases versus Go with exact persisted fields and Django readback. Existing rows
require two statements; creation writes configuration/audit/index atomically in
five. Thirty-two concurrent Go calls create one invariant in 67 statements.
Real permission/FK failures, injected errors, cancellation and a forced audit-actor
deadlock verify rollback and retry. The shared audit SQL retains username behavior.
Django's duplicate-creation race and partial autocommit failure effects are
characterized explicitly. Go-only locking does not close cross-runtime concurrent
creation; that remains an activation gap, alongside complete login composition.

Final login user writes add 17 actual-view cases for exact last_login and UTC
scheduled-deletion cancellation, including midnight between clock reads. Audit
and index are atomic; bounded query counts and 32 concurrent calls are verified.
Actual permission/FK failures and injected failures roll back cleanly. Django's
partial autocommit and stale-save overwrites are characterized; Go preserves
current unrelated fields. Public login composition, revocation races and mixed
Django/Go cancellation remain open. See the finalization component contract.

Response composition now has 36 explicitly expected actual-Django-builder cases
against Go, including full key presence, collaborator settings, registration
choices, instructor metadata and default-card persistence. Seven/ten-statement
paths and 32 concurrent compositions (224 statements) pass. A real instructor
permission failure remains required only for collaborators; every managed table
matches after restoration. Registration configuration matches 28 boolean and eight
dotenv cases. Image conversion is not implemented or counted: this fixture covers
null images and branches Django leaves unchanged. The public login coordinator,
image processor and final session delivery remain open.

Image prerequisites now include pure-Go permissive Base64 decoding, the actual
Django size policy and white-alpha/Lanczos pixel processing. Ten real-helper
policy cases, 54 byte-exact Pillow pixel vectors and 19,535 Base64 vectors pass,
with cancellation, logical memory limits and concurrency tests. The Pillow license
and source provenance are retained. A bounded native libjpeg encoder additionally
matches 180 exact Pillow JPEG vectors; the 54 transformed rasters also match their
JPEG output. Tests exercise byte ceilings, protocol failures, running/queued
cancellation, timeout, process cleanup and 32 concurrent encodes. The static helper
is shipped with license/version notices in the non-root Go image, which passes the
same 180-vector oracle without network or writable storage. The server remains
CGO-free. JPEG decoding adds 312 exact pixel/comment cases and 1,556 invalid
streams, with actual Django helper output/fallback checks across all 1,868 cases.
The shipped image passes the same decoder suite. Inherited comments, long-comment
save failure, native framing/budgets, process cancellation and concurrent decoding
are verified. A lazy JPEG marker reader passes 1,352 explicit Pillow cases and
three actual Django header-only passthrough checks, plus header budgets,
cancellation, metadata ownership/ordering and concurrent readers. The same marker
oracle runs in the image. EXIF/MPF admission adds 226 explicit cases; the complete
JPEG file pipeline adds 73 size/lazy/fallback cases and reruns all metadata and
decoder vectors. Django helper checks and the shipped image agree. Header/raster/
transform/file budgets, one deadline and admission across JPEG stages, 32
concurrent resizes and queued/running cancellation are verified. Other formats,
admission across formats and the complete image processor remain necessary for
login; no full image or HTTP parity is claimed.

The PNG pixel primitive now covers all 15 depth/color combinations, five filters
and Adam7 with 801 explicit Pillow cases. Expected samples are constructed
independently. The actual Django helper agrees on 478 unchanged-size JPEG outputs,
300 unsupported-mode fallbacks and 23 invalid-stream fallbacks; the shipped image
runs the same pixel/JPEG-composition oracle. Exact logical budgets, overflow,
mid-decode cancellation and 32 concurrent decodes pass. PNG chunk/metadata
admission, APNG composition and resized mode-1 nearest-neighbour behavior remain
open; this primitive is not a complete PNG file processor.

Missing work includes business endpoints, complete HTTP admission, endpoint-level
group/instructor/family authorization, login composition and impersonation audit workflows,
durable workers/scheduler, versioned routing and coordinated switching,
realtime/integrations/documents/manual delivery, Go backup/restore, the complete
fixture adapter, query/performance evidence and release/updater rehearsals.
Health routes also remain uncertified for manual metadata, version-file precedence,
global middleware, error-body and proxy parity. Keep the PR draft; the foundation
checks do not authorize activation or release.
