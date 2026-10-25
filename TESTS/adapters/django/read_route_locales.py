"""Independent HTTP language matrix on the accounts fixture's private database.

Expectations are static portable data. Both servers receive ordinary credentials
and database permissions. Complete model snapshots surround each workload; real
PostgreSQL tuple counters also reject writes that were later reverted. Cache and
upstream attribution are checked per request.
"""
import gzip
import hashlib
import http.client
import json
import sys
from pathlib import Path
import threading
import unittest
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'quality-reports/go-migration/read-route-locales'


def _sources():
    paths = set((ROOT / 'BE').rglob('*.py'))
    paths.update(p for p in (ROOT / 'BE_GO').rglob('*')
                 if p.is_file() and p.suffix in ('.go', '.mod', '.sum', '.c', '.h', '.pem'))
    paths.update(ROOT / name for name in (
        'BE_GO/Dockerfile', 'BE_GO/THIRD_PARTY_NOTICES.md',
        'TESTS/adapters/django/read_route_locales.py',
        'TESTS/contracts/test_read_route_locales.py',
        'TESTS/fixtures/read-route-locales.json',
        'TESTS/adapters/django/accounts.py', 'TESTS/adapters/django/settings.py',
        'TESTS/adapters/django/payment_info_stats.py',
        'TESTS/adapters/django/production_runtime.py',
        'TESTS/adapters/go/payment_category_server.py', 'TESTS/adapters/go/server.py',
        'TESTS/adapters/django/login_jpeg.py', 'TESTS/adapters/caddy.py',
        'TESTS/http_client.py', 'selfhost/caddy/Caddyfile', 'UI/Dockerfile',
    ))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def _runtime_sources():
    import gettext
    import locale
    import django
    import rest_framework
    from importlib.metadata import version

    catalogs = {}
    for module in (django, rest_framework):
        base = Path(module.__file__).resolve().parent
        for path in sorted(base.rglob('LC_MESSAGES/django.mo')):
            catalogs[module.__name__ + '/' + str(path.relative_to(base))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'python': sys.version.split()[0],
            'django': version('Django'), 'drf': version('djangorestframework'),
            'gettext_sha256': hashlib.sha256(Path(gettext.__file__).read_bytes()).hexdigest(),
            'locale_sha256': hashlib.sha256(Path(locale.__file__).read_bytes()).hexdigest(),
            'catalog_sha256': catalogs}


def _request(base, case, token, session, request_id):
    target = urlsplit(base)
    headers = list(case.get('headers', {}).items())
    for name, key in (('Accept-Language', 'accept_language_values'),
                      ('Cookie', 'cookie_values')):
        if key in case:
            headers = [(k, v) for k, v in headers if k.lower() != name.lower()]
            headers.extend((name, value) for value in case[key])
    headers = [(name, session if value == '__OWNED_SESSION__' else value)
               for name, value in headers]
    headers.append(('X-Contract-Case', request_id))
    if token:
        headers.append(('Authorization', 'Bearer ' + token))
    body = case.get('request_body')
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode()
        if not any(k.lower() == 'content-type' for k, _ in headers):
            headers.append(('Content-Type', 'application/json'))
    elif isinstance(body, str):
        body = body.encode()
    client = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=30)
    try:
        client.putrequest(case['method'], target.path.rstrip('/') + case['path'],
                          skip_accept_encoding=True)
        for name, value in headers:
            client.putheader(name, value)
        if body is not None:
            client.putheader('Content-Length', str(len(body)))
        client.endheaders(body)
        response = client.getresponse()
        wire = response.read()
        received = response.headers
        if received.get('Content-Encoding') == 'gzip':
            wire = gzip.decompress(wire)
        return response.status, json.loads(wire), received
    finally:
        client.close()


