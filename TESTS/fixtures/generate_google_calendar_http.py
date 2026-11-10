"""Author the google/* HTTP sequence (google-calendar.md).

Five @api_view function views in BE/application/views/google_views.py:

- google/check (GET, IsAuthenticated): the acting user's (the collaborator
  itself for collaborators) `google_sync_enabled`, i.e. stored credentials.
- google/calendar/config GET (IsAuthenticated): a google_auth_oauthlib Flow
  from the client secrets file builds the authorization URL (offline access,
  granted scopes, a fresh 30-character state); the state is stored on the
  acting user (User.save(), audited) and in the request session.
  DELETE: the acting user's credentials (refreshed and saved when expired),
  a POST to Google's revoke endpoint (answer ignored), then state,
  credentials and auto sync are cleared (User.save()).
- google/oauth2callback (GET, AllowAny): ?error answers the cancel page;
  otherwise the user holding ?state (400 when none), the code is exchanged at
  the client secrets token_uri (Basic client authentication), the credentials
  JSON is stored and auto sync turned on for non-collaborators; the page says
  'Autorizzato'.
- google/calendar/list (GET, IsAuthenticated): every calendarList page.
- google/calendar/<course_id>/export (POST, IsAuthenticated): the live
  group-aware course by id (404 'Course not found.'), its stored Google
  calendar deleted (errors ignored), a new calendar inserted ('quotaExceeded'
  in the error text: 403, any other error: 400), the course saved (the new id
  only for non-collaborators), the calendar list entry deleted (errors
  ignored) and inserted with the course colour, then the first attendance
  registry (404 when none, after those writes) exported event by event (insert
  errors ignored).

Google is a per-run stub: every outbound request is pinned (method, path and
query, the exact ordered headers of requests / httplib2 / googleapiclient
except the Host value, and the body) and answered with the authored reply.
Credentials JSON is pinned key by key; its expiry only by format and the
request window. Run with ``python3 -I``.
"""
import base64
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, urlencode
from zoneinfo import ZoneInfo

OUTPUT = Path(__file__).with_name('google_calendar_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FULL = IDS['owner'], IDS['full_collaborator']
ids = iter(f'a1000000-0000-4000-8000-{1640001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
ROME = ZoneInfo('Europe/Rome')

# --- client configuration (placeholders only) ---------------------------------
CLIENT = {'client_id': 'placeholder-client-id.apps.googleusercontent.com', 'project_id': 'assozeta-placeholder',
          'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
          'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs', 'client_secret': 'placeholder-client-secret',
          'redirect_uris': ['https://assozeta.example.test/google/oauth2callback']}
REDIRECT = 'https://assozeta.example.test/google/oauth2callback'
SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/userinfo.email',
          'https://www.googleapis.com/auth/userinfo.profile', 'openid']
PY = '3.13.16'
GDCL = f'gdcl/2.164.0 gl-python/{PY} cred-type/u'
AUTH_METRIC = f'gl-python/{PY} auth/2.38.0 cred-type/u'
REQUESTS_UA = 'python-requests/2.34.2'
HTTPLIB2_UA = 'Python-httplib2/0.32.0 (gzip)'
DESCRIPTION = 'Il calendario viene sincronizzato in automatico, non modificarlo.'


def stored(token, refresh, *, expiry, client_id='placeholder-client-id.apps.googleusercontent.com',
           client_secret='placeholder-client-secret', scopes=SCOPES, extra=None, omit=()):
    """Credentials.to_json() order: token, refresh_token, token_uri, client_id, client_secret, scopes,
    rapt_token, universe_domain, account, expiry (None values dropped)."""
    info = {'token': token, 'refresh_token': refresh, 'token_uri': 'https://oauth2.googleapis.com/token', 'client_id': client_id,
            'client_secret': client_secret, 'scopes': scopes, 'universe_domain': 'googleapis.com', 'account': '', 'expiry': expiry}
    info.update(extra or {})
    return {k: v for k, v in info.items() if v is not None and k not in omit}


# --- seeded users (base actors) -------------------------------------------------
SEED_USERS = {
    # Expired credentials: list refreshes them through requests and saves them.
    'full_collaborator': {'state': None, 'auto_sync': False,
                          'credentials': stored('fc-old-token', 'fc-refresh', expiry='2026-01-01T00:00:00Z')},
    # No access token, a future expiry and a quota project: the first API call refreshes in memory (httplib2).
    'athlete': {'state': 'athlete-fixture-state', 'auto_sync': False,
                'credentials': {'refresh_token': 'athlete-refresh', 'client_id': 'athlete-client', 'client_secret': 'athlete-secret',
                                'scopes': 'https://www.googleapis.com/auth/calendar openid', 'expiry': '2099-01-01T00:00:00Z',
                                'quota_project_id': 'assozeta-quota'}},
    # Stored without refresh_token: Credentials.from_authorized_user_info raises.
    'owner_without_association': {'state': None, 'auto_sync': True,
                                  'credentials': {'token': 'owa-token', 'client_id': 'c', 'client_secret': 's'}},
}

# --- courses and registries ----------------------------------------------------


def ev(n, title, start, end, **extra):
    return {'event_id': f'e{n:07d}-0000-4000-8000-000000000{n:03d}', 'title': title, 'start': start, 'end': end, **extra}


COURSES = {
    'alpha': {'id': next(ids), 'association': OWN, 'title': 'Corso Alfa è', 'calendar': None, 'color': '#1a73e8', 'group': None,
              'deleted': False, 'registry': [
                  ev(1, 'Lezione 1', '2026-10-05T16:00:00.000Z', '2026-10-05T17:30:00.000Z',
                     extendedProps={'instructor': {'value': 'x', 'label': 'Ines Uno'}}, className='fc-event-primary'),
                  ev(2, 'Saggio', '2026-12-20T00:00:00Z', None),
                  ev(3, 'Prova', '2026-11-02T09:00:00', 'None', extendedProps={'instructor': None}),
                  ev(4, 'Allenamento è', '2026-10-06T18:00:00+02:00', '2026-10-06T19:00:00+02:00',
                     extendedProps={'instructor': {'value': 'y'}}),
                  ev(5, 'Duplicato', '2026-10-10', '2026-10-10T10:00:00Z', extendedProps={'instructor': {'label': 7}}),
              ]},
    'beta': {'id': next(ids), 'association': OWN, 'title': 'Corso Beta', 'calendar': 'beta-old@group.calendar.google.com',
             'color': '#ffe082', 'group': None, 'deleted': False, 'registry': []},
    'gamma': {'id': next(ids), 'association': OWN, 'title': 'Corso Gamma', 'calendar': None, 'color': '#000000', 'group': None,
              'deleted': False, 'registry': [ev(6, 'Gamma', '2026-10-01T08:00:00Z', '2026-10-01T09:00:00Z')]},
    'delta': {'id': next(ids), 'association': OWN, 'title': 'Corso Delta', 'calendar': None, 'color': '#808080', 'group': None,
              'deleted': False, 'registry': None},
    'epsilon': {'id': next(ids), 'association': OWN, 'title': 'Corso Epsilon', 'calendar': None, 'color': None, 'group': None,
                'deleted': False, 'registry': []},
    'zeta': {'id': next(ids), 'association': OWN, 'title': 'Corso Zeta', 'calendar': None, 'color': '#7f7f7f', 'group': None,
             'deleted': False, 'registry': [ev(7, 'Prima', '2026-10-01T08:00:00Z', '2026-10-01T09:00:00Z'),
                                            {'event_id': 'e0000008-x', 'start': '2026-10-02T08:00:00Z', 'end': None}]},
    'grouped': {'id': next(ids), 'association': OWN, 'title': 'Corso Gruppo', 'calendar': None, 'color': '#123456', 'group': 'own_group',
                'deleted': False, 'registry': []},
    'gone': {'id': next(ids), 'association': OWN, 'title': 'Corso Eliminato', 'calendar': None, 'color': '#123456', 'group': None,
             'deleted': True, 'registry': []},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'title': 'Corso Estero', 'calendar': None, 'color': '#334455', 'group': None,
               'deleted': False, 'registry': [ev(9, 'Estero', '2026-07-01T10:00:00Z', '2026-07-01T11:00:00Z')]},
}
for row in COURSES.values():
    row['registry_id'] = next(ids) if row['registry'] is not None else None
