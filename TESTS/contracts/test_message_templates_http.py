"""Authored response contract for the message template and composer routes."""
from pathlib import Path
from uuid import UUID

from TESTS.contracts import http_family as family

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures' / 'message_templates_http.json'
require = family.require


def load_source():
    return family.load_source(SOURCE)


def normalize(_case, expected, actual):
    def substitute_id(expected, actual):
        if isinstance(expected, dict):
            return {key: substitute_id(value, actual[key]) for key, value in expected.items()}
        if isinstance(expected, list):
            return [substitute_id(value, match) for value, match in zip(expected, actual, strict=True)]
        if expected == '$uuid':
            require(str(UUID(actual)) == actual, 'response.generated-id')
            return actual
        return expected

    return substitute_id(expected, actual), actual


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, normalize=normalize,
                              allowed=lambda case: (*case['methods'], 'OPTIONS'))
