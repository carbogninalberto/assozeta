"""Real v7 API persistence and authorization, without email/provider dispatch.

Only the disposable seed guard is patched inside the transaction-owned database.
No successful API handler, permission resolver or database operation is mocked.
"""
import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Invoice, Payment, Subscription, User
from application.models.user_models import Associate, CollaborationInvites, SportAssociation
from application.services.jwt_token_service import JWTTokenService
from application.tests.base import BaseTestCase
from application.utils.api_utils import BalanceSheetData


class ManualOrganizationAccessTests(BaseTestCase):
    def seed(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'browser.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0)
            return json.loads(output.read_text())

    def setUp(self):
        super().setUp()
        self.fixture = self.seed()
        self.assertEqual(self.fixture['fixture_version'], 8)
        self.assertEqual(self.fixture['fixture_profile'], 'baseline')
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'))
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def read(self, route, client=None):
        response = (client or self.client).get(route)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def profile_payload(self, client=None, **changes):
        return {'user_data': {**self.read('/profile/info', client)['user_data'], **changes}}

    def reader_client(self):
        # Use a real issued token so reusing it after permission changes proves
        # current database authorization rather than force-auth cached fields.
        client = APIClient()
        token = JWTTokenService.generate_tokens_for_user(self.reader)['access_token']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def test_owner_updates_organization_and_independent_periods_without_rewriting_existing_records(self):
        registrations = list(Subscription._base_manager.filter(sport_association=self.association).values())
        payments = list(Payment._base_manager.filter(sport_association=self.association).values())
        invoices = list(Invoice._base_manager.filter(sport_association=self.association).values())
        payload = self.profile_payload()
        payload['user_data']['sport_association'].update({'address': 'Via dello Sport 14', 'address_cap': '00101',
            'website': 'https://aurora.example.test', 'abbreviated': 'Aurora', 'sport': 'Ginnastica'})
        response = self.client.patch('/profile/update', payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.association.refresh_from_db()
        self.assertEqual(self.association.address, 'Via dello Sport 14')
        self.assertEqual(self.association.address_cap, '00101')
        self.assertEqual(self.association.website, 'https://aurora.example.test')
        self.assertEqual(self.association.abbreviated, 'Aurora')
        self.assertEqual(self.association.sport, 'Ginnastica')
        self.assertEqual(self.association.denomination, 'Associazione Sportiva Aurora')
        self.assertEqual(self.association.user_id, self.owner.pk)

        settings = self.read('/profile/settings')['settings']
        settings.update({'balance_sheet_year': '4', 'balance_sheet_start_month': 10, 'balance_sheet_start_day': 15,
            'subscription_start_month': 9, 'subscription_start_day': 1, 'custom_end_date': True,
            'subscription_end_month': 6, 'subscription_end_day': 30, 'subscription_duration': 3,
            'membership_duration': 3})
        updated = self.client.post('/profile/settings', settings, format='json')
        self.assertEqual(updated.status_code, 200, updated.content)
        self.owner.refresh_from_db()
        for field, expected in {'balance_sheet_year': 4, 'balance_sheet_start_month': 10,
            'balance_sheet_start_day': 15, 'subscription_start_month': 9, 'subscription_start_day': 1,
            'custom_end_date': True, 'subscription_end_month': 6, 'subscription_end_day': 30,
            'subscription_duration': 3, 'membership_duration': 3}.items():
            self.assertEqual(getattr(self.owner, field), expected, field)
        fresh = APIClient()
        fresh.force_authenticate(user=User.objects.get(pk=self.owner.pk))
        reread = self.read('/profile/settings', fresh)['settings']
        self.assertEqual(reread['balance_sheet_year'], '4')
        self.assertEqual(reread['balance_sheet_start_day'], 15)
        self.assertEqual(reread['subscription_end_month'], 6)
        self.assertEqual(reread['membership_duration'], 3)
        self.assertEqual(list(Subscription._base_manager.filter(sport_association=self.association).values()), registrations)
        self.assertEqual(list(Payment._base_manager.filter(sport_association=self.association).values()), payments)
        self.assertEqual(list(Invoice._base_manager.filter(sport_association=self.association).values()), invoices)

        fiscal_start, fiscal_end = BalanceSheetData.get_range_from_year_and_starting_date(
            date=date(2026, 9, 30), starting_day=self.owner.balance_sheet_start_day,
            starting_month=self.owner.balance_sheet_start_month)
        self.assertEqual((fiscal_start.date(), fiscal_end.date()), (date(2025, 10, 15), date(2026, 10, 14)))
        season_start, season_end = BalanceSheetData.get_range_from_year_and_starting_date(
            date=date(2026, 9, 30), starting_day=self.owner.subscription_start_day,
            starting_month=self.owner.subscription_start_month, user=self.owner)
        self.assertEqual((season_start.date(), season_end.date()), (date(2026, 9, 1), date(2027, 6, 30)))
        person = Associate.objects.create(sport_association=self.association, first_name='Nuova', last_name='Iscrizione')
        created = Subscription.objects.create(sport_association=self.association, associate=person,
            user=self.owner, creation_date=timezone.make_aware(datetime(2026, 9, 30, 12)),
            type=Subscription.ASSOCIATE_ONLY, status_flag=Subscription.PENDING)
        created.refresh_from_db()
        self.assertEqual(created.start_date, date(2026, 9, 30))
        self.assertEqual(created.end_date, date(2027, 6, 30))
        self.assertEqual(created.user_id, self.owner.pk)

    def test_existing_token_obeys_grant_and_revocation_and_actor_profile_keeps_owner_data(self):
        reader = self.reader_client()
        original_permissions = list(self.reader.collaborator_permissions)
        self.assertIn('other.settings.read', original_permissions)
        self.assertIn('other.users.collaborators.read', original_permissions)
        self.assertNotIn('other.settings.update', original_permissions)
        original_owner_name = self.owner.first_name
        original_association = SportAssociation.objects.values().get(pk=self.association.pk)
        original_invites = CollaborationInvites.objects.count()
        self.assertEqual(len(self.read('/collaborators/list', reader)), 1)
        denied = reader.patch('/profile/update', self.profile_payload(reader, first_name='Vietato'), format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        denied_permissions = reader.patch(f'/collaborators/{self.reader.pk}/update', {'collaborator_role': 1}, format='json')
        self.assertEqual(denied_permissions.status_code, 403, denied_permissions.content)
        self.reader.refresh_from_db()
        self.assertEqual(self.reader.first_name, 'Marco')
        granted_permissions = original_permissions + ['other.settings.update']
        granted = self.client.patch(f'/collaborators/{self.reader.pk}/update', {
            'collaborator_role': 3, 'collaborator_permissions': granted_permissions}, format='json')
        self.assertEqual(granted.status_code, 200, granted.content)
        current = self.read('/profile/info', reader)['user_data']
        self.assertEqual(current['collaborator_permissions'], granted_permissions)
        self.assertEqual(current['collaborator_role'], 3)
        # This production handler updates the actual collaborator profile and
        # intentionally skips association fields because the actor is not owner.
        payload = self.profile_payload(reader, first_name='Matteo')
        payload['user_data']['sport_association']['denomination'] = 'Tentativo organizzazione'
        allowed = reader.patch('/profile/update', payload, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.reader.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.reader.first_name, 'Matteo')
        self.assertEqual(self.reader.connected_user_id, self.owner.pk)
        self.assertEqual(self.owner.first_name, original_owner_name)
        self.assertEqual(SportAssociation.objects.values().get(pk=self.association.pk), original_association)
        self.assertEqual(self.read('/profile/info', reader)['user_data']['first_name'], 'Matteo')
        settings_before = self.read('/profile/settings')['settings']
        # Record the existing method mismatch honestly: the UI uses POST, while
        # the permission registry currently only assigns GET/PATCH permissions.
        settings_denied = reader.post('/profile/settings', {**settings_before, 'balance_sheet_start_day': 15}, format='json')
        self.assertEqual(settings_denied.status_code, 403, settings_denied.content)
        self.assertEqual(self.read('/profile/settings')['settings'], settings_before)
        restored_name = reader.patch('/profile/update', self.profile_payload(reader, first_name='Marco'), format='json')
        self.assertEqual(restored_name.status_code, 200, restored_name.content)
        revoked = self.client.patch(f'/collaborators/{self.reader.pk}/update', {
            'collaborator_role': 3, 'collaborator_permissions': original_permissions}, format='json')
        self.assertEqual(revoked.status_code, 200, revoked.content)
        denied_again = reader.patch('/profile/update', self.profile_payload(reader, first_name='Vietato'), format='json')
        self.assertEqual(denied_again.status_code, 403, denied_again.content)
        self.reader.refresh_from_db()
        self.assertEqual(self.reader.first_name, 'Marco')
        self.assertEqual(self.reader.collaborator_permissions, original_permissions)
        self.assertEqual(CollaborationInvites.objects.count(), original_invites)

    def test_full_collaborator_access_does_not_grant_instance_restore_or_owner_identity(self):
        response = self.client.patch(f'/collaborators/{self.reader.pk}/update', {
            'collaborator_role': User.FULL, 'collaborator_permissions': []}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        reader = self.reader_client()
        self.assertEqual(self.read('/profile/info', reader)['user_data']['collaborator_role'], User.FULL)
        restore = reader.get('/instance/admin/data-restore')
        self.assertEqual(restore.status_code, 403, restore.content)
        access = self.read('/instance/access', reader)
        self.assertFalse(access['is_owner'])
        self.assertFalse(access['is_administrator'])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, User.ASSOCIATION)

    def test_reseed_restores_mutated_organization_years_reader_name_and_permissions(self):
        owner_fields = ['balance_sheet_year', 'balance_sheet_start_day', 'balance_sheet_start_month',
            'subscription_duration', 'membership_duration', 'subscription_start_day', 'subscription_start_month',
            'custom_end_date', 'subscription_end_day', 'subscription_end_month']
        association_fields = ['denomination', 'address', 'address_cap', 'website', 'abbreviated', 'sport',
            'vat_number', 'iban', 'whatsapp', 'federation', 'enroll_number']
        owner_before = User.objects.values(*owner_fields).get(pk=self.owner.pk)
        association_before = SportAssociation.objects.values(*association_fields).get(pk=self.association.pk)
        reader_before = User.objects.values('first_name', 'last_name', 'collaborator_role', 'collaborator_permissions').get(pk=self.reader.pk)
        User.objects.filter(pk=self.owner.pk).update(balance_sheet_year=4, balance_sheet_start_day=15,
            balance_sheet_start_month=10, subscription_duration=3, membership_duration=3,
            subscription_start_day=15, subscription_start_month=10, custom_end_date=True,
            subscription_end_day=30, subscription_end_month=6)
        SportAssociation.objects.filter(pk=self.association.pk).update(address='Modificato', address_cap='99999',
            website='https://changed.example.test', abbreviated='Modificato', sport='Modificato',
            vat_number='99999999999', iban='Conto dimostrativo', whatsapp='0000000000',
            federation='Modificata', enroll_number='Modificato')
        User.objects.filter(pk=self.reader.pk).update(first_name='Matteo', collaborator_role=1, collaborator_permissions=[])
        self.seed()
        self.assertEqual(User.objects.values(*owner_fields).get(pk=self.owner.pk), owner_before)
        self.assertEqual(SportAssociation.objects.values(*association_fields).get(pk=self.association.pk), association_before)
        self.assertEqual(User.objects.values('first_name', 'last_name', 'collaborator_role', 'collaborator_permissions').get(pk=self.reader.pk), reader_before)
