"""Independently authored subscription/<uid>/update and medical-certificate/send-email-reminder HTTP expectations; no Django imports.

The shared http_family checks apply to every case; subscription_update is an
@api_view(['PATCH']) view (Allow: PATCH, OPTIONS) and the reminder an
@api_view(['POST']) view (Allow: POST, OPTIONS).
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/celery_routes_a_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'PATCH', 'OPTIONS'} if case['path'].endswith('/update') else {'POST', 'OPTIONS'}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed)
