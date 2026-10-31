"""Independently authored course/locations add, update and delete HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_location_writes_http.json'
# The viewset's own delete() answers DELETE on every mapped route.
ALLOWED = {'add': {'POST', 'DELETE', 'OPTIONS'}, 'update': {'PATCH', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path'].split('/')[-1]],
                              new=('course_location_id',))
