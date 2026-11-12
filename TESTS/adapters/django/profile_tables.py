"""Table preferences: real HTTP, audit and concurrency on private PostgreSQL/Redis."""
import json
import ipaddress
import os
import socket
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env, address):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections, connection, transaction
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client, poll
    import redis
    import uvicorn

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    original = snapshot()
    created, sessions, cases, expected = [], [], [], {}
    missing = object()

    def add(name, value=missing, *, role=2, grants=None, body=missing, status=200, response=None, changed=True, actor=None, impersonation=False, **kw):
        n = 120000 + 3 * len(cases)
        identifier, owner_id = uid(n), uid(n + 1)
        with disable_auditlog():
            if role == 3:
                owner = User.original_objects.create(user_id=owner_id, username='TABLE-OWNER-' + str(n), email=f'table-owner-{n}@example.test', role=1, first_name='Owner', last_name='Fixture', password='!synthetic', tables_settings={'before': [1]})
                created.append(owner_id)
                SportAssociation.original_objects.create(sport_association_id=uid(n + 10000), user=owner, denomination='Table ASD')
            user = User.original_objects.create(user_id=identifier, username='TABLE-' + str(n), email=f'table-{n}@example.test', role=role,
                first_name='First', last_name='Last', password='!synthetic', tables_settings={'before': [1]}, connected_user_id=owner_id if role == 3 else None,
                collaborator_role=3 if role == 3 else 1, collaborator_permissions=grants or [])
            created.append(identifier)
            if role == 1:
                SportAssociation.original_objects.create(sport_association_id=uid(n + 10000), user=user, denomination='Table ASD')
        target = owner_id if role == 3 else identifier
        actor_id = owner_id if actor == 'owner' else uid(actor) if actor else identifier
        actor_user = User.original_objects.get(pk=actor_id)
        token = AccessToken(); token['user_id'] = actor_id
        headers = kw.pop('headers', {})
        additional = None
        if impersonation:
            session = uid(n + 20000); sessions.append(session)
            association = uid(n + 10000) if actor == 'owner' else None
            cache.set(session_key(session), {'actor_id': actor_id, 'target_id': identifier, 'association_id': association}, 3600)
            headers['X-Impersonation-Id'] = session
            additional = {'impersonation_session': session, 'actor_id': actor_id, 'target_user_id': identifier, 'association_id': association}
        before = User.original_objects.filter(pk=target).values().get()
        after = value if changed and value is not missing else before['tables_settings']
        expected[target] = (before, after, actor_id, actor_user.email, additional, uid(n + 10000) if role in (1, 3) else None)
        cases.append(dict(name=name, target=target, token=str(token), body=({'tables_settings': value} if value is not missing else {}) if body is missing else body,
            status=status, response=response if response is not None else {'status': 'success'}, headers=headers, **kw))

    add('athlete-object', {'columns': ['name', 'price'], 'hidden': False})
    add('owner-object', {'z': 1, 'a': ['è', True] }, role=1)
    add('collaborator-owner-preferences', {'columns': ['owner']}, role=3, grants=['other.settings.update'])
    add('array', [1, 'x', False, None])
    add('string', '{"plain": "text"}')
    add('integer', 9007199254740993)
    add('float', 1.5)
    add('boolean', False)
    add('empty-object', {})
    add('empty-array', [])
    add('empty-string', '')
    add('missing-field')
    add('unknown-field', body={'first_name': 'Ignored', 'password': 'Ignored'})
    add('same-value', {'before': [1]})
    add('null', None, status=400, changed=False, response={'tables_settings': ['This field may not be null.']})
    add('list-body', body=[], status=400, changed=False, response={'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']})
    add('string-body', raw_body='"text"', status=400, changed=False, response={'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']})
    add('form-json', {'columns': [True]}, raw_body='tables_settings=%7B%22columns%22%3A%5Btrue%5D%7D', content_type='application/x-www-form-urlencoded')
    add('form-invalid', raw_body='tables_settings=invalid', content_type='application/x-www-form-urlencoded', status=400, changed=False, response={'tables_settings': ['Value must be valid JSON.']})
    add('form-missing', raw_body='ignored=x', content_type='application/x-www-form-urlencoded')
    add('restricted', {'denied': 1}, role=3, status=403, changed=False, response={'detail': 'Missing permission: other.settings.update'})
    add('admin-direct', actor=1, status=403, changed=False, response={'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('admin-impersonated', {'admin': 1}, actor=1, impersonation=True, role=1)
    add('owner-impersonated-collaborator', {'owner': 1}, actor='owner', impersonation=True, role=3, grants=['other.settings.update'])
    add('anonymous', status=401, changed=False, response={'detail': 'Authentication credentials were not provided.'}); cases[-1]['token'] = None
    add('inactive', actor=3, status=401, changed=False, response={'detail': 'User is inactive', 'code': 'user_inactive'})
    add('deleted', actor=4, status=401, changed=False, response={'detail': 'User not found', 'code': 'user_not_found'})
    add('get', method='GET', status=405, changed=False, response={'detail': 'Method "GET" not allowed.'})
    add('options', method='OPTIONS', changed=False, response={'name': 'Profile Settings Tables', 'description': '', 'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
    add('format', query='?format=xml', status=404, changed=False, response={'detail': 'Not found.'})
    add('accept', headers={'Accept': 'text/html'}, status=406, changed=False, response={'detail': 'Could not satisfy the request Accept header.'})
    add('malformed', raw_body='{', status=400, changed=False, response={'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
    add('media', raw_body='x', content_type='text/plain', status=415, changed=False, response={'detail': 'Unsupported media type "text/plain" in request.'})
    add('audit-insert-failure', {'partial': 1}, role=1, status=500, changed=False)
    add('audit-index-failure', {'partial': 1}, role=1, status=500, changed=False)
    add('json-storage-failure', {'bad': '\x00'}, role=1, status=500, changed=False)
    add('null-body', raw_body='null', status=400, changed=False, response={'non_field_errors': ['No data provided']})
    add('number-body', raw_body='1', status=400, changed=False, response={'non_field_errors': ['Invalid data. Expected a dictionary, but got int.']})
    add('bool-body', raw_body='false', status=400, changed=False, response={'non_field_errors': ['Invalid data. Expected a dictionary, but got bool.']})
    add('float-overflow', raw_body='{"tables_settings":1e309}', status=400, changed=False, response={'tables_settings': ['Value must be valid JSON.']})
    add('nested-float-overflow', raw_body='{"tables_settings":{"nested":[1e309]}}', status=400, changed=False, response={'tables_settings': ['Value must be valid JSON.']})
    add('form-null', raw_body='tables_settings=null', content_type='application/x-www-form-urlencoded', status=500, changed=False)
    add('form-float-overflow', raw_body='tables_settings=1e309', content_type='application/x-www-form-urlencoded', status=500, changed=False)
    add('form-nan', raw_body='tables_settings=NaN', content_type='application/x-www-form-urlencoded', status=400, changed=False, response={'tables_settings': ['Value must be valid JSON.']})
    add('form-duplicate', False, raw_body='tables_settings=true&tables_settings=false', content_type='application/x-www-form-urlencoded')
    add('json-duplicate', {'last': 1}, raw_body='{"tables_settings":false,"tables_settings":{"last":1}}')
    add('italian-null', None, headers={'Accept-Language': 'it'}, status=400, changed=False, response={'tables_settings': ['Il campo non può essere nullo.']})
    add('italian-body', body=[], headers={'Accept-Language': 'it'}, status=400, changed=False, response={'non_field_errors': ['Dati non validi. Era atteso un dizionario, ma si è ricevuto list.']})
    add('italian-json', raw_body='tables_settings=bad', content_type='application/x-www-form-urlencoded', headers={'Accept-Language': 'it'}, status=400, changed=False, response={'tables_settings': ['Il valore deve essere un JSON valido.']})
    add('bound-foreign-reference', {'sport_association_id': uid(90)}, actor='owner', impersonation=True, role=3, grants=['other.settings.update'], status=403, changed=False, response={'detail': 'Risorsa non disponibile per questo utente e associazione.'})
    add('forged-target', {'forged': 1}, headers={'User-Id': uid(9)}, status=403, changed=False, response={'detail': 'Solo il titolare può impersonare utenti.', 'impersonation_invalid': 'True'})
    add('italian-null-body', raw_body='null', headers={'Accept-Language': 'it'}, status=400, changed=False, response={'non_field_errors': ['No data provided']})
    add('multipart-json', {'columns': [True]}, raw_body='--tables\r\nContent-Disposition: form-data; name="tables_settings"\r\n\r\n{"columns":[true]}\r\n--tables--\r\n', content_type='multipart/form-data; boundary=tables')
    add('multipart-file-name', False, raw_body='--tables\r\nContent-Disposition: form-data; name="tables_settings"; filename="false"\r\nContent-Type: application/octet-stream\r\n\r\nignored\r\n--tables--\r\n', content_type='multipart/form-data; boundary=tables')
    add('unpaired-surrogate', raw_body='{"tables_settings":"\\ud800"}', status=500, changed=False)
    add('literal-replacement-character', '\ufffd')
    add('form-unpaired-surrogate', raw_body='tables_settings=%22%5Cud800%22', content_type='application/x-www-form-urlencoded', status=500, changed=False)
    add('unpaired-json-key', raw_body='{"tables_settings":{"\\udfff":true}}', status=500, changed=False)
    add('ignored-unpaired-value', raw_body='{"ignored":"\\ud800"}')
    add('ignored-unpaired-key', raw_body='{"\\ud800":true}')
    add('surrogate-pair', {'emoji': '\U0001f600'}, raw_body='{"tables_settings":{"emoji":"\\ud83d\\ude00"}}')
    add('impersonated-unpaired-surrogate', actor=1, impersonation=True, role=1, raw_body='{"tables_settings":"\\ud800"}', status=500, changed=False)
    path = work / 'profile-tables.json'; path.write_text(json.dumps({'cases': cases})); path.chmod(0o600)
    host, port = address.split(':'); raw = redis.Redis(host=host, port=int(port), password='synthetic-identity-only', db=0)
    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
    seeded, cache_before = snapshot(), cache_snapshot()
    audit_target = next(c['target'] for c in cases if c['name'] == 'audit-insert-failure')
    index_target = next(c['target'] for c in cases if c['name'] == 'audit-index-failure')
    with connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION tables_http_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic table audit failure'; END $$")
        cursor.execute("CREATE TRIGGER tables_http_reject_audit BEFORE INSERT ON auditlog_logentry FOR EACH ROW WHEN (NEW.object_pk = '" + audit_target + "') EXECUTE FUNCTION tables_http_reject_audit()")
        cursor.execute("CREATE TRIGGER tables_http_reject_index BEFORE INSERT ON application_auditlogindex FOR EACH ROW WHEN (NEW.sport_association_id = '" + expected[index_target][-1] + "'::uuid) EXECUTE FUNCTION tables_http_reject_audit()")

    def contracts(base, remote_addr='198.51.100.83'):
        events = set(LogEntry.objects.values_list('pk', flat=True))
        try:
            with patch.dict(os.environ, TESTS_BASE_URL=base, TESTS_PROFILE_TABLES_FIXTURE=str(path)):
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_profile_tables.ProfileTablesContracts'))
            count = 0
            for case in cases:
                identifier = case['target']; before, after, actor_id, actor_email, additional, association = expected[identifier]
                assert User.original_objects.filter(pk=identifier).values().get() == {**before, 'tables_settings': after}, (case['name'], 'wrong mutation')
                changes = json.dumps(before['tables_settings'], sort_keys=True) != json.dumps(after, sort_keys=True)
                records = list(LogEntry.objects.exclude(pk__in=events).filter(object_pk=identifier))
                assert len(records) == int(changes), (case['name'], 'audit count')
                count += int(changes)
                if changes:
                    event = records[0]
                    assert event.changes_dict == {'tables_settings': [json.dumps(before['tables_settings'], sort_keys=True), json.dumps(after, sort_keys=True)]}, (case['name'], event.changes_dict)
                    assert event.object_repr == 'name: ' + before['first_name'] + ' ' + before['last_name'] and event.action == 1 and event.content_type.model == 'user'
                    assert str(event.actor_id) == actor_id and event.actor_email == actor_email and event.additional_data == additional, (case['name'], 'audit attribution')
                    assert event.cid == case['name'] and event.remote_addr == remote_addr
                    indexes = list(AuditLogIndex.objects.filter(log_entry=event))
                    assert len(indexes) == int(association is not None)
                    if indexes: assert str(indexes[0].sport_association_id) == association and indexes[0].resolution_path == 'user.sport_association'
            assert LogEntry.objects.exclude(pk__in=events).count() == count
            assert result.wasSuccessful() and not result.skipped
            print(f'{len(cases)} table-preference HTTP/effect/audit cases passed', flush=True)
        finally:
            LogEntry.objects.exclude(pk__in=events).delete()
            for identifier, (before, *_rest) in expected.items():
                User.original_objects.filter(pk=identifier).update(tables_settings=before['tables_settings'])
        assert snapshot() == seeded and cache_snapshot() == cache_before

    def concurrent_updates(base):
        case = cases[0]; identifier = case['target']; before = expected[identifier][0]
        for scenario, count in (('unrelated', 1), ('duplicate', 2), ('competing', 2)):
            events = set(LogEntry.objects.values_list('pk', flat=True))
            try:
                with ThreadPoolExecutor(max_workers=count) as pool:
                    with transaction.atomic():
                        User.original_objects.select_for_update().get(pk=identifier)
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()'); blocker = cursor.fetchone()[0]
                        def blocked():
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT pg_stat_clear_snapshot()')
                                cursor.execute('WITH RECURSIVE waiting(pid) AS (SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN waiting w ON w.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM waiting', [blocker])
                                return cursor.fetchone()[0]
                        futures = []
                        for i in range(count):
                            value = {'writer': i + 1} if scenario == 'competing' else {'same': True}
                            futures.append(pool.submit(Client(base, case['token']).request, 'POST', '/profile/settings/tables', {'tables_settings': value}, {'X-Correlation-ID': 'tables-race-' + scenario}))
                            poll(blocked, lambda n: n == i + 1, timeout=15, interval=.01)
                        if scenario == 'unrelated':
                            User.original_objects.filter(pk=identifier).update(password='!new-credential', first_name='Parallel')
                    results = [future.result(timeout=20) for future in futures]
                assert all(result[:2] == (200, {'status': 'success'}) for result in results)
                row = User.original_objects.filter(pk=identifier).values().get()
                value = {'writer': 2} if scenario == 'competing' else {'same': True}
                want = {**before, 'tables_settings': value}
                if scenario == 'unrelated': want.update(password='!new-credential', first_name='Parallel')
                assert row == want, (scenario, 'table preference save overwrote concurrent personal/credential fields')
                audits = list(LogEntry.objects.exclude(pk__in=events).order_by('id'))
                values = [{'writer': 1}, {'writer': 2}] if scenario == 'competing' else [value]
                previous = before['tables_settings']
                assert len(audits) == len(values), (scenario, 'duplicate or missing audit')
                for event, after in zip(audits, values):
                    assert event.changes_dict == {'tables_settings': [json.dumps(previous, sort_keys=True), json.dumps(after, sort_keys=True)]}, (scenario, 'stale audit')
                    assert str(event.actor_id) == identifier and event.cid == 'tables-race-' + scenario
                    previous = after
            finally:
                LogEntry.objects.exclude(pk__in=events).delete()
                User.original_objects.filter(pk=identifier).update(tables_settings=before['tables_settings'], first_name=before['first_name'], password=before['password'])
            assert snapshot() == seeded and cache_snapshot() == cache_before
        print('Three table-preference locked-row races passed', flush=True)

    server = uvicorn.Server(uvicorn.Config(get_asgi_application(), host='0.0.0.0', port=0, lifespan='off', log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start(); api = None
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(.01)
        django_base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        concurrent_updates(django_base)
        contracts(django_base)
        from TESTS.adapters.go.server import build_server
        build_server(work / 'profile-tables-server', log, tool_env)
        with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); http_port = sock.getsockname()[1]
        environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': 'assozeta_contract_accounts', 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only', 'DBHOST': str(work), 'DBPORT': '5432', 'REDIS_HOST': host, 'REDIS_PORT': port, 'REDIS_PASSWORD': 'synthetic-identity-only', 'GO_LISTEN_ADDR': f'0.0.0.0:{http_port}', **{name: getattr(settings, name) for name in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')}, 'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False'}
        api = subprocess.Popen([str(work / 'profile-tables-server')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None
            try: return Client(base).request('GET', '/readyz')[0] == 200
            except OSError: return False
        poll(ready, bool, timeout=30); contracts(base); concurrent_updates(base)
        from TESTS.adapters.caddy import proxy
        django_port = server.servers[0].sockets[0].getsockname()[1]
        with proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{http_port}', log) as (gateway, _image):
            _, _, headers = Client(gateway).request('GET', '/healthz', include_headers=True)
            remote_addr = headers['X-Fixture-Client-IP']
            ipaddress.ip_address(remote_addr)
            assert remote_addr != '198.51.100.83', 'untrusted forwarded address was accepted'
            for version in ('v1', 'v2'):
                with patch.dict(os.environ, TESTS_BASE_URL=gateway + '/api/' + version):
                    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_health.HealthContract'))
                    assert result.wasSuccessful() and not result.skipped
                contracts(gateway + '/api/' + version, remote_addr)
                concurrent_updates(gateway + '/api/' + version)
            print('Both versioned APIs passed the actual table-preference contracts and races', flush=True)
        subprocess.run(['go', 'test', '-race', '-tags=profiletablesintegration', './internal/service', '-run', '^TestProfileTablesPostgres$', '-v'], cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_ACCOUNT_TEST_DSN': f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'}, check=True, timeout=90)
        assert snapshot() == seeded and cache_snapshot() == cache_before
        return {'profile_tables_http_cases_per_backend': len(cases), 'profile_tables_concurrency_cases_per_backend': 3,
                'profile_tables_versioned_http_cases_per_backend': len(cases), 'profile_tables_versioned_concurrency_cases_per_backend': 3}
    finally:
        server.should_exit = True; thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try: api.wait(timeout=30)
            except subprocess.TimeoutExpired: api.kill(); api.wait(timeout=5)
        connections.close_all(); raw.close(); path.unlink(missing_ok=True)
        for session in sessions: cache.delete(session_key(session))
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER tables_http_reject_audit ON auditlog_logentry')
            cursor.execute('DROP TRIGGER tables_http_reject_index ON application_auditlogindex')
            cursor.execute('DROP FUNCTION tables_http_reject_audit()')
        with disable_auditlog(): User.original_objects.filter(pk__in=created).delete()
        assert snapshot() == original
