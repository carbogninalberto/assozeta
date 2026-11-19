from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from application.models import Associate, User
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from communications.message_template_views import DELIVERY
from communications.models import CommunicationConfiguration, MessageTemplate, OutgoingMessage


class MessageTemplateTests(BaseAPITestCase):
    settings = '/communications/settings/message-templates'
    previews = '/communications/outgoing/previews/'

    def setUp(self):
        super().setUp()
        self.recipient = Associate.objects.create(
            sport_association=self.sport_association, first_name='Giulia', last_name='Rossi',
            email='giulia@example.org', phone='0039 333-1234567',
        )
        self.recipient_url = f'/communications/outgoing/recipients/{self.recipient.pk}'

    def create_template(self, **values):
        return self.client.post(self.settings, {
            'channel': 'email', 'name': 'Invito', 'locale': 'EN-gb',
            'subject': 'Hello {{ customer_name }}',
            'body': 'Join us on {{date}} at {{time}}. {{link}}', 'is_default': True,
            **values,
        }, format='json')

    def preview(self, **values):
        return self.client.post(self.recipient_url, {'channel': 'email', 'locale': 'it', **values}, format='json')

    def act(self, preview, action, **values):
        return self.client.post(self.previews + preview['id'], {
            'action': action, 'subject': preview['subject'], 'body': preview['body'], **values,
        }, format='json')

    def test_template_lifecycle_defaults_and_sample(self):
        seeded = self.client.get(self.settings).json()
        self.assertEqual({row['channel'] for row in seeded['data']}, {'email', 'whatsapp', 'sms'})
        self.assertIn('customer_name', seeded['variables'])
        for body in ('{{ unknown }}', '{{date', '{{{date}}}', '{{date|safe}}'):
            self.assertEqual(self.create_template(body=body).status_code, 400)
        trial = self.client.post(self.settings + '/sample', {
            'channel': 'whatsapp', 'name': 'Invito', 'locale': 'it',
            'body': 'Ciao {{customer_name}}, alle {{time}}.',
        }, format='json')
        self.assertEqual(trial.json()['data']['body'], 'Ciao Giulia Rossi, alle 18:30.')
        self.assertEqual(MessageTemplate.objects.count(), 3)

        response = self.create_template()
        self.assertEqual(response.status_code, 201)
        template = response.json()['data']
        self.assertEqual(template['locale'], 'en-gb')
        second = self.create_template(name='Secondo').json()['data']
        self.assertFalse(MessageTemplate.objects.get(pk=template['id']).is_default)
        url = f"{self.settings}/{second['id']}"
        result = self.client.patch(url, {'name': 'Modificato', 'enabled': False}, format='json')
        self.assertEqual(result.json()['data']['name'], 'Modificato')
        self.assertFalse(result.json()['data']['is_default'])
        self.assertNotIn(second['id'], [row['id'] for row in self.client.get(self.recipient_url).json()['data']['templates']])
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertTrue(MessageTemplate.objects.get(pk=second['id']).archived)
        self.assertNotIn(second['id'], [row['id'] for row in self.client.get(self.settings).json()['data']])

    def test_preview_and_email_deliver_exact_edits_once(self):
        template = self.create_template().json()['data']
        missing = self.preview(locale='en-gb')
        self.assertEqual(missing.status_code, 400)
        self.assertIn('date', missing.json())
        response = self.preview(
            locale='en-gb', date='2026-12-03', time='18:15', link='https://example.org/join',
        )
        self.assertEqual(response.status_code, 201)
        preview = response.json()['data']
        self.assertEqual(preview['template_id'], template['id'])
        self.assertEqual(preview['recipient'], self.recipient.email)
        self.assertEqual(preview['subject'], 'Hello Giulia Rossi')
        self.assertEqual(preview['body'], 'Join us on 2026-12-03 at 18:15. https://example.org/join')

        edit = {'subject': 'New subject', 'body': '  Edited content\nwith a second line.  '}
        with patch.object(CommunicationConfiguration, 'send_email', return_value=(True, 'Ok')) as send:
            for _ in range(2):
                result = self.act(preview, 'send', **edit)
                self.assertEqual(result.json()['data']['status'], 'submitted')
            send.assert_called_once_with(edit['subject'], edit['body'], [self.recipient.email])
            self.assertEqual(self.act(preview, 'send', body='different').status_code, 409)
        row = OutgoingMessage.objects.get(pk=preview['id'])
        self.assertEqual((row.rendered_body, row.rendered_subject), (preview['body'], preview['subject']))
        self.assertEqual((row.body, row.subject, row.actor_id), (edit['body'], edit['subject'], self.user.pk))

    def test_fallback_discard_skip_and_failed_send(self):
        with patch.object(CommunicationConfiguration, 'send_email', return_value=(False, 'Unavailable')) as send:
            whatsapp = self.preview(channel='whatsapp').json()['data']
            self.assertTrue(whatsapp['can_open_whatsapp'])
            final = self.act(whatsapp, 'open_whatsapp', body='Ciao & grazie! ☀').json()['data']
            url = urlsplit(final['whatsapp_url'])
            self.assertEqual((url.netloc, url.path), ('wa.me', '/393331234567'))
            self.assertEqual(parse_qs(url.query), {'text': ['Ciao & grazie! ☀']})
            self.assertEqual(final['status'], 'manual')
            self.assertTrue(final['warning'])

            email = self.preview().json()['data']
            final = self.act(email, 'discard').json()['data']
            self.assertEqual(final['status'], 'discarded')
            self.assertTrue(final['warning'])
            self.assertEqual(self.act(email, 'send').status_code, 409)
            sms = self.preview(channel='sms').json()['data']
            final = self.act(sms, 'send').json()['data']
            self.assertEqual(final['status'], 'skipped')
            self.assertTrue(final['warning'])
            send.assert_not_called()
            failed = self.act(self.preview().json()['data'], 'send').json()['data']
            self.assertEqual(failed['status'], 'failed')
            self.assertTrue(failed['warning'])

    def test_direct_whatsapp_hook_and_contact_revalidation(self):
        delivered = []
        with patch.dict(DELIVERY, {'whatsapp': lambda attempt, _association: delivered.append(attempt.body) or True}):
            message = self.preview(channel='whatsapp').json()['data']
            self.assertTrue(message['can_send'])
            self.assertFalse(message['can_open_whatsapp'])
            self.assertEqual(self.act(message, 'open_whatsapp').status_code, 400)
            self.assertEqual(self.act(message, 'send', body='approved').json()['data']['status'], 'submitted')
            self.assertEqual(delivered, ['approved'])
            stale = self.preview(channel='whatsapp').json()['data']
            self.recipient.phone = '+393338765432'
            self.recipient.save(update_fields=['phone'])
            self.assertEqual(self.act(stale, 'send').status_code, 409)
            self.assertEqual(self.act(stale, 'discard').json()['data']['status'], 'discarded')

    def test_permission_and_tenant_boundaries(self):
        owner_preview = self.preview().json()['data']
        foreign = create_test_sport_association()
        other = Associate.objects.create(sport_association=foreign, first_name='Elsewhere')
        self.assertEqual(self.client.get(f'/communications/outgoing/recipients/{other.pk}').status_code, 404)

        collaborator = create_test_user(
            role=User.COLLABORATOR, connected_user=self.user, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.communication.settings.read', 'association.communication.messages.create'],
        )
        self.client.force_authenticate(user=collaborator)
        self.assertEqual(self.client.get(self.settings).status_code, 200)
        self.assertEqual(self.create_template().status_code, 403)
        self.assertEqual(self.client.get(self.recipient_url).status_code, 403)
        collaborator.collaborator_permissions += ['association.personas.read']
        collaborator.save(update_fields=['collaborator_permissions'])
        self.assertEqual(self.client.get(self.recipient_url).status_code, 200)
        self.assertEqual(self.act(owner_preview, 'send').status_code, 404)
        self.client.force_authenticate(user=create_test_user(role=User.ATHLETE))
        self.assertEqual(self.client.get(self.settings).status_code, 403)
