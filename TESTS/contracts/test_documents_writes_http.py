"""Independently authored document upload HTTP expectations; no Django imports.

The checks are http_family's (status, error pages, Allow, Content-Language, the
401 challenge, exact JSON types), plus:
- '$folder:<label>' in paths, bodies and responses is the seeded folder's ID;
- '$doc<i>', '$doc<i>.created', '$doc<i>.token', '$archive<i>', '$archive<i>.date'
  and '$cert<i>' accept the generated string and are recorded in RUNTIME so the
  runner can bind them to the rows and audit entries the request created;
- '<bad-request-page>' is Django's SuspiciousOperation 400 page.
"""
import json
from pathlib import Path
import re

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/documents_writes_http.json'
BAD_REQUEST_DOCUMENT = (b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n'
                        b'  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n')
RUNTIME = {}
PLACEHOLDER = re.compile(r'^\$(doc|archive|cert|file)\d+(\.(created|token|date))?$')


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0]
    if path == '/documents/add':
        return {'POST', 'DELETE', 'OPTIONS'}
    if path.startswith('/documents/'):
        return {'PATCH', 'DELETE', 'OPTIONS'}
    return {'POST', 'OPTIONS'}


def bind(value, folders):
    """Replace $folder:<label> (integer) and $folder-text:<label> (str) with the seeded folder ID."""
    if isinstance(value, str) and value.startswith('$folder-text:'):
        return str(folders[value[13:]])
    if isinstance(value, str) and value.startswith('$folder:'):
        return folders[value[8:]]
    if isinstance(value, list):
        return [bind(item, folders) for item in value]
    if isinstance(value, dict):
        return {key: bind(item, folders) for key, item in value.items()}
    return value


def capture(expected, actual, captured):
    """expected with each generated placeholder replaced by the actual string (recorded in captured)."""
    if isinstance(expected, str) and PLACEHOLDER.match(expected):
        if isinstance(actual, str) and actual and captured.setdefault(expected, actual) == actual:
            return actual
        return expected
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: capture(value, actual.get(key), captured) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [capture(value, item, captured) for value, item in zip(expected, actual)]
    return expected


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def assert_case(case, base_url, *, token, sessions, folders):
    RUNTIME['captured'] = captured = {}
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    path = re.sub(r'\$folder:([a-z_]+)', lambda match: str(folders[match.group(1)]), case['path'])
    body = bind(case.get('body'), folders)
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    status, raw, received = Client(base_url, token).request(case['method'], path, body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    if case['response'] == '<bad-request-page>':
        check(raw == BAD_REQUEST_DOCUMENT, 'response.bad-request-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.bad-request-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = capture(bind(case['response'], folders), actual, captured)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(_canonical(actual) == _canonical(expected), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
