"""Parent-only backup actions HTTP runner; imports are inert.

service_context owns, for the whole run and before either server starts, the
update runner stub: an HTTP/1.1 server on '<dir>/runner.sock' with the bearer
token in '<dir>/token' (a short directory under /tmp, inside the Unix socket
path limit). Per case it answers with the scripted response, after reading the
request body (or, 'early', without reading it), and records every request
(method, path, headers, body bytes; an early answer is sent before the body
is read, which is then drained and recorded as 'unread'). Production mode with
ASSOZETA_UPDATER_DIRECTORY on that directory is set for Django
(override_settings) and Go (GO_FIXTURE_ENVIRONMENT), with Django's production
password hashers, and the fixture's upload limit as
ASSOZETA_BACKUP_UPLOAD_MAX_BYTES (plain http:// endpoints stay off). Django is
served by core.asgi's application, so the upload reaches the view through the
stream_uploads wrapper as in production.

seed_rows inserts the self-hosted InstanceConfiguration. Around each request:
- the owner's and the administrator's stored hashes are set (the case's
  'password' for the actor) and put back afterwards, keeping the value the
  request left for the check;
- 'runner' state: the socket is renamed away ('no-socket') or the token file
  removed ('no-token');
- 'cache' presets the password attempts; afterwards the backup keys must be
  exactly 'cache_after' (values, fresh or kept TTLs), and they are deleted.
The effect check: no model changes except the authored UPDATE entry and its
index row, and the expected stored hash (an upgraded one when 'rehash').
"""
from contextlib import contextmanager
from datetime import datetime
import http.server
import json
import os
import re
import shutil
import socketserver
import tempfile
import threading
import time
import types

from TESTS.contracts import test_instance_backups_actions_http as base_contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/instance_backups_actions_http.json', 'TESTS/contracts/test_instance_backups_actions_http.py',
           'TESTS/adapters/django/instance_backups_actions_http.py', 'BE/instance/backups.py', 'BE/instance/remote_backups.py',
           'BE/instance/updater_client.py', 'BE/instance/upload_stream.py', 'BE/instance/permissions.py',
           'BE/instance/models.py', 'BE/instance/urls.py', 'BE/core/asgi.py', 'BE/core/middleware.py',
           'BE/application/signals.py', 'BE/core/settings.py') + harness.SOURCES
AUDIT, INDEX = harness.AUDIT, harness.INDEX
STATE = {}
require = base_contract.require
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')


class RunnerStub:
    """The update runner on a Unix socket: scripted answers, recorded requests."""

    def __init__(self, directory):
        self.directory, self.script, self.requests, self.lock = directory, None, [], threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def address_string(self):
                return 'runner-client'

            def handle_one(self):
                names = [name.lower() for name in self.headers.keys()]
                with outer.lock:
                    script = outer.script or {'status': 500, 'body': '{"error": "unscripted"}', 'early': False}
                body = None
                if script['early']:
                    body = 'unread'
                elif 'Content-Length' in self.headers:
                    body = self.rfile.read(int(self.headers['Content-Length']))
                with outer.lock:
                    outer.requests.append({'method': self.command, 'path': self.path,
                                           'headers': {name.lower(): value for name, value in self.headers.items()},
                                           'duplicates': len(names) != len(set(names)), 'body': body})
                content = script['body'].encode()
                self.send_response(script['status'])
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
                self.wfile.flush()
                if script['early'] and 'Content-Length' in self.headers:
                    # Drain after answering, so the close never discards unread
                    # bytes (which would reset the sender's read of the answer).
                    self.rfile.read(int(self.headers['Content-Length']))
                self.close_connection = True

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_one

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


def asgi_application():
    from core.asgi import application
    return application


