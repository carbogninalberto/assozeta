"""Author the saved-reports list/add/info/update/delete HTTP sequence (saved-reports.md).

Reports are scoped by SportAssociation.objects.get(user=request.user) and
created_by=request.user (the scope user: collaborators act as the owner).
Timestamps render with Python isoformat() (+00:00); ``$time`` accepts a
server-written value the runner checks against the stored row, ``$new:<label>``
binds generated IDs. SavedReport is not audited. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('saved_reports_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{850001 + n:012d}' for n in range(10))
SEED = {
    'r1': {'id': next(ids), 'association': OWN, 'creator': 'owner', 'name': 'Iscritti è', 'description': 'Elenco', 'tool': 'query_data',
           'params': {'model': 'subscription', 'filters': [1, None]}, 'ui': {'chart': 'bar'},
           'created': '2026-10-01T08:00:00+00:00', 'updated': '2026-10-03T08:00:00.250000+00:00'},
    'r2': {'id': next(ids), 'association': OWN, 'creator': 'owner', 'name': 'Pagamenti', 'description': '', 'tool': 'count_data',
           'params': {}, 'ui': {}, 'created': '2026-10-01T09:00:00+00:00', 'updated': '2026-10-02T08:00:00+00:00'},
    'rc': {'id': next(ids), 'association': OWN, 'creator': 'full_collaborator', 'name': 'Collaboratore', 'description': '', 'tool': 'count_data',
           'params': {}, 'ui': {}, 'created': '2026-10-01T10:00:00+00:00', 'updated': '2026-10-04T08:00:00+00:00'},
    'rf': {'id': next(ids), 'association': FOREIGN, 'creator': 'foreign_owner', 'name': 'Estero', 'description': '', 'tool': 'get_schema',
           'params': {}, 'ui': {}, 'created': '2026-10-01T11:00:00+00:00', 'updated': '2026-10-01T11:00:00+00:00'},
}
SERVER = '<stable-server-error-page>'
NOT_FOUND = {'error': 'Report not found'}
REQUIRED = {'error': 'name and tool_name are required'}


def summary(label):
    row = SEED[label]
    return {'saved_report_id': row['id'], 'name': row['name'], 'description': row['description'], 'tool_name': row['tool'],
            'created_at': row['created'], 'updated_at': row['updated']}


def detail(label):
    row = SEED[label]
    return {'data': {**summary(label), 'params': row['params'], 'ui_config': row['ui']}}


cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, effect=None, headers=None):
    case = {'name': f'saved-reports-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


LIST, ADD = '/saved-reports/list', '/saved-reports/add'
info = lambda key: f'/saved-reports/{SEED[key]["id"] if key in SEED else key}/info'
update = lambda key: f'/saved-reports/{SEED[key]["id"] if key in SEED else key}/update'
delete = lambda key: f'/saved-reports/{SEED[key]["id"] if key in SEED else key}/delete'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}

# Ordered -updated_at; the collaborator-created row is never visible (created_by is the scope user).
add('list-owner', 'owner', 'GET', LIST, 200, {'data': [summary('r1'), summary('r2')]})
add('list-full-collaborator-owner-scope', 'full_collaborator', 'GET', LIST, 200, {'data': [summary('r1'), summary('r2')]})
add('list-admin-impersonating-owner', 'admin', 'GET', LIST, 200, {'data': [summary('r1'), summary('r2')]},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-foreign-owner', 'foreign_owner', 'GET', LIST, 200, {'data': [summary('rf')]})
add('list-restricted-unmapped', 'restricted_collaborator', 'GET', LIST, 403, unmapped(LIST))
add('list-athlete-server-error', 'athlete', 'GET', LIST, 500, SERVER)
add('list-admin-without-session', 'admin', 'GET', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-anonymous', 'anonymous', 'GET', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-wrong-method', 'owner', 'POST', LIST, 405, {'detail': 'Method "POST" not allowed.'}, body={})
# The viewset's delete() answers DELETE on every route; without a pk nothing matches.
add('list-delete-method-not-found', 'owner', 'DELETE', LIST, 404, NOT_FOUND)
add('list-options', 'owner', 'OPTIONS', LIST, 200, {'name': 'Saved Report', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
add('info-owner', 'owner', 'GET', info('r1'), 200, detail('r1'))
add('info-foreign-not-found', 'owner', 'GET', info('rf'), 404, NOT_FOUND)
add('info-collaborator-created-not-found', 'owner', 'GET', info('rc'), 404, NOT_FOUND)
add('info-malformed-server-error', 'owner', 'GET', info('abc'), 500, SERVER)
created = lambda label, values: {'kind': 'create', 'label': label, 'values': values}
add('add-json', 'owner', 'POST', ADD, 201, {'saved_report_id': '$new:json', 'name': 'Report è'},
    body={'name': '  Report è ', 'tool_name': ' export_data ', 'description': 7, 'params': {'b': [1, 2.5]}, 'ui_config': ['x']},
    effect=created('json', {'name': 'Report è', 'tool_name': 'export_data', 'description': '7', 'params': {'b': [1, 2.5]}, 'ui_config': ['x']}))
add('add-form', 'full_collaborator', 'POST', ADD, 201, {'saved_report_id': '$new:form', 'name': 'Da form'},
    raw_body='name=Da+form&tool_name=count_data&params=x', content_type='application/x-www-form-urlencoded',
    effect=created('form', {'name': 'Da form', 'tool_name': 'count_data', 'description': '', 'params': 'x', 'ui_config': {}}))
add('add-missing-tool', 'owner', 'POST', ADD, 400, REQUIRED, body={'name': 'x'})
add('add-blank-name', 'owner', 'POST', ADD, 400, REQUIRED, body={'name': '   ', 'tool_name': 'count_data'})
add('add-unknown-tool', 'owner', 'POST', ADD, 400, {'error': 'Unknown tool: nope'}, body={'name': 'x', 'tool_name': 'nope'})
add('add-non-string-name-server-error', 'owner', 'POST', ADD, 500, SERVER, body={'name': 5, 'tool_name': 'count_data'})
add('add-null-description-server-error', 'owner', 'POST', ADD, 500, SERVER, body={'name': 'x', 'tool_name': 'count_data', 'description': None})
add('add-list-body-server-error', 'owner', 'POST', ADD, 500, SERVER, body=[])
# Raw values are assigned and saved; the response echoes the in-memory name.
add('update-raw-values', 'owner', 'PATCH', update('r2'), 200, {'saved_report_id': SEED['r2']['id'], 'name': 5},
    body={'name': 5, 'params': [1], 'ui_config': {'k': True}, 'tool_name': 'ignored'},
    effect={'kind': 'update', 'label': 'r2', 'values': {'name': '5', 'params': [1], 'ui_config': {'k': True}}})
# JSONField(null=False) stores None as SQL NULL, which the column rejects.
add('update-null-config-server-error', 'owner', 'PATCH', update('r2'), 500, SERVER, body={'ui_config': None})
add('update-list-body-server-error', 'owner', 'PATCH', update('r2'), 500, SERVER, body=['name'])
add('update-null-name-server-error', 'owner', 'PATCH', update('r2'), 500, SERVER, body={'name': None})
add('update-foreign-not-found', 'owner', 'PATCH', update('rf'), 404, NOT_FOUND, body={'name': 'x'})
add('delete-owner', 'owner', 'DELETE', delete('r1'), 204, '<empty>', effect={'kind': 'delete', 'label': 'r1'})
add('delete-repeat-not-found', 'owner', 'DELETE', delete('r1'), 404, NOT_FOUND)
add('delete-foreign-not-found', 'owner', 'DELETE', delete('rf'), 404, NOT_FOUND)
add('list-after-writes', 'owner', 'GET', LIST, 200, {'data': [
    {'saved_report_id': SEED['r2']['id'], 'name': '5', 'description': '', 'tool_name': 'count_data', 'created_at': SEED['r2']['created'],
     'updated_at': '$time'},
    {'saved_report_id': '$new:form', 'name': 'Da form', 'description': '', 'tool_name': 'count_data', 'created_at': '$time', 'updated_at': '$time'},
    {'saved_report_id': '$new:json', 'name': 'Report è', 'description': '7', 'tool_name': 'export_data', 'created_at': '$time', 'updated_at': '$time'}]})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Saved reports CRUD authored sequence', 'ids': IDS, 'seed': SEED,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
