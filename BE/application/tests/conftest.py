"""
Self-host test conftest: patches cache settings for Redis-free test runs.
"""
import os


def pytest_configure(config):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    import django
    from django.conf import settings
    if not settings.configured:
        django.setup()
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
    settings.SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    settings.SESSION_CACHE_ALIAS = 'default'
