"""Differential harness for the Go Celery consumer: export_association_data.

Private PostgreSQL (actual Django current schema), a private Redis (broker DB 0, Django cache DB 1, channel layer DB 0)
and a private MinIO (the production S3Boto3Storage). Each scenario builds one message with Django's own producer,
routed by GO_TASK_QUEUE_TASKS to ``assozeta-go``, and runs the identical bytes once on a real Django worker subprocess
(prefork, ``-Q assozeta-go``, django-db results, CoordinatedTask, the production cache, channel layer and S3 storage)
and, after re-seeding the database, the cache and the bucket, once on the race-built Go consumer.

Compared after each worker stopped:
- the complete managed-model state (TaskResult, Document, archive rows, LogEntry and AuditLogIndex), new ids labelled
  and timestamps checked against the run window;
- every Django cache key (database 1): the unpickled value, its ``updated_at`` inside the run window, the TTL range;
- every broker key with decoded messages (the ``send_mail_async`` child on ``celery`` included: ids labelled, its
  root/parent ids checked, the archive name in the e-mail text normalized after its shape check);
- every bucket object (ContentType, ContentEncoding, CacheControl, Metadata). The export archive is compared member
  by member: names and order, every local and central header field, the compressed bytes and CRC of every member
  except ``manifest.json`` (whose ``export_date`` differs per run: its decompressed JSON is compared with the date
  normalized), DOS times inside the run window. Offsets after ``manifest.json`` follow its compressed size and are
  not compared.

Authored assertions check that the Django oracle took the intended path. Handoff scenarios require Go to move the
message unchanged to Django's ``celery`` queue with no effect; a Django worker on that queue then reaches the
Django-only final state.
"""
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import pickle
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import free_port

DATABASE = 'assozeta_contract_consumer_export'
REDIS_PASSWORD = 'private-redis-password-canary'
GO_QUEUE, DJANGO_QUEUE = 'assozeta-go', 'celery'
TASK = 'export_association_data'
BUCKET, LOCATION = 'consumer-export', 'private'
BRAND, HOST_USER = 'Assozeta Fixture', 'noreply@example.test'
OUTPUT = ROOT / os.environ.get('CONSUMER_EXPORT_OUTPUT', 'quality-reports/go-migration/consumer-restore-export-process')
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
ONLY = os.environ.get('CELERY_CONSUMER_ONLY', '')
ARCHIVE_NAME = re.compile(r'export_([0-9a-f-]{36})_(\d{8}_\d{6})\.zip')
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PBKDF2 = 'pbkdf2_sha256$1000000$fixturesalt$' + base64.b64encode(hashlib.sha256(b'fixture').digest()).decode()
PBKDF2_SHORT = 'pbkdf2_sha256$1000000$fixturesalt$' + base64.b64encode(b'short').decode()
MD5 = 'md5$fixturesalt$' + hashlib.md5(b'fixturesaltfixture').hexdigest()


def U(n):
    return UUID(int=0xC7000000 + n)


OWNER, COLLABORATOR, ATHLETE, OUTSIDER, OTHER_OWNER = U(1), U(2), U(3), U(4), U(5)
ASSOCIATION, OTHER, EMPTY, COLLABORATOR_OWN = U(11), U(12), U(13), U(14)
SMTP_SECRET = 'smtp-fixture-value'
D_PATH, D_MISSING, D_RECONSTRUCTED, D_MEDICAL, D_INVOICE, D_ATTACHMENT, D_ARCHIVED = (U(n) for n in range(21, 28))
E_OLD, E_NEW1, E_NEW2, E_NEW3 = U(31), U(32), U(33), U(34)
SOURCE_CREATED = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class Scenario:
    def __init__(self, name, args=(), kwargs=None, *, seed='rich', handoff=False, expect=None, send_task=False):
        self.name, self.args, self.kwargs, self.seed = name, list(args), kwargs or {}, seed
        self.handoff, self.expect, self.send_task = handoff, expect or {}, send_task


SCENARIOS = [
    Scenario('export of an association with members, subscriptions, payments, documents and media',
             [str(ASSOCIATION), str(OWNER)], expect={'success': True, 'files': 7, 'deleted_exports': 2, 'own_records': True}),
    Scenario('export of an association without other data', [str(EMPTY), str(OTHER_OWNER)], seed='empty',
             expect={'success': True, 'files': 0, 'deleted_exports': 0}),
    Scenario('a missing user stores the failure without an e-mail', [str(ASSOCIATION), str(U(99))],
             expect={'success': False, 'error': 'User matching query does not exist.', 'mails': 0}),
    Scenario('a missing association sends the error e-mail', [str(U(98)), str(OWNER)],
             expect={'success': False, 'error': 'SportAssociation matching query does not exist.', 'mails': 1}),
    Scenario('a credential account with an MD5 password hash is handed off', [str(ASSOCIATION), str(OWNER)], seed='md5',
             handoff=True, expect={'success': True, 'files': 7, 'deleted_exports': 2}),
    Scenario('an export document another row references is handed off', [str(ASSOCIATION), str(OWNER)], seed='referenced',
             handoff=True, expect={'success': True, 'files': 7, 'deleted_exports': 2}),
    Scenario('a pending restore defers the export by 15 s', [str(ASSOCIATION), str(OWNER)], seed='restore-pending',
             expect={'status': 'RETRY'}),
    Scenario('an unknown keyword argument is handed off', [str(ASSOCIATION), str(OWNER)], kwargs={'extra': 1}, handoff=True,
             send_task=True, expect={'status': 'FAILURE'}),
]


def difference_summary(left, right, path='', limit=12):
    """First differing leaves of two observations, so a mismatch is readable in a CI log."""
    found = []

    def walk(a, b, where):
        if len(found) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b), key=str):
                if key not in a or key not in b:
                    found.append(f'{where}/{key}: only in {"django" if key in a else "go"}')
                else:
                    walk(a[key], b[key], f'{where}/{key}')
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f'{where}[{index}]')
        elif a != b:
            found.append(f'{where}: django={str(a)[:160]!r} go={str(b)[:160]!r}')
    walk(left, right, path)
    return ''.join('\n  ' + line for line in found)


