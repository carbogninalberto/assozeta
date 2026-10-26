# Opt-in connection observation in owned service runtimes

`TESTS/adapters/django/message_reads_browser_runtime.py` exposes connection
diagnostics through the existing parent-owned service context managers. Both
options are disabled by default. Existing callers retain their application
wrapper, command, liveness guards, counters, artifact checks and return values.
Importing the module does not install an observer or start services.

This wiring supplies diagnostic attribution for the parent's same-message
concurrent HTTP DELETE acceptance. It does not implement acceptance, parse
diagnostic streams, modify fixtures, or change observer/server implementations.
The observer contracts are documented in
`django_request_connection_observation.md` and `query_connection_observation.md`
(provided by the separate observer workers).

## Django ownership

```python
owned_django(report, *, liveness_observer=None, connection_observer=None)
```

The parent constructs the request connection observer and passes that exact
instance. The owner enters `connection_observer.installed()` before constructing
the server or starting its thread. Composition is:

```text
CompleteAsgiObserver(connection_observer.wrap(real_application))
```

`CompleteAsgiObserver` remains the outer wrapper. The yielded value is still
`(complete_asgi_observer, port)`, and the existing liveness callback receives a
guard for that outer wrapper and the actual owned server/thread. The connection
observer is not substituted into either API.

On exit, while installation remains active, the owner requests shutdown, joins
the thread with the existing graceful/forced deadlines, closes listeners, and
requires the outer observer to drain HTTP **and WebSocket** application lifetimes.
Only then does normal exit detach the connection observer. The parent can inspect
the connection observer's final `snapshot()` after the context exits.

```python
# Parent creates a fresh run UUID and observer before entering the owner.
with owned_django(report, connection_observer=connection_observer) as (http, port):
    # Parent performs its real HTTP/catalog workload and takes bounded snapshots.
    ...
final_django = connection_observer.snapshot()
```

Independent listener closes and drain checks are attempted even if another
cleanup step fails. An unjoined thread or failed drain records `cleanup_failed`
and raises; it cannot produce a successful service cleanup report. Detach still
runs on failure so the observer can invalidate incomplete lifetimes and attempt
its own cleanup. A thread that survives forced shutdown cannot be killed by this
owner: the parent must reject the run and prevent fixture reset while it remains
live. No fixture reset is performed by this context manager.

Single application errors/cancellation propagate unchanged. Simultaneous
application and cleanup/detach errors are retained in a `BaseExceptionGroup`,
including cancellation derived directly from `BaseException`. Cleanup errors
are never converted into successful observation. The no-observer path keeps its
existing cleanup behavior and does not add the opt-in drain operation.

## Go diagnostic destinations

```python
owned_go(work, executable, codec, environment, private_log, report, native_build,
         *, liveness_observer=None, query_observation_file=None,
         query_connection_observation_file=None)
```

Supply both destinations or neither. This integration deliberately does not
expose a summary-only mode. Before port reservation, artifact copying or spawn,
the owner requires:

- Distinct resolved destinations strictly within the actual owned work directory.
- Existing parent directories, fresh targets, and no symlink traversal at or
  beneath the owned directory, including dangling links and links followed by `..`.
- No collision with the owner's `message-browser-go` or `image-codec` copies.

Relative destinations are interpreted relative to `work`. Accepted destinations
are passed as resolved absolute paths using exactly these CLI flags:

```text
<copied-server> --query-observation-file <summary-path> --query-connection-observation-file <connection-path>
```

No arbitrary CLI arguments or new environment overrides are exposed. With both
options omitted, the command remains the single copied executable. The return
value remains the port, and executable/codec hashes and native-build binding are
still checked. Popen acquisition remains inside `defer_interruption()`, so a
cooperative cancellation at acquisition cannot lose the child handle.

The owner does **not** precreate, truncate, chmod, read, or finalize either sink.
The server worker must implement the two flags and authoritative exclusive
creation with mode `0600`, reject existing/symlink/aliased sinks, and propagate
initialization/write/sync/close errors. Parent path validation is a preflight
check; it cannot replace exclusive creation or protect against an external
directory mutation after validation. Unknown server errors remain fatal through
the existing readiness/liveness behavior; there is no flag fallback or retry.

Shutdown retains `stop_child`: terminate, bounded wait, then existing process
group kill/reap escalation if necessary. Opt-in cleanup additionally requires a
reaped process with exit status zero before reporting `stopped`; failed diagnostic
finalization and forced termination are rejected even if a footer exists. Stop and binary-integrity errors stay
visible alongside an original exception/cancellation. `stopped` describes service
ownership only. Forced termination, successful process reap, or paths being
accepted says nothing about observer finalization. After shutdown, the parent
must read and verify both final files, reject missing/incomplete/error output,
and require the server's successful graceful drain/finalization evidence.

## Evidence required from the parent

While the blocker is held, bind generated request/query IDs and measured positive
PIDs to independent PostgreSQL backend incarnation, transaction identities and
the expected lock chain. After release, bind IDs to each original HTTP response
header and reconcile all request/query/driver lifetimes, extra HTTP scopes and
unattributed SQL. Stop all producers, then require valid final Django snapshots
and complete final Go streams, without late events, gaps, overflow or errors.
Callback observations alone never establish PostgreSQL arrival, transaction
overlap, same-message DELETE attribution, or acceptance.

## Offline controls and handoff

Run with the prescribed cached production runtime:

```text
/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python -m unittest TESTS.test_message_reads_browser_runtime
```

New controls use mocked servers, threads, sockets, Popen handles and observer
contexts. They verify defaults, wrapper composition, install/wrap/start/yield/
stop/drain/detach ordering, startup/body/callback errors and cancellation,
independent cleanup failures, paired confined fresh paths, aliases/symlinks,
exact flags, child acquisition ownership and binary integrity. They do not use
the sibling workers' edited implementation files or prove live PG/HTTP behavior.

Initial RED: 7 new tests failed with 3 failures and 25 subtest errors because the
keyword APIs were absent. Additional RED controls caught live-child cleanup and
artifact-path collisions (3 tests: 3 failures, 2 errors), then symlink traversal
through `..` (1 failing subtest). The full final result and exact command evidence
are recorded in ignored `quality-reports/worker-progress.md`. Parent integration
still requires independent review, server flag/observer integration, and live
PostgreSQL plus actual concurrent HTTP DELETE verification.
