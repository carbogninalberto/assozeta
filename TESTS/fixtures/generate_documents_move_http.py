"""Author the documents/<pk>/move HTTP sequence from documents-archive.md.

The view assigns the raw request value to folder_id and saves; auditlog
diffs the raw foreign key value (smart_str(strings_only=True) keeps ints
and None), so a string ID differs from the stored integer even when equal.
Effects name the archive row's stored folder and the audit changes (or
none). ``$folder:<label>`` binds folder IDs. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('documents_move_http.json')
LIST = json.loads(Path(__file__).with_name('documents_list_http.json').read_text())
IDS, FOLDERS, ARCHIVES = LIST['ids'], LIST['folders'], LIST['archives']
ARCHIVE = {item['label']: item for item in ARCHIVES}
path = lambda label: f'/documents/{ARCHIVE[label]["id"] if label in ARCHIVE else label}/move'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, archive, status, response, *, body=None, raw_body=None, content_type=None, method='POST', effect=None):
    case = {'name': f'documents-move-{name}', 'actor': actor, 'method': method, 'path': path(archive), 'headers': {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def moved(archive, folder, changes):
    return {'archive': ARCHIVE[archive]['id'], 'folder': folder, 'changes': changes}


add('restricted-missing-permission', 'restricted_collaborator', 'root1', 403, {'detail': 'Missing permission: association.archive.update'},
    body={'folder': '$folder:a'})
add('athlete-server-error', 'athlete', 'root1', 500, SERVER, body={})
add('admin-without-session', 'admin', 'root1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('anonymous', 'anonymous', 'root1', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('foreign-archive-not-found', 'owner', 'foreign', 404, {'detail': 'No SportAssociationDocumentsArchive matches the given query.'}, body={})
add('malformed-archive-not-found', 'owner', 'not-a-uuid', 404, {'detail': 'Not found.'}, body={})
add('foreign-folder-denied', 'owner', 'root1', 403, {'detail': 'Cannot move to folder from different sport association.'},
    body={'folder': '$folder:f'})
add('missing-folder-server-error', 'owner', 'root1', 500, SERVER, body={'folder': 999999999})
add('non-integer-folder-server-error', 'owner', 'root1', 500, SERVER, body={'folder': 'abc'})
add('list-body-server-error', 'owner', 'root1', 500, SERVER, body=[])
add('form-body-unsupported', 'owner', 'root1', 415, {'detail': 'Unsupported media type "application/x-www-form-urlencoded" in request.'},
    raw_body='folder=1', content_type='application/x-www-form-urlencoded')
add('into-folder', 'owner', 'root1', 200, '<empty>', body={'folder': '$folder:a'},
    effect=moved('root1', 'a', {'folder': ['None', '$folder:a']}))
# The stored integer and an equal string differ for auditlog; the row does not change.
add('same-folder-as-string', 'owner', 'root1', 200, '<empty>', body={'folder': '$folder-text:a'},
    effect=moved('root1', 'a', {'folder': ['$folder:a', '$folder:a']}))
add('same-folder-as-integer', 'full_collaborator', 'root1', 200, '<empty>', body={'folder': '$folder:a'}, effect=moved('root1', 'a', None))
add('to-root', 'owner', 'root1', 200, '<empty>', body={'folder': None}, effect=moved('root1', None, {'folder': ['$folder:a', 'None']}))
add('falsy-to-root', 'owner', 'in_a', 200, '<empty>', body={'folder': 0}, effect=moved('in_a', None, {'folder': ['$folder:a', 'None']}))
add('root-to-root', 'owner', 'root2', 200, '<empty>', body={}, effect=moved('root2', None, None))
add('wrong-method', 'owner', 'root1', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
info = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    'label': label, **extra}
document = {'document_id': info('string', 'Document id'), 'creation_date': info('datetime', 'Creation date'),
            'filepath': info('string', 'Filepath', max_length=1000), 'filename': info('string', 'Filename', True, max_length=255),
            'file_size_bytes': info('integer', 'File size bytes', min_value=0, max_value=9223372036854775807), 'token': info('string', 'Token')}
add('options', 'owner', 'root1', 200, {'name': 'Document Archive', 'description': '', 'renders': ['application/json'], 'parses': ['application/json'],
    'actions': {'POST': {'sport_association_documents_archive_id': info('string', 'Sport association documents archive id'),
                         'sport_association': info('field', 'Sport association', True),
                         'document': {'type': 'field', 'required': False, 'read_only': True, 'label': 'Document',
                                      'child': {'type': 'nested object', 'required': False, 'read_only': True, 'children': document}},
                         'date': info('date', 'Date'), 'folder': info('field', 'Folder')}}}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Documents archive move authored sequence with audit effects', 'ids': IDS,
                              'folders': FOLDERS, 'archives': ARCHIVES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
