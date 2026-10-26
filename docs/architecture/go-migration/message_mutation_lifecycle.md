# Actual HTTP message mutation lifecycle acceptance

`TESTS/adapters/go/message_mutation_lifecycle.py` is an inert helper. Importing it
starts nothing. Its `run()` must be called by the parent that owns the migrated
private PostgreSQL database, Redis DB1, configured Django apps, existing
`message_mutations.fixture_context`, native Go server, and that server's actual
`subprocess.Popen`. This correction pass has not run these runtime cases. The
parent's successful initial-helper run is prior-source evidence only.

The fixed sequence is CREATE with a disconnected HTTP client, DELETE with a
disconnected HTTP client, then CREATE during graceful SIGTERM drain. The last
case ends the passed native process. Run this helper last for that process,
inside a fresh/reset fixture context; the ordinary 119-case matrix consumes
DELETE targets and cannot precede it in the same unreset fixture. The parent
fixture context resets rows, audit history, sequences and Redis after this
helper, including after exceptions. The helper does not seed or repair state.

## Parent API and dependencies

```python
from django.db import connections
from TESTS.adapters.go.message_mutation_lifecycle import (
    Bounds, PortableAssertions, run,
)
from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import historical_read_cache as cache

portable = PortableAssertions.from_modules(
    contract=contract, scopes=scopes, shared=shared, cache=cache, adapter=mutations,
)
# Parent creates the fixture and native service; this is only the call inside
# those contexts, after readiness and independent client-address observation.
receipt = run(
    apps=apps,
    database=connections['default'],
    raw=raw,
    fixture=fixture,
    base_url=native_base_url,
    process=owned_native_process,
    process_guard=process_guard,
    client_ip=independently_measured_client_ip,
    portable=portable,
    bounds=Bounds(),
)
```

`run()` has no environment DSN discovery, subprocess launcher, default address,
backend fallback or missing-configuration skip. It requires Python **3.13.16**
with assertions enabled. The connected `database` must be the actual default
Django `DatabaseWrapper`, PostgreSQL, in an `assozeta_contract_*` database with
explicit name, user and host. The portable `require_private_database()` adds
its existing exclusive private-database and autocommit checks. Parent execution
must be serial and exclusive, without workers or unrelated requests.

`raw` must be the parent's raw Redis DB1 client, with positive finite
`socket_timeout` and `socket_connect_timeout` no greater than `bounds.io`
(default two seconds), no configured retry policy, no timeout retries and no
retry-on-error list. The fixture supplies the existing persistent/expiring
contrast keys and `contrasts` object. Redis must support the portable atomic Lua
snapshot, TYPE, DUMP and absolute PEXPIRETIME observation. The parent may need
to construct its existing raw client with these finite timeouts; this worker
does not edit the shared helper or reconfigure an existing client's pool.
If the client uses a blocking connection pool, its pool wait timeout must also
be positive, finite and no greater than `bounds.io`.

`base_url` is the actual native numeric loopback HTTP endpoint with explicit
port and no path prefix. `process` is the exact parent-owned disposable native
`Popen`, never an inferred PID, shell wrapper or process-name match. The parent
must tie this endpoint to that process. `process_guard()` returns the same
stable dictionary while the process lives, including integer `pid`,
`running=True`, and a non-null immutable `instance` identity. It must not use
response headers as process attribution. No guard is required to remain running
after the final operation commits. `client_ip` is an independently observed
numeric client address for this native path; no loopback default or forwarded
header supplies the audit expectation.

The required portable functions are bound directly:

- `contract.validate_runtime(runtime)` (canonical source), then
  `contract.validate_runtime(runtime, authored)`, and `contract.assert_effect(...)`;
- `scopes.snapshot_state(apps)` for all 128 managed models;
- `shared.assert_state_unchanged(...)` and `shared.assert_cache_unchanged(...)`;
- `cache.snapshot_historical_read_cache(raw)` and `mutations.require_private_database()`.

The parent tree already contains these modules. They are absent from this
isolated worker tree. Integrating the three owned new files into the parent
makes the normal module imports above available; no shared source change is
required. The helper accepts those modules explicitly through `portable` and
does not copy them or introduce another backend normalization. `assert_effect`'s
comparison projection is discarded after its exact raw assertions succeed.
Canonical validation happens before case selection or helper setup. The first
validation loads the independent contract's canonical fixture using its existing
default source. The second binds the supplied `authored` fixture to that same
runtime/digest. Jointly changing authored/runtime expectations and recomputing
their digest therefore fails canonical validation; the supplied pair cannot
define its own expectations. No portable binding or public API extension is
required.

