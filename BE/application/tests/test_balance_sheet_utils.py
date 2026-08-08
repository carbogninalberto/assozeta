"""
Tests for application.utils.balance_sheet_utils module.
"""
import datetime
import logging
from decimal import Decimal

import pytz
from django.test import TestCase

from application.models import BillingPlan, BillingSubscription
from application.models.payment_models import Payment, PaymentCategory
from application.models.user_models import SportAssociation, Associate, User
from application.utils.balance_sheet_utils import (
    find_category_index,
    reset_balance_sheet_to_zero,
    generate_balance_sheet
)
from application.tests.fixtures.factories import create_test_user, create_test_sport_association

logger = logging.getLogger(__name__)


class FindCategoryIndexTests(TestCase):
    """Tests for find_category_index function."""

    def test_find_category_index_found(self):
        category_list = [
            {'id': '123', 'name': 'Category 1'},
            {'id': '456', 'name': 'Category 2'},
            {'id': '789', 'name': 'Category 3'},
        ]
        result = find_category_index(category_list, '456')
        self.assertEqual(result, 1)

    def test_find_category_index_not_found(self):
        category_list = [
            {'id': '123', 'name': 'Category 1'},
            {'id': '456', 'name': 'Category 2'},
        ]
        result = find_category_index(category_list, '999')
        self.assertEqual(result, -1)

    def test_find_category_index_empty_list(self):
        category_list = []
        result = find_category_index(category_list, '123')
        self.assertEqual(result, -1)

    def test_find_category_index_missing_id_key(self):
        category_list = [
            {'name': 'Category 1'},
            {'id': '456', 'name': 'Category 2'},
        ]
        result = find_category_index(category_list, '456')
        self.assertEqual(result, 1)

    def test_find_category_index_first_item(self):
        category_list = [
            {'id': '123', 'name': 'Category 1'},
            {'id': '456', 'name': 'Category 2'},
        ]
        result = find_category_index(category_list, '123')
        self.assertEqual(result, 0)

    def test_find_category_index_last_item(self):
        category_list = [
            {'id': '123', 'name': 'Category 1'},
            {'id': '456', 'name': 'Category 2'},
            {'id': '789', 'name': 'Category 3'},
        ]
        result = find_category_index(category_list, '789')
        self.assertEqual(result, 2)

    def test_find_category_index_with_uuid(self):
        uuid_str = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
        category_list = [
            {'id': uuid_str, 'name': 'Category 1'},
        ]
        result = find_category_index(category_list, uuid_str)
        self.assertEqual(result, 0)


