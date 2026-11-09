"""Differential harness for the Go Celery consumer: instance.restore.execute (restore_instance_data).

Private PostgreSQL (actual Django current schema), a private Redis (broker DB 0, Django cache DB 1, channel layer DB 0)
and a private MinIO (the production S3Boto3Storage). The fixture archive is made by Django itself: the instance's
association is seeded in an earlier state and AssociationExportService.export() writes the backup; the variants
(truncated, another manifest version or association, a missing attachment, circular folders, a value the database
rejects, course subscriptions) are rewritten from it with zipfile. Each scenario seeds the instance in its current
state (self-hosted InstanceConfiguration, the owner as administrator, business rows and their stored attachments, an
earlier completed restore whose recovery archive and staged attachments the pruning removes, the uploaded backup and
its DataRestore receipt), builds the message with Django's own producer (args [operation id], task_id the operation id,
timelimit [3600, 3300]) routed by GO_TASK_QUEUE_TASKS to ``assozeta-go``, and runs the identical bytes once on a real
Django worker subprocess (prefork, acks_late, django-db results, production cache, channel layer and S3 storage) and,
after re-seeding, once on the race-built Go consumer.

Compared after each worker stopped: every row of every managed model and auto-created through table (serial ids
included, except LogEntry/AuditLogIndex keys), every auditlog entry (object, repr, action, changes) and AuditLogIndex
row, the TaskResult (status, result, meta), every broker key with decoded messages, every cache key (unpickled value
and TTL; the progress payload's ``sequence`` and ``updated_at`` depend on the 1 s throttle and are normalized after a
shape check) and every bucket object (key, ContentType, ContentEncoding, CacheControl, Metadata, SHA-256). The
recovery archive differs per run in its DOS times and manifest ``export_date`` (and so in its SHA-256, which the
receipt stores): it is compared member by member (describe_zip of the export harness) and its digest is checked against
the receipt. Random names (staged attachments, recovery archive) are labelled in order of appearance.

The worker-loss scenario holds the receipt's ``stage='files'`` UPDATE behind an advisory lock (a trigger the harness
installs), SIGKILLs the prefork child while it waits, terminates its waiting backend and releases the gate; the
acks_late message is requeued and redelivered. Go performs the replacement's database work during Plan, in the
transaction it commits after the media are staged, so the killed attempt has consumed sequence values Django's has not
reached yet (documented difference): at the gate, before the kill, the harness puts every sequence back to its value
before the run, on both sides. A message Go hands off after its Plan transaction started (a database error) has consumed
sequence values the same way; the harness puts them back before Django's worker runs the handed-off message.

Authored assertions check that the Django oracle took the intended path. Handoff scenarios require Go to move the
message unchanged to Django's ``celery`` queue with no effect; the original message then runs on Django's worker.
"""
import base64
import fcntl
import hashlib
import io
import json
import os
import pickle
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
from TESTS.adapters.go.celery_consumer_import_process import (truncate, rewrite, edit_records, private_minio)  # noqa: E402
from TESTS.adapters.go.celery_consumer_export_process import describe_zip  # noqa: E402

DATABASE = 'assozeta_contract_consumer_restore'
REDIS_PASSWORD = 'private-redis-password-canary'
GO_QUEUE, DJANGO_QUEUE = 'assozeta-go', 'celery'
TASK = 'instance.restore.execute'
BUCKET, LOCATION = 'consumer-restore', 'private'
VERSION = 'v9.8.7-restore'
OUTPUT = ROOT / os.environ.get('CONSUMER_RESTORE_OUTPUT', 'quality-reports/go-migration/consumer-restore-instance-process')
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
ONLY = os.environ.get('CELERY_CONSUMER_ONLY', '')
PBKDF2 = 'pbkdf2_sha256$1000000$fixturesalt$' + base64.b64encode(hashlib.sha256(b'fixture').digest()).decode()
SOURCE_CREATED = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
PRIOR_CREATED = datetime(2026, 1, 10, 8, 0, 0, tzinfo=timezone.utc)
OP_CREATED = datetime(2026, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
UUID_TEXT = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
NAIVE = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{6})?')
GATE = 4242
GENERIC = 'Ripristino non riuscito. I dati precedenti sono conservati. Verifica il backup e riprova.'
DAMAGED = 'Il backup ZIP è danneggiato o non rispetta il formato Bakney.'


def U(n):
    return UUID(int=0xD9000000 + n)


OWNER, COLLABORATOR, ATHLETE, EXTRA = U(1), U(2), U(3), U(4)
ASSOCIATION, SECOND = U(11), U(12)
OPERATION, PRIOR = U(300), U(301)
D_PATH, D_MEDICAL, D_INVOICE, D_ATTACHMENT, D_EXTRA = (U(n) for n in range(21, 26))
TAG_A, TAG_B = U(81), U(82)
UPLOAD = f'restores/{OPERATION}/upload/backup.zip'
PRIOR_BACKUP = f'restores/{PRIOR}/recovery/{U(310)}.zip'
PRIOR_MEDIA = f'restores/{PRIOR}/media/{U(311)}/vecchio.pdf'


