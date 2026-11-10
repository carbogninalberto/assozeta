"""Author the course-subscriptions list, update, delete and bulk-delete HTTP sequence (course-subscriptions.md).

CourseSubscriptionViewSet (ModelViewSet, CourseSubscriptionOverviewSerializer)
is mapped as_view({'get': 'list', 'post': 'list'}), ({'patch': 'update'}),
({'delete': 'delete'}) and ({'delete': 'bulk_delete'}); the class's own
delete() answers DELETE on every route (no pk: get_object() fails, 500).

get_queryset() filters CourseSubscription.objects (soft-delete manager, or
all_objects() for ``include_deleted=true`` and the delete action) by the
optional ``course_id`` / ``subscription_id`` query values (a value the UUID
column rejects is a 500) and ``course__sport_association`` =
User.sport_association (athletes: PermissionDenied 'User is not a sport
association'; owners without an association: 500). list renders
{'data': [...]} unordered; POST reads request.data (a parse error is DRF's
400) and runs current_view_course_subscription: an invalid format is a 500,
'excel' answers {'file', 'filename'} with get_excel_base64 of the rows in the
database's order (Cognome, Nome, Nato il, Email, Telefono, Corso, Data
creazione, Tags, a Carnet column when a listed row has an enabled carnet
subscription, one column per Tags of the association); a row without
subscription, associate or birth date is a 500. The pdf file is not ported.

The serializer renders the nested subscription (SubscriptionSerializerSimplify),
the live group-aware installments, the live group-aware membership payments
(PaymentSerializer), the subscription's medical label (time dependent: the
contract fills the ``{since:}``, ``{until:}``, ``{missing:}`` and ``{toage:}``
day counts at request time) and the enabled carnet holdings.

delete() is the model's soft delete: the live group-aware installments are
deleted through the collector (each unpaid installment payment is deleted by
the pre_delete signals and audited twice, with the installment between them)
and the row is saved with deleted=True. bulk_delete hard-deletes the matching
live rows: installments (base manager) and course subscriptions get delete
entries in the collector's reversed pk order after the pre_delete signals;
delete_related_course_payment fails on Payment.objects.get(id=...) for rows
with a payment, otherwise it deletes the unpaid membership payments.
update is atomic: the serializer (partial) and its membership validate(),
then the current membership payment is matched by its meta billed_until and
saved with the new amount and period (Payment.save() fills a null category).
``events`` that are not null (installment edits) and a changed primary key are
not ported. Run with ``python3 -I``.
"""
import copy
import datetime as dt
import decimal
import json
from pathlib import Path
import uuid
import zoneinfo

OUTPUT = Path(__file__).with_name('course_subscriptions_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FOREIGN_OWNER = IDS['owner'], IDS['foreign_owner']
ids = iter(f'a1000000-0000-4000-8000-{660001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
CATEGORY = next(ids)
DOCUMENT = {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta è.pdf', 'filepath': 'docs/ricevuta.pdf', 'size': 2048,
            'created': '2026-01-02T08:00:00Z'}
OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}

ASSOCIATES = {
    'mario': {'id': next(ids), 'association': OWN, 'first': 'Mario', 'last': 'Rossi', 'sex': 'M', 'tax': 'RSSMRA90E10H501Z',
              'born': '1990-05-10', 'email': 'mario@example.test'},
    'luisa': {'id': next(ids), 'association': OWN, 'first': 'Luisa', 'last': 'Bianchi', 'sex': 'F', 'tax': None, 'born': '1985-02-02',
              'email': None},
    'piccolo': {'id': next(ids), 'association': OWN, 'first': 'Piccolo', 'last': 'Verdi', 'sex': 'M', 'tax': None, 'born': '2024-06-01',
                'email': None},
    'senza': {'id': next(ids), 'association': OWN, 'first': 'Senza', 'last': 'Data', 'sex': 'A', 'tax': None, 'born': None, 'email': None},
    'gino': {'id': next(ids), 'association': OWN, 'first': 'Gino', 'last': 'Neri', 'sex': 'M', 'tax': None, 'born': '1970-03-03',
             'email': None},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'first': 'Hans', 'last': 'Muller', 'sex': 'M', 'tax': None, 'born': '1980-01-01',
               'email': None},
}
MEDICALS = {'expired': {'id': next(ids), 'expiration': '2020-01-15', 'competitive': True},
            'valid': {'id': next(ids), 'expiration': '2099-12-31', 'competitive': False},
            'undated': {'id': next(ids), 'expiration': None, 'competitive': False}}
SUBSCRIPTIONS = {
    'sub_mario': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'medical': 'expired', 'start': '2020-09-01', 'end': '2099-08-31',
                  'created': '2026-08-01T10:00:00Z', 'group': None},
    'sub_luisa': {'id': next(ids), 'association': OWN, 'associate': 'luisa', 'medical': 'valid', 'start': '2020-01-01', 'end': '2021-12-31',
                  'created': '2025-08-01T10:00:00Z', 'group': None},
    'sub_piccolo': {'id': next(ids), 'association': OWN, 'associate': 'piccolo', 'medical': None, 'start': '2099-01-01', 'end': '2099-12-31',
                    'created': '2026-08-02T10:00:00Z', 'group': None},
    'sub_senza': {'id': next(ids), 'association': OWN, 'associate': 'senza', 'medical': None, 'start': None, 'end': None,
                  'created': '2026-08-03T10:00:00Z', 'group': None},
    'sub_gino': {'id': next(ids), 'association': OWN, 'associate': 'gino', 'medical': 'undated', 'start': None, 'end': None,
                 'created': '2026-09-01T10:00:00Z', 'group': None},
    'sub_group': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'medical': None, 'start': None, 'end': None,
                  'created': '2026-08-04T10:00:00Z', 'group': 'own_group'},
    'sub_abroad': {'id': next(ids), 'association': FOREIGN, 'associate': 'abroad', 'medical': None, 'start': None, 'end': None,
                   'created': '2026-08-05T10:00:00Z', 'group': None},
}
COURSES = {
    'yoga': {'id': next(ids), 'association': OWN, 'type': 1, 'title': 'Yoga è', 'deleted': False, 'group': None},
    'palestra': {'id': next(ids), 'association': OWN, 'type': 3, 'title': 'Palestra', 'deleted': False, 'group': None},
    'sala': {'id': next(ids), 'association': OWN, 'type': 3, 'title': 'Sala pesi', 'deleted': False, 'group': None},
    'vecchio': {'id': next(ids), 'association': OWN, 'type': 1, 'title': 'Vecchio', 'deleted': True, 'group': None},
    'gruppo': {'id': next(ids), 'association': OWN, 'type': 1, 'title': 'Gruppo', 'deleted': False, 'group': 'own_group'},
    'estero': {'id': next(ids), 'association': FOREIGN, 'type': 1, 'title': 'Estero', 'deleted': False, 'group': None},
    'estero_abb': {'id': next(ids), 'association': FOREIGN, 'type': 3, 'title': 'Estero abbonamento', 'deleted': False, 'group': None},
    'corsa': {'id': next(ids), 'association': OWN, 'type': 1, 'title': 'Corsa', 'deleted': False, 'group': None},
}


# PaymentSerializer's nested instructor and invoice of a membership payment.
INSTRUCTOR = {'id': next(ids), 'first_name': 'Anna è', 'last_name': 'Bianchi', 'email': 'anna@example.test', 'tax_code': 'BNCNNA80A41H501X',
              'default_hourly_billing': '12.50'}
INVOICE = {'id': next(ids), 'created': '2026-02-02T09:00:00Z', 'membership_fee': '25.00', 'activity_fee': '0.00', 'description': 'Fattura è',
           'number': 7, 'meta': {'numero': 7}}


def period(start, end, fee='25.00', title='Palestra'):
    return {'description': f'Abbonamento: {title} dal {start[8:10]}/{start[5:7]}/{start[:4]} al {end[8:10]}/{end[5:7]}/{end[:4]}',
            'billed_from': start, 'billed_until': end, 'amount': fee}


def payment(subject=2, amount='10.00', paid=False, meta=None, *, association=OWN, associate=None, user=None, group=None, deleted=False,
            category=CATEGORY, created='2026-10-01T08:00:00Z', paid_on=None, description='Quota', attachment=False):
    return {'id': next(ids), 'association': association, 'subject': subject, 'amount': amount, 'paid': paid, 'meta': meta,
            'associate': associate, 'user': user, 'group': group, 'deleted': deleted, 'category': category, 'created': created,
            'payment_date': paid_on, 'description': description, 'attachment': attachment}


PAYMENTS = {
    'main': payment(amount='50.00', description='Quota corso Yoga'),
    'inst_a': payment(amount='20.00'),
    'inst_b': payment(amount='30.00', paid=True),
    'inst_g': payment(group='own_group'),
    'mp_old': payment(amount='25.00', paid=True, meta=period('2026-01-01', '2026-02-01'), associate='luisa', user=OWNER,
                      created='2026-01-01T00:00:00Z', paid_on='2026-01-01T00:00:00Z', description='Abbonamento: Palestra dal 01/01/2026 al 01/02/2026',
                      attachment=True),
    'mp_cur': payment(amount='25.00', meta=period('2026-02-01', '2026-03-01'), associate='luisa', user=OWNER, category=None,
                      created='2026-02-01T00:00:00Z', paid_on='2026-02-01T00:00:00Z', description='Abbonamento: Palestra dal 01/02/2026 al 01/03/2026'),
    'mp_removed': payment(amount='25.00', meta=period('2026-02-01', '2026-03-01'), deleted=True),
    'mp_other': payment(subject=0, amount='5.00', paid=True, meta={'billed_from': '2025-12-01', 'billed_until': '2026-01-01'}, associate='luisa'),
    'dup_a': payment(amount='25.00', meta=period('2026-04-01', '2026-05-01')),
    'dup_b': payment(amount='25.00', meta=period('2026-04-01', '2026-05-01')),
    'mis_unpaid': payment(amount='25.00', meta=period('2026-06-01', '2026-07-01')),
    'mis_paid': payment(amount='25.00', paid=True, meta=period('2026-05-15', '2026-06-15')),
    'rec': payment(amount='25.00', meta=period('2026-03-01', '2026-04-01')),
    'bad': payment(amount='25.00', meta={'billed_until': '2026-08-01'}),
    'del_pa': payment(amount='15.00'),
    'del_pb': payment(amount='15.00', paid=True),
    'del2_p': payment(amount='12.00'),
    'bd_main': payment(amount='50.00'),
    'bd_pa': payment(amount='20.00'),
    'bd_pb': payment(amount='20.00', paid=True),
    'bd_mu': payment(amount='25.00', meta=period('2026-02-01', '2026-03-01')),
    'bd_mp': payment(amount='25.00', paid=True, meta=period('2026-01-01', '2026-02-01')),
    'abroad_pay': payment(association=FOREIGN),
    'bd4_pa': payment(amount='20.00'),
    # installment edits (events)
    'ev_pk': payment(amount='20.00'),
    'ev_pd': payment(amount='25.00'),
    'ev_pp': payment(amount='30.00', paid=True, category=None, description='Rata pagata'),
    'mp_inv': {**payment(amount='25.00', paid=True, meta=period('2026-01-01', '2026-02-01', title='Sala pesi'), associate='gino', user=OWNER,
                         description='Abbonamento: Sala pesi dal 01/01/2026 al 01/02/2026'), 'invoice': True, 'instructor': True},
}


