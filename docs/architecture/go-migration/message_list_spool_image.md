# Packaged message LIST disk spool — executable phase one

This parent-only adapter exercises the production `/app/server` and real PostgreSQL,
Redis and filesystem storage. It never builds, pulls, installs or falls back to a
native binary or mocked handler. Imports and offline controls use only stdlib.

**Full acceptance is incomplete.** This version implements large complete LIST
and overlapping requests. It does not implement request cancellation or storage
failure acceptance. `success` and `complete_acceptance` always remain false;
`incomplete` names both missing gates. The parent must independently review and
execute this harness. No packaged execution was performed during implementation.

Owned files:

- `TESTS/adapters/go/message_list_spool_image.py`
- `TESTS/test_message_list_spool_image.py`
- This document, plus ignored `quality-reports/spool-diagnostic-progress.md` for local progress.

## Offline RED/GREEN controls

From the repository root, without services, network or dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest TESTS.test_message_list_spool_image -v
```

GREEN controls validate an incrementally generated complete array over 64 MiB,
UTF-8 fragmentation, exact build/schema controls, disk and descriptor observations.
RED controls reject truncation, pagination, duplicates, wrong order/content/tenant,
extra fields, missing observations, mutable/stale/dirty provenance, ownership and
security regressions, partial cleanup and unsafe receipt strings. Additional
dependency `VOLUME` declarations must fail before service creation; unexpected
actual mounts and failed inspections must keep cleanup RED. Controls cover both
PostgreSQL and Redis create/inspection failures, including daemon creation before
client failure, independent Redis/DB close failures, partial Django setup and
settings override failures. The unchanged inherited service class is exercised
offline with an in-memory Docker boundary. The in-memory
positive evidence control is never saved as a runtime success receipt. These are
controls of the harness, not evidence that the production image passes.
Diagnostic controls also cover network creation and dependency readiness, real
subprocess-wrapper failures with secret canaries, preflight resources never
attempted, and preservation of the first failure through private teardown.

## Parent prerequisites and command

Provision these independently; the adapter performs no provisioning downloads:

1. The exact production Python version from `BE/Dockerfile` (currently 3.13.16),
   already installed with the repository's Django/Redis/crypto dependencies and
   tools used by the existing private environment helpers, including OpenSSL.
   Invoke in a fresh process; preinitialized Django is rejected before resources
   or output are created.
2. A local Docker daemon at the default socket, or an explicitly supplied existing
   local Unix socket through `--docker-socket`, accessible from this process.
   Loopback published PostgreSQL, Redis and API ports must be reachable from the
   parent. Ambient Docker contexts, credentials, host overrides and client plugins
   are excluded by a fresh private `DOCKER_CONFIG` and sanitized environment.
   Explicit socket paths are validated locally; TCP/SSH endpoints are rejected.
   No daemon configuration or context is changed, and socket paths are not recorded
   in the public evidence.
3. A locally available immutable Go image `sha256:<64 lowercase hex>` and the
   original successful receipt from `TESTS.adapters.go.message_image_build`, with
   the exact closed build schema. All `BE_GO` context file hashes must equal the
   copied candidate, including non-Go assets. The binary digest, full revision,
   packaged revision, stable source, exact tracked context and verified builder
   cleanup must agree. The adapter rechecks these at request admission and exit.
4. Locally present PostgreSQL 16 and Redis 7 images (the defaults below or explicit
   local refs). Every container uses `--pull never`; dependency image IDs are
   resolved and pinned before use. Declared volumes must be absent or confined to
   PostgreSQL's `/var/lib/postgresql/data` and Redis's `/data`, respectively; these
   destinations are explicitly mounted as tmpfs. Additional declarations are
   rejected before network/container creation. They are private fixtures, not
   existing shared services.
5. Capacity for full migrations, synthetic DB rows and three complete response
   transfers (each approximately 72 MiB plus JSON overhead). Two disk spools coexist.
   The daemon's local volumes must be on an observable supported disk filesystem
   (`ext2/3/4`, `xfs`, `btrfs`, `zfs`). Missing `/proc` visibility or unsupported
   filesystems fail closed. No arbitrary memory or timing performance target is set.

The parent supplies its independently built local candidate and original exact
build receipt. A receipt for a different source revision or Go context does
**not** establish candidate provenance. These harness repairs do not change the
Go context or binary; the adapter still compares every current Go input with the
original build manifest and records a fresh digest of the complete harness and
runtime inputs. No build or commit occurs in this worker.

Example after provisioning, from this repository root (replace all placeholders):

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/python3.13 -B -m TESTS.adapters.go.message_list_spool_image \
  --image sha256:REPLACE_WITH_64_HEX_DIGEST \
  --expected-revision REPLACE_WITH_FULL_40_HEX_REVISION \
  --build-receipt /absolute/path/to/original-build/results.json \
  --postgres postgres:16-alpine \
  --redis redis:7-alpine \
  --deadline 1500 --cleanup-grace 180 \
  --output quality-reports/go-migration/message-list-spool-parent-FRESH
```

