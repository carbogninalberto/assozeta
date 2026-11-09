"""Independently authored module responses HTTP expectations; no Django imports.

The shared family checks plus:
- the Allow set of each view (add-attachment allows POST and DELETE);
- '$new' module_response_id accepts the created id (the runner compares it
  with the stored row through RUNTIME['response']);
- a response's attachment pks and attachments_docs are unordered.
"""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/modules_responses_http.json'
ALLOWED = {'info': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'approve': {'POST', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'},
           'add-attachment': {'POST', 'DELETE', 'OPTIONS'}}
RUNTIME = {}


def load_source(path=SOURCE):
    return family.load_source(path, minimum=60)


def allowed(case):
    return ALLOWED[case['path'].split('?')[0].rstrip('/').rsplit('/', 1)[1]]


def normalize(case, expected, actual):
    RUNTIME['response'] = actual

    def ordered(body):
        data = body.get('response_data') if isinstance(body, dict) else None
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get('attachments'), list):
                data['attachments'] = sorted(data['attachments'], key=str)
            if isinstance(data.get('attachments_docs'), list) and all(isinstance(d, dict) for d in data['attachments_docs']):
                data['attachments_docs'] = sorted(data['attachments_docs'], key=lambda d: str(d.get('document_id')))
            body = {**body, 'response_data': data}
        return body

    return ordered(expected), ordered(actual)


def assert_case(case, base_url, *, token, sessions):
    RUNTIME['response'] = None
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, new=('module_response_id',),
                              normalize=normalize)