def subscription_row(course, subscription, **fields):
    row = {'id': next(ids), 'course': course, 'subscription': subscription, 'paid': False, 'created': '2026-09-10T08:00:00Z',
           'payment': None, 'multi_payments': False, 'one_fee_payment': False, 'archived': False, 'type': COURSES[course]['type'],
           'multiple_quote': None, 'billed_frequency': 1, 'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1,
           'membership_active': True, 'membership_fee': '0.00', 'auto_renewal': False, 'deleted': False, 'membership_payments': []}
    row.update(fields)
    return row


MEMBER = {'billed_from': '2026-01-01T00:00:00Z', 'billed_until': '2026-03-01T00:00:00Z', 'membership_fee': '25.00'}
CS = {
    'cs_yoga': subscription_row('yoga', 'sub_mario', payment='main', multi_payments=True, multiple_quote={'title': 'Base', 'amount': '10,50'},
                                created='2026-09-10T08:00:00.250000Z'),
    'cs_member': subscription_row('palestra', 'sub_luisa', auto_renewal=True, membership_payments=['mp_old', 'mp_cur', 'mp_removed', 'mp_other'],
                                  **MEMBER),
    'cs_carnet': subscription_row('yoga', 'sub_piccolo', paid=True),
    'cs_nosub': subscription_row('yoga', None, one_fee_payment=True),
    'cs_senza': subscription_row('yoga', 'sub_senza', archived=True),
    'cs_gino': subscription_row('yoga', 'sub_gino', membership_active=False),
    'cs_oldcourse': subscription_row('vecchio', 'sub_mario', billed_frequency=6),
    'cs_deleted': subscription_row('yoga', 'sub_mario', deleted=True),
    'cs_dup': subscription_row('palestra', 'sub_luisa', billed_from='2026-01-01T00:00:00Z', billed_until='2026-05-01T00:00:00Z',
                               membership_fee='25.00', membership_payments=['dup_a', 'dup_b']),
    'cs_mismatch': subscription_row('palestra', 'sub_luisa', billed_from='2026-01-01T00:00:00Z', billed_until='2026-06-01T00:00:00Z',
                                    membership_fee='25.00', membership_payments=['mis_unpaid', 'mis_paid']),
    'cs_recover': subscription_row('palestra', 'sub_luisa', billed_from='2026-04-01T00:00:00Z', billed_until='2026-05-01T00:00:00Z',
                                   membership_fee='25.00', membership_payments=['rec']),
    'cs_badmeta': subscription_row('palestra', 'sub_luisa', billed_from='2026-07-01T00:00:00Z', billed_until='2026-08-01T00:00:00Z',
                                   membership_fee='25.00', membership_payments=['bad']),
    'cs_del': subscription_row('yoga', 'sub_mario'),
    'cs_del2': subscription_row('yoga', 'sub_gino'),
    'cs_del3': subscription_row('yoga', 'sub_luisa'),
    'cs_del4': subscription_row('yoga', 'sub_luisa'),
    'cs_del5': subscription_row('yoga', 'sub_piccolo'),
    'cs_bd1': subscription_row('yoga', 'sub_mario', payment='bd_main'),
    'cs_bd2': subscription_row('palestra', 'sub_luisa', membership_payments=['bd_mu', 'bd_mp'], **MEMBER),
    'cs_bd3': subscription_row('yoga', None),
    'cs_abroad': subscription_row('estero', 'sub_abroad'),
    'cs_bd4': subscription_row('gruppo', None),
    'cs_ev': subscription_row('corsa', 'sub_luisa', multi_payments=True),
    'cs_ev2': subscription_row('corsa', 'sub_gino', multi_payments=True),
    'cs_invoiced': subscription_row('sala', 'sub_gino', billed_from='2026-01-01T00:00:00Z', billed_until='2026-02-01T00:00:00Z',
                                    membership_fee='25.00', membership_payments=['mp_inv']),
}


def installment(cs, number, amount, payment_label=None, *, group=None, deleted=False, when='2026-10-01T22:30:00Z'):
    return {'id': next(ids), 'cs': cs, 'number': number, 'amount': amount, 'payment': payment_label, 'group': group, 'deleted': deleted,
            'payment_date': when, 'paid': False}


INSTALLMENTS = {
    'ia': installment('cs_yoga', 0, '20.00', 'inst_a'),
    'ib': installment('cs_yoga', 1, '30.00', 'inst_b', when='2026-11-01T00:00:00Z'),
    'ig': installment('cs_yoga', 2, '10.00', 'inst_g', group='own_group'),
    'idel': installment('cs_yoga', 3, '10.00', deleted=True),
    'del_ia': installment('cs_del', 0, '15.00', 'del_pa'),
    'del_ib': installment('cs_del', 1, '15.00', 'del_pb'),
    'del_ig': installment('cs_del', 2, '15.00', group='own_group'),
    'del_idel': installment('cs_del', 3, '15.00', deleted=True),
    'del2_i': installment('cs_del2', 0, '12.00', 'del2_p'),
    'del4_ig': installment('cs_del4', 0, '5.00', group='own_group'),
    'del4_i': installment('cs_del4', 1, '5.00'),
    'bda': installment('cs_bd1', 0, '20.00', 'bd_pa'),
    'bdb': installment('cs_bd1', 1, '20.00', 'bd_pb'),
    'bddel': installment('cs_bd1', 2, '20.00', deleted=True),
    'bdg': installment('cs_bd1', 3, '20.00', group='own_group'),
    'bd4a': installment('cs_bd4', 0, '20.00', 'bd4_pa'),
    'bd4b': installment('cs_bd4', 1, '20.00'),
    'bd4del': installment('cs_bd4', 2, '20.00', deleted=True),
    'bd4g': installment('cs_bd4', 3, '20.00', group='own_group'),
    'ev_keep': installment('cs_ev', 0, '20.00', 'ev_pk'),
    'ev_drop': installment('cs_ev', 1, '25.00', 'ev_pd'),
    'ev_paid': installment('cs_ev', 2, '30.00', 'ev_pp', when='2026-11-01T00:00:00Z'),
    'ev_none': installment('cs_ev', 3, '10.00'),
    'ev2_only': installment('cs_ev2', 0, '10.00'),
}
CARNETS = {'dieci': {'id': next(ids), 'title': 'Dieci ingressi', 'lessons': 10},
           'zero': {'id': next(ids), 'title': 'Libero', 'lessons': 0}}
HOLDINGS = {
    'h_active': {'id': next(ids), 'carnet': 'dieci', 'cs': ['cs_carnet', 'cs_bd1'], 'disabled': False, 'meta': {'lessons_left': 7},
                 'created': '2026-09-15T21:30:00Z'},
    'h_disabled': {'id': next(ids), 'carnet': 'dieci', 'cs': ['cs_carnet'], 'disabled': True, 'meta': None, 'created': '2026-09-16T08:00:00Z'},
    'h_plain': {'id': next(ids), 'carnet': 'dieci', 'cs': ['cs_carnet'], 'disabled': False, 'meta': None, 'created': '2026-09-17T08:00:00Z'},
    'h_zero': {'id': next(ids), 'carnet': 'zero', 'cs': ['cs_carnet'], 'disabled': False, 'meta': {}, 'created': '2026-09-18T08:00:00Z'},
}
# Tags of the excel export, with ids of their own (the shared iterator stays as it was). Tags and their through rows are
# seeded in this order; the export reads them without ORDER BY (course-subscriptions.md, "Excel export").
TAGS = {'t_a': {'id': 'a1000000-0000-4000-8000-000000669901', 'association': OWN, 'name': 'Agonisti'},
        't_b': {'id': 'a1000000-0000-4000-8000-000000669902', 'association': OWN, 'name': 'Master è'},
        't_f': {'id': 'a1000000-0000-4000-8000-000000669903', 'association': FOREIGN, 'name': 'Estero'}}
SUBSCRIPTION_TAGS = {'sub_mario': ['t_a', 't_b'], 'sub_luisa': ['t_b'], 'sub_abroad': ['t_f']}
# A carnet subscription of a soft-deleted course subscription: the export's prefetch does not list it.
HOLDINGS['h_deleted'] = {'id': 'a1000000-0000-4000-8000-000000669904', 'carnet': 'zero', 'cs': ['cs_deleted'], 'disabled': False,
                         'meta': None, 'created': '2026-09-19T08:00:00Z'}
UNKNOWN = 'a1000000-0000-4000-8000-000000669999'

STATE = {'cs': copy.deepcopy(CS), 'payments': copy.deepcopy(PAYMENTS), 'installments': copy.deepcopy(INSTALLMENTS)}
for row in STATE['installments'].values():
    row['paid'] = False
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'course-subscriptions-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# --- rendering -------------------------------------------------------------

def associate(label):
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': row['born'],
            'email': row['email'], 'sex': row['sex'], 'first_name': row['first'], 'last_name': row['last'], 'is_minor': False,
            'sport_association': row['association'], 'tax_code': row['tax'], 'user': OWNER if row['association'] == OWN else FOREIGN_OWNER,
            'phone': None, 'family': None, 'family_role': None}


def current_year(row):
    """Subscription.current_year() (the authored dates are far from today)."""
    if row['start'] is None or row['end'] is None:
        return 'corrente'
    if row['end'] < '2026-01-01':
        return 'precedente'
    if row['start'] > '2030-01-01':
        return 'successivo'
    return 'corrente'


def medical_label(label):
    """Subscription.get_medical_label(); the contract fills the day counts."""
    row = SUBSCRIPTIONS[label]
    medical = MEDICALS[row['medical']] if row['medical'] else None
    born = ASSOCIATES[row['associate']]['born']
    ag = '<span class="badge badge-info ml-1" title="Certificato Medico Agonistico">AG</span>' if medical and medical['competitive'] else ''
    if born is None:
        return f'<span class="text-success font-weight-boldest">Esente per 0 giorni</span>{ag}'
    if born > '2021-01-01':
        return f'<span class="text-success font-weight-boldest">Esente per {{toage:{born}}}</span>{ag}'
    if medical is None or medical['expiration'] is None:
        return f'<span class="text-warning font-weight-boldest">Mancante da {{missing:{row["created"]}}}</span>{ag}'
    if medical['expiration'] < '2026-01-01':
        return ('<span class="text-danger font-weight-boldest"><i class="ph-bold ph-first-aid-kit"></i>'
                f'Scaduto da {{since:{medical["expiration"]}}}</span>{ag}')
    return ('<span class="text-success font-weight-boldest"><i class="ph-bold ph-first-aid-kit"></i>'
            f'{{until:{medical["expiration"]}}} rimanenti</span>{ag}')


def subscription(label):
    row = SUBSCRIPTIONS[label]
    return {'subscription_id': row['id'], 'associate': associate(row['associate']), 'current_year': current_year(row)}


