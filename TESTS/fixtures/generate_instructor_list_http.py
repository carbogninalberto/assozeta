"""Author the instructor/list HTTP sequence from instructor-list.md.

The view lists Instructor.objects.filter(user=request.user) (collaborators act
as the owner; any authenticated role is served) through InstructorSerializer
(fields '__all__', documents as primary keys). Rows and document keys are
unordered. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('instructor_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
ids = iter(f'a1000000-0000-4000-8000-{950001 + n:012d}' for n in range(20))
DOCUMENTS = {'d1': next(ids), 'd2': next(ids)}
FIELDS = ('first_name', 'last_name', 'email', 'born_date', 'born_city', 'born_province', 'tax_code', 'address_city', 'address',
          'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3', 'phone_4', 'draft',
          'associated_user_id', 'is_volunteer', 'stipulated_contract_in', 'default_hourly_billing', 'default_percentage_billing')


def instructor(owner, first, **values):
    row = {'id': next(ids), 'owner': owner, 'documents': values.pop('documents', []), **{f: None for f in FIELDS},
           'first_name': first, 'last_name': 'Rossi', 'role': 'Istruttore', 'draft': False, 'is_volunteer': False,
           'default_hourly_billing': '0.00', 'default_percentage_billing': '0.00'}
    row.update(values)
    return row


INSTRUCTORS = {
    'full': instructor('owner', 'Anna è', email='anna@example.test', born_date='1985-04-03', born_city='Roma', tax_code='RSSNNA85D43H501X',
                       phone='+39 1', study_title='ISEF', associated_user_id=IDS['full_collaborator'], is_volunteer=True,
                       stipulated_contract_in='2026-01-15', default_hourly_billing='25.50', default_percentage_billing=None,
                       documents=['d1', 'd2'], role=None),
    'draft': instructor('owner', 'Bruno', draft=True, default_percentage_billing='12.50'),
    'abroad': instructor('foreign_owner', 'Carla'),
}


def serialized(label):
    row = INSTRUCTORS[label]
    return {'instructor_id': row['id'], 'user': IDS[row['owner']], **{f: row[f] for f in FIELDS},
            'documents': [DOCUMENTS[d] for d in row['documents']]}


cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'instructor-list-{name}', 'actor': actor, 'method': method, 'path': '/instructor/list', 'headers': headers or {},
                  'status': status, 'response': response})


OWN = {'data': [serialized('full'), serialized('draft')]}
add('owner', 'owner', 200, OWN)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWN)
add('admin-impersonating-owner', 'admin', 200, OWN, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 200, {'data': [serialized('abroad')]})
add('athlete-empty', 'athlete', 200, {'data': []})
add('owner-without-association-empty', 'owner_without_association', 200, {'data': []})
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.instructor.read'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Instructor List', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Instructor list authored sequence', 'ids': IDS, 'documents': DOCUMENTS,
                              'instructors': INSTRUCTORS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
