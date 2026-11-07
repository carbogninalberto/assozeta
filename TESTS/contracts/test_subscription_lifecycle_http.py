"""Independently authored subscription/<uid>/reject, archive and delete HTTP expectations; no Django imports.

The shared http_family checks apply to every case; all three are
@api_view(['POST']) views (Allow: POST, OPTIONS).
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_lifecycle_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'})
