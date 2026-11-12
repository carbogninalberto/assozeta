# Bounded saved-message restore acceptance

`TESTS/adapters/go/message_mutation_restore.py` adds four inert actual-HTTP
controls for the parent-owned **packaged Go** service. The existing 119 authored
cases cover queued admission and a preheld exclusive lock; they do not establish
lease retention during an actual blocked mutation or running-row admission.
This worker has executed offline controls only. Actual packaged-Go execution,
container probe validation and integration remain parent work.

## Explicit parent interface

```python
run(*, case, apps, database, raw, fixture, base_url, process_guard,
    client_ip, portable, exclusive_probe, bounds=Bounds())
```

`case` is exactly one of `CASE_NAMES`: `create-lease`, `delete-lease`,
`create-running`, `delete-running`. **One fresh existing fixture_context per
call** is required. Do not run these against a fixture consumed by the 119-case
matrix or lifecycle helper. The helper checks the supplied baseline, never
rewrites fixture expectations, and never seeds or repairs the fixture.

`exclusive_probe(*, timeout: float) -> bool` is a required parent callback:

- Execute actual `/usr/bin/flock` **inside the exact owned Go container**, on
  `/operations/lifecycle.lock`, using nonblocking exclusive acquisition. The
  service must use this same file as `DATA_RESTORE_LOCK_PATH`. Parent has
  independently verified that its image contains `/usr/bin/flock`.
- Return the exact bool `True` only after acquisition **and release**. Return
  `False` only for the explicitly designated contention exit code. Missing
  executable/file/volume, exec failure, unexpected status, timeout, malformed
  output and interrupted execution must raise. Do not map all nonzero exits to
  `False`. The parent validates its actual script and exit codes.
- Apply the supplied timeout to the whole exec, including cleanup/reaping. Do
  not leave a lock-owning exec behind. The helper checks elapsed time too; that
  check cannot interrupt an unbounded callback. Parent callbacks must be bounded.
- Tie the callback to the same immutable container identity as `process_guard`.
  A host-side Docker bind mount lock is not evidence of the container lock.

Each control first requires an idle `True` probe. Lease controls require at
least two `False` probes while the exact transaction remains blocked, then a
`True` probe after complete HTTP success and exact committed effects. An
always-False callback fails before any HTTP work. A wrong unlocked inode fails
the blocked probes. No container command, subprocess, host flock or fallback is
implemented in this helper.

`process_guard()` returns a stable dictionary containing integer `pid > 0`,
`running=True`, and a non-null immutable `instance` identity. The parent must
bind both the endpoint and probe to that packaged process/container; headers
are not process attribution. Container PID 1 is supported. No `Popen` is passed
or fabricated. The helper sends no signal and never waits for, kills, or
consumes the parent's process. The guard itself must be bounded.

`base_url` is the parent's explicit numeric loopback HTTP endpoint and port,
with no path prefix, credentials, query or fragment. The parent publishes its
owned container port there. `client_ip` is the independently measured address
seen by the actual service for this transport, including container forwarding;
the helper never infers loopback or uses a forwarded header as its expectation.

`database` is the already connected Django default `DatabaseWrapper`,
PostgreSQL, autocommit, outside atomic blocks, with explicit name/user/host in
an exclusively owned migrated `assozeta_contract_*` database. `apps` is the
configured ready Django registry. The portable private-database assertion also
enforces the existing production Python policy (3.13.16). Blocker, observer and
default connections must have three distinct PostgreSQL backend PIDs. Their
catalog visibility must expose the Go backend query and tuple locks. All DB
work stays in the calling thread.

`raw` is the parent's existing Redis DB1 client. Positive finite socket and
connect timeouts, and a blocking pool's wait timeout if applicable, must not
exceed `bounds.io`. No timeout retry, retry-on-error list or configured retry
object is accepted. Full cache snapshots use the existing atomic portable
TYPE/DUMP/absolute-expiry oracle and fixture contrasts, without key exclusions.

Bind the existing portable functions directly:

```python
from django.db import connections
from TESTS.adapters.go.message_mutation_restore import (
    CASE_NAMES, Bounds, PortableAssertions, run,
)
from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import historical_read_cache as cache

portable = PortableAssertions.from_modules(
    contract=contract, scopes=scopes, shared=shared, cache=cache, adapter=mutations,
)
# Parent already owns readiness, image, services, callbacks and independent IP.
# private_dirs maps each name to its own new parent-supplied private directory.
receipts = []
for name in CASE_NAMES:
    cleanup = {}
    with mutations.fixture_context(
        apps, private_dirs[name], raw, cleanup_receipt=cleanup,
    ) as fixture:
        try:
            receipts.append(run(
                case=name, apps=apps, database=connections['default'], raw=raw,
                fixture=fixture, base_url=packaged_base_url,
                process_guard=process_guard, client_ip=measured_client_ip,
                portable=portable, exclusive_probe=exclusive_probe, bounds=Bounds(),
            ))
        except BaseException:
            # Parent-owned failure policy must finish/quiesce pending work
            # BEFORE fixture_context.__exit__ resets the database/cache.
            parent_ensure_service_quiescent()
            raise
    assert cleanup['verified'] is True
```

