"""Author the modules add/update/duplicate/delete HTTP sequence (modules-writes.md).

Four @api_view IsAuthenticated views of modules_views.py:
- modules_add (POST): request.user.sport_association (athletes PermissionDenied,
  an owner without an active association a 500), then
  request.data['sport_association'] = own id (a list, string or form body
  raises, 500) and ModulesAddSerializer(fields='__all__'): every field is
  optional; DRF rules from the Module fields (UUID pk and custom_link with
  UniqueValidator, CharField(255/1024, null, not blank), DateField with
  DATE_INPUT_FORMATS ['%d/%m/%Y'], PositiveIntegerField 0..2147483647,
  BooleanField, JSONField not null). Errors are DRF's 400 field map; success
  is 200 {'msg': 'module created'}.
- modules_update (PATCH): is_valid_uuid (400 list) first;
  Module.objects.get(module_id=...) (miss: 404
  {'error': 'Camp or retreat not found'}); the debug log's extra evaluates
  module.sport_association.sport_association_id (no association:
  AttributeError) and request.user.sport_association (athletes: the DRF
  PermissionDenied, re-raised; an owner without an association: DoesNotExist);
  a module of another association is PermissionDenied 'You are not allowed to
  update this camp or retreat'. ModulesSerializer(partial=True) nests
  SportAssociationSerializer; DRF's ValidationError is caught and rendered as
  400 {'error': str(e)} (the ErrorDetail repr, in field order); any other
  exception (including the nested-write assertion) is 500 {'error': str(e)}.
  Success: 200 with the message and the serialized module.
- modules_duplicate (POST, no registry key): is_valid_uuid, then
  Module.objects.filter(module_id=..., sport_association=request.user.
  sport_association).first() (miss: 404 {'error': 'module not found'}),
  Module.objects.create(...) copying the fields with title '<title> (copia)'
  and custom_link uuid4().hex[:12]; 201 {'data': ModulesSerializer(clone)}.
- modules_delete (DELETE): no UUID check (a malformed id is the 500 page),
  the same filter (404), module.delete() cascading to its ModuleResponses and
  their attachment links (documents stay); 200 {'msg': 'module deleted'}.
auditlog records creates, changed fields and deletes (responses then module,
post_delete). Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / 'modules_writes_http.json'
IDS = json.loads((HERE / 'message_mutations.json').read_text())['ids']
OWN, FOREIGN, DELETED = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{642001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000642999'
CLIENT = 'a1000000-0000-4000-8000-000000642900'
# SportAssociationSerializer of the own association, as authored by the modules reads family.
READS = json.loads((HERE / 'modules_reads_http.json').read_text())
PROFILE = next(c for c in READS['cases'] if c['name'] == 'modules-reads-list-owner')['response']['data'][0]['sport_association']
assert PROFILE['sport_association_id'] == OWN

BASE = {'association': OWN, 'link': None, 'created': '2026-09-01T08:00:00Z', 'title': 'Modulo', 'require_approval': False,
        'start': None, 'end': None, 'always_active': False, 'max_responses': None, 'queue_mode': False, 'only_users': False,
        'payment_required': False, 'payment_data': {}, 'response_message': None, 'allow_attachments': False, 'elements': {}}
MODULES = {
    'edit': {**BASE, 'id': next(ids), 'link': 'modifica', 'title': 'Da modificare'},
    'taken': {**BASE, 'id': next(ids), 'link': 'occupato', 'title': 'Occupato'},
    # The dates are far enough from today (UTC) for 'enabled' to be stable.
    'source': {**BASE, 'id': next(ids), 'link': 'sorgente', 'created': '2026-08-01T10:00:00.250000Z', 'title': 'Iscrizioni è',
               'require_approval': True, 'start': '2000-01-01', 'end': '2999-12-31', 'max_responses': 40, 'queue_mode': True,
               'only_users': True, 'payment_required': True, 'payment_data': {'amount': 12.5, 'methods': ['card'], 'note': None},
               'response_message': 'Grazie\nè', 'allow_attachments': True, 'elements': [{'type': 'text', 'label': 'Nome'}, 3]},
    'untitled': {**BASE, 'id': next(ids), 'title': None, 'always_active': True, 'payment_data': [], 'elements': 'testo'},
    'remove': {**BASE, 'id': next(ids), 'link': 'rimuovi', 'title': 'Da eliminare'},
    'remove_collab': {**BASE, 'id': next(ids), 'link': 'rimuovi-collaboratore', 'title': 'Collaboratore'},
    'remove_plain': {**BASE, 'id': next(ids), 'title': 'Senza risposte', 'max_responses': 3},
    'foreign': {**BASE, 'id': next(ids), 'association': FOREIGN, 'link': 'estero-modulo', 'title': 'Estero'},
    'abroad': {**BASE, 'id': next(ids), 'association': DELETED, 'link': 'cancellata', 'title': 'Associazione cancellata'},
    'orphan': {**BASE, 'id': next(ids), 'association': None, 'link': 'orfano', 'title': 'Orfano'},
}
DOCUMENTS = {'d1': {'id': next(ids), 'filename': 'certificato è.pdf', 'token': next(ids)},
             'd2': {'id': next(ids), 'filename': 'foto.png', 'token': next(ids)}}
RESPONSES = {
    'source_r': {'id': next(ids), 'module': 'source', 'created': '2026-09-02T10:00:00Z', 'number': 1, 'queue': None,
                 'response': {'nome': 'Anna'}, 'approved': True, 'user': 'athlete', 'attachments': ['d2']},
    'remove_r1': {'id': next(ids), 'module': 'remove', 'created': '2026-09-02T10:00:00.500000Z', 'number': 1, 'queue': 2,
                  'response': {'nome': 'Anna è', 'eta': 12, 'consensi': [True, None]}, 'approved': True, 'user': 'athlete',
                  'attachments': ['d1', 'd2']},
    'remove_r2': {'id': next(ids), 'module': 'remove', 'created': '2026-09-03T10:00:00Z', 'number': None, 'queue': None,
                  'response': [], 'approved': False, 'user': None, 'attachments': []},
    'collab_r': {'id': next(ids), 'module': 'remove_collab', 'created': '2026-09-04T10:00:00Z', 'number': 2, 'queue': None,
                 'response': {}, 'approved': True, 'user': 'owner', 'attachments': ['d1']},
}
STATE = copy.deepcopy(MODULES)
cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'modules-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def serialized(row, *, ident=None, link=None, created=None, title=None, enabled):
    """ModulesSerializer: every field, 'enabled' authored per row, the nested association."""
    return {'module_id': ident or row['id'], 'enabled': enabled, 'sport_association': PROFILE,
            'custom_link': link or row['link'], 'creation_date': created or row['created'],
            'title': row['title'] if title is None else title, 'require_approval': row['require_approval'],
            'start_date': row['start'], 'end_date': row['end'], 'always_active': row['always_active'],
            'max_responses': row['max_responses'], 'queue_mode': row['queue_mode'], 'only_users': row['only_users'],
            'payment_required': row['payment_required'], 'payment_data': row['payment_data'],
            'response_message': row['response_message'], 'allow_attachments': row['allow_attachments'], 'elements': row['elements']}


def stored(row):
    return {key: row[key] for key in ('association', 'link', 'title', 'require_approval', 'start', 'end', 'always_active',
                                     'max_responses', 'queue_mode', 'only_users', 'payment_required', 'payment_data',
                                     'response_message', 'allow_attachments', 'elements')}


def audit_text(key, value):
    """auditlog's str() of one Module field value."""
    if value is None:
        return 'None'
    if key in ('payment_data', 'elements'):
        return json.dumps(value, sort_keys=True)
    if key == 'created':
        return value.replace('T', ' ').replace('Z', '')
    return str(value)


