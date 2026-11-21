"""Django settings of the Phase 4 handoff driver, its Celery workers, Beat and relays.

The Phase 3 settings (TESTS.handoff.p3_django_settings: private broker, default queue
``celery``, django-db results, counting SMTP, barriers) plus the Phase 4 test seams. Every
value below is consumed by the implementation only when set; core.settings defaults them
so production behaviour is unchanged:

  ASSOZETA_HANDOFF_CLOCK_FILE        test-only scheduler clock (contract: test_p4_scheduling).
                                     Only the Beat subprocess receives it (env var).
  ASSOZETA_SCHEDULER_LEASE_SECONDS   scheduler lease length (production default 30)
  CELERY_BEAT_MAX_LOOP_INTERVAL      Beat re-evaluates at least this often (test speed)
  WHATSAPP_*                         the stack's provider stub (contract: test_p4_outbox_realtime)
  ASSOZETA_WHATSAPP_POLL_SECONDS     interval of the Go/Django outbox drain in tests (Beat entry
                                     deliver-whatsapp keeps its 10 s production schedule)
"""
import os

from TESTS.handoff.p3_django_settings import *  # noqa: F401,F403

ASSOZETA_HANDOFF_CLOCK_FILE = os.environ.get('ASSOZETA_HANDOFF_CLOCK_FILE', '')
ASSOZETA_SCHEDULER_LEASE_SECONDS = float(os.environ.get('ASSOZETA_SCHEDULER_LEASE_SECONDS') or 1.0)
CELERY_BEAT_MAX_LOOP_INTERVAL = 0.1
CELERY_BEAT_SCHEDULER = 'instance.scheduler.InstanceScheduler'

WHATSAPP_API_BASE_URL = os.environ.get('WHATSAPP_API_BASE_URL', 'http://127.0.0.1:9')
WHATSAPP_API_VERSION = 'v23.0'
WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_BUSINESS_ACCOUNT_ID = os.environ.get('WHATSAPP_BUSINESS_ACCOUNT_ID', '')
WHATSAPP_ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN', '')
WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.environ.get('WHATSAPP_WEBHOOK_VERIFY_TOKEN', '')
WHATSAPP_NOTIFICATION_ROUTES = os.environ.get('WHATSAPP_NOTIFICATION_ROUTES') or '{}'

# The realtime relays send through the production layer (channels_redis + the Go mirror on the
# 'assozeta:realtime:v1' pub/sub channel) on the private Redis, like core.settings does.
_redis = os.environ.get('ASSOZETA_HANDOFF_BROKER_URL', '')
_password = os.environ.get('ASSOZETA_HANDOFF_REDIS_PASSWORD', '')
CHANNEL_LAYERS = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer',
                              'CONFIG': {'hosts': [{'address': 'redis://' + _redis.rsplit('@', 1)[-1],
                                                    'password': _password}]}}}

# Production-shaped cache (django-redis, database 1 of the private Redis): cross-process state such as
# the backup-failure notice dedup keys lives there in production.
CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                      'LOCATION': 'redis://' + _redis.rsplit('@', 1)[-1].rsplit('/', 1)[0] + '/1',
                      'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': _password}}}
# A valid sender for team notices (core.settings derives it from EMAIL_HOST_USER); no password, so no SMTP AUTH.
EMAIL_HOST_USER = 'team@example.test'
DEFAULT_TEAM_EMAIL = 'Il Team di Handoff Fixture <team@example.test>'

# Matrix families (p4_matrix_kit / p4_family_*): the stack's private services, when it runs them.
if os.environ.get('STORE_ON_DISK') == 'False' and os.environ.get('AWS_S3_ENDPOINT_URL'):
    # Same backend as production (core.settings), on the stack's private MinIO bucket.
    STORAGES = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    STORE_ON_DISK = False
    AWS_S3_ADDRESSING_STYLE = 'path'
STRIPE_KEY = os.environ.get('STRIPE_KEY', '')
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
if os.environ.get('STRIPE_API_BASE'):
    import stripe
    stripe.api_key = STRIPE_KEY
    stripe.api_base = os.environ['STRIPE_API_BASE']  # the stack's Stripe stub (both runtimes read STRIPE_API_BASE)
if os.environ.get('ASSOZETA_HANDOFF_DEVELOPMENT_HTTP') == '1':
    # Plain-http peers (the Bakney peer stub) are accepted only in development mode (instance.sso.protocol).
    DEBUG = True
    ASSOZETA_DEPLOYMENT_MODE = 'development'
if os.environ.get('ASSOZETA_HANDOFF_CURRENT_HOST'):
    # The trusted print origin (a family's recording stub or the active API); the base test settings pin a dead port.
    CURRENT_HOST = CURRENT_HOST_PUPPETEER = os.environ['ASSOZETA_HANDOFF_CURRENT_HOST']
