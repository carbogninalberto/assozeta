"""Independently authored customer and supplier invoice list, add and update HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_parties_http.json'
# Function views: @api_view([...]) lists exactly its methods plus OPTIONS.
ALLOWED = {'list': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('?')[0].split('/')[-1]])
