"""Inert actual-HTTP acceptance for parent-owned native message mutation services.

No environment discovery, service launch, fixture creation or fallback backend.
The parent supplies portable assertions and owns fixture_context/reset. The final
case sends SIGTERM to the exact supplied disposable Popen and consumes it.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import copy
import errno
import http.client
import io
import ipaddress
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID


OWNER_QUERY = 'SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE'
CASE_ORDER = ('create-disconnect', 'delete-disconnect', 'create-sigterm-drain')


def _require(condition, code):
    if not condition:
        raise AssertionError('message-lifecycle.' + code)


@dataclass(frozen=True)
class Bounds:
    lock_wait: float = 5
    survival: float = .25
    listener_stop: float = 5
    completion: float = 10
    exit: float = 10
    io: float = 2
    query: float = 2
    poll: float = .02

    def validate(self):
        for value in vars(self).values():
            _require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 30,
                     'invalid-bound')
        _require(.1 <= self.survival <= 2 and self.poll <= self.survival / 2,
                 'survival-observation-bound')
        _require(self.listener_stop + self.survival + self.query < 20,
                 'must-release-before-25s-server-expiry')
        return self


@dataclass(frozen=True)
class PortableAssertions:
    """Bind the existing portable oracle; none of its expected values are rewritten."""
    validate_runtime: object
    assert_effect: object
    snapshot_state: object
    assert_state_unchanged: object
    assert_cache_unchanged: object
    snapshot_cache: object
    require_private_database: object

    @classmethod
    def from_modules(cls, *, contract, scopes, shared, cache, adapter):
        return cls(contract.validate_runtime, contract.assert_effect, scopes.snapshot_state,
                   shared.assert_state_unchanged, shared.assert_cache_unchanged,
                   cache.snapshot_historical_read_cache, adapter.require_private_database)


@dataclass(frozen=True)
class OwnerRow:
    blocker: int
    owner_id: str
    relation: int
    page: int
    item: int


@dataclass(frozen=True)
class WaitProof:
    pid: int
    xact: str
    backend: str


def _blocked(rows, owner):
    matches = []
    for row in rows:
        # The tuple-lock identity binds this wait to the ONE exact row locked by
        # our separate blocker, rather than any transaction touching this table.
        if (row['pid'] != owner.blocker and owner.blocker in row['blockers']
                and row['state'] == 'active' and row['wait'] == 'Lock'
                and row['xact'] is not None and row['backend'] is not None
                and row['query'] == OWNER_QUERY
                and (owner.relation, owner.page, owner.item) in
                    [tuple(value) for value in row['tuples']]):
            matches.append(WaitProof(row['pid'], str(row['xact']), str(row['backend'])))
    _require(len(matches) <= 1, 'ambiguous-owner-wait')
    return matches[0] if matches else None


def _same_wait(rows, owner, proof):
    _require(_blocked(rows, owner) == proof, 'transaction-lost-after-client-close-or-signal')


def _verify_effect(portable, case, before, after, contrasts, *, started, finished, client_ip):
    _require(len(before[0]) == len(after[0]) == 128, 'requires-all-128-models')
    # Both portable domains are checked even if one fails; no generated-value
    # normalization is introduced here. The return value of assert_effect is
    # deliberately discarded: acceptance is against its exact raw projection.
    errors = []
    for check in (
        lambda: portable.assert_effect(case, before[0], after[0], started=started,
                                      finished=finished, remote_addr=client_ip, cid=case['name']),
        lambda: portable.assert_cache_unchanged(before[1], after[1], contrasts),
    ):
        try:
            check()
        except AssertionError as error:
            errors.append(error)
    if errors:
        if len(errors) == 1:
            raise errors[0]
        raise ExceptionGroup('message lifecycle exact effects failed', errors)
    return {'models': 128, 'exact_effects': True, 'full_redis': True}


def _run_case(control, case, mode):
    _require(mode in ('disconnect', 'drain'), 'unknown-case-mode')
    before = control.capture()
    started = time.time()
    try:
        with control.hold_owner() as owner:
            control.assert_alive()
            control.open_request(case)
            proof = control.wait_blocked(owner)
            _require(isinstance(proof, WaitProof), 'missing-lock-wait-proof')
            control.unchanged(before)
            if mode == 'disconnect':
                control.disconnect()
            else:
                control.signal()
                # TCP refusal is a synchronization event: Shutdown has closed
                # the actual listener, while the admitted operation still waits.
                control.listener_stopped()
            control.confirm_wait(owner, proof)
            control.unchanged(before)
        control.wait_finished(proof)
        if mode == 'drain':
            control.response(case)
            control.clean_exit()
        # Shutdown hooks can still write after the request transaction ends.
        # The final drain projection must include everything through exit zero.
        result = control.verify(case, before, started, time.time())
        if mode == 'disconnect':
            control.assert_alive()
        return dict(name=case['name'], success=True, blocked_backend=proof.pid,
                    exact_owner_row=True, transaction_survived=True, **result)
    finally:
        control.close_request()


class _ResponseSocket:
    """HTTPResponse socket adapter with one absolute header/body read deadline."""
    def __init__(self, stream, timeout):
        self.stream, self.deadline = stream, time.monotonic() + timeout

    def makefile(self, mode):
        adapter = self

        class Reader(io.RawIOBase):
            def readable(self):
                return True

            def readinto(self, buffer):
                remaining = adapter.deadline - time.monotonic()
                _require(remaining > 0, 'response-read-deadline')
                adapter.stream.settimeout(remaining)
                count = adapter.stream.recv_into(buffer)
                _require(time.monotonic() < adapter.deadline, 'response-read-deadline')
                return count

        return io.BufferedReader(Reader())


def _response_body(response):
    """Read one complete frame, with separate 64 KiB body/framing budgets."""
    limit = 65536
    lengths = response.headers.get_all('Content-Length', [])
    encodings = response.headers.get_all('Transfer-Encoding', [])
    _require(not (lengths and encodings), 'response-ambiguous-framing')
    if lengths:
        _require(len(lengths) == 1 and re.fullmatch(r'[0-9]+', lengths[0]) is not None,
                 'response-content-length')
        _require(int(lengths[0]) <= limit, 'response-size')
        # With an already bounded length, the unqualified read enforces exact
        # length via HTTPResponse's safe read and raises on premature EOF.
        return response.read()
    if not encodings:
        body = response.read(limit + 1)  # EOF is the close-delimited terminator.
        _require(len(body) <= limit, 'response-size')
        return body
    _require(len(encodings) == 1 and encodings[0].lower() == 'chunked',
             'response-transfer-encoding')
    # HTTPResponse accepts EOF in trailers and skips chunk CRLF unchecked.
    # Read chunk framing explicitly through the same deadline-bound buffer.
    framing_left, body = limit, bytearray()

    def line():
        nonlocal framing_left
        value = response.fp.readline(min(8193, framing_left + 1))
        _require(len(value) <= min(8192, framing_left) and value.endswith(b'\r\n'),
                 'response-chunk-framing')
        framing_left -= len(value)
        return value[:-2]

    while True:
        size = line().split(b';', 1)[0]
        _require(re.fullmatch(rb'[0-9a-fA-F]+', size) is not None, 'response-chunk-size')
        count = int(size, 16)
        _require(count <= limit - len(body), 'response-size')
        if count == 0:
            while line():  # The empty trailer line is mandatory, even at EOF.
                pass
            return bytes(body)
        chunk = response.fp.read(count)
        _require(len(chunk) == count, 'response-incomplete-body')
        body.extend(chunk)
        _require(response.fp.read(2) == b'\r\n', 'response-chunk-delimiter')
        framing_left -= 2
        _require(framing_left >= 0, 'response-chunk-framing')


class _LiveControl:
    def __init__(self, *, apps, database, raw, fixture, base_url, process, process_guard,
                 client_ip, portable, bounds):
        self.apps, self.database, self.raw, self.fixture = apps, database, raw, fixture
        self.process, self.process_guard, self.client_ip = process, process_guard, client_ip
        self.portable, self.bounds = portable, bounds
        self.endpoint = urlsplit(base_url)
        self.guard = copy.deepcopy(process_guard())
        self.blocker = self.observer = self.request = None
        self.signaled = False
        self.original_statement_timeout = None

    def assert_alive(self):
        _require(self.process.poll() is None, 'process-exited-before-operation-finished')
        _require(self.process_guard() == self.guard, 'process-guard-changed')

    def _connection(self, label):
        connection = self.database.copy(alias='message-lifecycle-' + label)
        try:
            options = connection.settings_dict.setdefault('OPTIONS', {})
            options['connect_timeout'] = max(1, math.ceil(self.bounds.io))
            connection.ensure_connection()
            connection.set_autocommit(True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false), "
                               "set_config('lock_timeout', %s, false)",
                               [str(math.ceil(self.bounds.query * 1000))] * 2)
                cursor.execute('SELECT current_database(), current_user')
                _require(cursor.fetchone() == (self.database.settings_dict['NAME'],
                                              self.database.settings_dict['USER']), 'database-identity')
            return connection
        except BaseException:
            connection.close()
            raise

    def setup(self):
        self.assert_alive()
        # Snapshot queries use the explicitly supplied configured default wrapper.
        # Bound its queries too, then restore its previous session setting.
        with self.database.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            self.original_statement_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('statement_timeout', %s, false)",
                           [str(math.ceil(self.bounds.query * 1000))])
        self.blocker = self._connection('blocker')
        self.observer = self._connection('observer')

    def capture(self):
        deadline = time.monotonic() + self.bounds.completion

        def bounded_query(execute, sql, params, many, context):
            _require(time.monotonic() < deadline, 'snapshot-deadline')
            result = execute(sql, params, many, context)
            _require(time.monotonic() < deadline, 'snapshot-deadline')
            return result

        with self.database.execute_wrapper(bounded_query):
            state = self.portable.snapshot_state(self.apps)
        _require(time.monotonic() < deadline, 'snapshot-deadline')
        _require(isinstance(state, dict) and len(state) == 128, 'requires-all-128-models')
        cached = self.portable.snapshot_cache(self.raw)
        _require(time.monotonic() < deadline, 'snapshot-deadline')
        return state, cached

    def unchanged(self, before):
        after = self.capture()
        self.portable.assert_state_unchanged(before[0], after[0])
        self.portable.assert_cache_unchanged(before[1], after[1], self.fixture['contrasts'])

    @contextmanager
    def hold_owner(self):
        owner_id = self.fixture['authored']['ids']['owner']
        self.blocker.set_autocommit(False)
        try:
            with self.blocker.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
                cursor.execute('SELECT user_id::text, tableoid::oid, ctid::text FROM bakney_user '
                               'WHERE user_id = %s FOR UPDATE', [owner_id])
                rows = cursor.fetchall()
                _require(len(rows) == 1 and rows[0][0] == owner_id, 'exact-owner-row-not-found')
                identity, relation, ctid = rows[0]
                match = re.fullmatch(r'\((\d+),(\d+)\)', ctid)
                _require(match is not None, 'owner-ctid')
                owner = OwnerRow(pid, identity, relation, *map(int, match.groups()))
            yield owner
        finally:
            # Rollback releases ONLY this helper's row lock, without fixture edits.
            self.blocker.rollback()
            self.blocker.set_autocommit(True)

    def activity(self):
        with self.observer.cursor() as cursor:
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute('SELECT pid, pg_blocking_pids(pid), state, wait_event_type, '
                           'xact_start, backend_start, query FROM pg_stat_activity '
                           'WHERE datname = current_database() AND pid <> pg_backend_pid()')
            rows = [dict(zip(('pid', 'blockers', 'state', 'wait', 'xact', 'backend', 'query'), row))
                    for row in cursor.fetchall()]
            cursor.execute("SELECT pid, relation::bigint, page, tuple FROM pg_locks "
                           "WHERE locktype = 'tuple' AND granted")
            tuples = cursor.fetchall()
        for row in rows:
            row['tuples'] = [entry[1:] for entry in tuples if entry[0] == row['pid']]
        return rows

    def wait_blocked(self, owner):
        deadline = time.monotonic() + self.bounds.lock_wait
        while True:
            _require(time.monotonic() < deadline, 'lock-wait-timeout')
            self.assert_alive()
            proof = _blocked(self.activity(), owner)
            _require(time.monotonic() < deadline, 'lock-wait-timeout')
            if proof is not None:
                return proof
            time.sleep(self.bounds.poll)

    def confirm_wait(self, owner, proof):
        # Every observation must show the SAME active transaction still blocked.
        # Elapsed time without this predicate never counts as survival evidence.
        started, observations = time.monotonic(), 0
        deadline = started + self.bounds.survival + self.bounds.query
        while True:
            _require(time.monotonic() < deadline, 'survival-observation-timeout')
            self.assert_alive()
            _same_wait(self.activity(), owner, proof)
            observations += 1
            observed = time.monotonic()
            _require(observed < deadline, 'survival-observation-timeout')
            if observed - started >= self.bounds.survival and observations >= 2:
                return
            time.sleep(self.bounds.poll)

    def open_request(self, case):
        _require(self.request is None, 'previous-socket-still-open')
        host, port = self.endpoint.hostname, self.endpoint.port
        body = json.dumps(case['body'], ensure_ascii=True).encode() if 'body' in case else b''
        headers = {'Host': self.endpoint.netloc, 'Authorization': 'Bearer ' + case['token'],
                   'Accept': 'application/json', 'Accept-Language': 'en',
                   'X-Correlation-ID': case['name'], 'Connection': 'close',
                   'Content-Length': str(len(body))}
        if body:
            headers['Content-Type'] = 'application/json'
        # Selected authored cases have no impersonation/forwarding headers.
        _require(case['headers'] == {}, 'unexpected-authored-headers')
        lines = [f"{case['method']} {case['path']} HTTP/1.1", *[f'{k}: {v}' for k, v in headers.items()], '', '']
        payload = '\r\n'.join(lines).encode('ascii') + body
        self.request = socket.create_connection((host, port), timeout=self.bounds.io)
        self.request.sendall(payload)

    def disconnect(self):
        _require(self.request is not None, 'disconnect-without-request')
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        finally:
            self.close_request()

    def close_request(self):
        if self.request is not None:
            self.request.close()
            self.request = None

    def signal(self):
        self.assert_alive()
        _require(not self.signaled and self.process.pid == self.guard['pid'], 'signal-pid-mismatch')
        self.signaled = True
        # No killpg, pgrep, shell, process name lookup or inferred process identity.
        self.process.send_signal(signal.SIGTERM)

    def listener_stopped(self):
        deadline = time.monotonic() + self.bounds.listener_stop
        while True:
            self.assert_alive()
            remaining = deadline - time.monotonic()
            _require(remaining > 0, 'listener-did-not-stop')
            stopped = False
            try:
                with socket.create_connection((self.endpoint.hostname, self.endpoint.port),
                                              timeout=min(self.bounds.io, remaining)):
                    pass
            except OSError as error:
                if error.errno == errno.ECONNREFUSED:
                    self.assert_alive()
                    stopped = True
                else:
                    raise  # A timeout, DNS error or unreachable host is not drain proof.
            _require(time.monotonic() < deadline, 'listener-did-not-stop')
            if stopped:
                return
            time.sleep(self.bounds.poll)

    def wait_finished(self, proof):
        deadline = time.monotonic() + self.bounds.completion
        while True:
            _require(time.monotonic() < deadline, 'transaction-did-not-finish')
            rows = self.activity()
            current = [row for row in rows if row['pid'] == proof.pid
                       and str(row['backend']) == proof.backend and str(row['xact']) == proof.xact]
            _require(time.monotonic() < deadline, 'transaction-did-not-finish')
            if not current:
                return
            time.sleep(self.bounds.poll)

    def response(self, case):
        _require(self.request is not None, 'missing-drain-response-socket')
        # Bound decoded body size and require an entire HTTP frame. A bounded
        # read alone permits short EOF even under a larger Content-Length.
        stream = _ResponseSocket(self.request, self.bounds.io)
        with http.client.HTTPResponse(stream) as response:
            response.begin()
            body = _response_body(response)
            _require(response.status == case['status'] and json.loads(body) == case['response'],
                     'drain-response')
            _require(response.getheader('Content-Type', '').split(';')[0] == 'application/json',
                     'drain-response-content-type')
            _require(response.getheader('Content-Language') == 'en', 'drain-response-language')
            _require(response.getheader('Set-Cookie') is None, 'drain-response-cookie')
            _require(set(response.getheader('Allow', '').replace(' ', '').split(',')) == {'POST', 'OPTIONS'},
                     'drain-response-allow')
            _require(response.getheader('WWW-Authenticate') is None, 'drain-response-authentication')
            _require(time.monotonic() < stream.deadline, 'response-read-deadline')

    def verify(self, case, before, started, finished):
        return _verify_effect(self.portable, case, before, self.capture(), self.fixture['contrasts'],
                              started=started, finished=finished, client_ip=self.client_ip)

    def clean_exit(self):
        deadline = time.monotonic() + self.bounds.exit
        try:
            code = self.process.wait(timeout=self.bounds.exit)
        except subprocess.TimeoutExpired:
            raise AssertionError('message-lifecycle.process-did-not-exit-cleanly') from None
        _require(time.monotonic() < deadline, 'process-did-not-exit-cleanly')
        _require(code == 0, 'nonzero-process-exit')

    def teardown(self):
        errors = []
        def attempt(operation):
            try:
                operation()
            except BaseException as error:
                errors.append(error)
        attempt(self.close_request)
        if self.blocker is not None:
            attempt(self.blocker.rollback)
            attempt(self.blocker.close)
        if self.observer is not None:
            attempt(self.observer.close)
        # A failed disconnect case leaves the un-signaled parent process owned
        # by the parent. Once we signal it, we must reap it even on assertion failure.
        if self.signaled:
            try:
                self.process.wait(timeout=self.bounds.exit)
            except subprocess.TimeoutExpired:
                attempt(self.process.kill)  # exact same authorized disposable PID
                attempt(lambda: self.process.wait(timeout=self.bounds.io))
            except BaseException as error:
                errors.append(error)
            attempt(lambda: _require(self.process.poll() is not None, 'signaled-process-still-alive'))
        if self.original_statement_timeout is not None:
            def restore():
                with self.database.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, false)",
                                   [self.original_statement_timeout])
            attempt(restore)
        if errors:
            raise BaseExceptionGroup('message lifecycle teardown failed', errors)


def _cases(fixture):
    cases = fixture['runtime']['cases']
    selected = []
    for source_name, suffix in zip(('message-mutations-create-email', 'message-mutations-delete-email',
                                   'message-mutations-create-email'), CASE_ORDER, strict=True):
        matches = [case for case in cases if case['name'] == source_name]
        _require(len(matches) == 1, 'missing-authored-case-' + source_name)
        case = copy.deepcopy(matches[0])
        _require(case['status'] == 200 and case['audit_additional'] is None
                 and case['audit_actor'] == fixture['authored']['ids']['owner']
                 and not case.get('fault') and not case.get('admission'), 'case-not-authorized-owner')
        _require(case['effect']['kind'] == ('delete' if suffix.startswith('delete') else 'create'),
                 'case-effect-kind')
        case['name'] = 'message-mutations-lifecycle-' + suffix
        selected.append(case)
    return selected


def run(*, apps, database, raw, fixture, base_url, process, process_guard, client_ip,
        portable, bounds=Bounds()):
    """Run CREATE/DELETE disconnects then final CREATE SIGTERM drain, serially.

    ``database`` is the connected Django default DatabaseWrapper (not its proxy);
    ``raw`` is the parent's finite-timeout Redis DB1 client; ``process`` is the
    actual owned native Popen. ``client_ip`` must be independently measured by
    the parent. No DSN, process, address, fixture or assertion has a fallback.
    Raises on any missing evidence. Parent fixture_context always owns DB reset.
    """
    _require(__debug__ and sys.version_info[:3] == (3, 13, 16), 'requires-python-3.13.16')
    bounds.validate()
    _require(isinstance(portable, PortableAssertions)
             and all(callable(value) for value in vars(portable).values()), 'portable-assertions-required')
    _require(isinstance(process, subprocess.Popen) and callable(process_guard), 'actual-owned-process-required')
    endpoint = urlsplit(base_url)
    _require(endpoint.scheme == 'http' and endpoint.hostname is not None and endpoint.port is not None
             and endpoint.path in ('', '/') and not endpoint.query and not endpoint.fragment
             and endpoint.username is None and endpoint.password is None, 'explicit-native-http-base-required')
    _require(ipaddress.ip_address(endpoint.hostname).is_loopback, 'native-loopback-listener-required')
    ipaddress.ip_address(client_ip)  # no inferred remote_addr or forwarded-header substitute
    from django.db import connections
    _require(database is connections['default'] and database.connection is not None,
             'explicit-connected-default-database-required')
    config = database.settings_dict
    _require(database.vendor == 'postgresql' and all(config.get(key) for key in ('NAME', 'USER', 'HOST'))
             and config['NAME'].startswith('assozeta_contract_') and config['NAME'].isidentifier(),
             'configured-private-postgresql-required')
    portable.require_private_database()
    _require(getattr(apps, 'ready', False) is True, 'configured-django-apps-required')
    redis_options = raw.connection_pool.connection_kwargs
    _require(redis_options.get('db') == 1 and all(
        type(redis_options.get(key)) in (int, float) and math.isfinite(redis_options[key])
        and 0 < redis_options[key] <= bounds.io for key in ('socket_timeout', 'socket_connect_timeout')),
        'explicit-bounded-redis-db1-required')
    _require(not redis_options.get('retry_on_timeout', False)
             and not redis_options.get('retry_on_error') and redis_options.get('retry') is None,
             'redis-retry-not-bounded')
    pool_timeout = getattr(raw.connection_pool, 'timeout', bounds.io)
    _require(type(pool_timeout) in (int, float) and math.isfinite(pool_timeout)
             and 0 < pool_timeout <= bounds.io, 'redis-pool-wait-not-bounded')
    # First bind runtime to the independent canonical contract. Validating only
    # the supplied pair lets jointly edited expectations and digest pass.
    portable.validate_runtime(fixture['runtime'])
    portable.validate_runtime(fixture['runtime'], fixture['authored'])
    guard = process_guard()
    _require(isinstance(guard, dict) and guard.get('running') is True
             and type(guard.get('pid')) is int and guard['pid'] == process.pid
             and process.pid > 1 and process.pid != os.getpid()
             and guard.get('instance') is not None, 'actual-process-guard-required')
    UUID(fixture['authored']['ids']['owner'])
    control = _LiveControl(apps=apps, database=database, raw=raw, fixture=fixture, base_url=base_url,
                           process=process, process_guard=process_guard, client_ip=client_ip,
                           portable=portable, bounds=bounds)
    _require(control.guard == guard, 'process-guard-changed-during-setup')
    failure, result = None, None
    try:
        control.setup()
        portable.assert_state_unchanged(fixture['baseline_state'], control.capture()[0])
        portable.assert_cache_unchanged(fixture['baseline_cache'], control.capture()[1], fixture['contrasts'])
        cases = _cases(fixture)
        results = []
        for case, mode in zip(cases, ('disconnect', 'disconnect', 'drain'), strict=True):
            results.append(_run_case(control, case, mode))
        result = dict(success=True, backend='go', transport='native-actual-http', cases=results,
                      process_pid=process.pid, process_exit=process.returncode,
                      fixture_reset_owned_by_parent=True)
    except BaseException as error:
        failure = error
    try:
        control.teardown()
    except BaseException as cleanup_error:
        if failure is not None:
            raise BaseExceptionGroup('message lifecycle acceptance and cleanup failed', [failure, cleanup_error])
        raise
    if failure is not None:
        raise failure
    return result
