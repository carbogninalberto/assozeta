"""Independently authored printing/generate HTTP expectations; no Django imports.

printing_generate is @api_view(['POST']) (Allow POST, OPTIONS). Dates are
authored relative to the day of the run: '$today' and '$date:<offset>' in the
request body and the expectation are the %d/%m/%Y dates of date.today()
(UTC) plus the offset in days, read before and after the request (a run
across midnight accepts either day). '{"$xlsx": spec}' is a workbook checked
part by part with the opt-in XLSX standard of TESTS/contracts/xlsx.py within
the request window, '{"$pdf": spec}' a reportlab document checked with the
opt-in PDF standard of TESTS/contracts/pdf.py; '<empty>' is a response
without a body.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import pdf, xlsx
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure', 'dated')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/printing_generate_http.json'
ALLOWED = {'POST', 'OPTIONS'}
DATE = re.compile(r'^\$date:(-?\d+)$')


def load_source(path=SOURCE):
    return family.load_source(path)


def _today():
    return datetime.now(timezone.utc).date()


def dated(value, today):
    """value with '$today' and '$date:<offset>' replaced for the given day."""
    if isinstance(value, dict):
        return {key: dated(item, today) for key, item in value.items()}
    if isinstance(value, list):
        return [dated(item, today) for item in value]
    if isinstance(value, str):
        match = DATE.match(value)
        if match:
            return (today + timedelta(days=int(match.group(1)))).strftime('%d/%m/%Y')
        return value.replace('$today', today.strftime('%d/%m/%Y'))
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    before = _today()
    body = dated(case.get('body'), before)
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    started = datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    finished = datetime.now(timezone.utc)
    after = _today()

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
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    reasons = []
    for day in dict.fromkeys((before, after)):
        expected, reason = xlsx.resolve(dated(case['response'], day), actual, started, finished)
        if reason is None:
            expected, reason = pdf.resolve(expected, actual, started, finished)
        if reason is None and actual == expected:
            break
        reasons.append(f'response.file: {reason}'[:600] if reason else 'response.body' + difference(expected, actual))
    else:
        check(False, reasons[-1])
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
