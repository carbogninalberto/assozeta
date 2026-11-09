"""Author the export-all-data HTTP sequence (export-all-data.md).

export_all_data (@api_view(['POST']), profile_views.py) is listed in the
registry's EXCLUDED_ENDPOINTS. The first association access decides the
identity outcome: request.user.sport_association raises
PermissionDenied for athletes (403 'User is not a sport association') and
SportAssociation.objects (live rows only) .get(user=...) raises
DoesNotExist (500) without a live association.

The workbook is a plain openpyxl Workbook (default sheet removed) with two
sheets written with sheet.append:
- 'Iscrizioni': scoped_queryset(Subscription.objects) (the group-aware
  manager) of the association with select_related('associate',
  'medical') and prefetch_related('associate__tutors'), in database order.
  Headers are the concrete field names of Subscription, MedicalCertificate,
  Associate (prefixed 'associate__') and Associate again ('tutor__') minus
  EXCLUDE_HEADERS. status_flag, type and role are the choice labels at index
  value - 1 (0 is the last label; out of range raises: 500). Every other
  value is str(value) or '-' for None: dates 'YYYY-MM-DD', datetimes
  str(aware UTC datetime), booleans 'True'/'False', Decimals as stored,
  JSON values str() of the decoded value, foreign keys str(related object)
  ('<Model> object (<pk>)'). Without a certificate every medical column is
  '-'; a subscription without an associate raises (500). Each tutor of
  associate.tutors (the M2M's live, group-aware Associate manager) appends its
  columns.
- 'Pagamenti': scoped_queryset(Payment.objects) (live, group-aware) of the
  association with six select_related relations, in database order: the
  payment's fields (subject as its SUBJECT label, type as its TYPE_CHOICES
  label or '-'), course_name (course payments: the scoped live course
  subscription's course title, else the scoped live group-aware
  installment's, else course['label']), then payment_category__,
  custom_accounts__ (account_type as its label), associate__, supplier__,
  instructor__ and invoice__ columns ('-' each without the relation).
The response is the workbook with Content-Disposition 'attachment;
filename=export_<denomination>.xlsx'. Run with ``python3 -I``.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

OUTPUT = Path(__file__).with_name('export_all_data_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ids = iter(f'a1000000-0000-4000-8000-{2720001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
PATH = '/export-all-data'
DENOMINATION = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}

# _meta.fields names, in order (Django 5 field creation order).
SUBSCRIPTION_FIELDS = ['subscription_id', 'sport_association', 'associate', 'supplier', 'instructor', 'custom_data', 'additional_fields',
                       'user', 'type', 'role', 'acceptance_date', 'resignation_date', 'medical', 'status_flag', 'creation_date', 'start_date',
                       'end_date', 'signature_url', 'signature_storage_key', 'document_pdf', 'payment', 'archived', 'draft', 'trial', 'meta',
                       'subscription_number', 'subscription_type', 'group', 'deleted', 'competitive', 'notes']
MEDICAL_FIELDS = ['medical_id', 'document', 'user', 'creation_date', 'expiration_date', 'competitive_medical_certificate', 'notes']
ASSOCIATE_FIELDS = ['associate_id', 'user', 'sport_association', 'picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date',
                    'born_city', 'address_city', 'address', 'address_cap', 'nationality', 'nationality_residence', 'email', 'phone',
                    'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor',
                    'disabled', 'draft', 'group', 'deleted', 'family', 'family_role', 'id_number', 'creation_date', 'migrated', 'notes']
PAYMENT_FIELDS = ['payment_id', 'type', 'subject', 'amount', 'creation_date', 'payment_date', 'paid', 'expense', 'description',
                  'payment_category', 'meta_payment_categories', 'invoice', 'user', 'associate', 'supplier', 'instructor', 'sport_association',
                  'payment_intent_id', 'archived', 'invoice_fiscal_code', 'meta', 'notes', 'custom_accounts', 'signature', 'deleted', 'group',
                  'course']
CATEGORY_FIELDS = ['payment_category_id', 'name', 'creation_date', 'sport_association', 'expense', 'tax_deductible', 'archived', 'type',
                   'vat_management', 'deleted']
ACCOUNT_FIELDS = ['custom_account_id', 'enabled', 'name', 'initial_balance', 'account_type', 'account_code', 'sport_association', 'editable']
SUPPLIER_FIELDS = ['supplier_id', 'name', 'address', 'tax_code', 'sport_association', 'vat_number', 'type', 'email', 'phone_number', 'city',
                   'cap', 'state', 'country', 'nationality', 'note', 'group', 'deleted']
INSTRUCTOR_FIELDS = ['instructor_id', 'user', 'first_name', 'last_name', 'email', 'born_date', 'born_city', 'born_province', 'tax_code',
                     'address_city', 'address', 'address_province', 'civic_number', 'study_title', 'role', 'phone', 'phone_2', 'phone_3',
                     'phone_4', 'draft', 'associated_user_id', 'is_volunteer', 'stipulated_contract_in', 'default_hourly_billing',
                     'default_percentage_billing']
INVOICE_FIELDS = ['invoice_id', 'document_pdf', 'sport_association', 'creation_date', 'membership_fee', 'activity_fee',
                  'meta_payment_categories', 'description', 'number', 'archived', 'cancelled', 'selected_tutor', 'meta',
                  'imported_from_associami', 'group', 'deleted']
EXCLUDE_HEADERS = ['associate__tutors', 'signature', 'associate', 'supplier', 'instructor', 'medical', 'medical_id', 'sport_association',
                   'associate__sport_association', 'tutor__sport_association', 'tutor__associate_id', 'associate__associate_id',
                   'associate_id', 'payment', 'document', 'subscription_id', 'custom_data', 'additional_fields', 'user', 'associate__user',
                   'tutor__user', 'deleted', 'trial', 'document_pdf', 'associate__picture_path', 'picture_path', 'meta']
PAYMENT_EXCLUDE_HEADERS = ['associate', 'instructor', 'supplier', 'payment_category', 'payment_id', 'invoice', 'user', 'sport_association',
                           'payment_intent_id', 'invoice_fiscal_code', 'custom_accounts', 'custom_account_id', 'deleted', 'enabled',
                           'editable', 'course_id', 'associate_id', 'supplier_id', 'instructor_id', 'document_pdf',
                           'imported_from_associami', 'picture_path', 'payment_category_id', 'invoice_id']
STATUS_FLAG = ['non firmata', 'in attesa', 'rifiutata', 'accettata', 'ritirata']
TYPE = ['associato', 'associato e tesserato', 'tesserato']
ROLE = ['Socio ordinario', 'Consigliere', 'Segretario', 'Vice Presidente', 'Presidente', 'Socio volontario', 'Socio sostenitore',
        'Tesoriere']
SUBJECT = ['Altro', 'Iscrizione', 'Corso', 'Giroconto']
PAYMENT_TYPES = {'default': 'DEFAULT', 'cash': 'CONTANTI', 'transfer': 'BONIFICO', 'online': 'ONLINE', 'sepa-transfer': 'BONIFICO SEPA',
                 'stripe': 'STRIPE', 'pos': 'POS'}
ACCOUNT_TYPE = ['contanti', 'banca', 'altro']


def keep(fields, exclude):
    return [f for f in fields if f not in exclude]


def moment(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def obj(model, pk):
    """A related object: str() is Django's default '<Model> object (<pk>)'."""
    return {'$object': f'{model} object ({pk})'}


