# Message LIST memory slice

This LIST-only change preserves the complete JSON array while removing the Go
row, domain-record and DTO population slices. There is no SQL pagination,
additional query, altered filter, tie-breaker or DETAIL change. Starting revision:
`7d55489385efd764d64817e1cdc9e26d82ec9f90`.

## Boundaries and response behavior

- `domain.MessageReadStore.VisitMessages(ctx, trustedOwnerID, consumer) error`
  synchronously supplies one record at a time. The repository keeps the existing
  association lookup and exact LIST SQL: two queries, association/type filtering,
  and `ORDER BY m.created_at DESC`. PostgreSQL retains descending NULL-first
  behavior and its existing unspecified equal-date order.
- The repository uses `QueryxContext` and `StructScan` on one row. Scan, iteration,
  consumer, cancellation and cursor-close failures propagate; every opened cursor
  is closed, including early returns. LIST owns a private derived query context:
  early errors cancel it **before** closing rows, so pgx cannot indefinitely drain
  unread results after a failed spool write/consumer. The caller's context remains
  live; normal exhaustion closes rows before the private context is released.
  Errors retain `errors.Is` identity. No extra session/query statements are added.
- `service.MessageReads.WriteList(ctx, trustedIdentity, io.Writer) error` admits
  `ScopeUser.ID` before reading or writing. It emits `[`, comma-separated records,
  then `]\n`, including `[]\n` for an empty result. Each record uses the unchanged
  DTO conversion and standard JSON escaping. Null subject remains `"None"`,
  nullable dates remain null, and microsecond UTC formatting remains unchanged.
  Context, writer errors and short writes stop serialization. Partial bytes in
  this internal writer are allowed; the handler owns response staging.
- After unchanged negotiation, authentication, admission, OPTIONS and method
  handling, the handler creates a private temporary file. `os.CreateTemp` creates
  it exclusively with mode 0600; the handler immediately unlinks it and explicitly
  applies 0600 to the open descriptor. The descriptor closes on every subsequent
  exit. Creation/unlink/chmod failures close and clean up before returning an
  existing generic error response. This relies on POSIX open-file unlink support.
  `handler.WithMessageListSpoolDirectory(string)` selects the existing directory;
  the original three-argument constructor retains the native default temp path.
  Explicit-directory creation errors never fall back to another directory.
- Only successful serialization, rewind, complete read validation, second rewind
  and a final context check permit HTTP 200. Validation and response copying use
  fixed-size memory. The read check adds a complete disk read before sending.
  No response bodies, records or credentials are logged.

A new disk read error, cancellation or client-write error **during final response
transmission** cannot undo a committed HTTP status. Those errors are returned and
cleanup still runs. A close failure after transmission is likewise returned.
Spooling preserves pre-response handling for database/serialization and detected
spool preparation failures; it cannot make a transport atomic after sending starts.

## Memory and middleware limits

Application memory is proportional to the largest individual record, its escaped
DTO/JSON representation, and fixed copy buffers, rather than the number of rows.
This does not bound a single arbitrarily large message. Temporary disk usage,
total allocated bytes, database work, output bytes and elapsed time still grow
with the complete result. Serialization now also keeps the cursor/connection open
while writing the spool. Concurrent requests multiply those resources.

Static inspection of `BE_GO/cmd/server/main.go` found no response-body buffering
middleware on `/communications/messages/list`. Its pre-middleware is
`QueryObservation`, `InstanceBootstrapLocale`, `DjangoCORS`,
`DjangoRestoreAdmission`, and `DjangoSecurityHeaders`; the route adds
`DjangoSessionAudit`. Their response wrappers forward writes. The
`ManualResponseEncoding` middleware's complete-body `bytes.Buffer` is attached to
other routes, not message LIST. Production composition now passes the configured
LIST directory; no shared middleware changed. This inspection does not establish
proxy/process/production memory bounds.

## Offline TDD and evidence

Ignored `quality-reports/` artifacts preserve:

