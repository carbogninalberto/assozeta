"""Parent-only subscription medical appointments and certificates HTTP runner; imports are inert.

Seeds documents, medical certificates, subscriptions, medical appointments and
associate import drafts (audit disabled) and verifies each case against the
complete model state and the auditlog entries in write order:
- appointment deletes (delete entry indexed to the actor's own association);
- certificate edits and expirations (update entries indexed to the actor's
  own association, if any), copies of certificates another association also
  references (create entry, then the subscription's ``medical`` update,
  indexed directly), removals (subscription update, certificate and document
  deletes) and the collector's SET_NULL on other subscriptions (no entry);
- draft saves (update entries of ``data``, indexed directly) and the
  certificates created for drafts.
Entries carry the impersonation context of the request. New certificates are
removed before the fixture cleanup compares the model state.
"""
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime

import types

from TESTS.contracts import test_subscription_medical_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printing
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_medical_http.json', 'TESTS/contracts/test_subscription_medical_http.py',
           'TESTS/adapters/django/subscription_medical_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/docmanager/models.py',
           'BE/application/mixin.py', 'BE/application/utils/api_utils.py', 'BE/application/impersonation_scope.py',
           'BE/application/impersonation.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/printing_tasks.py', 'BE/application/utils/api_utils.py', 'TESTS/adapters/django/instance_logo.py',
           'TESTS/adapters/django/s3_objects.py') + harness.SOURCES
APPOINTMENT, CERTIFICATE, SUBSCRIPTION = 'application.MedicalAppointments', 'application.MedicalCertificate', 'application.Subscription'
DRAFT, DOCUMENT = 'application.AssociateImportDraft', 'docmanager.Document'
MANAGED = (APPOINTMENT, CERTIFICATE, SUBSCRIPTION, DRAFT, DOCUMENT, harness.AUDIT, harness.INDEX)
TABLES = {'MedicalAppointments': (APPOINTMENT, 'medical_appointments_id', 'appointments'),
          'MedicalCertificate': (CERTIFICATE, 'medical_id', 'certificates'), 'Subscription': (SUBSCRIPTION, 'subscription_id', 'subscriptions'),
          'AssociateImportDraft': (DRAFT, 'associate_import_draft_id', 'drafts'), 'Document': (DOCUMENT, 'document_id', 'documents')}
# The actor's own association (SportAssociation.objects.filter(user=actor).first()).
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association', 'foreign_owner': 'foreign_association'}


STATE = {}
BUCKET, LOCATION = 'subscription-medical-fixture', 'subscription-medical'
SENTINEL = ('subscription-medical/sentinel/keep.txt', b'sentinel')
STORAGE = {}
STUB = printing.STUB


def _assert_case(case, base_url, *, token, sessions):
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = [item['reply'] for item in case.get('prints', [])]
    STUB['token'] = token
    return base_contract.assert_case(case, base_url, token=token, sessions=sessions)


# The harness drives this proxy: the CURRENT_HOST stub is scripted for the request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_prints(case):
    """The print_document_medical_appointment requests: GET CURRENT_HOST/document/medical-appointment/<id>, requests' default
    headers and the request's Authorization, no body."""
    require = contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case.get('prints', [])
    require(len(log) == len(expected), 'print.request-count')
    headers = [('host', STUB['host']), ('user-agent', printing.USER_AGENT), ('accept-encoding', 'gzip, deflate'), ('accept', '*/*'),
               ('connection', 'keep-alive')]
    if STUB['token'] is not None:
        headers.append(('authorization', 'Bearer ' + STUB['token']))
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        require(sent['method'] == 'GET' and sent['path'] == spec['path'], 'print.request-line' + where)
        require(sorted((name.lower(), value) for name, value in sent['headers']) == sorted(headers), 'print.headers' + where)
        require(sent['body'] == b'', 'print.body' + where)


@contextmanager
def service_context(report, log):
    with printing.service_context(report, log):
        with _storage_context(report, log):
            yield


