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
