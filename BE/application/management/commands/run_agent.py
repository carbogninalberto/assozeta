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
        from application.agent.providers.groq_provider import GroqProvider

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

        provider = GroqProvider()
        callback = CLICallback()
        agent = Agent(
            sport_association_id=str(sa.sport_association_id),
            sport_association_name=sa.denomination,
            provider=provider,
            callback=callback,
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

            await agent.process_message(user_input)
