"""Author the course/locations/list HTTP sequence from course-locations.md.

CourseLocationViewSet.as_view({'get': 'list'}): the queryset is
CourseLocation.objects.filter(sport_association=SportAssociation.objects.get(
user=request.user)) (a missing association is a 500, including for
athletes), unordered, through CourseLocationSerializer (fields '__all__',
documents nested, unordered). The viewset's own delete() method answers
DELETE on this route too: without a pk get_object() fails (500). Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_locations_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{980001 + n:012d}' for n in range(20))
DOCUMENTS = {'d1': {'id': next(ids), 'token': next(ids), 'filename': 'mappa è.pdf', 'filepath': 'docs/mappa.pdf', 'size': 2048,
                    'created': '2026-09-01T08:00:00.500000Z'},
             'd2': {'id': next(ids), 'token': next(ids), 'filename': 'orari.pdf', 'filepath': None, 'size': None, 'created': '2026-09-02T08:00:00Z'}}
LOCATIONS = {'pool': {'id': next(ids), 'association': OWN, 'title': 'Piscina è', 'description': 'Vasca', 'address': 'Via 1', 'documents': ['d1', 'd2']},
             'gym': {'id': next(ids), 'association': OWN, 'title': None, 'description': None, 'address': None, 'documents': []},
             'abroad': {'id': next(ids), 'association': FOREIGN, 'title': 'Estero', 'description': '', 'address': '', 'documents': []}}


def document(label):
    row = DOCUMENTS[label]
    return {'document_id': row['id'], 'creation_date': row['created'], 'filepath': row['filepath'], 'filename': row['filename'],
            'file_size_bytes': row['size'], 'token': row['token']}


def location(label):
    row = LOCATIONS[label]
    return {'course_location_id': row['id'], 'documents': [document(d) for d in row['documents']], 'title': row['title'],
            'description': row['description'], 'address': row['address'], 'sport_association': row['association']}


cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'course-locations-list-{name}', 'actor': actor, 'method': method, 'path': '/course/locations/list',
                  'headers': headers or {}, 'status': status, 'response': response})


SERVER = '<stable-server-error-page>'
OWN_ROWS = {'data': [location('pool'), location('gym')]}
add('owner', 'owner', 200, OWN_ROWS)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWN_ROWS)
add('admin-impersonating-owner', 'admin', 200, OWN_ROWS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 200, {'data': [location('abroad')]})
add('owner-without-association-server-error', 'owner_without_association', 500, SERVER)
add('athlete-server-error', 'athlete', 500, SERVER)
add('delete-without-pk-server-error', 'owner', 500, SERVER, method='DELETE')
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.courses.read'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Course Location', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course locations list authored sequence', 'ids': IDS, 'documents': DOCUMENTS,
                              'locations': LOCATIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
