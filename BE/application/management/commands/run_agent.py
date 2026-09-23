import asyncio

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run an interactive CLI chat with the AI agent'

    def add_arguments(self, parser):
        parser.add_argument(
            '--association-id',
            type=str,
            required=True,
            help='Sport association UUID',
        )

    def handle(self, *args, **options):
        association_id = options['association_id']
        asyncio.run(self._run(association_id))

    async def _run(self, association_id: str):
        from application.models import SportAssociation
        from application.agent.core import Agent
        from application.agent.callbacks import CLICallback
        from application.agent.providers.ai_provider import AIProvider

        try:
            sa = await asyncio.to_thread(
                SportAssociation.objects.get,
                sport_association_id=association_id,
            )
        except SportAssociation.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                f'Sport association {association_id} not found'
            ))
            return

        from asgiref.sync import sync_to_async
        from instance.integration_configuration import effective_integration
        config = await sync_to_async(effective_integration)('ai')
        if not config['enabled']:
            self.stderr.write('Il bot AI è disattivato per questa istanza.')
            return
        provider = AIProvider(api_key=config['api_key'], model=config['model'], base_url=config['base_url'])
        callback = CLICallback()
        agent = Agent(
            sport_association_id=str(sa.sport_association_id),
            sport_association_name=sa.denomination,
            provider=provider,
            callback=callback,
            ai_config=config,
        )

        self.stdout.write(self.style.SUCCESS(
            f'\nAgent CLI per "{sa.denomination}"'
        ))
        self.stdout.write('Scrivi un messaggio o "exit" per uscire.\n')

        while True:
            try:
                user_input = input('\n> ')
            except (EOFError, KeyboardInterrupt):
                break

            if user_input.strip().lower() in ('exit', 'quit', 'esci'):
                break

            if user_input.strip().lower() in ('clear', 'reset', 'pulisci'):
                agent.clear_history()
                self.stdout.write(self.style.SUCCESS('Cronologia cancellata.'))
                continue

            if not user_input.strip():
                continue

            config = await sync_to_async(effective_integration)('ai')
            if not config['enabled']:
                self.stderr.write('Il bot AI è disattivato per questa istanza.')
                continue
            agent.provider = AIProvider(api_key=config['api_key'], model=config['model'], base_url=config['base_url'])
            agent.max_iterations = config['max_iterations']
            agent.history_cap = config['history_cap']
            await agent.process_message(user_input)
