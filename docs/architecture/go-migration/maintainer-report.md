# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@fddcee3` including PR #22's security updates and PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

Upstream `main@fddcee3` (PR #22) changed the production reference to Python
3.13.16, Pillow 13.0.0, Django 5.2.18 and DRF 3.17.2. The existing Go compatibility
suites have now been revalidated against that baseline. Earlier full-suite results
below remain historical evidence.
CI now derives the exact Python patch version from `BE/Dockerfile`.

Current-reference checks pass 54 session cases, twenty account-state observations,
27 session HTTP contracts and 37 refresh HTTP contracts per backend. Credential
exchange passes 76 JWT and 37 password cases from Django to Go, plus two token
pairs and three passwords in the reverse direction. Focused image proofs pass
PCD (63), PCX/DCX (831), QOI (2,762), XBM (1,509) and PIXAR (81), including the
actual Django helper and shipped codec image. JPEG decoding (1,870), PNG headers
(398) and complete PNG files (1,472) now also pass locally and in the rebuilt
image, including 237 lazy PNG cases with zero pixel loads. The native comment
encoder passes 330 checks across ordinary and ASan/UBSan builds. Shared dispatch
passes 94 URI comparisons with separately reported TIFF and AVIF gaps; the PostgreSQL response
component passes 48 cases. These component results are also included in the
passing full accounts-adapter run. Current local proofs pass JPEG headers
(1,364), metadata (236), whole-file policies (97 plus metadata/decoder vectors),
BMP/DIB (534), TGA (1,150) and CUR (533). The full accounts adapter and the
rebuilt non-root, read-only, networkless shipped-image oracle both pass. CORS now passes 44 declared URL comparisons and 51
portable HTTP methods per backend, including uppercase IPvFuture. The pixel
oracle passes 84 exact transformations with the corrected Pillow 13 pass order,
and 19,535 CPython Base64 comparisons with nonterminal padding. This matrix is
now required by the shipped-image oracle. AVIF admission is now guarded in registry order,
but its decoder and other unsupported formats remain implementation gaps.

Go race, ordinary/tagged vet, formatting, twelve inventory tests, catalog drift,
workflow lint, self-host static checks and eighteen supplementary adapter checks
pass. The resize cancellation test now exercises reachable checkpoints in both
pass orders, including the final check. Current evidence is retained in
`quality-reports/go-migration/security-baseline/`, notably `full-accounts-results.json`,
`accounts-after-resample.log`, `full-image-with-transforms.log`, `go-race-current.log`
and `supplementary-current.json`. These are existing compatibility suites with
current-model disposable databases; they do not prove historical migrations,
frontend switching, complete Django/frontend checks or the release/activation
matrix. No ledger entry is promoted to verified.

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
remain unfinished. There are 514 ledger declarations: 4 deprecated, 15 implementing,
6 requiring renewed review and 489 uncharacterized; none is verified. Runtime
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
| CORS | 51 portable HTTP methods per backend across five policies, 44 declared URL comparisons and four actual-application-root methods preserve origin policy, preflight bypass, body bytes, Vary, streaming, concurrency and malformed-list error timing. Model/session readback stays unchanged. Five actual Go preflight paths still work with PostgreSQL and Redis unavailable. Complete global middleware and versioned/browser routing remain open. |
| Security headers | Six portable methods per backend verify current Django defaults, preservation of explicit/empty fields, response errors, streaming, forwarded protocol, concurrency and CORS preflight bypass. Five actual-root methods verify health, missing Bearer, CSRF/method denials, missing route and preflight. Full trusted-host/restore execution/proxy policy remains open. |
| Restore HTTP admission | 42 portable methods per backend cover six modes on real Django ASGI and the compiled Go root, with unchanged model/cache state. Shared/exclusive flock interoperability, in-flight HTTP, panic release and SIGKILL recovery pass in both process directions. PostgreSQL checks one SELECT per unsafe admission over 1/16/128/512 receipts, no pending SELECT for reads, and 32 concurrent admissions in four connections; EXPLAIN uses the existing partial index. Full restore execution/upload/recovery, stale tasks, worker/scheduler and stream/WS coordination remain open. |
| Durable job repository | Ten private PostgreSQL scenario groups cover atomic intent/completion, permanent scoped deduplication, renewal/expiry/fencing, bounded attempts/backoff, pending cancellation, 16 publishers, eight consumers, SIGKILL recovery and four-connection query bounds. One claim query across 1/16/128/512 rows; the actual 4,096-job plan uses bounded indexed branches after a failing scan test. Four concurrent migrators preserve a separate additive history. This uses synthetic business/history tables, not historical Django migrations. No application task or production process/schedule is activated. |
| Job executor | Thirteen real PostgreSQL groups verify service-owned completion, explicit failures/retry, heartbeats, stale-token/blocked-database cancellation, maintenance attempt preservation, stale restores, joined concurrency and cooperative shutdown. Thirteen actual Django `CoordinatedTask`/Go comparisons preserve global receipt and publication rules, eager Retry metadata and unchanged managed model/cache state. This does not migrate task bodies or prove Celery broker interoperability, a production worker command or scheduler ownership. |
| Rate admission | 14 bidirectional Django/Go Redis counter cases check keys, inclusive windows, thresholds and nonrenewed TTLs. Sixty-four concurrent Go calls admit five and deny 59 using 127 writes and at most four connections. Actual-key expiration races, paused Redis deadlines, no retries and pool reuse pass. |
| Rate HTTP boundary | 15 portable contracts per backend run actual Django key functions/decorators/handler403 and Go middleware on private Redis. The terminal view is controlled to verify body bytes, not credentials. Keys, representations/casefold, ordering, exact 429, method/disable bypass and nonfinite/integer rules pass. Public/global middleware composition and parser/body extremes remain open. |
| Credentials and refresh | 76 Django-to-Go JWT vectors, password-hasher interoperability and 37 portable refresh methods per backend on actual Django ASGI and the Go executable. Refresh preserves the refresh claims/JTI/expiry and gives the access token a fresh issue time. Denied refresh issues no BKN_AUTH; stale-session deletion may accompany it. Session identity cannot replace Bearer/scope or token identity, and 32 mixed concurrent requests agree. Nonstandard JWT numbers, lone surrogates and aggregate parser budgets remain open. |
| Retired software billing | Ten portable methods per backend retain 410 for association scope across legacy plan states, 403 for other roles and all current authentication/impersonation/resource restrictions. Unused checkout bodies remain unparsed; scoped references retain validation. Model/cache readback stays unchanged. PostgreSQL tracing excludes legacy billing queries; 32 mixed calls use 40 admission SELECTs and four test connections. These are compatibility tombstones; association/course payments remain supported scope. |
| 2FA status | Ten portable HTTP methods per backend verify current scoped-account status, required Bearer, collaborator permissions, administrator/association impersonation, foreign references, methods/negotiation, malformed sessions and 32 concurrent requests. Same credentials observe two flag changes per backend. PostgreSQL checks one/two admission SELECTs and zero extra status queries; 32 mixed reads use 48 SELECTs with four database connections. Setup/update remain open. |
| Public identifiers | 16 portable contracts per backend on actual Django ASGI and the Go executable cover email/username availability, duplicate UUID ordering, get_user presence/fallback, Unicode limits, REST/session/identity precedence and 32 concurrent requests. Managed tables and malformed-session bytes remain unchanged. PostgreSQL tests preserve one/two SELECT bounds across up to 512 duplicates and 32 concurrent calls in a four-connection test pool. Actual plans show sequential scans; no latency improvement is claimed. |
| Account/permission services | Real PostgreSQL/Redis comparisons cover actor admission, identity transitions/revocation, collaborator policy and resource predicates, including 132 scoped-request and 67 resource-scope cases with measured batch/concurrent query bounds. This is not endpoint-wide authorization parity. |
| Cache-session identity | 54 declared cases and twenty same-account observations across four backends compare actual Django session/authentication/audit middleware with Go on PostgreSQL/Redis. Current state, inactive rejection and active soft-deleted actors, password revocation, unchanged cache bytes/TTL and managed-model readback agree. Thirty-two concurrent resolutions use 32 SELECTs/cache reads and pools of four. Response bounds, Redis deadlines/reuse and failures are checked. HTTP behavior has separate evidence below; writes/rotation and remaining route wiring stay open; public refresh is composed. |
| Session HTTP audit | 27 portable contracts per backend use shipped-style ASGI/Uvicorn and concrete Go session repositories around a controlled terminal. Cookie/Vary rules, Latin-1 headers, actor separation, streaming and 32 concurrent mixed-actor/CID requests agree. Sixteen malformed cache/UUID/hash inputs retain exact production HTML 500, Cookie Vary and unchanged cache bytes. Seven rolled-back database cases retain exact audit-port values and column constraints. Session writes/rotation, complete error/global middleware policy and remaining route wiring stay open; public refresh has separate evidence. |
| Login services | 68 password-selection cases, 47 OTP cases, 20 normalization/audit cases, 33 persisted social-configuration cases, 41 social admissions and 26 composed sessions. Profile/card/response/finalization cases compare durable writes and Django readback. Public login and full image dispatch remain unfinished. |
| Providers | 89 Apple and 93 Google cases use actual Django libraries, ephemeral HTTPS and real signatures, with declared identity/error/request expectations. Client bounds, cancellation and configuration changes have separate tests. Live-provider and remaining raw-input/transport evidence are open. |
| Images | Independently declared Pillow/Django comparisons cover implemented pixel, metadata and file pipelines. A concrete domain LoginImageProcessor now composes eighteen pipelines with shared admission, but registry coverage remains incomplete; partial codec evidence is not successful public-login evidence. |
| Operations | Actual Go health contracts, PostgreSQL/Redis loss/recovery and idle SIGTERM pass. Non-root image and existing Django/self-host/manual rehearsals have scoped evidence. Active-request/task/WebSocket draining, Go release delivery and backend switching remain unverified. |

The historical pre-security-update oracle required Python **3.13.2**, matching
the production Dockerfile at that revision. The earlier local 3.12.6 interpreter accepted malformed bracketed
origins that CI 3.12.15 rejected; production 3.13.2 also has different array/object
trailing-comma JSON diagnostics. Explicit expectations and Go parsers were corrected
after failing comparisons. Forty URL cases also pass in the shipped-style Django
image. CI pins the production interpreter and checks it against the Dockerfile.
The preexisting disposable Docker fixture adapter is preserved.

The historical pre-security-update accounts rehearsal passed, including 42
restore-admission, 36 refresh, 16 identifier, 10 2FA and 10 billing methods per
backend; 43 cache-session cases and 27 controlled-terminal session methods;
four actual-root CORS and five security-header methods; and all existing image,
login, scope and audit-write checks. Results are retained under
`quality-reports/go-migration/restore-admission/full-accounts-results.json`.
Fixtures use current-model `migrate --run-syncdb`, not historical migrations or
real frontend switching.

At that historical revision, full Go race/vet, tagged restore/middleware/config
vet, formatting, actionlint, 12 inventory tests and catalog drift checks passed. The actual-server health
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
`6260a8e` quality 39684049859 and manual 39684049515 were cancelled.
`91723e4` quality 39685388758 and manual 39685388740 were cancelled.
`a4ca276` quality 39686967161 passed; manual 39686966726 was cancelled.
`b9a34ee` quality 39689822168 and manual 39689821977 are running at this checkpoint.
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
`BE_GO/internal/service/payment_save_inputs.md`. The complete production-Python accounts rerun now passes with all eight reader
groups and the existing credentials/session/image/HTTP/scope/task suites; evidence:
`quality-reports/go-migration/payment-save-inputs/full-accounts-results.json`.
That process started before the archive fixture was added; its success is not
an archive-inclusive full run. Full mutation locking, audits and automatic archive remain open.

Automatic subscription archive now composes date/payment defaults with scoped
subscription/payment/invoice updates, audits/indexes and fenced completion in one
transaction. The actual legacy task body is exercised after two explicit clock/PK
repairs; fixtures also establish optional-link partial failures and false historical
audits with missing indexes. Go corrects those documented defects. Thirteen required
PostgreSQL groups verify exact Django readback/resave, bounded pages with distinct
financial links, rollback across pages, concurrency, cancellation and actual executor
heartbeat. The five-task combined rehearsal passes all 48 required groups, with
unchanged preexisting managed rows after each fixture. Go race/vet, tagged vet,
inventory/drift, formatting and workflow lint pass. The executed 4,096-row page
plan uses existing indexes. See
`BE_GO/internal/service/subscription_auto_archive.md`. Task registration, scheduling
and production ownership remain unfinished; this is not complete model-hook parity.

Medical cleanup characterization now confirms fourteen actual Django cases: draft
and active-upload references are ignored, shared/foreign documents can cascade or
clear live references, medical deletion audits duplicate, and SQL rollback does
not restore an already deleted file. The Go discovery dependency protects all
fifteen current Document FK/M2M relationships plus canonical draft medical IDs.
Six required PostgreSQL groups verify exact metadata, historical scope, new-reference
transaction visibility, bounded query/pool use, cancellation and the 4,096-document
plan. It is read-only and does not implement destructive cleanup or S3. The task
remains `needs_review` pending upload/attachment coordination and durable provider
recovery. See `BE_GO/internal/repository/medical_cleanup_candidates.md`.


Medical S3 key resolution also passes 5,045 production Python/django-storages
cases, including timestamp rounding and rejection of cross-document paths.
This does not execute provider operations. See
`BE_GO/pkg/storage/documentkey/README.md` and run
`TESTS/adapters/django/document_keys.py`.


The full local accounts suite now passes on production Python 3.13.2 with
all five task bodies, thirteen archive groups and the fourteen Django plus six
Go medical cases included. Existing password/session/image/HTTP/scope/restore
checks pass unchanged. Evidence:
`quality-reports/go-migration/medical-orphan-cleanup/full-accounts-results.json`.
The separate 5,045-case medical S3 key adapter also passes; provider execution,
historical schemas and backend switching remain outside these checks.


The S3 deletion dependency now passes seven real MinIO groups and four Django
storage cases, including batches through 1,000 objects, version/delete-marker
readback, real retention partial failure and credential denial. Wire checks cover
response bounds, cancellation, deadlines, admission and fixed errors. It is not
wired to the medical task: durable intent, uploads/references and restore fencing
remain open. See `BE_GO/pkg/storage/s3objects/README.md` and run
`TESTS/adapters/django/s3_objects.py`.

Storage environment parsing now passes 65 authored production-Python comparisons
against the unchanged storage assignments and actual S3Storage defaults/constructor.
Go Load preserves required/empty values, proxies, process precedence, Spaces
classification/defaults, ACL nil semantics, literal token/profile lookup and the
separate lazy-backend validation boundary. No credential file or provider is opened.
The shared URL-origin parser retains the CORS contract; 51 HTTP contracts per backend,
40 URL cases, the 87 existing environment cases and live health/dependency recovery
pass again. See `BE_GO/internal/config/README.md` and
`TESTS/adapters/django/storage_configuration.py`. Complete provider-chain
parity, production storage composition and destructive cleanup coordination remain open.

The full accounts suite was rerun after storage configuration became required at
server startup. All real-server adapters now supply the same synthetic storage
settings as Django. Password/session, image, HTTP/scope/restore and all five task
body checks pass on production Python 3.13.2. Evidence:
`quality-reports/go-migration/storage-configuration/full-accounts-results.json`.
This is current-model regression evidence, not historical-schema or switch parity.

Storage credentials are constructed through explicit SDK-backed providers with
fixed errors and bounded HTTP/process/token-file reads. Tested selection preserves
Django priorities; partial static credentials and oversized process/token output
are explicitly rejected. Subprocess groups terminate on cancellation, and shared
caches coalesce concurrent retrieval. The storage deletion client uses this
constructor in the disposable MinIO rehearsal; production composition is still open.

| Boundary | Executed differential evidence |
|---|---|
| Static/environment/shared-file/process selection | 44 cases, `TESTS/adapters/django/storage_credentials.py` |
| AssumeRole, MFA and ECS/IMDS sources | 82 cases, `TESTS/adapters/django/storage_roles.py`; independent SigV4 checks, exact fields/counts, nested identities, validation and provider failures |
| Web identity | 38 cases, `TESTS/adapters/django/storage_web_identity.py`; unsigned requests, profile/environment priorities, literal token files, newline/UTF-8 behavior, symlinks and response failures |
| Real object deletion | Seven MinIO groups and four Django cases, `TESTS/adapters/django/s3_objects.py` |

Go race tests verify one process plus two STS calls for 32 nested-role callers,
one MFA callback for 32 waiters despite cancellation, and one source fetch plus
one STS call for 64 ECS/IMDS callers. Web-identity rotation uses two waves of 64
callers, actual expiry and atomic symlink replacement, with one STS call per wave.
MFA never implicitly reads console input and does not reprompt within the same
provider after failure/expiry. IMDS endpoint prefixes and container token-file
precedence are covered. The web-token oracle records its 1 MiB cap as an explicit
correction; FIFO rejection is tested separately. Go vet/race, inventory and workflow
lint pass. Evidence is under `quality-reports/go-migration/storage-credentials/`,
`storage-roles/`, `storage-mfa/`, `storage-metadata/` and `storage-web-identity/`.

See `BE_GO/pkg/storage/s3credentials/README.md` for precise limits. Direct/default
metadata chains, SSO/legacy files, full INI and provider precedence, early renewal,
live cloud/IdP policy, `.env` fallback-chain and production composition remain open.
No additional cleanup task or backend activation is claimed.

The `dd146b7` CI reload fixture once reported an empty frontend-version label.
Its Vite/Node/Caddy fixture does not run Go. All eleven cases passed unchanged
locally, and the later `bcbb8c7` UI job passed; no cause or fix is attributed.
Whole-workflow success must be assessed at the PR head, separately from these
component results. Safe logs remain in `storage-credentials/ui-job.log` and
`reload-ui.log` beneath the evidence directory.

The GIF first-frame path now has 644 declared Django/Pillow comparisons for
palette/grayscale behavior, canvas/transparency, interlacing, LZW decoding,
truncated blocks, 64 KiB refill boundaries, comments and JPEG output. The shipped
image and local race checks cover the same vectors, plus bounded admission and
cancellation. Evidence is in `quality-reports/go-migration/gif/`. Shared image
dispatch and remaining formats still prevent complete public login composition.
The complete accounts rerun passes with production Python 3.13.2, real PostgreSQL
and Redis, including all 644 GIF cases, 68 password cases, 47 OTP cases, 26 sessions,
20 admission/audit cases and existing task/HTTP/scope/restore/image checks. Results:
`quality-reports/go-migration/gif/full-accounts-results.json`.

Shared image composition now has 69 whole-URI Django comparisons under one
admission/deadline and explicit resource limits. Four demonstrated missing-codec
cases are separately recorded as gaps, with complete image parity explicitly
false; other missing formats and ambiguous registry cases remain open. Forty-three
PostgreSQL response cases now include seven concrete image scenarios, concurrent
composition and a missing-decoder failure that releases the pool and returns no
tokens before card reads/writes. Evidence: `quality-reports/go-migration/image-dispatch/`.
The shipped non-root/read-only/networkless image passes the complete codec oracle,
including shared dispatch. Go race/vet, tagged vet, inventory/drift, formatting
and workflow lint pass. The complete accounts adapter also passes on production
Python 3.13.2 and private PostgreSQL/Redis, including all 43 response cases, 69
image parity cases with four separately reported gaps, 68 password cases, 47 OTP
cases, 26 sessions and 20 admission/audit cases. The terminal successful report is
retained as `image-dispatch/full-accounts-results.json` under the same evidence
root. Current-model fixtures do not establish historical-schema or lifecycle parity.
Public login remains unwired.

The `f66c816` Linux legacy/browser CI job failed while installing Playwright's
browser: `cdn.playwright.dev` presented a certificate for `*.azureedge.net`
(`ERR_TLS_CERT_ALTNAME_INVALID`). Browser verification did not run. This is an
unresolved CI gap, not a passing rehearsal or a code fix. The log is retained in
`quality-reports/go-migration/gif/previous-head-linux-legacy.log` (job 119558174927).

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


ICO decoding now preserves selected-frame ordering, eager PNG/DIB loading and
DIB alpha-mask behavior. The 678 authored pixel/file cases agree with Pillow and
the actual Django helper. Exact budgets, cancellation, concurrent calls, native
PNG/JPEG reaping and admission reuse pass. Shared dispatch now has 70 parity cases
and three demonstrated missing-codec gaps, which are not an exhaustive census.
Forty-four PostgreSQL response cases include ICO logo conversion. Evidence is under
`quality-reports/go-migration/ico/`; public login and complete backend parity remain
unverified.

The complete production-Python 3.13.2 accounts suite passes with all 678 ICO cases,
44 responses, 70 shared-image parity cases and three separately reported gaps,
plus existing task/HTTP/scope/restore/image and real-hash/session checks. Its terminal
report is `quality-reports/go-migration/ico/full-accounts-results.json`. The shipped
non-root/read-only/networkless image passes the complete codec oracle. Go race/vet,
image/response-tagged vet, all twelve inventory tests, catalog drift, formatting
and workflow lint pass. These current-model fixtures do not prove historical
migration, browser switching or full lifecycle compatibility.


WebP first-frame decoding now follows the native animation API used by Pillow.
Its 178 declared header/pixel/file cases pass against the actual Django helper,
local libwebp 1.6.0 and the shipped static libwebp 1.4.0 image (Pillow uses 1.5.0).
The native wrapper passes 380 exact ASan/UBSan frame/error/protocol checks; linked
system libraries are not rebuilt with sanitizers. Existing PNG sanitizer checks
also pass with the combined executable. Required license/patent/version notices
are verified in the non-root image. Stage budgets, native failures, cancellation,
reaping/reuse and concurrent calls pass. Shared URI parity now has 72 cases and
two demonstrated non-exhaustive gaps; 45 PostgreSQL responses include WebP images.
Evidence: `quality-reports/go-migration/webp/`. Full public login, all format/registry
coverage and the other migration requirements remain unverified.

The complete production-Python 3.13.2 accounts suite now passes with all 178 WebP
cases, 45 responses and 72 URI comparisons, retaining the two demonstrated gaps
separately. Existing task/HTTP/scope/restore/image and credential/session checks
also pass. The terminal report is
`quality-reports/go-migration/webp/full-accounts-results.json`. The final image
passes its complete codec oracle. Go race/vet, image/response-tagged vet, twelve
inventory tests, catalog drift, formatting and workflow lint pass. These remain
current-model component checks, not historical-data or switching proof.

CUR decoding adds 495 authored Django/Pillow cases and reuses DIB sample decoding.
The shared parser now supports CUR's post-halving dimension admission and preserves
recoverable versus terminal opening errors for CUR/TGA precedence. All 512 BMP and
678 ICO regression cases pass. Shared URI parity has 80 cases, with two separate
non-exhaustive gaps; the response fixture contains 46 cases. Exact budgets,
cancellation, native JPEG reaping/reuse and 32 concurrent calls pass, as do Go
race/vet and image/response-tagged vet. Evidence: `quality-reports/go-migration/cur/`.
These are component results; full public login and the complete migration remain
unverified.

The complete production-Python 3.13.2 accounts suite passes with the 495 CUR cases,
46 composed responses and 80 URI comparisons, retaining the two gaps separately.
The terminal report is `quality-reports/go-migration/cur/full-accounts-results.json`.
Existing task/HTTP/scope/restore/image and real-hash/session checks also pass. The
final non-root, read-only, networkless image passes the full codec oracle. Twelve
inventory tests, catalog drift, formatting and workflow lint pass. Historical data,
backend switching and lifecycle compatibility remain separate unverified gates.


PCD decoding adds 59 authored Django/Pillow cases with independent PhotoYCC RGB
expectations. Header/read limits, chroma layout, masked orientation, truncation
and file-size policy are covered. The pinned Pillow rotation hook fails for
orientations 1/3; Go retains the original image as Django does. Shared dispatch
has 84 URI comparisons and one non-exhaustive demonstrated TIFF gap. The response
fixture has 47 cases, including eleven concrete image scenarios. Exact logical
budgets, row-pair cancellation, blocked JPEG stdin cancellation, queued deadlines,
child reaping/reuse and mixed concurrency are checked. Evidence is retained under
`quality-reports/go-migration/pcd/`; full registry and public login remain open.

The complete production-Python 3.13.2 accounts suite passes with 59 PCD cases,
47 responses and 84 URI comparisons, retaining the TIFF gap separately and
`complete_image_parity=false`. Its terminal report is
`quality-reports/go-migration/pcd/full-accounts-results.json`. The final non-root,
read-only, networkless Docker image passes the full codec oracle. Go race/vet,
image/response-tagged vet, all twelve inventory tests, catalog drift, workflow
lint, formatting and diff checks pass. Current-model fixtures do not establish
historical-schema, backend-switching or complete lifecycle parity.


PIXAR adds 81 authored Django/Pillow header/pixel/file cases. Raw RGB layout,
short headers, mode/pixel-ceiling classification, ignored bytes, refill boundaries,
lazy passthrough and file-size/JPEG-dimension limits are checked. Shared dispatch
has 88 URI comparisons and one non-exhaustive TIFF gap; PCD/PIXAR precedence is
explicit. Forty-eight response cases include twelve image scenarios. Exact logical
budgets, ownership, cancellation, native admission/deadline/reaping/reuse and
mixed concurrency are covered. Evidence: `quality-reports/go-migration/pixar/`.
Full registry, public login and all broader migration requirements remain open.

The complete accounts suite on the previous Python 3.13.2/Pillow 11.1 baseline
passes with 81 PIXAR cases, 48 responses and 88 URI comparisons, retaining the TIFF
gap separately. Its terminal report is `quality-reports/go-migration/pixar/full-accounts-results.json`.
The full codec oracle in the non-root read-only/networkless image, Go race/vet,
image/response-tagged vet, twelve inventory tests, catalog drift, workflow lint,
formatting and diff checks also pass on that baseline. Separately, all 81 PIXAR
cases pass both locally and in the shipped image with Python 3.13.16/Pillow 13.0.0.
The other codec families and application-wide checks require reconciliation with
upstream `fddcee3`; no current-production full-suite or complete migration success
is claimed.
