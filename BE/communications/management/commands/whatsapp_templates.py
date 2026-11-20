import json
import sys

from django.core.management.base import BaseCommand, CommandError

from communications.whatsapp_provider import CloudAPI, ProviderError


class Command(BaseCommand):
    help = 'List/get approved WhatsApp templates, or register a Meta definition from stdin.'

    def add_arguments(self, parser):
        commands = parser.add_subparsers(dest='action', required=True)
        commands.add_parser('list')
        commands.add_parser('register')
        get = commands.add_parser('get')
        get.add_argument('name')
        get.add_argument('language')

    def handle(self, action, **options):
        provider = CloudAPI()
        try:
            if action == 'list':
                result = provider.list_templates()
            elif action == 'get':
                result = provider.get_template(options['name'], options['language'])
            else:
                definition = json.load(sys.stdin)
                if not isinstance(definition, dict):
                    raise ValueError
                result = provider.register_template(definition)
        except ValueError:
            raise CommandError('Expected a JSON template object on stdin') from None
        except ProviderError as error:
            raise CommandError(f'{error.code}: {error}') from None
        return json.dumps(result, ensure_ascii=False)
