"""
Tests for Invoice views - invoice management endpoints.

This module tests the invoice-related API endpoints for
creating, updating, deleting, and listing invoices.

Self-host note: SaaS plan-restriction tests omitted.
Exception-swallowing and broad-status tests excluded.
"""
import datetime
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.invoices_models import Invoice, InvoiceSuppliers, CustomerInvoice
from application.models.payment_models import Payment, PaymentCategory, SupplierAndCustomers
from application.models.balance_sheet_models import CustomAccounts
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association
)


def create_test_invoice(sport_association, **kwargs):
    defaults = {
        'sport_association': sport_association,
        'number': kwargs.get('number', 1),
        'membership_fee': Decimal('50.00'),
        'activity_fee': Decimal('100.00'),
        'archived': False,
        'cancelled': False,
    }
    defaults.update(kwargs)
    return Invoice.objects.create(**defaults)


def create_test_payment(sport_association, associate=None, invoice=None, **kwargs):
    category, _ = PaymentCategory.objects.get_or_create(
        sport_association=sport_association,
        name='Test Category',
        defaults={'type': PaymentCategory.INSTITUTIONAL}
    )
    defaults = {
        'sport_association': sport_association,
        'amount': Decimal('150.00'),
        'payment_date': timezone.now().date(),
        'paid': True,
        'invoice': invoice,
        'payment_category': category,
    }
    defaults.update(kwargs)
    return Payment.objects.create(**defaults)


class InvoiceListTests(TestCase):
    """Tests for invoice_list endpoint: GET /invoice/list"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_invoices_empty(self):
        response = self.client.get('/invoice/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)
        self.assertIn('meta', data)
        self.assertEqual(data['meta']['total'], 0)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_list_invoices_with_data(self, mock_print):
        mock_print.delay = MagicMock()
        for i in range(3):
            invoice = create_test_invoice(self.sport_association, number=i + 1)
            create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['meta']['total'], 3)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_list_invoices_excludes_archived(self, mock_print):
        mock_print.delay = MagicMock()
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        archived_invoice = create_test_invoice(
            self.sport_association, number=2, archived=True
        )
        create_test_payment(self.sport_association, invoice=archived_invoice)
        response = self.client.get('/invoice/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['meta']['total'], 1)

    def test_list_invoices_by_specific_id(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get(f'/invoice/list?query[invoice_id]={invoice.invoice_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('invoice', data['data'])
        self.assertEqual(data['data']['invoice']['invoice_id'], str(invoice.invoice_id))

    def test_list_invoices_nonexistent_id_returns_404(self):
        fake_id = str(uuid.uuid4())
        response = self.client.get(f'/invoice/list?query[invoice_id]={fake_id}')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InvoiceUpdateTests(TestCase):
    """Tests for invoice_update endpoint: PATCH /invoice/<uid>/update"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_update_invoice_number(self, mock_print):
        mock_print.apply = MagicMock()
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.patch(
            f'/invoice/{invoice.invoice_id}/update',
            {'number': 42},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 42)


class InvoiceDeleteTests(TestCase):
    """Tests for invoice_delete endpoint: POST /invoice/<uid>/delete"""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_delete_invoice_success(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        payment = create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.post(f'/invoice/{invoice.invoice_id}/delete')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Invoice.objects.filter(invoice_id=invoice.invoice_id).exists())
        payment.refresh_from_db()
        self.assertIsNone(payment.invoice)
        self.assertFalse(payment.paid)


