"""Parent-only subscription/import/upload HTTP runner; imports are inert.

service_context is documents-writes' private MinIO (bucket, sentinel object,
S3Boto3Storage for Django and the same AWS_* settings for Go). Nothing is
seeded. Each case is checked against the complete model state and the bucket:
an upload creates exactly one Document (filename, uuid4 id and token, creation
date within the request) with its auditlog create entry indexed to the actor's
association, and one object under django-storages' key for
<str(creation_date.timestamp())>/<id>/<filename> with the uploaded bytes,
mimetypes' ContentType and CacheControl; a 200 names that Document. Created
objects are removed once verified, so the bucket ends at its baseline.
"""
from datetime import datetime, timedelta, timezone
import base64
import ipaddress

from TESTS.contracts import test_subscription_import_upload_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import documents_writes_http as uploads
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_import_upload_http.json', 'TESTS/contracts/test_subscription_import_upload_http.py',
           'TESTS/adapters/django/subscription_import_upload_http.py', 'TESTS/adapters/django/documents_writes_http.py',
           'TESTS/adapters/django/instance_logo.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/services/subscription_service.py', 'BE/application/utils/api_utils.py', 'BE/docmanager/models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py', 'BE/core/settings.py') + harness.SOURCES
DOCUMENT = 'docmanager.Document'
MANAGED = (DOCUMENT, harness.AUDIT, harness.INDEX)
# SportAssociation.objects.filter(user=actor).first(): a collaborator owns none.
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association'}
service_context = uploads.service_context


def document_model():
    from docmanager.models import Document
    return Document


def seed_rows(source, created):
    pass


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(document_model,))


def prepare(source):
    return {'started': datetime.now(timezone.utc) - timedelta(seconds=1), 'objects': uploads.listing()}


def assert_effect(case, before, after, source, context):
    require = contract.require
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids, fixture = source['ids'], context['fixture']
    window = (context['prepared']['started'], datetime.now(timezone.utc) + timedelta(seconds=1))
    objects = uploads.listing() - context['prepared']['objects']
    old, new = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    require(all(new.get(k) == v for k, v in old.items()), 'state.kept')
    fresh = [new[k] for k in set(new) - set(old)]
    added, indexes = harness.audit_delta(contract, before, after)
    effect = case['effect']
    if effect is None:
        require(not fresh and not added and not indexes and not objects, 'state.no-effect')
        return
    label = ACTOR_ASSOCIATION.get(effect['actor'])
    require(len(fresh) == 1 and len(added) == 1 and len(indexes) == (1 if label else 0), 'state.document-count')
    row, event = fresh[0], added[0]
    key, token = uploads.uuid4_text(str(row['document_id'])), uploads.uuid4_text(str(row['token']))
    created = datetime.fromisoformat(str(row['creation_date']).replace('Z', '+00:00'))
    require(window[0] <= created <= window[1] and row['filename'] == effect['filename'] and row['filepath'] is None
            and row['file_size_bytes'] is None, 'state.document')
    if case['status'] == 200:
        require(contract.RESPONSE.get('document_id') == key, 'response.document-id')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'docmanager'}
    require(event['content_type_id'] == types['document'] and event['action'] == 0 and event['object_pk'] == key
            and event['object_repr'] == f'Document object ({key})', 'audit.identity')
    require(event['changes'] == {'document_id': ['None', key], 'creation_date': ['None', auditlog_moment(created)],
                                 'filename': ['None', effect['filename']], 'token': ['None', token],
                                 'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': fixture['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    require(label is None or (index is not None and index['sport_association_id'] == ids[label]
                              and index['resolution_path'] == 'actor.sport_association'), 'index.fields')
    object_key = uploads.object_key(created, key, effect['filename'])
    require(objects == {object_key}, 'storage.keys')
    client = uploads.STORAGE['client']
    stored = client.get_object(Bucket=uploads.BUCKET, Key=object_key)
    require(stored['Body'].read() == base64.b64decode(effect['body_b64']), 'storage.bytes')
    require(stored['ContentType'] == effect['content_type'] and stored.get('ContentEncoding') is None
            and stored.get('CacheControl') == 'max-age=86400' and not stored.get('Metadata'), 'storage.metadata')
    client.delete_object(Bucket=uploads.BUCKET, Key=object_key)


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)


def run_workload(*args):
    # A failed workload keeps its objects; only complete runs must restore the bucket.
    uploads.STORAGE['pending'] += 1
    result = _run_workload(*args)
    uploads.STORAGE['pending'] -= 1
    return result
