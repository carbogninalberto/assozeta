"""Parent-only camps-and-retreats list, add, info, update and delete HTTP runner; imports are inert.

Seeds the camps periods fixture shape (payment categories, camps, periods,
period services, camp subscriptions, a payment, subscription periods and their
link rows; audit disabled) through the camps periods seeder. Each case is
checked against the complete model state: only the authored rows of camps,
periods, services, subscriptions, subscription periods and payments change
(a created camp is matched as the one new key), link rows change by the
authored pairs, and every new auditlog entry matches an authored event with
its index row. Rows the run creates are removed before the fixture cleanup
compares the model state.
"""
from TESTS.contracts import test_camps_core_http as contract
from TESTS.adapters.django import camps_periods_http as periods
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/camps_core_http.json', 'TESTS/contracts/test_camps_core_http.py', 'TESTS/contracts/test_camps_periods_http.py',
           'TESTS/adapters/django/camps_core_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/camp_and_retreats_views.py', 'BE/application/serializers/camps_and_retreats_serializers.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/models/courses_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/utils/api_utils.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
CAMP, SUBSCRIPTION = 'application.CampsAndRetreats', 'application.CampsAndRetreatsSubscription'
PERIOD, SERVICE, SUBPERIOD, PAYMENT = periods.PERIOD, periods.SERVICE, periods.SUBPERIOD, periods.PAYMENT
PERIOD_LINK, SUBPERIOD_LINK = periods.PERIOD_LINK, periods.SUBPERIOD_LINK
KEYS = {**periods.KEYS, CAMP: 'camps_and_retreats_id', SUBSCRIPTION: 'camps_and_retreats_subscription_id'}
ROWS = (CAMP, PERIOD, SERVICE, SUBSCRIPTION, SUBPERIOD, PAYMENT)
MANAGED = ROWS + (PERIOD_LINK, SUBPERIOD_LINK, harness.AUDIT, harness.INDEX)


def models():
    from application.models import CampsAndRetreats, CampsAndRetreatsSubscription
    return {**periods.models(), CAMP: CampsAndRetreats, SUBSCRIPTION: CampsAndRetreatsSubscription}


def deleted_rows(source):
    """auditlog's delete diffs of every camp row of the managed models, captured before each case."""
    return {str(row.pk): replace.deleted_changes(model, row) for label, model in models().items() for row in model._base_manager.all()}


def rows_of(state, label):
    return {key: periods.plain(row) for key, row in keyed(state[label], KEYS[label]).items()}


def check_rows(label, before, after, authored, context):
    old, new = rows_of(before, label), rows_of(after, label)
    authored = authored or {}
    created = set(new) - set(old)
    expected_new = [key for key in authored if key == '$new' or (key not in old and authored[key] is not None)]
    contract.require(len(created) == len(expected_new), 'state.created-count:' + label)
    for key in expected_new:
        if key == '$new':
            fresh = created - set(authored)
            contract.require(len(fresh) == 1, 'state.created:' + label)
            actual = fresh.pop()
            context['new'] = actual
        else:
            contract.require(key in created, 'state.created-id:' + label)
            actual = key
        contract.require(all(new[actual][column] == value for column, value in authored[key].items()), 'state.created-row:' + label)
    for key, row in old.items():
        if key in authored and authored[key] is None:
            contract.require(key not in new, 'state.deleted:' + label)
        elif key in authored:
            contract.require(key in new and all(new[key][c] == v for c, v in authored[key].items())
                             and all(new[key][c] == row[c] for c in row if c not in authored[key]), 'state.updated:' + label)
        else:
            contract.require(new.get(key) == row, 'state.unchanged:' + label)


def substitute(changes, context):
    return {field: [context.get('new') if value == '$key' else value for value in pair] for field, pair in changes.items()}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    created = {}
    for label in ROWS:
        check_rows(label, before, after, effect['rows'].get(label), created)
    for label in (PERIOD_LINK, SUBPERIOD_LINK):
        periods.check_links(label, before, after, effect.get('links', {}).get(label))
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']) and len(indexes) <= len(added), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    remaining = sorted(added, key=lambda event: event['id'])
    for spec in effect['audit']:
        key = created.get('new') if spec['key'] == '$new' else spec['key']
        matches = [event for event in remaining if event['object_pk'] == key and event['action'] == spec['action']
                   and event['content_type_id'] == types[spec['model'].lower()]]
        contract.require(matches, 'audit.missing')
        event = matches[0]
        remaining.remove(event)
        changes = context['prepared'][key] if spec['changes'] == '$deleted' else substitute(spec['changes'], created)
        periods.check_event(case, event, indexes, before=before, ids=source['ids'], spec=spec, key=key, changes=changes, context=context)
    contract.require(len(indexes) == sum(1 for spec in effect['audit'] if spec['association'] is not None), 'index.count')


fixture_context = harness.fixture_context_for(contract, periods.seed_rows, purge=(lambda: models()[CAMP],))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
