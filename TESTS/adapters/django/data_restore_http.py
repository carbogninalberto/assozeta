"""Parent-only data-restore HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run: an empty bucket, Django's default storage switched to
S3Boto3Storage on it (AWS_LOCATION set) and DATA_RESTORE_MAX_UPLOAD_BYTES
lowered, before either server starts; the Go server receives the same AWS_*
settings, the upload limit and the running version (GO_FIXTURE_ENVIRONMENT).

seed_rows inserts the self-hosted InstanceConfiguration and the DataRestore
receipts (created_at/updated_at set after the insert: auto_now_add would
replace them). Around each request:
- 'setup' overwrites receipt fields (queryset.update, no save hooks);
- 'cache' stores progress snapshots through Django's cache (or raw bytes);
- 'objects' are stored under AWS_LOCATION; afterwards exactly
  'objects_after' must remain, then the bucket is emptied;
- '$token:<receipt>:<variant>' in the path is signed with the running
  SECRET_KEY (fresh, expired, tampered, other salt, other checksum, extra
  key, compressed, without timestamp);
- 'lock' holds DATA_RESTORE_LOCK_PATH with flock (shared or exclusive);
- 'single' moves every other SportAssociation row aside for the request
  (session_replication_role=replica on this connection, so referencing rows
  are not touched) and puts them back unchanged afterwards.
The effect check compares every receipt field with the authored state
('$now': updated_at advanced within the request window and equal to the
response), the rest of the database, and the Celery messages
(restore_instance_data with task_id equal to the receipt id).
"""
from contextlib import contextmanager
import base64
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import time
import types
import zlib

from TESTS.contracts import test_data_restore_http as base_contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/data_restore_http.json', 'TESTS/contracts/test_data_restore_http.py',
           'TESTS/adapters/django/data_restore_http.py', 'TESTS/adapters/django/instance_logo.py',
           'BE/instance/restore/views.py', 'BE/instance/restore/locking.py', 'BE/instance/restore/progress.py',
           'BE/instance/restore/downloads.py', 'BE/instance/restore/execution.py', 'BE/instance/restore/replacement.py',
           'BE/instance/restore/archive.py', 'BE/instance/tasks.py', 'BE/instance/models.py', 'BE/instance/permissions.py',
           'BE/instance/urls.py', 'BE/application/utils/printing.py', 'BE/core/settings.py') + harness.SOURCES
BUCKET = 'data-restore-fixture'
LOCATION = 'tenant-restore'
RECEIPT = 'instance.DataRestore'
FIELDS = ('owner_id', 'association_id', 'state', 'stage', 'completed_at', 'created_at', 'upload_path', 'sha256', 'version',
          'preview', 'backup_path', 'backup_sha256', 'media', 'error', 'allow_missing_media', 'attempts')
STORAGE = {}
require = base_contract.require


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.conf import settings
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = base_contract.load_source()
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path',
                                   DATA_RESTORE_MAX_UPLOAD_BYTES=source['max_upload_bytes']):
                require(bool(settings.DATA_RESTORE_LOCK_PATH), 'fixture.lock-path')
                os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({
                    **json.loads(saved or '{}'), 'DATA_RESTORE_MAX_UPLOAD_BYTES': str(source['max_upload_bytes']),
                    'RUNNING_VERSION': settings.RUNNING_VERSION})
                yield
            require(listing() == set(), 'storage.objects-restored')
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def empty_bucket():
    for key in listing():
        STORAGE['client'].delete_object(Bucket=BUCKET, Key=key)


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def resolve(value):
    from django.conf import settings
    return settings.RUNNING_VERSION if value == '$version' else value


def columns(fields, ids):
    """Authored receipt fields -> model column values."""
    values = {}
    for key, value in fields.items():
        if key in ('created_at', 'updated_at', 'completed_at'):
            values[key] = moment(value)
        elif key == 'owner':
            values['owner_id'] = ids[value]
        elif key != 'id':
            values[key] = resolve(value)
    return values