def jsonb(value):
    """The decoded value of a jsonb column: object keys ordered by (UTF-8 length, bytes), duplicates removed."""
    if isinstance(value, dict):
        return {key: jsonb(value[key]) for key in sorted(value, key=lambda key: (len(key.encode()), key.encode()))}
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    return value


def py_str(value):
    if isinstance(value, dict) and set(value) == {'$object'}:
        return value['$object']
    if isinstance(value, dict) and set(value) == {'$json'}:
        return str(jsonb(value['$json']))
    return str(value)


def cell(value):
    return '-' if value is None else py_str(value)


# --- rows: Python values as Django hands them to str() ------------------------------------------------------------
FAMILY = next(ids)
GROUP_OBJ = obj('Group', OWN_GROUP)


def associate(first, association=OWN, **fields):
    row = {name: None for name in ASSOCIATE_FIELDS}
    row.update(associate_id=next(ids), sport_association=association, first_name=first, sex='A', nationality='Italiana',
               nationality_residence='Italia', is_minor=False, disabled=False, draft=False, deleted=False, migrated=False,
               creation_date=moment('2026-01-02T09:00:00'))
    row.update(fields)
    return row


ASSOCIATES = {
    'anna': associate('Anna', last_name='Rossi', sex='F', tax_code='RSSNNA80A41H501X', born_date=date(1980, 1, 1), born_city='Roma',
                      address_city='Milano', address='Via Roma, 1', address_cap='20100', email='anna@example.test', phone='3331112222',
                      phone_2='', phone_label='Casa', is_minor=False, group=GROUP_OBJ, family=obj('Family', FAMILY),
                      family_role='madre', id_number='AB123', creation_date=moment('2026-01-02T09:00:00.250000'), notes='Prima riga\nseconda',
                      nationality='Tedesca'),
    'bruno': associate('Bruno', last_name=None, sex='M', nationality=None, nationality_residence=None, draft=True, migrated=True),
    'carla': associate('Carla', last_name='Neri', is_minor=True, born_date=date(2016, 6, 15)),
    'tutor': associate('Teo', last_name='Rossi', sex='M', phone='3470000000', email='teo@example.test'),
    'gone': associate('Gina', last_name='Verdi', deleted=True),
    'zero': associate('Zeno', last_name='Bassi', disabled=True),
    'hans': associate('Hans', FOREIGN, last_name='Muller'),
}
RELATIONS = [['anna', 'tutor', True], ['carla', 'gone', True]]
CERTIFICATES = {
    'full': {'medical_id': next(ids), 'creation_date': moment('2026-02-03T10:11:12.000001'), 'expiration_date': date(2027, 2, 3),
             'competitive_medical_certificate': True, 'notes': 'Agonistico'},
    'bare': {'medical_id': next(ids), 'creation_date': moment('2026-02-04T08:00:00'), 'expiration_date': None,
             'competitive_medical_certificate': False, 'notes': None},
}