@contextmanager
def _storage_context(report, log):
    """A private MinIO as both servers' default storage (the placeholder certificate image); PIL_PLACEHOLDER_ZLIB tells the
    Go server which zlib this interpreter's Pillow compresses PNGs with. The bucket keeps only the sentinel at the end."""
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from PIL import features
    from TESTS.adapters.django.instance_logo import minio
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret, region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client)
        saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
        zlib = 'zlib-ng' if (features.version('zlib') or '').endswith('zlib-ng') else 'zlib'
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**json.loads(saved or '{}'), 'PIL_PLACEHOLDER_ZLIB': zlib})
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'):
                yield
            contract.require(listing() == {SENTINEL[0]}, 'storage.objects-restored')
        finally:
            STORAGE.clear()
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def document_model():
    return models()[4]


def models():
    from application.models.subscriptions_models import AssociateImportDraft, MedicalAppointments, MedicalCertificate, Subscription
    from docmanager.models import Document
    return MedicalAppointments, MedicalCertificate, Subscription, AssociateImportDraft, Document


def certificate_model():
    return models()[1]


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


day = lambda text: date.fromisoformat(text) if text else None


def seed_rows(source, created):
    appointment, certificate, subscription, draft, document = models()
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    from application.models import Associate
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'],
                                    last_name=row['last']))
    for row in source['documents'].values():
        insert(document, document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=moment(row['created'])))
    for row in source['certificates'].values():
        insert(certificate, certificate(medical_id=row['id'], user_id=ids[row['user']], creation_date=moment(row['created']),
                                        expiration_date=day(row['expiration']), notes=row['notes'],
                                        competitive_medical_certificate=row['competitive'],
                                        document_id=source['documents'][row['document']]['id'] if row['document'] else None))
    for row in source['subscriptions'].values():
        insert(subscription, subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=ids[row['user']],
                                          medical_id=source['certificates'][row['medical']]['id'] if row['medical'] else None,
                                          associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None,
                                          deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None,
                                          start_date=day(row['start']), end_date=day(row['end']), creation_date=moment(row['created']),
                                          status_flag=subscription.ACCEPTED))
    for row in source['appointments'].values():
        insert(appointment, appointment(medical_appointments_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'],
                                        region=row['region'], document_id=source['documents'][row['document']]['id'] if row['document'] else None, date=day(row['date']),
                                        notes=row['notes'], sport=row['sport'], meta=row['meta'], address=row['address'],
                                        creation_date=moment(row['created'])))
    for row in source['drafts'].values():
        insert(draft, draft(associate_import_draft_id=row['id'], sport_association_id=row['association'], data=row['data'], valid=row['valid']))


def deleted_rows(source):
    """auditlog's delete diffs of the seeded appointments, certificates and documents, captured before each case."""
    appointment, certificate, _, _, document = models()
    result = {}
    for model, table in ((appointment, 'appointments'), (certificate, 'certificates'), (document, 'documents')):
        for row in model._base_manager.filter(pk__in=[item['id'] for item in source[table].values()]):
            result[str(row.pk)] = replace.deleted_changes(model, row)
            if model is document:  # auditlog renders the reverse FK accessor that declares a related_name
                result[str(row.pk)]['customer_invoice_pdf'] = ['application.CustomerInvoice.None', 'None']
    return result


def placed(value, new):
    """value with each '$new' leaf replaced by the new certificate id."""
    if value == '$new':
        return new
    if isinstance(value, dict):
        return {k: placed(v, new) for k, v in value.items()}
    if isinstance(value, list):
        return [placed(v, new) for v in value]
    return value


def additional_data(case, fixture, ids, actor):
    session = case['headers'].get('X-Impersonation-Id', '')
    if not session.startswith('$session:'):
        return None
    label = session[9:]
    return {'impersonation_session': fixture['sessions'][label], 'actor_id': ids[actor], 'target_user_id': case['headers']['User-Id'],
            'association_id': None if label.startswith('admin_') else ids['own_association']}


