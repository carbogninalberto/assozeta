"""Independently authored document-render HTTP expectations; no Django imports.

Every route is @api_view(['POST', 'GET']): Allow is POST, GET, OPTIONS on
every DRF answer (the PDF HttpResponse included). A '<pdf>' case must return
exactly the renderer stub's PDF bytes with Content-Type application/pdf, its
Content-Length and the authored Content-Disposition as Django's response
headers carry it: latin-1 text as is (http.client reads headers as
ISO-8859-1), anything else MIME-encoded by email.header.Header('utf-8').
A '<form-pdf>' case must return the bytes RUNTIME['forms'] holds for it (the
regional form filled with the case's authored values), with the same headers.
A '<connection-closed>' case is an answer the server refuses to send: a direct
client reads the connection closed with no status line ('closed') and Caddy
answers its 502 with an empty body; its authored status is 'closed'. A '<form-pdf-visual>' case is a form filled over a president signature (or
stamp) drawn with PyMuPDF: it must return a PDF whose every page, rendered at
100 dpi, has the expected page's size and samples within PIXEL_TOLERANCE.
Other cases are JSON bodies or the stable server error page; every 401 is DRF's
(NotAuthenticated or AuthenticationFailed) with the Bearer challenge.

'$bypass' (the DOCUMENT_BYPASS_TOKEN), '$folder:<label>' (a folder id) and
'$folder-text:<label>' (its str()) are filled from RUNTIME, installed by
TESTS/adapters/django/document_render_http.py. In an exception text,
'$document-directory' stands for the new Document's '<creation timestamp>/<
document id>' storage directory: the view builds the storage name, so the
fixture cannot author it.

renderer_payload is FastRender.get_pdf's payload for an authored view path,
serialized as json.dumps does (requests' data=json.dumps(payload)).
"""
from email.header import Header
import http.client
import json
from pathlib import Path
import re
import sys
import urllib.error

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/document_render_http.json'
RUNTIME = {}
ALLOWED = {'GET', 'POST', 'OPTIONS'}
STORAGE_DIRECTORY = '$document-directory'
DOCUMENT_DIRECTORY = re.compile(r'[0-9]+(?:\.[0-9]+)?/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
PROXY_PREFIXES = ('/api/v1', '/api/v2')

# Per-channel tolerance of the rendered pages of a '<form-pdf-visual>' case. The authored cases measure 0 on every
# channel of every page (the same image samples, mask and matrix reach the same renderer), so a difference above 1 means
# a drawn image, a matrix or a page that the two backends do not share; 1 only leaves room for a renderer that rounds
# an edge sample differently.
PIXEL_TOLERANCE = 1


def visual_difference(received, expected):
    """None when every page of the two PDFs renders alike at 100 dpi, else what differs."""
    import contextlib
    import pymupdf
    with contextlib.ExitStack() as stack:
        try:
            actual = stack.enter_context(pymupdf.open(stream=received, filetype='pdf'))
            wanted = stack.enter_context(pymupdf.open(stream=expected, filetype='pdf'))
        except Exception as error:  # noqa: BLE001 - any reader failure is a different document
            return f'not a PDF ({error})'
        if actual.page_count != wanted.page_count:
            return f'{actual.page_count} pages, expected {wanted.page_count}'
        for index in range(actual.page_count):
            left = actual[index].get_pixmap(dpi=100)
            right = wanted[index].get_pixmap(dpi=100)
            if (left.width, left.height, left.n) != (right.width, right.height, right.n):
                return f'page {index}: {left.width}x{left.height}x{left.n}, expected {right.width}x{right.height}x{right.n}'
            if left.samples == right.samples:
                continue
            for position, (one, two) in enumerate(zip(left.samples, right.samples)):
                if abs(one - two) > PIXEL_TOLERANCE:
                    return f'page {index}: sample {position} differs by {abs(one - two)}'
    return None


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


def connection_closed(error):
    """Whether error is the client seeing the server close the connection with no response."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return isinstance(reason, (http.client.RemoteDisconnected, ConnectionResetError, http.client.IncompleteRead,
                               http.client.BadStatusLine))


def storage_exception(case, actual):
    """The authored exception body, or None when the case has no '$document-directory' placeholder.

    Only the directory the view generates varies: everything around it is exact.
    """
    expected = case['response']
    if not isinstance(expected, dict) or STORAGE_DIRECTORY not in str(expected.get('exception', '')):
        return None
    if not (isinstance(actual, dict) and set(actual) == {'exception'} and isinstance(actual['exception'], str)):
        return False
    before, after = expected['exception'].split(STORAGE_DIRECTORY, 1)
    text = actual['exception']
    if not (text.startswith(before) and text.endswith(after)):
        return False
    return DOCUMENT_DIRECTORY.fullmatch(text[len(before):len(text) - len(after)]) is not None


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name']}
    headers.update({key: fill(value) for key, value in substitute(case['headers'], sessions).items()})
    body = fill(case['body']) if 'body' in case else None
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    closed = case['response'] == '<connection-closed>'
    try:
        status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True,
                                                                include_headers=True)
    except (OSError, http.client.HTTPException) as error:
        if not (closed and connection_closed(error)):
            raise
        status, raw, received = 'closed', b'', {}

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    if closed:
        # The server refuses the answer after the view ran: a direct client reads no status line, and Caddy
        # answers its 502 for the upstream that closed (Caddy 2.11 reverse_proxy: an empty body).
        check(case['status'] == 'closed', 'fixture.status')
        if any(prefix in base_url for prefix in PROXY_PREFIXES):
            check(status == 502, 'response.status')
            check(raw == b'' and received.get('Content-Length') == '0', 'response.proxy')
        else:
            check(status == 'closed', 'response.status')
        return status
    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] in ('<pdf>', '<form-pdf>', '<form-pdf-visual>'):
        expected = RUNTIME['pdf'] if case['response'] == '<pdf>' else RUNTIME['forms'][case['name']]
        if case['response'] == '<form-pdf-visual>':
            difference = visual_difference(raw, expected)
            check(difference is None, 'pdf.body:' + str(difference))
        else:
            check(len(expected) > 0 and raw == expected, 'pdf.body')
        check(received.get('Content-Type') == 'application/pdf', 'pdf.content-type')
        check(received.get('Content-Length') == str(len(raw)), 'pdf.content-length')
        check(received.get_all('Content-Disposition') == [header_value(case['disposition'])], 'pdf.content-disposition')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    stored = storage_exception(case, actual)
    if stored is None:
        check(actual == expected, 'response.body' + difference(expected, actual))
    else:
        check(stored, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Disposition') is None, 'response.no-disposition')
    return status
