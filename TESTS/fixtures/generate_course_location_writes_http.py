"""Author the course/locations add, update and delete HTTP sequence (course-locations.md).

Builds on the course-locations list seed. CourseLocationViewSet validates
CourseLocationSerializer (fields '__all__': a read-only course_location_id,
so a key in the body is ignored and the add generates one; title CharField(255, null, not blank), description
(2048) and address (255) nullable and blank, a tenant-scoped
sport_association relation and a write-only documents_data list of dicts)
before SportAssociation.objects.get(user=request.user) (a 500 for athletes
and owners without an association). add saves with the owner's association
(201); update and delete get_object() from the association's locations
(otherwise, including a malformed pk, 404) and answer 200 and 204. The
viewset's own delete() also answers DELETE on the add (no pk: 500) and update
routes, which deletes the location. The
serializer saves the location before handling documents_data, so a document
without its keys (400) or with bad base64 (500) still leaves the saved row:
requests are not atomic. Each documents_data item creates a Document, stores
its decoded bytes (default_storage.save on the runner's MinIO) and links it. auditlog records the create, the changed fields and
the delete. Run with ``python3 -I`` after the course-locations list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_location_writes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('course_locations_list_http.json').read_text()))
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
DOCUMENTS, LOCATIONS = SOURCE['documents'], SOURCE['locations']
UNKNOWN = 'a1000000-0000-4000-8000-000000989999'
NEW_ID = 'a1000000-0000-4000-8000-000000989001'
SERVER = '<stable-server-error-page>'
STATE = copy.deepcopy(LOCATIONS)
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'course-location-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def document(label):
    row = DOCUMENTS[label]
    return {'document_id': row['id'], 'creation_date': row['created'], 'filepath': row['filepath'], 'filename': row['filename'],
            'file_size_bytes': row['size'], 'token': row['token']}


def item(row, ident):
    return {'course_location_id': ident, 'documents': [document(d) for d in row.get('documents', [])], 'title': row['title'],
            'description': row['description'], 'address': row['address'], 'sport_association': OWN}


def created(actor, fields, ident='$new'):
    row = {'title': None, 'description': None, 'address': None, **fields}
    return item(row, ident), {'kind': 'add', 'actor': actor, 'id': ident, 'stored': row}


def changed(actor, label, **fields):
    """Apply a partial update to the running state; changes list auditlog's str() pairs."""
    row = STATE[label]
    changes = {key: [str(row[key]), str(value)] for key, value in fields.items() if row[key] != value}
    row.update(fields)
    return item(row, row['id']), {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes,
                                   'stored': {key: row[key] for key in ('title', 'description', 'address')}}


ADD = '/course/locations/add'
one = lambda label, verb: f'/course/locations/{LOCATIONS[label]["id"] if label in LOCATIONS else label}/{verb}'
NOT_FOUND = {'detail': 'No CourseLocation matches the given query.'}
# get_object_or_404 turns the UUID ValidationError into a bare Http404.
MALFORMED = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
OPTIONS = {'name': 'Course Location', 'description': '', 'renders': ['application/json'],
           'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
MISSING_KEY = {'exception': 'document key not present'}
field = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    **({'label': label} if label else {}), **extra}
# DRF SimpleMetadata describes the serializer for POST, the only PUT/POST method allowed on add.
ADD_OPTIONS = {**OPTIONS, 'actions': {'POST': {
    'course_location_id': field('string', 'Course location id', read_only=True),
    'documents': field('field', 'Documents', read_only=True, child={'type': 'nested object', 'required': False, 'read_only': True, 'children': {
        'document_id': field('string', 'Document id'), 'creation_date': field('datetime', 'Creation date'),
        'filepath': field('string', 'Filepath', max_length=1000), 'filename': field('string', 'Filename', required=True, max_length=255),
        'file_size_bytes': field('integer', 'File size bytes', min_value=0, max_value=9223372036854775807),
        'token': field('string', 'Token')}}),
    'documents_data': field('list', 'Documents data', child={'type': 'nested object', 'required': True, 'read_only': False,
                                                              'child': {'type': 'field', 'required': True, 'read_only': False}}),
    'title': field('string', 'Title', max_length=255), 'description': field('string', 'Description', max_length=2048),
    'address': field('string', 'Address', max_length=255), 'sport_association': field('field', 'Sport association')}}}