def check_entries(case, added, indexes, expected, *, before, ids, actor, context):
    """added (in id order) against expected [(app, model, key, action, changes, association, path)]."""
    contract.require(len(added) == len(expected), 'audit.count')
    contract.require(len(indexes) == sum(1 for item in expected if item[5] is not None), 'index.count')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    extra = additional_data(case, context['fixture'], ids, actor)
    for event, (app, model, key, action, changes, association, path) in zip(added, expected):
        contract.require(event['content_type_id'] == types[(app, model.lower())] and event['action'] == action and event['object_pk'] == key
                         and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        contract.require(event['actor_id'] == ids[actor] and event['actor_email'] == users[ids[actor]]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == extra and event['changes_text'] == '', 'audit.context')
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if association is None:
            contract.require(index is None, 'index.absent')
        else:
            contract.require(index is not None and index['sport_association_id'] == association and index['resolution_path'] == path,
                             'index.fields')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    check_prints(case)
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        contract.require(listing() == STATE['objects'], 'storage.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    actor = effect['actor']
    actor_association = ids[ACTOR_ASSOCIATION[actor]] if actor in ACTOR_ASSOCIATION else None
    state = {name: (keyed(before[table], key), keyed(after[table], key)) for name, (table, key, _) in TABLES.items()}
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: int(event['id']))
    prepared = context['prepared']
    pk = lambda model, label: source[TABLES[model][2]][label]['id']

    if effect['kind'] == 'placeholder':
        contract.require(len(added) == 4, f'audit.count:{len(added)}')
        new, doc = added[0]['object_pk'], added[1]['object_pk']
        old_c, new_c = state['MedicalCertificate']
        old_doc, new_doc = state['Document']
        contract.require(set(new_c) == set(old_c) | {new} and new not in old_c and set(new_doc) == set(old_doc) | {doc}
                         and doc not in old_doc, 'state.placeholder-rows')
        row, stored_doc = new_c[new], new_doc[doc]
        uuid4_text = lambda text: uuid.UUID(text).version == 4 and str(uuid.UUID(text)) == text
        contract.require(uuid4_text(new) and uuid4_text(doc) and uuid4_text(str(stored_doc['token']))
                         and len({new, doc, str(stored_doc['token'])}) == 3, 'state.placeholder-ids')
        contract.require(all(new_c[k] == old_c[k] for k in old_c) and row == {
            'medical_id': new, 'document_id': doc, 'user_id': ids[effect['user']], 'creation_date': row['creation_date'],
            'expiration_date': day(effect['expiration']), 'competitive_medical_certificate': False, 'notes': None}, 'state.placeholder-certificate')
        contract.require(all(new_doc[k] == old_doc[k] for k in old_doc) and stored_doc == {
            'document_id': doc, 'token': stored_doc['token'], 'creation_date': stored_doc['creation_date'], 'filepath': None,
            'filename': effect['filename'], 'file_size_bytes': None}, 'state.placeholder-document')
        since, until = STATE['started'], time.time()
        created = document_model()._base_manager.get(pk=doc).creation_date
        for stamp in (certificate_model()._base_manager.get(pk=new).creation_date, created):
            contract.require(since - 1 <= stamp.timestamp() <= until + 1, 'state.placeholder-created')
        old_s, new_s = state['Subscription']
        key = pk('Subscription', effect['subscription'])
        contract.require(new_s == {**old_s, key: {**old_s[key], 'medical_id': new}}, 'state.subscriptions')
        for name in ('AssociateImportDraft', 'MedicalAppointments'):
            contract.require(state[name][0] == state[name][1], 'state.' + name)
        # The stored image: Pillow's PNG of the view's text, under the upload layout.
        from io import BytesIO
        from application.utils.api_utils import generate_image_with_text
        buffer = BytesIO()
        generate_image_with_text(effect['text']).save(buffer, format='PNG')
        name = f"{LOCATION}/{created.timestamp()}/{doc}/{effect['filename']}"
        contract.require(listing() == STATE['objects'] | {name}, 'storage.placeholder-key')
        stored = STORAGE['client'].get_object(Bucket=BUCKET, Key=name)
        body = stored['Body'].read()
        contract.require(body[:8] == b'\x89PNG\r\n\x1a\n' and body == buffer.getvalue(), f'storage.placeholder-bytes:{len(body)}')
        contract.require(stored['ContentType'] == 'image/png' and stored.get('ContentEncoding') is None, 'storage.placeholder-metadata')
        STATE['objects'] = STATE['objects'] | {name}
        text = topup.auditlog_moment
        association = actor_association
        path = 'actor.sport_association'
        expected = [
            ('application', 'MedicalCertificate', new, 0, {
                'medical_id': ['None', new], 'document': ['None', 'None'], 'user': ['None', ids[effect['user']]],
                'creation_date': ['None', text(certificate_model()._base_manager.get(pk=new).creation_date)],
                'competitive_medical_certificate': ['None', 'False']}, association, path),
            ('docmanager', 'Document', doc, 0, {
                'document_id': ['None', doc], 'token': ['None', str(stored_doc['token'])], 'filename': ['None', effect['filename']],
                'creation_date': ['None', text(document_model()._base_manager.get(pk=doc).creation_date)],
                'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}, association, path),
            ('application', 'Subscription', key, 1, {'medical': ['None', new]}, own, 'direct'),
            ('application', 'MedicalCertificate', new, 1, {
                'document': ['None', doc], 'expiration_date': ['None', effect['expiration'] + ' 00:00:00+00:00']}, association, path)]
        check_entries(case, added, indexes, expected, before=before, ids=ids, actor=actor, context=context)
        return

    contract.require(listing() == STATE['objects'], 'storage.unchanged')
    if effect['kind'] == 'delete':
        old_a, new_a = state['MedicalAppointments']
        keys = {pk('MedicalAppointments', label) for label in effect['appointments']}
        contract.require(set(new_a) == set(old_a) - keys and all(new_a[k] == old_a[k] for k in new_a), 'state.appointments')
        for name in ('MedicalCertificate', 'Subscription', 'AssociateImportDraft', 'Document'):
            contract.require(state[name][0] == state[name][1], 'state.' + name)
        association = actor_association if effect['index'] else None
        expected = [('application', 'MedicalAppointments', key, 2, prepared[key], association, 'actor.sport_association') for key in keys]
        check_entries(case, added, indexes, expected, before=before, ids=ids, actor=actor, context=context)
        return

    # The certificate the request creates (a copy or a new one) is the one create entry.
    types = {ct['id']: (ct['app_label'], ct['model']) for ct in before['contenttypes.ContentType']}
    creates = [event for event in added if event['action'] == 0 and types[event['content_type_id']] == ('application', 'medicalcertificate')]
    expects_new = bool(effect['copy'] or effect['created'])
    contract.require(len(creates) == (1 if expects_new else 0), 'state.new-certificate')
    new = creates[0]['object_pk'] if creates else None
    old_c, new_c = state['MedicalCertificate']
    expected_c = {k: v for k, v in old_c.items() if k not in {pk('MedicalCertificate', label) for label in effect['removed']}}
    for label, values in effect['certificates'].items():
        key = pk('MedicalCertificate', label)
        expected_c[key] = {**expected_c[key], **{k: day(v) if k == 'expiration_date' else v for k, v in values.items()}}
    if effect['copy'] and not effect['copy'].get('removed'):
        origin = old_c[pk('MedicalCertificate', effect['copy']['from'])]
        overrides = {k: day(v) if k == 'expiration_date' else v for k, v in effect['copy'].items() if k != 'from'}
        contract.require(new in new_c, 'state.copy')
        expected_c[new] = {**origin, 'medical_id': new, 'creation_date': new_c[new]['creation_date'], **overrides}
    if effect['created']:
        contract.require(new in new_c, 'state.created')
        expected_c[new] = {'medical_id': new, 'document_id': None, 'user_id': ids[effect['created']['user']],
                           'creation_date': new_c[new]['creation_date'], 'expiration_date': day(effect['created']['expiration']),
                           'competitive_medical_certificate': False, 'notes': None}
    contract.require(new_c == expected_c, 'state.certificates')

    old_s, new_s = state['Subscription']
    expected_s = dict(old_s)
    for label, values in effect['subscriptions'].items():
        key = pk('Subscription', label)
        resolved = {k: (day(v) if k in ('start_date', 'end_date') else placed(v, new)) for k, v in values.items()}
        expected_s[key] = {**old_s[key], **resolved}
    for label in effect['detached']:
        key = pk('Subscription', label)
        expected_s[key] = {**old_s[key], 'medical_id': None}
    contract.require(new_s == expected_s, 'state.subscriptions')

    old_d, new_d = state['AssociateImportDraft']
    expected_d = dict(old_d)
    for label, data in effect['drafts'].items():
        key = pk('AssociateImportDraft', label)
        expected_d[key] = {**old_d[key], 'data': placed(data, new)}
    contract.require(new_d == expected_d and all(json.dumps(new_d[k]['data'], sort_keys=True) == json.dumps(expected_d[k]['data'], sort_keys=True)
                                                 for k in new_d), 'state.drafts')
    old_doc, new_doc = state['Document']
    removed_docs = {pk('Document', label) for label in effect['documents_removed']}
    contract.require(new_doc == {k: v for k, v in old_doc.items() if k not in removed_docs}, 'state.documents')
    contract.require(state['MedicalAppointments'][0] == state['MedicalAppointments'][1], 'state.appointments')

    expected = []
    created_changes = None
    for entry in effect['audit']:
        model = entry['model']
        key = new if entry['key'] == '$new' else pk(model, entry['key'])
        changes = entry['changes']
        if changes == '$created':
            row = certificate_model()._base_manager.filter(pk=new).first()
            if row is not None:
                stamp = topup.auditlog_moment(row.creation_date)
            else:  # removed later in the same request: only its create entry remains
                stamp = creates[0]['changes'].get('creation_date', ['None', ''])[1]
                contract.require(bool(stamp) and stamp != 'None', 'audit.copy-moment')
            origin = old_c[pk('MedicalCertificate', entry['copy_of'])] if 'copy_of' in entry else {
                'document_id': None, 'user_id': ids[effect['created']['user']], 'expiration_date': None,
                'competitive_medical_certificate': False, 'notes': None}
            text = lambda value: 'None' if value is None else str(value)
            changes = {'medical_id': ['None', new], 'document': ['None', text(origin['document_id'])], 'user': ['None', text(origin['user_id'])],
                       'creation_date': ['None', stamp],
                       'competitive_medical_certificate': ['None', str(origin['competitive_medical_certificate'])]}
            if origin['expiration_date'] is not None:
                changes['expiration_date'] = ['None', str(origin['expiration_date'])]
            if origin['notes'] is not None:
                changes['notes'] = ['None', origin['notes']]
            created_changes = changes
        elif changes == '$deleted':
            if key == new:
                changes = {k: [v[1], 'None'] for k, v in created_changes.items()}
            else:
                changes = prepared[key]
        else:
            changes = {k: [new if v == '$new' else v for v in pair] for k, pair in changes.items()}
            if model == 'AssociateImportDraft':
                changes = {k: [v.replace('"$new"', json.dumps(new)) for v in pair] for k, pair in changes.items()}
        association, path = None, None
        if entry['index'] == 'own':
            association, path = own, 'direct'
        elif actor_association is not None:
            association, path = actor_association, 'actor.sport_association'
        app = 'docmanager' if model == 'Document' else 'application'
        expected.append((app, model, key, entry['action'], changes, association, path))
    check_entries(case, added, indexes, expected, before=before, ids=ids, actor=actor, context=context)


_base_context = harness.fixture_context_for(contract, seed_rows, purge=(certificate_model, document_model))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    """The base fixture; the placeholder images the cases stored are removed with it."""
    with _base_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        STATE['objects'] = listing()
        try:
            yield fixture
        finally:
            for item in listing() - {SENTINEL[0]}:
                STORAGE['client'].delete_object(Bucket=BUCKET, Key=item)


def prepared(source):
    STATE['started'] = time.time()
    return deleted_rows(source)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepared)
