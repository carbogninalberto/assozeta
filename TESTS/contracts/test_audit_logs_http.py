"""Independently authored audit-logs list/detail HTTP expectations; no Django imports.

Both routes are @api_view(['GET']): Allow is GET, OPTIONS. Every list is
ordered (timestamp, action or model) and display_changes follows the jsonb key
order, so nothing is compared unordered.

Expected text for a stored datetime before year 1000 keeps a '$libc_year:NNN'
placeholder: strftime('%Y') is the C library's, which pads the year to four
digits where it pads (macOS) and writes the digits alone on glibc, so the
expectation follows the platform the reference Django runs on.
"""
import re
from datetime import date
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/audit_logs_http.json'
YEAR = re.compile(r'\$libc_year:(\d{1,4})')


def load_source(path=SOURCE):
    return _resolve_years(family.load_source(path), _pads_years())


def _pads_years():
    """Whether the C library writes a year below 1000 with four digits (macOS pads, glibc does not)."""
    return len(date(999, 1, 1).strftime('%Y')) == 4


def _resolve_years(value, padded):
    if isinstance(value, str):
        return YEAR.sub(lambda match: match.group(1).rjust(4, '0') if padded else match.group(1), value)
    if isinstance(value, dict):
        return {key: _resolve_years(item, padded) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_years(item, padded) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'GET', 'OPTIONS'})
