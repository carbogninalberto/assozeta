"""
Tests for Balance Sheet views - balance sheet management endpoints.

This module tests balance sheet-related API endpoints for balance sheet
operations, custom accounts, and account transfers.

Self-host note: plan-restriction tests (base-plan forbidden etc.) are
omitted — every self-host instance has Pro entitlement.
"""
import uuid
import datetime
from decimal import Decimal

import pytz
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User, BillingPlan, BillingSubscription, BalanceSheet
from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_billing_subscription
)


def create_test_balance_sheet(sport_association, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'status_flag': BalanceSheet.DRAFT,
        'year': timezone.now().year,
        'data': {
            'bank': 0,
            'cash': 0,
            'other': 0,
            'total': 0,
        },
        'archived': False,
    }
    defaults.update(kwargs)
    return BalanceSheet.objects.create(**defaults)


def create_test_custom_account(sport_association, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'name': f'Test Account {uuid.uuid4().hex[:8]}',
        'initial_balance': Decimal('1000.00'),
        'account_type': CustomAccounts.BANK,
        'account_code': f'ACC{uuid.uuid4().hex[:6].upper()}',
        'enabled': True,
        'editable': True,
    }
    defaults.update(kwargs)
    return CustomAccounts.objects.create(**defaults)


def create_test_account_transfer(sport_association, from_account, to_account, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'custom_account_from': from_account,
        'custom_account_to': to_account,
        'amount': Decimal('100.00'),
        'date': timezone.now(),
    }
    defaults.update(kwargs)
    return CustomAccountsTransfer.objects.create(**defaults)