def stored_objects(created_key):
    objects = {
        f'{LOCATION}/documents/tessera.pdf': PDF + b'tessera',
        f'{LOCATION}/{created_key}/{D_RECONSTRUCTED}/ricevuta è.pdf': PDF + b'ricevuta' * 3000,
        f'{LOCATION}/medical/certificato.pdf': PDF + b'certificato',
        f'{LOCATION}/invoices/fattura-1.pdf': PDF + b'fattura',
        f'{LOCATION}/attachments/allegato.txt': ('riga di prova\n' * 500).encode(),
        f'{LOCATION}/signatures/firma-socio.png': b'\x89PNG\r\n\x1a\nfirma',
        f'{LOCATION}/exports/old/export_old.zip': b'old export',
        f'{LOCATION}/exports/new1/export_new1.zip': b'new export 1',
        f'{LOCATION}/exports/new2/export_new2.zip': b'new export 2',
        f'{LOCATION}/exports/new3/export_new3.zip': b'new export 3',
    }
    return objects


class Stack:
    def __init__(self, work, environment, redis_port, storage):
        import redis
        import boto3
        from botocore.config import Config
        self.work, self.env, self.redis_port, self.storage = work, environment, redis_port, storage
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=10)
        self.cache = redis.Redis(host='127.0.0.1', port=redis_port, db=1, password=REDIS_PASSWORD, socket_timeout=10)
        self.s3 = boto3.client('s3', endpoint_url=storage['endpoint'], aws_access_key_id=storage['access'],
                               aws_secret_access_key=storage['secret'], region_name='us-east-1',
                               config=Config(s3={'addressing_style': 'path'}, retries={'max_attempts': 1}))
        self.s3.create_bucket(Bucket=BUCKET)
        self.children = []

    @property
    def broker_url(self):
        return f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}'

    # -- seed ---------------------------------------------------------------------------------
    def seed(self, scenario):
        from django.db import connection
        from application.models import (User, SportAssociation, Group, Associate, Subscription, Tags, Payment, PaymentCategory,
                                        VatManagement, MedicalCertificate, Folder, SportAssociationDocumentsArchive, Course,
                                        CourseLocation, Reminder)
        from application.models.invoices_models import Invoice
        from docmanager.models import Document
        started = datetime.now(timezone.utc)
        with connection.cursor() as cursor:
            cursor.execute('SELECT string_agg(quote_ident(tablename), \', \') FROM pg_tables WHERE schemaname = \'public\' '
                           "AND tablename NOT IN ('django_migrations', 'django_content_type', 'auth_permission')")
            tables = cursor.fetchone()[0]
            cursor.execute(f'TRUNCATE {tables} RESTART IDENTITY CASCADE')
        password = MD5 if scenario.seed == 'md5' else PBKDF2
        users = [User(user_id=OWNER, username='export-owner', role=1, password=password, date_joined=SOURCE_CREATED,
                      email='owner@example.test', first_name='Anna'),
                 User(user_id=COLLABORATOR, username='export-collaborator', role=1, password='!unusable', date_joined=SOURCE_CREATED,
                      email='collab@example.test', connected_user_id=None),
                 User(user_id=ATHLETE, username='export-athlete', role=0, password=PBKDF2_SHORT, date_joined=SOURCE_CREATED,
                      email='atleta@example.test', first_name='Luca', last_name='Bianchi'),
                 User(user_id=OUTSIDER, username='export-outsider', role=0, password=PBKDF2, date_joined=SOURCE_CREATED,
                      email='fuori@example.test'),
                 User(user_id=OTHER_OWNER, username='export-other', role=1, password=PBKDF2, date_joined=SOURCE_CREATED,
                      email='other@example.test')]
        User.original_objects.bulk_create(users)
        User.original_objects.filter(user_id=COLLABORATOR).update(connected_user_id=OWNER)
        SportAssociation.original_objects.bulk_create([
            SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER, denomination='ASD Esporta «Uno»', tax_code='01234567890'),
            SportAssociation(sport_association_id=OTHER, user_id=OUTSIDER, denomination='Altra', tax_code='00000000000'),
            SportAssociation(sport_association_id=EMPTY, user_id=OTHER_OWNER, denomination='Vuota', tax_code='99999999999'),
            # The collaborator also runs an association of its own.
            SportAssociation(sport_association_id=COLLABORATOR_OWN, user_id=COLLABORATOR, denomination='Del collaboratore',
                             tax_code='88888888888')])
        created_key = str(SOURCE_CREATED.timestamp())
        if scenario.seed == 'empty':
            self.fix_defaults(started)
            self.reset_bucket(created_key)
            self.seeding = (started, datetime.now(timezone.utc))
            return
        Group.objects.bulk_create([Group(group_id=U(41), name='Gruppo A', description='primo', sport_association_id=ASSOCIATION)])
        Document.objects.bulk_create([
            Document(document_id=D_PATH, creation_date=SOURCE_CREATED, filepath='documents/tessera.pdf', filename='tessera.pdf',
                     token=U(51), file_size_bytes=10),
            Document(document_id=D_MISSING, creation_date=SOURCE_CREATED, filepath='documents/mancante.pdf', filename='mancante.pdf',
                     token=U(52)),
            Document(document_id=D_RECONSTRUCTED, creation_date=SOURCE_CREATED, filepath=None, filename='ricevuta è.pdf', token=U(53)),
            Document(document_id=D_MEDICAL, creation_date=SOURCE_CREATED, filepath='medical/certificato.pdf',
                     filename='certificato.pdf', token=U(54)),
            Document(document_id=D_INVOICE, creation_date=SOURCE_CREATED, filepath='invoices/fattura-1.pdf', filename='fattura-1.pdf',
                     token=U(55)),
            Document(document_id=D_ATTACHMENT, creation_date=SOURCE_CREATED, filepath='attachments/allegato.txt',
                     filename='allegato.txt', token=U(56)),
            Document(document_id=D_ARCHIVED, creation_date=SOURCE_CREATED, filepath='documents/tessera.pdf', filename='',
                     token=U(57)),
        ] + [Document(document_id=pk, creation_date=created, filepath=f'exports/{tag}/export_{tag}.zip',
                      filename=f'export_{tag}.zip', token=U(60 + i))
             for i, (pk, tag, created) in enumerate(((E_OLD, 'old', NOW - timedelta(days=40)), (E_NEW1, 'new1', NOW - timedelta(days=3)),
                                                     (E_NEW2, 'new2', NOW - timedelta(days=2)), (E_NEW3, 'new3', NOW - timedelta(days=1))))])
        Associate.objects.bulk_create([
            Associate(associate_id=U(71), user_id=ATHLETE, sport_association_id=ASSOCIATION, first_name='Luca', last_name='Bianchi',
                      born_date=date(2001, 2, 3), email='atleta@example.test', group_id=U(41), creation_date=SOURCE_CREATED,
                      notes='nota "virgolettata"\nseconda riga\ttab'),
            Associate(associate_id=U(72), sport_association_id=ASSOCIATION, first_name='Cancellato', last_name='Socio', deleted=True,
                      creation_date=SOURCE_CREATED)])
        Tags.objects.bulk_create([Tags(tag_id=U(81), tag_name='agonisti', sport_association_id=ASSOCIATION, creation_date=SOURCE_CREATED),
                                  Tags(tag_id=U(82), tag_name='ünder', sport_association_id=ASSOCIATION, creation_date=SOURCE_CREATED)])
        MedicalCertificate.objects.bulk_create([MedicalCertificate(medical_id=U(91), document_id=D_MEDICAL, user_id=ATHLETE,
                                                                   creation_date=SOURCE_CREATED, expiration_date=date(2027, 1, 1))])
        subscription = Subscription(subscription_id=U(101), sport_association_id=ASSOCIATION, associate_id=U(71), user_id=ATHLETE,
                                    medical_id=U(91), creation_date=SOURCE_CREATED, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                                    custom_data={'zeta': 1, 'alfa': [1.5, 2, None, True], 'testo': 'àèì', 'big': 12345678901234567890},
                                    meta={'k': {'nested': 0.1}}, signature_storage_key='signatures/firma-socio.png')
        Subscription.objects.bulk_create([subscription])
        Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=U(101), tags_id=U(82)),
                                                       Subscription.tags.through(subscription_id=U(101), tags_id=U(81))])
        VatManagement.objects.bulk_create([VatManagement(vat_management_id=U(111), percentage=Decimal('22.00'))]) \
            if hasattr(VatManagement, 'percentage') else None
        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(121), sport_association_id=ASSOCIATION,
                                                             name='Quote')]) if hasattr(PaymentCategory, 'name') else None
        Payment.objects.bulk_create([
            Payment(payment_id=U(131), type='quota', subject=1, amount=Decimal('10.50'), creation_date=SOURCE_CREATED,
                    payment_date=SOURCE_CREATED + timedelta(days=1), sport_association_id=ASSOCIATION, user_id=ATHLETE,
                    meta={'via': 'bonifico'}, course={'id': None}, payment_intent_id='pi_fixture'),
            Payment(payment_id=U(132), type='quota', subject=1, amount=Decimal('0.0000001'), creation_date=SOURCE_CREATED,
                    sport_association_id=ASSOCIATION, deleted=True)])
        Payment.attachments.through.objects.bulk_create([Payment.attachments.through(payment_id=U(131), document_id=D_ATTACHMENT),
                                                         Payment.attachments.through(payment_id=U(131), document_id=D_MISSING)])
        Invoice.objects.bulk_create([Invoice(invoice_id=U(141), sport_association_id=ASSOCIATION, document_pdf_id=D_INVOICE,
                                             membership_fee=Decimal('25.00'), activity_fee=Decimal('0.50'), number=7)])
        Folder.objects.create(name='Radice', sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
        Reminder.objects.bulk_create([Reminder(reminder_id=U(151), sport_association_id=ASSOCIATION, reminder_type=1, frequency=1,
                                               name='Promemoria', next_occurrence=date(2026, 3, 1),
                                               time_occurrence=datetime(2026, 1, 1, 9, 30, 15, 123456).time(), recipients=['a', 'b'])])
        CourseLocation.objects.bulk_create([CourseLocation(course_location_id=U(161), title='Palestra', sport_association_id=ASSOCIATION)]) \
            if hasattr(CourseLocation, 'title') else None
        Course.objects.bulk_create([Course(course_id=U(171), sport_association_id=ASSOCIATION, title='Nuoto', fee=Decimal('100.00'), google_background_color='#123456',
                                           creation_date=SOURCE_CREATED, events=[{'d': 1}])])
        Course.locations.through.objects.bulk_create([Course.locations.through(course_id=U(171), courselocation_id=U(161))])
        self.seed_own_records()
        if scenario.seed == 'referenced':
            Payment.attachments.through.objects.bulk_create([Payment.attachments.through(payment_id=U(131), document_id=E_OLD)])
        if scenario.seed == 'restore-pending':
            from instance.models import DataRestore
            DataRestore._base_manager.bulk_create([DataRestore(id=U(201), owner_id=OWNER, association_id=ASSOCIATION, state='running',
                                                                sha256='0' * 64)])
        SportAssociationDocumentsArchive.objects.bulk_create([
            SportAssociationDocumentsArchive(sport_association_documents_archive_id=U(181), sport_association_id=ASSOCIATION,
                                             document_id=D_PATH, date=date(2026, 1, 2)),
            SportAssociationDocumentsArchive(sport_association_documents_archive_id=U(182), sport_association_id=ASSOCIATION,
                                             document_id=D_RECONSTRUCTED, date=date(2026, 1, 2)),
            SportAssociationDocumentsArchive(sport_association_documents_archive_id=U(183), sport_association_id=ASSOCIATION,
                                             document_id=D_ARCHIVED, date=date(2026, 1, 2)),
        ] + [SportAssociationDocumentsArchive(sport_association_documents_archive_id=U(190 + i), sport_association_id=ASSOCIATION,
                                              document_id=pk, date=(NOW - timedelta(days=days)).date())
             for i, (pk, days) in enumerate(((E_OLD, 40), (E_NEW1, 3), (E_NEW2, 2), (E_NEW3, 1)))])
        self.fix_defaults(started)
        self.cluster_tables()
        self.reset_bucket(created_key)
        self.seeding = (started, datetime.now(timezone.utc))

    @staticmethod
    def seed_own_records():
        """Mail settings, and records of the collaborator's own association next to the exported ones."""
        from application.models import Signature
        from application.models.modules_models import Module, ModuleResponses
        from application.models.user_models import CollaborationInvites, Instructor, UsersOnboarding
        from communications.models import CommunicationConfiguration
        CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(
            communication_configuration_id=U(211), sport_association_id=ASSOCIATION, email_smtp_host='smtp.example.test',
            email_smtp_port=587, email_smtp_user='mittente@example.test', email_smtp_password=SMTP_SECRET,
            email_encryption='TLS')])
        Instructor.objects.bulk_create([
            Instructor(instructor_id=U(221), user_id=OWNER, first_name='Marco', last_name='Verdi'),
            Instructor(instructor_id=U(222), user_id=COLLABORATOR, first_name='Altro', last_name='Istruttore')])
        CollaborationInvites.objects.bulk_create([
            CollaborationInvites(collaboration_invite_id=U(231), user_id=OWNER, email='invito@example.test',
                                 creation_date=SOURCE_CREATED, token='invite-own'),
            CollaborationInvites(collaboration_invite_id=U(232), user_id=COLLABORATOR, email='altro@example.test',
                                 creation_date=SOURCE_CREATED, token='invite-other')])
        Module.objects.bulk_create([
            Module(module_id=U(241), sport_association_id=ASSOCIATION, title='Modulo', creation_date=SOURCE_CREATED),
            Module(module_id=U(242), sport_association_id=COLLABORATOR_OWN, title='Altro modulo', creation_date=SOURCE_CREATED)])
        ModuleResponses.objects.bulk_create([
            ModuleResponses(module_response_id=U(251), module_id=U(241), user_id=COLLABORATOR, response={'a': 1},
                            creation_date=SOURCE_CREATED),
            ModuleResponses(module_response_id=U(252), module_id=U(242), user_id=COLLABORATOR, response={'b': 2},
                            creation_date=SOURCE_CREATED)])
        Signature.objects.bulk_create([Signature(signature_id=U(261), user_id=OWNER, signature='titolare'),
                                       Signature(signature_id=U(262), user_id=COLLABORATOR, signature='altrove')])
        UsersOnboarding.objects.bulk_create([UsersOnboarding(users_onboarding_id=U(271), user_id=COLLABORATOR)])

    @staticmethod
    def fix_defaults(started):
        """Model defaults of timezone.now (seed time) become fixed values, so both runs export the same bytes."""
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
              WHERE table_schema = 'public' AND data_type IN ('timestamp with time zone', 'date')
              AND table_name NOT LIKE 'django_%%' AND table_name NOT LIKE 'auditlog_%%'""")
            for table, column, kind in cursor.fetchall():
                fixed = SOURCE_CREATED if kind != 'date' else SOURCE_CREATED.date()
                bound = started if kind != 'date' else started.date()
                cursor.execute(f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" >= %s', [fixed, bound])

    @staticmethod
    def cluster_tables():
        """Rewrite each public table's rows in primary-key order, so both export runs read them in the same physical order."""
        from django.db import connection

        def quoted(name):
            return '"' + name.replace('"', '""') + '"'
        with connection.cursor() as cursor:
            cursor.execute("""SELECT c.relname, i.relname FROM pg_index x JOIN pg_class c ON c.oid = x.indrelid
              JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE x.indisprimary AND n.nspname = 'public' ORDER BY c.relname""")
            for table, index in cursor.fetchall():
                cursor.execute(f'CLUSTER {quoted(table)} USING {quoted(index)}')
            cursor.execute('ANALYZE')

    def reset_bucket(self, created_key):
        for item in self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            self.s3.delete_object(Bucket=BUCKET, Key=item['Key'])
        for key, body in stored_objects(created_key).items():
            self.s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType='application/octet-stream')
        self.cache.flushdb()

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding

        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    # -- broker -------------------------------------------------------------------------------
    def publish(self, scenario):
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (self.broker_url, DJANGO_QUEUE, go_task_routes(TASK), 'django-db'))))
        _reset_celery_app(app)
        try:
            self.broker.flushdb()
            if scenario.send_task:
                app.send_task(TASK, args=scenario.args, kwargs=scenario.kwargs)
            else:
                app.tasks[TASK].apply_async(args=scenario.args, kwargs=scenario.kwargs)
            raw = self.broker.rpop(GO_QUEUE)
            assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
        self.broker.flushdb()
        return raw.decode()

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
                state[name] = ['zset', len(self.broker.zrange(key, 0, -1))]
            else:
                state[name] = [kind, None]
        return state

    def quiescent(self, task_id, queue=GO_QUEUE, held=0):
        from django_celery_results.models import TaskResult
        if self.broker.llen(queue) or self.broker.hlen('unacked') != held:
            return False
        final = ('RETRY',) if held else ('SUCCESS', 'FAILURE')
        return TaskResult.objects.filter(task_id=task_id, status__in=final).exists()


