"""Independently authored subscription calculate-tax-code, import/status, get-associations-for-federation,
generate-token-link, validate-token-link-and-get-subscriptions and delete-document HTTP expectations;
no Django imports.

The checks are http_family's, plus:
- the Allow set is the route's one method and OPTIONS;
- '$new' accepts a generated uuid4 token and '$later' an ISO 'Z' timestamp two
  days after the request (the runner compares both with the stored link, kept in
  ``LAST``);
- ``unordered`` names the identity field of a list Django leaves to the database
  order (the top-level list, or ``subscriptions``); an expected ``id`` given as a
  list accepts any of its values (the first of duplicate denominations wins);
- JSON types are compared exactly (Python equality conflates True with 1).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_misc_http.json'
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z')
LAST = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0].rstrip('/')
    if '/delete-document/' in path:
        return {'DELETE', 'OPTIONS'}
    if path.endswith(('calculate-tax-code', 'generate-token-link')):
        return {'POST', 'OPTIONS'}
    return {'GET', 'OPTIONS'}


def accept(expected, actual, started):
    """expected with the '$new' / '$later' / id-choice leaves resolved against actual."""
    if expected == '$new':
        return actual if isinstance(actual, str) and UUID4.fullmatch(actual) else expected
    if expected == '$later':
        if isinstance(actual, str) and STAMP.fullmatch(actual):
            moment = datetime.fromisoformat(actual.replace('Z', '+00:00'))
            if started + timedelta(days=2) - timedelta(seconds=5) <= moment <= datetime.now(timezone.utc) + timedelta(days=2, seconds=5):
                return actual
        return expected
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = {key: accept(value, actual.get(key), started) for key, value in expected.items()}
        if isinstance(expected.get('id'), list) and actual.get('id') in expected['id']:
            result['id'] = actual['id']
        return result
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [accept(e, a, started) for e, a in zip(expected, actual)]
    return expected


def ordered(value, field):
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return sorted(value, key=lambda item: str(item.get(field)))
    if isinstance(value, dict) and isinstance(value.get('subscriptions'), list):
        return {**value, 'subscriptions': ordered(value['subscriptions'], field)}
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    started = datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    LAST.clear()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

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
    expected = case['response']
    if case.get('unordered'):
        expected, actual = ordered(expected, case['unordered']), ordered(actual, case['unordered'])
    expected = accept(expected, actual, started)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    LAST['body'] = actual
    return status
