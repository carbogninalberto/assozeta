Django exclusively owns the existing business schema during coexistence. The Go
server never runs schema migrations at startup.

The first additive migration owns only `public.assozeta_go_jobs`, its indexes and
the distinct `public.assozeta_go_schema_migrations` history. The latter belongs to
golang-migrate v4.19.1, matching the pinned Kuayle version. The embedded SQL source
is applied by `migrations.Apply` on a dedicated, single-connection pool. Concurrent
applicants use the migration driver's advisory lock. Connect/statement/lock
timeouts are bounded; application queries still use their own pool.

`Apply` exposes only forward migration. There is no force/baseline operation and
the down script explicitly refuses deletion, retaining even terminal jobs and
their deduplication keys during binary rollback. Invoking a third-party down tool
will report that refusal and may leave its migration history dirty; it is not a
supported rollback procedure. Operator recovery must inspect the actual schema,
not blindly force a version. No Django table or migration history is changed.

Only disposable integration tests apply this migration today. HTTP startup,
Compose and the updater do not call it. Deployment integration must hold the
existing lifecycle lock and supply the existing database configuration before
adding an explicit migration step. Importing Kuayle business migrations, forcing
a baseline version, or changing an existing Django table here is prohibited.

Rollback must preserve Go-written business data, pending durable jobs, file keys
and identities. Reverting routing alone does not prove rollback. Ownership
fencing, task draining and full restore execution remain unimplemented. HTTP
admission now uses the existing Django/CLI file lock and DataRestore active-state
query; see `internal/service/restore_admission.md`. This does not transfer migration
or restore ownership to Go.

See `internal/repository/jobs.md` for queue semantics and verification limits.
