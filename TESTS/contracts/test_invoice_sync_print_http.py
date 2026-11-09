"""Independently authored invoice/<uid>/update and invoice-customers/add HTTP expectations; no Django imports.

Both are function views: Allow is the view's method plus OPTIONS. Responses are
exact JSON (or the stable server error page); the effects (rows, auditlog
entries and the outbound print requests) are checked by the runner.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_sync_print_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'POST', 'OPTIONS'} if case['path'].startswith('/invoice-customers/') else {'PATCH', 'OPTIONS'}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed)
