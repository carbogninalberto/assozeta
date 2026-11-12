"""Independently authored audit-logs list/detail HTTP expectations; no Django imports.

Both routes are @api_view(['GET']): Allow is GET, OPTIONS. Every list is
ordered (timestamp, action or model) and display_changes follows the jsonb key
order, so nothing is compared unordered.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/audit_logs_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'GET', 'OPTIONS'})
