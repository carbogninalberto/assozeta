"""Independently authored course/locations add, update and delete HTTP expectations; no Django imports."""
from pathlib import Path
import re

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_location_writes_http.json'
# The viewset's own delete() answers DELETE on every mapped route.
ALLOWED = {'add': {'POST', 'DELETE', 'OPTIONS'}, 'update': {'PATCH', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def uploaded(case, expected, actual):
    """A '$document' item of expected documents is a Document the request created: it takes the received item with the
    same filename whose id and token are UUIDs, creation_date a UTC 'Z' timestamp and the path and size null (the
    runner checks the rows, entries and stored objects)."""
    if not (isinstance(expected, dict) and isinstance(actual, dict) and isinstance(expected.get('documents'), list)):
        return expected, actual
    received = [item for item in actual.get('documents') or [] if isinstance(item, dict)]
    documents = []
    for item in expected['documents']:
        if item.get('document_id') != '$document':
            documents.append(item)
            continue
        known = {entry.get('document_id') for entry in expected['documents']}
        match = [entry for entry in received if entry.get('filename') == item['filename'] and entry.get('document_id') not in known
                 and entry not in documents]
        require(len(match) >= 1 and UUID.match(str(match[0].get('document_id'))) and UUID.match(str(match[0].get('token')))
                and str(match[0].get('creation_date', '')).endswith('Z') and match[0].get('filepath') is None
                and match[0].get('file_size_bytes') is None, 'response.uploaded-document')
        documents.append(match[0])
    return {**expected, 'documents': documents}, actual


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path'].split('/')[-1]],
                              new=('course_location_id',), normalize=uploaded, unordered=((('documents',), 'document_id'),))
