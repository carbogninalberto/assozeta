# Packaged message LIST query cancellation runner

`TESTS/adapters/go/message_list_query_cancellation_runtime.py` runs the unchanged
phase1 LIST workload and then the unchanged `run_query_cancellation` helper in
one fixture lifetime. It produces separate bounded evidence and preserves the
original phase1 report and acceptance gate. This implementation was verified
with offline controls only; actual packaged execution belongs to the parent.

Use the production Python required by `BE/Dockerfile`, with its existing
production dependencies, an already provisioned local Unix Docker daemon and
an immutable application image and original build receipt:

```sh
PRODUCTION_PYTHON -m TESTS.adapters.go.message_list_query_cancellation_runtime \
  --image sha256:IMAGE_ID_64_LOWERCASE_HEX \
  --expected-revision REVISION_40_LOWERCASE_HEX \
  --build-receipt BUILD_RECEIPT_JSON \
  --docker-socket LOCAL_UNIX_SOCKET \
  --output FRESH_OUTPUT_DIRECTORY \
  --deadline 1500 --cleanup-grace 180
```

All values above are placeholders. The runner neither builds nor pulls the
application image. `--postgres` and `--redis` retain phase1 defaults. Phase1's
local endpoint policy and service ownership checks remain in force. Imports
are inert and stdlib only; enabled assertions, finite positive deadlines,
production Python, the original build receipt and live sources are checked
before invoking phase1 or starting services.

The output directory must be new, including when an existing directory is
empty. It is reserved with mode `0700`. Existing directories and leaf symlinks
are rejected, as are output paths within `BE_GO`, `BE`, `TESTS` or `docs`. It
contains `phase1/results.json`, published by phase1 unchanged, and the separate
`query-cancellation.json` with mode `0600` and a 32 KiB maximum. A closed RED
receipt is published before interpreter/build/source checks. Errors use fixed
codes and never include exception messages, credentials, SQL, PIDs, HTTP data
or private paths. Invalid CLI arguments produce a fixed startup diagnostic.
Invalid/unwritable output cannot receive a receipt; hard termination or storage
failure may leave only the initial RED receipt.

The original phase1 execution deadline covers both workloads and teardown uses
its existing cleanup grace. This runner installs no competing signal handler.
`--deadline` is at most 3600 seconds and `--cleanup-grace` at most 300 seconds.
Preflight hashing and final independent verification occur outside phase1's
service execution timer. The query helper's own polling, connection, request
and recovery limits remain unchanged.

Exit status is **2** when this bounded query evidence is accepted, **1** on
failure, and **0** for help only. `success` and `complete_acceptance` always
remain false. This run leaves post-header response abandonment, creation
permission denial, ENOSPC, spool write/read failure and new storage failure
during final copy outside its scope. Evidence from a separate fault run is not
silently incorporated.

## Execution binding and independent verification

The runner temporarily wraps only the in-process `phase1.run_lists` pointer.
The wrapper calls the original workload first, then the query helper with the
same proof, base endpoint, token and association. Both execute within phase1's
existing `spool_server` context. It checks image, revision, opaque fixture owner,
phase1 source and exact build bytes before the original workload, before the
helper and after the helper. At most one callback is allowed. `finally` restores
the original pointer on success, exception and interruption. This entry point
requires exclusive use of that module in a single-threaded parent; concurrent
invocations within the same Python interpreter are unsupported.

`source_binding()` combines phase1's full application/Go/fixture/Python digest
with explicit hashes for phase1, query helper, driver, their tests and docs,
and the local endpoint helper/test. All fixed required files must exist without
symlink components. It also hashes the exact `messageReadsListSQL` constant
using the same text interpretation as the helper. Public source bindings contain
only fixed repository-relative filenames and SHA-256 hashes. No source bodies
or machine paths are copied into receipts.

