"""Differential harness for the Go Celery consumer: print, storage and archive tasks.

Tasks: print_document_subscription, print_document_invoice,
print_document_customer_invoice, print_document_compensation,
print_document_medical_appointment (GET CURRENT_HOST/document/...),
save_to_storage (S3 default storage) and export_invoices_to_zip (Document,
archive row and ZIP in the S3 default storage).

Private PostgreSQL (actual Django current schema), a private Redis broker, a
private MinIO (the production S3Boto3Storage backend) and a local HTTP stub
standing for CURRENT_HOST that records every request (method, path, headers).
Each scenario builds one message with Django's own producer, routed by
GO_TASK_QUEUE_TASKS to ``assozeta-go``, and runs the identical bytes once on a
real Django Celery worker (solo pool, ``-Q assozeta-go``, django-db results,
CoordinatedTask) and, after re-seeding the database, the bucket and the stub,
once on the race-built Go consumer. After each worker stopped the harness
compares: the stub's requests, the complete managed-model state (TaskResult,
Document, archive, auditlog rows), every broker key, and every bucket object
(key, body and the metadata S3Storage writes). Random per-run values (new
document ids and tokens, timestamps, the Rome-clock archive name, a random
alternative storage name) are normalized after their shape and run window are
checked. ZIP objects are compared entry by entry: every header field and the
decompressed bytes; the DOS times must fall within the run window; the deflate
streams themselves (and so the compressed sizes, offsets, object size and
ETag) are not compared. Authored assertions check the Django oracle's path.
Handoff scenarios require Go to move the message unchanged to Django's
``celery`` queue with no effect; a Django worker on that queue must then reach
the Django-only final state.
"""
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import free_port

DATABASE = 'assozeta_contract_celery_consumer_print'
REDIS_PASSWORD = 'private-redis-password-canary'
GO_QUEUE = 'assozeta-go'
DJANGO_QUEUE = 'celery'
BUCKET = 'consumer-print'
MISSING_BUCKET = 'consumer-print-missing'
LOCATION = 'private'
TASKS = ('print_document_subscription', 'print_document_invoice', 'print_document_customer_invoice',
         'print_document_compensation', 'print_document_medical_appointment', 'save_to_storage', 'export_invoices_to_zip')
OUTPUT = ROOT / 'quality-reports/go-migration/celery-consumer-print-process'
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
ROME_NAME = re.compile(r'(\d{4}-\d\d-\d\d_\d\d-\d\d)_ricevute\.zip')
AWARE = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d{1,6})?(?:\+00:00)?')
ALTERNATIVE = re.compile(r'_[A-Za-z0-9]{7}(?=\.pdf$)')


def U(n):
    return UUID(int=0xC6000000 + n)


OWNER, OTHER, ASSOCIATION, DELETED = U(1), U(2), U(11), U(12)
D_FILEPATH, D_MISSING, D_TIMESTAMP, D_EMPTYPATH, D_OTHER = U(21), U(22), U(23), U(24), U(25)
SOURCE_CREATED = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
TOKEN = 'Bearer fixture-print-token'


def stored_objects():
    """The seed bucket: source documents of the export and a taken name."""
    def ts(created, doc):
        return f'{LOCATION}/{created.timestamp()}/{doc}'
    return {
        f'{LOCATION}/exports/src/one.pdf': PDF + b'one',
        f'{ts(SOURCE_CREATED, D_TIMESTAMP)}/ricevuta-2.pdf': PDF + b'two' * 2000,
        f'{ts(SOURCE_CREATED, D_EMPTYPATH)}/ricevuta è.pdf': b'',
        f'{ts(SOURCE_CREATED, D_OTHER)}/altro.pdf': PDF + b'not requested',
        f'{LOCATION}/existing/taken.pdf': b'already stored',
    }


class Scenario:
    def __init__(self, name, task, args=(), kwargs=None, *, retries=None, send_task=False, http='ok', storage='ok',
                 setup=None, done='result', handoff=False, expect=None):
        self.name, self.task, self.args, self.kwargs = name, task, list(args), kwargs or {}
        self.retries, self.send_task, self.http, self.storage = retries, send_task, http, storage
        self.setup, self.done, self.handoff, self.expect = setup, done, handoff, expect or {}


