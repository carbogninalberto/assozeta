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


def add(name, actor, path, status, response, *, method='GET', headers=None, location=None, cookie=None, vary=VARY, language='en'):
    cases.append({'name': f'chat-test-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'location': location, 'set_cookie': cookie, 'vary': vary,
                  'language': language, 'effect': None})


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
# CsrfViewMiddleware rejects an unsafe request with csrf_failure: 403 and the localized page (DEBUG off), in one of three
# forms. The page is 200+ bytes, so GZipMiddleware varies the answer on Accept-Encoding too.
PAGE_HEAD = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta http-equiv="content-type" content="text/html; charset=utf-8">\n'
    '  <meta name="robots" content="NONE,NOARCHIVE">\n  <title>403 Forbidden</title>\n  <style>\n'
    '    html * { padding:0; margin:0; }\n    body * { padding:10px 20px; }\n    body * * { padding:0; }\n'
    '    body { font-family: sans-serif; background:#eee; color:#000; }\n    body>div { border-bottom:1px solid #ddd; }\n'
    '    h1 { font-weight:normal; margin-bottom:.4em; }\n    h1 span { font-size:60%; color:#666; font-weight:normal; }\n'
    '    #info { background:#f6f6f6; }\n    #info ul { margin: 0.5em 4em; }\n    #info p, #summary p { padding-top:10px; }\n'
    '    #summary { background: #ffc; }\n    #explanation { background:#eee; border-bottom: 0px none; }\n  </style>\n</head>\n')
MESSAGES = {
    'en': {'title': 'Forbidden', 'main': 'CSRF verification failed. Request aborted.',
           'referer': ['You are seeing this message because this HTTPS site requires a “Referer header” to be sent by your web browser, '
                       'but none was sent. This header is required for security reasons, to ensure that your browser is not being '
                       'hijacked by third parties.',
                       'If you have configured your browser to disable “Referer” headers, please re-enable them, at least for this site, '
                       'or for HTTPS connections, or for “same-origin” requests.',
                       'If you are using the &lt;meta name=&quot;referrer&quot; content=&quot;no-referrer&quot;&gt; tag or including the '
                       '“Referrer-Policy: no-referrer” header, please remove them. The CSRF protection requires the “Referer” header to do '
                       'strict referer checking. If you’re concerned about privacy, use alternatives like &lt;a rel=&quot;noreferrer&quot; '
                       '…&gt; for links to third-party sites.'],
           'cookie': ['You are seeing this message because this site requires a CSRF cookie when submitting forms. This cookie is '
                      'required for security reasons, to ensure that your browser is not being hijacked by third parties.',
                      'If you have configured your browser to disable cookies, please re-enable them, at least for this site, '
                      'or for “same-origin” requests.'],
           'more': 'More information is available with DEBUG=True.'},
    'it': {'title': 'Proibito', 'main': 'Verifica CSRF fallita. Richiesta interrotta.',
           'referer': ['Vedi questo messaggio perchè questo sito HTTPS richiede l&#x27;invio da parte del tuo browser del “Referer header”, '
                       'che non è invece stato inviato. Questo header è richiesto per motivi di sicurezza, per assicurare che il tuo '
                       'browser non sia stato sabotato da terzi.',
                       'Se hai configurato il tuo browser web per disattivare l&#x27;invio delle intestazioni &quot;Referer&quot;, '
                       'riattiva questo invio, almeno per questo sito, o per le connessioni HTTPS, o per le connessioni '
                       '&quot;same-origin&quot;.',
                       'Se usi il tag &lt;meta name=&quot;referrer&quot; content=&quot;no-referrer&quot;&gt; o includi header '
                       '&#x27;Referrer-Policy: no-referrer&#x27;, per favore rimuovili. Per la protezione CSRF è necessario eseguire un '
                       'controllo rigoroso sull&#x27;header &#x27;Referer&#x27;. Se ti preoccupano le ricadute sulla privacy, puoi '
                       'ricorrere ad alternative come &lt;a rel=&quot;noreferrer&quot; …&gt; per i link a siti di terze parti.'],
           'cookie': ['Stai vedendo questo messaggio perché questo sito richiede un cookie CSRF quando invii dei form. Questo cookie è '
                      'necessario per ragioni di sicurezza, per assicurare che il tuo browser non sia stato dirottato da terze parti.',
                      'Se hai configurato il tuo browser web per disattivare l&#x27;invio dei cookies, riattivalo almeno per questo sito, '
                      'o per connessioni &quot;same-origin&quot;'],
           'more': 'Maggiorni informazioni sono disponibili con DEBUG=True'},
}


