# Maintainer report — incomplete Go migration

This draft retains all fourteen [accepted requirements](requirements.md).
**It is not ready for production activation or merge as a backend replacement.**
Work is isolated on `codex/go-backend-parity`, reconciled through
`main@79a9460`. Django retains production API selection, business-schema,
worker and scheduler ownership. No production/customer data or published release
has been changed.

The manual knowledge library now implements search, section reads, owner evidence
and coverage diagnostics. All 67 authored function cases compare complete Django
results with Go under fixture-supplied trusted context. The separate Go service
also matches all 67 cases using live PostgreSQL owner resolution, with one query
per valid call, 32 concurrent calls and blocked-query cancellation/recovery.
The independent Django stdio oracle now passes 40 MCP requests through the actual
management command and SDK, with protocol negotiation, pipelining, request
cancellation, database query budgets and clean EOF checks. Six regression checks
also reject extra database reads, premature process exits and trailing stdout.
The Go stdio component passes the same forty requests with live PostgreSQL and
sealed manual files, plus observed SQL cancellation and one-query recovery. It
exposes four manual tools only; a production MCP command, eleven business tools,
JWT/WebSocket admission and agent dispatch remain open.
The unchanged manual reader browser passes on both
backends, while the separate full manual capture remains incomplete at 55/60
workflows. See [manual evidence and limitations](manual-reader.md).
The corrected source mappings pass 168 Python and 79 Node checks locally and in
CI. Remote manual workflows then stop before captures: companion `9d9cffd` has
277 stale recipe bindings across eleven modules. A private supported editorial
review updates only those bindings, preserving all 44 Italian MDX files and zero
verified sections. Companion publication and the five historical capture failures
remain unresolved.

The native worker now registers expired membership-card token cleanup. Twelve
Django/Go PostgreSQL contract groups and nine native process scenarios pass under
race detection, including stale admission, exact audit rollback and crash recovery.
Seven packaged token scenarios also pass on the pinned `14919ba` image, including
actual Django readback and signal recovery. Django remains the execution owner;
task rate policy and installed handoff are still open. See [token worker scope](token-worker-process.md).

Audit retention also rejects stale admission before deletion or handled completion.
Its twelve PostgreSQL groups include owner/token loss after a real deferred-FK
COMMIT rejection, preserving all audit/index records and recoverable job state.
Native registration and scheduler activation remain open.

Scheduler authority now has 48 explicit observations against the pinned actual
Django runtime and private PostgreSQL. It distinguishes 21 source declarations
from the single effective configured fresh entry, implicit cleanup and persisted
historical rows. Tests also expose cursor advancement before failed publication.
The pure Go evaluator passes 23 differential timing cases, 37 unsupported-input
and five calendar/precision rejection cases. It supports persisted daily reset
using seconds intervals or scalar daily UTC cron, without publishing jobs. The
combined oracle/differential adapter is required in CI; it does not activate Go
scheduling or establish broker/handoff parity. See [the characterization](scheduler-authority.md).

The exact-row periodic publisher additionally passes seventeen real PostgreSQL
race/fault groups using Django's actual Beat schema. Ownership, source and schedule
definition locks precede an atomic job/receipt/cursor transaction; retained sequence
numbers survive count reset and source-ID reuse. Additive schema 3 is delivered
through the explicit migrator. Bounded candidate pages also pass eleven service
groups and nine actual-Beat PostgreSQL/race groups, with finite rotations under
continuous inserts and measured plans at 100/1,000/10,000 rows. Each page retains
its cursor on errors and independently locks each publication. An installed
scheduler service, cardinality/backlog policy and Celery handoff remain open; see
[publication limits](../../../BE_GO/internal/service/occurrence.md) and
[page bounds](../../../BE_GO/internal/service/schedule_page.md).

The image packages a native scheduler with serial bounded pages, cursor retention
after ambiguous errors, positive pacing and joined shutdown. Ten loop groups,
twelve native-process cases and four packaged cases pass. It publishes only the
supported persisted daily-reset definitions and does not select ownership or start
an installed service. See [scheduler delivery scope](scheduler-process.md).

Explicit repository transactions now retain their private connection through
automatic rollback and driver/socket cleanup. Eight real PostgreSQL/race groups
cover worker, HTTP and snapshot lifetime plus connection reuse and failure paths;
the old implementation fails the three representative lifetime assertions. All
seventeen transaction entrypoints use the helper without changing their original
context or isolation. Local socket cleanup and server disconnect processing are
distinct boundaries; see [the transaction contract](../../../BE_GO/internal/repository/transaction.md).

