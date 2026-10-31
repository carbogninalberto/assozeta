"""Shared authored HTTP expectations for one Django/Go parity family; no Django imports.

A family contract names its fixture and how its responses vary; every check
below applies to every case:
- the exact status, and for '<stable-server-error-page>' the exact HTML page;
- the Allow set from `allowed(case)`, Content-Language 'en', no Set-Cookie and
  the Bearer challenge only on a 401;
- '<empty>' for a response without a body (DRF's Response(status=...));
- otherwise exact JSON equality, after accepting generated values for `new`
  keys and request-time values for `now` keys, and after sorting the
  `unordered` lists by their identity field.
"""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')


def load_source(path, *, minimum=10):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= minimum, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def _at(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _sorted(value, path, field):
    """Copy of value with the list at path sorted by field (missing paths unchanged)."""
    if not path:
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return sorted(value, key=lambda item: str(item.get(field)))
        return value
    if not isinstance(value, dict) or path[0] not in value:
        return value
    return {**value, path[0]: _sorted(value[path[0]], path[1:], field)}


def assert_case(case, base_url, *, token, sessions, allowed, new=(), now=(), unordered=()):
    """Check one authored case.

    allowed(case) -> the exact Allow method set (OPTIONS included by the caller).
    new / now: top-level response keys whose '$new' / '$now' placeholder accepts
    a generated string (the runner checks the effect) / a UTC 'Z' timestamp.
    unordered: (path tuple, identity field) pairs of unordered lists.
    """
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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == set(allowed(case)), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    if isinstance(expected, dict):
        for key in new:
            if expected.get(key) == '$new':
                check(isinstance(actual, dict) and isinstance(actual.get(key), str) and actual[key], 'response.new-' + key)
                expected = {**expected, key: actual[key]}
        for key in now:
            if expected.get(key) == '$now':
                check(isinstance(actual, dict) and isinstance(actual.get(key), str) and actual[key].endswith('Z'), 'response.now-' + key)
                expected = {**expected, key: actual[key]}
    for path, field in unordered:
        if isinstance(_at(expected, path), list):
            expected, actual = _sorted(expected, path, field), _sorted(actual, path, field)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