def payment_subscription(row, group):
    """PaymentSerializer.get_subscription_id for the authored payments."""
    if row['subject'] == 0 and row['associate']:
        subs = [s for s in SUBSCRIPTIONS.values() if s['associate'] == row['associate'] and (group is None or s['group'] == group)]
        return min(subs, key=lambda s: s['created'])['id'] if subs else None
    return None


def instructor_item():
    row = INSTRUCTOR
    return {'instructor_id': row['id'], 'user': OWNER, 'first_name': row['first_name'], 'last_name': row['last_name'], 'email': row['email'],
            'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row['tax_code'], 'address_city': None, 'address': None,
            'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore', 'phone': None, 'phone_2': None,
            'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None, 'is_volunteer': False, 'stipulated_contract_in': None,
            'default_hourly_billing': row['default_hourly_billing'], 'default_percentage_billing': '0.00', 'documents': []}


def invoice_item():
    row = INVOICE
    return {'invoice_id': row['id'], 'document_token': None, 'creation_date': row['created'], 'membership_fee': row['membership_fee'],
            'activity_fee': row['activity_fee'], 'meta_payment_categories': None, 'description': row['description'], 'number': row['number'],
            'archived': False, 'cancelled': False, 'meta': row['meta'], 'imported_from_associami': False, 'deleted': False, 'document_pdf': None,
            'sport_association': OWN, 'selected_tutor': None, 'group': None, 'attached_documents': []}


def payment_item(label, group=None):
    row = STATE['payments'][label]
    attachments = [{'document_id': DOCUMENT['id'], 'creation_date': DOCUMENT['created'], 'filepath': DOCUMENT['filepath'],
                    'filename': DOCUMENT['filename'], 'file_size_bytes': DOCUMENT['size'], 'token': DOCUMENT['token']}] if row['attachment'] else []
    category = row['category']
    return {'payment_id': row['id'], 'user': OWNER_USER if row['user'] == OWNER else None,
            'associate': associate(row['associate']) if row['associate'] else None, 'subscription_id': payment_subscription(row, group),
            'instructor_id': INSTRUCTOR['id'] if row.get('instructor') else None, 'instructor': instructor_item() if row.get('instructor') else None,
            'supplier': None, 'supplier_id': None, 'custom_account_type': None,
            'custom_account_name': None, 'invoice': invoice_item() if row.get('invoice') else None, 'attachments': attachments,
            'payment_category_name': None if category is None else ('$default_category_name' if category == '$default_category' else 'Corsi fixture'),
            'type': 'default', 'subject': row['subject'], 'amount': row['amount'], 'creation_date': row['created'],
            'payment_date': row['payment_date'], 'paid': row['paid'], 'expense': False, 'description': row['description'],
            'meta_payment_categories': None, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': row['meta'],
            'notes': None, 'deleted': row['deleted'], 'course': None, 'payment_category': category, 'sport_association': row['association'],
            'custom_accounts': None, 'signature': None, 'group': IDS[row['group']] if row['group'] else None}


def installment_item(label):
    row = STATE['installments'][label]
    paid = bool(row['payment']) and STATE['payments'][row['payment']]['paid']
    return {'course_subscription_installment_id': row['id'], 'id': row['number'], 'amount': row['amount'],
            'payment_date': row['payment_date'][:10], 'paid': paid}


def carnet_items(cs):
    result = []
    for row in HOLDINGS.values():
        if cs not in row['cs'] or row['disabled']:
            continue
        carnet = CARNETS[row['carnet']]
        total = carnet['lessons']
        left = row['meta'].get('lessons_left', total) if row['meta'] else total
        result.append({'carnet_subscription_id': row['id'], 'carnet_id': carnet['id'], 'title': carnet['title'], 'lessons_total': total,
                       'lessons_left': left, 'lessons_used': total - left if total else 0,
                       'lessons_display': f'{left}/{total}' if total else None,
                       'creation_date': f'{row["created"][8:10]}/{row["created"][5:7]}/{row["created"][:4]}'})
    return result


def item(label, group=None):
    row = STATE['cs'][label]
    installments = [k for k, i in STATE['installments'].items() if i['cs'] == label and not i['deleted'] and (group is None or i['group'] == group)]
    payments = [p for p in row['membership_payments'] if not STATE['payments'][p]['deleted']
                and (group is None or STATE['payments'][p]['group'] == group)]
    sub = row['subscription']
    return {'course_subscription_id': row['id'], 'creation_date': row['created'], 'paid': row['paid'],
            'subscription': subscription(sub) if sub else None, 'multi_payments': row['multi_payments'], 'multiple_quote': row['multiple_quote'],
            'one_fee_payment': row['one_fee_payment'], 'installments': [installment_item(i) for i in installments],
            'course': COURSES[row['course']]['id'], 'type': row['type'], 'billed_frequency': row['billed_frequency'],
            'billed_from': row['billed_from'], 'billed_until': row['billed_until'], 'billed_from_day_of_month': row['billed_from_day_of_month'],
            'membership_active': row['membership_active'], 'membership_fee': row['membership_fee'], 'auto_renewal': row['auto_renewal'],
            'membership_payments': [payment_item(p, group) for p in payments], 'medical_label': medical_label(sub) if sub else None,
            'carnets': carnet_items(label)}


def listing(*labels, group=None):
    return {'data': [item(label, group) for label in labels]}


def own_rows(include_deleted=False):
    return [k for k, r in STATE['cs'].items() if COURSES[r['course']]['association'] == OWN and (include_deleted or not r['deleted'])]


# --- effects ---------------------------------------------------------------

def audit_text(value):
    """auditlog's str(): DateTimeFields as naive UTC, None as 'None'."""
    if value is None:
        return 'None'
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    text = str(value)
    if len(text) >= 20 and text[10] == 'T' and text.endswith('Z'):
        return text.replace('T', ' ').replace('Z', '')
    return text


def jsonb(value):
    """A JSON value as read back from jsonb: object keys by length, then bytes."""
    if isinstance(value, dict):
        return {key: jsonb(value[key]) for key in sorted(value, key=lambda key: (len(key.encode()), key.encode()))}
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    return value


def cs_repr(label, sent=()):
    """auditlog's object_repr: CourseSubscription.__str__ is str(self.__dict__) of
    the in-memory instance. Values read from the database carry
    datetime.timezone.utc and jsonb key order; values the serializer set carry
    DRF's types (zoneinfo UTC datetimes, the request's JSON). The ModelState
    address is not reproducible ('0x?')."""
    row = STATE['cs'][label]

    def moment(name, value):
        if value is None:
            return None
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(zoneinfo.ZoneInfo('UTC') if name in sent else dt.timezone.utc)
    key = lambda value: uuid.UUID(value) if value else None
    values = {'course_subscription_id': key(row['id']), 'course_id': key(COURSES[row['course']]['id']), 'paid': row['paid'],
              'creation_date': moment('creation_date', row['created']),
              'subscription_id': key(SUBSCRIPTIONS[row['subscription']]['id'] if row['subscription'] else None),
              'payment_id': key(PAYMENTS[row['payment']]['id'] if row['payment'] else None), 'multi_payments': row['multi_payments'],
              'one_fee_payment': row['one_fee_payment'], 'archived': row['archived'], 'type': row['type'],
              'multiple_quote': row['multiple_quote'] if 'multiple_quote' in sent else jsonb(row['multiple_quote']),
              'billed_frequency': row['billed_frequency'], 'billed_from': moment('billed_from', row['billed_from']),
              'billed_until': moment('billed_until', row['billed_until']), 'billed_from_day_of_month': row['billed_from_day_of_month'],
              'membership_active': row['membership_active'],
              'membership_fee': decimal.Decimal(row['membership_fee']) if row['membership_fee'] is not None else None,
              'auto_renewal': row['auto_renewal'], 'deleted': row['deleted']}
    return "{'_state': <django.db.models.base.ModelState object at 0x?>, " + repr(values)[1:]


def deleted(kind, label, index, overrides=None):
    """A delete entry rendered from the row before the request (the adapter)."""
    return {'model': kind, 'label': label, 'action': 2, 'changes': None, 'overrides': overrides or {}, 'index': index,
            'repr': cs_repr(label) if kind == 'coursesubscription' else None}


def changed_entry(kind, label, index, changes, sent=()):
    return {'model': kind, 'label': label, 'action': 1, 'changes': changes, 'overrides': {}, 'index': index,
            'repr': cs_repr(label, sent) if kind == 'coursesubscription' else None}


def actor_index(actor):
    """The audit index through the actor's own association (none for collaborators)."""
    return {'owner': [OWN, 'actor.sport_association'], 'admin': [IDS['admin_association'], 'actor.sport_association']}.get(actor)


PAYMENT_INDEX = [OWN, 'changes_json.sport_association']


def unpaid_installment(label, actor):
    """installment pre_delete: its unpaid payment is deleted through Payment's
    pre_delete (the installment nested in between)."""
    pay = STATE['installments'][label]['payment']
    if not pay or STATE['payments'][pay]['paid']:
        return []
    return [deleted('payment', pay, PAYMENT_INDEX), deleted('coursesubscriptioninstallment', label, actor_index(actor)),
            deleted('payment', pay, PAYMENT_INDEX)]


def unseen_changes(label):
    """auditlog's diff of an instance whose old row the default manager hides:
    every field against None (null foreign keys and datetimes 'None', null JSON 'null')."""
    row = STATE['cs'][label]
    ident = lambda table, value: table[value]['id'] if value else None
    values = {'course_subscription_id': row['id'], 'course': ident(COURSES, row['course']), 'paid': row['paid'], 'creation_date': row['created'],
              'subscription': ident(SUBSCRIPTIONS, row['subscription']), 'payment': ident(PAYMENTS, row['payment']),
              'multi_payments': row['multi_payments'], 'one_fee_payment': row['one_fee_payment'], 'archived': row['archived'], 'type': row['type'],
              'multiple_quote': row['multiple_quote'], 'billed_frequency': row['billed_frequency'], 'billed_from': row['billed_from'],
              'billed_until': row['billed_until'], 'billed_from_day_of_month': row['billed_from_day_of_month'],
              'membership_active': row['membership_active'], 'membership_fee': row['membership_fee'], 'auto_renewal': row['auto_renewal'],
              'deleted': row['deleted']}
    changes = {}
    for name, value in values.items():
        if value is None:
            if name in ('course', 'subscription', 'payment', 'creation_date', 'billed_from', 'billed_until'):
                changes[name] = ['None', 'None']
            elif name == 'multiple_quote':
                changes[name] = ['None', 'null']
            continue
        changes[name] = ['None', audit_text(value)]
    return changes


