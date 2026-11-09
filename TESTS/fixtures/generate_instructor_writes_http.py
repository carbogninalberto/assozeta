"""Author the instructor add, update, delete, hours/add/compensation and lessons-hours HTTP sequence (instructor-writes.md).

lessons-hours (instructor/lessons-hours and instructor/<uid>/lessons-hours) runs
check_collaborator_permission, resolves the scope user's association
(SportAssociation.objects.get, None when missing) and the requesting user's own
instructor (associated_user_id = the acting user, within the scope user's
instructors when an association exists), parses start_date/end_date as
Europe/Rome DD/MM/YYYY (400 'Invalid start_date', 'Invalid end_date', 'Invalid
date range'), then answers one instructor (uid: is_valid_uuid, 404 'instructor
not found', 403 'not allowed') or the list. Owners and collaborators without an
own instructor read every non-draft instructor of their association; everyone
else reads only their own instructor (a single object). Hours come from the
published attendance registries of the association: each timed event assigned
to the instructor counts end - start clipped to the period, courses are sorted
by title and include_lessons=true (uid only) adds the events.

add resolves SportAssociation.objects.get(user=request.user) (a 500 when
missing), sets data['user'] (a non-dict or form body is a 500) and saves
InstructorSerializer (fields '__all__'; documents in the create is a 500).
update looks the instructor up by id and user=request.user (404), forces
data['user'] and saves the full (non-partial) serializer: changed fields are
audited, documents are set, a new instructor_id inserts a copy. delete looks
the instructor up the same way and runs the collector: hours and reminders are
deleted (audited, reverse key order), payments and subscriptions keep the row
with instructor NULL, the document links go, then the instructor.

hours/add/compensation checks the uid and 'hours' in the body (400), normalizes
payment_date (strptime of the part before '+', else now), looks the instructor
up by id and user=request.user (404), selects its unpaid hours without payment
among the ids, creates the expense Payment (category 'Compensi e Rimborsi
Spese', the default bank account, the X-Group-Id group), links every hour
(audited) while composing the notes, then saves the notes. Work saved before
an exception stays. Run with ``python3 -I`` after the message-mutations fixture.
"""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

OUTPUT = Path(__file__).with_name('instructor_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{967001 + n:012d}' for n in range(300))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000967999'

DOCUMENTS = {'d1': next(ids), 'd2': next(ids)}
FIELDS = ('first_name', 'last_name', 'email', 'born_date', 'born_city', 'born_province', 'tax_code', 'address_city', 'address',
          'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3', 'phone_4', 'draft',
          'associated_user_id', 'is_volunteer', 'stipulated_contract_in', 'default_hourly_billing', 'default_percentage_billing')


def instructor(owner, first, last='Rossi', **values):
    row = {'id': next(ids), 'owner': owner, 'documents': values.pop('documents', []), **{f: None for f in FIELDS},
           'first_name': first, 'last_name': last, 'role': 'Istruttore', 'draft': False, 'is_volunteer': False,
           'default_hourly_billing': '0.00', 'default_percentage_billing': '0.00'}
    row.update(values)
    return row


INSTRUCTORS = {
    'full': instructor('owner', 'Anna è', email='anna@example.test', born_date='1985-04-03', born_city='Roma', tax_code='RSSNNA85D43H501X',
                       phone='+39 1', study_title='ISEF', associated_user_id=IDS['full_collaborator'], is_volunteer=True,
                       stipulated_contract_in='2026-01-15', default_hourly_billing='25.50', default_percentage_billing=None,
                       documents=['d1', 'd2'], role=None),
    'plain': instructor('owner', 'Paolo', 'Neri', default_hourly_billing='30.00', default_percentage_billing='10.00'),
    'draft': instructor('owner', 'Bruno', draft=True),
    'tied': instructor('owner', 'Tina', associated_user_id=IDS['athlete']),
    'removable': instructor('owner', 'Remo', documents=['d1']),
    'bare': instructor('owner', 'Bea'),
    'comp': instructor('owner', 'Carlo', 'Verdi'),
    'abroad': instructor('foreign_owner', 'Carla'),
    'orphan': instructor('owner_without_association', 'Olga'),
}
I = lambda label: INSTRUCTORS[label]['id']


def hour(instructor, day, hours, amount, **values):
    row = {'id': next(ids), 'instructor': instructor, 'date': day, 'hours': hours, 'amount': amount, 'created': '2026-09-01T08:00:00Z',
           'compensation_type': 'hourly', 'hourly_billing': '10.00', 'percentage_billing': '0.00', 'paid': False, 'payment': None,
           'notes': None, 'calculation_data': []}
    row.update(values)
    return row


PAYMENTS = {'linked': {'id': next(ids), 'instructor': 'comp', 'amount': '9.00'},
            'remo': {'id': next(ids), 'instructor': 'removable', 'amount': '4.00'}}
# Keys increase in seed order, so the heap order and the key order agree.
HOURS = {
    'c_hourly': hour('comp', '2026-09-10', '1.50', '15.00'),
    'c_percent': hour('comp', '2026-09-12', '1.00', '10.50', compensation_type='percentage', percentage_billing='50.00', notes='Lezione è',
                      calculation_data=[{'amount': 10.5, 'full_athlete_name': 'Maria Bianchi', 'course': 'Corso è'},
                                        {'amount': 21, 'full_athlete_name': None, 'course': ['x', 1]},
                                        {'amount': '1e-7', 'full_athlete_name': 'Luca', 'course': 'Basket'}]),
    'c_paid': hour('comp', '2026-09-13', '1.00', '3.00', paid=True),
    'c_linked': hour('comp', '2026-09-13', '1.00', '9.00', payment='linked'),
    'c_group': hour('comp', '2026-09-14', '2.00', '20.00'),
    'c_zero_pct': hour('comp', '2026-09-15', '1.00', '7.00', compensation_type='percentage', percentage_billing='0.00',
                       calculation_data=[{'amount': '7', 'full_athlete_name': 'Sara', 'course': 'Corso A'}]),
    'c_bad': hour('comp', '2026-09-16', '1.00', '5.00', compensation_type='percentage', calculation_data=None),
    'r_a': hour('removable', '2026-09-01', '1.00', '1.00'),
    'r_b': hour('removable', '2026-09-02', '2.00', '2.00', notes='due'),
    'abroad_h': hour('abroad', '2026-09-03', '1.00', '1.00'),
}
REMINDERS = {
    'm_a': {'id': next(ids), 'instructor': 'removable', 'title': 'Promemoria', 'send_at': '2026-09-20T08:00:00Z', 'association': OWN},
    'm_b': {'id': next(ids), 'instructor': 'removable', 'title': 'Secondo', 'send_at': '2026-09-21T08:00:00Z', 'association': None},
}
SUBSCRIPTIONS = {'s_remo': {'id': next(ids), 'instructor': 'removable'}}

