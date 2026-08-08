import json
import logging

from django.conf import settings
from openai import AsyncOpenAI

from application.agent.providers.base import LLMResponse, OnChunkCallback, ToolCall

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class AIProvider:
    """OpenAI-compatible LLM provider implementation."""

    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        base_url: str = None,
    ):
        self.api_key = api_key or getattr(settings, 'AI_API_KEY', None)
        self.model = model or getattr(settings, 'AI_MODEL', 'deepseek-v4-flash')
        self.base_url = base_url or getattr(settings, 'AI_BASE_URL', 'https://api.deepseek.com')

        if not self.api_key:
            raise ValueError("AI_API_KEY is required")

        client_kwargs = {'api_key': self.api_key}
        if self.base_url:
            client_kwargs['base_url'] = self.base_url

        self.client = AsyncOpenAI(**client_kwargs)

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if the error is a tool validation failure that can be retried."""
        error_str = str(error)
        return 'tool_use_failed' in error_str or 'tool call validation failed' in error_str.lower()

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """Send a chat completion with retry on tool validation errors."""
        kwargs = {
            'model': self.model,
            'messages': messages,
            'max_tokens': 4096,
            'temperature': 0.1,
        }

        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                return self._parse_response(response)
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES and self._is_retryable_error(e):
                    logger.warning(f"AI tool validation error (attempt {attempt + 1}), retrying: {e}")
                    # Increase temperature slightly on retry to get different output
                    kwargs['temperature'] = 0.3 + (attempt * 0.1)
                    continue
                break

        logger.error("AI API error: %s", last_error)
        return LLMResponse(
            content=str(last_error),
            finish_reason='error',
        )

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        on_chunk: OnChunkCallback | None = None,
    ) -> LLMResponse:
        """Send a streaming completion, calling on_chunk for each content delta."""
        kwargs = {
            'model': self.model,
            'messages': messages,
            'max_tokens': 4096,
            'temperature': 0.1,
            'stream': True,
        }

        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                stream = await self.client.chat.completions.create(**kwargs)
                return await self._consume_stream(stream, on_chunk)
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES and self._is_retryable_error(e):
                    logger.warning(f"AI stream tool validation error (attempt {attempt + 1}), retrying: {e}")
                    kwargs['temperature'] = 0.3 + (attempt * 0.1)
                    continue
                break

        logger.error("AI streaming API error: %s", last_error)
        return LLMResponse(
            content=str(last_error),
            finish_reason='error',
        )

    async def _consume_stream(self, stream, on_chunk: OnChunkCallback | None) -> LLMResponse:
        """Forward stream content and accumulate tool calls."""
        content_parts: list[str] = []
        # tool_calls_acc: {index: {id, name, arguments_str}}
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = ''
        usage = {}

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                # Final chunk may carry usage only
                if chunk.usage:
                    usage = {
                        'prompt_tokens': chunk.usage.prompt_tokens,
                        'completion_tokens': chunk.usage.completion_tokens,
                        'total_tokens': chunk.usage.total_tokens,
                    }
                continue

            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            # Content delta
            if delta and delta.content:
                content_parts.append(delta.content)
                if on_chunk:
                    await on_chunk(delta.content)

            # Tool call deltas
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            'id': tc_delta.id or '',
                            'name': (tc_delta.function.name if tc_delta.function else '') or '',
                            'arguments': '',
                        }
                    acc = tool_calls_acc[idx]
                    if tc_delta.id:
                        acc['id'] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc['name'] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc['arguments'] += tc_delta.function.arguments

            # Usage in final chunk
            if chunk.usage:
                usage = {
                    'prompt_tokens': chunk.usage.prompt_tokens,
                    'completion_tokens': chunk.usage.completion_tokens,
                    'total_tokens': chunk.usage.total_tokens,
                }

        # Build tool calls
        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_calls_acc):
            acc = tool_calls_acc[idx]
            try:
                args = json.loads(acc['arguments']) if acc['arguments'] else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(ToolCall(id=acc['id'], name=acc['name'], arguments=args))

        return LLMResponse(
            content=''.join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _parse_response(self, response) -> LLMResponse:
        """Normalize an OpenAI-compatible chat completion response."""
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Parse tool calls
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if response.usage:
            usage = {
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content or '',
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )
