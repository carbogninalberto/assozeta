"""Parent-only subscription/memberships list, add, update and delete HTTP runner; imports are inert.

Seeds documents, associates, subscriptions, payments and memberships with
their document links (audit disabled) and verifies each case against the
complete model state: reads change nothing; membership creates, updates and
deletes write one auditlog entry each (none for an unchanged update),
document links are removed only with their membership and documents,
associates, subscriptions and payments are kept. New memberships are removed
before the fixture cleanup compares the model state.

service_context owns a private MinIO as both servers' default storage: documents_data uploads are checked as new Document
rows (filename, generated id and token, created in the request window, their create entries indexed through the actor's
association), their links and the stored objects '<AWS_LOCATION>/<repr(creation timestamp)>/<id>/<filename>' with the decoded
bytes; nothing else in the bucket changes.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import base64
import os
import time
import uuid

from TESTS.contracts import test_subscription_memberships_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_memberships_http.json', 'TESTS/contracts/test_subscription_memberships_http.py',
           'TESTS/adapters/django/subscription_memberships_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/user_models.py', 'BE/application/mixin.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py', 'BE/docmanager/models.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py') + harness.SOURCES
MEMBERSHIP, LINK = 'application.SubscriptionMembership', 'application.SubscriptionMembership_attached_membership_documents'
DOCUMENT = 'docmanager.Document'
MANAGED = (MEMBERSHIP, LINK, DOCUMENT, harness.AUDIT, harness.INDEX)
BUCKET, LOCATION = 'subscription-memberships-fixture', 'subscription-memberships'
SENTINEL = ('subscription-memberships/sentinel/keep.txt', b'sentinel')
STORAGE = {}


@contextmanager
def service_context(report, log):
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret, region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'):
                yield
            contract.require(listing() == {SENTINEL[0]}, 'storage.objects-restored')
        finally:
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def document_model():
    from docmanager.models import Document
    return Document
COLUMNS = {'subscription': 'subscription_id', 'sport_association': 'sport_association_id', 'associate': 'associate_id',
           'payment': 'payment_id', 'creation_date': 'creation_date', 'start_date': 'start_date', 'end_date': 'end_date',
           'membership_type': 'membership_type', 'membership_number': 'membership_number', 'description': 'description',
           'price': 'price'}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def day(text):
    return date.fromisoformat(text) if text else None


def membership_model():
    from application.models.subscriptions_models import SubscriptionMembership
    return SubscriptionMembership


def seed_rows(source, created):
    from application.models import Associate, Payment, Subscription
    from docmanager.models import Document
    ids = source['ids']
    model = membership_model()
    group = lambda label: ids[label] if label else None
    for row in source['documents'].values():
        Document._base_manager.bulk_create([Document(document_id=row['id'], token=row['token'], filename=row['filename'], filepath=row['filepath'],
            file_size_bytes=row['size'], creation_date=moment(row['created']))])
        created.append((Document, row['id']))
    for label, row in source['associates'].items():
        Associate._base_manager.bulk_create([Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=label.title(),
            last_name='Socio', sex='A', is_minor=False, deleted=row['deleted'], group_id=group(row['group']))])
        created.append((Associate, row['id']))
    for row in source['subscriptions'].values():
        Subscription._base_manager.bulk_create([Subscription(subscription_id=row['id'], sport_association_id=row['association'],
            associate_id=source['associates'][row['associate']]['id'], user_id=ids['owner'], status_flag=Subscription.ACCEPTED)])
        created.append((Subscription, row['id']))
    for row in source['payments'].values():
        Payment._base_manager.bulk_create([Payment(payment_id=row['id'], sport_association_id=row['association'], subject=Payment.OTHER,
            amount=Decimal('10.00'), paid=row['paid'], deleted=row['deleted'], group_id=group(row['group']),
            creation_date=moment('2026-10-01T08:00:00Z'))])
        created.append((Payment, row['id']))
    for row in source['memberships'].values():
        model._base_manager.bulk_create([model(subscription_membership_id=row['id'], sport_association_id=row['sport_association'],
            subscription_id=source['subscriptions'][row['subscription']]['id'] if row['subscription'] else None,
            associate_id=row['associate'], payment_id=row['payment'], creation_date=moment(row['creation_date']),
            start_date=day(row['start_date']), end_date=day(row['end_date']), membership_type=row['membership_type'],
            membership_number=row['membership_number'], description=row['description'],
            price=Decimal(row['price']) if row['price'] is not None else None)])
        created.append((model, row['id']))
        model.attached_membership_documents.through.objects.bulk_create([model.attached_membership_documents.through(
            subscriptionmembership_id=row['id'], document_id=source['documents'][d]['id']) for d in row['documents']])


def deleted_rows(source):
    """auditlog's delete diffs of the seeded memberships, captured before each case."""
    model = membership_model()
    return {str(row.pk): replace.deleted_changes(model, row)
            for row in model._base_manager.filter(pk__in=[row['id'] for row in source['memberships'].values()])}


