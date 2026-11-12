Django exclusively owns the existing business schema during coexistence. The Go
server never runs schema migrations at startup.

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
supported rollback procedure. Operator recovery must inspect the actual schema,
not blindly force a version. No Django table or migration history is changed.
Worker readiness requires the exact current clean operational version and table
shape. An older worker binary expecting an earlier version therefore refuses a newer
database. Rolling back that binary alone cannot resume Go workers; a coordinated
and verified restoration/recovery procedure is required. No installed Go worker
activation is offered by this increment.

The explicit `/app/migrate` command is invoked by the tools-profile `migrate-go`
service after Django migration in the ordinary installation/update/restore
lifecycle. Its caller must hold the existing exclusive operations lock; it uses
the existing direct PostgreSQL configuration. HTTP and worker startup do not
call it. See [lifecycle contracts and evidence](../../docs/architecture/go-migration/operational-migrations.md).
Importing Kuayle business migrations, forcing
a baseline version, or changing an existing Django table here is prohibited.

Rollback must preserve Go-written business data, pending durable jobs, file keys
and identities. Reverting routing alone does not prove rollback. Ownership
fencing has scoped tests; installed handoff, coordinated task draining and full
historical restore compatibility remain incomplete. HTTP
admission now uses the existing Django/CLI file lock and DataRestore active-state
query; see `internal/service/restore_admission.md`. This does not transfer migration
or restore ownership to Go.

See `internal/repository/jobs.md` for queue semantics and verification limits.
