"""Parent-only document upload HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run: it creates an empty bucket plus one sentinel object and
switches Django's default storage to S3Boto3Storage on that endpoint before
either server starts, so the Go server receives the same AWS_* settings.

seed_rows inserts (audit disabled) folders, documents, archive rows, a
medical certificate, subscriptions and associate import drafts. Each case is
checked against the complete model state, the bucket listing and the cache:
- new Documents are bound to their auditlog create entries in write order; each
  stored object key is django-storages' own ``safe_join(AWS_LOCATION,
  clean_name(...))`` of ``<str(creation_date.timestamp())>/<id>/<filename>``
  for the stored row, with the exact bytes, ContentType, ContentEncoding and
  CacheControl (ids are uuid4: checked for version and bound, never guessed);
- archive rows, subscription files, certificates, subscription and draft
  updates, and every auditlog entry with its index row are exact;
- an association-scoped medical upload leaves its cache grant (value and TTL).
Created objects and cache grants are removed after each verified case, so the
bucket and the cache must be back to their baseline at the end of the run.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import base64
import json
import os
import pickle
import uuid

from TESTS.contracts import test_documents_writes_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/documents_writes_http.json', 'TESTS/contracts/test_documents_writes_http.py',
           'TESTS/adapters/django/documents_writes_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/archive_views.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/services/subscription_service.py', 'BE/docmanager/views/document_view.py', 'BE/docmanager/models.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/impersonation_scope.py', 'BE/application/impersonation.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/mixin.py', 'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py',
           'BE/core/settings.py', 'BE/core/middleware.py') + harness.SOURCES
BUCKET = 'documents-writes-fixture'
SENTINEL = ('documents-writes/sentinel/keep.txt', b'sentinel')
DOCUMENT, CERTIFICATE, FILE = 'docmanager.Document', 'application.MedicalCertificate', 'application.SubscriptionFile'
ARCHIVE, SUBSCRIPTION, DRAFT = 'application.SportAssociationDocumentsArchive', 'application.Subscription', 'application.AssociateImportDraft'
MANAGED = (DOCUMENT, CERTIFICATE, FILE, ARCHIVE, SUBSCRIPTION, DRAFT, harness.AUDIT, harness.INDEX)
KEYS = {DOCUMENT: 'document_id', CERTIFICATE: 'medical_id', FILE: 'subscription_file_id', ARCHIVE: 'sport_association_documents_archive_id',
        SUBSCRIPTION: 'subscription_id', DRAFT: 'associate_import_draft_id'}
MODELS = {'Document': ('docmanager', DOCUMENT), 'MedicalCertificate': ('application', CERTIFICATE), 'SubscriptionFile': ('application', FILE),
          'SportAssociationDocumentsArchive': ('application', ARCHIVE), 'Subscription': ('application', SUBSCRIPTION),
          'AssociateImportDraft': ('application', DRAFT)}
# The actor's own association (SportAssociation.objects.filter(user=actor).first()).
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association', 'foreign_owner': 'foreign_association'}
SESSION_TTL = 3600
STORAGE = {}
FOLDERS = {}


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
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, location=source['location'], pending=0)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'):
                yield
            # A failed workload is already reported; only complete runs must restore the bucket.
            if STORAGE['pending'] == 0:
                contract.require(listing() == {SENTINEL[0]}, 'storage.objects-restored')
                contract.require(client.get_object(Bucket=BUCKET, Key=SENTINEL[0])['Body'].read() == SENTINEL[1], 'storage.sentinel')
        finally:
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def models():
    from application.models import Folder, Subscription
    from application.models.subscriptions_models import AssociateImportDraft, MedicalCertificate, SubscriptionFile
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    return Folder, Document, SportAssociationDocumentsArchive, MedicalCertificate, Subscription, AssociateImportDraft, SubscriptionFile


moment = lambda text: datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None
day = lambda text: date.fromisoformat(text) if text else None


def seed_rows(source, created):
    folder, document, archive, certificate, subscription, draft, _ = models()
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    FOLDERS.clear()
    for label, association in source['folders']:
        FOLDERS[label] = folder.objects.create(name=f'documents-writes {label}', sport_association_id=association).pk
        created.append((folder, FOLDERS[label]))
    for row in source['documents'].values():
        insert(document, document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=moment(row['created'])))
    for row in source['archives'].values():
        insert(archive, archive(sport_association_documents_archive_id=row['id'], sport_association_id=row['association'],
                                document_id=source['documents'][row['document']]['id'], date=day(row['date']),
                                folder_id=FOLDERS[row['folder']] if row['folder'] else None))
    for row in source['certificates'].values():
        insert(certificate, certificate(medical_id=row['id'], user_id=ids[row['user']], creation_date=moment(row['created']),
                                        document_id=source['documents'][row['document']]['id']))
    for row in source['subscriptions'].values():
        insert(subscription, subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']],
                                          medical_id=source['certificates'][row['medical']]['id'] if row['medical'] else None,
                                          group_id=ids[row['group']] if row['group'] else None, start_date=day(row['start']),
                                          end_date=day(row['end']), creation_date=moment(row['created']), status_flag=subscription.ACCEPTED))
    for row in source['drafts'].values():
        insert(draft, draft(associate_import_draft_id=row['id'], sport_association_id=row['association'], data=row['data']))


# Rows the run creates: subscription files, archive rows, certificates, documents (in that order).
PURGED = tuple((lambda index=index: models()[index]) for index in (6, 2, 3, 1))


def prepare(source):
    """auditlog's delete diffs of the seeded archive rows, captured before each case."""
    archive = models()[2]
    return {str(row.pk): replace.deleted_changes(archive, row)
            for row in archive._base_manager.filter(pk__in=[item['id'] for item in source['archives'].values()])}


