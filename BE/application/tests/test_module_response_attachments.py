"""Attachments of a module response: who may add or remove them."""
import base64
import hashlib
import tempfile
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from rest_framework.test import APIClient

from application.models import Document, ModuleResponses, User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from application.tests.test_modules_views import create_test_module, create_test_module_response


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class ModuleResponseAttachmentTests(BaseAPITestCase):
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
        self.response = create_test_module_response(self.module, attachment_token_hash=token_hash('mine'))
        self.other = create_test_module_response(self.module, attachment_token_hash=token_hash('theirs'))
        self.mine_document = Document.objects.create(filename='mine.txt')
        self.theirs_document = Document.objects.create(filename='theirs.txt')
        self.response.attachments.add(self.mine_document)
        self.other.attachments.add(self.theirs_document)

    def url(self, response=None):
        return f'/modules/response/{(response or self.response).module_response_id}/add-attachment'

    def file_body(self, **extra):
        return {'filename': 'a.txt', 'base64': base64.b64encode(b'ciao').decode(), **extra}

    def test_response_add_returns_a_token_that_is_stored_hashed(self):
        reply = self.anonymous.post(f'/modules/{self.module.module_id}/response/add', {'first_name': 'Ada'}, format='json')
        self.assertEqual(reply.status_code, 200)
        token = reply.json()['attachment_token']
        stored = ModuleResponses.objects.get(module_response_id=reply.json()['module_response_id'])
        self.assertEqual(stored.attachment_token_hash, token_hash(token))
        self.assertNotEqual(stored.attachment_token_hash, token)

    def test_attach_needs_the_response_token_without_an_account(self):
        for body in (self.file_body(), self.file_body(attachment_token='theirs'), self.file_body(attachment_token='')):
            reply = self.anonymous.post(self.url(), body, format='json')
            self.assertEqual(reply.status_code, 404)
            self.assertEqual(reply.json(), {'error': 'response not found'})
        self.assertEqual(self.response.attachments.count(), 1)
        self.assertEqual(Document.objects.count(), 2)

        reply = self.anonymous.post(self.url(), self.file_body(attachment_token='mine'), format='json')
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.response.attachments.count(), 2)

    def test_response_without_a_token_cannot_be_changed_anonymously(self):
        legacy = create_test_module_response(self.module)
        self.assertEqual(self.anonymous.post(self.url(legacy), self.file_body(attachment_token=''), format='json').status_code, 404)
        self.assertEqual(self.anonymous.delete(self.url(legacy), {'document_id': str(self.theirs_document.document_id)}, format='json').status_code, 404)

    def test_delete_needs_the_response_token_without_an_account(self):
        body = {'document_id': str(self.mine_document.document_id)}
        self.assertEqual(self.anonymous.delete(self.url(), body, format='json').status_code, 404)
        self.assertEqual(self.anonymous.delete(self.url(), {**body, 'attachment_token': 'theirs'}, format='json').status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.mine_document.pk).exists())

        reply = self.anonymous.delete(self.url(), {**body, 'attachment_token': 'mine'}, format='json')
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json(), {'msg': 'attachment deleted'})
        self.assertFalse(Document.objects.filter(pk=self.mine_document.pk).exists())

    def test_delete_only_removes_an_attachment_of_that_response(self):
        reply = self.anonymous.delete(
            self.url(), {'document_id': str(self.theirs_document.document_id), 'attachment_token': 'mine'}, format='json')
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(reply.json(), {'error': 'document not found'})
        self.assertTrue(Document.objects.filter(pk=self.theirs_document.pk).exists())
        self.assertEqual(self.other.attachments.count(), 1)

        stray = Document.objects.create(filename='stray.txt')
        reply = self.client.delete(self.url(), {'document_id': str(stray.document_id)}, format='json')
        self.assertEqual(reply.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=stray.pk).exists())

        reply = self.client.delete(self.url(), {'document_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(reply.status_code, 404)

    def test_association_staff_needs_no_token(self):
        reply = self.client.post(self.url(), self.file_body(), format='json')
        self.assertEqual(reply.status_code, 200)
        reply = self.client.delete(self.url(), {'document_id': str(self.mine_document.document_id)}, format='json')
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=self.mine_document.pk).exists())

    def test_another_association_is_refused_even_with_an_account(self):
        stranger = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=stranger)
        client = self.create_authenticated_client(stranger)
        self.assertEqual(client.post(self.url(), self.file_body(), format='json').status_code, 404)
        reply = client.delete(self.url(), {'document_id': str(self.mine_document.document_id)}, format='json')
        self.assertEqual(reply.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.mine_document.pk).exists())
        # The token still works for a signed-in respondent.
        self.assertEqual(client.post(self.url(), self.file_body(attachment_token='mine'), format='json').status_code, 200)

    def test_signed_in_respondent_manages_their_own_response(self):
        athlete = create_test_user(role=User.ATHLETE)
        own = create_test_module_response(self.module, user=athlete)
        client = self.create_authenticated_client(athlete)
        self.assertEqual(client.post(self.url(own), self.file_body(), format='json').status_code, 200)
        self.assertEqual(client.post(self.url(), self.file_body(), format='json').status_code, 404)

    def test_unknown_response_is_not_found(self):
        reply = self.client.post(f'/modules/response/{uuid.uuid4()}/add-attachment', self.file_body(), format='json')
        self.assertEqual(reply.status_code, 404)
        reply = self.anonymous.delete(f'/modules/response/{uuid.uuid4()}/add-attachment', {'document_id': 'x'}, format='json')
        self.assertEqual(reply.status_code, 404)

    # The public module page lists a response's attachments only for its respondent or staff.
    def info(self, client, response=None, token=None):
        query = f'?response_id={(response or self.response).module_response_id}'
        if token is not None:
            query += f'&attachment_token={token}'
        return client.get(f'/modules/{self.module.custom_link}/info{query}')

    def test_token_shows_the_attachment_list(self):
        reply = self.info(self.anonymous, token='mine')
        self.assertEqual(reply.status_code, 200)
        self.assertEqual([d['filename'] for d in reply.json()['response_data']['attachments_docs']], ['mine.txt'])

    def test_missing_or_other_token_answers_as_unknown_response(self):
        for token in (None, '', 'theirs', 'nope'):
            reply = self.info(self.anonymous, token=token)
            self.assertEqual(reply.status_code, 200)
            self.assertIsNone(reply.json()['response_data'])

    def test_association_staff_needs_no_token(self):
        reply = self.info(self.client)
        self.assertEqual([d['filename'] for d in reply.json()['response_data']['attachments_docs']], ['mine.txt'])

    def test_signed_in_respondent_sees_their_own_response_only(self):
        athlete = create_test_user(role=User.ATHLETE)
        own = create_test_module_response(self.module, user=athlete)
        client = self.create_authenticated_client(athlete)
        self.assertIsNotNone(self.info(client, own).json()['response_data'])
        self.assertIsNone(self.info(client).json()['response_data'])
