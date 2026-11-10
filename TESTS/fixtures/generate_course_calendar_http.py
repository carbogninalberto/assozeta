"""Author the course calendar and full events calendar HTTP sequence (course-calendar.md).

Routes (BE/application/views/attendee_views.py):
- GET course/<uid>/calendar (`calendar`, AllowAny): is_valid_uuid, then the live
  group-aware course of any association (404 'Course not found.'); the first
  registry's events with `description` added to events that have extendedProps;
  `google_sync_enabled` is the scope user's Google credentials flag.
- POST/DELETE course/<uid>/calendar/update (`calendar_update`, IsAuthenticated +
  IsAssociationUser): POST validates the body keys and status, resolves the
  owner's association (a 500 without one) and course (404), updates the
  association's reminders by event id (instructors of request.user), then creates, replaces (DRAFT) or merges
  (PUBLISHED) the registry with its attendance days. POST is not atomic: work
  saved before an exception stays. DELETE (atomic) removes one event, a group
  of events (before True/False/all) or resets the registry to an empty draft.
- GET calendar/events (`full_events_calendar`): every registry event of the
  association's live courses (instructor collaborators: only their events),
  className as a one-item list (a tuple in the view), attendance day ids, then
  the association's global events when the actor may read events; the global
  calendar row is created on first read. Lesson details (get_lesson, the
  default) add get_extended_prop_for_attendance_day of the matched day
  (section 8).
- POST calendar/events/update (`full_events_calendar_update`, atomic):
  mutate_events validation and per-action event permissions, reminders scoped
  to the association, then the global calendar save.
Every write is checked against the complete state of the four calendar models
with auditlog entries and index rows. Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_calendar_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, GONE_ASSOCIATION = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
OWNER, FOREIGN_OWNER = IDS['owner'], IDS['foreign_owner']
_numbers = iter(range(960001, 960100))


def nid():
    return f'a1000000-0000-4000-8000-{next(_numbers):012d}'


def eid(prefix, n):
    return f'{prefix}000000-0000-4000-8000-{n:012d}'


SERVER = '<stable-server-error-page>'
DRAFT, PUBLISHED = 1, 2
COLORS = ['fc-event-primary fc-event-light-primary', 'fc-event-secondary fc-event-light-secondary',
          'fc-event-warning fc-event-light-warning', 'fc-event-danger fc-event-light-danger',
          'fc-event-info fc-event-light-info', 'fc-event-success fc-event-light-success',
          'fc-event-dark fc-event-light-dark']
LOGO = 'data:image/png;base64,QUJD'
CREDENTIALS = '{"token": "synthetic"}'

INSTRUCTORS = {
    'ines': {'id': nid(), 'user': OWNER, 'first_name': 'Ines', 'last_name': 'Uno', 'associated_user': IDS['restricted_collaborator']},
    'ivo': {'id': nid(), 'user': OWNER, 'first_name': 'Ivo', 'last_name': 'Due', 'associated_user': None},
    'ugo': {'id': nid(), 'user': FOREIGN_OWNER, 'first_name': 'Ugo', 'last_name': 'Tre', 'associated_user': None},
}
INES, IVO, UGO = INSTRUCTORS['ines']['id'], INSTRUCTORS['ivo']['id'], INSTRUCTORS['ugo']['id']

# Insertion order is the courses' heap order, which assigns the default colors.
COURSES = {
    'alpha': {'id': nid(), 'association': OWN, 'title': 'Corso Alfa', 'group': None, 'deleted': False, 'calendar': 'alpha@calendar.test'},
    'beta': {'id': nid(), 'association': OWN, 'title': 'Corso Beta', 'group': None, 'deleted': False, 'calendar': None},
    'gamma': {'id': nid(), 'association': OWN, 'title': 'Corso Gamma', 'group': None, 'deleted': False, 'calendar': None},
    'delta': {'id': nid(), 'association': OWN, 'title': 'Corso Delta', 'group': None, 'deleted': False, 'calendar': None},
    'epsilon': {'id': nid(), 'association': OWN, 'title': 'Corso Epsilon', 'group': None, 'deleted': False, 'calendar': None},
    'grouped': {'id': nid(), 'association': OWN, 'title': 'Corso Gruppo', 'group': 'own_group', 'deleted': False, 'calendar': None},
    'gone': {'id': nid(), 'association': OWN, 'title': 'Corso Eliminato', 'group': None, 'deleted': True, 'calendar': None},
    'abroad': {'id': nid(), 'association': FOREIGN, 'title': 'Corso Estero', 'group': None, 'deleted': False, 'calendar': None},
    'abroad2': {'id': nid(), 'association': FOREIGN, 'title': 'Corso Estero 2', 'group': None, 'deleted': False, 'calendar': None},
    'broken': {'id': nid(), 'association': GONE_ASSOCIATION, 'title': 'Corso Rotto', 'group': None, 'deleted': False, 'calendar': None},
}
C = {label: row['id'] for label, row in COURSES.items()}
EA = {n: eid('a0', n) for n in range(1, 8)}
EB = {1: eid('b0', 1), 2: 'B0000000-0000-4000-8000-000000000002'}
EC = {1: eid('c0', 1)}
ED = {n: eid('d0', n) for n in range(1, 3)}
EG = {n: eid('e0', n) for n in range(1, 4)}
EX, EF, EBR, EZ = eid('f0', 1), eid('f1', 1), eid('f2', 1), eid('f3', 1)
GL = {n: eid('90', n) for n in range(1, 6)}
SEED_CREATED = '2026-10-01T08:00:00Z'


def event(event_id, title, start, end=None, *, all_day=False, props=None, extra=None):
    row = {'event_id': event_id, 'title': title, 'start': start, 'end': end, 'allDay': all_day}
    if props is not None:
        row['extendedProps'] = props
    row.update(extra or {})
    return row


A1 = event(EA[1], 'Lezione 1', '2026-11-02T17:00:00.000Z', '2026-11-02T18:00:00.000Z',
           props={'instructor': {'instructor_id': INES, 'label': 'Ines Uno'}, 'groupId': 'gA'})
A2 = event(EA[2], 'Lezione 2', '2026-11-09T17:00:00.000Z', '2026-11-09T18:00:00.000Z',
           props={'groupId': 'gA', 'reminder_enabled': True, 'reminder_amount': 1, 'reminder_unit': 'hours', 'description': 'Portare scarpe'})
A3 = event(EA[3], 'Lezione 3', '2026-11-16T17:00:00.000Z', '2026-11-16T18:00:00.000Z', props={'groupId': 'gA'},
           extra={'className': 'fc-custom'})
A4 = event(EA[4], 'Recupero', '2026-11-20T09:00:00.000Z', None, all_day=True)
D1 = event(ED[1], 'Delta 1', '2026-10-30T16:00:00.000Z', '2026-10-30T17:00:00.000Z', props={})
G1 = event(EG[1], 'Gruppo 1', '2026-11-11T10:00:00.000Z', '2026-11-11T11:00:00.000Z', props={'groupId': 'gG'})
G2 = event(EG[2], 'Gruppo 2', '2026-11-12T10:00:00.000Z', '2026-11-12T11:00:00.000Z', props={'groupId': 'gG'})
G3 = event(EG[3], 'Gruppo 3', '2026-11-13T10:00:00.000Z', '2026-11-13T11:00:00.000Z',
           props={'groupId': 'gG', 'reminder_enabled': True, 'reminder_amount': 15, 'reminder_unit': 'minutes'})
X1 = event(EX, 'Fantasma', '2026-11-03T10:00:00.000Z', '2026-11-03T11:00:00.000Z', props={})
F1 = event(EF, 'Estero 1', '2026-11-04T10:00:00.000Z', '2026-11-04T11:00:00.000Z', props={'description': 'Fuori sede'})
BR = {'event_id': EBR, 'title': 'Rotto', 'start': '2026-11-05T10:00:00.000Z', 'extendedProps': None}

REGISTRY_IDS = {label: nid() for label in ('alpha', 'delta', 'epsilon', 'grouped', 'gone', 'abroad', 'broken')}
REGISTRIES = {
    REGISTRY_IDS['alpha']: {'course': C['alpha'], 'status': PUBLISHED, 'events': [A1, A2, A3, A4], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['delta']: {'course': C['delta'], 'status': DRAFT, 'events': [D1], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['epsilon']: {'course': C['epsilon'], 'status': 3, 'events': [], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['grouped']: {'course': C['grouped'], 'status': PUBLISHED, 'events': [G1, G2, G3], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['gone']: {'course': C['gone'], 'status': PUBLISHED, 'events': [X1], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['abroad']: {'course': C['abroad'], 'status': PUBLISHED, 'events': [F1], 'creation_date': SEED_CREATED},
    REGISTRY_IDS['broken']: {'course': C['broken'], 'status': PUBLISHED, 'events': [BR], 'creation_date': SEED_CREATED},
}
R = REGISTRY_IDS


def day(registry, title, date, associated, attendees=None):
    return {'registry': registry, 'title': title, 'date': date, 'associated_event': associated, 'attendees': attendees,
            'expected_absences': None, 'auto_marked': False}


DAY_IDS = {label: nid() for label in ('a1', 'a2', 'a3', 'a4', 'g3')}
DAYS = {
    DAY_IDS['a1']: day(R['alpha'], 'Lezione 1', '2026-11-02T17:00:00Z', EA[1], attendees=[]),
    DAY_IDS['a2']: day(R['alpha'], 'Lezione 2', '2026-11-09T17:00:00Z', EA[2]),
    # A historical row without associated_event: matched by title and date.
    DAY_IDS['a3']: day(R['alpha'], 'Lezione 3', '2026-11-16T17:00:00Z', None),
    DAY_IDS['a4']: day(R['alpha'], 'Recupero', '2026-11-20T09:00:00Z', EA[4]),
    DAY_IDS['g3']: day(R['grouped'], 'Gruppo 3', '2026-11-13T10:00:00Z', EG[3]),
}
DY = DAY_IDS


def reminder(event_id, title, description, course_title, text, send_at, *, user=OWNER, instructor=None, association=OWN):
    return {'event_id': event_id, 'event_title': title, 'event_description': description, 'event_course_title': course_title,
            'event_reminder_text': text, 'send_at': send_at, 'user': user, 'instructor': instructor, 'completed': False,
            'sport_association': association}


REMINDER_IDS = {label: nid() for label in ('a2', 'gl1', 'g3', 'foreign', 'foreign_z')}
REMINDERS = {
    REMINDER_IDS['a2']: reminder(EA[2], 'Lezione 2', 'Portare scarpe', None, '1 ore', '2026-11-09T16:00:00Z'),
    REMINDER_IDS['gl1']: reminder(GL[1], 'Assemblea', '', None, '2 giorni', '2026-11-03T19:00:00Z'),
    REMINDER_IDS['g3']: reminder(EG[3], 'Gruppo 3', '', None, '15 minuti', '2026-11-13T09:45:00Z'),
    REMINDER_IDS['foreign']: reminder(EF, 'Estero 1', 'Fuori sede', None, '1 ore', '2026-11-04T09:00:00Z',
                                      user=FOREIGN_OWNER, association=FOREIGN),
    REMINDER_IDS['foreign_z']: reminder(EZ, 'Zeta 1', '', None, '2 ore', '2026-11-05T08:00:00Z', user=FOREIGN_OWNER, association=FOREIGN),
}
RM = REMINDER_IDS

SEED = {'instructors': INSTRUCTORS, 'courses': COURSES, 'registries': REGISTRIES, 'days': DAYS, 'reminders': REMINDERS,
        'logo': LOGO, 'credentials': CREDENTIALS,
        'restricted_permissions': ['association.calendar.read', 'association.events.update']}
STATE = {'registries': copy.deepcopy(REGISTRIES), 'days': copy.deepcopy(DAYS), 'reminders': copy.deepcopy(REMINDERS), 'globals': {}}
cases = []


def snapshot():
    return {kind: [{'ref': ref, **row} for ref, row in rows.items()] for kind, rows in STATE.items()}


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, audit=None):
    case = {'name': f'course-calendar-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': None if audit is None else {'state': snapshot(), 'audit': audit}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# ---------------------------------------------------------------------------
# expected auditlog entries ('$new:<tag>' is a row created by the run,
# '$created:<tag>' its creation_date as auditlog renders it)
# ---------------------------------------------------------------------------

def dumps(value):
    return json.dumps(value, sort_keys=True)


def naive(moment):
    """auditlog's str() of a naive UTC datetime from an ISO 'Z' state value."""
    return moment.replace('T', ' ').removesuffix('Z')