def drf_moment(value):
    """DRF DateTimeField rendering (UTC, 'Z', microseconds only when set)."""
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + 'Z'


def resolve(value, refs, folders):
    if isinstance(value, str):
        if value in refs:
            return refs[value]
        for name, ref in refs.items():  # a reference inside rendered JSON text (draft data diffs)
            value = value.replace(json.dumps(name), json.dumps(ref))
        return contract.bind(value, folders)
    if isinstance(value, dict):
        return {k: resolve(v, refs, folders) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, refs, folders) for v in value]
    return value


def uuid4_text(value):
    contract.require(isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4, 'state.uuid4')
    return value


def object_key(created, document_id, filename):
    from storages.utils import clean_name, safe_join
    return safe_join(STORAGE['location'], clean_name(os.path.join('', str(created.timestamp()), document_id, filename)))


def additional_data(case, fixture, ids, actor):
    session = case['headers'].get('X-Impersonation-Id', '')
    if not session.startswith('$session:'):
        return None
    label = session[9:]
    return {'impersonation_session': fixture['sessions'][label], 'actor_id': ids[actor], 'target_user_id': case['headers']['User-Id'],
            'association_id': None if label.startswith('admin_') else ids['own_association']}


def assert_effect(case, before, after, source, context, raw, window):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect, ids, folders = case['effect'], source['ids'], context['folders']
    captured = contract.RUNTIME.get('captured', {})
    objects = listing() - context['objects']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(not objects, 'storage.no-effect')
        return
    state = {table: (keyed(before[table], key), keyed(after[table], key)) for table, key in KEYS.items()}
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: int(event['id']))
    types = {ct['id']: (ct['app_label'], ct['model']) for ct in before['contenttypes.ContentType']}
    type_ids = {value: key for key, value in types.items()}
    creates = lambda app, model: [e for e in added if e['action'] == 0 and types[e['content_type_id']] == (app, model)]
    refs = {}

    # Documents: bound to their create entries in write order.
    doc_events = creates('docmanager', 'document')
    contract.require(len(doc_events) == len(effect['documents']), 'state.document-count')
    old_doc, new_doc = state[DOCUMENT]
    expected_doc, expected_objects, moments = dict(old_doc), {}, {}
    for i, (event, spec) in enumerate(zip(doc_events, effect['documents'])):
        changes = event['changes']
        # auditlog walks _meta.get_fields(): the reverse FK with a related_name renders as its manager.
        contract.require(set(changes) == {'document_id', 'creation_date', 'filename', 'token', 'customer_invoice_pdf'}
                         and all(pair[0] == 'None' for pair in changes.values())
                         and changes['customer_invoice_pdf'][1] == 'application.CustomerInvoice.None', 'audit.document-create')
        key, token = uuid4_text(changes['document_id'][1]), uuid4_text(changes['token'][1])
        stamp = datetime.fromisoformat(changes['creation_date'][1]).replace(tzinfo=timezone.utc)
        contract.require(window[0] <= stamp <= window[1] and changes['filename'][1] == spec['filename'], 'audit.document-values')
        refs.update({f'$doc{i}': key, f'$doc{i}.token': token, f'$doc{i}.created': drf_moment(stamp)})
        moments[key] = stamp
        if spec['kept']:
            expected_doc[key] = {'document_id': key, 'creation_date': drf_moment(stamp), 'filepath': None, 'filename': spec['filename'],
                                 'file_size_bytes': None, 'token': token}
        if spec['body_b64'] is not None:
            expected_objects[object_key(stamp, key, spec['filename'])] = spec
    contract.require(new_doc == expected_doc, 'state.documents')

    # Stored objects: exact keys, bytes and metadata; removed once verified.
    client = STORAGE['client']
    contract.require(objects == set(expected_objects), 'storage.keys')
    for key, spec in expected_objects.items():
        stored = client.get_object(Bucket=BUCKET, Key=key)
        contract.require(stored['Body'].read() == base64.b64decode(spec['body_b64']), 'storage.bytes')
        contract.require(stored['ContentType'] == spec['content_type'] and stored.get('ContentEncoding') == spec['encoding']
                         and stored.get('CacheControl') == 'max-age=86400' and not stored.get('Metadata'), 'storage.metadata')
    for key in expected_objects:
        client.delete_object(Bucket=BUCKET, Key=key)

    # Archive rows created by add (kept or cascaded), updated, inserted or deleted.
    archive_events = creates('application', 'sportassociationdocumentsarchive')
    inserted = effect['archive_inserted']
    contract.require(len(archive_events) == len([e for e in effect['audit'] if e['key'].startswith('$archive') and e['action'] == 0])
                     + (1 if inserted else 0), 'state.archive-count')
    old_a, new_a = state[ARCHIVE]
    expected_a = {k: v for k, v in old_a.items() if k not in {source['archives'][label]['id'] for label in effect['archive_deleted']}}
    archive_dates = {}
    for i, event in enumerate([] if inserted else archive_events):
        key = uuid4_text(event['changes']['sport_association_documents_archive_id'][1])
        refs[f'$archive{i}'] = key
        text = event['changes']['date'][1]
        stamp = datetime.fromisoformat(text)
        contract.require(stamp.utcoffset().total_seconds() == 0 and window[0] <= stamp <= window[1], 'audit.archive-date')
        refs[f'$archive{i}.date'] = stamp.isoformat()
        archive_dates[key] = (text, stamp)
    for i, spec in enumerate(effect['archives']):
        key = refs[f'$archive{i}']
        expected_a[key] = {'sport_association_documents_archive_id': key, 'sport_association_id': ids['own_association'],
                           'document_id': refs[f'$doc{spec["document"]}'], 'date': archive_dates[key][1].date(),
                           'folder_id': folders[spec['folder']] if spec['folder'] else None}
    for label, values in effect['archive_updates'].items():
        key = source['archives'][label]['id']
        expected_a[key] = {**old_a[key], **{k + ('_id' if k == 'folder' else ''): day(v) if k == 'date' else contract.bind(v, folders)
                                            for k, v in values.items()}}
    if inserted:
        origin = old_a[source['archives'][inserted['from']]['id']]
        expected_a[inserted['id']] = {**origin, 'sport_association_documents_archive_id': inserted['id']}
    contract.require(new_a == expected_a, 'state.archives')

    # Subscription files and certificates.
    file_events, cert_events = creates('application', 'subscriptionfile'), creates('application', 'medicalcertificate')
    contract.require(len(file_events) == len(effect['files']) and len(cert_events) == len(effect['certificates']), 'state.link-count')
    old_f, new_f = state[FILE]
    expected_f = dict(old_f)
    for i, (event, spec) in enumerate(zip(file_events, effect['files'])):
        key = refs[f'$file{i}'] = uuid4_text(event['changes']['subscription_file_id'][1])
        row = new_f.get(key)
        contract.require(row is not None and window[0] <= moment(row['creation_date']) <= window[1], 'state.file-moment')
        expected_f[key] = {'subscription_file_id': key, 'subscription_id': source['subscriptions'][spec['subscription']]['id'],
                           'document_id': refs[f'$doc{spec["document"]}'], 'creation_date': row['creation_date']}
    contract.require(new_f == expected_f, 'state.files')
    old_c, new_c = state[CERTIFICATE]
    expected_c = dict(old_c)
    for i, (event, spec) in enumerate(zip(cert_events, effect['certificates'])):
        key = refs[f'$cert{i}'] = uuid4_text(event['changes']['medical_id'][1])
        row = new_c.get(key)
        contract.require(row is not None and window[0] <= moment(row['creation_date']) <= window[1], 'state.certificate-moment')
        expected_c[key] = {'medical_id': key, 'document_id': refs[f'$doc{spec["document"]}'], 'user_id': ids[spec['user']] if spec['user'] else None,
                           'creation_date': row['creation_date'], 'expiration_date': None, 'competitive_medical_certificate': False, 'notes': None}
    contract.require(new_c == expected_c, 'state.certificates')

    old_s, new_s = state[SUBSCRIPTION]
    expected_s = dict(old_s)
    for label, values in effect['subscriptions'].items():
        key = source['subscriptions'][label]['id']
        expected_s[key] = {**old_s[key], **{k: day(v) if k in ('start_date', 'end_date') else resolve(v, refs, folders) for k, v in values.items()}}
    contract.require(new_s == expected_s, 'state.subscriptions')
    old_d, new_d = state[DRAFT]
    expected_d = dict(old_d)
    for label, data in effect['drafts'].items():
        key = source['drafts'][label]['id']
        expected_d[key] = {**old_d[key], 'data': resolve(data, refs, folders)}
    contract.require(new_d == expected_d and all(json.dumps(new_d[k]['data'], sort_keys=True) == json.dumps(expected_d[k]['data'], sort_keys=True)
                                                 for k in new_d), 'state.drafts')

    # The response's generated values are the ones the request stored.
    contract.require(all(refs.get(name) == value for name, value in captured.items()), 'response.generated-values')

    # Auditlog entries in write order, with their index rows.
    actor = effect['actor']
    users = keyed(before['application.User'], 'user_id')
    extra = additional_data(case, context['fixture'], ids, actor) if actor else None
    contract.require(len(added) == len(effect['audit']), 'audit.count')
    created_changes = {}
    for event, spec in zip(added, effect['audit']):
        app, table = MODELS[spec['model']]
        if spec['key'].startswith('$'):
            key = refs[spec['key']]
        elif spec['key'] in source['archives']:
            key = source['archives'][spec['key']]['id']
        elif spec['key'] in source['subscriptions']:
            key = source['subscriptions'][spec['key']]['id']
        elif spec['key'] in source['drafts']:
            key = source['drafts'][spec['key']]['id']
        else:
            key = spec['key']
        contract.require(event['content_type_id'] == type_ids[(app, spec['model'].lower())] and event['action'] == spec['action']
                         and event['object_pk'] == key and event['object_repr'] == f'{spec["model"]} object ({key})', 'audit.identity')
        changes = spec['changes']
        if changes == '$created':
            changes = created(spec['model'], key, event, refs, effect, source, ids, folders, archive_dates, moments, new_f, new_c)
            created_changes[key] = changes
        elif changes == '$inserted':
            origin = source['archives'][inserted['from']]
            changes = {'sport_association_documents_archive_id': ['None', key], 'sport_association': ['None', origin['association']],
                       'document': ['None', source['documents'][origin['document']]['id']], 'date': ['None', origin['date']],
                       'folder': ['None', 'None']}
        elif changes == '$deleted':
            if key in created_changes:
                changes = {k: [v[1], 'None'] for k, v in created_changes[key].items()}
                if spec['model'] != 'Document':  # the collector reloads the row: the stored date
                    changes['date'] = [str(archive_dates[key][1].date()), 'None']
            else:
                changes = context['prepared'][key]
        else:
            changes = resolve(changes, refs, folders)
        contract.require(event['changes'] == changes, 'audit.changes')
        if actor is None:
            contract.require(event['actor_id'] is None and event['actor_email'] is None, 'audit.actor')
        else:
            contract.require(event['actor_id'] == ids[actor] and event['actor_email'] == users[ids[actor]]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == extra and event['changes_text'] == '', 'audit.context')
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        association, path = index_target(spec, actor, ids, source)
        index = indexes.get(str(event['id']))
        if association is None:
            contract.require(index is None, 'index.absent')
        else:
            contract.require(index is not None and index['sport_association_id'] == association and index['resolution_path'] == path,
                             'index.fields')
    contract.require(len(indexes) == sum(1 for spec in effect['audit'] if index_target(spec, actor, ids, source)[0]), 'index.count')

    # The association-scoped medical upload grant (remember_medical_upload).
    if effect['cache']:
        name = (f':1:impersonation-upload:{context["fixture"]["sessions"][effect["cache"]["session"]]}:'
                f'{refs["$cert%d" % effect["cache"]["certificate"]]}').encode()
        value = raw.get(name)
        contract.require(value is not None and pickle.loads(value) == ids['own_association'], 'cache.grant')
        contract.require(SESSION_TTL - 120 <= raw.ttl(name) <= SESSION_TTL, 'cache.grant-ttl')
        raw.delete(name)


