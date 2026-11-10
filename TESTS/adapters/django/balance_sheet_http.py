"""Parent-only balance-sheet and balance-sheet/accounts/<uid>/delete HTTP runner; imports are inert.

Seeds payment categories, custom accounts, transfers, payments and balance
sheets (audit disabled) and verifies each case against the complete model
state: a sheet case changes only the authored sheet (or creates one) with its
exact auditlog update/create entry indexed directly, or no entry when nothing
changed; the failed-year POST leaves the sheet unchanged with its entry; an
account delete removes the account and its transfers with one delete entry
each, indexed through changes_json. Created sheets are removed before the
fixture cleanup compares the model state.
"""
from datetime import datetime
from decimal import Decimal
import json

from TESTS.contracts import test_balance_sheet_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/balance_sheet_http.json', 'TESTS/contracts/test_balance_sheet_http.py',
           'TESTS/adapters/django/balance_sheet_http.py', 'TESTS/contracts/test_balance_archived_http.py',
           'TESTS/adapters/django/carnet_replace_http.py', 'TESTS/adapters/django/carnet_subscription_topup_http.py',
           'BE/application/views/balance_sheet_views.py', 'BE/application/utils/balance_sheet_utils.py',
           'BE/application/utils/printing.py', 'BE/application/utils/excel_utils.py', 'TESTS/contracts/xlsx.py',
           'BE/application/utils/api_utils.py', 'BE/application/serializers/balance_sheet.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/balance_sheet_models.py',
           'BE/application/models/payment_models.py', 'BE/application/mixin.py', 'BE/application/permissions.py',
           'BE/application/permissions_registry.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py') + harness.SOURCES
SHEET, ACCOUNT, TRANSFER = 'application.BalanceSheet', 'application.CustomAccounts', 'application.CustomAccountsTransfer'
MANAGED = (SHEET, ACCOUNT, TRANSFER, harness.AUDIT, harness.INDEX)
MANAGER = ['application.CustomAccountsTransfer.None', 'None']


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import Payment
    from application.models.payment_models import PaymentCategory
    from application.models.balance_sheet_models import BalanceSheet, CustomAccounts, CustomAccountsTransfer
    ids, accounts = source['ids'], source['accounts']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for row in source['categories'].values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], sport_association_id=row['association'],
                                                type=row['type']))
    for row in accounts.values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], sport_association_id=row['association'], name=row['name'],
                                              initial_balance=Decimal(row['initial']), account_type=row['type'], account_code=row['code'],
                                              editable=row['editable'], enabled=row['enabled']))
    account = lambda label: accounts[label]['id'] if label else None
    for row in source['transfers'].values():
        insert(CustomAccountsTransfer, CustomAccountsTransfer(custom_account_transfer_id=row['id'], custom_account_from_id=account(row['from']),
                                                              custom_account_to_id=account(row['to']), amount=Decimal(row['amount']),
                                                              date=moment(row['date']), sport_association_id=ids['own_association']))
    category = lambda label: source['categories'][label]['id'] if label else None
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], custom_accounts_id=account(row['account']),
                                payment_category_id=category(row['category']), amount=Decimal(row['amount']), paid=row['paid'],
                                expense=row['expense'], deleted=row['deleted'], archived=row['archived'],
                                group_id=ids[row['group']] if row['group'] else None, payment_date=moment(row['date']),
                                creation_date=moment(row['created']), meta_payment_categories=row['meta'], subject=Payment.OTHER))
    for row in source['sheets'].values():
        insert(BalanceSheet, BalanceSheet(balance_sheet_id=row['id'], sport_association_id=row['association'], status_flag=row['status'],
                                          year=row['year'], archived=row['archived'], data=row['data'], creation_date=moment(row['created'])))


def purged():
    from application.models.balance_sheet_models import BalanceSheet
    return BalanceSheet


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(purged,))


def prepare(source):
    """auditlog's delete changes of the seeded accounts and transfers still present."""
    from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
    rows = {str(r.pk): {**replace.deleted_changes(CustomAccounts, r), 'custom_account_from': MANAGER, 'custom_account_to': MANAGER}
            for r in CustomAccounts._base_manager.filter(pk__in=[a['id'] for a in source['accounts'].values()])}
    rows.update({str(r.pk): replace.deleted_changes(CustomAccountsTransfer, r)
                 for r in CustomAccountsTransfer._base_manager.filter(pk__in=[t['id'] for t in source['transfers'].values()])})
    return rows


def resolved(value):
    return contract.current_year() if value == '$current_year' else value


def sheet_fields(row):
    return {'data': row.data, 'year': row.year, 'status': row.status_flag, 'archived': row.archived}


