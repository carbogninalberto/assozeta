"""Author the supplier/<uid>/delete and supplier/bulk-delete HTTP sequence (suppliers.md).

Builds on the supplier-list seed plus a payment of one supplier.
supplier_delete validates the uid (400), gets the owner's association (a
missing one is a 500, e.g. for athletes) and the live, group-aware supplier
of that association (otherwise 403 'User not allowed.'), then hard-deletes
it: references are set to NULL (no audit) and auditlog records the delete,
indexed through changes_json. supplier_bulk_delete refuses athletes with a
bare 403, requires a non-empty supplier_ids (400), validates every id
(400 'Invalid UUID format.') and deletes the matching live suppliers of the
association. Run with ``python3 -I`` after the supplier-list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('supplier_deletes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('suppliers_http.json').read_text()))
IDS = SOURCE['ids']
OWN = IDS['own_association']
SUPPLIERS = SOURCE['suppliers']
SOURCE['payments'] = {'supplied': {'id': 'a1000000-0000-4000-8000-000000910101', 'association': OWN, 'supplier': 'acqua', 'amount': '12.00'}}
UNKNOWN = 'a1000000-0000-4000-8000-000000919999'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, path, status, response, *, method='DELETE', body=None, headers=None, effect=None):
    case = {'name': f'supplier-deletes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    cases.append(case)


one = lambda label: f'/supplier/{SUPPLIERS[label]["id"] if label in SUPPLIERS else label}/delete'
BULK = '/supplier/bulk-delete'
DENIED = {'detail': 'User not allowed.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
gone = lambda actor, labels, payments=(): {'actor': actor, 'deleted': list(labels), 'nulled_payments': list(payments)}
add('malformed', 'owner', one('abc'), 400, ['badly formed hexadecimal UUID string'])
add('unknown', 'owner', one(UNKNOWN), 403, DENIED)
add('foreign', 'owner', one('estero'), 403, DENIED)
add('soft-deleted', 'owner', one('gone'), 403, DENIED)
add('group-header-hides', 'owner', one('grouped'), 403, DENIED, headers={'X-Group-Id': IDS['foreign_group']})
add('athlete-server-error', 'athlete', one('acqua'), 500, SERVER)
add('owner-scoped-athlete-denied', 'owner', one('acqua'), 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED)
add('restricted-missing-permission', 'restricted_collaborator', one('acqua'), 403,
    {'detail': 'Missing permission: bookeeping.management.suppliers.delete'})
add('admin-without-session', 'admin', one('acqua'), 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', one('acqua'), 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', one('acqua'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', one('acqua'), 200, {'name': 'Supplier Delete', 'description': 'API endpoint to delete a supplier',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
OK = {'data': {'msg': 'supplier deleted.'}}
add('owner-deletes-referenced', 'owner', one('acqua'), 200, OK, effect=gone('owner', ['acqua'], ['supplied']))
add('owner-deletes-in-group', 'owner', one('grouped'), 200, OK, headers={'X-Group-Id': IDS['own_group']}, effect=gone('owner', ['grouped']))
add('full-collaborator-deletes', 'full_collaborator', one('beta'), 200, OK, effect=gone('full_collaborator', ['beta']))
add('bulk-athlete-forbidden', 'athlete', BULK, 403, '<empty>', body={'supplier_ids': [SUPPLIERS['cavalli']['id']]})
add('bulk-missing-ids', 'owner', BULK, 400, {'msg': 'No supplier IDs provided.'}, body={})
add('bulk-empty-ids', 'owner', BULK, 400, {'msg': 'No supplier IDs provided.'}, body={'supplier_ids': []})
add('bulk-invalid-id', 'owner', BULK, 400, {'msg': 'Invalid UUID format.'}, body={'supplier_ids': [SUPPLIERS['cavalli']['id'], 'abc']})
add('bulk-text-ids', 'owner', BULK, 400, {'msg': 'Invalid UUID format.'}, body={'supplier_ids': 'abc'})
add('bulk-number-id', 'owner', BULK, 400, {'msg': 'Invalid UUID format.'}, body={'supplier_ids': [5]})
add('bulk-list-body-server-error', 'owner', BULK, 500, SERVER, body=[])
add('bulk-restricted-missing-permission', 'restricted_collaborator', BULK, 403,
    {'detail': 'Missing permission: bookeeping.management.suppliers.delete'}, body={'supplier_ids': [SUPPLIERS['cavalli']['id']]})
add('bulk-wrong-method', 'owner', BULK, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('bulk-options', 'owner', BULK, 200, {'name': 'Supplier Bulk Delete', 'description': 'API endpoint to delete multiple suppliers at once',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('bulk-owner', 'owner', BULK, 200, {'data': {'msg': '2 suppliers deleted.'}},
    body={'supplier_ids': [SUPPLIERS['cavalli']['id'], SUPPLIERS['delta']['id'], SUPPLIERS['estero']['id'], SUPPLIERS['gone']['id'], UNKNOWN,
                           SUPPLIERS['delta']['id']]}, effect=gone('owner', ['cavalli', 'delta']))
add('bulk-nothing-matches', 'owner', BULK, 200, {'data': {'msg': '0 suppliers deleted.'}}, body={'supplier_ids': [UNKNOWN]},
    effect=gone('owner', []))
add('bulk-full-collaborator', 'full_collaborator', BULK, 200, {'data': {'msg': '1 suppliers deleted.'}},
    body={'supplier_ids': [SUPPLIERS['emme']['id']]}, effect=gone('full_collaborator', ['emme']))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Supplier delete and bulk delete authored sequence with audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
