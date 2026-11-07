"""Parent-only camps-and-retreats subscriptions HTTP runner; imports are inert.

Seeds a family, associates, registrations, a bank account, payment
categories, camps, periods, period services, camp subscriptions, payments,
subscription periods and their link rows (audit disabled). Each case is
checked against the complete model state: only camp subscriptions,
subscription periods, payments and subscription period link rows change;
deleted rows are the authored ones (rows created by earlier cases are named by
their subscription and period), created rows match the authored columns
(subscription periods by period), SET_NULL clears references to deleted
payments, link rows follow the deleted and created subscription periods, every
new auditlog entry matches an authored event with its index row, and the
response's bound keys are the created rows. Rows the run creates are removed
before the fixture cleanup compares the model state.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from TESTS.contracts import test_camps_subscriptions_http as contract
from TESTS.adapters.django import camps_periods_http as periods
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.course_subscriptions_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/camps_subscriptions_http.json', 'TESTS/contracts/test_camps_subscriptions_http.py',
           'TESTS/contracts/test_camps_periods_http.py', 'TESTS/adapters/django/camps_subscriptions_http.py',
           'TESTS/adapters/django/camps_periods_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/camp_and_retreats_views.py', 'BE/application/utils/camps_and_retreats_utils.py',
           'BE/application/serializers/camps_and_retreats_serializers.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/subscriptions_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/courses_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/utils/api_utils.py', 'BE/application/mixin.py',
           'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
CSUB, SUBPERIOD, PAYMENT = 'application.CampsAndRetreatsSubscription', periods.SUBPERIOD, periods.PAYMENT
LINK = periods.SUBPERIOD_LINK
KEYS = {CSUB: 'camps_and_retreats_subscription_id', SUBPERIOD: 'camps_and_retreats_subscription_period_id', PAYMENT: 'payment_id'}
MANAGED = (CSUB, SUBPERIOD, PAYMENT, LINK, harness.AUDIT, harness.INDEX)


def models():
    from application.models import CampsAndRetreatsSubscription, CampsAndRetreatsSubscriptionPeriod, Payment
    return {CSUB: CampsAndRetreatsSubscription, SUBPERIOD: CampsAndRetreatsSubscriptionPeriod, PAYMENT: Payment}


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService,
                                    CampsAndRetreatsSubscription, CampsAndRetreatsSubscriptionPeriod, Family, Payment, PaymentCategory,
                                    Subscription)
    from application.models.balance_sheet_models import CustomAccounts
    ids = source['ids']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    insert(Family, Family(family_id=source['family']))
    account = source['account']
    insert(CustomAccounts, CustomAccounts(custom_account_id=account['id'], name=account['name'], initial_balance=Decimal('0.00'),
                                          account_type=CustomAccounts.BANK, account_code=account['code'], sport_association_id=ids['own_association'],
                                          editable=False, enabled=True))
    categories = source['categories']
    for row in categories.values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], creation_date=periods.when(row['created']),
                                                sport_association_id=row['association']))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=row['user'], first_name=row['first'],
                                    last_name=row['last'], sex=row['sex'], tax_code=row['tax'], born_date=day(row['born']), email=row['email'],
                                    is_minor=False, family_id=row['family'], family_role=row['role']))
    registrations = source['registrations']
    for row in registrations.values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                          associate_id=associates[row['associate']]['id'] if row['associate'] else None, start_date=day(row['start']), end_date=day(row['end']),
                                          creation_date=periods.when(row['created']), archived=row['archived'], deleted=row['deleted'],
                                          group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED))
    camps, plan, services = source['camps'], source['periods'], source['services']
    for row in camps.values():
        insert(CampsAndRetreats, CampsAndRetreats(camps_and_retreats_id=row['id'], title=row['title'], description=row['description'],
                                                  sport_association_id=row['association']))
    for row in plan.values():
        insert(CampsAndRetreatsPeriod, CampsAndRetreatsPeriod(
            camps_and_retreats_period_id=row['id'], camps_and_retreats_id=camps[row['camp']]['id'], start_date=periods.when(row['start']),
            end_date=periods.when(row['end']), title=row['title'], description=row['description'], fee=Decimal(row['fee']),
            max_participants=row['max'], created_at=periods.when(row['created'])))
    for row in services.values():
        insert(CampsAndRetreatsPeriodsService, CampsAndRetreatsPeriodsService(
            camps_and_retreats_period_service_id=row['id'], camps_and_retreats_period_id=plan[row['period']]['id'], title=row['title'],
            description=row['description'], fee=Decimal(row['fee']),
            payment_category_id=categories[row['category']]['id'] if row['category'] else None, created_at=periods.when(row['created'])))
    through = CampsAndRetreatsPeriod.camps_and_Retreats_period_services.through
    for label, linked in source['period_links'].items():
        through.objects.bulk_create([through(campsandretreatsperiod_id=plan[label]['id'], campsandretreatsperiodsservice_id=services[s]['id'])
                                     for s in linked])
    subscriptions = source['camp_subscriptions']
    for row in subscriptions.values():
        insert(CampsAndRetreatsSubscription, CampsAndRetreatsSubscription(
            camps_and_retreats_subscription_id=row['id'], camps_and_retreats_id=camps[row['camp']]['id'], status_flag=row['status'],
            subscription_id=registrations[row['registration']]['id'] if row['registration'] else None, created_at=periods.when(row['created'])))
    payments = source['payments']
    for row in payments.values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                associate_id=associates[row['associate']]['id'], amount=Decimal(row['amount']), paid=row['paid'],
                                deleted=row['deleted'], subject=Payment.OTHER, description=row['description'],
                                payment_category_id=categories[row['category']]['id'] if row['category'] else None, notes=row['notes'],
                                meta_payment_categories=row['meta'], creation_date=periods.when(row['created'])))
    link = CampsAndRetreatsSubscriptionPeriod.camps_and_retreats_period_services.through
    for row in source['subscription_periods'].values():
        insert(CampsAndRetreatsSubscriptionPeriod, CampsAndRetreatsSubscriptionPeriod(
            camps_and_retreats_subscription_period_id=row['id'], camps_and_retreats_period_id=plan[row['period']]['id'],
            camps_and_retreats_subscription_id=subscriptions[row['cs']]['id'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None))
        link.objects.bulk_create([link(campsandretreatssubscriptionperiod_id=row['id'], campsandretreatsperiodsservice_id=services[s]['id'])
                                  for s in row['services']])


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=tuple((lambda label=label: models()[label])
                                                                                       for label in (SUBPERIOD, CSUB, PAYMENT)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'], fixture['default_category_name'] = category, name
        yield fixture


def deleted_rows(source):
    """auditlog's delete diffs of every row of the changed models, captured before each case."""
    return {str(row.pk): replace.deleted_changes(model, row) for model in models().values() for row in model._base_manager.all()}


