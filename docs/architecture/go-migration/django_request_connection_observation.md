# Private Django request/native-connection observation

`TESTS/adapters/django/request_connection_observation.py` supplies a test-only,
explicit opt-in observer. It records a generated HTTP request identity and the
positive `info.backend_pid` measured on the native PostgreSQL connection that
Django's execute wrapper is about to use. The query-start event is synchronously
available to the parent's memory snapshot **before the original execute returns**.
It does not run SQL, discover connections through SQL, replace SQL effects, change
settings, create services/sockets, or enable itself on import. Unwrapped apps keep
their existing behavior. Existing QueryTrace/AsgiObserver/CompleteAsgiObserver and
shared `owned_django` composition are unchanged.

This callback establishes Python attribution only. It does **not** prove that a
query reached PostgreSQL, that a connection is still alive, that a transaction has
a particular identity, that requests overlap at the database, or that concurrency
acceptance passed. All live evidence remains parent-owned.

## API and parent wiring

```python
from uuid import uuid4
from TESTS.adapters.django.request_connection_observation import RequestConnectionObserver

observer = RequestConnectionObserver(
    uuid4(), capacity=4096, max_bytes=4 * 1024 * 1024, max_active=128,
)
with observer.installed():
    observed_app = observer.wrap(real_asgi_app)
    # Parent composes observed_app with existing owned_django/CompleteAsgiObserver
    # and QueryTrace without removing/replacing their wrappers or counters.
    # Parent starts, probes, stops and joins its actual services here.
    # While blocked: observer.snapshot() / observer.delta(cursor).
    # Stop ALL SQL/HTTP producers and drain app lifetimes before leaving.
final = observer.snapshot()
assert final['complete'] and final['valid'] and not final['invalidated']
```

The sample is wiring guidance, not a live fixture or integration implementation.
The parent must extend its shared runtime after handoff. Use one observer, a fresh
parent-generated canonical UUID for every run, and the actual application. The
constructor accepts a UUID object or canonical lower-case UUID text. Identity and
capacity properties are read-only. No public request-ID, query-ID, PID, begin/end,
or injected connection API exists. Never reuse a run UUID across separate
observers/runs; the parent owns that uniqueness (there is no process-global
completed-run map).

`installed()` imports Django lazily, registers its own `connection_created`
receiver, then attaches its exact owned execute-wrapper object to current existing
Django wrappers. Newly opened native connections signal and attach as well;
repeated signals do not duplicate registration. It does not open native database
connections. Django's connection registry is thread-local: install before
starting request/worker threads. Already-open wrappers in unrelated existing
threads cannot be discovered by scanning the installing thread. Parent fixture
and monitor connections should be separately owned and identified; intercepted
SQL without HTTP context remains explicitly unattributed.

`wrap(app)` observes **every** actual HTTP scope. It generates `run_id-1`,
`run_id-2`, ... internally, records request begin, sets a ContextVar and adds one
`X-Query-Observation-ID` response header. Query IDs are `run_id-q1`, ...;
sequences are generated internally and cannot be supplied through HTTP. Incoming
CID values have no role. Incoming observation headers invalidate diagnostics.
Any preexisting application observation header, even with the expected value,
also invalidates; all conflicting values are removed and the generated value is
inserted into a copied response-start message. No incoming header value is saved.
Nested HTTP observer lifetimes invalidate, including cross-observer nesting.

The HTTP lifetime ends on application return/raise/cancellation, after any work
following the last response byte. One successfully sent response-start and a
successfully sent final response-body are required. Response progress advances
only after downstream `send()` returns successfully. Every HTTP send has an
independently tracked lifetime; overlapping sends, sends still pending when the
application returns, and send errors/cancellation invalidate permanently, even
when caught by the application or followed by a successful retry. Original
exceptions propagate unchanged. Missing/duplicate/out-of-order response events,
unfinished queries at return, duplicate finishes and late sends invalidate.
Synchronous Django query work reached through installed `asgiref.sync_to_async`
inherits the HTTP ContextVar.
Query end must match its exact original Django context dict, request token,
connection wrapper, native object, PID, and ContextVar execution context. Separate
ContextVar Token provenance guards reject copied-context early ends while leaving
the original live lifetime intact. Request finish has the same provenance guard.

WebSocket/lifespan/other non-HTTP scopes pass their messages/results/errors through
and clear HTTP attribution for their duration. They are outside HTTP SQL
attribution; any intercepted SQL gets null request ID. This adapter does not
replace CompleteAsgiObserver's WebSocket drain/error checks.

## Reads, cleanup and bounds

| API | Result |
| --- | --- |
| `cursor()` | Last retained sequence (zero before any event; constructor emits header at one). |
| `delta(cursor)` | Tuple of copied event dictionaries with sequence greater than cursor. Invalid cursors raise ValueError and invalidate diagnostics. |
| `snapshot()` | Copied events/counters and live validity/finalization state; no wait, SQL or sockets. |
| `close()` | Marks closed, requires drained HTTP/query/send lifetimes, independently attempts signal and every owned wrapper detachment, emits one footer; returns snapshot for ordinary cleanup errors, or re-raises the first cleanup control exception after finalization. |

