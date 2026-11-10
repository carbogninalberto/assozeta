from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from asgiref.sync import async_to_sync
from django.test import RequestFactory, SimpleTestCase

from application.utils.printing import PrintingService
from core.middleware import GZipMiddleware


class PrintingServiceTests(SimpleTestCase):
    @staticmethod
    async def consume_stream(response):
        return [chunk async for chunk in response.streaming_content]

    @patch('application.utils.printing.default_storage')
    def test_download_file_streams_s3_object_without_gzip(self, storage):
        body = MagicMock()
        body.read.side_effect = [b'first', b'second', b'']
        storage.exists.return_value = True
        storage._normalize_name.return_value = 'storage/exports/backup.zip'
        storage.bucket_name = 'backups'
        storage.connection.meta.client.get_object.return_value = {
            'Body': body,
            'ContentLength': 11,
            'ContentType': 'application/octet-stream',
        }
        document = SimpleNamespace(filepath='exports\\backup.zip', filename='backup.zip')
        request = RequestFactory().get(
            '/document/retrieve/id?download=true',
            HTTP_ACCEPT_ENCODING='gzip',
        )

        response = PrintingService().download_file(request, document, token=None)
        response = GZipMiddleware(lambda current_request: response).process_response(
            request,
            response,
        )

        self.assertTrue(response.streaming)
        self.assertTrue(response.is_async)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Content-Length'], '11')
        self.assertNotIn('Content-Encoding', response)
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="backup.zip"',
        )
        body.read.assert_not_called()

        self.assertEqual(async_to_sync(self.consume_stream)(response), [b'first', b'second'])
        self.assertEqual(
            body.read.call_args_list,
            [call(1024 * 1024), call(1024 * 1024), call(1024 * 1024)],
        )
        body.close.assert_called_once_with()

    @patch('application.utils.printing.default_storage')
    def test_stream_closes_s3_body_when_response_is_not_consumed(self, storage):
        body = MagicMock()
        storage._normalize_name.return_value = 'storage/exports/backup.zip'
        storage.bucket_name = 'backups'
        storage.connection.meta.client.get_object.return_value = {'Body': body}

        response = PrintingService()._stream_file_response('exports/backup.zip', 'backup.zip')
        response.close()

        body.read.assert_not_called()
        body.close.assert_called_once_with()

    @patch('application.utils.printing.default_storage')
    def test_stream_closes_s3_body_when_read_fails(self, storage):
        body = MagicMock()
        body.read.side_effect = OSError('S3 connection lost')
        storage._normalize_name.return_value = 'storage/exports/backup.zip'
        storage.bucket_name = 'backups'
        storage.connection.meta.client.get_object.return_value = {'Body': body}

        response = PrintingService()._stream_file_response('exports/backup.zip', 'backup.zip')

        with self.assertRaisesRegex(OSError, 'S3 connection lost'):
            async_to_sync(self.consume_stream)(response)
        body.close.assert_called_once_with()


class StoredDocumentTypeTests(SimpleTestCase):
    """Documents are served with a fixed set of inline types; every other file is a download."""

    def serve(self, filename, query='download=false', stored_type='binary/octet-stream'):
        document = SimpleNamespace(filepath='files/stored', filename=filename)
        request = RequestFactory().get(f'/document/retrieve/id?{query}')
        service = PrintingService()
        with patch('application.utils.printing.default_storage') as storage:
            storage.exists.return_value = True
            storage._normalize_name.return_value = 'storage/files/stored'
            storage.bucket_name = 'documents'
            storage.connection.meta.client.get_object.return_value = {
                'Body': MagicMock(),
                'ContentLength': 3,
                'ContentType': stored_type,
            }
            if query == 'download=false':
                return service.view_file(request, document, token=None)
            return service.download_file(request, document, token=None)

    def test_inline_types_keep_their_type_and_have_no_disposition_when_viewed(self):
        for filename, expected in (
            ('ricevuta.pdf', 'application/pdf'),
            ('foto.png', 'image/png'),
            ('foto.jpg', 'image/jpeg'),
            ('foto.JPEG', 'image/jpeg'),
            ('foto.gif', 'image/gif'),
            ('foto.webp', 'image/webp'),
        ):
            with self.subTest(filename=filename):
                response = self.serve(filename)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], expected)
                self.assertNotIn('Content-Disposition', response)
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_other_types_are_downloads_when_viewed(self):
        for filename in (
            'pagina.html', 'pagina.htm', 'disegno.svg', 'dati.xml', 'script.js', 'foglio.css', 'nota.txt',
            'pagina.xhtml', 'archivio.zip', 'senza-estensione', 'data:text/html,x', 'data:image/svg+xml,x',
            'foto.bmp', 'foto.tiff', 'icona.ico',
        ):
            with self.subTest(filename=filename):
                response = self.serve(filename)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/octet-stream')
                self.assertTrue(response['Content-Disposition'].startswith('attachment; filename='))
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_the_stored_object_type_is_not_used(self):
        response = self.serve('senza-estensione', stored_type='text/html')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="senza-estensione"')

    def test_other_types_are_downloads_without_the_download_value(self):
        response = self.serve('pagina.html', query='download=')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="pagina.html"')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_inline_types_follow_the_download_value(self):
        inline = self.serve('ricevuta.pdf', query='download=')
        self.assertEqual(inline['Content-Type'], 'application/pdf')
        self.assertEqual(inline['Content-Disposition'], 'inline; filename="ricevuta.pdf"')
        attached = self.serve('ricevuta.pdf', query='download=true')
        self.assertEqual(attached['Content-Type'], 'application/pdf')
        self.assertEqual(attached['Content-Disposition'], 'attachment; filename="ricevuta.pdf"')
        self.assertEqual(attached['X-Content-Type-Options'], 'nosniff')
