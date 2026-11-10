"""Parent-only instance administration HTTP runner; imports are inert.

Seeds the singleton InstanceConfiguration (self-hosted, primary association
the shared owner's) with stored email settings, diagnostics, an active
diagnostics lease and a Stripe override; secret markers are encrypted with
the run's SECRET_KEY. Every case is checked against the complete model state:
only that row may change, and a write case states every managed field. Stored
secrets are compared by decrypting them; updated_at must advance exactly when
the view saves it. The model is not registered with auditlog; the only entry
is the explicit UPDATE log of a web UI API version change (case['audit']),
with its AuditLogIndex row resolved from the actor's association.

service_context owns, for the whole run and before either server starts,
every service the diagnostics probes reach (both backends see the same ones):
- instance-outbound's HTTPS proxy and private authority, whose leaf also
  covers example.org (APP_URL): the public URL probe's request is recorded
  and answered with the case's 'outbound' script;
- scripted HTTP stubs recording every request: the API on 127.0.0.1:8000
  (Django's fixed API probe address; Go's ASSOZETA_DIAGNOSTICS_API_URL points
  at it), the renderer (PUPPETEER_HOST/PORT) and S3 (AWS_S3_ENDPOINT_URL);
  a reply may 'reset' the connection or 'trickle' its body one byte a second;
- a pidbox worker on the broker (DB 0): it records each raw broadcast on
  '/0.celery.pidbox' and answers through kombu's own Node.handle_message /
  Mailbox._publish_reply (mode 'pong' with Celery's ping handler, 'pang', or
  'silent');
- the sender environment (WHITELABEL_NAME, EMAIL_HOST_USER, DEFAULT_FROM_EMAIL).
Before each request the case's 'setup' is applied (instance-outbound's
apply_setup) and the scheduler heartbeat is set in Django's cache ('fresh',
'stale', 'integer' or absent); a 'race' case bumps email_revision when the
renderer request arrives. Afterwards every stub's requests, the normalized
ping envelopes and the absence of pidbox reply queues are checked, and the
heartbeat is removed. State matchers: '$isonow' (a recent isoformat()) and
'$lease45' (a claimed lease, now + 45 s).
"""
from contextlib import contextmanager
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import socket
import threading
import types
import urllib.parse
import ipaddress
import time
from datetime import datetime

from TESTS.contracts import test_instance_admin_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instance_admin_http.json', 'TESTS/contracts/test_instance_admin_http.py',
           'TESTS/adapters/django/instance_admin_http.py', 'BE/instance/administration.py', 'BE/instance/operations.py',
           'BE/instance/integrations.py', 'BE/instance/diagnostics.py', 'BE/instance/email_configuration.py',
           'BE/instance/integration_configuration.py', 'BE/instance/operation_limits.py', 'BE/instance/permissions.py',
           'BE/instance/serializers.py', 'BE/instance/models.py', 'BE/instance/updater_client.py',
           'BE/instance/defaults.py', 'BE/application/impersonation.py', 'BE/core/settings.py',
           'BE/instance/probe_runner.py', 'BE/instance/diagnostic_probe.py', 'BE/instance/scheduler.py', 'BE/core/celery.py',
           'TESTS/adapters/django/instance_outbound_http.py', 'TESTS/adapters/go/payment_category_server.py') + harness.SOURCES
CONFIG = 'instance.InstanceConfiguration'
AUDIT, INDEX = harness.AUDIT, harness.INDEX
FIELDS = ('name', 'abbreviation', 'primary_color', 'support_email', 'ui_api_version', 'email_settings', 'email_revision',
          'diagnostic_results', 'integration_settings')
FERNET = '$fernet-integration:'


def seed_rows(source, created):
    from instance.integration_configuration import encrypt_secret
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    base_contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')

    def encrypted(value):
        if isinstance(value, dict):
            return {key: encrypted(item) for key, item in value.items()}
        if isinstance(value, str) and value.startswith(FERNET):
            return encrypt_secret(value[len(FERNET):])
        return value
    spec['integration_settings'] = encrypted(spec['integration_settings'])
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


