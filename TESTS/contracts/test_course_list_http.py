"""Independently authored course/list HTTP expectations; no Django imports.

Every check of http_family applies (exact status, error page, Allow, headers,
exact JSON); Content-Language is the case's Accept-Language ('en' by default).
Two kinds of values are resolved before the comparison:
- '{"$current_year": "YYYY-MM-DD"}' is course_list's current_year at request time:
  1 when that date's midnight lies within the request user's balance year
  (BalanceSheetData.get_range_from_year_and_starting_date(now, 1, 1) with
  TIME_ZONE='UTC': 1 January to 31 December of now's year), else 0;
- unordered collections are compared as multisets: each course's tags and
  locations (unordered M2M reads), and each course's athletes, a dict keyed
  '0'..'n-1' over an unordered query whose keys must be exactly that range;
- '$today' in a string is the %d/%m/%Y date of date.today() (UTC), read before and
  after the request (a run across midnight accepts either day), and
  '{"$xlsx": spec}' is a workbook checked part by part with the opt-in XLSX
  standard of TESTS/contracts/xlsx.py within the request window.
"""
import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts import xlsx
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_list_http.json'
ALLOWED = {'GET', 'POST', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def current_year(created, now=None):
    """The owner fixtures keep the default balance start (day 1, month 1)."""
    now = now or datetime.now(timezone.utc)
    return 1 if date(now.year, 1, 1) <= date.fromisoformat(created) <= date(now.year, 12, 31) else 0


def _resolve(value):
    if isinstance(value, dict):
        if set(value) == {'$current_year'}:
            return current_year(value['$current_year'])
        return {key: _resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    return value


def _today():
    return datetime.now(timezone.utc).date()


def _dated(value, today):
    if isinstance(value, dict):
        return {key: _dated(item, today) for key, item in value.items()}
    if isinstance(value, list):
        return [_dated(item, today) for item in value]
    if isinstance(value, str):
        return value.replace('$today', today.strftime('%d/%m/%Y'))
    return value


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _course(value, check):
    if not isinstance(value, dict):
        return value
    value = dict(value)
    for name in ('tags', 'locations'):
        if isinstance(value.get(name), list):
            value[name] = sorted(value[name], key=_key)
    athletes = value.get('athletes')
    if isinstance(athletes, dict):
        check(sorted(athletes) == sorted(str(i) for i in range(len(athletes))), 'response.athletes-keys')
        value['athletes'] = sorted(athletes.values(), key=_key)
    return value


def _normalize(body, check):
    if not isinstance(body, dict) or 'data' not in body:
        return body
    data = body['data']
    if isinstance(data, list):
        data = [_course(item, check) for item in data]
    elif isinstance(data, dict):
        data = {key: _course(item, check) for key, item in data.items()}
    return {**body, 'data': data}


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    before, started = _today(), datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    finished, after = datetime.now(timezone.utc), _today()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    language = headers['Accept-Language']
    check(received.get('Content-Language') == language and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    actual = _normalize(actual, check)
    reasons = []
    for day in dict.fromkeys((before, after)):
        expected = _normalize(_resolve(_dated(copy.deepcopy(case['response']), day)), check)
        expected, reason = xlsx.resolve(expected, actual, started, finished)
        if reason is None and actual == expected:
            break
        reasons.append(f'response.xlsx: {reason}'[:600] if reason else 'response.body' + difference(expected, actual))
    else:
        check(False, reasons[-1])
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