def soft_delete(actor, label, group=None):
    """CourseSubscription.delete(): live group-aware installments (pk order for
    the signals, reversed for the deletes), then deleted=True."""
    row = STATE['cs'][label]
    gone = sorted(k for k, i in STATE['installments'].items() if i['cs'] == label and not i['deleted'] and (group is None or i['group'] == group))
    gone.sort(key=lambda k: STATE['installments'][k]['id'])
    audit, payments = [], []
    for key in gone:
        entries = unpaid_installment(key, actor)
        audit += entries
        payments += [e['label'] for e in entries if e['model'] == 'payment'][:1]
    audit += [deleted('coursesubscriptioninstallment', key, actor_index(actor)) for key in reversed(gone)]
    cs_changes = {}
    if row['deleted']:
        # auditlog diffs against _default_manager, which hides deleted rows: the
        # save is an update entry with every field against None.
        audit.append(changed_entry('coursesubscription', label, actor_index(actor), unseen_changes(label)))
    else:
        cs_changes = {'deleted': {'deleted': True}}
        row['deleted'] = True
        # auditlog writes the entry before the UPDATE: the row still resolves through its course.
        audit.append(changed_entry('coursesubscription', label, [COURSES[row['course']]['association'], 'course.sport_association'],
                                   {'deleted': ['False', 'True']}))
    for key in gone:
        del STATE['installments'][key]
    for key in payments:
        STATE['payments'][key]['gone'] = True
    return {'kind': 'delete', 'actor': actor, 'cs_changed': {label: {'deleted': True}} if cs_changes else {}, 'cs_deleted': [],
            'installments_deleted': gone, 'payments_deleted': payments, 'payments_changed': {}, 'audit': audit}


def nothing(actor):
    return {'kind': 'delete', 'actor': actor, 'cs_changed': {}, 'cs_deleted': [], 'installments_deleted': [], 'payments_deleted': [],
            'payments_changed': {}, 'audit': []}


LIST = '/course-subscriptions/list'
PAYMENT_TABLE, INSTALLMENT_TABLE = 'application.Payment', 'application.CourseSubscriptionInstallment'
BULK = '/course-subscriptions/bulk-delete'
one = lambda label, verb: f'/course-subscriptions/{CS[label]["id"] if label in CS else label}/{verb}'
C = lambda label: COURSES[label]['id']
S = lambda label: SUBSCRIPTIONS[label]['id']
NOT_FOUND = {'detail': 'No CourseSubscription matches the given query.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
GROUP = {'X-Group-Id': IDS['own_group']}
OPTIONS = {'name': 'Course Subscription', 'description': '', 'renders': ['application/json'],
           'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
missing = lambda verb: {'detail': f'Missing permission: association.courses.{verb}'}
missing_pk = lambda value: f'Invalid pk "{value}" - object does not exist.'
DATETIME_FORMAT = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
method = lambda name: {'detail': f'Method "{name}" not allowed.'}

field = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    **({'label': label} if label else {}), **extra}
text = lambda label, size=None, required=False: field('string', label, required, **({'max_length': size} if size else {}))
nested = lambda label, children, required=True, read_only=False: {**field('nested object', label, required, read_only), 'children': children}
choices = lambda *pairs: [{'value': value, 'display_name': name} for value, name in pairs]
ASSOCIATE_META = {
    'associate_id': text('Associate id'), 'address': text('Address', 150), 'address_cap': text('Address cap', 5),
    'address_city': text('Address city', 150), 'born_city': text('Born city', 150), 'born_date': field('date', 'Born date'),
    'email': field('email', 'Email', max_length=254), 'sex': field('choice', 'Sex', choices=choices(('A', 'other'), ('M', 'male'), ('F', 'female'))),
    'first_name': text('First name', 150), 'last_name': text('Last name', 150), 'is_minor': field('boolean', 'Is minor'),
    'sport_association': field('field', 'Sport association'), 'tax_code': text('Tax code', 25), 'user': field('field', 'User', read_only=True),
    'phone': text('Phone', 30), 'family': field('field', 'Family'), 'family_role': text('Family role', 100)}
USER_META = {'first_name': text('First name', 150), 'last_name': text('Last name', 150),
             'email': field('email', 'Email address', max_length=254), 'phone': text('Phone', 20), 'username': text('Username'),
             'role': field('choice', 'Role', choices=choices((1, 'association'), (2, 'athlete'), (3, 'association')))}
INSTRUCTOR_META = {
    'instructor_id': text('Instructor id'), 'first_name': text('First name', 150, True), 'last_name': text('Last name', 150, True),
    'email': field('email', 'Email', max_length=254), 'born_date': field('date', 'Born date'), 'born_city': text('Born city', 150),
    'born_province': text('Born province', 150), 'tax_code': text('Tax code', 25), 'address_city': text('Address city', 150),
    'address': text('Address', 150), 'address_province': text('Address province', 150), 'civic_number': text('Civic number', 10),
    'study_title': text('Study title', 150), 'role': text('Role', 150), 'phone': text('Phone', 20), 'phone_2': text('Phone 2', 20),
    'phone_3': text('Phone 3', 20), 'phone_4': text('Phone 4', 20), 'draft': field('boolean', 'Draft'),
    'associated_user_id': text('Associated user id'), 'is_volunteer': field('boolean', 'Is volunteer'),
    'stipulated_contract_in': field('date', 'Stipulated contract in'),
    'default_hourly_billing': field('decimal', 'Default hourly billing', max_digits=9, decimal_places=2),
    'default_percentage_billing': field('decimal', 'Default percentage billing', max_digits=4, decimal_places=2),
    'user': field('field', 'User', True), 'documents': field('field', 'Documents')}
SUPPLIER_META = {
    'supplier_id': text('Supplier id'), 'name': text('Name', 255, True), 'address': text('Address', 255), 'tax_code': text('Tax code', 30),
    'vat_number': text('Vat number', 30), 'type': text('Type', 30), 'email': field('email', 'Email', max_length=254),
    'phone_number': text('Phone number', 20), 'city': text('City', 100), 'cap': text('Cap', 10), 'state': text('State', 50),
    'country': text('Country', 50), 'nationality': text('Nationality', 100), 'note': text('Note'), 'deleted': field('boolean', 'Deleted'),
    'sport_association': field('field', 'Sport association'), 'group': field('field', 'Group')}
INVOICE_META = {
    'invoice_id': text('Invoice id'), 'document_token': field('field', 'Document token', read_only=True),
    'creation_date': field('datetime', 'Creation date'),
    'membership_fee': field('decimal', 'Membership fee', True, max_digits=9, decimal_places=2),
    'activity_fee': field('decimal', 'Activity fee', True, max_digits=9, decimal_places=2),
    'meta_payment_categories': field('field', 'Meta payment categories'), 'description': text('Description', 255),
    'number': field('integer', 'Number', min_value=-2147483648, max_value=2147483647), 'archived': field('boolean', 'Archived'),
    'cancelled': field('boolean', 'Cancelled'), 'meta': field('field', 'Meta'), 'imported_from_associami': field('boolean', 'Imported from associami'),
    'deleted': field('boolean', 'Deleted'), 'document_pdf': field('field', 'Document pdf'), 'sport_association': field('field', 'Sport association', True),
    'selected_tutor': field('field', 'Selected tutor'), 'group': field('field', 'Group'), 'attached_documents': field('field', 'Attached documents')}
DOCUMENT_META = {'document_id': text('Document id'), 'creation_date': field('datetime', 'Creation date'), 'filepath': text('Filepath', 1000),
                 'filename': text('Filename', 255, True), 'file_size_bytes': field('integer', 'File size bytes', min_value=0, max_value=9223372036854775807),
                 'token': text('Token')}
PAYMENT_META = {
    'payment_id': text('Payment id'), 'user': nested('User', USER_META), 'associate': nested('Associate', ASSOCIATE_META),
    'subscription_id': field('field', 'Subscription id', read_only=True), 'instructor_id': field('field', 'Instructor id', read_only=True),
    'instructor': nested('Instructor', INSTRUCTOR_META), 'supplier': nested('Supplier', SUPPLIER_META),
    'supplier_id': field('field', 'Supplier id', read_only=True), 'custom_account_type': field('field', 'Custom account type', read_only=True),
    'custom_account_name': field('field', 'Custom account name', read_only=True), 'invoice': nested('Invoice', INVOICE_META, required=False),
    'attachments': {**field('field', 'Attachments', read_only=True), 'child': {**field('nested object', None, read_only=True), 'children': DOCUMENT_META}},
    'payment_category_name': field('field', 'Payment category name', read_only=True),
    'type': field('choice', 'Type', choices=choices(('default', 'DEFAULT'), ('cash', 'CONTANTI'), ('transfer', 'BONIFICO'), ('online', 'ONLINE'),
                                                   ('sepa-transfer', 'BONIFICO SEPA'), ('stripe', 'STRIPE'), ('pos', 'POS'))),
    'subject': field('choice', 'Subject', choices=choices((0, 'Altro'), (1, 'Iscrizione'), (2, 'Corso'), (3, 'Giroconto'))),
    'amount': field('decimal', 'Amount', max_digits=9, decimal_places=2), 'creation_date': field('datetime', 'Creation date'),
    'payment_date': field('datetime', 'Payment date'), 'paid': field('boolean', 'Paid'), 'expense': field('boolean', 'Expense'),
    'description': text('Description', 5120), 'meta_payment_categories': field('field', 'Meta payment categories'),
    'payment_intent_id': text('Payment intent id', 125), 'archived': field('boolean', 'Archived'),
    'invoice_fiscal_code': text('Invoice fiscal code', 125), 'meta': field('field', 'Meta'), 'notes': text('Notes', 5120),
    'deleted': field('boolean', 'Deleted'), 'course': field('field', 'Course'), 'payment_category': field('field', 'Payment category'),
    'sport_association': field('field', 'Sport association'), 'custom_accounts': field('field', 'Custom accounts'),
    'signature': field('field', 'Signature'), 'group': field('field', 'Group')}
# DRF SimpleMetadata describes CourseSubscriptionOverviewSerializer for POST (the list route allows it).
LIST_OPTIONS = {**OPTIONS, 'actions': {'POST': {
    'course_subscription_id': text('Course subscription id'), 'subscription_id': text('Subscription id', required=True),
    'creation_date': field('datetime', 'Creation date'), 'paid': field('boolean', 'Paid'),
    'subscription': nested('Subscription', {'subscription_id': text('Subscription id'), 'associate': nested('Associate', ASSOCIATE_META),
                                            'current_year': field('field', 'Current year', read_only=True)}, required=False, read_only=True),
    'multi_payments': field('boolean', 'Multi payments'), 'multiple_quote': field('field', 'Multiple quote'),
    'one_fee_payment': field('boolean', 'One fee payment'), 'installments': field('field', 'Installments', read_only=True),
    'course': field('field', 'Course', True), 'type': field('choice', 'Type', read_only=True),
    'billed_frequency': field('integer', 'Billed frequency', min_value=0, max_value=32767), 'billed_from': field('datetime', 'Billed from'),
    'billed_until': field('datetime', 'Billed until'),
    'billed_from_day_of_month': field('integer', 'Billed from day of month', min_value=1, max_value=28),
    'membership_active': field('boolean', 'Membership active'),
    'membership_fee': field('decimal', 'Membership fee', max_digits=9, decimal_places=2), 'auto_renewal': field('boolean', 'Auto renewal'),
    'membership_payments': {**field('field', 'Membership payments', read_only=True),
                            'child': {**field('nested object', None, read_only=True), 'children': PAYMENT_META}},
    'events': field('field', 'Events'), 'medical_label': field('field', 'Medical label', read_only=True),
    'carnets': field('field', 'Carnets', read_only=True)}}}

# --- list ------------------------------------------------------------------
YOGA = ('cs_yoga', 'cs_carnet', 'cs_nosub', 'cs_senza', 'cs_gino', 'cs_del', 'cs_del2', 'cs_del3', 'cs_del4', 'cs_del5', 'cs_bd1', 'cs_bd3')
add('list-owner', 'owner', LIST, 200, listing(*own_rows()))
add('list-full-collaborator-owner-scope', 'full_collaborator', f'{LIST}?course_id={C("palestra")}', 200,
    listing('cs_member', 'cs_dup', 'cs_mismatch', 'cs_recover', 'cs_badmeta', 'cs_bd2'))
add('list-admin-impersonating-owner', 'admin', f'{LIST}?subscription_id={S("sub_mario")}', 200, listing('cs_yoga', 'cs_oldcourse', 'cs_del', 'cs_bd1'),
    headers=ADMIN_OWNER)
add('list-foreign-owner', 'foreign_owner', LIST, 200, listing('cs_abroad'))
add('list-course-filter', 'owner', f'{LIST}?course_id={C("yoga")}', 200, listing(*YOGA))
add('list-course-and-subscription-filter', 'owner', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_luisa")}', 200,
    listing('cs_del3', 'cs_del4'))
add('list-include-deleted', 'owner', f'{LIST}?include_deleted=TRUE&subscription_id={S("sub_mario")}', 200,
    listing('cs_yoga', 'cs_oldcourse', 'cs_deleted', 'cs_del', 'cs_bd1'))
add('list-include-deleted-other-value', 'owner', f'{LIST}?include_deleted=yes&subscription_id={S("sub_mario")}', 200,
    listing('cs_yoga', 'cs_oldcourse', 'cs_del', 'cs_bd1'))
# A membership payment with an invoice (generated from the payment page) and an instructor renders both nested.
add('list-membership-invoice-and-instructor', 'owner', f'{LIST}?course_id={C("sala")}', 200, listing('cs_invoiced'))
add('list-foreign-course-filter-empty', 'owner', f'{LIST}?course_id={C("estero")}', 200, {'data': []})
add('list-group-filters-installments-and-payments', 'owner', f'{LIST}?subscription_id={S("sub_luisa")}&course_id={C("palestra")}', 200,
    listing('cs_member', 'cs_dup', 'cs_mismatch', 'cs_recover', 'cs_badmeta', 'cs_bd2', group='own_group'), headers=GROUP)
add('list-group-installments', 'full_collaborator', f'{LIST}?subscription_id={S("sub_mario")}&course_id={C("yoga")}', 200,
    listing('cs_yoga', 'cs_del', 'cs_bd1', group='own_group'), headers=GROUP)
add('list-invalid-group-ignored', 'owner', f'{LIST}?subscription_id={S("sub_luisa")}&course_id={C("palestra")}', 200,
    listing('cs_member', 'cs_dup', 'cs_mismatch', 'cs_recover', 'cs_badmeta', 'cs_bd2'), headers={'X-Group-Id': 'abc'})
add('list-malformed-course-server-error', 'owner', f'{LIST}?course_id=abc', 500, SERVER)
add('list-blank-subscription-server-error', 'owner', f'{LIST}?subscription_id=', 500, SERVER)
add('list-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER)
add('list-athlete-not-association', 'athlete', LIST, 403, NOT_ASSOCIATION)
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_DENIED, headers=SCOPED)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST, 403, missing('read'))
add('list-admin-without-session', 'admin', LIST, 403, ADMIN)
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('list-wrong-method', 'owner', LIST, 405, method('PUT'), method='PUT', body={})
add('list-head', 'owner', f'{LIST}?course_id={C("sala")}', 200, '<empty>', method='HEAD')
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-delete-athlete-not-association', 'athlete', LIST, 403, NOT_ASSOCIATION, method='DELETE')
add('list-options', 'owner', LIST, 200, LIST_OPTIONS, method='OPTIONS')
# POST runs the export: an unknown or missing format raises (500).
add('list-export-unknown-format-server-error', 'owner', LIST, 500, SERVER, method='POST', body={'format': 'csv'})
add('list-export-missing-format-server-error', 'full_collaborator', f'{LIST}?course_id={C("yoga")}', 500, SERVER, method='POST', body={})
add('list-export-list-body-server-error', 'owner', LIST, 500, SERVER, method='POST', body=['pdf'])
add('list-export-form-format-server-error', 'owner', LIST, 500, SERVER, method='POST', raw_body='format=docx',
    content_type='application/x-www-form-urlencoded')
