"""Real bookkeeping handlers and fixture-owned ORM persistence.

The disposable-database guard alone is patched for this transaction-owned test
database. These tests never create browser evidence or contact external services.
"""
import json
import tempfile
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from freezegun import freeze_time
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Payment, SportAssociation, User
from application.models.balance_sheet_models import BalanceSheet, CustomAccounts, CustomAccountsTransfer
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import create_test_user, create_test_sport_association


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
@freeze_time('2026-09-30 12:00:00+00:00')
class ManualAccountingBalanceTests(BaseTestCase):
    def seed(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            return json.loads(output.read_text())

    def setUp(self):
        super().setUp()
        cache.clear()
        self.fixture = self.seed()
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'))
        self.cash = CustomAccounts.objects.get(pk=fixture_id('cash-account'))
        self.owner_client = self.client_for(self.owner)
        self.reader_client = self.client_for(self.reader)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def assert_response(self, response, expected=200):
        self.assertEqual(response.status_code, expected, response.content)
        return response.data

    def accounts(self, client=None):
        return self.assert_response((client or self.owner_client).get('/balance-sheet/accounts/list'))['data']

    def balance(self, client=None):
        return self.assert_response((client or self.owner_client).get('/balance-sheet',
            {'currentDate': '2026-09-30'}))['data']

    def save_balance(self, data, draft=True):
        return self.assert_response(self.owner_client.post('/balance-sheet', {
            'balance_sheet': {**deepcopy(data), 'year': 2026, 'draft': draft}}, format='json'))['data']

    def test_account_create_update_transfer_delete_preserve_payments_and_total_liquidity(self):
        original_payments = list(Payment._base_manager.values())
        baseline = self.accounts()
        self.assertEqual(len(baseline), 1)
        self.assertEqual(Decimal(str(baseline[0]['current_balance'])), Decimal('50'))
        self.assertFalse(baseline[0]['deletable'])
        bank = self.assert_response(self.owner_client.post('/balance-sheet/accounts/add', {
            'name': 'Banca Aurora', 'account_type': 2, 'initial_balance': '100.00',
            'account_code': 'BANCA-AURORA'}, format='json'))
        identifier = bank['custom_account_id']
        self.assert_response(self.owner_client.patch(f'/balance-sheet/accounts/{identifier}/update',
            {'initial_balance': '125.00'}, format='json'))
        persisted = CustomAccounts.objects.get(pk=identifier)
        self.assertEqual(persisted.initial_balance, Decimal('125'))
        self.assertTrue(persisted.enabled)
        transfer = self.assert_response(self.owner_client.post('/balance-sheet/accounts-transfer/add', {
            'custom_account_from': str(self.cash.pk), 'custom_account_to': str(identifier),
            'amount': '20.00', 'date': '2026-09-30'}, format='json'))
        self.assertTrue(CustomAccountsTransfer.objects.filter(pk=transfer['custom_account_transfer_id']).exists())
        rows = {str(row['custom_account_id']): row for row in self.accounts(self.client_for(self.owner))}
        self.assertEqual(Decimal(str(rows[str(self.cash.pk)]['current_balance'])), Decimal('30'))
        self.assertEqual(Decimal(str(rows[str(identifier)]['current_balance'])), Decimal('145'))
        self.assertFalse(rows[str(identifier)]['deletable'])
        self.assertEqual(self.balance()['balance_sheet']['data']['total'], 175)
        self.assert_response(self.owner_client.delete('/balance-sheet/accounts-transfer/' +
            str(transfer['custom_account_transfer_id']) + '/delete'))
        rows = {str(row['custom_account_id']): row for row in self.accounts()}
        self.assertEqual(Decimal(str(rows[str(self.cash.pk)]['current_balance'])), Decimal('50'))
        self.assertEqual(Decimal(str(rows[str(identifier)]['current_balance'])), Decimal('125'))
        self.assertTrue(rows[str(identifier)]['deletable'])
        self.assert_response(self.owner_client.delete(f'/balance-sheet/accounts/{identifier}/delete'))
        self.assertFalse(CustomAccounts.objects.filter(pk=identifier).exists())
        self.assert_response(self.owner_client.delete(f'/balance-sheet/accounts/{self.cash.pk}/delete'), 403)
        self.assertEqual(len(self.accounts()), 1)
        self.assertEqual(list(Payment._base_manager.values()), original_payments)

    def test_manual_nonzero_row_save_publish_unpublish_persists_without_creating_payments(self):
        original_payments = list(Payment._base_manager.values())
        initial = self.balance()
        self.assertEqual(len(initial['available_years']), 6)
        data = initial['balance_sheet']['data']
        self.assertEqual(sum(row['institutional'] for row in data['incoming']['generalIncome']), 50)
        row = {'id': 'manual-test-row', 'description': 'Contributo dimostrativo direttivo',
               'institutional': 15, 'commercial': 5, 'editable': False}
        data['incoming']['generalIncome'].append(row)
        self.save_balance(data)
        stored = BalanceSheet.objects.get(sport_association=self.association, year=2026)
        self.assertEqual(stored.status_flag, BalanceSheet.DRAFT)
        self.assertIn(row, stored.data['incoming']['generalIncome'])
        refreshed = self.balance(self.client_for(self.owner))['balance_sheet']['data']
        self.assertIn(row, refreshed['incoming']['generalIncome'])
        self.assertEqual(sum(item['institutional'] + item['commercial'] for item in refreshed['incoming']['generalIncome']), 70)
        self.assertEqual(refreshed['total'], 50)
        self.save_balance(refreshed, draft=False)
        stored.refresh_from_db()
        self.assertEqual(stored.status_flag, BalanceSheet.APPROVED)
        published = self.balance()['balance_sheet']
        self.assertFalse(published['draft'])
        self.assertIn(row, published['data']['incoming']['generalIncome'])
        self.save_balance(published['data'], draft=True)
        stored.refresh_from_db()
        self.assertEqual(stored.status_flag, BalanceSheet.DRAFT)
        self.assertTrue(self.balance()['balance_sheet']['draft'])
        self.assertIn(row, self.balance()['balance_sheet']['data']['incoming']['generalIncome'])
        self.assertEqual(list(Payment._base_manager.values()), original_payments)

    def test_payment_filters_and_missing_payment_date_explain_different_liquidity_total(self):
        first = Payment.objects.get(pk=fixture_id('payment-1'))
        first.payment_date = None
        first.save(update_fields=['payment_date'])
        data = self.balance()['balance_sheet']['data']
        # The income helper falls back to creation_date; dated liquidity does not.
        self.assertEqual(sum(row['institutional'] for row in data['incoming']['generalIncome']), 50)
        self.assertEqual(data['cash'], 25)
        self.assertEqual(Decimal(str(self.accounts()[0]['current_balance'])), Decimal('50'))
        first.archived = True
        first.save(update_fields=['archived'])
        # Use a fresh draft so obsolete category rows cannot mask the selection.
        BalanceSheet.objects.filter(sport_association=self.association).delete()
        data = self.balance()['balance_sheet']['data']
        self.assertEqual(sum(row['institutional'] for row in data['incoming']['generalIncome']), 25)
        self.assertEqual(Decimal(str(self.accounts()[0]['current_balance'])), Decimal('50'))

    def test_baseline_reader_denied_and_read_only_permission_cannot_publish_or_write_accounts(self):
        original_accounts = list(CustomAccounts.objects.values())
        original_payments = list(Payment._base_manager.values())
        draft = self.balance()['balance_sheet']['data']
        for route in ['/balance-sheet', '/balance-sheet/accounts/list', '/balance-sheet/accounts-transfer/list']:
            self.assert_response(self.reader_client.get(route), 403)
        self.assert_response(self.reader_client.post('/balance-sheet', {
            'balance_sheet': {**draft, 'draft': False, 'year': 2026}}, format='json'), 403)
        self.reader.collaborator_permissions = ['bookeeping.management.balancesheet.read',
            'bookeeping.management.accounts.read', 'bookeeping.management.accountstransfers.read']
        self.reader.save(update_fields=['collaborator_permissions'])
        self.assertTrue(self.balance(self.reader_client)['balance_sheet']['draft'])
        self.assertEqual(len(self.accounts(self.reader_client)), 1)
        self.assert_response(self.reader_client.post('/balance-sheet', {
            'balance_sheet': {**draft, 'draft': False, 'year': 2026}}, format='json'), 403)
        self.assert_response(self.reader_client.patch(f'/balance-sheet/accounts/{self.cash.pk}/update',
            {'initial_balance': '999.00'}, format='json'), 403)
        self.assert_response(self.reader_client.post('/balance-sheet/accounts-transfer/add', {
            'amount': '20.00', 'custom_account_from': str(self.cash.pk),
            'custom_account_to': str(self.cash.pk)}, format='json'), 403)
        self.assertEqual(list(CustomAccounts.objects.values()), original_accounts)
        self.assertEqual(list(Payment._base_manager.values()), original_payments)
        self.assertTrue(self.balance()['balance_sheet']['draft'])

    def test_reseed_cleans_only_fixture_accounts_transfers_and_balance_sheets(self):
        foreign_user = create_test_user(role=User.ASSOCIATION)
        foreign_association = create_test_sport_association(user=foreign_user)
        foreign_from = CustomAccounts.objects.create(sport_association=foreign_association,
            name='Cassa altra associazione', initial_balance='700.00', account_code='FOREIGN-CASH', account_type=1)
        foreign_to = CustomAccounts.objects.create(sport_association=foreign_association,
            name='Banca altra associazione', initial_balance='200.00', account_code='FOREIGN-BANK', account_type=2)
        foreign_transfer = CustomAccountsTransfer.objects.create(sport_association=foreign_association,
            custom_account_from=foreign_from, custom_account_to=foreign_to, amount='30.00')
        foreign_balance = BalanceSheet.objects.create(sport_association=foreign_association,
            year=2026, status_flag=BalanceSheet.APPROVED, data={'foreign': True})
        owned_bank = CustomAccounts.objects.create(sport_association=self.association,
            name='Banca da azzerare', initial_balance='125.00', account_code='MANUAL-BANK', account_type=2)
        owned_transfer = CustomAccountsTransfer.objects.create(sport_association=self.association,
            custom_account_from=self.cash, custom_account_to=owned_bank, amount='20.00')
        owned_balance = BalanceSheet.objects.create(sport_association=self.association,
            year=2026, status_flag=BalanceSheet.APPROVED, data={'manual_row': 20})
        self.cash.initial_balance = Decimal('999')
        self.cash.save(update_fields=['initial_balance'])
        original_foreign_accounts = list(CustomAccounts.objects.filter(sport_association=foreign_association).values())
        original_foreign_transfer = CustomAccountsTransfer.objects.values().get(pk=foreign_transfer.pk)
        original_foreign_balance = BalanceSheet.objects.values().get(pk=foreign_balance.pk)
        self.assertEqual(self.seed()['fixture_version'], 8)
        self.assertFalse(CustomAccounts.objects.filter(pk=owned_bank.pk).exists())
        self.assertFalse(CustomAccountsTransfer.objects.filter(pk=owned_transfer.pk).exists())
        self.assertFalse(BalanceSheet.objects.filter(pk=owned_balance.pk).exists())
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.initial_balance, Decimal('0'))
        self.assertEqual(CustomAccounts.objects.filter(sport_association=self.association).count(), 1)
        self.assertEqual(list(CustomAccounts.objects.filter(sport_association=foreign_association).values()), original_foreign_accounts)
        self.assertEqual(CustomAccountsTransfer.objects.values().get(pk=foreign_transfer.pk), original_foreign_transfer)
        self.assertEqual(BalanceSheet.objects.values().get(pk=foreign_balance.pk), original_foreign_balance)
