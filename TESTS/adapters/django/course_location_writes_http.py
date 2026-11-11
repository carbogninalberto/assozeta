"""Parent-only course location writes HTTP runner; imports are inert.

service_context owns a private MinIO (documents_writes_http's setup) and
switches Django's default storage to S3Boto3Storage on it before either
server starts. documents_data uploads are checked as new Documents (bound to
their auditlog create entries in write order, uuid4 keys, recent timestamps),
their links to the location and the stored objects (django-storages' own key,
exact bytes and metadata), which are removed once verified.

Seeds the course-locations list rows and documents (audit disabled) and
verifies each case against the complete model state: location creates,
updates and deletes with one auditlog entry each (none for an unchanged
update), document links removed only with their location and documents
kept. New locations are removed before the fixture cleanup compares the
model state.
"""
from TESTS.contracts import test_course_location_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import course_locations_list_http as locations
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import os
import uuid

SOURCES = ('TESTS/fixtures/course_location_writes_http.json', 'TESTS/contracts/test_course_location_writes_http.py',
           'TESTS/adapters/django/course_location_writes_http.py', 'TESTS/adapters/django/course_locations_list_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py', 'TESTS/adapters/django/instance_logo.py', 'BE/docmanager/models.py',
           'BE/core/settings.py') + harness.SOURCES
BUCKET = 'course-locations-fixture'
STORAGE = {}
DOCUMENT = 'docmanager.Document'
LOCATION, LINK = 'application.CourseLocation', 'application.CourseLocation_documents'
MANAGED = (LOCATION, LINK, 'docmanager.Document', harness.AUDIT, harness.INDEX)
FIELDS = ('title', 'description', 'address')


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = contract.load_source()
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret, region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, location=source['location'])
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'):
                yield
        finally:
            STORAGE.clear()


def objects():
    """The bucket's keys; families reusing this effect check without the storage service see none."""
    if not STORAGE:
        return set()
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def object_key(created, document_id, filename):
    from storages.utils import clean_name, safe_join
    return safe_join(STORAGE['location'], clean_name(os.path.join('', str(created.timestamp()), document_id, filename)))


def check_uploads(case, effect, before, after, key, added):
    """The new Documents (create entries in write order), their links to the location and stored objects."""
    specs = effect.get('uploads', [])
    old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    contract.require(all(new_d.get(k) == v for k, v in old_d.items()), 'state.documents-kept')
    fresh = set(new_d) - set(old_d)
    events = [e for e in added if e['action'] == 0 and e['changes'].get('customer_invoice_pdf')]
    contract.require(len(fresh) == len(specs) == len(events), 'state.document-count')
    now = datetime.now(timezone.utc)
    expected_objects, links = {}, set()
    for event, spec in zip(events, specs):
        changes = event['changes']
        contract.require(set(changes) == {'document_id', 'creation_date', 'filename', 'token', 'customer_invoice_pdf'}
                         and all(pair[0] == 'None' for pair in changes.values())
                         and changes['customer_invoice_pdf'][1] == 'application.CustomerInvoice.None', 'audit.document-create')
        ident = changes['document_id'][1]
        contract.require(str(uuid.UUID(ident)) == ident and uuid.UUID(ident).version == 4 and ident in fresh, 'state.uuid4')
        stamp = datetime.fromisoformat(changes['creation_date'][1]).replace(tzinfo=timezone.utc)
        contract.require(abs(now - stamp) < timedelta(minutes=10) and changes['filename'][1] == spec['filename'], 'audit.document-values')
        row = new_d[ident]
        contract.require(row['filename'] == spec['filename'] and row['filepath'] is None and row['file_size_bytes'] is None
                         and row['token'] == changes['token'][1], 'state.document-row')
        contract.require(event['object_repr'] == f'Document object ({ident})', 'audit.document-repr')
        expected_objects[object_key(stamp, ident, spec['filename'])] = spec
        links.add((key, ident))
    contract.require(objects() == set(expected_objects), 'storage.keys')
    client = STORAGE.get('client')
    for name, spec in expected_objects.items():
        stored = client.get_object(Bucket=BUCKET, Key=name)
        contract.require(stored['Body'].read() == base64.b64decode(spec['body_b64']), 'storage.bytes')
        contract.require(stored['ContentType'] == spec['content_type'] and stored.get('ContentEncoding') == spec['encoding']
                         and stored.get('CacheControl') == 'max-age=86400', 'storage.metadata')
        client.delete_object(Bucket=BUCKET, Key=name)
    return links, [e for e in added if e not in events]


