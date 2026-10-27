"""Author the attendance-day/<uid>/delete HTTP sequence from attendance-days.md.

The lookup is scoped_queryset(AttendanceDay) filtered by the course owner
(request.user, the scope user). A miss answers DRF's rendering of the set
literal {'error: attendance day not found.'}: a one-item list. AttendanceDay
is audited; after the delete it no longer resolves and its changes have no
sport_association, so the index falls back to the actor's association (none
for a collaborator). Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('attendance_day_delete_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{860001 + n:012d}' for n in range(20))
COURSES = {'own': {'id': next(ids), 'association': OWN}, 'foreign': {'id': next(ids), 'association': FOREIGN}}
REGISTRIES = {'own': {'id': next(ids), 'course': 'own'}, 'foreign': {'id': next(ids), 'course': 'foreign'}}
DAYS = {
    'd1': {'id': next(ids), 'registry': 'own', 'title': 'Lezione è', 'date': '2026-11-02T17:30:00Z',
           'attendees': [{'course_subscription_id': 'x', 'z': 1.50}], 'expected_absences': None, 'auto_marked': False, 'associated_event': next(ids)},
    'd2': {'id': next(ids), 'registry': 'own', 'title': None, 'date': '2026-11-03T17:30:00.250000Z',
           'attendees': None, 'expected_absences': [], 'auto_marked': True, 'associated_event': None},
    'df': {'id': next(ids), 'registry': 'foreign', 'title': 'Estero', 'date': '2026-11-04T10:00:00Z',
           'attendees': None, 'expected_absences': None, 'auto_marked': False, 'associated_event': None},
}
path = lambda key: f'/attendance-day/{DAYS[key]["id"] if key in DAYS else key}/delete'
NOT_FOUND = ['error: attendance day not found.']


def changes(key):
    day = DAYS[key]
    rendered = {'attendance_day_id': day['id'], 'attendance_registry': REGISTRIES[day['registry']]['id'], 'title': day['title'],
                'date': day['date'].rstrip('Z').replace('T', ' '),
                # auditlog renders JSONField values with json.dumps, so None is 'null' (never omitted).
                'attendees': json.dumps(day['attendees'], sort_keys=True),
                'expected_absences': json.dumps(day['expected_absences'], sort_keys=True),
                'auto_marked': str(day['auto_marked']), 'associated_event': day['associated_event']}
    return {name: [value, 'None'] for name, value in rendered.items() if value is not None}


cases = []


def add(name, actor, method, target, status, response, *, effect=None, headers=None):
    cases.append({'name': f'attendance-day-delete-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


add('malformed-id', 'owner', 'DELETE', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('foreign-not-found', 'owner', 'DELETE', 'df', 404, NOT_FOUND)
add('missing-not-found', 'owner', 'DELETE', IDS['missing'], 404, NOT_FOUND)
add('restricted-missing-permission', 'restricted_collaborator', 'DELETE', 'd1', 403,
    {'detail': 'Missing permission: association.courses.attendance.update'})
add('athlete-not-found', 'athlete', 'DELETE', 'd1', 404, NOT_FOUND)
add('admin-without-session', 'admin', 'DELETE', 'd1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'DELETE', 'd1', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'GET', 'd1', 405, {'detail': 'Method "GET" not allowed.'})
add('options', 'owner', 'OPTIONS', 'd1', 200, {'name': 'Attendance Day Delete', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
add('owner-deletes', 'owner', 'DELETE', 'd1', 200, {'data': {'msg': 'attendance day deleted'}},
    effect={'day': DAYS['d1']['id'], 'changes': changes('d1'), 'index': 'own_association'})
add('repeat-not-found', 'owner', 'DELETE', 'd1', 404, NOT_FOUND)
add('full-collaborator-deletes', 'full_collaborator', 'DELETE', 'd2', 200, {'data': {'msg': 'attendance day deleted'}},
    effect={'day': DAYS['d2']['id'], 'changes': changes('d2'), 'index': None})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Attendance day delete authored sequence with audit effects', 'ids': IDS,
                              'courses': COURSES, 'registries': REGISTRIES, 'days': DAYS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