Output must not already exist. Exit **2** means `phase1_accepted: true` while the
full objective remains incomplete. Exit **1** means phase one failed, was
interrupted, or could not start. There is no full-acceptance exit 0 in this version.
Do not convert exit 2 into an all-accepted CI result. Inspect `results.json` and
independently assess its source/image bindings and observed evidence.

The independent phase-one gate requires the original build bytes and live source
inputs; `phase1_acceptance(report)` by itself returns false. To recheck evidence
offline after the parent execution, replace the two paths below:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import json
from pathlib import Path
from TESTS.adapters.go import message_list_spool_image as gate
report = json.loads(Path('/absolute/path/to/parent/results.json').read_bytes())
accepted = gate.phase1_acceptance(
    report,
    build_bytes=gate.read_build('/absolute/path/to/original-build/results.json'),
    manifest=gate.go_source_hashes(),
    current_source=gate.source_digest(),
)
print(json.dumps({'phase1_accepted': accepted, 'success': False}))
raise SystemExit(2 if accepted else 1)
PY
```

This validates bindings and evidence structure. It cannot authenticate statements
forged by a different program; the parent must trust/review the actual producer
and supervise the execution. There is no mock runtime mode or imported-receipt
shortcut that launches or synthesizes a passing production run.

## What runs

The adapter reuses the explicitly private service/network lifetime from
`payment_category_image.ImageFixture`, actual full migrations/private environment
from `message_reads_runtime`, and existing synthetic auth fixtures from
`message_reads`. The local adapter also constrains dependency storage and checks
actual mounts. The category
server uses `/tmp` 16 MiB and lacks this spool mount, so its server method cannot
prove the deployed LIST behavior. No shared helper or source test is modified.

A labeled private named volume serves as an explicitly removable counterpart of
the anonymous disk mount in `selfhost/compose.yml`. Docker copies directory metadata
from the unchanged image on first mount. No root/chown helper or runtime directory
repair occurs. Runtime checks require UID/GID 10001 and mode 0700, a read-only root,
all capabilities dropped, no-new-privileges, `/app/server` as the actual PID 1
executable, no injected executable mounts, the production spool environment and
an unchanged `/tmp:rw,noexec,nosuid,size=64m`. Mountinfo checks the real read-only
root, tmpfs size/options and distinct disk mount. The volume must have local driver
and no options; tmpfs/ramfs/bind replacements fail. This is filesystem/mount evidence,
not proof of a physical disk, storage quota or durability across daemon failures.

Full migration history must report exactly 547 applied migrations and zero
remaining; this closed expectation intentionally requires review when schema grows.
Existing helpers create synthetic identities, signed JWTs and foreign fixtures.
The adapter replaces only its own owner's private fixture messages with 1,152
64-KiB bodies, alternating EMAIL/INSIDE_APP, varied null/escaped subjects and unique
timestamps. A disallowed own SMS and existing foreign messages remain excluded.
A DB cursor checks the actual fixture content/count/order against independently
authored expectations before HTTP execution. No complete result population is
loaded in the Python verifier, and fixture creation uses small batches.

The first GET is held after headers, before body consumption. Because production
prepares the complete disk spool before HTTP 200, the adapter can inspect its
unlinked descriptor through `/proc/1/fd` and `fdinfo`: size over 64 MiB, UID/GID
10001, mode 0600, and mount ID exactly matching the private disk volume. A directory
listing alone is insufficient because production unlinks the spool immediately.
A complete response is then consumed in 64-KiB reads, one bounded JSON object at a
time. Every field, association, count and position must equal independently
constructed expected rows. Canonical content hashes and wire hashes are retained;
raw bodies, identifiers and credentials are not published. Decoder limits cap a
row at 256 Ki characters and each response at 128 MiB.

Two more GETs are held simultaneously. Two distinct deleted inodes on the same
verified disk mount must coexist before either response is consumed. Two bounded
reader threads then consume both complete arrays. All three responses must have
identical wire and content hashes, and each received byte count must equal the
observed staged file size. Descriptors and directory entries must be absent after
both workloads. This proves a fixed two-request overlap, not a general load test.
The same container must remain live without restart for the entire workload and
stop normally before DB fixture cleanup.

Three actual `/proc/1/status` VmRSS samples are retained: the single staged
response, the interval after its descriptor closes, and the two-response overlap.
They measure process resident pages, including Go heap and other resident pages;
they do not isolate Go heap. Unmapped filesystem page cache is not process RSS.
Container totals (which can include page cache) and Go heap are explicitly null.
These snapshots are neither peaks nor evidence of asymptotic heap bounds. There
is no pprof/metrics endpoint or instrumentation added to production.

## Fail-closed ownership, cleanup and receipt rules

A fresh RED receipt is reserved before runtime. Assertions must be enabled.
Duplicate JSON keys, nonfinite values and unknown build/receipt fields fail closed.
The full Go build context and transitive BE/TESTS fixture/runtime sources, compose,
example environment and owned document are hashed; source is checked immediately
before requests and at exit, and the original build-receipt bytes must stay stable.
Outputs are forbidden inside BE, BE_GO or TESTS. A snapshot comparison cannot detect
a malicious transient source edit restored between observations; the parent owns
source stability and trust in the independently generated build receipt.

Only randomly named, per-invocation labeled containers, network and volume are
created or removed. Cleanup checks exact names, labels and any recorded immutable
container IDs before deletion and verifies absence through successful listings.
Dependency image declarations are checked before creation and their immutable
IDs are passed to the inherited helper. After startup and again before removal,
dependency mount inspection allows only the corresponding tmpfs data destination;
binds, inherited volumes, extra API mounts and anonymous/named data volumes fail.
The server allows only its labeled spool volume and `/tmp` tmpfs. Cleanup checks
also apply to recorded attempts that failed before returning a container ID,
including the inherited Redis creation path. Docker `rm --force` is used only
after storage and container ownership checks pass; no `rm --volumes` or deletion
by an unowned volume name is allowed. Unexpected mounts or unavailable inspection
retain the container for parent recovery and leave both container and volume
cleanup false, even if the separately owned spool volume was removed. Keeping
that association prevents an unverified volume from becoming an invisible orphan.
Other independently owned cleanup attempts still run; an attached network can
remain RED until the parent resolves the retained container.
The opaque `owner` is persisted before the first daemon mutation; it is the exact
`org.assozeta.category-image-proof` label value inherited from the service helper.
After a hard kill the parent can inspect that specific owner's resources, compare
exact names/labels and recover only those resources. Owner IDs contain no fixture
data or credentials.
No Docker prune, shared service, arbitrary database or global cache operation is
used. Cache restoration is confined to the owned private Redis DB, using existing
helpers. Fixture rows are removed and original migrated state restored; Redis/DB
connections, settings override and environment are closed/restored.
Resource initialization is inside the cleanup `try`; the lazy DB connection
registry is acquired before Django app setup so a partially failing setup still
attempts DB closure. Redis and DB closure have independent attempts, and the
connection flag is true only if every acquired handle closes successfully.
Settings disable is independently attempted even after a failing override enable.
Private JWT
manifests, env files and service diagnostics live under one mode-0700 temporary
root and disappear before phase acceptance. Raw Docker/service logs are never
collected into public output. Public errors are fixed codes; receipt fields are
closed, typed, bounded and allowlisted. Public JSON is capped at 32 KiB, mode 0600,
and replaced atomically; stdout contains only a small fixed summary.

The existing `ExecutionDeadline` supplies SIGINT/TERM/deadline handling and bounded
cleanup grace. Subprocess operations and HTTP reads have explicit limits. SIGKILL,
daemon failure or cleanup-grace expiry can prevent cleanup; such runs are RED and
must not claim verified cleanup. Hard interruption may leave only the initial RED
receipt. Parent inspection/recovery must target the exact invocation's labeled
resources; never use blanket cleanup.

## Safe failure diagnostics

The parent r1 failed before `runtime_executed`, with only
`spool.execution-failed`, a recorded owner, stable source, and verified command,
environment and private-directory cleanup. All other cleanup booleans were false.
Its private log was deleted. Those observations do not identify a root cause or
establish that any service started. Source inspection identifies dependency image
inspection and declared-volume checks as the first startup gates, followed by
packaged-image metadata, pinned dependency reinspection, network creation,
PostgreSQL/Redis creation, readiness, port inspection and actual mount validation.
Docker executable availability or daemon access are plausible early blockers;
the old receipt cannot distinguish them from the other gates.

The closed receipt now requires two additional fields. `failure` is null or an
exact `{stage, category}` object containing only the enums in `FAILURE_STAGES` and
`FAILURE_CATEGORIES`. It retains the first observed failure; cleanup or source
recheck failures do not replace it. Startup stages distinguish each dependency's
image/volume checks, packaged-image checks, network creation, each dependency's
creation/inspection/readiness, and mount validation. Broader stages identify
interpreter/build/source preflight, private environment setup, Django setup,
migration, fixtures/cache, packaged server and LIST execution. Categories
distinguish missing executable, permission denial, command timeout, nonzero exit,
output limits, leftover descendants, malformed metadata, assertion/check failure,
interruption and unexpected errors. They describe the observed boundary and
exception type; `command-failed` cannot establish why the daemon refused a command.
Exception messages, arguments, command lines, arbitrary paths and raw logs are
never copied into either diagnostic field. The existing generic `errors` codes,
public size limit and private-log deletion remain in force.

`cleanup_status` has exactly the same keys as `cleanup`. `not-attempted` means no
corresponding acquisition/mutation was attempted; its boolean remains false.
`verified` means the cleanup postcondition was checked successfully and requires
boolean true; it does not assert that a deletion command was necessary. `failed`
means cleanup of an attempted/acquired resource remains unverified and requires
boolean false. On hard cleanup-grace expiry, every status becomes `unverified`
with boolean false: bookkeeping or verification may have been interrupted, so
neither acquisition nor cleanup completion is inferred. A dependency preflight
failure leaves runtime fixture/cache,
connections/settings and unattempted containers/network/spool volume false with
`not-attempted`, instead of claiming successful destruction. An uncertain network
create is recorded before the daemon call and still gets ownership-checked
recovery. A retained container with unverified storage leaves volume status
`failed`, even when no owned spool volume was attempted. Cleanup of independently
owned resources continues. These statuses do not relax any ownership or mount
checks, and `not-attempted` never satisfies phase-one acceptance.

Publication and phase-one validation both enforce these closed fields and their
boolean consistency. Phase one additionally requires null failure and every
cleanup status `verified`, alongside all existing runtime, provenance, LIST and
storage evidence. Older receipts missing the new fields fail this validator;
their missing diagnostics cannot be reconstructed after the private log is gone.
The next supervised parent run must produce fresh evidence. This worker used
offline controls only and did not reproduce or fix an established service fault.

## Remaining acceptance and evidence limits

Phase two must exercise actual request cancellation and storage failure against
the packaged server, including descriptor/cursor release, lack of partial success
and successful subsequent requests. This version does not run those cases and
cannot authorize them through text in a receipt, copied logs or a CLI flag. It
also does not claim ENOSPC/EIO behavior, precommit cancellation, higher concurrency,
Django/Caddy parity, migration/restore lifecycle parity, installed selfhost compose
acceptance, peak memory, or a benchmark. Existing source tests remain read-only
inputs and do not substitute for these missing packaged observations.

## Parent execution checkpoint

Parent run `message-list-spool-image-r2` accepted phase one against image
`sha256:3e6aba2f05cc6e76e471db34c1bd942cc22ebeb67c98652b1d69fff0a4fa9605`,
built from `07148adf3b01c5865bf3fc4e43b5997314d3de4b`. The original build receipt
and all Go build inputs were rechecked. The run's harness/runtime source digest
is `68cf1ed7ec40f446f6621c92f4833e98ac791e94844f671c12b8c1621a705a82`;
receipt SHA-256 is `4136618c7b001b87859fe624e0bf58e40141ced4d65c3ee516ffe5fcbbfcef46`.
This evidence retains its frozen inputs; this later documentation addition is
not relabeled as part of that execution.

All three responses contained the independently expected 1,152 rows and
75,806,816 bytes, with identical wire/content hashes. Two live unlinked spools
were observed concurrently on an ext4 local volume. The server ran as UID10001
with a read-only root and a separate 64 MiB temporary filesystem. All fixture,
cache, process, container, volume, network and environment cleanup checks passed.
The parent independently re-executed the phase-one acceptance predicate with
the original build bytes and frozen source inputs. Combined offline controls
passed 54 tests, including actual temporary Unix-socket validation.

The earlier r1 failed before runtime because the private Docker client
configuration did not reach the local daemon. A read-only parent probe confirmed
that explicit selection of the existing local Unix socket restored access; no
daemon configuration was changed. r1 remains failed. Phase-one exit2 and
`complete_acceptance: false` remain intentional: cancellation/storage faults,
peak-memory and full-family verification are not established by this checkpoint.
