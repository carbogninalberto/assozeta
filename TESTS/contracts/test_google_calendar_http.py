"""Independently authored google/* HTTP expectations; no Django imports.

The checks are http_family's, plus:
- '{state:<label>}' in a case path is the state the earlier config case
  answered for that user (STATES);
- a config GET answer's authorization_url is the authored URL with '{state}'
  replaced by the answered state, which must be oauthlib's generate_token()
  shape (30 ASCII letters and digits) and becomes STATES[state_for];
- 'html' cases answer the exact page as 'text/html' with the family headers;
- a config GET answer (SessionMiddleware saves the new session holding the
  state) carries Django's session cookie: a new 32-character key, the
  14-day expiry and its attributes (Secure only when the run's Django
  setting is on); SESSIONS[case] keeps the key for the adapter.
"""
from pathlib import Path
import re

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/google_calendar_http.json'
STATE = re.compile(r'[?&]state=([A-Za-z0-9]{30})(?=&)')
STATES = {}
SESSIONS = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0]
    if path.endswith('/export'):
        return {'POST', 'OPTIONS'}
    if path == '/google/calendar/config':
        return {'GET', 'DELETE', 'OPTIONS'}
    return {'GET', 'OPTIONS'}


def bind(case):
    path = re.sub(r'\{state:(\w+)\}', lambda m: STATES.get(m.group(1), 'unbound-state'), case['path'])
    return {**case, 'path': path}


def normalize(case, expected, actual):
    if isinstance(expected, dict) and 'authorization_url' in expected and isinstance(actual, dict):
        match = STATE.search(actual.get('authorization_url') or '')
        if match is None:
            raise ResponseFailure('response.state', case['status'])
        state = match.group(1)
        expected = {**expected, 'authorization_url': expected['authorization_url'].replace('{state}', state)}
        STATES[case['state_for']] = state
    return expected, actual


def cookie_pattern(secure):
    return re.compile(r'sessionid=([a-z0-9]{32}); expires=[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT; '
                      r'HttpOnly; Max-Age=1209600; Path=/; SameSite=Lax' + ('; Secure' if secure else '') + r'\Z')


def assert_session(case, base_url, *, token, sessions):
    """http_family's checks for a config GET, with the session cookie instead of no Set-Cookie."""
    import json
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)
    check(status == case['status'], 'response.status')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('WWW-Authenticate') is None, 'response.headers')
    match = cookie_pattern(case['secure_cookie']).match(received.get('Set-Cookie') or '')
    check(match is not None, 'response.session-cookie')
    SESSIONS[case['name']] = match.group(1)
    check(received.get('Content-Type') == 'application/json', 'response.type')
    try:
        actual = json.loads(raw)
    except ValueError:
        raise ResponseFailure('response.json', status) from None
    expected, actual = normalize(case, case['response'], actual)
    check(actual == expected, 'response.body')
    return status


def assert_html(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)
    check(status == case['status'], 'response.status')
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') is None, 'response.authentication')
    check(received.get('Content-Type') == 'text/html', 'response.type')
    check(raw == case['html'].encode(), 'response.html')
    return status


def assert_case(case, base_url, *, token, sessions):
    case = bind(case)
    if 'html' in case:
        return assert_html(case, base_url, token=token, sessions=sessions)
    if case.get('session'):
        return assert_session(case, base_url, token=token, sessions=sessions)
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, normalize=normalize)