def seed_rows(source, created):
    from instance.models import DataRestore, InstanceConfiguration
    spec = dict(source['instance'])
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))
    require(not DataRestore._base_manager.exists(), 'fixture.receipts-exist')
    for row in source['receipts'].values():
        values = columns(row, source['ids'])
        stamps = {key: values.pop(key) for key in ('created_at', 'updated_at')}
        DataRestore._base_manager.bulk_create([DataRestore(id=row['id'], **values)])
        created.append((DataRestore, row['id']))
        DataRestore._base_manager.filter(pk=row['id']).update(**stamps)


# ---------------------------------------------------------------------------
# Per-request stimuli
# ---------------------------------------------------------------------------

def progress_key(identity):
    return f'data-restore-progress:{identity}'


def receipt_payload(identity, *, sha=None):
    from instance.models import DataRestore
    op = DataRestore._base_manager.get(pk=identity)
    return {'operation': str(op.id), 'owner': str(op.owner_id), 'sha256': op.backup_sha256 if sha is None else sha}


def signed_token(identity, variant):
    from django.core import signing
    from instance.restore.downloads import SALT
    payload = receipt_payload(identity)
    signer = signing.Signer(salt=SALT)

    def segment(value, compress=False):
        raw = json.dumps(value, separators=(',', ':')).encode()
        if compress:
            return '.' + base64.urlsafe_b64encode(zlib.compress(raw)).rstrip(b'=').decode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    now = int(time.time())
    if variant == 'fresh':
        return signing.dumps(payload, salt=SALT)
    if variant == 'expired':
        return signer.sign(f'{segment(payload)}:{signing.b62_encode(now - 3700)}')
    if variant == 'tampered':
        value = signing.dumps(payload, salt=SALT)
        return value[:-1] + ('A' if value[-1] != 'A' else 'B')
    if variant == 'other-salt':
        return signing.dumps(payload, salt='instance.data-restore.other')
    if variant == 'other-sha':
        return signing.dumps({**payload, 'sha256': 'c' * 64}, salt=SALT)
    if variant == 'extra-key':
        return signing.dumps({**payload, 'extra': 1}, salt=SALT)
    if variant == 'compressed':
        return signer.sign(f'{segment(payload, compress=True)}:{signing.b62_encode(now)}')
    if variant == 'untimed':
        return signer.sign(segment(payload))
    raise AssertionError('fixture.token-variant')


def resolve_path(path, receipts):
    from urllib.parse import quote
    marker = '$token:'
    if marker not in path:
        return path
    head, tail = path.split(marker, 1)
    key, variant = tail.split(':', 1)
    return head + quote(signed_token(receipts[key]['id'], variant), safe='')


def apply_setup(case, source):
    from instance.models import DataRestore
    for key, fields in (case['setup'] or {}).items():
        count = DataRestore._base_manager.filter(pk=source['receipts'][key]['id']).update(**columns(fields, source['ids']))
        require(count == 1, 'setup.receipt-row')


def apply_cache(case, source, raw):
    from django.core.cache import cache
    stored = {}
    for key, spec in (case['cache'] or {}).items():
        name = progress_key(source['receipts'][key]['id'])
        if 'raw' in spec:
            raw.set(cache.make_key(name), spec['raw'].encode())
        else:
            cache.set(name, spec['payload'], timeout=86400)
        stored[cache.make_key(name)] = raw.get(cache.make_key(name))
    return stored


def check_cache(source, raw, stored):
    from django.core.cache import cache
    try:
        for key, value in stored.items():
            require(raw.get(key) == value, 'cache.progress-unchanged')
        for row in source['receipts'].values():
            name = cache.make_key(progress_key(row['id']))
            require(name in stored or raw.get(name) is None, 'cache.no-progress-written')
    finally:
        for key in stored:
            raw.delete(key)


