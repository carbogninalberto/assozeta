"""Author the attendance-day/<uid>/mark-absent HTTP sequence from attendance-days.md.

Builds on the attendance-day-delete seed plus subscriptions and course
subscriptions. The view edits the day's attendees/expected_absences lists in
memory, compares raw str() IDs (so an uppercase ID is appended as sent) and
saves; the "already in" answers return before saving. Saves are audited
(JSON fields as sorted json.dumps, 'null' for None) and indexed through the
attendance_day resolver. Set literals render as one-item lists. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('attendance_mark_absent_http.json')
BASE = json.loads(Path(__file__).with_name('attendance_day_delete_http.json').read_text())
IDS, COURSES, REGISTRIES = BASE['ids'], BASE['courses'], BASE['registries']
ids = iter(f'a1000000-0000-4000-8000-{870001 + n:012d}' for n in range(20))
SUBSCRIPTIONS = {'owner_sub': {'id': next(ids), 'association': 'own_association', 'user': 'owner'},
                 'athlete_sub': {'id': next(ids), 'association': 'own_association', 'user': 'athlete'},
                 'foreign_sub': {'id': next(ids), 'association': 'foreign_association', 'user': 'foreign_owner'},
                 # Subscription.objects has no soft-delete filter and the tax-code join ignores
                 # the associate's deletion: a deleted subscription of a deleted associate
                 # still extends the athlete's family visibility to an active relative.
                 'ghost_sub': {'id': next(ids), 'association': 'own_association', 'user': 'athlete', 'tax_code': 'FMLTAX80A01H501Z',
                               'deleted': True, 'associate_deleted': True},
                 'cousin_sub': {'id': next(ids), 'association': 'own_association', 'user': 'owner', 'tax_code': 'FMLTAX80A01H501Z'}}
COURSE_SUBSCRIPTIONS = {'cs1': {'id': next(ids), 'course': 'own', 'subscription': 'owner_sub'},
                        'cs2': {'id': next(ids), 'course': 'own', 'subscription': 'athlete_sub'},
                        'csf': {'id': next(ids), 'course': 'foreign', 'subscription': 'foreign_sub'},
                        'csc': {'id': next(ids), 'course': 'own', 'subscription': 'cousin_sub'}}
CS = {label: row['id'] for label, row in COURSE_SUBSCRIPTIONS.items()}
DAYS = {'day': {'id': next(ids), 'registry': 'own', 'title': 'Allenamento', 'date': '2026-11-05T18:00:00Z',
                'attendees': [{'course_subscription_id': CS['cs1']}], 'expected_absences': None, 'auto_marked': False, 'associated_event': None},
        'foreign_day': {'id': next(ids), 'registry': 'foreign', 'title': 'Estero', 'date': '2026-11-05T18:00:00Z',
                        'attendees': None, 'expected_absences': None, 'auto_marked': False, 'associated_event': None}}
path = lambda key: f'/attendance-day/{DAYS[key]["id"] if key in DAYS else key}/mark-absent'
SERVER = '<stable-server-error-page>'
dumps = lambda value: json.dumps(value, sort_keys=True)
entry = lambda value: [{'course_subscription_id': value}]
cases = []


def add(name, actor, target, status, response, *, body=None, method='POST', headers=None, effect=None):
    case = {'name': f'attendance-mark-absent-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    cases.append(case)


def saved(attendees, absences, changes, actor='owner', impersonation=None):
    return {'attendees': attendees, 'expected_absences': absences, 'changes': changes, 'actor': actor, 'impersonation': impersonation}


ABSENT = {'data': {'msg': 'added to expected_absences and removed from attendees.'}}
PRESENT = {'data': {'msg': 'removed from expected_absences and added to attendees.'}}
add('malformed-day', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'], body={})
add('foreign-day-not-found', 'owner', 'foreign_day', 404, ['error: attendance day not found.'], body={'course_subscription_id': CS['csf']})
add('missing-subscription-id', 'owner', 'day', 400, ['error: course_subscription_id is required.'], body={'absent': True})
add('malformed-subscription-id', 'owner', 'day', 400, ['badly formed hexadecimal UUID string'], body={'course_subscription_id': 'nope'})
add('foreign-course-subscription-denied', 'owner', 'day', 403, {'detail': 'Iscrizione al corso non disponibile.'},
    body={'course_subscription_id': CS['csf']})
add('list-body-server-error', 'owner', 'day', 500, SERVER, body=[])
add('owner-marks-absent', 'owner', 'day', 200, ABSENT, body={'course_subscription_id': CS['cs1']},
    effect=saved([], entry(CS['cs1']), {'attendees': [dumps(entry(CS['cs1'])), '[]'], 'expected_absences': ['null', dumps(entry(CS['cs1']))]}))
add('already-absent-no-save', 'owner', 'day', 200, ['error: course_subscription_id already in expected_absences.'],
    body={'course_subscription_id': CS['cs1'], 'absent': 1})
add('full-collaborator-marks-present', 'full_collaborator', 'day', 200, PRESENT, body={'course_subscription_id': CS['cs1'], 'absent': False},
    effect=saved(entry(CS['cs1']), [], {'attendees': ['[]', dumps(entry(CS['cs1']))], 'expected_absences': [dumps(entry(CS['cs1'])), '[]']},
                 actor='full_collaborator'))
add('already-present-no-save', 'owner', 'day', 200, ['error: course_subscription_id already in attendees.'],
    body={'course_subscription_id': CS['cs1'], 'absent': 0})
# Raw str() comparison: an uppercase ID never matches and is appended as sent.
UPPER = CS['cs2'].upper()
add('uppercase-id-appended-raw', 'owner', 'day', 200, ABSENT, body={'course_subscription_id': UPPER},
    effect=saved(entry(CS['cs1']), entry(UPPER), {'expected_absences': ['[]', dumps(entry(UPPER))]}))
# Athletes outside impersonation need the primary association; the fixture configures none.
add('athlete-without-primary-association', 'athlete', 'day', 404, ['error: attendance day not found.'], body={'course_subscription_id': CS['cs2']})
add('owner-scoped-athlete-foreign-subscription', 'owner', 'day', 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    body={'course_subscription_id': CS['cs1']}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('owner-scoped-athlete-marks-present', 'owner', 'day', 200, PRESENT, body={'course_subscription_id': CS['cs2'], 'absent': False},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']},
    effect=saved(entry(CS['cs1']) + entry(CS['cs2']), entry(UPPER),
                 {'attendees': [dumps(entry(CS['cs1'])), dumps(entry(CS['cs1']) + entry(CS['cs2']))]}, impersonation='owner_athlete'))
add('owner-scoped-athlete-family-through-deleted-subscription', 'owner', 'day', 200, PRESENT,
    body={'course_subscription_id': CS['csc'], 'absent': False}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']},
    effect=saved(entry(CS['cs1']) + entry(CS['cs2']) + entry(CS['csc']), entry(UPPER),
                 {'attendees': [dumps(entry(CS['cs1']) + entry(CS['cs2'])), dumps(entry(CS['cs1']) + entry(CS['cs2']) + entry(CS['csc']))]},
                 impersonation='owner_athlete'))
add('restricted-missing-permission', 'restricted_collaborator', 'day', 403, {'detail': 'Missing permission: association.courses.attendance.update'},
    body={'course_subscription_id': CS['cs1']})
add('admin-without-session', 'admin', 'day', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('anonymous', 'anonymous', 'day', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('wrong-method', 'owner', 'day', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'day', 200, {'name': 'Attendance Day Mark Absent', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Attendance mark-absent authored sequence with audit effects', 'ids': IDS,
                              'courses': COURSES, 'registries': REGISTRIES, 'days': DAYS, 'subscriptions': SUBSCRIPTIONS,
                              'course_subscriptions': COURSE_SUBSCRIPTIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
