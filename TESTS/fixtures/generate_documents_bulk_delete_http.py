"""Author the documents/bulk-delete HTTP sequence from documents-archive.md.

The view loads the requested archive rows across associations, requires one
row per requested ID (duplicates or unknown IDs: 400), refuses foreign rows
(403), then in one transaction deletes the archive rows and their Documents.
Both models are audited: archive deletes index through changes_json, Document
deletes (no resolver, no association field) through the actor's association.
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('documents_bulk_delete_http.json')
LIST = json.loads(Path(__file__).with_name('documents_list_http.json').read_text())
IDS, FOLDERS, ARCHIVES = LIST['ids'], LIST['folders'], LIST['archives']
ARCHIVE = {item['label']: item for item in ARCHIVES}
PATH = '/documents/bulk-delete'
cases = []


def add(name, actor, status, response, *, body=None, method='DELETE', effect=None):
    case = {'name': f'documents-bulk-delete-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    cases.append(case)


def deleted(*labels):
    return {'archives': [ARCHIVE[label]['id'] for label in labels], 'documents': [ARCHIVE[label]['document'] for label in labels],
            'archive_changes': {ARCHIVE[label]['id']: {'sport_association_documents_archive_id': [ARCHIVE[label]['id'], 'None'],
                                'sport_association': [ARCHIVE[label]['association'], 'None'], 'document': [ARCHIVE[label]['document'], 'None'],
                                'date': [ARCHIVE[label]['date'], 'None'],
                                'folder': [f'$folder:{ARCHIVE[label]["folder"]}' if ARCHIVE[label]['folder'] else 'None', 'None']}
                                for label in labels},
            'document_changes': {ARCHIVE[label]['document']: {key: [value, 'None'] for key, value in (
                ('document_id', ARCHIVE[label]['document']), ('creation_date', ARCHIVE[label]['created'].rstrip('Z').replace('T', ' ')),
                ('filepath', ARCHIVE[label]['filepath']), ('filename', ARCHIVE[label]['filename']),
                ('file_size_bytes', None if ARCHIVE[label]['size'] is None else str(ARCHIVE[label]['size'])),
                ('token', ARCHIVE[label]['token'])) if value is not None}
                # auditlog walks _meta.get_fields() on delete: the reverse FK with an
                # explicit related_name renders as its empty manager.
                | {'customer_invoice_pdf': ['application.CustomerInvoice.None', 'None']} for label in labels}}


EMPTY = {'exception': 'No instances provided'}
add('missing-files', 'owner', 400, EMPTY, body={})
add('empty-files', 'owner', 400, EMPTY, body={'files': []})
add('list-body-server-error', 'owner', 500, '<stable-server-error-page>', body=[])
add('invalid-id-server-error', 'owner', 500, '<stable-server-error-page>', body={'files': ['nope']})
add('unknown-id', 'owner', 400, {'exception': 'Some instances not found'}, body={'files': [ARCHIVE['root2']['id'], IDS['missing']]})
add('duplicate-id', 'owner', 400, {'exception': 'Some instances not found'}, body={'files': [ARCHIVE['root2']['id'], ARCHIVE['root2']['id']]})
add('foreign-included', 'owner', 403, {'detail': 'User not allowed to delete some of these instances.'},
    body={'files': [ARCHIVE['root2']['id'], ARCHIVE['foreign']['id']]})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.archive.update'},
    body={'files': [ARCHIVE['root2']['id']]})
add('athlete-server-error', 'athlete', 500, '<stable-server-error-page>', body={'files': [ARCHIVE['root2']['id']]})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'files': [ARCHIVE['root2']['id']]})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body={'files': [ARCHIVE['root2']['id']]})
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Document Archive', 'description': '', 'renders': ['application/json'], 'parses': ['application/json']},
    method='OPTIONS')
add('owner-deletes-two', 'owner', 204, '<empty>', body={'files': [ARCHIVE['root1']['id'], ARCHIVE['in_a']['id']]}, effect=deleted('root1', 'in_a'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Documents archive bulk delete authored sequence with audit effects', 'ids': IDS,
                              'folders': FOLDERS, 'archives': ARCHIVES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
