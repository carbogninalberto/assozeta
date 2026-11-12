"""Author the printing/generate HTTP sequence (printing-generate.md).

printing_generate (@api_view(['POST']), printing_views.py) logs
request.data.get('type') first (a body without .get, such as a JSON list, is
a 500), answers athletes an empty 403, then requires the 'type' and 'format'
keys (400 {'msg': 'Mancano alcuni parametri obbligatori:<missing>'}). Inside
its try block it reads request.user.sport_association
(SportAssociation.objects.get(user=...): no association is an exception) and
dispatches eleven report builders by type (anything else: 400 {'msg': 'Tipo
stampa non valido'}). Every builder exception is re-raised and answered 400
{'msg': 'Errore nella generazione della stampa.'}.

The builders (application/utils/printing.py) read data['filters']:
filter_subscriptions takes the association's group-aware Subscription.objects
rows with archived=False, optional only-member types
(2, 3) and medical certificate, 'year' ('pre-iscrizioni', 'corrente',
'precedenti' against timezone.now()) and 'status' (status_flag__in), in
database order. Python semantics apply to the request values: `in` on a str
is a substring test, on a list an element test; status values go through
IntegerField.get_prep_value (int()) and None is dropped. Each row's phone
columns read Associate.main_tutor for minors (age < 18), which promotes and
saves the associate's first tutor relation when none is primary (an audited
update, kept when a later step raises). Dates are formatted %d/%m/%Y,
ages are int(days / 365.25) against date.today() (UTC). The 'excel' format is
get_excel_base64(*columns, columns=...) (compared with TESTS/contracts/xlsx.py)
and the response is {'file': <base64>, 'filename': '[<today>] <title> -
<denomination>.xlsx'}. The 'pdf' format is generate_pdf_from_data(rows,
columns, title=...) with the same columns turned into rows (zip(*rows)): a
reportlab table document compared with TESTS/contracts/pdf.py, under the same
filename ending in .pdf and a title of its own per report. Any other format
raises after the rows are built.

The querysets have no ordering: the adapter analyzes the seeded tables so
every transport plans them alike, and the rows come in insertion order.
Dates are authored relative to the day of the run: the adapter seeds
today + offset and the contract replaces '$today' and '$date:<offset>' with
%d/%m/%Y dates. Run with ``python3 -I``.
"""
import json
import re
from pathlib import Path

