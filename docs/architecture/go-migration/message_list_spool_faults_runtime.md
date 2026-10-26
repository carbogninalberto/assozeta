# Packaged message LIST bounded fault driver

`TESTS/adapters/go/message_list_spool_faults_runtime.py` combines the unchanged
phase1 packaged workload with the unchanged `run_faults(proof, base, token,
association)` helper. This worker implements and tests orchestration offline.
Actual packaged execution and integration belong to the parent.

## Parent invocation

Use the exact production Python specified in `BE/Dockerfile`, with the existing
production dependencies, provisioned local Unix Docker daemon and immutable
packaged image/build receipt. From the repository root:

```sh
PRODUCTION_PYTHON -m TESTS.adapters.go.message_list_spool_faults_runtime \
  --image sha256:IMAGE_ID_64_LOWERCASE_HEX \
  --expected-revision REVISION_40_LOWERCASE_HEX \
  --build-receipt BUILD_RECEIPT_JSON \
  --docker-socket LOCAL_UNIX_SOCKET \
  --output FRESH_OUTPUT_DIRECTORY \
  --deadline 1500 --cleanup-grace 180
```

These are placeholders, not runnable credentials or published runtime artifacts.
`--postgres` and `--redis` retain phase1 defaults. The local endpoint helper strips
ambient Docker contexts, remote hosts, TLS and proxy overrides. An explicit socket
is checked locally; omitting it retains the default local endpoint behavior.
The driver neither builds nor pulls the application image. Imports are inert and
stdlib only. Production Python and enabled assertions are checked before services.

The output directory must not exist, even if empty. It is reserved with mode 0700;
existing directories, receipts and symlink targets are never adopted. Source
directories are rejected. The fresh output contains:

* `phase1/results.json`: phase1's original, unmodified receipt.
* `faults.json`: the separate closed combined receipt, mode 0600, at most 32 KiB.

A failure receipt is written before interpreter/build/source/service checks.
Public failures use fixed codes; arbitrary exception messages, paths and secrets
are excluded. Invalid CLI arguments fail with a fixed startup diagnostic. No
receipt is possible when arguments cannot identify a valid fresh output or when
that output cannot be written. Hard kills or storage failure can also prevent an
updated receipt; the initial receipt remains unaccepted. Phase1 owns deadlines,
signals and cleanup grace during its run. The same deadline arguments bound the
original lists and additional faults within that lifetime; this driver adds no
competing signal owner. Source hashing and final pure verification occur outside
that service execution timer.

Exit status is **2** for accepted bounded evidence, **1** for failure. Only
`--help` returns 0. `success` and `complete_acceptance` always remain false.

## Execution and evidence binding

The driver temporarily replaces only the in-process `phase1.run_lists` pointer.
Its extension calls the original lists first, then `run_faults`, inside the same
`spool_server` context with the same proof, token and association. It checks the
fixture image, revision, opaque owner, phase1 source and build bindings. Source and
build bytes must remain unchanged across fault execution. `finally` restores the
original pointer on success, exception and interruption. This is a single-threaded
parent orchestration entry point; concurrent use of the same module is unsupported.
Production handlers and acceptance functions are never replaced.

The source snapshot combines phase1's full application/Go/fixture/Python digest
with explicit hashes for the phase1, fault and driver modules/tests/docs and local
endpoint module/test. Required missing files and symlinks are rejected. This adds
driver and fault documentation binding that phase1's source digest does not cover.
No source manifest content, fixture credentials or private diagnostics are copied
into public evidence; the explicit manifest contains only fixed repo names/hashes.

`bounded_acceptance(report, *, phase1_bytes, build_bytes, manifest,
current_source)` is a pure independent gate. Every argument is required for
acceptance; omitted arguments return false. Pass the **original file bytes**, a
fresh `phase1.go_source_hashes()` result and fresh `source_binding()` result:

```python
from pathlib import Path
from TESTS.adapters.go import message_list_spool_image as phase1
from TESTS.adapters.go import message_list_spool_faults_runtime as runtime

report = phase1.decode_json(Path('FRESH_OUTPUT_DIRECTORY/faults.json').read_bytes())
accepted = runtime.bounded_acceptance(
    report,
    phase1_bytes=Path('FRESH_OUTPUT_DIRECTORY/phase1/results.json').read_bytes(),
    build_bytes=phase1.read_build('BUILD_RECEIPT_JSON'),
    manifest=phase1.go_source_hashes(),
    current_source=runtime.source_binding(),
)
```

