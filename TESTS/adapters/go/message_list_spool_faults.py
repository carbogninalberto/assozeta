"""Bounded parent-only packaged LIST fault observations; no service lifecycle.

Imports are inert and stdlib only. Phase1 is loaded lazily by run_faults. Errors
carry only fixed codes; raw HTTP/proc/fixture diagnostics never enter evidence.
"""
import hashlib
import http.client
import math
import re
import time

SPOOL = '/var/cache/assozeta-message-list'
ROWS = 1152
MIN_RESPONSE = 64 * 1024 * 1024
MAX_RESPONSE = 128 * 1024 * 1024
ERROR_LIMIT = 512
HEADER_SECONDS = 15
ERROR_SECONDS = 5
RECOVERY_SECONDS = 90
CLEANUP_SECONDS = 15
GENERIC_ERROR = (b'\n<!doctype html>\n<html lang="en">\n<head>\n'
                 b'  <title>Server Error (500)</title>\n</head>\n<body>\n'
                 b'  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n')
ERROR_HASH = hashlib.sha256(GENERIC_ERROR).hexdigest()
HASH = re.compile(r'[0-9a-f]{64}\Z')
REMAINING = ('database-in-flight-cancellation', 'spool-enospc',
             'spool-write-failure', 'spool-read-failure',
             'new-storage-failure-during-final-copy',
             'parent-source-build-and-final-cleanup-binding')
TOP_KEYS = {'format', 'scope', 'faults_accepted', 'complete_acceptance',
            'runtime', 'cancellation', 'storage', 'remaining'}
RUNTIME_KEYS = {'uid', 'gid', 'same_process', 'exclusive_spool_verified',
                'ownership_preserved'}
CANCEL_KEYS = {'kind', 'status', 'body_bytes_read', 'live_unlinked_spools',
               'staged_bytes', 'response_closed', 'socket_closed', 'empty_after',
               'cleanup_seconds', 'recovery'}
STORAGE_KEYS = {'kind', 'original_mode', 'denied_mode', 'restored_mode', 'status',
                'error_contract', 'error_bytes', 'error_sha256', 'no_private_data',
                'empty_after_failure', 'restored', 'recovery'}
LIST_KEYS = {'rows', 'bytes', 'wire_sha256', 'content_sha256', 'expected_sha256'}

# Fixed paths only. exec() belongs to the existing ownership-checking fixture and
# inherits its bounded command runner. No root, chown, arbitrary path or temp file.
PROBE_SCRIPT = '''set -eu
test -d /var/cache/assozeta-message-list
test ! -L /var/cache/assozeta-message-list
id -u
id -g
stat -c '%u:%g:%a:%d:%i' /var/cache/assozeta-message-list
awk '/^Uid:/ {print $2, $3, $4, $5}' /proc/1/status
awk '/^Gid:/ {print $2, $3, $4, $5}' /proc/1/status
awk '{print $22}' /proc/1/stat
readlink /proc/1/exe'''


def _need(condition):
    if not condition:
        raise RuntimeError('faults.check-failed')


def _closed(value, keys):
    return type(value) is dict and len(value) == len(keys) and set(value) == keys


def _integer(value, expected):
    return type(value) is int and value == expected


def _literal(value, expected):
    return type(value) is str and value == expected


def _list_valid(value):
    return (_closed(value, LIST_KEYS) and _integer(value['rows'], ROWS)
            and type(value['bytes']) is int and MIN_RESPONSE < value['bytes'] <= MAX_RESPONSE
            and all(type(value[k]) is str and HASH.fullmatch(value[k])
                    for k in ('wire_sha256', 'content_sha256', 'expected_sha256'))
            and value['content_sha256'] == value['expected_sha256'])


