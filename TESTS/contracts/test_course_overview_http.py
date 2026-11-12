"""Independently authored course/<uid>/overview HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_overview_http.json'
# tags and locations are unordered prefetches.
UNORDERED = ((('data', 'course', 'tags'), 'tag_id'), (('data', 'course', 'locations'), 'course_location_id'))


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'GET', 'OPTIONS'}, unordered=UNORDERED)
