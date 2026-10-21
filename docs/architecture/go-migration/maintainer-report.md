# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@06bac65` including PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

## Current result

The Go composition root exposes `/healthz`, `/readyz`, `/oauth2/refresh-token`,
`/oauth2/check/email`, `/oauth2/check/username`, `/two-fa/info`, and the two legacy
software-billing tombstones. Refresh, identifier, 2FA and retired-billing routes
compose existing-session audit middleware with PostgreSQL/Redis and the configured
SECRET_KEY. Identifier checks preserve the existing public disclosure, inactive/
deleted filtering and duplicate selection behavior.
The root now admits CORS preflight before routing/session/authentication using the
existing environment settings, then takes the shared Django/CLI restore lease
and applies current Django security response headers. Configured unsafe requests
also reject globally queued/running restore receipts before session/authentication. Remaining global middleware is still incomplete.
Login has tested services, providers, transport and rate-admission components but
is not registered publicly. Most business workflows and every activation gate
remain unfinished. There are 514 ledger declarations: 4 deprecated, 14 implementing,
6 requiring renewed review and 490 uncharacterized; none is verified. Runtime
discovery resolves 408 HTTP patterns, four WebSocket paths, 42 tasks, 112 models and
15 MCP schemas. These discovery counts are not counts of implemented capabilities.

The [ledger](ledger.json) and [evidence index](README.md) retain detailed ownership,
contracts, source references, test evidence and limitations. Source discovery's
21 schedules still need reconciliation with the single effective initialized Beat
entry and historical database schedules.

## Architecture and data decisions

The implementation follows the inspected specification at `4d0f68d` and its pinned
Kuayle revision `5025d2a6bc966d0d08c49832ff9c7c86184207cd`: one `BE_GO/` module,
Echo handlers, DTOs, context-aware services, explicit constructors and sqlx/pgx
repositories. Infrastructure/provider clients have explicit interfaces. The durable
job repository is an explicit Assozeta extension, following the pinned operation
lease pattern. Its additive migration owns only Go operational tables and a
separate golang-migrate history; it is not invoked by the application or updater.
No Go migration changes existing business tables. Redis interoperability is implemented
only for specifically characterized cache protocols, not assumed for Channels or
Celery payloads.

Actor, effective account and resource scope remain distinct. Service transactions
cover the implemented username, card-configuration and final-login audit/index
invariants. Real PostgreSQL tests measure query counts and bounded-pool concurrency.
Go-only creation locking does not coordinate unmodified Django writers; mixed-runtime
creation races and differences from Django's partial autocommit failures remain open.
No monetary business workflow is claimed as complete.

Current-main deprecations remain intact: software subscription gating, platform
checkout and SaaS nurturing are retired; association/course memberships, payments
and supported communications remain in scope. Historical backup skips remain
exactly `BillingPlan` and `PreviewAndCustomFeatures`; no Go implementation gap
expands that allowlist. Go has not yet implemented historical restore or the
immutable manual-package boundary delivered by PR #20.

## Verification and its limits

