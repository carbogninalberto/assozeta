"""Parent-only personas writes HTTP runner; imports are inert.

Seeds two families, the associates (own, grouped, deleted, foreign, without an
association), tutor relations, a medical certificate, a tag and subscriptions
(audit disabled), and checks every case against the complete model state:
exactly the authored associate columns change, the authored associates and
tutor relations are created (generated keys and creation times bound from the
case's auditlog create entries), the authored relations are deleted or
changed, nothing else moves, and the authored auditlog entries are appended in
order (relation deletes rendered from the rows captured before the request)
with their index rows.

service_context owns a private MinIO behind subscription-sign's recording
proxy (it answers the object ACL calls itself: MinIO supports only the private
canned ACL). The default storage keeps the bucket and AWS_LOCATION of the
environment and only its endpoint and credentials point at the proxy; object
ACLs are on (AWS_S3_USE_OBJECT_ACL) and there is no public base URL, so a
picture's URL is '<endpoint>/<bucket>/<key>'. Every case is checked against the
recorded storage requests (HeadObject 404, PutObject, PutObjectAcl public-read
on '<AWS_LOCATION>/associates/<associate id>/picture_<repr(timestamp)>.jpg'
with the timestamp inside the request window; the folder of a new associate is
the id of the row the case creates), the stored bytes and metadata, and the
picture URL of the response and of the saved row; the objects are removed once
verified. Cases without an upload must send no storage request.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import base64
import ipaddress
import json
import os
import re
import time

from TESTS.contracts import test_personas_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/personas_writes_http.json', 'TESTS/contracts/test_personas_writes_http.py',
           'TESTS/adapters/django/personas_writes_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/subscription_sign_http.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py',
           'BE/application/views/personas_views.py', 'BE/application/serializers/personas_serializers.py',
           'BE/application/models/user_models.py', 'BE/application/models/subscriptions_models.py', 'BE/application/mixin.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/application/urls.py', 'BE/core/settings.py') + harness.SOURCES
ASSOCIATE, RELATION = 'application.Associate', 'application.AssociateTutorRelation'
MANAGED = (ASSOCIATE, RELATION, harness.AUDIT, harness.INDEX)
FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap',
          'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label',
          'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role', 'id_number', 'migrated', 'notes')
COLUMNS = {'user': 'user_id', 'association': 'sport_association_id', 'group': 'group_id', 'family': 'family_id',
           'created': 'creation_date', 'id': 'associate_id'}


STORAGE = {}


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    import core.settings as module_settings
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    from TESTS.adapters.django.subscription_sign_http import RecordingProxy
    source = contract.load_source()
    bucket, location = module_settings.AWS_STORAGE_BUCKET_NAME, module_settings.AWS_LOCATION
    contract.require(bucket and location and '/' not in location, 'storage.settings')
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=bucket)
        proxy = RecordingProxy(endpoint, source['acl_denied'], bucket)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, proxy=proxy, bucket=bucket, location=location,
                       picture_base=proxy.endpoint.rstrip('/') + '/' + bucket)
        # The Go server reads the object ACL switch from its environment.
        saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**json.loads(saved or '{}'), 'AWS_S3_USE_OBJECT_ACL': 'True'})
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=proxy.endpoint, AWS_S3_ADDRESSING_STYLE='path',
                                   AWS_S3_USE_OBJECT_ACL=True, AWS_S3_PUBLIC_BASE_URL=''):
                yield
            contract.require(not listing(), 'storage.objects-removed')
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            proxy.close()
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=STORAGE['bucket'])
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def check_storage(case, source, since, bindings):
    """The case's storage requests and objects; binds each '$picN' to its URL.

    Families reusing this effect check without the storage service author no uploads."""
    if not STORAGE:
        contract.require(not case.get('pictures') and not case.get('storage'), 'storage.unavailable')
        return
    try:
        verify_storage(case, source, since, bindings)
    finally:
        # A failed case must not leave its objects for the following ones.
        for key in listing():
            STORAGE['client'].delete_object(Bucket=STORAGE['bucket'], Key=key)


def verify_storage(case, source, since, bindings):
    requests = STORAGE['proxy'].take()
    until = time.time()
    expected, objects = [], {}
    puts = [item[1] for item in requests if item[0] == 'PUT']
    pictures = case.get('pictures', {})
    contract.require(len(puts) == len(pictures), 'storage.put-count:' + json.dumps(requests))
    base = STORAGE['picture_base']
    for (ref, spec), key in zip(pictures.items(), puts):
        # A new associate's folder ('$a<n>') is checked against the created row once it is known.
        created = spec['folder'].startswith('$')
        folder = '([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})' if created else '(' + re.escape(spec['folder']) + ')'
        match = re.fullmatch(re.escape(f"{STORAGE['location']}/associates/") + folder + r'/picture_(\d+\.\d+)\.jpg', key)
        contract.require(match is not None, 'storage.key-shape:' + key)
        stamp = match.group(2)
        contract.require(repr(float(stamp)) == stamp and since - 1 <= float(stamp) <= until + 1, 'storage.key-timestamp')
        bindings[ref] = base + '/' + key
        if created:
            bindings.setdefault('$folders', {})[spec['folder']] = match.group(1)
        objects[key] = spec
    keys = {ref: bindings[ref][len(base) + 1:] for ref in pictures}
    for item in case.get('storage', []):
        operation, ref, *rest = item.split(' ')
        if operation == 'ACL':
            expected.append(('ACL', keys[ref], 'public-read', 403 if rest == ['denied'] else 200))
        elif operation == 'HEAD':
            expected.append(('HEAD', keys[ref], '', 404))
        else:
            expected.append(('PUT', keys[ref], '', 200))
    contract.require(requests == expected, 'storage.requests:' + json.dumps([requests, expected]))
    contract.require(listing() == set(objects), 'storage.objects')
    client = STORAGE['client']
    for key, spec in objects.items():
        stored = client.get_object(Bucket=STORAGE['bucket'], Key=key)
        contract.require(stored['Body'].read() == base64.b64decode(spec['body_b64']), 'storage.bytes')
        contract.require(stored['ContentType'] == 'image/jpeg' and stored.get('ContentEncoding') is None
                         and stored.get('CacheControl') == 'max-age=86400', 'storage.metadata')
    # The response named the same URL (bound by the contract).
    contract.require(all(bindings[ref] == value for ref, value in contract.BOUND.items()), 'storage.response-url')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Associate, AssociateTutorRelation, Family, MedicalCertificate, Subscription, Tags
    ids = source['ids']

    def put(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    for family in source['families']:
        put(Family, Family(family_id=family, type='family'))
    for row in source['associates'].values():
        put(Associate, Associate(associate_id=row['id'], user_id=row['user'], sport_association_id=row['association'], group_id=row['group'],
                                 family_id=row['family'], born_date=day(row['born_date']), creation_date=moment(row['created']),
                                 **{key: row[key] for key in FIELDS}))
    for row in source['relations'].values():
        put(AssociateTutorRelation, AssociateTutorRelation(id=row['id'], associate_id=source['associates'][row['associate']]['id'],
                                                           tutor_id=source['associates'][row['tutor']]['id'], is_primary=row['primary']))
    medical = put(MedicalCertificate, MedicalCertificate(medical_id=source['medical']['id'], user_id=ids['owner'],
                                                         creation_date=moment('2025-01-01T08:00:00Z'),
                                                         expiration_date=day(source['medical']['expiration'])))
    tag = put(Tags, Tags(tag_id=source['tag']['id'], tag_name=source['tag']['name'], sport_association_id=ids['own_association'],
                         creation_date=moment('2025-01-01T08:00:00Z')))
    for row in source['subscriptions'].values():
        subscription = put(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row.get('association', ids['own_association']), user_id=ids['owner'],
            associate_id=row['associate'], start_date=day(row['start']), end_date=day(row['end']), creation_date=moment(row['created']),
            status_flag=row.get('status_flag', 1), type=row.get('type', 2), role=row.get('role', 1),
            acceptance_date=day(row.get('acceptance_date')), medical=medical if row.get('medical') else None,
            signature_url=row.get('signature_url'), signature_storage_key=row.get('signature_storage_key'),
            custom_data=row.get('custom_data'), additional_fields=row.get('additional_fields'), meta=row.get('meta'),
            subscription_number=row.get('subscription_number', '0'), notes=row.get('notes'), competitive=row.get('competitive', False),
            draft=row.get('draft', True), trial=row.get('trial', False), archived=row.get('archived', False),
            deleted=row.get('deleted', False), group_id=row.get('group')))
        if row.get('tags'):
            link = Subscription.tags.through._base_manager.create(subscription_id=subscription.pk, tags_id=tag.pk)
            created.append((Subscription.tags.through, link.pk))


def associate_model():
    from application.models import Associate
    return Associate


# Relations a case creates always involve an associate it created: purging those cascades them.
fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(associate_model,))


def captured(source):
    """auditlog's delete diffs of the seeded relations, captured before each case."""
    from application.models import AssociateTutorRelation
    if STORAGE:
        STORAGE['proxy'].take()
    rows = {'$started': time.time()}
    for row in AssociateTutorRelation._base_manager.filter(pk__in=[r['id'] for r in source['relations'].values()]):
        rows[str(row.pk)] = replace.deleted_changes(AssociateTutorRelation, row)
    return rows


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def substitute(value, bindings):
    if isinstance(value, dict):
        return {key: substitute(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, bindings) for item in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def audit_moment(text):
    return str(moment(text).astimezone(timezone.utc).replace(tzinfo=None))


def stored(row, labels):
    """The authored associate as its stored columns (refs resolved later)."""
    result = {}
    for key, value in row.items():
        column = COLUMNS.get(key, key)
        if key in ('user', 'association', 'group', 'family') and value in labels:
            value = labels[value]
        result[column] = value
    return result


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    bindings = {}
    check_storage(case, source, context['prepared']['$started'], bindings)
    if effect is None:
        contract.require(not bindings.get('$folders'), 'storage.key-folder')
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    labels = {label: row['id'] for label, row in source['associates'].items()}
    relation_ids = {label: row['id'] for label, row in source['relations'].items()}

    entries, indexes = harness.audit_delta(contract, before, after)
    entries.sort(key=lambda row: int(row['id']))
    contract.require(len(entries) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')

    old = {str(flat(r)['associate_id']): flat(r) for r in before[ASSOCIATE]}
    new = {str(flat(r)['associate_id']): flat(r) for r in after[ASSOCIATE]}
    for event, entry in zip(entries, effect['audit']):
        if entry['action'] == 0 and entry['ref'].startswith('$'):
            bindings[entry['ref']] = event['object_pk']
    # A new associate's picture lives under the id of the row the case created.
    contract.require(all(bindings.get(ref) == folder for ref, folder in bindings.pop('$folders', {}).items()), 'storage.key-folder')
    added = set(new) - set(old)
    created_rows = effect['created']
    contract.require(len(added) == len(created_rows), 'state.associates-created')
    for row in created_rows:
        key = bindings.get(row['id'])
        contract.require(key in added, 'state.associate-created-key')
        actual = new.pop(key)
        stamp = moment(actual['creation_date'])
        contract.require(abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10), 'state.associate-creation-date')
        bindings[row['created']] = audit_moment(actual['creation_date'])
        expected = stored({**row, 'id': key, 'created': actual['creation_date']}, labels)
        expected = substitute(expected, bindings)
        contract.require(actual == expected, 'state.associate-created-row:' + row['id'])
    expected = {}
    for key, row in old.items():
        expected[key] = dict(row)
    for label, columns in effect['changed'].items():
        key = labels[label]
        for name, value in columns.items():
            expected[key][COLUMNS.get(name, name)] = (labels.get(value, value) if name in ('user', 'association', 'group', 'family')
                                                      else bindings.get(value, value) if isinstance(value, str) else value)
    contract.require(new == expected, 'state.associates')

    def relation_key(ref):
        return str(relation_ids[ref]) if ref in relation_ids else bindings[ref]

    def associate_key(ref):
        return labels[ref] if ref in labels else bindings[ref]

    gone = {relation_ids[label] for label in effect['relations_deleted']}
    changes = {relation_ids[label]: columns for label, columns in effect['relations_changed'].items()}
    kept = [{**flat(r), **changes.get(flat(r)['id'], {})} for r in before[RELATION] if flat(r)['id'] not in gone]
    after_relations = [flat(r) for r in after[RELATION]]
    existing = {r['id'] for r in kept}
    created_relations = [r for r in after_relations if r['id'] not in existing]
    contract.require([r for r in after_relations if r['id'] in existing] == kept, 'state.relations')
    expected_relations = sorted(((associate_key(r['associate']), associate_key(r['tutor']), r['primary'], relation_key(r['id']))
                                 for r in effect['relations_created']))
    contract.require(sorted((str(r['associate_id']), str(r['tutor_id']), r['is_primary'], str(r['id'])) for r in created_relations)
                     == expected_relations, 'state.relations-created')

    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, entry) in enumerate(zip(entries, effect['audit'])):
        if entry['model'] == 'associate':
            key, name = associate_key(entry['ref']), 'Associate'
        else:
            key, name = relation_key(entry['ref']), 'AssociateTutorRelation'
        where = f":{position}:{entry['model']}:{entry['ref']}"
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{name} object ({key})', 'audit.identity' + where)
        expected_changes = entry['changes']
        if expected_changes is None:
            expected_changes = context['prepared'][key]
        resolved = substitute(expected_changes, bindings)
        contract.require(event['changes'] == resolved, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)


def run_workload(*args, **kwargs):
    """The family workload; a failed case's objects are removed so the run reports that case."""
    try:
        return _run_workload(*args, **kwargs)
    except BaseException:
        if STORAGE:
            for key in listing():
                STORAGE['client'].delete_object(Bucket=STORAGE['bucket'], Key=key)
        raise
