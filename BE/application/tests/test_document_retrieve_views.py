"""
Tests for document retrieval - auth token, cookie, share token paths.
"""
from unittest.mock import patch

from django.http import HttpResponse
from django.test import TestCase
from rest_framework.test import APIClient

from application.models.user_models import User
from application.models.invoices_models import Invoice
from application.services.jwt_token_service import JWTTokenService
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)
from docmanager.models import Document


class RetrieveDocumentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.document = Document.objects.create(filename='test.pdf')

    @patch('docmanager.views.document_view.PrintingService.download_file')
    def test_retrieve_document_accepts_bkn_auth_cookie(self, mock_download_file):
        mock_download_file.return_value = HttpResponse(b'pdf', content_type='application/pdf')
        access_token = JWTTokenService.generate_tokens_for_user(self.user)['access_token']
        self.client.cookies['BKN_AUTH'] = access_token
        response = self.client.get(f'/document/retrieve/{self.document.document_id}?download=true')
        self.assertEqual(response.status_code, 200)
        mock_download_file.assert_called_once()

    @patch('docmanager.views.document_view.PrintingService.download_file')
    def test_retrieve_document_accepts_legacy_query_jwt(self, mock_download_file):
        mock_download_file.return_value = HttpResponse(b'pdf', content_type='application/pdf')
        access_token = JWTTokenService.generate_tokens_for_user(self.user)['access_token']
        response = self.client.get(
            f'/document/retrieve/{self.document.document_id}?download=true&token={access_token}'
        )
        self.assertEqual(response.status_code, 200)
        mock_download_file.assert_called_once()

    @patch('docmanager.views.document_view.PrintingService.download_file')
    def test_retrieve_document_accepts_share_token_uuid(self, mock_download_file):
        mock_download_file.return_value = HttpResponse(b'pdf', content_type='application/pdf')
        response = self.client.get(
            f'/document/retrieve/{self.document.document_id}?download=true&token={self.document.token}'
        )
        self.assertEqual(response.status_code, 200)
        mock_download_file.assert_called_once()

    @patch('docmanager.views.document_view.PrintingService.download_file')
    def test_retrieve_document_accepts_invoice_id_when_authenticated(self, mock_download_file):
        mock_download_file.return_value = HttpResponse(b'pdf', content_type='application/pdf')
        access_token = JWTTokenService.generate_tokens_for_user(self.user)['access_token']
        invoice = Invoice.objects.create(
            sport_association=self.sport_association,
            document_pdf=self.document,
            membership_fee=0,
            activity_fee=0,
        )
        response = self.client.get(
            f'/document/retrieve/{invoice.invoice_id}?download=true&token={access_token}'
        )
        self.assertEqual(response.status_code, 200)
        mock_download_file.assert_called_once()


# Note: scoped download_token test omitted — target codebase does not
# implement create_document_download_token.
