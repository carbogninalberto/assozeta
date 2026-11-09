# Celery consumer — prefork capabilities and the restore family

The restore family (`instance.restore.execute`, `export_association_data`, `import_association_data`) needs worker
semantics the first consumer slices did not have: `restore_instance_data` is declared with `acks_late=True`,
`reject_on_worker_lost=True`, `soft_time_limit=3300`, `time_limit=3600`, and its messages carry the `timelimit`
header `[3600, 3300]`. This document covers those capabilities first (step 1); the task ports follow in their own
sections. See [celery-consumer](celery-consumer.md) for routing, the handoff rule and the base worker semantics.

## Step 1: prefork capabilities (`internal/celeryworker/prefork.go`)

A task registered with `Prefork: true` runs in a child process, one per execution, as Celery 5.6's prefork pool
(billiard `AsynPool`, the production pool on Linux) runs it. The consumer starts the child itself
(`celery-consumer --prefork-child`, the message on stdin, events on file descriptor 3). Tasks without `Prefork` keep
running in the consumer process as before; `celeryworker.New` refuses `AcksLate`, `RejectOnWorkerLost` or time
limits on them.

| Celery / billiard behaviour | Go |
|---|---|
| The child runs the trace: CoordinatedTask admission, the body, the stored result (and the retry publish) | `RunChild` → `perform` (shared with in-process tasks), then the `done` event |
| `on_accepted`: acks_late off acknowledges when the child accepts the job | `accepted` event → `Ack` |
| acks_late on: `on_success`, `on_retry` and `on_failure` (with `acks_on_failure_or_timeout`, the default) acknowledge after the outcome | `done` event → `Ack` |
| Soft limit: `SIGUSR1` to the child, `SoftTimeLimitExceeded()` raised in the task; unhandled → FAILURE `billiard.exceptions.SoftTimeLimitExceeded` `[]` | `SIGUSR1` cancels the body's context with `ErrSoftTimeLimit`; an unhandled cancellation is stored as that exception |
| Hard limit: AsynPool schedules it `hard - soft` after the soft one (or at `hard` without a soft limit); `on_timeout` stores `TimeLimitExceeded(hard)`, acknowledges an acks_late message, `_trywaitkill` sends SIGTERM, then SIGKILL after 0.1 s; the worker continues | parent timers, the same store/ack/kill order |
| Limits: `timelimit` header value when truthy, else the task's `time_limit`/`soft_time_limit` (Python `or`: a `0` in the header falls back) | `effectiveLimit` with `Task.TimeLimit`/`SoftTimeLimit` |
| Worker loss (child exits without a result): acks_late with `reject_on_worker_lost` → `QoS.reject(requeue=True)`: the message is restored redelivered at the far end of its queue (`restore_by_tag(leftmost=True)`), no result | `preforkLost` → `Transport.Reject(tag, true)` |
| Worker loss otherwise: acks_late acknowledges; FAILURE `billiard.exceptions.WorkerLostError('Worker exited prematurely: signal 9 (SIGKILL) Job: <n>.')`, `n` from billiard's per-process job counter | `humanStatus` (`signal N (NAME)` / `exitcode N`), `Worker.jobs` counts every task the worker executes |
| Retry from the body: the republished message keeps the request's `timelimit` (`as_execution_options`) and its argsrepr is the args tuple's repr; the stored RETRY meta lists the task's own id as a child | `EncodeRepublish` (time limits, tuple argsrepr), `perform(child=true)` adds the child |
| `delivery_info['redelivered']` visible to the task | `Message.Redelivered` |
| `CoordinatedTask.__call__` skips admission for names starting with `instance.restore.` | `Worker.coordinated` |

Messages with time limits are inside the decoded subset only for prefork tasks (`Message.PlainWith(true)`, both values
`None` or numbers); for other tasks they are still handed off.

### Documented differences

- billiard waits `worker_lost_wait` (10 s) before declaring a lost worker; Go reacts when the child exits. Only the
  timing differs.
- Celery keeps one long-lived child per slot (`worker_max_tasks_per_child=100`); Go starts one child per execution.
  Process-level state is not shared between tasks in either case that matters here (Django closes connections after
  fork).
- At shutdown the consumer waits `--drain-timeout` for running tasks; a prefork child still running then is left to
  finish on its own and its acks_late message, still unacknowledged, comes back through the visibility restore (Celery's
  warm shutdown waits without limit).
- Observed while characterizing (not changed here): with the production worker started as a separate process
  (`celery worker`, prefork or solo pool), a message published from inside the task body — including `Task.retry` — is
  recorded in the stored result's `meta.children`. The earlier consumer harnesses ran the worker in a thread of the
  harness process and saw no children for body publishes. The prefork tasks follow the separate-process oracle.

### Evidence

`TESTS/adapters/go/celery_consumer_capabilities_process.py` with the probe tasks of
`TESTS/adapters/go/consumer_restore_probe_tasks.py` (`consumer_restore.probe`: acks_late + reject_on_worker_lost,
`probe_lost`: acks_late, `probe_early`: early ack; defaults soft 1 s / hard 3 s; registered in Go only by
`-tags consumerprobe`, `cmd/celery-consumer/probe_tasks.go`). The Django side is a real worker subprocess
(`consumer_restore_worker.py`: `-P prefork -c 1 -Q assozeta-go`, django-db, CoordinatedTask; it forces prefork
because `core.celery` selects solo on macOS). Compared after each worker stopped: TaskResult rows, every broker key
with decoded messages, and the broker while a gated task runs. 13 scenarios:

acks_late holds the message unacknowledged during the run; early ack removes it; acks_late failure (FAILURE then ack);
acks_late retry (RETRY, its own id as child, retry message with `timelimit [30, 20]`, eta +600 s, held and restored);
worker loss with reject_on_worker_lost (requeued redelivered, then SUCCESS); worker loss without reject and with early
ack (WorkerLostError, `Job: 0.`); soft limit handled (SUCCESS) and unhandled (SoftTimeLimitExceeded); hard limit
(TimeLimitExceeded `[3]`, then the next message runs on the same worker); hard limit with early ack; task defaults
with `[null, null]`; a zero soft limit falling back to the default.

- Oracle: Django alone 13/13 (`consumer-restore-capabilities-oracle`).
- RED: probe tasks registered without the prefork options; Django passed, Go handed the time-limited message off and
  timed out (`consumer-restore-capabilities-process-red`).
- GREEN: 13/13 (`consumer-restore-capabilities-process/results.json`).
- Mutation (CAUGHT): acknowledging every prefork message at acceptance (acks_late ignored); during the gated run Django
  had one unacked entry and Go none (`consumer-restore-capabilities-process-mutation`).
- Unit tests: `go test ./internal/celeryworker` (effective limits, `human_status`, option validation),
  `./internal/celery -run RepublishKeepsTimeLimits`.
- Re-run after the shared changes (worker, `EncodeRepublish`): `celery_consumer_process.py`,
  `celery_consumer_email_process.py`, `celery_consumer_workflow_process.py` (receipts `consumer-restore-rerun-*`).
