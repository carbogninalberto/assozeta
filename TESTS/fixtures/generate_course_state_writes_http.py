"""Author the course/<uid>/enable, disable and pin HTTP sequence (course-state.md).

Each POST view runs is_valid_uuid(uid) first (a DRF 400 with the uuid
error), then SportAssociation.objects.get(user=request.user) (a 500 for
athletes) and Course.objects.filter(course_id=uid).first(): the live,
group-aware course of any association (404 {'msg': 'Course not found.'}),
whose foreign association is PermissionDenied('User not allowed.').
enable sets ACTIVE and disable DRAFT, refusing a course already in that
state (403 'User cannot change the status of the subscription.'); pin
toggles pinned. Course.save() updates the row and auditlog records the
changed field. Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_state_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
DRAFT, ACTIVE, INTERNAL = 1, 2, 3
ids = iter(f'a1000000-0000-4000-8000-{990001 + n:012d}' for n in range(10))
COURSES = {
    'draft': {'id': next(ids), 'association': OWN, 'title': 'Bozza', 'status': DRAFT, 'pinned': False, 'deleted': False, 'group': None},
    'internal': {'id': next(ids), 'association': OWN, 'title': 'Interno', 'status': INTERNAL, 'pinned': True, 'deleted': False, 'group': None},
    'active': {'id': next(ids), 'association': OWN, 'title': 'Attivo', 'status': ACTIVE, 'pinned': False, 'deleted': False, 'group': None},
    'grouped': {'id': next(ids), 'association': OWN, 'title': 'Gruppo', 'status': DRAFT, 'pinned': False, 'deleted': False, 'group': 'own_group'},
    'gone': {'id': next(ids), 'association': OWN, 'title': 'Eliminato', 'status': DRAFT, 'pinned': False, 'deleted': True, 'group': None},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'title': 'Estero', 'status': DRAFT, 'pinned': False, 'deleted': False, 'group': None},
}
SEED = copy.deepcopy(COURSES)
UNKNOWN = 'a1000000-0000-4000-8000-000000999998'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, label, verb, status, response, *, method='POST', headers=None, effect=None):
    uid = COURSES[label]['id'] if label in COURSES else label
    cases.append({'name': f'course-state-writes-{name}', 'actor': actor, 'method': method, 'path': f'/course/{uid}/{verb}',
                  'headers': headers or {}, 'status': status, 'response': response, 'effect': effect})


def change(actor, label, field, value):
    row = COURSES[label]
    key = 'status' if field == 'status_flag' else 'pinned'
    before, row[key] = row[key], value
    return {'actor': actor, 'label': label, 'field': field, 'from': before, 'to': value}


NOT_FOUND = {'msg': 'Course not found.'}
NOT_ALLOWED = {'detail': 'User not allowed.'}
SAME_STATE = {'detail': 'User cannot change the status of the subscription.'}
MALFORMED = ['badly formed hexadecimal UUID string']
GROUP = lambda key: {'X-Group-Id': IDS[key]}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}

add('enable-anonymous', 'anonymous', 'draft', 'enable', 401, {'detail': 'Authentication credentials were not provided.'})
add('enable-admin-without-session', 'admin', 'draft', 'enable', 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('enable-restricted-missing-permission', 'restricted_collaborator', 'draft', 'enable', 403,
    {'detail': 'Missing permission: association.courses.update'})
add('enable-owner-scoped-athlete-denied', 'owner', 'draft', 'enable', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('enable-wrong-method', 'owner', 'draft', 'enable', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('enable-options', 'owner', 'draft', 'enable', 200, options('Course Enable'), method='OPTIONS')
add('enable-malformed', 'owner', 'abc', 'enable', 400, MALFORMED)
add('enable-athlete-malformed', 'athlete', 'abc', 'enable', 400, MALFORMED)
add('enable-athlete-server-error', 'athlete', 'draft', 'enable', 500, SERVER)
add('enable-unknown', 'owner', UNKNOWN, 'enable', 404, NOT_FOUND)
add('enable-deleted', 'owner', 'gone', 'enable', 404, NOT_FOUND)
add('enable-group-header-hides', 'owner', 'grouped', 'enable', 404, NOT_FOUND, headers=GROUP('foreign_group'))
add('enable-foreign-course', 'owner', 'abroad', 'enable', 403, NOT_ALLOWED)
add('enable-already-active', 'owner', 'active', 'enable', 403, SAME_STATE)
add('enable-draft', 'owner', 'draft', 'enable', 200, {'data': {'msg': 'course enabled.'}}, effect=change('owner', 'draft', 'status_flag', ACTIVE))
add('enable-internal', 'full_collaborator', 'internal', 'enable', 200, {'data': {'msg': 'course enabled.'}},
    effect=change('full_collaborator', 'internal', 'status_flag', ACTIVE))
add('enable-grouped', 'owner', 'grouped', 'enable', 200, {'data': {'msg': 'course enabled.'}}, headers=GROUP('own_group'),
    effect=change('owner', 'grouped', 'status_flag', ACTIVE))

add('disable-restricted-missing-permission', 'restricted_collaborator', 'active', 'disable', 403,
    {'detail': 'Missing permission: association.courses.update'})
add('disable-wrong-method', 'owner', 'active', 'disable', 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH')
add('disable-options', 'owner', 'active', 'disable', 200, options('Course Disable'), method='OPTIONS')
add('disable-malformed', 'owner', 'abc', 'disable', 400, MALFORMED)
add('disable-unknown', 'owner', UNKNOWN, 'disable', 404, NOT_FOUND)
add('disable-foreign-course', 'owner', 'abroad', 'disable', 403, NOT_ALLOWED)
add('disable-active', 'owner', 'active', 'disable', 200, {'data': {'msg': 'course disabled.'}}, effect=change('owner', 'active', 'status_flag', DRAFT))
add('disable-already-draft', 'full_collaborator', 'active', 'disable', 403, SAME_STATE)

add('pin-restricted-missing-permission', 'restricted_collaborator', 'draft', 'pin', 403, {'detail': 'Missing permission: association.courses.update'})
add('pin-wrong-method', 'owner', 'draft', 'pin', 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('pin-options', 'owner', 'draft', 'pin', 200, options('Course Pin'), method='OPTIONS')
add('pin-malformed', 'owner', 'abc', 'pin', 400, MALFORMED)
add('pin-deleted', 'owner', 'gone', 'pin', 404, NOT_FOUND)
add('pin-foreign-course', 'owner', 'abroad', 'pin', 403, NOT_ALLOWED)
add('pin-athlete-server-error', 'athlete', 'draft', 'pin', 500, SERVER)
add('pin-draft', 'owner', 'draft', 'pin', 200, {'data': {'msg': 'corso messo in evidenza.'}}, effect=change('owner', 'draft', 'pinned', True))
add('pin-again-unpins', 'full_collaborator', 'draft', 'pin', 200, {'data': {'msg': "corso tolto dall'evidenza."}},
    effect=change('full_collaborator', 'draft', 'pinned', False))
add('pin-internal-unpins', 'owner', 'internal', 'pin', 200, {'data': {'msg': "corso tolto dall'evidenza."}},
    effect=change('owner', 'internal', 'pinned', False))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course enable, disable and pin authored sequence with audit effects', 'ids': IDS,
                              'courses': SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
