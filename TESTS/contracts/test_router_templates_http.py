"""Independently authored DefaultRouter templates HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import router_stock
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_templates_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def _rows(case, expected, actual):
    """The list has no ORDER BY."""
    if isinstance(expected, list) and isinstance(actual, list):
        key = lambda row: str(row.get('sport_association_module_templates_id')) if isinstance(row, dict) else ''
        return sorted(expected, key=key), sorted(actual, key=key)
    return expected, actual


def assert_case(case, base_url, *, token, sessions):
    return router_stock.assert_case(case, base_url, token=token, sessions=sessions, new=('sport_association_module_templates_id',),
                                    normalize=_rows)
