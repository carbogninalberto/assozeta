"""Independently authored export-all-data HTTP expectations; no Django imports.

export_all_data is @api_view(['POST']) (Allow POST, OPTIONS). A workbook
response is '{"$xlsx": spec, "filename": name}': the raw body is the plain
openpyxl workbook of the spec, checked part by part with the opt-in XLSX
standard of TESTS/contracts/xlsx.py within the request window, with
Content-Type application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
and Content-Disposition 'attachment; filename=<name>'. Other responses are
exact JSON (or the stable 500 page).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import xlsx
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/export_all_data_http.json'
ALLOWED = {'POST', 'OPTIONS'}
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    started = datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], case.get('body'), headers, raw=True,
                                                            include_headers=True)
    finished = datetime.now(timezone.utc)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    expected = case['response']
    if expected == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if isinstance(expected, dict) and '$xlsx' in expected:
        check(received.get('Content-Type') == XLSX_TYPE, 'response.type')
        check(received.get('Content-Disposition') == f"attachment; filename={expected['filename']}", 'response.disposition')
        reason = xlsx.difference(xlsx.build(expected['$xlsx']), raw, started, finished)
        check(reason is None, f'response.xlsx: {reason}'[:600])
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
