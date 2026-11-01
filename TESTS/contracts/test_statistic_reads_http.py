"""Independently authored statistic/dashboard, athlete-dashboard and report HTTP expectations; no Django imports.

All three routes are @api_view(['GET']): Allow is GET, OPTIONS. Bodies are compared through their JSON
renderings (sorted keys), so the integer 0 and the float 0.0 of DRF's Decimal/float output stay distinct.

Time specs (see the generator) are resolved against the seed anchor, a whole-second UTC instant fixed by
the runner. The servers run with TZ=UTC, so naive ``datetime.now()`` is UTC. Request-time placeholders:
- {'$drf': spec}: DRF's JSONEncoder datetime (isoformat, '+00:00' -> 'Z');
- {'$date': spec}: date isoformat; {'$dmy': spec}: '%d/%m/%Y'; {'$fmt': [spec, format]}: strftime;
- {'$available_years': [day, month]}: the previous and current balance-sheet year starts from today;
- {'$month_series': {'start_month': m, 'value': v}}: twelve months from m, v at today's month, else 0;
- {'$income': rows}: the incomeAndExpenses totals over rows (amount, expense, at) for the request time.
A case fails with 'fixture.day-rollover' when the UTC date changed after seeding.
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from TESTS.http_client import Client
from TESTS.contracts.http_family import load_source as _load, require
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/statistic_reads_http.json'
__all__ = ('require', 'load_source', 'assert_case', 'resolve', 'materialize')


def load_source(path=SOURCE):
    return _load(path, minimum=60)


def resolve(spec, anchor):
    """A time spec as an aware UTC datetime (date for a bare {'day': k} or 'YYYY-MM-DD')."""
    if isinstance(spec, str):
        if len(spec) == 10:
            return date.fromisoformat(spec)
        return datetime.fromisoformat(spec.replace('Z', '+00:00'))
    if 'now' in spec:
        return anchor + timedelta(seconds=spec['now'], microseconds=spec.get('us', 0))
    base = anchor.date() + timedelta(days=spec['day'])
    if 'at' not in spec:
        return base
    return datetime.combine(base, time.fromisoformat(spec['at']), tzinfo=timezone.utc)


def drf(value):
    representation = value.isoformat()
    return representation[:-6] + 'Z' if representation.endswith('+00:00') else representation


def balance_start(today, day, month):
    """BalanceSheetData.get_range_from_year_and_starting_date(...)[0] for a naive date at 00:00 (UTC)."""
    current = datetime(today.year, month, day, tzinfo=timezone.utc)
    moment = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return current if moment >= current else datetime(today.year - 1, month, day, tzinfo=timezone.utc)


def income(rows, anchor, now):
    """incomeAndExpenses: pytz keeps the current Europe/Rome offset for every derived bound."""
    offset = now.astimezone(ZoneInfo('Europe/Rome')).utcoffset()
    rome_now = now.astimezone(timezone(offset))
    today_start = rome_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    windows = {'today': today_start, 'last_month': today_start - timedelta(days=31), 'total': today_start.replace(month=1, day=1)}
    out = {}
    for name, start in windows.items():
        for kind, expense in (('income', False), ('expenses', True)):
            values = [Decimal(row['amount']) for row in rows if row['expense'] == expense
                      and start <= resolve(row['at'], anchor) <= today_end]
            out[f'{name}_{kind}'] = float(sum(values)) if values else None
    return out


def materialize(value, anchor, now):
    if isinstance(value, list):
        return [materialize(item, anchor, now) for item in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        (key, argument), = value.items()
        today = now.date()
        if key == '$drf':
            return drf(resolve(argument, anchor))
        if key == '$date':
            return resolve(argument, anchor).isoformat()
        if key == '$dmy':
            return resolve(argument, anchor).strftime('%d/%m/%Y')
        if key == '$fmt':
            return resolve(argument[0], anchor).strftime(argument[1])
        if key == '$available_years':
            start = balance_start(today, *argument)
            previous = start.replace(year=start.year - 1)
            return [{'year': start.year - 1, 'start_date': drf(previous)}, {'year': start.year, 'start_date': drf(start)}]
        if key == '$month_series':
            series = [0] * 12
            series[(today.month - argument['start_month']) % 12] = argument['value']
            return series
        if key == '$income':
            return income(argument, anchor, now)
    return {key: materialize(item, anchor, now) for key, item in value.items()}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def assert_case(case, base_url, *, token, sessions, anchor):
    """anchor: the seed's whole-second UTC instant."""
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)
    now = datetime.now(timezone.utc)
    require(now.date() == anchor.date(), 'fixture.day-rollover')

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
    expected = materialize(case['response'], anchor, now)
    check(canonical(actual) == canonical(expected), 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
