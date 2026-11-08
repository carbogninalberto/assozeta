"""Independently authored profile-misc HTTP expectations; no Django imports.

profile/associates/* iterate querysets without an ORDER BY: their two lists
are compared as unordered sets keyed by associate id.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/profile_misc_http.json'
UNORDERED = ((('associates_subscribed',), 'id'), (('associates_unsubscribed',), 'id'))


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {'PATCH', 'OPTIONS'} if case['path'].startswith('/onboarding/') else {'GET', 'OPTIONS'}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, unordered=UNORDERED)
