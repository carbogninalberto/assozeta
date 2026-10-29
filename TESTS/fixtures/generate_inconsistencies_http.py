"""Author the check-inconsistencies HTTP sequence from inconsistencies.md.

Associates belong to the shared fixture's athlete user. Born dates sit years
away from any 18th birthday, so the view's date.today() cannot move a row.
missing_tutors is unordered (the queryset has no ordering). The first
request promotes the only tutor relation of an associate without a primary
tutor (Associate.get_main_tutor saves it), which auditlog records once.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('inconsistencies_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/check-inconsistencies'
row_ids = iter(f'a1000000-0000-4000-8000-{790001 + n:012d}' for n in range(30))


def associate(label, born, *, association=OWN, deleted=False, group=None, **values):
    return label, {'id': next(row_ids), 'association': association, 'born_date': born, 'deleted': deleted, 'group': group,
                   'first_name': values.get('first_name', label), 'last_name': values.get('last_name', 'Rossi'),
                   'email': values.get('email'), 'sex': values.get('sex', 'A'), 'tax_code': values.get('tax_code'),
                   'address': values.get('address'), 'address_cap': values.get('address_cap'), 'address_city': values.get('address_city'),
                   'born_city': values.get('born_city'), 'is_minor': values.get('is_minor', False), 'phone': values.get('phone'),
                   'family_role': values.get('family_role')}


ASSOCIATES = dict([
    associate('minor_no_tutor', '2015-06-15', email='minore@example.test', sex='M', tax_code='RSSMNR15H15H501X',
              address='Via 1', address_cap='00100', address_city='Roma', born_city='Roma', is_minor=True, phone='+39 1',
              family_role='figlio', first_name='Minore è'),
    associate('minor_primary_tutor', '2016-01-10'),
    associate('minor_secondary_tutor', '2014-03-03'),
    associate('adult_no_tutor', '1990-05-05'),
    associate('no_born_date', None),
    associate('deleted_minor', '2015-01-01', deleted=True),
    associate('foreign_minor', '2013-09-09', association=FOREIGN),
    associate('group_minor', '2017-11-11', group='own_group'),
    associate('tutor', '1980-02-02'),
])
# Relations: (associate, tutor, primary). The secondary one is promoted on first read.
RELATIONS = [('minor_primary_tutor', 'tutor', True), ('minor_secondary_tutor', 'tutor', False)]
FIELDS = ('address', 'address_cap', 'address_city', 'born_city', 'born_date', 'email', 'sex', 'first_name', 'last_name',
          'is_minor', 'tax_code', 'phone', 'family_role')


def serialized(label):
    row = ASSOCIATES[label]
    # AssociateSerializer; user is the associate's user (the athlete).
    return {'associate_id': row['id'], **{field: row[field] for field in FIELDS}, 'sport_association': row['association'],
            'user': IDS['athlete'], 'family': None}


def report(*labels):
    return {'inconsistencies': bool(labels), 'missing_tutors': [serialized(label) for label in labels]}


cases = []


def add(name, actor, status, response, *, method='GET', headers=None, promotes=False):
    cases.append({'name': f'inconsistencies-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
                  'status': status, 'response': response, 'promotes': promotes})


ALL = ('minor_no_tutor', 'foreign_minor', 'group_minor')
add('athlete-promotes-secondary-tutor', 'athlete', 200, report(*ALL), promotes=True)
add('athlete-repeat-no-effect', 'athlete', 200, report(*ALL))
add('athlete-group-filter', 'athlete', 200, report('group_minor'), headers={'X-Group-Id': IDS['own_group']})
# GroupAwareManager logs and swallows the invalid UUID filter: no group filter.
add('athlete-invalid-group-unfiltered', 'athlete', 200, report(*ALL), headers={'X-Group-Id': 'nope'})
add('admin-impersonating-athlete', 'admin', 200, report(*ALL), headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']})
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('owner-no-associates', 'owner', 200, report())
add('full-collaborator-owner-scope', 'full_collaborator', 200, report())
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'athlete', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'athlete', 200, {'name': 'Check Inconsistencies', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'check-inconsistencies authored sequence with tutor promotion audit',
                              'ids': IDS, 'associates': ASSOCIATES, 'relations': RELATIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
