# Packaged-Go saved-message restore lock probe

`TESTS/adapters/go/message_restore_lock_probe.py` supplies an inert factory:

```python
make_exclusive_probe(*, commands, container, expected_image, identity_guard)
# Returns exclusive_probe(*, timeout) -> bool
```

Import and construction perform no I/O. Invocation uses the existing
`message_image_build.Commands` instance and exactly one
`docker exec FULL_CONTAINER_ID /bin/sh -c FIXED_SCRIPT`, as the image's default
user. No service launch, network discovery, tool installation, executable mount,
root override, mutable container name, host flock, or helper relaxation exists.
The implementation and tests are local only; no runtime acceptance is claimed.

## Parent inputs and necessary integration

`container` must be the full 64-character lowercase immutable owned ID, not a
name or abbreviated ID. `expected_image` is required and must be exactly
`sha256:` followed by 64 lowercase hexadecimal characters. The caller must supply
it from the parent's validated build receipt. Tags, repository references,
abbreviations, other algorithms, whitespace and non-string inputs are rejected
at construction without I/O. There is no default or hardcoded supported image:
new source-bound builds include this helper and receive their own digest.

Every identity returned by the guard, before and after exec and on later calls,
must match `expected_image` exactly. An initial mismatch prevents exec; a later
post-exec mismatch invalidates cleanup. The digest pins identity only. Parent
must independently validate the actual image's BusyBox `/usr/bin/flock` behavior
and negative controls described below before using that image. A syntactically
valid digest does not establish compatible error semantics or build provenance.

`commands` is the parent's existing `builder.Commands`, retaining its private
diagnostics, process-session ownership, cancellation, reaping, descendant checks,
and `cleanup_verified` state. The probe does not wrap Popen, replace stop_group,
catch `build.command-failed` as contention, or inspect private raw logs.

`identity_guard(*, timeout)` is a **budget-aware parent callback**, returning the
existing runner's `_linux_lock_identity(proof, guard)` tuple:

```python
(
    {'id': ..., 'image': ..., 'pid': ..., 'started': ..., 'restarts': 0},
    {'pid': ..., 'instance': ..., 'running': True},
    (host_lock_device, host_lock_inode),
)
```

The callback must execute the runner's full actual validation: owned image and
label; default UID `10001:10001`; running PID, start identity, zero restarts;
process guard; exact host regular non-symlink lock; exactly one writable bind of
the owned operations directory at `/operations`; and exactly
`DATA_RESTORE_LOCK_PATH=/operations/lifecycle.lock` in actual container config.
The returned tuple omits UID, mount, ownership label and environment; tuple shape
validation cannot replace those checks. Never supply a fabricated tuple in live
use. Use an already independently verified owned server, with serial probes and
no unrelated operations-directory changes or container execs.

The existing `_linux_lock_identity(proof, guard)` is **not directly budget-aware**.
Its proof inspection and process callback can use their own much larger timeouts.
An unmodified zero-argument lambda around it is rejected; adding `timeout` to a
lambda and ignoring it is also insufficient. Parent must thread the remaining
budget through all actual inspections/process checks in an adapter or future
reviewed runner interface. This worker changes no shared interface or runner.
Use the factory only after that integration is implemented and reviewed.

Every invocation checks identity before and after exec. The first pre-exec tuple
is defensively copied and pinned for the entire returned callable's lifetime.
Container, image, PID/start/restart, process instance, and host inode changes fail;
the probe never repins to a replacement. The in-container descriptor/path check
compares Linux device/inode values within that container. It does not equate a
Docker Desktop host device number with a guest device number. The actual bind
validation ties those observations together. These are bracketed observations,
not proof against an adversarial transient inode replacement between checks.

## Shell protocol and BusyBox distinction

The shell first requires an existing regular non-symlink lock path, then opens
FD 9 with read-only `<` redirection. It never uses a creating redirection or
touches/chmods the file. Linux flock permits an exclusive BSD flock on a read-only
descriptor. Permission/open errors fail rather than falling back to root or
creating another lock file. A `stat` comparison of the descriptor and path
precedes flock and follows its result and any unlock; changes fail. This also
requires the image's `stat` and `/proc/self/fd/9` to work as the default user.

`/usr/bin/flock -x -n 9` never waits for a lock. The script captures its status
and all diagnostic output. An appended sentinel prevents command substitution
from erasing newline-only diagnostics. It accepts:

| Actual flock outcome | Required subsequent steps | Exact stdout on shell exit 0 | Result |
| --- | --- | --- | --- |
| Exit 0, no stdout/stderr | Same inode; successful diagnostic-free `flock -u 9`; same inode; FD close | `RESTORE_LOCK_V1\nOPEN\nACQUIRED\nUNLOCKED\nCLOSED\n` | `True` |
| Exit 1, no stdout/stderr | Same inode; FD close | `RESTORE_LOCK_V1\nOPEN\nCONTENDED\nCLOSED\n` | `False` |
| Any other exit or any diagnostic | Exit with failure, close FD during shell exit | No accepted protocol | Raise |

The Python callback matches the entire stdout **bytes** value; partial output,
extra bytes, strings, and malformed protocols fail. Shell open, stat, exec,
unlock and close failures cannot become contention. An EXIT trap closes FD 9;
HUP/INT/TERM also close it and fail. No background child, sleep, input wait,
blocking flock, or persistent holder is intentionally launched. Unlock failure
still closes the shell's descriptor during exit, but never reports success.

