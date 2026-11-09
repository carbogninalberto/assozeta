"""Nightly subscription maintenance tasks: cleanup of deleted rows and yearly archive."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
from application.models.payment_models import Invoice, Payment
from application.models.subscriptions_models import Subscription
from application.tasks import auto_archive_subscription, clear_deleted_subscription
from application.tests.fixtures.factories import (
    create_test_associate,
    create_test_course,
    create_test_course_subscription,
    create_test_course_subscription_installment,
    create_test_invoice,
    create_test_payment,
    create_test_sport_association,
    create_test_subscription,
    create_test_user,
)


class ClearDeletedSubscriptionTests(TestCase):
    def setUp(self):
        self.association = create_test_sport_association()

    def _payment(self, paid, with_invoice=True, invoice=None):
        if with_invoice and invoice is None:
            invoice = create_test_invoice(sport_association=self.association)
        return create_test_payment(sport_association=self.association, paid=paid, invoice=invoice)

    def _subscription(self, deleted=True, payment=None):
        return create_test_subscription(
            sport_association=self.association,
            associate=create_test_associate(sport_association=self.association),
            payment=payment,
            deleted=deleted,
        )

    def test_keeps_paid_payment_and_invoice_and_removes_the_subscription(self):
        payment = self._payment(paid=True)
        subscription = self._subscription(payment=payment)

        clear_deleted_subscription()

        self.assertFalse(Subscription._base_manager.filter(pk=subscription.pk).exists())
        self.assertTrue(Payment._base_manager.filter(pk=payment.pk).exists())
        self.assertTrue(Invoice._base_manager.filter(pk=payment.invoice_id).exists())

    def test_removes_unpaid_payment_and_its_invoice(self):
        payment = self._payment(paid=False)
        invoice_id = payment.invoice_id
        subscription = self._subscription(payment=payment)

        clear_deleted_subscription()

        self.assertFalse(Subscription._base_manager.filter(pk=subscription.pk).exists())
        self.assertFalse(Payment._base_manager.filter(pk=payment.pk).exists())
        self.assertFalse(Invoice._base_manager.filter(pk=invoice_id).exists())

    def test_keeps_an_invoice_still_used_by_another_payment(self):
        shared_invoice = create_test_invoice(sport_association=self.association)
        unpaid = self._payment(paid=False, invoice=shared_invoice)
        other = self._payment(paid=True, invoice=shared_invoice)
        self._subscription(payment=unpaid)

        clear_deleted_subscription()

        self.assertFalse(Payment._base_manager.filter(pk=unpaid.pk).exists())
        self.assertTrue(Payment._base_manager.filter(pk=other.pk).exists())
        self.assertTrue(Invoice._base_manager.filter(pk=shared_invoice.pk).exists())

    def test_course_and_installment_payments_follow_the_paid_flag(self):
        subscription = self._subscription()
        course = create_test_course(sport_association=self.association)
        paid_course_payment = self._payment(paid=True)
        unpaid_course_payment = self._payment(paid=False)
        paid_course = create_test_course_subscription(
            course=course, subscription=subscription, payment=paid_course_payment)
        create_test_course_subscription(
            course=create_test_course(sport_association=self.association),
            subscription=subscription, payment=unpaid_course_payment, deleted=True)
        multi = create_test_course_subscription(
            course=create_test_course(sport_association=self.association),
            subscription=subscription, multi_payments=True)
        paid_installment_payment = self._payment(paid=True)
        unpaid_installment_payment = self._payment(paid=False)
        create_test_course_subscription_installment(
            course_subscription=multi, id=1, payment=paid_installment_payment, paid=True)
        create_test_course_subscription_installment(
            course_subscription=multi, id=2, payment=unpaid_installment_payment)

        clear_deleted_subscription()

        self.assertFalse(CourseSubscription._base_manager.filter(subscription_id=subscription.pk).exists())
        self.assertFalse(CourseSubscriptionInstallment._base_manager.filter(course_subscription=multi).exists())
        self.assertFalse(CourseSubscription._base_manager.filter(pk=paid_course.pk).exists())
        kept = set(Payment._base_manager.filter(pk__in=[
            paid_course_payment.pk, unpaid_course_payment.pk,
            paid_installment_payment.pk, unpaid_installment_payment.pk,
        ]).values_list('pk', flat=True))
        self.assertEqual(kept, {paid_course_payment.pk, paid_installment_payment.pk})
        self.assertTrue(Invoice._base_manager.filter(pk=paid_course_payment.invoice_id).exists())
        self.assertTrue(Invoice._base_manager.filter(pk=paid_installment_payment.invoice_id).exists())
        self.assertFalse(Invoice._base_manager.filter(pk__in=[
            unpaid_course_payment.invoice_id, unpaid_installment_payment.invoice_id]).exists())

    def test_handles_several_subscriptions_and_leaves_active_ones_alone(self):
        deleted = [self._subscription(payment=self._payment(paid=bool(i % 2))) for i in range(5)]
        active_payment = self._payment(paid=False)
        active = self._subscription(deleted=False, payment=active_payment)

        clear_deleted_subscription()

        self.assertFalse(Subscription._base_manager.filter(pk__in=[s.pk for s in deleted]).exists())
        self.assertTrue(Subscription._base_manager.filter(pk=active.pk).exists())
        self.assertTrue(Payment._base_manager.filter(pk=active_payment.pk).exists())
        self.assertEqual(
            Payment._base_manager.filter(pk__in=[s.payment_id for s in deleted]).count(), 2)

    def test_second_run_finds_nothing_left_to_clean(self):
        paid = self._payment(paid=True)
        self._subscription(payment=paid)
        self._subscription(payment=self._payment(paid=False))

        clear_deleted_subscription()
        payments_after_first = Payment._base_manager.count()
        invoices_after_first = Invoice._base_manager.count()
        clear_deleted_subscription()

        self.assertFalse(Subscription._base_manager.filter(deleted=True).exists())
        self.assertEqual(Payment._base_manager.count(), payments_after_first)
        self.assertEqual(Invoice._base_manager.count(), invoices_after_first)
        self.assertTrue(Payment._base_manager.filter(pk=paid.pk).exists())


def _at(year, month, day, hour=0, minute=0, tz='UTC'):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


class AutoArchiveSubscriptionTests(TestCase):
    def setUp(self):
        self.owner = create_test_user(
            auto_archive=True, balance_sheet_start_day=1, balance_sheet_start_month=9)
        self.association = create_test_sport_association(user=self.owner)

    def _subscription(self, created, with_payment=True, with_invoice=True, association=None):
        association = association or self.association
        payment = None
        if with_payment:
            invoice = create_test_invoice(sport_association=association) if with_invoice else None
            payment = create_test_payment(sport_association=association, invoice=invoice)
        subscription = create_test_subscription(
            sport_association=association,
            associate=create_test_associate(sport_association=association),
            payment=payment,
        )
        Subscription._base_manager.filter(pk=subscription.pk).update(creation_date=created)
        return subscription

    def _run_at(self, now):
        with patch('application.tasks.timezone.now', return_value=now):
            auto_archive_subscription()

    def _archived(self, subscription):
        return Subscription._base_manager.get(pk=subscription.pk).archived

    def test_archives_subscriptions_of_a_closed_year_with_payment_and_invoice(self):
        old = self._subscription(_at(2025, 10, 15, 10))
        current = self._subscription(_at(2026, 9, 2, 10))

        self._run_at(_at(2026, 9, 1, 1))

        self.assertTrue(self._archived(old))
        self.assertTrue(Payment._base_manager.get(pk=old.payment_id).archived)
        self.assertTrue(Invoice._base_manager.get(pk=old.payment.invoice_id).archived)
        self.assertFalse(self._archived(current))
        self.assertFalse(Payment._base_manager.get(pk=current.payment_id).archived)

    def test_archives_every_eligible_subscription_even_without_payment_or_invoice(self):
        without_payment = self._subscription(_at(2025, 9, 1, 8), with_payment=False)
        without_invoice = self._subscription(_at(2025, 12, 1, 8), with_invoice=False)
        complete = self._subscription(_at(2026, 8, 30, 8))

        self._run_at(_at(2026, 9, 1, 1))

        self.assertTrue(self._archived(without_payment))
        self.assertTrue(self._archived(without_invoice))
        self.assertTrue(Payment._base_manager.get(pk=without_invoice.payment_id).archived)
        self.assertTrue(self._archived(complete))

    def test_skips_associations_without_auto_archive(self):
        other = create_test_sport_association(user=create_test_user(
            auto_archive=False, balance_sheet_start_day=1, balance_sheet_start_month=9))
        subscription = self._subscription(_at(2025, 10, 1), association=other)

        self._run_at(_at(2026, 9, 1, 1))

        self.assertFalse(self._archived(subscription))

    def test_running_twice_keeps_the_same_result(self):
        old = self._subscription(_at(2025, 10, 1))

        self._run_at(_at(2026, 9, 1, 1))
        self._run_at(_at(2026, 9, 2, 1))

        self.assertTrue(self._archived(old))
        self.assertEqual(Subscription._base_manager.filter(archived=True).count(), 1)

    @override_settings(TIME_ZONE='Europe/Rome')
    def test_year_boundary_follows_the_local_time_zone(self):
        self.owner.balance_sheet_start_month = 1
        self.owner.save(update_fields=['balance_sheet_start_month'])
        last_year = self._subscription(_at(2025, 12, 31, 20, tz='Europe/Rome'))
        # 00:30 on 1 January in Rome is still 31 December in UTC.
        new_year = self._subscription(_at(2026, 1, 1, 0, 30, tz='Europe/Rome'))

        self._run_at(_at(2025, 12, 31, 23, 30, tz='Europe/Rome'))
        self.assertFalse(self._archived(last_year))

        self._run_at(_at(2026, 1, 1, 0, 30, tz='Europe/Rome'))
        self.assertTrue(self._archived(last_year))
        self.assertFalse(self._archived(new_year))
