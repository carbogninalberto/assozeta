"""Independently authored stripe/* HTTP expectations; no Django imports.

The checks are http_family's. Webhook cases carry a Stripe-Signature template
that is signed at request time (Stripe rejects timestamps older than five
minutes): '{t}' is the current Unix time minus the case's age, '{v1}' the
HMAC-SHA256 of '<t>.<payload>' with the fixture webhook secret and '{other}'
the same with a different secret. 'raw_latin1' sends the raw body's Latin-1
bytes (an invalid UTF-8 payload).
"""
import hashlib
import hmac
from pathlib import Path
import time

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/stripe_routes_http.json'
OTHER_SECRET = 'whsec_other_placeholder'
SOURCE_CACHE = {}


class RawBody(str):
    """A raw_body whose encode() (http_family's UTF-8 step) yields the authored bytes."""
    def __new__(cls, text, raw):
        value = super().__new__(cls, text)
        value.raw = raw
        return value

    def encode(self, *_args, **_kwargs):
        return self.raw


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'GET', 'OPTIONS'} if case['path'].split('?')[0] == '/stripe/info' else {'POST', 'OPTIONS'}


def body_bytes(case):
    return case['raw_body'].encode('latin-1' if case.get('raw_latin1') else 'utf-8')


def signature(case, secret):
    spec = case['signature']
    if isinstance(spec, str):
        spec = {'template': spec, 'age': 0}
    stamp = int(time.time()) - spec['age']
    payload = body_bytes(case)

    def sign(key):
        return hmac.new(key.encode(), str(stamp).encode() + b'.' + payload, hashlib.sha256).hexdigest()
    return spec['template'].format(t=stamp, v1=sign(secret), other=sign(OTHER_SECRET))


def assert_case(case, base_url, *, token, sessions):
    if 'signature' in case or case.get('raw_latin1'):
        if 'source' not in SOURCE_CACHE:
            SOURCE_CACHE['source'] = load_source()
        case = dict(case)
        headers = dict(case['headers'])
        if 'signature' in case:
            headers['Stripe-Signature'] = signature(case, SOURCE_CACHE['source']['stripe']['webhook_secret'])
        case['headers'] = headers
        if case.get('raw_latin1'):
            case['raw_body'] = RawBody(case['raw_body'], body_bytes(case))
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed)
