"""Independently authored collaborators/<str:uid>/delete HTTP expectations; no Django imports.

collaborators_delete is an @api_view(['DELETE']) view (Allow: DELETE, OPTIONS).
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/collaborator_delete_go_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'DELETE', 'OPTIONS'})