class BalanceSheetTests(TestCase):
    """Tests for balance_sheet endpoint: GET/POST/DELETE /balance-sheet"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_get_balance_sheet_success(self):
        response = self.client.get('/balance-sheet')
        self.assertIn(response.status_code, [status.HTTP_200_OK])
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('balance_sheet', data['data'])
        self.assertIn('available_years', data['data'])

    def test_get_balance_sheet_with_current_date(self):
        current_date = timezone.now().strftime('%Y-%m-%d')
        response = self.client.get(f'/balance-sheet?currentDate={current_date}')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_get_balance_sheet_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/balance-sheet')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_balance_sheet_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/balance-sheet')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_approved_balance_sheet(self):
        balance_sheet = create_test_balance_sheet(
            self.sport_association,
            status_flag=BalanceSheet.APPROVED
        )
        response = self.client.get('/balance-sheet')
        self.assertIn(response.status_code, [status.HTTP_200_OK])
        data = response.json()
        self.assertIn('data', data)
        self.assertFalse(data['data']['balance_sheet']['draft'])

    def test_post_balance_sheet_update(self):
        balance_sheet = create_test_balance_sheet(self.sport_association)
        response = self.client.post('/balance-sheet', {
            'balance_sheet': {
                'draft': True,
                'year': timezone.now().year,
                'data': {'bank': 100, 'cash': 50, 'other': 25, 'total': 175}
            }
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_post_balance_sheet_invalid_data(self):
        response = self.client.post('/balance-sheet', {
            'invalid_key': 'invalid_value'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_balance_sheet_no_existing(self):
        response = self.client.post('/balance-sheet', {
            'balance_sheet': {
                'draft': True,
                'year': timezone.now().year,
            }
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_balance_sheet(self):
        balance_sheet = create_test_balance_sheet(self.sport_association)
        response = self.client.delete('/balance-sheet')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_delete_balance_sheet_not_found(self):
        BalanceSheet.objects.filter(sport_association=self.sport_association).delete()
        response = self.client.delete('/balance-sheet')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_balance_sheet_excel(self):
        response = self.client.post('/balance-sheet', {
            'format': 'excel'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_balance_sheet_pdf(self):
        response = self.client.post('/balance-sheet', {
            'format': 'pdf'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class BalanceSheetArchivedTests(TestCase):
    """Tests for balance_sheet_archived endpoint: GET /balance-sheet/archived"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_get_archived_balance_sheets(self):
        create_test_balance_sheet(self.sport_association, year=2023)
        create_test_balance_sheet(self.sport_association, year=2024)
        response = self.client.get('/balance-sheet/archived')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('balance_sheets', data)
        self.assertIn('sport_association', data)

    def test_get_archived_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/balance-sheet/archived')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_archived_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/balance-sheet/archived')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class BalanceSheetAccountsListTests(TestCase):
    """Tests for balance_sheet_accounts_list endpoint: GET /balance-sheet/accounts/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_get_accounts_list(self):
        create_test_custom_account(self.sport_association, account_type=CustomAccounts.BANK)
        create_test_custom_account(self.sport_association, account_type=CustomAccounts.CASH)
        response = self.client.get('/balance-sheet/accounts/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertGreaterEqual(len(data['data']), 2)

    def test_get_accounts_list_with_related_false(self):
        create_test_custom_account(self.sport_association)
        response = self.client.get('/balance-sheet/accounts/list?related=false')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_accounts_list_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/balance-sheet/accounts/list')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_accounts_list_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/balance-sheet/accounts/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class BalanceSheetAccountsAddTests(TestCase):
    """Tests for balance_sheet_accounts_add endpoint: POST /balance-sheet/accounts/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_add_account_success(self):
        response = self.client.post('/balance-sheet/accounts/add', {
            'name': 'Test Bank Account',
            'initial_balance': '1000.00',
            'account_type': CustomAccounts.BANK,
            'account_code': 'BANK001',
            'enabled': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['name'], 'Test Bank Account')

    def test_add_account_cash_type(self):
        response = self.client.post('/balance-sheet/accounts/add', {
            'name': 'Cash Register',
            'initial_balance': '500.00',
            'account_type': CustomAccounts.CASH,
            'account_code': 'CASH001',
            'enabled': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['account_type'], CustomAccounts.CASH)

    def test_add_account_other_type(self):
        response = self.client.post('/balance-sheet/accounts/add', {
            'name': 'Other Account',
            'initial_balance': '250.00',
            'account_type': CustomAccounts.OTHER,
            'account_code': 'OTHER001',
            'enabled': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['account_type'], CustomAccounts.OTHER)

    def test_add_account_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.post('/balance-sheet/accounts/add', {
            'name': 'Test Account',
            'initial_balance': '1000.00',
            'account_type': CustomAccounts.BANK,
            'account_code': 'BANK002',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsUpdateTests(TestCase):
    """Tests for balance_sheet_accounts_update endpoint: PATCH /balance-sheet/accounts/<uid>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)
        self.account = create_test_custom_account(self.sport_association)

    def test_update_account_success(self):
        response = self.client.patch(
            f'/balance-sheet/accounts/{self.account.custom_account_id}/update',
            {'name': 'Updated Account Name'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['name'], 'Updated Account Name')

    def test_update_account_initial_balance(self):
        response = self.client.patch(
            f'/balance-sheet/accounts/{self.account.custom_account_id}/update',
            {'initial_balance': '2000.00'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_account_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.patch(
            f'/balance-sheet/accounts/{self.account.custom_account_id}/update',
            {'name': 'Unauthorized Update'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsDeleteTests(TestCase):
    """Tests for balance_sheet_accounts_delete endpoint: DELETE /balance-sheet/accounts/<uid>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_delete_account_success(self):
        account = create_test_custom_account(self.sport_association, editable=True)
        response = self.client.delete(
            f'/balance-sheet/accounts/{account.custom_account_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_non_editable_account_forbidden(self):
        account = create_test_custom_account(self.sport_association, editable=False)
        response = self.client.delete(
            f'/balance-sheet/accounts/{account.custom_account_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_account_non_association_forbidden(self):
        account = create_test_custom_account(self.sport_association)
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.delete(
            f'/balance-sheet/accounts/{account.custom_account_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsTransferListTests(TestCase):
    """Tests for balance_sheet_accounts_transfer_list endpoint: GET /balance-sheet/accounts-transfer/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_get_transfers_list(self):
        account1 = create_test_custom_account(self.sport_association, name='Account 1')
        account2 = create_test_custom_account(self.sport_association, name='Account 2')
        create_test_account_transfer(self.sport_association, account1, account2)
        response = self.client.get('/balance-sheet/accounts-transfer/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_transfers_list_empty(self):
        response = self.client.get('/balance-sheet/accounts-transfer/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)

    def test_get_transfers_list_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.get('/balance-sheet/accounts-transfer/list')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsTransferAddTests(TestCase):
    """Tests for balance_sheet_accounts_transfer_add endpoint: POST /balance-sheet/accounts-transfer/add"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)
        self.account1 = create_test_custom_account(self.sport_association, name='Account 1')
        self.account2 = create_test_custom_account(self.sport_association, name='Account 2')

    def test_add_transfer_success(self):
        response = self.client.post('/balance-sheet/accounts-transfer/add', {
            'custom_account_from': str(self.account1.custom_account_id),
            'custom_account_to': str(self.account2.custom_account_id),
            'amount': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(Decimal(data['amount']), Decimal('150.00'))

    def test_add_transfer_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.post('/balance-sheet/accounts-transfer/add', {
            'custom_account_from': str(self.account1.custom_account_id),
            'custom_account_to': str(self.account2.custom_account_id),
            'amount': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsTransferUpdateTests(TestCase):
    """Tests for balance_sheet_accounts_transfer_update endpoint: PATCH /balance-sheet/accounts-transfer/<uid>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)
        self.account1 = create_test_custom_account(self.sport_association, name='Account 1')
        self.account2 = create_test_custom_account(self.sport_association, name='Account 2')
        self.transfer = create_test_account_transfer(
            self.sport_association, self.account1, self.account2
        )

    def test_update_transfer_success(self):
        response = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.transfer.custom_account_transfer_id}/update',
            {'amount': '200.00'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_transfer_non_association_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.patch(
            f'/balance-sheet/accounts-transfer/{self.transfer.custom_account_transfer_id}/update',
            {'amount': '200.00'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetAccountsTransferDeleteTests(TestCase):
    """Tests for balance_sheet_accounts_transfer_delete endpoint: DELETE /balance-sheet/accounts-transfer/<uid>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_delete_transfer_success(self):
        account1 = create_test_custom_account(self.sport_association, name='Account 1')
        account2 = create_test_custom_account(self.sport_association, name='Account 2')
        transfer = create_test_account_transfer(self.sport_association, account1, account2)
        response = self.client.delete(
            f'/balance-sheet/accounts-transfer/{transfer.custom_account_transfer_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_transfer_non_association_forbidden(self):
        account1 = create_test_custom_account(self.sport_association, name='Account 1')
        account2 = create_test_custom_account(self.sport_association, name='Account 2')
        transfer = create_test_account_transfer(self.sport_association, account1, account2)
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.delete(
            f'/balance-sheet/accounts-transfer/{transfer.custom_account_transfer_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BalanceSheetEdgeCaseTests(TestCase):
    """Tests for edge cases in balance sheet operations."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user, plan_type='pro')
        self.client.force_authenticate(user=self.user)

    def test_balance_sheet_with_custom_accounts(self):
        create_test_custom_account(
            self.sport_association,
            account_type=CustomAccounts.BANK,
            initial_balance=Decimal('1000.00')
        )
        create_test_custom_account(
            self.sport_association,
            account_type=CustomAccounts.CASH,
            initial_balance=Decimal('500.00')
        )
        create_test_custom_account(
            self.sport_association,
            account_type=CustomAccounts.OTHER,
            initial_balance=Decimal('250.00')
        )
        response = self.client.get('/balance-sheet')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_balance_sheet_with_transfers(self):
        account1 = create_test_custom_account(
            self.sport_association,
            account_type=CustomAccounts.BANK,
            initial_balance=Decimal('1000.00')
        )
        account2 = create_test_custom_account(
            self.sport_association,
            account_type=CustomAccounts.CASH,
            initial_balance=Decimal('500.00')
        )
        create_test_account_transfer(
            self.sport_association,
            account1,
            account2,
            amount=Decimal('200.00')
        )
        response = self.client.get('/balance-sheet')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_approve_balance_sheet(self):
        balance_sheet = create_test_balance_sheet(
            self.sport_association,
            status_flag=BalanceSheet.DRAFT
        )
        response = self.client.post('/balance-sheet', {
            'balance_sheet': {
                'draft': False,
                'year': timezone.now().year,
            }
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK])

    def test_multiple_years_balance_sheets(self):
        for year in [2022, 2023, 2024]:
            create_test_balance_sheet(self.sport_association, year=year)
        response = self.client.get('/balance-sheet/archived')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertGreaterEqual(len(data['balance_sheets']), 3)
