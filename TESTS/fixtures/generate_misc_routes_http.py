"""Author the misc-routes HTTP sequence (misc-routes.md): calendar/events/export,
communications/send/post and subscription/<uid>/card.

calendar/events/export (full_events_calendar_export, attendee_views.py,
@api_view(['GET']), IsAuthenticated + IsAssociationUser, registry
association.calendar.read): is_valid_uuid(course_id) when the parameter is
present (400 list), then the live group-aware courses of
request.user.sport_association (500 without one), narrowed to course_id, and
their scoped registries in database order. Every event of a truthy events
value becomes a VEVENT: SUMMARY title, DTSTART parse_datetime(start), DTEND
parse_datetime(end) or start + 1 hour when end is None or "None", DTSTAMP
datetime.now(), UID event_id and DESCRIPTION "Instructor: <label>" when
extendedProps.instructor is a dict with a label. icalendar 6.1.1 renders the
properties in its canonical order (SUMMARY, DTSTART, DTEND, DTSTAMP, UID, then
DESCRIPTION), TEXT escaping (\\N first, then backslash, ';', ',', CRLF and LF),
str() of non-text values, naive datetimes as floating local time, UTC ones with
'Z', DTSTAMP in UTC, folding at 75 octets. The body is the bare calendar
(no PRODID/VERSION) as text/calendar with an attachment disposition. Missing
keys, non-dict events, unparseable dates and an extendedProps value that
contains 'instructor' without being a dict raise (500).

communications/send/post (configuration_send_post, communications/views.py,
@api_view(['POST']), IsAuthenticated, not in the registry): PostSerializer's
is_valid reads initial_data.get('message') (a non-dict body is a 500; a falsy
message is 400 ["Message is required"]) before the CharField(max_length=10000)
checks (400 {'message': [...]}); then request.user.sport_association (athletes
403 'User is not a sport association', no live association 500), one
Message (INSIDE_APP, audited create, direct index) and one MessageTransaction
whose recipient is the association id (not audited); 200 {'msg': 'post sent.'}.

subscription/<uid>/card (subscription_card, subscriptions_views.py,
@api_view(['GET', 'POST']), no permission classes: AllowAny; registry GET
association.members.read, POST association.members.update): is_valid_uuid(uid).
GET: a missing or empty token is 400 {'msg': 'il token è scaduto'}; the token
lookup filter(token=..., subscription_id=uid).first() (a value the UUID field
rejects is a 500); an unknown token goes to source_token, which answers None
without a self-hosted pairing (400); an expired token is deleted (audited) and
answers 400; then the scoped group-aware subscription (None: 500), the
association's first membership card configuration (created and audited when
missing) and the member card. POST: anonymous or a non-association role is 403
{'msg': 'User not allowed.'}; the scoped subscription of
request.user.sport_association (404 {'msg': 'Subscription not found.'}); a new
SubscriptionToken expiring in three days (audited, indexed through the
subscription) and 200 {'token': <uuid>}.
Run with ``python3 -I`` after the message-mutations fixture.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('misc_routes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
SERVER = '<stable-server-error-page>'
ids = iter(f'a1000000-0000-4000-8000-0000e1{n:06d}' for n in range(1, 1000))

ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
ADMIN_FOREIGN = {'X-Impersonation-Id': '$session:admin_foreign', 'User-Id': IDS['foreign_owner']}
GENERIC_DENIED = {'detail': 'You do not have permission to perform this action.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
BAD_UUID = ['badly formed hexadecimal UUID string']


def options(name, description=''):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def missing(permission):
    return {'detail': f'Missing permission: {permission}'}


def unmapped(path):
    return {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}


cases = []


def add(name, actor, method, path, status, response, *, headers=None, body=None, raw_body=None, content_type=None, effect=None):
    case = {'name': f'misc-routes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# ---------------------------------------------------------------------------
# calendar/events/export
# ---------------------------------------------------------------------------

def course(association=OWN, group=None, deleted=False, title='Corso'):
    return {'id': next(ids), 'association': association, 'group': group, 'deleted': deleted, 'title': title}


def event(title, start, end, event_id=None, **extra):
    row = {'event_id': event_id if event_id is not None else next(ids), 'title': title, 'start': start, 'end': end, 'allDay': False}
    row.update(extra)
    return row


def instructor(label):
    return {'extendedProps': {'instructor': {'instructor_id': next(ids), 'label': label}}}


LONG_ASCII = 'Allenamento agonistico ' + 'avanzato ' * 15 + 'fine'
LONG_TEXT = 'Attività motoria per bambini più piccoli: giochi, percorsi e psicomotricità à è ì ò ù — sessione completa'
MAIN_EVENTS = [
    event('Lezione di nuoto', '2026-03-02T17:00:00Z', '2026-03-02T18:30:00Z', **instructor('Mario Rossi')),
    event('Yoga, pilates; stretching \\ base\nlivello 2\r\nfine', '2026-03-03T09:15:00', None, event_id='EVENTO-MAIUSCOLO,1'),
    event(LONG_TEXT, '2026-03-04T07:05:09.123456+00:00', 'None', extendedProps={'instructor': {'instructor_id': 'x'}}),
    event(LONG_ASCII, '2026-03-05T10:00:00Z', '2026-03-05T11:00:00Z', extendedProps={'instructor': [{'label': 'Lista'}]}),
    event('Note libere', '2026-03-06T10:00:00Z', '2026-03-06T11:00:00Z', extendedProps='nota senza istruttore'),
    event(42, '2026-03-07T10:00:00Z', '2026-03-07T11:00:00Z', event_id=7, **instructor(5)),
    event(None, '2026-03-08T10:00:00Z', '2026-03-08T11:00:00Z', event_id=True, **instructor(None)),
    event('Giornata intera', '2026-03-10', '2026-03-10 12:00', **instructor(True)),
    event('Offset zero', '2026-03-11T10:30+00:00', '2026-03-11T11:45Z', extendedProps={'description': 'senza istruttore'}),
    event('Formato breve', '2026-3-5 9:05', '2026-03-05T10:05:07,5', extendedProps={}),
    event('\\Nuovo corso; base', '20260312T081500Z', '2026-03-12T09:15:00.000001Z', **instructor('Ana, Bea; Cy\\')),
]
GROUP_EVENTS = [event('Gruppo serale', '2026-04-01T19:00:00Z', '2026-04-01T20:00:00Z', **instructor('Luca Bianchi'))]
COURSES = {
    'main': course(title='Nuoto'),
    'grouped': course(group='own_group', title='Serale'),
    'deleted': course(deleted=True, title='Cancellato'),
    'null_events': course(title='Senza eventi'),
    'empty_events': course(title='Lista vuota'),
    'no_registry': course(title='Senza registro'),
    'foreign_ok': course(FOREIGN, title='Estero'),
    'foreign_no_title': course(FOREIGN),
    'foreign_bad_start': course(FOREIGN),
    'foreign_bad_end': course(FOREIGN),
    'foreign_no_end': course(FOREIGN),
    'foreign_string_event': course(FOREIGN),
    'foreign_dict_events': course(FOREIGN),
    'foreign_props_instructor_text': course(FOREIGN),
    'foreign_props_null': course(FOREIGN),
}
FOREIGN_OK_EVENTS = [event('Corso estero', '2026-05-01T08:00:00Z', None, **instructor('Hans'))]
REGISTRIES = {
    'main': {'id': next(ids), 'course': 'main', 'events': MAIN_EVENTS},
    'grouped': {'id': next(ids), 'course': 'grouped', 'events': GROUP_EVENTS},
    'deleted': {'id': next(ids), 'course': 'deleted', 'events': [event('Mai visto', '2026-03-01T10:00:00Z', None)]},
    'null_events': {'id': next(ids), 'course': 'null_events', 'events': None},
    'empty_events': {'id': next(ids), 'course': 'empty_events', 'events': []},
    'foreign_ok': {'id': next(ids), 'course': 'foreign_ok', 'events': FOREIGN_OK_EVENTS},
    'foreign_no_title': {'id': next(ids), 'course': 'foreign_no_title',
                         'events': [{'event_id': next(ids), 'start': '2026-03-01T10:00:00Z', 'end': None}]},
    'foreign_bad_start': {'id': next(ids), 'course': 'foreign_bad_start', 'events': [event('x', 'domani alle dieci', None)]},
    'foreign_bad_end': {'id': next(ids), 'course': 'foreign_bad_end', 'events': [event('x', '2026-03-01T10:00:00Z', 'dopo')]},
    'foreign_no_end': {'id': next(ids), 'course': 'foreign_no_end',
                       'events': [{'event_id': next(ids), 'title': 'x', 'start': '2026-03-01T10:00:00Z'}]},
    'foreign_string_event': {'id': next(ids), 'course': 'foreign_string_event', 'events': ['evento']},
    'foreign_dict_events': {'id': next(ids), 'course': 'foreign_dict_events', 'events': {'evento': 1}},
    'foreign_props_instructor_text': {'id': next(ids), 'course': 'foreign_props_instructor_text',
                                      'events': [event('x', '2026-03-01T10:00:00Z', None, extendedProps='instructor: Mario')]},
    'foreign_props_null': {'id': next(ids), 'course': 'foreign_props_null',
                           'events': [event('x', '2026-03-01T10:00:00Z', None, extendedProps=None)]},
}
# Appended after the rows above (their ids stay as they were authored).
ZONED_COURSES = ('foreign_zoned', 'foreign_offset_day_start', 'foreign_offset_day_end', 'foreign_offset_past_year')
for _label in ZONED_COURSES:
    COURSES[_label] = course(FOREIGN)
REGISTRIES['foreign_zoned'] = {'id': next(ids), 'course': 'foreign_zoned', 'events': 'ZONED'}
# Float values are str() of the Python float (repr).
COURSES['foreign_floats'] = course(FOREIGN)
REGISTRIES['foreign_floats'] = {'id': next(ids), 'course': 'foreign_floats', 'events': [
    event(2.5, '2026-03-07T10:00:00Z', '2026-03-07T11:00:00Z', event_id=0.1, **instructor(1e-07))]}
# A list becomes one property line per item (an empty list none), each str(item); a dict is its repr.
COURSES['foreign_containers'] = course(FOREIGN)
REGISTRIES['foreign_containers'] = {'id': next(ids), 'course': 'foreign_containers', 'events': [
    event(['Primo', 'Secondo, due', 3, None, [1, 'x']], '2026-03-07T10:00:00Z', '2026-03-07T11:00:00Z', event_id=['id-a', 'id-b'],
          **instructor(['Ana', {'k': 1.5}])),
    event({'it': "L'uno", 'n': [True, None]}, '2026-03-08T10:00:00Z', '2026-03-08T11:00:00Z', event_id={'x': 'y'},
          **instructor({'nome': 'Bea'})),
    event([], '2026-03-09T10:00:00Z', '2026-03-09T11:00:00Z', event_id=[], **instructor([]))]}
# An offset of a whole day or more is no timezone (ValueError); the last hour of year 9999 plus the default hour overflows.
REGISTRIES['foreign_offset_day_start'] = {'id': next(ids), 'course': 'foreign_offset_day_start',
                                          'events': [event('x', '2019-06-10T18:00:00+24:00', None)]}
REGISTRIES['foreign_offset_day_end'] = {'id': next(ids), 'course': 'foreign_offset_day_end',
                                        'events': [event('x', '2019-06-10T18:00:00+02:00', '2019-6-10 9:05-99:00')]}
REGISTRIES['foreign_offset_past_year'] = {'id': next(ids), 'course': 'foreign_offset_past_year',
                                          'events': [event('x', '9999-12-31T23:30:00+02:00', None)]}


def escape(text):
    return (text.replace('\\N', '\n').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\\n').replace('\n', '\\n'))


def fold(line):
    out, size = [], 0
    for char in line:
        width = len(char.encode())
        size += width
        if size >= 75:
            out.append('\r\n ')
            size = width
        out.append(char)
    return ''.join(out)


def text(value):
    """str() of the JSON value as Python renders it."""
    if value is None:
        return 'None'
    if value is True or value is False:
        return str(value)
    return str(value)


def texts(value):
    """The property lines icalendar writes: one per list item (str of each), else str(value)."""
    return [text(item) for item in value] if isinstance(value, list) else [text(value)]


def moment(value):
    """(naive datetime, utc flag) of the authored formats."""
    raw = value
    utc = False
    for suffix in ('Z', '+00:00'):
        if raw.endswith(suffix):
            raw, utc = raw[:-len(suffix)], True
    raw = raw.replace(',', '.')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y%m%dT%H%M%S'):
        try:
            return datetime.strptime(raw, fmt), utc
        except ValueError:
            pass
    year_month_day, clock = raw.split(' ')
    year, month, day = (int(part) for part in year_month_day.split('-'))
    hour, minute = (int(part) for part in clock.split(':'))
    return datetime(year, month, day, hour, minute), utc


def stamp(value):
    moment_, utc = value
    return moment_.strftime('%Y%m%dT%H%M%S') + ('Z' if utc else '')


# Events with a UTC offset (older clients saved local times). icalendar names a fixed offset with the first identifier of
# its equivalent-timezone table (+01:00 Africa/Bangui, +02:00 Africa/Blantyre, ...) and, for an offset the table lacks,
# with the timezone's own name, quoted because of its colon. label: (start, end, DTSTART and DTEND after the name).
ZONED = {
    'Ora legale': ('2019-06-10T18:00:00+02:00', '2019-06-10T19:00:00+02:00',
                   ';TZID=Africa/Blantyre:20190610T180000', ';TZID=Africa/Blantyre:20190610T190000'),
    'Ora solare': ('2019-01-10T18:00:00.000+01:00', None, ';TZID=Africa/Bangui:20190110T180000', ';TZID=Africa/Bangui:20190110T190000'),
    'Compatto': ('20190610T180000+0200', '2019-06-10 19:00-03:30',
                 ';TZID=Africa/Blantyre:20190610T180000', ';TZID=America/Paramaribo:20190610T190000'),
    'Nepal': ('2019-06-10T18:00:00+05:45', '2019-06-10T19:00:00Z', ';TZID="UTC+05:45":20190610T180000', ':20190610T190000Z'),
    'Solo ore': ('2019-06-10T18+02', '2019-06-10T19:00:00-00:30', ';TZID=Africa/Blantyre:20190610T180000', ';TZID="UTC-00:30":20190610T190000'),
    'Forma breve': ('2019-6-10 9:05+02:00', '2019-06-10T10:00:00 +0100', ';TZID=Africa/Blantyre:20190610T090500', ';TZID=Africa/Bangui:20190610T100000'),
    'Ovest': ('2019-06-10T18:00:00-05:00', 'None', ';TZID=America/Atikokan:20190610T180000', ';TZID=America/Atikokan:20190610T190000'),
    'Mezzanotte': ('2019-12-31T23:30:00+09:00', None, ';TZID=Asia/Jayapura:20191231T233000', ';TZID=Asia/Jayapura:20200101T003000'),
}


def vevent(row):
    if isinstance(row['title'], str) and row['title'] in ZONED:
        zoned = ZONED[row['title']]
        return [fold(line) for line in ('BEGIN:VEVENT', 'SUMMARY:' + row['title'], 'DTSTART' + zoned[2], 'DTEND' + zoned[3],
                                        'DTSTAMP:$dtstamp', 'UID:' + row['event_id'], 'END:VEVENT')]
    start = moment(row['start'])
    end = moment(row['end']) if row['end'] is not None and row['end'] != 'None' else (start[0] + timedelta(hours=1), start[1])
    lines = (['BEGIN:VEVENT'] + ['SUMMARY:' + escape(item) for item in texts(row['title'])] + ['DTSTART:' + stamp(start),
             'DTEND:' + stamp(end), 'DTSTAMP:$dtstamp'] + ['UID:' + escape(item) for item in texts(row['event_id'])])
    props = row.get('extendedProps')
    if isinstance(props, dict) and isinstance(props.get('instructor'), dict) and 'label' in props['instructor']:
        lines.append('DESCRIPTION:' + escape('Instructor: ' + text(props['instructor']['label'])))
    lines.append('END:VEVENT')
    return [fold(line) for line in lines]


def calendar(*registries):
    lines = ['BEGIN:VCALENDAR']
    for label in registries:
        for row in REGISTRIES[label]['events']:
            lines.extend(vevent(row))
    lines.append('END:VCALENDAR')
    return {'$ics': ''.join(line + '\r\n' for line in lines)}


EXPORT = '/calendar/events/export'


def export(query=''):
    return EXPORT + query


ALL_OWN = calendar('main', 'grouped')
REGISTRIES['foreign_zoned']['events'] = [event(title, start, end) for title, (start, end, _, _) in ZONED.items()]
add('export-offset-times', 'foreign_owner', 'GET', export(f'?course_id={COURSES["foreign_zoned"]["id"]}'), 200, calendar('foreign_zoned'))
add('export-container-texts', 'foreign_owner', 'GET', export(f'?course_id={COURSES["foreign_containers"]["id"]}'), 200,
    calendar('foreign_containers'))
add('export-float-texts', 'foreign_owner', 'GET', export(f'?course_id={COURSES["foreign_floats"]["id"]}'), 200, calendar('foreign_floats'))
for label in ZONED_COURSES[1:]:
    add('export-' + label.replace('_', '-') + '-server-error', 'foreign_owner', 'GET', export(f'?course_id={COURSES[label]["id"]}'),
        500, SERVER)
add('export-owner', 'owner', 'GET', export(), 200, ALL_OWN)
add('export-full-collaborator', 'full_collaborator', 'GET', export(), 200, ALL_OWN)
add('export-admin-impersonating-owner', 'admin', 'GET', export(), 200, ALL_OWN, headers=ADMIN_OWNER)
add('export-group-header', 'owner', 'GET', export(), 200, calendar('grouped'), headers={'X-Group-Id': IDS['own_group']})
add('export-foreign-group-header', 'owner', 'GET', export(), 200, calendar(), headers={'X-Group-Id': IDS['foreign_group']})
add('export-course-main', 'owner', 'GET', export(f'?course_id={COURSES["main"]["id"]}'), 200, calendar('main'))
add('export-course-upper-case', 'owner', 'GET', export(f'?course_id={COURSES["grouped"]["id"].upper()}'), 200, calendar('grouped'))
add('export-course-deleted', 'owner', 'GET', export(f'?course_id={COURSES["deleted"]["id"]}'), 200, calendar())
add('export-course-without-events', 'owner', 'GET', export(f'?course_id={COURSES["null_events"]["id"]}'), 200, calendar())
add('export-course-without-registry', 'owner', 'GET', export(f'?course_id={COURSES["no_registry"]["id"]}'), 200, calendar())
add('export-course-other-association', 'owner', 'GET', export(f'?course_id={COURSES["foreign_ok"]["id"]}'), 200, calendar())
add('export-course-unknown', 'owner', 'GET', export('?course_id=a1000000-0000-4000-8000-0000e1999999'), 200, calendar())
add('export-course-malformed', 'owner', 'GET', export('?course_id=abc'), 400, BAD_UUID)
add('export-course-empty', 'owner', 'GET', export('?course_id='), 400, BAD_UUID)
add('export-foreign-owner', 'foreign_owner', 'GET', export(f'?course_id={COURSES["foreign_ok"]["id"]}'), 200, calendar('foreign_ok'))
add('export-admin-impersonating-foreign', 'admin', 'GET', export(f'?course_id={COURSES["foreign_ok"]["id"]}'), 200,
    calendar('foreign_ok'), headers=ADMIN_FOREIGN)
for label in ('foreign_no_title', 'foreign_bad_start', 'foreign_bad_end', 'foreign_no_end', 'foreign_string_event',
              'foreign_dict_events', 'foreign_props_instructor_text', 'foreign_props_null'):
    add('export-' + label.replace('_', '-') + '-server-error', 'foreign_owner', 'GET', export(f'?course_id={COURSES[label]["id"]}'),
        500, SERVER)
add('export-anonymous', 'anonymous', 'GET', export(), 401, ANONYMOUS)
add('export-admin-without-session', 'admin', 'GET', export(), 403, ADMIN)
add('export-restricted-missing-permission', 'restricted_collaborator', 'GET', export(), 403, missing('association.calendar.read'))
add('export-athlete-denied', 'athlete', 'GET', export('?course_id=abc'), 403, GENERIC_DENIED)
add('export-owner-scoped-athlete-denied', 'owner', 'GET', export(), 403, SCOPED_DENIED, headers=SCOPED)
add('export-owner-without-association-server-error', 'owner_without_association', 'GET', export(), 500, SERVER)
add('export-deleted-association-server-error', 'deleted_association_owner', 'GET', export(), 500, SERVER)
add('export-wrong-method', 'owner', 'POST', export(), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('export-options', 'owner', 'OPTIONS', export(), 200,
    options('Full Events Calendar Export', 'API endpoint to get the attendance registry for the current course'))

# ---------------------------------------------------------------------------
# communications/send/post
# ---------------------------------------------------------------------------
POST = '/communications/send/post'
REQUIRED = ['Message is required']


def posted(message, actor='owner', session=None, association=OWN):
    return {'kind': 'post', 'actor': actor, 'session': session, 'message': message, 'association': association}


add('post-owner', 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 'Allenamento spostato alle 18'},
    effect=posted('Allenamento spostato alle 18'))
add('post-trimmed', 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': '  Ciao a tutti \n', 'subject': 'ignorato'},
    effect=posted('Ciao a tutti'))
add('post-number', 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 12345}, effect=posted('12345'))
# A JSON float is str() of the Python float.
for label, literal, stored in (('float', '1.50', '1.5'), ('float-exponent', '1E+3', '1000.0'), ('float-small', '1e-7', '1e-07'),
                               ('float-beyond-range', '-1e999', '-inf')):
    add('post-' + label, 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, raw_body='{"message": %s}' % literal,
        content_type='application/json', effect=posted(stored))
add('post-max-length', 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 'à' * 10000}, effect=posted('à' * 10000))
add('post-form', 'owner', 'POST', POST, 200, {'msg': 'post sent.'}, raw_body='message=Ciao+dal+form&message=secondo',
    content_type='application/x-www-form-urlencoded', effect=posted('secondo'))
add('post-full-collaborator', 'full_collaborator', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 'Dal collaboratore'},
    effect=posted('Dal collaboratore', actor='full_collaborator'))
add('post-admin-impersonating-owner', 'admin', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 'Dall’amministratore'},
    headers=ADMIN_OWNER, effect=posted('Dall’amministratore', actor='admin', session='admin_owner'))
add('post-foreign-owner', 'foreign_owner', 'POST', POST, 200, {'msg': 'post sent.'}, body={'message': 'Estero'},
    effect=posted('Estero', actor='foreign_owner', association=FOREIGN))
for label, value in (('missing', None), ('empty', ''), ('null', None), ('zero', 0), ('false', False), ('empty-list', [])):
    body = {} if label == 'missing' else {'message': value}
    add(f'post-required-{label}', 'owner', 'POST', POST, 400, REQUIRED, body=body)
add('post-empty-body', 'owner', 'POST', POST, 400, REQUIRED, raw_body='', content_type='application/json')
add('post-blank', 'owner', 'POST', POST, 400, {'message': ['This field may not be blank.']}, body={'message': '   '})
add('post-boolean', 'owner', 'POST', POST, 400, {'message': ['Not a valid string.']}, body={'message': True})
add('post-list', 'owner', 'POST', POST, 400, {'message': ['Not a valid string.']}, body={'message': ['a']})
add('post-object', 'owner', 'POST', POST, 400, {'message': ['Not a valid string.']}, body={'message': {'a': 1}})
add('post-too-long', 'owner', 'POST', POST, 400, {'message': ['Ensure this field has no more than 10000 characters.']},
    body={'message': 'x' * 10001})
add('post-null-character', 'owner', 'POST', POST, 400, {'message': ['Null characters are not allowed.']}, body={'message': 'a\x00b'})
add('post-list-body-server-error', 'owner', 'POST', POST, 500, SERVER, body=[{'message': 'x'}])
add('post-malformed-json', 'owner', 'POST', POST, 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    raw_body='nope', content_type='application/json')
add('post-athlete-invalid', 'athlete', 'POST', POST, 400, REQUIRED, body={})
add('post-athlete-not-association', 'athlete', 'POST', POST, 403, NOT_ASSOCIATION, body={'message': 'x'})
add('post-owner-without-association-server-error', 'owner_without_association', 'POST', POST, 500, SERVER, body={'message': 'x'})
add('post-deleted-association-server-error', 'deleted_association_owner', 'POST', POST, 500, SERVER, body={'message': 'x'})
add('post-anonymous', 'anonymous', 'POST', POST, 401, ANONYMOUS, body={'message': 'x'})
add('post-admin-without-session', 'admin', 'POST', POST, 403, ADMIN, body={'message': 'x'})
add('post-restricted-unmapped', 'restricted_collaborator', 'POST', POST, 403, unmapped(POST), body={'message': 'x'})
add('post-owner-scoped-athlete-denied', 'owner', 'POST', POST, 403, SCOPED_DENIED, body={'message': 'x'}, headers=SCOPED)
add('post-wrong-method', 'owner', 'GET', POST, 405, {'detail': 'Method "GET" not allowed.'})
add('post-options', 'owner', 'OPTIONS', POST, 200, options('Configuration Send Post'))

# ---------------------------------------------------------------------------
# subscription/<uid>/card
# ---------------------------------------------------------------------------
ASSOCIATES = {name: {'id': next(ids), 'association': association, 'first': first, 'last': last}
              for name, association, first, last in (('mario', OWN, 'Mario', 'Rossi'), ('giulia', OWN, 'Giulia', 'Neri'),
                                                     ('hans', FOREIGN, 'Hans', 'Müller'))}


def subscription(associate='mario', association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'associate': associate, 'number': '17', 'type': 'Ordinaria',
           'end': '2026-12-31', 'group': None, 'deleted': False}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'card': subscription(),
    'second': subscription('giulia', number=None, type=None, end=None),
    'grouped': subscription('giulia', group='own_group', number='0'),
    'flagged': subscription(deleted=True, number='9'),
    'orphan': subscription(None),
    'abroad': subscription('hans', FOREIGN, number='A-1', type='Agonistica', end='2027-06-30'),
    'expired_anon': subscription(),
    'expired_owner': subscription(),
}
FUTURE, PAST = '2099-01-01T10:00:00Z', '2020-01-01T10:00:00Z'
TOKENS = {
    'card': {'id': next(ids), 'subscription': 'card', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'second': {'id': next(ids), 'subscription': 'second', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'grouped': {'id': next(ids), 'subscription': 'grouped', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'flagged': {'id': next(ids), 'subscription': 'flagged', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'orphan': {'id': next(ids), 'subscription': 'orphan', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'abroad': {'id': next(ids), 'subscription': 'abroad', 'token': next(ids), 'created': '2026-01-01T09:00:00Z', 'expires': FUTURE},
    'expired_anon': {'id': next(ids), 'subscription': 'expired_anon', 'token': next(ids), 'created': '2019-12-29T10:00:00.250000Z',
                     'expires': PAST},
    'expired_owner': {'id': next(ids), 'subscription': 'expired_owner', 'token': next(ids), 'created': '2019-12-29T10:00:00Z',
                      'expires': PAST},
}
TEMPLATE = {'colore': '#112233', 'campi': ['nome', 'numero'], 'scala': 1.5, 'logo': None, 'z': {'b': 2, 'a': 1}}
CONFIGURATIONS = {'foreign': {'id': next(ids), 'association': FOREIGN, 'emit_only_on_approval': True, 'customized_template': TEMPLATE},
                  'foreign_later': {'id': 'ffffffff-0000-4000-8000-0000e1000999', 'association': FOREIGN,
                                    'emit_only_on_approval': False, 'customized_template': None}}
DENOMINATIONS = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}
EXPIRED = {'msg': 'il token è scaduto'}


def card_path(label, upper=False):
    value = SUBSCRIPTIONS[label]['id'] if label in SUBSCRIPTIONS else label
    return f'/subscription/{value.upper() if upper else value}/card'


def member(label, *, emit=False, template=None):
    row = SUBSCRIPTIONS[label]
    associate = ASSOCIATES[row['associate']]
    return {'member': {'subscription_number': row['number'], 'subscription_type': row['type'], 'subscription_end_date': row['end'],
                       'associate': {'first_name': associate['first'], 'last_name': associate['last'], 'avatar': None},
                       'sport_association': {'denomination': DENOMINATIONS[row['association']], 'user': {'avatar_image': None},
                                             'membership_card_configuration': {
                                                 'emit_only_on_approval': emit,
                                                 'customized_template': {} if template is None else template}}}}


def token_query(label, upper=False):
    value = TOKENS[label]['token']
    return f'?token={value.upper() if upper else value}'


CONFIG_CREATED = {'kind': 'card_configuration', 'actor': None, 'association': OWN}

# The first own-association card creates its membership card configuration.
add('card-anonymous-creates-configuration', 'anonymous', 'GET', card_path('card') + token_query('card'), 200, member('card'),
    effect=CONFIG_CREATED)
add('card-anonymous', 'anonymous', 'GET', card_path('card') + token_query('card'), 200, member('card'))
add('card-empty-values', 'anonymous', 'GET', card_path('second') + token_query('second'), 200, member('second'))
add('card-upper-case-token', 'anonymous', 'GET', card_path('card') + token_query('card', upper=True), 200, member('card'))
add('card-upper-case-uid', 'anonymous', 'GET', card_path('card', upper=True) + token_query('card'), 200, member('card'))
add('card-soft-deleted-subscription', 'anonymous', 'GET', card_path('flagged') + token_query('flagged'), 200, member('flagged'))
add('card-other-association-configuration', 'anonymous', 'GET', card_path('abroad') + token_query('abroad'), 200,
    member('abroad', emit=True, template=TEMPLATE))
add('card-group', 'anonymous', 'GET', card_path('grouped') + token_query('grouped'), 200, member('grouped'),
    headers={'X-Group-Id': IDS['own_group']})
add('card-other-group-server-error', 'anonymous', 'GET', card_path('grouped') + token_query('grouped'), 500, SERVER,
    headers={'X-Group-Id': IDS['foreign_group']})
add('card-without-associate-server-error', 'anonymous', 'GET', card_path('orphan') + token_query('orphan'), 500, SERVER)
add('card-athlete', 'athlete', 'GET', card_path('card') + token_query('card'), 200, member('card'))
add('card-owner', 'owner', 'GET', card_path('abroad') + token_query('abroad'), 200, member('abroad', emit=True, template=TEMPLATE))
add('card-missing-token', 'anonymous', 'GET', card_path('card'), 400, EXPIRED)
add('card-empty-token', 'anonymous', 'GET', card_path('card') + '?token=', 400, EXPIRED)
add('card-token-of-other-subscription', 'anonymous', 'GET', card_path('second') + token_query('card'), 400, EXPIRED)
add('card-unknown-token', 'anonymous', 'GET', card_path('card') + '?token=a1000000-0000-4000-8000-0000e1999998', 400, EXPIRED)
add('card-unknown-upper-case-token', 'anonymous', 'GET', card_path('card') + '?token=A1000000-0000-4000-8000-0000E1999998', 400, EXPIRED)
add('card-malformed-token-server-error', 'anonymous', 'GET', card_path('card') + '?token=abc', 500, SERVER)
add('card-expired-anonymous', 'anonymous', 'GET', card_path('expired_anon') + token_query('expired_anon'), 400, EXPIRED,
    effect={'kind': 'token_deleted', 'token': 'expired_anon', 'actor': None, 'index': None})
add('card-expired-owner', 'owner', 'GET', card_path('expired_owner') + token_query('expired_owner'), 400, EXPIRED,
    effect={'kind': 'token_deleted', 'token': 'expired_owner', 'actor': 'owner', 'index': [OWN, 'actor.sport_association']})
add('card-expired-again', 'anonymous', 'GET', card_path('expired_anon') + token_query('expired_anon'), 400, EXPIRED)
add('card-malformed-uid', 'anonymous', 'GET', card_path('abc') + token_query('card'), 400, BAD_UUID)
add('card-get-restricted-missing-permission', 'restricted_collaborator', 'GET', card_path('card') + token_query('card'), 403,
    missing('association.members.read'))
add('card-get-admin-without-session', 'admin', 'GET', card_path('card') + token_query('card'), 403, ADMIN)


def minted(label, actor='owner', session=None):
    return {'kind': 'token_created', 'subscription': label, 'actor': actor, 'session': session,
            'index': [SUBSCRIPTIONS[label]['association'], 'subscription.sport_association']}


add('card-post-owner', 'owner', 'POST', card_path('card'), 200, {'token': '$new'}, body={}, effect=minted('card'))
add('card-post-soft-deleted', 'owner', 'POST', card_path('flagged'), 200, {'token': '$new'}, effect=minted('flagged'))
add('card-post-full-collaborator', 'full_collaborator', 'POST', card_path('grouped'), 200, {'token': '$new'},
    headers={'X-Group-Id': IDS['own_group']}, effect=minted('grouped', 'full_collaborator'))
add('card-post-admin-impersonating-owner', 'admin', 'POST', card_path('second'), 200, {'token': '$new'}, headers=ADMIN_OWNER,
    effect=minted('second', 'admin', 'admin_owner'))
add('card-post-other-group', 'owner', 'POST', card_path('grouped'), 404, {'msg': 'Subscription not found.'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('card-post-other-association', 'owner', 'POST', card_path('abroad'), 404, {'msg': 'Subscription not found.'})
add('card-post-unknown', 'owner', 'POST', card_path('a1000000-0000-4000-8000-0000e1999997'), 404, {'msg': 'Subscription not found.'})
add('card-post-malformed', 'anonymous', 'POST', card_path('abc'), 400, BAD_UUID)
add('card-post-anonymous', 'anonymous', 'POST', card_path('card'), 403, {'msg': 'User not allowed.'})
add('card-post-athlete', 'athlete', 'POST', card_path('card'), 403, {'msg': 'User not allowed.'})
add('card-post-owner-without-association-server-error', 'owner_without_association', 'POST', card_path('card'), 500, SERVER)
add('card-post-restricted-missing-permission', 'restricted_collaborator', 'POST', card_path('card'), 403,
    missing('association.members.update'))
add('card-post-admin-without-session', 'admin', 'POST', card_path('card'), 403, ADMIN)
# The subscription is not the scoped athlete's: the scoped-request check of the path reference answers first.
add('card-post-owner-scoped-athlete-resource', 'owner', 'POST', card_path('card'), 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, headers=SCOPED)

# ---------------------------------------------------------------------------
# Unknown tokens while a pairing is active (instance.sso.public_links.source_token)
# ---------------------------------------------------------------------------
# A canonical token no local row holds is asked of the paired server: POST <authority>/pairing/v1/public-token with the
# kind, the token, the resource, the remote pairing id and the pairing secret. The answer must carry the protocol
# version, the pairing's binding, the kind, a canonical generation and an aware expires_at in the future.
PAIR = {'instance_id': 'a1000000-0000-4000-8000-0000e1880001', 'pairing_id': 'a1000000-0000-4000-8000-0000e1880002',
        'remote_pairing_id': 'a1000000-0000-4000-8000-0000e1880003', 'generation': 'a1000000-0000-4000-8000-0000e1880004'}
PAIR_ORIGIN = 'http://localhost'


def grant(kind, **values):
    return {'protocol': 'bakney-pairing-v1', 'instance_id': PAIR['instance_id'], 'pairing_id': PAIR['remote_pairing_id'],
            'association_id': OWN, 'origin': PAIR_ORIGIN, 'callback_uri': PAIR_ORIGIN + '/bakney/v1/callback', 'kind': kind,
            'generation': PAIR['generation'], 'expires_at': '$iso+600', **values}


def paired(request=None, reply=None, *, state='paired', **row):
    """The case's pairing; request/reply: the one exchange with the paired server (none expected otherwise)."""
    authority = [] if request is None else [{'path': '/pairing/v1/public-token', 'reply': reply,
                                             'body': {**request, 'pairing_id': PAIR['remote_pairing_id'], 'secret': '$secret'}}]
    return {**PAIR, 'state': state, **row, 'authority': authority}


