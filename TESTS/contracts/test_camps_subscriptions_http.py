"""Independently authored camps-and-retreats subscriptions HTTP expectations; no Django imports.

Responses reuse the camps periods placeholders ('$new', '$now', {'$unordered':
[...]}) and add two: '$bind:<name>' accepts a UUID string and records it in
BINDINGS (the same name must always see the same value; the runner checks the
recorded keys against the rows the case created), and {'$bag': [...], '$by':
field} is a list of objects in any order, compared item by item after sorting
both sides by that (authored, concrete) field.

'$default_category' / '$default_category_name' are the fixture's default payment
category, which only the database resolves: the adapter passes the seeded values
as bindings. The refused generated payment's DETAIL names a generated id and
creation date; normalize replaces both by placeholders on either side.
"""
import re
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


def bind(value, bindings):
    """Replace the placeholders the fixture's own rows fill in (the seeded default payment category)."""
    if isinstance(value, str):
        for name in sorted(bindings, key=len, reverse=True):
            value = value.replace(name, bindings[name])
        return value
    if isinstance(value, dict):
        return {key: bind(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, bindings) for item in value]
    return value


# The payment generate_payment_for_periods inserts has a generated id and
# creation date; the check constraint's DETAIL names both.
GENERATED_ROW = re.compile(
    r'(Failing row contains \()'
    r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
    r'(, default, 0, -[0-9]+\.[0-9]{2}, )'
    r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00')


def generated_placeholders(value):
    if isinstance(value, str):
        return GENERATED_ROW.sub(r'\1$payment\2$created', value)
    if isinstance(value, dict):
        return {key: generated_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [generated_placeholders(item) for item in value]
    return value


def normalize(case, expected, actual):
    """The refused generated payment: its id and creation date differ between the two backends."""
    if case['name'] == 'camps-subscriptions-add-negative-generated-payment':
        return generated_placeholders(expected), generated_placeholders(actual)
    return expected, actual


def assert_case(case, base_url, *, token, sessions, bindings=None):
    BINDINGS.clear()
    response = expectation(case['response'])
    if bindings:
        response = bind(response, bindings)
    return family.assert_case({**case, 'response': response}, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('/')[-1]], normalize=normalize)

