"""Opt-in real HTTP races at independently observed PostgreSQL audit barriers.

The expected duplicate ended audits and surviving replacement sessions are
Django baseline observations to preserve, not an exactly-once guarantee.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import unittest

from TESTS.adapters.django.impersonation_effects import assert_cache_transition, assert_retained_rows
from TESTS.contracts.test_impersonation_management import assert_json_equal
from TESTS.http_client import poll


class Transport:
    """Own every HTTP socket, with a total body deadline and bounded abort."""
    def __init__(self):
        self.connections = set()
        self.sockets = set()
        self.lock = threading.Lock()

    def request(self, base, token, method, cid, previous=None, target=None):
        url = urlsplit(base)
        assert url.scheme == 'http' and not url.path
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=15)
        with self.lock:
            self.connections.add(connection)
        actual_socket = None
        try:
            deadline = time.monotonic() + 25
            headers = {'Authorization': 'Bearer ' + token, 'Accept-Language': 'en',
                       'X-Correlation-ID': cid, 'Content-Type': 'application/json'}
            if previous:
                headers['X-Impersonation-Id'] = previous
            body = json.dumps({'target_user_id': target}).encode() if target else None
            connection.request(method, '/association/impersonation', body=body, headers=headers)
            response = connection.getresponse()
            actual_socket = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            with self.lock:
                if actual_socket is not None:
                    self.sockets.add(actual_socket)
            content = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Owned concurrent HTTP total deadline exceeded')
                if actual_socket is not None:
                    actual_socket.settimeout(remaining)
                chunk = response.read1(8192)
                if not chunk:
                    break
                content.extend(chunk)
                assert len(content) <= 131072, 'Unexpected concurrent response size'
            if response.status < 500:
                assert response.getheader('Content-Language') == 'en'
            assert response.getheader('Set-Cookie') is None
            assert response.getheader('WWW-Authenticate') is None
            if response.status < 500 and response.status != 204:
                assert response.getheader('Content-Type', '').split(';', 1)[0] == 'application/json'
            try:
                result = json.loads(content) if content else ''
            except (ValueError, UnicodeDecodeError):
                assert response.status >= 500
                result = content.decode(errors='replace')
            return response.status, result
        finally:
            connection.close()
            with self.lock:
                self.connections.discard(connection)
                self.sockets.discard(actual_socket)

    def abort(self):
        with self.lock:
            connections = list(self.connections)
            sockets = set(self.sockets) | {connection.sock for connection in connections if connection.sock is not None}
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for connection in connections:
            connection.close()


@contextmanager
def audit_barrier(cids, old_session, report, *, action="ended"):
    """Park ended INSERTs only after their real Redis GET/target lookup."""
    from django.db import connection
    function = 'imp_race_' + uuid4().hex
    blocker = connection.copy(alias=function)
    blocker.ensure_connection()
    blocker.set_autocommit(True)
    keys = [uuid4().int & ((1 << 62) - 1) for _ in cids]
    assert len(set(keys)) == len(keys)
    installed = False
    held = set()
    try:
        with blocker.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            blocker_pid = cursor.fetchone()[0]
            for key in keys:
                cursor.execute('SELECT pg_advisory_lock(%s)', [key])
                held.add(key)
        branches = []
        for cid, key in zip(cids, keys):
            assert re.fullmatch(r'[a-z0-9-]{1,50}', cid)
            assert action in ('started', 'ended')
            session_condition = ''
            if old_session is not None:
                assert str(UUID(old_session)) == old_session
                session_condition = f" AND NEW.additional_data->>'impersonation_session' = '{old_session}'"
            branches.append(f"""IF NEW.cid = '{cid}' AND NEW.changes->'impersonation'->>1 = '{action}'
                {session_condition} THEN
                PERFORM set_config('application_name', 'race:{cid}', true);
                PERFORM pg_advisory_xact_lock({key}); END IF;""")
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                           + ' '.join(branches) + ' RETURN NEW; END $$')
            cursor.execute(f'CREATE TRIGGER {function} BEFORE INSERT ON auditlog_logentry '
                           f'FOR EACH ROW EXECUTE FUNCTION {function}()')
        installed = True

        def observed():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pid,application_name,wait_event_type,wait_event,pg_blocking_pids(pid) "
                               "FROM pg_stat_activity WHERE datname=current_database() AND application_name=ANY(%s)",
                               [['race:' + cid for cid in cids]])
                return cursor.fetchall()

        def wait():
            rows = poll(observed, lambda rows: len(rows) == len(cids) and all(
                row[2] == 'Lock' and row[3] == 'advisory' and blocker_pid in row[4] for row in rows),
                timeout=8, interval=.025)
            assert len({row[0] for row in rows}) == len(cids), 'Requests did not use independent database backends'
            report['observed_barriers'] = [{'cid': row[1].removeprefix('race:'),
                                            'wait_event': row[3], 'owned_blocker_verified': True} for row in rows]

        def release(index):
            key = keys[index]
            with blocker.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [key])
                assert cursor.fetchone()[0]
            held.remove(key)
        yield wait, release
    finally:
        try:
            with blocker.cursor() as cursor:
                for key in list(held):
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [key])
                    assert cursor.fetchone()[0]
                    held.remove(key)
        finally:
            blocker.close()
        # Caller drains HTTP workers before leaving the context on normal paths;
        # lock release precedes DDL on failure so waiting INSERTs can finish.
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            try:
                if installed:
                    cursor.execute(f'DROP TRIGGER {function} ON auditlog_logentry')
                cursor.execute(f'DROP FUNCTION IF EXISTS {function}()')
                cursor.execute('SELECT to_regprocedure(%s)', [function + '()'])
                assert cursor.fetchone()[0] is None
            finally:
                cursor.execute('SET lock_timeout = DEFAULT')
        report['barrier_cleanup'] = True


@contextmanager
def eligibility_read_barrier(report):
    """Block only the owner membership SELECT, after its earlier Redis GET."""
    from django.db import connection
    blocker = connection.copy(alias='imp_read_' + uuid4().hex)
    blocker.ensure_connection()
    blocker.set_autocommit(False)
    try:
        with blocker.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            pid = cursor.fetchone()[0]
            cursor.execute('LOCK TABLE application_associate IN ACCESS EXCLUSIVE MODE')
        def observed():
            with connection.cursor() as cursor:
                # Django's long eligibility SELECT can exceed track_activity_query_size;
                # inspect the actual ungranted table lock rather than a truncated SQL suffix.
                cursor.execute("SELECT a.pid,a.wait_event_type,a.wait_event FROM pg_stat_activity a "
                    "JOIN pg_locks l ON l.pid=a.pid WHERE a.datname=current_database() "
                    "AND a.pid<>pg_backend_pid() AND a.query LIKE 'SELECT %%' "
                    "AND NOT l.granted AND l.relation='application_associate'::regclass "
                    "AND l.mode='AccessShareLock' AND %s=ANY(pg_blocking_pids(a.pid))", [pid])
                return cursor.fetchall()
        def wait():
            rows = poll(observed, lambda rows: len(rows) == 1 and rows[0][1:] == ('Lock', 'relation'),
                        timeout=8, interval=.025)
            report['observed_barriers'] = [{'wait_event': 'relation', 'owned_blocker_verified': True,
                                            'independent_eligibility_reader': len(rows) == 1, 'ungranted_membership_table_lock': True}]
        yield wait, blocker.commit
    finally:
        blocker.rollback()
        blocker.close()
        report['barrier_cleanup'] = True


def run(*, work, log, django_base, go_base, users, tokens, descriptions, associations, address, observe):
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.db import connection
    from django.core.cache import cache
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    import redis

    del log
    work = Path(work).resolve(strict=True)
    assert work.is_dir() and work.name.startswith('az-accounts-'), 'Owned native account fixture required'
    assert connection.settings_dict['NAME'] == 'assozeta_contract_accounts'
    assert Path(connection.settings_dict['HOST']).resolve() == work
    assert re.fullmatch(r'127\.0\.0\.1:[0-9]+', address), 'Owned loopback Redis required'
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    host, port = address.split(':')
    raw = redis.Redis(host=host, port=int(port), password='synthetic-identity-only', db=1)
    transport = Transport()
    tester = unittest.TestCase()
    pairings = [('django-django', django_base, django_base)]
    if go_base:
        pairings += [('go-go', go_base, go_base), ('django-go', django_base, go_base),
                     ('go-django', go_base, django_base)]
    scenarios = ('dual-delete', 'dual-replace', 'replace-delete', 'double-delete-del-failure',
                 'eligibility-after-admission', 'read-after-revocation')
    expected_names = [name + ':' + scenario for name, *_ in pairings for scenario in scenarios]
    report = {'success': False, 'requested': True, 'cases': [], 'expected_scenarios': expected_names,
              'go_requested': go_base is not None, 'source_sha256': source_hash,
              'scope': 'Observed audit barriers for overlapping DELETE/replacement; no mutation linearizability claim',
              'cleanup_verified': False}

    output = Path(__file__).resolve().parents[3] / 'quality-reports/go-migration/impersonation-concurrency'
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / 'results.json'
    receipt.write_text(json.dumps(report, indent=2) + '\n')

    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
                for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}

    def caches():
        return {key: raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}

    baseline, baseline_cache = snapshot(), caches()
    baseline_ids = {row['id'] for row in baseline['auditlog.LogEntry']}
    target = {**descriptions['athlete'], 'association': associations['owner']}
    actor_id, target_id = str(users['owner'].pk), str(users['athlete'].pk)
    association = associations['owner']
    expected_record = {'actor_id': actor_id, 'target_id': target_id, 'association_id': association['id']}
    owned_sessions = set()
    owned_cids = set()
    value_bytes = {}

    def cleanup_owned():
        for event in LogEntry.objects.filter(cid__in=owned_cids):
            identifier = (event.additional_data or {}).get('impersonation_session')
            if identifier:
                assert str(UUID(identifier)) == identifier
                owned_sessions.add(identifier)
        for identifier in owned_sessions:
            cache.delete(session_key(identifier))
        LogEntry.objects.filter(cid__in=owned_cids).delete()

    def validate_start(response):
        status, result = response
        assert status == 201
        identifier = result['session_id']
        assert str(UUID(identifier)) == identifier and UUID(identifier).version == 4
        assert identifier not in owned_sessions
        owned_sessions.add(identifier)
        assert_json_equal(tester, result, {'session_id': identifier, 'target': target, 'expires_in': 3600,
            'actor_role': 'association', 'association': association})
        return identifier

    def request(base, method, cid, previous=None, target=None):
        owned_cids.add(cid)
        response = transport.request(base, tokens['owner'], method, cid, previous, target)
        expected_django = base == django_base
        seen = observe(cid)
        assert [row['case'] for row in seen] == ([cid] if expected_django else []), 'concurrent upstream mismatch'
        if seen:
            assert seen[0]['status'] == response[0]
        return response

    def state(expected_events, current_sessions, *, expected_business=None):
        events = list(LogEntry.objects.exclude(pk__in=baseline_ids).order_by('pk'))
        actual = [(event.cid, event.additional_data['impersonation_session'],
                   event.changes_dict['impersonation'][1]) for event in events]
        assert sorted(actual) == sorted(expected_events), 'Unexpected concurrent audit transitions'
        new_indices = []
        for event in events:
            identifier = event.additional_data['impersonation_session']
            action = event.changes_dict['impersonation'][1]
            assert event.action == 3 and event.changes_dict == {'impersonation': ['', action]}
            assert str(event.actor_id) == actor_id and event.object_pk == target_id
            assert event.content_type.model == 'user'
            assert event.object_repr == 'name: ' + users['athlete'].first_name + ' ' + users['athlete'].last_name
            assert event.actor_email is None
            assert_json_equal(tester, event.additional_data, {'impersonation_session': identifier,
                'actor_id': actor_id, 'administrator_id': None, 'association_id': association['id'],
                'target_user_id': target_id, 'target_role': users['athlete'].role})
            indexes = list(AuditLogIndex.objects.filter(log_entry=event))
            assert len(indexes) == 1
            index = indexes[0]
            assert str(index.sport_association_id) == association['id'] and index.resolution_path == 'actor.sport_association'
            new_indices.append(index.pk)
        after = snapshot()
        assert_retained_rows(baseline['auditlog.LogEntry'], after['auditlog.LogEntry'], key='id', created=[event.pk for event in events])
        assert_retained_rows(baseline['application.AuditLogIndex'], after['application.AuditLogIndex'],
                             key='audit_log_index_id', created=new_indices)
        for model, rows in baseline.items():
            if model not in ('auditlog.LogEntry', 'application.AuditLogIndex'):
                assert after[model] == (expected_business or baseline)[model], 'Concurrent operation changed business rows'
        keys = set()
        for identifier in current_sessions:
            key = cache.make_key(session_key(identifier)).encode()
            keys.add(key)
            assert_json_equal(tester, cache.get(session_key(identifier)), expected_record)
            value = raw.get(key)
            if identifier in value_bytes:
                assert value == value_bytes[identifier], 'Retained session bytes changed'
            else:
                value_bytes[identifier] = value
            assert 0 < raw.pttl(key) <= 3600000
        assert_cache_transition(baseline_cache, caches(), created=keys)
        return {'audit_rows': len(events), 'index_rows': len(new_indices), 'live_sessions': len(keys),
                'retained_rows_preserved': True, 'retained_cache_bytes_preserved': True,
                'business_state_matches_expected': True, 'authored_fixture_mutation_verified': expected_business is not None}

    def live_read(base, cid, identifier, entry):
        key = cache.make_key(session_key(identifier)).encode()
        assert raw.pexpire(key, 60000)
        before = raw.pttl(key)
        assert 0 < before <= 60000
        status, body = request(base, 'GET', cid, identifier)
        assert status == 200
        assert_json_equal(tester, body, {'target': target})
        after = raw.pttl(key)
        assert 0 < after <= before
        entry['shortened_ttl_checks'].append({'before_ms': before, 'after_ms': after})

    try:
        for pairing, first, second in pairings:
            for scenario in scenarios:
                entry = {'name': pairing + ':' + scenario, 'success': False, 'barrier_cleanup': False,
                         'shortened_ttl_checks': []}
                report['cases'].append(entry)
                receipt.write_text(json.dumps(report, indent=2) + '\n')
                prefix = 'ic-' + uuid4().hex[:10]
                setup_cid, cid_a, cid_b = prefix + '-setup', prefix + '-a', prefix + '-b'
                old = None
                expected = []
                if scenario != 'eligibility-after-admission':
                    old = validate_start(request(first, 'POST', setup_cid, target=target_id))
                    expected.append((setup_cid, old, 'started'))
                    state(expected, [old])
                executor = ThreadPoolExecutor(max_workers=2)
                futures = []
                try:
                    if scenario == 'eligibility-after-admission':
                        from application.models import Associate
                        members = list(Associate.objects.all_objects().filter(user_id=target_id).values('pk', 'deleted'))
                        assert members and any(not row['deleted'] for row in members)
                        mutated = {model: [dict(row) for row in rows] for model, rows in baseline.items()}
                        for row in mutated['application.Associate']:
                            if str(row['user_id']) == target_id:
                                row['deleted'] = True
                        try:
                            with audit_barrier([cid_a], None, entry, action='started') as (wait, release):
                                futures.append(executor.submit(request, first, 'POST', cid_a, None, target_id))
                                wait()
                                entry['blocked_state'] = state([], [])
                                count = Associate.objects.all_objects().filter(user_id=target_id).update(deleted=True)
                                assert count == len(members)
                                state([], [], expected_business=mutated)
                                release(0)
                                identifier = validate_start(futures[0].result(timeout=12))
                            expected.append((cid_a, identifier, 'started'))
                            entry['response_statuses'] = [201]
                            entry['final_state'] = state(expected, [identifier], expected_business=mutated)
                            assert request(second, 'GET', prefix + '-fresh-denial', identifier)[0] == 403
                            cleanup_cid = prefix + '-cleanup'
                            assert request(second, 'DELETE', cleanup_cid, identifier) == (204, '')
                            expected.append((cleanup_cid, identifier, 'ended'))
                            state(expected, [], expected_business=mutated)
                        finally:
                            for row in members:
                                assert Associate.objects.all_objects().filter(pk=row['pk']).update(deleted=row['deleted']) == 1
                        state(expected, [])
                    elif scenario == 'read-after-revocation':
                        with eligibility_read_barrier(entry) as (wait, release):
                            futures.append(executor.submit(request, first, 'GET', cid_a, old))
                            wait()
                            assert request(second, 'DELETE', cid_b, old) == (204, '')
                            expected.append((cid_b, old, 'ended'))
                            assert cache.get(session_key(old)) is None
                            entry['session_absent_before_reader_release'] = True
                            release()
                            status, body = futures[0].result(timeout=12)
                            assert status == 200
                            assert_json_equal(tester, body, {'target': target})
                        entry['response_statuses'] = [200, 204]
                        entry['final_state'] = state(expected, [])
                        assert request(first, 'GET', prefix + '-fresh-denial', old)[0] == 403
                    else:
                        new = []
                        with audit_barrier([cid_a, cid_b], old, entry) as (wait, release):
                            actions = ('POST', 'POST') if scenario == 'dual-replace' else (
                                      ('POST', 'DELETE') if scenario == 'replace-delete' else ('DELETE', 'DELETE'))
                            for base, cid, method in zip((first, second), (cid_a, cid_b), actions):
                                futures.append(executor.submit(request, base, method, cid, old,
                                    target_id if method == 'POST' else None))
                            wait()
                            for cid, method in zip((cid_a, cid_b), actions):
                                if method != 'POST':
                                    continue
                                events = list(LogEntry.objects.filter(cid=cid))
                                assert len(events) == 1 and events[0].changes_dict == {'impersonation': ['', 'started']}
                                identifier = events[0].additional_data['impersonation_session']
                                assert str(UUID(identifier)) == identifier and UUID(identifier).version == 4
                                assert identifier not in owned_sessions
                                owned_sessions.add(identifier)
                                new.append(identifier)
                                expected.append((cid, identifier, 'started'))
                            entry['blocked_state'] = state(expected, [old, *new])
                            faulted = scenario == 'double-delete-del-failure'
                            from TESTS.adapters.redis_faults import deny_redis_command
                            fault = deny_redis_command(address, work, 'DEL') if faulted else nullcontext()
                            responses = []
                            with fault as measured:
                                for index, (cid, method) in enumerate(zip((cid_a, cid_b), actions)):
                                    release(index)
                                    response = futures[index].result(timeout=12)
                                    responses.append(response[0])
                                    if faulted:
                                        assert response[0] == 500
                                    elif method == 'DELETE':
                                        assert response == (204, '')
                                    else:
                                        assert response[0] == 201
                                        assert_json_equal(tester, response[1], {'session_id': new[index], 'target': target,
                                            'expires_in': 3600, 'actor_role': 'association', 'association': association})
                                    expected.append((cid, old, 'ended'))
                                    state(expected, [old] if faulted else new)
                            if faulted:
                                assert measured['observed_denials'] >= 2 and measured['acl_restored']
                                entry['redis_fault'] = measured
                            entry['response_statuses'] = responses
                        entry['final_state'] = state(expected, [old] if faulted else new)
                        if faulted:
                            live_read(second, prefix + '-recovered', old, entry)
                            cleanup_cid = prefix + '-cleanup'
                            assert request(second, 'DELETE', cleanup_cid, old) == (204, '')
                            expected.append((cleanup_cid, old, 'ended'))
                        else:
                            assert request(first, 'GET', prefix + '-revoked', old)[0] == 403
                        for index, identifier in enumerate(new):
                            live_read(second, prefix + '-usable-' + str(index), identifier, entry)
                            cid = prefix + '-cleanup-' + str(index)
                            assert request(second, 'DELETE', cid, identifier) == (204, '')
                            expected.append((cid, identifier, 'ended'))
                        state(expected, [])
                    entry['success'] = True
                finally:
                    transport.abort()
                    for future in futures:
                        try:
                            future.result(timeout=28)
                        except Exception:
                            pass
                    if not all(future.done() for future in futures):
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise AssertionError('Owned HTTP workers did not stop')
                    executor.shutdown(wait=True, cancel_futures=True)
                    entry['workers_stopped'] = True
                    cleanup_owned()
                    assert snapshot() == baseline and caches() == baseline_cache
                    entry['cleanup_verified'] = True
                    receipt.write_text(json.dumps(report, indent=2) + '\n')
        # Exercise the actual exceptional teardown path while a real HTTP INSERT
        # is waiting. This is fixture cleanup evidence, not another parity case.
        probe = {'success': False, 'barrier_cleanup': False,
                 'scope': 'Deliberate harness exception after real SQL wait; cleanup control only'}
        report['cleanup_control'] = probe
        prefix = 'ic-' + uuid4().hex[:10]
        setup_cid, request_cid = prefix + '-setup', prefix + '-cleanup-probe'
        identifier = validate_start(request(django_base, 'POST', setup_cid, target=target_id))
        executor = ThreadPoolExecutor(max_workers=1)
        future = None
        class ExpectedFixtureFailure(Exception):
            pass
        try:
            try:
                with audit_barrier([request_cid], identifier, probe) as (wait, _release):
                    future = executor.submit(request, django_base, 'DELETE', request_cid, identifier)
                    wait()
                    raise ExpectedFixtureFailure()
            except ExpectedFixtureFailure:
                probe['injected_failure_observed'] = True
            assert future is not None and future.result(timeout=12) == (204, '')
            state([(setup_cid, identifier, 'started'), (request_cid, identifier, 'ended')], [])
            assert probe['barrier_cleanup'] and probe['injected_failure_observed']
            probe['success'] = True
        finally:
            transport.abort()
            if future is not None:
                try:
                    future.result(timeout=28)
                except Exception:
                    pass
                if not future.done():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise AssertionError('Cleanup control worker did not stop')
            executor.shutdown(wait=True, cancel_futures=True)
            probe['workers_stopped'] = True
            cleanup_owned()
            assert snapshot() == baseline and caches() == baseline_cache
            probe['cleanup_verified'] = True
        assert probe['success'] and probe['workers_stopped'] and probe['cleanup_verified']
        assert [case['name'] for case in report['cases']] == expected_names
        assert all(case['success'] and case['barrier_cleanup'] and case['workers_stopped'] and case['cleanup_verified']
                   for case in report['cases'])
        report['success'] = True
        return report
    finally:
        cleanup_errors = []
        try:
            transport.abort()
            cleanup_owned()
            report['cleanup_verified'] = snapshot() == baseline and caches() == baseline_cache
        except BaseException as error:
            cleanup_errors.append(type(error).__name__)
        finally:
            raw.close()
            report['cleanup_errors'] = cleanup_errors
            report['source_stable'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == source_hash
            report['success'] = bool(report['success'] and report['cleanup_verified'] and report['source_stable'] and not cleanup_errors)
            receipt.write_text(json.dumps(report, indent=2) + '\n')
