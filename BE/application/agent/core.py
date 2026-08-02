"""
Agent core: the agentic loop that processes user messages,
calls LLM, executes tools, and repeats until done.
"""
import asyncio
import html
import json
import logging

from django.conf import settings

from application.agent.callbacks import AgentCallback
from application.agent.prompts import get_system_prompt
from application.agent.providers.base import LLMProvider, LLMResponse
from application.mcp_server.server import (
    TOOL_DEFINITIONS,
    tool_get_schema,
    tool_query_data,
    tool_count_data,
    tool_get_field_values,
    tool_export_data,
    tool_aggregate_data,
    tool_get_attendance_matrix,
    tool_export_multi_sheet,
    tool_sanitize_text,
    tool_save_report,
    tool_list_reports,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000

# Map tool names to their Python functions
TOOL_FUNCTIONS = {
    'get_schema': tool_get_schema,
    'query_data': tool_query_data,
    'count_data': tool_count_data,
    'get_field_values': tool_get_field_values,
    'export_data': tool_export_data,
    'aggregate_data': tool_aggregate_data,
    'get_attendance_matrix': tool_get_attendance_matrix,
    'export_multi_sheet': tool_export_multi_sheet,
    'sanitize_text': tool_sanitize_text,
    'save_report': tool_save_report,
    'list_reports': tool_list_reports,
}

# OpenAI-compatible tool definitions
TOOLS_FOR_LLM = [
    {
        'type': 'function',
        'function': {
            'name': t['name'],
            'description': t['description'],
            'parameters': t['parameters'],
        },
    }
    for t in TOOL_DEFINITIONS
]


def _sanitize_input(text: str) -> str:
    """Sanitize user input: strip, truncate, escape HTML."""
    text = text.strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH]
    return html.escape(text)


def _truncate_tool_result(result: str, max_length: int = 15000) -> str:
    """Truncate tool results that are too large for the LLM context."""
    if len(result) <= max_length:
        return result
    return result[:max_length] + '\n\n... [risultato troncato, usa filtri più specifici o limit per ridurre i dati]'