# lessons-hours: courses, attendance registries and their calendar events.
COURSES = {'ca': {'id': next(ids), 'title': 'Corso A', 'association': OWN}, 'cb': {'id': next(ids), 'title': 'Basket', 'association': OWN},
           'cf': {'id': next(ids), 'title': 'Estero', 'association': FOREIGN}}
timed = lambda who, start, end, **extra: {'start': start, 'end': end, 'extendedProps': {'instructor': who}, **extra}
one = lambda label: {'instructor_id': I(label)}
REGISTRIES = {
    'reg_a': {'id': next(ids), 'course': 'ca', 'status': 2, 'events': [
        timed(one('full'), '2026-09-15T08:30:00Z', '2026-09-15T10:00:00Z', event_id='e1', title='Lezione 1'),
        timed([one('plain'), one('full')], '2026-09-16T10:00:00+02:00', '2026-09-16T11:15:00+02:00', id=7),
        timed(one('full'), '2026-09-17', '2026-09-18', event_id='e3', allDay=True),
        timed(one('full'), '2026-10-01T18:00:00', '2026-10-01T19:00:00.500000', event_id='e4', title=''),
        timed(one('full'), 'domani', 'dopodomani', event_id='e5'),
        {'end': '2026-09-15T10:00:00Z', 'extendedProps': {'instructor': one('full')}},
        timed(one('full'), 123, 456, event_id='e7'),
        'testo',
        {'event_id': 'e9', 'start': '2026-09-15T08:30:00Z', 'end': '2026-09-15T10:00:00Z'},
        timed({'instructor_id': 5}, '2026-09-15T08:30:00Z', '2026-09-15T10:00:00Z', event_id='e10'),
        timed(one('full'), '2026-08-31T21:00:00Z', '2026-08-31T23:00:00Z', event_id='e11', title='Confine'),
        timed(one('full'), '2026-09-20T10:00:00Z', '2026-09-20T09:00:00Z', event_id='e12'),
        timed(one('full'), '2026-09-21T10:00:00Z', '2026-09-21T11:00:00Z', event_id='e13', allDay='false'),
        timed([one('tied'), 'x'], '2026-09-18T16:00:00.250000Z', '2026-09-18T17:20:00Z', event_id='e14', title='Tina'),
        timed(I('full'), '2026-09-15T08:30:00Z', '2026-09-15T10:00:00Z', event_id='e15'),
        {'event_id': 'e16', 'start': '2026-09-15T08:30:00Z', 'end': '2026-09-15T10:00:00Z', 'extendedProps': 'x'},
        timed(one('full'), '2026-09-15T08:30:00Z', '2026-09-15T09:00:00Z', event_id='a0'),
        timed(one('full'), '2026-09-19T07:00:00+00:00', '2026-09-19T08:00:00+00:00', event_id=0, id='z9', title=42),
    ]},
    'reg_b': {'id': next(ids), 'course': 'cb', 'status': 2, 'events': [
        timed(one('full'), '2026-09-15T15:00:00Z', '2026-09-15T17:00:00Z', event_id='b1', title='Basket'),
        timed(one('draft'), '2026-09-15T15:00:00Z', '2026-09-15T16:00:00Z', event_id='b2'),
        timed(one('plain'), '2026-11-02T09:00:00+01:00', '2026-11-02T10:30:00+01:00', event_id='b3'),
    ]},
    'reg_draft': {'id': next(ids), 'course': 'ca', 'status': 1, 'events': [
        timed(one('full'), '2026-09-15T12:00:00Z', '2026-09-15T13:00:00Z', event_id='d1')]},
    'reg_dict': {'id': next(ids), 'course': 'cb', 'status': 2, 'events': {'k': 1}},
    'reg_foreign': {'id': next(ids), 'course': 'cf', 'status': 2, 'events': [
        timed(one('abroad'), '2026-09-15T08:00:00Z', '2026-09-15T09:30:00Z', event_id='f1'),
        timed(one('full'), '2026-09-15T08:00:00Z', '2026-09-15T09:00:00Z', event_id='f2')]},
}
# instructor/report: a registry of all-day events (lessons-hours skips them),
# attendance days and the course subscriptions their attendees name.
rids = iter(f'a1000000-0000-4000-8000-{967401 + n:012d}' for n in range(80))
told = lambda who, title=None, **extra: {'start': '2026-09-01', 'end': '2026-09-02', 'allDay': True,
                                         'extendedProps': {'instructor': who, **extra.pop('props', {})},
                                         **({} if title is None else {'title': title}), **extra}
