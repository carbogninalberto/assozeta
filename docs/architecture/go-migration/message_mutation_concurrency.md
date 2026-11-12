# Same saved-message DELETE concurrency probe

This is an inert, independent characterization companion. Importing
`TESTS.adapters.django.message_mutation_concurrency` starts nothing. Calling
`run` sends exactly two native HTTP DELETEs for the canonical present
`message-mutations-delete-email` target, using its existing ordinary signed
owner token. It never creates/resets fixtures, starts/stops a server, discovers a
DSN, retries a request, or changes application/reference behavior.

The full goal still requires independently attributed overlapping transactions
for the two exact DELETE requests, one **200** and one **404** in either order,
and exactly one deletion effect. Optional connection observation now joins generated
request/query IDs and actual native PIDs to independently validated catalog
transaction identities before blocker release. `run` always withholds full
acceptance and raises `ConcurrencyFailure`: finalization belongs after parent
service shutdown. Without the callback it retains honest characterization under
exclusive two-request isolation. This offline implementation establishes no new
live Django/Go result. Existing 119-case/browser evidence supplies no concurrency
acceptance, and the full message-family migration goal remains incomplete.

## Explicit API

```python
run(*, backend, apps, database, raw, fixture, base_url, process_guard,
    observer, client_ip, private_evidence, bounds=Bounds(),
    connection_observation=None)

finalize_attribution(private_evidence, final_observation)  # pure, copied safe result
```

| Input | Parent responsibility |
| --- | --- |
| `backend` | Exactly `"django"` or `"go"`; native target selected explicitly. |
| `apps`, `database` | Ready production Django registry and already-connected, autocommit, nonpooled default PostgreSQL `DatabaseWrapper`; all 128 managed models and full migrations. Private database name starts with `assozeta_contract_`. |
| `raw` | Actual raw Redis DB1 client, finite socket/connect/pool waits no greater than `bounds.query`, no retries or health-check precommands. Accepts `retry=None` or the installed concrete `redis.retry.Retry` with integer `_retries == 0`. |
| `fixture` | A fresh actual `message_mutations.fixture_context` fixture, including authored/runtime documents, ordinary signed tokens, baseline state/cache and cache contrasts. It must be reset separately for each backend. |
| `base_url` | Parent-owned actual native `http://127.0.0.1:PORT` origin, no gateway/API/version prefix. Bind it independently to the reviewed Django owner or actual Go executable/source. |
| `process_guard` | Existing actual owner's measured `pid/running/instance` callback. Must remain exactly stable. No invented backend/version label is accepted as attribution evidence. |
| `observer` | Actual `CompleteAsgiObserver` wrapping the Django application, also present during the Go run. Its entire window must contain exactly the two Django requests, or zero events for Go. No websocket activity is permitted. |
| `client_ip` | Independently measured native source IP. Must be a canonical loopback address usable as the IPv4 source for this origin; the probe binds and verifies it rather than forwarding a header. |
| `connection_observation` | Optional synchronous callback returning exactly `{backend, run_id, events}` from the actual connection observer. Parent bounds reads/decode/file I/O and owns the stream source. All records must be complete and cumulative from the header, including readiness traffic. See the attribution boundary below. |
| `private_evidence` | Required empty parent-owned dictionary. Full originals remain here in memory on failure and cancellation, with safe `diagnostics` added. It includes signed request tokens, raw HTTP, SQL, models and complete raw DB1; never publish or print it. Parent may persist it only to its own private destination. |

PostgreSQL uses a single explicit Unix socket directory or numeric loopback
host and explicit port matching the actual connected server, without service-file,
multi-host or pooled connection acquisition. Both copied connections must match
the supplied connection's actual host/port and database/user identity.
The parent must already bound its supplied default connection's I/O. The probe
temporarily bounds its statement timeout, bounds copied observer/blocker
connections, and restores the original setting. No pool/service/DSN fallback is
available. Signature, expiry and access-token identity are verified using actual
Django JWT settings when explicitly called.

The installed Redis `Retry` has `_retries` and no `get_retries()`. Its actual
implementation treats negative values as unbounded. Validation accepts only the
concrete installed class and an exact integer zero; it rejects positive/negative
counts, bools, coerced values, subclasses and duck-typed accessors. With
`retry=None` and both retry flags disabled, the installed `Connection` constructor
creates `Retry(NoBackoff(), 0)`. Offline controls instantiate both supported paths
and verify the constructed policy without opening any socket. There is no
heuristic/default-count fallback for unknown retry objects.

