"""Independently authored subscription/import/upload HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)

# The last response's document_id, bound by the runner to the created Document.
RESPONSE = {}
SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_import_upload_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def _remember(case, expected, actual):
    RESPONSE['document_id'] = actual.get('document_id') if isinstance(actual, dict) else None
    return expected, actual


def assert_case(case, base_url, *, token, sessions):
    RESPONSE.clear()
    # The view's own Response(status=401) carries no Bearer challenge.
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'}, new=('document_id',),
                              challenged=lambda case, status: status == 401 and case['actor'] == 'anonymous',
                              normalize=_remember)
