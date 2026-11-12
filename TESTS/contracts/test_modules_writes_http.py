"""Independently authored modules add/update/duplicate/delete HTTP expectations; no Django imports.

Every check of http_family applies (status, error page, Allow, headers,
exact JSON). The duplicate's generated values sit inside 'data': '$new'
accepts a UUID module_id and a 12-character lowercase hex custom_link
(uuid4().hex[:12]), '$now' a UTC 'Z' creation_date; the runner checks the
stored clone.
"""
import json
import re
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/modules_writes_http.json'
ALLOWED = {'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'duplicate': {'POST', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}
GENERATED = {'module_id': re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'),
             'custom_link': re.compile(r'[0-9a-f]{12}'), 'creation_date': re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z')}


def load_source(path=SOURCE):
    return family.load_source(path, minimum=60)


def resolve(expected, actual, check):
    """Accept the generated values of a duplicate's 'data'."""
    if not (isinstance(expected, dict) and isinstance(expected.get('data'), dict)):
        return expected
    data = dict(expected['data'])
    received = actual.get('data') if isinstance(actual, dict) else None
    for key, pattern in GENERATED.items():
        if data.get(key) in ('$new', '$now'):
            value = received.get(key) if isinstance(received, dict) else None
            check(isinstance(value, str) and pattern.fullmatch(value) is not None, 'response.generated-' + key)
            data[key] = value
    return {**expected, 'data': data}


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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED[case['path'].rstrip('/').rsplit('/', 1)[1]],
          'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = resolve(case['response'], actual, check)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