| Area | Evidence and scope |
| --- | --- |
| Environment | 87 declared comparisons execute unchanged selected settings assignments with django-environ 0.12.0, then Go Load. They cover literal files, process precedence, provider/JWT references, required/default/cycle rules, multiline/base64, CSRF/CORS lists, DEBUG, derived rate/CORS flags and required SECRET_KEY. Secret representations and process isolation are checked. Full environment coverage is unfinished. |
| CORS | 51 portable HTTP methods per backend across five policies, 40 declared URL comparisons and four actual-application-root methods preserve origin policy, preflight bypass, body bytes, Vary, streaming, concurrency and malformed-list error timing. Model/session readback stays unchanged. Five actual Go preflight paths still work with PostgreSQL and Redis unavailable. Complete global middleware and versioned/browser routing remain open. |
| Security headers | Six portable methods per backend verify current Django defaults, preservation of explicit/empty fields, response errors, streaming, forwarded protocol, concurrency and CORS preflight bypass. Five actual-root methods verify health, missing Bearer, CSRF/method denials, missing route and preflight. Full trusted-host/restore execution/proxy policy remains open. |
| Restore HTTP admission | 42 portable methods per backend cover six modes on real Django ASGI and the compiled Go root, with unchanged model/cache state. Shared/exclusive flock interoperability, in-flight HTTP, panic release and SIGKILL recovery pass in both process directions. PostgreSQL checks one SELECT per unsafe admission over 1/16/128/512 receipts, no pending SELECT for reads, and 32 concurrent admissions in four connections; EXPLAIN uses the existing partial index. Full restore execution/upload/recovery, stale tasks, worker/scheduler and stream/WS coordination remain open. |
| Durable job repository | Ten private PostgreSQL scenario groups cover atomic intent/completion, permanent scoped deduplication, renewal/expiry/fencing, bounded attempts/backoff, pending cancellation, 16 publishers, eight consumers, SIGKILL recovery and four-connection query bounds. One claim query across 1/16/128/512 rows; the actual 4,096-job plan uses bounded indexed branches after a failing scan test. Four concurrent migrators preserve a separate additive history. This uses synthetic business/history tables, not historical Django migrations. No application task or production process/schedule is activated. |
| Job executor | Thirteen real PostgreSQL groups verify service-owned completion, explicit failures/retry, heartbeats, stale-token/blocked-database cancellation, maintenance attempt preservation, stale restores, joined concurrency and cooperative shutdown. Thirteen actual Django `CoordinatedTask`/Go comparisons preserve global receipt and publication rules, eager Retry metadata and unchanged managed model/cache state. This does not migrate task bodies or prove Celery broker interoperability, a production worker command or scheduler ownership. |
| Rate admission | 14 bidirectional Django/Go Redis counter cases check keys, inclusive windows, thresholds and nonrenewed TTLs. Sixty-four concurrent Go calls admit five and deny 59 using 127 writes and at most four connections. Actual-key expiration races, paused Redis deadlines, no retries and pool reuse pass. |
| Rate HTTP boundary | 15 portable contracts per backend run actual Django key functions/decorators/handler403 and Go middleware on private Redis. The terminal view is controlled to verify body bytes, not credentials. Keys, representations/casefold, ordering, exact 429, method/disable bypass and nonfinite/integer rules pass. Public/global middleware composition and parser/body extremes remain open. |
| Credentials and refresh | 76 Django-to-Go JWT vectors, password-hasher interoperability and 36 portable refresh methods per backend on actual Django ASGI and the Go executable. Refresh reserializes/signs unchanged claims/JTI/expiry. Denied refresh issues no BKN_AUTH; stale-session deletion may accompany it. Session identity cannot replace Bearer/scope or token identity, and 32 mixed concurrent requests agree. Nonstandard JWT numbers, lone surrogates and aggregate parser budgets remain open. |
| Retired software billing | Ten portable methods per backend retain 410 for association scope across legacy plan states, 403 for other roles and all current authentication/impersonation/resource restrictions. Unused checkout bodies remain unparsed; scoped references retain validation. Model/cache readback stays unchanged. PostgreSQL tracing excludes legacy billing queries; 32 mixed calls use 40 admission SELECTs and four test connections. These are compatibility tombstones; association/course payments remain supported scope. |
| 2FA status | Ten portable HTTP methods per backend verify current scoped-account status, required Bearer, collaborator permissions, administrator/association impersonation, foreign references, methods/negotiation, malformed sessions and 32 concurrent requests. Same credentials observe two flag changes per backend. PostgreSQL checks one/two admission SELECTs and zero extra status queries; 32 mixed reads use 48 SELECTs with four database connections. Setup/update remain open. |
| Public identifiers | 16 portable contracts per backend on actual Django ASGI and the Go executable cover email/username availability, duplicate UUID ordering, get_user presence/fallback, Unicode limits, REST/session/identity precedence and 32 concurrent requests. Managed tables and malformed-session bytes remain unchanged. PostgreSQL tests preserve one/two SELECT bounds across up to 512 duplicates and 32 concurrent calls in a four-connection test pool. Actual plans show sequential scans; no latency improvement is claimed. |
| Account/permission services | Real PostgreSQL/Redis comparisons cover actor admission, identity transitions/revocation, collaborator policy and resource predicates, including 132 scoped-request and 67 resource-scope cases with measured batch/concurrent query bounds. This is not endpoint-wide authorization parity. |
| Cache-session identity | 43 declared cases and five same-account transitions compare actual Django session/authentication/audit middleware with Go on PostgreSQL/Redis. Current state, backend-specific inactive/deleted actors, password revocation, unchanged cache bytes/TTL and managed-model readback agree. Thirty-two concurrent resolutions use 32 SELECTs/cache reads and pools of four. Response bounds, Redis deadlines/reuse and failures are checked. HTTP behavior has separate evidence below; writes/rotation and remaining route wiring stay open; public refresh is composed. |
| Session HTTP audit | 27 portable contracts per backend use shipped-style ASGI/Uvicorn and concrete Go session repositories around a controlled terminal. Cookie/Vary rules, Latin-1 headers, actor separation, streaming and 32 concurrent mixed-actor/CID requests agree. Sixteen malformed cache/UUID/hash inputs retain exact production HTML 500, Cookie Vary and unchanged cache bytes. Seven rolled-back database cases retain exact audit-port values and column constraints. Session writes/rotation, complete error/global middleware policy and remaining route wiring stay open; public refresh has separate evidence. |
| Login services | 68 password-selection cases, 47 OTP cases, 20 normalization/audit cases, 33 persisted social-configuration cases, 41 social admissions and 26 composed sessions. Profile/card/response/finalization cases compare durable writes and Django readback. Public login and full image dispatch remain unfinished. |
| Providers | 89 Apple and 93 Google cases use actual Django libraries, ephemeral HTTPS and real signatures, with declared identity/error/request expectations. Client bounds, cancellation and configuration changes have separate tests. Live-provider and remaining raw-input/transport evidence are open. |
| Images | Independently declared Pillow/Django comparisons cover implemented pixel, metadata and file pipelines. The domain LoginImageProcessor and complete format dispatch remain unimplemented; partial codec evidence is not successful public-login evidence. |
| Operations | Actual Go health contracts, PostgreSQL/Redis loss/recovery and idle SIGTERM pass. Non-root image and existing Django/self-host/manual rehearsals have scoped evidence. Active-request/task/WebSocket draining, Go release delivery and backend switching remain unverified. |

