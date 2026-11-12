"""Independently authored payment generate-invoice and invoiced approve dispatch HTTP expectations; no Django imports.

Response placeholders: '$now' accepts a UTC 'Z' timestamp within ten minutes
(recorded in NOW; the runner checks it is the stored value); '$invoice' accepts
a uuid4 string (recorded in INVOICE; the runner checks it is the created
invoice); '$year:-MM-DDTHH:MM:SSZ' / '$lastyear:...' are dates in the current /
previous UTC year; '$default_category' and '$default_category_name' are the
default payment category (from the session bindings).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_generate_http.json'
NOW, INVOICE = [], []
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'POST', 'OPTIONS'}


def dated(text):
    """'$year:' / '$lastyear:' dates of the run."""
    if isinstance(text, str):
        year = datetime.now(timezone.utc).year
        if text.startswith('$year:'):
            return f'{year}{text[6:]}'
        if text.startswith('$lastyear:'):
            return f'{year - 1}{text[10:]}'
    return text


class _Now(str):
    def __eq__(self, other):
        if not isinstance(other, str) or not other.endswith('Z'):
            return False
        try:
            moment = datetime.fromisoformat(other.replace('Z', '+00:00'))
        except ValueError:
            return False
        if abs(datetime.now(timezone.utc) - moment) >= timedelta(minutes=10):
            return False
        NOW.append(other)
        return True

    __hash__ = str.__hash__


class _Invoice(str):
    def __eq__(self, other):
        if not isinstance(other, str) or not UUID4.match(other):
            return False
        INVOICE.append(other)
        return True

    __hash__ = str.__hash__


def expectation(value, sessions):
    if isinstance(value, str):
        if value == '$now':
            return _Now(value)
        if value == '$invoice':
            return _Invoice(value)
        if value in ('$default_category', '$default_category_name'):
            return sessions[value]
        return dated(value)
    if isinstance(value, dict):
        return {k: expectation(item, sessions) for k, item in value.items()}
    if isinstance(value, list):
        return [expectation(item, sessions) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    NOW.clear()
    INVOICE.clear()
    response = expectation(case['response'], sessions)
    return family.assert_case({**case, 'response': response}, base_url, token=token, sessions=sessions, allowed=allowed)
