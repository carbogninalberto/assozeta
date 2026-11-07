"""Independently authored camps-and-retreats subscriptions HTTP expectations; no Django imports.

Responses reuse the camps periods placeholders ('$new', '$now', {'$unordered':
[...]}) and add two: '$bind:<name>' accepts a UUID string and records it in
BINDINGS (the same name must always see the same value; the runner checks the
recorded keys against the rows the case created), and {'$bag': [...], '$by':
field} is a list of objects in any order, compared item by item after sorting
both sides by that (authored, concrete) field.
"""
from pathlib import Path
from uuid import UUID

from TESTS.contracts import http_family as family
from TESTS.contracts import test_camps_periods_http as periods
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/camps_subscriptions_http.json'
ALLOWED = {'list': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}
BINDINGS = {}


def load_source(path=SOURCE):
    return family.load_source(path)


class _Bind(str):
    def __eq__(self, other):
        if not isinstance(other, str):
            return False
        try:
            if str(UUID(other)) != other:
                return False
        except ValueError:
            return False
        name = str.__str__(self)[len('$bind:'):]
        return BINDINGS.setdefault(name, other) == other

    __hash__ = str.__hash__


class _Bag(list):
    def __init__(self, items, by):
        super().__init__(items)
        self.by = by

    def __eq__(self, other):
        if not isinstance(other, list) or len(other) != len(self) or not all(isinstance(item, dict) for item in other):
            return False
        key = lambda item: str(item.get(self.by))
        return all(actual == expected for actual, expected in zip(sorted(other, key=key), sorted(self, key=key)))

    __hash__ = None


def expectation(value):
    """The authored response with its placeholders as matchers."""
    if isinstance(value, str) and value.startswith('$bind:'):
        return _Bind(value)
    if isinstance(value, dict) and set(value) == {'$bag', '$by'}:
        return _Bag([expectation(item) for item in value['$bag']], value['$by'])
    if isinstance(value, dict) and set(value) != {'$unordered'}:
        return {key: expectation(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expectation(item) for item in value]
    return periods.expectation(value)


def assert_case(case, base_url, *, token, sessions):
    BINDINGS.clear()
    return family.assert_case({**case, 'response': expectation(case['response'])}, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('/')[-1]])

