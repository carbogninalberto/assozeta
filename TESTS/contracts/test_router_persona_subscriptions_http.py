"""Independently authored DefaultRouter personas-subscriptions HTTP expectations; no Django imports.

The personas-writes conventions apply (each case names its Allow set, ages
are computed for the request day, nested tutor lists are unordered).
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_personas_writes_http import assert_case  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/router_persona_subscriptions_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)
