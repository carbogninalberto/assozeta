"""Independently authored staff-board HTTP expectations, shared by all transports.

No Django imports; actual responses never define expected behavior. Generated
row IDs (``$new:<label>``) and write times (``$now``) are validated, bound and
then compared like any other value.
"""
from datetime import datetime
import json
from pathlib import Path
import time
from uuid import UUID

from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/staff_board_http.json'
ALLOW = {'list': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def require(condition, code):
    if not condition:
        raise AssertionError(code)


class ResponseFailure(AssertionError):
    def __init__(self, code, status):
        self.code, self.actual_status = code, status
        super().__init__(code)


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 30, 'source.version-count')
    names = [case['name'] for case in source['cases']]
    require(len(names) == len(set(names)) and all(name.startswith('staff-board-') for name in names), 'source.names')
    for case in source['cases']:
        require(case['method'] in ('GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'), 'source.method')
        require(case['path'].startswith('/communications/staff-board/'), 'source.path')
        require(case['effect']['kind'] in ('none', 'create', 'update', 'delete'), 'source.effect')
        require(case['effect']['kind'] == 'none' or 200 <= case['status'] < 300, 'source.effect-status')
    return source


def substitute(value, sessions):
    if isinstance(value, str) and value.startswith('$session:'):
        return sessions[value[9:]]
    if isinstance(value, dict):
        return {key: substitute(item, sessions) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, sessions) for item in value]
    return value


def route(path):
    return path.rstrip('/').rsplit('/', 1)[1]


def _parse_time(value):
    require(isinstance(value, str) and value.endswith('Z'), 'response.time-format')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def normalize(expected, actual, bindings, window, *, known_ids=()):
    """Return expected with placeholders replaced by validated actual values."""
    if isinstance(expected, str) and expected.startswith('$new:'):
        label = expected[5:]
        require(isinstance(actual, str) and str(UUID(actual)) == actual and UUID(actual).version == 4, 'response.new-uuid4')
        if label in bindings:
            require(bindings[label] == actual, 'response.new-binding')
        else:
            require(actual not in known_ids and actual not in bindings.values(), 'response.new-collision')
            bindings[label] = actual
        return actual
    if expected == '$now':
        started, finished = window
        moment = _parse_time(actual)
        require(started - 0.001 <= moment <= finished + 0.001, 'response.now-window')
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: normalize(value, actual.get(key), bindings, window, known_ids=known_ids) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [normalize(e, a, bindings, window, known_ids=known_ids) for e, a in zip(expected, actual)]
    return expected


def assert_case(case, base_url, *, token, sessions, bindings, known_ids):
    """Send one authored request and compare status, body and headers exactly."""
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    started = time.time()
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], case.get('body'), headers,
                                                             raw=True, include_headers=True)
    finished = time.time()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    try:
        expected = normalize(case['response'], actual, bindings, (started, finished), known_ids=known_ids)
    except AssertionError as error:
        raise ResponseFailure(str(error), status) from None
    check(type(actual) is type(expected) and actual == expected, 'response.body')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOW[route(case['path'])], 'response.allow')
    check(received.get('Set-Cookie') is None, 'response.cookie')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    return status, (started, finished)
