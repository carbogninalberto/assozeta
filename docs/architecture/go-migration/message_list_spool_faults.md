# Packaged message LIST: phase2 fault helpers

`TESTS/adapters/go/message_list_spool_faults.py` implements two narrowly scoped
observations against the parent's existing packaged server. This worker ran only
offline controls. Actual packaged execution remains a parent acceptance gate.
The phase1 module, its acceptance function/report, shared fixtures and production
handler are unchanged.

## Parent interface

```python
from TESTS.adapters.go.message_list_spool_faults import run_faults, validate_faults

# Inside the existing proof.spool_server(...) context, after run_lists(...):
faults = run_faults(proof, base, token, association)
assert validate_faults(faults)
# Parent binds faults to its own source/build receipt and final cleanup evidence.
```

`proof` must be the already provisioned phase1 `SpoolFixture`. Its `exec(*args)`
checks fixture ownership before each operation and uses the configured runtime
user; `volume_info()` verifies the private volume's ownership. The existing
phase1 package/mount/security checks must have passed. The service, volume, rows,
token and association remain exclusively owned by the parent. No concurrent LIST
request or other writer may use this fixture during `run_faults`.

Imports use stdlib only and perform no I/O. Phase1 loads lazily during execution
to reuse `ROUTE`, `held_list`, `observe_spools`, `wait_empty` and `verify_list`.
The helper starts/stops no service, constructs no fixture, builds no image and
writes no report file. It accepts only a loopback fixture URL with port 1–65535.

On success, `run_faults(proof, base, token, association)` returns the closed
dictionary below. On any unexpected observation, HTTP/fixture error or
interruption it raises `RuntimeError('faults.execution-failed')`. An unsuccessful
mode restoration raises `RuntimeError('faults.restoration-failed')`, including
when the request also failed. These public errors contain no original exception
context/cause. There is no partial success receipt. A parent catching either
error must mark this phase unsuccessful and still run its normal fixture cleanup.
Restoration failure requires parent recovery of the exclusively owned fixture;
the helper never escalates privileges or changes an unidentified directory.

## Behaviors observed

**Post-header response abandonment.** With the existing large seeded population,
the helper receives HTTP 200 headers without consuming response body bytes through
the response API. It observes exactly one live, unlinked, private spool using
phase1's descriptor/mount/size/ownership checks. Exiting `held_list` deliberately
closes the response and connection. The helper verifies `response.isclosed()` and
the retained socket's `fileno() == -1`, then requires descriptor/directory cleanup
within 15 seconds. A fresh complete LIST must pass phase1's independently authored
row/order/content hash verifier. Header parsing may already buffer body bytes;
`body_bytes_read: 0` describes application consumption, not bytes on the wire.

In `BE_GO/internal/handler/message_reads.go`, `WriteList`, the full spool read
check, both seeks and the final context check precede HTTP 200. This exercise
therefore covers abandonment during response delivery after staging. It provides
no evidence of cancellation during database work.

**Spool creation permission denial.** Immediately before mutation, a fixed-path
probe checks exec UID/GID and all four PID1 UID/GID values are 10001, PID1 executes
`/app/server`, and the spool is a real directory owned by 10001:10001 with mode
0700. Directory device/inode and PID1 start time are retained privately. The
helper rechecks the volume and identity across the cases.

As UID10001, a guarded command changes only
`/var/cache/assozeta-message-list` from 0700 to 0500. Keeping read/search while
removing write makes `os.CreateTemp` fail under the package's existing dropped
capabilities. There is no root helper, fallback path, disk filling, handler mock,
restart, global mutation or ownership change. The GET must return HTTP 500 before
any successful status, `text/html; charset=utf-8`, and exactly the existing generic
`restError` HTML body. Statuses such as 200, 401 or 403 are rejected. Private paths,
rows and provider diagnostics cannot satisfy the exact body comparison.

The mutation attempt is inside `try/finally`, including the case where chmod
succeeds but its command receipt fails. Restoration verifies UID, ownership,
device/inode and allowable mode (0500 or already 0700), restores the original 0700,
then verifies process/directory identity. Descriptor cleanup is attempted even
if restoration fails. Once restored and empty, another complete independent LIST
must match the first recovery's rows, bytes and hashes. Final UID/GID, directory
identity/mode and PID1 start time must still match. Parent package checks bind the
container ID, running state, restart count and process identity separately.

## Bounds and safe evidence

Phase1 `held_list` retains its 45-second socket timeout for admission/headers.
Recovery bodies have a 90-second absolute deadline checked between `read1`
fragments, with each socket read capped at five seconds. Using `read1` avoids
`read(n)` accumulating indefinitely while a peer supplies body fragments. Phase1
still bounds the response to 128 MiB, each row to 256 KiB, and verifies 1152 rows.

The permission request uses a 15-second socket timeout for connect/headers and
a five-second body deadline. It retains at most 513 bytes to reject an error body
over the 512-byte cap; no error body is returned or logged. HTTP header parsing
uses the stdlib limits. These are controls for the trusted packaged server,
not an arbitrary hostile HTTP peer. The fixture's existing exec runner retains
its finite 90-second command limit. `wait_empty` has its existing 15-second poll
deadline; elapsed observation time, including exec overhead, must also be at most
15 seconds to accept the evidence. Cleanup is attempted on error paths too.

