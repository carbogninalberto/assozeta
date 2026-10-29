"""Independently authored module-template HTTP expectations; no Django imports."""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute
from TESTS.contracts.test_workflow_writes_http import bind

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/module_templates_http.json'
# ViewSet.as_view maps HEAD to the get action, and the viewset's own delete()
# method makes DELETE allowed (and dispatched) on every route.
ALLOW = {'list': {'GET', 'DELETE', 'HEAD', 'OPTIONS'}, 'add': {'POST', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}, 'bulk-delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 30, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def canonical(rows):
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOW[case['path'].rstrip('/').rsplit('/', 1)[1]], 'response.allow')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(received.get('Set-Cookie') is None, 'response.cookie')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'' and received.get('Content-Type') is None, 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    if case['unordered']:
        check(isinstance(actual, list) and len(actual) == len(expected), 'response.rows')
        # Listed generated rows were bound by their earlier add case.
        resolve = lambda value: bindings[value[5:]] if isinstance(value, str) and value.startswith('$new:') else value
        expected = [{key: resolve(value) for key, value in row.items()} for row in expected]
        actual, expected = canonical(actual), canonical(expected)
    else:
        try:
            expected = bind(expected, actual, bindings)
        except AssertionError as error:
            raise ResponseFailure(str(error), status) from None
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
