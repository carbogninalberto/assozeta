"""Author the production-mode instance updates and restarts HTTP sequence (instance-updates.md).

Pins BE/instance/administration.py InstanceUpdatesView (GET, POST) and
InstanceRestartsView (POST) in production mode, with updater_client.call_runner
(the update runner's Unix socket and bearer token) and release_catalog.fetch_release
(GitHub's release API through the run's HTTPS proxy), in Django's order:
identity, IsInstanceOwner on the authenticated actor, method dispatch, the
serializer, then the runner and release calls.

Per case:
- 'runner': the runner state ('ok', 'no-socket', 'no-token', 'empty-token'),
  the scripted answers in request order and the exact requests expected
  (method, path, and for a body exactly json.dumps of 'json');
- 'github': the scripted release API answers and the requests expected.
The development-mode answers of the same views are in instance-admin. Every
value is a placeholder. Run with ``python3 -I``.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instance_updates_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
UPDATES, RESTARTS = '/instance/admin/updates', '/instance/admin/restarts'
REPOSITORY = 'carbogninalberto/assozeta'
SERVER = '<stable-server-error-page>'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
UNREACHABLE = 'Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.'
COMPATIBLE = 'Il servizio di aggiornamento richiede una versione compatibile.'
NOT_READY = 'La release selezionata non è pronta per l’aggiornamento.'
NOT_NEWER = 'La versione installata non consente questo aggiornamento.'
USED = 'Questo identificativo è già stato usato per un altro aggiornamento.'
RUNNER_TOKEN = 'runner-token-placeholder'
GITHUB_HEADERS = {'host': 'api.github.com', 'accept-encoding': 'identity', 'user-agent': 'Assozeta-Self-Instance',
                  'connection': 'close', 'accept': 'application/vnd.github+json', 'x-github-api-version': '2022-11-28'}
REQUEST_ID = 'f1000000-0000-4000-8000-000000000001'
OTHER_REQUEST = 'f1000000-0000-4000-8000-000000000002'
INSTANCE = {'id': 7410, 'domain': 'updates.example.test', 'name': 'Istanza aggiornamenti', 'self_hosted': True,
            'setup_provenance': 'fresh', 'primary_association': 'own_association'}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, headers=None, runner=None, github=()):
    case = {'name': f'instance-updates-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': None,
            'runner': runner or {'state': 'ok', 'replies': [], 'expect': []}, 'github': list(github)}
    if body is not None:
        case['body'] = body
    cases.append(case)


def answer(status, body=None, *, raw=None):
    data = raw if raw is not None else json.dumps(body)
    return {'status': status, 'body_b64': base64.b64encode(data.encode()).decode()}


def call(method, path, payload=None):
    """call_runner's request: http.client headers, a JSON body only with a payload."""
    headers = {'host': 'localhost', 'accept-encoding': 'identity', 'authorization': f'Bearer {RUNNER_TOKEN}',
               'content-type': 'application/json'}
    request = {'method': method, 'path': path, 'headers': headers}
    if payload is not None:
        headers['content-length'] = str(len(json.dumps(payload).encode()))
        request['json'] = payload
    return request


def runner(*exchanges, state='ok'):
    """exchanges: (request, answer) pairs in order."""
    return {'state': state, 'replies': [reply for _, reply in exchanges], 'expect': [request for request, _ in exchanges]}


def raw_release(rid, tag, *, draft=False, ready=True, published='2026-05-01T10:00:00Z', name=None):
    assets = []
    if ready:
        assets = [{'name': f'assozeta-selfhost-{tag}.tar.gz', 'state': 'uploaded', 'size': 1024},
                  {'name': 'assozeta-update.json', 'state': 'uploaded', 'size': 12}]
    return {'id': rid, 'tag_name': tag, 'name': name, 'body': 'Note', 'draft': draft, 'prerelease': False,
            'published_at': published, 'assets': assets}


def release_call(rid, status, body):
    reply = {'status': status, 'headers': {'Content-Type': 'application/json'},
             'raw_b64': base64.b64encode(json.dumps(body).encode()).decode()}
    return {'connect': 'api.github.com:443', 'method': 'GET', 'path': f'/repos/{REPOSITORY}/releases/{rid}',
            'headers': GITHUB_HEADERS, 'reply': reply}


STATUS = {'protocol': 1, 'available': True, 'can_update': True, 'active': None, 'reason': '', 'history': [
    {'request_id': OTHER_REQUEST, 'actor_id': IDS['owner'], 'release_id': 7, 'tag': 'v1.0.0', 'state': 'completed'}],
    'installed_version': 'v0.0.0', 'note': 'àè'}
GET_STATUS = call('GET', '/status')

