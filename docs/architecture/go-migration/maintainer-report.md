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
and Adam7 with 803 explicit Pillow cases. Expected samples are constructed
independently. The actual Django helper agrees on 480 unchanged-size JPEG outputs,
300 unsupported-mode fallbacks and 23 invalid-stream fallbacks; the shipped image
runs the same pixel/JPEG-composition oracle. Exact logical budgets, overflow,
mid-decode cancellation and 32 concurrent decodes pass. Lazy PNG header/metadata admission now adds 394
explicit cases, including CRCs,
text limits, APNG first-frame declarations and metadata collisions. Actual Django
checks verify 235 valid 200×100 headers return their original URI with zero pixel
loads; the shipped image passes the same header oracle. File-level metadata and
canvas composition are covered by the PNG pipeline below; public image/response
wiring remains open.
Mode-1 nearest-neighbour transforms now preserve Pillow's accumulated-coordinate
rounding and exact JPEG output across 210 vectors, with 18 actual Django PNG
size-policy cases. Invalid binary samples, exact logical budgets, cancellation,
source ownership and 32 concurrent transforms/encodes are covered. The PNG decoder
also accepts valid early zlib stream ends on complete ordinary/Adam7 rows, leaving
unvisited pixels zero. Incomplete rows fail, as do missing trailers when more rows
are required. The shipped non-root/read-only/networkless image runs these oracles. File-level IDAT boundaries
must remain explicit because concatenation changes reference checksum timing.

Missing work includes business endpoints, complete HTTP admission, endpoint-level
group/instructor/family authorization, login composition and impersonation audit workflows,
durable workers/scheduler, versioned routing and coordinated switching,
realtime/integrations/documents/manual delivery, Go backup/restore, the complete
fixture adapter, query/performance evidence and release/updater rehearsals.
Health routes also remain uncertified for manual metadata, version-file precedence,
global middleware, error-body and proxy parity. Keep the PR draft; the foundation
checks do not authorize activation or release.


The PNG chunk-stream component now preserves Pillow's per-IDAT/64 KiB reads and
row-limited zlib calls, including DDAT/fdAT, sequence checks, active-chunk truncation
and checksum timing. Go reuses its filter/sample unpacker. All 852 explicit
pixel/chunk cases compare pixels and consumed file state; the built non-root,
read-only/networkless image runs the same oracle. Positive logical budgets,
malformed protocol, process failures, timeout, queued/running cancellation,
mid-unpack cancellation and 32 concurrent decodes are covered. Native ASan/UBSan
checks require exact successful frames and exact invalid-input exits without
diagnostics. Static zlib and its version/license ship in the existing helper;
the server remains CGO-free. The complete PNG first-frame pipeline now composes
this reader with header/tail metadata, canvas placement, resizing and JPEG saving.
Its 1,466 explicit whole-file cases compare with Pillow and the actual Django
helper, including cumulative header-plus-tail text limits, comment inheritance
and RGBA comment removal, fdAT sequencing, APNG next-frame stopping and the
observed Adam7 origin placement across all 15 mode/depth pairs. The shipped image
passes the same oracle. Per-stage budgets, tail/canvas cancellation, 32 concurrent
pipelines and whole-pipeline queue/running cancellation with child reaping and
admission reuse pass. Other image formats, dispatch and public image/login wiring
remain open. The ledger's status counts are unchanged; no endpoint is newly
marked verified.


The BMP/DIB pipeline now covers all seven supported header sizes, raw depths and
accepted bitfield layouts, palette mode reduction/load failures and RLE4/RLE8.
Its 512 cases declare expected header modes, pixels and JPEG/original outcomes;
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image agree. Tests preserve optional final padding, wrapped grayscale palettes,
absolute RLE alignment, odd RLE4 runs and Pillow's double-read delta behavior.
Positive stage budgets, exact bounds, mid-header/raw/RLE cancellation, immutable
input, 32 concurrent pipelines and encoder-stage cancellation with queued requests,
child reaping and admission reuse pass. Other supported image formats, format
dispatch/shared admission and public login composition remain open. This adds
component evidence without marking any endpoint verified or authorizing release.


