from decimal import Decimal
from unittest.mock import patch

from application.models.courses_models import Course, CourseSubscription
from application.models.payment_models import Payment
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import (
    create_test_associate, create_test_subscription, create_test_course,
    create_test_user, create_test_sport_association,
)


class MembershipFormAPITests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.associate = create_test_associate(sport_association=self.sport_association)
        self.subscription = create_test_subscription(sport_association=self.sport_association, associate=self.associate)
        self.course = create_test_course(self.sport_association, course_type=Course.MEMBERSHIP_TYPE)
        self.payload = {
            'course': str(self.course.pk), 'subscription_id': str(self.subscription.pk),
            'billed_from': '2026-09-01', 'billed_until': '2026-10-01',
            'billed_frequency': 1, 'billed_from_day_of_month': 1,
            'membership_fee': '25.50', 'membership_active': True, 'auto_renewal': False,
        }

    def create_membership(self):
        response = self.client.post('/course-subscriptions/add', [self.payload], format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return CourseSubscription.objects.get(pk=response.data[0]['course_subscription_id'])

    def test_create_stores_exact_dates_and_creates_matching_payment(self):
        membership = self.create_membership()
        self.assertEqual(str(membership.billed_from.date()), '2026-09-01')
        self.assertEqual(str(membership.billed_until.date()), '2026-10-01')
        payment = membership.membership_payments.get()
        self.assertEqual(payment.amount, Decimal('25.50'))
        self.assertEqual(payment.meta['billed_from'], '2026-09-01')
        self.assertEqual(payment.meta['billed_until'], '2026-10-01')

    def test_invalid_billing_rejected_before_subscription_or_payment_creation(self):
        for changes in [
            {'billed_from': None}, {'billed_until': None}, {'billed_from': '2026-02-30'},
            {'billed_until': '2026-08-31'}, {'billed_frequency': 0}, {'billed_frequency': 13},
            {'billed_from_day_of_month': None}, {'billed_from_day_of_month': 29},
            {'membership_fee': '-1'}, {'membership_fee': None},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post('/course-subscriptions/add', [{**self.payload, **changes}], format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(CourseSubscription.objects.filter(course=self.course).exists())
                self.assertFalse(Payment.objects.filter(subject=Payment.COURSE).exists())

    def test_mixed_invalid_batch_does_not_partially_create_memberships(self):
        response = self.client.post('/course-subscriptions/add', [self.payload, {**self.payload, 'billed_until': None}], format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CourseSubscription.objects.filter(course=self.course).exists())

    def test_patch_updates_unpaid_payment_without_resending_all_fields(self):
        membership = self.create_membership()
        response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', {'membership_fee': '38.00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        membership.refresh_from_db()
        self.assertEqual(membership.membership_fee, Decimal('38.00'))
        self.assertEqual(membership.membership_payments.get().amount, Decimal('38.00'))

    def test_patch_preserves_paid_payment_and_rejects_reversed_period(self):
        membership = self.create_membership()
        payment = membership.membership_payments.get()
        payment.paid = True
        payment.save()
        path = f'/course-subscriptions/{membership.pk}/update'
        self.assertEqual(self.client.patch(path, {'membership_fee': '38.00'}, format='json').status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal('25.50'))
        self.assertEqual(self.client.patch(path, {'billed_until': '2026-08-01'}, format='json').status_code, 400)
        membership.refresh_from_db()
        self.assertEqual(str(membership.billed_until.date()), '2026-10-01')

    def test_foreign_course_or_athlete_rejected(self):
        other = create_test_sport_association(user=create_test_user())
        course = create_test_course(other, course_type=Course.MEMBERSHIP_TYPE)
        subscription = create_test_subscription(sport_association=other, associate=create_test_associate(sport_association=other))
        for changes in [{'course': str(course.pk)}, {'subscription_id': str(subscription.pk)}]:
            response = self.client.post('/course-subscriptions/add', [{**self.payload, **changes}], format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_payment_failure_rolls_back_created_membership(self):
        with patch('application.signals.Payment.objects.create', side_effect=RuntimeError('payment failed')):
            with self.assertRaises(RuntimeError):
                self.client.post('/course-subscriptions/add', [self.payload], format='json')
        self.assertFalse(CourseSubscription.objects.filter(course=self.course).exists())
