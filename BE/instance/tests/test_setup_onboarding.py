from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import BillingPlan, SportAssociation, User
from application.serializers.auth_serializers import UserAuthSerializer
from instance.models import InstanceConfiguration


@override_settings(INSTANCE_SETUP_TOKEN='setup-token')
class InstanceSetupOnboardingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        BillingPlan.objects.create(name='Piano Pro', billing_type=BillingPlan.PRO_PLAN)

    def _fresh_payload(self, domain='fresh.example.test'):
        return {
            'domain': domain,
            'oem': {
                'name': 'Fresh Instance',
                'abbreviation': 'FI',
                'primaryColor': '#351DC2',
                'supportEmail': '',
            },
            'initialization': {
                'type': 'fresh',
                'associationName': 'Fresh ASD',
                'ownerEmail': 'owner@example.test',
                'ownerPassword': 'FreshPass!1',
            },
        }

    def _post_configure(self, payload):
        return self.client.post(
            '/instance/configure',
            payload,
            HTTP_X_SETUP_TOKEN='setup-token',
            format='json',
        )

    def test_setup_token_can_be_validated_before_configuration(self):
        valid_response = self.client.post(
            '/instance/setup-token/validate',
            HTTP_X_SETUP_TOKEN='setup-token',
        )
        invalid_response = self.client.post(
            '/instance/setup-token/validate',
            HTTP_X_SETUP_TOKEN='wrong-token',
        )

        self.assertEqual(valid_response.status_code, 200)
        self.assertTrue(valid_response.data['valid'])
        self.assertEqual(invalid_response.status_code, 401)

    def test_public_status_ignores_a_stale_authorization_header(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer stale-token')

        response = self.client.get('/instance/status')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['configured'])

    def _create_association(self, email, denomination='Legacy ASD'):
        user = User.objects.create_user(
            username=email,
            email=email,
            password='Password!1',
            role=User.ASSOCIATION,
        )
        association = SportAssociation.objects.create(
            user=user,
            denomination=denomination,
            tax_code='12345678901',
        )
        return user, association

    def _membership_payload(self):
        return {
            'membership_data': {
                'season': {'day': 1, 'month': 9},
                'fiscal': {'day': 1, 'month': 1},
                'subscription_fee': '20.00',
                'membership_fee': '20.00',
            },
        }

    def test_fresh_setup_persists_defaults_and_incomplete_provenance(self):
        response = self._post_configure(self._fresh_payload())

        self.assertEqual(response.status_code, 200, response.content)
        association = SportAssociation.objects.get(denomination='Fresh ASD')
        self.assertEqual(association.subscription_fee, Decimal('20.00'))
        self.assertEqual(association.membership_fee, Decimal('20.00'))

        config = InstanceConfiguration.objects.get()
        self.assertEqual(config.setup_provenance, InstanceConfiguration.SETUP_PROVENANCE_FRESH)
        self.assertIsNone(config.onboarding_completed_at)
        self.assertEqual(config.primary_association, association)

        config_response = self.client.get('/instance/config')
        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(config_response.data['setup']['provenance'], 'fresh')
        self.assertFalse(config_response.data['setup']['onboardingComplete'])

    def test_import_setup_persists_completed_provenance_and_preserves_values(self):
        owner, association = self._create_association('import-owner@example.test', 'Imported ASD')
        association.logo = None
        association.subscription_fee = Decimal('55.00')
        association.membership_fee = Decimal('66.00')
        association.save()

        result = Mock()
        result.ready.return_value = True
        result.failed.return_value = False
        result.result = {
            'success': True,
            'sport_association_id': str(association.sport_association_id),
        }

        with patch('instance.views.AsyncResult', return_value=result):
            response = self._post_configure({
                'domain': 'import.example.test',
                'oem': {'name': 'Imported Instance'},
                'initialization': {
                    'type': 'import',
                    'importTaskId': 'task-id',
                },
            })

        self.assertEqual(response.status_code, 200, response.content)
        config = InstanceConfiguration.objects.get()
        self.assertEqual(config.setup_provenance, InstanceConfiguration.SETUP_PROVENANCE_IMPORT)
        self.assertIsNotNone(config.onboarding_completed_at)
        self.assertEqual(config.primary_association, association)

        association.refresh_from_db()
        self.assertEqual(association.subscription_fee, Decimal('55.00'))
        self.assertEqual(association.membership_fee, Decimal('66.00'))
        self.assertFalse(UserAuthSerializer(owner).data['requires_welcome'])

    def test_fresh_requires_welcome_until_membership_step_completes_without_logo_or_leads(self):
        response = self._post_configure(self._fresh_payload())
        self.assertEqual(response.status_code, 200, response.content)

        config = InstanceConfiguration.objects.select_related('primary_association__user').get()
        owner = config.primary_association.user

        self.assertTrue(UserAuthSerializer(owner).data['requires_welcome'])

        self.client.force_authenticate(user=owner)
        response = self.client.patch('/onboarding/update', self._membership_payload(), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        config.refresh_from_db()
        owner.refresh_from_db()
        config.primary_association.refresh_from_db()
        self.assertIsNotNone(config.onboarding_completed_at)
        self.assertIsNone(config.primary_association.logo)
        self.assertIsNone(owner.lead_sport_association_role)
        self.assertIsNone(owner.lead_sport_association_size)
        self.assertIsNone(owner.lead_sport_market_channel)
        self.assertFalse(UserAuthSerializer(owner).data['requires_welcome'])

    def test_incomplete_fresh_membership_payload_does_not_complete_or_partially_commit(self):
        response = self._post_configure(self._fresh_payload())
        self.assertEqual(response.status_code, 200, response.content)

        config = InstanceConfiguration.objects.select_related('primary_association__user').get()
        owner = config.primary_association.user

        self.client.force_authenticate(user=owner)
        response = self.client.patch('/onboarding/update', {
            'membership_data': {
                'season': {'day': 15, 'month': 9},
                'subscription_fee': '99.00',
            },
        }, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.data['error'],
            'Fresh self-host onboarding requires a complete membership payload.',
        )
        self.assertIn('fiscal', response.data['details'])
        self.assertIn('membership_fee', response.data['details'])

        config.refresh_from_db()
        owner = User.objects.get(user_id=owner.user_id)
        association = SportAssociation.objects.get(sport_association_id=config.primary_association_id)
        self.assertIsNone(config.onboarding_completed_at)
        self.assertEqual(owner.subscription_start_day, 1)
        self.assertEqual(owner.subscription_start_month, 1)
        self.assertEqual(owner.balance_sheet_year, User.SOLAR_YEAR)
        self.assertEqual(association.subscription_fee, Decimal('20.00'))
        self.assertEqual(association.membership_fee, Decimal('20.00'))
        self.assertTrue(UserAuthSerializer(owner).data['requires_welcome'])

    def test_membership_endpoint_completes_only_matching_fresh_self_host_owner(self):
        primary_owner, primary_association = self._create_association('primary@example.test', 'Primary ASD')
        other_owner, _other_association = self._create_association('other@example.test', 'Other ASD')
        config = InstanceConfiguration.objects.create(
            domain='fresh.example.test',
            name='Fresh Instance',
            setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,
            primary_association=primary_association,
            self_hosted=True,
        )

        self.client.force_authenticate(user=other_owner)
        response = self.client.patch('/onboarding/update', self._membership_payload(), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        config.refresh_from_db()
        self.assertIsNone(config.onboarding_completed_at)

        self.client.force_authenticate(user=primary_owner)
        response = self.client.patch('/onboarding/update', self._membership_payload(), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        config.refresh_from_db()
        self.assertIsNotNone(config.onboarding_completed_at)

    def test_legacy_requires_welcome_behavior_still_uses_existing_fields(self):
        owner, association = self._create_association('legacy@example.test')

        self.assertTrue(UserAuthSerializer(owner).data['requires_welcome'])

        association.logo = 'logo-data'
        association.save()
        owner = User.objects.get(user_id=owner.user_id)
        self.assertTrue(UserAuthSerializer(owner).data['requires_welcome'])

        owner.lead_sport_association_role = 'Presidente'
        owner.lead_sport_association_size = '50 - 100 tesserati'
        owner.lead_sport_market_channel = 'google'
        owner.save()
        owner = User.objects.get(user_id=owner.user_id)
        self.assertFalse(UserAuthSerializer(owner).data['requires_welcome'])
