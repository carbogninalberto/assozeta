from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from instance.diagnostic_probe import probe
from instance.integration_probe import verify_integration, get_json
from instance.integration_configuration import effective_integration
from instance.models import InstanceConfiguration
from instance.tests import test_integrations
from django.test import override_settings


@override_settings(**test_integrations.ENV, AI_API_KEY='fixture-secret')
class IntegrationVerificationTests(TestCase):
    setUp = test_integrations.IntegrationSettingsTests.setUp

    @patch('instance.probe_runner.run_probe')
    def test_each_provider_verifies_saved_revision_and_persists_result(self, run):
        run.return_value = {'status': 'passed', 'level': 'connectivity', 'message': 'Fixture check', 'checked_at': timezone.now().isoformat()}
        for provider in ('stripe', 'google', 'apple', 'ai'):
            endpoint = '/instance/admin/integrations/' + provider
            # Enable Google independently of legacy OAuth display settings.
            if provider == 'google':
                self.client.put(endpoint, {'enabled': True, 'client_id': 'fixture.apps.googleusercontent.com', 'revision': 0}, format='json')
            revision = effective_integration(provider)['revision']
            response = self.client.post(endpoint + '/test', {'revision': revision}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            run.assert_called_with('integration_' + provider, {'revision': revision})
            stored = self.client.get(endpoint).data['last_test']
            self.assertEqual(stored['revision'], revision)
            self.assertEqual(stored['status'], 'passed')
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(self.client.post(endpoint + '/test', {'revision': revision}, format='json').status_code, 429)
            self.client.delete(endpoint, {'revision': revision}, format='json')
            self.assertIsNone(self.client.get(endpoint).data['last_test'])

    @patch('instance.probe_runner.run_probe')
    def test_rejects_stale_disabled_and_unauthorized_tests(self, run):
        endpoint = '/instance/admin/integrations/stripe/test'
        self.assertEqual(self.client.post(endpoint, {'revision': 99}, format='json').status_code, 409)
        self.assertEqual(self.client.post(endpoint, {}, format='json').status_code, 400)
        self.client.put('/instance/admin/integrations/stripe', {'enabled': False, 'public_key': 'pk_test_fixture', 'revision': 0}, format='json')
        self.assertEqual(self.client.post(endpoint, {'revision': 1}, format='json').status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(endpoint, {'revision': 1}, format='json').status_code, (401, 403))
        from application.models import User
        other = User.objects.create_user(username='verification-other', is_superuser=True)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(endpoint, {'revision': 1}, format='json').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.config.self_hosted = False
        self.config.save(update_fields=['self_hosted'])
        self.assertEqual(self.client.post(endpoint, {'revision': 1}, format='json').status_code, 403)
        run.assert_not_called()

    @patch('instance.probe_runner.run_probe')
    def test_result_is_discarded_if_settings_change_during_probe(self, run):
        def change(*args):
            config = InstanceConfiguration.get_config()
            config.integration_settings = {'stripe': {'revision': 1, 'settings': None}}
            config.save(update_fields=['integration_settings'])
            return {'status': 'passed', 'level': 'connectivity', 'message': 'Old settings', 'checked_at': timezone.now().isoformat()}
        run.side_effect = change
        response = self.client.post('/instance/admin/integrations/stripe/test', {'revision': 0}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.client.get('/instance/admin/integrations/stripe').data['last_test'])


class IntegrationProbeTests(SimpleTestCase):
    @patch('instance.integration_probe.get_json')
    @patch('instance.integration_probe.effective_integration')
    def test_ai_checks_catalog_without_generating(self, effective, fetch):
        effective.return_value = {'enabled': True, 'revision': 4, 'api_key': 'secret', 'base_url': 'https://provider.example.test/v1', 'model': 'model'}
        fetch.return_value = ({'data': [{'id': 'model'}]}, None)
        self.assertEqual(probe('integration_ai', {'revision': 4})['status'], 'passed')
        fetch.assert_called_once_with('https://provider.example.test/v1/models', {'Authorization': 'Bearer secret'})
        fetch.return_value = ({'data': []}, None)
        self.assertEqual(verify_integration('ai', 4)['status'], 'warning')
        effective.return_value['enabled'] = False
        fetch.reset_mock()
        self.assertEqual(verify_integration('ai', 4)['status'], 'not_applicable')
        fetch.assert_not_called()

    @patch('instance.integration_probe.get_json')
    @patch('instance.integration_probe.effective_integration')
    def test_stripe_only_reads_balance_and_checks_mode(self, effective, fetch):
        effective.return_value = {'enabled': True, 'revision': 0, 'public_key': 'pk_test_fixture', 'secret_key': 'secret', 'webhook_secret': 'whsec_fixture'}
        fetch.return_value = ({'object': 'balance', 'livemode': False}, None)
        self.assertEqual(verify_integration('stripe', 0)['status'], 'passed')
        fetch.assert_called_once_with('https://api.stripe.com/v1/balance', {'Authorization': 'Bearer secret'})
        fetch.return_value = ({'object': 'balance', 'livemode': True}, None)
        self.assertEqual(verify_integration('stripe', 0)['status'], 'failed')

    @patch('instance.integration_probe.get_json')
    @patch('instance.integration_probe.effective_integration')
    def test_login_checks_do_not_claim_client_id_or_login_verified(self, effective, fetch):
        effective.return_value = {'enabled': True, 'revision': 0, 'client_id': 'fixture.apps.googleusercontent.com'}
        fetch.return_value = ({'keys': [{'kid': 'key', 'kty': 'RSA', 'n': 'modulus', 'e': 'AQAB'}]}, None)
        for provider in ('google', 'apple'):
            result = verify_integration(provider, 0)
            self.assertEqual(result['status'], 'warning')
            self.assertIn('prova di login', result['message'])
        fetch.return_value = ({'keys': []}, None)
        self.assertEqual(verify_integration('google', 0)['status'], 'failed')

    @patch('instance.integration_probe.requests.get')
    def test_network_reads_are_bounded_no_redirects_and_errors_are_sanitized(self, get):
        response = MagicMock()
        get.return_value.__enter__.return_value = response
        for code, expected in ((401, 'failed'), (403, 'failed'), (429, 'warning'), (302, 'failed'), (500, 'failed')):
            response.status_code = code
            data, error = get_json('https://provider.example.test', {'Authorization': 'Bearer secret'})
            self.assertIsNone(data)
            self.assertEqual(error['status'], expected)
            self.assertNotIn('secret', str(error))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertEqual(get.call_args.kwargs['timeout'], (3, 4))
        response.status_code = 200
        response.raw.read.return_value = b'x' * 262145
        self.assertEqual(get_json('https://provider.example.test')[1]['status'], 'failed')
