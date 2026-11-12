"""Offline composition controls; ALL HTTP, owner and snapshot inputs are doubles.

Positive composition uses the actual fixed denial route policy without patches.
No sockets, services, browser or live acceptance.
"""
import copy
from contextlib import contextmanager, ExitStack
from email.message import Message
import io
import json
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from TESTS import test_message_mutations_browser as controls
from TESTS.adapters.django import message_mutations_browser as browser
from TESTS.adapters.django import message_mutations_browser_denials as companion
from TESTS.adapters.django import message_mutations_browser_runtime as mutations
from TESTS.adapters.django import message_reads_browser_runtime as shared
from TESTS.adapters.django.historical_read_cache import (
    HistoricalReadCacheEntry, HistoricalReadCacheSnapshot, EXPIRING_CONTRAST_KEY,
)

FOREIGN_ROUTE = '/api/communications/messages/a1000000-0000-4000-8000-000000702004/delete'
PRIVATE = 'PRIVATE-do-not-publish'


class OfflineParent:
    """Explicit synthetic peer/journal/ASGI, NEVER actual observation evidence."""
    def __init__(self, version, fault=None):
        self.version, self.fault = version, fault
        self.cases = browser.authored_spec()['denials']
        self.models = controls.state_control()
        self.cache, self.contrasts = controls.cache_control()
        self.observer = shared.CompleteAsgiObserver(None)
        self.journal = None
        self.connections, self.sent, self.lifecycle = [], [], []
        self.snapshots = 0
        self.port = 1001 if version == 'v1' else 1002
        self.guard = {'running': True, 'pid': 123, 'instance': 'explicit-offline-double'}
        self.tokens = {actor: 'offline-token-' + actor for actor in ('owner', 'foreign_owner', 'restricted_collaborator')}
        self.identities = {token: browser.authored_spec()['actors'][actor]['user_id'] for actor, token in self.tokens.items()}
        if fault == 'wrong-actor':
            self.identities[self.tokens['owner']] = self.identities[self.tokens['foreign_owner']]
        if fault == 'absent-target':
            identifier = self.cases[2]['path'].split('/')[-2]
            self.models[browser.contract.MESSAGE] = [r for r in self.models[browser.contract.MESSAGE]
                                                     if r['message_id'] != identifier]
        if fault == 'wrong-ownership':
            for row in self.models[browser.contract.MESSAGE]:
                if row['message_id'] == self.cases[2]['path'].split('/')[-2]:
                    row['sport_association_id'] = browser.authored_spec()['actors']['owner']['association_id']

    def snapshot(self, *_args):
        self.snapshots += 1
        # Full state/cache is provided on every call; final snapshot is #10.
        if self.fault == 'final-state' and self.snapshots == 10:
            self.models['offline.Model0'][0]['sentinel'] = PRIVATE
        if self.fault == 'between-state' and self.snapshots == 4:
            self.models['offline.Model0'][0]['sentinel'] = PRIVATE
        if self.fault == 'trailing-snapshot' and self.snapshots == 10:
            self.extra()
        return {'models': copy.deepcopy(self.models), 'cache': self.cache}

    def process(self):
        if self.fault == 'process' and self.sent:
            return dict(self.guard, instance='replaced-offline-double')
        if self.fault == 'dead-process' and self.sent:
            return dict(self.guard, running=False)
        return copy.deepcopy(self.guard)

    def identity(self, token):
        if self.fault == 'invalid-token':
            raise ValueError(PRIVATE)
        if self.fault == 'late-actor' and self.sent:
            return 'wrong'
        return self.identities[token]

    def handler(self, **kwargs):
        self.journal = kwargs['journal']
        assert kwargs['consumer'] == 'mutation-denials' and kwargs['artifacts'] == {}
        return object()

    @contextmanager
    def owner(self, _handler, *, journal):
        self.lifecycle.append('proxy-enter')
        try:
            yield 'http://127.0.0.1:9999'
        finally:
            self.lifecycle.append('proxy-exit')
            if self.fault == 'trailing-proxy': self.extra()
            if self.fault == 'cleanup': raise RuntimeError(PRIVATE)

    def extra(self):
        if self.version == 'v1' or self.fault == 'asgi-extra':
            self.observer.events.append({'seq': len(self.observer.events), 'method': 'GET',
                'path': '/unrelated-private', 'cid': '', 'status': 200, 'error_type': None})
        else:
            self.journal.record({'cid': '', 'method': 'GET', 'path': '/', 'status': 200,
                'backend': 'ui-static', 'intended_backend': 'ui-static'})

    def connection(self, host, port, timeout):
        assert host == '127.0.0.1' and port == 9999 and 0 < timeout <= 5
        parent = self
        connection = Mock()
        connection.sock.getpeername.return_value = ('127.0.0.1', 9999)
        connection.sock.getsockname.return_value = ('127.0.0.2' if self.fault == 'ip' else '127.0.0.1', 54321)
        self.connections.append(connection)
        if self.fault == 'connection-cleanup': connection.close.side_effect = RuntimeError(PRIVATE)
        def request(method, target, *, body, headers):
            number = len(parent.sent)
            case = parent.cases[number]
            # Assert exact authored request, token, language and permitted CID.
            assert method == case['method'] and target == '/api' + case['path']
            assert body == (json.dumps(case['body']).encode() if 'body' in case else None)
            assert headers['Authorization'] == 'Bearer ' + parent.tokens[case['actor']]
            assert headers['Accept-Language'] == 'en' and not any(k.lower() in
                ('user-id', 'cookie') for k in headers)
            cid = headers['X-Correlation-ID']
            shared.validate_correlation(cid, {'path': case['path']}, 'mutation-denials')
            parent.sent.append((method, target, body, dict(headers)))
            if parent.fault == 'request-failure': raise RuntimeError(PRIVATE)
            proof = controls.traffic_control(method, case['path'], case['response'], cid=cid,
                version=parent.version, request_body=body)
            proof['status'] = proof['proxy']['status'] = case['status']
            if proof['django_events']: proof['django_events'][0]['status'] = case['status']
            event = dict(proof['proxy'], intended_backend=proof['proxy']['backend'],
                peer=['127.0.0.1', parent.port], intended_peer=['127.0.0.1', parent.port],
                set_cookie_sha256=shared.json_digest([]))
            if parent.fault == 'wrong-target': event['path'] = parent.cases[1]['path']
            if parent.fault == 'wrong-cid': event['cid'] = f'message-mutations-{parent.version}-owner-delete-9'
            if parent.fault == 'wrong-backend': event['backend'] = 'django-v1' if parent.version == 'v2' else 'go-v2'
            if parent.fault == 'wrong-peer': event['peer'] = ['127.0.0.1', 4444]
            if parent.fault == 'fallback': event['fallback'] = True
            wire = proof['body']
            response_headers = list(proof['headers'].items())
            if parent.fault == 'bad-status': event['status'] = 200
            if parent.fault == 'bad-body': wire = b'{"private":"' + PRIVATE.encode() + b'"}'
            if parent.fault == 'body-tail': wire += PRIVATE.encode()
            private = dict(event, body=wire, wire_body=wire, response_headers=response_headers,
                headers=dict(response_headers), request_body=body, complete=True)
            if parent.fault == 'private-mismatch': private['body'] = PRIVATE.encode()
            if parent.fault == 'body-mismatch': private['wire_body'] = PRIVATE.encode()
            if parent.fault == 'request-mismatch': private['request_body'] = b'{}'
            if parent.fault == 'incomplete': private['complete'] = False
            if parent.fault == 'model': parent.models['offline.Model0'][0]['sentinel'] = PRIVATE
            if parent.fault == 'audit': parent.models['auditlog.LogEntry'].pop()
            if parent.fault == 'delivery': parent.models['communications.MessageTransaction'].pop()
            if parent.fault == 'schema': parent.models.pop('offline.Model0')
            if parent.fault in ('cache-value', 'cache-expiry', 'cache-extra', 'cache-missing'):
                entries = dict(parent.cache.entries)
                if parent.fault == 'cache-missing': entries.pop(EXPIRING_CONTRAST_KEY)
                elif parent.fault == 'cache-extra': entries[b'private-extra'] = HistoricalReadCacheEntry(b'string', PRIVATE.encode(), -1)
                else:
                    entries[EXPIRING_CONTRAST_KEY] = HistoricalReadCacheEntry(b'string',
                        PRIVATE.encode() if parent.fault == 'cache-value' else b'expiring-dump',
                        2000000000001 if parent.fault == 'cache-expiry' else 2000000000000)
                parent.cache = HistoricalReadCacheSnapshot(MappingProxyType(entries))
            if parent.fault not in ('missing-event', 'missing-all'):
                parent.journal.record(event)
            if parent.fault not in ('missing-private', 'missing-all'):
                parent.journal.record_private(private)
            if parent.fault != 'missing-asgi':
                for row in proof['django_events']:
                    parent.observer.events.append(dict(row, seq=len(parent.observer.events)))
            if parent.fault == 'duplicate-event': parent.journal.record(event)
            if parent.fault == 'extra-private':
                parent.journal.record_private(dict(private, cid=cid + '0'))
            if parent.fault == 'asgi-extra': parent.extra()
            if parent.fault == 'asgi-error' and parent.observer.events:
                parent.observer.events[-1]['error_type'] = PRIVATE
            if parent.fault == 'websocket': parent.observer.websocket_started += 1
            if parent.fault == 'proxy-failure': parent.journal.fail(PRIVATE)
            sent_headers = shared.clean_headers(response_headers, response=True) + [
                ('Content-Length', str(len(wire))), ('X-Fixture-Backend', proof['proxy']['backend']),
                ('Connection', 'close')]
            if parent.fault == 'header-mismatch': sent_headers[0] = ('Content-Type', 'text/plain')
            if parent.fault == 'duplicate-length': sent_headers.append(('Content-Length', str(len(wire))))
            if parent.fault == 'chunked': sent_headers.append(('Transfer-Encoding', 'chunked'))
            if parent.fault == 'oversize': sent_headers[-3] = ('Content-Length', str(shared.MAX_BODY + 1))
            response = Mock()
            if parent.fault == 'response-cleanup': response.close.side_effect = RuntimeError(PRIVATE)
            response.status = 302 if parent.fault == 'redirect' else event['status']
            response.getheaders.return_value = sent_headers
            response.length = len(wire)
            remaining = bytearray(wire)
            def read1(count):
                if parent.fault == 'truncated': return b''
                piece = bytes(remaining[:min(count, 7)])
                del remaining[:len(piece)]
                response.length -= len(piece)
                return piece
            response.read1.side_effect = read1
            response.isclosed.side_effect = lambda: response.length == 0
            connection.getresponse.return_value = response
            # Real HTTPConnection relinquishes its socket for Connection: close.
            def getresponse():
                connection.sock = None
                return response
            connection.getresponse.side_effect = getresponse
        connection.request.side_effect = request
        return connection

    @contextmanager
    def patches(self):
        with ExitStack() as stack:
            stack.enter_context(patch('TESTS.adapters.django.message_mutations.require_private_database'))
            stack.enter_context(patch.object(companion, '_identity', side_effect=self.identity))
            stack.enter_context(patch.object(shared, 'durable_snapshot', side_effect=self.snapshot))
            stack.enter_context(patch.object(shared, 'proxy_handler', side_effect=self.handler))
            stack.enter_context(patch.object(shared, 'owned_http_server', side_effect=self.owner))
            stack.enter_context(patch.object(companion.http.client, 'HTTPConnection', side_effect=self.connection))
            # Canonical assertion function remains real, with calls inspected.
            verifier = stack.enter_context(patch.object(browser, 'verify_denial', wraps=browser.verify_denial))
            yield verifier

    def run(self, **kwargs):
        return companion.run_denials(version=self.version, apps=None, raw=None,
            fixture={'tokens': self.tokens, 'contrasts': self.contrasts}, observer=self.observer,
            django_port=1001, go_port=1002, process_guard=self.process,
            remote_addr='127.0.0.1', **kwargs)