class Agent:
    """AI agent that processes user messages using LLM + tools."""

    def __init__(
        self,
        sport_association_id: str,
        sport_association_name: str,
        provider: LLMProvider,
        callback: AgentCallback,
        user_id: str = None,
    ):
        self.sport_association_id = str(sport_association_id)
        self.sport_association_name = sport_association_name
        self.user_id = str(user_id) if user_id else None
        self.provider = provider
        self.callback = callback
        self.max_iterations = getattr(settings, 'MCP_AGENT_MAX_ITERATIONS', 10)
        self.history_cap = getattr(settings, 'MCP_AGENT_HISTORY_CAP', 50)

        self._schema_cache = None
        self._recent_tool_calls = []  # Track recent tool call signatures for loop detection

        # Initialize message history with system prompt
        self.messages = [
            {
                'role': 'system',
                'content': get_system_prompt(sport_association_name),
            }
        ]

    def clear_history(self):
        """Reset conversation history, keeping only the system prompt."""
        self.messages = [self.messages[0]]
        # Keep schema cache across history clears

    def _trim_history(self):
        """Trim history to stay under the cap, keeping system prompt."""
        if len(self.messages) <= self.history_cap:
            return
        # Keep system prompt + most recent messages
        system = self.messages[0]
        recent = self.messages[-(self.history_cap - 1):]
        self.messages = [system] + recent

    def _is_stuck_in_loop(self) -> bool:
        """Check if the last 3 tool calls are identical (same tool + same args)."""
        calls = self._recent_tool_calls
        if len(calls) < 3:
            return False
        return calls[-1] == calls[-2] == calls[-3]

    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool function and return the result as JSON string."""
        func = TOOL_FUNCTIONS.get(tool_name)
        if not func:
            return json.dumps({'error': f'Unknown tool: {tool_name}'})

        # Return cached full schema if available (avoids re-sending large schema on every turn)
        # Only cache the full schema (no model_name), not model-specific requests
        if tool_name == 'get_schema' and self._schema_cache is not None:
            if not arguments.get('model_name'):
                return self._schema_cache

        # Strip None values from arguments (LLMs sometimes send null for optional params)
        arguments = {k: v for k, v in arguments.items() if v is not None}

        # Inject sport_association_id (and user_id for tools that need it)
        arguments['sport_association_id'] = self.sport_association_id
        if self.user_id and tool_name in ('save_report', 'list_reports'):
            arguments['user_id'] = self.user_id

        try:
            # Run sync Django ORM calls in a thread
            result = await asyncio.to_thread(func, **arguments)
            result_json = json.dumps(result, default=str, ensure_ascii=False)

            # Cache full schema result (not model-specific ones)
            if tool_name == 'get_schema' and not arguments.get('model_name'):
                self._schema_cache = result_json

            return result_json
        except Exception as e:
            logger.warning(f"Tool execution error: {tool_name} - {e}")
            error_msg = str(e)

            # Enhance field resolution errors with hints
            if 'Cannot resolve keyword' in error_msg and 'Choices are:' in error_msg:
                error_msg += (
                    '. HINT: To access fields on related models, use double underscore '
                    'traversal (e.g. subscription__associate__first_name). '
                    'Check get_schema for the correct relation names.'
                )

            return json.dumps({'error': error_msg})

    async def process_message(self, user_message: str) -> None:
        """Process a user message through the agent loop.

        This is the main entry point. It:
        1. Adds the user message to history
        2. Calls the LLM
        3. If LLM returns tool calls, executes them and loops
        4. If LLM returns text, sends it to the callback
        5. Repeats until LLM is done or max iterations reached
        """
        # Sanitize and add user message
        sanitized = _sanitize_input(user_message)
        self.messages.append({
            'role': 'user',
            'content': sanitized,
        })

        self._trim_history()
        self._recent_tool_calls.clear()
        pending_exports: list[dict] = []
        pending_report_saves: list[dict] = []
        await self.callback.on_status('thinking')

        for iteration in range(self.max_iterations):
            # Call LLM with streaming
            response: LLMResponse = await self.provider.chat_stream(
                messages=self.messages,
                tools=TOOLS_FOR_LLM,
                on_chunk=self.callback.on_message_chunk,
            )

            if response.finish_reason == 'error':
                logger.error(f"LLM error: {response.content}")
                await self.callback.on_error(
                    "Si e' verificato un problema temporaneo. Riprova tra qualche secondo."
                )
                break

            # Signal end of streamed content (if any was sent)
            if response.content:
                await self.callback.on_message_end()

            if response.has_tool_calls:
                # Add assistant message with tool calls to history
                assistant_msg = {
                    'role': 'assistant',
                    'content': response.content or None,
                    'tool_calls': [
                        {
                            'id': tc.id,
                            'type': 'function',
                            'function': {
                                'name': tc.name,
                                'arguments': json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                self.messages.append(assistant_msg)

                # Execute each tool call
                for tc in response.tool_calls:
                    await self.callback.on_status('querying')
                    await self.callback.on_tool_call(tc.name, tc.arguments)

                    result = await self._execute_tool(tc.name, tc.arguments)
                    truncated_result = _truncate_tool_result(result)

                    # Track tool call signature for loop detection
                    sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                    self._recent_tool_calls.append(sig)

                    # Add tool result to history
                    self.messages.append({
                        'role': 'tool',
                        'tool_call_id': tc.id,
                        'content': truncated_result,
                    })

                    # Collect exports for deferred delivery (after final text response)
                    if tc.name in ('export_data', 'get_attendance_matrix', 'export_multi_sheet'):
                        try:
                            export_result = json.loads(result)
                            if 'data_base64' in export_result:
                                pending_exports.append(export_result)
                        except (json.JSONDecodeError, KeyError):
                            pass

                    # Collect saved reports for deferred delivery
                    if tc.name == 'save_report':
                        try:
                            save_result = json.loads(result)
                            if 'saved_report_id' in save_result:
                                pending_report_saves.append(save_result)
                        except (json.JSONDecodeError, KeyError):
                            pass

                # Detect loop: same tool+args called 3+ times in last 3 calls
                if self._is_stuck_in_loop():
                    logger.warning(
                        f"Breaking tool loop after 3 identical calls: "
                        f"{self._recent_tool_calls[-1][:120]}"
                    )
                    self._recent_tool_calls.clear()
                    # Force LLM to respond with text (no tools, streaming)
                    forced = await self.provider.chat_stream(
                        messages=self.messages + [{
                            'role': 'user',
                            'content': (
                                '[SISTEMA: Stai ripetendo la stessa operazione. '
                                'FERMATI e rispondi all\'utente con le informazioni che hai. '
                                'Se non hai dati sufficienti, spiega il problema e suggerisci '
                                'di riformulare la domanda. NON chiamare strumenti.]'
                            ),
                        }],
                        tools=[],  # No tools — forces text response
                        on_chunk=self.callback.on_message_chunk,
                    )
                    if forced.content:
                        await self.callback.on_message_end()
                        self.messages.append({
                            'role': 'assistant',
                            'content': forced.content,
                        })
                    break

                # Continue the loop to let LLM process tool results
                continue

            else:
                # No tool calls - this is the final text response
                if response.content:
                    self.messages.append({
                        'role': 'assistant',
                        'content': response.content,
                    })
                break

        else:
            # Max iterations reached
            await self.callback.on_error(
                'Ho raggiunto il limite massimo di operazioni. '
                'Prova a riformulare la richiesta in modo più specifico.'
            )

        # Send all deferred exports after the final text response
        for export_data in pending_exports:
            await self.callback.on_export_ready(export_data)

        # Notify frontend about saved reports
        for report_data in pending_report_saves:
            await self.callback.on_report_saved(report_data)

        await self.callback.on_done()