# A second, later registry of alpha: .first() orders by primary key, so the first one is exported.
ALPHA_LATER_REGISTRY = 'ffffffff-0000-4000-8000-000000000001'
CALENDAR_IDS = {label: row['calendar'] for label, row in COURSES.items()}
USERS = {label: {'state': None, 'credentials': None, 'auto_sync': False} for label in
         ('owner', 'full_collaborator', 'restricted_collaborator', 'athlete', 'owner_without_association', 'admin')}
for label, row in SEED_USERS.items():
    USERS[label] = {'state': row['state'], 'credentials': json.dumps(row['credentials']), 'auto_sync': row['auto_sync']}
cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, google=(), users=None, courses=None, audit=(),
        html=None, state_for=None, session=None):
    """users: {label: {field: value}} after the case ('$state' = the state answered by this or an earlier
    config case, a dict with '$expiry' = credentials JSON whose expiry is checked by kind)."""
    case = {'name': f'google-calendar-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'google': list(google), 'users': users or {}, 'courses': courses or {},
            'audit': list(audit)}
    if html is not None:
        case['html'] = html
    if state_for is not None:
        # A config GET: the state is also stored in a new Django session (cookie and cache entry).
        case['state_for'], case['session'] = state_for, True
    if session:
        # keep: the session stays in the cache for a later case; cookie_from: send that case's session cookie
        # (reused: the same key is saved again); cookie: a literal Cookie header (a new session is created).
        case['session_cookie'] = session
    cases.append(case)


# --- outbound requests -----------------------------------------------------------

def reply(status=200, body=None, *, text=None, reason=None):
    answer = {'status': status}
    if text is not None:
        answer['text'] = text
    elif status != 204:
        answer['json'] = {} if body is None else body
    if reason is not None:
        answer['reason'] = reason
    return answer


def api(method, path, token, answer, *, body=None, quota=None):
    data = '' if body is None else json.dumps(body)
    headers = [['accept', '*/*' if method == 'DELETE' else 'application/json'], ['accept-encoding', 'gzip, deflate'],
               ['user-agent', '(gzip)'], ['x-goog-api-client', GDCL]]
    if body is not None:
        headers.append(['content-type', 'application/json'])
    headers += [['content-length', str(len(data.encode()))], ['authorization', 'Bearer ' + token]]
    if quota:
        headers.append(['x-goog-user-project', quota])
    return {'request': {'method': method, 'path': path, 'headers': headers, 'body': data}, 'reply': answer}


def refresh_body(info):
    body = {'grant_type': 'refresh_token', 'client_id': info['client_id'], 'client_secret': info['client_secret'],
            'refresh_token': info['refresh_token']}
    scopes = info.get('scopes')
    if isinstance(scopes, str):
        scopes = scopes.split(' ')
    if scopes:
        body['scope'] = ' '.join(scopes)
    return urlencode(body)


