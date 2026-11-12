"""Author the course/tags/list HTTP sequence from course-tags.md.

The view lists CourseTags of request.user.sport_association ordered by
tag_name (collaborators act as the owner). The sport_association property
refuses athletes and raises DoesNotExist (500) for an owner without an
association. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_tags_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{970001 + n:012d}' for n in range(10))
TAGS = {'zeta': {'id': next(ids), 'name': 'Zeta', 'association': OWN}, 'alpha': {'id': next(ids), 'name': 'Alpha è', 'association': OWN},
        'lower': {'id': next(ids), 'name': 'bimbi', 'association': OWN}, 'abroad': {'id': next(ids), 'name': 'Estero', 'association': FOREIGN}}
# C collation: uppercase sorts before lowercase.
tags = lambda *labels: {'tags': [{'tag_id': TAGS[l]['id'], 'tag_name': TAGS[l]['name']} for l in labels]}
cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'course-tags-list-{name}', 'actor': actor, 'method': method, 'path': '/course/tags/list', 'headers': headers or {},
                  'status': status, 'response': response})


OWN_TAGS = tags('alpha', 'zeta', 'lower')
add('owner', 'owner', 200, OWN_TAGS)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWN_TAGS)
add('admin-impersonating-owner', 'admin', 200, OWN_TAGS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 200, tags('abroad'))
add('owner-without-association-server-error', 'owner_without_association', 500, '<stable-server-error-page>')
add('athlete-not-association', 'athlete', 403, {'detail': 'User is not a sport association'})
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.courses.read'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Course Tags List',
    'description': 'API endpoint to get the list of tags for the subscriptions\n:param request: None\n:return: list of tags',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course tags list authored sequence', 'ids': IDS, 'tags': TAGS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