The application oracle now requires Python **3.13.2**, matching the production
Dockerfile. The earlier local 3.12.6 interpreter accepted malformed bracketed
origins that CI 3.12.15 rejected; production 3.13.2 also has different array/object
trailing-comma JSON diagnostics. Explicit expectations and Go parsers were corrected
after failing comparisons. Forty URL cases also pass in the shipped-style Django
image. CI pins the production interpreter and checks it against the Dockerfile.
The preexisting disposable Docker fixture adapter is preserved.

The complete production-interpreter accounts rehearsal passes, including 42
restore-admission, 36 refresh, 16 identifier, 10 2FA and 10 billing methods per
backend; 43 cache-session cases and 27 controlled-terminal session methods;
four actual-root CORS and five security-header methods; and all existing image,
login, scope and audit-write checks. Results are retained under
`quality-reports/go-migration/restore-admission/full-accounts-results.json`.
Fixtures use current-model `migrate --run-syncdb`, not historical migrations or
real frontend switching.

Full Go race/vet, tagged restore/middleware/config vet, formatting, actionlint,
12 inventory tests and catalog drift checks pass. The actual-server health
rehearsal passes startup, PostgreSQL/Redis loss/recovery, five preflights with both
dependencies unavailable and idle SIGTERM. The 87 environment declarations pass
on production Python. Bidirectional credentials, 38 collaborator policy cases,
89 Apple and 93 Google signature/HTTPS cases, impersonation cache, rate admission
and security headers also pass on that interpreter. The Linux image builds, runs
as UID 10001 and passes its image pipeline rehearsal. All 68 Unicode decimal-zero
blocks were checked against production Python 3.13.2 / Unicode 15.1.0. These are
scoped checks, not the full activation matrix.