The installed production runtime uses psycopg2. Offline source inspection of
Django's PostgreSQL `get_new_connection` confirms
`register_default_jsonb(..., loads=lambda x: x)`: raw cursor JSONB aggregates can
be text. Activity therefore retains that original payload, explicitly decodes
text (or accepts a driver-decoded list), and strictly validates exact lock keys,
types, modes and tuple/permanent/virtual-XID ranges. Malformed, duplicate-key or unknown shapes fail
closed without supplying missing witnesses. Installed psycopg2's public
`connection.info` and `ConnectionInfo.backend_pid/host/port` descriptors were
checked offline; no connection or low-level typecaster was invoked. Actual cursor
representation and values remain parent-only PostgreSQL checks.

Default bounds, in seconds:

```python
Bounds(connect=2, read=15, query=1, overlap=5, overall=30, cleanup=10, poll=.02)
```

Connect is an integral 1–5 seconds, compatible with libpq's actual timeout
granularity. Every bound must be finite; the read budget must exceed the overlap
and query budgets, and the independent cleanup budget must permit connection and
query completion. Read is an absolute complete-response deadline, including the
period spent blocked. Overall covers setup/snapshots/admission/overlap/completion;
cleanup has its own deadline. Each synchronous database/Redis operation must also
respect its finite underlying I/O boundary. Observer waits and thread joins always
receive finite remaining time. A late operation cannot become successful evidence.

## Catalog overlap and exact effect

The probe first checks the fixture against the independent canonical portable
contract, including its digest and full authored projection. It checks the complete
baseline against the supplied fresh fixture, a present owned target, both actual
delivery children, foreign-message/delivery sentinels, and historical audit/index
sentinels. Rewriting both supplied manifests cannot rewrite expectations.

One probe-owned transaction locks the exact canonical message and its two actual
delivery rows with real PostgreSQL `FOR UPDATE`. It records their relation OIDs and
CTIDs, the owner row identity without locking that owner, and the blocker's real
PID/XID/virtual-XID/transaction-start/backend-start identity. No row is modified by the blocker.

Both actual clients must remain outstanding, with sent requests and no response
status. Before rollback releases the blocker, **two successive catalog
observations** must show two distinct active transaction/backend identities and
a validated dependency path from each to that same blocker. Every previously
observed wait must remain present with the same PID/virtual-XID/transaction-start/
backend-start identity. A permanent XID can first appear within that lifetime;
once present it cannot change or disappear. This proves catalog overlap only. Outstanding HTTP clients, polling
delays, thread starts and eventual statuses cannot identify whose transactions
were observed.

Each edge requires `pg_blocking_pids`, exact tuple coordinates, and either an
actual transaction-ID wait on the parent's XID or an actual tuple-lock queue
dependency. The root is revalidated as the owned idle transaction. The narrow SQL
allowlist recognizes the current Go owner/message `FOR UPDATE` statements,
the exact parent-observed corrected Django scoped `SELECT ... FOR UPDATE`, and
Django's historical collector DELETE grammar, including the complete canonical
message or delivery ID set. Delivery DELETE accepts both primary-key batches and the
collector fast-cascade `message_id IN (canonical-target)` form; both still require
exact owned delivery tuple witnesses. Installed Django `Collector.related_objects`
and `fast_deletes` source supplies this distinction, not a live observation.
The corrected SELECT comes from the parent's
`quality-reports/go-migration/parallel-scale-20261025/message-delete-corrected-sql-observation.json`:
both measured active/Lock request PIDs had the same template, with only the actual
target/association UUID literals replaced by markers. The recorded private-original
SHA256 is `a3d32f67905bfaa1329e9e185fc77bfc8ee263ac5e9424c584b704d8273ffd72`.
It selects exactly `message_id, sport_association_id, type, message, subject,
created_at` from the quoted `communications_message` table, with fully qualified
quoted columns. Its parenthesized predicates are `message_id = '<target>'::uuid`
then `AND sport_association_id = '<association>'::uuid`, followed by
`ORDER BY "communications_message"."message_id" ASC LIMIT 1 FOR UPDATE`.
Acceptance uses exact bytes and canonical lowercase UUIDs matching the held
blocker's target and association. No whitespace, comments, casts, columns,
predicate order, additional predicates, joins, lock options, order or limit
variants are normalized or accepted. The resulting allowed tuple set contains
only the exact held message row; a delivery tuple cannot witness this SELECT.

