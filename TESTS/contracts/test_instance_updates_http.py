"""Independently authored production-mode instance updates and restarts expectations; no Django imports.

The shared family checks (status, error page, Allow, Content-Language, no
cookie, Bearer challenge, exact JSON). These views set no Cache-Control.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instance_updates_http.json'
ALLOWED = {'/instance/admin/updates': {'GET', 'POST', 'HEAD', 'OPTIONS'}, '/instance/admin/restarts': {'POST', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path']])
