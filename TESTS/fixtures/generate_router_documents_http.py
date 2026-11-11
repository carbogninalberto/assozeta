"""Author the DefaultRouter documents HTTP sequence (router-stock.md).

`router.register(r'documents', DocumentArchiveViewSet)` (parser_classes
JSONParser only: another media type is 415 when request.data is read). The
list route binds GET to list() and POST to the stock CreateModelMixin.create:
DocumentArchiveSerializer validates the client's input (sport_association
required; it and folder tenant-scoped; date DD/MM/YYYY; document read-only),
then perform_create's SportAssociation.objects.get(user=request.user) and the
insert, whose NOT NULL document column fails: a 500 with nothing written.
The detail route binds GET to the stock retrieve(): the serializer's
document field (many=True on a foreign key) iterates a Document, a 500 for
every row found (404 otherwise); PUT to update() (partial: it saves, then the
same serialization fails, so the change stays and the answer is a 500);
PATCH to partial_update() (500); DELETE to the stock destroy() (the archive
row only). `documents/bulk_delete/` is the @action bulk_delete (DELETE only,
named 'Bulk delete'). Format twins: list(), update() and bulk_delete() reject
format= (500). Builds on the documents list seed (``$folder:<label>`` binds
folder ids). Run with ``python3 -I`` after the documents list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_documents_http.json')
LIST = json.loads(Path(__file__).with_name('documents_list_http.json').read_text())
IDS, FOLDERS, ARCHIVES = LIST['ids'], LIST['folders'], LIST['archives']
ARCHIVE = {item['label']: item for item in ARCHIVES}
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, effect=None):
    case = {'name': f'router-documents-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def row(item):
    return {'sport_association_documents_archive_id': item['id'], 'sport_association': item['association'],
            'document': {'document_id': item['document'], 'creation_date': item['created'], 'filepath': item['filepath'],
                         'filename': item['filename'], 'file_size_bytes': item['size'], 'token': item['token']},
            'date': item['date'], 'folder': f'$folder:{item["folder"]}' if item['folder'] else None}


def page(labels):
    rows = [row(item) for item in ARCHIVES if item['label'] in labels]
    return {'data': rows, 'pagination': {'page': 0, 'total_pages': -(-len(rows) // 40), 'total_items': len(rows), 'has_next': False}}


def archive_changes(item):
    return {'sport_association_documents_archive_id': [item['id'], 'None'], 'sport_association': [item['association'], 'None'],
            'document': [item['document'], 'None'], 'date': [item['date'], 'None'],
            'folder': [f'$folder:{item["folder"]}' if item['folder'] else 'None', 'None']}


def document_changes(item):
    values = (('document_id', item['document']), ('creation_date', item['created'].rstrip('Z').replace('T', ' ')),
              ('filepath', item['filepath']), ('filename', item['filename']),
              ('file_size_bytes', None if item['size'] is None else str(item['size'])), ('token', item['token']))
    # auditlog renders the reverse FK with an explicit related_name as its empty manager.
    return {key: [value, 'None'] for key, value in values if value is not None} | {'customer_invoice_pdf': ['application.CustomerInvoice.None', 'None']}


def deleted(labels, documents=False):
    items = [ARCHIVE[label] for label in labels]
    return {'kind': 'delete', 'archives': [item['id'] for item in items], 'documents': [item['document'] for item in items] if documents else [],
            'archive_changes': {item['id']: archive_changes(item) for item in items},
            'document_changes': {item['document']: document_changes(item) for item in items} if documents else {}}


LIST_PATH = '/documents/'
BULK = '/documents/bulk_delete/'
detail = lambda label, suffix='/': f'/documents/{ARCHIVE[label]["id"] if label in ARCHIVE else label}{suffix}'
NOT_FOUND = {'detail': 'No SportAssociationDocumentsArchive matches the given query.'}
BARE = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
REQUIRED = 'This field is required.'
DATE = 'Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
method = lambda name: {'detail': f'Method "{name}" not allowed.'}
info = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    'label': label, **extra}
SERIALIZER = {
    'sport_association_documents_archive_id': info('string', 'Sport association documents archive id', read_only=True),
    'sport_association': info('field', 'Sport association', True),
    'document': info('field', 'Document', read_only=True, child={'type': 'nested object', 'required': False, 'read_only': True, 'children': {
        'document_id': info('string', 'Document id'), 'creation_date': info('datetime', 'Creation date'),
        'filepath': info('string', 'Filepath', max_length=1000), 'filename': info('string', 'Filename', True, max_length=255),
        'file_size_bytes': info('integer', 'File size bytes', min_value=0, max_value=9223372036854775807), 'token': info('string', 'Token')}}),
    'date': info('date', 'Date'), 'folder': info('field', 'Folder')}
OPTIONS = lambda name, actions=None: {'name': name, 'description': '', 'renders': ['application/json'], 'parses': ['application/json'],
                                      **({'actions': actions} if actions else {})}

# The list route.
add('list-anonymous', 'anonymous', LIST_PATH, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', LIST_PATH, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', LIST_PATH, 403, unmapped('documents'))
add('list-owner-root', 'owner', LIST_PATH, 200, page({'root1', 'root2'}))
add('list-owner-folder', 'full_collaborator', LIST_PATH + '?folder=$folder:a', 200, page({'in_a'}))
add('list-foreign-folder-denied', 'owner', LIST_PATH + '?folder=$folder:f', 403, {'detail': 'Cannot access folder from different sport association.'})
add('list-athlete-server-error', 'athlete', LIST_PATH, 500, SERVER)
add('list-put-not-allowed', 'owner', LIST_PATH, 405, method('PUT'), method='PUT', body={})
add('list-delete-without-pk-server-error', 'owner', LIST_PATH, 500, SERVER, method='DELETE')
add('list-options', 'owner', LIST_PATH, 200, OPTIONS('Document Archive List', {'POST': SERIALIZER}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/documents.json', 500, SERVER)
add('list-format-unknown-not-found', 'owner', '/documents.xml/', 404, BARE)
# create (stock): JSON only, validated, then a 500 without a write.
add('create-form-unsupported-media', 'owner', LIST_PATH, 415,
    {'detail': 'Unsupported media type "application/x-www-form-urlencoded" in request.'}, method='POST', raw_body='date=01%2F10%2F2026',
    content_type='application/x-www-form-urlencoded')
add('create-required', 'owner', LIST_PATH, 400, {'sport_association': [REQUIRED]}, method='POST', body={})
add('create-list-body', 'owner', LIST_PATH, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST',
    body=[])
add('create-invalid-fields', 'owner', LIST_PATH, 400, {'date': [DATE], 'folder': ['Invalid pk "999999999" - object does not exist.']},
    method='POST', body={'sport_association': OWN, 'date': '2026-10-01', 'folder': 999999999})
# The archive key is read-only: a key in the body (an existing row's, a malformed one) is ignored, the input validates and
# the insert still fails on the NOT NULL document column.
add('create-existing-id-ignored', 'owner', LIST_PATH, 500, SERVER, method='POST',
    body={'sport_association': OWN, 'sport_association_documents_archive_id': ARCHIVE['root1']['id']})
add('create-malformed-id-ignored', 'owner', LIST_PATH, 500, SERVER, method='POST',
    body={'sport_association': OWN, 'sport_association_documents_archive_id': 'abc'})
add('create-foreign-association-forbidden', 'owner', LIST_PATH, 403, FORBIDDEN, method='POST', body={'sport_association': FOREIGN})
add('create-foreign-folder-forbidden', 'owner', LIST_PATH, 403, FORBIDDEN, method='POST',
    body={'sport_association': OWN, 'folder': '$folder:f'})
add('create-valid-server-error', 'owner', LIST_PATH, 500, SERVER, method='POST',
    body={'sport_association': OWN, 'folder': '$folder:a', 'date': '01/10/2026'})
add('create-format-json-server-error', 'full_collaborator', '/documents.json', 500, SERVER, method='POST', body={'sport_association': OWN})
add('create-athlete-required', 'athlete', LIST_PATH, 400, {'sport_association': [REQUIRED]}, method='POST', body={})
add('create-athlete-server-error', 'athlete', LIST_PATH, 500, SERVER, method='POST', body={'sport_association': FOREIGN})

# The detail route.
add('detail-anonymous', 'anonymous', detail('root1'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', detail('root1'), 403, unmapped('documents/' + ARCHIVE['root1']['id']))
add('retrieve-found-server-error', 'owner', detail('root1'), 500, SERVER)
add('retrieve-format-json-server-error', 'owner', detail('in_a', '.json'), 500, SERVER)
add('retrieve-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND)
add('retrieve-missing-not-found', 'owner', detail(IDS['missing']), 404, NOT_FOUND)
add('retrieve-malformed-not-found', 'owner', detail('nope'), 404, BARE)
add('retrieve-athlete-server-error', 'athlete', detail('root1'), 500, SERVER)
add('detail-post-not-allowed', 'owner', detail('root1'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('root1'), 200, OPTIONS('Document Archive Instance', {'PUT': SERIALIZER}), method='OPTIONS')
add('detail-options-foreign-no-actions', 'owner', detail('foreign'), 200, OPTIONS('Document Archive Instance'), method='OPTIONS')
add('partial-update-server-error', 'owner', detail('root2'), 500, SERVER, method='PATCH', body={'date': '05/10/2026'})
add('update-format-json-server-error', 'owner', detail('root2', '.json'), 500, SERVER, method='PUT', body={'date': '05/10/2026'})
add('update-form-unsupported-media', 'owner', detail('root2'), 415,
    {'detail': 'Unsupported media type "application/x-www-form-urlencoded" in request.'}, method='PUT', raw_body='date=x',
    content_type='application/x-www-form-urlencoded')
add('update-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND, method='PUT', body={'date': '05/10/2026'})
add('update-invalid-date', 'owner', detail('root2'), 400, {'date': [DATE]}, method='PUT', body={'date': '2026-10-05'})
add('update-saves-then-server-error', 'owner', detail('root2'), 500, SERVER, method='PUT', body={'date': '05/10/2026'},
    effect={'kind': 'update', 'archive': ARCHIVE['root2']['id'], 'stored': {'date': '2026-10-05'},
            'changes': {'date': [ARCHIVE['root2']['date'], '2026-10-05']}})
ARCHIVE['root2'] = {**ARCHIVE['root2'], 'date': '2026-10-05'}
# The archive key is read-only: another row's key in the body is ignored and the path's row is saved in place.
add('update-primary-key-ignored', 'owner', detail('root2'), 500, SERVER, method='PUT',
    body={'sport_association_documents_archive_id': ARCHIVE['root1']['id'], 'date': '06/10/2026'},
    effect={'kind': 'update', 'archive': ARCHIVE['root2']['id'], 'stored': {'date': '2026-10-06'},
            'changes': {'date': ['2026-10-05', '2026-10-06']}})
ARCHIVE['root2'] = {**ARCHIVE['root2'], 'date': '2026-10-06'}
add('destroy-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND, method='DELETE')
add('destroy-malformed-not-found', 'owner', detail('nope'), 404, BARE, method='DELETE')
add('destroy-athlete-server-error', 'athlete', detail('root1'), 500, SERVER, method='DELETE')
add('destroy-owner', 'owner', detail('root1'), 204, '<empty>', method='DELETE', effect=deleted(['root1']))
add('destroy-again-not-found', 'owner', detail('root1'), 404, NOT_FOUND, method='DELETE')
add('destroy-format-json', 'full_collaborator', detail('in_a', '.json'), 204, '<empty>', method='DELETE', effect=deleted(['in_a']))

# The bulk_delete extra action.
add('bulk-anonymous', 'anonymous', BULK, 401, {'detail': 'Authentication credentials were not provided.'}, method='DELETE', body={})
add('bulk-restricted-unmapped', 'restricted_collaborator', BULK, 403, unmapped('documents/bulk_delete'), method='DELETE', body={})
add('bulk-get-not-allowed', 'owner', BULK, 405, method('GET'))
add('bulk-options', 'owner', BULK, 200, OPTIONS('Bulk delete'), method='OPTIONS')
add('bulk-missing-files', 'owner', BULK, 400, {'exception': 'No instances provided'}, method='DELETE', body={})
add('bulk-foreign-included', 'owner', BULK, 403, {'detail': 'User not allowed to delete some of these instances.'}, method='DELETE',
    body={'files': [ARCHIVE['root2']['id'], ARCHIVE['foreign']['id']]})
add('bulk-format-json-server-error', 'owner', '/documents/bulk_delete.json', 500, SERVER, method='DELETE',
    body={'files': [ARCHIVE['root2']['id']]})
add('bulk-owner-deletes', 'owner', BULK, 204, '<empty>', method='DELETE', body={'files': [ARCHIVE['root2']['id']]},
    effect=deleted(['root2'], documents=True))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'DefaultRouter documents routes with audit effects', 'ids': IDS,
                              'folders': FOLDERS, 'archives': LIST['archives'], 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