def refresh(info, answer, *, transport='requests'):
    """google.oauth2.reauth.refresh_grant through google.auth.transport.requests (get_google_credentials)
    or google_auth_httplib2 (AuthorizedHttp before_request / 401 retry)."""
    data = refresh_body(info)
    if transport == 'requests':
        headers = [['User-Agent', REQUESTS_UA], ['Accept-Encoding', 'gzip, deflate'], ['Accept', '*/*'], ['Connection', 'keep-alive'],
                   ['Content-Type', 'application/x-www-form-urlencoded'], ['x-goog-api-client', AUTH_METRIC],
                   ['Content-Length', str(len(data))]]
    else:
        headers = [['Content-Length', str(len(data))], ['content-type', 'application/x-www-form-urlencoded'],
                   ['x-goog-api-client', AUTH_METRIC], ['user-agent', HTTPLIB2_UA], ['accept-encoding', 'gzip, deflate']]
    return {'request': {'method': 'POST', 'path': '/token', 'headers': headers, 'body': data}, 'reply': answer}


def exchange(code, answer):
    data = urlencode({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT})
    basic = base64.b64encode(f"{CLIENT['client_id']}:{CLIENT['client_secret']}".encode('latin1')).decode()
    headers = [['User-Agent', REQUESTS_UA], ['Accept-Encoding', 'gzip, deflate'], ['Accept', 'application/json'],
               ['Connection', 'keep-alive'], ['Content-Type', 'application/x-www-form-urlencoded'], ['Content-Length', str(len(data))],
               ['Authorization', 'Basic ' + basic]]
    return {'request': {'method': 'POST', 'path': '/token', 'headers': headers, 'body': data}, 'reply': answer}


def revoke(token, answer=None):
    path = '/revoke' + ('' if token is None else '?' + urlencode({'token': token}))
    headers = [['User-Agent', REQUESTS_UA], ['Accept-Encoding', 'gzip, deflate'], ['Accept', '*/*'], ['Connection', 'keep-alive'],
               ['content-type', 'application/x-www-form-urlencoded'], ['Content-Length', '0']]
    return {'request': {'method': 'POST', 'path': path, 'headers': headers, 'body': ''}, 'reply': answer or reply(200, {})}


def token_answer(access, expires_in=3599, refresh_token=None, scope=SCOPES, **extra):
    body = {'access_token': access, 'expires_in': expires_in}
    if refresh_token is not None:
        body['refresh_token'] = refresh_token
    if scope is not None:
        body['scope'] = ' '.join(scope)
    body.update({'token_type': 'Bearer', **extra})
    return reply(200, body)


def google_error(status, message, reason, domain='global'):
    return reply(status, {'error': {'code': status, 'message': message, 'errors': [{'domain': domain, 'reason': reason, 'message': message}]}})


# --- expected state ---------------------------------------------------------------

def creds_after_callback(access, refresh_token, expires_in=3599):
    return {'$json': stored(access, refresh_token, expiry=f'$callback:{expires_in}')}


def creds_after_refresh(info, access, expires_in=3599, refresh_token=None):
    """to_json of the refreshed credentials (from_authorized_user_info defaults: Google token_uri,
    universe_domain googleapis.com, account '')."""
    scopes = info.get('scopes')
    if isinstance(scopes, str):
        scopes = scopes.split(' ')
    data = {'token': access, 'refresh_token': refresh_token or info['refresh_token'], 'token_uri': 'https://oauth2.googleapis.com/token',
            'client_id': info['client_id'], 'client_secret': info['client_secret'], 'scopes': scopes, 'rapt_token': info.get('rapt_token'),
            'universe_domain': info.get('universe_domain', 'googleapis.com'), 'account': info.get('account', ''),
            'expiry': None if expires_in is None else f'$refresh:{expires_in}'}
    return {'$json': {k: v for k, v in data.items() if v is not None}}


# AuditLogIndex for a User entry: user_resolver (the user's own association), else the actor's association.
ASSOCIATION_OF = {'owner': OWN, 'admin': IDS['admin_association']}


def user_audit(label, fields, actor, *, intermediate=None):
    if label in ASSOCIATION_OF:
        index = [ASSOCIATION_OF[label], 'user.sport_association']
    elif actor in ASSOCIATION_OF:
        index = [ASSOCIATION_OF[actor], 'actor.sport_association']
    else:
        index = None
    return {'model': 'user', 'label': label, 'fields': fields, 'actor': actor, 'index': index, 'intermediate': intermediate}


def course_audit(label, old, new, actor):
    return {'model': 'course', 'label': label, 'changes': {'google_calendar_id': [str(old), str(new)]}, 'actor': actor,
            'index': [COURSES[label]['association'], 'direct']}



# --- shared expectations ----------------------------------------------------------
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED_ATHLETE = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': FULL}
OWNER_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
method_not_allowed = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'], 'parses': PARSES}
missing = lambda key: {'detail': f'Missing permission: {key}'}
CHECK, CONFIG, CALLBACK, LIST = '/google/check', '/google/calendar/config', '/google/oauth2callback', '/google/calendar/list'
EXPORT = lambda label: f"/google/calendar/{COURSES[label]['id'] if label in COURSES else label}/export"
_page = lambda text: ('\n            <html>\n                <head>\n                    <script type="text/javascript">\n'
                      '                        window.close();\n                    </script>            \n                </head>\n'
                      f'                <body>\n                    <p>{text}</p>\n                </body>\n            </html>\n        ')
