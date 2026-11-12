# HTTP coordination with Django data replacement

The Go root now participates in the same `DATA_RESTORE_LOCK_PATH` flock used by
Django `instance.restore.locking`, Celery's `CoordinatedTask` and the self-host CLI.
It introduces no new lock namespace, schema, migration or environment setting.
The existing path follows `env.str` defaults/proxies and process/file precedence;
87 environment declarations are compared against unchanged Django assignments.

The explicit admission service obtains a nonblocking shared file lease before
ordinary synchronous HTTP work. An exclusive Django/CLI lease therefore blocks
those requests with the existing Italian HTTP 503 JSON and `Retry-After: 10`.
An unavailable configured volume has its separate existing 503 message. An empty
setting retains Django's optional-coordination behavior. The primitive also
supports exclusive and required acquisition for future operational callers.

With a configured path, unsafe methods check the existing `instance_datarestore`
table for **any** `queued` or `running` row. This is an instance-wide barrier,
deliberately independent of association or actor scope. Pending replacement
rejects the mutation and releases the lease. Reads keep their shared lease but
do not query pending state. SQL errors deny admission with production HTML 500,
release the file lease and do not expose database diagnostics.

Existing exemptions are preserved: OPTIONS; all `/oauth2/refresh-token` methods;
the `/instance/admin/data-restore` prefix; and GET/HEAD for `/instance/access`,
`/instance/config`, `/instance/status`, `/profile/info`, `/readyz` and `/healthz`.
Other methods on those read-only exemptions still pass ordinary admission.
This preserves the current literal prefix rule, not a newly restricted route
match. Exemptions do not register otherwise unimplemented endpoints.

Middleware order is CORS, restore admission, security headers, then route-specific
session/authentication. Preflight bypasses the inner chain. Maintenance denials
have CORS headers but no inner security/session headers. A supplied malformed
session cannot mask a prior restore denial. The caller retains the lease through
handler work and releases it on normal return, errors and panic unwinding.
The lock file is never unlinked on release, avoiding a second inode namespace.

`accounts.py --only restore-admission` prepares only private PostgreSQL/Redis,
synthetic accounts and restore receipts. Seven portable methods run in six modes
on actual Django ASGI and the compiled Go root: open with inactive receipts,
queued, running, exclusive Django lock, unavailable volume and disabled path.
All 42 methods per backend check ordinary/safe reads, unsafe methods, authentication
and CSRF precedence, OPTIONS, preflight, malformed sessions and 32 mixed concurrent
requests. All managed model rows and prepared cache bytes remain unchanged;
fixture cleanup restores preexisting state.

Real PostgreSQL integration varies inactive receipt counts across 1, 16, 128 and
512 rows, then changes one foreign association's receipt through active/inactive
states. Each pending-state call uses one SELECT; GET/HEAD add none. Thirty-two
concurrent admissions use 32 SELECTs and at most four test connections. The
captured EXPLAIN uses the existing `one_active_data_restore` partial index; no
new index or latency improvement is claimed. Cancellation and a closed database
are checked, including release of the lease after a query failure.

Cross-process tests use the unchanged Django lock implementation: Go shared
leases exclude Django exclusive acquisition, Django exclusive acquisition denies
Go, and SIGKILL of either holder releases the kernel lock. HTTP tests also keep a
handler in flight while exclusive acquisition is rejected, then verify release.
The supported deployment targets for this primitive are Linux and macOS.

Evidence is under `quality-reports/go-migration/restore-admission/`. This is HTTP
admission and the shared lease primitive, **not complete restore support**.
Go upload limits, historical import/export, replacement/recovery, durable workers,
stale-task fencing and scheduler/backend ownership remain unimplemented. Lazy
business streaming and WebSocket lifetime coordination require their own tests;
Django's streaming producer runs after its synchronous middleware returns.
Ordinary updates still retain Django; no activation or production restore occurred.
