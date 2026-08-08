"""
Tests for Printing views - printing_generate endpoint for various report types.
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from application.models import User
from application.tests.fixtures.factories import (
    create_test_user, create_test_sport_association, create_test_billing_subscription
)


class BasePrintingTestCase(TestCase):
    """Base test case for printing tests."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        self.billing_subscription = create_test_billing_subscription(user=self.user)
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        self.client.force_authenticate(user=None)


class PrintingGenerateTests(BasePrintingTestCase):
    """Tests for printing_generate endpoint: POST /printing/generate"""

    def test_generate_missing_type(self):
        response = self.client.post('/printing/generate', {
            'format': 'pdf'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_missing_format(self):
        response = self.client.post('/printing/generate', {
            'type': 'all_subscriptions'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_missing_both_params(self):
        response = self.client.post('/printing/generate', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_invalid_type(self):
        response = self.client.post('/printing/generate', {
            'type': 'invalid_type',
            'format': 'pdf'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_as_athlete_forbidden(self):
        athlete = create_test_user(role=User.ATHLETE)
        self.client.force_authenticate(user=athlete)
        response = self.client.post('/printing/generate', {
            'type': 'all_subscriptions',
            'format': 'pdf'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/printing/generate', {
            'type': 'all_subscriptions',
            'format': 'pdf'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_generate_expiring_medical_certificates(self):
        response = self.client.post('/printing/generate', {
            'type': 'expiring_medical_certificates',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_expired_medical_certificates(self):
        response = self.client.post('/printing/generate', {
            'type': 'expired_medical_certificates',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_empty_medical_certificates(self):
        response = self.client.post('/printing/generate', {
            'type': 'empty_medical_certificates',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_exempt_medical_certificates(self):
        response = self.client.post('/printing/generate', {
            'type': 'exempt_medical_certificates',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_expiring_subscriptions(self):
        response = self.client.post('/printing/generate', {
            'type': 'expiring_subscriptions',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_expiring_memberships(self):
        response = self.client.post('/printing/generate', {
            'type': 'expiring_memberships',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_all_subscriptions(self):
        response = self.client.post('/printing/generate', {
            'type': 'all_subscriptions',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_not_paid_quotes_subscriptions(self):
        response = self.client.post('/printing/generate', {
            'type': 'not_paid_quotes_subscriptions',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_not_paid_courses_subscriptions(self):
        response = self.client.post('/printing/generate', {
            'type': 'not_paid_courses_subscriptions',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_expired_payments(self):
        response = self.client.post('/printing/generate', {
            'type': 'expired_payments',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])

    def test_generate_subscriptions_with_all_payments(self):
        response = self.client.post('/printing/generate', {
            'type': 'subscriptions_with_all_payments',
            'format': 'xlsx'
        }, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST])
