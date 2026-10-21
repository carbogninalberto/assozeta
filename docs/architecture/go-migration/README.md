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
login/refresh routes are `implementing`, not `verified`; 508 other rows still need
characterization. The auth rows track primitives only, not registered endpoints.
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
| Shared PostgreSQL account reads | Go reads seven Django-created account rows; soft deletion, inactive state, nullable owner/grants, duplicate identifiers, parameter safety, cancellation and unchanged Django readback pass |
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

These packages are not wired to auth routes. Account status, duplicate-login
selection, legacy rehash, 2FA, cookies, trusted actor/effective identity, scope and
impersonation remain unimplemented in Go. No authentication ledger row is marked
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
builds the current Django model schema, creates seven synthetic accounts, verifies
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

## Required acceptance matrix

Every row below is required before this draft can claim completion.

| Requirement | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1. Authoritative scope | Pinned sources, static/runtime inventories, baseline contracts | Characterize every schema, method, side effect, permission, caller and operation; reconcile schedules |
| 2. Kuayle architecture | Module, explicit composition, Echo/service/repository paths | Remaining packages, DTO validation, domain implementations, queue/realtime extensions |
| 3. Shared data | Metadata snapshot and Django-created account read tests; no business schema mutation | Migrated PostgreSQL baseline, precise SQL/decimals/defaults, transactions, Django reads of Go writes |
| 4. Identity/permissions | Django denial contracts, bidirectional credential tests and collaborator route policy | HTTP login/refresh/session flows, account state, trusted identities, full role/scope denials in Go |
| 5. Routing/selection | Current Caddy stripping inspected | `/api/v1`, `/api/v2`, active alias, SSO/download/WS routing, coordinated drain/switch |
| 6. Environment | DB/Redis and JWT key subset/precedence tests | Remaining auth, storage, providers, logging, manual, instance flags and complete value compatibility |
| 7. Workers/scheduler | Task/schedule inventory; restore task fencing inspected | Durable intent, all task implementations, leases/retries/recovery, one scheduler owner, coexistence protocol |
| 8. Realtime/integrations/documents | Channels/MCP/manual inventoried | Protocol clients, replica fanout, permission/revocation/race tests, document visual evidence |
| 9. Backups | Fixed historical Django contracts pass | Go import/export, replacement/interrupted restore, identities/files, full lifecycle fencing |
| 10. Independent specification | New portable health tests and real Go harness | Full Go fixture adapter, all capability contracts, differential durable effects, browser workflows |
| 11. Queries/performance | Existing framework inspected | Dataset scaling, query counts/plans, pool/concurrency/load and export bounds |
| 12. Normal delivery | Current manifest/build/update paths inspected; standalone Dockerfile added | Multi-architecture/runtime image verification, release images/bundle/updater integration, verified-artifact gate, installation/upgrade/switch/rollback rehearsals |
| 13. PR artifacts | Draft implementation, ledger, this report and foundation CI | Complete implementation and operator docs, full checks, reviewable final PR |
| 14. Completion audit | Explicitly not satisfied | Verify every requirement against authoritative current evidence; no missing behavior |

The next implementation work is to close health/config compatibility gaps and
implement authentication services plus shared-schema integration. Route switching
and installable Go artifacts must wait for tested API and background ownership.
