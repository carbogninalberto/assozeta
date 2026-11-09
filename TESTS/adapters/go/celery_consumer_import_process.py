"""Differential harness for the Go Celery consumer: import_association_data.

Private PostgreSQL (actual Django current schema), a private Redis (broker DB 0, Django cache DB 1) and a private MinIO
(the production S3Boto3Storage). The fixture archives are made by Django itself: a source association is seeded and
AssociationExportService.export() writes the archive; the variants (truncated, without manifest or association file,
circular folders, an undecodable data file, a foreign-key value the database rejects, a media member with a wrong
CRC, an owner without an archived password hash, course subscriptions) are rewritten from it with zipfile.

Each scenario seeds the target database and bucket (the archive uploaded where the API stores it), builds one message
with Django's own producer, routed by GO_TASK_QUEUE_TASKS to ``assozeta-go``, and runs the identical bytes once on a
real Django worker subprocess (prefork, ``-Q assozeta-go``, django-db results, CoordinatedTask, production cache and
S3 storage, Django's default password hashers) and, after re-seeding, once on the race-built Go consumer.

Compared after each worker stopped: the complete managed-model state including the auto-created many-to-many through
tables (every row of every table, serial ids included, except LogEntry/AuditLogIndex keys), every auditlog entry
(object, repr, action, changes) and AuditLogIndex row, the TaskResult (status, result, meta), every broker key and
every bucket object (key, ContentType, ContentEncoding, CacheControl, Metadata, SHA-256 of the body). Values that
differ per run by construction are normalized after a shape check: timestamps inside the run window, the
``%Y%m%d_%H%M%S`` storage stamps, the staged temporary file name, random tokens of documents created from media
(labelled), unusable passwords (``!`` + 40 characters) and the owner's recovery hash (verified with check_password).

Authored assertions check that the Django oracle took the intended path. Handoff scenarios require Go to move the
message unchanged to Django's ``celery`` queue with no effect; the original message then runs on Django's worker.
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
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
from TESTS.adapters.django.production_runtime import require_production_python  # noqa: E402
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema  # noqa: E402
from TESTS.adapters.go.email_tasks_worker_process import free_port  # noqa: E402

DATABASE = 'assozeta_contract_consumer_import'
REDIS_PASSWORD = 'private-redis-password-canary'
GO_QUEUE, DJANGO_QUEUE = 'assozeta-go', 'celery'
TASK = 'import_association_data'
BUCKET, LOCATION = 'consumer-import', 'private'
OUTPUT = ROOT / os.environ.get('CONSUMER_IMPORT_OUTPUT', 'quality-reports/go-migration/consumer-import-process')
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
ONLY = os.environ.get('CELERY_CONSUMER_ONLY', '')
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PBKDF2 = 'pbkdf2_sha256$1000000$fixturesalt$' + base64.b64encode(hashlib.sha256(b'fixture').digest()).decode()
RECOVERY = 'Recupero-Fixture-2026!'
SOURCE_CREATED = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
STAMP = re.compile(r'(?<!\d)(\d{8}_\d{6})(?!\d)')
UUID_TEXT = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
STAGED = re.compile(r'/[^\'"\]]*/tmp[a-z0-9_]{8}\.zip')
NAIVE = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{6})?')


def U(n):
    return UUID(int=0xC8000000 + n)


OWNER, COLLABORATOR, ATHLETE, OTHER_OWNER = U(1), U(2), U(3), U(5)
ASSOCIATION, OTHER = U(11), U(12)
D_PATH, D_MEDICAL, D_INVOICE, D_ATTACHMENT, D_PLAIN = (U(n) for n in range(21, 26))
GLOBAL_CATEGORY = U(130)
TAG_A, TAG_B = U(81), U(82)


class Scenario:
    def __init__(self, name, archive, *, target='empty', kwargs=None, handoff=False, expect=None, send_task=False,
                 args=None):
        self.name, self.archive, self.target = name, archive, target
        self.kwargs = kwargs or {}
        self.args = args
        self.handoff, self.expect, self.send_task = handoff, expect or {}, send_task


SCENARIOS = [
    Scenario('an export archive is imported into an instance with other data', 'rich',
             expect={'success': True, 'files': 5}),
    Scenario('an existing owner account without an association is reused', 'rich', target='owner-account',
             expect={'success': True, 'files': 5}),
    Scenario('an owner without an archived hash gets the recovery password', 'no-owner-hash',
             kwargs={'owner_password': RECOVERY}, expect={'success': True, 'files': 5}),
    Scenario('an existing association fails validation', 'rich', target='association-exists',
             expect={'success': False, 'error': f"Validazione fallita: ['SportAssociation UUID collision: {ASSOCIATION} "
                                                "already exists']"}),
    Scenario('an owner without a hash or recovery password fails validation', 'no-owner-hash',
             expect={'success': False, 'error': "Validazione fallita: ['Owner recovery password is required because the "
                                                "archive does not contain a supported usable password hash']"}),
    Scenario('a truncated archive is not a ZIP file', 'not-zip',
             expect={'success': False, 'error': "Validazione fallita: ['Not a valid ZIP file: <staged>']"}),
    Scenario('an archive without manifest fails validation', 'no-manifest',
             expect={'success': False, 'error': "Validazione fallita: ['Missing manifest.json in export file']"}),
    Scenario('an archive without the association file fails validation', 'no-association',
             expect={'success': False, 'error': "Validazione fallita: ['Missing required data file: "
                                                "01_sport_association.json']"}),
    Scenario('circular folder parents roll the import back', 'circular-folders',
             expect={'success': False, 'error': 'Folder hierarchy has missing or circular parents: 1, 2, 3'}),
    Scenario('an existing tag key rolls the import back', 'rich', target='tag-exists',
             expect={'success': False, 'error': f'Tags UUID collision: {TAG_A} already exists. Only User rows may be '
                                                'reused by exact UUID during import.'}),
    Scenario('an undecodable data file is handed off', 'bad-json', handoff=True, expect={'success': False}),
    Scenario('course subscriptions are handed off', 'course-subscription', handoff=True, expect={'success': True, 'files': 5}),
    Scenario('a key the database rejects at the constraint check is handed off', 'fk-violation', handoff=True,
             expect={'success': False}),
    Scenario('a media member with a wrong CRC is handed off', 'bad-crc', handoff=True, expect={'success': False}),
    Scenario('a missing staged archive is handed off', None, handoff=True, expect={'success': False}),
    Scenario('a pending restore defers the import by 15 s', 'rich', target='restore-pending', expect={'status': 'RETRY'}),
    Scenario('an unknown keyword argument is handed off', 'rich', kwargs={'extra': 1}, handoff=True, send_task=True,
             expect={'status': 'FAILURE'}),
]


# -- fixture archives ------------------------------------------------------------------------------
MEDIA = {
    'documents/tessera.pdf': PDF + b'tessera',
    'medical/certificato.pdf': PDF + b'certificato',
    'invoices/fattura-1.pdf': PDF + b'fattura',
    'attachments/allegato.txt': ('riga di prova\n' * 500).encode(),
    'signatures/firma-socio.png': b'\x89PNG\r\n\x1a\nfirma',
}


def truncate():
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT string_agg(quote_ident(tablename), \', \') FROM pg_tables WHERE schemaname = \'public\' '
                       "AND tablename NOT IN ('django_migrations', 'django_content_type', 'auth_permission')")
        cursor.execute(f'TRUNCATE {cursor.fetchone()[0]} RESTART IDENTITY CASCADE')


def seed_source(course_subscription=False, owner_hash=True):
    """The source association the fixture archives are exported from."""
    from django.conf import settings
    from application.models import (User, SportAssociation, Group, Associate, Subscription, Tags, Payment,
                                    PaymentCategory, VatManagement, MedicalCertificate, Folder, Course, CourseLocation,
                                    Reminder, Family, AssociateTutorRelation, Instructor, Signature)
    from application.models.invoices_models import Invoice
    from communications.models import Message, MessageTransaction
    from docmanager.models import Document
    truncate()
    users = [User(user_id=OWNER, username='import-owner', role=1, password=PBKDF2 if owner_hash else '!unusable',
                  date_joined=SOURCE_CREATED, email='owner@example.test', first_name='Anna', last_name='Rossi'),
             User(user_id=COLLABORATOR, username='import-collaborator', role=1, password='!unusable',
                  date_joined=SOURCE_CREATED, email='collab@example.test'),
             User(user_id=ATHLETE, username='import-athlete', role=2, password=PBKDF2, date_joined=SOURCE_CREATED,
                  email='atleta@example.test', first_name='Luca', last_name='Bianchi')]
    User.original_objects.bulk_create(users)
    User.original_objects.filter(user_id=COLLABORATOR).update(connected_user_id=OWNER)
    SportAssociation.original_objects.bulk_create([SportAssociation(
        sport_association_id=ASSOCIATION, user_id=OWNER, denomination='ASD Importa «Uno»', tax_code='01234567890')])
    Group.objects.bulk_create([Group(group_id=U(41), name='Gruppo A', description='primo', sport_association_id=ASSOCIATION)])
    Document.objects.bulk_create([
        Document(document_id=D_PATH, creation_date=SOURCE_CREATED, filepath='documents/tessera.pdf', filename='tessera.pdf',
                 token=U(51), file_size_bytes=10),
        Document(document_id=D_MEDICAL, creation_date=SOURCE_CREATED, filepath='medical/certificato.pdf',
                 filename='certificato.pdf', token=U(54)),
        Document(document_id=D_INVOICE, creation_date=SOURCE_CREATED, filepath='invoices/fattura-1.pdf',
                 filename='fattura-1.pdf', token=U(55)),
        Document(document_id=D_ATTACHMENT, creation_date=SOURCE_CREATED, filepath='attachments/allegato.txt',
                 filename='allegato.txt', token=U(56)),
    ])
    Family.objects.bulk_create([Family(family_id=U(61), type='nucleo')])
    Associate.objects.bulk_create([
        Associate(associate_id=U(71), user_id=ATHLETE, sport_association_id=ASSOCIATION, first_name='Luca',
                  last_name='Bianchi', born_date=date(2001, 2, 3), email='atleta@example.test', group_id=U(41),
                  creation_date=SOURCE_CREATED, family_id=U(61), notes='nota "virgolettata"\nseconda riga\ttab'),
        Associate(associate_id=U(72), sport_association_id=ASSOCIATION, first_name='Genitore', last_name='Bianchi',
                  creation_date=SOURCE_CREATED, family_id=U(61)),
        Associate(associate_id=U(73), sport_association_id=ASSOCIATION, first_name='Cancellato', last_name='Socio',
                  deleted=True, creation_date=SOURCE_CREATED)])
    AssociateTutorRelation.objects.bulk_create([AssociateTutorRelation(id=7, associate_id=U(71), tutor_id=U(72),
                                                                       is_primary=True)])
    Tags.objects.bulk_create([Tags(tag_id=TAG_A, tag_name='agonisti', sport_association_id=ASSOCIATION,
                                   creation_date=SOURCE_CREATED),
                              Tags(tag_id=TAG_B, tag_name='ünder', sport_association_id=ASSOCIATION,
                                   creation_date=SOURCE_CREATED)])
    MedicalCertificate.objects.bulk_create([MedicalCertificate(medical_id=U(91), document_id=D_MEDICAL, user_id=ATHLETE,
                                                               creation_date=SOURCE_CREATED,
                                                               expiration_date=date(2027, 1, 1))])
    Subscription.objects.bulk_create([Subscription(
        subscription_id=U(101), sport_association_id=ASSOCIATION, associate_id=U(71), user_id=ATHLETE, medical_id=U(91),
        creation_date=SOURCE_CREATED, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        custom_data={'zeta': 1, 'alfa': [1.5, 2, None, True], 'testo': 'àèì', 'big': 12345678901234567890},
        meta={'k': {'nested': 0.1}}, signature_storage_key='signatures/firma-socio.png',
        signature_url='https://cdn.example.test/old-signature.png')])
    Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=U(101), tags_id=TAG_B),
                                                   Subscription.tags.through(subscription_id=U(101), tags_id=TAG_A)])
    VatManagement.objects.bulk_create([VatManagement(vat_management_id=U(111), vat=22)])
    PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(121), sport_association_id=ASSOCIATION,
                                                         name='Quote', vat_management_id=U(111),
                                                         creation_date=SOURCE_CREATED)])
    Signature.objects.bulk_create([Signature(signature_id=U(125), user_id=ATHLETE, signature='firma')])
    Invoice.objects.bulk_create([Invoice(invoice_id=U(141), sport_association_id=ASSOCIATION, document_pdf_id=D_INVOICE,
                                         membership_fee=Decimal('25.00'), activity_fee=Decimal('0.50'), number=7)])
    Payment.objects.bulk_create([
        Payment(payment_id=U(131), type='quota', subject=1, amount=Decimal('10.50'), creation_date=SOURCE_CREATED,
                payment_date=SOURCE_CREATED + timedelta(days=1), sport_association_id=ASSOCIATION, user_id=ATHLETE,
                associate_id=U(71), payment_category_id=U(121), meta={'via': 'bonifico'}, course={'id': None},
                description='Quota annuale', invoice_id=U(141), signature_id=U(125)),
        Payment(payment_id=U(132), type='quota', subject=1, amount=Decimal('0.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, deleted=True, description=None),
        Payment(payment_id=U(133), type='quota', subject=2, amount=Decimal('35.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, description='quota CORSO',
                meta={'description': 'Nuoto, primo trimestre'}),
        Payment(payment_id=U(134), type='quota', subject=2, amount=Decimal('12.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, description=None, course={'label': 'Nuoto'}),
        Payment(payment_id=U(135), type='quota', subject=0, amount=Decimal('5.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, description=None)])
    Payment.attachments.through.objects.bulk_create([Payment.attachments.through(payment_id=U(131), document_id=D_ATTACHMENT),
                                                     Payment.attachments.through(payment_id=U(131), document_id=D_PATH)])
    root = Folder.objects.create(name='Radice', sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    child = Folder.objects.create(name='Figlia', parent=root, sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    Folder.objects.create(name='Nipote', parent=child, sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    Folder.objects.create(name='Seconda radice', sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    Reminder.objects.bulk_create([Reminder(reminder_id=U(151), sport_association_id=ASSOCIATION, reminder_type=1, frequency=1,
                                           name='Promemoria', next_occurrence=date(2026, 3, 1),
                                           time_occurrence=datetime(2026, 1, 1, 9, 30, 15).time(), recipients=['a', 'b'])])
    CourseLocation.objects.bulk_create([CourseLocation(course_location_id=U(161), title='Palestra',
                                                       sport_association_id=ASSOCIATION)])
    Course.objects.bulk_create([Course(course_id=U(171), sport_association_id=ASSOCIATION, title='Nuoto', fee=Decimal('100.00'),
                                       google_background_color='#123456', creation_date=SOURCE_CREATED, events=[{'d': 1}])])
    Course.locations.through.objects.bulk_create([Course.locations.through(course_id=U(171), courselocation_id=U(161))])
    Instructor.objects.bulk_create([Instructor(instructor_id=U(181), user_id=OWNER, first_name='Marco', last_name='Verdi',
                                               default_hourly_billing=Decimal('15.00'))])
    Instructor.documents.through.objects.bulk_create([Instructor.documents.through(instructor_id=U(181), document_id=D_PATH)])
    Message.objects.bulk_create([Message(message_id=U(191), sport_association_id=ASSOCIATION, type='email', message='Ciao',
                                         subject='Benvenuto', created_at=SOURCE_CREATED)])
    MessageTransaction.objects.bulk_create([MessageTransaction(message_transaction_id=U(192), message_id=U(191),
                                                               recipient='atleta@example.test', sent_on=SOURCE_CREATED)])
    if course_subscription:
        from application.models import CourseSubscription
        CourseSubscription.objects.bulk_create([CourseSubscription(course_subscription_id=U(201), course_id=U(171),
                                                                   subscription_id=U(101), creation_date=SOURCE_CREATED)])
    media = Path(settings.MEDIA_ROOT)
    if media.exists():
        shutil.rmtree(media)
    for name, body in MEDIA.items():
        (media / name).parent.mkdir(parents=True, exist_ok=True)
        (media / name).write_bytes(body)


def export_archive():
    from django.core.files.storage import default_storage
    from application.services.export_service import AssociationExportService
    document = AssociationExportService(ASSOCIATION).export()
    with default_storage.open(document.filepath, 'rb') as stream:
        return stream.read()


def rewrite(data, change):
    """A copy of the archive with ``change(name, body)`` applied to every member (None drops it)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            body = change(info.filename, source.read(info))
            if body is not None:
                target.writestr(info.filename, body)
    return out.getvalue()