class Harness:
    def __init__(self, stack, binary):
        self.stack, self.binary = stack, binary
        self.completed, self.observations = [], {}

    @staticmethod
    def wait(predicate, description, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError('Timed out: ' + description)

    def stop(self, process, stream):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=10)
        stream.close()
        return code

    def service_env(self):
        return {'AWS_ACCESS_KEY_ID': self.stack.storage['access'], 'AWS_SECRET_ACCESS_KEY': self.stack.storage['secret'],
                'AWS_STORAGE_BUCKET_NAME': BUCKET, 'AWS_S3_ENDPOINT_URL': self.stack.storage['endpoint'], 'AWS_LOCATION': LOCATION,
                'WHITELABEL_NAME': BRAND, 'EMAIL_HOST_USER': HOST_USER}

    def run_django(self, scenario, payload, task_id, queue=GO_QUEUE, push=True):
        if push:
            self.stack.broker.flushdb()
            self.stack.broker.lpush(GO_QUEUE, payload)
        log = OUTPUT / f'django-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE='TESTS.adapters.go.consumer_restore_settings',
                           CONSUMER_RESTORE_BROKER_URL=self.stack.broker_url, CONSUMER_RESTORE_PRODUCTION_SERVICES='1',
                           CONSUMER_RESTORE_REDIS_PORT=str(self.stack.redis_port), CONSUMER_RESTORE_REDIS_PASSWORD=REDIS_PASSWORD,
                           PYTHONPATH=f'{ROOT / "BE"}:{ROOT}', OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES', **self.service_env())
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-P', 'prefork', '-c', '1',
                                    '-Q', queue, '-n', 'oracle@%h', '--without-heartbeat', '--without-gossip', '--without-mingle',
                                    '-l', 'WARNING'], cwd=ROOT / 'BE', env=environment, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            held = 1 if scenario.seed == 'restore-pending' else 0
            self.wait(lambda: self.stack.quiescent(task_id, queue, held) or process.poll() is not None, 'Django worker: ' + scenario.name)
            assert process.poll() is None, 'Django worker exited: ' + log.read_text()[-3000:]
            time.sleep(0.5)
        finally:
            code = self.stop(process, stream)
        assert code == 0, f'Django worker exit status {code}: {log.read_text()[-3000:]}'

    def env_file(self):
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(self.stack.work), DBPORT='5432',
                      DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
                      JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
                      SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_S3_REGION_NAME='us-east-1', REDIS_HOST='127.0.0.1',
                      REDIS_PORT=str(self.stack.redis_port), REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD,
                      IS_WHITELABEL='False', GO_TASK_QUEUE_TASKS=TASK, CURRENT_HOST='http://127.0.0.1:9',
                      DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'contract-lifecycle.lock'), EMAIL_HOST='127.0.0.1',
                      **self.service_env())
        path = self.stack.work / 'consumer.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        path.chmod(0o600)
        return path

    def run_go(self, scenario, payload, task_id, handoff):
        self.stack.broker.flushdb()
        self.stack.broker.lpush(GO_QUEUE, payload)
        log = OUTPUT / f'consumer-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        process = subprocess.Popen([str(self.binary), '--env-file', str(self.env_file()), '--concurrency', '1'],
                                   env=dict(self.stack.env), stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            if handoff:
                self.wait(lambda: self.stack.broker.llen(GO_QUEUE) == 0 and self.stack.broker.hlen('unacked') == 0
                          and self.stack.broker.llen(DJANGO_QUEUE) == 1 or process.poll() is not None, 'Go handoff: ' + scenario.name)
            else:
                held = 1 if scenario.seed == 'restore-pending' else 0
                self.wait(lambda: self.stack.quiescent(task_id, held=held) or process.poll() is not None, 'Go consumer: ' + scenario.name)
            assert process.poll() is None, 'Go consumer exited: ' + log.read_text()[-3000:]
            time.sleep(0.5)
        finally:
            code = self.stop(process, stream)
        text = log.read_text()
        assert 'WARNING: DATA RACE' not in text, 'Go race detector: ' + scenario.name
        for private in (REDIS_PASSWORD, 'private-worker-password-canary', self.stack.storage['secret']):
            assert private not in text, 'Go consumer leaked private configuration'
        assert code == 0, f'Go consumer exit status {code}: {text[-3000:]}'

    # -- observation --------------------------------------------------------------------------
    def observe(self, payload, since, until):
        original = json.loads(payload)
        task_id = original['headers']['id']
        labels = {task_id: 'task'}

        def label(value, kind):
            if value not in labels:
                UUID(value)
                labels[value] = f'<{kind}-{sum(1 for v in labels.values() if v.startswith("<" + kind))}>'
            return labels[value]

        def window(value):
            assert since <= value <= until, f'timestamp {value} outside the run window'
            return 'run'
        state = self.stack.snapshot()
        before = self.before
        out = {}
        for model, rows in state.items():
            if rows == before.get(model):
                continue
            seeded = {json.dumps(r, sort_keys=True, default=str) for r in before.get(model, [])}
            current = [r for r in rows if json.dumps(r, sort_keys=True, default=str) not in seeded]
            removed = [r for r in before.get(model, []) if r not in rows]
            out[model] = {'removed': removed, 'added': current}
        results = out.pop('django_celery_results.TaskResult', {'added': [], 'removed': []})
        rows = []
        for row in results['added']:
            row = dict(row)
            row.pop('id')
            for field in ('date_created', 'date_done'):
                row[field] = window(row[field])
            row['task_id'] = label(row['task_id'], 'task')
            meta = json.loads(row['meta'])
            meta['children'] = [[[label(child, 'child'), rest], extra] for (child, rest), extra in meta['children']]
            row['meta'] = meta
            result = json.loads(row['result'])
            if isinstance(result, dict) and result.get('document_id'):
                result['document_id'] = label(result['document_id'], 'document')
                result['filename'] = self.archive_name(result['filename'], since, until)
            row['result'] = result
            rows.append(row)
        documents = out.pop('docmanager.Document', {'added': [], 'removed': []})
        new_documents = []
        for row in documents['added']:
            row = dict(row)
            row['document_id'] = label(str(row['document_id']), 'document')
            row['token'] = (UUID(str(row['token'])), 'token')[1]
            row['creation_date'] = window(row['creation_date'])
            row['filename'] = self.archive_name(row['filename'], since, until)
            prefix, stamp, doc, name = row['filepath'].split('/', 3)
            assert prefix == 'exports' and re.fullmatch(r'\d{8}_\d{6}', stamp), row['filepath']
            row['filepath'] = f'exports/<ts>/{label(doc, "document")}/{self.archive_name(name, since, until)}'
            # The archive size follows manifest.json's compressed size (its export_date differs per run).
            assert isinstance(row['file_size_bytes'], int) and row['file_size_bytes'] > 0, row
            row['file_size_bytes'] = 'archive-size'
            new_documents.append(row)
        archives = out.pop('application.SportAssociationDocumentsArchive', {'added': [], 'removed': []})
        new_archives = []
        for row in archives['added']:
            row = dict(row)
            row['sport_association_documents_archive_id'] = label(str(row['sport_association_documents_archive_id']), 'archive')
            row['document_id'] = label(str(row['document_id']), 'document')
            assert row['date'] in (since.date(), until.date())
            row['date'] = 'run'
            new_archives.append(row)
        entries = out.pop('auditlog.LogEntry', {'added': [], 'removed': []})
        audit = []
        for row in entries['added']:
            row = dict(row)
            row.pop('id')
            row['timestamp'] = window(row['timestamp'])
            for key in ('object_pk', 'object_repr'):
                for value in re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', row[key] or ''):
                    if UUID(value) not in (E_OLD, E_NEW1, E_NEW2, E_NEW3) and value not in [str(u) for u in (U(190), U(191), U(192), U(193))]:
                        row[key] = row[key].replace(value, label(value, 'new'))
            changes = row['changes']
            if isinstance(changes, str):
                changes = json.loads(changes)
            normalized = {}
            for field, pair in changes.items():
                if field == 'file_size_bytes' and pair[0] == 'None' and pair[1].isdigit():
                    normalized[field] = ['None', 'archive-size']
                    continue
                values = []
                for value in pair:
                    if isinstance(value, str):
                        if ARCHIVE_NAME.search(value):
                            value = ARCHIVE_NAME.sub(lambda m: 'export_' + m.group(1) + '_<ts>.zip', value)
                            value = re.sub(r'exports/\d{8}_\d{6}/', 'exports/<ts>/', value)
                        if re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?(\+00:00)?', value) and field in (
                                'creation_date', 'date'):
                            value = 'run'
                        for found in re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value):
                            if found in labels or field in ('document_id', 'token', 'sport_association_documents_archive_id',
                                                            'document', 'filepath'):
                                if UUID(found) in (E_OLD, E_NEW1, E_NEW2, E_NEW3) or found in [str(U(i)) for i in range(190, 194)]:
                                    continue
                                value = value.replace(found, label(found, 'new') if found not in labels else labels[found])
                    values.append(value)
                normalized[field] = values
            row['changes'] = normalized
            row['additional_data'] = row.get('additional_data')
            audit.append(row)
        audit.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        index = out.pop('application.AuditLogIndex', {'added': [], 'removed': []})
        index_rows = []
        for row in index['added']:
            row = dict(row)
            UUID(str(row.pop('audit_log_index_id')))
            row.pop('log_entry_id')
            row['created_at'] = window(row['created_at'])
            index_rows.append(row)
        index_rows.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        observed = {'task_results': rows, 'documents': {'added': new_documents, 'removed': documents['removed']},
                    'archives': {'added': new_archives, 'removed': archives['removed']}, 'audit': audit,
                    'audit_removed': entries['removed'], 'audit_index': index_rows, 'other_models': out}
        observed['cache'] = self.cache_state(labels, since, until)
        observed['broker'] = self.broker_observed(original, labels, since, until)
        observed['bucket'] = self.bucket(labels, since, until)
        return observed

    @staticmethod
    def archive_name(name, since, until):
        match = ARCHIVE_NAME.fullmatch(name)
        assert match, name
        stamp = datetime.strptime(match.group(2), '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
        assert since.replace(microsecond=0) <= stamp <= until, name
        return f'export_{match.group(1)}_<ts>.zip'

    def cache_state(self, labels, since, until):
        state = {}
        for key in sorted(self.stack.cache.keys('*')):
            raw = self.stack.cache.get(key)
            value = pickle.loads(raw)
            ttl = self.stack.cache.ttl(key)
            if isinstance(value, dict) and value.get('updated_at'):
                stamp = datetime.fromisoformat(value['updated_at'])
                assert since <= stamp <= until, value['updated_at']
                value['updated_at'] = 'run'
                value['task_id'] = labels.get(value['task_id'], value['task_id'])
                document = value.get('document')
                if document:
                    document['document_id'] = labels.get(document['document_id'], document['document_id'])
                    document['filename'] = self.archive_name(document['filename'], since, until)
            state[key.decode()] = {'value': value, 'ttl': 'day' if 86000 < ttl <= 86400 else '2h' if 7000 < ttl <= 7200 else ttl}
        return state

    def broker_observed(self, original, labels, since, until):
        broker = {}
        for key, (kind, value) in self.stack.broker_state().items():
            if kind == 'list':
                value = [self.normalize_message(item, original, labels, since, until) for item in value]
            broker[key] = [kind, value]
        return broker

    def normalize_message(self, raw, original, labels, since, until):
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        if headers['id'] == original['headers']['id']:
            if raw == json.dumps(original):
                return {'original': True}
            # The maintenance deferral: the same command with eta +15 s, marked redelivered when restored at shutdown.
            assert headers['retries'] == original['headers']['retries'] and headers['assozeta_published_at'] == \
                original['headers']['assozeta_published_at'], headers
            offset = datetime.fromisoformat(headers['eta']).timestamp()
            assert since.timestamp() + 14 <= offset <= until.timestamp() + 16, headers['eta']
            headers['eta'] = 'deferred+15s'
            UUID(properties['delivery_tag'])
            properties['delivery_tag'] = '<tag>'
            headers['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', headers['origin']) else headers['origin']
            properties['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', properties.get('origin') or '') else properties.get('origin')
            message['body'] = json.loads(base64.b64decode(message['body']))
            return message
        UUID(headers['id'])
        assert headers['root_id'] == original['headers']['id'] and headers['parent_id'] == original['headers']['id'], headers
        headers['id'] = labels.get(headers['id'], '<unlabelled-child>')
        headers['root_id'] = headers['parent_id'] = 'task'
        properties['correlation_id'] = headers['id']
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = '<tag>'
        assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
        headers['origin'] = '<origin>'
        UUID(properties['reply_to'])
        properties['reply_to'] = '<reply-to>'
        stamp = headers.pop('assozeta_published_at')
        assert since.timestamp() <= stamp <= until.timestamp(), stamp
        body = json.loads(base64.b64decode(message['body']))
        text = body[1].get('message', '')
        body[1]['message'] = ARCHIVE_NAME.sub(lambda m: 'export_' + m.group(1) + '_<ts>.zip', text)
        headers['kwargsrepr'] = ARCHIVE_NAME.sub(lambda m: 'export_' + m.group(1) + '_<ts>.zip', headers['kwargsrepr'])
        message['body'] = body
        return message

    def bucket(self, labels, since, until):
        objects = {}
        for item in self.stack.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            key = item['Key']
            head = self.stack.s3.head_object(Bucket=BUCKET, Key=key)
            body = self.stack.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
            entry = {k: head.get(k) for k in ('ContentType', 'ContentEncoding', 'CacheControl', 'Metadata')}
            match = re.fullmatch(LOCATION + r'/exports/(\d{8}_\d{6})/([0-9a-f-]{36})/(export_.*\.zip)', key)
            if match:
                key = f'{LOCATION}/exports/<ts>/{labels.get(match.group(2), "<unlabelled>")}/{self.archive_name(match.group(3), since, until)}'
                entry['zip'] = describe_zip(body, since, until)
            else:
                entry['sha256'] = hashlib.sha256(body).hexdigest()
            objects[key] = entry
        return objects

    # -- scenarios ----------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        stack.seed(scenario)
        payload = stack.publish(scenario)
        task_id = json.loads(payload)['headers']['id']
        stack.seed(scenario)
        self.before = stack.snapshot()
        since = datetime.now(timezone.utc)
        self.run_django(scenario, payload, task_id)
        django = self.observe(payload, since, datetime.now(timezone.utc))
        self.authored(scenario, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return
        stack.seed(scenario)
        again = stack.snapshot()
        if again != self.before:
            diff = {k: {'before': self.before.get(k), 'again': v} for k, v in again.items() if v != self.before.get(k)}
            (OUTPUT / 'seed-difference.json').write_text(json.dumps(diff, indent=1, default=str) + '\n')
        assert again == self.before, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        self.run_go(scenario, payload, task_id, scenario.handoff)
        if scenario.handoff:
            handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
            expected = json.loads(payload)
            expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
            assert len(handed) == 1 and handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes'
            assert stack.snapshot() == self.before, 'handoff has no effects'
            # The handed-off bytes differ from the original only in the routing key (checked above). A Django worker on
            # ``celery`` would also consume the send_mail_async child the export publishes there, so the original
            # message runs on Django's worker from the routed queue, as in the reference run.
            self.run_django(scenario, payload, task_id)
        go = self.observe(payload, since, datetime.now(timezone.utc))
        if scenario.handoff:
            for observation in (django, go):
                observation['broker'] = {k: v for k, v in observation['broker'].items() if not k.startswith('_kombu.binding.')}
        self.observations[scenario.name] = {'django': django, 'go': go}
        if go != django:
            (OUTPUT / 'difference.json').write_text(json.dumps({'scenario': scenario.name, 'django': django, 'go': go},
                                                               indent=1, default=str, ensure_ascii=False) + '\n')
        assert go == django, 'Go differs from Django: ' + scenario.name + difference_summary(django, go)
        self.completed.append(scenario.name)
        print('PASS: ' + scenario.name, flush=True)

    def authored(self, scenario, observed):
        expect = scenario.expect
        rows = observed['task_results']
        assert len(rows) == 1, rows
        row = rows[0]
        if 'status' in expect:
            assert row['status'] == expect['status'], row
            return
        assert row['status'] == 'SUCCESS', row
        result = row['result']
        assert result['success'] is expect['success'], result
        mails = [m for m in observed['broker'].get(DJANGO_QUEUE, ['list', []])[1] if isinstance(m, dict) and 'headers' in m]
        if not expect['success']:
            assert result['error'] == expect['error'], result
            assert len(mails) == expect['mails'], mails
            return
        assert len(mails) == 1 and mails[0]['headers']['task'] == 'core.tasks.send_mail_async', mails
        archive = [v for k, v in observed['bucket'].items() if 'zip' in v]
        assert len(archive) == 1, observed['bucket']
        members = [e['filename'] for e in archive[0]['zip']['entries']]
        assert 'manifest.json' in members and 'data/01_sport_association.json' in members, members
        assert len([m for m in members if m.startswith('files/')]) == expect['files'], members
        assert len(observed['documents']['removed']) == expect['deleted_exports'], observed['documents']
        if expect.get('own_records'):
            records = {}
            created = [item['Key'] for item in self.stack.s3.list_objects_v2(Bucket=BUCKET).get('Contents', [])
                       if re.fullmatch(LOCATION + r'/exports/\d{8}_\d{6}/[0-9a-f-]{36}/export_.*\.zip', item['Key'])]
            assert len(created) == 1, created
            body = self.stack.s3.get_object(Bucket=BUCKET, Key=created[0])['Body'].read()
            with zipfile.ZipFile(io.BytesIO(body)) as exported:
                for name in exported.namelist():
                    if name.startswith('data/'):
                        member = exported.read(name)
                        assert SMTP_SECRET.encode() not in member, name
                        records[name] = json.loads(member)

            def keys(member, key):
                return {r[key] for r in records[member]}
            assert keys('data/01_sport_association.json', 'sport_association_id') == {str(ASSOCIATION)}
            config = records['data/21_communication_config.json']
            assert len(config) == 1 and 'email_smtp_password' not in config[0] and config[0]['email_smtp_user'], config
            assert keys('data/23_instructors.json', 'instructor_id') == {str(U(221))}
            assert keys('data/05_collaboration_invites.json', 'collaboration_invite_id') == {str(U(231))}
            assert keys('data/60_module_responses.json', 'module_response_id') == {str(U(251))}
            assert keys('data/42_signatures.json', 'signature_id') == {str(U(261))}
            assert keys('data/04_users_onboarding.json', 'users_onboarding_id') == {str(U(271))}
            assert str(COLLABORATOR) in keys('data/02_users.json', 'user_id')
        last = [v for k, v in observed['cache'].items() if 'association-export-last' in k]
        assert len(last) == 1 and last[0]['value']['status'] == 'SUCCESS', observed['cache']
        assert not [k for k in observed['cache'] if 'association-export-active' in k], observed['cache']


def describe_zip(data, since, until):
    archive = zipfile.ZipFile(io.BytesIO(data))
    entries = []
    for info in archive.infolist():
        offset = info.header_offset
        assert data[offset:offset + 4] == b'PK\x03\x04'
        names, extra = struct.unpack('<HH', data[offset + 26:offset + 30])
        local = bytearray(data[offset:offset + 30 + names + extra])
        local[10:14] = b'\0\0\0\0'
        stamp = datetime(*info.date_time, tzinfo=timezone.utc)
        assert since.replace(microsecond=0) - timedelta(seconds=2) <= stamp <= until, info.date_time
        body = archive.open(info).read()
        entry = {'filename': info.filename, 'compress_type': info.compress_type, 'flag_bits': info.flag_bits,
                 'create_system': info.create_system, 'create_version': info.create_version, 'extract_version': info.extract_version,
                 'reserved': info.reserved, 'volume': info.volume, 'internal_attr': info.internal_attr,
                 'external_attr': info.external_attr, 'extra': info.extra.hex(), 'comment': info.comment.hex()}
        if info.filename == 'manifest.json':
            manifest = json.loads(body)
            stamp = datetime.fromisoformat(manifest['export_date'])
            assert since <= stamp <= until, manifest['export_date']
            manifest['export_date'] = 'run'
            entry['manifest'] = manifest
            entry['indent'] = body.decode().replace(json.loads(body)['export_date'], 'run')
        else:
            local[14:18] = b'\0\0\0\0' if False else local[14:18]
            entry.update(CRC=info.CRC, file_size=info.file_size, compress_size=info.compress_size, local_header=local.hex(),
                         compressed=hashlib.sha256(data[offset + 30 + names + extra:offset + 30 + names + extra + info.compress_size]).hexdigest(),
                         data=base64.b64encode(body).decode() if len(body) < 4096 else hashlib.sha256(body).hexdigest())
        entries.append(entry)
    end = data.rfind(b'PK\x05\x06')
    record = bytearray(data[end:])
    record[16:20] = b'\0' * 4  # central directory offset (follows manifest.json's compressed size)
    return {'entries': entries, 'comment': archive.comment.hex(), 'end_record': record.hex()}


@contextlib.contextmanager
def private_minio(log, env, report):
    from TESTS.adapters.django.instance_logo import minio
    with minio(log, env, report) as (endpoint, access, secret):
        yield {'endpoint': endpoint, 'access': access, 'secret': secret}


def main():
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
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
    selected = [s for s in SCENARIOS if not ONLY or ONLY in s.name]
    assert selected, 'no scenario selected'
    harness = None
    with tempfile.TemporaryDirectory(prefix='az-consumer-export-', dir='/tmp') as directory, \
            (OUTPUT / 'services.log').open('w') as log, private_minio(log, docker_env, result) as storage:
        work = Path(directory)
        attempted = False
        redis_process = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            # The Go plan must be the one Django generates now (fields, queries, exclusions).
            plan = ROOT / 'BE_GO/internal/associationexport/plan.json'
            if not plan.is_file() or plan.stat().st_size == 0:
                raise RuntimeError('missing export plan')
            subprocess.run([sys.executable, '-I', str(ROOT / 'TESTS/adapters/go/association_export_plan.py'), '--check', str(plan)],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            result['plan_current'] = True
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass',
                                              REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                                             stdout=log, stderr=subprocess.STDOUT)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            os.environ.update(WHITELABEL_NAME=BRAND, EMAIL_HOST_USER=HOST_USER)
            stack = Stack(work, environment, redis_port, storage)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in selected:
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
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate()
                        redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    assert harness and harness.completed == [s.name for s in selected], 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_export_process.py', 'TESTS/adapters/go/association_export_plan.py',
             'TESTS/adapters/go/consumer_restore_settings.py', 'TESTS/adapters/go/consumer_restore_worker.py',
             'BE/application/tasks.py', 'BE/application/export_progress.py', 'BE/application/services/export_service.py',
             'BE/application/services/validators.py', 'BE_GO/internal/associationexport/plan.json']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/',
                                                        'internal/associationexport/', 'internal/pyzip/', 'internal/pyzlib/',
                                                        'consumer_export_association', 'consumer_restore_')))
    result.update(success=True, partial=bool(ONLY), django_only=DJANGO_ONLY, race_instrumented=not DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope')}, default=str))


if __name__ == '__main__':
    main()
