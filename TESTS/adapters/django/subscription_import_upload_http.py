"""Parent-only subscription/import/upload HTTP runner; imports are inert.

service_context is documents-writes' private MinIO (bucket, sentinel object,
S3Boto3Storage for Django and the same AWS_* settings for Go). seed_rows
inserts the status rows and one Document with its workbook under
<str(creation_date.timestamp())>/<id>/<filename> ('$document:stored', removed
by the case that releases it). Each case is checked against the complete model
state and the bucket: an upload creates exactly one Document (filename, uuid4
id and token, creation date within the request) with its auditlog create entry
indexed to the actor's association, and one object under django-storages' key
for subscription-import/<association id>/<str(creation_date.timestamp())>/
<id>/<filename> with the uploaded bytes, mimetypes' ContentType and
CacheControl; a 200 names that Document. Created
objects are removed once verified, so the bucket ends at its baseline; an
upload marked 'keep' leaves its object for the imports that name it, and the
case that lists it in 'release' removes it.

An import ('kind': 'import') is checked against the seeded
AssociateImportDraftStatus rows (the association's IMPORTING ones are deleted,
one is created and deleted), the MedicalCertificate rows it keeps, the
AssociateImportDraft rows bulk_create stores (no audit entries) and the
auditlog entries in order: status deletes, status create, each certificate's
create with its update or delete, status delete.
"""
from datetime import date, datetime, timedelta, timezone
import base64
import ipaddress
import json
import os

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
DRAFT, STATUS, CERTIFICATE = 'application.AssociateImportDraft', 'application.AssociateImportDraftStatus', 'application.MedicalCertificate'
MANAGED = (DOCUMENT, DRAFT, STATUS, CERTIFICATE, harness.AUDIT, harness.INDEX)
# label -> object key of the kept uploads.
KEPT = {}
# SportAssociation.objects.filter(user=actor).first(): a collaborator owns none.
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association', 'foreign_owner': 'foreign_association'}
service_context = uploads.service_context


def document_model():
    from docmanager.models import Document
    return Document


def import_models():
    from application.models.subscriptions_models import AssociateImportDraft, AssociateImportDraftStatus, MedicalCertificate
    return AssociateImportDraft, AssociateImportDraftStatus, MedicalCertificate


def seed_rows(source, created):
    status = import_models()[1]
    for row in source['statuses'].values():
        status._base_manager.bulk_create([status(associate_import_draft_status_id=row['id'], sport_association_id=row['association'],
                                                 doing=row['doing'], status_type=row['type'],
                                                 creation_date=datetime.fromisoformat(row['created'].replace('Z', '+00:00')))])
        created.append((status, row['id']))
    contract.DOCUMENTS.clear()
    KEPT.clear()
    stored, document = source['stored'], document_model()
    moment = datetime.fromisoformat(stored['created'].replace('Z', '+00:00'))
    document._base_manager.bulk_create([document(document_id=stored['id'], token=stored['token'], filename=stored['filename'],
                                                 creation_date=moment)])
    created.append((document, stored['id']))
    key = uploads.object_key(moment, stored['id'], stored['filename'])
    uploads.STORAGE['client'].put_object(Bucket=uploads.BUCKET, Key=key, Body=base64.b64decode(stored['body_b64']),
                                         ContentType=stored['content_type'])
    KEPT['stored'], contract.DOCUMENTS['stored'] = key, stored['id']


def import_key(created, association, document_id, filename):
    """django-storages' key of an import upload: the association's import folder, then the document's own."""
    from storages.utils import clean_name, safe_join
    return safe_join(uploads.STORAGE['location'],
                     clean_name(os.path.join('', 'subscription-import', association, str(created.timestamp()), document_id, filename)))


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(document_model, lambda: import_models()[0],
                                                                         lambda: import_models()[1], lambda: import_models()[2]))
dumps = lambda value: json.dumps(value, sort_keys=True)


def placed(value, certificates):
    """value with each '$mc:<label>' leaf replaced by that certificate's id."""
    if isinstance(value, str) and value.startswith('$mc:'):
        return certificates[value[4:]]
    if isinstance(value, dict):
        return {k: placed(v, certificates) for k, v in value.items()}
    if isinstance(value, list):
        return [placed(v, certificates) for v in value]
    return value


