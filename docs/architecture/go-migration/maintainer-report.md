# Maintainer report — incomplete Go migration

This draft pursues the complete [requested replacement](requirements.md), using an
isolated worktree and `codex/go-backend-parity`, reconciled through
`main@fddcee3` including PR #22's security updates and PR #20's manual delivery. **It is not ready for production
activation or merge as a backend replacement.** Django retains business-schema,
worker and scheduler ownership. No production installation, customer data or
published release has been changed.

`PATCH /profile/update` now updates the admitted personal profile and optional
owner association in one transaction, including audit entries and indexes.
Characterization exposed Django partial writes after an association error and a
successful response after audit-index failure; the reference now locks/re-reads
both records and saves selected fields atomically. Successful legacy responses,
null/conversion behavior, impersonation attribution and post-commit serialization
failures remain explicit contracts.

Actual Django and Go roots pass 52 HTTP/durable-effect/audit cases and four
locked-row concurrency scenarios per backend. PostgreSQL measures 3 SELECT / 2
UPDATE / 4 INSERT for an owner change, 2 / 2 / 0 for an unchanged repetition and
49 / 48 / 4 for 24 competitors with exactly two audit records. Cancellation before
and during mutation rolls back all writes, releases the connection and recovers
within a four-connection pool. These are mutation-only counts. See the
[profile update contract](../../../BE_GO/internal/handler/profile_update.md) and
`quality-reports/go-migration/profile-update/response-snapshot-final.log`.
The final response also preserves each concurrent writer's committed personal
fields; `response-snapshot-red.log` demonstrates the preceding reader-overwrite
defect. The HTTP race now verifies both response names.

The shared avatar-upload helper retains 512-pixel mode, the strict decoded-file
1 MiB threshold and the same admission pool as 200-pixel presentation. Fifty
upload cases, 32 concurrent uploads and 94 presentation cases passed in the prior
image increment. TIFF/AVIF remain explicit gaps. Final Go race tests, ordinary
and tagged vet pass. The rebuilt non-root/read-only/networkless image passes
nine pre-database HTTP checks. Twelve inventory tests and catalog drift pass,
as do 190 manual Python tests and 11 manual Node tests. The 324 existing Django
tests pass on the same unchanged atomic-repair source. Full accounts regression
passes (`full-current.log`, `full-current-results.json`), including 52 profile
cases, four profile/password writer combinations and the upload fixtures. That
full run began before the final response-snapshot correction; the final focused
profile rehearsal, race/vet checks and rebuilt image additionally pass after it.

The new `PATCH /profile/update/password` preserves the effective person's
credential ownership, model-hash behavior, password policy and endpoint-specific
response. Actual Django and Go roots pass 67 declared HTTP/effect scenarios,
three locked-row concurrency scenarios per backend and four cross-runtime
password/login/JWT flows. The concurrency test exposed Django's full-row save
losing a concurrent profile edit; both runtimes now lock before verification and
write only the password. Concurrent resets invalidate stale credentials, and a
duplicate change succeeds once. Two further real HTTP races exposed the reverse
problem: Django profile updates could overwrite Go-written credentials. Those
personal-profile saves now update only supplied fields. Both password backends
preserve the new credentials and profile edit, with one profile-field audit.
Password-only writes produce no audit/session changes, matching the reference
registration.

The real PostgreSQL mutation test measures one SELECT/one UPDATE for success and
one SELECT/no UPDATE for incorrect credentials. Twenty-four competing requests
produce one success, 24 SELECTs and one UPDATE. Cancellation, connection release,
recovery and trigger-rejected writes pass with a four-connection pool. The full
focused evidence is `quality-reports/go-migration/profile-password/profile-write-interleave-green.log`.
Go race and ordinary/tagged vet pass, along with 324 existing Django tests,
190 manual Python tests, 11 manual Node tests and twelve inventory tests.
The rebuilt non-root/read-only/networkless image passes eight HTTP checks;
these do not exercise database-backed password changes. The full accounts
regression passes (`full-accounts.log`, `full-accounts-results.json`); it began
before the final legacy profile-writer repair. The final focused rehearsal and
324 existing Django tests additionally pass after that repair. Current-head
remote CI remains pending. See the
[password contract](../../../BE_GO/internal/handler/profile_password.md).

Quality run `39727996265` at preceding commit `1ede0ff` now passes completely,
including Go accounts/images, backend, UI, SSO, self-host, recovery and legacy
checks. The preceding SSO failure is not reproduced, but its cause was not
established. Manual run `39727996235` fails on the external dashboard recipe
bindings. These results do not establish current-head remote CI or manual parity.

Remote quality workflow `39723905840` passes at preceding commit `25b91ba`,
including the Go accounts/image job and existing backend/UI/self-host/browser
checks. Its manual workflow `39723905805` still fails on external recipe bindings.
Those results do not establish current-head remote CI.

Six manual source maps were reviewed after the atomic profile repair.
Other profile functions are unchanged; the profile snippet now includes the full
function. Successful settings/registration workflows retain their authored prose. The updated local companion
patch against `Bakney/manuale@4253c5d` now refreshes 204 bindings in total while
preserving all authored MDX; authoring validation passes. It is saved as
`quality-reports/go-migration/profile-update/manual-bindings-review.patch`.
The previously offered 83-binding patch is preserved. Publication to the separate
repository still awaits authorization; no full manual CI success is claimed.

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

Remote quality run `39721860272` at `d11eff6` failed while saving a QR
preview: the full accounts runner had not created the setup artifact directory.
Backend/UI/SSO/recovery/legacy, self-host and workflow jobs passed, but the Go
job and aggregate quality gate failed; later Go steps were skipped. Both setup
and profile adapters now create their own artifact directory before use. The
same clean-directory reproduction also exposed the profile SQL-plan output path.
Local red evidence is `fresh-artifact-red.log` and `plan-artifact-red.log` under
`quality-reports/go-migration/profile-info/`. Repeating both real Django/Go
rehearsals with initially absent artifact roots passes and verifies the generated
files (`fresh-artifact-green.log`, `plan-artifact-green.log`): all 31 setup and
56 profile scenarios per backend, 512 QR vectors and the 4,000-tenant query plan.
Current-head CI remains required.

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

The root exposes fourteen upstream paths: health/readiness, login, refresh, email and
username availability, scoped profile information/profile and password changes and 2FA setup/status/updates, and two retired software-billing
responses. CORS, restore admission and security headers are composed explicitly.
Most business endpoints remain unimplemented. The ledger has **514 declarations:
4 deprecated, 20 implementing, 6 requiring review and 484 uncharacterized; none
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
