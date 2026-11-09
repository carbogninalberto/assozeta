"""Author the association-transfer HTTP sequence (association-transfer.md).

AssociationExportViewSet and AssociationImportViewSet in
BE/application/views/export_views.py, in Django's order:

- export/* (IsAuthenticated): _check_export_permission (role ASSOCIATION and
  not request.collaborator) answers 403 {'error': ...}; then
  request.user.sport_association (User's cached property:
  SportAssociation.objects, not deleted; DoesNotExist answers 404).
  - active: the association's active/last export snapshots in the Django
    cache; with neither, {'active': False}.
  - list: SportAssociationDocumentsArchive rows of the association whose
    document filename starts with 'export_', newest date first, at most 20;
    each row carries a signed download token (django.core.signing, salt
    docmanager.association-export-download). A null file size with a
    filepath asks the storage backend (not in this sequence).
  - status: task_id is required (400) before the association lookup; the
    task owner entry in the cache decides 404 'Export non trovato'.
  - delete: request.data.get('document_id') runs first (a list body is a
    500 page), then the permission, the 400 for a falsy document_id, the
    association and the archive lookup (UUIDField: ints become UUID(int=n),
    a malformed string is a 500 page).
  - start: the export count (>= 3 is 409) and cache.add on the active key
    (an existing key is 409) before the Celery dispatch.
- import/* (SetupTokenOrAuthenticated with an InstanceConfiguration whose
  primary association is the own one: only its owner or a superuser;
  others 403 'Setup token missing or invalid.', anonymous 401). Parsers are
  multipart and form only (a JSON body is 415 when request.FILES is read).
  - status: task_id required, then AsyncResult on the django-db result
    backend (no row is PENDING; SUCCESS carries the decoded JSON result).
  - validate / start: request.FILES['file'] missing is 400 'File ZIP richiesto'.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('association_transfer_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
TOKEN = '$download-token'


def uid(n):
    return f'a1000000-0000-4000-8000-{976000 + n:012d}'


# --- seeded rows --------------------------------------------------------------------------------
# 21 own exports with distinct dates (2026-01-01 .. 2026-01-21); the oldest is past the 20-row limit.
DOCUMENTS, ARCHIVES = {}, {}


def document(key, association, filename, *, day, created, filepath='exports/archive.zip', size=1024):
    DOCUMENTS[key] = {'id': uid(len(DOCUMENTS) + 1), 'filename': filename, 'filepath': filepath, 'size': size, 'created': created}
    ARCHIVES[key] = {'id': uid(500 + len(ARCHIVES) + 1), 'association': association, 'document': key, 'date': f'2026-01-{day:02d}'}


for day in range(1, 22):
    size = {5: None, 6: None, 7: 0, 8: 2 ** 40}.get(day, 1000 + day)
    filepath = {5: None, 6: ''}.get(day, f'exports/own/export_{day:02d}.zip')
    created = f'2026-01-{day:02d}T08:30:00' + ('.123456' if day % 2 else '') + '+00:00'
    document(f'own{day}', 'own_association', f'export_own_{day:02d}.zip', day=day, created=created, filepath=filepath, size=size)
document('own_report', 'own_association', 'report.pdf', day=25, created='2026-01-25T10:00:00+00:00')
document('own_upper', 'own_association', 'Export_upper.zip', day=26, created='2026-01-26T10:00:00+00:00')
document('foreign1', 'foreign_association', 'export_foreign.zip', day=3, created='2026-01-03T09:00:00+00:00', size=77)

# The foreign association has an export in progress: its active cache key exists.
ACTIVE_KEYS = {'foreign_association': {'task_id': uid(900), 'sport_association_id': FOREIGN, 'user_id': IDS['foreign_owner'],
                                       'status': 'PENDING', 'ready': False, 'estimate': '0%',
                                       'progress': {'percent': 0, 'phase': 'queued'}, 'updated_at': '2026-01-01T00:00:00+00:00'}}

TASKS = {
    'started': {'task_id': uid(800), 'status': 'STARTED', 'result': '{"pid": 7, "hostname": "worker"}'},
    'progress': {'task_id': 'import-progress-task', 'status': 'PROGRESS', 'result': '{"current": 3}'},
    'success': {'task_id': uid(801), 'status': 'SUCCESS',
                'result': '{"success": true, "counts": {"users": 3, "courses": 12}, "warnings": ["a", "b"], "ratio": 1.5, '
                          '"big": 12345678901234567890, "nested": [null, {"x": "\\u00e8"}]}'},
    'success_null': {'task_id': uid(802), 'status': 'SUCCESS', 'result': None},
    'success_text': {'task_id': uid(803), 'status': 'SUCCESS', 'result': '"Import completato"'},
    'success_broken': {'task_id': uid(804), 'status': 'SUCCESS', 'result': '{not json'},
}
INSTANCE = {'id': 976090, 'domain': 'association-transfer.example.test', 'name': 'Association transfer', 'self_hosted': True,
            'setup_provenance': 'fresh', 'primary_association': 'own_association'}
UNKNOWN = uid(999)

cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, association=None):
    case = {'name': f'association-transfer-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': None}
    if association is not None:
        case['association'] = IDS[association]
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
ADMIN_FOREIGN = {'X-Impersonation-Id': '$session:admin_foreign', 'User-Id': IDS['foreign_owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
SETUP_DENIED = {'detail': 'Setup token missing or invalid.'}
NO_ASSOCIATION = {'error': 'Associazione non trovata'}
NOT_FOUND = {'error': 'Export non trovato'}
TASK_REQUIRED = {'error': 'task_id richiesto'}
VIEW_DENIED = {'error': 'Solo il proprietario può visualizzare gli export'}
DELETE_DENIED = {'error': 'Solo il proprietario può eliminare gli export'}
START_DENIED = {'error': 'Solo il proprietario può esportare i dati'}
FILE_REQUIRED = {'error': 'File ZIP richiesto'}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
EXPORT_DESCRIPTION = ('API endpoints for association data export.\n\n'
                      'Allows association owners to export their data for migration\nto a self-hosted instance.')
IMPORT_DESCRIPTION = ('API endpoints for association data import.\n\n'
                      'Allows importing association data from a ZIP export file\ninto a fresh Bakney instance.')


def options(name, description, parses=('application/json', 'application/x-www-form-urlencoded', 'multipart/form-data')):
    return {'name': name, 'description': description, 'renders': ['application/json'], 'parses': list(parses)}


EXPORT_OPTIONS = options('Association Export', EXPORT_DESCRIPTION)
IMPORT_OPTIONS = options('Association Import', IMPORT_DESCRIPTION, ('multipart/form-data', 'application/x-www-form-urlencoded'))
BOUNDARY = 'association-transfer-boundary'


def multipart(*parts):
    lines = []
    for name, filename, value in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        lines += [f'--{BOUNDARY}', f'Content-Disposition: {disposition}']
        if filename is not None:
            lines.append('Content-Type: application/zip')
        lines += ['', value]
    return '\r\n'.join(lines + [f'--{BOUNDARY}--', '']), f'multipart/form-data; boundary={BOUNDARY}'


def export_row(key):
    doc, archive = DOCUMENTS[key], ARCHIVES[key]
    return {'archive_id': archive['id'], 'document_id': doc['id'], 'filename': doc['filename'], 'file_size_bytes': doc['size'],
            'download_token': TOKEN, 'date': archive['date'], 'created_at': doc['created']}


# === export/active ==============================================================================
ACTIVE = '/association/export/active'
add('active-anonymous', 'anonymous', ACTIVE, 401, ANONYMOUS)
add('active-admin-without-session', 'admin', ACTIVE, 200, {'active': False})
add('active-restricted-unmapped', 'restricted_collaborator', ACTIVE, 403, unmapped('association/export/active'))
add('active-restricted-post-permission-first', 'restricted_collaborator', ACTIVE, 403, unmapped('association/export/active'),
    method='POST', body={})
add('active-full-collaborator', 'full_collaborator', ACTIVE, 403, VIEW_DENIED)
add('active-athlete', 'athlete', ACTIVE, 403, VIEW_DENIED)
add('active-owner-scoped-athlete', 'owner', ACTIVE, 403, SCOPED_DENIED, headers=SCOPED)
add('active-owner-without-association', 'owner_without_association', ACTIVE, 404, NO_ASSOCIATION)
add('active-owner-idle', 'owner', ACTIVE, 200, {'active': False})
add('active-owner-query-ignored', 'owner', ACTIVE + '?task_id=' + UNKNOWN, 200, {'active': False})
add('active-admin-impersonating-owner', 'admin', ACTIVE, 200, {'active': False}, headers=ADMIN_OWNER)
add('active-deleted-association-owner', 'deleted_association_owner', ACTIVE, 404, NO_ASSOCIATION)
add('active-post-not-allowed', 'owner', ACTIVE, 405, method('POST'), method='POST', body={})
add('active-delete-not-allowed', 'owner', ACTIVE, 405, method('DELETE'), method='DELETE')
add('active-options', 'owner', ACTIVE, 200, EXPORT_OPTIONS, method='OPTIONS')

# === export/list ================================================================================
LIST = '/association/export/list'
OWN_LIST = [export_row(f'own{day}') for day in range(21, 1, -1)]
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('list-admin-without-session', 'admin', LIST, 200, {'exports': OWN_LIST, 'count': 20}, association='own_association')
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('association/export/list'))
add('list-full-collaborator', 'full_collaborator', LIST, 403, VIEW_DENIED)
add('list-athlete', 'athlete', LIST, 403, VIEW_DENIED)
add('list-owner-scoped-athlete', 'owner', LIST, 403, SCOPED_DENIED, headers=SCOPED)
add('list-owner-without-association', 'owner_without_association', LIST, 404, NO_ASSOCIATION)
add('list-owner-latest-twenty', 'owner', LIST, 200, {'exports': OWN_LIST, 'count': 20}, association='own_association')
add('list-admin-impersonating-owner', 'admin', LIST, 200, {'exports': OWN_LIST, 'count': 20}, headers=ADMIN_OWNER,
    association='own_association')
add('list-foreign-owner', 'foreign_owner', LIST, 200, {'exports': [export_row('foreign1')], 'count': 1},
    association='foreign_association')
add('list-admin-impersonating-foreign', 'admin', LIST, 200, {'exports': [export_row('foreign1')], 'count': 1},
    headers=ADMIN_FOREIGN, association='foreign_association')
add('list-deleted-association-owner', 'deleted_association_owner', LIST, 404, NO_ASSOCIATION)
add('list-group-header-ignored', 'owner', LIST, 200, {'exports': OWN_LIST, 'count': 20},
    headers={'X-Group-Id': IDS['foreign_group']}, association='own_association')
add('list-post-not-allowed', 'owner', LIST, 405, method('POST'), method='POST', body={})
add('list-options', 'owner', LIST, 200, EXPORT_OPTIONS, method='OPTIONS')

# === export/status ==============================================================================
STATUS = '/association/export/status'
add('status-anonymous', 'anonymous', STATUS + '?task_id=x', 401, ANONYMOUS)
add('status-admin-without-session', 'admin', STATUS, 400, TASK_REQUIRED)
add('status-restricted-unmapped', 'restricted_collaborator', STATUS, 403, unmapped('association/export/status'))
add('status-full-collaborator', 'full_collaborator', STATUS + '?task_id=x', 403, VIEW_DENIED)
add('status-athlete', 'athlete', STATUS, 403, VIEW_DENIED)
add('status-owner-scoped-athlete', 'owner', STATUS, 403, SCOPED_DENIED, headers=SCOPED)
add('status-owner-missing-task', 'owner', STATUS, 400, TASK_REQUIRED)
add('status-owner-empty-task', 'owner', STATUS + '?task_id=', 400, TASK_REQUIRED)
add('status-without-association-task-first', 'owner_without_association', STATUS, 400, TASK_REQUIRED)
add('status-without-association', 'owner_without_association', STATUS + '?task_id=' + UNKNOWN, 404, NO_ASSOCIATION)
add('status-owner-unknown-task', 'owner', STATUS + '?task_id=' + UNKNOWN, 404, NOT_FOUND)
add('status-owner-import-task-not-registered', 'owner', STATUS + '?task_id=' + TASKS['success']['task_id'], 404, NOT_FOUND)
add('status-owner-foreign-active-task', 'owner', STATUS + '?task_id=' + uid(900), 404, NOT_FOUND)
add('status-owner-free-text-task', 'owner', STATUS + '?task_id=not%20a%20uuid', 404, NOT_FOUND)
add('status-repeated-task-last-wins', 'owner', STATUS + '?task_id=' + UNKNOWN + '&task_id=', 400, TASK_REQUIRED)
add('status-post-not-allowed', 'owner', STATUS, 405, method('POST'), method='POST', body={})
add('status-options', 'owner', STATUS, 200, EXPORT_OPTIONS, method='OPTIONS')

# === export/delete ==============================================================================
DELETE = '/association/export/delete'
d = lambda value: {'document_id': value}
add('delete-anonymous', 'anonymous', DELETE, 401, ANONYMOUS, method='DELETE', body=d(UNKNOWN))
add('delete-admin-without-session', 'admin', DELETE, 404, NOT_FOUND, method='DELETE', body=d(UNKNOWN))
add('delete-restricted-unmapped', 'restricted_collaborator', DELETE, 403, unmapped('association/export/delete'), method='DELETE',
    body=d(UNKNOWN))
add('delete-full-collaborator', 'full_collaborator', DELETE, 403, DELETE_DENIED, method='DELETE', body=d(DOCUMENTS['own21']['id']))
add('delete-collaborator-list-body', 'full_collaborator', DELETE, 500, SERVER, method='DELETE', body=[1])
add('delete-athlete-number-body', 'athlete', DELETE, 500, SERVER, method='DELETE', body=5)
add('delete-athlete', 'athlete', DELETE, 403, DELETE_DENIED, method='DELETE', body={})
add('delete-owner-scoped-athlete', 'owner', DELETE, 403, SCOPED_DENIED, method='DELETE', body=d(UNKNOWN), headers=SCOPED)
add('delete-owner-without-body', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE')
add('delete-owner-empty-id', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE', body=d(''))
add('delete-owner-zero-id', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE', body=d(0))
add('delete-owner-false-id', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE', body=d(False))
add('delete-owner-null-id', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE', body=d(None))
add('delete-owner-empty-list-id', 'owner', DELETE, 400, {'error': 'document_id richiesto'}, method='DELETE', body=d([]))
add('delete-owner-query-ignored', 'owner', DELETE + '?document_id=' + DOCUMENTS['own21']['id'], 400,
    {'error': 'document_id richiesto'}, method='DELETE', body={})
add('delete-without-association', 'owner_without_association', DELETE, 404, NO_ASSOCIATION, method='DELETE', body=d('x'))
add('delete-owner-unknown', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(UNKNOWN))
add('delete-owner-foreign-export', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['foreign1']['id']))
add('delete-owner-non-export-document', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['own_report']['id']))
add('delete-owner-case-sensitive-prefix', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['own_upper']['id']))
add('delete-owner-integer-id', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(5))
add('delete-owner-true-id', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(True))
add('delete-owner-uppercase-foreign', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['foreign1']['id'].upper()))
add('delete-owner-hex-foreign', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['foreign1']['id'].replace('-', '')))
add('delete-owner-form-unknown', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', raw_body='document_id=' + UNKNOWN,
    content_type='application/x-www-form-urlencoded')
add('delete-deleted-association-owner', 'deleted_association_owner', DELETE, 404, NO_ASSOCIATION, method='DELETE',
    body=d(DOCUMENTS['own21']['id']))
add('delete-owner-malformed-id', 'owner', DELETE, 500, SERVER, method='DELETE', body=d('not-a-uuid'))
add('delete-owner-dict-id', 'owner', DELETE, 500, SERVER, method='DELETE', body=d({'a': 1}))
add('delete-owner-float-id', 'owner', DELETE, 500, SERVER, method='DELETE', body=d(1.5))
add('delete-owner-negative-id', 'owner', DELETE, 500, SERVER, method='DELETE', body=d(-1))
add('delete-get-not-allowed', 'owner', DELETE, 405, method('GET'))
add('delete-options', 'owner', DELETE, 200, EXPORT_OPTIONS, method='OPTIONS')

# === export/start (pre-dispatch refusals only) ==================================================
START = '/association/export/start'
add('start-anonymous', 'anonymous', START, 401, ANONYMOUS, method='POST', body={})
add('start-admin-without-session', 'admin', START, 409, {'error': 'Elimina un export prima di crearne uno nuovo'}, method='POST', body={})
add('start-restricted-unmapped', 'restricted_collaborator', START, 403, unmapped('association/export/start'), method='POST', body={})
add('start-full-collaborator', 'full_collaborator', START, 403, START_DENIED, method='POST', body={})
add('start-athlete', 'athlete', START, 403, START_DENIED, method='POST')
add('start-owner-scoped-athlete', 'owner', START, 403, SCOPED_DENIED, method='POST', body={}, headers=SCOPED)
add('start-owner-without-association', 'owner_without_association', START, 404, NO_ASSOCIATION, method='POST', body={})
add('start-owner-export-limit', 'owner', START, 409, {'error': 'Elimina un export prima di crearne uno nuovo'}, method='POST',
    body={'include_media': False})
add('start-admin-impersonating-owner-limit', 'admin', START, 409, {'error': 'Elimina un export prima di crearne uno nuovo'},
    method='POST', body={}, headers=ADMIN_OWNER)
add('start-foreign-owner-already-running', 'foreign_owner', START, 409, {'error': 'Un export è già in corso'}, method='POST', body={})
add('start-get-not-allowed', 'owner', START, 405, method('GET'))
add('start-options', 'owner', START, 200, EXPORT_OPTIONS, method='OPTIONS')

# === import/status ==============================================================================
ISTATUS = '/association/import/status'
task = lambda key: ISTATUS + '?task_id=' + TASKS[key]['task_id']
pending = lambda task_id: {'task_id': task_id, 'status': 'PENDING', 'ready': False}
add('import-status-anonymous', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS)
add('import-status-anonymous-setup-token', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS,
    headers={'X-Setup-Token': 'contract-setup-token'})
add('import-status-admin-without-session', 'admin', ISTATUS, 403, ADMIN)
add('import-status-restricted-unmapped', 'restricted_collaborator', ISTATUS, 403, unmapped('association/import/status'))
add('import-status-athlete', 'athlete', ISTATUS + '?task_id=x', 403, SETUP_DENIED)
add('import-status-foreign-owner', 'foreign_owner', ISTATUS + '?task_id=x', 403, SETUP_DENIED)
add('import-status-admin-impersonating-foreign', 'admin', ISTATUS + '?task_id=x', 403, SETUP_DENIED, headers=ADMIN_FOREIGN)
add('import-status-owner-without-association', 'owner_without_association', ISTATUS + '?task_id=x', 403, SETUP_DENIED)
add('import-status-owner-scoped-athlete', 'owner', ISTATUS + '?task_id=x', 403, SCOPED_DENIED, headers=SCOPED)
add('import-status-owner-missing-task', 'owner', ISTATUS, 400, TASK_REQUIRED)
add('import-status-owner-empty-task', 'owner', ISTATUS + '?task_id=', 400, TASK_REQUIRED)
add('import-status-owner-unknown', 'owner', ISTATUS + '?task_id=' + UNKNOWN, 200, pending(UNKNOWN))
add('import-status-owner-free-text', 'owner', ISTATUS + '?task_id=caf%C3%A8%20task', 200, pending('cafè task'))
add('import-status-owner-started', 'owner', task('started'), 200, {'task_id': TASKS['started']['task_id'], 'status': 'STARTED',
                                                                   'ready': False})
add('import-status-owner-custom-state', 'owner', task('progress'), 200, {'task_id': 'import-progress-task', 'status': 'PROGRESS',
                                                                        'ready': False})
add('import-status-owner-success', 'owner', task('success'), 200, {
    'task_id': TASKS['success']['task_id'], 'status': 'SUCCESS', 'ready': True,
    'result': json.loads(TASKS['success']['result'])})
add('import-status-owner-success-null', 'owner', task('success_null'), 200, {
    'task_id': TASKS['success_null']['task_id'], 'status': 'SUCCESS', 'ready': True, 'result': None})
add('import-status-owner-success-text', 'owner', task('success_text'), 200, {
    'task_id': TASKS['success_text']['task_id'], 'status': 'SUCCESS', 'ready': True, 'result': 'Import completato'})
add('import-status-owner-success-undecodable', 'owner', task('success_broken'), 500, SERVER)
add('import-status-full-collaborator', 'full_collaborator', task('started'), 200, {
    'task_id': TASKS['started']['task_id'], 'status': 'STARTED', 'ready': False})
add('import-status-admin-impersonating-owner', 'admin', ISTATUS + '?task_id=' + UNKNOWN, 200, pending(UNKNOWN), headers=ADMIN_OWNER)
add('import-status-post-not-allowed', 'owner', ISTATUS, 405, method('POST'), method='POST')
add('import-status-options', 'owner', ISTATUS, 200, IMPORT_OPTIONS, method='OPTIONS')

# === import/validate and import/start (no archive) ==============================================
for route in ('validate', 'start'):
    path = f'/association/import/{route}'
    name = 'import-' + route
    add(f'{name}-anonymous', 'anonymous', path, 401, ANONYMOUS, method='POST')
    add(f'{name}-admin-without-session', 'admin', path, 403, ADMIN, method='POST')
    add(f'{name}-restricted-unmapped', 'restricted_collaborator', path, 403, unmapped(f'association/import/{route}'), method='POST')
    add(f'{name}-athlete', 'athlete', path, 403, SETUP_DENIED, method='POST')
    add(f'{name}-foreign-owner', 'foreign_owner', path, 403, SETUP_DENIED, method='POST', body={})
    add(f'{name}-owner-scoped-athlete', 'owner', path, 403, SCOPED_DENIED, method='POST', headers=SCOPED)
    add(f'{name}-owner-json-body', 'owner', path, 415, {'detail': 'Unsupported media type "application/json" in request.'},
        method='POST', body={'file': 'x'})
    add(f'{name}-owner-without-body', 'owner', path, 400, FILE_REQUIRED, method='POST')
    add(f'{name}-owner-form-field', 'owner', path, 400, FILE_REQUIRED, method='POST', raw_body='file=archive.zip&owner_password=x',
        content_type='application/x-www-form-urlencoded')
    raw, content = multipart(('file', None, 'not a file'), ('owner_password', None, 'secret'))
    add(f'{name}-owner-multipart-text-field', 'owner', path, 400, FILE_REQUIRED, method='POST', raw_body=raw, content_type=content)
    raw, content = multipart(('archive', 'export.zip', 'PK'))
    add(f'{name}-owner-multipart-other-file', 'owner', path, 400, FILE_REQUIRED, method='POST', raw_body=raw, content_type=content)
    raw, content = multipart(('file', '', 'PK'))
    add(f'{name}-owner-multipart-empty-filename', 'owner', path, 400, FILE_REQUIRED, method='POST', raw_body=raw, content_type=content)
    add(f'{name}-full-collaborator', 'full_collaborator', path, 400, FILE_REQUIRED, method='POST')
    add(f'{name}-admin-impersonating-owner', 'admin', path, 400, FILE_REQUIRED, method='POST', headers=ADMIN_OWNER)
    add(f'{name}-get-not-allowed', 'owner', path, 405, method('GET'))
    add(f'{name}-options', 'owner', path, 200, IMPORT_OPTIONS, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'association export/import routes: reads, refusals and pre-dispatch checks',
    'ids': IDS, 'documents': DOCUMENTS, 'archives': ARCHIVES, 'active_keys': ACTIVE_KEYS, 'tasks': TASKS,
    'instance': INSTANCE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