The blocker's scoped SELECT now returns the actual stored
`sport_association_id::text` alongside the message ID, relation OID and CTID.
The returned message ID and association must equal the explicit canonical fixture
before any Blocker is created. Its private `association` comes from that held row,
not an unchecked caller label. Missing, distinct or foreign stored scope fails.
Canonical manifest validation also rejects jointly rewritten target/scope values.

This supports Go owner serialization chained to the message,
corrected Django message SELECT waits, and historical Django delivery/message
collector waits. Unknown SQL, foreign tuples/literals, missing
witnesses, extra/ambiguous blockers, lost/transient waits and an unobserved second
transaction fail closed. The real catalog shapes still need parent validation.

The catalog projection includes only `tuple`, `transactionid` and `virtualxid`
locks for each actual backend PID. Every JSON object has exactly these keys:

| Key | Exact catalog source and validation |
| --- | --- |
| `kind`, `granted`, `mode` | `locktype`, native bool `granted`, and an explicitly supported lock mode. |
| `xid` | `transactionid::text`; positive canonical uint32 text only for transaction-ID locks, otherwise null. |
| `relation`, `page`, `item` | `relation::bigint`, `page`, `tuple`; bounded integer tuple coordinates only for tuple locks, otherwise null. |
| `virtualtransaction` | Owning transaction's catalog identity on every projected lock, canonical positive `backend/local` uint32 components. |
| `virtualxid` | `virtualxid::text`; canonical positive `backend/local` uint32 components for virtual-XID locks, otherwise null. |

Each active waiter must have exactly one granted `ExclusiveLock` on its own
virtual XID. That target must equal the `virtualtransaction` on **every** projected
lock for the PID. Nonempty `xact_start` and `backend_start` bind this identity to
the measured PID and backend incarnation. `backend_xid=NULL` remains actual null
in the private proof; it requires no granted permanent-XID lock. An assigned
`backend_xid` requires exactly one matching granted permanent `ExclusiveLock`.
Missing, duplicate or conflicting self-locks fail closed. The blocker captures
its own measured transaction start and virtual identity on its actual connection;
each monitor sample revalidates those against its self-locks and permanent XID.

Private `WaitProof` is now `(pid, xid, xact, backend, vxid)`, with `xid` either
actual permanent-ID text or `None`; `Blocker` includes measured `xact`, `vxid` and
the held row's canonical `association`. Its message SELECT projection has four
columns rather than three. That SQL repair did not change required inputs; the
optional callback and provisional/final diagnostics are described below.
Distinct virtual identities, all assigned permanent IDs, and backend starts
remain unique across the two waiters **and the owned blocker**. Polling permits
only `None -> actual XID` within the unchanged lifetime. Reused PIDs, changed
backend/transaction starts, stale/changed virtual IDs, disappearing waits and
permanent-ID changes fail. A transaction-ID dependency can target only the
parent's actual assigned XID; virtual identity never substitutes for that edge,
the exact tuple witness, or the exact SQL allowlist. Catalog sampling races
or unsupported shapes fail closed and require parent inspection.

Catalog waits alone cannot identify HTTP requests. In particular, the Go owner
`FOR UPDATE` also belongs to other mutations. The optional connection stream
supplies a generated request/query/native-PID association; the following verifier
joins that association to the exact catalog proof. The private `overlap` field
continues to contain catalog proofs only.

The original HTTP status line/version/reason, ordered headers, complete body and
framing bytes are retained privately, including partial/error responses. The
existing lifecycle framing helpers enforce finite bodies, unambiguous length or
chunked framing, mandatory chunk terminators and complete EOF. Re-parsing original
bytes rejects hidden buffered tails. No redirect, retry, proxy, fake response,
`SELECT 1`, forced endpoint or production test hook participates.
Response JSON also rejects duplicate keys before comparing the authored body.
Bytes consumed by the separate raw-socket EOF check are recorded too: a trailing
byte arriving after the complete frame must appear in failed private evidence.

