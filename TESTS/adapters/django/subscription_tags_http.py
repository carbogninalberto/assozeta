"""Parent-only subscription tags HTTP runner; imports are inert.

Seeds tags, subscriptions and their tag links (audit disabled) and verifies
each case against the complete model state: tag creates, renames and
deletes with one auditlog entry each (none for an unchanged name), link
changes on the Subscription M2M, and the dates Subscription.save() fills
(one auditlog update of the subscription, none when nothing changed). New
tags are removed before the fixture cleanup compares the model state.
"""
from datetime import date, datetime

from TESTS.contracts import test_subscription_tags_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_tags_http.json', 'TESTS/contracts/test_subscription_tags_http.py',
           'TESTS/adapters/django/subscription_tags_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/services/subscription_service.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/mixin.py',
           'BE/application/utils/api_utils.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py') + harness.SOURCES
TAG, LINK, SUBSCRIPTION = 'application.Tags', 'application.Subscription_tags', 'application.Subscription'
MANAGED = (TAG, LINK, SUBSCRIPTION, harness.AUDIT, harness.INDEX)


def tag_model():
    from application.models.subscriptions_models import Tags
    return Tags


def seed_rows(source, created):
    from application.models.subscriptions_models import Subscription, Tags
    ids = source['ids']
    for row in source['tags'].values():
        Tags._base_manager.bulk_create([Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=row['association'])])
        created.append((Tags, row['id']))
    day = lambda text: date.fromisoformat(text) if text else None
    for row in source['subscriptions'].values():
        Subscription._base_manager.bulk_create([Subscription(subscription_id=row['id'], sport_association_id=row['association'],
            user_id=ids['athlete'], start_date=day(row['start']), end_date=day(row['end']), deleted=row['deleted'],
            group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED,
            creation_date=datetime.fromisoformat(row['created'].replace('Z', '+00:00')))])
        Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=row['id'], tags_id=source['tags'][label]['id'])
                                                       for label in row['tags']])
        created.append((Subscription, row['id']))


def deleted_rows(source):
    """auditlog's delete diffs of the seeded tags, captured before each case."""
    model = tag_model()
    return {str(row.pk): replace.deleted_changes(model, row) for row in model._base_manager.filter(pk__in=[t['id'] for t in source['tags'].values()])}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    own = ids['own_association']
    pair = lambda labels: (source['tags'][labels[0]]['id'], source['subscriptions'][labels[1]]['id'])
    links = lambda state: harness.pairs(state[LINK], 'tags_id', 'subscription_id')
    expected_links = (links(before) | {pair(p) for p in effect.get('add', [])}) - {pair(p) for p in effect.get('remove', [])}
    contract.require(links(after) == expected_links and len(after[LINK]) == len(expected_links), 'state.links')
    old_t, new_t = keyed(before[TAG], 'tag_id'), keyed(after[TAG], 'tag_id')
    old_s, new_s = keyed(before[SUBSCRIPTION], 'subscription_id'), keyed(after[SUBSCRIPTION], 'subscription_id')
    dates = effect.get('dates', {})
    expected_s = dict(old_s)
    for label, (start, end) in dates.items():
        key = source['subscriptions'][label]['id']
        expected_s[key] = {**old_s[key], 'start_date': date.fromisoformat(start), 'end_date': date.fromisoformat(end)}
    contract.require(new_s == expected_s, 'state.subscriptions')
    added, indexes = harness.audit_delta(contract, before, after)
    if effect['kind'] == 'link':
        contract.require(new_t == old_t, 'state.tags')
        contract.require(len(added) == len(dates) and len(indexes) == len(added), 'audit.count')
        by_key = {event['object_pk']: event for event in added}
        for label, (start, end) in dates.items():
            key = source['subscriptions'][label]['id']
            contract.require(key in by_key and old_s[key]['start_date'] is None and old_s[key]['end_date'] is None, 'audit.subscription')
            harness.check_audit_event(contract, case, by_key[key], indexes, before=before, ids=ids, model='Subscription', key=key, action=1,
                                      changes={'start_date': ['None', start], 'end_date': ['None', end]}, actor=effect['actor'],
                                      association=own, path='direct', context=context)
        return
    if effect['kind'] == 'add':
        keys = set(new_t) - set(old_t)
        contract.require(len(keys) == 1 and all(new_t[k] == old_t[k] for k in old_t), 'state.created')
        key = keys.pop()
        row = tag_model()._base_manager.get(pk=key)
        contract.require(row.tag_name == effect['stored'] and str(row.sport_association_id) == own, 'state.tag')
        action, path = 0, 'direct'
        changes = topup.created_changes(tag_model(), row, {'creation_date': topup.auditlog_moment(row.creation_date)})
    elif effect['kind'] == 'update':
        key = source['tags'][effect['tag']]['id']
        changes = effect['changes']
        expected = dict(old_t[key], tag_name=changes['tag_name'][1]) if changes else old_t[key]
        contract.require(set(new_t) == set(old_t) and new_t[key] == expected and all(new_t[k] == old_t[k] for k in old_t if k != key), 'state.tag')
        action, path = 1, 'direct'
    else:
        key = source['tags'][effect['tag']]['id']
        contract.require(set(new_t) == set(old_t) - {key}, 'state.deleted')
        action, path, changes = 2, 'changes_json.sport_association', context['prepared'][key]
    contract.require(len(added) == (1 if changes else 0) and len(indexes) == len(added), 'audit.count')
    for event in added:
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='Tags', key=key, action=action,
                                  changes=changes, actor=effect['actor'], association=own, path=path, context=context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(tag_model,))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