def ok(body):
    return {'status': 200, 'json': body}


PAIR_TOKEN = 'a1000000-0000-4000-8000-0000e1880010'
CARD_ID = SUBSCRIPTIONS['card']['id']
ASK = {'kind': 'card', 'token': PAIR_TOKEN, 'resource_id': CARD_ID}


def paired_card(name, status, response, pairing, *, label='card', token=PAIR_TOKEN, actor='anonymous', headers=None):
    add(name, actor, 'GET', card_path(label) + '?token=' + token, status, response, headers=headers)
    cases[-1]['pairing'] = pairing


paired_card('card-paired-server-vouches', 200, member('card'), paired(ASK, ok(grant('card', resource_id=CARD_ID))))
paired_card('card-pending-pairing-vouches-athlete', 200, member('card'), paired(ASK, ok(grant('card', resource_id=CARD_ID)), state='pending'),
            actor='athlete')
paired_card('card-paired-server-rejects', 400, EXPIRED, paired(ASK, {'status': 401, 'json': {'error': 'unknown'}}))
paired_card('card-paired-server-refuses-token', 400, EXPIRED, paired(ASK, {'status': 410, 'json': {'error': 'gone'}}))
paired_card('card-paired-server-unavailable', 400, EXPIRED, paired(ASK, {'status': 503, 'raw': 'unavailable'}))
paired_card('card-paired-server-not-json', 400, EXPIRED, paired(ASK, {'status': 200, 'raw': '<html>'}))
paired_card('card-paired-server-other-protocol', 400, EXPIRED,
            paired(ASK, ok({**grant('card', resource_id=CARD_ID), 'protocol': 'bakney-pairing-v2'})))