def entry(model, action, key, changes, actor, index):
    return {'model': model, 'action': action, 'key': key, 'changes': changes, 'actor': actor, 'index': index}


def registry_create(ref, actor, status_repr, association=OWN):
    row = STATE['registries'][ref]
    return entry('attendanceregistry', 0, ref, {
        'attendance_registry_id': ['None', ref], 'course': ['None', row['course']], 'creation_date': ['None', '$created:' + ref[5:]],
        'events': ['None', dumps(row['events'])], 'status': ['None', status_repr]}, actor, [association, 'course.sport_association'])


def registry_update(ref, actor, changes):
    return entry('attendanceregistry', 1, ref, changes, actor, [OWN, 'course.sport_association'])


def registry_delete(ref, actor, row, actor_index):
    created = naive(row['creation_date']) if not row['creation_date'].startswith('$') else '$created:' + ref[5:]
    return entry('attendanceregistry', 2, ref, {
        'attendance_registry_id': [ref, 'None'], 'course': [row['course'], 'None'], 'creation_date': [created, 'None'],
        'events': [dumps(row['events']), 'None'], 'status': [str(row['status']), 'None']}, actor, actor_index)


def json_repr(value):
    return 'null' if value is None else dumps(value)


def day_create(ref, actor, associated_repr):
    row = STATE['days'][ref]
    changes = {'attendance_day_id': ['None', ref], 'attendance_registry': ['None', row['registry']], 'title': ['None', row['title']],
               'date': ['None', naive(row['date'])], 'attendees': ['None', 'null'], 'expected_absences': ['None', 'null'],
               'auto_marked': ['None', 'False']}
    if associated_repr is not None:
        changes['associated_event'] = ['None', associated_repr]
    return entry('attendanceday', 0, ref, changes, actor, [OWN, 'attendance_registry.course.sport_association'])


def day_update(ref, actor, changes):
    return entry('attendanceday', 1, ref, changes, actor, [OWN, 'attendance_registry.course.sport_association'])


def day_delete(ref, actor, actor_index):
    row = STATE['days'].pop(ref)
    changes = {'attendance_day_id': [ref, 'None'], 'attendance_registry': [row['registry'], 'None'], 'title': [row['title'], 'None'],
               'date': [naive(row['date']), 'None'], 'attendees': [json_repr(row['attendees']), 'None'],
               'expected_absences': [json_repr(row['expected_absences']), 'None'], 'auto_marked': [str(row['auto_marked']), 'None']}
    if row['associated_event'] is not None:
        changes['associated_event'] = [row['associated_event'], 'None']
    return entry('attendanceday', 2, ref, changes, actor, actor_index)


def reminder_fields(row):
    values = {'event_id': row['event_id'], 'event_title': row['event_title'], 'event_description': row['event_description'],
              'event_course_title': row['event_course_title'], 'event_reminder_text': row['event_reminder_text'],
              'send_at': naive(row['send_at']), 'user': row['user'], 'instructor': row['instructor'],
              'completed': str(row['completed']), 'sport_association': row['sport_association']}
    return values


def reminder_create(ref, actor):
    changes = {'reminders_id': ['None', ref]}
    for field, value in reminder_fields(STATE['reminders'][ref]).items():
        if value is not None:
            changes[field] = ['None', value]
        elif field == 'instructor':
            changes[field] = ['None', 'None']
    return entry('reminders', 0, ref, changes, actor, [STATE['reminders'][ref]['sport_association'], 'direct'])


def reminder_update(ref, actor, changes):
    return entry('reminders', 1, ref, changes, actor, [STATE['reminders'][ref]['sport_association'], 'direct'])


def reminder_delete(ref, actor):
    row = STATE['reminders'].pop(ref)
    changes = {'reminders_id': [ref, 'None']}
    for field, value in reminder_fields(row).items():
        if value is not None:
            changes[field] = [value, 'None']
        elif field == 'instructor':
            changes[field] = ['None', 'None']
    return entry('reminders', 2, ref, changes, actor, [row['sport_association'], 'changes_json.sport_association'])