def release(effect):
    for label in effect.get('release', ()):
        uploads.STORAGE['client'].delete_object(Bucket=uploads.BUCKET, Key=KEPT.pop(label))
        contract.DOCUMENTS.pop(label)


def assert_import(case, before, after, source, context, added, indexes):
    """The effect of an import (see the module docstring)."""
    require = contract.require
    ids, fixture, effect = source['ids'], context['fixture'], case['effect']
    own, owner, actor = ids['own_association'], ids['owner'], ids[effect['actor']]
    window = (context['prepared']['started'], datetime.now(timezone.utc) + timedelta(seconds=1))
    require(before[DOCUMENT] == after[DOCUMENT] and uploads.listing() == context['prepared']['objects'], 'state.documents')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': fixture['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    events = iter(sorted(added, key=lambda event: event['id']))
    seen_indexes = 0

    def entry(model, action, association, path):
        nonlocal seen_indexes
        event = next(events, None)
        require(event is not None, 'audit.count')
        key = event['object_pk']
        require(event['content_type_id'] == types[model.lower()] and event['action'] == action and uploads.uuid4_text(key) == key
                and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
        require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if association is None:
            require(index is None, 'index.absent')
        else:
            seen_indexes += 1
            require(index is not None and index['sport_association_id'] == association and index['resolution_path'] == path, 'index.fields')
        return key, event['changes']

    def moment(text):
        value = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        require(window[0] <= value <= window[1] and auditlog_moment(value) == text, 'audit.moment')
        return value

    # Statuses: the association's IMPORTING rows are deleted (the collector's order is not fixed), one is created.
    old_s, new_s = keyed(before[STATUS], 'associate_import_draft_status_id'), keyed(after[STATUS], 'associate_import_draft_status_id')
    gone = {source['statuses'][label]['id'] for label in effect['statuses']}
    require(new_s == {k: v for k, v in old_s.items() if k not in gone}, 'state.statuses')
    deleted = {}
    for _ in gone:
        key, changes = entry('AssociateImportDraftStatus', 2, own, 'changes_json.sport_association')
        deleted[key] = changes
    require(set(deleted) == gone, 'audit.status-deletes')
    for key, changes in deleted.items():
        row = old_s[key]
        require(changes == {'associate_import_draft_status_id': [key, 'None'], 'sport_association': [own, 'None'],
                            'doing': [str(row['doing']), 'None'], 'status_type': [str(row['status_type']), 'None'],
                            'creation_date': [auditlog_moment(datetime.fromisoformat(str(row['creation_date']).replace('Z', '+00:00'))), 'None']},
                'audit.status-delete')
    status, changes = entry('AssociateImportDraftStatus', 0, own, 'direct')
    require(set(changes) == {'associate_import_draft_status_id', 'sport_association', 'doing', 'status_type', 'creation_date'}
            and changes['associate_import_draft_status_id'] == ['None', status] and changes['sport_association'] == ['None', own]
            and changes['doing'] == ['None', 'True'] and changes['status_type'] == ['None', '1']
            and changes['creation_date'][0] == 'None', 'audit.status-create')
    status_created = changes['creation_date'][1]
    moment(status_created)

    # Certificates, in row order: created for request.user, then the expiration saved, or deleted.
    old_c, new_c = keyed(before[CERTIFICATE], 'medical_id'), keyed(after[CERTIFICATE], 'medical_id')
    require(all(new_c.get(k) == v for k, v in old_c.items()), 'state.certificates-kept')
    actor_association = ids[ACTOR_ASSOCIATION[effect['actor']]] if effect['actor'] in ACTOR_ASSOCIATION else None
    actor_path = 'actor.sport_association' if actor_association else None
    certificates = {}
    for step in effect['steps']:
        key, changes = entry('MedicalCertificate', 0, actor_association, actor_path)
        require(set(changes) == {'medical_id', 'document', 'user', 'creation_date', 'competitive_medical_certificate'}
                and changes['medical_id'] == ['None', key] and changes['document'] == ['None', 'None'] and changes['user'] == ['None', owner]
                and changes['competitive_medical_certificate'] == ['None', 'False'] and changes['creation_date'][0] == 'None',
                'audit.certificate-create')
        created = moment(changes['creation_date'][1])
        if step[0] == 'deleted':
            again, changes = entry('MedicalCertificate', 2, actor_association, actor_path)
            require(again == key and key not in new_c, 'state.certificate-deleted')
            require(changes == {'medical_id': [key, 'None'], 'document': ['None', 'None'], 'user': [owner, 'None'],
                                'creation_date': [auditlog_moment(created), 'None'],
                                'competitive_medical_certificate': ['False', 'None']}, 'audit.certificate-delete')
            continue
        _, label, expiration = step
        again, changes = entry('MedicalCertificate', 1, actor_association, actor_path)
        require(again == key and changes == {'expiration_date': ['None', f'{expiration} 00:00:00+00:00']}, 'audit.certificate-update')
        row = new_c.get(key)
        require(row is not None and key not in old_c and str(row['user_id']) == owner and row['document_id'] is None and row['notes'] is None
                and row['competitive_medical_certificate'] is False and row['expiration_date'] == date.fromisoformat(expiration)
                and datetime.fromisoformat(str(row['creation_date']).replace('Z', '+00:00')) == created, 'state.certificate')
        certificates[label] = key
    require(set(new_c) - set(old_c) == set(certificates.values()), 'state.certificates')

    again, changes = entry('AssociateImportDraftStatus', 2, own, 'changes_json.sport_association')
    require(again == status and changes == {'associate_import_draft_status_id': [status, 'None'], 'sport_association': [own, 'None'],
                                            'doing': ['True', 'None'], 'status_type': ['1', 'None'],
                                            'creation_date': [status_created, 'None']}, 'audit.status-end')
    require(next(events, None) is None, 'audit.count')
    require(len(indexes) == seen_indexes, 'index.count')

    # Drafts: bulk_create, in any order.
    old_d, new_d = keyed(before[DRAFT], 'associate_import_draft_id'), keyed(after[DRAFT], 'associate_import_draft_id')
    require(all(new_d.get(k) == v for k, v in old_d.items()), 'state.drafts-kept')
    fresh = [new_d[k] for k in set(new_d) - set(old_d)]
    require(all(uploads.uuid4_text(str(row['associate_import_draft_id'])) and str(row['sport_association_id']) == own for row in fresh),
            'state.draft-identity')
    stored = sorted((dumps(row['data']), row['valid']) for row in fresh)
    expected = sorted((dumps(placed(draft['data'], certificates)), draft['valid']) for draft in effect['drafts'])
    require(stored == expected, 'state.drafts')


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
    if effect is None or effect.get('kind') == 'release':
        require(not fresh and not added and not indexes and not objects, 'state.no-effect')
        require(all(before[k] == after[k] for k in (DRAFT, STATUS, CERTIFICATE)), 'state.no-import-effect')
        release(effect or {})
        return
    if effect.get('kind') == 'import':
        assert_import(case, before, after, source, context, added, indexes)
        release(effect)
        return
    require(all(before[k] == after[k] for k in (DRAFT, STATUS, CERTIFICATE)), 'state.no-import-effect')
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
    object_key = import_key(created, ids[effect['association']], key, effect['filename'])
    require(objects == {object_key}, 'storage.keys')
    client = uploads.STORAGE['client']
    stored = client.get_object(Bucket=uploads.BUCKET, Key=object_key)
    require(stored['Body'].read() == base64.b64decode(effect['body_b64']), 'storage.bytes')
    require(stored['ContentType'] == effect['content_type'] and stored.get('ContentEncoding') is None
            and stored.get('CacheControl') == 'max-age=86400' and not stored.get('Metadata'), 'storage.metadata')
    if effect.get('keep'):
        KEPT[effect['keep']], contract.DOCUMENTS[effect['keep']] = object_key, key
        return
    client.delete_object(Bucket=uploads.BUCKET, Key=object_key)


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)


def run_workload(*args):
    # A failed workload keeps its objects; only complete runs must restore the bucket.
    uploads.STORAGE['pending'] += 1
    result = _run_workload(*args)
    uploads.STORAGE['pending'] -= 1
    return result
