"""Author the folders/list HTTP sequence from folders.md.

Folders are MPTT nodes created by the runner through the ORM in the authored
order; responses use ``$folder:<label>`` for their integer IDs. Nodes are in
tree order (tree_id, lft), children recursively by get_children(). Run with
``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('folders_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/folders/list'
# (label, parent label, association, created_at); creation order fixes tree order.
FOLDERS = [('a', None, OWN, '2026-10-01T08:00:00Z'), ('a1', 'a', OWN, '2026-10-02T08:00:00.250000Z'),
           ('a1a', 'a1', OWN, '2026-10-03T08:00:00Z'), ('a2', 'a', OWN, '2026-10-04T08:00:00Z'),
           ('b', None, OWN, '2026-10-05T08:00:00Z'), ('f', None, FOREIGN, '2026-10-06T08:00:00Z')]
NAMES = {'a': 'Archivio è', 'a1': 'Verbali', 'a1a': '2026', 'a2': 'Bilanci', 'b': 'Contratti', 'f': 'Estero'}
LEVEL = {'a': 0, 'a1': 1, 'a1a': 2, 'a2': 1, 'b': 0, 'f': 0}


def node(label):
    _, parent, association, created = next(row for row in FOLDERS if row[0] == label)
    children = [row[0] for row in FOLDERS if row[1] == label]
    return {'id': f'$folder:{label}', 'name': NAMES[label], 'parent': f'$folder:{parent}' if parent else None,
            'sport_association': association, 'created_at': created, 'level': LEVEL[label],
            'children': [node(child) for child in children]}


def crumb(*labels):
    return [{'id': f'$folder:{label}', 'name': NAMES[label]} for label in labels]


cases = []


def add(name, actor, status, response, *, query='', method='GET', headers=None):
    cases.append({'name': f'folders-list-{name}', 'actor': actor, 'method': method, 'path': PATH + query,
                  'headers': headers or {}, 'status': status, 'response': response})


ROOTS = {'data': [node('a'), node('b')], 'path': None}
add('owner-roots', 'owner', 200, ROOTS)
add('owner-empty-folder-roots', 'owner', 200, ROOTS, query='?folder=')
add('owner-children', 'owner', 200, {'data': [node('a1'), node('a2')], 'path': crumb('a')}, query='?folder=$folder:a')
add('owner-leaf-path', 'owner', 200, {'data': [], 'path': crumb('a', 'a1', 'a1a')}, query='?folder=$folder:a1a')
add('full-collaborator-owner-scope', 'full_collaborator', 200, ROOTS)
add('admin-impersonating-owner', 'admin', 200, ROOTS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-folder-denied', 'owner', 403, {'detail': 'Cannot access folder from different sport association.'}, query='?folder=$folder:f')
add('missing-folder', 'owner', 404, {'detail': 'Parent folder not found.'}, query='?folder=999999999')
add('non-numeric-folder-server-error', 'owner', 500, '<stable-server-error-page>', query='?folder=abc')
add('foreign-owner-roots', 'foreign_owner', 200, {'data': [node('f')], 'path': None})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.archive.read'})
add('athlete-server-error', 'athlete', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
# The viewset's delete() makes DELETE allowed and dispatched without a pk.
add('delete-method-server-error', 'owner', 500, '<stable-server-error-page>', method='DELETE')
add('options', 'owner', 200, {'name': 'Folder', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Folders list authored sequence', 'ids': IDS, 'folders': FOLDERS,
                              'names': NAMES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