def global_create(ref, actor, association):
    STATE['globals'][ref] = {'sport_association': association, 'events': None}
    return entry('globalcalendarevents', 0, ref, {'global_calendar_id': ['None', ref], 'sport_association': ['None', association],
                                                  'events': ['None', 'null']}, actor, [association, 'direct'])


def global_save(ref, actor, events):
    row = STATE['globals'][ref]
    old, row['events'] = row['events'], copy.deepcopy(events)
    return entry('globalcalendarevents', 1, ref, {'events': [json_repr(old), dumps(events)]}, actor, [row['sport_association'], 'direct'])


# ---------------------------------------------------------------------------
# shared expectations
# ---------------------------------------------------------------------------

ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED_ATHLETE = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NOT_ASSOCIATION = {'detail': 'You do not have permission to perform this action.'}
MALFORMED = ['badly formed hexadecimal UUID string']
COURSE_NOT_FOUND = {'detail': 'Course not found.'}
UPDATED = {'message': 'Calendar updated.'}
DELETED = {'message': 'Event deleted.'}
DRAFT_ONLY = {'msg': 'cannot delete a draft.'}
ADDED = {'message': 'Events added.'}
UNKNOWN = 'a1000000-0000-4000-8000-000000960999'
GROUP = lambda key: {'X-Group-Id': IDS[key]}
SESSION = lambda label, target: {'X-Impersonation-Id': f'$session:{label}', 'User-Id': IDS[target]}


def missing(permission):
    return {'detail': f'Missing permission: {permission}'}


def options(name, description):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def described(course, row):
    """calendar: events with extendedProps but no description gain one."""
    row = copy.deepcopy(row)
    if 'extendedProps' in row and 'description' not in row['extendedProps']:
        row['extendedProps']['description'] = f"{COURSES[course]['title']} - {row['title']}"
    return row


def course_calendar(course, status, events, *, sync, association='Message reads own', logo=LOGO):
    return {'data': {'status': status, 'events': [described(course, row) for row in events], 'course': {'title': COURSES[course]['title']},
                     'sport_association': {'denomination': association, 'logo': logo},
                     'google_calendar_id': COURSES[course]['calendar'], 'google_sync_enabled': sync}}


def listed(course, row, color, day=None):
    """full_events_calendar: one registry event as rendered."""
    row = copy.deepcopy(row)
    row.setdefault('extendedProps', {})
    if 'className' not in row:
        row['className'] = [color]
    if day is not None:
        row['extendedProps']['attendace_day_id'] = day
    row['extendedProps']['course'] = C[course]
    row['extendedProps'].setdefault('description', f"{COURSES[course]['title']} - {row['title']}")
    row['id'] = row['event_id']
    return row


def global_listed(row):
    return {**copy.deepcopy(row), 'id': row['event_id']}


def events(rows):
    return {'data': {'events': rows}}


CAL = lambda course: f"/course/{C[course] if course in C else course}/calendar"
UPD = lambda course: f"/course/{C[course] if course in C else course}/calendar/update"
EVENTS, EVENTS_UPDATE = '/calendar/events', '/calendar/events/update'
LIST = EVENTS + '?get_lesson=false'

# ===========================================================================
# 1. authorization, methods, OPTIONS and validation (no state change)
# ===========================================================================