A separate database barrier now drains cooperating Go transactions before recovery
export, CLI backup snapshots and schema migration. The original seventeen explicit
transactions and five queue mutations acquire an acknowledged shared transaction
lock before effects; the lifecycle caller takes the exclusive form while retaining
its file lease. Fifteen queue cases, five actual PgBouncer/CLI cases and three
Go→Django restore cases cover late commits, canceled admission and fail-closed
timeout. Eighty-eight Django restore/operations tests pass. This adds fixed protocol
query costs, recorded [per path](transaction-query-costs.md), and does not fence
every Django writer or provider. See [the exact guarantee](transaction-drain.md).
The rebuilt-image fresh installation, backup, rollback and restore rehearsal and
the current-controller update/recovery follow-up both pass. Exact source scope,
initial fixture timeouts and the newer-main validation boundary are recorded in
[operational migrations](operational-migrations.md).
The combined current-controller matrix also passes on clean `b042928` after
alignment to `0ffe9f6`, with unchanged source hashes and complete fixture cleanup.
It exercises the new candidate manual preflight alongside the Go transaction
drain; its receipt explicitly excludes browser execution and background activation.
The dependency closure correctly invalidates all sixty previous manual captures;
those captures require regeneration before being counted as current evidence.

## Latest increment: delivery alongside Django

The [bootstrap increment](instance-bootstrap.md) implements public status/config
and authenticated instance access with 86 cases per backend and four PostgreSQL
projection/plan groups. Manual availability47 and strict bundle CLI19 agree with
Django. Both image contexts receive the same verified release artifact, and Go
packages its own non-root validator, which passes nineteen actual image cases.
Browser acceptance and a genuine required-release artifact rehearsal remain
pending; manual reader/search/assets are separate work.

The Go image also packages explicit native worker and forward-only operational
migration commands. Fourteen race-instrumented process scenarios and seven actual
image scenarios pass. Startup fails on missing/dirty/unknown operational schema;
migrations connect directly to PostgreSQL while the worker retains runtime
pooler routing. The worker registers daily email reset, associate age update and token cleanup,
honors maintenance and drains or recovers unfinished work. The age body now checks
owner/generation/live lease before business SQL in both transaction paths; eleven
body groups, nine native cases and five image cases pass. The existing seven-case
image regression passes on the same immutable image after repairing a transient
SQL observation race in its fixture. Installed worker/scheduler services and
ownership handoff remain open. See [worker evidence](../../../BE_GO/internal/worker_process.md)
and [age process scope](age-worker-process.md).

Install/start/restart/migrate/update and post-swap restore now run the explicit
Go migrator after Django, before resuming application processes, under the
exclusive operations lease. Current-distribution recovery refuses surviving
one-offs. Fresh-install and current-controller failure rehearsals preserve exact
pending intent and Django history, including rollback after dirty-history
rejection during restore and recovery after a killed controller leaves a real
Go migrator running. See [operational migrations](operational-migrations.md).

Ordinary Compose now includes the Go API. Builds and publication include its
image; updater manifests carry an independently validated immutable Go reference
in an extension compatible with the frozen v1.0.17 bootstrap. Target verification
requires Go readiness, the expected image and its embedded version. Backup,
restore and recovery include both APIs, including removal of Go when recovering
an older Compose distribution. Explicit custom Go images prevent managed updates.

Fresh installation, backup and restore pass with both APIs, including a Go-written
preference read by Django and exact restored PostgreSQL audit state. Current and
frozen v1.0.17 controller update/recovery rehearsals pass; the combined historical
controller run also passes thirteen browser checks. The historical controller
uses a current application fixture. Exact scopes and limits are tracked in
[delivery evidence](delivery-transition.md). Installable releases require the
shared Go quality job and a current, completely verified/deprecated inventory;
the inventory gate intentionally rejects this incomplete implementation.

## Fixed versioned proxy routes

Caddy now sends `/api/v1` to Django and `/api/v2` to Go before matching the legacy
Django alias. Exact version roots preserve methods, bodies and queries. Root
Bakney and WebSocket protocols also have versioned aliases; pairing responses
retain restrictive cache/referrer headers. Readiness stays available during
maintenance, other new application requests are blocked, and admitted response
streams can finish. A Go outage does not fall back to Django.