ISO_NOW = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00\Z')
# Stored run results are compared again by later cases: recent means within the run.
RECENT = 1800


def same(expected, actual, decrypt):
    """Strict JSON equality; {'$secret': plain} / {'$literal': stored} match stored secrets;
    '$isonow' a recent isoformat(), '$lease45' a lease claimed for 45 s."""
    if expected == '$isonow':
        return isinstance(actual, str) and bool(ISO_NOW.match(actual)) and \
            -RECENT < time.time() - datetime.fromisoformat(actual).timestamp() < RECENT
    if expected == '$lease45':
        return type(actual) is float and time.time() + 45 - RECENT < actual <= time.time() + 46
    if isinstance(expected, dict) and set(expected) == {'$secret'}:
        if not isinstance(actual, str):
            return False
        if expected['$secret'] == '':
            return actual == ''
        try:
            return actual != '' and decrypt(actual) == expected['$secret']
        except Exception:
            return False
    if isinstance(expected, dict) and set(expected) == {'$literal'}:
        return actual == expected['$literal']
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(expected) == set(actual) and all(same(expected[k], actual[k], decrypt) for k in expected)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(same(e, a, decrypt) for e, a in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


def moment(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def assert_effect(case, before, after, source, context):
    from instance.email_configuration import decrypt_password
    from instance.integration_configuration import decrypt_secret
    harness.require_unmanaged_unchanged(base_contract, before, after, (CONFIG, AUDIT, INDEX))
    check_audit(case, before, after, source, context)
    key = str(source['instance']['id'])
    old, new = keyed(before[CONFIG], 'id'), keyed(after[CONFIG], 'id')
    base_contract.require(set(old) == set(new) == {key}, 'state.instance-row')
    old, new = old[key], new[key]
    effect = case['effect']
    if effect is None:
        base_contract.require(old == new, 'state.instance-unchanged')
        return
    expected = effect['fields']
    settled = set(FIELDS) | {'email_password_encrypted', 'updated_at'}
    base_contract.require({k: v for k, v in old.items() if k not in settled} == {k: v for k, v in new.items() if k not in settled},
                     'state.instance-other-fields')
    for field in FIELDS:
        base_contract.require(same(expected[field], new[field], decrypt_secret), 'state.' + field)
    base_contract.require(same(expected['email_password'], new['email_password_encrypted'], decrypt_password), 'state.email-password')
    if effect['touched']:
        base_contract.require(moment(new['updated_at']) > moment(old['updated_at']), 'state.updated-at-advanced')
    else:
        base_contract.require(new['updated_at'] == old['updated_at'], 'state.updated-at-kept')


# ---------------------------------------------------------------------------
# Diagnostics services
# ---------------------------------------------------------------------------

STUBS = {}
WHITELABEL = 'A.S.D. Polisportiva "Città" (Aurora)'
EMAIL_USER = 'segreteria@polisportiva.example'
API_PORT = 8000
TRICKLE_SECONDS = 20


class _ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    role = None

    def log_message(self, *_args):
        pass

    def do_GET(self):
        stub = STUBS[self.role]
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        address = f'127.0.0.1:{stub["port"]}'
        split = urllib.parse.urlsplit(self.path)
        record = {'method': self.command, 'path': split.path, 'query': dict(urllib.parse.parse_qsl(split.query, keep_blank_values=True)),
                  'headers': {k.lower(): ('$self' if k.lower() == 'host' and v == address else v) for k, v in self.headers.items()},
                  'duplicates': len(self.headers.keys()) != len({k.lower() for k in self.headers.keys()}), 'body': body}
        with STUBS['lock']:
            stub['log'].append(record)
            reply = stub['replies'].pop(0) if stub['replies'] else {'status': 599, 'headers': {}, 'raw_b64': ''}
            hook = STUBS.get('hook') if self.role == 'renderer' else None
        if hook:
            hook()
        mode = reply.get('mode')
        if mode == 'reset':
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b'\x01\x00\x00\x00\x00\x00\x00\x00')
            self.close_connection = True
            return
        if mode == 'trickle':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(TRICKLE_SECONDS + 2))
            self.end_headers()
            try:
                self.wfile.write(b'{')
                self.wfile.flush()
                for _ in range(TRICKLE_SECONDS):
                    time.sleep(1)
                    self.wfile.write(b' ')
                    self.wfile.flush()
                self.wfile.write(b'}')
            except OSError:
                pass
            self.close_connection = True
            return
        data = base64.b64decode(reply['raw_b64'])
        self.send_response(reply['status'])
        for key, value in reply['headers'].items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class PidboxWorker:
    """A worker's remote-control side on the broker, answering through kombu."""

    def __init__(self, url):
        import redis
        from celery.worker.control import Panel
        from kombu import Connection
        from kombu.pidbox import Mailbox
        self.mode, self.log, self.lock = 'silent', [], threading.Lock()
        self.raw = redis.Redis.from_url(url)
        self.connection = Connection(url)
        self.channel = self.connection.channel()
        mailbox = Mailbox('celery', type='fanout', accept=['json'], serializer='json')(self.connection)

        def handler(state, **kwargs):
            return Panel.data['ping'](state, **kwargs) if self.mode == 'pong' else {'ok': 'pang'}
        self.node = mailbox.Node('contract-worker@diagnostics', handlers={'ping': handler}, channel=self.channel)
        self.pubsub = self.raw.pubsub(ignore_subscribe_messages=True)
        self.pubsub.psubscribe('/0.celery.pidbox')
        self.stopping = False
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stopping:
            message = self.pubsub.get_message(timeout=0.2)
            if not message or message['type'] != 'pmessage':
                continue
            raw = message['data']
            with self.lock:
                self.log.append((message['channel'].decode(), raw))
                mode = self.mode
            if mode == 'silent':
                continue
            try:
                envelope = json.loads(raw)
                body = self.channel.Message(envelope, channel=self.channel).decode()
                self.node.handle_message(body, None)
            except Exception:  # recorded; the case's checks fail on the log
                with self.lock:
                    self.log.append(('error', raw))

    def take(self, mode):
        with self.lock:
            self.mode, log, self.log = mode, self.log, []
        return log

    def close(self):
        self.stopping = True
        self.thread.join(timeout=5)
        self.pubsub.close()
        self.channel.close()
        self.connection.release()
        self.raw.close()
        return not self.thread.is_alive()


UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')


def normalized_ping(channel, raw):
    """A broadcast as the fixture states it: generated ids '$uuid', expires '$expires'."""
    require = base_contract.require
    envelope = json.loads(raw, object_pairs_hook=lambda pairs: dict(pairs))
    require(raw.decode() == json.dumps(envelope), 'worker.envelope-format')
    body_bytes = base64.b64decode(envelope['body'])
    body = json.loads(body_bytes)
    require(body_bytes.decode() == json.dumps(body), 'worker.body-format')

    def ids(value):
        if isinstance(value, dict):
            return {k: ids(v) for k, v in value.items()}
        return '$uuid' if isinstance(value, str) and UUID.match(value) else value
    headers = dict(envelope['headers'])
    expires = headers.get('expires')
    if type(expires) is float and abs(expires - (time.time() + 2)) < 15:
        headers['expires'] = '$expires'
    return {'channel': channel, 'envelope_keys': list(envelope), 'content-encoding': envelope['content-encoding'],
            'content-type': envelope['content-type'], 'headers': headers, 'properties': ids(envelope['properties']),
            'body': ids(body), 'body_keys': list(body)}


@contextmanager
def service_context(report, log):
    from django.test.utils import override_settings
    from TESTS.adapters.django import instance_outbound_http as outbound
    servers, threads, worker = {}, [], None
    names = ('WHITELABEL_NAME', 'EMAIL_HOST_USER', 'PUPPETEER_HOST', 'PUPPETEER_PORT', 'AWS_S3_ENDPOINT_URL', 'GO_FIXTURE_ENVIRONMENT')
    saved = {name: os.environ.get(name) for name in names}
    hosts = outbound.HOSTS
    report['service_cleanup']['diagnostics_stubs'] = 'running'
    try:
        STUBS.update(lock=threading.Lock(), hook=None)
        for role, port in (('api', API_PORT), ('renderer', 0), ('s3', 0)):
            handler = type('_Stub', (_ScriptedHandler,), {'role': role})
            # The API stub must own 127.0.0.1:8000 (Django's fixed probe address): a busy port fails the run.
            servers[role] = ThreadingHTTPServer(('127.0.0.1', port), handler)
            STUBS[role] = {'port': servers[role].server_address[1], 'log': [], 'replies': []}
            thread = threading.Thread(target=servers[role].serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        redis_url = (f"redis://{os.environ['REDIS_USERNAME']}:{os.environ['REDIS_PASSWORD']}@"
                     f"{os.environ['REDIS_HOST']}:{os.environ['REDIS_PORT']}/0")
        worker = PidboxWorker(redis_url)
        STUBS['worker'] = worker
        s3 = f'http://127.0.0.1:{STUBS["s3"]["port"]}'
        os.environ.update(WHITELABEL_NAME=WHITELABEL, EMAIL_HOST_USER=EMAIL_USER, PUPPETEER_HOST='127.0.0.1',
                          PUPPETEER_PORT=str(STUBS['renderer']['port']), AWS_S3_ENDPOINT_URL=s3,
                          GO_FIXTURE_ENVIRONMENT=json.dumps({**json.loads(saved['GO_FIXTURE_ENVIRONMENT'] or '{}'),
                                                             'ASSOZETA_DIAGNOSTICS_API_URL': f'http://127.0.0.1:{API_PORT}',
                                                             'AWS_S3_ENDPOINT_URL': s3}))
        outbound.HOSTS = hosts + ('example.org',)
        with outbound.service_context(report, log), \
                override_settings(WHITELABEL_NAME=WHITELABEL, EMAIL_HOST_USER=EMAIL_USER,
                                  DEFAULT_FROM_EMAIL=f'{WHITELABEL} <{EMAIL_USER}>', AWS_S3_ENDPOINT_URL=s3):
            outbound.HOSTS = hosts
            yield
    finally:
        outbound.HOSTS = hosts
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        stopped = worker.close() if worker is not None else True
        for server in servers.values():
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=10)
            stopped = stopped and not thread.is_alive()
        report['service_cleanup']['diagnostics_stubs'] = 'stopped' if stopped else 'cleanup_failed'
        STUBS.clear()