After complete responses and ASGI/database quiescence, the existing portable
single-DELETE oracle compares every field of all 128 models and the complete raw
DB1 snapshot. The target and its actual deliveries must be gone; exactly one new
audit and one index must match the winning request's actor, correlation, client IP,
time bounds, changes and index path. Foreign state/history and all cache entries,
types, serialized values and absolute expirations must be preserved. Duplicate
audits/indexes are never removed or normalized away. Both effect/cache domains are
checked even when response validation fails.

Correlation IDs are fixed and distinct:
`message-mutations-concurrency-{django|go}-delete-{1|2}`. Reusing a correlation in
the observer's history fails before HTTP. Extra, duplicated or misattributed ASGI
traffic fails across the full window through cleanup, even if status/body match.
The Go window must be empty, so an actual Django response on the intended Go path
cannot pass. Native target/source/binary binding belongs to the parent; this
probe makes no gateway/version-routing claim. The corrected SQL grammar remains
independent of the optional request attribution verifier.

## Connection stream attribution boundary

`TESTS.adapters.django.message_connection_attribution` is a small pure consumer,
independent of either producer. Its strict `parse_observation` accepts only:

```python
{"backend": "django" or "go", "run_id": canonical_nonzero_uuid,
 "events": complete_cumulative_decoded_records_from_actual_stream}
```

Do not pass producer snapshot metadata, `valid`, success/binding flags, filtered
request subsets, deltas, partial JSONL lines or a guessed PID. Django parent code
projects the actual observer's `run_id/events` into this envelope; Go parent code
bounds and decodes complete actual connection JSONL records. Duplicate JSON keys
must be rejected during parent decoding, before a dictionary can erase them.
The parent owns byte/file bounds, read deadlines and source authenticity. A
synchronous callback that stalls cannot be interrupted safely by this consumer;
parent I/O must obey its bound. The parser caps cumulative streams at 65,536 events.

The consumer pins exactly the current version-1 schemas in the read-only
[Go producer contract](../../../../assozeta-go-query-connection-observation-sol/docs/architecture/go-migration/query_connection_observation.md)
and [Django producer contract](../../../../assozeta-go-django-connection-observation-sol/docs/architecture/go-migration/django_request_connection_observation.md).
These are sibling candidate references; parent integration must bind their actual
reviewed source. Go omits absent identity fields; Django explicitly emits null
request/query IDs. Django has 13 counters and keeps HTTP send bookkeeping private;
the completed send repair changes no event fields. Go retains its full query,
lifecycle, connection and driver counters. Unknown/missing keys, unknown types,
wrong booleans/numbers, foreign/zero/noncanonical run UUIDs, gaps, duplicates,
invalidations, errors, sticky violations and overflow symptoms all reject.
Driver totals must remain monotonic, reconcile connect/reset/ping operations and
open-operation gauges, and drain in the footer. The separate Go summary stream
still needs the parent's independent final verifier.

The header is first, unique, sequence one and all-zero counters. Generated request
IDs must be `run_id-1`, `run_id-2`, ...; query IDs must be `run_id-q1`, ... in order.
The parser recomputes request/query counters and gauges from all events, checks
unique active native PIDs, and requires every end to match its original query,
request and PID. Attributed queries must remain inside a single begun/finished
HTTP request lifetime. Django alone permits explicit null request IDs on query
events: each still has its next generated query ID, positive native PID, exact
matching end and full query/binding gauges. `unattributed_queries` is recomputed
as the cumulative number of null-request starts, including before baseline and
after the HTTP responses. All error/violation counters remain zero. Go unbound
queries remain forbidden. No caller flags can repair a malformed lifecycle.

Null-request accounting is separate from ownership acceptance. For Django, the
probe measures exactly its supplied default wrapper and its own blocker/monitor
copies. Each native `connection.info.backend_pid` must match a self-catalog
`SELECT pid, backend_start::text ... WHERE pid = pg_backend_pid()` on that actual
handle. The same wrapper/native object, PID and backend start are remeasured
before cleanup closes the copies and before default-connection timeout restoration.
The default native object/PID is checked again after restoration, then its stream
cursor is sealed; no new self-catalog SQL runs with the restored timeout.
Missing, foreign, repeated/ambiguous or replaced handles/PIDs/incarnations fail.
Every null-request event must belong to this measured set; any HTTP query on an
owned PID also fails, even if it runs sequentially. Existing catalog samples must
agree with these backend incarnations, including the blocker's held identity.