B64 = base64.b64encode(PDF).decode()
SCENARIOS = [
    # -- print tasks --------------------------------------------------------------------------
    Scenario('subscription print sends the GET with the token', 'print_document_subscription', [str(U(101)), TOKEN],
             expect={'status': 'SUCCESS', 'requests': [f'/document/subscription/{U(101)}']}),
    Scenario('invoice print sends the default flag', 'print_document_invoice', [str(U(102)), TOKEN],
             expect={'status': 'SUCCESS', 'requests': [f'/document/invoice/{U(102)}?send_receipt_email=False']}),
    Scenario('invoice print with send_receipt_email keyword and a 500 from the renderer', 'print_document_invoice',
             kwargs={'invoice_id': str(U(103)), 'auth_token': TOKEN, 'send_receipt_email': True}, http='error',
             expect={'status': 'SUCCESS', 'requests': [f'/document/invoice/{U(103)}?send_receipt_email=True']}),
    Scenario('customer invoice print follows a redirect', 'print_document_customer_invoice', [str(U(104)), TOKEN], http='redirect',
             expect={'status': 'SUCCESS', 'requests': [f'/document/einvoice/{U(104)}', f'/final/document/einvoice/{U(104)}']}),
    Scenario('compensation print with the connection refused succeeds', 'print_document_compensation', [str(U(105)), TOKEN],
             http='refused', expect={'status': 'SUCCESS', 'requests': []}),
    Scenario('medical appointment print without a token sends no Authorization', 'print_document_medical_appointment',
             [str(U(106)), None], expect={'status': 'SUCCESS', 'requests': [f'/document/medical-appointment/{U(106)}'], 'auth': None}),
    Scenario('a UUID id and a republished message (retries 2) print normally', 'print_document_subscription', [U(107), TOKEN],
             retries=2, expect={'status': 'SUCCESS', 'requests': [f'/document/subscription/{U(107)}']}),
    Scenario('a non-string token raises before any request', 'print_document_compensation', [str(U(108)), 12345],
             expect={'status': 'SUCCESS', 'requests': []}),
    Scenario('a token starting with a blank raises before any request', 'print_document_invoice', [str(U(109)), ' Bearer x'],
             expect={'status': 'SUCCESS', 'requests': []}),
    Scenario('a restore completed after the stamp refuses the print', 'print_document_subscription', [str(U(110)), TOKEN],
             setup='restore-completed', expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'requests': []}),
    Scenario('a pending restore defers the print by 15 s', 'print_document_invoice', [str(U(111)), TOKEN], setup='restore-pending',
             done='held', expect={'status': 'RETRY', 'exc': 'Busy', 'retry': (0, 15), 'requests': []}),
    Scenario('a print with an extra argument is handed off', 'print_document_subscription', [str(U(112)), TOKEN, 'extra'],
             send_task=True, handoff=True, expect={'status': 'FAILURE', 'exc': 'TypeError', 'requests': []}),
    # -- save_to_storage ----------------------------------------------------------------------
    Scenario('save_to_storage stores the decoded PDF', 'save_to_storage', ['documenti/2026/ricevuta n.1.pdf', B64],
             expect={'status': 'SUCCESS', 'new_objects': [f'{LOCATION}/documenti/2026/ricevuta n.1.pdf']}),
    Scenario('save_to_storage of empty gzip-typed content', 'save_to_storage', ['./archivio//export.tar.gz', ''],
             expect={'status': 'SUCCESS', 'new_objects': [f'{LOCATION}/archivio/export.tar.gz']}),
    Scenario('save_to_storage with incorrect padding fails', 'save_to_storage', ['documenti/x.pdf', 'QUJDRA'],
             expect={'status': 'FAILURE', 'exc': 'Error', 'new_objects': []}),
    Scenario('save_to_storage with 4n+1 data characters fails', 'save_to_storage', ['documenti/x.pdf', 'QUJDR'],
             expect={'status': 'FAILURE', 'exc': 'Error', 'new_objects': []}),
    Scenario('save_to_storage outside the location fails', 'save_to_storage', ['documenti/../../fuori.pdf', B64],
             expect={'status': 'FAILURE', 'exc': 'SuspiciousFileOperation', 'new_objects': []}),
    Scenario('save_to_storage to a missing bucket fails', 'save_to_storage', ['documenti/y.pdf', B64], storage='missing-bucket',
             expect={'status': 'FAILURE', 'exc': 'NoSuchBucket', 'new_objects': []}),
    Scenario('save_to_storage with rejected credentials fails on the existence check', 'save_to_storage', ['documenti/z.pdf', B64],
             storage='forbidden', expect={'status': 'FAILURE', 'exc': 'ClientError', 'new_objects': []}),
    Scenario('save_to_storage after a completed restore is refused', 'save_to_storage', ['documenti/r.pdf', B64],
             setup='restore-completed', expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'new_objects': []}),
    Scenario('save_to_storage to a taken name is handed off', 'save_to_storage', ['existing/taken.pdf', B64], handoff=True,
             expect={'status': 'SUCCESS', 'new_objects': [f'{LOCATION}/existing/taken<alternative>.pdf']}),
    # -- export_invoices_to_zip ---------------------------------------------------------------
    Scenario('export zips the readable documents and archives the zip', 'export_invoices_to_zip',
             [[str(D_FILEPATH), str(D_MISSING), str(D_TIMESTAMP), str(D_EMPTYPATH), str(U(99))], ASSOCIATION],
             expect={'status': 'SUCCESS', 'documents': 1, 'archives': 1, 'zip': ['1_ricevuta 1.pdf', '3_ricevuta-2.pdf', '4_ricevuta è.pdf']}),
    Scenario('export without files does nothing', 'export_invoices_to_zip', [], expect={'status': 'SUCCESS', 'documents': 0}),
    Scenario('export of an empty list stores an empty archive', 'export_invoices_to_zip', kwargs={'files': [],
             'sport_association': str(ASSOCIATION)}, expect={'status': 'SUCCESS', 'documents': 1, 'archives': 1, 'zip': []}),
    Scenario('export for a deleted association keeps only the document', 'export_invoices_to_zip', [[str(D_FILEPATH)], DELETED],
             expect={'status': 'SUCCESS', 'documents': 1, 'archives': 0}),
    Scenario('export with a malformed file id does nothing', 'export_invoices_to_zip', [[str(D_FILEPATH), 'non-un-uuid'], ASSOCIATION],
             expect={'status': 'SUCCESS', 'documents': 0}),
    Scenario('export with a malformed association keeps only the document', 'export_invoices_to_zip', [[U(21)], 'associazione'],
             expect={'status': 'SUCCESS', 'documents': 1, 'archives': 0}),
    Scenario('export to a missing bucket keeps the rows without an object', 'export_invoices_to_zip', [[str(D_FILEPATH)], ASSOCIATION],
             storage='missing-bucket', expect={'status': 'SUCCESS', 'documents': 1, 'archives': 1}),
    Scenario('export after a completed restore is refused', 'export_invoices_to_zip', [[str(D_FILEPATH)], ASSOCIATION],
             setup='restore-completed', expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'documents': 0}),
    Scenario('export with an integer file id is handed off', 'export_invoices_to_zip', [[5], ASSOCIATION], handoff=True,
             expect={'status': 'SUCCESS', 'documents': 1, 'archives': 1, 'zip': []}),
]


