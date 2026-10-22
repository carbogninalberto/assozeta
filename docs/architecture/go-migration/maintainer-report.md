# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@fddcee3` including PR #22's security updates and PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

The public `/profile/info` now preserves the effective person's profile and the
admitted owner's association, including collaborator-specific preferences,
review settings and registration choices. It performs no login/card/audit writes.
Actual Django ASGI and the Go root pass 56 declared HTTP scenarios and 88
concurrent reads per backend across all eight registration-flag combinations.
Four further reads per backend verify fresh stored personal/review settings.
Managed model rows and session/cache bytes remain unchanged by reads.

Real PostgreSQL/Redis/native-image tests measure two SELECTs for direct
athlete/administrator reads and six for owner/collaborator reads. Thirty-two mixed
calls use 128 SELECTs within four connections. Adding 1/16/128/512 nested groups
retains six SELECTs and excludes foreign groups. An executed plan with 4,000
additional synthetic tenants uses the existing user and association-owner indexes.
A blocked read cancels, releases
its connection and recovers. The legacy full group payload remains unpaginated;
query counts do not establish total-memory bounds or whole-application performance.
Login and profile share the same process-wide image pool. Unit tests cover reader,
identity, image and cancellation failures. Go race and ordinary/tagged vet pass.
The rebuilt non-root/read-only/networkless image passes seven HTTP checks; these
do not exercise a database-backed profile. Dependency loss/recovery and idle
SIGTERM checks pass, along with twelve inventory tests, drift and formatting.
The full accounts regression passes (`full-accounts.log` and
`full-accounts-results.json`). The final focused rehearsal additionally passes
the 4,000-tenant plan and unchanged-token transitions (`fresh-token-final.log`
and `final-focused-results.json`).
Evidence is under `quality-reports/go-migration/profile-info/`; see the
[profile contract](../../../BE_GO/internal/handler/profile_info.md).

The preceding setup checkpoint `d11eff6` passes 31 HTTP scenarios and 62 concurrent
requests per backend, 512 decoded QR symbols, four activation/disabling flows and
four fresh-login flows. Its full accounts suite, 103 environment cases and image
probe pass. These are prior-checkpoint results, not current-head CI evidence.

The preceding atomic-update change passes 50 HTTP/effect and two locked-row
concurrency scenarios per backend, including user/audit/index rollback and
preservation of concurrent profile/password changes. Its 246 existing Django
authentication/impersonation/permission/audit tests pass. The manual source review
was updated in `3f6d5c1`, with all 190 local manual tests passing. Remote manual
run `39721860028` at `d11eff6` stops because the external manual's reviewed recipe bindings
still reference the previous recipe files. A local companion patch against
`Bakney/manuale@4253c5d` refreshes 83 reviewed bindings with unchanged MDX and
passing authoring validation (`manual-bindings-review.patch` and JSON report in
the setup evidence directory). Publication to that separate repository awaits
authorization; no complete manual CI success is claimed.
Global middleware, the complete frontend workflow, versioned routing and
activation remain unfinished; no ledger entry is promoted to verified.

The preceding public-login checkpoint `e709159` passed the full accounts suite,
private provider TLS checks and remote quality workflow. Its manual run was
cancelled. Full shipped codec evidence belongs to checkpoint `1fe83c7`; codec
implementation/build inputs are unchanged in this increment. These prior results
do not establish current-head CI or complete application parity.

## Implementation and architecture

The root exposes twelve upstream paths: health/readiness, login, refresh, email and
username availability, scoped profile information and 2FA setup/status/updates, and two retired software-billing
responses. CORS, restore admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 18 implementing, 6 requiring review and 486 uncharacterized; none
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
| Profile information | 56 HTTP scenarios, 88 concurrent and four fresh-state reads per backend across eight registration modes. Exact effective/owner fields, actual image conversion, query/pool bounds, foreign lookup denial and cancellation; all image formats/frontend workflows remain open. |
| 2FA writes | 50 HTTP/effect and two locked-row concurrency scenarios per backend; atomic failure rollback, correct impersonation audit, query/pool bounds and cancellation. Setup has separate evidence below; full frontend activation remains open. |
| 2FA provisioning | 31 HTTP scenarios, 62 concurrent requests per backend, 512 decoded symbols, four cross-runtime update flows and four fresh-login flows. No setup persistence; entropy/encoder failures and cancellation covered. Full browser/phone interaction remains unverified. |
| Providers and environment | 89 Apple and 93 Google component HTTPS/signature cases, supplemented by the public root fixture. 103 environment comparisons use actual Django settings and requests CA precedence. Trust directories/rotation, default roots, full proxy/JWK-platform policy and live browser/provider flows remain open. |
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
