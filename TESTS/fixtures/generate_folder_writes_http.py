"""Author the folders/add and folders/<pk>/update HTTP sequence (folders.md).

FolderViewSet (BE/application/views/archive_views.py), FolderSerializer
(fields id, name, parent, sport_association, created_at, read-only level and
recursive children) on the MPTT Folder model (unique_together name, parent,
sport_association; not audited). DRF 3.17, django-mptt 0.16, Django 5.2.

add: SportAssociation.objects.get(user=request.user) first (athletes and
owners without an association: 500, even before the body is parsed), then
``request.data['sport_association'] = ...``: a JSON object (or an empty body)
is mutable; a list, null or string body raises TypeError and a form or a
multipart body without files is an immutable QueryDict (500); a multipart
body with a file is a mutable copy and validates as HTML input. Validation:
name CharField(255) required; parent a tenant-scoped pk field with
default None (unique_together nullable field); sport_association always the
owner's; created_at optional DateTimeField. UniqueTogetherValidator skips a
create whose parent is None (duplicate root names are allowed). The save is
MPTT insert_node 'last-child' (a root takes MAX(tree_id)+1 over every
folder). 201 with the serialized folder (children []).

update: get_object() (association lookup, then a bare 404 for a pk int()
rejects and the model 404 for unknown, out-of-range or foreign pks) before
the body; partial FolderSerializer. On update the unique validator fills
missing fields from the instance and checks only changed values; an
unchanged None parent filters IS NULL, so renaming a root to another root's
name is a 400, while making a child a root (a None among the changed values)
is never checked. MPTTModel.save moves a changed parent: 'last-child' of the
new parent (within the tree, to another tree, or a root into another tree,
whose tree id is left unused), or a new root after the rightmost root
(MAX(tree_id)+1); a move into itself or a descendant raises InvalidMove
before any write (500). 200 with the folder and its nested children.

The viewset's own delete() answers DELETE on both routes: no pk on add (500),
the folder's subtree on update (204). Folder ids are fixed: the runner seeds
these folders with their ids and sets the folder sequence so new folders get
980101, 980102, ... Expected trees come from the logical forest below
(children in order, numbered depth-first per tree), which is what the MPTT
SQL keeps; tree ids are relative to the runner's offset.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('folder_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
UNKNOWN = 'a1000000-0000-4000-8000-000000989999'
FIRST, NEXT = 980001, 980101
SERVER = '<stable-server-error-page>'
# label, name, parent label, association, created_at (DRF rendering)
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


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=False):
    case = {'name': f'folder-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'state': forest.state() if effect else None}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ADD = '/folders/add'
update = lambda target: f'/folders/{ID(target) if target in forest.nodes else target}/update'
NOT_FOUND = {'detail': 'No Folder matches the given query.'}
MALFORMED = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
MISSING = {'detail': 'Missing permission: association.archive.update'}
UNIQUE = {'non_field_errors': ['The fields name, parent, sport_association must make a unique set.']}
DATETIME = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
REQUIRED, NULL, BLANK, STRING = 'This field is required.', 'This field may not be null.', 'This field may not be blank.', 'Not a valid string.'
incorrect = lambda kind: f'Incorrect type. Expected pk value, received {kind}.'
missing_pk = lambda value: f'Invalid pk "{value}" - object does not exist.'
JSON_ERROR = {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}
OPTIONS = {'name': 'Folder', 'description': '', 'renders': ['application/json'],
           'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
info = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    'label': label, **extra}
# add allows POST, so SimpleMetadata describes FolderSerializer's fields.
ADD_OPTIONS = {**OPTIONS, 'actions': {'POST': {
    'id': info('integer', 'ID', read_only=True), 'name': info('string', 'Name', True, max_length=255), 'parent': info('field', 'Parent'),
    'sport_association': info('field', 'Sport association', True), 'created_at': info('datetime', 'Created at'),
    'level': info('integer', 'Level', read_only=True), 'children': info('field', 'Children', read_only=True)}}}
BOUNDARY = 'folderwritesboundary'


def multipart(fields, files=()):
    parts = [f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n' for key, value in fields]
    parts += [f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'
              f'{content}\r\n' for key, filename, content in files]
    return ''.join(parts) + f'--{BOUNDARY}--\r\n'


MULTIPART = f'multipart/form-data; boundary={BOUNDARY}'
FORM = 'application/x-www-form-urlencoded'

# add: authorization, methods and the association lookup before the body.
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body={})
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, MISSING, body={'name': 'X'})
# DRF checks permissions before dispatching the method.
add('add-restricted-wrong-method-permission-first', 'restricted_collaborator', ADD, 403, MISSING, method='GET')
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body={'name': 'X'}, headers=SCOPED)
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, ADD_OPTIONS, method='OPTIONS')
add('add-route-delete-without-pk-server-error', 'owner', ADD, 500, SERVER, method='DELETE')
add('add-athlete-server-error', 'athlete', ADD, 500, SERVER, body={'name': 'X'})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, body={'name': 'X'})
add('add-athlete-malformed-json-server-error', 'athlete', ADD, 500, SERVER, raw_body='{', content_type='application/json')
add('add-malformed-json', 'owner', ADD, 400, JSON_ERROR, raw_body='{', content_type='application/json')
add('add-unsupported-media-type', 'owner', ADD, 415, {'detail': 'Unsupported media type "text/plain" in request.'},
    raw_body='name=X', content_type='text/plain')
# request.data['sport_association'] = ... on a non-dict body or an immutable QueryDict.
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[])
add('add-null-body-server-error', 'owner', ADD, 500, SERVER, raw_body='null', content_type='application/json')
add('add-form-body-server-error', 'owner', ADD, 500, SERVER, raw_body='name=Modulo', content_type=FORM)
add('add-multipart-without-file-server-error', 'owner', ADD, 500, SERVER, raw_body=multipart([('name', 'Modulo')]), content_type=MULTIPART)
# Validation.
add('add-missing-name', 'owner', ADD, 400, {'name': [REQUIRED]}, body={})
add('add-empty-json-body', 'owner', ADD, 400, {'name': [REQUIRED]}, raw_body='', content_type='application/json')
add('add-invalid-strings', 'owner', ADD, 400, {'name': [BLANK], 'parent': [incorrect('str')], 'created_at': [DATETIME]},
    body={'name': '   ', 'parent': 'abc', 'created_at': 'ieri'})
add('add-invalid-booleans', 'owner', ADD, 400, {'name': [STRING], 'parent': [incorrect('bool')], 'created_at': [NULL]},
    body={'name': True, 'parent': True, 'created_at': None})
add('add-invalid-collections', 'owner', ADD, 400, {'name': [STRING], 'parent': [incorrect('list')], 'created_at': [DATETIME]},
    body={'name': ['x'], 'parent': [], 'created_at': 5})
add('add-invalid-limits', 'owner', ADD, 400, {'name': ['Ensure this field has no more than 255 characters.'], 'parent': [incorrect('dict')],
    'created_at': ['Datetime value out of range.']}, body={'name': 'x' * 256, 'parent': {}, 'created_at': '0001-01-01T00:00:00+01:00'})
add('add-null-name-unknown-parent', 'owner', ADD, 400, {'name': [NULL], 'parent': [missing_pk(999999)]},
    body={'name': None, 'parent': 999999})
# Out-of-range integers find nothing (Django's IntegerFieldOverflow lookup).
add('add-null-character-out-of-range-parent', 'owner', ADD, 400, {'name': ['Null characters are not allowed.'],
    'parent': [missing_pk('2147483648')]}, body={'name': 'a\x00b', 'parent': '2147483648'})
add('add-huge-parent', 'owner', ADD, 400, {'parent': [missing_pk('99999999999999999999')]},
    body={'name': 'Grande', 'parent': 99999999999999999999})
add('add-invalid-datetime-day', 'owner', ADD, 400, {'created_at': [DATETIME]}, body={'name': 'Data', 'created_at': '2026-02-30T10:00:00'})
# tenant_scope: a foreign parent escapes the collected field errors with DRF's generic 403.
add('add-foreign-parent-forbidden', 'owner', ADD, 403, FORBIDDEN, body={'name': '', 'parent': ID('f')})
add('add-duplicate-child-name', 'owner', ADD, 400, UNIQUE, body={'name': ' Verbali ', 'parent': ID('a')})
# Effects: MPTT inserts as the parent's last child or as a new tree.
forest.insert('s', 'Statuto', 'a', OWN, '2026-10-10T10:00:00Z')
add('add-child-last-ignores-read-only', 'owner', ADD, 201, forest.render('s'),
    body={'name': '  Statuto  ', 'parent': ID('a'), 'created_at': '2026-10-10T10:00:00Z', 'id': 1, 'level': 7, 'children': ['x']}, effect=True)
forest.insert('n2', '5', 'a2', OWN, '2026-01-02T01:04:05.500000Z')
add('add-integer-name-string-parent-offset-datetime', 'owner', ADD, 201, forest.render('n2'),
    body={'name': 5, 'parent': f' {ID("a2")} ', 'created_at': '2026-01-02T03:04:05.5+02:00'}, effect=True)
forest.insert('n3', 'Contratti', 'c', OWN, '2026-03-04T05:06:00Z')
add('add-float-parent-naive-datetime', 'owner', ADD, 201, forest.render('n3'),
    body={'name': 'Contratti', 'parent': float(ID('c')), 'created_at': '2026-03-04T05:06'}, effect=True)
# The view overwrites sport_association; a None parent skips the unique validator.
forest.insert('n4', 'Archivio è', None, OWN, '$now')
add('add-root-duplicate-name-association-overwritten', 'owner', ADD, 201, forest.render('n4'),
    body={'name': 'Archivio è', 'sport_association': FOREIGN}, effect=True)
forest.insert('n5', 'Collaboratore', None, OWN, '2026-04-05T06:07:08.000009Z')
add('add-full-collaborator-null-parent', 'full_collaborator', ADD, 201, forest.render('n5'),
    body={'name': 'Collaboratore', 'parent': None, 'created_at': '2026-04-05T06:07:08.000009Z'}, effect=True)
forest.insert('n6', 'Allegati', 'b', OWN, '$now')
add('add-multipart-with-file', 'owner', ADD, 201, forest.render('n6'),
    raw_body=multipart([('name', 'Allegati'), ('parent', str(ID('b'))), ('created_at', '')], [('file', 'nota.txt', 'ciao')]),
    content_type=MULTIPART, effect=True)
forest.insert('n7', 'Radice', None, OWN, '2026-05-01T00:00:00Z')
add('add-empty-parent-is-root', 'owner', ADD, 201, forest.render('n7'),
    body={'name': 'Radice', 'parent': '', 'created_at': '2026-05-01T00:00:00Z'}, effect=True)

# update: authorization, methods, get_object before the body.
add('update-anonymous', 'anonymous', update('a2'), 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', update('a2'), 403, MISSING, method='PATCH', body={'name': 'X'})
add('update-owner-scoped-athlete-denied', 'owner', update('a2'), 403, SCOPED_DENIED, method='PATCH', body={'name': 'X'}, headers=SCOPED)
add('update-wrong-method', 'owner', update('a2'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('update-options', 'owner', update('a2'), 200, OPTIONS, method='OPTIONS')
add('update-malformed-pk-not-found', 'owner', update('abc'), 404, MALFORMED, method='PATCH', body={'name': 'X'})
add('update-unknown-pk-not-found', 'owner', update('999999'), 404, NOT_FOUND, method='PATCH', body={'name': 'X'})
add('update-out-of-range-pk-not-found', 'owner', update('2147483648'), 404, NOT_FOUND, method='PATCH', body={'name': 'X'})
add('update-foreign-pk-not-found', 'owner', update('f'), 404, NOT_FOUND, method='PATCH', body={'name': 'X'})
add('update-athlete-server-error', 'athlete', update('a2'), 500, SERVER, method='PATCH', body={'name': 'X'})
add('update-owner-without-association-server-error', 'owner_without_association', update('a2'), 500, SERVER, method='PATCH', body={})
add('update-unknown-pk-malformed-json-not-found', 'owner', update('999999'), 404, NOT_FOUND, method='PATCH', raw_body='{',
    content_type='application/json')
add('update-malformed-json', 'owner', update('a2'), 400, JSON_ERROR, method='PATCH', raw_body='{', content_type='application/json')
add('update-list-body', 'owner', update('a2'), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PATCH', body=[])
add('update-null-body', 'owner', update('a2'), 400, {'non_field_errors': ['No data provided']}, method='PATCH', raw_body='null',
    content_type='application/json')
add('update-invalid-strings', 'owner', update('a2'), 400, {'name': [BLANK], 'parent': [incorrect('str')],
    'sport_association': ['“abc” is not a valid UUID.'], 'created_at': [DATETIME]}, method='PATCH',
    body={'name': ' ', 'parent': 'x', 'sport_association': 'abc', 'created_at': 'x'})
add('update-null-values', 'owner', update('a2'), 400, {'name': [NULL], 'parent': [missing_pk(999999)], 'sport_association': [NULL],
    'created_at': [NULL]}, method='PATCH', body={'name': None, 'parent': 999999, 'sport_association': None, 'created_at': None})
add('update-empty-association', 'owner', update('a2'), 400, {'sport_association': [NULL]}, method='PATCH', body={'sport_association': ''})
add('update-unknown-association', 'owner', update('a2'), 400, {'sport_association': [missing_pk(UNKNOWN)]}, method='PATCH',
    body={'sport_association': UNKNOWN})
add('update-foreign-association-forbidden', 'owner', update('a2'), 403, FORBIDDEN, method='PATCH',
    body={'name': '', 'sport_association': FOREIGN})
# The related field's queryset is SportAssociation._default_manager (original_objects): a deleted association exists.
add('update-deleted-association-forbidden', 'owner', update('a2'), 403, FORBIDDEN, method='PATCH',
    body={'sport_association': IDS['deleted_association']})
add('update-foreign-parent-forbidden', 'owner', update('a2'), 403, FORBIDDEN, method='PATCH', body={'parent': ID('f1')})
# The unique validator on update: an unchanged None parent filters IS NULL.
add('update-rename-duplicate-root', 'owner', update('b'), 400, UNIQUE, method='PATCH', body={'name': 'Archivio è'})
add('update-rename-duplicate-sibling', 'owner', update('a2'), 400, UNIQUE, method='PATCH', body={'name': 'Verbali'})
add('update-move-into-name-conflict', 'owner', update('b1'), 400, UNIQUE, method='PATCH', body={'parent': ID('a')})
# mptt InvalidMove before any write.
add('update-child-into-itself-server-error', 'owner', update('a1'), 500, SERVER, method='PATCH', body={'parent': ID('a1')})
add('update-child-into-descendant-server-error', 'owner', update('a1'), 500, SERVER, method='PATCH', body={'parent': ID('a1a')})
add('update-root-into-itself-server-error', 'owner', update('c'), 500, SERVER, method='PATCH', body={'parent': ID('c')})
add('update-root-into-own-tree-server-error', 'owner', update('a'), 500, SERVER, method='PATCH', body={'parent': ID('a1a')})
# Effects.
add('update-empty-body-no-change', 'owner', update('a2'), 200, forest.render('a2'), method='PATCH', body={}, effect=True)
add('update-same-values-no-change', 'owner', update('a1'), 200, forest.render('a1'), method='PATCH',
    body={'name': 'Verbali', 'parent': ID('a'), 'sport_association': OWN}, effect=True)
forest.nodes['a1'].update(name='Verbali 2026', created='2026-11-01T13:30:00Z')
add('update-rename-created-at-ignores-read-only', 'owner', update('a1'), 200, forest.render('a1'), method='PATCH',
    body={'name': ' Verbali 2026 ', 'created_at': '2026-11-01T12:00:00-01:30', 'id': 5, 'level': 3, 'children': []}, effect=True)
# Within the tree, to a target on the right (target rght > node rght) and on the left.
forest.move('a1', 'a2')
add('update-child-within-tree-rightward', 'owner', update('a1'), 200, forest.render('a1'), method='PATCH', body={'parent': ID('a2')},
    effect=True)
forest.move('s', 'a2')
add('update-child-within-tree-leftward', 'owner', update('s'), 200, forest.render('s'), method='PATCH', body={'parent': str(ID('a2'))},
    effect=True)
# A root with its subtree into another tree; its tree id stays unused.
forest.move('c', 'b1')
add('update-root-into-other-tree', 'owner', update('c'), 200, forest.render('c'), method='PATCH', body={'parent': ID('b1')}, effect=True)
# A child made root takes MAX(tree_id)+1; the unique validator ignores a None parent change.
forest.move('n3', None)
add('update-child-to-root-duplicate-root-name', 'owner', update('n3'), 200, forest.render('n3'), method='PATCH', body={'parent': None},
    effect=True)
forest.move('a1', 'n5')
add('update-child-to-other-tree', 'owner', update('a1'), 200, forest.render('a1'), method='PATCH', body={'parent': ID('n5')}, effect=True)
# HTML input: an empty parent is None and an empty created_at is skipped.
forest.move('s', None)
forest.nodes['s']['name'] = 'Moduli'
add('update-form-rename-to-root', 'owner', update('s'), 200, forest.render('s'), method='PATCH',
    raw_body='name=Moduli&parent=&created_at=', content_type=FORM, effect=True)
forest.nodes['b1']['name'] = 'Verbali B'
add('update-full-collaborator-rename', 'full_collaborator', update('b1'), 200, forest.render('b1'), method='PATCH',
    body={'name': 'Verbali B'}, effect=True)
# Folder has no group-aware manager: X-Group-Id changes nothing. A None parent on a root keeps the tree.
add('update-root-null-parent-group-header-ignored', 'owner', update('n7'), 200, forest.render('n7'), method='PATCH',
    body={'parent': None, 'name': 'Radice'}, headers={'X-Group-Id': IDS['own_group']}, effect=True)
# The viewset's delete() on the update route deletes the folder's subtree.
add('update-route-delete-restricted', 'restricted_collaborator', update('a'), 403, MISSING, method='DELETE')
add('update-route-delete-foreign-not-found', 'owner', update('f'), 404, NOT_FOUND, method='DELETE')
path = update('a')
forest.delete('a')
add('update-route-delete-deletes-subtree', 'owner', path, 204, '<empty>', method='DELETE', effect=True)
add('update-deleted-not-found', 'owner', f'/folders/{FIRST}/update', 404, NOT_FOUND, method='PATCH', body={})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Folder add and update authored sequence with MPTT effects', 'ids': IDS,
                              'first_id': FIRST, 'next_id': NEXT, 'new_ids': list(range(NEXT, forest.next_id)), 'folders': SEEDED, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
