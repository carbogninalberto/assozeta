"""Independently authored Senja webhook HTTP expectations; no Django imports.

The caller supplies the deployment secret; it signs only the authored
``signed`` text and is never recorded.
"""
import hashlib
import hmac
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/testimonial_webhook_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 25, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def assert_case(case, base_url, *, token, secret):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'], **case['headers']}
    body = None
    if case['raw_body']:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    if case['signature'] == 'valid':
        headers['x-senja-signature'] = hmac.new(secret.encode(), case['signed'].encode(), hashlib.sha256).hexdigest()
    elif case['signature'] is not None:
        headers['x-senja-signature'] = case['signature']
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'POST', 'OPTIONS'},
          'response.allow')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(received.get('Set-Cookie') is None and received.get('WWW-Authenticate') is None, 'response.headers')
    if case['response'] == '<empty>':
        check(raw == b'' and received.get('Content-Type') is None, 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    check(actual == case['response'], 'response.body' + difference(case['response'], actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
