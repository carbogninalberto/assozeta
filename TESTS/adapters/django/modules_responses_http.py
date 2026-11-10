"""Parent-only module responses HTTP runner; imports are inert.

service_context owns a private MinIO (the instance-logo image and lifetime)
for the whole run and switches Django's default storage to S3Boto3Storage on
it before either server starts, so the Go server receives the same AWS_*
settings. Each workload starts from an empty bucket; after every case the
complete object listing under AWS_LOCATION (key, size, sha256, ContentType,
CacheControl, ContentEncoding only when authored, no user metadata) must equal the objects
the sequence has stored so far.

Seeds modules of the own and the foreign association and without one, their
responses, documents and attachment links (audit disabled, the modules reads
seed). Each case is checked against the complete model state:
- no effect: modules, responses, links, documents, audit and index unchanged;
- 'response': one created response (stored fields, the response body's id),
  then the authored auditlog entries in order (create, user save, queue saves);
- 'document': one created document (fields, the stored object at
  '<str(creation_date.timestamp())>/<id>/<filename>'), the new attachment link
  if any, and its create entry;
- 'rows': deleted responses (with their links) or documents (with every link
  to them), and the authored update entries applied to the other responses.
Every audit entry is compared field by field (content type, action, pk, repr,
changes, actor, cid, impersonation additional_data) with its index row; delete
entries carry auditlog's own diff of the row captured before the case, and
'$actor' indexes resolve to the actor's own association, if any.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import os
import re

from TESTS.contracts import test_modules_responses_http as contract
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import modules_reads_http as reads
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/modules_responses_http.json', 'TESTS/fixtures/generate_modules_responses_http.py',
           'TESTS/contracts/test_modules_responses_http.py', 'TESTS/adapters/django/modules_responses_http.py',
           'TESTS/adapters/django/modules_reads_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'BE/application/views/modules_views.py',
           'BE/application/serializers/modules_serializers.py', 'BE/application/models/modules_models.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/invoices_models.py', 'BE/docmanager/models.py', 'BE/application/utils/api_utils.py',
           'BE/application/audit_resolvers.py', 'BE/application/signals.py', 'BE/application/permissions_registry.py',
           'BE/application/impersonation_scope.py', 'BE/application/urls.py', 'BE/core/settings.py',
           'BE/application/models/payment_models.py', 'TESTS/contracts/xlsx.py') + harness.SOURCES
BUCKET = 'modules-responses-fixture'
MODULE, RESPONSE, LINK = 'application.Module', 'application.ModuleResponses', 'application.ModuleResponses_attachments'
DOCUMENT = 'docmanager.Document'
MANAGED = (MODULE, RESPONSE, LINK, DOCUMENT, harness.AUDIT, harness.INDEX)
STORAGE = {}
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')


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
        STORAGE.update(client=client, location=source['location'])
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'):
                yield
        finally:
            STORAGE.clear()


def stored_objects():
    client, location = STORAGE['client'], STORAGE['location']
    result = {}
    for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []):
        key = item['Key']
        contract.require(key.startswith(location + '/'), 'storage.location')
        head = client.head_object(Bucket=BUCKET, Key=key)
        body = client.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        contract.require(not head.get('Metadata'), 'storage.object-metadata')
        result[key[len(location) + 1:]] = {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                           'content_type': head.get('ContentType'), 'cache_control': head.get('CacheControl')}
        if head.get('ContentEncoding') is not None:
            # mimetypes' encoding of a compression suffix (S3Storage._get_write_parameters).
            result[key[len(location) + 1:]]['content_encoding'] = head['ContentEncoding']
    return result


def clear_storage():
    client = STORAGE['client']
    for item in client.list_objects_v2(Bucket=BUCKET).get('Contents', []):
        client.delete_object(Bucket=BUCKET, Key=item['Key'])
    contract.require(stored_objects() == {}, 'storage.cleared')


def models():
    from application.models import ModuleResponses
    from docmanager.models import Document
    return ModuleResponses, Document


def prepared_diffs(source):
    """auditlog's delete diff of every response and document, before each case."""
    from auditlog.diff import model_instance_diff
    rows = {}
    for model in models():
        for row in model._base_manager.all():
            rows[str(row.pk)] = {key: list(value) for key, value in (model_instance_diff(row, None) or {}).items()}
    return rows


def recent(value):
    now = datetime.now(timezone.utc)
    return now - timedelta(minutes=15) <= value <= now + timedelta(minutes=1)


