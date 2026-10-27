import importlib
from unittest.mock import patch

from auditlog.models import LogEntry
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType

from application.models import User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from communications.models import SMTP_TIMEOUT_SECONDS, CommunicationConfiguration, Message

redaction = importlib.import_module('communications.migrations.0014_redact_smtp_password_audit')


class SendEmailScopeTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.caller = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.caller)
        CommunicationConfiguration.objects.create(sport_association=self.association, email_smtp_port=587, email_encryption='TLS')
        other = create_test_user(role=User.ASSOCIATION)
        self.foreign = Message.objects.create(sport_association=create_test_sport_association(user=other),
                                              type=Message.EMAIL, message='foreign body', subject='foreign subject')
        self.client.force_authenticate(self.caller)

    def send(self, message):
        with patch.object(CommunicationConfiguration, 'send_email', autospec=True, return_value=(True, 'ok')) as sent:
            response = self.client.post('/communications/send/email',
                                        {'message_id': str(message.message_id), 'email': 'a@example.test'}, format='json')
        return response, sent

    def test_foreign_message_id_is_not_found_and_not_sent(self):
        response, sent = self.send(self.foreign)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'msg': 'message not found.'})
        sent.assert_not_called()

    def test_own_message_id_still_sends(self):
        own = Message.objects.create(sport_association=self.association, type=Message.EMAIL, message='own body', subject='own subject')
        response, sent = self.send(own)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sent.call_args.kwargs['body'], 'own body')


class SmtpCredentialAuditTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(role=User.ASSOCIATION)
        create_test_sport_association(user=self.owner)
        self.client.force_authenticate(self.owner)

    def test_password_updates_are_not_audited(self):
        self.assertEqual(self.client.get('/communications/settings/smtp/info').status_code, 200)
        marker = 'not-a-real-credential'
        response = self.client.patch('/communications/settings/smtp/update',
                                     {'email_smtp_password': marker, 'email_smtp_user': 'sender@example.test'}, format='json')
        self.assertEqual(response.status_code, 200)
        config = CommunicationConfiguration.objects.get()
        entries = LogEntry.objects.get_for_object(config)
        self.assertTrue(entries.exists())
        self.assertFalse(any(marker in str(entry.changes) for entry in entries))
        self.assertTrue(any('email_smtp_user' in (entry.changes or {}) for entry in entries))

    def test_migration_redacts_existing_entries(self):
        config = CommunicationConfiguration.objects.create(email_smtp_port=587, email_encryption='TLS')
        content_type = ContentType.objects.get_for_model(CommunicationConfiguration)
        legacy = [LogEntry.objects.create(content_type=content_type, object_pk=str(config.pk), object_repr='c', action=1,
                                          changes=changes)
                  for changes in ({'email_smtp_password': ['old', 'new'], 'email_smtp_host': [None, 'h']},
                                  '{"email_smtp_password": ["a", "b"], "email_smtp_port": [null, "587"]}')]
        redaction.redact(django_apps, None)
        for entry in legacy:
            entry.refresh_from_db()
            changes = entry.changes if isinstance(entry.changes, dict) else __import__('json').loads(entry.changes)
            self.assertNotIn('email_smtp_password', changes)
            self.assertTrue(changes)


class SmtpTimeoutTests(BaseAPITestCase):
    def test_connections_are_bounded(self):
        config = CommunicationConfiguration(email_smtp_host='smtp.example.test', email_smtp_port=587,
                                            email_smtp_user='u', email_smtp_password='p', email_encryption='TLS')
        with patch('communications.models.smtplib.SMTP') as plain, patch('communications.models.smtplib.SMTP_SSL') as secure:
            config.verify_smtp(send_email=False)
            config.email_encryption = config.SSL
            config.verify_smtp(send_email=False)
        self.assertEqual(plain.call_args.kwargs['timeout'], SMTP_TIMEOUT_SECONDS)
        self.assertEqual(secure.call_args.kwargs['timeout'], SMTP_TIMEOUT_SECONDS)
