"""A supplier invoice may only link a payment the caller can see in its association."""
import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from application.models import Payment, PaymentCategory, User
from application.models.balance_sheet_models import CustomAccounts
from application.models.invoices_models import InvoiceSuppliers
from application.models.payment_models import SupplierAndCustomers
from application.models.user_models import Group
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)


class SupplierInvoicePaymentLinkTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.foreign_owner = create_test_user(role=User.ASSOCIATION)
        self.foreign = create_test_sport_association(user=self.foreign_owner)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.cash = CustomAccounts.objects.create(
            sport_association=self.association,
            name='Test cash',
            initial_balance=0,
            account_type=CustomAccounts.CASH,
            account_code='TEST',
        )
        self.supplier = SupplierAndCustomers.objects.create(
            sport_association=self.association,
            name='Supplier owned by this association',
            type='supplier',
        )
        # Test-owned database cleanup makes the prerequisite explicit.
        PaymentCategory.objects.filter(name__iexact='Pagamento Fornitore').delete()
        PaymentCategory.objects.create(
            sport_association=self.association,
            name='Pagamento Fornitore',
            expense=True,
        )

    def payment(self, association, **kwargs):
        values = {
            'user': association.user,
            'sport_association': association,
            'amount': Decimal('12.00'),
            'paid': False,
        }
        values.update(kwargs)
        return Payment.objects.create(**values)

    def invoice(self, **kwargs):
        values = {
            'sport_association': self.association,
            'supplier': self.supplier,
            'invoice_identifier': 'MANUALE-LOCAL-1',
            'amount': Decimal('12.00'),
        }
        values.update(kwargs)
        return InvoiceSuppliers.objects.create(**values)

    def update(self, invoice, payload, **headers):
        return self.client.patch(
            f'/invoice-suppliers/{invoice.pk}/update',
            payload,
            format='json',
            **headers,
        )

    def add_payload(self, **kwargs):
        now = timezone.now().isoformat()
        payload = {
            'invoice_identifier': 'MANUALE-LOCAL-1',
            'amount': '12.00',
            'paid': False,
            'payment_date': now,
            'expire_date': now,
            'notes': 'Owned local record',
            'custom_accounts': str(self.cash.pk),
            'supplier_id': str(self.supplier.pk),
        }
        payload.update(kwargs)
        return payload

    def assert_payment_untouched(self, payment):
        stored = Payment._base_manager.get(pk=payment.pk)
        self.assertFalse(stored.paid)
        self.assertIsNone(stored.payment_date)

    def test_update_links_an_own_visible_payment_and_sets_it_paid(self):
        payment = self.payment(self.association)
        invoice = self.invoice()

        response = self.update(invoice, {'payment_id': str(payment.pk), 'paid': True})

        self.assertEqual(response.status_code, 200, response.content)
        invoice.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(invoice.payment_id, payment.pk)
        self.assertTrue(invoice.paid)
        self.assertTrue(payment.paid)
        self.assertIsNotNone(payment.payment_date)

    def test_update_with_a_payment_of_another_association_is_a_field_error(self):
        payment = self.payment(self.foreign, paid=True, payment_date=timezone.now())
        invoice = self.invoice()
        before_paid, before_date = payment.paid, payment.payment_date

        response = self.update(invoice, {'payment_id': str(payment.pk), 'paid': True})

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['payment_id'][0].code, 'does_not_exist')
        invoice.refresh_from_db()
        stored = Payment._base_manager.get(pk=payment.pk)
        self.assertIsNone(invoice.payment_id)
        self.assertEqual((stored.paid, stored.payment_date), (before_paid, before_date))

    def test_update_with_an_own_soft_deleted_payment_is_a_field_error(self):
        payment = self.payment(self.association)
        invoice = self.invoice()
        payment.deleted = True
        payment.save()

        response = self.update(invoice, {'payment_id': str(payment.pk), 'paid': True})

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['payment_id'][0].code, 'does_not_exist')
        invoice.refresh_from_db()
        self.assertIsNone(invoice.payment_id)
        self.assert_payment_untouched(payment)

    def test_update_with_a_payment_hidden_by_the_group_header_is_a_field_error(self):
        group = Group.objects.create(name='Own group', sport_association=self.association)
        payment = self.payment(self.association)
        invoice = self.invoice()

        response = self.update(
            invoice,
            {'payment_id': str(payment.pk), 'paid': True},
            HTTP_X_GROUP_ID=str(group.pk),
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['payment_id'][0].code, 'does_not_exist')
        invoice.refresh_from_db()
        self.assertIsNone(invoice.payment_id)
        self.assert_payment_untouched(payment)

    def test_update_with_an_unknown_or_malformed_payment_id_is_a_field_error(self):
        invoice = self.invoice()
        for value in (str(uuid.uuid4()), 'zzz'):
            with self.subTest(value=value):
                response = self.update(invoice, {'payment_id': value, 'paid': True})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.data['payment_id'][0].code, 'does_not_exist')
                invoice.refresh_from_db()
                self.assertIsNone(invoice.payment_id)
                self.assertFalse(invoice.paid)

    def test_update_resending_the_current_soft_deleted_payment_is_accepted(self):
        payment = self.payment(self.association)
        invoice = self.invoice(payment=payment)
        payment.deleted = True
        payment.save()

        response = self.update(invoice, {'payment_id': str(payment.pk), 'paid': True})

        self.assertEqual(response.status_code, 200, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_id, payment.pk)

    def test_add_with_a_payment_of_another_association_is_a_field_error(self):
        payment = self.payment(self.foreign, paid=True, payment_date=timezone.now())
        before_paid, before_date = payment.paid, payment.payment_date
        before_invoices = InvoiceSuppliers._base_manager.count()
        before_payments = Payment._base_manager.count()

        response = self.client.post(
            '/invoice-suppliers/add',
            self.add_payload(payment_id=str(payment.pk)),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['payment_id'][0].code, 'does_not_exist')
        self.assertEqual(InvoiceSuppliers._base_manager.count(), before_invoices)
        self.assertEqual(Payment._base_manager.count(), before_payments)
        stored = Payment._base_manager.get(pk=payment.pk)
        self.assertEqual((stored.paid, stored.payment_date), (before_paid, before_date))
