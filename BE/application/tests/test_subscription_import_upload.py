"""The member import wizard (subscription/import/upload) against the real view, database and temporary file storage."""
import io
import os
import tempfile
from datetime import date, datetime
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from application.models import AssociateImportDraft, MedicalCertificate, User
from application.models.subscriptions_models import AssociateImportDraftStatus
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from application.utils.api_utils import SubscriptionMaps
from docmanager.models import Document

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
ENDPOINT = '/subscription/import/upload'


def workbook(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    content = io.BytesIO()
    book.save(content)
    return content.getvalue()


class SubscriptionImportUploadTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = FileSystemStorage(location=temporary.name)
        for module in ('application.services.subscription_service', 'application.views.subscriptions_views'):
            for name, value in (('default_storage', self.storage), ('STORAGE_DIR', 'import-test')):
                replacement = patch(f'{module}.{name}', value)
                replacement.start()
                self.addCleanup(replacement.stop)

    def upload(self, filename, content, content_type=XLSX, client=None):
        response = (client or self.client).post(
            ENDPOINT, {'associates_file': SimpleUploadedFile(filename, content, content_type=content_type)}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def run_import(self, document_id, mapping, client=None):
        return (client or self.client).post(ENDPOINT, {'document_id': str(document_id), 'map': mapping}, format='json')

    def test_upload_map_names_the_columns_of_this_upload(self):
        first = self.upload('soci.csv', b'Nome;Cognome\nMario;Rossi\n', 'text/csv')
        second = self.upload('contatti.csv', b'Email;Telefono\nmario@example.test;333\n', 'text/csv')

        self.assertEqual({key: value for key, value in first['map'].items() if value}, {'nome': 'Nome', 'cognome': 'Cognome'})
        self.assertEqual({key: value for key, value in second['map'].items() if value}, {'email': 'Email', 'telefono': 'Telefono'})
        self.assertEqual(list(second['map']), list(SubscriptionMaps.MAP))
        self.assertTrue(all(value is None for value in SubscriptionMaps.MAP.values()))

    def test_import_map_is_empty_after_an_upload(self):
        uploaded = self.upload('soci.xlsx', workbook([['Nome', 'Cognome'], ['Mario', 'Rossi']]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'cognome': 'Cognome'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['columns'], ['Nome', 'Cognome'])
        self.assertTrue(all(value is None for value in response.json()['map'].values()))

    def test_import_of_an_upload_creates_the_drafts(self):
        uploaded = self.upload('soci.xlsx', workbook([['Nome', 'Cognome'], ['Mario', 'Rossi'], ['Anna', 'Bianchi']]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'cognome': 'Cognome'})

        self.assertEqual(response.status_code, 200)
        drafts = AssociateImportDraft.objects.filter(sport_association=self.sport_association)
        self.assertCountEqual([draft.data['associate'] for draft in drafts],
                              [{'first_name': 'Mario', 'last_name': 'Rossi'}, {'first_name': 'Anna', 'last_name': 'Bianchi'}])

    def test_import_needs_a_document_uploaded_for_the_import(self):
        document = Document.objects.create(filename='elenco.xlsx')
        self.storage.save(os.path.join('import-test', str(document.creation_date.timestamp()), str(document.document_id),
                                       document.filename), io.BytesIO(workbook([['Nome'], ['Mario']])))

        with self.assertRaisesMessage(ValueError, 'No document found'):
            self.run_import(document.document_id, {'nome': 'Nome'})

        self.assertFalse(AssociateImportDraft.objects.exists())
        self.assertFalse(AssociateImportDraftStatus.objects.exists())

    def test_import_needs_an_upload_of_the_same_association(self):
        other = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=other)
        other_client = self.create_authenticated_client(other)
        uploaded = self.upload('soci.xlsx', workbook([['Nome'], ['Mario']]), client=other_client)

        with self.assertRaisesMessage(ValueError, 'No document found'):
            self.run_import(uploaded['document_id'], {'nome': 'Nome'})
        self.assertFalse(AssociateImportDraft.objects.exists())

        self.assertEqual(self.run_import(uploaded['document_id'], {'nome': 'Nome'}, client=other_client).status_code, 200)
        self.assertEqual(AssociateImportDraft.objects.filter(sport_association__user=other).count(), 1)
        self.assertFalse(AssociateImportDraft.objects.filter(sport_association=self.sport_association).exists())

    def test_import_stores_a_date_cell_of_the_certificate_column(self):
        uploaded = self.upload('certificati.xlsx', workbook([
            ['Nome', 'Scadenza certificato medico'], ['Mario', datetime(2027, 3, 5)], ['Anna', None]]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'scadenza certificato medico': 'Scadenza certificato medico'})

        self.assertEqual(response.status_code, 200)
        certificate = MedicalCertificate.objects.get()
        self.assertEqual(certificate.expiration_date, date(2027, 3, 5))
        drafts = {draft.data['associate']['first_name']: draft.data for draft in AssociateImportDraft.objects.all()}
        self.assertEqual(drafts, {
            'Mario': {'associate': {'first_name': 'Mario', 'certificate_expiring_date': '05/03/2027'}, 'associate_tutor': {},
                      'medical_certificate': {'medical_id': str(certificate.medical_id), 'filename': None,
                                              'certificate_expring_date': '05/03/2027'}},
            'Anna': {'associate': {'first_name': 'Anna'}, 'associate_tutor': {}},
        })
        self.assertFalse(AssociateImportDraftStatus.objects.exists())
