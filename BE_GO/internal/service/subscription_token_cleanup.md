# Expired membership-card token cleanup

`SubscriptionTokenCleanup.Run` implements the body of
`application.tasks.clear_expired_subscription_tokens`. `SubscriptionToken` backs
the membership-card links minted and consumed by `subscription_card` in
`application/views/subscriptions_views.py`. The task takes no arguments and runs
instance-wide. Its Go envelope requires the exact kind, version 1, an empty object,
nil association scope and a running fenced job. Invalid envelopes fail before SQL.

The cutoff is midnight of the execution date in UTC, matching
`datetime.now().date()` with the existing `TIME_ZONE='UTC'` and Django's date-to-
datetime coercion. Expiry equal to midnight survives. A token that expired earlier
today also survives until a later cleanup date; the public card view separately
checks expiry against the current instant. The task's cutoff is never accepted
from a job payload. Tokens belonging to deleted associations/subscriptions and
valid historical zero UUIDs remain within the original unfiltered scope.

The service owns cutoff calculation, the explicit audited-deletion command,
transaction boundaries, handled-error policy and fenced completion. The repository
uses one PostgreSQL `DELETE ... RETURNING`/audit `INSERT` command, deriving audit
records only from tokens actually removed. It returns aggregate counts rather
than loading the token set into Go. Completion shares the transaction, so lost
ownership or replay of a completed attempt rolls back both deletion and audit.

The audit matches the installed Django auditlog 3.4.1 behavior: DELETE action,
the five token fields mapped from their old string values to the string `None`,
canonical UUIDs and naive UTC date strings with exactly six fractional digits
when microseconds are nonzero. The record representation and all nullable
actor/network/serialized/additional fields match Django. Injected audit timestamps
are compared exactly. Django emits the log after deletion; its resolver can no
longer fetch the token and a background task has no actor/direct association
fallback. Therefore no `AuditLogIndex` is created. Unrelated audits are preserved.

There is an explicitly tested concurrency correction. Two real Django collectors,
synchronized after collection, delete three tokens but emit six audits: each
collector sends `post_delete` for its original objects even if its SQL removed no
row. The intended Go contract is one audit per token actually deleted, as required
for idempotent effects. Two overlapping Go SQL commands settle with three audits.
This difference is deliberate and recorded in the ledger; mixed Django/Go deletion
races and the public card endpoints still need their own lifecycle verification.

The task catches deletion/audit errors and returns `None` without retry. Go
preserves that handled outcome after a full rollback, emits a fixed diagnostic
code rather than database details, and completes the job with SQL NULL result in
a fresh transaction. Cancellation, rollback failure, failed operational completion
or lost ownership propagate. A handled audit failure leaves every token intact.

The transaction uses READ COMMITTED so completion can observe a lease renewed by
the executor. An initial repeatable-read implementation passed the ordinary cases
but failed a deliberate slow-delete test when heartbeat changed the job row.
The corrected single-command deletion has one PostgreSQL statement snapshot and
allows heartbeat renewal while keeping deletion/audit/completion atomic. The test
with real renewal now passes; it is retained as a regression guard.

The focused adapter runs the actual Django task before ten mandatory Go/PostgreSQL
groups under race detection. Cases cover cutoff and exact readback, invalid scope/
arguments, audit-trigger failure after deletion, stale completion, replay preserving
later tokens, forced overlapping cleanup, blocked SQL cancellation/pool recovery,
query counts, actual heartbeat overlap and an executed query plan. It rejects
missing/skipped groups and verifies complete managed-row cleanup afterwards.

For 1/16/128 tokens, Django uses 7/52/389 SQL statements in the captured fixture.
The Go service uses five statements for 0/1/16/128/129/257/512 tokens: BEGIN, shared transaction fence, the
audited deletion, fenced completion, COMMIT. These counts exclude worker claim,
restore admission, verification and heartbeats. EXPLAIN ANALYZE of the actual SQL
over 4,096 tokens deletes/audits 128 and filters 3,968 in a sequential token scan;
content-type lookup uses its existing unique index. There is no new business index,
no constant database-work claim and no latency benchmark. PostgreSQL may materialize
the statement's intermediate rows; Go does not materialize them. Realistic scale,
contention and coexistence workloads remain open.

Run production Python with `TESTS/adapters/django/accounts.py --only
subscription-token-cleanup`. Evidence is in ignored
`quality-reports/go-migration/subscription-token-cleanup/`. The full accounts
adapter also includes this fixture. The captured Celery task policy has early
acknowledgement, max_retries 3, default_retry_delay 180 seconds, rate limit `60/m`
and no explicit hard/soft timeout; its body never calls retry. The Go executor
does not yet implement that rate policy. Scheduler reconciliation, production
registration/publication, task timeout/attempt policy, historical migrations,
backend switching and release delivery are still required. The ledger remains
`implementing`, and Django retains production ownership.
