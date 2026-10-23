# Audit retention task

`AuditRetention.Run` implements the body of
`application.tasks.delete_old_audit_logs`. The internal version-1 envelope requires
the exact task kind, nil association scope, an empty object and a running fenced
job. Invalid scope/arguments fail before SQL. The retention period is not supplied
by a caller: the service uses execution time in UTC minus exactly 365 days.
Equality survives, with microsecond comparisons preserved. A leap-year fixture
uses 2032-03-01 and a 2031-03-02 cutoff, distinguishing duration from calendar year.

The task is instance-wide across content types, actors and associations. Logs
without an association index remain eligible; deleted associations do not exempt
logs. An index is removed because its parent log expired, independently of the
index's creation time. The oracle checks the actual reverse model relationships
so a newly introduced dependant cannot silently escape this contract.

The repository replaces Django's collector cascade with one parameterized SQL
command: DELETE expired logs RETURNING identifiers, then DELETE their indexes.
It returns the aggregate number of deleted logs plus indexes, matching the legacy
collector count. No deleted identifier set is loaded into Go. A READ COMMITTED
transaction joins effect admission, both deletions and fenced job completion; the
existing deferred foreign key remains enforced. `BeginAuditRetention` receives the
job and acquires the shared server restore fence, then checks the live Go ownership
generation, lease token, running state and unexpired lease before returning the
transaction. The shared ownership-row lock keeps the generation stable while
effects run without locking the job row, so heartbeat renewal remains possible.
There are no schema changes, new audit records, provider calls or child jobs.
Retained rows and all other managed models are compared exactly through Django.

Django catches collector errors and returns `None`. Go rolls back a failed delete,
emits a fixed diagnostic code and completes the handled NULL result in a fresh
transaction. Real triggers independently fail each delete; neither leaves partial
retention. Early stale ownership or lease loss sends no deletion SQL. A token or
lease change after deletion rolls both deletes back, and replay is rejected before
effects, preserving records created after a completed occurrence. Cancellation
leaves recoverable work and pool reuse is verified.

A concurrent index writer is tested in both runtimes. Its parent-key lock holds
retention after the statement/collector has selected rows; the writer then commits
an index that the deletion snapshot cannot see. The deferred foreign key rejects
the retention commit, preserving every log and index. Django catches that failure
and returns `None`. The first Go implementation propagated the error, and a
Django-based expectation failed. The corrected repository classifies PostgreSQL
SQLSTATE 23503 at COMMIT as a definite rollback; the service records the same
handled NULL result in a new transaction. Completed occurrence replay cannot
retry the deletion, while a new occurrence can remove the now-visible child.
That completion-only transaction has its own pre-effect admission fence. The real
concurrent-index test repeats with unchanged authority, a new Django ownership
generation and a replaced token immediately after PostgreSQL rejects COMMIT.
Only unchanged authority records the handled NULL result; lost authority rejects
fallback before its completion SQL, preserving every log/index and a running job.

Only that definite FK rejection receives this treatment. Transport/unknown commit
errors, cancellation, failed operational completion and other commit errors
propagate. A deferred constraint trigger with SQLSTATE 23514 proves it is not
swallowed: all effects and completion roll back, and the connection pool recovers.
An uncertain commit still requires the executor's durable outcome readback; it is
never declared handled merely because COMMIT returned an error.

The focused adapter requires twelve Go/PostgreSQL groups under race detection after
running the actual Django task. Cases include exact cutoff/readback, invalid input,
both delete failures, late lease loss and replay, two observed overlapping retention
commands, the concurrent deferred-FK index writer, unrelated commit failures,
cancellation, query scaling/plan inspection and actual executor heartbeat. Missing
or skipped groups fail the adapter. Two admission groups prove that a changed owner,
generation, token or expired lease sends no business SQL, and that a handled failure
rolls back before a completion-only transaction checks ownership again.

Django uses 5/5/6 statements at 1/16/128 indexed expired logs in the fixture. Go
uses six at 0/1/16/128/512: BEGIN, shared server restore fence, live job ownership
fence, deletion, completion and COMMIT, excluding worker claim/admission/heartbeat
and test setup/readback. EXPLAIN ANALYZE of the actual command over 4,096 logs and
indexes deletes 128 of each and rolls back the experiment. The observed plan scans
both tables; 3,968 retained logs remain. There is no claimed index or latency
improvement. PostgreSQL may materialize the intermediate IDs, and realistic
scale/contention remains unverified.

Run production Python with `TESTS/adapters/django/accounts.py --only audit-retention`.
`--only task-bodies` runs all four currently implemented task fixtures in one private
schema; the complete accounts adapter also includes them. Evidence is under ignored
`quality-reports/go-migration/audit-retention/`, `audit-admission-review/` and
`task-bodies/`. Removing the early fence reproduces stale attempts sending business
SQL; the restored implementation passes all twelve groups. Independent review
checked the deferred-COMMIT callback ordering and post-effect lease-loss coverage.

Observed Celery policy has early acknowledgement, max_retries 3, default delay 180
seconds, no rate limit and no explicit hard/soft timeout; the body does not invoke
retry. The static midnight schedule is absent from initialized app configuration
and persisted Beat rows in the fresh fixture. Production registration/publication,
occurrence identity, timeout/attempt policy, historical schedule/schema rehearsal,
worker/scheduler ownership, restore/switching and release delivery remain open.
The ledger stays `implementing`; Django remains the production owner.
