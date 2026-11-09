"""Parent-only docmanager-reads HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run: it creates the bucket, stores the authored objects under
AWS_LOCATION and switches Django's default storage to S3Boto3Storage on that
endpoint before either server starts, so the Go server receives the same
AWS_* settings. Objects are only read, so they are shared by all transports.

seed_rows inserts (audit disabled) the authored Document rows with their
archive rows, subscriptions, medical certificates and invoices. Every case is
a read or a refusal: the complete model state and the cache must be
unchanged. Download tokens are issued per workload with django.core.signing
(fresh, expired, tampered, compressed and crafted payloads).
"""
import base64
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import json
import os
import time
import zlib

from TESTS.contracts import test_docmanager_reads_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/docmanager_reads_http.json', 'TESTS/contracts/test_docmanager_reads_http.py',
           'TESTS/adapters/django/docmanager_reads_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py', 'BE/docmanager/views/document_view.py',
           'BE/docmanager/download_tokens.py', 'BE/docmanager/models.py', 'BE/application/document_scope.py',
           'BE/application/utils/printing.py', 'BE/application/impersonation_scope.py', 'BE/application/mixin.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/user_models.py', 'BE/application/permissions_registry.py',
           'BE/core/middleware.py') + harness.SOURCES
BUCKET = 'docmanager-reads-fixture'


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
        for stored in source['objects'].values():
            extra = {'ContentEncoding': stored['encoding']} if stored['encoding'] else {}
            client.put_object(Bucket=BUCKET, Key=stored['key'], Body=base64.b64decode(stored['body_b64']),
                              ContentType=stored['content_type'], **extra)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                               AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                               AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'):
            yield
        listed = sorted(item['Key'] for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []))
        contract.require(listed == sorted(stored['key'] for stored in source['objects'].values()), 'storage.objects-unchanged')


def seed_rows(source, created):
    from application.models import Invoice, Subscription
    from application.models.subscriptions_models import MedicalCertificate
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    ids, documents = source['ids'], source['documents']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    def document(key):
        return documents[key]['id'] if key else None

    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], filepath=row['filepath'],
                                  creation_date=datetime.fromisoformat(row['created'])))
    for row in source['archives'].values():
        insert(SportAssociationDocumentsArchive, SportAssociationDocumentsArchive(
            sport_association_documents_archive_id=row['id'], sport_association_id=ids[row['association']],
            document_id=document(row['document']), date=date.fromisoformat(row['date'])))
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=ids[row['association']],
                                          user_id=ids[row['user']], document_pdf_id=document(row['document']),
                                          group_id=ids[row['group']] if row['group'] else None))
    for row in source['medicals'].values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], document_id=document(row['document'])))
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=ids[row['association']],
                                document_pdf_id=document(row['document']), membership_fee=Decimal('0.00'),
                                activity_fee=Decimal('10.00'), deleted=row['deleted']))


def download_tokens(source):
    """Tokens signed with the running SECRET_KEY, valid for this workload."""
    from django.core import signing
    from docmanager.download_tokens import DOWNLOAD_TOKEN_PURPOSE, DOWNLOAD_TOKEN_SALT, create_document_download_token
    documents, own = source['documents'], source['ids']['own_association']
    signer = signing.Signer(salt=DOWNLOAD_TOKEN_SALT)

    def payload(key, association=own, purpose=DOWNLOAD_TOKEN_PURPOSE):
        return {'document_id': documents[key]['id'], 'sport_association_id': association, 'purpose': purpose}

    def segment(value, compress=False):
        raw = json.dumps(value, separators=(',', ':')).encode()
        if compress:
            return '.' + base64.urlsafe_b64encode(zlib.compress(raw)).rstrip(b'=').decode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    def timed(value, moment):
        return signer.sign(f'{value}:{signing.b62_encode(moment)}')

    now = int(time.time())
    export = create_document_download_token(documents['export']['id'], own)
    tampered = export[:-1] + ('A' if export[-1] != 'A' else 'B')
    return {
        'export': export,
        'compressed': timed(segment(payload('export'), compress=True), now),
        'expired': timed(segment(payload('export')), now - 7200),
        'tampered': tampered,
        'wrong_purpose': signing.dumps(payload('export', purpose='other'), salt=DOWNLOAD_TOKEN_SALT),
        'wrong_salt': signing.dumps(payload('export'), salt='docmanager.other'),
        'not_dict': signing.dumps([documents['export']['id']], salt=DOWNLOAD_TOKEN_SALT),
        'empty_association': signing.dumps(payload('export', ''), salt=DOWNLOAD_TOKEN_SALT),
        'foreign_export_own_association': create_document_download_token(documents['export_foreign']['id'], own),
        'plain_own_association': create_document_download_token(documents['plain']['id'], own),
        'int_association': signing.dumps(payload('export', 5), salt=DOWNLOAD_TOKEN_SALT),
        'malformed_association': signing.dumps(payload('export', 'not-a-uuid'), salt=DOWNLOAD_TOKEN_SALT),
        'without_timestamp': signer.sign(segment(payload('export'))),
        'bad_timestamp': signer.sign(segment(payload('export')) + ':!!'),
    }


fixture_context = harness.fixture_context_for(contract, seed_rows)
_run_workload = harness.run_workload_for(contract)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    contract.RUNTIME.clear()
    contract.RUNTIME.update(tokens=fixture['tokens'], download_tokens=download_tokens(contract.load_source()))
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        contract.RUNTIME.clear()
