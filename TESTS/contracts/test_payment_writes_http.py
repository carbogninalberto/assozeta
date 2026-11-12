"""Independently authored payment add, update and bulk add HTTP expectations; no Django imports.

Responses may carry these placeholders: '$bind:<name>' accepts a canonical
UUID string and records it in BINDINGS (the runner checks it is the created
row), '$now' accepts a UTC 'Z' timestamp within ten minutes of the request and
records it in NOW (the runner checks it is the stored value),
'$default_category' / '$default_category_name' are the first
'entrate e proventi da attività tipiche' category (from the session
bindings), and {'$any_order': [...]} is a list of strings in any order.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/payment_writes_http.json'
BINDINGS, NOW = {}, []


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'PATCH', 'OPTIONS'} if case['path'].endswith('/update') else {'POST', 'OPTIONS'}


class _Bind(str):
    def __eq__(self, other):
        if not isinstance(other, str):
            return False
        try:
            if str(UUID(other)) != other:
                return False
        except ValueError:
            return False
        return BINDINGS.setdefault(str.__str__(self)[len('$bind:'):], other) == other

    __hash__ = str.__hash__


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


class _AnyOrder(list):
    def __eq__(self, other):
        return isinstance(other, list) and all(isinstance(item, str) for item in other) and sorted(other) == sorted(self)

    __hash__ = None


def expectation(value, bindings):
    """The authored response with its placeholders as matchers."""
    if isinstance(value, str):
        if value.startswith('$bind:'):
            return _Bind(value)
        if value == '$now':
            return _Now(value)
        return bindings.get(value, value)
    if isinstance(value, dict):
        if set(value) == {'$any_order'}:
            return _AnyOrder(value['$any_order'])
        return {key: expectation(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [expectation(item, bindings) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    BINDINGS.clear()
    NOW.clear()
    bindings = {key: value for key, value in sessions.items() if key.startswith('$default')}
    return family.assert_case({**case, 'response': expectation(case['response'], bindings)}, base_url, token=token, sessions=sessions,
                              allowed=allowed)
