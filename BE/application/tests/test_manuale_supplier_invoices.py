"""Prepared real API regressions; no renderer, provider or browser proof."""
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from application.models import User, Payment, PaymentCategory
from application.models.payment_models import SupplierAndCustomers
from application.models.balance_sheet_models import CustomAccounts
from application.models.invoices_models import InvoiceSuppliers
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import create_test_user, create_test_sport_association


class ManualSupplierInvoiceCreationTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.foreign_owner = create_test_user(role=User.ASSOCIATION)
        self.foreign = create_test_sport_association(user=self.foreign_owner)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.cash = CustomAccounts.objects.create(sport_association=self.association,
            name='Test cash', initial_balance=0, account_type=CustomAccounts.CASH, account_code='TEST')
        self.supplier = SupplierAndCustomers.objects.create(sport_association=self.association,
            name='Supplier owned by this association', type='supplier')
        # Test-owned database cleanup makes the prerequisite explicit.
        PaymentCategory.objects.filter(name__iexact='Pagamento Fornitore').delete()

    def category(self, association, **kwargs):
        return PaymentCategory.objects.create(sport_association=association,
            name='Pagamento Fornitore', expense=True, **kwargs)

    def create_invoice(self, **kwargs):
        now = timezone.now().isoformat()
        payload = {'invoice_identifier': 'MANUALE-LOCAL-1', 'amount': '12.00', 'paid': False,
            'payment_date': now, 'expire_date': now, 'notes': 'Owned local record',
            'custom_accounts': str(self.cash.pk), 'supplier_id': str(self.supplier.pk)}
        payload.update(kwargs)
        return self.client.post('/invoice-suppliers/add', payload, format='json')

    def test_own_category_is_used_instead_of_same_named_foreign_category(self):
        foreign_category = self.category(self.foreign)
        own_category = self.category(self.association)
        response = self.create_invoice()
        self.assertEqual(response.status_code, 200, response.content)
        invoice = InvoiceSuppliers.objects.get(invoice_identifier='MANUALE-LOCAL-1')
        self.assertEqual(invoice.sport_association, self.association)
        self.assertEqual(invoice.supplier, self.supplier)
        self.assertEqual(invoice.payment.payment_category, own_category)
        self.assertNotEqual(invoice.payment.payment_category, foreign_category)
        self.assertEqual(invoice.payment.sport_association, self.association)
        self.assertEqual(invoice.payment.custom_accounts, self.cash)
        self.assertEqual(invoice.payment.amount, Decimal('12.00'))
        self.assertTrue(invoice.payment.expense)
        self.assertFalse(invoice.payment.paid)

    def test_foreign_and_deleted_categories_are_not_fallbacks(self):
        self.category(self.foreign)
        self.category(self.association, deleted=True)
        before = Payment._base_manager.count()
        response = self.create_invoice()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['error'], 'Causale Pagamento Fornitore non disponibile.')
        self.assertFalse(InvoiceSuppliers.objects.filter(invoice_identifier='MANUALE-LOCAL-1').exists())
        self.assertEqual(Payment._base_manager.count(), before)

    def test_shared_category_and_missing_payment_date_use_local_creation_time(self):
        shared_category = self.category(None)
        response = self.create_invoice(payment_date=None)
        self.assertEqual(response.status_code, 200, response.content)
        invoice = InvoiceSuppliers.objects.get(invoice_identifier='MANUALE-LOCAL-1')
        self.assertEqual(invoice.payment.payment_category, shared_category)
        self.assertIsNotNone(invoice.payment_date)
        self.assertFalse(invoice.payment.paid)
        self.assertIsNone(invoice.payment.payment_date)

    def test_foreign_supplier_is_rejected_without_creating_invoice_or_payment(self):
        self.category(self.association)
        foreign_supplier = SupplierAndCustomers.objects.create(sport_association=self.foreign,
            name='Foreign supplier', type='supplier')
        before = Payment._base_manager.count()
        response = self.create_invoice(supplier_id=str(foreign_supplier.pk))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data['error'], 'Fornitore non trovato.')
        self.assertFalse(InvoiceSuppliers.objects.filter(invoice_identifier='MANUALE-LOCAL-1').exists())
        self.assertEqual(Payment._base_manager.count(), before)
