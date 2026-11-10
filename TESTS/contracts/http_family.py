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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')


def day_first_dates(today):
    """Birth dates whose two readings fall on either side of the 18th birthday, both numbers at most 12.

    '$day_first_minor': D/M/YYYY of a person under 18 today who would be 18 with the numbers read month first;
    '$day_first_adult': the reverse. The '_iso' forms are the day-first reading as YYYY-MM-DD. On 1 January no such
    pair exists for a minor: the date is then the day after the 18th birthday (under 18 in the day-first reading only).
    """
    try:
        limit = today.replace(year=today.year - 18)
    except ValueError:  # 29 February
        limit = today.replace(year=today.year - 18, day=28)
    values = {}
    for name, forward in (('minor', True), ('adult', False)):
        found = None
        for offset in range(1, 400):
            born = limit + timedelta(days=offset) if forward else limit - timedelta(days=offset - 1)
            if born.day > 12 or born.day == born.month:
                continue
            other = date(born.year, born.day, born.month)
            if (other <= limit) if forward else (other > limit):
                found = born
                break
        if found is None:
            found = limit + timedelta(days=1) if forward else limit
        values[f'$day_first_{name}'] = found.strftime('%d/%m/%Y')
        values[f'$day_first_{name}_iso'] = found.isoformat()
    return values


def _resolve_dates(value, values):
    if isinstance(value, str):
        return values.get(value, value)
    if isinstance(value, dict):
        return {key: _resolve_dates(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_dates(item, values) for item in value]
    return value


def load_source(path, *, minimum=10):
    source = _resolve_dates(json.loads(Path(path).read_text()), day_first_dates(datetime.now(timezone.utc).date()))
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


def assert_case(case, base_url, *, token, sessions, allowed, new=(), now=(), unordered=(), normalize=None, challenged=None):
    """Check one authored case.

    allowed(case) -> the exact Allow method set (OPTIONS included by the caller).
    new / now: top-level response keys whose '$new' / '$now' placeholder accepts
    a generated string (the runner checks the effect) / a UTC 'Z' timestamp.
    unordered: (path tuple, identity field) pairs of unordered lists.
    normalize(case, expected, actual) -> (expected, actual): optional family
    hook applied before the unordered sort (for example verified signed tokens).
    challenged(case, status) -> whether the response carries the Bearer
    challenge; by default every 401 (DRF's NotAuthenticated) does, while a
    view's own Response(status=401) does not.
    """
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    elif 'raw_b64' in case:
        # Binary bodies (multipart uploads of archives).
        import base64
        body = base64.b64decode(case['raw_b64'], validate=True)
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
    challenge = challenged(case, status) if challenged is not None else status == 401
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if challenge else None), 'response.authentication')
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
    if normalize is not None:
        expected, actual = normalize(case, expected, actual)
    for path, field in unordered:
        if isinstance(_at(expected, path), list):
            expected, actual = _sorted(expected, path, field), _sorted(actual, path, field)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
