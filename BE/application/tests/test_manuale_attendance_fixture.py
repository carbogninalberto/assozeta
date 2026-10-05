"""The attendance matrix coexists with the reset baseline and cleans only itself."""
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from freezegun import freeze_time

from application.management.commands.run_manuale_attendance import Command, CASES
from application.management.commands.seed_manuale import fixture_id
from application.models import User, SportAssociation, Associate, Subscription, Course, Payment
from application.tests.base import BaseTestCase


class ManualAttendanceFixtureTests(BaseTestCase):
    def test_real_preparation_retains_duplicate_protection_and_cleanup_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'), \
                patch('redis.Redis'), patch('notifications.services.NotificationService.invalidate_user_broadcasts_cache'):
            call_command('seed_manuale', output=str(Path(temporary)/'browser.json'),
                origin='http://127.0.0.1:5010', verbosity=0)
        models = (User, SportAssociation, Associate, Subscription, Course, Payment)
        baseline = {model: list(model._base_manager.order_by('pk').values()) for model in models}
        command = Command()
        command.reference = date(2026, 9, 30)
        command.when = datetime.combine(command.reference, time(12), timezone.utc)
        command.owner = User.objects.get(pk=fixture_id('owner'))
        command.association = SportAssociation.objects.get(pk=fixture_id('association'))
        with freeze_time(command.when):
            data = command.prepare()
        self.assertEqual([row['key'] for row in data['cases']], [case[0] for case in CASES])
        self.assertEqual(len(data['cases']), 8)
        for row in data['cases']:
            person = Associate.objects.get(pk=row['person_id'])
            self.assertEqual(Subscription.objects.get(pk=row['subscription_id']).associate_id, person.pk)
            self.assertFalse(Subscription.objects.subscription_exists(
                sport_association=command.association,
                associate_data={'first_name': person.first_name, 'last_name': person.last_name, 'tax_code': 'OTHER'},
                start_date=command.reference, end_date=command.reference))
            self.assertTrue(Subscription.objects.subscription_exists(
                sport_association=command.association, associate=person,
                start_date=command.reference, end_date=command.reference))
        command.validate(data)
        command.cleanup(data)
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model._base_manager.order_by('pk').values()), baseline[model])
