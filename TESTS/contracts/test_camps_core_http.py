"""Independently authored camps-and-retreats list, add, info, update and delete HTTP expectations; no Django imports.

Responses reuse the camps periods placeholders: '$new' accepts a generated UUID
string and {'$unordered': [...]} a list in any order (many-to-many primary
keys). The camp list has no ordering: its 'data' items are compared by
camps_and_retreats_id.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import test_camps_periods_http as periods
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/camps_core_http.json'
ALLOWED = {'list': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'info': {'GET', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'},
           'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case({**case, 'response': periods.expectation(case['response'])}, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('/')[-1]],
                              unordered=((('data',), 'camps_and_retreats_id'),))
