# PostgreSQL migrations

`/app/migrate` applies the shared business schema and then the Go operational
schema. The deployment lifecycle calls it under the exclusive operations lock
using a direct PostgreSQL connection. HTTP and worker startup do no migration
work. The `--operational-only` option is available to callers preparing a
database whose business schema is managed separately.

## Business schema

`business/` starts at `000100` and is embedded in the Go migrator. Its plan
follows the Django migration graph, including the pinned third-party apps.
The file numbers identify business steps independently of the Go operational version.
Each step runs in a PostgreSQL transaction with its `django_migrations` record.
A database already migrated by Django keeps its history, including the
individual names covered by the application squash. New databases record both
the replacement and the migration names it covers. Content types and
permissions are created using Django's natural keys after the plan completes.
Existing numeric IDs, names and permission assignments are retained.

Use the backend's pinned Python environment and the PostgreSQL 16 server tools to
regenerate or verify the SQL. The tool creates its own disposable cluster.
Pass `--pg-bin DIR` when the server tools are outside `PATH`.

```sh
python -B tools/go-migration/business_schema.py write
python -B tools/go-migration/business_schema.py check
```

The generator runs Django's `sqlmigrate` SQL collector with a live schema in
the private cluster. Later operations in the same migration can then see the
constraints and indexes already installed there. It emits only DDL and
markers for manual ports, and folds temporary callable defaults into SQL
expressions. The manual ports live in [business/data](business/data) and
cover squash seed rows and content-type rename, document token regeneration,
OAuth table removal, retired invoice document cleanup, the group foreign key
repair, Beat schedule retirement, workflow and audit cleanup, Bakney protocol
reset, and the pinned auth, Celery results and social-auth data steps.
[signatures.go](business/signatures.go) ports application migration 0426,
which also reads and writes the configured signature object storage.

## Operational schema

The first additive migration owns only `public.assozeta_go_jobs`, its indexes and
the distinct `public.assozeta_go_schema_migrations` history. The latter belongs to
golang-migrate v4.19.1, matching the pinned Kuayle version. The embedded SQL source
is applied by `migrations.Apply` on a dedicated, single-connection pool. Concurrent
applicants use the migration driver's advisory lock. Connect/statement/lock
timeouts are bounded; application queries still use their own pool.

Migration 2 adds `public.assozeta_go_job_ownership`, initially Django at generation
1, and a generation field on existing operational jobs. Old jobs retain their
intent and leases but have no authorized generation until a fresh Go claim. Both
down scripts refuse destructive rollback. See
[`internal/repository/job_ownership.md`](../internal/repository/job_ownership.md).

Migration 3 adds retained `public.assozeta_go_periodic_occurrences` receipts,
uniquely keyed by Django source ID and increasing dispatch sequence. It has no
foreign key to Django schedules, so source deletion cannot erase publication
history; its job reference prevents deleting retained intent. It creates no
schedule or ownership transition and changes no Django table. Its down script
also refuses destructive rollback. See
[transactional publication](../internal/service/occurrence.md).

Migration 4 adds `public.assozeta_go_realtime_events`, a transactional realtime
outbox. Go services append a version-1 envelope (`kind`, `target_id`) in the same
transaction as the business write and `pg_notify('assozeta_go_realtime', id)`,
so rolled-back writes publish nothing and committed ones are never lost. While
Django owns WebSocket delivery, `python manage.py relay_go_realtime`
(`BE/notifications/go_realtime.py`) claims pending rows with `FOR UPDATE SKIP
LOCKED`, forwards allowlisted kinds through Django's own channel layer and
records `delivered_at` or a `failed_code`; Go never writes the Channels Redis
encoding. Delivery is at least once. Its down script refuses rollback so pending
events survive a binary rollback.

`Apply` exposes only forward migration. There is no force/baseline operation and
the down script explicitly refuses deletion, retaining even terminal jobs and
their deduplication keys during binary rollback. Invoking a third-party down tool
will report that refusal and may leave its migration history dirty; it is not a
supported rollback procedure. Operator recovery uses the operational history and the installed schema together.
Worker readiness requires the exact current clean operational version and table
shape. An older worker binary expecting an earlier version therefore refuses a newer
database. Rolling back that binary alone cannot resume Go workers; a coordinated
and verified restoration/recovery procedure is required. No installed Go worker
activation is offered by this increment.

See [lifecycle contracts and evidence](../../docs/architecture/go-migration/operational-migrations.md)
for details on operational versions and recovery.

Rollback must preserve Go-written business data, pending durable jobs, file keys
and identities. Reverting routing alone does not prove rollback. Ownership
fencing has scoped tests; installed handoff, coordinated task draining and full
historical restore compatibility remain incomplete. HTTP
admission now uses the existing Django/CLI file lock and DataRestore active-state
query. See `internal/service/restore_admission.md`.

See `internal/repository/jobs.md` for queue semantics and verification limits.
