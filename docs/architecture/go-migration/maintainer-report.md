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
existing environment settings, then applies the current Django security response
headers. Remaining global middleware is still incomplete.
Login has tested services, providers, transport and rate-admission components but
is not registered publicly. Most business workflows and every activation gate
remain unfinished. There are 514 ledger declarations: 4 deprecated, 8 implementing,
5 requiring renewed review and 497 uncharacterized; none is verified. Runtime
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
| Environment | 79 declared comparisons execute unchanged selected settings assignments with django-environ 0.12.0, then Go Load. They cover literal files, process precedence, provider/JWT references, required/default/cycle rules, multiline/base64, CSRF/CORS lists, DEBUG, derived rate/CORS flags and required SECRET_KEY. Secret representations and process isolation are checked. Full environment coverage is unfinished. |
| CORS | 51 portable HTTP methods per backend across five policies, 32 declared URL comparisons and four actual-application-root methods preserve origin policy, preflight bypass, body bytes, Vary, streaming, concurrency and malformed-list error timing. Model/session readback stays unchanged. Five actual Go preflight paths still work with PostgreSQL and Redis unavailable. Complete global middleware and versioned/browser routing remain open. |
| Security headers | Six portable methods per backend verify current Django defaults, preservation of explicit/empty fields, response errors, streaming, forwarded protocol, concurrency and CORS preflight bypass. Five actual-root methods verify health, missing Bearer, CSRF/method denials, missing route and preflight. Full trusted-host/restore/proxy policy remains open. |
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

The complete private PostgreSQL/Redis accounts rehearsal with CORS composition
passes: 10 retired-billing, 10 2FA status, 16 identifier and 36 refresh contracts
per backend, 43 cache-session cases, 27 controlled-terminal session contracts per
backend and all existing identity/scope, image, login and audit-write checks.
A subsequent focused refresh rehearsal verifies both final middleware components:
four CORS and five security-header methods per backend, with the existing refresh,
session and identity cases retained. These use current-model `migrate --run-syncdb`,
not historical migrations. The security-header addition changes transport headers;
its public-root checks are separate from the preceding full accounts run.

Full Go race/vet, tagged middleware/config vet, formatting, actionlint, 12 inventory
tests and catalog drift checks pass. The actual-server health rehearsal passes
startup, PostgreSQL/Redis loss/recovery, five preflights with both dependencies
unavailable and idle SIGTERM. Earlier provider/rate and query-plan evidence remains
in the ledger. Complete new-head CI and every broader activation gate remain open.

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
preceding published head `3260727` has a successful Go foundation job in quality
run 39661523420, including full accounts and the non-root image rehearsal. Its
complete quality and manual workflows (39661523420 and 39661523346) were still
running at this checkpoint. The preceding `d7a58ba` and `adbb728` workflows were
cancelled; cancellations are not successes. The last jointly successful whole
quality/manual baseline remains `dbf1beb` (39593769742 and 39593769720).
Full access/import/lifecycle/manual workflows that call these tombstones still
need Go implementation and verification; see
`BE_GO/internal/handler/retired_billing.md`.

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