After phase1 completes owned cleanup, the runner reads its original receipt and
requires equality with phase1's returned report. It verifies source/build
stability and calls phase1's original acceptance gate. The separate pure gate
has no fallback to prior reports or live files:

```python
from pathlib import Path
from TESTS.adapters.go import message_list_spool_image as phase1
from TESTS.adapters.go import message_list_query_cancellation_runtime as runtime

report = phase1.decode_json(
    Path('FRESH_OUTPUT_DIRECTORY/query-cancellation.json').read_bytes())
accepted = runtime.bounded_acceptance(
    report,
    phase1_bytes=Path('FRESH_OUTPUT_DIRECTORY/phase1/results.json').read_bytes(),
    build_bytes=phase1.read_build('BUILD_RECEIPT_JSON'),
    manifest=phase1.go_source_hashes(),
    current_source=runtime.source_binding(),
)
```

Supply original build/phase1 bytes and fresh source/Go manifests; every argument
is required for acceptance. The gate requires the exact closed report schema,
exact raw-byte build and phase1 hashes, unchanged before/after/live sources,
phase1 acceptance with all cleanup booleans/statuses, matching owner/image/
revision/build provenance, the helper's strict validator and its canonical JSON
hash. `query_cancellation_sha256` uses sorted compact ASCII JSON without NaN.
The helper's query hash must match the current source binding. Recovery rows,
bytes and wire/content/expected hashes must equal **every** original phase1
LIST after removing only its label. A coherent pair of edited recovery hashes
cannot replace that comparison. Receipt integrity is not cryptographic
attestation against a malicious receipt author or untrusted live-source caller.

The format-1 report has exactly `format`, `scope`, `success`,
`complete_acceptance`, `query_cancellation_accepted`, `phase1_accepted`,
`incomplete`, `image`, `revision`, `build_receipt_sha256`,
`phase1_receipt_sha256`, `source_before`, `source_after`, `source_stable`,
`workload`, `query_cancellation`, `query_cancellation_sha256`,
`final_cleanup_verified`, `failure`. Its scope is
`packaged-message-list-query-cancellation-runtime`. Source objects have exactly
`source_sha256`, `files`, `query_sha256`; `files` has exactly `SOURCE_FILES`.
Workload has exactly `image`, `revision`, `owner`, `source`,
`build_receipt_sha256`. Nested successful query evidence follows the unchanged
schema in `message_list_query_cancellation.md`. Rejected/partial helper evidence
is omitted (`null`) to preserve the closed public schema. Failure codes are
`runtime.incomplete`, `runtime.execution-failed`, `runtime.interrupted`,
`runtime.evidence-rejected`; successful bounded evidence has `failure: null`.

## Offline validation and limitations

TDD RED: `python3 -m unittest TESTS.test_message_list_query_cancellation_runtime`
first failed because the new driver did not exist. A later mutation control
also failed until the runner rechecked sources/build between the original
workload and the query helper. GREEN: the focused command passes 28 tests.
The combined command passes 141 tests with no skips:

```sh
python3 -m unittest \
  TESTS.test_message_list_query_cancellation_runtime \
  TESTS.test_message_list_query_cancellation \
  TESTS.test_message_list_spool_image TESTS.test_local_docker_endpoint \
  TESTS.test_message_list_spool_faults_runtime TESTS.test_message_list_spool_faults
```

Controls cover exact original receipts, every required top-level/helper field,
query attribution, all three recovery comparisons, cleanup failure, wrong
fixture provenance, repeated/missing callbacks, source/build mutation, malformed
helper evidence, failure/interruption restoration, fresh output protection,
production Python/assertion checks, invalid deadlines, inert imports and secret
canaries. Positive dictionaries and doubles are offline controls, never packaged
execution receipts. The earlier parent prototype run is not accepted by this
new source-bound runner and must not be relabeled. Parent-owned integration must
execute these final exact sources and independently revalidate final cleanup.
No services, image execution, shared infrastructure changes, commits or
publication are part of this worker's task.