def subscription(person, association=OWN, **fields):
    row = {name: None for name in SUBSCRIPTION_FIELDS}
    row.update(subscription_id=next(ids), sport_association=association, associate=person, type=2, role=1, status_flag=1,
               creation_date=moment('2026-03-01T10:00:00'), archived=False, draft=True, trial=False, deleted=False, competitive=False,
               subscription_number='0', medical=None)
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'anna': subscription('anna', type=2, role=5, status_flag=4, acceptance_date=date(2026, 3, 2), creation_date=moment('2026-03-01T10:00:00.123456'),
                         start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), signature_url='https://storage.example.test/firma.png',
                         signature_storage_key='', draft=False, subscription_number='12', subscription_type='Socio ordinario',
                         group=GROUP_OBJ, competitive=True, notes='Nota', medical='full', custom_data={'$json': {'k': 'v'}}, meta={'$json': {'m': 1}}),
    'bruno': subscription('bruno', type=1, role=1, status_flag=1, archived=True, deleted=True, subscription_number=None, medical='bare'),
    'carla': subscription('carla', type=3, role=8, status_flag=5, resignation_date=date(2026, 4, 30), subscription_type='',
                          creation_date=moment('2026-03-05T00:00:00')),
    'zero': subscription('zero', type=0, role=0, status_flag=0, subscription_number='7'),
    'hans_bad': subscription('hans', FOREIGN, status_flag=9),
}
CATEGORY = {'payment_category_id': next(ids), 'name': 'Quote corsi', 'creation_date': moment('2025-12-01T00:00:00.5'),
            'sport_association': OWN, 'expense': False, 'tax_deductible': True, 'archived': False, 'type': 2, 'vat_management': None,
            'deleted': False}