The PPM-family pipeline adds PBM/PGM/PPM, grayscale PFM and Pillow's CMYK/RGBA/P
extensions. Its 429 declared header/pixel/whole-file cases cover numeric syntax,
range/scaling rules, PFM endian/row behavior, unsupported JPEG modes, comment
joining and 1 MiB plain-read boundary semantics. Pillow, the actual Django helper
and the shipped non-root/read-only/networkless image agree. Exact stage budgets,
overflow, input ownership, mid-header/raw/plain/PFM cancellation, 32 concurrent
pipelines and encoder cancellation with queueing/reaping/admission reuse pass.
Other file formats, shared dispatch/admission and public login composition remain
open. The ledger's status counts and release/activation restrictions are unchanged.


The PCX/DCX first-frame pipeline adds 831 declared header/pixel/file cases,
compared with Pillow, the actual Django helper and the shipped non-root,
read-only/networkless image. It preserves partial headers, stride correction,
all RLE counts and row-overrun failures, palette tails, padded planar unpacking,
DCX directory bounds and the inherited absolute 128-byte pixel seek for 8-bit
frames. Exact stage budgets, overflow, ownership, mid-header/RLE cancellation,
32 concurrent pipelines and encoder-stage queue/cancellation/reaping/reuse pass.
Other formats, shared dispatch/admission and public login wiring remain open;
ledger statuses and release/activation restrictions are unchanged.


The TGA pipeline adds 1,112 declared header/pixel/file cases, compared with Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves raw/RLE modes, row/horizontal orientation, inverted 16-bit alpha,
short ID/palette reads, palette core-mode changes and load failures, no-tile lazy
admission, repeat-row bounds and full literal-packet validation. Exact stage
budgets, overflow, input ownership, mid-operation cancellation, 32 concurrent
pipelines and encoder-stage queue/cancellation/reaping/reuse pass. Other formats,
shared dispatch/admission and public login wiring remain open; ledger status
counts and release/activation restrictions are unchanged.


The QOI pipeline adds 2,758 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves 13-byte lazy header admission, permissive channel values, seeded
cache/alpha state, wrapping deltas, runs crossing rows or the final pixel, and
ignored footer bytes. Exact stage budgets, overflow, ownership, mid-operation
cancellation, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. The test-only fault codec accepts RGB; production
native sources are unchanged. Other formats, shared dispatch/admission and public
login wiring remain open. Ledger status counts and activation restrictions remain.


The XBM pipeline adds 1,485 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves the 16-byte recognition prefix, greedy 512-byte header window,
permissive lowercase-x scanner, low-bit-first rows, padding and ignored
hotspot/trailing text. Exact budgets, overflow, ownership, cancellation during
long scans, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. Production native sources are unchanged. Other
formats, shared dispatch/admission and public login remain open; ledger status
counts and backend activation restrictions are unchanged.


The SGI pipeline adds 1,294 declared header/pixel/file cases checked against Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
It preserves all eight supported planar/RLE layouts, high-byte 16-bit samples,
partial/lazy headers, table offset/length semantics, persistent partial rows and
early successful exit. Exact budgets, overflow, ownership, mid-operation
cancellation, 32 concurrent mixed-image pipelines and encoder-stage admission/
cancellation/reaping/reuse pass. Production native sources are unchanged. Other
formats, shared dispatch/admission and public login remain open; ledger status
counts and backend activation restrictions are unchanged.


The SUN Raster pipeline adds 1,453 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves raw alignment versus packed RLE rows, all supported sample
layouts, every literal/run count, palette/core-mode and short-read boundaries,
64 KiB operand boundaries and lazy file policy. Exact budgets, overflow,
ownership, mid-operation cancellation, 32 mixed concurrent pipelines and encoder
queue/cancellation/reaping/reuse pass. Production native sources are unchanged.
SUN/GBR recognition precedence is documented as a remaining shared-dispatch
requirement. Other formats, shared admission and public login remain open;
ledger status counts and backend activation restrictions are unchanged.

