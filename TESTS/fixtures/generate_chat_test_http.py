"""Author the chat/test/ HTTP sequence (chat-test.md).

`chat_test` is a plain Django function view under `login_required` that
renders 'chat/test.html'. No such template exists in BE (nor in its history),
so an authenticated request raises TemplateDoesNotExist: the stable 500 page.
`login_required` reads request.user from AuthenticationMiddleware, i.e. the
cache session (sessionid cookie); a Bearer token does not authenticate it.
Anonymous requests (and sessions without an active authenticated user) are
redirected with 302 to settings.LOGIN_URL ('/accounts/login/', the default)
with next=request.get_full_path(), quoted by QueryDict.urlencode(safe='/').
Every method reaches the view; CsrfViewMiddleware runs first for unsafe
methods (Go refuses the failing ones, see chat-test.md, so those are not
authored here). AuditlogMiddleware reads the session for every request, so
every answer varies on Cookie, then CorsMiddleware adds 'origin' and
LocaleMiddleware Accept-Language. A
supplied cookie whose session is empty is deleted. The path without its
trailing slash is CommonMiddleware's APPEND_SLASH 301.
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('chat_test_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
LOGIN = '/accounts/login/?next='
VARY = 'Cookie, origin, Accept-Language'
DELETE = 'sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax'
# Cache sessions the adapter seeds (fixed keys).
SESSIONS = {
    'owner': 'chattestowner0000000000000000001',
    'inactive': 'chattestinactive0000000000000001',
    'state': 'chattestoauthstate00000000000001',
    'missing': 'chattestmissing00000000000000001',
}
CSRF = 'chattestcsrfsecret00000000000001'
cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, location=None, cookie=None):
    cases.append({'name': f'chat-test-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'location': location, 'set_cookie': cookie, 'vary': VARY,
                  'effect': None})


def session(label, *, csrf=False):
    value = f'sessionid={SESSIONS[label]}'
    return {'Cookie': value + (f'; csrftoken={CSRF}' if csrf else ''), **({'X-CSRFToken': CSRF} if csrf else {})}


redirect = LOGIN + '/chat/test/'
# Anonymous: every safe method is redirected to the login page.
add('anonymous-get', 'anonymous', '/chat/test/', 302, '<empty>', location=redirect)
add('anonymous-head', 'anonymous', '/chat/test/', 302, '<empty>', method='HEAD', location=redirect)
add('anonymous-options', 'anonymous', '/chat/test/', 302, '<empty>', method='OPTIONS', location=redirect)
# next is get_full_path() (raw query kept) quoted with safe='/'.
add('anonymous-query', 'anonymous', '/chat/test/?a=1&b=%2F&c=%C3%A8&d=x+y', 302, '<empty>',
    location=LOGIN + '/chat/test/%3Fa%3D1%26b%3D%252F%26c%3D%25C3%25A8%26d%3Dx%2By')
add('anonymous-empty-query', 'anonymous', '/chat/test/?', 302, '<empty>', location=redirect)
# A Bearer token is not a session: still anonymous for login_required.
for actor in ('owner', 'full_collaborator', 'athlete', 'admin'):
    add(f'bearer-{actor}', actor, '/chat/test/', 302, '<empty>', location=redirect)
# Unsafe methods with a matching CSRF cookie and header pass the middleware.
for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
    add(f'anonymous-{method.lower()}-csrf', 'anonymous', '/chat/test/', 302, '<empty>', method=method,
        headers={'Cookie': f'csrftoken={CSRF}', 'X-CSRFToken': CSRF}, location=redirect)
# Sessions: an active authenticated owner reaches render() and its missing template.
add('session-owner', 'anonymous', '/chat/test/', 500, SERVER, headers=session('owner'))
add('session-owner-head', 'anonymous', '/chat/test/', 500, SERVER, method='HEAD', headers=session('owner'))
add('session-owner-bearer', 'owner', '/chat/test/', 500, SERVER, headers=session('owner'))
add('session-owner-post-csrf', 'anonymous', '/chat/test/', 500, SERVER, method='POST', headers=session('owner', csrf=True))
# An inactive user's session is anonymous (ModelBackend.get_user) and stays.
add('session-inactive', 'anonymous', '/chat/test/', 302, '<empty>', headers=session('inactive'), location=redirect)
# A session without an authenticated user stays and is anonymous.
add('session-without-user', 'anonymous', '/chat/test/', 302, '<empty>', headers=session('state'), location=redirect)
# A missing or too short session key is an empty session: its cookie is deleted.
add('session-missing', 'anonymous', '/chat/test/', 302, '<empty>', headers=session('missing'), location=redirect, cookie=DELETE)
add('session-short-key', 'anonymous', '/chat/test/', 302, '<empty>', headers={'Cookie': 'sessionid=short'},
    location=redirect, cookie=DELETE)
# APPEND_SLASH (DEBUG off: every method), query kept.
add('append-slash', 'anonymous', '/chat/test', 301, '<empty>', location='/chat/test/')
add('append-slash-query', 'anonymous', '/chat/test?a=1&b=%2F', 301, '<empty>', location='/chat/test/?a=1&b=%2F')
add('append-slash-session', 'anonymous', '/chat/test', 301, '<empty>', headers=session('owner'), location='/chat/test/')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'chat/test/ login_required page without its template',
                              'ids': IDS, 'sessions': SESSIONS, 'csrf': CSRF, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
