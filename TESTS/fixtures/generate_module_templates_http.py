"""Author the module-template list/add/delete/bulk-delete HTTP sequence.

Expectations come from docs/architecture/go-migration/module-templates.md:
DRF 3.17 ModelSerializer(fields='__all__') rules for SportAssociationModuleTemplates,
auditlog create/delete entries (None values omitted, naive UTC timestamps not
involved), direct index path for creates and changes_json after deletes.
``$new:<label>`` binds generated IDs. List results are unordered (no
ORDER BY). Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('module_templates_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
row_ids = iter(f'a1000000-0000-4000-8000-{770001 + n:012d}' for n in range(50))
FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')


def template(association, name, *, template='<p>Corpo</p>', footer=True, header=True, custom=False, custom_footer=None, custom_header=None):
    return {'id': next(row_ids), 'association': association, 'name': name, 'template': template, 'footer': footer,
            'header': header, 'custom_footer_header': custom, 'custom_footer': custom_footer, 'custom_header': custom_header}


SEED = {
    'own_keep': template(OWN, 'Modulo iscrizione à', custom=True, custom_footer='<b>piede</b>', custom_header='<b>testa</b>'),
    'own_delete': template(OWN, 'Da eliminare', footer=False, custom_footer='f', custom_header='h'),
    'own_bulk_nulls': template(OWN, 'Bulk nulli', template=None),
    'own_bulk': template(OWN, 'Bulk', header=False),
    'foreign': template(FOREIGN, 'Estero'),
}


def serialized(row, ident=None):
    return {'sport_association_module_templates_id': ident or row['id'], 'sport_association': row['association'],
            **{field: row[field] for field in FIELDS}}


def audit_values(row):
    values = {'sport_association_module_templates_id': row['id'], 'sport_association': row['association']}
    for field in FIELDS:
        if row[field] is not None:
            values[field] = str(row[field]) if not isinstance(row[field], str) else row[field]
    return values


def deleted(label):
    return {'id': SEED[label]['id'], 'changes': {k: [v, 'None'] for k, v in audit_values(SEED[label]).items()}}


cases = []
unmapped_detail = lambda permission: {'detail': f'Missing permission: {permission}'}
SERVER = '<stable-server-error-page>'
LIST, ADD, BULK = '/templates/list', '/templates/add', '/templates/bulk-delete'
delete_path = lambda value: f'/templates/{SEED[value]["id"] if value in SEED else value}/delete'


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, effect=None, headers=None, unordered=False):
    case = {'name': f'module-templates-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'unordered': unordered, 'effect': effect or {'kind': 'none'}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


own_rows = [serialized(SEED[k]) for k in ('own_keep', 'own_delete', 'own_bulk_nulls', 'own_bulk')]
add('list-owner', 'owner', 'GET', LIST, 200, own_rows, unordered=True)
add('list-full-collaborator', 'full_collaborator', 'GET', LIST, 200, own_rows, unordered=True)
add('list-admin-impersonating-owner', 'admin', 'GET', LIST, 200, own_rows, unordered=True,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-foreign-owner', 'foreign_owner', 'GET', LIST, 200, [serialized(SEED['foreign'])])
add('list-restricted-missing-permission', 'restricted_collaborator', 'GET', LIST, 403, unmapped_detail('association.templates.read'))
add('list-athlete-server-error', 'athlete', 'GET', LIST, 500, SERVER)
add('list-deleted-association-server-error', 'deleted_association_owner', 'GET', LIST, 500, SERVER)
add('list-admin-without-session', 'admin', 'GET', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-anonymous', 'anonymous', 'GET', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-wrong-method', 'owner', 'POST', LIST, 405, {'detail': 'Method "POST" not allowed.'}, body={})
# DELETE dispatches to the viewset's delete() without a pk: get_object asserts.
add('list-delete-method-server-error', 'owner', 'DELETE', LIST, 500, SERVER)


def created(label, actor, values, *, fixed_id=None):
    row = {'association': OWN, **{field: None for field in FIELDS}, 'footer': True, 'header': True, 'custom_footer_header': False, **values}
    audit = {k: v for k, v in audit_values({'id': '$new', **row}).items()}
    return {'kind': 'create', 'label': label, 'fixed_id': fixed_id, 'row': row,
            'audit': {'action': 0, 'actor': actor, 'changes': {k: ['None', v] for k, v in audit.items()}}, 'index_path': 'direct'}


def response_for(label, values, ident=None):
    row = {'association': OWN, **{field: None for field in FIELDS}, 'footer': True, 'header': True, 'custom_footer_header': False, **values}
    return serialized({'id': None, **row}, ident or '$new:' + label)


body = {'name': '  Nuovo modulo è  ', 'template': ' <p>x</p> ', 'footer': 'no', 'custom_footer_header': 1,
        'sport_association': FOREIGN}
values = {'name': 'Nuovo modulo è', 'template': '<p>x</p>', 'footer': False, 'custom_footer_header': True}
add('add-owner', 'owner', 'POST', ADD, 201, response_for('owner', values), body=body, effect=created('owner', 'owner', values))
CLIENT = 'a1000000-0000-4000-8000-000000770099'
add('add-client-id-number-name', 'full_collaborator', 'POST', ADD, 201, response_for('client', {'name': '12.5', 'custom_header': 'h'}, CLIENT),
    body={'sport_association_module_templates_id': CLIENT, 'name': 12.5, 'custom_header': 'h'},
    effect=created('client', 'full_collaborator', {'name': '12.5', 'custom_header': 'h'}, fixed_id=CLIENT))
add('add-duplicate-id', 'owner', 'POST', ADD, 400,
    {'sport_association_module_templates_id': ['sport association module templates with this sport association module templates id already exists.']},
    body={'sport_association_module_templates_id': CLIENT, 'name': 'Doppio'})
add('add-invalid-fields', 'owner', 'POST', ADD, 400,
    {'name': ['Ensure this field has no more than 150 characters.'], 'template': ['This field may not be blank.'],
     'footer': ['Must be a valid boolean.'], 'header': ['This field may not be null.'], 'custom_footer': ['Not a valid string.'],
     'sport_association_module_templates_id': ['Must be a valid UUID.']},
    body={'name': 'x' * 151, 'template': '   ', 'footer': 'maybe', 'header': None, 'custom_footer': True,
          'sport_association_module_templates_id': 'nope'})
add('add-missing-name', 'owner', 'POST', ADD, 400, {'name': ['This field is required.']}, body={'template': 'x'})
add('add-blank-name', 'owner', 'POST', ADD, 400, {'name': ['This field may not be blank.']}, body={'name': ' '})
add('add-list-body-server-error', 'owner', 'POST', ADD, 500, SERVER, body=[])
add('add-form-body-server-error', 'owner', 'POST', ADD, 500, SERVER, raw_body='name=x', content_type='application/x-www-form-urlencoded')
add('add-restricted-missing-permission', 'restricted_collaborator', 'POST', ADD, 403, unmapped_detail('association.templates.create'), body={'name': 'x'})
add('add-athlete-server-error', 'athlete', 'POST', ADD, 500, SERVER, body={'name': 'x'})
add('add-anonymous', 'anonymous', 'POST', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={'name': 'x'})
add('add-wrong-method', 'owner', 'GET', ADD, 405, {'detail': 'Method "GET" not allowed.'})
add('add-delete-method-server-error', 'owner', 'DELETE', ADD, 500, SERVER)
label = lambda text: {'type': 'string', 'required': False, 'read_only': False, 'label': text}
flag = lambda text: {'type': 'boolean', 'required': False, 'read_only': False, 'label': text}
add('add-options', 'owner', 'OPTIONS', ADD, 200, {'name': 'Sport Association Module Templates', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
    'actions': {'POST': {'sport_association_module_templates_id': label('Sport association module templates id'),
                         'name': {'type': 'string', 'required': True, 'read_only': False, 'label': 'Name', 'max_length': 150},
                         'template': label('Template'), 'footer': flag('Footer'), 'header': flag('Header'),
                         'custom_footer_header': flag('Custom footer header'), 'custom_footer': label('Custom footer'),
                         'custom_header': label('Custom header'),
                         'sport_association': {'type': 'field', 'required': True, 'read_only': False, 'label': 'Sport association'}}}})
NOT_FOUND = {'detail': 'No SportAssociationModuleTemplates matches the given query.'}
add('delete-malformed', 'owner', 'DELETE', delete_path('not-a-uuid'), 404, {'detail': 'Not found.'})
add('delete-foreign', 'owner', 'DELETE', delete_path('foreign'), 404, NOT_FOUND)
add('delete-missing', 'owner', 'DELETE', delete_path(IDS['missing']), 404, NOT_FOUND)
add('delete-restricted-missing-permission', 'restricted_collaborator', 'DELETE', delete_path('own_delete'), 403,
    unmapped_detail('association.templates.delete'))
add('delete-owner', 'owner', 'DELETE', delete_path('own_delete'), 204, '<empty>',
    effect={'kind': 'delete', 'audits': [deleted('own_delete')], 'actor': 'owner'})
add('delete-repeat', 'owner', 'DELETE', delete_path('own_delete'), 404, NOT_FOUND)
add('delete-options', 'owner', 'OPTIONS', delete_path('own_keep'), 200, {'name': 'Sport Association Module Templates', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
# DRF keeps a string inside a dict detail unwrapped (no list).
EMPTY = {'exception': 'No instances provided'}
add('bulk-missing-key', 'owner', 'DELETE', BULK, 400, EMPTY, body={})
add('bulk-empty-list', 'owner', 'DELETE', BULK, 400, EMPTY, body={'sport_association_module_templates_ids': []})
add('bulk-list-body-server-error', 'owner', 'DELETE', BULK, 500, SERVER, body=[])
add('bulk-invalid-id-server-error', 'owner', 'DELETE', BULK, 500, SERVER, body={'sport_association_module_templates_ids': ['nope']})
add('bulk-number-server-error', 'owner', 'DELETE', BULK, 500, SERVER, body={'sport_association_module_templates_ids': 5})
add('bulk-unmatched', 'owner', 'DELETE', BULK, 204, '<empty>',
    body={'sport_association_module_templates_ids': [SEED['foreign']['id'], IDS['missing'], 7]})
add('bulk-restricted-missing-permission', 'restricted_collaborator', 'DELETE', BULK, 403, unmapped_detail('association.templates.delete'),
    body={'sport_association_module_templates_ids': [SEED['own_bulk']['id']]})
add('bulk-owner', 'owner', 'DELETE', BULK, 204, '<empty>',
    body={'sport_association_module_templates_ids': [SEED['own_bulk']['id'], SEED['foreign']['id'], SEED['own_bulk_nulls']['id'], SEED['own_bulk']['id']]},
    effect={'kind': 'delete', 'audits': [deleted('own_bulk'), deleted('own_bulk_nulls')], 'actor': 'owner'})
add('list-after-writes', 'owner', 'GET', LIST, 200, [serialized(SEED['own_keep']), response_for('owner', values, '$new:owner'),
    response_for('client', {'name': '12.5', 'custom_header': 'h'}, CLIENT)], unordered=True)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Module template list/add/delete/bulk-delete authored sequence with audit effects',
                              'ids': IDS, 'seed': SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
