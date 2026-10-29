"""Independently authored email-logs HTTP expectations, shared by all transports.

No Django imports. ``$row:<label>`` materializes from the authored seed and the
runner's anchor (sent_at = anchor - offset), never from responses.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/email_logs_http.json'
SERVER_ERROR_DOCUMENT = (b'\n<!doctype html>\n<html lang="en">\n<head>\n'
    b'  <title>Server Error (500)</title>\n</head>\n<body>\n'
    b'  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n')


def require(condition, code):
    if not condition:
        raise AssertionError(code)


class ResponseFailure(AssertionError):
    def __init__(self, code, status):
        self.code, self.actual_status = code, status
        super().__init__(code)


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    margin = source['window_margin_seconds']
    for row in source['rows'].values():
        for boundary in (86400, 90 * 86400):
            require(abs(row['offset'] - boundary) >= margin, 'source.boundary-margin')
    return source


def drf_time(value):
    text = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    return text + 'Z'


def materialize(value, source, anchor):
    if isinstance(value, str) and value.startswith('$row:'):
        row = source['rows'][value[5:]]
        return {'email_log_id': row['id'], 'recipient': row['recipient'], 'subject': row['subject'],
                'sent_at': drf_time(anchor - timedelta(seconds=row['offset'])), 'result': row['result']}
    if isinstance(value, dict):
        return {key: materialize(item, source, anchor) for key, item in value.items()}
    if isinstance(value, list):
        return [materialize(item, source, anchor) for item in value]
    return value


def difference(expected, actual):
    """Name the first differing key or row field; never include values."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return ':keys'
        for key in expected:
            if expected[key] != actual[key]:
                return ':' + key + difference(expected[key], actual[key])
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return ':length'
        for index, (left, right) in enumerate(zip(expected, actual)):
            if left != right:
                return f'[{index}]' + difference(left, right)
    return ''


def substitute(value, sessions):
    if isinstance(value, str) and value.startswith('$session:'):
        return sessions[value[9:]]
    if isinstance(value, dict):
        return {key: substitute(item, sessions) for key, item in value.items()}
    return value


def assert_case(case, base_url, *, token, sessions, source, anchor):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = {} if case['method'] == 'POST' else None
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers,
                                                             raw=True, include_headers=True)

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
    expected = materialize(case['response'], source, anchor)
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('Set-Cookie') is None, 'response.cookie')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    return status
