# Maintained packaged message image gate

`TESTS.adapters.go.message_image_build` builds the existing `BE_GO/Dockerfile`
for the current full Git HEAD. It is a parent-only entry point: run it only in a
provisioned process authorized to use Docker. Importing it starts no processes.
It requires the production Python version declared in `BE/Dockerfile` (currently
3.13.16) and rejects optimized Python before creating output.

The builder requires a fresh output directory and an exact 40-character lowercase
revision equal to checkout HEAD. All tracked `BE_GO` files must be clean, including
staged changes. Their actual bytes and executable modes must match committed
blobs even when Git's stat cache or assume-unchanged flags hide modifications.
Untracked files, including local `.DS_Store`, never enter the build context.
Tracked symlinks, submodules and `.DS_Store` are rejected. No repository Git writes occur.
Every provenance Git invocation (HEAD, status, tree, blob export and exit rechecks)
uses command-local `--no-replace-objects` and disables optional index locks.
Replacement refs cannot supply a different tree under the original revision;
no Git configuration is changed.

The frozen `source/BE_GO` is exported from `git ls-tree` and `git cat-file --batch`.
Every tracked context input is retained, including assets, notices, testdata,
`go.sum`, third-party modules and any tracked Docker ignore file. Blob object
identities are checked before writing files. This avoids archive export-ignore or
export-subst attributes silently changing the build context. The source manifest
uses the existing packaged runner's `go_source_hashes` helper.

Docker builds this frozen context with `CODE_REVISION=<full HEAD>` and a private
`--iidfile`, without a mutable tag. The resulting immutable `sha256:` image must
inspect as Linux amd64/arm64 with the existing nonroot user, server entrypoint and
no implicit volumes. A uniquely named and labeled stopped container, created
with networking disabled, supplies the actual `/app/CODE_REVISION` and
`/app/server` bytes. The revision file must match exactly. Server copying uses
Docker's uncompressed file tar on stdout, preserving original numeric owner, group
and mode for inspection; the applicable execution permission for runtime user
10001:10001 must be set. Only one regular `server` member is copied manually;
archive links and alternate paths are rejected, and archive extraction is never
used. The copied executable must be ELF64 little endian ET_EXEC or ET_DYN for the
expected machine, with a nonempty bounded program-header table, file-backed
segments inside the file and valid LOAD file/memory ranges/alignment. Its entry
point must correspond to file-backed bytes of an executable LOAD segment.
The native ELF64 header-entry size and Linux 64 KiB program-table limit apply;
fixed Go header offsets/counts, base addresses, sections and OSABI are not assumed.
BSS, PIE and additional NOTE or other segment types are supported. The SHA-256 is
computed from the validated copied bytes. These package checks establish build provenance;
the existing live runner independently checks that digest against the running
image and performs application acceptance.

Each external command has a deadline (60 seconds normally, 30 minutes for the
build). The builder has an overall 2,100-second deadline and a separate
180-second cleanup grace. Tool output goes to private files rather than the job
log. A command timeout or interruption sends TERM to the owned process group,
waits up to 10 seconds, then uses KILL only if the group remains. It reaps the
leader and verifies group absence, waiting at most a further 20 seconds across
reap/absence checks. A surviving or uninspectable group makes cleanup unverified
and rejects the build. A command that exits leaving group descendants also fails.
Signal delivery is briefly deferred across Popen ownership assignment and group
cleanup so an interrupt cannot lose the process handle; child signal masks are
unchanged.

The shared `message_image_execution.ExecutionDeadline` installs POSIX main-thread
SIGINT, SIGTERM and SIGALRM handlers for each invocation, restoring prior handlers
and cancelling its timer on exit. The first cancellation during business execution
records a sticky failure, starts cleanup grace and raises once to unwind existing
finally blocks. Later
INT/TERM events cannot interrupt cleanup or extend its budget. The builder
explicitly enters cleanup mode even on normal completion, so a first INT/TERM
arriving there records failure while allowing that cleanup step to finish.
Grace expiry records failed/unverified cleanup where possible, writes a fixed
diagnostic and exits 124. It does not claim resources were removed.

The packaged job calls `run_packaged(argv, deadline=1500, cleanup_grace=180)`
in process with the unchanged existing runner arguments. Its finally blocks can
run on deadline/SIGINT/SIGTERM; there is no subprocess.run timeout that kills the
runner before unwinding. Even if runner code catches cancellation and returns
success, the wrapper forces a failed receipt and nonzero exit. Existing cleanup
failures remain failures. The wrapper adds `execution` interruption metadata on
failure; it does not manufacture successful cleanup evidence. The CLI equivalent
is `python -m TESTS.adapters.go.message_image_execution --deadline 1500 --cleanup-grace 180 -- <existing runner arguments>`.

The runner calls `message_image_execution.begin_cleanup()` at its terminal
fixture/resource teardown and outer private-file/image teardown boundaries.
This handoff is inert without an active controller, preserving standalone runner
behavior. It retains the execution deadline until the first cancellation, including
SIGALRM; that event records failure and starts cleanup grace without raising inside
an owned removal operation. Later handoffs and INT/TERM signals cannot restart
grace. Its SIGALRM expiry still escalates. Service contexts that exit between
workloads do not declare terminal cleanup, since business execution resumes.

