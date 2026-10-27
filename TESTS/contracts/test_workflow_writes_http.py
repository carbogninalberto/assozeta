"""Independently authored workflow add/delete HTTP expectations; no Django imports."""
import json
from pathlib import Path
from uuid import UUID

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/workflow_writes_http.json'
ALLOW = {'add': {'POST', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 20, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def bind(expected, actual, bindings):
    if isinstance(expected, str) and expected.startswith('$new:'):
        require(isinstance(actual, str) and str(UUID(actual)) == actual and UUID(actual).version == 4, 'response.new-uuid4')
        require(bindings.setdefault(expected[5:], actual) == actual, 'response.new-binding')
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: bind(value, actual.get(key), bindings) for key, value in expected.items()}
    return expected


def assert_case(case, base_url, *, token, sessions, bindings):
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
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    try:
        expected = bind(case['response'], actual, bindings)
    except AssertionError as error:
        raise ResponseFailure(str(error), status) from None
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOW[case['path'].rstrip('/').rsplit('/', 1)[1]], 'response.allow')
    check(received.get('Set-Cookie') is None, 'response.cookie')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    return status
