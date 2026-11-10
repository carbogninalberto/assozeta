"""Parent-only subscription calculate-tax-code, import/status, get-associations-for-federation,
generate-token-link, validate-token-link-and-get-subscriptions and delete-document HTTP runner;
imports are inert.

Seeds import statuses, associates, a payment, documents, subscriptions, subscription files and
gym token links (audit disabled) and verifies each case against the complete model state:
- generate-token-link creates one link holding the response's token and expiry, ``str()`` of
  the gym name and the owner's association (one auditlog create, indexed directly; the
  view's second save() changes nothing);
- validate-token-link deletes the expired links (auditlog deletes, indexed from changes, by the
  request's user or anonymously);
- delete-document deletes the file and, outside an association-scoped session, its Document
  (auditlog deletes of the file, the Document and the file's stale instance, indexed to the
  actor's own association, if any).
Every other case leaves the state unchanged. New links are removed before the fixture
cleanup compares the model state.
"""
from datetime import date, datetime
from decimal import Decimal

from TESTS.contracts import test_subscription_misc_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import bakney_public_links as links
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.subscription_medical_http import additional_data
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_misc_http.json', 'TESTS/contracts/test_subscription_misc_http.py',
           'TESTS/adapters/django/subscription_misc_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/subscription_medical_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/docmanager/models.py', 'BE/application/mixin.py',
           'BE/application/utils/api_utils.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py',
           'BE/instance/sso/public_links.py', 'BE/instance/sso/service.py', 'BE/instance/sso/models.py',
           'TESTS/adapters/django/bakney_public_links.py', 'TESTS/adapters/django/sso_pairing_http.py', 'BE/core/settings.py', 'BE/requirements.txt') + harness.SOURCES
LINK, FILE, DOCUMENT = 'application.CumulativeSubscriptionGymLinks', 'application.SubscriptionFile', 'docmanager.Document'
MANAGED = (LINK, FILE, DOCUMENT, harness.AUDIT, harness.INDEX)


def link_model():
    from application.models.subscriptions_models import CumulativeSubscriptionGymLinks
    return CumulativeSubscriptionGymLinks


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Associate, Payment, Subscription
    from application.models.subscriptions_models import AssociateImportDraftStatus, SubscriptionFile
    from docmanager.models import Document

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['statuses'].values():
        insert(AssociateImportDraftStatus, AssociateImportDraftStatus(
            associate_import_draft_status_id=row['id'], sport_association_id=row['association'], doing=row['doing'],
            status_type=row['type'], creation_date=moment(row['created'])))
    associates = source['associates']
    for row in associates.values():
        fields = {key: row[key] for key in contract_associate_keys() if key != 'born_date'}
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], group_id=row.get('group'),
                                    creation_date=moment(row['created']), born_date=day(row['born_date']), **fields))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], amount=Decimal(row['amount']),
                                paid=row['paid'], associate_id=associates[row['associate']]['id'], creation_date=moment(row['created'])))
    documents = source['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], creation_date=moment(row['created'])))
    subscriptions = source['subscriptions']
    for row in subscriptions.values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, creation_date=moment(row['created']),
            start_date=day(row['start']), end_date=day(row['end']), status_flag=row['status'], type=row['type'], role=row['role'],
            custom_data=row['custom_data'], deleted=row['deleted'], group_id=row['group']))
    for row in source['files'].values():
        insert(SubscriptionFile, SubscriptionFile(subscription_file_id=row['id'], subscription_id=subscriptions[row['subscription']]['id'],
                                                  document_id=documents[row['document']]['id'], creation_date=moment(row['created'])))
    for row in source['links'].values():
        insert(link_model(), link_model()(cumulative_subscription_gym_links_id=row['id'], sport_association_id=row['association'],
                                          token=row['token'], gym_name=row['gym_name'], expires_at=moment(row['expires'])))


def contract_associate_keys():
    return ('address', 'address_cap', 'address_city', 'born_city', 'born_date', 'email', 'first_name', 'last_name', 'is_minor', 'sex',
            'tax_code', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label', 'phone_4_label',
            'picture_path')


def deleted_rows(source):
    """auditlog's delete diffs of the seeded files and documents, captured before each case."""
    from application.models.subscriptions_models import SubscriptionFile
    from docmanager.models import Document
    result = {}
    for model, table in ((SubscriptionFile, 'files'), (Document, 'documents'), (link_model(), 'links')):
        for row in model._base_manager.filter(pk__in=[item['id'] for item in source[table].values()]):
            result[str(row.pk)] = replace.deleted_changes(model, row)
            if model is Document:  # auditlog renders the reverse FK accessor that declares a related_name
                result[str(row.pk)]['customer_invoice_pdf'] = ['application.CustomerInvoice.None', 'None']
    return result