paired_card('card-paired-server-other-resource', 400, EXPIRED,
            paired(ASK, ok(grant('card', resource_id=SUBSCRIPTIONS['second']['id']))))
paired_card('card-paired-server-other-kind', 400, EXPIRED, paired(ASK, ok(grant('cumulative', resource_id=CARD_ID))))
paired_card('card-paired-server-other-origin', 400, EXPIRED,
            paired(ASK, ok({**grant('card', resource_id=CARD_ID), 'origin': 'http://localhost:8080'})))
paired_card('card-paired-server-generation-not-canonical', 400, EXPIRED,
            paired(ASK, ok({**grant('card', resource_id=CARD_ID), 'generation': PAIR['generation'].upper()})))
paired_card('card-paired-server-expired-grant', 400, EXPIRED, paired(ASK, ok(grant('card', resource_id=CARD_ID, expires_at='$iso-60'))))
paired_card('card-paired-server-naive-expiry', 400, EXPIRED, paired(ASK, ok(grant('card', resource_id=CARD_ID, expires_at='$naive+600'))))
paired_card('card-paired-server-expiry-not-a-date', 400, EXPIRED, paired(ASK, ok(grant('card', resource_id=CARD_ID, expires_at='domani'))))
paired_card('card-paired-server-expiry-number', 400, EXPIRED, paired(ASK, ok(grant('card', resource_id=CARD_ID, expires_at=4102444800))))
# Nothing is asked for a subscription outside the paired association, a token or uid that is not canonical, a pairing
# that is disconnected or whose binding no longer matches the settings, or an instance that is not self-hosted.
paired_card('card-paired-other-association-not-asked', 400, EXPIRED, paired(), label='abroad')
paired_card('card-paired-upper-case-token-not-asked', 400, EXPIRED, paired(), token=PAIR_TOKEN.upper())
paired_card('card-pairing-disconnected-not-asked', 400, EXPIRED, paired(state='disconnected'))
paired_card('card-pairing-other-origin-not-asked', 400, EXPIRED, paired(origin='http://localhost:8080'))
paired_card('card-pairing-other-association-not-asked', 400, EXPIRED, paired(association=FOREIGN))
paired_card('card-pairing-not-self-hosted-not-asked', 400, EXPIRED, paired(self_hosted=False))
# A locally stored token never reaches the paired server.
paired_card('card-paired-local-token-not-asked', 200, member('card'), paired(), token=TOKENS['card']['token'])

add('card-wrong-method', 'owner', 'DELETE', card_path('card'), 405, {'detail': 'Method "DELETE" not allowed.'})
add('card-options', 'anonymous', 'OPTIONS', card_path('card'), 200, options('Subscription Card'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'calendar/events/export, communications/send/post and subscription/<uid>/card authored sequence',
    'ids': IDS, 'courses': COURSES, 'registries': REGISTRIES, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS,
    'tokens': TOKENS, 'configurations': CONFIGURATIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