Events never mutate after append; callers get copies. An error does not erase
prior events. `valid` means no known invalidation/query error/HTTP error so far;
`complete` in a **snapshot** requires closed, drained (including sends), and valid.
An open snapshot is always incomplete even when valid. Diagnostic failures are sticky.

`capacity >= 4` caps retained event count; `max_bytes >= 8192` caps compact ASCII
JSON array bytes of retained events (a conservative extra comma is counted).
Two event slots and 4096 bytes are reserved for invalidation/footer, including an
invalidation after a previously valid footer. Metadata/counters and returned
copies are additional bounded-size structures, so this is not an exact Python
heap-byte quota. `max_active >= 1` independently caps retained connection-wrapper
registrations and the sum of indexed request/query tokens; active native bindings
cannot exceed query tokens. Defaults above are explicit test budgets. Overflow
invalidates immediately; events are not evicted or silently treated as complete.
Counters continue to count actual HTTP/execute starts/ends after overflow, so extra
HTTP activity cannot disappear into successful acceptance. Excess tokens are not
indexed or bound. Temporary per-call locals follow actual app concurrency; there
are no completed-ID maps. Send bookkeeping adds only an observer-wide count and
per-request count/error flag; it retains no messages, exceptions or send-task map.
Snapshot copies are caller-owned and must also be read with a bounded parent cadence.

A single shared reentrant lock serializes small observer bookkeeping sections,
including cross-observer invalidation, to avoid lock-order deadlocks. Original
execute/application/send calls run outside it. A query retains its measured native
object, wrapper and Django context only until its actual finally ends; completed
query tokens clear these references. Bounded registered Django wrappers remain
owned for detach until close. Overlapping native-object **or PID** bindings,
missing/nonpositive/noninteger PIDs, closed/lost/replaced connections, changed PID,
unregistered wrappers, nested/reused/late/cross-context callbacks invalidate.
Sequential native connection reuse across distinct generated requests is allowed;
the parent still checks backend_start and transactions independently.

`close()` does not wait or kill services. The parent must stop/join actual services
and drain all SQL/HTTP producers, including outstanding sends, **before** close
and fixture reset. Premature close invalidates and still attempts signal
disconnect plus each wrapper removal,
preserving unrelated wrappers/order. Repeated close retries failed detachments
without a second footer. Ordinary cleanup `Exception` failures remain sticky
invalid diagnostics and return the failed snapshot. Cleanup control exceptions
(`BaseException` outside `Exception`, including `KeyboardInterrupt`, `SystemExit`
and `asyncio.CancelledError`) are deferred until every remaining detachment has
been attempted and invalid evidence finalized, then the first control exception
is re-raised as the same object with unchanged arguments. No healthy final receipt
is emitted after a cleanup fault. A caller can read `snapshot()` after catching a
control exception, and retry `close()` to detach registrations still owned.

`installed()` preserves any original installation or body exception/cancellation
over a cleanup exception, including failures after partial signal registration
or wrapper attachment. On a normal body exit, cleanup control exceptions propagate.
Original execute/ASGI failures escaping the installation body therefore retain
their identity and arguments. Retained callbacks/wrapped requests invoked after
close invalidate and increment `post_close_events`, even after a successful
footer; require a final snapshot after producer drain. New SQL invoked through
already-detached wrappers cannot be intercepted: correct parent shutdown is
mandatory, not inferred from a footer.

## Private fixed event schema (version 1)

Each event contains only these fixed fields, with two conditional fields:

| Field | Meaning |
| --- | --- |
| `stream`, `version`, `run_id` | `query_connection`, integer `1`, parent UUID. |
| `sequence`, `type` | Contiguous 1-based retained sequence; `header`, `request_begin`, `query_start`, `query_end`, `request_finish`, `footer`, `invalidated`. |
| `request_id`, `query_id` | Internally generated strings, or null when not applicable. SQL outside an HTTP request has null request ID. |
| `backend_pid` | Present only on query events with measured matching native binding; positive integer. Invalid/missing bindings omit it. |
| `error`, `complete` | Booleans. Error indicates execute error at query end or an application/send error known at request finish, including caught send cancellation. Complete means healthy so far; header/invalidated are false. It is not drain, PG arrival, or acceptance proof. |
| `reason` | Only on `invalidated`: first fixed diagnostic reason code, never raw exception text. |
| counters below | Nonnegative numeric counts on ordinary valid lifetimes; malformed synthetic callbacks invalidate rather than being accepted as balanced. |

Every event embeds all counters:

```
request_starts request_finishes finish_calls active_requests
query_starts query_ends active_queries active_bindings
unattributed_queries request_errors query_errors
connection_violations post_close_events
```

