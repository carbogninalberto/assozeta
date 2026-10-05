"""Temporary category cleanup preserves baseline and unrelated fixture records."""
from django.core.management import CommandError

from application.management.commands.seed_manuale import fixture_id, reset_payment_categories
from application.models import Payment, PaymentCategory, User
from application.models.payment_models import VatManagement
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import create_test_user, create_test_sport_association


class ManualCategoryResetTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(pk=fixture_id('owner'), role=User.ASSOCIATION)
        self.association = create_test_sport_association(pk=fixture_id('association'), user=self.owner)
        self.baseline = PaymentCategory.objects.create(pk=fixture_id('payment-category'),
            sport_association=self.association, name='Baseline')

    def test_reset_removes_temporary_and_deleted_categories_without_losing_shared_vat(self):
        orphan_vat = VatManagement.objects.create()
        shared_vat = VatManagement.objects.create()
        temporary = PaymentCategory.objects.create(sport_association=self.association,
            name='Repeated local label', vat_management=orphan_vat)
        deleted = PaymentCategory.objects.create(sport_association=self.association,
            name='Repeated local label', deleted=True, vat_management=shared_vat)
        global_category = PaymentCategory.objects.create(name='Shared category', vat_management=shared_vat)
        foreign = PaymentCategory.objects.create(sport_association=create_test_sport_association(),
            name='Unrelated category')
        preserved_ids = set(PaymentCategory._base_manager.values_list('pk', flat=True)) - {temporary.pk, deleted.pk}
        for _ in range(2):
            reset_payment_categories(self.association, self.owner)
            self.assertFalse(PaymentCategory._base_manager.filter(pk__in=[temporary.pk, deleted.pk]).exists())
            self.assertFalse(VatManagement.objects.filter(pk=orphan_vat.pk).exists())
            self.assertTrue(VatManagement.objects.filter(pk=shared_vat.pk).exists())
            self.assertEqual(set(PaymentCategory._base_manager.values_list('pk', flat=True)),
                preserved_ids)

    def test_foreign_reference_is_rejected_before_any_category_or_vat_is_deleted(self):
        vat = VatManagement.objects.create()
        category = PaymentCategory.objects.create(sport_association=self.association, name='Temporary', vat_management=vat)
        foreign = create_test_sport_association()
        payment = Payment.objects.create(sport_association=foreign, user=foreign.user,
            payment_category=category, amount=10, paid=False)
        with self.assertRaisesRegex(CommandError, 'unrelated references'):
            reset_payment_categories(self.association, self.owner)
        payment.refresh_from_db()
        self.assertEqual(payment.payment_category_id, category.pk)
        self.assertTrue(PaymentCategory.objects.filter(pk=category.pk).exists())
        self.assertTrue(VatManagement.objects.filter(pk=vat.pk).exists())

    def test_reset_refuses_other_association_or_owner(self):
        foreign = create_test_sport_association()
        for association, owner in [(foreign, foreign.user), (self.association, foreign.user)]:
            with self.assertRaisesRegex(CommandError, 'owned manual fixture'):
                reset_payment_categories(association, owner)
        self.assertTrue(PaymentCategory.objects.filter(pk=self.baseline.pk).exists())
