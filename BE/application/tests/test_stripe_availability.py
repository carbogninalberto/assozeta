from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import Associate, Payment, SportAssociation, User
from application.serializers.auth_serializers import UserSerializerSearch
from application.utils.stripe_utils import online_payments_available
from instance.models import InstanceConfiguration


class StripeAvailabilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='StrongPass!1',
            role=User.ASSOCIATION,
            online_payments=True,
        )
        self.association = SportAssociation.objects.create(
            user=self.owner,
            denomination='Primary Association',
            tax_code='12345678901',
        )
        self.instance_config = InstanceConfiguration.objects.create(
            domain='selfhost.example.com',
            name='Self-hosted Instance',
            primary_association=self.association,
            self_hosted=True,
        )
        self.athlete = User.objects.create_user(
            username='athlete',
            email='athlete@example.com',
            password='StrongPass!1',
            role=User.ATHLETE,
        )
        self.associate = Associate.objects.create(
            user=self.athlete,
            sport_association=self.association,
            first_name='Mario',
            last_name='Rossi',
            tax_code='RSSMRA80A01H501U',
        )
        self.payment = Payment.objects.create(
            user=self.athlete,
            associate=self.associate,
            sport_association=self.association,
            amount='20.00',
        )

    @override_settings(STRIPE_KEY='')
    def test_association_preference_does_not_enable_unconfigured_payments(self):
        self.assertFalse(online_payments_available(self.association))
        self.assertFalse(UserSerializerSearch(self.owner).data['online_payments'])

    @override_settings(STRIPE_KEY='')
    @patch('application.views.stripe_views.stripe.PaymentIntent.create')
    def test_checkout_api_rejects_unconfigured_payments_without_calling_stripe(
        self,
        create_payment_intent,
    ):
        self.client.force_authenticate(user=self.athlete)

        response = self.client.post(f'/stripe/pay/{self.payment.payment_id}')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error'], 'Online payments are not configured.')
        create_payment_intent.assert_not_called()

    @override_settings(STRIPE_KEY='sk_test_configured')
    def test_configured_connected_account_enables_online_payments(self):
        self.instance_config.stripe_public_key = 'pk_test_configured'
        self.instance_config.save(update_fields=['stripe_public_key'])
        self.owner.stripe_account_id = 'acct_configured'
        self.owner.stripe_on_boarding_completed = True
        self.owner.save(update_fields=['stripe_account_id', 'stripe_on_boarding_completed'])

        self.assertTrue(online_payments_available(self.association))
        self.assertTrue(UserSerializerSearch(self.owner).data['online_payments'])

    def test_athlete_search_serialization_keeps_online_payments_disabled(self):
        self.assertFalse(UserSerializerSearch(self.athlete).data['online_payments'])
