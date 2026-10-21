# Ordinary durable-job execution

`JobExecutor` is the Assozeta durable execution extension from specification §5.5.
It receives an explicit repository, ordinary-job restore admission and a copied
registry of service methods. There is no task discovery, global registration,
Celery payload decoding or production worker activation. The existing Django
workers and scheduler still own all application tasks.

Each slot acquires the existing shared restore file lease before claiming one
job. With a configured path, one PostgreSQL statement checks queued/running
receipts and reads the latest nonnull completed restore timestamp. Maintenance
therefore postpones intake without increasing attempts. The lease remains held
through service work and durable outcome handling; no database connection is held
just to retain that file lease. An empty lock setting preserves Django's disabled
coordination behavior, including skipping the receipt query.

Jobs older than the latest completed restore are terminalized before dispatch;
equal publication timestamps remain valid. Retry preserves the original job
creation time. The comparison is instance-wide, independent of association scope.
This reproduces ordinary `CoordinatedTask` admission. Restore execution itself
requires an exclusive lifecycle workflow and is not an ordinary registry entry.

The registry supplies a supported argument version, an execution deadline and a
context-aware service method. Unknown versions fail explicitly. The service owns
successful completion: database effects use `CompleteInTx` in their invariant's
transaction; provider effects need their own idempotency boundary before separate
completion. No business effect may follow a successful completion commit. Returning
nil without durable completion is a terminal `completion_missing` programming
error. An unexpected error or panic records only a fixed machine code, never raw
provider errors, arguments or panic values. A structured `JobFailure` supplies a
workflow-specific retry decision and delay; the persisted attempt limit remains
authoritative. Generic timeouts are terminal unless the service returns an explicit
failure policy. Actual task policies still require individual Django comparison.

A bounded heartbeat renews the current fencing token while the service runs.
Renewal failure cancels the service context and prevents stale outcome writes.
A concurrent atomic completion can legitimately make renewal report lease loss;
the executor accepts only the same attempt's durable completed state. A blocked
renewal is bounded by its store timeout. Original creation time and scoped identity
are retained throughout retry and dispatch.

`Run` owns 1–32 fixed slots. Each claims one job, rather than creating a queue of
already leased jobs waiting for capacity. The options require renewal plus store
timeout to fit within the lease. Idle and maintenance polling are explicit;
production composition must preserve Django's 15-second maintenance cadence unless
a changed policy is separately verified. The current tests use shorter injected
intervals where they do not compare that external cadence.

Shutdown stops intake, gives admitted work its grace period, then cancels remaining
service contexts and joins every slot. Interrupted work is retried within its
existing attempt limit. An ownership/store failure remains observable even if
shutdown happens concurrently. Heartbeats and the file lease remain alive until
the service actually exits; a cancellation-ignoring goroutine is never detached
and allowed to mutate data after restore exclusion has been released. Go cannot
forcibly terminate such a goroutine. Production task clients must honor bounded
contexts, and the future worker process supervisor needs a final hard-stop policy.
The service's cooperative drain tests do not establish that deployment lifecycle.

`TESTS/adapters/go/jobs.py` runs the ten repository groups and thirteen executor
groups against private PostgreSQL with race detection. Executor cases cover atomic
effects, explicit retry/backoff, argument versions, panic/timeout/missing completion,
long-running renewal, stale-token cancellation, blocked database renewal and pool
reuse, disabled/failed admission, maintenance attempt preservation, stale restores,
draining, simultaneous shutdown/lease failure and four concurrent joined slots.
Its JSON event verifier requires every declared group and rejects skipped tests.

The focused accounts restore rehearsal additionally uses the actual Django
`CoordinatedTask` and actual `DataRestore` schema for thirteen declared comparisons
against the Go executor. The Django probe runs eagerly: maintenance raises the real
Retry with countdown 15 and unchanged retry count/publication header, without broker
publication. Cases cover active/inactive receipts, missing/earlier/equal/later
completion times, latest completion, exclusive/unavailable locks and disabled
coordination. Managed model and session-cache readback remains unchanged after
fixture cleanup. This is not a Celery broker interoperability or task-body test.

Evidence lives in `quality-reports/go-migration/job-executor/` and `jobs/`.
Remaining requirements include concrete task services and argument/policy contracts,
progress and cancellation APIs, scheduler occurrence/ownership logic, process
entrypoints and signal supervision, coordinated backend switching, historical
restore/recovery and migration/image/updater integration. No task or complete
requirement becomes verified from these executor tests alone.
