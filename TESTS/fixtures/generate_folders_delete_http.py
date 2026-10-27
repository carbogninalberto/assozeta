"""Author the folders/<pk>/delete HTTP sequence from folders.md.

The runner creates the MPTT folders through the ORM in the authored order,
which numbers tree ``a`` as a(1,8) > a1(2,5) > a1a(3,4), a2(6,7) and ``b`` as
b(1,2). Effects list the expected (lft, rght) of every remaining own folder,
the deleted labels, and archive rows whose folder becomes NULL (SET_NULL
through the collector, no audit). Folder is not audited. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('folders_delete_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
FOLDERS = [('a', None, OWN), ('a1', 'a', OWN), ('a1a', 'a1', OWN), ('a2', 'a', OWN), ('b', None, OWN), ('f', None, FOREIGN)]
ARCHIVE = {'id': 'a1000000-0000-4000-8000-000000830001', 'document': 'a1000000-0000-4000-8000-000000830002', 'folder': 'a1a',
           'association': OWN}
path = lambda value: f'/folders/$folder:{value}/delete' if value in dict((f[0], f) for f in FOLDERS) else f'/folders/{value}/delete'
NOT_FOUND = {'detail': 'No Folder matches the given query.'}
cases = []


def add(name, actor, method, target, status, response, *, effect=None):
    cases.append({'name': f'folders-delete-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': {},
                  'status': status, 'response': response, 'effect': effect})


add('restricted-missing-permission', 'restricted_collaborator', 'DELETE', 'a1', 403, {'detail': 'Missing permission: association.archive.update'})
add('athlete-server-error', 'athlete', 'DELETE', 'a1', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 'DELETE', 'a1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'DELETE', 'a1', 401, {'detail': 'Authentication credentials were not provided.'})
add('foreign-not-found', 'owner', 'DELETE', 'f', 404, NOT_FOUND)
add('missing-not-found', 'owner', 'DELETE', '999999999', 404, NOT_FOUND)
add('non-integer-not-found', 'owner', 'DELETE', 'abc', 404, {'detail': 'Not found.'})
add('wrong-method', 'owner', 'GET', 'a1', 405, {'detail': 'Method "GET" not allowed.'})
add('options', 'owner', 'OPTIONS', 'a1', 200, {'name': 'Folder', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
# Deleting a1 removes its subtree, closes the gap of width 4 after rght 5, and
# detaches the archive row filed under a1a.
add('owner-deletes-subtree', 'owner', 'DELETE', 'a1', 204, '<empty>',
    effect={'deleted': ['a1', 'a1a'], 'tree': {'a': [1, 4], 'a2': [2, 3], 'b': [1, 2]}, 'detached': [ARCHIVE['id']]})
add('repeat-not-found', 'owner', 'DELETE', 'a1', 404, NOT_FOUND)
add('full-collaborator-deletes-root', 'full_collaborator', 'DELETE', 'b', 204, '<empty>',
    effect={'deleted': ['b'], 'tree': {'a': [1, 4], 'a2': [2, 3]}, 'detached': []})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Folder delete authored sequence with MPTT effects', 'ids': IDS,
                              'folders': FOLDERS, 'archive': ARCHIVE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
