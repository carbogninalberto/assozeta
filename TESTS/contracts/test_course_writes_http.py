"""Independently authored course add, update and delete and course-overview update and delete HTTP expectations; no Django imports.

The shared http_family checks apply to every case; the Allow set depends on the route:
course/add POST, course/<uid>/update PATCH, course/<uid>/delete POST,
course/<uid>/overview/<uid_subscription>/delete DELETE and .../update POST.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_writes_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    parts = case['path'].split('?')[0].strip('/').split('/')
    if 'overview' in parts:
        return {'DELETE' if parts[-1] == 'delete' else 'POST', 'OPTIONS'}
    return {'PATCH' if parts[-1] == 'update' else 'POST', 'OPTIONS'}


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed)

