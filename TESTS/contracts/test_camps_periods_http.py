"""Independently authored camps-and-retreats periods and period services HTTP expectations; no Django imports.

Responses nest generated values: '$new' accepts a generated UUID string, '$now'
a UTC 'Z' timestamp (the runner checks the stored row), and {'$unordered': [...]}
a list in any order (many-to-many primary keys without an ordering).
"""
from pathlib import Path
from uuid import UUID

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/camps_periods_http.json'
ALLOWED = {'add': {'POST', 'OPTIONS'}, 'info': {'GET', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


class _Generated(str):
    def __eq__(self, other):
        if not isinstance(other, str):
            return False
        try:
            return str(UUID(other)) == other
        except ValueError:
            return False

    __hash__ = str.__hash__


class _Now(str):
    def __eq__(self, other):
        from datetime import datetime
        if not isinstance(other, str) or not other.endswith('Z'):
            return False
        try:
            datetime.fromisoformat(other[:-1] + '+00:00')
        except ValueError:
            return False
        return True

    __hash__ = str.__hash__


class _Unordered(list):
    def __eq__(self, other):
        return isinstance(other, list) and sorted(map(str, other)) == sorted(map(str, self)) and len(other) == len(self)

    __hash__ = None


def expectation(value):
    """The authored response with its placeholders as matchers."""
    if value == '$new':
        return _Generated(value)
    if value == '$now':
        return _Now(value)
    if isinstance(value, dict):
        if set(value) == {'$unordered'}:
            return _Unordered(value['$unordered'])
        return {key: expectation(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expectation(item) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case({**case, 'response': expectation(case['response'])}, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('/')[-1]])