CANCELLED_PAGE = _page('Richiesta cancellata. Per favore chiudi la pagina.')
AUTHORIZED_PAGE = _page('Autorizzato. Per favore chiudi la pagina.')
AUTH_URL = (CLIENT['auth_uri'] + '?' + urlencode({'response_type': 'code', 'client_id': CLIENT['client_id'], 'redirect_uri': REDIRECT,
                                                  'scope': ' '.join(SCOPES)}) + '&state={state}&access_type=offline&include_granted_scopes=true')
NO_USER = {'msg': 'No user found for the integration flow.'}
DELETED = {'msg': 'deleted'}


def set_user(label, **fields):
    USERS[label].update(fields)
    return {label: dict(fields)}


# --- google/check ------------------------------------------------------------------
add('check-options', 'owner', CHECK, 200, options('Google Check'), method='OPTIONS')
add('check-wrong-method', 'owner', CHECK, 405, method_not_allowed('POST'), method='POST')
add('check-anonymous', 'anonymous', CHECK, 401, ANONYMOUS)
add('check-admin-without-session', 'admin', CHECK, 403, ADMIN)
add('check-restricted-collaborator-denied', 'restricted_collaborator', CHECK, 403, missing('other.settings.read'))
add('check-owner-scoped-athlete-denied', 'owner', CHECK, 403, SCOPED_ATHLETE, headers=OWNER_ATHLETE)
add('check-owner-disconnected', 'owner', CHECK, 200, {'google_sync_enabled': False})
add('check-full-collaborator-own-credentials', 'full_collaborator', CHECK, 200, {'google_sync_enabled': True})
add('check-owner-as-collaborator-session', 'owner', CHECK, 200, {'google_sync_enabled': True}, headers=OWNER_FULL)
add('check-athlete', 'athlete', CHECK, 200, {'google_sync_enabled': True})
add('check-owner-without-association', 'owner_without_association', CHECK, 200, {'google_sync_enabled': True})
add('check-admin-impersonating-owner', 'admin', CHECK, 200, {'google_sync_enabled': False}, headers=ADMIN_OWNER)

# --- google/calendar/list and config without credentials -----------------------------
add('list-options', 'owner', LIST, 200, options('Google Calendar List'), method='OPTIONS')
add('list-wrong-method', 'owner', LIST, 405, method_not_allowed('POST'), method='POST')
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('list-restricted-collaborator-denied', 'restricted_collaborator', LIST, 403, missing('association.calendar.read'))
add('list-owner-disconnected-server-error', 'owner', LIST, 500, SERVER)
add('list-without-refresh-token-server-error', 'owner_without_association', LIST, 500, SERVER)
add('config-options', 'owner', CONFIG, 200, options('Google Calendar Config'), method='OPTIONS')
add('config-wrong-method', 'owner', CONFIG, 405, method_not_allowed('POST'), method='POST')
add('config-anonymous', 'anonymous', CONFIG, 401, ANONYMOUS)
add('config-get-restricted-collaborator-denied', 'restricted_collaborator', CONFIG, 403, missing('other.settings.read'))
add('config-delete-restricted-collaborator-denied', 'restricted_collaborator', CONFIG, 403, missing('other.settings.update'),
    method='DELETE')
add('config-delete-owner-disconnected-server-error', 'owner', CONFIG, 500, SERVER, method='DELETE')

# --- refresh on load (full collaborator) -----------------------------------------------
FC_INFO = SEED_USERS['full_collaborator']['credentials']
add('list-refresh-rejected-server-error', 'full_collaborator', LIST, 500, SERVER,
    google=[refresh(FC_INFO, reply(400, {'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'}))])
FC_REFRESHED = creds_after_refresh(FC_INFO, 'fc-token-2')
CAL_A = {'kind': 'calendar#calendarListEntry', 'id': 'primary-fc@example.test', 'summary': 'Agenda è', 'accessRole': 'owner',
         'backgroundColor': '#9fe1e7', 'primary': True, 'defaultReminders': [{'method': 'popup', 'minutes': 10}]}
CAL_B = {'kind': 'calendar#calendarListEntry', 'id': 'it.italian#holiday@group.v.calendar.google.com', 'summary': 'Festività',
         'accessRole': 'reader', 'conferenceProperties': {'allowedConferenceSolutionTypes': ['hangoutsMeet']}, 'hidden': False,
         'selected': None, 'colorId': '8', 'nested': {'n': 1.5, 'big': 12345678901234567890}}
add('list-full-collaborator-refreshed-and-saved', 'full_collaborator', LIST, 200, {'data': {'calendars': [CAL_A, CAL_B]}},
    # A retryable token endpoint answer (temporarily_unavailable) is retried after the backoff.
    google=[refresh(FC_INFO, reply(400, {'error': 'temporarily_unavailable'})), refresh(FC_INFO, token_answer('fc-token-2')),
            api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'fc-token-2',
                reply(200, {'kind': 'calendar#calendarList', 'etag': '"p1"', 'nextPageToken': 'page two/+', 'items': [CAL_A]})),
            api('GET', '/calendar/v3/users/me/calendarList?pageToken=page+two%2F%2B&alt=json', 'fc-token-2',
                reply(200, {'kind': 'calendar#calendarList', 'etag': '"p2"', 'items': [CAL_B], 'nextSyncToken': 'sync'}))],
    users=set_user('full_collaborator', credentials=FC_REFRESHED),
    audit=[user_audit('full_collaborator', ['integration_google_credentials'], 'full_collaborator')])
