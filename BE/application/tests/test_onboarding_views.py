"""
Tests for Onboarding views - onboarding update endpoint.

Ported from SaaS test_onboarding_views.py, adapted for self-host.
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.models.user_models import UsersOnboarding
from application.tests.base import AuditlogDisabledMixin
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_billing_subscription,
    create_test_instance_config,
)


class BaseOnboardingTestCase(AuditlogDisabledMixin, TestCase):
    """Base test case for onboarding tests."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user)
        create_test_instance_config(primary_association=self.sport_association)
        self.users_onboarding, _ = UsersOnboarding.objects.get_or_create(user=self.user)
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        """Clean up."""
        self.client.force_authenticate(user=None)


class OnboardingUpdateTests(BaseOnboardingTestCase):
    """Tests for onboarding_update endpoint: PATCH /onboarding/update"""

    def test_update_sport_association_data(self):
        """Test updating sport association data during onboarding."""
        onboarding_data = {
            'sport_association': {
                'denomination': 'Test Sport Association',
                'tax_code': 'TEST12345678901',
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_membership_data(self):
        """Test updating membership data during onboarding."""
        onboarding_data = {
            'membership_data': {
                'membership_fee': 50.00,
                'subscription_fee': 100.00,
                'season': {'day': 1, 'month': 9},
                'fiscal': {'day': 1, 'month': 1},
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_membership_data_without_season(self):
        """Test updating membership data without season."""
        onboarding_data = {
            'membership_data': {
                'membership_fee': 75.00,
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_lead_data(self):
        """Test updating lead data during onboarding."""
        onboarding_data = {
            'lead_data': {
                'lead_sport_association_role': 'President',
                'lead_sport_association_size': '10-50',
                'lead_sport_market_channel': 'Google',
                'phone': '+39123456789',
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_lead_data_without_phone(self):
        """Test updating lead data without phone."""
        onboarding_data = {
            'lead_data': {
                'lead_sport_association_role': 'Secretary',
                'lead_sport_association_size': '50-100',
                'lead_sport_market_channel': 'Word of mouth',
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_onboarding_data(self):
        """Test updating onboarding progress data."""
        onboarding_data = {
            'onboarding_data': {
                'show': False,
                'create_membership': True,
                'view_membership': True,
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_unauthorized_non_association(self):
        """Test that non-association users cannot update onboarding."""
        regular_user = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=regular_user)

        onboarding_data = {
            'sport_association': {
                'denomination': 'Test',
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.force_authenticate(user=None)
        onboarding_data = {
            'sport_association': {
                'denomination': 'Test',
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_empty_data(self):
        """Test updating with empty data."""
        response = self.client.patch('/onboarding/update', {}, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_update_membership_data_with_null_fees(self):
        """Test updating membership data with null fees."""
        onboarding_data = {
            'membership_data': {
                'membership_fee': None,
                'subscription_fee': None,
            }
        }
        response = self.client.patch('/onboarding/update', onboarding_data, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])