def links(state):
    return harness.pairs(state[LINK], 'moduleresponses_id', 'document_id')


def check_event(case, event, indexes, spec, *, before, ids, actor, key, values, context):
    """One auditlog entry and its index row against an authored entry."""
    from application.models import SportAssociation
    app = 'docmanager' if spec['model'] == 'Document' else 'application'
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor_id = ids[actor] if actor else None
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor_id,
                      'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    contract.require(event['content_type_id'] == types[(app, spec['model'].lower())] and event['action'] == spec['action']
                     and event['object_pk'] == key and event['object_repr'] == f"{spec['model']} object ({key})", 'audit.identity')
    changes = context['prepared'][key] if spec['changes'] == '$prepared' else \
        {field: [values.get(item, item) for item in pair] for field, pair in spec['changes'].items()}
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor_id and event['actor_email'] == (users[actor_id]['email'] if actor_id else None),
                     'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                     'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    association = spec['index']
    if association == '$actor':
        found = SportAssociation.objects.filter(user_id=actor_id).first() if actor_id else None
        association = None if found is None else str(found.pk)
    index = indexes.get(str(event['id']))
    if association is None:
        contract.require(index is None, 'index.absent')
    else:
        contract.require(index is not None and str(index['sport_association_id']) == association
                         and index['resolution_path'] == spec['path'], 'index.fields')


def resolve_key(label, source, context, created):
    if label == '$new':
        return created
    if label.startswith('$new'):
        return context['new'][label]
    if label in source['responses']:
        return source['responses'][label]['id']
    return source['documents'][label]['id']


def check_created_response(effect, row, source):
    ids = source['ids']
    expected = effect['created']
    contract.require(str(row.module_id) == source['modules'][expected['module']]['id'], 'response.module')
    contract.require(row.progressive_response_number == expected['number'] and row.queue_position == expected['queue'], 'response.numbers')
    contract.require(row.response == expected['response'] and type(row.response) is type(expected['response']), 'response.payload')
    contract.require(row.approved is expected['approved'] and row.payment_id is None, 'response.flags')
    contract.require((None if row.user_id is None else str(row.user_id)) == (ids[expected['user']] if expected['user'] else None),
                     'response.user')
    contract.require(recent(row.creation_date) and not row.attachments.exists(), 'response.created')


