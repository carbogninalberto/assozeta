"""Parent-only association-transfer HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run: an empty bucket and Django's default storage switched to
S3Boto3Storage on it (AWS_LOCATION set) before either server starts; the Go
server reads the same AWS_* settings. Celery publishes go to the run's
private Redis DB 0 (family_harness.with_celery_broker).

Seeds (audit disabled) Document rows with their
SportAssociationDocumentsArchive rows (21 own exports on distinct dates, five
older own exports the delete cases remove, an own non-export document, an own
'Export_' document and one foreign export), django-celery-results TaskResult
rows and a self-hosted InstanceConfiguration whose primary association is the
own one.

Around each request:
- setup.instance false removes the InstanceConfiguration for the request (the
  first-run setup wizard state) and restores the identical row afterwards;
- setup.cache entries are stored through Django's cache (pickled by
  django-redis); afterwards the association-export-* and association-import-* keys must be exactly
  cache_after (None: the stored entries unchanged; 'unchanged': the same
  bytes; otherwise the unpickled value, '$task' being the response's task
  id and '$updated' its updated_at, with a TTL within a minute of 'ttl'),
  then they are deleted;
- setup.objects are stored under AWS_LOCATION; afterwards exactly
  objects_after must remain (None: the setup objects) with their bodies and
  content types. '$upload' is the single new temp/imports/import_<uuid4>.zip
  object; the bucket is emptied afterwards.
The effect check: without an effect, or with no deleted rows, the complete
model state is unchanged; a size saved on a document changes only that field,
with one auditlog update entry indexed through the actor's association; a
delete removes exactly the authored archive and
document rows, each with one auditlog delete entry and its index row
(archives through changes_json.sport_association, documents through the
actor's association). Every case checks the Celery messages it published
(none, or the authored task with its id equal to the response's task_id).
"""
from contextlib import contextmanager
from datetime import date, datetime
import base64
import ipaddress
import json
import os
import re
import time
import types

from TESTS.contracts import test_association_transfer_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/association_transfer_http.json', 'TESTS/contracts/test_association_transfer_http.py',
           'TESTS/adapters/django/association_transfer_http.py', 'TESTS/adapters/django/instance_logo.py',
           'BE/application/views/export_views.py', 'BE/application/export_progress.py', 'BE/application/models/user_models.py',
           'BE/application/services/validators.py', 'BE/application/services/json_stream.py', 'BE/application/tasks.py',
           'BE/application/signals.py', 'BE/application/audit_resolvers.py', 'BE/docmanager/models.py',
           'BE/docmanager/download_tokens.py', 'BE/instance/permissions.py', 'BE/instance/models.py',
           'BE/application/permissions_registry.py', 'BE/core/middleware.py', 'BE/core/settings.py') + harness.SOURCES
BUCKET = 'association-transfer-fixture'
LOCATION = 'tenant-transfer'
EXPORT_KEYS = ':1:association-[ei][xm]port-*'
UPLOAD = re.compile(LOCATION + r'/temp/imports/import_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.zip\Z')
ARCHIVE, DOCUMENT, AUDIT, INDEX = ('application.SportAssociationDocumentsArchive', 'docmanager.Document', 'auditlog.LogEntry',
                                   'application.AuditLogIndex')
STORAGE, STATE = {}, {}
require = base_contract.require


