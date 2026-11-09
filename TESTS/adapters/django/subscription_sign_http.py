"""Parent-only subscription/sign HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime) behind
a recording storage proxy for the whole run: both servers use the proxy as
AWS_S3_ENDPOINT_URL, with AWS_LOCATION, AWS_S3_USE_OBJECT_ACL=True and
AWS_S3_PUBLIC_BASE_URL set the same way (Django through override_settings, Go
through GO_FIXTURE_ENVIRONMENT). The proxy forwards every request unchanged to
MinIO except the object ACL calls, which it answers itself (MinIO only supports
the private canned ACL): 200 for public-read, AccessDenied for the fixture's
acl_denied row. Every request is recorded.

seed_rows inserts (audit disabled) the subscriptions; each workload starts with
the seeded signature objects in the bucket and ends with only the sentinel.
Every case is checked against the complete model state, the recorded storage
requests and the bucket:
- the storage requests are exactly the authored ones, in order (HeadObject,
  PutObject, PutObjectAcl public-read, DeleteObject), with the new key bound to
  '<AWS_LOCATION>/subscriptions/<id>/signature_<repr(float)>.png' whose
  timestamp lies in the request window;
- a stored object has the decoded bytes, ContentType image/png, no
  ContentEncoding and CacheControl max-age=86400; an old object is deleted or
  kept as authored; nothing else in the bucket changes;
- the subscription columns change exactly as authored (the key relative to
  AWS_LOCATION, the public URL of the object key), a new Signature row (uuid4,
  request.user, the trimmed data) when one is saved;
- the auditlog entries (Signature create, Subscription update with the changed
  fields) with their index rows, and the on_commit Celery messages
  (check_workflows_trigger subscription_signed, print_document_subscription).
Failures leave all of it unchanged.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
import re
import threading
import time
import urllib.parse
import uuid

from TESTS.contracts import test_subscription_sign_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_sign_http.json', 'TESTS/contracts/test_subscription_sign_http.py',
           'TESTS/adapters/django/subscription_sign_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/utils/subscriptions_utils.py', 'BE/application/utils/api_utils.py', 'BE/application/exceptions.py',
           'BE/application/signals.py', 'BE/application/tasks.py', 'BE/core/celery.py', 'BE/core/settings.py',
           'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py') + harness.SOURCES
BUCKET = 'subscription-sign-fixture'
SENTINEL = ('subscription-sign/sentinel/keep.txt', b'sentinel')
SUB, SIG = 'application.Subscription', 'application.Signature'
MANAGED = (SUB, SIG, harness.AUDIT, harness.INDEX)
STORAGE = {}


class RecordingProxy:
    """Forwards S3 requests to MinIO byte for byte (signed Host kept) and records them;
    answers PutObjectAcl itself."""

    def __init__(self, endpoint, denied):
        target = endpoint.removeprefix('http://')
        self.host, self.port = target.rsplit(':', 1)[0], int(target.rsplit(':', 1)[1])
        self.denied, self.lock, self.requests = denied, threading.Lock(), []
        outer = self

        class Forward(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.forward()

            do_GET = do_PUT = do_POST = do_DELETE = do_HEAD

            def body(self):
                if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                    raw = b''
                    while True:
                        line = self.rfile.readline()
                        raw += line
                        size = int(line.split(b';', 1)[0].strip(), 16)
                        if size == 0:
                            while True:
                                trailer = self.rfile.readline()
                                raw += trailer
                                if trailer in (b'\r\n', b'\n', b''):
                                    return raw
                        raw += self.rfile.read(size + 2)
                length = int(self.headers.get('Content-Length') or 0)
                return self.rfile.read(length) if length else b''

            def respond(self, status, payload, content_type=None):
                self.send_response_only(status)
                if content_type:
                    self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(payload)
                self.close_connection = True

            def forward(self):
                payload = self.body()
                parsed = urllib.parse.urlsplit(self.path)
                query = sorted(set(urllib.parse.parse_qs(parsed.query, keep_blank_values=True)) - {'x-id'})
                prefix = f'/{BUCKET}/'
                key = urllib.parse.unquote(parsed.path[len(prefix):]) if parsed.path.startswith(prefix) else None
                if self.command == 'PUT' and query == ['acl'] and key is not None:
                    acl = self.headers.get('x-amz-acl')
                    refused = outer.denied in key
                    with outer.lock:
                        outer.requests.append(('ACL', key, acl, 403 if refused else 200))
                    if refused:
                        return self.respond(403, b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
                                                 b'<Message>Access Denied.</Message></Error>', 'application/xml')
                    return self.respond(200, b'')
                connection = http.client.HTTPConnection(outer.host, outer.port, timeout=30)
                try:
                    connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                    for name, value in self.headers.items():
                        if name.lower() not in ('expect', 'connection'):
                            connection.putheader(name, value)
                    connection.endheaders(payload if payload else None)
                    response = connection.getresponse()
                    data = response.read()
                    with outer.lock:
                        outer.requests.append((self.command, key, ','.join(query), response.status))
                    self.send_response_only(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                            self.send_header(name, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    if self.command != 'HEAD':
                        self.wfile.write(data)
                    self.close_connection = True
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Forward)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'

    def take(self):
        with self.lock:
            items, self.requests = self.requests, []
        return items

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = contract.load_source()
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        proxy = RecordingProxy(endpoint, source['acl_denied'])
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, proxy=proxy, location=source['location'])
        saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
        environment = {**json.loads(saved or '{}'), 'AWS_S3_USE_OBJECT_ACL': 'True', 'AWS_S3_PUBLIC_BASE_URL': source['public_base']}
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps(environment)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=proxy.endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path', AWS_S3_USE_OBJECT_ACL=True,
                                   AWS_S3_PUBLIC_BASE_URL=source['public_base']):
                yield
            contract.require(listing() == {SENTINEL[0]}, 'storage.objects-restored')
            contract.require(client.get_object(Bucket=BUCKET, Key=SENTINEL[0])['Body'].read() == SENTINEL[1], 'storage.sentinel')
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            proxy.close()
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def object_key(name):
    return STORAGE['location'] + '/' + name


moment = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None
day = lambda text: date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Subscription
    ids = source['ids']
    for row in source['subscriptions'].values():
        item = Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']],
                            status_flag=row['status'], start_date=day(row['start']), end_date=day(row['end']),
                            creation_date=datetime(2026, 1, 10, 9, tzinfo=timezone.utc), group_id=ids[row['group']] if row['group'] else None,
                            signature_storage_key=row['signature_key'], signature_url=row['signature_url'])
        Subscription._base_manager.bulk_create([item])
        created.append((Subscription, item.pk))


def signature_model():
    from application.models.subscriptions_models import Signature
    return Signature


_base_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=(signature_model,)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    """The seeded signature objects for the workload; afterwards only the sentinel stays."""
    source = contract.load_source()
    client = STORAGE['client']
    with _base_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        try:
            for name, body in source['objects'].items():
                client.put_object(Bucket=BUCKET, Key=object_key(name), Body=base64.b64decode(body), ContentType='image/png')
            STORAGE['proxy'].take()
            yield fixture
        finally:
            for key in listing() - {SENTINEL[0]}:
                client.delete_object(Bucket=BUCKET, Key=key)


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def prepare(source):
    STORAGE['proxy'].take()
    return {'started': time.time(), 'objects': listing()}


def resolve(value, binding):
    if isinstance(value, str) and value in binding:
        return binding[value]
    if isinstance(value, dict):
        return {k: resolve(v, binding) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, binding) for v in value]
    return value


def new_key(requests, row_id, since, until):
    """The key of the case's upload: <location>/subscriptions/<id>/signature_<repr(timestamp)>.png."""
    keys = [item[1] for item in requests if item[0] == 'PUT']
    contract.require(len(keys) == 1, 'storage.new-key-count')
    match = re.fullmatch(re.escape(f'{STORAGE["location"]}/subscriptions/{row_id}/signature_') + r'(\d+\.\d+)\.png', keys[0])
    contract.require(match is not None, 'storage.new-key-shape')
    stamp = match.group(1)
    contract.require(repr(float(stamp)) == stamp and since - 1 <= float(stamp) <= until + 1, 'storage.new-key-timestamp')
    return keys[0]


