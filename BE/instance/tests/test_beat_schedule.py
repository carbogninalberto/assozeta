from django.conf import settings
from django.test import SimpleTestCase

from core.celery import app


class BeatScheduleTests(SimpleTestCase):
    """A CELERY_BEAT_SCHEDULE setting replaces the schedule in core/celery.py."""

    def test_settings_do_not_override_the_schedule(self):
        self.assertFalse(hasattr(settings, 'CELERY_BEAT_SCHEDULE'))

    def test_schedule_keeps_maintenance_and_business_tasks(self):
        schedule = app.conf.beat_schedule
        for name in ('bakney-pairing-maintenance', 'cleanup-data-restores',
                     'renew-memberships-payments', 'send-reminders'):
            self.assertIn(name, schedule)

    def test_every_scheduled_task_is_registered(self):
        app.loader.import_default_modules()
        missing = {entry['task'] for entry in app.conf.beat_schedule.values()} - set(app.tasks)
        self.assertEqual(missing, set())
