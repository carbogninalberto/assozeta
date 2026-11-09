"""Independently authored association/impersonation/users and administration/impersonation/users HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/impersonation_users_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    # Users are listed in the authored order (username, then pk).
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'GET', 'OPTIONS'})
