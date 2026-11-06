"""Parent-only subscription/associates-draft HTTP runner; imports are inert.

Seeds associate import drafts, their importing / approving statuses, a
medical certificate and a family (audit disabled) and verifies each case
against the complete model state and the auditlog entries in write order:
- list may save an expired status (one update entry, indexed directly);
- add creates one draft (create entry, the data as sorted json.dumps);
- edit saves the draft once or twice (an update entry per changed save),
  creates or links a medical certificate and may store its expiration (create
  and update entries indexed to the actor's own association, if any);
- delete and bulk-delete remove drafts (delete entries indexed from changes).
New drafts and certificates are removed before the fixture cleanup compares
the model state.
"""
import json
from datetime import date, datetime

from TESTS.contracts import test_associates_draft_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/associates_draft_http.json', 'TESTS/contracts/test_associates_draft_http.py',
           'TESTS/adapters/django/associates_draft_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/utils/api_utils.py',
           'BE/application/impersonation_scope.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py') + harness.SOURCES
DRAFT, STATUS, CERTIFICATE = 'application.AssociateImportDraft', 'application.AssociateImportDraftStatus', 'application.MedicalCertificate'
MANAGED = (DRAFT, STATUS, CERTIFICATE, harness.AUDIT, harness.INDEX)


def models():
    from application.models.subscriptions_models import AssociateImportDraft, AssociateImportDraftStatus, MedicalCertificate
    return AssociateImportDraft, AssociateImportDraftStatus, MedicalCertificate


def draft_model():
    return models()[0]


def certificate_model():
    return models()[2]


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_rows(source, created):
    from application.models import Family
    ids = source['ids']
    draft, status, certificate = models()
    for row in source['families'].values():
        Family._base_manager.bulk_create([Family(family_id=row['id'])])
        created.append((Family, row['id']))
    for row in source['certificates'].values():
        certificate._base_manager.bulk_create([certificate(medical_id=row['id'], user_id=ids[row['user']], creation_date=moment(row['created']),
                                                           expiration_date=date.fromisoformat(row['expiration']))])
        created.append((certificate, row['id']))
    for row in source['drafts'].values():
        draft._base_manager.bulk_create([draft(associate_import_draft_id=row['id'], sport_association_id=row['association'], data=row['data'],
                                               valid=row['valid'])])
        created.append((draft, row['id']))
    for row in source['statuses'].values():
        status._base_manager.bulk_create([status(associate_import_draft_status_id=row['id'], sport_association_id=row['association'],
                                                 doing=row['doing'], status_type=row['type'], creation_date=moment(row['created']))])
        created.append((status, row['id']))


def deleted_rows(source):
    """auditlog's delete diffs of the seeded drafts, captured before each case."""
    model = draft_model()
    return {str(row.pk): replace.deleted_changes(model, row) for row in model._base_manager.filter(pk__in=[d['id'] for d in source['drafts'].values()])}


dumps = lambda value: json.dumps(value, sort_keys=True)
text = lambda value: 'None' if value is None else str(value)


def placed(value, mc):
    """value with each '$mc' leaf replaced by the certificate id."""
    if value == '$mc':
        return mc
    if isinstance(value, dict):
        return {k: placed(v, mc) for k, v in value.items()}
    if isinstance(value, list):
        return [placed(v, mc) for v in value]
    return value