# --- permissions (production mode changes nothing before the views) ----------
add('anonymous', 'anonymous', UPDATES, 401, ANON)
add('athlete', 'athlete', UPDATES, 403, DENIED)
add('collaborator-restarts', 'full_collaborator', RESTARTS, 403, DENIED, method='POST', body={'request_id': REQUEST_ID})

# --- GET admin/updates ----------------------------------------------------------
add('status', 'owner', UPDATES, 200, STATUS, runner=runner((GET_STATUS, answer(200, STATUS))))
add('status-admin', 'admin', UPDATES, 200, STATUS, runner=runner((GET_STATUS, answer(200, STATUS))))
add('status-protocol-float', 'owner', UPDATES, 200, {'protocol': 1.0, 'available': False},
    runner=runner((GET_STATUS, answer(200, {'protocol': 1.0, 'available': False}))))
add('status-other-protocol', 'owner', UPDATES, 200, {'available': False, 'reason': COMPATIBLE, 'active': None, 'history': []},
    runner=runner((GET_STATUS, answer(200, {'protocol': 2, 'available': True}))))
add('status-without-protocol', 'owner', UPDATES, 200, {'available': False, 'reason': COMPATIBLE, 'active': None, 'history': []},
    runner=runner((GET_STATUS, answer(200, {}))))
add('status-rejected', 'owner', UPDATES, 409, {'error': 'Operazione in corso.'},
    runner=runner((GET_STATUS, answer(409, {'error': 'Operazione in corso.'}))))
add('status-rejected-default', 'owner', UPDATES, 500, {'error': 'Aggiornamento non disponibile.'},
    runner=runner((GET_STATUS, answer(500, {'detail': 'x'}))))
add('status-not-json', 'owner', UPDATES, 200, {'available': False, 'reason': UNREACHABLE, 'active': None, 'history': []},
    runner=runner((GET_STATUS, answer(200, raw='<html>'))))
add('status-list', 'owner', UPDATES, 500, SERVER, runner=runner((GET_STATUS, answer(200, [1]))))
for state in ('no-socket', 'no-token', 'empty-token'):
    add(f'status-{state}', 'owner', UPDATES, 200, {'available': False, 'reason': UNREACHABLE, 'active': None, 'history': []},
        runner={'state': state, 'replies': [], 'expect': []})

# --- POST admin/updates -----------------------------------------------------------
REQUEST = {'release_id': 12, 'tag': 'v1.2.0', 'request_id': REQUEST_ID}
add('request-invalid', 'owner', UPDATES, 400, {'release_id': ['A valid integer is required.'], 'request_id': ['Must be a valid UUID.']},
    method='POST', body={'release_id': 'x', 'tag': 'v1', 'request_id': 'nope'})
add('request-other-protocol', 'owner', UPDATES, 503, {'error': COMPATIBLE}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {'protocol': 0}))))
add('request-runner-down', 'owner', UPDATES, 503, {'error': UNREACHABLE}, method='POST', body=REQUEST,
    runner={'state': 'no-socket', 'replies': [], 'expect': []})
REPLAYED = {'request_id': REQUEST_ID, 'actor_id': IDS['owner'], 'release_id': 12, 'tag': 'v1.2.0', 'state': 'running'}
add('request-replayed-active', 'owner', UPDATES, 202, {'operation': REPLAYED}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'active': REPLAYED}))))
add('request-replayed-history', 'owner', UPDATES, 202, {'operation': {**REPLAYED, 'release_id': 12.0}}, method='POST',
    body={**REQUEST, 'request_id': REQUEST_ID.upper(), 'tag': ' v1.2.0 '},
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'available': False, 'history': [{**REPLAYED, 'release_id': 12.0}]}))))
add('request-replayed-other-tag', 'owner', UPDATES, 409, {'error': USED}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'history': [{**REPLAYED, 'tag': 'v1.1.0'}]}))))
add('request-replayed-other-actor', 'admin', UPDATES, 409, {'error': USED}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'active': REPLAYED}))))
add('request-unavailable', 'owner', UPDATES, 503, {'error': 'Manutenzione in corso.'}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'available': False, 'reason': 'Manutenzione in corso.'}))))
add('request-unavailable-default', 'owner', UPDATES, 503, {'error': 'Il servizio di aggiornamento non è disponibile.'}, method='POST',
    body=REQUEST, runner=runner((GET_STATUS, answer(200, {'protocol': 1, 'history': []}))))
add('request-needs-recovery', 'owner', UPDATES, 409, {'error': 'La transazione precedente richiede un ripristino.'}, method='POST',
    body=REQUEST, runner=runner((GET_STATUS, answer(200, {**STATUS, 'can_update': False}))))