ACCOUNT = {'custom_account_id': next(ids), 'enabled': True, 'name': 'Banca Intesa', 'initial_balance': Decimal('1500.50'), 'account_type': 2,
           'account_code': 'IT60X', 'sport_association': OWN, 'editable': False}
SUPPLIER = {name: None for name in SUPPLIER_FIELDS}
SUPPLIER.update(supplier_id=next(ids), name='Fornitore Srl', address='Via Po 3', sport_association=OWN, vat_number='01234567890',
                type='supplier', email='forn@example.test', country='Italia', nationality='Italiana', note='', group=None, deleted=False)
INSTRUCTOR = {name: None for name in INSTRUCTOR_FIELDS}
INSTRUCTOR.update(instructor_id=next(ids), user=obj('User', IDS['owner']), first_name='Ivo', last_name='Mori', born_date=date(1990, 5, 6),
                  role='Istruttore', phone='0451', draft=False, associated_user_id=UUID(IDS['full_collaborator']), is_volunteer=True,
                  stipulated_contract_in=date(2026, 9, 1), default_hourly_billing=Decimal('25.00'), default_percentage_billing=Decimal('12.50'))
INVOICE = {name: None for name in INVOICE_FIELDS}
INVOICE.update(invoice_id=next(ids), sport_association=OWN, creation_date=moment('2026-03-10T12:00:00'), membership_fee=Decimal('10.00'),
               activity_fee=Decimal('40.00'), meta_payment_categories={'$json': [{'amount': 10.5, 'name': 'Quota'}]}, description='Ricevuta',
               number=12, archived=False, cancelled=False, selected_tutor=obj('Associate', ASSOCIATES['tutor']['associate_id']),
               meta=None, imported_from_associami=False, group=GROUP_OBJ, deleted=False)
COURSES = {'nuoto': {'id': next(ids), 'title': 'Nuoto'}, 'yoga': {'id': next(ids), 'title': 'Yoga'}, 'senza': {'id': next(ids), 'title': None}}


def payment(**fields):
    row = {name: None for name in PAYMENT_FIELDS}
    row.update(payment_id=next(ids), type='default', subject=0, amount=Decimal('0.00'), creation_date=moment('2026-03-20T08:00:00'),
               paid=False, expense=False, archived=False, deleted=False, sport_association=OWN, links=[])
    row.update(fields)
    return row


PAYMENTS = {
    'full': payment(type='cash', subject=2, amount=Decimal('50.00'), creation_date=moment('2026-03-15T08:30:00.654321'),
                    payment_date=moment('2026-03-16T09:00:00'), paid=True, description='Quota corso', payment_category='category',
                    meta_payment_categories={'$json': [{'payment_category_id': 1, 'amount': 10.0, 'subject': 0}]}, invoice='invoice',
                    associate='anna', meta={'$json': {'description': 'Rata', 'n': 1, 'ok': True, 'x': None}}, notes='nota',
                    custom_accounts='account', group=GROUP_OBJ, links=[['course', 'nuoto', False, 'anna']]),
    'supplier': payment(type='weird', subject=0, amount=Decimal('12.30'), expense=True, supplier='supplier', description='',
                        course={'$json': {'label': 'ignorato'}}),
    'installment': payment(type='transfer', subject=2, amount=Decimal('30.00'), instructor='instructor', associate='carla',
                           links=[['course', 'nuoto', True, None], ['installment', 'yoga', False, None]]),
    'label': payment(type='pos', subject=2, amount=Decimal('45.00'), associate='bruno', course={'$json': {'label': 'Danza'}}),
    'no-label': payment(type='online', subject=2, amount=Decimal('5.00'), course={'$json': {'x': 1}}),
    'untitled': payment(type='stripe', subject=2, amount=Decimal('7.00'), links=[['course', 'senza', False, None]]),
    'transfer': payment(type='sepa-transfer', subject=3, amount=Decimal('100.00'), payment_intent_id='pi_x', invoice_fiscal_code='CF'),
    'deleted': payment(subject=1, deleted=True),
    'foreign': payment(subject=1, sport_association=FOREIGN, associate='hans'),
}
RELATED = {'category': CATEGORY, 'account': ACCOUNT, 'supplier': SUPPLIER, 'instructor': INSTRUCTOR, 'invoice': INVOICE}


