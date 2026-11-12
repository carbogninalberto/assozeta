"""Independently authored communications/settings/smtp info, update and verify HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/communications_smtp_http.json'
ALLOWED = {'info': {'GET', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'verify': {'GET', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path'].split('/')[-1]])
