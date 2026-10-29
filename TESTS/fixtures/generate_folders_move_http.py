"""Author the folders/<pk>/move HTTP sequence from folders.md.

The runner creates the folders through the ORM in this order, so MPTT gives
tree ids k+1 (a), k+2 (b), k+3 (f) after any existing trees, numbered
a(1,8) > a1(2,5) > a1a(3,4), a2(6,7); b(1,4) > b1(2,3); f(1,2). Expected
states use relative tree ids (r -> k + r); a node made root gets the next id.
Each state was derived by hand from django-mptt 0.x TreeManager SQL
(move_to(target) is 'first-child'; move_to(None) makes a child a new root).
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('folders_move_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
FOLDERS = [('a', None, OWN), ('a1', 'a', OWN), ('a1a', 'a1', OWN), ('a2', 'a', OWN), ('b', None, OWN), ('b1', 'b', OWN), ('f', None, FOREIGN)]
LABELS = {label for label, _, _ in FOLDERS}
path = lambda value: f'/folders/$folder:{value}/move' if value in LABELS else f'/folders/{value}/move'
target = lambda label: f'$folder:{label}'
SERVER = '<stable-server-error-page>'


def state(**nodes):
    """label -> [relative tree, lft, rght, level, parent label]."""
    return {label: list(values) for label, values in nodes.items()}


INITIAL = state(a=(1, 1, 8, 0, None), a1=(1, 2, 5, 1, 'a'), a1a=(1, 3, 4, 2, 'a1'), a2=(1, 6, 7, 1, 'a'),
                b=(2, 1, 4, 0, None), b1=(2, 2, 3, 1, 'b'), f=(3, 1, 2, 0, None))
cases = []


def add(name, actor, folder, status, response, *, body=None, method='POST', after=None):
    case = {'name': f'folders-move-{name}', 'actor': actor, 'method': method, 'path': path(folder), 'headers': {},
            'status': status, 'response': response, 'after': after}
    if body is not None:
        case['body'] = body
    cases.append(case)


add('restricted-missing-permission', 'restricted_collaborator', 'a1', 403, {'detail': 'Missing permission: association.archive.update'},
    body={'new_parent': target('a2')})
add('athlete-server-error', 'athlete', 'a1', 500, SERVER, body={})
add('admin-without-session', 'admin', 'a1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('anonymous', 'anonymous', 'a1', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('foreign-folder-not-found', 'owner', 'f', 404, {'detail': 'No Folder matches the given query.'}, body={})
add('non-integer-folder-not-found', 'owner', 'abc', 404, {'detail': 'Not found.'}, body={})
add('foreign-target-denied', 'owner', 'a1', 403, {'detail': 'Cannot move to folder from different sport association.'},
    body={'new_parent': target('f')})
add('missing-target-server-error', 'owner', 'a1', 500, SERVER, body={'new_parent': 999999999})
add('non-integer-target-server-error', 'owner', 'a1', 500, SERVER, body={'new_parent': 'abc'})
# InvalidMove raises before any SQL.
add('into-descendant-server-error', 'owner', 'a', 500, SERVER, body={'new_parent': target('a1a')})
add('into-itself-server-error', 'owner', 'a1', 500, SERVER, body={'new_parent': target('a1')})
add('list-body-server-error', 'owner', 'a1', 500, SERVER, body=[])
add('root-to-root-no-change', 'owner', 'b', 200, '<empty>', body={'new_parent': 0}, after=INITIAL)
add('wrong-method', 'owner', 'a1', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
info = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    'label': label, **extra}
# The route allows POST, so SimpleMetadata describes FolderSerializer's fields.
add('options', 'owner', 'a1', 200, {'name': 'Folder', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
    'actions': {'POST': {'id': info('integer', 'ID', read_only=True), 'name': info('string', 'Name', True, max_length=255),
                         'parent': info('field', 'Parent'), 'sport_association': info('field', 'Sport association', True),
                         'created_at': info('datetime', 'Created at'), 'level': info('integer', 'Level', read_only=True),
                         'children': info('field', 'Children', read_only=True)}}}, method='OPTIONS')
# 1. Child within its tree: a1 becomes first child of a2.
S1 = state(a=(1, 1, 8, 0, None), a2=(1, 2, 7, 1, 'a'), a1=(1, 3, 6, 2, 'a2'), a1a=(1, 4, 5, 3, 'a1'),
           b=(2, 1, 4, 0, None), b1=(2, 2, 3, 1, 'b'), f=(3, 1, 2, 0, None))
add('child-within-tree', 'owner', 'a1', 200, '<empty>', body={'new_parent': target('a2')}, after=S1)
# 2. Child to root: a1a gets the next tree id (4).
S2 = state(a=(1, 1, 6, 0, None), a2=(1, 2, 5, 1, 'a'), a1=(1, 3, 4, 2, 'a2'), a1a=(4, 1, 2, 0, None),
           b=(2, 1, 4, 0, None), b1=(2, 2, 3, 1, 'b'), f=(3, 1, 2, 0, None))
add('child-to-root', 'full_collaborator', 'a1a', 200, '<empty>', body={'new_parent': None}, after=S2)
# 3. Root into another tree: b with its child under a1.
S3 = state(a=(1, 1, 10, 0, None), a2=(1, 2, 9, 1, 'a'), a1=(1, 3, 8, 2, 'a2'), b=(1, 4, 7, 3, 'a1'), b1=(1, 5, 6, 4, 'b'),
           a1a=(4, 1, 2, 0, None), f=(3, 1, 2, 0, None))
add('root-into-other-tree', 'owner', 'b', 200, '<empty>', body={'new_parent': target('a1')}, after=S3)
# 4. Child into another tree: a2's subtree under the new root a1a.
S4 = state(a=(1, 1, 2, 0, None), a1a=(4, 1, 10, 0, None), a2=(4, 2, 9, 1, 'a1a'), a1=(4, 3, 8, 2, 'a2'), b=(4, 4, 7, 3, 'a1'),
           b1=(4, 5, 6, 4, 'b'), f=(3, 1, 2, 0, None))
add('child-into-other-tree', 'owner', 'a2', 200, '<empty>', body={'new_parent': target('a1a')}, after=S4)
# DELETE on the move route reaches the viewset's delete() with this pk.
S5 = {label: values for label, values in S4.items() if label != 'a'}
add('delete-method-deletes-folder', 'owner', 'a', 204, '<empty>', method='DELETE', after=S5)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Folder move authored sequence with MPTT effects', 'ids': IDS,
                              'folders': FOLDERS, 'initial': INITIAL, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
