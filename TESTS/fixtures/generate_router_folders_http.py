"""Author the DefaultRouter folders HTTP sequence (router-stock.md).

`router.register(r'folders', FolderViewSet)`. The list route binds GET to
list() (roots, or ?folder= children with their path) and POST to the stock
CreateModelMixin.create: request.data is not written (unlike add), so a JSON
dict, a form or a multipart body validates as is, a non-dict JSON body is
the 400 non-dict error, sport_association is required and tenant-scoped
(without a current association, athletes and owners without one, the scope
does not apply), the unique-together check (skipped for a None parent) runs
on the client's values, and perform_create's SportAssociation.objects.get(
user=request.user) (a 500 for athletes) supplies the saved association; the
MPTT insert is the parent's last child or a new tree. The detail route binds
GET to the stock retrieve() (FolderSerializer with nested children; a pk
int() rejects is the bare 404), PUT to update() (partial), PATCH to
partial_update() (500) and DELETE to the stock destroy() (the folder's
subtree). On the format twins list() and update() reject format= (500).
Folder is not audited. Folder ids are fixed as in folder-writes. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_folders_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
UNKNOWN = 'a1000000-0000-4000-8000-000000989999'
FIRST, NEXT = 990301, 990401
SERVER = '<stable-server-error-page>'
SEED = [('a', 'Archivio è', None, OWN, '2026-10-01T08:00:00Z'), ('a1', 'Verbali', 'a', OWN, '2026-10-02T08:00:00.250000Z'),
        ('a1a', '2026', 'a1', OWN, '2026-10-03T08:00:00Z'), ('a2', 'Bilanci', 'a', OWN, '2026-10-04T08:00:00Z'),
        ('b', 'Contratti', None, OWN, '2026-10-05T08:00:00Z'), ('b1', 'Verbali', 'b', OWN, '2026-10-06T08:00:00Z'),
        ('c', 'Vuota', None, OWN, '2026-10-07T08:00:00Z'), ('f', 'Estero', None, FOREIGN, '2026-10-08T08:00:00Z'),
        ('f1', 'Interno', 'f', FOREIGN, '2026-10-09T08:00:00Z')]


class Forest:
    """The logical folder forest; numbering() is the nested-set state MPTT keeps."""

    def __init__(self):
        self.nodes, self.next_id = {}, NEXT

    def insert(self, label, name, parent, association, created, ident=None):
        if ident is None:
            ident, self.next_id = self.next_id, self.next_id + 1
        tree = self.nodes[parent]['tree'] if parent else self.next_tree()
        self.nodes[label] = {'id': ident, 'name': name, 'parent': parent, 'association': association, 'created': created,
                             'tree': tree, 'children': []}
        if parent:
            self.nodes[parent]['children'].append(label)
        return label

    def next_tree(self):
        return max((node['tree'] for node in self.nodes.values()), default=0) + 1

    def subtree(self, label):
        return [label] + [item for child in self.nodes[label]['children'] for item in self.subtree(child)]

    def detach(self, label):
        parent = self.nodes[label]['parent']
        if parent:
            self.nodes[parent]['children'].remove(label)

    def set_tree(self, label, tree):
        for item in self.subtree(label):
            self.nodes[item]['tree'] = tree

    def move(self, label, target):
        """MPTTModel.save with a changed parent: last child of target, or a new root."""
        node = self.nodes[label]
        if target is None:
            tree = self.next_tree()
            self.detach(label)
            node['parent'] = None
            self.set_tree(label, tree)
            return
        assert target not in self.subtree(label)
        self.detach(label)
        node['parent'] = target
        self.nodes[target]['children'].append(label)
        self.set_tree(label, self.nodes[target]['tree'])

    def delete(self, label):
        self.detach(label)
        for item in self.subtree(label):
            del self.nodes[item]

    def numbering(self):
        result = {}

        def walk(label, counter, level):
            left = counter
            counter += 1
            for child in self.nodes[label]['children']:
                counter = walk(child, counter, level + 1)
            result[label] = (left, counter, level)
            return counter + 1

        for label, node in self.nodes.items():
            if node['parent'] is None:
                walk(label, 1, 0)
        return result

    def state(self):
        numbers = self.numbering()
        rows = {}
        for label, node in self.nodes.items():
            left, right, level = numbers[label]
            rows[str(node['id'])] = {'name': node['name'], 'parent': self.nodes[node['parent']]['id'] if node['parent'] else None,
                                     'association': node['association'], 'created_at': node['created'], 'tree': node['tree'],
                                     'lft': left, 'rght': right, 'level': level}
        return rows

    def render(self, label, top=True):
        """FolderSerializer: children recurse through get_children() in tree order."""
        node, (_, _, level) = self.nodes[label], self.numbering()[label]
        assert top or node['created'] != '$now', 'a server-time folder cannot be rendered as a child'
        return {'id': node['id'], 'name': node['name'], 'parent': self.nodes[node['parent']]['id'] if node['parent'] else None,
                'sport_association': node['association'], 'created_at': node['created'], 'level': level,
                'children': [self.render(child, False) for child in node['children']]}


forest = Forest()
for label, name, parent, association, created in SEED:
    forest.insert(label, name, parent, association, created, FIRST + len(forest.nodes))
SEEDED = [{'label': label, 'id': forest.nodes[label]['id'], 'name': name, 'parent': forest.nodes[parent]['id'] if parent else None,
           'association': association, 'created_at': created} for label, name, parent, association, created in SEED]
ID = lambda label: forest.nodes[label]['id']
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=False):
    case = {'name': f'router-folders-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'state': forest.state() if effect else None}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def roots(association=OWN):
    return [forest.render(label) for label, node in sorted(forest.nodes.items(), key=lambda item: item[1]['tree'])
            if node['parent'] is None and node['association'] == association]


LIST = '/folders/'
detail = lambda target, suffix='/': f'/folders/{ID(target) if target in forest.nodes else target}{suffix}'
NOT_FOUND = {'detail': 'No Folder matches the given query.'}
BARE = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
UNIQUE = {'non_field_errors': ['The fields name, parent, sport_association must make a unique set.']}
REQUIRED, NULL = 'This field is required.', 'This field may not be null.'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
method = lambda name: {'detail': f'Method "{name}" not allowed.'}
info = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    'label': label, **extra}
SERIALIZER = {'id': info('integer', 'ID', read_only=True), 'name': info('string', 'Name', True, max_length=255), 'parent': info('field', 'Parent'),
              'sport_association': info('field', 'Sport association', True), 'created_at': info('datetime', 'Created at'),
              'level': info('integer', 'Level', read_only=True), 'children': info('field', 'Children', read_only=True)}
OPTIONS = lambda suffix, actions=None: {'name': f'Folder {suffix}', 'description': '', 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
                                        **({'actions': actions} if actions else {})}
FORM = 'application/x-www-form-urlencoded'
BOUNDARY = 'routerfoldersboundary'
MULTIPART = f'multipart/form-data; boundary={BOUNDARY}'


def multipart(fields):
    return ''.join(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n' for key, value in fields) + f'--{BOUNDARY}--\r\n'


# The list route.
add('list-anonymous', 'anonymous', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('folders'))
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED)
add('list-owner-roots', 'owner', LIST, 200, {'data': roots(), 'path': None})
add('list-children', 'full_collaborator', f'{LIST}?folder={ID("a")}', 200,
    {'data': [forest.render('a1'), forest.render('a2')], 'path': [{'id': ID('a'), 'name': 'Archivio è'}]})
add('list-foreign-folder-denied', 'owner', f'{LIST}?folder={ID("f")}', 403, {'detail': 'Cannot access folder from different sport association.'})
add('list-foreign-owner', 'foreign_owner', LIST, 200, {'data': roots(FOREIGN), 'path': None})
add('list-athlete-server-error', 'athlete', LIST, 500, SERVER)
add('list-put-not-allowed', 'owner', LIST, 405, method('PUT'), method='PUT', body={})
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-options', 'owner', LIST, 200, OPTIONS('List', {'POST': SERIALIZER}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/folders.json', 500, SERVER)
add('list-format-unknown-not-found', 'owner', '/folders.txt/', 404, BARE)
# create (stock)
add('create-required', 'owner', LIST, 400, {'name': [REQUIRED], 'sport_association': [REQUIRED]}, method='POST', body={})
add('create-list-body', 'owner', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST',
    body=[])
add('create-null-association', 'owner', LIST, 400, {'sport_association': [NULL]}, method='POST', body={'name': 'X', 'sport_association': None})
add('create-unknown-association', 'owner', LIST, 400, {'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    method='POST', body={'name': 'X', 'sport_association': UNKNOWN})
add('create-foreign-association-forbidden', 'owner', LIST, 403, FORBIDDEN, method='POST', body={'name': 'X', 'sport_association': FOREIGN})
add('create-foreign-parent-forbidden', 'owner', LIST, 403, FORBIDDEN, method='POST',
    body={'name': 'X', 'parent': ID('f'), 'sport_association': OWN})
add('create-duplicate-child-name', 'owner', LIST, 400, UNIQUE, method='POST',
    body={'name': 'Verbali', 'parent': ID('a'), 'sport_association': OWN})
add('create-athlete-required', 'athlete', LIST, 400, {'name': [REQUIRED], 'sport_association': [REQUIRED]}, method='POST', body={})
add('create-athlete-server-error', 'athlete', LIST, 500, SERVER, method='POST', body={'name': 'X', 'sport_association': FOREIGN})
add('create-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='POST',
    body={'name': 'X', 'sport_association': FOREIGN})
forest.insert('n1', 'Statuto', 'a', OWN, '2026-10-10T10:00:00Z')
add('create-child-last', 'owner', LIST, 201, forest.render('n1'), method='POST',
    body={'name': ' Statuto ', 'parent': ID('a'), 'sport_association': OWN, 'created_at': '2026-10-10T10:00:00Z', 'level': 9}, effect=True)
forest.insert('n2', 'Da form', 'b', OWN, '2026-10-11T10:00:00Z')
add('create-form-body', 'full_collaborator', LIST, 201, forest.render('n2'), method='POST',
    raw_body=f'name=Da+form&parent={ID("b")}&sport_association={OWN}&created_at=2026-10-11T10:00:00Z', content_type=FORM, effect=True)
forest.insert('n3', 'Multipart', 'c', OWN, '2026-10-12T10:00:00Z')
add('create-multipart-body', 'owner', LIST, 201, forest.render('n3'), method='POST',
    raw_body=multipart([('name', 'Multipart'), ('parent', str(ID('c'))), ('sport_association', OWN), ('created_at', '2026-10-12T10:00:00Z')]),
    content_type=MULTIPART, effect=True)
forest.insert('n4', 'Archivio è', None, OWN, '2026-10-13T10:00:00Z')
add('create-format-json-root-duplicate-name', 'owner', '/folders.json', 201, forest.render('n4'), method='POST',
    body={'name': 'Archivio è', 'sport_association': OWN, 'created_at': '2026-10-13T10:00:00Z'}, effect=True)
forest.insert('n5', 'Adesso', None, OWN, '$now')
add('create-root-server-time', 'owner', LIST, 201, forest.render('n5'), method='POST', body={'name': 'Adesso', 'sport_association': OWN},
    effect=True)

# The detail route.
add('detail-anonymous', 'anonymous', detail('a'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', detail('a'), 403, unmapped(f'folders/{ID("a")}'))
add('retrieve-owner-nested', 'owner', detail('a'), 200, forest.render('a'))
add('retrieve-format-json', 'full_collaborator', detail('a1', '.json'), 200, forest.render('a1'))
add('retrieve-malformed-not-found', 'owner', detail('abc'), 404, BARE)
add('retrieve-unknown-not-found', 'owner', detail('999999'), 404, NOT_FOUND)
add('retrieve-out-of-range-not-found', 'owner', detail('2147483648'), 404, NOT_FOUND)
add('retrieve-foreign-not-found', 'owner', detail('f'), 404, NOT_FOUND)
add('retrieve-athlete-server-error', 'athlete', detail('a'), 500, SERVER)
add('detail-post-not-allowed', 'owner', detail('a'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('a'), 200, OPTIONS('Instance', {'PUT': SERIALIZER}), method='OPTIONS')
add('detail-options-foreign-no-actions', 'owner', detail('f'), 200, OPTIONS('Instance'), method='OPTIONS')
add('partial-update-server-error', 'owner', detail('a2'), 500, SERVER, method='PATCH', body={'name': 'Mai'})
add('update-format-json-server-error', 'owner', detail('a2', '.json'), 500, SERVER, method='PUT', body={'name': 'Mai'})
add('update-foreign-parent-forbidden', 'owner', detail('a2'), 403, FORBIDDEN, method='PUT', body={'parent': ID('f1')})
add('update-rename-duplicate-sibling', 'owner', detail('a2'), 400, UNIQUE, method='PUT', body={'name': 'Verbali'})
forest.nodes['a2']['name'] = 'Bilanci 2026'
forest.move('a2', 'c')
add('update-owner-put-is-partial', 'owner', detail('a2'), 200, forest.render('a2'), method='PUT',
    body={'name': 'Bilanci 2026', 'parent': ID('c')}, effect=True)
add('destroy-foreign-not-found', 'owner', detail('f'), 404, NOT_FOUND, method='DELETE')
add('destroy-malformed-not-found', 'owner', detail('abc'), 404, BARE, method='DELETE')
add('destroy-athlete-server-error', 'athlete', detail('b'), 500, SERVER, method='DELETE')
path = detail('b')
forest.delete('b')
add('destroy-owner-subtree', 'owner', path, 204, '<empty>', method='DELETE', effect=True)
add('destroy-again-not-found', 'owner', path, 404, NOT_FOUND, method='DELETE')
path = detail('a1', '.json')
forest.delete('a1')
add('destroy-format-json', 'full_collaborator', path, 204, '<empty>', method='DELETE', effect=True)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'DefaultRouter folders routes with MPTT effects', 'ids': IDS,
                              'first_id': FIRST, 'next_id': NEXT, 'new_ids': list(range(NEXT, forest.next_id)), 'folders': SEEDED,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
