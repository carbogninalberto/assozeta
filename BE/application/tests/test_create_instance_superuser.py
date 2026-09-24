from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from application.models import User


class CreateAdministratorTests(TestCase):
    def create(self, **options):
        values = {'username': 'admin-test', 'email': 'admin@example.test',
                  'first_name': 'Test', 'last_name': 'Administrator', 'phone': '+390000000000',
                  'noinput': True, 'stdout': StringIO()}
        values.update(options)
        with patch.dict('os.environ', DJANGO_SUPERUSER_PASSWORD='A-long-Test-password-491!'):
            call_command('create_instance_superuser', **values)

    def test_creates_usable_administrator_with_chosen_profile(self):
        self.create()
        user = User.objects.get(username='ADMIN-TEST')
        self.assertTrue(user.is_active and user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password('A-long-Test-password-491!'))
        self.assertEqual(user.email, 'admin@example.test')
        self.assertEqual(user.first_name, 'Test')
        self.assertEqual(user.phone, '+390000000000')

    def test_duplicate_identity_is_not_modified(self):
        user = User.objects.create_user(username='Admin-Test', email='existing@example.test')
        with self.assertRaisesMessage(CommandError, 'username already exists'):
            self.create()
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)

    def test_email_duplicates_and_invalid_fields_are_rejected(self):
        User.objects.create_user(username='other', email='ADMIN@example.test')
        with self.assertRaisesMessage(CommandError, 'email already exists'):
            self.create()
        with self.assertRaises(CommandError):
            self.create(email='not-an-email')

    @override_settings(ASSOZETA_DEPLOYMENT_MODE='production')
    def test_production_cannot_bypass_password_validation(self):
        with self.assertRaisesMessage(CommandError, 'only in development'):
            self.create(allow_weak_password=True)

    def test_interactive_password_is_hidden_and_confirmed(self):
        with patch('builtins.input', side_effect=['interactive', 'test@example.test', 'Test', 'User', '']), \
                patch('getpass.getpass', side_effect=['first', 'different']) as hidden:
            with self.assertRaisesMessage(CommandError, 'Passwords do not match'):
                call_command('create_instance_superuser', stdout=StringIO())
        self.assertEqual(hidden.call_count, 2)
        self.assertFalse(User.objects.filter(username='INTERACTIVE').exists())