def check_requests(requests, effect, binding):
    expected = []
    for item in effect['storage']:
        operation, name, *rest = item.split(' ')
        key = binding[name]
        if operation == 'ACL':
            expected.append(('ACL', key, 'public-read', 403 if rest == ['denied'] else 200))
        elif operation == 'HEAD':
            expected.append(('HEAD', key, '', 404))
        elif operation == 'PUT':
            expected.append(('PUT', key, '', 200))
        else:
            expected.append(('DELETE', key, '', 204))
    contract.require(requests == expected, 'storage.requests:' + json.dumps([requests, expected]))


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    prepared = context['prepared']
    since, until = prepared['started'], time.time()
    requests = STORAGE['proxy'].take()
    objects = listing()
    effect = case['effect']
    token = fixture['tokens'].get(case['actor'])
    binding = {'$authorization': f'Bearer {token}' if token else None}
    harness.celery_published(contract, fixture, resolve(effect['celery'], binding) if effect else [], since=since, until=until)
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(not requests, 'storage.no-requests:' + json.dumps(requests))
        contract.require(objects == prepared['objects'], 'storage.no-effect')
        return
    row = source['subscriptions'][effect['subscription']]
    old_rows, new_rows = keyed(before[SUB], 'subscription_id'), keyed(after[SUB], 'subscription_id')
    previous = flat(old_rows[row['id']])
    binding.update({'$previous': previous['signature_storage_key'], '$previous_url': previous['signature_url']})
    if previous['signature_storage_key']:
        binding['$old'] = object_key(previous['signature_storage_key'])
    if any('$new' in item for item in effect['storage']):
        key = new_key(requests, row['id'], since, until)
        binding.update({'$new': key, '$key': key[len(STORAGE['location']) + 1:], '$public': source['public_base'] + '/' + key})
    check_requests(requests, effect, binding)

    # Bucket: the stored object, the old object deleted or kept, nothing else.
    expected_objects = set(prepared['objects'])
    client = STORAGE['client']
    if effect['stored'] is not None:
        expected_objects.add(binding['$new'])
        stored = client.get_object(Bucket=BUCKET, Key=binding['$new'])
        contract.require(stored['Body'].read() == base64.b64decode(effect['stored']), 'storage.bytes')
        contract.require(stored['ContentType'] == 'image/png' and stored.get('ContentEncoding') is None
                         and stored.get('CacheControl') == 'max-age=86400' and not stored.get('Metadata'), 'storage.metadata')
    if effect['old'] == 'deleted':
        contract.require(binding['$old'] in prepared['objects'], 'storage.old-present-before')
        expected_objects.discard(binding['$old'])
    elif effect['old'] == 'kept':
        contract.require(binding['$old'] in objects, 'storage.old-kept')
    contract.require(objects == expected_objects, 'storage.objects')

    # Subscriptions: only the authored columns of the row.
    columns = resolve(effect['columns'], binding)
    expected_rows = []
    for stored in before[SUB]:
        stored = flat(stored)
        if str(stored['subscription_id']) == row['id']:
            stored = {**stored, **columns}
        expected_rows.append(stored)
    contract.require([flat(r) for r in after[SUB]] == expected_rows, 'state.subscriptions')

    # Signature rows.
    old_sig, new_sig = keyed(before[SIG], 'signature_id'), keyed(after[SIG], 'signature_id')
    contract.require(all(flat(new_sig.get(k, {})) == flat(v) for k, v in old_sig.items()), 'state.signature-changed')
    added_sig = [flat(v) for k, v in new_sig.items() if k not in old_sig]
    spec = effect['signature']
    contract.require(len(added_sig) == (1 if spec else 0), 'state.signature-count')
    if spec:
        signature = added_sig[0]
        contract.require(uuid.UUID(signature['signature_id']).version == 4 and str(uuid.UUID(signature['signature_id'])) == signature['signature_id']
                         and signature == {'signature_id': signature['signature_id'], 'user_id': ids[spec['user']], 'signature': spec['data']},
                         'state.signature-row')
        binding['$signature'] = signature['signature_id']

    check_audit(case, effect, before, after, fixture, context, binding, ids)


