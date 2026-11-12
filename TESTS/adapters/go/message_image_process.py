"""Bounded live packaged-server evidence, composed with the existing guard.

Parent integration (inside ImageFixture.server, after readiness, before stop)::

    guard = executing_server_guard(backend_guard, docker=fixture.docker,
        container=fixture.identifier + '-server',
        expected_server_sha256=build['server_sha256'])
    # Pass guard to existing workloads; ordinary calls only inspect the backend.
    start = guard.evidence
    # ... workloads ...
    end = guard.probe()  # Required explicit end observation, before server stop.

Construction probes once. probe() refreshes executable/port evidence and raises
AssertionError on failure. Failure permanently invalidates this guard, including
its evidence property. Calls preserve exactly {pid, instance, running}, using the
backend's host PID/instance. Evidence's pid is the container PID (always 1).

docker has ImageFixture.docker's (*args, check, timeout) -> CompletedProcess text
interface. Only one fixed exec is issued per probe, as the image's default user;
there is no root fallback. Both proc network tables must be readable. Output,
FD count and Docker exec time are bounded. Proc permissions may prevent proof.

Evidence is a defensive dictionary: pid, starttime_ticks, executable {path,
sha256, device, inode}, port, listeners [{family, local_address_hex, local_port,
inode, fds}]. SHA is of /proc/1/exe, never the file merely left at /app/server.
Every observed TCP/TCP6 LISTEN socket on 8000 must belong to PID 1's descriptors.

These are bracketed point observations, not continuous exec tracing. A transient
exec/socket replacement entirely between observations cannot be excluded. Parent
owns container execution, workload integration and runtime acceptance.
"""
from copy import deepcopy
import re
import subprocess


# Constant shell only: no container name, digest, environment or caller text is
# interpolated. Alpine BusyBox supplies these utilities in BE_GO/Dockerfile.
# head bounds each table; Python rejects a truncated/oversized section. Failed
# readlink (including permission or FD disappearance) terminates the observation.
PROBE_SCRIPT = r'''set -eu
export LC_ALL=C
section() { printf '@%s\n' "$1"; }
fds() {
    count=0
    for path in /proc/1/fd/*; do
        count=$((count + 1))
        [ "$count" -le 4096 ] || exit 1
        target=$(readlink "$path") || exit 1
        printf '%s\t%s\n' "${path##*/}" "$target"
    done
}
table() { head -c 262145 "$1"; printf '\n'; }
section stat_before
head -c 8193 /proc/1/stat
section exe_before
readlink /proc/1/exe
section exe_id_before
stat -L -c '%d:%i' /proc/1/exe
section sha_before
sha256sum /proc/1/exe
section fd_before
fds
section tcp_before
table /proc/1/net/tcp
section tcp6_before
table /proc/1/net/tcp6
section tcp_after
table /proc/1/net/tcp
section tcp6_after
table /proc/1/net/tcp6
section fd_after
fds
section sha_after
sha256sum /proc/1/exe
section exe_id_after
stat -L -c '%d:%i' /proc/1/exe
section exe_after
readlink /proc/1/exe
section stat_after
head -c 8193 /proc/1/stat
section done
'''

_SECTIONS = ('stat_before', 'exe_before', 'exe_id_before', 'sha_before',
             'fd_before', 'tcp_before', 'tcp6_before', 'tcp_after',
             'tcp6_after', 'fd_after', 'sha_after', 'exe_id_after', 'exe_after', 'stat_after')
_FAILURE = 'image.executing-server'
_OBSERVATION_ERRORS = (AssertionError, AttributeError, LookupError, TypeError,
                       ValueError, OSError, RuntimeError, subprocess.SubprocessError)


def _need(condition):
    # Deliberately independent of Python assert / optimization flags.
    if not condition:
        raise AssertionError(_FAILURE)


def _sections(output):
    _need(type(output) is str and 0 < len(output) <= 1_200_000
          and output.isascii() and output.endswith('@done\n')
          and all(char in '\t\n' or ' ' <= char <= '~' for char in output))
    result, current = {}, None
    for line in output.splitlines():
        if line.startswith('@'):
            name = line[1:]
            _need(name in (*_SECTIONS, 'done') and name not in result)
            result[name] = []
            current = name
        else:
            _need(current is not None and current != 'done')
            result[current].append(line)
    _need(tuple(result) == (*_SECTIONS, 'done'))
    for name in _SECTIONS:
        value = '\n'.join(result[name])
        limit = 262144 if name.startswith(('tcp', 'fd')) else 8192
        _need(0 < len(value) <= limit)
        result[name] = value.strip('\n')
        _need(bool(result[name]))
    return result


def _starttime(value):
    match = re.fullmatch(r'1 \((.*)\) ([RSDI]) (.*)', value)
    _need(match is not None)
    fields = match[3].split()
    _need(49 <= len(fields) <= 128
          and all(re.fullmatch(r'-?[0-9]+', field) for field in fields)
          and fields[0] == '0' and int(fields[18]) > 0)
    return int(fields[18])


def _executable(values, when, expected):
    _need(values['exe_' + when] == '/app/server'
          and values['sha_' + when] == expected + '  /proc/1/exe')
    identity = re.fullmatch(r'([0-9]+):([0-9]+)', values['exe_id_' + when])
    _need(identity is not None and int(identity[2]) > 0)
    return {'path': '/app/server', 'sha256': expected,
            'device': int(identity[1]), 'inode': int(identity[2])}


