"""Author the DefaultRouter course-locations HTTP sequence (router-stock.md).

`router.register(r'course-locations', CourseLocationViewSet)`: the list route
binds GET to the viewset's list() and POST to CreateModelMixin.create (the
same serializer, perform_create and 201 body as the add route; the serializer
has no 'url' field, so no Location header). The detail route binds GET to the
stock retrieve() (get_object() over get_queryset(): the owner's locations,
404 otherwise, a malformed pk the bare 'Not found.'), PUT to the viewset's
update() (partial), PATCH to partial_update(), which calls update() with
partial=True that its signature does not accept (500, nothing written), and
DELETE to the stock destroy() (instance.delete()). The viewset's own delete()
answers DELETE on the list route (no pk: 500). Format twins pass format= to
the handler: list() and update() do not accept it (500), create(),
retrieve() and destroy() do; an unknown format is content negotiation's 404
before authentication. There is no permission-registry key for these paths
(restricted collaborators: 'No permission mapping'). OPTIONS names the view
'<Name> List' or '<Name> Instance' and adds SimpleMetadata's actions: POST on
the list route, PUT on the detail route when get_object() succeeds.
Builds on the course-locations list seed. Run with ``python3 -I`` after the
course-locations list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_course_locations_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('course_locations_list_http.json').read_text()))
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
DOCUMENTS, LOCATIONS = SOURCE['documents'], SOURCE['locations']
UNKNOWN = 'a1000000-0000-4000-8000-000000989999'
SERVER = '<stable-server-error-page>'
STATE = copy.deepcopy(LOCATIONS)
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'router-course-locations-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
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
            'description': row['description'], 'address': row['address'], 'sport_association': row.get('association', OWN)}


def created(actor, fields, ident='$new'):
    row = {'title': None, 'description': None, 'address': None, **fields}
    return item(row, ident), {'kind': 'add', 'actor': actor, 'id': ident, 'stored': row}


def changed(actor, label, **fields):
    row = STATE[label]
    changes = {key: [str(row[key]), str(value)] for key, value in fields.items() if row[key] != value}
    row.update(fields)
    return item(row, row['id']), {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes,
                                   'stored': {key: row[key] for key in ('title', 'description', 'address')}}


LIST = '/course-locations/'
detail = lambda label, suffix='/': f'/course-locations/{LOCATIONS[label]["id"] if label in LOCATIONS else label}{suffix}'
NOT_FOUND = {'detail': 'No CourseLocation matches the given query.'}
BARE = {'detail': 'Not found.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
method = lambda name: {'detail': f'Method "{name}" not allowed.'}
field = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    **({'label': label} if label else {}), **extra}
FIELDS = {
    'course_location_id': field('string', 'Course location id'),
    'documents': field('field', 'Documents', read_only=True, child={'type': 'nested object', 'required': False, 'read_only': True, 'children': {
        'document_id': field('string', 'Document id'), 'creation_date': field('datetime', 'Creation date'),
        'filepath': field('string', 'Filepath', max_length=1000), 'filename': field('string', 'Filename', required=True, max_length=255),
        'file_size_bytes': field('integer', 'File size bytes', min_value=0, max_value=9223372036854775807),
        'token': field('string', 'Token')}}),
    'documents_data': field('list', 'Documents data', child={'type': 'nested object', 'required': True, 'read_only': False,
                                                              'child': {'type': 'field', 'required': True, 'read_only': False}}),
    'title': field('string', 'Title', max_length=255), 'description': field('string', 'Description', max_length=2048),
    'address': field('string', 'Address', max_length=255), 'sport_association': field('field', 'Sport association')}
OPTIONS = lambda suffix, actions=None: {'name': f'Course Location {suffix}', 'description': '', 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
                                        **({'actions': actions} if actions else {})}
OWN_ROWS = {'data': [item(LOCATIONS['pool'], LOCATIONS['pool']['id']), item(LOCATIONS['gym'], LOCATIONS['gym']['id'])]}

# The list route.
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('list-admin-without-session', 'admin', LIST, 403, ADMIN)
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('course-locations'))
add('list-restricted-unmapped-post', 'restricted_collaborator', LIST, 403, unmapped('course-locations'), method='POST', body={'title': 'X'})
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_DENIED, headers=SCOPED)
add('list-owner', 'owner', LIST, 200, OWN_ROWS)
add('list-full-collaborator', 'full_collaborator', LIST, 200, OWN_ROWS)
add('list-admin-impersonating-owner', 'admin', LIST, 200, OWN_ROWS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-foreign-owner', 'foreign_owner', LIST, 200, {'data': [item(LOCATIONS['abroad'], LOCATIONS['abroad']['id'])]})
add('list-athlete-server-error', 'athlete', LIST, 500, SERVER)
add('list-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER)
add('list-put-not-allowed', 'owner', LIST, 405, method('PUT'), method='PUT', body={})
add('list-patch-not-allowed', 'owner', LIST, 405, method('PATCH'), method='PATCH', body={})
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-options', 'owner', LIST, 200, OPTIONS('List', {'POST': FIELDS}), method='OPTIONS')
add('list-options-athlete', 'athlete', LIST, 200, OPTIONS('List', {'POST': FIELDS}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/course-locations.json', 500, SERVER)
add('list-format-json-slash-server-error', 'owner', '/course-locations.json/', 500, SERVER)
add('list-format-unknown-not-found', 'owner', '/course-locations.xml', 404, BARE)
add('list-format-unknown-anonymous-not-found', 'anonymous', '/course-locations.api/', 404, BARE, method='POST', body={})
add('list-query-format-unknown-not-found', 'owner', LIST + '?format=xml', 404, BARE)
# create (CreateModelMixin.create with the viewset's perform_create).
add('create-invalid-fields', 'owner', LIST, 400, {'course_location_id': ['Must be a valid UUID.'], 'title': ['This field may not be blank.'],
    'description': ['Ensure this field has no more than 2048 characters.']}, method='POST',
    body={'course_location_id': 'abc', 'title': '', 'description': 'x' * 2049})
add('create-existing-id', 'owner', LIST, 400, {'course_location_id': ['course location with this course location id already exists.']},
    method='POST', body={'course_location_id': LOCATIONS['gym']['id']})
add('create-unknown-association', 'owner', LIST, 400, {'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    method='POST', body={'sport_association': UNKNOWN})
add('create-foreign-association-forbidden', 'owner', LIST, 403, FORBIDDEN, method='POST', body={'sport_association': FOREIGN, 'title': ''})
add('create-list-body', 'owner', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST', body=[])
add('create-athlete-invalid', 'athlete', LIST, 400, {'title': ['This field may not be blank.']}, method='POST', body={'title': ''})
add('create-athlete-server-error', 'athlete', LIST, 500, SERVER, method='POST', body={'title': 'X'})
response, effect = created('owner', {'title': 'Campo router', 'description': 'Erba', 'address': 'Via 9'})
add('create-owner', 'owner', LIST, 201, response, method='POST', body={'title': 'Campo router', 'description': 'Erba', 'address': 'Via 9'},
    effect=effect)
response, effect = created('full_collaborator', {})
add('create-full-collaborator-empty', 'full_collaborator', LIST, 201, response, method='POST', body={}, effect=effect)
response, effect = created('owner', {'title': 'Formato'})
add('create-format-json', 'owner', '/course-locations.json', 201, response, method='POST', body={'title': 'Formato'}, effect=effect)
response, effect = created('owner', {'description': ''})
add('create-form-blank-title-is-null', 'owner', LIST, 201, response, method='POST', raw_body='title=&description=',
    content_type='application/x-www-form-urlencoded', effect=effect)
response, effect = created('owner', {'title': 'Senza chiavi'})
add('create-document-missing-key-keeps-location', 'owner', LIST, 400, {'exception': 'document key not present'}, method='POST',
    body={'title': 'Senza chiavi', 'documents_data': [{'filename': 'a.pdf'}]}, effect=effect)

# The detail route.
add('detail-anonymous', 'anonymous', detail('pool'), 401, ANONYMOUS)
add('detail-admin-without-session', 'admin', detail('pool'), 403, ADMIN)
add('detail-restricted-unmapped', 'restricted_collaborator', detail('pool'), 403, unmapped('course-locations/' + LOCATIONS['pool']['id']))
add('detail-owner-scoped-athlete-denied', 'owner', detail('pool'), 403, SCOPED_DENIED, headers=SCOPED)
add('retrieve-owner', 'owner', detail('pool'), 200, item(LOCATIONS['pool'], LOCATIONS['pool']['id']))
add('retrieve-full-collaborator', 'full_collaborator', detail('gym'), 200, item(LOCATIONS['gym'], LOCATIONS['gym']['id']))
add('retrieve-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND)
add('retrieve-unknown-not-found', 'owner', detail(UNKNOWN), 404, NOT_FOUND)
add('retrieve-malformed-not-found', 'owner', detail('abc'), 404, BARE)
add('retrieve-list-segment-not-found', 'owner', detail('list'), 404, BARE)
add('retrieve-athlete-server-error', 'athlete', detail('pool'), 500, SERVER)
add('retrieve-format-json', 'owner', detail('pool', '.json'), 200, item(LOCATIONS['pool'], LOCATIONS['pool']['id']))
add('retrieve-format-json-slash', 'foreign_owner', detail('abroad', '.json/'), 200, item(LOCATIONS['abroad'], LOCATIONS['abroad']['id']))
add('retrieve-format-unknown-not-found', 'owner', detail('pool', '.yaml'), 404, BARE)
add('detail-post-not-allowed', 'owner', detail('pool'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('pool'), 200, OPTIONS('Instance', {'PUT': FIELDS}), method='OPTIONS')
add('detail-options-foreign-no-actions', 'owner', detail('abroad'), 200, OPTIONS('Instance'), method='OPTIONS')
add('detail-options-malformed-no-actions', 'owner', detail('abc'), 200, OPTIONS('Instance'), method='OPTIONS')
add('detail-options-athlete-server-error', 'athlete', detail('pool'), 500, SERVER, method='OPTIONS')
add('partial-update-server-error', 'owner', detail('gym'), 500, SERVER, method='PATCH', body={'title': 'Mai'})
add('update-format-json-server-error', 'owner', detail('gym', '.json'), 500, SERVER, method='PUT', body={'title': 'Mai'})
add('update-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND, method='PUT', body={'title': 'X'})
add('update-malformed-not-found', 'owner', detail('abc'), 404, BARE, method='PUT', body={'title': 'X'})
add('update-athlete-server-error', 'athlete', detail('gym'), 500, SERVER, method='PUT', body={'title': 'X'})
add('update-blank-title', 'owner', detail('gym'), 400, {'title': ['This field may not be blank.']}, method='PUT', body={'title': ''})
add('update-foreign-association-forbidden', 'owner', detail('gym'), 403, FORBIDDEN, method='PUT', body={'sport_association': FOREIGN})
response, effect = changed('owner', 'gym', title='Palestra router', address='Via 4')
add('update-owner-put-is-partial', 'owner', detail('gym'), 200, response, method='PUT', body={'title': 'Palestra router', 'address': 'Via 4'},
    effect=effect)
response, effect = changed('full_collaborator', 'gym')
add('update-same-values-no-audit', 'full_collaborator', detail('gym'), 200, response, method='PUT', body={'title': 'Palestra router'},
    effect=effect)
add('destroy-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND, method='DELETE')
add('destroy-malformed-not-found', 'owner', detail('abc'), 404, BARE, method='DELETE')
add('destroy-athlete-server-error', 'athlete', detail('pool'), 500, SERVER, method='DELETE')
add('destroy-owner-with-documents', 'owner', detail('pool'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'label': 'pool'})
add('destroy-again-not-found', 'owner', detail('pool'), 404, NOT_FOUND, method='DELETE')
add('destroy-format-json', 'full_collaborator', detail('gym', '.json'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'label': 'gym'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'DefaultRouter course-locations list, create, retrieve, update, destroy and format routes',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
