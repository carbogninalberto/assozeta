# Go migration: compatibility ledger and acceptance evidence

**Incomplete implementation; not ready for activation or release.** The complete
accepted scope is retained in [requirements.md](requirements.md). Passing the
foundation checks below does not establish backend parity.

Baseline: `main` at `f115d1e`. Worktree branch: `codex/go-backend-parity`.
The manual-delivery worktree and production installations have not been changed.

## Architecture and current-main reconciliation

The original specification is
[`4d0f68d:docs/architecture/golang-backend-migration.md`](https://github.com/carbogninalberto/assozeta/blob/4d0f68d/docs/architecture/golang-backend-migration.md).
Its pinned Kuayle revision is
[`5025d2a6bc966d0d08c49832ff9c7c86184207cd`](https://github.com/carbogninalberto/kuayle/tree/5025d2a6bc966d0d08c49832ff9c7c86184207cd).
The reference was inspected locally using `git show`, including `BE/go.mod`,
`cmd/server/main.go`, `internal/config/config.go`, team handlers/services/SQL
repositories and `Dockerfile`. Observed patterns: explicit constructors, Echo,
DTOs, context-aware services and repository interfaces with sqlx/pgx. No claim is
made that the reference implements a general durable queue or distributed hub.

The current implementation uses one Assozeta-owned module in **`BE_GO/`**.
The earlier document's `BE-go/` path and final Django-retirement phase do not
supersede this request's coexistence requirement. Django continues to own all
existing business tables. No Go migration has been applied or introduced.

Current `main` has retired software subscription gating, platform checkout and
SaaS nurturing. `auto_move_users_to_base_plan` and `nurturing_warm_leads` remain
registered only to drain old messages without their retired effects; migration
0430 disables their persisted schedules. Association/course memberships,
financial records and supported communications stay in scope.

Historical backup skips remain exactly `BillingPlan` and
`PreviewAndCustomFeatures`, with validated section paths/counts. This work does
not expand that allowlist. The fixed `TESTS/fixtures/bakney-v1.json` remains the
historical contract.

Manual source, search/MCP tools, authenticated assets and corpus revision binding
are part of current scope. `selfhost/compose.yml` mounts `manuale_data` and sets
`MANUAL_INDEX_PATH`; Go release integration must consume the same verified
package. Seed/demo commands cannot substitute for package delivery.

## Ledger use and limitations

[ledger.json](ledger.json) contains **514 static declarations**: 372 route calls,
8 mounts, 4 WebSocket routes, 42 task declarations, 21 schedule declarations,
16 management commands, 5 model hooks, 14 serializer hooks, 12 signal receivers,
3 signal connections, 15 MCP definitions and 2 MCP dispatch registrations.
These are declarations, not 514 supported operations or completed ports.

[django-runtime.json](django-runtime.json) independently resolves the production
URL tree: **408 HTTP patterns**, 4 WebSocket paths, 42 registered tasks, 112 model
metadata records and 15 MCP tool schemas. Format suffixes and included patterns
explain why static and resolved counts differ. One plain Django callback,
`/chat/test/`, needs manual method characterization. Debug routes remain visible
in static discovery but are absent from the production resolver.

The runtime snapshot uses `BE/requirements.txt`, records resolved dependency
versions, and uses the committed environment example, synthetic key values and a
database-connection prohibition. A previous developer virtualenv had newer
Django/Celery-results/Silk packages; regeneration against repository requirements
removed three newer dependency-only fields from the metadata snapshot. Django's audit resolver setup
attempts database access, which is denied and recorded in the local diagnostic
log. This snapshot proves no persisted schema, installed schedule or audit
resolver state. It is not a schema-only database export.

An unresolved discrepancy is explicit: source discovery finds 21 schedule
entries across `core/celery.py` and settings, while this initialized app exposes
only the Bakney pairing schedule in `app.conf.beat_schedule`. Historical
`django_celery_beat` rows may add others. Worker/Beat startup and disposable
migration fixtures must establish the effective supported schedule; never infer
live schedule ownership from the AST count.

Each ledger row has source ownership/hash, Go implementation, dependencies,
request/response/error/auth/scope/transaction/effect fields, tests, evidence and
status. Unknown values are `null`, not assertions of no behavior. Changes to a
source file invalidate reviewed rows conservatively. Removed declarations remain
as `source_removed` until explicitly reconciled. A structural validator prevents
empty `verified` rows, but cannot establish that claimed evidence is true.

```sh
python3 -m unittest discover -s TESTS/inventory -v
python3 -m TESTS.inventory.catalog          # reconcile discovery with review notes
python3 -m TESTS.inventory.catalog --check  # fail on drift
# Use the existing backend virtualenv, not the portable-contract interpreter:
python TESTS/adapters/django/inventory.py
```

Two retired tasks are explicitly classified. Both health routes and the two
login/refresh routes and the partial audit-index connection are `implementing`, not
`verified`; 507 other rows still need
characterization. Refresh now has a registered upstream handler and portable HTTP evidence; login remains component work.
Caller tracing, internal service operations, persisted schedules, conditional
installations, full schemas, side effects and failure behavior remain work.

## Current implementation and evidence

`BE_GO/cmd/server/main.go` constructs config, bounded database/cache pools,
repository probes, a service and handlers explicitly. Business code does not
import Echo contexts. The server registers upstream `/healthz` and `/readyz`
paths, supports bounded dependency checks and SIGTERM HTTP draining. It performs
no business writes or schema migration.

Configuration reuses the existing DB/Redis variables and optional deployment
`.env`, with process environment taking precedence. PgBouncer's exact `True`
comparison and Django's nonempty-string `REDIS_SSLAUTH` behavior are preserved.
`godotenv` and `go-redis/v9` are named compatibility/infrastructure extensions;
the latter uses a separate short-lived probe key, never Django cache pickle or
Channels/Celery payload encoding. JWT key parsing now supports the existing raw/multiline and base64 precedence.
Other environment settings remain unported.

TDD evidence from this worktree:

| Check | Result and scope |
| --- | --- |
| Inventory tests | 12 tests pass; initial missing generator and later MCP-discovery failures were observed before implementation |
| Go unit/race tests | Config, service, handler, CSRF admission, JWT and password tests pass; undefined implementations failed first |
| `go vet ./...`, formatting | Pass |
| Portable Django regression | 18 tests pass on disposable PostgreSQL using repository Python requirements: 16 configured contracts, fresh setup/restore and initial historical import |
| Portable Go contracts | The same 3 health contracts pass over real HTTP |
| Actor admission | 18 explicit outcomes pass through Django JWTAuthentication and Go with real JWTs/accounts; includes current state, ignored role/email claims and exact numeric UUIDs |
| Impersonation cache boundary | 10 Django-written records read in Go and 2 Go records read in Django; protocol 4/5, TTL, expiry, no-renewal, revocation and concurrent reads pass on private Redis |
| Identity selection | 46 explicit Django/Go cases, 8 eligibility/configuration changes and 32 concurrent resolutions using a four-connection pool; real PostgreSQL/Redis and unchanged persisted readback |
| Login image pixel prerequisites | Ten actual Django decisions, 54 exact Pillow pixel vectors and 19,535 Base64 cases; cancellation, logical memory limits and concurrency; codecs remain open |
| Login response composition | 36 explicit full response cases without image conversion; seven/ten-statement paths, 32 concurrent calls, required instructor denial; 28 boolean and eight dotenv cases |
| Final login user writes | 17 actual Django view cases; UTC date boundaries, exact audit/index readback, two/four/five-statement paths, concurrent cancellation and atomic failure rollback |
| Login membership-card configuration | 20 actual Django response-builder/audit cases; exact durable readback, two/five-statement paths, concurrent Go creation, failure rollback and real deadlock retry |
| Association login profile | 23 explicit Django-rendered/Go JSON cases; all 50 fields, exact decimal text, reviewed boundary, scoped groups, required-read failure, bounded queries and concurrency |
| Base login profile | 46 explicit Django-rendered/Go JSON cases; all 37 fields, welcome/onboarding precedence, bounded queries, current preferences and concurrent reads |
| Composed password admission | 20 actual Django login/audit middleware versus Go service cases; exact event/index readback, rollback, bounded queries, concurrent normalization and forced deadlock recovery |
| Login second factor | 47 actual Django login versus Go service decisions; fixed PyOTP clock, malformed/Unicode/multipart inputs, JWT identity and denial last-login checks |
| Password credential stage | 21 actual Django login versus Go service selection/rehash cases; production hashes, Django readback of Go writes and managed-table comparison |
| Refresh HTTP | 26 identical portable contracts over actual Django WSGI and Go HTTP; cookie/admission/parsing behavior, Django signature/readback checks and unchanged managed tables |
| Scoped request guard | 132 Django/Go cases, including all 44 athlete operations and six composed resolver cases; batches through 1,025 references, 32 concurrent calls and Redis upload-grant expiry/revocation |
| Resource predicates | 67 explicit Django/Go visibility sets, transactional family/payment revocation, one query per batch of 1–512 tag IDs and 32 concurrent reads; all seeded resource tables unchanged |
| Shared PostgreSQL account reads | Go reads from 21 Django-created account rows; soft deletion, inactive state, nullable owner/grants, duplicate identifiers, parameter safety, cancellation and unchanged Django readback pass |
| Go infrastructure rehearsal | Real PostgreSQL loss, simultaneous DB/Redis loss, dependency restart/recovery and clean SIGTERM exit pass |
| Credential interoperability | Actual Django service/hashers: 20 Django-to-Go token vectors and 37 password vectors; 2 Go-issued/refreshed pairs and 3 Go password hashes accepted by Django; database access prohibited |
| Collaborator route policy | 38 explicit outcomes pass in Django and Go, including deny-by-default, read/write separation, manual GET exceptions and effective-user grants; all 269 rule declarations and 28 exemptions match |
| Password parser fuzzing | 441,963 executions in a five-second bounded run pass; explicit cost-limit and malformed-input tests also pass |
| Workflow syntax | `actionlint` passes |
| Existing self-host validation | `make selfhost-test` passes (configuration/updater/manual-sync checks) |
| Local image | Linux arm64 image builds; isolated `id -u` returns 10001 and server help executes; no release publication |

The first portable health comparison caught a real difference: Django CSRF
middleware returns 403 for unsafe requests without a valid token before the
view returns 405. The Go admission tests now cover missing/matching/masked/bad
tokens, origin checks and HTTPS referer checks. The shared contract checks 403
without a token and 405 with a matching cookie/header. This is not proof of all
Django middleware parity: error HTML, manual capture metadata, VERSION-file
precedence, trusted-host/CORS/security headers and lifecycle exclusion remain
unverified or incomplete. The ledger deliberately does not certify health parity.

Commands and diagnostic output are under ignored
`quality-reports/go-migration/`. The Django baseline used the current-model
schema adapter; it is not migration-history evidence. The Go harness uses no
business tables and is not the full scenario fixture adapter. The shutdown check
proves a clean idle exit, not draining active uploads, WebSockets or tasks.

### Credential primitives, not endpoint parity

`pkg/jwt` preserves the Ed25519/SimpleJWT format, four-hour access/thirty-day
refresh lifetimes and the actual non-rotating refresh behavior. SimpleJWT 5.3.1
copies the original `iat` and custom claims to renewed access tokens. REST does
not configure issuer/audience validation; WebSocket authentication must separately
apply its existing issuer/audience checks. JSON numbers retain integer precision.

`pkg/crypto` verifies the five configured Django password families and imported
raw bcrypt, including historical Argon2 variants. Its narrow internal Go upstream
Argon2 adaptation retains provenance/license and is tested against Django/cffi.
New PBKDF2-SHA256 hashes are readable by Django. Verification never writes account
state. Cost limits are explicit errors; deployment parameter census, aggregate
resource budgets and login admission limits still need evidence.

These packages are not wired to auth routes. Duplicate-login
selection, legacy rehash, 2FA, cookies and the complete HTTP auth workflow remain
unimplemented. Actor admission and identity selection are covered separately below;
resource authorization and impersonation audit workflows remain open. No authentication ledger row is marked
verified. Tests use fresh synthetic credentials in private temporary files and
save only counts/results/version metadata. The dependency adapter is intentionally
outside portable HTTP contracts. See the reproduction commands in `TESTS/README.md`.

The first foundation CI run exposed a missing Echo import after formatting. It
was fixed in `8110caa`; the next Go CI job passed. Whole-backend and release parity
must still be evaluated separately from the Go foundation job.

### Collaborator route policy

`internal/service/permissions.go` ports the existing collaborator route gate,
with unchanged permission keys (including the historical `bookeeping` spelling),
method-specific precedence, FULL-role behavior and default denial for unmapped
routes. The 38 explicitly specified fixture cases passed against Django before
Go implementation; Go initially failed for the missing service. Both now pass.
An additional declaration comparison detects drift in all 269 rules and 28
exemptions. These counts are not counts of implemented or authorized endpoints.

The service accepts the trusted **effective collaborator's** grants, not an
initiating administrator's grants or JWT/header role claims. Identity resolution
must still establish that subject from current persisted state. Registry
exemptions delegate to later checks: calendar mutation actions, exports, private
files, resource ownership, instructor/family/group scope and nested referenced
IDs are not authorized by this gate. Version-prefix stripping belongs to routing;
`/api/v2/...` is deliberately not interpreted as an upstream route here.

Run `TESTS/adapters/django/permissions.py` in the same Python environment as the
credential adapter. It prohibits database access and compares explicit outcomes
from `TESTS/fixtures/collaborator-policy.json` in both runtimes. Portable HTTP
contracts and full permission parity remain separate acceptance requirements.

### Shared account reads

`internal/repository/accounts.go` queries the existing `bakney_user` table using
bound values and the existing soft-deletion rule. It keeps the collaborator's
own grants/role/connected owner separate. Inactive accounts remain distinguishable
because Django resolves candidate credentials before rejecting inactive accounts.
Email/username comparisons use PostgreSQL `UPPER`, as Django's `iexact` does;
percent and underscore characters remain literal values. Duplicate legacy emails
and case variants of usernames are retained in UUID order.

`TESTS/adapters/django/accounts.py` creates a private Unix-socket PostgreSQL cluster,
builds the current Django model schema, creates 21 synthetic accounts, verifies
explicit Django lookup expectations, and runs the Go repository integration tests
against the same database. Django readback confirms no rows changed. The initial
run established Django expectations and failed for the absent Go implementation;
the implemented repository passes with the race detector. The cluster is removed
on success or failure. This does not exercise historical migrations, writes,
password rehash, login side effects or rollback of Go-written business data.

The candidate iterator bounds repository memory while retaining one database
connection during credential checks. Authentication must still bound aggregate
KDF work and coordinate writes/transactions; callback writes on that same
connection are not supported. No query-plan or throughput improvement is claimed.

### Actor admission and impersonation cache boundary

`ActorAuthentication` now connects cryptographic access-token verification to the
current account row. Bearer header parsing, missing/inactive/deleted accounts and
stored role/email precedence are checked through Django's actual
`JWTAuthentication` and Go in the shared private PostgreSQL harness. An observed
failure for numeric, null and boolean UUID claims drove explicit compatibility
handling; numeric identities never pass through floating-point conversion.
Passwords are removed from the returned actor. Account state is read per request.

This is the first stage of `ScopedJWTAuthentication`. The identity selector below
follows it with association-bound operation/reference admission; HTTP error-body
rendering beyond the tested refresh cases, remaining malformed claim shapes, endpoint authorization and login handlers remain
unfinished. Infrastructure errors propagate; they do not produce
an anonymous or authenticated result.

The real `django-redis` serializer was inspected and exercised to establish an
explicit `impersonation:v1` record boundary. Go uses a closed, size-bounded parser
for the existing small string/None dictionaries in pickle protocols 4/5. It does
not implement general Python object deserialization. The exact database-1 key
namespace, one-hour writes, non-renewing reads and cross-runtime revocation are
verified against private Redis. Go-written records are read back by Django.
See `BE_GO/internal/repository/impersonation.md` for format ownership and limits.
No claim is made about Celery, Channels, arbitrary Django cache values, session
eligibility, begin/end audit effects or WebSocket authorization.

### Persisted identity selection

`IdentityResolver` separates the initiating actor, effective person, data-scope
user and optional association constraint. It preserves administration/control
exceptions, primary-association export scope and the effective collaborator's
current grants. Impersonation resolves the actor and eligible target from the
existing schema on every request and checks the cache record's actor, association
and optional target-header binding. Owner and administrator target predicates are
parameterized SQL with EXISTS checks, not per-member application lookups.

The account adapter now creates 23 accounts plus associations, membership links,
instance configuration and real Django-issued cache sessions. Forty-six manually
specified outcomes pass against Django's actual `resolve_request_identity` and
Go with that same PostgreSQL/Redis state. Eight state/configuration changes prove
revocation after actor/target disabling or deletion, association deletion,
membership removal and unavailable primary export configuration. Thirty-two
concurrent resolutions succeed with four database connections and no retained
connections. A Go session deletion is subsequently observed by Django.

Django readback compares users, associations, member links, instance configuration
and audit rows; test-only state changes are restored. Infrastructure/cancellation
failures return no usable identity. This rehearsal uses the current model schema;
it does not prove migration history, throughput or business-write rollback.

The original identity fixtures contain no nested resource IDs. `IdentityResolver`
now invokes `ScopedRequestGuard` after target selection and before collaborator
mapping/grants. Association-bound requests with no guard or trusted operation
metadata fail closed. HTTP handlers, endpoint-specific instructor/returned-record
rules, begin/end audit writes and WebSocket revalidation remain separate work.
No authentication route is marked verified.

### Association/family/group resource predicates

`ScopedResources.Available` now implements the existing `scoped_queryset` record
boundary for 16 resource kinds, including subscription-linked medical references.
The service must supply trusted association/effective identity and treat selected
group only as an additional restriction. The repository accepts the transaction
owning a future mutation and never constructs identifiers from request input.
See [the repository contract](../../../BE_GO/internal/repository/scoped_resources.md)
for the preserved manager/FK behavior, bounded batch API and limitations.

`TESTS/adapters/django/scoped_resources.py` establishes 67 manually expected
visibility sets against actual Django managers, then the Go integration test
checks those sets in the same private PostgreSQL database. Foreign tenants and
families, deleted history, selected/foreign groups and inconsistent parent links
are included. Two transactional changes prove immediate loss of family/invoice
visibility in both runtimes and are rolled back. Every seeded resource table is
compared afterward. Go counts one query per reference batch and tests 1, 16, 128
and 512 distinct tag rows, plus 32 concurrent reads with four connections.

This is a read component, not a completed endpoint. The guard below now composes
it into association-bound identity admission. Document authorization, certificate
isolation, returned-query filtering and business-write concurrency remain separate
work. No ledger endpoint status is promoted by these component tests.

### Scoped request admission

`ScopedRequestGuard` preserves the operation gate, association username checks,
path/query/body reference traversal and denial order from `enforce_scoped_request`.
Ordered parsed objects preserve dictionary order and 128-bit numeric UUIDs. It
batches up to 512 references per kind, including alternating kinds, while returning
the first ordered denial. Medical references also use the existing Django upload
cache namespace and bounded UUID-string pickle reader, without TTL renewal.

The account adapter passes 132 explicit expected guard outcomes in Django and Go,
including all 44 athlete operation names and six real resolver cases using
Django-issued sessions. The latter prove a foreign reference is denied before
collaborator grants and scope errors do not mark a valid session invalid. Real
PostgreSQL tests for 1, 16, 128, 512 and 1,025 distinct tags interleaved with a
course use 2, 2, 2, 2 and 4 SQL queries; 32 concurrent calls use four connections.
Redis revocation/expiry are observed on the same guard, and Django sees Go cache
revocation. Persisted business/audit readback remains unchanged.

See [the service contract](../../../BE_GO/internal/service/scoped_request.md) for
trust boundaries and retained manager quirks. These are parsed service requests,
not public HTTP proofs. Transport size limits, multipart/query conversion, error
rendering, ordinary/instructor/endpoint permissions, private documents and writes
remain open. The preceding CI's Go foundation job now passes with the Italian
locale setup; that is still a subset of the required matrix.

### Refresh HTTP composition

The Go composition root now registers `/oauth2/refresh-token` with optional Bearer
and current identity/scoped admission. `RefreshService` preserves non-rotation,
original refresh claims and the existing null-token constructor behavior. The
handler sets the compatible BKN_AUTH cookie and implements method/OPTIONS,
content negotiation and JSON/form/multipart handling. The server now requires the
existing valid JWT keys at startup; the health rehearsal generates disposable keys.

`TESTS/contracts/test_refresh.py` contains 26 portable tests run unchanged over
actual Django WSGI and Go HTTP in `TESTS/adapters/django/refresh_http.py`. Both use
the private account/resource database and cache. Django separately validates Go
signatures and consumes a Go-created refresh token over HTTP. Every managed model
table is compared before/after, including business/auth/audit/jobs. All test keys
and credential fixtures are temporary. The existing health failure/recovery and
shutdown rehearsal still passes.

See [the HTTP contract](../../../BE_GO/internal/handler/refresh.md) for exact scope.
The refresh ledger row remains `implementing`: global restore/maintenance fencing,
CORS/security/session middleware, all locale/charset and multipart/body-limit edge
cases, versioned proxy routes and release/browser switching evidence remain open.
Login now composes credential selection/rehash, username/audit preparation and the second-factor gate; it still requires final response/write composition and social authentication,
response serialization and its persisted side effects. These remaining workflows
are required before replacement; the refresh tests do not shrink that scope.

### Password credential stage

`PasswordLogin` now selects among duplicate identifiers, preserves Unicode casefold
preference and coordinates the existing eager Django upgrades versus selected-only
raw bcrypt migration. It checks account status after those credential writes.
One repeatable-read transaction locks bounded pages and batches password updates;
expected denials retain documented rehashes, while infrastructure failures roll
back without a selected account. Admission is bounded before acquiring a database
connection; serialization conflicts retry at most three times.

The account adapter runs 21 explicit cases through the actual Django login view
and the Go service using the production hasher list. Django accepts the Go-written
hashes; all managed model tables are compared after declared fixture changes are
restored. Real PostgreSQL checks cover 1/16/128/129/257 candidates with reads of
1/1/2/2/3 queries and upgrade writes of 1/1/1/2/3, rollback/constraint failures
and 16 concurrent credential requests through two admission slots/four connections. See [the service contract](../../../BE_GO/internal/service/password_login.md)
for the atomic failure boundary and remaining admission/cost-budget work.

This is not a complete login or an authenticated session. Go HTTP login, distributed
rate limiting, social providers, response-created
configuration, final-login write composition and full rollback/release proof remain
required. The login row remains implementing.

### Login second factor

The independent second-factor gate now preserves PyOTP's six digits, 30-second
current-window check, base32 decoding, Unicode NFKC comparison and the view's
length/error precedence. Multipart OTP values retain file size and filename while
contents remain opaque. The gate has no replay counter or durable effects, matching
current behavior; it does not issue a session.

The account adapter runs 47 explicit actual-Django-view versus Go service cases,
with only PyOTP's clock controlled. Successful Django tokens identify the expected
account; failed OTPs never update last-login. All managed model tables are compared
after declared fixture resets. See [the gate contract](../../../BE_GO/internal/service/second_factor.md).
Password admission now composes username/audit normalization before OTP; the
remaining login response/writes still follow. Provisioning/reset, social login, distributed
rate limiting and full HTTP/release evidence remain open.

### Composed password admission and audit

`PasswordAdmission` now runs credential selection, current-account preparation
and OTP in order. Preparation normalizes the username and writes the existing
audit event/association index inside one explicit transaction, committed before
OTP. Thus missing/invalid OTP retains the same normalization/audit effects while
returning no admitted account. Target and request audit actor remain separate.

Twenty actual Django login/audit-middleware cases now pass against this composed
Go service; Django reads the Go-written event/index fields and relationships. The
fixture checks every managed table after restoring declared changes. Real PostgreSQL
checks cover rollback, constraint/cancellation/commit failures, five queries for
indexed normalization and one for unchanged names, 32 concurrent calls with one
event, and retry after a forced cross-actor deadlock.

The adapter now rebuilds audit resolvers after its schema creation, matching normal
post-migration server startup. It also characterizes a Django failure difference:
username uniqueness failure leaves a pre-save audit, while Go's requested atomic
invariant rolls it back. This is documented rather than counted as identical
failure-side-effect parity. See [the service contract](../../../BE_GO/internal/service/login_preparation.md).
The login and audit-index rows remain implementing. Final login response/configuration
creation, final-login write composition, social providers, public HTTP admission,
audit-context middleware composition and all remaining acceptance work are open.

### Base login profile

The internal profile service now preserves all 37 `UserAuthSerializer` fields.
Forty-six explicit JSON cases run against Django's real serializer/renderer and
Go on the same PostgreSQL. They cover null/scalar JSON, integer precision, category
UUIDs, Google-credential presence, welcome/setup precedence, strict elapsed-time
boundaries and missing/duplicate onboarding. One/two SELECTs suffice for up to
512 onboarding rows; 32 concurrent association reads use 64 SELECTs and release
the four-connection pool. Managed tables remain unchanged after test restoration.

This is response-component evidence, not a public login route. A real PostgreSQL
permission failure now proves optional onboarding returns null in both runtimes;
Go rolls back the aborted read transaction and releases the connection.
Association augmentation, image processing,
configuration-stage and final-write composition remain open. See the
[profile contract](../../../BE_GO/internal/service/login_profile.md).

### Association login profile

Twenty-three explicit contracts now compare all 50 `SportAssociationSerializer`
fields against Go. Exact decimal strings, PostgreSQL scale rounding, nullable and
scalar JSON, custom template names and the strict five-day reviewed boundary are
preserved. One/two SELECTs fetch an association and its scoped groups; 32 concurrent
reads with 512 groups use 64 SELECTs. A deletion/restoration transition, foreign
scope denials and a real required-group permission failure return no stale or
partial profile. Managed tables remain unchanged after fixture restoration.

This remains a response component. Complete login augmentation, image processing,
configuration-stage and final-write composition remain open. See the
[association profile contract](../../../BE_GO/internal/service/association_profile.md).

### Login membership-card configuration

The response stage now selects the existing lowest-UUID configuration or creates
its default, audit event and direct association index transactionally. Twenty
explicit cases compare the actual Django response builder/audit middleware with
Go; Django validates every written field and all managed tables after resets.
Existing rows require two statements, creation five. Thirty-two concurrent Go
calls use 67 statements and create one configuration/audit/index. Permission/FK
failures, injected failures and cancellation roll back; a forced PostgreSQL audit
actor deadlock verifies whole-transaction retry.

Django's synchronized missing lookups can create two rows, and denied audit/index
INSERTs retain partial effects under autocommit. Go's required atomic boundary is
explicitly stronger. The Go parent lock does not coordinate an unmodified Django
writer; cross-runtime creation remains an activation gap. See the
[configuration contract](../../../BE_GO/internal/service/membership_card_configuration.md).
Full login composition remains open; the final-write component is described below.

## Required acceptance matrix

Every row below is required before this draft can claim completion.

| Requirement | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1. Authoritative scope | Pinned sources, static/runtime inventories, baseline contracts | Characterize every schema, method, side effect, permission, caller and operation; reconcile schedules |
| 2. Kuayle architecture | Module, explicit composition, Echo/service/repository paths | Remaining packages, DTO validation, domain implementations, queue/realtime extensions |
| 3. Shared data | Metadata snapshot and Django-created account read tests; no business schema mutation | Migrated PostgreSQL baseline, precise SQL/decimals/defaults, transactions, Django reads of Go writes |
| 4. Identity/permissions | Django denial contracts, bidirectional credentials, actor admission, cache boundary, persisted identity, scoped operation/reference admission and collaborator route policy | Public HTTP login/refresh/session flows, endpoint group/instructor/family authorization, audit and WebSocket revalidation |
| 5. Routing/selection | Current Caddy stripping inspected | `/api/v1`, `/api/v2`, active alias, SSO/download/WS routing, coordinated drain/switch |
| 6. Environment | DB/Redis and JWT key subset/precedence tests | Remaining auth, storage, providers, logging, manual, instance flags and complete value compatibility |
| 7. Workers/scheduler | Task/schedule inventory; restore task fencing inspected | Durable intent, all task implementations, leases/retries/recovery, one scheduler owner, coexistence protocol |
| 8. Realtime/integrations/documents | Channels/MCP/manual inventoried | Protocol clients, replica fanout, permission/revocation/race tests, document visual evidence |
| 9. Backups | Fixed historical Django contracts pass | Go import/export, replacement/interrupted restore, identities/files, full lifecycle fencing |
| 10. Independent specification | New portable health tests and real Go harness | Full Go fixture adapter, all capability contracts, differential durable effects, browser workflows |
| 11. Queries/performance | Resource batch query counts and bounded-pool concurrency; existing framework inspected | Full dataset scaling, critical query plans, representative load and export bounds |
| 12. Normal delivery | Current manifest/build/update paths inspected; standalone Dockerfile added | Multi-architecture/runtime image verification, release images/bundle/updater integration, verified-artifact gate, installation/upgrade/switch/rollback rehearsals |
| 13. PR artifacts | Draft implementation, ledger, this report and foundation CI | Complete implementation and operator docs, full checks, reviewable final PR |
| 14. Completion audit | Explicitly not satisfied | Verify every requirement against authoritative current evidence; no missing behavior |

The next implementation work is to close health/config compatibility gaps and
implement authentication services plus shared-schema integration. Route switching
and installable Go artifacts must wait for tested API and background ownership.


### Final login user writes

The separate finalization service now matches 17 explicitly expected actual
Django-login cases for last_login and scheduled-deletion cancellation. UTC date
boundaries include midnight between the view's two clock reads. Cancellation
writes its User audit and association index in the same transaction. Two/four/five
statements cover unchanged/unindexed/indexed paths; 32 concurrent Go calls use 67
statements and cancel once. Real permission/FK failures, injected failures and
cancellation leave no partial invariant and release the pool. All managed tables
match after fixture restoration and Django reads Go-written audits.

The fixture separately demonstrates Django's retained pre-save audit on failed
User UPDATE, swallowed index failures and stale full-model overwrites. Go's atomic
transaction and narrow update deliberately preserve current unrelated user state.
Those differences, end-to-end revocation and mixed-runtime audit races remain
explicit gaps. The finalization service is not registered as a public login route;
see the [component contract](../../../BE_GO/internal/service/login_finalization.md).


### Login response composition and registration choices

The response builder composes base/association profiles and the card-configuration
invariant with role-specific keys, collaborator-owner selection, instructor
metadata, tables settings and registration choices. Thirty-six explicit actual
Django-builder cases compare complete JSON/outcomes with Go, including historical
role 0, empty feature arrays, JSON null/scalars/large integers and duplicate draft
instructors across owners. That metadata lookup does not authorize instructors.

Existing association responses use seven statements, default configuration ten;
32 concurrent 512-instructor responses use 224 statements. A real denied instructor
SELECT is required for collaborators but does not affect owners. Persisted
settings/instructor transitions are visible and all managed tables match after
fixture restoration. Twenty-eight boolean values and eight literal dotenv cases
match Django's reader, using existing registration variables and defaults.

These cases need no image conversion. Unit image doubles establish dispatch only;
the actual processor, public HTTP workflow and final session delivery remain open.
The builder refuses to silently pass through images when no processor is supplied.
See the [response contract](../../../BE_GO/internal/service/login_response.md).


### Login image pixel prerequisites

The pure-Go core preserves Django's strict 1 MiB decoded-file threshold, its
occasional enlargement/floating-point truncation, white alpha composition and
Pillow's two-pass fixed-point Lanczos output. Ten explicit actual-helper decisions,
54 exact pixel vectors across six modes and 19,535 Base64 differential cases pass;
no pixel tolerance is used. Input immutability, cancellation throughout processing,
logical allocation limits/overflow and 32 concurrent transforms are checked.
All managed database tables remain unchanged.

The source provenance and Pillow license are retained, including a supporting
license in the Go image. This does not provide file decoders or a JPEG encoder and
is not wired as a complete image processor. Format recognition/lazy headers,
metadata/mode/truncation behavior, byte-compatible JPEG, global memory admission
and full response/HTTP composition remain required. See the
[pixel-core contract](../../../BE_GO/pkg/storage/loginimage/README.md).