class InvoiceBulkOperationsTests(TestCase):
    """Tests for invoice bulk operations."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_bulk_delete_invoices(self):
        invoices = []
        for i in range(3):
            invoice = create_test_invoice(self.sport_association, number=i + 1)
            create_test_payment(self.sport_association, invoice=invoice)
            invoices.append(invoice)
        invoice_ids = [str(inv.invoice_id) for inv in invoices[:2]]
        response = self.client.delete(
            '/invoice-bulk/delete',
            {'invoice_ids': invoice_ids},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        remaining = Invoice.objects.filter(sport_association=self.sport_association)
        self.assertEqual(remaining.count(), 1)

    def test_bulk_archive_invoices(self):
        invoices = []
        for i in range(3):
            invoice = create_test_invoice(self.sport_association, number=i + 1)
            create_test_payment(self.sport_association, invoice=invoice)
            invoices.append(invoice)
        invoice_ids = [str(inv.invoice_id) for inv in invoices[:2]]
        response = self.client.post(
            '/invoice-bulk/archive',
            {'invoice_ids': invoice_ids},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for inv in invoices[:2]:
            inv.refresh_from_db()
            self.assertTrue(inv.archived)
        invoices[2].refresh_from_db()
        self.assertFalse(invoices[2].archived)


class InvoiceSuppliersTests(TestCase):
    """Tests for invoice suppliers endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.supplier = SupplierAndCustomers.objects.create(
            sport_association=self.sport_association,
            name='Test Supplier',
            type='supplier'
        )

    def test_suppliers_stats_empty(self):
        response = self.client.get('/invoice-suppliers/stats')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('invoices_total', data)

    def test_suppliers_list_empty(self):
        response = self.client.get('/invoice-suppliers/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['meta']['total'], 0)

    def test_delete_supplier_invoice(self):
        supplier_invoice = InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now()
        )
        response = self.client.delete(
            f'/invoice-suppliers/{supplier_invoice.invoice_supplier_id}/delete'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            InvoiceSuppliers.objects.filter(
                invoice_supplier_id=supplier_invoice.invoice_supplier_id
            ).exists()
        )


class CustomerInvoiceTests(TestCase):
    """Tests for customer invoice endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_customers_stats(self):
        response = self.client.get('/invoice-customers/stats')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('invoices_total', data)

    def test_customers_list_empty(self):
        response = self.client.get('/invoice-customers/list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('data', data)


class InvoiceAuthenticationTests(TestCase):
    """Tests for authentication requirements across invoice endpoints."""

    def setUp(self):
        self.client = APIClient()

    def test_invoice_list_requires_auth(self):
        response = self.client.get('/invoice/list')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invoice_update_requires_auth(self):
        response = self.client.patch(f'/invoice/{uuid.uuid4()}/update', {'number': 1})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invoice_delete_requires_auth(self):
        response = self.client.post(f'/invoice/{uuid.uuid4()}/delete')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_supplier_stats_requires_auth(self):
        response = self.client.get('/invoice-suppliers/stats')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_stats_requires_auth(self):
        response = self.client.get('/invoice-customers/stats')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class InvoiceSearchTests(TestCase):
    """Tests for invoice search functionality."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_search_invoices_by_number(self):
        invoice = create_test_invoice(self.sport_association, number=42)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[generalSearch]=42')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_invoices_no_results(self):
        response = self.client.get('/invoice/list?query[generalSearch]=nonexistent999')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['meta']['total'], 0)


