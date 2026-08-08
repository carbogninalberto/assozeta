from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

OnChunkCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCall:
    """Represents a tool call requested by the LLM."""
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    content: str = ''
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ''
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(Protocol):
    """Protocol for normalized LLM provider responses."""

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """Send a chat completion request with tool definitions.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Tool definitions in OpenAI-compatible JSON format.

        Returns:
            Normalized LLMResponse.
        """
        ...

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        on_chunk: OnChunkCallback | None = None,
    ) -> LLMResponse:
        """Send a streaming chat completion request.

        Streams content tokens via on_chunk callback as they arrive,
        while accumulating tool calls. Returns the complete LLMResponse.
        """
        ...