| Artifact | Result |
| --- | --- |
| `message-list-cache-blocked.log` | Initial setup failure: sandbox denied default Go cache writes. |
| `message-list-red.log` | RED: unchanged production lacked the new visitor/writer interfaces. |
| `message-list-first-implementation.log` | First implementation: new handler setup used an invalid owner ID; corrected to the existing valid synthetic UUID. |
| `message-list-cancellation-red.log` | Behavioral RED: cancellation during the final JSON write incorrectly returned nil. |
| `message-list-green.log` | GREEN: focused repository, service, handler and DTO assertions pass, including the final-write cancellation fix. |
| `message-list-compile.log` | Tagged live-test source and server composition compile; zero live tests executed. |
| `message-list-benchmark.log` | Historical benchmark; shared-payload fixture, superseded for regression evidence. |
| `message-list-followup-red.log` | Missing config/constructor APIs and callback-close-before-cancel behavioral RED. |
| `message-list-followup-green.log` | Repaired focused assertions and independent-row heap/control measurements. |
| `message-list-followup-race.log` | Focused config/repository/handler race checks. |
| `message-list-followup-compile.log` | Tagged repository/server compile only; no live execution. |
| `message-list-followup-composition.log` | Offline deployment guards and deliberate bad-composition controls. |
| `message-list-followup-benchmark.log` | Repaired independent-row benchmark; direct handler, not production performance. |

Existing assertions are retained through test-only callback collection and JSON
decoding helpers. Additional tests cover exact two-query SQL/bindings, late scan
and row errors, consumer errors, cursor closure, connection release/reuse,
cancellation, short writes, nil/empty output, independent escaping/null/timestamp
expectations, and temporary-file closure/removal on success and failures. Handler
faults cover creation, late write, short write, both rewind positions, late read,
late store failure and cancellation; all preparation failures return the existing
500 without partial array bytes. A client-write failure verifies descriptor cleanup
and propagated transport error.

The repaired live-heap test allocates an independent fixed-size content string
lazily for **each** row. It checks complete response byte counts and array
boundaries, and discards bytes without a recorder. Both populations use the same
4 MiB growth guard. A test-only negative control retains every domain record
before consumption, then serializes through the same production service/handler;
the guard must reject that O(N) retention. Production code is unchanged by the
control. Forced-GC samples include generation and response copying.

Followup focused run on Darwin/arm64, GOMAXPROCS=2:

| Path | Rows | Response bytes | Peak sampled live-heap increase over baseline |
| --- | ---: | ---: | ---: |
| Streaming | 512 | 450,562 | 0 bytes |
| Streaming | 32,768 | 28,835,842 | 40,672 bytes |
| Buffering control | 512 | 450,562 | 178,600 bytes |
| Buffering control | 32,768 | 28,835,842 | 13,363,792 bytes |

The negative control's 13,185,192-byte growth fails the same 4 MiB limit that the
streaming path passes. The earlier 0 / 35,416-byte samples in historical artifacts
used a shared string across all rows; they were insufficient regression evidence
and are superseded by this repaired test and its control. Zero means no sampled
post-GC heap exceeded baseline; runtime/test noise can lower it. These are direct
handler synthetic-store retained-heap samples, not continuous high-water,
real-driver, RSS, container/page-cache, proxy, throughput or production measurements.
Total allocated bytes still scale with serialization and DTO escaping. Historical
benchmark output remains separate from the repaired fixture's followup output.
The repaired one-iteration benchmark measured 8,323,872 B/op at 512 rows and
528,443,000 B/op at 32,768 rows. These are total Go allocations for the synthetic
direct handler, distinct from retained live heap; no production timing claim.

Reproduce from `BE_GO` without services/network, using the cached toolchain:

