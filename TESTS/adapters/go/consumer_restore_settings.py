"""Settings of the Django prefork worker the consumer-restore harnesses start (a private broker, the Celery
default queue). Everything else is the isolated contract settings module."""
import os

from TESTS.adapters.django.settings import *  # noqa: F401,F403

_broker = os.environ.get('CONSUMER_RESTORE_BROKER_URL', '')
if not _broker.startswith('redis://') or '127.0.0.1' not in _broker:
    raise RuntimeError('CONSUMER_RESTORE_BROKER_URL must name the private broker')
CELERY_BROKER_URL = _broker
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_RESULT_BACKEND = 'django-db'