The billing contracts first passed on actual Django ASGI/Uvicorn and failed
against unregistered Go routes. They now pass on both real servers. Stored plan
states do not change the 410 response; roles, impersonation and foreign references
remain enforced. Ordinary checkout leaves unused bodies unparsed, while scoped
requests retain parser/reference denials. Managed models, prepared legacy
entitlements and valid/malformed session bytes remain unchanged. Fixture cleanup
restores preexisting state; general session hash revocation remains supported.

A shared protected REST transport helper retains 2FA admission behavior. The
billing service has no entitlement/payment/provider dependencies and reads only
resolved scope. PostgreSQL tracing forbids legacy billing-table queries: one/two
SELECTs establish current account/scope, the service adds none, and 32 mixed calls
use 40 SELECTs within four test database connections. These are scoped service
measurements, not entire middleware query counts or latency improvement claims.

Local CORS evidence is under ignored `quality-reports/go-migration/cors/`;
security-header evidence is under `quality-reports/go-migration/security-headers/`.
Both preserve initial failing Go results after passing Django expectations. The
published `530c791` Go foundation job failed at the CORS oracle because CI's
Python 3.12.15 rejected malformed brackets accepted by local 3.12.6. Its overall
quality/manual runs (39664404614 and 39664405326) were still running at this
checkpoint; they are not counted as successful. The `3260727` Go foundation job
passed, but both complete workflows (39661523420 and 39661523346) were cancelled,
as were the `d7a58ba` and `adbb728` workflows. The last jointly successful whole
quality/manual baseline remains `dbf1beb` (39593769742 and 39593769720).
Full access/import/lifecycle/manual workflows that call these tombstones still
need Go implementation and verification; see
`BE_GO/internal/handler/retired_billing.md`.

The latest durable-job increment passes ten explicitly required PostgreSQL
scenario groups with no missing/skipped tests. Local Go race/vet, tagged queue
vet, inventory/drift, actionlint, formatting, health/dependency recovery and the
non-root Linux image build pass. Embedded SQL files are present in that image;
no deployment invokes the migration yet. The existing full accounts and provider
evidence above comes from the preceding restore/runtime increment. The new
queue is unused by those application paths; its CI step runs independently.
Published `ba38c00` quality/manual runs (39667678239 and 39667678229) were
subsequently cancelled, including their Go job; they are not successful CI. The preceding
`530c791` quality run 39664404614 ultimately failed. No unfinished workflow is
counted as passing CI.

The new executor and admission evidence is component-scoped: the focused actual
Django restore rehearsal retains all 42 HTTP methods per backend and adds thirteen
ordinary-task comparisons. The standalone queue rehearsal requires all ten queue
and thirteen executor groups, with no skipped cases. The complete application
accounts/provider evidence above still comes from the earlier restore/runtime
increment. Published `714031d` quality/manual workflows (39669625934 and
39669625927) were subsequently cancelled. The `6fce63e` Go job (119471004737 in quality run 39671572241)
was cancelled by its 15-minute execution limit, as confirmed by its check annotation.
The complete quality workflow and manual run 39671572116 were subsequently cancelled.
The Go job budget is raised to 30 minutes for the expanded integration suite;
individual adapter deadlines are unchanged and the new head still needs CI.