def verify_token(token):
    from django.core import signing
    from docmanager.download_tokens import DOWNLOAD_TOKEN_MAX_AGE, DOWNLOAD_TOKEN_SALT
    try:
        return signing.loads(token, salt=DOWNLOAD_TOKEN_SALT, max_age=DOWNLOAD_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None


base_contract.TOKEN_VERIFIER[0] = verify_token


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'):
                yield
            require(listing() == {}, 'storage.objects-restored')
        finally:
            STORAGE.clear()


def listing():
    """{key: (body, content type)} of the whole bucket."""
    client = STORAGE['client']
    pages = client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    objects = {}
    for page in pages:
        for item in page.get('Contents', []):
            got = client.get_object(Bucket=BUCKET, Key=item['Key'])
            objects[item['Key']] = (got['Body'].read(), got.get('ContentType'))
    return objects


def empty_bucket():
    for key in listing():
        STORAGE['client'].delete_object(Bucket=BUCKET, Key=key)


def seed_rows(source, created):
    from django_celery_results.models import TaskResult
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    from instance.models import InstanceConfiguration
    ids = source['ids']

    def insert(model, row):
        require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    require(not SportAssociationDocumentsArchive._base_manager.filter(
        sport_association_id__in=[ids['own_association'], ids['foreign_association'], ids['deleted_association']],
        document__filename__startswith='export_').exists(), 'fixture.exports-exist')
    for key, row in source['documents'].items():
        extra = {'token': row['token']} if 'token' in row else {}
        insert(Document, Document(document_id=row['id'], filename=row['filename'], filepath=row['filepath'],
                                  file_size_bytes=row['size'], creation_date=datetime.fromisoformat(row['created']), **extra))
        archive = source['archives'][key]
        insert(SportAssociationDocumentsArchive, SportAssociationDocumentsArchive(
            sport_association_documents_archive_id=archive['id'], sport_association_id=ids[archive['association']],
            document_id=row['id'], date=date.fromisoformat(archive['date'])))
    for row in source['tasks'].values():
        insert(TaskResult, TaskResult(task_id=row['task_id'], status=row['status'], result=row['result'],
                                      content_type='application/json', content_encoding='utf-8', meta='{"children": []}',
                                      task_name=row.get('task_name')))
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec = dict(source['instance'])
    spec['primary_association_id'] = ids[spec.pop('primary_association')]
    insert(InstanceConfiguration, InstanceConfiguration(**spec))


# ---------------------------------------------------------------------------
# Per-request stimuli
# ---------------------------------------------------------------------------

@contextmanager
def instance_absent(absent):
    """The InstanceConfiguration rows are removed for the request and restored unchanged."""
    from auditlog.context import disable_auditlog
    from instance.models import InstanceConfiguration
    if not absent:
        yield
        return
    rows = list(InstanceConfiguration._base_manager.values())
    require(len(rows) == 1, 'setup.instance-row')
    with disable_auditlog():
        InstanceConfiguration._base_manager.all().delete()
    try:
        yield
    finally:
        with disable_auditlog():
            for row in rows:
                stamps = {key: row.pop(key) for key in ('configured_at', 'updated_at')}
                InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**row)])
                InstanceConfiguration._base_manager.filter(pk=row['id']).update(**stamps)


def resolve(value, task, updated):
    if value == '$task':
        return task
    if value == '$updated':
        return updated
    if isinstance(value, str) and '$task' in value:
        return value.replace('$task', task or '')
    if isinstance(value, dict):
        return {key: resolve(item, task, updated) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, task, updated) for item in value]
    return value


def check_cache(case, raw, stored, body):
    from django.core.cache import cache
    task = body.get('task_id') if isinstance(body, dict) else None
    updated = body.get('updated_at') if isinstance(body, dict) else None
    present = {key.decode(): raw.get(key) for key in raw.keys(EXPORT_KEYS)}
    try:
        expected = case['cache_after']
        if expected is None:
            expected = {name: 'unchanged' for name in case['setup']['cache']}
        expected = {cache.make_key(resolve(name, task, updated)): spec for name, spec in expected.items()}
        require(set(present) == set(expected), 'cache.keys')
        for key, spec in expected.items():
            if spec == 'unchanged':
                require(present[key] == stored[key], 'cache.unchanged')
                continue
            name = key.split(':', 2)[2]
            require(cache.get(name) == resolve(spec['value'], task, updated), 'cache.value')
            ttl = raw.ttl(key)
            require(spec['ttl'] - 60 <= ttl <= spec['ttl'], 'cache.ttl')
    finally:
        for key in present:
            raw.delete(key)


def check_objects(case, before):
    after = listing()
    expected = case['objects_after']
    if expected is None:
        expected = case['setup']['objects']
    wanted = {}
    for key, spec in expected.items():
        body = base64.b64decode(spec['body_b64']) if 'body_b64' in spec else spec['body'].encode()
        if key == '$upload':
            uploads = [name for name in after if name not in before and UPLOAD.match(name)]
            require(len(uploads) == 1, 'storage.upload')
            STATE['upload'] = uploads[0][len(LOCATION) + 1:]
            wanted[uploads[0]] = (body, spec['content_type'])
        else:
            wanted[f'{LOCATION}/{key}'] = (body, spec['content_type'])
    require(after == wanted, 'storage.objects-after')


def _assert_case(case, base_url, *, token, sessions):
    from django.core.cache import cache
    from django_redis import get_redis_connection
    raw = get_redis_connection('default')
    STATE.clear()
    base_contract.LAST.clear()
    require(not raw.keys(EXPORT_KEYS), 'setup.cache-clean')
    stored = {}
    for name, value in case['setup']['cache'].items():
        cache.set(name, value, timeout=3600)
        stored[cache.make_key(name)] = raw.get(cache.make_key(name))
    for key, spec in case['setup']['objects'].items():
        STORAGE['client'].put_object(Bucket=BUCKET, Key=f'{LOCATION}/{key}', Body=spec['body'].encode(),
                                     ContentType=spec['content_type'])
    before = listing()
    STATE['started'] = time.time()
    try:
        with instance_absent(not case['setup']['instance']):
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
        STATE['finished'] = time.time()
        body = base_contract.LAST.get('body')
        STATE['task'] = body.get('task_id') if isinstance(body, dict) else None
        check_objects(case, before)
        check_cache(case, raw, stored, body)
    finally:
        empty_bucket()
        for key in raw.keys(EXPORT_KEYS):
            raw.delete(key)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def celery_specs(effect):
    specs = []
    for spec in (effect or {}).get('celery', []):
        value = lambda item: STATE['upload'] if item == '$upload' else STATE['task'] if item == '$task' else item
        specs.append({**spec, 'id': value(spec['id']), 'kwargs': [[key, value(item)] for key, item in spec['kwargs']]})
    return specs