class DenialPolicyTests(unittest.TestCase):
    """Actual Python policy/handler/observer, with memory-only upstream I/O."""

    def test_exact_authored_routes_and_existing_browser_profiles(self):
        spec = browser.authored_spec()
        expected = {'/api' + case['path']: case['method'] for case in spec['denials']}
        self.assertEqual(shared.consumer_routes('mutation-denials'), expected)
        self.assertEqual(shared.consumer_routes(), shared.browser.SELECTED_ROUTES)
        normal = dict(shared.browser.SELECTED_ROUTES)
        normal['/api/communications/messages/add'] = 'POST'
        normal.update({'/api' + spec['actors'][actor]['delete']['path']: 'DELETE'
                       for actor in browser.READERS})
        self.assertEqual(shared.consumer_routes('mutations'), normal)
        # A caller cannot mutate the next invocation's policy.
        shared.consumer_routes('mutation-denials').clear()
        for version in ('v1', 'v2'):
            cids = companion._preflight(version, spec['denials'])
            self.assertEqual(len(cids), 4)
            for case in spec['denials']:
                self.assertEqual(shared.route_target('/api' + case['path'], case['method'],
                    version=version, artifacts={}, consumer='mutation-denials'),
                    {'kind': 'selected', 'backend': 'django-v1' if version == 'v1' else 'go-v2',
                     'path': case['path']})
            for profile in ('reads', 'mutations'):
                with self.subTest(version=version, profile=profile), self.assertRaises(shared.HarnessError):
                    shared.route_target(FOREIGN_ROUTE, 'DELETE', version=version,
                        artifacts={}, consumer=profile)

    def test_denial_profile_rejects_every_other_route_method_query_and_upgrade(self):
        spec = browser.authored_spec()
        excluded = set(shared.browser.SELECTED_ROUTES) | set(shared.browser.COEXISTENCE_ROUTES) | {
            '/', '/index.html', '/asset.js', '/oauth2/login', '/api/oauth2/login',
            '/api/communications/messages/list', '/api/unknown', '/unrelated',
            '/api/communications/messages/00000000-0000-4000-8000-000000000000/delete',
        }
        selected = shared.consumer_routes('mutation-denials')
        excluded -= set(selected)
        for version in ('v1', 'v2'):
            for path in excluded:
                for method in ('GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS', 'TRACE', 'delete', ''):
                    with self.subTest(version=version, path=path, method=method), self.assertRaises(shared.HarnessError):
                        shared.route_target(path, method, version=version,
                            artifacts={'index.html': 'a' * 64, 'asset.js': 'b' * 64}, consumer='mutation-denials')
            for case in spec['denials']:
                path, method = '/api' + case['path'], case['method']
                for wrong in ('GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS', 'TRACE', 'post', ''):
                    if wrong == method: continue
                    with self.subTest(version=version, path=path, method=wrong), self.assertRaises(shared.HarnessError):
                        shared.route_target(path, wrong, version=version, artifacts={}, consumer='mutation-denials')
                for target in (path + '?', path + '?x=1', path + '?v=0', path + '#', path + '#x',
                        path + '/', path.replace('/messages/', '/messages//'),
                        path.replace('/communications/', '/%63ommunications/'),
                        path.replace('/communications/', '/%2563ommunications/'),
                        '/api/../' + path[1:], 'http://127.0.0.1' + path, '/' + path):
                    with self.subTest(version=version, target=target), self.assertRaises(shared.HarnessError):
                        shared.route_target(target, method, version=version, artifacts={}, consumer='mutation-denials')
                with self.assertRaises(shared.HarnessError):
                    shared.route_target(path, method, version=version, artifacts={},
                        upgrade=True, consumer='mutation-denials')
            for path in shared.browser.WEBSOCKET_ROUTES:
                for upgrade in (False, True):
                    with self.subTest(version=version, path=path, upgrade=upgrade), self.assertRaises(shared.HarnessError):
                        shared.route_target(path, 'GET', version=version, artifacts={},
                            upgrade=upgrade, consumer='mutation-denials')

    def test_denial_correlations_bind_canonical_actor_action_route_and_version(self):
        cases = browser.authored_spec()['denials']
        for version in ('v1', 'v2'):
            for number, case in enumerate(cases, 1):
                route = {'path': case['path'], 'backend': 'django-v1' if version == 'v1' else 'go-v2'}
                action = 'create' if case['method'] == 'POST' else 'delete'
                cid = f'message-mutations-{version}-{case["actor"]}-{action}-{number}'
                shared.validate_correlation(cid, route, 'mutation-denials')
                for bad in ('', cid + '-tail', cid.replace(version, 'v3'),
                        cid.replace(version, 'v2' if version == 'v1' else 'v1'),
                        cid.rsplit('-', 1)[0] + '-0', cid.rsplit('-', 1)[0] + '-01',
                        cid.replace(case['actor'], 'full_collaborator'),
                        cid.replace(action, 'read'), cid.replace(action, 'login'),
                        cid.replace(action, action + '_reload')):
                    with self.subTest(version=version, case=case['name'], cid=bad), self.assertRaises(shared.HarnessError):
                        shared.validate_correlation(bad, route, 'mutation-denials')
                other = cases[2 if case['method'] == 'POST' else 0]
                with self.assertRaises(shared.HarnessError):
                    shared.validate_correlation(cid, {'path': other['path']}, 'mutation-denials')
        # Valid syntax still cannot substitute a different authored actor on a route.
        with self.assertRaises(shared.HarnessError):
            shared.validate_correlation('message-mutations-v1-owner-create-1',
                {'path': cases[0]['path']}, 'mutation-denials')

    def dispatch(self, version, case, cid, journal, observer, *, target=None, method=None):
        handler_type = shared.proxy_handler(ui=Path('/unused'), artifacts={}, version=version,
            django_port=1001, go_port=1002, journal=journal, consumer='mutation-denials')
        handler = handler_type.__new__(handler_type)
        handler.command, handler.path = method or case['method'], target or '/api' + case['path']
        handler.server = SimpleNamespace(server_port=9999)
        handler.headers = Message()
        handler.headers['Host'], handler.headers['X-Correlation-ID'] = '127.0.0.1:9999', cid
        body = json.dumps(case['body']).encode() if 'body' in case else None
        if body is not None: handler.headers['Content-Length'] = str(len(body))
        handler.rfile, handler.wfile = io.BytesIO(body or b''), io.BytesIO()
        handler.send_response_only, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        wire = json.dumps(case['response']).encode()
        response = Mock(status=case['status'], length=0)
        response.read.return_value = wire
        response.isclosed.return_value = True
        response.getheaders.return_value = [('Content-Type', 'application/json'), ('X-Private', PRIVATE)]
        connection = Mock()
        connection.sock.getpeername.return_value = ('127.0.0.1', 1001 if version == 'v1' else 1002)

        async def application(scope, receive, send):
            await send({'type': 'http.response.start', 'status': case['status']})
            await send({'type': 'http.response.body', 'body': wire})

        async def receive(): return {'type': 'http.request', 'body': body or b''}
        async def send(message): pass

        def upstream_response():
            if version == 'v1':
                observer.application = application
                coroutine = observer({'type': 'http', 'method': handler.command,
                    'path': handler.path.removeprefix('/api'),
                    'headers': [(b'x-correlation-id', cid.encode())]}, receive, send)
                # All awaits complete synchronously on memory-only send/receive.
                # An asyncio event loop would create an internal wakeup socket.
                try:
                    with self.assertRaises(StopIteration):
                        coroutine.send(None)
                finally:
                    coroutine.close()
            return response
        connection.getresponse.side_effect = upstream_response
        with patch.object(shared.http.client, 'HTTPConnection', return_value=connection) as factory, \
                patch.object(shared.socket, 'socket', side_effect=AssertionError('no sockets')), \
                patch.object(shared.socket, 'socketpair', side_effect=AssertionError('no socket pairs')):
            getattr(handler, 'do_' + handler.command)()
        return handler, connection, factory, wire, body

    def evidence(self, version):
        journal, observer = shared.ProxyJournal(), shared.CompleteAsgiObserver(None)
        for number, case in enumerate(browser.authored_spec()['denials'], 1):
            action = 'create' if case['method'] == 'POST' else 'delete'
            cid = f'message-mutations-{version}-{case["actor"]}-{action}-{number}'
            handler, connection, factory, wire, body = self.dispatch(version, case, cid, journal, observer)
            factory.assert_called_once_with('127.0.0.1', 1001 if version == 'v1' else 1002, timeout=30)
            connection.putrequest.assert_called_once_with(case['method'], case['path'],
                skip_host=True, skip_accept_encoding=True)
            connection.endheaders.assert_called_once_with(body)
            connection.close.assert_called_once()
            self.assertEqual(handler.wfile.getvalue(), wire)
            self.assertEqual(journal.private_traffic[-1]['wire_body'], wire)
            self.assertEqual(journal.private_traffic[-1]['request_body'], body)
        self.assertEqual(journal.failures, [])
        self.assertEqual(journal.active_http, 0)
        observer.wait_idle(timeout=.01, include_websockets=True)
        self.assertNotIn(PRIVATE, json.dumps(journal.events))
        self.assertNotIn('wire_body', json.dumps(journal.events))
        return journal.private_traffic, journal.snapshot(), observer.delta(0)

    def test_real_policy_dispatch_private_journal_and_complete_asgi_both_versions(self):
        for version in ('v1', 'v2'):
            with self.subTest(version=version):
                private, events, asgi = self.evidence(version)
                result = mutations.observed_traffic(private, events, asgi, version=version, consumer='mutation-denials')
                self.assertEqual(len(result), 4)
                self.assertEqual(len(asgi), 4 if version == 'v1' else 0)
                self.assertEqual([row['status'] for row in result], [403, 403, 404, 404])
                self.assertEqual([row['body'] for row in result], [row['body'] for row in private])
                self.assertTrue(all(row['complete'] for row in result))

    def test_denial_dispatch_rejects_route_method_correlation_before_upstream(self):
        case = browser.authored_spec()['denials'][2]
        for version in ('v1', 'v2'):
            cid = f'message-mutations-{version}-owner-delete-3'
            for target, method, correlation in ((FOREIGN_ROUTE + '?x=1', 'DELETE', cid),
                    ('/api/communications/messages/list', 'GET', cid),
                    (FOREIGN_ROUTE, 'POST', cid), (FOREIGN_ROUTE, 'DELETE', ''),
                    (FOREIGN_ROUTE, 'DELETE', cid.replace('owner', 'restricted_collaborator'))):
                journal, observer = shared.ProxyJournal(), shared.CompleteAsgiObserver(None)
                with self.subTest(version=version, target=target, method=method, cid=correlation):
                    handler, connection, factory, _, _ = self.dispatch(version, case, correlation,
                        journal, observer, target=target, method=method)
                    factory.assert_not_called()
                    self.assertTrue(journal.failures)
                    self.assertEqual(journal.events, [])
                    self.assertEqual(journal.private_traffic, [])
                    self.assertEqual(observer.delta(0), [])
                    handler.send_response_only.assert_called_with(502)

    def test_denial_private_windows_reject_malicious_routes_correlations_and_asgi(self):
        for version in ('v1', 'v2'):
            baseline = self.evidence(version)
            for fault in ('method', 'route', 'query', 'cid', 'version', 'empty-cid', 'wire',
                    'incomplete', 'missing-private', 'extra-event', 'duplicate', 'extra-asgi',
                    'missing-asgi', 'asgi-status', 'asgi-error'):
                if version == 'v2' and fault in ('missing-asgi', 'asgi-status', 'asgi-error'): continue
                private, events, asgi = copy.deepcopy(baseline)
                if fault in ('method', 'route', 'query', 'cid', 'version', 'empty-cid'):
                    key, value = {
                        'method': ('method', 'GET'), 'route': ('path', '/oauth2/login'),
                        'query': ('path', events[0]['path'] + '?x=1'),
                        'cid': ('cid', f'message-mutations-{version}-owner-create-1'),
                        'version': ('cid', events[0]['cid'].replace(version, 'v2' if version == 'v1' else 'v1')),
                        'empty-cid': ('cid', ''),
                    }[fault]
                    private[0][key] = events[0][key] = value
                    if asgi: asgi[0][key] = value
                    if fault == 'empty-cid': private.pop(0)
                if fault == 'wire': private[0]['wire_body'] += b'tail'
                if fault == 'incomplete': private[0]['complete'] = False
                if fault == 'missing-private': private.pop()
                if fault == 'extra-event':
                    events.append(dict(events[-1], path='/statistic/dashboard', cid='', backend='django-coexistence'))
                    asgi.append(dict(events[-1], error_type=None))
                if fault == 'duplicate':
                    private.append(copy.deepcopy(private[-1]))
                    events.append(copy.deepcopy(events[-1]))
                    if asgi: asgi.append(copy.deepcopy(asgi[-1]))
                if fault == 'extra-asgi':
                    asgi.append({'method': 'GET', 'path': '/extra', 'cid': '', 'status': 200, 'error_type': None})
                if fault == 'missing-asgi': asgi.pop()
                if fault == 'asgi-status': asgi[0]['status'] = 200
                if fault == 'asgi-error': asgi[0]['error_type'] = PRIVATE
                with self.subTest(version=version, fault=fault), self.assertRaises(shared.HarnessError):
                    mutations.observed_traffic(private, events, asgi, version=version, consumer='mutation-denials')

    def test_consumer_selectors_fail_closed_without_io(self):
        for version in ('v1', 'v2'):
            proof = controls.traffic_control('GET', browser.LIST, [],
                cid=f'message-mutations-{version}-owner-read-1', version=version)
            event = dict(proof['proxy'], intended_backend=proof['proxy']['backend'])
            private = event | {key: proof[key] for key in ('body', 'request_body', 'headers', 'complete')}
            private.update(wire_body=proof['body'], response_headers=list(proof['headers'].items()))
            args = ([private], [event], proof['django_events'])
            self.assertEqual(mutations.observed_traffic(*args, version=version),
                mutations.observed_traffic(*args, version=version, consumer='mutations'))
            with self.assertRaises(shared.HarnessError):
                mutations.observed_traffic(*args, version=version, consumer='mutation-denials')
        for consumer in ('reads', 'unknown', '', None, True, 'mutation-denials-extra'):
            with self.subTest(consumer=consumer), self.assertRaises(shared.HarnessError):
                mutations.observed_traffic([], [], [], version='v1', consumer=consumer)
        for consumer in ('mutation-denials', 'unknown', None):
            with self.subTest(consumer=consumer), patch.object(shared, 'private_write') as write, \
                    patch.object(shared.selectors, 'DefaultSelector') as selector, \
                    self.assertRaises(shared.HarnessError):
                shared.run_node(node=None, module=None, work=None, private_log=None,
                    environment={}, input_data={}, checkpoint=None, probe_origin=None, consumer=consumer)
            write.assert_not_called()
            selector.assert_not_called()
            with self.assertRaises(shared.HarnessError):
                shared.Checkpoints(lambda _: None, consumer=consumer)
            with self.assertRaises(shared.HarnessError):
                shared.prepare_browser_identities(None, None, consumer=consumer)
        for consumer in ('unknown', '', None, True, 'mutation-denials-extra'):
            with self.subTest(consumer=consumer), self.assertRaises(shared.HarnessError):
                shared.consumer_routes(consumer)


