"""Independently authored DefaultRouter course-locations HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import router_stock
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_course_locations_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def _documents(case, expected, actual):
    """Locations and their documents are unordered."""
    def norm(value):
        if isinstance(value, dict) and isinstance(value.get('documents'), list):
            return {**value, 'documents': sorted(value['documents'], key=lambda d: str(d.get('document_id')))}
        if isinstance(value, dict) and isinstance(value.get('data'), list):
            return {**value, 'data': [norm(row) for row in value['data']]}
        return value
    return norm(expected), norm(actual)


def assert_case(case, base_url, *, token, sessions):
    return router_stock.assert_case(case, base_url, token=token, sessions=sessions, new=('course_location_id',),
                                    unordered=((('data',), 'course_location_id'),), normalize=_documents)