def apply_heartbeat(case):
    from django.core.cache import cache
    spec = case.get('heartbeat')
    cache.delete('instance:scheduler:heartbeat')
    if spec == 'fresh':
        cache.set('instance:scheduler:heartbeat', time.time() - 5, timeout=90)
    elif spec == 'stale':
        cache.set('instance:scheduler:heartbeat', time.time() - 200, timeout=90)
    elif spec == 'integer':
        cache.set('instance:scheduler:heartbeat', int(time.time()) - 3, timeout=90)
    else:
        base_contract.require(spec is None, 'fixture.heartbeat')


def race_hook(source):
    """Another save of the configuration while the probes run: email_revision + 1."""
    def bump():
        from django.db import connection
        from django.db.models import F
        from instance.models import InstanceConfiguration
        try:
            InstanceConfiguration._base_manager.filter(pk=source['instance']['id']).update(email_revision=F('email_revision') + 1)
        finally:
            connection.close()
    return bump


def check_stubs(case):
    require = base_contract.require
    bucket = os.environ['AWS_STORAGE_BUCKET_NAME']
    scripts = case.get('stubs') or {}
    for role in ('api', 'renderer', 's3'):
        with STUBS['lock']:
            log, left = list(STUBS[role]['log']), STUBS[role]['replies']
        expected = (scripts.get(role) or {}).get('expect', [])
        require(not left, f'{role}.unused-replies')
        require(len(log) == len(expected), f'{role}.request-count')
        for sent, want in zip(log, expected):
            require(not sent['duplicates'] and sent['body'] == b'', f'{role}.request-shape')
            if role == 's3':
                path = sent['path'].replace('/' + bucket, '/$bucket', 1) if sent['path'] == '/' + bucket else sent['path']
                require({'method': sent['method'], 'path': path, 'query': sent['query'],
                         'signed': sent['headers'].get('authorization', '').startswith('AWS4-HMAC-SHA256 ')} == want, 's3.request')
            else:
                require({'method': sent['method'], 'path': sent['path'], 'headers': sent['headers']} == want, f'{role}.request')


