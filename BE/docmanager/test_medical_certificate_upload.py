"""The certificate upload of the registration forms (document/medical/certificate/): accepted files and the hourly rate."""
import tempfile
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from application.models import MedicalCertificate
from application.tests.base import BaseAPITestCase
from docmanager.models import Document

ENDPOINT = '/document/medical/certificate/'


class MedicalCertificateUploadTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for name, value in (('default_storage', FileSystemStorage(location=temporary.name)), ('STORAGE_DIR', 'certificates-test')):
            replacement = patch(f'docmanager.views.document_view.{name}', value)
            replacement.start()
            self.addCleanup(replacement.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.anonymous = APIClient()

    def send(self, client, filename='visita.pdf', content=b'%PDF-1.4 visita', content_type='application/pdf'):
        return client.post(ENDPOINT, {'medical_certificate': SimpleUploadedFile(filename, content, content_type=content_type)},
                           format='multipart')

    def test_pdf_and_images_are_stored_without_an_account(self):
        for filename, kind in (('visita.pdf', 'application/pdf'), ('Scansione.JPG', 'image/jpeg'), ('foto.png', 'image/png')):
            response = self.send(self.anonymous, filename, b'contenuto', kind)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['msg'], 'success!')
        self.assertEqual(MedicalCertificate.objects.filter(user=None).count(), 3)

    def test_other_files_are_refused_before_any_write(self):
        for filename, kind in (('note.txt', 'text/plain'), ('pagina.html', 'application/pdf'), ('certificato', 'image/png'),
                               ('archivio.pdf.zip', 'application/zip')):
            response = self.send(self.client, filename, b'contenuto', kind)
            self.assertEqual(response.status_code, 400, filename)
            self.assertEqual(response.json(), {'error': 'Seleziona un file PDF o un’immagine.'})
        self.assertFalse(Document.objects.exists())
        self.assertFalse(MedicalCertificate.objects.exists())

    @override_settings(MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES=32)
    def test_file_above_the_limit_is_refused(self):
        response = self.send(self.anonymous, content=b'x' * 33)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {'error': 'Il file supera la dimensione massima consentita (32 byte).'})
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.send(self.anonymous, content=b'x' * 32).status_code, 200)

    def test_default_limit_is_ten_megabytes(self):
        from django.conf import settings
        from docmanager.views.document_view import medical_certificate_too_large_message
        self.assertEqual(settings.MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES, 10 * 1024 * 1024)
        self.assertEqual(settings.MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR, 60)
        self.assertEqual(medical_certificate_too_large_message(settings.MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES),
                         'Il file supera la dimensione massima consentita (10 MB).')

    @override_settings(MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR=2)
    def test_requests_without_an_account_are_limited_per_hour(self):
        self.assertEqual(self.send(self.anonymous).status_code, 200)
        self.assertEqual(self.send(self.anonymous).status_code, 200)

        response = self.send(self.anonymous)

        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)
        self.assertEqual(Document.objects.count(), 2)
        # A signed-in account is not counted.
        for _ in range(3):
            self.assertEqual(self.send(self.client).status_code, 200)
