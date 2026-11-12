"""Independently authored saved-reports HTTP expectations; no Django imports."""
from datetime import datetime
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute
from TESTS.contracts.test_workflow_writes_http import bind

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/saved_reports_http.json'
# SavedReportViewSet defines delete(), so DELETE is allowed on every route.
ALLOW = {'list': {'GET', 'HEAD', 'DELETE', 'OPTIONS'}, 'add': {'POST', 'DELETE', 'OPTIONS'}, 'info': {'GET', 'HEAD', 'DELETE', 'OPTIONS'},
         'update': {'PATCH', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 25, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def resolve(expected, actual, bindings):
    """$time: any aware isoformat() value (the runner checks it against the row); $new:<label>: bound IDs."""
    if expected == '$time':
        require(isinstance(actual, str) and actual.endswith('+00:00') and datetime.fromisoformat(actual).tzinfo is not None, 'response.time')
        return actual
    if isinstance(expected, str) and expected.startswith('$new:') and expected[5:] in bindings:
        return bindings[expected[5:]]
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: resolve(value, actual.get(key), bindings) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [resolve(e, a, bindings) for e, a in zip(expected, actual)]
    return bind(expected, actual, bindings)


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
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOW[case['path'].rstrip('/').rsplit('/', 1)[1]], 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'' and received.get('Content-Type') is None, 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    try:
        expected = resolve(case['response'], actual, bindings)
    except AssertionError as error:
        raise ResponseFailure(str(error), status) from None
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