These cooperative handlers require a POSIX main thread and exclusive ownership
of its real-time timer; an existing timer is refused. Builder API/CLI options
`deadline`/`--deadline` and `cleanup_grace`/`--cleanup-grace` can shorten the defaults;
finite positive deadlines are capped at 3,600 seconds and grace at 300 seconds.

An external SIGKILL, host loss, GitHub's eventual hard cancellation or a blocked
native call can prevent Python finalization and even failure-receipt writes.
Grace escalation is explicitly unverified. Errors in individual removal operations
still fail cleanup verification; independent cleanup checks and parent cancellation
replay remain required.
Stopping the Docker CLI cannot guarantee cancellation of daemon-side build work;
the parent owns that infrastructure and any remaining build cache. No application
container is started by the builder. No fixture services, dependencies, registry
publication or native fallback are provided by this entry point.

Helper cleanup resolves the exact unique name, checks container ID, image ID,
owner label and stopped state, then removes only that container ID. It verifies
absence with a successful Docker list operation, so inspect permission errors
cannot masquerade as removal. Cleanup also runs after an uncertain create
failure/timeout. Foreign ownership, replacement, failed removal or failed
absence queries reject the build; foreign containers are left for parent review.
The output image is retained for acceptance. Private logs, blob transport and
copied package files are removed before success is possible. Frozen source and
tracked checkout bytes/modes/HEAD are checked again after helper cleanup.

The output interface is:

| Path | Consumer |
| --- | --- |
| `results.json` | Redacted build receipt; the only uploadable builder artifact |
| `source/BE_GO/` | Frozen complete context; local input to the packaged runner |

The successful receipt contains `success: true`, `source_stable: true`,
`source_revision`, `source_sha256` (paths relative to `source`, prefixed with
`BE_GO/`), immutable `image_id`, and actual `server_sha256`, directly compatible
with `message_read_scopes_image.verify_build_receipt`. Additional fields are
`packaged_revision`, `tracked_context_exact`, `cleanup_verified`,
`private_directory_removed`, original numeric `server_mode`/`server_uid`/`server_gid`,
optional interruption `execution` metadata, fixed-code `errors`/`cleanup_errors` and a bounded
scope description. No local absolute paths, raw command errors, logs, executable
bytes or fixture credentials enter the receipt. A failed attempt returns nonzero
and keeps a fresh failed receipt when output reservation succeeded. A stale
output directory is refused without rewriting its existing receipt. Preflight
interpreter/argument failures create no receipt.

Provisioned parent commands, from the repository root and with two new output
directories (the worker does not run these Docker commands):

```sh
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m TESTS.adapters.go.message_image_build \
  --expected-revision "$(git --no-replace-objects rev-parse HEAD)" \
  --output "$NEW_BUILD_DIRECTORY"
```

Read `image_id` from the successful receipt and pass the original file unchanged:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m TESTS.adapters.go.message_image_execution \
  --deadline 1500 --cleanup-grace 180 -- \
  --image "$IMMUTABLE_IMAGE_ID_FROM_RECEIPT" \
  --expected-revision "$(git --no-replace-objects rev-parse HEAD)" \
  --build-receipt "$NEW_BUILD_DIRECTORY/results.json" \
  --go-source "$NEW_BUILD_DIRECTORY/source" \
  --postgres postgres:16-alpine --redis redis:7-alpine \
  --output "$NEW_ACCEPTANCE_DIRECTORY"
```

The existing runner additionally requires its frozen context manifest to match
the executing checkout's complete `BE_GO` file manifest. Use a clean acceptance
checkout without untracked context files; the builder excludes local metadata
but does not change the runner's strict source rules.

`.github/workflows/go-message-reads.yml` retains every existing native, query and
browser gate. A separate `message-image` Linux job gets its own 90-minute platform budget,
selects the exact production Python, runs all three offline control suites, pulls
PostgreSQL/Redis fixtures and Caddy from the shipped UI Dockerfile, builds for
`git --no-replace-objects rev-parse HEAD`, and invokes the actual packaged runner with the immutable
image ID, original receipt and frozen context. Default checkout behavior retains
the PR merge SHA where applicable. Permissions remain `contents: read` and
checkout credentials are not persisted. Neither build nor acceptance may fail
open. Artifact uploads name only the two redacted `results.json` files, including
on failure; no source directories, logs, binaries or fixtures are uploaded.
The parent owns integration, branch protection and hosted validation.

Offline controls (production interpreter, no Docker or network):

```sh
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest -v TESTS.adapters.go.test_message_image_build
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest -v TESTS.adapters.go.test_message_image_execution
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest -v TESTS.adapters.go.test_message_read_scopes_image
```

Synthetic receipts, ELF/tar fixtures and deterministic signal/process mocks
demonstrate rejection/shape behavior only. The isolated replacement-ref fixture
uses authored temporary objects/refs and read-only Git subprocesses; real
repository refs, objects, index and configuration are unchanged. Real process
signals, descendants, active fixtures and escalation require parent OS checks.
This gate is bounded packaged message authorization and warm-session acceptance;
it does not establish manual-required packaging, installed activation/release
acceptance, full message-family parity or complete route verification. Actual
Docker and hosted runs remain required parent validation.
