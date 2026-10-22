# Maintainer report — incomplete Go migration

This draft retains all fourteen [accepted requirements](requirements.md).
**It is not ready for production activation or merge as a backend replacement.**
Work is isolated on `codex/go-backend-parity`, reconciled through
`main@fddcee3`. Django retains production API selection, business-schema,
worker and scheduler ownership. No production/customer data or published release
has been changed.

## Latest increment: table preferences

`POST /profile/settings/tables` preserves partial JSONField validation, mapped
association-owner preferences for collaborators and existing permission/scope
admission. TDD exposed Django full-row saves overwriting concurrent personal and
credential fields, plus successful responses after audit-index failure. Both
runtimes now lock fresh state and commit the selected preference with its audit
and index. Missing fields are no-ops; unrelated columns remain intact.

The actual roots pass 62 HTTP/effect/audit cases and three locked-row races per
backend. Mutation counts across 1/16/128/512 table entries are 2 SELECT / 1 UPDATE /
2 INSERT; unchanged repeats use 1 / 1 / 0; missing fields use 0 / 0 / 0.
Twenty-four competing writes produce 25 / 24 / 2 with one audit. Cancellation
before locking and during audit insertion rolls back, releases connections and
recovers within a four-slot pool. These counts exclude HTTP admission.

The first full regression exposed Go replacing an invalid Unicode surrogate and
saving data that Django rejects. A route-specific parser now preserves this
failure boundary without conflating valid pairs, literal U+FFFD or ignored fields.
The final focused suite, unit/race tests and 571,538 fuzz executions pass; fuzzing
is bounded evidence. The full accounts regression passes after the repair, including
the table workflow and all existing profile, password, 2FA, provider, image and task
checks (`full-final.log` and `full-final-results.json`).
See the [contract](../../../BE_GO/internal/handler/profile_tables.md) and
`quality-reports/go-migration/profile-tables/` for red/green logs.

Go race tests and ordinary/tagged vet pass, as do 324 existing Django tests,
190 manual Python tests, 11 manual Node tests and twelve inventory tests. The
rebuilt UID 10001, read-only, networkless image passes ten pre-database HTTP
checks. This does not establish authenticated database-backed image parity.

The preceding `a08b8ff` quality workflow `39730720795` passes completely, including
Go, backend, UI, SSO, self-host, recovery and legacy checks. Its manual workflow
`39730720797` fails on external dashboard recipe bindings. A local reviewed patch against `Bakney/manuale@4253c5d` refreshes
204 bindings with unchanged MDX and passing authoring validation; publication to
that separate repository awaits authorization. The current patch is
`quality-reports/go-migration/profile-tables/manual-bindings-review.patch`.
Prior commit `1ede0ff` passes quality workflow `39727996265`; prior success does
not establish current-head CI or full manual verification.

## Implementation and architecture

The root exposes fifteen upstream paths: health/readiness, login, refresh, email and
username availability, scoped profile information/profile and password changes, table preferences and 2FA setup/status/updates, and two retired software-billing
responses. CORS, restore admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 21 implementing, 6 requiring review and 483 uncharacterized; none
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
| Profile mutations | 52 HTTP/effect/audit cases and four locked-row scenarios per backend; selected personal/association writes and audits are atomic. Responses retain each writer's committed personal fields. Cross-endpoint races involving other serialized preferences remain open. |
| Password changes | 67 HTTP/effect cases, three locked scenarios per backend, four Django/Go profile/password writer combinations and four cross-runtime login flows. Credential-only writes preserve other columns; full frontend/lifecycle parity remains open. |
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
