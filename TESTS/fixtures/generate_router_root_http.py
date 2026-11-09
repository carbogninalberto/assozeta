"""Author the DefaultRouter API root HTTP sequence (router-stock.md).

DefaultRouter.get_api_root_view(): APIRootView with the default permission
classes (AllowAny) and GET only (View.setup adds HEAD). get() reverses each
registration's '<basename>-list' name with the request: reverse() returns
the last pattern with that name that accepts the arguments, so the names an
explicit route of BE/application/urls.py reuses (course-location-list,
folder-list, subscription-membership-list, course-subscription-list,
persona-list) resolve to those routes; with a format suffix only the
router's format patterns accept format=, giving '<prefix>.<format>'. URLs
are absolute on the request's host (USE_X_FORWARDED_HOST; '$host' is the
host the client addressed). An unknown format is content negotiation's 404.
OPTIONS names the view 'Api Root' with the class docstring. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_root_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'router-root-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response})


PREFIXES = ('course-locations', 'folders', 'documents', 'templates', 'subscription/memberships', 'course-subscriptions', 'personas',
            'personas-subscriptions')
NAMED = {'course-locations': '/course/locations/list', 'folders': '/folders/list', 'subscription/memberships': '/subscription/memberships/list',
         'course-subscriptions': '/course-subscriptions/list', 'personas': '/personas/list'}
ROOT = {prefix: 'http://$host' + NAMED.get(prefix, f'/{prefix}/') for prefix in PREFIXES}
FORMATTED = {prefix: f'http://$host/{prefix}.json' for prefix in PREFIXES}

add('anonymous', 'anonymous', '/', 200, ROOT)
add('owner', 'owner', '/', 200, ROOT)
add('athlete', 'athlete', '/', 200, ROOT)
add('full-collaborator', 'full_collaborator', '/', 200, ROOT)
add('restricted-collaborator', 'restricted_collaborator', '/', 403,
    {'detail': "Access denied: No permission mapping for endpoint ''. Contact administrator to grant access."})
add('admin-without-session', 'admin', '/', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('format-json', 'anonymous', '/.json', 200, FORMATTED)
add('format-json-slash', 'owner', '/.json/', 200, FORMATTED)
add('format-unknown-not-found', 'anonymous', '/.xml', 404, {'detail': 'Not found.'})
add('query-format-unknown-not-found', 'anonymous', '/?format=xml', 404, {'detail': 'Not found.'})
add('post-not-allowed', 'anonymous', '/', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('delete-not-allowed', 'owner', '/', 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('options', 'anonymous', '/', 200, {'name': 'Api Root', 'description': 'The default basic root view for DefaultRouter',
                                       'renders': ['application/json'],
                                       'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'DefaultRouter API root', 'ids': IDS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
