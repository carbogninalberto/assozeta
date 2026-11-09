"""Parent-only chat/test/ HTTP runner; imports are inert.

Seeds an inactive user and three Django cache sessions under fixed keys (an
active owner logged in through ModelBackend, the inactive user, and a session
holding only an OAuth state); the 'missing' key is never stored. Every case is
a read: the complete model state and the cache must stay unchanged.
"""
from TESTS.contracts import test_chat_test_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/chat_test_http.json', 'TESTS/contracts/test_chat_test_http.py',
           'TESTS/adapters/django/chat_test_http.py', 'BE/application/chat/urls.py', 'BE/application/chat/views.py',
           'BE/core/urls.py', 'BE/core/middleware.py', 'BE/core/settings.py',
           'BE/instance/restore/locking.py') + harness.SOURCES

INACTIVE = 'a1000000-0000-4000-8000-000000790001'
BACKEND = 'django.contrib.auth.backends.ModelBackend'


def seed_rows(source, created):
    from django.conf import settings
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.sessions.backends.cache import KEY_PREFIX
    from django.core.cache import caches
    from application.models import User
    inactive = User(user_id=INACTIVE, username='chat-test-inactive', email='chat-test-inactive@example.test',
                    role=1, is_active=False, password='!private-fixture')
    User.original_objects.bulk_create([inactive])
    created.append((User, inactive.pk))
    owner = User.original_objects.get(pk=source['ids']['owner'])
    contract.require(owner.is_active, 'fixture.owner-active')
    cache = caches[settings.SESSION_CACHE_ALIAS]
    keys = source['sessions']
    values = {
        'owner': {SESSION_KEY: str(owner.pk), BACKEND_SESSION_KEY: BACKEND, HASH_SESSION_KEY: owner.get_session_auth_hash()},
        'inactive': {SESSION_KEY: str(inactive.pk), BACKEND_SESSION_KEY: BACKEND, HASH_SESSION_KEY: inactive.get_session_auth_hash()},
        'state': {'state': 'chat-test-fixture'},
    }
    for label, value in values.items():
        cache.set(KEY_PREFIX + keys[label], value, settings.SESSION_COOKIE_AGE)
        contract.require(cache.get(KEY_PREFIX + keys[label]) == value, 'fixture.session-stored')
    contract.require(cache.get(KEY_PREFIX + keys['missing']) is None, 'fixture.session-missing')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