OUTPUT = Path(__file__).with_name('printing_generate_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{2710001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
PATH = '/printing/generate'
DENOMINATION = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign', IDS['deleted_association']: 'Message reads deleted'}
ERROR = {'msg': 'Errore nella generazione della stampa.'}
INVALID_TYPE = {'msg': 'Tipo stampa non valido'}


def associate(first, last, born, association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'first_name': first, 'last_name': last, 'born': born, 'email': None,
           'phone': None}
    row.update(fields)
    return row


# born: day offset from the run's date (None: no birth date).
ASSOCIATES = {
    'anna': associate('Anna', 'Rossi', -11000, email='anna@example.test', phone='3331234567'),
    'marco': associate('Marco', 'Bianchi', -3000),
    'lia': associate('Lia', 'Verdi', -2000, email='lia@example.test', phone='0451234'),
    'tobia': associate('Tobia', 'Neri', -9000, email='', phone=''),
    'paolo': associate('Paolo', 'Bianchi', -14000, phone='3470001111'),
    'sara': associate('Sara', 'Verdi', -13000, phone=''),
    'nina': associate('Nina', None, None),
    'gina': associate('Gina', 'Gialli', -2192),
    'ugo': associate('Ugo', 'Ro\x07ssi', -10000),
    'hans': associate('Hans', 'Muller', -4000, FOREIGN),
    'greta': associate('Greta', 'Muller', -12000, FOREIGN, phone='0049123'),
}
# (child, tutor, is_primary) in primary key order.
RELATIONS = [['marco', 'paolo', False], ['marco', 'sara', False], ['lia', 'sara', True], ['gina', 'paolo', False],
             ['hans', 'greta', False]]
CERTIFICATES = {name: {'id': next(ids), 'expiration': offset} for name, offset in
                (('in10', 10), ('past5', -5), ('in3', 3), ('in25', 25), ('today', 0), ('past100', -100))}


def subscription(person, kind, status, medical, start, end, association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'associate': person, 'type': kind, 'status': status, 'medical': medical,
           'start': start, 'end': end, 'group': None, 'archived': False, 'deleted': False}
    row.update(fields)
    return row


# Insertion order is the database order of the unordered queries.
SUBSCRIPTIONS = {
    'anna': subscription('anna', 2, 4, 'in10', -100, 20),
    'marco': subscription('marco', 3, 4, 'past5', -50, 200),
    'lia': subscription('lia', 2, 1, None, -10, 355),
    'tobia': subscription('tobia', 2, 2, None, 30, 395),
    'anna_archived': subscription('anna', 2, 4, 'in3', -30, 10, archived=True),
    'tobia_deleted': subscription('tobia', 2, 3, 'in25', -500, -135, deleted=True),
    'nina': subscription('nina', 2, 5, None, -20, 345),
    'gina': subscription('gina', 2, 4, 'today', -5, 0, group='own_group'),
    'paolo': subscription('paolo', 1, 4, 'past100', -365, -1),
    'ugo': subscription('ugo', 1, 3, None, -10, 100),
    'hans': subscription('hans', 2, 4, None, -10, 100, FOREIGN),
    'nobody': subscription(None, 2, 4, None, -10, 100, FOREIGN),
}
MEMBERSHIPS = [
    {'id': next(ids), 'associate': 'anna', 'end': 15, 'type': 'FIDAL', 'association': OWN},
    {'id': next(ids), 'associate': 'marco', 'end': -3, 'type': None, 'association': OWN},
    {'id': next(ids), 'associate': 'tobia', 'end': 100, 'type': 'CSI', 'association': OWN},
    {'id': next(ids), 'associate': 'lia', 'end': None, 'type': 'CSI', 'association': OWN},
    {'id': next(ids), 'associate': 'anna', 'end': -400, 'type': 'UISP', 'association': OWN},
    {'id': next(ids), 'associate': 'hans', 'end': 1, 'type': 'DTB', 'association': FOREIGN},
    {'id': next(ids), 'associate': 'nina', 'end': 2, 'type': 'CSEN', 'association': OWN},
    {'id': next(ids), 'associate': None, 'end': 2, 'type': 'Libero', 'association': OWN},
]
MEMBERSHIP_ORDER = [4, 0, 1, 2, 3, 5, 6, 7]
COURSES = {'nuoto': {'id': next(ids), 'title': 'Nuoto'}, 'yoga': {'id': next(ids), 'title': 'Yoga'}}
CARNETS = {'dieci': {'id': next(ids), 'title': 'Carnet 10 ingressi'}}
CATEGORIES = {'extra': {'id': next(ids), 'name': 'Quota extra'}}
ACCOUNTS = {'cassa': {'id': next(ids), 'name': 'Cassa'}}
INVOICES = {'twelve': {'id': next(ids), 'number': 12}}


def payment(person, subject, amount, created, **fields):
    """created: [day offset, 'HH:MM:SS'] in UTC."""
    row = {'id': next(ids), 'associate': person, 'subject': subject, 'amount': amount, 'created': created, 'paid_on': None, 'paid': False,
           'archived': False, 'expense': False, 'deleted': False, 'group': None, 'description': None, 'meta': None, 'course': None,
           'category': None, 'account': None, 'invoice': None, 'association': OWN, 'link': None}
    row.update(fields)
    return row


PAYMENTS = {
    'p1': payment('anna', 1, '50.00', [-40, '08:00:00']),
    'p2': payment('marco', 2, '120.50', [-20, '09:30:00'], link=['course', 'nuoto']),
    'p3': payment('marco', 2, '0.00', [-15, '10:00:00']),
    'p4': payment('tobia', 2, '30.00', [-10, '11:00:00'], paid_on=[-12, '18:00:00'], meta={'description': 'Rata 1 Yoga'},
                  link=['installment', 'yoga']),
    'p5': payment('anna', 0, '25.00', [-5, '07:15:00'], category='extra', description='Extra'),
    'p6': payment('lia', 2, '45.00', [-8, '12:00:00'], course={'label': 'Danza'}, description='Mensile'),
    'p7': payment('anna', 1, '50.00', [-60, '08:00:00'], paid=True, paid_on=[-59, '09:00:00'], invoice='twelve'),
    'p8': payment('anna', 2, '10.00', [-30, '16:00:00'], archived=True, link=['carnet', 'dieci']),
    'p9': payment('tobia', 0, '15.00', [-3, '10:00:00'], expense=True),
    'p10': payment('gina', 1, '40.00', [-2, '06:00:00'], group='own_group'),
    'p11': payment('anna', 1, '35.00', [-7, '10:00:00'], deleted=True),
    'p12': payment('nina', 3, '5.00', [-1, '05:00:00'], account='cassa'),
    'p13': payment('anna', 1, '20.00', [-25, '00:00:00']),
    'p14': payment('hans', 1, '60.00', [-6, '10:00:00'], association=FOREIGN),
    # A JSON boolean as the description: the column holds bools next to strings (an openpyxl boolean cell).
    'p15': payment('anna', 2, '12.00', [-4, '10:30:00'], meta={'description': True}, link=['course', 'nuoto']),
    'p16': payment('marco', 2, '13.00', [-9, '10:45:00'], meta={'description': False}, link=['course', 'nuoto']),
}
SUBJECT = {0: 'Altro', 1: 'Iscrizione', 2: 'Corso', 3: 'Giroconto'}
TYPE = {1: 'associato', 2: 'associato e tesserato', 3: 'tesserato'}


class Failure(Exception):
    """A builder exception: 400 'Errore nella generazione della stampa.'."""


# --- Python semantics of the request values ----------------------------------------------------------------------
def py_in(key, container):
    if isinstance(container, dict):
        return key in container
    if isinstance(container, (list, str)):
        return key in container
    raise Failure('argument of type is not iterable')


def py_get(container, key):
    if isinstance(container, dict) and key in container:
        return container[key]
    raise Failure('not subscriptable by str')


def py_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise Failure('expected a number') from None
    raise Failure('expected a number')


def status_set(value):
    if isinstance(value, dict):
        items = list(value)
    elif isinstance(value, (list, str)):
        items = list(value)
    else:
        raise Failure('not iterable')
    return {py_int(item) for item in items if item is not None}


def age(person):
    born = ASSOCIATES[person]['born']
    return 0 if born is None else int(-born / 365.25)


class State:
    """The tutor relations' is_primary flags across the sequence."""

    def __init__(self):
        self.relations = [list(r) for r in RELATIONS]
        self.promoted = []

    def main_tutor(self, person):
        mine = [r for r in self.relations if r[0] == person]
        for r in mine:
            if r[2]:
                return r[1]
        if mine:
            mine[0][2] = True
            self.promoted.append(person)
            return mine[0][1]
        return None


STATE = State()


def phone_columns(person):
    if person is None:
        raise Failure('NoneType has no phone')
    row = ASSOCIATES[person]
    phone, tutor_phone, contact = row['phone'] or '-', '-', 'Socio'
    if age(person) < 18:
        tutor = STATE.main_tutor(person)
        if tutor and ASSOCIATES[tutor]['phone']:
            tutor_phone, contact = ASSOCIATES[tutor]['phone'], 'Tutore'
    return phone, tutor_phone, contact


def filtered(data, association, group, *, medical=False, only_members=False):
    filters = py_get(data, 'filters')
    rows = [(label, s) for label, s in SUBSCRIPTIONS.items() if s['association'] == association and not s['archived']
            and (group is None or s['group'] == group)]
    if only_members:
        rows = [(label, s) for label, s in rows if s['type'] in (2, 3)]
    if medical:
        rows = [(label, s) for label, s in rows if s['medical']]
    if py_in('year', filters):
        year = py_get(filters, 'year')
        if year == 'pre-iscrizioni':
            rows = [(label, s) for label, s in rows if s['start'] is not None and s['start'] > 0]
        elif year == 'corrente':
            rows = [(label, s) for label, s in rows if s['start'] is not None and s['end'] is not None and s['start'] <= 0 <= s['end']]
        elif year == 'precedenti':
            rows = [(label, s) for label, s in rows if s['end'] is not None and s['end'] < 0]
    if py_in('status', filters):
        wanted = status_set(py_get(filters, 'status'))
        rows = [(label, s) for label, s in rows if s['status'] in wanted]
    # The query has no ordering: with the adapter's fresh statistics the
    # rows come in insertion order.
    return [s for _, s in rows]


def text(value):
    return value


def number(value):
    return {'int': str(value)}


def born_text(person):
    born = ASSOCIATES[person]['born']
    if born is None:
        raise Failure("'NoneType' object has no attribute 'strftime'")
    return f'$date:{born}'


def person_columns(people, phones):
    """Nome, Cognome, Email, Telefono, Tel. Tutore, Contatto (the first six columns of most reports)."""
    for person in people:
        if person is None:
            raise Failure('no associate')
    return [[ASSOCIATES[p]['first_name'] for p in people], [ASSOCIATES[p]['last_name'] for p in people],
            [ASSOCIATES[p]['email'] or '-' for p in people], [d[0] for d in phones], [d[1] for d in phones], [d[2] for d in phones]]


ILLEGAL = re.compile(r'[\000-\010]|[\013-\014]|[\016-\037]')


# The document title of each report's 'pdf' branch (the filename keeps the workbook's title).
PDF_TITLES = {
    'expiring_medical_certificates': 'Certificati medici in scadenza (entro 30 giorni dal $today)',
    'expired_medical_certificates': 'Certificati medici scaduti (giorno stampa $today)',
    'empty_medical_certificates': 'Certificati medici non presenti esclusi soggetti esenti (giorno stampa $today)',
    'exempt_medical_certificates': 'Atleti esenti dai certificati medici (giorno stampa $today)',
    'expiring_subscriptions': 'Iscrizioni in scadenza entro 30gg (giorno stampa $today)',
    'expiring_memberships': 'Tesseramenti in scadenza entro 30gg o scaduti (giorno stampa $today)',
    'all_subscriptions': 'Iscrizioni',
    'not_paid_quotes_subscriptions': 'Quote associative non pagate (giorno stampa $today)',
    'not_paid_courses_subscriptions': 'Quote corso/carnet non pagate (giorno stampa $today)',
    'expired_payments': 'Pagamenti scaduti (giorno stampa $today)',
    'subscriptions_with_all_payments': 'Iscrizioni con tutti i pagamenti (stampa $today)',
}


def finish(data, association, title, columns, rows):
    fmt = data['format']
    if fmt == 'excel':
        if any(isinstance(value, str) and ILLEGAL.search(value) for column in rows for value in column):
            raise Failure('openpyxl IllegalCharacterError: get_excel_base64 returns None')
        return {'file': {'$xlsx': {'engine': 'excel_utils', 'columns': columns, 'data': rows}},
                'filename': f'[$today] {title} - {DENOMINATION[association]}.xlsx'}
    if fmt == 'pdf':
        return {'file': {'$pdf': {'engine': 'data_table', 'columns': columns, 'rows': [list(row) for row in zip(*rows)],
                                  'title': PDF_TITLES[data['type']]}},
                'filename': f'[$today] {title} - {DENOMINATION[association]}.pdf'}
    raise Failure('Format not valid')


MEDICAL_COLUMNS = ['Nome', 'Cognome', 'Email', 'Telefono', 'Tel. Tutore', 'Contatto', 'Data di nascita', 'età',
                   'Scadenza certificato medico', 'Giorni rimanenti']


def medical_report(data, association, group, title, keep):
    subs = filtered(data, association, group, medical=True, only_members=True)
    subs = [s for s in subs if CERTIFICATES[s['medical']]['expiration'] is not None and keep(CERTIFICATES[s['medical']]['expiration'])]
    subs.sort(key=lambda s: CERTIFICATES[s['medical']]['expiration'])
    people = [s['associate'] for s in subs]
    phones = [phone_columns(p) for p in people]
    rows = person_columns(people, phones) + [[born_text(p) for p in people], [number(age(p)) for p in people],
                                             [f"$date:{CERTIFICATES[s['medical']]['expiration']}" for s in subs],
                                             [number(CERTIFICATES[s['medical']]['expiration']) for s in subs]]
    return finish(data, association, title, MEDICAL_COLUMNS, rows)


def expiring_medical(data, association, group):
    return medical_report(data, association, group, 'Certificati medici in scadenza (entro 30 giorni)', lambda d: 0 <= d <= 30)


def expired_medical(data, association, group):
    return medical_report(data, association, group, 'Certificati medici scaduti', lambda d: d < 0)


def without_medical(data, association, group, title, keep, columns):
    subs = filtered(data, association, group, only_members=True)
    subs = [s for s in subs if not s['medical'] and keep(age(s['associate']))]
    people = [s['associate'] for s in subs]
    phones = [phone_columns(p) for p in people]
    rows = person_columns(people, phones) + [[born_text(p) for p in people], [number(age(p)) for p in people]]
    if len(columns) == 10:
        rows += [['-' for _ in people], ['-' for _ in people]]
    return finish(data, association, title, columns, rows)


def empty_medical(data, association, group):
    return without_medical(data, association, group, 'Certificati medici non presenti (esclusi soggetti esenti)', lambda a: a >= 6,
                           MEDICAL_COLUMNS[:8])


def exempt_medical(data, association, group):
    return without_medical(data, association, group, 'Atleti esenti dai certificati medici', lambda a: a <= 6, MEDICAL_COLUMNS)


def expiring_subscriptions(data, association, group):
    subs = [s for s in filtered(data, association, group) if s['end'] is not None and 0 <= s['end'] <= 30]
    people = [s['associate'] for s in subs]
    phones = [phone_columns(p) for p in people]
    rows = person_columns(people, phones) + [[born_text(p) for p in people], [number(age(p)) for p in people],
                                             [number(s['end']) for s in subs]]
    return finish(data, association, 'Iscrizioni in scadenza (entro 30gg)', MEDICAL_COLUMNS[:8] + ['Giorni rimanenti'], rows)


def expiring_memberships(data, association, group):
    subs = filtered(data, association, group)
    people = {s['associate'] for s in subs}
    members = [m for m in MEMBERSHIPS if m['associate'] is not None and m['associate'] in people and m['end'] is not None and m['end'] <= 30]
    # No ordering: PostgreSQL answers the IN (subquery) with a hashed
    # semi-join; observed order of the authored memberships.
    members.sort(key=lambda m: MEMBERSHIP_ORDER.index(MEMBERSHIPS.index(m)))
    persons = [m['associate'] for m in members]
    phones = [phone_columns(p) for p in persons]
    rows = [[m['type'] for m in members]] + person_columns(persons, phones) + [
        [born_text(p) for p in persons], [number(age(p)) for p in persons], [number(m['end']) for m in members]]
    return finish(data, association, 'Tesseramenti in scadenza (entro 30gg o scaduti)', ['Tesseramento'] + MEDICAL_COLUMNS[:8] +
                  ['Giorni rimanenti'], rows)


def all_subscriptions(data, association, group):
    subs = filtered(data, association, group)
    people = [s['associate'] for s in subs]
    phones = [phone_columns(p) for p in people]
    rows = person_columns(people, phones) + [[born_text(p) for p in people], [number(age(p)) for p in people]]
    tutors = [STATE.main_tutor(p) for p in people]
    rows += [[ASSOCIATES[t]['first_name'] if t else '-' for t in tutors], [ASSOCIATES[t]['last_name'] if t else '-' for t in tutors]]
    return finish(data, association, 'Iscrizioni', MEDICAL_COLUMNS[:8] + ['Nome tutore', 'Cognome tutore'], rows)


def period(data):
    """None, or (from offset, to offset) of data['filters']['period'] (strings '$date:<offset>' only)."""
    filters = data['filters']
    if not py_in('period', filters):
        return None
    value = py_get(filters, 'period')
    bounds = []
    for key in ('from_date', 'to_date'):
        raw = py_get(value, key)
        if not (isinstance(raw, str) and raw.startswith('$date:')):
            raise Failure('time data does not match format')
        bounds.append(int(raw.split(':')[1]))
    return bounds


def created_in(p, bounds):
    """creation_date >= from 00:00 and <= to 00:00 (dates compared as UTC midnights)."""
    day, clock = p['created']
    return bounds is None or (day >= bounds[0] and (day < bounds[1] or (day == bounds[1] and clock == '00:00:00')))


def delay(p):
    return -(p['paid_on'] or p['created'])[0]


def course_name(p):
    """Payment.get_course_carnet_name for the authored payments."""
    if p['subject'] == 2:
        if p['link']:
            if p['meta'] and 'description' in p['meta']:
                description = p['meta']['description']
                return {'bool': description} if isinstance(description, bool) else description
            kind, name = p['link']
            return (CARNETS if kind == 'carnet' else COURSES)[name]['title']
        if p['course'] and 'label' in p['course']:
            return ', '.join([p['course']['label'], p['description'] or ''])
        return p['description']
    if p['subject'] == 1:
        return 'Iscrizione'
    if p['subject'] == 0:
        return CATEGORIES[p['category']]['name'] if p['category'] else None
    if p['subject'] == 3:
        raise Failure("'CustomAccounts' object has no attribute 'destination_account_name'")
    return None


def visible(p, association, group):
    return p['association'] == association and not p['deleted'] and (group is None or p['group'] == group)


def payment_report(data, association, group, title, columns, keep, *, sort=False, named=True):
    subs = filtered(data, association, group)
    people = {s['associate'] for s in subs if s['associate']}
    payments = [p for p in PAYMENTS.values() if visible(p, association, group) and not p['paid'] and p['associate'] in people and keep(p)]
    bounds = period(data)
    payments = [p for p in payments if created_in(p, bounds)]
    if sort:
        payments.sort(key=delay)
    persons = [p['associate'] for p in payments]
    phones = [phone_columns(person) for person in persons]
    rows = person_columns(persons, phones) + [[p['amount'].replace('.', ',') for p in payments]]
    if named:
        rows.append([course_name(p) for p in payments])
    rows += [[f"$date:{(p['paid_on'] or p['created'])[0]}" for p in payments], [number(delay(p)) for p in payments]]
    return finish(data, association, title, columns, rows)


PAYMENT_COLUMNS = MEDICAL_COLUMNS[:6] + ['quota (€)']


def not_paid_quotes(data, association, group):
    return payment_report(data, association, group, 'Quote associative non pagate', PAYMENT_COLUMNS + ['data', 'ritardo pagamento (gg)'],
                          lambda p: p['subject'] == 1 and p['created'][0] < 0, named=False)


def not_paid_courses(data, association, group):
    return payment_report(data, association, group, 'Quote/Carnet corso non pagate',
                          PAYMENT_COLUMNS + ['corso/carnet', 'data', 'ritardo pagamento (gg)'],
                          lambda p: p['subject'] == 2 and float(p['amount']) > 0 and p['created'][0] < 0)


def expired_payments(data, association, group):
    return payment_report(data, association, group, 'Pagamenti scaduti', PAYMENT_COLUMNS + ['attività', 'data', 'ritardo pagamento (gg)'],
                          lambda p: not p['archived'] and not p['expense'] and float(p['amount']) > 0 and p['created'][0] < 0, sort=True)


ALL_PAYMENT_COLUMNS = ['Cognome', 'Nome', 'Data nascita', 'Email', 'Telefono', 'Tel. Tutore', 'Contatto', 'Tipo iscrizione',
                       'Oggetto pagamento', 'Descrizione', 'Importo (€)', 'Stato', 'Data creazione', 'Data pagamento', 'N. Ricevuta']


def with_all_payments(data, association, group):
    subs = filtered(data, association, group)
    pairs = []
    for s in subs:
        mine = [p for p in PAYMENTS.values() if visible(p, association, group) and p['associate'] == s['associate']
                and not p['archived'] and not p['expense']]
        mine.sort(key=lambda p: (p['created'][0], p['created'][1]), reverse=True)
        bounds = period(data)
        pairs += [(s, p) for p in mine if created_in(p, bounds)]
    if not pairs:
        pairs = [(s, None) for s in subs]
    for s, _ in pairs:
        if s['associate'] is None:
            raise Failure('no associate')
    people = [s['associate'] for s, _ in pairs]
    phones = [phone_columns(person) for person in people]
    rows = [[ASSOCIATES[p]['last_name'] for p in people], [ASSOCIATES[p]['first_name'] for p in people],
            [f"$date:{ASSOCIATES[p]['born']}" if ASSOCIATES[p]['born'] is not None else '-' for p in people],
            [ASSOCIATES[p]['email'] or '-' for p in people], [d[0] for d in phones], [d[1] for d in phones], [d[2] for d in phones],
            [TYPE[s['type']] for s, _ in pairs],
            [SUBJECT[p['subject']] if p else '-' for _, p in pairs],
            [p['description'] if p and p['description'] else '-' for _, p in pairs],
            [p['amount'] if p else '-' for _, p in pairs],
            [('Pagato' if p['paid'] else 'Non pagato') if p else '-' for _, p in pairs],
            [f"$date:{p['created'][0]}" if p else '-' for _, p in pairs],
            [f"$date:{p['paid_on'][0]}" if p and p['paid_on'] else '-' for _, p in pairs],
            [str(INVOICES[p['invoice']]['number']) if p and p['invoice'] else '-' for _, p in pairs]]
    return finish(data, association, 'Iscrizioni con tutti i pagamenti', ALL_PAYMENT_COLUMNS, rows)


BUILDERS = {'expiring_medical_certificates': expiring_medical, 'expired_medical_certificates': expired_medical,
            'empty_medical_certificates': empty_medical, 'exempt_medical_certificates': exempt_medical,
            'expiring_subscriptions': expiring_subscriptions, 'expiring_memberships': expiring_memberships,
            'all_subscriptions': all_subscriptions, 'not_paid_quotes_subscriptions': not_paid_quotes,
            'not_paid_courses_subscriptions': not_paid_courses, 'expired_payments': expired_payments,
            'subscriptions_with_all_payments': with_all_payments}

cases = []


def add(name, actor, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'printing-generate-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def report(name, actor, kind, filters, *, fmt='excel', association=OWN, group=None, headers=None):
    """A builder case: the expectation and the promotions come from the builder model above."""
    body = {'type': kind, 'format': fmt, 'filters': filters}
    STATE.promoted = []
    try:
        status, response = 200, BUILDERS[kind](body, association, group)
    except Failure:
        status, response = 400, ERROR
    effect = {'promoted': [[child, actor] for child in STATE.promoted]} if STATE.promoted else None
    add(name, actor, status, response, body=body, headers=headers, effect=effect)


D = {'status': [1, 2, 4]}
OWN_GROUP_HEADER = {'X-Group-Id': IDS['own_group']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
VALID = {'type': 'all_subscriptions', 'format': 'excel', 'filters': D}

# --- identity, methods, OPTIONS --------------------------------------------------------------------------------------
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body=VALID)
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body=VALID)
add('restricted-collaborator', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.members.read'}, body=VALID)
add('scoped-athlete', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body=VALID,
    headers=SCOPED_ATHLETE)
add('get', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('put', 'owner', 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body=VALID)
add('options', 'owner', 200, {'name': 'Printing Generate', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('athlete', 'athlete', 403, '<empty>', body=VALID)
add('athlete-missing-keys', 'athlete', 403, '<empty>', body={})
add('list-body', 'owner', 500, SERVER, body=[VALID])
add('list-body-athlete', 'athlete', 500, SERVER, body=[])
add('null-body', 'owner', 500, SERVER, raw_body='null', content_type='application/json')
add('malformed-json', 'owner', 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'}, raw_body='oops',
    content_type='application/json')

# --- required keys and the type dispatch ---------------------------------------------------------------------------
add('missing-both', 'owner', 400, {'msg': 'Mancano alcuni parametri obbligatori:type, format'}, body={'filters': D})
add('missing-format', 'owner', 400, {'msg': 'Mancano alcuni parametri obbligatori:format'}, body={'type': 'all_subscriptions'})
add('missing-type', 'owner', 400, {'msg': 'Mancano alcuni parametri obbligatori:type'}, body={'format': 'excel', 'filters': D})
add('missing-null-values-present', 'owner', 400, INVALID_TYPE, body={'type': None, 'format': None})
add('unknown-type', 'owner', 400, INVALID_TYPE, body={'type': 'balance_sheet', 'format': 'excel', 'filters': D})
add('type-number', 'owner', 400, INVALID_TYPE, body={'type': 7, 'format': 'excel', 'filters': D})
add('type-case', 'owner', 400, INVALID_TYPE, body={'type': 'All_Subscriptions', 'format': 'excel', 'filters': D})
add('owner-without-association', 'owner_without_association', 400, ERROR, body=VALID)
add('owner-without-association-unknown-type', 'owner_without_association', 400, ERROR, body={'type': 'x', 'format': 'excel'})
add('missing-filters', 'owner', 400, ERROR, body={'type': 'expiring_subscriptions', 'format': 'excel'})
add('missing-filters-pdf', 'owner', 400, ERROR, body={'type': 'expiring_subscriptions', 'format': 'pdf'})
add('form-missing-format', 'owner', 400, {'msg': 'Mancano alcuni parametri obbligatori:format'}, raw_body='type=all_subscriptions',
    content_type='application/x-www-form-urlencoded')

# --- the medical reports: the first two promote gina's and marco's first tutor relations ------------------------------
report('expiring-medical', 'owner', 'expiring_medical_certificates', D)
report('expired-medical-collaborator', 'full_collaborator', 'expired_medical_certificates', D)
report('expiring-medical-again', 'owner', 'expiring_medical_certificates', D)
report('expiring-medical-deleted-subscription', 'owner', 'expiring_medical_certificates', {'status': [3]})
report('expired-medical-precedenti', 'owner', 'expired_medical_certificates', {'year': 'precedenti'})
report('empty-medical', 'owner', 'empty_medical_certificates', D)
report('exempt-medical', 'owner', 'exempt_medical_certificates', D)
report('exempt-medical-without-birth-date', 'owner', 'exempt_medical_certificates', {'status': [5]})
report('empty-medical-group', 'owner', 'empty_medical_certificates', D, group='own_group', headers=OWN_GROUP_HEADER)
report('exempt-medical-csv', 'owner', 'exempt_medical_certificates', D, fmt='csv')

# --- subscriptions and memberships --------------------------------------------------------------------------------
report('expiring-subscriptions', 'owner', 'expiring_subscriptions', D)
report('expiring-subscriptions-corrente', 'owner', 'expiring_subscriptions', {'year': 'corrente', 'status': [1, 2, 4]})
report('expiring-subscriptions-no-filters', 'owner', 'expiring_subscriptions', {})
report('expiring-subscriptions-filters-text', 'owner', 'expiring_subscriptions', 'tutti')
report('expiring-subscriptions-filters-text-year', 'owner', 'expiring_subscriptions', 'year')
report('expiring-subscriptions-filters-list', 'owner', 'expiring_subscriptions', ['corrente'])
report('expiring-subscriptions-filters-list-key', 'owner', 'expiring_subscriptions', ['status'])
report('expiring-subscriptions-filters-null', 'owner', 'expiring_subscriptions', None)
report('expiring-subscriptions-status-text', 'owner', 'expiring_subscriptions', {'status': '41'})
report('expiring-subscriptions-status-text-other', 'owner', 'expiring_subscriptions', {'status': '13'})
report('expiring-subscriptions-status-keys-other', 'owner', 'expiring_subscriptions', {'status': {'1': 4}})
report('expiring-subscriptions-status-mixed', 'owner', 'expiring_subscriptions', {'status': [None, '4', 2.9, True]})
report('expiring-subscriptions-status-keys', 'owner', 'expiring_subscriptions', {'status': {'4': False}})
report('expiring-subscriptions-status-empty', 'owner', 'expiring_subscriptions', {'status': []})
report('expiring-subscriptions-status-only-null', 'owner', 'expiring_subscriptions', {'status': [None]})
report('expiring-subscriptions-status-invalid', 'owner', 'expiring_subscriptions', {'status': ['quattro']})
report('expiring-subscriptions-status-number', 'owner', 'expiring_subscriptions', {'status': 4})
report('expiring-subscriptions-status-nested', 'owner', 'expiring_subscriptions', {'status': [[4]]})
report('expiring-subscriptions-year-other', 'owner', 'expiring_subscriptions', {'year': 2026, 'status': [4]})
report('expiring-subscriptions-pdf-bad-status', 'owner', 'expiring_subscriptions', {'status': ['x']}, fmt='pdf')
report('expiring-memberships', 'owner', 'expiring_memberships', D)
report('expiring-memberships-without-birth-date', 'owner', 'expiring_memberships', {'status': [5]})
report('all-subscriptions', 'owner', 'all_subscriptions', D)
report('all-subscriptions-pre-iscrizioni', 'owner', 'all_subscriptions', {'year': 'pre-iscrizioni'})
report('all-subscriptions-precedenti', 'owner', 'all_subscriptions', {'year': 'precedenti', 'status': [3, 4]})
report('all-subscriptions-illegal-character', 'owner', 'all_subscriptions', {'status': [3]})
report('all-subscriptions-group', 'owner', 'all_subscriptions', D, group='own_group', headers=OWN_GROUP_HEADER)
report('all-subscriptions-group-foreign', 'owner', 'all_subscriptions', D, group='foreign_group',
       headers={'X-Group-Id': IDS['foreign_group']})
report('all-subscriptions-group-malformed', 'owner', 'all_subscriptions', D, headers={'X-Group-Id': 'nope'})
report('all-subscriptions-collaborator', 'full_collaborator', 'all_subscriptions', D)
report('all-subscriptions-admin-impersonating', 'admin', 'all_subscriptions', D, headers=ADMIN_OWNER)
report('all-subscriptions-without-birth-date', 'owner', 'all_subscriptions', {'status': [5]})
report('all-subscriptions-foreign-promotes-then-fails', 'foreign_owner', 'all_subscriptions', D, association=FOREIGN)
report('all-subscriptions-foreign-again', 'foreign_owner', 'all_subscriptions', D, association=FOREIGN)
report('expiring-memberships-foreign', 'foreign_owner', 'expiring_memberships', D, association=FOREIGN)
# SportAssociation.objects (ActiveManager) hides deleted associations: DoesNotExist.
add('all-subscriptions-deleted-association', 'deleted_association_owner', 400, ERROR, body=VALID)
report('all-subscriptions-csv', 'owner', 'all_subscriptions', D, fmt='csv')
add('expiring-subscriptions-form', 'owner', 200, BUILDERS['expiring_subscriptions'](
    {'type': 'expiring_subscriptions', 'format': 'excel', 'filters': 'tutti'}, OWN, None),
    raw_body='type=expiring_subscriptions&format=excel&filters=tutti', content_type='application/x-www-form-urlencoded')
add('expiring-subscriptions-form-status', 'owner', 400, ERROR, raw_body='type=expiring_subscriptions&format=excel&filters=status',
    content_type='application/x-www-form-urlencoded')

# --- payments --------------------------------------------------------------------------------------------------------
PERIOD = {'from_date': '$date:-30', 'to_date': '$date:-25'}
report('not-paid-quotes', 'owner', 'not_paid_quotes_subscriptions', D)
report('not-paid-quotes-period', 'owner', 'not_paid_quotes_subscriptions', {**D, 'period': PERIOD})
report('not-paid-quotes-group', 'owner', 'not_paid_quotes_subscriptions', D, group='own_group', headers=OWN_GROUP_HEADER)
report('not-paid-quotes-period-invalid', 'owner', 'not_paid_quotes_subscriptions',
       {**D, 'period': {'from_date': '31/02/2026', 'to_date': '$date:0'}})
report('not-paid-quotes-period-missing-bound', 'owner', 'not_paid_quotes_subscriptions', {**D, 'period': {'from_date': '$date:-3'}})
report('not-paid-quotes-period-text', 'owner', 'not_paid_quotes_subscriptions', {**D, 'period': '$date:-3'})
report('not-paid-courses', 'owner', 'not_paid_courses_subscriptions', D)
report('not-paid-courses-foreign', 'foreign_owner', 'not_paid_courses_subscriptions', {'status': [4], 'year': 'corrente'},
       association=FOREIGN)
report('expired-payments', 'owner', 'expired_payments', D)
report('expired-payments-period', 'owner', 'expired_payments', {**D, 'period': {'from_date': '$date:-21', 'to_date': '$date:-1'}})
report('expired-payments-account-transfer', 'owner', 'expired_payments', {'status': [4, 5]})
report('expired-payments-group', 'full_collaborator', 'expired_payments', D, group='own_group', headers=OWN_GROUP_HEADER)
report('with-all-payments', 'owner', 'subscriptions_with_all_payments', D)
report('with-all-payments-period', 'owner', 'subscriptions_with_all_payments', {**D, 'period': PERIOD})
report('with-all-payments-period-empty', 'owner', 'subscriptions_with_all_payments',
       {**D, 'period': {'from_date': '$date:-900', 'to_date': '$date:-800'}})
report('with-all-payments-no-subscriptions-invalid-period', 'owner', 'subscriptions_with_all_payments',
       {'status': [], 'period': {'from_date': 'x'}})
report('with-all-payments-foreign', 'foreign_owner', 'subscriptions_with_all_payments', D, association=FOREIGN)

# --- the 'pdf' format: one document per report, after every promotion above has happened ------------------------------
for kind in PDF_TITLES:
    report(f"pdf-{kind.replace('_', '-')}", 'owner', kind, D, fmt='pdf')
report('pdf-all-subscriptions-control-character', 'owner', 'all_subscriptions', {'status': [3]}, fmt='pdf')
report('pdf-all-subscriptions-no-rows', 'owner', 'all_subscriptions', {'status': []}, fmt='pdf')
report('pdf-all-subscriptions-group', 'owner', 'all_subscriptions', D, fmt='pdf', group='own_group', headers=OWN_GROUP_HEADER)
report('pdf-all-subscriptions-collaborator', 'full_collaborator', 'all_subscriptions', D, fmt='pdf')
report('pdf-all-subscriptions-without-birth-date', 'owner', 'all_subscriptions', {'status': [5]}, fmt='pdf')
report('pdf-expired-payments-period', 'owner', 'expired_payments', {**D, 'period': {'from_date': '$date:-21', 'to_date': '$date:-1'}},
       fmt='pdf')
report('pdf-expired-payments-account-transfer', 'owner', 'expired_payments', {'status': [4, 5]}, fmt='pdf')
report('pdf-with-all-payments-foreign', 'foreign_owner', 'subscriptions_with_all_payments', D, fmt='pdf', association=FOREIGN)
report('pdf-not-paid-courses-foreign', 'foreign_owner', 'not_paid_courses_subscriptions', {'status': [4], 'year': 'corrente'},
       fmt='pdf', association=FOREIGN)
add('pdf-expiring-subscriptions-form', 'owner', 200, BUILDERS['expiring_subscriptions'](
    {'type': 'expiring_subscriptions', 'format': 'pdf', 'filters': 'tutti'}, OWN, None),
    raw_body='type=expiring_subscriptions&format=pdf&filters=tutti', content_type='application/x-www-form-urlencoded')
report('pdf-format-case', 'owner', 'all_subscriptions', D, fmt='PDF')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1,
                              'scope': 'printing/generate excel and pdf reports, tutor promotion and request validation',
                              'ids': IDS, 'associates': ASSOCIATES, 'relations': RELATIONS, 'certificates': CERTIFICATES,
                              'subscriptions': SUBSCRIPTIONS, 'memberships': MEMBERSHIPS, 'courses': COURSES, 'carnets': CARNETS,
                              'categories': CATEGORIES, 'accounts': ACCOUNTS, 'invoices': INVOICES, 'payments': PAYMENTS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
