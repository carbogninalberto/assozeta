"""Independently authored instructor add/update/delete, hours/add/compensation and lessons-hours HTTP expectations; no Django imports.

The checks are http_family's, plus JSON types (lesson hours are Python floats:
2.0 must not render as 2). Responses may nest {'$unordered': [...]} (the
lesson-hours list, whose instructor order the view does not fix: compared as a
multiset of exact JSON values). The report's ``{'$xlsx': spec}`` workbook is
checked with the opt-in XLSX standard of TESTS/contracts/xlsx.py within the
request window.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts import xlsx
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instructor_writes_http.json'
ALLOWED = {'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}, 'compensation': {'POST', 'OPTIONS'},
           'lessons-hours': {'GET', 'OPTIONS'}, 'report': {'GET', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def resolve(expected, actual):
    """(expected, actual) with unordered lists sorted canonically on both sides."""
    if isinstance(expected, dict) and set(expected) == {'$unordered'}:
        items = expected['$unordered']
        if not isinstance(actual, list):
            return items, actual
        return sorted(items, key=_canonical), sorted(actual, key=_canonical)
    if isinstance(expected, dict) and isinstance(actual, dict):
        pairs = {key: resolve(value, actual.get(key)) for key, value in expected.items()}
        resolved = {key: pair[1] for key, pair in pairs.items()}
        resolved.update({key: value for key, value in actual.items() if key not in expected})
        return {key: pair[0] for key, pair in pairs.items()}, resolved
    return expected, actual


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    started = datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    finished = datetime.now(timezone.utc)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED[case['path'].split('?')[0].split('/')[-1]], 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected, reason = xlsx.resolve(case['response'], actual, started, finished)
    check(reason is None, f'response.xlsx: {reason}'[:600])
    expected, actual = resolve(expected, actual)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(_canonical(actual) == _canonical(expected), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
