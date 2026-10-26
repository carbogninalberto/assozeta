# Private Go query connection attribution

`queryobservation.NewWithConnectionEvents(summaryWriter, connectionWriter, runID)`
adds opt-in attribution of generated request IDs to the real `*pgx.Conn` received
by `TraceQueryStart`, using `conn.PgConn().PID()`. It writes a separate bounded
JSONL connection stream synchronously before that callback returns. A consumer
can read it while a later PostgreSQL query is blocked and the HTTP response is
still outstanding. The query-end callback must match the original context token,
request token, `*pgx.Conn`, underlying `*pgconn.PgConn`, and positive measured PID.
There is no public constructor accepting a PID as evidence.

`New(writer, runID)` retains the existing v1 output, counters, error behavior,
and acceptance of nil connections in synthetic tests. The opt-in constructor
keeps the v1 summary schema and adds a separate stream. This bounded change does
not wire configuration, filesystem handling, server composition, or verifier
consumers. Those are parent integration work, with the diagnostic sink explicitly
disabled by default.

## API and ownership

```go
observer, err := queryobservation.NewWithConnectionEvents(summary, connections, runID)
// Existing methods/interfaces are unchanged:
ctx, generatedRequestID, finish := observer.Begin(parentContext)
// pgx calls observer.TraceQueryStart/End using its actual connection.
finish() // exactly once, after all request-owned work
err = observer.Close() // only after all HTTP and database work has drained
```

The parent supplies a fresh generated nonzero UUID per run, two distinct private
writers with immediate write visibility (normally unbuffered `*os.File`), and
valid request contexts. A buffered writer must flush each record before `Write`
returns; otherwise pre-release visibility is not established. Run IDs are canonicalized by the existing
UUID parser. Request IDs are `<canonical-run-uuid>-<request-number>`; query IDs
are `<canonical-run-uuid>-q<query-number>`. Numbers start at 1 and increase per
observer. Context values, correlation labels, client headers, URLs, bodies, SQL,
arguments, command tags, and raw query/driver errors never select an ID or enter
the stream. Middleware already sets `X-Query-Observation-ID` from the generated
`Begin` result, ignoring incoming IDs. This is attribution inside trusted server
code, not a security boundary against malicious in-process Go code fabricating
connections or contexts.

Writers are synchronous under the observer mutex and must not call back into the
observer. Each stream has its own 64 MiB cap, including headers and footers.
Records are marshalled before the cap check; a limit failure writes none of that
record. Short writes, write errors (including a full write accompanied by an
error), and cap failures are sticky and never retried. Other callbacks continue
normal lifecycle accounting and retain their context cancellation/error outcome.
A writer that never returns blocks callbacks and `Close`; there is no internal
async queue, timeout, retry, cancellation bypass, or dropped-event fallback.
The parent execution owner must impose a bounded deadline and reject a stall or
forced termination as failed evidence. Diagnostic I/O can affect timing and
request deadlines while enabled.

Neither writer is synced or closed by the observer, even on initialization
failure. Either constructor can leave partial output on failure, including a
summary header before a connection header fails. The parent owns cleanup,
`Sync`, `Close`, and their errors. Distinct writer objects can still alias an
underlying file; the coordinator must ensure separate destinations.

## Connection JSONL version 1

Every record has `stream: "query_connection"`, `version: 1`, `type`, canonical
`run_id`, and a 1-based strictly increasing `sequence` covering every connection
record, including header/footer/invalidation. This sequence differs from the
request ordinal in the existing v1 summaries. Omitted identity fields have no
attribution. All counters are cumulative observer totals, including on request
and query events; request/query IDs identify the event, not the scope of those
counters. The internal schema is `connectionRecord` in `connection_events.go`.

| Type | Identity and meaning |
| --- | --- |
| `header` | First record; no request/query/PID; `complete: false`. |
| `request_begin` | New generated request ID, emitted during `Begin`, even if no query follows. |
| `query_start` | Generated query ID, original request ID when locally owned, and positive `backend_pid` only for a structurally valid connection binding. Written before the callback returns. |
| `query_end` | Original query/request IDs when locally owned. Positive `backend_pid` only when the callback matches the original active binding. `error` reports presence of its query error. |
| `request_finish` | Original request ID; every finish call before close is represented, including duplicates. |
| `footer` | Final snapshot after drain. No request/query/PID; `complete` reflects both streams and all lifecycle/driver/connection accounting known before writing this footer. |
| `invalidated` | Once on the first callback after `Close`, if the sink still works; `complete: false`. Invalidates an earlier footer. |