## Synchronization and acceptance

Each case snapshots complete managed state and Redis, then opens a separate
copied Django connection and takes `FOR UPDATE` on the fixture's exact owner
row in `bakney_user`. The blocker records its PostgreSQL PID plus the selected
row's user ID, `tableoid` and `ctid`. It locks no other row.

The helper sends a complete real HTTP/1.1 request through a raw socket to the
native service, with the existing fixture's owner token, authored request body
and a distinct lifecycle correlation ID. CREATE reuses
`message-mutations-create-email`; DELETE reuses
`message-mutations-delete-email`, including its actual two deliveries and
historical CREATE/UPDATE audits/indexes.

A distinct observer connection must observe all of the following before a
client disconnect or signal:

- The exact service query
  `SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE`.
- `pg_stat_activity.state = active`, `wait_event_type = Lock`, a non-null
  transaction start and backend start, and `pg_blocking_pids` containing the
  exact blocker PID.
- A granted tuple lock in `pg_locks` for that waiting backend with the exact
  recorded owner relation, page and tuple offset.

Multiple matching waiting backends fail as ambiguous. This binds the evidence
to a service transaction waiting for the one owner row, rather than an arbitrary
query, authentication request, advisory lock or elapsed delay. These catalog
views and query text must be visible to the parent's database role; missing
visibility fails acceptance. The integrated repository inspected read-only
uses precisely this query after lifecycle admission. A future query change
requires reviewing this proof against the new source; it must not be weakened
to an arbitrary table-name substring.

While the owner remains locked, all 128 models and Redis must still be exactly
unchanged. The disconnect cases then shut down and close both directions of the
actual socket. Every subsequent observation during a bounded survival window
(default 250 ms, at least two observations) must show the same backend start,
transaction start, exact owner tuple lock and blocker. The process guard must
remain stable. A sleep without these catalog predicates provides no proof.
The helper checks complete state/cache again before releasing the row lock.

The final case keeps the HTTP socket open, calls
`owned_native_process.send_signal(SIGTERM)` once, then polls until the actual
listener refuses TCP connections with ECONNREFUSED while the exact owned
process remains alive. A timeout, unreachable network or early process exit is
a failure. The same transaction/tuple predicates must continue to hold after
listener shutdown. Only then does the helper release its row lock.

After release, the observer waits for that exact transaction to end. Disconnect
cases verify effects while the process remains alive. For the drain case, the
helper first reads the complete success response and requires a clean process
exit; only then does it capture final managed state and Redis. Writes during
shutdown are included in the final projection. The portable effect assertion
must find exactly one successful Message CREATE
or DELETE, the expected delivery projection, exactly one complete Message audit
and exactly one complete index with expected actor, independently measured
client IP, correlation ID, changes and resolution path. Historical and foreign
rows and every other field across all 128 models must match the exact portable
projection. Full Redis comparison includes all keys, typed serialized values,
absolute expiration and the seeded contrasts; readiness keys have no exclusion.
An ended or rolled-back transaction without its effects cannot pass.

The drain case checks the actual HTTP 200 JSON success document, media and
language headers, Allow, and absence of cookies/authentication challenge. A
Content-Length response must contain exactly its declared, bounded body;
valid JSON followed by EOF before that length fails. A chunked response must
contain complete chunks, their CRLF delimiters, a zero chunk and the terminating
empty trailer line. Close-delimited responses require EOF. Conflicting or
duplicate lengths, ambiguous length/transfer framing and unsupported transfer
encodings fail. These checks precede the bounded process wait with exit status
**0** and the final effect snapshot. SIGTERM termination status, early exit, expiry rollback,
partial audit or a process still running past the exit bound fail acceptance.
The inspected integrated server stops admission and invokes HTTP shutdown with
a 25-second graceful deadline; it expires admitted work only afterward. This
helper covers successful graceful drain, not the separate expiry path.

## Bounds, teardown and evidence

