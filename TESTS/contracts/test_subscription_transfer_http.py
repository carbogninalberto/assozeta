"""Independently authored subscription/<uid>/transfer HTTP expectations; no Django imports.

subscription_transfer is an @api_view(['POST']) view (Allow: POST, OPTIONS).
A created transfer's token, expires_at and (for the e-mail path) recipient are
generated: the runner binds them to the stored rows.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_transfer_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


# The last response body, so the runner can bind generated values to the new rows.
LAST = {}


def _keep(case, expected, actual):
    LAST['body'] = actual
    return expected, actual


def assert_case(case, base_url, *, token, sessions):
    LAST.clear()
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'},
                              new=('token', 'recipient'), now=('expires_at',), normalize=_keep)
