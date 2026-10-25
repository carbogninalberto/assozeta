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

`internal/database.OpenDB` wraps the owned pgx connector beneath the existing
`sqlx.DB`, `sqlx.Conn` and `sqlx.Tx` architecture. Its small driver transaction
wrapper publishes an immutable completion record after the actual driver
Commit/Rollback returns. The repository passes that record through a context
value that preserves the caller's cancellation and deadline. BEGIN, queries and
database/sql's commit cancellation guard remain attached to the caller.

The wrapper closes failed or cancelled transactions inside the driver call,
where database/sql exclusively owns the driver connection. The completion record
contains pgx's captured cleanup channel; the repository joins it before returning
and releasing restore admission. The connector implements `driver.Validator`,
so a concurrently closing `sql.Conn` cannot return a discarded socket to the
pool. Healthy commits and rollbacks preserve normal pool reuse. `ErrTxDone` from
a concurrent terminal caller is not itself a failure: all callers join the same
driver result, and `sync.Once` releases the private connection only once. Failed
BEGIN also publishes cleanup; a BEGIN that never enters the driver has nothing
to join. Original transaction errors take precedence over cleanup errors.

There is one capability check using `Conn.Raw` before BEGIN and no Raw call after
the transaction starts. This avoids a Go 1.25.12 `database/sql` race observed in
the scheduler TERM fixture: `Conn.grabConn` checks `done` before taking its read
lock, so concurrent automatic rollback can clear the driver pointer before Raw
uses it. Driver completion alone cannot make a subsequent Raw safe, because
database/sql releases its connection after the driver method returns. Closing
and invalidating failed connections inside the driver boundary removes the
need for that access. No panic recovery or retained native pointer bypasses the
database/sql ownership rules.

Production composition roots and integration databases that invoke owned
transactions use `database.OpenDB`. The scheduling fixture composes its real-pgx
barriers through `database.WrapConnector`. `stdlib.OpenDBFromPool` is unsupported:
its driver close returns a connection to another pool rather than closing the
socket. This is an internal owned-pgx contract, not a general driver abstraction.

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

Additional `internal/database` unit tests control the database/sql driver
boundary before and after SQL releases its transaction lease. They cover
concurrent terminal callers, cancelled rollback with independent cleanup,
unentered and failed BEGIN, failed COMMIT including ErrBadConn, and successful
pool reuse. They supplement the required PostgreSQL tests; they do not substitute
for real SQL cancellation, physical socket cleanup or process shutdown evidence.

```sh
python TESTS/adapters/go/transaction_lifetime.py
```

Evidence is written to ignored `quality-reports/go-migration/transaction-lifetime/`.
The isolated pre-adoption source run retains the three expected failing lifetime
assertions. CI requires all eight current groups and rejects missing or skipped
cases; success is recorded only after its private PostgreSQL process stops.