def index_target(spec, actor, ids, source):
    index = spec['index']
    if index == 'actor':
        label = ACTOR_ASSOCIATION.get(actor)
        return (ids[label], 'actor.sport_association') if label else (None, None)
    if index == 'direct':
        if spec['model'] == 'Subscription':
            return source['subscriptions'][spec['key']]['association'], 'direct'
        if spec['model'] == 'AssociateImportDraft':
            return source['drafts'][spec['key']]['association'], 'direct'
        return ids['own_association'], 'direct'
    if index == 'changes':
        return ids['own_association'], 'changes_json.sport_association'
    if index and index.startswith('subscription:'):
        return source['subscriptions'][index[13:]]['association'], 'subscription.sport_association'
    return None, None


def created(model, key, event, refs, effect, source, ids, folders, archive_dates, moments, files, certificates):
    """auditlog's create diff of a row the request created."""
    if model == 'Document':
        stamp = moments[key]
        return {'document_id': ['None', key], 'creation_date': ['None', topup.auditlog_moment(stamp)],
                'filename': ['None', event['changes']['filename'][1]], 'token': ['None', event['changes']['token'][1]],
                'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}
    if model == 'SportAssociationDocumentsArchive':
        i = int([name for name, value in refs.items() if value == key and '.' not in name][0][8:])
        spec = effect['archives'][i] if i < len(effect['archives']) else None
        doc = event['changes']['document'][1]
        contract.require(doc in moments and archive_dates[key][1] >= moments[doc], 'audit.archive-document')
        folder = 'None'
        if spec is not None and spec['folder']:
            folder = str(folders[spec['folder']])
        return {'sport_association_documents_archive_id': ['None', key], 'sport_association': ['None', ids['own_association']],
                'document': ['None', doc], 'date': ['None', archive_dates[key][0]], 'folder': ['None', folder]}
    if model == 'SubscriptionFile':
        row = files[key]
        return {'subscription_file_id': ['None', key], 'subscription': ['None', row['subscription_id']], 'document': ['None', row['document_id']],
                'creation_date': ['None', topup.auditlog_moment(moment(row['creation_date']))]}
    row = certificates[key]
    return {'medical_id': ['None', key], 'document': ['None', row['document_id']], 'user': ['None', str(row['user_id'])],
            'creation_date': ['None', topup.auditlog_moment(moment(row['creation_date']))], 'competitive_medical_certificate': ['None', 'False']}


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=PURGED)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    from TESTS.adapters.django import carnet_list_http as carnets
    scopes_state = scopes.snapshot_state
    carnets.state_unchanged(fixture['baseline_state'], scopes_state(apps))
    results, context = [], {'remote': {}, 'fixture': fixture, 'folders': dict(FOLDERS)}
    STORAGE['pending'] += 1
    for case in source['cases']:
        before = scopes_state(apps)
        context['prepared'] = prepare(source)
        context['objects'] = listing()
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            start = datetime.now(timezone.utc) - timedelta(seconds=1)
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'], folders=context['folders'])
            window = (start, datetime.now(timezone.utc) + timedelta(seconds=1))
            after = scopes_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, context, raw, window)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    STORAGE['pending'] -= 1
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