def check_size_saved(case, before, after, ids, context):
    """doc.save(update_fields=['file_size_bytes']): the field, one update entry and its index row."""
    effect = case['effect']
    harness.require_unmanaged_unchanged(base_contract, before, after, (DOCUMENT, AUDIT, INDEX))
    old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    require(set(old_d) == set(new_d), 'state.documents')
    changed = {str(key) for key in new_d if new_d[key] != old_d[key]}
    require(changed == set(effect['documents_updated']), 'state.documents-changed')
    added, indexes = harness.audit_delta(base_contract, before, after)
    require(len(added) == len(changed) and len(indexes) == len(changed), 'audit.entries')
    types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[case['actor']]
    for key, fields in effect['documents_updated'].items():
        row = next(new_d[k] for k in new_d if str(k) == key)
        previous = next(old_d[k] for k in old_d if str(k) == key)
        require(all(row[name] == value for name, value in fields.items())
                and {k: v for k, v in row.items() if k not in fields} == {k: v for k, v in previous.items() if k not in fields},
                'state.document-fields')
        (event,) = [e for e in added if e['content_type_id'] == types_[('docmanager', 'document')] and e['object_pk'] == key]
        require(event['action'] == 1 and event['object_repr'] == f'Document object ({key})', 'audit.repr')
        require(event['changes'] == {name: [str(previous[name]), str(value)] for name, value in fields.items()}, 'audit.changes')
        require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        require(event['cid'] == case['name'] and event['additional_data'] is None, 'audit.context')
        require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        require(index is not None and str(index['sport_association_id']) == ids[effect['actor_association']]
                and index['resolution_path'] == 'actor.sport_association', 'index.fields')


def assert_effect(case, before, after, source, context):
    effect = case['effect']
    ids = source['ids']
    if effect and effect.get('documents_updated'):
        check_size_saved(case, before, after, ids, context)
    elif not effect or not effect.get('archives'):
        require(before == after, 'state.unchanged')
    else:
        managed = (ARCHIVE, DOCUMENT, AUDIT, INDEX)
        harness.require_unmanaged_unchanged(base_contract, before, after, managed)
        old_r, new_r = keyed(before[ARCHIVE], 'sport_association_documents_archive_id'), keyed(after[ARCHIVE], 'sport_association_documents_archive_id')
        old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
        require({str(k) for k in old_r} - {str(k) for k in new_r} == set(effect['archives'])
                and all(new_r[k] == old_r[k] for k in new_r), 'state.archives')
        require({str(k) for k in old_d} - {str(k) for k in new_d} == set(effect['documents'])
                and all(new_d[k] == old_d[k] for k in new_d), 'state.documents')
        added, indexes = harness.audit_delta(base_contract, before, after)
        expected = [('application', 'sportassociationdocumentsarchive', key, 'SportAssociationDocumentsArchive', changes,
                     ids['own_association'], 'changes_json.sport_association') for key, changes in effect['archive_changes'].items()]
        expected += [('docmanager', 'document', key, 'Document', changes, ids[effect['actor_association']], 'actor.sport_association')
                     for key, changes in effect['document_changes'].items()]
        require(len(added) == len(expected) and len(indexes) == len(expected), 'audit.entries')
        types_ = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
        users = keyed(before['application.User'], 'user_id')
        actor = ids[case['actor']]
        for app, model, key, name, changes, association, path in expected:
            matches = [e for e in added if e['content_type_id'] == types_[(app, model)] and e['object_pk'] == key]
            require(len(matches) == 1, 'audit.identity')
            event = matches[0]
            require(event['action'] == 2 and event['object_repr'] == f'{name} object ({key})', 'audit.repr')
            require(event['changes'] == changes, 'audit.changes')
            require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
            additional = effect['additional_data']
            if additional is not None:
                sessions = context['fixture']['sessions']
                additional = {key: sessions[value.removeprefix('$session:')] if str(value).startswith('$session:') else value
                              for key, value in additional.items()}
            require(event['cid'] == case['name'] and event['additional_data'] == additional, 'audit.context')
            ipaddress.ip_address(event['remote_addr'])
            require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
            index = indexes.get(str(event['id']))
            require(index is not None and str(index['sport_association_id']) == association and index['resolution_path'] == path,
                    'index.fields')
    harness.celery_published(base_contract, context['fixture'], celery_specs(effect), since=STATE['started'],
                             until=STATE.get('finished', time.time()))


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))
run_workload = harness.run_workload_for(contract, assert_effect)
