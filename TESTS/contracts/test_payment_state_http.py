"""Independently authored payment approve, cancel, delete and bulk archive/delete HTTP expectations; no Django imports.

The checks are http_family's (status, server error page, Allow, headers,
Bearer challenge, exact JSON), with two runner bindings substituted anywhere
in the expected body: '$default_category' / '$default_category_name' (the
first 'entrate e proventi da attività tipiche' category by key) and '$now'
(a payment date set from timezone.now(): a UTC 'Z' timestamp within ten
minutes of the request, which the runner also matches against the saved row).
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/payment_state_http.json'
# The last parsed response body, for the runner's '$now' cross-check.
LAST = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(path):
    path = path.split('?')[0]
    if path.startswith('/payment/') and path.endswith('/delete'):
        return {'DELETE', 'OPTIONS'}
    return {'POST', 'OPTIONS'}


def recent(text):
    if not isinstance(text, str) or not text.endswith('Z'):
        return False
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return False
    return abs(datetime.now(timezone.utc) - moment) < timedelta(minutes=10)


def resolve(expected, actual, bindings):
    """expected with the bindings filled and '$now' replaced by the actual recent timestamp."""
    if expected == '$now':
        return actual if recent(actual) else '<not a recent UTC timestamp>'
    if isinstance(expected, str):
        return bindings.get(expected, expected)
    if isinstance(expected, dict):
        other = actual if isinstance(actual, dict) else {}
        return {key: resolve(value, other.get(key), bindings) for key, value in expected.items()}
    if isinstance(expected, list):
        other = actual if isinstance(actual, list) and len(actual) == len(expected) else [None] * len(expected)
        return [resolve(value, item, bindings) for value, item in zip(expected, other)]
    return expected


def assert_case(case, base_url, *, token, sessions):
    LAST.clear()
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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case['path']), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    bindings = {key: value for key, value in sessions.items() if key.startswith('$')}
    expected = resolve(case['response'], actual, bindings)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    LAST['body'] = actual
    return status
