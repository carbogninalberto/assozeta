"""Independently authored unmatched-URL HTTP expectations; no Django imports.

Every case checks the exact status, Location, Set-Cookie (or its absence),
the comma-joined Vary lines, Content-Language, Content-Type, Allow (never
set: no view ran) and the body: Django's default 404 page, or empty for a
redirect or HEAD, and the 500 page for an /api/ path with a Bearer token.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure
from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/unknown_paths_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], **case['headers']}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers,
                                                             raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(received.get('Location') == case['location'], 'response.location')
    check(received.get_all('Set-Cookie') == ([case['set_cookie']] if case['set_cookie'] else None), 'response.set-cookie')
    # Repeated Vary field lines are one comma-joined list (RFC 9110 5.3).
    check(', '.join(received.get_all('Vary') or []) == case['vary'], 'response.vary')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(received.get('Content-Type') == 'text/html; charset=utf-8', 'response.type')
    check(received.get('Allow') is None and received.get('WWW-Authenticate') is None, 'response.no-view-headers')
    check(received.get('X-Content-Type-Options') == 'nosniff', 'response.nosniff')
    if case['method'] == 'HEAD' or case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
    elif case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
    else:
        check(raw == case['response'].encode(), 'response.not-found-page')
    return status
