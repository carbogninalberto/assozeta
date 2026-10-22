# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@fddcee3` including PR #22's security updates and PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

The public 2FA update now passes 50 declared HTTP/effect scenarios and two
locked-row concurrency scenarios per backend on actual Django ASGI and the Go
root. Tests cover JSON/form/multipart input, permissions and impersonation,
JWT-versus-session audit attribution, and failed user/audit/index writes. The
failure fixture exposed a Django orphan audit; both backends now commit the user,
audit and association index atomically and preserve concurrent password/profile
changes. Identical concurrent updates produce one audit.

Real PostgreSQL service tests pass 4/66 statement budgets for 1/32 identical
updates, a four-connection pool, blocked cancellation and subsequent recovery.
246 existing Django authentication/impersonation/permission/audit tests also pass
on private PostgreSQL/Redis. Go race and ordinary/tagged vet, twelve inventory
tests, catalog drift, formatting and the rebuilt non-root/read-only/networkless
image's five startup/login HTTP checks pass. These image checks do not exercise
a successful database-backed 2FA mutation. The full accounts regression also passes,
including existing task/scope/session/provider/image/hash/audit groups and the new
2FA mutation checks (`full-accounts.log`, `full-accounts-results.json`).
Evidence is under `quality-reports/go-migration/two-factor-update/`, notably
`transport-current.log`, `django-regression-final.log`, `go-race-final.log`,
`go-vet-final.log` and `image-http.log`. See the
[2FA update contract](../../../BE_GO/internal/handler/two_factor_update.md).
Setup/QR, global middleware/authentication, versioned routing and activation remain
unfinished; no ledger entry is promoted to verified.

The preceding public-login checkpoint `e709159` passed nineteen ordinary HTTP
methods, five rate/session methods, forty-one credential/effect scenarios and
sixteen password plus sixteen cross-identity social requests per backend. It also
passed the full accounts suite and private provider TLS checks. Provider trust
rotation/directories, full image coverage and representative resource sizing remain
open. The full shipped codec oracle belongs to the preceding `1fe83c7` checkpoint;
codec implementation/build inputs are unchanged in this increment. The complete remote quality workflow for `e709159` passed; its manual run is
still running. These are prior-commit results, not current-patch CI evidence.

## Implementation and architecture

The root exposes ten upstream paths: health/readiness, login, refresh, email and
username availability, scoped 2FA status and updates, and two retired software-billing
responses. CORS, restore admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 16 implementing, 6 requiring review and 488 uncharacterized; none
is verified**. Runtime discovery resolves 408 HTTP patterns, four WebSocket paths,
42 tasks, 112 models and 15 MCP schemas. Discovery is not implementation evidence.

The architecture follows specification `4d0f68d` and inspected Kuayle revision
`5025d2a6bc966d0d08c49832ff9c7c86184207cd`: one Go module, Echo transport/DTOs,
context-aware services, explicit constructors and sqlx/pgx repositories. Actor,
effective user and resource scope are distinct. The durable job repository is an
explicit Assozeta extension with its own additive operational migration history;
it is not automatically applied. **Django owns existing business migrations and
production workers/scheduling.** No schema rename, customer identity regeneration,
production deployment or release publication has occurred.

Implemented service transactions cover credentials, username/audit/index writes,
card configuration, login finalization and 2FA user/audit/index changes. JSON audit data is explicitly supplied
as text for the root's transaction-pooler-safe pgx mode. Earlier committed effects
remain after later login failure, but credentials are delivered only after success.
Go-only creation locks do not coordinate unmodified Django writers. Deliberate
atomic rollback differs from Django's partial effects on some exceptional failures;
those differences remain explicit activation gaps.

## Evidence available for review

| Area | Verified subset and practical limit |
| --- | --- |
| Public login | 19 ordinary and 5 rate methods; 41 credential/effect scenarios; 16 password and 16 cross-identity social concurrent requests per backend; exact response/cookie/signature and persistent audit/index/card readback. Complete login remains open. |
| Identity and credentials | Bidirectional Django/Go hashes/JWTs; 54 cache-session cases; 132 scoped-request and 67 resource-scope cases; 37 refresh, 16 identifier, 10 2FA-status and 10 retired-billing HTTP methods per backend. These do not prove authorization on every endpoint. |
| 2FA writes | 50 HTTP/effect and two locked-row concurrency scenarios per backend; atomic failure rollback, correct impersonation audit, query/pool bounds and cancellation. Setup/QR and frontend activation remain open. |
| Providers and environment | 89 Apple and 93 Google component HTTPS/signature cases, supplemented by the public root fixture. 95 environment comparisons use actual Django settings and requests CA precedence. Trust directories/rotation, default roots, full proxy/JWK-platform policy and live browser/provider flows remain open. |
| Query and concurrency bounds | Real PostgreSQL/Redis tests measure batched lookups, fixed connection pools, concurrent identities and query counts across growing datasets. Critical job claiming uses measured indexed plans. No representative whole-application performance improvement is claimed. |
| Jobs | Transactional intent, deduplication, fencing, leases, retries, crash recovery and executor drain are tested in private PostgreSQL. Five task bodies have 48 scenario groups. Production worker/scheduler entrypoints, remaining task bodies and coordinated ownership are unfinished. |
| Images and storage | Eighteen image pipelines have current Pillow 13 oracles, including shipped codec/pixel/Base64 checks. TIFF/AVIF gaps are explicit and non-exhaustive. Storage configuration/credential-provider and disposable MinIO checks are scoped evidence; destructive medical cleanup remains unwired. |
| Operations | Restore HTTP admission, shared lifecycle locks, dependency loss/recovery, idle SIGTERM and non-root image checks have evidence. Historical restore, active-request/task/WebSocket draining, normal release delivery and backend switching remain unverified. |

Current reference: Python 3.13.16, Pillow 13.0.0, Django 5.2.18, DRF 3.17.2 and
SimpleJWT 5.5.1, reconciled with `main@fddcee3`. Tests use disposable PostgreSQL,
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
| 5. Versioned routing | Implement `/api/v1`, `/api/v2`, compatibility selection, callbacks/root protocols and safe drain. |
| 6. Existing environment | Finish provider/proxy, logging, manual/instance and remaining configuration compatibility. |
| 7. Background processing | Finish every supported task/schedule, production commands, interoperability and ownership transitions. |
| 8. Realtime/integrations/documents | Implement all supported WS and provider flows, private files, documents and immutable manual/MCP delivery. |
| 9. Historical backups | Fixed historical fixtures, imports/exports, interrupted restore/recovery and coordinated rollback. |
| 10. Independent tests | Run shared contracts across both versioned APIs and the full unchanged-frontend/lifecycle matrix. |
| 11. Performance | Representative query plans, datasets, concurrent read/write/export workloads and comparable benchmarks. |
| 12. Release/update delivery | Include Go in ordinary releases/updater manifests; verify delivery, activation gates and interrupted rollback. |
| 13. PR deliverables | Keep this PR draft until implementation, operator procedures, ledger evidence and applicable checks are complete. |
| 14. Completion audit | Every requirement above remains an acceptance gate; passing a component does not close the migration. |

Software subscription gating, platform checkout and SaaS nurturing stay retired.
Association/course memberships, payments and supported communications remain in
scope. Historical backup skips remain limited to `BillingPlan` and
`PreviewAndCustomFeatures`; missing Go support cannot expand that allowlist.
Existing installations must retain Django until explicit, verified activation.