def validate_faults(evidence):
    """Pure, closed, fail-closed phase2 gate; does not assert overall acceptance.

    This validates the observation contract, not provenance. The parent must bind
    the receipt to its actual packaged execution, source/build and final cleanup.
    """
    try:
        if not (_closed(evidence, TOP_KEYS) and _integer(evidence['format'], 1)
                and _literal(evidence['scope'], 'packaged-message-list-spool-phase2')
                and evidence['faults_accepted'] is True
                and evidence['complete_acceptance'] is False
                and type(evidence['remaining']) is list
                and len(evidence['remaining']) == len(REMAINING)
                and all(type(v) is str for v in evidence['remaining'])
                and evidence['remaining'] == list(REMAINING)):
            return False
        runtime, cancel, storage = (evidence[k] for k in ('runtime', 'cancellation', 'storage'))
        if not (_closed(runtime, RUNTIME_KEYS) and _integer(runtime['uid'], 10001)
                and _integer(runtime['gid'], 10001)
                and all(runtime[k] is True for k in
                        ('same_process', 'exclusive_spool_verified', 'ownership_preserved'))
                and _closed(cancel, CANCEL_KEYS) and _closed(storage, STORAGE_KEYS)):
            return False
        if not (_literal(cancel['kind'], 'post-header-response-abandonment')
                and _integer(cancel['status'], 200)
                and _integer(cancel['body_bytes_read'], 0)
                and _integer(cancel['live_unlinked_spools'], 1)
                and type(cancel['staged_bytes']) is int
                and MIN_RESPONSE < cancel['staged_bytes'] <= MAX_RESPONSE
                and all(cancel[k] is True for k in ('response_closed', 'socket_closed', 'empty_after'))
                and type(cancel['cleanup_seconds']) in (float, int)
                and math.isfinite(cancel['cleanup_seconds'])
                and 0 <= cancel['cleanup_seconds'] <= CLEANUP_SECONDS):
            return False
        if not (_literal(storage['kind'], 'spool-create-permission-denied')
                and _integer(storage['original_mode'], 0o700)
                and _integer(storage['denied_mode'], 0o500)
                and _integer(storage['restored_mode'], 0o700)
                and _integer(storage['status'], 500)
                and _literal(storage['error_contract'], 'generic-html-500')
                and _integer(storage['error_bytes'], len(GENERIC_ERROR))
                and _literal(storage['error_sha256'], ERROR_HASH)
                and all(storage[k] is True for k in
                        ('no_private_data', 'empty_after_failure', 'restored'))):
            return False
        first, second = cancel['recovery'], storage['recovery']
        return bool(_list_valid(first) and _list_valid(second) and first == second
                    and first['bytes'] == cancel['staged_bytes'])
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        return False


def _phase1():
    from TESTS.adapters.go import message_list_spool_image
    return message_list_spool_image


def _socket(response):
    # held_list intentionally hides its HTTPConnection; retain the response's
    # actual socket to verify closure after its owning context exits.
    return response.fp.raw._sock


class DeadlineResponse:
    """Per-read socket timeout plus an absolute body deadline for reused verifier."""
    def __init__(self, response, seconds):
        self.response = response
        self.status = response.status
        self.socket = _socket(response)
        self.deadline = time.monotonic() + seconds

    def getheader(self, name, default=None):
        return self.response.getheader(name, default)

    def read(self, amount):
        _need(type(amount) is int and 0 < amount <= 65536)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('faults.response-deadline')
        # A known Content-Length can close fp/socket on the final nonempty
        # fragment. The verifier still needs its next read to observe EOF.
        if not self.response.isclosed():
            self.socket.settimeout(min(remaining, 5))
        # read(n) can perform arbitrarily many recv calls while a peer trickles
        # data. read1 returns after one buffered read, allowing the absolute
        # deadline to be checked between fragments as well as between rows.
        result = self.response.read1(amount)
        _need(type(result) is bytes and len(result) <= amount)
        if time.monotonic() > self.deadline:
            raise TimeoutError('faults.response-deadline')
        return result


def _probe(proof, mode):
    raw = proof.exec('/bin/sh', '-c', PROBE_SCRIPT)
    _need(type(raw) is str and len(raw) <= 512)
    lines = raw.splitlines()
    _need(len(lines) == 7 and lines[:2] == ['10001', '10001']
          and lines[3:5] == ['10001 10001 10001 10001'] * 2
          and lines[6] == '/app/server')
    match = re.fullmatch(r'10001:10001:([0-7]{3}):([0-9]{1,20}):([0-9]{1,20})', lines[2])
    _need(match is not None and int(match[1], 8) == mode
          and re.fullmatch(r'[0-9]{1,20}', lines[5]) and int(lines[5]) > 0)
    # Private directory device/inode and PID1 starttime never leave this helper.
    return (match[2], match[3], lines[5])


def _change_mode(proof, identity, target):
    _need(target in (0o500, 0o700))
    device, inode, _ = identity
    _need(all(re.fullmatch(r'[0-9]{1,20}', v) for v in (device, inode)))
    # Recheck ownership, UID and directory identity within the same command that
    # changes mode. Restoration permits 0700 too: chmod may have failed before
    # changing mode, or succeeded before its command receipt failed.
    allowed = '700' if target == 0o500 else '500|700'
    script = ("set -eu\n"
              'test "$(id -u)" = 10001\n'
              'test "$(id -g)" = 10001\n'
              f'test -d {SPOOL}\ntest ! -L {SPOOL}\n'
              f'test "$(stat -c \'%u:%g:%d:%i\' {SPOOL})" = '
              f'"10001:10001:{device}:{inode}"\n'
              f'case "$(stat -c %a {SPOOL})" in {allowed}) ;; *) exit 1 ;; esac\n'
              f'chmod {target:04o} {SPOOL}')
    proof.exec('/bin/sh', '-c', script)
    _need(_probe(proof, target) == identity)


def _empty(phase, proof):
    start = time.monotonic()
    phase.wait_empty(proof)
    elapsed = time.monotonic() - start
    _need(0 <= elapsed <= CLEANUP_SECONDS)
    return elapsed


def _recover(phase, proof, base, token, association):
    try:
        with phase.held_list(base, token) as response:
            result = phase.verify_list(DeadlineResponse(response, RECOVERY_SECONDS), association)
            _need(_list_valid(result))
    finally:
        _empty(phase, proof)
    return result


