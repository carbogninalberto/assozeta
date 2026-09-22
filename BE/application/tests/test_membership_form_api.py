from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from application.models.courses_models import Course, CourseSubscription
from application.models.payment_models import Payment
from application.tasks import renew_memberships_payments
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

    def renew_membership(self, original_paid=False):
        self.payload['auto_renewal'] = True
        self.course.status_flag = Course.ACTIVE
        self.course.save()
        membership = self.create_membership()
        original = membership.membership_payments.get()
        original.paid = original_paid
        original.save()
        with patch('application.tasks.cache') as cache, patch('application.tasks.send_mail'), patch(
            'application.tasks.timezone.now', return_value=datetime(2026, 10, 2, tzinfo=timezone.utc),
        ):
            cache.add.return_value = True
            renew_memberships_payments.run()
        membership.refresh_from_db()
        self.assertEqual(membership.membership_payments.count(), 2)
        current = membership.membership_payments.exclude(pk=original.pk).get()
        self.assertEqual(current.meta['billed_from'], '2026-10-01')
        self.assertEqual(current.meta['billed_until'], '2026-11-01')
        return membership, original, current

    @staticmethod
    def payment_snapshot(payment):
        return Payment.objects.filter(pk=payment.pk).values().get()

    def test_renewed_fee_edit_updates_only_current_payment(self):
        for original_paid in (False, True):
            for full_form in (False, True):
                with self.subTest(original_paid=original_paid, full_form=full_form):
                    membership, original, current = self.renew_membership(original_paid)
                    original_before = self.payment_snapshot(original)
                    current_dates = (current.creation_date, current.payment_date)
                    data = {**self.payload, 'billed_until': '2026-11-01'} if full_form else {}
                    data['membership_fee'] = '38.00'
                    response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', data, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(self.payment_snapshot(original), original_before)
                    current.refresh_from_db()
                    self.assertEqual(current.amount, Decimal('38.00'))
                    self.assertEqual(current.meta['billed_from'], '2026-10-01')
                    self.assertEqual(current.meta['billed_until'], '2026-11-01')
                    self.assertEqual(current.meta['amount'], '38.00')
                    self.assertIn('dal 01/10/2026 al 01/11/2026', current.description)
                    self.assertEqual((current.creation_date, current.payment_date), current_dates)

    def test_paid_current_period_does_not_fall_back_to_unpaid_original(self):
        membership, original, current = self.renew_membership()
        current.paid = True
        current.save()
        before = [self.payment_snapshot(payment) for payment in (original, current)]
        response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', {'membership_fee': '38.00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([self.payment_snapshot(payment) for payment in (original, current)], before)
        membership.refresh_from_db()
        self.assertEqual(membership.membership_fee, Decimal('38.00'))

    def test_renewed_period_end_edit_preserves_history_and_remains_editable(self):
        membership, original, current = self.renew_membership()
        original_before = self.payment_snapshot(original)
        path = f'/course-subscriptions/{membership.pk}/update'
        self.assertEqual(self.client.patch(path, {'billed_until': '2026-11-15'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(path, {'membership_fee': '38.00'}, format='json').status_code, 200)
        self.assertEqual(self.payment_snapshot(original), original_before)
        current.refresh_from_db()
        self.assertEqual(current.meta['billed_from'], '2026-10-01')
        self.assertEqual(current.meta['billed_until'], '2026-11-15')
        self.assertEqual(current.amount, Decimal('38.00'))

    def test_renewed_period_cannot_reverse_or_overlap_history(self):
        membership, original, current = self.renew_membership()
        before = [self.payment_snapshot(payment) for payment in (original, current)]
        for data in ({'billed_until': '2026-09-15'}, {'billed_from': '2026-09-15'}):
            with self.subTest(data=data):
                response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', data, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual([self.payment_snapshot(payment) for payment in (original, current)], before)
                membership.refresh_from_db()
                self.assertEqual(membership.billed_from.date().isoformat(), '2026-09-01')
                self.assertEqual(membership.billed_until.date().isoformat(), '2026-11-01')

    def test_initial_period_dates_remain_editable(self):
        membership = self.create_membership()
        response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', {
            'billed_from': '2026-09-10', 'billed_until': '2026-10-10',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        payment = membership.membership_payments.get()
        self.assertEqual(payment.meta['billed_from'], '2026-09-10')
        self.assertEqual(payment.meta['billed_until'], '2026-10-10')
        self.assertEqual(payment.payment_date.date().isoformat(), '2026-09-10')

    def test_payment_update_failure_rolls_back_membership_changes(self):
        membership, original, current = self.renew_membership()
        before = [self.payment_snapshot(payment) for payment in (original, current)]
        with patch.object(Payment, 'save', side_effect=RuntimeError('payment update failed')):
            with self.assertRaises(RuntimeError):
                self.client.patch(f'/course-subscriptions/{membership.pk}/update', {'membership_fee': '38.00'}, format='json')
        membership.refresh_from_db()
        self.assertEqual(membership.membership_fee, Decimal('25.50'))
        self.assertEqual([self.payment_snapshot(payment) for payment in (original, current)], before)

    def test_full_form_repairs_single_unpaid_payment_after_legacy_partial_save(self):
        self.payload.update(billed_from='2026-10-04', billed_until='2027-01-04',
                            billed_frequency=3, membership_fee='150.00')
        membership = self.create_membership()
        payment = membership.membership_payments.get()
        # Reproduce the old handler saving the subscription before its KeyError.
        CourseSubscription.objects.filter(pk=membership.pk).update(
            billed_from=datetime(2026, 10, 25, tzinfo=timezone.utc),
            billed_until=datetime(2027, 1, 25, tzinfo=timezone.utc),
        )
        response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', {
            **self.payload, 'billed_from': '2026-10-25', 'billed_until': '2027-01-25',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(membership.membership_payments.count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.meta['billed_from'], '2026-10-25')
        self.assertEqual(payment.meta['billed_until'], '2027-01-25')
        self.assertEqual(payment.meta['course_subscription_id'], str(membership.pk))
        self.assertEqual(payment.payment_date.date().isoformat(), '2026-10-25')
        self.assertEqual(payment.amount, Decimal('150.00'))

    def test_mismatched_renewed_period_is_rejected_without_guessing_payment(self):
        membership, original, current = self.renew_membership()
        before = [self.payment_snapshot(payment) for payment in (original, current)]
        CourseSubscription.objects.filter(pk=membership.pk).update(
            billed_until=datetime(2026, 11, 25, tzinfo=timezone.utc),
        )
        response = self.client.patch(f'/course-subscriptions/{membership.pk}/update', {
            **self.payload, 'billed_until': '2026-11-25', 'membership_fee': '38.00',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('billed_until', response.data)
        membership.refresh_from_db()
        self.assertEqual(membership.membership_fee, Decimal('25.50'))
        self.assertEqual([self.payment_snapshot(payment) for payment in (original, current)], before)