add('request-history-not-list', 'owner', UPDATES, 500, SERVER, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, {**STATUS, 'history': None}))))
add('request-release-missing', 'owner', UPDATES, 503, {'error': 'Release service returned HTTP 404.'}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, STATUS))), github=[release_call(12, 404, {'message': 'Not Found'})])
add('request-release-rate-limit', 'owner', UPDATES, 503, {'error': 'Release service rate limit reached. Please try again later.'},
    method='POST', body=REQUEST, runner=runner((GET_STATUS, answer(200, STATUS))), github=[release_call(12, 403, {})])
add('request-release-draft', 'owner', UPDATES, 503, {'error': 'The selected release is not a published stable release.'},
    method='POST', body=REQUEST, runner=runner((GET_STATUS, answer(200, STATUS))),
    github=[release_call(12, 200, raw_release(12, 'v1.2.0', draft=True))])
add('request-release-other-tag', 'owner', UPDATES, 409, {'error': NOT_READY}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, STATUS))), github=[release_call(12, 200, raw_release(12, 'v1.2.1'))])
add('request-release-not-ready', 'owner', UPDATES, 409, {'error': NOT_READY}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, STATUS))), github=[release_call(12, 200, raw_release(12, 'v1.2.0', ready=False))])
add('request-release-not-newer', 'owner', UPDATES, 409, {'error': NOT_NEWER}, method='POST',
    body={**REQUEST, 'tag': 'v0.0.0'}, runner=runner((GET_STATUS, answer(200, STATUS))),
    github=[release_call(12, 200, raw_release(12, 'v0.0.0'))])
STARTED = {'operation': {'request_id': REQUEST_ID, 'state': 'queued'}}
UPDATE_CALL = call('POST', '/updates', {'release_id': 12, 'tag': 'v1.2.0', 'request_id': REQUEST_ID, 'actor_id': IDS['owner'],
                                        'source_version': 'v0.0.0'})
add('request', 'owner', UPDATES, 202, STARTED, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, STATUS)), (UPDATE_CALL, answer(200, STARTED))),
    github=[release_call(12, 200, raw_release(12, 'v1.2.0'))])
add('request-admin', 'admin', UPDATES, 202, STARTED, method='POST', body={**REQUEST, 'release_id': '12'},
    runner=runner((GET_STATUS, answer(200, STATUS)),
                  (call('POST', '/updates', {**UPDATE_CALL['json'], 'actor_id': IDS['admin']}), answer(200, STARTED))),
    github=[release_call(12, 200, raw_release(12, 'v1.2.0'))])
add('request-runner-refuses', 'owner', UPDATES, 507, {'error': 'Spazio insufficiente.'}, method='POST', body=REQUEST,
    runner=runner((GET_STATUS, answer(200, STATUS)), (UPDATE_CALL, answer(507, {'error': 'Spazio insufficiente.'}))),
    github=[release_call(12, 200, raw_release(12, 'v1.2.0'))])

# --- POST admin/restarts -----------------------------------------------------------
RESTART = {'request_id': REQUEST_ID, 'state': 'queued', 'services': ['backend', 'web']}
add('restart', 'owner', RESTARTS, 202, RESTART, method='POST', body={'request_id': REQUEST_ID},
    runner=runner((call('POST', '/restarts', {'request_id': REQUEST_ID, 'actor_id': IDS['owner']}), answer(200, RESTART))))
add('restart-admin-hex', 'admin', RESTARTS, 202, ['ok'], method='POST', body={'request_id': REQUEST_ID.replace('-', '').upper()},
    runner=runner((call('POST', '/restarts', {'request_id': REQUEST_ID, 'actor_id': IDS['admin']}), answer(200, ['ok']))))
add('restart-extra-key', 'owner', RESTARTS, 400, ['È richiesto soltanto l’identificativo della richiesta.'], method='POST',
    body={'request_id': REQUEST_ID, 'force': True})
add('restart-rejected', 'owner', RESTARTS, 409, {'error': 'Riavvio già in corso.'}, method='POST', body={'request_id': REQUEST_ID},
    runner=runner((call('POST', '/restarts', {'request_id': REQUEST_ID, 'actor_id': IDS['owner']}),
                   answer(409, {'error': 'Riavvio già in corso.'}))))
add('restart-runner-down', 'owner', RESTARTS, 503, {'error': UNREACHABLE}, method='POST', body={'request_id': REQUEST_ID},
    runner={'state': 'empty-token', 'replies': [], 'expect': []})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'instance updates and restarts in production mode', 'ids': IDS,
                              'instance': INSTANCE, 'runner_token': RUNNER_TOKEN, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
