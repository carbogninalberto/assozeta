"""Independently authored instance administration HTTP expectations; no Django imports.

The shared family checks (status, error page, Allow, Content-Language, no
cookie, Bearer challenge, exact JSON) plus the headers these views set:
Cache-Control on every email/integration response and on diagnostics reads,
Retry-After on a leased diagnostics run. '$isonow' accepts a request-time
datetime.isoformat() in UTC (diagnostics integration checks).
"""
import json
import re
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instance_admin_http.json'
ALLOWED = {'/instance/admin': {'GET', 'PUT', 'HEAD', 'OPTIONS'},
           '/instance/admin/email': {'GET', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'},
           '/instance/admin/diagnostics': {'GET', 'POST', 'HEAD', 'OPTIONS'},
           '/instance/admin/updates': {'GET', 'POST', 'HEAD', 'OPTIONS'},
           '/instance/admin/restarts': {'POST', 'OPTIONS'}}
INTEGRATION = {'GET', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'}
ISO_NOW = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00\Z')


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(path):
    return INTEGRATION if path.startswith('/instance/admin/integrations/') else ALLOWED[path]


def accept_now(expected, actual, check):
    """Copy of expected with each '$isonow' replaced by the actual timestamp."""
    if expected == '$isonow':
        check(isinstance(actual, str) and ISO_NOW.match(actual), 'response.isonow')
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: accept_now(value, actual.get(key), check) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [accept_now(value, item, check) for value, item in zip(expected, actual)]
    return expected


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case['path']), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    check(received.get('Cache-Control') == case['cache_control'], 'response.cache-control')
    check(received.get('Retry-After') == case['retry_after'], 'response.retry-after')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = accept_now(case['response'], actual, check)
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
