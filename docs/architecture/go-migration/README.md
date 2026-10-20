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

The runtime snapshot uses the committed environment example, synthetic key
values and a database-connection prohibition. Django's audit resolver setup
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

Two retired tasks are explicitly classified. Both health routes are
`implementing`, not `verified`; all other rows still need characterization.
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
Channels/Celery payload encoding. Other environment settings remain unported.

TDD evidence from this worktree:

| Check | Result and scope |
| --- | --- |
| Inventory tests | 11 tests pass; initial missing generator and later MCP-discovery failures were observed before implementation |
| Go unit/race tests | Config, service, handler and CSRF admission tests pass; undefined implementations failed first |
| `go vet ./...`, formatting | Pass |
| Portable Django regression | 18 tests pass on disposable PostgreSQL: 16 configured contracts, fresh setup/restore and initial historical import |
| Portable Go contracts | The same 3 health contracts pass over real HTTP |
| Go infrastructure rehearsal | Real PostgreSQL loss, simultaneous DB/Redis loss, dependency restart/recovery and clean SIGTERM exit pass |
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

## Required acceptance matrix

Every row below is required before this draft can claim completion.

| Requirement | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1. Authoritative scope | Pinned sources, static/runtime inventories, baseline contracts | Characterize every schema, method, side effect, permission, caller and operation; reconcile schedules |
| 2. Kuayle architecture | Module, explicit composition, Echo/service/repository paths | Remaining packages, DTO validation, domain implementations, queue/realtime extensions |
| 3. Shared data | Metadata snapshot; no schema mutation | Migrated PostgreSQL baseline, precise SQL/decimals/defaults, transactions, Django reads of Go writes |
| 4. Identity/permissions | Existing denial contracts pass on Django | Password/JWT/session interoperability, trusted identities, full role/scope denials in Go |
| 5. Routing/selection | Current Caddy stripping inspected | `/api/v1`, `/api/v2`, active alias, SSO/download/WS routing, coordinated drain/switch |
| 6. Environment | DB/Redis subset and precedence tests | Auth, storage, providers, logging, manual, instance flags and complete value compatibility |
| 7. Workers/scheduler | Task/schedule inventory; restore task fencing inspected | Durable intent, all task implementations, leases/retries/recovery, one scheduler owner, coexistence protocol |
| 8. Realtime/integrations/documents | Channels/MCP/manual inventoried | Protocol clients, replica fanout, permission/revocation/race tests, document visual evidence |
| 9. Backups | Fixed historical Django contracts pass | Go import/export, replacement/interrupted restore, identities/files, full lifecycle fencing |
| 10. Independent specification | New portable health tests and real Go harness | Full Go fixture adapter, all capability contracts, differential durable effects, browser workflows |
| 11. Queries/performance | Existing framework inspected | Dataset scaling, query counts/plans, pool/concurrency/load and export bounds |
| 12. Normal delivery | Current manifest/build/update paths inspected; standalone Dockerfile added | Multi-architecture/runtime image verification, release images/bundle/updater integration, verified-artifact gate, installation/upgrade/switch/rollback rehearsals |
| 13. PR artifacts | Draft implementation, ledger, this report and foundation CI | Complete implementation and operator docs, full checks, reviewable final PR |
| 14. Completion audit | Explicitly not satisfied | Verify every requirement against authoritative current evidence; no missing behavior |

The next implementation work is to close health/config compatibility gaps and
establish authentication fixtures plus shared-schema integration. Route switching
and installable Go artifacts must wait for tested API and background ownership.
