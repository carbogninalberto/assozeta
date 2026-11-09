"""Settings of the Django prefork worker the consumer-restore harnesses start: a private broker, the Celery default
queue and, when CONSUMER_RESTORE_PRODUCTION_SERVICES is set, the production cache (django-redis, database 1), channel
layer and S3 default storage on the harness's private Redis and MinIO. Everything else is the isolated contract
settings module."""
import os

from TESTS.adapters.django.settings import *  # noqa: F401,F403
from TESTS.adapters.django.settings import CACHES, CHANNEL_LAYERS, STORAGES  # noqa: F401

_broker = os.environ.get('CONSUMER_RESTORE_BROKER_URL', '')
if not _broker.startswith('redis://') or '127.0.0.1' not in _broker:
    raise RuntimeError('CONSUMER_RESTORE_BROKER_URL must name the private broker')
CELERY_BROKER_URL = _broker
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_RESULT_BACKEND = 'django-db'

if os.environ.get('CONSUMER_RESTORE_PRODUCTION_SERVICES'):
    _port = os.environ['CONSUMER_RESTORE_REDIS_PORT']
    _password = os.environ['CONSUMER_RESTORE_REDIS_PASSWORD']
    if not _port or not _password:
        raise RuntimeError('private Redis port and password are required')
    CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{_port}/1',
                          'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': _password}}}
    from core.settings import CHANNEL_LAYERS as _production_layers
    CHANNEL_LAYERS = {'default': {**_production_layers['default'], 'CONFIG': {
        **_production_layers['default'].get('CONFIG', {}),
        'hosts': [{'address': f'redis://127.0.0.1:{_port}/0', 'password': _password}]}}}
    STORAGES = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
    AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
    AWS_STORAGE_BUCKET_NAME = os.environ['AWS_STORAGE_BUCKET_NAME']
    AWS_S3_ENDPOINT_URL = os.environ['AWS_S3_ENDPOINT_URL']
    AWS_S3_REGION_NAME = 'us-east-1'
    AWS_LOCATION = os.environ['AWS_LOCATION']
    AWS_S3_ADDRESSING_STYLE = 'path'
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
