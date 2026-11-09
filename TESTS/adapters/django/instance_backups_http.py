"""Parent-only instance backups HTTP runner; imports are inert.

service_context owns, for the whole run and before either server starts:
- a private MinIO bucket (the instance-logo image and lifetime) named by the
  AWS_* settings both servers read; object_bytes sums the whole bucket;
- the update runner stub: an HTTP/1.1 server on '<dir>/runner.sock' with
  the bearer token in '<dir>/token' (a short directory under /tmp, inside
  the Unix socket path limit). It answers each request with the case's
  scripted response and records every request (method, path, headers, body);
- production mode with ASSOZETA_UPDATER_DIRECTORY on that directory, for
  Django (override_settings) and Go (GO_FIXTURE_ENVIRONMENT), and Django's
  production password hashers (the test runtime prefers MD5).

seed_rows inserts the self-hosted InstanceConfiguration. Around each request:
- the owner's and the administrator's stored hashes are set (the authored
  PBKDF2 hashes, or the case's 'password' for the actor) and put back to the
  seeded value afterwards, keeping the value the request left for the check;
- 'runner' state: the socket is renamed away ('no-socket'), the token file
  removed ('no-token') or blank ('empty-token');
- 'objects' are stored, 'bucket_absent' removes the bucket; the bucket is
  emptied (and recreated) afterwards;
- 'cache' presets the three Django cache keys; afterwards the keys must be
  exactly 'cache_after' (pickled bytes as Django writes them, TTLs fresh or
  kept), and they are deleted;
- '$token' in the path is a token signed with the running SECRET_KEY.
The effect check: no model changes except the authored ACCESS entry and its
index row, and the expected stored hash (an upgraded one when 'rehash').
"""
from contextlib import contextmanager
import base64
from datetime import datetime
import http.server
import json
import os
import pickle
import re
import shutil
import socketserver
import tempfile
import threading
import time
import types
from urllib.parse import quote

from TESTS.contracts import test_instance_backups_http as base_contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/instance_backups_http.json', 'TESTS/contracts/test_instance_backups_http.py',
           'TESTS/adapters/django/instance_backups_http.py', 'TESTS/adapters/django/instance_logo.py',
           'BE/instance/backups.py', 'BE/instance/updater_client.py', 'BE/instance/permissions.py', 'BE/instance/models.py',
           'BE/instance/urls.py', 'BE/application/utils/printing.py', 'BE/application/signals.py',
           'BE/core/settings.py') + harness.SOURCES
BUCKET = 'instance-backups-fixture'
SIZES_KEY = 'instance:backup-data-sizes:v1'
TOKEN_SALT = 'instance.backups.download'
AUDIT, INDEX, USER = harness.AUDIT, harness.INDEX, 'application.User'
STATE = {}
require = base_contract.require


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
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                with outer.lock:
                    outer.requests.append({'method': self.command, 'path': self.path,
                                           'headers': {name.lower(): value for name, value in self.headers.items()},
                                           'duplicates': len(names) != len(set(names)), 'body': len(body)})
                    script = outer.script or {'status': 500, 'body': '{"error": "unscripted"}', 'chunked': False}
                content = base64.b64decode(script['body_b64']) if 'body_b64' in script else script['body'].encode()
                self.send_response(script['status'])
                self.send_header('Content-Type', 'application/json')
                if script['chunked']:
                    self.send_header('Transfer-Encoding', 'chunked')
                else:
                    self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                if script['chunked']:
                    self.wfile.write(f'{len(content):x}\r\n'.encode() + content + b'\r\n0\r\n\r\n')
                elif self.command != 'HEAD':
                    self.wfile.write(content)
                self.close_connection = True

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_one

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