```sh
export GOMAXPROCS=2 GOFLAGS=-p=1 GOPROXY=off GOSUMDB=off
export GOTOOLCHAIN=local GOWORK=off
export GOCACHE="$PWD/../quality-reports/go-build-cache"
GO_BIN=/Users/alberto/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin/go
"$GO_BIN" test ./internal/config ./internal/repository ./internal/service ./internal/handler ./internal/dto \
  -run 'Test(MessageReads|MessageListSpool)' -count=1 -v
"$GO_BIN" test -race ./internal/config ./internal/repository ./internal/handler \
  -run 'TestMessage(ListSpool|ReadsConsumerFailure|ReadsVisitFailures|ReadsConfigured|ReadsPrivateSpool)' -count=1
"$GO_BIN" test -tags=messageintegration ./internal/repository ./cmd/server -run '^$' -count=1
"$GO_BIN" test ./internal/handler -run '^$' \
  -bench '^BenchmarkMessageReadsListSpool$' -benchtime=1x -count=1
# From the repository root:
python3 -B -m unittest TESTS.test_message_list_spool_config -v
```

## Disk-backed deployment and capacity

`Config.MessageListSpoolDirectory` reads `GO_MESSAGE_LIST_SPOOL_DIR` through the
existing loader: process environment overrides the optional env file (including
an explicitly empty value); `$NAME` indirection expands through the merged
values. Missing optional proxies and an empty value preserve the native default.
Cycles fail with a generic config error. The field is excluded from JSON and from
`String`/`GoString`; private configured paths are not published in config output.

Production Compose sets the path to `/var/cache/assozeta-message-list` and mounts
a dedicated anonymous **disk** volume. The image owns that directory as
UID/GID 10001 with mode 0700, copied into a fresh volume at initialization. Each
request's file is 0600, immediately unlinked, and closed on completion/error.
This is disposable staging, not authoritative message storage or a logo volume.
The root stays read-only, runtime stays nonroot, `/tmp` remains the existing
64 MiB RAM tmpfs, and global `TMPDIR` is unchanged. Native callers can still use
the platform temp directory; set the explicit option/env when that path is a tmpfs
or has insufficient capacity. The handler does not create the configured directory.

There is no new per-request disk quota, admission/concurrency limit, or maximum
individual-row size. Each active request can consume its entire encoded response
size until copying completes and the descriptor closes. Concurrent requests add
those sizes and hold database cursors while preparing spools. Operators must
provision and monitor Docker host/volume capacity and I/O for the aggregate load.
Disk exhaustion or a failed write before HTTP 200 returns the existing generic
error and releases the spool; this does not promise that every arbitrarily large
valid response will fit. A huge individual message can still require a huge
row/escaped DTO/JSON allocation. Disk page cache may count against container/host
memory; Go heap measurements do not measure that. Validation adds a full disk read
before the final response read.

## Parent integration gates

No live services, apps, private evidence, network access, commits or pushes were
used for these repairs. The parent reported `message-list-stream-repository-r1`
**PASSED at the prior exact candidate**, covering hydration, executed plans/query
budgets, blocked cancellation, state and cleanup. That historical receipt is not
relabelled for this changed source. Fresh integration remains parent-owned.

From the integrated checkout, the parent must rerun the provisioned runtime gates
with fresh owned output directories:

```sh
"$MESSAGE_ACCEPTANCE_PYTHON" TESTS/adapters/go/message_reads_repository.py \
  --output "$MESSAGE_REPOSITORY_OUTPUT"
"$MESSAGE_ACCEPTANCE_PYTHON" TESTS/adapters/django/message_reads_runtime.py \
  --output "$MESSAGE_ACCEPTANCE_NATIVE_OUTPUT"
"$MESSAGE_ACCEPTANCE_PYTHON" TESTS/adapters/django/message_reads_runtime.py \
  --versioned --output "$MESSAGE_ACCEPTANCE_VERSIONED_OUTPUT"
```

Retain the existing race-enabled `TestMessageRepositoryHydrationLive`,
`TestMessageRepositoryExecutedPlansLive`, `TestMessageRepositoryBlockedCancellationLive`
and their unchanged assertions/receipts. The HTTP runners retain independent
fixture bodies, ignored query parameters, ordering, admission, raw query accounting,
growth, concurrency, artifact binding and cleanup expectations.

