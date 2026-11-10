"""Author the instructor/<uid>/hours list, add, update, delete and calculate HTTP sequence (instructor-hours.md).

Every view first checks ``is_valid_uuid`` on the path ids (400 list).
list filters InstructorHours by ``instructor__user=request.user`` and the uid,
ordered by -date, -creation_date; ``query[date_range]`` 'A al B' (YYYY/MM/DD,
else DD/MM/YYYY, else the last 365 days to tomorrow; no separator is a 500)
and ``query[generalSearch]`` (icontains on date, hours, amount, notes) narrow
it, then KTDatatablePagination pages it. For each row of the page a paid
payment marks the hour paid (Model.save(), audited), and a document without a
payment is deleted (the hour's document_id is nulled by the collector; the
response still renders the in-memory id and token).

add looks the instructor up by id and ``user=request.user``, sets ``data['instructor'] = uid``
(a non-dict or form body is a 500) and saves InstructorHoursSerializer
(fields '__all__'; model defaults the serializer does not provide stay the
model's in-memory values, which auditlog records). update and delete look the
instructor up by id and ``user=request.user`` and the hour by instructor and
id (404 ``{'error': 'hours not found'}``) before reading the body; update is a
partial serializer save (a new primary key inserts a copy), delete removes the
hour's document first. calculate looks the
instructor up the same way and sums paid payments of the listed courses of
the caller's association in the period (membership payments, installments, or
the subscription payment) and applies an optional percentage. The
serializer's instructor relation answers 403 for another association's
instructor (resolved through its owner). auditlog records every hour and
document change; the index resolves an hour stored when its entry is written
through its instructor's owner's association, everything else through the
actor's. Run with ``python3 -I``
after the instructor-list generator.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

OUTPUT = Path(__file__).with_name('instructor_hours_http.json')
SOURCE = json.loads(Path(__file__).with_name('instructor_list_http.json').read_text())
IDS, INSTRUCTORS = SOURCE['ids'], SOURCE['instructors']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{966001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'

DOCUMENTS = {label: {'id': next(ids), 'token': next(ids), 'filename': f'{label}.pdf', 'created': '2026-09-01T08:00:00Z'}
             for label in ('doc_synced', 'doc_detailed', 'doc_orphan', 'doc_removable', 'doc_free', 'doc_twin')}


def payment(association, amount, *, paid=True, date=None, deleted=False, group=None):
    return {'id': next(ids), 'association': association, 'amount': amount, 'paid': paid, 'date': date, 'deleted': deleted, 'group': group}


PAYMENTS = {
    'paid': payment(OWN, '25.00'), 'unpaid': payment(OWN, '10.50', paid=False), 'abroad': payment(FOREIGN, '5.00'),
    'removed': payment(OWN, '5.00', deleted=True), 'grouped': payment(OWN, '7.00', group='own_group'),
}


def hour(instructor, date, hours, amount, created, **values):
    row = {'id': next(ids), 'instructor': instructor, 'date': date, 'hours': hours, 'amount': amount, 'created': created,
           'compensation_type': 'hourly', 'hourly_billing': '0.00', 'percentage_billing': '0.00', 'courses': None, 'period': None,
           'paid': False, 'payment': None, 'notes': None, 'calculation_data': [], 'document': None}
    row.update(values)
    return row


HOURS = {
    'today': hour('full', '$today', '1.25', '5.00', '2026-01-01T00:00:00Z', notes='oggi'),
    'synced': hour('full', '2026-10-05', '2.50', '25.00', '2026-09-01T08:00:00Z', payment='paid', document='doc_synced'),
    'detailed': hour('full', '2026-09-20', '1.00', '10.50', '2026-09-21T09:30:00.123456Z', compensation_type='percentage',
                     hourly_billing='10.00', percentage_billing=None, courses=[{'course_id': 'c1', 'title': 'Corso è'}],
                     period='2026/09/01 al 2026/09/30', payment='unpaid', notes='Lezione è 50%',
                     calculation_data=[{'amount': 10.5, 'course': 'Corso'}], document='doc_detailed'),
    'plain': hour('full', '2026-08-01', '3.00', '30.00', '2026-08-02T10:00:00Z', hourly_billing='10.00'),
    'orphan': hour('full', '2025-12-31', '0.50', '5.50', '2026-01-02T10:00:00Z', document='doc_orphan'),
    'removable': hour('full', '2025-06-01', '1.00', '1.00', '2025-06-02T10:00:00Z'),
    'with_doc': hour('full', '2025-05-01', '2.00', '2.00', '2025-05-02T10:00:00Z', payment='unpaid', document='doc_removable'),
    'draft_row': hour('draft', '2026-09-10', '1.00', '12.00', '2026-09-10T18:00:00Z'),
    'movable': hour('draft', '2026-09-11', '1.00', '12.00', '2026-09-11T18:00:00Z'),
    'copyable': hour('draft', '2026-09-12', '1.50', '18.00', '2026-09-12T18:00:00Z', notes='copia', courses={'k': [1, 'è']}),
    'abroad_row': hour('abroad', '2026-09-15', '4.00', '40.00', '2026-09-15T18:00:00Z'),
}
# Two hours kept their document after their payment was detached: the list deletes it for the first and then fails to load it
# for the second.
HOURS.update({
    'twin_a': hour('abroad', '2026-09-16', '1.00', '1.00', '2026-09-16T18:00:00Z', notes='comune', document='doc_twin'),
    'twin_b': hour('abroad', '2026-09-17', '1.00', '1.00', '2026-09-17T18:00:00Z', notes='comune', document='doc_twin'),
})
# Compensations without their document yet: the list prints them (print_document_compensation.apply).
PAYMENTS.update({'comp_paid': payment(OWN, '15.00'), 'comp_unpaid': payment(OWN, '8.00', paid=False)})
HOURS.update({
    'print_paid': hour('draft', '2026-09-14', '1.00', '15.00', '2026-09-14T18:00:00Z', payment='comp_paid'),
    'print_unpaid': hour('draft', '2026-09-13', '1.00', '8.00', '2026-09-13T18:00:00Z', payment='comp_unpaid'),
})

# calculate: courses, their subscriptions and payments.
ASSOCIATES = {'maria': {'id': next(ids), 'first': 'Maria', 'last': 'Bianchi'}, 'luca': {'id': next(ids), 'first': 'Luca', 'last': 'Verdi'},
              'sara': {'id': next(ids), 'first': ' Sara', 'last': 'Neri '}}
SUBSCRIPTIONS = {label: {'id': next(ids), 'associate': label} for label in ASSOCIATES}
COURSES = {
    'member': {'id': next(ids), 'title': 'Abbonamento è', 'type': 3, 'multi': False, 'deleted': False, 'group': None},
    'multi': {'id': next(ids), 'title': 'Quote', 'type': 2, 'multi': True, 'deleted': False, 'group': None},
    'single': {'id': next(ids), 'title': 'Singolo', 'type': 1, 'multi': False, 'deleted': False, 'group': None},
    'grouped': {'id': next(ids), 'title': 'Gruppo', 'type': 1, 'multi': False, 'deleted': False, 'group': 'own_group'},
    'removed': {'id': next(ids), 'title': 'Rimosso', 'type': 1, 'multi': False, 'deleted': True, 'group': None},
}
for row in COURSES.values():
    row['association'] = OWN
COURSES['abroad'] = {'id': next(ids), 'title': 'Estero', 'type': 1, 'multi': False, 'deleted': False, 'group': None, 'association': FOREIGN}
IN, LATE = '2026-09-15T08:30:00Z', '2026-10-02T10:00:00Z'
PAYMENTS.update({
    'mp1': payment(OWN, '30.00', date='2026-09-10T10:00:00.250000Z'), 'mp2': payment(OWN, '3.00', paid=False, date=IN),
    'mp3': payment(OWN, '3.00', date=LATE), 'mp4': payment(OWN, '3.00', date=IN, deleted=True),
    'mp5': payment(OWN, '7.00', date=IN, group='own_group'), 'mp6': payment(OWN, '3.00', date=IN),
    'pi1': payment(OWN, '20.00', date=IN), 'pi2': payment(OWN, '3.00', paid=False, date=IN), 'pi3': payment(OWN, '3.00', date=IN),
    'pi4': payment(OWN, '2.50', date=IN, deleted=True), 'pi6': payment(OWN, '4.00', date=IN),
    'ps1': payment(OWN, '50.50', date='2026-09-20T00:00:00Z'), 'ps3': payment(OWN, '3.00', paid=False, date=IN),
    'ps4': payment(OWN, '0.00', date=IN), 'ps5': payment(OWN, '5.00', date='2026-09-30T00:00:00Z'),
    'ps6': payment(OWN, '3.00', date='2026-09-30T00:00:01Z'), 'ps7': payment(OWN, '3.00'),
    'ps8': payment(OWN, '1.00', date=IN, deleted=True), 'pg': payment(OWN, '9.00', date=IN), 'pr': payment(OWN, '3.00', date=IN),
    'pf': payment(FOREIGN, '11.00', date=IN),
})


def course_subscription(course, subscription, *, payment=None, deleted=False, memberships=()):
    return {'id': next(ids), 'course': course, 'subscription': subscription, 'payment': payment, 'deleted': deleted,
            'memberships': list(memberships)}


COURSE_SUBSCRIPTIONS = {
    'cs_m': course_subscription('member', 'maria', memberships=['mp1', 'mp2', 'mp3', 'mp4', 'mp5']),
    'cs_m_removed': course_subscription('member', 'maria', deleted=True, memberships=['mp6']),
    'cs_q': course_subscription('multi', 'luca'),
    'cs_s1': course_subscription('single', 'sara', payment='ps1'), 'cs_s2': course_subscription('single', 'sara'),
    'cs_s3': course_subscription('single', 'sara', payment='ps3'), 'cs_s4': course_subscription('single', 'sara', payment='ps4'),
    'cs_s5': course_subscription('single', 'sara', payment='ps5'), 'cs_s6': course_subscription('single', 'sara', payment='ps6'),
    'cs_s7': course_subscription('single', 'sara', payment='ps7'), 'cs_s8': course_subscription('single', 'sara', payment='ps8'),
    'cs_g': course_subscription('grouped', 'sara', payment='pg'), 'cs_r': course_subscription('removed', 'sara', payment='pr'),
    'cs_f': course_subscription('abroad', 'sara', payment='pf'),
}
INSTALLMENTS = {
    'i1': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': 'pi1', 'deleted': False, 'group': None},
    'i2': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': 'pi2', 'deleted': False, 'group': None},
    'i3': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': 'pi3', 'deleted': True, 'group': None},
    'i4': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': 'pi4', 'deleted': False, 'group': None},
    'i5': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': None, 'deleted': False, 'group': None},
    'i6': {'id': next(ids), 'course_subscription': 'cs_q', 'payment': 'pi6', 'deleted': False, 'group': 'own_group'},
}
UNKNOWN, NEW_KEY, FULL_KEY = 'a1000000-0000-4000-8000-000000966998', next(ids), next(ids)
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=()):
    """prints: the CURRENT_HOST requests of the eager print_document_compensation tasks, in loop order."""
    case = {'name': f'instructor-hours-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect,
            'prints': [{'path': f'/document/compensation/{PAYMENTS[label]["id"]}', 'reply': {'status': 200, 'body': 'printed'}}
                       for label in prints]}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ref = lambda table, key: table[key]['id'] if key in table else key
base = lambda instructor: f'/instructor/{ref(INSTRUCTORS, instructor)}/hours'
LIST = lambda instructor='full', query='': base(instructor) + '/list' + query
ADD = lambda instructor='draft': base(instructor) + '/add'
CALC = lambda instructor='full': base(instructor) + '/calculate'
UPD = lambda row, instructor=None: f'{base(instructor or HOURS.get(row, {}).get("instructor", "full"))}/{ref(HOURS, row)}/update'
DEL = lambda row, instructor=None: f'{base(instructor or HOURS.get(row, {}).get("instructor", "full"))}/{ref(HOURS, row)}/delete'
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
NOT_HOURS, NOT_INSTRUCTOR = {'error': 'hours not found'}, {'error': 'instructor not found'}
missing = lambda key: {'detail': f'Missing permission: association.instructor.hours.{key}'}
not_allowed = lambda method: {'detail': f'Method "{method}" not allowed.'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
FORM = 'application/x-www-form-urlencoded'


def drf_datetime(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


def serialized(label, **changes):
    """InstructorHoursSerializer of a seeded hour (decimals as strings, dates ISO, DRF datetimes)."""
    row = {**HOURS[label], **changes}
    document = DOCUMENTS[row['document']] if row['document'] else None
    return {'instructor_hours_id': row['id'], 'document_token': document['token'] if document else None,
            'creation_date': drf_datetime(row['created']), 'date': row['date'], 'compensation_type': row['compensation_type'],
            'hours': row['hours'], 'hourly_billing': row['hourly_billing'], 'percentage_billing': row['percentage_billing'],
            'courses': row['courses'], 'period': row['period'], 'amount': row['amount'], 'paid': row['paid'], 'notes': row['notes'],
            'calculation_data': row['calculation_data'], 'instructor': INSTRUCTORS[row['instructor']]['id'],
            'payment': PAYMENTS[row['payment']]['id'] if row['payment'] else None, 'document': document['id'] if document else None}


def page(items, *, total=None, number=1, pages=1, perpage=10):
    return {'data': items, 'meta': {'total': len(items) if total is None else total, 'page': number, 'pages': pages, 'perpage': perpage,
                                    'rowIds': [item['instructor_hours_id'] for item in items]}}


query = lambda **values: '?' + '&'.join(f'{quote(k)}={quote(v)}' for k, v in values.items())
RANGE, SEARCH, PAGE, PERPAGE = 'query[date_range]', 'query[generalSearch]', 'pagination[page]', 'pagination[perpage]'
hours_effect = lambda actor, rows=None, documents=None, audit=(): {'actor': actor, 'rows': rows or {}, 'documents': documents or {},
                                                                    'audit': list(audit)}
updated = lambda label, changes: {'model': 'InstructorHours', 'key': HOURS[label]['id'], 'action': 1, 'changes': changes}
deleted_hour = lambda label: {'model': 'InstructorHours', 'key': HOURS[label]['id'], 'action': 2, 'changes': '$deleted'}
deleted_document = lambda label: {'model': 'Document', 'key': DOCUMENTS[label]['id'], 'action': 2, 'changes': '$deleted'}

# list (reads, except the paid flag and orphan documents of the page's rows)
add('list-iso-range', 'owner', LIST(query=query(**{RANGE: '2026/09/01 al 2026/09/30'})), 200, page([serialized('detailed')]))
add('list-italian-range', 'owner', LIST(query=query(**{RANGE: '01/08/2026 al 30/09/2026'})), 200,
    page([serialized('detailed'), serialized('plain')]))
add('list-search-notes-case-insensitive', 'owner', LIST(query=query(**{SEARCH: 'LEZIONE'})), 200, page([serialized('detailed')]))
add('list-search-literal-percent', 'owner', LIST(query=query(**{SEARCH: '0%'})), 200, page([serialized('detailed')]))
add('list-search-literal-underscore', 'owner', LIST(query=query(**{SEARCH: '_'})), 200, page([]))
add('list-search-date-deletes-orphan-document', 'owner', LIST(query=query(**{SEARCH: '2025-12'})), 200, page([serialized('orphan')]),
    effect=hours_effect('owner', rows={'orphan': {'document_id': None}}, documents={'doc_orphan': None},
                        audit=[deleted_document('doc_orphan')]))
add('list-second-page', 'owner', LIST(query=query(**{PERPAGE: '2', PAGE: '2'})), 200,
    page([serialized('detailed'), serialized('plain')], total=7, number=2, pages=4, perpage=2))
ALL = ['today', 'synced', 'detailed', 'plain', 'orphan', 'removable', 'with_doc']
after_sync = lambda label: serialized(label, paid=True) if label == 'synced' else serialized(label, document=None) if label == 'orphan' \
    else serialized(label)
add('list-owner-syncs-paid', 'owner', LIST(), 200, page([after_sync(label) for label in ALL]),
    effect=hours_effect('owner', rows={'synced': {'paid': True}}, audit=[updated('synced', {'paid': ['False', 'True']})]))
add('list-last-page', 'owner', LIST(query=query(**{PERPAGE: '3', PAGE: 'last'})), 200,
    page([serialized('with_doc')], total=7, number=3, pages=3, perpage=3))
add('list-invalid-page', 'owner', LIST(query=query(**{PAGE: '9'})), 404, {'detail': 'Invalid page.'})
add('list-zero-perpage-default', 'owner', LIST(query=query(**{PERPAGE: '0', SEARCH: '25.0'})), 200, page([after_sync('synced')]))
add('list-unparsed-range-last-year', 'owner', LIST(query=query(**{RANGE: 'ieri al domani', SEARCH: 'oggi'})), 200,
    page([serialized('today')]))
add('list-empty-range-ignored-already-paid', 'full_collaborator', LIST(query=query(**{RANGE: ''})), 200,
    page([after_sync(label) for label in ALL]))
add('list-range-without-separator-server-error', 'owner', LIST(query=query(**{RANGE: '2026/09/01'})), 500, SERVER)
add('list-range-two-separators-server-error', 'owner', LIST(query=query(**{RANGE: 'a al b al c'})), 500, SERVER)
add('list-athlete-empty', 'athlete', LIST(), 200, page([]))
add('list-foreign-instructor-empty', 'owner', LIST('abroad'), 200, page([]))
add('list-foreign-owner', 'foreign_owner', LIST('abroad', query(**{SEARCH: '40.00'})), 200, page([serialized('abroad_row')]))
add('list-rows-sharing-a-document-server-error', 'foreign_owner', LIST('abroad', query(**{SEARCH: 'comune'})), 500, SERVER,
    effect=hours_effect('foreign_owner', rows={'twin_a': {'document_id': None}, 'twin_b': {'document_id': None}},
                        documents={'doc_twin': None}, audit=[deleted_document('doc_twin')]))
# Each row with a payment and no document prints it after its own paid sync; the response keeps the in-memory document (None).
DRAFT = ['print_paid', 'print_unpaid', 'copyable', 'movable', 'draft_row']
add('list-owner-prints-compensations', 'owner', LIST('draft'), 200,
    page([serialized(label, paid=True) if label == 'print_paid' else serialized(label) for label in DRAFT]),
    effect=hours_effect('owner', rows={'print_paid': {'paid': True}}, audit=[updated('print_paid', {'paid': ['False', 'True']})]),
    prints=['comp_paid', 'comp_unpaid'])
add('list-admin-impersonating-owner', 'admin', LIST('draft'), 200,
    page([serialized(label, paid=True) if label == 'print_paid' else serialized(label) for label in DRAFT]),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}, prints=['comp_paid', 'comp_unpaid'])
add('list-prints-only-page-rows', 'owner', LIST('draft', query(**{PERPAGE: '1', PAGE: '2'})), 200,
    page([serialized('print_unpaid')], total=5, number=2, pages=5, perpage=1), prints=['comp_unpaid'])
add('list-page-without-compensation-no-print', 'owner', LIST('draft', query(**{PERPAGE: '3', PAGE: '2'})), 200,
    page([serialized('movable'), serialized('draft_row')], total=5, number=2, pages=2, perpage=3))
add('list-malformed', 'owner', LIST('abc'), 400, BAD_UUID)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST(), 403, missing('read'))
add('list-owner-scoped-athlete-denied', 'owner', LIST(), 403, SCOPED_DENIED, headers=SCOPED)
add('list-admin-without-session', 'admin', LIST(), 403, ADMIN)
add('list-anonymous', 'anonymous', LIST(), 401, ANONYMOUS)
add('list-wrong-method', 'owner', LIST(), 405, not_allowed('POST'), method='POST', body={})
add('list-options', 'owner', LIST(), 200, options('Instructor Hours List'), method='OPTIONS')

# add
ADDED = {'msg': 'hours added.'}
DEFAULTS = {'compensation_type': 'hourly', 'hourly_billing': '0.00', 'percentage_billing': '0.00', 'courses': None, 'period': None,
            'amount': '0.00', 'paid': False, 'payment_id': None, 'notes': None, 'calculation_data': [], 'document_id': None}


def created(instructor, actor, *, key='$new', columns=None, defaults=('hourly_billing', 'percentage_billing', 'amount'), date=True):
    """A new hour: its stored columns, and auditlog's create diff from the stored row except the model
    defaults the serializer did not provide (float 0.0 in memory) and an absent date (timezone.now())."""
    row = {**DEFAULTS, 'instructor_id': INSTRUCTORS[instructor]['id'], **(columns or {})}
    overrides = {name: '0.0' for name in defaults}
    if date:
        overrides['date'] = '$now'
    return hours_effect(actor, rows={key: row}, audit=[{'model': 'InstructorHours', 'key': key, 'action': 0, 'changes': '$created',
                                                         'overrides': overrides}])


add('add-minimal', 'owner', ADD(), 200, ADDED, method='POST', body={'hours': '1.5'},
    effect=created('draft', 'owner', columns={'hours': '1.50', 'date': '$today'}))
FULL_BODY = {'instructor_hours_id': FULL_KEY, 'instructor': INSTRUCTORS['abroad']['id'], 'document_token': 'ignored',
             'creation_date': '2026-09-01T10:00:00+02:00', 'date': '15/09/2026', 'compensation_type': 'percentage', 'hours': 2,
             'hourly_billing': '12.5', 'percentage_billing': 7.5, 'courses': [{'course_id': 'c1'}, None], 'period': 'Settembre',
             'amount': 30, 'paid': 'true', 'payment': PAYMENTS['unpaid']['id'], 'notes': '  Nota è ',
             'calculation_data': {'b': 1.5, 'a': ['x']}, 'document': DOCUMENTS['doc_free']['id']}
add('add-full', 'owner', ADD(), 200, ADDED, method='POST', body=FULL_BODY,
    effect=created('draft', 'owner', key=FULL_KEY, defaults=(), date=False, columns={
        'creation_date': '2026-09-01T08:00:00Z', 'date': '2026-09-15', 'compensation_type': 'percentage', 'hours': '2.00',
        'hourly_billing': '12.50', 'percentage_billing': '7.50', 'courses': [{'course_id': 'c1'}, None], 'period': 'Settembre',
        'amount': '30.00', 'paid': True, 'payment_id': PAYMENTS['unpaid']['id'], 'notes': 'Nota è',
        'calculation_data': {'b': 1.5, 'a': ['x']}, 'document_id': DOCUMENTS['doc_free']['id']}))
add('add-null-percentage-and-amount-given', 'full_collaborator', ADD(), 200, ADDED, method='POST',
    body={'hours': ' 0.5 ', 'percentage_billing': None, 'amount': '1e1', 'period': '', 'notes': None, 'courses': None},
    effect=created('draft', 'full_collaborator', defaults=('hourly_billing',),
                   columns={'hours': '0.50', 'date': '$today', 'percentage_billing': None, 'amount': '10.00', 'period': ''}))
# Only the caller's own instructors: another owner's instructor, or any for an athlete, is not found.
add('add-other-association-instructor', 'owner', ADD('abroad'), 404, NOT_INSTRUCTOR, method='POST', body={'hours': 1, 'date': '01/09/2026'})
add('add-other-owner-instructor', 'foreign_owner', ADD('abroad'), 200, ADDED, method='POST', body={'hours': 1, 'date': '01/09/2026'},
    effect=created('abroad', 'foreign_owner', date=False, columns={'hours': '1.00', 'date': '2026-09-01'}))
add('add-athlete-not-found', 'athlete', ADD(), 404, NOT_INSTRUCTOR, method='POST', body={'hours': '1'})
add('add-grouped-payment-with-group-header', 'owner', ADD(), 200, ADDED, method='POST',
    body={'hours': '1', 'payment': PAYMENTS['grouped']['id']}, headers={'X-Group-Id': IDS['own_group']},
    effect=created('draft', 'owner', columns={'hours': '1.00', 'date': '$today', 'payment_id': PAYMENTS['grouped']['id']}))
add('add-validation-errors', 'owner', ADD(), 400, {
    'instructor_hours_id': ['Must be a valid UUID.'], 'creation_date': [
        'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'],
    'date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'compensation_type': ['Ensure this field has no more than 15 characters.'],
    'hours': ['Ensure that there are no more than 2 decimal places.'], 'hourly_billing': ['A valid number is required.'],
    'percentage_billing': ['Ensure that there are no more than 2 digits before the decimal point.'],
    'amount': ['This field may not be null.'], 'paid': ['Must be a valid boolean.'],
    'payment': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'document': ['“abc” is not a valid UUID.'],
    'period': ['Ensure this field has no more than 50 characters.'], 'notes': ['Not a valid string.']},
    method='POST', body={'instructor_hours_id': 'nope', 'creation_date': 'ieri', 'date': '2026-09-01', 'compensation_type': 'x' * 16,
                         'hours': '1.234', 'hourly_billing': 'dieci', 'percentage_billing': '123', 'amount': None, 'paid': 'forse',
                         'payment': UNKNOWN, 'document': 'abc', 'period': 'p' * 51, 'notes': True})
add('add-hours-required', 'owner', ADD(), 400, {'hours': ['This field is required.']}, method='POST')
add('add-hours-total-digits', 'owner', ADD(), 400, {'hours': ['Ensure that there are no more than 4 digits in total.'],
                                                  'compensation_type': ['This field may not be blank.']},
    method='POST', body={'hours': '12345', 'compensation_type': ' '})
add('add-blank-hours', 'owner', ADD(), 400, {'hours': ['A valid number is required.'], 'percentage_billing': ['A valid number is required.']},
    method='POST', body={'hours': '', 'percentage_billing': 'x'})
add('add-existing-key', 'owner', ADD(), 400, {'instructor_hours_id': ['instructor hours with this instructor hours id already exists.']},
    method='POST', body={'hours': '1', 'instructor_hours_id': HOURS['plain']['id']})
add('add-removed-payment', 'owner', ADD(), 400, {'payment': [f'Invalid pk "{PAYMENTS["removed"]["id"]}" - object does not exist.']},
    method='POST', body={'hours': '1', 'payment': PAYMENTS['removed']['id']})
add('add-group-header-hides-payment', 'owner', ADD(), 400, {'payment': [f'Invalid pk "{PAYMENTS["grouped"]["id"]}" - object does not exist.']},
    method='POST', body={'hours': '1', 'payment': PAYMENTS['grouped']['id']}, headers={'X-Group-Id': IDS['foreign_group']})
add('add-unknown-document', 'owner', ADD(), 400, {'document': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
                                                 'payment': ['Incorrect type. Expected pk value, received bool.']},
    method='POST', body={'hours': '1', 'document': UNKNOWN, 'payment': True})
add('add-foreign-payment-denied', 'owner', ADD(), 403, FORBIDDEN, method='POST',
    body={'hours': 'x', 'payment': PAYMENTS['abroad']['id']})
add('add-unknown-instructor', 'owner', ADD(UNKNOWN), 404, NOT_INSTRUCTOR, method='POST', body={'hours': '1'})
add('add-malformed', 'owner', ADD('abc'), 400, BAD_UUID, method='POST', raw_body='nope', content_type='application/json')
add('add-malformed-json', 'owner', ADD(UNKNOWN), 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    method='POST', raw_body='nope', content_type='application/json')
add('add-form-body-server-error', 'owner', ADD(), 500, SERVER, method='POST', raw_body='hours=1', content_type=FORM)
add('add-list-body-server-error', 'owner', ADD(), 500, SERVER, method='POST', body=[])
add('add-null-body-server-error', 'owner', ADD(), 500, SERVER, method='POST', raw_body='null', content_type='application/json')
add('add-list-body-unknown-instructor', 'owner', ADD(UNKNOWN), 404, NOT_INSTRUCTOR, method='POST', body=[])
add('add-restricted-missing-permission', 'restricted_collaborator', ADD(), 403, missing('create'), method='POST', body={'hours': '1'})
add('add-owner-scoped-athlete-denied', 'owner', ADD(), 403, SCOPED_DENIED, method='POST', body={'hours': '1'}, headers=SCOPED)
add('add-admin-without-session', 'admin', ADD(), 403, ADMIN, method='POST', body={'hours': '1'})
add('add-anonymous', 'anonymous', ADD(), 401, ANONYMOUS, method='POST', body={'hours': '1'})
add('add-wrong-method', 'owner', ADD(), 405, not_allowed('GET'))
add('add-options', 'owner', ADD(), 200, options('Instructor Hours Add'), method='OPTIONS')

# update
SUCCESS = {'success': True}
add('update-fields', 'owner', UPD('draft_row'), 200, SUCCESS, method='PATCH', body={
    'hours': '2', 'notes': 'Aggiornato', 'date': '01/02/2026', 'paid': 'true', 'percentage_billing': None, 'courses': {'a': [1, 'è']},
    'payment': PAYMENTS['unpaid']['id'], 'creation_date': '2026-09-10T18:00:00Z', 'document_token': 'ignored'},
    effect=hours_effect('owner', rows={'draft_row': {'hours': '2.00', 'notes': 'Aggiornato', 'date': '2026-02-01', 'paid': True,
                                                     'percentage_billing': None, 'courses': {'a': [1, 'è']},
                                                     'payment_id': PAYMENTS['unpaid']['id']}},
                        audit=[updated('draft_row', {'hours': ['1.00', '2.00'], 'notes': ['None', 'Aggiornato'],
                                                     'date': ['2026-09-10', '2026-02-01'], 'paid': ['False', 'True'],
                                                     'percentage_billing': ['0.00', 'None'],
                                                     'courses': ['null', '{"a": [1, "\\u00e8"]}'],
                                                     'payment': ['None', PAYMENTS['unpaid']['id']]})]))
add('update-same-values-no-audit', 'owner', UPD('draft_row'), 200, SUCCESS, method='PATCH',
    body={'hours': '2.0', 'notes': 'Aggiornato', 'courses': {'a': [1, 'è']}, 'paid': 1}, effect=hours_effect('owner'))
add('update-form', 'owner', UPD('draft_row'), 200, SUCCESS, method='PATCH',
    raw_body='notes=Modulo&percentage_billing=&period=&calculation_data=%5B1%2C+2.5%5D&payment=', content_type=FORM,
    effect=hours_effect('owner', rows={'draft_row': {'notes': 'Modulo', 'period': '', 'calculation_data': [1, 2.5], 'payment_id': None}},
                        audit=[updated('draft_row', {'notes': ['Aggiornato', 'Modulo'], 'period': ['None', ''],
                                                     'calculation_data': ['[]', '[1, 2.5]'],
                                                     'payment': [PAYMENTS['unpaid']['id'], 'None']})]))
add('update-form-invalid-json', 'owner', UPD('draft_row'), 400, {'courses': ['Value must be valid JSON.'], 'instructor': ['This field may not be null.']},
    method='PATCH', raw_body='courses=nope&instructor=', content_type=FORM)
add('update-validation-errors', 'owner', UPD('draft_row'), 400, {
    'hours': ['This field may not be null.'], 'date': ['This field may not be null.'], 'paid': ['This field may not be null.'],
    'instructor': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'compensation_type': ['Not a valid string.'],
    'amount': ['Ensure that there are no more than 9 digits in total.']},
    method='PATCH', body={'hours': None, 'date': None, 'paid': None, 'instructor': UNKNOWN, 'compensation_type': ['x'],
                          'amount': '1234567890'})
add('update-foreign-payment-denied', 'owner', UPD('draft_row'), 403, FORBIDDEN, method='PATCH',
    body={'hours': None, 'payment': PAYMENTS['abroad']['id']})
# The instructor relation resolves through the instructor's owner: another association's one is denied.
add('update-other-association-instructor-denied', 'owner', UPD('draft_row'), 403, FORBIDDEN, method='PATCH',
    body={'hours': None, 'instructor': INSTRUCTORS['abroad']['id']})
add('update-list-body', 'owner', UPD('draft_row'), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PATCH', body=['hours'])
add('update-null-body', 'owner', UPD('draft_row'), 400, {'non_field_errors': ['No data provided']}, method='PATCH', raw_body='null',
    content_type='application/json')
add('update-empty-body-no-audit', 'full_collaborator', UPD('draft_row'), 200, SUCCESS, method='PATCH', effect=hours_effect('full_collaborator'))
add('update-existing-key', 'owner', UPD('draft_row'), 400,
    {'instructor_hours_id': ['instructor hours with this instructor hours id already exists.']}, method='PATCH',
    body={'instructor_hours_id': HOURS['plain']['id']})
add('update-own-key-unchanged', 'owner', UPD('draft_row'), 200, SUCCESS, method='PATCH',
    body={'instructor_hours_id': HOURS['draft_row']['id'].upper()}, effect=hours_effect('owner'))
add('update-move-to-other-instructor', 'full_collaborator', UPD('movable'), 200, SUCCESS, method='PATCH',
    body={'instructor': INSTRUCTORS['full']['id'], 'amount': 0},
    effect=hours_effect('full_collaborator', rows={'movable': {'instructor_id': INSTRUCTORS['full']['id'], 'amount': '0.00'}},
                        audit=[{**updated('movable', {'instructor': [INSTRUCTORS['draft']['id'], INSTRUCTORS['full']['id']],
                                                      'amount': ['12.00', '0.00']})}]))
add('update-new-key-copies-row', 'owner', UPD('copyable'), 200, SUCCESS, method='PATCH',
    body={'instructor_hours_id': NEW_KEY, 'hours': '9.5'},
    effect=hours_effect('owner', rows={NEW_KEY: {'$copy': 'copyable', 'hours': '9.50'}},
                        # auditlog's pre_save finds no row under the new key and logs the full diff as an update, then post_save logs the create.
                        audit=[{'model': 'InstructorHours', 'key': NEW_KEY, 'action': action, 'changes': '$created', 'overrides': {}}
                               for action in (1, 0)]))
add('update-unknown', 'owner', UPD(UNKNOWN, 'draft'), 404, NOT_HOURS, method='PATCH', raw_body='nope', content_type='application/json')
add('update-other-instructor', 'owner', UPD('draft_row', 'full'), 404, NOT_HOURS, method='PATCH', body={'hours': '1'})
add('update-foreign-instructor', 'owner', UPD('abroad_row'), 404, NOT_HOURS, method='PATCH', body={'hours': '1'})
add('update-unknown-instructor', 'owner', UPD('draft_row', UNKNOWN), 404, NOT_HOURS, method='PATCH', body={'hours': '1'})
add('update-malformed-instructor', 'owner', UPD('draft_row', 'abc'), 400, BAD_UUID, method='PATCH', body={'hours': '1'})
add('update-malformed-hours', 'owner', UPD('abc', 'draft'), 400, BAD_UUID, method='PATCH', body={'hours': '1'})
add('update-malformed-json', 'owner', UPD('draft_row'), 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'},
    method='PATCH', raw_body='nope', content_type='application/json')
add('update-athlete-not-found', 'athlete', UPD('draft_row'), 404, NOT_HOURS, method='PATCH', body={'hours': '1'})
add('update-restricted-missing-permission', 'restricted_collaborator', UPD('draft_row'), 403, missing('update'), method='PATCH', body={})
add('update-anonymous', 'anonymous', UPD('draft_row'), 401, ANONYMOUS, method='PATCH', body={})
add('update-wrong-method', 'owner', UPD('draft_row'), 405, not_allowed('POST'), method='POST', body={})
add('update-options', 'owner', UPD('draft_row'), 200, options('Instructor Hours Update'), method='OPTIONS')

# delete
DELETED = {'msg': 'hours deleted.'}
add('delete-unknown', 'owner', DEL(UNKNOWN), 404, NOT_HOURS, method='DELETE')
add('delete-other-instructor', 'owner', DEL('removable', 'draft'), 404, NOT_HOURS, method='DELETE')
add('delete-foreign-instructor', 'owner', DEL('abroad_row'), 404, NOT_HOURS, method='DELETE')
add('delete-malformed', 'owner', DEL('abc'), 400, BAD_UUID, method='DELETE')
add('delete-athlete-not-found', 'athlete', DEL('removable'), 404, NOT_HOURS, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', DEL('removable'), 403, missing('delete'), method='DELETE')
add('delete-wrong-method', 'owner', DEL('removable'), 405, not_allowed('GET'))
add('delete-options', 'owner', DEL('removable'), 200, options('Instructor Hours Delete'), method='OPTIONS')
add('delete', 'full_collaborator', DEL('removable'), 200, DELETED, method='DELETE',
    effect=hours_effect('full_collaborator', rows={'removable': None}, audit=[deleted_hour('removable')]))
add('delete-with-document', 'owner', DEL('with_doc'), 200, DELETED, method='DELETE',
    effect=hours_effect('owner', rows={'with_doc': None}, documents={'doc_removable': None},
                        audit=[deleted_document('doc_removable'), deleted_hour('with_doc')]))
add('delete-again-not-found', 'owner', DEL('with_doc'), 404, NOT_HOURS, method='DELETE')


# calculate (reads)
def entry(cs, payment, installment=None):
    row = COURSE_SUBSCRIPTIONS[cs]
    associate = ASSOCIATES[SUBSCRIPTIONS[row['subscription']]['associate']]
    item = {'course': COURSES[row['course']]['title'], 'course_subscription': row['id'],
            'full_athlete_name': f"{associate['first']} {associate['last']}".strip()}
    if installment:
        item['course_subscription_installment'] = INSTALLMENTS[installment]['id']
    item['amount'] = float(Decimal(PAYMENTS[payment]['amount']))
    item['payment_date'] = drf_datetime(PAYMENTS[payment]['date'])
    return item


ENTRIES = [entry('cs_m', 'mp1'), entry('cs_m', 'mp5'), entry('cs_q', 'pi1', 'i1'), entry('cs_q', 'pi4', 'i4'), entry('cs_q', 'pi6', 'i6'),
           entry('cs_s1', 'ps1'), entry('cs_s5', 'ps5'), entry('cs_s8', 'ps8')]
TOTAL = sum(Decimal(PAYMENTS[p]['amount']) for p in ('mp1', 'mp5', 'pi1', 'pi4', 'pi6', 'ps1', 'ps5', 'ps8'))
ALL_COURSES = [{'course_id': COURSES[label]['id']} for label in ('member', 'multi', 'single', 'grouped', 'removed')]
ABROAD_COURSE = {'course_id': COURSES['abroad']['id']}
SEPTEMBER = '2026/09/01 al 2026/09/30'
calculated = lambda amount, entries: {'data': {'amount': amount, 'calculation_data': {'$unordered': entries}}}
add('calculate', 'owner', CALC(), 200, calculated(float(TOTAL + Decimal('9.00')), ENTRIES + [entry('cs_g', 'pg')]), method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES + [{'course_id': UNKNOWN}, {'course_id': None, 'title': 'x'}, ABROAD_COURSE]})
# Only courses of the caller's association count (Estero is another association's).
add('calculate-other-association-course-ignored', 'owner', CALC(), 200, calculated(0, []), method='POST',
    body={'period': SEPTEMBER, 'courses': [ABROAD_COURSE]})
add('calculate-other-owner', 'foreign_owner', CALC('abroad'), 200, calculated(11.0, [entry('cs_f', 'pf')]), method='POST',
    body={'period': SEPTEMBER, 'courses': [ABROAD_COURSE] + ALL_COURSES})
add('calculate-other-association-instructor', 'owner', CALC('abroad'), 404, NOT_INSTRUCTOR, method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES})
add('calculate-percentage', 'full_collaborator', CALC('draft'), 200, calculated(float(TOTAL) * (12.5 / 100), ENTRIES), method='POST',
    body={'period': '01/09/2026 al 30/09/2026', 'courses': ALL_COURSES[:3], 'percentage': ' 12.5 '})
add('calculate-integer-percentage', 'owner', CALC(), 200, calculated(float(TOTAL) * (33 / 100), ENTRIES), method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES[:3], 'percentage': 33})
add('calculate-boolean-percentage', 'owner', CALC(), 200, calculated(float(TOTAL) * (1.0 / 100), ENTRIES), method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES[:3], 'percentage': True})
add('calculate-zero-percentage', 'owner', CALC(), 200, calculated(0.0, ENTRIES), method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES[:3], 'percentage': '0'})
add('calculate-group-header', 'owner', CALC(), 200, calculated(9.0, [entry('cs_g', 'pg')]), method='POST',
    body={'period': SEPTEMBER, 'courses': ALL_COURSES}, headers={'X-Group-Id': IDS['own_group']})
add('calculate-no-payments', 'owner', CALC(), 200, calculated(0, []), method='POST',
    body={'period': '2020/01/01 al 2020/12/31', 'courses': ALL_COURSES})
add('calculate-no-payments-percentage', 'owner', CALC(), 200, calculated(0.0, []), method='POST',
    body={'period': '2020/01/01 al 2020/12/31', 'courses': ALL_COURSES, 'percentage': 50})
add('calculate-courses-required', 'owner', CALC(), 400, {'error': 'courses is required'}, method='POST')
add('calculate-empty-courses', 'owner', CALC(UNKNOWN), 400, {'error': 'courses is required'}, method='POST',
    body={'courses': [], 'period': SEPTEMBER})
add('calculate-unknown-instructor', 'owner', CALC(UNKNOWN), 404, NOT_INSTRUCTOR, method='POST', body={'courses': 'x'})
add('calculate-string-courses', 'owner', CALC(), 400, {'error': 'Invalid courses format'}, method='POST', body={'courses': 'abc'})
add('calculate-courses-without-id', 'owner', CALC(), 400, {'error': 'Invalid courses format'}, method='POST',
    body={'courses': [{'course_id': UNKNOWN}, {'id': 1}]})
add('calculate-number-courses', 'owner', CALC(), 400, {'error': 'Invalid courses format'}, method='POST', body={'courses': 5})
add('calculate-form-courses', 'owner', CALC(), 400, {'error': 'Invalid courses format'}, method='POST',
    raw_body='courses=abc&period=' + quote(SEPTEMBER), content_type=FORM)
add('calculate-malformed-course-server-error', 'owner', CALC(), 500, SERVER, method='POST', body={'courses': [{'course_id': 'abc'}]})
add('calculate-period-required', 'owner', CALC(), 400, {'msg': 'Periodo di pagamento non valido'}, method='POST',
    body={'courses': ALL_COURSES, 'percentage': 'x'})
add('calculate-empty-period', 'owner', CALC(), 400, {'msg': 'Periodo di pagamento non valido'}, method='POST',
    body={'courses': ALL_COURSES, 'period': ''})
add('calculate-period-without-separator', 'owner', CALC(), 400, {'error': 'Invalid period format'}, method='POST',
    body={'courses': ALL_COURSES, 'period': '2026/09/01'})
add('calculate-period-two-separators', 'owner', CALC(), 400, {'error': 'Invalid period format'}, method='POST',
    body={'courses': ALL_COURSES, 'period': 'a al b al c'})
add('calculate-period-dates', 'owner', CALC(), 400, {'error': 'Invalid date format'}, method='POST',
    body={'courses': ALL_COURSES, 'period': '01/09/2026 al 2026/09/30'})
# The first format parsed the start into a datetime, which the second strptime() rejects with a TypeError.
add('calculate-period-mixed-formats-server-error', 'owner', CALC(), 500, SERVER, method='POST',
    body={'courses': ALL_COURSES, 'period': '2026/09/01 al 30/09/2026'})
add('calculate-number-period-server-error', 'owner', CALC(), 500, SERVER, method='POST', body={'courses': ALL_COURSES, 'period': 5})
add('calculate-invalid-percentage', 'owner', CALC(), 400, {'error': 'Invalid percentage'}, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': 'dieci'})
add('calculate-list-percentage', 'owner', CALC(), 400, {'error': 'Invalid percentage'}, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': [10]})
add('calculate-percentage-above-range', 'owner', CALC(), 400, {'error': 'Percentage must be 0-100'}, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': '100.5'})
add('calculate-negative-percentage', 'owner', CALC(), 400, {'error': 'Percentage must be 0-100'}, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': -1})
add('calculate-infinite-percentage', 'owner', CALC(), 400, {'error': 'Percentage must be 0-100'}, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': 'inf'})
add('calculate-nan-percentage-server-error', 'owner', CALC(), 500, SERVER, method='POST',
    body={'courses': ALL_COURSES, 'period': SEPTEMBER, 'percentage': 'nan'})
add('calculate-list-body-server-error', 'owner', CALC(), 500, SERVER, method='POST', body=[1])
add('calculate-malformed', 'owner', CALC('abc'), 400, BAD_UUID, method='POST', body={})
add('calculate-restricted-missing-permission', 'restricted_collaborator', CALC(), 403, missing('read'), method='POST', body={})
add('calculate-owner-scoped-athlete-denied', 'owner', CALC(), 403, SCOPED_DENIED, method='POST', body={}, headers=SCOPED)
add('calculate-admin-without-session', 'admin', CALC(), 403, ADMIN, method='POST', body={})
add('calculate-anonymous', 'anonymous', CALC(), 401, ANONYMOUS, method='POST', body={})
add('calculate-wrong-method', 'owner', CALC(), 405, not_allowed('GET'))
add('calculate-options', 'owner', CALC(), 200, options('Instructor Hours Calculate'), method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'matrix_version': 1,
                              'scope': 'Instructor hours authored sequence with hour, document and audit effects',
                              'hour_documents': DOCUMENTS, 'payments': PAYMENTS, 'hours': HOURS, 'associates': ASSOCIATES,
                              'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS,
                              'installments': INSTALLMENTS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
