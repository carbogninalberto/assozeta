"""Independently authored docmanager-reads HTTP expectations; no Django imports.

A '<stream>' case names the stored object whose exact bytes must be returned
('$rendered': the PDF printed again for a missing object),
with the exact Content-Type, Content-Length, Content-Disposition and
Content-Encoding (absent when the authored value is None). Every other case is
a JSON body or the stable server error page. Unlike http_family, a 401 carries
the Bearer challenge only when the case says so: retrieve_document answers
some 401s itself (no challenge) and DRF answers the others.

'$jwt:<actor>' and '$download:<name>' in paths and headers are filled from
RUNTIME, installed by TESTS/adapters/django/docmanager_reads_http.py (it owns
the signing key and issues the fixture's access tokens).
"""
import base64
import json
from pathlib import Path
import re

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/docmanager_reads_http.json'
RUNTIME = {}
PLACEHOLDER = re.compile(r'\$(jwt|download):([a-z_]+)')


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    verb = 'DELETE' if case['path'].split('?')[0].endswith('/delete') else 'GET'
    return {verb, 'OPTIONS'}


def fill(value):
    def replace(match):
        kind, name = match.groups()
        values = RUNTIME['tokens'] if kind == 'jwt' else RUNTIME['download_tokens']
        return values[name]
    return PLACEHOLDER.sub(replace, value) if isinstance(value, str) else value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name']}
    headers.update({key: fill(value) for key, value in substitute(case['headers'], sessions).items()})
    status, raw, received = Client(base_url, token).request(case['method'], fill(case['path']), None, headers,
                                                            raw=True, include_headers=True)

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
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if case['challenge'] else None), 'response.authentication')
    if case['response'] == '<stream>':
        stream = case['stream']
        if stream['object'] == '$rendered':
            # The PDF the renderer stub printed, stored by the save_to_storage task.
            body = RUNTIME['pdf']
        else:
            body = base64.b64decode(load_source()['objects'][stream['object']]['body_b64'])
        check(raw == body, 'stream.body')
        check(received.get('Content-Type') == stream['content_type'], 'stream.content-type')
        check(received.get('Content-Length') == str(len(body)), 'stream.content-length')
        check(received.get('Content-Disposition') == stream['disposition'], 'stream.content-disposition')
        check(received.get('Content-Encoding') == stream['encoding'], 'stream.content-encoding')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Disposition') is None and received.get('Content-Encoding') is None, 'response.no-stream-headers')
    return status
