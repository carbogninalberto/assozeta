# Go server query connection observation wiring

The server explicitly enables the separate connection stream with
`--query-connection-observation-file`. It requires the existing summary flag:

```sh
./server --query-observation-file ./summary.jsonl \
  --query-connection-observation-file ./connections.jsonl
```

Both flags default to empty. With neither flag, no diagnostic files or observer
are created. With only `--query-observation-file`, the existing `New` constructor
and v1 summary format remain in use, including the original
`openQueryObservation(path)` API. No environment settings or credentials are
added. Normal deployment configuration still applies.

`openQueryObservationFiles(summaryPath, connectionPath)` validates that connection
mode has a summary path before attempting any file creation. It acquires both
destinations before writing headers. Each file uses `O_WRONLY|O_CREATE|O_EXCL`
and mode `0600`; existing files and symlinks, including dangling symlinks, are
refused. Equal paths and aliases to the first destination also fail exclusive
creation. Files are never truncated or unlinked. If the second acquisition fails,
the first handle is closed and its newly created empty file remains. Constructor
failure closes both owned handles independently and may leave partial headers.
Any such failure rejects the outputs as evidence. Diagnostic errors use fixed
messages without paths, SQL, credentials, or underlying error text.

Paired mode calls
`queryobservation.NewWithConnectionEvents(summary, connections, runID)` with
unbuffered files and one freshly generated run UUID. The returned observer is
the same instance installed as the pgx tracer, passed to `OpenObservedDB`, and
wrapped by `middleware.QueryObservation` before authentication/session/audit
work. The existing middleware generates request IDs and response headers;
incoming observation or correlation headers cannot select them. See the
[observer contract](query_connection_observation.md) for stream semantics,
connection binding, lifecycle requirements, and consumer acceptance rules.

Production shutdown order and deadlines are unchanged. The existing finalizer
runs after database close. A failed startup or drain closes both owned sinks
without calling `Observer.Close`, so no successful footer is emitted while
producers may still be active. After a successful drain, finalization calls
`Observer.Close` first, then attempts `Sync` and `Close` on each sink even if
observer finalization or either sink operation fails. An observer error, sync
error, or close error rejects the entire pair. A complete footer in one or even
both files alone is insufficient: the caller must also require successful
finalization and reconcile both final streams after all producers stop.

Writes and finalization are synchronous. The execution owner must bound stalls
externally and reject timeouts or forced termination; this wiring adds no
background writer, retry, or diagnostic fallback. Partial files are retained
for diagnosis and must never be accepted as complete receipts.

## Offline verification and parent follow-up

Focused server tests cover unchanged disabled/summary-only behavior, paired
headers and final footers sharing one generated run ID, in-memory HTTP middleware
ID binding with spoofed incoming headers, private/exclusive destinations and
aliases, independent cleanup after acquisition/initialization/finalization
failures, and invalid/incomplete/aborted drains. I/O faults are injected only at
the filesystem boundary. Tests use no listeners, database, or fabricated PID
attribution.

From the repository root, select already installed Go 1.25.12 via `PATH`:

```sh
export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOWORK=off GOMAXPROCS=2 GOFLAGS=-p=1
export GOCACHE=/private/tmp/assozeta-query-connection-gocache
GO=go
"$GO" -C BE_GO test -race ./cmd/server
```

These checks do not establish live PostgreSQL execution, pre-release connection
visibility, backend incarnation/lock-chain identity, or actual network response
header binding. The parent must run socket-capable checks and the guarded live
PostgreSQL/HTTP workload, reconcile both streams with independent catalog and
HTTP observations, and verify successful process finalization. Migration
acceptance remains open.
