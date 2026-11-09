"""Independently authored document-views HTTP expectations; no Django imports.

Every route is @api_view(['GET']): Allow is GET, OPTIONS on every DRF answer
(the rendered HttpResponse included). An '<html>' case must return exactly the
bytes Django's template engine renders for the case's authored template,
context and language (RUNTIME['render'], installed by the adapter), as
'text/html; charset=utf-8' with the authored Content-Language (the
compensation and medical views activate Italian). Other cases are JSON bodies
or the stable server error page; every 401 is DRF's with the Bearer challenge.

'$bypass' in headers is the DOCUMENT_BYPASS_TOKEN; '$pdf-base64' is the
renderer stub's PDF in base64 (RUNTIME, installed by
TESTS/adapters/django/document_views_http.py).
"""
import base64
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/document_views_http.json'
RUNTIME = {}
ALLOWED = {'GET', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def fill(value):
    if isinstance(value, dict):
        return {key: fill(item) for key, item in value.items()}
    if value == '$bypass':
        return RUNTIME['bypass']
    if value == '$pdf-base64':
        return base64.b64encode(RUNTIME['pdf']).decode()
    return value


def renderer_payload(current_host, url):
    """json.dumps(FastRender.get_pdf(url, {})'s payload)."""
    payload = {
        'type': 'pdf', 'url': f'{current_host}/{url}', 'async': False, 'headers': {},
        'device': {'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/91.0.4472.124 Safari/537.36', 'scale': 1, 'width': 1920, 'height': 1080},
        'render': {'block': {'cookies': False, 'ads': False, 'trackers': False, 'banners': False}, 'waitTime': 0, 'timeout': 30000,
                   'fullPage': False, 'triggerLazyAnimations': False,
                   'scroll': {'position': 0, 'animate': False, 'duration': 0, 'animation': ''}, 'waitUntil': 'networkidle2'},
        'pdf': {'scale': 1, 'displayHeaderFooter': False, 'headerTemplate': '', 'footerTemplate': '', 'printBackground': True,
                'landscape': False, 'pageRanges': '', 'format': 'A4', 'margin': {'top': '1cm', 'right': '0px', 'bottom': '1cm', 'left': '0px'},
                'preferCSSPageSize': False, 'omitBackground': False, 'timeout': 30000},
    }
    return json.dumps(payload).encode()


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
    if case['response'] == '<html>':
        expected = RUNTIME['render'](case['html'])
        check(received.get('Content-Type') == 'text/html; charset=utf-8', 'html.content-type')
        check(raw == expected, 'html.body')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = fill(case['response'])
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