def failure_page(kind, language='en'):
    text = MESSAGES[language]
    # The template keeps the line break after each {% if %} / {% endif %} tag.
    summary = f'<body>\n<div id="summary">\n  <h1>{text["title"]} <span>(403)</span></h1>\n  <p>{text["main"]}</p>\n'
    if kind == 'referer':
        summary += '\n' + ''.join(f'  <p>{line}</p>\n' for line in text['referer'])
    summary += '\n'
    if kind == 'cookie':
        summary += '\n' + ''.join(f'  <p>{line}</p>\n' for line in text['cookie'])
    summary += '\n'
    return (PAGE_HEAD + summary + f'</div>\n\n<div id="explanation">\n  <p><small>{text["more"]}</small></p>\n</div>\n\n</body>\n</html>\n')


FAILED_VARY = VARY + ', Accept-Encoding'
for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
    add(f'csrf-{method.lower()}-no-cookie', 'anonymous', '/chat/test/', 403, failure_page('cookie'), method=method, vary=FAILED_VARY)
add('csrf-no-cookie-italian', 'anonymous', '/chat/test/', 403, failure_page('cookie', 'it'), method='POST', vary=FAILED_VARY,
    headers={'Accept-Language': 'it'}, language='it')
add('csrf-no-cookie-italian-quality', 'anonymous', '/chat/test/', 403, failure_page('cookie', 'it'), method='POST', vary=FAILED_VARY,
    headers={'Accept-Language': 'xx;q=0.9, it-IT;q=0.5, en;q=0.4'}, language='it')
add('csrf-session-no-cookie', 'anonymous', '/chat/test/', 403, failure_page('cookie'), method='POST', vary=FAILED_VARY,
    headers=session('owner'))
add('csrf-token-mismatch', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': f'csrftoken={CSRF}', 'X-CSRFToken': 'a' * 32})
add('csrf-token-missing', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': f'csrftoken={CSRF}'})
# process_request replaces a malformed CSRF cookie by a new one on the answer (random value, whatever the method).
NEW_CSRF_COOKIE = '<new-csrftoken-cookie>'
add('csrf-cookie-wrong-length', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': 'csrftoken=short', 'X-CSRFToken': 'short'}, cookie=NEW_CSRF_COOKIE)
add('csrf-cookie-invalid-character', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': 'csrftoken=' + '!' * 32, 'X-CSRFToken': CSRF}, cookie=NEW_CSRF_COOKIE)
add('anonymous-get-malformed-csrf-cookie', 'anonymous', '/chat/test/', 302, '<empty>', headers={'Cookie': 'csrftoken=short'},
    location=redirect, cookie=NEW_CSRF_COOKIE)
add('csrf-bad-origin', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': f'csrftoken={CSRF}', 'X-CSRFToken': CSRF, 'Origin': 'http://evil.example'})
add('csrf-https-no-referer', 'anonymous', '/chat/test/', 403, failure_page('referer'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': f'csrftoken={CSRF}', 'X-CSRFToken': CSRF, 'X-Forwarded-Proto': 'https'})
add('csrf-https-bad-referer', 'anonymous', '/chat/test/', 403, failure_page('other'), method='POST', vary=FAILED_VARY,
    headers={'Cookie': f'csrftoken={CSRF}', 'X-CSRFToken': CSRF, 'X-Forwarded-Proto': 'https', 'Referer': 'https://evil.example/x'})
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
