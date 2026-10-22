# Maintainer report — incomplete Go migration

This draft retains all fourteen [accepted requirements](requirements.md).
**It is not ready for production activation or merge as a backend replacement.**
Work is isolated on `codex/go-backend-parity`, reconciled through
`main@0ffe9f6`. Django retains production API selection, business-schema,
worker and scheduler ownership. No production/customer data or published release
has been changed.

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
scheduler, cardinality/backlog policy and Celery handoff remain open; see
[publication limits](../../../BE_GO/internal/service/occurrence.md) and
[page bounds](../../../BE_GO/internal/service/schedule_page.md).

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
The dependency closure correctly invalidates all sixty previous manual captures;
those captures require regeneration before being counted as current evidence.

## Latest increment: delivery alongside Django

The Go image also packages explicit native worker and forward-only operational
migration commands. Fourteen race-instrumented process scenarios and seven actual
image scenarios pass. Startup fails on missing/dirty/unknown operational schema;
migrations connect directly to PostgreSQL while the worker retains runtime
pooler routing. The worker registers daily email reset only, honors maintenance
and drains or recovers unfinished work. No scheduler, installed worker service
or ownership handoff is implied. See [process evidence](../../../BE_GO/internal/worker_process.md).

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

The root exposes fifteen upstream paths: health/readiness, login, refresh, email and
username availability, scoped profile information/profile and password changes, table preferences and 2FA setup/status/updates, and two retired software-billing
responses. CORS, restore admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 22 implementing, 6 requiring review and 482 uncharacterized; none
is verified**. Runtime discovery resolves 408 HTTP patterns, four WebSocket paths,
42 tasks, 112 models and 15 MCP schemas. Discovery is not implementation evidence.

The architecture follows specification `4d0f68d` and inspected Kuayle revision
`5025d2a6bc966d0d08c49832ff9c7c86184207cd`: one Go module, Echo transport/DTOs,
context-aware services, explicit constructors and sqlx/pgx repositories. Actor,
effective user and resource scope are distinct. The durable job repository is an
explicit Assozeta extension with its own additive operational migration history;
the normal lifecycle now applies it through a separate command under its lease.
**Django owns existing business migrations and
production workers/scheduling.** No schema rename, customer identity regeneration,
production deployment or release publication has occurred.

Implemented service transactions cover credentials, profile/table preferences,
username/audit/index writes, card configuration, login finalization and 2FA changes. JSON audit data is explicitly supplied
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
| Providers and environment | 89 Apple and 93 Google component HTTPS/signature cases, supplemented by the public root fixture. 103 environment comparisons use actual Django settings and requests CA precedence. Trust directories/rotation, default roots, full proxy/JWK-platform policy and live browser/provider flows remain open. |
| Query and concurrency bounds | Real PostgreSQL/Redis tests measure batched lookups, fixed connection pools, concurrent identities and query counts across growing datasets. Critical job claiming uses measured indexed plans. No representative whole-application performance improvement is claimed. |
| Jobs | Transactional intent, deduplication, leases, crash recovery and executor drain have private PostgreSQL evidence. The durable runtime owner defaults to Django and fences Go claims/outcomes by generation; daily reset locks ownership before writes. Thirty-eight queue/executor/ownership/migration groups and the five task bodies' 48 groups pass. Production worker/scheduler delivery, remaining tasks and coordinated handoff/restore remain unfinished. |
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
