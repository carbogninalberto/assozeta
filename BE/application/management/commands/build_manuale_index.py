import json
from pathlib import Path

from django.core.management import BaseCommand, CommandError

from application.manuale.index import EvidenceError, build_index, promote


class Command(BaseCommand):
    help = 'Validate code/browser evidence and atomically replace the manual retrieval index.'

    def add_arguments(self, parser):
        parser.add_argument('--manual-root', required=True)
        parser.add_argument('--code-root', required=True)
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--artifact-root')
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        try:
            manifest = json.loads(Path(options['manifest']).read_text())
            result = build_index(options['manual_root'], options['code_root'], manifest,
                                 artifact_root=options['artifact_root'])
            promote(result, options['output'])
        except (EvidenceError, OSError, ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Indexed {len(result["chunks"])} verified sections; {len(result["gaps"])} explicit gaps. Identity: {result["identity"]}')