@contextmanager
def service_context(report, log):
    from pathlib import Path
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.test.utils import override_settings
    source = base_contract.load_source()
    limit = source['upload_max_bytes']
    require(isinstance(limit, int) and limit > 0, 'fixture.upload-limit')
    require('ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP' not in os.environ, 'fixture.allow-http-unset')
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    directory = Path(tempfile.mkdtemp(prefix='azba-', dir='/tmp'))
    stub = RunnerStub(directory)
    try:
        (directory / 'token').write_text(source['runner_token'] + '\n')
        STATE.update(stub=stub, directory=directory)
        try:
            with override_settings(ASSOZETA_DEPLOYMENT_MODE='production', ASSOZETA_UPDATER_DIRECTORY=str(directory),
                                   PASSWORD_HASHERS=PASSWORD_HASHERS, ASSOZETA_BACKUP_UPLOAD_MAX_BYTES=str(limit)):
                os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({
                    **json.loads(saved or '{}'), 'ASSOZETA_DEPLOYMENT_MODE': 'production',
                    'ASSOZETA_UPDATER_DIRECTORY': str(directory), 'ASSOZETA_BACKUP_UPLOAD_MAX_BYTES': str(limit)})
                yield
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            STATE.clear()
    finally:
        stub.close()
        shutil.rmtree(directory, ignore_errors=True)


def seed_rows(source, created):
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


# ---------------------------------------------------------------------------
# Per-request stimuli
# ---------------------------------------------------------------------------

def our_keys(raw):
    return {key.decode() for key in raw.scan_iter(match=':1:instance:backup-*')}


def apply_cache(case, raw):
    from django.core.cache import cache
    require(our_keys(raw) == set(), 'cache.clean-before')
    for actor, value in case['cache'].get('attempts', {}).items():
        cache.set(f'instance:backup-download-attempts:{actor}', value, timeout=900)
    return {key: raw.get(key) for key in our_keys(raw)}


def check_cache(case, raw, preset):
    from django.core.cache import cache
    keys = set()
    try:
        for actor, spec in case['cache_after'].get('attempts', {}).items():
            spec = spec if isinstance(spec, dict) else {'value': spec, 'ttl': 'kept'}
            key = cache.make_key(f'instance:backup-download-attempts:{actor}')
            keys.add(key)
            ttl = raw.pttl(key)
            require(raw.get(key) == str(spec['value']).encode(), 'cache.attempts-value')
            if spec['ttl'] == 'fresh':
                require(key not in preset and 890_000 < ttl <= 900_000, 'cache.attempts-fresh')
            else:
                require(key in preset and 0 < ttl <= 900_000, 'cache.attempts-kept')
        require(our_keys(raw) == keys, 'cache.keys')
    finally:
        for key in our_keys(raw):
            raw.delete(key)


def resolve_template(template, actual):
    """The template with each '$uuid4' replaced by the actual value found at its place (a canonical uuid4)."""
    if template == '$uuid4':
        require(isinstance(actual, str) and UUID4.fullmatch(actual) is not None, 'runner.request-uuid4')
        return actual
    if isinstance(template, dict):
        require(isinstance(actual, dict), 'runner.request-shape')
        return {key: resolve_template(value, actual.get(key)) for key, value in template.items()}
    if isinstance(template, list):
        require(isinstance(actual, list) and len(actual) == len(template), 'runner.request-shape')
        return [resolve_template(value, item) for value, item in zip(template, actual)]
    return template


def check_request(expected, actual):
    require(not actual.pop('duplicates'), 'runner.request-duplicate-headers')
    spec, body = expected['body'], actual.pop('body')
    headers = dict(expected['headers'])
    if spec is None:
        require(body is None, 'runner.request-no-body')
    elif spec == 'unread':
        require(body == 'unread', 'runner.request-unread')
    elif 'json' in spec:
        require(isinstance(body, bytes), 'runner.request-json-body')
        try:
            parsed = json.loads(body)
        except ValueError:
            raise AssertionError('runner.request-json') from None
        wanted = json.dumps(resolve_template(spec['json'], parsed)).encode()
        require(body == wanted, 'runner.request-json-bytes')
        if headers.get('content-length') == '$length':
            headers['content-length'] = str(len(wanted))
    else:
        require(body == base_contract.materialize(spec), 'runner.request-upload-bytes')
    require({key: expected[key] for key in ('method', 'path')} == {key: actual[key] for key in ('method', 'path')},
            'runner.request-line')
    require(actual['headers'] == headers, 'runner.request-headers')