Private `connection_owners` retains exactly `default`, `blocker`, `monitor`, each
with `native_pid`, original `catalogs` readings and `end_cursor`. The cursor seals
all SQL on that handle before close/return. All setup/fixture, monitoring,
snapshot and post-release cleanup SQL stays in the original cumulative stream;
nothing is omitted, filtered, relabelled or assigned synthetic zero counters.
Finalization repeats ownership checks against the complete final stream and
rejects any later null-request query beyond a sealed cursor. The parent must
perform no additional instrumented fixture SQL between probe return and observer
shutdown. Fixture reset remains after producer shutdown, as in the parent runner.
No callback/envelope or producer schema expansion is required. These measurements
cannot authenticate caller-forged evidence; actual handle/stream ownership and
the absence of external connection replacement remain parent responsibilities.

Immediately before launching the two DELETEs, `run` captures a complete baseline
prefix. Earlier readiness/health requests may exist only as fully completed,
healthy, accounted lifetimes. No unknown active baseline request/query/driver
operation is allowed. At most two new generated request IDs may begin after this
baseline, including requests that issue no query. Extra begin/finish pairs cannot
hide through correlation filtering or through eventual completion.

Every exact catalog sample is bracketed by two callback snapshots while the
blocker remains held. Each observed WaitProof PID must have exactly one active
query in the preceding prefix and the same query/request/PID still active in the
following prefix. Two waiters require distinct requests, queries and PIDs. The
request must be a real active generated HTTP lifetime begun after baseline;
a null-request query never qualifies as an HTTP waiter. The verifier preserves
the actual catalog `backend_start`, `xact_start`,
`virtualtransaction/virtualxid` and nullable permanent XID with that association.
Across samples the whole binding must persist: PID reuse, backend/transaction
replacement, changed/ambiguous queries or a vanished wait reject. Permanent XID
assignment permits only `None -> measured XID`; it never substitutes for the
virtual identity. Two successive full catalog pairs remain mandatory.

Callbacks must expose exact cumulative prefixes; rewrites, truncation and sequence
loss reject. Private `connection_attribution` retains the latest owned copied
stream, baseline cursor and each bracket's cursors/activity index. It stores
new records once rather than duplicating every full prefix. Existing private
`activity` retains original catalog rows; final verification reruns the exact
SQL/tuple/dependency and transaction predicates against every retained sample.

After release, the original wire is reparsed, then each original response must
have exactly one case-insensitive `X-Query-Observation-ID`. Their two distinct
values must equal the two captured generated request IDs. Missing, duplicate,
spoofed, foreign-run, whitespace-altered or shared values reject. CID, launch
order, client labels and caller success/binding flags play no role. Query ends
and request finishes must match, including all post-response work and any sticky
request/send errors recorded by the producer.

## Failure, finalization and ownership

`run` always raises `ConcurrencyFailure("message-concurrency-failed")` after its
normal cleanup. Existing no-callback behavior remains: attribution, overlap and
full acceptance stay false, with `request-transaction-attribution` in failed
checks. With a valid callback, completed headers and matched post-release ends,
`request_transaction_attribution_verified` and `overlap_verified` can become true
provisionally. `success` and `full_concurrency_acceptance` remain **false**;
`attribution_finalized=False` and the fixed `pending_reason`/failed check is
`connection-observation-finalization-pending`. A missing footer is expected while
services run and never causes an early failure solely for being absent.

After actual producer shutdown, the parent calls
`finalize_attribution(private_evidence, final_observation)`. This pure verifier
returns a fresh count-only safe dictionary, never mutates the supplied originals
or promotes their diagnostics/acceptance flags. It independently revalidates the
entire final connection stream, every retained exact prefix/catalog bracket,
original response wires/IDs, canonical manifest projection, fresh baseline,
128-model/raw-DB1 exact effect, ASGI window and all cleanup/guard checks. The
canonical source is loaded independently during `run` and retained for pure
finalization; finalization performs no file, database, network or service I/O.
Only the required pure fixture fields are retained, including a copied immutable
raw-cache mapping; live model/fixture ownership handles are excluded.