add('list-athlete-in-memory-refresh', 'athlete', LIST, 200, {'data': {'calendars': []}},
    google=[refresh(SEED_USERS['athlete']['credentials'], token_answer('athlete-token', scope=None), transport='httplib2'),
            api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'athlete-token', reply(200, {'items': []}), quota='assozeta-quota')])

# --- config GET and the callback -------------------------------------------------------
add('config-get-owner', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='owner',
    users=set_user('owner', state='$state'), audit=[user_audit('owner', ['integration_google_state'], 'owner')])
# "Connetti a Google" again in the same browser: the request carries the sessionid cookie set by the first
# config GET. SessionMiddleware loads that cache session, the view stores the new state in it and the
# session is saved again under the same key (cache.set, SESSION_COOKIE_AGE). A key the cache does not hold
# (expired, or shorter than 8 characters) loads an empty session and a new key is created.
add('config-get-owner-keeps-session', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='owner',
    session={'keep': True}, users=set_user('owner', state='$state'), audit=[user_audit('owner', ['integration_google_state'], 'owner')])
add('config-get-owner-existing-session', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='owner',
    session={'cookie_from': 'google-calendar-config-get-owner-keeps-session', 'reused': True},
    users=set_user('owner', state='$state'), audit=[user_audit('owner', ['integration_google_state'], 'owner')])
add('config-get-owner-expired-session-cookie', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='owner',
    session={'cookie': 'sessionid=0123456789abcdefghijklmnopqrstuv'},
    users=set_user('owner', state='$state'), audit=[user_audit('owner', ['integration_google_state'], 'owner')])
add('config-get-owner-short-session-cookie', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='owner',
    session={'cookie': 'theme=dark; sessionid=abc'},
    users=set_user('owner', state='$state'), audit=[user_audit('owner', ['integration_google_state'], 'owner')])
add('config-get-collaborator-keeps-session', 'full_collaborator', CONFIG, 200, {'authorization_url': AUTH_URL},
    state_for='full_collaborator', session={'keep': True}, users=set_user('full_collaborator', state='$state'),
    audit=[user_audit('full_collaborator', ['integration_google_state'], 'full_collaborator')])
add('config-get-collaborator-existing-session', 'full_collaborator', CONFIG, 200, {'authorization_url': AUTH_URL},
    state_for='full_collaborator', session={'cookie_from': 'google-calendar-config-get-collaborator-keeps-session', 'reused': True},
    users=set_user('full_collaborator', state='$state'),
    audit=[user_audit('full_collaborator', ['integration_google_state'], 'full_collaborator')])
add('callback-options', 'anonymous', CALLBACK, 200, options('Google Oauth2Callback'), method='OPTIONS')
add('callback-wrong-method', 'anonymous', CALLBACK, 405, method_not_allowed('POST'), method='POST')
add('callback-restricted-collaborator-denied', 'restricted_collaborator', CALLBACK + '?error=access_denied', 403,
    missing('other.settings.update'))
add('callback-cancelled', 'anonymous', CALLBACK + '?error=access_denied&state={state:owner}', 200, None, html=CANCELLED_PAGE)
add('callback-unknown-state', 'anonymous', CALLBACK + '?state=not-a-known-state&code=4%2Fcode', 400, NO_USER)
add('callback-missing-state-server-error', 'anonymous', CALLBACK + '?code=4%2Fcode', 500, SERVER)
add('callback-missing-code-server-error', 'anonymous', CALLBACK + '?state={state:owner}', 500, SERVER)
add('callback-token-rejected-server-error', 'anonymous', CALLBACK + '?state={state:owner}&code=4%2Frejected', 500, SERVER,
    google=[exchange('4/rejected', reply(400, {'error': 'invalid_grant', 'error_description': 'Bad Request'}))])
add('callback-scope-changed-server-error', 'anonymous', CALLBACK + '?state={state:owner}&code=4%2Fscope', 500, SERVER,
    google=[exchange('4/scope', token_answer('owner-token-x', refresh_token='owner-refresh-x',
                                             scope=SCOPES + ['https://www.googleapis.com/auth/drive']))])
add('callback-owner-authorized', 'anonymous', CALLBACK + '?state={state:owner}&code=4%2F0A%C3%A8b+c&scope=ignored', 200, None,
    html=AUTHORIZED_PAGE,
    google=[exchange('4/0Aèb c', token_answer('owner-token-1', refresh_token='owner-refresh-1', scope=list(reversed(SCOPES)),
                                              id_token='header.payload.signature'))],
    users=set_user('owner', credentials=creds_after_callback('owner-token-1', 'owner-refresh-1'), auto_sync=True),
    audit=[user_audit('owner', ['integration_google_credentials', 'integration_google_auto_sync'], 'anonymous')])
