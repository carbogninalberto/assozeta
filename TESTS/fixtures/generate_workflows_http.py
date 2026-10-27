"""Author the workflow list/details HTTP sequence from automation-workflows.md.

Seeded workflows have fixed timestamps, so every expected body is literal.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('workflows_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
TREE = [{'id': 'trigger', 'value': 'new_subscription', 'data': {}},
        {'id': 'message', 'value': 'email', 'data': {'subject': 'Benvenuto à', 'content': 'Ciao'}}]
SEED = {
    'own_older': {'id': 'a1000000-0000-4000-8000-000000750001', 'association': OWN, 'name': 'Benvenuto',
                  'description': None, 'tree': TREE, 'enabled': False, 'created_at': '2026-10-20T09:00:00Z', 'updated_at': '2026-10-21T10:00:00.250000Z'},
    'own_newer': {'id': 'a1000000-0000-4000-8000-000000750002', 'association': OWN, 'name': 'Promemoria',
                  'description': 'Invio mensile', 'tree': [], 'enabled': True, 'created_at': '2026-10-22T09:00:00Z', 'updated_at': '2026-10-22T09:00:00Z'},
    'foreign': {'id': 'a1000000-0000-4000-8000-000000750003', 'association': FOREIGN, 'name': 'Estero',
                'description': '', 'tree': TREE, 'enabled': True, 'created_at': '2026-10-23T09:00:00Z', 'updated_at': '2026-10-23T09:00:00Z'},
}


def row(label):
    values = SEED[label]
    return {'automation_workflow_id': values['id'], 'name': values['name'], 'description': values['description'],
            'automation_tree': values['tree'], 'enabled': values['enabled'], 'created_at': values['created_at'],
            'updated_at': values['updated_at'], 'sport_association': values['association']}


LIST = '/communications/workflows/list'
details = lambda value: f'/communications/workflows/{SEED[value]["id"] if value in SEED else value}/details'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'workflows-{name}', 'actor': actor, 'method': method, 'path': path,
                  'headers': headers or {}, 'status': status, 'response': response})


own = [row('own_newer'), row('own_older')]
add('list-owner', 'owner', LIST, 200, own)
add('list-full-collaborator', 'full_collaborator', LIST, 200, own)
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped(LIST))
add('list-athlete', 'athlete', LIST, 403, {'detail': 'You do not have permission to perform this action.'})
add('list-anonymous', 'anonymous', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-foreign-owner', 'foreign_owner', LIST, 200, [row('foreign')])
add('list-admin-without-session', 'admin', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-admin-impersonating-owner', 'admin', LIST, 200, own, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-deleted-association', 'deleted_association_owner', LIST, 500, '<stable-server-error-page>')
add('list-owner-without-association', 'owner_without_association', LIST, 500, '<stable-server-error-page>')
add('list-wrong-method', 'owner', LIST, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('list-options', 'owner', LIST, 200, {'name': 'Communication Workflows List', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('details-owner', 'owner', details('own_older'), 200, row('own_older'))
add('details-full-collaborator', 'full_collaborator', details('own_newer'), 200, row('own_newer'))
add('details-foreign-not-found', 'owner', details('foreign'), 404, {'msg': 'workflow not found.'})
add('details-missing-not-found', 'owner', details('a1000000-0000-4000-8000-000000759999'), 404, {'msg': 'workflow not found.'})
add('details-malformed', 'owner', details('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'])
add('details-restricted-unmapped', 'restricted_collaborator', details('own_older'), 403, unmapped(details('own_older')))
add('details-foreign-owner-own-row', 'foreign_owner', details('foreign'), 200, row('foreign'))
add('details-options', 'owner', details('own_older'), 200, {'name': 'Communication Workflows Details', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Workflow list/details authored sequence', 'ids': IDS, 'seed': SEED,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
