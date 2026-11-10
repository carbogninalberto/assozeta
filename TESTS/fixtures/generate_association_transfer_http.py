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
# An export created before Document.file_size_bytes existed: no size, a stored path.
document('foreign_legacy', 'foreign_association', 'export_foreign_legacy.zip', day=2, created='2025-12-20T09:00:00+00:00',
         filepath='exports/foreign/export_legacy.zip', size=None)
DOCUMENTS['foreign_legacy']['token'] = uid(720)

# The foreign association's export in progress: its active cache key, set for the cases that need it.
FOREIGN_ACTIVE = {'task_id': uid(900), 'sport_association_id': FOREIGN, 'user_id': IDS['foreign_owner'],
                  'status': 'PENDING', 'ready': False, 'estimate': '0%',
                  'progress': {'percent': 0, 'phase': 'queued'}, 'updated_at': '2026-01-01T00:00:00+00:00'}
ACTIVE_KEY = lambda association: f'association-export-active:{IDS[association]}'
LAST_KEY = lambda association: f'association-export-last:{IDS[association]}'
TASK_KEY = lambda task_id: f'association-export-task:{task_id}'

# Old own exports (before every listed date) that the delete cases remove; tokens are fixed for the audit entries.
for n, (filepath, size) in enumerate((('exports/own/export_old_a.zip', 10), (None, None), ('exports/own/export_old_c.zip', 30),
                                      ('exports/own/export_old_d.zip', 40), ('exports/own/export_old_e.zip', 50)), 1):
    key = 'old' + 'abcde'[n - 1]
    document(key, 'own_association', f'export_old_{"abcde"[n - 1]}.zip', day=n, created=f'2025-06-0{n}T07:00:00+00:00',
             filepath=filepath, size=size)
    DOCUMENTS[key]['token'] = uid(700 + n)
    ARCHIVES[key]['date'] = f'2025-06-0{n}'

TASKS = {
    'export_running': {'task_id': uid(810), 'status': 'PROGRESS', 'result': '{"percent": 40}'},
    'export_done': {'task_id': uid(811), 'status': 'SUCCESS', 'result': '{"success": true}'},
    'export_revoked': {'task_id': uid(812), 'status': 'REVOKED', 'result': '{"exc_type": "TaskRevokedError", "exc_message": ["revoked"], "exc_module": "celery.exceptions"}'},
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


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, association=None,
        setup=None, cache_after=None, objects_after=None, effect=None, raw_b64=None):
    case = {'name': f'association-transfer-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect,
            'setup': {'instance': True, 'cache': {}, 'objects': {}, **(setup or {})},
            'cache_after': cache_after, 'objects_after': objects_after}
    if association is not None:
        case['association'] = IDS[association]
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if raw_b64 is not None:
        case['raw_b64'], case['content_type'] = raw_b64, content_type
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
add('list-foreign-owner', 'foreign_owner', LIST, 200, {'exports': [export_row('foreign1'), export_row('foreign_legacy')], 'count': 2},
    association='foreign_association')
