# Native expired-token cleanup worker

The existing worker command now registers `clear_expired_subscription_tokens`,
version 1, alongside daily email balance reset and associate age update. It uses
the existing environment, restore admission, bounded pool, task deadline,
durable-job executor and graceful-drain path. This is command composition only:
it does not publish work, install a service, change runtime ownership or activate
the scheduler. Django remains the default owner.

The accepted envelope is an instance-wide running job with no association scope,
an empty JSON object and version 1. Invalid versions, payloads and tenant scope
are rejected without a token or audit mutation. The service keeps Django's UTC
midnight cutoff and preserves the exact deleted-token audit representation,
including deleted associations and subscriptions, valid zero UUIDs, no audit
index and a NULL business result.

Both the effect transaction and the completion-only transaction used after a
handled SQL/audit failure perform the shared restore fence and the live
owner/generation/job lease fence before effects or completion. A stale token,
expired lease, changed generation or Django owner cannot delete a token or record
handled success. The effect transaction retains the ownership lock through its
single audited deletion statement and fenced completion, while heartbeat renewal
remains possible because the job row itself is not locked for the effect.

## Native proof

Run with the pinned production Python and existing test dependencies:

```sh
python TESTS/adapters/go/token_worker_process.py
```

The adapter builds race-instrumented native worker and migration commands, starts
private PostgreSQL, creates the actual current Django model schema with `syncdb`,
then uses the real worker executable and command registration. It does not prove
historical migration or imported data compatibility. Every queue intent,
ownership change and restore row belongs to the disposable fixture.

Nine required groups cover default Django and missing ownership, two workers with
one exact cleanup, unsupported envelopes, replay after a later expired token,
exclusive maintenance and pending restore, SIGKILL recovery, final-attempt
exhaustion, SIGTERM cancellation and handled audit rollback. The seven-token
fixture checks the exact UTC-midnight boundary, retained same-day and later
tokens, three deletion audits, deleted association/subscription scope, absent
audit index, unrelated audit/index sentinels and complete managed-model readback.
The handled failure completes once with a NULL result only after all tokens and
audits roll back. Diagnostics do not expose the injected database error.

The focused Django/PostgreSQL integration contract separately requires twelve
Go groups, including stale token, expired lease, generation and Django-owner
admission with zero business commands; completion-only fencing after handled
failure; six SQL statements at increasing affected-row counts; blocked-query
cancellation and pool recovery; heartbeat renewal; and the existing 4,096-token
plan. PostgreSQL can materialize intermediate rows, so statement count is not a
constant-database-work or latency claim.

This native rehearsal does not itself establish packaged-image behavior; the
separate [image rehearsal](token-worker-image.md) covers that slice. PgBouncer
deployment parity, production process supervision, rate limiting, installed
ownership activation, scheduler reconciliation, mixed-runtime races, historical
migrations, restore/rollback coordination and release delivery remain open. A successful
report covers only the scenarios listed in the adapter. Parent execution passes
all nine native groups and all twelve Django/Go PostgreSQL body groups under race
detection. Removing the early admission check reproduces stale attempts reaching
business SQL; restoring it passes the same regression. The native fixture's initial
datetime-name error is retained separately from the corrected passing run.
Evidence is under ignored `quality-reports/go-migration/token-worker-process/`,
`subscription-token-cleanup/` and `token-worker-review/`. These are local native
receipts, not a clean packaged release or complete task-policy parity claim.