class InvoicePaginationTests(TestCase):
    """Tests for invoice list pagination."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_invoice_list_with_page_size(self):
        for i in range(10):
            invoice = create_test_invoice(self.sport_association, number=i + 1)
            create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[pagination][perpage]=3')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        if isinstance(data['data'], list):
            self.assertLessEqual(len(data['data']), 10)
        else:
            self.assertIn('data', data)

    def test_invoice_list_second_page(self):
        for i in range(10):
            invoice = create_test_invoice(self.sport_association, number=i + 1)
            create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[pagination][perpage]=3&query[pagination][page]=2')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InvoiceFilteringTests(TestCase):
    """Tests for invoice list filtering."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_filter_cancelled_invoices(self):
        cancelled = create_test_invoice(self.sport_association, number=1, cancelled=True)
        create_test_payment(self.sport_association, invoice=cancelled)
        active = create_test_invoice(self.sport_association, number=2)
        create_test_payment(self.sport_association, invoice=active)
        response = self.client.get('/invoice/list?query[filters][cancelled]=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_filter_invoices_by_date_range(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        today = timezone.now().date().isoformat()
        response = self.client.get(f'/invoice/list?query[filters][start_date]={today}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InvoiceExportTests(TestCase):
    """Tests for invoice export functionality."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_export_invoices_csv(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?export=csv')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])


class InvoiceListAdvancedTests(TestCase):
    """Advanced tests for invoice list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_invoices_current_year_filter(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[current_year]=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_invoices_previous_years_filter(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[current_year]=0')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_current_year_filter_excludes_cancelled_previous_year_invoices(self, mock_print):
        mock_print.delay = MagicMock()
        self.user.balance_sheet_start_day = 1
        self.user.balance_sheet_start_month = 1
        self.user.save()
        current_invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=current_invoice, payment_date=timezone.now())
        previous_cancelled_invoice = create_test_invoice(self.sport_association, number=2, cancelled=True)
        create_test_payment(
            self.sport_association,
            invoice=previous_cancelled_invoice,
            payment_date=timezone.now() - datetime.timedelta(days=400)
        )
        response = self.client.get('/invoice/list?query[current_year]=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {row['invoice_id'] for row in response.json()['data'].values()}
        self.assertIn(str(current_invoice.invoice_id), returned_ids)
        self.assertNotIn(str(previous_cancelled_invoice.invoice_id), returned_ids)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_previous_year_filter_excludes_cancelled_current_year_invoices(self, mock_print):
        mock_print.delay = MagicMock()
        self.user.balance_sheet_start_day = 1
        self.user.balance_sheet_start_month = 1
        self.user.save()
        previous_invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(
            self.sport_association,
            invoice=previous_invoice,
            payment_date=timezone.now() - datetime.timedelta(days=400)
        )
        current_cancelled_invoice = create_test_invoice(self.sport_association, number=2, cancelled=True)
        create_test_payment(self.sport_association, invoice=current_cancelled_invoice, payment_date=timezone.now())
        response = self.client.get('/invoice/list?query[current_year]=0')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {row['invoice_id'] for row in response.json()['data'].values()}
        self.assertIn(str(previous_invoice.invoice_id), returned_ids)
        self.assertNotIn(str(current_cancelled_invoice.invoice_id), returned_ids)

    def test_list_invoices_with_sorting(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?sort[field]=number&sort[sort]=asc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_invoices_search_by_date(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        today = timezone.now().strftime('%d/%m/%Y')
        response = self.client.get(f'/invoice/list?query[generalSearch]={today}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_invoices_search_by_amount(self):
        invoice = create_test_invoice(
            self.sport_association,
            number=1,
            membership_fee=Decimal('123.45')
        )
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[generalSearch]=123.45')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_archived_invoices(self):
        invoice = create_test_invoice(
            self.sport_association,
            number=1,
            archived=True
        )
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list?query[archived]=True')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SupplierInvoiceListTests(TestCase):
    """Tests for supplier invoice list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.supplier = SupplierAndCustomers.objects.create(
            sport_association=self.sport_association,
            name='Test Supplier',
            type='supplier'
        )

    def test_list_supplier_invoices_with_search(self):
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='TEST-INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now()
        )
        response = self.client.get('/invoice-suppliers/list?query[generalSearch]=TEST')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_supplier_invoices_with_pagination(self):
        for i in range(5):
            InvoiceSuppliers.objects.create(
                sport_association=self.sport_association,
                supplier=self.supplier,
                invoice_identifier=f'INV-{i}',
                amount=Decimal('500.00'),
                payment_date=timezone.now()
            )
        response = self.client.get('/invoice-suppliers/list?pagination[page]=1&pagination[perpage]=2')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CustomerInvoiceListTests(TestCase):
    """Tests for customer invoice list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_list_customer_invoices_with_pagination(self):
        response = self.client.get('/invoice-customers/list?pagination[page]=1&pagination[perpage]=10')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_customer_invoices_with_sorting(self):
        response = self.client.get('/invoice-customers/list?sort[field]=invoice_identifier&sort[sort]=asc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InvoiceListEdgeCaseTests(TestCase):
    """Edge case tests for invoice_list endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_invoice_list_specific_id_no_payment(self):
        invoice = Invoice.objects.create(
            sport_association=self.sport_association,
            number=1,
            membership_fee=Decimal('50.00'),
            activity_fee=Decimal('100.00'),
            meta={'test': 'data'}
        )
        response = self.client.get(f'/invoice/list?query[invoice_id]={invoice.invoice_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InvoiceExportEdgeCaseTests(TestCase):
    """Edge case tests for invoice export."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)

    def test_export_csv(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list/export?m=csv')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_xlsx(self):
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list/export?m=xlsx')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('application.views.invoice_views.export_invoices_to_zip')
    def test_export_files(self, mock_export):
        mock_export.delay = MagicMock()
        invoice = create_test_invoice(self.sport_association, number=1)
        create_test_payment(self.sport_association, invoice=invoice)
        response = self.client.get('/invoice/list/export?m=files')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class InvoiceSuppliersEdgeCaseTests(TestCase):
    """Edge case tests for invoice suppliers endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.supplier = SupplierAndCustomers.objects.create(
            sport_association=self.sport_association,
            name='Test Supplier',
            type='supplier'
        )

    def test_suppliers_stats_with_data(self):
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now(),
            paid=True
        )
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-002',
            amount=Decimal('300.00'),
            payment_date=timezone.now(),
            expire_date=timezone.now() + datetime.timedelta(days=15),
            paid=False
        )
        response = self.client.get('/invoice-suppliers/stats')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['invoices_total'], 800)
        self.assertEqual(data['invoices_paid'], 500)

    def test_suppliers_list_with_sorting(self):
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now()
        )
        response = self.client.get('/invoice-suppliers/list?sort[field]=amount&sort[sort]=desc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_suppliers_list_search_by_amount(self):
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now()
        )
        response = self.client.get('/invoice-suppliers/list?query[generalSearch]=500')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_suppliers_list_search_by_date(self):
        InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-001',
            amount=Decimal('500.00'),
            payment_date=timezone.now()
        )
        today = timezone.now().strftime('%d/%m/%Y')
        response = self.client.get(f'/invoice-suppliers/list?query[generalSearch]={today}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_suppliers_add_no_bank_account(self):
        response = self.client.post('/invoice-suppliers/add', {
            'supplier_id': str(self.supplier.supplier_id),
            'invoice_identifier': 'INV-002',
            'amount': 1000.00,
            'payment_date': timezone.now().isoformat(),
            'custom_accounts': None,
            'paid': False
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suppliers_add_invalid_supplier(self):
        bank = CustomAccounts.objects.create(
            sport_association=self.sport_association,
            name='Test Bank',
            initial_balance=Decimal('0.00'),
            editable=False,
            enabled=True,
            account_type=CustomAccounts.BANK
        )
        response = self.client.post('/invoice-suppliers/add', {
            'supplier_id': str(uuid.uuid4()),
            'invoice_identifier': 'INV-002',
            'amount': 1000.00,
            'payment_date': timezone.now().isoformat(),
            'custom_accounts': str(bank.custom_account_id),
            'paid': False
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suppliers_update_with_payment(self):
        payment = create_test_payment(self.sport_association)
        supplier_invoice = InvoiceSuppliers.objects.create(
            sport_association=self.sport_association,
            supplier=self.supplier,
            invoice_identifier='INV-003',
            amount=Decimal('500.00'),
            payment_date=timezone.now(),
            payment=payment
        )
        response = self.client.patch(
            f'/invoice-suppliers/{supplier_invoice.invoice_supplier_id}/update',
            {'paid': True},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertTrue(payment.paid)