class Scenario:
    def __init__(self, name, archive='good', *, op=None, setup=None, handoff=False, expect=None, mode=None, args=None,
                 kwargs=None):
        self.name, self.archive, self.op, self.setup = name, archive, op or {}, setup
        self.handoff, self.expect, self.mode = handoff, expect or {}, mode
        self.args, self.kwargs = args, kwargs or {}


SCENARIOS = [
    Scenario('an archive replaces the current association data', expect={'state': 'completed'}),
    Scenario('a second delivery after completion only cleans up', op={'state': 'completed', 'stage': 'completed',
                                                                        'completed_at': OP_CREATED},
             expect={'state': 'completed', 'untouched': True}),
    Scenario('an operation under review is left untouched', op={'state': 'review', 'stage': 'validated'},
             expect={'state': 'review', 'untouched': True}),
    Scenario('a missing operation fails with DoesNotExist', op={'absent': True}, expect={'status': 'FAILURE'}),
    Scenario('a held restore lock defers the restore by 10 s', mode='flock', expect={'status': 'RETRY'}),
    Scenario('a busy database barrier defers the restore by 10 s', mode='barrier', expect={'status': 'RETRY'}),
    Scenario('three interrupted attempts fail the restore', op={'attempts': 3},
             expect={'state': 'failed', 'error': 'Ripristino interrotto più volte. I dati precedenti sono conservati; '
                                                 'verifica il worker e carica nuovamente il backup.'}),
    Scenario('a changed application version fails the restore', op={'version': 'v0.0.1'},
             expect={'state': 'failed', 'error': 'La versione dell’applicazione è cambiata. Valida nuovamente il backup.'}),
    Scenario('an owner who no longer administers the instance fails the restore', op={'owner_id': ATHLETE},
             expect={'state': 'failed', 'error': 'Il titolare o l’associazione sono cambiati. Valida nuovamente il backup.'}),
    Scenario('a second association fails the single-association check', setup='second-association',
             expect={'state': 'failed', 'error': 'Il ripristino richiede una istanza self-hosted con una sola associazione.'}),
    Scenario('a changed upload fails the digest check', op={'sha256': '0' * 64},
             expect={'state': 'failed', 'error': 'Il file caricato è cambiato dopo la validazione.'}),
    Scenario('a truncated archive is rejected as damaged', 'truncated', expect={'state': 'failed', 'error': DAMAGED}),
    Scenario('an unsupported manifest version is rejected', 'manifest-version',
             expect={'state': 'failed', 'error': 'Formato non supportato. Carica un export ZIP Bakney versione 1.0.0.'}),
    Scenario('a manifest naming another association is rejected', 'manifest-association',
             expect={'state': 'failed', 'error': 'Il manifest non corrisponde all’associazione esportata.'}),
    Scenario('missing attachments need an explicit confirmation', 'missing-media',
             expect={'state': 'failed', 'error': 'Conferma esplicitamente l’assenza degli allegati indicati.'}),
    Scenario('confirmed missing attachments are restored without them', 'missing-media', op={'allow_missing_media': True},
             expect={'state': 'completed'}),
    Scenario('circular folders roll the replacement back', 'circular-folders', expect={'state': 'failed', 'error': GENERIC}),
    Scenario('a value the database rejects is handed off', 'db-reject', handoff=True,
             expect={'state': 'failed', 'error': GENERIC}),
    Scenario('course subscriptions are handed off', 'course-subscription', handoff=True, expect={'state': 'completed'}),
    Scenario('an unknown keyword argument is handed off', kwargs={'extra': 1}, handoff=True, expect={'status': 'FAILURE'}),
    Scenario('a worker killed during the restore is redelivered and completes', mode='kill',
             expect={'state': 'completed', 'attempts': 2}),
    Scenario('a concurrent delivery defers while the restore holds the lock', mode='concurrent',
             expect={'state': 'completed'}),
]


# -- fixture -----------------------------------------------------------------------------------------
MEDIA = {
    'documents/tessera.pdf': PDF + b'tessera',
    'medical/certificato.pdf': PDF + b'certificato',
    'invoices/fattura-1.pdf': PDF + b'fattura',
    'attachments/allegato.txt': ('riga di prova\n' * 500).encode(),
    'signatures/firma-socio.png': b'\x89PNG\r\n\x1a\nfirma',
}
CURRENT_MEDIA = {'attachments/nuovo.pdf': PDF + b'nuovo'}


