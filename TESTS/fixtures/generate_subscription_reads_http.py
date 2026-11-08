"""Author the subscription list, list/all, list/archived, info and calendar HTTP sequence (subscription-reads.md).

All five views are function views behind IsAuthenticated (subscription_calendar
also IsAssociationUser | IsAthleteUser, which every authenticated role passes).
Registry: ``association.members.read`` (GET and POST ``subscription/list``,
``subscription/list/all``, ``subscription/*/info``, ``subscription/*/calendar``)
and ``association.members.archive.read`` (``subscription/list/archived``).
subscription_list, subscription_list_all, subscription_info and
subscription_calendar are athlete views for association-scoped sessions;
subscription_list_archived is not. ``Subscription.objects`` is group-aware
(``X-Group-Id``) and does not hide soft-deleted rows.

subscription_list (GET, POST):
- ``field`` (truthy): the association's (``sport_association__user``)
  unarchived subscriptions, excluding the ones whose associate has no tutor
  with email and phone (four separate EXISTS) while its own email or phone is
  empty or null; ``values_list('associate__<field>').distinct()`` (unordered);
  an unknown field is a FieldError (500).
- athletes: their own branch (not ported in Go; not authored).
- the scoped live association (500 without one); ``m`` absent: the
  unarchived subscriptions by ``-creation_date`` with the tag, age, hide,
  status, current-year, search, type-of-associate, certificate, payment and
  type filters and the sorting; POST then prints (``current_view_subscriptions``:
  a missing or unknown ``format`` is a 500); GET paginates (KTDatatable) with
  SubscriptionFastOptimizedSerializer. ``m`` present: the basic filters, then
  ``filter`` (absent: accepted only; ``all``: any; else the status), as
  SubscriptionFastBasicSerializer rows keyed by index, ``meta`` {}.

subscription_list_all (GET): ``preregistration=1`` lists the newest
non-renewed subscription per associate (name and tax code), keyed by the
row's index in the ordered queryset; otherwise the athlete's subscriptions of
``sport_association`` (403 without it) or the association's unarchived ones
(unordered), with ``current_year``, ``type`` and ``course_id`` filters; a row
without associate is a 500.

subscription_list_archived (GET): athletes get an empty 401; the scoped live
association; archived subscriptions by ``-creation_date``, smart search,
sorting, KTDatatable pages and SubscriptionSerializer plus the balance-year
``current_year``/``next_years``, ``renewal_available`` and
``medical_document``.

subscription_info (GET): is_valid_uuid (400); athletes: their own and same
tax code subscriptions (403 ``Subscription not found.``), the subscription's
live association; others the scoped live association; the group-aware
subscription (404 ``{'msg': 'Subscription not found.'}``, another
association 403 ``User not allowed.``); the live course subscriptions
(CourseDetailSerializer plus CourseSubscriptionOverviewSerializer and, for
multi-payment ones, the installments) sorted by course creation date
(descending, as strings); SubscriptionInfoSerializer. A subscription without
document_pdf dispatches the Celery print task (not ported in Go; not
authored).

subscription_calendar (GET): is_valid_uuid; the athlete's or the
association's group-aware subscription (none: no events); the registries of
its live course subscriptions' courses; each event as ``{title: "<title>
(<course title>)", start, end, allDay}`` (a missing key is a 500).

Expected bodies come from a model of those semantics over the authored rows.
Day counts, ages and medical labels are tokens the contract resolves on the
run date; the authored dates keep every other value stable through 2029.
Personal data is synthetic. Run with ``python3 -I``.
"""
import copy
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, FOREIGN_OWNER, ATHLETE = (IDS['own_association'], IDS['foreign_association'], IDS['owner'],
                                               IDS['foreign_owner'], IDS['athlete'])
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
SERVER = '<stable-server-error-page>'
# Letters only after the fixed version and variant digits: numeric search words never match a seeded id.
_letters = iter(f'{a}{b}{c}' for a in 'abcdef' for b in 'abcdef' for c in 'abcdef')


def uid(kind):
    return f'{kind}-eeee-4eee-8eee-eeeeeeeee{next(_letters)}'


USERS = {
    OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
            'username': 'message-reads-700001', 'role': 1},
    FOREIGN_OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700007@example.test', 'phone': None,
                    'username': 'message-reads-700007', 'role': 1},
    ATHLETE: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700004@example.test', 'phone': None,
              'username': 'message-reads-700004', 'role': 2},
}

DOCUMENTS = {label: {'id': uid('d0c0d0c0'), 'token': uid('70ce70ce'), 'filename': f'{label}.pdf', 'filepath': f'docs/{label}.pdf',
                     'size': 1024, 'created': '2025-09-01T08:00:00Z'}
             for label in ('pdf_mario', 'pdf_old', 'pdf_giulia', 'pdf_athlete', 'pdf_foreign', 'pdf_arch', 'medical', 'medical_undated',
                           'file')}
DOCUMENTS['file']['filename'] = 'modulo privacy è.pdf'
DOCUMENTS['file']['created'] = '2025-09-20T08:00:00.500000Z'
MEDICALS = {'valid': {'id': uid('3ed1ca1e'), 'expiration': '2031-06-30', 'competitive': True, 'document': 'medical'},
            'expired': {'id': uid('3ed1ca1e'), 'expiration': '2024-01-31', 'competitive': False, 'document': None},
            'undated': {'id': uid('3ed1ca1e'), 'expiration': None, 'competitive': False, 'document': 'medical_undated'}}
TAGS = {'agonisti': {'id': uid('7a6b7a6b'), 'name': 'Agonisti', 'association': OWN},
        'master': {'id': uid('7a6b7a6b'), 'name': 'Master', 'association': OWN},
        'estero': {'id': uid('7a6b7a6b'), 'name': 'Estero', 'association': FOREIGN}}

ASSOCIATE_DEFAULTS = {'picture_path': None, 'first_name': None, 'last_name': None, 'sex': 'A', 'tax_code': None, 'born_date': None,
                      'born_city': None, 'address_city': None, 'address': None, 'address_cap': None, 'nationality': 'Italiana',
                      'nationality_residence': 'Italia', 'email': None, 'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None,
                      'phone_label': None, 'phone_2_label': None, 'phone_3_label': None, 'phone_4_label': None, 'is_minor': False,
                      'disabled': False, 'draft': False, 'deleted': False, 'family_role': None, 'id_number': None, 'migrated': False,
                      'notes': None, 'user': None, 'association': OWN, 'group': None, 'family': None}


def person(created, **values):
    return {**ASSOCIATE_DEFAULTS, **values, 'id': uid('decafbad'), 'created': created}


ASSOCIATES = {
    'mario': person('2023-08-01T08:00:00Z', first_name='Mario', last_name='Rossi', sex='M', tax_code='RSSMRA90E10H501Z',
                    born_date='1990-05-10', born_city='Verona', address_city='Verona', address='Via Mazzini 3', address_cap='37121',
                    email='mario@example.test', phone='3331112222', phone_label='casa', notes='nuoto libero',
                    picture_path='https://cdn.example.test/mario.jpg'),
    # Another association's associate with mario's tax code in lower case: renewals match it case-insensitively.
    'twin': person('2023-08-02T08:00:00Z', first_name='Marco', last_name='Russo', sex='M', tax_code='rssmra90e10h501z',
                   born_date='1991-07-21', email='marco@example.test', phone='3330000001', association=FOREIGN),
    'giulia': person('2023-08-03T08:00:00Z', first_name='Giulia', last_name='Verdi', sex='F', tax_code='VRDGLI16C55L781X',
                     born_date='2016-03-15', is_minor=True, email='', phone=None, address_city='Padova'),
    'lucia': person('2023-08-04T08:00:00Z', first_name='Lucia', last_name='Bianchi', sex='F', born_date='1965-02-14',
                    email='lucia@example.test', phone='3405556666', id_number='CA12345'),
    'baby': person('2023-08-05T08:00:00Z', first_name='Bimbo', last_name='Neri', sex='M', tax_code='NREBMB23H01L781Y',
                   born_date='2023-06-01', is_minor=True),
    'nobirth': person('2023-08-06T08:00:00Z', first_name='Senza', last_name='Data', tax_code='SNZDTA00A01L781Z',
                      email='senza@example.test', phone='3471234567'),
    'paolo': person('2023-08-07T08:00:00Z', first_name='Paolo', last_name='Gallo', sex='M', tax_code='GLLPLA63P30L781Q',
                    born_date='1963-09-30', group=OWN_GROUP),
    'atleta': person('2023-08-08T08:00:00Z', first_name='Anna', last_name='Atleta', sex='F', tax_code='TLTNNA80A41L781A',
                     born_date='1980-01-01', email='anna@example.test', phone='3470000000', user=ATHLETE),
    'abroad': person('2023-08-09T08:00:00Z', first_name='Hugo', last_name='Esterno', sex='M', tax_code='STRHGU80A01Z404X',
                     born_date='1980-01-01', association=FOREIGN),
}
# associate -> [(tutor, is_primary)]
TUTORS = {'giulia': [('lucia', True)]}
A = {label: row['id'] for label, row in ASSOCIATES.items()}

PAYMENT_DEFAULTS = {'amount': '0.00', 'paid': False, 'archived': False, 'deleted': False, 'group': None, 'associate': None,
                    'association': OWN, 'paid_on': None}


def payment(created, **values):
    return {**PAYMENT_DEFAULTS, **values, 'id': uid('ba1dface'), 'created': created}


PAYMENTS = {
    'sub': payment('2025-09-02T09:00:00Z', amount='50.00', paid=True, associate='mario', paid_on='2025-09-02T10:00:00.250000Z'),
    'course': payment('2025-09-03T09:00:00Z', amount='120.00', associate='mario'),
    'archived': payment('2025-09-04T09:00:00Z', amount='5.00', associate='mario', archived=True),
    'removed': payment('2025-09-05T09:00:00Z', amount='7.00', associate='mario', deleted=True),
    'grouped': payment('2025-09-06T09:00:00Z', amount='10.00', associate='mario', group=OWN_GROUP),
    'twin': payment('2025-09-07T09:00:00Z', amount='15.50', paid=True, associate='twin', association=FOREIGN),
    'installment': payment('2025-10-01T09:00:00Z', amount='40.00', paid=True, paid_on='2025-10-01T09:00:00Z'),
}

SUB_DEFAULTS = {'association': OWN, 'user': OWNER, 'associate': None, 'start': '2025-09-01', 'end': '2030-08-31', 'status': 4, 'type': 2,
                'role': 1, 'payment': None, 'medical': None, 'document': None, 'tags': [], 'custom_data': None, 'additional_fields': None,
                'meta': None, 'signature_url': None, 'signature_storage_key': None, 'number': '0', 'subscription_type': None,
                'competitive': False, 'notes': None, 'acceptance_date': None, 'resignation_date': None, 'draft': True, 'trial': False,
                'archived': False, 'deleted': False, 'group': None}


def sub(created, **values):
    return {**SUB_DEFAULTS, **values, 'id': uid('5ab5c0de'), 'created': created}


SUBSCRIPTIONS = {
    'cur_mario': sub('2025-09-01T08:00:00Z', associate='mario', payment='sub', medical='valid', document='pdf_mario',
                     tags=['agonisti', 'master'], custom_data={'tipo': 'agonista'}, additional_fields={'taglia': 'M'},
                     meta={'origine': 'sportello'}, signature_url='https://cdn.example.test/firma.png', number='12',
                     subscription_type='annuale', competitive=True, notes='iscrizione completa', acceptance_date='2025-09-02',
                     draft=False),
    'old_mario': sub('2023-09-01T08:00:00Z', associate='mario', start='2023-09-01', end='2024-08-31', document='pdf_old'),
    'next_twin': sub('2025-10-01T08:00:00Z', associate='twin', start='2031-09-01', end='2032-08-31', status=2, type=3,
                     signature_storage_key='private/firma.png', resignation_date='2032-01-01'),
    'giulia': sub('2025-09-05T08:00:00Z', associate='giulia', status=1, type=1, document='pdf_giulia', tags=['agonisti'],
                  custom_data={'tipo': 'promozionale'}, role=2),
    'baby': sub('2025-09-06T08:00:00Z', associate='baby', medical='undated'),
    'nobirth': sub('2025-09-07T08:00:00Z', associate='nobirth', start=None, end=None, status=5, medical='expired', signature_url=''),
    'deleted': sub('2025-09-08T08:00:00Z', associate='lucia', deleted=True, type=3),
    'grouped': sub('2025-09-09T08:00:00Z', associate='paolo', group=OWN_GROUP),
    'athlete': sub('2025-09-10T08:00:00Z', associate='atleta', user=ATHLETE, document='pdf_athlete'),
    'arch_mario': sub('2022-09-01T08:00:00Z', associate='mario', start='2022-09-01', end='2023-08-31', archived=True, document='pdf_arch',
                      medical='valid', tags=['master']),
    'arch_lucia': sub('2021-01-10T08:00:00Z', associate='lucia', start='2021-01-01', end='2021-12-31', archived=True, medical='expired'),
    'arch_late': sub('2024-12-31T15:00:00Z', associate='twin', start='2024-01-01', end='2024-12-31', archived=True, status=5),
    'arch_none': sub('2020-03-01T08:00:00Z', start='2020-01-01', end='2020-12-31', archived=True),
    'arch_group': sub('2021-02-01T08:00:00Z', associate='lucia', start='2021-02-01', end='2021-06-30', archived=True, group=OWN_GROUP),
    'abroad': sub('2025-09-11T08:00:00Z', association=FOREIGN, user=FOREIGN_OWNER, associate='abroad', document='pdf_foreign',
                  tags=['estero']),
    'foreign_none': sub('2025-09-12T08:00:00Z', association=FOREIGN, user=FOREIGN_OWNER),
    'athlete_foreign': sub('2025-09-13T08:00:00Z', association=FOREIGN, user=ATHLETE, associate='abroad', status=2,
                           document='pdf_foreign'),
}
S = {label: row['id'] for label, row in SUBSCRIPTIONS.items()}

COURSES = {
    'swim': {'id': uid('c0a5ec0a'), 'title': 'Corso nuoto', 'created': '2024-01-10T09:00:00Z', 'multi': False, 'fee': '50.00',
             'deleted': False, 'events': None},
    'gym': {'id': uid('c0a5ec0a'), 'title': 'Corso palestra', 'created': '2024-02-10T09:00:00.500000Z', 'multi': True, 'fee': '80.00',
            'deleted': True, 'events': [{'title': 'Lunedi'}]},
    'broken': {'id': uid('c0a5ec0a'), 'title': None, 'created': '2024-03-10T09:00:00Z', 'multi': False, 'fee': '10.00', 'deleted': False,
               'events': None},
    'other': {'id': uid('c0a5ec0a'), 'title': 'Corso altro', 'created': '2024-04-10T09:00:00Z', 'multi': False, 'fee': '10.00',
              'deleted': False, 'events': None},
}
COURSE_SUBS = {
    'swim': {'id': uid('c5c5c5c5'), 'course': 'swim', 'subscription': 'cur_mario', 'paid': True, 'multi': False,
             'created': '2025-09-03T08:00:00Z', 'deleted': False},
    'gym': {'id': uid('c5c5c5c5'), 'course': 'gym', 'subscription': 'cur_mario', 'paid': False, 'multi': True,
            'created': '2025-09-04T08:00:00Z', 'deleted': False},
    'gone': {'id': uid('c5c5c5c5'), 'course': 'other', 'subscription': 'cur_mario', 'paid': False, 'multi': False,
             'created': '2025-09-05T08:00:00Z', 'deleted': True},
    'broken': {'id': uid('c5c5c5c5'), 'course': 'broken', 'subscription': 'nobirth', 'paid': False, 'multi': False,
               'created': '2025-09-06T08:00:00Z', 'deleted': False},
    'giulia': {'id': uid('c5c5c5c5'), 'course': 'swim', 'subscription': 'giulia', 'paid': False, 'multi': False,
               'created': '2025-09-07T08:00:00Z', 'deleted': False},
    # Without subscription: NOT IN (... NULL ...) excludes every row of list/all?course_id=<broken>.
    'orphan': {'id': uid('c5c5c5c5'), 'course': 'broken', 'subscription': None, 'paid': False, 'multi': False,
               'created': '2025-09-08T08:00:00Z', 'deleted': False},
}
INSTALLMENTS = {
    'first': {'id': uid('11a5aaaa'), 'cs': 'gym', 'number': 0, 'amount': '40.00', 'paid': False, 'payment': 'installment',
              'when': '2025-10-01T08:00:00Z', 'deleted': False, 'group': None},
    'second': {'id': uid('11a5aaaa'), 'cs': 'gym', 'number': 1, 'amount': '40.00', 'paid': True, 'payment': None,
               'when': '2025-11-01T08:00:00.250000Z', 'deleted': False, 'group': None},
    'removed': {'id': uid('11a5aaaa'), 'cs': 'gym', 'number': 2, 'amount': '40.00', 'paid': False, 'payment': None,
                'when': '2025-12-01T08:00:00Z', 'deleted': True, 'group': None},
}
REGISTRIES = {
    'swim': {'id': uid('7e617e61'), 'course': 'swim', 'events': [
        {'event_id': uid('e0e0e0e0'), 'title': 'Lezione', 'start': '2025-10-01T18:00:00', 'end': '2025-10-01T19:00:00', 'allDay': False,
         'extendedProps': {'description': 'Vasca grande'}},
        {'event_id': uid('e0e0e0e0'), 'title': 'Recupero', 'start': '2025-10-08', 'end': None, 'allDay': True}]},
    'gym': {'id': uid('7e617e61'), 'course': 'gym', 'events': None},
    'gym_more': {'id': uid('7e617e61'), 'course': 'gym', 'events': [{'title': 7, 'start': '2025-11-01T10:00:00Z', 'end': 3, 'allDay': 'si'}]},
    'other': {'id': uid('7e617e61'), 'course': 'other', 'events': [{'title': 'Nascosto', 'start': 'x', 'end': 'y', 'allDay': False}]},
    'broken': {'id': uid('7e617e61'), 'course': 'broken', 'events': [{'title': 'Senza giorno', 'start': 'x', 'end': 'y'}]},
}
FILES = {'privacy': {'id': uid('f11ef11e'), 'subscription': 'cur_mario', 'document': 'file', 'created': '2025-09-20T08:00:00Z'}}
CARD = {'id': uid('ca2dca2d'), 'association': OWN, 'emit_only_on_approval': True, 'customized_template': {'colore': 'blu', 'n': 2}}


# --- clock-independent helpers (the authored dates are far from the run date) --------------------------------------------
def is_current(row):
    return row['start'] is None or row['end'] is None or row['start'] <= '2026-11-01' <= row['end']


def is_next_year(row):
    return row['start'] is not None and row['end'] is not None and row['start'] > '2029-12-31'


def drf_time(text):
    if text is None:
        return None
    value = datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
    return value.strftime('%Y-%m-%dT%H:%M:%S') + ('.%06d' % value.microsecond if value.microsecond else '') + 'Z'


def age_token(label):
    born = ASSOCIATES[label]['born_date']
    return 0 if born is None else f'{{age:{born}}}'


def plain_label(sublabel):
    """Subscription.get_plain_medical_label(); the contract fills the day counts."""
    row = SUBSCRIPTIONS[sublabel]
    born = ASSOCIATES[row['associate']]['born_date']
    medical = MEDICALS[row['medical']] if row['medical'] else None
    if born is None:
        return 'Esente per 0 giorni'
    if born > '2021-01-01':
        return f'Esente per {{toage:{born}}}'
    if medical is None or medical['expiration'] is None:
        return f'Mancante da {{missing:{row["created"]}}}'
    if medical['expiration'] < '2026-01-01':
        return f'Scaduto da {{since:{medical["expiration"]}}}'
    return f'{{until:{medical["expiration"]}}} rimanenti'