@contextmanager
def held_lock(mode):
    from django.conf import settings
    if mode is None:
        yield
        return
    with open(settings.DATA_RESTORE_LOCK_PATH, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if mode == 'exclusive' else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def single_association(enabled, primary):
    """Other SportAssociation rows are moved to a session temporary table for the request."""
    from django.db import connection
    from application.models import SportAssociation
    if not enabled:
        yield
        return
    table, column = SportAssociation._meta.db_table, SportAssociation._meta.pk.column
    with connection.cursor() as cursor:
        cursor.execute('SET session_replication_role = replica')
        try:
            cursor.execute(f'CREATE TEMP TABLE IF NOT EXISTS data_restore_hidden AS SELECT * FROM {table} WITH NO DATA')
            cursor.execute('DELETE FROM data_restore_hidden')
            cursor.execute(f'INSERT INTO data_restore_hidden SELECT * FROM {table} WHERE {column} <> %s', [primary])
            cursor.execute(f'DELETE FROM {table} WHERE {column} <> %s', [primary])
        finally:
            cursor.execute('SET session_replication_role = DEFAULT')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SET session_replication_role = replica')
            try:
                cursor.execute(f'INSERT INTO {table} SELECT * FROM data_restore_hidden')
                cursor.execute('DELETE FROM data_restore_hidden')
            finally:
                cursor.execute('SET session_replication_role = DEFAULT')


def verify_token(source):
    def verify(key, token):
        from django.core import signing
        from instance.restore.downloads import SALT
        try:
            return signing.loads(token, salt=SALT, max_age=3600) == receipt_payload(source['receipts'][key]['id'])
        except signing.BadSignature:
            return False
    return verify


def _assert_case(case, base_url, *, token, sessions):
    from django_redis import get_redis_connection
    source = base_contract.load_source()
    raw = get_redis_connection('default')
    apply_setup(case, source)
    stored = apply_cache(case, source, raw)
    for key, body in case['objects'].items():
        STORAGE['client'].put_object(Bucket=BUCKET, Key=f'{LOCATION}/{key}', Body=body.encode('latin-1'),
                                     ContentType='application/octet-stream')
    path = resolve_path(case['path'], source['receipts'])
    try:
        with held_lock(case['lock']), single_association(case['single'], source['ids'][source['instance']['primary_association']]):
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions, path=path,
                                               verify_token=verify_token(source))
        require(listing() == {f'{LOCATION}/{key}' for key in case['objects_after']}, 'storage.objects-after')
    finally:
        empty_bucket()
        check_cache(source, raw, stored)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def drf_datetime(value):
    if value is None:
        return None
    text = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    return text + 'Z'


def assert_effect(case, before, after, source, context):
    from instance.models import DataRestore
    harness.require_unmanaged_unchanged(base_contract, before, after, (RECEIPT,))
    started, finished = context['prepared']['started'], time.time()
    expected = case['effect']['receipts']
    rows = {str(row['id']): row for row in DataRestore._base_manager.values()}
    require(set(rows) == {row['id'] for row in expected.values()}, 'state.receipt-rows')
    for key, spec in expected.items():
        row = rows[spec['id']]
        values = columns({name: spec[name] for name in spec if name not in ('id', 'updated_at')}, source['ids'])
        for name in FIELDS:
            actual = row[name]
            if name in ('association_id', 'owner_id'):
                actual = str(actual)
            require(actual == values[name], f'state.{key}.{name}')
        if spec['updated_at'] == '$now':
            require(started - 1 <= row['updated_at'].timestamp() <= finished + 1, f'state.{key}.updated-at-window')
            body = base_contract.LAST.get('body')
            if isinstance(body, dict) and body.get('id') == spec['id']:
                require(body['updated_at'] == drf_datetime(row['updated_at']), f'state.{key}.updated-at-response')
        else:
            require(row['updated_at'] == moment(spec['updated_at']), f'state.{key}.updated-at')
    harness.celery_published(base_contract, context['fixture'], case['effect']['celery'], since=started, until=finished)


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})