class DenialCompanionTests(unittest.TestCase):
    def test_real_http_parser_and_connection_close_without_sockets(self):
        # Real stdlib HTTPConnection/HTTPResponse; all I/O uses in-memory bytes.
        # This catches connection.sock being relinquished on Connection: close.
        actual_connection = companion.http.client.HTTPConnection
        for body in (b'{"detail":"offline"}', b''):
            with self.subTest(body=body):
                stream = io.BytesIO(b'HTTP/1.1 403 Forbidden\r\nContent-Length: ' +
                    str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
                sock = Mock()
                sock.getpeername.return_value = ('127.0.0.1', 9999)
                sock.getsockname.return_value = ('127.0.0.1', 54321)
                sock.makefile.return_value = stream
                connection = actual_connection('127.0.0.1', 9999)
                connection.sock = sock
                connection.connect = Mock()
                case = browser.authored_spec()['denials'][0]
                with patch.object(companion.http.client, 'HTTPConnection', return_value=connection):
                    result = companion._request('http://127.0.0.1:9999', case,
                        'offline-token', 'message-mutations-v1-restricted_collaborator-create-1',
                        '127.0.0.1', companion._Budget(10))
                self.assertEqual(result['wire_body'], body)
                self.assertEqual(result['status'], 403)
                sent = b''.join(call.args[0] for call in sock.sendall.call_args_list)
                self.assertIn(b'Authorization: Bearer offline-token\r\n', sent)
                self.assertTrue(sent.endswith(json.dumps(case['body']).encode()))
                self.assertTrue(stream.closed)
                self.assertIsNone(connection.sock)

    def test_actual_signed_access_token_validation_offline(self):
        # Isolate Django settings/app registry. Uses installed JWT+crypto only;
        # no database access, service, process owner or socket is constructed.
        script = r'''
from django.conf import settings
settings.configure(SECRET_KEY='offline-only', INSTALLED_APPS=[
    'django.contrib.auth', 'django.contrib.contenttypes'], SIMPLE_JWT={
    'ALGORITHM': 'EdDSA', 'SIGNING_KEY': '', 'VERIFYING_KEY': ''})
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
settings.SIMPLE_JWT['SIGNING_KEY'] = key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()
settings.SIMPLE_JWT['VERIFYING_KEY'] = key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
import django
django.setup()
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from TESTS.adapters.django.message_mutations_browser_denials import _identity
token = AccessToken()
token['user_id'] = 'actual-signed-offline-actor'
assert _identity(str(token)) == 'actual-signed-offline-actor'
import jwt
payload = dict(token.payload, user_id='tampered-actor')
wrong_key = Ed25519PrivateKey.generate()
bad_signature = jwt.encode(payload, wrong_key, algorithm='EdDSA')
expired = jwt.encode(dict(payload, exp=1), key, algorithm='EdDSA')
refresh = RefreshToken()
refresh['user_id'] = 'actual-signed-offline-actor'
for bad in [bad_signature, expired, str(refresh), 'not-a-jwt']:
    try: _identity(bad)
    except Exception: pass
    else: raise AssertionError('unverified token accepted')
'''
        completed = subprocess.run([sys.executable, '-B', '-c', script],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_import_is_inert_with_resource_creation_forbidden(self):
        script = r'''
import socket, subprocess, os
def forbidden(*args, **kwargs): raise RuntimeError('import side effect')
class GuardedSocket(socket.socket):
    def __new__(cls, *args, **kwargs): return forbidden()
socket.socket = GuardedSocket
socket.create_connection = forbidden
subprocess.Popen = forbidden
os.mkdir = forbidden
from TESTS.adapters.django.message_mutations_browser_denials import run_denials
assert callable(run_denials)
'''
        completed = subprocess.run([sys.executable, '-B', '-c', script],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_inflight_proxy_and_asgi_cannot_close_a_window(self):
        for kind in ('proxy', 'asgi'):
            parent = OfflineParent('v1')
            with parent.patches(), ExitStack() as stack:
                original_request = parent.connection
                def pending(*args, **kwargs):
                    conn = original_request(*args, **kwargs)
                    send = conn.request.side_effect
                    def request(*args, **kwargs):
                        send(*args, **kwargs)
                        if kind == 'proxy': parent.journal.active_http = 1
                        else: parent.observer.active = 1
                    conn.request.side_effect = request
                    return conn
                stack.enter_context(patch.object(companion.http.client, 'HTTPConnection', side_effect=pending))
                if kind == 'proxy':
                    # Expire at the first wait instead of actually sleeping.
                    stack.enter_context(patch.object(companion.time, 'sleep', side_effect=TimeoutError(PRIVATE)))
                else:
                    stack.enter_context(patch.object(parent.observer.condition, 'wait_for',
                        side_effect=lambda predicate, timeout: predicate()))
                with self.subTest(kind=kind), self.assertRaises(companion.DenialCompanionError):
                    parent.run()
            self.assertEqual(parent.lifecycle, ['proxy-enter', 'proxy-exit'])
            self.assertEqual(parent.journal.private_traffic, [])

    def test_positive_each_authored_case_each_version_real_canonical_verifier(self):
        for version in ('v1', 'v2'):
            parent = OfflineParent(version)
            with self.subTest(version=version), parent.patches() as verifier:
                result = parent.run()
            self.assertEqual([r['case'] for r in result['cases']], list(browser.DENIAL_CASE_NAMES))
            self.assertEqual([r['status'] for r in result['cases']], [403, 403, 404, 404])
            self.assertEqual(verifier.call_count, 4)
            for call, case in zip(verifier.call_args_list, parent.cases, strict=True):
                self.assertEqual(call.args, (case['name'],))
                self.assertEqual(call.kwargs['authenticated_user_id'], case['audit_actor'])
                self.assertEqual(len(call.kwargs['before']), 128)
                self.assertEqual(len(call.kwargs['after']), 128)
            self.assertTrue(result['http_denials_verified'])
            self.assertFalse(result['browser_acceptance'])
            self.assertEqual(parent.lifecycle, ['proxy-enter', 'proxy-exit'])
            self.assertEqual(parent.journal.private_traffic, [])
            self.assertEqual(len(parent.sent), 4)
            self.assertTrue(all(c.close.call_count == 1 for c in parent.connections))
            public = json.dumps(result)
            for value in (PRIVATE, 'offline-token-', 'Authorization', 'request_body', 'wire_body', '@example.test'):
                self.assertNotIn(value, public)

    def test_missing_authored_route_blocks_before_proxy_or_http(self):
        original_routes = shared.consumer_routes
        def missing_route(consumer='reads'):
            selected = original_routes(consumer)
            selected.pop(FOREIGN_ROUTE, None)
            return selected
        for version in ('v1', 'v2'):
            parent = OfflineParent(version)
            with self.subTest(version=version), parent.patches(), \
                    patch.object(shared, 'consumer_routes', side_effect=missing_route), \
                    patch('TESTS.adapters.django.message_mutations.require_private_database') as database, \
                    self.assertRaisesRegex(companion.DenialCompanionError, '^denial-companion-route-preflight$'):
                parent.run()
            self.assertEqual(parent.sent, [])
            self.assertEqual(parent.lifecycle, [])
            self.assertIsNone(parent.journal)
            database.assert_not_called()  # Even DB connection access follows route preflight.

    def test_malicious_composition_rejects_with_cleanup_and_private_errors(self):
        faults = ('wrong-actor', 'late-actor', 'invalid-token', 'absent-target', 'wrong-ownership',
            'wrong-target', 'wrong-cid', 'wrong-backend', 'wrong-peer', 'fallback', 'bad-status',
            'bad-body', 'body-tail', 'private-mismatch', 'body-mismatch', 'request-mismatch', 'incomplete',
            'missing-event', 'missing-private', 'missing-all', 'missing-asgi', 'duplicate-event', 'extra-private',
            'asgi-extra', 'asgi-error', 'model', 'audit', 'delivery', 'schema', 'between-state', 'final-state',
            'cache-value', 'cache-expiry', 'cache-extra', 'cache-missing', 'process', 'dead-process', 'ip',
            'websocket', 'proxy-failure', 'header-mismatch', 'duplicate-length', 'chunked', 'oversize',
            'redirect', 'truncated', 'request-failure', 'trailing-proxy', 'trailing-snapshot', 'cleanup',
            'response-cleanup', 'connection-cleanup')
        for version in ('v1', 'v2'):
            for fault in faults:
                if version == 'v2' and fault in ('missing-asgi', 'asgi-error'): continue
                parent = OfflineParent(version, fault)
                with self.subTest(version=version, fault=fault), parent.patches(), \
                        self.assertRaises(companion.DenialCompanionError) as caught:
                    parent.run()
                self.assertRegex(str(caught.exception), r'^denial-companion-[a-z-]+$')
                self.assertNotIn(PRIVATE, str(caught.exception))
                if parent.journal is not None:
                    self.assertEqual(parent.journal.private_traffic, [])
                    self.assertEqual(parent.lifecycle, ['proxy-enter', 'proxy-exit'])
                self.assertTrue(all(c.close.call_count == 1 for c in parent.connections))

    def test_deadline_and_input_bounds_reject_without_io(self):
        for timeout in (0, 9, 181, True, float('inf')):
            parent = OfflineParent('v1')
            with self.subTest(timeout=timeout), parent.patches(), self.assertRaises(companion.DenialCompanionError):
                parent.run(timeout=timeout)
            self.assertEqual(parent.connections, [])
        parent = OfflineParent('v2')
        with parent.patches(), patch.object(companion.time, 'monotonic', side_effect=[0, 121]), \
                self.assertRaises(companion.DenialCompanionError):
            parent.run()
        self.assertEqual(parent.connections, [])

    def test_source_drift_rejects_after_proxy_cleanup(self):
        parent = OfflineParent('v2')
        with parent.patches(), patch.object(companion, 'source_hashes', side_effect=[{'source': 'a'}, {'source': 'b'}]), \
                self.assertRaisesRegex(companion.DenialCompanionError, '^denial-companion-final-window$'):
            parent.run()
        self.assertEqual(parent.lifecycle, ['proxy-enter', 'proxy-exit'])
        self.assertEqual(parent.journal.private_traffic, [])

    def assert_final_boundary_fault_rejected(self, fault, boundary):
        # Preserve actual hashes, reconciliation and all four canonical verdicts.
        # Inject only after that independent evidence succeeds; no success flag
        # or final validation function is substituted by the fault probe.
        for version in ('v1', 'v2'):
            parent = OfflineParent(version)
            hashes, reconcile = companion.source_hashes, mutations.observed_traffic
            hash_calls, injections = 0, []

            def inject():
                self.assertEqual(parent.lifecycle, ['proxy-enter', 'proxy-exit'])
                self.assertEqual(len(parent.sent), 4)
                self.assertEqual(verifier.call_count, 4)
                injections.append(fault)
                if fault == 'process-death':
                    parent.guard['running'] = False
                elif fault == 'completed-websocket':
                    with parent.observer.condition:
                        parent.observer.websocket_started += 1
                        parent.observer.websocket_completed += 1
                    self.assertEqual(parent.observer.active_websockets, 0)
                elif fault == 'state':
                    parent.models['offline.Model0'][0]['sentinel'] = PRIVATE
                elif fault == 'cache':
                    entries = dict(parent.cache.entries)
                    entries[EXPIRING_CONTRAST_KEY] = HistoricalReadCacheEntry(
                        b'string', PRIVATE.encode(), 2000000000000)
                    parent.cache = HistoricalReadCacheSnapshot(MappingProxyType(entries))
                elif fault == 'active-http':
                    with parent.observer.condition:
                        parent.observer.active += 1
                elif fault == 'websocket-error':
                    with parent.observer.condition:
                        parent.observer.websocket_errors += 1
                elif fault == 'traffic':
                    parent.extra()
                else:
                    self.fail('unknown final-boundary fault')

            def source_hashes():
                nonlocal hash_calls
                result = hashes()
                hash_calls += 1
                if boundary == 'source-hashes' and hash_calls == 2:
                    inject()
                return result

            def observed_traffic(private, events, asgi, **kwargs):
                result = reconcile(private, events, asgi, **kwargs)
                if boundary == 'reconciliation' and len(private) == len(events) == 4:
                    inject()
                return result

            with self.subTest(version=version, fault=fault, boundary=boundary), \
                    parent.patches() as verifier, \
                    patch.object(companion, 'source_hashes', side_effect=source_hashes), \
                    patch.object(mutations, 'observed_traffic', side_effect=observed_traffic), \
                    patch.object(parent.observer.condition, 'wait_for',
                        side_effect=lambda predicate, timeout: predicate()), \
                    self.assertRaisesRegex(companion.DenialCompanionError,
                        '^denial-companion-final-window$'):
                parent.run()
            self.assertEqual(injections, [fault])
            self.assertEqual(hash_calls, 2)
            self.assertEqual(parent.journal.private_traffic, [])
            self.assertTrue(all(c.close.call_count == 1 for c in parent.connections))

    def test_process_death_during_final_source_hashes_rejects(self):
        self.assert_final_boundary_fault_rejected('process-death', 'source-hashes')

    def test_completed_websocket_during_final_source_hashes_rejects(self):
        self.assert_final_boundary_fault_rejected('completed-websocket', 'source-hashes')

    def test_final_source_hashes_cannot_outlive_state_or_idle_checks(self):
        for fault in ('state', 'cache', 'active-http', 'websocket-error', 'traffic'):
            self.assert_final_boundary_fault_rejected(fault, 'source-hashes')

    def test_final_reconciliation_cannot_outlive_evidence_checks(self):
        for fault in ('process-death', 'completed-websocket', 'state', 'cache',
                      'active-http', 'websocket-error', 'traffic'):
            self.assert_final_boundary_fault_rejected(fault, 'reconciliation')