def run(work, log, tool_env, address):
    from auditlog.context import disable_auditlog
    from django.apps import apps
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from application.models import User, SportAssociation
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.contracts.test_read_route_locales import fixture, assert_http_case
    from TESTS.adapters.django.payment_info_stats import capture, compare, require_no_user_writes
    from TESTS.http_client import poll
    import redis
    import uvicorn

    assert connections['default'].settings_dict['NAME'] == 'assozeta_contract_accounts'
    assert str(connections['default'].settings_dict['HOST']).startswith('/tmp/az-accounts-')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = OUTPUT / 'http-results.json'
    report = {'success': False, 'cleanup_verified': False, 'workloads': [],
              'complete_route_parity': False,
              'caddy_containers': [],
              'scope': 'Static language selection and six framework messages; other error paths remain separate'}
    sources = _sources()
    runtime_sources = _runtime_sources()
    models = [m for m in apps.get_models(include_auto_created=True)
              if m._meta.managed and not m._meta.proxy]
    assert len(models) == 128
    report['managed_models'] = len(models)

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values()) for m in models}

    host, port = address.rsplit(':', 1)
    raw = redis.Redis(host=host, port=int(port), db=1, password='synthetic-identity-only')

    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter()
                if not key.startswith(b'assozeta:go:readiness:')}

    original, original_cache = snapshot(), cache_snapshot()
    created, observed = [], {}
    session = 'a3000000-0000-4000-8000-000000780010'
    server = thread = None
    session_created = False
    go_requested = tool_env.get('TESTS_READ_ROUTE_LOCALES_GO') == '1'
    versioned = tool_env.get('TESTS_READ_ROUTE_LOCALES_VERSIONED') == '1'
    assert not versioned or go_requested

    def seed(model, number, **values):
        key = UUID(f'a3000000-0000-4000-8000-{number:012d}')
        assert not model._base_manager.filter(pk=key).exists()
        row = model(pk=key, **values)
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    def save():
        receipt.write_text(json.dumps(report, indent=2) + '\n')

    try:
        owner = seed(User, 780001, username='locale-owner', email='locale-owner@example.test',
                     role=1, password='!synthetic')
        athlete = seed(User, 780002, username='locale-athlete', email='locale-athlete@example.test',
                       role=2, password='!synthetic')
        collaborator = seed(User, 780003, username='locale-collaborator',
                            email='locale-collaborator@example.test', role=3,
                            connected_user=owner, collaborator_role=1, password='!synthetic')
        association = seed(SportAssociation, 780004, user=owner, denomination='Locale fixture')
        assert cache.get(session_key(session)) is None
        cache.set(session_key(session), {'actor_id': str(owner.pk),
                  'target_id': str(collaborator.pk), 'association_id': str(association.pk)}, 3600)
        session_created = True
        tokens = {'anonymous': None}
        for name, user in [('owner', owner), ('athlete', athlete), ('scoped-owner', owner)]:
            access = AccessToken()
            access['user_id'] = str(user.pk)
            tokens[name] = str(access)
        data = fixture()
        cases = []
        for selection in data['selection_cases']:
            for route, path, identity, title, description in (
                ('instance-manifest', '/instance/manifest.json', 'anonymous',
                 'Instance Manifest', 'GET /instance/manifest.json\nDynamic PWA manifest.'),
                ('collaborators-list', '/collaborators/list', 'owner', 'Collaborators List', ''),
            ):
                cases.append({**selection, 'name': route + '-' + selection['name'],
                    'route': route, 'method': 'OPTIONS', 'path': path, 'identity': identity,
                    'status': 200, 'body': {'name': title, 'description': description,
                        'renders': ['application/json'], 'parses': ['application/json',
                            'application/x-www-form-urlencoded', 'multipart/form-data']}})
        cases.extend(data['http_cases'])
        assert len({case['name'] for case in cases}) == len(cases)
        assert all(case['identity'] in tokens for case in cases)
        report.update(expected_cases=len(cases), supported_languages=len(data['supported_languages']),
                      selection_cases=len(data['selection_cases']), message_cases=len(data['http_cases']))
        application = get_asgi_application()

        async def observed_application(scope, receive, send):
            if scope['type'] == 'http':
                marker = dict(scope['headers']).get(b'x-contract-case', b'').decode()
                if marker:
                    observed.setdefault(marker, []).append({'method': scope['method'], 'path': scope['path']})
            await application(scope, receive, send)

        server = uvicorn.Server(uvicorn.Config(observed_application,
            host='0.0.0.0' if versioned else '127.0.0.1', port=0,
            lifespan='off', log_level='critical'))
        # Seed writers must finish before the all-user-table counter baseline.
        connections.close_all()
        writes_before = capture(connections['default'])
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        poll(lambda: server.started, bool, timeout=15)
        django_port = server.servers[0].sockets[0].getsockname()[1]
        django_base = f'http://127.0.0.1:{django_port}'
        test = unittest.TestCase()

        def replay(base, label, django_expected, selected=None, negative=False):
            before, before_cache = snapshot(), cache_snapshot()
            workload = {'backend': label, 'success': False, 'cases': []}
            if not negative:
                report['workloads'].append(workload)
            failures = []
            for case in cases if selected is None else selected:
                marker = label + '-' + case['name']
                observed.pop(marker, None)
                status, body, headers = _request(base, case, tokens[case['identity']], session, marker)
                assert cache_snapshot() == before_cache, 'Read changed cache bytes'
                seen = observed.pop(marker, [])
                expected_seen = [{'method': case['method'], 'path': case['path'].split('?')[0]}] if django_expected else []
                row = {'name': case['name'], 'success': False, 'status': status,
                       'django_requests': len(seen)}
                workload['cases'].append(row)
                if seen != expected_seen:
                    assert snapshot() == before and cache_snapshot() == before_cache
                    raise RuntimeError('upstream_mismatch')
                try:
                    assert_http_case(test, case, status, body, headers)
                    vary = {token.strip().lower() for value in headers.get_all('Vary', [])
                            for token in value.split(',')}
                    test.assertIn('accept-language', vary)
                    test.assertIsNone(headers.get('Set-Cookie'))
                    test.assertEqual(headers.get('Content-Type', '').split(';')[0], 'application/json')
                    test.assertEqual(headers.get('WWW-Authenticate'),
                                     'Bearer realm="api"' if status == 401 else None)
                    row['success'] = True
                except AssertionError as error:
                    row['failure'] = str(error)
                    failures.append(case['name'])
                if len(workload['cases']) % 50 == 0:
                    save()
            assert snapshot() == before and cache_snapshot() == before_cache
            workload.update(success=not failures, managed_state_preserved=True, cache_preserved=True)
            save()
            assert not failures, ('Locale response mismatches', label, failures)
            print(f'{label}: {len(workload["cases"])} locale cases passed', flush=True)

        replay(django_base, 'django', True)
        if go_requested:
            from TESTS.adapters.go.payment_category_server import native_server
            with native_server(work, log, tool_env, address, versioned=versioned) as runtime:
                report['native_binary_sha256'] = hashlib.sha256(
                    (work / 'payment-categories-server').read_bytes()).hexdigest()
                replay(runtime['base_url'], 'go', False)
                if versioned:
                    from TESTS.adapters.caddy import proxy
                    with proxy(f'host.docker.internal:{django_port}', runtime['proxy_upstream'],
                               log, network=runtime.get('network'),
                               container_observer=report['caddy_containers'].append) as (base, image):
                        report['caddy_image'] = image
                        replay(base + '/api/v1', 'v1', True)
                        replay(base + '/api/v2', 'v2', False)
                    with proxy(f'host.docker.internal:{django_port}',
                               f'host.docker.internal:{django_port}', log,
                               container_observer=report['caddy_containers'].append) as (base, _):
                        try:
                            replay(base + '/api/v2', 'negative-v2', False, cases[:1], True)
                        except RuntimeError as error:
                            assert str(error) == 'upstream_mismatch'
                            report['fallback_rejected'] = True
                        else:
                            raise AssertionError('Django fallback accepted as Go')
                    report['proxy_cleanup_verified'] = True
        server.should_exit = True
        thread.join(timeout=30)
        assert not thread.is_alive()
        connections.close_all()
        report['database_write_evidence'] = compare(writes_before, capture(connections['default']))
        require_no_user_writes(report['database_write_evidence'])
        report['success'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        errors = []

        def clean(stage, operation):
            try:
                operation()
            except BaseException as error:
                errors.append({'stage': stage, 'error_type': type(error).__name__})

        def stop():
            if thread:
                server.should_exit = True
                thread.join(timeout=30)
                assert not thread.is_alive()

        clean('server', stop)
        if session_created:
            clean('session', lambda: cache.delete(session_key(session)))
        with disable_auditlog():
            for model, key in reversed(created):
                clean('fixture', lambda model=model, key=key: model._base_manager.filter(pk=key).delete())
        clean('connections', connections.close_all)

        def verify():
            assert snapshot() == original and cache_snapshot() == original_cache

        clean('state', verify)
        clean('redis', raw.close)
        report.update(cleanup_verified=not errors, cleanup_errors=errors,
                      source_stable=_sources() == sources, source_sha256=sources,
                      runtime_sources=runtime_sources, runtime_stable=_runtime_sources() == runtime_sources)
        report['success'] = bool(report['success'] and report['cleanup_verified'] and report['source_stable'] and report['runtime_stable'])
        save()
    assert report['success']
    return report