def edit_records(data, member, edit):
    def change(name, body):
        if name != member:
            return body
        records = json.loads(body)
        edit(records)
        return json.dumps(records, ensure_ascii=False).encode()
    return rewrite(data, change)


def corrupt_crc(data, member):
    archive = zipfile.ZipFile(io.BytesIO(data))
    info = archive.getinfo(member)
    raw = bytearray(data)
    wrong = (info.CRC ^ 0x5A5A5A5A).to_bytes(4, 'little')
    raw[info.header_offset + 14:info.header_offset + 18] = wrong
    position = raw.find(b'PK\x01\x02')
    while position >= 0:
        names = int.from_bytes(raw[position + 28:position + 30], 'little')
        if raw[position + 46:position + 46 + names].decode() == member:
            raw[position + 16:position + 20] = wrong
            break
        position = raw.find(b'PK\x01\x02', position + 4)
    assert position >= 0, member
    return bytes(raw)


def build_archives():
    seed_source()
    rich = export_archive()
    members = zipfile.ZipFile(io.BytesIO(rich)).namelist()
    media = [m for m in members if m.startswith('files/')]
    assert len(media) == 5, members
    archives = {'rich': rich}
    archives['not-zip'] = rich[:len(rich) // 2]
    archives['no-manifest'] = rewrite(rich, lambda name, body: None if name == 'manifest.json' else body)
    archives['no-association'] = rewrite(rich, lambda name, body: None if name == 'data/01_sport_association.json' else body)

    def circular(records):
        by_name = {r['name']: r for r in records}
        by_name['Radice']['parent_id'] = by_name['Figlia']['id']
    archives['circular-folders'] = edit_records(rich, 'data/14_folders.json', circular)
    archives['bad-json'] = rewrite(rich, lambda name, body: b'[{"tag_id": ' if name == 'data/12_tags.json' else body)

    def violation(records):
        for record in records:
            if record['payment_id'] == str(U(131)):
                record['associate_id'] = str(TAG_A)
    fk = rewrite(rich, lambda name, body: None if name == 'data/14_folders.json' else body)
    archives['fk-violation'] = edit_records(fk, 'data/43_payments.json', violation)
    archives['bad-crc'] = corrupt_crc(rich, media[-1])
    seed_source(owner_hash=False)
    archives['no-owner-hash'] = export_archive()
    assert '"password"' not in [json.dumps(u) for u in json.loads(zipfile.ZipFile(
        io.BytesIO(archives['no-owner-hash'])).read('data/02_users.json')) if u['user_id'] == str(OWNER)][0]
    seed_source(course_subscription=True)
    archives['course-subscription'] = export_archive()
    truncate()
    return archives


# -- stack -----------------------------------------------------------------------------------------
class Stack:
    def __init__(self, work, environment, redis_port, storage, archives):
        import redis
        import boto3
        from botocore.config import Config
        self.work, self.env, self.redis_port, self.storage, self.archives = work, environment, redis_port, storage, archives
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

    def seed(self, scenario):
        from application.models import User, SportAssociation, PaymentCategory, Tags
        started = datetime.now(timezone.utc)
        truncate()
        User.original_objects.bulk_create([
            User(user_id=OTHER_OWNER, username='other-owner', role=1, password=PBKDF2, date_joined=SOURCE_CREATED,
                 email='other@example.test'),
            # The archive's athlete already has an account here, without a usable password.
            User(user_id=ATHLETE, username='existing-athlete', role=2, password='!existing', date_joined=SOURCE_CREATED,
                 email='existing-athlete@example.test', first_name='Esistente')])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=OTHER, user_id=OTHER_OWNER,
                                                                        denomination='Altra', tax_code='00000000000')])
        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=GLOBAL_CATEGORY, name='Entrate e proventi '
                                                             'da attività tipiche', creation_date=SOURCE_CREATED)])
        if scenario.target == 'owner-account':
            User.original_objects.bulk_create([User(user_id=OWNER, username='owner-account', role=1, password='!reset',
                                                    date_joined=SOURCE_CREATED, email='owner-account@example.test')])
        if scenario.target == 'association-exists':
            User.original_objects.bulk_create([User(user_id=OWNER, username='owner-account', role=1, password=PBKDF2,
                                                    date_joined=SOURCE_CREATED, email='owner-account@example.test')])
            SportAssociation.original_objects.bulk_create([SportAssociation(
                sport_association_id=ASSOCIATION, user_id=OWNER, denomination='Già presente', tax_code='11111111111')])
        if scenario.target == 'tag-exists':
            Tags.objects.bulk_create([Tags(tag_id=TAG_A, tag_name='altro', sport_association_id=OTHER,
                                           creation_date=SOURCE_CREATED)])
        if scenario.target == 'restore-pending':
            from instance.models import DataRestore
            DataRestore._base_manager.bulk_create([DataRestore(id=U(301), owner_id=OTHER_OWNER, association_id=OTHER,
                                                               state='running', sha256='0' * 64)])
        self.fix_defaults(started)
        for item in self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            self.s3.delete_object(Bucket=BUCKET, Key=item['Key'])
        self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/unrelated/keep.txt', Body=b'keep',
                           ContentType='text/plain')
        if scenario.archive:
            self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{self.archive_name(scenario)}',
                               Body=self.archives[scenario.archive], ContentType='application/zip')
        self.cache.flushdb()
        self.seeding = (started, datetime.now(timezone.utc))

    @staticmethod
    def archive_name(scenario):
        return 'imports/' + re.sub(r'[^a-z0-9]+', '-', scenario.name.lower()).strip('-') + '.zip'

    @staticmethod
    def fix_defaults(started):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
              WHERE table_schema = 'public' AND data_type IN ('timestamp with time zone', 'date')
              AND table_name NOT LIKE 'django_%%' AND table_name NOT LIKE 'auditlog_%%'""")
            for table, column, kind in cursor.fetchall():
                fixed = SOURCE_CREATED if kind != 'date' else SOURCE_CREATED.date()
                bound = started if kind != 'date' else started.date()
                cursor.execute(f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" >= %s', [fixed, bound])

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: [dict(row) for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}

    def publish(self, scenario):
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (self.broker_url, DJANGO_QUEUE, go_task_routes(TASK), 'django-db'))))
        _reset_celery_app(app)
        args = scenario.args if scenario.args is not None else [self.archive_name(scenario)]
        try:
            self.broker.flushdb()
            if scenario.send_task:
                app.send_task(TASK, args=args, kwargs=scenario.kwargs)
            else:
                app.tasks[TASK].apply_async(args=args, kwargs=scenario.kwargs)
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
    def wait(predicate, description, timeout=180):
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
                'AWS_STORAGE_BUCKET_NAME': BUCKET, 'AWS_S3_ENDPOINT_URL': self.stack.storage['endpoint'],
                'AWS_LOCATION': LOCATION}

    def run_django(self, scenario, payload, task_id, queue=GO_QUEUE, push=True):
        if push:
            self.stack.broker.flushdb()
            self.stack.broker.lpush(GO_QUEUE, payload)
        log = OUTPUT / f'django-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE='TESTS.adapters.go.consumer_import_settings',
                           CONSUMER_RESTORE_BROKER_URL=self.stack.broker_url, CONSUMER_RESTORE_PRODUCTION_SERVICES='1',
                           CONSUMER_RESTORE_REDIS_PORT=str(self.stack.redis_port),
                           CONSUMER_RESTORE_REDIS_PASSWORD=REDIS_PASSWORD,
                           PYTHONPATH=f'{ROOT / "BE"}:{ROOT}', OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES', **self.service_env())
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-P', 'prefork', '-c', '1',
                                    '-Q', queue, '-n', 'oracle@%h', '--without-heartbeat', '--without-gossip',
                                    '--without-mingle', '-l', 'WARNING'], cwd=ROOT / 'BE', env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            held = 1 if scenario.target == 'restore-pending' else 0
            self.wait(lambda: self.stack.quiescent(task_id, queue, held) or process.poll() is not None,
                      'Django worker: ' + scenario.name)
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
                          and self.stack.broker.llen(DJANGO_QUEUE) == 1 or process.poll() is not None,
                          'Go handoff: ' + scenario.name)
            else:
                held = 1 if scenario.target == 'restore-pending' else 0
                self.wait(lambda: self.stack.quiescent(task_id, held=held) or process.poll() is not None,
                          'Go consumer: ' + scenario.name)
            assert process.poll() is None, 'Go consumer exited: ' + log.read_text()[-3000:]
            time.sleep(0.5)
        finally:
            code = self.stop(process, stream)
        text = log.read_text()
        assert 'WARNING: DATA RACE' not in text, 'Go race detector: ' + scenario.name
        for private in (REDIS_PASSWORD, 'private-worker-password-canary', self.stack.storage['secret']):
            assert private not in text, 'Go consumer leaked private configuration'
        assert code == 0, f'Go consumer exit status {code}: {text[-3000:]}'

    # -- observation ---------------------------------------------------------------------------
    def observe(self, scenario, payload, since, until):
        from django.contrib.auth.hashers import check_password
        original = json.loads(payload)
        task_id = original['headers']['id']
        known = set(self.known)
        labels = {task_id: '<task>'}

        def label(text):
            if text in known:
                return text
            if text not in labels:
                labels[text] = f'<random-{len(labels)}>'
            return labels[text]

        def stamp(match):
            moment = datetime.strptime(match.group(1), '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
            assert since.replace(microsecond=0) - timedelta(seconds=1) <= moment <= until, match.group(1)
            return '<ts>'

        def text(value):
            value = STAMP.sub(stamp, value)
            if STAGED.search(value):
                value = STAGED.sub('<staged>', value)
            naive = NAIVE.fullmatch(value)
            if naive:
                moment = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
                if since - timedelta(seconds=1) <= moment <= until:
                    return 'run'
            if re.fullmatch(r'![A-Za-z0-9]{40}', value):
                return '<unusable-password>'
            if value.startswith('pbkdf2_sha256$1000000$') and value != PBKDF2:
                assert check_password(RECOVERY, value), 'owner recovery hash'
                assert re.fullmatch(r'pbkdf2_sha256\$1000000\$[A-Za-z0-9]{22}\$[A-Za-z0-9+/]{43}=', value), value
                return '<recovery-hash>'
            return UUID_TEXT.sub(lambda m: label(m.group(0)), value)

        def normalize(value):
            if isinstance(value, datetime):
                if since - timedelta(seconds=1) <= value <= until:
                    return 'run'
                return value.isoformat()
            if isinstance(value, UUID):
                return label(str(value))
            if isinstance(value, str):
                return text(value)
            if isinstance(value, dict):
                return {normalize(k) if isinstance(k, str) else k: normalize(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            if isinstance(value, (date, Decimal)):
                return str(value)
            return value
        state = self.stack.snapshot()
        before = self.before
        out = {}
        for model, rows in state.items():
            if rows == before.get(model):
                continue
            seeded = [json.dumps(r, sort_keys=True, default=str) for r in before.get(model, [])]
            current = [r for r in rows if json.dumps(r, sort_keys=True, default=str) not in seeded]
            removed = [r for r in before.get(model, []) if r not in rows]
            out[model] = {'removed': removed, 'added': current}
        results = out.pop('django_celery_results.TaskResult', {'added': [], 'removed': []})
        rows = []
        for row in results['added']:
            row = dict(row)
            row.pop('id')
            row['meta'] = json.loads(row['meta'])
            row['meta']['children'] = [[[labels.get(c, '<child>') if c != task_id else '<task>', rest], extra]
                                       for (c, rest), extra in row['meta']['children']]
            row['result'] = json.loads(row['result'])
            rows.append(normalize(row))
        entries = out.pop('auditlog.LogEntry', {'added': [], 'removed': []})
        audit = []
        for row in entries['added']:
            row = dict(row)
            row.pop('id')
            changes = row['changes']
            row['changes'] = json.loads(changes) if isinstance(changes, str) else changes
            audit.append(normalize(row))
        audit.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        index = out.pop('application.AuditLogIndex', {'added': [], 'removed': []})
        index_rows = []
        for row in index['added']:
            row = dict(row)
            UUID(str(row.pop('audit_log_index_id')))
            row.pop('log_entry_id')
            index_rows.append(normalize(row))
        index_rows.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        observed = {'task_results': rows, 'audit': audit, 'audit_removed': normalize(entries['removed']),
                    'audit_index': index_rows, 'models': normalize(out), 'broker': self.broker_observed(original, since, until),
                    'bucket': self.bucket(normalize), 'cache': sorted(k.decode() for k in self.stack.cache.keys('*'))}
        return observed

    def broker_observed(self, original, since, until):
        broker = {}
        for key, (kind, value) in self.stack.broker_state().items():
            if kind == 'list':
                value = [self.normalize_message(item, original, since, until) for item in value]
            broker[key] = [kind, value]
        return broker

    @staticmethod
    def normalize_message(raw, original, since, until):
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        if raw == json.dumps(original):
            return {'original': True}
        assert headers['id'] == original['headers']['id'], headers
        # The maintenance deferral: the same command with eta +15 s.
        assert headers['retries'] == original['headers']['retries']
        offset = datetime.fromisoformat(headers['eta']).timestamp()
        assert since.timestamp() + 14 <= offset <= until.timestamp() + 16, headers['eta']
        headers['eta'] = 'deferred+15s'
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = '<tag>'
        headers['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', headers['origin']) else headers['origin']
        properties['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', properties.get('origin') or '') else properties.get('origin')
        message['body'] = json.loads(base64.b64decode(message['body']))
        return message

    def bucket(self, normalize):
        objects = {}
        for item in self.stack.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            key = item['Key']
            head = self.stack.s3.head_object(Bucket=BUCKET, Key=key)
            body = self.stack.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
            entry = {k: head.get(k) for k in ('ContentType', 'ContentEncoding', 'CacheControl', 'Metadata')}
            entry['sha256'] = hashlib.sha256(body).hexdigest()
            objects[normalize(key)] = entry
        return objects

    # -- scenarios ------------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        stack.seed(scenario)
        payload = stack.publish(scenario)
        task_id = json.loads(payload)['headers']['id']
        stack.seed(scenario)
        self.before = stack.snapshot()
        self.known = known_uuids(self.before, stack.archives.get(scenario.archive))
        since = datetime.now(timezone.utc)
        self.run_django(scenario, payload, task_id)
        django = self.observe(scenario, payload, since, datetime.now(timezone.utc))
        self.authored(scenario, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return
        stack.seed(scenario)
        again = stack.snapshot()
        assert again == self.before, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        self.run_go(scenario, payload, task_id, scenario.handoff)
        if scenario.handoff:
            handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
            expected = json.loads(payload)
            expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
            assert len(handed) == 1 and handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes'
            assert stack.snapshot() == self.before, 'handoff has no effects'
            if scenario.archive:
                stack.s3.head_object(Bucket=BUCKET, Key=f'{LOCATION}/{stack.archive_name(scenario)}')
            self.run_django(scenario, payload, task_id)
        go = self.observe(scenario, payload, since, datetime.now(timezone.utc))
        if scenario.handoff:
            for observation in (django, go):
                observation['broker'] = {k: v for k, v in observation['broker'].items() if not k.startswith('_kombu.binding.')}
        self.observations[scenario.name] = {'django': django, 'go': go}
        if go != django:
            (OUTPUT / 'difference.json').write_text(json.dumps({'scenario': scenario.name, 'django': django, 'go': go},
                                                               indent=1, default=str, ensure_ascii=False) + '\n')
        assert go == django, 'Go differs from Django: ' + scenario.name
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
        keys = [k for k in observed['bucket'] if k.startswith(f'{LOCATION}/imports/')]
        assert not keys, ('the staged archive is deleted', keys)
        if not expect['success']:
            if 'error' in expect:
                assert result['error'] == expect['error'], result
            assert not observed['models'].get('application.SportAssociation', {}).get('added'), 'rolled back'
            return
        media = [k for k in observed['bucket'] if not k.startswith(f'{LOCATION}/unrelated/')]
        assert len(media) == expect['files'], observed['bucket']
        assert result['stats']['files_imported'] == expect['files'], result
        assert result['stats']['Folder'] == 4 and result['stats']['m2m_resolved'] >= 3, result
        assert observed['audit'] and observed['audit_index'], 'audited'


def known_uuids(snapshot, archive):
    found = set(UUID_TEXT.findall(json.dumps(snapshot, default=str)))
    if archive:
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for name in zf.namelist():
                    found.update(UUID_TEXT.findall(name))
                    if name.endswith('.json'):
                        found.update(UUID_TEXT.findall(zf.read(name).decode('utf-8', 'replace')))
        except zipfile.BadZipFile:
            pass
    return found


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
    with tempfile.TemporaryDirectory(prefix='az-consumer-import-', dir='/tmp') as directory, \
            (OUTPUT / 'services.log').open('w') as log, private_minio(log, docker_env, result) as storage:
        work = Path(directory)
        attempted = False
        redis_process = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True,
                                  timeout=180)
        try:
            plan = ROOT / 'BE_GO/internal/importeffects/plan.json'
            if not plan.is_file() or plan.stat().st_size == 0:
                raise RuntimeError('missing import plan')
            subprocess.run([sys.executable, '-I', str(ROOT / 'TESTS/adapters/go/association_import_plan.py'), '--check', str(plan)],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            result['plan_current'] = True
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'],
                               cwd=ROOT / 'BE_GO', env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass',
                                              REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                                             stdout=log, stderr=subprocess.STDOUT)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale',
                 '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w',
                 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            archives = build_archives()
            result['archives'] = {name: hashlib.sha256(data).hexdigest() for name, data in archives.items()}
            stack = Stack(work, environment, redis_port, storage, archives)
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
    paths = ['TESTS/adapters/go/celery_consumer_import_process.py', 'TESTS/adapters/go/association_import_plan.py',
             'TESTS/adapters/go/consumer_import_settings.py', 'TESTS/adapters/go/consumer_restore_settings.py',
             'TESTS/adapters/go/consumer_restore_worker.py', 'BE/application/tasks.py',
             'BE/application/services/import_service.py', 'BE/application/services/legacy_backup.py',
             'BE/application/services/json_stream.py', 'BE/application/services/validators.py',
             'BE/application/signals.py', 'BE/application/audit_resolvers.py', 'BE_GO/internal/importeffects/plan.json']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/',
                                                        'internal/importeffects/', 'consumer_import_', 'import_effects')))
    result.update(success=True, partial=bool(ONLY), django_only=DJANGO_ONLY, race_instrumented=not DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope', 'archives')}, default=str))


if __name__ == '__main__':
    main()
