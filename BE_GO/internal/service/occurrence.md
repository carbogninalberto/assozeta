# Transactional periodic publication

`OccurrencePublisher.PublishNext` evaluates one explicitly selected persisted
Django Beat row. It is an Assozeta extension using the existing durable jobs,
explicit service coordination and parameterized repositories. No command or
installed service invokes it yet; Django remains the production scheduling owner.
It neither scans candidates nor recreates lower-priority static declarations.
The supported timing/transport subset is described in
[scheduler authority](../../../docs/architecture/go-migration/scheduler-authority.md).

The service requires the existing shared lifecycle file lease, checks for a
pending restore, then opens a database transaction. The repository locks the Go
ownership generation first, the selected source second, and its interval/cron
definition third. Source and definition locks use NOWAIT: a Django editor may
lock these in reverse order, so a contended publication rolls back instead of
waiting in that cycle. PostgreSQL supplies the evaluation clock after these
locks. The service then evaluates the immutable snapshot.

A due dispatch commits three things together: a daily-reset v1 job with empty
arguments and instance scope, a retained occurrence receipt, and the existing
source's last_run_at/total_run_count/date_changed. Normal Django reservation does
not update the PeriodicTasks change marker; Go preserves that behavior. Errors
at enqueue, receipt insert, cursor update or deferred commit roll back all three.
The file lease remains held through transaction completion and local driver/socket
cleanup, including automatic cancellation rollback; see the precise
[transaction lifetime contract](../repository/transaction.md).

Occurrence identity is source ID plus an increasing retained dispatch sequence.
It excludes ownership generation, mutable source name and resettable Django run
count. Deleting/recreating a source ID therefore continues its retained sequence.
The operational receipt records the definition digest, generation, source name,
dispatch time and job ID. Migration 3 owns this separate table; it has no foreign
key to Django's source, and its job reference prevents deleting retained intent.
No receipt pruning or destructive down migration is supported.

The job allows three total operational attempts, an explicit bounded crash
recovery policy. This is distinct from Celery's early acknowledgement and the
daily-reset body's handled UPDATE failure, which completes without retry. A
third abandoned execution becomes terminal without another publication receipt.
Atomic enqueue also intentionally strengthens Django's observed failed-send
boundary; it cannot establish whether older Celery messages were delivered.

The required adapter uses actual pinned Django Beat migrations in private
PostgreSQL. Restore/business sentinels remain synthetic and are labeled as such.
It requires every named case and rejects skips. Tests cover concurrent publishers,
source/definition editor contention, ownership transitions, all transaction fault
points, retained sequence across source reuse, lifecycle lease lifetime and crash
exhaustion. Separate native/image checks validate schema preflight and the
packaged worker. Run from the repository root with production Python:

```sh
python TESTS/adapters/go/occurrence.py
```

`NextCheckAfter` is a raw timing result: a future scheduler must cap polling and
reload definitions. Candidate selection, per-row latency and large-schedule query
bounds are not established by this exact-row API. Installed activation still
requires quiescing Django producers, resolving pending Celery work, coordinating
ownership/cursors and recovering interrupted handoff. Backup restore can rewind
receipts and generations; process draining and the shared operations lease remain
necessary. This component does not establish complete scheduler or rollback parity.
