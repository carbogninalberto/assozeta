"""Independently authored subscription/sign HTTP expectations; no Django imports.

subscription_sign is an @api_view(['POST']) view (Allow: POST, OPTIONS). Storage keys
and public URLs are generated (datetime.now().timestamp()): the runner binds them to
the stored rows and the recorded storage requests.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_sign_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'})