CI was not fully green on the SGI head: the
[SSO browser job](https://github.com/carbogninalberto/assozeta/actions/runs/39549108830/job/119054304006)
timed out waiting for “Genera codice di collegamento” at
`selfhost/tests/browser/bakney-sso.mjs:234`. That failure produced no screenshot
or JSON artifact. Its cause remains unverified; it is not counted as a passing
gate. This increment changes neither that fixture nor the UI/SSO implementation.


The subsequent SUN-head [SSO browser job](https://github.com/carbogninalberto/assozeta/actions/runs/39550083913/job/119057239512)
passed the complete existing HTTPS/production-proxy/simulated-provider harness.
Ten fresh local browser sessions also reached an enabled pairing button using a
built UI and disposable PostgreSQL/Redis/backend. The local startup probe used
an alternate HTTPS port because port 443 was occupied, so it is not additional
full-protocol proof. The earlier timeout was not reproduced and no application
fix is claimed. Initial-stage failures now retain bounded, redacted JSON evidence;
unit tests cover its allowlist, sensitive-data exclusion and listener cleanup.
A real-UI injected pairing-status HTTP 503 verifies the failure report's status,
message identifier and missing button. This strengthens Django regression
verification; Go SSO and the complete migration remain unimplemented/unverified.


The GBR pipeline adds 1,341 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves versions 1/2, packed L/RGBA samples, actual comment-read
positions, negative/short reads, unconditional last-byte removal, grayscale
comment inheritance and RGBA metadata removal. The oracle exposed and now guards
the shared JPEG encoder's omission of empty comments. Exact budgets, overflow,
ownership, cancellation, 32 mixed concurrent pipelines and encoder-stage
queue/cancellation/reaping/reuse pass. Native production sources are unchanged.
A SUN-signature input recognized as GBR is covered; shared registry dispatch,
remaining formats and public login composition remain open. Ledger statuses and
backend activation restrictions are unchanged.


The MSP pipeline adds 1,704 declared header/pixel/file cases checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. It preserves DanM/LinS header checksums, MSB-first packing, blank and
concatenated records, clipped literals, zero-count repeats and validation of
malformed surplus operands. Expansion streams directly into normalized output
without an expanded intermediate buffer. Exact budgets, overflow, ownership,
raw/RLE/surplus cancellation, 32 mixed concurrent pipelines and encoder-stage
queue/cancellation/reaping/reuse pass. Production native sources are unchanged.
Other formats, shared dispatch/admission and public login remain open; ledger
status counts and backend activation restrictions are unchanged.


All eleven reported checks (including the aggregate PR status) on SUN head `ccfa67f` subsequently passed in
[run 39550083913](https://github.com/carbogninalberto/assozeta/actions/runs/39550083913),
including 1,794 backend tests and 16 diagnostic tests. This records the earlier
head’s regression result; it does not establish Go parity or replace the full
required acceptance matrix. CI for the GBR/MSP/SSO-diagnostics head is pending.


Password session coordination now connects the existing admission, JWT, response
and finalization services. Eighteen declared cases compare the real Django login
view with concrete Go repositories/services, including signed token claims and
all managed-table readback. A database-triggered final-write failure retains
normalization/default-card effects but exposes no response; OTP denial precedes
card creation. Eight concurrent repetitions preserve unique effects and release
connections. Explicit fixture query counts range from 1 to 18. Unit tests cover
all stage failures/cancellations and inconsistent identity/token results. Images,
public HTTP composition, social admission and middleware remain open.

The MSP-head manual verification exposed stale overview source hashes left by
main's `f115d1e` reader redesign. The coverage failure was reproduced locally;
review of the reader's loading/search/navigation and content-card link behavior
confirmed that the cited introduction text still applies. The two reviewed hashes
are updated without changing the UI or promoting pending workflow evidence.
All 180 manual Python tests and 11 browser-helper tests pass locally without
skips. The full isolated manual capture/publication workflow remains a CI gate;
these unit/helper checks do not establish that broader result.