`request_id`, `query_id`, and `backend_pid` are omitted when unavailable. All
other fields below are present, including zeros. No timestamps or connection
addresses are serialized.

| Fields | Type and meaning |
| --- | --- |
| `error` | Boolean; true only on a query-end callback with a nonnil error; no error formatting. |
| `complete` | Boolean; for begin/start/end/finish, the event is valid and no prior error, violation, or sink loss is known. It does **not** assert the run has drained. Header/invalidation are false; only a final complete footer is a candidate for run acceptance. |
| `request_starts`, `request_finishes`, `finish_calls` | Numeric request lifecycle totals. |
| `active_requests`, `active_queries`, `active_driver_operations` | Numeric open lifecycle counts. |
| `active_bindings` | Number of live accepted connection bindings. A wrong end cannot release the original binding. |
| `connection_violations` | Sticky numeric count of invalid connection starts/ends, invalid request finishes, and nil `Begin` contexts. |
| `post_close_events` | Numeric count of callbacks after finalization at this snapshot. |
| `query_starts`, `query_ends`, `query_errors`, `unbound_starts`, `unbound_ends` | Existing numeric query callback/error totals. |
| `violations`, `late_starts`, `late_ends`, `duplicate_ends`, `duplicate_finishes`, `reused_contexts`, `missing_contexts`, `foreign_contexts`, `context_mismatches` | Existing numeric lifecycle failure totals. |
| `driver_starts`, `driver_ends`, `driver_errors`, `driver_unbound_starts`, `driver_unbound_ends` | Existing numeric driver callback/error totals. |
| `connect_starts`, `connect_ends`, `connect_errors`, `reset_starts`, `reset_ends`, `reset_errors`, `ping_starts`, `ping_ends`, `ping_errors` | Existing numeric per-operation totals. |
| `reset_probes`, `driver_violations`, `unknown_driver_kinds`, `invalid_reset_probes` | Existing numeric driver validation totals. |

A query error can have a positive PID and `complete: false`: the connection
matched, but the run cannot be accepted as clean. Likewise, a later valid event
can contain a PID after a prior unrelated failure; completeness remains false.
An invalid start/end has no PID. An unknown/foreign end never copies another
observer's IDs. Reusing an active or completed query context is invalid. Two
active queries on the same connection or PID are invalid. Sequential queries
may reuse the same connection, and new connections may reuse a completed PID;
backend incarnation is the independent consumer's responsibility.

The observer retains only active connection/PID indexes plus context-owned
request/query/driver tokens. Valid ends remove index entries and release handle
references from the query token; no completed-ID or completed-connection map
exists. Index capacity can reflect peak active work. A mismatched end leaves its
binding active; a subsequent correct end may drain it but cannot erase the
violation. `Close` releases index entries even on incomplete finalization.

Binding cleanup is independent of request lifecycle validity: an end matching
the original context, request and connection identities releases both indexes
and the token's connection handles even if the request already finished. Such a
late end remains invalid, has no PID attribution, increments the sticky violation
count and cannot repair the premature request summary or final acceptance. A
mismatched late end retains the original binding until an exact match arrives or
`Close` clears the indexes. Cleanup also continues after sink failure or output
cap exhaustion; losing diagnostics never makes an incomplete stream acceptable.

## Parent wiring and acceptance instructions

1. Add the explicit disabled-by-default connection sink in parent-owned startup
   code. Create separate exclusive private files (0600, no reuse/symlink overwrite)
   and pass a fresh parent-generated run UUID to the new constructor. Retain
   `New` for the existing summary-only mode. Treat either initialization failure
   as fatal diagnostics failure; partial files are not evidence.
2. Install this **same observer instance** as `pgx.ConnConfig.Tracer`, as the
   driver observer passed to `database.OpenObservedDB`, and in
   `middleware.QueryObservation` before authentication/session/audit work. Keep
   driver contexts and the tracer-returned context intact. Include database work
   from error handlers inside the observed request lifetime. Preserve existing
   transaction ownership, reset, ping, and cancellation behavior.
3. Continuously consume complete JSONL lines from the connection file while the
   blocker is held. Check fixed stream/version/allowed fields and gapless event
   sequences. Do not wait for request summaries or HTTP responses to obtain the
   start attribution. Partial lines and files without finalization are not
   acceptable completed evidence.
