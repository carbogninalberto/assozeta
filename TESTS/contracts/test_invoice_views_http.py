"""Independently authored invoice-views HTTP expectations; no Django imports.

Both routes are @api_view(['GET']): Allow is GET, OPTIONS on every DRF answer
(the rendered HttpResponse included). An '<html>' case must return exactly the
bytes Django's template engine renders for the case's authored template,
context and language (RUNTIME['render']); an '<einvoice>' case exactly the
bytes generate_invoice_html returns for the case's authored CustomerInvoice
row (RUNTIME['einvoice']); both as 'text/html; charset=utf-8' with the
authored Content-Language. Other cases are JSON bodies or the stable server
error page; every 401 is DRF's with the Bearer challenge.

'$bypass' in headers is the DOCUMENT_BYPASS_TOKEN (RUNTIME, installed by
TESTS/adapters/django/invoice_views_http.py).
"""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_views_http.json'
RUNTIME = {}
ALLOWED = {'GET', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def fill(value):
    return RUNTIME['bypass'] if value == '$bypass' else value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name']}
    headers.update({key: fill(value) for key, value in substitute(case['headers'], sessions).items()})
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == case['content_language'] and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] in ('<html>', '<einvoice>'):
        expected = RUNTIME['render'](case['html']) if case['response'] == '<html>' else RUNTIME['einvoice'](case['einvoice'])
        check(received.get('Content-Type') == 'text/html; charset=utf-8', 'html.content-type')
        check(raw == expected, 'html.body')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