missing = lambda verb: {'detail': f'Missing permission: association.courses.{verb}'}

add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body={})
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, missing('create'), body={'title': 'X'})
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body={'title': 'X'}, headers=SCOPED)
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, ADD_OPTIONS, method='OPTIONS')
add('add-route-delete-without-pk-server-error', 'owner', ADD, 500, SERVER, method='DELETE')
add('add-invalid-fields', 'owner', ADD, 400, {'title': ['This field may not be blank.'],
    'description': ['Ensure this field has no more than 2048 characters.']},
    body={'course_location_id': 'abc', 'title': '', 'description': 'x' * 2049})
add('add-invalid-types', 'owner', ADD, 400, {'title': ['Not a valid string.'], 'address': ['Ensure this field has no more than 255 characters.']},
    body={'title': True, 'address': 'y' * 256})
# The body's key is ignored: another location's key still creates a location under a new key.
response, effect = created('owner', {})
add('add-existing-id-ignored', 'owner', ADD, 201, response, body={'course_location_id': LOCATIONS['gym']['id']}, effect=effect)
add('add-unknown-association', 'owner', ADD, 400, {'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    body={'sport_association': UNKNOWN})
add('add-foreign-association-forbidden', 'owner', ADD, 403, FORBIDDEN, body={'sport_association': FOREIGN, 'title': ''})
add('add-documents-data-not-list', 'owner', ADD, 400, {'documents_data': ['Expected a list of items but got type "str".']},
    body={'documents_data': 'x'})
add('add-documents-data-item-not-dict', 'owner', ADD, 400,
    {'documents_data': {'1': ['Expected a dictionary of items but got type "int".']}}, body={'documents_data': [{}, 1]})
add('add-list-body', 'owner', ADD, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, body=[])
add('add-athlete-invalid', 'athlete', ADD, 400, {'title': ['This field may not be blank.']}, body={'title': ''})
add('add-athlete-server-error', 'athlete', ADD, 500, SERVER, body={'title': 'X'})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, body={})
response, effect = created('owner', {'title': 'Campo è', 'description': 'Erba', 'address': 'Via 2'})
add('add-owner', 'owner', ADD, 201, response, body={'title': 'Campo è', 'description': 'Erba', 'address': 'Via 2'}, effect=effect)
response, effect = created('full_collaborator', {})
add('add-full-collaborator-empty', 'full_collaborator', ADD, 201, response, body={}, effect=effect)
response, effect = created('owner', {'title': '5'})
add('add-explicit-id-ignored-and-own-association', 'owner', ADD, 201, response,
    body={'course_location_id': NEW_ID, 'sport_association': OWN, 'title': 5, 'documents': ['ignored']}, effect=effect)
response, effect = created('owner', {'description': ''})
add('add-form-blank-title-is-null', 'owner', ADD, 201, response, raw_body='title=&description=',
    content_type='application/x-www-form-urlencoded', effect=effect)
response, effect = created('owner', {'title': 'Senza chiavi'})
add('add-document-missing-key-keeps-location', 'owner', ADD, 400, MISSING_KEY,
    body={'title': 'Senza chiavi', 'documents_data': [{'filename': 'a.pdf'}]}, effect=effect)
response, effect = created('owner', {'title': 'Base64'})
add('add-document-bad-base64-keeps-location', 'owner', ADD, 500, SERVER,
    body={'title': 'Base64', 'documents_data': [{'document': 'abc', 'filename': 'a.pdf'}]}, effect=effect)



def upload(filename, text, content_type, encoding=None):
    """A documents_data item (the form's base64 file) and what the runner expects stored."""
    body = __import__('base64').b64encode(text.encode()).decode()
    return {'document': body, 'filename': filename}, {'filename': filename, 'body_b64': body, 'content_type': content_type, 'encoding': encoding}


def new_documents(*specs):
    return [{'document_id': '$document', 'creation_date': '$now', 'filepath': None, 'filename': spec['filename'], 'file_size_bytes': None,
             'token': '$token'} for spec in specs]


# The location form attaches files as {document: <base64>, filename}: each becomes a Document, a stored object and a link.
first, first_spec = upload('regolamento.pdf', 'Regolamento della piscina', 'application/pdf')
second, second_spec = upload('orari è.txt', 'Lun-Ven 9-21', 'text/plain')
response, effect = created('owner', {'title': 'Con allegati'})
response['documents'] = new_documents(first_spec, second_spec)
effect['uploads'] = [first_spec, second_spec]
add('add-with-documents', 'owner', ADD, 201, response, body={'title': 'Con allegati', 'documents_data': [first, second]}, effect=effect)
third, third_spec = upload('mappa.tar.gz', 'archivio', 'application/x-tar', 'gzip')
response, effect = created('owner', {'title': 'Archivio'})
response['documents'] = new_documents(third_spec)
effect['uploads'] = [third_spec]
add('add-with-compressed-document', 'owner', ADD, 201, response, body={'title': 'Archivio', 'documents_data': [third]}, effect=effect)

add('update-anonymous', 'anonymous', one('gym', 'update'), 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', one('gym', 'update'), 403, missing('update'), method='PATCH',
    body={'title': 'X'})
add('update-wrong-method', 'owner', one('gym', 'update'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('update-options', 'owner', one('gym', 'update'), 200, OPTIONS, method='OPTIONS')
add('update-malformed-not-found', 'owner', one('abc', 'update'), 404, MALFORMED, method='PATCH', body={'title': 'X'})
add('update-unknown-not-found', 'owner', one(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={'title': 'X'})
add('update-foreign-not-found', 'owner', one('abroad', 'update'), 404, NOT_FOUND, method='PATCH', body={'title': 'X'})
add('update-athlete-server-error', 'athlete', one('gym', 'update'), 500, SERVER, method='PATCH', body={'title': 'X'})
add('update-blank-title', 'owner', one('gym', 'update'), 400, {'title': ['This field may not be blank.']}, method='PATCH', body={'title': ''})
add('update-foreign-association-forbidden', 'owner', one('gym', 'update'), 403, FORBIDDEN, method='PATCH',
    body={'sport_association': FOREIGN})
response, effect = changed('owner', 'gym', title='Palestra', address='Via 3')
add('update-owner', 'owner', one('gym', 'update'), 200, response, method='PATCH', body={'title': 'Palestra', 'address': 'Via 3'}, effect=effect)
response, effect = changed('full_collaborator', 'gym')
add('update-same-values-no-audit', 'full_collaborator', one('gym', 'update'), 200, response, method='PATCH',
    body={'title': 'Palestra', 'sport_association': OWN}, effect=effect)
response, effect = changed('owner', 'pool', title=None, address='')
add('update-null-title', 'owner', one('pool', 'update'), 200, response, method='PATCH', body={'title': None, 'address': ''}, effect=effect)
# course_location_id is read-only: a key in the body is ignored and the path's row is updated in place.
response, effect = changed('owner', 'pool', title='Copia')
add('update-primary-key-ignored', 'owner', one('pool', 'update'), 200, response, method='PATCH',
    body={'course_location_id': 'a1000000-0000-4000-8000-000000989002', 'title': 'Copia'}, effect=effect)
response, effect = changed('owner', 'pool')
add('update-primary-key-of-other-row-ignored', 'owner', one('pool', 'update'), 200, response, method='PATCH',
    body={'course_location_id': LOCATIONS['gym']['id']}, effect=effect)
response, effect = changed('owner', 'pool')
add('update-primary-key-malformed-ignored', 'owner', one('pool', 'update'), 200, response, method='PATCH',
    body={'course_location_id': 'abc'}, effect=effect)
FORM = 'application/x-www-form-urlencoded'
quote = __import__('urllib.parse', fromlist=['quote']).quote
NOT_DICT = lambda kind: [f'Expected a dictionary of items but got type "{kind}".']
sixth, sixth_spec = upload('modulo.pdf', 'Modulo di iscrizione', 'application/pdf')
response, effect = changed('owner', 'gym')
add('update-form-indexed-keys-ignored', 'owner', one('gym', 'update'), 200, response, method='PATCH', effect=effect, content_type=FORM,
    raw_body=f'documents_data[0].document={quote(sixth["document"])}&documents_data[0].filename={quote(sixth["filename"])}')
add('update-form-documents-data-key', 'owner', one('gym', 'update'), 400, {'documents_data': {'0': NOT_DICT('str')}}, method='PATCH',
    raw_body='documents_data=abc&documents_data[0].document=x', content_type=FORM)


response, effect = changed('owner', 'gym', description='Nuova')
add('update-document-missing-key-keeps-change', 'owner', one('gym', 'update'), 400, MISSING_KEY, method='PATCH',
    body={'description': 'Nuova', 'documents_data': [{'document': ''}]}, effect=effect)

# Not atomic: the first document is stored and linked before the second item raises.
fourth, fourth_spec = upload('turni.pdf', 'Turni estivi', 'application/pdf')
response, effect = changed('owner', 'pool')
effect['uploads'] = [fourth_spec]
add('update-document-then-missing-key', 'owner', one('pool', 'update'), 400, MISSING_KEY, method='PATCH',
    body={'documents_data': [fourth, {'filename': 'senza.pdf'}]}, effect=effect)
fifth, fifth_spec = upload('nuovo.pdf', 'Nuovo regolamento', 'application/pdf')
response, effect = changed('owner', 'gym', title='Palestra grande')
response['documents'] = response['documents'] + new_documents(fifth_spec)
effect['uploads'] = [fifth_spec]
add('update-with-document', 'owner', one('gym', 'update'), 200, response, method='PATCH',
    body={'title': 'Palestra grande', 'documents_data': [fifth]}, effect=effect)

# Form bodies: ListField reads the repeated key documents_data as a list of texts; without it, DRF's html list notation
# documents_data[N].name=... builds one dict per index (sorted by number). A partial update ignores the indexed keys.
FORM = 'application/x-www-form-urlencoded'
quote = __import__('urllib.parse', fromlist=['quote']).quote
NOT_DICT = lambda kind: [f'Expected a dictionary of items but got type "{kind}".']


def multipart(*parts):
    boundary = 'testboundary4f2a'
    lines = []
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
        lines.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n{content}\r\n')
    return ''.join(lines) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


add('add-form-documents-data-key', 'owner', ADD, 400, {'documents_data': {'0': NOT_DICT('str')}}, raw_body='title=Form&documents_data=abc',
    content_type=FORM)
add('add-form-documents-data-repeated', 'owner', ADD, 400, {'documents_data': {'0': NOT_DICT('str'), '1': NOT_DICT('str')}},
    raw_body='documents_data=a&documents_data=b&documents_data[0].document=x', content_type=FORM)
add('add-form-indexed-text', 'owner', ADD, 400, {'documents_data': {'0': NOT_DICT('str'), '1': NOT_DICT('str')}},
    raw_body='documents_data[3]=a&documents_data[1]=b&documents_data[x]=ignored&other[0]=ignored', content_type=FORM)
add('add-form-indexed-text-after-dict', 'owner', ADD, 400, {'documents_data': {'0': NOT_DICT('str')}},
    raw_body='documents_data[0].document=b&documents_data[0]=a', content_type=FORM)
response, effect = created('owner', {})
add('add-form-indexed-dict-after-text-keeps-location', 'owner', ADD, 400, MISSING_KEY, effect=effect,
    raw_body='documents_data[0]=a&documents_data[0].document=b', content_type=FORM)
response, effect = created('owner', {'title': 'Indici non validi'})
add('add-form-indexed-keys-not-matching', 'owner', ADD, 201, response, effect=effect,
    raw_body='title=Indici+non+validi&documents_data[x]=1&documents_data[0=2&documents_data0=3&other[0].document=4', content_type=FORM)
response, effect = created('owner', {'title': 'Senza nome'})
add('add-form-indexed-without-dot-keeps-location', 'owner', ADD, 400, MISSING_KEY, effect=effect,
    raw_body='title=Senza+nome&documents_data[0][document]=eA==&documents_data[0][filename]=a.pdf', content_type=FORM)
response, effect = created('owner', {'title': 'Un file'})
response['documents'] = new_documents(sixth_spec)
effect['uploads'] = [sixth_spec]
add('add-form-indexed-document', 'owner', ADD, 201, response, effect=effect, content_type=FORM,
    raw_body=f'title=Un+file&documents_data[0].document={quote(sixth["document"])}&documents_data[0].filename={quote(sixth["filename"])}')
response, effect = created('owner', {'title': 'Due file'})
seventh, seventh_spec = upload('orari.txt', 'Orari estivi', 'text/plain')
eighth, eighth_spec = upload('mappa.txt', 'Mappa del campo', 'text/plain')
response['documents'] = new_documents(seventh_spec, eighth_spec)
effect['uploads'] = [seventh_spec, eighth_spec]
# Index 2 comes first in the body and 10 after it; the items are sorted by number, and 02 is the same index as 2.
add('add-form-indexed-two-documents-sorted', 'owner', ADD, 201, response, effect=effect, content_type=FORM,
    raw_body=(f'title=Due+file&documents_data[10].filename={quote(eighth["filename"])}&documents_data[02].document={quote(seventh["document"])}'
              f'&documents_data[10].document={quote(eighth["document"])}&documents_data[2].filename={quote(seventh["filename"])}'))
response, effect = created('owner', {'title': 'Multipart'})
ninth, ninth_spec = upload('multipart.txt', 'Dal modulo con file', 'text/plain')
response['documents'] = new_documents(ninth_spec)
effect['uploads'] = [ninth_spec]
body, content_type = multipart(('title', None, 'Multipart'), ('documents_data[0].document', None, ninth['document']),
                               ('documents_data[0].filename', None, ninth['filename']), ('allegato', 'a.txt', 'dati'))
add('add-multipart-indexed-document', 'owner', ADD, 201, response, effect=effect, raw_body=body, content_type=content_type)
body, content_type = multipart(('title', None, 'Multipart'), ('documents_data', 'doc.bin', 'x'))
add('add-multipart-documents-data-file', 'owner', ADD, 400, {'documents_data': {'0': NOT_DICT('InMemoryUploadedFile')}},
    raw_body=body, content_type=content_type)
response, effect = created('owner', {'title': 'File come documento'})
body, content_type = multipart(('title', None, 'File come documento'), ('documents_data[0].document', 'doc.bin', 'eA=='),
                               ('documents_data[0].filename', None, 'a.pdf'))
add('add-multipart-indexed-document-as-file-server-error', 'owner', ADD, 500, SERVER, raw_body=body, content_type=content_type, effect=effect)
add('delete-restricted-missing-permission', 'restricted_collaborator', one('pool', 'delete'), 403, missing('delete'), method='DELETE')
add('delete-wrong-method', 'owner', one('pool', 'delete'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('delete-options', 'owner', one('pool', 'delete'), 200, OPTIONS, method='OPTIONS')
add('delete-malformed-not-found', 'owner', one('abc', 'delete'), 404, MALFORMED, method='DELETE')
add('delete-foreign-not-found', 'owner', one('abroad', 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-athlete-server-error', 'athlete', one('pool', 'delete'), 500, SERVER, method='DELETE')
add('delete-owner-with-documents', 'owner', one('pool', 'delete'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'label': 'pool'})
add('delete-again-not-found', 'owner', one('pool', 'delete'), 404, NOT_FOUND, method='DELETE')
add('update-route-delete-deletes', 'full_collaborator', one('gym', 'update'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'label': 'gym'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Course location add, update and delete authored sequence with audit and storage effects',
                              'location': 'course-locations', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
