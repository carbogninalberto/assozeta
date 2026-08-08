from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings

from application.agent.providers.ai_provider import AIProvider


class AIProviderTests(SimpleTestCase):
    @override_settings(
        AI_API_KEY='test-key',
        AI_MODEL='test-model',
        AI_BASE_URL='https://ai.example.test/v1',
    )
    @patch('application.agent.providers.ai_provider.AsyncOpenAI')
    def test_uses_provider_neutral_settings(self, openai_client):
        provider = AIProvider()

        openai_client.assert_called_once_with(
            api_key='test-key',
            base_url='https://ai.example.test/v1',
        )
        self.assertEqual(provider.model, 'test-model')

    @override_settings(AI_API_KEY=None)
    def test_requires_api_key(self):
        with self.assertRaisesMessage(ValueError, 'AI_API_KEY is required'):
            AIProvider()

    @override_settings(AI_API_KEY=None, GROQ_API_KEY='legacy-key')
    def test_does_not_accept_legacy_groq_key(self):
        with self.assertRaisesMessage(ValueError, 'AI_API_KEY is required'):
            AIProvider()

    @override_settings(
        AI_API_KEY='test-key',
        AI_MODEL='test-model',
        AI_BASE_URL='https://ai.example.test/v1',
    )
    @patch('application.agent.providers.ai_provider.AsyncOpenAI')
    async def test_chat_uses_openai_compatible_completions(self, openai_client):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = 'Ciao'
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = 'stop'
        response.usage = None
        create = AsyncMock(return_value=response)
        openai_client.return_value.chat.completions.create = create

        result = await AIProvider().chat(
            messages=[{'role': 'user', 'content': 'Ciao'}],
            tools=[],
        )

        create.assert_awaited_once_with(
            model='test-model',
            messages=[{'role': 'user', 'content': 'Ciao'}],
            max_tokens=4096,
            temperature=0.1,
        )
        self.assertEqual(result.content, 'Ciao')
        self.assertEqual(result.finish_reason, 'stop')
