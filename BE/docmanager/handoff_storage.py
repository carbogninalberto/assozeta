"""Checkpoint S3 multipart uploads made by the coordinated storage task."""

import json

from botocore.exceptions import ClientError
from django.core.files.utils import validate_file_name
from django.db import transaction
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from core.handoff import _fetch, _running, barrier


TASK_METADATA = 'assozeta-task-id'


def _abort(client, bucket, key, upload):
    try:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'NoSuchUpload':
            raise


def _recover(client, bucket, key, upload, task_id):
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response['ResponseMetadata']['HTTPStatusCode'] != 404:
            raise
    else:
        if head.get('Metadata', {}).get(TASK_METADATA) == task_id:
            return True
    _abort(client, bucket, key, upload)
    return False


def save_task_file(storage, name, content):
    active = _running.get()
    if active is None or not isinstance(storage, S3Storage) or storage.gzip:
        return storage.save(name, content)
    config = storage.transfer_config
    if content.size < config.multipart_threshold:
        return storage.save(name, content)

    task_id, attempt, _ = active
    effect_key = f'{task_id}:storage:multipart'
    client, bucket = storage.bucket.meta.client, storage.bucket_name
    previous = _fetch('SELECT provider_ref FROM public.assozeta_handoff_effects WHERE effect_key=%s', (effect_key,))
    if previous and _recover(client, bucket, *json.loads(previous[0]), task_id):
        return

    validate_file_name(name, allow_relative_path=True)
    name = storage.get_available_name(name)
    key = storage._normalize_name(clean_name(name))
    options = storage._get_write_parameters(key, content)
    options['Metadata'] = {**options.get('Metadata', {}), TASK_METADATA: task_id}
    upload = client.create_multipart_upload(Bucket=bucket, Key=key, **options)['UploadId']
    try:
        with transaction.atomic():
            _fetch('SELECT public.assozeta_handoff_fence(%s)', (attempt,))
            _fetch("""INSERT INTO public.assozeta_handoff_effects
                (effect_key, task_id, attempt_id, kind, state, provider_ref)
                VALUES (%s, %s, %s, 'storage_multipart', 'confirmed', %s)
                ON CONFLICT (effect_key) DO UPDATE SET attempt_id=EXCLUDED.attempt_id, provider_ref=EXCLUDED.provider_ref
                RETURNING 1""", (effect_key, task_id, attempt, json.dumps([key, upload])))
        parts = []
        chunk_size = max(config.multipart_chunksize, 5 << 20)
        while chunk := content.read(chunk_size):
            number = len(parts) + 1
            part = client.upload_part(Bucket=bucket, Key=key, UploadId=upload, PartNumber=number, Body=chunk)
            parts.append({'PartNumber': number, 'ETag': part['ETag']})
            if number == 1:
                barrier('storage:part_uploaded', task_id)
        client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload, MultipartUpload={'Parts': parts})
    except Exception:
        _abort(client, bucket, key, upload)
        raise