The gate requires the exact closed format-1 combined schema, immutable image and
revision, unchanged before/after/live source bindings, the original phase1 gate
against build bytes and live Go manifest, and all phase1 cleanup checks. Its
phase1 receipt link hashes original bytes, including whitespace; build linkage
also hashes original bytes. `faults_sha256` hashes canonical ASCII JSON with
sorted keys, compact separators and no NaN. Nested faults must pass the existing
strict validator. Both recovery LIST dictionaries must equal every original
phase1 LIST after removing only its label. Workload provenance must match phase1's
owner, image, revision, source and build. `final_cleanup_verified` is accepted only
alongside the original phase1 gate, which checks every cleanup boolean/status.

The combined receipt has exactly `format`, `scope`, `success`,
`complete_acceptance`, `bounded_faults_accepted`, `phase1_accepted`, `incomplete`,
`image`, `revision`, `build_receipt_sha256`, `phase1_receipt_sha256`, `source_before`,
`source_after`, `source_stable`, `workload`, `faults`, `faults_sha256`,
`final_cleanup_verified`, `failure`. Source bindings have exactly `source_sha256`
and `files`; `files` has exactly the names in `SOURCE_FILES`. Workload has exactly
`image`, `revision`, `owner`, `source`, `build_receipt_sha256`. Fault evidence uses
the unchanged schema documented in `message_list_spool_faults.md`. In failure
receipts incomplete observations are null/false and `failure` is a fixed code.
Revalidation is integrity/provenance checking for trusted execution receipts,
not a cryptographic attestation against a malicious receipt author.

## Explicit remaining work and validation

Overall acceptance remains incomplete for database in-flight cancellation,
ENOSPC, spool write/read failures, and new storage failure during final copy.
The nested helper's original `remaining` list is preserved verbatim; its final
parent-binding item is discharged only by this driver's separate acceptance gate.
Post-header abandonment and create-permission denial retain their narrow meaning.

The parent's earlier source-frozen phase2r1 receipt, at
`quality-reports/go-migration/message-list-spool-faults-image-r1/faults.json` in the
parent checkout, records an already executed prototype. It is reference evidence
only. Its schema and source bindings do not satisfy this driver's gate and it
must not be relabeled as acceptance for this driver. Parent real execution on
the final integrated sources remains required.

Offline checks:

```sh
python3 -m unittest TESTS.test_message_list_spool_faults_runtime
python3 -m unittest TESTS.test_message_list_spool_faults_runtime TESTS.test_message_list_spool_faults TESTS.test_message_list_spool_image TESTS.test_local_docker_endpoint
```

The new tests were added first and initially failed to import the missing driver.
Controls reuse existing offline fixture/HTTP and phase1 schema helpers; synthetic
positive dictionaries are never packaged execution evidence. They do not replace
either acceptance validator. The focused command passed 22 tests; the combined
command passed 98 tests with one existing endpoint test skipped because the sandbox
denies binding a temporary Unix socket (mocked stat controls passed). Worker
limitations and command results are recorded
in ignored `quality-reports/fault-driver-progress.md`. No services, network,
Docker execution, commits or publication are part of this worker's validation.

## Parent execution checkpoint

The reproducible driver passed `message-list-spool-runtime-image-r1` against
the original `07148adf3b01c5865bf3fc4e43b5997314d3de4b` packaged image
`sha256:3e6aba2f05cc6e76e471db34c1bd942cc22ebeb67c98652b1d69fff0a4fa9605`.
Combined receipt SHA-256 is
`a17d3f0e573d2ef45cdda9252a0ee287f2f38d137ea67b2b3dc88b3d59cc9555`;
nested phase-one receipt SHA-256 is
`38b82733c1ae9d75454c9a9012770cdee5d3da2fd645e01b456c879a5ff4ffb3`.
The parent independently reran the bounded acceptance predicate with original
build bytes, nested receipt bytes, the exact Go manifest and live frozen source
binding. All final cleanup checks passed. All 98 combined offline controls
passed in the parent environment without skips.

This checkpoint covers the large complete LIST, two concurrent disk spools,
post-header response abandonment, creation-permission denial and full recovery.
It leaves database-inflight cancellation and the other named disk faults open;
`complete_acceptance` remains false. The receipt retains its exact source files
and hashes. This later checkpoint text is not relabeled as a tested source input.
