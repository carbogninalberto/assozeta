"""Parent-only production-mode instance updates HTTP runner; imports are inert.

service_context owns, for the whole run and before either server starts:
- the update runner stub: an HTTP/1.1 server on '<dir>/runner.sock' with the
  bearer token in '<dir>/token' (a short directory under /tmp). It answers
  each request with the case's next scripted reply and records every request
  (method, path, headers, body);
- the release API: instance-outbound's HTTPS proxy and private certificate
  authority (HTTPS_PROXY/NO_PROXY, SSL_CERT_FILE, REQUESTS_CA_BUNDLE for both
  servers; urllib's opener is rebuilt), answering inside the tunnel with the
  case's scripted replies and recording each request;
- production mode with ASSOZETA_UPDATER_DIRECTORY on the runner directory, for
  Django (override_settings) and Go (GO_FIXTURE_ENVIRONMENT).
seed_rows inserts the self-hosted InstanceConfiguration. Around each request
the runner state is applied ('no-socket' renames the socket away, 'no-token'
removes the token file, 'empty-token' blanks it) and restored; afterwards the
recorded runner requests (a body exactly json.dumps of the authored 'json')
and release API requests must equal the authored ones. Every case must leave
the complete model state unchanged.
"""
from contextlib import contextmanager
import base64
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import tempfile
import threading
import types

from TESTS.contracts import test_instance_updates_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import instance_outbound_http as outbound

SOURCES = ('TESTS/fixtures/instance_updates_http.json', 'TESTS/contracts/test_instance_updates_http.py',
           'TESTS/adapters/django/instance_updates_http.py', 'TESTS/adapters/django/instance_outbound_http.py',
           'BE/instance/administration.py', 'BE/instance/updater_client.py', 'BE/instance/release_catalog.py',
           'BE/instance/permissions.py', 'BE/instance/models.py', 'BE/instance/urls.py', 'BE/core/settings.py') + harness.SOURCES
STATE = {}
require = base_contract.require


class SequenceRunner:
    """The update runner on a Unix socket: the case's replies in order, recorded requests."""

    def __init__(self, directory):
        self.replies, self.requests, self.lock = [], [], threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def address_string(self):
                return 'runner-client'

            def handle_one(self):
                names = [name.lower() for name in self.headers.keys()]
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                with outer.lock:
                    outer.requests.append({'method': self.command, 'path': self.path,
                                           'headers': {name.lower(): value for name, value in self.headers.items()},
                                           'duplicates': len(names) != len(set(names)), 'body': body})
                    reply = outer.replies.pop(0) if outer.replies else {'status': 599, 'body_b64': ''}
                content = base64.b64decode(reply['body_b64'])
                self.send_response(reply['status'])
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True

            do_GET = do_POST = handle_one

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def get_request(self):
                request, _ = self.socket.accept()
                return request, ('runner-client', 0)

        self.server = Server(str(directory / 'runner.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


@contextmanager
def service_context(report, log):
    from django.test.utils import override_settings
    source = base_contract.load_source()
    saved_go = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    directory = Path(tempfile.mkdtemp(prefix='azu-', dir='/tmp'))
    runner = SequenceRunner(directory)
    try:
        (directory / 'token').write_text(source['runner_token'] + '\n')
        with outbound.service_context(report, log):
            STATE.update(runner=runner, directory=directory, token=source['runner_token'])
            try:
                with override_settings(ASSOZETA_DEPLOYMENT_MODE='production', ASSOZETA_UPDATER_DIRECTORY=str(directory)):
                    os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({
                        **json.loads(saved_go or '{}'), 'ASSOZETA_DEPLOYMENT_MODE': 'production',
                        'ASSOZETA_UPDATER_DIRECTORY': str(directory)})
                    yield
            finally:
                if saved_go is None:
                    os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
                else:
                    os.environ['GO_FIXTURE_ENVIRONMENT'] = saved_go
                STATE.clear()
    finally:
        runner.close()
        shutil.rmtree(directory, ignore_errors=True)


def seed_rows(source, created):
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


@contextmanager
def runner_state(case):
    directory, runner = STATE['directory'], STATE['runner']
    spec = case['runner']
    socket_path, token_path = directory / 'runner.sock', directory / 'token'
    with runner.lock:
        runner.requests.clear()
        runner.replies = list(spec['replies'])
    state = spec['state']
    if state == 'no-socket':
        socket_path.rename(directory / 'runner.sock.off')
    elif state == 'no-token':
        token_path.unlink()
    elif state == 'empty-token':
        token_path.write_text(' \n')
    try:
        yield
    finally:
        if state == 'no-socket':
            (directory / 'runner.sock.off').rename(socket_path)
        token_path.write_text(STATE['token'] + '\n')
    with runner.lock:
        recorded, left = [dict(request) for request in runner.requests], runner.replies
        runner.replies = []
    require(not left, 'runner.unused-replies')
    require(all(not request.pop('duplicates') for request in recorded), 'runner.request-shape')
    expected = [dict(request) for request in spec['expect']]
    require(len(recorded) == len(expected), 'runner.request-count')
    for request, want in zip(recorded, expected):
        payload = want.pop('json', None)
        require(request.pop('body') == (b'' if payload is None else json.dumps(payload).encode()), 'runner.request-body')
        require(request == want, 'runner.request')


def check_github(case):
    with outbound.STUB['lock']:
        log = list(outbound.STUB['log'])
    expected = case['github']
    require(len(log) == len(expected), 'github.request-count')
    for sent, spec in zip(log, expected):
        require(sent['connect'] == spec['connect'] and sent['method'] == spec['method'] and sent['path'] == spec['path'],
                'github.request-line')
        require(sent['headers'] == spec['headers'] and len(sent['names']) == len(set(sent['names'])), 'github.headers')
        require(sent['body'] == b'', 'github.body')


def _assert_case(case, base_url, *, token, sessions):
    with outbound.STUB['lock']:
        outbound.STUB['log'] = []
        outbound.STUB['script'] = case['github']
    with runner_state(case):
        status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    check_github(case)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case)
fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