FIELD_NAMES = {'link': 'custom_link', 'created': 'creation_date', 'start': 'start_date', 'end': 'end_date'}


def changed(label, actor, **fields):
    """Apply a partial update to the running state; changes are auditlog's str() pairs of changed fields."""
    row = STATE[label]
    changes = {FIELD_NAMES.get(k, k): [audit_text(k, row[k]), audit_text(k, v)] for k, v in fields.items()
               if audit_text(k, row[k]) != audit_text(k, v)}
    row.update(fields)
    return {'kind': 'update', 'actor': actor, 'label': label, 'stored': stored(row), 'created': row['created'], 'changes': changes}


OPTIONS = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
PERMISSION = lambda key: {'detail': f'Missing permission: {key}'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
SCOPED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NO_SESSION = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
OWNER_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
METHOD = lambda method: {'detail': f'Method "{method}" not allowed.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
mid = lambda label: MODULES[label]['id'] if label in MODULES else label

# ---------------------------------------------------------------- modules/add
ADD = '/modules/add'
CREATED = {'msg': 'module created'}
DEFAULTS = {'association': OWN, 'link': None, 'title': None, 'require_approval': False, 'start': None, 'end': None,
            'always_active': False, 'max_responses': None, 'queue_mode': False, 'only_users': False, 'payment_required': False,
            'payment_data': {}, 'response_message': None, 'allow_attachments': False, 'elements': {}}


def created(actor, *, fixed_id=None, created_at='$now', **fields):
    return {'kind': 'create', 'actor': actor, 'fixed_id': fixed_id, 'created': created_at, 'stored': {**DEFAULTS, **fields}}


add('add-anonymous', 'anonymous', 'POST', ADD, 401, ANONYMOUS, body={})
add('add-admin-without-session', 'admin', 'POST', ADD, 403, NO_SESSION, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', 'POST', ADD, 403, PERMISSION('association.modules.create'), body={})
add('add-restricted-get-missing-permission', 'restricted_collaborator', 'GET', ADD, 403, PERMISSION('association.modules.create'))
add('add-owner-scoped-athlete', 'owner', 'POST', ADD, 403, SCOPED, body={}, headers=OWNER_ATHLETE)
add('add-wrong-method', 'owner', 'GET', ADD, 405, METHOD('GET'))
add('add-delete-method', 'owner', 'DELETE', ADD, 405, METHOD('DELETE'))
add('add-options', 'owner', 'OPTIONS', ADD, 200, OPTIONS('Modules Add'))
add('add-athlete-denied', 'athlete', 'POST', ADD, 403, NOT_ASSOCIATION, body={'title': 'x'})
add('add-owner-without-association', 'owner_without_association', 'POST', ADD, 500, SERVER, body={'title': 'x'})
add('add-deleted-association-owner', 'deleted_association_owner', 'POST', ADD, 500, SERVER, body={'title': 'x'})
# request.data['sport_association'] = ... on a list, a str and an immutable QueryDict.
add('add-list-body-server-error', 'owner', 'POST', ADD, 500, SERVER, body=[])
add('add-string-body-server-error', 'owner', 'POST', ADD, 500, SERVER, body='testo')
add('add-form-body-server-error', 'owner', 'POST', ADD, 500, SERVER, raw_body='title=x', content_type='application/x-www-form-urlencoded')
add('add-invalid-fields', 'owner', 'POST', ADD, 400, {
    'module_id': ['Must be a valid UUID.'], 'custom_link': ['module with this custom link already exists.'],
    'creation_date': ['Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'],
    'title': ['Ensure this field has no more than 255 characters.'], 'require_approval': ['Must be a valid boolean.'],
    'start_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'end_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'always_active': ['This field may not be null.'], 'max_responses': ['Ensure this value is greater than or equal to 0.'],
    'queue_mode': ['Must be a valid boolean.'], 'payment_data': ['This field may not be null.'],
    'response_message': ['Not a valid string.'], 'elements': ['This field may not be null.']},
    body={'module_id': 'nope', 'custom_link': 'occupato', 'creation_date': 'ieri', 'title': 'x' * 256, 'require_approval': 'forse',
          'start_date': '2026-01-01', 'end_date': 5, 'always_active': None, 'max_responses': -1, 'queue_mode': [],
          'payment_data': None, 'response_message': True, 'elements': None, 'sport_association': 'ignorata'})
add('add-more-invalid-fields', 'owner', 'POST', ADD, 400, {
    'module_id': ['module with this module id already exists.'], 'custom_link': ['This field may not be blank.'],
    'creation_date': ['This field may not be null.'], 'title': ['This field may not be blank.'],
    'start_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'max_responses': ['Ensure this value is less than or equal to 2147483647.'],
    'response_message': ['Ensure this field has no more than 1024 characters.'], 'only_users': ['This field may not be null.']},
    body={'module_id': MODULES['taken']['id'].upper(), 'custom_link': '  ', 'creation_date': None, 'title': ' ', 'start_date': '31/02/2026',
          'max_responses': 2147483648, 'response_message': 'y' * 1025, 'only_users': None})
add('add-integer-text-invalid', 'owner', 'POST', ADD, 400, {'max_responses': ['A valid integer is required.'],
                                                           'custom_link': ['Ensure this field has no more than 255 characters.']},
    body={'max_responses': 'abc', 'custom_link': 'z' * 256})
add('add-owner-defaults', 'owner', 'POST', ADD, 200, CREATED, body={}, effect=created('owner'))
add('add-owner-every-field', 'owner', 'POST', ADD, 200, CREATED, body={
    'module_id': CLIENT, 'custom_link': ' nuovo-è ', 'creation_date': '2026-03-01T10:00:00.5+02:00', 'title': '  Nuovo modulo  ',
    'require_approval': 'true', 'start_date': '1/2/2026', 'end_date': '28/02/2026', 'always_active': 1, 'max_responses': '25',
    'queue_mode': True, 'only_users': 'off', 'payment_required': 'yes', 'payment_data': {'z': 1, 'a': [1, 'è', None]},
    'response_message': 12.5, 'allow_attachments': 'on', 'elements': [{'type': 'text'}, 'x'], 'enabled': False,
    'sport_association': FOREIGN, 'responses': 'ignorate'},
    effect=created('owner', fixed_id=CLIENT, created_at='2026-03-01T08:00:00.500000Z', link='nuovo-è', title='Nuovo modulo',
                   require_approval=True, start='2026-02-01', end='2026-02-28', always_active=True, max_responses=25, queue_mode=True,
                   payment_required=True, payment_data={'z': 1, 'a': [1, 'è', None]}, response_message='12.5', allow_attachments=True,
                   elements=[{'type': 'text'}, 'x']))
add('add-client-id-taken', 'owner', 'POST', ADD, 400, {'module_id': ['module with this module id already exists.'],
                                                       'custom_link': ['module with this custom link already exists.']},
    body={'module_id': CLIENT, 'custom_link': 'nuovo-è'})
add('add-explicit-nulls', 'full_collaborator', 'POST', ADD, 200, CREATED,
    body={'custom_link': None, 'title': None, 'start_date': None, 'end_date': None, 'max_responses': None, 'response_message': None,
          'payment_data': 'testo', 'elements': 0},
    effect=created('full_collaborator', payment_data='testo', elements=0))
add('add-admin-impersonating-owner', 'admin', 'POST', ADD, 200, CREATED, body={'title': 'Amministratore', 'max_responses': 0},
    headers=ADMIN_OWNER, effect=created('admin', title='Amministratore', max_responses=0))
add('add-foreign-owner-own-association', 'foreign_owner', 'POST', ADD, 200, CREATED, body={'title': 'Altro', 'sport_association': OWN},
    effect=created('foreign_owner', association=FOREIGN, title='Altro'))

# ------------------------------------------------------ modules/<id>/update
update = lambda label: f'/modules/{mid(label)}/update'
UPDATED = 'Camp or retreat updated successfully'


def updated(label, *, enabled, **values):
    return {'message': UPDATED, 'camp_and_retreat': serialized(STATE[label], enabled=enabled, **values)}


def error(text):
    return {'error': text}


def detail(field, message, code):
    return f"'{field}': [ErrorDetail(string={message!r}, code={code!r})]"


EDIT = mid('edit')
add('update-anonymous', 'anonymous', 'PATCH', update('edit'), 401, ANONYMOUS, body={})
add('update-admin-without-session', 'admin', 'PATCH', update('edit'), 403, NO_SESSION, body={})
add('update-restricted-missing-permission', 'restricted_collaborator', 'PATCH', update('edit'), 403,
    PERMISSION('association.modules.update'), body={})
add('update-owner-scoped-athlete', 'owner', 'PATCH', update('edit'), 403, SCOPED, body={}, headers=OWNER_ATHLETE)
add('update-wrong-method', 'owner', 'POST', update('edit'), 405, METHOD('POST'), body={})
add('update-put-method', 'owner', 'PUT', update('edit'), 405, METHOD('PUT'), body={})
add('update-options', 'owner', 'OPTIONS', update('edit'), 200, OPTIONS('Modules Update'))
add('update-malformed', 'owner', 'PATCH', update('abc'), 400, BAD_UUID, body={'title': 'x'})
add('update-athlete-malformed', 'athlete', 'PATCH', update('abc'), 400, BAD_UUID, body={'title': 'x'})
add('update-unknown', 'owner', 'PATCH', update(UNKNOWN), 404, error('Camp or retreat not found'), body={'title': 'x'})
add('update-athlete-unknown', 'athlete', 'PATCH', update(UNKNOWN), 404, error('Camp or retreat not found'), body={'title': 'x'})
add('update-foreign-module', 'owner', 'PATCH', update('foreign'), 403, {'detail': 'You are not allowed to update this camp or retreat'},
    body={'title': 'x'})
add('update-deleted-association-module', 'owner', 'PATCH', update('abroad'), 403,
    {'detail': 'You are not allowed to update this camp or retreat'}, body={'title': 'x'})
add('update-foreign-owner', 'foreign_owner', 'PATCH', update('edit'), 403, {'detail': 'You are not allowed to update this camp or retreat'},
    body={'title': 'x'})
ORPHAN = error("'NoneType' object has no attribute 'sport_association_id'")
add('update-orphan-module', 'owner', 'PATCH', update('orphan'), 500, ORPHAN, body={'title': 'x'})
add('update-athlete-orphan-module', 'athlete', 'PATCH', update('orphan'), 500, ORPHAN, body={'title': 'x'})
add('update-athlete-denied', 'athlete', 'PATCH', update('edit'), 403, NOT_ASSOCIATION, body={'title': 'x'})
NO_ASSOCIATION = error('SportAssociation matching query does not exist.')
add('update-owner-without-association', 'owner_without_association', 'PATCH', update('edit'), 500, NO_ASSOCIATION, body={'title': 'x'})
add('update-deleted-association-owner', 'deleted_association_owner', 'PATCH', update('abroad'), 500, NO_ASSOCIATION, body={'title': 'x'})
add('update-list-body', 'owner', 'PATCH', update('edit'), 400,
    error('{' + detail('non_field_errors', 'Invalid data. Expected a dictionary, but got list.', 'invalid') + '}'), body=[1])
add('update-invalid-fields', 'owner', 'PATCH', update('edit'), 400, error('{' + ', '.join([
    detail('module_id', 'Must be a valid UUID.', 'invalid'),
    detail('custom_link', 'module with this custom link already exists.', 'unique'),
    detail('creation_date', 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].',
           'invalid'),
    detail('title', 'Ensure this field has no more than 255 characters.', 'max_length'),
    detail('require_approval', 'Must be a valid boolean.', 'invalid'),
    detail('start_date', 'Date has wrong format. Use one of these formats instead: DD/MM/YYYY.', 'invalid'),
    detail('end_date', 'Date has wrong format. Use one of these formats instead: DD/MM/YYYY.', 'invalid'),
    detail('always_active', 'This field may not be null.', 'null'),
    detail('max_responses', 'Ensure this value is greater than or equal to 0.', 'min_value'),
    detail('queue_mode', 'Must be a valid boolean.', 'invalid'),
    detail('payment_data', 'This field may not be null.', 'null'),
    detail('response_message', 'Not a valid string.', 'invalid'),
    detail('elements', 'This field may not be null.', 'null')]) + '}'),
    body={'elements': None, 'response_message': True, 'payment_data': None, 'queue_mode': [], 'max_responses': -1, 'always_active': None,
          'end_date': 5, 'start_date': '2026-01-01', 'require_approval': 'forse', 'title': 'x' * 256, 'creation_date': 'ieri',
          'custom_link': 'occupato', 'module_id': 'nope'})
add('update-more-invalid-fields', 'owner', 'PATCH', update('edit'), 400, error('{' + ', '.join([
    detail('module_id', 'module with this module id already exists.', 'unique'),
    detail('custom_link', 'This field may not be blank.', 'blank'),
    detail('creation_date', 'This field may not be null.', 'null'),
    detail('title', 'This field may not be blank.', 'blank'),
    detail('max_responses', 'Ensure this value is less than or equal to 2147483647.', 'max_value'),
    detail('response_message', 'Ensure this field has no more than 1024 characters.', 'max_length')]) + '}'),
    body={'response_message': 'y' * 1025, 'max_responses': 2147483648, 'title': '', 'creation_date': None, 'custom_link': ' ',
          'module_id': mid('taken')})
add('update-integer-invalid', 'owner', 'PATCH', update('edit'), 400, error('{' + ', '.join([
    detail('title', 'Not a valid string.', 'invalid'), detail('max_responses', 'A valid integer is required.', 'invalid'),
    detail('allow_attachments', 'This field may not be null.', 'null')]) + '}'),
    body={'max_responses': 1.5, 'title': {'a': 1}, 'allow_attachments': None})
add('update-unique-link', 'owner', 'PATCH', update('edit'), 400,
    error('{' + detail('custom_link', 'module with this custom link already exists.', 'unique') + '}'),
    body={'custom_link': 'occupato', 'title': "l'ultimo"})
add('update-nested-association-string', 'owner', 'PATCH', update('edit'), 400,
    error("{'sport_association': {'non_field_errors': [ErrorDetail(string='Invalid data. Expected a dictionary, but got str.', "
          "code='invalid')]}}"), body={'sport_association': OWN})
add('update-nested-association-null', 'owner', 'PATCH', update('edit'), 400,
    error('{' + ', '.join([detail('sport_association', 'This field may not be null.', 'null'),
                           detail('title', 'Ensure this field has no more than 255 characters.', 'max_length')]) + '}'),
    body={'title': 'x' * 256, 'sport_association': None})
NESTED = ('The `.update()` method does not support writable nested fields by default.\nWrite an explicit `.update()` method for '
          'serializer `application.serializers.modules_serializers.ModulesSerializer`, or set `read_only=True` on nested serializer fields.')
add('update-nested-association-write', 'owner', 'PATCH', update('edit'), 500, error(NESTED), body={'title': 'Mai salvato', 'sport_association': {}})
add('update-owner', 'owner', 'PATCH', update('edit'), 200, None, body={
    'module_id': EDIT.upper(), 'custom_link': 'modifica', 'title': '  Titolo nuovo è ', 'require_approval': 'true',
    'start_date': '01/02/2026', 'end_date': '28/02/2999', 'always_active': False, 'max_responses': '30', 'queue_mode': 1,
    'payment_data': {'z': 1, 'a': ['x', None]}, 'response_message': 'Ciao', 'elements': [1, 'due'],
    'creation_date': '2026-03-01T10:00:00.5+02:00', 'enabled': False, 'sconosciuto': 'x'},
    effect=changed('edit', 'owner', title='Titolo nuovo è', require_approval=True, start='2026-02-01', end='2999-02-28',
                   max_responses=30, queue_mode=True, payment_data={'z': 1, 'a': ['x', None]}, response_message='Ciao',
                   elements=[1, 'due'], created='2026-03-01T08:00:00.500000Z'))
cases[-1]['response'] = updated('edit', enabled=True)
add('update-full-collaborator', 'full_collaborator', 'PATCH', update('edit'), 200, None,
    body={'title': 'Collaboratore', 'max_responses': None, 'start_date': None},
    effect=changed('edit', 'full_collaborator', title='Collaboratore', max_responses=None, start=None))
cases[-1]['response'] = updated('edit', enabled=False)
add('update-admin-impersonating-owner', 'admin', 'PATCH', update('edit'), 200, None, body={'always_active': 'yes'}, headers=ADMIN_OWNER,
    effect=changed('edit', 'admin', always_active=True))
cases[-1]['response'] = updated('edit', enabled=True)
add('update-empty-body-no-audit', 'owner', 'PATCH', update('edit'), 200, None, effect=changed('edit', 'owner'))
cases[-1]['response'] = updated('edit', enabled=True)
add('update-same-values-no-audit', 'owner', 'PATCH', update('edit'), 200, None,
    body={'title': 'Collaboratore', 'custom_link': 'modifica', 'payment_data': {'a': ['x', None], 'z': 1}, 'module_id': EDIT},
    effect=changed('edit', 'owner'))
cases[-1]['response'] = updated('edit', enabled=True)
add('update-uppercase-id', 'owner', 'PATCH', update(EDIT.upper()), 200, None, body={'custom_link': None},
    effect=changed('edit', 'owner', link=None))
cases[-1]['response'] = updated('edit', enabled=True)
# HTML form input: '' of a nullable field is None, JSON fields are json.loads() of the text.
add('update-form-body', 'owner', 'PATCH', update('edit'), 200, None,
    raw_body='title=Form+%C3%A8&max_responses=&payment_data=%7B%22k%22%3A+%5B1%2C+2%5D%7D&only_users=on&end_date=&elements=%22testo%22',
    content_type='application/x-www-form-urlencoded',
    effect=changed('edit', 'owner', title='Form è', payment_data={'k': [1, 2]}, only_users=True, end=None, elements='testo'))
cases[-1]['response'] = updated('edit', enabled=True)
add('update-form-invalid-json', 'owner', 'PATCH', update('edit'), 400,
    error('{' + detail('payment_data', 'Value must be valid JSON.', 'invalid') + '}'),
    raw_body='payment_data=%7Bx&title=Mai', content_type='application/x-www-form-urlencoded')
add('update-other-module-after', 'owner', 'PATCH', update('taken'), 200, None, body={'custom_link': 'Occupato', 'elements': {'b': {'c': []}}},
    effect=changed('taken', 'owner', link='Occupato', elements={'b': {'c': []}}))
cases[-1]['response'] = updated('taken', enabled=False)
# Keys the nested serializer has no field for: the validated value is the empty dict, which the update refuses.
add('update-nested-association-unknown-keys', 'owner', 'PATCH', update('edit'), 500, error(NESTED),
    body={'title': 'Mai salvato', 'sport_association': {'sconosciuto': 1, 'altro': [None]}})


# === nested sport_association dict with keys (SportAssociationSerializer validates every field present, in Meta.fields
# order, before ModelSerializer.update refuses the nested write; the module is never saved)
def nested_errors(*fields, outer=()):
    """str(ValidationError) of {'sport_association': {field: [ErrorDetail, ...]}} (+ outer module fields after it)."""
    inner = ', '.join(f"'{name}': [" + ', '.join(f'ErrorDetail(string={m!r}, code={c!r})' for m, c in details) + ']'
                      for name, details in fields)
    return error('{' + ', '.join([f"'sport_association': {{{inner}}}", *outer]) + '}')


def nested_case(name, nested, *fields, **kw):
    add('update-nested-' + name, 'owner', 'PATCH', update('edit'), 400, nested_errors(*fields, **kw),
        body={'title': 'Mai salvato', 'sport_association': nested})


def nested_valid(name, nested):
    add('update-nested-' + name, 'owner', 'PATCH', update('edit'), 500, error(NESTED),
        body={'title': 'Mai salvato', 'sport_association': nested})


MAX = lambda n: (f'Ensure this field has no more than {n} characters.', 'max_length')
BLANK, NULL, NOT_STRING, NOT_BOOL = (('This field may not be blank.', 'blank'), ('This field may not be null.', 'null'),
                                     ('Not a valid string.', 'invalid'), ('Must be a valid boolean.', 'invalid'))
UNIQUE_ID = ('sport association with this sport association id already exists.', 'unique')
CAP = ('Enter exactly 5 digits', 'invalid')
nested_valid('valid-fields', {'sport': 'Calcio', 'tax_code': '12345678901', 'reviewed': 'x', 'groups': 5, 'address_cap': '12345',
                              'sport_association_id': UNKNOWN, 'unknown': 1})
nested_case('text-errors', {'denomination': None, 'tax_code': 'x' * 12, 'address': '', 'address_cap': '123', 'address_city': 5,
                            'regulation': '', 'logo': True},
            ('denomination', [NULL]), ('tax_code', [MAX(11)]), ('address', [BLANK]), ('address_cap', [CAP]), ('regulation', [BLANK]),
            ('logo', [NOT_STRING]))
nested_case('number-boolean-errors', {'subscription_fee': 'abc', 'membership_fee': '1234567.123', 'multiple_subscription_fee': 'maybe',
                                      'multiple_membership_fee': None, 'enable_quotes_management': 'x'},
            ('subscription_fee', [('A valid number is required.', 'invalid')]), ('multiple_subscription_fee', [NOT_BOOL]),
            ('membership_fee', [('Ensure that there are no more than 9 digits in total.', 'max_digits')]),
            ('multiple_membership_fee', [NULL]), ('enable_quotes_management', [NOT_BOOL]))
nested_case('decimal-places', {'subscription_fee': 1.234, 'membership_fee': 12345678},
            ('subscription_fee', [('Ensure that there are no more than 2 decimal places.', 'max_decimal_places')]),
            ('membership_fee', [('Ensure that there are no more than 7 digits before the decimal point.', 'max_whole_digits')]))
nested_case('null-fee', {'subscription_fee': None, 'membership_fee': None}, ('subscription_fee', [NULL]))
nested_case('blank-fees', {'subscription_fee': '', 'membership_fee': '', 'multiple_subscription_fee': '',
                           'additional_sections': {'a': 1}, 'configuration': [1], 'subscription_fee_plans': 'x'},
            ('subscription_fee', [('A valid number is required.', 'invalid')]), ('multiple_subscription_fee', [NOT_BOOL]))
nested_valid('blank-nullable-fee', {'membership_fee': '  ', 'enabled_for': None, 'custom_subscription_form_data': None})
nested_case('json-nulls', {'subscription_fee_plans': None, 'membership_fee_plans': None, 'additional_sections': None, 'configuration': None,
                           'custom_subscription_form_data': None, 'enabled_for': None, 'additional_fields': None,
                           'stripe_available_methods': None},
            ('subscription_fee_plans', [NULL]), ('membership_fee_plans', [NULL]), ('configuration', [NULL]), ('additional_sections', [NULL]))
for name, value in (('own', OWN), ('foreign', FOREIGN), ('deleted', DELETED)):
    nested_case('primary-key-' + name, {'sport_association_id': value}, ('sport_association_id', [UNIQUE_ID]))
nested_case('primary-key-null', {'sport_association_id': None}, ('sport_association_id', [NULL]))
nested_case('primary-key-text', {'sport_association_id': 'x'}, ('sport_association_id', [('Must be a valid UUID.', 'invalid')]))
nested_case('primary-key-list', {'sport_association_id': [1]}, ('sport_association_id', [('Must be a valid UUID.', 'invalid')]))
nested_valid('primary-key-unused', {'sport_association_id': UNKNOWN.upper()})
nested_valid('primary-key-int', {'sport_association_id': 5})
nested_valid('primary-key-bool', {'sport_association_id': True})
nested_case('column-limits', {'affiliate_code': '123456789', 'iban': 'x' * 35, 'website': 'w' * 1025, 'abbreviated': 'a' * 101,
                              'vat_number': 'v' * 21, 'whatsapp': 'w' * 21, 'president_first_name': 'p' * 256, 'president_last_name': 7,
                              'federation': '', 'enroll_number': '', 'sport': '', 'invoice_template': '', 'subscription_template': None,
                              'extra_text_invoices': '', 'imported_from_associami': 'zz', 'show_regulation_to_members': 3},
            ('affiliate_code', [MAX(8)]), ('president_first_name', [MAX(255)]), ('iban', [MAX(34)]), ('website', [MAX(1024)]),
            ('abbreviated', [MAX(100)]), ('vat_number', [MAX(20)]), ('whatsapp', [MAX(20)]), ('extra_text_invoices', [BLANK]),
            ('imported_from_associami', [NOT_BOOL]), ('show_regulation_to_members', [NOT_BOOL]))
nested_case('long-and-blank', {'denomination': 'd' * 151, 'tax_code': '  ', 'address': 'a' * 151, 'address_city': 'c' * 151},
            ('denomination', [MAX(150)]), ('tax_code', [BLANK]), ('address', [MAX(150)]), ('address_city', [MAX(150)]))
nested_case('not-strings', {'sport': {'a': 1}, 'iban': ['x'], 'denomination': 5, 'tax_code': 1.5}, ('sport', [NOT_STRING]), ('iban', [NOT_STRING]))
nested_case('text-field-types', {'regulation': None, 'demand': 5, 'logo': 'ok', 'document_header': ['a'], 'invoice_footer': '',
                                 'checkout_info': False, 'president_signature': 1, 'stamp': None},
            ('document_header', [NOT_STRING]), ('invoice_footer', [BLANK]), ('checkout_info', [NOT_STRING]))
nested_case('null-characters', {'denomination': '\u0000', 'sport': 'a\u0000b'},
            ('denomination', [('Null characters are not allowed.', 'null_characters_not_allowed')]),
            ('sport', [('Null characters are not allowed.', 'null_characters_not_allowed')]))
for name, value in (('digits-unicode', '١٢٣٤٥'), ('digits-number', 12345), ('digits-trimmed', ' 12345 '), ('null', None)):
    nested_valid('address-cap-' + name, {'address_cap': value})
nested_case('address-cap-letters', {'address_cap': 'abcde'}, ('address_cap', [CAP]))
nested_case('address-cap-short', {'address_cap': '1234'}, ('address_cap', [CAP]))
nested_case('address-cap-long', {'address_cap': '123456'}, ('address_cap', [CAP, MAX(5)]))
nested_case('address-cap-blank', {'address_cap': ''}, ('address_cap', [BLANK]))
nested_case('address-cap-boolean', {'address_cap': True}, ('address_cap', [NOT_STRING]))
add('update-nested-with-outer-errors', 'owner', 'PATCH', update('edit'), 400,
    nested_errors(('sport', [MAX(100)]), outer=[detail('title', 'Ensure this field has no more than 255 characters.', 'max_length')]),
    body={'title': 'x' * 256, 'sport_association': {'sport': 's' * 101}})
add('update-nested-collaborator', 'full_collaborator', 'PATCH', update('edit'), 400, nested_errors(('sport', [MAX(100)])),
    body={'sport_association': {'sport': 's' * 101}})

# === nested association as HTML form input ('sport_association.<field>' keys, parse_html_dict): the nested serializer
# reads the last value of each key; '' is None for a nullable field ('' where blank is allowed), skipped for an optional
# one and kept for a required one; JSON fields are json.loads of the text.
FORM = 'application/x-www-form-urlencoded'


def form_nested_case(name, raw, *fields, outer=()):
    add('update-nested-form-' + name, 'owner', 'PATCH', update('edit'), 400, nested_errors(*fields, outer=outer), raw_body=raw, content_type=FORM)


def form_nested_valid(name, raw):
    add('update-nested-form-' + name, 'owner', 'PATCH', update('edit'), 500, error(NESTED), raw_body=raw, content_type=FORM)


form_nested_valid('valid', 'title=Mai&sport_association.sport=Calcio&sport_association.tax_code=12345678901')
form_nested_case('errors', 'sport_association.denomination=&sport_association.tax_code=xxxxxxxxxxxx&sport_association.address='
                 '&sport_association.address_cap=123&sport_association.subscription_fee=abc&sport_association.multiple_subscription_fee=maybe'
                 '&sport_association.configuration=%7Bx&sport_association.federation=&sport_association.enabled_for=',
                 ('denomination', [BLANK]), ('tax_code', [MAX(11)]), ('address_cap', [CAP]),
                 ('subscription_fee', [('A valid number is required.', 'invalid')]), ('multiple_subscription_fee', [NOT_BOOL]),
                 ('configuration', [('Value must be valid JSON.', 'invalid')]))
form_nested_valid('empty-values', 'sport_association.subscription_fee=&sport_association.multiple_subscription_fee='
                  '&sport_association.configuration=&sport_association.sport_association_id=&sport_association.membership_fee='
                  '&sport_association.address_cap=&sport_association.sport=')
form_nested_valid('unknown-keys', 'sport_association.zzz=1&title=Mai')
form_nested_valid('last-value-wins', 'sport_association.sport=' + 's' * 101 + '&sport_association.sport=ok')
form_nested_case('last-value-invalid', 'sport_association.sport=ok&sport_association.sport=' + 's' * 101, ('sport', [MAX(100)]))
form_nested_valid('json-null', 'sport_association.configuration=null&sport_association.enabled_for=%5B1%5D')
form_nested_case('json-text', 'sport_association.additional_fields=abc&sport_association.stripe_available_methods=%22ok%22',
                 ('additional_fields', [('Value must be valid JSON.', 'invalid')]))
form_nested_case('primary-key', 'sport_association.sport_association_id=' + OWN, ('sport_association_id', [UNIQUE_ID]))
form_nested_case('primary-key-text', 'sport_association.sport_association_id=x', ('sport_association_id', [('Must be a valid UUID.', 'invalid')]))
form_nested_case('with-outer-errors', 'title=' + 'x' * 256 + '&sport_association.sport=' + 's' * 101, ('sport', [MAX(100)]),
                 outer=[detail('title', 'Ensure this field has no more than 255 characters.', 'max_length')])
# A key with nothing after the prefix is no nested input: the update goes on.
add('update-nested-form-bare-prefix', 'owner', 'PATCH', update('edit'), 200, None,
    raw_body='title=Form+senza+annidato&sport_association.=x', content_type=FORM,
    effect=changed('edit', 'owner', title='Form senza annidato'))
cases[-1]['response'] = updated('edit', enabled=True)


def created_audit(row, ident):
    """auditlog's diff of a module against nothing: None-valued fields are kept only for the association and the JSON fields."""
    changes = {'module_id': ['None', ident]}
    for key, value in row.items():
        if key == 'id':
            continue
        name = 'sport_association' if key == 'association' else FIELD_NAMES.get(key, key)
        if value is None and key != 'association':
            continue
        changes[name] = ['None', audit_text(key, value)]
    return changes


# A module_id no row has: Model.save() inserts a copy with the validated values and the module of the path stays.
# auditlog logs the row twice under the new key: the update of its pre_save (indexed from its changes), then the create.
assert STATE['edit']['link'] is None and STATE['taken']['link'] == 'Occupato'
COPY = 'a1000000-0000-4000-8000-000000642901'
COPIED = {**STATE['edit'], 'id': COPY, 'title': 'Copia con nuova chiave', 'queue_mode': False}
add('update-new-key-copies', 'owner', 'PATCH', update('edit'), 200,
    {'message': UPDATED, 'camp_and_retreat': serialized(COPIED, enabled=True)},
    body={'module_id': COPY.upper(), 'title': 'Copia con nuova chiave', 'queue_mode': False},
    effect={'kind': 'copy', 'actor': 'owner', 'fixed_id': COPY, 'stored': stored(COPIED), 'created': COPIED['created']})
add('update-new-key-source-unchanged', 'owner', 'PATCH', update('edit'), 200, None, effect=changed('edit', 'owner'))
cases[-1]['response'] = updated('edit', enabled=True)
# The copy repeats the custom link: the database refuses the insert, the view answers its text and the first entry stays.
REFUSED = 'a1000000-0000-4000-8000-000000642902'
add('update-new-key-link-taken', 'full_collaborator', 'PATCH', update('taken'), 500,
    error('duplicate key value violates unique constraint "application_module_custom_link_key"\n'
          'DETAIL:  Key (custom_link)=(Occupato) already exists.\n'),
    body={'module_id': REFUSED, 'title': 'Mai copiato'},
    effect={'kind': 'copy-refused', 'actor': 'full_collaborator', 'fixed_id': REFUSED, 'association': OWN,
            'changes': created_audit({**STATE['taken'], 'title': 'Mai copiato'}, REFUSED)})

# --------------------------------------------------- modules/<id>/duplicate
duplicate = lambda label: f'/modules/{mid(label)}/duplicate'
UNMAPPED = lambda label: {'detail': f"Access denied: No permission mapping for endpoint 'modules/{mid(label)}/duplicate'. "
                                    'Contact administrator to grant access.'}
NOT_FOUND = {'error': 'module not found'}


def cloned(label, actor, *, enabled):
    row = STATE[label]
    title = f"{row['title']} (copia)"
    return ({'data': serialized(row, ident='$new', link='$new', created='$now', title=title, enabled=enabled)},
            {'kind': 'create', 'actor': actor, 'fixed_id': None, 'created': '$now', 'random_link': True,
             'stored': {**stored(row), 'title': title}})


add('duplicate-anonymous', 'anonymous', 'POST', duplicate('source'), 401, ANONYMOUS)
add('duplicate-admin-without-session', 'admin', 'POST', duplicate('source'), 403, NO_SESSION)
add('duplicate-restricted-unmapped', 'restricted_collaborator', 'POST', duplicate('source'), 403, UNMAPPED('source'))
add('duplicate-owner-scoped-athlete', 'owner', 'POST', duplicate('source'), 403, SCOPED, headers=OWNER_ATHLETE)
add('duplicate-wrong-method', 'owner', 'GET', duplicate('source'), 405, METHOD('GET'))
add('duplicate-options', 'owner', 'OPTIONS', duplicate('source'), 200, OPTIONS('Modules Duplicate'))
add('duplicate-malformed', 'owner', 'POST', duplicate('abc'), 400, BAD_UUID)
add('duplicate-athlete-malformed', 'athlete', 'POST', duplicate('abc'), 400, BAD_UUID)
add('duplicate-athlete-denied', 'athlete', 'POST', duplicate('source'), 403, NOT_ASSOCIATION)
add('duplicate-owner-without-association', 'owner_without_association', 'POST', duplicate('source'), 500, SERVER)
add('duplicate-deleted-association-owner', 'deleted_association_owner', 'POST', duplicate('abroad'), 500, SERVER)
add('duplicate-foreign-module', 'owner', 'POST', duplicate('foreign'), 404, NOT_FOUND)
add('duplicate-foreign-owner', 'foreign_owner', 'POST', duplicate('source'), 404, NOT_FOUND)
add('duplicate-deleted-association-module', 'owner', 'POST', duplicate('abroad'), 404, NOT_FOUND)
add('duplicate-orphan-module', 'owner', 'POST', duplicate('orphan'), 404, NOT_FOUND)
add('duplicate-unknown', 'owner', 'POST', duplicate(UNKNOWN), 404, NOT_FOUND)
response, effect = cloned('source', 'owner', enabled=True)
add('duplicate-owner', 'owner', 'POST', duplicate('source'), 201, response, body={'title': 'ignorato'}, effect=effect)
response, effect = cloned('untitled', 'full_collaborator', enabled=True)
add('duplicate-null-title-full-collaborator', 'full_collaborator', 'POST', duplicate('untitled'), 201, response, effect=effect)
response, effect = cloned('edit', 'admin', enabled=True)
add('duplicate-admin-impersonating-hex-id', 'admin', 'POST', duplicate(EDIT.replace('-', '')), 201, response, headers=ADMIN_OWNER,
    effect=effect)

# ------------------------------------------------------ modules/<id>/delete
delete = lambda label: f'/modules/{mid(label)}/delete'
DELETED_MSG = {'msg': 'module deleted'}


def removed(label, actor):
    return {'kind': 'delete', 'actor': actor, 'label': label,
            'responses': [key for key, row in RESPONSES.items() if row['module'] == label]}


add('delete-anonymous', 'anonymous', 'DELETE', delete('remove'), 401, ANONYMOUS)
add('delete-admin-without-session', 'admin', 'DELETE', delete('remove'), 403, NO_SESSION)
add('delete-restricted-missing-permission', 'restricted_collaborator', 'DELETE', delete('remove'), 403,
    PERMISSION('association.modules.delete'))
add('delete-owner-scoped-athlete', 'owner', 'DELETE', delete('remove'), 403, SCOPED, headers=OWNER_ATHLETE)
add('delete-wrong-method', 'owner', 'POST', delete('remove'), 405, METHOD('POST'))
add('delete-options', 'owner', 'OPTIONS', delete('remove'), 200, OPTIONS('Modules Delete'))
# No is_valid_uuid: the UUIDField lookup raises a ValidationError DRF does not handle.
add('delete-malformed-server-error', 'owner', 'DELETE', delete('abc'), 500, SERVER)
# request.user.sport_association is evaluated before the lookup.
add('delete-athlete-malformed-denied', 'athlete', 'DELETE', delete('abc'), 403, NOT_ASSOCIATION)
add('delete-athlete-denied', 'athlete', 'DELETE', delete('remove'), 403, NOT_ASSOCIATION)
add('delete-owner-without-association', 'owner_without_association', 'DELETE', delete('remove'), 500, SERVER)
add('delete-deleted-association-owner', 'deleted_association_owner', 'DELETE', delete('abroad'), 500, SERVER)
add('delete-foreign-module', 'owner', 'DELETE', delete('foreign'), 404, NOT_FOUND)
add('delete-foreign-owner', 'foreign_owner', 'DELETE', delete('remove'), 404, NOT_FOUND)
add('delete-deleted-association-module', 'owner', 'DELETE', delete('abroad'), 404, NOT_FOUND)
add('delete-orphan-module', 'owner', 'DELETE', delete('orphan'), 404, NOT_FOUND)
add('delete-unknown', 'owner', 'DELETE', delete(UNKNOWN), 404, NOT_FOUND)
add('delete-owner-with-responses', 'owner', 'DELETE', delete(mid('remove').upper()), 200, DELETED_MSG, effect=removed('remove', 'owner'))
add('delete-repeat', 'owner', 'DELETE', delete('remove'), 404, NOT_FOUND)
add('delete-full-collaborator', 'full_collaborator', 'DELETE', delete('remove_collab'), 200, DELETED_MSG,
    effect=removed('remove_collab', 'full_collaborator'))
add('delete-admin-impersonating-owner', 'admin', 'DELETE', delete('remove_plain'), 200, DELETED_MSG, headers=ADMIN_OWNER,
    effect=removed('remove_plain', 'admin'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Modules add/update/duplicate/delete authored sequence with audit effects',
                              'ids': IDS, 'modules': MODULES, 'documents': DOCUMENTS, 'responses': RESPONSES, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
