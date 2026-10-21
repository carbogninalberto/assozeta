# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@06bac65` including PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

## Current result

The Go composition root exposes `/healthz`, `/readyz` and `/oauth2/refresh-token`.
Refresh now composes the existing-session audit middleware with PostgreSQL/Redis
and the configured SECRET_KEY.
Login has tested services, providers, transport and rate-admission components but
is not registered publicly. Most business workflows and every activation gate
remain unfinished. There are 514 ledger declarations: 2 deprecated, 5 implementing,
5 requiring renewed review and 502 uncharacterized; none is verified. Runtime
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
repositories. Infrastructure/provider clients have explicit interfaces. No Go
migration changes existing business tables. Redis interoperability is implemented
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
| Environment | 67 declared comparisons execute unchanged selected settings assignments with django-environ 0.12.0, then Go Load. They cover literal files, process precedence, provider/JWT references, required/default/cycle rules, multiline/base64, CSRF lists, DEBUG, derived RATELIMIT_ENABLE and required SECRET_KEY. Secret representations and process isolation are checked. Full environment coverage is unfinished. |
| Rate admission | 14 bidirectional Django/Go Redis counter cases check keys, inclusive windows, thresholds and nonrenewed TTLs. Sixty-four concurrent Go calls admit five and deny 59 using 127 writes and at most four connections. Actual-key expiration races, paused Redis deadlines, no retries and pool reuse pass. |
| Rate HTTP boundary | 15 portable contracts per backend run actual Django key functions/decorators/handler403 and Go middleware on private Redis. The terminal view is controlled to verify body bytes, not credentials. Keys, representations/casefold, ordering, exact 429, method/disable bypass and nonfinite/integer rules pass. Public/global middleware composition and parser/body extremes remain open. |
| Credentials and refresh | 76 Django-to-Go JWT vectors, password-hasher interoperability and 36 portable refresh methods per backend on actual Django ASGI and the Go executable. Refresh reserializes/signs unchanged claims/JTI/expiry. Denied refresh issues no BKN_AUTH; stale-session deletion may accompany it. Session identity cannot replace Bearer/scope or token identity, and 32 mixed concurrent requests agree. Nonstandard JWT numbers, lone surrogates and aggregate parser budgets remain open. |
| Account/permission services | Real PostgreSQL/Redis comparisons cover actor admission, identity transitions/revocation, collaborator policy and resource predicates, including 132 scoped-request and 67 resource-scope cases with measured batch/concurrent query bounds. This is not endpoint-wide authorization parity. |
| Cache-session identity | 43 declared cases and five same-account transitions compare actual Django session/authentication/audit middleware with Go on PostgreSQL/Redis. Current state, backend-specific inactive/deleted actors, password revocation, unchanged cache bytes/TTL and managed-model readback agree. Thirty-two concurrent resolutions use 32 SELECTs/cache reads and pools of four. Response bounds, Redis deadlines/reuse and failures are checked. HTTP behavior has separate evidence below; writes/rotation and remaining route wiring stay open; public refresh is composed. |
| Session HTTP audit | 27 portable contracts per backend use shipped-style ASGI/Uvicorn and concrete Go session repositories around a controlled terminal. Cookie/Vary rules, Latin-1 headers, actor separation, streaming and 32 concurrent mixed-actor/CID requests agree. Sixteen malformed cache/UUID/hash inputs retain exact production HTML 500, Cookie Vary and unchanged cache bytes. Seven rolled-back database cases retain exact audit-port values and column constraints. Session writes/rotation, complete error/global middleware policy and remaining route wiring stay open; public refresh has separate evidence. |
| Login services | 68 password-selection cases, 47 OTP cases, 20 normalization/audit cases, 33 persisted social-configuration cases, 41 social admissions and 26 composed sessions. Profile/card/response/finalization cases compare durable writes and Django readback. Public login and full image dispatch remain unfinished. |
| Providers | 89 Apple and 93 Google cases use actual Django libraries, ephemeral HTTPS and real signatures, with declared identity/error/request expectations. Client bounds, cancellation and configuration changes have separate tests. Live-provider and remaining raw-input/transport evidence are open. |
| Images | Independently declared Pillow/Django comparisons cover implemented pixel, metadata and file pipelines. The domain LoginImageProcessor and complete format dispatch remain unimplemented; partial codec evidence is not successful public-login evidence. |
| Operations | Actual Go health contracts, PostgreSQL/Redis loss/recovery and idle SIGTERM pass. Non-root image and existing Django/self-host/manual rehearsals have scoped evidence. Active-request/task/WebSocket draining, Go release delivery and backend switching remain unverified. |

The complete private PostgreSQL/Redis accounts adapter passes with the public
refresh composition: 36 refresh contracts per backend, 43 cache-session cases,
27 controlled-terminal session HTTP contracts per backend and all existing
identity/scope, image, login and audit-write checks. The focused `--only refresh`
mode also passes and is explicitly labelled as a component rehearsal. Both use a
current-model `migrate --run-syncdb` schema, not historical migrations. Results:
`refresh-session/full-accounts-results.json` and `focused-results.json` under the
local evidence directory.

Full Go race/vet, formatting, 12 inventory tests and catalog drift checks pass.
The actual-server health rehearsal still passes startup, PostgreSQL/Redis
loss/recovery and idle SIGTERM after adding the session dependencies. Existing
67-case environment and rate-admission evidence remains recorded in the ledger.

Six new public-refresh contracts first passed on actual Django ASGI/Uvicorn and
failed against the unwired Go executable. They verify Cookie Vary, retained and
revoked sessions, simultaneous BKN_AUTH/sessionid response cookies, malformed
session precedence before method/negotiation/Bearer/body, and separation of session,
Bearer/scope and refreshed-token identities. Thirty-two mixed concurrent requests
pass. Shared cache readback allows only the declared invalid-hash revocation;
managed-model snapshots remain unchanged. This extends the real public route,
not only the earlier controlled-terminal session middleware tests.

Evidence for this increment is under ignored
`quality-reports/go-migration/refresh-session/`. The complete quality workflow for
`14ddbb8` passed (39651032242). The `cae2689` quality/manual workflows
(39653306929 and 39653306706) were still running at this checkpoint; the prior
manual workflow was cancelled and is not a success. The last jointly successful
quality/manual baseline remains `dbf1beb` (39593769742 and 39593769720). New-head
complete CI remains required. Session writes/rotation, remaining route composition,
full global-header/middleware policy and activation gates remain open; see
`BE_GO/internal/handler/refresh.md`.

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