The real pinned Caddy image passes transport contracts for encoded paths/queries,
binary and chunked uploads, errors, cookies, redirects, ranges, HEAD, streaming
and WebSocket frames. The actual Django/Go roots also pass 62 table contracts,
three locked-row races and three health contracts per version, with independent
PostgreSQL/audit readback and retained query/cancellation checks. Eighty-seven
existing self-host tests and eleven reload/browser tests pass. The browser tests
still use their existing synthetic API fixture. See [routing evidence and limits](versioned-routing.md).

Runtime selection, coordinated worker/scheduler ownership, interrupted switching and
WebSocket draining remain required. No production activation or complete browser,
SSO/callback, private-file or realtime parity is established by these checks.

The preceding `6e6d6f9` quality workflow `39733767156` passes completely. Its manual
workflow `39733767137` fails on external dashboard recipe bindings. The reviewed
local companion patch remains unpublished, pending authorization for the separate
repository. Current-head CI remains an independent gate.

Published `149c415` passes the complete [quality workflow](https://github.com/carbogninalberto/assozeta/actions/runs/39852318675),
including Go, backend, frontend, production and current/historical controller
checks. [Manual verification](https://github.com/carbogninalberto/assozeta/actions/runs/39852318676)
and the [release-manual rehearsal](https://github.com/carbogninalberto/assozeta/actions/runs/39852318993)
both stop at the external content recipe review boundary
(`docs/manuale/content-recipes.mjs`). These results cover that published commit,
not the subsequent transaction-drain changes or newer main commits. The reviewed
separate-repository companion changes remain unpublished; manual delivery still
requires passing evidence.

The user additionally requires periodic main alignment and communicating parallel
agents. The initial fetch confirmed `origin/main@fddcee3` was contained. Upstream
subsequently advanced to `65f0535` with a manual-visibility fix. The completed
rebase has exactly the tree of an independently computed clean merge. The subsequent
rebase also includes `941321e`, a manual-delivery goal document without runtime changes.
[Independent reassessment](main-alignment.md) found and repaired pre-SQL audit-index
exceptions that previously left successful partial Django mutations. Six focused
PostgreSQL tests and separate JWT/fresh-connection proof pass, together with
324 existing Django tests and the profile/table/2FA Go/Django HTTP, concurrency
and query checks. Twenty manual recipe modules now bind reviewed current sources;
190 manual Python, eleven manual Node, 163 frontend and three SSO diagnostic tests
pass. The private companion patch changes 507 bindings with all 44 authored MDX
files unchanged; separate-repository publication remains pending.
The follow-up `bc03bb7` also binds the strict helper and index signal to all 29
existing profile/2FA recipe consumers, so helper-only changes invalidate stale
full captures. The expanded 197 Python and eleven Node manual tests pass. Its
updated private patch preserves the same 507-binding/44-unchanged-MDX boundary.

## Implementation and architecture

The root exposes 31 upstream paths: health/readiness, login/refresh/identifier
checks, instance bootstrap, manual sections/assets, profile information/updates/
password/table preferences, 2FA setup/status/updates, two retired software-billing
responses, four payment-category routes, public payment information and the
canonical public logo and four impersonation-management routes. CORS, restore
admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 43 implementing, 5 requiring review and 462 uncharacterized; none
is verified**. Runtime discovery resolves 408 HTTP patterns, four WebSocket paths,
42 tasks, 112 models and 15 MCP schemas. Discovery is not implementation evidence.

The [impersonation-management slice](impersonation-management.md) implements the
four owner/admin routes, shared sessions and exact ACCESS audit/cache ordering.
Native and immutable packaged-image acceptance each pass 264 cases and 64 audits
on Django, Go and both shipped Caddy prefixes, plus four cross-runtime session
rounds. Shortened 60-second sessions prove reads do not renew TTL; actual fallback
rejection checks durable state before rejecting routing. Five live PostgreSQL
groups prove bounded reads, audit projection and cancellation, while real Redis
GET/SET/DEL failures verify partial effects and explicit recovery. All 128 managed
models and Redis database 1 excluding readiness keys are checked. Full Go race
and vet checks pass. The image is bound to the modified source context and embeds
its baseline revision; committed-image CI remains separate. Browser, concurrent
mutations, historical data and activation acceptance remain open.

The [public logo](instance-logo.md) has dedicated transport/service/storage
boundaries and retains the S3 default-storage selection, candidate precedence,
preparation failure fallback, exact bytes and streaming encoding behavior.
Response preparation owns four bounded slots and anonymous open files. Packaged
storage tests pass exact 65 MiB and four concurrent 20 MiB bodies, cancellation,
SIGKILL and same-volume restart. Native/versioned S3 contracts pass 76 cases per
runtime, and both filesystem adapters pass 64. Real kernel unlink denial is
tested before payload consumption. The [storage trust correction](storage-trust.md)
passes 34 component and native/packaged HTTP cases per runtime, with pinned
certifi roots and separately characterized proxy rules. Broader provider behavior,
installed PWA behavior and operator selection remain open; no full-parity claim
is made.

The [public PWA manifest](instance-manifest.md) now has dedicated Go transport,
service and five-column repository projection. Actual Django/Go pass 42 new
manifest cases within 128 bootstrap cases, including defaults, Unicode branding,
ignored authentication headers, sessions and restore/negotiation behavior.
One-query read-only PostgreSQL projections remain bounded through 10,000 rows;
actual blocked-query cancellation releases the reader before its blocker.
All 42 manifest cases also pass through each shipped Caddy prefix, with observed
upstream identity, fallback rejection and actual gzip/header checks. The packaged
manifest runner independently passes the same 42 cases on Django, the immutable
Go image and both prefixes, checking all 128 managed models, SQL, sessions and
cleanup. A preimplementation image fails with actual404; a rolled-back INSERT
control proves the SQL observer detects non-durable writes. The fixture holds
real restore locks separately in each runtime; cross-kernel sharing is not
claimed. Native PostgreSQL tests remain a separate required gate.

The [unchanged browser consumer workflow](instance-pwa.md) passes stock/v1/v2
open and actual reload against committed image `9188744`, including Chromium's
native manifest parser and a visible decoded 512-by-512 logo. Separate manifest
and logo misroutes return real HTTP 200 but fail the same upstream validator used
by positive workloads. All 128 managed models, full owned S3 state and Redis are
preserved; source hashes and cleanup pass. Timeout regressions also prevent
private Docker fixture arguments from appearing in exception tracebacks. Installed
PWA, OS icons, offline behavior and runtime operator selection remain open.

The [public payment-info implementation](payment-info.md) passes 137 authored
cases on native Django/Go and both shipped Caddy routes, with exact managed state,
zero user-table PostgreSQL writes and cleanup. Independent runtime observation rejects a
deliberate v2 fallback. Six real PostgreSQL groups prove one hydration query plus
one metadata batch through 128 distinct IDs, exact money/integers and cancellation
release before blocker removal. JSONField fractions and NaN errors retain the
measured Django wire behavior. The corrected nested installment association
boundary retains its TDD evidence and 190 existing Django regressions. Public
disclosure, malformed group fail-open and unscoped metadata remain review gaps;
enabled checkout/history/installed acceptance remains open and the ledger stays needs_review.
The revision-bound non-root/read-only Go image also passes all four 137-case
workloads. Native and image reports remain separate. The corrected write guard
checks all 128 user tables and has seven actual PostgreSQL negative/control
cases; catalog maintenance remains diagnostic. See the linked evidence for the
locally reproduced hosted counter failure and its complete catalog attribution.

The unchanged checkout UI separately passes eight authored loading-summary and
revisit checks per API version, followed by eight real disabled-Stripe409 responses
each. Runtime observation rejects false Go routing, all 128 managed models
including auto-created M2M tables remain unchanged, and owned cleanup passes.
Stripe and manifest/logo/health traffic retain explicit Django coexistence in
this browser fixture. This proves rendering and disabled-payment rejection;
enabled provider checkout and approved public-access policy remain open.

The [payment-category slice](payment-categories.md) implements all four Go routes
against the repaired Django identity/association and atomic-audit baseline.
Actual native Django/Go and shipped Caddy versioned runs each pass 188 HTTP cases,
43 exact audit rows and 18 SQL-failure rollback scenarios, including full durable
state, seeded references and observed-blocking concurrency histories. Separate
independent parent probes add six faults and six successful recoveries each for native
Go and Caddy v2: missing-content-type application errors leave PostgreSQL healthy
but require rollback, while deferred constraint failures are observed at actual
blocked COMMIT. Exact managed-state rollback, recovery audits/indexes and cleanup
pass; both ignored-error overlays are rejected. Go has no request-reachable ORM
constructor at Django's injected pre-SQL failure point, so this is distinct
coverage of distinct failure paths rather than reproduction of that seam.
Six real-schema Go groups pass without skips; LIST uses two
repository queries through 128 distinct VATs and blocked-query cancellation joins
rollback before recovery. Nineteen focused and 188 existing Django baseline
regressions remain evidence for the intentional correction. All four ledger rows
stay `implementing`. Nine native lifecycle probes per runtime now cover profile/
category overlap, owner deactivation/deletion denial and recovery. A real HTTP
disconnect rolls Go back before lock release, while Django completes the admitted
synchronous deletion once after release: the distinct expectations pass, but
`disconnect_semantics_match` and complete category parity remain false. Independent
parent Caddy socket probes retain the same distinction, observe actual blocked
SQL and reject deliberate v2-to-Django misrouting. The
non-root, read-only packaged image passes the same four 188/43/18 workloads with
immutable identity and revision/version/health checks. The independently rerun
unchanged category browser passes six checks, five writes, seven denials and five
exact audits per version, including Django traffic observation and a deliberately
misrouted v2 negative control. A fresh installed backup/restore rehearsal also
preserves exact category/VAT/audit/reference state through corrupt-archive
rejection, actual post-swap rollback and valid restore, with no Django fallback
when the disposable Go upstream stops. Historical data, process shutdown,
ambiguous-response recovery and activation remain open. The transaction probes
do not establish arbitrary infrastructure or panic failures.
Local verification also corrects two test-harness failures: development Silk writes
are removed from the category fixture without dropping managed-table assertions,
and the bootstrap large-row seed evaluates each conversion once with explicit
row counts and separate setup/read deadlines. All 13 integrity tests and bootstrap's
86 HTTP/four PostgreSQL checks pass. All 15 jobs in hosted quality run
`40033677362` at `f2e4957` pass, including the four split Go jobs and legacy
upgrade. That revision includes the 109-case Django payment-info baseline; the
Go implementation then ran in hosted quality `40037197739` at `c742cd2`.
Every other substantive job passed, but the payment job and aggregate failed the
whole-database DML counter after all four 137-case workloads and six PostgreSQL
groups passed. The parent reproduced and corrected catalog contamination as
documented in [payment-info evidence](payment-info.md). Published `77adbc7` then
passed all 15 jobs in quality run `40039675776`; its safe artifact independently
confirms the stats/native/image and category transaction-failure gates on Linux.
That result excludes later work. Published `1539245` now passes all 15 jobs in
quality run `40043636123`, with independently audited native128/128 bootstrap,
42-case versioned manifests, PostgreSQL bounds/cancellation, checkout browser and
packaged manifest42-by-four receipts. Foundation artifact `12540946983` binds its
Linux/amd64 image to PR merge `9725e9cf3a28d8694edf5dbf99fe28039d47ba6f`; it is
distinct from local arm64 evidence. Separate manual runs `40043635989` and
`40043636014` fail the recipe-binding gate and are not passing acceptance.

The bootstrap contract validator also now rejects JSON boolean/number aliases
and non-integer write counters. Pure failing regressions precede the fix. Its
stricter comparisons pass when reapplied to the exact committed153 fixture and
downloaded native/versioned/image observations. That is assertion revalidation,
not another runtime execution or expanded endpoint coverage.

Published `2715eed` subsequently passes all 15 quality jobs in `40046002650`.
Foundation artifact `12541833237` independently passes the exact committed
strict validator/fixture: native128/128, versioned42/42, both PostgreSQL groups
and four bounds subcases, and packaged42-by-four. All 11 packaged source hashes,
observer42/0/42/0, actual200 fallback rejection, SQL observer controls, 128 model
snapshots and cleanup pass. Its Linux/amd64 image revision is PR merge
`9eb3ec45e00c4276fa42f356a1bb31f19d4bb0c4`. This excludes later logo work.
Manual release `40046002082` and MCP `40046002042` separately fail the reviewed
content-recipe binding gate.

The later `9188744` foundation run stops before logo HTTP execution because its
pinned MinIO image was not pulled on the fresh runner. Native bootstrap/manifest
checks pass; packaged manifest, spool and kernel checks are skipped. The workflow
fix pulls the exact pinned dependency before the logo step. Separate local tests
of the clean committed image pass the full spool and browser checks; this does
not convert the failed hosted run into a success. Its manual runs `40049170440`
and `40049170500` also fail the recipe-binding gate.

At `07507b8`, hosted browser job `120745204244` fails before PWA navigation:
schema setup drops the configured Playwright cache path. The adapter now preserves
its browser environment before schema setup. A real local nondefault-cache replay
passes all five workloads, with recorded executable paths; a separate missing-cache
control fails without default-cache fallback and cleans up. That foundation run
was canceled after bootstrap and partial logo observations; it has no complete
logo cleanup receipt. Its manual runs `40051561938` and `40051561934`
separately fail the content-recipe binding gate.

At `15743d5`, the hosted PWA matrix, account and payment jobs pass. Foundation
passes native logo 76-by-four plus both 64-case disk adapters and packaged
manifest 42-by-four, then exceeds its 30-minute job budget during spool checks.
The spool receipt is initial-only and kernel unlink is skipped. An owned Linux
probe identifies the fixture's aggressive receive-buffer shrink: only 589,824
bytes arrive in 15 seconds, while default buffering still holds the response
under backpressure and then drains 65 MiB. The corrected fixture retains the
resource assertions, adds a total body deadline and atomic phase receipts, and
passes the actual Compose matrix in 14.002 seconds. Manifest/spool/unlink checks
move to the mandatory storage job using its fresh image, preserving the release
gate. Local proof is Linux/arm64 with a macOS client; hosted Linux/amd64 acceptance
of the correction remains pending.

Current manual release `40052417456` and MCP `40052417495` stop at recipe binding.
Exact companion `9d9cffd` has no binding mismatch against `main@79a9460`, but this
branch invalidates 375 sections across 14 recipe modules. This is branch-caused
evidence drift, not a preexisting main binding failure. A separate local-only
candidate reviews all affected claims, preserves the other 132 bindings, and
passes all 507 authoring bindings and 46 offline checks. Only one Italian
paragraph changes to clarify manual-link visibility. Parent validation against
application `2ed5591` confirms all 507 bindings, the unchanged 132 binding objects
and unchanged reviewed recipe/source paths. Candidate `ec847aea` is committed
locally with the agent identity. Its disposable release rehearsal passes all 60
fresh captures, 43-page compilation, actual Mintlify rendering, MCP/cited-answer
evaluation and embedded checks covering 434 compatible public sections and 982
image references. The 73 explicit coverage gaps remain. Packaging succeeds with
no external actions, but appliance startup fails because the smoke harness stages
only Django/web/renderer and omits the newly required Go image. Owned runtime
resources are removed. A source-owned rehearsal repair and installation/upgrade/
rollback acceptance remain required; the companion commit has not been pushed
or published.

Hosted `2ed5591` quality run `40056270692` passes all 16 jobs, including storage,
including native/packaged TLS, packaged manifest, corrected spool resource and
kernel unlink checks. Artifact `12546211090` records Linux/amd64 spool completion
in 10.686 seconds with 128-model preservation, matching source hashes and cleanup.
Foundation finishes in 21 minutes 28 seconds, with bootstrap/manifest/logo/disk
receipts in artifact `12546082869`; browser, accounts and payment also pass.
Aggregate job `120764475269` completes successfully. This resolves
the earlier spool timeout without treating the canceled `15743d5` run as passing.

The architecture follows specification `4d0f68d` and inspected Kuayle revision
`5025d2a6bc966d0d08c49832ff9c7c86184207cd`: one Assozeta-owned Go module, Echo transport/DTOs,
context-aware services, explicit constructors and sqlx/pgx repositories. Actor,
effective user and resource scope are distinct. The durable job repository is an
explicit Assozeta extension with its own additive operational migration history;
the normal lifecycle now applies it through a separate command under its lease.
**Django owns existing business migrations and
production workers/scheduling.** No schema rename, customer identity regeneration,
production deployment or release publication has occurred.

Implemented service transactions cover credentials, profile/table preferences,
username/audit/index writes, card configuration, login finalization, 2FA changes
and category/VAT/audit/index mutations. JSON audit data is explicitly supplied
as text for the root's transaction-pooler-safe pgx mode. Earlier committed effects
remain after later login failure, but credentials are delivered only after success.
Go-only creation locks do not coordinate unmodified Django writers. Deliberate
atomic rollback differs from Django's partial effects on some exceptional failures;
those differences remain explicit activation gaps.

## Evidence available for review

| Area | Executed evidence and practical limit |
| --- | --- |
| Public login | 19 ordinary and 5 rate methods; 41 credential/effect scenarios; 16 password and 16 cross-identity social concurrent requests per backend; exact response/cookie/signature and persistent audit/index/card readback. Complete login remains open. |
| Identity and credentials | Bidirectional Django/Go hashes/JWTs; 54 cache-session cases; 132 scoped-request and 67 resource-scope cases; 37 refresh, 16 identifier, 10 2FA-status and 10 retired-billing HTTP methods per backend. These do not prove authorization on every endpoint. |
| Profile information | 56 HTTP scenarios, 88 concurrent and four fresh-state reads per backend across eight registration modes. Exact effective/owner fields, actual image conversion, query/pool bounds, foreign lookup denial and cancellation; all image formats/frontend workflows remain open. |
| Table preferences | 62 HTTP/effect/audit cases and three locked-row races per backend, now also through each versioned API. Fixed mutation-query counts, 24 competing writers, atomic audit/index rollback and cancellation recovery; invalid Unicode surrogates preserve rejection. Full frontend/lifecycle parity remains open. |
| Profile mutations | 52 HTTP/effect/audit cases and four locked-row scenarios per backend; selected personal/association writes and audits are atomic. Responses retain each writer's committed personal fields. Cross-endpoint races involving other serialized preferences remain open. |
| Password changes | 67 HTTP/effect cases, three locked scenarios per backend, four Django/Go profile/password writer combinations and four cross-runtime login flows. Credential-only writes preserve other columns; full frontend/lifecycle parity remains open. |
| 2FA writes | 50 HTTP/effect and two locked-row concurrency scenarios per backend; atomic failure rollback, correct impersonation audit, query/pool bounds and cancellation. Setup has separate evidence below; full frontend activation remains open. |
| 2FA provisioning | 31 HTTP scenarios, 62 concurrent requests per backend, 512 decoded symbols, four cross-runtime update flows and four fresh-login flows. No setup persistence; entropy/encoder failures and cancellation covered. Full browser/phone interaction remains unverified. |
| Public payment information | 137 authored cases each on native Django/Go and Caddy v1/v2; exact state, zero user-table DML, runtime observer and fallback rejection. Six PostgreSQL groups prove one/two service queries through 128 distinct categories and blocked-reader release before blocker removal. Integer/Decimal and fractional JSONField semantics are distinguished; NaN errors match. The immutable non-root image independently passes the same four workloads; native repository checks remain separate. Seven PostgreSQL guard regressions distinguish catalog maintenance from all-user-table writes. The unchanged browser passes eight summaries/revisits and actual disabled-Stripe409s per version, with all128 managed models/M2M unchanged. Enabled checkout, public disclosure policy and historical/installed acceptance remain open. See [payment-info evidence](payment-info.md). |
| Payment categories | 188 HTTP cases, 43 exact audit rows and 18 SQL fault scenarios each for native Django/Go and Caddy v1/v2; exact durable/reference preservation and observed-blocking PATCH/PATCH-DELETE histories. Six real-schema PostgreSQL groups; two repository LIST queries at 1/8/32/128 VATs and blocked-query cancellation. Parser: 113 golden cases and 15,992 pinned-runtime comparisons. Nine native lifecycle probes per runtime pass owner/profile races and audited recovery; actual HTTP disconnect cancels Go but allows Django admitted sync completion, so protocol parity stays open. The immutable non-root/read-only image passes four 188/43/18 workloads. Both unchanged browser workflows pass six checks/five writes/seven denials/five audits each, with independent Django observation and a routing negative control. Installed category backup/restore passes actual post-swap rollback and valid restoration with four references retained. Separate native-Go/Caddy-v2 probes pass six application-error/deferred-COMMIT faults and six recoveries each, with actual blocked COMMIT, exact rollback and rejected ignored-error overlays. Django’s injected ORM-constructor seam has no request-reachable Go counterpart; historical data, shutdown, ambiguous-response and activation gaps remain. See [category evidence](payment-categories.md). |
| Providers and environment | 89 Apple and 93 Google component HTTPS/signature cases, supplemented by the public root fixture. 103 environment comparisons use actual Django settings and requests CA precedence. Trust directories/rotation, default roots, full proxy/JWK-platform policy and live browser/provider flows remain open. |
| Query and concurrency bounds | Real PostgreSQL/Redis tests measure batched lookups, fixed connection pools, concurrent identities and query counts across growing datasets. Critical job claiming uses measured indexed plans. No representative whole-application performance improvement is claimed. |
| Jobs | Transactional intent, deduplication, leases, crash recovery and executor drain have private PostgreSQL evidence. The durable runtime owner defaults to Django and fences Go claims/outcomes by generation; daily reset and age update lock ownership before writes. Thirty-eight queue/executor/ownership/migration groups and the five task bodies' 51 groups pass. Worker/scheduler commands are packaged with scoped process/image evidence; installed activation, remaining tasks and coordinated handoff/restore remain unfinished. |
| Images and storage | Eighteen image pipelines have current Pillow 13 oracles, including shipped codec/pixel/Base64 checks. TIFF/AVIF gaps are explicit and non-exhaustive. Storage configuration/credential-provider and disposable MinIO checks are scoped evidence; destructive medical cleanup remains unwired. |
| Operations | Restore HTTP admission, shared lifecycle locks, dependency loss/recovery, idle SIGTERM and non-root image checks have evidence. Dual-API fresh install, backup and restore pass. Complete historical restore, active-request/task/WebSocket draining, release/activation acceptance and backend switching remain open. |

Current reference: Python 3.13.16, Pillow 13.0.0, Django 5.2.18, DRF 3.17.2 and
SimpleJWT 5.5.1, reconciled with `main@0ffe9f6`. Tests use disposable PostgreSQL,
Redis, synthetic identities/keys and private provider fixtures. Current-model
`migrate --run-syncdb` does **not** verify historical migration chains. No skipped,
mocked-away or unexecuted required behavior counts as parity. Detailed component
contracts, historical checkpoints and local evidence paths are maintained in the
[evidence index](README.md), [ledger](ledger.json) and component documentation.

## Completion audit against the accepted scope

| Requirement | Remaining acceptance work |
| --- | --- |
| 1. Authoritative scope | Characterize remaining declarations and reconcile 21 discovered schedules with effective/historical database schedules. |
| 2. Kuayle architecture | Apply the established boundaries to all remaining business, realtime and operational implementations. |
| 3. Existing database | Complete model hooks, financial invariants, historical-data and rollback readback; resolve mixed-runtime write races. |
| 4. Authentication/permissions | Finish remaining auth routes, global middleware, endpoint/resource denials and credential/resource sizing. |
| 5. Versioned routing | Implement validated compatibility selection, real callbacks/links, worker ownership and safe drain. Fixed proxy paths and the Compose Go upstream have scoped evidence. |
| 6. Existing environment | Finish provider/proxy, logging, manual/instance and remaining configuration compatibility. |
| 7. Background processing | Finish every supported task/schedule, production commands, interoperability and ownership transitions. |
| 8. Realtime/integrations/documents | Implement all supported WS and provider flows, private files, documents and immutable manual/MCP delivery. |
| 9. Historical backups | Fixed historical fixtures, imports/exports, interrupted restore/recovery and coordinated rollback. |
| 10. Independent tests | Run shared contracts across both versioned APIs and the full unchanged-frontend/lifecycle matrix. |
| 11. Performance | Representative query plans, datasets, concurrent read/write/export workloads and comparable benchmarks. |
| 12. Release/update delivery | Finish update/recovery acceptance, Go worker/scheduler delivery, multi-architecture verification and coordinated activation/rollback. API delivery and the incomplete-ledger release gate are implemented. |
| 13. PR deliverables | Keep this PR draft until implementation, operator procedures, ledger evidence and applicable checks are complete. |
| 14. Completion audit | Every requirement above remains an acceptance gate; passing a component does not close the migration. |

Software subscription gating, platform checkout and SaaS nurturing stay retired.
Association/course memberships, payments and supported communications remain in
scope. Historical backup skips remain limited to `BillingPlan` and
`PreviewAndCustomFeatures`; missing Go support cannot expand that allowlist.
Existing installations must retain Django until explicit, verified activation.
