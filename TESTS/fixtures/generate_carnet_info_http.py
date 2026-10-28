"""Author the carnet/<uid>/info HTTP sequence from carnet-info.md.

Builds on the carnet-list seed plus a second own course whose subscription
is not linked to any carnet. Only associations are served; the carnet must
belong to the scope user's association. ``data`` is CarnetListInfoSerializer
(no ``meta``) plus ``subscriptions``: the carnet's holdings, newest first,
through CarnetSubscriptionSerializer, each with ``courses`` from every live
course subscription of the holding's subscription (not the M2M links),
through CourseSimpleSerializer; those course rows are unordered. Run with
``python3 -I`` after the carnet-list generator.
"""
import importlib.util
import json
from pathlib import Path

# Loading the list generator rewrites its (unchanged) fixture.
spec = importlib.util.spec_from_file_location('carnet_list_generator', Path(__file__).with_name('generate_carnet_list_http.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

OUTPUT = Path(__file__).with_name('carnet_info_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, CARNETS = SOURCE['ids'], SOURCE['carnets']
ids = iter(f'a1000000-0000-4000-8000-{890001 + n:012d}' for n in range(10))
COURSES = {**SOURCE['courses'], 'gym': {'id': next(ids), 'association': IDS['own_association'], 'title': 'Palestra', 'description': None}}
COURSES['swim'] = {**COURSES['swim'], 'description': 'Corsia è'}
COURSE_SUBSCRIPTIONS = {**SOURCE['course_subscriptions'],
                        'unlinked': {'id': next(ids), 'course': 'gym', 'subscription': 'current', 'deleted': False}}
path = lambda key: f'/carnet/{CARNETS[key]["id"] if key in CARNETS else key}/info'


def courses(subscription):
    rows = [COURSES[c['course']] for c in COURSE_SUBSCRIPTIONS.values() if c['subscription'] == subscription and not c['deleted']]
    return [{'course_id': row['id'], 'title': row['title'], 'description': row.get('description', '')} for row in rows]


def info(label, holdings):
    carnet = {k: v for k, v in base.carnet(label).items() if k != 'meta'}
    subscriptions = [{**base.held(h), 'courses': courses(base.CARNET_SUBSCRIPTIONS[h]['subscription']) if base.CARNET_SUBSCRIPTIONS[h]['subscription'] else []}
                     for h in holdings]
    return {'data': {**carnet, 'subscriptions': subscriptions}}


cases = []


def add(name, actor, target, status, response, *, method='GET', headers=None):
    cases.append({'name': f'carnet-info-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
                  'status': status, 'response': response})


NOT_FOUND = {'msg': 'Carnet not found.'}
add('owner-with-holdings', 'owner', 'c1', 200, info('c1', ['held', 'family']))
add('owner-detached-holding', 'full_collaborator', 'c2', 200, info('c2', ['someone_else', 'detached']))
add('admin-impersonating-owner', 'admin', 'c1', 200, info('c1', ['held', 'family']),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 'cf', 200, info('cf', ['abroad']))
add('foreign-carnet-not-found', 'owner', 'cf', 404, NOT_FOUND)
add('missing-not-found', 'owner', IDS['missing'], 404, NOT_FOUND)
add('owner-without-association-not-found', 'owner_without_association', 'c1', 404, NOT_FOUND)
add('malformed-id', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('athlete-not-allowed', 'athlete', 'c1', 403, {'msg': 'Not allowed.'})
add('admin-impersonating-athlete-not-allowed', 'admin', 'c1', 403, {'msg': 'Not allowed.'},
    headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']})
add('owner-scoped-athlete-denied', 'owner', 'c1', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 403, {'detail': 'Missing permission: association.carnet.read'})
add('admin-without-session', 'admin', 'c1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'c1', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'c1', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
# DRF metadata renders the view docstring as the description.
add('options', 'owner', 'c1', 200, {'name': 'Carnet Info', 'description': 'Info about a carnet', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Carnet info authored sequence',
                              'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
