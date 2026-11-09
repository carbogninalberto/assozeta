"""Author the Bakney pairing and SSO HTTP sequence (sso-pairing.md).

Views (BE/instance/sso/views.py), all SecureView: transaction.atomic dispatch,
HandoffThrottle (30/min per view class and client), SSOError -> {'error': code}
and Cache-Control/Pragma/Referrer-Policy/X-Frame-Options on every DRF response.

- instance/admin/bakney-pairing (GET, POST): PairingAdministrator on the
  authenticated actor; GET synchronizes a pending/paired pairing with the
  authority and reports sync_error; POST {action, pairing_id} generates
  (rotate: queue revocation, new local pairing id and secret), disconnects or
  synchronizes.
- bakney/v1/pairing/challenge, pairing/commit (POST, public): the authority's
  signed handshake (HMAC-SHA256 over the ordered binding, single-use nonces,
  challenge digest), answered with a proof.
- bakney/v1/login-start, callback (GET, public): the browser handoff; every
  SSOError becomes a redirect to /#/bakney-login?error=<code>. login-start
  stores a login (cookie digest, state, encrypted PKCE verifier) and redirects
  to the authority UI with an S256 code challenge; callback consumes the state,
  redeems the code at the authority's token endpoint and marks the login ready.
- bakney/v1/session (GET, POST, public): reads the ready login; POST checks
  Origin and X-SSO-CSRF, the confirmed account switch, synchronizes again and
  issues the local JWT login response (BKN_AUTH cookie).

Every case starts from an authored SSO state (setup: configuration, pairing,
logins, challenges, nonces, revocations) applied by the runner before the
request; the per-run secret and the private authority stub address are
substituted at run time ('$secret', '$authority'). Outbound authority requests
are pinned (path, JSON body) with the stub's authored replies.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('sso_pairing_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE, COLLABORATOR = (IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete'],
                                             IDS['full_collaborator'])
ids = iter(f'b2000000-0000-4000-8000-{1600001 + n:012d}' for n in range(100))
INSTANCE, LOCAL, REMOTE, GEN, GEN2, ATTEMPT, OLD_LOCAL, OLD_REMOTE, UNKNOWN = (next(ids) for _ in range(9))
EXTRA_USERS = {label: {'id': next(ids), **spec} for label, spec in {
    'outsider': {'role': 2, 'first_name': 'Esterno', 'link': None},
    'inactive_member': {'role': 2, 'first_name': 'Inattivo', 'link': 'associate', 'is_active': False},
    'staff_member': {'role': 2, 'first_name': 'Staff', 'link': 'associate', 'is_staff': True},
    'instructor_member': {'role': 2, 'first_name': '', 'link': 'instructor'},
    'former_member': {'role': 2, 'first_name': 'Ex', 'link': 'deleted_associate'},
}.items()}
LINK_IDS = {label: next(ids) for label in EXTRA_USERS}

VERSION = 'bakney-pairing-v1'
ORIGIN = 'http://localhost'
CALLBACK = ORIGIN + '/bakney/v1/callback'
UI = 'https://app.bakney.example'
COOKIE = 'assozeta-sso-dev'
ADMIN, CHALLENGE, COMMIT = '/instance/admin/bakney-pairing', '/bakney/v1/pairing/challenge', '/bakney/v1/pairing/commit'
START, CALLBACK_PATH, SESSION = '/bakney/v1/login-start', '/bakney/v1/callback', '/bakney/v1/session'
JSON = 'application/json'
DENOMINATION = 'Message reads own'
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
SECURE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
CHECKED = '2026-11-01T08:00:00Z'

BROWSER = 'sso-browser-cookie-000000000000000000000001'
OTHER_BROWSER = 'sso-browser-cookie-000000000000000000000002'
STATE = 'sso-state-value-0000000000000000000000000001'
VERIFIER = 'sso-pkce-verifier-00000000000000000000000001'
CODE = 'sso-authorization-code-000000000000000000001'
NONCES = iter(f'nonce-{n:037d}' for n in range(1, 200))

cases = []
VIEWS = {ADMIN: 'PairingAdminView', CHALLENGE: 'PairingChallengeView', COMMIT: 'PairingCommitView', START: 'LoginStartView',
         CALLBACK_PATH: 'LoginCallbackView', SESSION: 'LoginSessionView'}


# --- State presets ---------------------------------------------------------

def pairing(state='paired', **changes):
    base = {'instance_id': INSTANCE, 'pairing_id': LOCAL, 'secret': '$secret', 'authority': '$authority', 'ui_origin': UI,
            'remote_pairing_id': REMOTE, 'remote_generation': GEN, 'origin': ORIGIN, 'association_id': OWN,
            'association_name': DENOMINATION, 'state': 'paired', 'forwarding_enabled': True, 'checked_at': CHECKED}
    if state == 'generated':
        base.update(remote_pairing_id=None, remote_generation=None, association_name='', state='generated', forwarding_enabled=False,
                    checked_at=None)
    elif state == 'challenged':
        base.update(remote_pairing_id=None, remote_generation=None, association_name='', state='pending', forwarding_enabled=False,
                    checked_at=None)
    elif state == 'pending':
        base.update(association_name='', state='pending', forwarding_enabled=False, checked_at=None)
    elif state == 'disconnected':
        base.update(secret='', state='disconnected', forwarding_enabled=False)
    base.update(changes)
    return base


def login(stage='started', **changes):
    row = {'cookie': BROWSER, 'pairing_id': LOCAL, 'remote_generation': GEN, 'attempt_id': ATTEMPT, 'state': STATE,
           'verifier': VERIFIER if stage == 'started' else '', 'stage': stage, 'user': None, 'expires_in': 240}
    row.update(changes)
    return row


def setup(pair='paired', logins=(), challenges=(), nonces=(), revocations=(), config=None):
    return {'config': config or {'self_hosted': True, 'primary': 'own'}, 'pairing': pair if pair is None or isinstance(pair, dict)
            else pairing(pair), 'logins': list(logins), 'challenges': list(challenges), 'nonces': list(nonces),
            'revocations': list(revocations)}


def unchanged():
    return {'pairing': 'unchanged', 'logins': 'unchanged', 'challenges': 'unchanged', 'nonces': 'unchanged',
            'revocations': 'unchanged', 'last_login': None}


def effect(**changes):
    result = unchanged()
    result.update(changes)
    return result


def saved(row):
    return {**row, 'saved': True}


def revocation(local=LOCAL, remote=REMOTE):
    return {'pairing_id': local, 'remote_pairing_id': remote, 'authority': '$authority', 'secret': '$secret'}


# --- Authority exchanges -----------------------------------------------------

def status_request():
    return {'path': '/pairing/v1/status', 'body': {'pairing_id': REMOTE, 'secret': '$secret', 'action': 'status'}}


def status_reply(status='verified', forwarding=True, **changes):
    body = {'protocol': VERSION, 'pairing_id': REMOTE, 'association_id': OWN, 'instance_id': None if status == 'pending' else INSTANCE,
            'origin': ORIGIN, 'callback_uri': CALLBACK, 'generation': GEN, 'status': status, 'forwarding': forwarding}
    body.update(changes)
    return body


def status_exchange(reply=None, *, http=200, raw=None):
    exchange = status_request()
    exchange['reply'] = {'status': http, 'json': status_reply() if reply is None else reply} if raw is None else {'status': http, 'raw': raw}
    return exchange


def token_exchange(reply=None, *, http=200, raw=None, user=ATHLETE):
    body = {'protocol': VERSION, 'pairing_id': REMOTE, 'instance_id': INSTANCE, 'association_id': OWN, 'amr': ['mfa'],
            'auth_time': '$now-30', 'user_id': user}
    if reply:
        body.update(reply)
    exchange = {'path': '/pairing/v1/token',
                'body': {'callback_uri': CALLBACK, 'code': CODE, 'code_verifier': VERIFIER, 'pairing_id': REMOTE, 'secret': '$secret'}}
    exchange['reply'] = {'status': http, 'json': body} if raw is None else {'status': http, 'raw': raw}
    return exchange


# --- Case builders -----------------------------------------------------------

def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None,
        cookies=None, state=None, authority=(), effect_=None, location=None, set_cookies=(), throttle=None, query=''):
    case = {'name': f'sso-pairing-{name}', 'actor': actor, 'method': method, 'path': path + query, 'view': VIEWS[path],
            'headers': {'Host': 'localhost', **(headers or {})}, 'cookies': cookies or [], 'setup': state or setup(),
            'authority': list(authority), 'status': status, 'response': response, 'location': location,
            'set_cookies': list(set_cookies), 'effect': effect_ or unchanged()}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if throttle is not None:
        case['throttle'] = throttle
    cases.append(case)


def error(code):
    return {'error': code}


def redirect_error(code):
    return '/#/bakney-login?error=' + code


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'], 'parses': PARSES}


def method_error(method):
    return {'detail': f'Method "{method}" not allowed.'}


def snapshot(row, *, valid=True, pending=False, sync_error=None, checked_at=None, **extra):
    state = row['state']
    if row['secret'] and not valid:
        state = 'revalidation_required'
    body = {'protocol': VERSION, 'instance_id': row['instance_id'], 'pairing_id': row['pairing_id'], 'origin': row['origin'],
            'state': state, 'association': {'id': row['association_id'], 'name': row['association_name']} if row['association_name'] else None,
            'forwarding_enabled': row['forwarding_enabled'] if valid and row['state'] == 'paired' else False,
            'checked_at': row['checked_at'] if checked_at is None else checked_at, 'notification_pending': pending}
    body.update(extra)
    if sync_error is not False:
        body['sync_error'] = sync_error
    return body


def handshake(kind, **changes):
    body = {'protocol': VERSION, 'pairing_id': REMOTE, 'association_id': OWN, 'instance_id': '' if kind == 'challenge' else INSTANCE,
            'origin': ORIGIN, 'callback_uri': CALLBACK, 'generation': GEN, 'nonce': next(NONCES), 'expires_at': '$expires+60'}
    if kind == 'commit':
        body['proof'] = '$proof'
    body.update(changes)
    return body


def challenge_row(expires='$sent', **changes):
    row = {'pairing_id': LOCAL, 'remote_pairing_id': REMOTE, 'generation': GEN, 'expires': expires}
    row.update(changes)
    return row


def nonce_row(phase, nonce, expires='$sent', local=LOCAL):
    return {'pairing_id': local, 'phase': phase, 'nonce': nonce, 'expires': expires}


def cookie_header(value=BROWSER, name=COOKIE):
    return [[name, value]]


STARTED_COOKIE = COOKIE + '=$fresh; expires=$expires(300); HttpOnly; Max-Age=300; Path=/; SameSite=Lax'
READY_COOKIE = COOKIE + '=' + BROWSER + '; expires=$expires(120); HttpOnly; Max-Age=120; Path=/; SameSite=Lax'
AUTH_COOKIE = 'BKN_AUTH=$access; expires=$expires(14400); HttpOnly; Max-Age=14400; Path=/; SameSite=Strict'
DELETE_COOKIE = COOKIE + '=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax'
HANDOFF = (UI + '/handoff.html#attempt_id=' + ATTEMPT + '&callback_uri=http%3A%2F%2Flocalhost%2Fbakney%2Fv1%2Fcallback'
           '&state=$state&code_challenge_method=S256&code_challenge=$challenge')
START_QUERY = f'?protocol={VERSION}&pairing_id={REMOTE}&attempt_id={ATTEMPT}'
CALLBACK_QUERY = f'?code={CODE}&state={STATE}'
SYNCED = saved(pairing(checked_at='$now'))


# =============================================================================
# instance/admin/bakney-pairing
# =============================================================================

add('admin-anonymous', 'anonymous', ADMIN, 401, {'detail': 'Authentication credentials were not provided.'})
add('admin-athlete', 'athlete', ADMIN, 403, {'detail': 'Only the instance owner or an administrator can administer Bakney pairing.'})
add('admin-collaborator', 'full_collaborator', ADMIN, 403,
    {'detail': 'Only the instance owner or an administrator can administer Bakney pairing.'})
add('admin-foreign-owner-post', 'foreign_owner', ADMIN, 403,
    {'detail': 'Only the instance owner or an administrator can administer Bakney pairing.'}, method='POST',
    body={'action': 'disconnect', 'pairing_id': LOCAL})
add('admin-options', 'owner', ADMIN, 200, options('Pairing Admin'), method='OPTIONS')
add('admin-delete', 'owner', ADMIN, 405, method_error('DELETE'), method='DELETE')
add('admin-get-creates', 'owner', ADMIN, 200,
    {'protocol': VERSION, 'instance_id': '$new', 'pairing_id': '$new', 'origin': '', 'state': 'disconnected', 'association': None,
     'forwarding_enabled': False, 'checked_at': None, 'notification_pending': False, 'sync_error': None},
    state=setup(None), effect_=effect(pairing='created'))
add('admin-get-generated', 'owner', ADMIN, 200, snapshot(pairing('generated')), state=setup('generated'))
add('admin-get-synchronizes', 'owner', ADMIN, 200, snapshot(pairing(), checked_at='$now'), authority=[status_exchange()],
    effect_=effect(pairing=SYNCED))
add('admin-get-forwarding-off', 'admin', ADMIN, 200,
    snapshot(pairing(forwarding_enabled=False), checked_at='$now'),
    authority=[status_exchange(status_reply(forwarding=False))],
    effect_=effect(pairing=saved(pairing(checked_at='$now', forwarding_enabled=False))))
add('admin-get-pending-confirmed', 'owner', ADMIN, 200, snapshot(pairing('pending')), state=setup('pending'),
    authority=[status_exchange(status_reply('pending', False))], effect_=effect(pairing=saved(pairing('pending'))))
add('admin-get-pending-to-paired', 'owner', ADMIN, 200,
    snapshot(pairing(), checked_at='$now'), state=setup('pending'),
    authority=[status_exchange(status_reply(forwarding=True))], effect_=effect(pairing=SYNCED))
add('admin-get-pending-new-generation', 'owner', ADMIN, 200, snapshot(pairing('pending'), sync_error='revalidation_required'),
    state=setup('pending'), authority=[status_exchange(status_reply('pending', False, generation=GEN2))])
add('admin-get-rejected', 'admin', ADMIN, 200,
    snapshot(pairing('disconnected'), pending=True, sync_error='pairing_rejected'),
    authority=[status_exchange(http=401, raw='{"error": "invalid_secret"}')],
    state=setup(logins=[login(), login('ready', cookie=OTHER_BROWSER, user='athlete')], challenges=[challenge_row(expires=300)],
                revocations=[revocation(OLD_LOCAL, OLD_REMOTE)]),
    effect_=effect(pairing=saved(pairing('disconnected')), logins=[], challenges=[],
                   revocations=[revocation(OLD_LOCAL, OLD_REMOTE), revocation()]))
add('admin-get-revoked', 'owner', ADMIN, 200, snapshot(pairing('disconnected'), pending=True),
    authority=[status_exchange(status_reply('revoked', False))],
    effect_=effect(pairing=saved(pairing('disconnected')), revocations=[revocation()]))
add('admin-get-unavailable', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='bakney_unavailable'),
    authority=[status_exchange(http=503, raw='busy')])
add('admin-get-redirect', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='bakney_unavailable'),
    authority=[status_exchange(http=302, raw='')])
add('admin-get-gone', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='handoff_rejected'),
    authority=[status_exchange(http=410, raw='{}')])
add('admin-get-not-json', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='bakney_unavailable'),
    authority=[status_exchange(raw='<html>not json</html>')])
add('admin-get-list-json', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(raw='[1, 2]')])
add('admin-get-wrong-protocol', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(status_reply(protocol='bakney-pairing-v2'))])
add('admin-get-wrong-binding', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(status_reply(origin='http://localhost:8080'))])
add('admin-get-bad-generation', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(status_reply(generation=GEN.upper()))])
add('admin-get-forwarding-not-bool', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(status_reply(forwarding=1))])
add('admin-get-oversize', 'owner', ADMIN, 200, snapshot(pairing(), sync_error='invalid_response'),
    authority=[status_exchange(raw=json.dumps({**status_reply(), 'padding': 'x' * 8200}))])
add('admin-get-binding-changed', 'owner', ADMIN, 200,
    snapshot(pairing(origin='http://localhost:8000'), valid=False, sync_error='revalidation_required'),
    state=setup(pairing(origin='http://localhost:8000')))
add('admin-get-undecryptable', 'owner', ADMIN, 200, snapshot(pairing(secret='$garbage'), sync_error='configuration_required'),
    state=setup(pairing(secret='$garbage')))
add('admin-get-no-primary', 'admin', ADMIN, 409, error('configuration_required'),
    state=setup(config={'self_hosted': True, 'primary': None}))
add('admin-not-self-hosted', 'owner', ADMIN, 403, {'detail': 'Only the instance owner or an administrator can administer Bakney pairing.'},
    state=setup(config={'self_hosted': False, 'primary': 'own'}))

GENERATED_ROW = {'instance_id': INSTANCE, 'pairing_id': '$new', 'secret': '$response', 'authority': '$authority', 'ui_origin': UI,
                 'remote_pairing_id': None, 'remote_generation': None, 'origin': ORIGIN, 'association_id': OWN, 'association_name': '',
                 'state': 'generated', 'forwarding_enabled': False, 'checked_at': None, 'saved': True}
add('admin-generate', 'owner', ADMIN, 200,
    {'protocol': VERSION, 'instance_id': INSTANCE, 'pairing_id': '$new', 'origin': ORIGIN, 'state': 'generated', 'association': None,
     'forwarding_enabled': False, 'checked_at': None, 'notification_pending': True, 'secret': '$secret-new'},
    method='POST', body={'action': 'generate', 'pairing_id': LOCAL},
    state=setup(logins=[login()], challenges=[challenge_row(expires=300)]),
    effect_=effect(pairing=GENERATED_ROW, logins=[], challenges=[], revocations=[revocation()]))
add('admin-generate-from-disconnected', 'admin', ADMIN, 200,
    {'protocol': VERSION, 'instance_id': INSTANCE, 'pairing_id': '$new', 'origin': ORIGIN, 'state': 'generated', 'association': None,
     'forwarding_enabled': False, 'checked_at': None, 'notification_pending': False, 'secret': '$secret-new'},
    method='POST', body={'action': 'generate', 'pairing_id': LOCAL}, state=setup('disconnected'),
    effect_=effect(pairing=GENERATED_ROW))
add('admin-disconnect', 'owner', ADMIN, 200, snapshot(pairing('disconnected'), pending=True, sync_error=False), method='POST',
    body={'action': 'disconnect', 'pairing_id': LOCAL}, state=setup(logins=[login()]),
    effect_=effect(pairing=saved(pairing('disconnected')), logins=[], revocations=[revocation()]))
add('admin-disconnect-form', 'owner', ADMIN, 200, snapshot(pairing('disconnected'), pending=True, sync_error=False), method='POST',
    raw_body=f'action=generate&action=disconnect&pairing_id={LOCAL}', content_type='application/x-www-form-urlencoded',
    effect_=effect(pairing=saved(pairing('disconnected')), revocations=[revocation()]))
add('admin-sync', 'owner', ADMIN, 200, snapshot(pairing(), checked_at='$now', sync_error=False), method='POST',
    body={'action': 'sync', 'pairing_id': LOCAL}, authority=[status_exchange()], effect_=effect(pairing=SYNCED))
add('admin-sync-generated', 'owner', ADMIN, 409, error('invalid_pairing'), method='POST', body={'action': 'sync', 'pairing_id': LOCAL},
    state=setup('generated'))
add('admin-sync-rejected', 'owner', ADMIN, 403, error('pairing_rejected'), method='POST', body={'action': 'sync', 'pairing_id': LOCAL},
    authority=[status_exchange(http=401, raw='')],
    effect_=effect(pairing=saved(pairing('disconnected')), revocations=[revocation()]))
add('admin-sync-conflict', 'owner', ADMIN, 400, error('handoff_rejected'), method='POST', body={'action': 'sync', 'pairing_id': LOCAL},
    authority=[status_exchange(http=409, raw='{}')])
add('admin-stale', 'owner', ADMIN, 409, error('stale_pairing'), method='POST', body={'action': 'disconnect', 'pairing_id': OLD_LOCAL})
add('admin-extra-key', 'owner', ADMIN, 409, error('stale_pairing'), method='POST',
    body={'action': 'disconnect', 'pairing_id': LOCAL, 'force': True})
add('admin-list-body', 'owner', ADMIN, 409, error('stale_pairing'), method='POST', body=['action', 'pairing_id'])
add('admin-unknown-action', 'owner', ADMIN, 400, error('invalid_request'), method='POST', body={'action': 'pair', 'pairing_id': LOCAL})
add('admin-malformed-json', 'owner', ADMIN, 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    method='POST', raw_body='nope', content_type=JSON, state=setup(None), effect_=effect(pairing='created'))
add('admin-throttled', 'owner', ADMIN, 429, {'detail': 'Request was throttled. Expected available in 50 seconds.'},
    throttle={'preload': 30, 'age': 10.2})

# =============================================================================
# pairing/challenge and pairing/commit
# =============================================================================

add('challenge-options', 'anonymous', CHALLENGE, 200, options('Pairing Challenge'), method='OPTIONS')
add('commit-options', 'anonymous', COMMIT, 200, options('Pairing Commit'), method='OPTIONS')
add('challenge-get', 'anonymous', CHALLENGE, 405, method_error('GET'))
add('challenge-no-primary', 'anonymous', CHALLENGE, 409, error('configuration_required'), method='POST', body=handshake('challenge'),
    state=setup('generated', config={'self_hosted': True, 'primary': None}))
add('challenge-not-self-hosted', 'anonymous', CHALLENGE, 409, error('configuration_required'), method='POST',
    body=handshake('challenge'), state=setup('generated', config={'self_hosted': False, 'primary': 'own'}))
add('challenge-creates-disconnected', 'anonymous', CHALLENGE, 409, error('revalidation_required'), method='POST',
    body=handshake('challenge'), state=setup(None), effect_=effect(pairing='created'))
add('challenge-binding-changed', 'anonymous', CHALLENGE, 409, error('revalidation_required'), method='POST',
    body=handshake('challenge'), state=setup(pairing('generated', ui_origin='https://other.bakney.example')))
add('challenge-paired', 'athlete', CHALLENGE, 409, error('invalid_pairing'), method='POST', body=handshake('challenge'))
add('challenge-pending-remote', 'anonymous', CHALLENGE, 409, error('invalid_pairing'), method='POST', body=handshake('challenge'),
    state=setup('pending'))

nonce = next(NONCES)
add('challenge-accepted', 'anonymous', CHALLENGE, 200, {'protocol': VERSION, 'instance_id': INSTANCE, 'proof': '$proof:challenge'},
    method='POST', body=handshake('challenge', nonce=nonce), state=setup('generated'),
    effect_=effect(pairing=saved(pairing('challenged')), challenges=[challenge_row()], nonces=[nonce_row('challenge', nonce)]))
nonce = next(NONCES)
add('challenge-repeated', 'anonymous', CHALLENGE, 200, {'protocol': VERSION, 'instance_id': INSTANCE, 'proof': '$proof:challenge'},
    method='POST', body=handshake('challenge', nonce=nonce, expires_at='$expires+120'),
    state=setup('challenged', challenges=[challenge_row(expires=30)], nonces=[nonce_row('challenge', 'nonce-' + '9' * 37, 30)]),
    effect_=effect(pairing=saved(pairing('challenged')), challenges=[challenge_row()],
                   nonces=[nonce_row('challenge', 'nonce-' + '9' * 37, 30), nonce_row('challenge', nonce)]))
nonce = next(NONCES)
add('challenge-replayed', 'anonymous', CHALLENGE, 409, error('replayed_request'), method='POST', body=handshake('challenge', nonce=nonce),
    state=setup('generated', nonces=[nonce_row('challenge', nonce, 60)]))
add('challenge-commit-nonce-reused', 'anonymous', CHALLENGE, 200,
    {'protocol': VERSION, 'instance_id': INSTANCE, 'proof': '$proof:challenge'}, method='POST',
    body=handshake('challenge', nonce='nonce-' + '7' * 37), state=setup('generated', nonces=[nonce_row('commit', 'nonce-' + '7' * 37, 60)]),
    effect_=effect(pairing=saved(pairing('challenged')), challenges=[challenge_row()],
                   nonces=[nonce_row('commit', 'nonce-' + '7' * 37, 60), nonce_row('challenge', 'nonce-' + '7' * 37)]))
for label, change in (('missing-field', {'drop': 'generation'}), ('proof-field', {'proof': '0' * 64}),
                      ('protocol', {'protocol': 'bakney-pairing-v0'}), ('instance-id', {'instance_id': INSTANCE}),
                      ('association', {'association_id': FOREIGN}), ('origin', {'origin': 'http://localhost:80'}),
                      ('callback', {'callback_uri': CALLBACK + '/'}), ('pairing-upper', {'pairing_id': REMOTE.upper()}),
                      ('generation-braces', {'generation': '{' + GEN + '}'}), ('nonce-short', {'nonce': 'n' * 42}),
                      ('nonce-chars', {'nonce': 'n' * 42 + '='}), ('nonce-number', {'nonce': 7}),
                      ('expires-far', {'expires_at': '$expires+125'}), ('expires-past', {'expires_at': '$expires+0'}),
                      ('expires-float', {'expires_at': '$expires+60.0'}), ('expires-string', {'expires_at': '$expires+60s'}),
                      ('expires-bool', {'expires_at': True})):
    body = handshake('challenge')
    if 'drop' in change:
        body.pop(change['drop'])
    else:
        body.update(change)
    add('challenge-invalid-' + label, 'anonymous', CHALLENGE, 400, error('invalid_request'), method='POST', body=body,
        state=setup('generated'))
add('challenge-list-body', 'anonymous', CHALLENGE, 400, error('invalid_request'), method='POST', body=[1], state=setup('generated'))
add('challenge-form-body', 'anonymous', CHALLENGE, 400, error('invalid_request'), method='POST', state=setup('generated'),
    raw_body=f'protocol={VERSION}&pairing_id={REMOTE}', content_type='application/x-www-form-urlencoded')
add('challenge-malformed-json', 'anonymous', CHALLENGE, 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}, method='POST',
    raw_body='{,}', content_type=JSON, state=setup('generated'))
add('challenge-text-body', 'anonymous', CHALLENGE, 415, {'detail': 'Unsupported media type "text/plain" in request.'}, method='POST',
    raw_body='hello', content_type='text/plain', state=setup('generated'))
add('challenge-undecryptable', 'anonymous', CHALLENGE, 409, error('configuration_required'), method='POST', body=handshake('challenge'),
    state=setup(pairing('generated', secret='$garbage')))
add('challenge-throttled', 'anonymous', CHALLENGE, 429, {'detail': 'Request was throttled. Expected available in 50 seconds.'},
    method='POST', body=handshake('challenge'), state=setup('generated'), throttle={'preload': 30, 'age': 10.2})

nonce = next(NONCES)
add('commit-accepted', 'anonymous', COMMIT, 200, {'protocol': VERSION, 'instance_id': INSTANCE, 'proof': '$proof:ack'}, method='POST',
    body=handshake('commit', nonce=nonce),
    state=setup('challenged', logins=[login()], challenges=[challenge_row(expires=60), challenge_row(expires=60, generation=GEN2)]),
    effect_=effect(pairing=saved(pairing('pending')), challenges=[], nonces=[nonce_row('commit', nonce)]))
add('commit-bad-proof', 'anonymous', COMMIT, 403, error('invalid_proof'), method='POST', body=handshake('commit', proof='$proof-other'),
    state=setup('challenged', challenges=[challenge_row(expires=60)]))
add('commit-upper-proof', 'anonymous', COMMIT, 403, error('invalid_proof'), method='POST', body=handshake('commit', proof='$proof-upper'),
    state=setup('challenged', challenges=[challenge_row(expires=60)]))
add('commit-proof-number', 'anonymous', COMMIT, 403, error('invalid_proof'), method='POST', body=handshake('commit', proof=12),
    state=setup('challenged', challenges=[challenge_row(expires=60)]))
add('commit-challenge-proof', 'anonymous', COMMIT, 403, error('invalid_proof'), method='POST',
    body=handshake('commit', proof='$proof-challenge'), state=setup('challenged', challenges=[challenge_row(expires=60)]))
add('commit-without-challenge', 'anonymous', COMMIT, 409, error('invalid_pairing'), method='POST', body=handshake('commit'),
    state=setup('challenged', challenges=[challenge_row(expires=60, generation=GEN2)]))
add('commit-expired-challenge', 'anonymous', COMMIT, 409, error('invalid_pairing'), method='POST', body=handshake('commit'),
    state=setup('challenged', challenges=[challenge_row(expires=-5)]))
add('commit-other-pairing-challenge', 'anonymous', COMMIT, 409, error('invalid_pairing'), method='POST', body=handshake('commit'),
    state=setup('challenged', challenges=[challenge_row(expires=60, pairing_id=OLD_LOCAL)]))
nonce = next(NONCES)
add('commit-replayed', 'anonymous', COMMIT, 409, error('replayed_request'), method='POST', body=handshake('commit', nonce=nonce),
    state=setup('challenged', challenges=[challenge_row(expires=60)], nonces=[nonce_row('commit', nonce, 60)]))
add('commit-empty-instance', 'anonymous', COMMIT, 400, error('invalid_request'), method='POST',
    body=handshake('commit', instance_id=''), state=setup('challenged', challenges=[challenge_row(expires=60)]))
commit_without_proof = handshake('commit')
commit_without_proof.pop('proof')
add('commit-missing-proof', 'anonymous', COMMIT, 400, error('invalid_request'), method='POST', body=commit_without_proof,
    state=setup('challenged', challenges=[challenge_row(expires=60)]))
add('commit-after-commit', 'anonymous', COMMIT, 409, error('invalid_pairing'), method='POST', body=handshake('commit'),
    state=setup('pending', challenges=[challenge_row(expires=60)]))

# =============================================================================
# login-start
# =============================================================================

NEW_LOGIN = {'cookie': '$fresh', 'pairing_id': LOCAL, 'remote_generation': GEN, 'attempt_id': ATTEMPT, 'state': '$state',
             'verifier': '$verifier', 'stage': 'started', 'user': None, 'expires_in': 300}
add('start-options', 'anonymous', START, 200, options('Login Start'), method='OPTIONS')
add('start-post', 'anonymous', START, 405, method_error('POST'), method='POST', body={})
add('start-success', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=HANDOFF, set_cookies=[STARTED_COOKIE],
    authority=[status_exchange()], effect_=effect(pairing=SYNCED, logins=[NEW_LOGIN]))
add('start-replaces-previous', 'athlete', START, 302, '<redirect>', query=START_QUERY, location=HANDOFF,
    set_cookies=[STARTED_COOKIE], cookies=cookie_header(), authority=[status_exchange()],
    state=setup(logins=[login(), login('ready', cookie=OTHER_BROWSER, user='athlete')]),
    effect_=effect(pairing=SYNCED, logins=[login('ready', cookie=OTHER_BROWSER, user='athlete'), NEW_LOGIN]))
add('start-wrong-host', 'anonymous', START, 302, '<redirect>', query=START_QUERY, headers={'Host': '127.0.0.1'},
    location=redirect_error('revalidation_required'))
add('start-no-primary', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('configuration_required'),
    state=setup(config={'self_hosted': True, 'primary': None}))
add('start-generated', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('invalid_pairing'),
    state=setup('generated'))
add('start-forwarding-off', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('forwarding_disabled'),
    authority=[status_exchange(status_reply(forwarding=False))],
    effect_=effect(pairing=saved(pairing(checked_at='$now', forwarding_enabled=False))))
add('start-pending', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('forwarding_disabled'),
    state=setup('pending'), authority=[status_exchange(status_reply('pending', False))],
    effect_=effect(pairing=saved(pairing('pending'))))
add('start-rejected', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('pairing_rejected'),
    authority=[status_exchange(http=401, raw='')], state=setup(logins=[login()]),
    effect_=effect(pairing=saved(pairing('disconnected')), logins=[], revocations=[revocation()]))
add('start-unavailable', 'anonymous', START, 302, '<redirect>', query=START_QUERY, location=redirect_error('bakney_unavailable'),
    authority=[status_exchange(http=500, raw='')])
for label, query in (('missing-attempt', f'?protocol={VERSION}&pairing_id={REMOTE}'),
                     ('extra-param', START_QUERY + '&next=/'),
                     ('duplicate-attempt', START_QUERY + f'&attempt_id={ATTEMPT}'),
                     ('wrong-pairing', f'?protocol={VERSION}&pairing_id={LOCAL}&attempt_id={ATTEMPT}'),
                     ('attempt-upper', f'?protocol={VERSION}&pairing_id={REMOTE}&attempt_id={ATTEMPT.upper()}'),
                     ('protocol', f'?protocol=bakney-pairing-v2&pairing_id={REMOTE}&attempt_id={ATTEMPT}'),
                     ('blank-key', START_QUERY + '&=x')):
    add('start-invalid-' + label, 'anonymous', START, 302, '<redirect>', query=query, location=redirect_error('invalid_handoff'),
        authority=[status_exchange()], effect_=effect(pairing=SYNCED))
add('start-encoded-query', 'anonymous', START, 302, '<redirect>',
    query=f'?protocol=bakney%2Dpairing%2Dv1&pairing_id={REMOTE}&attempt_id={ATTEMPT}', location=HANDOFF, set_cookies=[STARTED_COOKIE],
    authority=[status_exchange()], effect_=effect(pairing=SYNCED, logins=[NEW_LOGIN]))
add('start-throttled', 'anonymous', START, 429, {'detail': 'Request was throttled. Expected available in 50 seconds.'},
    query=START_QUERY, throttle={'preload': 30, 'age': 10.2})

# =============================================================================
# callback
# =============================================================================

def ready_login(user='athlete', stage='ready', **changes):
    return login(stage, user=user, expires_in=120, **changes)


add('callback-success', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location='/#/bakney-login',
    set_cookies=[READY_COOKIE], cookies=cookie_header(), state=setup(logins=[login()]),
    authority=[status_exchange(), token_exchange()], effect_=effect(pairing=SYNCED, logins=[ready_login()]))
add('callback-collaborator', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location='/#/bakney-login',
    set_cookies=[READY_COOKIE], cookies=cookie_header(), state=setup(logins=[login()]),
    authority=[status_exchange(), token_exchange({'amr': ['authenticated']}, user=COLLABORATOR)],
    effect_=effect(pairing=SYNCED, logins=[ready_login('full_collaborator')]))
add('callback-instructor', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location='/#/bakney-login',
    set_cookies=[READY_COOKIE], cookies=cookie_header(), state=setup(logins=[login()]),
    authority=[status_exchange(), token_exchange(user=EXTRA_USERS['instructor_member']['id'])],
    effect_=effect(pairing=SYNCED, logins=[ready_login('instructor_member')]))
add('callback-no-cookie', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location=redirect_error('handoff_expired'),
    state=setup(logins=[login()]))
add('callback-unknown-cookie', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('handoff_expired'), cookies=cookie_header(OTHER_BROWSER), state=setup(logins=[login()]))
add('callback-short-cookie', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('handoff_expired'), cookies=cookie_header('short'), state=setup(logins=[login()]))
add('callback-expired', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location=redirect_error('handoff_expired'),
    cookies=cookie_header(), state=setup(logins=[login(expires_in=-1)]))
add('callback-other-pairing', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('handoff_expired'), cookies=cookie_header(), state=setup(logins=[login(pairing_id=OLD_LOCAL)]))
add('callback-wrong-state', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=f'?code={CODE}&state={STATE[:-1]}2',
    location=redirect_error('invalid_handoff'), cookies=cookie_header(), state=setup(logins=[login()]))
add('callback-extra-param', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY + '&error=x',
    location=redirect_error('invalid_handoff'), cookies=cookie_header(), state=setup(logins=[login()]))
add('callback-short-code', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=f'?code=abc&state={STATE}',
    location=redirect_error('invalid_handoff'), cookies=cookie_header(), state=setup(logins=[login()]))
add('callback-already-used', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('invalid_handoff'), cookies=cookie_header(), state=setup(logins=[login('failed')]))
add('callback-wrong-host', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, headers={'Host': '127.0.0.1'},
    location=redirect_error('revalidation_required'), cookies=cookie_header(), state=setup(logins=[login()]))
add('callback-forwarding-off', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('forwarding_disabled'), cookies=cookie_header(), state=setup(logins=[login()]),
    authority=[status_exchange(status_reply(forwarding=False))],
    effect_=effect(pairing=saved(pairing(checked_at='$now', forwarding_enabled=False)), logins=[login('failed')]))
add('callback-generation-changed', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('invalid_handoff'), cookies=cookie_header(), state=setup(logins=[login(remote_generation=GEN2)]),
    authority=[status_exchange()], effect_=effect(pairing=SYNCED, logins=[login('failed', remote_generation=GEN2)]))
for label, exchange, code in (
        ('token-rejected', token_exchange(http=403, raw='{"error": "invalid_grant"}'), 'handoff_rejected'),
        ('token-unauthorized', token_exchange(http=401, raw=''), 'pairing_rejected'),
        ('token-unavailable', token_exchange(http=502, raw=''), 'bakney_unavailable'),
        ('token-binding', token_exchange({'instance_id': UNKNOWN}), 'invalid_response'),
        ('token-amr', token_exchange({'amr': ['pwd']}), 'invalid_response'),
        ('token-auth-time-string', token_exchange({'auth_time': '1700000000'}), 'invalid_response'),
        ('token-auth-time-future', token_exchange({'auth_time': '$now+120'}), 'invalid_response'),
        ('token-auth-time-zero', token_exchange({'auth_time': 0}), 'invalid_response'),
        ('token-protocol', token_exchange({'protocol': 'other'}), 'invalid_response'),
        ('token-unknown-user', token_exchange(user=UNKNOWN), 'account_unavailable'),
        ('token-user-number', token_exchange({'user_id': 12}), 'account_unavailable'),
        ('token-inactive-user', token_exchange(user=EXTRA_USERS['inactive_member']['id']), 'account_unavailable'),
        ('token-owner', token_exchange(user=OWNER), 'account_not_eligible'),
        ('token-staff', token_exchange(user=EXTRA_USERS['staff_member']['id']), 'account_not_eligible'),
        ('token-outsider', token_exchange(user=EXTRA_USERS['outsider']['id']), 'membership_required'),
        ('token-former-member', token_exchange(user=EXTRA_USERS['former_member']['id']), 'membership_required')):
    add('callback-' + label, 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY, location=redirect_error(code),
        cookies=cookie_header(), state=setup(logins=[login()]), authority=[status_exchange(), exchange],
        effect_=effect(pairing=SYNCED, logins=[login('failed')]))
add('callback-undecryptable-verifier', 'anonymous', CALLBACK_PATH, 302, '<redirect>', query=CALLBACK_QUERY,
    location=redirect_error('configuration_required'), cookies=cookie_header(), state=setup(logins=[login(verifier='$garbage')]))

# =============================================================================
# session
# =============================================================================

READY = setup(logins=[ready_login()])
SESSION_USER = {'id': ATHLETE, 'name': 'Message'}
add('session-options', 'anonymous', SESSION, 200, options('Login Session'), method='OPTIONS')
add('session-put', 'anonymous', SESSION, 405, method_error('PUT'), method='PUT', body={})
add('session-read', 'anonymous', SESSION, 200, {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': []}, cookies=cookie_header(),
    state=READY)
add('session-read-other-bearer', 'owner', SESSION, 200, {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': [OWNER]},
    cookies=cookie_header(), state=READY)
add('session-read-same-user', 'athlete', SESSION, 200, {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': []},
    cookies=[['BKN_AUTH', '$token:athlete'], [COOKIE, BROWSER]], state=READY)
add('session-read-cookie-and-bearer', 'full_collaborator', SESSION, 200,
    {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': sorted([OWNER, COLLABORATOR])},
    cookies=[['BKN_AUTH', '$token:owner'], [COOKIE, BROWSER]], state=READY)
add('session-read-invalid-tokens', 'anonymous', SESSION, 200, {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': []},
    headers={'Authorization': 'Bearer not-a-token'}, cookies=[['BKN_AUTH', 'garbage'], [COOKIE, BROWSER]], state=READY)
add('session-read-instructor-name', 'anonymous', SESSION, 200,
    {'user': {'id': EXTRA_USERS['instructor_member']['id'], 'name': 'sso-pairing-instructor_member'}, 'csrf_token': STATE,
     'confirm_for': []}, cookies=cookie_header(), state=setup(logins=[ready_login('instructor_member')]))
add('session-read-started', 'anonymous', SESSION, 400, error('handoff_expired'), cookies=cookie_header(), state=setup(logins=[login()]))
add('session-read-no-cookie', 'anonymous', SESSION, 400, error('handoff_expired'), state=READY)
add('session-read-quoted-cookie', 'anonymous', SESSION, 200, {'user': SESSION_USER, 'csrf_token': STATE, 'confirm_for': []},
    cookies=[[COOKIE, '"' + BROWSER + '"']], state=READY)
add('session-read-ineligible', 'anonymous', SESSION, 403, error('account_unavailable'), cookies=cookie_header(),
    state=setup(logins=[ready_login('inactive_member')]))
add('session-read-binding-changed', 'anonymous', SESSION, 409, error('revalidation_required'), cookies=cookie_header(),
    state=setup(pairing(ui_origin=UI + ':8443'), logins=[ready_login()]))
add('session-read-wrong-host', 'anonymous', SESSION, 409, error('revalidation_required'), cookies=cookie_header(), state=READY,
    headers={'Host': '127.0.0.1'})

POST_HEADERS = {'Origin': ORIGIN, 'X-SSO-CSRF': STATE}
CONSUMED = [ready_login(stage='consumed')]
add('session-login', 'anonymous', SESSION, 200, '$login:athlete', method='POST', body={'confirm_for': []}, headers=POST_HEADERS,
    cookies=cookie_header(), state=READY, authority=[status_exchange()], set_cookies=[AUTH_COOKIE, DELETE_COOKIE],
    effect_=effect(pairing=SYNCED, logins=CONSUMED, last_login='athlete'))
add('session-login-confirmed-switch', 'owner', SESSION, 200, '$login:athlete', method='POST', body={'confirm_for': [OWNER]},
    headers=POST_HEADERS, cookies=cookie_header(), state=READY, authority=[status_exchange()], set_cookies=[AUTH_COOKIE, DELETE_COOKIE],
    effect_=effect(pairing=SYNCED, logins=CONSUMED, last_login='athlete'))
add('session-login-unconfirmed-switch', 'owner', SESSION, 409, error('account_switch_required'), method='POST',
    body={'confirm_for': []}, headers=POST_HEADERS, cookies=cookie_header(), state=READY)
add('session-login-form-confirm', 'anonymous', SESSION, 409, error('account_switch_required'), method='POST', headers=POST_HEADERS,
    raw_body='confirm_for=', content_type='application/x-www-form-urlencoded', cookies=cookie_header(), state=READY)
add('session-login-wrong-origin', 'anonymous', SESSION, 403, error('invalid_handoff'), method='POST', body={'confirm_for': []},
    headers={'Origin': 'http://127.0.0.1', 'X-SSO-CSRF': STATE}, cookies=cookie_header(), state=READY)
add('session-login-no-origin', 'anonymous', SESSION, 403, error('invalid_handoff'), method='POST', body={'confirm_for': []},
    headers={'X-SSO-CSRF': STATE}, cookies=cookie_header(), state=READY)
add('session-login-wrong-csrf', 'anonymous', SESSION, 403, error('invalid_handoff'), method='POST', body={'confirm_for': []},
    headers={'Origin': ORIGIN, 'X-SSO-CSRF': STATE[:-1]}, cookies=cookie_header(), state=READY)
add('session-login-started', 'anonymous', SESSION, 400, error('handoff_expired'), method='POST', body={'confirm_for': []},
    headers=POST_HEADERS, cookies=cookie_header(), state=setup(logins=[login()]))
add('session-login-consumed', 'anonymous', SESSION, 400, error('handoff_expired'), method='POST', body={'confirm_for': []},
    headers=POST_HEADERS, cookies=cookie_header(), state=setup(logins=CONSUMED))
add('session-login-extra-key', 'anonymous', SESSION, 400, error('invalid_request'), method='POST',
    body={'confirm_for': [], 'remember': True}, headers=POST_HEADERS, cookies=cookie_header(), state=READY)
add('session-login-malformed-json', 'anonymous', SESSION, 400,
    {'detail': 'JSON parse error - Expecting value: line 1 column 17 (char 16)'}, method='POST', raw_body='{"confirm_for": }',
    content_type=JSON, headers=POST_HEADERS, cookies=cookie_header(), state=READY)
add('session-login-forwarding-off', 'anonymous', SESSION, 403, error('forwarding_disabled'), method='POST', body={'confirm_for': []},
    headers=POST_HEADERS, cookies=cookie_header(), state=READY, authority=[status_exchange(status_reply(forwarding=False))],
    effect_=effect(pairing=saved(pairing(checked_at='$now', forwarding_enabled=False))))
add('session-login-generation-changed', 'anonymous', SESSION, 400, error('invalid_handoff'), method='POST', body={'confirm_for': []},
    headers=POST_HEADERS, cookies=cookie_header(), state=setup(logins=[ready_login(remote_generation=GEN2)]),
    authority=[status_exchange()], effect_=effect(pairing=SYNCED))
add('session-login-ineligible', 'anonymous', SESSION, 403, error('membership_required'), method='POST', body={'confirm_for': []},
    headers=POST_HEADERS, cookies=cookie_header(), state=setup(logins=[ready_login('former_member')]),
    authority=[status_exchange()], effect_=effect(pairing=SYNCED))
add('session-throttled', 'anonymous', SESSION, 429, {'detail': 'Request was throttled. Expected available in 50 seconds.'},
    cookies=cookie_header(), state=READY, throttle={'preload': 30, 'age': 10.2})

assert len({c['name'] for c in cases}) == len(cases)
per_view = {}
for c in cases:
    per_view[c['view']] = per_view.get(c['view'], 0) + 1
assert all(count <= 60 for count in per_view.values()), per_view
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'instance/admin/bakney-pairing and bakney/v1/{pairing/challenge, pairing/commit, login-start, callback, '
    'session}', 'ids': IDS,
    'settings': {'app_url': ORIGIN, 'ui_origin': UI, 'api_path': '/api', 'cookie': COOKIE, 'version': VERSION},
    'pairing_ids': {'instance': INSTANCE, 'local': LOCAL, 'remote': REMOTE, 'generation': GEN, 'old_local': OLD_LOCAL},
    'users': EXTRA_USERS, 'links': LINK_IDS, 'denomination': DENOMINATION, 'secure_headers': SECURE,
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases', per_view)
