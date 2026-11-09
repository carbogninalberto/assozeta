"""Independently authored misc-routes HTTP expectations; no Django imports.

calendar/events/export is @api_view(['GET']) (Allow: GET, OPTIONS),
communications/send/post @api_view(['POST']) and subscription/<uid>/card
@api_view(['GET', 'POST']). JSON answers use http_family.assert_case (the
card POST's token is a generated '$new' string; the runner checks it against
the stored row). A calendar body ``{'$ics': text}`` is compared byte for byte
as text/calendar with the attachment disposition, after each
``DTSTAMP:<UTC>Z`` value is checked to lie within the request window (one
second of slack on each side) and replaced by ``$dtstamp``.
"""
from datetime import datetime, timezone
from pathlib import Path
import re

from TESTS.contracts import http_family as family
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/misc_routes_http.json'
DTSTAMP = re.compile(rb'DTSTAMP:(\d{8}T\d{6})Z\r\n')
# The last parsed JSON answer, for the runner's effect check (the minted token).
ANSWERED = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0]
    if path.endswith('/card'):
        return {'GET', 'POST', 'OPTIONS'}
    if path == '/communications/send/post':
        return {'POST', 'OPTIONS'}
    return {'GET', 'OPTIONS'}


def _calendar_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    before = datetime.now(timezone.utc).timestamp()
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)
    after = datetime.now(timezone.utc).timestamp()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') is None, 'response.authentication')
    check(received.get('Content-Type') == 'text/calendar', 'response.type')
    check(received.get('Content-Disposition') == 'attachment; filename="events.ics"', 'response.disposition')
    for match in DTSTAMP.finditer(raw):
        moment = datetime.strptime(match.group(1).decode(), '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()
        check(before - 1 <= moment <= after + 1, 'response.dtstamp-window')
    actual = DTSTAMP.sub(b'DTSTAMP:$dtstamp\r\n', raw)
    expected = case['response']['$ics'].encode()
    if actual != expected:
        check(False, 'response.body' + difference(expected.decode().split('\r\n'), actual.decode('utf-8', 'replace').split('\r\n')))
    return status


def assert_case(case, base_url, *, token, sessions):
    if isinstance(case['response'], dict) and set(case['response']) == {'$ics'}:
        return _calendar_case(case, base_url, token=token, sessions=sessions)
    ANSWERED.clear()

    def remember(case, expected, actual):
        ANSWERED['body'] = actual
        return expected, actual

    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, new=('token',), normalize=remember)