def html_label(sublabel):
    """Subscription.get_medical_label()."""
    row = SUBSCRIPTIONS[sublabel]
    medical = MEDICALS[row['medical']] if row['medical'] else None
    ag = '<span class="badge badge-info ml-1" title="Certificato Medico Agonistico">AG</span>' if medical and medical['competitive'] else ''
    plain = plain_label(sublabel)
    if plain.startswith('Esente'):
        return f'<span class="text-success font-weight-boldest">{plain}</span>{ag}'
    if plain.startswith('Mancante'):
        return f'<span class="text-warning font-weight-boldest">{plain}</span>{ag}'
    icon = '<i class="ph-bold ph-first-aid-kit"></i>'
    if plain.startswith('Scaduto'):
        return f'<span class="text-danger font-weight-boldest">{icon}{plain}</span>{ag}'
    return f'<span class="text-success font-weight-boldest">{icon}{plain}</span>{ag}'


def live_payments(associate, group):
    """Payment.objects (live, group-aware) of the associate, archived=False."""
    return [p for p in PAYMENTS.values() if p['associate'] == associate and not p['deleted'] and not p['archived']
            and (group is None or p['group'] == group)]


def visible(row, group):
    return group is None or row['group'] == group


def future_renewal(sublabel, group):
    """future_renewal_exists(): same association, start >= end, unarchived, UPPER(tax) equal (group-aware candidates)."""
    row = SUBSCRIPTIONS[sublabel]
    tax = ASSOCIATES[row['associate']]['tax_code'] if row['associate'] else None
    if tax is None or row['end'] is None:
        return False
    return any(other['association'] == row['association'] and other['start'] is not None and other['start'] >= row['end']
               and not other['archived'] and other['associate'] and ASSOCIATES[other['associate']]['tax_code'] is not None
               and ASSOCIATES[other['associate']]['tax_code'].upper() == tax.upper() and visible(other, group)
               for other in SUBSCRIPTIONS.values())


def renewal_lookup(sublabel, group):
    """has_future_renewal() without a usable annotation: associate__tax_code__iexact=None is IS NULL."""
    row = SUBSCRIPTIONS[sublabel]
    if row['associate'] is None:
        return False
    tax = ASSOCIATES[row['associate']]['tax_code']
    if tax is not None:
        return future_renewal(sublabel, group)
    assert row['end'] is not None
    return any(other['association'] == row['association'] and other['start'] is not None and other['start'] >= row['end']
               and not other['archived'] and (other['associate'] is None or ASSOCIATES[other['associate']]['tax_code'] is None)
               and visible(other, group) for other in SUBSCRIPTIONS.values())


def tags_of(sublabel):
    return [{'tag_id': TAGS[t]['id'], 'tag_name': TAGS[t]['name']} for t in SUBSCRIPTIONS[sublabel]['tags']]


def info_associate(label):
    row = ASSOCIATES[label]
    keys = ('address', 'address_cap', 'address_city', 'born_city', 'born_date', 'email', 'first_name', 'last_name', 'is_minor', 'sex',
            'tax_code', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label', 'phone_4_label',
            'picture_path')
    return {'associate_id': row['id'], **{key: row[key] for key in keys}}


def medical_fields(row):
    medical = MEDICALS[row['medical']] if row['medical'] else None
    document = DOCUMENTS[medical['document']] if medical and medical['document'] else None
    return medical, document


# --- SubscriptionFastOptimizedSerializer --------------------------------------------------------------------------------
def fast(label, group=None):
    row = SUBSCRIPTIONS[label]
    medical, medical_document = medical_fields(row)
    document = DOCUMENTS[row['document']] if row['document'] else None
    payments = live_payments(row['associate'], group) if row['associate'] else []
    total = sum((Decimal(p['amount']) for p in payments), Decimal('0'))
    unpaid = sum((Decimal(p['amount']) for p in payments if not p['paid']), Decimal('0'))
    user = USERS[row['user']]
    linked = PAYMENTS[row['payment']] if row['payment'] else None
    return {
        'subscription_id': row['id'], 'sport_association': row['association'], 'creation_date': drf_time(row['created']),
        'start_date': row['start'], 'end_date': row['end'], 'status_flag': row['status'], 'type': row['type'], 'role': row['role'],
        'acceptance_date': row['acceptance_date'], 'resignation_date': row['resignation_date'], 'signature_url': row['signature_url'],
        'deleted': row['deleted'], 'archived': row['archived'], 'draft': row['draft'], 'trial': row['trial'], 'meta': row['meta'],
        'custom_data': row['custom_data'], 'additional_fields': row['additional_fields'], 'subscription_number': row['number'],
        'subscription_type': row['subscription_type'], 'competitive': row['competitive'], 'notes': row['notes'],
        'user': {'user_id': row['user'], 'username': user['username'], 'email': user['email'], 'first_name': user['first_name'],
                 'last_name': user['last_name'], 'role': user['role']},
        'associate': info_associate(row['associate']) if row['associate'] else None,
        'payment': {'payment_id': linked['id'], 'amount': float(linked['amount']), 'paid': linked['paid'],
                    'payment_date': drf_time(linked['paid_on'])} if linked else None,
        'tags': tags_of(label), 'all_payments_paid': not any(not p['paid'] for p in payments),
        'payments_info': {'total': float(total), 'to_be_paid': float(unpaid)},
        'age': age_token(row['associate']) if row['associate'] else None, 'current_year': is_current(row), 'next_years': is_next_year(row),
        'renewal_available': not is_current(row) and not future_renewal(label, group),
        'medical': medical['id'] if medical else None, 'medical_document': medical_document['id'] if medical_document else None,
        'medical_token': medical_document['token'] if medical_document else None,
        'medical_expiration_date': medical['expiration'] if medical else None,
        'document': document['id'] if document else None, 'document_token': document['token'] if document else None,
        'plain_medical_label': plain_label(label) if row['associate'] else None,
        'signature_present': bool(row['signature_storage_key'] or row['signature_url']),
    }


def by_created(labels):
    return sorted(labels, key=lambda k: SUBSCRIPTIONS[k]['created'], reverse=True)


