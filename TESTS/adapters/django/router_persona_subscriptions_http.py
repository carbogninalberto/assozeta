"""Parent-only DefaultRouter personas-subscriptions HTTP runner; imports are inert.

Reuses the personas-writes seed. Reads and refused requests leave the state
unchanged. The stock destroy is Subscription.delete() through Django's
Collector: the subscription row and its tag links disappear (nothing else
changes), with one auditlog delete entry whose changes are auditlog's own
diff of the row read before the request, indexed through
changes_json.sport_association.

The stock update (persona-subs-writes.md) changes exactly the authored
columns of one subscription, sets its tag links to the authored list (links
it keeps are untouched) and appends one auditlog update entry with the
authored changes, indexed directly to the association (none when no
audited field changed). Create writes nothing.
"""
import ipaddress

from TESTS.contracts import test_router_persona_subscriptions_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import personas_writes_http as personas
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/router_persona_subscriptions_http.json', 'TESTS/fixtures/generate_personas_writes_http.py',
           'TESTS/contracts/test_router_persona_subscriptions_http.py', 'TESTS/adapters/django/router_persona_subscriptions_http.py',
           'TESTS/contracts/test_personas_writes_http.py', 'BE/application/serializers/personas_serializers.py') \
    + tuple(source for source in personas.SOURCES if source not in harness.SOURCES and 'personas_writes_http.json' not in source
            and 'test_personas_writes_http' not in source) + harness.SOURCES
SUBSCRIPTION, TAGS = 'application.Subscription', 'application.Subscription_tags'
MANAGED = (SUBSCRIPTION, TAGS, harness.AUDIT, harness.INDEX)
fixture_context = harness.fixture_context_for(contract, personas.seed_rows, purge=(personas.associate_model,))


def captured(source):
    """auditlog's delete diff (model_instance_diff(row, None)) of each seeded subscription."""
    from auditlog.diff import model_instance_diff
    from application.models import Subscription
    return {str(row.pk): {name: list(pair) for name, pair in model_instance_diff(row, None).items()}
            for row in Subscription._base_manager.filter(pk__in=[r['id'] for r in source['subscriptions'].values()])}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    if effect['kind'] == 'update':
        assert_update(case, effect, before, after, source, context)
        return
    if effect['kind'] == 'audit':
        assert_audit_only(case, effect, before, after, source, context)
        return
    key = effect['subscription']
    old, new = keyed(before[SUBSCRIPTION], 'subscription_id'), keyed(after[SUBSCRIPTION], 'subscription_id')
    old, new = {str(k): v for k, v in old.items()}, {str(k): v for k, v in new.items()}
    contract.require(key in old and {k: v for k, v in old.items() if k != key} == new, 'state.subscriptions')
    kept = [row for row in before[TAGS] if str(personas.flat(row)['subscription_id']) != key]
    contract.require(after[TAGS] == kept, 'state.tags')
    entries, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(entries) == 1 and len(indexes) == 1, 'audit.count')
    event, ids = entries[0], source['ids']
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['content_type_id'] == types['subscription'] and event['action'] == 2 and event['object_pk'] == key, 'audit.identity')
    contract.require(event['changes'] == context['prepared'][key], 'audit.changes')
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes[str(event['id'])]
    contract.require([index['sport_association_id'], index['resolution_path']] == [ids['own_association'], 'changes_json.sport_association'],
                     'index.fields')


def assert_update(case, effect, before, after, source, context):
    key = effect['subscription']
    old = {str(k): personas.flat(v) for k, v in keyed(before[SUBSCRIPTION], 'subscription_id').items()}
    new = {str(k): personas.flat(v) for k, v in keyed(after[SUBSCRIPTION], 'subscription_id').items()}
    contract.require(set(old) == set(new) and all(old[k] == new[k] for k in old if k != key), 'state.other-subscriptions')
    contract.require(new[key] == {**old[key], **effect['columns']}, 'state.subscription')
    link = lambda row: str(personas.flat(row)['subscription_id'])
    others = lambda rows: [row for row in rows if link(row) != key]
    contract.require(others(before[TAGS]) == others(after[TAGS]), 'state.other-tags')
    mine_before = [row for row in before[TAGS] if link(row) == key]
    mine_after = [row for row in after[TAGS] if link(row) == key]
    if effect['tags'] is None:
        contract.require(mine_before == mine_after, 'state.tags')
    else:
        contract.require(sorted(str(personas.flat(row)['tags_id']) for row in mine_after) == sorted(effect['tags']), 'state.tags')
        kept = [row for row in mine_before if str(personas.flat(row)['tags_id']) in effect['tags']]
        contract.require(all(row in mine_after for row in kept), 'state.tags-kept')
    entries, indexes = harness.audit_delta(contract, before, after)
    if effect['changes'] is None:
        contract.require(not entries and not indexes, 'audit.count')
        return
    contract.require(len(entries) == 1 and len(indexes) == 1, 'audit.count')
    harness.check_audit_event(contract, case, entries[0], indexes, before=before, ids=source['ids'], model='Subscription', key=key,
                              action=1, changes=effect['changes'], actor=effect['actor'], association=source['ids']['own_association'],
                              path='direct', context=context)


def assert_audit_only(case, effect, before, after, source, context):
    """A save that fails after auditlog's pre_save entry: that entry and its index row, nothing else."""
    contract.require(before[SUBSCRIPTION] == after[SUBSCRIPTION] and before[TAGS] == after[TAGS], 'state.unchanged')
    entries, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(entries) == 1 and len(indexes) == 1, 'audit.count')
    harness.check_audit_event(contract, case, entries[0], indexes, before=before, ids=source['ids'], model='Subscription',
                              key=effect['subscription'], action=1, changes=effect['changes'], actor=effect['actor'],
                              association=source['ids']['own_association'], path='direct', context=context)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
