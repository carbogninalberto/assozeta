"""Author the documents/<pk>/delete HTTP sequence from documents-archive.md.

instance.delete() removes only the archive row (its Document stays). The
audited row's delete entry lists every field against 'None' (a null folder
foreign key as ['None', 'None']) and is indexed through changes_json.
DELETE on the move route reaches the same delete(). Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('documents_delete_http.json')
LIST = json.loads(Path(__file__).with_name('documents_list_http.json').read_text())
IDS, FOLDERS, ARCHIVES = LIST['ids'], LIST['folders'], LIST['archives']
ARCHIVE = {item['label']: item for item in ARCHIVES}
path = lambda label, action='delete': f'/documents/{ARCHIVE[label]["id"] if label in ARCHIVE else label}/{action}'
cases = []


def add(name, actor, method, target, status, response, *, action='delete', effect=None):
    cases.append({'name': f'documents-delete-{name}', 'actor': actor, 'method': method, 'path': path(target, action), 'headers': {},
                  'status': status, 'response': response, 'effect': effect})


def deleted(label):
    item = ARCHIVE[label]
    folder = f'$folder:{item["folder"]}' if item['folder'] else 'None'
    return {'archive': item['id'], 'changes': {'sport_association_documents_archive_id': [item['id'], 'None'],
            'sport_association': [item['association'], 'None'], 'document': [item['document'], 'None'], 'date': [item['date'], 'None'],
            'folder': [folder, 'None']}}


add('restricted-missing-permission', 'restricted_collaborator', 'DELETE', 'root2', 403, {'detail': 'Missing permission: association.archive.update'})
add('athlete-server-error', 'athlete', 'DELETE', 'root2', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 'DELETE', 'root2', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'DELETE', 'root2', 401, {'detail': 'Authentication credentials were not provided.'})
add('foreign-not-found', 'owner', 'DELETE', 'foreign', 404, {'detail': 'No SportAssociationDocumentsArchive matches the given query.'})
add('malformed-not-found', 'owner', 'DELETE', 'not-a-uuid', 404, {'detail': 'Not found.'})
add('wrong-method', 'owner', 'GET', 'root2', 405, {'detail': 'Method "GET" not allowed.'})
add('options', 'owner', 'OPTIONS', 'root2', 200, {'name': 'Document Archive', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json']})
add('owner-deletes-root-archive', 'owner', 'DELETE', 'root2', 204, '<empty>', effect=deleted('root2'))
add('repeat-not-found', 'owner', 'DELETE', 'root2', 404, {'detail': 'No SportAssociationDocumentsArchive matches the given query.'})
add('delete-through-move-route', 'full_collaborator', 'DELETE', 'in_a', 204, '<empty>', action='move', effect=deleted('in_a'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Documents archive delete authored sequence with audit effects', 'ids': IDS,
                              'folders': FOLDERS, 'archives': ARCHIVES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
