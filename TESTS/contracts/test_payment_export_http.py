"""Independently authored payment/list/export HTTP expectations; no Django imports.

The route is @api_view(['GET']): Allow is GET, OPTIONS. The checks are
http_family.assert_case's, with the export body's two placeholders:
- ``{'$csv': text}`` is the base64 of the UTF-8 CSV text, compared byte for
  byte;
- ``{'$xlsx': spec}`` is an m=xlsx workbook, checked part by part with the
  opt-in XLSX standard of TESTS/contracts/xlsx.py within the request window;
- ``$today`` in the filename is ``date.today()`` of the server (UTC), read
  before and after the request (a run across midnight accepts either day).
"""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import xlsx
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/payment_export_http.json'
ALLOWED = {'GET', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _expected(value, today):
    if isinstance(value, dict):
        if set(value) == {'$csv'}:
            return base64.b64encode(value['$csv'].encode()).decode()
        return {key: _expected(item, today) for key, item in value.items()}
    if isinstance(value, list):
        return [_expected(item, today) for item in value]
    if isinstance(value, str):
        return value.replace('$today', today)
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    started = datetime.now(timezone.utc)
    before = _today()
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)
    after = _today()
    finished = datetime.now(timezone.utc)

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
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    resolved, reason = xlsx.resolve(case['response'], actual, started, finished)
    check(reason is None, f'response.xlsx: {reason}'[:600])
    candidates = [_expected(resolved, day) for day in dict.fromkeys((before, after))]
    if actual not in candidates:
        check(False, 'response.body' + difference(candidates[-1], actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
