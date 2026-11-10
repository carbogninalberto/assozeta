"""Independently authored oauth2/signup, oauth2/partial-signup, oauth2/reset and oauth2/delete-account HTTP expectations; no Django imports.

The shared http_family checks apply to every case; the four views are
@api_view(['POST']) function views (Allow: POST, OPTIONS). Plus:
- a 401 the view returns itself ({'error': ...} or {'msg': ...}) carries no Bearer challenge;
  only DRF's NotAuthenticated does;
- '$signup' accepts a login response with Bearer token strings; the runner
  verifies it (RUNTIME['response'] holds the decoded body) against the
  stored user, SimpleJWT and Django's build_login_response;
- body values {'$social': ...} (a provider token) and {'$totp': ...} (a current
  one-time code) are minted for each request by RUNTIME['mint'], which the
  runner sets for the run.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/oauth_accounts_http.json'
RUNTIME = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'POST', 'OPTIONS'}


def challenged(case, status):
    return status == 401 and isinstance(case['response'], dict) and 'detail' in case['response']


def normalize(case, expected, actual):
    RUNTIME['response'] = actual
    if expected == '$signup':
        family.require(isinstance(actual, dict) and all(isinstance(actual.get(key), str) and actual[key]
                                                         for key in ('access_token', 'refresh_token'))
                       and actual.get('token_type') == 'Bearer', 'response.signup-tokens')
        expected = actual
    return expected, actual


def minted(case):
    body = case.get('body')
    if not isinstance(body, dict):
        return case
    marked = {key for key, value in body.items() if isinstance(value, dict) and set(value) & {'$social', '$totp'}}
    if not marked:
        return case
    family.require(callable(RUNTIME.get('mint')), 'fixture.mint')
    return {**case, 'body': {key: RUNTIME['mint'](value) if key in marked else value for key, value in body.items()}}


def assert_case(case, base_url, *, token, sessions):
    RUNTIME['response'] = None
    case = minted(case)
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, normalize=normalize,
                              challenged=challenged)
