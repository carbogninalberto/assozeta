# Durable PostgreSQL job repository

This is the Assozeta extension required by specification §5.5, following the
inspected `5025d2a` Kuayle `DevMachineRepository` claim/lease pattern. Kuayle does
not supply a general Celery queue. No Celery message serialization is assumed.
The repository is used by the explicit `service.JobExecutor` and its ordinary-job
restore admission. It is not connected to the HTTP root, a production worker
entrypoint or a scheduler.
Django retains every existing task and scheduling owner.

The additive runtime ownership fence now defaults to Django and prevents Go
claims until an authorized Go generation exists. No production handoff API is
provided. See [ownership and transaction fencing](job_ownership.md) for the
generation checks, effect boundaries and private fixture-only transitions.

`JobIntent` records a UUID, trusted association scope, kind, argument version,
JSON payload, stable idempotency key, availability and explicit attempt limit.
The nil association UUID is reserved for instance-wide work, never a fallback for
missing authentication. These operational references deliberately have no foreign
keys into Django tables: jobs must remain inspectable after business deletion or
restore. The executor applies ordinary-job restore fencing before dispatch;
replacement restore and coordinated backend switching remain separate work.

`Enqueue` requires the caller's sqlx transaction. Business mutations and durable
intent commit or roll back together. Deduplication is permanent within association,
kind and key: duplicates return the original job, including terminal state and
publication time; they never reopen work. A different payload, argument version
or attempt policy produces a conflict. JSONB comparison is semantic and retains
large integer precision. The first schedule wins. This does not deduplicate a
caller's business mutation; its service must enforce that invariant separately.

Workers supply explicitly supported kinds and an owner. Claiming uses one SQL
statement and `FOR UPDATE SKIP LOCKED`; active leases cannot be claimed again.
Each claim invocation has a fresh random fencing token. Completion, failure and
renewal require the job ID, current token, running state and unexpired lease.
Reusing the same worker name cannot revive an earlier token. Renewal cannot
shorten an existing lease, even if a caller supplies a smaller duration. Database wall-clock
checks occur when performing the ownership update. A crashed worker's expired
lease can be reclaimed until the explicit attempt limit; an exhausted lease is
marked failed, rather than remaining permanently running. Empty claim results can
also mean that exhausted jobs were terminalized; an executor must keep polling.

Claim size and kind count are each limited to 64. Each kind's pending and expired
branches select through separate due-time indexes before merging a bounded set.
At most `2 × batch size × distinct kinds` candidates are temporarily row-locked;
unused locks are released at statement completion. Stable statement time permits
index range filtering. No per-job query or application-side table filtering is
used. Services must still establish task-specific fairness, rates and priorities.

`Fail` records only an explicit machine code and caller-selected bounded delay,
then retries or terminalizes according to the persisted attempt limit. It does
not invent Celery retry policies. `CompleteInTx` lets database-only service
effects and successful completion share one transaction; any error requires full
rollback. `Complete` supports a separate completion after idempotent external
effects. Delivery remains at least once: a provider action followed by a crash
can be replayed and needs its own idempotency boundary. Lease checks cannot undo
an external call. Result/payload size is capped at 64 KiB, keys at 512 bytes,
owners/codes at 128, and attempts at 100. Durations have millisecond resolution; task migrations must reconcile these
operational bounds before claiming argument compatibility.

Scoped reads and pending-job cancellation include the association predicate.
Pending cancellation is idempotent. Running/terminal work cannot be silently
cancelled by this method; cooperative running cancellation requires the executor
and a per-workflow policy. Domain rows include private execution tokens and are
not a public response DTO.

The private PostgreSQL adapter runs ten scenario groups with race detection:
atomic intent and effects, durable scoped deduplication, conflicting payloads,
mixed kinds/states, 32 claims across eight concurrent consumers and four database
connections, renewal/expiry/stale-token denial, delayed and exhausted retries,
pending cancellation, actual SIGKILL recovery, and 16 concurrent publishers.
Query counts stay at one claim statement over 1/16/128/512 jobs. The actual claim
EXPLAIN on 4,096 jobs originally scanned the whole candidate table; the corrected
plan uses the pending, expired and primary-key indexes, visiting only the requested
branch batches. This is bounded fixture evidence, not a latency claim or a full
production workload benchmark. Four concurrent migrators preserve the separate
history and the synthetic Django-history sentinel; the down guard retains jobs.

Run `python TESTS/adapters/go/jobs.py`. Evidence, including initial failures and
the actual SQL plan, is under `quality-reports/go-migration/jobs/`. This fixture
uses a private cluster and synthetic business/history tables, not historical
Django migrations. No production database or release was changed.

The executor now supplies version checks, heartbeats, cooperative drain and
ordinary-job restore/stale-task admission; see `internal/service/job_executor.md`.
Remaining work includes task services and their complete ledger contracts,
production registration and process supervision, task-specific retry/backoff/
timeouts/rates, progress and cancellation APIs, scheduler occurrences and ownership,
backend switching, retention,
operator controls, and normal migration/image/updater delivery. No task becomes
implemented or verified solely because this repository exists.
