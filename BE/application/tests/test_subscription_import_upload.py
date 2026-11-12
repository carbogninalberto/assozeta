"""The member import wizard (subscription/import/upload) against the real view, database and temporary file storage."""
import io
import os
import tempfile
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.test import override_settings
from django.utils import timezone
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

    def stored_files(self):
        return sorted(os.path.join(folder, name)[len(self.storage.location) + 1:]
                      for folder, _, names in os.walk(self.storage.location) for name in names)

    def drafts(self):
        return {draft.data['associate']['first_name']: draft.data for draft in AssociateImportDraft.objects.all()}

    def test_failed_import_answers_an_error_and_keeps_nothing(self):
        uploaded = self.upload('soci.xlsx', workbook([
            ['Nome', 'Scadenza'], ['Mario', '31/12/2026'], ['Anna', '15/01/2027']]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'scadenza certificato medico': 'Scadenza',
                                                             'tessera oro': 'Nome'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'msg': 'Importazione non riuscita. Controlla i dati e riprova.'})
        self.assertFalse(AssociateImportDraft.objects.exists())
        self.assertFalse(MedicalCertificate.objects.exists())
        self.assertFalse(AssociateImportDraftStatus.objects.exists())
        # The upload stays for another attempt.
        self.assertTrue(Document.objects.filter(document_id=uploaded['document_id']).exists())
        self.assertEqual(len(self.stored_files()), 1)

    def test_date_cell_of_a_text_field_is_stored_as_text(self):
        uploaded = self.upload('soci.xlsx', workbook([
            ['Nome', 'Indirizzo', 'CAP'], ['Mario', datetime(2026, 1, 2), datetime(2026, 3, 4)], ['Anna', 'Via Po 3', '10100']]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'indirizzo': 'Indirizzo', 'cap': 'CAP'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.drafts(), {
            'Mario': {'associate': {'first_name': 'Mario', 'address': '02/01/2026', 'address_cap': '04/03/2026'}, 'associate_tutor': {}},
            'Anna': {'associate': {'first_name': 'Anna', 'address': 'Via Po 3', 'address_cap': '10100'}, 'associate_tutor': {}},
        })

    def test_codes_and_numbers_are_read_as_text(self):
        uploaded = self.upload('soci.xlsx', workbook([
            ['Nome', 'CAP', 'Telefono', 'Tessera', 'Quota'],
            ['Mario', '00100', '0612345678', 7, 10],
            ['Anna', 20100, 3331234567, '0042', 12.5],
            ['Luca', None, None, None, None]]))

        response = self.run_import(uploaded['document_id'], {
            'nome': 'Nome', 'cap': 'CAP', 'telefono': 'Telefono', 'numero tessera': 'Tessera', 'cognome': 'Quota'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.drafts(), {
            'Mario': {'associate': {'first_name': 'Mario', 'address_cap': '00100', 'phone': '0612345678', 'subscription_number': '7',
                                    'last_name': 10.0}, 'associate_tutor': {}},
            'Anna': {'associate': {'first_name': 'Anna', 'address_cap': '20100', 'phone': '3331234567', 'subscription_number': '0042',
                                   'last_name': 12.5}, 'associate_tutor': {}},
            'Luca': {'associate': {'first_name': 'Luca'}, 'associate_tutor': {}},
        })

    def test_codes_of_a_csv_are_read_as_text(self):
        uploaded = self.upload('soci.csv', b'Nome;CAP;Telefono\nMario;00100;0612345678\nAnna;;\n', 'text/csv')

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'cap': 'CAP', 'telefono': 'Telefono', 'email': 'Assente'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.drafts(), {
            'Mario': {'associate': {'first_name': 'Mario', 'address_cap': '00100', 'phone': '0612345678'}, 'associate_tutor': {}},
            'Anna': {'associate': {'first_name': 'Anna'}, 'associate_tutor': {}},
        })

    def test_sheet_of_whole_numbers_is_imported(self):
        uploaded = self.upload('numeri.xlsx', workbook([['Nome', 'Cognome'], [1, 2], [3, 4]]))

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome', 'cognome': 'Cognome'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertCountEqual([draft.data['associate'] for draft in AssociateImportDraft.objects.all()],
                              [{'first_name': 1, 'last_name': 2}, {'first_name': 3, 'last_name': 4}])

    def test_text_dates_have_the_day_first(self):
        uploaded = self.upload('date.xlsx', workbook([
            ['Nome', 'Nascita', 'Tesseramento', 'Scadenza tesseramento', 'Certificato'],
            ['Mario', '03/02/1990', '1/9/2026', '2027-08-31', '05/03/2027'],
            ['Anna', '1975-12-25', '25/12/2026', 'domani', '2027-03-05 00:00:00'],
            ['Luca', datetime(1980, 2, 1), '12/25/2026', None, None]]))

        response = self.run_import(uploaded['document_id'], {
            'nome': 'Nome', 'data di nascita': 'Nascita', 'data tesseramento': 'Tesseramento',
            'data scadenza tesseramento': 'Scadenza tesseramento', 'scadenza certificato medico': 'Certificato',
            'data di nascita tutore': 'Nascita'})

        self.assertEqual(response.status_code, 200, response.content)
        drafts = self.drafts()
        self.assertEqual({name: data['associate'] for name, data in drafts.items()}, {
            'Mario': {'first_name': 'Mario', 'born_date': '03/02/1990', 'membership_start_date': '01/09/2026',
                      'membership_end_date': '31/08/2027', 'certificate_expiring_date': '05/03/2027'},
            'Anna': {'first_name': 'Anna', 'born_date': '25/12/1975', 'membership_start_date': '25/12/2026',
                     'certificate_expiring_date': '2027-03-05 00:00:00'},
            # A first number that is no month is read as the day of a month-first date.
            'Luca': {'first_name': 'Luca', 'born_date': '01/02/1980', 'membership_start_date': '25/12/2026'},
        })
        self.assertEqual(drafts['Mario']['associate_tutor'], {'born_date': '03/02/1990'})
        self.assertEqual(drafts['Mario']['medical_certificate']['certificate_expring_date'], '05/03/2027')
        self.assertEqual(drafts['Anna']['medical_certificate']['certificate_expring_date'], '05/03/2027')
        self.assertEqual(sorted(MedicalCertificate.objects.values_list('expiration_date', flat=True)), [date(2027, 3, 5)] * 2)

    @override_settings(SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES=64)
    def test_upload_above_the_limit_is_refused(self):
        response = self.client.post(
            ENDPOINT, {'associates_file': SimpleUploadedFile('soci.csv', b'Nome;Cognome\n' + b'Mario;Rossi\n' * 5, content_type='text/csv')},
            format='multipart')

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {'msg': 'Il file supera la dimensione massima consentita (64 byte).'})
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.stored_files(), [])
        # A file of exactly the limit is accepted.
        self.upload('soci.csv', b'Nome;Cognome\n' + b'x' * 51, 'text/csv')

    def test_limit_message_names_megabytes(self):
        from application.services.subscription_service import SubscriptionImportService
        self.assertEqual(SubscriptionImportService.upload_limit(), 20 * 1024 * 1024)
        self.assertEqual(SubscriptionImportService.too_large_message(20 * 1024 * 1024),
                         'Il file supera la dimensione massima consentita (20 MB).')

    def test_import_removes_its_upload(self):
        uploaded = self.upload('soci.xlsx', workbook([['Nome'], ['Mario']]))
        self.assertEqual(len(self.stored_files()), 1)

        response = self.run_import(uploaded['document_id'], {'nome': 'Nome'})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Document.objects.filter(document_id=uploaded['document_id']).exists())
        self.assertEqual(self.stored_files(), [])

    def test_upload_removes_the_uploads_past_their_retention(self):
        from application.services.subscription_service import SubscriptionImportService
        other = create_test_sport_association(user=create_test_user(role=User.ASSOCIATION))

        def stored(association, age_hours):
            document = Document.objects.create(filename='vecchio.csv', creation_date=timezone.now() - timedelta(hours=age_hours))
            self.storage.save(SubscriptionImportService.import_file_path(document, association), io.BytesIO(b'Nome\nMario\n'))
            return document

        expired, recent, foreign = stored(self.sport_association, 25), stored(self.sport_association, 23), stored(other, 48)
        elsewhere = Document.objects.create(filename='altro.pdf', creation_date=timezone.now() - timedelta(days=30))

        uploaded = self.upload('soci.csv', b'Nome;Cognome\nMario;Rossi\n', 'text/csv')

        self.assertCountEqual([str(key) for key in Document.objects.values_list('document_id', flat=True)],
                              [str(recent.document_id), str(foreign.document_id), str(elsewhere.document_id), uploaded['document_id']])
        files = self.stored_files()
        self.assertEqual(len(files), 3)
        self.assertFalse(any(str(expired.document_id) in name for name in files))