@contextmanager
def runner_state(case, source):
    directory, stub = STATE['directory'], STATE['stub']
    spec = case['runner']
    socket_path, token_path = directory / 'runner.sock', directory / 'token'
    with stub.lock:
        stub.requests.clear()
        stub.script = spec if 'status' in spec else None
    state = spec['state']
    if state == 'no-socket':
        socket_path.rename(directory / 'runner.sock.off')
    elif state == 'no-token':
        token_path.unlink()
    try:
        yield
    finally:
        if state == 'no-socket':
            (directory / 'runner.sock.off').rename(socket_path)
        token_path.write_text(source['runner_token'] + '\n')
    # An early answer may still be draining the client's body: wait briefly for the record.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with stub.lock:
            if len(stub.requests) >= len(spec['expect']):
                break
        time.sleep(0.02)
    with stub.lock:
        recorded = [dict(request) for request in stub.requests]
        stub.script = None
    require(len(recorded) == len(spec['expect']), 'runner.request-count')
    for expected, actual in zip(spec['expect'], recorded):
        check_request(expected, actual)


@contextmanager
def passwords(case, source):
    from application.models import User
    ids = source['ids']
    hashes = {ids[actor]: value for actor, value in source['hashes'].items()}
    if case['password'] is not None:
        hashes[ids[case['actor']]] = case['password']
    original = dict(User._base_manager.filter(pk__in=list(hashes)).values_list('pk', 'password'))
    for identity, value in hashes.items():
        User._base_manager.filter(pk=identity).update(password=value)
    try:
        yield
    finally:
        STATE['stored'] = {str(pk): value for pk, value in User._base_manager.filter(pk__in=list(hashes)).values_list('pk', 'password')}
        STATE['hashes'] = {str(key): value for key, value in hashes.items()}
        for identity, value in original.items():
            User._base_manager.filter(pk=identity).update(password=value)


def _assert_case(case, base_url, *, token, sessions):
    from django_redis import get_redis_connection
    source = base_contract.load_source()
    raw = get_redis_connection('default')
    preset = apply_cache(case, raw)
    try:
        with passwords(case, source), runner_state(case, source):
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    finally:
        check_cache(case, raw, preset)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def assert_effect(case, before, after, source, context):
    import ipaddress
    from django.contrib.auth.hashers import check_password
    harness.require_unmanaged_unchanged(base_contract, before, after, (AUDIT, INDEX))
    added, indexes = harness.audit_delta(base_contract, before, after)
    expected = case['audit']
    require(len(added) == (1 if expected else 0) and len(indexes) == len(added), 'audit.count')
    if expected:
        event = added[0]
        types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
        actor = source['ids'][expected['actor']]
        instance = source['instance']
        require(event['content_type_id'] == types_[('instance', 'instanceconfiguration')] and event['action'] == 1
                and event['object_pk'] == str(instance['id']) and event['object_id'] == instance['id']
                and event['object_repr'] == f"Instance: {instance['name']} ({instance['domain']})"
                and event['serialized_data'] is None, 'audit.identity')
        require(event['changes'] == expected['changes'] and event['changes_text'] == '', 'audit.changes')
        require(str(event['actor_id']) == actor and event['actor_email'] is None, 'audit.actor')
        require(event['cid'] == case['name'], 'audit.cid')
        require(event['additional_data'] == expected['additional_data']
                and type(event['additional_data'].get('backup')) is type(expected['additional_data'].get('backup'))
                and type(event['additional_data'].get('operation')) is type(expected['additional_data'].get('operation')),
                'audit.additional-data')
        ipaddress.ip_address(event['remote_addr'])
        require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        started = context['prepared']['started']
        stamp = event['timestamp']
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else stamp
        require(stamp.tzinfo is not None and started - 1 <= stamp.timestamp() <= time.time() + 1, 'audit.timestamp')
        index = indexes[str(event['id'])]
        require(str(index['sport_association_id']) == source['ids'][expected['association']]
                and index['resolution_path'] == 'actor.sport_association', 'index.fields')
    stored, hashes = STATE.pop('stored'), STATE.pop('hashes')
    for identity, value in hashes.items():
        if case['rehash'] and identity == source['ids'][case['actor']]:
            require(stored[identity] != value and stored[identity].startswith('pbkdf2_sha256$1000000$')
                    and check_password(source['password'], stored[identity]), 'state.password-upgraded')
        else:
            require(stored[identity] == value, 'state.password-unchanged')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})
