"""Parent-only docmanager-reads HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run: it creates the bucket, stores the authored objects under
AWS_LOCATION and switches Django's default storage to S3Boto3Storage on that
endpoint before either server starts, so the Go server receives the same
AWS_* settings. Objects are only read, so they are shared by all transports.

seed_rows inserts (audit disabled) the authored Document rows with their
archive rows, associates, subscriptions, medical certificates, attached files,
invoices and payments, plus a self-hosted InstanceConfiguration whose primary
association belongs to an athlete account. Every case is
a read or a refusal: the complete model state and the cache must be
unchanged.

A stored PDF whose object is missing is printed again: service_context also
owns the renderer stub of document_render_http (scripted per case, every
request compared with the authored FastRender payload), and the fixture
publishes Celery tasks to the run's broker. Such a case must publish exactly
one save_to_storage('<creation timestamp>/<id>/<filename>', base64 PDF), or
nothing when the renderer fails. With effect.store the adapter then runs the
task itself (docmanager.tasks.save_to_storage, as the worker would), so the
next case streams the stored PDF. The objects stored this way are removed
before and after each workload. Download tokens are issued per workload with django.core.signing
(fresh, expired, tampered, compressed and crafted payloads).
"""
import base64
import types
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import json
import os
import time
import zlib

from TESTS.contracts import test_docmanager_reads_http as base_contract
from TESTS.adapters.django import document_render_http as renderer
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/docmanager_reads_http.json', 'TESTS/contracts/test_docmanager_reads_http.py',
           'TESTS/adapters/django/docmanager_reads_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py', 'TESTS/adapters/django/document_render_http.py',
           'TESTS/contracts/test_document_render_http.py', 'BE/docmanager/tasks.py', 'BE/docmanager/views/document_view.py',
           'BE/docmanager/access.py', 'BE/instance/permissions.py', 'BE/docmanager/download_tokens.py', 'BE/docmanager/models.py', 'BE/application/document_scope.py',
           'BE/application/utils/printing.py', 'BE/application/impersonation_scope.py', 'BE/application/mixin.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/user_models.py', 'BE/application/permissions_registry.py',
           'BE/core/middleware.py') + harness.SOURCES
BUCKET = 'docmanager-reads-fixture'
STORAGE = {}


def _assert_case(case, base_url, *, token, sessions):
    with renderer.STUB['lock']:
        renderer.STUB['log'] = []
        renderer.STUB['script'] = [item['reply'] for item in case['renders']]
    renderer.STUB['token'] = token
    return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


# The harness drives this proxy: the renderer stub is scripted with the case's replies before each request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure, RUNTIME=base_contract.RUNTIME)


def authored_keys(source):
    return sorted(stored['key'] for stored in source['objects'].values())


def remove_stored(source):
    """Delete the objects the save_to_storage step stored; the authored ones must all remain."""
    client = STORAGE['client']
    listed = sorted(item['Key'] for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []))
    authored = authored_keys(source)
    contract.require(authored and set(authored) <= set(listed), 'storage.authored-objects')
    for key in listed:
        if key not in authored:
            client.delete_object(Bucket=BUCKET, Key=key)


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
        STORAGE['client'] = client
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'), \
                    renderer.renderer_context(report, log):
                yield
            listed = sorted(item['Key'] for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []))
            contract.require(listed == authored_keys(source), 'storage.objects-unchanged')
        finally:
            STORAGE.clear()


def seed_rows(source, created):
    from application.models import Associate, Invoice, Payment, SportAssociation, Subscription
    from application.models.subscriptions_models import MedicalCertificate, SubscriptionFile
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    from instance.models import InstanceConfiguration
    ids, documents = dict(source['ids']), source['documents']
    ids.update({label: row['id'] for label, row in source['extra_users'].items()})
    associates, medicals, subscriptions, invoices = (source[key] for key in ('associates', 'medicals', 'subscriptions', 'invoices'))

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
    primary = source['primary']
    insert(SportAssociation, SportAssociation(sport_association_id=primary['association'], user_id=ids[primary['user']],
                                              denomination=primary['denomination']))
    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    insert(InstanceConfiguration, InstanceConfiguration(primary_association_id=primary['association'], **source['instance']))
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=ids[row['association']],
                                    user_id=ids[row['user']] if row['user'] else None, tax_code=row['tax_code']))
    for row in medicals.values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], document_id=document(row['document']),
                                                      user_id=ids[row['user']] if row.get('user') else None))
    for row in subscriptions.values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=ids[row['association']],
                                          user_id=ids[row['user']], document_pdf_id=document(row['document']),
                                          group_id=ids[row['group']] if row['group'] else None,
                                          associate_id=associates[row['associate']]['id'] if row.get('associate') else None,
                                          medical_id=medicals[row['medical']]['id'] if row.get('medical') else None))
    for row in source['files'].values():
        insert(SubscriptionFile, SubscriptionFile(subscription_file_id=row['id'], subscription_id=subscriptions[row['subscription']]['id'],
                                                  document_id=document(row['document'])))
    for row in invoices.values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=ids[row['association']],
                                document_pdf_id=document(row['document']), membership_fee=Decimal('0.00'),
                                activity_fee=Decimal('10.00'), deleted=row['deleted']))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=ids[row['association']], invoice_id=invoices[row['invoice']]['id'],
                                user_id=ids[row['user']], amount=Decimal('10.00'),
                                associate_id=associates[row['associate']]['id'] if row['associate'] else None))


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


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))


def assert_effect(case, before, after, source, context):
    """Nothing is written. A case that prints a missing PDF again sends the authored renderer requests and
    publishes the storage task of the document; effect.store then runs the task as the worker would."""
    require = contract.require
    require(before == after, 'state.unchanged')
    filled = {**case, 'renders': [{**item, 'authorization': base_contract.fill(item['authorization'])} for item in case['renders']]}
    renderer.check_renderer(filled)
    effect = case['effect'] or {'published': None, 'store': False}
    expected = []
    if effect['published']:
        row = source['documents'][effect['published']]
        stored = f"{datetime.fromisoformat(row['created']).timestamp()}/{row['id']}/{row['filename']}"
        expected.append({'task': 'save_to_storage', 'args': [stored, base64.b64encode(renderer.PDF_BYTES).decode()], 'kwargs': []})
    require(bool(expected) or not effect['store'], 'fixture.store-without-task')
    checked = harness.celery_published(contract, context['fixture'], expected, since=context['prepared']['started'], until=time.time())
    if effect['store']:
        from docmanager.tasks import save_to_storage
        from kombu.utils.json import loads
        args, kwargs, _ = loads(base64.b64decode(checked[0]['body']))
        require(len(args) == 2 and not kwargs, 'celery.storage-arguments')
        save_to_storage.run(*args)
        key = f"{source['location']}/{args[0]}"
        head = STORAGE['client'].head_object(Bucket=BUCKET, Key=key)
        require(head['ContentLength'] == len(renderer.PDF_BYTES), 'storage.stored-object')


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: {'started': time.time()})


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    source = contract.load_source()
    contract.RUNTIME.clear()
    contract.RUNTIME.update(tokens=fixture['tokens'], download_tokens=download_tokens(source), pdf=renderer.PDF_BYTES)
    remove_stored(source)
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        remove_stored(source)
        contract.RUNTIME.clear()
