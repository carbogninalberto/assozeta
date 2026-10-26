# Live PostgreSQL connection-event tests

`BE_GO/internal/queryobservation/connection_events_integration_test.go` adds
`TestConnectionEventsPostgreSQL` under the existing
`queryobservationintegration` build tag. It tests
`NewWithConnectionEvents` with real pgx connections through
`database.OpenObservedDB`, including its owned connector and driver observer.
There are no production changes or changes to the older integration tests.

The test calls the existing `pgFixture` unchanged. A missing
`ASSOZETA_QUERY_OBSERVATION_DSN` **fails**, never skips. The parent must supply
the disposable fixture: database `assozeta_query_observation`, login/owner role
`contracts` with the existing restricted role attributes, and an absolute
`az-query-*` Unix socket directory with mode 0700, including every fallback
host. There is no TCP, alternate-database, or default-DSN fallback. The fixture
validation happens before observed work.

## Cases and evidence

| Subtest | Required evidence |
| --- | --- |
| `blocked_start_rows_close` | A complete start record is already readable when the observer's start callback returns. A separate catalog monitor confirms that this PID is executing the exact parameterized SELECT and waiting for the controller's owned advisory lock. The query worker is still pending. After release, the returned `pg_backend_pid()` agrees with the event PID; an unread second row keeps the query active until explicit rows close. |
| `blocked_cancellation_preserves_error` | The same independently confirmed wait precedes cancellation of only the test-owned query context. The original callback error and returned driver error are the identical error value and satisfy `errors.Is(err, context.Canceled)`. The parent request context remains valid. Start/end attribution retains the same measured PID, but both final streams reject the errored run. |
| `server_error_preserves_original_error_and_rejects_finalization` | A real invalid integer cast returns PostgreSQL SQLSTATE `22P02`. The original callback error is returned unchanged. An independent monitor identifies the real workload PID and backend incarnation before and after the error. Start/end IDs and PID agree, and both final streams reject the errored run. |

The observed connection is acquired inside the generated request context. A
`sql.Conn.Raw` callback checks the actual owned driver and captures only its
measured PID and pgx cleanup channel. No synthetic connection, reflection,
unsafe access, PID injection, or manual invocation of observer query callbacks
is used. A transparent tracer forwards pgx's actual callbacks and contexts. It
takes a connection-stream snapshot immediately after the observer's start
callback returns, before returning control to pgx; this checks synchronous
publication separately from the later catalog sample. Its error receipt checks
identity without formatting or substituting the error.

The successful and canceled cases each own two additional unobserved
`*pgx.Conn` sessions: controller and monitor. A fresh UUID supplies two positive
int32 advisory keys. `pg_try_advisory_lock` must succeed on the controller;
collision or acquisition failure fails the test instead of waiting on a foreign
lock. The workload uses `pg_advisory_xact_lock` in an implicit statement
transaction. The monitor requires all of these in the same catalog query:

- Event PID equals the actual workload driver's measured PID and differs from
  both controller and monitor PIDs; all three sessions are distinct.
- `pg_stat_activity` identifies the current database/application role, exact
  workload SELECT, active state, `Lock`/`advisory` wait, and nonzero
  `backend_start`.
- `pg_locks` contains an ungranted exclusive advisory lock on the exact two-key
  namespace (`objsubid=2`) and database, plus the controller PID's matching
  granted exclusive lock.
- `pg_blocking_pids` names the controller as the workload's blocker.

Polling uses these positive catalog predicates and a finite context, with a
10 ms ticker between samples. Elapsed time alone never proves blocking.
Before blocker release or cancellation, both snapshots and a worker-completion
channel must show no query return, end, or request summary. After release, the
first returned row supplies an independent server PID and the monitor rechecks
the workload's original `backend_start`. The second row stays unread until explicit close;
another snapshot still requires the start without an end or summary.

Final assertions require exactly one begin/start/end/finish lifecycle, the
generated `<run>-1` request ID and `<run>-q1` query ID, identical positive PID
at start/end, gapless connection-stream sequences, exact query and driver
counters, zero active requests/queries/bindings/driver operations, no post-close
events, and final footers in both streams. The clean case requires nil
`Observer.Close` and both complete footers. Error cases require
`ErrIncomplete`, one query error, no invented driver error, and incomplete
request/end/finish/footer diagnostics. Both active connection indexes are checked
before `Close` can clear them, including in the rejected cases. The observer
also records one connection violation when finishing an errored request; the tests expect that existing
rejection behavior explicitly. Error cases are passing negative controls, never
clean acceptance receipts.

