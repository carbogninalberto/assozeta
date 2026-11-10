"""Author the owner's backup actions HTTP sequence (instance-backups-actions.md).

Pins BE/instance/backups.py (BackupItemView, BackupScheduleView,
BackupRestoreView, BackupUploadView), BE/instance/remote_backups.py (the
remote copy views) and BE/instance/updater_client.py (call_runner with a
JSON payload, send_runner_stream) with BE/instance/upload_stream.py (the
ASGI wrapper hands the upload body to the view unread), in Django's order:
identity, IsInstanceOwner, method dispatch, the archive name, the request
data or serializer, the password attempts and check (restore, delete,
un-keep, the remote settings and key), the runner call, then the UPDATE
audit entry when the runner accepted. Uploads above the configured
'upload_max_bytes' are refused before the runner; the remote endpoint is
checked before the runner (https://, no loopback, link-local or instance
addresses).

The run is in production mode with ASSOZETA_UPDATER_DIRECTORY on a runner
stub owned by the adapter. Per case:
- 'runner' scripts the stub: 'state' ('up', 'no-socket', 'no-token'), the
  answer ('status', raw 'body' text), 'early' (answer without reading the
  request body) and 'expect', the exact requests it must receive: method,
  path, every header (lower case names; '$length' is the length of the
  expected body) and the body: {'json': template} for a JSON payload (the
  bytes must equal Python's json.dumps of the template, '$uuid4' standing
  for a fresh str(uuid4())), upload bytes ({'b64'} or {'seed', 'size'}),
  'unread' when the stub answers first, None for none;
- 'cache' presets the password attempts ({actor: count}); 'cache_after' is
  their exact state afterwards (values, fresh or kept TTLs);
- 'password' replaces the actor's stored hash for the request, 'rehash'
  expects check_password's upgrade;
- 'audit' is the expected UPDATE entry on the instance configuration;
- 'send' shapes an upload body ('b64' bytes with their Content-Length, or
  'chunked' without one; 'early' writes the body while the answer is read);
  'no_store' expects Cache-Control: no-store.
Every credential, host and name is a placeholder. Run with ``python3 -I``.
"""
import base64
import copy
import hashlib
import json
import random
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_backups_actions_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
LEAVE = {'detail': 'Esci dall’impersonificazione per accedere all’amministrazione.'}
REQUIRED, NULL, BLANK = 'This field is required.', 'This field may not be null.', 'This field may not be blank.'
PATTERN, STRING = 'This value does not match the required pattern.', 'Not a valid string.'
BOOLEAN, INTEGER = 'Must be a valid boolean.', 'A valid integer is required.'
UNREACHABLE = 'Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.'
NEWER = 'Il servizio di aggiornamento richiede una versione più recente.'
NOT_FOUND = 'Backup non trovato.'
INVALID = 'Richiesta non valida.'
WRONG = 'Password non corretta.'
TOO_MANY = 'Troppi tentativi. Riprova tra qualche minuto.'
CONFIRM_WORD = 'Scrivi RIPRISTINA per confermare.'
CHOOSE_FILE = 'Seleziona un file da caricare.'
TOO_LARGE = 'Il file supera la dimensione massima consentita per il caricamento.'
INVALID_ENDPOINT = 'Inserisci un indirizzo valido, ad esempio https://s3.example.com.'
HTTPS_ENDPOINT = 'Usa un indirizzo https://.'
BLOCKED_ENDPOINT = 'Questo indirizzo non può essere usato per la copia remota.'
RUNNER_TOKEN = 'runner-token-placeholder'
ARCHIVE = 'assozeta-20261101T020000Z.tar.gz'
SECOND = 'assozeta-20261102T020000Z-1.tar.gz'
BASE = '/instance/admin/backups'
SCHEDULE, UPLOAD = BASE + '/schedule', BASE + '/upload'
REMOTE, REMOTE_TEST, REMOTE_KEY = BASE + '/remote', BASE + '/remote/test', BASE + '/remote/key'
KEY_CONFIRM, FETCH = BASE + '/remote/key/confirm', BASE + '/remote/fetch'
OWNER, ADMIN = IDS['owner'], IDS['admin']

INSTANCE = {
    'id': 7306, 'domain': 'backup-actions.example.test', 'name': 'Istanza azioni', 'abbreviation': 'IA',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'support_multiple_associations': False,
    'primary_association': 'own_association', 'google_client_id': '', 'apple_client_id': '', 'email_settings': {},
    'email_password_encrypted': '', 'email_revision': 0, 'diagnostic_results': {}, 'integration_settings': {},
}
REPR = 'Instance: Istanza azioni (backup-actions.example.test)'
PASSWORD = 'Fixture-backup-placeholder-2'


def pbkdf2(raw, salt, iterations):
    digest = hashlib.pbkdf2_hmac('sha256', raw.encode(), salt.encode(), iterations)
    return f'pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}'


# Django's current PBKDF2 cost and a 128-bit salt: a correct password is not rehashed.
HASHES = {'owner': pbkdf2(PASSWORD, 'backupactionsowner0000001', 1_000_000),
          'admin': pbkdf2(PASSWORD, 'backupactionsadmin0000001', 1_000_000)}
LEGACY_HASH = pbkdf2(PASSWORD, 'backupactionslegacy000001', 1000)

cases = []


def headers_for(*, body=True, content_type='application/json', length='$length'):
    value = {'host': 'localhost', 'accept-encoding': 'identity', 'authorization': f'Bearer {RUNNER_TOKEN}',
             'content-type': content_type}
    if body:
        value['content-length'] = length
    return value


def call(method, path, payload):
    """call_runner(method, path, payload): one JSON request."""
    if payload is None:
        return {'method': method, 'path': path, 'headers': headers_for(body=False), 'body': None}
    return {'method': method, 'path': path, 'headers': headers_for(), 'body': {'json': payload}}


def runner(status=200, body='', *, expect=(), state='up', early=False):
    return {'state': state, 'status': status, 'body': body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
            'early': early, 'expect': list(expect) if state == 'up' else []}


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None,
        run=None, cache=None, cache_after=None, password=None, rehash=False, audit=None, send=None, no_store=False):
    """run=None means the runner must receive no request (it is up)."""
    case = {'name': f'instance-backups-actions-{name}', 'actor': actor, 'method': method, 'path': path,
            'headers': headers or {}, 'status': status, 'response': response,
            'runner': run or {'state': 'up', 'expect': []}, 'cache': cache or {},
            'cache_after': copy.deepcopy(cache_after if cache_after is not None else (cache or {})),
            'password': password, 'rehash': rehash, 'audit': audit, 'send': send, 'no_store': no_store}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def options(name, description=''):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def error(text):
    return {'error': text}


def not_allowed(method):
    return {'detail': f'Method "{method}" not allowed.'}


def logged(key, value, actor='owner', association='own_association', **extra):
    """audit(actor, key, value, **extra): an UPDATE entry on the configuration."""
    return {'changes': {key: ['', value]}, 'additional_data': {'actor_id': IDS[actor], **extra}, 'actor': actor,
            'association': association}


def attempts_preset(actor, value, ttl=None):
    """The password attempts of 'cache'/'cache_after' (shared by every password confirmation)."""
    if ttl is None:
        return {'attempts': {actor: value}}
    return {'attempts': {actor: {'value': value, 'ttl': ttl}}}


def item(name=ARCHIVE):
    return f'{BASE}/{name}'


def restore_path(name=ARCHIVE):
    return f'{BASE}/{name}/restore'


# ---------------------------------------------------------------------------
# Identity, IsInstanceOwner (before method dispatch), methods and OPTIONS.
# ---------------------------------------------------------------------------
ROUTES = [(item(), 'PATCH'), (SCHEDULE, 'PUT'), (restore_path(), 'POST'), (UPLOAD, 'POST'), (REMOTE, 'PUT'),
          (REMOTE_TEST, 'POST'), (REMOTE_KEY, 'POST'), (KEY_CONFIRM, 'POST'), (FETCH, 'POST')]
