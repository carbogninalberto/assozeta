import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from application.services.social_auth_service import SocialAuthService
from application.utils.stripe_utils import stripe_public_key, stripe_secret_key, stripe_webhook_secret, stripe_request_options, stripe_direct_credentials_configured
from instance.integration_configuration import effective_integration, encrypt_secret, decrypt_secret
from instance.models import InstanceConfiguration

ENV = dict(STRIPE_PUBLIC_KEY='pk_test_environment', STRIPE_KEY='sk_test_environment',
           STRIPE_WEBHOOK_SECRET='whsec_environment',
           SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='environment.apps.googleusercontent.com',
           SOCIAL_AUTH_APPLE_ID_CLIENT='com.example.environment')


@override_settings(**ENV)
class IntegrationSettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='integration-owner', role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Integration Club')
        self.config = InstanceConfiguration.objects.create(domain='integration.example.test', name='Integration Club',
                                                           primary_association=association, self_hosted=True,
                                                           stripe_secret_key='unused-legacy-secret',
                                                           google_client_id='legacy.apps.googleusercontent.com')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def endpoint(self, provider):
        return '/instance/admin/integrations/' + provider

    def save(self, provider='stripe', **changes):
        value = {'revision': 0, 'enabled': True, **({'public_key': 'pk_test_saved'} if provider == 'stripe' else {'client_id': 'saved.apps.googleusercontent.com' if provider == 'google' else 'com.example.saved'}), **changes}
        return self.client.put(self.endpoint(provider), value, format='json')

    def test_unrelated_users_denied_for_each_provider_and_method(self):
        other = User.objects.create_user(username='unrelated-user')
        for user in (None, other):
            self.client.force_authenticate(user)
            for provider in ('stripe', 'google', 'apple'):
                for method in ('get', 'put', 'delete'):
                    with self.subTest(user=user, provider=provider, method=method):
                        response = getattr(self.client, method)(self.endpoint(provider), {}, format='json', HTTP_USER_ID=str(self.owner.pk))
                        self.assertIn(response.status_code, (401, 403))
        self.client.force_authenticate(self.owner)
        self.config.self_hosted = False
        self.config.save(update_fields=['self_hosted'])
        self.assertEqual(self.client.get(self.endpoint('stripe')).status_code, 403)

    def test_environment_values_loaded_and_secrets_never_returned(self):
        for provider in ('stripe', 'google', 'apple'):
            response = self.client.get(self.endpoint(provider))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['source'], 'environment')
            self.assertFalse(response.data['restart_required'])
            self.assertEqual(response['Cache-Control'], 'no-store')
        response = self.client.get(self.endpoint('stripe'))
        self.assertEqual(response.data['public_key'], ENV['STRIPE_PUBLIC_KEY'])
        self.assertTrue(response.data['secret_key_configured'])
        self.assertTrue(response.data['webhook_secret_configured'])
        for secret in ('sk_test_environment', 'whsec_environment', 'unused-legacy-secret'):
            self.assertNotIn(secret, response.content.decode())
        self.assertEqual(self.client.get(self.endpoint('google')).data['client_id'], ENV['SOCIAL_AUTH_GOOGLE_OAUTH2_KEY'])

    def test_save_encrypts_retained_environment_credentials_and_reset_restores_environment(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source'], 'instance')
        self.assertEqual(stripe_public_key(), 'pk_test_saved')
        self.assertEqual(stripe_secret_key(), 'sk_test_environment')
        self.config.refresh_from_db()
        self.assertNotIn('sk_test_environment', json.dumps(self.config.integration_settings))
        self.assertNotIn('whsec_environment', json.dumps(self.config.integration_settings))
        self.assertEqual(self.config.stripe_secret_key, 'unused-legacy-secret')
        self.assertEqual(self.save(revision=1, secret_key='sk_test_replaced').status_code, 200)
        self.assertEqual(stripe_request_options(), {'api_key': 'sk_test_replaced'})
        self.assertEqual(self.save(revision=2).status_code, 200)
        self.assertEqual(stripe_secret_key(), 'sk_test_replaced')
        self.assertEqual(settings.STRIPE_KEY, 'sk_test_environment')
        response = self.client.delete(self.endpoint('stripe'), {'revision': 3}, format='json')
        self.assertEqual(response.data['source'], 'environment')
        self.assertEqual(response.data['revision'], 4)
        self.assertEqual(stripe_secret_key(), 'sk_test_environment')
        self.config.refresh_from_db()
        self.assertIsNone(self.config.integration_settings['stripe']['settings'])

    def test_disable_fails_closed_and_clear_requires_explicit_action(self):
        self.assertEqual(self.save(enabled=False, clear_secrets=['secret_key']).status_code, 200)
        self.assertEqual(stripe_public_key(), '')
        self.assertEqual(stripe_secret_key(), '')
        self.assertEqual(stripe_webhook_secret(), '')
        with self.assertRaises(ImproperlyConfigured):
            stripe_request_options()
        response = self.client.get(self.endpoint('stripe'))
        self.assertFalse(response.data['secret_key_configured'])
        self.assertTrue(response.data['webhook_secret_configured'])
        self.assertEqual(self.save(revision=1).status_code, 400)
        self.assertEqual(self.save(revision=1, secret_key='sk_test_restored').status_code, 200)

    def test_revisions_are_independent_and_stale_saves_and_resets_rejected(self):
        self.assertEqual(self.save('google').status_code, 200)
        self.assertEqual(self.save('stripe').status_code, 200)
        self.assertEqual(self.save('google').status_code, 409)
        self.assertEqual(self.client.delete(self.endpoint('google'), {'revision': 0}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(self.endpoint('google'), {'revision': 1}, format='json').status_code, 200)
        self.assertEqual(self.save('google', revision=1).status_code, 409)
        self.assertEqual(self.client.get(self.endpoint('stripe')).data['revision'], 1)

    def test_validation_rejects_partial_enabled_mismatched_modes_and_conflicting_secret_actions(self):
        for value in ({'secret_key': 'sk_live_wrong'}, {'secret_key': 'not-a-key'},
                      {'secret_key': 'sk_test_new', 'clear_secrets': ['secret_key']},
                      {'clear_secrets': ['webhook_secret']}, {'public_key': ''}):
            with self.subTest(value=value):
                self.assertEqual(self.save(**value).status_code, 400)
        self.assertEqual(self.save('google', client_id='https://wrong.example.test').status_code, 400)
        self.assertEqual(self.save('google', client_id='not-a-google-client').status_code, 400)
        self.assertEqual(self.save('apple', client_id='').status_code, 400)
        self.assertEqual(self.client.get(self.endpoint('unknown')).status_code, 404)
        self.assertEqual(self.client.get(self.endpoint('stripe')).data['revision'], 0)

    def test_unreadable_secret_can_be_replaced_or_cleared_without_disclosing_ciphertext(self):
        self.save(secret_key='sk_test_saved')
        self.config.refresh_from_db()
        encrypted = self.config.integration_settings['stripe']['settings']['secret_key']
        with override_settings(SECRET_KEY='different-key', SECRET_KEY_FALLBACKS=[]):
            response = self.client.get(self.endpoint('stripe'))
            self.assertTrue(response.data['credential_error'])
            self.assertFalse(stripe_direct_credentials_configured())
            self.assertNotIn(encrypted, response.content.decode())
            self.assertEqual(self.save(revision=1).status_code, 400)
            self.assertEqual(self.save(revision=1, enabled=False, clear_secrets=['secret_key', 'webhook_secret']).status_code, 200)
        with override_settings(SECRET_KEY='new-key', SECRET_KEY_FALLBACKS=[settings.SECRET_KEY]):
            self.assertEqual(decrypt_secret(encrypted), 'sk_test_saved')

    def test_public_runtime_configuration_matches_override_without_exposing_credentials(self):
        self.save(secret_key='sk_test_saved', webhook_secret='whsec_saved')
        self.save('google')
        self.save('apple')
        self.client.force_authenticate(None)
        response = self.client.get('/instance/config')
        self.assertEqual(response.data['stripe']['publicKey'], 'pk_test_saved')
        self.assertEqual(response.data['oauth']['googleClientId'], 'saved.apps.googleusercontent.com')
        self.assertTrue(response.data['oauth']['googleEnabled'])
        self.assertEqual(response.data['oauth']['appleClientId'], 'com.example.saved')
        for secret in ('sk_test_saved', 'whsec_saved', 'unused-legacy-secret', 'integration_settings'):
            self.assertNotIn(secret, response.content.decode())

    def test_save_preserves_other_settings_and_secret_requests_are_not_profiled_or_audited(self):
        self.config.email_settings = {'unchanged': True}
        self.config.diagnostic_results = {'unchanged': True}
        self.config.save(update_fields=['email_settings', 'diagnostic_results'])
        self.save()
        self.config.refresh_from_db()
        self.assertEqual(self.config.email_settings, {'unchanged': True})
        self.assertEqual(self.config.diagnostic_results, {'unchanged': True})
        self.assertIn('instance-integration-settings', settings.DRF_API_LOGGER_SKIP_URL_NAME)
        for provider in ('stripe', 'google', 'apple'):
            self.assertIn(self.endpoint(provider), settings.SILKY_IGNORE_PATHS)

    @patch('application.services.social_auth_service.id_token.verify_oauth2_token')
    def test_google_verification_uses_saved_client_id_and_disabling_stops_verification(self, verify):
        verify.return_value = {'iss': 'accounts.google.com', 'email': 'fixture@example.test', 'sub': 'fixture'}
        self.save('google')
        self.assertTrue(SocialAuthService.verify_google_token('fixture-token')[0])
        self.assertEqual(verify.call_args.args[2], 'saved.apps.googleusercontent.com')
        self.save('google', revision=1, enabled=False)
        verify.reset_mock()
        self.assertEqual(SocialAuthService.verify_google_token('fixture-token'), (False, None))
        verify.assert_not_called()

    @patch('application.services.social_auth_service.requests.get')
    @patch('application.services.social_auth_service.id_token.verify_oauth2_token', side_effect=ValueError('not an ID token'))
    def test_google_access_token_fallback_rejects_another_client(self, verify, get):
        self.save('google')
        get.return_value = MagicMock(status_code=200, json=lambda: {'aud': 'other.apps.googleusercontent.com'})
        self.assertEqual(SocialAuthService.verify_google_token('fixture-access-token'), (False, None))
        self.assertEqual(get.call_count, 1)

    @patch('jwt.algorithms.RSAAlgorithm.from_jwk', return_value='fixture-key')
    @patch('application.services.social_auth_service.jwt.decode', return_value={'email': 'fixture@example.test', 'sub': 'fixture'})
    @patch('application.services.social_auth_service.jwt.get_unverified_header', return_value={'kid': 'fixture'})
    @patch('application.services.social_auth_service.requests.get')
    def test_apple_verification_uses_saved_audience_and_reports_web_limit(self, get, header, decode, key):
        self.save('apple')
        get.return_value = MagicMock(json=lambda: {'keys': [{'kid': 'fixture'}]})
        self.assertTrue(SocialAuthService.verify_apple_token('fixture-token')[0])
        self.assertEqual(decode.call_args.kwargs['audience'], 'com.example.saved')
        self.assertFalse(self.client.get(self.endpoint('apple')).data['web_login_available'])
        self.save('apple', revision=1, enabled=False)
        get.reset_mock()
        self.assertEqual(SocialAuthService.verify_apple_token('fixture-token'), (False, None))
        get.assert_not_called()

    def test_legacy_google_visibility_is_preserved_until_explicit_override_and_reset(self):
        self.config.display_settings = {'login': {'allowOauthLogin': False}}
        self.config.save(update_fields=['display_settings'])
        self.assertFalse(self.client.get(self.endpoint('google')).data['enabled'])
        self.assertFalse(self.client.get('/instance/config').data['oauth']['googleEnabled'])
        self.assertEqual(self.save('google').status_code, 200)
        self.assertTrue(self.client.get('/instance/config').data['oauth']['googleEnabled'])
        self.client.delete(self.endpoint('google'), {'revision': 1}, format='json')
        self.assertFalse(self.client.get('/instance/config').data['oauth']['googleEnabled'])
        from instance.defaults import DEFAULT_DISPLAY_SETTINGS
        self.assertTrue(DEFAULT_DISPLAY_SETTINGS['login']['allowOauthLogin'])