def check_worker(case, log):
    require = base_contract.require
    expected = (case.get('worker') or {}).get('expect', [])
    require(all(channel != 'error' for channel, _ in log), 'worker.reply-failed')
    require(len(log) == len(expected), 'worker.ping-count')
    for (channel, raw), want in zip(log, expected):
        require(normalized_ping(channel, raw) == {**want, 'body_keys': list(want['body'])}, 'worker.ping')
    leftover = STUBS['worker'].raw.keys('*reply.celery.pidbox*')
    require(leftover == [], 'worker.reply-queue-removed')


def _assert_case(case, base_url, *, token, sessions):
    from TESTS.adapters.django import instance_outbound_http as outbound
    source = base_contract.load_source()
    outbound.apply_setup(case, source)
    apply_heartbeat(case)
    scripts = case.get('stubs') or {}
    with STUBS['lock']:
        for role in ('api', 'renderer', 's3'):
            STUBS[role]['log'], STUBS[role]['replies'] = [], list((scripts.get(role) or {}).get('replies', []))
        STUBS['hook'] = race_hook(source) if case.get('race') else None
    with outbound.STUB['lock']:
        outbound.STUB['log'] = []
        outbound.STUB['script'] = case.get('outbound') or []
    STUBS['worker'].take((case.get('worker') or {}).get('mode', 'silent'))
    try:
        status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    finally:
        from django.core.cache import cache
        cache.delete('instance:scheduler:heartbeat')
        STUBS['hook'] = None
    # A worker reply may still be in flight for a fast client; let the stub settle.
    time.sleep(0.05)
    log = STUBS['worker'].take('silent')
    outbound.check_outbound({'outbound': case.get('outbound') or []})
    check_stubs(case)
    check_worker(case, log)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)
fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)


def check_audit(case, before, after, source, context):
    """No entry, or the one UPDATE entry of a web UI API version change."""
    added, indexes = harness.audit_delta(base_contract, before, after)
    expected = case.get('audit')
    base_contract.require(len(added) == (1 if expected else 0), 'audit.count')
    if not expected:
        base_contract.require(not indexes, 'audit.index-count')
        return
    event = added[0]
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    instance = source['instance']
    after_row = keyed(after[CONFIG], 'id')[str(instance['id'])]
    actor = source['ids'][expected['actor']]
    base_contract.require(event['content_type_id'] == types[('instance', 'instanceconfiguration')] and event['action'] == 1
                     and event['object_pk'] == str(instance['id']) and event['object_id'] == instance['id']
                     and event['object_repr'] == f"Instance: {after_row['name']} ({instance['domain']})"
                     and event['serialized_data'] is None, 'audit.identity')
    base_contract.require(event['changes'] == expected['changes'] and event['changes_text'] == '', 'audit.changes')
    base_contract.require(str(event['actor_id']) == actor and event['actor_email'] is None, 'audit.actor')
    base_contract.require(event['cid'] == case['name'], 'audit.cid')
    base_contract.require(event['additional_data'] == {'actor_id': actor}, 'audit.additional-data')
    ipaddress.ip_address(event['remote_addr'])
    base_contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    stamp = event['timestamp']
    stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else stamp
    base_contract.require(stamp.tzinfo is not None and time.time() - 600 <= stamp.timestamp() <= time.time() + 1, 'audit.timestamp')
    if expected['association'] is None:
        base_contract.require(not indexes, 'index.none')
    else:
        index = indexes.get(str(event['id']))
        base_contract.require(index is not None and len(indexes) == 1
                         and str(index['sport_association_id']) == source['ids'][expected['association']]
                         and index['resolution_path'] == 'actor.sport_association', 'index.fields')
