"""Independently authored DefaultRouter personas HTTP expectations; no Django imports.

The personas-writes conventions apply (each case names its Allow set, ages
and minority are computed for the request day, generated keys and creation
times are bound from the response, tutor and family lists are unordered).
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_personas_writes_http import assert_case  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_personas_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)
