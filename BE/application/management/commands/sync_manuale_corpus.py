from django.core.management import BaseCommand, CommandError

from application.manuale.sync import synchronize


class Command(BaseCommand):
    help = 'Download and atomically promote the published corpus for the running code revision/release.'

    def handle(self, *args, **options):
        try:
            result = synchronize()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError('Published corpus unavailable or incompatible; previous index retained.') from exc
        self.stdout.write('Published manual corpus updated: ' + result['corpus_identity'])