`Bounds` defaults: lock admission wait 5 s, survival 0.25 s, listener stop 5 s,
transaction completion 10 s, clean exit 10 s, socket I/O and SQL statements 2 s,
poll cadence 20 ms. Values must be finite and positive; unsafe survival/drain
configurations fail before execution. Each lock-wait, listener-stop and
completion loop checks its deadline both before another observation and after
the blocking observation, before accepting success. Observations at or beyond
the deadline fail, including a matching lock, absent transaction or connection
refusal. Survival requires at least two matching observations spanning
`survival`, completed strictly before `survival + query` (2.25 s by default).
Each complete model/Redis capture has one `completion` deadline, checked before
and after SQL execution, after model collection and after Redis observation.
Every helper connection has bounded connect,
statement and lock timeouts; the parent's snapshot connection's statement
timeout is saved and restored. HTTP header/body reading uses one absolute I/O
deadline and a 64 KiB document bound. Chunk framing/trailers have a separate
64 KiB aggregate budget and an 8 KiB line limit. Socket reads check the deadline
after receiving bytes or EOF, as well as before blocking; response validation
and the process wait also check elapsed time before accepting success. These
are phase deadlines, not a single total lifecycle deadline or a mechanism to
interrupt an already blocking call. The listener/survival bounds reserve
headroom under the inspected server's 25-second expiry; snapshots and other
phases consume additional time. If expiry occurs, the transaction/effect/exit
requirements still fail closed. Catalog observations, rather than poll cadence,
establish synchronization.

Every case closes its request socket in `finally`, and rolls back its blocker
to release the row lock even on failed assertions. Global teardown attempts both
owned connection closures and restores the parent's statement timeout, retaining
cleanup errors alongside acceptance errors. If SIGTERM was sent, teardown reaps
that exact process; if its bounded wait fails, it kills only that supplied
process and waits again. Failure cleanup killing a process never counts as a
clean-exit pass. Before any signal, the process remains the parent's
responsibility. The parent continues to own its Redis and default DB clients.

Success returns a plain dictionary with `success`, `backend`, `transport`, the
three ordered case receipts, exact owner-row/wait evidence flags, model count,
state/cache checks, process PID/exit status and parent-reset ownership. Failures
raise, including combined acceptance/cleanup errors; there is no partial-success
receipt or swallowed missing-evidence case. No tokens, raw Redis data or private
state are written by the helper.

## Offline checks and remaining runtime gap

The unit suite starts no sockets, services or database. It uses fake controls
only at the unit boundary and synthetic states passed to the actual portable
assertions. Positive CREATE and DELETE controls ensure the negative controls
are meaningful. Rejected controls include missing/wrong/ambiguous waits,
transaction/backend replacement, early exit, nonzero/missing clean exit,
missing effects, incomplete audit/index, historical/foreign row changes, cache
key/type/value/expiration changes, incorrect client IP, and missing model
inventory. Unit controls also check signaling and resource teardown.
Canonical fixture controls reject both jointly edited authored/runtime data
with a recomputed digest and individually edited inputs. Shutdown controls use
the actual portable effects/cache assertions to reject unexpected model and
Redis writes made by `clean_exit()`. Framing controls cover valid fixed-length,
chunked (with and without trailers) and close-delimited responses, premature
EOF, invalid chunk boundaries/termination, ambiguous headers and size limits.
Virtual-clock controls cover on-time success and observations exactly at and
beyond the lock, completion, listener, survival, exit, snapshot and read bounds.

From the isolated checkout root, explicitly supply the parent's contract file
read-only. Set `PARENT_CHECKOUT` to the absolute root of the integrated parent
checkout and `PRODUCTION_PYTHON` to the already installed Python 3.13.16 executable:

```sh
: "${PARENT_CHECKOUT:?Set PARENT_CHECKOUT to the integrated parent checkout root}"
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
MESSAGE_MUTATIONS_PORTABLE_CONTRACT="$PARENT_CHECKOUT/TESTS/contracts/test_message_mutations.py" \
"$PRODUCTION_PYTHON" \
-m unittest TESTS.test_message_mutation_lifecycle
```

After integration, that environment variable is unnecessary because the normal
local portable contract exists. Missing contract dependencies fail; they do not
skip tests. The corrected helper passes 27 offline tests on the required
Python. Its new behavioral assertions fail against the original helper.
Offline green does **not** establish route-level lifecycle
acceptance. The parent must execute this actual-HTTP sequence against its
configured migrated fixture and native process, retain its process/binary and
client-address provenance, and run fixture reset verification afterward. No
runtime database, Docker, network, service, installation, Git write, nested
agent or publication was used by this worker.
The parent previously reported actual full-547-schema execution of all three
HTTP cases, all 128 models, Redis, source/artifact provenance and cleanup for
the initial three files. That remains prior-source evidence. The corrected
helper requires a new parent run, including shutdown-write-sensitive final
effects and fixture-reset verification; the separate expiry path also remains
outside this helper's successful-drain coverage.
