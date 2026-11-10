"""Author the statistic/dashboard, statistic/athlete-dashboard and statistic/report HTTP sequence.

Read from BE/application/views/statistic_views.py (see docs/architecture/go-migration/statistic-reads.md).
The dataset is declarative; the expectations below are an independent oracle written from the view
source, never copied from a run. Time specs are resolved by the contract at seed and request time:
- an ISO string is absolute ('YYYY-MM-DD' for DateFields, '...Z' for DateTimeFields);
- {'day': k, 'at': 'HH:MM:SS[.ffffff]'} is the seed's UTC date T plus k days at that UTC time
  ({'day': k} alone is a date);
- {'now': seconds} is the seed instant (whole seconds) plus seconds (optional 'us' microseconds).
Rows relative to T sit at least a day from every window edge, rows at T 00:00 are inside every
current balance-sheet year, and rows before 2024-01-01 are outside every relative window, so the
static expectations hold on any run day. Request-time values ($available_years, $month_series,
$income) are computed by the contract. Run with ``python3 -I`` and commit the output.
"""
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('statistic_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
GROUP = IDS['own_group']
SERVER = '<stable-server-error-page>'
_numbers = iter(range(930001, 939999))
R = {}


def rid(label):
    assert label not in R, label
    R[label] = f'a1000000-0000-4000-8000-{next(_numbers):012d}'
    return R[label]


OLD = '2023-01-01T10:00:00Z'  # outside every relative window and every authored fiscal year


def day(k, at=None):
    return {'day': k} if at is None else {'day': k, 'at': at}


MIDNIGHT = day(0, '00:00:00')

# ---------------------------------------------------------------------------
# Identities added by this family (tokens are minted by the adapter).
# ---------------------------------------------------------------------------
USERS = {
    'athlete_two': {'role': 'athlete'},
    'athlete_broken': {'role': 'athlete'},
    'owner_three': {'role': 'association'},
}
for label in USERS:
    rid(label)
rid('association_three')
# The owner's balance-sheet year starts on 1 September; the foreign owner keeps 1 January.
OWNER_BALANCE = {'day': 1, 'month': 9}

# ---------------------------------------------------------------------------
# Associates (born dates relative to T keep int(days / 365.25) away from an integer).
# ---------------------------------------------------------------------------
ASSOCIATES = {
    'as1': {'association': OWN, 'first': 'Giulia', 'last': 'Rossi', 'sex': 'F', 'born': day(-11000), 'age': 30},
    'as2': {'association': OWN, 'first': 'Marco', 'last': 'Bianchi', 'sex': 'M', 'born': day(-7400), 'age': 20},
    'as3': {'association': OWN, 'first': 'Alex', 'last': 'Verdi', 'sex': 'A', 'born': None, 'age': 0},
    'as_a1': {'association': OWN, 'first': 'Atleta', 'last': 'Uno', 'sex': 'M', 'born': None, 'age': 0,
              'user': ATHLETE, 'tax': 'RSSMRA80A01H501U'},
    # Same tax code as as_a1: only a soft-deleted associate may share it (unique among active associates).
    'as_a2': {'association': OWN, 'first': 'Figlio', 'last': 'Uno', 'sex': 'M', 'born': None, 'age': 0, 'tax': 'RSSMRA80A01H501U',
              'deleted': True},
    'as_a3': {'association': OWN, 'first': 'Atleta', 'last': 'Tre', 'sex': 'M', 'born': None, 'age': 0, 'user': ATHLETE},
    'as_b': {'association': OWN, 'first': 'Rotto', 'last': 'Eventi', 'sex': 'F', 'born': None, 'age': 0, 'user': 'athlete_broken'},
    'as_f': {'association': FOREIGN, 'first': 'Estero', 'last': 'Socio', 'sex': 'F', 'born': None, 'age': 0},
}
for label in ASSOCIATES:
    rid(label)

MEDICALS = {'m1': day(5), 'm2': day(-1), 'm3': day(0), 'm4': day(12), 'm5': day(-40), 'm6': day(3), 'm7': None}
for label in MEDICALS:
    rid(label)

# ---------------------------------------------------------------------------
# Subscriptions. status: 1 not signed, 2 pending, 3 rejected, 4 accepted, 5 resigned.
# type: 1 associate only, 2 associate and member, 3 member only.
# ---------------------------------------------------------------------------
def sub(association=OWN, *, status, type=2, start=None, end=None, created, associate=None, medical=None, archived=False,
        group=None, user=OWNER):
    return {'association': association, 'status': status, 'type': type, 'start': start, 'end': end, 'created': created,
            'associate': associate, 'medical': medical, 'archived': archived, 'group': group, 'user': user}


SUBSCRIPTIONS = {
    'sa': sub(status=4, start=day(-100), end=day(200), created=day(-40, '12:00:00'), associate='as1'),
    'sb': sub(status=2, type=3, start=day(-5), end=day(5), created=day(-3, '12:00:00'), associate='as2'),
    'sc': sub(status=1, start=day(0), end=day(0), created=day(-3, '15:00:00.250000'), associate='as3'),
    'sq': sub(status=2, start=day(-1), end=day(1), created=day(-3, '15:00:00.250000')),
    'sd': sub(status=3, start=day(-1), end=day(1), created=day(-10, '12:00:00'), associate='as1'),
    'se': sub(status=4, type=1, start=day(-1), end=day(1), created=day(-10, '13:00:00'), associate='as2'),
    'sf': sub(status=2, start=day(-1), end=day(1), created=day(-5, '12:00:00'), associate='as1', archived=True),
    'sg': sub(status=4, start=day(1), end=day(30), created=MIDNIGHT, associate='as2', medical='m1'),
    'sh': sub(status=2, start=day(-30), end=day(-1), created=MIDNIGHT, associate='as3', medical='m2'),
    'si': sub(status=4, type=3, start=day(-10), end=day(10), created=MIDNIGHT, associate='as1', medical='m3'),
    'sj': sub(status=4, created=MIDNIGHT, associate='as1', medical='m4', archived=True),
    'sk': sub(status=4, created=MIDNIGHT, associate='as2', medical='m5', archived=True),
    'sl': sub(status=2, created='2023-06-01T10:00:00Z', associate='as3', medical='m6'),
    'sm': sub(status=2, created=MIDNIGHT, associate='as3', medical='m7'),
    'sn': sub(status=4, start=day(-2), end=day(2), created=day(-2, '12:00:00'), associate='as2', group=GROUP),
    'sp': sub(status=5, start=day(-1), end=day(1), created=day(-20, '12:00:00'), associate='as1'),
    # Report rows: the 2024-09-01 .. 2025-08-31 balance-sheet year of the owner.
    'r1': sub(status=4, created='2024-09-15T10:00:00Z', associate='as3'),
    'r2': sub(status=2, created='2024-09-20T10:00:00Z', associate='as1'),
    'r3': sub(status=4, created='2025-01-10T10:00:00Z', associate='as1'),
    'r4': sub(status=4, created='2025-01-11T10:00:00Z', associate='as2', archived=True),
    'r5': sub(status=4, created='2025-08-31T00:00:00Z', associate='as2'),
    'r6': sub(status=3, created='2025-08-31T00:00:01Z', associate='as2'),
    'r7': sub(status=1, created='2024-09-01T00:00:00Z', associate='as2'),
    'r8': sub(status=3, created='2024-08-31T23:59:59Z', associate='as2'),
    'r9': sub(status=4, created='2025-02-14T10:00:00Z', associate='as1', group=GROUP),
    'r10': sub(status=4, created='2024-08-31T00:00:00Z', associate='as3'),
    # Athlete dashboard rows (status 5 and created long ago: neutral for every association widget).
    'sub_a1': sub(status=5, start=day(-10), end=day(10), created=OLD, associate='as_a1', user=ATHLETE),
    'sub_a2': sub(status=5, start=day(20), end=day(40), created=OLD, associate='as_a2'),
    'sub_a3': sub(status=5, created=OLD, associate='as_a3', archived=True),
    # The foreign association registered this subscription under its own account.
    'sub_a4': sub(FOREIGN, status=5, start=day(-10), end=day(10), created=OLD, associate='as_f', user=IDS['foreign_owner']),
    'sub_b1': sub(status=5, created=OLD, associate='as_b', user='athlete_broken'),
    # Foreign association.
    'sf1': sub(FOREIGN, status=2, start=day(-1), end=day(1), created=MIDNIGHT, associate='as_f', user=IDS['foreign_owner']),
    # Third association: an accepted subscription without an associate.
    'st1': sub('association_three', status=4, created=OLD, user='owner_three'),
}
for label in SUBSCRIPTIONS:
    rid(label)


# ---------------------------------------------------------------------------
# Payments. subject: 0 other, 1 subscription, 2 course.
# ---------------------------------------------------------------------------
def pay(amount, *, association=OWN, paid=True, expense=False, archived=False, deleted=False, subject=0, created=OLD,
        paid_on=None, group=None):
    return {'association': association, 'amount': amount, 'paid': paid, 'expense': expense, 'archived': archived,
            'deleted': deleted, 'subject': subject, 'created': created, 'payment_date': paid_on, 'group': group}


PAYMENTS = {
    # statistic/dashboard?widget=payments (creation in the last 30 days, amount > 0, paid).
    'p1': pay('0.10', created=day(-5, '09:00:00')),
    'p2': pay('0.20', created=day(-5, '18:00:00')),
    'p3': pay('0.00', created=day(-5, '10:00:00')),
    'p4': pay('5.05', paid=False, created=day(-4, '10:00:00')),
    'p5': pay('0.10', archived=True, created=day(-4, '10:00:00')),
    'p6': pay('99.99', deleted=True, created=day(-4, '11:00:00')),
    'p7': pay('0.30', expense=True, created=day(-1, '10:00:00')),
    'p8': pay('3.00', created=day(-40, '10:00:00')),
    'p9': pay('0.20', created=MIDNIGHT),
    'p10': pay('0.10', created=day(-2, '10:00:00'), group=GROUP),
    'p11': pay('0.10', created=day(-20, '10:00:00')),
    'pf1': pay('50.00', association=FOREIGN, subject=1, created=MIDNIGHT),
    # statistic/dashboard?widget=incomeAndExpenses (payment_date windows in Europe/Rome).
    'i1': pay('15.50', paid_on={'now': 0}),
    'i2': pay('4.00', expense=True, paid_on={'now': -60}),
    'i3': pay('100.00', paid_on={'now': -10 * 86400}),
    'i4': pay('30.00', expense=True, paid_on={'now': -20 * 86400}),
    'i5': pay('1000.00', paid_on={'now': -60 * 86400}),
    'i6': pay('7.00', paid=False, paid_on={'now': 0}),
    'i7': pay('8.00', archived=True, paid_on={'now': 0}),
    'i8': pay('9.00'),
    'i9': pay('2.00', paid_on={'now': -400 * 86400}),
    'i10': pay('6.00', deleted=True, paid_on={'now': 0}),
    'i11': pay('0.25', paid_on={'now': -3 * 86400}, group=GROUP),
    # statistic/report rows for the 2024-09-01 .. 2025-08-31 year.
    'q1': pay('50.00', subject=1, created='2024-10-01T10:00:00Z', paid_on='2024-10-05T12:00:00Z'),
    'q2': pay('30.00', subject=2, created='2024-06-01T10:00:00Z', paid_on='2025-02-01T12:00:00Z'),
    'q3': pay('12.34', created='2025-03-01T10:00:00Z'),
    'q4': pay('20.00', paid=False, subject=1, created='2025-04-01T10:00:00Z'),
    'q5': pay('9.00', expense=True, created='2025-01-01T10:00:00Z', paid_on='2025-01-01T10:00:00Z'),
    'q6': pay('5.00', archived=True, subject=1, created='2025-01-02T10:00:00Z', paid_on='2025-01-02T10:00:00Z'),
    'q7': pay('70.00', subject=1, created='2025-03-01T10:00:00Z', paid_on='2025-09-15T10:00:00Z'),
    'q8': pay('11.11', subject=2, created='2025-05-05T10:00:00Z', paid_on='2025-05-05T10:00:00Z', group=GROUP),
    'q9': pay('3.00', deleted=True, subject=2, created='2025-05-06T10:00:00Z', paid_on='2025-05-06T10:00:00Z'),
    'q10': pay('40.00', subject=2, created='2023-10-10T10:00:00Z', paid_on='2024-09-10T10:00:00Z'),
    'q11': pay('25.00', subject=1, created='2024-03-03T10:00:00Z', paid_on='2024-08-31T00:00:00Z'),
}
for label in PAYMENTS:
    rid(label)

INVOICES = {
    'v1': {'created': '2024-12-01T10:00:00Z'}, 'v2': {'created': '2024-12-02T10:00:00Z', 'archived': True},
    'v3': {'created': '2025-08-31T00:00:00Z'}, 'v4': {'created': '2024-12-03T10:00:00Z', 'deleted': True},
    'v5': {'created': '2023-05-01T10:00:00Z'},
}
for label in INVOICES:
    rid(label)


# ---------------------------------------------------------------------------
# Courses and course subscriptions. status: 1 draft, 2 active.
# ---------------------------------------------------------------------------
def course(title, *, association=OWN, status=2, fee='10.00', created=OLD, deleted=False, description=None, group=None):
    return {'association': association, 'title': title, 'status': status, 'fee': fee, 'created': created, 'deleted': deleted,
            'description': description, 'group': group}


COURSES = {
    'b1': course('Pilates', description='Mattina'), 'b2': course('Zumba'), 'b3': course('Boxe', description='Sera è'),
    'b4': course('Danza', status=1), 'b5': course('Tennis', deleted=True), 'fb1': course('Estero', association=FOREIGN),
    'c_r1': course('Nuoto', fee='10.00', created='2024-10-01T10:00:00Z'),
    'c_r2': course('Nuoto', fee='15.50', created='2024-11-01T10:00:00Z'),
    'c_r3': course('Yoga', fee='20.00', created='2025-01-01T10:00:00Z'),
    'c_r4': course('Bozza', status=1, created='2025-01-01T10:00:00Z'),
    'c_r5': course('Vecchio', created='2023-01-01T10:00:00Z'),
    'c_r6': course('Cancellato', deleted=True, created='2025-01-01T10:00:00Z'),
    'c1': course('Calcio'), 'c2': course('Basket'), 'c3': course('Eventi rotti'),
}
for label in COURSES:
    rid(label)

COURSE_SUBSCRIPTIONS = {}


def cs(label, course_label, *, created=OLD, deleted=False, subscription=None):
    COURSE_SUBSCRIPTIONS[label] = {'course': course_label, 'created': created, 'deleted': deleted, 'subscription': subscription}
    rid(label)


for n, created in enumerate([day(-1, '10:00:00'), day(-1, '11:00:00'), day(-3, '10:00:00'), OLD, OLD]):
    cs(f'cs_b1_{n}', 'b1', created=created)
cs('cs_b1_deleted', 'b1', created=day(-2, '10:00:00'), deleted=True)
for n, created in enumerate([day(-6, '10:00:00'), OLD, OLD, OLD, OLD]):
    cs(f'cs_b2_{n}', 'b2', created=created)
for n, created in enumerate([MIDNIGHT, OLD, OLD, OLD]):
    cs(f'cs_b3_{n}', 'b3', created=created)
for n in range(7):
    cs(f'cs_b4_{n}', 'b4', created=day(-1, '12:00:00') if n == 0 else OLD)
for n in range(8):
    cs(f'cs_b5_{n}', 'b5', created=day(-2, '12:00:00') if n == 0 else OLD)
for n in range(9):
    cs(f'cs_fb1_{n}', 'fb1', created=day(-1, '12:00:00'))
cs('cs_r1_a', 'c_r1')
cs('cs_r1_b', 'c_r1')
cs('cs_r1_deleted', 'c_r1', deleted=True)
cs('cs_r2_a', 'c_r2', subscription='r3')
cs('cs_a1', 'c1', subscription='sub_a1')
cs('cs_a3', 'c1', subscription='sub_a3')
cs('cs_a2', 'c2', subscription='sub_a2')
cs('cs_b', 'c3', subscription='sub_b1')

# ---------------------------------------------------------------------------
# Attendance registries and days (athlete dashboard).
# ---------------------------------------------------------------------------
MS = '%Y-%m-%dT%H:%M:%S.000Z'


def event(event_id, start, *, title='Allenamento', fmt=MS):
    item = {'event_id': event_id, 'start': {'$fmt': [start, fmt]}, 'end': None}
    if title is not None:
        item['title'] = title
    return item


REGISTRIES = {
    'rg1': {'course': 'c1', 'events': [
        event('ev-1', day(2, '10:00:00')), event('ev-2', day(4, '18:30:00'), title='Partita è'),
        event('ev-3', day(3, '10:00:00')), event('ev-4', day(10, '10:00:00')), event('ev-5', day(-2, '10:00:00')),
        event('ev-6', day(1, '10:00:00'), fmt='%Y-%m-%dT%H:%M:%SZ'), event('ev-7', day(5, '10:00:00'), title=None),
        event('ev-8', day(6, '10:00:00'))]},
    'rg2': {'course': 'c2', 'events': [event('ev-9', day(1, '08:00:00'), title='Basket mattina')]},
    'rg3': {'course': 'c3', 'events': ['not-an-event']},
}
for label in REGISTRIES:
    rid(label)
DAYS = {
    'd1': {'registry': 'rg1', 'date': day(2, '10:00:00'), 'attendees': ['cs_a1']},
    'd2': {'registry': 'rg1', 'date': day(4, '18:30:00'), 'absences': ['cs_a1']},
    'd4': {'registry': 'rg1', 'date': day(10, '10:00:00')},
    'd5': {'registry': 'rg1', 'date': day(-2, '10:00:00')},
    'd6': {'registry': 'rg1', 'date': day(1, '10:00:00')},
    'd7': {'registry': 'rg1', 'date': day(5, '10:00:00')},
    'd8': {'registry': 'rg1', 'date': day(6, '10:00:00'), 'auto_marked': True},
    'd9': {'registry': 'rg2', 'date': day(1, '08:00:00')},
}
for label in DAYS:
    rid(label)

# ---------------------------------------------------------------------------
# Messages delivered to the own association (athlete dashboard communications).
# ---------------------------------------------------------------------------
MESSAGES = {'msg_post': {'association': OWN, 'type': 'INSIDE_APP', 'text': 'Benvenuti in palestra è'},
            'msg_email': {'association': OWN, 'type': 'EMAIL', 'text': 'Solo email'},
            'msg_foreign_post': {'association': FOREIGN, 'type': 'INSIDE_APP', 'text': 'Avviso estero'}}
for label in MESSAGES:
    rid(label)
DELIVERIES = {'mt1': {'message': 'msg_post', 'sent': {'now': -86400, 'us': 123456}},
              'mt2': {'message': 'msg_post', 'sent': {'now': -3 * 86400}},
              'mt3': {'message': 'msg_email', 'sent': {'now': -86400}},
              'mt4': {'message': 'msg_post', 'sent': {'now': -20 * 86400}},
              'mt5': {'message': 'msg_foreign_post', 'sent': {'now': -2 * 86400}},
              'mt6': {'message': 'msg_post', 'sent': {'now': -3600}, 'recipient': FOREIGN}}
for label in DELIVERIES:
    rid(label)

# ---------------------------------------------------------------------------
# Carnets (expiringcarnets).
# ---------------------------------------------------------------------------
CARNETS = {'k1': {'association': OWN, 'title': 'Carnet 10', 'description': 'Dieci ingressi', 'lessons': 10, 'fee': '50.00', 'public': True},
           'kf': {'association': FOREIGN, 'title': 'Estero', 'description': None, 'lessons': 5, 'fee': '9.90', 'public': False},
           'k3': {'association': 'association_three', 'title': 'Terzo', 'description': None, 'lessons': 3, 'fee': '1.00', 'public': True}}
for label in CARNETS:
    rid(label)
REG_FMT = '%Y-%m-%d %H:%M:%S+0000'
CARNET_SUBSCRIPTIONS = {
    'ck1': {'subscription': 'sa', 'meta': {'lessons_left': 2}},
    'ck2': {'subscription': 'sb', 'meta': {'lessons_left': 3.5, 'note': 'mezza è'}},
    'ck3': {'subscription': 'sa', 'meta': {'lessons_left': 5}},
    'ck4': {'subscription': 'sa', 'meta': {'lessons_left': 0}},
    'ck5': {'subscription': 'sb', 'meta': {'lessons_left': 0, 'lessons_registry': [{'date': {'$fmt': [{'now': -5 * 86400}, REG_FMT]}}]}},
    'ck6': {'subscription': 'sa', 'meta': {'lessons_left': 0, 'lessons_registry': [{'date': {'$fmt': [{'now': -40 * 86400}, REG_FMT]}}]}},
    'ck7': {'subscription': 'sa', 'meta': {'lessons_left': '1'}},
    'ck8': {'subscription': 'sa', 'meta': {'lessons_left': None}},
    'ck9': {'subscription': 'sa', 'meta': None},
    'ck10': {'subscription': 'sa', 'meta': {'lessons_left': True}},
    'ck11': {'subscription': 'sd', 'meta': {'lessons_left': -1, 'lessons_registry': 'ignored'}},
    'ck12': {'subscription': 'sa', 'meta': {'lessons_left': 0, 'lessons_registry': [{'date': 'garbage'}]}},
    'ck13': {'subscription': 'sf1', 'carnet': 'kf', 'meta': {'lessons_left': 1}},
    'ck14': {'subscription': 'sa', 'meta': {'lessons_left': 0, 'lessons_registry': ['x', {'other': 1}]}},
    'ck_three': {'subscription': 'st1', 'carnet': 'k3', 'meta': {'lessons_left': 1}},
}
for label, row in CARNET_SUBSCRIPTIONS.items():
    row.setdefault('carnet', 'k1')
    row['created'] = '2026-09-01T10:00:00.123456Z'
    rid(label)

# ---------------------------------------------------------------------------
# A fourth association for the todaylessons and expiredPayments widgets, with an owner and a full
# collaborator who is also linked to one of the owner's instructors (Instructor.associated_user_id).
# Its rows are only read by the cases below.
# ---------------------------------------------------------------------------
FOUR = 'association_four'
USERS['owner_four'] = {'role': 'association'}
USERS['collab_four'] = {'role': 'collaborator', 'connected': 'owner_four'}
for label in ('owner_four', 'collab_four', FOUR):
    rid(label)
EXTRA_ASSOCIATIONS = {FOUR: {'owner': 'owner_four', 'denomination': 'Statistiche quarta', 'tax_code': '93000000004'}}
FOUR_ASSOCIATES = {
    'a4_1': {'association': FOUR, 'first': 'Lucia', 'last': 'Neri', 'sex': 'F', 'born': None, 'age': 0, 'tax': 'NRELCU90A41H501X'},
    'a4_2': {'association': FOUR, 'first': 'Paolo', 'last': 'Gialli', 'sex': 'M', 'born': None, 'age': 0},
    'a4_3': {'association': FOUR, 'first': 'Sara', 'last': 'Blu', 'sex': 'F', 'born': None, 'age': 0},
    'a4_4': {'association': FOUR, 'first': 'Ugo', 'last': 'Verde', 'sex': 'M', 'born': None, 'age': 0},
}
for label in FOUR_ASSOCIATES:
    rid(label)
ASSOCIATES.update(FOUR_ASSOCIATES)
FOUR_SUBSCRIPTIONS = {
    's4_1': sub(FOUR, status=4, start=day(-60), end=day(300), created='2023-03-01T10:00:00Z', associate='a4_1', user='owner_four'),
    # Same associate and dates as s4_1, created later: the view's dict keeps the last row of the
    # '-creation_date' order for a key, so s4_1 wins.
    's4_dup': sub(FOUR, status=4, start=day(-60), end=day(300), created='2023-05-01T10:00:00Z', associate='a4_1', user='owner_four'),
    's4_arch': sub(FOUR, status=4, start=day(-400), end=day(400), created='2023-06-01T10:00:00Z', associate='a4_1', archived=True,
                   user='owner_four'),
    's4_2': sub(FOUR, status=4, start=day(-60), end=day(-20), created='2023-02-01T10:00:00Z', associate='a4_2', user='owner_four'),
    's4_3': sub(FOUR, status=4, start=day(-19), end=day(100), created='2023-04-01T10:00:00Z', associate='a4_2', user='owner_four'),
    # end_date is compared with payment_date (a timestamp) in SQL: a payment at 10:00 on the end day is outside.
    's4_5': sub(FOUR, status=4, start=day(-30), end=day(-2), created='2023-07-01T10:00:00Z', associate='a4_4', user='owner_four'),
}
for label in FOUR_SUBSCRIPTIONS:
    rid(label)
SUBSCRIPTIONS.update(FOUR_SUBSCRIPTIONS)
PAYMENT_CATEGORIES = {'cat4': {'association': FOUR, 'name': 'Quota extra'}}
for label in PAYMENT_CATEGORIES:
    rid(label)


def unpaid(amount, associate, created, *, paid_on=None, subject=0, **extra):
    row = pay(amount, association=FOUR, paid=False, subject=subject, created=created, paid_on=paid_on)
    row.update({'associate': associate, **extra})
    return row


FOUR_PAYMENTS = {
    'e1': unpaid('12.50', 'a4_1', day(-3, '10:00:00'), paid_on=day(-3, '10:00:00'), subject=2, meta={'description': 'Rata judo è'}),
    'e2': unpaid('30.00', 'a4_1', day(-10, '09:00:00'), subject=1),
    'e3': unpaid('20.00', 'a4_2', day(-5, '11:00:00'), paid_on=day(-30, '12:00:00'), subject=2),
    'e4': unpaid('7.00', 'a4_2', day(2, '08:00:00'), category='cat4'),
    'e5': unpaid('15.00', 'a4_1', day(-7, '10:00:00'), subject=2, course={'label': 'Corso estivo'}, description='luglio'),
    'e6': unpaid('5.00', 'a4_1', day(-12, '10:00:00'), subject=2),
    'e7': unpaid('3.00', 'a4_1', day(-15, '10:00:00')),
    'e8': unpaid('9.00', 'a4_3', day(-4, '10:00:00')),
    'e9': unpaid('4.00', 'a4_4', day(-6, '10:00:00'), paid_on=day(-2, '10:00:00')),
    # Excluded by the payment filters.
    'pp4': {**unpaid('1.00', 'a4_2', OLD, paid_on=day(-1, '10:00:00')), 'paid': True},
    'x_paid': {**unpaid('8.00', 'a4_1', day(-2, '10:00:00')), 'paid': True},
    'x_archived': {**unpaid('8.00', 'a4_1', day(-2, '10:00:00')), 'archived': True},
    'x_expense': {**unpaid('8.00', 'a4_1', day(-2, '10:00:00')), 'expense': True},
    'x_zero': unpaid('0.00', 'a4_1', day(-2, '10:00:00')),
    'x_deleted': {**unpaid('8.00', 'a4_1', day(-2, '10:00:00')), 'deleted': True},
    'x_old': unpaid('8.00', 'a4_1', day(-40, '10:00:00')),
    'x_future': unpaid('8.00', 'a4_1', day(9, '10:00:00')),
    'x_no_associate': unpaid('8.00', None, day(-2, '10:00:00')),
}
for label in FOUR_PAYMENTS:
    rid(label)
PAYMENTS.update(FOUR_PAYMENTS)
FOUR_COURSES = {'k4c1': course('Judo', association=FOUR), 'k4c2': course('Karate', association=FOUR)}
for label in FOUR_COURSES:
    rid(label)
COURSES.update(FOUR_COURSES)
cs('cs4_1', 'k4c1', subscription='s4_1')
cs('cs4_del', 'k4c1', subscription='s4_1', deleted=True)
cs('cs4_2', 'k4c2', subscription='s4_3')
COURSE_SUBSCRIPTIONS['cs4_1']['payment'] = 'e1'
COURSE_SUBSCRIPTIONS['cs4_2']['payment'] = 'e3'
CARNETS['kc4'] = {'association': FOUR, 'title': 'Carnet judo', 'description': None, 'lessons': 8, 'fee': '40.00', 'public': True}
rid('kc4')
CARNET_SUBSCRIPTIONS['ck4_four'] = {'subscription': 's4_1', 'carnet': 'kc4', 'meta': {'lessons_left': 10}, 'payment': 'e6',
                               'created': '2026-09-01T10:00:00.123456Z'}
rid('ck4_four')

INSTRUCTORS = {
    'ins_c': {'owner': 'owner_four', 'first': 'Carla', 'last': 'Rossi', 'associated_user': 'collab_four'},
    'ins_x': {'owner': 'owner_four', 'first': 'Dario', 'last': 'Neri'},
    'ins_f': {'owner': IDS['foreign_owner'], 'first': 'Elena', 'last': 'Estera'},
}
for label in INSTRUCTORS:
    rid(label)
for label in ('ev4a', 'ev4b', 'ev4c', 'ev4d', 'ev4e', 'ev4h', 'ev4z'):
    rid(label)


def event4(label, start, *, title='Lezione', **extra):
    item = {'event_id': R[label], 'title': title, 'start': {'$fmt': [start, MS]}, 'end': {'$fmt': [start, MS]}}
    item.update(extra)
    return item


INSTRUCTOR_C_UPPER = R['ins_c'].upper()  # events__iregex matches it case-insensitively
EV4A = event4('ev4a', day(0, '09:00:00'), allDay=False,
              extendedProps={'instructor': {'instructor_id': INSTRUCTOR_C_UPPER}, 'location': 'Palestra'})
EV4B = event4('ev4b', day(0, '18:00:00'), title='Serale', allDay=True, extendedProps={'instructor': [
    {'instructor_id': R['ins_x']}, {'instructor_id': R['ins_f']}, {'name': 'esterno'}]})
EV4C = event4('ev4c', day(-1, '22:30:00'))
EV4D = event4('ev4d', day(-1, '21:00:00'))
EV4E = event4('ev4e', day(1, '01:30:00'))
EV4H = event4('ev4h', day(1, '03:00:00'))
EV4I = {'title': 'Senza id', 'start': {'$fmt': [day(0, '15:00:00'), MS]}, 'end': None, 'extendedProps': {'note': 'libera'}}
REGISTRIES['rg4'] = {'course': 'k4c1', 'events': [EV4C, EV4D, EV4A, EV4B, EV4E, EV4H]}
REGISTRIES['rg4b'] = {'course': 'k4c2', 'events': [EV4I]}
rid('rg4')
rid('rg4b')
FOUR_DAYS = {
    'd4c': {'registry': 'rg4', 'date': day(-1, '22:30:00'), 'title': 'Lezione', 'event': 'ev4c'},
    'd4d': {'registry': 'rg4', 'date': day(-1, '21:00:00'), 'title': 'Lezione', 'event': 'ev4d'},
    'd4a': {'registry': 'rg4', 'date': day(0, '09:00:00'), 'title': 'Lezione', 'event': 'ev4a', 'attendees': ['cs4_1'],
            'absences': ['cs4_2']},
    'd4i': {'registry': 'rg4b', 'date': day(0, '15:00:00'), 'title': 'Senza id'},
    # Unlinked: the first request links it to ev4b and saves the day (expected_absences None -> []).
    'd4b': {'registry': 'rg4', 'date': day(0, '18:00:00'), 'title': 'Serale'},
    # Linked to an event the registry does not list: no event data.
    'd4e': {'registry': 'rg4', 'date': day(1, '01:30:00'), 'title': 'Notturna', 'event': 'ev4z'},
    'd4h': {'registry': 'rg4', 'date': day(1, '03:00:00'), 'title': 'Lezione', 'event': 'ev4h'},
}
for label in FOUR_DAYS:
    rid(label)
DAYS.update(FOUR_DAYS)

# ===========================================================================
# Oracle (statistic_views.py read line by line).
# ===========================================================================


def visible_subscriptions(association, group=None):
    """Subscription.objects: group-aware, soft-deleted rows are NOT excluded (none are seeded deleted)."""
    return {k: v for k, v in SUBSCRIPTIONS.items() if v['association'] == association and (group is None or v['group'] == group)}


def live_payments(association, group=None):
    return {k: v for k, v in PAYMENTS.items() if v['association'] == association and not v['deleted']
            and (group is None or v['group'] == group)}


def is_static(spec):
    return isinstance(spec, str)


def days_of(spec):
    """Relative day of a {'day': k, ...} spec (None for absolute/now specs)."""
    return spec['day'] if isinstance(spec, dict) and 'day' in spec else None


def widget_subscriptions(association, group=None):
    counts = Counter()
    for row in visible_subscriptions(association, group).values():
        if row['status'] in (1, 2, 3, 4) and not row['archived'] and row['type'] in (2, 3) and row['start'] is not None \
                and row['end'] is not None and days_of(row['start']) <= 0 <= days_of(row['end']):
            counts[row['status']] += 1
    pie = [counts[1], counts[2], counts[3], counts[4]]
    return {'data': {'pie_subscriptions': pie, 'total_subscriptions': sum(pie)}}


def in_last(spec, span):
    """creation_date in [now - span days, now]: rows sit at days -span+1 .. 0 (00:00 for day 0)."""
    k = days_of(spec)
    if k is None:
        assert is_static(spec) and spec < '2025-09', spec  # at least 400 days before any run day
        return None
    if k < -span:
        return None
    assert -span < k <= 0 and (k < 0 or spec['at'] == '00:00:00'), spec
    return span + k


def python_sum(values):
    """CPython >= 3.12 sum(): Neumaier-compensated for floats, starting from int 0."""
    total, compensation, started = 0, 0.0, False
    for value in values:
        if not started:
            total, started = total + value, True
            continue
        t = total + value
        compensation += (total - t) + value if abs(total) >= abs(value) else (value - t) + total
        total = t
    return total + compensation if compensation else total


def widget_payments(association, group=None):
    daily = {}
    for row in live_payments(association, group).values():
        if Decimal(row['amount']) > 0 and row['paid']:
            index = in_last(row['created'], 30)
            if index is not None:
                daily[index] = daily.get(index, Decimal(0)) + Decimal(row['amount'])
    series = [float(daily.get(i, 0)) for i in range(31)]
    return {'data': {'current_month_payments': series, 'total_payments': python_sum(series)}}


def widget_associates(association, group=None):
    daily = Counter()
    for row in visible_subscriptions(association, group).values():
        index = in_last(row['created'], 30)
        if index is not None:
            daily[index] += 1
    series = [daily[i] for i in range(31)]
    return {'data': {'current_month_associates': series, 'total_associates': sum(series)}}


def widget_bestcourses(association):
    active = {k: v for k, v in COURSES.items() if v['association'] == association and v['status'] == 2 and not v['deleted']}
    # Count('coursesubscription') joins every course subscription, soft-deleted ones included.
    counted = [(sum(1 for c in COURSE_SUBSCRIPTIONS.values() if c['course'] == k), k) for k in active]
    counted.sort(key=lambda item: -item[0])
    assert len(counted) < 4 or counted[2][0] > counted[3][0], 'top three must be unambiguous'
    best = [{'course_id': R[k], 'title': COURSES[k]['title'], 'description': COURSES[k]['description'], 'subscriptions': n}
            for n, k in counted[:3]]
    assert len({n for n, _ in counted[:3]}) == len(counted[:3]), 'top three must be strictly ordered'
    own_cs = {k: v for k, v in COURSE_SUBSCRIPTIONS.items() if COURSES[v['course']]['association'] == association and not v['deleted']}
    daily = Counter()
    for row in own_cs.values():
        index = in_last(row['created'], 7)
        if index is not None:
            daily[index] += 1
    return {'data': {'current_week_course_associates': [daily[i] for i in range(8)], 'total_course_associates': len(own_cs),
                     'best_courses': best}}


def widget_toapprove(association):
    rows = [(k, v) for k, v in visible_subscriptions(association).items() if v['status'] in (1, 2) and not v['archived']
            and v['start'] is not None and v['end'] is not None and days_of(v['start']) <= 0 <= days_of(v['end'])]
    rows.sort(key=lambda kv: (days_of(kv[1]['created']), kv[1]['created']['at'], kv[1]['status']), reverse=True)
    keys = [(days_of(v['created']), v['created']['at'], v['status']) for _, v in rows]
    assert len(set(keys)) == len(keys), 'order must be total'
    return {'data': {'subscriptions_to_approve': [{
        'creation_date': {'$drf': v['created']}, 'subscription_id': R[k], 'status': v['status'],
        'associate': person(v['associate'])} for k, v in rows]}}


def person(label):
    if label is None:
        return {'associate_id': None, 'first_name': None, 'last_name': None}
    row = ASSOCIATES[label]
    return {'associate_id': R[label], 'first_name': row['first'], 'last_name': row['last']}


def widget_medical(association, expiring):
    # Every candidate row is created at T 00:00 (inside the current balance-sheet year) or before 2024 (outside).
    out = []
    for k, v in visible_subscriptions(association).items():
        if v['medical'] is None or MEDICALS[v['medical']] is None:
            continue
        if v['created'] != MIDNIGHT:
            assert is_static(v['created']) and v['created'] < '2024', k
            continue
        left = days_of(MEDICALS[v['medical']])
        if expiring and (v['archived'] or not 0 <= left <= 30):
            continue
        if not expiring and left >= 0:
            continue
        out.append({'subscription_id': R[k], 'days_left': left, 'associate': person(v['associate']),
                    'medical': {'expiration_date': {'$date': MEDICALS[v['medical']]}}})
    out.sort(key=lambda item: item['days_left'])
    assert len({item['days_left'] for item in out}) == len(out)
    key = 'expiring_medical_certificates' if expiring else 'expired_medical_certificates'
    return {'data': {key: out}}


def widget_carnets(association):
    out = []
    for k, v in CARNET_SUBSCRIPTIONS.items():
        s = SUBSCRIPTIONS[v['subscription']]
        if s['association'] != association:
            continue
        meta = v['meta']
        if not isinstance(meta, dict) or 'lessons_left' not in meta:
            continue
        left = meta['lessons_left']
        # jsonb ordering: null < string < number < boolean, so < 4 keeps null, strings and numbers below 4.
        if isinstance(left, bool) or (isinstance(left, (int, float)) and left >= 4):
            continue
        if left == 0 and not isinstance(left, (str, type(None))) and 'lessons_registry' not in meta:
            continue
        if left is None or not isinstance(left, (int, float)):
            continue
        if left == 0:
            registry = meta.get('lessons_registry', [])
            recent = False
            for lesson in registry:
                if isinstance(lesson, dict) and 'date' in lesson:
                    if lesson['date'] == 'garbage':
                        recent = None  # strptime ValueError: the carnet is skipped
                        break
                    if lesson['date']['$fmt'][0]['now'] > -30 * 86400:
                        recent = True
                        break
            if not recent:
                continue
        carnet = CARNETS[v['carnet']]
        out.append({'carnet_subscription_id': R[k], 'subscription': {'subscription_id': R[v['subscription']], 'associate': person(s['associate'])},
                    'carnet': {'carnet_id': R[v['carnet']], 'title': carnet['title'], 'description': carnet['description'],
                               'lessons_number': carnet['lessons'], 'fee': float(Decimal(carnet['fee'])),
                               'creation_date': v['created'], 'public': carnet['public'], 'meta': meta}})
    out.sort(key=lambda item: item['carnet']['meta'].get('lessons_left', 0))
    assert len({item['carnet']['meta']['lessons_left'] for item in out}) == len(out)
    return {'data': {'expiring_carnets': out}}


# --- statistic/report for an absolute balance-sheet year [start, next start - 1 day at 00:00] -----------------

MONTHS = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']


def months_from(start_month):
    return MONTHS[start_month - 1:] + MONTHS[:start_month - 1], [(start_month - 1 + i) % 12 + 1 for i in range(12)]


def in_year(spec, year):
    if not is_static(spec):
        return False  # every relative row is after 2026-01-01, outside the authored years
    return f'{year}-09-01T00:00:00Z' <= spec <= f'{year + 1}-08-31T00:00:00Z'


def money(value):
    return float(value)


def report(association, year, group=None, available=None, impersonated=False):
    """impersonated: scoped_queryset keeps course subscriptions whose subscription is in the association."""
    names, numbers = months_from(9)
    subs = visible_subscriptions(association, group)
    by_month, approved = Counter(), Counter()
    for v in subs.values():
        if not v['archived'] and in_year(v['created'], year):
            month = int(v['created'][5:7])
            by_month[month] += 1
            if v['status'] == 4:
                approved[month] += 1
    for counter in (by_month, approved):
        for month in counter:  # every authored row of a month falls in the same calendar year
            assert len({v['created'][:7] for v in subs.values() if not v['archived'] and in_year(v['created'], year)
                        and int(v['created'][5:7]) == month}) == 1
    y = [by_month[m] for m in numbers]
    y_approved = [approved[m] for m in numbers]
    courses = [k for k, v in COURSES.items() if v['association'] == association and v['status'] == 2 and not v['deleted']
               and (group is None or v['group'] == group) and in_year(v['created'], year)]
    titles = {}
    for c in COURSE_SUBSCRIPTIONS.values():
        if c['course'] in courses and not c['deleted'] and (not impersonated or (
                c['subscription'] is not None and SUBSCRIPTIONS[c['subscription']]['association'] == association)):
            entry = titles.setdefault(COURSES[c['course']]['title'], [0, Decimal(0)])
            entry[0] += 1
            entry[1] += Decimal(COURSES[c['course']]['fee'])
    assert len(titles) <= 1, 'group order of several titles is unspecified'
    x_courses, y_courses, revenue = [''] * len(courses), [0] * len(courses), [0] * len(courses)
    for idx, (title, (count, total)) in enumerate(titles.items()):
        x_courses[idx], y_courses[idx], revenue[idx] = title, count, money(total)
    pie = [0, 0, 0, 0]
    statuses = Counter(v['status'] for v in subs.values() if v['status'] <= 4)
    for status in sorted(statuses):
        pie[status - 1] = statuses[status]
    pays = live_payments(association, group)

    def in_range(v):
        return in_year(v['payment_date'], year) if v['payment_date'] is not None else in_year(v['created'], year)

    pay_months = {}
    for v in sorted((v for v in pays.values() if not v['archived'] and not v['expense'] and v['paid'] and in_range(v)),
                    key=lambda v: v['created'][:7]):
        if True:
            pay_months.setdefault(v['created'][:7], Decimal(0))
            pay_months[v['created'][:7]] += Decimal(v['amount'])
    by_number = {}
    for key in sorted(pay_months):  # ORDER BY month: a later year overwrites the same month number
        by_number[int(key[5:7])] = pay_months[key]
    pay_y = [money(by_number[m]) if m in by_number else 0 for m in numbers]
    paid = [Decimal(v['amount']) for v in pays.values() if not v['archived'] and not v['expense'] and in_range(v) and v['paid']]
    unpaid = [Decimal(v['amount']) for v in pays.values() if not v['archived'] and not v['expense'] and in_range(v) and not v['paid']]
    accepted = [v for v in subs.values() if not v['archived'] and v['status'] == 4]
    ages = [ASSOCIATES[v['associate']]['age'] for v in accepted]
    sexes = Counter(ASSOCIATES[v['associate']]['sex'] for v in accepted)

    def total(subject):
        values = [Decimal(v['amount']) for v in pays.values() if not v['archived'] and v['subject'] == subject and v['paid'] and in_range(v)]
        return money(sum(values)) if values else None

    from fractions import Fraction
    average_month = Fraction(sum(y_approved), 12)
    average_age = Fraction(sum(ages), len(ages)) if ages else 0
    invoices = sum(1 for v in INVOICES.values() if association == OWN and group is None and not v.get('archived')
                   and not v.get('deleted') and in_year(v['created'], year))
    return {'data': {
        'available_years': available,
        'subscriptions': {'x': names, 'y': y},
        'kpi_subscriptions': {'average_subscriptions_month': '{:.2f}'.format(float(average_month)),
                              'total_subscriptions': sum(y_approved), 'average_age': '{:.2f}'.format(float(average_age)),
                              'female': sexes['F'], 'male': sexes['M'], 'other': sexes['A']},
        'kpi_payments': {'total_subscriptions': total(1), 'total_payments': sum(1 for v in pays.values() if not v['archived'] and in_range(v)),
                         'total_activities': total(2), 'total_invoices': invoices},
        'pie_subscriptions': {'y': pie, 'x': ['Non firmata', 'In attesa', 'Rifiutate', 'Accettate']},
        'payments': {'x': names, 'y': pay_y},
        'pie_payments': {'y': [money(sum(paid)) if paid else 0, money(sum(unpaid)) if unpaid else 0], 'x': ['Pagato', 'Non pagato']},
        'courses': {'y': y_courses, 'x': x_courses, 'revenue': {'y': revenue}},
    }}


def foreign_default_report():
    """The foreign owner (1 January year) with the current date: rows at T 00:00 only."""
    names, _ = months_from(1)
    return {'data': {
        'available_years': {'$available_years': [1, 1]},
        'subscriptions': {'x': names, 'y': {'$month_series': {'start_month': 1, 'value': 1}}},
        'kpi_subscriptions': {'average_subscriptions_month': '0.00', 'total_subscriptions': 0, 'average_age': '0.00',
                              'female': 0, 'male': 0, 'other': 0},
        'kpi_payments': {'total_subscriptions': 50.0, 'total_payments': 1, 'total_activities': None, 'total_invoices': 0},
        'pie_subscriptions': {'y': [0, 1, 0, 0], 'x': ['Non firmata', 'In attesa', 'Rifiutate', 'Accettate']},
        'payments': {'x': names, 'y': {'$month_series': {'start_month': 1, 'value': 50.0}}},
        'pie_payments': {'y': [50.0, 0], 'x': ['Pagato', 'Non pagato']},
        'courses': {'y': [], 'x': [], 'revenue': {'y': []}},
    }}


# --- statistic/dashboard?widget=todaylessons ------------------------------------------------------------------
# Days whose date is in [today 00:00 - 121 min, today 23:59:59 + 121 min] (naive datetime.now(), UTC) of the
# association's courses, ordered by date, each rendered by get_extended_prop_for_attendance_day: the event of the
# registry starting in the same hour (and, for a linked day, with the linked event_id) provides allDay, start, end
# and extendedProps (instructor labels from the course owner's instructors). Its subscription map is keyed by UUID
# and looked up by str, so attendees keep their stored shape and expected_absences is always [].


def potential(course_label):
    out = []
    for label, row in COURSE_SUBSCRIPTIONS.items():
        if row['course'] != course_label or row['deleted']:
            continue
        a = ASSOCIATES[SUBSCRIPTIONS[row['subscription']]['associate']]
        assert a['born'] is None  # calculate_age() is 0: the exemption label with 0 days
        out.append({'course_subscription_id': R[label], 'subscription': {'associate': {
            'first_name': a['first'], 'last_name': a['last'], 'tax_code': a.get('tax'),
            'medical_label': '<span class="text-success font-weight-boldest">Esente per 0 giorni</span>'}}})
    assert len(out) <= 1, 'CourseSubscription.objects.filter(course=...) has no order: keep one member per course'
    return out


def today_lesson(day_label, event=None, props=None):
    row = DAYS[day_label]
    registry = REGISTRIES[row['registry']]
    attendees = [{'course_subscription_id': R[cs]} for cs in row.get('attendees', [])]
    absences = row.get('absences', [])
    members = potential(registry['course'])
    return {'title': row['title'], 'allDay': event.get('allDay', False) if event else False,
            'extended_props': props if event else {}, 'attendance_day_id': R[day_label],
            'attendance_registry': R[row['registry']], 'date': {'$drf': row['date']},
            'start': event.get('start') if event else None, 'end': event.get('end') if event else None,
            'course_id': R[registry['course']], 'total_attendees': len(attendees), 'total_expected_absences': len(absences),
            'potential_attendees': members, 'total_expected_total': len(members) - len(absences), 'attendees': attendees,
            'expected_absences': []}


PROPS_4A = {'instructor': {'instructor_id': INSTRUCTOR_C_UPPER, 'label': 'Carla Rossi'}, 'location': 'Palestra'}
PROPS_4B = {'instructor': [{'instructor_id': R['ins_x'], 'label': 'Dario Neri'}, {'instructor_id': R['ins_f']}, {'name': 'esterno'}]}
LESSONS_4C = today_lesson('d4c', EV4C, {})
LESSONS_4A = today_lesson('d4a', EV4A, PROPS_4A)
LESSONS_4I = today_lesson('d4i', EV4I, {'note': 'libera'})
LESSONS_4B = today_lesson('d4b', EV4B, PROPS_4B)
LESSONS_4E = today_lesson('d4e')
# The owner: every day of the association in the window. The collaborator is ins_c's associated user,
# so only days of registries whose events mention ins_c (rg4) are listed.
TODAY_OWNER_FOUR = {'data': {'lessons': [LESSONS_4C, LESSONS_4A, LESSONS_4I, LESSONS_4B, LESSONS_4E]}}
TODAY_COLLAB_FOUR = {'data': {'lessons': [LESSONS_4C, LESSONS_4A, LESSONS_4B, LESSONS_4E]}}
# The first request links d4b to ev4b: auditlog's update entry for the save of the whole day.
TODAY_LINK = {'day': R['d4b'], 'associated_event': R['ev4b'], 'attendees': None, 'expected_absences': [],
              'changes': {'associated_event': ['None', R['ev4b']], 'expected_absences': ['null', '[]']},
              'actor': 'owner_four', 'association': R[FOUR], 'path': 'attendance_registry.course.sport_association'}


# --- statistic/dashboard?widget=expiredPayments ---------------------------------------------------------------

def payment_title(label):
    row = PAYMENTS[label]
    if row['subject'] == 2:
        for cs_label, cs_row in COURSE_SUBSCRIPTIONS.items():
            if cs_row.get('payment') == label:
                return (row.get('meta') or {}).get('description') or COURSES[cs_row['course']]['title']
        for ck_label, ck_row in CARNET_SUBSCRIPTIONS.items():
            if ck_row.get('payment') == label:
                return (row.get('meta') or {}).get('description') or CARNETS[ck_row['carnet']]['title']
        if row.get('course') and 'label' in row['course']:
            return ', '.join([row['course']['label'], row.get('description') or ''])
        return row.get('description')
    if row['subject'] == 1:
        return 'Iscrizione'
    return PAYMENT_CATEGORIES[row['category']]['name'] if row.get('category') else None


def expired_payments(association):
    """The unpaid payments created in [now - 30 days, now + 7 days] with a matching subscription."""
    chosen = []
    for label, row in PAYMENTS.items():
        if row['association'] != association or row['paid'] or row['archived'] or row['deleted'] or row['expense'] \
                or row.get('associate') is None or Decimal(row['amount']) <= 0:
            continue
        k = days_of(row['created'])
        if k is None or not -30 < k < 7:
            assert k is None or k <= -31 or k >= 8, label
            continue
        chosen.append(label)
    # Subscriptions with some payment of the same associate whose payment_date (a timestamp) lies in
    # [start_date 00:00, end_date 00:00]; the dict keeps, per (associate, start, end), the last row of the
    # '-creation_date' order and its first insertion position.
    associates = {PAYMENTS[label]['associate'] for label in chosen}
    rows = []
    for label, s in SUBSCRIPTIONS.items():
        if s['association'] != association or s['archived'] or s['associate'] not in associates or s['start'] is None:
            continue
        for p in PAYMENTS.values():
            at = p['payment_date']
            if p.get('associate') == s['associate'] and at is not None:
                moment = (days_of(at), at['at'])
                assert at['at'] != '00:00:00'
                if days_of(s['start']) <= moment[0] < days_of(s['end']):
                    rows.append(label)
    assert all(is_static(SUBSCRIPTIONS[label]['created']) for label in rows)
    rows.sort(key=lambda label: SUBSCRIPTIONS[label]['created'], reverse=True)
    mapping = {}
    for label in rows:
        s = SUBSCRIPTIONS[label]
        mapping[(s['associate'], days_of(s['start']), days_of(s['end']))] = label
    out = []
    for label in sorted(chosen, key=lambda item: (days_of(PAYMENTS[item]['created']), PAYMENTS[item]['created']['at']), reverse=True):
        row = PAYMENTS[label]
        effective = row['payment_date'] or row['created']
        match = next((sub_label for (associate, start, end), sub_label in mapping.items()
                      if associate == row['associate'] and start <= days_of(effective) <= end), None)
        if match is None:
            continue
        out.append({'payment_id': R[label], 'expired_days': -days_of(row['created']), 'amount': float(Decimal(row['amount'])),
                    'payment_date': {'$drf': row['payment_date']} if row['payment_date'] else None,
                    'creation_date': {'$drf': row['created']}, 'title': payment_title(label), 'date': {'$dmy': effective},
                    'associate': person(row['associate']), 'subscription_id': R[match]})
    out.sort(key=lambda item: item['expired_days'])
    assert len({item['expired_days'] for item in out}) == len(out)
    return {'data': {'expired_payments': out}}


# --- statistic/athlete-dashboard -----------------------------------------------------------------------------

def lesson(event_id, title, start, day_label, cs_label, sub_label, course_label, *, present=False, absent=False):
    s = SUBSCRIPTIONS[sub_label]
    return {'event_id': event_id, 'is_absent': absent, 'is_present': present, 'attendance_day_id': R[day_label], 'title': title,
            'start': {'$dmy': start}, 'course_subscription_id': R[cs_label], 'course_id': R[course_label],
            'course_title': COURSES[course_label]['title'],
            'subscription': {'subscription_id': R[sub_label], 'associate': person(s['associate'])}}


OWNER_USER = {'user_id': OWNER, 'avatar_image': None, 'first_name': 'Message', 'last_name': 'Fixture', 'username': 'message-reads-700001'}
COMMUNICATIONS = [{'message': MESSAGES['msg_post']['text'], 'sent_on': {'$drf': DELIVERIES['mt1']['sent']}},
                  {'message': MESSAGES['msg_foreign_post']['text'], 'sent_on': {'$drf': DELIVERIES['mt5']['sent']}},
                  {'message': MESSAGES['msg_post']['text'], 'sent_on': {'$drf': DELIVERIES['mt2']['sent']}}]


def athlete_body(active, lessons):
    return {'data': [{'sport_association': {'denomination': 'Message reads own', 'sport_association_id': OWN, 'user': OWNER_USER,
                                            'review_url': 'https://example.test/recensioni', 'review_url_enabled': True,
                                            'communications': COMMUNICATIONS},
                      'subscriptions': active}], 'upcoming_lessons': lessons}


ATHLETE_LESSONS = [
    lesson('ev-9', 'Basket mattina', day(1), 'd9', 'cs_a2', 'sub_a2', 'c2'),
    lesson('ev-1', 'Allenamento', day(2), 'd1', 'cs_a1', 'sub_a1', 'c1', present=True),
    lesson('ev-2', 'Partita è', day(4), 'd2', 'cs_a1', 'sub_a1', 'c1', absent=True),
]
ATHLETE_BODY = athlete_body(2, ATHLETE_LESSONS)
# An association-scoped session (impersonation_association) narrows every queryset through
# scoped_queryset: family members come from Associate.objects, which hides the soft-deleted as_a2,
# so its subscription (and lesson) drop out; it was not active, so the count is unchanged.
ATHLETE_SCOPED_BODY = athlete_body(2, ATHLETE_LESSONS[1:])

# ===========================================================================
# Cases
# ===========================================================================
cases = []
D, A, P = '/statistic/dashboard', '/statistic/athlete-dashboard', '/statistic/report'
OPTIONS_PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']


def add(name, actor, path, status, response, *, method='GET', headers=None, setup=None, link=None):
    case = {'name': f'statistic-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response}
    if setup:
        case['setup'] = setup
    if link:
        case['link'] = link
    cases.append(case)


def w(widget):
    return f'{D}?widget={widget}'


IMPERSONATE_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
GROUP_HEADER = {'X-Group-Id': GROUP}

# statistic/dashboard: every ported widget for the owner.
add('subscriptions-owner', 'owner', w('subscriptions'), 200, widget_subscriptions(OWN))
add('subscriptions-group', 'owner', w('subscriptions'), 200, widget_subscriptions(OWN, GROUP), headers=GROUP_HEADER)
add('subscriptions-invalid-group-ignored', 'owner', w('subscriptions'), 200, widget_subscriptions(OWN), headers={'X-Group-Id': 'not-a-uuid'})
add('subscriptions-foreign-owner', 'foreign_owner', w('subscriptions'), 200, widget_subscriptions(FOREIGN))
add('subscriptions-full-collaborator', 'full_collaborator', w('subscriptions'), 200, widget_subscriptions(OWN))
add('subscriptions-admin-impersonating-owner', 'admin', w('subscriptions'), 200, widget_subscriptions(OWN), headers=IMPERSONATE_OWNER)
add('subscriptions-owner-impersonating-collaborator', 'owner', w('subscriptions'), 200, widget_subscriptions(OWN),
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})
add('payments-owner', 'owner', w('payments'), 200, widget_payments(OWN))
add('payments-group', 'owner', w('payments'), 200, widget_payments(OWN, GROUP), headers=GROUP_HEADER)
add('payments-foreign-owner', 'foreign_owner', w('payments'), 200, widget_payments(FOREIGN))
add('associates-owner', 'owner', w('associates'), 200, widget_associates(OWN))
add('associates-group', 'owner', w('associates'), 200, widget_associates(OWN, GROUP), headers=GROUP_HEADER)
add('bestcourses-owner', 'owner', w('bestcourses'), 200, widget_bestcourses(OWN))
add('bestcourses-foreign-owner', 'foreign_owner', w('bestcourses'), 200, widget_bestcourses(FOREIGN))
add('expiringcarnets-owner', 'owner', w('expiringcarnets'), 200, widget_carnets(OWN))
add('expiringcarnets-missing-associate', 'owner_three', w('expiringcarnets'), 500, SERVER)
add('expiringmedical-owner', 'owner', w('expiringmedicalcertificates'), 200, widget_medical(OWN, True))
add('expiredmedical-owner', 'owner', w('expiredmedicalcertificates'), 200, widget_medical(OWN, False))
add('expiringmedical-foreign-owner', 'foreign_owner', w('expiringmedicalcertificates'), 200, {'data': {'expiring_medical_certificates': []}})
add('toapprove-owner', 'owner', w('subscriptionstoapprove'), 200, widget_toapprove(OWN))
add('income-owner', 'owner', w('incomeAndExpenses'), 200, {'data': {'$income': [
    {'amount': v['amount'], 'expense': v['expense'], 'at': v['payment_date']} for k, v in live_payments(OWN).items()
    if v['paid'] and not v['archived'] and v['payment_date'] is not None and not is_static(v['payment_date'])]}})
add('income-group', 'owner', w('incomeAndExpenses'), 200, {'data': {'$income': [
    {'amount': v['amount'], 'expense': v['expense'], 'at': v['payment_date']} for k, v in live_payments(OWN, GROUP).items()
    if v['paid'] and not v['archived'] and v['payment_date'] is not None and not is_static(v['payment_date'])]}}, headers=GROUP_HEADER)
add('income-foreign-owner-empty', 'foreign_owner', w('incomeAndExpenses'), 200, {'data': {
    'today_income': None, 'today_expenses': None, 'last_month_income': None, 'last_month_expenses': None,
    'total_income': None, 'total_expenses': None}})
# todaylessons: the first request links the unlinked day (a write during the GET); later ones read it linked.
add('todaylessons-links-unlinked-day', 'owner_four', w('todaylessons'), 200, TODAY_OWNER_FOUR, link=TODAY_LINK)
add('todaylessons-owner', 'owner_four', w('todaylessons'), 200, TODAY_OWNER_FOUR)
add('todaylessons-instructor-collaborator', 'collab_four', w('todaylessons'), 200, TODAY_COLLAB_FOUR)
add('todaylessons-other-association-empty', 'owner', w('todaylessons'), 200, {'data': {'lessons': []}})
add('expiredpayments-owner', 'owner_four', w('expiredPayments'), 200, expired_payments(FOUR))
add('expiredpayments-collaborator', 'collab_four', w('expiredPayments'), 200, expired_payments(FOUR))
add('expiredpayments-group-empty', 'owner_four', w('expiredPayments'), 200, {'data': {'expired_payments': []}}, headers=GROUP_HEADER)
add('expiredpayments-other-association-empty', 'owner', w('expiredPayments'), 200, expired_payments(OWN))
add('widget-expiredpayments-lowercase', 'owner_four', w('expiredpayments'), 404, ['error: widget not found.'])
# The widget parameter: QueryDict.get keeps the last value, matching is exact.
add('widget-missing', 'owner', D, 400, ['error: widget is required.'])
add('widget-missing-extra-parameter', 'owner', D + '?widgets=payments', 400, ['error: widget is required.'])
add('widget-empty', 'owner', w(''), 404, ['error: widget not found.'])
add('widget-unknown', 'owner', w('calendar'), 404, ['error: widget not found.'])
add('widget-case-sensitive', 'owner', w('Subscriptions'), 404, ['error: widget not found.'])
add('widget-income-lowercase', 'owner', w('incomeandexpenses'), 404, ['error: widget not found.'])
add('widget-last-value-wins', 'owner', D + '?widget=calendar&widget=subscriptions', 200, widget_subscriptions(OWN))
add('widget-encoded', 'owner', D + '?widget=sub%73criptions', 200, widget_subscriptions(OWN))
# The association lookup precedes the widget checks.
add('dashboard-athlete-server-error', 'athlete', w('subscriptions'), 500, SERVER)
add('dashboard-no-association-before-widget', 'owner_without_association', D, 500, SERVER)
add('dashboard-deleted-association', 'deleted_association_owner', w('subscriptions'), 500, SERVER)
add('dashboard-restricted', 'restricted_collaborator', w('subscriptions'), 403, {'detail': 'Missing permission: association.dashboard.read'})
add('dashboard-restricted-post', 'restricted_collaborator', w('subscriptions'), 403, {'detail': 'Missing permission: association.dashboard.read'},
    method='POST')
add('dashboard-owner-scoped-to-athlete', 'owner', w('subscriptions'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE})
add('dashboard-admin-without-session', 'admin', w('subscriptions'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('dashboard-anonymous', 'anonymous', w('subscriptions'), 401, {'detail': 'Authentication credentials were not provided.'})
add('dashboard-wrong-method', 'owner', w('subscriptions'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('dashboard-options', 'owner', D, 200, {'name': 'Statistic Dashboard', 'description': '', 'renders': ['application/json'],
                                           'parses': OPTIONS_PARSES}, method='OPTIONS')

# statistic/athlete-dashboard
add('athlete', 'athlete', A, 200, ATHLETE_BODY)
add('athlete-admin-impersonating', 'admin', A, 200, ATHLETE_BODY, headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': ATHLETE})
add('athlete-owner-impersonating', 'owner', A, 200, ATHLETE_SCOPED_BODY, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE})
add('athlete-without-subscriptions', 'athlete_two', A, 200, athlete_body(0, []))
add('athlete-invalid-event-json-error', 'athlete_broken', A, 500, {'error': 'An error occurred while loading the dashboard'})
add('athlete-no-primary-association', 'athlete', A, 503, {'error': 'Primary sport association is not configured.'},
    setup={'primary_association': None})
add('athlete-impersonated-without-primary', 'owner', A, 200, ATHLETE_SCOPED_BODY,
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}, setup={'primary_association': None})
add('athlete-owner-forbidden', 'owner', A, 403, ['error: forbidden.'])
add('athlete-full-collaborator-forbidden', 'full_collaborator', A, 403, ['error: forbidden.'])
add('athlete-admin-impersonating-owner-forbidden', 'admin', A, 403, ['error: forbidden.'], headers=IMPERSONATE_OWNER)
add('athlete-restricted', 'restricted_collaborator', A, 403, {'detail': 'Missing permission: association.dashboard.read'})
add('athlete-admin-without-session', 'admin', A, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('athlete-anonymous', 'anonymous', A, 401, {'detail': 'Authentication credentials were not provided.'})
add('athlete-wrong-method', 'athlete', A, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('athlete-options', 'athlete', A, 200, {'name': 'Statistic Athlete Dashboard', 'description': '', 'renders': ['application/json'],
                                           'parses': OPTIONS_PARSES}, method='OPTIONS')

# statistic/report
OWNER_YEARS = {'$available_years': [OWNER_BALANCE['day'], OWNER_BALANCE['month']]}
add('report-year', 'owner', P + '?currentDate=2025-03-10', 200, report(OWN, 2024, available=OWNER_YEARS))
add('report-year-start-day', 'owner', P + '?currentDate=2024-09-01', 200, report(OWN, 2024, available=OWNER_YEARS))
add('report-year-unpadded-date', 'owner', P + '?currentDate=2025-3-10', 200, report(OWN, 2024, available=OWNER_YEARS))
add('report-previous-year', 'owner', P + '?currentDate=2024-08-31', 200, report(OWN, 2023, available=OWNER_YEARS))
add('report-year-group', 'owner', P + '?currentDate=2025-03-10', 200, report(OWN, 2024, GROUP, available=OWNER_YEARS), headers=GROUP_HEADER)
add('report-full-collaborator', 'full_collaborator', P + '?currentDate=2025-03-10', 200, report(OWN, 2024, available=OWNER_YEARS))
add('report-owner-impersonating-collaborator', 'owner', P + '?currentDate=2025-03-10', 200,
    report(OWN, 2024, available=OWNER_YEARS, impersonated=True),
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})
add('report-foreign-current-year', 'foreign_owner', P, 200, foreign_default_report())
add('report-foreign-empty-date-is-current', 'foreign_owner', P + '?currentDate=', 200, foreign_default_report())
add('report-invalid-date', 'owner', P + '?currentDate=2025-02-30', 500, SERVER)
add('report-unparseable-date', 'owner', P + '?currentDate=10/03/2025', 500, SERVER)
add('report-accepted-without-associate', 'owner_three', P + '?currentDate=2025-03-10', 500, SERVER)
add('report-no-association', 'owner_without_association', P, 500, SERVER)
add('report-deleted-association', 'deleted_association_owner', P, 500, SERVER)
add('report-athlete-forbidden', 'athlete', P, 403, {'detail': 'You do not have permission to perform this action.'})
add('report-restricted', 'restricted_collaborator', P, 403, {'detail': 'Missing permission: association.report.read'})
add('report-owner-scoped-to-athlete', 'owner', P, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE})
add('report-admin-without-session', 'admin', P, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('report-anonymous', 'anonymous', P, 401, {'detail': 'Authentication credentials were not provided.'})
add('report-wrong-method', 'owner', P, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('report-options', 'owner', P, 200, {'name': 'Statistic Report', 'description': '', 'renders': ['application/json'],
                                        'parses': OPTIONS_PARSES}, method='OPTIONS')

# Sanity of the authored oracle itself.
naive = 0.0
for value in widget_payments(OWN)['data']['current_month_payments']:
    naive += value
assert naive != widget_payments(OWN)['data']['total_payments'], 'the payments total must distinguish compensated summation'
assert len({c['name'] for c in cases}) == len(cases)

OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'statistic/dashboard, statistic/athlete-dashboard and statistic/report authored sequence',
    'ids': IDS, 'R': R, 'users': USERS, 'owner_balance': OWNER_BALANCE, 'associates': ASSOCIATES, 'medicals': MEDICALS,
    'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'invoices': INVOICES, 'courses': COURSES,
    'course_subscriptions': COURSE_SUBSCRIPTIONS, 'registries': REGISTRIES, 'days': DAYS, 'messages': MESSAGES,
    'deliveries': DELIVERIES, 'carnets': CARNETS, 'carnet_subscriptions': CARNET_SUBSCRIPTIONS,
    'association_three': {'denomination': 'Statistiche terza', 'tax_code': '93000000003'},
    'extra_associations': EXTRA_ASSOCIATIONS, 'payment_categories': PAYMENT_CATEGORIES, 'instructors': INSTRUCTORS,
    'own_review': {'review_url': 'https://example.test/recensioni', 'review_url_enabled': True},
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
