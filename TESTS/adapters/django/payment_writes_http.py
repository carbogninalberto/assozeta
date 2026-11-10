"""Parent-only payment add, update and bulk add HTTP runner; imports are inert.

Seeds payment categories (own, global, foreign), accounts (the own default
bank, an own cash account, a foreign bank), associates (own, grouped, deleted,
foreign), a subscription, an instructor, a supplier and the authored payments
(audit disabled). Each case is checked against the complete model state: only
payments and audit rows change; created payments match the authored columns
(bulk rows by associate), changed payments only the authored columns, every
other payment is unchanged, and the new auditlog entries match the authored
ones in order with their index rows. A '$now' column must be recent and, when
the response renders it, equal to the stored value; the response's bound
payment id is the created row. Payments the run creates are removed before
the fixture cleanup compares the model state.

A CURRENT_HOST print stub (invoice-sync-print's service_context) runs for the
whole run: each case's outbound requests must be exactly its authored
'prints' (the update of an invoiced payment prints the invoice).
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress

import types

from TESTS.contracts import test_payment_writes_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.workflow_writes_http import keyed
from TESTS.adapters.django import default_payment_category

SOURCES = ('TESTS/fixtures/payment_writes_http.json', 'TESTS/contracts/test_payment_writes_http.py',
           'TESTS/adapters/django/payment_writes_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/payment_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/tenant_scope.py',
           'BE/application/permissions_registry.py', 'BE/application/utils/api_utils.py', 'BE/docmanager/models.py',
           'TESTS/adapters/django/invoice_sync_print_http.py', 'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py',
           'BE/application/serializers/invoice_serializers.py') + harness.SOURCES + default_payment_category.SOURCES
PAYMENT = 'application.Payment'
service_context = printer.service_context


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print stub scripted for it and its outbound requests checked after."""
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    printer.check_prints(case, None)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 BINDINGS=base_contract.BINDINGS,
                                 NOW=base_contract.NOW)
MANAGED = (PAYMENT, harness.AUDIT, harness.INDEX)
TIMES = ('creation_date', 'payment_date')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import Associate, Instructor, Payment, PaymentCategory, SupplierAndCustomers, Subscription
    from application.models.balance_sheet_models import CustomAccounts
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['categories'].values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], sport_association_id=row['association'],
                                                creation_date=moment('2026-01-01T00:00:00Z')))
    for row in source['accounts'].values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], name=row['name'], initial_balance=Decimal('0.00'),
                                              account_type=row['type'], account_code=row['code'], sport_association_id=row['association'],
                                              editable=row['editable'], enabled=True))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], user_id=owner, first_name=row['first'],
                                    last_name=row['last'], sex=row['sex'], tax_code=row['tax'], born_date=date.fromisoformat(row['born']),
                                    email=row['email'], is_minor=False, deleted=row['deleted'],
                                    group_id=ids[row['group']] if row['group'] else None))
    associates = source['associates']
    for row in source['subscriptions'].values():
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
                                          associate_id=associates[row['associate']]['id'], creation_date=moment(row['created']),
                                          status_flag=Subscription.ACCEPTED))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'], last_name=teacher['last_name'],
                                  email=teacher['email'], tax_code=teacher['tax_code'],
                                  default_hourly_billing=Decimal(teacher['default_hourly_billing'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    from application.models import Invoice
    bill = source['invoice']
    insert(Invoice, Invoice(invoice_id=bill['id'], sport_association_id=own, creation_date=moment(bill['created']),
                            membership_fee=Decimal(bill['membership_fee']), activity_fee=Decimal(bill['activity_fee']),
                            description=bill['description'], number=bill['number']))
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in TIMES:
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))


def payments():
    from application.models import Payment
    return Payment


base_fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(payments,))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        row = default_payment_category.default_category(base_contract.load_source()['ids']['own_association'])
        base_contract.require(row is not None, 'fixture.default-category')
        category, name = str(row.pk), row.name
        fixture['default_category'], fixture['default_category_name'] = category, name
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        yield fixture


def rows_of(state):
    return {key: plain(row) for key, row in keyed(state[PAYMENT], 'payment_id').items()}


def recent(text):
    stamp = moment(text) if isinstance(text, str) else None
    return stamp is not None and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10)


def check_row(stored, authored, fixture, where):
    """A created row against its authored columns ('$now' recent and, when rendered, the response's value)."""
    bindings = {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name']}
    for column, value in authored.items():
        actual = stored.get(column)
        if value == '$now':
            contract.require(recent(actual), 'state.now:' + column + where)
            if contract.NOW and column == 'creation_date':
                contract.require(actual in contract.NOW, 'response.now-stored' + where)
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
    fixture, ids = context['fixture'], source['ids']
    old, new = rows_of(before), rows_of(after)
    fresh = sorted(set(new) - set(old))
    authored = effect['created']
    contract.require(len(fresh) == len(authored) and set(old) <= set(new), 'state.created-count')
    created = {}
    if len(authored) == 1:
        created[fresh[0]] = authored[0]
    else:
        by_associate = {new[key]['associate_id']: key for key in fresh}
        for spec in authored:
            contract.require(spec['associate_id'] in by_associate, 'state.created-associate')
            created[by_associate[spec['associate_id']]] = spec
    for key, spec in created.items():
        check_row(new[key], spec, fixture, ':' + key)
    for name, value in contract.BINDINGS.items():
        contract.require(len(fresh) == 1 and value == fresh[0], 'response.binding:' + name)
    labels = {row['id']: label for label, row in source['payments'].items()}
    bindings = {'$default_category': fixture['default_category'], '$default_category_name': fixture['default_category_name']}
    for key, row in old.items():
        changes = effect['changed'].get(labels.get(key), {})
        expected = {**row, **{column: bindings.get(value, value) if isinstance(value, str) else value for column, value in changes.items()}}
        contract.require(new[key] == expected, 'state.payment:' + labels.get(key, key))
    # Audit entries and their index rows.
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