The new fourth test is `TestMessageRepositoryConsumerFailurePendingRowsLive`
(`-tags=messageintegration`). It uses the same mandatory helper-owned DSN and
receipt-directory env vars. Its test-only `Queryer` wraps the exact LIST SQL with
a 128 KiB first-row projection and an advisory lock on later rows; it does not
alter fixtures or schema. The callback is entered before an observer proves that
PostgreSQL is waiting on the blocker while producing later rows. Then the first
callback fails. Required evidence: return within 2 seconds with the blocker still
held, the consumer sentinel preserved and exactly one call, caller context still
live, no pool lease, backend idle/gone, and successful unwrapped LIST reuse with
the same four independently expected IDs. Reconnection after cancellation is
allowed. This is a driver/cursor-lifetime control, not a plan acceptance query.

New receipt: `consumer_failure.json`, with the following closed schema for the
parent's adapter (worker did not edit that adapter):

| Key | Required value |
| --- | --- |
| `list_sql_wrapped_for_pending_control` | `true` |
| `first_row_bytes` | `131072` |
| `consumer_calls` | `1` |
| `later_row_lock_wait_observed` | `true` |
| `blocker_held_at_return` | `true` |
| `consumer_error_preserved` | `true` |
| `caller_context_live_at_return` | `true` |
| `return_ms` | measured integer, `0 <= value < 2000` |
| `backend_idle_or_gone` | `true` |
| `pool_in_use_at_return` | `0` |
| `pool_in_use_after_followup` | `0` |
| `followup_rows` | `4` |

The parent owns adding the fourth test/receipt validation to any maintained
Python gate that currently accepts only three tests/receipts. Local compilation
of this test does not establish its runtime result.

The parent must build and exercise the packaged deployment: verify disk-volume
mount type, fresh-volume UID/GID10001/mode0700, read-only root/nonroot execution,
POSIX unlink/descriptor cleanup, a **complete successful response larger than
64 MiB**, and concurrent disk capacity/failure cleanup with `/tmp` still size64m.
Use fresh source-bound receipts. Also collect large-population evidence with the
real driver/full middleware/proxy chain using lazy inputs and a discarding sink.
Do not retain a complete response as server heap evidence. No full acceptance or
production performance claim is made here.


## Parent integration checkpoint

The parent independently reviewed the repair and executed the four real
PostgreSQL repository cases on the isolated candidate. The initial control failed
before its first callback; 16 KiB second-row padding then exposed an actual
`ClientWrite` wait instead of the required advisory lock. The final 8 KiB padding
control passes without changing production SQL or any acceptance assertion:
first callback, observed later-row lock, consumer sentinel, still-live caller,
held blocker, idle/gone backend, zero leased connections and four-row production
followup. Prior failed receipts remain separate. The control is protocol-lifecycle
evidence, not a production SQL plan measurement.

`message-list-stream-repository-r6/results.json` passes all four cases with source
stability and complete owned cleanup; SHA-256
`ff25623df648fbe17ef7873e2ff425cec40eb8618cbf906311f5207719ce5037`.
The original hydration/plan/cancellation cases remain intact. After integration,
full `go vet ./...`, `go test -race ./...` and 24 repository/composition offline
controls pass. These results do not establish native/versioned HTTP, packaged
large-response or full message-family acceptance; those gates remain separate.


The subsequent integrated native/versioned HTTP gate also passes: 30 authored
cases each on Django native, Go native, Caddy v1 and Caddy v2, plus its growth,
concurrency, query-attribution, state/cache and misroute controls. The parent
re-executed the finalizer and matched all 1194 source inputs to the integrated
worktree. Fresh native artifacts and complete service cleanup are bound in
`message-list-integrated-http-r1/results.json`, SHA-256
`d606654ff9af74d93e3cfd586cd76927f2becfca916f7668759c105014b7f058`.
This advances the HTTP boundary only; packaged large-response/concurrent disk,
request cancellation/storage-failure and complete family acceptance remain open.
