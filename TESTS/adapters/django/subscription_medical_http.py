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
from datetime import date, datetime

from TESTS.contracts import test_subscription_medical_http as contract
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
           'BE/application/printing_tasks.py') + harness.SOURCES
APPOINTMENT, CERTIFICATE, SUBSCRIPTION = 'application.MedicalAppointments', 'application.MedicalCertificate', 'application.Subscription'
DRAFT, DOCUMENT = 'application.AssociateImportDraft', 'docmanager.Document'
MANAGED = (APPOINTMENT, CERTIFICATE, SUBSCRIPTION, DRAFT, DOCUMENT, harness.AUDIT, harness.INDEX)
TABLES = {'MedicalAppointments': (APPOINTMENT, 'medical_appointments_id', 'appointments'),
          'MedicalCertificate': (CERTIFICATE, 'medical_id', 'certificates'), 'Subscription': (SUBSCRIPTION, 'subscription_id', 'subscriptions'),
          'AssociateImportDraft': (DRAFT, 'associate_import_draft_id', 'drafts'), 'Document': (DOCUMENT, 'document_id', 'documents')}
# The actor's own association (SportAssociation.objects.filter(user=actor).first()).
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association', 'foreign_owner': 'foreign_association'}


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
                                          deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None,
                                          start_date=day(row['start']), end_date=day(row['end']), creation_date=moment(row['created']),
                                          status_flag=subscription.ACCEPTED))
    for row in source['appointments'].values():
        insert(appointment, appointment(medical_appointments_id=row['id'], subscription_id=source['subscriptions'][row['subscription']]['id'],
                                        region=row['region'], document_id=source['documents'][row['document']]['id'], date=day(row['date']),
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
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
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


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(certificate_model,))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
