# Packaged LIST cancellation while its real SELECT is blocked

This is a parent-invoked proof for one cancellation case. It does not establish
complete message LIST acceptance or change the phase1 report/gate. The worker
implemented and tested offline controls only. Actual PostgreSQL and packaged
HTTP execution were unavailable to this worker and **could not be run**.

## Parent invocation

Inside the existing `SpoolFixture.spool_server` context, after other workloads
have finished and with no concurrent requests:

```python
from TESTS.adapters.go.message_list_query_cancellation import (
    run_query_cancellation, validate_query_cancellation,
)

evidence = run_query_cancellation(proof, base, token, association)
if not validate_query_cancellation(evidence):
    raise RuntimeError('query-cancellation.not-proven')
```

The parent supplies the initialized private Django environment, committed 1152
phase1 rows, owner token, association, running packaged process, and fixture.
Imports are inert and stdlib-only; phase1 and Django are imported lazily. The
helper neither provisions resources nor changes Django settings, production
SQL, tables, functions, schemas, fixture data, or phase1 evidence.

`run_query_cancellation(...)` returns a closed safe dictionary on execution or
cleanup failure too. The validator returns `True` only for complete successful
evidence. The parent must validate it explicitly; a partial record is diagnostic
only. Neither interface authenticates evidence supplied by an unrelated caller.

## Observation and ordering

1. Record owned running process identity, process start time, and the spool
   directory's inode/device and ownership `10001:10001:700`. Require no live
   spool and no named directory entries before starting.
2. Derive two independently owned psycopg connections from the initialized
   Django backend's driver and a copy of its explicit endpoint/credentials.
   Require PostgreSQL, user `contracts`, database
   `assozeta_contract_category_image`, host `127.0.0.1`, and a port matching both
   `proof.postgres_port` and its owned fixture's published PostgreSQL port.
   Check actual database/user/PID on each connection. Explicit `hostaddr`, port,
   database, options and SSL mode prevent inherited libpq endpoint redirection;
   arbitrary Django connection options and pools are not copied. The global
   Django connection, settings and timeout values are never modified.
3. Acquire `LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE` in the
   owned blocker transaction. Start an actual socket GET in a client thread.
   Accept only the exact fixture-published loopback HTTP endpoint.
4. Read `messageReadsListSQL` verbatim from
   `BE_GO/internal/repository/message_reads.go`. This is SQL attribution, not the
   expected response contract. Observe exactly one active client backend for
   that exact query, in a real `Lock` wait with `pg_blocking_pids` containing only
   the actual owned blocker PID. Exclude observer/blocker backends and reject
   a competing or wrong-query waiter. The baseline must have no candidate.
5. Observe exactly one live unlinked regular spool descriptor, size 0–4096
   bytes, permissions `0600`, UID/GID `10001`, link count zero, on the actual
   disk spool mount with matching device and mount ID. Reject named files,
   extra descriptors, malformed metadata and oversized observation output.
   Recheck the same exact waiter and granted blocker lock after this sample.
6. Require no response byte or socket readability. Shut down the actual client
   socket in both directions and join its request thread. Require EOF caused
   by this shutdown; headers, unexpected EOF, thread exceptions and timeouts
   fail the proof. Close the socket in `finally`.
7. While the blocker still holds its granted lock in its transaction, require
   that same backend identity to be idle without a transaction or Lock wait,
   or gone, and the spool descriptor to be absent. Recheck the granted blocker
   lock after observing absence. A valid closing small spool can be retried;
   metadata/observation errors fail immediately. Cancellation that requires
   unblocking cannot pass.
8. Roll back the blocker only after all release observations. Use the existing
   phase1 `held_list`/`verify_list` exclusively for a complete recovery LIST,
   validating all 1152 authored rows, order and contents. Verify empty spooling,
   the same process, and preservation of the same owned `0700` directory.
9. Close/join the client, roll back the blocker even on exceptions, and close
   both owned DB connections. Every acquired cursor uses a context manager.
   Cleanup errors invalidate success. There is no backend termination or global
   timeout restoration step because no global timeout was changed.