Both `validate_runtime(runtime)` against the independent canonical source and
`validate_runtime(runtime, authored)` run before helper setup. Jointly editing
runtime/authored/digest cannot redefine expected behavior. Portable assertions
are passed explicitly; there is no environment discovery or dependency fallback.

## Lease controls

CREATE selects `message-mutations-create-email`; DELETE selects
`message-mutations-delete-email`, retaining its authored two deliveries and
historical CREATE/UPDATE audits and indexes. Only the request correlation name
changes to the restore control name. The helper copies cases, leaving the
parent's source and runtime manifest untouched.

A separate copied Django connection takes `FOR UPDATE` on the one fixture
owner row in `bakney_user` and records blocker PID, owner ID, relation OID and
exact `ctid`. The helper sends a complete authenticated real HTTP/1.1 request.
A distinct observer must prove the actual service backend has:

- Exactly `SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE`.
- Active state, a Lock wait, non-null transaction/backend start identities,
  and the exact blocker PID in `pg_blocking_pids`.
- A granted tuple lock for the exact recorded relation, page and tuple offset.

The existing lifecycle pure predicates reject absent/ambiguous proof and a
replacement transaction/backend. All 128 managed models and full Redis must
remain unchanged while blocked. Every exclusive contention probe is bracketed
by proof of the **same transaction and exact tuple**. At least two observations
span `bounds.survival` (default 250 ms). Releasing the shared lease before the
owner-row wait therefore produces `True` during a required `False` probe and
fails acceptance. Merely waiting a fixed amount of time never supplies proof.

Only after these checks does the helper roll back its blocker, observe that
exact service transaction end, and consume a complete expected HTTP response.
The existing portable `assert_effect` checks the exact full raw projection:
Message fields, delivery cascade and preservation, complete audit actor/diff/
correlation/client IP, index resolution and historical rows, and all unrelated
fields across all 128 models. Full Redis comparison runs even if model effects
fail. Generated values are validated by the existing oracle; its normalized
return projection is discarded. A rollback/no-op, partial audit or index,
foreign write, wrong measured IP or cache write fails. Exclusive acquisition
must then succeed with the same parent guard still alive.

## Running admission controls

The helper selects the canonical authenticated owner pending-admission request
and its independently authored **503** JSON expectation. It does not call the
existing queued-only `admission_control`. It inserts one real `DataRestore`
row with `state='running'`, requires running membership in `DataRestore.ACTIVE`,
verifies it is persisted with null completion, and refuses any preexisting
active restore. This matches the inspected Go query's queued/running states and
Django's `DataRestore.ACTIVE` gate without changing production code.

The complete expected denial includes the canonical application error string,
`Retry-After: 10`, JSON media type, English language header, no Allow header,
cookie or authentication challenge. All 128 model fields (including the running
receipt) and Redis must remain exactly unchanged during the request. The helper
deletes only its own UUID receipt in `finally` and compares complete state/cache
again to the pre-insertion snapshot. Parent fixture_context restores rows,
audit/index history, sequences, cache and private files after every case.

## Bounds, cleanup and limits

Default limits are request 20 s, owner-wait 5 s, transaction completion/snapshot
10 s, I/O/query/probe 2 s, survival 250 ms and poll 20 ms. Bounds must be positive,
finite and at most 20 s. Acceptance checks deadlines before and after blocking
observations; success after a deadline fails. Separate connections have finite
PostgreSQL connect, statement and lock bounds. The helper temporarily bounds
the borrowed default connection's statement/lock timeouts and restores both.
The 20 s acceptance budget starts before HTTP connect/send and covers the
subsequent observations/effects/probe; cleanup has its own finite budget and
cannot convert a late request into success.

Responses reuse the lifecycle deadline reader and bounded complete-frame parser:
exact Content-Length, complete chunk/CRLF/zero-chunk/trailer framing, or EOF for
close-delimited responses. Short EOF, conflicting lengths, ambiguous framing,
unsupported transfer encoding and bodies/framing exceeding 64 KiB fail. Success
Allow headers are checked per CREATE/DELETE route. A fully consumed but incorrect
response is not parsed again during cleanup.

On errors or interruption the blocker releases before HTTP settlement; failed
rollback closes its exact connection immediately. Cleanup attempts request
drain, known transaction-end observation, socket/connection close and timeout
restoration, retaining original and cleanup errors together. The running row
remains present until request cleanup has been attempted. An unresponsive service,
incomplete response, broken DB connection or unbounded parent callback cannot
be certified clean by this helper; failures propagate. Parent must quiesce its
owned service before fixture reset if settlement fails. The example's parent
failure hook is deliberately inside fixture_context so an unsettled request
cannot race its automatic reset. That hook belongs to the parent's service
ownership policy; it is not another helper input or a helper subprocess.
No SIGTERM or process consumption is hidden in cleanup.