4. During the same-owner/same-message DELETE scenario, require two distinct
   generated request IDs and two outstanding query tokens. Match each positive
   start PID independently to `pg_stat_activity.backend_start` and the
   permanent/virtual transaction identity observed in `pg_stat_activity` and
   `pg_locks`. Establish each expected lock chain while the blocker remains
   held. Track connection replacement/backend reincarnation across samples.
   Owner-row lock presence alone cannot identify DELETE versus other mutations.
   Keep exact SQL/transaction/business-operation predicates in the parent's
   independent verifier; the private stream intentionally has no SQL label.
5. Collect the eventual real `X-Query-Observation-ID` response headers for each
   actual HTTP request and match them to the captured generated IDs. A client
   CID, scheduling order, supplied header, process-local label, or simultaneous
   outstanding request is insufficient. Include spoofed incoming observation-ID
   and correlation headers as controls; they must not choose generated IDs.
6. Match each start to exactly one valid end with identical generated query ID,
   request ID, and PID, inside exactly one begin/finish request lifetime. Detect
   unknown extra requests using begin/finish events, including query-free
   requests. Reconcile all records, summary request IDs, expected workload counts,
   driver counters, and independently observed catalog/HTTP outcomes. A
   `complete: true` intermediate record is provisional evidence only.
7. Drain HTTP, rows, driver callbacks, and database work before `Observer.Close`.
   Require nil `Close` result, a final complete footer in **both** streams, zero
   active counts/errors/violations, matching starts/ends and request/finish
   totals, no gaps/duplicates/unknown events, and successful `Sync`/`Close` for
   both files. Reject early shutdown, errors, timeouts, truncation, missing
   callbacks, late/duplicate events, cap/short-write failure, missing footer, or
   any record after a footer (including `invalidated`). Re-read the final record
   after all producers stop. Footer-write failure cannot rewrite an already
   written footer in the other stream; the joined error and both final streams
   must be checked together. A previously returned successful `Close` cannot
   predict future misuse; post-close producers must be excluded by drain.

## Verification and limits

Synthetic unit tests use deliberately fabricated inert connection handles via
**test-only** reflection/unsafe access to pinned pgx internals. They never open
connections or execute SQL and provide no live evidence. They cover visible
start-before-end/request-finish output, generated IDs, no sensitive data,
cancellation/deadlines/errors, nil/zero/replaced/reused connections, recycled PIDs,
missing/foreign/mismatched contexts, duplicate/late/missing callbacks, corrected
mismatches staying invalid, query-free requests, concurrent callbacks/close,
short writes, sticky errors, per-stream caps, synchronous stalls, parent writer
ownership, and post-close invalidation.
Late-end regressions assert both indexes and the context token release handles
before `Close`, including 64 repeated lifecycles per combination of query success,
query error, cancellation or deadline and healthy, failed or capped sinks.
Mismatched late ends cannot release the original binding; an exact late end
drains it while preserving violations and rejection. Normal successful flow
still drains and accepts. Cancellation/deadline causes and sticky sink error
identities remain unchanged.

Offline worker commands use the cached toolchain and an authorized writable cache:

```sh
export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOWORK=off GOMAXPROCS=2 GOFLAGS=-p=1
export GOCACHE=/private/tmp/assozeta-query-connection-gocache
GO=/Users/alberto/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin/go
"$GO" -C BE_GO test -race ./internal/queryobservation ./internal/database
"$GO" -C BE_GO test -race ./internal/middleware ./cmd/server -run '^TestQueryObservation'
"$GO" -C BE_GO test -tags=queryobservationintegration ./internal/queryobservation -run '^$'
```

The last command compiles tagged tests without executing them. The parent must
run the existing tagged live suites with the guarded parent-owned PostgreSQL
fixture (`ASSOZETA_QUERY_OBSERVATION_DSN` contract in `pgFixture`) and independently
exercise the new constructor against actual connections: start visible before
blocker release, no end until query/rows drain, same measured connection/PID at
end, server error and cancellation preserved, and real HTTP response-header
binding under the actual DELETE concurrency workload. Full middleware testing
also requires the parent's permitted listener environment. This worker does not
start services, run live PG/HTTP, or perform build/artifact integration.

Callbacks measure attribution attempts/lifetimes. They do not prove that SQL
reached PostgreSQL, identify the backend incarnation, prove transaction overlap,
or establish full migration acceptance. Batches, CopyFrom, direct PgConn work,
and other operations outside QueryTracer remain outside this evidence.
