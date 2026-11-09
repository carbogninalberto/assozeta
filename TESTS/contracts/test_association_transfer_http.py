"""Independently authored association-transfer HTTP expectations; no Django imports.

export/list rows carry a signed, timestamped download token: the authored
'$download-token' placeholder accepts a token that TOKEN_VERIFIER (installed
by the parent adapter, which owns the signing key) accepts for that row's
document and association, and whose payload segment is exactly the compact
JSON Django signs.
"""
import base64
import json
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/association_transfer_http.json'
TOKEN = '$download-token'
PURPOSE = 'association-export'
# verifier(token) -> payload dict or None; set by TESTS/adapters/django/association_transfer_http.py.
TOKEN_VERIFIER = [None]
ALLOWED = {'active': 'GET', 'list': 'GET', 'status': 'GET', 'delete': 'DELETE', 'start': 'POST', 'validate': 'POST'}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    route = case['path'].split('?')[0].rstrip('/').split('/')
    verb = 'GET' if route[-2:] == ['import', 'status'] else ALLOWED[route[-1]]
    # A ViewSet mapping 'get' also answers HEAD.
    return {verb, 'HEAD', 'OPTIONS'} if verb == 'GET' else {verb, 'OPTIONS'}


def _segment(payload):
    raw = json.dumps(payload, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def normalize(case, expected, actual):
    """Accept a verified download token for each '$download-token' of export/list."""
    if not (isinstance(expected, dict) and isinstance(expected.get('exports'), list)
            and isinstance(actual, dict) and isinstance(actual.get('exports'), list)
            and len(expected['exports']) == len(actual['exports'])):
        return expected, actual
    verify = TOKEN_VERIFIER[0]
    family.require(verify is not None, 'contract.token-verifier')
    rows = []
    for want, got in zip(expected['exports'], actual['exports']):
        if want.get('download_token') == TOKEN and isinstance(got, dict) and isinstance(got.get('download_token'), str):
            payload = {'document_id': want['document_id'], 'sport_association_id': case['association'], 'purpose': PURPOSE}
            token = got['download_token']
            if verify(token) == payload and token.split(':')[0] == _segment(payload):
                got = {**got, 'download_token': TOKEN}
        rows.append(got)
    return expected, {**actual, 'exports': rows}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, normalize=normalize)


