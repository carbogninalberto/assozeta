# Packaged saved-message restore controls

The existing packaged mutation acceptance runner now requires all four frozen
`message_mutation_restore.CASE_NAMES` controls, in addition to every existing
119-case workload on Django, packaged Go, Caddy v1 and Caddy v2. Missing helper
or probe dependencies fail the gate; there is no skip flag or native substitute.
Actual execution and final committed-source/image validation remain parent work.

`message_restore_image.run_controls` executes immediately after direct Go's
119 cases, inside the same owned packaged Go server lifetime and before Caddy
and the final executing-server observation. Each call receives one new existing
`message_mutations.fixture_context`, the connected Django default database, raw
Redis DB1, measured native-Go TCP peer address and canonical PortableAssertions.
It calls the frozen `run` with `Bounds(io=5, probe=5)`, retaining all other helper
bounds and all canonical expectation validation. Five-second I/O matches the
existing explicitly bounded raw Redis client. No fixture expectations change.

The lease controls require the actual owner row to block the exact Go backend
transaction while an in-container exclusive lock probe reports contention at
least twice, bracketed by positive acquisition/release before work and after
complete HTTP/effects. Running controls insert a real running restore receipt,
verify canonical CREATE/DELETE denial and full-state/cache invariance, then
remove that receipt and verify the original state/cache. The integration
preserves every existing schema, replay, misroute, process, provenance, cleanup
and strict expected source revision/build/image assertion.

## Probe and process binding

The required parent-frozen probe interface is:

```python
make_exclusive_probe(*, commands, container, expected_image, identity_guard)
# returns exclusive_probe(*, timeout) -> bool
```

The callback is pinned to the immutable actual Go container ID and requested
image. It must use the image's default UID 10001 and Linux `/usr/bin/flock` on
the existing `/operations/lifecycle.lock`; host Darwin flock is not evidence.
The parent owns real protocol/image validation. Read the frozen probe document
for its script, classification, timeout and cleanup limitations.

`make_identity_guard` performs one explicit, remaining-budget `docker inspect`
per call using existing builder Commands. It derives the same actual process
identity as `container_liveness_guard`, compares it to the executing-server
wrapper and retains `_linux_lock_identity` validation of owner, image, UID,
restart count, configured lock path, RW bind source and host path/inode. Later
calls inspect the immutable ID and require the entire tuple to remain equal.
There is no call to the old default-90-second inspect inside these callbacks,
no fabricated Popen, broad Commands change or global monkeypatch. Deadline
checks reject late successful observations. Commands retains its existing
process-group cleanup grace; it cannot interrupt an unresponsive Docker daemon
or prove remote exec cleanup merely by reaping the local client.

## Failure and fixture teardown

The failure handler is inside fixture_context. Helper failure, malformed result,
invalid probe history, interrupted work, uncertain command cleanup or guard
drift first enters terminal cleanup and stops the actual immutable owned Go
container. Independent inspection must prove the same owned container is stopped
with PID zero before the fixture resets DB/cache/sequences. A failed stop tries
KILL and independently rechecks. Subsequent fixture/server cleanup cannot make
uncertain exec cleanup verified again.

Immediately after pinning the container identity, before opening any restore
control request, the runner records the immutable ID in a provisional public
receipt with unsuccessful/unverified flags. The outer cleanup-grace escalation
therefore retains `recovery.container_id` even when it expires during helper
cleanup or Docker shutdown. Normal completion replaces this provisional receipt.
A child-process control proves expiry exits 124 with the ID and without fixture
reset. The provisional write never copies private in-memory evidence and refuses
to replace an already successful receipt.

If Docker cannot establish quiescence, the runner marks command cleanup
unverified and exits 124 immediately, before automatic fixture reset. This is a
fail-closed last resort: it replaces this invocation's initial public receipt
with only unsuccessful/unverified flags, the fixed
`mutation-image.restore-quiescence-unverified` error and
`recovery.container_id` (the exact owned immutable ID for parent recovery).
It never serializes the private in-memory report. Receipt or diagnostic write
failure still hard-exits; the initial receipt cannot become successful. No
pending HTTP request is permitted to race a reset. The
outer execution cleanup-grace hard-exit remains active. Host death or an
unresponsive daemon cannot establish successful cleanup and needs parent
resource recovery. Successful helpers complete and settle work themselves,
then each fixture resets before the next control begins.

## Required public receipt

`runtime.restore_controls` contains exactly four ordered, distinct `results`
for `create-lease`, `delete-lease`, `create-running`, `delete-running`.
Its process and binary digests, measured peer receipt and baseline must match
the actual direct-Go workload. The independently validated container/lock tuple
is retained only as `identity_sha256`.

Each row requires the exact frozen helper result shape (`models=128`,
`exact_effects`, `full_redis`, backend/transport/fixture ownership and the specific
lease or running flags), a fully verified existing fixture cleanup receipt and
full `state`, `cache`, `sequences` restoration. Lease backend PIDs are hashed
before publication. Probe evidence requires idle `before=True`, at least two
blocked observations for leases (zero for running), and `after=True` following
completion. Missing, duplicate, extra, malformed or false results, mock
transports, incomplete cleanup or provenance drift fail independent receipt
validation. Only production calls to the frozen helper and actual probe produce
these rows; offline mocks are test controls, never runtime acceptance.

Tokens, sessions, raw DB/cache, SQL and probe diagnostics stay inside the
existing private diagnostics boundary. The exact integration/helper/probe
Python files, tests and docs enter the runtime source graph and must match
tracked Git blobs at the strict expected revision. The parent must integrate
and commit these dependencies before the full build/gate; untracked copies
cannot satisfy acceptance. No whole-family parity, real restore execution,
media replacement, deployment or release acceptance is claimed.

Offline TDD and handoff hashes/results are recorded in
`quality-reports/worker-progress.md`. Parent owns actual Docker validation.
