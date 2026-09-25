"""Create an administrative account without inventing an association identity."""
import getpass
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction


class Command(BaseCommand):
    help = 'Create a Bakney administrator (Self Instance, data management and impersonation).'

    def add_arguments(self, parser):
        for field in ('username', 'email', 'first-name', 'last-name', 'phone'):
            parser.add_argument(f'--{field}')
        parser.add_argument('--noinput', action='store_true', help='Read the password from DJANGO_SUPERUSER_PASSWORD; username and email are required.')
        parser.add_argument('--allow-weak-password', action='store_true', help='Development only: explicitly allow a test password.')

    def handle(self, *args, **options):
        if options['allow_weak_password'] and getattr(settings, 'ASSOZETA_DEPLOYMENT_MODE', '') != 'development':
            raise CommandError('Weak test passwords are allowed only in development.')
        User = get_user_model()
        values = {}
        try:
            for field, label in (('username', 'Username'), ('email', 'Email'), ('first_name', 'First name (optional)'),
                                 ('last_name', 'Last name (optional)'), ('phone', 'Phone (optional)')):
                value = options.get(field)
                if value is None and not options['noinput']:
                    value = input(f'{label}: ')
                values[field] = (value or '').strip()
            values['username'] = values['username'].upper()
            if not values['username'] or not values['email']:
                raise CommandError('Username and email are required.')
            validate_email(values['email'])
            for field in ('username', 'email'):
                if User.original_objects.filter(**{f'{field}__iexact': values[field]}).exists():
                    raise CommandError(f'An account with this {field} already exists; no account was changed.')
            for field, value in values.items():
                User._meta.get_field(field).clean(value, None)
            user = User(**values, role=User.ATHLETE, is_active=True, is_staff=True, is_superuser=True)
            if options['noinput']:
                password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
            else:
                password = getpass.getpass('Password: ')
                if getpass.getpass('Password (again): ') != password:
                    raise CommandError('Passwords do not match.')
            if not password:
                raise CommandError('A nonempty password is required.')
            if not options['allow_weak_password']:
                validate_password(password, user)
            with transaction.atomic():
                user = User.objects.create_superuser(password=password, role=User.ATHLETE, **values)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        except IntegrityError as exc:
            raise CommandError('An account with these details already exists; no account was changed.') from exc
        except (EOFError, KeyboardInterrupt):
            raise CommandError('Account creation cancelled.') from None
        self.stdout.write(self.style.SUCCESS(f'Administrator {user.username} created. Sign in with the chosen username and password.'))
