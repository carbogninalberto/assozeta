"""Author the carnet-subscription/<uid>/delete/<uid_course> HTTP sequence from carnet-writes.md.

Despite its stacked @api_view(['POST']) and @api_view(['DELETE']) decorators
the route behaves as an authenticated DELETE view (Allow: DELETE, OPTIONS;
confirmed against Django). It refuses non-association roles, gets the
carnet subscription within the scope user's subscriptions (404; a non-UUID is
a 500), removes one course-subscription link (a non-UUID course id is a 500;
an unlinked id is a no-op) and saves without field changes: no audit entry.
Run with ``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_subscription_delete_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, HOLDINGS, LINKS = SOURCE['ids'], SOURCE['carnet_subscriptions'], SOURCE['course_subscriptions']
holding = lambda key: HOLDINGS[key]['id'] if key in HOLDINGS else key
link = lambda key: LINKS[key]['id'] if key in LINKS else key
cases = []


def add(name, actor, target, course, status, response, *, method='DELETE', headers=None, effect=None):
    cases.append({'name': f'carnet-subscription-delete-{name}', 'actor': actor, 'method': method,
                  'path': f'/carnet-subscription/{holding(target)}/delete/{link(course)}', 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


NOT_FOUND, OK = {'msg': 'Carnet subscription not found.'}, {'msg': 'Carnet subscription deleted.'}
SERVER = '<stable-server-error-page>'
add('foreign-not-found', 'owner', 'abroad', 'active', 404, NOT_FOUND)
add('without-subscription-not-found', 'owner', 'detached', 'active', 404, NOT_FOUND)
add('missing-not-found', 'owner', IDS['missing'], 'active', 404, NOT_FOUND)
add('malformed-holding-server-error', 'owner', 'abc', 'active', 500, SERVER)
add('malformed-course-server-error', 'owner', 'held', 'abc', 500, SERVER)
add('athlete-not-allowed', 'athlete', 'held', 'active', 403, {'msg': 'Not allowed.'})
add('owner-scoped-athlete-denied', 'owner', 'held', 'active', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'held', 'active', 403, {'detail': 'Missing permission: association.carnet.delete'})
add('admin-without-session', 'admin', 'held', 'active', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'held', 'active', 401, {'detail': 'Authentication credentials were not provided.'})
add('post-not-allowed', 'owner', 'held', 'active', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 'held', 'active', 200, {'name': 'Carnet Subscription Delete', 'description': 'Set carnet subscription delete',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('unlinked-course-no-op', 'owner', 'held', IDS['missing'], 200, OK, effect={'holding': 'held', 'removed': []})
add('owner-removes-link', 'owner', 'held', 'active', 200, OK, effect={'holding': 'held', 'removed': ['active']})
add('repeat-no-op', 'full_collaborator', 'held', 'active', 200, OK, effect={'holding': 'held', 'removed': []})
# remove() filters the ids through the target's default (soft-delete) manager:
# a link to a deleted course subscription stays.
add('deleted-course-subscription-link-kept', 'owner', 'held', 'deleted', 200, OK, effect={'holding': 'held', 'removed': []})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Carnet subscription course-link delete authored sequence',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
