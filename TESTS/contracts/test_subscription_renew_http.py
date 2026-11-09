"""Independently authored subscription/renew and associates-draft approve HTTP expectations; no Django imports.

Both views are @api_view(['POST']) (Allow: POST, OPTIONS). payment_id of a
created payment is generated: the runner binds it to the new row. The
approve view's own Response(status=401) carries no Bearer challenge.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_renew_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


# The last response body, so the runner can bind a generated payment_id to the new row.
LAST = {}


def _keep(case, expected, actual):
    LAST.clear()
    LAST['body'] = actual
    return expected, actual


def _challenged(case, status):
    return status == 401 and case['response'] != '<empty>'


def assert_case(case, base_url, *, token, sessions):
    LAST.clear()
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'},
                              new=('payment_id',), normalize=_keep, challenged=_challenged)