def check_entries(case, added, indexes, expected, *, before, ids, actor, context):
    """added (in id order) against expected [(model, key, action, changes, association, path)]."""
    contract.require(len(added) == len(expected), 'audit.count')
    contract.require(len(indexes) == sum(1 for item in expected if item[4] is not None), 'index.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    for event, (model, key, action, changes, association, path) in zip(added, expected):
        if association is not None:
            harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model=model, key=key, action=action,
                                      changes=changes, actor=actor, association=association, path=path, context=context)
            continue
        contract.require(event['content_type_id'] == types[model.lower()] and event['action'] == action and event['object_pk'] == key
                         and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        contract.require(event['actor_id'] == ids[actor] and event['actor_email'] == users[ids[actor]]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        contract.require(str(event['id']) not in indexes, 'index.absent')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_d, new_d = keyed(before[DRAFT], 'associate_import_draft_id'), keyed(after[DRAFT], 'associate_import_draft_id')
    old_s, new_s = keyed(before[STATUS], 'associate_import_draft_status_id'), keyed(after[STATUS], 'associate_import_draft_status_id')
    old_c, new_c = keyed(before[CERTIFICATE], 'medical_id'), keyed(after[CERTIFICATE], 'medical_id')
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: event['id'])
    expected = []
    kind = effect['kind']
    if kind != 'status':
        contract.require(new_s == old_s, 'state.statuses')
    if kind not in ('edit',):
        contract.require(new_c == old_c, 'state.certificates')
    if kind == 'status':
        contract.require(new_d == old_d, 'state.drafts')
        expected_s = dict(old_s)
        for label, doing in effect['rows'].items():
            key = source['statuses'][label]['id']
            expected_s[key] = {**old_s[key], 'doing': doing}
            if old_s[key]['doing'] != doing:
                expected.append(('AssociateImportDraftStatus', key, 1, {'doing': [text(old_s[key]['doing']), text(doing)]}, own, 'direct'))
        contract.require(new_s == expected_s, 'state.statuses')
    elif kind == 'add':
        keys = set(new_d) - set(old_d)
        contract.require(len(keys) == 1 and all(new_d[k] == old_d[k] for k in old_d), 'state.created')
        key = keys.pop()
        row = new_d[key]
        contract.require(str(row['sport_association_id']) == own and row['valid'] is effect['valid'], 'state.draft')
        contract.require(row['data'] == effect['stored'] and dumps(row['data']) == dumps(effect['stored']), 'state.draft-data')
        expected.append(('AssociateImportDraft', key, 0, {'associate_import_draft_id': ['None', key], 'sport_association': ['None', own],
                                                          'data': ['None', dumps(effect['stored'])], 'valid': ['None', text(effect['valid'])]},
                         own, 'direct'))
    elif kind == 'delete':
        keys = {source['drafts'][label]['id'] for label in effect['labels']}
        contract.require(set(new_d) == set(old_d) - keys and all(new_d[k] == old_d[k] for k in new_d), 'state.deleted')
        expected.extend(('AssociateImportDraft', key, 2, context['prepared'][key], own, 'changes_json.sport_association')
                        for key in keys)
        # Bulk deletes follow the collector's order, which auditlog does not fix: compare as a set.
        order = {event['object_pk']: index for index, event in enumerate(added)}
        expected.sort(key=lambda item: order.get(item[1], -1))
    else:
        key = source['drafts'][effect['draft']]['id']
        certificate = effect['certificate']
        actor_association = own if effect['actor'] == 'owner' else None
        actor_path = 'actor.sport_association' if actor_association else None
        if certificate['label'] is None:
            created = set(new_c) - set(old_c)
            contract.require(len(created) == 1 and set(old_c) <= set(new_c) and all(new_c[k] == old_c[k] for k in old_c), 'state.certificate-created')
            mc = created.pop()
            row = new_c[mc]
            contract.require(str(row['user_id']) == ids['owner'] and row['document_id'] is None and row['notes'] is None
                             and row['competitive_medical_certificate'] is False, 'state.certificate')
            old_expiration = None
        else:
            mc = source['certificates'][certificate['label']]['id']
            contract.require(set(new_c) == set(old_c) and all(new_c[k] == old_c[k] for k in old_c if k != mc), 'state.certificates')
            row = new_c[mc]
            old_expiration = old_c[mc]['expiration_date']
            contract.require({k: v for k, v in row.items() if k != 'expiration_date'} == {k: v for k, v in old_c[mc].items() if k != 'expiration_date'},
                             'state.certificate')
        stored_expiration = date.fromisoformat(certificate['expiration']) if certificate['expiration'] else old_expiration
        contract.require(row['expiration_date'] == stored_expiration, 'state.certificate-expiration')
        saves = [placed(data, mc) for data in effect['saves']]
        contract.require(set(new_d) == set(old_d) and all(new_d[k] == old_d[k] for k in old_d if k != key), 'state.drafts')
        final_data, final_valid = (saves[-1], effect['valid']) if saves else (old_d[key]['data'], old_d[key]['valid'])
        contract.require(new_d[key]['data'] == final_data and dumps(new_d[key]['data']) == dumps(final_data)
                         and new_d[key]['valid'] is final_valid and new_d[key]['sport_association_id'] == old_d[key]['sport_association_id'],
                         'state.draft')
        previous = {'data': dumps(old_d[key]['data']), 'valid': text(old_d[key]['valid'])}

        def save(data):
            nonlocal previous
            current = {'data': dumps(data), 'valid': text(effect['valid'])}
            changes = {name: [previous[name], current[name]] for name in ('data', 'valid') if previous[name] != current[name]}
            previous = current
            if changes:
                expected.append(('AssociateImportDraft', key, 1, changes, own, 'direct'))

        pending = list(saves)
        if effect['first_save']:
            save(pending.pop(0))
        if certificate['label'] is None:
            instance = certificate_model()._base_manager.get(pk=mc)
            changes = topup.created_changes(certificate_model(), instance, {'creation_date': topup.auditlog_moment(instance.creation_date)})
            changes.pop('expiration_date', None)  # set after the create
            expected.append(('MedicalCertificate', mc, 0, changes, actor_association, actor_path))
        if certificate['updated']:
            expected.append(('MedicalCertificate', mc, 1, {'expiration_date': [text(old_expiration), f"{certificate['expiration']} 00:00:00+00:00"]},
                             actor_association, actor_path))
        if effect['final_save']:
            save(pending.pop(0))
        contract.require(not pending, 'effect.saves')
    check_entries(case, added, indexes, expected, before=before, ids=ids, actor=effect['actor'], context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(draft_model, certificate_model))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
