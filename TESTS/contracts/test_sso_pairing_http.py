"""Independently authored Bakney pairing/SSO HTTP expectations; no Django imports.

Every response of these SecureViews is checked for the exact status, the
view's Allow header, Content-Language, the four SecureView headers
(Cache-Control no-store, Pragma no-cache, Referrer-Policy no-referrer,
X-Frame-Options DENY), the Bearer challenge only on DRF's 401, Retry-After on
a 429, and the exact Set-Cookie list. JSON bodies are compared exactly after
accepting generated values; redirects must carry an empty text/html body and
the authored Location.

Request markers are resolved at request time from RUNTIME (filled by the
runner): '$expires+N' (Unix time plus N; '+N.0' a float, '+Ns' a string),
'$proof' (the commit proof over the sent binding with the per-run secret;
'$proof-other' another key, '$proof-upper' upper-cased, '$proof-challenge'
the challenge phase) and '$token:<actor>' in cookies.

Response markers: '$new' (a UUID), '$now' (a recent UTC 'Z' timestamp),
'$secret-new' (a 43-character URL-safe secret), '$proof:challenge' /
'$proof:ack' (the HMAC answer over the sent binding), '$login:<actor>'
(verified by the runner). In Location and Set-Cookie: '$state',
'$challenge', '$fresh' (43-character URL-safe values), '$access' (a JWT) and
'$expires(N)' (an HTTP date N seconds from now, within five seconds).
RUNTIME['last'] keeps what the runner's effect check needs.
"""
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import time

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import ResponseFailure, require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/sso_pairing_http.json'
RUNTIME = {}
ALLOW = {'PairingAdminView': 'GET, POST, HEAD, OPTIONS', 'PairingChallengeView': 'POST, OPTIONS', 'PairingCommitView': 'POST, OPTIONS',
         'LoginStartView': 'GET, HEAD, OPTIONS', 'LoginCallbackView': 'GET, HEAD, OPTIONS', 'LoginSessionView': 'GET, POST, HEAD, OPTIONS'}
OPAQUE43 = r'([A-Za-z0-9_-]{43})'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
FIELDS = ('pairing_id', 'association_id', 'instance_id', 'origin', 'callback_uri', 'generation', 'nonce', 'expires_at')


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def signature(secret, phase, payload, version='bakney-pairing-v1'):
    fields = [version, phase] + [str(payload[key]) for key in FIELDS]
    return hmac.new(base64.urlsafe_b64decode(secret + '='), '\n'.join(fields).encode('ascii'), hashlib.sha256).hexdigest()


def _expires(text, now):
    offset = text[len('$expires+'):]
    if offset.endswith('s'):
        return str(now + int(offset[:-1])) + 's'
    if '.' in offset:
        return now + float(offset)
    return now + int(offset)


def materialize_body(body, source):
    """The JSON body actually sent and the binding payload used for proofs."""
    if not isinstance(body, dict):
        return body, None
    now = int(time.time())
    sent = {key: _expires(value, now) if isinstance(value, str) and value.startswith('$expires+') else value
            for key, value in body.items()}
    payload = {key: value for key, value in sent.items() if key != 'proof'}
    payload['instance_id'] = source['pairing_ids']['instance']
    if 'proof' in sent and isinstance(sent['proof'], str) and sent['proof'].startswith('$proof'):
        marker = sent['proof']
        if all(key in payload for key in FIELDS):
            secret = RUNTIME['secret'] if marker != '$proof-other' else base64.urlsafe_b64encode(b'\x07' * 32).decode().rstrip('=')
            proof = signature(secret, 'challenge' if marker == '$proof-challenge' else 'commit', payload)
            sent['proof'] = proof.upper() if marker == '$proof-upper' else proof
    return sent, payload


def cookie_header(case):
    parts = []
    for name, value in case['cookies']:
        if value.startswith('$token:'):
            value = RUNTIME['tokens'][value[len('$token:'):]]
        parts.append(f'{name}={value}')
    return '; '.join(parts)


