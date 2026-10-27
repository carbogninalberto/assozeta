"""Author the workflow add/delete HTTP sequence from automation-workflows.md.

Audit expectations follow the characterized auditlog rules: JSON fields as
json.dumps(sort_keys=True) with ASCII escaping, datetimes as naive UTC
str(datetime), None values omitted, and the changes_json.sport_association
index path. ``$new:<label>``/``$now`` bind generated IDs and write times.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('workflow_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
TREE = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'b': 1, 'a': [True, None, 1.5]}},
        {'id': 'message', 'value': 'email', 'data': {'subject': 'Oggetto à 😀'}}]
CLIENT_ID = 'a1000000-0000-4000-8000-000000760009'
SEED = {
    'own_delete': {'id': 'a1000000-0000-4000-8000-000000760001', 'association': OWN, 'name': 'Da eliminare', 'description': 'Vecchia',
                   'tree': [], 'enabled': True, 'created_at': '2026-10-20T09:00:00.250000Z', 'updated_at': '2026-10-21T10:00:00Z'},
    'foreign': {'id': 'a1000000-0000-4000-8000-000000760002', 'association': FOREIGN, 'name': 'Estero', 'description': None,
                'tree': [], 'enabled': False, 'created_at': '2026-10-22T09:00:00Z', 'updated_at': '2026-10-22T09:00:00Z'},
}
TREE_AUDIT = json.dumps(TREE, sort_keys=True)
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
ADD = '/communications/workflows/add'
delete = lambda value: f'/communications/workflows/{SEED[value]["id"] if value in SEED else value}/delete'
cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, effect=None, headers=None):
    case = {'name': f'workflow-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect or {'kind': 'none'}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def created(label, actor, *, name, tree, enabled=False, description=None, tree_audit=None):
    changes = {'automation_workflow_id': ['None', f'$new:{label}'], 'name': ['None', name],
               'automation_tree': ['None', tree_audit if tree_audit is not None else json.dumps(tree, sort_keys=True)],
               'enabled': ['None', str(enabled)], 'sport_association': ['None', OWN],
               'created_at': ['None', '$now-audit'], 'updated_at': ['None', '$now-audit']}
    if description is not None:
        changes['description'] = ['None', description]
    return {'kind': 'create', 'label': label, 'values': {'sport_association_id': OWN, 'name': name, 'description': description,
            'automation_tree': tree, 'enabled': enabled}, 'audit': {'action': 0, 'actor': actor, 'changes': changes}}


add('add-owner', 'owner', 'POST', ADD, 200, {'msg': 'workflow added.', 'workflow_id': '$new:owner'},
    body={'name': 'Benvenuto à', 'automation_tree': TREE}, effect=created('owner', 'owner', name='Benvenuto à', tree=TREE, tree_audit=TREE_AUDIT))
add('add-client-id-truthy-enabled', 'owner', 'POST', ADD, 200, {'msg': 'workflow added.', 'workflow_id': CLIENT_ID},
    body={'automation_workflow_id': CLIENT_ID, 'name': 'Scelto', 'automation_tree': [], 'enabled': 'yes', 'description': '',
          'created_at': '2020-01-01T00:00:00Z', 'sport_association': FOREIGN},
    effect=created('client', 'owner', name='Scelto', tree=[], enabled=True, description=''))
cases[-1]['effect']['fixed_id'] = CLIENT_ID
add('add-duplicate-id', 'owner', 'POST', ADD, 400, {'msg': 'workflow not valid.'},
    body={'automation_workflow_id': CLIENT_ID, 'name': 'Doppio', 'automation_tree': []})
for label, body in (('blank-name', {'name': '   ', 'automation_tree': []}), ('long-name', {'name': 'x' * 256}),
                    ('missing-name', {'automation_tree': []}), ('tree-object', {'name': 'x', 'automation_tree': {}}),
                    ('tree-null', {'name': 'x', 'automation_tree': None}),
                    ('sms-message', {'name': 'x', 'automation_tree': [{'id': 'message', 'value': 'sms'}]}),
                    ('bad-id', {'name': 'x', 'automation_workflow_id': 'nope'}), ('enabled-invalid', {'name': 'x', 'enabled': 'maybe'})):
    add(f'add-invalid-{label}', 'owner', 'POST', ADD, 400, {'msg': 'workflow not valid.'}, body=body)
add('add-list-body', 'owner', 'POST', ADD, 500, '<stable-server-error-page>', body=[])
add('add-null-body', 'owner', 'POST', ADD, 500, '<stable-server-error-page>', raw_body='null', content_type='application/json')
add('add-form-body', 'owner', 'POST', ADD, 500, '<stable-server-error-page>', raw_body='name=x', content_type='application/x-www-form-urlencoded')
add('add-full-collaborator', 'full_collaborator', 'POST', ADD, 200, {'msg': 'workflow added.', 'workflow_id': '$new:full'},
    body={'name': 'Collaboratore'}, effect=created('full', 'full_collaborator', name='Collaboratore', tree=[]))
add('add-restricted-unmapped', 'restricted_collaborator', 'POST', ADD, 403, unmapped(ADD), body={'name': 'x'})
add('add-athlete', 'athlete', 'POST', ADD, 403, {'detail': 'You do not have permission to perform this action.'}, body={'name': 'x'})
add('add-anonymous', 'anonymous', 'POST', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={'name': 'x'})
add('add-wrong-method', 'owner', 'GET', ADD, 405, {'detail': 'Method "GET" not allowed.'})
add('add-options', 'owner', 'OPTIONS', ADD, 200, {'name': 'Communication Workflows Add', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
add('delete-malformed', 'owner', 'DELETE', delete('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'])
add('delete-foreign-server-error', 'owner', 'DELETE', delete('foreign'), 500, '<stable-server-error-page>')
add('delete-missing-server-error', 'owner', 'DELETE', delete('a1000000-0000-4000-8000-000000769999'), 500, '<stable-server-error-page>')
add('delete-restricted-unmapped', 'restricted_collaborator', 'DELETE', delete('own_delete'), 403, unmapped(delete('own_delete')))
seed = SEED['own_delete']
add('delete-owner', 'owner', 'DELETE', delete('own_delete'), 200, {'msg': 'workflow deleted.'},
    effect={'kind': 'delete', 'row': seed['id'], 'audit': {'action': 2, 'actor': 'owner', 'changes': {
        'automation_workflow_id': [seed['id'], 'None'], 'name': [seed['name'], 'None'], 'description': [seed['description'], 'None'],
        'automation_tree': ['[]', 'None'], 'enabled': ['True', 'None'], 'sport_association': [OWN, 'None'],
        'created_at': ['2026-10-20 09:00:00.250000', 'None'], 'updated_at': ['2026-10-21 10:00:00', 'None']}}})
add('delete-repeat-server-error', 'owner', 'DELETE', delete('own_delete'), 500, '<stable-server-error-page>')
add('delete-options', 'owner', 'OPTIONS', delete('own_delete'), 200, {'name': 'Communication Workflows Delete', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Workflow add/delete authored sequence with audit effects', 'ids': IDS,
                              'seed': SEED, 'index_path': 'changes_json.sport_association', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
