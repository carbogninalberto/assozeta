"""Author the Phase 1 instance runtime switch HTTP sequence (Django == Go equivalence).

Pins GET/POST /instance/admin/runtime on both APIs, which relay to the update
runner's GET /runtime and POST /runtime-switches, in this order: identity,
IsInstanceOwner on the authenticated actor, method dispatch, the exact body
{target, expected_generation, idempotency_key, password} (anything else is
{'error': 'Richiesta non valida.'} before the password), the shared owner
password confirmation (attempt limit included), then the runner. The runner's
status and JSON body come back verbatim; a transport failure is 503 with the
updater's unreachable text. Every response carries Cache-Control: no-store.
One UPDATE audit entry on the instance configuration when the runner accepted
a new operation (202 with replayed false), none otherwise.

Case shape follows instance_backups_actions_http.json, so the adapter reuses
its runner stub, password, cache and audit checks. Every value is a
placeholder. Run with ``python3 -I``.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_runtime_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
PATH = '/instance/admin/runtime'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
UNREACHABLE = 'Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.'
INVALID = {'error': 'Richiesta non valida.'}
WRONG = {'error': 'Password non corretta.'}
TOO_MANY = {'error': 'Troppi tentativi. Riprova tra qualche minuto.'}
BLANK = 'This field may not be blank.'
RUNNER_TOKEN = 'runner-token-placeholder'
OWNER, ADMIN = IDS['owner'], IDS['admin']
KEY = 'b1000000-0000-4000-8000-000000000001'
OPERATION = 'b1000000-0000-4000-8000-0000000000a1'
INSTANCE = {
    'id': 7520, 'domain': 'runtime.example.test', 'name': 'Istanza runtime', 'abbreviation': 'IR',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'support_multiple_associations': False,
    'primary_association': 'own_association', 'google_client_id': '', 'apple_client_id': '', 'email_settings': {},
    'email_password_encrypted': '', 'email_revision': 0, 'diagnostic_results': {}, 'integration_settings': {},
}
PASSWORD = 'Fixture-runtime-placeholder-1'


def pbkdf2(raw, salt, iterations):
    digest = hashlib.pbkdf2_hmac('sha256', raw.encode(), salt.encode(), iterations)
    return f'pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}'


HASHES = {'owner': pbkdf2(PASSWORD, 'runtimeswitchowner0000001', 1_000_000),
          'admin': pbkdf2(PASSWORD, 'runtimeswitchadmin0000001', 1_000_000)}
AUTHORITY = {'runtime': 'django', 'generation': 1, 'operation_id': None}
COUNTS = {'pending': 0, 'transferred': 0, 'blocked': 0}
cases = []


def view(**changes):
    return {'id': OPERATION, 'kind': 'runtime_switch', 'actor_id': OWNER, 'previous_runtime': 'django',
            'requested_runtime': 'go', 'expected_generation': 1, 'status': 'queued', 'stage': 'queued', 'outcome': None,
            'counts': COUNTS, 'committed_generation': None, 'error': None, 'error_code': None, 'recovery': None,
            'created_at': '2026-11-20T10:00:00+00:00', 'updated_at': '2026-11-20T10:00:00+00:00', **changes}


STATUS = {'protocol': 1, 'effective_runtime': 'django', 'generation': 1, 'last_operation_id': None,
          'requested_runtime': 'go', 'operation': view(), 'active': view(), 'authority_error': None,
          'recovery_command': './bin/assozeta runtime-recover', 'note': 'àè'}


def headers_for(*, body=True):
    value = {'host': 'localhost', 'accept-encoding': 'identity', 'authorization': f'Bearer {RUNNER_TOKEN}',
             'content-type': 'application/json'}
    if body:
        value['content-length'] = '$length'
    return value


def call(method, path, payload):
    if payload is None:
        return {'method': method, 'path': path, 'headers': headers_for(body=False), 'body': None}
    return {'method': method, 'path': path, 'headers': headers_for(), 'body': {'json': payload}}


def runner(status=200, body='', *, expect=(), state='up'):
    return {'state': state, 'status': status, 'body': body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
            'early': False, 'expect': list(expect) if state == 'up' else []}


def attempts(actor, value, ttl=None):
    if ttl is None:
        return {'attempts': {actor: value}}
    return {'attempts': {actor: {'value': value, 'ttl': ttl}}}


def add(name, actor, status, response, *, method='POST', body=None, raw_body=None, content_type=None, run=None,
        cache=None, cache_after=None, audit=None):
    case = {'name': f'instance-runtime-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': {},
            'status': status, 'response': response, 'runner': run or {'state': 'up', 'expect': []},
            'cache': cache or {}, 'cache_after': copy.deepcopy(cache_after if cache_after is not None else (cache or {})),
            'password': None, 'rehash': False, 'audit': audit, 'send': None, 'no_store': True}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def request(**changes):
    return {'target': 'go', 'expected_generation': 1, 'idempotency_key': KEY, 'password': PASSWORD, **changes}


def relayed(actor=OWNER, **changes):
    return {'request_id': KEY, 'actor_id': actor, 'target': 'go', 'expected_generation': 1, **changes}


SWITCH = ('POST', '/runtime-switches')

# --- identity and IsInstanceOwner, before method dispatch -------------------
add('anonymous-status', 'anonymous', 401, ANON, method='GET')
add('anonymous-switch', 'anonymous', 401, ANON, body=request())
add('athlete-status', 'athlete', 403, DENIED, method='GET')
add('collaborator-switch', 'full_collaborator', 403, DENIED, body=request())
add('put-not-allowed', 'owner', 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body=request())

# --- GET relays the runner status verbatim ---------------------------------
add('status-owner', 'owner', 200, STATUS, method='GET', run=runner(200, STATUS, expect=[call('GET', '/runtime', None)]))
add('status-admin', 'admin', 200, STATUS, method='GET', run=runner(200, STATUS, expect=[call('GET', '/runtime', None)]))
add('status-unreachable', 'owner', 503, {'error': UNREACHABLE}, method='GET', run=runner(state='no-socket'))

# --- the exact body, before the password -----------------------------------
add('extra-key', 'owner', 400, INVALID, body=request(tasks='x'))
add('missing-password', 'owner', 400, INVALID, body={k: v for k, v in request().items() if k != 'password'})
add('missing-key', 'owner', 400, INVALID, body={k: v for k, v in request().items() if k != 'idempotency_key'})
add('unknown-target', 'owner', 400, INVALID, body=request(target='node'))
add('capitalized-target', 'owner', 400, INVALID, body=request(target='Go'))
add('boolean-generation', 'owner', 400, INVALID, body=request(expected_generation=True))
add('string-generation', 'owner', 400, INVALID, body=request(expected_generation='1'))
add('zero-generation', 'owner', 400, INVALID, body=request(expected_generation=0))
add('invalid-key', 'owner', 400, INVALID, body=request(idempotency_key='retry-1'))
add('list-body', 'owner', 400, INVALID, raw_body=json.dumps([request()]), content_type='application/json')

# --- password confirmation (shared attempt limit) --------------------------
add('blank-password', 'owner', 400, {'password': [BLANK]}, body=request(password=''))
add('wrong-password', 'owner', 400, WRONG, body=request(password=PASSWORD + 'x'), cache_after=attempts(OWNER, 1, 'fresh'))
add('too-many-attempts', 'owner', 429, TOO_MANY, body=request(), cache=attempts(OWNER, 5),
    cache_after=attempts(OWNER, 5, 'kept'))

# --- relayed submissions ----------------------------------------------------
ACCEPTED = {'operation': view(), 'authority': AUTHORITY, 'replayed': False}
add('accepted', 'owner', 202, ACCEPTED, body=request(), cache=attempts(OWNER, 3), cache_after={},
    run=runner(202, ACCEPTED, expect=[call(*SWITCH, relayed())]),
    audit={'changes': {'runtime_switch': ['django', 'go']},
           'additional_data': {'actor_id': OWNER, 'operation': OPERATION, 'expected_generation': 1},
           'actor': 'owner', 'association': 'own_association'})
add('uppercase-key-is-canonical', 'owner', 202, {**ACCEPTED, 'replayed': True},
    body=request(idempotency_key=KEY.upper()),
    run=runner(202, {**ACCEPTED, 'replayed': True}, expect=[call(*SWITCH, relayed())]))
NOOP = {'operation': view(requested_runtime='django', status='succeeded', stage='completed', outcome='noop'),
        'authority': AUTHORITY, 'replayed': False}
add('noop', 'owner', 200, NOOP, body=request(target='django'),
    run=runner(200, NOOP, expect=[call(*SWITCH, relayed(target='django'))]))
STALE = {'error': 'Lo stato è cambiato.', 'code': 'stale_generation',
         'authority': {'runtime': 'go', 'generation': 2, 'operation_id': OPERATION}, 'active': None}
add('stale-generation', 'admin', 409, STALE, body=request(target='django'),
    run=runner(409, STALE, expect=[call(*SWITCH, relayed(ADMIN, target='django'))]))
BUSY = {'error': 'Operazione in corso.', 'code': 'operation_in_progress', 'authority': AUTHORITY,
        'active': {'id': 'b1000000-0000-4000-8000-0000000000b2', 'kind': 'restore', 'status': 'running', 'stage': 'checking'}}
add('operation-in-progress', 'owner', 409, BUSY, body=request(), run=runner(409, BUSY, expect=[call(*SWITCH, relayed())]))
REUSED = {'error': 'Identificativo già usato.', 'code': 'idempotency_conflict', 'authority': AUTHORITY, 'active': None,
          'operation': view()}
add('idempotency-conflict', 'owner', 409, REUSED, body=request(expected_generation=2),
    run=runner(409, REUSED, expect=[call(*SWITCH, relayed(expected_generation=2))]))
UNAVAILABLE = {'error': 'Lo stato del runtime non è leggibile.', 'code': 'authority_unavailable'}
add('authority-unavailable', 'owner', 503, UNAVAILABLE, body=request(),
    run=runner(503, UNAVAILABLE, expect=[call(*SWITCH, relayed())]))
add('runner-unreachable', 'owner', 503, {'error': UNREACHABLE}, body=request(), run=runner(state='no-socket'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance/admin/runtime (phase 1 handoff)', 'ids': IDS,
                              'instance': INSTANCE, 'runner_token': RUNNER_TOKEN, 'hashes': HASHES, 'password': PASSWORD,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
