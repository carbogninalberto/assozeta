import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.middleware import ExceptionHandlerMiddleware


class ExceptionHandlerMiddlewareTests(SimpleTestCase):
    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=128)
    def test_multipart_upload_is_not_eagerly_buffered(self):
        file_content = b'x' * 4096
        request = RequestFactory().post(
            '/association/import/validate',
            {'file': SimpleUploadedFile('backup.zip', file_content, content_type='application/zip')},
        )

        def get_response(upload_request):
            self.assertEqual(upload_request.FILES['file'].read(), file_content)
            return HttpResponse(status=204)

        response = ExceptionHandlerMiddleware(get_response)(request)

        self.assertEqual(response.status_code, 204)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=8)
    def test_oversized_non_multipart_request_returns_rendered_413(self):
        request = RequestFactory().post(
            '/api/test',
            data=b'{"value":"too large"}',
            content_type='application/json',
        )

        response = ExceptionHandlerMiddleware(lambda request: HttpResponse())(request)

        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            json.loads(response.content),
            {'exception': 'Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE.'},
        )

    def test_unhandled_exception_returns_rendered_json(self):
        request = RequestFactory().get('/api/test')

        def get_response(_request):
            raise RuntimeError('boom')

        response = ExceptionHandlerMiddleware(get_response)(request)

        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content), {'exception': 'boom'})