add('list-export-athlete-not-association', 'athlete', LIST, 403, NOT_ASSOCIATION, method='POST', body={'format': 'pdf'})
add('list-export-malformed-filter-server-error', 'owner', f'{LIST}?subscription_id=abc', 500, SERVER, method='POST', body={'format': 'pdf'})
add('list-export-restricted-missing-permission', 'restricted_collaborator', LIST, 403, missing('read'), method='POST', body={'format': 'pdf'})

# POST excel: current_view_course_subscription over get_queryset() (database order: the seed order of CS).
EXCEL = {'format': 'excel'}
ddmmyyyy = lambda text: f'{text[8:10]}/{text[5:7]}/{text[:4]}'


def export(*labels, association=OWN, denomination='Message reads own', fmt='excel'):
    title, lookup, listed = 'Iscrizioni ai corsi', {}, set(labels)
    for holding in HOLDINGS.values():
        if holding['disabled']:
            continue
        # One queryset row per listed course subscription of the holding; each walks its live course subscriptions.
        for _ in [cs for cs in holding['cs'] if cs in listed]:
            for cs in holding['cs']:
                if cs not in listed or CS[cs]['deleted']:
                    continue
                carnet = CARNETS[holding['carnet']]
                title = carnet['title'] or 'Carnet'  # the loop rebinds the function's title
                total = carnet['lessons'] or 0
                left = holding['meta'].get('lessons_left', total) if holding['meta'] else total
                display = f"{title} ({ddmmyyyy(holding['created'])})" + (f' - {left}/{total}' if total else '')
                lookup.setdefault(cs, []).append(display)
    rows = [CS[label] for label in labels]
    people = [ASSOCIATES[SUBSCRIPTIONS[row['subscription']]['associate']] for row in rows]
    owned = [SUBSCRIPTION_TAGS.get(row['subscription'], []) for row in rows]
    columns = ['Cognome', 'Nome', 'Nato il', 'Email', 'Telefono', 'Corso', 'Data creazione', 'Tags']
    data = [[person['last'] for person in people], [person['first'] for person in people],
            [f"{person['born'][8:10]}/{person['born'][5:7]}/{person['born'][:4]}" for person in people],
            [person['email'] for person in people], [None for _ in people], [COURSES[row['course']]['title'] for row in rows],
            [ddmmyyyy(row['created']) for row in rows], [', '.join(TAGS[t]['name'] for t in tags) for tags in owned]]
    if lookup:
        columns.append('Carnet')
        data.append([', '.join(lookup.get(label, [])) or '-' for label in labels])
    if fmt == 'pdf':
        # generate_pdf_from_data(zip(*rows), columns, title=...): the document carries the (rebound) title, the
        # filename a fixed one; the per-tag columns belong to the workbook only.
        return {'file': {'$pdf': {'engine': 'data_table', 'columns': columns, 'rows': [list(row) for row in zip(*data)],
                                  'title': f'{title} (giorno stampa $today)'}},
                'filename': f'[$today] Iscrizioni corsi filtrate - {denomination}.pdf'}
    for key, tag in TAGS.items():
        if tag['association'] == association:
            columns.append(tag['name'])
            data.append(['assegnato' if key in tags else '' for tags in owned])
    return {'file': {'$xlsx': {'engine': 'excel_utils', 'columns': columns, 'data': data}},
            'filename': f'[$today] {title} - {denomination}.xlsx'}


PALESTRA = ('cs_member', 'cs_dup', 'cs_mismatch', 'cs_recover', 'cs_badmeta', 'cs_bd2')
add('list-excel-course', 'owner', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA), method='POST', body=EXCEL)
add('list-excel-full-collaborator', 'full_collaborator', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA), method='POST', body=EXCEL)
add('list-excel-group-header-ignored', 'owner', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA), method='POST', body=EXCEL,
    headers=GROUP)
add('list-excel-form-body', 'owner', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA), method='POST',
    raw_body='format=pdf&format=excel', content_type='application/x-www-form-urlencoded')
# One carnet subscription on a listed row: the Carnet column, and its title in the filename.
# The export cases filter one course: across courses the unordered statement's row order follows the query plan.
add('list-excel-subscription-carnet-title', 'admin', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_mario")}', 200,
    export('cs_yoga', 'cs_del', 'cs_bd1'), method='POST', body=EXCEL, headers=ADMIN_OWNER)
add('list-excel-several-carnets', 'owner', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_piccolo")}', 200,
    export('cs_carnet', 'cs_del5'), method='POST', body=EXCEL)
add('list-excel-include-deleted', 'owner', f'{LIST}?include_deleted=true&course_id={C("yoga")}&subscription_id={S("sub_mario")}', 200,
    export('cs_yoga', 'cs_deleted', 'cs_del', 'cs_bd1'), method='POST', body=EXCEL)
add('list-excel-one-row', 'owner', f'{LIST}?course_id={C("sala")}', 200, export('cs_invoiced'), method='POST', body=EXCEL)
add('list-excel-empty', 'owner', f'{LIST}?course_id={C("estero")}', 200, export(), method='POST', body=EXCEL)
add('list-excel-foreign-owner', 'foreign_owner', LIST, 200, export('cs_abroad', association=FOREIGN, denomination='Message reads foreign'),
    method='POST', body=EXCEL)
# A row without subscription, or an associate without birth date, raises in the export.
add('list-excel-row-without-subscription-server-error', 'owner', LIST, 500, SERVER, method='POST', body=EXCEL)
add('list-excel-missing-birth-date-server-error', 'owner', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_senza")}', 500, SERVER,
    method='POST', body=EXCEL)
add('list-excel-format-case-sensitive', 'owner', f'{LIST}?course_id={C("sala")}', 500, SERVER, method='POST', body={'format': 'EXCEL'})
add('list-excel-string-body-server-error', 'owner', f'{LIST}?course_id={C("sala")}', 500, SERVER, method='POST', raw_body='"excel"',
    content_type='application/json')
