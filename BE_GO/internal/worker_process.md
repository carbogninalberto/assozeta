# Native worker and operational migration commands

The Assozeta-owned module has explicit `cmd/worker` and `cmd/migrate`
composition roots alongside `cmd/server`. These are extensions of the specified
Kuayle layout. The single image contains `/app/server`, `/app/worker` and
`/app/migrate`; its default entrypoint and HTTP readiness check remain the server.
The worker registers only daily email balance reset. It does not publish work,
schedule occurrences or change ownership. There is no installed worker service
or operator activation command yet; Django remains the execution owner.

Both commands load the existing environment through `internal/config`, optionally
with `--env-file PATH`. No separate credential file is introduced. The worker uses
the configured runtime database endpoint, including PgBouncer. Migration uses
the original `DBHOST`/`DBPORT` directly because the migration driver's session
advisory lock is incompatible with transaction pooling. It applies only additive
Go operational migrations, never Django business migrations or destructive down
migrations. The deployment caller must hold the existing exclusive operations
lock throughout migrations and before resuming application/background services;
managed updates take their backup first. The tools-profile `migrate-go`
service is now invoked after Django migration by the ordinary lifecycle; see
[the lifecycle boundary](../../docs/architecture/go-migration/operational-migrations.md).
Running the command alone does not acquire that deployment lock or make
activation safe.

The worker requires a nonblank `DATA_RESTORE_LOCK_PATH` so a standalone invocation
cannot silently disable shared-lock and pending-restore admission. Use the same
mounted operations path as the installation; inventing a second lock path would
not coordinate with the CLI or Django. Existing HTTP configuration defaults remain
unchanged. A configured path alone does not prove correct deployment wiring.

Worker startup validates execution bounds before opening connections, then checks
the database and exact clean operational migration version through a read-only
repository method. Missing, dirty or unknown schema fails before readiness,
including when maintenance would defer all claims. A missing ownership row is
inert; Django ownership spends no attempts. Diagnostics omit SQL, payloads and
credential values.

Defaults are two execution slots, a 30-second lease, renewal every ten seconds,
five-second queue deadlines, one-second idle polling, 15-second maintenance
deferral, 25-second shutdown drain and a one-minute daily-reset deadline. These
are bounded command options, not established per-task parity for every Celery
task. The connection pool allows two connections per slot plus two, so effect
transactions do not consume heartbeat capacity. SIGTERM stops intake and drains
admitted work; an expired drain cancels and rolls back unfinished effects.
SIGKILL leaves a recoverable lease. The existing shared lifecycle file lease is
retained throughout admitted execution. See [ownership](repository/job_ownership.md)
for generation fencing and its limits across database restore.

Required checks from the repository root:

```sh
python TESTS/adapters/go/worker_process.py
docker build -t assozeta-go-foundation:test BE_GO
docker pull postgres:16-alpine
python TESTS/adapters/go/worker_image.py --image assozeta-go-foundation:test
```

The native adapter requires the integration Python environment with psycopg2,
Go and PostgreSQL binaries. Thirteen scenarios execute race-instrumented commands
against private PostgreSQL: missing/dirty/unknown schema, direct migration versus
pooler routing, default/missing owner, redacted errors, maintenance, two workers,
graceful/expired drain, crash recovery and ownership loss. Missing, empty and
whitespace-only lock paths also fail before readiness without changing jobs,
effects or ownership, even with pending restore state. An initial failing
case demonstrated readiness being announced under maintenance with missing
migrations; the read-only preflight fixes that defect. Independent review then
reproduced missing-lock startup bypassing restore coordination; the worker-only
configuration requirement fixes that separate admission defect.

Seven image scenarios execute the packaged binaries as UID 10001 with a read-only
filesystem on an internal Docker network without published ports. A fixture-only
Linux helper holds the same kernel's file lock and a database transaction.
Assertions cover the default server health/version, forward migrations, inert
Django ownership, maintenance, two-worker single completion, SIGTERM drain and
SIGKILL recovery. Worker fixture containers disable the server-specific HTTP
healthcheck; this does not establish a worker readiness probe for deployment.
Fixtures clean up their processes, containers and networks before recording
success. Both adapters are required in the reusable Go quality workflow.

Evidence is under ignored `quality-reports/go-migration/worker-process/` and
`worker-image/`. The image proof used immutable image
`sha256:bdf8fcff48ee14d59850a8a3bf897a9d30061505452d642bb84f8906446c063b`,
built from the working tree with version `worker-proof`; its embedded parent
revision is not a claim of a clean source commit. The tables/triggers in these
fixtures are synthetic transaction sentinels. Actual task behavior remains
covered separately by Django/task adapters. These checks do not prove scheduler
semantics, Celery queue transfer, production ownership handoff, external-effect
idempotence or historical data parity.
