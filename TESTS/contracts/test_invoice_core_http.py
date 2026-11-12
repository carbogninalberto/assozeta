"""Independently authored invoice list, archived list, bulk archive, bulk delete and delete HTTP expectations; no Django imports.

The shared http_family checks apply to every case. Each route is an
@api_view function view: Allow is its method plus OPTIONS.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_core_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?', 1)[0]
    if path in ('/invoice/list', '/invoice/list/archived'):
        return {'GET', 'OPTIONS'}
    if path == '/invoice-bulk/delete':
        return {'DELETE', 'OPTIONS'}
    return {'POST', 'OPTIONS'}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed)
