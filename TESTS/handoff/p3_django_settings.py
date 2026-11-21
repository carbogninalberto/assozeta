"""Django settings of the Phase 3 handoff driver (producer) and its Celery worker subprocesses.

The isolated contract settings (TESTS.adapters.django.settings) on the private broker, the
Celery default queue, the django-db result backend and no task routes (GO_TASK_QUEUE_TASKS
empty), so every Django task goes to ``celery`` exactly as an exclusive-mode installation
publishes. The connection's application_name tells the effect counter which process wrote.

Phase 3 settings consumed by the implementation (core.settings defaults them):
  ASSOZETA_HANDOFF_BARRIER_DIR      test-only file barrier directory ('' = no barriers)
  ASSOZETA_HANDOFF_HEARTBEAT_SECONDS consumer heartbeat / freeze-poll interval (default 1.0)
"""
import os

from TESTS.adapters.django.settings import *  # noqa: F401,F403
from TESTS.adapters.django.settings import DATABASES

_broker = os.environ.get('ASSOZETA_HANDOFF_BROKER_URL', '')
if not _broker.startswith('redis://') or '127.0.0.1' not in _broker:
    raise RuntimeError('ASSOZETA_HANDOFF_BROKER_URL must name the private broker')
CELERY_BROKER_URL = _broker
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_RESULT_BACKEND = 'django-db'
CELERY_TASK_ROUTES = None
DATABASES['default'].setdefault('OPTIONS', {})['application_name'] = os.environ.get('ASSOZETA_HANDOFF_APPLICATION',
                                                                                    'handoff-django')
ASSOZETA_HANDOFF_BARRIER_DIR = os.environ.get('ASSOZETA_HANDOFF_BARRIER_DIR', '')
ASSOZETA_HANDOFF_HEARTBEAT_SECONDS = 0.1

# The counting SMTP server of the stack (TESTS/handoff/p3_stack.py CountingSMTP): no TLS, no AUTH.
EMAIL_BACKEND = 'instance.email_configuration.EmailBackend'
EMAIL_HOST = '127.0.0.1'
EMAIL_PORT = int(os.environ.get('ASSOZETA_HANDOFF_SMTP_PORT') or 9)
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
EMAIL_USE_TLS = EMAIL_USE_SSL = False
DEFAULT_FROM_EMAIL = 'Handoff Fixture <noreply@example.test>'
# Test-only task 'fixture.early_ack' (early acknowledgement probe), never registered in production.
CELERY_IMPORTS = ['TESTS.handoff.p3_probe_tasks']
