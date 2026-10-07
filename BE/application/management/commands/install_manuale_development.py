"""Install verified local manual assets without touching application records."""
import json

from django.core.management import BaseCommand, CommandError

from application.manuale.development import install_development_run


class Command(BaseCommand):
    help = 'Install a completed local verification run in the ordinary development reader and MCP.'

    def add_arguments(self, parser):
        parser.add_argument('--run', required=True)

    def handle(self, *args, **options):
        try:
            result = install_development_run(options['run'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