The daily communication counter reset now has a concrete Go service and repository.
Seven required PostgreSQL groups pass after the actual Django oracle, covering
instance-wide bulk updates, nullable results, legacy swallowed SQL errors, fencing,
replay preserving later usage, cancellation and bounded statement counts. Django
reads the Go-written counters and unchanged configuration/audit fields. No publisher
or scheduler is activated; the task ledger is `implementing`. Static midnight
scheduling is absent from this fresh initialized app/Beat fixture, so historical
schedule reconciliation remains open. See
`BE_GO/internal/service/email_balance_reset.md`.

The full local production-Python accounts adapter passes with the new task fixture
included, as do the focused restore regression, Go race/vet and tagged integration
vet, formatting, inventory/drift and actionlint. This includes all previously
declared account, session, login image and restore-admission cases, with no missing
or skipped task scenarios. It remains current-model component evidence; the live
application login and full lifecycle are not thereby implemented.

Membership-card token cleanup now has a concrete Go service with exact UTC-day
selection and deletion audits. Ten required real PostgreSQL groups pass, including
actual heartbeat during an atomic deletion, stale completion, forced overlapping
cleanup and a 4,096-row executed plan. The actual Django oracle also confirms a
concurrency bug: two overlapping collectors emit six logs for three deleted tokens.
Go deliberately emits one audit per row actually deleted. The task remains
`implementing`: its observed Celery `60/m` rate policy, production registration and
scheduler/coexistence/lifecycle gates remain open. See
`BE_GO/internal/service/subscription_token_cleanup.md`.
The full local accounts adapter also passes on production Python 3.13.2 with the
new task fixture included. Later focused runs additionally verify forced collector
and SQL overlap; the production service/repository is identical across these runs.
Go race/vet, tagged integration vet, inventory drift and workflow lint pass.

Associate age updates now have a concrete service with eight required PostgreSQL
scenario groups and exact Django audit/index readback. It preserves the existing
6,575-day adulthood threshold and ordinary background scope. Actual Django failures
confirm false pre-save audits after a failed UPDATE and missing indexes after a
swallowed index INSERT error; the Go transaction deliberately rolls back the entire
invariant. Failure, replay, overlapping execution, cancellation, heartbeat and a
4,096-row executed plan pass. The task remains `implementing`; see
`BE_GO/internal/service/associate_age_update.md` for ownership/policy gaps.
Go race/vet and tagged vet pass. The full production-Python accounts run with
age updates included subsequently passed, including all unchanged image/auth
components. That run predates the new audit-retention fixture.

Audit retention now has ten required PostgreSQL groups and actual Django readback.
Exact 365-day selection and the dependent-index cascade share fenced completion.
A forced concurrent index writer exposed a result difference at deferred-FK commit;
the corrected Go behavior preserves Django's handled NULL outcome after a definite
rollback, while other commit errors still propagate. Both delete failures, replay,
concurrency, cancellation, heartbeat and an executed 4,096-row plan pass. The task
stays `implementing`; see `BE_GO/internal/service/audit_retention.md`.
The new `accounts.py --only task-bodies` rehearsal passes all 35 required groups
across reset/token/age/retention in one private schema, including exact managed-row
cleanup between fixtures. Go race/vet, tagged vet, inventory drift and workflow
lint pass. Current-head whole CI remains required.

Subscription calendar/default calculations now pass 293 actual Django range cases
and 36 real model-save cases, including date-change audits/indexes on the Django
side. Go computes the defaults but does not yet persist/audit the subscription.
The actual automatic archive task fails first on naive/aware comparison; an aware
clock exposes a second nonexistent-PK attribute failure. Both leave all managed
rows unchanged. The task is explicitly `needs_review`, not implemented as a Go
no-op; its full supported archive workflow remains required. See
`BE_GO/internal/service/subscription_dates.md`. Go race/vet, tagged oracle vet,
all 329 current oracle cases, inventory checks and workflow lint pass. The subsequent complete local accounts run described below includes retention,
calendar and payment description. Current-head whole CI remains required.