add('calendar-admin-without-session', 'admin', CAL('alpha'), 403, ADMIN)
add('calendar-restricted-missing-permission', 'restricted_collaborator', CAL('alpha'), 403, missing('association.courses.read'))
add('calendar-owner-scoped-athlete-denied', 'owner', CAL('alpha'), 403, SCOPED_ATHLETE, headers=SESSION('owner_athlete', 'athlete'))
add('calendar-wrong-method', 'owner', CAL('alpha'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('calendar-anonymous-wrong-method', 'anonymous', CAL('alpha'), 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('calendar-options', 'owner', CAL('alpha'), 200, options('Calendar', 'API endpoint to get the calendar for the current course'),
    method='OPTIONS')
add('calendar-malformed', 'owner', CAL('abc'), 400, MALFORMED)
add('calendar-anonymous-malformed', 'anonymous', CAL('abc'), 400, MALFORMED)
add('calendar-unknown', 'owner', CAL(UNKNOWN), 404, COURSE_NOT_FOUND)
add('calendar-deleted-course', 'anonymous', CAL('gone'), 404, COURSE_NOT_FOUND)
add('calendar-group-header-hides', 'owner', CAL('grouped'), 404, COURSE_NOT_FOUND, headers=GROUP('foreign_group'))
add('calendar-scoped-session-hides-foreign', 'owner', CAL('abroad'), 404, COURSE_NOT_FOUND, headers=SESSION('owner_full', 'full_collaborator'))
add('calendar-broken-extended-props', 'owner', CAL('broken'), 500, SERVER)

add('update-anonymous', 'anonymous', UPD('alpha'), 401, ANONYMOUS, method='POST', body={'events': [], 'status': 1})
add('update-admin-without-session', 'admin', UPD('alpha'), 403, ADMIN, method='POST', body={'events': [], 'status': 1})
add('update-restricted-missing-permission', 'restricted_collaborator', UPD('alpha'), 403, missing('association.courses.update'),
    method='POST', body={'events': [], 'status': 1})
add('update-delete-restricted-missing-permission', 'restricted_collaborator', UPD('alpha'), 403, missing('association.courses.update'),
    method='DELETE', body={})
add('update-owner-scoped-athlete-denied', 'owner', UPD('alpha'), 403, SCOPED_ATHLETE, method='POST', body={'events': [], 'status': 1},
    headers=SESSION('owner_athlete', 'athlete'))
add('update-athlete-not-association', 'athlete', UPD('alpha'), 403, NOT_ASSOCIATION, method='POST', body={'events': [], 'status': 1})
add('update-athlete-options-not-association', 'athlete', UPD('alpha'), 403, NOT_ASSOCIATION, method='OPTIONS')
add('update-wrong-method', 'owner', UPD('alpha'), 405, {'detail': 'Method "GET" not allowed.'})
add('update-options', 'owner', UPD('alpha'), 200, options('Calendar Update', 'API endpoint to update the calendar of a course'),
    method='OPTIONS')
add('update-malformed', 'owner', UPD('abc'), 400, MALFORMED, method='POST', body={})
add('update-missing-body-keys', 'owner', UPD('alpha'), 400, ['Missing or wrong events data.'], method='POST', body={})
add('update-missing-status', 'owner', UPD('alpha'), 400, ['Missing or wrong events data.'], method='POST', body={'events': []})
add('update-two-other-keys', 'owner', UPD('alpha'), 500, SERVER, method='POST', body={'events': [], 'state': 2})
add('update-invalid-status', 'owner', UPD('alpha'), 400, ['Status not valid.'], method='POST', body={'events': [], 'status': 3})
add('update-string-status', 'full_collaborator', UPD('alpha'), 400, ['Status not valid.'], method='POST', body={'events': [], 'status': '2'})
add('update-list-body', 'owner', UPD('alpha'), 500, SERVER, method='POST', body=[{'events': []}])
add('update-without-association', 'owner_without_association', UPD('alpha'), 500, SERVER, method='POST', body={'events': [], 'status': 1})
add('update-without-association-invalid-body', 'owner_without_association', UPD('alpha'), 400, ['Status not valid.'], method='POST',
    body={'events': [], 'status': 0})
add('update-unknown-course', 'owner', UPD(UNKNOWN), 404, COURSE_NOT_FOUND, method='POST', body={'events': [], 'status': 1})
add('update-foreign-course', 'owner', UPD('abroad'), 404, COURSE_NOT_FOUND, method='POST', body={'events': [], 'status': 1})
add('update-deleted-course', 'owner', UPD('gone'), 404, COURSE_NOT_FOUND, method='POST', body={'events': [], 'status': 1})
add('update-group-header-hides', 'owner', UPD('grouped'), 404, COURSE_NOT_FOUND, method='POST', body={'events': [], 'status': 1},
    headers=GROUP('foreign_group'))
add('update-event-without-id', 'owner', UPD('beta'), 500, SERVER, method='POST', body={'events': [{'title': 'Senza id'}], 'status': 1})
add('update-event-invalid-id', 'owner', UPD('beta'), 500, SERVER, method='POST',
    body={'events': [{'event_id': 'nope', 'title': 'Id errato', 'start': '2026-11-03T18:00:00.000Z'}], 'status': 1})
add('update-events-not-list', 'owner', UPD('beta'), 500, SERVER, method='POST', body={'events': 'abc', 'status': 1})
add('update-malformed-json', 'owner', UPD('alpha'), 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    method='POST', raw_body='nope', content_type='application/json')
add('update-malformed-uid-before-json', 'owner', UPD('abc'), 400, MALFORMED, method='POST', raw_body='nope', content_type='application/json')
add('update-delete-anonymous', 'anonymous', UPD('alpha'), 401, ANONYMOUS, method='DELETE', body={})
add('update-delete-athlete', 'athlete', UPD('alpha'), 403, NOT_ASSOCIATION, method='DELETE', body={})
add('update-delete-malformed', 'owner', UPD('abc'), 400, MALFORMED, method='DELETE', body={})
add('update-delete-draft', 'owner', UPD('delta'), 400, DRAFT_ONLY, method='DELETE', body={'event_id': ED[1]})
add('update-delete-unknown-course', 'owner', UPD(UNKNOWN), 400, DRAFT_ONLY, method='DELETE', body={})
add('update-delete-foreign-course', 'owner', UPD('abroad'), 400, DRAFT_ONLY, method='DELETE', body={'event_id': EF})
add('update-delete-status-three', 'owner', UPD('epsilon'), 400, DRAFT_ONLY, method='DELETE', body={})
add('update-delete-without-association', 'owner_without_association', UPD('alpha'), 500, SERVER, method='DELETE', body={})
add('update-delete-unknown-event', 'owner', UPD('alpha'), 200, DELETED, method='DELETE', body={'event_id': UNKNOWN})
add('update-delete-list-body', 'owner', UPD('alpha'), 500, SERVER, method='DELETE', body=[EA[1]])

add('events-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('events-admin-without-session', 'admin', LIST, 403, ADMIN)
add('events-athlete-not-association', 'athlete', LIST, 403, NOT_ASSOCIATION)
add('events-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_ATHLETE, headers=SESSION('owner_athlete', 'athlete'))
add('events-wrong-method', 'owner', EVENTS, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('events-options', 'owner', EVENTS, 200, options('Full Events Calendar', 'API endpoint to get the attendance registry for the current course'),
    method='OPTIONS')
add('events-invalid-start', 'owner', EVENTS + '?start=abc&get_lesson=false', 400, ['Invalid calendar date.'])
add('events-invalid-end', 'full_collaborator', EVENTS + '?start=2026-11-01T00:00:00Z&end=2026-13-01', 400, ['Invalid calendar date.'])
add('events-without-association', 'owner_without_association', LIST, 500, SERVER)
add('events-deleted-association', 'deleted_association_owner', LIST, 500, SERVER)

add('global-anonymous', 'anonymous', EVENTS_UPDATE, 401, ANONYMOUS, method='POST', body={'events': []})
add('global-admin-without-session', 'admin', EVENTS_UPDATE, 403, ADMIN, method='POST', body={'events': []})
add('global-athlete-not-association', 'athlete', EVENTS_UPDATE, 403, NOT_ASSOCIATION, method='POST', body={'events': []})
add('global-wrong-method', 'owner', EVENTS_UPDATE, 405, {'detail': 'Method "GET" not allowed.'})
add('global-delete-method', 'owner', EVENTS_UPDATE, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE', body={})
add('global-options', 'restricted_collaborator', EVENTS_UPDATE, 200, options('Full Events Calendar Update', ''), method='OPTIONS')
add('global-without-association', 'owner_without_association', EVENTS_UPDATE, 500, SERVER, method='POST', body={'events': []})


def invalid(name, body, message, *, actor='owner'):
    add(f'global-{name}', actor, EVENTS_UPDATE, 400, [message], method='POST', body=body)


NEW_GLOBAL = {'event_id': GL[3], 'title': 'Cena sociale', 'start': '2026-12-01T20:00:00Z'}
invalid('list-body', [NEW_GLOBAL], 'Calendar payload must be an object.')
invalid('events-not-list', {'events': {'a': 1}}, 'Events must be a list of objects.')
invalid('event-not-object', {'events': [NEW_GLOBAL, 'x']}, 'Events must be a list of objects.')
invalid('missing-event-id', {'events': [{'title': 'Senza id', 'start': '2026-12-01T20:00:00Z'}]}, 'Invalid event ID.')
invalid('invalid-event-id', {'events': [{**NEW_GLOBAL, 'event_id': 'nope'}]}, 'Invalid event ID.')
invalid('numeric-event-id', {'events': [{**NEW_GLOBAL, 'event_id': 5}]}, 'Invalid event ID.')
invalid('duplicate-event-id', {'events': [NEW_GLOBAL, NEW_GLOBAL]}, 'Duplicate event ID.')
invalid('blank-title', {'events': [{**NEW_GLOBAL, 'title': '   '}]}, 'Event title is required.')
invalid('numeric-title', {'events': [{**NEW_GLOBAL, 'title': 5}]}, 'Event title is required.')
invalid('invalid-start', {'events': [{**NEW_GLOBAL, 'start': '2026-02-30T10:00:00Z'}]}, 'Invalid calendar date.')
invalid('missing-start', {'events': [{'event_id': GL[3], 'title': 'Senza data'}]}, 'Invalid calendar date.')
invalid('invalid-end', {'events': [{**NEW_GLOBAL, 'end': 'domani'}]}, 'Invalid calendar date.')
invalid('end-precedes-start', {'events': [{**NEW_GLOBAL, 'end': '2026-12-01T21:00:00+02:00'}]}, 'Event end precedes start.')
invalid('course-property', {'events': [{**NEW_GLOBAL, 'extendedProps': {'course': C['alpha']}}]}, 'Invalid global event properties.')
invalid('list-properties', {'events': [{**NEW_GLOBAL, 'extendedProps': []}]}, 'Invalid global event properties.')
invalid('reminder-amount', {'events': [{**NEW_GLOBAL, 'extendedProps': {'reminder_enabled': True, 'reminder_amount': 'dieci',
                                                                      'reminder_unit': 'minutes'}}]}, 'Invalid reminder amount.')
invalid('reminder-unit', {'events': [{**NEW_GLOBAL, 'extendedProps': {'reminder_enabled': True, 'reminder_amount': 1,
                                                                    'reminder_unit': 'weeks'}}]}, 'Invalid reminder settings.')
invalid('reminder-boolean-amount', {'events': [{**NEW_GLOBAL, 'extendedProps': {'reminder_enabled': True, 'reminder_amount': True,
                                                                              'reminder_unit': 'days'}}]}, 'Invalid reminder settings.')
invalid('reminder-amount-too-large', {'events': [{**NEW_GLOBAL, 'extendedProps': {'reminder_enabled': 1, 'reminder_amount': 525601,
                                                                                'reminder_unit': 'minutes'}}]}, 'Invalid reminder settings.')
invalid('create-two-events', {'action': 'create', 'events': [NEW_GLOBAL, {**NEW_GLOBAL, 'event_id': GL[4]}]}, 'Exactly one event is required.')
invalid('delete-without-id', {'action': 'delete', 'events': []}, 'Event ID is required.')
invalid('delete-numeric-id', {'action': 'delete', 'event_id': 5}, 'Event ID is required.')
invalid('unknown-action', {'action': 'archive', 'events': [NEW_GLOBAL]}, 'Invalid calendar action.')
invalid('no-events-no-action', {}, 'Invalid calendar action.')
add('global-update-unknown', 'owner', EVENTS_UPDATE, 404, {'detail': 'Event not found.'}, method='POST',
    body={'action': 'update', 'events': [NEW_GLOBAL]})
add('global-restricted-create-denied', 'restricted_collaborator', EVENTS_UPDATE, 403, {'detail': 'Missing event permission.'},
    method='POST', body={'action': 'create', 'events': [NEW_GLOBAL]})
add('global-restricted-snapshot-create-denied', 'restricted_collaborator', EVENTS_UPDATE, 403, {'detail': 'Missing event permission.'},
    method='POST', body={'events': [NEW_GLOBAL]})
add('global-form-body', 'owner', EVENTS_UPDATE, 400, ['Events must be a list of objects.'], method='POST',
    raw_body='events=abc', content_type='application/x-www-form-urlencoded')

# ===========================================================================
# 2. the first global calendar write creates the association's row
# ===========================================================================

GLOBAL_1 = {'event_id': GL[1], 'title': 'Assemblea', 'start': '2026-11-05T19:00:00Z', 'end': '2026-11-05T21:00:00Z', 'allDay': False,
            'extendedProps': {'reminder_enabled': True, 'reminder_amount': 2, 'reminder_unit': 'days'}}
GLOBAL_2 = {'event_id': GL[2], 'title': 'Festa', 'start': '2026-12-20', 'allDay': True}
OWN_GLOBAL = '$new:global-own'
# get_or_create, the unchanged reminder of GLOBAL_1 (no entry), then save().
audit = [global_create(OWN_GLOBAL, 'owner', OWN)]
audit.append(global_save(OWN_GLOBAL, 'owner', [GLOBAL_1, GLOBAL_2]))
add('global-snapshot-creates-calendar', 'owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'events': [GLOBAL_1, GLOBAL_2]}, audit=audit)

# ===========================================================================
# 3. reads of the seeded registries
# ===========================================================================

add('calendar-anonymous', 'anonymous', CAL('alpha'), 200, course_calendar('alpha', PUBLISHED, [A1, A2, A3, A4], sync=False))
add('calendar-owner', 'owner', CAL('alpha'), 200, course_calendar('alpha', PUBLISHED, [A1, A2, A3, A4], sync=True))
add('calendar-full-collaborator', 'full_collaborator', CAL('alpha'), 200, course_calendar('alpha', PUBLISHED, [A1, A2, A3, A4], sync=True))
add('calendar-athlete', 'athlete', CAL('alpha'), 200, course_calendar('alpha', PUBLISHED, [A1, A2, A3, A4], sync=False))
add('calendar-admin-session', 'admin', CAL('alpha'), 200, course_calendar('alpha', PUBLISHED, [A1, A2, A3, A4], sync=True),
    headers=SESSION('admin_owner', 'owner'))
add('calendar-without-registry', 'owner', CAL('beta'), 200, course_calendar('beta', DRAFT, [], sync=True))
add('calendar-draft-registry', 'owner', CAL('delta'), 200, course_calendar('delta', DRAFT, [D1], sync=True))
add('calendar-odd-status', 'owner', CAL('epsilon'), 200, course_calendar('epsilon', 3, [], sync=True))
add('calendar-group-header', 'owner', CAL('grouped'), 200, course_calendar('grouped', PUBLISHED, [G1, G2, G3], sync=True),
    headers=GROUP('own_group'))
add('calendar-malformed-group-ignored', 'owner', CAL('grouped'), 200, course_calendar('grouped', PUBLISHED, [G1, G2, G3], sync=True),
    headers={'X-Group-Id': 'not-a-group'})
add('calendar-foreign-course', 'owner', CAL('abroad'), 200,
    course_calendar('abroad', PUBLISHED, [F1], sync=True, association='Message reads foreign', logo=None))

OWN_COLORS = {label: COLORS[i] for i, label in enumerate(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'grouped'])}


def own_listing(*, alpha=(A1, A2, A3, A4), extra=()):
    days = {EA[1]: DY['a1'], EA[2]: DY['a2'], EA[3]: DY['a3'], EA[4]: DY['a4'], EG[3]: DY['g3']}
    rows = [listed('alpha', row, OWN_COLORS['alpha'], days.get(row['event_id'])) for row in alpha]
    rows += [listed('delta', D1, OWN_COLORS['delta'])]
    rows += [listed('grouped', row, OWN_COLORS['grouped'], days.get(row['event_id'])) for row in (G1, G2, G3)]
    return rows + list(extra)


GLOBALS = [global_listed(GLOBAL_1), global_listed(GLOBAL_2)]
add('events-owner', 'owner', LIST, 200, events(own_listing(extra=GLOBALS)))
add('events-full-collaborator', 'full_collaborator', EVENTS + '?get_lesson=FALSE', 200, events(own_listing(extra=GLOBALS)))
add('events-instructor-collaborator', 'restricted_collaborator', LIST, 200,
    events([listed('alpha', A1, COLORS[0], DY['a1'])]))
add('events-group-header', 'owner', LIST, 200,
    events([listed('grouped', G1, COLORS[0]), listed('grouped', G2, COLORS[0]), listed('grouped', G3, COLORS[0], DY['g3'])] + GLOBALS),
    headers=GROUP('own_group'))
add('events-range', 'owner', EVENTS + '?start=2026-11-08T00:00:00Z&end=2026-11-18T00:00:00Z&get_lesson=0', 200,
    events([listed('alpha', A2, OWN_COLORS['alpha'], DY['a2']), listed('alpha', A3, OWN_COLORS['alpha'], DY['a3'])]
           + [listed('grouped', G1, OWN_COLORS['grouped']), listed('grouped', G2, OWN_COLORS['grouped']),
              listed('grouped', G3, OWN_COLORS['grouped'], DY['g3'])]))
add('events-start-only', 'owner', EVENTS + '?start=2026-11-10T00:00:00Z&get_lesson=no', 200,
    events(own_listing(extra=[global_listed(GLOBAL_2)])))
add('events-end-only', 'full_collaborator', EVENTS + '?end=2026-11-06T00:00:00%2B01:00&get_lesson=false', 200,
    events(own_listing(extra=[global_listed(GLOBAL_1)])))
add('events-by-id-without-day', 'owner', EVENTS + f'?event_id={ED[1]}&get_lesson=true', 200, events([listed('delta', D1, OWN_COLORS['delta'])]))
add('events-by-global-id', 'owner', EVENTS + f'?event_id={GL[2]}', 200, events([global_listed(GLOBAL_2)]))
add('events-by-unknown-id', 'owner', EVENTS + f'?event_id={UNKNOWN}', 200, events([]))
add('events-scoped-collaborator-session', 'owner', LIST, 200, events(own_listing(extra=GLOBALS)),
    headers=SESSION('owner_full', 'full_collaborator'))

FOREIGN_GLOBAL = '$new:global-foreign'
audit = [global_create(FOREIGN_GLOBAL, 'foreign_owner', FOREIGN)]
add('events-foreign-creates-calendar', 'foreign_owner', LIST, 200, events([listed('abroad', F1, COLORS[0])]), audit=audit)
add('events-foreign-again', 'foreign_owner', LIST, 200, events([listed('abroad', F1, COLORS[0])]))

# ===========================================================================
# 4. course calendar POST: create, draft replace, publish and merge
# ===========================================================================

B1 = event(EB[1], 'Prova', '2026-11-03T18:00:00.000Z', '2026-11-03T19:00:00.000Z', props={'groupId': 'gB'})
B2 = event(EB[2], 'Prova 2', '2026-11-10T18:00:00.000Z', '2026-11-10T19:00:00.000Z', props={'reminder_enabled': False})

# A new draft registry: no attendance days. The second save() changes nothing.
BETA_REGISTRY = '$new:registry-beta'
STATE['registries'][BETA_REGISTRY] = {'course': C['beta'], 'status': DRAFT, 'events': [B1], 'creation_date': '$any'}
add('update-creates-draft', 'owner', UPD('beta'), 200, UPDATED, method='POST', body={'events': [B1], 'status': DRAFT},
    audit=[registry_create(BETA_REGISTRY, 'owner', '1')])

# Publishing a draft replaces the events and creates one day per event; the
# upper-case event id is stored canonically, so the day's second save() logs it.
STATE['registries'][BETA_REGISTRY].update(events=[B1, B2], status=PUBLISHED)
STATE['days']['$new:day-b1'] = day(BETA_REGISTRY, 'Prova', '2026-11-03T18:00:00Z', EB[1])
STATE['days']['$new:day-b2'] = day(BETA_REGISTRY, 'Prova 2', '2026-11-10T18:00:00Z', EB[2].lower())
audit = [registry_update(BETA_REGISTRY, 'full_collaborator', {'events': [dumps([B1]), dumps([B1, B2])], 'status': ['1', '2']}),
         day_create('$new:day-b1', 'full_collaborator', EB[1]), day_create('$new:day-b2', 'full_collaborator', EB[2]),
         day_update('$new:day-b2', 'full_collaborator', {'associated_event': [EB[2].lower(), EB[2]]})]
add('update-publishes-draft', 'full_collaborator', UPD('beta'), 200, UPDATED, method='POST', body={'events': [B1, B2], 'status': PUBLISHED},
    audit=audit)

# A published registry created directly: the reminder first (start with an
# offset), then the registry (status 2.0 is valid; its repr differs from the
# stored 2, so the second save() logs it) and the day.
C1 = event(EC[1], 'Gamma 1', '2026-11-04T08:30:00+01:00', '2026-11-04T09:30:00+01:00',
           props={'reminder_enabled': True, 'reminder_amount': '30', 'reminder_unit': 'minutes', 'course_title': 'Corso Gamma',
                  'instructor': {'instructor_id': IVO}})
GAMMA_REGISTRY = '$new:registry-gamma'
STATE['reminders']['$new:reminder-c1'] = reminder(EC[1], 'Gamma 1', '', 'Corso Gamma', '30 minuti', '2026-11-04T07:00:00Z', instructor=IVO)
STATE['registries'][GAMMA_REGISTRY] = {'course': C['gamma'], 'status': PUBLISHED, 'events': [C1], 'creation_date': '$any'}
STATE['days']['$new:day-c1'] = day(GAMMA_REGISTRY, 'Gamma 1', '2026-11-04T07:30:00Z', EC[1])
audit = [reminder_create('$new:reminder-c1', 'owner'), registry_create(GAMMA_REGISTRY, 'owner', '2.0'),
         registry_update(GAMMA_REGISTRY, 'owner', {'status': ['2', '2.0']}), day_create('$new:day-c1', 'owner', EC[1])]
add('update-creates-published', 'owner', UPD('gamma'), 200, UPDATED, method='POST', body={'events': [C1], 'status': 2.0}, audit=audit)

# A draft stays a draft: events replaced, no days.
D2 = event(ED[2], 'Delta 2', '2026-11-06T16:00:00.000Z', '2026-11-06T17:00:00.000Z')
STATE['registries'][R['delta']]['events'] = [D1, D2]
add('update-replaces-draft', 'owner', UPD('delta'), 200, UPDATED, method='POST', body={'events': [D1, D2], 'status': DRAFT},
    audit=[registry_update(R['delta'], 'owner', {'events': [dumps([D1]), dumps([D1, D2])]})])

# A registry in an unknown status answers 412 after the reminder pass.
add('update-odd-status', 'owner', UPD('epsilon'), 412, {'message': 'Calendar is already published.'}, method='POST',
    body={'events': [], 'status': PUBLISHED, 'note': 'extra key'})
# Only the association's reminders and request.user's instructors: another association's reminder
# for the same event id stays, a new one is created without the other owner's instructor.
Z1 = event(EZ, 'Zeta 1', '2026-11-05T10:00:00.000Z', '2026-11-05T11:00:00.000Z',
           props={'reminder_enabled': True, 'reminder_amount': 1, 'reminder_unit': 'hours', 'instructor': {'instructor_id': UGO}})
STATE['reminders']['$new:reminder-z1'] = reminder(EZ, 'Zeta 1', '', None, '1 ore', '2026-11-05T09:00:00Z')
add('update-other-association-reminder-kept', 'owner', UPD('epsilon'), 412, {'message': 'Calendar is already published.'}, method='POST',
    body={'events': [Z1], 'status': PUBLISHED}, audit=[reminder_create('$new:reminder-z1', 'owner')])

# Published merge: existing events are rewritten in place and their day is
# found by the previous start; new events are appended with a day; the
# events are sorted by start. Reminders: A2's is recomputed, A5 gains one.
A1B = event(EA[1], 'Lezione 1 bis', '2026-11-02T18:00:00.000Z', '2026-11-02T19:00:00.000Z',
            props={'instructor': {'instructor_id': INES, 'label': 'Ines Uno'}, 'groupId': 'gA'})
A2B = event(EA[2], 'Lezione 2', '2026-11-09T17:00:00.000Z', '2026-11-09T18:00:00.000Z',
            props={'groupId': 'gA', 'reminder_enabled': True, 'reminder_amount': 2, 'reminder_unit': 'hours', 'description': 'Portare scarpe'})
A5 = event(EA[5], 'Lezione 5', '2026-11-23T17:00:00.000Z', '2026-11-23T18:00:00.000Z',
           props={'groupId': 'gA', 'reminder_enabled': True, 'reminder_amount': 10, 'reminder_unit': 'minutes'})
old_alpha = copy.deepcopy(STATE['registries'][R['alpha']]['events'])
STATE['reminders'][RM['a2']].update(event_reminder_text='2 ore', send_at='2026-11-09T15:00:00Z')
STATE['reminders']['$new:reminder-a5'] = reminder(EA[5], 'Lezione 5', '', None, '10 minuti', '2026-11-23T16:50:00Z')
STATE['days'][DY['a1']].update(title='Lezione 1 bis', date='2026-11-02T18:00:00Z')
STATE['days']['$new:day-a5'] = day(R['alpha'], 'Lezione 5', '2026-11-23T17:00:00Z', EA[5])
merged_a1 = {**A1, 'title': 'Lezione 1 bis', 'start': A1B['start'], 'end': A1B['end'], 'extendedProps': A1B['extendedProps']}
merged_a2 = {**A2, 'extendedProps': A2B['extendedProps']}
STATE['registries'][R['alpha']]['events'] = [merged_a1, merged_a2, A3, A4, A5]
audit = [reminder_update(RM['a2'], 'owner', {'event_reminder_text': ['1 ore', '2 ore'], 'send_at': ['2026-11-09 16:00:00', '2026-11-09 15:00:00']}),
         reminder_create('$new:reminder-a5', 'owner'),
         day_update(DY['a1'], 'owner', {'title': ['Lezione 1', 'Lezione 1 bis'], 'date': ['2026-11-02 17:00:00', '2026-11-02 18:00:00']}),
         day_create('$new:day-a5', 'owner', EA[5]),
         registry_update(R['alpha'], 'owner', {'events': [dumps(old_alpha), dumps(STATE['registries'][R['alpha']]['events'])]})]
add('update-merges-published', 'owner', UPD('alpha'), 200, UPDATED, method='POST', body={'events': [A5, A2B, A1B], 'status': DRAFT},
    audit=audit)

# Not atomic: the new event's day is saved, then the existing event without
# 'allDay' raises before the registry is saved.
A6 = event(EA[6], 'Lezione 6', '2026-11-30T17:00:00.000Z', '2026-11-30T18:00:00.000Z', props={'groupId': 'gA'})
A3B = {'event_id': EA[3], 'title': 'Lezione 3 bis', 'start': A3['start'], 'end': A3['end'], 'extendedProps': {'groupId': 'gA'}}
STATE['days']['$new:day-a6'] = day(R['alpha'], 'Lezione 6', '2026-11-30T17:00:00Z', EA[6])
add('update-partial-failure-keeps-day', 'owner', UPD('alpha'), 500, SERVER, method='POST', body={'events': [A6, A3B], 'status': PUBLISHED},
    audit=[day_create('$new:day-a6', 'owner', EA[6])])

# The reminder pass also runs before the registry: the first event's reminder
# is saved, then the second's unknown unit raises.
A7 = event(EA[7], 'Lezione 7', '2026-12-07T17:00:00.000Z', '2026-12-07T18:00:00.000Z',
           props={'reminder_enabled': True, 'reminder_amount': 1, 'reminder_unit': 'days', 'description': 'Settimana 7'})
A7B = {**A7, 'event_id': eid('a0', 8), 'extendedProps': {'reminder_enabled': True, 'reminder_amount': 1, 'reminder_unit': 'weeks'}}
STATE['reminders']['$new:reminder-a7'] = reminder(EA[7], 'Lezione 7', 'Settimana 7', None, '1 giorni', '2026-12-06T17:00:00Z')
add('update-partial-reminder-failure', 'full_collaborator', UPD('alpha'), 500, SERVER, method='POST',
    body={'events': [A7, A7B], 'status': PUBLISHED}, audit=[reminder_create('$new:reminder-a7', 'full_collaborator')])

add('calendar-after-merge', 'anonymous', CAL('alpha'), 200,
    course_calendar('alpha', PUBLISHED, STATE['registries'][R['alpha']]['events'], sync=False))

# ===========================================================================
# 5. course calendar DELETE
# ===========================================================================

def remove_events(ref, ids):
    STATE['registries'][ref]['events'] = [row for row in STATE['registries'][ref]['events'] if row['event_id'] not in ids]


# One event: its day by associated_event, its reminders, then the events.
old = copy.deepcopy(STATE['registries'][R['alpha']]['events'])
audit = [day_delete(DY['a4'], 'owner', [OWN, 'actor.sport_association'])]
remove_events(R['alpha'], {EA[4]})
audit.append(registry_update(R['alpha'], 'owner', {'events': [dumps(old), dumps(STATE['registries'][R['alpha']]['events'])]}))
add('delete-one-event', 'owner', UPD('alpha'), 200, DELETED, method='DELETE', body={'event_id': EA[4]}, audit=audit)

# A group from the current event onwards: days by id and the historical
# title/date row; reminders of the association. A collaborator has no
# association of its own, so deleted days have no index row.
old = copy.deepcopy(STATE['registries'][R['alpha']]['events'])
audit = [day_delete(DY['a2'], 'full_collaborator', None), day_delete('$new:day-a5', 'full_collaborator', None),
         day_delete(DY['a3'], 'full_collaborator', None), reminder_delete(RM['a2'], 'full_collaborator'),
         reminder_delete('$new:reminder-a5', 'full_collaborator')]
remove_events(R['alpha'], {EA[2], EA[3], EA[5]})
audit.append(registry_update(R['alpha'], 'full_collaborator', {'events': [dumps(old), dumps(STATE['registries'][R['alpha']]['events'])]}))
add('delete-group-after', 'full_collaborator', UPD('alpha'), 200, DELETED, method='DELETE',
    body={'event_id': EA[2], 'groupId': 'gA', 'before': False}, audit=audit)

# A group up to the current event (the group header keeps the course visible).
old = copy.deepcopy(STATE['registries'][R['grouped']]['events'])
remove_events(R['grouped'], {EG[1], EG[2]})
add('delete-group-before', 'owner', UPD('grouped'), 200, DELETED, method='DELETE', headers=GROUP('own_group'),
    body={'event_id': EG[2], 'groupId': 'gG', 'before': True},
    audit=[registry_update(R['grouped'], 'owner', {'events': [dumps(old), dumps(STATE['registries'][R['grouped']]['events'])]})])
add('delete-group-header-hides', 'owner', UPD('grouped'), 400, DRAFT_ONLY, method='DELETE', headers=GROUP('foreign_group'), body={})

# The whole group (before absent).
old = copy.deepcopy(STATE['registries'][R['alpha']]['events'])
audit = [day_delete(DY['a1'], 'owner', [OWN, 'actor.sport_association'])]
remove_events(R['alpha'], {EA[1]})
audit.append(registry_update(R['alpha'], 'owner', {'events': [dumps(old), dumps([])]}))
add('delete-whole-group', 'owner', UPD('alpha'), 200, DELETED, method='DELETE', body={'event_id': EA[1], 'groupId': 'gA'}, audit=audit)

# No event id: reminders of the events, the registry with its days, then a
# new empty draft.
audit = [reminder_delete(RM['g3'], 'owner'), day_delete(DY['g3'], 'owner', [OWN, 'actor.sport_association'])]
audit.append(registry_delete(R['grouped'], 'owner', STATE['registries'].pop(R['grouped']), [OWN, 'actor.sport_association']))
GROUPED_REGISTRY = '$new:registry-grouped'
STATE['registries'][GROUPED_REGISTRY] = {'course': C['grouped'], 'status': DRAFT, 'events': [], 'creation_date': '$any'}
audit.append(registry_create(GROUPED_REGISTRY, 'owner', '1'))
add('delete-resets-registry', 'owner', UPD('grouped'), 200, UPDATED, method='DELETE', body={}, audit=audit)
add('delete-after-reset-is-draft', 'owner', UPD('grouped'), 400, DRAFT_ONLY, method='DELETE', body={})
add('calendar-after-reset', 'owner', CAL('grouped'), 200, course_calendar('grouped', DRAFT, [], sync=True))

# ===========================================================================
# 6. global calendar actions
# ===========================================================================

GLOBAL_3 = {'event_id': GL[3], 'title': 'Cena sociale', 'start': '2026-12-01T20:00:00Z', 'allDay': False,
            'extendedProps': {'reminder_enabled': True, 'reminder_amount': 3, 'reminder_unit': 'hours', 'description': 'Ristorante',
                              'instructor': {'instructor_id': IVO}}}
add('global-create-existing', 'owner', EVENTS_UPDATE, 400, ['Event already exists.'], method='POST',
    body={'action': 'create', 'events': [GLOBAL_1]})
STATE['reminders']['$new:reminder-gl3'] = reminder(GL[3], 'Cena sociale', 'Ristorante', None, '3 ore', '2026-12-01T17:00:00Z', instructor=IVO)
audit = [reminder_create('$new:reminder-gl3', 'owner'), global_save(OWN_GLOBAL, 'owner', [GLOBAL_1, GLOBAL_2, GLOBAL_3])]
add('global-create', 'owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'action': 'create', 'events': [GLOBAL_3]}, audit=audit)

GLOBAL_2B = {**GLOBAL_2, 'title': 'Festa di Natale'}
add('global-update-by-permission', 'restricted_collaborator', EVENTS_UPDATE, 200, ADDED, method='POST',
    body={'action': 'update', 'events': [GLOBAL_2B]}, audit=[global_save(OWN_GLOBAL, 'restricted_collaborator', [GLOBAL_1, GLOBAL_2B, GLOBAL_3])])
add('global-restricted-delete-denied', 'restricted_collaborator', EVENTS_UPDATE, 403, {'detail': 'Missing event permission.'},
    method='POST', body={'action': 'delete', 'event_id': GL[3]})

audit = [reminder_delete('$new:reminder-gl3', 'full_collaborator'), global_save(OWN_GLOBAL, 'full_collaborator', [GLOBAL_1, GLOBAL_2B])]
add('global-delete', 'full_collaborator', EVENTS_UPDATE, 200, ADDED, method='POST', body={'action': 'delete', 'event_id': GL[3]}, audit=audit)
add('global-delete-missing-is-noop-save', 'owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'action': 'delete', 'event_id': UNKNOWN},
    audit=[])

# A date-only start: parse_datetime gives naive midnight (UTC).
GLOBAL_4 = {'event_id': GL[4], 'title': 'Vigilia', 'start': '2026-12-24', 'allDay': True,
            'extendedProps': {'reminder_enabled': True, 'reminder_amount': '1', 'reminder_unit': 'days', 'course_title': 'Tutti'}}
STATE['reminders']['$new:reminder-gl4'] = reminder(GL[4], 'Vigilia', '', 'Tutti', '1 giorni', '2026-12-23T00:00:00Z')
audit = [reminder_create('$new:reminder-gl4', 'owner'), global_save(OWN_GLOBAL, 'owner', [GLOBAL_1, GLOBAL_2B, GLOBAL_4])]
add('global-create-date-only', 'owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'action': 'create', 'events': [GLOBAL_4]}, audit=audit)

# Snapshot: omitted events lose their reminders; a disabled reminder is deleted.
GLOBAL_1B = {**GLOBAL_1, 'extendedProps': {'reminder_enabled': False}}
audit = [reminder_delete('$new:reminder-gl4', 'owner'), reminder_delete(RM['gl1'], 'owner'),
         global_save(OWN_GLOBAL, 'owner', [GLOBAL_1B])]
add('global-snapshot-replaces', 'owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'events': [GLOBAL_1B]}, audit=audit)

# Another association's instructor is not found (instructor None).
GLOBAL_5 = {'event_id': GL[5], 'title': 'Torneo', 'start': '2026-12-05T09:00:00Z',
            'extendedProps': {'reminder_enabled': True, 'reminder_amount': 30, 'reminder_unit': 'minutes', 'instructor': {'instructor_id': IVO}}}
STATE['reminders']['$new:reminder-gl5'] = reminder(GL[5], 'Torneo', '', None, '30 minuti', '2026-12-05T08:30:00Z', user=FOREIGN_OWNER,
                                                   association=FOREIGN)
audit = [reminder_create('$new:reminder-gl5', 'foreign_owner'), global_save(FOREIGN_GLOBAL, 'foreign_owner', [GLOBAL_5])]
add('global-foreign-create', 'foreign_owner', EVENTS_UPDATE, 200, ADDED, method='POST', body={'action': 'create', 'events': [GLOBAL_5]},
    audit=audit)


# ===========================================================================
# 6b. more course calendar paths on the published beta registry
# ===========================================================================

# An existing reminder whose event disables it is deleted; the event is
# appended with a day.
A7C = event(EA[7], 'Lezione 7', '2026-12-07T17:00:00.000Z', '2026-12-07T18:00:00.000Z', props={'reminder_enabled': False})
old = copy.deepcopy(STATE['registries'][BETA_REGISTRY]['events'])
audit = [reminder_delete('$new:reminder-a7', 'owner')]
STATE['days']['$new:day-beta-a7'] = day(BETA_REGISTRY, 'Lezione 7', '2026-12-07T17:00:00Z', EA[7])
audit.append(day_create('$new:day-beta-a7', 'owner', EA[7]))
STATE['registries'][BETA_REGISTRY]['events'] = old + [A7C]
audit.append(registry_update(BETA_REGISTRY, 'owner', {'events': [dumps(old), dumps(old + [A7C])]}))
add('update-disables-reminder', 'owner', UPD('beta'), 200, UPDATED, method='POST', body={'events': [A7C], 'status': PUBLISHED}, audit=audit)

# A title longer than the day's varchar(125) fails the INSERT: nothing saved.
add('update-day-title-too-long', 'owner', UPD('beta'), 500, SERVER, method='POST',
    body={'events': [event(eid('b0', 3), 'x' * 126, '2026-11-20T18:00:00.000Z', props={})], 'status': PUBLISHED})

# A naive start is stored as UTC and sorts as text.
B4 = event(eid('b0', 4), 'Prova 4', '2026-11-17T18:00:00', '2026-11-17T19:00:00', props={'groupId': 'gB'})
old = copy.deepcopy(STATE['registries'][BETA_REGISTRY]['events'])
STATE['days']['$new:day-b4'] = day(BETA_REGISTRY, 'Prova 4', '2026-11-17T18:00:00Z', B4['event_id'])
STATE['registries'][BETA_REGISTRY]['events'] = [B1, B2, B4, A7C]
add('update-naive-start', 'owner', UPD('beta'), 200, UPDATED, method='POST', body={'events': [B4], 'status': PUBLISHED},
    audit=[day_create('$new:day-b4', 'owner', B4['event_id']),
           registry_update(BETA_REGISTRY, 'owner', {'events': [dumps(old), dumps(STATE['registries'][BETA_REGISTRY]['events'])]})])

# Comparing the aware current start with a naive group start raises: the
# atomic delete rolls back.
add('delete-naive-aware-group-fails', 'owner', UPD('beta'), 500, SERVER, method='DELETE',
    body={'event_id': EB[1], 'groupId': 'gB', 'before': False})
add('delete-malformed-json', 'owner', UPD('beta'), 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    method='DELETE', raw_body='nope', content_type='application/json')
add('delete-draft-before-json', 'owner', UPD('delta'), 400, DRAFT_ONLY, method='DELETE', raw_body='nope', content_type='application/json')

# Without 'before' the whole group goes, whatever the start forms.
old = copy.deepcopy(STATE['registries'][BETA_REGISTRY]['events'])
audit = [day_delete('$new:day-b1', 'owner', [OWN, 'actor.sport_association']), day_delete('$new:day-b4', 'owner', [OWN, 'actor.sport_association'])]
remove_events(BETA_REGISTRY, {EB[1], B4['event_id']})
audit.append(registry_update(BETA_REGISTRY, 'owner', {'events': [dumps(old), dumps(STATE['registries'][BETA_REGISTRY]['events'])]}))
add('delete-mixed-group', 'owner', UPD('beta'), 200, DELETED, method='DELETE', body={'event_id': EB[1], 'groupId': 'gB'}, audit=audit)

# ===========================================================================
# 7. final reads
# ===========================================================================

add('events-owner-final', 'owner', LIST, 200, events(
    [listed('beta', B2, OWN_COLORS['beta'], '$new:day-b2'), listed('beta', A7C, OWN_COLORS['beta'], '$new:day-beta-a7'),
     listed('gamma', C1, OWN_COLORS['gamma'], '$new:day-c1'), listed('delta', D1, OWN_COLORS['delta']),
     listed('delta', D2, OWN_COLORS['delta']), global_listed(GLOBAL_1B)]))
add('events-foreign-final', 'foreign_owner', LIST, 200, events([listed('abroad', F1, COLORS[0]), global_listed(GLOBAL_5)]))

# A stored start that is not a date: the lookup by date raises, and the range
# filter answers 400 first.
BAD = event(eid('f3', 1), 'Data rotta', 'domani', None, all_day=True, props={})
ABROAD2_REGISTRY = '$new:registry-abroad2'
STATE['registries'][ABROAD2_REGISTRY] = {'course': C['abroad2'], 'status': DRAFT, 'events': [BAD], 'creation_date': '$any'}
add('update-stores-invalid-start', 'foreign_owner', UPD('abroad2'), 200, UPDATED, method='POST', body={'events': [BAD], 'status': DRAFT},
    audit=[registry_create(ABROAD2_REGISTRY, 'foreign_owner', '1', FOREIGN)])
add('events-invalid-stored-start', 'foreign_owner', LIST, 500, SERVER)
add('events-invalid-stored-start-range', 'foreign_owner', EVENTS + '?start=2026-11-01T00:00:00Z&end=2026-12-01T00:00:00Z&get_lesson=false',
    400, ['Invalid calendar date.'])
add('calendar-invalid-stored-start', 'anonymous', CAL('abroad2'), 200,
    course_calendar('abroad2', DRAFT, [BAD], sync=False, association='Message reads foreign', logo=None))

# ===========================================================================
# 8. lesson details (get_lesson): opening an event of the global calendar
# ===========================================================================


def lesson(day_ref, course, *, event=None):
    """get_extended_prop_for_attendance_day of a day without attendees in a course without subscriptions: the registry event
    of the same hour (linked days only to their own event_id), its extendedProps and times."""
    row = STATE['days'][day_ref]
    props = copy.deepcopy(event.get('extendedProps', {})) if event else {}
    return {'title': row['title'], 'allDay': event.get('allDay', False) if event else False, 'extended_props': props,
            'attendance_day_id': day_ref, 'attendance_registry': row['registry'], 'date': row['date'],
            'start': event['start'] if event else None, 'end': event['end'] if event else None, 'course_id': C[course],
            'total_attendees': 0, 'total_expected_absences': 0, 'potential_attendees': [], 'total_expected_total': 0, 'attendees': [],
            'expected_absences': []}


def with_lesson(row, value):
    row['extendedProps']['lesson'] = value
    return row


add('events-lesson', 'owner', EVENTS + f'?event_id={EA[7]}&get_lesson=true', 200, events([
    with_lesson(listed('beta', A7C, OWN_COLORS['beta'], '$new:day-beta-a7'), lesson('$new:day-beta-a7', 'beta', event=A7C))]))
add('events-lesson-collaborator', 'full_collaborator', EVENTS + f'?event_id={EA[7]}&get_lesson=1', 200, events([
    with_lesson(listed('beta', A7C, OWN_COLORS['beta'], '$new:day-beta-a7'), lesson('$new:day-beta-a7', 'beta', event=A7C))]))
# The day is linked to the lowercase id; the stored event id is uppercase, so no event matches.
add('events-lesson-linked-to-other-spelling', 'owner', EVENTS + f'?event_id={EB[2]}', 200, events([
    with_lesson(listed('beta', B2, OWN_COLORS['beta'], '$new:day-b2'), lesson('$new:day-b2', 'beta'))]))
# Registry event starts are read with strptime('%Y-%m-%dT%H:%M:%S.%fZ'): an offset start raises.
add('events-lesson-offset-start-server-error', 'owner', EVENTS + f'?event_id={EC[1]}&get_lesson=true', 500, SERVER)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course calendar and full events calendar authored sequence with exact effects',
                              'ids': IDS, 'seed': SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