def rows_of(state, label):
    return {key: periods.plain(row) for key, row in keyed(state[label], KEYS[label]).items()}


class Keys:
    """Authored key specs: literal ids, the rows this case created ('$sub', '$payment', '$sp:<period>') and rows
    created by earlier cases ('@sp:<camp subscription>:<period>', '@pay:<camp subscription>:<period>')."""

    def __init__(self, before_subperiods):
        self.before = before_subperiods
        self.created = {}

    def __call__(self, spec):
        if spec is None:
            return None
        if spec.startswith('$'):
            contract.require(spec[1:] in self.created, 'state.created-key')
            return self.created[spec[1:]]
        if spec.startswith('@'):
            kind, cs, period = spec[1:].split(':')
            matches = [key for key, row in self.before.items()
                       if row['camps_and_retreats_subscription_id'] == cs and row['camps_and_retreats_period_id'] == period]
            contract.require(len(matches) == 1, 'state.earlier-row')
            return matches[0] if kind == 'sp' else self.before[matches[0]]['payment_id']
        return spec


def bound(value, fixture):
    return {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name']}.get(value, value) \
        if isinstance(value, str) else value


def check_simple(label, before, after, authored, deleted, resolve, fixture, name):
    """One created row at most (authored columns) and the deleted keys; every other row unchanged."""
    old, new = rows_of(before, label), rows_of(after, label)
    created = set(new) - set(old)
    contract.require(len(created) == (1 if authored else 0), 'state.created-count:' + label)
    if authored:
        key = created.pop()
        resolve.created[name] = key
        for column, value in authored.items():
            if value == '$now':
                contract.require(isinstance(new[key][column], str) and new[key][column].endswith('Z'), 'state.created-now:' + label)
            else:
                contract.require(new[key][column] == bound(value, fixture), 'state.created-row:' + label)
    gone = {resolve(spec) for spec in deleted}
    contract.require(gone <= set(old), 'state.deleted-known:' + label)
    for key, row in old.items():
        if key in gone:
            contract.require(key not in new, 'state.deleted:' + label)
        elif label != SUBPERIOD:
            contract.require(new.get(key) == row, 'state.unchanged:' + label)
    return old, new, gone


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture = context['fixture']
    resolve = Keys(rows_of(before, SUBPERIOD))
    check_simple(CSUB, before, after, effect['subscription']['created'], effect['subscription']['deleted'], resolve, fixture, 'sub')
    _, _, gone_payments = check_simple(PAYMENT, before, after, effect['payments']['created'], effect['payments']['deleted'], resolve,
                                       fixture, 'payment')
    # Subscription periods: deleted, created by period, the rest unchanged but for SET_NULL on deleted payments.
    old, new = rows_of(before, SUBPERIOD), rows_of(after, SUBPERIOD)
    gone = {resolve(spec) for spec in effect['subperiods']['deleted']}
    contract.require(gone <= set(old), 'state.deleted-known:subperiods')
    created = {new[key]['camps_and_retreats_period_id']: key for key in set(new) - set(old)}
    authored = effect['subperiods']['created']
    contract.require(len(created) == len(set(new) - set(old)) == len(authored), 'state.created-count:subperiods')
    for spec in authored:
        contract.require(spec['period'] in created, 'state.created-period:subperiods')
        resolve.created['sp:' + spec['period']] = created[spec['period']]
    for spec in authored:
        row = new[created[spec['period']]]
        contract.require(row['camps_and_retreats_subscription_id'] == resolve(spec['subscription'])
                         and row['payment_id'] == resolve(spec['payment']), 'state.created-row:subperiods')
    for key, row in old.items():
        if key in gone:
            contract.require(key not in new, 'state.deleted:subperiods')
        else:
            expected = {**row, 'payment_id': None} if row['payment_id'] in gone_payments else row
            contract.require(new.get(key) == expected, 'state.unchanged:subperiods')
    # Link rows: those of deleted periods go, the created periods' services are added.
    left, right = periods.LINK_COLUMNS[LINK]
    pairs_before = harness.pairs(before[LINK], left, right)
    added = {(created[spec['period']], service) for spec in authored for service in spec['services']}
    expected = {pair for pair in pairs_before if pair[0] not in gone} | added
    contract.require(harness.pairs(after[LINK], left, right) == expected and len(after[LINK]) == len(expected), 'state.links')
    kept = {(str(row[left]), str(row[right])): row for row in before[LINK]}
    contract.require(all(row == kept[(str(row[left]), str(row[right]))] for row in after[LINK]
                         if (str(row[left]), str(row[right])) in kept), 'state.links-kept')
    # The response's bound keys are the created rows.
    for name, value in contract.BINDINGS.items():
        contract.require(resolve.created.get(name) == value, 'response.binding:' + name)
    # Audit entries and their index rows.
    stamps = {}
    for label, column in ((CSUB, 'created_at'), (PAYMENT, 'creation_date')):
        rows = rows_of(after, label)
        for key in resolve.created.values():
            if key in rows:
                stamps[key] = auditlog_moment(periods.when(rows[key][column]))
    events, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(events) == len(effect['audit']) and len(indexes) <= len(events), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    remaining = sorted(events, key=lambda event: event['id'])
    for spec in effect['audit']:
        key = resolve(spec['key'])
        matches = [event for event in remaining if event['object_pk'] == key and event['action'] == spec['action']
                   and event['content_type_id'] == types[spec['model'].lower()]]
        contract.require(matches, 'audit.missing')
        event = matches[0]
        remaining.remove(event)
        if spec['changes'] == '$deleted':
            changes = context['prepared'][key]
        else:
            def value(item):
                if item == '$created':
                    return stamps[key]
                if isinstance(item, str) and item[:1] in '$@' and item not in ('$default_category', '$default_category_name'):
                    return resolve(item)
                return bound(item, fixture)
            changes = {field: [value(item) for item in pair] for field, pair in spec['changes'].items()}
        periods.check_event(case, event, indexes, before=before, ids=source['ids'], spec=spec, key=key, changes=changes, context=context)
    contract.require(len(indexes) == sum(1 for spec in effect['audit'] if spec['association'] is not None), 'index.count')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