def _template(text):
    """Regex for a Location/Set-Cookie template and the marker names it captures."""
    pattern, names = '', []
    for piece in re.split(r'(\$fresh|\$state|\$challenge|\$access|\$expires\(\d+\))', text):
        if piece in ('$fresh', '$state', '$challenge'):
            pattern += OPAQUE43
            names.append(piece[1:])
        elif piece == '$access':
            pattern += r'([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)'
            names.append('access')
        elif piece.startswith('$expires('):
            pattern += r'([A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT)'
            names.append('expires:' + piece[len('$expires('):-1])
        else:
            pattern += re.escape(piece)
    return re.compile(pattern + r'\Z'), names


def _match(template, actual, captured, code, status):
    regex, names = _template(template)
    found = regex.match(actual or '')
    if not found:
        raise ResponseFailure(code, status)
    for name, value in zip(names, found.groups()):
        if name.startswith('expires:'):
            moment = parsedate_to_datetime(value).timestamp()
            if abs(moment - (time.time() + int(name.split(':')[1]))) > 5:
                raise ResponseFailure(code + '-expires', status)
            continue
        if name in captured and captured[name] != value:
            raise ResponseFailure(code + '-consistent', status)
        captured[name] = value


def _recent_timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        return False
    try:
        moment = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        return False
    return abs((datetime.now(timezone.utc) - moment).total_seconds()) < 120


def _resolve_expected(expected, actual, payload, captured, status):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return expected
    resolved = {}
    for key, value in expected.items():
        got = actual.get(key)
        if value == '$new':
            if not (isinstance(got, str) and UUID.match(got)):
                raise ResponseFailure('response.new-' + key, status)
            captured['new:' + key] = got
            value = got
        elif value == '$now':
            if not _recent_timestamp(got):
                raise ResponseFailure('response.now-' + key, status)
            captured['now:' + key] = got
            value = got
        elif value == '$secret-new':
            if not (isinstance(got, str) and re.fullmatch(OPAQUE43, got)):
                raise ResponseFailure('response.secret', status)
            captured['secret'] = got
            value = got
        elif isinstance(value, str) and value.startswith('$proof:'):
            value = signature(RUNTIME['secret'], value[len('$proof:'):], payload)
        resolved[key] = value
    return resolved


def assert_case(case, base_url, *, token, sessions):
    source = RUNTIME['source']
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'], **case['headers']}
    cookies = cookie_header(case)
    if cookies:
        headers['Cookie'] = cookies
    payload = sent = None
    body = None
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    elif 'body' in case:
        sent, payload = materialize_body(case['body'], source)
        body = json.dumps(sent).encode()
        headers['Content-Type'] = 'application/json'
    RUNTIME['last'] = last = {'sent': sent, 'payload': payload, 'captured': {}, 'response': None, 'started': time.time()}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    captured = last['captured']

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    check(received.get('Allow') == ALLOW[case['view']], 'response.allow')
    check(received.get('Content-Language') == 'en', 'response.language')
    for name, value in source['secure_headers'].items():
        check(received.get_all(name) == [value], 'response.secure-' + name.lower())
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    check(received.get('Retry-After') == ('50' if status == 429 else None), 'response.retry-after')
    set_cookies = received.get_all('Set-Cookie') or []
    check(len(set_cookies) == len(case['set_cookies']), 'response.set-cookie-count')
    for template, actual in zip(case['set_cookies'], set_cookies):
        _match(template, actual, captured, 'response.set-cookie', status)
    if case['response'] == '<redirect>':
        check(raw == b'', 'response.redirect-body')
        check(received.get('Content-Type') == 'text/html; charset=utf-8', 'response.redirect-type')
        _match(case['location'], received.get('Location'), captured, 'response.location', status)
        return status
    check(received.get('Location') is None, 'response.no-location')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    last['response'] = actual
    expected = case['response']
    if isinstance(expected, str) and expected.startswith('$login:'):
        check(isinstance(actual, dict) and isinstance(actual.get('access_token'), str) and actual.get('token_type') == 'Bearer',
              'response.login-tokens')
        check(captured.get('access') == actual['access_token'], 'response.login-cookie')
        return status
    expected = _resolve_expected(expected, actual, payload, captured, status)
    check(actual == expected, 'response.body')
    return status
