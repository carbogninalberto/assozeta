# Associate age update task

`AssociateAgeUpdate.Run` implements the body of
`application.tasks.update_age_of_associates`. Its trusted internal envelope requires
version 1, the exact task kind, a running fenced job, nil association scope and an
empty JSON object. Callers cannot supply the date or select another scope.

The existing `Associate.calculate_age` truncates elapsed days divided by 365.25.
Its `>= 18` threshold is therefore 6,575 days, not a calendar-birthday comparison.
For example, on 2030-03-01 the task retains a minor born on 2012-03-01
and promotes one born on 2012-02-29. The service computes the execution date in
UTC (the configured Django timezone) and passes a birth-date cutoff to the
repository. NULL/future birth dates remain
unchanged. The task only changes `is_minor=True, deleted=False` associates; disabled
and draft records, every group, deleted associations and NULL associations remain
in scope. This preserves the ordinary background manager behavior without a
request-local group. Existing false flags are not recalculated or reversed.

The explicit repository command updates only `is_minor` and derives UPDATE audit
logs from rows actually changed. Auditlog 3.4.1 records the string pair
`{"is_minor":["True","False"]}`, the existing object representation and background
NULL actor/network metadata. Each non-NULL association receives an `AuditLogIndex`
with resolution path `direct`, including a deleted association. NULL association
has no actor fallback and produces no index. UUID index identifiers use PostgreSQL's
`gen_random_uuid()` (available in the existing PostgreSQL 16 deployment); there is
no business-schema change. Fixed-clock Django readback verifies audit/index
timestamps and every unrelated associate field. Unrelated audit/index sentinels
and valid zero-UUID associate identities are covered.

READ COMMITTED transaction coordination joins the update, audit, index and fenced
job completion. Both the effect transaction and the handled-failure completion-only
transaction acquire the shared server restore fence first, then verify the live
job token, lease and generation while retaining a shared ownership-row lock.
A stale token, expired lease, changed generation or Django owner therefore sends
no business statement. Ownership cannot change while admitted effects are in
progress. No job-row lock is held during effects, so heartbeat renewal can continue;
completion still rechecks the live lease and rolls back effects after late lease loss. One SQL command fixes the candidate snapshot; aggregate counts
verify that every update has an audit and every required association has an index.
No candidate rowset is loaded into Go. Heartbeat renewal remains visible at
completion. A stale attempt or replay rolls back all business effects, so it cannot
undo a later correction to an associate's flag.

There are deliberate atomicity corrections, established with real Django triggers:

- A failed Django UPDATE leaves a committed pre-save audit and association index,
  although `is_minor` remains true. Go rolls back the whole invariant.
- A failed Django index INSERT is caught by its signal; the update and audit still
  commit without the index. Go rolls back that incomplete invariant too.
- Django's loop may commit earlier rows before a later row fails. Go applies the
  task's database effects atomically with completion, as required by the migration.

The legacy task catches save errors and returns `None`. After a business failure,
Go rolls back and records that handled NULL result in a fresh fenced transaction,
with a fixed diagnostic code. Cancellation, rollback failure, failed completion
and lost ownership remain errors. The adapter verifies the two legacy defects;
the Go integration test injects failures independently into UPDATE, audit INSERT
and index INSERT. A successful task never leaves a partial audit/index invariant.

The adapter first runs the actual Django task, then requires eleven Go/PostgreSQL
scenario groups under race detection. Missing/skipped groups are rejected. They
cover exact selection and readback, invalid envelopes without SQL, failures,
stale ownership/replay, two observed overlapping commands, blocked SQL cancellation
and pool recovery, scaling/plan inspection, and real executor heartbeat. Three additional required groups prove stale
admission sends no business SQL, ownership is held both before and after actual
effects with late token loss rolling everything back, and the completion-only
transaction rejects ownership or token loss after the business rollback. The
control failure still completes once with NULL result and unchanged business data.
Django uses 9/114/898 statements for 1/16/128 eligible associates in this fixture.
Go uses six at 0/1/16/128/512: BEGIN, shared server restore fence, early job
ownership/lease fence, the update/audit/index command, completion and COMMIT.
The previous five-statement count lacked early job admission; the additional
statement is explicit protocol cost, including the no-op business case. These counts exclude claim/admission/heartbeat and test setup/readback.
EXPLAIN ANALYZE of the actual command over 4,096 rows updates, logs and indexes 128.
The observed plan uses the existing birth-date bitmap index and content-type unique
index. PostgreSQL can materialize intermediate rows; statement count does not prove
constant database work or a latency improvement. Realistic scale and contention
remain separate requirements.

Run production Python with `TESTS/adapters/django/accounts.py --only
associate-age-update`; the full accounts adapter also includes it. Evidence lives
under ignored `quality-reports/go-migration/associate-age-update/`. The observed
Celery task has early acknowledgement, max_retries 3, default delay 180 seconds,
no rate limit and no explicit hard/soft timeout. The body never invokes retry.
The static midnight declaration is absent from both initialized app configuration
and persisted Beat rows in this fresh fixture. Historical schedule reconciliation,
production publication and coordinated registration/ownership, timeout/attempt policy, restore/backend
switching, mixed-runtime mutation races and release delivery remain open. Django
reads the date separately for each candidate, whereas Go fixes it at invocation;
long-running work across midnight still needs an explicit lifecycle contract.
The task stays `implementing`; Django retains production ownership.
