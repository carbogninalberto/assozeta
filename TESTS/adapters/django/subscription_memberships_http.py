"""Parent-only subscription/memberships list, add, update and delete HTTP runner; imports are inert.

Seeds documents, associates, subscriptions, payments and memberships with
their document links (audit disabled) and verifies each case against the
complete model state: reads change nothing; membership creates, updates and
deletes write one auditlog entry each (none for an unchanged update),
document links are removed only with their membership and documents,
associates, subscriptions and payments are kept. New memberships are removed
before the fixture cleanup compares the model state.
"""
from datetime import date, datetime
from decimal import Decimal

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
           'BE/application/audit_resolvers.py', 'BE/docmanager/models.py') + harness.SOURCES
MEMBERSHIP, LINK = 'application.SubscriptionMembership', 'application.SubscriptionMembership_attached_membership_documents'
MANAGED = (MEMBERSHIP, LINK, harness.AUDIT, harness.INDEX)
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


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    old_m, new_m = keyed(before[MEMBERSHIP], 'subscription_membership_id'), keyed(after[MEMBERSHIP], 'subscription_membership_id')
    if effect['kind'] == 'add':
        keys = set(new_m) - set(old_m)
        contract.require(len(keys) == 1 and set(old_m) <= set(new_m) and all(new_m[k] == old_m[k] for k in old_m), 'state.created')
        key = keys.pop()
        contract.require(effect['id'] == '$new' or key == effect['id'], 'state.created-id')
        contract.require(stored(new_m[key], effect['stored']) == effect['stored'], 'state.membership')
        contract.require(after[LINK] == before[LINK], 'state.links')
        action, path = 0, 'direct'
        row = membership_model()._base_manager.get(pk=key)
        changes = topup.created_changes(membership_model(), row, {'creation_date': topup.auditlog_moment(row.creation_date)})
    elif effect['kind'] == 'update':
        key = source['memberships'][effect['label']]['id']
        contract.require(set(new_m) == set(old_m) and stored(new_m[key], effect['stored']) == effect['stored']
                         and all(new_m[k] == old_m[k] for k in old_m if k != key), 'state.membership')
        contract.require(after[LINK] == before[LINK], 'state.links')
        action, path, changes = 1, 'direct', effect['changes']
    else:
        key = source['memberships'][effect['label']]['id']
        contract.require(set(new_m) == set(old_m) - {key}, 'state.deleted')
        kept = {pair for pair in harness.pairs(before[LINK], 'subscriptionmembership_id', 'document_id') if pair[0] != key}
        contract.require(harness.pairs(after[LINK], 'subscriptionmembership_id', 'document_id') == kept and len(after[LINK]) == len(kept),
                         'state.links')
        action, path, changes = 2, 'changes_json.sport_association', context['prepared'][key]
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    for event in added:
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='SubscriptionMembership', key=key,
                                  action=action, changes=changes, actor=effect['actor'], association=own, path=path, context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(membership_model,))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