# AttendanceDay.associated_event is a UUID: report events carry UUID event ids.
EV = {name: next(rids) for name in ('r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'missing')}
REGISTRIES['reg_report'] = {'id': next(rids), 'course': 'ca', 'status': 2, 'events': [
    told([one('plain'), one('full')], 'Doppia', event_id=EV['r1'], props={'description': 'Descrizione & <b>'}),
    told(one('comp'), 'Numero', event_id=EV['r2'], props={'description': 42}),
    told(one('abroad'), 'Estero', event_id=EV['r3']),
    told(one('draft'), 'Lista', event_id=EV['r4'], props={'description': ['a', 1]}),
    told(one('full'), 'Collegamento', event_id=EV['r5'], props={'description': 'http://example.test/lezione'}),
    told('stringa', 'Testo', event_id=EV['r6']),
    told([one('full'), 'x'], 'Misto', event_id=EV['r7']),
    told(one('full'), event_id=EV['r8']),
    told(one('full'), 'Malformato', event_id=EV['r9']),
    told(one('full'), 'Vuota', event_id=EV['r10'], props={'description': 0}),
]}
PEOPLE = {'mario': {'id': next(rids), 'first_name': 'Mario', 'last_name': 'Rossi'},
          'luisa': {'id': next(rids), 'first_name': 'Luisa', 'last_name': None},
          'paolo': {'id': next(rids), 'first_name': 'Paolo', 'last_name': 'Bianchi'}}
COURSE_SUBSCRIPTIONS = {'cs_mario': {'id': next(rids), 'person': 'mario', 'deleted': False},
                        'cs_luisa': {'id': next(rids), 'person': 'luisa', 'deleted': False},
                        'cs_deleted': {'id': next(rids), 'person': 'paolo', 'deleted': True},
                        'cs_nosub': {'id': next(rids), 'person': None, 'deleted': False}}
CS = lambda label: {'course_subscription_id': COURSE_SUBSCRIPTIONS[label]['id']}
UNKNOWN_CS = {'course_subscription_id': 'a1000000-0000-4000-8000-000000967499'}


def attendance(registry, event, date, title=None, attendees=None, expected=None):
    return {'id': next(rids), 'registry': registry, 'event': EV.get(event, event), 'date': date, 'title': title, 'attendees': attendees,
            'expected_absences': expected}


DAYS = {
    'r1': attendance('reg_report', 'r1', '2026-09-14T22:30:00Z', 'Lezione doppia', [CS('cs_mario'), UNKNOWN_CS], [CS('cs_luisa')]),
    'r1b': attendance('reg_report', 'r1', '2026-09-14T21:59:00Z', 'Prima', [CS('cs_nosub')], []),
    'r2': attendance('reg_report', 'r2', '2026-09-16T06:00:00Z', None, [], None),
    'r3': attendance('reg_report', 'r3', '2026-09-17T06:00:00Z', 'Estero'),
    'r4': attendance('reg_report', 'r4', '2026-09-18T06:00:00Z', 'Lista', [CS('cs_deleted'), {'course_subscription_id': None}]),
    'r5': attendance('reg_report', 'r5', '2026-09-19T06:00:00Z', 'Maiuscolo',
                     [{'course_subscription_id': COURSE_SUBSCRIPTIONS['cs_mario']['id'].upper()}]),
    'r10': attendance('reg_report', 'r10', '2026-09-20T06:00:00Z', 'Zero', {}),
    'nothing': attendance('reg_report', None, '2026-09-20T07:00:00Z', 'Senza evento'),
    'b1': attendance('reg_b', 'missing', '2026-09-21T15:00:00Z', 'Basket', [CS('cs_luisa'), CS('cs_mario')]),
    'err6': attendance('reg_report', 'r6', '2026-10-05T06:00:00Z'),
    'err7': attendance('reg_report', 'r7', '2026-10-06T06:00:00Z'),
    'err8': attendance('reg_report', 'r8', '2026-10-07T06:00:00Z'),
    'err9': attendance('reg_report', 'r9', '2026-10-08T06:00:00Z', None, [{'course_subscription_id': 'non-un-uuid'}]),
    'err_b2': attendance('reg_b', 'missing', '2026-10-12T06:00:00Z'),
    'err_e14': attendance('reg_a', 'missing', '2026-10-13T06:00:00Z'),
    'err_dict': attendance('reg_dict', 'missing', '2026-10-14T06:00:00Z'),
    'foreign': attendance('reg_foreign', 'missing', '2026-09-25T06:00:00Z'),
}
NEW_ADD, NEW_COPY = next(ids), next(ids)
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'instructor-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
IMPERSONATED = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
NOT_INSTRUCTOR, NOT_ALLOWED = {'error': 'instructor not found'}, {'error': 'not allowed'}
JSON_ERROR = {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'}
missing = lambda key: {'detail': f'Missing permission: association.instructor.{key}'}
not_allowed = lambda method: {'detail': f'Method "{method}" not allowed.'}
options = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
FORM, JSON_TYPE = 'application/x-www-form-urlencoded', 'application/json'
REQUIRED, BLANK = 'This field is required.', 'This field may not be blank.'


def authorization(prefix, path, method, permission, name, wrong, description=''):
    body = {} if method != 'GET' else None
    add(f'{prefix}-anonymous', 'anonymous', path, 401, ANONYMOUS, method=method, body=body)
    add(f'{prefix}-admin-without-session', 'admin', path, 403, ADMIN, method=method, body=body)
    add(f'{prefix}-restricted-missing-permission', 'restricted_collaborator', path, 403, missing(permission), method=method, body=body)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', path, 403, SCOPED_DENIED, method=method, body=body, headers=SCOPED)
    add(f'{prefix}-wrong-method', 'owner', path, 405, not_allowed(wrong), method=wrong, body={} if wrong != 'GET' else None)
    add(f'{prefix}-options', 'owner', path, 200, options(name, description), method='OPTIONS')


# --- lessons-hours (reads) ------------------------------------------------------------
ROME = ZoneInfo('Europe/Rome')
LESSONS_DOC = ('Lesson hours computed from the course calendar for one instructor (uid)\nor for all instructors of the current association '
               '(no uid).\n\nAuthorization:\n    - association admins and collaborators may query any instructor\n      of their association or '
               'the whole list\n    - a user tied to an instructor profile (Instructor.associated_user_id)\n      may only query their own hours\n\n'
               'Query params:\n    start_date, end_date: optional Europe/Rome DD/MM/YYYY period filter\n    include_lessons: true adds event details '
               'in single-instructor mode')


def period(start=None, end=None):
    parse = lambda text: datetime.strptime(text, '%d/%m/%Y')
    low = parse(start).replace(tzinfo=ROME).astimezone(timezone.utc) if start else None
    high = (parse(end) + timedelta(days=1)).replace(tzinfo=ROME).astimezone(timezone.utc) if end else None
    return low, high


def lessons(label, association, start=None, end=None, include=False):
    """get_instructor_lessons_hours over the seeded published registries of the association."""
    key, low, high = I(label), *period(start, end)
    aware = lambda value: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    total, count, courses = 0, 0, {}
    for registry in REGISTRIES.values():
        course = COURSES[registry['course']]
        if registry['status'] != 2 or course['association'] != association:
            continue
        seconds, lessons_count, details = 0, 0, []
        for event in registry['events'] if isinstance(registry['events'], list) else []:
            if not isinstance(event, dict) or event.get('allDay'):
                continue
            try:
                begin = aware(datetime.fromisoformat(event['start'].replace('Z', '+00:00')))
                finish = aware(datetime.fromisoformat(event['end'].replace('Z', '+00:00')))
            except (KeyError, AttributeError, ValueError, TypeError):
                continue
            if (low is not None and finish <= low) or (high is not None and begin >= high):
                continue
            props = event.get('extendedProps')
            who = props.get('instructor') if isinstance(props, dict) else None
            if who is None:
                continue
            who = [who] if isinstance(who, dict) else who
            if not isinstance(who, list) or not any(isinstance(w, dict) and str(w.get('instructor_id')) == key for w in who):
                continue
            duration = ((min(finish, high) if high else finish) - (max(begin, low) if low else begin)).total_seconds()
            if duration <= 0:
                continue
            seconds += duration
            lessons_count += 1
            if include:
                details.append({'event_id': str(event.get('event_id') or event.get('id') or ''), 'title': event.get('title') or 'Lezione',
                                'start': begin.astimezone(timezone.utc).isoformat(), 'end': finish.astimezone(timezone.utc).isoformat(),
                                'hours': round(duration / 3600, 2)})
        if lessons_count:
            entry = courses.setdefault(course['id'], {'course_id': course['id'], 'course_title': course['title'], 'lessons_count': 0, 'seconds': 0})
            entry['lessons_count'] += lessons_count
            entry['seconds'] += seconds
            if include:
                entry.setdefault('lessons', []).extend(details)
            total += seconds
            count += lessons_count
    for entry in courses.values():
        entry['hours'] = round(entry.pop('seconds') / 3600, 2)
        if include:
            entry['lessons'].sort(key=lambda lesson: (lesson['start'], lesson['event_id']))
    row = INSTRUCTORS[label]
    return {'total_hours': round(total / 3600, 2), 'lessons_count': count, 'courses': sorted(courses.values(), key=lambda c: c['course_title']),
            'instructor_id': key, 'first_name': row['first_name'], 'last_name': row['last_name']}


LESSONS = '/instructor/lessons-hours'
ONE = lambda label: f'/instructor/{INSTRUCTORS[label]["id"] if label in INSTRUCTORS else label}/lessons-hours'
q = lambda **values: '?' + '&'.join(f'{quote(k)}={quote(v)}' for k, v in values.items())
OWN_LIST = ['full', 'plain', 'tied', 'removable', 'bare', 'comp']
listed = lambda labels, association=OWN, **window: {'data': {'$unordered': [lessons(label, association, **window) for label in labels]}}

add('lessons-owner-list', 'owner', LESSONS, 200, listed(OWN_LIST))
add('lessons-owner-list-period', 'owner', LESSONS + q(start_date='01/09/2026', end_date='30/09/2026'), 200,
    listed(OWN_LIST, start='01/09/2026', end='30/09/2026'))
add('lessons-owner-list-unpadded-start', 'owner', LESSONS + q(start_date='1/9/2026'), 200, listed(OWN_LIST, start='01/09/2026'))
add('lessons-owner-list-include-ignored', 'owner', LESSONS + q(include_lessons='true', end_date='15/09/2026'), 200,
    listed(OWN_LIST, end='15/09/2026'))
add('lessons-admin-impersonating-owner', 'admin', LESSONS, 200, listed(OWN_LIST), headers=IMPERSONATED)
add('lessons-foreign-owner-list', 'foreign_owner', LESSONS, 200, listed(['abroad'], FOREIGN))
add('lessons-owner-one-with-lessons', 'owner', ONE('full') + q(include_lessons='true'), 200, {'data': lessons('full', OWN, include=True)})
add('lessons-owner-one-period-with-lessons', 'owner', ONE('full') + q(include_lessons='true', start_date='01/09/2026', end_date='16/09/2026'),
    200, {'data': lessons('full', OWN, start='01/09/2026', end='16/09/2026', include=True)})
add('lessons-owner-one-include-not-true', 'owner', ONE('plain') + q(include_lessons='True'), 200, {'data': lessons('plain', OWN)})
add('lessons-owner-one-draft', 'owner', ONE('draft'), 200, {'data': lessons('draft', OWN)})
add('lessons-owner-one-foreign-not-allowed', 'owner', ONE('abroad'), 403, NOT_ALLOWED)
add('lessons-owner-one-orphan-not-allowed', 'owner', ONE('orphan'), 403, NOT_ALLOWED)
add('lessons-owner-one-unknown', 'owner', ONE(UNKNOWN), 404, NOT_INSTRUCTOR)
add('lessons-owner-one-malformed', 'owner', ONE('abc'), 400, BAD_UUID)
add('lessons-malformed-after-dates', 'owner', ONE('abc') + q(start_date='31/02/2026'), 400, {'error': 'Invalid start_date'})
add('lessons-invalid-end', 'owner', LESSONS + q(end_date='2026-09-30'), 400, {'error': 'Invalid end_date'})
add('lessons-start-overflow', 'owner', LESSONS + q(start_date='01/01/0001'), 400, {'error': 'Invalid start_date'})
add('lessons-end-overflow', 'owner', LESSONS + q(end_date='31/12/9999'), 400, {'error': 'Invalid end_date'})
add('lessons-range-inverted', 'owner', LESSONS + q(start_date='10/09/2026', end_date='09/09/2026'), 400, {'error': 'Invalid date range'})
add('lessons-same-day', 'owner', ONE('full') + q(start_date='15/09/2026', end_date='15/09/2026'), 200,
    {'data': lessons('full', OWN, start='15/09/2026', end='15/09/2026')})
add('lessons-empty-dates-ignored', 'owner', ONE('tied') + q(start_date='', end_date=''), 200, {'data': lessons('tied', OWN)})
add('lessons-collaborator-own-instructor', 'full_collaborator', LESSONS, 200, {'data': lessons('full', OWN)})
add('lessons-collaborator-own-one', 'full_collaborator', ONE('full') + q(include_lessons='true'), 200, {'data': lessons('full', OWN, include=True)})
add('lessons-collaborator-other-not-allowed', 'full_collaborator', ONE('plain'), 403, NOT_ALLOWED)
add('lessons-athlete-own-instructor', 'athlete', LESSONS, 200, {'data': lessons('tied', OWN)})
add('lessons-athlete-own-one', 'athlete', ONE('tied') + q(include_lessons='true'), 200, {'data': lessons('tied', OWN, include=True)})
add('lessons-athlete-other-not-allowed', 'athlete', ONE('full'), 403, NOT_ALLOWED)
add('lessons-owner-without-association-not-allowed', 'owner_without_association', LESSONS, 403, NOT_ALLOWED)
add('lessons-owner-without-association-one-not-allowed', 'owner_without_association', ONE('orphan'), 403, NOT_ALLOWED)
authorization('lessons', LESSONS, 'GET', 'read', 'Instructor Lessons Hours', 'POST', LESSONS_DOC)
add('lessons-one-restricted-missing-permission', 'restricted_collaborator', ONE('full'), 403, missing('read'))
add('lessons-one-options', 'owner', ONE('full'), 200, options('Instructor Lessons Hours', LESSONS_DOC), method='OPTIONS')

# --- report (a read: the seeded state) ---------------------------------------------------
REPORT = '/instructor/report'
SCHEMA = [('first_name', 'Nome'), ('last_name', 'Cognome'), ('course', 'Corso'), ('title', 'Titolo'), ('description', 'Descrizione'),
          ('date', 'Data'), ('attendees_count', 'Numero di partecipanti'), ('attendees', 'Partecipanti'), ('expected_absences', 'Assenti previsti')]


def names_of(entries):
    """', '.join(lookup.get(str(id), 'Atleta cancellato')): live course subscriptions by their canonical id."""
    lookup = {}
    for row in COURSE_SUBSCRIPTIONS.values():
        if row['deleted']:
            continue
        person = PEOPLE[row['person']] if row['person'] else {'first_name': None, 'last_name': None}
        lookup[row['id']] = f"{person['first_name'] or ''} {person['last_name'] or ''}"
    return ', '.join(lookup.get(str(entry['course_subscription_id']), 'Atleta cancellato') for entry in entries)


def report(association, start, end):
    """get_report over the seeded registries (registry seed order, days by date)."""
    low = datetime.strptime(start, '%d/%m/%Y').replace(tzinfo=ROME).astimezone(timezone.utc)
    high = datetime.strptime(end, '%d/%m/%Y').replace(hour=23, minute=59, second=59, tzinfo=ROME).astimezone(timezone.utc)
    mine = {row['id']: row for row in INSTRUCTORS.values() if IDS[row['owner']] == IDS['owner']} if association == OWN else \
        {row['id']: row for row in INSTRUCTORS.values() if row['owner'] == 'foreign_owner'}
    rows = []
    for label, registry in REGISTRIES.items():
        if COURSES[registry['course']]['association'] != association:
            continue
        days = sorted((d for d in DAYS.values() if d['registry'] == label
                       and low <= datetime.fromisoformat(d['date'].replace('Z', '+00:00')) <= high), key=lambda d: d['date'])
        for day in days:
            assert isinstance(registry['events'], list), 'iterating a dict of events raises'
            event = None
            for candidate in registry['events']:
                assert isinstance(candidate, dict) and 'event_id' in candidate, "e['event_id'] raises"
                if candidate['event_id'] == str(day['event']):
                    event = candidate
                    break
            if event is None:
                continue
            assert 'title' in event, 'a matched event without a title is a 500'
            props = event.get('extendedProps') if isinstance(event.get('extendedProps'), dict) else {}
            who, description = props.get('instructor'), props.get('description')
            assert isinstance(who, (list, dict)), 'a str instructor is a 500'
            candidates = [w.get('instructor_id') for w in who] if isinstance(who, list) else [str(who.get('instructor_id'))]
            for candidate in candidates:
                if candidate not in mine:
                    continue
                person = mine[candidate]
                when = datetime.fromisoformat(day['date'].replace('Z', '+00:00')).astimezone(ROME)
                rows.append({'first_name': person['first_name'], 'last_name': person['last_name'],
                             'course': COURSES[registry['course']]['title'], 'title': day['title'],
                             'description': description if description else '', 'date': when,
                             'attendees_count': len(day['attendees']) if day['attendees'] else 0,
                             'attendees': names_of(day['attendees'] or []), 'expected_absences': names_of(day['expected_absences'] or [])})
    rows.sort(key=lambda row: (row['first_name'], row['last_name'], row['date']))
    for row in rows:
        row['date'] = row['date'].strftime('%d/%m/%Y %H:%M')
    columns = [label for _, label in SCHEMA] if rows else []
    table = [[typed(row[key]) for key, _ in SCHEMA] for row in rows]
    widths = [max(max(len(text_of(row[key])) for row in rows), len(label)) + 2 for key, label in SCHEMA] if rows else []
    spec = {'engine': 'xlsxwriter', 'sheet': 'Sheet1', 'columns': columns, 'rows': table, 'index': False, 'widths': widths}
    return {'data': {'results': rows, 'report_file': {'$xlsx': spec}}}


def typed(value):
    if isinstance(value, bool):
        return {'bool': value}
    if isinstance(value, int):
        return {'int': str(value)}
    if isinstance(value, list):
        return {'list': [typed(item) for item in value]}
    return value


def text_of(value):
    """str() of a cell for astype(str) (the authored columns keep their object or int64 dtype)."""
    return 'None' if value is None else str(value)


RQ = lambda start, end: REPORT + q(start_date=start, end_date=end)
MAIN = report(OWN, '14/09/2026', '30/09/2026')
add('report-owner', 'owner', RQ('14/09/2026', '30/09/2026'), 200, MAIN)
add('report-full-collaborator', 'full_collaborator', RQ('14/09/2026', '30/09/2026'), 200, MAIN)
add('report-admin-impersonating-owner', 'admin', RQ('14/09/2026', '30/09/2026'), 200, MAIN, headers=IMPERSONATED)
add('report-separators', 'owner', RQ('14-09-2026', '30.09.2026'), 200, MAIN)
add('report-unpadded', 'owner', RQ(' 14/9/2026', '30/9/2026 '), 200, MAIN)
add('report-rome-midnight', 'owner', RQ('15/09/2026', '15/09/2026'), 200, report(OWN, '15/09/2026', '15/09/2026'))
add('report-day-before', 'owner', RQ('14/09/2026', '14/09/2026'), 200, report(OWN, '14/09/2026', '14/09/2026'))
add('report-one-day-uppercase-id', 'owner', RQ('19/09/2026', '19/09/2026'), 200, report(OWN, '19/09/2026', '19/09/2026'))
add('report-empty-period', 'owner', RQ('01/11/2026', '30/11/2026'), 200, report(OWN, '01/11/2026', '30/11/2026'))
add('report-inverted-period', 'owner', RQ('30/09/2026', '14/09/2026'), 200, report(OWN, '30/09/2026', '14/09/2026'))
add('report-foreign-owner-empty', 'foreign_owner', RQ('01/11/2026', '30/11/2026'), 200, report(FOREIGN, '01/11/2026', '30/11/2026'))
for name, day in (('instructor-text', '05/10/2026'), ('instructor-list-text', '06/10/2026'), ('event-without-title', '07/10/2026'),
                  ('attendee-malformed-id', '08/10/2026'), ('events-text-before-match', '13/10/2026'), ('events-object', '14/10/2026')):
    add(f'report-{name}-server-error', 'owner', RQ(day, day), 500, SERVER)
add('report-unmatched-event', 'owner', RQ('12/10/2026', '12/10/2026'), 200, report(OWN, '12/10/2026', '12/10/2026'))
add('report-foreign-owner', 'foreign_owner', RQ('01/09/2026', '30/09/2026'), 200, report(FOREIGN, '01/09/2026', '30/09/2026'))
add('report-missing-both', 'owner', REPORT, 400, {'msg': 'Entrambe le date sono richieste.'})
add('report-missing-end', 'owner', REPORT + q(start_date='14/09/2026'), 400, {'msg': 'Entrambe le date sono richieste.'})
add('report-missing-start', 'owner', REPORT + q(end_date='14/09/2026'), 400, {'msg': 'Entrambe le date sono richieste.'})
add('report-nonexistent-date', 'owner', RQ('31/02/2026', '30/09/2026'), 500, SERVER)
add('report-text-date', 'owner', RQ('ieri', '30/09/2026'), 500, SERVER)
add('report-empty-date', 'owner', RQ('', '30/09/2026'), 500, SERVER)
add('report-athlete-denied', 'athlete', REPORT, 403, {'detail': 'User is not a sport association'})
add('report-owner-without-association', 'owner_without_association', RQ('14/09/2026', '30/09/2026'), 500, SERVER)
authorization('report', REPORT, 'GET', 'read', 'Instructor Report', 'POST')

# --- add -------------------------------------------------------------------------------
ADD = '/instructor/add'
effect = lambda actor, rows=None, documents=None, audit=(): {'actor': actor, 'rows': rows or {}, 'documents': documents or {},
                                                             'audit': list(audit)}
index = lambda association, path: {'association': association, 'path': path}
OWNER_INDEX = index(OWN, 'instructor.user.sport_association')
created_columns = lambda **values: {'user_id': IDS['owner'], 'first_name': None, 'last_name': None, 'email': None, 'born_date': None,
                                    'born_city': None, 'born_province': None, 'tax_code': None, 'address_city': None, 'address': None,
                                    'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore', 'phone': None,
                                    'phone_2': None, 'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None,
                                    'is_volunteer': False, 'stipulated_contract_in': None, 'default_hourly_billing': '0.00',
                                    'default_percentage_billing': '0.00', **values}

add('add-minimal', 'owner', ADD, 200, {'msg': 'instructor created'}, method='POST',
    body={'first_name': 'Nuovo', 'last_name': 'Istruttore', 'user': IDS['foreign_owner'], 'ignored': True},
    effect=effect('owner', rows={'Instructor': {'$new': created_columns(first_name='Nuovo', last_name='Istruttore')}},
                  audit=[{'model': 'Instructor', 'key': '$new', 'action': 0, 'changes': '$created', 'index': OWNER_INDEX,
                          'overrides': {'default_hourly_billing': '0.0', 'default_percentage_billing': '0.0'}}]))
FULL_BODY = {'instructor_id': NEW_ADD.upper(), 'first_name': ' Giulia ', 'last_name': 'Bianchi', 'email': '', 'born_date': '03/04/1985',
             'born_city': None, 'tax_code': 'BNCGLI85D43H501X', 'address': 'Via Roma 1', 'civic_number': '10', 'role': 'Allenatrice',
             'phone': 393331234567, 'draft': 'true', 'associated_user_id': '', 'is_volunteer': 1, 'stipulated_contract_in': '1/2/2026',
             'default_hourly_billing': '12.5', 'default_percentage_billing': 7}
add('add-full-collaborator', 'full_collaborator', ADD, 200, {'msg': 'instructor created'}, method='POST', body=FULL_BODY,
    effect=effect('full_collaborator', rows={'Instructor': {NEW_ADD: created_columns(
        first_name='Giulia', last_name='Bianchi', email='', born_date='1985-04-03', tax_code='BNCGLI85D43H501X', address='Via Roma 1',
        civic_number='10', role='Allenatrice', phone='393331234567', draft=True, is_volunteer=True, stipulated_contract_in='2026-02-01',
        default_hourly_billing='12.50', default_percentage_billing='7.00')}},
        audit=[{'model': 'Instructor', 'key': NEW_ADD, 'action': 0, 'changes': '$created', 'index': OWNER_INDEX, 'overrides': {}}]))
INVALID_BODY = {'first_name': '', 'email': 'nope', 'born_date': '1985-04-03', 'associated_user_id': 'zzz', 'draft': 'forse',
                'default_hourly_billing': 'abc', 'default_percentage_billing': '100.00', 'role': '', 'tax_code': 'X' * 26, 'documents': 'd1',
                'is_volunteer': None, 'stipulated_contract_in': 20260101, 'phone': True}
INVALID = {'first_name': [BLANK], 'last_name': [REQUIRED], 'email': ['Enter a valid email address.'],
           'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'], 'associated_user_id': ['Must be a valid UUID.'],
           'draft': ['Must be a valid boolean.'], 'default_hourly_billing': ['A valid number is required.'],
           'default_percentage_billing': ['Ensure that there are no more than 4 digits in total.'], 'role': [BLANK],
           'tax_code': ['Ensure this field has no more than 25 characters.'], 'documents': ['Expected a list of items but got type "str".'],
           'is_volunteer': ['This field may not be null.'], 'phone': ['Not a valid string.'],
           'stipulated_contract_in': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']}
add('add-validation', 'owner', ADD, 400, INVALID, method='POST', body=INVALID_BODY)
add('add-required', 'owner', ADD, 400, {'first_name': [REQUIRED], 'last_name': [REQUIRED]}, method='POST', body={})
add('add-related-errors', 'owner', ADD, 400, {'documents': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
                                             'instructor_id': ['instructor with this instructor id already exists.'],
                                             'default_hourly_billing': ['Ensure that there are no more than 2 decimal places.']},
    method='POST', body={'first_name': 'A', 'last_name': 'B', 'documents': [DOCUMENTS['d1'], UNKNOWN], 'instructor_id': I('full'),
                         'default_hourly_billing': '1.234'})
add('add-document-invalid-uuid', 'owner', ADD, 400, {'documents': ['“nope” is not a valid UUID.']}, method='POST',
    body={'first_name': 'A', 'last_name': 'B', 'documents': ['nope']})
add('add-documents-server-error', 'owner', ADD, 500, SERVER, method='POST', body={'first_name': 'A', 'last_name': 'B', 'documents': []})
add('add-athlete-server-error', 'athlete', ADD, 500, SERVER, method='POST', body={'first_name': 'A', 'last_name': 'B'})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, method='POST', raw_body='nope',
    content_type=JSON_TYPE)
add('add-malformed-json', 'owner', ADD, 400, JSON_ERROR, method='POST', raw_body='nope', content_type=JSON_TYPE)
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, method='POST', body=[{'first_name': 'A'}])
add('add-null-body-server-error', 'owner', ADD, 500, SERVER, method='POST', raw_body='null', content_type=JSON_TYPE)
add('add-form-body-server-error', 'owner', ADD, 500, SERVER, method='POST', raw_body='first_name=A&last_name=B', content_type=FORM)
authorization('add', ADD, 'POST', 'create', 'Instructor Add', 'GET')

# --- update ----------------------------------------------------------------------------
UPD = lambda label: f'/instructor/{INSTRUCTORS[label]["id"] if label in INSTRUCTORS else label}/update'
UPDATED = {'msg': 'instructor updated'}
updated = lambda label, changes, at=OWNER_INDEX: {'model': 'Instructor', 'key': I(label), 'action': 1, 'changes': changes, 'index': at}

add('update-plain-owner', 'owner', UPD('plain'), 200, UPDATED, method='PATCH',
    body={'first_name': 'Paolo', 'last_name': 'Neri Bianchi', 'email': 'paolo@example.test', 'born_date': '02/03/1990',
          'default_hourly_billing': '30', 'default_percentage_billing': None, 'is_volunteer': True, 'phone': ' 333 ',
          'user': IDS['foreign_owner'], 'unknown': 1},
    effect=effect('owner', rows={'Instructor': {'plain': {'last_name': 'Neri Bianchi', 'email': 'paolo@example.test', 'born_date': '1990-03-02',
                                                          'default_percentage_billing': None, 'is_volunteer': True, 'phone': '333'}}},
                  audit=[updated('plain', {'last_name': ['Neri', 'Neri Bianchi'], 'email': ['None', 'paolo@example.test'],
                                           'born_date': ['None', '1990-03-02'], 'default_percentage_billing': ['10.00', 'None'],
                                           'is_volunteer': ['False', 'True'], 'phone': ['None', '333']})]))
add('update-unchanged-no-audit', 'full_collaborator', UPD('plain'), 200, UPDATED, method='PATCH',
    body={'first_name': 'Paolo', 'last_name': 'Neri Bianchi', 'default_hourly_billing': 30}, effect=effect('full_collaborator'))
add('update-documents-replaced', 'owner', UPD('full'), 200, UPDATED, method='PATCH',
    body={'first_name': 'Anna è', 'last_name': 'Rossi', 'documents': [DOCUMENTS['d2']]}, effect=effect('owner', documents={'full': ['d2']}))
add('update-documents-duplicates-admin', 'admin', UPD('tied'), 200, UPDATED, method='PATCH', headers=IMPERSONATED,
    body={'first_name': 'Tina', 'last_name': 'Gialli', 'documents': [DOCUMENTS['d1'], DOCUMENTS['d1']]},
    effect=effect('admin', rows={'Instructor': {'tied': {'last_name': 'Gialli'}}}, documents={'tied': ['d1']},
                  audit=[updated('tied', {'last_name': ['Rossi', 'Gialli']})]))
add('update-copy-new-key', 'owner', UPD('plain'), 200, UPDATED, method='PATCH',
    body={'instructor_id': NEW_COPY, 'first_name': 'Paolo', 'last_name': 'Copia'},
    effect=effect('owner', rows={'Instructor': {NEW_COPY: {'$copy': 'plain', 'last_name': 'Copia'}}},
                  audit=[{'model': 'Instructor', 'key': NEW_COPY, 'action': 1, 'changes': '$created', 'overrides': {},
                          'index': index(OWN, 'actor.sport_association')},
                         {'model': 'Instructor', 'key': NEW_COPY, 'action': 0, 'changes': '$created', 'overrides': {}, 'index': OWNER_INDEX}]))
add('update-orphan-no-index', 'owner_without_association', UPD('orphan'), 200, UPDATED, method='PATCH',
    body={'first_name': 'Olga', 'last_name': 'Nuova', 'associated_user_id': IDS['owner'].upper()},
    effect=effect('owner_without_association', rows={'Instructor': {'orphan': {'last_name': 'Nuova', 'associated_user_id': IDS['owner']}}},
                  audit=[updated('orphan', {'last_name': ['Rossi', 'Nuova'], 'associated_user_id': ['None', IDS['owner']]}, None)]))
add('update-taken-key', 'owner', UPD('bare'), 400, {'instructor_id': ['instructor with this instructor id already exists.']}, method='PATCH',
    body={'instructor_id': I('comp'), 'first_name': 'Bea', 'last_name': 'Rossi'})
add('update-own-key-accepted', 'owner', UPD('bare'), 200, UPDATED, method='PATCH',
    body={'instructor_id': I('bare'), 'first_name': 'Bea', 'last_name': 'Rossi'}, effect=effect('owner'))
add('update-validation', 'owner', UPD('bare'), 400, INVALID, method='PATCH', body=INVALID_BODY)
add('update-full-required', 'owner', UPD('bare'), 400, {'last_name': [REQUIRED]}, method='PATCH', body={'first_name': 'Bea'})
add('update-associated-user-blank', 'owner', UPD('bare'), 400, {'associated_user_id': ['Must be a valid UUID.']}, method='PATCH',
    body={'first_name': 'Bea', 'last_name': 'Rossi', 'associated_user_id': ''})
add('update-unknown', 'owner', UPD(UNKNOWN), 404, NOT_INSTRUCTOR, method='PATCH', raw_body='nope', content_type=JSON_TYPE)
add('update-foreign', 'owner', UPD('abroad'), 404, NOT_INSTRUCTOR, method='PATCH', body={'first_name': 'X', 'last_name': 'Y'})
add('update-athlete', 'athlete', UPD('tied'), 404, NOT_INSTRUCTOR, method='PATCH', body={'first_name': 'X', 'last_name': 'Y'})
add('update-malformed', 'owner', UPD('abc'), 400, BAD_UUID, method='PATCH', body={})
add('update-malformed-json', 'owner', UPD('bare'), 400, JSON_ERROR, method='PATCH', raw_body='nope', content_type=JSON_TYPE)
add('update-list-body-server-error', 'owner', UPD('bare'), 500, SERVER, method='PATCH', body=[])
add('update-null-body-server-error', 'owner', UPD('bare'), 500, SERVER, method='PATCH', raw_body='null', content_type=JSON_TYPE)
add('update-form-body-server-error', 'owner', UPD('bare'), 500, SERVER, method='PATCH', raw_body='first_name=A&last_name=B', content_type=FORM)
authorization('update', UPD('bare'), 'PATCH', 'update', 'Instructor Update', 'POST')

# --- hours/add/compensation ---------------------------------------------------------------
COMP = lambda label='comp': f'/instructor/{INSTRUCTORS[label]["id"] if label in INSTRUCTORS else label}/hours/add/compensation'
H = lambda label: HOURS[label]['id']
CREATED_COMP = {'msg': 'compensation created.'}


def notes_for(labels):
    """The notes the view composes for the selected hours (heap order)."""
    text = ''
    for i, label in enumerate(labels):
        row = HOURS[label]
        amount_text, percentage = row['amount'], Decimal(row['percentage_billing']) if row['percentage_billing'] is not None else None
        if row['compensation_type'] == 'hourly':
            text += f"Compenso per il giorno {row['date']}, ore {row['hours']} con importo totale di {amount_text}€\n"
        else:
            text += f"Compenso per il giorno {row['date']}, importo di {amount_text}€\n"
            text += f"Note: {row['notes'] if row['notes'] and row['notes'] != '' else '-'}\n"
            text += "Nel dettaglio:\n\n"
            for idx, data in enumerate(row['calculation_data']):
                amount = Decimal(str(data['amount']))
                scaled = amount * (percentage / 100) if percentage else amount
                text += (f"{idx + 1}° Pagamento\nAtleta: {data['full_athlete_name']} \nCorso: {data['course']}\n"
                         f"Importo: {amount} € * {percentage} % = {scaled} €\n\n")
        if i != len(labels) - 1:
            text += '\n ---------------- \n\n'
    return text


def payment_columns(amount, payment_date, notes, group=None):
    return {'type': 'default', 'subject': 0, 'amount': amount, 'payment_date': payment_date, 'paid': False, 'expense': True,
            'description': 'Compensi e Rimborsi Spese per Carlo Verdi', 'payment_category_id': '$compensation-category',
            'meta_payment_categories': None, 'invoice_id': None, 'user_id': IDS['owner'], 'associate_id': None, 'supplier_id': None,
            'instructor_id': I('comp'), 'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None,
            'meta': None, 'notes': notes, 'custom_accounts_id': '$default-bank-account', 'signature_id': None, 'deleted': False,
            'group_id': group, 'course': None}


DIRECT = index(OWN, 'direct')
linked = lambda label: {'model': 'InstructorHours', 'key': H(label), 'action': 1, 'changes': {'payment': ['None', '$new']}, 'index': OWNER_INDEX}
# The create entry precedes the notes save: notes are None in memory.
payment_created = lambda overrides=None: {'model': 'Payment', 'key': '$new', 'action': 0, 'changes': '$created', 'index': DIRECT,
                                          'overrides': {'notes': None, **(overrides or {})}}
payment_notes = lambda changes: {'model': 'Payment', 'key': '$new', 'action': 1, 'changes': changes, 'index': DIRECT}

add('comp-malformed', 'owner', COMP('abc'), 400, BAD_UUID, method='POST', raw_body='nope', content_type=JSON_TYPE)
add('comp-hours-missing', 'owner', COMP(UNKNOWN), 400, {'error': 'hours not found'}, method='POST', body={'payment_date': 'x'})
add('comp-empty-list-body', 'owner', COMP(), 400, {'error': 'hours not found'}, method='POST', body=[])
add('comp-form-without-hours', 'owner', COMP(), 400, {'error': 'hours not found'}, method='POST', raw_body='payment_date=x', content_type=FORM)
add('comp-null-body-server-error', 'owner', COMP(), 500, SERVER, method='POST', raw_body='null', content_type=JSON_TYPE)
add('comp-list-with-hours-server-error', 'owner', COMP(), 500, SERVER, method='POST', body=['hours'])
add('comp-form-server-error', 'owner', COMP(), 500, SERVER, method='POST', raw_body='hours=' + H('c_hourly'), content_type=FORM)
add('comp-unknown-instructor', 'owner', COMP(UNKNOWN), 404, NOT_INSTRUCTOR, method='POST', body={'hours': [H('c_hourly')]})
add('comp-foreign-instructor', 'owner', COMP('abroad'), 404, NOT_INSTRUCTOR, method='POST', body={'hours': [H('abroad_h')]})
add('comp-athlete', 'athlete', COMP(), 404, NOT_INSTRUCTOR, method='POST', body={'hours': []})
add('comp-hours-string-server-error', 'owner', COMP(), 500, SERVER, method='POST', body={'hours': 'abc'})
add('comp-hours-invalid-id-server-error', 'owner', COMP(), 500, SERVER, method='POST', body={'hours': [H('c_hourly'), 'nope']})
add('comp-owner-without-association-server-error', 'owner_without_association', COMP('orphan'), 500, SERVER, method='POST',
    body={'hours': []})
SELECTED = ['c_hourly', 'c_percent']
add('comp-owner-selected-hours', 'owner', COMP(), 200, CREATED_COMP, method='POST',
    body={'hours': [H('c_hourly'), H('c_percent'), H('c_paid'), H('c_linked'), UNKNOWN, H('abroad_h')],
          'payment_date': '2026-09-30T10:00:00.25+02:00'},
    effect=effect('owner', rows={'Payment': {'$new': payment_columns('25.50', '2026-09-30T10:00:00.250000Z', notes_for(SELECTED))},
                                 'InstructorHours': {label: {'payment_id': '$new'} for label in SELECTED}},
                  audit=[payment_created(), linked('c_hourly'), linked('c_percent'), payment_notes({'notes': ['None', notes_for(SELECTED)]})]))
add('comp-empty-hours-collaborator', 'full_collaborator', COMP(), 200, CREATED_COMP, method='POST', body={'hours': [], 'payment_date': 'ieri'},
    effect=effect('full_collaborator', rows={'Payment': {'$new': payment_columns('0.00', '$now', '')}},
                  audit=[payment_created({'amount': '0'}), payment_notes({'notes': ['None', ''], 'amount': ['0.00', '0']})]))
GROUPED = ['c_group', 'c_zero_pct']
add('comp-group-now', 'owner', COMP(), 200, CREATED_COMP, method='POST', headers={'X-Group-Id': IDS['own_group']},
    body={'hours': [H('c_zero_pct'), H('c_group')]},
    effect=effect('owner', rows={'Payment': {'$new': payment_columns('27.00', '$now', notes_for(GROUPED), IDS['own_group'])},
                                 'InstructorHours': {label: {'payment_id': '$new'} for label in GROUPED}},
                  audit=[payment_created(), linked('c_group'), linked('c_zero_pct'), payment_notes({'notes': ['None', notes_for(GROUPED)]})]))
add('comp-bad-calculation-keeps-saved-work', 'owner', COMP(), 500, SERVER, method='POST', body={'hours': [H('c_bad')], 'payment_date': 123},
    effect=effect('owner', rows={'Payment': {'$new': payment_columns('5.00', '$now', None)}, 'InstructorHours': {'c_bad': {'payment_id': '$new'}}},
                  audit=[payment_created(), linked('c_bad')]))
authorization('comp', COMP(), 'POST', 'hours.create', 'Instructor Hours Add Compensation', 'GET')

# --- delete ----------------------------------------------------------------------------
DEL = lambda label: f'/instructor/{INSTRUCTORS[label]["id"] if label in INSTRUCTORS else label}/delete'
DELETED = {'msg': 'instructor deleted'}
gone = lambda model, key, at: {'model': model, 'key': key, 'action': 2, 'changes': '$deleted', 'index': at}

add('delete-unknown', 'owner', DEL(UNKNOWN), 404, NOT_INSTRUCTOR, method='DELETE')
add('delete-foreign', 'owner', DEL('abroad'), 404, NOT_INSTRUCTOR, method='DELETE')
add('delete-athlete', 'athlete', DEL('tied'), 404, NOT_INSTRUCTOR, method='DELETE')
add('delete-malformed', 'owner', DEL('abc'), 400, BAD_UUID, method='DELETE')
authorization('delete', DEL('bare'), 'DELETE', 'delete', 'Instructor Delete', 'POST')
add('delete-removable-cascade-collaborator', 'full_collaborator', DEL('removable'), 200, DELETED, method='DELETE',
    effect=effect('full_collaborator', rows={'Instructor': {'removable': None}, 'InstructorHours': {'r_a': None, 'r_b': None},
                                             'Reminders': {'m_a': None, 'm_b': None}, 'Payment': {'remo': {'instructor_id': None}},
                                             'Subscription': {'s_remo': {'instructor_id': None}}},
                  documents={'removable': []},
                  audit=[gone('InstructorHours', H('r_b'), None), gone('InstructorHours', H('r_a'), None),
                         gone('Reminders', REMINDERS['m_b']['id'], None),
                         gone('Reminders', REMINDERS['m_a']['id'], index(OWN, 'changes_json.sport_association')),
                         gone('Instructor', I('removable'), None)]))
add('delete-bare-owner', 'owner', DEL('bare'), 200, DELETED, method='DELETE',
    effect=effect('owner', rows={'Instructor': {'bare': None}}, audit=[gone('Instructor', I('bare'), index(OWN, 'actor.sport_association'))]))
add('delete-orphan-no-index', 'owner_without_association', DEL('orphan'), 200, DELETED, method='DELETE',
    effect=effect('owner_without_association', rows={'Instructor': {'orphan': None}}, audit=[gone('Instructor', I('orphan'), None)]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Instructor writes and lesson hours authored sequence', 'ids': IDS,
                              'documents': DOCUMENTS, 'instructors': INSTRUCTORS, 'hours': HOURS, 'payments': PAYMENTS, 'reminders': REMINDERS,
                              'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'registries': REGISTRIES, 'people': PEOPLE,
                              'course_subscriptions': COURSE_SUBSCRIPTIONS, 'days': DAYS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