for index, (path, method) in enumerate(ROUTES):
    add(f'anonymous-{index}', 'anonymous', path, 401, ANON, method=method, body={})
add('athlete-item', 'athlete', item(), 403, DENIED, method='PATCH', body={'keep': True})
add('collaborator-restore', 'full_collaborator', restore_path(), 403, DENIED, method='POST',
    body={'password': PASSWORD, 'confirm': 'RIPRISTINA'})
add('restricted-collaborator-schedule', 'restricted_collaborator', SCHEDULE, 403, DENIED, method='PUT', body={})
add('foreign-owner-remote', 'foreign_owner', REMOTE, 403, DENIED)
add('owner-without-association-key', 'owner_without_association', REMOTE_KEY, 403, DENIED, method='POST', body={})
add('athlete-upload', 'athlete', UPLOAD, 403, DENIED, method='POST', send={'b64': base64.b64encode(b'\x1f\x8bdata').decode()})
add('athlete-wrong-method-denied-first', 'athlete', SCHEDULE, 403, DENIED, method='GET')
add('owner-impersonation-header', 'owner', item(), 403, LEAVE, method='DELETE',
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('item-get', 'owner', item(), 405, not_allowed('GET'))
add('item-post', 'owner', item(), 405, not_allowed('POST'), method='POST', body={})
add('item-put', 'owner', item('notes.txt'), 405, not_allowed('PUT'), method='PUT', body={})
add('schedule-get', 'owner', SCHEDULE, 405, not_allowed('GET'))
add('schedule-patch-is-not-an-archive', 'owner', SCHEDULE, 405, not_allowed('PATCH'), method='PATCH', body={'keep': True})
add('restore-get', 'owner', restore_path(), 405, not_allowed('GET'))
add('upload-get', 'owner', UPLOAD, 405, not_allowed('GET'))
add('upload-put', 'owner', UPLOAD, 405, not_allowed('PUT'), method='PUT', body={})
add('remote-post', 'owner', REMOTE, 405, not_allowed('POST'), method='POST', body={})
add('remote-test-get', 'owner', REMOTE_TEST, 405, not_allowed('GET'))
add('remote-key-get', 'owner', REMOTE_KEY, 405, not_allowed('GET'))
add('key-confirm-get', 'owner', KEY_CONFIRM, 405, not_allowed('GET'))
add('fetch-get', 'owner', FETCH, 405, not_allowed('GET'))
add('fetch-delete-italian', 'owner', FETCH, 405, {'detail': 'Metodo "DELETE" non consentito'}, method='DELETE',
    headers={'Accept-Language': 'it'})
UPLOAD_DOC = ('Add an archive from the owner\'s computer, streamed to the runner as it arrives.\n\n'
              'The body is the raw .tar.gz (not a form): core.asgi hands it over unread,\n'
              'so a large file never sits in memory or in a temporary file of the API.')
for path, name, doc in ((item(), 'Backup Item', 'Keep (exclude from rotation) or delete one archive.'),
                        (SCHEDULE, 'Backup Schedule', ''),
                        (restore_path(), 'Backup Restore', 'Restore one server archive; the runner backs up the current data first.'),
                        (UPLOAD, 'Backup Upload', UPLOAD_DOC),
                        (REMOTE, 'Remote Backup Settings', ''), (REMOTE_TEST, 'Remote Backup Test', ''),
                        (REMOTE_KEY, 'Remote Backup Key', 'Generate a key pair: the recovery key is in this response only.'),
                        (KEY_CONFIRM, 'Remote Backup Key Confirm', ''),
                        (FETCH, 'Remote Backup Fetch', 'Bring a remote-only copy back to the server; the key is held in memory only.')):
    add('options-' + name.lower().replace(' ', '-'), 'owner', path, 200, options(name, doc), method='OPTIONS')

# ---------------------------------------------------------------------------
# PATCH/DELETE admin/backups/<name> (BackupItemView): the archive name, then
# request.data, then relay(); audited when the runner answers.
# ---------------------------------------------------------------------------
KEPT = {'archive': {'name': ARCHIVE, 'keep': True}}
add('keep-not-an-archive', 'owner', item('notes.txt'), 404, error(NOT_FOUND), method='PATCH', body={'keep': True})
add('keep-path-name', 'owner', item('assozeta-20261101T020000Z.tar.gz.bak'), 404, error(NOT_FOUND), method='PATCH',
    body={'keep': True})
add('keep-missing', 'owner', item(), 400, error(INVALID), method='PATCH', body={})
add('keep-no-body', 'owner', item(), 400, error(INVALID), method='PATCH')
add('keep-text', 'owner', item(), 400, error(INVALID), method='PATCH', body={'keep': 'true'})
add('keep-number', 'owner', item(), 400, error(INVALID), method='PATCH', body={'keep': 1})
add('keep-null', 'owner', item(), 400, error(INVALID), method='PATCH', body={'keep': None})
add('keep-list-body', 'owner', item(), 400, error(INVALID), method='PATCH', body=[{'keep': True}])
add('keep-form-body', 'owner', item(), 400, error(INVALID), method='PATCH', raw_body='keep=true',
    content_type='application/x-www-form-urlencoded')
add('keep-true', 'owner', item(), 200, KEPT, method='PATCH', body={'keep': True, 'ignored': 1},
    run=runner(200, KEPT, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]),
    audit=logged('backup_keep', ARCHIVE, backup=ARCHIVE))
add('keep-false-admin', 'admin', item(SECOND), 200, {'ok': True}, method='PATCH', body={'keep': False, 'password': PASSWORD},
    cache=attempts_preset(ADMIN, 2), cache_after={},
    run=runner(201, {'ok': True}, expect=[call('PATCH', f'/backups/{SECOND}', {'keep': False})]),
    audit=logged('backup_unkeep', SECOND, actor='admin', association='admin_association', backup=SECOND))
add('unkeep-without-password', 'owner', item(), 400, {'password': [REQUIRED]}, method='PATCH', body={'keep': False})
add('unkeep-password-rules', 'owner', item(), 400, {'password': ['Ensure this field has no more than 256 characters.']},
    method='PATCH', body={'keep': False, 'password': 'p' * 257})
add('unkeep-wrong-password', 'owner', item(), 400, error(WRONG), method='PATCH', body={'keep': False, 'password': PASSWORD + 'x'},
    cache_after=attempts_preset(OWNER, 1, 'fresh'))
add('unkeep-too-many', 'owner', item(), 429, error(TOO_MANY), method='PATCH', body={'keep': False, 'password': PASSWORD},
    cache=attempts_preset(OWNER, 5), cache_after=attempts_preset(OWNER, 5, 'kept'))
