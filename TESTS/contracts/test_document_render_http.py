"""Independently authored document-render HTTP expectations; no Django imports.

Every route is @api_view(['POST', 'GET']): Allow is POST, GET, OPTIONS on
every DRF answer (the PDF HttpResponse included). A '<pdf>' case must return
exactly the renderer stub's PDF bytes with Content-Type application/pdf, its
Content-Length and the authored Content-Disposition as Django's response
headers carry it: latin-1 text as is (http.client reads headers as
ISO-8859-1), anything else MIME-encoded by email.header.Header('utf-8').
Other cases are JSON bodies or the stable server error page; every 401 is
DRF's (NotAuthenticated or AuthenticationFailed) with the Bearer challenge.

'$bypass' (the DOCUMENT_BYPASS_TOKEN), '$folder:<label>' (a folder id) and
'$folder-text:<label>' (its str()) are filled from RUNTIME, installed by
TESTS/adapters/django/document_render_http.py.

renderer_payload is FastRender.get_pdf's payload for an authored view path,
serialized as json.dumps does (requests' data=json.dumps(payload)).
"""
from email.header import Header
import json
from pathlib import Path
import sys

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/document_render_http.json'
RUNTIME = {}
ALLOWED = {'GET', 'POST', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def fill(value):
    """Runtime placeholders inside headers and JSON bodies."""
    if isinstance(value, dict):
        return {key: fill(item) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item) for item in value]
    if value == '$bypass':
        return RUNTIME['bypass']
    if isinstance(value, str) and value.startswith('$folder:'):
        return RUNTIME['folders'][value.split(':', 1)[1]]
    if isinstance(value, str) and value.startswith('$folder-text:'):
        return str(RUNTIME['folders'][value.split(':', 1)[1]])
    return value


def header_value(text):
    """django.http.ResponseHeaders: latin-1 when it encodes, else MIME (as a client reads it)."""
    try:
        text.encode('latin-1')
        return text
    except UnicodeEncodeError:
        return Header(text, 'utf-8', maxlinelen=sys.maxsize).encode()


def renderer_payload(current_host, url, authorization):
    payload = {
        'type': 'pdf',
        'url': f'{current_host}/{url}',
        'async': False,
        'headers': {'Authorization': authorization},
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
    body = fill(case['body']) if 'body' in case else None
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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<pdf>':
        check(raw == RUNTIME['pdf'], 'pdf.body')
        check(received.get('Content-Type') == 'application/pdf', 'pdf.content-type')
        check(received.get('Content-Length') == str(len(raw)), 'pdf.content-length')
        check(received.get_all('Content-Disposition') == [header_value(case['disposition'])], 'pdf.content-disposition')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Disposition') is None, 'response.no-disposition')
    return status
