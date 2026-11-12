"""Independently authored subscription/add HTTP expectations; no Django imports.

The shared http_family checks apply to every case; subscription_add is an
@api_view(['POST']) view (Allow: POST, OPTIONS). payment_id of a created
payment is generated: the runner binds it to the new row.
"""
from datetime import datetime, timezone
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_create_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


# The last response body, so the runner can bind a generated payment_id to the new row.
LAST = {}


def _keep(case, expected, actual):
    LAST.clear()
    LAST['body'] = actual
    # The family wizard's serialized payments: the runner compares them with the stored rows.
    if isinstance(expected, dict) and expected.get('payments') == '$payments' and isinstance(actual, dict) \
            and isinstance(actual.get('payments'), list):
        expected = {**expected, 'payments': actual['payments']}
    return expected, actual


def _today(value):
    """'$today' in a request body: the servers' naive date (TZ=UTC), YYYY-MM-DD."""
    if value == '$today':
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if isinstance(value, dict):
        return {key: _today(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_today(item) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    LAST.clear()
    if case.get('body') is not None:
        case = {**case, 'body': _today(case['body'])}
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'},
                              new=('payment_id',), normalize=_keep)
