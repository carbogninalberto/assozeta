"""Independently authored instructor/<uid>/info HTTP expectations; no Django imports."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instructor_info_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def hour_date(text):
    return date.today() if text == '$today' else date.fromisoformat(text)


def window(spec):
    """The view's date window, from today's local date like datetime.now()."""
    now = datetime.now()
    if spec == 'month':
        start = now.date().replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if spec == 'fallback':
        return (now - timedelta(days=365)).date(), (now + timedelta(days=1)).date()
    for pattern in ('%Y/%m/%d', '%d/%m/%Y'):
        try:
            return tuple(datetime.strptime(text, pattern).date() for text in spec)
        except ValueError:
            continue
    return window('fallback')


def expected_stats(spec):
    start, end = window(spec['$window'])
    rows = [h for h in load_source()['hours'] if h['instructor'] == spec['instructor'] and start <= hour_date(h['date']) <= end]
    if not rows:
        return {'hours': 0, 'total_amount': 0, 'total_amount_paid': 0, 'total_amount_to_pay': 0}
    # Python sum() of Decimals starts at int 0; DRF's encoder renders Decimal as float.
    total = lambda values: float(sum(values)) if values else 0
    return {'hours': total([Decimal(h['hours']) for h in rows]), 'total_amount': total([Decimal(h['amount']) for h in rows]),
            'total_amount_paid': total([Decimal(h['amount']) for h in rows if h['paid']]),
            'total_amount_to_pay': total([Decimal(h['amount']) for h in rows if not h['paid']])}


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = {} if case['method'] == 'POST' else None
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    if isinstance(expected, dict) and isinstance(expected.get('stats'), dict) and '$window' in expected['stats']:
        expected = {**expected, 'stats': expected_stats(expected['stats'])}
    if isinstance(actual, dict) and isinstance(actual.get('data'), dict) and isinstance(actual['data'].get('documents'), list):
        actual = {**actual, 'data': {**actual['data'], 'documents': sorted(actual['data']['documents'])}}
        expected = {**expected, 'data': {**expected['data'], 'documents': sorted(expected['data']['documents'])}}
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