Connection establishment and client join use 3-second limits. Owned DB sessions
have 2-second statement and lock timeouts. Query-wait observation has an 8-second
deadline; disconnect/backend/descriptor release has a 5-second deadline checked
again after the final observation. Polling uses 50 ms. The parent's existing
bounded `proof.exec`/ownership commands retain their per-command maximum of 90
seconds; a slow command cannot turn a missed release deadline into success.
Recovery inherits phase1's bounded HTTP/stream reads. The parent remains
responsible for its overall execution/cleanup deadline and final service cleanup.

## Closed evidence schema (format 1)

No SQL, response bodies, tokens, PIDs, raw exceptions, private paths, connection
parameters or process metadata are exported. Keys outside this schema fail
validation.

| Fields | Type and successful value |
| --- | --- |
| `format`, `scope` | integer `1`, enum `packaged-message-list-query-cancellation` |
| `success`, `failure` | boolean `true`, enum `none` |
| `private_database`, `exact_lock_wait`, `blocker_retained`, `before_headers`, `thread_exited` | boolean `true` |
| `backend_released`, `spool_released_under_lock`, `same_process`, `owned_spool_preserved`, `recovery_complete`, `cleanup_complete` | boolean `true` |
| `matching_backends`, `blocked_spools`, `released_spools`, `recovery_rows`, `owned_connections_closed` | integers `1`, `1`, `0`, `1152`, `2` |
| `cleanup_errors`, `cancel_ms`, `blocked_spool_bytes` | integer `0`; integer 0–5000; integer 0–4096 |
| `recovery_bytes` | integer greater than 64 MiB, at most 128 MiB |
| `query_sha256`, `recovery_wire_sha256`, `recovery_content_sha256`, `recovery_expected_sha256` | lowercase SHA-256; content and expected hashes must match |

Failure-stage enums are `setup`, `database`, `lock`, `wait`, `spool`,
`disconnect`, `release`, `recovery`, `continuity`, `cleanup`. All remaining fields
retain bounded counts/booleans/hashes; no exception text is copied. The query hash
records adapter attribution only. The parent must bind this source and evidence
to the actual packaged executable and exact target revision.

## Offline validation and handoff

TDD RED: the initial `python3 -m unittest
TESTS.test_message_list_query_cancellation` failed because the new adapter did
not exist. GREEN: the same command passes after implementation. These controls
use doubles and never constitute a real PostgreSQL/package receipt.

Controls cover ordering, exact wait/query/blocker/backend attribution, duplicate
waiters, early blocker release, cancellation that only works after unblocking,
backend identity replacement, retained transaction/cursor/spool, thread/socket
failures, early headers, process/directory changes, recovery failures, cursor and
connection cleanup errors, partial acquisition, endpoint rejection, unlinked
file metadata, strict evidence schema, inert imports and secret canaries.

Owned files are exactly:

- `TESTS/adapters/go/message_list_query_cancellation.py`
- `TESTS/test_message_list_query_cancellation.py`
- `docs/architecture/go-migration/message_list_query_cancellation.md`

The ignored `quality-reports/query-cancel-progress.md` carries local progress.
Parent work remains: integrate invocation, run against actual provisioned
PostgreSQL and the packaged HTTP process, bind source/build evidence, and verify
final fixture/service cleanup. Storage-fault cases and complete-family acceptance
are outside this helper. No commit or publication was performed.

## Parent packaged execution checkpoint

The parent executed this helper against the immutable packaged image built from
`07148adf3b01c5865bf3fc4e43b5997314d3de4b`. The actual SELECT was uniquely
attributed in a lock wait. A one-byte unlinked spool was observed before headers.
Client shutdown released the backend and descriptor within 317 ms while the
owned table lock remained held. Recovery returned all 1,152 rows and 75,806,816
bytes in the same process. Final fixture/service cleanup passed.

The independently revalidated receipt is
`quality-reports/go-migration/message-list-query-cancel-image-r1/query-cancellation.json`,
SHA-256 `4bfa65ed0f9fcba65a9c408118f0bfa45a497b007e815e22dad37cfa66c1bb95`.
The parent verified original phase1/build bytes, the exact Go manifest, frozen
source digest, query hash, driver hash and every recovery field against all three
phase1 responses. All 58 parent offline controls passed without skips. Independent
Sol review found no actionable issues. This later documentation text is not
relabeled as an input to the frozen run. ENOSPC, spool read/write failures and
new storage failure during final copy remain outstanding; full-family acceptance
is unchanged. The prototype receipt does not accept a future orchestration driver.
