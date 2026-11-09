"""Independently authored DefaultRouter API root HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import router_stock
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_root_http.json'


def load_source(path=SOURCE):
    return family.load_source(path, minimum=5)


def assert_case(case, base_url, *, token, sessions):
    return router_stock.assert_case(case, base_url, token=token, sessions=sessions)