def location_model():
    from application.models.courses_models import CourseLocation
    return CourseLocation


def deleted_rows(source):
    """auditlog's delete diffs of the seeded locations, captured before each case."""
    model = location_model()
    return {str(row.pk): replace.deleted_changes(model, row)
            for row in model._base_manager.filter(pk__in=[row['id'] for row in source['locations'].values()])}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(not objects(), 'storage.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_l, new_l = keyed(before[LOCATION], 'course_location_id'), keyed(after[LOCATION], 'course_location_id')
    stored = lambda row: {field: row[field] for field in FIELDS}
    if effect['kind'] == 'add':
        keys = set(new_l) - set(old_l)
        contract.require(len(keys) == 1 and all(new_l[k] == old_l[k] for k in old_l), 'state.created')
        key = keys.pop()
        contract.require(effect['id'] == '$new' or key == effect['id'], 'state.created-id')
        contract.require(stored(new_l[key]) == effect['stored'] and str(new_l[key]['sport_association_id']) == own, 'state.location')
        action, path = 0, 'direct'
        changes = topup.created_changes(location_model(), location_model()._base_manager.get(pk=key), {})
    elif effect['kind'] == 'update':
        key = source['locations'][effect['label']]['id']
        contract.require(set(new_l) == set(old_l) and stored(new_l[key]) == effect['stored']
                         and str(new_l[key]['sport_association_id']) == own
                         and all(new_l[k] == old_l[k] for k in old_l if k != key), 'state.location')
        action, path, changes = 1, 'direct', effect['changes']
    elif effect['kind'] == 'copy':
        # Model.save() under a key no row has: the path's row stays, the validated values are inserted under the new key.
        key = effect['id']
        contract.require(set(new_l) == set(old_l) | {key} and all(new_l[k] == old_l[k] for k in old_l)
                         and stored(new_l[key]) == effect['stored'] and str(new_l[key]['sport_association_id']) == own, 'state.copy')
        action, path = 0, 'direct'
        changes = topup.created_changes(location_model(), location_model()._base_manager.get(pk=key), {})
    else:
        key = source['locations'][effect['label']]['id']
        contract.require(set(new_l) == set(old_l) - {key}, 'state.deleted')
        kept = {pair for pair in harness.pairs(before[LINK], 'courselocation_id', 'document_id') if pair[0] != key}
        contract.require(harness.pairs(after[LINK], 'courselocation_id', 'document_id') == kept and len(after[LINK]) == len(kept),
                         'state.links')
        action, path, changes = 2, 'changes_json.sport_association', context['prepared'][key]
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: int(event['id']))
    if effect['kind'] != 'delete':
        links, added = check_uploads(case, effect, before, after, key, added)
        old_pairs = harness.pairs(before[LINK], 'courselocation_id', 'document_id')
        contract.require(harness.pairs(after[LINK], 'courselocation_id', 'document_id') == old_pairs | links
                         and len(after[LINK]) == len(before[LINK]) + len(links), 'state.links')
        # Document create entries are indexed to the actor's own association (the owner's), the location's entry directly.
        contract.require(len(indexes) == len(added) + (len(links) if effect['actor'] == 'owner' else 0), 'index.count')
    else:
        contract.require(len(indexes) == len(added), 'index.count')
    if effect['kind'] == 'copy':
        # auditlog's pre_save finds no row under the new key: the whole row is logged as an update (indexed from its
        # changes), then the create (indexed directly).
        contract.require(len(added) == 2, 'audit.count')
        expected = [(1, 'changes_json.sport_association'), (0, 'direct')]
    else:
        contract.require(len(added) == (1 if changes else 0), 'audit.count')
        expected = [(action, path)] * len(added)
    for event, (event_action, event_path) in zip(added, expected):
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='CourseLocation', key=key,
                                  action=event_action, changes=changes, actor=effect['actor'], association=own, path=event_path, context=context)


def document_model():
    from docmanager.models import Document
    return Document


fixture_context = harness.fixture_context_for(contract, locations.seed_rows, purge=(location_model, document_model))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