# -- CURRENT_HOST stub ----------------------------------------------------------------------------
class Stub:
    def __init__(self):
        self.requests, self.mode = [], 'ok'
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                outer.requests.append({'method': 'GET', 'path': self.path,
                                       'headers': sorted([k.lower(), v] for k, v in self.headers.items())})
                if outer.mode == 'redirect' and not self.path.startswith('/final/'):
                    self.send_response(302)
                    self.send_header('Location', '/final' + self.path)
                    body = b''
                elif outer.mode == 'error':
                    self.send_response(500)
                    body = b'renderer failure'
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/pdf')
                    body = PDF
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def take(self):
        requests, self.requests = self.requests, []
        return requests

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Stack:
    def __init__(self, work, environment, redis_port, stub, storage):
        import boto3
        import redis
        from botocore.config import Config
        self.work, self.env, self.redis_port, self.stub, self.storage = work, environment, redis_port, stub, storage
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=10)
        self.client = lambda: boto3.client('s3', endpoint_url=storage['endpoint'], aws_access_key_id=storage['access'],
                                           aws_secret_access_key=storage['secret'], region_name='us-east-1',
                                           config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
        self.s3 = self.client()
        self.s3.create_bucket(Bucket=BUCKET)
        self.closed_port = free_port()
        self.children = []

    # -- seed ---------------------------------------------------------------------------------
    def seed(self, scenario, stamp=None):
        from django.db import connection
        from application.models import User, SportAssociation
        from docmanager.models import Document
        from instance.models import DataRestore
        started = datetime.now(timezone.utc)
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, django_celery_results_taskresult,
              instance_datarestore, application_sportassociationdocumentsarchive, docmanager_document,
              application_sportassociation, instance_instanceconfiguration, bakney_user RESTART IDENTITY CASCADE''')
        User.original_objects.bulk_create([User(user_id=pk, username=f'print-owner-{i}', role=1, password='!private-fixture',
                                                date_joined=SOURCE_CREATED, email=f'print{i}@example.test')
                                           for i, pk in enumerate((OWNER, OTHER))])
        SportAssociation.original_objects.bulk_create([
            SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER, denomination='ASD Stampa', tax_code='00000000000'),
            SportAssociation(sport_association_id=DELETED, user_id=OTHER, denomination='Cancellata', tax_code='00000000000', deleted=True)])
        # Insertion order is the heap order the unordered filter returns.
        Document.objects.bulk_create([
            Document(document_id=D_FILEPATH, creation_date=SOURCE_CREATED, filepath='exports/src/one.pdf', filename='ricevuta 1.pdf',
                     token=U(31)),
            Document(document_id=D_MISSING, creation_date=SOURCE_CREATED, filepath='exports/src/missing.pdf', filename='mancante.pdf',
                     token=U(32)),
            Document(document_id=D_TIMESTAMP, creation_date=SOURCE_CREATED, filepath=None, filename='ricevuta-2.pdf', token=U(33)),
            Document(document_id=D_EMPTYPATH, creation_date=SOURCE_CREATED, filepath='', filename='ricevuta è.pdf', token=U(34)),
            Document(document_id=D_OTHER, creation_date=SOURCE_CREATED, filepath=None, filename='altro.pdf', token=U(35))])
        if scenario.setup == 'restore-pending':
            DataRestore._base_manager.bulk_create([DataRestore(id=U(50), owner_id=OWNER, association_id=ASSOCIATION, state='running',
                                                                sha256='0' * 64)])
        if scenario.setup == 'restore-completed':
            DataRestore._base_manager.bulk_create([DataRestore(id=U(51), owner_id=OWNER, association_id=ASSOCIATION,
                state='completed', completed_at=datetime.fromtimestamp(stamp + 0.001, timezone.utc), sha256='0' * 64)])
        listed = self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', [])
        for item in listed:
            self.s3.delete_object(Bucket=BUCKET, Key=item['Key'])
        for key, body in stored_objects().items():
            # MinIO's reply to an empty PUT leaves the pooled connection unusable (urllib3
            # HeaderParsingError, then a 30 s read timeout): empty bodies use a throwaway client.
            client = self.client() if not body else self.s3
            client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType='application/pdf')
            if client is not self.s3:
                client.close()
        self.stub.take()
        self.seeding = (started, datetime.now(timezone.utc))

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding

        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def bucket(self):
        objects = {}
        for item in self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            key = item['Key']
            head = self.s3.head_object(Bucket=BUCKET, Key=key)
            body = self.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
            entry = {k: head.get(k) for k in ('ContentType', 'ContentEncoding', 'CacheControl', 'Metadata')}
            if key.endswith('.zip'):
                entry['zip'] = describe_zip(body)
            else:
                entry.update(ContentLength=head['ContentLength'], ETag=head['ETag'], body=base64.b64encode(body).decode())
            objects[key] = entry
        return objects

    # -- broker -------------------------------------------------------------------------------
    def broker_state(self):
        state = {}
        for key in sorted(self.broker.keys('*')):
            kind = self.broker.type(key).decode()
            name = key.decode('utf-8', 'backslashreplace')
            if kind == 'list':
                state[name] = ['list', [item.decode() for item in self.broker.lrange(key, 0, -1)]]
            elif kind == 'set':
                state[name] = ['set', sorted(item.decode('utf-8', 'backslashreplace') for item in self.broker.smembers(key))]
            elif kind == 'hash':
                state[name] = ['hash', {k.decode(): v.decode() for k, v in self.broker.hgetall(key).items()}]
            elif kind == 'zset':
                state[name] = ['zset', [(m.decode(), s) for m, s in self.broker.zrange(key, 0, -1, withscores=True)]]
            else:
                state[name] = [kind, None]
        return state

    def publish(self, scenario):
        from core.celery import app
        options = {}
        if scenario.retries is not None:
            options['retries'] = scenario.retries
        self.broker.flushdb()
        if scenario.send_task:
            app.send_task(scenario.task, args=scenario.args, kwargs=scenario.kwargs, **options)
        else:
            app.tasks[scenario.task].apply_async(args=scenario.args, kwargs=scenario.kwargs, **options)
        raw = self.broker.rpop(GO_QUEUE)
        assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        return raw.decode()

    def push(self, payload):
        self.broker.flushdb()
        self.broker.lpush(GO_QUEUE, payload)

    def quiescent(self, scenario, task_id):
        from django_celery_results.models import TaskResult
        if self.broker.llen(GO_QUEUE) or self.broker.llen(DJANGO_QUEUE):
            return False
        held = 1 if scenario.done == 'held' else 0
        if self.broker.hlen('unacked') != held:
            return False
        rows = {row.task_id: row.status for row in TaskResult.objects.all()}
        final = ('SUCCESS', 'FAILURE', 'REVOKED', 'RETRY') if held else ('SUCCESS', 'FAILURE', 'REVOKED')
        return task_id in rows and all(status in final for status in rows.values())

    def settings(self, scenario):
        host = f'http://127.0.0.1:{self.closed_port if scenario.http == "refused" else self.stub.port}'
        secret = 'rejected-' + self.storage['secret'][9:] if scenario.storage == 'forbidden' else self.storage['secret']
        bucket = MISSING_BUCKET if scenario.storage == 'missing-bucket' else BUCKET
        return host, {'AWS_ACCESS_KEY_ID': self.storage['access'], 'AWS_SECRET_ACCESS_KEY': secret, 'AWS_STORAGE_BUCKET_NAME': bucket,
                      'AWS_S3_REGION_NAME': 'us-east-1', 'AWS_S3_ENDPOINT_URL': self.storage['endpoint'], 'AWS_LOCATION': LOCATION}


def describe_zip(data):
    """Every header field and the decompressed bytes; deflate-dependent fields removed, DOS times kept for the window check."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    entries = []
    for info in archive.infolist():
        offset = info.header_offset
        assert data[offset:offset + 4] == b'PK\x03\x04'
        names, extra = struct.unpack('<HH', data[offset + 26:offset + 30])
        local = bytearray(data[offset:offset + 30 + names + extra])
        local[18:22] = b'\0\0\0\0'          # compressed size
        local[10:14] = b'\0\0\0\0'          # DOS time and date (checked through date_time)
        entries.append({'filename': info.filename, 'orig_filename': info.orig_filename, 'date_time': list(info.date_time),
                        'compress_type': info.compress_type, 'flag_bits': info.flag_bits, 'create_system': info.create_system,
                        'create_version': info.create_version, 'extract_version': info.extract_version, 'reserved': info.reserved,
                        'volume': info.volume, 'internal_attr': info.internal_attr, 'external_attr': info.external_attr,
                        'extra': info.extra.hex(), 'comment': info.comment.hex(), 'CRC': info.CRC, 'file_size': info.file_size,
                        'local_header': local.hex(),
                        'data': base64.b64encode(archive.open(info).read()).decode()})
    end = data.rfind(b'PK\x05\x06')
    record = bytearray(data[end:])
    record[12:20] = b'\0' * 8                # central directory size and offset
    return {'entries': entries, 'comment': archive.comment.hex(), 'end_record': record.hex(),
            'leading_bytes': data[:4].hex()}


class Harness:
    def __init__(self, stack, binary):
        self.stack, self.binary = stack, binary
        self.completed, self.observations = [], {}

    def django_context(self, scenario):
        from contextlib import ExitStack
        from unittest.mock import patch
        from django.test import override_settings
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        import application.printing_tasks as printing_tasks
        import application.tasks as application_tasks
        stack = ExitStack()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.stack.redis_port}', DJANGO_QUEUE,
                                        go_task_routes(','.join(TASKS)), 'django-db'))))
        _reset_celery_app(app)

        def restore():
            app.conf.update(saved)
            _reset_celery_app(app)
        stack.callback(restore)
        host, storage = self.stack.settings(scenario)
        stack.enter_context(patch.object(printing_tasks, 'CURRENT_HOST', host))
        stack.enter_context(patch.object(application_tasks, 'CURRENT_HOST', host))
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        stack.enter_context(override_settings(STORAGES=storages, AWS_S3_ADDRESSING_STYLE='path', AWS_DEFAULT_ACL=None,
                                              AWS_S3_FILE_OVERWRITE=False, DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'operations.lock'),
                                              **storage))
        self.stack.stub.mode = scenario.http
        return stack, app

    def run_django(self, scenario, payload, queues=(GO_QUEUE,), push=True, wait=None):
        from celery.contrib.testing.worker import start_worker
        task_id = json.loads(payload)['headers']['id']
        stack, app = self.django_context(scenario)
        with stack:
            if push:
                self.stack.push(payload)
            with contextlib.redirect_stdout(io.StringIO()), start_worker(app, pool='solo', concurrency=1, perform_ping_check=False,
                                                                          loglevel='WARNING', shutdown_timeout=30, queues=list(queues)):
                self.wait(wait or (lambda: self.stack.quiescent(scenario, task_id)), 'Django worker: ' + scenario.name)
                time.sleep(0.3)
        return task_id

    def env_file(self, scenario):
        host, storage = self.stack.settings(scenario)
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(self.stack.work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', REDIS_HOST='127.0.0.1', REDIS_PORT=str(self.stack.redis_port), REDIS_USERNAME='default',
            REDIS_PASSWORD=REDIS_PASSWORD, WHITELABEL_NAME='Assozeta Fixture', IS_WHITELABEL='False', GO_TASK_QUEUE_TASKS=','.join(TASKS),
            DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'operations.lock'), CURRENT_HOST=host, EMAIL_HOST='127.0.0.1',
            EMAIL_PORT='25', EMAIL_HOST_USER='noreply@example.test', EMAIL_HOST_PASSWORD='', **storage)
        path = self.stack.work / 'consumer.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        path.chmod(0o600)
        return path

    def run_go(self, scenario, payload, push=True, handoff=None):
        task_id = json.loads(payload)['headers']['id']
        handoff = scenario.handoff if handoff is None else handoff
        if push:
            self.stack.push(payload)
        self.stack.stub.mode = scenario.http
        log = OUTPUT / f'consumer-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        process = subprocess.Popen([str(self.binary), '--env-file', str(self.env_file(scenario)), '--concurrency', '1'],
                                   env=self.stack.env, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            if handoff:
                self.wait(lambda: self.stack.broker.llen(GO_QUEUE) == 0 and self.stack.broker.hlen('unacked') == 0
                          and self.stack.broker.llen(DJANGO_QUEUE) == 1 or process.poll() is not None, 'Go handoff: ' + scenario.name)
            else:
                self.wait(lambda: self.stack.quiescent(scenario, task_id) or process.poll() is not None, 'Go consumer: ' + scenario.name)
            assert process.poll() is None, 'Go consumer exited: ' + log.read_text()[-2000:]
            time.sleep(0.3)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=30)
            stream.close()
        text = log.read_text()
        assert 'WARNING: DATA RACE' not in text, 'Go race detector: ' + scenario.name
        for private in (REDIS_PASSWORD, 'private-worker-password-canary', self.stack.storage['secret']):
            assert private not in text, 'Go consumer leaked private configuration'
        assert code == 0, f'Go consumer exit status {code}: {text[-2000:]}'
        return task_id

    @staticmethod
    def wait(predicate, description, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Timed out: ' + description)

    # -- observation --------------------------------------------------------------------------
    def observe(self, scenario, payload, since, until, before):
        import pytz
        stack = self.stack
        state = stack.snapshot()
        original = json.loads(payload)
        seed_documents = {row['document_id'] for row in before['docmanager.Document']}
        replacements = {}
        new_documents = sorted((row for row in state['docmanager.Document'] if row['document_id'] not in seed_documents),
                               key=lambda row: row['creation_date'])
        for i, row in enumerate(new_documents):
            assert since <= row['creation_date'] <= until, 'Document.creation_date outside the run window'
            replacements[str(row['document_id'])] = f'<new-document-{i}>'
            replacements[str(row['token'])] = f'<new-token-{i}>'
            replacements[str(row['creation_date'].timestamp())] = f'<new-timestamp-{i}>'
        seed_archives = {row['sport_association_documents_archive_id'] for row in before['application.SportAssociationDocumentsArchive']}
        for i, row in enumerate(r for r in state['application.SportAssociationDocumentsArchive']
                                if r['sport_association_documents_archive_id'] not in seed_archives):
            replacements[str(row['sport_association_documents_archive_id'])] = f'<new-archive-{i}>'
        rome = pytz.timezone('Europe/Rome')
        minutes = set()
        moment = since.replace(second=0, microsecond=0)
        while moment <= until:
            minutes.add(moment.astimezone(rome).strftime('%Y-%m-%d_%H-%M'))
            moment += timedelta(minutes=1)

        def text(value):
            for old, new in replacements.items():
                value = value.replace(old, new)

            def rome_name(match):
                assert match.group(1) in minutes, f'archive name {match.group(0)} outside the run window'
                return '<rome-minute>_ricevute.zip'
            value = ROME_NAME.sub(rome_name, value)

            def aware(match):
                # auditlog renders aware values as str() and DateTimeFields as naive UTC.
                moment = datetime.fromisoformat(match.group(0)).replace(tzinfo=timezone.utc)
                assert since <= moment <= until, f'audited time {match.group(0)} outside the run window'
                return '<run-time>'
            return AWARE.sub(aware, value)

        def normalize(value):
            if isinstance(value, datetime):
                if since <= value <= until:
                    return 'run'
                return value.isoformat()
            if isinstance(value, date):
                if since.date() <= value <= until.date():
                    return 'run-date'
                return value.isoformat()
            if isinstance(value, UUID):
                return text(str(value))
            if isinstance(value, str):
                return text(value)
            if isinstance(value, dict):
                return {text(str(k)): normalize(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            return value

        rows = state.pop('django_celery_results.TaskResult')
        results, reference = [], None
        for row in rows:
            row = dict(row)
            reference = row['date_done']
            for field in ('date_created', 'date_done'):
                assert since <= row[field] <= until, f'{field} outside the run window'
                row[field] = 'run'
            row.pop('id')
            if row['task_id'] != original['headers']['id']:
                UUID(row['task_id'])
                row['task_id'] = '<child-task-id>'
            results.append(row)
        for row in state['application.AuditLogIndex']:
            UUID(str(row['audit_log_index_id']))
            row['audit_log_index_id'] = '<index-id>'
        bucket = {}
        for key, entry in stack.bucket().items():
            if 'zip' in entry:
                for item in entry['zip']['entries']:
                    at = datetime(*item['date_time'], tzinfo=timezone.utc)
                    assert since.replace(microsecond=0) - timedelta(seconds=2) <= at <= until, f'zip entry time {at} outside the run window'
                    item['date_time'] = 'run'
            key = text(key)
            if scenario.handoff and key not in {text(k) for k in stored_objects()}:
                key = ALTERNATIVE.sub('<alternative>', key)
            bucket[key] = normalize(entry)
        broker = {}
        for key, (kind, value) in stack.broker_state().items():
            if kind == 'list':
                value = [self.normalize_message(item, original, reference) for item in value]
            elif kind == 'hash':
                value = {('<original-tag>' if k == original['properties']['delivery_tag'] else '<new-tag>'):
                         self.normalize_unacked(v, original, reference) for k, v in value.items()}
            elif kind == 'zset':
                value = len(value)
            broker[key] = [kind, value]
        return {'state': normalize(state), 'task_results': results, 'broker': broker, 'requests': stack.stub.take(), 'bucket': bucket}

    def normalize_unacked(self, raw, original, reference):
        message, exchange, routing_key = json.loads(raw)
        return [self.normalize_message(json.dumps(message), original, reference), exchange, routing_key]

    @staticmethod
    def normalize_message(raw, original, reference):
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = '<original-tag>' if properties['delivery_tag'] == original['properties']['delivery_tag'] else '<new-tag>'
        assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
        headers['origin'] = '<original-origin>' if headers['origin'] == original['headers']['origin'] else '<origin>'
        UUID(properties['reply_to'])
        properties['reply_to'] = '<original-reply-to>' if properties['reply_to'] == original['properties']['reply_to'] else '<reply-to>'
        if headers.get('eta') and headers['eta'] != original['headers'].get('eta'):
            assert reference is not None, 'a new eta without a task result'
            offset = datetime.fromisoformat(headers['eta']).timestamp() - reference.timestamp()
            assert 0 < math.ceil(offset) - offset < 1 or offset == int(offset), offset
            headers['eta'] = 'date_done+%ds' % math.ceil(offset)
        message['body'] = json.loads(base64.b64decode(message['body']))
        return json.dumps(message, ensure_ascii=False)

    # -- scenarios ----------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        context, _ = self.django_context(scenario)
        with context:
            payload = stack.publish(scenario)
        stamp = json.loads(payload)['headers']['assozeta_published_at']
        stack.seed(scenario, stamp)
        before, seeded_bucket = stack.snapshot(), stack.bucket()
        since = datetime.now(timezone.utc)
        self.run_django(scenario, payload)
        until = datetime.now(timezone.utc)
        django = self.observe(scenario, payload, since, until, before)
        self.authored(scenario, payload, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return

        stack.seed(scenario, stamp)
        assert stack.snapshot() == before and stack.bucket() == seeded_bucket, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        self.run_go(scenario, payload)
        if scenario.handoff:
            handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
            assert len(handed) == 1, 'handoff publishes exactly one message'
            expected = json.loads(payload)
            expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
            assert handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes (routing key celery)'
            assert stack.snapshot() == before and stack.bucket() == seeded_bucket and stack.stub.take() == [], 'handoff has no effects'
            task_id = expected['headers']['id']
            from django_celery_results.models import TaskResult
            self.run_django(scenario, payload, queues=(DJANGO_QUEUE,), push=False,
                            wait=lambda: stack.broker.llen(DJANGO_QUEUE) == 0 and stack.broker.hlen('unacked') == 0
                            and TaskResult.objects.filter(task_id=task_id, status__in=('SUCCESS', 'FAILURE')).exists())
        until = datetime.now(timezone.utc)
        go = self.observe(scenario, payload, since, until, before)
        if scenario.handoff:
            for observation in (django, go):
                observation['broker'] = {k: v for k, v in observation['broker'].items() if not k.startswith('_kombu.binding.')}
        self.observations[scenario.name] = {'django': django, 'go': go}
        for side in (django, go):
            # Neither side may compare as empty: a result row and the seeded rows and objects always exist.
            assert side['task_results'] and side['state']['docmanager.Document'] and side['bucket'], 'an observation has no data'
        if go != django:
            (OUTPUT / 'difference.json').write_text(json.dumps({'scenario': scenario.name, 'django': django, 'go': go},
                                                               indent=1, default=str, ensure_ascii=False) + '\n')
        assert go == django, 'Go differs from Django: ' + scenario.name
        self.completed.append(scenario.name)
        print('PASS: ' + scenario.name, flush=True)

    def authored(self, scenario, payload, observed):
        """The Django oracle took the path the scenario was written for."""
        expect = scenario.expect
        results = observed['task_results']
        own = [row for row in results if row['task_id'] == json.loads(payload)['headers']['id']]
        assert len(results) == 1 and len(own) == 1 and own[0]['status'] == expect['status'], results
        children = [[[own[0]['task_id'], None], None]] if expect.get('exc') == 'Busy' else []
        assert own[0]['meta'] == json.dumps({'children': children}), results
        if 'exc' in expect:
            assert json.loads(own[0]['result'])['exc_type'] == expect['exc'], results
        else:
            assert own[0]['result'] == 'null', results
        if 'requests' in expect:
            assert [r['path'] for r in observed['requests']] == expect['requests'], observed['requests']
            for request in observed['requests']:
                headers = dict(request['headers'])
                assert headers.get('authorization') == expect.get('auth', TOKEN), request
        else:
            assert observed['requests'] == [], observed['requests']
        seeded = {k for k in stored_objects()}
        new = sorted(k for k in observed['bucket'] if k not in seeded)
        if 'new_objects' in expect:
            assert new == expect['new_objects'], new
        documents = [row for row in observed['state']['docmanager.Document'] if str(row['document_id']).startswith('<new-document')]
        if 'documents' in expect:
            assert len(documents) == expect['documents'], documents
            archives = [row for row in observed['state']['application.SportAssociationDocumentsArchive']]
            assert len(archives) == expect.get('archives', 0), archives
            zips = [entry for key, entry in observed['bucket'].items() if 'zip' in entry]
            if 'zip' in expect:
                assert len(zips) == 1 and [e['filename'] for e in zips[0]['zip']['entries']] == expect['zip'], zips
                assert new == [f'{LOCATION}/<new-timestamp-0>/<new-document-0>/<rome-minute>_ricevute.zip'], new
            else:
                assert zips == [], zips
        if 'retry' in expect:
            retries, countdown = expect['retry']
            held = [v for k, (kind, v) in observed['broker'].items() if k == GO_QUEUE]
            assert held and len(held[0]) == 1, observed['broker']
            message = json.loads(held[0][0])
            assert message['headers']['retries'] == retries and message['headers']['eta'] == 'date_done+%ds' % countdown


@contextlib.contextmanager
def private_minio(log, env, report):
    from TESTS.adapters.django.instance_logo import minio
    with minio(log, env, report) as (endpoint, access, secret):
        yield {'endpoint': endpoint, 'access': access, 'secret': secret}


def main():
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    import faulthandler
    traces = (OUTPUT / 'stacks.txt').open('w')
    faulthandler.register(signal.SIGUSR1, file=traces, all_threads=True)
    report = OUTPUT / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server or not shutil.which('docker'):
        raise RuntimeError('Private PostgreSQL, Redis, Docker (MinIO) and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    docker_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'DOCKER_HOST', 'DOCKER_CONTEXT')}
    only = os.environ.get('CELERY_CONSUMER_ONLY')
    with tempfile.TemporaryDirectory(prefix='az-consumer-print-', dir='/tmp') as directory, \
            (OUTPUT / 'services.log').open('w') as log, private_minio(log, docker_env, result) as storage:
        work = Path(directory); attempted = False; redis_process = None; stub = None; harness = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            stub = Stub()
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port, stub, storage)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in SCENARIOS:
                if only and only not in scenario.name:
                    continue
                print('START: ' + scenario.name, flush=True)
                harness.execute(scenario)
            if (work / 'celery-consumer').exists():
                result['binary_sha256'] = hashlib.sha256((work / 'celery-consumer').read_bytes()).hexdigest()
        finally:
            try:
                if harness:
                    result.update(scenarios=harness.completed, scenario_count=len(harness.completed))
                    (OUTPUT / 'observations.json').write_text(json.dumps(harness.observations, indent=1, default=str,
                                                                         ensure_ascii=False) + '\n')
                    for process, stream, _ in harness.stack.children:
                        if process.poll() is None:
                            process.kill()
                        stream.close()
            finally:
                try:
                    if stub:
                        stub.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate(); redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    expected = [s.name for s in SCENARIOS if not only or only in s.name]
    assert harness.completed == expected, 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_print_process.py', 'BE/application/printing_tasks.py', 'BE/application/tasks.py',
             'BE/docmanager/tasks.py', 'BE/application/utils/printing.py', 'BE/docmanager/models.py', 'BE/core/task_routing.py',
             'BE/core/settings.py', 'BE/instance/restore/task_base.py']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/', 'consumer_print',
                                                        'consumer_save_to_storage', 'consumer_export', 'internal/pyzip/',
                                                        'pkg/storage/documentkey/', 'pkg/storage/documentobjects/')))
    result.update(success=True, partial=bool(only), race_instrumented=not DJANGO_ONLY, django_only=DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope')}, default=str))


if __name__ == '__main__':
    main()
