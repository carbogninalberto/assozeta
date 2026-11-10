"""Independently authored data-restore HTTP expectations; no Django imports.

The shared family checks (status, error page, Allow, Content-Language, no
cookie, Bearer challenge) plus what these views set: Cache-Control on every
DRF response (finalize_response), the attachment of the missing-relations
report (exact JSON text), the streamed recovery archive (exact bytes,
Content-Type, Content-Length, Content-Disposition) and the download view's
Referrer-Policy. In JSON bodies '$now' accepts a request-time UTC 'Z'
timestamp, '$new' a uuid4 receipt id and '$download-token:<receipt>' a token the runner's
verify_token(receipt, token) accepts. '<head>' is a body-less HEAD answer.
LAST holds the most recent JSON body for the runner's effect check.
"""
import base64
from datetime import datetime, timezone
import json
import re
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/data_restore_http.json'
ROOT = '/instance/admin/data-restore'
NOW = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z\Z')
LAST = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(path):
    path = path.split('?', 1)[0]
    if path == ROOT:
        return {'GET', 'POST', 'HEAD', 'OPTIONS'}
    if path == ROOT + '/backups' or path.endswith('/download'):
        return {'GET', 'HEAD', 'OPTIONS'}
    return {'GET', 'POST', 'HEAD', 'OPTIONS'}


def accept(expected, actual, check, verify_token):
    """Copy of expected with '$now' and '$download-token:' placeholders replaced by the verified actual values."""
    if expected == '$new':
        check(isinstance(actual, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', actual)
              is not None, 'response.new-id')
        return actual
    if expected == '$now':
        check(isinstance(actual, str) and NOW.match(actual), 'response.now')
        moment = datetime.fromisoformat(actual.replace('Z', '+00:00'))
        check(abs((datetime.now(timezone.utc) - moment).total_seconds()) < 120, 'response.now-window')
        return actual
    if isinstance(expected, str) and expected.startswith('$download-token:'):
        check(isinstance(actual, str) and verify_token(expected.split(':', 1)[1], actual), 'response.download-token')
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: accept(value, actual.get(key), check, verify_token) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [accept(value, item, check, verify_token) for value, item in zip(expected, actual)]
    return expected


def assert_case(case, base_url, *, token, sessions, path=None, verify_token=lambda receipt, value: False):
    LAST.clear()
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_b64' in case:
        body = base64.b64decode(case['raw_b64'])
        headers['Content-Type'] = case['content_type']
    status, raw, received = Client(base_url, token).request(case['method'], path or case['path'], body, headers, raw=True,
                                                             include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        check(received.get('Cache-Control') is None, 'response.cache-control')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case['path']), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    check(received.get('Cache-Control') == case['cache_control'], 'response.cache-control')
    if case['referrer_policy'] is not None:
        check(received.get('Referrer-Policy') == case['referrer_policy'], 'response.referrer-policy')
    stream = case['stream']
    if stream is not None:
        check(received.get('Content-Type') == stream['content_type'], 'response.stream-type')
        check(received.get('Content-Length') == str(stream['length']), 'response.stream-length')
        check(received.get('Content-Disposition') == stream['disposition'], 'response.stream-disposition')
        check(received.get('Content-Encoding') is None, 'response.stream-encoding')
    if case['response'] == '<head>':
        check(raw == b'', 'response.head-body')
        return status
    if case['response'] == '<stream>':
        check(raw == base64.b64decode(stream['body_b64']), 'response.stream-body')
        return status
    expected = case['response']
    if isinstance(expected, dict) and set(expected) == {'$raw'}:
        check(raw.decode('utf-8') == expected['$raw'], 'response.raw-body')
        check(received.get('Content-Type') == 'application/json', 'response.raw-type')
        check(received.get('Content-Disposition') == case['attachment'], 'response.attachment')
        return status
    check(received.get('Content-Disposition') is None, 'response.no-attachment')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = accept(expected, actual, check, verify_token)
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    LAST['body'] = actual
    return status
