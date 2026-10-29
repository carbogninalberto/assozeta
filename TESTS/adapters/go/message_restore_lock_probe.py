"""Inert packaged-Go exclusive lock observation; parent owns all runtime I/O.

Only explicit invocation executes Docker, through existing builder.Commands.
identity_guard(*, timeout) must validate the actual owned runtime and return the
runner's _linux_lock_identity tuple. See the companion document for hard timeout
and remote-exec cleanup limitations of the existing Commands interface.
"""
from copy import deepcopy
import math
import re
import time


# No caller values, executable mounts, creation redirections, background jobs,
# blocking locks, root override, or input-dependent waits. The default image user
# opens an EXISTING regular file read-only; Linux flock supports exclusive locks
# on read-only descriptors. The shell owns FD 9 only for this short observation.
# An appended sentinel preserves even newline-only diagnostics in substitutions.
PROBE_SCRIPT = r'''set -u
export LC_ALL=C
fail() { exit 70; }
lock_path_ok() {
    [ -f /operations/lifecycle.lock ] && [ ! -L /operations/lifecycle.lock ]
}
same_inode() {
    lock_path_ok || return 1
    current=$(stat -L -c '%d:%i' /operations/lifecycle.lock) || return 1
    descriptor=$(stat -L -c '%d:%i' /proc/self/fd/9) || return 1
    [ "$opened" = "$current" ] && [ "$opened" = "$descriptor" ]
}
lock_path_ok || fail
exec 9< /operations/lifecycle.lock || fail
trap 'exec 9<&-' 0
trap 'exec 9<&-; exit 79' HUP INT TERM
opened=$(stat -L -c '%d:%i' /proc/self/fd/9) || fail
[ -n "$opened" ] || fail
same_inode || fail
printf 'RESTORE_LOCK_V1\nOPEN\n'
status=0
diagnostics=$(/usr/bin/flock -x -n 9 2>&1; status=$?; printf x; exit "$status") || status=$?
[ "$diagnostics" = x ] || fail
case "$status" in
    0)
        printf 'ACQUIRED\n'
        same_inode || fail
        status=0
        diagnostics=$(/usr/bin/flock -u 9 2>&1; status=$?; printf x; exit "$status") || status=$?
        [ "$status" = 0 ] && [ "$diagnostics" = x ] || fail
        printf 'UNLOCKED\n'
        ;;
    1) printf 'CONTENDED\n' ;;
    *) fail ;;
esac
same_inode || fail
exec 9<&- || fail
[ ! -e /proc/self/fd/9 ] || fail
printf 'CLOSED\n'
'''

_ACQUIRED = b'RESTORE_LOCK_V1\nOPEN\nACQUIRED\nUNLOCKED\nCLOSED\n'
_CONTENDED = b'RESTORE_LOCK_V1\nOPEN\nCONTENDED\nCLOSED\n'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
_STARTED = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z\Z')


class ProbeError(RuntimeError):
    """Fixed public code only; no raw command, identity, path, or diagnostics."""


def _fail(code):
    raise ProbeError('message-restore-lock.' + code) from None


def _positive_integer(value):
    return type(value) is int and value > 0


def _identity(value, container, expected_image):
    """Check tuple shape defensively; the parent guard owns actual validation.

    The runner's returned tuple omits UID/mount/env metadata: the callback must
    retain its _container_identity and _linux_lock_identity validations, including
    owner label, default UID 10001:10001, configured path and exact bind mount.
    """
    if type(value) is not tuple or len(value) != 3:
        _fail('identity')
    runtime, process, inode = value
    if (type(runtime) is not dict or set(runtime) != {'id', 'image', 'pid', 'started', 'restarts'}
            or runtime['id'] != container or type(runtime['image']) is not str
            or runtime['image'] != expected_image
            or not _positive_integer(runtime['pid']) or runtime['restarts'] != 0
            or type(runtime['restarts']) is not int or type(runtime['started']) is not str
            or _STARTED.fullmatch(runtime['started']) is None or runtime['started'].startswith('0001-')):
        _fail('identity')
    if (type(process) is not dict or set(process) != {'pid', 'instance', 'running'}
            or process['running'] is not True or not _positive_integer(process['pid'])
            or process['pid'] != runtime['pid'] or not _positive_integer(process['instance'])
            or type(inode) is not tuple or len(inode) != 2
            or type(inode[0]) is not int or inode[0] < 0 or not _positive_integer(inode[1])):
        _fail('identity')
    return deepcopy(value)


def make_exclusive_probe(*, commands, container, expected_image, identity_guard):
    """Return exclusive_probe(*, timeout) -> bool, without doing any I/O now.

    container is the full immutable owned ID. expected_image is an explicit
    sha256 digest from the parent's validated build receipt; parent must validate
    actual BusyBox behavior for that image before use. commands is existing
    message_image_build.Commands (bytes on success, exceptions otherwise), with
    cleanup_verified tracking. identity_guard(*, timeout) must bound ALL its
    inspection and process checks using that remaining total budget. No fallback
    to an unbounded zero-argument guard is allowed. Baseline is pinned on first
    invocation and cannot change across the helper's idle/busy/completed probes.

    A failed/late exec invalidates commands.cleanup_verified: local client group
    reaping alone cannot certify the remote exec released its descriptor. Parent
    must handle that uncertainty before fixture reset; see companion document.
    """
    if (not callable(commands) or not callable(identity_guard)
            or type(container) is not str or _HASH.fullmatch(container) is None
            or type(expected_image) is not str or _IMAGE.fullmatch(expected_image) is None
            or type(getattr(commands, 'cleanup_verified', None)) is not bool):
        _fail('inputs')
    baseline = None

    def exclusive_probe(*, timeout):
        nonlocal baseline
        if (type(timeout) not in (int, float) or not 0 < timeout <= 20
                or not math.isfinite(timeout)):
            _fail('budget')
        deadline = time.monotonic() + timeout

        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                _fail('deadline')
            return left

        def observe():
            try:
                value = identity_guard(timeout=remaining())
                remaining()
                return _identity(value, container, expected_image)
            except ProbeError:
                raise
            except BaseException:
                _fail('identity')

        if commands.cleanup_verified is not True:
            _fail('cleanup-unverified')
        before = observe()
        if baseline is None:
            baseline = before
        if before != baseline:
            _fail('identity-changed')

        failure = None
        output = None
        try:
            output = commands(['docker', 'exec', container, '/bin/sh', '-c', PROBE_SCRIPT],
                              timeout=remaining())
        except BaseException:
            # Commands has already run its original cancellation/group-reaping
            # path. Never reinterpret arbitrary nonzero exits as contention.
            failure = 'exec'
        try:
            after = observe()
            if after != baseline:
                failure = 'identity-changed'
        except BaseException:
            failure = 'post-identity'
        if failure is not None:
            commands.cleanup_verified = False
            _fail(failure)
        if commands.cleanup_verified is not True:
            _fail('cleanup-unverified')
        if type(output) is not bytes or output not in (_ACQUIRED, _CONTENDED):
            commands.cleanup_verified = False
            _fail('protocol')
        # Includes both guard calls, command completion, parsing and validation.
        try:
            remaining()
        except ProbeError:
            commands.cleanup_verified = False
            raise
        return output == _ACQUIRED

    return exclusive_probe
