"""Independently authored API v2 handoff HTTP expectations; no Django imports.

Each case is checked by its source family's contract (personas-reads,
course-list, workflow-update: exact status, headers and body as authored there),
then by the handoff marker: `X-Assozeta-Handoff: django` on a Go transport for a
case Go refuses and hands off, and no marker otherwise (Django itself, and inputs
Go serves). The harness checks the attribution: a handed-off case is one request
observed by Django with the case's correlation id, method, path and status.
"""
from pathlib import Path
from unittest.mock import patch

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts import test_course_list_http as course_list
from TESTS.contracts import test_personas_reads_http as personas_reads
from TESTS.contracts import test_workflow_update_http as workflow_update
from TESTS.contracts.http_family import require, ResponseFailure  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/django_handoff_http.json'
CONTRACTS = {'personas-reads': personas_reads, 'course-list': course_list, 'workflow-update': workflow_update}
HEADER = 'X-Assozeta-Handoff'


def load_source(path=SOURCE):
    source = family.load_source(path)
    require(set(source['sources']) == set(CONTRACTS) and all(case['family'] in CONTRACTS for case in source['cases']), 'source.families')
    require(any(case['handoff'] for case in source['cases']) and not all(case['handoff'] for case in source['cases']), 'source.handoff-mix')
    return source


def handed_off(case, backend):
    return backend == 'go' and case['handoff']


def assert_case(case, base_url, *, token, sessions, backend):
    require(backend in ('django', 'go'), 'handoff.backend')
    received = []
    original = Client.request

    def request(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        received.append(result[2] if kwargs.get('include_headers') and len(result) == 3 else None)
        return result

    with patch.object(Client, 'request', request):
        status = CONTRACTS[case['family']].assert_case(case, base_url, token=token, sessions=sessions)
    if len(received) != 1 or received[0] is None:
        raise ResponseFailure('handoff.single-request', status)
    marker = received[0].get_all(HEADER) or []
    if marker != (['django'] if handed_off(case, backend) else []):
        raise ResponseFailure('response.handoff-header', status)
    return status