def _fds(value):
    result = {}
    lines = value.splitlines()
    _need(0 < len(lines) <= 4096)
    for line in lines:
        pair = line.split('\t', 1)
        _need(len(pair) == 2 and re.fullmatch(r'[0-9]+', pair[0])
              and pair[1] and int(pair[0]) not in result)
        fd, target = int(pair[0]), pair[1]
        inode = None
        if target.startswith('socket:'):
            match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
            _need(match is not None and int(match[1]) > 0)
            inode = int(match[1])
        result[fd] = inode
    return result


def _listeners(value, family, descriptors):
    lines = value.splitlines()
    header = lines[0].split()
    _need(header == ['sl', 'local_address',
                     'rem_address' if family == 'tcp' else 'remote_address',
                     'st', 'tx_queue', 'rx_queue', 'tr', 'tm->when',
                     'retrnsmt', 'uid', 'timeout', 'inode'])
    width = 8 if family == 'tcp' else 32
    address_pattern = r'[0-9A-Fa-f]{' + str(width) + r'}:[0-9A-Fa-f]{4}'
    result, slots, inodes = [], set(), set()
    for line in lines[1:]:
        if not line.strip():
            continue
        row = line.split()
        _need(10 <= len(row) <= 32 and re.fullmatch(r'[0-9]+:', row[0])
              and row[0] not in slots)
        slots.add(row[0])
        _need(re.fullmatch(address_pattern, row[1])
              and re.fullmatch(address_pattern, row[2])
              and re.fullmatch(r'[0-9A-Fa-f]{2}', row[3])
              and re.fullmatch(r'[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}', row[4])
              and re.fullmatch(r'[0-9A-Fa-f]{2}:[0-9A-Fa-f]{8}', row[5])
              and re.fullmatch(r'[0-9A-Fa-f]{8}', row[6])
              and all(re.fullmatch(r'[0-9]+', field) for field in row[7:10]))
        address, port = row[1].split(':')
        if int(row[3], 16) != 10 or int(port, 16) != 8000:
            continue
        inode = int(row[9])
        _need(int(row[2].replace(':', ''), 16) == 0
              and inode > 0 and inode not in inodes)
        inodes.add(inode)
        owned = sorted(fd for fd, target in descriptors.items() if target == inode)
        _need(bool(owned))
        result.append({'family': family, 'local_address_hex': address.upper(),
                       'local_port': 8000, 'inode': inode, 'fds': owned})
    return sorted(result, key=lambda row: (row['local_address_hex'], row['inode']))


def _evidence(output, expected):
    values = _sections(output)
    before, after = _starttime(values['stat_before']), _starttime(values['stat_after'])
    executable = _executable(values, 'before', expected)
    _need(before == after and executable == _executable(values, 'after', expected))
    observations = []
    for when in ('before', 'after'):
        descriptors = _fds(values['fd_' + when])
        observations.append([listener for family in ('tcp', 'tcp6')
            for listener in _listeners(values[family + '_' + when], family, descriptors)])
    _need(bool(observations[0]) and observations[0] == observations[1])
    # A socket inode cannot describe two distinct listening sockets.
    _need(len({row['inode'] for row in observations[0]}) == len(observations[0]))
    return {'pid': 1, 'starttime_ticks': before, 'executable': executable,
            'port': 8000, 'listeners': observations[0]}


def _backend(guard):
    value = guard()
    _need(type(value) is dict and set(value) == {'pid', 'instance', 'running'}
          and value['running'] is True
          and all(type(value[key]) is int and value[key] > 0 for key in ('pid', 'instance')))
    return dict(value)


class _ExecutingServerGuard:
    def __init__(self, backend_guard, docker, container, expected):
        self._backend_guard, self._docker = backend_guard, docker
        self._container, self._expected = container, expected
        self._failed, self._evidence = False, None
        try:
            self._identity = _backend(backend_guard)
        except _OBSERVATION_ERRORS:
            raise AssertionError(_FAILURE) from None
        self.probe()

    def __call__(self):
        if not self._failed:
            try:
                _need(_backend(self._backend_guard) == self._identity)
            except _OBSERVATION_ERRORS:
                self._failed = True
        return {**self._identity, 'running': not self._failed}

    @property
    def evidence(self):
        """Last successful observation; unavailable after any observed failure."""
        _need(not self._failed and self._evidence is not None)
        return deepcopy(self._evidence)

    def probe(self):
        """Explicit start/end live proof; one default-user exec, no retry/bypass."""
        try:
            _need(not self._failed and self()['running'])
            result = self._docker('exec', self._container, 'sh', '-c', PROBE_SCRIPT,
                                  check=False, timeout=10)
            _need(type(result.returncode) is int and result.returncode == 0)
            evidence = _evidence(result.stdout, self._expected)
            _need(self()['running'])
            if self._evidence is not None:
                _need(evidence == self._evidence)
            self._evidence = evidence
            return self.evidence
        except _OBSERVATION_ERRORS:
            self._failed = True
            raise AssertionError(_FAILURE) from None
        except BaseException:
            # Cancellation still unwinds the parent's lifetime/cleanup handlers,
            # but interrupted evidence can never remain available for reuse.
            self._failed = True
            raise


def executing_server_guard(backend_guard, *, docker, container, expected_server_sha256):
    """Wrap container_liveness_guard; ownership remains the backend's obligation.

    container must be the owned name already validated by that guard, and the
    expected SHA must come from the exact builder-server receipt. This helper
    neither starts containers nor determines migration runtime acceptance.
    """
    if (not callable(backend_guard) or not callable(docker)
            or type(container) is not str
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', container) is None
            or type(expected_server_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_server_sha256) is None):
        raise ValueError('image.executing-server-input')
    return _ExecutingServerGuard(backend_guard, docker, container, expected_server_sha256)
