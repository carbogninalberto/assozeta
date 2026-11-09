"""Independently authored DefaultRouter folders HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import router_stock
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_folders_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    # Folder ids are fixed by the runner; only a server-time created_at varies.
    return router_stock.assert_case(case, base_url, token=token, sessions=sessions, now=('created_at',))