def _cancel(phase, proof, base, token, association):
    try:
        with phase.held_list(base, token) as response:
            _need(response.status == 200)
            sock = _socket(response)
            observed = phase.observe_spools(proof, 1)
            _need(type(observed) is list and len(observed) == 1
                  and len(observed[0]) == 3 and type(observed[0][0]) is int
                  and MIN_RESPONSE < observed[0][0] <= MAX_RESPONSE)
            # No body read. A full live unlinked spool and received HTTP 200
            # headers precede deliberate response/socket abandonment on exit.
    finally:
        elapsed = _empty(phase, proof)
    _need(response.isclosed() and sock.fileno() == -1)
    recovery = _recover(phase, proof, base, token, association)
    return {'kind': 'post-header-response-abandonment', 'status': 200,
            'body_bytes_read': 0, 'live_unlinked_spools': 1,
            'staged_bytes': observed[0][0], 'response_closed': True,
            'socket_closed': True, 'empty_after': True, 'cleanup_seconds': elapsed,
            'recovery': recovery}


def _error_get(phase, base, token):
    connection = http.client.HTTPConnection('127.0.0.1', int(base.rsplit(':', 1)[1]),
                                            timeout=HEADER_SECONDS)
    response = None
    try:
        connection.request('GET', phase.ROUTE, headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = connection.getresponse()
        _need(response.status == 500
              and response.getheader('Content-Type') == 'text/html; charset=utf-8'
              and response.getheader('Content-Encoding') in (None, 'identity'))
        bounded = DeadlineResponse(response, ERROR_SECONDS)
        body = bytearray()
        while len(body) <= ERROR_LIMIT:
            chunk = bounded.read(ERROR_LIMIT + 1 - len(body))
            _need(type(chunk) is bytes)
            if not chunk:
                break
            body.extend(chunk)
        # Exact generic contract excludes paths, row data and provider messages.
        # Only the known length/hash is retained in the evidence.
        _need(body == GENERIC_ERROR)
        return {'status': 500, 'error_contract': 'generic-html-500',
                'error_bytes': len(body), 'error_sha256': hashlib.sha256(body).hexdigest(),
                'no_private_data': True}
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def _storage(phase, proof, base, token, association, identity):
    restored, empty = False, False
    try:
        # Mark the mutation boundary by entering this try before invoking chmod.
        # A command can change mode and then fail to return a usable receipt.
        _change_mode(proof, identity, 0o500)
        error = _error_get(phase, base, token)
    finally:
        try:
            _change_mode(proof, identity, 0o700)
            restored = True
        finally:
            # Attempt descriptor cleanup even when restoration fails.
            try:
                _empty(phase, proof)
                empty = True
            finally:
                if not restored:
                    raise RuntimeError('faults.restoration-failed') from None
    _need(restored and empty)
    recovery = _recover(phase, proof, base, token, association)
    return {'kind': 'spool-create-permission-denied', 'original_mode': 0o700,
            'denied_mode': 0o500, 'restored_mode': 0o700, **error,
            'empty_after_failure': True, 'restored': True, 'recovery': recovery}


def run_faults(proof, base, token, association):
    """Use an already running exclusive SpoolFixture; return evidence or raise.

    Caller owns the packaged service and binds source/build/final cleanup. Run
    after run_lists and before spool_server exits, without concurrent requests.
    All failures are redacted, including command/HTTP errors and interruptions.
    """
    failure = None
    try:
        _need(type(base) is str and re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', base)
              and 1 <= int(base.rsplit(':', 1)[1]) <= 65535)
        _need(type(token) is str and 0 < len(token) <= 16384
              and '\r' not in token and '\n' not in token
              and type(association) is str and 0 < len(association) <= 128)
        phase = _phase1()
        proof.volume_info()
        identity = _probe(proof, 0o700)
        _empty(phase, proof)
        cancellation = _cancel(phase, proof, base, token, association)
        _need(_probe(proof, 0o700) == identity)
        proof.volume_info()
        storage = _storage(phase, proof, base, token, association, identity)
        _need(_probe(proof, 0o700) == identity)
        proof.volume_info()
        evidence = {'format': 1, 'scope': 'packaged-message-list-spool-phase2',
                    'faults_accepted': True, 'complete_acceptance': False,
                    'runtime': {'uid': 10001, 'gid': 10001, 'same_process': True,
                                'exclusive_spool_verified': True, 'ownership_preserved': True},
                    'cancellation': cancellation, 'storage': storage,
                    'remaining': list(REMAINING)}
        _need(validate_faults(evidence))
        return evidence
    except BaseException as error:
        failure = ('faults.restoration-failed' if type(error) is RuntimeError
                   and error.args == ('faults.restoration-failed',)
                   else 'faults.execution-failed')
    # Raise outside the except suite so untrusted exception text is not retained
    # as context/cause on the public error, even for naive traceback consumers.
    raise RuntimeError(failure) from None