def plain(value):
    if isinstance(value, dict) and value.get('type') == 'decimal':
        return value['value']
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return None if value is None else str(value)


def stored(row, expected):
    values = {name: plain(row[column]) for name, column in COLUMNS.items()}
    if expected.get('creation_date') == '$now':
        contract.require(isinstance(values['creation_date'], str) and values['creation_date'].endswith('Z'), 'state.creation-date')
        values['creation_date'] = '$now'
    return values


def check_documents(case, effect, before, after, key, context):
    """The request's new documents: rows, links to the membership, stored objects and their create entries' changes."""
    Document = document_model()
    old, new = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    contract.require(set(old) <= set(new) and all(new[k] == old[k] for k in old), 'state.documents-changed')
    added = [k for k in new if k not in old]
    specs = (effect or {}).get('documents') or []
    contract.require(len(added) == len(specs), 'state.document-count')
    started = context['prepared']['started']
    by_name = {Document._base_manager.get(pk=k).filename: k for k in added}
    contract.require(sorted(by_name) == sorted(spec['filename'] for spec in specs), 'state.document-filenames')
    objects = listing()
    expected_objects = set(context['prepared']['objects'])
    expected_links = harness.pairs(before[LINK], 'subscriptionmembership_id', 'document_id')
    listed = {item['document_id']: item for item in contract.LAST.get('documents', [])}
    entries = {}
    for spec in specs:
        document = Document._base_manager.get(pk=by_name[spec['filename']])
        ident = str(document.pk)
        contract.require(uuid.UUID(ident).version == 4 and uuid.UUID(str(document.token)).version == 4 and document.filepath is None
                         and document.file_size_bytes is None and started - 1 <= document.creation_date.timestamp() <= time.time() + 1,
                         'state.document-row')
        if case['status'] in (200, 201):
            item = listed.get(ident)
            contract.require(item is not None and item['token'] == str(document.token)
                             and item['creation_date'] == document.creation_date.isoformat().replace('+00:00', 'Z'), 'response.document-row')
        object_key = f'{LOCATION}/{repr(document.creation_date.timestamp())}/{ident}/{document.filename}'
        contract.require(object_key in objects, 'storage.object:' + object_key)
        stored = STORAGE['client'].get_object(Bucket=BUCKET, Key=object_key)
        contract.require(stored['Body'].read() == base64.b64decode(spec['content']), 'storage.bytes')
        expected_objects.add(object_key)
        expected_links.add((key, ident))
        entries[ident] = {**topup.created_changes(Document, document, {'creation_date': topup.auditlog_moment(document.creation_date)}),
                          # the reverse foreign key with a related_name renders as its empty manager
                          'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}
    contract.require(objects == expected_objects, 'storage.objects')
    contract.require(harness.pairs(after[LINK], 'subscriptionmembership_id', 'document_id') == expected_links
                     and len(after[LINK]) == len(expected_links), 'state.links')
    return entries


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(listing() == context['prepared']['objects'], 'storage.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_m, new_m = keyed(before[MEMBERSHIP], 'subscription_membership_id'), keyed(after[MEMBERSHIP], 'subscription_membership_id')
    # A new primary key on update: auditlog finds no old row (pre_save logs an update against None), then post_save logs the create.
    copy = effect['kind'] == 'copy'
    if effect['kind'] in ('add', 'copy'):
        keys = set(new_m) - set(old_m)
        contract.require(len(keys) == 1 and set(old_m) <= set(new_m) and all(new_m[k] == old_m[k] for k in old_m), 'state.created')
        key = keys.pop()
        contract.require(effect['id'] == '$new' or key == effect['id'], 'state.created-id')
        contract.require(stored(new_m[key], effect['stored']) == effect['stored'], 'state.membership')
        documents = check_documents(case, effect, before, after, key, context)
        action, path = 0, 'direct'
        row = membership_model()._base_manager.get(pk=key)
        changes = topup.created_changes(membership_model(), row, {'creation_date': topup.auditlog_moment(row.creation_date)})
    elif effect['kind'] == 'update':
        key = source['memberships'][effect['label']]['id']
        contract.require(set(new_m) == set(old_m) and stored(new_m[key], effect['stored']) == effect['stored']
                         and all(new_m[k] == old_m[k] for k in old_m if k != key), 'state.membership')
        documents = check_documents(case, effect, before, after, key, context)
        action, path, changes = 1, 'direct', effect['changes']
    else:
        key = source['memberships'][effect['label']]['id']
        contract.require(set(new_m) == set(old_m) - {key}, 'state.deleted')
        kept = {pair for pair in harness.pairs(before[LINK], 'subscriptionmembership_id', 'document_id') if pair[0] != key}
        contract.require(harness.pairs(after[LINK], 'subscriptionmembership_id', 'document_id') == kept and len(after[LINK]) == len(kept),
                         'state.links')
        action, path, changes = 2, 'changes_json.sport_association', context['prepared']['deleted'][key]
        documents = {}
        contract.require(listing() == context['prepared']['objects'], 'storage.no-effect')
    added, indexes = harness.audit_delta(contract, before, after)
    added = sorted(added, key=lambda event: int(event['id']))
    # Document has no association: its entries are indexed through the actor's own association (a collaborator has none).
    actor_association = {'owner': own}.get(effect['actor'])
    leading = 2 if copy else (1 if changes else 0)
    indexed = len(added) if actor_association else leading
    contract.require(len(added) == leading + len(documents) and len(indexes) == indexed, f'audit.count:{len(added)}:{len(indexes)}')
    for position in range(leading):
        harness.check_audit_event(contract, case, added[position], indexes, before=before, ids=ids, model='SubscriptionMembership', key=key,
                                  action=1 if copy and position == 0 else action, changes=changes, actor=effect['actor'], association=own,
                                  path=path, context=context)
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'docmanager'}
    for event in added[leading:]:
        ident = event['object_pk']
        contract.require(event['content_type_id'] == types['document'] and event['action'] == 0 and ident in documents
                         and event['object_repr'] == f'Document object ({ident})' and event['changes'] == documents.pop(ident),
                         'audit.document')
        contract.require(event['actor_id'] == ids[effect['actor']] and event['cid'] == case['name'], 'audit.document-actor')
        index = indexes.get(str(event['id']))
        contract.require(index is None if actor_association is None else
                         (index is not None and index['sport_association_id'] == actor_association
                          and index['resolution_path'] == 'actor.sport_association'), 'index.document')


def fixture_context_for(family_contract):
    """The seeded fixture for a contract; the workload's uploads go with it."""
    base = harness.fixture_context_for(family_contract, seed_rows, purge=(membership_model, document_model))

    @contextmanager
    def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
        try:
            with base(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
                yield fixture
        finally:
            for item in listing() - {SENTINEL[0]}:
                STORAGE['client'].delete_object(Bucket=BUCKET, Key=item)

    return fixture_context


fixture_context = fixture_context_for(contract)


def prepared(source):
    return {'deleted': deleted_rows(source), 'objects': listing(), 'started': time.time()}


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepared)