Published `59ecdfa` whole quality run 39674013825 and independent manual dispatch
39674019168 both passed. The dispatch used `edit_content=false`; its successful
result is exact-head evidence for `59ecdfa`, not for later components. The PR-linked
manual 39674013814 was cancelled. Both workflows for `446c184`, `1038495` and
`0973e53` were subsequently cancelled. Published calendar head `0003cc6` quality
39680784844 and manual 39680784761 were subsequently cancelled. Payment head
`30f9c10` quality 39681839219 and manual 39681839372 were cancelled.
`6260a8e` quality 39684049859 and manual 39684049515 are running at this checkpoint.
None of these running/cancelled runs is counted as success for later increments.

Payment description calculation now passes 81 real Django-save cases and matching
Go cases. The fixture checks exact payment readback, description audit changes and
direct association indexes; invalid calculations leave all managed rows unchanged.
The actual default-category lookup selects a deleted foreign-association category,
which remains a required scope correction before Go persistence. Stored and pending
metadata are distinguished, including non-string CharField behavior. The model hook
is `implementing`, not verified; see `BE_GO/internal/service/payment_description.md`.
Full atomic payment save and automatic archive remain open; batch reads are now
implemented as the separate component below.
Go race/vet, tagged oracle vet, inventory/drift, formatting and workflow lint pass.
The full production-Python accounts run at `30f9c10` passed retention, calendar and
payment-description stages, then failed an eight-concurrent-session assertion:
credential `SELECT FOR UPDATE` exhausted its three retries after concurrent
`last_login` writes. This is a failed full run, not successful account parity.

The credential correction retains repeatable-read candidate snapshots without
locking read-only candidates. Actual password writes still lock and reject stale
snapshots, preserving concurrent reset protection and atomic rehash rollback.
Six forced PostgreSQL groups pass, including actual Django second-connection
interleaving, reset, current-account revocation, page 129, cancellation and pool
recovery. No retry limit or existing concurrent-session assertion was relaxed.
The complete production-Python accounts rerun now passes: 68 real-hash credential
cases and 16 concurrent credential requests, 26 composed sessions including all
eight concurrent repetitions, 47 OTP cases, 20 admission/audit cases, all image and
existing HTTP/scope/restore checks, and reset/token/age/retention/calendar/payment
components. No required group was skipped. Latest focused checks additionally
confirm the actual rehash lock wait before cancellation. Results are preserved in
`quality-reports/go-migration/credential-concurrency/full-accounts-results.json`.
Go race/vet, tagged integration vet, inventory/drift, formatting and workflow lint
pass. See `BE_GO/internal/service/password_login.md`. These are current-model
component checks; public login and backend activation remain unfinished.

Existing-payment background preparation now passes eight required PostgreSQL
groups with twelve actual-Django descriptions and eleven denial inputs. It reads
up to 512 owned payments in one statement, keeps historical relationship visibility
and explicitly corrects foreign category/title selection. Transaction visibility,
32 concurrent reads in four connections and actual lock cancellation/recovery pass.
The executed plan over 4,096 course relations uses the existing payment FK index;
the primary payment scan remains sequential. No constant-work or latency claim is
made. Full managed snapshots remain unchanged. See
`BE_GO/internal/service/payment_save_inputs.md`. The preceding full accounts success
predates this reader; its eight focused groups are the current new-component
evidence. Full mutation locking, audits and automatic archive remain open.

## Remaining completion gates

Every requirement in the [acceptance matrix](README.md#required-acceptance-matrix)
remains binding. Outstanding work includes complete endpoint/business/hook and
permission coverage; public authentication and session/audit composition; complete
images/files/documents/integrations/manual; durable workers and authoritative
scheduling; realtime; versioned proxy routing and coordinated switching; historical
backup/restore and rollback; realistic performance/query-plan evidence; automatic
Go image/updater delivery and disposable lifecycle rehearsals. Ordinary updates
must retain Django until explicit, verified activation. The PR must remain a draft
until the full requirement-by-requirement audit passes.
