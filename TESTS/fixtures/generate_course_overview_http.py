"""Author the course/<uid>/overview HTTP sequence (course-overview.md).

course_overview runs is_valid_uuid(uid) (a DRF 400) and reads the live,
group-aware course whose association belongs to request.user (anything else,
including athletes, is PermissionDenied('Course not found')), then answers
{'data': {'course': CourseSerializer(course).data}}: every Course field, the
unordered tags (tag_id, tag_name) and locations (CourseLocationSimpleSerializer),
and 'athletes', an AthleteSerializer list on a missing attribute (allow_null:
None). The route is a read: every case leaves the state unchanged. Run with
``python3 -I``.
"""
import json
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_overview_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{995001 + n:012d}' for n in range(20))
TAGS = {'t1': {'id': next(ids), 'association': OWN, 'name': 'Agonistico è'}, 't2': {'id': next(ids), 'association': OWN, 'name': 'Base'}}
LOCATIONS = {'l1': {'id': next(ids), 'association': OWN, 'title': 'Piscina', 'address': 'Via 1'},
             'l2': {'id': next(ids), 'association': OWN, 'title': None, 'address': None}}
BASE = {'association': OWN, 'status': 2, 'type': 1, 'created': '2026-09-01T08:00:00Z', 'title': 'Corso', 'description': 'Testo',
        'multi_payments': False, 'fee': '10', 'events': None, 'one_fee_payment': False, 'one_fee': '0', 'google_calendar_id': None,
        'color': '#123456', 'multiple_quotes': None, 'start': None, 'end': None, 'pinned': False, 'season': False, 'frequency': 1,
        'from_subscription': False, 'day': 1, 'renewal': False, 'deleted': False, 'group': None, 'tags': [], 'locations': []}
COURSES = {
    'full': {**BASE, 'id': next(ids), 'status': 3, 'type': 2, 'created': '2026-09-01T08:00:00.123456Z', 'title': 'Nuoto è',
             'description': 'Lungo\ntesto', 'multi_payments': True, 'fee': '12.5', 'events': {'days': [1, 'lun'], 'note': None},
             'one_fee_payment': True, 'one_fee': '3', 'google_calendar_id': 'cal-1', 'color': '#abcdef',
             'multiple_quotes': [{'amount': 5, 'label': 'Prima'}], 'start': '2026-09-10T07:30:00Z', 'end': '2027-06-30T18:00:00.500000Z',
             'pinned': True, 'season': True, 'frequency': None, 'from_subscription': True, 'day': 28, 'renewal': True,
             'tags': ['t1', 't2'], 'locations': ['l1', 'l2']},
    'bare': {**BASE, 'id': next(ids), 'title': None, 'description': None, 'fee': None, 'color': None},
    'grouped': {**BASE, 'id': next(ids), 'title': 'Gruppo', 'group': 'own_group', 'tags': ['t2']},
    'gone': {**BASE, 'id': next(ids), 'title': 'Eliminato', 'deleted': True},
    'abroad': {**BASE, 'id': next(ids), 'association': FOREIGN, 'title': 'Estero'},
}
UNKNOWN = 'a1000000-0000-4000-8000-000000999997'
cases = []


def decimal(value):
    return None if value is None else str(Decimal(value).quantize(Decimal('0.01')))


def course(label):
    row = COURSES[label]
    return {'course_id': row['id'], 'athletes': None,
            'tags': [{'tag_id': TAGS[t]['id'], 'tag_name': TAGS[t]['name']} for t in row['tags']],
            'locations': [{'course_location_id': LOCATIONS[l]['id'], 'title': LOCATIONS[l]['title'], 'address': LOCATIONS[l]['address']}
                          for l in row['locations']],
            'status_flag': row['status'], 'course_type': row['type'], 'creation_date': row['created'], 'title': row['title'],
            'description': row['description'], 'multi_payments': row['multi_payments'], 'fee': decimal(row['fee']), 'events': row['events'],
            'one_fee_payment': row['one_fee_payment'], 'one_fee': decimal(row['one_fee']), 'google_calendar_id': row['google_calendar_id'],
            'google_background_color': row['color'], 'multiple_quotes': row['multiple_quotes'], 'start_date': row['start'],
            'end_date': row['end'], 'pinned': row['pinned'], 'billed_duration_is_sport_season': row['season'],
            'billed_frequency': row['frequency'], 'billed_from_subscription_date': row['from_subscription'],
            'billed_from_day_of_month': row['day'], 'auto_renewal': row['renewal'], 'deleted': row['deleted'],
            'sport_association': row['association'], 'group': IDS[row['group']] if row['group'] else None}


def add(name, actor, label, status, response, *, method='GET', headers=None):
    uid = COURSES[label]['id'] if label in COURSES else label
    cases.append({'name': f'course-overview-{name}', 'actor': actor, 'method': method, 'path': f'/course/{uid}/overview',
                  'headers': headers or {}, 'status': status, 'response': response})


DENIED = {'detail': 'Course not found'}
found = lambda label: {'data': {'course': course(label)}}
add('owner-full', 'owner', 'full', 200, found('full'))
add('owner-bare', 'owner', 'bare', 200, found('bare'))
add('full-collaborator', 'full_collaborator', 'full', 200, found('full'))
add('admin-impersonating-owner', 'admin', 'full', 200, found('full'),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 'abroad', 200, found('abroad'))
add('grouped-own-group', 'owner', 'grouped', 200, found('grouped'), headers={'X-Group-Id': IDS['own_group']})
add('grouped-without-group', 'owner', 'grouped', 200, found('grouped'))
add('group-header-hides', 'owner', 'grouped', 403, DENIED, headers={'X-Group-Id': IDS['foreign_group']})
add('malformed-group-header-ignored', 'owner', 'grouped', 200, found('grouped'), headers={'X-Group-Id': 'abc'})
add('foreign-course', 'owner', 'abroad', 403, DENIED)
add('deleted-course', 'owner', 'gone', 403, DENIED)
add('unknown-course', 'owner', UNKNOWN, 403, DENIED)
add('malformed', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('athlete-denied', 'athlete', 'full', 403, DENIED)
add('owner-without-association', 'owner_without_association', 'full', 403, DENIED)
add('restricted-missing-permission', 'restricted_collaborator', 'full', 403, {'detail': 'Missing permission: association.courses.read'})
add('restricted-post-missing-update', 'restricted_collaborator', 'full', 403, {'detail': 'Missing permission: association.courses.update'},
    method='POST')
add('owner-scoped-athlete-denied', 'owner', 'full', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('admin-without-session', 'admin', 'full', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'full', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'full', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 'full', 200, {'name': 'Course Overview', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course overview authored sequence', 'ids': IDS, 'tags': TAGS,
                              'locations': LOCATIONS, 'courses': COURSES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
