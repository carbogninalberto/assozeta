import json
import logging
import sys
from typing import Protocol

logger = logging.getLogger(__name__)


class AgentCallback(Protocol):
    """Protocol for agent event callbacks."""

    async def on_status(self, status: str) -> None:
        """Agent status update (e.g. 'thinking', 'querying')."""
        ...

    async def on_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Agent is calling a tool."""
        ...

    async def on_message(self, message: str) -> None:
        """Agent text response (complete, non-streamed)."""
        ...

    async def on_message_chunk(self, chunk: str) -> None:
        """A single streamed token/chunk of the agent's response."""
        ...

    async def on_message_end(self) -> None:
        """End of a streamed message segment."""
        ...

    async def on_export_ready(self, export_data: dict) -> None:
        """An export file is ready for download."""
        ...

    async def on_report_saved(self, report_data: dict) -> None:
        """A report configuration was saved."""
        ...

    async def on_error(self, error: str) -> None:
        """An error occurred."""
        ...

    async def on_done(self) -> None:
        """Agent has finished processing."""
        ...


class CLICallback:
    """Callback implementation that prints to stdout for CLI usage."""

    async def on_status(self, status: str) -> None:
        print(f"\033[90m[{status}]\033[0m", flush=True)

    async def on_tool_call(self, tool_name: str, arguments: dict) -> None:
        args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
        print(f"\033[36m>> {tool_name}\033[0m", flush=True)
        # Truncate long arguments
        if len(args_str) > 500:
            args_str = args_str[:500] + '...'
        print(f"\033[90m{args_str}\033[0m", flush=True)

    async def on_message(self, message: str) -> None:
        print(f"\n\033[32m{message}\033[0m\n", flush=True)

    async def on_message_chunk(self, chunk: str) -> None:
        print(f"\033[32m{chunk}\033[0m", end='', flush=True)

    async def on_message_end(self) -> None:
        print(flush=True)

    async def on_export_ready(self, export_data: dict) -> None:
        filename = export_data.get('filename', 'export')
        row_count = export_data.get('row_count', 0)
        print(f"\033[33m[Export] {filename} ({row_count} righe)\033[0m", flush=True)

    async def on_report_saved(self, report_data: dict) -> None:
        name = report_data.get('name', 'report')
        print(f"\033[33m[Report salvato] {name}\033[0m", flush=True)

    async def on_error(self, error: str) -> None:
        print(f"\033[31m[Errore] {error}\033[0m", file=sys.stderr, flush=True)

    async def on_done(self) -> None:
        pass
