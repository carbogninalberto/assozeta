"""Independently authored chat/test/ HTTP expectations; no Django imports.

The page answers are not DRF responses: every case checks the exact status,
Location, Set-Cookie (or its absence), Vary, Content-Language and the body
('<empty>' for redirects, the stable 500 page or the CSRF failure page otherwise; HEAD has no body).
"""
import re
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure
from TESTS.http_client import Client

NEW_CSRF_COOKIE = '<new-csrftoken-cookie>'
NEW_COOKIE = re.compile(r'csrftoken=[A-Za-z0-9]{32}; expires=[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT; '
                        r'Max-Age=31449600; Path=/; SameSite=Lax(; Secure)?')
SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/chat_test_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], **case['headers']}  # a case may name its own language
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers,
                                                             raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(received.get('Location') == case['location'], 'response.location')
    cookies = received.get_all('Set-Cookie')
    if case['set_cookie'] == NEW_CSRF_COOKIE:
        check(cookies is not None and len(cookies) == 1 and NEW_COOKIE.fullmatch(cookies[0]) is not None, 'response.set-cookie')
    else:
        check(cookies == ([case['set_cookie']] if case['set_cookie'] else None), 'response.set-cookie')
    # Repeated Vary field lines are one comma-joined list (RFC 9110 5.3).
    check(', '.join(received.get_all('Vary') or []) == case['vary'], 'response.vary')
    check(received.get('Content-Language') == case['language'], 'response.language')
    check(received.get('Content-Type') == 'text/html; charset=utf-8', 'response.type')
    if case['method'] == 'HEAD' or case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
    elif case['response'].startswith('<!DOCTYPE html>'):
        check(raw.decode() == case['response'], 'response.csrf-page')
    else:
        check(case['response'] == '<stable-server-error-page>' and raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
    return status