A valid final connection stream has exactly one complete last footer, balanced
requests/finishes/queries/bindings/driver operations, zero open counts and no
record after the footer. Missing/incomplete/mismatched/duplicate footer,
post-close invalidation, late events, truncation, new requests, rewritten prefixes,
any previously failed execution or any failed exact effect/cleanup rejects.
Failure returns fixed `connection-observation-finalization` diagnostics. Success
returns `attribution_finalized=True`, `success=True` and
`full_concurrency_acceptance=True` **for this single backend probe's evidence**.
The original `run` diagnostics remain provisional and unchanged. Synthetic inputs
can exercise this branch but supply no live proof.

This pure verifier cannot authenticate a supplied stream, observe process exit or
detect an external writer error/forced termination that leaves no new record.
The parent must separately require actual owned-process/source/native-port binding,
normal bounded shutdown and producer drain, successful observer Close, successful
file Sync/Close, no forced termination/stall/timeout/writer failure, and the final
actual source after all producers stop. For Go, require complete valid footers
and reconciled IDs/counters in **both** the summary and connection outputs and the
joined Close result. A connection footer alone never establishes those facts.
Any failed external requirement vetoes overall acceptance even when this verifier
returns true. Producer close/drain cannot be replaced by caller flags in the
consumer envelope. The full migration goal remains incomplete.

Cleanup now retains a required private `terminal_observation` with the exact
`activity_cursor` and complete final `asgi` window. Its catalog is sampled after
release, HTTP joins/idle and the final full-state snapshot, before owned copies
close. Finalization requires this to be the last retained catalog, strictly after
all overlap samples, and requires the saved ASGI window to match the revalidated
full window. Appending even an idle catalog without updating the boundary fails.
The terminal catalog must include the original measured blocker incarnation in
`idle` state, with no transaction, permanent XID, blockers or projected locks.
Every other observed session must satisfy those same idle/drained predicates.
The held-blocker exclusion in the polling `busy()` helper is never used here.
Missing terminal evidence, recycled blocker, active/open transactions or held
locks fail despite positive cleanup booleans. Both original HTTP responses must
also have completed attempted sends and independently reparsable complete wires.
This is retained observation evidence; actual parent process shutdown remains an
external prerequisite and is not inferred from an instantaneous idle catalog.

Safe run diagnostics preserve actual statuses/completeness, counts, fixed failure
categories and cleanup, never SQL/IDs/credentials/exception text. `catalog_waits`
contains last validated waiter/pre-XID/permanent-XID counts; `catalog_wait_failure`
is a fixed identity/catalog failure category. These are observations, not a claim
that they survived a later failure. Callback errors/malformed data fail closed
while preserving cleanup and original control cancellation.

Control `BaseException`s such as `ExecutionInterrupted`, `KeyboardInterrupt` and
`SystemExit` survive cleanup and final verification as the **same exception
object**, re-raised with safe `.diagnostics`. Worker-thread control exceptions
transfer to the calling thread without printing through `threading.excepthook`.
Cleanup attempts the remaining owned resources, retains fixed failure categories
and re-raises its control exception after recording safety. A later cleanup
interrupt cannot replace an earlier observed cancellation. The parent must stop
its backend loop on these exceptions, preserve evidence and honor the reset gate;
catching only `ConcurrencyFailure` must not consume cancellation. Do not publish
the original exception text/traceback, which may contain private strings.

Always inspect `diagnostics["cleanup"]["reset_safe"]`. The probe releases only its
blocker, closes only its clients/copied connections, joins its two client threads,
checks ASGI/DB quiescence, restores its supplied connection setting and verifies
the actual parent guard. It never kills or consumes the server. An incomplete
sent request whose original response is incomplete remains unsafe even when a
catalog pair was observed: absent attribution, it could still be queued before
admission. Failed/interrupted cleanup or uncertain admission keeps
`reset_safe=False`. Only complete attempted requests, joined/closed clients and
the remaining explicit quiescence/resource/guard checks can make reset safe.

The parent must hold its fixture-reset gate until this evidence is available.
When false, stop and verify quiescence of the actual parent-owned server before
resetting. `fixture_context` has unconditional cleanup on exit: do not let it
unwind into a reset while server cleanup or outstanding request ownership is
unproved. Preserve the private evidence and quarantine the fixture if stopping
the parent-owned service itself fails.

Parent sequence: use fresh canonical fixtures separately for Django and Go;
compose the existing complete ASGI observer with the actual connection observer;
keep fixture reset gated; supply the bounded cumulative callback; catch ordinary
`ConcurrencyFailure` and retain private originals; stop/join services and all
producers; close/finalize observers and files; validate both Go streams and owned
process/source evidence; then call the pure final verifier. On control
cancellation, stop the backend loop and preserve the same exception after guarded
shutdown. Never reset while service/admission safety is uncertain. Parent runtime
and observer producers are outside this worker's scope.

