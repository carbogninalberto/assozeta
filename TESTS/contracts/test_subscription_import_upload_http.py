"""Independently authored subscription/import/upload HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)

# The last response's document_id, bound by the runner to the created Document.
RESPONSE = {}
# label -> document_id of the uploads whose object stays for later imports (set by the runner).
DOCUMENTS = {}
SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_import_upload_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def _remember(case, expected, actual):
    RESPONSE['document_id'] = actual.get('document_id') if isinstance(actual, dict) else None
    return expected, actual


def _placed(value):
    """value with each '$document:<label>' replaced by the Document a kept upload created."""
    if isinstance(value, str):
        for label, key in DOCUMENTS.items():
            value = value.replace(f'$document:{label}', key)
        require('$document:' not in value, 'fixture.document-not-kept')
        return value
    if isinstance(value, dict):
        return {key: _placed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_placed(item) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    RESPONSE.clear()
    case = {**case, **{key: _placed(case[key]) for key in ('body', 'raw_body') if key in case}}
    # The view's own Response(status=401) carries no Bearer challenge.
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: {'POST', 'OPTIONS'}, new=('document_id',),
                              challenged=lambda case, status: status == 401 and case['actor'] == 'anonymous',
                              normalize=_remember)
