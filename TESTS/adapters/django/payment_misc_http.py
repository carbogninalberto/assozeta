"""Parent-only payment simulation, partial quotes apply, sign and invoiced approve HTTP runner; imports are inert.

Seeds a second association owner (with a token), the subscription fee plans
of the own, foreign and second associations, a payment category, the own
default bank account, two documents, a supplier, associates, invoices (one
with a document and an attached document), the authored payments and, for
every authored subscription, its previous-plan payment and the subscription
(audit disabled). Subscription creation dates are set at seed time inside or
before the current balance year (1 January, the owners' default start).
Each case is checked against the complete model state: only payments and
audit rows change; created payments match the authored columns, changed
payments only the authored columns, every other payment is unchanged, and
the new auditlog entries match the authored ones in order with their index
rows. A '$now' column must be recent and, when rendered, equal to the stored
value. Payments the run creates are removed before the fixture cleanup.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress

from TESTS.contracts import test_payment_misc_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.course_subscriptions_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_misc_http.json', 'TESTS/contracts/test_payment_misc_http.py',
           'TESTS/adapters/django/payment_misc_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/payment_views.py', 'BE/application/utils/payments_utils.py', 'BE/application/tasks.py',
           'BE/application/utils/api_utils.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/subscriptions_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/payment_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py',
           'BE/application/mixin.py', 'BE/application/impersonation.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py', 'BE/docmanager/models.py') + harness.SOURCES
PAYMENT = 'application.Payment'
MANAGED = (PAYMENT, harness.AUDIT, harness.INDEX)
TIMES = ('creation_date', 'payment_date')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def created_at(label):
    """Inside the current balance year (from 1 January) or in the previous one."""
    year = datetime.now(timezone.utc).year
    return datetime(year, 1, 1, 0, 1, tzinfo=timezone.utc) if label == 'in_year' else datetime(year - 1, 6, 1, tzinfo=timezone.utc)


def seed_rows(source, created):
    from application.models import (Associate, Invoice, Payment, PaymentCategory, SportAssociation, Subscription, SupplierAndCustomers,
                                    User)
    from application.models.balance_sheet_models import CustomAccounts
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    second = source['plans_owner']
    insert(User, User(user_id=second['id'], username=second['username'], email=second['email'], password='!synthetic', role=User.ASSOCIATION,
                      first_name=second['first'], last_name=second['last']))
    insert(SportAssociation, SportAssociation(sport_association_id=second['association'], user_id=second['id'],
                                              denomination=second['denomination'], tax_code='79900000001'))
    for association, plans in source['plans'].items():
        SportAssociation._base_manager.filter(pk=association).update(subscription_fee_plans=plans)
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category']['id'], name=source['category']['name'],
                                            sport_association_id=own, creation_date=moment('2026-01-01T00:00:00Z')))
    bank = source['bank']
    insert(CustomAccounts, CustomAccounts(custom_account_id=bank['id'], name=bank['name'], initial_balance=Decimal('0.00'),
                                          account_type=CustomAccounts.BANK, account_code=bank['code'], sport_association_id=own,
                                          editable=False, enabled=True))
    for key in ('document', 'attached'):
        doc = source[key]
        insert(Document, Document(document_id=doc['id'], token=doc['token'], filename=doc['filename'], filepath=doc['filepath'],
                                  file_size_bytes=doc['size'], creation_date=moment(doc['created'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'],
                                    user_id=owner if row['association'] == own else ids['foreign_owner'], first_name=row['first'],
                                    last_name=row['last'], sex='M', born_date=date(1990, 5, 10), is_minor=False,
                                    group_id=ids[row['group']] if row['group'] else None))
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=own, creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership_fee']), activity_fee=Decimal(row['activity_fee']),
                                meta_payment_categories=row['meta_payment_categories'], description=row['description'], number=row['number'],
                                document_pdf_id=source['document']['id'] if row['document'] else None))
        if row['attached']:
            Invoice.attached_documents.through.objects.bulk_create([Invoice.attached_documents.through(
                invoice_id=row['id'], document_id=source['attached']['id'])])
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in TIMES:
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))
    for row in source['subscriptions'].values():
        if row['payment']:
            insert(Payment, Payment(payment_id=row['payment'], subject=1, amount=Decimal('10.00'), meta=row['payment_meta'], user_id=row['user'],
                                    associate_id=row['associate'], sport_association_id=row['association'], type='default',
                                    description='Quota precedente', creation_date=moment('2026-01-01T00:00:00Z')))
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                          associate_id=row['associate'], payment_id=row['payment'], creation_date=created_at(row['created']),
                                          deleted=row['deleted'], archived=row['archived'], meta=row['meta'],
                                          group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED))


def payments():
    from application.models import Payment
    return Payment


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(payments,))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from django.conf import settings
    from rest_framework_simplejwt.tokens import AccessToken
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'], fixture['default_category_name'] = category, name
        fixture['sessions'] = {**fixture['sessions'], '$whitelabel': settings.WHITELABEL_NAME}
        token = AccessToken()
        token['user_id'] = contract.load_source()['plans_owner']['id']
        fixture['tokens'] = {**fixture['tokens'], 'plans_owner': str(token)}
        yield fixture


def rows_of(state):
    return {key: plain(row) for key, row in keyed(state[PAYMENT], 'payment_id').items()}


def recent(text):
    stamp = moment(text) if isinstance(text, str) else None
    return stamp is not None and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10)


def check_row(stored, authored, fixture, where):
    bindings = {'$default_category': fixture['default_category']}
    for column, value in authored.items():
        actual = stored.get(column)
        if value == '$now':
            contract.require(recent(actual), 'state.now:' + column + where)
        else:
            contract.require(actual == bindings.get(value, value) if isinstance(value, str) else actual == value,
                             'state.created-column:' + column + where)
    contract.require(set(stored) - {'payment_id'} == set(authored), 'state.created-columns' + where)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], {**source['ids'], 'plans_owner': source['plans_owner']['id']}
    old, new = rows_of(before), rows_of(after)
    fresh = sorted(set(new) - set(old))
    authored = effect['created']
    contract.require(len(fresh) == len(authored) and set(old) <= set(new), 'state.created-count')
    for key, spec in zip(fresh, authored):
        check_row(new[key], spec, fixture, ':' + key)
    labels = {row['id']: label for label, row in source['payments'].items()}
    bindings = {'$default_category': fixture['default_category']}
    for key, row in old.items():
        changes = effect['changed'].get(labels.get(key), {})
        expected = dict(row)
        for column, value in changes.items():
            if value == '$now':
                contract.require(recent(new[key].get(column)), 'state.now:' + column)
                if contract.NOW:
                    contract.require(new[key].get(column) in contract.NOW, 'response.now-stored')
                value = new[key].get(column)
            expected[column] = bindings.get(value, value) if isinstance(value, str) else value
        contract.require(new[key] == expected, 'state.payment:' + labels.get(key, key))
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: int(row['id']))
    contract.require(len(events) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for event, spec in zip(events, effect['audit']):
        key = fresh[0] if spec['key'] == '$id' else source['payments'][spec['key']]['id']
        stored = new.get(key, {})

        def value(item, column):
            if item == '$id':
                return key
            if item == '$now':
                return auditlog_moment(moment(stored[column]))
            return bindings.get(item, item)
        changes = {column: [value(item, column) for item in pair] for column, pair in spec['changes'].items()}
        where = f":{spec['key']}:{spec['action']}"
        contract.require(event['content_type_id'] == types['payment'] and event['action'] == spec['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'Payment object ({key})', 'audit.identity' + where)
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


run_workload = harness.run_workload_for(contract, assert_effect)
