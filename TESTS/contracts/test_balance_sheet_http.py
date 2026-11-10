"""Independently authored balance-sheet and balance-sheet/accounts/<uid>/delete HTTP expectations; no Django imports.

balance_sheet is @api_view(['GET', 'POST', 'DELETE']); balance_sheet_accounts_delete
is @api_view(['DELETE']). Request-time values are placeholders: '$now' is a
generated UTC timestamp, '$current_year' today's year, '$available_years' the
six balance years ending with today's (the fixture owner's year starts on
1 January) and {'$profile': id} SportAssociationSerializer, characterized by the
profile families (its keys and ID are checked here; the runner requires it to
be identical across backends). '$today' in a string is the %d/%m/%Y date of
date.today() (UTC), read before and after the request, and '{"$xlsx": spec}'
is a workbook checked part by part with the opt-in XLSX standard of
TESTS/contracts/xlsx.py within the request window.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts import pdf, xlsx
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_balance_archived_http import source_keys
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/balance_sheet_http.json'
LAST_PROFILE = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def current_year():
    return datetime.now(timezone.utc).year


def available_years(year):
    return [{'year': y, 'start_date': f'{y}-01-01T00:00:00Z'} for y in range(year - 5, year + 1)]


def dated(value, today):
    if isinstance(value, dict):
        return {key: dated(item, today) for key, item in value.items()}
    if isinstance(value, list):
        return [dated(item, today) for item in value]
    if isinstance(value, str):
        return value.replace('$today', today.strftime('%d/%m/%Y'))
    return value


def resolve(expected, actual, case_name, check):
    """expected with its placeholders replaced by the accepted actual values."""
    if expected == '$now':
        check(isinstance(actual, str) and actual.endswith('Z'), 'response.now')
        return actual
    if expected == '$current_year':
        return current_year()
    if expected == '$available_years':
        return available_years(current_year())
    if isinstance(expected, dict) and set(expected) == {'$profile'}:
        check(isinstance(actual, dict) and sorted(actual) == source_keys() and actual.get('sport_association_id') == expected['$profile'],
              'response.association')
        LAST_PROFILE[case_name] = actual
        return actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: resolve(value, actual.get(key), case_name, check) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [resolve(value, item, case_name, check) for value, item in zip(expected, actual)]
    return expected


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    before, started = datetime.now(timezone.utc).date(), datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    finished, after = datetime.now(timezone.utc), datetime.now(timezone.utc).date()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    allowed = {'DELETE', 'OPTIONS'} if case['path'].startswith('/balance-sheet/accounts/') else {'GET', 'POST', 'DELETE', 'OPTIONS'}
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    reasons = []
    for day in dict.fromkeys((before, after)):
        expected = resolve(dated(case['response'], day), actual, case['name'], check)
        expected, reason = xlsx.resolve(expected, actual, started, finished)
        if reason is None:
            expected, reason = pdf.resolve(expected, actual, started, finished)
        if reason is None and actual == expected:
            break
        reasons.append(f'response.file: {reason}'[:600] if reason else 'response.body' + difference(expected, actual))
    else:
        check(False, reasons[-1])
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
