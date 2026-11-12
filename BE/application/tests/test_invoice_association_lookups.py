"""Invoice lookups stay inside the caller's association.

A selected tutor or a customer invoice number that belongs to another
association behaves like an unknown value: the tutor of the invoice is left
untouched and the customer invoice number does not block the add.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from application.models import User
from application.models.invoices_models import CustomerInvoice
from application.models.user_models import Associate, Group
from application.tests.fixtures.factories import (
    create_test_sport_association, create_test_user,
)
from application.tests.test_invoice_views import create_test_invoice


class InvoiceAssociationLookupTests(TestCase):
    def setUp(self):
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.foreign_owner = create_test_user(role=User.ASSOCIATION)
        self.foreign_association = create_test_sport_association(user=self.foreign_owner)

        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

        self.own_tutor = Associate.objects.create(
            user=self.owner, sport_association=self.association,
            first_name='Own', last_name='Tutor',
        )
        self.other_own_tutor = Associate.objects.create(
            user=self.owner, sport_association=self.association,
            first_name='Second', last_name='Tutor',
        )
        self.foreign_tutor = Associate.objects.create(
            user=self.foreign_owner, sport_association=self.foreign_association,
            first_name='Foreign', last_name='Tutor',
        )

    def invoice_numbers(self):
        return (
            CustomerInvoice.objects.filter(sport_association=self.association).count(),
            CustomerInvoice.objects.filter(sport_association=self.foreign_association).count(),
        )

    def customer_invoice_body(self, number, fiscal_year, prefix='FE'):
        return {
            'number': number,
            'fiscal_year': fiscal_year,
            'prefix': prefix,
            'id_transmitter': 'X',
        }

    @patch('application.views.invoice_views.print_document_invoice')
    def test_update_with_another_association_associate_keeps_the_tutor(self, mock_print):
        invoice = create_test_invoice(
            self.association, number=1, selected_tutor=self.own_tutor,
        )

        response = self.client.patch(
            f'/invoice/{invoice.pk}/update',
            {'number': 5, 'selected_tutor': str(self.foreign_tutor.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 5)
        self.assertEqual(invoice.selected_tutor, self.own_tutor)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_update_with_an_own_associate_sets_the_tutor(self, mock_print):
        invoice = create_test_invoice(
            self.association, number=1, selected_tutor=self.own_tutor,
        )

        response = self.client.patch(
            f'/invoice/{invoice.pk}/update',
            {'number': 2, 'selected_tutor': str(self.other_own_tutor.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 2)
        self.assertEqual(invoice.selected_tutor, self.other_own_tutor)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_group_update_with_another_association_associate_keeps_the_tutor(self, mock_print):
        group = Group.objects.create(
            name='Shared group', sport_association=self.association,
        )
        self.foreign_tutor.group = group
        self.foreign_tutor.save(update_fields=['group'])
        invoice = create_test_invoice(
            self.association, number=1, selected_tutor=self.own_tutor, group=group,
        )

        response = self.client.patch(
            f'/invoice/{invoice.pk}/update',
            {'number': 5, 'selected_tutor': str(self.foreign_tutor.pk)},
            format='json',
            HTTP_X_GROUP_ID=str(group.pk),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 5)
        self.assertEqual(invoice.selected_tutor, self.own_tutor)

    @patch('application.views.invoice_views.print_document_invoice')
    def test_group_update_with_an_own_associate_in_the_group_sets_the_tutor(self, mock_print):
        group = Group.objects.create(
            name='Own group', sport_association=self.association,
        )
        own_group_tutor = Associate.objects.create(
            user=self.owner, sport_association=self.association, group=group,
            first_name='Grouped', last_name='Tutor',
        )
        invoice = create_test_invoice(
            self.association, number=1, selected_tutor=self.own_tutor, group=group,
        )

        response = self.client.patch(
            f'/invoice/{invoice.pk}/update',
            {'number': 5, 'selected_tutor': str(own_group_tutor.pk)},
            format='json',
            HTTP_X_GROUP_ID=str(group.pk),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, 5)
        self.assertEqual(invoice.selected_tutor, own_group_tutor)

    @patch('application.views.invoice_views.print_document_customer_invoice')
    def test_customer_add_ignores_numbers_of_another_association(self, mock_print):
        foreign_invoice = CustomerInvoice.objects.create(
            sport_association=self.foreign_association, number=3,
            fiscal_year=2026, prefix='FE',
        )
        before = self.invoice_numbers()

        response = self.client.post(
            '/invoice-customers/add',
            self.customer_invoice_body(3, 2026),
            format='json',
        )

        # XML generation is not available in this environment, so the view
        # still answers 200 with an error body once the row is created.
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(
            response.json(), {'error': 'Errore durante la generazione del file XML.'},
        )
        self.assertEqual(self.invoice_numbers(), (before[0] + 1, before[1]))
        foreign_invoice.refresh_from_db()
        self.assertEqual(
            (foreign_invoice.number, foreign_invoice.fiscal_year, foreign_invoice.prefix),
            (3, 2026, 'FE'),
        )
        self.assertTrue(CustomerInvoice.objects.filter(
            sport_association=self.association, number=3, fiscal_year=2026,
        ).exists())

    @patch('application.views.invoice_views.print_document_customer_invoice')
    def test_customer_add_rejects_a_number_the_association_already_uses(self, mock_print):
        CustomerInvoice.objects.create(
            sport_association=self.association, number=3,
            fiscal_year=2026, prefix='Z',
        )
        before = self.invoice_numbers()

        response = self.client.post(
            '/invoice-customers/add',
            self.customer_invoice_body(3, 2026),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(
            response.json()['non_field_errors'], ['Invoice already exists!'],
        )
        self.assertEqual(self.invoice_numbers(), before)

    @patch('application.views.invoice_views.print_document_customer_invoice')
    def test_customer_add_ignores_a_null_fiscal_year_of_another_association(self, mock_print):
        CustomerInvoice.objects.create(
            sport_association=self.foreign_association, number=4,
            fiscal_year=None, prefix='FE',
        )
        before = self.invoice_numbers()

        response = self.client.post(
            '/invoice-customers/add',
            self.customer_invoice_body(4, None),
            format='json',
        )

        # XML generation is not available in this environment, so the view
        # still answers 200 with an error body once the row is created.
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(
            response.json(), {'error': 'Errore durante la generazione del file XML.'},
        )
        self.assertEqual(self.invoice_numbers(), (before[0] + 1, before[1]))
        self.assertTrue(CustomerInvoice.objects.filter(
            sport_association=self.association, number=4, fiscal_year__isnull=True,
        ).exists())
