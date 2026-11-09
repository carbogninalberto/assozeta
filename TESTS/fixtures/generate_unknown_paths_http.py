"""Author the unmatched-URL HTTP sequence (unknown-paths.md).

A path that no pattern of ``core.urls`` resolves (including a path segment a
converter rejects) reaches Django's default ``page_not_found`` (DEBUG off, no
404.html anywhere): the plain HTML page ``ERROR_PAGE_TEMPLATE`` with the title
"Not Found", status 404, for every method (CSRF never runs: there is no view).
``CommonMiddleware.process_response`` then turns that 404 into a 301 to the
path with a trailing slash when the request path does not end in '/' and the
path plus '/' resolves (DEBUG off: every method), keeping the raw query.
Router routes (``include(router.urls)``) and the docmanager ``.../view/``
routes are the slash-terminated patterns that make this reachable.
Every answer passes LocaleMiddleware (Content-Language, Vary Accept-Language),
CorsMiddleware (Vary origin) and AuditlogMiddleware (reads the session: Vary
Cookie) unless ConditionalSessionMiddleware skips the session (an ``/api/``
path with a Bearer token).
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('unknown_paths_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
NOT_FOUND = ('\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Not Found</title>\n</head>\n<body>\n'
             '  <h1>Not Found</h1><p>The requested resource was not found on this server.</p>\n</body>\n</html>\n')
VARY = 'Cookie, origin, Accept-Language'
DELETE = 'sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax'
METHODS = ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')
cases = []


def add(name, path, status, *, actor='anonymous', method='GET', headers=None, location=None, cookie=None, vary=VARY):
    response = {301: '<empty>', 404: NOT_FOUND, 500: '<stable-server-error-page>'}[status]
    cases.append({'name': f'unknown-paths-{name}', 'actor': actor, 'method': method, 'path': path,
                  'headers': headers or {}, 'status': status,
                  'response': '<empty>' if method == 'HEAD' else response,
                  'location': location, 'set_cookie': cookie, 'vary': vary, 'effect': None})


def missing(name, path, **options):
    add(name, path, 404, **options)


def slash(name, path, location, **options):
    add(name, path, 301, location=location, **options)


# Unmatched paths at the root (the '' mounts: application, chat, docmanager,
# communications), every method, any actor: no view, so no authentication,
# permission registry, CSRF or method check runs.
for method in METHODS:
    missing(f'root-{method.lower()}', '/no-such-route', method=method)
missing('root-slash-terminated', '/no-such-route/')
missing('root-nested', '/no/such/route')
missing('root-query', '/no-such-route?a=1&b=%2F')
for actor in ('owner', 'full_collaborator', 'athlete', 'admin', 'restricted_collaborator'):
    missing(f'root-bearer-{actor}', '/no-such-route', actor=actor)
missing('root-post-without-csrf', '/no-such-route', method='POST', headers={'Cookie': 'csrftoken=' + 'c' * 32})
# Under each include's own prefixes.
missing('communications-unknown', '/communications/no-such-route')
missing('communications-prefix', '/communications/')
missing('communications-known-extra-segment', '/communications/messages/list/extra')
missing('docmanager-unknown', '/document/no-such-route')
missing('docmanager-known-without-segment', '/document/invoice/view/')
missing('chat-unknown', '/chat/no-such-route')
missing('chat-prefix', '/chat/')
missing('application-known-with-slash', '/profile/info/')
missing('application-known-extra-segment', '/course/list/extra', actor='owner')
# Django's path converter needs one character; Caddy collapses '//' itself, so
# empty str segments are not authored (they are unit-tested in Go).
missing('application-empty-path-converter', '/manuale/assets/', actor='owner')
missing('instance-unknown', '/instance/no-such-route')
missing('instance-prefix', '/instance/')
missing('instance-prefix-bare', '/instance')
missing('instance-known-with-slash', '/instance/status/')
missing('instance-admin-unknown', '/instance/admin/no-such-route', actor='owner')
missing('bakney-unknown', '/bakney/v1/no-such-route')
missing('bakney-prefix', '/bakney/v1/')
# Converters that reject the segment leave the URL unmatched.
missing('converter-int', '/audit-logs/12a/detail', actor='owner')
missing('converter-uuid-download', '/instance/admin/data-restore/not-a-uuid/download')
missing('converter-uuid-action', '/instance/admin/data-restore/not-a-uuid/start', actor='owner', method='POST')
# An /api/ path with a Bearer token skips the session (no Vary: Cookie), then
# AuthenticationMiddleware raises ImproperlyConfigured: the 500 page.
missing('api-prefix-anonymous', '/api/no-such-route')
add('api-prefix-bearer', '/api/no-such-route', 500, actor='owner', vary='origin, Accept-Language')
# A supplied cookie whose session is empty is deleted, as on any other page.
missing('empty-session-cookie', '/no-such-route', headers={'Cookie': 'sessionid=short'}, cookie=DELETE)
# APPEND_SLASH: the path plus '/' resolves.
# document/subscription/preview resolves without the slash too: no redirect.
slash('slash-docmanager-medical', '/document/medical/certificate', '/document/medical/certificate/')
slash('slash-docmanager-view', '/document/invoice/abc/view', '/document/invoice/abc/view/')
slash('slash-docmanager-view-query', '/document/invoice/abc/view?token=a%2Fb&x=%C3%A8', '/document/invoice/abc/view/?token=a%2Fb&x=%C3%A8')
for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'):
    slash(f'slash-docmanager-{method.lower()}', '/document/medical/certificate', '/document/medical/certificate/', method=method)
slash('slash-docmanager-bearer', '/document/subscription/abc/view', '/document/subscription/abc/view/', actor='owner')
slash('slash-router-list', '/folders', '/folders/')
slash('slash-router-detail', '/course-locations/abc', '/course-locations/abc/', actor='owner')
slash('slash-router-action', '/documents/bulk_delete', '/documents/bulk_delete/', method='DELETE')
slash('slash-router-nested-prefix', '/subscription/memberships', '/subscription/memberships/')
slash('slash-router-persona-detail', '/personas/abc', '/personas/abc/', actor='owner')
slash('slash-chat', '/chat/test', '/chat/test/')
slash('slash-empty-session-cookie', '/folders', '/folders/', headers={'Cookie': 'sessionid=short'}, cookie=DELETE)
# A router detail pk excludes '.' and '/': no slash redirect, plain 404.
missing('router-detail-dot', '/folders/a.b.c')
missing('router-detail-nested', '/folders/a/b')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'unmatched URLs: 404 page and APPEND_SLASH redirects',
                              'ids': IDS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
