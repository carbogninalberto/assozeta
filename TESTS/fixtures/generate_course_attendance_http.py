"""Author the course attendance HTTP sequence (course-attendance.md).

Routes: GET course/<uid>/attendees (attendees), POST
course/<uid>/attendees/<attendance_day_uid>/update (attendees_update), GET
subscription/<uid>/attendance (subscription_attendance) and POST
attendance/<uid>/mark (attendance_mark).

Pinned Django semantics, in the views' order:
- attendees links unlinked days first (map_unlinked_attendance: the first
  unlinked day by key, stopping at the first one it cannot link), then lists
  the registry's days by date. A day's event is the first registry event in
  the same hour ('%Y/%m/%d %H') that is not linked to another event; with
  extendedProps it renders get_extended_prop_for_attendance_day, whose
  subscription map is keyed by UUID and looked up by str (attendee names and
  its expected_absences never resolve) and which links an unlinked day,
  saving the names the view added to expected_absences.
- attendees_update gives back the carnet lesson of each removed attendee (the
  newest carnet whose lesson matches course and date; lessons without
  attendance_day_id gain a null one) and consumes the carnet with the fewest
  lessons left for each new attendee. It is not atomic: a 412 or an exception
  keeps the carnets saved before it.
- subscription_attendance lists the days whose attendees name one of the
  subscription's course subscriptions (deleted ones included), once per
  matching attendee.
- attendance_mark checks a subscription in on today's day (Europe/Rome date,
  matched against the UTC date), consuming a carnet when it has one.
'{today}' is the Europe/Rome date when the fixture is seeded and the request
is made. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_attendance_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
_ids = iter(f'a1000000-0000-4000-8000-{950001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
FULL_SCOPED = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}


def new():
    return next(_ids)


INSTRUCTOR = {'id': new(), 'first': 'Ivo', 'last': 'Insegnante'}
UNPAID, PAID = new(), new()
PAYMENTS = {'unpaid': {'id': UNPAID, 'paid': False}, 'paid': {'id': PAID, 'paid': True}}
CARNET = new()
MEDICAL = {'id': new(), 'expiration': '2030-12-31', 'competitive': True}

# label: (association, first, last, born)
PEOPLE = {'anna': (OWN, 'Anna', 'Rossi', '1990-01-01'), 'bruno': (OWN, 'Bruno', 'Verdi', '1985-05-05'),
          'dario': (OWN, 'Dario', 'Neri', '1992-02-02'), 'elena': (OWN, 'Elena', 'Grigi', '1993-03-03'),
          'gina': (OWN, 'Gina', 'Gialli', '1994-04-04'), 'hugo': (OWN, 'Hugo', 'Blu', '1995-05-05'),
          'ivan': (OWN, 'Ivan', 'Viola', '1996-06-06'), 'lia': (OWN, 'Lia', 'Rosa', '1997-07-07'),
          'franco': (FOREIGN, 'Franco', 'Esteri', '1980-08-08')}
ASSOCIATES = {label: {'id': new(), 'association': a, 'first': f, 'last': l, 'born': b, 'tax': f'TAX{i:013d}'}
              for i, (label, (a, f, l, b)) in enumerate(PEOPLE.items())}
SUB_CREATED = '2026-01-10T10:00:00Z'
SUBSCRIPTIONS = {label: {'id': new(), 'association': row['association'], 'associate': label,
                         'user': 'athlete' if label == 'dario' else 'foreign_owner' if label == 'franco' else 'owner',
                         'medical': MEDICAL['id'] if label == 'bruno' else None, 'created': SUB_CREATED}
                 for label, row in ASSOCIATES.items()}
SUB = {label: row['id'] for label, row in SUBSCRIPTIONS.items()}

# label: (association, title, deleted)
COURSE_ROWS = {'a': (OWN, 'Nuoto', False), 'b': (OWN, 'Yoga', False), 'c': (OWN, 'Pilates', False),
               'm': (OWN, 'Corsa', False), 'n': (OWN, 'Danza', False), 'o': (OWN, 'Tennis', False),
               'empty': (OWN, 'Senza registro', False), 'deleted': (OWN, 'Eliminato', True), 'f': (FOREIGN, 'Estero', False)}
COURSES = {label: {'id': new(), 'association': a, 'title': t, 'deleted': d} for label, (a, t, d) in COURSE_ROWS.items()}
C = {label: row['id'] for label, row in COURSES.items()}

# Seeded in this order (database order for unordered querysets).
CS_ROWS = [('a_anna', 'a', 'anna', False), ('a_bruno', 'a', 'bruno', False), ('a_gone', 'a', 'bruno', True),
           ('b_anna', 'b', 'anna', False), ('c_anna', 'c', 'anna', False), ('c_bruno', 'c', 'bruno', False),
           ('c_dario', 'c', 'dario', False), ('m_anna', 'm', 'anna', False), ('n_anna', 'n', 'anna', False),
           ('n_elena', 'n', 'elena', False), ('m_gina', 'm', 'gina', False), ('m_hugo', 'm', 'hugo', False),
           ('m_ivan', 'm', 'ivan', False), ('m_lia', 'm', 'lia', False), ('f_franco', 'f', 'franco', False),
           ('orphan', 'f', None, False)]
COURSE_SUBSCRIPTIONS = {label: {'id': new(), 'course': course, 'subscription': sub, 'deleted': deleted}
                        for label, course, sub, deleted in CS_ROWS}
CS = {label: row['id'] for label, row in COURSE_SUBSCRIPTIONS.items()}

E = {label: new() for label in ('E1', 'E2', 'E3', 'F1', 'F2')}
EVENTS = {
    'a': [{'event_id': E['E1'], 'title': 'Lezione', 'start': '2020-03-02T17:00:00.000Z', 'end': '2020-03-02T18:00:00.000Z', 'allDay': False,
           'extendedProps': {'instructor': {'instructor_id': INSTRUCTOR['id'], 'label': 'vecchio'}, 'description': 'Prima'}},
          {'event_id': E['E2'], 'title': 'Libera', 'start': '2020-03-09T17:00:00.000Z', 'end': '2020-03-09T18:00:00.000Z', 'allDay': True},
          {'event_id': E['E3'], 'title': 'Gruppo', 'start': '2020-03-16T17:30:00.000Z', 'end': '2020-03-16T18:30:00.000Z',
           'extendedProps': {'instructor': [{'instructor_id': INSTRUCTOR['id']}, {'name': 'esterno'}]}}],
    'b': [{'event_id': E['F1'], 'title': 'Yoga', 'start': '2020-04-06T18:00:00.000Z', 'end': '2020-04-06T19:00:00.000Z', 'allDay': False,
           'extendedProps': {'description': 'Respiro'}},
          {'event_id': E['F2'], 'title': 'Yoga due', 'start': '2020-04-13T18:00:00.000Z', 'end': '2020-04-13T19:00:00.000Z'}],
}
REGISTRIES = {label: {'id': new(), 'course': label, 'events': EVENTS.get(label, [])} for label in ('a', 'b', 'c', 'm', 'n', 'o', 'f')}
R = {label: row['id'] for label, row in REGISTRIES.items()}
entry = lambda *labels: [{'course_subscription_id': CS[label]} for label in labels]

# Day keys follow this order (map_unlinked_attendance takes the first unlinked day by key).
DAY_ROWS = [
    ('a1', 'a', 'Lezione', '2020-03-02T17:00:00Z', entry('a_anna'), entry('a_bruno', 'a_gone'), 'E1'),
    ('a2', 'a', 'Libera', '2020-03-09T17:00:00Z', None, None, 'E2'),
    ('a3', 'a', 'Gruppo', '2020-03-16T17:00:00Z', [], [], 'E3'),
    ('a4', 'a', 'Spostata', '2020-03-25T10:00:00Z', None, None, 'E3'),
    ('a5', 'a', 'Doppia', '2020-03-02T17:45:00Z', None, None, 'E2'),
    ('a6', 'a', 'Futura', '2031-01-10T10:00:00Z', entry('a_anna', 'a_anna'), None, 'E2'),
    ('b0', 'b', 'Yoga bis', '2020-04-06T18:20:00Z', None, None, 'F1'),
    ('b1', 'b', 'Yoga', '2020-04-06T18:00:00Z', entry('b_anna'), entry('b_anna'), None),
    ('b2', 'b', 'Yoga due', '2020-04-13T18:00:00Z', None, None, None),
    ('c1', 'c', 'Pilates base', '2020-05-04T09:00:00Z', [], None, None),
    ('c2', 'c', 'Pilates due', '2020-05-11T09:00:00Z', entry('c_anna'), None, None),
    ('c3', 'c', 'Pilates tre', '2020-05-18T09:00:00Z', [{'course_subscription_id': 'a1000000-0000-4000-8000-000000959999'}], None, None),
    ('c4', 'c', 'Pilates quattro', '2020-05-25T09:00:00Z', None, None, None),
    ('m_today', 'm', 'Corsa oggi', '{today}T12:00:00Z', [], None, None),
    ('n_today', 'n', 'Danza oggi', '{today}T15:00:00Z', None, None, None),
    ('o_today', 'o', 'Tennis oggi', '{today}T08:00:00Z', [], None, None),
    ('f_today', 'f', 'Estero oggi', '{today}T08:00:00Z', None, None, None),
    ('f_old', 'f', 'Estero vecchio', '2020-02-01T08:00:00Z', entry('orphan'), None, None),
]
DAYS = {label: {'id': new(), 'registry': registry, 'title': title, 'date': date, 'attendees': attendees, 'expected_absences': absences,
                'associated_event': E[event] if event else None}
        for label, registry, title, date, attendees, absences, event in DAY_ROWS}
D = {label: row['id'] for label, row in DAYS.items()}


def lesson(day, *, course='c', with_day=True):
    row = {'date': DAYS[day]['date'].replace('T', ' ').replace('Z', '+00:00'), 'attendance_day_id': D[day],
           'course': {'id': C[course], 'title': COURSES[course]['title']}, 'title': DAYS[day]['title']}
    if not with_day:
        del row['attendance_day_id']
    return row


def meta(left, registry, counter=10):
    return {'lessons_counter': counter, 'lessons_left': left, 'lessons_registry': registry}


OLD_LESSON = {'date': '2020-04-01 09:00:00+00:00', 'course': {'id': C['c'], 'title': 'Pilates'}, 'title': 'vecchia'}
HOLDINGS = {
    'anna_1': {'id': new(), 'subscription': 'anna', 'cs': ['c_anna'], 'created': '2026-01-02T10:00:00Z', 'payment': None, 'meta': meta(2, [])},
    'anna_2': {'id': new(), 'subscription': 'anna', 'cs': [], 'created': '2026-01-01T10:00:00Z', 'payment': None,
               'meta': meta(5, [OLD_LESSON, lesson('c2', with_day=False)])},
    'bruno': {'id': new(), 'subscription': 'bruno', 'cs': ['c_bruno'], 'created': '2026-01-03T10:00:00Z', 'payment': 'unpaid', 'meta': meta(3, [])},
    'dario': {'id': new(), 'subscription': 'dario', 'cs': ['c_dario'], 'created': '2026-01-04T10:00:00Z', 'payment': None, 'meta': meta(0, [])},
    'elena': {'id': new(), 'subscription': 'elena', 'cs': ['n_elena'], 'created': '2026-01-05T10:00:00Z', 'payment': 'paid', 'meta': meta(3, [])},
    'gina': {'id': new(), 'subscription': 'gina', 'cs': [], 'created': '2026-01-06T10:00:00Z', 'payment': 'unpaid', 'meta': meta(4, [])},
    'hugo': {'id': new(), 'subscription': 'hugo', 'cs': [], 'created': '2026-01-07T10:00:00Z', 'payment': None, 'meta': meta(0, [])},
}
H = {label: row['id'] for label, row in HOLDINGS.items()}

cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'course-attendance-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def dumps(value):
    return json.dumps(value, sort_keys=True)


def change(model, label, changes, *, actor='owner', association='own_association', path=None):
    if path is None:
        path = 'attendance_registry.course.sport_association' if model == 'attendanceday' else 'subscription.sport_association'
    return {'model': model, 'label': label, 'changes': changes, 'actor': actor, 'index': [association, path]}


def effect(days=None, holdings=None, audit=(), impersonation=None):
    return {'days': days or {}, 'holdings': holdings or {}, 'audit': list(audit), 'impersonation': impersonation}


DETAIL_403 = {'detail': 'You do not have permission to perform this action.'}
ANON = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
RESOURCE_DENIED = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
missing = lambda key: {'detail': f'Missing permission: {key}'}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
AG = '<span class="badge badge-info ml-1" title="Certificato Medico Agonistico">AG</span>'
LABELS = {'anna': f'<span class="text-warning font-weight-boldest">Mancante da {{missing:{SUB_CREATED}}}</span>',
          'bruno': ('<span class="text-success font-weight-boldest"><i class="ph-bold ph-first-aid-kit"></i>'
                    f'{{until:{MEDICAL["expiration"]}}} rimanenti</span>{AG}')}


def name(label):
    return f'{ASSOCIATES[label]["first"]} {ASSOCIATES[label]["last"]}'


def potential(*labels):
    return [{'course_subscription_id': CS[label], 'subscription': {'associate': {
        'first_name': ASSOCIATES[label.split('_')[1]]['first'], 'last_name': ASSOCIATES[label.split('_')[1]]['last'],
        'tax_code': ASSOCIATES[label.split('_')[1]]['tax'], 'medical_label': LABELS[label.split('_')[1]]}}} for label in labels]


def event_of(registry, event_id):
    return next(e for e in EVENTS[registry] if e['event_id'] == event_id)


def extended(day, *, event, props, potentials, attendees=None, absences_total=0):
    row = DAYS[day]
    e = event_of(row['registry'], E[event])
    attendees = row['attendees'] or [] if attendees is None else attendees
    return {'title': row['title'], 'allDay': e.get('allDay', False), 'extended_props': props, 'attendance_day_id': D[day],
            'attendance_registry': R[row['registry']], 'date': row['date'], 'start': e['start'], 'end': e['end'],
            'course_id': C[row['registry']], 'total_attendees': len(attendees), 'total_expected_absences': absences_total,
            'potential_attendees': potentials, 'total_expected_total': len(potentials) - absences_total,
            'attendees': attendees, 'expected_absences': []}


def listed(day, *, event=None, ext=None, absences=()):
    row = DAYS[day]
    e = event_of(row['registry'], E[event]) if event else {}
    return {'title': row['title'], 'allDay': e.get('allDay', False), 'extended_props': ext if ext is not None else {},
            'attendance_day_id': D[day], 'attendance_registry': R[row['registry']], 'date': row['date'],
            'start': e.get('start'), 'end': e.get('end'), 'attendees': row['attendees'] or [], 'expected_absences': list(absences)}


INSTRUCTOR_NAME = f'{INSTRUCTOR["first"]} {INSTRUCTOR["last"]}'

# ---------------------------------------------------------------------------
# subscription/<uid>/attendance
# ---------------------------------------------------------------------------
sub_path = lambda label, query='': f'/subscription/{SUB.get(label, label)}/attendance{query}'


def attended(*days):
    return [{'title': DAYS[d]['title'], 'attendance_day_id': D[d], 'attendance_registry': R[DAYS[d]['registry']],
             'course': {'course_id': C[DAYS[d]['registry']], 'title': COURSES[DAYS[d]['registry']]['title']}, 'date': DAYS[d]['date']}
            for d in days]


def history(courses, days, recent):
    return {'data': {'attendance_days': attended(*days), 'courses': [{'course_id': C[c], 'title': COURSES[c]['title']} for c in courses],
                     'stats': {'total_attendance_last_30_days': recent, 'total_absences_last_30_days': 0,
                               'total_attendance': len(days), 'total_absences': 0}}}


ANNA_HISTORY = history(['a', 'b', 'c', 'm', 'n'], ['a6', 'a6', 'c2', 'b1', 'a1'], 2)
ORPHAN_HISTORY = history(['f'], ['f_old'], 0)
add('history-owner', 'owner', sub_path('anna'), 200, ANNA_HISTORY)
add('history-full-collaborator', 'full_collaborator', sub_path('anna'), 200, ANNA_HISTORY)
add('history-course-filter', 'owner', sub_path('anna', f'?course={C["c"]}'), 200, history(['c'], ['c2'], 0))
add('history-course-filter-other', 'owner', sub_path('anna', f'?course={C["f"]}'), 200, history([], [], 0))
add('history-course-filter-malformed', 'owner', sub_path('anna', '?course=nope'), 500, SERVER)
add('history-athlete-own', 'athlete', sub_path('dario'), 200, history(['c'], [], 0))
add('history-athlete-not-own', 'athlete', sub_path('anna'), 200, ORPHAN_HISTORY)
add('history-owner-other-association', 'owner', sub_path('franco'), 200, ORPHAN_HISTORY)
add('history-owner-unknown', 'owner', sub_path('a1000000-0000-4000-8000-000000959998'), 200, ORPHAN_HISTORY)
add('history-group-hidden', 'owner', sub_path('anna'), 200, ORPHAN_HISTORY, headers={'X-Group-Id': IDS['own_group']})
add('history-group-malformed-ignored', 'owner', sub_path('anna', f'?course={C["c"]}'), 200, history(['c'], ['c2'], 0),
    headers={'X-Group-Id': 'nope'})
add('history-scoped-athlete-own', 'owner', sub_path('dario'), 200, history(['c'], [], 0), headers=SCOPED)
add('history-scoped-athlete-other', 'owner', sub_path('anna'), 403, RESOURCE_DENIED, headers=SCOPED)
add('history-scoped-full', 'owner', sub_path('anna', f'?course={C["c"]}'), 200, history(['c'], ['c2'], 0), headers=FULL_SCOPED)
add('history-malformed', 'owner', sub_path('nope'), 400, BAD_UUID)
add('history-owner-without-association', 'owner_without_association', sub_path('anna'), 500, SERVER)
add('history-restricted', 'restricted_collaborator', sub_path('anna'), 403, missing('association.members.read'))
add('history-anonymous', 'anonymous', sub_path('anna'), 401, ANON)
add('history-admin-without-session', 'admin', sub_path('anna'), 403, ADMIN)
add('history-wrong-method', 'owner', sub_path('anna'), 405, method('POST'), method='POST', body={})
add('history-options', 'owner', sub_path('anna'), 200, {'name': 'Subscription Attendance',
    'description': 'API endpoint to get the attendance registry for the current course', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------------------
# course/<uid>/attendees
# ---------------------------------------------------------------------------
list_path = lambda label: f'/course/{C.get(label, label)}/attendees'
A_POTENTIAL = potential('a_anna', 'a_bruno')
A1_PROPS = {'instructor': {'instructor_id': INSTRUCTOR['id'], 'label': INSTRUCTOR_NAME}, 'description': 'Prima'}
A3_PROPS = {'instructor': [{'instructor_id': INSTRUCTOR['id'], 'label': INSTRUCTOR_NAME}, {'name': 'esterno'}]}
COURSE_A = {'data': {'events': [
    listed('a1', event='E1', ext=extended('a1', event='E1', props=A1_PROPS, potentials=A_POTENTIAL, absences_total=2),
           absences=[name('bruno'), '<rimosso>']),
    listed('a5'),
    listed('a2', event='E2'),
    listed('a3', event='E3', ext=extended('a3', event='E3', props=A3_PROPS, potentials=A_POTENTIAL)),
    listed('a4'),
    listed('a6'),
]}}
add('attendees-owner', 'owner', list_path('a'), 200, COURSE_A)
add('attendees-full-collaborator', 'full_collaborator', list_path('a'), 200, COURSE_A)
add('attendees-scoped-full', 'owner', list_path('a'), 200, COURSE_A, headers=FULL_SCOPED)
B_POTENTIAL = potential('b_anna')
COURSE_B = {'data': {'events': [
    listed('b1', event='F1', ext=extended('b1', event='F1', props={'description': 'Respiro'}, potentials=B_POTENTIAL, absences_total=1),
           absences=[name('anna')]),
    listed('b0', event='F1', ext=extended('b0', event='F1', props={'description': 'Respiro'}, potentials=B_POTENTIAL)),
    listed('b2', event='F2'),
]}}
B1_ABSENCES = entry('b_anna')
B1_NAMED = [{'course_subscription_id': CS['b_anna'], 'associate_name': name('anna')}]
# The first unlinked day (b1) cannot link to F1 (b0 has it), which stops the mapping;
# the listing then links b1 through get_extended_prop_for_attendance_day, saving the names.
add('attendees-links-through-extended-props', 'owner', list_path('b'), 200, COURSE_B,
    effect=effect(days={'b1': {'associated_event': E['F1'], 'expected_absences': B1_NAMED}},
                  audit=[change('attendanceday', 'b1', {'associated_event': ['None', E['F1']], 'expected_absences': [dumps(B1_ABSENCES), dumps(B1_NAMED)]})]))
add('attendees-maps-unlinked', 'full_collaborator', list_path('b'), 200, COURSE_B,
    effect=effect(days={'b2': {'associated_event': E['F2']}},
                  audit=[change('attendanceday', 'b2', {'associated_event': ['None', E['F2']]}, actor='full_collaborator')]))
add('attendees-linked-no-writes', 'owner', list_path('b'), 200, COURSE_B)
add('attendees-without-registry', 'owner', list_path('empty'), 200, {'data': {'events': []}})
add('attendees-deleted-course', 'owner', list_path('deleted'), 404, {'detail': 'Course not found.'})
add('attendees-foreign-course', 'owner', list_path('f'), 404, {'detail': 'Course not found.'})
add('attendees-unknown-course', 'owner', list_path('a1000000-0000-4000-8000-000000959998'), 404, {'detail': 'Course not found.'})
add('attendees-group-hidden', 'owner', list_path('a'), 404, {'detail': 'Course not found.'}, headers={'X-Group-Id': IDS['own_group']})
add('attendees-malformed', 'owner', list_path('nope'), 400, BAD_UUID)
add('attendees-athlete', 'athlete', list_path('a'), 403, DETAIL_403)
add('attendees-owner-without-association', 'owner_without_association', list_path('a'), 500, SERVER)
add('attendees-restricted', 'restricted_collaborator', list_path('a'), 403, missing('association.courses.attendance.read'))
add('attendees-anonymous', 'anonymous', list_path('a'), 401, ANON)
add('attendees-admin-without-session', 'admin', list_path('a'), 403, ADMIN)
add('attendees-wrong-method', 'owner', list_path('a'), 405, method('POST'), method='POST', body={})
add('attendees-options', 'owner', list_path('a'), 200, {'name': 'Attendees',
    'description': 'API endpoint to get the attendance registry for the current course', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------------------
# course/<uid>/attendees/<attendance_day_uid>/update
# ---------------------------------------------------------------------------
update_path = lambda course, day: f'/course/{C.get(course, course)}/attendees/{D.get(day, day)}/update'
UPDATED = {'message': 'Attendance Registry updated.'}
ANNA_C1 = lesson('c1')
add('update-adds-attendee-consumes-carnet', 'owner', update_path('c', 'c1'), 200, UPDATED, method='POST',
    body={'attendees': entry('c_anna')},
    effect=effect(days={'c1': {'attendees': entry('c_anna')}}, holdings={'anna_1': meta(1, [ANNA_C1])},
                  audit=[change('carnetsubscription', 'anna_1', {'meta': [dumps(meta(2, [])), dumps(meta(1, [ANNA_C1]))]}),
                         change('attendanceday', 'c1', {'attendees': ['[]', dumps(entry('c_anna'))]})]))
add('update-same-attendees-no-change', 'owner', update_path('c', 'c1'), 200, UPDATED, method='POST', body={'attendees': entry('c_anna')})
add('update-removes-attendee-returns-lesson', 'full_collaborator', update_path('c', 'c1'), 200, UPDATED, method='POST',
    body={'attendees': []},
    effect=effect(days={'c1': {'attendees': []}}, holdings={'anna_1': meta(2, [])},
                  audit=[change('carnetsubscription', 'anna_1', {'meta': [dumps(meta(1, [ANNA_C1])), dumps(meta(2, []))]}, actor='full_collaborator'),
                         change('attendanceday', 'c1', {'attendees': [dumps(entry('c_anna')), '[]']}, actor='full_collaborator')]))
add('update-unpaid-carnet', 'owner', update_path('c', 'c1'), 412, {'msg': 'Il carnet non è ancora stato pagato.'}, method='POST',
    body={'attendees': entry('c_bruno')})
add('update-finished-carnet', 'owner', update_path('c', 'c1'), 412, {'message': 'Carnet is already finished.', 'associate_name': name('dario')},
    method='POST', body={'attendees': entry('c_dario')})
# Not atomic: Anna's carnet stays consumed when Bruno's unpaid carnet stops the request.
add('update-partial-keeps-carnet', 'owner', update_path('c', 'c1'), 412, {'msg': 'Il carnet non è ancora stato pagato.'}, method='POST',
    body={'attendees': entry('c_anna', 'c_bruno')},
    effect=effect(holdings={'anna_1': meta(1, [ANNA_C1])},
                  audit=[change('carnetsubscription', 'anna_1', {'meta': [dumps(meta(2, [])), dumps(meta(1, [ANNA_C1]))]})]))
OLD_NULL = {**OLD_LESSON, 'attendance_day_id': None}
ANNA_2_BEFORE = meta(5, [OLD_LESSON, lesson('c2', with_day=False)])
add('update-removes-legacy-lesson', 'owner', update_path('c', 'c2'), 200, UPDATED, method='POST', body={'attendees': []},
    effect=effect(days={'c2': {'attendees': []}}, holdings={'anna_2': meta(6, [OLD_NULL])},
                  audit=[change('carnetsubscription', 'anna_2', {'meta': [dumps(ANNA_2_BEFORE), dumps(meta(6, [OLD_NULL]))]}),
                         change('attendanceday', 'c2', {'attendees': [dumps(entry('c_anna')), '[]']})]))
UNKNOWN = [{'course_subscription_id': 'a1000000-0000-4000-8000-000000959997'}]
add('update-unknown-subscription-kept', 'owner', update_path('c', 'c4'), 200, UPDATED, method='POST', body={'attendees': UNKNOWN},
    effect=effect(days={'c4': {'attendees': UNKNOWN}}, audit=[change('attendanceday', 'c4', {'attendees': ['null', dumps(UNKNOWN)]})]))
add('update-removed-unknown-subscription', 'owner', update_path('c', 'c3'), 500, SERVER, method='POST', body={'attendees': []})
add('update-attendee-not-object', 'owner', update_path('c', 'c1'), 500, SERVER, method='POST', body={'attendees': ['x']})
add('update-attendee-malformed-id', 'owner', update_path('c', 'c1'), 500, SERVER, method='POST',
    body={'attendees': [{'course_subscription_id': 'nope'}]})
add('update-attendee-without-id', 'owner', update_path('c', 'c1'), 500, SERVER, method='POST', body={'attendees': [{}]})
add('update-attendees-null', 'owner', update_path('c', 'c2'), 500, SERVER, method='POST', body={'attendees': None})
add('update-empty-body', 'owner', update_path('c', 'c1'), 400, ['Missing or wrong events data.'], method='POST', body={})
add('update-extra-key', 'owner', update_path('c', 'c1'), 400, ['Missing or wrong events data.'], method='POST',
    body={'attendees': [], 'other': 1})
add('update-list-body', 'owner', update_path('c', 'c1'), 500, SERVER, method='POST', body=[])
add('update-malformed-course', 'owner', update_path('nope', 'c1'), 400, BAD_UUID, method='POST', body={})
add('update-malformed-day', 'owner', update_path('c', 'nope'), 400, BAD_UUID, method='POST', body={})
add('update-unknown-course', 'owner', update_path('a1000000-0000-4000-8000-000000959998', 'c1'), 404, {'detail': 'Course not found.'},
    method='POST', body={'attendees': []})
add('update-foreign-course', 'owner', update_path('f', 'f_old'), 404, {'detail': 'Course not found.'}, method='POST', body={'attendees': []})
add('update-without-registry', 'owner', update_path('empty', 'c1'), 404, {'detail': 'Course Attendance Registry not found.'},
    method='POST', body={'attendees': []})
add('update-day-of-other-course', 'owner', update_path('c', 'a1'), 404, {'detail': 'Course Event day not found.'}, method='POST',
    body={'attendees': []})
add('update-scoped-full-foreign-subscription', 'owner', update_path('c', 'c1'), 403, RESOURCE_DENIED, method='POST',
    body={'attendees': entry('f_franco')}, headers=FULL_SCOPED)
add('update-athlete', 'athlete', update_path('c', 'c1'), 403, DETAIL_403, method='POST', body={'attendees': []})
add('update-owner-without-association', 'owner_without_association', update_path('c', 'c1'), 500, SERVER, method='POST',
    body={'attendees': []})
add('update-restricted', 'restricted_collaborator', update_path('c', 'c1'), 403, missing('association.courses.attendance.update'),
    method='POST', body={'attendees': []})
add('update-anonymous', 'anonymous', update_path('c', 'c1'), 401, ANON, method='POST', body={'attendees': []})
add('update-admin-without-session', 'admin', update_path('c', 'c1'), 403, ADMIN, method='POST', body={'attendees': []})
add('update-wrong-method', 'owner', update_path('c', 'c1'), 405, method('GET'))
add('update-options', 'owner', update_path('c', 'c1'), 200, {'name': 'Attendees Update',
    'description': 'API endpoint to update an attendance registry event', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------------------
# attendance/<uid>/mark
# ---------------------------------------------------------------------------
mark_path = lambda label: f'/attendance/{SUB.get(label, label)}/mark'
checked = lambda label: {'msg': f'Check-in effettuato! Ciao {name(label)}.'}
add('mark-malformed', 'owner', mark_path('nope'), 400, BAD_UUID, method='POST', body={})
add('mark-unknown-subscription', 'owner', mark_path('a1000000-0000-4000-8000-000000959998'), 404, {'msg': 'Iscrizione non trovata.'},
    method='POST', body={})
add('mark-group-hidden', 'owner', mark_path('dario'), 404, {'msg': 'Iscrizione non trovata.'}, method='POST', body={},
    headers={'X-Group-Id': IDS['own_group']})
IVAN = entry('m_ivan')
add('mark-selected-day', 'owner', mark_path('ivan'), 200, checked('ivan'), method='POST', body={'attendance_day_id': D['m_today']},
    effect=effect(days={'m_today': {'attendees': IVAN}}, audit=[change('attendanceday', 'm_today', {'attendees': ['[]', dumps(IVAN)]})]))
IVAN_LIA = entry('m_ivan', 'm_lia')
add('mark-today', 'full_collaborator', mark_path('lia'), 200, checked('lia'), method='POST', body={},
    effect=effect(days={'m_today': {'attendees': IVAN_LIA}},
                  audit=[change('attendanceday', 'm_today', {'attendees': [dumps(IVAN), dumps(IVAN_LIA)]}, actor='full_collaborator')]))
add('mark-already-checked-in', 'owner', mark_path('lia'), 200, {'msg': f'Ciao {name("lia")}, hai già effettuato il check-in.'},
    method='POST', body={})
add('mark-no-course-today', 'owner', mark_path('dario'), 404, {'msg': f'Ciao {name("dario")}, non hai corsi oggi.'}, method='POST', body={})
add('mark-choose-course', 'owner', mark_path('anna'), 207, {'msg': 'Seleziona il corso per il check-in.', 'data': [
    {'attendance_day_id': D['m_today'], 'date': DAYS['m_today']['date'], 'course': {'course_id': C['m'], 'title': 'Corsa'}},
    {'attendance_day_id': D['n_today'], 'date': DAYS['n_today']['date'], 'course': {'course_id': C['n'], 'title': 'Danza'}}]},
    method='POST', body={})
add('mark-selected-day-with-attendees', 'owner', mark_path('anna'), 500, SERVER, method='POST', body={'attendance_day_id': D['m_today']})
ELENA = entry('n_elena')
ELENA_LESSON = {'date': '{today} 15:00:00+00:00', 'attendance_day_id': D['n_today'], 'course': {'id': C['n'], 'title': 'Danza'},
                'title': 'Danza oggi'}
add('mark-consumes-carnet', 'owner', mark_path('elena'), 200, checked('elena'), method='POST', body={'attendance_day_id': None},
    effect=effect(days={'n_today': {'attendees': ELENA}}, holdings={'elena': meta(2, [ELENA_LESSON])},
                  audit=[change('carnetsubscription', 'elena', {'meta': [dumps(meta(3, [])), dumps(meta(2, [ELENA_LESSON]))]}),
                         change('attendanceday', 'n_today', {'attendees': ['null', dumps(ELENA)]})]))
add('mark-unpaid-carnet', 'owner', mark_path('gina'), 412, {'msg': f'Caro {name("gina")}, non hai ancora pagato il carnet.'},
    method='POST', body={})
add('mark-finished-carnet', 'owner', mark_path('hugo'), 412, {'msg': f'Caro {name("hugo")}, hai finito il carnet.'}, method='POST', body={})
FRANCO = entry('f_franco')
add('mark-franco', 'owner', mark_path('franco'), 200, checked('franco'), method='POST', body={},
    effect=effect(days={'f_today': {'attendees': FRANCO}},
                  audit=[change('attendanceday', 'f_today', {'attendees': ['null', dumps(FRANCO)]}, association='foreign_association')]))
add('mark-selected-day-without-course-subscription', 'owner', mark_path('dario'), 500, SERVER, method='POST',
    body={'attendance_day_id': D['o_today']})
add('mark-selected-day-unknown', 'owner', mark_path('dario'), 404, {'msg': 'Giorno di presenza non trovato.'}, method='POST',
    body={'attendance_day_id': 'a1000000-0000-4000-8000-000000959998'})
add('mark-selected-day-malformed', 'owner', mark_path('dario'), 500, SERVER, method='POST', body={'attendance_day_id': 'nope'})
add('mark-list-body', 'owner', mark_path('dario'), 500, SERVER, method='POST', body=[])
add('mark-athlete', 'athlete', mark_path('dario'), 404, {'msg': f'Ciao {name("dario")}, non hai corsi oggi.'}, method='POST', body={})
add('mark-owner-without-association', 'owner_without_association', mark_path('dario'), 404,
    {'msg': f'Ciao {name("dario")}, non hai corsi oggi.'}, method='POST', body={})
add('mark-scoped-athlete', 'owner', mark_path('dario'), 403, SCOPED_DENIED, method='POST', body={}, headers=SCOPED)
add('mark-scoped-full-foreign-day', 'owner', mark_path('dario'), 403, RESOURCE_DENIED, method='POST',
    body={'attendance_day_id': D['f_today']}, headers=FULL_SCOPED)
add('mark-restricted', 'restricted_collaborator', mark_path('dario'), 403, missing('association.courses.attendance.update'),
    method='POST', body={})
add('mark-anonymous', 'anonymous', mark_path('dario'), 401, ANON, method='POST', body={})
add('mark-admin-without-session', 'admin', mark_path('dario'), 403, ADMIN, method='POST', body={})
add('mark-wrong-method', 'owner', mark_path('dario'), 405, method('GET'))
add('mark-options', 'owner', mark_path('dario'), 200, {'name': 'Attendance Mark', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'Course attendance listing, update, subscription history and check-in with carnet and audit effects',
    'ids': IDS, 'instructor': INSTRUCTOR, 'payments': PAYMENTS, 'carnet': CARNET, 'medical': MEDICAL, 'associates': ASSOCIATES,
    'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'registries': REGISTRIES,
    'days': DAYS, 'holdings': HOLDINGS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
