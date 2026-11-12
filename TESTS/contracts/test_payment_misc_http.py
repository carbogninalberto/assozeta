"""Independently authored payment simulation, partial quotes apply, sign and invoiced approve HTTP expectations; no Django imports.

Responses may carry these placeholders: '$now' accepts a UTC 'Z' timestamp
within ten minutes of the request and records it in NOW (the runner checks it
is the stored value); '$whitelabel' inside a string is the WHITELABEL_NAME
setting (from the session bindings). The simulation's quotes come from an
unordered queryset: 'quotes_to_emit' is compared as a multiset.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/payment_misc_http.json'
NOW = []


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0]
    return {'GET', 'OPTIONS'} if path.startswith('/payment/simulation/') else {'POST', 'OPTIONS'}


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


class _Multiset(list):
    """A list compared without order (items are plain JSON)."""
    def __eq__(self, other):
        key = lambda item: json.dumps(item, sort_keys=True)
        return isinstance(other, list) and sorted(map(key, other)) == sorted(map(key, self))

    __hash__ = None


def expectation(value, whitelabel, key=None):
    if isinstance(value, str):
        if value == '$now':
            return _Now(value)
        return value.replace('$whitelabel', whitelabel)
    if isinstance(value, dict):
        return {k: expectation(item, whitelabel, k) for k, item in value.items()}
    if isinstance(value, list):
        items = [expectation(item, whitelabel) for item in value]
        return _Multiset(items) if key == 'quotes_to_emit' else items
    return value


def assert_case(case, base_url, *, token, sessions):
    NOW.clear()
    response = expectation(case['response'], sessions.get('$whitelabel', ''))
    return family.assert_case({**case, 'response': response}, base_url, token=token, sessions=sessions, allowed=allowed)