def page(labels, *, group=None, number=1, size=10, ordered=None, render=fast):
    rows = ordered if ordered is not None else by_created(labels)
    total = len(rows)
    pages = max(1, -(-total // size))
    chunk = rows[(number - 1) * size:number * size]
    return {'data': {str(i): render(k, group) for i, k in enumerate(chunk)},
            'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': size, 'rowIds': [S[k] for k in chunk]}}


def own_listed(group=None, association=OWN):
    return [k for k, r in SUBSCRIPTIONS.items() if r['association'] == association and not r['archived'] and visible(r, group)]


# --- search model (sqlite_search over Subscription: one row per tutor relation) -------------------------------------------
PERSON_FIELDS = ('first_name', 'last_name', 'tax_code', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date',
                 'address_city', 'address', 'notes', 'nationality', 'id_number')


def jsonb_text(value):
    """jsonb output: keys by length then bytes, ': ' and ', ' separators."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '[' + ', '.join(jsonb_text(item) for item in value) + ']'
    keys = sorted(value, key=lambda key: (len(key.encode()), key.encode()))
    return '{' + ', '.join(json.dumps(key, ensure_ascii=False) + ': ' + jsonb_text(value[key]) for key in keys) + '}'


def ts_text(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
    rendered = value.strftime('%Y-%m-%d %H:%M:%S')
    if value.microsecond:
        rendered += ('.%06d' % value.microsecond).rstrip('0')
    return rendered + '+00'


def bool_text(value):
    return 'true' if value else 'false'


def sub_texts(row):
    """The concrete non-relation Subscription fields as field::text (name -> text)."""
    return {'subscription_id': row['id'], 'custom_data': None if row['custom_data'] is None else jsonb_text(row['custom_data']),
            'additional_fields': None if row['additional_fields'] is None else jsonb_text(row['additional_fields']),
            'type': str(row['type']), 'role': str(row['role']), 'acceptance_date': row['acceptance_date'],
            'resignation_date': row['resignation_date'], 'status_flag': str(row['status']), 'creation_date': ts_text(row['created']),
            'start_date': row['start'], 'end_date': row['end'], 'signature_url': row['signature_url'],
            'signature_storage_key': row['signature_storage_key'], 'archived': bool_text(row['archived']), 'draft': bool_text(row['draft']),
            'trial': bool_text(row['trial']), 'meta': None if row['meta'] is None else jsonb_text(row['meta']),
            'subscription_number': row['number'], 'subscription_type': row['subscription_type'], 'deleted': bool_text(row['deleted']),
            'competitive': bool_text(row['competitive']), 'notes': row['notes']}


def join_rows(row):
    base = dict(sub_texts(row))
    associate = row['associate']
    for key in PERSON_FIELDS:
        base[f'associate__{key}'] = ASSOCIATES[associate][key] if associate else None
    tutors = TUTORS.get(associate, []) if associate else []
    if not tutors:
        return [{**base, **{f'associate__tutors__{k}': None for k in PERSON_FIELDS}}]
    return [{**base, **{f'associate__tutors__{k}': ASSOCIATES[t][k] for k in PERSON_FIELDS}} for t, _ in tutors]


def icontains(value, needle):
    return value is not None and needle.upper() in value.upper()


def date_variations(term):
    for pattern in (r'(\d{1,2})[.](\d{1,2})[.](\d{2,4})', r'(\d{1,2})[/](\d{1,2})[/](\d{2,4})', r'(\d{1,2})[-](\d{1,2})[-](\d{2,4})'):
        match = re.search(pattern, term)
        if match:
            day, month, year = match.groups()
            full = (('20' if int(year) <= 26 else '19') + year) if len(year) == 2 else year
            return [f'{day}.{month}.{year}', f'{day}.{month}.{full}', f'{day}/{month}/{year}', f'{day}/{month}/{full}',
                    f'{day}-{month}-{year}', f'{day}-{month}-{full}', f'{full}-{month.zfill(2)}-{day.zfill(2)}',
                    f'{month}.{day}.{year}', f'{month}/{day}/{year}', f'{month}-{day}-{year}']
    return []


def smart_match(joined, raw):
    term = raw.strip().lower()
    words = re.findall(r'\w+', term)
    variations = date_variations(term)
    keys = list(joined)

    def word_ok(word):
        if any(icontains(joined[k], word) for k in keys):
            return True
        if re.match(r'^\d+$', word) or re.match(r'^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$', word):
            dated = [k for k in keys if 'date' in k or 'created' in k]
            return any(icontains(joined[k], v) for v in variations for k in dated)
        return False

    result = all(word_ok(word) for word in words)
    if len(words) > 1:
        perms = [' '.join(reversed(words)), ' '.join(words)]
        fields = [k for k in keys if any(t in k for t in ('name', 'address', 'city', 'notes'))]
        result = result or any(icontains(joined[k], perm) for perm in perms for k in fields)
    return result


def dmy(term):
    try:
        return datetime.strptime(term, '%d/%m/%Y').strftime('%Y-%m-%d')
    except ValueError:
        return term


def searched(labels, raw):
    term = dmy(raw)
    if not term:
        return list(labels)
    return [k for k in labels if any(smart_match(joined, term) for joined in join_rows(SUBSCRIPTIONS[k]))]


# --- SubscriptionSerializer (archived list) ------------------------------------------------------------------------------
def balance_year_end(created):
    """BalanceSheetData.get_range_from_year_and_starting_date with the owner's 1 January start: the end at 00:00 UTC."""
    moment = datetime.fromisoformat(created.replace('Z', '+00:00'))
    return datetime(moment.year, 12, 31, tzinfo=timezone.utc)


def archived_row(label, group=None):
    """Without an associate, _active_payments(None) is Payment.objects.filter(associate=None, archived=False)."""
    row = SUBSCRIPTIONS[label]
    medical, medical_document = medical_fields(row)
    document = DOCUMENTS[row['document']] if row['document'] else None
    payments = live_payments(row['associate'], group)
    end = balance_year_end(row['created'])
    created = datetime.fromisoformat(row['created'].replace('Z', '+00:00'))
    item = {
        'subscription_id': row['id'], 'user': USERS[row['user']], 'associate': info_associate(row['associate']) if row['associate'] else None,
        'tags': tags_of(label), 'all_payments_paid': not any(not p['paid'] for p in payments),
        'payments_info': {'total': float(sum((Decimal(p['amount']) for p in payments), Decimal('0'))),
                          'to_be_paid': float(sum((Decimal(p['amount']) for p in payments if not p['paid']), Decimal('0')))},
        'age': age_token(row['associate']) if row['associate'] else None,
        'medical': medical_document['id'] if medical_document else None, 'medical_token': medical_document['token'] if medical_document else None,
        'medical_expiration_date': medical['expiration'] if medical else None,
        'document': document['id'] if document else None, 'document_token': document['token'] if document else None,
        'signature_present': bool(row['signature_storage_key'] or row['signature_url']),
        'custom_data': row['custom_data'], 'additional_fields': row['additional_fields'], 'type': row['type'], 'role': row['role'],
        'acceptance_date': row['acceptance_date'], 'resignation_date': row['resignation_date'], 'status_flag': row['status'],
        'creation_date': drf_time(row['created']), 'start_date': row['start'], 'end_date': row['end'], 'signature_url': row['signature_url'],
        'archived': row['archived'], 'draft': row['draft'], 'trial': row['trial'], 'meta': row['meta'], 'subscription_number': row['number'],
        'subscription_type': row['subscription_type'], 'deleted': row['deleted'], 'competitive': row['competitive'], 'notes': row['notes'],
        'sport_association': row['association'], 'supplier': None, 'instructor': None,
        'document_pdf': document['id'] if document else None, 'payment': PAYMENTS[row['payment']]['id'] if row['payment'] else None,
        'group': row['group'],
        # view additions
        'current_year': False, 'next_years': created > end,
    }
    item['renewal_available'] = not renewal_lookup(label, group)
    if medical_document:
        item['medical_document'] = medical_document['id']
    return item


def archived_listed(group=None):
    return [k for k, r in SUBSCRIPTIONS.items() if r['association'] == OWN and r['archived'] and visible(r, group)]


def archived_page(labels, *, group=None, number=1, size=10, ordered=None):
    return page(labels, group=group, number=number, size=size, ordered=ordered, render=archived_row)


# --- list/all ------------------------------------------------------------------------------------------------------------
def all_item(label, season=True):
    row = SUBSCRIPTIONS[label]
    associate = ASSOCIATES[row['associate']]
    item = {'associate': {'first_name': associate['first_name'], 'last_name': associate['last_name'], 'associate_id': associate['id']},
            'subscription_id': row['id'], 'current_year': is_current(row)}
    if season:
        item['season'] = (None if row['start'] is None or row['end'] is None else
                          row['start'][:4] if row['start'][:4] == row['end'][:4] else f"{row['start'][:4]}/{row['end'][:4]}")
    return item


def all_body(labels):
    return {'data': {str(i): all_item(k) for i, k in enumerate(labels)}, 'meta': {}}


def full_name_key(label):
    row = SUBSCRIPTIONS[label]
    associate = ASSOCIATES[row['associate']]
    return f"{('%s %s' % (associate['first_name'], associate['last_name'])).strip().lower()}__{associate['tax_code']}"


def preregistration(labels):
    """The newest non-renewed subscription per name and tax code, keyed by its index in the ordered rows."""
    def key(k):
        associate = ASSOCIATES[SUBSCRIPTIONS[k]['associate']]
        # ORDER BY first_name, last_name, tax_code (NULLS LAST), -creation_date
        return (associate['first_name'], associate['last_name'], (associate['tax_code'] is None, associate['tax_code'] or ''),
                [-ord(c) for c in SUBSCRIPTIONS[k]['created']])
    ordered = sorted(labels, key=key)
    renewed = {full_name_key(k) for k in ordered if is_next_year(SUBSCRIPTIONS[k])}
    data, added = {}, set()
    for index, k in enumerate(ordered):
        name = full_name_key(k)
        if name in renewed or name in added:
            continue
        data[str(index)] = all_item(k, season=False)
        added.add(name)
    return {'data': data}


# --- subscription_info ---------------------------------------------------------------------------------------------------
def persona_model(label):
    row = ASSOCIATES[label]
    keys = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date', 'born_city', 'address_city', 'address', 'address_cap',
            'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label',
            'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role', 'id_number')
    return {**{key: row[key] for key in keys}, 'creation_date': drf_time(row['created']), 'migrated': row['migrated'], 'notes': row['notes'],
            'user': row['user'], 'sport_association': row['association'], 'group': row['group'], 'family': row['family']}


def full_name(label):
    row = ASSOCIATES[label]
    return ('%s %s' % (row['first_name'], row['last_name'])).strip()


def basic_associate(label):
    """BasicAssociateSerializer: tutors through the M2M manager (BasicTutorsSerializer with their own tutors' keys)."""
    tutors = [{'associate_id': A[t], 'full_name': full_name(t), **persona_model(t), 'tutors': [A[x] for x, _ in TUTORS.get(t, [])]}
              for t, _ in TUTORS.get(label, [])]
    return {'associate_id': A[label], 'full_name': full_name(label), 'tutors': tutors, **persona_model(label)}


def persona(label):
    """personas AssociateSerializer on a plain instance: is_tutor is the relation lookup."""
    tutors = [{'tutor': basic_associate(t), 'is_primary': primary} for t, primary in TUTORS.get(label, [])]
    is_tutor = any(label == t for pairs in TUTORS.values() for t, _ in pairs)
    born = ASSOCIATES[label]['born_date']
    return {'associate_id': A[label], 'full_name': full_name(label), 'is_minor_now': True if born is None else f'{{minor:{born}}}',
            'is_tutor': is_tutor, 'age': age_token(label), 'family_members': [], 'tutors': tutors, **persona_model(label)}


def course_detail(label):
    row = COURSES[label]
    return {'course_id': row['id'], 'status_flag': 2, 'course_type': 1, 'creation_date': drf_time(row['created']), 'title': row['title'],
            'description': None, 'multi_payments': row['multi'], 'fee': row['fee'], 'events': row['events'], 'one_fee_payment': False,
            'one_fee': '0.00', 'google_calendar_id': None, 'google_background_color': '#3a87ad', 'multiple_quotes': None,
            'start_date': None, 'end_date': None, 'pinned': False, 'billed_duration_is_sport_season': False, 'billed_frequency': 1,
            'billed_from_subscription_date': False, 'billed_from_day_of_month': 1, 'auto_renewal': False, 'deleted': row['deleted'],
            'sport_association': OWN, 'group': None, 'tags': [], 'locations': []}


def simplify_associate(label):
    """user_serializers.AssociateSerializer (the Overview's subscription.associate)."""
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], 'address': row['address'], 'address_cap': row['address_cap'], 'address_city': row['address_city'],
            'born_city': row['born_city'], 'born_date': row['born_date'], 'email': row['email'], 'sex': row['sex'],
            'first_name': row['first_name'], 'last_name': row['last_name'], 'is_minor': row['is_minor'],
            'sport_association': row['association'], 'tax_code': row['tax_code'],
            'user': row['user'] or (OWNER if row['association'] == OWN else FOREIGN_OWNER), 'phone': row['phone'], 'family': row['family'],
            'family_role': row['family_role']}


def current_year_text(row):
    if row['start'] is None or row['end'] is None or is_current(row):
        return 'corrente'
    return 'precedente' if row['end'] < '2026-01-01' else 'successivo'


