# Draining cooperating Go database transactions

The lifecycle file lease prevents new admitted application work while backup or
restore runs. It cannot, by itself, establish the outcome of a database command
after a client disconnects. Real PostgreSQL tests reproduce a COMMIT completing
after local pgx cleanup and exclusive file-lease admission when the separate
cancel-request connection fails. Ordinary deferred triggers control timing in
these tests; they do not swallow PostgreSQL cancellation or replace SQL outcomes.
Actual queue mutations reproduce the same gap with implicit transactions.

The Go transaction boundary therefore acquires a shared **transaction-level**
advisory lock in a separate first statement, before sending any business or queue
mutation. Its two integer keys are `(1095981639, 1381192786)` (`ASZG`, `RSTR`).
PostgreSQL retains that lock through the server transaction's actual commit or
rollback, even if the client has already returned an ambiguous error. This is an
operational protocol within the existing database, with no business-schema change.

All seventeen existing explicit transaction paths use the boundary. The five
standalone queue mutations—claim, renew, complete, fail and cancel-pending—now
also use an owned transaction. Queue operations accepting a caller's transaction
require that caller to establish the same boundary. Validation that previously
failed without SQL remains before transaction admission where applicable.

The separate admission statement matters. If cancellation occurs while it waits,
the original canceled context prevents the client from sending later effect SQL.
A same-statement locking CTE could still execute its mutation after a delayed
lock acquisition; it is not equivalent. The helper retains the original context
and joins local cleanup as described in the [transaction contract](../../../BE_GO/internal/repository/transaction.md).

Under the existing exclusive file lease, in-app Django restore acquires the
exclusive transaction form of the same advisory lock in a short transaction,
before reading the restore operation, incrementing attempts or starting recovery
export. A five-second transaction-local statement timeout fails closed through
the existing `Busy` deferral path. No export or replacement begins on that path.
After the exclusive barrier is acknowledged and committed, the file lease stays
exclusive throughout export and replacement, preventing new cooperating writes.
An enclosing transaction is rejected because it would retain the barrier and
could supply an earlier snapshot. SQLite retains its existing behavior: Go has
no SQLite backend and cannot have transactions to drain there.

The self-host CLI uses the same short exclusive transaction before `pg_dump` and
before Django/Go schema migration. It requires its actual operations lease and
keeps applications stopped while draining. PostgreSQL errors or timeout prevent
the snapshot or migration. Historical distributions without the Go operational
command keep their existing lifecycle. Database replacement retains its existing
session termination and database-rename checks.

Lock order is file lease, database transaction fence, then ownership and
source/job/business locks. No session advisory lock is used, so transaction-mode
PgBouncer may reassign backends between completed transactions. The new first
statement also establishes a repeatable-read snapshot earlier than the previous
first business query; isolation and whole-transaction conflict handling remain
explicit and are covered by the existing concurrency regressions.

The protocol adds one shared-lock statement per existing explicit transaction.
A standalone queue mutation now has BEGIN, shared-lock admission, its mutation
and COMMIT on success. Query-count assertions include this fixed protocol cost;
historical Django fixture counts remain unchanged. Cardinality does not add a
per-record fence. Cancellation deadlines stop further work, while required local
cleanup may still extend elapsed return time.

Required checks include fifteen queue scenarios across all five mutation paths,
actual transaction-pooler backend reassignment, canceled admission, late remote
commit and the real CLI drain. Separate actual Go→Django tests check restore
ordering, timeout without attempts/export, fresh post-drain observation and
absence of pooled timeout leakage. Fixtures use private PostgreSQL/PgBouncer;
synthetic rows and export collaborators establish ordering, not complete backup
graph or storage parity. Native worker, image and lifecycle checks remain separate.

```sh
python TESTS/adapters/go/server_fence.py
python TESTS/adapters/go/restore_drain_pooler.py
python TESTS/adapters/django/restore_server_barrier.py
```

This guarantee covers cooperating Go database writes and requires the existing
shared file-lease admission protocol. It does not fence every Django writer,
Redis, object storage or external providers, resolve ambiguous commits for the
original caller, activate Go scheduling, or establish complete backend handoff.
Older binaries without the protocol still require independently verified draining
before mixed-version operation. These remain part of the fourteen acceptance gates.