@contextmanager
def service_context(report, log):
    import boto3
    from pathlib import Path
    from botocore.config import Config
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = base_contract.load_source()
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    directory = Path(tempfile.mkdtemp(prefix='azb-', dir='/tmp'))
    stub = RunnerStub(directory)
    try:
        (directory / 'token').write_text(source['runner_token'] + '\n')
        with minio(log, dict(os.environ), report) as (endpoint, access, secret):
            client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                                  region_name='us-east-1',
                                  config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
            client.create_bucket(Bucket=BUCKET)
            storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
            STATE.update(client=client, stub=stub, directory=directory)
            try:
                with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                       AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                       AWS_LOCATION='', AWS_S3_ADDRESSING_STYLE='path',
                                       ASSOZETA_DEPLOYMENT_MODE='production', ASSOZETA_UPDATER_DIRECTORY=str(directory),
                                       PASSWORD_HASHERS=PASSWORD_HASHERS):
                    os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({
                        **json.loads(saved or '{}'), 'ASSOZETA_DEPLOYMENT_MODE': 'production',
                        'ASSOZETA_UPDATER_DIRECTORY': str(directory)})
                    yield
                require(listing() == set(), 'storage.objects-restored')
            finally:
                if saved is None:
                    os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
                else:
                    os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
                STATE.clear()
    finally:
        stub.close()
        shutil.rmtree(directory, ignore_errors=True)


def listing():
    pages = STATE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def empty_bucket():
    for key in listing():
        STATE['client'].delete_object(Bucket=BUCKET, Key=key)


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

def resolve_ids(value, ids):
    if isinstance(value, str) and value.startswith('$id:'):
        return ids[value[4:]]
    if isinstance(value, dict):
        return {key: resolve_ids(item, ids) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_ids(item, ids) for item in value]
    return value


def signed_token(spec, ids):
    from django.core import signing
    payload = resolve_ids(spec['claims'], ids)
    segment = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).rstrip(b'=').decode()
    signer = signing.Signer(salt=spec.get('salt') or TOKEN_SALT)
    if spec.get('untimed'):
        value = signer.sign(segment)
    else:
        value = signer.sign(f'{segment}:{signing.b62_encode(int(time.time()) - spec.get("age", 0))}')
    if spec.get('tamper'):
        value = value[:-1] + ('A' if value[-1] != 'A' else 'B')
    return value


def verify_token(source):
    def verify(name, actor, token):
        from django.core import signing
        try:
            claims = signing.loads(token, salt=TOKEN_SALT, max_age=300)
        except signing.BadSignature:
            return False
        segment = token.split(':')[0]
        raw = base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))
        return (isinstance(claims, dict) and list(claims) == ['name', 'actor', 'id'] and claims['name'] == name
                and claims['actor'] == source['ids'][actor] and isinstance(claims['id'], str)
                and re.fullmatch(r'[0-9a-f]{32}', claims['id']) is not None
                and raw == json.dumps(claims, separators=(',', ':')).encode())
    return verify


def database_size():
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_database_size(current_database())')
        return int(cursor.fetchone()[0])


def our_keys(raw):
    return {key.decode() for key in raw.scan_iter(match=':1:instance:backup-*')}


def apply_cache(case, raw):
    from django.core.cache import cache
    require(our_keys(raw) == set(), 'cache.clean-before')
    preset = {}
    spec = case['cache']
    if 'sizes' in spec:
        if 'raw' in spec['sizes']:
            raw.set(cache.make_key(SIZES_KEY), spec['sizes']['raw'].encode(), ex=600)
        else:
            cache.set(SIZES_KEY, spec['sizes']['payload'], timeout=600)
    for actor, value in spec.get('attempts', {}).items():
        cache.set(f'instance:backup-download-attempts:{actor}', value, timeout=900)
    for identity in spec.get('used', []):
        cache.set(f'instance:backup-download-used:{identity}', True, timeout=360)
    for key in our_keys(raw):
        preset[key] = raw.get(key)
    return preset


