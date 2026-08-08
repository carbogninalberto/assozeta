from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from application.models.payment_models import Payment, PaymentCategory
from application.models.user_models import User
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)


class PaymentViewsTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.athlete_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=self.user)

    def payment_payload(self, **overrides):
        data = {
            'amount': '100.00',
            'subject': Payment.SUBSCRIPTION,
            'type': Payment.CASH,
            'description': 'Test payment',
            'creation_date': '',
            'payment_date': '',
            'complex_item': None,
            'paid': False,
            'expense': False,
        }
        data.update(overrides)
        return data

    def create_payment(self, **overrides):
        return create_test_payment(
            sport_association=self.sport_association,
            associate=self.associate,
            user=self.user,
            **overrides,
        )


class PaymentCreationTests(PaymentViewsTestCase):
    def test_add_payment(self):
        response = self.client.post('/payment/add', self.payment_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(payment_id=response.data['payment_id'])
        self.assertEqual(payment.amount, Decimal('100.00'))

    def test_add_payment_linked_to_subscription(self):
        subscription = create_test_subscription(
            sport_association=self.sport_association,
            associate=self.associate,
        )
        response = self.client.post('/payment/add', self.payment_payload(
            amount='50.00',
            type=Payment.TRANSFER,
            complex_item={
                'group': 'Iscrizioni',
                'subscription_id': str(subscription.subscription_id),
            },
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(payment_id=response.data['payment_id'])
        self.assertEqual(payment.associate, self.associate)

    def test_athlete_cannot_add_payment(self):
        self.client.force_authenticate(user=self.athlete_user)

        response = self.client.post('/payment/add', self.payment_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_add_supported_payment_types(self):
        for payment_type in (Payment.CASH, Payment.TRANSFER, Payment.POS, Payment.SEPA_TRANSFER):
            with self.subTest(payment_type=payment_type):
                response = self.client.post(
                    '/payment/add',
                    self.payment_payload(type=payment_type),
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                payment = Payment.objects.get(payment_id=response.data['payment_id'])
                self.assertEqual(payment.type, payment_type)

    def test_add_supported_subjects(self):
        for subject in (Payment.OTHER, Payment.SUBSCRIPTION, Payment.COURSE):
            with self.subTest(subject=subject):
                response = self.client.post(
                    '/payment/add',
                    self.payment_payload(subject=subject),
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                payment = Payment.objects.get(payment_id=response.data['payment_id'])
                self.assertEqual(payment.subject, subject)

    def test_add_expense(self):
        response = self.client.post(
            '/payment/add',
            self.payment_payload(expense=True),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(payment_id=response.data['payment_id'])
        self.assertTrue(payment.expense)

    def test_zero_amount_is_valid(self):
        response = self.client.post(
            '/payment/add',
            self.payment_payload(amount='0.00'),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(payment_id=response.data['payment_id'])
        self.assertEqual(payment.amount, Decimal('0.00'))


class PaymentStateTransitionTests(PaymentViewsTestCase):
    def test_approve_payment(self):
        payment = self.create_payment(paid=False)

        response = self.client.post(
            f'/payment/{payment.payment_id}/approve',
            {'payment_date': '2024-01-15', 'generate_invoice': False},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertTrue(payment.paid)
        self.assertIsNotNone(payment.payment_date)

    def test_approving_paid_payment_is_idempotent(self):
        payment = self.create_payment(paid=True)

        response = self.client.post(
            f'/payment/{payment.payment_id}/approve',
            {'payment_date': '2024-01-15', 'generate_invoice': False},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertTrue(payment.paid)

    def test_cancel_paid_payment(self):
        payment = self.create_payment(paid=True)

        response = self.client.post(f'/payment/{payment.payment_id}/cancel')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertFalse(payment.paid)

    def test_cancel_unpaid_payment_rejected(self):
        payment = self.create_payment(paid=False)

        response = self.client.post(f'/payment/{payment.payment_id}/cancel')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        self.assertFalse(payment.paid)

    def test_archive_toggles(self):
        payment = self.create_payment(archived=False)

        first_response = self.client.post(f'/payment/{payment.payment_id}/archive')
        payment.refresh_from_db()
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertTrue(payment.archived)

        second_response = self.client.post(f'/payment/{payment.payment_id}/archive')
        payment.refresh_from_db()
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertFalse(payment.archived)

    def test_delete_soft_deletes(self):
        payment = self.create_payment()

        response = self.client.delete(f'/payment/{payment.payment_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Payment.objects.filter(payment_id=payment.payment_id).exists())


class PaymentUpdateTests(PaymentViewsTestCase):
    def test_update_payment_fields(self):
        payment = self.create_payment(
            amount=Decimal('100.00'),
            type=Payment.CASH,
            description='Old description',
        )

        response = self.client.patch(
            f'/payment/{payment.payment_id}/update',
            {
                'amount': '150.00',
                'type': Payment.TRANSFER,
                'description': 'Updated description',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal('150.00'))
        self.assertEqual(payment.type, Payment.TRANSFER)
        self.assertEqual(payment.description, 'Updated description')


class PaymentPermissionTests(PaymentViewsTestCase):
    def test_cannot_approve_other_association_payment(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_payment = create_test_payment(
            sport_association=other_association,
            user=other_user,
        )

        response = self.client.post(
            f'/payment/{other_payment.payment_id}/approve',
            {'payment_date': '2024-01-15'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_delete_other_association_payment(self):
        other_user = create_test_user(role=User.ASSOCIATION)
        other_association = create_test_sport_association(user=other_user)
        other_payment = create_test_payment(
            sport_association=other_association,
            user=other_user,
        )

        response = self.client.delete(f'/payment/{other_payment.payment_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get('/payment/list')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PaymentListTests(PaymentViewsTestCase):
    def test_list_returns_payments(self):
        payment = self.create_payment()

        response = self.client.get('/payment/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)
        payment_ids = [item['payment_id'] for item in response.data['data']]
        self.assertIn(str(payment.payment_id), payment_ids)

    def test_list_accepts_paid_filter(self):
        self.create_payment(paid=True)
        self.create_payment(paid=False)

        response = self.client.get('/payment/list', {'query[paid]': 'true'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_accepts_expense_filter(self):
        self.create_payment(expense=False)
        self.create_payment(expense=True)

        response = self.client.get('/payment/list', {'query[expense]': 'true'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PaymentBulkOperationTests(PaymentViewsTestCase):
    def test_bulk_archive(self):
        payments = [self.create_payment() for _ in range(3)]

        response = self.client.post(
            '/payment-bulk/archive',
            {'payment_ids': [str(payment.payment_id) for payment in payments]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for payment in payments:
            payment.refresh_from_db()
            self.assertTrue(payment.archived)

    def test_bulk_delete(self):
        payments = [self.create_payment() for _ in range(3)]

        response = self.client.post(
            '/payment-bulk/delete',
            {'payment_ids': [str(payment.payment_id) for payment in payments]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for payment in payments:
            self.assertFalse(Payment.objects.filter(payment_id=payment.payment_id).exists())


class PaymentCategoryTests(PaymentViewsTestCase):
    def test_list_categories(self):
        response = self.client.get('/payment/category/list')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_add_category(self):
        response = self.client.post('/payment/category/add', {
            'name': 'Test Category',
            'expense': False,
            'vat_management': {'vat': 0, 'deduction': 0},
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(PaymentCategory.objects.filter(
            name='Test Category',
            sport_association=self.sport_association,
        ).exists())

    def test_update_category(self):
        category = PaymentCategory.objects.create(
            name='Original',
            sport_association=self.sport_association,
        )

        response = self.client.patch(
            f'/payment/category/{category.payment_category_id}/update',
            {'name': 'Updated'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.name, 'Updated')

    def test_delete_category(self):
        category = PaymentCategory.objects.create(
            name='Delete',
            sport_association=self.sport_association,
        )

        response = self.client.delete(f'/payment/category/{category.payment_category_id}/delete')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertTrue(category.deleted)

    def test_delete_missing_category_returns_not_found(self):
        response = self.client.delete(
            '/payment/category/00000000-0000-0000-0000-000000000000/delete'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_athlete_cannot_manage_categories(self):
        self.client.force_authenticate(user=self.athlete_user)

        list_response = self.client.get('/payment/category/list')
        add_response = self.client.post('/payment/category/add', {'name': 'Denied'}, format='json')

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(add_response.status_code, status.HTTP_403_FORBIDDEN)


class PaymentStatsTests(PaymentViewsTestCase):
    def test_stats_return_all_totals(self):
        self.create_payment(amount=Decimal('100.00'), paid=True, expense=False)
        self.create_payment(amount=Decimal('50.00'), paid=True, expense=True)
        self.create_payment(amount=Decimal('25.00'), paid=False, expense=False)

        response = self.client.get('/payment/stats')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('monthly_income', response.data['data'])
        self.assertIn('monthly_expenses', response.data['data'])
        self.assertIn('monthly_to_cash_in', response.data['data'])


class PaymentInfoTests(PaymentViewsTestCase):
    def test_get_payment_info(self):
        payment = self.create_payment()

        response = self.client.get(f'/payment/{payment.payment_id}/info')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)

    def test_missing_payment_returns_not_found(self):
        response = self.client.get('/payment/00000000-0000-0000-0000-000000000000/info')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_paid_payment_cannot_be_requested(self):
        payment = self.create_payment(paid=True)

        response = self.client.post(f'/payment/{payment.payment_id}/request')

        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)


class PaymentBulkAddTests(PaymentViewsTestCase):
    def bulk_payload(self, associate_ids=None, **overrides):
        data = {
            'associate_ids': associate_ids if associate_ids is not None else [str(self.associate.associate_id)],
            'amount': '100.00',
            'type': Payment.CASH,
            'subject': Payment.SUBSCRIPTION,
        }
        data.update(overrides)
        return data

    def test_bulk_add(self):
        second = create_test_associate(sport_association=self.sport_association)
        third = create_test_associate(sport_association=self.sport_association)

        response = self.client.post('/payment/bulk-add', self.bulk_payload([
            str(self.associate.associate_id),
            str(second.associate_id),
            str(third.associate_id),
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['created_count'], 3)
        self.assertEqual(response.data['data']['invalid_associate_ids'], [])

    def test_bulk_add_reports_invalid_associates(self):
        missing_id = '00000000-0000-0000-0000-000000000000'

        response = self.client.post('/payment/bulk-add', self.bulk_payload([
            str(self.associate.associate_id),
            missing_id,
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['created_count'], 1)
        self.assertIn(missing_id, response.data['data']['invalid_associate_ids'])

    def test_bulk_add_all_invalid_returns_no_content_created(self):
        response = self.client.post('/payment/bulk-add', self.bulk_payload([
            '00000000-0000-0000-0000-000000000001',
            '00000000-0000-0000-0000-000000000002',
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['created_count'], 0)

    def test_athlete_cannot_bulk_add(self):
        self.client.force_authenticate(user=self.athlete_user)

        response = self.client.post('/payment/bulk-add', self.bulk_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_bulk_add_validates_required_fields(self):
        cases = [
            ({'amount': '100.00', 'type': Payment.CASH, 'subject': Payment.OTHER}, 'associate_ids'),
            (self.bulk_payload([]), 'associate_ids'),
            ({'associate_ids': [str(self.associate.associate_id)], 'type': Payment.CASH, 'subject': Payment.OTHER}, 'amount'),
        ]
        for payload, field in cases:
            with self.subTest(field=field):
                response = self.client.post('/payment/bulk-add', payload, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data['msg'])

    def test_bulk_add_validates_values(self):
        cases = [
            (self.bulk_payload(amount='-100.00'), 'amount'),
            (self.bulk_payload(type='invalid'), 'type'),
            (self.bulk_payload(subject=99), 'subject'),
        ]
        for payload, field in cases:
            with self.subTest(field=field):
                response = self.client.post('/payment/bulk-add', payload, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data['msg'])

    def test_bulk_add_optional_fields(self):
        response = self.client.post('/payment/bulk-add', self.bulk_payload(
            amount='75.50',
            type=Payment.TRANSFER,
            subject=Payment.COURSE,
            description='Custom description',
            creation_date='2026-01-15',
            payment_date='2026-01-20',
            expense=True,
            paid=True,
            notes='Test notes',
        ), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payment = Payment.objects.get(associate=self.associate, amount=Decimal('75.50'))
        self.assertEqual(payment.description, 'Custom description')
        self.assertTrue(payment.expense)
        self.assertTrue(payment.paid)
        self.assertEqual(payment.notes, 'Test notes')

    def test_bulk_add_default_descriptions(self):
        cases = [
            (Payment.SUBSCRIPTION, 'Iscrizione'),
            (Payment.COURSE, 'Quota Corso'),
        ]
        for subject, description in cases:
            with self.subTest(subject=subject):
                response = self.client.post(
                    '/payment/bulk-add',
                    self.bulk_payload(subject=subject),
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                payment = Payment.objects.filter(
                    associate=self.associate,
                    subject=subject,
                ).latest('creation_date')
                self.assertEqual(payment.description, description)

    def test_bulk_add_rejects_foreign_associate(self):
        other_association = create_test_sport_association()
        foreign_associate = create_test_associate(sport_association=other_association)

        response = self.client.post('/payment/bulk-add', self.bulk_payload([
            str(self.associate.associate_id),
            str(foreign_associate.associate_id),
        ]), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['created_count'], 1)
        self.assertIn(
            str(foreign_associate.associate_id),
            response.data['data']['invalid_associate_ids'],
        )
