"""Flows open without an account (form answers, form attachments, the subscription link): size and rate limits."""
import base64
import hashlib
import tempfile
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.test import override_settings
from rest_framework.test import APIClient

from application.models import Document, ModuleResponses
from application.tests.base import BaseAPITestCase
from application.tests.test_modules_views import create_test_module, create_test_module_response


class PublicFlowLimitTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for name, value in (('default_storage', FileSystemStorage(location=temporary.name)), ('STORAGE_DIR', 'attachments-test')):
            replacement = patch(f'application.views.modules_views.{name}', value)
            replacement.start()
            self.addCleanup(replacement.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.anonymous = APIClient()
        self.module = create_test_module(sport_association=self.sport_association)

    def answer(self, client):
        return client.post(f'/modules/{self.module.module_id}/response/add', {'first_name': 'Ada'}, format='json')

    def attach(self, client, content, response=None):
        response = response or create_test_module_response(self.module)
        response.attachment_token_hash = hashlib.sha256(b'respondent-token').hexdigest()
        response.save()
        body = {'filename': 'visita.pdf', 'base64': base64.b64encode(content).decode(),
                'attachment_token': 'respondent-token'}
        return client.post(f'/modules/response/{response.module_response_id}/add-attachment', body, format='json')

    @override_settings(PUBLIC_REQUESTS_PER_WINDOW=2)
    def test_form_answers_without_an_account_are_limited_per_address(self):
        self.assertEqual(self.answer(self.anonymous).status_code, 200)
        self.assertEqual(self.answer(self.anonymous).status_code, 200)

        limited = self.answer(self.anonymous)

        self.assertEqual(limited.status_code, 429)
        self.assertIn('Retry-After', limited)
        self.assertEqual(ModuleResponses.objects.filter(module=self.module).count(), 2)
        # A signed-in account is not counted, and another address has its own count.
        for _ in range(3):
            self.assertEqual(self.answer(self.client).status_code, 200)
        self.assertEqual(self.anonymous.post(f'/modules/{self.module.module_id}/response/add', {'first_name': 'Bo'},
                                             format='json', REMOTE_ADDR='10.1.2.3').status_code, 200)

    @override_settings(PUBLIC_REQUESTS_PER_WINDOW=1)
    def test_each_endpoint_has_its_own_count(self):
        self.assertEqual(self.answer(self.anonymous).status_code, 200)
        self.assertEqual(self.attach(self.anonymous, b'x').status_code, 200)
        self.assertEqual(self.attach(self.anonymous, b'x').status_code, 429)
        self.assertEqual(self.answer(self.anonymous).status_code, 429)

    @override_settings(PUBLIC_REQUESTS_PER_WINDOW=1)
    def test_subscription_link_submit_is_limited_without_an_account(self):
        first = self.anonymous.post('/subscription/add', {}, format='json')
        self.assertEqual(first.status_code, 400)
        self.assertEqual(self.anonymous.post('/subscription/add', {}, format='json').status_code, 429)
        self.assertEqual(self.client.post('/subscription/add', {}, format='json').status_code, 400)

    def test_defaults(self):
        from django.conf import settings
        self.assertEqual(settings.PUBLIC_UPLOAD_MAX_BYTES, 10 * 1024 * 1024)
        self.assertEqual(settings.PUBLIC_REQUESTS_PER_WINDOW, 30)
        self.assertEqual(settings.PUBLIC_REQUEST_WINDOW_SECONDS, 600)

    @override_settings(PUBLIC_UPLOAD_MAX_BYTES=32)
    def test_attachment_above_the_limit_is_refused_before_any_write(self):
        response = self.attach(self.anonymous, b'x' * 33)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {'error': 'Il file supera la dimensione massima consentita (32 byte).'})
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.attach(self.anonymous, b'x' * 32).status_code, 200)
        self.assertEqual(Document.objects.count(), 1)

    @override_settings(PUBLIC_UPLOAD_MAX_BYTES=64)
    def test_declared_body_above_the_limit_is_refused_without_an_account(self):
        body = {'note': 'x' * 100}
        url = f'/modules/{self.module.module_id}/response/add'

        self.assertEqual(self.anonymous.post(url, body, format='json').status_code, 413)
        self.assertEqual(self.anonymous.post('/subscription/add', body, format='json').status_code, 413)
        self.assertFalse(ModuleResponses.objects.filter(module=self.module).exists())
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)

    def test_unknown_module_keeps_its_answer(self):
        response = self.anonymous.post(f'/modules/{uuid.uuid4()}/response/add', {'a': 1}, format='json')
        self.assertEqual(response.status_code, 404)