class BalanceSheetIntegrationTests(TestCase):
    """Integration tests for balance sheet generation with real DB."""

    def setUp(self):
        self.user = User.objects.create_user(
            first_name='Test',
            last_name='User',
            username='TEST.USER.BS',
            email='test.user.bs@example.com',
            password='testpassword',
            role=User.ASSOCIATION
        )

        self.sport_association = SportAssociation.objects.create(
            denomination='Test ASD Balance',
            tax_code='TXCODEBS123',
            subscription_fee='100.00',
            user=self.user
        )

        base_plan = BillingPlan.objects.filter(name__exact="Piano Base").first()
        if not base_plan:
            base_plan = BillingPlan.objects.create(
                name="Piano Base",
                description='Base plan',
                monthly_fee=0,
                annually_fee=0,
                billing_type=BillingPlan.BASE_PLAN,
            )
        BillingSubscription.objects.get_or_create(
            user=self.user,
            auto_renewal=True,
            renewal_type=BillingSubscription.ANNUALLY,
            ends_on=pytz.timezone('Europe/Rome').localize(
                datetime.datetime(2030, 12, 31), is_dst=None
            ),
            billing_plan=base_plan
        )

        self.athlete_user = User.objects.create_user(
            first_name='Athlete',
            last_name='User',
            username='ATHLETE.BS',
            email='athlete.bs@test.com',
            password='athletepassword',
            role=User.ATHLETE
        )

        self.associate = Associate.objects.create(
            user=self.athlete_user,
            first_name='Athlete',
            last_name='User',
            sex=Associate.MALE,
            tax_code='TXCODEATHBS',
            born_date='1990-01-01',
            born_city='Milano',
            address_city='Milano',
            address='Via Test 1',
            address_cap='20100',
        )

        self.payment_category = PaymentCategory.objects.create(
            name='Test Category',
            sport_association=self.sport_association,
            type=PaymentCategory.INSTITUTIONAL
        )

        self.commercial_category = PaymentCategory.objects.create(
            name='Commercial Category',
            sport_association=self.sport_association,
            type=PaymentCategory.COMMERCIAL
        )

    def _create_default_balance_sheet(self):
        return {
            'incoming': {
                'generalIncome': [],
            },
            'outgoing': {
                'generalExpenses': [],
            }
        }

    def test_generate_balance_sheet_no_payments(self):
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        self.assertIn('incoming', result)
        self.assertIn('outgoing', result)

    def test_generate_balance_sheet_with_income_payment(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('150.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=False,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        general_income = result['incoming']['generalIncome']
        self.assertGreater(len(general_income), 0)

    def test_generate_balance_sheet_with_expense_payment(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('75.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=True,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        general_expenses = result['outgoing']['generalExpenses']
        self.assertGreater(len(general_expenses), 0)

    def test_generate_balance_sheet_excludes_unpaid(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('200.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=False,
            expense=False,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        general_income = result['incoming']['generalIncome']
        for item in general_income:
            if item.get('id') == str(self.payment_category.payment_category_id):
                self.assertEqual(item['institutional'], 0)

    def test_generate_balance_sheet_excludes_deleted(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=False,
            deleted=True,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)

    def test_generate_balance_sheet_commercial_category(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('500.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.commercial_category,
            paid=True,
            expense=False,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        general_income = result['incoming']['generalIncome']
        commercial_found = False
        for item in general_income:
            if item.get('id') == str(self.commercial_category.payment_category_id):
                commercial_found = True
                self.assertGreater(item['commercial'], 0)
        self.assertTrue(commercial_found)

    def test_generate_balance_sheet_date_range(self):
        payment_outside = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('300.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=False,
            payment_date=datetime.datetime(2023, 6, 15, 10, 0, 0)
        )
        payment_inside = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('400.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=False,
            payment_date=datetime.datetime(2024, 6, 15, 10, 0, 0)
        )
        date_from = datetime.datetime(2024, 1, 1)
        date_to = datetime.datetime(2024, 12, 31)
        default_bs = self._create_default_balance_sheet()
        result = generate_balance_sheet(
            self.sport_association,
            date_from,
            date_to,
            default_bs
        )
        self.assertIsNotNone(result)
        general_income = result['incoming']['generalIncome']
        total = 0
        for item in general_income:
            if item.get('id') == str(self.payment_category.payment_category_id):
                total = item.get('institutional', 0) + item.get('commercial', 0)
        self.assertEqual(total, 400.0)


class ResetBalanceSheetTests(TestCase):
    """Tests for reset_balance_sheet_to_zero function."""

    def setUp(self):
        self.user = User.objects.create_user(
            first_name='Reset',
            last_name='Test',
            username='RESET.TEST',
            email='reset.test@example.com',
            password='testpassword',
            role=User.ASSOCIATION
        )

        self.sport_association = SportAssociation.objects.create(
            denomination='Reset Test ASD',
            tax_code='TXCODERST',
            subscription_fee='100.00',
            user=self.user
        )

        base_plan = BillingPlan.objects.filter(name__exact="Piano Base").first()
        if not base_plan:
            base_plan = BillingPlan.objects.create(
                name="Piano Base",
                description='Base plan',
                monthly_fee=0,
                annually_fee=0,
                billing_type=BillingPlan.BASE_PLAN,
            )
        BillingSubscription.objects.get_or_create(
            user=self.user,
            auto_renewal=True,
            renewal_type=BillingSubscription.ANNUALLY,
            ends_on=pytz.timezone('Europe/Rome').localize(
                datetime.datetime(2030, 12, 31), is_dst=None
            ),
            billing_plan=base_plan
        )

        self.athlete_user = User.objects.create_user(
            first_name='Reset',
            last_name='Athlete',
            username='RESET.ATHLETE',
            email='reset.athlete@test.com',
            password='athletepassword',
            role=User.ATHLETE
        )

        self.associate = Associate.objects.create(
            user=self.athlete_user,
            first_name='Reset',
            last_name='Athlete',
            sex=Associate.MALE,
            tax_code='TXCODERSTA',
            born_date='1990-01-01',
            born_city='Roma',
            address_city='Roma',
            address='Via Reset 1',
            address_cap='00100',
        )

        self.payment_category = PaymentCategory.objects.create(
            name='Reset Category',
            sport_association=self.sport_association,
            type=PaymentCategory.INSTITUTIONAL
        )

    def test_reset_balance_sheet_with_payments(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            paid=True,
            expense=False,
        )
        category_id = str(self.payment_category.payment_category_id)
        default_bs = {
            'incoming': {
                'generalIncome': [
                    {
                        'id': category_id,
                        'institutional': 500,
                        'commercial': 200
                    }
                ],
            },
            'outgoing': {
                'generalExpenses': [],
            }
        }
        result = reset_balance_sheet_to_zero(default_bs, [payment])
        self.assertEqual(result['incoming']['generalIncome'][0]['institutional'], 0)

    def test_reset_balance_sheet_empty_payments(self):
        default_bs = {
            'incoming': {
                'generalIncome': [
                    {
                        'id': 'test-id',
                        'institutional': 500,
                        'commercial': 200
                    }
                ],
            },
            'outgoing': {
                'generalExpenses': [],
            }
        }
        result = reset_balance_sheet_to_zero(default_bs, [])
        self.assertEqual(result['incoming']['generalIncome'][0]['institutional'], 500)
        self.assertEqual(result['incoming']['generalIncome'][0]['commercial'], 200)

    def test_reset_balance_sheet_no_category(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=None,
            paid=True,
            expense=False,
        )
        default_bs = {
            'incoming': {
                'generalIncome': [
                    {
                        'id': 'other_category',
                        'institutional': 500,
                        'commercial': 200
                    }
                ],
            },
            'outgoing': {
                'generalExpenses': [],
            }
        }
        result = reset_balance_sheet_to_zero(default_bs, [payment])
        self.assertEqual(result['incoming']['generalIncome'][0]['institutional'], 500)
