import asyncio

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run the MCP server for Bakney Sport data access'

    def add_arguments(self, parser):
        parser.add_argument(
            '--transport',
            type=str,
            default='stdio',
            choices=['stdio', 'sse'],
            help='Transport type (default: stdio)',
        )
        parser.add_argument(
            '--host',
            type=str,
            default='0.0.0.0',
            help='Host for SSE transport (default: 0.0.0.0)',
        )
        parser.add_argument(
            '--port',
            type=int,
            default=8081,
            help='Port for SSE transport (default: 8081)',
        )
        parser.add_argument(
            '--association-id',
            type=str,
            required=True,
            help='Sport association UUID to scope queries to',
        )

    def handle(self, *args, **options):
        transport = options['transport']
        association_id = options['association_id']

        if transport == 'stdio':
            from application.mcp_server.server import run_stdio_server
            self.stdout.write(self.style.SUCCESS(
                f'Starting MCP server (stdio) for association {association_id}'
            ))
            asyncio.run(run_stdio_server(association_id))
        elif transport == 'sse':
            self.stdout.write(self.style.WARNING(
                'SSE transport: use the MCP server directly with an SSE adapter. '
                'For now, use stdio transport.'
            ))