def check_audit(case, effect, before, after, fixture, context, binding, ids):
    from auditlog.diff import model_instance_diff
    added, indexes = harness.audit_delta(contract, before, after)
    added = sorted(added, key=lambda event: int(event['id']))
    authored = effect['audit']
    contract.require(len(added) == len(authored) and len(indexes) == len([a for a in authored if index_of(a, effect) is not None]),
                     f'audit.count:{len(added)}:{len(indexes)}')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, spec) in enumerate(zip(added, authored)):
        where = f':{position}:{spec["model"]}'
        if spec['model'] == 'signature':
            pk = binding['$signature']
            instance = signature_model()._base_manager.get(pk=pk)
            expected = {k: list(v) for k, v in model_instance_diff(None, instance).items()}
            representation = f'Signature object ({pk})'
        else:
            pk = context['source']['subscriptions'][effect['subscription']]['id']
            expected = resolve(spec['changes'], binding)
            representation = f'Subscription object ({pk})'
        contract.require(event['content_type_id'] == types[('application', spec['model'])] and event['action'] == (0 if spec['model'] == 'signature' else 1)
                         and event['object_pk'] == pk and event['object_repr'] == representation and event['object_id'] is None, 'audit.identity' + where)
        contract.require(event['changes'] == expected, 'audit.changes' + where + json.dumps([expected, event['changes']]))
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote' + where)
        index = indexes.get(str(event['id']))
        wanted = index_of(spec, effect)
        if wanted is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == wanted[0] and index['resolution_path'] == wanted[1],
                             'index.fields' + where)


def index_of(spec, effect):
    return effect['signature']['index'] if spec['model'] == 'signature' else spec['index']


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    source = contract.load_source()

    def effect_source(case, before, after, source_, context):
        context['source'] = source
        return assert_effect(case, before, after, source_, context)
    return harness.run_workload_for(contract, effect_source, prepare=prepare)(label, backend, base_url, fixture, apps, raw, observer)