add('unkeep-runner-409', 'owner', item(), 409, error('Backup in corso.'), method='PATCH', body={'keep': False, 'password': PASSWORD},
    run=runner(409, {'error': 'Backup in corso.'}, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': False})]))
add('keep-runner-list', 'owner', item(), 200, [1, 'x'], method='PATCH', body={'keep': True},
    run=runner(200, [1, 'x'], expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]),
    audit=logged('backup_keep', ARCHIVE, backup=ARCHIVE))
add('keep-runner-409', 'owner', item(), 409, error('Backup in corso.'), method='PATCH', body={'keep': True},
    run=runner(409, {'error': 'Backup in corso.'}, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-runner-404', 'owner', item(), 404, error('Archivio assente.'), method='PATCH', body={'keep': True},
    run=runner(404, {'error': 'Archivio assente.'}, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-runner-400-default', 'owner', item(), 400, error('Aggiornamento non disponibile.'), method='PATCH',
    body={'keep': True}, run=runner(400, {'detail': 'x'}, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-runner-500', 'owner', item(), 503, error("{'a': [1, None]}"), method='PATCH', body={'keep': True},
    run=runner(500, {'error': {'a': [1, None]}}, expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-runner-error-list', 'owner', item(), 500, SERVER, method='PATCH', body={'keep': True},
    run=runner(409, ['x'], expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-runner-invalid-json', 'owner', item(), 503, error(UNREACHABLE), method='PATCH', body={'keep': True},
    run=runner(200, '{"archive": ', expect=[call('PATCH', f'/backups/{ARCHIVE}', {'keep': True})]))
add('keep-no-socket', 'owner', item(), 503, error(UNREACHABLE), method='PATCH', body={'keep': True},
    run=runner(state='no-socket'))
add('keep-no-token', 'owner', item(), 503, error(UNREACHABLE), method='PATCH', body={'keep': True},
    run=runner(state='no-token'))

DELETED = {'deleted': ARCHIVE}
add('delete-not-an-archive', 'owner', item('backup.tar.gz'), 404, error(NOT_FOUND), method='DELETE')
add('delete-no-body', 'owner', item(), 400, {'password': [REQUIRED]}, method='DELETE')
add('delete-without-confirm', 'owner', item(), 200, DELETED, method='DELETE', body={'password': PASSWORD},
    run=runner(200, DELETED, expect=[call('DELETE', f'/backups/{ARCHIVE}', {'confirm': False})]),
    audit=logged('backup_delete', ARCHIVE, backup=ARCHIVE))
add('delete-confirmed', 'owner', item(SECOND), 200, DELETED, method='DELETE', body={'confirm': True, 'password': PASSWORD},
    cache=attempts_preset(OWNER, 4), cache_after={},
    run=runner(200, DELETED, expect=[call('DELETE', f'/backups/{SECOND}', {'confirm': True})]),
    audit=logged('backup_delete', SECOND, backup=SECOND))
add('delete-confirmed-admin-upgrades-hash', 'admin', item(), 200, DELETED, method='DELETE',
    body={'confirm': False, 'password': PASSWORD}, password=LEGACY_HASH, rehash=True,
    run=runner(200, DELETED, expect=[call('DELETE', f'/backups/{ARCHIVE}', {'confirm': False})]),
    audit=logged('backup_delete', ARCHIVE, actor='admin', association='admin_association', backup=ARCHIVE))
add('delete-list-body', 'owner', item(), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='DELETE', body=[True])
add('delete-null-body', 'owner', item(), 400, {'non_field_errors': ['No data provided']}, method='DELETE', raw_body='null',
    content_type='application/json')
add('delete-confirm-text', 'owner', item(), 400, error(INVALID), method='DELETE', body={'confirm': 'yes', 'password': PASSWORD})
add('delete-confirm-null', 'owner', item(), 400, error(INVALID), method='DELETE', body={'confirm': None})
add('delete-password-types', 'owner', item(), 400, {'password': [BLANK]}, method='DELETE', body={'confirm': True, 'password': ''})
add('delete-password-null', 'owner', item(), 400, {'password': [NULL]}, method='DELETE', body={'confirm': True, 'password': None})
add('delete-wrong-password', 'owner', item(), 400, error(WRONG), method='DELETE', body={'confirm': True, 'password': ' ' + PASSWORD},
    cache_after=attempts_preset(OWNER, 1, 'fresh'))
add('delete-wrong-password-counts', 'admin', item(), 400, error(WRONG), method='DELETE', body={'confirm': True, 'password': 123},
    cache=attempts_preset(ADMIN, 4), cache_after=attempts_preset(ADMIN, 5, 'kept'))
add('delete-too-many', 'owner', item(), 429, error(TOO_MANY), method='DELETE', body={'confirm': True, 'password': PASSWORD},
    cache=attempts_preset(OWNER, 7), cache_after=attempts_preset(OWNER, 7, 'kept'))
add('delete-unusable-password', 'owner', item(), 400, error(WRONG), method='DELETE', body={'confirm': True, 'password': PASSWORD},
    password='!unusable-placeholder', cache_after=attempts_preset(OWNER, 1, 'fresh'))
add('delete-form-without-confirm', 'owner', item(), 400, {'password': [REQUIRED]}, method='DELETE', raw_body='other=1',
    content_type='application/x-www-form-urlencoded')
add('delete-form-confirm', 'owner', item(), 400, error(INVALID), method='DELETE', raw_body='confirm=true',
    content_type='application/x-www-form-urlencoded')
add('delete-runner-502', 'owner', item(), 503, error('Runner non pronto.'), method='DELETE', body={'confirm': True, 'password': PASSWORD},
    cache=attempts_preset(OWNER, 1), cache_after={},
    run=runner(502, {'error': 'Runner non pronto.'}, expect=[call('DELETE', f'/backups/{ARCHIVE}', {'confirm': True})]))
add('delete-runner-null', 'owner', item(), 200, '<none>', method='DELETE', body={'confirm': True, 'password': PASSWORD},
    run=runner(200, 'null', expect=[call('DELETE', f'/backups/{ARCHIVE}', {'confirm': True})]),
    audit=logged('backup_delete', ARCHIVE, backup=ARCHIVE))

# ---------------------------------------------------------------------------
# PUT admin/backups/schedule (ScheduleSerializer, relay, audit sorted JSON).
# ---------------------------------------------------------------------------
SCHEDULED = {'schedule': {'enabled': True, 'frequency': 'daily', 'time': '02:30', 'weekday': 6, 'keep': 7}}


def schedule_ok(name, body, value, *, actor='owner', association='own_association', reply=SCHEDULED, status=200):
    add(name, actor, SCHEDULE, status, reply, method='PUT', body=body,
        run=runner(status, reply, expect=[call('PUT', '/backups/schedule', value)]),
        audit=logged('backup_schedule', json.dumps(value, sort_keys=True), actor=actor, association=association)
        if status == 200 else None)


add('schedule-empty', 'owner', SCHEDULE, 400, {'enabled': [REQUIRED], 'frequency': [REQUIRED], 'time': [REQUIRED],
                                               'keep': [REQUIRED]}, method='PUT', body={})
add('schedule-null-body', 'owner', SCHEDULE, 400, {'non_field_errors': ['No data provided']}, method='PUT',
    raw_body='null', content_type='application/json')
add('keep-malformed-json', 'owner', item(), 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    method='PATCH', raw_body='{keep: true}', content_type='application/json')
add('schedule-list', 'owner', SCHEDULE, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PUT', body=[1])
add('schedule-nulls', 'owner', SCHEDULE, 400, {key: [NULL] for key in ('enabled', 'frequency', 'time', 'weekday', 'keep')},
    method='PUT', body={key: None for key in ('enabled', 'frequency', 'time', 'weekday', 'keep')})
add('schedule-wrong-types', 'owner', SCHEDULE, 400,
    {'enabled': [BOOLEAN], 'frequency': ['"True" is not a valid choice.'], 'time': [PATTERN], 'weekday': [INTEGER],
     'keep': [INTEGER]}, method='PUT', body={'enabled': 'maybe', 'frequency': True, 'time': ['02:30'], 'weekday': True,
                                             'keep': 7.5})
add('schedule-ranges', 'owner', SCHEDULE, 400,
    {'enabled': [BOOLEAN], 'frequency': ['"monthly" is not a valid choice.'], 'time': [PATTERN],
     'weekday': ['Ensure this value is less than or equal to 6.'], 'keep': ['Ensure this value is greater than or equal to 1.']},
    method='PUT', body={'enabled': 2, 'frequency': 'monthly', 'time': '24:00', 'weekday': 7, 'keep': 0})
add('schedule-ranges-other-side', 'owner', SCHEDULE, 400,
    {'enabled': [BOOLEAN], 'frequency': ['"" is not a valid choice.'], 'time': [BLANK],
     'weekday': ['Ensure this value is greater than or equal to 0.'], 'keep': ['Ensure this value is less than or equal to 30.']},
    method='PUT', body={'enabled': '', 'frequency': '', 'time': '  ', 'weekday': -1, 'keep': '31'})
add('schedule-integer-forms', 'owner', SCHEDULE, 400,
    {'weekday': [INTEGER], 'keep': ['String value too large.']},
    method='PUT', body={'enabled': True, 'frequency': 'daily', 'time': '02:30', 'weekday': '1e0', 'keep': '1' * 1001})
add('schedule-time-forms', 'owner', SCHEDULE, 400, {'time': [PATTERN]},
    method='PUT', body={'enabled': True, 'frequency': 'weekly', 'time': '2:30', 'keep': 3})
add('schedule-time-number', 'owner', SCHEDULE, 400, {'time': [PATTERN]},
    method='PUT', body={'enabled': True, 'frequency': 'weekly', 'time': 230, 'keep': 3})
add('schedule-italian', 'owner', SCHEDULE, 400,
    {'enabled': ['Must be a valid boolean.'], 'frequency': ['"mensile" non è una scelta valida.'],
     'time': ['Questo valore non corrisponde alla sequenza richiesta.'],
     'weekday': ['Assicurati che il valore sia minore o uguale a 6.'], 'keep': ['È richiesto un numero intero valido.']},
    method='PUT', body={'enabled': 'x', 'frequency': 'mensile', 'time': '25:00', 'weekday': 9, 'keep': 'x'},
    headers={'Accept-Language': 'it'})
add('schedule-italian-required', 'owner', SCHEDULE, 400,
    {'enabled': ['Campo obbligatorio.'], 'frequency': ['Campo obbligatorio.'], 'time': ['Questo campo non può essere omesso.'],
     'keep': ['Il campo non può essere nullo.'], 'weekday': ['Assicurati che il valore sia maggiore o uguale a 0.']},
    method='PUT', body={'time': '', 'keep': None, 'weekday': -3}, headers={'Accept-Language': 'it-IT,it;q=0.9'})
schedule_ok('schedule-saved', {'enabled': True, 'frequency': 'daily', 'time': '02:30', 'keep': 7},
            {'enabled': True, 'frequency': 'daily', 'time': '02:30', 'weekday': 6, 'keep': 7})
schedule_ok('schedule-coerced', {'keep': '7.0', 'weekday': ' 3 ', 'time': ' 23:59\n', 'frequency': 'weekly', 'enabled': 'OFF',
                                 'extra': 'ignored'},
            {'enabled': False, 'frequency': 'weekly', 'time': '23:59', 'weekday': 3, 'keep': 7}, actor='admin',
            association='admin_association')
schedule_ok('schedule-numbers', {'enabled': 1.0, 'frequency': 'daily', 'time': '00:00', 'weekday': 0.0, 'keep': 30},
            {'enabled': True, 'frequency': 'daily', 'time': '00:00', 'weekday': 0, 'keep': 30})
schedule_ok('schedule-unicode-digits', {'enabled': 't', 'frequency': 'daily', 'time': '1٢:3٠', 'weekday': '٣', 'keep': '1_0'},
            {'enabled': True, 'frequency': 'daily', 'time': '1٢:3٠', 'weekday': 3, 'keep': 10})
schedule_ok('schedule-runner-409', {'enabled': True, 'frequency': 'daily', 'time': '02:30', 'keep': 7},
            {'enabled': True, 'frequency': 'daily', 'time': '02:30', 'weekday': 6, 'keep': 7},
            reply={'error': 'Pianificazione non valida.'}, status=409)
add('schedule-runner-404', 'owner', SCHEDULE, 409, error(NEWER), method='PUT',
    body={'enabled': False, 'frequency': 'weekly', 'time': '01:00', 'weekday': 2, 'keep': 1},
    run=runner(404, {'error': 'Unknown endpoint.'}, expect=[call('PUT', '/backups/schedule', {
        'enabled': False, 'frequency': 'weekly', 'time': '01:00', 'weekday': 2, 'keep': 1})]))
add('schedule-runner-503', 'owner', SCHEDULE, 503, error('Occupato.'), method='PUT',
    body={'enabled': False, 'frequency': 'weekly', 'time': '01:00', 'weekday': 2, 'keep': 1},
    run=runner(503, {'error': 'Occupato.'}, expect=[call('PUT', '/backups/schedule', {
        'enabled': False, 'frequency': 'weekly', 'time': '01:00', 'weekday': 2, 'keep': 1})]))
add('schedule-no-socket', 'owner', SCHEDULE, 503, error(UNREACHABLE), method='PUT',
    body={'enabled': False, 'frequency': 'weekly', 'time': '01:00', 'keep': 1}, run=runner(state='no-socket'))

# ---------------------------------------------------------------------------
# POST admin/backups/<name>/restore: the name, RestoreSerializer, the word,
# the shared password attempts, the password, relay (202), audit.
# ---------------------------------------------------------------------------
RESTORE_OK = {'password': PASSWORD, 'confirm': 'RIPRISTINA'}
OPERATION = {'operation': {'id': 'op-restore-1', 'kind': 'restore', 'stage': 'queued'}}


def restore_call(name=ARCHIVE, actor=OWNER):
    return call('POST', f'/backups/{name}/restore', {'request_id': '$uuid4', 'actor_id': actor, 'confirm': 'RIPRISTINA'})


attempts = attempts_preset


add('restore-not-an-archive', 'owner', restore_path('notes.txt'), 404, error(NOT_FOUND), method='POST', body={})
add('restore-empty', 'owner', restore_path(), 400, {'password': [REQUIRED], 'confirm': [REQUIRED]}, method='POST', body={})
add('restore-list', 'owner', restore_path(), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=[])
add('restore-field-rules', 'owner', restore_path(), 400,
    {'password': ['Ensure this field has no more than 256 characters.'], 'confirm': ['Ensure this field has no more than 32 characters.']},
    method='POST', body={'password': 'p' * 257, 'confirm': 'R' * 33})
add('restore-field-types', 'owner', restore_path(), 400, {'password': [BLANK], 'confirm': [STRING]},
    method='POST', body={'password': '', 'confirm': True})
add('restore-blank-word', 'owner', restore_path(), 400, {'confirm': [BLANK]}, method='POST', body={'password': 'x', 'confirm': '  '})
add('restore-wrong-word', 'owner', restore_path(), 400, error(CONFIRM_WORD), method='POST',
    body={'password': PASSWORD, 'confirm': 'ripristina'}, cache=attempts(OWNER, 5), cache_after=attempts(OWNER, 5, 'kept'))
add('restore-too-many', 'owner', restore_path(), 429, error(TOO_MANY), method='POST', body=RESTORE_OK,
    cache=attempts(OWNER, 5), cache_after=attempts(OWNER, 5, 'kept'))
add('restore-wrong-password', 'owner', restore_path(), 400, error(WRONG), method='POST',
    body={'password': PASSWORD + ' ', 'confirm': 'RIPRISTINA'}, cache_after=attempts(OWNER, 1, 'fresh'))
add('restore-wrong-password-counts', 'admin', restore_path(), 400, error(WRONG), method='POST',
    body={'password': 12345, 'confirm': 'RIPRISTINA'}, cache=attempts(ADMIN, 4), cache_after=attempts(ADMIN, 5, 'kept'))
add('restore-unusable-password', 'owner', restore_path(), 400, error(WRONG), method='POST', body=RESTORE_OK,
    password='!unusable-placeholder', cache_after=attempts(OWNER, 1, 'fresh'))
add('restore-accepted', 'owner', restore_path(), 202, OPERATION, method='POST', body=RESTORE_OK,
    cache=attempts(OWNER, 3), cache_after={},
    run=runner(202, OPERATION, expect=[restore_call()]),
    audit=logged('backup_restore', ARCHIVE, backup=ARCHIVE, operation='op-restore-1'))
add('restore-accepted-admin-trimmed-word', 'admin', restore_path(SECOND), 202, {'operation': {'id': 77}}, method='POST',
    body={'password': PASSWORD, 'confirm': ' RIPRISTINA\n'},
    run=runner(200, {'operation': {'id': 77}}, expect=[restore_call(SECOND, ADMIN)]),
    audit=logged('backup_restore', SECOND, actor='admin', association='admin_association', backup=SECOND, operation=77))
add('restore-accepted-no-id', 'owner', restore_path(), 202, {'operation': {}}, method='POST', body=RESTORE_OK,
    run=runner(202, {'operation': {}}, expect=[restore_call()]),
    audit=logged('backup_restore', ARCHIVE, backup=ARCHIVE, operation=''))
add('restore-upgrades-hash', 'owner', restore_path(), 202, OPERATION, method='POST', body=RESTORE_OK, password=LEGACY_HASH,
    rehash=True, run=runner(202, OPERATION, expect=[restore_call()]),
    audit=logged('backup_restore', ARCHIVE, backup=ARCHIVE, operation='op-restore-1'))
add('restore-runner-without-operation', 'owner', restore_path(), 500, SERVER, method='POST', body=RESTORE_OK,
    cache=attempts(OWNER, 2), cache_after={}, run=runner(202, {'queued': True}, expect=[restore_call()]))
add('restore-runner-409', 'owner', restore_path(), 409, error('Il backup è di una versione più recente.'), method='POST',
    body=RESTORE_OK, run=runner(409, {'error': 'Il backup è di una versione più recente.'}, expect=[restore_call()]))
add('restore-runner-404', 'owner', restore_path(), 404, error('Aggiornamento non disponibile.'), method='POST',
    body=RESTORE_OK, run=runner(404, {}, expect=[restore_call()]))
add('restore-no-socket', 'owner', restore_path(), 503, error(UNREACHABLE), method='POST', body=RESTORE_OK,
    cache=attempts(OWNER, 1), cache_after={}, run=runner(state='no-socket'))

# ---------------------------------------------------------------------------
# POST admin/backups/upload: the raw body, streamed to the runner.
# ---------------------------------------------------------------------------
def archive_bytes(spec):
    """{'seed', 'size'}: gzip magic then random.Random(seed).randbytes(size - 4) (the contract's materialize)."""
    return b'\x1f\x8b\x08\x00' + random.Random(spec['seed']).randbytes(spec['size'] - 4)


SMALL_SPEC, LARGE_SPEC = {'seed': 1, 'size': 4096}, {'seed': 2, 'size': 2 * 1024 * 1024 + 17}
SMALL, LARGE = archive_bytes(SMALL_SPEC), archive_bytes(LARGE_SPEC)
# ASSOZETA_BACKUP_UPLOAD_MAX_BYTES of the run: the large archive is exactly at the limit.
UPLOAD_MAX = len(LARGE)
SPECS = {SMALL: SMALL_SPEC, LARGE: LARGE_SPEC}


def encoded(data):
    return dict(SPECS[data]) if data in SPECS else {'b64': base64.b64encode(data).decode()}
UPLOADED = {'archive': {'name': 'assozeta-20261109T100000Z.tar.gz', 'origin': 'uploaded'}}


def upload_call(data):
    return {'method': 'POST', 'path': '/backups/upload',
            'headers': headers_for(content_type='application/gzip', length=str(len(data))),
            'body': encoded(data)}


def early_call(data):
    value = upload_call(data)
    value['body'] = 'unread'
    return value


def sent(data):
    return encoded(data)


add('upload-empty', 'owner', UPLOAD, 411, error(CHOOSE_FILE), method='POST', send=sent(b''))
add('upload-chunked', 'owner', UPLOAD, 411, error(CHOOSE_FILE), method='POST', send={**sent(SMALL), 'chunked': True})
add('upload-stored', 'owner', UPLOAD, 201, UPLOADED, method='POST', send=sent(SMALL),
    run=runner(201, UPLOADED, expect=[upload_call(SMALL)]),
    audit=logged('backup_upload', 'assozeta-20261109T100000Z.tar.gz', backup='assozeta-20261109T100000Z.tar.gz', size=len(SMALL)))
add('upload-large-admin', 'admin', UPLOAD + '?ignored=1', 201, UPLOADED, method='POST', send=sent(LARGE),
    headers={'Content-Type': 'application/octet-stream'},
    run=runner(200, UPLOADED, expect=[upload_call(LARGE)]),
    audit=logged('backup_upload', 'assozeta-20261109T100000Z.tar.gz', actor='admin', association='admin_association',
                 backup='assozeta-20261109T100000Z.tar.gz', size=len(LARGE)))
add('upload-over-the-limit', 'owner', UPLOAD, 413, {'error': TOO_LARGE, 'max_bytes': UPLOAD_MAX}, method='POST',
    send={'seed': 3, 'size': UPLOAD_MAX + 1, 'early': True})
add('upload-over-the-limit-admin', 'admin', UPLOAD + '?size=1', 413, {'error': TOO_LARGE, 'max_bytes': UPLOAD_MAX}, method='POST',
    send={'seed': 4, 'size': UPLOAD_MAX + 65536, 'early': True}, headers={'Content-Type': 'application/octet-stream'})
add('upload-one-byte', 'owner', UPLOAD, 201, {'archive': None, 'extra': [1.5]}, method='POST', send=sent(b'x'),
    run=runner(201, {'archive': None, 'extra': [1.5]}, expect=[upload_call(b'x')]),
    audit=logged('backup_upload', '', backup='', size=1))
add('upload-name-number', 'owner', UPLOAD, 201, {'archive': {'name': 7}}, method='POST', send=sent(SMALL),
    run=runner(201, {'archive': {'name': 7}}, expect=[upload_call(SMALL)]),
    audit=logged('backup_upload', '7', backup=7, size=len(SMALL)))
add('upload-reply-without-archive', 'owner', UPLOAD, 201, {}, method='POST', send=sent(SMALL),
    run=runner(201, {}, expect=[upload_call(SMALL)]), audit=logged('backup_upload', '', backup='', size=len(SMALL)))
add('upload-reply-list', 'owner', UPLOAD, 500, SERVER, method='POST', send=sent(SMALL),
    run=runner(201, [UPLOADED], expect=[upload_call(SMALL)]))
add('upload-reply-archive-text', 'owner', UPLOAD, 500, SERVER, method='POST', send=sent(SMALL),
    run=runner(201, {'archive': 'x'}, expect=[upload_call(SMALL)]))
add('upload-invalid-archive', 'owner', UPLOAD, 400, error('Il file non è un backup valido di Assozeta.'), method='POST',
    send=sent(SMALL), run=runner(400, {'error': 'Il file non è un backup valido di Assozeta.'}, expect=[upload_call(SMALL)]))
add('upload-early-refusal', 'owner', UPLOAD, 409, error('Spazio su disco insufficiente.'), method='POST', send=sent(SMALL),
    run=runner(409, {'error': 'Spazio su disco insufficiente.'}, early=True, expect=[early_call(SMALL)]))
add('upload-runner-413-default', 'owner', UPLOAD, 413, error('Caricamento non riuscito.'), method='POST', send=sent(SMALL),
    run=runner(413, {}, early=True, expect=[early_call(SMALL)]))
add('upload-runner-404', 'owner', UPLOAD, 409, error(NEWER), method='POST', send=sent(SMALL),
    run=runner(404, {'error': 'Unknown endpoint.'}, early=True, expect=[early_call(SMALL)]))
add('upload-runner-500', 'owner', UPLOAD, 503, error('Errore interno.'), method='POST', send=sent(SMALL),
    run=runner(500, {'error': 'Errore interno.'}, expect=[upload_call(SMALL)]))
add('upload-runner-error-list', 'owner', UPLOAD, 500, SERVER, method='POST', send=sent(SMALL),
    run=runner(400, ['x'], expect=[upload_call(SMALL)]))
add('upload-runner-invalid-json', 'owner', UPLOAD, 503, error(UNREACHABLE), method='POST', send=sent(SMALL),
    run=runner(201, 'stored', expect=[upload_call(SMALL)]))
add('upload-no-socket', 'owner', UPLOAD, 503, error(UNREACHABLE), method='POST', send=sent(SMALL), run=runner(state='no-socket'))
add('upload-no-token', 'owner', UPLOAD, 503, error(UNREACHABLE), method='POST', send=sent(SMALL), run=runner(state='no-token'))

# ---------------------------------------------------------------------------
# admin/backups/remote*: relay with Cache-Control: no-store on success; a
# runner 404 is "older runner" only for 'Unknown endpoint.'.
# ---------------------------------------------------------------------------
REMOTE_STATE = {'configured': True, 'endpoint': 'https://s3.example.test', 'bucket': 'copie', 'enabled': False, 'key_id': None}
add('remote-get', 'owner', REMOTE, 200, REMOTE_STATE, run=runner(200, REMOTE_STATE, expect=[call('GET', '/backups/remote', None)]),
    no_store=True)
add('remote-get-admin', 'admin', REMOTE, 200, REMOTE_STATE,
    run=runner(200, REMOTE_STATE, expect=[call('GET', '/backups/remote', None)]), no_store=True)
add('remote-get-unknown-endpoint', 'owner', REMOTE, 409, error(NEWER),
    run=runner(404, {'error': 'Unknown endpoint.'}, expect=[call('GET', '/backups/remote', None)]))
add('remote-get-404', 'owner', REMOTE, 404, error('Unknown endpoint'),
    run=runner(404, {'error': 'Unknown endpoint'}, expect=[call('GET', '/backups/remote', None)]))
add('remote-get-500', 'owner', REMOTE, 503, error('Aggiornamento non disponibile.'),
    run=runner(500, {}, expect=[call('GET', '/backups/remote', None)]))
add('remote-get-no-token', 'owner', REMOTE, 503, error(UNREACHABLE), run=runner(state='no-token'))
add('remote-get-error-text', 'owner', REMOTE, 503, error(UNREACHABLE),
    run=runner(400, 'Bad Request', expect=[call('GET', '/backups/remote', None)]))
add('remote-head', 'owner', REMOTE, 200, '<empty>', method='HEAD',
    run=runner(200, REMOTE_STATE, expect=[call('GET', '/backups/remote', None)]), no_store=True)

SETTINGS_FULL = {'enabled': True, 'secret_key': ' segreto-placeholder ', 'path_style': 'true', 'access_key': ' chiave ',
                 'retention': '5', 'prefix': '', 'bucket': 'copie', 'region': 'eu-south-1', 'endpoint': 'https://s3.example.test'}
SETTINGS_VALUE = {'endpoint': 'https://s3.example.test', 'region': 'eu-south-1', 'bucket': 'copie', 'prefix': '',
                  'access_key': 'chiave', 'secret_key': 'segreto-placeholder', 'path_style': True, 'retention': 5,
                  'enabled': True}
SETTINGS_REPLY = {'configured': True, 'enabled': True}


def settings_change(value):
    change = {key: value[key] for key in ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'path_style', 'retention',
                                          'enabled') if key in value}
    if value.get('secret_key'):
        change['secret_key'] = 'changed'
    return json.dumps(change, sort_keys=True)


def settings_ok(name, body, value, *, actor='owner', association='own_association'):
    add(name, actor, REMOTE, 200, SETTINGS_REPLY, method='PUT', body=body,
        run=runner(200, SETTINGS_REPLY, expect=[call('PUT', '/backups/remote', value)]),
        audit=logged('backup_remote_settings', settings_change(value), actor=actor, association=association), no_store=True)


add('remote-put-list', 'owner', REMOTE, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PUT', body=['x'])
add('remote-put-rules', 'owner', REMOTE, 400,
    {'endpoint': ['Ensure this field has no more than 255 characters.'], 'region': [STRING], 'bucket': [NULL],
     'prefix': ['Ensure this field has no more than 200 characters.'], 'access_key': [STRING],
     'secret_key': ['Ensure this field has no more than 256 characters.'], 'path_style': [BOOLEAN],
     'retention': ['Ensure this value is less than or equal to 30.'], 'enabled': [NULL], 'password': [STRING]},
    method='PUT', body={'password': ['x'], 'endpoint': 'e' * 256, 'region': False, 'bucket': None, 'prefix': 'p' * 201, 'access_key': {'a': 1},
                        'secret_key': 's' * 257, 'path_style': 'sometimes', 'retention': 31, 'enabled': None})
add('remote-put-bucket-length', 'owner', REMOTE, 400,
    {'bucket': ['Ensure this field has no more than 63 characters.'], 'retention': ['Ensure this value is greater than or equal to 1.'],
     'region': ['Null characters are not allowed.'], 'password': [REQUIRED]},
    method='PUT', body={'bucket': ' ' + 'b' * 64 + ' ', 'retention': 0, 'region': 'eu\x00'})
settings_ok('remote-put-full', {**SETTINGS_FULL, 'password': PASSWORD}, SETTINGS_VALUE)
settings_ok('remote-put-empty', {'password': PASSWORD}, {})
settings_ok('remote-put-blank-secret', {'secret_key': '   ', 'enabled': 0, 'endpoint': 'https://è.example.test', 'retention': 3.0,
                                        'password': PASSWORD},
            {'endpoint': 'https://è.example.test', 'secret_key': '', 'retention': 3, 'enabled': False}, actor='admin',
            association='admin_association')
settings_ok('remote-put-numbers-as-text', {'region': 12, 'prefix': 1.5, 'access_key': '  ', 'path_style': 'N', 'password': PASSWORD},
            {'region': '12', 'prefix': '1.5', 'access_key': '', 'path_style': False})
settings_ok('remote-put-lan-endpoint', {'endpoint': ' https://192.168.1.20:9000/minio ', 'path_style': True, 'password': PASSWORD},
            {'endpoint': 'https://192.168.1.20:9000/minio', 'path_style': True})
settings_ok('remote-put-blank-endpoint', {'endpoint': '', 'enabled': True, 'password': PASSWORD}, {'endpoint': '', 'enabled': True})
add('remote-put-without-password', 'owner', REMOTE, 400, {'password': [REQUIRED]}, method='PUT', body={'enabled': False})
add('remote-put-password-italian', 'owner', REMOTE, 400, {'retention': ['Assicurati che il valore sia minore o uguale a 30.'],
                                                          'password': ['Questo campo non può essere omesso.']},
    method='PUT', body={'retention': 31, 'password': ''}, headers={'Accept-Language': 'it'})
add('remote-put-wrong-password', 'owner', REMOTE, 400, error(WRONG), method='PUT',
    body={'secret_key': 'altro-segreto', 'password': PASSWORD.lower()}, cache=attempts(OWNER, 2), cache_after=attempts(OWNER, 3, 'kept'))
add('remote-put-too-many', 'admin', REMOTE, 429, error(TOO_MANY), method='PUT', body={'enabled': False, 'password': PASSWORD},
    cache=attempts(ADMIN, 5), cache_after=attempts(ADMIN, 5, 'kept'))
add('remote-put-endpoint-before-password', 'owner', REMOTE, 400, {'endpoint': [BLOCKED_ENDPOINT]}, method='PUT',
    body={'endpoint': 'https://169.254.169.254/latest', 'password': 'wrong'})
add('remote-put-http-endpoint', 'owner', REMOTE, 400, {'endpoint': [HTTPS_ENDPOINT], 'password': [REQUIRED]}, method='PUT',
    body={'endpoint': 'http://minio.lan:9000'})
add('remote-put-runner-422-body', 'owner', REMOTE, 422, error('Bucket non raggiungibile.'), method='PUT',
    body={'bucket': 'x', 'password': PASSWORD}, cache=attempts(OWNER, 3), cache_after={},
    run=runner(422, {'error': 'Bucket non raggiungibile.'}, expect=[call('PUT', '/backups/remote', {'bucket': 'x'})]))
add('remote-put-unknown-endpoint', 'owner', REMOTE, 409, error(NEWER), method='PUT', body={'enabled': False, 'password': PASSWORD},
    run=runner(404, {'error': 'Unknown endpoint.'}, expect=[call('PUT', '/backups/remote', {'enabled': False})]))
add('remote-put-no-socket', 'owner', REMOTE, 503, error(UNREACHABLE), method='PUT', body={'enabled': False, 'password': PASSWORD},
    run=runner(state='no-socket'))

TESTED = {'ok': True, 'message': 'Connessione riuscita.'}
add('remote-test-no-body', 'owner', REMOTE_TEST, 200, TESTED, method='POST',
    run=runner(200, TESTED, expect=[call('POST', '/backups/remote/test', {})]), no_store=True)
add('remote-test-empty-list', 'owner', REMOTE_TEST, 200, TESTED, method='POST', body=[],
    run=runner(200, TESTED, expect=[call('POST', '/backups/remote/test', {})]), no_store=True)
add('remote-test-list', 'owner', REMOTE_TEST, 400,
    {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST', body=[{}])
add('remote-test-without-enabled', 'owner', REMOTE_TEST, 200, TESTED, method='POST',
    body={'enabled': True, 'endpoint': ' https://s3.example.test ', 'secret_key': 'segreto-placeholder', 'retention': 2},
    run=runner(200, TESTED, expect=[call('POST', '/backups/remote/test', {
        'endpoint': 'https://s3.example.test', 'secret_key': 'segreto-placeholder', 'retention': 2})]), no_store=True)
add('remote-test-invalid', 'owner', REMOTE_TEST, 400, {'enabled': [BOOLEAN]}, method='POST', body={'enabled': 'x'})
for index, (endpoint, message) in enumerate((
        ('http://s3.example.test', HTTPS_ENDPOINT), ('http://192.168.1.20:9000', HTTPS_ENDPOINT),
        ('https://169.254.169.254', BLOCKED_ENDPOINT), ('https://169.254.169.254./latest/meta-data', BLOCKED_ENDPOINT),
        ('http://169.254.170.2/v2/credentials', BLOCKED_ENDPOINT), ('https://[fd00:ec2::254]', BLOCKED_ENDPOINT),
        ('https://[::ffff:169.254.169.254]:443', BLOCKED_ENDPOINT), ('https://[64:ff9b::a9fe:a9fe]', BLOCKED_ENDPOINT),
        ('https://127.0.0.1:9000', BLOCKED_ENDPOINT), ('https://LOCALHOST', BLOCKED_ENDPOINT), ('https://api.localhost.', BLOCKED_ENDPOINT),
        ('https://metadata.google.internal/computeMetadata', BLOCKED_ENDPOINT), ('https://[::1]', BLOCKED_ENDPOINT),
        ('https://0.0.0.0', BLOCKED_ENDPOINT), ('https://100.100.100.200', BLOCKED_ENDPOINT), ('https://[fe80::1]', BLOCKED_ENDPOINT),
        ('https://255.255.255.255', BLOCKED_ENDPOINT),
        ('https://2852039166', INVALID_ENDPOINT), ('https://0xa9fea9fe', INVALID_ENDPOINT), ('https://169.254.169.254.1', INVALID_ENDPOINT),
        ('https://0251.0376.0251.0376', INVALID_ENDPOINT), ('https://user:secret@s3.example.test', INVALID_ENDPOINT),
        ('https://s3.example.test:0', INVALID_ENDPOINT), ('https://s3.example.test:65536', INVALID_ENDPOINT),
        ('https://s3.example.test/?x=1', INVALID_ENDPOINT), ('HTTPS://s3.example.test', INVALID_ENDPOINT),
        ('ftp://s3.example.test', INVALID_ENDPOINT), ('https://[fe80::1%25en0]', INVALID_ENDPOINT), ('https://::1', INVALID_ENDPOINT),
        ('https://[1::2::3]', INVALID_ENDPOINT), ('https://.', INVALID_ENDPOINT), ('https://s3 .example.test', INVALID_ENDPOINT),
        ('https://s3\x0b.example.test', INVALID_ENDPOINT))):
    add(f'remote-test-endpoint-{index}', 'owner', REMOTE_TEST, 400, {'endpoint': [message]}, method='POST',
        body={'endpoint': endpoint, 'bucket': 'b'})
add('remote-test-endpoint-italian', 'owner', REMOTE_TEST, 400,
    {'endpoint': [BLOCKED_ENDPOINT], 'retention': ['Assicurati che il valore sia maggiore o uguale a 1.']}, method='POST',
    body={'endpoint': 'https://[::]', 'retention': 0}, headers={'Accept-Language': 'it'})
for index, endpoint in enumerate(('https://10.0.0.5', 'https://172.16.0.1:9000/', 'https://s3.eu-south-1.amazonaws.com',
                                  'https://[2001:db8::5]:9000/base', 'https://[::ffff:192.168.1.20]', 'https://minio.lan.',
                                  'https://8.8.8.8', 'https://xn--s3-fka.example.test', 'https://s3.example.test:65535/a/b')):
    add(f'remote-test-endpoint-allowed-{index}', 'owner', REMOTE_TEST, 200, TESTED, method='POST',
        body={'endpoint': endpoint}, run=runner(200, TESTED, expect=[call('POST', '/backups/remote/test', {'endpoint': endpoint})]),
        no_store=True)
add('remote-test-refused', 'owner', REMOTE_TEST, 400, error('Accesso negato dal bucket.'), method='POST', body={'bucket': 'b'},
    run=runner(400, {'error': 'Accesso negato dal bucket.'}, expect=[call('POST', '/backups/remote/test', {'bucket': 'b'})]))

KEY = {'key_id': 'a1b2c3d4', 'recovery_key': 'AGE-SECRET-KEY-PLACEHOLDER', 'public_key': 'age1placeholder'}
add('remote-key-generated', 'owner', REMOTE_KEY, 201, KEY, method='POST', body={'ignored': True, 'password': PASSWORD},
    cache=attempts(OWNER, 4), cache_after={},
    run=runner(200, KEY, expect=[call('POST', '/backups/remote/key', {})]),
    audit=logged('backup_remote_key_generated', 'a1b2c3d4'), no_store=True)
add('remote-key-without-id', 'admin', REMOTE_KEY, 201, {'recovery_key': 'x'}, method='POST', body={'password': PASSWORD},
    run=runner(201, {'recovery_key': 'x'}, expect=[call('POST', '/backups/remote/key', {})]),
    audit=logged('backup_remote_key_generated', '', actor='admin', association='admin_association'), no_store=True)
add('remote-key-number-id', 'owner', REMOTE_KEY, 201, {'key_id': 12}, method='POST', body={'password': PASSWORD},
    run=runner(201, {'key_id': 12}, expect=[call('POST', '/backups/remote/key', {})]),
    audit=logged('backup_remote_key_generated', '12'), no_store=True)
add('remote-key-reply-list', 'owner', REMOTE_KEY, 500, SERVER, method='POST', body={'password': PASSWORD},
    run=runner(201, ['x'], expect=[call('POST', '/backups/remote/key', {})]))
add('remote-key-refused', 'owner', REMOTE_KEY, 409, error('Una chiave esiste già.'), method='POST', body={'password': PASSWORD},
    run=runner(409, {'error': 'Una chiave esiste già.'}, expect=[call('POST', '/backups/remote/key', {})]))
add('remote-key-no-body', 'owner', REMOTE_KEY, 400, {'password': [REQUIRED]}, method='POST')
add('remote-key-list-body', 'owner', REMOTE_KEY, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=[PASSWORD])
add('remote-key-wrong-password', 'owner', REMOTE_KEY, 400, error(WRONG), method='POST', body={'password': PASSWORD[:-1]},
    cache_after=attempts(OWNER, 1, 'fresh'))
add('remote-key-fifth-wrong-password', 'owner', REMOTE_KEY, 400, error(WRONG), method='POST', body={'password': 'x'},
    cache=attempts(OWNER, 4), cache_after=attempts(OWNER, 5, 'kept'))
add('remote-key-too-many', 'owner', REMOTE_KEY, 429, error(TOO_MANY), method='POST', body={'password': PASSWORD},
    cache=attempts(OWNER, 5), cache_after=attempts(OWNER, 5, 'kept'))
add('remote-key-upgrades-hash', 'owner', REMOTE_KEY, 201, KEY, method='POST', body={'password': PASSWORD}, password=LEGACY_HASH,
    rehash=True, run=runner(201, KEY, expect=[call('POST', '/backups/remote/key', {})]),
    audit=logged('backup_remote_key_generated', 'a1b2c3d4'), no_store=True)

add('key-confirm-empty', 'owner', KEY_CONFIRM, 400, {'key_id': [REQUIRED], 'tail': [REQUIRED], 'password': [REQUIRED]},
    method='POST', body={})
add('key-confirm-rules', 'owner', KEY_CONFIRM, 400,
    {'key_id': [PATTERN], 'tail': ['Ensure this field has at least 6 characters.']}, method='POST',
    body={'key_id': 'A1B2C3D4', 'tail': ' abc ', 'password': PASSWORD})
add('key-confirm-rules-other', 'owner', KEY_CONFIRM, 400,
    {'key_id': [PATTERN], 'tail': ['Ensure this field has no more than 6 characters.'], 'password': [STRING]}, method='POST',
    body={'key_id': 12345678.5, 'tail': 1234567, 'password': False})
add('key-confirm-italian', 'owner', KEY_CONFIRM, 400,
    {'key_id': ['Questo valore non corrisponde alla sequenza richiesta.'],
     'tail': ['Assicurati che questo campo abbia almeno 6 caratteri.'], 'password': ['Campo obbligatorio.']}, method='POST',
    body={'key_id': 'zzzzzzzz', 'tail': 'abc'}, headers={'Accept-Language': 'it'})
add('key-confirm-without-password', 'owner', KEY_CONFIRM, 400, {'password': [REQUIRED]}, method='POST',
    body={'key_id': 'a1b2c3d4', 'tail': 'qwerty'})
add('key-confirm-wrong-password', 'admin', KEY_CONFIRM, 400, error(WRONG), method='POST',
    body={'key_id': 'a1b2c3d4', 'tail': 'qwerty', 'password': 'nope'}, cache=attempts(ADMIN, 1), cache_after=attempts(ADMIN, 2, 'kept'))
add('key-confirm-too-many', 'owner', KEY_CONFIRM, 429, error(TOO_MANY), method='POST',
    body={'key_id': 'a1b2c3d4', 'tail': 'qwerty', 'password': PASSWORD}, cache=attempts(OWNER, 6), cache_after=attempts(OWNER, 6, 'kept'))
add('key-confirmed', 'owner', KEY_CONFIRM, 200, {'confirmed': True}, method='POST',
    body={'key_id': ' a1b2c3d4 ', 'tail': 123456, 'extra': 1, 'password': PASSWORD}, cache=attempts(OWNER, 1), cache_after={},
    run=runner(200, {'confirmed': True}, expect=[call('POST', '/backups/remote/key/confirm', {'key_id': 'a1b2c3d4', 'tail': '123456'})]),
    audit=logged('backup_remote_key_confirmed', 'a1b2c3d4'), no_store=True)
add('key-confirm-wrong-tail', 'owner', KEY_CONFIRM, 400, error('Le ultime cifre non corrispondono.'), method='POST',
    body={'key_id': 'a1b2c3d4', 'tail': 'qwerty', 'password': PASSWORD},
    run=runner(400, {'error': 'Le ultime cifre non corrispondono.'},
               expect=[call('POST', '/backups/remote/key/confirm', {'key_id': 'a1b2c3d4', 'tail': 'qwerty'})]))


def fetch_call(name=ARCHIVE, key='AGE-SECRET-KEY-1PLACEHOLDER', actor=OWNER):
    return call('POST', '/backups/remote/fetch', {'name': name, 'recovery_key': key, 'request_id': '$uuid4', 'actor_id': actor})


FETCHING = {'operation': {'id': 'op-fetch-1', 'kind': 'remote_fetch'}}
add('fetch-empty', 'owner', FETCH, 400, {'name': [REQUIRED], 'recovery_key': [REQUIRED]}, method='POST', body={})
add('fetch-rules', 'owner', FETCH, 400,
    {'name': [PATTERN], 'recovery_key': ['Ensure this field has no more than 4096 characters.']}, method='POST',
    body={'name': 'backup.tar.gz', 'recovery_key': 'k' * 4097})
add('fetch-types', 'owner', FETCH, 400, {'name': [PATTERN], 'recovery_key': [BLANK]}, method='POST',
    body={'name': ['x'], 'recovery_key': ''})
add('fetch-accepted', 'owner', FETCH, 202, FETCHING, method='POST',
    body={'name': f' {ARCHIVE} ', 'recovery_key': 'AGE-SECRET-KEY-1PLACEHOLDER'},
    run=runner(202, FETCHING, expect=[fetch_call()]), audit=logged('backup_remote_fetch', ARCHIVE, backup=ARCHIVE), no_store=True)
add('fetch-accepted-admin-untrimmed-key', 'admin', FETCH, 202, FETCHING, method='POST',
    body={'name': SECOND, 'recovery_key': '  chiave è  '},
    run=runner(200, FETCHING, expect=[fetch_call(SECOND, '  chiave è  ', ADMIN)]),
    audit=logged('backup_remote_fetch', SECOND, actor='admin', association='admin_association', backup=SECOND), no_store=True)
add('fetch-refused', 'owner', FETCH, 400, error('La chiave non apre questa copia.'), method='POST',
    body={'name': ARCHIVE, 'recovery_key': 'AGE-SECRET-KEY-1PLACEHOLDER'},
    run=runner(400, {'error': 'La chiave non apre questa copia.'}, expect=[fetch_call()]))
add('fetch-unknown-endpoint', 'owner', FETCH, 409, error(NEWER), method='POST',
    body={'name': ARCHIVE, 'recovery_key': 'AGE-SECRET-KEY-1PLACEHOLDER'},
    run=runner(404, {'error': 'Unknown endpoint.'}, expect=[fetch_call()]))
add('fetch-no-socket', 'owner', FETCH, 503, error(UNREACHABLE), method='POST',
    body={'name': ARCHIVE, 'recovery_key': 'AGE-SECRET-KEY-1PLACEHOLDER'}, run=runner(state='no-socket'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance/admin/backups actions', 'ids': IDS, 'instance': INSTANCE,
                              'runner_token': RUNNER_TOKEN, 'hashes': HASHES, 'password': PASSWORD,
                              'upload_max_bytes': UPLOAD_MAX, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