add('list-admin-impersonating-foreign', 'admin', LIST, 200, {'exports': [export_row('foreign1'), export_row('foreign_legacy')],
                                                              'count': 2},
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
add('status-owner-foreign-active-task', 'owner', STATUS + '?task_id=' + uid(900), 404, NOT_FOUND,
    setup={'cache': {ACTIVE_KEY('foreign_association'): FOREIGN_ACTIVE}})
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
add('start-foreign-owner-already-running', 'foreign_owner', START, 409, {'error': 'Un export è già in corso'}, method='POST', body={},
    setup={'cache': {ACTIVE_KEY('foreign_association'): FOREIGN_ACTIVE}})
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
# Rows in an exception state: Celery rebuilds the exception from exc_type, exc_module and exc_message (the arguments),
# and the view answers str() of it. key: (status, stored result, error text or None for the 500 page).
exc = lambda kind, message, module='builtins': json.dumps({'exc_type': kind, 'exc_message': message, 'exc_module': module})
DUPLICATE = 'duplicate key value violates unique constraint "application_user_pkey"\nDETAIL:  Key (user_id) already exists.\n'
FAILED = {
    'failed_value': ('FAILURE', exc('ValueError', ['Archivio non valido: manca manifest.json']), 'Archivio non valido: manca manifest.json'),
    'failed_two_arguments': ('FAILURE', exc('Exception', ['Import fallito', 3]), "('Import fallito', 3)"),
    'failed_no_arguments': ('FAILURE', exc('RuntimeError', []), ''),
    'failed_key': ('FAILURE', exc('KeyError', ['users']), "'users'"),
    'failed_key_two_arguments': ('FAILURE', exc('KeyError', ['users', None]), "('users', None)"),
    'failed_integrity': ('FAILURE', exc('IntegrityError', [DUPLICATE], 'django.db.utils'), DUPLICATE),
    'failed_scalar_arguments': ('FAILURE', exc('TypeError', [True, "l'archivio", 7]), '(True, "l\'archivio", 7)'),
    'failed_null_argument': ('FAILURE', exc('AssertionError', [None]), 'None'),
    'failed_text_message': ('FAILURE', exc('ValueError', 'testo, non lista'), 'testo, non lista'),
    'failed_without_module': ('FAILURE', '{"exc_type": "ArchivioNonValido", "exc_message": ["è rotto"]}', 'è rotto'),
    'failed_null_module_no_message': ('FAILURE', '{"exc_type": "ArchivioNonValido", "exc_module": null}', ''),
    'failed_null_result': ('FAILURE', None, 'None'),
    'failed_empty_object': ('FAILURE', '{}', 'None'),
    'failed_without_type': ('FAILURE', '{"exc_message": ["x"], "exc_module": "builtins"}', None),
    'failed_text_result': ('FAILURE', '"boom"', None),
    'failed_number_result': ('FAILURE', '7', None),
    'retry_value': ('RETRY', exc('ValueError', ['riprovo']), ''),
    'retry_without_type': ('RETRY', '{"exc_message": ["x"]}', None),
}
for n, (key, (state, stored, _)) in enumerate(FAILED.items()):
    TASKS[key] = {'task_id': uid(840 + n), 'status': state, 'result': stored}
for key, (state, _, error) in FAILED.items():
    label = 'import-status-owner-' + key.replace('_', '-')
    if error is None:
        add(label, 'owner', task(key), 500, SERVER)
    elif state == 'RETRY':
        add(label, 'owner', task(key), 200, {'task_id': TASKS[key]['task_id'], 'status': 'RETRY', 'ready': False})
    else:
        add(label, 'owner', task(key), 200, {'task_id': TASKS[key]['task_id'], 'status': state, 'ready': True, 'error': error})
add('import-status-owner-revoked', 'owner', task('export_revoked'), 200,
    {'task_id': TASKS['export_revoked']['task_id'], 'status': 'REVOKED', 'ready': True, 'error': 'revoked'})
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


# === export/active with cached snapshots ========================================================
SETUP_TOKEN = 'contract-setup-token'
PROGRESS = {'percent': 40, 'phase': 'files', 'label': 'Copia file', 'completed': 4, 'total': 10, 'files_failed': 0}


def snapshot(task_id, *, association='own_association', user='owner', status='PROGRESS', **extra):
    return {'task_id': task_id, 'sport_association_id': IDS[association], 'user_id': IDS[user], 'status': status,
            'ready': status in ('SUCCESS', 'FAILURE'), 'estimate': '40%', 'progress': PROGRESS,
            'updated_at': '2026-02-01T10:00:00.123456+00:00', **extra}


RUNNING = snapshot(uid(920))
STARTED_ROW = snapshot(TASKS['export_running']['task_id'])
DONE = snapshot(TASKS['export_done']['task_id'])
TERMINAL = snapshot(TASKS['export_done']['task_id'], status='SUCCESS', result={'document_id': uid(930), 'filename': 'export_x.zip'})
TERMINAL_FAILED = snapshot(uid(921), status='FAILURE', error='Export non riuscito')
unchanged = lambda *keys: {key: 'unchanged' for key in keys}
active = lambda value: {ACTIVE_KEY('own_association'): value}
last = lambda value: {LAST_KEY('own_association'): value}
add('active-owner-running', 'owner', ACTIVE, 200, {'active': True, **RUNNING}, setup={'cache': active(RUNNING)})
add('active-owner-running-row', 'owner', ACTIVE, 200, {'active': True, **STARTED_ROW}, setup={'cache': active(STARTED_ROW)})
add('active-admin-impersonating-owner-running', 'admin', ACTIVE, 200, {'active': True, **RUNNING}, headers=ADMIN_OWNER,
    setup={'cache': active(RUNNING)})
add('active-admin-without-session-running', 'admin', ACTIVE, 200, {'active': True, **RUNNING}, setup={'cache': active(RUNNING)})
add('active-owner-running-extra-keys', 'owner', ACTIVE, 200,
    {'active': True, **snapshot(uid(922), nested={'a': [1, 2.5, None, True]}, label='è')},
    setup={'cache': active(snapshot(uid(922), nested={'a': [1, 2.5, None, True]}, label='è'))})
add('active-owner-snapshot-other-user', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': active(snapshot(uid(923), user='full_collaborator'))})
add('active-owner-snapshot-other-association', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': active(snapshot(uid(924), association='foreign_association'))})
add('active-owner-snapshot-integer-user', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': active({**RUNNING, 'user_id': 5})})
add('active-owner-snapshot-without-task', 'owner', ACTIVE, 500, SERVER,
    setup={'cache': active({key: value for key, value in RUNNING.items() if key != 'task_id'})})
add('active-owner-finished-with-terminal', 'owner', ACTIVE, 200, {'active': False, 'terminal': TERMINAL},
    setup={'cache': {**active(DONE), **last(TERMINAL)}}, cache_after=unchanged(LAST_KEY('own_association')))
add('active-owner-finished-without-terminal', 'owner', ACTIVE, 200, {'active': False}, setup={'cache': active(DONE)},
    cache_after={})
add('active-owner-finished-other-terminal', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': {**active(DONE), **last(TERMINAL_FAILED)}}, cache_after=unchanged(LAST_KEY('own_association')))
add('active-owner-terminal-success', 'owner', ACTIVE, 200, {'active': False, 'terminal': TERMINAL}, setup={'cache': last(TERMINAL)})
add('active-owner-terminal-failure', 'owner', ACTIVE, 200, {'active': False, 'terminal': TERMINAL_FAILED},
    setup={'cache': last(TERMINAL_FAILED)})
add('active-owner-terminal-not-final', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': last(snapshot(uid(925), status='PROGRESS'))})
add('active-owner-terminal-other-user', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': last(snapshot(uid(926), user='full_collaborator', status='SUCCESS'))})
add('active-owner-active-not-a-dict', 'owner', ACTIVE, 200, {'active': False, 'terminal': TERMINAL},
    setup={'cache': {**active('not a snapshot'), **last(TERMINAL)}})
# The snapshot's task row in an exception state: FAILURE and REVOKED are ready, RETRY is not.
for key in ('failed_value', 'export_revoked'):
    add('active-owner-' + key.replace('_', '-') + '-row', 'owner', ACTIVE, 200, {'active': False},
        setup={'cache': active(snapshot(TASKS[key]['task_id']))}, cache_after={})
add('active-owner-retry-row', 'owner', ACTIVE, 200, {'active': True, **snapshot(TASKS['retry_value']['task_id'])},
    setup={'cache': active(snapshot(TASKS['retry_value']['task_id']))})
add('active-owner-failed-row-without-type', 'owner', ACTIVE, 500, SERVER,
    setup={'cache': active(snapshot(TASKS['failed_without_type']['task_id']))})
add('active-owner-foreign-snapshot-ignored', 'owner', ACTIVE, 200, {'active': False},
    setup={'cache': {ACTIVE_KEY('foreign_association'): FOREIGN_ACTIVE}})

# === export/delete of an existing export ========================================================


def deleted(*keys, actor_association='own_association', additional=None):
    return {'archives': [ARCHIVES[k]['id'] for k in keys], 'documents': [DOCUMENTS[k]['id'] for k in keys],
            'archive_changes': {ARCHIVES[k]['id']: {'sport_association_documents_archive_id': [ARCHIVES[k]['id'], 'None'],
                                                    'sport_association': [OWN, 'None'], 'document': [DOCUMENTS[k]['id'], 'None'],
                                                    'date': [ARCHIVES[k]['date'], 'None'], 'folder': ['None', 'None']} for k in keys},
            'document_changes': {DOCUMENTS[k]['id']: {name: [value, 'None'] for name, value in (
                ('document_id', DOCUMENTS[k]['id']), ('creation_date', DOCUMENTS[k]['created'].replace('T', ' ').removesuffix('+00:00')),
                ('filepath', DOCUMENTS[k]['filepath']), ('filename', DOCUMENTS[k]['filename']),
                ('file_size_bytes', None if DOCUMENTS[k]['size'] is None else str(DOCUMENTS[k]['size'])),
                ('token', DOCUMENTS[k]['token'])) if value is not None}
                | {'customer_invoice_pdf': ['application.CustomerInvoice.None', 'None']} for k in keys},
            'actor_association': actor_association, 'additional_data': additional, 'celery': []}


SUCCESS_DELETE = {'success': True, 'message': 'Export eliminato'}
OBJECT = {'body': 'PK export archive', 'content_type': 'application/zip'}
add('delete-owner-existing-export', 'owner', DELETE, 200, SUCCESS_DELETE, method='DELETE', body=d(DOCUMENTS['olda']['id']),
    setup={'objects': {DOCUMENTS['olda']['filepath']: OBJECT, 'exports/own/keep.zip': OBJECT}},
    objects_after={'exports/own/keep.zip': OBJECT}, effect=deleted('olda'))
add('delete-owner-export-without-file', 'owner', DELETE, 200, SUCCESS_DELETE, method='DELETE', body=d(DOCUMENTS['oldb']['id']),
    effect=deleted('oldb'))
add('delete-owner-export-object-missing', 'owner', DELETE, 200, SUCCESS_DELETE, method='DELETE',
    body=d(DOCUMENTS['oldc']['id'].upper()), effect=deleted('oldc'))
add('delete-admin-impersonating-owner-export', 'admin', DELETE, 200, SUCCESS_DELETE, method='DELETE',
    body=d(DOCUMENTS['oldd']['id']), headers=ADMIN_OWNER, setup={'objects': {DOCUMENTS['oldd']['filepath']: OBJECT}},
    objects_after={}, effect=deleted('oldd', actor_association='admin_association', additional={
        'actor_id': IDS['admin'], 'association_id': None, 'target_user_id': IDS['owner'],
        'impersonation_session': '$session:admin_owner'}))
add('delete-owner-form-existing-export', 'owner', DELETE, 200, SUCCESS_DELETE, method='DELETE',
    raw_body='document_id=' + DOCUMENTS['olde']['id'].replace('-', ''), content_type='application/x-www-form-urlencoded',
    setup={'objects': {DOCUMENTS['olde']['filepath']: OBJECT}}, objects_after={}, effect=deleted('olde'))
add('delete-owner-already-deleted', 'owner', DELETE, 404, NOT_FOUND, method='DELETE', body=d(DOCUMENTS['olda']['id']))

# === export/start dispatch =======================================================================
QUEUED = {'percent': 0, 'phase': 'queued', 'label': 'Export in attesa', 'completed': None, 'total': None}
STARTED = {'task_id': '$task', 'status': 'started', 'estimate': '0%', 'progress': QUEUED, 'updated_at': '$isonow',
           'message': 'Export avviato. Riceverai una email quando sarà completato.'}


def dispatched(user):
    return {ACTIVE_KEY('foreign_association'): {'value': {
                'task_id': '$task', 'sport_association_id': FOREIGN, 'user_id': IDS[user], 'status': 'PENDING', 'ready': False,
                'estimate': '0%', 'progress': QUEUED, 'updated_at': '$updated'}, 'ttl': 7200},
            TASK_KEY('$task'): {'value': {'sport_association_id': FOREIGN, 'user_id': IDS[user]}, 'ttl': 86400}}


def export_task(user):
    return {'celery': [{'task': 'export_association_data', 'args': [],
                        'kwargs': [['sport_association_id', FOREIGN], ['user_id', IDS[user]]], 'id': '$task'}]}


add('start-foreign-owner-dispatch', 'foreign_owner', START, 202, STARTED, method='POST', body={},
    cache_after=dispatched('foreign_owner'), effect=export_task('foreign_owner'))
add('start-admin-impersonating-foreign-dispatch', 'admin', START, 202, STARTED, method='POST', headers=ADMIN_FOREIGN,
    cache_after=dispatched('foreign_owner'), effect=export_task('foreign_owner'))
add('start-foreign-owner-terminal-kept', 'foreign_owner', START, 202, STARTED, method='POST', body={'include_media': False},
    setup={'cache': {LAST_KEY('foreign_association'): {**FOREIGN_ACTIVE, 'status': 'SUCCESS'}}},
    cache_after={**dispatched('foreign_owner'), **unchanged(LAST_KEY('foreign_association'))}, effect=export_task('foreign_owner'))
add('start-foreign-owner-running-other-task-key', 'foreign_owner', START, 202, STARTED, method='POST',
    setup={'cache': {TASK_KEY(uid(900)): {'sport_association_id': FOREIGN, 'user_id': IDS['foreign_owner']}}},
    cache_after={**dispatched('foreign_owner'), **unchanged(TASK_KEY(uid(900)))}, effect=export_task('foreign_owner'))

# === setup wizard: no InstanceConfiguration, X-Setup-Token =======================================
TOKEN_HEADER = {'X-Setup-Token': SETUP_TOKEN}
NO_INSTANCE = {'instance': False}
add('import-status-setup-token', 'anonymous', ISTATUS + '?task_id=' + UNKNOWN, 200, pending(UNKNOWN), headers=TOKEN_HEADER,
    setup=NO_INSTANCE)
add('import-status-setup-token-success-row', 'anonymous', task('success_text'), 200, {
    'task_id': TASKS['success_text']['task_id'], 'status': 'SUCCESS', 'ready': True, 'result': 'Import completato'},
    headers=TOKEN_HEADER, setup=NO_INSTANCE)
add('import-status-setup-token-wrong', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS, headers={'X-Setup-Token': 'wrong'},
    setup=NO_INSTANCE)
add('import-status-setup-token-empty', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS, headers={'X-Setup-Token': ''},
    setup=NO_INSTANCE)
add('import-status-setup-token-prefix', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS,
    headers={'X-Setup-Token': SETUP_TOKEN[:-1]}, setup=NO_INSTANCE)
add('import-status-setup-token-owner-wrong', 'owner', ISTATUS + '?task_id=x', 403, SETUP_DENIED,
    headers={'X-Setup-Token': 'wrong'}, setup=NO_INSTANCE)
add('import-status-setup-token-owner-missing', 'owner', ISTATUS + '?task_id=x', 403, SETUP_DENIED, setup=NO_INSTANCE)
add('import-status-setup-token-owner', 'owner', ISTATUS + '?task_id=' + UNKNOWN, 200, pending(UNKNOWN), headers=TOKEN_HEADER,
    setup=NO_INSTANCE)
add('import-status-setup-token-missing-task', 'anonymous', ISTATUS, 400, TASK_REQUIRED, headers=TOKEN_HEADER, setup=NO_INSTANCE)
add('import-status-setup-token-options', 'anonymous', ISTATUS, 200, IMPORT_OPTIONS, method='OPTIONS', headers=TOKEN_HEADER,
    setup=NO_INSTANCE)
add('import-status-setup-token-post-not-allowed', 'anonymous', ISTATUS, 405, method('POST'), method='POST', headers=TOKEN_HEADER,
    setup=NO_INSTANCE)
add('import-status-configured-token-ignored', 'anonymous', ISTATUS + '?task_id=x', 401, ANONYMOUS, headers=TOKEN_HEADER)
add('import-validate-setup-token-without-file', 'anonymous', '/association/import/validate', 400, FILE_REQUIRED, method='POST',
    headers=TOKEN_HEADER, setup=NO_INSTANCE)
add('import-start-setup-token-json', 'anonymous', '/association/import/start', 415,
    {'detail': 'Unsupported media type "application/json" in request.'}, method='POST', body={'file': 'x'}, headers=TOKEN_HEADER,
    setup=NO_INSTANCE)

# === import/validate with archives ===============================================================
import base64
import io
import zipfile

NEW_ASSOCIATION, NEW_OWNER, NEW_MEMBER = uid(950), uid(951), uid(952)
# A PBKDF2-SHA256 hash Django can authenticate against (600000 iterations, 22-character salt, 32-byte digest).
PBKDF2 = 'pbkdf2_sha256$600000$AbCdEfGhIjKlMnOpQrStUv$' + base64.b64encode(bytes(range(32))).decode()


def make_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 15, 10, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
    return buffer.getvalue()


MANIFEST = {'version': '1.0.0', 'export_format': 'bakney_sport_export_v1', 'export_date': '2026-01-15T10:00:00+00:00',
            'association': {'sport_association_id': NEW_ASSOCIATION, 'denomination': 'Società importata', 'extra': 1},
            'models_exported': [{'name': 'SportAssociation', 'file': 'data/01_sport_association.json', 'count': 1},
                                {'name': 'User', 'file': '02_users.json', 'count': 2},
                                {'name': 'Course', 'file': 'data/10_courses.json', 'count': 0}, {'name': 'NoFile'}]}


def export_members(*, association=NEW_ASSOCIATION, owner=NEW_OWNER, password=PBKDF2, users=None, associations=None, manifest=None):
    record = {'sport_association_id': association, 'user_id': owner, 'denomination': 'Società importata', 'tax_code': '01234567890'}
    users = users if users is not None else [
        {'user_id': owner, 'username': 'imported-owner', 'email': 'owner@imported.example.test', 'password': password},
        {'user_id': NEW_MEMBER, 'username': 'imported-member', 'email': 'member@imported.example.test', 'password': '!'}]
    return {'manifest.json': manifest or MANIFEST, 'data/01_sport_association.json': associations or [record],
            'data/02_users.json': users}


def upload(route, name, archive, *, filename='export.zip', kind='application/zip', extra=()):
    parts = [('file', filename, kind, archive), *extra]
    lines = b''
    for field, file, content_type, value in parts:
        disposition = f'form-data; name="{field}"' + (f'; filename="{file}"' if file is not None else '')
        lines += f'--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n'.encode()
        if content_type:
            lines += f'Content-Type: {content_type}\r\n'.encode()
        lines += b'\r\n' + (value if isinstance(value, bytes) else value.encode()) + b'\r\n'
    return base64.b64encode(lines + f'--{BOUNDARY}--\r\n'.encode()).decode(), f'multipart/form-data; boundary={BOUNDARY}'


def validation(archive, *, valid=True, errors=(), warnings=(), info=None):
    size = len(archive)
    base = {'file_size_bytes': size, 'file_size_mb': round(size / (1024 * 1024), 2)}
    return {'is_valid': valid, 'errors': list(errors), 'warnings': list(warnings), 'info': {**base, **(info or {})}}


OWNER_INFO = lambda requires, *, owner=NEW_OWNER: {'user_id': owner, 'username': 'imported-owner',
                                                   'email': 'owner@imported.example.test', 'requires_recovery_password': requires}
IMPORTED = {'sport_association_id': NEW_ASSOCIATION, 'denomination': 'Società importata', 'tax_code': '01234567890'}
FILES_INFO = {'export_format': 'bakney_sport_export_v1', 'export_date': '2026-01-15T10:00:00+00:00', 'expected_files': 3,
              'missing_files': 1}
VALIDATE = '/association/import/validate'
ARCHIVES_IN = {}


def validate_case(name, archive, response, *, actor='owner', headers=None, setup=None, filename='export.zip', status=200):
    raw, kind = upload('validate', name, archive, filename=filename)
    ARCHIVES_IN[name] = archive
    add('import-validate-' + name, actor, VALIDATE, status, response, method='POST', raw_b64=raw, content_type=kind,
        headers=headers, setup=setup)


valid = make_zip(export_members())
validate_case('valid-archive', valid, validation(valid, warnings=['Missing data file: data/10_courses.json'],
                                                 info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(False)}))
validate_case('setup-token-valid-archive', valid, validation(valid, warnings=['Missing data file: data/10_courses.json'],
              info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(False)}), actor='anonymous',
              headers=TOKEN_HEADER, setup=NO_INSTANCE)
validate_case('full-collaborator-valid-archive', valid, validation(valid, warnings=['Missing data file: data/10_courses.json'],
              info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(False)}), actor='full_collaborator')
unusable = make_zip(export_members(password='!unusable'))
validate_case('unusable-owner-password', unusable, validation(unusable, warnings=['Missing data file: data/10_courses.json'],
              info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(True)}))
low = make_zip(export_members(password='pbkdf2_sha256$600000$$' + base64.b64encode(bytes(32)).decode()))
validate_case('owner-password-without-salt', low, validation(low, warnings=['Missing data file: data/10_courses.json'],
              info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(True)}))
conflict = make_zip(export_members(association=OWN, owner=IDS['owner'], password='!',
                                   users=[{'user_id': IDS['owner'], 'username': 'imported-owner', 'email': 'owner@imported.example.test',
                                           'password': '!'},
                                          {'user_id': IDS['athlete'], 'username': 'a'}, {'user_id': IDS['full_collaborator']},
                                          {'user_id': UNKNOWN}, {'username': 'no id'}]))
validate_case('existing-association-and-users', conflict, validation(conflict, valid=False, errors=[
    f'Association UUID conflict: {OWN} already exists', f'User UUID collision: {IDS["athlete"]} already exists and is not linked '
    'to the archive owner'], warnings=['Missing data file: data/10_courses.json'],
    info={**FILES_INFO, 'association': {**IMPORTED, 'sport_association_id': OWN},
          'owner_user': OWNER_INFO(False, owner=IDS['owner'])}))
other_owner = make_zip(export_members(owner=IDS['foreign_owner'], users=[
    {'user_id': IDS['foreign_owner'], 'username': 'imported-owner', 'email': 'owner@imported.example.test', 'password': '!'}]))
validate_case('owner-owns-another-association', other_owner, validation(other_owner, valid=False, errors=[
    f'Owner user already owns another SportAssociation: {FOREIGN}'], warnings=['Missing data file: data/10_courses.json'],
    info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(False, owner=IDS['foreign_owner'])}))
not_zip = b'This is not a ZIP archive.'
validate_case('not-a-zip', not_zip, {'is_valid': False, 'errors': ['$tmp-path:Not a valid ZIP file: :backup.zip'], 'warnings': [],
                                     'info': {}}, filename='backup.zip')
no_manifest = make_zip({'data/01_sport_association.json': []})
validate_case('missing-manifest', no_manifest, validation(no_manifest, valid=False, errors=['Missing manifest.json in export file']))
other_format = make_zip({'manifest.json': {'export_format': 'other', 'note': 'x'}})
validate_case('unsupported-format', other_format, validation(other_format, valid=False, errors=[
    'Missing required manifest field: version', 'Missing required manifest field: export_date',
    'Missing required manifest field: association', 'Unsupported export format: other'],
    info={'export_format': 'other', 'export_date': None, 'association': {}}))
only_manifest = make_zip({'manifest.json': MANIFEST})
validate_case('missing-association-file', only_manifest, validation(only_manifest, valid=False, errors=[
    'Missing required data file: 01_sport_association.json'], warnings=[
    'Missing data file: data/01_sport_association.json', 'Missing data file: data/02_users.json',
    'Missing data file: data/10_courses.json'], info={**FILES_INFO, 'missing_files': 3, 'association': MANIFEST['association']}))
two = make_zip(export_members(associations=[{'sport_association_id': NEW_ASSOCIATION, 'user_id': NEW_OWNER}] * 2))
validate_case('two-associations', two, validation(two, valid=False, errors=[
    'Export must contain exactly one SportAssociation record in data/01_sport_association.json'],
    warnings=['Missing data file: data/10_courses.json'], info={**FILES_INFO, 'association': MANIFEST['association']}))
no_user = make_zip(export_members(associations=[{'sport_association_id': NEW_ASSOCIATION, 'user_id': ''}]))
validate_case('association-without-owner', no_user, validation(no_user, valid=False, errors=[
    'SportAssociation record is missing user_id for owner selection'], warnings=['Missing data file: data/10_courses.json'],
    info={**FILES_INFO, 'association': MANIFEST['association']}))
orphan = make_zip(export_members(users=[{'user_id': NEW_MEMBER}]))
validate_case('owner-not-in-users', orphan, validation(orphan, valid=False, errors=[
    f'Expected exactly one User with user_id {NEW_OWNER} matching SportAssociation.user_id'],
    warnings=['Missing data file: data/10_courses.json'], info={**FILES_INFO, 'association': MANIFEST['association']}))
no_users = make_zip({key: value for key, value in export_members().items() if key != 'data/02_users.json'})
validate_case('missing-users-file', no_users, validation(no_users, valid=False, errors=['Missing required data file: 02_users.json'],
              warnings=['Missing data file: data/02_users.json', 'Missing data file: data/10_courses.json'],
              info={**FILES_INFO, 'missing_files': 2, 'association': MANIFEST['association']}))
raw, kind = upload('validate', 'upper', valid, filename='EXPORT.ZIP')
add('import-validate-admin-impersonating-owner-archive', 'admin', VALIDATE, 200,
    validation(valid, warnings=['Missing data file: data/10_courses.json'],
               info={**FILES_INFO, 'association': IMPORTED, 'owner_user': OWNER_INFO(False)}),
    method='POST', raw_b64=raw, content_type=kind, headers=ADMIN_OWNER)

# === import/start with an archive ================================================================
IMPORT_STARTED = {'task_id': '$task', 'status': 'started', 'message': 'Import avviato. Controlla lo stato con task_id.'}
IMPORT_START = '/association/import/start'


def import_started(name, archive, password, *, actor='owner', headers=None, setup=None, kind='application/zip', stored=None,
                   extra=(), filename='export.zip'):
    raw, content = upload('start', name, archive, kind=kind, extra=extra, filename=filename)
    add('import-start-' + name, actor, IMPORT_START, 202, IMPORT_STARTED, method='POST', raw_b64=raw, content_type=content,
        headers=headers, setup=setup,
        objects_after={'$upload': {'body_b64': base64.b64encode(archive).decode(), 'content_type': stored or kind}},
        effect={'celery': [{'task': 'import_association_data', 'args': [],
                            'kwargs': [['zip_file_path', '$upload'], ['owner_password', password]], 'id': '$task'}]})


import_started('owner-archive', valid, '', )
import_started('owner-archive-password', valid, 'recovery secret',
               extra=[('owner_password', None, None, 'first'), ('owner_password', None, None, 'recovery secret')])
import_started('setup-token-archive', valid, 'è segreta', actor='anonymous', headers=TOKEN_HEADER, setup=NO_INSTANCE,
               extra=[('owner_password', None, None, 'è segreta')])
import_started('windows-zip-type', not_zip, '', kind='application/x-zip-compressed')
import_started('archive-without-type', valid, '', kind=None, stored='application/zip', filename='export')
import_started('admin-impersonating-owner-archive', valid, '', actor='admin', headers=ADMIN_OWNER)

# === export/list of an export without a stored size ============================================
# default_storage.size(filepath) is saved on the document (an update entry); a missing object leaves it null.
LEGACY_BODY = 'PK legacy export archive'
LEGACY_SIZED = {**export_row('foreign_legacy'), 'file_size_bytes': len(LEGACY_BODY)}
add('list-foreign-legacy-size', 'foreign_owner', LIST, 200, {'exports': [export_row('foreign1'), LEGACY_SIZED], 'count': 2},
    association='foreign_association',
    setup={'objects': {DOCUMENTS['foreign_legacy']['filepath']: {'body': LEGACY_BODY, 'content_type': 'application/zip'}}},
    effect={'documents_updated': {DOCUMENTS['foreign_legacy']['id']: {'file_size_bytes': len(LEGACY_BODY)}},
            'actor_association': 'foreign_association', 'additional_data': None, 'celery': []})
add('list-foreign-legacy-size-saved', 'foreign_owner', LIST, 200, {'exports': [export_row('foreign1'), LEGACY_SIZED], 'count': 2},
    association='foreign_association')

import collections; assert len({c['name'] for c in cases}) == len(cases), [k for k, v in collections.Counter(c['name'] for c in cases).items() if v > 1]
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'association export/import routes: reads, refusals and pre-dispatch checks',
    'ids': IDS, 'documents': DOCUMENTS, 'archives': ARCHIVES, 'tasks': TASKS, 'setup_token': SETUP_TOKEN,
    'instance': INSTANCE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