add('list-excel-malformed-json', 'owner', f'{LIST}?course_id={C("sala")}', 400,
    {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'}, method='POST', raw_body='nope', content_type='application/json')
add('list-excel-filter-error-before-body', 'owner', f'{LIST}?course_id=abc', 500, SERVER, method='POST', raw_body='nope',
    content_type='application/json')
add('list-excel-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='POST', body=EXCEL)
add('list-excel-athlete-not-association', 'athlete', LIST, 403, NOT_ASSOCIATION, method='POST', body=EXCEL)

# POST pdf: the same rows as a reportlab table document (TESTS/contracts/pdf.py).
PDF = {'format': 'pdf'}
add('list-pdf-course', 'owner', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA, fmt='pdf'), method='POST', body=PDF)
add('list-pdf-full-collaborator', 'full_collaborator', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA, fmt='pdf'),
    method='POST', body=PDF)
add('list-pdf-form-body', 'owner', f'{LIST}?course_id={C("palestra")}', 200, export(*PALESTRA, fmt='pdf'), method='POST',
    raw_body='format=excel&format=pdf', content_type='application/x-www-form-urlencoded')
add('list-pdf-subscription-carnet-title', 'admin', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_mario")}', 200,
    export('cs_yoga', 'cs_del', 'cs_bd1', fmt='pdf'), method='POST', body=PDF, headers=ADMIN_OWNER)
add('list-pdf-several-carnets', 'owner', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_piccolo")}', 200,
    export('cs_carnet', 'cs_del5', fmt='pdf'), method='POST', body=PDF)
add('list-pdf-include-deleted', 'owner', f'{LIST}?include_deleted=true&course_id={C("yoga")}&subscription_id={S("sub_mario")}', 200,
    export('cs_yoga', 'cs_deleted', 'cs_del', 'cs_bd1', fmt='pdf'), method='POST', body=PDF)
add('list-pdf-one-row', 'owner', f'{LIST}?course_id={C("sala")}', 200, export('cs_invoiced', fmt='pdf'), method='POST', body=PDF)
add('list-pdf-empty', 'owner', f'{LIST}?course_id={C("estero")}', 200, export(fmt='pdf'), method='POST', body=PDF)
add('list-pdf-foreign-owner', 'foreign_owner', LIST, 200,
    export('cs_abroad', association=FOREIGN, denomination='Message reads foreign', fmt='pdf'), method='POST', body=PDF)
add('list-pdf-row-without-subscription-server-error', 'owner', LIST, 500, SERVER, method='POST', body=PDF)
add('list-pdf-missing-birth-date-server-error', 'owner', f'{LIST}?course_id={C("yoga")}&subscription_id={S("sub_senza")}', 500, SERVER,
    method='POST', body=PDF)
add('list-pdf-format-case-sensitive', 'owner', f'{LIST}?course_id={C("sala")}', 500, SERVER, method='POST', body={'format': 'PDF'})
add('list-pdf-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='POST', body=PDF)

# --- update ----------------------------------------------------------------
def updated(actor, label, fields, changes, *, payments=None, index=None, sent=()):
    """A partial update: the stored row takes fields; changes are auditlog's pairs;
    sent names the datetime/JSON fields the serializer set (their in-memory types)."""
    row = STATE['cs'][label]
    row.update(fields)
    audit = [changed_entry('coursesubscription', label, index or [OWN, 'course.sport_association'], changes, sent)] if changes else []
    payment_changes = {}
    for pay, values, pay_changes in payments or ():
        STATE['payments'][pay].update(values)
        payment_changes[pay] = values
        audit.append(changed_entry('payment', pay, [OWN, 'direct'], pay_changes))
    return {'kind': 'update', 'actor': actor, 'cs_changed': {label: fields}, 'cs_deleted': [], 'installments_deleted': [],
            'payments_deleted': [], 'payments_changed': payment_changes, 'audit': audit}


add('update-anonymous', 'anonymous', one('cs_yoga', 'update'), 401, ANONYMOUS, method='PATCH', body={})
add('update-admin-without-session', 'admin', one('cs_yoga', 'update'), 403, ADMIN, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', one('cs_yoga', 'update'), 403, missing('update'), method='PATCH',
    body={'paid': True})
add('update-owner-scoped-athlete-denied', 'owner', one('cs_yoga', 'update'), 403, SCOPED_DENIED, method='PATCH', body={'paid': True},
    headers=SCOPED)
add('update-wrong-method', 'owner', one('cs_yoga', 'update'), 405, method('PUT'), method='PUT', body={})
add('update-options', 'owner', one('cs_yoga', 'update'), 200, OPTIONS, method='OPTIONS')
# django.shortcuts.get_object_or_404 lets the UUID ValidationError escape.
add('update-malformed-server-error', 'owner', one('abc', 'update'), 500, SERVER, method='PATCH', body={'paid': True})
add('update-unknown-not-found', 'owner', one(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={'paid': True})
add('update-foreign-not-found', 'owner', one('cs_abroad', 'update'), 404, NOT_FOUND, method='PATCH', body={'paid': True})
add('update-deleted-not-found', 'owner', one('cs_deleted', 'update'), 404, NOT_FOUND, method='PATCH', body={'paid': True})
add('update-athlete-not-association', 'athlete', one('cs_yoga', 'update'), 403, NOT_ASSOCIATION, method='PATCH', body={'paid': True})
add('update-owner-without-association-server-error', 'owner_without_association', one('cs_yoga', 'update'), 500, SERVER, method='PATCH',
    body={})
add('update-invalid-fields', 'owner', one('cs_yoga', 'update'), 400, {
    'course_subscription_id': ['Must be a valid UUID.'], 'subscription_id': ['Must be a valid UUID.'], 'creation_date': [DATETIME_FORMAT],
    'paid': ['Must be a valid boolean.'], 'course': [missing_pk(UNKNOWN)], 'billed_frequency': ['Ensure this value is greater than or equal to 0.'],
    'billed_from': [DATETIME_FORMAT], 'billed_from_day_of_month': ['Ensure this value is less than or equal to 28.'],
    'membership_fee': ['A valid number is required.'], 'auto_renewal': ['This field may not be null.']},
    method='PATCH', body={'course_subscription_id': 'abc', 'subscription_id': [1], 'creation_date': 'ieri', 'paid': 'forse', 'course': UNKNOWN,
                          'billed_frequency': -1, 'billed_from': '2026-13-01', 'billed_from_day_of_month': 29, 'membership_fee': 'dieci',
                          'auto_renewal': None, 'multiple_quote': None, 'events': None})
add('update-invalid-numbers', 'owner', one('cs_yoga', 'update'), 400, {
    'billed_frequency': ['Ensure this value is less than or equal to 32767.'], 'billed_from_day_of_month': ['Ensure this value is greater than or equal to 1.'],
    'membership_fee': ['Ensure that there are no more than 9 digits in total.'], 'course': ['Incorrect type. Expected pk value, received bool.'],
    'billed_until': [DATETIME_FORMAT]},
    method='PATCH', body={'billed_frequency': 40000, 'billed_from_day_of_month': 0, 'membership_fee': '1234567.891', 'course': True,
                          'billed_until': 5})
add('update-deleted-course', 'owner', one('cs_yoga', 'update'), 400, {'course': [missing_pk(C('vecchio'))], 'membership_fee': [
    'Ensure that there are no more than 7 digits before the decimal point.']}, method='PATCH', body={'course': C('vecchio'), 'membership_fee': 12345678})
# The course field's queryset is evaluated once at import time.
add('update-group-header-keeps-course', 'owner', one('cs_yoga', 'update'), 200, item('cs_yoga', 'own_group'), method='PATCH',
    body={'course': C('yoga')}, headers=GROUP, effect=updated('owner', 'cs_yoga', {}, {}))
add('update-existing-id', 'owner', one('cs_yoga', 'update'), 400,
    {'course_subscription_id': ['course subscription with this course subscription id already exists.']}, method='PATCH',
    body={'course_subscription_id': CS['cs_carnet']['id']})
add('update-to-membership-course', 'owner', one('cs_yoga', 'update'), 400, {
    'course': ['Non puoi cambiare il corso di un abbonamento esistente.'], 'billed_from': ['Inserisci una data di inizio valida.'],
    'billed_until': ['Inserisci una data di fine valida.']}, method='PATCH', body={'course': C('palestra')})
add('update-unknown-subscription-server-error', 'owner', one('cs_yoga', 'update'), 500, SERVER, method='PATCH',
    body={'subscription_id': UNKNOWN, 'paid': True})
add('update-scoped-without-subscription-server-error', 'owner', one('cs_nosub', 'update'), 500, SERVER, method='PATCH', body={'paid': True},
    headers=OWNER_FULL)
effect = updated('owner', 'cs_yoga', {'paid': True, 'multiple_quote': {'title': 'Ridotta è', 'amount': '5,00', 'extra': [1, 2.5]},
                                      'billed_frequency': 3, 'created': '2026-09-11T08:00:00Z', 'membership_fee': '12.50'},
                 {'paid': ['False', 'True'], 'billed_frequency': ['1', '3'], 'creation_date': ['2026-09-10 08:00:00.250000', '2026-09-11 08:00:00'],
                  'membership_fee': ['0.00', '12.50'],
                  'multiple_quote': [json.dumps({'title': 'Base', 'amount': '10,50'}, sort_keys=True),
                                     json.dumps({'title': 'Ridotta è', 'amount': '5,00', 'extra': [1, 2.5]}, sort_keys=True)]},
                 sent=('creation_date', 'multiple_quote'))
add('update-owner', 'owner', one('cs_yoga', 'update'), 200, item('cs_yoga'), method='PATCH', body={
    'paid': True, 'multiple_quote': {'title': 'Ridotta è', 'amount': '5,00', 'extra': [1, 2.5]}, 'billed_frequency': '3',
    'creation_date': '2026-09-11T10:00:00+02:00', 'membership_fee': '12.5', 'events': None, 'type': 3, 'subscription': 'x',
    'course': C('yoga'), 'installments': [], 'membership_payments': None, 'medical_label': 'x',
    'course_subscription_id': CS['cs_yoga']['id']}, effect=effect)
effect = updated('full_collaborator', 'cs_yoga', {}, {})
add('update-same-values-no-audit', 'full_collaborator', one('cs_yoga', 'update'), 200, item('cs_yoga'), method='PATCH',
    body={'paid': 'true', 'billed_frequency': 3.0, 'membership_fee': 12.5, 'subscription_id': S('sub_mario').upper()}, effect=effect)
# HTML input: a blank nullable decimal or datetime is None.
effect = updated('owner', 'cs_carnet', {'subscription': 'sub_senza', 'paid': False, 'billed_frequency': 2, 'one_fee_payment': True,
                                        'membership_fee': None},
                 {'subscription': [S('sub_piccolo'), S('sub_senza')], 'paid': ['True', 'False'], 'billed_frequency': ['1', '2'],
                  'one_fee_payment': ['False', 'True'], 'membership_fee': ['0.00', 'None']}, sent=('billed_until',))
add('update-form-subscription-change', 'owner', one('cs_carnet', 'update'), 200, item('cs_carnet'), method='PATCH',
    raw_body=f'subscription_id={S("sub_senza")}&paid=false&billed_frequency=2&one_fee_payment=on&membership_fee=&billed_until=',
    content_type='application/x-www-form-urlencoded', effect=effect)
effect = updated('owner', 'cs_senza', {'course': 'gruppo', 'billed_from': '2026-02-01T00:00:00Z', 'billed_until': None},
                 {'course': [C('yoga'), C('gruppo')], 'billed_from': ['None', '2026-02-01 00:00:00']}, sent=('billed_from', 'billed_until'))
add('update-group-course', 'owner', one('cs_senza', 'update'), 200, item('cs_senza', 'own_group'), method='PATCH',
    body={'course': C('gruppo'), 'billed_from': '2026-02-01T01:00:00+01:00', 'billed_until': None}, headers=GROUP, effect=effect)

# membership validation and payment period
add('update-membership-validation', 'owner', one('cs_member', 'update'), 400, {
    'course': ['Non puoi cambiare il corso di un abbonamento esistente.'],
    'subscription_id': ['Non puoi cambiare il tesserato di un abbonamento esistente.'], 'billed_from': ['Inserisci una data di inizio valida.'],
    'billed_frequency': ['La durata deve essere compresa tra 1 e 12 mesi.'],
    'membership_fee': ['La quota deve essere un importo maggiore o uguale a zero.']},
    method='PATCH', body={'course': C('sala'), 'subscription_id': S('sub_mario'), 'billed_from': None, 'billed_frequency': 13,
                          'membership_fee': '-1'})
add('update-membership-end-before-start', 'owner', one('cs_member', 'update'), 400,
    {'billed_until': ['La data di fine non può precedere la data di inizio.'], 'billed_frequency': ['La durata deve essere compresa tra 1 e 12 mesi.'],
     'membership_fee': ['La quota deve essere un importo maggiore o uguale a zero.']},
    method='PATCH', body={'billed_until': '2025-12-01T00:00:00Z', 'billed_frequency': 0, 'membership_fee': None})
add('update-membership-foreign-course', 'owner', one('cs_member', 'update'), 400, {
    'course': ['Non puoi cambiare il corso di un abbonamento esistente.'],
    'subscription_id': ['Seleziona un tesserato della stessa associazione del corso.']}, method='PATCH', body={'course': C('estero_abb')})
add('update-membership-group-hides-subscription', 'owner', one('cs_member', 'update'), 400, {
    'subscription_id': ['Seleziona un tesserato della stessa associazione del corso.']}, method='PATCH', body={'auto_renewal': False},
    headers=GROUP)
add('update-membership-non-membership-course', 'owner', one('cs_member', 'update'), 400, {
    'course': ['Non puoi cambiare il corso di un abbonamento esistente.']}, method='PATCH', body={'course': C('yoga')})
add('update-membership-overlap', 'owner', one('cs_member', 'update'), 400,
    {'billed_from': 'Il periodo si sovrappone a un pagamento precedente. I periodi già fatturati devono rimanere separati.'},
    method='PATCH', body={'billed_from': '2026-01-15T00:00:00Z', 'membership_fee': '40'})
add('update-membership-end-before-payment', 'owner', one('cs_member', 'update'), 400,
    {'billed_until': 'La fine non può precedere l’inizio del periodo di pagamento corrente.'}, method='PATCH',
    body={'billed_until': '2026-01-20T00:00:00Z'})
add('update-membership-duplicate-payments', 'owner', one('cs_dup', 'update'), 400,
    {'billed_until': 'Più pagamenti corrispondono al periodo corrente. Verifica i pagamenti prima di modificare l’abbonamento.'},
    method='PATCH', body={'membership_fee': '5'})
add('update-membership-mismatch', 'owner', one('cs_mismatch', 'update'), 400,
    {'billed_until': 'Il periodo dell’abbonamento non corrisponde ai pagamenti. Verifica i periodi prima di salvare.'}, method='PATCH',
    body={'membership_fee': '5'})
add('update-membership-recover-needs-period', 'owner', one('cs_recover', 'update'), 400,
    {'billed_until': 'Il periodo dell’abbonamento non corrisponde ai pagamenti. Verifica i periodi prima di salvare.'}, method='PATCH',
    body={'membership_fee': '5', 'billed_from': '2026-04-01T00:00:00Z'})
add('update-membership-payment-without-start', 'owner', one('cs_badmeta', 'update'), 400,
    {'billed_from': 'Il pagamento corrente non contiene una data di inizio valida.'}, method='PATCH', body={'membership_fee': '5'})
cur = STATE['payments']['mp_cur']
fee_meta = {**cur['meta'], 'description': 'Abbonamento: Palestra dal 01/02/2026 al 01/03/2026', 'course_id': C('palestra'),
            'course_title': 'Palestra', 'course_subscription_id': CS['cs_member']['id'], 'billed_from': '2026-02-01', 'billed_until': '2026-03-01',
            'amount': '30.00'}
effect = updated('owner', 'cs_member', {'membership_fee': '30.00'}, {'membership_fee': ['25.00', '30.00']}, payments=[
    ('mp_cur', {'amount': '30.00', 'meta': fee_meta, 'category': '$default_category'},
     {'amount': ['25.00', '30.00'], 'payment_category': ['None', '$default_category'],
      'meta': [json.dumps(cur['meta'], sort_keys=True), json.dumps(fee_meta, sort_keys=True)]})], sent=('billed_from', 'billed_until'))
add('update-membership-fee', 'owner', one('cs_member', 'update'), 200, item('cs_member'), method='PATCH',
    body={'membership_fee': '30', 'billed_from': '2026-01-01T01:00:00+01:00', 'billed_until': '2026-03-01T00:00:00Z'}, effect=effect)
moved_meta = {**fee_meta, 'description': 'Abbonamento: Palestra dal 10/02/2026 al 10/03/2026', 'billed_from': '2026-02-10',
              'billed_until': '2026-03-10'}
effect = updated('full_collaborator', 'cs_member', {'billed_from': '2026-02-10T00:00:00Z', 'billed_until': '2026-03-10T00:00:00Z'},
                 {'billed_from': ['2026-01-01 00:00:00', '2026-02-10 00:00:00'], 'billed_until': ['2026-03-01 00:00:00', '2026-03-10 00:00:00']},
                 payments=[('mp_cur', {'created': '2026-02-10T00:00:00Z', 'payment_date': '2026-02-10T00:00:00Z', 'meta': moved_meta,
                                       'description': 'Abbonamento: Palestra dal 10/02/2026 al 10/03/2026'},
                            {'creation_date': ['2026-02-01 00:00:00', '2026-02-10 00:00:00'], 'payment_date': ['2026-02-01 00:00:00', '2026-02-10 00:00:00'],
                             'description': ['Abbonamento: Palestra dal 01/02/2026 al 01/03/2026', 'Abbonamento: Palestra dal 10/02/2026 al 10/03/2026'],
                             'meta': [json.dumps(fee_meta, sort_keys=True), json.dumps(moved_meta, sort_keys=True)]})],
                 sent=('billed_from', 'billed_until'))
add('update-membership-period', 'full_collaborator', one('cs_member', 'update'), 200, item('cs_member'), method='PATCH',
    body={'billed_from': '2026-02-10T00:00:00Z', 'billed_until': '2026-03-10T00:00:00Z'}, effect=effect)
rec = STATE['payments']['rec']
rec_meta = {**rec['meta'], 'description': 'Abbonamento: Palestra dal 01/04/2026 al 01/05/2026', 'course_id': C('palestra'),
            'course_title': 'Palestra', 'course_subscription_id': CS['cs_recover']['id'], 'billed_from': '2026-04-01', 'billed_until': '2026-05-01',
            'amount': '25.00'}
effect = updated('owner', 'cs_recover', {}, {}, payments=[
    ('rec', {'created': '2026-04-01T00:00:00Z', 'payment_date': '2026-04-01T00:00:00Z', 'meta': rec_meta,
             'description': 'Abbonamento: Palestra dal 01/04/2026 al 01/05/2026'},
     {'creation_date': ['2026-10-01 08:00:00', '2026-04-01 00:00:00'], 'payment_date': ['None', '2026-04-01 00:00:00'],
      'description': ['Quota', 'Abbonamento: Palestra dal 01/04/2026 al 01/05/2026'],
      'meta': [json.dumps(rec['meta'], sort_keys=True), json.dumps(rec_meta, sort_keys=True)]})])
add('update-membership-recover-initial-period', 'owner', one('cs_recover', 'update'), 200, item('cs_recover'), method='PATCH',
    body={'billed_from': '2026-04-01T00:00:00Z', 'billed_until': '2026-05-01T00:00:00Z'}, effect=effect)

# Installment edits: the form sends the selected events; installments without a matching event id go (a paid
# payment is relabelled first, an unpaid one is deleted by the installment's pre_delete); events without
# course_subscription_installment_id become a payment of the course's association and an installment.
def new_payment(sym, amount, *, created, description, meta, user=OWNER, associate='luisa'):
    return {'$sym': sym, 'payment_id': sym, 'type': 'default', 'subject': 2, 'amount': amount, 'creation_date': created, 'payment_date': None,
            'paid': False, 'expense': False, 'description': description, 'payment_category_id': '$default_category',
            'meta_payment_categories': None, 'invoice_id': None, 'user_id': user, 'associate_id': ASSOCIATES[associate]['id'], 'supplier_id': None,
            'instructor_id': None, 'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None,
            'meta': meta, 'notes': None, 'custom_accounts_id': '$bank_own', 'signature_id': None, 'deleted': False, 'group_id': None, 'course': None}


def new_installment(sym, cs, number, amount, payment_sym, created):
    return {'$sym': sym, 'course_subscription_installment_id': sym, 'course_subscription_id': CS[cs]['id'], 'id': number, 'amount': amount,
            'paid': False, 'payment_date': created, 'payment_id': payment_sym, 'archived': False, 'group_id': None, 'deleted': False}


def dropped(label, actor):
    """installment.delete(): its pre_delete deletes an unpaid payment (the installment nested in between)."""
    return unpaid_installment(label, actor) + [deleted('coursesubscriptioninstallment', label, actor_index(actor))]


actor = 'owner'
ev_meta = lambda description, when, cs='cs_ev': {'description': description, 'course_id': C('corsa'), 'course_title': 'Corsa',
                                                 'course_subscription_id': CS[cs]['id'], 'payment_date': when}
paid_meta = ev_meta('Rata n.3 del 01/11/26 (Corsa)', '2026-11-01 00:00:00+00:00')
new_text = 'Rata n.5 del 10/12/26 (Corsa)'
created = {PAYMENT_TABLE: [new_payment('@evp', '35.00', created='2026-12-10T00:00:00Z', description=new_text, meta=ev_meta(new_text, '2026-12-10'))],
           INSTALLMENT_TABLE: [new_installment('@evi', 'cs_ev', 4, '35.00', '@evp', '2026-12-10T00:00:00Z')]}
audit = dropped('ev_drop', actor) + [
    changed_entry('payment', 'ev_pp', [OWN, 'direct'], {'description': ['Rata pagata', 'Rata n.3 del 01/11/26 (Corsa)'],
                                                        'meta': ['null', json.dumps(paid_meta, sort_keys=True)],
                                                        'payment_category': ['None', '$default_category']}),
    deleted('coursesubscriptioninstallment', 'ev_paid', actor_index(actor)),
    deleted('coursesubscriptioninstallment', 'ev_none', actor_index(actor)),
    {'model': 'payment', 'label': '@evp', 'action': 0, 'changes': '$created', 'overrides': {}, 'index': [OWN, 'direct'], 'repr': None},
    {'model': 'coursesubscriptioninstallment', 'label': '@evi', 'action': 0, 'changes': '$created', 'overrides': {},
     'index': [OWN, 'course_subscription.course.sport_association'], 'repr': None}]
STATE['payments']['ev_pp'].update({'description': 'Rata pagata', 'meta': paid_meta, 'category': '$default_category'})
for key in ('ev_drop', 'ev_paid', 'ev_none'):
    del STATE['installments'][key]
STATE['payments']['@evp'] = {'paid': False}
STATE['installments']['@evi'] = {'id': '@evi', 'cs': 'cs_ev', 'number': 4, 'amount': '35.00', 'payment': '@evp', 'group': None, 'deleted': False,
                                 'payment_date': '2026-12-10T00:00:00Z', 'paid': False}
effect = {'kind': 'update', 'actor': actor, 'cs_changed': {}, 'cs_deleted': [], 'installments_deleted': ['ev_drop', 'ev_paid', 'ev_none'],
          'payments_deleted': ['ev_pd'], 'payments_changed': {'ev_pp': {'description': 'Rata n.3 del 01/11/26 (Corsa)', 'meta': paid_meta,
                                                                         'category': '$default_category'}},
          'created': created, 'audit': audit}
add('update-events', actor, one('cs_ev', 'update'), 200, item('cs_ev'), method='PATCH', body={
    'course': C('corsa'), 'subscription_id': S('sub_luisa'), 'multiple_quote': None, 'events': [
        {'course_subscription_installment_id': INSTALLMENTS['ev_keep']['id'], 'id': 0, 'amount': '20.00', 'payment_date': '2026-10-01'},
        {'id': 4, 'amount': '35.00', 'payment_date': '10/12/2026'}]}, effect=effect)
# Atomic: an invalid new event (400) or an event without an id compared with an installment (KeyError, 500) leaves nothing behind.
add('update-events-invalid-new-event', 'owner', one('cs_ev2', 'update'), 400,
    {'payment_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'], 'amount': ['A valid number is required.']},
    method='PATCH', body={'events': [{'id': 0, 'amount': '10.00', 'payment_date': '01/10/2026'}, {'id': 1, 'amount': 'dieci', 'payment_date': '2026-10-01'}]})
add('update-events-without-id-server-error', 'owner', one('cs_ev2', 'update'), 500, SERVER, method='PATCH', body={'events': [{'amount': '1'}]})
add('update-events-number-server-error', 'owner', one('cs_ev2', 'update'), 500, SERVER, method='PATCH', body={'events': 3})
# An empty selection removes every installment of the row.
audit = dropped('ev2_only', 'full_collaborator')
del STATE['installments']['ev2_only']
add('update-events-empty', 'full_collaborator', one('cs_ev2', 'update'), 200, item('cs_ev2'), method='PATCH', body={'events': []},
    effect={'kind': 'update', 'actor': 'full_collaborator', 'cs_changed': {}, 'cs_deleted': [], 'installments_deleted': ['ev2_only'],
            'payments_deleted': [], 'payments_changed': {}, 'audit': audit})

# --- delete ----------------------------------------------------------------
add('delete-anonymous', 'anonymous', one('cs_del', 'delete'), 401, ANONYMOUS, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', one('cs_del', 'delete'), 403, missing('delete'), method='DELETE')
add('delete-owner-scoped-athlete-denied', 'owner', one('cs_del', 'delete'), 403, SCOPED_DENIED, method='DELETE', headers=SCOPED)
add('delete-wrong-method', 'owner', one('cs_del', 'delete'), 405, method('GET'))
add('delete-options', 'owner', one('cs_del', 'delete'), 200, OPTIONS, method='OPTIONS')
add('delete-malformed-server-error', 'owner', one('abc', 'delete'), 500, SERVER, method='DELETE')
add('delete-unknown-not-found', 'owner', one(UNKNOWN, 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-foreign-not-found', 'owner', one('cs_abroad', 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-athlete-not-association', 'athlete', one('cs_del', 'delete'), 403, NOT_ASSOCIATION, method='DELETE')
add('delete-owner-without-association-server-error', 'owner_without_association', one('cs_del', 'delete'), 500, SERVER, method='DELETE')
add('delete-owner-with-installments', 'owner', one('cs_del', 'delete'), 204, '<empty>', method='DELETE', effect=soft_delete('owner', 'cs_del'))
add('delete-again-already-deleted', 'owner', one('cs_del', 'delete'), 204, '<empty>', method='DELETE', effect=soft_delete('owner', 'cs_del'))
add('delete-collaborator-unpaid-installment', 'full_collaborator', one('cs_del2', 'delete'), 204, '<empty>', method='DELETE',
    effect=soft_delete('full_collaborator', 'cs_del2'))
add('delete-admin-impersonating-owner', 'admin', one('cs_del3', 'delete'), 204, '<empty>', method='DELETE', headers=ADMIN_OWNER,
    effect=soft_delete('admin', 'cs_del3'))
add('delete-group-installments', 'owner', one('cs_del4', 'delete'), 204, '<empty>', method='DELETE', headers=GROUP,
    effect=soft_delete('owner', 'cs_del4', 'own_group'))
add('update-route-delete-deleted-not-found', 'owner', one('cs_del', 'update'), 404, NOT_FOUND, method='DELETE')
add('update-route-delete-deletes', 'owner', one('cs_del5', 'update'), 204, '<empty>', method='DELETE', effect=soft_delete('owner', 'cs_del5'))
add('bulk-route-patch-not-allowed', 'owner', BULK, 405, method('PATCH'), method='PATCH', body={})

# --- bulk-delete -------------------------------------------------------------
add('bulk-anonymous', 'anonymous', BULK, 401, ANONYMOUS, method='DELETE', body={})
add('bulk-restricted-missing-permission', 'restricted_collaborator', BULK, 403, missing('delete'), method='DELETE',
    body={'course_subscription_ids': []})
add('bulk-owner-scoped-athlete-denied', 'owner', BULK, 403, SCOPED_DENIED, method='DELETE', body={}, headers=SCOPED)
add('bulk-wrong-method', 'owner', BULK, 405, method('POST'), method='POST', body={})
add('bulk-options', 'owner', BULK, 200, OPTIONS, method='OPTIONS')
add('bulk-athlete-not-association', 'athlete', BULK, 403, NOT_ASSOCIATION, method='DELETE', body={'course_subscription_ids': []})
add('bulk-owner-without-association-server-error', 'owner_without_association', BULK, 500, SERVER, method='DELETE', body={})
add('bulk-list-body-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body=[CS['cs_bd3']['id']])
add('bulk-malformed-id-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body={'course_subscription_ids': [CS['cs_bd3']['id'], 'abc']})
add('bulk-string-ids-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body={'course_subscription_ids': CS['cs_bd3']['id']})
add('bulk-null-ids-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body={'course_subscription_ids': None})
add('bulk-form-id-server-error', 'owner', BULK, 500, SERVER, method='DELETE', raw_body=f'course_subscription_ids={CS["cs_bd3"]["id"]}',
    content_type='application/x-www-form-urlencoded')
add('bulk-empty-body', 'owner', BULK, 204, '<empty>', method='DELETE', body={}, effect=nothing('owner'))
add('bulk-integer-ids', 'owner', BULK, 204, '<empty>', method='DELETE', body={'course_subscription_ids': [1, 0]}, effect=nothing('owner'))
add('bulk-foreign-and-deleted-ignored', 'owner', BULK, 204, '<empty>', method='DELETE',
    body={'course_subscription_ids': [CS['cs_abroad']['id'], CS['cs_deleted']['id'], UNKNOWN]}, effect=nothing('owner'))
add('bulk-scoped-without-subscription-ignored', 'owner', BULK, 204, '<empty>', method='DELETE',
    body={'course_subscription_ids': [CS['cs_bd3']['id']]}, headers=OWNER_FULL, effect=nothing('owner'))


def bulk(actor, labels):
    """QuerySet.delete(): pre_delete for the installments (base manager, pk
    order) then the course subscriptions (delete_related_course_payment: for a
    payment-less row a membership's unpaid live payments, then its remaining
    live installments); deletes in reversed pk order."""
    rows = sorted(labels, key=lambda k: STATE['cs'][k]['id'])
    by_id = lambda k: STATE['installments'][k]['id']
    gone = sorted((k for k, i in STATE['installments'].items() if i['cs'] in rows), key=by_id)
    audit, payments, removed = [], [], set()
    for key in gone:
        entries = unpaid_installment(key, actor)
        audit += entries
        payments += [e['label'] for e in entries if e['model'] == 'payment'][:1]
        if entries:
            # Payment's pre_delete deleted the installment with its payment.
            removed.add(key)
    for label in rows:
        row = STATE['cs'][label]
        if row['payment'] is not None:
            continue
        if row['type'] == 3:
            unpaid = sorted((p for p in row['membership_payments'] if not STATE['payments'][p]['paid'] and not STATE['payments'][p]['deleted']),
                            key=lambda p: STATE['payments'][p]['id'])
            audit += [deleted('payment', p, PAYMENT_INDEX) for p in reversed(unpaid)]
            payments += unpaid
        live = [k for k in gone if STATE['installments'][k]['cs'] == label and not STATE['installments'][k]['deleted'] and k not in removed]
        audit += [deleted('coursesubscriptioninstallment', key, actor_index(actor)) for key in reversed(live)]
    audit += [deleted('coursesubscriptioninstallment', key, actor_index(actor)) for key in reversed(gone)]
    audit += [deleted('coursesubscription', label, actor_index(actor)) for label in reversed(rows)]
    for key in gone:
        del STATE['installments'][key]
    for label in rows:
        del STATE['cs'][label]
    return {'kind': 'bulk', 'actor': actor, 'cs_changed': {}, 'cs_deleted': rows, 'installments_deleted': gone, 'payments_deleted': payments,
            'payments_changed': {}, 'audit': audit}


add('bulk-owner', 'owner', BULK, 204, '<empty>', method='DELETE',
    body={'course_subscription_ids': [CS['cs_bd2']['id'], CS['cs_bd1']['id'].upper(), CS['cs_abroad']['id'], CS['cs_deleted']['id']]},
    effect=bulk('owner', ['cs_bd1', 'cs_bd2']))
# A payment-less row: the signal deletes its live installments (payment-less and grouped
# ones) after the collected installment with an unpaid payment went with that payment;
# the collector then deletes every collected installment again.
add('bulk-row-without-payment-installments', 'owner', BULK, 204, '<empty>', method='DELETE',
    body={'course_subscription_ids': [CS['cs_bd4']['id']]}, effect=bulk('owner', ['cs_bd4']))
add('list-after-writes', 'owner', f'{LIST}?include_deleted=true&course_id={C("yoga")}', 200,
    listing(*[k for k in own_rows(True) if STATE['cs'][k]['course'] == 'yoga']))
add('list-after-membership-writes', 'owner', f'{LIST}?subscription_id={S("sub_luisa")}&course_id={C("palestra")}', 200,
    listing('cs_member', 'cs_dup', 'cs_mismatch', 'cs_recover', 'cs_badmeta'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course subscriptions list, update, delete and bulk-delete authored sequence with '
                                                            'installment, payment and audit effects',
                              'ids': IDS, 'category': CATEGORY, 'document': DOCUMENT, 'associates': ASSOCIATES, 'medicals': MEDICALS,
                              'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'payments': PAYMENTS, 'course_subscriptions': CS,
                              'installments': INSTALLMENTS, 'carnets': CARNETS, 'holdings': HOLDINGS, 'instructor': INSTRUCTOR,
                              'tags': TAGS, 'subscription_tags': SUBSCRIPTION_TAGS,
                              'invoice': INVOICE, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
