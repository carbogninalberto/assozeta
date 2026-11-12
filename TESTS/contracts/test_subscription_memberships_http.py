"""Independently authored subscription/memberships list, add, update and delete HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_memberships_http.json'
# The viewset's own delete() answers DELETE on every mapped route; the GET route also allows HEAD.
ALLOWED = {'list': {'GET', 'HEAD', 'DELETE', 'OPTIONS'}, 'add': {'POST', 'DELETE', 'OPTIONS'},
           'update': {'PATCH', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


# The documents a request created, as the response listed them (the adapter checks them against the rows).
LAST = {}


def _documents(case, expected, actual):
    """'$new_document' marks a document the request created: its id, token and creation date are generated (checked by
    shape here and against the stored row by the adapter). Attached documents compare by filename."""
    import re
    import uuid
    LAST.clear()
    listed = expected.get('attached_membership_documents') if isinstance(expected, dict) else None
    if not listed or not any(item.get('document_id') == '$new_document' for item in listed):
        return expected, actual
    documents = actual.get('attached_membership_documents') if isinstance(actual, dict) else None
    require(isinstance(documents, list) and len(documents) == len(listed), 'response.documents')
    new = []
    names = {item['filename'] for item in listed if item.get('document_id') == '$new_document'}
    for item in documents:
        if item.get('filename') in names and item.get('document_id') not in {d.get('document_id') for d in listed}:
            for key in ('document_id', 'token'):
                require(str(uuid.UUID(str(item.get(key)))) == item.get(key), 'response.document-' + key)
            require(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z', str(item.get('creation_date'))) is not None,
                    'response.document-creation-date')
            new.append(dict(item))
            item.update(document_id='$new_document', token='$new_document', creation_date='$new_document')
    LAST['documents'] = new
    by_name = lambda items: sorted(items, key=lambda item: (item.get('filename') or '', item.get('document_id') or ''))
    return {**expected, 'attached_membership_documents': by_name(listed)}, {**actual, 'attached_membership_documents': by_name(documents)}


def assert_case(case, base_url, *, token, sessions):
    # Memberships are unordered; each carries at most one document.
    return family.assert_case(case, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('?')[0].split('/')[-1]],
                              new=('subscription_membership_id',), now=('creation_date',),
                              unordered=((('data',), 'subscription_membership_id'),), normalize=_documents)