def seed_association(current, course_subscription=False):
    """The instance's association: the backed-up state, or (current) the state the restore replaces."""
    from application.models import (User, SportAssociation, Group, Associate, Subscription, Tags, Payment,
                                    PaymentCategory, VatManagement, MedicalCertificate, Folder, Course, CourseLocation,
                                    Reminder, Family, AssociateTutorRelation, Instructor, Signature)
    from application.models.invoices_models import Invoice
    from communications.models import Message, MessageTransaction
    from docmanager.models import Document
    users = [User(user_id=OWNER, username='restore-owner', role=1, password=PBKDF2, date_joined=SOURCE_CREATED,
                  email='owner@example.test', first_name='Anna', last_name='Rossi'),
             User(user_id=COLLABORATOR, username='restore-collaborator', role=1, password='!unusable',
                  date_joined=SOURCE_CREATED, email='collab@example.test'),
             User(user_id=ATHLETE, username='restore-athlete', role=2, password=PBKDF2, date_joined=SOURCE_CREATED,
                  email='atleta@example.test', first_name='Luca', last_name='Bianchi')]
    if current:
        users.append(User(user_id=EXTRA, username='restore-extra', role=1, password=PBKDF2, date_joined=SOURCE_CREATED,
                          email='extra@example.test', first_name='Nuovo'))
    User.original_objects.bulk_create(users)
    User.original_objects.filter(user_id__in=[COLLABORATOR, EXTRA]).update(connected_user_id=OWNER)
    SportAssociation.original_objects.bulk_create([SportAssociation(
        sport_association_id=ASSOCIATION, user_id=OWNER, tax_code='01234567890',
        denomination='ASD Ripristino (corrente)' if current else 'ASD Ripristino «Uno»')])
    Group.objects.bulk_create([Group(group_id=U(41), name='Gruppo A', description='primo', sport_association_id=ASSOCIATION)])
    documents = [
        Document(document_id=D_PATH, creation_date=SOURCE_CREATED, filepath='documents/tessera.pdf', filename='tessera.pdf',
                 token=U(51), file_size_bytes=10),
        Document(document_id=D_MEDICAL, creation_date=SOURCE_CREATED, filepath='medical/certificato.pdf',
                 filename='certificato.pdf', token=U(54)),
        Document(document_id=D_INVOICE, creation_date=SOURCE_CREATED, filepath='invoices/fattura-1.pdf',
                 filename='fattura-1.pdf', token=U(55)),
        Document(document_id=D_ATTACHMENT, creation_date=SOURCE_CREATED, filepath='attachments/allegato.txt',
                 filename='allegato.txt', token=U(56))]
    if current:
        documents.append(Document(document_id=D_EXTRA, creation_date=SOURCE_CREATED, filepath='attachments/nuovo.pdf',
                                  filename='nuovo.pdf', token=U(57)))
    Document.objects.bulk_create(documents)
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
    Tags.objects.bulk_create([Tags(tag_id=TAG_A, tag_name='agonisti (rinominato)' if current else 'agonisti',
                                   sport_association_id=ASSOCIATION, creation_date=SOURCE_CREATED),
                              Tags(tag_id=TAG_B, tag_name='ünder', sport_association_id=ASSOCIATION,
                                   creation_date=SOURCE_CREATED)])
    MedicalCertificate.objects.bulk_create([MedicalCertificate(medical_id=U(91), document_id=D_MEDICAL, user_id=ATHLETE,
                                                               creation_date=SOURCE_CREATED,
                                                               expiration_date=date(2027, 1, 1))])
    Subscription.objects.bulk_create([Subscription(
        subscription_id=U(101), sport_association_id=ASSOCIATION, associate_id=U(71), user_id=ATHLETE, medical_id=U(91),
        creation_date=SOURCE_CREATED, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        custom_data={'zeta': 1, 'alfa': [1.5, 2, None, True], 'testo': 'àèì'},
        meta={'k': {'nested': 0.1}}, signature_storage_key='signatures/firma-socio.png',
        signature_url='https://cdn.example.test/old-signature.png')])
    Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=U(101), tags_id=TAG_B),
                                                   Subscription.tags.through(subscription_id=U(101), tags_id=TAG_A)])
    VatManagement.objects.bulk_create([VatManagement(vat_management_id=U(111), vat=22)])
    PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(121), sport_association_id=ASSOCIATION,
                                                         name='Quote', vat_management_id=U(111),
                                                         creation_date=SOURCE_CREATED)])
    if not current:
        User.original_objects.filter(user_id=OWNER).update(default_payment_category_id=U(121))
    Signature.objects.bulk_create([Signature(signature_id=U(125), user_id=ATHLETE, signature='firma')])
    Invoice.objects.bulk_create([Invoice(invoice_id=U(141), sport_association_id=ASSOCIATION, document_pdf_id=D_INVOICE,
                                         membership_fee=Decimal('25.00'), activity_fee=Decimal('0.50'), number=7)])
    payments = [
        Payment(payment_id=U(131), type='quota', subject=1, amount=Decimal('10.50'), creation_date=SOURCE_CREATED,
                payment_date=SOURCE_CREATED + timedelta(days=1), sport_association_id=ASSOCIATION, user_id=ATHLETE,
                associate_id=U(71), payment_category_id=U(121), meta={'via': 'bonifico'}, course={'id': None},
                description='Quota annuale', invoice_id=U(141), signature_id=U(125), payment_intent_id='pi_fixture'),
        Payment(payment_id=U(132), type='quota', subject=1, amount=Decimal('0.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, deleted=True, description=None),
        Payment(payment_id=U(133), type='quota', subject=2, amount=Decimal('35.00'), creation_date=SOURCE_CREATED,
                sport_association_id=ASSOCIATION, description='quota CORSO',
                meta={'description': 'Nuoto, primo trimestre'})]
    if current:
        payments.append(Payment(payment_id=U(136), type='quota', subject=0, amount=Decimal('7.00'),
                                creation_date=SOURCE_CREATED, sport_association_id=ASSOCIATION, description='Nuovo'))
    else:
        payments.append(Payment(payment_id=U(135), type='quota', subject=0, amount=Decimal('5.00'),
                                creation_date=SOURCE_CREATED, sport_association_id=ASSOCIATION, description=None))
    Payment.objects.bulk_create(payments)
    attachments = [Payment.attachments.through(payment_id=U(131), document_id=D_ATTACHMENT),
                   Payment.attachments.through(payment_id=U(131), document_id=D_PATH)]
    if current:
        attachments.append(Payment.attachments.through(payment_id=U(136), document_id=D_EXTRA))
    Payment.attachments.through.objects.bulk_create(attachments)
    root = Folder.objects.create(name='Radice', sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    child = Folder.objects.create(name='Figlia', parent=root, sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    Folder.objects.create(name='Nipote', parent=child, sport_association_id=ASSOCIATION, created_at=SOURCE_CREATED)
    if not current:
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


def seed_instance(primary=ASSOCIATION):
    from instance.models import InstanceConfiguration
    InstanceConfiguration.objects.create(domain='restore.example.test', name='Istanza di prova', self_hosted=True,
                                         support_multiple_associations=False, primary_association_id=primary)


def export_archive():
    from django.conf import settings
    from django.core.files.storage import default_storage
    from application.services.export_service import AssociationExportService
    media = Path(settings.MEDIA_ROOT)
    if media.exists():
        shutil.rmtree(media)
    for name, body in MEDIA.items():
        (media / name).parent.mkdir(parents=True, exist_ok=True)
        (media / name).write_bytes(body)
    document = AssociationExportService(ASSOCIATION).export()
    with default_storage.open(document.filepath, 'rb') as stream:
        return stream.read()


def build_archives():
    truncate()
    seed_association(current=False)
    good = export_archive()
    members = zipfile.ZipFile(io.BytesIO(good)).namelist()
    media = [m for m in members if m.startswith('files/')]
    assert len(media) == 5, members
    archives = {'good': good, 'truncated': good[:len(good) // 2]}

    def manifest(change):
        def edit(name, body):
            if name != 'manifest.json':
                return body
            data = json.loads(body)
            change(data)
            return json.dumps(data, indent=2, ensure_ascii=False).encode()
        return rewrite(good, edit)
    archives['manifest-version'] = manifest(lambda data: data.update(version='2.0.0'))
    archives['manifest-association'] = manifest(lambda data: data['association'].update(denomination='Altra'))
    dropped = [m for m in media if '/medical_certificates/' in m]
    assert len(dropped) == 1, media

    def missing(name, body):
        if name == dropped[0]:
            return None
        if name == 'manifest.json':
            data = json.loads(body)
            data['statistics']['files_exported'] -= 1
            return json.dumps(data, indent=2, ensure_ascii=False).encode()
        return body
    archives['missing-media'] = rewrite(good, missing)

    def circular(records):
        by_name = {r['name']: r for r in records}
        by_name['Radice']['parent_id'] = by_name['Figlia']['id']
    archives['circular-folders'] = edit_records(good, 'data/14_folders.json', circular)

    def negative(records):
        for record in records:
            record['frequency'] = -3
    archives['db-reject'] = edit_records(good, 'data/55_reminders.json', negative)
    truncate()
    seed_association(current=False, course_subscription=True)
    archives['course-subscription'] = export_archive()
    truncate()
    return archives


# -- stack -------------------------------------------------------------------------------------------
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

    @property
    def lock_path(self):
        return self.work / 'contract-lifecycle.lock'

    def seed(self, scenario):
        from application.models import User, SportAssociation
        from instance.models import DataRestore
        started = datetime.now(timezone.utc)
        truncate()
        seed_association(current=True)
        if scenario.setup == 'second-association':
            User.original_objects.bulk_create([User(user_id=U(5), username='second-owner', role=1, password=PBKDF2,
                                                    date_joined=SOURCE_CREATED, email='second@example.test')])
            SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=SECOND, user_id=U(5),
                                                                            denomination='Seconda', tax_code='00000000000')])
        seed_instance()
        archive = self.archives.get(scenario.archive, b'')
        DataRestore._base_manager.bulk_create([DataRestore(
            id=PRIOR, owner_id=OWNER, association_id=ASSOCIATION, state='completed', stage='completed',
            completed_at=PRIOR_CREATED, upload_path='', sha256='1' * 64, version=VERSION, backup_path=PRIOR_BACKUP,
            backup_sha256='2' * 64, media={f'Document:{D_PATH}': PRIOR_MEDIA}, attempts=1)])
        values = dict(id=OPERATION, owner_id=OWNER, association_id=ASSOCIATION, state='queued', stage='queued',
                      upload_path=UPLOAD, sha256=hashlib.sha256(archive).hexdigest(), version=VERSION,
                      preview={'association': 'ASD Ripristino «Uno»'}, attempts=0)
        values.update({k: v for k, v in scenario.op.items() if k != 'absent'})
        if not scenario.op.get('absent'):
            DataRestore._base_manager.bulk_create([DataRestore(**values)])
        self.fix_defaults(started)
        DataRestore._base_manager.filter(pk=PRIOR).update(created_at=PRIOR_CREATED, updated_at=PRIOR_CREATED)
        DataRestore._base_manager.filter(pk=OPERATION).update(created_at=OP_CREATED, updated_at=OP_CREATED)
        for item in self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            self.s3.delete_object(Bucket=BUCKET, Key=item['Key'])
        for name, body in {**MEDIA, **CURRENT_MEDIA}.items():
            self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{name}', Body=body, ContentType='application/octet-stream')
        self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/unrelated/keep.txt', Body=b'keep', ContentType='text/plain')
        self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{PRIOR_BACKUP}', Body=b'old backup', ContentType='application/zip')
        self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{PRIOR_MEDIA}', Body=PDF + b'old', ContentType='application/pdf')
        self.s3.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{UPLOAD}', Body=archive, ContentType='application/zip')
        self.cache.flushdb()
        self.cache.set(':1:unrelated-cache-key', pickle.dumps('kept until cache.clear()'))
        self.seeding = (started, datetime.now(timezone.utc))

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

    def sequences(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT schemaname, sequencename FROM pg_sequences WHERE schemaname = 'public' ORDER BY 2")
            names = cursor.fetchall()
            state = {}
            for schema, name in names:
                cursor.execute(f'SELECT last_value, is_called FROM "{schema}"."{name}"')
                state[name] = cursor.fetchone()
        return state

    def restore_sequences(self, state):
        assert state, 'sequence snapshot'
        from django.db import connection
        with connection.cursor() as cursor:
            for name, (value, called) in state.items():
                cursor.execute('SELECT setval(%s, %s, %s)', [f'public."{name}"', value, called])

    def publish(self, scenario, task_id=None):
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        import instance.tasks  # noqa: F401  (registers instance.restore.execute)
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (self.broker_url, DJANGO_QUEUE, go_task_routes(TASK), 'django-db'))))
        _reset_celery_app(app)
        args = scenario.args if scenario.args is not None else [str(OPERATION)]
        try:
            self.broker.flushdb()
            if scenario.kwargs:
                app.send_task(TASK, args=args, kwargs=scenario.kwargs, task_id=task_id or str(OPERATION),
                              time_limit=3600, soft_time_limit=3300)
            else:
                app.tasks[TASK].apply_async(args=args, task_id=task_id or str(OPERATION))
            raw = self.broker.rpop(GO_QUEUE)
            assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
        self.broker.flushdb()
        message = json.loads(raw)
        assert message['headers']['timelimit'] == [3600, 3300], message['headers']
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

    def results(self, task_ids, final):
        from django_celery_results.models import TaskResult
        return all(TaskResult.objects.filter(task_id=task_id, status__in=final).exists() for task_id in task_ids)

    def quiescent(self, task_ids, queue=GO_QUEUE, held=0, final=('SUCCESS', 'FAILURE')):
        if self.broker.llen(queue) or self.broker.hlen('unacked') != held:
            return False
        return self.results(task_ids, final)

    def waiting_on_gate(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND objid = %s AND NOT granted", [GATE])
            return [row[0] for row in cursor.fetchall()]


class Gate:
    """Holds the receipt's stage='files' UPDATE behind an advisory lock owned by a separate harness connection."""
    def __init__(self, stack):
        import psycopg2
        self.stack = stack
        self.connection = psycopg2.connect(host=str(stack.work), port=5432, user='contracts', dbname=DATABASE)
        self.connection.autocommit = True
        with self.connection.cursor() as cursor:
            cursor.execute(f"""CREATE OR REPLACE FUNCTION contract_restore_gate() RETURNS trigger AS $$
              BEGIN IF NEW.stage = 'files' AND NEW.id = '{OPERATION}'::uuid THEN PERFORM pg_advisory_lock({GATE});
              PERFORM pg_advisory_unlock({GATE}); END IF; RETURN NEW; END $$ LANGUAGE plpgsql""")
            cursor.execute('CREATE TRIGGER contract_restore_gate BEFORE UPDATE ON instance_datarestore FOR EACH ROW '
                           'EXECUTE FUNCTION contract_restore_gate()')
            cursor.execute('SELECT pg_advisory_lock(%s)', [GATE])

    def release(self):
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [GATE])
            cursor.execute('DROP TRIGGER IF EXISTS contract_restore_gate ON instance_datarestore')
            cursor.execute('DROP FUNCTION IF EXISTS contract_restore_gate()')
        self.connection.close()

    def terminate(self, pids):
        assert pids, 'no backend waits on the gate'
        with self.connection.cursor() as cursor:
            for pid in pids:
                cursor.execute('SELECT pg_terminate_backend(%s)', [pid])


def child_pids(parent):
    found = subprocess.run(['pgrep', '-P', str(parent)], capture_output=True, text=True, timeout=10)
    return [int(line) for line in found.stdout.split()]


class Harness:
    def __init__(self, stack, binary):
        self.stack, self.binary = stack, binary
        self.completed, self.observations = [], {}

    @staticmethod
    def wait(predicate, description, timeout=240):
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

    def drive(self, scenario, process, task_ids, payloads, start):
        """Waits for the scenario's end state while the worker runs; returns the observed task ids."""
        stack = self.stack
        mode = scenario.mode
        if mode == 'kill':
            self.wait(lambda: stack.waiting_on_gate() or process.poll() is not None, 'gate reached')
            assert process.poll() is None, 'worker exited before the gate'
            waiting = stack.waiting_on_gate()
            children = child_pids(process.pid)
            assert len(children) == 1, children
            stack.restore_sequences(self.sequences)
            os.kill(children[0], signal.SIGKILL)
            self.gate.terminate(waiting)
            self.gate.release()
            self.gate = None
            self.wait(lambda: stack.quiescent(task_ids) or process.poll() is not None, 'redelivered restore')
        elif mode == 'concurrent':
            self.wait(lambda: stack.waiting_on_gate() or process.poll() is not None, 'gate reached')
            assert process.poll() is None, 'worker exited before the gate'
            stack.broker.lpush(GO_QUEUE, payloads[1])
            self.wait(lambda: stack.results(task_ids[1:], ('RETRY',)) or process.poll() is not None, 'concurrent deferral')
            self.gate.release()
            self.gate = None
            self.wait(lambda: stack.quiescent(task_ids) or process.poll() is not None, 'both deliveries')
        elif mode in ('flock', 'barrier'):
            self.wait(lambda: stack.quiescent(task_ids, held=1, final=('RETRY',)) or process.poll() is not None,
                      'deferral: ' + scenario.name)
        else:
            self.wait(lambda: stack.quiescent(task_ids) or process.poll() is not None, 'worker: ' + scenario.name)

    def prepare(self, scenario):
        self.gate, self.holder = None, None
        self.sequences = self.stack.sequences()
        if scenario.mode in ('kill', 'concurrent'):
            self.gate = Gate(self.stack)
        elif scenario.mode == 'flock':
            self.holder = open(self.stack.lock_path, 'a')
            fcntl.flock(self.holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif scenario.mode == 'barrier':
            import psycopg2
            self.holder = psycopg2.connect(host=str(self.stack.work), port=5432, user='contracts', dbname=DATABASE)
            self.holder.autocommit = True
            with self.holder.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_lock(%s, %s)', [1095981639, 1381192786])

    def finish(self):
        if self.gate:
            self.gate.release()
            self.gate = None
        if self.holder:
            self.holder.close()
            self.holder = None

    def run_django(self, scenario, payloads, task_ids, queue=GO_QUEUE, push=True):
        if push:
            self.stack.broker.flushdb()
            self.stack.broker.lpush(queue, payloads[0])
        log = OUTPUT / f'django-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        environment = dict(os.environ, DJANGO_SETTINGS_MODULE='TESTS.adapters.go.consumer_import_settings',
                           CONSUMER_RESTORE_BROKER_URL=self.stack.broker_url, CONSUMER_RESTORE_PRODUCTION_SERVICES='1',
                           CONSUMER_RESTORE_REDIS_PORT=str(self.stack.redis_port),
                           CONSUMER_RESTORE_REDIS_PASSWORD=REDIS_PASSWORD, RUNNING_VERSION=VERSION,
                           TESTS_WORKDIR=str(self.stack.work), TESTS_DATABASE=DATABASE,
                           PYTHONPATH=f'{ROOT / "BE"}:{ROOT}', OBJC_DISABLE_INITIALIZE_FORK_SAFETY='YES', **self.service_env())
        concurrency = '2' if scenario.mode == 'concurrent' and push else '1'
        process = subprocess.Popen([sys.executable, '-m', 'TESTS.adapters.go.consumer_restore_worker', '-P', 'prefork',
                                    '-c', concurrency, '-Q', queue, '-n', 'oracle@%h', '--without-heartbeat',
                                    '--without-gossip', '--without-mingle', '-l', 'WARNING'], cwd=ROOT / 'BE',
                                   env=environment, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            if push:
                self.drive(scenario, process, task_ids, payloads, time.monotonic())
            else:
                self.wait(lambda: self.stack.quiescent(task_ids, queue) or process.poll() is not None,
                          'Django handoff run: ' + scenario.name)
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
                      DATA_RESTORE_LOCK_PATH=str(self.stack.lock_path), EMAIL_HOST='127.0.0.1', RUNNING_VERSION=VERSION,
                      **self.service_env())
        path = self.stack.work / 'consumer.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        path.chmod(0o600)
        return path

    def run_go(self, scenario, payloads, task_ids, handoff):
        self.stack.broker.flushdb()
        self.stack.broker.lpush(GO_QUEUE, payloads[0])
        log = OUTPUT / f'consumer-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        concurrency = '2' if scenario.mode == 'concurrent' else '1'
        process = subprocess.Popen([str(self.binary), '--env-file', str(self.env_file()), '--concurrency', concurrency],
                                   env=dict(self.stack.env), stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            if handoff:
                self.wait(lambda: self.stack.broker.llen(GO_QUEUE) == 0 and self.stack.broker.hlen('unacked') == 0
                          and self.stack.broker.llen(DJANGO_QUEUE) == 1 or process.poll() is not None,
                          'Go handoff: ' + scenario.name)
            else:
                self.drive(scenario, process, task_ids, payloads, time.monotonic())
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
    def observe(self, scenario, payloads, since, until):
        task_ids = [json.loads(p)['headers']['id'] for p in payloads]
        known = set(self.known)
        labels = {}

        def label(text):
            if text in known:
                return text
            if text not in labels:
                labels[text] = f'<random-{len(labels)}>'
            return labels[text]

        def text(value):
            naive = NAIVE.fullmatch(value)
            if naive:
                moment = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
                if since - timedelta(seconds=1) <= moment <= until:
                    return 'run'
            if re.fullmatch(r'![A-Za-z0-9]{40}', value):
                return '<unusable-password>'
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
        for row in sorted(results['added'], key=lambda r: task_ids.index(r['task_id']) if r['task_id'] in task_ids else 99):
            row = dict(row)
            row.pop('id')
            row['meta'] = json.loads(row['meta'])
            row['meta']['children'] = [[[f'<task-{task_ids.index(c)}>' if c in task_ids else '<child>', rest], extra]
                                       for (c, rest), extra in row['meta']['children']]
            row['result'] = json.loads(row['result']) if row['result'] else row['result']
            row['traceback'] = bool(row['traceback'])
            row['task_id'] = f'<task-{task_ids.index(row["task_id"])}>'
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
        restores = out.get('instance.DataRestore', {}).get('added', [])
        bucket, recovery, digests = self.bucket(restores)
        for row in restores:
            if row.get('backup_sha256') in digests:
                row['backup_sha256'] = '<recovery>'
        observed = {'task_results': rows, 'audit': audit, 'audit_removed': normalize(entries['removed']),
                    'audit_index': index_rows, 'models': normalize(out), 'recovery': recovery,
                    'broker': self.broker_observed(payloads, since, until), 'bucket': normalize(bucket),
                    'cache': self.cache_state(normalize, since, until)}
        return observed

    def bucket(self, restores):
        """Every object; the recovery archives described member by member, their digests checked against the receipt."""
        digests = {r['backup_sha256'] for r in restores if r.get('backup_sha256')}
        objects, recovery, found = {}, {}, set()
        for item in self.stack.s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            key = item['Key']
            head = self.stack.s3.head_object(Bucket=BUCKET, Key=key)
            body = self.stack.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
            entry = {k: head.get(k) for k in ('ContentType', 'ContentEncoding', 'CacheControl', 'Metadata')}
            digest = hashlib.sha256(body).hexdigest()
            if re.fullmatch(rf'{LOCATION}/restores/{OPERATION}/recovery/[0-9a-f-]{{36}}\.zip', key):
                assert digest in digests, ('recovery archive digest stored in the receipt', key)
                found.add(digest)
                entry['sha256'] = '<recovery>'
                recovery[key] = describe_zip(body, self.window[0] - timedelta(seconds=2), self.window[1])
            else:
                entry['sha256'] = digest
            objects[key] = entry
        return objects, [recovery[k] for k in sorted(recovery)], found

    def cache_state(self, normalize, since, until):
        state = {}
        for key in sorted(self.stack.cache.keys('*')):
            value = pickle.loads(self.stack.cache.get(key))
            ttl = self.stack.cache.ttl(key)
            if isinstance(value, dict) and 'sequence' in value:
                assert isinstance(value['sequence'], int) and value['sequence'] > 0, value
                value['sequence'] = '<n>'
                stamp = datetime.fromisoformat(value['updated_at'])
                assert since <= stamp <= until, value['updated_at']
                value['updated_at'] = 'run'
            state[key.decode()] = {'value': normalize(value), 'ttl': 'day' if 86000 < ttl <= 86400 else ttl}
        return state

    def broker_observed(self, payloads, since, until):
        broker = {}
        for key, (kind, value) in self.stack.broker_state().items():
            if key.startswith('_kombu.binding.'):
                continue
            if kind == 'list':
                value = [self.normalize_message(item, payloads, since, until) for item in value]
            broker[key] = [kind, value]
        return broker

    @staticmethod
    def normalize_message(raw, payloads, since, until):
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        if raw in payloads:
            return {'original': payloads.index(raw)}
        originals = [json.loads(p) for p in payloads]
        index = [o['headers']['id'] for o in originals].index(headers['id'])
        assert headers['retries'] == originals[index]['headers']['retries']
        offset = datetime.fromisoformat(headers['eta']).timestamp()
        assert since.timestamp() + 9 <= offset <= until.timestamp() + 11, headers['eta']
        headers['eta'] = 'deferred+10s'
        headers['id'] = f'<task-{index}>'
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = '<tag>'
        headers['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', headers['origin']) else headers['origin']
        properties['origin'] = '<origin>' if re.fullmatch(r'gen\d+@.+', properties.get('origin') or '') else properties.get('origin')
        properties['correlation_id'] = f'<task-{index}>'
        message['body'] = json.loads(base64.b64decode(message['body']))
        return message

    # -- scenarios ------------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        stack.seed(scenario)
        payloads = [stack.publish(scenario)]
        if scenario.mode == 'concurrent':
            payloads.append(stack.publish(scenario, task_id=str(U(399))))
        task_ids = [json.loads(p)['headers']['id'] for p in payloads]
        stack.seed(scenario)
        self.before = stack.snapshot()
        self.known = known_uuids(self.before, stack.archives.get(scenario.archive))
        since = datetime.now(timezone.utc)
        self.window = (since, since + timedelta(hours=1))
        self.prepare(scenario)
        try:
            self.run_django(scenario, payloads, task_ids)
        finally:
            self.finish()
        until = datetime.now(timezone.utc)
        self.window = (since, until)
        django = self.observe(scenario, payloads, since, until)
        self.authored(scenario, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return
        stack.seed(scenario)
        assert stack.snapshot() == self.before, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        self.window = (since, since + timedelta(hours=1))
        self.prepare(scenario)
        try:
            self.run_go(scenario, payloads, task_ids, scenario.handoff)
            if scenario.handoff:
                handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
                expected = json.loads(payloads[0])
                expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
                assert len(handed) == 1 and handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes'
                assert stack.snapshot() == self.before, 'handoff has no effects'
                stack.s3.head_object(Bucket=BUCKET, Key=f'{LOCATION}/{UPLOAD}')
                stack.restore_sequences(self.sequences)
                self.run_django(scenario, payloads, task_ids, queue=DJANGO_QUEUE, push=False)
        finally:
            self.finish()
        until = datetime.now(timezone.utc)
        self.window = (since, until)
        go = self.observe(scenario, payloads, since, until)
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
        assert rows, 'no task result'
        if 'status' in expect:
            assert [r['status'] for r in rows] == [expect['status']], rows
            if expect['status'] == 'RETRY':
                assert observed['models'].get('instance.DataRestore') is None, 'deferral without effects'
            return
        assert all(r['status'] == 'SUCCESS' and r['result'] is None for r in rows), rows
        receipts = observed['models'].get('instance.DataRestore', {})
        if expect.get('untouched') and expect['state'] == 'review':
            assert not receipts, receipts
            return
        current = [r for r in receipts.get('added', []) if r['id'] == str(OPERATION)]
        assert len(current) == 1, receipts
        op = current[0]
        assert op['state'] == expect['state'], op
        assert op['upload_path'] == '', 'the upload is cleaned up'
        keys = [k for k in observed['bucket'] if '/upload/' in k]
        assert not keys, ('the upload object is deleted', keys)
        if expect.get('untouched'):
            assert set(observed['models']) == {'instance.DataRestore'}, observed['models'].keys()
            return
        if expect['state'] == 'failed':
            assert op['error'] == expect['error'], op
            assert op['media'] == {}, op
            assert not observed['models'].get('application.Payment'), 'rolled back'
            return
        assert op['attempts'] == expect.get('attempts', 1), op
        assert op['stage'] == 'completed' and op['error'] == '', op
        assert observed['models']['application.Payment']['removed'], 'the current payment is replaced'
        assert len(observed['recovery']) == 1, 'one recovery archive is kept'
        assert not [k for k in observed['bucket'] if str(PRIOR) in k], 'the earlier restore is pruned'
        assert any(e['action'] == 2 for e in observed['audit']) and any(e['action'] == 0 for e in observed['audit']), 'audited'
        cache = observed['cache']
        assert list(cache) == [f':1:data-restore-progress:{OPERATION}'], cache
        assert cache[f':1:data-restore-progress:{OPERATION}']['value']['phase'] == 'completed', cache


def known_uuids(snapshot, archive):
    found = set(UUID_TEXT.findall(json.dumps(snapshot, default=str)))
    found.update(str(U(n)) for n in range(0, 400))
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
    with tempfile.TemporaryDirectory(prefix='az-consumer-restore-', dir='/tmp') as directory, \
            (OUTPUT / 'services.log').open('w') as log, private_minio(log, docker_env, result) as storage:
        work = Path(directory)
        attempted = False
        redis_process = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True,
                                  timeout=180)
        try:
            for generator, plan in (('association_import_plan.py', 'BE_GO/internal/importeffects/plan.json'),
                                    ('association_export_plan.py', 'BE_GO/internal/associationexport/plan.json')):
                path = ROOT / plan
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError('missing plan ' + plan)
                subprocess.run([sys.executable, '-I', str(ROOT / 'TESTS/adapters/go' / generator), '--check', str(path)],
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            subprocess.run([sys.executable, '-I', str(ROOT / 'TESTS/adapters/go/restore_deletion_graph.py'), '--check'],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            result['plans_current'] = True
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
    paths = ['TESTS/adapters/go/celery_consumer_restore_process.py', 'TESTS/adapters/go/restore_deletion_graph.py',
             'TESTS/adapters/go/association_import_plan.py', 'TESTS/adapters/go/association_export_plan.py',
             'TESTS/adapters/go/consumer_restore_settings.py', 'TESTS/adapters/go/consumer_restore_worker.py',
             'BE/instance/tasks.py', 'BE/instance/restore/execution.py', 'BE/instance/restore/archive.py',
             'BE/instance/restore/replacement.py', 'BE/instance/restore/locking.py', 'BE/instance/restore/progress.py',
             'BE/instance/restore/server_barrier.py', 'BE/instance/restore/task_base.py', 'BE/instance/permissions.py',
             'BE/application/services/import_service.py', 'BE/application/services/export_service.py',
             'BE/application/signals.py', 'BE_GO/internal/importeffects/plan.json',
             'BE_GO/internal/associationexport/plan.json', 'BE_GO/internal/repository/restore_deletion_graph.json']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/',
                                                        'internal/importeffects/', 'consumer_restore', 'consumer_import_',
                                                        'consumer_export_', 'restore_deletion', 'data_restore')))
    result.update(success=True, partial=bool(ONLY), django_only=DJANGO_ONLY, race_instrumented=not DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_hashes', 'scope', 'archives')}, default=str))


if __name__ == '__main__':
    main()
