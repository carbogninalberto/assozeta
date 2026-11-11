"""Request counts of the flows open without an account (PublicRequestThrottle), checked around every case.

DRF's cache history is `throttle_<scope>_<address>`: a requests without an account that reaches the throttle adds
one entry (newest first); a full history answers 429 and records nothing; requests with an account never touch it.
A case may preload the address's history ({'preload': n, 'age': seconds}); every case starts and ends clean.
"""
import json
import os
import re
import time
from contextlib import contextmanager

REQUESTS, WINDOW = 30, 600
ROUTES = ((re.compile(r'/modules/[^/]+/response/add\Z'), 'public-module-response'),
          (re.compile(r'/modules/response/[^/]+/add-attachment\Z'), 'public-module-attachment'),
          (re.compile(r'/subscription/add\Z'), 'public-subscription-add'))


def scope_of(case):
    path = case['path'].split('?')[0]
    return next((scope for pattern, scope in ROUTES if pattern.match(path)), None)


def client_ident(base_url, require):
    """DRF get_ident: REMOTE_ADDR natively; behind Caddy the X-Forwarded-For it sets."""
    from TESTS.http_client import Client
    if '/api/v' not in base_url:
        return '127.0.0.1'
    gateway = base_url.rsplit('/api/v', 1)[0]
    status, _body, headers = Client(gateway).request('GET', '/healthz', include_headers=True)
    require(status == 200 and headers.get('X-Fixture-Client-IP'), 'throttle.client-ip')
    return headers['X-Fixture-Client-IP']


def prepare(case, ident, require):
    """The address's history before the request: empty, or what the case preloads for its route."""
    from django.core.cache import cache
    for _pattern, scope in ROUTES:
        require(cache.get(f'throttle_{scope}_{ident}') is None, 'throttle.clean')
    spec = case.get('throttle')
    if spec:
        scope = scope_of(case)
        require(scope is not None, 'fixture.throttle-path')
        cache.set(f'throttle_{scope}_{ident}', [time.time() - spec['age']] * spec['preload'], WINDOW)


def finish(case, status, ident, require):
    """Only a request without an account that got past authentication is recorded, and only while below the rate."""
    from django.core.cache import cache
    scope = scope_of(case)
    spec = case.get('throttle') or {'preload': 0, 'age': 0}
    live = spec['preload'] if spec['age'] < WINDOW else 0
    reached = scope is not None and case['actor'] == 'anonymous' and status != 401
    require((status == 429) == (reached and live >= REQUESTS), 'throttle.status')
    for _pattern, name in ROUTES:
        history = cache.get(f'throttle_{name}_{ident}')
        if name == scope and reached and live < REQUESTS:
            require(isinstance(history, list) and len(history) == live + 1 and all(type(item) is float for item in history)
                    and 0 <= time.time() - history[0] < 30 and history == sorted(history, reverse=True), 'throttle.recorded')
        elif name == scope and spec['preload']:
            require(isinstance(history, list) and len(history) == spec['preload'] and len(set(history)) == 1, 'throttle.unchanged')
        else:
            require(history is None, 'throttle.not-reached')
        cache.delete(f'throttle_{name}_{ident}')


@contextmanager
def upload_limit(limit):
    """PUBLIC_UPLOAD_MAX_BYTES on both servers for the run."""
    from django.test.utils import override_settings
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**json.loads(saved or '{}'), 'PUBLIC_UPLOAD_MAX_BYTES': str(limit)})
    try:
        with override_settings(PUBLIC_UPLOAD_MAX_BYTES=limit):
            yield
    finally:
        if saved is None:
            os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
        else:
            os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
