"""Independently authored course calendar and full events calendar HTTP expectations; no Django imports.

A response may name a row created earlier in the run as '$new:<tag>'; the
runner binds each tag to the generated primary key when it verifies the case
that created the row, and an unbound reference fails the case.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_calendar_http.json'
BOUND = {}
ALLOWED = {'calendar': {'GET', 'OPTIONS'}, 'update': {'POST', 'DELETE', 'OPTIONS'}, 'events': {'GET', 'OPTIONS'},
           'events-update': {'POST', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def resolve(value):
    if isinstance(value, str) and value.startswith('$new:'):
        require(value in BOUND, 'source.unbound-reference')
        return BOUND[value]
    if isinstance(value, dict):
        return {key: resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item) for item in value]
    return value


def route(case):
    path = case['path'].split('?')[0]
    if path == '/calendar/events':
        return 'events'
    if path == '/calendar/events/update':
        return 'events-update'
    return 'update' if path.endswith('/calendar/update') else 'calendar'


def assert_case(case, base_url, *, token, sessions):
    kind = route(case)
    # full_events_calendar walks registries in database order.
    unordered = ((('data', 'events'), 'id'),) if kind == 'events' else ()
    return family.assert_case({**case, 'response': resolve(case['response'])}, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[kind], unordered=unordered)