def overview(cs_label, group=None):
    row = COURSE_SUBS[cs_label]
    sub_row = SUBSCRIPTIONS[row['subscription']]
    installments = [{'course_subscription_installment_id': i['id'], 'id': i['number'], 'amount': i['amount'], 'payment_date': i['when'][:10],
                     'paid': bool(i['payment']) and PAYMENTS[i['payment']]['paid']}
                    for i in INSTALLMENTS.values() if i['cs'] == cs_label and not i['deleted'] and (group is None or i['group'] == group)]
    return {'course_subscription_id': row['id'], 'creation_date': drf_time(row['created']), 'paid': row['paid'],
            'subscription': {'subscription_id': sub_row['id'], 'associate': simplify_associate(sub_row['associate']),
                             'current_year': current_year_text(sub_row)},
            'multi_payments': row['multi'], 'multiple_quote': None, 'one_fee_payment': False, 'installments': installments,
            'course': COURSES[row['course']]['id'], 'type': 1, 'billed_frequency': 1, 'billed_from': None, 'billed_until': None,
            'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': '0.00', 'auto_renewal': False,
            'membership_payments': [], 'medical_label': html_label(row['subscription']), 'carnets': []}


def info_courses(sublabel, group=None):
    courses = []
    for k, cs in COURSE_SUBS.items():
        if cs['subscription'] != sublabel or cs['deleted']:
            continue
        item = course_detail(cs['course'])
        item['course_subscription'] = overview(k, group)
        if cs['multi']:
            item['installments'] = [{'course_subscription_installment_id': i['id'], 'payment_id': PAYMENTS[i['payment']]['id'] if i['payment'] else None,
                                     'amount': float(i['amount']), 'payment_date': drf_time(i['when']),
                                     'paid': PAYMENTS[i['payment']]['paid'] if i['payment'] else i['paid']}
                                    for i in INSTALLMENTS.values() if i['cs'] == k and not i['deleted'] and (group is None or i['group'] == group)]
        courses.append(item)
    return sorted(courses, key=lambda c: c['creation_date'], reverse=True)


def info_body(sublabel, group=None):
    row = SUBSCRIPTIONS[sublabel]
    medical, medical_document = medical_fields(row)
    document = DOCUMENTS[row['document']]
    card = ({'emit_only_on_approval': CARD['emit_only_on_approval'], 'customized_template': CARD['customized_template']}
            if row['association'] == CARD['association'] else {'emit_only_on_approval': False, 'customized_template': {}})
    files = [{'subscription_file_id': f['id'], 'document_id': DOCUMENTS[f['document']]['id'], 'filename': DOCUMENTS[f['document']]['filename'],
              'document_token': DOCUMENTS[f['document']]['token'], 'creation_date': drf_time(f['created'])}
             for f in FILES.values() if f['subscription'] == sublabel]
    user = USERS[row['user']]
    info = {
        'membership_card_configuration': card, 'user': {k: user[k] for k in ('first_name', 'last_name', 'email', 'phone', 'username', 'role')},
        'associate': persona(row['associate']) if row['associate'] else None,
        'medical_expiration_date': medical['expiration'] if medical else None,
        'medical_document': medical_document['id'] if medical_document else None,
        'medical_token': medical_document['token'] if medical_document else None,
        'subscription_files': files, 'is_current': is_current(row), 'is_next_year': is_next_year(row),
        'plain_medical_label': plain_label(sublabel), 'signature_present': bool(row['signature_storage_key'] or row['signature_url']),
        'subscription_id': row['id'], 'custom_data': row['custom_data'], 'additional_fields': row['additional_fields'], 'type': row['type'],
        'role': row['role'], 'acceptance_date': row['acceptance_date'], 'resignation_date': row['resignation_date'],
        'status_flag': row['status'], 'creation_date': drf_time(row['created']), 'start_date': row['start'], 'end_date': row['end'],
        'signature_url': row['signature_url'], 'archived': row['archived'], 'draft': row['draft'], 'trial': row['trial'], 'meta': row['meta'],
        'subscription_number': row['number'], 'subscription_type': row['subscription_type'], 'deleted': row['deleted'],
        'competitive': row['competitive'], 'notes': row['notes'], 'sport_association': row['association'], 'supplier': None,
        'instructor': None, 'medical': medical['id'] if medical else None, 'document_pdf': document['id'],
        'payment': PAYMENTS[row['payment']]['id'] if row['payment'] else None, 'group': row['group'],
        'tags': [TAGS[t]['id'] for t in row['tags']],
    }
    if medical:
        info['competitive_medical_certificate'] = medical['competitive']
    return {'data': {'info': info, 'courses': info_courses(sublabel, group), 'payments': []}}


# --- subscription_calendar -----------------------------------------------------------------------------------------------
def calendar(sublabel):
    events = []
    if sublabel is not None:
        courses = {cs['course'] for cs in COURSE_SUBS.values() if cs['subscription'] == sublabel and not cs['deleted']}
        for registry in REGISTRIES.values():
            if registry['course'] in courses and registry['events']:
                title = COURSES[registry['course']]['title']
                for event in registry['events']:
                    events.append({'title': f"{event['title']} ({title})", 'start': event['start'], 'end': event['end'],
                                   'allDay': event['allDay']})
    return {'data': {'events': events}}


