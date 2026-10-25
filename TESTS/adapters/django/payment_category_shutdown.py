"""Native Go process-shutdown acceptance for admitted payment-category DELETE.

The two cases use real PostgreSQL row-lock waiters. They do not replace the
server clock, timeout, process, listener or database transaction. This helper
proves the observable owned-process, socket and PostgreSQL outcomes. It does
not instrument the internal ``server.Close()`` call in the drain-timeout branch.
Django process shutdown, packaged/Caddy shutdown, wider operations and
interruption/switching remain unproven.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import time
from urllib import error as urllib_error
from urllib.parse import urlsplit
import uuid

from TESTS.adapters.go.payment_category_server import native_server
from TESTS.http_client import Client


ROOT = Path(__file__).resolve().parents[3]
DRAIN_SECONDS = 25
# Separate bounded transaction/driver cleanup after the admission drain.
EXIT_CLEANUP_TOLERANCE_SECONDS = 6.5
MODEL_COUNT = 128
CATEGORY_MODEL = 'application.PaymentCategory'
AUDIT_MODELS = ('auditlog.LogEntry', 'application.AuditLogIndex')
EXPECTED_CASES = (
    'sigterm_graceful_drain_commits_admitted_delete',
    'sigterm_expired_drain_rolls_back_before_blocker_release',
)
DRAIN_DEADLINE_DIAGNOSTIC = 'HTTP drain deadline exceeded'
TRANSPORT_FAILURES = (
    urllib_error.URLError,
    http.client.HTTPException,
    ConnectionError,
    socket.timeout,
)

# Behavior-bearing native sources, build inputs and the fixture's oracle/context.
# The helper hashes all of these both before and after the run.
SOURCE_PATHS = (
    'BE_GO/go.mod',
    'BE_GO/go.sum',
    'BE_GO/cmd/server/main.go',
    'BE_GO/cmd/server/login.go',
    'BE_GO/internal/config/config.go',
    'BE_GO/internal/handler/authenticated_rest.go',
    'BE_GO/internal/handler/payment_categories.go',
    'BE_GO/internal/middleware/session_audit.go',
    'BE_GO/internal/service/identity.go',
    'BE_GO/internal/service/payment_categories.go',
    'BE_GO/internal/repository/accounts.go',
    'BE_GO/internal/repository/identity.go',
    'BE_GO/internal/repository/payment_categories.go',
    'BE_GO/internal/repository/model_audit.go',
    'BE_GO/internal/repository/transaction.go',
    'BE_GO/internal/domain/payment_categories.go',
    'BE_GO/internal/dto/payment_categories.go',
    'BE_GO/pkg/jwt/jwt.go',
    'BE_GO/pkg/jwt/canonical.go',
    'TESTS/adapters/go/server.py',
    'TESTS/adapters/go/payment_category_server.py',
    'TESTS/adapters/django/accounts.py',
    'TESTS/adapters/django/login_jpeg.py',
    'TESTS/adapters/django/production_runtime.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/adapters/django/payment_category_shutdown.py',
    'TESTS/http_client.py',
    'BE/application/models/audit_models.py',
    'BE/application/models/payment_models.py',
    'BE/application/models/user_models.py',
)


def _source_hashes():
    paths = {ROOT / name for name in SOURCE_PATHS}
    paths.update((ROOT / 'BE').rglob('*.py'))
    paths.update(p for p in (ROOT / 'BE_GO').rglob('*')
                 if p.is_file() and p.suffix in ('.go', '.mod', '.sum', '.c', '.h', '.pem'))
    return {str(p.relative_to(ROOT)): _sha256(p) for p in sorted(paths)}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _category_id(number):
    assert 197000 <= number <= 197999
    return f'00000000-0000-4000-8000-{number:012d}'


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    return value


def _write_evidence(path, evidence):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(_jsonable(evidence), indent=2, sort_keys=True, default=str) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _log_mark(log):
    log.flush()
    path = Path(log.name)
    return {'path': str(path), 'offset': path.stat().st_size}


def _log_has_line_after(mark, expected):
    path = Path(mark['path'])
    with path.open('rb') as stream:
        stream.seek(mark['offset'])
        content = stream.read().decode('utf-8', errors='replace')
    return any(line.rstrip().endswith(expected) for line in content.splitlines())


def _attempt(results, failures, name, operation):
    try:
        result = operation()
        if result is False:
            raise AssertionError(f'{name}: verification returned False')
    except BaseException as error:
        results[name] = False
        failures.append(f'{name}: {type(error).__name__}: {error}')
        return False
    results[name] = True
    return True


def _private_database_guard(work, database):
    assert work.name.startswith('az-accounts-') and work.parent == Path('/tmp'), (
        'payment-category shutdown fixture requires /tmp/az-accounts-*', work,
    )
    assert database['NAME'] == 'assozeta_contract_accounts', (
        'payment-category shutdown fixture database guard', database['NAME'],
    )
    assert str(database['HOST']).startswith('/tmp/az-accounts-'), (
        'payment-category shutdown fixture socket guard', database['HOST'],
    )


def _wait(predicate, description, timeout, not_before=None):
    deadline = time.monotonic() + timeout
    latest = None
    while True:
        latest = predicate()
        if latest:
            return latest
        if not_before is not None and time.monotonic() >= not_before:
            return latest
        if time.monotonic() >= deadline:
            raise AssertionError(f'timed out waiting for {description}: {latest!r}')
        time.sleep(.01)


def run(*, work, log, tool_env, address, owner_id, association_id, token, snapshot,
        evidence_path):
    import psycopg2

    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.conf import settings
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection
    from application.models import PaymentCategory, SportAssociation, User
    from application.models.audit_models import AuditLogIndex

    work = Path(work)
    evidence_path = Path(evidence_path)
    started = time.monotonic()
    owner_id, association_id = str(owner_id), str(association_id)
    evidence = {
        'success': False,
        'scope': 'native Go payment-category DELETE process shutdown on private PostgreSQL',
        'contract': (
            'observable owned process, HTTP socket, PostgreSQL backend and persisted state; '
            'the internal drain-timeout server.Close() call is not instrumented'
        ),
        'expected_cases': list(EXPECTED_CASES),
        'cases': [],
        'internal_close_call_proven': False,
        'complete_payment_category_parity': False,
        'django_process_shutdown_proven': False,
        'packaged_caddy_shutdown_proven': False,
        'wider_operations_proven': False,
        'interruption_and_switching_proven': False,
        'cleanup_verified': False,
        'source_stable': False,
        'native_binary_all_four_matches': False,
        'processes': [],
        'source_sha256': _source_hashes(),
        'started_at': _utc_now(),
    }
    _write_evidence(evidence_path, evidence)

    database = settings.DATABASES['default']
    _private_database_guard(work, database)
    initial_snapshot = snapshot()
    assert len(initial_snapshot) == MODEL_COUNT, (
        'caller must supply the complete managed snapshot', len(initial_snapshot),
    )
    owner = User.original_objects.filter(pk=owner_id).values(
        'email', 'first_name', 'is_active', 'deleted',
    ).get()
    association_owner = str(
        SportAssociation.original_objects.filter(pk=association_id).values_list(
            'user_id', flat=True,
        ).get()
    )
    assert association_owner == owner_id, 'shutdown fixture owner does not own association'
    category_content_type = ContentType.objects.get_for_model(PaymentCategory)

    process_records = []
    process_objects = []
    binary_hashes = set()

    @contextmanager
    def observed_server(label):
        observed = []

        def observe(process):
            executable = Path(process.args[0])
            digest = _sha256(executable)
            binary_hashes.add(digest)
            record = {
                'label': label,
                'pid': process.pid,
                'binary': str(executable),
                'binary_sha256': digest,
                'ready_observed_at': _utc_now(),
                'log_mark': _log_mark(log),
            }
            process_records.append(record)
            process_objects.append(process)
            observed.append((process, record))

        process = None
        record = None
        try:
            with native_server(
                work, log, tool_env, address, process_observer=observe,
            ) as runtime:
                assert len(observed) == 1, (
                    'native server must expose exactly one owned process after readiness'
                )
                process, record = observed[0]
                assert all(
                    name in runtime
                    for name in ('base_url', 'proxy_upstream', 'network', 'metadata')
                )
                yield runtime, process, record
        finally:
            if process is not None:
                record['exit_status'] = process.poll()
                record['exit_observed_at'] = _utc_now()

    def cleanup_process(process, results, failures, prefix='owned_process'):
        if process is None:
            results[f'{prefix}_stopped'] = False
            failures.append(f'{prefix}: process was never observed')
            return
        if process.poll() is None:
            _attempt(
                results, failures, f'{prefix}_terminate',
                lambda: process.terminate(),
            )
            if process.poll() is None:
                _attempt(
                    results, failures, f'{prefix}_wait_after_terminate',
                    lambda: process.wait(timeout=5),
                )
        if process.poll() is None:
            _attempt(
                results, failures, f'{prefix}_kill',
                lambda: process.kill(),
            )
        if process.poll() is None:
            _attempt(
                results, failures, f'{prefix}_wait_after_kill',
                lambda: process.wait(timeout=5),
            )
        results[f'{prefix}_stopped'] = process.poll() is not None
        if not results[f'{prefix}_stopped']:
            failures.append(f'{prefix}: still running after termination attempts')

    def db_connect():
        return psycopg2.connect(
            dbname=database['NAME'], user=database['USER'],
            password=database['PASSWORD'], host=database['HOST'],
            port=database['PORT'],
        )

    observer_connection = db_connect()
    observer_connection.autocommit = True

    def activity(backend=None, pid=None):
        with observer_connection.cursor() as cursor:
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute(
                '''SELECT pid, backend_start, xact_start, application_name, state,
                          wait_event_type, wait_event, query, pg_blocking_pids(pid)
                   FROM pg_stat_activity
                   WHERE datname=current_database() AND pid<>pg_backend_pid()
                   ORDER BY pid'''
            )
            rows = [
                dict(zip(
                    (
                        'pid', 'backend_start', 'xact_start', 'application_name',
                        'state', 'wait_event_type', 'wait_event', 'query', 'blockers',
                    ),
                    row,
                ))
                for row in cursor.fetchall()
            ]
        if backend is not None:
            return [
                row for row in rows
                if row['pid'] == backend['pid']
                and row['backend_start'] == backend['backend_start']
            ]
        return [row for row in rows if pid is None or row['pid'] == pid]

    def ungranted_locks(backend):
        with observer_connection.cursor() as cursor:
            cursor.execute(
                '''SELECT l.locktype, l.mode, l.granted,
                          CASE WHEN l.relation IS NULL THEN NULL
                               ELSE l.relation::regclass::text END
                   FROM pg_locks l
                   JOIN pg_stat_activity a
                     ON a.pid=l.pid AND a.datname=current_database()
                   WHERE a.pid=%s AND a.backend_start=%s AND NOT l.granted
                   ORDER BY l.locktype, l.mode''',
                [backend['pid'], backend['backend_start']],
            )
            return [
                dict(zip(('locktype', 'mode', 'granted', 'relation'), row))
                for row in cursor.fetchall()
            ]

    def backend_identity_ended(backend):
        return not activity(backend=backend)

    def wait_category_lock(blocker, description):
        def read():
            rows = [
                row for row in activity()
                if blocker['pid'] in row['blockers']
                and row['wait_event_type'] == 'Lock'
                and row['xact_start'] is not None
                and 'application_paymentcategory' in row['query']
                and 'FOR UPDATE' in row['query'].upper()
            ]
            return rows[0] if len(rows) == 1 else None

        row = _wait(read, description, timeout=10)
        assert len(activity(backend=row)) == 1, ('blocked backend disappeared', row)
        return row

    def listen_closed(port):
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=.25)
        except OSError as error:
            if error.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
                return {
                    'closed': True,
                    'error': type(error).__name__,
                    'errno': error.errno,
                }
            return {
                'closed': False,
                'error': type(error).__name__,
                'errno': error.errno,
            }
        connection.close()
        return {'closed': False, 'error': None, 'errno': None}

    def wait_listener_closed(port, process, future, waiter, seeded):
        def read():
            result = listen_closed(port)
            return result if result['closed'] else None

        observation = _wait(
            read, 'owned listener closure', timeout=10,
        )
        still_admitted = activity(backend=waiter)
        assert len(still_admitted) == 1, (
            'listener stopped after the admitted backend ended', still_admitted,
        )
        assert still_admitted[0]['xact_start'] is not None
        assert waiter['blockers'] and all(
            blocker in still_admitted[0]['blockers']
            for blocker in waiter['blockers']
        ), still_admitted
        assert not future.done(), 'HTTP request ended when only the listener stopped'
        assert process.poll() is None, 'owned process exited before blocker release'
        assert snapshot() == seeded, (
            'managed state changed while the admitted request remained blocked'
        )
        observation.update({
            'request_still_admitted': True,
            'request_future_done': False,
            'blocked_activity': still_admitted[0],
            'managed_state_unchanged': True,
        })
        return observation

    def audit_records(events):
        records = []
        for event in events:
            indexes = list(AuditLogIndex.objects.filter(log_entry=event).order_by('pk'))
            records.append({
                'id': event.pk,
                'content_type_id': event.content_type_id,
                'content_type_app_label': event.content_type.app_label,
                'model': event.content_type.model,
                'object_pk': event.object_pk,
                'object_id': event.object_id,
                'object_repr': event.object_repr,
                'serialized_data': event.serialized_data,
                'action': event.action,
                'changes_text': event.changes_text,
                'changes': event.changes_dict,
                'actor_id': str(event.actor_id),
                'actor_email': event.actor_email,
                'cid': event.cid,
                'remote_addr': event.remote_addr,
                'remote_port': event.remote_port,
                'timestamp': _jsonable(event.timestamp),
                'additional_data': event.additional_data,
                'indexes': [{
                    'audit_log_index_id': str(index.audit_log_index_id),
                    'log_entry_id': index.log_entry_id,
                    'sport_association_id': str(index.sport_association_id),
                    'resolution_path': index.resolution_path,
                    'created_at': _jsonable(index.created_at),
                } for index in indexes],
            })
        return records

    def expected_audit(cid, category_id, changes, timestamp_before, timestamp_after):
        return {
            'stable': {
                'content_type_id': category_content_type.pk,
                'content_type_app_label': category_content_type.app_label,
                'model': category_content_type.model,
                'object_pk': category_id,
                'object_id': None,
                'object_repr': f'PaymentCategory object ({category_id})',
                'serialized_data': None,
                'action': LogEntry.Action.UPDATE,
                'changes_text': '',
                'changes': changes,
                'actor_id': owner_id,
                'actor_email': owner['email'],
                'cid': cid,
                'remote_addr': '127.0.0.1',
                'remote_port': None,
                'additional_data': None,
            },
            'index': {
                'sport_association_id': association_id,
                'resolution_path': 'direct',
            },
            'timestamp_before': timestamp_before,
            'timestamp_after': timestamp_after,
        }

    def assert_authored_audit(event, expected):
        actual = audit_records([event])[0]
        stable = {
            key: value for key, value in actual.items()
            if key not in ('id', 'timestamp', 'indexes')
        }
        assert stable == expected['stable'], (
            'authored audit projection differs', stable, expected['stable'],
        )
        assert expected['timestamp_before'] <= event.timestamp <= expected['timestamp_after'], (
            'audit timestamp outside request/signal bounds',
            event.timestamp,
            expected['timestamp_before'],
            expected['timestamp_after'],
        )
        assert len(actual['indexes']) == 1, ('authored audit index count differs', actual['indexes'])
        index = actual['indexes'][0]
        assert uuid.UUID(index['audit_log_index_id']).version == 4, (
            'authored audit index identifier is not UUIDv4', index,
        )
        assert index['log_entry_id'] == event.pk, ('index points at another audit', index, event.pk)
        assert {
            key: index[key] for key in ('sport_association_id', 'resolution_path')
        } == expected['index'], ('authored audit index projection differs', index)
        assert index['created_at'] == actual['timestamp'], (
            'audit index created_at differs from audit timestamp', index, actual,
        )

    def assert_snapshot(before, category_id, cid_prefix, expected_audits,
                        expected_fields):
        after = snapshot()
        assert len(after) == MODEL_COUNT and set(after) == set(before), (
            'managed snapshot model set changed'
        )
        for label, prior in before.items():
            if label == CATEGORY_MODEL:
                expected = deepcopy(prior)
                matches = [
                    record for record in expected
                    if str(record['payment_category_id']) == category_id
                ]
                assert len(matches) == 1, ('owned category missing from seeded snapshot', matches)
                matches[0].update(expected_fields)
                assert after[label] == expected, (
                    'authored category projection differs', after[label], expected,
                )
            elif label in AUDIT_MODELS:
                assert len(after[label]) == len(prior) + len(expected_audits), (
                    label, 'unexpected audit/index count', len(after[label]), len(prior),
                )
                assert all(record in after[label] for record in prior), (
                    label, 'pre-existing audit/index row changed',
                )
            else:
                assert after[label] == prior, (label, 'unrelated 128-model state changed')
        events = list(LogEntry.objects.filter(cid__startswith=cid_prefix).order_by('cid'))
        expected_cids = sorted(expected['stable']['cid'] for expected in expected_audits)
        assert [event.cid for event in events] == expected_cids, (
            'authored audit CID set differs', [event.cid for event in events], expected_cids,
        )
        for event, expected in zip(events, expected_audits):
            assert_authored_audit(event, expected)
        return after

    @contextmanager
    def fixture(number, label):
        category_id = _category_id(number)
        cid_prefix = f'payment-category-shutdown-{label}-{uuid.uuid4().hex}'
        original = snapshot()
        assert original == initial_snapshot, (
            f'{label}: previous fixture cleanup did not restore the caller snapshot'
        )
        assert PaymentCategory._base_manager.filter(pk=category_id).exists() is False, (
            'shutdown category fixture collision', category_id,
        )
        assert not LogEntry.objects.filter(cid__startswith=cid_prefix).exists(), (
            'shutdown audit fixture collision', cid_prefix,
        )
        body_failure = None
        cleanup_results = {}
        cleanup_failures = []
        try:
            with disable_auditlog():
                PaymentCategory.objects.create(
                    payment_category_id=category_id,
                    name='Shutdown original',
                    sport_association_id=association_id,
                )
            seeded = snapshot()
            assert seeded != original, (
                'fixture category was not visible in the seeded snapshot'
            )
            yield category_id, cid_prefix, seeded, original
        except BaseException as failure:
            body_failure = failure
        finally:
            def delete_audits():
                with disable_auditlog():
                    LogEntry.objects.filter(cid__startswith=cid_prefix).delete()

            def delete_category():
                with disable_auditlog():
                    PaymentCategory._base_manager.filter(pk=category_id).delete()

            _attempt(
                cleanup_results, cleanup_failures,
                'fixture_audits_deleted', delete_audits,
            )
            _attempt(
                cleanup_results, cleanup_failures,
                'fixture_category_deleted', delete_category,
            )
            _attempt(
                cleanup_results, cleanup_failures,
                'fixture_snapshot_restored', lambda: snapshot() == original,
            )
            _attempt(
                cleanup_results, cleanup_failures,
                'fixture_owner_preserved',
                lambda: User.original_objects.filter(pk=owner_id).values(
                    'email', 'first_name', 'is_active', 'deleted',
                ).get() == owner,
            )
        if body_failure is not None:
            if cleanup_failures:
                body_failure.add_note(
                    f'{label} fixture cleanup failures: {cleanup_failures}'
                )
            raise body_failure
        assert not cleanup_failures, (
            f'{label}: shutdown fixture cleanup failed', cleanup_results, cleanup_failures,
        )

    def blocker_connection(category_id):
        blocker = db_connect()
        blocker.autocommit = False
        try:
            with blocker.cursor() as cursor:
                cursor.execute(
                    '''SELECT payment_category_id FROM application_paymentcategory
                       WHERE payment_category_id=%s FOR UPDATE''',
                    [category_id],
                )
                assert cursor.fetchone() == (uuid.UUID(category_id),)
                cursor.execute(
                    '''SELECT pid, backend_start FROM pg_stat_activity
                       WHERE pid=pg_backend_pid()'''
                )
                pid, backend_start = cursor.fetchone()
            identity = {'pid': pid, 'backend_start': backend_start}
            assert len(activity(backend=identity)) == 1, (
                'fixture blocker backend identity is not unique', identity,
            )
            return blocker, identity
        except BaseException:
            try:
                blocker.rollback()
            finally:
                blocker.close()
            raise

    def cleanup_request_resources(blocker, future, executor, process, prefix):
        results = {}
        failures = []
        if blocker is not None:
            _attempt(
                results, failures, 'blocker_rollback_completed',
                blocker.rollback,
            )
            _attempt(
                results, failures, 'blocker_close_completed',
                blocker.close,
            )
        else:
            results['blocker_rollback_completed'] = False
            results['blocker_close_completed'] = False
            failures.append('blocker connection was never observed')
        if future is not None:
            if not future.done():
                try:
                    future.result(timeout=5)
                except BaseException:
                    pass
            results['request_future_finished'] = future.done()
            if not results['request_future_finished']:
                failures.append('request future did not finish during cleanup')
            else:
                try:
                    future.result()
                except BaseException:
                    pass
        else:
            results['request_future_finished'] = False
            failures.append('request future was never observed')
        _attempt(
            results, failures, 'request_thread_stopped',
            lambda: executor.shutdown(wait=True, cancel_futures=True),
        )
        cleanup_process(process, results, failures, prefix=prefix)
        return results, failures

    def graceful(case):
        with fixture(197000, 'graceful') as (
            category_id, cid_prefix, seeded, original,
        ):
            executor = ThreadPoolExecutor(max_workers=1)
            future = None
            blocker = None
            process = None
            request_failure = None
            cleanup = None
            cleanup_failures = []
            try:
                with observed_server('graceful-native') as (
                    runtime, process, process_record,
                ):
                    target = urlsplit(runtime['base_url'])
                    blocker, blocker_identity = blocker_connection(category_id)
                    delete_cid = cid_prefix + '-delete'
                    request_started = datetime.now(timezone.utc)
                    future = executor.submit(
                        Client(runtime['base_url'], token, timeout=35).request,
                        'DELETE', f'/payment/category/{category_id}/delete', None,
                        {'X-Correlation-ID': delete_cid},
                    )
                    waiter = wait_category_lock(
                        blocker_identity, 'graceful DELETE category lock',
                    )
                    case.update({
                        'blocker_backend': blocker_identity,
                        'blocked_backend': waiter,
                        'ungranted_locks': ungranted_locks(waiter),
                    })
                    assert waiter['pid'] != blocker_identity['pid']
                    assert waiter['pid'] != connection.connection.get_backend_pid()
                    assert case['ungranted_locks'], (
                        'graceful DELETE did not expose an ungranted lock'
                    )
                    signal_at = time.monotonic()
                    signal_dt = datetime.now(timezone.utc)
                    case['signal'] = 'SIGTERM'
                    case['signal_sent_at'] = signal_dt.isoformat()
                    process.send_signal(signal.SIGTERM)
                    case['listener'] = wait_listener_closed(
                        target.port or 80, process, future, waiter, seeded,
                    )
                    release_at = time.monotonic()
                    case['signal_to_blocker_release_seconds'] = round(
                        release_at - signal_at, 3,
                    )
                    assert release_at - signal_at < 20, (
                        'blocker was not released comfortably before 25s drain'
                    )
                    release_dt = datetime.now(timezone.utc)
                    blocker.rollback()
                    case['blocker_released_at'] = _utc_now()
                    status, response = future.result(timeout=15)
                    delete_completed = datetime.now(timezone.utc)
                    assert (status, response) == (
                        200, {'msg': 'payment category deleted'},
                    ), (status, response)
                    process.wait(timeout=15)
                    process_record['exit_status'] = process.returncode
                    process_record['exit_observed_at'] = _utc_now()
                    case['exit_status'] = process.returncode
                    assert process.returncode == 0, (
                        'graceful drain exit status', process.returncode,
                    )
                    expected = [expected_audit(
                        delete_cid, category_id, {'deleted': ['False', 'True']},
                        release_dt, delete_completed,
                    )]
                    assert_snapshot(
                        seeded, category_id, cid_prefix, expected, {'deleted': True},
                    )
                    case.update({
                        'status': status,
                        'response': response,
                        'exact_delete_audit_rows': 1,
                        'exact_delete_audit_indexes': 1,
                        'process_exited': True,
                    })
            except BaseException as failure:
                request_failure = failure
            finally:
                cleanup, cleanup_failures = cleanup_request_resources(
                    blocker, future, executor, process, 'graceful_process',
                )
                case['request_cleanup'] = cleanup
                case['request_cleanup_failures'] = cleanup_failures
                case['request_cleanup_verified'] = not cleanup_failures
            if request_failure is not None:
                raise request_failure
            assert not cleanup_failures, (
                'graceful request cleanup failed', cleanup, cleanup_failures,
            )

            recovery_cid = cid_prefix + '-recovery'
            recovery_process = None
            recovery_failure = None
            recovery_cleanup = None
            recovery_cleanup_failures = []
            try:
                with observed_server('graceful-recovery-native') as (
                    runtime, recovery_process, process_record,
                ):
                    recovery_started = datetime.now(timezone.utc)
                    status, response = Client(
                        runtime['base_url'], token, timeout=15,
                    ).request(
                        'PATCH', f'/payment/category/{category_id}/update',
                        {'name': 'Shutdown recovered'},
                        {'X-Correlation-ID': recovery_cid},
                    )
                    completed = datetime.now(timezone.utc)
                    assert status == 200 and response['data']['name'] == 'Shutdown recovered', (
                        status, response,
                    )
                    case['recovery_status'] = status
                    case['recovery_created_at'] = completed.isoformat()
            except BaseException as failure:
                recovery_failure = failure
            finally:
                recovery_cleanup = {}
                cleanup_process(
                    recovery_process, recovery_cleanup,
                    recovery_cleanup_failures, prefix='recovery_process',
                )
                case['recovery_process_cleanup'] = recovery_cleanup
                case['recovery_process_cleanup_failures'] = recovery_cleanup_failures
                case['recovery_process_cleanup_verified'] = not recovery_cleanup_failures
            if recovery_failure is not None:
                raise recovery_failure
            assert not recovery_cleanup_failures, (
                'graceful recovery process cleanup failed',
                recovery_cleanup,
                recovery_cleanup_failures,
            )
            expected = [
                expected_audit(
                    delete_cid, category_id, {'deleted': ['False', 'True']},
                    release_dt, delete_completed,
                ),
                expected_audit(
                    recovery_cid, category_id,
                    {'name': ['Shutdown original', 'Shutdown recovered']},
                    recovery_started, completed,
                ),
            ]
            assert_snapshot(
                seeded, category_id, cid_prefix, expected,
                {'deleted': True, 'name': 'Shutdown recovered'},
            )
            assert process_record['exit_status'] == 0, (
                'fresh recovery process exit status', process_record['exit_status'],
            )
            case['audited_recovery'] = True

    def expired(case):
        with fixture(197001, 'expired') as (
            category_id, cid_prefix, seeded, original,
        ):
            executor = ThreadPoolExecutor(max_workers=1)
            future = None
            blocker = None
            process = None
            request_failure = None
            cleanup = None
            cleanup_failures = []
            try:
                with observed_server('expired-native') as (
                    runtime, process, process_record,
                ):
                    target = urlsplit(runtime['base_url'])
                    blocker, blocker_identity = blocker_connection(category_id)
                    abandoned_cid = cid_prefix + '-abandoned'
                    request_started = datetime.now(timezone.utc)
                    future = executor.submit(
                        Client(runtime['base_url'], token, timeout=40).request,
                        'DELETE', f'/payment/category/{category_id}/delete', None,
                        {'X-Correlation-ID': abandoned_cid},
                    )
                    waiter = wait_category_lock(
                        blocker_identity, 'expired DELETE category lock',
                    )
                    case.update({
                        'blocker_backend': blocker_identity,
                        'blocked_backend': waiter,
                        'ungranted_locks_before_signal': ungranted_locks(waiter),
                    })
                    assert waiter['pid'] != blocker_identity['pid']
                    assert case['ungranted_locks_before_signal'], (
                        'expired DELETE did not expose an ungranted lock'
                    )
                    signal_at = time.monotonic()
                    signal_dt = datetime.now(timezone.utc)
                    drain_deadline = signal_at + DRAIN_SECONDS
                    case['signal'] = 'SIGTERM'
                    case['signal_sent_at'] = signal_dt.isoformat()
                    process.send_signal(signal.SIGTERM)
                    case['listener'] = wait_listener_closed(
                        target.port or 80, process, future, waiter, seeded,
                    )
                    pre_deadline = drain_deadline - .5
                    _wait(
                        lambda: False,
                        'observation immediately before the 25-second drain deadline',
                        timeout=max(0, pre_deadline - time.monotonic()),
                        not_before=pre_deadline,
                    )
                    case['alive_before_drain_deadline'] = process.poll() is None
                    try:
                        process.wait(timeout=10)
                    except BaseException as failure:
                        case['exit_wait_failure'] = (
                            f'{type(failure).__name__}: {failure}'
                        )
                    exit_at = time.monotonic()
                    process_record['exit_status'] = process.returncode
                    process_record['exit_observed_at'] = _utc_now()
                    case.update({
                        'exit_status': process.returncode,
                        'signal_to_exit_seconds': round(exit_at - signal_at, 3),
                    })
                    assert _log_has_line_after(
                        process_record['log_mark'], DRAIN_DEADLINE_DIAGNOSTIC,
                    ), 'actual HTTP drain-deadline diagnostic was not logged'
                    case['drain_deadline_log_observed'] = True
                    case['internal_close_call_proven'] = False
                    case['internal_close_call_disposition'] = (
                        'source inspection only; not distinguished from os.Exit cleanup '
                        'by this process/socket/database fixture'
                    )

                    _wait(
                        lambda: False,
                        'blocker release after the real 25-second drain deadline',
                        timeout=max(0, drain_deadline - time.monotonic() + .01),
                        not_before=drain_deadline,
                    )
                    case['blocker_held_through_seconds'] = round(
                        time.monotonic() - signal_at, 3,
                    )
                    backend_deadline = time.monotonic() + 5
                    while (
                        time.monotonic() < backend_deadline
                        and not backend_identity_ended(waiter)
                    ):
                        time.sleep(.01)
                    case['backend_ended_before_blocker_release'] = (
                        backend_identity_ended(waiter)
                    )
                    case['activity_before_blocker_release'] = activity(backend=waiter)
                    case['ungranted_locks_before_blocker_release'] = ungranted_locks(
                        waiter,
                    )
                    case['listener_after_process_exit'] = listen_closed(target.port or 80)
                    pre_release = snapshot()
                    case['model_count_before_blocker_release'] = len(pre_release)
                    case['all_models_unchanged_before_blocker_release'] = (
                        pre_release == seeded
                    )

                    if not future.done():
                        try:
                            future.result(timeout=5)
                        except BaseException as failure:
                            case['expired_future_wait_error'] = (
                                f'{type(failure).__name__}: {failure}'
                            )
                    assert future.done(), (
                        'expired HTTP client future was not finished before blocker release'
                    )
                    case['request_future_done_before_blocker_release'] = True
                    try:
                        abandoned_result = future.result()
                    except TRANSPORT_FAILURES as failure:
                        case['abandoned_request_transport_failure'] = (
                            f'{type(failure).__name__}: {failure}'
                        )
                    except BaseException as failure:
                        raise AssertionError(
                            'expired HTTP request failed for a non-transport reason: '
                            f'{type(failure).__name__}: {failure}'
                        ) from failure
                    else:
                        raise AssertionError(
                            'expired HTTP request returned a response instead of a '
                            f'transport failure: {abandoned_result!r}'
                        )
                    case['request_transport_failure_before_blocker_release'] = True
                    case['successful_http_response_before_blocker_release'] = False

                    blocker.rollback()
                    completed = datetime.now(timezone.utc)
                    case['blocker_released_at'] = completed.isoformat()
                    late_commit = _wait(
                        lambda: snapshot() != seeded or None,
                        'late commit after blocker release',
                        timeout=2, not_before=time.monotonic() + 1,
                    )
                    case['late_commit_observed'] = bool(late_commit)
                    case['activity_after_blocker_release'] = activity(backend=waiter)
                    assert process.returncode == 1, (
                        'expired drain exit status', process.returncode,
                    )
                    assert case['alive_before_drain_deadline'] is True, (
                        'process exited before the 25-second shutdown deadline',
                    )
                    assert case['signal_to_exit_seconds'] >= DRAIN_SECONDS - .25, (
                        'process exited before the real drain deadline',
                        case['signal_to_exit_seconds'],
                    )
                    assert case['signal_to_exit_seconds'] <= DRAIN_SECONDS + EXIT_CLEANUP_TOLERANCE_SECONDS, (
                        'process exceeded bounded drain plus cleanup allowance',
                        case['signal_to_exit_seconds'],
                    )
                    assert case['blocker_held_through_seconds'] >= DRAIN_SECONDS
                    assert not case['activity_before_blocker_release'], (
                        case['activity_before_blocker_release']
                    )
                    assert not case['ungranted_locks_before_blocker_release'], (
                        'blocked transaction retained ungranted locks',
                        case['ungranted_locks_before_blocker_release'],
                    )
                    assert case['listener_after_process_exit']['closed']
                    assert (
                        len(pre_release) == MODEL_COUNT
                        and case['all_models_unchanged_before_blocker_release']
                    )
                    assert not late_commit, 'a late commit appeared after blocker release'
                    assert backend_identity_ended(waiter), (
                        'blocked backend identity reappeared after blocker release'
                    )
                    assert not LogEntry.objects.filter(cid=abandoned_cid).exists(), (
                        'expired drain audited a rolled-back DELETE'
                    )
                    case['abandoned_audit_rows'] = 0
                    case['expired_audit_timestamp_bounds'] = {
                        'before': request_started.isoformat(),
                        'after': completed.isoformat(),
                    }
            except BaseException as failure:
                request_failure = failure
            finally:
                cleanup, cleanup_failures = cleanup_request_resources(
                    blocker, future, executor, process, 'expired_process',
                )
                case['request_cleanup'] = cleanup
                case['request_cleanup_failures'] = cleanup_failures
                case['request_cleanup_verified'] = not cleanup_failures
            if request_failure is not None:
                raise request_failure
            assert not cleanup_failures, (
                'expired request cleanup failed', cleanup, cleanup_failures,
            )

            recovery_cid = cid_prefix + '-recovery'
            recovery_process = None
            recovery_failure = None
            recovery_cleanup = None
            recovery_cleanup_failures = []
            try:
                with observed_server('expired-recovery-native') as (
                    runtime, recovery_process, process_record,
                ):
                    recovery_started = datetime.now(timezone.utc)
                    status, response = Client(
                        runtime['base_url'], token, timeout=15,
                    ).request(
                        'DELETE', f'/payment/category/{category_id}/delete', None,
                        {'X-Correlation-ID': recovery_cid},
                    )
                    completed = datetime.now(timezone.utc)
                    assert (status, response) == (
                        200, {'msg': 'payment category deleted'},
                    ), (status, response)
                    case['recovery_status'] = status
                    case['recovery_created_at'] = completed.isoformat()
            except BaseException as failure:
                recovery_failure = failure
            finally:
                recovery_cleanup = {}
                cleanup_process(
                    recovery_process, recovery_cleanup,
                    recovery_cleanup_failures, prefix='recovery_process',
                )
                case['recovery_process_cleanup'] = recovery_cleanup
                case['recovery_process_cleanup_failures'] = recovery_cleanup_failures
                case['recovery_process_cleanup_verified'] = not recovery_cleanup_failures
            if recovery_failure is not None:
                raise recovery_failure
            assert not recovery_cleanup_failures, (
                'expired recovery process cleanup failed',
                recovery_cleanup,
                recovery_cleanup_failures,
            )
            expected = [
                expected_audit(
                    recovery_cid, category_id, {'deleted': ['False', 'True']},
                    recovery_started, completed,
                ),
            ]
            assert_snapshot(
                seeded, category_id, cid_prefix, expected, {'deleted': True},
            )
            assert process_record['exit_status'] == 0, (
                'fresh recovery process exit status', process_record['exit_status'],
            )
            case.update({
                'audited_recovery': True,
                'recovery_audit_rows': 1,
                'abandoned_audit_rows': 0,
            })

    failures = []
    run_failure = None
    try:
        for name, probe in zip(EXPECTED_CASES, (graceful, expired)):
            case = {'name': name, 'success': False}
            evidence['cases'].append(case)
            try:
                probe(case)
                assert case.get('request_cleanup_verified') is True, (
                    'request resource cleanup was not verified'
                )
                assert case.get('recovery_process_cleanup_verified') is True, (
                    'recovery process cleanup was not verified'
                )
                case['success'] = True
            except Exception as failure:
                case['failure'] = f'{type(failure).__name__}: {failure}'
                failures.append(case['failure'])
            _write_evidence(evidence_path, evidence)
            print(
                f'native Go payment-category shutdown {name}: '
                f'{"PASS" if case["success"] else "FAIL"}',
                flush=True,
            )
        snapshot_restored = snapshot() == initial_snapshot
        evidence['cleanup_verified'] = snapshot_restored
        assert snapshot_restored, 'shutdown helper did not restore the caller snapshot'
        if failures:
            raise AssertionError(
                f'payment-category native shutdown failures: {failures}'
            )
        evidence['success'] = True
    except BaseException as failure:
        run_failure = failure
        evidence['success'] = False
        evidence['failure'] = f'{type(failure).__name__}: {failure}'
    finally:
        global_cleanup = {'processes': [], 'failures': []}
        for index, process in enumerate(process_objects, start=1):
            record = {}
            cleanup_process(
                process, record, global_cleanup['failures'],
                prefix=f'owned_process_{index}',
            )
            global_cleanup['processes'].append(record)
        if observer_connection.closed:
            global_cleanup['observer_connection_closed'] = True
        else:
            _attempt(
                global_cleanup, global_cleanup['failures'],
                'observer_connection_closed', observer_connection.close,
            )
        try:
            snapshot_restored = snapshot() == initial_snapshot
        except BaseException as failure:
            snapshot_restored = False
            global_cleanup['failures'].append(
                f'snapshot_verification: {type(failure).__name__}: {failure}'
            )

        barriers_released = all(
            case.get('request_cleanup', {}).get('blocker_rollback_completed') is True
            for case in evidence['cases'][:2]
        )
        request_threads_stopped = all(
            case.get('request_cleanup', {}).get('request_thread_stopped') is True
            for case in evidence['cases'][:2]
        )
        owned_processes_stopped = bool(process_objects) and all(
            process.poll() is not None for process in process_objects
        )
        all_case_cleanup = all(
            case.get('request_cleanup_verified') is True
            and case.get('recovery_process_cleanup_verified') is True
            for case in evidence['cases']
        ) and len(evidence['cases']) == len(EXPECTED_CASES)
        cleanup_verified = (
            snapshot_restored
            and barriers_released
            and request_threads_stopped
            and owned_processes_stopped
            and observer_connection.closed
            and all_case_cleanup
            and not global_cleanup['failures']
        )
        evidence['cleanup_verified'] = cleanup_verified
        evidence['resource_cleanup'] = {
            'barriers_released': barriers_released,
            'request_threads_stopped': request_threads_stopped,
            'owned_processes_stopped': owned_processes_stopped,
            'owned_connections_closed': observer_connection.closed,
            'cases_verified': all_case_cleanup,
            'snapshot_restored': snapshot_restored,
            'failures': global_cleanup['failures'],
            'processes': global_cleanup['processes'],
        }
        if not cleanup_verified:
            evidence['success'] = False
            if run_failure is None:
                run_failure = AssertionError(
                    'shutdown helper resource cleanup was not fully verified'
                )
                evidence['failure'] = f'{type(run_failure).__name__}: {run_failure}'

        provenance_failures = []
        try:
            source_after = _source_hashes()
        except BaseException as failure:
            source_after = {}
            provenance_failures.append(
                f'source_hash_end: {type(failure).__name__}: {failure}'
            )
        evidence['source_sha256_after'] = source_after
        evidence['source_stable'] = (
            bool(source_after)
            and source_after == evidence['source_sha256']
        )
        if not evidence['source_stable']:
            provenance_failures.append(
                'behavior-bearing source changed or became unreadable during acceptance'
            )

        all_four_match = False
        native_binary = None
        native_binary_sha256 = None
        if len(process_records) != 4:
            provenance_failures.append(
                f'expected four owned native processes, observed {len(process_records)}'
            )
        elif len(binary_hashes) != 1:
            provenance_failures.append(
                f'native processes used {len(binary_hashes)} distinct binary hashes'
            )
        else:
            native_binary = Path(process_records[0]['binary'])
            native_binary_sha256 = next(iter(binary_hashes))
            all_four_match = all(
                record['binary_sha256'] == native_binary_sha256
                for record in process_records
            )
            if not all_four_match:
                provenance_failures.append(
                    'not all four native processes used the recorded binary'
                )
        evidence.update({
            'native_binary': str(native_binary) if native_binary is not None else None,
            'native_binary_sha256': native_binary_sha256,
            'native_binary_all_four_matches': all_four_match,
            'process_count': len(process_records),
        })
        if native_binary is not None and native_binary_sha256 is not None:
            try:
                binary_after = _sha256(native_binary)
            except BaseException as failure:
                binary_after = None
                provenance_failures.append(
                    f'native_binary_hash_end: {type(failure).__name__}: {failure}'
                )
            evidence['native_binary_sha256_after'] = binary_after
            if binary_after != native_binary_sha256:
                provenance_failures.append(
                    'recorded native binary changed before acceptance'
                )

        if provenance_failures:
            evidence['success'] = False
            if run_failure is None:
                run_failure = AssertionError(
                    f'shutdown provenance failed: {provenance_failures}'
                )
                evidence['failure'] = f'{type(run_failure).__name__}: {run_failure}'
            evidence.setdefault('provenance_failures', []).extend(provenance_failures)
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        evidence['finished_at'] = _utc_now()
        _write_evidence(evidence_path, evidence)
    if run_failure is not None:
        raise run_failure
    return _jsonable(evidence)


def run_fixture(work, log, tool_env, address):
    """Seed only the identities needed to run the two shutdown cases."""
    from auditlog.context import disable_auditlog
    from django.apps import apps
    from django.conf import settings
    from django.db import connections
    from rest_framework_simplejwt.tokens import AccessToken

    from application.models import SportAssociation, User

    work = Path(work)
    evidence_path = (
        ROOT
        / 'quality-reports/go-migration/payment-category-shutdown/http-results.json'
    )
    database = settings.DATABASES['default']
    _private_database_guard(work, database)

    models = sorted(
        (
            model for model in apps.get_models(include_auto_created=True)
            if model._meta.managed and not model._meta.proxy
        ),
        key=lambda model: model._meta.label,
    )
    assert len(models) == MODEL_COUNT, (
        'shutdown fixture must snapshot all managed models including auto-created models',
        len(models),
    )

    def managed_snapshot():
        return {
            model._meta.label: [
                {key: _jsonable(value) for key, value in row.items()}
                for row in model._base_manager.order_by('pk').values()
            ]
            for model in models
        }

    owner_id = uuid.UUID(_category_id(197998))
    association_id = uuid.UUID(_category_id(197999))
    original = managed_snapshot()
    assert not User.original_objects.filter(pk=owner_id).exists(), (
        'shutdown fixture owner collision', owner_id,
    )
    assert not SportAssociation.original_objects.filter(pk=association_id).exists(), (
        'shutdown fixture association collision', association_id,
    )

    import redis
    host, port = address.rsplit(':', 1)
    raw = redis.Redis(host=host, port=int(port), db=1, password='synthetic-identity-only')

    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter()
                if not key.startswith(b'assozeta:go:readiness:')}

    original_cache = cache_snapshot()
    owner_created = False
    association_created = False
    run_failure = None
    report = None
    cleanup_results = {}
    cleanup_failures = []

    def delete_owned_association():
        with disable_auditlog():
            SportAssociation.original_objects.filter(
                pk=association_id, user_id=owner_id,
            ).delete()

    def delete_owned_owner():
        with disable_auditlog():
            User.original_objects.filter(pk=owner_id).delete()

    try:
        with disable_auditlog():
            User.original_objects.bulk_create([User(
                user_id=owner_id,
                username='shutdown-fixture-owner-197998',
                email='shutdown-fixture-owner-197998@example.test',
                first_name='Shutdown fixture',
                role=User.ASSOCIATION,
                is_active=True,
                is_superuser=False,
                deleted=False,
                password='!shutdown-fixture-only',
            )])
            owner_created = True
            SportAssociation.original_objects.bulk_create([SportAssociation(
                sport_association_id=association_id,
                user_id=owner_id,
                denomination='Shutdown fixture association',
                tax_code='19799819799',
            )])
            association_created = True
        seeded = managed_snapshot()
        assert len(seeded) == MODEL_COUNT
        access = AccessToken()
        access['user_id'] = str(owner_id)
        report = run(
            work=work,
            log=log,
            tool_env=tool_env,
            address=address,
            owner_id=owner_id,
            association_id=association_id,
            token=str(access),
            snapshot=managed_snapshot,
            evidence_path=evidence_path,
        )
    except BaseException as failure:
        run_failure = failure
    finally:
        if association_created:
            _attempt(
                cleanup_results, cleanup_failures,
                'owned_association_deleted', delete_owned_association,
            )
        else:
            cleanup_results['owned_association_deleted'] = True
        if owner_created:
            _attempt(
                cleanup_results, cleanup_failures,
                'owned_user_deleted', delete_owned_owner,
            )
        else:
            cleanup_results['owned_user_deleted'] = True
        _attempt(
            cleanup_results, cleanup_failures,
            'original_snapshot_restored',
            lambda: managed_snapshot() == original,
        )
        _attempt(
            cleanup_results, cleanup_failures,
            'connections_closed',
            connections.close_all,
        )

        _attempt(cleanup_results, cleanup_failures, 'cache_preserved',
                 lambda: cache_snapshot() == original_cache)
        _attempt(cleanup_results, cleanup_failures, 'redis_closed', raw.close)

        fixture_cleanup = {
            'scope': (
                'only the two rows created by this wrapper: sport association then owner'
            ),
            'owner_id': str(owner_id),
            'association_id': str(association_id),
            'results': cleanup_results,
            'failures': cleanup_failures,
            'verified': not cleanup_failures,
        }
        try:
            if evidence_path.exists():
                recorded = json.loads(evidence_path.read_text())
                recorded['success'] = bool(recorded.get('success') and not cleanup_failures and run_failure is None)
                recorded['fixture_cleanup'] = fixture_cleanup
                recorded['fixture_snapshot_restored'] = cleanup_results.get(
                    'original_snapshot_restored', False,
                )
                _write_evidence(evidence_path, recorded)
        except BaseException as failure:
            cleanup_failures.append(
                f'evidence_update: {type(failure).__name__}: {failure}'
            )
            fixture_cleanup['verified'] = False

    if run_failure is not None:
        raise run_failure
    assert not cleanup_failures, (
        'standalone shutdown fixture cleanup failed',
        cleanup_results,
        cleanup_failures,
    )
    return {
        **report,
        'fixture_cleanup': fixture_cleanup,
        'scope': (
            'standalone native Go payment-category DELETE shutdown fixture; '
            'full payment-category, Django, packaged/Caddy and wider-operation parity remain unproven'
        ),
        'complete_payment_category_parity': False,
    }
