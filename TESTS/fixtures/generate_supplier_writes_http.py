"""Author the supplier/add and supplier/<uid>/update HTTP sequence (suppliers.md).

Builds on the supplier-list seed. SupplierSerializer is a ModelSerializer
over every field; its related fields are tenant scoped (a foreign
association or group is DRF's generic 403). supplier_add refuses athletes
with a bare 403, needs the owner's association (500 when missing), answers
400 with the repr of the serializer errors, creates through the
group-aware manager (the raw X-Group-Id becomes the group), saves the
association and returns 201 with the serialized supplier. supplier_update
validates the uid (400), gets the first live, group-aware supplier (none or
one without association is a 500), refuses another association's supplier
(403 'User not allowed.'), answers 400 'validation errors.' and returns
200 with the serialized supplier; auditlog records changed fields only.
Run with ``python3 -I`` after the supplier-list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('supplier_writes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('suppliers_http.json').read_text()))
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SUPPLIERS = SOURCE['suppliers']
UNKNOWN = 'a1000000-0000-4000-8000-000000929999'
SERVER = '<stable-server-error-page>'
FIELDS = ('name', 'address', 'tax_code', 'vat_number', 'type', 'email', 'phone_number', 'city', 'cap', 'state', 'country', 'nationality', 'note')
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'supplier-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def item(values, *, supplier_id='$new', association=OWN, group=None, deleted=False):
    row = {'supplier_id': supplier_id, 'name': None, 'address': None, 'tax_code': None, 'sport_association': association, 'vat_number': None,
           'type': 'supplier', 'email': None, 'phone_number': None, 'city': None, 'cap': None, 'state': None, 'country': 'Italia',
           'nationality': 'Italiana', 'note': None, 'group': group, 'deleted': deleted}
    row.update(values)
    return row


def existing(label, **changes):
    row = SUPPLIERS[label]
    return item({**{k: row[k] for k in FIELDS}, **changes}, supplier_id=row['id'], association=row['association'],
                group=IDS[row['group']] if row['group'] else None, deleted=row['deleted'])


def error(field, message, code):
    return {'field': field, 'message': message, 'code': code}


repr_errors = lambda *items: 'validation errors: {' + ', '.join(
    f"'{i['field']}': [ErrorDetail(string='{i['message']}', code='{i['code']}')]" for i in items) + '}'
ADD, update = '/supplier/add', lambda label: f'/supplier/{SUPPLIERS[label]["id"] if label in SUPPLIERS else label}/update'
TENANT = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
created = lambda actor, values, group=None: {'kind': 'add', 'actor': actor, 'values': values, 'group': group}
changed = lambda actor, label, changes: {'kind': 'update', 'actor': actor, 'supplier': label, 'changes': changes}

add('add-athlete-forbidden', 'athlete', ADD, 403, '<empty>', body={'name': 'Atleta'})
add('add-missing-name', 'owner', ADD, 400, {'msg': repr_errors(error('name', 'This field is required.', 'required'))}, body={'city': 'Roma'})
add('add-invalid-fields', 'owner', ADD, 400, {'msg': repr_errors(error('name', 'Ensure this field has no more than 255 characters.', 'max_length'),
                                                                   error('email', 'Enter a valid email address.', 'invalid'))},
    body={'name': 'x' * 256, 'email': 'not-an-email'})
add('add-existing-id', 'owner', ADD, 400, {'msg': repr_errors(error('supplier_id', 'supplier and customers with this supplier id already exists.', 'unique'))},
    body={'name': 'Copia', 'supplier_id': SUPPLIERS['acqua']['id']})
add('add-list-body', 'owner', ADD, 400, {'msg': repr_errors(error('non_field_errors', 'Invalid data. Expected a dictionary, but got list.', 'invalid'))},
    body=[])
add('add-foreign-association-denied', 'owner', ADD, 403, TENANT, body={'name': 'Estero', 'sport_association': FOREIGN})
add('add-foreign-group-denied', 'owner', ADD, 403, TENANT, body={'name': 'Gruppo estero', 'group': IDS['foreign_group']})
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={'name': 'X'}, headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, {'detail': 'Missing permission: bookeeping.management.suppliers.create'},
    body={'name': 'X'})
add('add-admin-without-session', 'admin', ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('add-anonymous', 'anonymous', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, {'name': 'Supplier Add', 'description': 'API endpoint to add a new supplier', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
MINIMAL = {'name': 'Nuovo è'}
add('add-minimal', 'owner', ADD, 201, item(MINIMAL), body=MINIMAL, effect=created('owner', item(MINIMAL)))
FULL = {'name': 'Completo', 'address': 'Via 2', 'tax_code': 'TAX', 'vat_number': 'IT9', 'type': 'customer', 'email': 'c@example.test',
        'phone_number': '+39 2', 'city': 'Roma', 'cap': '00100', 'state': 'RM', 'country': 'Italia', 'nationality': 'Italiana', 'note': 'Nota',
        'sport_association': OWN, 'group': IDS['own_group'], 'deleted': True}
add('add-full-own-references', 'owner', ADD, 201, item({k: v for k, v in FULL.items() if k not in ('sport_association', 'group', 'deleted')},
    group=IDS['own_group'], deleted=True), body=FULL,
    effect=created('owner', item({k: v for k, v in FULL.items() if k not in ('sport_association', 'group', 'deleted')}, group=IDS['own_group'], deleted=True)))
add('add-group-header', 'owner', ADD, 201, item({'name': 'Con gruppo'}, group=IDS['own_group']), body={'name': 'Con gruppo'},
    headers={'X-Group-Id': IDS['own_group']}, effect=created('owner', item({'name': 'Con gruppo'}, group=IDS['own_group'])))
add('add-full-collaborator-form', 'full_collaborator', ADD, 201, item({'name': 'Modulo', 'city': 'Pisa'}), raw_body='name=Modulo&city=Pisa',
    content_type='application/x-www-form-urlencoded', effect=created('full_collaborator', item({'name': 'Modulo', 'city': 'Pisa'})))
add('update-malformed', 'owner', update('abc'), 400, ['badly formed hexadecimal UUID string'], method='PATCH', body={'city': 'X'})
add('update-unknown-server-error', 'owner', update(UNKNOWN), 500, SERVER, method='PATCH', body={'city': 'X'})
add('update-soft-deleted-server-error', 'owner', update('gone'), 500, SERVER, method='PATCH', body={'city': 'X'})
add('update-group-header-hides-server-error', 'owner', update('grouped'), 500, SERVER, method='PATCH', body={'city': 'X'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('update-foreign-denied', 'owner', update('estero'), 403, {'detail': 'User not allowed.'}, method='PATCH', body={'city': 'X'})
add('update-athlete-server-error', 'athlete', update('acqua'), 500, SERVER, method='PATCH', body={'city': 'X'})
add('update-invalid-email', 'owner', update('acqua'), 400, {'msg': 'validation errors.'}, method='PATCH', body={'email': 'nope'})
add('update-foreign-association-denied', 'owner', update('acqua'), 403, TENANT, method='PATCH', body={'sport_association': FOREIGN})
add('update-restricted-missing-permission', 'restricted_collaborator', update('acqua'), 403,
    {'detail': 'Missing permission: bookeeping.management.suppliers.update'}, method='PATCH', body={'city': 'X'})
add('update-wrong-method', 'owner', update('acqua'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('update-options', 'owner', update('acqua'), 200, {'name': 'Supplier Update', 'description': 'API endpoint to update a new supplier',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('update-city', 'owner', update('acqua'), 200, existing('acqua', city='Torino'), method='PATCH', body={'city': 'Torino'},
    effect=changed('owner', 'acqua', {'city': ['Roma', 'Torino']}))
add('update-same-value-no-audit', 'owner', update('acqua'), 200, existing('acqua', city='Torino'), method='PATCH', body={'city': 'Torino'},
    effect=changed('owner', 'acqua', {}))
add('update-clear-and-set', 'full_collaborator', update('beta'), 200, existing('beta', address=None, note='Nuova'), method='PATCH',
    body={'address': None, 'note': 'Nuova'}, effect=changed('full_collaborator', 'beta', {'address': ['Via Rossi 1', 'None'], 'note': ['None', 'Nuova']}))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Supplier add and update authored sequence with audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
