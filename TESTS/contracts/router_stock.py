"""Shared authored expectations for the DefaultRouter routes of BE/application/urls.py; no Django imports.

`include(router.urls)` gives each registered ModelViewSet a list route
`^<prefix>/$` ({'get': 'list', 'post': 'create'}), a detail route
`^<prefix>/(?P<pk>[^/.]+)/$` ({'get': 'retrieve', 'put': 'update',
'patch': 'partial_update', 'delete': 'destroy'}) and format twins
`\\.(?P<format>[a-z0-9]+)/?$`; ViewSetMixin binds HEAD to the GET action and
every viewset defines its own delete(), so Allow is the bound methods plus
DELETE, HEAD and OPTIONS. `documents/bulk_delete/` is the one extra action
({'delete': 'bulk_delete'}); the API root is an APIView with GET only.
'$host' in an expected body is the host the client addressed (Caddy keeps the
Host header and sets X-Forwarded-Host to it).
"""
import re
from urllib.parse import urlsplit

from TESTS.contracts import http_family as family

PREFIX = r'(?:course-locations|folders|documents|templates|subscription/memberships|course-subscriptions|personas|personas-subscriptions)'
FORMAT = r'(?:\.[a-z0-9]+/?|/)'
KINDS = (('root', re.compile(r'^/(?:\.[a-z0-9]+/?)?$'), {'GET', 'HEAD', 'OPTIONS'}),
         ('bulk', re.compile(r'^/documents/bulk_delete' + FORMAT + r'$'), {'DELETE', 'OPTIONS'}),
         ('list', re.compile(r'^/' + PREFIX + FORMAT + r'$'), {'GET', 'POST', 'DELETE', 'HEAD', 'OPTIONS'}),
         ('detail', re.compile(r'^/' + PREFIX + r'/[^/.]+' + FORMAT + r'$'), {'GET', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}))


def kind(case):
    path = case['path'].split('?')[0]
    for name, pattern, _ in KINDS:
        if pattern.match(path):
            return name
    raise AssertionError('router path: ' + path)


def allowed(case):
    return dict((name, methods) for name, _, methods in KINDS)[kind(case)]


def _host(value, host):
    if isinstance(value, str):
        return value.replace('$host', host)
    if isinstance(value, list):
        return [_host(item, host) for item in value]
    if isinstance(value, dict):
        return {key: _host(item, host) for key, item in value.items()}
    return value


def assert_case(case, base_url, *, token, sessions, bind=None, **options):
    """bind(case) -> case: optional family substitution of runtime ids (for example seeded folder ids)."""
    case = {**case, 'response': _host(case['response'], urlsplit(base_url).netloc)}
    if bind is not None:
        case = bind(case)
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, **options)
