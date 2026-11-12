"""Independently authored subscription/<uid> medical appointments and medical certificate HTTP expectations; no Django imports.

The checks are http_family's, plus JSON types: the listed appointments echo
their stored ``meta`` JSON, where Python equality conflates True with 1.
"""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_medical_http.json'
ALLOWED = {'list': 'GET', 'delete': 'DELETE', 'edit': 'POST', 'set-certificate-expiration': 'POST'}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {ALLOWED[case['path'].split('?')[0].rstrip('/').split('/')[-1]], 'OPTIONS'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(_canonical(actual) == _canonical(expected), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
