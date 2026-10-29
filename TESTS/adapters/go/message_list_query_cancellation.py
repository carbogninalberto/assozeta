"""Parent-invoked packaged HTTP/real PostgreSQL cancellation proof.

Inert stdlib-only import. No provisioning, publication, global DB changes or
phase1 acceptance changes. Runtime dependencies are loaded only on invocation.
"""
import hashlib
from pathlib import Path
import re
import select
import socket
import stat
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
DATABASE = 'assozeta_contract_category_image'
SPOOL = '/var/cache/assozeta-message-list'
ROUTE = '/communications/messages/list'
WAIT_SECONDS = 8
RELEASE_SECONDS = 5
POLL_SECONDS = 0.05
IO_SECONDS = 3
SMALL_BYTES = 4096
HASH = re.compile(r'[0-9a-f]{64}\Z')
FLAGS = frozenset({
    'private_database', 'exact_lock_wait', 'blocker_retained', 'before_headers',
    'thread_exited', 'backend_released', 'spool_released_under_lock',
    'same_process', 'owned_spool_preserved', 'recovery_complete', 'cleanup_complete',
})
COUNTS = {'matching_backends': 1, 'blocked_spools': 1, 'released_spools': 0,
          'recovery_rows': 1152, 'owned_connections_closed': 2}
HASHES = {'query_sha256', 'recovery_wire_sha256', 'recovery_content_sha256',
          'recovery_expected_sha256'}
KEYS = {'format', 'scope', 'success', 'failure', 'cleanup_errors', 'cancel_ms',
        'blocked_spool_bytes', 'recovery_bytes'} | FLAGS | set(COUNTS) | HASHES


class _Failure(RuntimeError):
    def __init__(self, cleanup_errors=0):
        self.cleanup_errors = cleanup_errors
        super().__init__('query-cancellation.check-failed')


class _SpoolPresent(_Failure):
    """Only a valid, still-open small spool is eligible for a bounded retry."""


def _need(condition):
    if not condition:
        raise _Failure()


def _phase1():
    from TESTS.adapters.go import message_list_spool_image
    return message_list_spool_image


def _list_sql():
    """Exact source bytes are attribution, never the response oracle."""
    source = (ROOT / 'BE_GO/internal/repository/message_reads.go').read_text()
    matches = re.findall(r'(?m)^const messageReadsListSQL = `([^`]+)`', source)
    _need(len(matches) == 1)
    return matches[0]


