"""Independently authored payment list, payment stats and subscription payments HTTP expectations; no Django imports.

Every route is @api_view(['GET']): Allow is GET, OPTIONS. The checks are
http_family.assert_case's, with three family-specific differences:
- subscription_payments answers athletes with a bare Response(status=401)
  (no authentication challenge): cases with 'challenge': false expect no
  WWW-Authenticate header;
- related managers without ordering (an associate's tutor relations) are
  compared sorted by the tutor's id;
- 'unordered' cases (a random ordering) compare the rows and rowIds as
  multisets.
"""
import json
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/payment_reads_http.json'
ALLOWED = {'GET', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def _tutor_key(item):
    tutor = item.get('tutor') if isinstance(item, dict) else None
    return str(tutor.get('associate_id')) if isinstance(tutor, dict) else json.dumps(item, sort_keys=True)


def normalize(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalize(item)
            if key == 'available_tutors' and isinstance(item, list):
                item = sorted(item, key=_tutor_key)
            result[key] = item
        return result
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def unordered(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(result.get('data'), list):
        result['data'] = sorted(result['data'], key=lambda item: json.dumps(item, sort_keys=True))
    meta = result.get('meta')
    if isinstance(meta, dict) and isinstance(meta.get('rowIds'), list):
        result['meta'] = {**meta, 'rowIds': sorted(meta['rowIds'], key=str)}
    return result


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    challenge = case.get('challenge', status == 401)
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if challenge else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected, actual = normalize(case['response']), normalize(actual)
    if case['unordered']:
        expected, actual = unordered(expected), unordered(actual)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
