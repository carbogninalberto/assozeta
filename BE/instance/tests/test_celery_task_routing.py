"""GO_TASK_QUEUE_TASKS routing: off by default, opt-in per task name."""
import os

from celery import Celery
from django.conf import settings
from django.test import SimpleTestCase

from core.celery import app
from core.task_routing import GO_TASK_QUEUE, go_task_names, go_task_routes


class GoTaskRoutingTests(SimpleTestCase):
    def test_empty_value_defines_no_routes(self):
        for value in ('', None, ' , ', ','):
            self.assertIsNone(go_task_routes(value))
            self.assertEqual(go_task_names(value), [])

    def test_default_configuration_keeps_every_task_on_the_celery_queue(self):
        if os.environ.get('GO_TASK_QUEUE_TASKS'):
            self.skipTest('GO_TASK_QUEUE_TASKS is set in this environment')
        self.assertFalse(hasattr(settings, 'CELERY_TASK_ROUTES'))
        self.assertIsNone(app.conf.task_routes)
        for name in ('core.tasks.send_mail_async', 'send_email_text', 'check_workflows_trigger'):
            route = app.amqp.router.route({}, name, (), {})
            self.assertEqual(route['queue'].name, 'celery')

    def test_listed_names_go_to_the_go_queue(self):
        routes = go_task_routes(' core.tasks.send_mail_async ,send_email_text,')
        self.assertEqual(routes, {'core.tasks.send_mail_async': {'queue': GO_TASK_QUEUE},
                                  'send_email_text': {'queue': GO_TASK_QUEUE}})
        routed = Celery('routing-test', set_as_current=False)
        routed.conf.task_routes = routes
        self.assertEqual(routed.amqp.router.route({}, 'core.tasks.send_mail_async', (), {})['queue'].name, GO_TASK_QUEUE)
        self.assertEqual(routed.amqp.router.route({}, 'send_reminders', (), {})['queue'].name, 'celery')
        # An explicit queue option (a retry's signature_from_request) keeps the queue the message came from.
        self.assertEqual(routed.amqp.router.route({'queue': 'celery'}, 'core.tasks.send_mail_async', (), {})['queue'].name, 'celery')