The receipt contains constants, strict booleans/integers, one finite nonnegative
duration, and fixed-length hexadecimal hashes. It contains no URL, token,
association ID, raw body, proc output, resource name, inode, private path or error
diagnostic. Its closed shape fits below 8 KiB when JSON encoded. The validator is
pure, imports no phase1 code and rejects missing/extra keys, booleans masquerading
as integers, nonfinite/out-of-bound durations, false completion observations,
invalid modes/statuses, wrong hashes and inconsistent recovery results.
Validation establishes the observation schema, not execution provenance.

## Exact evidence schema (format 1)

Every dictionary has exactly the keys shown. No field is optional. All literal
strings have exact `str` type; numeric fields exclude booleans.

| Top-level key | Required value |
| --- | --- |
| `format` | integer `1` |
| `scope` | `"packaged-message-list-spool-phase2"` |
| `faults_accepted` | boolean `true` |
| `complete_acceptance` | boolean `false` |
| `runtime` | dictionary below |
| `cancellation` | dictionary below |
| `storage` | dictionary below |
| `remaining` | exact ordered list shown below |

`runtime` has exactly `uid: 10001`, `gid: 10001`, `same_process: true`,
`exclusive_spool_verified: true`, `ownership_preserved: true`.

| `cancellation` key | Required value |
| --- | --- |
| `kind` | `"post-header-response-abandonment"` |
| `status` | integer `200` |
| `body_bytes_read` | integer `0` |
| `live_unlinked_spools` | integer `1` |
| `staged_bytes` | integer greater than 67108864, at most 134217728 |
| `response_closed`, `socket_closed`, `empty_after` | each boolean `true` |
| `cleanup_seconds` | finite int/float in `[0, 15]` |
| `recovery` | complete LIST dictionary below |

| `storage` key | Required value |
| --- | --- |
| `kind` | `"spool-create-permission-denied"` |
| `original_mode`, `restored_mode` | integer `448` (0700) |
| `denied_mode` | integer `320` (0500) |
| `status` | integer `500` |
| `error_contract` | `"generic-html-500"` |
| `error_bytes` | integer `145` (`len(GENERIC_ERROR)`) |
| `error_sha256` | `"fd62a53afdd49595ccd111b6ac06466a1690e7c8da4aeb0e884b5e0f4e937624"` |
| `no_private_data`, `empty_after_failure`, `restored` | each boolean `true` |
| `recovery` | complete LIST dictionary below |

Both `recovery` dictionaries have exactly `rows`, `bytes`, `wire_sha256`,
`content_sha256`, `expected_sha256`. `rows` is integer 1152; `bytes` is an integer
greater than 67108864 and at most 134217728. Each hash is a 64-character lowercase
hexadecimal string. `content_sha256 == expected_sha256`. Both recovery dictionaries
must be identical, and their `bytes` must equal cancellation's `staged_bytes`.
`verify_list` computes the expected rows/content independently of the response.

The exact ordered `remaining` list is:

```json
[
  "database-in-flight-cancellation",
  "spool-enospc",
  "spool-write-failure",
  "spool-read-failure",
  "new-storage-failure-during-final-copy",
  "parent-source-build-and-final-cleanup-binding"
]
```

Permission denial at creation does not cover ENOSPC, spool write/read errors or
new storage faults during the final HTTP copy. Phase2 leaves these explicit gaps
and overall acceptance false. Parent execution/source/build/cleanup binding and
phase1 integration remain separate required work.

## Offline validation and handoff

TDD red: `python3 -m unittest TESTS.test_message_list_spool_faults` initially
exited 1 because the new implementation module did not yet exist. Green controls
replace fixture exec and HTTP boundaries only. They exercise response/socket
cleanup, independent verifier calls, permission restoration after command/request/
body/interruption failures, failed restoration, malformed or oversized generic
errors, rejected false evidence, expired deadlines, leaked descriptors, UID/mode
mismatch, process restart and directory replacement. The final focused command
passed 22 controls. No service, network or Docker execution occurs in these tests.
See the ignored
`quality-reports/spool-faults-progress.md` for final command results and handoff.

## Parent packaged checkpoint

Parent run `message-list-spool-faults-image-r1` exercised these helpers against
immutable image `sha256:3e6aba2f05cc6e76e471db34c1bd942cc22ebeb67c98652b1d69fff0a4fa9605`
from `07148adf3b01c5865bf3fc4e43b5997314d3de4b`. Its combined receipt SHA-256 is
`4b6a8929c7f3157f71ef7a8dd65e2f3d99742bcd6d0c44b6ae5ac7add8a6f157`; frozen
harness/runtime source digest is
`4c08406cb686fcdb44f370edf160773d4d71e7354bb3bc4acf110cefe2c1e65c`.
The parent independently revalidated the nested phase-one receipt, original
build bytes, source digest, driver hash, fault validator and complete cleanup.
This historical checkpoint is not acceptance for later runner or source changes.

The client abandoned an unconsumed 75,806,816-byte response while its unlinked
spool was live; the descriptor disappeared in approximately 0.437 seconds.
Removing owner write permission from the owned spool directory produced the
exact generic 145-byte HTTP 500 response. Mode 0700 was restored. After each case,
the same process returned all 1,152 independently checked rows and 75,806,816 bytes.
All private fixture/cache/connections/containers/volume/network cleanup checks
passed. Parent offline validation passed 76 tests across phase one, endpoint
selection and fault controls; independent helper review passed 22 tests and
found no actionable issue.

These observations cover post-header abandonment and creation-permission denial.
Database-inflight cancellation, ENOSPC, read/write failures and new storage
failure during final delivery remain unverified. Full acceptance remains false.
