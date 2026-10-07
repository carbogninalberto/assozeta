"""Install partial Italian guides for the local development reader."""
import json
from django.core.management import BaseCommand, CommandError
from application.manuale.preview import install_preview


class Command(BaseCommand):
    help = 'Install a development-only draft reader preview without changing application data.'

    def add_arguments(self, parser):
        parser.add_argument('--manual-root', required=True)
        parser.add_argument('--run', required=True)

    def handle(self, *args, **options):
        try:
            result = install_preview(options['manual_root'], options['run'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result))
