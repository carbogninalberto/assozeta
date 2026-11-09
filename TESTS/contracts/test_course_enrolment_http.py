"""Independently authored course enrolment HTTP expectations; no Django imports.

The shared http_family checks apply to every case. Responses render rows the
request creates, so the expected body may hold placeholders:
- '@name' matches a generated UUID, the same one wherever the name repeats in
  the case; the bindings are kept in BINDINGS for the runner's effect check;
- '$now' matches a timestamp within ten minutes of the request (DRF's UTC
  'Z' form or a datetime isoformat);
- '{today:FMT}' is the request's UTC date in strftime FMT, '{missing:T}' the
  medical label's day count (test_course_subscriptions_http.materialized);
- '$category', '$category_name' and the '$bank_far*' values are bound by the
  runner.
Unordered lists (installments, membership payments) are compared sorted.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_course_subscriptions_http import materialized
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_enrolment_http.json'
UNORDERED = {'installments': 'id', 'membership_payments': 'payment_id'}
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
TODAY = re.compile(r'\{today:([^}]+)\}')
BINDINGS = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0].rstrip('/')
    if path.endswith('/course-subscriptions/add'):
        # The viewset's own delete() answers DELETE on every mapped route.
        return {'POST', 'DELETE', 'OPTIONS'}
    return {'POST', 'OPTIONS'}


def today_tokens(value, now):
    return TODAY.sub(lambda match: now.strftime(match.group(1)), value)


def recent(text, now):
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return False
    return moment.tzinfo is not None and abs((now - moment).total_seconds()) < 600


def sort_unordered(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = sort_unordered(item)
            if key in UNORDERED and isinstance(item, list) and all(isinstance(entry, dict) for entry in item):
                item = sorted(item, key=lambda entry: str(entry.get(UNORDERED[key])))
            result[key] = item
        return result
    if isinstance(value, list):
        return [sort_unordered(item) for item in value]
    return value


def bind(expected, actual, binds, now):
    """expected with its placeholders replaced by the matching actual values."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: bind(item, actual.get(key), binds, now) if key in actual else item for key, item in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [bind(item, other, binds, now) for item, other in zip(expected, actual)]
    if isinstance(expected, str) and expected.startswith('@'):
        if expected not in binds and isinstance(actual, str) and UUID.match(actual) and actual not in binds.values():
            binds[expected] = actual
        return binds.get(expected, expected)
    if expected == '$now' and isinstance(actual, str) and recent(actual, now):
        return actual
    if isinstance(expected, str):
        for sym in sorted(binds, key=len, reverse=True):
            expected = expected.replace(sym, binds[sym])
    return expected


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], case.get('body'), headers, raw=True,
                                                            include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    BINDINGS[case['name']] = {}
    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    now = datetime.now(timezone.utc)
    bindings = {key: value for key, value in sessions.items() if key.startswith('$')}
    expected = json.loads(today_tokens(json.dumps(case['response'], ensure_ascii=False), now))
    expected = sort_unordered(materialized(expected, bindings, now))
    actual = sort_unordered(actual)
    binds = BINDINGS[case['name']]
    expected = bind(expected, actual, binds, now)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