The parent's redacted actual r4 receipt at
`quality-reports/go-migration/message-delete-concurrency-live-r4/results.json`
was read independently: both backends have two catalog waiters, actual 200/404,
one audit/index, exact cascade of two deliveries, 128 models/raw DB1 and cleanup.
Full acceptance correctly remains false only for absent measured request
attribution. No private pickle was read. Those earlier observations do not
retroactively contain the new streams or prove the new consumer's live behavior.
Parent integration, fresh actual PG/HTTP execution and review remain required.

## Offline verification and handoff limits

Portable form of the recorded command, from the repository root. Set
`PRODUCTION_PYTHON` to the already installed Python 3.13.16 executable.

```sh
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest TESTS.test_message_mutation_concurrency TESTS.test_message_connection_attribution -q
```

The initial 67 methods passed before editing. Initial new-suite RED was an absent
parser import. Behavioral RED caught invented balanced driver history in a header
and arbitrary cleanup data escaping safe final diagnostics (two failures), then
GREEN. Further RED reproduced production MappingProxyType fixture-copy failure;
a narrow pure projection fixed it. A pure-finalization control then failed when
canonical validation tried reading a file; independently retaining the canonical
source in `run` fixed it. The initial attribution candidate had **93 methods**,
preserving all original 67. Controls include both backend schemas, complete baseline requests, prefix
rewrites/gaps/duplicates/truncation, errors/late/footer events, mismatched run and
request/query/native PID, recycled incarnation and transaction identity,
permanent-XID transition, missing/duplicate/spoofed original response IDs, extra
query-free requests, callback exceptions and exact KeyboardInterrupt/asyncio
cancellation identity, post-release ends, provisional run results, pure final
reverification and unchanged inputs/safe copied output.

The r5 repair first reproduced the actual first null-request query at sequence
two as RED at the old zero-only counter check (five failing producer-shaped
controls). The ownership integration controls were RED before handle measurement
existed. A separate behavioral RED reproduced the reviewer's terminal open
transaction: the old finalizer returned full acceptance with forged positive
cleanup flags. Current GREEN: **109 methods**, including all prior 93. The
shared synthetic finalization fixture was deliberately expanded with an actual
terminal catalog/window, because the verifier now requires that evidence.
Added controls cover baseline/monitor/cleanup unassigned SQL and totals, exact
null-query ends, unowned/recycled/ambiguous/retired PIDs, HTTP/null PID confusion,
null HTTP waiter rejection, Go unbound rejection, pure Django finalization,
terminal boundaries/catalogs/locks/blocker/ASGI/admission contradictions, and
owned-measurement cancellation. The parent's r5 Go attribution/finalizer success
and Django pre-request parser failure are parent observations; this repair has
no new live acceptance result. Parent must rerun actual PG/HTTP composition.

All new observations in these tests are synthetic. No services, sockets,
installations, external actions, Git mutation/publication or nested agents were
used. This handoff changes only the five authorized adapter/test/doc files and
`quality-reports/worker-progress-attribution.md`. Producers, application/runtime,
shared oracles, prior live/browser results and infrastructure remain parent-owned.

## Parent verification of the integrated candidate

The fresh native Django/Go run `message-delete-concurrency-live-r6/results.json`
passed with receipt SHA-256
`87a779e80a190e2206541f072a54050c68b59630d431f240aa24e6b25ca63e42`.
It binds the candidate on `a827f96d` to 1,487 runtime source hashes, three
additional adapters and the actual native binaries. The parent independently
reparsed both original HTTP responses, replayed finalization, checked request
IDs and terminal catalog quiescence, and compared all 128 model snapshots and
raw Redis state. Each backend returned one 200 and one 404, removed the target
and two deliveries, and produced one audit record and index entry. Both Go
streams finalized and all owned services stopped.

Independent Sol review confirmed the three repaired findings; its 157 focused
offline tests passed. The earlier r5 failure remains historical evidence and is
not relabeled as success. This result covers native concurrent deletion only.
Packaged concurrency, interruption/recovery, historical restore, growing-data
performance and complete endpoint-family acceptance remain separate gates.