def course_name(row, group, scoped):
    """links: [kind, course, deleted, subscription]; during impersonation the scoped managers also require the
    course subscription's subscription in the association."""
    if row['subject'] != 2:
        return None
    live = [link for link in row['links'] if not link[2] and (not scoped or link[3] is not None)]
    courses = [link for link in live if link[0] == 'course']
    if courses:
        return COURSES[courses[0][1]]['title']
    installments = [link for link in live if link[0] == 'installment' and group is None]
    if installments:
        return COURSES[installments[0][1]]['title']
    course = jsonb(row['course']['$json']) if row['course'] else None
    if course:
        return course['label'] if 'label' in course else None
    return None


def tutors_of(person, group):
    tutors = [ASSOCIATES[t] for child, t, _ in RELATIONS if child == person]
    return [t for t in tutors if not t['deleted'] and (group is None or t['group'] == obj('Group', group))]


SUB_COLUMNS = keep(SUBSCRIPTION_FIELDS, EXCLUDE_HEADERS)
MED_COLUMNS = keep(MEDICAL_FIELDS, EXCLUDE_HEADERS)
ASSOC_COLUMNS = keep(ASSOCIATE_FIELDS, EXCLUDE_HEADERS)


def subscription_sheet(association, group, order):
    rows = [SUB_COLUMNS + MED_COLUMNS + [f'associate__{f}' for f in ASSOC_COLUMNS] + [f'tutor__{f}' for f in ASSOC_COLUMNS]]
    for label in order:
        s = SUBSCRIPTIONS[label]
        assert s['sport_association'] == association and (group is None or s['group'] == obj('Group', group))
        row = []
        for f in SUB_COLUMNS:
            if f == 'status_flag':
                row.append(STATUS_FLAG[s[f] - 1])
            elif f == 'type':
                row.append(TYPE[s[f] - 1])
            elif f == 'role':
                row.append(ROLE[s[f] - 1])
            else:
                row.append(cell(s[f]))
        medical = CERTIFICATES[s['medical']] if s['medical'] else None
        row += [cell(medical[f]) if medical else '-' for f in MED_COLUMNS]
        person = ASSOCIATES[s['associate']]
        row += [cell(person[f]) for f in ASSOC_COLUMNS]
        for tutor in tutors_of(s['associate'], group):
            row += [cell(tutor[f]) for f in ASSOC_COLUMNS]
        rows.append(row)
    return rows


PAY_COLUMNS = keep(PAYMENT_FIELDS, PAYMENT_EXCLUDE_HEADERS)
SECTIONS = [('payment_category', keep(CATEGORY_FIELDS, PAYMENT_EXCLUDE_HEADERS), 'category'),
            ('custom_accounts', keep(ACCOUNT_FIELDS, PAYMENT_EXCLUDE_HEADERS), 'account'),
            ('associate', keep(ASSOCIATE_FIELDS, PAYMENT_EXCLUDE_HEADERS), None),
            ('supplier', keep(SUPPLIER_FIELDS, PAYMENT_EXCLUDE_HEADERS), 'supplier'),
            ('instructor', keep(INSTRUCTOR_FIELDS, PAYMENT_EXCLUDE_HEADERS), 'instructor'),
            ('invoice', keep(INVOICE_FIELDS, PAYMENT_EXCLUDE_HEADERS), 'invoice')]


