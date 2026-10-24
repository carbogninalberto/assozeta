# Durable job runtime ownership

Migration 2 initializes the operational ownership row to Django, generation 1.
It preserves all existing job intent, lease fields and Django schema/history.
Go claims stamp the current generation only while the runtime is Go. Django or
missing ownership produces no claims and spends no attempts. A missing table or
database failure propagates; the worker never migrates implicitly.

Claim, renewal, failure and completion require the current Go generation and
retain a shared ownership row lock through their transaction. Ownership changes
therefore wait for admitted completion transactions to commit or roll back.
The claim query's lateral limit depends on the locked generation, requiring owner
evaluation before candidate job locks. Private PostgreSQL tests also hold an
owner update, block each operation, and verify that the owner transaction can
still lock both pending and running jobs immediately.

Daily email reset acquires the same ownership fence before its first business
write, without locking the job row or preventing concurrent lease renewal. Its
effects and completion commit atomically. Other existing database-only tasks
retain the completion fence: they must roll back the entire transaction on any
failed completion. Their business writes can precede owner locking, so their
lock order and potential deadlock/retry policies require review before production
registration. No automatic retry of ambiguous transactions is introduced here.

A Go → Django → Go round trip increments the generation: an old attempt cannot
renew, fail, complete or commit a reset even if its lease has not expired. A new
claim after lease expiry receives a fresh token and generation. Executor heartbeat
failure cancels admitted work and stops intake. This fences database effects;
it cannot undo an external provider call or make that call exactly once.

There is no production handoff setter, scheduler or operator activation flag.
Only trusted private fixture SQL changes ownership, using generation compare and
swap for competing transitions. This row does not fence historical Django
processes or drain Celery messages. Production handoff still requires coordinated
publication, scheduler, consumer, request and restore/rollback protocols.

Restoring a database backup can rewind both the ownership generation and job
lease tokens. This fence alone cannot exclude a stale process across restore.
The existing job admission file lease must remain held through execution, and
the future deployment lifecycle must stop every Go worker before replacing data.
No Go worker Compose service or complete handoff/restore window is delivered by
this change. Durable enqueue remains an explicit transactional repository
primitive; fencing production publishers is also part of the future handoff.

`python TESTS/adapters/go/jobs.py` requires eleven ownership and four migration
scenario groups with real PostgreSQL and Go race detection, in addition to the
existing ten queue and thirteen executor groups. Missing or skipped groups fail
the adapter. Migration tests upgrade populated version-1 operational tables,
preserve Django-history sentinels, refuse destructive down migration and reject
dirty history without forcing a version. Evidence is under the ignored
`quality-reports/go-migration/jobs/`; initial failure evidence is separately under
`quality-reports/go-migration/job-ownership/`.
