"""Independently authored search/profile HTTP expectations; no Django imports."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/search_profile_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


LAST_PROFILE = {}


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = {} if case['method'] == 'POST' else None
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    source = load_source()
    user = expected.get('data', {}).get('user') if isinstance(expected, dict) else None
    if isinstance(user, dict) and isinstance(user.get('sport_association'), dict):
        block = user['sport_association']
        got = actual.get('data', {}).get('user', {}).get('sport_association') if isinstance(actual, dict) else None
        keys = source['profile_keys'] + (source['module_keys'] if '$preregistration' in block else [])
        check(isinstance(got, dict) and sorted(got) == sorted(keys), 'response.association-keys')
        check(all(got.get(k) == v for k, v in block['$values'].items()), 'response.association-values')
        if '$preregistration' in block:
            # The view's window from today's UTC date and the owner's season start.
            today = datetime.now(timezone.utc).date()
            try:
                start = date(today.year, block['$preregistration']['month'], block['$preregistration']['day'])
                if start <= today:
                    start = date(today.year + 1, start.month, start.day)
                days = (start - today).days
                window = (not 1 <= days <= 120, days if 1 <= days <= 120 else 0)
            except ValueError:
                window = (True, 0)
            check((got.get('preregistration_module_closed'), got.get('remaining_preregistration_days')) == window, 'response.preregistration')
        LAST_PROFILE[case['name']] = got
        actual = {**actual, 'data': {**actual['data'], 'user': {**actual['data']['user'], 'sport_association': None}}}
        expected = {**expected, 'data': {**expected['data'], 'user': {**user, 'sport_association': None}}}
    if isinstance(actual, dict) and isinstance(actual.get('data'), dict) and isinstance(actual['data'].get('courses_tags'), list):
        key = lambda row: row.get('tag_id', '') if isinstance(row, dict) else ''
        actual = {**actual, 'data': {**actual['data'], 'courses_tags': sorted(actual['data']['courses_tags'], key=key)}}
        expected = {**expected, 'data': {**expected['data'], 'courses_tags': sorted(expected['data']['courses_tags'], key=key)}}
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