Run serially with no unrelated requests/jobs/restore execution. This is Go-only
blocked-transaction proof: Django has different SQL and requires a separately
reviewed proof before claiming equivalent blocked-mutation coverage. This work
does not execute a real restore, test media replacement, or validate the entire
restore implementation. Runtime defects must be reported separately, not
compensated for by weakening authored expectations.

## Checks and parent acceptance steps

Offline command (no service, DB, socket or network launch):

```sh
python3 -B -m unittest TESTS.test_message_mutation_restore \
  TESTS.test_message_mutation_lifecycle.WaitControls \
  TESTS.test_message_mutation_lifecycle.SequenceControls \
  TESTS.test_message_mutation_lifecycle.ResourceControls \
  TESTS.test_message_mutation_lifecycle.ExactEffectControls
```

At handoff: **53 tests passed** (29 new and 24 existing pure lifecycle controls),
Python 3.14.6. New tests were authored first; the initial red run failed importing
the absent helper. A later deliberate fault injection bypassed `_probe`: all
three early-release/always-False/never-release controls failed as expected.
Running-denial fault controls reject a write to each of all 128 model projections
and full-cache changes. No runtime acceptance is claimed by these offline mocks.

An exploratory broader run also selected existing lifecycle FixtureControls
and the portable actual-HTTP entry point. It failed their Python 3.13.16 and
explicit-runtime-input gates (five failure records). Those gates were preserved;
the HTTP entry point is excluded from the offline command above. Parent uses
the existing production Python environment for actual execution.

Parent must validate its real probe's success/contention/error exit handling,
same container/inode and timeout cleanup, then run all four calls above on the
actual packaged Go image using fresh fixtures and measured client IP. Preserve
all four receipts and all four verified fixture cleanup receipts. Rerun the
existing 119-case matrix in its own fresh context and required parent integration
checks. Record image/source hashes, actual bounds and runtime results. Actual
image/probe/HTTP/DB/Redis/integration checks remain unexecuted by this worker.

## Inspected dependency hashes

Worker base: `4ae64e41c46743e3546604e34f2f7628241725e9`. These are file-byte SHA256
values, not the authored contract's canonical JSON digest. Compare parent
dependencies before applying this handoff; changed owner SQL or private helper
behavior needs review. New owned-source hashes are in `quality-reports/worker-progress.md`.

| Dependency | SHA256 |
| --- | --- |
| `TESTS/adapters/go/message_mutation_lifecycle.py` | `588996bb168fe1eb935f7468bb2b94e4527aba5f601a87a599dfb488de974138` |
| `TESTS/adapters/django/message_mutations.py` | `0948bfaadfa97d7d58ec8fb98f6138057cc300b3b3d72eeccb121fee79416fa8` |
| `TESTS/contracts/test_message_mutations.py` | `b1d4477f65586b5c256c4bd163e58b9f20a73cae9fb1f2398c2607520267dd51` |
| `TESTS/fixtures/message_mutations.json` | `211b6fc7071a92bb4ea3f6281580803d9b083fa95cf912156323dca4222e183d` |
| `TESTS/adapters/django/message_read_scopes.py` | `8c2b3d7a0f54a69757548290140bacdd94724d6f23cddbec745745608037fe0f` |
| `TESTS/adapters/django/message_reads.py` | `77be4808886958133d4a907be0b569e31a5d5bb4a9941027d17874cc1056f58e` |
| `TESTS/adapters/django/historical_read_cache.py` | `045a230bf76e4c39ccb7b6495de362c896b846532397c289823b53f10f908bea` |
| `TESTS/test_message_mutation_lifecycle.py` | `27ef3d0d9eb2ab0924c9c8d7700398749410f98c2fea2b1204f935473b365b8c` |
| `BE/instance/models.py` | `035d845caebfb35c831f89a038fab7d8f789ed313628246b7f357f7757a9a297` |
| `BE/instance/restore/locking.py` | `ba50d6a20f90180b803acec3c3b051bd7a79757057e55b2c21c246911efe26cf` |
| `BE_GO/internal/middleware/restore_admission.go` | `f301dd5509708421de73f4d82e5995453d639b1b6b3ba167811e0f34b6c2238c` |
| `BE_GO/internal/service/restore_admission.go` | `1fb738db5232a720866744a76d4de9a9d8a24ff2537e9b926274c78e223bb0f5` |
| `BE_GO/internal/repository/restore_admission.go` | `5ea57940bb847d5070cef20b415a00eb82ddd325530c858ee61d664601454601` |
| `BE_GO/internal/repository/message_mutations.go` | `5bec8189475c664ade49e10ffd64405250f7cb0c5816e840b11f7052d520ab4c` |
