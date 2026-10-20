"""Isolated server settings. Used only by the disposable contract-test adapter."""
import os
# Choose production-style middleware before core.settings builds its app lists.
os.environ['DEBUG'] = 'False'
# Direct imports from core.settings must not resolve developer provider hosts.
os.environ['CURRENT_HOST'] = 'http://127.0.0.1:9'
os.environ['CURRENT_HOST_PUPPETEER'] = 'http://127.0.0.1:9'
from core.settings import *  # noqa: F403

database_name = os.environ['TESTS_DATABASE']
if not database_name.startswith('assozeta_contract_') or not database_name.isidentifier():
    raise RuntimeError('Contract adapter requires its own disposable database')
DATABASES['default']['NAME'] = database_name  # noqa: F405
DATABASES['default']['CONN_MAX_AGE'] = 0  # noqa: F405
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1']
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
MEDIA_ROOT = os.path.join(os.environ.get('TESTS_WORKDIR', '/tmp'), 'contract-media')
STORAGE_DIR = ''
DATA_RESTORE_LOCK_PATH = os.path.join(os.environ.get('TESTS_WORKDIR', '/tmp'), 'contract-lifecycle.lock')
INSTANCE_SETUP_TOKEN = 'contract-setup-token'
CELERY_BROKER_URL = 'memory://'
CELERY_TASK_DEFAULT_QUEUE = 'contracts'
CELERY_TASK_ALWAYS_EAGER = False
RATELIMIT_ENABLE = False
DRF_API_LOGGER_DATABASE = False
# Neither fixture creation nor requests may send messages to real providers.
STRIPE_KEY = STRIPE_PUBLIC_KEY = STRIPE_WEBHOOK_SECRET = ''
LOGGING['root']['level'] = 'WARNING'  # noqa: F405

# Test lifecycle responses do not need the developer's installed manual or AI.
MANUAL_CORPUS_BASE_URL = MANUAL_RUN_ID = MANUAL_SOURCE_ROOT = ''
MANUAL_INDEX_PATH = '/tmp/contract-no-manual.json'
MANUAL_DEVELOPMENT_ROOT = '/tmp/contract-manual'
AI_API_KEY = ''
CELERY_RESULT_BACKEND = 'django-db'

for contract_logger in LOGGING.get('loggers', {}).values():  # noqa: F405
    contract_logger['level'] = 'WARNING'

# Cheap synthetic fixture passwords; historical PBKDF2 hashes remain supported.
from django.conf.global_settings import PASSWORD_HASHERS as DEFAULT_PASSWORD_HASHERS
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher', *DEFAULT_PASSWORD_HASHERS]

LOGGING['loggers']['instance.restore'] = {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# Rendering is a separate integration; keep those jobs on an unconsumed private queue.
CELERY_TASK_ROUTES = {name: {'queue': 'contract-rendering'} for name in (
    'print_document_invoice', 'print_document_customer_invoice',
    'print_document_compensation', 'print_document_medical_appointment')}
