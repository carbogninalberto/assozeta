"""Independently authored DefaultRouter documents HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import router_stock
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_documents_delete_http import bind_folders, bind_path

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_documents_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def bound(case, folders):
    """$folder:<label> in the path, body and response is the seeded folder id."""
    result = {**case, 'path': bind_path(case['path'], folders), 'response': bind_folders(case['response'], folders)}
    if 'body' in case:
        result['body'] = bind_folders(case['body'], folders)
    return result


def assert_case(case, base_url, *, token, sessions, folders):
    # Archive rows are unordered (no ORDER BY).
    return router_stock.assert_case(case, base_url, token=token, sessions=sessions, bind=lambda case: bound(case, folders),
                                    unordered=((('data',), 'sport_association_documents_archive_id'),))
