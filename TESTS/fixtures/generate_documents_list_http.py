"""Author the documents/list HTTP sequence from documents-archive.md.

The runner creates folders and archive rows (with their documents) through
the ORM. Rows are compared unordered (the queryset has no ordering) within
pages of 40. ``$folder:<label>`` binds folder IDs. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('documents_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/documents/list'
FOLDERS = [('a', None, OWN), ('f', None, FOREIGN)]
ids = iter(f'a1000000-0000-4000-8000-{840001 + n:012d}' for n in range(40))


def archive(label, association, folder, filename, date, *, size=None, filepath=None):
    return {'label': label, 'id': next(ids), 'document': next(ids), 'token': next(ids), 'association': association, 'folder': folder,
            'filename': filename, 'date': date, 'created': '2026-10-01T08:00:00.500000Z', 'size': size, 'filepath': filepath}


ARCHIVES = [archive('root1', OWN, None, 'statuto è.pdf', '2026-09-01', size=1234),
            archive('root2', OWN, None, 'verbale.pdf', '2026-09-02', filepath='docs/verbale.pdf'),
            archive('in_a', OWN, 'a', 'bilancio.pdf', '2026-09-03', size=0),
            archive('foreign', FOREIGN, None, 'estero.pdf', '2026-09-04')]


def row(item):
    return {'sport_association_documents_archive_id': item['id'], 'sport_association': item['association'],
            'document': {'document_id': item['document'], 'creation_date': item['created'], 'filepath': item['filepath'],
                         'filename': item['filename'], 'file_size_bytes': item['size'], 'token': item['token']},
            'date': item['date'], 'folder': f'$folder:{item["folder"]}' if item['folder'] else None}


def page(labels, *, number=0, total=None):
    rows = [row(item) for item in ARCHIVES if item['label'] in labels]
    count = len(rows) if total is None else total
    return {'data': rows, 'pagination': {'page': number, 'total_pages': -(-count // 40), 'total_items': count,
                                         'has_next': (number + 1) * 40 < count}}


cases = []


def add(name, actor, status, response, *, query='', method='GET', headers=None):
    cases.append({'name': f'documents-list-{name}', 'actor': actor, 'method': method, 'path': PATH + query, 'headers': headers or {},
                  'status': status, 'response': response})


add('owner-root', 'owner', 200, page({'root1', 'root2'}))
add('owner-empty-folder-root', 'owner', 200, page({'root1', 'root2'}), query='?folder=')
add('owner-folder', 'owner', 200, page({'in_a'}), query='?folder=$folder:a')
add('owner-second-page-empty', 'owner', 200, {'data': [], 'pagination': {'page': 1, 'total_pages': 1, 'total_items': 2, 'has_next': False}},
    query='?page=1')
add('full-collaborator-owner-scope', 'full_collaborator', 200, page({'root1', 'root2'}))
add('foreign-owner-root', 'foreign_owner', 200, page({'foreign'}))
add('foreign-folder-denied', 'owner', 403, {'detail': 'Cannot access folder from different sport association.'}, query='?folder=$folder:f')
add('missing-folder-server-error', 'owner', 500, '<stable-server-error-page>', query='?folder=999999999')
add('non-integer-folder-server-error', 'owner', 500, '<stable-server-error-page>', query='?folder=abc')
add('non-integer-page-server-error', 'owner', 500, '<stable-server-error-page>', query='?page=x')
add('negative-page-server-error', 'owner', 500, '<stable-server-error-page>', query='?page=-1')
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.archive.read'})
add('athlete-server-error', 'athlete', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('delete-method-server-error', 'owner', 500, '<stable-server-error-page>', method='DELETE')
add('options', 'owner', 200, {'name': 'Document Archive', 'description': '', 'renders': ['application/json'], 'parses': ['application/json']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Documents archive list authored sequence', 'ids': IDS, 'folders': FOLDERS,
                              'archives': ARCHIVES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
