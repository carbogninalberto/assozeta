"""Independently authored instance backups HTTP expectations; no Django imports.

The shared family checks (status, error page, Allow, Content-Language, no
cookie, Bearer challenge) plus what these views set: the relayed archive
(exact bytes, Content-Type, Content-Length, Content-Disposition,
Cache-Control and Referrer-Policy) and no Cache-Control anywhere else. In
JSON bodies '$database' accepts the database size measured around the
request (the runner's measure()), '{"$next": ...}' the next-backup estimate
computed from it, and '$backup-token:<name>:<actor>' a token the runner's
verify_token(name, actor, token) accepts. '<head>' is a body-less answer.
LAST holds the database size the response reported, for the cache check.
"""
import base64
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instance_backups_http.json'
MIB = 1024 * 1024
LAST = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(path):
    path = path.split('?', 1)[0]
    if path == '/instance/admin/backups':
        return {'GET', 'HEAD', 'OPTIONS'}
    if path == '/instance/admin/backups/download-token':
        return {'POST', 'OPTIONS'}
    return {'GET', 'OPTIONS'}


def accept(expected, actual, check, window, verify_token):
    """Copy of expected with the placeholders replaced by the verified actual values."""
    if expected == '$database':
        check(type(actual) is int and window[0] <= actual <= window[1], 'response.database')
        LAST['database'] = actual
        return actual
    if isinstance(expected, dict) and set(expected) == {'$next'}:
        spec, database = expected['$next'], LAST.get('database')
        check(type(database) is int, 'response.next-database')
        estimated = database + spec['objects']
        required = estimated * 2 + spec['margin'] * MIB
        return {'estimated': estimated, 'required': required, 'fits': spec['free'] is None or spec['free'] >= required}
    if isinstance(expected, str) and expected.startswith('$backup-token:'):
        _, name, actor = expected.split(':', 2)
        check(isinstance(actual, str) and verify_token(name, actor, actual), 'response.download-token')
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        # 'disk' precedes 'next_backup', so the database is known first.
        return {key: accept(value, actual.get(key), check, window, verify_token) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [accept(value, item, check, window, verify_token) for value, item in zip(expected, actual)]
    return expected


def assert_case(case, base_url, *, token, sessions, path=None, verify_token=lambda name, actor, value: False,
                measure=lambda: None):
    LAST.clear()
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    before = measure() if case['database'] else None
    status, raw, received = Client(base_url, token).request(case['method'], path or case['path'], body, headers, raw=True,
                                                             include_headers=True)
    window = (before, measure()) if case['database'] else (None, None)

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
    stream = case['stream']
    if stream is not None:
        check(received.get('Content-Type') == stream['content_type'], 'response.stream-type')
        check(received.get('Content-Length') == str(stream['length']), 'response.stream-length')
        check(received.get('Content-Disposition') == stream['disposition'], 'response.stream-disposition')
        check(received.get('Content-Encoding') is None, 'response.stream-encoding')
        check(received.get('Cache-Control') == 'no-store', 'response.stream-cache-control')
        check(received.get('Referrer-Policy') == 'no-referrer', 'response.stream-referrer-policy')
        check(raw == base64.b64decode(stream['body_b64']), 'response.stream-body')
        return status
    check(received.get('Cache-Control') is None, 'response.cache-control')
    check(received.get('Content-Disposition') is None, 'response.no-attachment')
    if case['response'] == '<head>':
        check(raw == b'', 'response.head-body')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = accept(case['response'], actual, check, window, verify_token)
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
