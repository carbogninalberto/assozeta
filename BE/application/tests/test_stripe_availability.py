from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient
from stripe.error import SignatureVerificationError

from application.models import (
    Associate,
    BillingPayment,
    BillingPlan,
    BillingSubscription,
    Payment,
    SportAssociation,
    User,
)
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
            first_name='Owner',
            last_name='User',
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
            stripe_public_key='pk_legacy_instance',
            stripe_secret_key='sk_legacy_instance',
            stripe_webhook_secret='whsec_legacy_instance',
            stripe_pricing_table='prctbl_legacy_instance',
            stripe_client_portal='https://billing.legacy.example.com',
        )
        self.athlete = User.objects.create_user(
            username='athlete',
            email='athlete@example.com',
            password='StrongPass!1',
            first_name='Athlete',
            last_name='Buyer',
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

    def test_direct_env_and_owner_preference_control_availability_without_connect(self):
        self.owner.stripe_account_id = None
        self.owner.stripe_on_boarding_completed = False
        self.owner.save(update_fields=['stripe_account_id', 'stripe_on_boarding_completed'])

        with self.settings(STRIPE_KEY='sk_test_direct', STRIPE_PUBLIC_KEY='pk_test_direct'):
            self.assertTrue(online_payments_available(self.association))
            self.assertTrue(UserSerializerSearch(self.owner).data['online_payments'])

            self.owner.online_payments = False
            self.owner.save(update_fields=['online_payments'])
            self.owner.refresh_from_db()
            self.association.refresh_from_db()
            self.assertFalse(online_payments_available(self.association))
            fresh_owner = User.objects.get(pk=self.owner.pk)
            self.assertFalse(UserSerializerSearch(fresh_owner).data['online_payments'])

    def test_missing_direct_env_disables_payments_even_with_legacy_connect_and_instance_fields(self):
        self.owner.stripe_account_id = 'acct_legacy_ignored'
        self.owner.stripe_on_boarding_completed = True
        self.owner.save(update_fields=['stripe_account_id', 'stripe_on_boarding_completed'])

        for stripe_key, public_key in (('', ''), ('sk_test_direct', ''), ('', 'pk_test_direct')):
            with self.subTest(stripe_key=bool(stripe_key), public_key=bool(public_key)):
                with self.settings(STRIPE_KEY=stripe_key, STRIPE_PUBLIC_KEY=public_key):
                    self.assertFalse(online_payments_available(self.association))
                    self.assertFalse(UserSerializerSearch(self.owner).data['online_payments'])

    @override_settings(STRIPE_KEY='', STRIPE_PUBLIC_KEY='pk_test_direct')
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

    @override_settings(STRIPE_KEY='sk_test_direct', STRIPE_PUBLIC_KEY='pk_test_direct')
    @patch('application.views.stripe_views.stripe.PaymentIntent.create')
    def test_checkout_creates_direct_payment_intent_with_exact_args_and_response(
        self,
        create_payment_intent,
    ):
        create_payment_intent.return_value = SimpleNamespace(
            stripe_id='pi_direct_123',
            client_secret='pi_direct_123_secret_456',
        )
        self.client.force_authenticate(user=self.athlete)

        response = self.client.post(f'/stripe/pay/{self.payment.payment_id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            'data': {
                'client_secret': 'pi_direct_123_secret_456',
            },
        })
        create_payment_intent.assert_called_once_with(
            amount=2000,
            currency='eur',
            description='Associato: Mario Rossi - Utente: Athlete Buyer - '
                        'Pagamento di 20.00 euro per Primary Association',
            payment_method_types=['card', 'sepa_debit'],
        )
        _, kwargs = create_payment_intent.call_args
        self.assertNotIn('stripe_account', kwargs)
        self.assertNotIn('application_fee_amount', kwargs)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.payment_intent_id, 'pi_direct_123')

    def test_athlete_search_serialization_keeps_online_payments_disabled(self):
        with self.settings(STRIPE_KEY='sk_test_direct', STRIPE_PUBLIC_KEY='pk_test_direct'):
            self.assertFalse(UserSerializerSearch(self.athlete).data['online_payments'])

    @override_settings(STRIPE_WEBHOOK_SECRET='')
    @patch('application.views.stripe_views.stripe.Webhook.construct_event')
    def test_webhook_rejects_missing_secret_without_constructing_event(self, construct_event):
        response = self.client.post(
            '/stripe/webhook',
            data=b'{}',
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=sig',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['error'], 'Stripe webhook is not configured.')
        construct_event.assert_not_called()

    @override_settings(STRIPE_WEBHOOK_SECRET='whsec_direct')
    @patch('application.views.stripe_views.stripe.Webhook.construct_event')
    def test_webhook_rejects_invalid_signature(self, construct_event):
        construct_event.side_effect = SignatureVerificationError('invalid signature', 't=1,v1=bad')

        response = self.client.post(
            '/stripe/webhook',
            data=b'{}',
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=bad',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(STRIPE_WEBHOOK_SECRET='whsec_direct')
    @patch('application.views.stripe_views.stripe.Webhook.construct_event')
    def test_webhook_verifies_signature_and_marks_direct_payment_idempotently(self, construct_event):
        self.payment.payment_intent_id = 'pi_direct_charge_123'
        self.payment.save(update_fields=['payment_intent_id'])
        payload = b'{"id":"evt_charge"}'
        construct_event.return_value = {
            'type': 'charge.succeeded',
            'data': {
                'object': {
                    'payment_intent': 'pi_direct_charge_123',
                },
            },
        }

        def mark_paid(_request, payment, response=True):
            Payment.objects.filter(payment_id=payment.payment_id).update(paid=True)

        with patch('application.views.stripe_views.mark_payment_as_paid', side_effect=mark_paid) as mark_payment:
            first_response = self.client.post(
                '/stripe/webhook',
                data=payload,
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=sig',
            )
            second_response = self.client.post(
                '/stripe/webhook',
                data=payload,
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=sig',
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        construct_event.assert_any_call(payload, 't=1,v1=sig', 'whsec_direct')
        self.assertEqual(mark_payment.call_count, 1)
        _args, kwargs = mark_payment.call_args
        self.assertFalse(kwargs['response'])
        self.payment.refresh_from_db()
        self.assertTrue(self.payment.paid)

    @override_settings(STRIPE_WEBHOOK_SECRET='whsec_direct')
    @patch('application.views.stripe_views.stripe.Webhook.construct_event')
    def test_platform_billing_webhook_events_are_ignored(self, construct_event):
        construct_event.return_value = {
            'type': 'invoice.payment_succeeded',
            'data': {
                'object': {
                    'subscription': 'sub_platform_ignored',
                    'customer': 'cus_platform_ignored',
                    'amount_paid': 9999,
                    'amount_subtotal': 9999,
                },
            },
        }

        response = self.client.post(
            '/stripe/webhook',
            data=b'{"id":"evt_invoice"}',
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=sig',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(BillingPayment.objects.count(), 0)

    @override_settings(
        STRIPE_PUBLIC_KEY='pk_env_public',
        STRIPE_KEY='sk_env_secret',
        STRIPE_WEBHOOK_SECRET='whsec_env_secret',
    )
    def test_public_instance_config_uses_env_public_key_and_exposes_no_secrets(self):
        response = self.client.get('/instance/config')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['stripe'], {
            'publicKey': 'pk_env_public',
            'pricingTable': None,
            'clientPortal': None,
        })
        self.assertNotIn('secretKey', response.data['stripe'])
        self.assertNotIn('webhookSecret', response.data['stripe'])

    def test_instance_reconfigure_ignores_legacy_stripe_fields(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put('/instance/reconfigure', data={
            'stripe': {
                'publicKey': 'pk_submitted_ignored',
                'secretKey': 'sk_submitted_ignored',
                'webhookSecret': 'whsec_submitted_ignored',
                'pricingTable': 'prctbl_submitted_ignored',
                'clientPortal': 'https://billing.submitted.example.com',
            },
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.instance_config.refresh_from_db()
        self.assertEqual(self.instance_config.stripe_public_key, 'pk_legacy_instance')
        self.assertEqual(self.instance_config.stripe_secret_key, 'sk_legacy_instance')
        self.assertEqual(self.instance_config.stripe_webhook_secret, 'whsec_legacy_instance')
        self.assertEqual(self.instance_config.stripe_pricing_table, 'prctbl_legacy_instance')
        self.assertEqual(self.instance_config.stripe_client_portal, 'https://billing.legacy.example.com')

    def test_connect_endpoints_are_disabled_without_connect_api_calls(self):
        self.client.force_authenticate(user=self.owner)

        with patch('application.views.stripe_views.stripe.Account.retrieve') as retrieve_account, \
                patch('application.views.stripe_views.stripe.Account.create') as create_account, \
                patch('application.views.stripe_views.stripe.AccountLink.create') as create_account_link:
            endpoints = (
                ('get', '/stripe/info'),
                ('post', '/stripe/on-boarding'),
                ('post', '/stripe/complete-on-boarding'),
            )
            for method, path in endpoints:
                with self.subTest(path=path):
                    response = getattr(self.client, method)(path)
                    self.assertEqual(response.status_code, status.HTTP_410_GONE)
                    self.assertEqual(
                        response.data['error'],
                        'Stripe Connect onboarding is disabled for self-hosted direct Stripe.',
                    )

        retrieve_account.assert_not_called()
        create_account.assert_not_called()
        create_account_link.assert_not_called()

    def test_platform_billing_checkout_disabled_but_included_pro_plan_remains(self):
        BillingPlan.objects.create(
            name='Piano Pro',
            description='Included self-hosted Pro plan',
            monthly_fee=0,
            annually_fee=0,
            billing_type=BillingPlan.PRO_PLAN,
        )
        self.client.force_authenticate(user=self.owner)

        checkout_response = self.client.post('/billing/checkout')
        active_plan_response = self.client.get('/billing/active-plan')

        self.assertEqual(checkout_response.status_code, status.HTTP_410_GONE)
        self.assertEqual(active_plan_response.status_code, status.HTTP_200_OK)
        self.assertEqual(active_plan_response.data['data']['active_plan']['name'], 'Piano Pro')
        self.assertTrue(BillingSubscription.objects.filter(user=self.owner).exists())

    @override_settings(STRIPE_KEY='sk_test_direct', STRIPE_PUBLIC_KEY='pk_test_direct')
    @patch('application.views.payment_views.stripe.BalanceTransaction.list')
    def test_payment_stats_uses_direct_balance_transactions_without_connect_or_application_fee(
        self,
        list_balance_transactions,
    ):
        cache.clear()
        list_balance_transactions.return_value = SimpleNamespace(
            auto_paging_iter=lambda: iter([
                {
                    'type': 'charge',
                    'fee_details': [
                        {'type': 'stripe_fee', 'amount': 123},
                        {'type': 'application_fee', 'amount': 456},
                    ],
                },
            ]),
        )
        self.owner.stripe_account_id = 'acct_legacy_ignored'
        self.owner.stripe_on_boarding_completed = True
        self.owner.save(update_fields=['stripe_account_id', 'stripe_on_boarding_completed'])
        self.client.force_authenticate(user=self.owner)

        response = self.client.get('/payment/stats')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, kwargs = list_balance_transactions.call_args
        self.assertEqual(set(kwargs.keys()), {'created'})
        self.assertNotIn('stripe_account', kwargs)
        self.assertEqual(response.data['data']['stripe_charges'], {
            'total': 1.23,
            'stripe_fee': 1.23,
        })
        self.assertNotIn('application_fee_amount', response.data['data']['stripe_charges'])

    @override_settings(STRIPE_KEY='', STRIPE_PUBLIC_KEY='')
    @patch('application.views.payment_views.stripe.BalanceTransaction.list')
    def test_payment_stats_skips_stripe_when_direct_credentials_are_missing(
        self,
        list_balance_transactions,
    ):
        cache.clear()
        self.client.force_authenticate(user=self.owner)

        response = self.client.get('/payment/stats')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['stripe_charges'], {
            'total': 0,
            'stripe_fee': 0,
        })
        list_balance_transactions.assert_not_called()
