import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.integration_configuration import effective_integration
from instance.models import InstanceConfiguration


@override_settings(AI_API_KEY='environment-ai-secret')
class AIConfigurationTests(TestCase):
    endpoint = '/instance/admin/integrations/ai'

    def setUp(self):
        self.owner = User.objects.create_user(username='ai-owner', role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='AI Club')
        self.config = InstanceConfiguration.objects.create(domain='ai.example.test', name='AI Club',
            primary_association=association, self_hosted=True)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def payload(self, **changes):
        config = effective_integration('ai')
        return {key: value for key, value in {**config, **changes}.items() if key not in ('source', 'api_key')}

    def test_all_settings_round_trip_secret_retention_disable_and_reset(self):
        initial = self.client.get(self.endpoint)
        self.assertTrue(initial.data['enabled'])
        self.assertTrue(initial.data['api_key_configured'])
        self.assertNotIn('environment-ai-secret', initial.content.decode())
        self.assertEqual(initial['Cache-Control'], 'no-store')
        values = dict(model='custom-model', cheap_model='custom-small', base_url='https://provider.example.test/v1',
                      max_iterations=7, max_results=123, query_timeout=9, ws_rate_limit=4, ws_timeout=120, history_cap=24)
        response = self.client.put(self.endpoint, self.payload(**values), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for key, value in values.items():
            self.assertEqual(self.client.get(self.endpoint).data[key], value)
        self.config.refresh_from_db()
        self.assertNotIn('environment-ai-secret', json.dumps(self.config.integration_settings))
        self.assertEqual(effective_integration('ai')['api_key'], 'environment-ai-secret')
        response = self.client.put(self.endpoint, self.payload(enabled=False), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(effective_integration('ai')['enabled'])
        self.assertFalse(self.client.get('/instance/config').data['features']['aiEnabled'])
        response = self.client.put(self.endpoint, {**self.payload(enabled=True), 'api_key': 'replacement-secret'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(effective_integration('ai')['api_key'], 'replacement-secret')
        public = self.client.get('/instance/config')
        self.assertTrue(public.data['features']['aiEnabled'])
        self.assertNotIn('replacement-secret', public.content.decode())
        self.assertNotIn('replacement-secret', response.content.decode())
        revision = response.data['revision']
        response = self.client.delete(self.endpoint, {'revision': revision}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source'], 'environment')
        self.assertEqual(effective_integration('ai')['api_key'], 'environment-ai-secret')

    def test_empty_provider_url_can_be_saved(self):
        response = self.client.put(self.endpoint, self.payload(base_url=''), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(effective_integration('ai')['base_url'], '')

    def test_validation_conflicts_and_explicit_secret_removal(self):
        for changes in ({'max_iterations': 0}, {'max_results': 100001}, {'history_cap': 1},
                        {'query_timeout': 0}, {'ws_timeout': 3601}, {'ws_rate_limit': 0},
                        {'base_url': 'ftp://example.test'}, {'model': ''},
                        {'base_url': 'https://user:secret@example.test'},
                        {'clear_secrets': ['api_key']}):
            response = self.client.put(self.endpoint, self.payload(**changes), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.put(self.endpoint, self.payload(enabled=False, clear_secrets=['api_key']), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['api_key_configured'])
        self.assertEqual(self.client.put(self.endpoint, self.payload(revision=0), format='json').status_code, 409)
        self.assertEqual(self.client.delete(self.endpoint, {'revision': 0}, format='json').status_code, 409)

    def test_owner_only_and_hosted_instances_forbidden(self):
        unrelated = User.objects.create_user(username='ai-unrelated', is_superuser=True)
        for user in (None, unrelated):
            self.client.force_authenticate(user)
            for method in ('get', 'put', 'delete'):
                self.assertIn(getattr(self.client, method)(self.endpoint, {}, format='json').status_code, (401, 403))
        self.client.force_authenticate(self.owner)
        self.config.self_hosted = False
        self.config.save(update_fields=['self_hosted'])
        self.assertEqual(self.client.get(self.endpoint).status_code, 403)

    def test_query_builder_uses_saved_limits(self):
        from application.mcp_server.query_builder import QueryBuilder
        self.client.put(self.endpoint, self.payload(max_results=123, query_timeout=7), format='json')
        builder = QueryBuilder(str(self.config.primary_association_id))
        self.assertEqual((builder.max_results, builder.query_timeout), (123, 7))

    @patch('channels.db.close_old_connections')
    def test_existing_socket_rechecks_toggle_and_provider_settings(self, close_connections):
        from application.chat.consumers import AgentConsumer
        consumer = AgentConsumer()
        consumer.ai_config = effective_integration('ai')
        consumer.agent = MagicMock()
        consumer.throttle = MagicMock()
        consumer.send_json = AsyncMock()
        consumer._run_agent_with_timeout = AsyncMock()
        self.client.put(self.endpoint, self.payload(enabled=False), format='json')
        async_to_sync(consumer._handle_user_message)({'message': 'hello'})
        consumer.send_json.assert_awaited_once()
        consumer._run_agent_with_timeout.assert_not_awaited()
        self.client.put(self.endpoint, self.payload(enabled=True, model='new-model', max_iterations=8, history_cap=22, ws_rate_limit=6, ws_timeout=44), format='json')
        async def run():
            with patch('application.agent.providers.ai_provider.AIProvider') as provider:
                await consumer._handle_user_message({'message': 'hello'})
                await consumer.agent_task
                self.assertEqual(provider.call_args.kwargs['model'], 'new-model')
        async_to_sync(run)()
        self.assertEqual(consumer.agent.max_iterations, 8)
        self.assertEqual(consumer.agent.history_cap, 22)
        self.assertEqual(consumer.throttle.max_messages, 6)
        consumer._run_agent_with_timeout.assert_awaited_once_with('hello', 44)

    @patch('channels.db.close_old_connections')
    def test_disabled_bot_rejects_new_socket(self, close_connections):
        from application.chat.consumers import AgentConsumer
        self.client.put(self.endpoint, self.payload(enabled=False), format='json')
        consumer = AgentConsumer()
        consumer.scope = {'user': self.owner}
        consumer.close = AsyncMock()
        async_to_sync(consumer.connect)()
        consumer.close.assert_awaited_once_with(code=4004)