def check_entries(case, added, indexes, expected, *, before, ids, actor, context):
    """added (in id order) against expected [(app, model, key, action, changes, association, path)];
    actor None is an anonymous request."""
    contract.require(len(added) == len(expected), 'audit.count')
    contract.require(len(indexes) == sum(1 for item in expected if item[4] is not None), 'index.count')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    extra = additional_data(case, context['fixture'], ids, actor)
    for event, (app, model, key, action, changes, association, path) in zip(added, expected):
        contract.require(event['content_type_id'] == types[(app, model.lower())] and event['action'] == action and event['object_pk'] == key
                         and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
        contract.require(event['changes'] == changes, 'audit.changes')
        if actor is None:
            contract.require(event['actor_id'] is None and event['actor_email'] is None, 'audit.actor')
        else:
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
    old_l, new_l = keyed(before[LINK], 'cumulative_subscription_gym_links_id'), keyed(after[LINK], 'cumulative_subscription_gym_links_id')
    old_f, new_f = keyed(before[FILE], 'subscription_file_id'), keyed(after[FILE], 'subscription_file_id')
    old_d, new_d = keyed(before[DOCUMENT], 'document_id'), keyed(after[DOCUMENT], 'document_id')
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: int(event['id']))
    kind, ids, prepared = effect['kind'], source['ids'], context['prepared']
    actor = None if case['actor'] == 'anonymous' else case['actor']
    if kind != 'document':
        contract.require(old_f == new_f and old_d == new_d, 'state.files')
    if kind == 'link':
        keys = set(new_l) - set(old_l)
        contract.require(len(keys) == 1 and set(old_l) <= set(new_l) and all(new_l[k] == old_l[k] for k in old_l), 'state.link-created')
        row = new_l[keys.pop()]
        body = contract.LAST.get('body') or {}
        # The snapshot renders datetimes like DRF (isoformat in UTC, 'Z', microseconds only when non-zero).
        contract.require(row['token'] == body.get('token') and row['expires_at'] == body.get('expires_at'), 'state.link-values')
        contract.require(row['gym_name'] == effect['gym_name'] and str(row['sport_association_id']) == effect['association'], 'state.link')
        delta = moment(row['expires_at']) - moment(row['creation_date'])
        contract.require(abs(delta.total_seconds() - 2 * 86400) < 5, 'state.link-created-at')
        instance = link_model()._base_manager.get(pk=row['cumulative_subscription_gym_links_id'])
        changes = topup.created_changes(link_model(), instance, {name: topup.auditlog_moment(getattr(instance, name))
                                                                 for name in ('expires_at', 'creation_date')})
        expected = [('application', 'CumulativeSubscriptionGymLinks', str(instance.pk), 0, changes, effect['association'], 'direct')]
        check_entries(case, added, indexes, expected, before=before, ids=ids, actor=actor, context=context)
    elif kind == 'expired-links':
        expired = {row['id'] for row in source['links'].values() if row['expires'] < '2026-01-01'}
        contract.require(set(new_l) == set(old_l) - expired and expired <= set(old_l) and all(new_l[k] == old_l[k] for k in new_l),
                         'state.links-expired')
        expected = [('application', 'CumulativeSubscriptionGymLinks', key, 2, prepared[key], str(old_l[key]['sport_association_id']),
                     'changes_json.sport_association') for key in sorted(expired)]
        check_entries(case, added, indexes, expected, before=before, ids=ids, actor=actor, context=context)
    else:
        contract.require(old_l == new_l, 'state.links')
        file_row = source['files'][effect['file']]
        key, document = file_row['id'], source['documents'][file_row['document']]['id']
        contract.require(set(new_f) == set(old_f) - {key} and all(new_f[k] == old_f[k] for k in new_f), 'state.files')
        removed = {document} if effect['document'] else set()
        contract.require(set(new_d) == set(old_d) - removed and all(new_d[k] == old_d[k] for k in new_d), 'state.documents')
        association, path = effect['association'], 'actor.sport_association'
        expected = [('application', 'SubscriptionFile', key, 2, prepared[key], association, path)]
        if effect['document']:
            expected += [('docmanager', 'Document', document, 2, prepared[document], association, path),
                         ('application', 'SubscriptionFile', key, 2, prepared[key], association, path)]
        check_entries(case, added, indexes, expected, before=before, ids=ids, actor=effect['actor'], context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(link_model,))
run_workload = harness.run_workload_for(links.Contract(contract, 'own_association'), assert_effect, prepare=deleted_rows)
# The paired-server stub of the cases with a 'pairing' entry (public_links.source_token).
service_context = links.service_context
