"""S3 operations for the self-host CLI, run in the shared updater image."""
from contextlib import closing
from datetime import datetime
import errno
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys

HEADERS = ('ContentType', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage',
           'CacheControl', 'Expires', 'Metadata')


def client(backend=None):
    import boto3
    from botocore.config import Config

    env = os.environ
    endpoint, key, secret, region = (env[name] for name in (
        'AWS_S3_ENDPOINT_URL', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_S3_REGION_NAME'))
    if backend == 'garage':
        endpoint, region = 'http://garage:3900', 'garage'
        key, secret = env['GARAGE_ACCESS_KEY'], env['GARAGE_SECRET_KEY']
    elif backend == 'minio':
        endpoint = 'http://minio:9000'
        key, secret = env['MINIO_ROOT_USER'], env['MINIO_ROOT_PASSWORD']
        if env['AWS_S3_ENDPOINT_URL'] != endpoint:
            region = env.get('MINIO_REGION', 'us-east-1')
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=key,
                        aws_secret_access_key=secret, region_name=region,
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))


def objects(store, bucket):
    for page in store.get_paginator('list_objects_v2').paginate(Bucket=bucket):
        yield from page.get('Contents', [])


def metadata(response):
    return {k: response[k] for k in HEADERS if k in response}


def upload(store, bucket, key, body, headers):
    from boto3.s3.transfer import TransferConfig

    store.upload_fileobj(body, bucket, key, ExtraArgs=headers, Config=TransferConfig(use_threads=False))


def stream_digest(stream):
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(chunk)
    return checksum.hexdigest()


def fingerprint(store, bucket, key):
    obj = store.get_object(Bucket=bucket, Key=key)
    with closing(obj['Body']) as body:
        return metadata(obj), stream_digest(body)


def mirror(source, src, target, dst, final):
    remaining = {entry['Key']: (entry['Size'], entry['ETag']) for entry in objects(target, dst)}
    for entry in objects(source, src):
        key = entry['Key']
        existing = remaining.pop(key, None)
        if existing is not None:
            if not final and fingerprint(source, src, key) == fingerprint(target, dst, key):
                continue
            # ETags are only a copy hint. A metadata-only edit keeps the ETag,
            # and verify() still hashes every object before accepting the mirror.
            if final and (entry['Size'], entry['ETag']) == existing:
                headers = metadata(source.head_object(Bucket=src, Key=key))
                if headers == metadata(target.head_object(Bucket=dst, Key=key)):
                    continue
        obj = source.get_object(Bucket=src, Key=key)
        with closing(obj['Body']) as body:
            upload(target, dst, key, body, metadata(obj))
    if final:
        for key in remaining:
            target.delete_object(Bucket=dst, Key=key)
        verify(source, src, target, dst)


def verify(source, src, target, dst):
    keys = {entry['Key'] for entry in objects(source, src)}
    if keys != {entry['Key'] for entry in objects(target, dst)}:
        raise ValueError('object key sets differ')
    for key in keys:
        if fingerprint(source, src, key) != fingerprint(target, dst, key):
            raise ValueError('object content or HTTP/user metadata differs')


def export_archive(store, bucket, directory):
    # Keep ordinary object paths readable by older restore tools. S3 also
    # permits names that cannot coexist as files (e.g. "a" and "a/b").
    owner = directory.stat()
    os.setgid(owner.st_gid)
    os.setuid(owner.st_uid)
    root = directory / 'objects'
    with (directory / 'object-index.jsonl').open('x') as index:
        for number, entry in enumerate(objects(store, bucket)):
            key = entry['Key']
            path = root / key
            try:
                if any(part in ('', '.', '..') for part in key.split('/')):
                    raise ValueError('non-filesystem key')
                path.parent.mkdir(parents=True, exist_ok=True)
                output = path.open('xb')
            except (ValueError, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno not in (
                        errno.EEXIST, errno.EISDIR, errno.ENOTDIR, errno.ENAMETOOLONG):
                    raise
                path = directory / 'object-blobs' / str(number)
                path.parent.mkdir(exist_ok=True)
                output = path.open('xb')
            with output:
                obj = store.get_object(Bucket=bucket, Key=key)
                headers = metadata(obj)
                if 'Expires' in headers:
                    headers['Expires'] = headers['Expires'].isoformat()
                checksum = hashlib.sha256()
                with closing(obj['Body']) as body:
                    for chunk in iter(lambda: body.read(1024 * 1024), b''):
                        output.write(chunk)
                        checksum.update(chunk)
            index.write(json.dumps([key, str(path.relative_to(directory)), headers, checksum.hexdigest()]) + '\n')

    with (directory / 'object-index.jsonl').open('rb') as index, (directory / 'manifest.txt').open('a') as manifest:
        manifest.write(f'object_index_sha256={stream_digest(index)}\n')


def import_archive(store, bucket, directory):
    root = directory / 'objects'
    index = directory / 'object-index.jsonl'
    manifest = directory / 'manifest.txt'
    if manifest.is_file():
        for line in manifest.read_text().splitlines():
            if line.startswith('object_index_sha256='):
                with index.open('rb') as stream:
                    if stream_digest(stream) != line.split('=', 1)[1]:
                        raise ValueError('object index does not match backup manifest')
                break
    if not index.exists():
        # Historical mc mirrors have no metadata sidecar.
        for path in root.rglob('*'):
            if path.is_file():
                mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                with path.open('rb') as body:
                    upload(store, bucket, path.relative_to(root).as_posix(), body, {'ContentType': mime})
        return
    with index.open() as entries:
        for line in entries:
            key, relative, headers, expected = json.loads(line)
            path = (directory / relative).resolve()
            if not any(path.is_relative_to(directory / base) for base in ('objects', 'object-blobs')):
                raise ValueError('invalid object archive path')
            if 'Expires' in headers:
                headers['Expires'] = datetime.fromisoformat(headers['Expires'])
            with path.open('rb') as body:
                upload(store, bucket, key, body, headers)
            if fingerprint(store, bucket, key) != (headers, expected):
                raise ValueError('restored object does not match backup')


def main(command, *args):
    # Restores create private staging buckets. Historical MinIO application
    # keys may only have access to the live bucket.
    store = client(os.environ.get('ASSOZETA_STORAGE_BACKEND') or 'minio')
    bucket = os.environ['AWS_STORAGE_BUCKET_NAME']
    if command == 'size':
        print(sum(entry['Size'] for entry in objects(store, bucket)))
    elif command == 'probe':
        client(args[0]).head_bucket(Bucket=bucket)
    elif command == 'copy':
        backend, phase = args
        mirror(store, bucket, client(backend), bucket, phase == 'final')
    elif command == 'export':
        export_archive(store, bucket, Path('/backup'))
    elif command == 'stage':
        store.create_bucket(Bucket=args[0])
        import_archive(store, args[0], Path('/backup'))
    elif command == 'snapshot':
        store.create_bucket(Bucket=args[1])
        mirror(store, args[0], store, args[1], True)
    elif command == 'replace':
        mirror(store, args[0], store, args[1], True)
    elif command == 'remove':
        from botocore.exceptions import ClientError

        try:
            for entry in objects(store, args[0]):
                store.delete_object(Bucket=args[0], Key=entry['Key'])
            store.delete_bucket(Bucket=args[0])
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'NoSuchBucket':
                raise
    else:
        raise ValueError('unknown storage operation')


if __name__ == '__main__':
    main(*sys.argv[1:])
