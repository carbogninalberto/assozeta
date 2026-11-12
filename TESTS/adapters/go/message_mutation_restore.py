"""Inert, parent-owned packaged-Go restore acceptance over actual HTTP.

One case per fresh existing fixture_context. No services, discovery, subprocesses,
signals, host flock assumptions, fixture seeding or fallback backend.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import copy
import http.client
import ipaddress
import json
import math
import re
import socket
import time
from urllib.parse import urlsplit
from uuid import UUID

from TESTS.adapters.go.message_mutation_lifecycle import (
    OwnerRow, PortableAssertions, WaitProof, _blocked, _same_wait,
    _verify_effect, _ResponseSocket, _response_body,
)


CASE_NAMES = ('create-lease', 'delete-lease', 'create-running', 'delete-running')


def _require(condition, code):
    if not condition:
        raise AssertionError('message-restore.' + code)


@dataclass(frozen=True)
class Bounds:
    lock_wait: float = 5
    survival: float = .25
    completion: float = 10
    io: float = 2
    query: float = 2
    probe: float = 2
    poll: float = .02
    request: float = 20

    def validate(self):
        for value in vars(self).values():
            _require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 20,
                     'invalid-bound')
        _require(.1 <= self.survival <= 2 and self.poll <= self.survival / 2,
                 'survival-observation-bound')
        _require(max(self.io, self.query, self.probe, self.lock_wait, self.completion) < self.request,
                 'request-bound')
        return self


def _probe(exclusive_probe, expected, timeout):
    """Parent must bound real in-container exec; False means contention ONLY."""
    deadline = time.monotonic() + timeout
    acquired = exclusive_probe(timeout=timeout)
    _require(time.monotonic() < deadline, 'probe-deadline')
    _require(type(acquired) is bool, 'probe-result-must-be-bool')
    _require(acquired is expected, 'exclusive-probe-expected-' + str(expected))


def _case(fixture, name, validate_runtime):
    _require(name in CASE_NAMES, 'unknown-case')
    # Validate independently BEFORE selecting or copying expectations. The supplied
    # authored/runtime pair cannot redefine the canonical portable contract.
    validate_runtime(fixture['runtime'])
    validate_runtime(fixture['runtime'], fixture['authored'])
    operation, mode = name.split('-')
    source_name = ('message-mutations-' + operation + '-email' if mode == 'lease' else
                   'message-mutations-restore-pending-owner-' + operation)
    matches = [case for case in fixture['runtime']['cases'] if case['name'] == source_name]
    _require(len(matches) == 1, 'missing-authored-case')
    case = copy.deepcopy(matches[0])
    _require(case['token'] and case['headers'] == {} and case['audit_additional'] is None
             and case['audit_actor'] == fixture['authored']['ids']['owner']
             and not case.get('fault'), 'requires-authored-owner-case')
    _require(case['effect']['kind'] == (operation if mode == 'lease' else 'none'), 'case-effect')
    _require(case['status'] == (200 if mode == 'lease' else 503), 'case-status')
    _require(case.get('admission') == (None if mode == 'lease' else 'pending'), 'case-admission')
    case['name'] = 'message-mutations-restore-' + name
    return case


def _response(request, case, timeout, *, on_complete=None):
    stream = _ResponseSocket(request, timeout)
    with http.client.HTTPResponse(stream) as response:
        response.begin()
        body = _response_body(response)
        if on_complete is not None:
            on_complete()
        _require(response.status == case['status'] and json.loads(body) == case['response'], 'response')
        _require(response.getheader('Content-Type', '').split(';')[0] == 'application/json', 'response-type')
        _require(response.getheader('Content-Language') == 'en', 'response-language')
        _require(response.getheader('Set-Cookie') is None, 'response-cookie')
        _require(response.getheader('WWW-Authenticate') is None, 'response-authentication')
        if case['status'] == 503:
            _require(response.getheader('Retry-After') == '10' and response.getheader('Allow') is None,
                     'response-admission')
        else:
            allowed = {'POST', 'OPTIONS'} if case['path'].endswith('/add') else {'DELETE', 'OPTIONS'}
            _require(set(response.getheader('Allow', '').replace(' ', '').split(',')) == allowed,
                     'response-allow')
            _require(response.getheader('Retry-After') is None, 'unexpected-retry-after')
        _require(time.monotonic() < stream.deadline, 'response-deadline')


@contextmanager
def _request_cleanup(control, case):
    """After unwinding the row lock, drain synchronous work before parent reset."""
    failure = None
    try:
        yield
    except BaseException as error:
        failure = error
    errors = []
    for operation in (lambda: control.settle(case), control.close_request):
        try:
            operation()
        except BaseException as error:
            errors.append(error)
    if failure is not None:
        errors.insert(0, failure)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup('message restore request and cleanup failed', errors)


def _run_lease(control, case):
    with _request_cleanup(control, case):
        control.probe(True)  # An always-False or wrong-inode busy probe cannot pass.
        before, started = control.capture(), time.time()
        with control.hold_owner() as owner:
            control.open_request(case)
            proof = control.wait_blocked(owner)
            _require(isinstance(proof, WaitProof), 'missing-owner-wait-proof')
            control.unchanged(before)
            control.confirm_busy(owner, proof)
            control.unchanged(before)
        control.wait_finished(proof)
        control.response(case)
        effects = control.verify(case, before, started, time.time())
        control.probe(True)
        control.assert_alive()
        return dict(name=case['name'], success=True, exact_owner_row=True,
                    blocked_backend=proof.pid, shared_lease_while_blocked=True,
                    exclusive_after_completed_effects=True, **effects)


def _run_running(control, case):
    with _request_cleanup(control, case):
        control.probe(True)
        before = control.capture()
        with control.running_row():
            admitted, started = control.capture(), time.time()
            # Cleanup settles a request before removing its admission row, even
            # for an assertion failure or KeyboardInterrupt during the read.
            with _request_cleanup(control, case):
                control.open_request(case)
                control.response(case)
                effects = control.verify(case, admitted, started, time.time())
                control.unchanged(admitted)
                control.probe(True)
        control.unchanged(before)
        control.assert_alive()
        return dict(name=case['name'], success=True, running_admission=True,
                    running_row_removed=True, **effects)


class _LiveControl:
    def __init__(self, *, apps, database, raw, fixture, base_url, process_guard,
                 client_ip, portable, exclusive_probe, bounds):
        self.apps, self.database, self.raw, self.fixture = apps, database, raw, fixture
        self.process_guard, self.client_ip = process_guard, client_ip
        self.portable, self.exclusive_probe, self.bounds = portable, exclusive_probe, bounds
        self.endpoint, self.guard = urlsplit(base_url), copy.deepcopy(process_guard())
        self.blocker = self.observer = self.request = self.proof = None
        self.original_timeouts = None
        self.request_deadline = None
        self.response_complete = False

    def assert_alive(self):
        _require(self.process_guard() == self.guard, 'process-guard-changed')

    def check_request_deadline(self):
        if self.request_deadline is not None:
            _require(time.monotonic() < self.request_deadline, 'request-deadline')

    def probe(self, expected):
        self.assert_alive()
        self.check_request_deadline()
        timeout = self.bounds.probe
        if self.request_deadline is not None:
            timeout = min(timeout, self.request_deadline - time.monotonic())
        _require(timeout > 0, 'request-deadline')
        _probe(self.exclusive_probe, expected, timeout)
        self.check_request_deadline()
        self.assert_alive()

    def _connection(self, label):
        connection = self.database.copy(alias='message-restore-' + label)
        try:
            options = copy.deepcopy(connection.settings_dict.get('OPTIONS', {}))
            options['connect_timeout'] = max(1, math.ceil(self.bounds.io))
            connection.settings_dict['OPTIONS'] = options
            connection.ensure_connection()
            connection.set_autocommit(True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false), "
                               "set_config('lock_timeout', %s, false)",
                               [str(math.ceil(self.bounds.query * 1000))] * 2)
                cursor.execute('SELECT current_database(), current_user, pg_backend_pid()')
                name, user, pid = cursor.fetchone()
                _require((name, user) == (self.database.settings_dict['NAME'],
                                         self.database.settings_dict['USER']), 'database-identity')
                _require(type(pid) is int and pid > 0, 'database-pid')
            return connection
        except BaseException:
            connection.close()
            raise

    def setup(self):
        self.assert_alive()
        with self.database.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            statement = cursor.fetchone()[0]
            cursor.execute('SHOW lock_timeout')
            lock = cursor.fetchone()[0]
            self.original_timeouts = statement, lock
            cursor.execute("SELECT set_config('statement_timeout', %s, false), "
                           "set_config('lock_timeout', %s, false)",
                           [str(math.ceil(self.bounds.query * 1000))] * 2)
        self.blocker = self._connection('blocker')
        self.observer = self._connection('observer')
        with self.database.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            default_pid = cursor.fetchone()[0]
        pids = [default_pid]
        for connection in (self.blocker, self.observer):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pids.append(cursor.fetchone()[0])
        _require(len(set(pids)) == 3, 'requires-three-distinct-connections')

    def capture(self):
        self.check_request_deadline()
        deadline = time.monotonic() + self.bounds.completion

        def bounded(execute, sql, params, many, context):
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'snapshot-deadline')
            result = execute(sql, params, many, context)
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'snapshot-deadline')
            return result

        with self.database.execute_wrapper(bounded):
            state = self.portable.snapshot_state(self.apps)
        _require(isinstance(state, dict) and len(state) == 128, 'requires-all-128-models')
        cached = self.portable.snapshot_cache(self.raw)
        self.check_request_deadline()
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
            yield OwnerRow(pid, identity, relation, *map(int, match.groups()))
        finally:
            try:
                self.blocker.rollback()
                self.blocker.set_autocommit(True)
            except BaseException as error:
                # Release this connection before request cleanup attempts to
                # drain work, even if ROLLBACK itself was interrupted/failed.
                try:
                    self.blocker.close()
                except BaseException as close_error:
                    raise BaseExceptionGroup('message restore blocker release failed', [error, close_error])
                raise

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
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'lock-wait-timeout')
            self.assert_alive()
            proof = _blocked(self.activity(), owner)
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'lock-wait-timeout')
            if proof is not None:
                self.proof = proof
                return proof
            time.sleep(self.bounds.poll)

    def confirm_busy(self, owner, proof):
        started, observations = time.monotonic(), 0
        deadline = started + self.bounds.survival + 2 * self.bounds.query + self.bounds.probe
        while True:
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'busy-observation-timeout')
            self.assert_alive()
            _same_wait(self.activity(), owner, proof)
            self.probe(False)
            # A transaction that vanished while exec ran is not lease evidence.
            _same_wait(self.activity(), owner, proof)
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'busy-observation-timeout')
            observations += 1
            if time.monotonic() - started >= self.bounds.survival and observations >= 2:
                return
            time.sleep(self.bounds.poll)

    def open_request(self, case):
        _require(self.request is None, 'previous-request-open')
        self.assert_alive()
        self.request_deadline = time.monotonic() + self.bounds.request
        body = json.dumps(case['body'], ensure_ascii=True).encode() if 'body' in case else b''
        headers = {'Host': self.endpoint.netloc, 'Authorization': 'Bearer ' + case['token'],
                   'Accept': 'application/json', 'Accept-Language': 'en',
                   'X-Correlation-ID': case['name'], 'Connection': 'close',
                   'Content-Length': str(len(body))}
        if body:
            headers['Content-Type'] = 'application/json'
        _require(case['headers'] == {}, 'unexpected-authored-headers')
        lines = [f"{case['method']} {case['path']} HTTP/1.1",
                 *[f'{key}: {value}' for key, value in headers.items()], '', '']
        payload = '\r\n'.join(lines).encode('ascii') + body
        self.request = socket.create_connection((self.endpoint.hostname, self.endpoint.port), timeout=self.bounds.io)
        self.request.sendall(payload)
        self.check_request_deadline()

    def response(self, case):
        _require(self.request is not None, 'missing-response-socket')
        self.check_request_deadline()
        timeout = min(self.bounds.io, self.request_deadline - time.monotonic())
        _require(timeout > 0, 'request-deadline')
        # A complete but incorrect response must not be parsed a second time.
        # An incomplete frame still makes cleanup fail; it cannot certify reset.
        def complete():
            self.response_complete = True
        _response(self.request, case, timeout, on_complete=complete)
        self.check_request_deadline()

    def wait_finished(self, proof):
        deadline = time.monotonic() + self.bounds.completion
        while True:
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'transaction-did-not-finish')
            self.assert_alive()
            rows = self.activity()
            current = [row for row in rows if row['pid'] == proof.pid
                       and str(row['backend']) == proof.backend and str(row['xact']) == proof.xact]
            self.check_request_deadline()
            _require(time.monotonic() < deadline, 'transaction-did-not-finish')
            if not current:
                return
            time.sleep(self.bounds.poll)

    def verify(self, case, before, started, finished):
        return _verify_effect(self.portable, case, before, self.capture(), self.fixture['contrasts'],
                              started=started, finished=finished, client_ip=self.client_ip)

    @contextmanager
    def running_row(self):
        model = self.apps.get_model('instance', 'DataRestore')
        _require('running' in model.ACTIVE, 'running-not-active')
        _require(not model.objects.filter(state__in=model.ACTIVE).exists(), 'existing-active-restore')
        row = model(owner_id=self.fixture['authored']['ids']['owner'],
                    association_id=self.fixture['authored']['ids']['own_association'],
                    state='running', sha256='0' * 64)
        # UUID exists before INSERT, so even an interruption after commit cleans
        # up this exact receipt. Bulk create bypasses model save side effects.
        _require(row.pk is not None and not model.objects.filter(pk=row.pk).exists(), 'restore-row-collision')
        try:
            model.objects.bulk_create([row])
            _require(model.objects.filter(pk=row.pk, state='running', completed_at__isnull=True).count() == 1,
                     'running-row-not-persisted')
            yield
        finally:
            model.objects.filter(pk=row.pk).delete()
            _require(not model.objects.filter(pk=row.pk).exists(), 'running-row-not-removed')

    def settle(self, case):
        if self.request is None:
            return
        # Cleanup has its own finite budget after the blocker is released. The
        # acceptance deadline is never extended to turn a failure into success.
        previous_deadline = self.request_deadline
        self.request_deadline = time.monotonic() + self.bounds.completion
        errors = []
        try:
            if not self.response_complete:
                try:
                    self.response(case)
                except BaseException as error:
                    errors.append(error)
            if self.proof is not None:
                try:
                    self.wait_finished(self.proof)
                except BaseException as error:
                    errors.append(error)
        finally:
            self.request_deadline = previous_deadline
        if errors:
            raise BaseExceptionGroup('message restore request could not settle', errors)

    def close_request(self):
        if self.request is not None:
            self.request.close()
            self.request = None

    def teardown(self):
        errors = []
        def attempt(operation):
            try:
                operation()
            except BaseException as error:
                errors.append(error)
        # Never signal, poll, wait, kill or otherwise consume the parent's service.
        if self.blocker is not None:
            attempt(self.blocker.rollback)
            attempt(self.blocker.close)
        attempt(self.close_request)
        if self.observer is not None:
            attempt(self.observer.close)
        if self.original_timeouts is not None:
            def restore():
                with self.database.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, false), "
                                   "set_config('lock_timeout', %s, false)", list(self.original_timeouts))
            attempt(restore)
        if errors:
            raise BaseExceptionGroup('message restore teardown failed', errors)


def run(*, case, apps, database, raw, fixture, base_url, process_guard, client_ip,
        portable, exclusive_probe, bounds=Bounds()):
    """Run ONE CASE_NAMES entry in a fresh parent-owned fixture_context.

    exclusive_probe(*, timeout) returns bool acquired-and-released, False ONLY on
    contention, and raises for every other failure. Parent executes and validates
    actual /usr/bin/flock inside its owned Go container on /operations/lifecycle.lock.
    No Popen, service launch, environment discovery or process consumption.
    """
    _require(__debug__, 'assertions-required')
    bounds.validate()
    _require(isinstance(portable, PortableAssertions)
             and all(callable(value) for value in vars(portable).values()), 'portable-assertions-required')
    _require(callable(process_guard) and callable(exclusive_probe), 'explicit-parent-callables-required')
    selected = _case(fixture, case, portable.validate_runtime)
    endpoint = urlsplit(base_url)
    _require(endpoint.scheme == 'http' and endpoint.hostname is not None and endpoint.port is not None
             and endpoint.path in ('', '/') and not endpoint.query and not endpoint.fragment
             and endpoint.username is None and endpoint.password is None, 'explicit-http-base-required')
    # Parent publishes the owned container port to a numeric loopback endpoint.
    _require(ipaddress.ip_address(endpoint.hostname).is_loopback, 'loopback-listener-required')
    ipaddress.ip_address(client_ip)  # Independently measured by parent, never inferred here.
    from django.db import connections
    _require(database is connections['default'] and database.connection is not None,
             'explicit-connected-default-database-required')
    config = database.settings_dict
    _require(database.vendor == 'postgresql' and all(config.get(key) for key in ('NAME', 'USER', 'HOST'))
             and config['NAME'].startswith('assozeta_contract_') and config['NAME'].isidentifier(),
             'configured-private-postgresql-required')
    portable.require_private_database()
    _require(database.get_autocommit() and not database.in_atomic_block, 'autocommit-required')
    _require(getattr(apps, 'ready', False) is True, 'configured-apps-required')
    options = raw.connection_pool.connection_kwargs
    _require(options.get('db') == 1 and all(
        type(options.get(key)) in (int, float) and math.isfinite(options[key])
        and 0 < options[key] <= bounds.io for key in ('socket_timeout', 'socket_connect_timeout')),
        'explicit-bounded-redis-db1-required')
    _require(not options.get('retry_on_timeout', False) and not options.get('retry_on_error')
             and options.get('retry') is None, 'redis-retry-not-bounded')
    pool_timeout = getattr(raw.connection_pool, 'timeout', bounds.io)
    _require(type(pool_timeout) in (int, float) and math.isfinite(pool_timeout)
             and 0 < pool_timeout <= bounds.io, 'redis-pool-wait-not-bounded')
    guard = process_guard()
    _require(isinstance(guard, dict) and guard.get('running') is True
             and type(guard.get('pid')) is int and guard['pid'] > 0
             and guard.get('instance') is not None, 'actual-process-guard-required')
    UUID(fixture['authored']['ids']['owner'])
    control = _LiveControl(apps=apps, database=database, raw=raw, fixture=fixture, base_url=base_url,
        process_guard=process_guard, client_ip=client_ip, portable=portable,
        exclusive_probe=exclusive_probe, bounds=bounds)
    _require(control.guard == guard, 'process-guard-changed-during-setup')
    failure, result = None, None
    try:
        control.setup()
        control.unchanged((fixture['baseline_state'], fixture['baseline_cache']))
        result = (_run_lease if case.endswith('lease') else _run_running)(control, selected)
        result.update(backend='go', transport='packaged-actual-http', fixture_reset_owned_by_parent=True)
    except BaseException as error:
        failure = error
    try:
        control.teardown()
    except BaseException as error:
        if failure is not None:
            raise BaseExceptionGroup('message restore acceptance and teardown failed', [failure, error])
        raise
    if failure is not None:
        raise failure
    return result
