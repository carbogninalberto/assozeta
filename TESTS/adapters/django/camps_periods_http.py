"""Parent-only camps-and-retreats periods and period services HTTP runner; imports are inert.

Seeds payment categories, camps, periods, period services, camp
subscriptions, payments, subscription periods and their link rows (audit
disabled). Each case is checked against the complete model state: only the
authored rows of periods, services, subscription periods and payments change
(created rows are matched as the one new key), link rows change by the
authored pairs, and every new auditlog entry matches an authored event with
its index row (or none when the actor has no association). Rows the run
creates are removed before the fixture cleanup compares the model state.
"""
from TESTS.contracts import test_camps_periods_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/camps_periods_http.json', 'TESTS/contracts/test_camps_periods_http.py',
           'TESTS/adapters/django/camps_periods_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/camp_and_retreats_views.py', 'BE/application/serializers/camps_and_retreats_serializers.py',
           'BE/application/serializers/payment_serializers.py', 'BE/application/models/courses_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/utils/api_utils.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
PERIOD, SERVICE, SUBPERIOD, PAYMENT = ('application.CampsAndRetreatsPeriod', 'application.CampsAndRetreatsPeriodsService',
                                       'application.CampsAndRetreatsSubscriptionPeriod', 'application.Payment')
PERIOD_LINK = 'application.CampsAndRetreatsPeriod_camps_and_Retreats_period_services'
SUBPERIOD_LINK = 'application.CampsAndRetreatsSubscriptionPeriod_camps_and_retreats_period_services'
LINK_COLUMNS = {PERIOD_LINK: ('campsandretreatsperiod_id', 'campsandretreatsperiodsservice_id'),
                SUBPERIOD_LINK: ('campsandretreatssubscriptionperiod_id', 'campsandretreatsperiodsservice_id')}
KEYS = {PERIOD: 'camps_and_retreats_period_id', SERVICE: 'camps_and_retreats_period_service_id',
        SUBPERIOD: 'camps_and_retreats_subscription_period_id', PAYMENT: 'payment_id'}
MANAGED = (PERIOD, SERVICE, SUBPERIOD, PAYMENT, PERIOD_LINK, SUBPERIOD_LINK, harness.AUDIT, harness.INDEX)
MODEL_NAMES = {PERIOD: 'CampsAndRetreatsPeriod', SERVICE: 'CampsAndRetreatsPeriodsService', SUBPERIOD: 'CampsAndRetreatsSubscriptionPeriod'}


def models():
    from application.models import CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscriptionPeriod
    return {PERIOD: CampsAndRetreatsPeriod, SERVICE: CampsAndRetreatsPeriodsService, SUBPERIOD: CampsAndRetreatsSubscriptionPeriod}


