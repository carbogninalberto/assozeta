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
        self.assertEqual(response['Content-Type'], 'application/zip')
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
