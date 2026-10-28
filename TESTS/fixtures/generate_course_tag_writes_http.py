"""Author the course/tags add, update, delete, assign and unassign HTTP sequence (course-tags.md).

Builds on the course-tag list seed plus courses. add and update require a
tag_name key that is not None (400 'tag_name is required'), stored through
the CharField (str() of any other JSON value) while the response echoes the
value as sent; request.user.sport_association raises PermissionDenied
('User is not a sport association') for athletes. Tags are the association's
(otherwise 404 'tag not found'; a malformed id is a 500); assign/unassign
also need the live, group-aware course of the association (404 'course not
found') and change only the M2M link (both answer 'tag assigned'). auditlog
records the tag create, rename and delete. Run with ``python3 -I`` after
the course-tag list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_tag_writes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('course_tags_list_http.json').read_text()))
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
TAGS = SOURCE['tags']
ids = iter(f'a1000000-0000-4000-8000-{975001 + n:012d}' for n in range(10))
SOURCE['courses'] = {
    'swim': {'id': next(ids), 'association': OWN, 'title': 'Nuoto', 'deleted': False, 'group': None, 'tags': ['zeta']},
    'gym': {'id': next(ids), 'association': OWN, 'title': 'Palestra', 'deleted': True, 'group': None, 'tags': []},
    'grouped': {'id': next(ids), 'association': OWN, 'title': 'Gruppo', 'deleted': False, 'group': 'own_group', 'tags': []},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'title': 'Estero', 'deleted': False, 'group': None, 'tags': []},
}
COURSES = SOURCE['courses']
UNKNOWN = 'a1000000-0000-4000-8000-000000979998'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'course-tag-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ref = lambda table, key: table[key]['id'] if key in table else key
tag = lambda label, verb: f'/course/tags/{ref(TAGS, label)}/{verb}'
link = lambda label, verb, course: f'/course/tags/{ref(TAGS, label)}/{verb}/{ref(COURSES, course)}'
ADD = '/course/tags/add'
REQUIRED = {'msg': 'tag_name is required'}
NOT_TAG, NOT_COURSE = {'msg': 'tag not found'}, {'msg': 'course not found'}
ATHLETE = {'detail': 'User is not a sport association'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
options = lambda name, description: {'name': name, 'description': description, 'renders': ['application/json'],
                                     'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
add('add-missing-name', 'owner', ADD, 400, REQUIRED, body={})
add('add-null-name', 'owner', ADD, 400, REQUIRED, body={'tag_name': None})
add('add-athlete-denied', 'athlete', ADD, 403, ATHLETE, body={'tag_name': 'X'})
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[])
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={'tag_name': 'X'}, headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, {'detail': 'Missing permission: association.courses.update'},
    body={'tag_name': 'X'})
add('add-admin-without-session', 'admin', ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('add-anonymous', 'anonymous', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, options('Course Tags Add', 'API endpoint to add a new tag for the subscriptions\n:param request: tag_name\n:return: list of tags'),
    method='OPTIONS')
add('add-owner', 'owner', ADD, 200, {'tag_id': '$new', 'tag_name': 'Nuovo è'}, body={'tag_name': 'Nuovo è'},
    effect={'kind': 'add', 'actor': 'owner', 'stored': 'Nuovo è'})
add('add-number-name', 'full_collaborator', ADD, 200, {'tag_id': '$new', 'tag_name': 5}, body={'tag_name': 5},
    effect={'kind': 'add', 'actor': 'full_collaborator', 'stored': '5'})
add('add-form', 'owner', ADD, 200, {'tag_id': '$new', 'tag_name': ''}, raw_body='tag_name=', content_type='application/x-www-form-urlencoded',
    effect={'kind': 'add', 'actor': 'owner', 'stored': ''})
add('update-missing-name', 'owner', tag('alpha', 'update'), 400, REQUIRED, method='PATCH', body={})
add('update-malformed-server-error', 'owner', tag('abc', 'update'), 500, SERVER, method='PATCH', body={'tag_name': 'X'})
add('update-unknown', 'owner', tag(UNKNOWN, 'update'), 404, NOT_TAG, method='PATCH', body={'tag_name': 'X'})
add('update-foreign', 'owner', tag('abroad', 'update'), 404, NOT_TAG, method='PATCH', body={'tag_name': 'X'})
add('update-athlete-denied', 'athlete', tag('alpha', 'update'), 403, ATHLETE, method='PATCH', body={'tag_name': 'X'})
add('update-options', 'owner', tag('alpha', 'update'), 200,
    options('Course Tags Update', 'API endpoint to update a tag for the subscriptions\n:param request: tag_name\n:return: list of tags'), method='OPTIONS')
add('update-rename', 'owner', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 'Alfa'}, method='PATCH',
    body={'tag_name': 'Alfa'}, effect={'kind': 'update', 'actor': 'owner', 'tag': 'alpha', 'changes': {'tag_name': ['Alpha è', 'Alfa']}})
add('update-same-name-no-audit', 'owner', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 'Alfa'}, method='PATCH',
    body={'tag_name': 'Alfa'}, effect={'kind': 'update', 'actor': 'owner', 'tag': 'alpha', 'changes': {}})
add('update-list-name-stored-as-text', 'full_collaborator', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': ['a', 1]},
    method='PATCH', body={'tag_name': ['a', 1]},
    effect={'kind': 'update', 'actor': 'full_collaborator', 'tag': 'alpha', 'changes': {'tag_name': ['Alfa', "['a', 1]"]}})
add('assign-unknown-tag', 'owner', link(UNKNOWN, 'assign', 'swim'), 404, NOT_TAG, method='PATCH')
add('assign-foreign-course', 'owner', link('alpha', 'assign', 'abroad'), 404, NOT_COURSE, method='PATCH')
add('assign-deleted-course', 'owner', link('alpha', 'assign', 'gym'), 404, NOT_COURSE, method='PATCH')
add('assign-group-header-hides', 'owner', link('alpha', 'assign', 'grouped'), 404, NOT_COURSE, method='PATCH',
    headers={'X-Group-Id': IDS['foreign_group']})
add('assign-malformed-course-server-error', 'owner', link('alpha', 'assign', 'abc'), 500, SERVER, method='PATCH')
add('assign-athlete-denied', 'athlete', link('alpha', 'assign', 'swim'), 403, ATHLETE, method='PATCH')
add('assign-wrong-method', 'owner', link('alpha', 'assign', 'swim'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('assign', 'owner', link('alpha', 'assign', 'swim'), 200, {'msg': 'tag assigned'}, method='PATCH',
    effect={'kind': 'link', 'actor': 'owner', 'add': [['alpha', 'swim']], 'remove': []})
add('assign-again', 'owner', link('alpha', 'assign', 'swim'), 200, {'msg': 'tag assigned'}, method='PATCH',
    effect={'kind': 'link', 'actor': 'owner', 'add': [], 'remove': []})
add('assign-grouped-course', 'full_collaborator', link('lower', 'assign', 'grouped'), 200, {'msg': 'tag assigned'}, method='PATCH',
    headers={'X-Group-Id': IDS['own_group']}, effect={'kind': 'link', 'actor': 'full_collaborator', 'add': [['lower', 'grouped']], 'remove': []})
add('unassign', 'owner', link('zeta', 'unassign', 'swim'), 200, {'msg': 'tag assigned'}, method='PATCH',
    effect={'kind': 'link', 'actor': 'owner', 'add': [], 'remove': [['zeta', 'swim']]})
add('unassign-unlinked', 'owner', link('zeta', 'unassign', 'swim'), 200, {'msg': 'tag assigned'}, method='PATCH',
    effect={'kind': 'link', 'actor': 'owner', 'add': [], 'remove': []})
add('delete-unknown', 'owner', tag(UNKNOWN, 'delete'), 404, NOT_TAG, method='DELETE')
add('delete-foreign', 'owner', tag('abroad', 'delete'), 404, NOT_TAG, method='DELETE')
add('delete-athlete-denied', 'athlete', tag('lower', 'delete'), 403, ATHLETE, method='DELETE')
add('delete-options', 'owner', tag('lower', 'delete'), 200,
    options('Course Tags Delete', 'API endpoint to delete a tag for the subscriptions\n:param request: tag_name\n:return: list of tags'), method='OPTIONS')
add('delete-linked', 'owner', tag('lower', 'delete'), 200, {'msg': 'tag deleted'}, method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'tag': 'lower', 'remove': [['lower', 'grouped']]})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Course tag writes authored sequence with link and audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
