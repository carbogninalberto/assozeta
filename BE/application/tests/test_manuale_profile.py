"""Real profile PATCH persistence and authorization for the manual fixture."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Associate, SportAssociation, Subscription, User
from application.tests.base import BaseTestCase
from docmanager.models import Document


class ManualProfileTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        printing = patch('application.views.subscriptions_views.print_document_subscription.delay')
        self.printing = printing.start()
        self.addCleanup(printing.stop)
        workflows = patch('application.signals.check_workflows_trigger.delay')
        self.workflows = workflows.start()
        self.addCleanup(workflows.stop)
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            call_command('seed_manuale', output=str(Path(temporary) / 'browser.json'),
                         origin='http://127.0.0.1:5010', verbosity=0)
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.subscription = Subscription.objects.get(pk=fixture_id('subscription-1'))
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def endpoint(self, action='update', subscription=None):
        return f'/subscription/{(subscription or self.subscription).pk}/{action}'

    def snapshot(self, subscription):
        return Subscription._base_manager.filter(pk=subscription.pk).values().get()

    def test_owner_patch_persists_card_fields_and_serializes_number_as_string_preserving_person_and_status(self):
        document = Document.objects.create(filename='existing-registration.pdf')
        self.subscription.document_pdf = document
        self.subscription.save(update_fields=['document_pdf'])
        before = self.snapshot(self.subscription)
        person_before = Associate._base_manager.filter(pk=self.subscription.associate_id).values().get()
        response = self.client.patch(self.endpoint(),
            {'subscription_number': ' 42 ', 'subscription_type': ' Tessera Aurora '}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.subscription_number, '42')
        self.assertEqual(self.subscription.subscription_type, 'Tessera Aurora')
        for field in ('associate_id', 'user_id', 'sport_association_id', 'status_flag', 'role',
                      'type', 'acceptance_date', 'start_date', 'end_date', 'payment_id'):
            self.assertEqual(getattr(self.subscription, field), before[field], field)
        self.assertEqual(Associate._base_manager.filter(pk=self.subscription.associate_id).values().get(), person_before)
        self.assertIsNone(self.subscription.document_pdf_id)
        self.assertTrue(Document.objects.filter(pk=document.pk).exists())
        self.printing.assert_called_once_with(str(self.subscription.pk), None)
        self.workflows.assert_not_called()
        # Read through the real info handler and serializer, as a browser reload does.
        reloaded = self.client.get(self.endpoint('info'))
        self.assertEqual(reloaded.status_code, 200, reloaded.content)
        info = reloaded.data['data']['info']
        self.assertEqual(info['subscription_number'], '42')
        self.assertIsInstance(info['subscription_number'], str)
        self.assertEqual(info['subscription_type'], 'Tessera Aurora')
        self.assertEqual(info['associate']['first_name'], 'Giulia')
        self.assertEqual(info['status_flag'], Subscription.ACCEPTED)

    def test_reader_patch_is_denied_before_any_state_change_or_external_dispatch(self):
        before = self.snapshot(self.subscription)
        person_before = Associate._base_manager.filter(pk=self.subscription.associate_id).values().get()
        self.client.force_authenticate(user=self.reader)
        denied = self.client.patch(self.endpoint(),
            {'subscription_number': '99', 'subscription_type': 'Denied card type'}, format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(self.snapshot(self.subscription), before)
        self.assertEqual(Associate._base_manager.filter(pk=self.subscription.associate_id).values().get(), person_before)
        self.printing.assert_not_called()
        self.workflows.assert_not_called()

    def test_owner_cannot_patch_registration_from_another_association(self):
        foreign_owner = User.objects.create_user(username='profile-preserved-owner', email='profile@example.test')
        foreign_association = SportAssociation.objects.create(user=foreign_owner, denomination='Preserved association')
        foreign_person = Associate.objects.create(sport_association=foreign_association, first_name='Preserved', last_name='Person')
        foreign_subscription = Subscription.objects.create(sport_association=foreign_association,
            associate=foreign_person, user=foreign_owner, subscription_number='7', subscription_type='Foreign card',
            status_flag=Subscription.PENDING)
        before = self.snapshot(foreign_subscription)
        person_before = Associate._base_manager.filter(pk=foreign_person.pk).values().get()
        denied = self.client.patch(self.endpoint(subscription=foreign_subscription),
            {'subscription_number': '99', 'subscription_type': 'Denied card type'}, format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(self.snapshot(foreign_subscription), before)
        self.assertEqual(Associate._base_manager.filter(pk=foreign_person.pk).values().get(), person_before)
        self.printing.assert_not_called()
        self.workflows.assert_not_called()