add('check-owner-connected', 'owner', CHECK, 200, {'google_sync_enabled': True})
add('list-owner-single-page', 'owner', LIST, 200, {'data': {'calendars': [CAL_A]}},
    google=[api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-1', reply(200, {'items': [CAL_A], 'nextPageToken': ''}))])
add('list-owner-unauthorized-refresh-retry', 'owner', LIST, 200, {'data': {'calendars': [CAL_B]}},
    google=[api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-1',
                google_error(401, 'Request had invalid authentication credentials.', 'authError')),
            refresh(json.loads(json.dumps(stored('owner-token-1', 'owner-refresh-1', expiry='x'))), token_answer('owner-token-mem'),
                    transport='httplib2'),
            api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-mem', reply(200, {'items': [CAL_B]}))])
add('list-api-error-server-error', 'owner', LIST, 500, SERVER,
    google=[api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-1', google_error(503, 'Backend Error', 'backendError'))])
add('list-without-items-server-error', 'owner', LIST, 500, SERVER,
    google=[api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-1', reply(200, {'kind': 'calendar#calendarList'}))])
add('list-admin-impersonating-owner', 'admin', LIST, 200, {'data': {'calendars': []}}, headers=ADMIN_OWNER,
    google=[api('GET', '/calendar/v3/users/me/calendarList?alt=json', 'owner-token-1', reply(200, {'items': []}))])

# --- export ------------------------------------------------------------------------
OWNER_TOKEN = 'owner-token-1'


def fg(color):
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return '#000000' if ((r * 299) + (g * 587) + (b * 114)) / 1000 >= 128 else '#ffffff'


def parse(value):
    return datetime.fromisoformat(value)


def rome(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ROME)


def event_body(course, calendar, event):
    event_id = event['event_id'].replace('-', '')
    description = ''
    props = event.get('extendedProps')
    if props is not None and isinstance(props.get('instructor'), dict) and 'label' in props['instructor']:
        description = f"Istruttore: {props['instructor']['label']}"
    start = parse(event['start'])
    if event['end'] is not None and event['end'] != 'None':
        end, all_day = parse(event['end']), False
    else:
        end, all_day = start.replace(hour=23, minute=59), True
    start, end = rome(start), rome(end)
    body = {'extendedProperties': {'private': {'course_id': course['id'], 'event_id': event_id}}, 'calendar_id': calendar,
            'summary': event['title'], 'description': description,
            'reminders': {'useDefault': False, 'overrides': [{'method': 'email', 'minutes': 1440}, {'method': 'popup', 'minutes': 10}]}}
    if all_day:
        body['start'] = {'date': start.strftime('%Y-%m-%d'), 'timeZone': 'Europe/Rome'}
        body['end'] = {'date': end.strftime('%Y-%m-%d'), 'timeZone': 'Europe/Rome'}
    else:
        body['start'] = {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Europe/Rome'}
        body['end'] = {'dateTime': end.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Europe/Rome'}
    return body


def created(label, calendar):
    return reply(200, {'kind': 'calendar#calendar', 'etag': '"e"', 'id': calendar, 'summary': COURSES[label]['title'],
                       'timeZone': 'Europe/Rome', 'conferenceProperties': {'allowedConferenceSolutionTypes': ['hangoutsMeet']}})


def export_head(label, token, *, old_answer=None, insert_answer):
    course, steps = COURSES[label], []
    old = CALENDAR_IDS[label]
    if old is not None:
        steps.append(api('DELETE', f'/calendar/v3/calendars/{quote(old, safe="")}', token, old_answer or reply(204)))
    steps.append(api('POST', '/calendar/v3/calendars?alt=json', token, insert_answer,
                     body={'description': DESCRIPTION, 'summary': course['title'], 'timeZone': 'Europe/Rome'}))
    return steps


def export_list(label, token, calendar, *, delete_answer=None):
    color = COURSES[label]['color']
    return [api('DELETE', f'/calendar/v3/users/me/calendarList/{quote(calendar, safe="")}', token,
                delete_answer or google_error(404, 'Not Found', 'notFound')),
            api('POST', '/calendar/v3/users/me/calendarList?alt=json', token,
                reply(200, {'kind': 'calendar#calendarListEntry', 'id': calendar}),
                body={'id': calendar, 'backgroundColor': color, 'foregroundColor': fg(color)})]


def export_events(label, token, calendar, answers=None, events=None):
    course = COURSES[label]
    events = course['registry'] if events is None else events
    answers = answers or {}
    return [api('POST', f'/calendar/v3/calendars/{quote(calendar, safe="")}/events?alt=json', token,
                answers.get(i, reply(200, {'kind': 'calendar#event', 'id': e['event_id'].replace('-', ''), 'status': 'confirmed'})),
                body=event_body(course, calendar, e)) for i, e in enumerate(events)]


def export(name, actor, label, token, calendar, *, owner_saves=True, headers=None, old_answer=None, list_delete=None, answers=None,
           user_label='owner'):
    old = CALENDAR_IDS[label]
    google = (export_head(label, token, old_answer=old_answer, insert_answer=created(label, calendar))
              + export_list(label, token, calendar, delete_answer=list_delete) + export_events(label, token, calendar, answers))
    courses, audit = {}, []
    if owner_saves:
        courses = {label: calendar}
        audit = [course_audit(label, old, calendar, actor)]
        CALENDAR_IDS[label] = calendar
    add(name, actor, EXPORT(label), 200, {'msg': 'exported.', 'google_calendar_id': CALENDAR_IDS[label]}, method='POST', headers=headers,
        google=google, courses=courses, audit=audit)


add('export-options', 'owner', EXPORT('alpha'), 200, options('Google Calendar Export Course'), method='OPTIONS')
add('export-wrong-method', 'owner', EXPORT('alpha'), 405, method_not_allowed('GET'))
add('export-anonymous', 'anonymous', EXPORT('alpha'), 401, ANONYMOUS, method='POST')
add('export-admin-without-session', 'admin', EXPORT('alpha'), 403, ADMIN, method='POST')
add('export-restricted-collaborator-denied', 'restricted_collaborator', EXPORT('alpha'), 403, missing('association.calendar.read'),
    method='POST')
add('export-owner-scoped-athlete-denied', 'owner', EXPORT('alpha'), 403, SCOPED_ATHLETE, method='POST', headers=OWNER_ATHLETE)
add('export-missing-course', 'owner', EXPORT(IDS['missing']), 404, {'msg': 'Course not found.'}, method='POST')
add('export-malformed-course-server-error', 'owner', EXPORT('not-a-uuid'), 500, SERVER, method='POST')
add('export-deleted-course', 'owner', EXPORT('gone'), 404, {'msg': 'Course not found.'}, method='POST')
add('export-other-group-course', 'owner', EXPORT('grouped'), 404, {'msg': 'Course not found.'}, method='POST',
    headers={'X-Group-Id': IDS['foreign_group']})
add('export-disconnected-server-error', 'owner_without_association', EXPORT('alpha'), 500, SERVER, method='POST')
export('export-alpha-first', 'owner', 'alpha', OWNER_TOKEN, 'alpha-1@group.calendar.google.com',
       answers={4: google_error(409, 'The requested identifier already exists.', 'duplicate')})
export('export-alpha-replaces-calendar', 'owner', 'alpha', OWNER_TOKEN, 'alpha-2@group.calendar.google.com',
       list_delete=reply(204))
export('export-beta-old-calendar-missing', 'owner', 'beta', OWNER_TOKEN, 'beta 2/x@group.calendar.google.com',
       old_answer=google_error(404, 'Not Found', 'notFound'))
export('export-full-collaborator-keeps-course-id', 'full_collaborator', 'alpha', 'fc-token-2', 'alpha-fc@group.calendar.google.com',
       owner_saves=False)
export('export-admin-impersonating-owner', 'admin', 'beta', OWNER_TOKEN, 'beta-3@group.calendar.google.com', headers=ADMIN_OWNER)
export('export-course-of-another-association', 'owner', 'abroad', OWNER_TOKEN, 'abroad-1@group.calendar.google.com')
add('export-quota-exceeded', 'owner', EXPORT('gamma'), 403, {'msg': 'quotaExceeded'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=google_error(403, 'Calendar usage limits exceeded.', 'quotaExceeded',
                                                                       domain='usageLimits')))
add('export-quota-in-message', 'owner', EXPORT('gamma'), 403, {'msg': 'quotaExceeded'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=reply(403, {'error': {'code': 403, 'message': 'quotaExceeded for user'}})))
add('export-insert-error', 'owner', EXPORT('gamma'), 400, {'msg': 'Error creating calendar.'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=google_error(500, 'Backend Error', 'backendError')))
add('export-insert-without-id', 'owner', EXPORT('gamma'), 400, {'msg': 'Error creating calendar.'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=reply(200, {'kind': 'calendar#calendar'})))
add('export-insert-not-json', 'owner', EXPORT('gamma'), 400, {'msg': 'Error creating calendar.'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=reply(200, text='quota?')))
add('export-insert-error-quota-reason-phrase', 'owner', EXPORT('gamma'), 403, {'msg': 'quotaExceeded'}, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=reply(429, text='<html>slow down</html>', reason='quotaExceeded')))
# No registry: the calendar, the course and the list entry are written before the 404.
add('export-without-registry', 'owner', EXPORT('delta'), 404, {'msg': 'Attendance Registry not found.'}, method='POST',
    google=export_head('delta', OWNER_TOKEN, insert_answer=created('delta', 'delta-1@group.calendar.google.com'))
    + export_list('delta', OWNER_TOKEN, 'delta-1@group.calendar.google.com'),
    courses={'delta': 'delta-1@group.calendar.google.com'}, audit=[course_audit('delta', None, 'delta-1@group.calendar.google.com', 'owner')])
CALENDAR_IDS['delta'] = 'delta-1@group.calendar.google.com'
# No colour: foreground_color(None) raises after the course save.
add('export-without-colour-server-error', 'owner', EXPORT('epsilon'), 500, SERVER, method='POST',
    google=export_head('epsilon', OWNER_TOKEN, insert_answer=created('epsilon', 'epsilon-1@group.calendar.google.com')),
    courses={'epsilon': 'epsilon-1@group.calendar.google.com'},
    audit=[course_audit('epsilon', None, 'epsilon-1@group.calendar.google.com', 'owner')])
CALENDAR_IDS['epsilon'] = 'epsilon-1@group.calendar.google.com'
# An event without a title raises after the earlier events were inserted.
add('export-event-without-title-server-error', 'owner', EXPORT('zeta'), 500, SERVER, method='POST',
    google=export_head('zeta', OWNER_TOKEN, insert_answer=created('zeta', 'zeta-1@group.calendar.google.com'))
    + export_list('zeta', OWNER_TOKEN, 'zeta-1@group.calendar.google.com')
    + export_events('zeta', OWNER_TOKEN, 'zeta-1@group.calendar.google.com', events=COURSES['zeta']['registry'][:1]),
    courses={'zeta': 'zeta-1@group.calendar.google.com'}, audit=[course_audit('zeta', None, 'zeta-1@group.calendar.google.com', 'owner')])
CALENDAR_IDS['zeta'] = 'zeta-1@group.calendar.google.com'
add('export-list-insert-error-server-error', 'owner', EXPORT('gamma'), 500, SERVER, method='POST',
    google=export_head('gamma', OWNER_TOKEN, insert_answer=created('gamma', 'gamma-1@group.calendar.google.com'))
    + [api('DELETE', '/calendar/v3/users/me/calendarList/gamma-1%40group.calendar.google.com', OWNER_TOKEN, reply(204)),
       api('POST', '/calendar/v3/users/me/calendarList?alt=json', OWNER_TOKEN, google_error(403, 'Forbidden', 'forbidden'),
           body={'id': 'gamma-1@group.calendar.google.com', 'backgroundColor': '#000000', 'foregroundColor': '#ffffff'})],
    courses={'gamma': 'gamma-1@group.calendar.google.com'},
    audit=[course_audit('gamma', None, 'gamma-1@group.calendar.google.com', 'owner')])
CALENDAR_IDS['gamma'] = 'gamma-1@group.calendar.google.com'

# --- collaborator flow and a second owner authorization --------------------------------
add('config-get-full-collaborator', 'full_collaborator', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='full_collaborator',
    users=set_user('full_collaborator', state='$state'),
    audit=[user_audit('full_collaborator', ['integration_google_state'], 'full_collaborator')])
add('callback-collaborator-authorized', 'anonymous', CALLBACK + '?state={state:full_collaborator}&code=fc-code', 200, None,
    html=AUTHORIZED_PAGE, google=[exchange('fc-code', token_answer('fc-token-3', expires_in='3599', refresh_token='fc-refresh-3', scope=None))],
    users=set_user('full_collaborator', credentials=creds_after_callback('fc-token-3', 'fc-refresh-3')),
    audit=[user_audit('full_collaborator', ['integration_google_credentials'], 'anonymous')])
add('config-get-owner-as-collaborator-session', 'owner', CONFIG, 200, {'authorization_url': AUTH_URL}, headers=OWNER_FULL,
    state_for='full_collaborator', users=set_user('full_collaborator', state='$state'),
    audit=[user_audit('full_collaborator', ['integration_google_state'], 'owner')])
add('config-get-admin-impersonating-owner', 'admin', CONFIG, 200, {'authorization_url': AUTH_URL}, headers=ADMIN_OWNER,
    state_for='owner', users=set_user('owner', state='$state'),
    audit=[user_audit('owner', ['integration_google_state'], 'admin')])
add('callback-owner-short-lived', 'anonymous', CALLBACK + '?state={state:owner}&code=short', 200, None, html=AUTHORIZED_PAGE,
    google=[exchange('short', token_answer('owner-token-2', expires_in=100, refresh_token='owner-refresh-2'))],
    users=set_user('owner', credentials=creds_after_callback('owner-token-2', 'owner-refresh-2', 100)),
    audit=[user_audit('owner', ['integration_google_credentials'], 'anonymous')])
# The stored credentials expire within the refresh threshold: DELETE refreshes and saves them, revokes, then clears.
OWNER_SHORT = stored('owner-token-2', 'owner-refresh-2', expiry='x')
add('config-delete-owner-refreshes-then-revokes', 'owner', CONFIG, 200, DELETED, method='DELETE',
    google=[refresh(OWNER_SHORT, token_answer('owner-token-3', expires_in=3599.75, refresh_token='owner-refresh-3')),
            revoke('owner-token-3', reply(400, {'error': 'invalid_token'}))],
    users=set_user('owner', state=None, credentials=None, auto_sync=False),
    audit=[user_audit('owner', ['integration_google_credentials'], 'owner',
                      intermediate=creds_after_refresh(OWNER_SHORT, 'owner-token-3', 3599.75, 'owner-refresh-3')),
           user_audit('owner', ['integration_google_state', 'integration_google_credentials', 'integration_google_auto_sync'], 'owner')])
add('check-owner-after-delete', 'owner', CHECK, 200, {'google_sync_enabled': False})
add('config-delete-collaborator-session', 'owner', CONFIG, 200, DELETED, method='DELETE', headers=OWNER_FULL,
    google=[revoke('fc-token-3')], users=set_user('full_collaborator', state=None, credentials=None),
    audit=[user_audit('full_collaborator', ['integration_google_state', 'integration_google_credentials'], 'owner')])
add('config-delete-athlete-token-none', 'athlete', CONFIG, 200, DELETED, method='DELETE', google=[revoke(None)],
    users=set_user('athlete', state=None, credentials=None),
    audit=[user_audit('athlete', ['integration_google_state', 'integration_google_credentials'], 'athlete')])
add('config-get-athlete', 'athlete', CONFIG, 200, {'authorization_url': AUTH_URL}, state_for='athlete',
    users=set_user('athlete', state='$state'), audit=[user_audit('athlete', ['integration_google_state'], 'athlete')])
add('callback-athlete-without-refresh-token', 'anonymous', CALLBACK + '?state={state:athlete}&code=a', 200, None, html=AUTHORIZED_PAGE,
    google=[exchange('a', token_answer('athlete-token-2', expires_in=3599))],
    users=set_user('athlete', credentials=creds_after_callback('athlete-token-2', None), auto_sync=True),
    audit=[user_audit('athlete', ['integration_google_credentials', 'integration_google_auto_sync'], 'anonymous')])
add('list-athlete-stored-without-refresh-token-server-error', 'athlete', LIST, 500, SERVER)

assert len({c['name'] for c in cases}) == len(cases)
seed_courses = {label: {k: v for k, v in row.items()} for label, row in COURSES.items()}
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'google/check, google/calendar/config, google/oauth2callback, google/calendar/list, '
                                  'google/calendar/<course_id>/export', 'ids': IDS,
    'client': {'web': CLIENT}, 'redirect': REDIRECT, 'seed_users': SEED_USERS, 'courses': seed_courses,
    'alpha_later_registry': ALPHA_LATER_REGISTRY, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