def payment_sheet(group, order, scoped):
    rows = [PAY_COLUMNS + ['course_name'] + [f'{prefix}__{f}' for prefix, fields, _ in SECTIONS for f in fields]]
    for label in order:
        p = PAYMENTS[label]
        row = []
        for f in PAY_COLUMNS:
            if f == 'subject':
                row.append(SUBJECT[p['subject']])
            elif f == 'type':
                row.append(PAYMENT_TYPES.get(p['type'], '-'))
            else:
                row.append(cell(p[f]))
        name = course_name(p, group, scoped)
        row.append('-' if name is None else str(name))
        for prefix, fields, key in SECTIONS:
            related = (ASSOCIATES[p['associate']] if p['associate'] else None) if prefix == 'associate' else (
                RELATED[key] if p[prefix] else None)
            for f in fields:
                if related is None:
                    row.append('-')
                elif prefix == 'custom_accounts' and f == 'account_type':
                    row.append(ACCOUNT_TYPE[related[f] - 1])
                else:
                    row.append(cell(related[f]))
        rows.append(row)
    return rows


# Database order of the unordered querysets with the adapter's fresh statistics (observed, see export-all-data.md).
SUBSCRIPTION_ORDER = ['anna', 'bruno', 'carla', 'zero']
PAYMENT_ORDER = ['full', 'transfer', 'untitled', 'no-label', 'label', 'installment', 'supplier']


def workbook(group=None, subscriptions=SUBSCRIPTION_ORDER, payments=PAYMENT_ORDER, scoped=False):
    return {'$xlsx': {'engine': 'openpyxl_rows', 'stream': True, 'sheets': [['Iscrizioni', subscription_sheet(OWN, group, subscriptions)],
                                                            ['Pagamenti', payment_sheet(group, payments, scoped)]]},
            'filename': f'export_{DENOMINATION[OWN]}.xlsx'}


cases = []


def add(name, actor, status, response, *, method='POST', headers=None, body=None):
    case = {'name': f'export-all-data-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {}, 'status': status,
            'response': response}
    if body is not None:
        case['body'] = body
    cases.append(case)


ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
FULL = workbook()

add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('scoped-athlete', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers=SCOPED_ATHLETE)
add('get', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Export All Data', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('athlete', 'athlete', 403, {'detail': 'User is not a sport association'})
add('owner-without-association', 'owner_without_association', 500, SERVER)
add('deleted-association-owner', 'deleted_association_owner', 500, SERVER)
add('foreign-status-out-of-range', 'foreign_owner', 500, SERVER)
add('owner', 'owner', 200, FULL)
add('owner-with-body', 'owner', 200, FULL, body={'ignored': True})
add('full-collaborator', 'full_collaborator', 200, FULL)
add('restricted-collaborator', 'restricted_collaborator', 200, FULL)
add('admin-impersonating-owner', 'admin', 200, FULL, headers=ADMIN_OWNER)
add('group', 'owner', 200, workbook(OWN_GROUP, ['anna'], ['full']), headers={'X-Group-Id': OWN_GROUP})
add('group-foreign', 'owner', 200, workbook(FOREIGN_GROUP, [], []), headers={'X-Group-Id': FOREIGN_GROUP})
add('group-malformed', 'owner', 200, FULL, headers={'X-Group-Id': 'nope'})

assert len({c['name'] for c in cases}) == len(cases)


def encode(value):
    """JSON form of the fixture rows (seeded by the adapter)."""
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, datetime):
        return {'datetime': value.isoformat()}
    if isinstance(value, date):
        return {'date': value.isoformat()}
    if isinstance(value, Decimal):
        return {'decimal': str(value)}
    if isinstance(value, UUID):
        return {'uuid': str(value)}
    return value


OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'export-all-data workbook: str() of nine models, scoping and identity',
                              'ids': IDS, 'family': FAMILY, 'associates': encode(ASSOCIATES), 'relations': RELATIONS,
                              'certificates': encode(CERTIFICATES), 'subscriptions': encode(SUBSCRIPTIONS), 'category': encode(CATEGORY),
                              'account': encode(ACCOUNT), 'supplier': encode(SUPPLIER), 'instructor': encode(INSTRUCTOR),
                              'invoice': encode(INVOICE), 'courses': COURSES, 'payments': encode(PAYMENTS), 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