**BusyBox uncertainty:** exit 1 alone is ambiguous. The proposed distinction is
exit 1 **and no diagnostic**, after verified descriptor open and inode checks.
BusyBox's conventional flock behavior uses a silent exit 1 for EWOULDBLOCK and
diagnoses other syscall/usage failures. That convention is sufficient only if
the actual immutable applet preserves it for the tested failures. Parent's
`--help` observation does not establish this distinction; this worker has neither
executed that applet nor reviewed its exact source/binary. A silent noncontention
exit 1 would be indistinguishable and would invalidate this protocol. The
caller-supplied image pin prevents drift from the parent's validated image;
actual held-lock and error controls are mandatory for each image used. Shell
variables also cannot preserve arbitrary NUL diagnostic bytes;
the classification assumes this actual applet emits normal text diagnostics.

## Budgets, failure cleanup, and unresolved hard guarantee

Timeouts must be exact int/float values, finite, positive, and at most 20 seconds;
bools are rejected. One monotonic deadline starts before the first identity
check. Each guard and the whole Commands exec receive its remaining budget;
post-exec guard work and final protocol validation count too. Late results
always raise. A precheck failure performs no exec. The postcheck is attempted
after command errors if time remains; once exhausted, no additional unbounded
inspection is attempted and cleanup is unverified. Public failures are fixed
`ProbeError('message-restore-lock.<code>')` values with suppressed raw exception
context. Diagnostics remain in the parent's private Commands directory.

The current Commands API **cannot guarantee the supplied timeout as a hard
failure-path wall-clock bound**. Its cleanup can use multiple 10-second graces
after a command timeout; interrupting that cleanup would defeat its existing
reaping guarantees. It also reaps the local Docker client group, which does not
prove a container-side exec terminated after lost transport. The short script
has no intentional long-lived holder, but a delayed/stranded remote exec or
stalled syscall cannot be ruled out through this interface.

Consequently every failed command, malformed protocol, late exec or failed/changed
post-identity check sets `commands.cleanup_verified = False` before raising.
An existing false state is preserved and blocks further probes. This does not
assert that an abandoned exec was killed. The parent must treat the fixture as
unsafe to reset until it has quiesced the entire owned Go container and verified
release under its ownership policy, removing the container as necessary. A
second successful lock probe alone cannot
prove an earlier exec will not subsequently run. Do not certify strict
no-leftover-on-timeout acceptance using this adapter alone.

Meeting that hard requirement needs a reviewed remote-exec ownership contract:
an exec identifier with bounded cancellation/termination confirmation or a
verified container-side deadline mechanism, plus a bounded cleanup allowance
within the supplied budget. Existing Commands provides neither. No shared
change was made here. This is a concrete integration gap, not acceptance from
the offline mocks or a reason to weaken the consumer helper.

## Offline TDD and parent runtime checks

Run from the repository root with the supplied offline Python 3.13.16, without
bytecode. Set `PRODUCTION_PYTHON` to that already installed executable:

```sh
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
"$PRODUCTION_PYTHON" \
  -B -m unittest -v TESTS.test_message_restore_lock_probe
```

The original candidate passed 32 offline controls. For the explicit-image API
correction, three tests were added before implementation: a different explicit
parent image succeeds when both guard observations match, a mismatching actual
image prevents exec, and malformed/mutable expected images fail without I/O.
The RED run retained the prior 32 passing controls and failed the three additions
because the factory lacked `expected_image`. The corrected GREEN suite passes
all 35 controls. It covers factory inactivity, immutable identity pinning, missing
and changed identities, defensive copies, invalid and exhausted budgets, exact
output, arbitrary errors and cancellation, both guard calls, and the consumer's
idle True / blocked False twice / completed True sequence.

Shell controls execute only a local shell with an authored fake flock and a
portable test stat adapter. They check success, contention, other exits,
diagnostics including newline-only output, permission/missing/symlink/directory
paths, wrong descriptor inode, missing executable, unlock and close failures.
These substitutions exist only in tests; production exposes no overrides.
Two controls delegate to the real Commands with local Python children to verify
that nonzero exit and timeout still invoke its reaping path and leave no child
process group. They provide no remote Docker termination evidence.

Parent reports actual component checks passed for its earlier immutable
`sha256:3a5e37ac67f74c37fa9f0e7228d2e3f17ef33f5fcc032dd271e6d2119b779022`
image at default UID `10001:10001`: idle acquisition/release, held-lock contention
twice, acquisition after release, bad FD nonzero with diagnostics, permission
failure not classified as contention, and missing path not created. The owned
container was removed. Parent receipt (relative to the parent checkout):
`quality-reports/go-migration/restore-linux-protocol-r1/results.json`.
The script remains unchanged with SHA256
`78ca083813a4cdf60f1c693c114e7b7cdd9e6676980412a0dae0c33610aa3ecd`.
These are parent-reported component checks, not actual Go HTTP restore
acceptance. A rebuilt image still needs its own actual validation.

Parent still must, without altering the saved-message helper:

1. Implement/review the bounded actual identity adapter and resolve the hard
   timeout/remote-cleanup contract above before claiming that acceptance.
2. Supply `expected_image` from the validated build receipt, verify default-user
   success/release on that actual image and configured bind inode, and
   independently hold its actual lock and observe explicit contention. Repeat
   these component checks for a new image rather than relying on the older receipt.
3. Verify absent lock stays absent, unreadable/wrong/symlink path, absent executable,
   syscall/permission failure diagnostics, unexpected exit/output, unlock error,
   identity replacement, and transport-timeout cleanup. Record uncertainty if
   any noncontention failure is silent exit 1.
4. Run the consumer's fresh-fixture controls: idle `True`, blocked mutation
   `False` at least twice for the same backend transaction, then completed `True`.
   Preserve its HTTP/DB/Redis/effects and fixture cleanup assertions unchanged.

No Docker, image, HTTP, DB, Redis, or remote timeout behavior was exercised here.
Frozen source/dependency hashes and final RED/GREEN counts are in
`quality-reports/worker-progress.md`.