def assert_effect(case, before, after, source, context):
    ModuleResponses, Document = models()
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    objects = stored_objects()
    expected_objects = context.setdefault('objects', {})
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(objects == expected_objects, 'storage.unchanged')
        return
    contract.require(before[MODULE] == after[MODULE], 'state.modules')
    ids = source['ids']
    old_r, new_r = keyed(before[RESPONSE], 'module_response_id'), keyed(after[RESPONSE], 'module_response_id')
    old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    old_links, new_links = links(before), links(after)
    created_r, created_d = set(new_r) - set(old_r), set(new_d) - set(old_d)
    created, values = None, {}
    expected_links = set(old_links)
    gone_r = {resolve_key(label, source, context, None) for label in effect.get('deleted_responses', [])}
    gone_d = {resolve_key(label, source, context, None) for label in effect.get('deleted_documents', [])}
    if effect['kind'] == 'response':
        contract.require(len(created_r) == 1 and not created_d, 'state.created-response')
        created = created_r.pop()
        response = contract.RUNTIME.get('response') or {}
        contract.require(response.get('module_response_id') == created, 'response.created-id')
        row = ModuleResponses._base_manager.get(pk=created)
        check_created_response(effect, row, source)
        context.setdefault('new', {})[effect['label']] = created
        values = {'$id': created, '$created': topup.auditlog_moment(row.creation_date)}
    elif effect['kind'] == 'document':
        contract.require(len(created_d) == 1 and not created_r, 'state.created-document')
        created = created_d.pop()
        row = Document._base_manager.get(pk=created)
        contract.require(row.filename == effect['filename'] and row.filepath is None and row.file_size_bytes is None
                         and recent(row.creation_date) and UUID4.match(str(row.token)) is not None, 'document.fields')
        values = {'$id': created, '$created': topup.auditlog_moment(row.creation_date), '$token': str(row.token)}
        if effect['link']:
            expected_links.add((resolve_key(effect['link'], source, context, None), created))
        if effect['object'] is not None:
            name = f'{str(row.creation_date.timestamp())}/{created}/{row.filename}'
            expected_objects[name] = effect['object']
    else:
        contract.require(not created_r and not created_d, 'state.nothing-created')
    contract.require(objects == expected_objects, 'storage.objects')
    contract.require(set(new_r) == (set(old_r) - gone_r) | ({created} if effect['kind'] == 'response' else set()), 'state.responses')
    contract.require(set(new_d) == (set(old_d) - gone_d) | ({created} if effect['kind'] == 'document' else set()), 'state.documents')
    expected_links = {pair for pair in expected_links if pair[0] not in gone_r and pair[1] not in gone_d}
    contract.require(new_links == expected_links and len(after[LINK]) == len(expected_links), 'state.links')
    contract.require(all(new_d[k] == old_d[k] for k in new_d if k in old_d), 'state.documents-unchanged')
    # Authored saves of existing responses: only those fields change.
    changed = {}
    for spec in effect['audit']:
        key = resolve_key(spec['key'], source, context, created)
        if spec['model'] == 'ModuleResponses' and spec['action'] == 1 and key != created:
            for field, (_, new) in spec['changes'].items():
                changed.setdefault(key, {})[field] = new
    for key in new_r:
        if key == created:
            continue
        fields = changed.get(key, {})
        columns = {name for field in fields for name in (field, field + '_id')}
        contract.require({k: v for k, v in old_r[key].items() if k not in columns} == {k: v for k, v in new_r[key].items() if k not in columns},
                         'state.response-unchanged-fields')
        if fields:
            row = ModuleResponses._base_manager.get(pk=key)
            stored = {'approved': str(row.approved), 'queue_position': str(row.queue_position), 'user': str(row.user_id)}
            contract.require(all(stored[field] == value for field, value in fields.items()), 'state.response-saved-fields')
    events, indexes = harness.audit_delta(contract, before, after)
    events = sorted(events, key=lambda event: event['id'])
    contract.require(len(events) == len(effect['audit']), 'audit.count')
    for event, spec in zip(events, effect['audit']):
        check_event(case, event, indexes, spec, before=before, ids=ids, actor=effect['actor'],
                    key=resolve_key(spec['key'], source, context, created), values=values, context=context)
    contract.require(len(indexes) == len([i for i in indexes.values() if str(i['log_entry_id']) in {str(e['id']) for e in events}]),
                     'index.owned')


def seed_rows(source, created):
    """The modules reads seed, plus the export's users and payments; the export
    modules' responses are inserted last, in authored order, with their user
    and payment (the export reads them in the database's order)."""
    from application.models import ModuleResponses, Payment, User
    from TESTS.adapters.django.modules_reads_http import moment
    own = {label: row for label, row in source['responses'].items() if not row['module'].startswith('export')}
    reads.seed_rows({**source, 'responses': own}, created)
    for row in source['export_users'].values():
        User._base_manager.bulk_create([User(user_id=row['id'], username=row['username'], email=row['username'] + '@example.test',
                                             first_name=row['first_name'], last_name=row['last_name'], password='!synthetic')])
        created.append((User, row['id']))
    for row in source['export_payments'].values():
        Payment._base_manager.bulk_create([Payment(payment_id=row['id'], amount=1, paid=row['paid'], deleted=row['deleted'],
                                                   sport_association_id=source['ids']['own_association'])])
        created.append((Payment, row['id']))
    for label, row in source['responses'].items():
        if label in own:
            continue
        ModuleResponses._base_manager.bulk_create([ModuleResponses(module_response_id=row['id'],
            module_id=source['modules'][row['module']]['id'], creation_date=moment(row['created']),
            progressive_response_number=row['number'], queue_position=row['queue'], response=row['response'], approved=row['approved'],
            user_id=source['export_users'][row['export_user']]['id'] if row['export_user'] else None,
            payment_id=source['export_payments'][row['payment']]['id'] if row['payment'] else None)])
        created.append((ModuleResponses, row['id']))
        ModuleResponses.attachments.through.objects.bulk_create([ModuleResponses.attachments.through(
            moduleresponses_id=row['id'], document_id=source['documents'][d]['id']) for d in row['attachments']])


_base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple(lambda index=index: models()[index] for index in (0, 1)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    """VACUUM the responses table before seeding (outside the seed transaction): the rows a previous workload
    deleted leave reusable space, so the export rows are placed in authored order whatever autovacuum did."""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('VACUUM application_moduleresponses')
    with _base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        yield fixture
_run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepared_diffs)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    clear_storage()
    contract.RUNTIME.clear()
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        contract.RUNTIME.clear()
        clear_storage()
