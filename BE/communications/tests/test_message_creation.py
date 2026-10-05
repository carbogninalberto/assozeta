"""Saved message input must survive creation without weakening escaped output."""
from django.utils.html import escape

from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association
from communications.models import Message, MessageTransaction


class MessageCreationTests(BaseAPITestCase):
    def test_saved_email_preserves_body_subject_and_server_tenant_without_sending(self):
        foreign = create_test_sport_association()
        body = '<p>Prova <strong>A & B</strong></p>'
        subject = '  Quote & attività <2026>  '
        response = self.client.post('/communications/messages/add', {
            'type': Message.EMAIL, 'message': body, 'subject': subject,
            'sport_association': str(foreign.pk), 'message_id': 'untrusted-id',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        stored = Message.objects.get(sport_association=self.sport_association)
        self.assertEqual(stored.message, body)
        self.assertEqual(stored.subject, subject)
        self.assertFalse(Message.objects.filter(sport_association=foreign).exists())
        self.assertFalse(MessageTransaction.objects.exists())
        response = self.client.get('/communications/messages/list')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]['message'], escape(body))
        self.assertEqual(response.data[0]['subject'], escape(subject))

    def test_saved_post_preserves_text_with_optional_subject_and_rejects_empty_body_or_sms(self):
        response = self.client.post('/communications/messages/add', {
            'type': Message.INSIDE_APP, 'message': 'Avviso: lezione alle 18.',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        stored = Message.objects.get()
        self.assertEqual(stored.message, 'Avviso: lezione alle 18.')
        self.assertIsNone(stored.subject)
        for data in ({'type': Message.EMAIL, 'subject': 'manca testo'},
                     {'type': Message.EMAIL, 'message': ''},
                     {'type': 'SMS', 'message': 'non supportato'}):
            with self.subTest(data=data):
                response = self.client.post('/communications/messages/add', data, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Message.objects.count(), 1)
