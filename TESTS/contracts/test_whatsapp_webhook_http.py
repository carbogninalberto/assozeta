"""Independently authored communications/whatsapp/webhook HTTP expectations; no Django imports.

The view is a plain Django view, so every answer is checked byte for byte:
status, body, the exact Content-Type ('text/plain' for an echoed challenge,
HttpResponse's 'text/html; charset=utf-8' otherwise), Allow only on a 405,
Content-Language 'en', and no cookie or authentication challenge.
X-Hub-Signature-256 is computed at request time over the exact body bytes
with the case's 'signed_with' key; the key is the fixture's synthetic secret.
"""
import base64
import hashlib
import hmac
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts import http_family as family

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/whatsapp_webhook_http.json'


def load_source(path=SOURCE):
    source = family.load_source(path, minimum=60)
    require(all(case['path'].startswith('/communications/whatsapp/webhook') for case in source['cases']), 'source.path')
    return source


def body_bytes(case):
    if 'raw_body' in case:
        return case['raw_body'].encode('utf-8')
    if 'raw_b64' in case:
        return base64.b64decode(case['raw_b64'], validate=True)
    if 'raw_parts' in case:
        return ''.join(part if isinstance(part, str) else part['repeat'] * part['count'] for part in case['raw_parts']).encode('utf-8')
    return None


def signature(case, body):
    spec = case['signature']
    if spec is None:
        return None
    digest = hmac.new(case['signed_with'].encode(), body or b'', hashlib.sha256).hexdigest()
    return {'valid': 'sha256=' + digest, 'valid-upper': 'sha256=' + digest.upper(), 'valid-bare': digest}.get(spec, spec)


def assert_case(case, base_url, *, token, sessions=None):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'], **case['headers']}
    body = body_bytes(case)
    if body is not None:
        headers['Content-Type'] = case['content_type']
    header = signature(case, body)
    if header is not None:
        headers['X-Hub-Signature-256'] = header
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    expected = b'' if case['response'] == '<empty>' else case['response'].encode('utf-8')
    check(raw == expected, 'response.body')
    check(received.get('Content-Type') == case['content_type_response'], 'response.content-type')
    allow = received.get('Allow')
    if case['allow'] is None:
        check(allow is None, 'response.allow')
    else:
        check(allow is not None and set(allow.replace(' ', '').split(',')) == set(case['allow']), 'response.allow')
    check(received.get('Content-Language') == 'en', 'response.language')
    check(received.get('Set-Cookie') is None and received.get('WWW-Authenticate') is None, 'response.headers')
    return status