def when(text):
    from datetime import datetime
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_rows(source, created):
    from decimal import Decimal
    from application.models import (CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService, CampsAndRetreatsSubscription,
                                    CampsAndRetreatsSubscriptionPeriod, Payment, PaymentCategory)

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['categories'].values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], creation_date=when(row['created']),
                                                sport_association_id=row['association']))
    for row in source['camps'].values():
        insert(CampsAndRetreats, CampsAndRetreats(camps_and_retreats_id=row['id'], title=row['title'], description=row['description'],
                                                  sport_association_id=row['association']))
    camps, periods = source['camps'], source['periods']
    for row in periods.values():
        insert(CampsAndRetreatsPeriod, CampsAndRetreatsPeriod(
            camps_and_retreats_period_id=row['id'], camps_and_retreats_id=camps[row['camp']]['id'], start_date=when(row['start']),
            end_date=when(row['end']), title=row['title'], description=row['description'], fee=Decimal(row['fee']),
            max_participants=row['max'], created_at=when(row['created'])))
    categories = source['categories']
    for row in source['services'].values():
        insert(CampsAndRetreatsPeriodsService, CampsAndRetreatsPeriodsService(
            camps_and_retreats_period_service_id=row['id'], camps_and_retreats_period_id=periods[row['period']]['id'], title=row['title'],
            description=row['description'], fee=Decimal(row['fee']),
            payment_category_id=categories[row['category']]['id'] if row['category'] else None, created_at=when(row['created'])))
    for row in source['subscriptions'].values():
        insert(CampsAndRetreatsSubscription, CampsAndRetreatsSubscription(
            camps_and_retreats_subscription_id=row['id'], camps_and_retreats_id=camps[row['camp']]['id'], created_at=when(row['created'])))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], amount=Decimal(row['amount']), paid=row['paid'], subject=row['subject'],
                                description=row['description'], payment_category_id=categories[row['category']]['id'],
                                notes=row['notes'], meta_payment_categories=row['meta'], meta=row.get('payment_meta'),
                                creation_date=when(row['created']),
                                deleted=row.get('deleted', False),
                                sport_association_id=source['ids']['own_association']))
    services, payments = source['services'], source['payments']
    for row in source['subscription_periods'].values():
        insert(CampsAndRetreatsSubscriptionPeriod, CampsAndRetreatsSubscriptionPeriod(
            camps_and_retreats_subscription_period_id=row['id'], camps_and_retreats_period_id=periods[row['period']]['id'],
            camps_and_retreats_subscription_id=source['subscriptions'][row['subscription']]['id'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None))
        through = CampsAndRetreatsSubscriptionPeriod.camps_and_retreats_period_services.through
        through.objects.bulk_create([through(campsandretreatssubscriptionperiod_id=row['id'],
                                             campsandretreatsperiodsservice_id=services[s]['id']) for s in row['services']])
    through = CampsAndRetreatsPeriod.camps_and_Retreats_period_services.through
    for label, linked in source['period_links'].items():
        through.objects.bulk_create([through(campsandretreatsperiod_id=periods[label]['id'], campsandretreatsperiodsservice_id=services[s]['id'])
                                     for s in linked])


def deleted_rows(source):
    """auditlog's delete diffs of every camp period, service and subscription period, captured before each case."""
    return {str(row.pk): replace.deleted_changes(model, row) for model in models().values() for row in model._base_manager.all()}


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'} and value['type'] in ('uuid', 'datetime', 'decimal'):
        return value['value']
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def rows_of(state, label):
    return {key: plain(row) for key, row in keyed(state[label], KEYS[label]).items()}


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
        row = new[actual]
        contract.require(all(row[column] == value for column, value in authored[key].items()), 'state.created-row:' + label)
        contract.require(isinstance(row.get('created_at'), str), 'state.created-at:' + label)
        context['created_at'] = row.get('created_at')
    for key, row in old.items():
        if key in authored and authored[key] is None:
            contract.require(key not in new, 'state.deleted:' + label)
        elif key in authored:
            contract.require(key in new and all(new[key][c] == v for c, v in authored[key].items())
                             and all(new[key][c] == row[c] for c in row if c not in authored[key]), 'state.updated:' + label)
        else:
            contract.require(new.get(key) == row, 'state.unchanged:' + label)


def check_links(label, before, after, authored):
    left, right = LINK_COLUMNS[label]
    old = harness.pairs(before[label], left, right)
    new = harness.pairs(after[label], left, right)
    authored = authored or {'add': [], 'remove': []}
    expected = (old - {tuple(pair) for pair in authored['remove']}) | {tuple(pair) for pair in authored['add']}
    contract.require(new == expected and len(after[label]) == len(new), 'state.links:' + label)
    kept = {(str(row[left]), str(row[right])): row for row in before[label]}
    contract.require(all(row == kept[(str(row[left]), str(row[right]))] for row in after[label]
                         if (str(row[left]), str(row[right])) in kept and (str(row[left]), str(row[right])) not in
                         {tuple(pair) for pair in authored['add']}), 'state.links-kept:' + label)


def substitute(changes, context):
    from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
    created = when(context['created_at']) if context.get('created_at') else None
    return {field: [{'$key': context.get('new'), '$created': auditlog_moment(created) if created else None}.get(value, value)
                    for value in pair] for field, pair in changes.items()}


def check_event(case, event, indexes, *, before, ids, spec, key, changes, context):
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor_id = ids[case['effect']['actor']]
    model = spec['model']
    contract.require(event['content_type_id'] == types[model.lower()] and event['action'] == spec['action'] and event['object_pk'] == key
                     and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
    import ipaddress
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    if spec['association'] is None:
        contract.require(index is None, 'index.absent')
    else:
        contract.require(index is not None and index['sport_association_id'] == spec['association']
                         and index['resolution_path'] == spec['path'], 'index.fields')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    created = {}
    for label in (PERIOD, SERVICE, SUBPERIOD, PAYMENT):
        check_rows(label, before, after, effect['rows'].get(label), created)
    for label in (PERIOD_LINK, SUBPERIOD_LINK):
        check_links(label, before, after, effect.get('links', {}).get(label))
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']) and len(indexes) <= len(added), 'audit.count')
    remaining = sorted(added, key=lambda event: event['id'])
    for spec in effect['audit']:
        key = created.get('new') if spec['key'] == '$new' else spec['key']
        matches = [event for event in remaining if event['object_pk'] == key and event['action'] == spec['action']
                   and event['content_type_id'] == next(ct['id'] for ct in before['contenttypes.ContentType']
                                                        if ct['app_label'] == 'application' and ct['model'] == spec['model'].lower())]
        contract.require(matches, 'audit.missing')
        event = matches[0]
        remaining.remove(event)
        changes = context['prepared'][key] if spec['changes'] == '$deleted' else substitute(spec['changes'], created)
        check_event(case, event, indexes, before=before, ids=source['ids'], spec=spec, key=key, changes=changes, context=context)
    contract.require(len(indexes) == sum(1 for spec in effect['audit'] if spec['association'] is not None), 'index.count')


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(lambda: models()[PERIOD], lambda: models()[SERVICE]))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