def _rows(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _execute(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)


def _open_databases(proof):
    # Copy only the explicit private endpoint and credentials, never libpq
    # service/DSN/options/pool settings or the live global connection itself.
    from django.db import connections
    template = connections['default']
    config = dict(template.settings_dict)
    port = config.get('PORT')
    _need(template.vendor == 'postgresql'
          and config.get('ENGINE') == 'django.db.backends.postgresql'
          and config.get('HOST') == '127.0.0.1'
          and config.get('NAME') == DATABASE and config.get('USER') == 'contracts'
          and type(port) in (str, int) and str(port).isdigit()
          and 0 < int(port) <= 65535 and int(port) == int(proof.postgres_port)
          and proof.published_port(proof.postgres, 5432) == int(port)
          and type(config.get('PASSWORD')) is str and bool(config['PASSWORD'])
          and not template.in_atomic_block)
    params = dict(dbname=DATABASE, user=config['USER'], password=config['PASSWORD'],
                  host='127.0.0.1', hostaddr='127.0.0.1', port=int(port),
                  connect_timeout=IO_SECONDS, sslmode='disable',
                  options='-c statement_timeout=2000 -c lock_timeout=2000')
    acquired = []
    try:
        pids = []
        for _ in range(2):
            connection = template.Database.connect(**params)
            acquired.append(connection)  # Enroll before any fallible setup.
            connection.autocommit = True
            row, = _rows(connection, 'SELECT current_database(), current_user, pg_backend_pid()')
            _need(row[0] == DATABASE and row[1] == config['USER']
                  and type(row[2]) is int and row[2] > 0)
            pids.append(row[2])
        _need(pids[0] != pids[1])
        acquired[1].autocommit = False
        return acquired[0], acquired[1], pids[0], pids[1]
    except BaseException:
        errors = 0
        for connection in reversed(acquired):
            for action in (connection.rollback, connection.close):
                try:
                    action()
                except BaseException:
                    errors += 1
        raise _Failure(errors) from None


def _identity(proof):
    info = proof.owned(proof.server_name)
    _need(info['State']['Running'] is True and info['RestartCount'] == 0
          and type(info['State']['Pid']) is int and info['State']['Pid'] > 0)
    _need(proof.exec('stat', '-c', '%u:%g:%a', SPOOL) == '10001:10001:700')
    # Inode/device plus process start time prevent replacement by an empty dir
    # or a restarted process with a recycled PID. These never enter evidence.
    directory = proof.exec('stat', '-c', '%d:%i', SPOOL)
    _need(re.fullmatch(r'[0-9]+:[0-9]+', directory) is not None)
    process = proof.exec('cat', '/proc/1/stat')
    fields = process.rsplit(')', 1)[1].split()
    _need(len(fields) >= 20 and fields[19].isdigit())
    return (info['Id'], info['State']['Pid'], info['State']['StartedAt'],
            fields[19], directory)


FD_SCRIPT = '''for query_fd in /proc/1/fd/*; do
query_target=$(readlink "$query_fd") || continue
case "$query_target" in
/var/cache/assozeta-message-list/*)
case "$query_target" in
/var/cache/assozeta-message-list/assozeta-message-list-*" (deleted)") ;;
*) echo invalid; exit 1 ;;
esac
stat -Lc '%s %i %d %f %u %g %h' "$query_fd" || exit 1
sed -n 's/^mnt_id:[[:space:]]*//p' "/proc/1/fdinfo/${query_fd##*/}" || exit 1
;;
esac
done'''


def _linux_device(device):
    # The descriptor is in Linux even when the observer runs on macOS.
    major = ((device >> 8) & 0xfff) | ((device >> 32) & 0xfffff000)
    minor = (device & 0xff) | ((device >> 12) & 0xffffff00)
    return f'{major}:{minor}'


def _spools(proof, count):
    # Unlike phase1, accept a zero/small spool, but tighten descriptor metadata.
    mountinfo = proof.exec('cat', '/proc/1/mountinfo')
    _need(len(mountinfo) <= 1024 * 1024)
    mounts = [line.split(' - ', 1) for line in mountinfo.splitlines()
              if len(line.split()) > 4 and line.split()[4] == SPOOL]
    _need(len(mounts) == 1)
    left, right = mounts[0]
    mount, filesystem = left.split(), right.split()
    _need(filesystem[0] in ('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs')
          and 'rw' in mount[5].split(','))
    output = proof.exec('/bin/sh', '-c', FD_SCRIPT)
    _need(len(output) <= 4096)
    lines = output.splitlines()
    present = count == 0 and len(lines) == 2
    _need(len(lines) == count * 2 or present)
    result = []
    for offset in range(0, len(lines), 2):
        values = lines[offset].split()
        _need(len(values) == 7)
        size, inode, device = map(int, values[:3])
        mode = int(values[3], 16)
        uid, gid, links = map(int, values[4:])
        _need(0 <= size <= SMALL_BYTES and inode > 0 and device >= 0
              and stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600
              and uid == gid == 10001 and links == 0
              and int(lines[offset + 1]) == int(mount[0])
              and _linux_device(device) == mount[2])
        result.append((size, inode, device))
    _need(len({row[1:] for row in result}) == (1 if present else count))
    _need(proof.exec('/bin/sh', '-c', 'ls -A ' + SPOOL + ' | wc -l') == '0')
    if present:
        raise _SpoolPresent()
    return result


class _Request:
    def __init__(self, proof, base, token):
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', base)
        _need(match is not None and 0 < int(match[1]) <= 65535
              and int(match[1]) == proof.published_port(proof.server_name, 8000)
              and type(token) is str and bool(token) and '\r' not in token and '\n' not in token)
        self.port, self.token = int(match[1]), token
        self.sock = None
        self.thread = None
        self.sent, self.stopping = threading.Event(), threading.Event()
        self.headers, self.outcome = False, 'waiting'

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(IO_SECONDS)
        self.sock.connect(('127.0.0.1', self.port))
        self.sock.settimeout(WAIT_SECONDS + RELEASE_SECONDS + IO_SECONDS)

        def run():
            try:
                request = ('GET ' + ROUTE + ' HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                           'Authorization: Bearer ' + self.token + '\r\n'
                           'Accept: application/json\r\nAccept-Encoding: identity\r\n'
                           'Connection: close\r\n\r\n')
                self.sock.sendall(request.encode('ascii'))
                self.sent.set()
                first = self.sock.recv(1)
                self.headers = bool(first)
                self.outcome = ('disconnected' if not first and self.stopping.is_set()
                                else 'failed')
            except BaseException:
                self.outcome = 'failed'  # No exception text/traceback crosses thread.

        self.thread = threading.Thread(target=run, name='query-cancellation-client', daemon=True)
        self.thread.start()
        _need(self.sent.wait(IO_SECONDS) and self.pending())

    def pending(self):
        return (self.thread is not None and self.thread.is_alive()
                and self.outcome == 'waiting' and not self.headers
                and not select.select([self.sock], [], [], 0)[0])

    def disconnect(self):
        _need(self.pending())
        self.stopping.set()
        self.sock.shutdown(socket.SHUT_RDWR)  # Interrupt actual blocked recv.
        self.thread.join(IO_SECONDS)
        return (not self.thread.is_alive() and self.outcome == 'disconnected'
                and not self.headers)

    def close(self):
        errors = 0
        if self.sock is not None:
            try:
                if self.thread is not None and self.thread.is_alive():
                    self.stopping.set()
                    self.sock.shutdown(socket.SHUT_RDWR)
            except BaseException:
                errors += 1
            finally:
                try:
                    self.sock.close()
                except BaseException:
                    errors += 1
        if self.thread is not None:
            self.thread.join(IO_SECONDS)
            errors += int(self.thread.is_alive())
        _need(errors == 0)


def _request(proof, base, token):
    return _Request(proof, base, token)


def _held(observer, blocker_pid):
    row, = _rows(observer, """SELECT EXISTS(SELECT 1 FROM pg_locks l
 JOIN pg_stat_activity a ON a.pid=l.pid
 WHERE l.pid=%s AND l.relation='communications_message'::regclass
 AND l.mode='AccessExclusiveLock' AND l.granted
 AND a.datname=current_database() AND a.xact_start IS NOT NULL
 AND a.state='idle in transaction')""", (blocker_pid,))
    _need(row == (True,))


def _waiting(observer, query, observer_pid, blocker_pid):
    return _rows(observer, """SELECT pid,backend_start,state,xact_start IS NULL,
 query,wait_event_type,pg_blocking_pids(pid),usename,backend_type
 FROM pg_stat_activity WHERE datname=current_database()
 AND pid NOT IN (%s,%s) AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL)
 AND (query=%s OR %s=ANY(pg_blocking_pids(pid)))""",
                 (observer_pid, blocker_pid, query, blocker_pid))


def _released(observer, pid, start):
    rows = _rows(observer, """SELECT backend_start,state,xact_start IS NULL,
 cardinality(pg_blocking_pids(pid))=0 AND wait_event_type IS DISTINCT FROM 'Lock'
 FROM pg_stat_activity WHERE pid=%s""", (pid,))
    if not rows:
        return True
    _need(len(rows) == 1 and rows[0][0] == start)
    return rows[0][1:] == ('idle', True, True)


def _template():
    return dict(format=1, scope='packaged-message-list-query-cancellation',
                success=False, failure='setup', cleanup_errors=0, cancel_ms=0,
                blocked_spool_bytes=0, recovery_bytes=0,
                **{key: False for key in FLAGS}, **{key: 0 for key in COUNTS},
                **{key: '0' * 64 for key in HASHES})


def run_query_cancellation(proof, base, token, association):
    """Return closed safe evidence; parent owns source/build/final fixture cleanup.

    Invoke after all other requests, inside the existing spool_server context,
    with the initialized private Django environment and the committed 1152 rows.
    """
    evidence = _template()
    observer = blocker = client = None
    stage = 'setup'
    try:
        _need(__debug__)
        phase1 = _phase1()
        query = _list_sql()
        evidence['query_sha256'] = hashlib.sha256(query.encode()).hexdigest()
        identity = _identity(proof)
        _spools(proof, 0)
        stage = 'database'
        observer, blocker, observer_pid, blocker_pid = _open_databases(proof)
        evidence['private_database'] = True
        _need(not _waiting(observer, query, observer_pid, blocker_pid))
        stage = 'lock'
        _execute(blocker, 'LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE')
        _held(observer, blocker_pid)
        client = _request(proof, base, token)
        client.start()
        stage = 'wait'
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            _need(time.monotonic() < deadline and client.pending())
            _held(observer, blocker_pid)
            rows = _waiting(observer, query, observer_pid, blocker_pid)
            if rows:
                _need(len(rows) == 1)
                pid, start, state, no_transaction, sql, wait, blockers, user, kind = rows[0]
                _need(type(pid) is int and pid > 0 and pid not in (observer_pid, blocker_pid)
                      and start is not None and state == 'active' and no_transaction is False
                      and sql == query and wait == 'Lock' and blockers == [blocker_pid]
                      and user == 'contracts' and kind == 'client backend')
                _need(time.monotonic() < deadline)
                break
            time.sleep(POLL_SECONDS)
        evidence['exact_lock_wait'] = True
        evidence['matching_backends'] = 1
        stage = 'spool'
        spool, = _spools(proof, 1)
        evidence['blocked_spools'] = 1
        evidence['blocked_spool_bytes'] = spool[0]
        # Recheck the same exact waiter after observing the live descriptor.
        _need(_waiting(observer, query, observer_pid, blocker_pid) == rows)
        _held(observer, blocker_pid)
        stage = 'disconnect'
        began = time.monotonic()
        _need(client.disconnect())
        evidence['before_headers'] = evidence['thread_exited'] = True
        stage = 'release'
        deadline = began + RELEASE_SECONDS
        while True:
            _need(time.monotonic() < deadline)
            _held(observer, blocker_pid)
            if _released(observer, pid, start):
                try:
                    _spools(proof, 0)
                except _SpoolPresent:
                    pass  # A closing descriptor may be briefly visible.
                else:
                    _held(observer, blocker_pid)
                    _need(time.monotonic() < deadline)
                    break
            time.sleep(POLL_SECONDS)
        evidence['cancel_ms'] = int((time.monotonic() - began) * 1000)
        evidence['blocker_retained'] = evidence['backend_released'] = True
        evidence['spool_released_under_lock'] = True
        # No unblocking before the backend, thread and spool release proof.
        blocker.rollback()
        stage = 'recovery'
        with phase1.held_list(base, token) as response:
            recovered = phase1.verify_list(response, association)
        _need(type(recovered) is dict and set(recovered) == {
            'rows', 'bytes', 'wire_sha256', 'content_sha256', 'expected_sha256'})
        _need(type(recovered['rows']) is int and recovered['rows'] == 1152
              and type(recovered['bytes']) is int and 64 * 1024 * 1024 < recovered['bytes'] <= 128 * 1024 * 1024
              and all(type(recovered[k]) is str and HASH.fullmatch(recovered[k])
                      for k in ('wire_sha256', 'content_sha256', 'expected_sha256'))
              and recovered['content_sha256'] == recovered['expected_sha256'])
        evidence['recovery_rows'], evidence['recovery_bytes'] = recovered['rows'], recovered['bytes']
        for field in ('wire_sha256', 'content_sha256', 'expected_sha256'):
            evidence['recovery_' + field] = recovered[field]
        evidence['recovery_complete'] = True
        stage = 'continuity'
        _spools(proof, 0)
        _need(_identity(proof) == identity)
        evidence['same_process'] = evidence['owned_spool_preserved'] = True
        evidence['failure'] = 'none'
    except BaseException as error:
        evidence['failure'] = stage
        if isinstance(error, _Failure):
            evidence['cleanup_errors'] += error.cleanup_errors
    finally:
        actions = []
        if client is not None:
            actions.append((client.close, None))
        if blocker is not None:
            actions.extend(((blocker.rollback, None), (blocker.close, 'connection')))
        if observer is not None:
            actions.append((observer.close, 'connection'))
        for action, kind in actions:
            try:
                action()
                if kind == 'connection':
                    evidence['owned_connections_closed'] += 1
            except BaseException:
                evidence['cleanup_errors'] += 1
        evidence['cleanup_complete'] = (evidence['cleanup_errors'] == 0
                                         and evidence['owned_connections_closed'] == 2)
        if evidence['cleanup_errors']:
            evidence['failure'] = 'cleanup'
    evidence['success'] = evidence['failure'] == 'none' and evidence['cleanup_complete']
    return evidence


def validate_query_cancellation(evidence):
    """Strict success validator; a safe failure record cannot authorize success."""
    if type(evidence) is not dict or set(evidence) != KEYS:
        return False
    return (type(evidence['format']) is int and evidence['format'] == 1
            and evidence['scope'] == 'packaged-message-list-query-cancellation'
            and evidence['success'] is True and evidence['failure'] == 'none'
            and all(evidence[k] is True for k in FLAGS)
            and all(type(evidence[k]) is int and evidence[k] == v for k, v in COUNTS.items())
            and type(evidence['cleanup_errors']) is int and evidence['cleanup_errors'] == 0
            and type(evidence['cancel_ms']) is int and 0 <= evidence['cancel_ms'] <= RELEASE_SECONDS * 1000
            and type(evidence['blocked_spool_bytes']) is int and 0 <= evidence['blocked_spool_bytes'] <= SMALL_BYTES
            and type(evidence['recovery_bytes']) is int
            and 64 * 1024 * 1024 < evidence['recovery_bytes'] <= 128 * 1024 * 1024
            and all(type(evidence[k]) is str and HASH.fullmatch(evidence[k]) is not None for k in HASHES)
            and evidence['recovery_content_sha256'] == evidence['recovery_expected_sha256'])
