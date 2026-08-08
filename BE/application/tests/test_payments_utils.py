"""
Tests for application.utils.payments_utils module.
"""
import datetime
from decimal import Decimal

import pytz
from django.test import TestCase

from application.models import BillingPlan, BillingSubscription
from application.models.payment_models import Payment, PaymentCategory
from application.models.subscriptions_models import Subscription
from application.models.user_models import User, SportAssociation, Associate
from application.utils.payments_utils import (
    PaymentUtils,
    generate_invoice_description,
    calculate_simulation
)


class PaymentUtilsConstantsTests(TestCase):
    """Tests for PaymentUtils class constants."""

    def test_payment_method_constants(self):
        self.assertEqual(PaymentUtils.CASH, 'cash')
        self.assertEqual(PaymentUtils.TRANSFER, 'transfer')
        self.assertEqual(PaymentUtils.ONLINE, 'online')
        self.assertEqual(PaymentUtils.SEPA_TRANSFER, 'sepa-transfer')

    def test_payment_invoice_descriptions_exist(self):
        self.assertIn(0, PaymentUtils.PAYMENT_INVOICE_DESCRIPTION)
        self.assertIn(1, PaymentUtils.PAYMENT_INVOICE_DESCRIPTION)
        self.assertIn(2, PaymentUtils.PAYMENT_INVOICE_DESCRIPTION)


class GenerateInvoiceDescriptionTests(TestCase):
    """Tests for generate_invoice_description function."""

    def setUp(self):
        self.user = User.objects.create_user(
            first_name='Invoice',
            last_name='Test',
            username='INVOICE.TEST',
            email='invoice.test@example.com',
            password='testpassword',
            role=User.ASSOCIATION
        )

        self.sport_association = SportAssociation.objects.create(
            denomination='Invoice Test ASD',
            tax_code='TXCODEINV',
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
            first_name='Invoice',
            last_name='Athlete',
            username='INVOICE.ATHLETE',
            email='invoice.athlete@test.com',
            password='athletepassword',
            role=User.ATHLETE
        )

        self.associate = Associate.objects.create(
            user=self.athlete_user,
            first_name='Mario',
            last_name='Rossi',
            sex=Associate.MALE,
            tax_code='TXCODEINVA',
            born_date='1990-01-01',
            born_city='Roma',
            address_city='Roma',
            address='Via Invoice 1',
            address_cap='00100',
        )

        self.payment_category = PaymentCategory.objects.create(
            name='Test Category',
            sport_association=self.sport_association,
            type=PaymentCategory.INSTITUTIONAL
        )

    def test_generate_invoice_description_other_payment(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.OTHER,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
        )
        description = generate_invoice_description(payment, self.sport_association)
        self.assertIn(self.payment_category.name, description)
        self.assertIn(self.sport_association.denomination, description)

    def test_generate_invoice_description_subscription_payment(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.SUBSCRIPTION,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            meta={'subscription_data': {'name': 'Annual Plan'}}
        )
        Subscription.objects.create(
            sport_association=self.sport_association,
            status_flag=Subscription.ACCEPTED,
            associate=self.associate,
            user=self.athlete_user,
            payment=payment,
        )
        description = generate_invoice_description(payment, self.sport_association)
        self.assertIn("Mario", description)
        self.assertIn("Rossi", description)
        self.assertIn(self.sport_association.denomination, description)

    def test_generate_invoice_description_subscription_without_meta(self):
        payment = Payment.objects.create(
            user=self.athlete_user,
            associate=self.associate,
            amount=Decimal('100.00'),
            subject=Payment.SUBSCRIPTION,
            sport_association=self.sport_association,
            payment_category=self.payment_category,
            meta=None
        )
        Subscription.objects.create(
            sport_association=self.sport_association,
            status_flag=Subscription.ACCEPTED,
            associate=self.associate,
            user=self.athlete_user,
            payment=payment,
        )
        description = generate_invoice_description(payment, self.sport_association)
        self.assertIsNotNone(description)


class CalculateSimulationTests(TestCase):
    """Tests for calculate_simulation function."""

    def setUp(self):
        self.user = User.objects.create_user(
            first_name='Simulation',
            last_name='Test',
            username='SIMULATION.TEST',
            email='simulation.test@example.com',
            password='testpassword',
            role=User.ASSOCIATION
        )

        self.sport_association = SportAssociation.objects.create(
            denomination='Simulation Test ASD',
            tax_code='TXCODESIM',
            subscription_fee='100.00',
            user=self.user,
            multiple_subscription_fee=False,
            subscription_fee_plans=[]
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

    def test_calculate_simulation_no_associations(self):
        plans, quotes = calculate_simulation()
        self.assertIsInstance(plans, list)
        self.assertIsInstance(quotes, list)

    def test_calculate_simulation_with_specific_association(self):
        plans, quotes = calculate_simulation(
            sport_association=str(self.sport_association.sport_association_id)
        )
        self.assertIsInstance(plans, list)
        self.assertIsInstance(quotes, list)

    def test_calculate_simulation_with_date(self):
        plans, quotes = calculate_simulation(date='2024-01-15')
        self.assertIsInstance(plans, list)
        self.assertIsInstance(quotes, list)

    def test_calculate_simulation_with_advanced_options(self):
        self.sport_association.multiple_subscription_fee = True
        self.sport_association.subscription_fee_plans = [
            {
                'id': 'plan-1',
                'name': 'Plan 1',
                'subscription_fee': '100.00',
                'auto_assign': True,
                'advanced_options': True,
                'from_day': datetime.datetime.now().day,
                'from_month': datetime.datetime.now().month,
                'to_day': 31,
                'to_month': 12,
                'previous_subscription_fee_plan': 'previous-plan'
            }
        ]
        self.sport_association.save()

        plans, quotes = calculate_simulation(
            sport_association=str(self.sport_association.sport_association_id)
        )
        self.assertIsInstance(plans, list)
        self.assertIsInstance(quotes, list)

    def test_calculate_simulation_returns_tuple(self):
        result = calculate_simulation()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], list)
        self.assertIsInstance(result[1], list)
