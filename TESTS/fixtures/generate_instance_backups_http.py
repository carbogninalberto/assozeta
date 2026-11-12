"""Author the instance backups HTTP sequence (instance-backups.md).

Pins BE/instance/backups.py (BackupOverviewView GET and POST,
BackupDownloadTokenView, BackupDownloadView) with updater_client.py
(call_runner, open_runner_stream) in Django's order: identity, the
IsInstanceOwner permission (the download view has none), method dispatch, the
runner call, the cached data sizes, the serializer, the password attempts, the
password check, the audit entry, the signed token, the single-use marker and
the relayed archive. The overview passes the runner's schedule, actions and
remote status through; its POST relays a manual backup (relay()) and records
an UPDATE entry with the runner's operation id.

The run is in production mode with ASSOZETA_UPDATER_DIRECTORY pointing at a
runner stub owned by the adapter (a Unix socket plus a token file). Per case:
- 'runner' scripts the stub: 'state' ('up', 'no-socket', 'no-token',
  'empty-token'), the response ('status', raw 'body' text or 'body_b64',
  'chunked'), and 'expect', the exact requests it must receive (method, path
  and every header, names lower case; 'json' is the exact json.dumps body,
  '$uuid4' standing for str(uuid4()));
- 'objects' are stored in the run's bucket during the request (object_bytes
  sums the whole bucket); 'bucket_absent' removes the bucket for the request;
- 'cache' presets Django cache values: 'sizes' ({'payload': value} through
  cache.set, or {'raw': text}), 'attempts' ({actor: count}) and 'used'
  (single-use token ids); 'cache_after' is the exact state of these keys
  afterwards ('sizes': 'kept' or the fresh value, '$database' standing for
  pg_database_size at request time);
- 'token' signs the '$token' in a download path ('claims' with '$id:<key>'
  user ids, 'age' seconds, 'salt', 'tamper', 'untimed');
- 'password' replaces the actor's stored hash for the request; 'audit' is
  the expected entry (ACCESS unless 'action' says otherwise); 'rehash'
  expects check_password's upgrade.
Every credential, host and name is a placeholder. Run with ``python3 -I``.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_backups_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
LEAVE = {'detail': 'Esci dall’impersonificazione per accedere all’amministrazione.'}
REQUIRED, NULL, BLANK = 'This field is required.', 'This field may not be null.', 'This field may not be blank.'
PATTERN, STRING = 'This value does not match the required pattern.', 'Not a valid string.'
UNREACHABLE = 'Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.'
OUTDATED = 'Il servizio di aggiornamento richiede una versione più recente per mostrare i backup.'
OUTDATED_ACTION = 'Il servizio di aggiornamento richiede una versione più recente.'
WRONG = 'Password non corretta.'
TOO_MANY = 'Troppi tentativi. Riprova tra qualche minuto.'
INVALID_LINK = 'Link di download non valido o scaduto.'
UNAUTHORIZED = 'Download non autorizzato.'
USED = 'Link di download già usato. Conferma di nuovo la password.'
OVERVIEW, TOKEN = '/instance/admin/backups', '/instance/admin/backups/download-token'
RUNNER_TOKEN = 'runner-token-placeholder'
MIB = 1024 * 1024
ARCHIVE = 'assozeta-20261101T020000Z.tar.gz'
SECOND = 'assozeta-20261102T020000Z-1.tar.gz'

INSTANCE = {
    'id': 7305, 'domain': 'instance-backups.example.test', 'name': 'Istanza di prova', 'abbreviation': 'IP',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'support_multiple_associations': False,
    'primary_association': 'own_association', 'google_client_id': '', 'apple_client_id': '', 'email_settings': {},
    'email_password_encrypted': '', 'email_revision': 0, 'diagnostic_results': {}, 'integration_settings': {},
}
REPR = 'Instance: Istanza di prova (instance-backups.example.test)'

PASSWORD = 'Fixture-backup-placeholder-1'


def pbkdf2(raw, salt, iterations):
    digest = hashlib.pbkdf2_hmac('sha256', raw.encode(), salt.encode(), iterations)
    return f'pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}'


# Seeded hashes: Django's current PBKDF2 cost and a salt of at least 128 bits
# of entropy (22+ characters), so a correct password is not rehashed.
HASHES = {'owner': pbkdf2(PASSWORD, 'backupfixtureowner0000001', 1_000_000),
          'admin': pbkdf2(PASSWORD, 'backupfixtureadmin0000001', 1_000_000)}
LEGACY_HASH = pbkdf2(PASSWORD, 'backupfixturelegacy000001', 1000)

cases = []


def runner_request(path, *, json_type=True, method='GET', payload=None):
    headers = {'host': 'localhost', 'accept-encoding': 'identity', 'authorization': f'Bearer {RUNNER_TOKEN}'}
    if json_type:
        headers['content-type'] = 'application/json'
    request = {'method': method, 'path': path, 'headers': headers}
    if payload is not None:
        # json.dumps(payload); str(uuid4()) has a fixed length, so the length is exact.
        text = json.dumps({key: ('0' * 36 if value == '$uuid4' else value) for key, value in payload.items()})
        headers['content-length'] = str(len(text.encode()))
        request['json'] = payload
    return request


def runner(status=200, body='', *, state='up', chunked=False, body_b64=None, path='/backups', json_type=True, method='GET',
           payload=None):
    spec = {'state': state, 'status': status, 'body': body, 'chunked': chunked,
            'expect': [runner_request(path, json_type=json_type, method=method, payload=payload)] if state == 'up' else []}
    if body_b64 is not None:
        spec['body_b64'] = body_b64
    return spec


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None,
        run=None, objects=None, bucket_absent=False, cache=None, cache_after=None, token=None, password=None, audit=None,
        rehash=False, stream=None, database=False):
    """run=None means the runner must receive no request (it is up)."""
    case = {'name': f'instance-backups-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'runner': run or {'state': 'up', 'expect': []},
            'objects': objects or {}, 'bucket_absent': bucket_absent, 'cache': cache or {},
            'cache_after': copy.deepcopy(cache_after if cache_after is not None else (cache or {})),
            'token': token, 'password': password, 'audit': audit, 'rehash': rehash, 'stream': stream, 'database': database}
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


def inventory(**extra):
    value = {'archives': ARCHIVES, 'retention': {'daily': 7, 'weekly': 4, 'monthly': 3},
             'disk': {'total': 500 * 1024 ** 3, 'used': 120 * 1024 ** 3, 'free': 380 * 1024 ** 3}, 'space_margin_mb': 2}
    value.update(extra)
    return {key: item for key, item in value.items() if item is not OMIT}


OMIT = object()
ARCHIVES = [{'name': SECOND, 'size': 73400320, 'created_at': '2026-11-02T02:00:00Z', 'verified': True},
            {'name': ARCHIVE, 'size': 71303168, 'created_at': '2026-11-01T02:00:00Z', 'verified': False, 'note': 'àè €'}]
SIZES = {'database': 3 * MIB, 'objects': 5 * MIB}
CACHED = {'sizes': {'payload': SIZES}}


def overview(inv, sizes, *, margin=2):
    disk = inv.get('disk', {})
    merged = {**disk, **sizes}
    next_backup = None
    if sizes['database'] == '$database' and sizes['objects'] is not None:
        # Resolved by the contract from the measured database size.
        next_backup = {'$next': {'objects': sizes['objects'], 'margin': margin, 'free': disk.get('free')}}
    elif sizes['database'] is not None and sizes['objects'] is not None:
        estimated = sizes['database'] + sizes['objects']
        required = estimated * 2 + margin * MIB
        free = disk.get('free')
        next_backup = {'estimated': estimated, 'required': required, 'fits': free is None or free >= required}
    return {'available': True, 'archives': inv.get('archives', []), 'retention': inv.get('retention', {}), 'disk': merged,
            'next_backup': next_backup, 'schedule': inv.get('schedule'), 'actions': inv.get('actions', {}),
            'remote': inv.get('remote')}


def ok(inv):
    return runner(200, json.dumps(inv, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Identity and permission (IsInstanceOwner on the authenticated actor, before
# method dispatch). The runner is never called.
# ---------------------------------------------------------------------------
add('anonymous', 'anonymous', OVERVIEW, 401, ANON)
add('anonymous-token', 'anonymous', TOKEN, 401, ANON, method='POST', body={'name': ARCHIVE, 'password': PASSWORD})
add('athlete-denied', 'athlete', OVERVIEW, 403, DENIED)
add('collaborator-denied', 'full_collaborator', OVERVIEW, 403, DENIED)
add('restricted-collaborator-denied', 'restricted_collaborator', TOKEN, 403, DENIED, method='POST', body={})
add('foreign-owner-denied', 'foreign_owner', OVERVIEW, 403, DENIED)
add('owner-without-association-denied', 'owner_without_association', TOKEN, 403, DENIED, method='POST', body={})
add('athlete-wrong-method-denied-first', 'athlete', OVERVIEW, 403, DENIED, method='PUT', body={})
add('owner-impersonation-header', 'owner', OVERVIEW, 403, LEAVE, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('owner-overview-put', 'owner', OVERVIEW, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('owner-overview-delete', 'owner', OVERVIEW, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('anonymous-overview-post', 'anonymous', OVERVIEW, 401, ANON, method='POST', body={})
add('athlete-overview-post', 'athlete', OVERVIEW, 403, DENIED, method='POST', body={})
add('restricted-collaborator-overview-post', 'restricted_collaborator', OVERVIEW, 403, DENIED, method='POST', body={})
add('owner-impersonation-header-post', 'owner', OVERVIEW, 403, LEAVE, method='POST', body={},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('owner-token-get', 'owner', TOKEN, 405, {'detail': 'Method "GET" not allowed.'})
add('owner-token-delete', 'owner', TOKEN, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('options-overview', 'owner', OVERVIEW, 200, options('Backup Overview'), method='OPTIONS')
add('options-token', 'owner', TOKEN, 200, options('Backup Download Token'), method='OPTIONS')

# ---------------------------------------------------------------------------
# GET admin/backups: the runner first, then the data sizes (cached ten
# minutes), then the merged disk and the next backup estimate.
# ---------------------------------------------------------------------------
INV = inventory()
add('overview-cached-sizes', 'owner', OVERVIEW, 200, overview(INV, SIZES), run=ok(INV), cache=CACHED)
add('overview-head', 'owner', OVERVIEW, 200, '<head>', method='HEAD', run=ok(INV), cache=CACHED)
add('overview-admin', 'admin', OVERVIEW, 200, overview(INV, SIZES), run=ok(INV), cache=CACHED)
add('overview-admin-session-ignored', 'admin', OVERVIEW, 200, overview(INV, SIZES), run=ok(INV), cache=CACHED,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
OBJECTS = {'documents/a.pdf': 'a' * 10, 'restores/x/recovery/b.zip': 'b' * 200, 'logo/c.png': 'c' * 3000}
FRESH = {'database': '$database', 'objects': 3210}
add('overview-computes-sizes', 'owner', OVERVIEW, 200, overview(INV, {'database': '$database', 'objects': 3210}),
    run=ok(INV), objects=OBJECTS, cache_after={'sizes': FRESH}, database=True)
add('overview-refresh', 'owner', OVERVIEW + '?refresh=1', 200, overview(INV, {'database': '$database', 'objects': 3210}),
    run=ok(INV), objects=OBJECTS, cache=CACHED, cache_after={'sizes': FRESH}, database=True)
add('overview-refresh-not-one', 'owner', OVERVIEW + '?refresh=true', 200, overview(INV, SIZES), run=ok(INV), cache=CACHED,
    objects=OBJECTS)
add('overview-refresh-last-value', 'owner', OVERVIEW + '?refresh=1&refresh=0', 200, overview(INV, SIZES), run=ok(INV),
    cache=CACHED, objects=OBJECTS)
add('overview-refresh-last-one', 'owner', OVERVIEW + '?refresh=0&refresh=1', 200,
    overview(INV, {'database': '$database', 'objects': 0}), run=ok(INV), cache=CACHED,
    cache_after={'sizes': {'database': '$database', 'objects': 0}}, database=True)
add('overview-bucket-absent', 'owner', OVERVIEW, 200, overview(INV, {'database': '$database', 'objects': None}), run=ok(INV),
    bucket_absent=True, cache_after={'sizes': {'database': '$database', 'objects': None}}, database=True)
add('overview-cached-none', 'owner', OVERVIEW, 200, overview(INV, {'database': None, 'objects': 7}), run=ok(INV),
    cache={'sizes': {'payload': {'database': None, 'objects': 7}}})
add('overview-cached-extra-key', 'owner', OVERVIEW, 200, overview(INV, {**SIZES, 'measured': 'cache'}), run=ok(INV),
    cache={'sizes': {'payload': {**SIZES, 'measured': 'cache'}}})
add('overview-cached-missing-key', 'owner', OVERVIEW, 500, SERVER, run=ok(INV),
    cache={'sizes': {'payload': {'database': 1}}})
add('overview-cached-integer', 'owner', OVERVIEW, 500, SERVER, run=ok(INV), cache={'sizes': {'raw': '7'}})

for name, inv, margin in (
        ('free-equal', inventory(disk={'free': 2 * (8 * MIB) + 2 * MIB, 'total': 1}), 2),
        ('free-short', inventory(disk={'free': 2 * (8 * MIB) + 2 * MIB - 1}), 2),
        ('free-float', inventory(disk={'free': 1.5e20}), 2),
        ('free-missing', inventory(disk={'total': 10, 'used': 4}), 2),
        ('disk-missing', inventory(disk=OMIT), 2),
        ('disk-overrides', inventory(disk={'database': 'disk', 'objects': 'disk', 'free': 0}), 2),
        ('margin-missing', inventory(space_margin_mb=OMIT), 1024),
        ('margin-zero', inventory(space_margin_mb=0), 1024),
        ('margin-boolean', inventory(space_margin_mb=True), 1024),
        ('margin-float', inventory(space_margin_mb=3.0), 1024),
        ('margin-text', inventory(space_margin_mb='3'), 1024),
        ('margin-large', inventory(space_margin_mb=10 ** 13, disk={'free': 10 ** 30}), 10 ** 13),
        ('archives-missing', inventory(archives=OMIT, retention=OMIT), 2),
        ('numbers', inventory(archives=[{'size': 10 ** 30, 'ratio': 0.1, 'n': -0, 'flags': [True, None, 'x']}],
                              retention=[], extra={'ignored': True}), 2)):
    add('overview-' + name, 'owner', OVERVIEW, 200, overview(inv, SIZES, margin=margin), run=ok(inv), cache=CACHED)

SCHEDULE = {'enabled': True, 'frequency': 'weekly', 'time': '02:30', 'weekday': 6, 'keep': 7,
            'next_run': '2026-11-08T02:30:00Z', 'last_run': {'at': '2026-11-01T02:30:00Z', 'status': 'ok', 'operation': 'op-7'}}
ACTIONS = {'create': True, 'delete': True, 'keep': False, 'restore': True, 'upload': True, 'schedule': True}
REMOTE = {'configured': True, 'provider': 's3', 'bucket': 'remote-placeholder', 'last_upload': {'at': '2026-11-01T03:00:00Z'},
          'last_failure': None, 'note': 'copia àè €'}
for name, inv in (
        ('schedule-actions-remote', inventory(schedule=SCHEDULE, actions=ACTIONS, remote=REMOTE)),
        ('schedule-null', inventory(schedule=None, actions={}, remote=None)),
        ('schedule-only', inventory(schedule={'enabled': False})),
        ('actions-only', inventory(actions={'create': False})),
        ('remote-only', inventory(remote={'configured': False})),
        ('pass-through-shapes', inventory(schedule=[1, 'x'], actions=None, remote='off')),
        ('pass-through-numbers', inventory(schedule=0, actions=[True, 1.5, 10 ** 25], remote=False))):
    add('overview-' + name, 'owner', OVERVIEW, 200, overview(inv, SIZES), run=ok(inv), cache=CACHED)
add('overview-schedule-nan', 'owner', OVERVIEW, 500, SERVER,
    run=runner(200, '{"archives": [], "disk": {}, "schedule": {"keep": NaN}}'), cache=CACHED)
add('overview-remote-infinite', 'owner', OVERVIEW, 500, SERVER,
    run=runner(200, '{"archives": [], "disk": {}, "remote": {"size": -1e400}}'), cache=CACHED)
add('overview-number-forms', 'owner', OVERVIEW, 200,
    overview({'archives': [1.5, 100.0, 1e-07, 12345678901234567890], 'disk': {'free': 1}}, SIZES, margin=1024),
    run=runner(200, '{"archives": [1.50, 1E2, 1e-7, 12345678901234567890], "disk": {"free": 1}, "space_margin_mb": 1.0}'),
    cache=CACHED)
add('overview-duplicate-keys', 'owner', OVERVIEW, 200,
    overview({'archives': [], 'retention': {'daily': 2}}, SIZES, margin=1024),
    run=runner(200, '{"archives": [1], "retention": {"daily": 1, "daily": 2}, "archives": []}'), cache=CACHED)
add('overview-free-text', 'owner', OVERVIEW, 500, SERVER, run=ok(inventory(disk={'free': 'many'})), cache=CACHED)
add('overview-disk-list', 'owner', OVERVIEW, 500, SERVER, run=ok(inventory(disk=[1])), cache=CACHED)
# The sizes are measured and cached before the response fails.
add('overview-disk-null', 'owner', OVERVIEW, 500, SERVER, run=ok(inventory(disk=None)), objects={'k': 'v' * 5},
    cache_after={'sizes': {'database': '$database', 'objects': 5}})
add('overview-archives-nan', 'owner', OVERVIEW, 500, SERVER,
    run=runner(200, '{"archives": [NaN], "disk": {}}'), cache=CACHED)
add('overview-archives-infinite', 'owner', OVERVIEW, 500, SERVER,
    run=runner(200, '{"archives": [1e400], "disk": {}}'), cache=CACHED)
add('overview-inventory-list', 'owner', OVERVIEW, 500, SERVER, run=runner(200, '[]'), cache=CACHED)

# Runner failures: nothing is measured or cached.
add('overview-no-socket', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE},
    run=runner(state='no-socket'), cache=CACHED)
add('overview-no-token', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE}, run=runner(state='no-token'))
add('overview-empty-token', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE}, run=runner(state='empty-token'))
add('overview-invalid-json', 'owner', OVERVIEW + '?refresh=1', 200, {'available': False, 'reason': UNREACHABLE},
    run=runner(200, '{"archives": '), cache=CACHED)
add('overview-empty-body', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE}, run=runner(204, ''))
# json.loads(bytes) decodes a UTF-8 BOM away (utf-8-sig); invalid UTF-8 is a ValueError.
add('overview-bom', 'owner', OVERVIEW, 200, overview({}, SIZES, margin=1024), run=runner(200, '\ufeff{}'), cache=CACHED)
add('overview-invalid-utf8', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE},
    run=runner(200, body_b64=base64.b64encode(b'{"archives": ["\xff"]}').decode()))
add('overview-runner-404', 'owner', OVERVIEW, 200, {'available': False, 'reason': OUTDATED},
    run=runner(404, '{"error": "Not found"}'))
add('overview-runner-404-text', 'owner', OVERVIEW, 200, {'available': False, 'reason': UNREACHABLE},
    run=runner(404, 'Not Found'))
add('overview-runner-409', 'owner', OVERVIEW, 503, error('Backup in corso.'), run=runner(409, '{"error": "Backup in corso."}'))
add('overview-runner-400-default', 'owner', OVERVIEW, 503, error('Aggiornamento non disponibile.'),
    run=runner(400, '{"detail": "x"}'))
add('overview-runner-502', 'owner', OVERVIEW, 502, error('Runner non pronto.'), run=runner(502, '{"error": "Runner non pronto."}'))
add('overview-runner-500-number', 'owner', OVERVIEW, 500, error('42'), run=runner(500, '{"error": 42}'))
add('overview-runner-503-object', 'owner', OVERVIEW, 503, error("{'a': [1, None, True, 'b']}"),
    run=runner(503, '{"error": {"a": [1, null, true, "b"]}}'))
add('overview-runner-error-list', 'owner', OVERVIEW, 500, SERVER, run=runner(400, '[1]'))

# ---------------------------------------------------------------------------
# POST admin/backups: relay('POST', '/backups', {request_id, actor_id},
# success=202) and, on 202, audit(actor, 'backup_create', operation id). The
# request body is never parsed.
# ---------------------------------------------------------------------------
OWNER = IDS['owner']
ADMIN = IDS['admin']


def create_runner(status=202, body='', actor='owner', **options):
    return runner(status, body, method='POST', payload={'request_id': '$uuid4', 'actor_id': IDS[actor]}, **options)


def created(value, actor='owner', association='own_association'):
    return {'action': 1, 'object_pk': str(INSTANCE['id']), 'object_repr': REPR, 'changes': {'backup_create': ['', value]},
            'additional_data': {'actor_id': IDS[actor]}, 'actor': actor, 'association': association,
            'resolution_path': 'actor.sport_association'}


OPERATION = {'operation': {'id': 'op-20261109-1', 'kind': 'backup', 'status': 'queued', 'note': 'àè €'}}
add('create-started', 'owner', OVERVIEW, 202, OPERATION, method='POST', body={},
    run=create_runner(202, json.dumps(OPERATION, ensure_ascii=False)), audit=created('op-20261109-1'))
add('create-started-admin', 'admin', OVERVIEW, 202, OPERATION, method='POST', body={},
    run=create_runner(202, json.dumps(OPERATION), actor='admin'), audit=created('op-20261109-1', 'admin', 'admin_association'))
add('create-runner-200-is-202', 'owner', OVERVIEW, 202, {'operation': {'id': 'a'}, 'extra': [1, None]}, method='POST',
    run=create_runner(200, '{"operation": {"id": "a"}, "extra": [1, null]}'), audit=created('a'))
add('create-body-ignored', 'owner', OVERVIEW, 202, {'operation': {'id': 'b'}}, method='POST',
    body={'request_id': 'mine', 'actor_id': 'other'}, run=create_runner(202, '{"operation": {"id": "b"}}'), audit=created('b'))
add('create-malformed-json-ignored', 'owner', OVERVIEW, 202, {'operation': {'id': 'c'}}, method='POST',
    raw_body='{"request_id": ', content_type='application/json', run=create_runner(202, '{"operation": {"id": "c"}}'),
    audit=created('c'))
add('create-form-ignored', 'owner', OVERVIEW, 202, {'operation': {'id': 'd'}}, method='POST', raw_body='keep=1',
    content_type='application/x-www-form-urlencoded', run=create_runner(202, '{"operation": {"id": "d"}}'), audit=created('d'))
add('create-operation-id-missing', 'owner', OVERVIEW, 202, {'operation': {}}, method='POST',
    run=create_runner(202, '{"operation": {}}'), audit=created(''))
add('create-operation-id-number', 'owner', OVERVIEW, 202, {'operation': {'id': 42}}, method='POST',
    run=create_runner(202, '{"operation": {"id": 42}}'), audit=created('42'))
add('create-operation-id-object', 'owner', OVERVIEW, 202, {'operation': {'id': {'n': [True, None]}}}, method='POST',
    run=create_runner(202, '{"operation": {"id": {"n": [true, null]}}}'), audit=created("{'n': [True, None]}"))
add('create-operation-missing', 'owner', OVERVIEW, 500, SERVER, method='POST', run=create_runner(202, '{"queued": true}'))
add('create-operation-list', 'owner', OVERVIEW, 500, SERVER, method='POST', run=create_runner(202, '{"operation": ["x"]}'))
add('create-operation-null', 'owner', OVERVIEW, 500, SERVER, method='POST', run=create_runner(202, '{"operation": null}'))
add('create-reply-list', 'owner', OVERVIEW, 500, SERVER, method='POST', run=create_runner(202, '[1]'))
add('create-invalid-json', 'owner', OVERVIEW, 503, error(UNREACHABLE), method='POST', run=create_runner(202, '{"operation": '))
add('create-no-socket', 'owner', OVERVIEW, 503, error(UNREACHABLE), method='POST', run=create_runner(state='no-socket'))
add('create-no-token', 'owner', OVERVIEW, 503, error(UNREACHABLE), method='POST', run=create_runner(state='no-token'))
add('create-runner-404', 'owner', OVERVIEW, 409, error(OUTDATED_ACTION), method='POST',
    run=create_runner(404, '{"error": "Not found"}'))
add('create-runner-404-text', 'owner', OVERVIEW, 503, error(UNREACHABLE), method='POST', run=create_runner(404, 'Not Found'))
add('create-runner-409', 'owner', OVERVIEW, 409, error('Un backup è già in corso.'), method='POST',
    run=create_runner(409, '{"error": "Un backup è già in corso."}'))
add('create-runner-400-default', 'owner', OVERVIEW, 400, error('Aggiornamento non disponibile.'), method='POST',
    run=create_runner(400, '{"detail": "x"}'))
add('create-runner-422-number', 'owner', OVERVIEW, 422, error('7'), method='POST', run=create_runner(422, '{"error": 7}'))
add('create-runner-500', 'owner', OVERVIEW, 503, error('Runner non pronto.'), method='POST',
    run=create_runner(500, '{"error": "Runner non pronto."}'))
add('create-runner-507', 'owner', OVERVIEW, 503, error('Spazio insufficiente.'), method='POST',
    run=create_runner(507, '{"error": "Spazio insufficiente."}'))
add('create-runner-error-list', 'owner', OVERVIEW, 500, SERVER, method='POST', run=create_runner(409, '["x"]'))

# ---------------------------------------------------------------------------
# POST admin/backups/download-token
# ---------------------------------------------------------------------------


def attempts(actor, value):
    return {'attempts': {actor: value}}


def fresh_attempts(actor, value):
    return {'attempts': {actor: {'value': value, 'ttl': 'fresh'}}}


def kept_attempts(actor, value):
    return {'attempts': {actor: {'value': value, 'ttl': 'kept'}}}


def granted(name, actor='owner', association='own_association'):
    return {'object_pk': str(INSTANCE['id']), 'object_repr': REPR, 'changes': {'backup_download': ['', name]},
            'additional_data': {'backup': name, 'actor_id': IDS[actor]}, 'actor': actor, 'association': association,
            'resolution_path': 'actor.sport_association'}


def token_response(name, actor='owner'):
    return {'download_token': f'$backup-token:{name}:{actor}', 'expires_in': 300}


add('token-empty-body', 'owner', TOKEN, 400, {'name': [REQUIRED], 'password': [REQUIRED]}, method='POST', body={})
add('token-list-body', 'owner', TOKEN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='POST', body=[ARCHIVE])
add('token-name-pattern', 'owner', TOKEN, 400, {'name': [PATTERN]}, method='POST', body={'name': 'backup.tar.gz', 'password': PASSWORD})
add('token-name-path', 'owner', TOKEN, 400, {'name': [PATTERN]}, method='POST',
    body={'name': '../' + ARCHIVE, 'password': PASSWORD})
add('token-name-number', 'owner', TOKEN, 400, {'name': [PATTERN], 'password': [STRING]}, method='POST',
    body={'name': 20261101, 'password': [PASSWORD]})
# RegexField's 'invalid' message replaces CharField's (a bool is invalid).
add('token-name-boolean-password-blank', 'owner', TOKEN, 400, {'name': [PATTERN], 'password': [BLANK]}, method='POST',
    body={'name': True, 'password': ''})
add('token-null-values', 'owner', TOKEN, 400, {'name': [NULL], 'password': [NULL]}, method='POST',
    body={'name': None, 'password': None})
add('token-name-blank', 'owner', TOKEN, 400, {'name': [BLANK]}, method='POST', body={'name': '  ', 'password': PASSWORD})
add('token-password-long', 'owner', TOKEN, 400, {'password': ['Ensure this field has no more than 256 characters.']},
    method='POST', body={'name': ARCHIVE, 'password': 'p' * 257})
add('token-validation-before-attempts', 'owner', TOKEN, 400, {'name': [REQUIRED]}, method='POST', body={'password': 'x'},
    cache=attempts(OWNER, 5), cache_after=kept_attempts(OWNER, 5))
add('token-too-many', 'owner', TOKEN, 429, error(TOO_MANY), method='POST', body={'name': ARCHIVE, 'password': PASSWORD},
    cache=attempts(OWNER, 5), cache_after=kept_attempts(OWNER, 5))
add('token-too-many-more', 'owner', TOKEN, 429, error(TOO_MANY), method='POST', body={'name': ARCHIVE, 'password': 'x'},
    cache=attempts(OWNER, 9), cache_after=kept_attempts(OWNER, 9))
add('token-wrong-password', 'owner', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': 'wrong'},
    cache_after=fresh_attempts(OWNER, 1))
add('token-wrong-password-counts', 'owner', TOKEN, 400, error(WRONG), method='POST',
    body={'name': ARCHIVE, 'password': PASSWORD + ' '}, cache=attempts(OWNER, 4), cache_after=kept_attempts(OWNER, 5))
add('token-whitespace-password', 'owner', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': '   '},
    cache_after=fresh_attempts(OWNER, 1))
add('token-number-password', 'owner', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': 12345},
    cache_after=fresh_attempts(OWNER, 1))
add('token-unusable-password', 'admin', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': PASSWORD},
    password='!unusable-placeholder', cache_after=fresh_attempts(ADMIN, 1))
add('token-other-actor-attempts', 'admin', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': 'x'},
    cache={'attempts': {OWNER: 5}}, cache_after={'attempts': {OWNER: {'value': 5, 'ttl': 'kept'}, ADMIN: {'value': 1, 'ttl': 'fresh'}}})
# A form body reads like JSON strings: every field is a required CharField.
FORM = 'application/x-www-form-urlencoded'
add('token-form-empty', 'owner', TOKEN, 400, {'name': [REQUIRED], 'password': [REQUIRED]}, method='POST', raw_body='', content_type=FORM)
add('token-form-blank-values', 'owner', TOKEN, 400, {'name': [BLANK], 'password': [BLANK]}, method='POST',
    raw_body='name=&password=', content_type=FORM)
add('token-form-wrong-password', 'owner', TOKEN, 400, error(WRONG), method='POST',
    raw_body=f'name={ARCHIVE}&password=wrong&password=wrong2', content_type=FORM, cache_after=fresh_attempts(OWNER, 1))
add('token-form-granted', 'owner', TOKEN, 200, token_response(ARCHIVE), method='POST',
    raw_body=f'name={ARCHIVE}&password={PASSWORD}', content_type=FORM, audit=granted(ARCHIVE))
add('token-granted', 'owner', TOKEN, 200, token_response(ARCHIVE), method='POST', body={'name': ARCHIVE, 'password': PASSWORD},
    audit=granted(ARCHIVE))
add('token-granted-clears-attempts', 'owner', TOKEN, 200, token_response(SECOND), method='POST',
    body={'name': SECOND, 'password': PASSWORD}, cache=attempts(OWNER, 4), cache_after={}, audit=granted(SECOND))
add('token-granted-trimmed-name', 'owner', TOKEN, 200, token_response(ARCHIVE), method='POST',
    body={'name': f' {ARCHIVE}\n', 'password': PASSWORD}, audit=granted(ARCHIVE))
add('token-granted-admin', 'admin', TOKEN, 200, token_response(ARCHIVE, 'admin'), method='POST',
    body={'name': ARCHIVE, 'password': PASSWORD}, audit=granted(ARCHIVE, 'admin', 'admin_association'))
add('token-granted-upgrades-hash', 'owner', TOKEN, 200, token_response(ARCHIVE), method='POST',
    body={'name': ARCHIVE, 'password': PASSWORD}, password=LEGACY_HASH, rehash=True, audit=granted(ARCHIVE))
add('token-legacy-hash-wrong', 'owner', TOKEN, 400, error(WRONG), method='POST', body={'name': ARCHIVE, 'password': 'nope'},
    password=LEGACY_HASH, cache_after=fresh_attempts(OWNER, 1))

# ---------------------------------------------------------------------------
# GET admin/backups/<name>/download (no authentication; the signed token)
# ---------------------------------------------------------------------------
def download(name=ARCHIVE, token=True):
    path = f'/instance/admin/backups/{name}/download'
    return path + '?download_token=$token' if token else path


def claims(name=ARCHIVE, actor='$id:owner', identity='c0ffee00000000000000000000000001', **extra):
    value = {'name': name, 'actor': actor, 'id': identity}
    value.update(extra)
    return {key: item for key, item in value.items() if item is not OMIT}


def signed(value=None, **options):
    return {'claims': claims() if value is None else value, **options}


ARCHIVE_BYTES = base64.b64encode(b'\x1f\x8b\x08\x00 archive placeholder \x00\x01\xff' * 3).decode()


def streamed(name=ARCHIVE):
    return {'content_type': 'application/gzip', 'length': len(base64.b64decode(ARCHIVE_BYTES)),
            'disposition': f'attachment; filename="{name}"', 'body_b64': ARCHIVE_BYTES}


def archive_runner(status=200, body='', name=ARCHIVE, **options):
    return runner(status, body, path=f'/backups/{name}', json_type=False, **options)


def used(*ids):
    return {'used': list(ids)}


ONE = 'c0ffee00000000000000000000000001'
add('download-options', 'anonymous', download(token=False), 200,
    options('Backup Download', 'Browser navigation cannot send the JWT; a short signed token stands in.'), method='OPTIONS')
add('download-head', 'anonymous', download(token=False), 405, '<head>', method='HEAD')
add('download-post', 'anonymous', download(token=False), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('download-missing-token', 'anonymous', download(token=False), 403, error(INVALID_LINK))
add('download-tampered', 'anonymous', download(), 403, error(INVALID_LINK), token=signed(tamper=True))
add('download-expired', 'anonymous', download(), 403, error(INVALID_LINK), token=signed(age=301))
add('download-other-salt', 'anonymous', download(), 403, error(INVALID_LINK), token=signed(salt='instance.data-restore.recovery-download'))
add('download-untimed', 'anonymous', download(), 500, SERVER, token=signed(untimed=True))
add('download-other-name', 'anonymous', download(SECOND), 403, error(INVALID_LINK), token=signed())
add('download-claims-list', 'anonymous', download(), 403, error(INVALID_LINK), token=signed([ARCHIVE]))
add('download-not-an-archive', 'anonymous', download('backup.tar.gz'), 403, error(INVALID_LINK),
    token=signed(claims('backup.tar.gz')))
add('download-athlete', 'anonymous', download(), 403, error(UNAUTHORIZED), token=signed(claims(actor='$id:athlete')))
add('download-collaborator', 'anonymous', download(), 403, error(UNAUTHORIZED), token=signed(claims(actor='$id:full_collaborator')))
add('download-actor-missing', 'anonymous', download(), 403, error(UNAUTHORIZED), token=signed(claims(actor=OMIT)))
add('download-actor-unknown', 'anonymous', download(), 403, error(UNAUTHORIZED),
    token=signed(claims(actor='a1000000-0000-4000-8000-0000007fffff')))
add('download-actor-integer', 'anonymous', download(), 403, error(UNAUTHORIZED), token=signed(claims(actor=5)))
add('download-actor-malformed', 'anonymous', download(), 500, SERVER, token=signed(claims(actor='not-a-uuid')))
add('download-actor-float', 'anonymous', download(), 500, SERVER, token=signed(claims(actor=1.5)))
add('download-id-missing', 'anonymous', download(), 403, error(USED), token=signed(claims(identity=OMIT)))
add('download-id-number', 'anonymous', download(), 403, error(USED), token=signed(claims(identity=7)))
add('download-id-used', 'anonymous', download(), 403, error(USED), token=signed(), cache=used(ONE))
add('download-streams', 'anonymous', download(), 200, '<stream>', token=signed(),
    run=archive_runner(200, body_b64=ARCHIVE_BYTES), stream=streamed(), cache_after=used(ONE))
add('download-streams-admin-braced-actor', 'anonymous', download(SECOND), 200, '<stream>',
    token=signed(claims(SECOND, actor='{' + IDS['admin'].upper() + '}', identity='second-use')),
    run=archive_runner(200, body_b64=ARCHIVE_BYTES, name=SECOND), stream=streamed(SECOND), cache_after=used('second-use'))
add('download-with-bearer', 'owner', download(), 200, '<stream>', token=signed(),
    run=archive_runner(200, body_b64=ARCHIVE_BYTES), stream=streamed(), cache_after=used(ONE))
add('download-runner-404', 'anonymous', download(), 404, error('Backup non trovato.'), token=signed(),
    run=archive_runner(404, '{"error": "missing"}'), cache_after=used(ONE))
add('download-runner-404-text', 'anonymous', download(), 404, error('Backup non trovato.'), token=signed(),
    run=archive_runner(404, 'gone'), cache_after=used(ONE))
add('download-runner-409', 'anonymous', download(), 503, error('Backup in corso.'), token=signed(),
    run=archive_runner(409, '{"error": "Backup in corso."}'), cache_after=used(ONE))
add('download-runner-500-text', 'anonymous', download(), 503, error('Risorsa non disponibile.'), token=signed(),
    run=archive_runner(500, 'oops'), cache_after=used(ONE))
add('download-runner-400-default', 'anonymous', download(), 503, error('Risorsa non disponibile.'), token=signed(),
    run=archive_runner(400, '{}'), cache_after=used(ONE))
add('download-runner-400-number', 'anonymous', download(), 503, error('7'), token=signed(),
    run=archive_runner(400, '{"error": 7}'), cache_after=used(ONE))
add('download-runner-error-list', 'anonymous', download(), 500, SERVER, token=signed(),
    run=archive_runner(400, '["x"]'), cache_after=used(ONE))
add('download-runner-chunked', 'anonymous', download(), 503, error(UNREACHABLE), token=signed(),
    run=archive_runner(200, body_b64=ARCHIVE_BYTES, chunked=True), cache_after=used(ONE))
add('download-no-socket', 'anonymous', download(), 503, error(UNREACHABLE), token=signed(),
    run=archive_runner(state='no-socket'), cache_after=used(ONE))
add('download-no-token-file', 'anonymous', download(), 503, error(UNREACHABLE), token=signed(),
    run=archive_runner(state='no-token'), cache_after=used(ONE))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance/admin/backups*', 'ids': IDS, 'instance': INSTANCE,
                              'runner_token': RUNNER_TOKEN, 'hashes': HASHES, 'password': PASSWORD, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
