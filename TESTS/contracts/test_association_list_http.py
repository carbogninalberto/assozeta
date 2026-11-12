"""Independently authored superuser association-list HTTP expectations; no Django imports."""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import ResponseFailure, difference, require, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/association_list_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = {} if case['method'] == 'POST' else None
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    if case['response'] == '<empty>':
        # A bare Response(status=401) is not an authentication exception.
        check(raw == b'' and received.get('Content-Type') is None and received.get('WWW-Authenticate') is None, 'response.empty')
        return status
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    if case['unordered']:
        key = lambda row: row['sport_association_id']
        check(isinstance(actual, list), 'response.rows')
        actual, expected = sorted(actual, key=key), sorted(expected, key=key)
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
