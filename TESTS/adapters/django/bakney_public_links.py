"""Paired-server stub for families whose routes call instance.sso.public_links.source_token.

service_context owns a local HTTP server for the whole run: it records every
request and answers with the replies authored on the current case. The Go
server's environment gets the development flags, APP_URL, the stub as
BAKNEY_SSO_API_BASE and a placeholder UI origin (the flags DEBUG would change
there are pinned to Django's effective settings, as in sso_pairing_http).

Only cases with a 'pairing' entry see a pairing: for the request alone the
self-hosted InstanceConfiguration (primary association: the fixture's own)
and the BakneyPairing row are inserted and Django's settings overridden with
the same values (HTTP origins need DEBUG with the development mode); the rows
are removed before the effect check. The outbound requests must be exactly
the authored ones (path, JSON bytes, the requests library's headers); every
other case must send none.

Placeholders: '$secret' the per-run pairing secret, '$iso<+-seconds>' an
aware UTC isoformat() relative to now, '$naive<+-seconds>' the same without
an offset.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading

from TESTS.adapters.django.sso_pairing_http import REQUESTS_HEADERS

APP_URL, UI_ORIGIN, API_PATH = 'http://localhost', 'https://app.bakney.example', '/api'
DOMAIN = 'public-links.example.test'
STUB = {}


def _values(value):
    if isinstance(value, dict):
        return {key: _values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_values(item) for item in value]
    if value == '$secret':
        return STUB['secret']
    if isinstance(value, str) and value.startswith(('$iso', '$naive')):
        aware = value.startswith('$iso')
        moment = datetime.now(timezone.utc) + timedelta(seconds=int(value[4 if aware else 6:]))
        return moment.isoformat() if aware else moment.replace(tzinfo=None).isoformat()
    return value


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # A client closing its idle keep-alive connection is not a failure.


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': [(k.lower(), v) for k, v in self.headers.items()],
                                'body': body})
            number, script = len(STUB['log']), STUB['script']
            reply = script[number - 1]['reply'] if number <= len(script) else None
        if reply is None:
            status, data = 500, b'{"error": "unexpected request"}'
        elif 'raw' in reply:
            status, data = reply['status'], reply['raw'].encode()
        else:
            status, data = reply['status'], json.dumps(_values(reply['json'])).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _answer


@contextmanager
def service_context(report, log):
    from django.conf import settings
    server = _Server(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    authority = f'http://127.0.0.1:{server.server_address[1]}' + API_PATH
    values = {'DEBUG': True, 'ASSOZETA_DEPLOYMENT_MODE': 'development', 'APP_URL': APP_URL, 'BAKNEY_SSO_API_BASE': authority,
              'BAKNEY_SSO_UI_ORIGIN': UI_ORIGIN}
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    environment = {**json.loads(saved or '{}'), 'DEBUG': 'True', 'ASSOZETA_DEPLOYMENT_MODE': 'development', 'APP_URL': APP_URL,
                   'BAKNEY_SSO_API_BASE': authority, 'BAKNEY_SSO_UI_ORIGIN': UI_ORIGIN,
                   'CORS_ORIGIN_ALLOW_ALL': str(settings.CORS_ORIGIN_ALLOW_ALL),
                   'CORS_ALLOWED_ORIGINS': ','.join(settings.CORS_ALLOWED_ORIGINS),
                   'CSRF_COOKIE_SECURE': str(settings.CSRF_COOKIE_SECURE)}
    report['service_cleanup']['bakney_stub'] = 'running'
    thread.start()
    try:
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps(environment)
        STUB.update(lock=threading.Lock(), log=[], script=[], values=values, authority=authority, secret=secrets.token_urlsafe(32))
        yield
    finally:
        if saved is None:
            os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
        else:
            os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        report['service_cleanup']['bakney_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        STUB.clear()


@contextmanager
def _pairing(spec, association, require):
    """The configuration and pairing rows of one case, removed afterwards (no signals)."""
    from django.db import connection
    from instance.models import InstanceConfiguration
    from instance.sso.models import BakneyPairing
    from instance.sso.protocol import encrypt
    require(not InstanceConfiguration._base_manager.exists() and not BakneyPairing._base_manager.exists(), 'pairing.rows-exist')
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(
        domain=DOMAIN, name='Public links fixture', self_hosted=spec.get('self_hosted', True), primary_association_id=association)])
    config = InstanceConfiguration._base_manager.get(domain=DOMAIN)
    try:
        BakneyPairing._base_manager.bulk_create([BakneyPairing(
            config_id=config.pk, instance_id=spec['instance_id'], pairing_id=spec['pairing_id'], secret_encrypted=encrypt(STUB['secret']),
            authority=STUB['authority'], ui_origin=UI_ORIGIN, remote_pairing_id=spec['remote_pairing_id'],
            remote_generation=spec['generation'], origin=spec.get('origin', APP_URL), association_id=spec.get('association', association),
            association_name='Fixture', state=spec['state'], forwarding_enabled=False)])
        yield
    finally:
        BakneyPairing._base_manager.filter(config_id=config.pk)._raw_delete(connection.alias)
        InstanceConfiguration._base_manager.filter(pk=config.pk)._raw_delete(connection.alias)


def check_requests(case, require):
    with STUB['lock']:
        log = list(STUB['log'])
    expected = (case.get('pairing') or {}).get('authority', [])
    require(len(log) == len(expected), 'authority.request-count')
    port = STUB['authority'].split('://', 1)[1].split('/', 1)[0]
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        body = json.dumps(_values(spec['body'])).encode()
        require(sent['method'] == 'POST' and sent['path'] == API_PATH + spec['path'], 'authority.request-line' + where)
        require(sent['body'] == body, 'authority.body' + where)
        names = [name for name, _ in sent['headers']]
        require(len(names) == len(set(names)), 'authority.duplicate-header' + where)
        require(dict(sent['headers']) == {**REQUESTS_HEADERS, 'host': port, 'content-length': str(len(body))}, 'authority.headers' + where)


class Contract:
    """The family contract with assert_case run under the case's pairing."""

    def __init__(self, contract, association):
        self._contract, self._association = contract, association

    def __getattr__(self, name):
        return getattr(self._contract, name)

    def assert_case(self, case, base_url, *, token, sessions):
        from django.test.utils import override_settings
        require = self._contract.require
        spec = case.get('pairing')
        with STUB['lock']:
            STUB['log'], STUB['script'] = [], (spec or {}).get('authority', [])
        if spec is None:
            status = self._contract.assert_case(case, base_url, token=token, sessions=sessions)
        else:
            association = self._contract.load_source()['ids'][self._association]
            with _pairing(spec, association, require), override_settings(**STUB['values']):
                status = self._contract.assert_case(case, base_url, token=token, sessions=sessions)
        check_requests(case, require)
        return status
