"""Author the DefaultRouter templates HTTP sequence (router-stock.md).

`router.register(r'templates', SportAssociationModuleTemplatesViewSet)`: the
list route binds GET to the viewset's list() (a bare list of the owner's
templates) and POST to its own create(), which writes the owner's
association into request.data before validation (a list body or an
immutable form QueryDict: 500); the detail route binds GET to the stock
retrieve() (get_object() over the owner's templates), PUT to the viewset's
update() (partial, so PUT validates only the fields sent), PATCH to
partial_update() (update() rejects partial=: 500) and DELETE to the stock
destroy(). DELETE on the list route is the viewset's delete() without a pk
(500). On the format routes create(), list() and update() reject format=
(500); retrieve() and destroy() accept it. No permission-registry key
(restricted collaborators: 'No permission mapping'). Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_templates_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
row_ids = iter(f'a1000000-0000-4000-8000-{990101 + n:012d}' for n in range(50))
FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')
SERVER = '<stable-server-error-page>'


def template(association, name, *, body='<p>Corpo</p>', footer=True, header=True, custom=False, custom_footer=None, custom_header=None):
    return {'id': next(row_ids), 'association': association, 'name': name, 'template': body, 'footer': footer,
            'header': header, 'custom_footer_header': custom, 'custom_footer': custom_footer, 'custom_header': custom_header}


SEED = {
    'keep': template(OWN, 'Modulo router à', custom=True, custom_footer='<b>piede</b>', custom_header='<b>testa</b>'),
    'edit': template(OWN, 'Da modificare', footer=False, custom_footer='f'),
    'remove': template(OWN, 'Da eliminare', body=None),
    'foreign': template(FOREIGN, 'Estero'),
}
STATE = copy.deepcopy(SEED)
cases = []


def serialized(row, ident=None):
    return {'sport_association_module_templates_id': ident or row['id'], 'sport_association': row['association'],
            **{field: row[field] for field in FIELDS}}


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'router-templates-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def created(actor, values, ident='$new'):
    row = {'association': OWN, **{field: None for field in FIELDS}, 'footer': True, 'header': True, 'custom_footer_header': False, **values}
    return serialized(row, ident), {'kind': 'add', 'actor': actor, 'id': ident, 'stored': {f: row[f] for f in FIELDS}}


def changed(actor, label, **values):
    row = STATE[label]
    changes = {key: [str(row[key]), str(value)] for key, value in values.items() if row[key] != value}
    row.update(values)
    return serialized(row), {'kind': 'update', 'actor': actor, 'id': row['id'], 'changes': changes, 'stored': {f: row[f] for f in FIELDS}}


LIST = '/templates/'
detail = lambda label, suffix='/': f'/templates/{SEED[label]["id"] if label in SEED else label}{suffix}'
NOT_FOUND = {'detail': 'No SportAssociationModuleTemplates matches the given query.'}
BARE = {'detail': 'Not found.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
method = lambda name: {'detail': f'Method "{name}" not allowed.'}
text = lambda label: {'type': 'string', 'required': False, 'read_only': False, 'label': label}
flag = lambda label: {'type': 'boolean', 'required': False, 'read_only': False, 'label': label}
SERIALIZER = {'sport_association_module_templates_id': text('Sport association module templates id'),
              'name': {'type': 'string', 'required': True, 'read_only': False, 'label': 'Name', 'max_length': 150},
              'template': text('Template'), 'footer': flag('Footer'), 'header': flag('Header'),
              'custom_footer_header': flag('Custom footer header'), 'custom_footer': text('Custom footer'),
              'custom_header': text('Custom header'),
              'sport_association': {'type': 'field', 'required': True, 'read_only': False, 'label': 'Sport association'}}
OPTIONS = lambda suffix, actions=None: {'name': f'Sport Association Module Templates {suffix}', 'description': '',
                                        'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
                                        **({'actions': actions} if actions else {})}
OWN_ROWS = [serialized(SEED[k]) for k in ('keep', 'edit', 'remove')]

add('list-anonymous', 'anonymous', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('templates'))
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED)
add('list-owner', 'owner', LIST, 200, OWN_ROWS)
add('list-full-collaborator', 'full_collaborator', LIST, 200, OWN_ROWS)
add('list-foreign-owner', 'foreign_owner', LIST, 200, [serialized(SEED['foreign'])])
add('list-athlete-server-error', 'athlete', LIST, 500, SERVER)
add('list-put-not-allowed', 'owner', LIST, 405, method('PUT'), method='PUT', body={})
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-options', 'owner', LIST, 200, OPTIONS('List', {'POST': SERIALIZER}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/templates.json', 500, SERVER)
add('list-format-unknown-not-found', 'anonymous', '/templates.csv/', 404, BARE)
add('create-format-json-server-error', 'owner', '/templates.json', 500, SERVER, method='POST', body={'name': 'Mai'})
add('create-missing-name', 'owner', LIST, 400, {'name': ['This field is required.']}, method='POST', body={'template': 'x'})
add('create-invalid-fields', 'owner', LIST, 400,
    {'name': ['Ensure this field has no more than 150 characters.'], 'footer': ['Must be a valid boolean.'],
     'sport_association_module_templates_id': ['Must be a valid UUID.']}, method='POST',
    body={'name': 'x' * 151, 'footer': 'maybe', 'sport_association_module_templates_id': 'nope'})
add('create-list-body-server-error', 'owner', LIST, 500, SERVER, method='POST', body=[])
add('create-form-body-server-error', 'owner', LIST, 500, SERVER, method='POST', raw_body='name=x',
    content_type='application/x-www-form-urlencoded')
add('create-athlete-server-error', 'athlete', LIST, 500, SERVER, method='POST', body={'name': 'x'})
response, effect = created('owner', {'name': 'Nuovo router', 'template': '<p>x</p>', 'footer': False})
add('create-owner', 'owner', LIST, 201, response, method='POST',
    body={'name': ' Nuovo router ', 'template': '<p>x</p>', 'footer': 'no', 'sport_association': FOREIGN}, effect=effect)
FIXED = 'a1000000-0000-4000-8000-000000990199'
response, effect = created('full_collaborator', {'name': '7', 'custom_header': 'h'}, FIXED)
add('create-client-id', 'full_collaborator', LIST, 201, response, method='POST',
    body={'sport_association_module_templates_id': FIXED, 'name': 7, 'custom_header': 'h'}, effect=effect)

add('detail-anonymous', 'anonymous', detail('keep'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', detail('keep'), 403, unmapped('templates/' + SEED['keep']['id']))
add('retrieve-owner', 'owner', detail('keep'), 200, serialized(SEED['keep']))
add('retrieve-format-json', 'full_collaborator', detail('remove', '.json'), 200, serialized(SEED['remove']))
add('retrieve-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND)
add('retrieve-missing-not-found', 'owner', detail(IDS['missing']), 404, NOT_FOUND)
add('retrieve-malformed-not-found', 'owner', detail('nope'), 404, BARE)
add('retrieve-athlete-server-error', 'athlete', detail('keep'), 500, SERVER)
add('detail-post-not-allowed', 'owner', detail('keep'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('keep'), 200, OPTIONS('Instance', {'PUT': SERIALIZER}), method='OPTIONS')
add('detail-options-foreign-no-actions', 'owner', detail('foreign'), 200, OPTIONS('Instance'), method='OPTIONS')
add('partial-update-server-error', 'owner', detail('edit'), 500, SERVER, method='PATCH', body={'name': 'Mai'})
add('update-format-json-server-error', 'owner', detail('edit', '.json/'), 500, SERVER, method='PUT', body={'name': 'Mai'})
add('update-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND, method='PUT', body={'name': 'x'})
add('update-blank-name', 'owner', detail('edit'), 400, {'name': ['This field may not be blank.']}, method='PUT', body={'name': ' '})
add('update-foreign-association-denied', 'owner', detail('edit'), 403, {'detail': 'You do not have permission to perform this action.'},
    method='PUT', body={'sport_association': FOREIGN})
response, effect = changed('owner', 'edit', name='Modificato router', footer=True, custom_footer=None)
add('update-owner-put-is-partial', 'owner', detail('edit'), 200, response, method='PUT',
    body={'name': 'Modificato router', 'footer': True, 'custom_footer': None}, effect=effect)
response, effect = changed('full_collaborator', 'edit')
add('update-unchanged-no-audit', 'full_collaborator', detail('edit'), 200, response, method='PUT', body={'name': 'Modificato router'},
    effect=effect)
add('destroy-foreign-not-found', 'owner', detail('foreign'), 404, NOT_FOUND, method='DELETE')
add('destroy-malformed-not-found', 'owner', detail('nope'), 404, BARE, method='DELETE')
add('destroy-athlete-server-error', 'athlete', detail('remove'), 500, SERVER, method='DELETE')
add('destroy-owner', 'owner', detail('remove'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'id': SEED['remove']['id'], 'stored': {}})
add('destroy-again-not-found', 'owner', detail('remove'), 404, NOT_FOUND, method='DELETE')
add('destroy-format-json', 'full_collaborator', detail('edit', '.json'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'id': SEED['edit']['id'], 'stored': {}})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'DefaultRouter templates routes', 'ids': IDS, 'seed': SEED, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