def assert_effect(case, before, after, source, context):
    from application.models.balance_sheet_models import BalanceSheet
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect, ids = case['effect'], source['ids']
    sheets = context.setdefault('sheets', {label: row['id'] for label, row in source['sheets'].items()})
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    entries, indexes = harness.audit_delta(contract, before, after)
    common = dict(before=before, ids=ids, actor=effect['actor'], context=context)
    old_s, new_s = keyed(before[SHEET], 'balance_sheet_id'), keyed(after[SHEET], 'balance_sheet_id')
    if effect['kind'] == 'delete-account':
        contract.require(before[SHEET] == after[SHEET], 'state.sheets')
        removed = [source['accounts'][effect['account']]['id']] + [source['transfers'][label]['id'] for label in effect['transfers']]
        for model, key in ((ACCOUNT, 'custom_account_id'), (TRANSFER, 'custom_account_transfer_id')):
            old, new = keyed(before[model], key), keyed(after[model], key)
            contract.require(set(new) == set(old) - set(removed) and all(new[k] == old[k] for k in new), 'state.deleted:' + model)
        contract.require(len(entries) == len(removed) and len(indexes) == len(entries), 'audit.count')
        by_key = {event['object_pk']: event for event in entries}
        contract.require(set(by_key) == set(removed), 'audit.objects')
        for index, key in enumerate(removed):
            model = 'CustomAccounts' if index == 0 else 'CustomAccountsTransfer'
            harness.check_audit_event(contract, case, by_key[key], indexes, model=model, key=key, action=2, changes=context['prepared'][key],
                                      association=ids['own_association'], path='changes_json.sport_association', **common)
        return
    contract.require(all(before[k] == after[k] for k in (ACCOUNT, TRANSFER)), 'state.accounts')
    expected = effect.get('row')
    if effect['kind'] == 'create':
        keys = set(new_s) - set(old_s)
        contract.require(len(keys) == 1 and len(new_s) == len(old_s) + 1 and all(new_s[k] == old_s[k] for k in old_s), 'state.created')
        key = str(keys.pop())
        sheets[effect['sheet']] = key
        row = BalanceSheet._base_manager.get(pk=key)
        contract.require(str(row.sport_association_id) == ids[effect['association']], 'state.association')
        contract.require(sheet_fields(row) == {'data': expected['data'], 'year': resolved(expected['year']), 'status': 1, 'archived': False},
                         'state.sheet')
        changes = topup.created_changes(BalanceSheet, row, {'data': json.dumps(row.data, sort_keys=True),
                                                            'creation_date': topup.auditlog_moment(row.creation_date)})
        contract.require(len(entries) == 1 and len(indexes) == 1, 'audit.count')
        harness.check_audit_event(contract, case, entries[0], indexes, model='BalanceSheet', key=key, action=0, changes=changes,
                                  association=ids[effect['association']], path='direct', **common)
        return
    key = sheets[effect['sheet']]
    contract.require(set(new_s) == set(old_s) and all(new_s[k] == old_s[k] for k in old_s if str(k) != key), 'state.other-sheets')
    row = BalanceSheet._base_manager.get(pk=key)
    if effect['kind'] == 'audit-only':
        contract.require(before[SHEET] == after[SHEET], 'state.sheet-unchanged')
    else:
        old = [r for k, r in old_s.items() if str(k) == key][0]
        contract.require(sheet_fields(row) == {'data': expected['data'], 'year': resolved(expected['year']), 'status': expected['status'],
                                               'archived': old['archived']}, 'state.sheet')
        contract.require(str(old['sport_association_id']) == str(row.sport_association_id), 'state.association')
    changes = effect['changes']
    contract.require(len(entries) == (1 if changes else 0) and len(indexes) == len(entries), 'audit.count')
    if changes:
        harness.check_audit_event(contract, case, entries[0], indexes, model='BalanceSheet', key=key, action=1, changes=changes,
                                  association=str(row.sport_association_id), path='direct', **common)


PROFILES = {}


class _Contract:
    """The contract, also requiring the embedded association object to be identical across backends."""
    require = staticmethod(contract.require)
    load_source = staticmethod(contract.load_source)

    @staticmethod
    def assert_case(case, base_url, **kwargs):
        status = contract.assert_case(case, base_url, **kwargs)
        if case['name'] in contract.LAST_PROFILE:
            profile = contract.LAST_PROFILE.pop(case['name'])
            contract.require(PROFILES.setdefault(case['name'], profile) == profile, 'response.association-differs')
        return status


run_workload = harness.run_workload_for(_Contract, assert_effect, prepare=prepare)
