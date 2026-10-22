# Daily communication counter reset

`EmailBalanceReset.Run` implements the body of Django's
`reset_communication_daily_email_balance` (`application.tasks.reset_email_balance`).
It is an instance job with no arguments: the Go envelope requires version 1, an
empty object and the reserved nil association UUID. It rejects association-scoped,
wrong-kind/version or invalid execution envelopes before database access. No HTTP
route, publisher or scheduler is enabled. The packaged native worker explicitly
registers this body, but no installed Go worker service or ownership activation
exists; see [the process boundary](../worker_process.md).

The supported mutation is one bulk UPDATE of
`communications_communicationconfiguration.daily_email_balance` where the value
is greater than zero. All associations, including soft-deleted associations, and
NULL association references are included, matching the unfiltered Django manager.
Zero balances, configuration IDs, SMTP fields and limits remain unchanged. Django
bulk UPDATE bypasses model save hooks and auditlog; Go likewise creates no business
audit, email, provider request or child job. No SMTP credentials are selected.

The service owns an explicit transaction across the update and the current job's
fenced completion. Completion has a nullable result, corresponding to Django's
`None`. Lost ownership rolls back the counter update. Permanent job-key deduplication
prevents a completed occurrence from resetting usage recorded later. Direct replay
of a stale/completed attempt also rolls back. The future scheduler must assign one
stable key per occurrence and coordinate runtime ownership; different keys are
different resets, and this component cannot make two schedulers authoritative.

The legacy task catches UPDATE exceptions, prints an error and returns `None`
without retry. A real PostgreSQL trigger failure verifies this behavior. Go rolls
back that failed transaction, records a fixed diagnostic code without database
details, and completes the same leased job in a fresh transaction with no business
effects. This preserves the legacy handled-error outcome; it does not prove that a
failed reset succeeded. Cancellation, failed rollback, failed completion or lost
ownership propagate so the executor can retain/recover or terminate work according
to its persisted attempt policy. Go's extra operational completion can fail where
Django has no equivalent write; no cross-broker result protocol is claimed.

The captured actual Celery policy has early acknowledgement, max_retries 3 and
default_retry_delay 180 seconds, with no explicit rate, hard or soft time limit.
The body never invokes retry. It stores results and does not track started state.
The initialized app uses UTC with UTC enabled. Although `core/celery.py` declares
a midnight occurrence, the initialized configuration in this fresh model-schema
fixture has no occurrence for this task, and its Beat table is empty. Historical
database schedules and operator overrides still need reconciliation. The test does
not invent a new active midnight timer from the static declaration.

Run production Python with `TESTS/adapters/django/accounts.py --only
email-balance-reset`. The fixture creates its own PostgreSQL cluster and actual
Django model tables. Django first establishes empty/mixed/zero/max-int/repeated
reset behavior, no unrelated managed-row or audit changes, one UPDATE at
1/16/128/512 rows, and an atomic handled SQL error. Go then passes seven required
race-detected groups: real executor dispatch and Django readback, invalid envelope
denial, replay preserving later usage, stale completion rollback, handled SQL
failure and redacted diagnostics, blocked UPDATE cancellation/pool recovery, and
bounded query counts. Missing or skipped groups fail the adapter. All fixture
records are removed, with complete managed-table readback checked afterwards.

The Go success path uses five statements (BEGIN, ownership fence, bulk UPDATE, fenced completion,
COMMIT) for 0/1/16/128/512 positive counters within 512 configurations. Its inspected
UPDATE plan uses a sequential scan, appropriate to the intentional global reset;
no index or business schema change is introduced. This demonstrates bounded query
count and no application-side materialization, not constant database work or a
latency improvement. Realistic large-installation and concurrent SMTP accounting
workloads remain required.

Evidence is in ignored `quality-reports/go-migration/email-balance-reset/`, including
the initial Go compile failure after the passing Django oracle and the subsequent
passing integration, runtime-policy capture and query plan. This is current-model
schema evidence, not historical migrations, scheduler/delivery/rollback ownership,
production timeouts, or complete task parity. The ledger remains `implementing`.