## Bounds, cleanup, and privacy

Each stream uses a mutex-protected sink capped at 256 KiB of content. Readers
receive independent copied snapshots while callbacks may write. Receipt channels
are capacity one and never block a callback; exact record counts reject extra
callbacks. There is one query worker per case with a buffered outcome and an
explicit completion channel. The caller joins it before successful finalization.

The lock cases have 30-second root contexts; the server-error case has a
20-second context. Fixture validation retains its existing bounds. Cleanup is
registered before waiting on barriers. On every failure path it cancels the
owned query, releases the controller lock, joins the worker with a five-second
deadline, closes any returned rows, closes the leased connection and database,
and joins pgx's physical-connection cleanup. If unlock fails, only the owned
controller connection is closed as a fallback. Independent sessions are closed
and their cleanup channels joined with finite contexts. No other backend is
canceled or terminated. Cleanup timeouts fail the test and cannot yield accepted
diagnostics. The parent test-process timeout bounds the entire execution.

The normal path also polls until the controller/workload have no remaining
advisory locks for their key. Queries inspect catalogs or acquire/release only
owned advisory locks. They create no tables, modify no business data, and use
no business schema.

Failures use fixed sanitized messages. Assertions never print the DSN, socket
path, SQL, parameters, raw errors, or whole diagnostic records. Both streams are
checked for fixture secrets, SQL markers, the private test parameter/context
value, and observed raw error text. Do not enable shell tracing or put private
DSNs in command arguments/logs. Tests keep diagnostics in memory and publish no
files or receipts.

## Parent execution

With the parent-owned fixture already running and its validated DSN inherited
through the environment, run from the repository root:

```sh
export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOWORK=off GOMAXPROCS=2 GOFLAGS=-p=1
export GOCACHE=/private/tmp/assozeta-query-connection-live-tests-gocache
GO=/Users/alberto/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin/go
"$GO" -C BE_GO test -race -tags=queryobservationintegration \
  ./internal/queryobservation -run '^TestConnectionEventsPostgreSQL$' -count=1 -timeout=3m
```

All three subtests must run and pass; a timeout or partial run is failed
evidence. This command deliberately selects the new suite. Existing
`TestQueryObservationPostgreSQL` and `TestObservedDriverPostgreSQLRequests`
remain separate parent checks.

Offline compilation, with no fixture use:

```sh
env -u ASSOZETA_QUERY_OBSERVATION_DSN GOPROXY=off GOSUMDB=off \
  GOTOOLCHAIN=local GOWORK=off GOMAXPROCS=2 GOFLAGS=-p=1 \
  GOCACHE=/private/tmp/assozeta-query-connection-live-tests-gocache \
  /Users/alberto/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin/go \
  -C BE_GO test -tags=queryobservationintegration ./internal/queryobservation -run '^$'
```

The worker compiled the final tagged package with cached Go 1.25.12 successfully,
including `-race` (`ok`, 1.641s, `[no tests to run]`). It did not execute this live suite, open database
connections, start services, install dependencies, or publish Git changes.
Existing offline controls `TestConnectionEventsVisibleBeforeEndAndFinish` and
`TestConnectionStalledWriterIsSynchronous` passed under `-race -count=1`.
A temporary Go overlay deliberately suppressed `query_start` publication;
`TestConnectionEventsVisibleBeforeEndAndFinish` then failed at its intended
"start not independently visible" assertion. The overlay used a temporary copy,
left repository production/test sources unchanged, and was removed afterward.
These existing controls use synthetic offline connections and are not
PostgreSQL evidence. The new live cases have no runtime red/green result yet.

## Acceptance limits

These tests establish the specified live PostgreSQL/driver attribution only
after the parent executes them. Compilation is not live evidence. The
advisory-lock workload is deliberately independent of business schema and is
not the actual DELETE workload.

Generated callback/request IDs do not prove HTTP request binding. The parent's
actual HTTP/catalog/final verifier remains separate: exercise the real server
and concurrent business operation, establish its expected lock/transaction
chains and backend incarnations, match actual generated HTTP response headers
to captured events (including spoofed incoming-header controls), reconcile all
requests and driver counters, drain producers, and accept only successfully
finalized complete streams. These tests do not exercise middleware response
headers, HTTP listeners, business-row locks, server sink files/Sync/Close,
final-verifier parsing, or operations outside pgx QueryTracer coverage.