def check_cache(case, raw, preset, window):
    from django.core.cache import cache
    expected = case['cache_after']
    keys = set()
    try:
        sizes = expected.get('sizes')
        if sizes is not None:
            key = cache.make_key(SIZES_KEY)
            keys.add(key)
            ttl = raw.pttl(key)
            if set(sizes) <= {'payload', 'raw'}:
                require(raw.get(key) == preset.get(key) and 0 < ttl <= 600_000, 'cache.sizes-kept')
            else:
                value = dict(sizes)
                if value['database'] == '$database':
                    stored = pickle.loads(raw.get(key))
                    database = stored.get('database') if isinstance(stored, dict) else None
                    require(type(database) is int and window[0] <= database <= window[1], 'cache.sizes-database')
                    if 'database' in base_contract.LAST:
                        require(database == base_contract.LAST['database'], 'cache.sizes-database-response')
                    value['database'] = database
                require(raw.get(key) == pickle.dumps(value, pickle.DEFAULT_PROTOCOL), 'cache.sizes-bytes')
                require(590_000 < ttl <= 600_000, 'cache.sizes-ttl')
        for actor, spec in expected.get('attempts', {}).items():
            spec = spec if isinstance(spec, dict) else {'value': spec, 'ttl': 'kept'}
            key = cache.make_key(f'instance:backup-download-attempts:{actor}')
            keys.add(key)
            ttl = raw.pttl(key)
            require(raw.get(key) == str(spec['value']).encode(), f'cache.attempts-value.{actor}')
            if spec['ttl'] == 'fresh':
                require(key not in preset and 890_000 < ttl <= 900_000, 'cache.attempts-fresh')
            else:
                require(key in preset and 0 < ttl <= 900_000, 'cache.attempts-kept')
        for identity in expected.get('used', []):
            key = cache.make_key(f'instance:backup-download-used:{identity}')
            keys.add(key)
            ttl = raw.pttl(key)
            require(raw.get(key) == pickle.dumps(True, pickle.DEFAULT_PROTOCOL), 'cache.used-value')
            require(0 < ttl <= 360_000 and (key in preset or ttl > 350_000), 'cache.used-ttl')
        require(our_keys(raw) == keys, 'cache.keys')
    finally:
        for key in our_keys(raw):
            raw.delete(key)


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
    elif state == 'empty-token':
        token_path.write_text(' \n')
    try:
        yield
    finally:
        if state == 'no-socket':
            (directory / 'runner.sock.off').rename(socket_path)
        token_path.write_text(source['runner_token'] + '\n')
    with stub.lock:
        recorded = [dict(request) for request in stub.requests]
        stub.script = None
    require(all(not request.pop('duplicates') and request.pop('body') == 0 for request in recorded), 'runner.request-shape')
    require(recorded == spec['expect'], 'runner.requests')


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
    for key, body in case['objects'].items():
        STATE['client'].put_object(Bucket=BUCKET, Key=key, Body=body.encode('latin-1'), ContentType='application/octet-stream')
    if case['bucket_absent']:
        require(listing() == set(), 'storage.empty-for-absent')
        STATE['client'].delete_bucket(Bucket=BUCKET)
    path = case['path']
    if '$token' in path:
        path = path.replace('$token', quote(signed_token(case['token'], source['ids']), safe=''))
    window = [database_size()]
    try:
        with passwords(case, source), runner_state(case, source):
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions, path=path,
                                               verify_token=verify_token(source), measure=database_size)
        window.append(database_size())
    finally:
        if case['bucket_absent']:
            STATE['client'].create_bucket(Bucket=BUCKET)
        empty_bucket()
        check_cache(case, raw, preset, window + [window[0]] * (2 - len(window)))
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
        require(event['content_type_id'] == types_[('instance', 'instanceconfiguration')] and event['action'] == 3
                and event['object_pk'] == expected['object_pk'] and event['object_id'] == int(expected['object_pk'])
                and event['object_repr'] == expected['object_repr'] and event['serialized_data'] is None, 'audit.identity')
        require(event['changes'] == expected['changes'] and event['changes_text'] == '', 'audit.changes')
        require(str(event['actor_id']) == actor and event['actor_email'] is None, 'audit.actor')
        require(event['cid'] == case['name'] and event['additional_data'] == expected['additional_data'], 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        started = context['prepared']['started']
        stamp = event['timestamp']
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else stamp
        require(stamp.tzinfo is not None and started - 1 <= stamp.timestamp() <= time.time() + 1, 'audit.timestamp')
        index = indexes[str(event['id'])]
        require(str(index['sport_association_id']) == source['ids'][expected['association']]
                and index['resolution_path'] == expected['resolution_path'], 'index.fields')
    stored, hashes = STATE.pop('stored'), STATE.pop('hashes')
    for identity, value in hashes.items():
        if case['rehash'] and identity == source['ids'][case['actor']]:
            require(stored[identity] != value and stored[identity].startswith('pbkdf2_sha256$1000000$')
                    and check_password(source['password'], stored[identity]), 'state.password-upgraded')
        else:
            require(stored[identity] == value, 'state.password-unchanged')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})