Counters are global observer totals/current gauges, not per-request SQL summaries.
`request_errors` counts a tracked HTTP request once across application and send
errors, including caught errors. A send that fails after application return can
increment that live counter after `request_finish`; previously emitted events
stay immutable and the stream remains invalid. Pending sends are tracked
privately without adding counters or changing version-1 snapshot/event keys.
`finish_calls` includes invalid/duplicate HTTP finishes; `query_ends` includes
attempted invalid/duplicate query ends. `unattributed_queries` counts queries with
no HTTP context, including fixture/monitor or non-HTTP scope work that is actually
intercepted. A stale/foreign/rejected HTTP context instead invalidates diagnostics.
This independent stream does not alter QueryTrace summaries. Go-peer vocabulary
matches stream/version/type/sequence and run-prefixed generated IDs; the parent
parser must allow the explicitly documented Django counters and request-finish
error semantics rather than assuming identical backend counter sets.

Snapshot keys are `version`, `stream`, `run_id`, `events` (copied tuple), `counters`
(copied dict), `valid`, `invalidated`, `reason`, `overflow`, `closed`, `complete`,
`retained_bytes`, `attached_connections`. No native objects or tokens are exposed.
No SQL, parameters, URLs, body bytes, status, auth, client headers, exception
names/text, connection addresses or object identities are recorded. Treat run IDs,
PIDs and generated request IDs as private test evidence.

## Parent live acceptance still required

While the real same-owner/same-message concurrent DELETE calls remain blocked,
the parent must read `query_start` and pair each generated request/query ID with
its measured PID. On its independently owned monitoring connection, reconcile
that PID against actual `pg_stat_activity.backend_start`, permanent transaction
identity and virtual transaction identity, plus the expected lock chain and
outstanding HTTP lifetimes **before releasing the blocker**. Reject missing,
ambiguous, recycled or mismatched bindings. Python callback ordering is not that
catalog evidence.

After release, match every generated request ID to its **original response**
observation header; require matching query-end/native bindings and complete HTTP
lifetimes. Account for all extra actual HTTP scopes and unattributed SQL rather
than filtering them by caller labels. Reject overflow, any invalidation/error,
missing/duplicate/late events, response header conflicts, incomplete snapshots,
timeouts and cleanup failures. Stop/join producers, close, and require final
snapshot validity plus one complete footer and balanced counts. Parent owns
persistence/private files, parser/runtime wiring and actual acceptance results.

## Offline RED/GREEN evidence

The prescribed production Python is
`/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python`
(Python 3.13.16, Django 5.2.18, asgiref 3.8.1).

Initial RED: the test module failed with ModuleNotFoundError before this adapter
existed. Behavioral RED during review: copied execution contexts or replaced
Django context dicts could prematurely end a live query; the new test failed
`True is not false`. Token provenance and exact-context matching fixed this.

Run the bounded suite:

```text
<production-python> -m unittest TESTS.test_django_request_connection_observation -v
```

Repair RED: the original 31 tests passed, but caught downstream send failures and
pending final-body sends at application return could still yield healthy streams.
Before the fix, the first four added tests produced nine failing subcases; the
premature-close rejection and normal awaited-child-task controls passed. The
expanded caught-failure and pending-send regressions, loaded against the saved
unmodified adapter, produced **15 failing subcases** (zero test errors), including
`valid=True` after caught final-body failure or pending send at application return.

Send-lifetime repair result: **40 tests GREEN**, with no services, database connections or sockets.
The nine repair tests cover caught start/streaming/final-body errors and
cancellation with/without retry; real `Task.cancel()` caught by the application;
escaping final-body failures counted once; multiple send failures followed by the
exact original application exception; pending sends that later succeed, fail or
cancel; close during send; overlapping sends; 100 repeated caught send failures
with released request/send bookkeeping; and normal awaited child-task sends.

Connections/native info are explicitly synthetic, and the real installed asgiref
thread adapter executes without a DB. The test-only scheduler avoids asyncio's
socketpair wakeup pipe and forbids `socket.socket`/`socket.socketpair`. Tests cover
concurrent synthetic execute calls visible before release; generated/spoofed IDs;
extra HTTP; native lookup/reuse/overlap/loss; wrong/missing/duplicate/late ends;
ContextVar thread propagation and provenance; response lifetimes; original
exceptions/cancellation; event/byte/registration/active bounds; independent cleanup
and retries; partial installation failure; existing QueryTrace coexistence; no
sensitive fields; inert imports and unchanged unwrapped defaults. Synthetic tests
provide no positive live PG/transaction/HTTP concurrency evidence.

Cancellation repair (parent review finding 3 only): the unchanged 40-test suite
passed first. Eight added tests run against that unchanged adapter produced
**17 failing subcases**, all swallowed cleanup control exceptions (zero test
errors). After repair, the prescribed command passes **48 tests**. The original
40 test methods remain unchanged. Added controls cover signal/wrapper control
exception identity and arguments, mixed ordinary/control faults with every
detachment attempted, first-control precedence, normal installation exit,
original application/execute failures and cancellation, original body control
exceptions, failed partial signal registration/connection scanning, sticky
ordinary cleanup errors, preserved unrelated wrapper order and retry without a
duplicate footer. These remain offline synthetic controls; parent integration
and actual services verification remain outstanding. No public fields, method
signatures, snapshot keys, counters or version-1 event schema were added.