# --- cases ---------------------------------------------------------------------------------------------------------------
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, headers=None, unordered=False, challenge=None):
    """unordered: the data rows are compared as a multiset (an ordering Django leaves to the database)."""
    case = {'name': f'subscription-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'unordered': unordered}
    if body is not None:
        case['body'] = body
    if challenge is not None:
        case['challenge'] = challenge
    cases.append(case)


LIST, ALL, ARCHIVED = '/subscription/list', '/subscription/list/all', '/subscription/list/archived'
info_path = lambda label: f'/subscription/{S[label]}/info'
calendar_path = lambda label: f'/subscription/{S[label]}/calendar'
UNKNOWN = uid('5ab5c0de')
missing = lambda key: {'detail': f'Missing permission: {key}'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
GROUP = {'X-Group-Id': OWN_GROUP}
NOT_AVAILABLE = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
INVALID_PAGE = {'detail': 'Invalid page.'}
BAD_UUID = ['badly formed hexadecimal UUID string']


def options(name, description=''):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


CALENDAR_DOC = 'API endpoint to get the attendance registry for the current course'
ROUTES = (('list', LIST, 'association.members.read', 'Subscription List', '', ('PUT', 'DELETE')),
          ('all', ALL, 'association.members.read', 'Subscription List All', '', ('POST', 'DELETE')),
          ('archived', ARCHIVED, 'association.members.archive.read', 'Subscription List Archived', '', ('POST', 'DELETE')),
          ('info', info_path('cur_mario'), 'association.members.read', 'Subscription Info', '', ('POST', 'PATCH')),
          ('calendar', calendar_path('cur_mario'), 'association.members.read', 'Subscription Calendar', CALENDAR_DOC, ('POST', 'DELETE')))
for kind, path, permission, name, description, refused in ROUTES:
    add(f'{kind}-anonymous', 'anonymous', path, 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-admin-without-session', 'admin', path, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-restricted-collaborator', 'restricted_collaborator', path, 403, missing(permission))
    for method in refused:
        add(f'{kind}-{method.lower()}', 'owner', path, 405, {'detail': f'Method "{method}" not allowed.'}, method=method)
    add(f'{kind}-options', 'owner', path, 200, options(name, description), method='OPTIONS')
    add(f'{kind}-format-unknown', 'owner', path + '?format=xml', 404, {'detail': 'Not found.'})
for kind, path in (('list', LIST), ('all', ALL), ('archived', ARCHIVED), ('info', info_path('cur_mario')),
                   ('calendar', calendar_path('cur_mario'))):
    add(f'{kind}-owner-without-association', 'owner_without_association', path, 500, SERVER)
    add(f'{kind}-deleted-association-owner', 'deleted_association_owner', path, 500, SERVER)
add('list-restricted-collaborator-post', 'restricted_collaborator', LIST, 403, missing('association.members.read'), method='POST', body={})
add('archived-scoped-athlete', 'owner', ARCHIVED, 403, NOT_AVAILABLE, headers=SCOPED_ATHLETE)

# --- subscription/list: identities and the default listing ---
OWN_LIST = page(own_listed())
add('list-owner', 'owner', LIST, 200, OWN_LIST)
add('list-full-collaborator', 'full_collaborator', LIST, 200, OWN_LIST)
add('list-admin-impersonating-owner', 'admin', LIST, 200, OWN_LIST, headers=ADMIN_OWNER)
add('list-owner-scoped-full-collaborator', 'owner', LIST, 200, OWN_LIST, headers=OWNER_FULL)
add('list-foreign-owner', 'foreign_owner', LIST, 200, page(own_listed(association=FOREIGN)))
add('list-group', 'owner', LIST, 200, page(own_listed(GROUP['X-Group-Id']), group=OWN_GROUP), headers=GROUP)
add('list-group-foreign', 'owner', LIST, 200, page([]), headers={'X-Group-Id': FOREIGN_GROUP})
add('list-group-malformed-ignored', 'owner', LIST, 200, OWN_LIST, headers={'X-Group-Id': 'not-a-group'})
add('list-perpage', 'owner', LIST + '?pagination[perpage]=3&pagination[page]=2', 200, page(own_listed(), number=2, size=3))
add('list-page-last', 'owner', LIST + '?pagination[perpage]=4&pagination[page]=last', 200, page(own_listed(), number=3, size=4))
add('list-perpage-invalid-default', 'owner', LIST + '?pagination[perpage]=zero', 200, OWN_LIST)
add('list-page-out-of-range', 'owner', LIST + '?pagination[page]=5', 404, INVALID_PAGE)
add('list-page-text', 'owner', LIST + '?pagination[page]=uno', 404, INVALID_PAGE)


def where(predicate, labels=None):
    return [k for k in (own_listed() if labels is None else labels) if predicate(SUBSCRIPTIONS[k])]


def associate_of(row):
    return ASSOCIATES[row['associate']] if row['associate'] else None


# --- subscription/list: filters ---
T = TAGS
add('list-tag', 'owner', LIST + f"?query[tags]={T['master']['id']}", 200, page(where(lambda r: 'master' in r['tags'])))
add('list-tags-any', 'owner', LIST + f"?query[tags]={T['master']['id']},{T['agonisti']['id']}", 200,
    page(where(lambda r: bool(r['tags']))))
add('list-tags-all', 'owner', LIST + f"?query[tags]={T['master']['id']},{T['agonisti']['id']}&query[tags_and]=1", 200,
    page(where(lambda r: {'master', 'agonisti'} <= set(r['tags']))))
add('list-tags-all-repeated', 'owner', LIST + f"?query[tags]={T['agonisti']['id']},{T['agonisti']['id']}&query[tags_and]=1", 200,
    page(where(lambda r: 'agonisti' in r['tags'])))
add('list-tag-foreign', 'owner', LIST + f"?query[tags]={T['estero']['id']}", 200, page([]))
add('list-tag-malformed', 'owner', LIST + '?query[tags]=nope', 500, SERVER)
add('list-tag-trailing-comma', 'owner', LIST + f"?query[tags]={T['master']['id']},&query[tags_and]=1", 500, SERVER)
add('list-from-age', 'owner', LIST + '?query[from_age]=30', 200,
    page(where(lambda r: associate_of(r) is not None and associate_of(r)['born_date'] is not None and associate_of(r)['born_date'] < '1996-01-01')))
add('list-to-age', 'owner', LIST + '?query[to_age]=%2012%20', 200,
    page(where(lambda r: associate_of(r) is not None and associate_of(r)['born_date'] is not None and associate_of(r)['born_date'] > '2015-01-01')))
add('list-age-text', 'owner', LIST + '?query[from_age]=trenta', 500, SERVER)
add('list-age-out-of-range', 'owner', LIST + '?query[to_age]=5000', 500, SERVER)
add('list-hide-associate-and-members', 'owner', LIST + '?query[hide_associate_and_members]=1', 200, page(where(lambda r: r['type'] != 2)))
add('list-hide-members', 'owner', LIST + '?query[hide_members]=1', 200, page(where(lambda r: r['type'] != 3)))
add('list-hide-flag-other-value', 'owner', LIST + '?query[hide_members]=true', 200, OWN_LIST)
add('list-status', 'owner', LIST + '?query[status_flag]=%204', 200, page(where(lambda r: r['status'] == 4)))
add('list-status-several', 'owner', LIST + '?query[status_flag]=1,5', 200, page(where(lambda r: r['status'] in (1, 5))))
add('list-status-out-of-range', 'owner', LIST + '?query[status_flag]=70000', 200, page([]))
add('list-status-several-out-of-range', 'owner', LIST + '?query[status_flag]=2,70000', 200, page(where(lambda r: r['status'] == 2)))
add('list-status-text', 'owner', LIST + '?query[status_flag]=accettata', 500, SERVER)
add('list-status-several-empty', 'owner', LIST + '?query[status_flag]=4,', 500, SERVER)
add('list-current-year', 'owner', LIST + '?query[current_year]=1', 200,
    page(where(lambda r: r['start'] is not None and r['end'] is not None and is_current(r))))
add('list-current-year-future', 'owner', LIST + '?query[current_year]=2', 200, page(where(lambda r: r['start'] is not None and r['start'] > '2027-01-01')))
add('list-current-year-past', 'owner', LIST + '?query[current_year]=0', 200, page(where(lambda r: r['end'] is not None and r['end'] < '2026-01-01')))
add('list-current-year-unknown', 'owner', LIST + '?query[current_year]=9', 200, OWN_LIST)
add('list-period', 'owner', LIST + '?query[current_year]=3&query[period_start]=01/01/2024&query[period_end]=31/12/2024', 200,
    page(where(lambda r: r['start'] is not None and r['end'] is not None and r['start'] <= '2024-12-31' and r['end'] >= '2024-01-01')))
add('list-period-reversed', 'owner', LIST + '?query[current_year]=3&query[period_start]=01/01/2025&query[period_end]=31/12/2024', 200, page([]))
add('list-period-invalid-ignored', 'owner', LIST + '?query[current_year]=3&query[period_start]=2024-01-01&query[period_end]=31/12/2024', 200,
    OWN_LIST)
add('list-period-incomplete-ignored', 'owner', LIST + '?query[current_year]=3&query[period_start]=01/01/2024', 200, OWN_LIST)
add('list-type-of-associate', 'owner', LIST + '?query[type_of_associate]=PROMO', 200,
    page(where(lambda r: r['custom_data'] is not None and 'PROMO' in jsonb_text(r['custom_data']).upper())))
add('list-certificate-missing', 'owner', LIST + '?query[certificate_missing_flag]=1', 200, page(where(lambda r: r['medical'] is None)))
add('list-certificate-expired', 'owner', LIST + '?query[certificate_expired_flag]=1', 200, page(where(lambda r: r['medical'] == 'expired')))
add('list-not-paid', 'owner', LIST + '?query[subscription_not_paid_flag]=1', 200,
    page(where(lambda r: r['payment'] is not None and not PAYMENTS[r['payment']]['paid'])))
add('list-type-athletes', 'owner', LIST + '?type=athletes', 200, page(where(lambda r: r['type'] in (2, 3))))
add('list-type-associates', 'owner', LIST + '?type=associates', 200, page(where(lambda r: r['type'] in (1, 2))))
add('list-filters-combined', 'owner', LIST + f"?query[tags]={T['agonisti']['id']}&query[status_flag]=4&type=athletes", 200,
    page(where(lambda r: 'agonisti' in r['tags'] and r['status'] == 4 and r['type'] in (2, 3))))

# --- subscription/list: search ---
SEARCH = LIST + '?query[generalSearch]='
for name, raw, query in (
        ('last-name', 'rossi', 'rossi'), ('upper', 'GIULIA', 'GIULIA'), ('tutor-name', 'bianchi', 'bianchi'),
        ('words', 'Mario Rossi', 'Mario%20Rossi'), ('words-reversed', 'rossi mario', 'rossi%20mario'),
        ('custom-data', 'agonista', 'agonista'), ('meta', 'sportello', 'sportello'), ('notes', 'completa', 'completa'),
        ('tax-code', 'vrdgli', 'vrdgli'), ('subscription-id', S['baby'], S['baby']), ('boolean-text', 'true', 'true'),
        ('date-dmy', '01/09/2025', '01/09/2025'), ('date-words', '2031 09', '2031%2009'), ('tutor-id-number', 'ca123', 'ca123'),
        ('blank', '   ', '%20%20%20'), ('percent', '%', '%25'), ('nothing', 'zzzz', 'zzzz'), ('number', '12', '12')):
    add(f'list-search-{name}', 'owner', SEARCH + query, 200, page(searched(own_listed(), raw)))
add('list-search-prefix', 'owner', SEARCH + 'ossi&query[prefix]=r', 200, page(searched(own_listed(), 'rossi')))
add('list-search-prefix-without-term', 'owner', LIST + '?query[prefix]=gall', 200, page(searched(own_listed(), 'gallNone')))
add('list-search-expiry', 'owner', SEARCH + 'scade=31/08/2030', 200, page(where(lambda r: r['end'] == '2030-08-31')))
add('list-search-expiry-invalid-falls-back', 'owner', SEARCH + 'Scade=agosto', 200, page(searched(own_listed(), 'Scade=agosto')))
add('list-search-username', 'owner', SEARCH + '@=700004', 200, page(where(lambda r: r['user'] == ATHLETE)))
add('list-search-group', 'owner', SEARCH + 'gallo', 200, page(searched(own_listed(OWN_GROUP), 'gallo'), group=OWN_GROUP), headers=GROUP)
add('list-search-paginated', 'owner', SEARCH + 'mario&pagination[perpage]=1&pagination[page]=2', 200,
    page(searched(own_listed(), 'mario'), number=2, size=1))
add('list-search-and-filter', 'owner', SEARCH + 'rossi&query[current_year]=1', 200, page(['cur_mario']))

# --- subscription/list: sorting ---
CURRENT = where(lambda r: r['start'] is not None and r['end'] is not None and is_current(r))


def ordered_by(labels, key, reverse=False):
    return sorted(labels, key=key, reverse=reverse)


last = lambda k: ASSOCIATES[SUBSCRIPTIONS[k]['associate']]['last_name']
first = lambda k: ASSOCIATES[SUBSCRIPTIONS[k]['associate']]['first_name']
add('list-sort-lastname-asc', 'owner', LIST + '?query[current_year]=1&query[sort_lastname_asc_flag]=1', 200,
    page(CURRENT, ordered=ordered_by(CURRENT, last)))
add('list-sort-lastname-desc', 'owner', LIST + '?query[current_year]=1&query[sort_lastname_desc_flag]=1', 200,
    page(CURRENT, ordered=ordered_by(CURRENT, last, True)))
add('list-sort-lastname-flags-zero', 'owner', LIST + '?query[sort_lastname_asc_flag]=0&query[sort_lastname_desc_flag]=0', 200, OWN_LIST)
add('list-sort-associate', 'owner', LIST + '?query[current_year]=1&sort[field]=associate&sort[sort]=asc', 200,
    page(CURRENT, ordered=ordered_by(CURRENT, first)))
add('list-sort-associate-desc', 'owner', LIST + '?query[current_year]=1&sort[field]=associate&sort[sort]=desc', 200,
    page(CURRENT, ordered=ordered_by(CURRENT, first, True)))
add('list-sort-field-overrides-lastname', 'owner',
    LIST + '?query[current_year]=1&query[sort_lastname_asc_flag]=1&sort[field]=associate&sort[sort]=desc', 200,
    page(CURRENT, ordered=ordered_by(CURRENT, first, True)))
born_year = lambda k: ASSOCIATES[SUBSCRIPTIONS[k]['associate']]['born_date'][:4]
add('list-sort-age', 'owner', LIST + '?query[current_year]=1&query[hide_members]=1&sort[field]=age&sort[sort]=desc', 200,
    page(where(lambda r: r['type'] != 3, CURRENT), ordered=ordered_by(where(lambda r: r['type'] != 3, CURRENT), born_year, True)))
add('list-sort-creation', 'owner', LIST + '?sort[field]=creation_date', 200, page(own_listed(), ordered=list(reversed(by_created(own_listed())))))
add('list-sort-status-desc-pk', 'owner', LIST + '?query[status_flag]=4&sort[field]=pk&sort[sort]=desc', 200,
    page(where(lambda r: r['status'] == 4), ordered=sorted(where(lambda r: r['status'] == 4), key=lambda k: S[k], reverse=True)))
add('list-sort-unknown', 'owner', LIST + '?sort[field]=nope', 500, SERVER)
add('list-sort-double-dash', 'owner', LIST + '?sort[field]=-creation_date&sort[sort]=desc', 500, SERVER)
add('list-sort-invalid-page-first', 'owner', LIST + '?sort[field]=nope&pagination[page]=9', 500, SERVER)
# Relation orderings compile with the rows: the page check comes first.
add('list-sort-relation-invalid-page', 'owner', LIST + '?sort[field]=user__username&pagination[page]=9', 404, INVALID_PAGE)
add('list-sort-annotation-invalid-page', 'owner', LIST + '?sort[field]=_has_future_renewal&pagination[page]=9', 404, INVALID_PAGE)

# --- subscription/list: alternate mode ---
def basic(label, group=None):
    row = SUBSCRIPTIONS[label]
    associate = ASSOCIATES[row['associate']] if row['associate'] else None
    return {'subscription_id': row['id'], 'associate': {'associate_id': associate['id'], 'first_name': associate['first_name'],
                                                        'last_name': associate['last_name'], 'tax_code': associate['tax_code'],
                                                        'family': associate['family'], 'family_role': associate['family_role']}
            if associate else None, 'current_year': is_current(row)}


def mode_body(labels, group=None):
    return {'data': {str(i): basic(k, group) for i, k in enumerate(by_created(labels))}, 'meta': {}}


add('list-mode', 'owner', LIST + '?m=1', 200, mode_body(where(lambda r: r['status'] == 4)))
add('list-mode-empty-value', 'owner', LIST + '?m=', 200, mode_body(where(lambda r: r['status'] == 4)))
add('list-mode-all', 'owner', LIST + '?m=1&filter=all', 200, mode_body(own_listed()))
add('list-mode-status', 'owner', LIST + '?m=1&filter=2', 200, mode_body(where(lambda r: r['status'] == 2)))
add('list-mode-status-text', 'owner', LIST + '?m=1&filter=tutte', 500, SERVER)
add('list-mode-ignores-search', 'owner', LIST + '?m=1&filter=all&query[generalSearch]=rossi&query[current_year]=1&sort[field]=nope', 200,
    mode_body(own_listed()))
add('list-mode-basic-filters', 'owner', LIST + f"?m=1&filter=all&query[tags]={T['agonisti']['id']}", 200,
    mode_body(where(lambda r: 'agonisti' in r['tags'])))
add('list-mode-group', 'owner', LIST + '?m=1&filter=all', 200, mode_body(own_listed(OWN_GROUP)), headers=GROUP)
add('list-mode-foreign-without-associate', 'foreign_owner', LIST + '?m=1&filter=all', 200, mode_body(own_listed(association=FOREIGN)))
add('list-mode-post', 'owner', LIST + '?m=1', 200, mode_body(where(lambda r: r['status'] == 4)), method='POST', body={'format': 'pdf'})
add('list-post-without-format', 'owner', LIST, 500, SERVER, method='POST', body={})
add('list-post-unknown-format', 'owner', LIST, 500, SERVER, method='POST', body={'format': 'csv'})
add('list-post-filter-error-first', 'owner', LIST + '?query[from_age]=x', 500, SERVER, method='POST', body={'format': 'pdf'})


# --- subscription/list: field extraction ---
def tutor_contact(label):
    tutors = [ASSOCIATES[t] for t, _ in TUTORS.get(label, [])] if label else []
    return (any(t['email'] not in (None, '') for t in tutors) and any(t['email'] is not None for t in tutors)
            and any(t['phone'] not in (None, '') for t in tutors) and any(t['phone'] is not None for t in tutors))


def field_values(field, group=None, association=OWN):
    values = []
    for k, r in SUBSCRIPTIONS.items():
        if r['association'] != association or r['archived'] or not visible(r, group):
            continue
        associate = ASSOCIATES[r['associate']] if r['associate'] else None
        email = associate['email'] if associate else None
        phone = associate['phone'] if associate else None
        incomplete = email in ('', None) or phone in ('', None)
        if incomplete and not tutor_contact(r['associate']):
            continue
        value = None if associate is None else (associate['id'] if field == 'pk' else drf_time(associate['created']) if field == 'creation_date'
                                                else associate['association'] if field == 'sport_association_id' else associate[field])
        if value not in values:
            values.append(value)
    return {'data': values}


for field in ('email', 'born_date', 'is_minor', 'creation_date', 'pk', 'tax_code', 'user', 'sport_association_id'):
    add(f'list-field-{field.replace("_", "-")}', 'owner', LIST + f'?field={field}', 200, field_values(field), unordered=True)
add('list-field-group', 'owner', LIST + '?field=email', 200, field_values('email', OWN_GROUP), headers=GROUP, unordered=True)
add('list-field-foreign', 'foreign_owner', LIST + '?field=email', 200, field_values('email', association=FOREIGN), unordered=True)
add('list-field-athlete', 'athlete', LIST + '?field=email', 200, {'data': []}, unordered=True)
add('list-field-unknown', 'owner', LIST + '?field=nope', 500, SERVER)
add('list-field-empty-is-listing', 'owner', LIST + '?field=', 200, OWN_LIST)
add('list-field-before-filters', 'owner', LIST + '?field=email&query[from_age]=x&m=1', 200, field_values('email'), unordered=True)

# --- subscription/list/all ---
OWN_ALL = own_listed()
add('all-owner', 'owner', ALL, 200, all_body(OWN_ALL), unordered=True)
add('all-full-collaborator', 'full_collaborator', ALL, 200, all_body(OWN_ALL), unordered=True)
add('all-admin-impersonating-owner', 'admin', ALL, 200, all_body(OWN_ALL), headers=ADMIN_OWNER, unordered=True)
add('all-owner-scoped-full-collaborator', 'owner', ALL, 200, all_body(OWN_ALL), headers=OWNER_FULL, unordered=True)
add('all-group', 'owner', ALL, 200, all_body(own_listed(OWN_GROUP)), headers=GROUP, unordered=True)
add('all-foreign-owner-without-associate', 'foreign_owner', ALL, 500, SERVER)
add('all-current-year', 'owner', ALL + '?current_year=1', 200, all_body(where(lambda r: r['start'] is not None and is_current(r))),
    unordered=True)
add('all-current-year-true', 'owner', ALL + '?current_year=true', 200, all_body(where(lambda r: r['start'] is not None and is_current(r))),
    unordered=True)
add('all-current-year-other', 'owner', ALL + '?current_year=yes', 200, all_body(OWN_ALL), unordered=True)
add('all-type-athletes', 'owner', ALL + '?type=athletes', 200, all_body(where(lambda r: r['type'] in (2, 3))), unordered=True)
add('all-type-associates', 'owner', ALL + '?type=associates', 200, all_body(where(lambda r: r['type'] in (1, 2))), unordered=True)
add('all-type-unknown', 'owner', ALL + '?type=altro', 200, all_body(OWN_ALL), unordered=True)
add('all-course', 'owner', ALL + f"?course_id={COURSES['swim']['id']}", 200,
    all_body(where(lambda r: r['id'] not in (S['cur_mario'], S['giulia']))), unordered=True)
add('all-course-deleted-subscription-kept', 'owner', ALL + f"?course_id={COURSES['other']['id']}", 200, all_body(OWN_ALL), unordered=True)
add('all-course-malformed', 'owner', ALL + '?course_id=nope', 500, SERVER)
add('all-course-null-subscription-excludes-all', 'owner', ALL + f"?course_id={COURSES['broken']['id']}", 200, all_body([]))
add('all-athlete-without-association', 'athlete', ALL, 403, {'msg': 'not allowed'})
add('all-athlete', 'athlete', ALL + f'?sport_association={OWN}', 200, all_body(['athlete']))
add('all-athlete-foreign', 'athlete', ALL + f'?sport_association={FOREIGN}&type=athletes', 200, all_body(['athlete_foreign']))
add('all-athlete-malformed-association', 'athlete', ALL + '?sport_association=nope', 500, SERVER)
add('all-athlete-empty-association', 'athlete', ALL + '?sport_association=', 500, SERVER)
add('all-athlete-current-year', 'athlete', ALL + f'?sport_association={OWN}&current_year=1', 200, all_body(['athlete']))
PREREG = ALL + '?preregistration=1'
add('all-preregistration', 'owner', PREREG, 200, preregistration(OWN_ALL))
add('all-preregistration-group', 'owner', PREREG, 200, preregistration(own_listed(OWN_GROUP)), headers=GROUP)
add('all-preregistration-other-value', 'owner', ALL + '?preregistration=2', 200, all_body(OWN_ALL), unordered=True)
add('all-preregistration-ignores-filters', 'owner', PREREG + '&type=athletes&course_id=nope', 200, preregistration(OWN_ALL))
add('all-preregistration-athlete', 'athlete', PREREG + f'&sport_association={OWN}', 200, preregistration(['athlete']))
add('all-preregistration-athlete-status', 'athlete', PREREG + f'&sport_association={FOREIGN}', 200, preregistration(['athlete_foreign']))
add('all-preregistration-athlete-without-association', 'athlete', PREREG, 200, {'data': {}})
add('all-preregistration-foreign-without-associate', 'foreign_owner', PREREG, 500, SERVER)

# --- subscription/list/archived ---
OWN_ARCHIVED = archived_listed()
add('archived-owner', 'owner', ARCHIVED, 200, archived_page(OWN_ARCHIVED))
add('archived-full-collaborator', 'full_collaborator', ARCHIVED, 200, archived_page(OWN_ARCHIVED))
add('archived-admin-impersonating-owner', 'admin', ARCHIVED, 200, archived_page(OWN_ARCHIVED), headers=ADMIN_OWNER)
add('archived-owner-scoped-full-collaborator', 'owner', ARCHIVED, 200, archived_page(OWN_ARCHIVED), headers=OWNER_FULL)
add('archived-foreign-owner', 'foreign_owner', ARCHIVED, 200, archived_page([]))
add('archived-athlete', 'athlete', ARCHIVED, 401, '<empty>', challenge=False)
add('archived-group', 'owner', ARCHIVED, 200, archived_page(archived_listed(OWN_GROUP), group=OWN_GROUP), headers=GROUP)
add('archived-group-renewal-lookup', 'owner', ARCHIVED + '?query[generalSearch]=bianchi', 200,
    archived_page(searched(archived_listed(OWN_GROUP), 'bianchi'), group=OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('archived-paginated', 'owner', ARCHIVED + '?pagination[perpage]=2&pagination[page]=2', 200, archived_page(OWN_ARCHIVED, number=2, size=2))
add('archived-invalid-page', 'owner', ARCHIVED + '?pagination[page]=3', 404, INVALID_PAGE)
for name, raw, query in (('name', 'rossi', 'rossi'), ('date', '01/09/2022', '01/09/2022'), ('words', 'lucia bianchi', 'lucia%20bianchi'),
                         ('status', '5', '5'), ('nothing', 'zzzz', 'zzzz')):
    add(f'archived-search-{name}', 'owner', ARCHIVED + '?query[generalSearch]=' + query, 200, archived_page(searched(OWN_ARCHIVED, raw)))
add('archived-search-not-expiry', 'owner', ARCHIVED + '?query[generalSearch]=scade=31/12/2021', 200,
    archived_page(searched(OWN_ARCHIVED, 'scade=31/12/2021')))
WITH_ASSOCIATE = [k for k in OWN_ARCHIVED if SUBSCRIPTIONS[k]['associate']]
add('archived-sort-associate', 'owner', ARCHIVED + '?query[generalSearch]=a&sort[field]=associate', 200,
    archived_page(searched(OWN_ARCHIVED, 'a'), ordered=ordered_by(searched(OWN_ARCHIVED, 'a'),
                  lambda k: (first(k) if SUBSCRIPTIONS[k]['associate'] else '￿'))))
add('archived-sort-start-date', 'owner', ARCHIVED + '?sort[field]=start_date&sort[sort]=desc', 200,
    archived_page(OWN_ARCHIVED, ordered=ordered_by(OWN_ARCHIVED, lambda k: SUBSCRIPTIONS[k]['start'], True)))
add('archived-sort-lastname-flag-zero', 'owner', ARCHIVED + '?query[generalSearch]=a&query[sort_lastname_asc_flag]=0', 200,
    archived_page(WITH_ASSOCIATE, ordered=ordered_by([k for k in searched(OWN_ARCHIVED, 'a')], lambda k: last(k) if SUBSCRIPTIONS[k]['associate'] else '￿')))
add('archived-sort-lastname-desc', 'owner', ARCHIVED + '?query[sort_lastname_desc_flag]=1&sort[field]=start_date', 200,
    archived_page(OWN_ARCHIVED, ordered=ordered_by(OWN_ARCHIVED, lambda k: last(k) if SUBSCRIPTIONS[k]['associate'] else '\uffff', True)))
add('archived-sort-unknown', 'owner', ARCHIVED + '?sort[field]=nope', 500, SERVER)
add('archived-sort-relation-invalid-page', 'owner', ARCHIVED + '?sort[field]=payment&pagination[page]=4', 404, INVALID_PAGE)
add('archived-sort-age-unknown', 'owner', ARCHIVED + '?sort[field]=age', 500, SERVER)
add('archived-sort-relation-replaced-by-lastname', 'owner', ARCHIVED + '?sort[field]=medical&query[sort_lastname_desc_flag]=1', 200,
    archived_page(OWN_ARCHIVED, ordered=ordered_by(OWN_ARCHIVED, lambda k: last(k) if SUBSCRIPTIONS[k]['associate'] else '\uffff', True)))

# --- subscription/<uid>/info ---
add('info-owner', 'owner', info_path('cur_mario'), 200, info_body('cur_mario'))
add('info-full-collaborator', 'full_collaborator', info_path('cur_mario'), 200, info_body('cur_mario'))
add('info-admin-impersonating-owner', 'admin', info_path('cur_mario'), 200, info_body('cur_mario'), headers=ADMIN_OWNER)
add('info-owner-scoped-full-collaborator', 'owner', info_path('cur_mario'), 200, info_body('cur_mario'), headers=OWNER_FULL)
add('info-uppercase-uuid', 'owner', f"/subscription/{S['cur_mario'].upper()}/info", 200, info_body('cur_mario'))
add('info-hyphenless-uuid', 'owner', f"/subscription/{S['giulia'].replace('-', '')}/info", 200, info_body('giulia'))
add('info-minor-with-tutor', 'owner', info_path('giulia'), 200, info_body('giulia'))
add('info-archived', 'owner', info_path('arch_mario'), 200, info_body('arch_mario'))
add('info-old', 'owner', info_path('old_mario'), 200, info_body('old_mario'))
add('info-group', 'owner', info_path('cur_mario'), 404, {'msg': 'Subscription not found.'}, headers=GROUP)
add('info-group-malformed-ignored', 'owner', info_path('cur_mario'), 200, info_body('cur_mario'), headers={'X-Group-Id': 'x'})
add('info-malformed', 'owner', '/subscription/nope/info', 400, BAD_UUID)
add('info-athlete-malformed', 'athlete', '/subscription/nope/info', 400, BAD_UUID)
add('info-unknown', 'owner', f'/subscription/{UNKNOWN}/info', 404, {'msg': 'Subscription not found.'})
add('info-foreign', 'owner', info_path('abroad'), 403, {'detail': 'User not allowed.'})
add('info-foreign-owner', 'foreign_owner', info_path('abroad'), 200, info_body('abroad'))
add('info-athlete-own', 'athlete', info_path('athlete'), 200, info_body('athlete'))
add('info-athlete-other-association', 'athlete', info_path('athlete_foreign'), 200, info_body('athlete_foreign'))
add('info-athlete-same-tax-code', 'athlete', info_path('abroad'), 200, info_body('abroad'))
add('info-athlete-not-own', 'athlete', info_path('cur_mario'), 403, {'detail': 'Subscription not found.'})
add('info-athlete-uppercase', 'athlete', f"/subscription/{S['athlete'].upper()}/info", 403, {'detail': 'Subscription not found.'})
add('info-athlete-unknown', 'athlete', f'/subscription/{UNKNOWN}/info', 403, {'detail': 'Subscription not found.'})

# --- subscription/<uid>/calendar ---
add('calendar-owner', 'owner', calendar_path('cur_mario'), 200, calendar('cur_mario'), unordered=True)
add('calendar-full-collaborator', 'full_collaborator', calendar_path('cur_mario'), 200, calendar('cur_mario'), unordered=True)
add('calendar-admin-impersonating-owner', 'admin', calendar_path('cur_mario'), 200, calendar('cur_mario'), headers=ADMIN_OWNER, unordered=True)
add('calendar-owner-scoped-full-collaborator', 'owner', calendar_path('cur_mario'), 200, calendar('cur_mario'), headers=OWNER_FULL,
    unordered=True)
add('calendar-giulia', 'owner', calendar_path('giulia'), 200, calendar('giulia'), unordered=True)
add('calendar-without-courses', 'owner', calendar_path('baby'), 200, calendar('baby'))
add('calendar-missing-all-day', 'owner', calendar_path('nobirth'), 500, SERVER)
add('calendar-foreign', 'owner', calendar_path('abroad'), 200, calendar(None))
add('calendar-unknown', 'owner', f'/subscription/{UNKNOWN}/calendar', 200, calendar(None))
add('calendar-group-hidden', 'owner', calendar_path('cur_mario'), 200, calendar(None), headers=GROUP)
add('calendar-malformed', 'owner', '/subscription/nope/calendar', 400, BAD_UUID)
add('calendar-uppercase', 'owner', f"/subscription/{S['cur_mario'].upper()}/calendar", 200, calendar('cur_mario'), unordered=True)
add('calendar-athlete-own', 'athlete', calendar_path('athlete'), 200, calendar('athlete'))
add('calendar-athlete-not-own', 'athlete', calendar_path('cur_mario'), 200, calendar(None))
add('calendar-athlete-malformed', 'athlete', '/subscription/nope/calendar', 400, BAD_UUID)

assert len({c['name'] for c in cases}) == len(cases)
SOURCE = {'matrix_version': 1, 'scope': 'subscription list, list/all, list/archived, info and calendar reads', 'ids': IDS,
          'users': USERS, 'documents': DOCUMENTS, 'medicals': MEDICALS, 'tags': TAGS, 'associates': ASSOCIATES, 'tutors': TUTORS,
          'payments': PAYMENTS, 'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBS,
          'installments': INSTALLMENTS, 'registries': REGISTRIES, 'files': FILES, 'card': CARD, 'cases': cases}
OUTPUT.write_text(json.dumps(SOURCE, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
