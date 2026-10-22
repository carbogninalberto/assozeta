# Transaction completion and lifecycle admission

Explicit repository transactions retain a private `sql.Conn` through completion.
They keep the caller's original context, isolation and read-only options. Services
and middleware retain their existing lifecycle lease until the repository returns.

An actual PostgreSQL regression exposed why a deferred `Tx.Rollback` alone was
insufficient: `database/sql` marks a transaction done before its automatic rollback
calls the driver. Another Rollback can return `ErrTxDone` while the driver remains
paused and PostgreSQL still holds the transaction's row locks. The old worker and
HTTP paths then released their lifecycle lease prematurely; a read-only snapshot
also returned before its rollback completed.

`ownedTransaction` captures pgx's immutable cleanup channel while it owns the
driver connection. A successful synchronous commit or rollback returns the
connection to the normal pool. An error, cancellation or concurrent `ErrTxDone`
discards the private connection and joins its driver/socket cleanup. A `sync.Once`
joins concurrent completion callers and prevents a deferred rollback from
discarding a connection already returned after successful commit. No pgx state
is inspected after pool return, and cancellation is not detached from SQL work.

This requires directly owned pgx connections (`stdlib.OpenDB` or `sql.Open` with
the pgx driver), as used by the composition roots. `stdlib.OpenDBFromPool` is not
supported: its driver close returns a connection to another pool rather than
closing the socket. This is an internal repository contract, not a general driver
or pool abstraction.

Successful synchronous transaction completion has a server acknowledgement.
For failed/cancelled work, joining local socket cleanup does not assert that the
server has processed disconnect at precisely the same instant. PostgreSQL may
release its locks before or after that local boundary. It cannot receive another
statement from the closed client connection. Ambiguous commits remain errors;
the helper does not invent a definite rollback outcome. Cleanup can extend elapsed
return time beyond the caller's admission/work deadline.

All seventeen original explicit production transaction entrypoints use this helper:
five worker mutations, eight HTTP mutation constructors, three read-only snapshots
and exact-row periodic publication. Existing error classifications and transaction
options remain unchanged. This does not replace process draining, restore fences
or the coordination required for backend switching.

Separate regressions also reproduce remote commits after local cleanup when the
cancel-request connection fails, using ordinary PostgreSQL triggers as timing
barriers. The five standalone queue mutations now also use the helper. A separate first
statement acquires a shared transaction advisory lock before any effects, allowing
Django restore and CLI backup/migration to drain remote transactions under the
exclusive file lease. See the [server-drain protocol](../../../docs/architecture/go-migration/transaction-drain.md)
for the tested boundary and remaining mixed-runtime limits.

The required private PostgreSQL adapter runs eight named groups under the race
detector. Three exercise real worker, HTTP middleware/service and snapshot paths;
five cover pool reuse, concurrent completion, cancelled begin, automatic rollback
with physical socket cleanup, and failed SQL. Deterministic barriers wrap the real
pgx driver; independent SQL verifies actual row-lock contention before rollback.
Callers and lifecycle admission remain blocked through both controlled cleanup
stages. The tests subsequently await server lock release and verify durable state.
Synthetic business tables are transaction sentinels, not Django parity evidence.
Separate actual-Beat publication and full Django/Go adapters cover those schemas.

```sh
python TESTS/adapters/go/transaction_lifetime.py
```

Evidence is written to ignored `quality-reports/go-migration/transaction-lifetime/`.
The isolated pre-adoption source run retains the three expected failing lifetime
assertions. CI requires all eight current groups and rejects missing or skipped
cases; success is recorded only after its private PostgreSQL process stops.
