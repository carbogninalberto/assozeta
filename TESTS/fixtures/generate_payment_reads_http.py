"""Author the payment list, payment stats and subscription payments HTTP sequence (payment-reads.md).

payment_list, payment_stats (BE/application/views/payment_views.py) and
subscription_payments (BE/application/views/subscriptions_views.py) are
@api_view(['GET']) function views behind IsAuthenticated (registry:
``bookeeping.payments.read`` for the first two, ``association.members.read``
for ``subscription/*/payments``). payment_list and subscription_payments are
athlete views for association-scoped sessions; payment_stats is not.

payment_list:
- ``course_subscription_id`` (truthy) goes through is_valid_uuid (400 list)
  before anything else.
- ``mode=compressed``: ``request.user.sportassociation`` (the reverse one-to-one
  uses the base manager: a soft-deleted association is found; without one the
  scoped ``SportAssociation.objects.get`` raises, 500), then the live,
  group-aware payments of it with ``archived=query[archived]`` (BooleanField
  to_python: 't', 'True', '1', 'f', 'False', '0'; anything else a 500) as
  ``values(...)`` ordered by ``-creation_date``; Decimals render as floats.
- athletes take their own branch (authored at the end of the sequence, with its
  own rows in two further associations).
- everyone else: the association as above, the same archived filter, then one
  ``filter(Q)`` of ``associate_id`` (UUID, else 500), ``query[payment_range]``
  ('Y/m/d al Y/m/d' or 'd/m/Y al d/m/Y'; a value that does not split in two,
  or a first date parsed with the first format and a second that is not, is
  logged and ignored; two unparseable dates fall back to the last year; the
  end is 23:59:59; skipped when ``query[generalSearch]`` is a valid UUID),
  ``query[subject]`` (int(), else 500; out of range matches nothing),
  ``query[type]``, ``query[paid]`` / ``query[expense]`` (== 'true'),
  ``query[payment_categories]`` ('[]' is no category, else comma ids) and
  ``query[account]`` ('[]' is no account), each only when truthy; ordered by
  ``-payment_date, -creation_date``; ``query[generalSearch]``: a d/m/Y date
  becomes Y-m-d, a float() (',' as '.') searches the amount (icontains of
  str(float) or equality), anything else is smart_search (each \\w+ word of
  the stripped, lowercased term icontains one of Payment's concrete fields,
  the associate's and its tutors' listed fields, the supplier's and the
  instructor's; numeric words also match date variations on the date fields;
  several words also match the reversed/original sequence on name, address,
  city and notes fields; the joins are per row and the result is distinct;
  Payment gets no relevance ordering); ``sort[field]`` orders ascending unless
  ``sort[sort] == 'desc'``. KTDatatablePagination and PaymentOptimizedSerializer
  (associate with its tutor relations, instructor, supplier, invoice,
  attachments, the course and subscription lookups through the group-aware
  managers) plus ``instructor_document`` / ``instructor_document_token`` from
  the payment's InstructorHours document; meta {total, page, pages, perpage,
  rowIds}.

payment_stats: the scoped live association (500 otherwise), this month (to
today 00:00) or ``query[payment_range]`` (a value that does not split in two
is a 500; unparseable dates fall back to the last year), the end at 00:00;
``subject``/``type``/``paid``/``expense`` whenever present, categories and
account when truthy, the same general search; aggregates over the distinct
payments; the account balances group the paid payments by account type (the
search joins repeat a payment once per matching tutor row). Stripe is not
configured in the test environment: ``stripe_charges`` is zero.

subscription_payments: athletes get an empty 401; is_valid_uuid; the scoped
association (500 otherwise); the group-aware subscription (missing: 500;
another association: 403 'User not allowed.'; without associate: 500); the
association's live group-aware payments of the associate or of any associate
with the same tax code (iexact), any archived state, by ``-creation_date``;
filters present (not only truthy); the general search is the amount or one
icontains OR over the listed fields and relations without distinct (a payment
joined to two installments is listed twice); sort, pagination and
PaymentOptimizedSerializer plus ``is_carnet`` and the course subscription or
carnet of each course payment.

The search, ordering and serializer expectations below are computed by an
independent model of those Django semantics over the authored rows. Personal
data is synthetic. Run with ``python3 -I``.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Context, Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('payment_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
SERVER = '<stable-server-error-page>'
# Letters only outside the fixed version/variant digits: numeric search words
# never match a seeded payment id.
_letters = iter(f'{a}{b}{c}' for a in 'abcdef' for b in 'abcdef' for c in 'abcdef')


def uid(kind):
    return f'{kind}-eeee-4eee-8eee-eeeeeeeee{next(_letters)}'


# --- rows --------------------------------------------------------------------------------------------
CATEGORY = {'id': uid('cafecafe'), 'name': 'Quote sociali'}
ACCOUNTS = {'cash': {'id': uid('acc0cace'), 'name': 'Cassa sede', 'type': 1, 'code': 'C01'},
            'bank': {'id': uid('acc0bace'), 'name': 'Banca Intesa', 'type': 2, 'code': 'B01'}}
DOCUMENTS = {
    'invoice_pdf': {'id': uid('d0c0aaaa'), 'token': uid('70ce0aaa'), 'filename': 'ricevuta.pdf', 'filepath': 'docs/ricevuta.pdf',
                    'size': 2048, 'created': '2024-02-10T10:05:00Z'},
    'invoice_extra': {'id': uid('d0c0bbbb'), 'token': uid('70ce0bbb'), 'filename': 'allegato.pdf', 'filepath': None, 'size': None,
                      'created': '2024-02-10T10:06:00.500000Z'},
    'attachment': {'id': uid('d0c0cccc'), 'token': uid('70ce0ccc'), 'filename': 'scontrino è.png', 'filepath': 'docs/s.png',
                   'size': 512, 'created': '2024-06-03T09:01:00Z'},
    'compensation': {'id': uid('d0c0dddd'), 'token': uid('70ce0ddd'), 'filename': 'compenso.pdf', 'filepath': 'docs/c.pdf',
                     'size': 1024, 'created': '2024-08-02T09:00:00Z'},
}
ASSOCIATE_DEFAULTS = {'picture_path': None, 'first_name': None, 'last_name': None, 'sex': 'A', 'tax_code': None, 'born_date': None,
                      'born_city': None, 'address_city': None, 'address': None, 'address_cap': None, 'nationality': 'Italiana',
                      'nationality_residence': 'Italia', 'email': None, 'phone': None, 'phone_2': None, 'phone_3': None,
                      'phone_4': None, 'phone_label': None, 'phone_2_label': None, 'phone_3_label': None, 'phone_4_label': None,
                      'is_minor': False, 'disabled': False, 'draft': False, 'deleted': False, 'family_role': None, 'id_number': None,
                      'migrated': False, 'notes': None, 'user': None, 'association': OWN, 'group': None, 'family': None}


def person(created, **values):
    return {**ASSOCIATE_DEFAULTS, **values, 'id': uid('decafbad'), 'created': created}


ASSOCIATES = {
    'mario': person('2023-11-01T08:00:00Z', first_name='Mario', last_name='Rossi', sex='M', tax_code='RSSMRA90E10H501Z',
                    born_date='1990-05-10', born_city='Verona', address_city='Verona', address='Via Mazzini 3', email='mario@example.test',
                    phone='3331112222', notes='nuoto libero'),
    'twin': person('2023-11-02T08:00:00Z', first_name='Marco', last_name='Russo', sex='M', tax_code=' rssmra90e10h501z ',
                   born_date='1991-07-21', email='marco@example.test'),
    'lucia': person('2023-11-03T08:00:00Z', first_name='Lucia', last_name='Verdi', sex='F', tax_code='VRDLCU65B54L781K',
                    born_date='1965-02-14', born_city='Padova', email='lucia@example.test'),
    'paolo': person('2023-11-04T08:00:00Z', first_name='Paolo', last_name='Neri', sex='M', born_date='1963-09-30', group=OWN_GROUP),
    'abroad': person('2023-11-05T08:00:00Z', first_name='Hugo', last_name='Esterno', association=FOREIGN),
}
TUTORS = {'mario': [('lucia', True), ('paolo', False)]}
INSTRUCTOR = {'id': uid('1eac0e00'), 'first_name': 'Giulia', 'last_name': 'Bianchi', 'email': 'giulia@example.test',
              'tax_code': 'BNCGLI85A41H501X', 'phone': '3405556666', 'address': 'Via Garibaldi 9', 'hourly': '18.50'}
SUPPLIER = {'id': uid('5abb0ace'), 'name': 'Palestre Riunite', 'vat_number': '01234567890', 'email': 'fornitore@example.test',
            'address': 'Viale Europa 12', 'city': 'Verona', 'tax_code': None}
COURSES = {'swim': {'id': uid('c0a5ec0a'), 'title': 'Corso nuoto', 'deleted': False},
           'old': {'id': uid('c0a5ec0b'), 'title': 'Corso vecchio', 'deleted': True}}
CARNET = {'id': uid('ca0e7aaa'), 'title': 'Carnet dieci ingressi'}
INVOICE = {'id': uid('1a0c0ace'), 'document': 'invoice_pdf', 'extra': 'invoice_extra', 'created': '2024-02-10T10:05:00Z',
           'membership_fee': '40.00', 'activity_fee': '10.00', 'description': 'Ricevuta iscrizione', 'number': 7,
           'meta': {'serie': 'A'}}

SUBSCRIPTIONS = {
    # label: associate, association, payment, created, extra
    'mario': {'id': uid('5ab5c0de'), 'associate': 'mario', 'association': OWN, 'payment': 'sub', 'created': '2023-12-01T08:00:00Z'},
    'twin': {'id': uid('5ab5c0de'), 'associate': 'twin', 'association': OWN, 'payment': None, 'created': '2023-12-02T08:00:00Z'},
    'lucia': {'id': uid('5ab5c0de'), 'associate': 'lucia', 'association': OWN, 'payment': None, 'created': '2023-12-03T08:00:00Z',
              'deleted': True},
    'nobody': {'id': uid('5ab5c0de'), 'associate': None, 'association': OWN, 'payment': None, 'created': '2000-01-01T08:00:00Z'},
    'abroad': {'id': uid('5ab5c0de'), 'associate': 'abroad', 'association': FOREIGN, 'payment': None,
               'created': '2023-12-04T08:00:00Z'},
}
for row in SUBSCRIPTIONS.values():
    row.setdefault('deleted', False)

PAYMENT_DEFAULTS = {'subject': 0, 'type': 'default', 'amount': '0.00', 'paid': False, 'expense': False, 'description': None,
                    'category': None, 'meta_payment_categories': None, 'invoice': None, 'user': False, 'associate': None,
                    'supplier': False, 'instructor': False, 'association': OWN, 'payment_intent_id': None, 'archived': False,
                    'invoice_fiscal_code': None, 'meta': None, 'notes': None, 'account': None, 'deleted': False, 'group': None,
                    'course': None, 'paid_on': None, 'attachment': None}


def payment(created, **values):
    return {**PAYMENT_DEFAULTS, **values, 'id': uid('ba1dface'), 'created': created}


PAYMENTS = {
    'sub': payment('2024-02-01T09:00:00Z', subject=1, type='cash', amount='50.00', paid=True, associate='mario', user=True,
                   account='cash', category=True, invoice=True, description='Iscrizione annuale',
                   paid_on='2024-02-10T10:00:00.250000Z'),
    'course': payment('2024-03-05T09:00:00Z', subject=2, type='transfer', amount='120.00', associate='mario', category=True,
                      description='Corso nuoto'),
    'installment': payment('2024-04-01T09:00:00Z', subject=2, type='pos', amount='40.00', paid=True, associate='mario',
                           account='bank', description='Rata corso', paid_on='2024-04-15T09:30:00Z'),
    'carnet': payment('2024-05-02T09:00:00Z', subject=2, type='online', amount='30.00', associate='mario',
                      course={'title': 'Carnet JSON', 'n': 1.5}, paid_on='2024-05-20T08:00:00Z'),
    'other': payment('2024-06-01T09:00:00Z', subject=0, amount='15.50', paid=True, associate='twin', notes='nota contanti',
                     meta={'source': 'sportello', 'n': 2}, payment_intent_id='pi_sportello', invoice_fiscal_code='FSCL0001',
                     meta_payment_categories=[{'payment_category_id': CATEGORY['id'], 'amount': 5.5, 'subject': 0}],
                     attachment='attachment', paid_on='2024-06-03T09:00:00Z'),
    'supplier': payment('2024-07-01T09:00:00Z', subject=0, type='transfer', amount='200.00', paid=True, expense=True, supplier=True,
                        account='bank', description='Affitto palestra città', paid_on='2024-07-02T09:00:00Z'),
    'instructor': payment('2024-08-01T09:00:00Z', subject=0, amount='80.00', expense=True, instructor=True, description='Compenso'),
    'instructor_plain': payment('2024-08-15T09:00:00Z', subject=0, type='cash', amount='60.00', paid=True, expense=True,
                                instructor=True, description='Compenso agosto', paid_on='2024-08-20T09:00:00Z'),
    'transfer': payment('2024-09-01T09:00:00Z', subject=3, amount='25.00', paid=True, expense=True, account='cash',
                        description='Giroconto cassa', paid_on='2024-09-01T12:00:00Z'),
    'zero': payment('2024-11-01T09:00:00Z', subject=0, amount='0.00', associate='mario'),
    'grouped': payment('2024-10-01T09:00:00Z', subject=1, amount='10.00', associate='mario', group=OWN_GROUP,
                       paid_on='2024-10-05T09:00:00Z'),
    'archived': payment('2024-01-15T09:00:00Z', subject=0, amount='5.00', paid=True, associate='mario', archived=True,
                        paid_on='2024-01-15T09:00:00Z'),
    'removed': payment('2024-01-20T09:00:00Z', subject=0, amount='7.00', associate='mario', deleted=True),
    'abroad': payment('2024-03-01T09:00:00Z', subject=0, amount='99.00', association=FOREIGN, paid_on='2024-03-02T09:00:00Z'),
}
COURSE_SUBSCRIPTIONS = {
    'swim': {'id': uid('c5c5c5c5'), 'course': 'swim', 'subscription': 'mario', 'payment': 'course', 'deleted': False},
    'rates': {'id': uid('c5c5c5c5'), 'course': 'swim', 'subscription': 'mario', 'payment': None, 'deleted': False},
    'old': {'id': uid('c5c5c5c5'), 'course': 'old', 'subscription': 'mario', 'payment': None, 'deleted': True},
}
# The deleted installment has the smaller key: .first() on all_objects() picks it.
INSTALLMENTS = {
    'old': {'id': uid('11a5aaaa'), 'cs': 'old', 'payment': 'installment', 'deleted': True, 'number': 1},
    'live': {'id': uid('11a5bbbb'), 'cs': 'rates', 'payment': 'installment', 'deleted': False, 'number': 2},
}
HOLDINGS = {'carnet': {'id': uid('ab0ca7ee'), 'subscription': 'mario', 'payment': 'carnet'}}
HOURS = {'instructor': {'id': uid('b0a5b0a5'), 'payment': 'instructor', 'document': 'compensation'},
         # Several hours paid by one compensation share its document (the map keeps the last row).
         'instructor_more': {'id': uid('b0a5b0a5'), 'payment': 'instructor', 'document': 'compensation'},
         # A compensation without its document yet: payment_list prints it (print_document_compensation.apply)
         # and re-reads the hours; the stub renders nothing, so the row has no instructor_document.
         'instructor_plain': {'id': uid('b0a5b0a5'), 'payment': 'instructor_plain', 'document': None}}

P = {label: row['id'] for label, row in PAYMENTS.items()}
A = {label: row['id'] for label, row in ASSOCIATES.items()}
S = {label: row['id'] for label, row in SUBSCRIPTIONS.items()}


# --- PostgreSQL text renderings (Django's icontains operand is field::text in a UTC session) ----------
def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def ts_text(text):
    if text is None:
        return None
    value = moment(text).astimezone(timezone.utc)
    rendered = value.strftime('%Y-%m-%d %H:%M:%S')
    if value.microsecond:
        rendered += ('.%06d' % value.microsecond).rstrip('0')
    return rendered + '+00'


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


def text(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def payment_texts(row):
    """get_controlled_searchable_fields(Payment): the concrete non-relation fields."""
    return {'payment_id': row['id'], 'type': row['type'], 'subject': str(row['subject']), 'amount': row['amount'],
            'creation_date': ts_text(row['created']), 'payment_date': ts_text(row['paid_on']), 'paid': text(row['paid']),
            'expense': text(row['expense']), 'description': row['description'],
            'meta_payment_categories': None if row['meta_payment_categories'] is None else jsonb_text(row['meta_payment_categories']),
            'payment_intent_id': row['payment_intent_id'], 'archived': text(row['archived']),
            'invoice_fiscal_code': row['invoice_fiscal_code'], 'meta': None if row['meta'] is None else jsonb_text(row['meta']),
            'notes': row['notes'], 'deleted': text(row['deleted']), 'course': None if row['course'] is None else jsonb_text(row['course'])}


PERSON_FIELDS = ('first_name', 'last_name', 'tax_code', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date',
                 'address_city', 'address', 'notes', 'nationality', 'id_number')
SUPPLIER_FIELDS = ('name', 'email', 'address', 'city', 'tax_code', 'vat_number')
INSTRUCTOR_FIELDS = ('first_name', 'last_name', 'email', 'phone', 'tax_code', 'address')


def join_rows(row):
    """The search's LEFT JOINs: payment x associate x tutor relation x tutor, supplier, instructor."""
    base = {f'p.{k}': v for k, v in payment_texts(row).items()}
    if row['supplier']:
        base.update({f'supplier.{k}': SUPPLIER[k] for k in SUPPLIER_FIELDS})
    if row['instructor']:
        base.update({f'instructor.{k}': INSTRUCTOR[k] for k in INSTRUCTOR_FIELDS})
    associate = row['associate']
    if associate:
        base.update({f'associate.{k}': ASSOCIATES[associate][k] for k in PERSON_FIELDS})
    tutors = TUTORS.get(associate, []) if associate else []
    if not tutors:
        return [base]
    return [{**base, **{f'tutor.{k}': ASSOCIATES[tutor][k] for k in PERSON_FIELDS}} for tutor, _ in tutors]


def icontains(value, needle):
    return value is not None and needle.upper() in value.upper()


def date_variations(term):
    for pattern in (r'(\d{1,2})[.](\d{1,2})[.](\d{2,4})', r'(\d{1,2})[/](\d{1,2})[/](\d{2,4})', r'(\d{1,2})[-](\d{1,2})[-](\d{2,4})'):
        match = re.search(pattern, term)
        if match:
            day, month, year = match.groups()
            full = (('20' if int(year) <= datetime.now().year % 100 else '19') + year) if len(year) == 2 else year
            return [f'{day}.{month}.{year}', f'{day}.{month}.{full}', f'{day}/{month}/{year}', f'{day}/{month}/{full}',
                    f'{day}-{month}-{year}', f'{day}-{month}-{full}', f'{full}-{month.zfill(2)}-{day.zfill(2)}',
                    f'{month}.{day}.{year}', f'{month}/{day}/{year}', f'{month}-{day}-{year}']
    return []


def smart_match(row, raw):
    """sqlite_search's condition on one join row."""
    term = raw.strip().lower()
    words = re.findall(r'\w+', term)
    variations = date_variations(term)
    keys = list(row)

    def word_ok(word):
        if any(icontains(row[k], word) for k in keys):
            return True
        if re.match(r'^\d+$', word):
            dated = [k for k in keys if 'date' in k or 'created' in k]
            return any(icontains(row[k], v) for v in variations for k in dated)
        return False

    result = all(word_ok(word) for word in words)
    if len(words) > 1:
        perms = [' '.join(reversed(words)), ' '.join(words)]
        fields = [k for k in keys if any(t in k.split('.', 1)[1] for t in ('name', 'address', 'city', 'notes'))]
        result = result or any(icontains(row[k], perm) for perm in perms for k in fields)
    return result


def python_float(value):
    try:
        return float(value.replace(',', '.'))
    except ValueError:
        return None


def amount_match(row, number):
    """amount__icontains=str(float) or amount__exact=DecimalField.to_python(float) (Context(prec=9))."""
    exact = Decimal(row['amount']) == Context(prec=9).create_decimal_from_float(number)
    return repr(number).upper() in row['amount'].upper() or exact


def dmy(term):
    try:
        return datetime.strptime(term, '%d/%m/%Y').strftime('%Y-%m-%d')
    except ValueError:
        return term


def search_filter(labels, raw):
    """payment_list / payment_stats general search: (matching labels, join multiplicity)."""
    term = dmy(raw)
    number = python_float(term)
    if number is not None:
        return [k for k in labels if amount_match(PAYMENTS[k], number)], {k: 1 for k in labels}
    hits, multiplicity = [], {}
    for k in labels:
        count = sum(1 for joined in join_rows(PAYMENTS[k]) if smart_match(joined, term))
        if count:
            hits.append(k)
            multiplicity[k] = count
    return hits, multiplicity


# --- serializers ---------------------------------------------------------------------------------
def drf_time(text_value):
    if text_value is None:
        return None
    value = moment(text_value).astimezone(timezone.utc)
    return value.strftime('%Y-%m-%dT%H:%M:%S') + ('.%06d' % value.microsecond if value.microsecond else '') + 'Z'


MODEL_FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date', 'born_city', 'address_city', 'address',
                'address_cap', 'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
                'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role',
                'id_number', 'migrated', 'notes')


def basic_associate(label):
    """BasicAssociateSerializer: '__all__' plus full_name; nested tutors (M2M manager) are empty here."""
    row = ASSOCIATES[label]
    full = f"{row['first_name']} {row['last_name']}".strip()
    return {'associate_id': row['id'], 'full_name': full, 'tutors': [], **{k: row[k] for k in MODEL_FIELDS},
            'creation_date': drf_time(row['created']), 'user': row['user'], 'sport_association': row['association'],
            'group': row['group'], 'family': row['family']}


def optimized_associate(label):
    """AssociateOptimizedSerializer: available_tutors are the tutor relations (base manager)."""
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], 'first_name': row['first_name'], 'last_name': row['last_name'], 'is_minor': row['is_minor'],
            'available_tutors': [{'tutor': basic_associate(tutor), 'is_primary': primary} for tutor, primary in TUTORS.get(label, [])]}


def document(label):
    row = DOCUMENTS[label]
    return {'document_id': row['id'], 'creation_date': drf_time(row['created']), 'filepath': row['filepath'],
            'filename': row['filename'], 'file_size_bytes': row['size'], 'token': row['token']}


def instructor():
    row = INSTRUCTOR
    return {'instructor_id': row['id'], 'user': OWNER, 'first_name': row['first_name'], 'last_name': row['last_name'],
            'email': row['email'], 'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row['tax_code'],
            'address_city': None, 'address': row['address'], 'address_province': None, 'civic_number': None, 'study_title': None,
            'role': 'Istruttore', 'phone': row['phone'], 'phone_2': None, 'phone_3': None, 'phone_4': None, 'draft': False,
            'associated_user_id': None, 'is_volunteer': False, 'stipulated_contract_in': None, 'default_hourly_billing': row['hourly'],
            'default_percentage_billing': '0.00', 'documents': []}


def supplier():
    row = SUPPLIER
    return {'supplier_id': row['id'], 'name': row['name'], 'address': row['address'], 'tax_code': row['tax_code'],
            'sport_association': OWN, 'vat_number': row['vat_number'], 'type': 'supplier', 'email': row['email'],
            'phone_number': None, 'city': row['city'], 'cap': None, 'state': None, 'country': 'Italia', 'nationality': 'Italiana',
            'note': None, 'group': None, 'deleted': False}


def invoice():
    row = INVOICE
    return {'invoice_id': row['id'], 'document_token': DOCUMENTS[row['document']]['token'], 'creation_date': drf_time(row['created']),
            'membership_fee': row['membership_fee'], 'activity_fee': row['activity_fee'], 'meta_payment_categories': None,
            'description': row['description'], 'number': row['number'], 'archived': False, 'cancelled': False, 'meta': row['meta'],
            'imported_from_associami': False, 'deleted': False, 'document_pdf': DOCUMENTS[row['document']]['id'],
            'sport_association': OWN, 'selected_tutor': None, 'group': None, 'attached_documents': [DOCUMENTS[row['extra']]['id']]}


def subscription_of(label, group):
    """PaymentOptimizedSerializer.get_subscription_id with the X-Group-Id of the request (group-aware managers)."""
    row = PAYMENTS[label]

    def visible(sub):
        return group is None or sub.get('group') == group

    def by_associate(associate, *, live):
        found = [s for s in SUBSCRIPTIONS.values() if visible(s) and (s['associate'] == associate)
                 and not s.get('archived') and (not live or not s['deleted'])]
        found.sort(key=lambda s: s['created'])
        return found[0]['id'] if found else None

    if row['subject'] == 1:
        linked = [s for s in SUBSCRIPTIONS.values() if s['payment'] == label and not s['deleted'] and visible(s)]
        if linked:
            return linked[0]['id']
        return by_associate(row['associate'], live=True)
    if row['subject'] == 2:
        installments = [i for i in INSTALLMENTS.values() if i['payment'] == label and not i['deleted'] and group is None]
        if installments:
            return S[COURSE_SUBSCRIPTIONS[installments[0]['cs']]['subscription']]
        linked = [c for c in COURSE_SUBSCRIPTIONS.values() if c['payment'] == label and not c['deleted']]
        if linked:
            return S[linked[0]['subscription']]
        holdings = [h for h in HOLDINGS.values() if h['payment'] == label]
        return S[holdings[0]['subscription']] if holdings else None
    if row['subject'] == 0:
        return by_associate(row['associate'], live=False)
    return None


def course_of(label, group):
    """PaymentOptimizedSerializer.get_course."""
    row = PAYMENTS[label]
    if row['subject'] != 2:
        return None
    linked = [c for c in COURSE_SUBSCRIPTIONS.values() if c['payment'] == label and not c['deleted']]
    if linked:
        course = COURSES[linked[0]['course']]
        return {'title': course['title'], 'course_id': course['id']}
    installments = [i for i in INSTALLMENTS.values() if i['payment'] == label and not i['deleted'] and group is None]
    if installments:
        course = COURSES[COURSE_SUBSCRIPTIONS[installments[0]['cs']]['course']]
        return {'title': course['title'], 'course_id': course['id']}
    return row['course'] or None


def optimized(label, group=None):
    row = PAYMENTS[label]
    account = ACCOUNTS[row['account']] if row['account'] else None
    item = {
        'payment_id': row['id'], 'associate': optimized_associate(row['associate']) if row['associate'] else None,
        'subscription_id': subscription_of(label, group), 'instructor_id': INSTRUCTOR['id'] if row['instructor'] else None,
        'instructor': instructor() if row['instructor'] else None, 'supplier': supplier() if row['supplier'] else None,
        'supplier_id': SUPPLIER['id'] if row['supplier'] else None, 'custom_account_type': account['type'] if account else None,
        'custom_account_name': account['name'] if account else None, 'invoice': invoice() if row['invoice'] else None,
        'course': course_of(label, group), 'attachments': [document(row['attachment'])] if row['attachment'] else [],
        'payment_category_name': CATEGORY['name'] if row['category'] else None, 'type': row['type'], 'subject': row['subject'],
        'amount': row['amount'], 'creation_date': drf_time(row['created']), 'payment_date': drf_time(row['paid_on']),
        'paid': row['paid'], 'expense': row['expense'], 'description': row['description'],
        'meta_payment_categories': row['meta_payment_categories'], 'payment_intent_id': row['payment_intent_id'],
        'archived': row['archived'], 'invoice_fiscal_code': row['invoice_fiscal_code'], 'meta': row['meta'], 'notes': row['notes'],
        'deleted': row['deleted'], 'payment_category': CATEGORY['id'] if row['category'] else None,
        'user': OWNER if row['user'] else None, 'sport_association': row['association'],
        'custom_accounts': account['id'] if account else None, 'signature': None, 'group': row['group'],
    }
    return item


def listed(label, group=None):
    """payment_list's row: the serializer plus the InstructorHours document."""
    item = optimized(label, group)
    for hours in HOURS.values():
        if hours['payment'] == label and PAYMENTS[label]['instructor'] and hours['document']:
            item['instructor_document'] = DOCUMENTS[hours['document']]['id']
            item['instructor_document_token'] = DOCUMENTS[hours['document']]['token']
    return item


def by_subscription(label, group=None):
    """subscription_payments' row: the serializer plus is_carnet and the course subscription or carnet."""
    item = optimized(label, group)
    item['is_carnet'] = False
    if PAYMENTS[label]['subject'] != 2:
        return item
    linked = sorted((c for c in COURSE_SUBSCRIPTIONS.values() if c['payment'] == label), key=lambda c: c['id'])
    if linked:
        course = COURSES[linked[0]['course']]
    else:
        installments = sorted((i for i in INSTALLMENTS.values() if i['payment'] == label and group is None), key=lambda i: i['id'])
        if not installments:
            holdings = sorted((h for h in HOLDINGS.values() if h['payment'] == label), key=lambda h: h['id'])
            if holdings:
                item['is_carnet'] = True
                item['carnet'] = {'course_id': CARNET['id'], 'title': CARNET['title']}
            return item
        course = COURSES[COURSE_SUBSCRIPTIONS[installments[0]['cs']]['course']]
    item['course_subscriptions'] = {'course_id': course['id'], 'title': course['title'], 'deleted': course['deleted']}
    return item


# --- orderings -------------------------------------------------------------------------------------
def newest_first(labels):
    return sorted(labels, key=lambda k: moment(PAYMENTS[k]['created']), reverse=True)


def list_order(labels):
    """order_by('-payment_date', '-creation_date'): NULL payment dates first."""
    nulls = sorted((k for k in labels if PAYMENTS[k]['paid_on'] is None), key=lambda k: moment(PAYMENTS[k]['created']), reverse=True)
    dated = sorted((k for k in labels if PAYMENTS[k]['paid_on'] is not None),
                   key=lambda k: (moment(PAYMENTS[k]['paid_on']), moment(PAYMENTS[k]['created'])), reverse=True)
    return nulls + dated


def column_order(labels, key, descending):
    """order_by(field): ascending NULLs last, descending NULLs first."""
    present = [k for k in labels if key(k) is not None]
    missing = [k for k in labels if key(k) is None]
    present.sort(key=key, reverse=descending)
    return missing + present if descending else present + missing


def page(rows, ids, *, number=1, size=10, total=None):
    total = len(rows) if total is None else total
    pages = max(1, -(-total // size))
    start = (number - 1) * size
    return {'data': rows[start:start + size], 'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': size,
                                                      'rowIds': ids[start:start + size]}}


def list_page(labels, *, group=None, number=1, size=10, ordered=None):
    order = list_order(labels) if ordered is None else ordered
    return page([listed(k, group) for k in order], [P[k] for k in order], number=number, size=size)


cases = []


PRINTED = [k for k in HOURS.values() if k['document'] is None]


def prints_for(path, response):
    """payment_list's serializer branch prints each listed compensation whose hours have no document."""
    if not path.startswith('/payment/list') or not isinstance(response, dict) or not response.get('meta') or 'data' not in response:
        return []
    listed_ids = [row['payment_id'] for row in response['data']]
    return [{'path': f'/document/compensation/{PAYMENTS[h["payment"]]["id"]}', 'reply': {'status': 200, 'body': 'printed'}}
            for h in PRINTED if PAYMENTS[h['payment']]['id'] in listed_ids]


def add(name, actor, path, status, response, *, method='GET', headers=None, unordered=False, challenge=None, stripe=None,
        renders=(), generated=()):
    """unordered: the rows (and rowIds) are compared as multisets (an ordering Django leaves to the database).
    prints: the CURRENT_HOST requests the case makes (compensation prints of listed rows).
    stripe: (setup, requests): the instance's Stripe integration and Django cache entry around the request,
    and the Stripe API requests it makes with the stub's replies (none for every other case).
    renders: the PDF renderer requests the case makes, with the stub's replies (the athlete list's receipts).
    generated: the receipts those requests produce, in order: the invoice, the new Document's filename, whether
    the invoice is linked to it (with the storage task) and the receipt e-mail."""
    case = {'name': f'payment-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'unordered': unordered, 'prints': prints_for(path, response),
            'renders': list(renders), 'generated': list(generated)}
    if challenge is not None:
        case['challenge'] = challenge
    case['stripe_setup'], case['stripe'] = stripe if stripe else (None, [])
    cases.append(case)


LIST, STATS = '/payment/list', '/payment/stats'
sub_path = lambda label: f'/subscription/{S[label]}/payments'
missing = lambda key: {'detail': f'Missing permission: {key}'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
NOT_AVAILABLE = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
INVALID_PAGE = {'detail': 'Invalid page.'}
BAD_UUID = ['badly formed hexadecimal UUID string']


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


VISIBLE = [k for k, row in PAYMENTS.items() if row['association'] == OWN and not row['deleted'] and not row['archived']]
GROUPED = [k for k in VISIBLE if PAYMENTS[k]['group'] == OWN_GROUP]
OWNER_LIST = list_page(VISIBLE)

# --- identity, methods and OPTIONS ------------------------------------------------------------------
ROUTES = (('list', LIST, OWNER_LIST, 'bookeeping.payments.read', 'Payment List'),
          ('stats', STATS, None, 'bookeeping.payments.read', 'Payment Stats'),
          ('subscription', sub_path('mario'), None, 'association.members.read', 'Subscription Payments'))
for kind, path, body, permission, name in ROUTES:
    add(f'{kind}-anonymous', 'anonymous', path, 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-admin-without-session', 'admin', path, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-restricted-collaborator', 'restricted_collaborator', path, 403, missing(permission))
    add(f'{kind}-post', 'owner', path, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
    add(f'{kind}-delete', 'owner', path, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
    add(f'{kind}-options', 'owner', path, 200, options(name), method='OPTIONS')
    add(f'{kind}-owner-without-association', 'owner_without_association', path, 500, SERVER)
    add(f'{kind}-format-unknown', 'owner', path + '?format=xml', 404, {'detail': 'Not found.'})

# --- payment/list: identities ---
add('list-owner', 'owner', LIST, 200, OWNER_LIST)
add('list-full-collaborator', 'full_collaborator', LIST, 200, OWNER_LIST)
add('list-admin-impersonating-owner', 'admin', LIST, 200, OWNER_LIST, headers=ADMIN_OWNER)
add('list-foreign-owner', 'foreign_owner', LIST, 200, list_page(['abroad']))
# request.user.sportassociation is the base manager's row: the soft-deleted association lists (no payments).
add('list-deleted-association-owner', 'deleted_association_owner', LIST, 200, list_page([]))
add('list-athlete-unknown-course-subscription', 'athlete', LIST + '?course_subscription_id=' + uid('c5c5c5c5'), 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'})
add('list-athlete-malformed-course-subscription', 'athlete', LIST + '?course_subscription_id=nope', 400, BAD_UUID)
add('list-malformed-course-subscription', 'owner', LIST + '?course_subscription_id=nope', 400, BAD_UUID)
add('list-malformed-course-subscription-before-compressed', 'owner', LIST + '?mode=compressed&course_subscription_id=x', 400, BAD_UUID)
add('list-empty-course-subscription-ignored', 'owner', LIST + '?course_subscription_id=', 200, OWNER_LIST)
add('list-course-subscription-ignored-for-owner', 'owner', LIST + '?course_subscription_id=' + COURSE_SUBSCRIPTIONS['swim']['id'], 200,
    OWNER_LIST)
add('list-athlete-compressed', 'athlete', LIST + '?mode=compressed', 500, SERVER)
add('list-scoped-athlete-compressed', 'owner', LIST + '?mode=compressed', 500, SERVER, headers=SCOPED_ATHLETE)


# --- payment/list: compressed mode ---
def compressed(label):
    row = PAYMENTS[label]
    associate = ASSOCIATES[row['associate']] if row['associate'] else {}
    return {'payment_id': row['id'], 'amount': float(row['amount']), 'payment_date': drf_time(row['paid_on']), 'paid': row['paid'],
            'associate': row['associate'] and A[row['associate']], 'supplier': SUPPLIER['id'] if row['supplier'] else None,
            'instructor': INSTRUCTOR['id'] if row['instructor'] else None, 'associate__first_name': associate.get('first_name'),
            'associate__last_name': associate.get('last_name'), 'supplier__name': SUPPLIER['name'] if row['supplier'] else None,
            'instructor__first_name': INSTRUCTOR['first_name'] if row['instructor'] else None,
            'instructor__last_name': INSTRUCTOR['last_name'] if row['instructor'] else None,
            'custom_accounts__name': ACCOUNTS[row['account']]['name'] if row['account'] else None}


def compressed_body(labels):
    return {'data': [compressed(k) for k in newest_first(labels)]}


C = LIST + '?mode=compressed'
add('list-compressed', 'owner', C, 200, compressed_body(VISIBLE))
add('list-compressed-ignores-filters', 'owner', C + '&query[subject]=abc&sort[field]=nope&pagination[page]=9', 200, compressed_body(VISIBLE))
add('list-compressed-archived', 'owner', C + '&query[archived]=True', 200, compressed_body(['archived']))
add('list-compressed-archived-one', 'owner', C + '&query[archived]=1', 200, compressed_body(['archived']))
add('list-compressed-archived-false', 'owner', C + '&query[archived]=f', 200, compressed_body(VISIBLE))
add('list-compressed-archived-lowercase-true', 'owner', C + '&query[archived]=true', 500, SERVER)
add('list-compressed-group', 'owner', C, 200, compressed_body(GROUPED), headers={'X-Group-Id': OWN_GROUP})
add('list-compressed-group-malformed-ignored', 'owner', C, 200, compressed_body(VISIBLE), headers={'X-Group-Id': 'not-a-group'})
add('list-compressed-foreign-owner', 'foreign_owner', C, 200, compressed_body(['abroad']))
add('list-compressed-deleted-association-owner', 'deleted_association_owner', C, 200, {'data': []})
add('list-compressed-collaborator', 'full_collaborator', C, 200, compressed_body(VISIBLE))

# --- payment/list: archived, groups and filters ---
add('list-archived-true', 'owner', LIST + '?query[archived]=True', 200, list_page(['archived']))
add('list-archived-t', 'owner', LIST + '?query[archived]=t', 200, list_page(['archived']))
add('list-archived-false-text', 'owner', LIST + '?query[archived]=False', 200, OWNER_LIST)
add('list-archived-zero', 'owner', LIST + '?query[archived]=0', 200, OWNER_LIST)
add('list-archived-lowercase-true', 'owner', LIST + '?query[archived]=true', 500, SERVER)
add('list-archived-empty', 'owner', LIST + '?query[archived]=', 500, SERVER)
add('list-group', 'owner', LIST, 200, list_page(GROUPED, group=OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('list-group-foreign', 'owner', LIST, 200, list_page([]), headers={'X-Group-Id': FOREIGN_GROUP})
add('list-group-malformed-ignored', 'owner', LIST, 200, OWNER_LIST, headers={'X-Group-Id': '{oops}'})


def where(predicate, labels=None):
    return [k for k in (VISIBLE if labels is None else labels) if predicate(PAYMENTS[k])]


add('list-paid-true', 'owner', LIST + '?query[paid]=true', 200, list_page(where(lambda r: r['paid'])))
add('list-paid-other-is-false', 'owner', LIST + '?query[paid]=yes', 200, list_page(where(lambda r: not r['paid'])))
add('list-paid-empty-ignored', 'owner', LIST + '?query[paid]=', 200, OWNER_LIST)
add('list-expense-true', 'owner', LIST + '?query[expense]=true', 200, list_page(where(lambda r: r['expense'])))
add('list-expense-false', 'owner', LIST + '?query[expense]=false', 200, list_page(where(lambda r: not r['expense'])))
add('list-subject-course', 'owner', LIST + '?query[subject]=2', 200, list_page(where(lambda r: r['subject'] == 2)))
add('list-subject-python-int', 'owner', LIST + '?query[subject]=%201%20', 200, list_page(where(lambda r: r['subject'] == 1)))
add('list-subject-zero', 'owner', LIST + '?query[subject]=0', 200, list_page(where(lambda r: r['subject'] == 0)))
add('list-subject-text', 'owner', LIST + '?query[subject]=abc', 500, SERVER)
add('list-subject-float', 'owner', LIST + '?query[subject]=1.0', 500, SERVER)
add('list-subject-out-of-range', 'owner', LIST + '?query[subject]=-1', 200, list_page([]))
add('list-subject-empty-ignored', 'owner', LIST + '?query[subject]=', 200, OWNER_LIST)
add('list-type', 'owner', LIST + '?query[type]=transfer', 200, list_page(where(lambda r: r['type'] == 'transfer')))
add('list-type-unknown', 'owner', LIST + '?query[type]=bitcoin', 200, list_page([]))
add('list-category', 'owner', LIST + '?query[payment_categories]=' + CATEGORY['id'], 200, list_page(where(lambda r: r['category'])))
add('list-category-list', 'owner', LIST + f"?query[payment_categories]={CATEGORY['id']},{uid('cafecafe')}", 200,
    list_page(where(lambda r: r['category'])))
add('list-category-none', 'owner', LIST + '?query[payment_categories]=[]', 200, list_page(where(lambda r: not r['category'])))
add('list-category-malformed', 'owner', LIST + '?query[payment_categories]=nope', 500, SERVER)
add('list-category-trailing-comma', 'owner', LIST + f"?query[payment_categories]={CATEGORY['id']},", 500, SERVER)
add('list-account', 'owner', LIST + '?query[account]=' + ACCOUNTS['bank']['id'], 200, list_page(where(lambda r: r['account'] == 'bank')))
add('list-account-none', 'owner', LIST + '?query[account]=[]', 200, list_page(where(lambda r: r['account'] is None)))
add('list-account-malformed', 'owner', LIST + '?query[account]=1', 500, SERVER)
add('list-associate', 'owner', LIST + '?associate_id=' + A['mario'], 200, list_page(where(lambda r: r['associate'] == 'mario')))
add('list-associate-hyphenless', 'owner', LIST + '?associate_id=' + A['twin'].replace('-', '').upper(), 200,
    list_page(where(lambda r: r['associate'] == 'twin')))
add('list-associate-malformed', 'owner', LIST + '?associate_id=nope', 500, SERVER)
add('list-associate-empty', 'owner', LIST + '?associate_id=', 500, SERVER)
add('list-filters-combined', 'owner', LIST + f"?associate_id={A['mario']}&query[paid]=true&query[subject]=2", 200,
    list_page(where(lambda r: r['associate'] == 'mario' and r['paid'] and r['subject'] == 2)))


# --- payment/list: payment range ---
def in_range(row, start, end):
    when = moment(row['paid_on']) if row['paid_on'] else moment(row['created'])
    return start <= when <= end


def ranged(start, end, labels=None):
    lo, hi = moment(start), moment(end)
    return where(lambda r: in_range(r, lo, hi), labels)


R = LIST + '?query[payment_range]='
add('list-range-ymd', 'owner', R + '2024/04/01%20al%202024/07/02', 200, list_page(ranged('2024-04-01T00:00:00Z', '2024-07-02T23:59:59Z')))
add('list-range-dmy', 'owner', R + '01/04/2024%20al%2002/07/2024', 200, list_page(ranged('2024-04-01T00:00:00Z', '2024-07-02T23:59:59Z')))
add('list-range-single-digits', 'owner', R + '2024/4/1%20al%202024/7/1', 200, list_page(ranged('2024-04-01T00:00:00Z', '2024-07-01T23:59:59Z')))
add('list-range-creation-date-for-unpaid', 'owner', R + '2024/03/01%20al%202024/03/31', 200,
    list_page(ranged('2024-03-01T00:00:00Z', '2024-03-31T23:59:59Z')))
add('list-range-mixed-formats-ignored', 'owner', R + '2024/04/01%20al%2002/07/2024', 200, OWNER_LIST)
add('list-range-without-separator-ignored', 'owner', R + '2024/04/01', 200, OWNER_LIST)
add('list-range-three-parts-ignored', 'owner', R + '2024/04/01%20al%202024/05/01%20al%202024/06/01', 200, OWNER_LIST)
# Two unparseable dates: the last year up to today (no seeded payment is that recent).
add('list-range-fallback-last-year', 'owner', R + 'ieri%20al%20oggi', 200, list_page([]))
add('list-range-invalid-day-fallback', 'owner', R + '2024/02/30%20al%202024/03/01', 200, list_page([]))
add('list-range-empty-ignored', 'owner', R, 200, OWNER_LIST)
add('list-range-skipped-for-uuid-search', 'owner', R + '2030/01/01%20al%202030/12/31&query[generalSearch]=' + P['installment'], 200,
    list_page(['installment']))
add('list-range-and-filter', 'owner', R + '2024/01/01%20al%202024/12/31&query[expense]=true', 200,
    list_page(ranged('2024-01-01T00:00:00Z', '2024-12-31T23:59:59Z', where(lambda r: r['expense']))))


# --- payment/list: general search ---
def searched(raw, labels=None):
    hits, _ = search_filter(VISIBLE if labels is None else labels, raw)
    return hits


SEARCH = LIST + '?query[generalSearch]='
for name, raw, query in (
        ('last-name', 'rossi', 'rossi'), ('upper', 'ROSSI', 'ROSSI'), ('tutor-name', 'lucia', 'lucia'),
        ('words', 'Mario Rossi', 'Mario%20Rossi'), ('words-in-other-order', 'rossi, mario', 'rossi,%20mario'),
        ('tutor-words-same-row-only', 'lucia paolo', 'lucia%20paolo'), ('tutor-words-one-tutor', 'lucia verdi', 'lucia%20verdi'),
        ('supplier', 'palestre', 'palestre'), ('instructor', 'giulia', 'giulia'), ('description', 'giroconto', 'giroconto'),
        ('notes', 'contanti', 'contanti'), ('json-meta', 'sportello', 'sportello'), ('json-course', 'JSON', 'JSON'),
        ('boolean-text', 'true', 'true'), ('type-text', 'online', 'online'), ('payment-id', P['supplier'], P['supplier']),
        ('date-dmy', '10/02/2024', '10/02/2024'), ('date-words', '2024 09 01', '2024%2009%2001'),
        ('underscore-escaped', 'pi_', 'pi_'), ('percent-only', '%', '%25'), ('blank', '   ', '%20%20%20'),
        ('accented', 'CITTÀ', 'CITT%C3%80'), ('tax-code', 'rssmra', 'rssmra'), ('nothing', 'zzzz', 'zzzz')):
    add(f'list-search-{name}', 'owner', SEARCH + query, 200, list_page(searched(raw)))
for name, raw, query in (('amount', '50', '50'), ('amount-comma', '15,5', '15,5'), ('amount-zero', '0', '0'),
                         ('amount-exponent', '1e1', '1e1'), ('amount-padded', ' 40 ', '%2040%20'), ('amount-underscore', '1_20', '1_20')):
    add(f'list-search-{name}', 'owner', SEARCH + query, 200, list_page(searched(raw)))
add('list-search-group', 'owner', SEARCH + 'rossi', 200, list_page(searched('rossi', GROUPED), group=OWN_GROUP),
    headers={'X-Group-Id': OWN_GROUP})
add('list-search-and-filter', 'owner', SEARCH + 'rossi&query[paid]=true', 200,
    list_page(searched('rossi', where(lambda r: r['paid']))))
add('list-search-paginated', 'owner', SEARCH + 'rossi&pagination[perpage]=2&pagination[page]=2', 200,
    list_page(searched('rossi'), number=2, size=2))

# --- payment/list: sorting ---
S_ = LIST + '?sort[field]='
amount_key = lambda k: Decimal(PAYMENTS[k]['amount'])
add('list-sort-amount', 'owner', S_ + 'amount&sort[sort]=asc', 200, list_page(VISIBLE, ordered=column_order(VISIBLE, amount_key, False)))
add('list-sort-amount-desc', 'owner', S_ + 'amount&sort[sort]=desc', 200, list_page(VISIBLE, ordered=column_order(VISIBLE, amount_key, True)))
add('list-sort-without-direction-is-asc', 'owner', S_ + 'amount', 200, list_page(VISIBLE, ordered=column_order(VISIBLE, amount_key, False)))
add('list-sort-other-direction-is-asc', 'owner', S_ + 'amount&sort[sort]=up', 200,
    list_page(VISIBLE, ordered=column_order(VISIBLE, amount_key, False)))
add('list-sort-negated-field', 'owner', S_ + '-amount&sort[sort]=asc', 200, list_page(VISIBLE, ordered=column_order(VISIBLE, amount_key, True)))
add('list-sort-double-negation', 'owner', S_ + '-amount&sort[sort]=desc', 500, SERVER)
paid_key = lambda k: moment(PAYMENTS[k]['paid_on'])
# NULLs sort last; their relative order is the database's, so the page holds only the dated rows.
DATED = len([k for k in VISIBLE if PAYMENTS[k]['paid_on']])
add('list-sort-payment-date-nulls-last', 'owner', S_ + f'payment_date&sort[sort]=asc&pagination[perpage]={DATED}', 200,
    list_page(VISIBLE, size=DATED, ordered=column_order(VISIBLE, paid_key, False)))
add('list-sort-payment-date-desc-nulls-first', 'owner', S_ + 'payment_date&sort[sort]=desc&pagination[perpage]=3&pagination[page]=2', 200,
    list_page(VISIBLE, size=3, number=2, ordered=column_order(VISIBLE, paid_key, True)))
created_key = lambda k: moment(PAYMENTS[k]['created'])
add('list-sort-creation-date', 'owner', S_ + 'creation_date&sort[sort]=desc', 200,
    list_page(VISIBLE, ordered=column_order(VISIBLE, created_key, True)))
add('list-sort-pk', 'owner', S_ + 'pk&sort[sort]=asc', 200, list_page(VISIBLE, ordered=sorted(VISIBLE, key=lambda k: P[k])))
add('list-sort-payment-id-desc', 'owner', S_ + 'payment_id&sort[sort]=desc', 200,
    list_page(VISIBLE, ordered=sorted(VISIBLE, key=lambda k: P[k], reverse=True)))
add('list-sort-random', 'owner', S_ + '?&pagination[perpage]=20', 200, list_page(VISIBLE, size=20), unordered=True)
add('list-sort-random-desc', 'owner', S_ + '?&sort[sort]=desc', 500, SERVER)
add('list-sort-unknown', 'owner', S_ + 'nope&sort[sort]=asc', 500, SERVER)
add('list-sort-unknown-before-page', 'owner', S_ + 'nope&pagination[page]=9', 500, SERVER)
add('list-sort-relevance-unknown', 'owner', S_ + 'relevance&query[generalSearch]=rossi', 500, SERVER)
add('list-sort-lazy-path-error', 'owner', S_ + 'associate__nope&sort[sort]=asc', 500, SERVER)
add('list-sort-path-before-page', 'owner', S_ + 'associate__nope&pagination[page]=9', 500, SERVER)
# Only the payment's own columns order the list: paths through a relation or the owner account, transforms and the
# stored signature or payment intent answer as an unknown ordering.
for _name, _field in (('user-path', 'user__email'), ('user-password', 'user__password'), ('owner-account-path', 'sport_association__user__password'),
                      ('association-path', 'sport_association__denomination'), ('associate-path', 'associate__last_name'),
                      ('supplier-path', 'supplier__name'), ('payment-intent', 'payment_intent_id'), ('signature', 'signature'),
                      ('signature-key', 'signature_id'), ('transform', 'creation_date__year'), ('reverse-relation', 'attachments')):
    add('list-sort-' + _name + '-server-error', 'owner', S_ + _field + '&sort[sort]=desc', 500, SERVER)
# The "Informazioni" column sends sort[field]=user: a relation whose model (User) has no Meta.ordering orders by
# user_id (NULLs last ascending, first descending); ties among NULLs are the database's order.
USERED = [k for k in VISIBLE if PAYMENTS[k].get('user')]
assert len(USERED) == 1
add('list-sort-user-first-page', 'owner', S_ + 'user&sort[sort]=asc&pagination[perpage]=1', 200,
    list_page(VISIBLE, size=1, ordered=USERED + [k for k in VISIBLE if k not in USERED]))
add('list-sort-user-desc-all', 'owner', S_ + f'user&sort[sort]=desc&pagination[perpage]={len(VISIBLE)}', 200,
    list_page(VISIBLE, size=len(VISIBLE)), unordered=True)
add('list-sort-user-desc-last-page', 'owner', S_ + f'user&sort[sort]=desc&pagination[perpage]={len(VISIBLE) - 1}&pagination[page]=2',
    200, list_page(VISIBLE, size=len(VISIBLE) - 1, number=2, ordered=[k for k in VISIBLE if k not in USERED] + USERED))
add('list-sort-with-search', 'owner', SEARCH + 'rossi&sort[field]=amount&sort[sort]=desc', 200,
    list_page(searched('rossi'), ordered=column_order(searched('rossi'), amount_key, True)))

# --- payment/list: pagination ---
ORDER = list_order(VISIBLE)
add('list-page-two', 'owner', LIST + '?pagination[page]=2', 200, list_page(VISIBLE, number=2))
add('list-page-last', 'owner', LIST + '?pagination[perpage]=4&pagination[page]=last', 200, list_page(VISIBLE, number=3, size=4))
add('list-page-python-int', 'owner', LIST + '?pagination[perpage]=%204%20&pagination[page]=+2', 200, list_page(VISIBLE, number=2, size=4))
add('list-page-empty-is-first', 'owner', LIST + '?pagination[perpage]=4&pagination[page]=', 200, list_page(VISIBLE, size=4))
add('list-page-beyond', 'owner', LIST + '?pagination[page]=3', 404, INVALID_PAGE)
add('list-page-zero', 'owner', LIST + '?pagination[page]=0', 404, INVALID_PAGE)
add('list-page-text', 'owner', LIST + '?pagination[page]=abc', 404, INVALID_PAGE)
add('list-perpage-zero-default', 'owner', LIST + '?pagination[perpage]=0', 200, OWNER_LIST)
add('list-perpage-text-default', 'owner', LIST + '?pagination[perpage]=all', 200, OWNER_LIST)
HUGE = 10 ** 25
add('list-perpage-huge', 'owner', LIST + f'?pagination[perpage]={HUGE}', 200,
    {'data': [listed(k) for k in ORDER], 'meta': {'total': len(ORDER), 'page': 1, 'pages': 1, 'perpage': HUGE,
                                                 'rowIds': [P[k] for k in ORDER]}})
add('list-empty-page-two', 'owner', LIST + '?query[type]=bitcoin&pagination[page]=2', 404, INVALID_PAGE)


# --- payment/stats ---
def stats(labels, multiplicity=None, stripe=None):
    """stripe: the (total, stripe_fee) charges; net_balance subtracts Decimal(total or 0) (default context)."""
    multiplicity = multiplicity or {}
    rows = [PAYMENTS[k] for k in labels]

    def total(predicate):
        value = sum((Decimal(r['amount']) for r in rows if predicate(r)), Decimal('0.00'))
        return float(value) if value else 0

    accounts = {'cash': 0, 'bank': 0, 'other': 0}
    names = {1: 'cash', 2: 'bank', 3: 'other'}
    for k in labels:
        row = PAYMENTS[k]
        if row['paid'] and row['account']:
            name = names[ACCOUNTS[row['account']]['type']]
            change = Decimal(row['amount']) * multiplicity.get(k, 1) * (-1 if row['expense'] else 1)
            accounts[name] = accounts[name] + change
    income = sum((Decimal(r['amount']) for r in rows if not r['expense']), Decimal('0'))
    expenses = sum((Decimal(r['amount']) for r in rows if r['expense']), Decimal('0'))
    to_pay = sum((Decimal(r['amount']) for r in rows if r['expense'] and not r['paid']), Decimal('0'))
    to_cash_in = sum((Decimal(r['amount']) for r in rows if not r['expense'] and not r['paid']), Decimal('0'))
    total_fee, fee = stripe or (0, 0)
    return {'data': {'stripe_charges': {'total': total_fee, 'stripe_fee': fee},
                     'monthly_income': total(lambda r: not r['expense'] and r['paid']),
                     'monthly_expenses': total(lambda r: r['expense'] and r['paid']),
                     'monthly_to_pay': total(lambda r: r['expense'] and not r['paid']),
                     'monthly_to_cash_in': total(lambda r: not r['expense'] and not r['paid']),
                     'current_income': total(lambda r: not r['expense']), 'current_expenses': total(lambda r: r['expense']),
                     'accounts': {k: (float(v) if isinstance(v, Decimal) else v) for k, v in accounts.items()},
                     'total_transactions': len(rows),
                     'net_balance': float(income - expenses + to_pay - to_cash_in - Decimal(total_fee or 0))}}


def stats_range(start, end, labels=None):
    return ranged(start, end, labels)


YEAR = '?query[payment_range]=2024/01/01%20al%202024/12/31'
IN_YEAR = stats_range('2024-01-01T00:00:00Z', '2024-12-31T00:00:00Z')
add('stats-owner-this-month', 'owner', STATS, 200, stats([]))


# Stripe configured (the instance's integration): the fees of the range from the cache, else
# BalanceTransaction.list(created={gte, lte}).auto_paging_iter() (payouts skipped, only stripe_fee
# amounts summed), round(float/100, 2) cached for 300 seconds; any exception: zero charges, uncached.
STRIPE = {'secret_key': 'sk_test_payment_reads_fixture', 'public_key': 'pk_test_payment_reads_fixture',
          'api_version': '2025-02-24.acacia', 'user_agent': 'Stripe/v1 PythonBindings/11.6.0'}
STRIPE_IDENTITY = hashlib.sha256(STRIPE['secret_key'].encode()).hexdigest()[:24]


def epoch(text):
    return int(datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp())


def stripe_page(items, more):
    return {'object': 'list', 'url': '/v1/balance_transactions', 'has_more': more, 'data': items}


def stripe_get(start, end, reply, *, after=None, telemetry=None, status=200):
    path = f'/v1/balance_transactions?created%5Bgte%5D={start}&created%5Blte%5D={end}'
    if after:
        path += '&starting_after=' + after
    return {'method': 'GET', 'path': path, 'body': '', 'same_key_as_previous': False, 'telemetry': telemetry,
            'status': status, 'response': reply}


def stripe_setup(start, end, *, enabled=True, before=None, after=None):
    return {'enabled': enabled, 'cache_key': f'stripe_fees_direct_{STRIPE_IDENTITY}_{start}_{end}', 'cache_before': before,
            'cache_after': after}


START, END = epoch('2024-01-01T00:00:00Z'), epoch('2024-12-31T00:00:00Z')
FIRST = stripe_page([
    {'id': 'txn_reads_1', 'object': 'balance_transaction', 'type': 'charge',
     'fee_details': [{'type': 'stripe_fee', 'amount': 31}, {'type': 'application_fee', 'amount': 50}]},
    {'id': 'txn_reads_2', 'object': 'balance_transaction', 'type': 'payout', 'fee_details': [{'type': 'stripe_fee', 'amount': 999}]}], True)
SECOND = stripe_page([{'id': 'txn_reads_3', 'object': 'balance_transaction', 'type': 'charge',
                       'fee_details': [{'type': 'stripe_fee', 'amount': 1234}]}], False)
FETCHED = {'total': 12.65, 'stripe_fee': 12.65}
add('stats-stripe-fees-fetched-and-cached', 'owner', STATS + YEAR, 200, stats(IN_YEAR, stripe=(12.65, 12.65)),
    stripe=(stripe_setup(START, END, after=FETCHED),
            [stripe_get(START, END, FIRST), stripe_get(START, END, SECOND, after='txn_reads_2', telemetry='req_stub_1')]))
CACHED = {'total': 3.3, 'stripe_fee': 2.2}
add('stats-stripe-fees-from-cache', 'full_collaborator', STATS + YEAR, 200, stats(IN_YEAR, stripe=(3.3, 2.2)),
    stripe=(stripe_setup(START, END, before=CACHED, after=CACHED), []))
add('stats-stripe-error-zero-uncached', 'owner', STATS + YEAR, 200, stats(IN_YEAR),
    stripe=(stripe_setup(START, END),
            [stripe_get(START, END, {'error': {'type': 'invalid_request_error', 'message': 'fixture'}}, status=400)]))
add('stats-stripe-missing-fee-type-zero-uncached', 'owner', STATS + YEAR, 200, stats(IN_YEAR),
    stripe=(stripe_setup(START, END), [stripe_get(START, END, stripe_page([{'id': 'txn_reads_4', 'type': 'charge',
                                                                             'fee_details': [{'amount': 5}]}], False))]))
add('stats-stripe-disabled', 'owner', STATS + YEAR, 200, stats(IN_YEAR), stripe=(stripe_setup(START, END, enabled=False), []))
MID_START, MID_END = epoch('2024-02-01T00:00:00Z'), epoch('2024-07-02T00:00:00Z')
add('stats-stripe-no-transactions', 'owner', STATS + '?query[payment_range]=2024/02/01%20al%202024/07/02', 200,
    stats(stats_range('2024-02-01T00:00:00Z', '2024-07-02T00:00:00Z'), stripe=(0.0, 0.0)),
    stripe=(stripe_setup(MID_START, MID_END, after={'total': 0.0, 'stripe_fee': 0.0}),
            [stripe_get(MID_START, MID_END, stripe_page([], False))]))
add('stats-full-collaborator', 'full_collaborator', STATS + YEAR, 200, stats(IN_YEAR))
add('stats-admin-impersonating-owner', 'admin', STATS + YEAR, 200, stats(IN_YEAR), headers=ADMIN_OWNER)
add('stats-scoped-athlete-not-available', 'owner', STATS, 403, NOT_AVAILABLE, headers=SCOPED_ATHLETE)
add('stats-athlete', 'athlete', STATS, 500, SERVER)
add('stats-deleted-association-owner', 'deleted_association_owner', STATS, 500, SERVER)
add('stats-foreign-owner', 'foreign_owner', STATS + YEAR, 200, stats(['abroad']))
add('stats-year', 'owner', STATS + YEAR, 200, stats(IN_YEAR))
add('stats-year-dmy', 'owner', STATS + '?query[payment_range]=01/01/2024%20al%2031/12/2024', 200, stats(IN_YEAR))
add('stats-end-is-midnight', 'owner', STATS + '?query[payment_range]=2024/02/01%20al%202024/07/02', 200,
    stats(stats_range('2024-02-01T00:00:00Z', '2024-07-02T00:00:00Z')))
add('stats-end-day-after', 'owner', STATS + '?query[payment_range]=2024/02/01%20al%202024/07/03', 200,
    stats(stats_range('2024-02-01T00:00:00Z', '2024-07-03T00:00:00Z')))
add('stats-range-mixed-falls-back', 'owner', STATS + '?query[payment_range]=2024/01/01%20al%2031/12/2024', 200, stats([]))
add('stats-range-invalid-falls-back', 'owner', STATS + '?query[payment_range]=ieri%20al%20oggi', 200, stats([]))
add('stats-range-without-separator', 'owner', STATS + '?query[payment_range]=2024/01/01', 500, SERVER)
add('stats-range-empty', 'owner', STATS + '?query[payment_range]=', 500, SERVER)
add('stats-group', 'owner', STATS + YEAR, 200, stats([k for k in IN_YEAR if PAYMENTS[k]['group'] == OWN_GROUP]),
    headers={'X-Group-Id': OWN_GROUP})
add('stats-group-malformed-ignored', 'owner', STATS + YEAR, 200, stats(IN_YEAR), headers={'X-Group-Id': 'nope'})
add('stats-ignores-archived-parameter', 'owner', STATS + YEAR + '&query[archived]=True', 200, stats(IN_YEAR))
add('stats-paid', 'owner', STATS + YEAR + '&query[paid]=true', 200, stats(where(lambda r: r['paid'], IN_YEAR)))
add('stats-paid-empty-is-false', 'owner', STATS + YEAR + '&query[paid]=', 200, stats(where(lambda r: not r['paid'], IN_YEAR)))
add('stats-expense', 'owner', STATS + YEAR + '&query[expense]=true', 200, stats(where(lambda r: r['expense'], IN_YEAR)))
add('stats-subject', 'owner', STATS + YEAR + '&query[subject]=2', 200, stats(where(lambda r: r['subject'] == 2, IN_YEAR)))
add('stats-subject-empty', 'owner', STATS + YEAR + '&query[subject]=', 500, SERVER)
add('stats-subject-out-of-range', 'owner', STATS + YEAR + '&query[subject]=40000', 200, stats([]))
add('stats-type-empty', 'owner', STATS + YEAR + '&query[type]=', 200, stats([]))
add('stats-type', 'owner', STATS + YEAR + '&query[type]=cash', 200, stats(where(lambda r: r['type'] == 'cash', IN_YEAR)))
add('stats-category-none', 'owner', STATS + YEAR + '&query[payment_categories]=[]', 200, stats(where(lambda r: not r['category'], IN_YEAR)))
add('stats-category', 'owner', STATS + YEAR + '&query[payment_categories]=' + CATEGORY['id'], 200,
    stats(where(lambda r: r['category'], IN_YEAR)))
add('stats-category-malformed', 'owner', STATS + YEAR + '&query[payment_categories]=x', 500, SERVER)
add('stats-account', 'owner', STATS + YEAR + '&query[account]=' + ACCOUNTS['cash']['id'], 200,
    stats(where(lambda r: r['account'] == 'cash', IN_YEAR)))
add('stats-account-none', 'owner', STATS + YEAR + '&query[account]=[]', 200, stats(where(lambda r: r['account'] is None, IN_YEAR)))
for name, raw, query in (('rossi', 'rossi', 'rossi'), ('tutor', 'verdi', 'verdi'), ('supplier', 'palestre', 'palestre'),
                         ('amount', '40', '40'), ('date', '15/04/2024', '15/04/2024'), ('none', 'zzzz', 'zzzz')):
    hits, multiplicity = search_filter(IN_YEAR, raw)
    add(f'stats-search-{name}', 'owner', STATS + YEAR + '&query[generalSearch]=' + query, 200, stats(hits, multiplicity))

# --- subscription/<uid>/payments ---
def members(label):
    """The association's live payments of the associate or of an associate whose tax code is iexact the stripped one."""
    associate = SUBSCRIPTIONS[label]['associate']
    tax = (ASSOCIATES[associate]['tax_code'] or '').strip()

    def member(row):
        if row['associate'] == associate:
            return True
        other = ASSOCIATES[row['associate']]['tax_code'] if row['associate'] else None
        return bool(tax) and other is not None and other.upper() == tax.upper()
    return newest_first([k for k, row in PAYMENTS.items() if row['association'] == OWN and not row['deleted'] and member(row)])


MEMBERS = members('mario')


def sub_page(labels, *, group=None, number=1, size=10, ordered=None):
    order = newest_first(labels) if ordered is None else ordered
    return page([by_subscription(k, group) for k in order], [P[k] for k in order], number=number, size=size)


SUB = sub_path('mario')
add('subscription-owner', 'owner', SUB, 200, sub_page(MEMBERS))
add('subscription-full-collaborator', 'full_collaborator', SUB, 200, sub_page(MEMBERS))
add('subscription-admin-impersonating-owner', 'admin', SUB, 200, sub_page(MEMBERS), headers=ADMIN_OWNER)
add('subscription-athlete', 'athlete', SUB, 401, '<empty>', challenge=False)
# The scoped-request check runs first: the athlete's session cannot reference this subscription.
add('subscription-scoped-athlete-reference', 'owner', SUB, 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers=SCOPED_ATHLETE)
add('subscription-athlete-malformed', 'athlete', '/subscription/nope/payments', 401, '<empty>', challenge=False)
add('subscription-malformed', 'owner', '/subscription/nope/payments', 400, BAD_UUID)
add('subscription-unknown', 'owner', '/subscription/' + uid('5ab5c0de') + '/payments', 500, SERVER)
add('subscription-foreign', 'owner', sub_path('abroad'), 403, {'detail': 'User not allowed.'})
add('subscription-without-associate', 'owner', sub_path('nobody'), 500, SERVER)
add('subscription-deleted-association-owner', 'deleted_association_owner', SUB, 500, SERVER)
add('subscription-tax-code-twin', 'owner', sub_path('twin'), 200, sub_page(members('twin')))
add('subscription-deleted-subscription', 'owner', sub_path('lucia'), 200, sub_page([]))
add('subscription-group-hides-subscription', 'owner', SUB, 500, SERVER, headers={'X-Group-Id': OWN_GROUP})
add('subscription-group-malformed-ignored', 'owner', SUB, 200, sub_page(MEMBERS), headers={'X-Group-Id': 'x'})
add('subscription-hyphenless-uuid', 'owner', '/subscription/' + S['mario'].replace('-', '') + '/payments', 200, sub_page(MEMBERS))
add('subscription-paid', 'owner', SUB + '?query[paid]=true', 200, sub_page(where(lambda r: r['paid'], MEMBERS)))
add('subscription-paid-empty-is-false', 'owner', SUB + '?query[paid]=', 200, sub_page(where(lambda r: not r['paid'], MEMBERS)))
add('subscription-expense-false', 'owner', SUB + '?query[expense]=no', 200, sub_page(where(lambda r: not r['expense'], MEMBERS)))
add('subscription-subject', 'owner', SUB + '?query[subject]=2', 200, sub_page(where(lambda r: r['subject'] == 2, MEMBERS)))
add('subscription-subject-empty', 'owner', SUB + '?query[subject]=', 500, SERVER)
add('subscription-type', 'owner', SUB + '?query[type]=pos', 200, sub_page(where(lambda r: r['type'] == 'pos', MEMBERS)))
add('subscription-type-empty', 'owner', SUB + '?query[type]=', 200, sub_page([]))
add('subscription-archived-parameter-ignored', 'owner', SUB + '?query[archived]=nope', 200, sub_page(MEMBERS))


def sub_rows(label):
    """subscription_payments' search joins: course subscriptions x installments (any state)."""
    row = PAYMENTS[label]
    associate = ASSOCIATES[row['associate']] if row['associate'] else {}
    base = {'payment_id': row['id'], 'notes': row['notes'], 'meta': None if row['meta'] is None else jsonb_text(row['meta']),
            'description': row['description'], 'category': CATEGORY['name'] if row['category'] else None,
            'full_name': (associate.get('first_name') or '') + ' ' + (associate.get('last_name') or ''),
            'supplier': SUPPLIER['name'] if row['supplier'] else None,
            'instructor_first': INSTRUCTOR['first_name'] if row['instructor'] else None,
            'instructor_last': INSTRUCTOR['last_name'] if row['instructor'] else None,
            'first_name': associate.get('first_name'), 'last_name': associate.get('last_name'), 'tax_code': associate.get('tax_code'),
            'payment_date': ts_text(row['paid_on']), 'type': row['type'], 'subject': str(row['subject']),
            'creation_date': ts_text(row['created']), 'account': ACCOUNTS[row['account']]['name'] if row['account'] else None}
    linked = [COURSES[c['course']]['title'] for c in COURSE_SUBSCRIPTIONS.values() if c['payment'] == label] or [None]
    rates = [COURSES[COURSE_SUBSCRIPTIONS[i['cs']]['course']]['title'] for i in INSTALLMENTS.values() if i['payment'] == label] or [None]
    return [{**base, 'cs_course': a, 'inst_course': b} for a in linked for b in rates]


def sub_search(raw, labels=MEMBERS):
    term = dmy(raw)
    number = python_float(term)
    out = []
    for k in newest_first(labels):
        if number is not None:
            if amount_match(PAYMENTS[k], number):
                out.append(k)
            continue
        out.extend(k for joined in sub_rows(k) if any(icontains(v, term) for v in joined.values()))
    return out


SS = SUB + '?query[generalSearch]='
for name, raw, query in (('full-name', 'mario rossi', 'mario%20rossi'), ('course-title-duplicates', 'corso', 'corso'),
                         ('old-course', 'vecchio', 'vecchio'), ('tax-code', 'RSSMRA90', 'RSSMRA90'), ('json-meta', 'sportello', 'sportello'),
                         ('payment-date', '15/04/2024', '15/04/2024'), ('category', 'sociali', 'sociali'), ('nothing', 'zzzz', 'zzzz'),
                         ('not-stripped', ' rossi', '%20rossi'), ('amount', '30', '30'), ('amount-one', '1', '1')):
    hits = sub_search(raw)
    add(f'subscription-search-{name}', 'owner', SS + query, 200, sub_page(hits, ordered=hits))
# float('inf') is refused by the amount lookup when the filter is built, as NaN is: payment_list and payment_stats do
# not catch it, subscription_payments falls back to the text search.
add('list-search-infinite', 'owner', SEARCH + 'inf', 500, SERVER)
add('list-search-negative-infinity', 'owner', SEARCH + '-Infinity', 500, SERVER)
add('stats-search-infinite', 'owner', STATS + YEAR + '&query[generalSearch]=inf', 500, SERVER)
add('subscription-search-infinite', 'owner', SS + 'infinity', 200, sub_page(sub_search('infinity'), ordered=sub_search('infinity')))
add('subscription-search-percent-escaped', 'owner', SS + '%25', 200, sub_page(sub_search('%'), ordered=sub_search('%')))
created_order = column_order(MEMBERS, created_key, False)
add('subscription-sort-creation-asc', 'owner', SUB + '?sort[field]=creation_date', 200, sub_page(MEMBERS, ordered=created_order))
add('subscription-sort-amount-desc', 'owner', SUB + '?sort[field]=amount&sort[sort]=desc', 200,
    sub_page(MEMBERS, ordered=column_order(MEMBERS, amount_key, True)))
add('subscription-sort-unknown', 'owner', SUB + '?sort[field]=nope', 500, SERVER)
add('subscription-sort-full-name-unknown-without-search', 'owner', SUB + '?sort[field]=full_name', 500, SERVER)
add('subscription-sort-account-path-server-error', 'owner', SUB + '?sort[field]=user__password', 500, SERVER)
add('subscription-sort-owner-account-path-server-error', 'owner', SUB + '?sort[field]=sport_association__user__email&sort[sort]=desc', 500, SERVER)
add('subscription-page-two', 'owner', SUB + '?pagination[perpage]=3&pagination[page]=2', 200, sub_page(MEMBERS, number=2, size=3))
add('subscription-page-beyond', 'owner', SUB + '?pagination[perpage]=3&pagination[page]=4', 404, INVALID_PAGE)
add('subscription-search-duplicates-paginated', 'owner', SS + 'corso&pagination[perpage]=1&pagination[page]=2', 200,
    sub_page(sub_search('corso'), ordered=sub_search('corso'), number=2, size=1))

# --- payment/list: athletes ----------------------------------------------------------------------------
# An athlete (no mode=compressed) takes its own branch:
# - with a ``course_subscription_id`` key: the live course subscription with that id (an empty value: 500)
#   whose subscription has the athlete as user or an associate of the athlete's subscriptions (the people of
#   the list below; an association-scoped session keeps scoped_queryset's members instead); any other id is
#   the 403 of a resource that is not available; when it has ``multi_payments`` the payments of its live installments (amount > 0, or the
#   association owner's ``show_zero_payments``; an installment without payment: 500), otherwise its own
#   payment under the same amount rule; then the payments of the carnet holdings linked to it; finally the
#   live payments with those ids, in database order;
# - without it: the live income payments (``amount >= 0``, created at most 60 days ahead) of the athlete's
#   user or of the associates of the athlete's subscriptions, by ``paid, -creation_date``, minus the
#   exclusions: for every live course subscription of those subscriptions, when the course has
#   ``multi_payments`` each unpaid live installment due more than 31 days ahead, or with a zero payment
#   unless the owner shows zero payments (an installment due earlier without payment: 500); otherwise a zero
#   course subscription payment unless the owner shows zero payments.
# Before the exclusions every listed paid payment whose invoice has no PDF goes through document_invoice
# in-process (the list's own query string: ``send_receipt_email``); its result is ignored and the row still
# renders the invoice as loaded before. Rows are PaymentCompressedSerializer (``meta`` from the installment,
# subscription, carnet holding, course subscription or camp period of the payment) plus ``payment_method``,
# ``online_pay_available`` (the owner's ``online_payments`` and configured Stripe credentials),
# ``sport_association_denomination``, ``one_solution_available`` (an unpaid installment of a one-fee course
# whose event 0 has the installment's date) and, for paid invoiced rows, ``document_pdf``. The response is
# ``{'data': {index: row}, 'meta': {}}``.
NORTH, SOUTH = uid('a550c1a7'), uid('a550c1a7')
ATH_OWNERS = {
    'north_owner': {'id': uid('0b0e0000'), 'username': 'payment-reads-north', 'email': 'payment-reads-north@example.test',
                    'show_zero_payments': False, 'online_payments': True, 'association': NORTH,
                    'denomination': 'Polisportiva Nord', 'tax_code': '77410000001'},
    'south_owner': {'id': uid('0b0e0000'), 'username': 'payment-reads-south', 'email': 'payment-reads-south@example.test',
                    'show_zero_payments': True, 'online_payments': False, 'association': SOUTH,
                    'denomination': 'ASD Sud', 'tax_code': '77410000002'},
}
ASSOCIATION_OWNER = {row['association']: row for row in ATH_OWNERS.values()}
EXTRA_USERS = {label: {'id': uid('a7b1e7e0'), 'username': f'payment-reads-{label}', 'email': f'payment-reads-{label}@example.test'}
               for label in ('runner', 'scoped', 'faulty', 'partner')}
U = {label: row['id'] for label, row in EXTRA_USERS.items()}
ATH_GROUP = {'id': uid('6a0bbbbb'), 'name': 'Agonisti', 'association': NORTH}
ATH_SESSION = {'label': 'north_scoped', 'actor': 'north_owner', 'target': 'scoped', 'association': NORTH}


def ath_person(association, first, last, **values):
    return {'id': uid('a550c1a7'), 'association': association, 'first_name': first, 'last_name': last, 'email': None, 'tax_code': None,
            'deleted': False, **values}


ATH_ASSOCIATES = {
    'r_self': ath_person(NORTH, 'Rita', 'Corsa', email='rita.corsa@example.test', tax_code='CRSRTI85A41H501A'),
    'r_kid': ath_person(NORTH, 'Leo', 'Corsa'),
    'r_south': ath_person(SOUTH, 'Rita', 'Corsa'),
    'r_old': ath_person(NORTH, 'Vecchio', 'Socio', deleted=True),
    'stranger': ath_person(NORTH, 'Altro', 'Socio'),
    # The scoped athlete's subscriptions: one associate with a tax code, one without.
    'sc_child': ath_person(NORTH, 'Sara', 'Pista', tax_code='PSTSRA10A41H501B'),
    'sc_other': ath_person(NORTH, 'Ospite', 'Pista'),
}


def ath_subscription(association, user, associate, **values):
    return {'id': uid('5ab5c0de'), 'association': association, 'user': user, 'associate': associate, 'payment': None, 'deleted': False,
            'start': None, 'end': None, **values}


ATH_SUBSCRIPTIONS = {
    'n_self': ath_subscription(NORTH, 'runner', 'r_self', payment='p_member', start='2024-01-01', end='2024-12-31'),
    'n_kid': ath_subscription(NORTH, 'runner', 'r_kid'),
    's_self': ath_subscription(SOUTH, 'runner', 'r_south'),
    'n_old': ath_subscription(NORTH, 'runner', 'r_old', deleted=True),
    'sc_one': ath_subscription(NORTH, 'scoped', 'sc_child', start='2024-09-01'),
    'sc_two': ath_subscription(NORTH, 'scoped', 'sc_other'),
    'f_sub': ath_subscription(NORTH, 'faulty', None),
    # Another user's subscription of an associate the runner also subscribed.
    'n_kid_partner': ath_subscription(NORTH, 'partner', 'r_kid'),
}
ATH_COURSES = {
    'c_single': {'id': uid('c0a5ec0a'), 'association': NORTH, 'title': 'Atletica', 'multi': False, 'one_fee': False, 'events': []},
    'c_multi': {'id': uid('c0a5ec0a'), 'association': NORTH, 'title': 'Mezzofondo', 'multi': True, 'one_fee': True,
                'events': [{'id': 1, 'amount': '30.00', 'payment_date': '15/01/2099'},
                           {'id': '0', 'amount': '30.00', 'payment_date': '15/09/2024'}]},
    'c_south': {'id': uid('c0a5ec0a'), 'association': SOUTH, 'title': 'Yoga', 'multi': False, 'one_fee': False, 'events': []},
}


def ath_cs(course, subscription, payment=None, **values):
    return {'id': uid('c5c5c5c5'), 'course': course, 'subscription': subscription, 'payment': payment, 'deleted': False,
            'multi': False, **values}


ATH_COURSE_SUBSCRIPTIONS = {
    'cs_single': ath_cs('c_single', 'n_self', 'p_course'),
    'cs_zero': ath_cs('c_single', 'n_kid', 'p_zero'),
    'cs_multi': ath_cs('c_multi', 'n_self', multi=True),
    'cs_south': ath_cs('c_south', 's_self', 'p_szero'),
    'cs_deleted': ath_cs('c_single', 'n_kid', 'p_csdel', deleted=True),
    # Its only installment is paid and has no payment.
    'cs_broken': ath_cs('c_multi', 'n_kid', multi=True),
    'sc_cs': ath_cs('c_single', 'sc_one', 'sp_child'),
    'f_cs': ath_cs('c_multi', 'f_sub', multi=True),
    'cs_member': ath_cs('c_single', 'n_kid_partner', 'p_member_course'),
}


def ath_installment(cs, number, amount, date, payment, **values):
    return {'id': uid('11a5cccc'), 'cs': cs, 'number': number, 'amount': amount, 'date': date, 'payment': payment, 'paid': False,
            'deleted': False, **values}


ATH_INSTALLMENTS = {
    'i0': ath_installment('cs_multi', 0, '30.00', '2024-09-15T00:00:00Z', 'p_i0'),
    'i1': ath_installment('cs_multi', 1, '30.00', '2099-01-15T00:00:00Z', 'p_i1'),
    'i2': ath_installment('cs_multi', 2, '0.00', '2024-11-15T00:00:00Z', 'p_i2'),
    'i3': ath_installment('cs_multi', 3, '30.00', '2024-10-15T08:30:00.123456Z', 'p_i3', paid=True),
    'i_del': ath_installment('cs_multi', 4, '30.00', '2099-02-15T00:00:00Z', 'p_idel', deleted=True),
    'i_nopay': ath_installment('cs_broken', 0, '10.00', '2024-09-15T00:00:00Z', None, paid=True),
    'f_i': ath_installment('f_cs', 0, '10.00', '2024-09-15T00:00:00Z', None),
}
ATH_CARNET = {'id': uid('ca0e7bbb'), 'title': 'Carnet dieci lezioni', 'association': NORTH}
ATH_HOLDINGS = {'h1': {'id': uid('ab0ca7ef'), 'subscription': 'n_self', 'payment': 'p_carnet', 'course_subscriptions': ['cs_single']}}
ATH_CAMP = {'id': uid('ca0b0000'), 'title': 'Camp estivo', 'association': NORTH,
            'period': {'id': uid('ca0b1111'), 'title': 'Settimana uno', 'start': '2024-07-01T00:00:00Z', 'end': '2024-07-07T00:00:00Z'},
            'subscription': {'id': uid('ca0b2222'), 'subscription': 'n_kid'},
            'link': {'id': uid('ca0b3333'), 'payment': 'p_camp'}}
ATH_DOCUMENTS = {'south_pdf': {'id': uid('d0c0eeee'), 'token': uid('70ce0eee'), 'filename': 'ricevuta-sud.pdf'}}
ATH_INVOICES = {label: {'id': uid('1a0c0bbb'), 'association': association, 'document': document, 'deleted': deleted}
                for label, association, document, deleted in (
                    ('inv_member', NORTH, None, False), ('inv_i3', NORTH, None, False), ('inv_south', SOUTH, 'south_pdf', False),
                    ('inv_unpaid', NORTH, None, False), ('inv_nodate', NORTH, None, False), ('inv_shared', NORTH, None, False),
                    ('inv_deleted', NORTH, None, True), ('sc_inv', NORTH, None, False))}


def ath_payment(created, association=NORTH, **values):
    return {'id': uid('ba1dbeef'), 'created': created, 'association': association, 'subject': 0, 'type': 'default', 'amount': '0.00',
            'paid': False, 'expense': False, 'description': None, 'notes': None, 'user': None, 'associate': None, 'invoice': None,
            'paid_on': None, 'deleted': False, 'archived': False, 'group': None, 'course': None, **values}


ATH_PAYMENTS = {
    'p_member': ath_payment('2024-02-01T09:00:00Z', user='runner', associate='r_self', subject=1, type='cash', amount='50.00', paid=True,
                            paid_on='2024-02-10T10:00:00.250000Z', invoice='inv_member', description='Quota associativa'),
    'p_course': ath_payment('2024-03-01T09:00:00Z', associate='r_self', subject=2, amount='80.00', description='Corso atletica',
                            notes='saldo entro marzo', course={'title': 'Atletica JSON'}),
    'p_zero': ath_payment('2024-03-02T09:00:00Z', associate='r_kid', subject=2),
    'p_i0': ath_payment('2024-03-03T09:00:00Z', associate='r_self', subject=2, amount='30.00'),
    'p_i1': ath_payment('2024-03-04T09:00:00Z', associate='r_self', subject=2, amount='30.00'),
    'p_i2': ath_payment('2024-03-05T09:00:00Z', associate='r_self', subject=2),
    'p_i3': ath_payment('2024-03-06T09:00:00Z', associate='r_self', subject=2, type='online', amount='30.00', paid=True,
                        paid_on='2024-10-15T08:30:00Z', invoice='inv_i3'),
    'p_idel': ath_payment('2024-03-07T09:00:00Z', associate='r_self', subject=2, amount='30.00'),
    'p_carnet': ath_payment('2024-03-08T09:00:00Z', associate='r_self', subject=2, amount='25.00'),
    'p_camp': ath_payment('2024-03-09T09:00:00Z', associate='r_kid', amount='45.00'),
    'p_szero': ath_payment('2024-03-10T09:00:00Z', SOUTH, associate='r_south', subject=2),
    'p_csdel': ath_payment('2024-03-11T09:00:00Z', associate='r_kid', subject=2),
    'p_south_paid': ath_payment('2024-03-12T09:00:00Z', SOUTH, user='runner', amount='12.50', paid=True, paid_on='2024-03-12T10:00:00Z',
                                invoice='inv_south'),
    'p_expense': ath_payment('2024-03-13T09:00:00Z', user='runner', amount='5.00', expense=True),
    'p_future': ath_payment('2099-01-01T09:00:00Z', user='runner', amount='7.00'),
    'p_deleted': ath_payment('2024-03-15T09:00:00Z', user='runner', amount='7.50', deleted=True),
    'p_old': ath_payment('2024-03-16T09:00:00Z', associate='r_old', amount='3.00'),
    'p_stranger': ath_payment('2024-03-17T09:00:00Z', associate='stranger', amount='4.00'),
    'p_unpaid_invoiced': ath_payment('2024-03-18T09:00:00Z', user='runner', amount='6.00', invoice='inv_unpaid'),
    'p_nodate': ath_payment('2024-03-19T09:00:00Z', user='runner', amount='8.00', paid=True, invoice='inv_nodate'),
    'p_shared_a': ath_payment('2024-03-20T09:00:00Z', user='runner', amount='9.00', paid=True, paid_on='2024-03-20T10:00:00Z',
                              invoice='inv_shared'),
    'p_shared_b': ath_payment('2024-03-21T09:00:00Z', user='runner', amount='9.50', paid=True, paid_on='2024-03-21T10:00:00Z',
                              invoice='inv_shared'),
    'p_delinv': ath_payment('2024-03-22T09:00:00Z', user='runner', amount='10.00', paid=True, paid_on='2024-03-22T10:00:00Z',
                            invoice='inv_deleted'),
    'p_grouped': ath_payment('2024-03-23T09:00:00Z', user='runner', amount='11.00', group=ATH_GROUP['id']),
    'p_archived': ath_payment('2024-03-24T09:00:00Z', user='runner', amount='2.00', archived=True),
    'p_member_course': ath_payment('2024-03-25T09:00:00Z', associate='r_kid', subject=2, amount='35.00'),
    'sp_user': ath_payment('2024-04-01T09:00:00Z', user='scoped', amount='20.00', paid=True, paid_on='2024-04-02T09:00:00Z',
                           invoice='sc_inv'),
    'sp_child': ath_payment('2024-04-02T09:00:00Z', associate='sc_child', subject=2, amount='15.00'),
    'sp_other': ath_payment('2024-04-03T09:00:00Z', associate='sc_other', amount='9.00'),
}
AP = {label: row['id'] for label, row in ATH_PAYMENTS.items()}
TODAY = datetime.now(timezone.utc)
# The authored dates are far from the 31 and 60 day limits: the expectations do not depend on the run date.
assert all(abs((moment(row['date']) - TODAY).days) > 400 for row in ATH_INSTALLMENTS.values())
assert all(abs((moment(row['created']) - TODAY).days) > 400 for row in ATH_PAYMENTS.values())


class AthleteServerError(Exception):
    pass


def ath_live(label, group):
    row = ATH_PAYMENTS[label]
    return not row['deleted'] and (group is None or row['group'] == group)


def ath_candidates(user, group=None, member=None):
    """The athlete's payment queryset before the exclusions (member: scoped_queryset's athlete boundary)."""
    # Subscriptions carry no group here: a group header leaves none.
    subscriptions = [s for s in ATH_SUBSCRIPTIONS.values() if s['user'] == user and group is None]
    associates = {s['associate'] for s in subscriptions if s['associate']}
    labels = [k for k, row in ATH_PAYMENTS.items()
              if ath_live(k, group) and (row['user'] == user or row['associate'] in associates) and Decimal(row['amount']) >= 0
              and not row['expense'] and moment(row['created']) <= TODAY and (member is None or member(row))]
    return sorted(sorted(labels, key=lambda k: moment(ATH_PAYMENTS[k]['created']), reverse=True), key=lambda k: ATH_PAYMENTS[k]['paid'])


def ath_excluded(user, group=None):
    excluded = set()
    for name, subscription in ATH_SUBSCRIPTIONS.items():
        if subscription['user'] != user or group is not None:
            continue
        show_zero = ASSOCIATION_OWNER[subscription['association']]['show_zero_payments']
        for cs_name, cs in ATH_COURSE_SUBSCRIPTIONS.items():
            if cs['subscription'] != name or cs['deleted']:
                continue
            if ATH_COURSES[cs['course']]['multi']:
                for installment in ATH_INSTALLMENTS.values():
                    if installment['cs'] != cs_name or installment['deleted'] or installment['paid']:
                        continue
                    if (moment(installment['date']).date() - TODAY.date()).days > 31:
                        excluded.add(installment['payment'])
                    elif installment['payment'] is None:
                        raise AthleteServerError
                    elif Decimal(ATH_PAYMENTS[installment['payment']]['amount']) == 0 and not show_zero:
                        excluded.add(installment['payment'])
            elif cs['payment'] and Decimal(ATH_PAYMENTS[cs['payment']]['amount']) == 0 and not show_zero:
                excluded.add(cs['payment'])
    return excluded


def ath_course_subscription_available(user, name, group=None):
    """Outside a scoped session: a live course subscription of the athlete's user or of an associate of their subscriptions."""
    cs = ATH_COURSE_SUBSCRIPTIONS[name]
    subscription = ATH_SUBSCRIPTIONS[cs['subscription']]
    # Subscriptions carry no group here: a group header leaves no associates.
    associates = {s['associate'] for s in ATH_SUBSCRIPTIONS.values() if s['user'] == user and group is None and s['associate']}
    return not cs['deleted'] and (subscription['user'] == user or subscription['associate'] in associates)


def ath_by_course_subscription(name, group=None):
    """The course subscription branch: the payment labels (database order is not defined)."""
    cs = ATH_COURSE_SUBSCRIPTIONS[name]
    if cs['deleted']:
        raise AthleteServerError
    show_zero = ASSOCIATION_OWNER[ATH_COURSES[cs['course']]['association']]['show_zero_payments']
    labels = []
    if cs['multi']:
        for installment in ATH_INSTALLMENTS.values():
            if installment['cs'] == name and not installment['deleted'] and (Decimal(installment['amount']) > 0 or show_zero):
                if installment['payment'] is None:
                    raise AthleteServerError
                labels.append(installment['payment'])
    elif cs['payment'] and (Decimal(ATH_PAYMENTS[cs['payment']]['amount']) > 0 or show_zero):
        labels.append(cs['payment'])
    for holding in ATH_HOLDINGS.values():
        if name in holding['course_subscriptions'] and holding['payment'] and (
                Decimal(ATH_PAYMENTS[holding['payment']]['amount']) > 0 or show_zero):
            labels.append(holding['payment'])
    return [k for k in labels if ath_live(k, group)]


def ath_meta(label):
    row = ATH_PAYMENTS[label]
    meta = {'type': '', 'is_installment': False, 'installment': {'id': None, 'payment_date': None}, 'conjunction': '',
            'description': row['description'], 'notes': row['notes'], 'name': None,
            'subscription': {'subscription_id': None, 'start_date': None, 'end_date': None}}

    def subscription(name):
        found = ATH_SUBSCRIPTIONS[name]
        return {'subscription_id': found['id'], 'start_date': found['start'], 'end_date': found['end']}

    installments = [i for i in ATH_INSTALLMENTS.values() if i['payment'] == label and not i['deleted']]
    if installments:
        installment = installments[0]
        cs = ATH_COURSE_SUBSCRIPTIONS[installment['cs']]
        return {**meta, 'type': 'rata', 'conjunction': ' del corso ', 'is_installment': True,
                'installment': {'id': installment['number'], 'payment_date': drf_time(installment['date'])},
                'name': ATH_COURSES[cs['course']]['title'], 'subscription': subscription(cs['subscription'])}
    for name, found in ATH_SUBSCRIPTIONS.items():
        if found['payment'] == label:
            return {**meta, 'type': 'Iscrizione', 'conjunction': " all'associazione ",
                    'name': ASSOCIATION_OWNER[found['association']]['denomination'], 'subscription': subscription(name)}
    for holding in ATH_HOLDINGS.values():
        if holding['payment'] == label:
            return {**meta, 'type': 'Assegnazione carnet', 'conjunction': ' con nome ', 'name': ATH_CARNET['title'],
                    'subscription': subscription(holding['subscription'])}
    for cs in ATH_COURSE_SUBSCRIPTIONS.values():
        if cs['payment'] == label and not cs['deleted']:
            return {**meta, 'type': 'Iscrizione ', 'conjunction': ' al corso ', 'name': ATH_COURSES[cs['course']]['title'],
                    'subscription': subscription(cs['subscription'])}
    if ATH_CAMP['link']['payment'] == label:
        return {**meta, 'type': 'Iscrizione ', 'conjunction': ' al campo ', 'name': ATH_CAMP['period']['title'],
                'subscription': subscription(ATH_CAMP['subscription']['subscription'])}
    return meta


# The invoice PDFs as the sequence leaves them: a seeded Document id, None, or '$document:<invoice>' once generated.
PDFS = {label: ATH_DOCUMENTS[row['document']]['id'] if row['document'] else None for label, row in ATH_INVOICES.items()}


def ath_row(label, stripe):
    row = ATH_PAYMENTS[label]
    owner = ASSOCIATION_OWNER[row['association']]
    online = stripe and owner['online_payments']
    one_solution = False
    installments = [i for i in ATH_INSTALLMENTS.values() if i['payment'] == label and not i['deleted']]
    if online and installments:
        installment = installments[-1]
        course = ATH_COURSES[ATH_COURSE_SUBSCRIPTIONS[installment['cs']]['course']]
        if course['one_fee'] and not row['paid']:
            due = moment(installment['date']).strftime('%d/%m/%Y')
            one_solution = any(int(event['id']) == 0 and event['payment_date'] == due for event in course['events'])
    associate = ATH_ASSOCIATES[row['associate']] if row['associate'] else None
    item = {'payment_id': row['id'], 'type': row['type'], 'subject': row['subject'], 'amount': row['amount'],
            'associate': associate and {'associate_id': associate['id'], 'first_name': associate['first_name'],
                                        'last_name': associate['last_name']},
            'description': row['description'], 'paid': row['paid'], 'creation_date': drf_time(row['created']),
            'payment_date': drf_time(row['paid_on']), 'payment_category': None, 'sport_association': row['association'],
            'notes': row['notes'], 'course': row['course'], 'meta': ath_meta(label), 'payment_method': row['type'],
            'online_pay_available': online, 'sport_association_denomination': owner['denomination'],
            'one_solution_available': one_solution}
    if row['paid'] and row['invoice']:
        item['document_pdf'] = PDFS[row['invoice']]
    return item


def ath_body(labels, stripe=False):
    return {'data': {str(index): ath_row(label, stripe) for index, label in enumerate(labels)}, 'meta': {}}


def ath_receipts(labels, *, mail=False, group=None):
    """The listed payments that reach the renderer, in order: paid, with a live invoice without PDF that exactly
    one live payment references, and a payment date."""
    found = []
    for label in labels:
        row = ATH_PAYMENTS[label]
        if not (row['paid'] and row['invoice'] and PDFS[row['invoice']] is None):
            continue
        sharing = [k for k, other in ATH_PAYMENTS.items() if other['invoice'] == row['invoice'] and ath_live(k, group)]
        if ATH_INVOICES[row['invoice']]['deleted'] or len(sharing) != 1 or row['paid_on'] is None:
            continue
        day = moment(row['paid_on']).astimezone(timezone.utc)
        owner = ASSOCIATION_OWNER[row['association']]
        associate = ATH_ASSOCIATES[row['associate']] if row['associate'] else None
        message = None
        if mail and associate and associate['email']:
            short = day.strftime('%d/%m/%y')
            message = {'subject': f'Ricevuta di pagamento {short}', 'recipient': associate['email'], 'message': (
                f"Gentile {associate['first_name']} {associate['last_name']},\n"
                f"abbiamo il piacere di inviarle la ricevuta di pagamento di {row['amount'].replace('.', ',')} € del {short}.\n\n"
                "Ecco il link per scaricare la ricevuta:\n $app_url/api/document/retrieve/$document?download=false&token=$token\n\n"
                f"Cordiali saluti,\n{owner['denomination']}")}
        found.append({'invoice': row['invoice'], 'document': f"[{owner['denomination']}] Ricevuta {day.strftime('%Y-%m-%d')}.pdf",
                      'mail': message})
    return found


def ath_add(name, actor, path, labels, *, stripe=False, headers=None, unordered=False, receipts=(), failing=False, audit=None,
            stripe_enabled=True):
    """One athlete list: the rows as the sequence stands, then the receipts the request generates."""
    body = ath_body(labels, stripe and stripe_enabled)
    reply = ({'status': 500, 'content_type': 'text/plain; charset=utf-8', 'body': 'renderer exploded'} if failing
             else {'status': 200, 'content_type': 'application/pdf', 'body': '$pdf'})
    renders = [{'url': f"document/invoice/{ATH_INVOICES[item['invoice']]['id']}/view", 'authorization': '$bearer', 'reply': reply}
               for item in receipts]
    generated = [{**item, 'linked': not failing, 'mail': None if failing else item['mail']} for item in receipts]
    add(name, actor, path, 200, body, headers=headers, unordered=unordered, renders=renders, generated=generated,
        stripe=(stripe_setup(0, 0, enabled=stripe_enabled), []) if stripe else None)
    cases[-1]['audit'] = audit or {'actor': actor, 'association': None, 'session_association': None}
    if not failing:
        for item in receipts:
            PDFS[item['invoice']] = '$document:' + item['invoice']


ACS = {label: LIST + '?course_subscription_id=' + row['id'] for label, row in ATH_COURSE_SUBSCRIPTIONS.items()}
ADMIN_ATHLETE = {'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']}
NORTH_SCOPED = {'X-Impersonation-Id': '$session:north_scoped', 'User-Id': U['scoped']}
EMPTY_LIST = {'data': {}, 'meta': {}}
add('athlete-list-empty', 'athlete', LIST, 200, EMPTY_LIST)
add('athlete-list-admin-impersonating-athlete', 'admin', LIST, 200, EMPTY_LIST, headers=ADMIN_ATHLETE)
add('athlete-list-installment-without-payment', 'faulty', LIST, 500, SERVER)
add('athlete-course-subscription-empty-value', 'runner', LIST + '?course_subscription_id=', 500, SERVER)
NOT_AVAILABLE_RESOURCE = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
assert not ath_course_subscription_available('runner', 'cs_deleted')
add('athlete-course-subscription-deleted', 'runner', ACS['cs_deleted'], 403, NOT_AVAILABLE_RESOURCE)
add('athlete-course-subscription-installment-without-payment', 'runner', ACS['cs_broken'], 500, SERVER)
ath_add('athlete-course-subscription-zero-hidden', 'runner', ACS['cs_zero'], ath_by_course_subscription('cs_zero'))
ath_add('athlete-course-subscription-zero-shown', 'runner', ACS['cs_south'], ath_by_course_subscription('cs_south'))
assert ath_by_course_subscription('cs_zero') == [] and ath_by_course_subscription('cs_south') == ['p_szero']
GROUP_LIST = ath_candidates('runner', ATH_GROUP['id'])
assert GROUP_LIST == ['p_grouped']
ath_add('athlete-list-group', 'runner', LIST, GROUP_LIST, headers={'X-Group-Id': ATH_GROUP['id']})

RUNNER_QUERY = ath_candidates('runner')
RUNNER = [k for k in RUNNER_QUERY if k not in ath_excluded('runner')]
assert set(RUNNER_QUERY) - set(RUNNER) == {'p_zero', 'p_i1', 'p_i2'}
assert not {'p_expense', 'p_future', 'p_deleted', 'p_stranger', 'sp_user'} & set(RUNNER_QUERY)
assert {'p_old', 'p_archived', 'p_csdel', 'p_idel', 'p_szero', 'p_camp'} <= set(RUNNER)
RECEIPTS = ath_receipts(RUNNER_QUERY, mail=True)
assert [item['invoice'] for item in RECEIPTS] == ['inv_i3', 'inv_member'] and all(item['mail'] for item in RECEIPTS)
# The renderer fails: each Document stays, the invoices are not linked and the list still answers.
ath_add('athlete-list-renderer-failure', 'runner', LIST, RUNNER, receipts=ath_receipts(RUNNER_QUERY), failing=True)
# The list's own send_receipt_email reaches document_invoice; the rows still show the invoices as loaded.
ath_add('athlete-list-receipts-generated', 'runner', LIST + '?send_receipt_email=true', RUNNER, stripe=True, receipts=RECEIPTS)
assert ath_row('p_i0', True)['one_solution_available'] and not ath_row('p_idel', True)['one_solution_available']
ath_add('athlete-list', 'runner', LIST, RUNNER)
assert ath_row('p_i3', False)['document_pdf'] == '$document:inv_i3' and ath_row('p_nodate', False)['document_pdf'] is None
ath_add('athlete-list-stripe-disabled', 'runner', LIST, RUNNER, stripe=True, stripe_enabled=False)
ath_add('athlete-list-ignores-owner-filters', 'runner', LIST + '?query[paid]=true&sort[field]=nope&pagination[page]=9&query[archived]=x',
        RUNNER)
SINGLE, INSTALLED = ath_by_course_subscription('cs_single'), ath_by_course_subscription('cs_multi')
assert sorted(SINGLE) == ['p_carnet', 'p_course'] and sorted(INSTALLED) == ['p_i0', 'p_i1', 'p_i3']
ath_add('athlete-course-subscription-single', 'runner', ACS['cs_single'], SINGLE, unordered=True)
ath_add('athlete-course-subscription-installments', 'runner', ACS['cs_multi'], INSTALLED, stripe=True, unordered=True)
# The course subscription of another user's subscription of an associate the athlete subscribed too.
assert ath_course_subscription_available('runner', 'cs_member') and ath_by_course_subscription('cs_member') == ['p_member_course']
ath_add('athlete-course-subscription-of-associate', 'runner', ACS['cs_member'], ['p_member_course'])
# A group header: the athlete's subscriptions (none in the group) give no associates; the payments are the group's.
assert not ath_course_subscription_available('runner', 'cs_member', ATH_GROUP['id'])
add('athlete-course-subscription-of-associate-group', 'runner', ACS['cs_member'], 403, NOT_AVAILABLE_RESOURCE,
    headers={'X-Group-Id': ATH_GROUP['id']})
assert ath_course_subscription_available('runner', 'cs_single', ATH_GROUP['id'])
assert ath_by_course_subscription('cs_single', ATH_GROUP['id']) == []
ath_add('athlete-course-subscription-group', 'runner', ACS['cs_single'], [], headers={'X-Group-Id': ATH_GROUP['id']})
# Only the athlete's own course subscriptions and those of their associates.
assert not ath_course_subscription_available('runner', 'sc_cs') and not ath_course_subscription_available('scoped', 'cs_single')
add('athlete-course-subscription-of-another-athlete', 'runner', ACS['sc_cs'], 403, NOT_AVAILABLE_RESOURCE)
add('athlete-course-subscription-of-another-athlete-installments', 'scoped', ACS['cs_multi'], 403, NOT_AVAILABLE_RESOURCE)
add('athlete-course-subscription-admin-impersonating-athlete', 'admin', ACS['cs_single'], 403, NOT_AVAILABLE_RESOURCE,
    headers=ADMIN_ATHLETE)
assert ath_course_subscription_available('scoped', 'sc_cs')
ath_add('athlete-course-subscription-own', 'scoped', ACS['sc_cs'], ath_by_course_subscription('sc_cs'))

# An association-scoped session (the owner viewing as the athlete): scoped_queryset keeps the association's rows
# of the athlete's user and of the members it resolves (the associate with the athlete's user, or with the tax
# code of an associate of the athlete's subscriptions).
SCOPED_MEMBERS = {'sc_child'}
SCOPED_QUERY = ath_candidates('scoped', member=lambda row: row['association'] == NORTH and (
    row['user'] == 'scoped' or row['associate'] in SCOPED_MEMBERS))
assert SCOPED_QUERY == ['sp_child', 'sp_user'] and ath_excluded('scoped') == set()
SCOPED_AUDIT = {'actor': 'north_owner', 'association': NORTH, 'session_association': NORTH}
ath_add('athlete-scoped-list', 'north_owner', LIST, SCOPED_QUERY, headers=NORTH_SCOPED, receipts=ath_receipts(SCOPED_QUERY),
        audit=SCOPED_AUDIT)
assert cases[-1]['generated'] and cases[-1]['generated'][0]['invoice'] == 'sc_inv'
ath_add('athlete-scoped-course-subscription', 'north_owner', ACS['sc_cs'], ath_by_course_subscription('sc_cs'), headers=NORTH_SCOPED,
        audit=SCOPED_AUDIT)
add('athlete-scoped-course-subscription-not-available', 'north_owner', ACS['cs_single'], 403, NOT_AVAILABLE_RESOURCE, headers=NORTH_SCOPED)
add('athlete-scoped-compressed', 'north_owner', LIST + '?mode=compressed', 500, SERVER, headers=NORTH_SCOPED)
UNSCOPED = ath_candidates('scoped')
assert UNSCOPED == ['sp_other', 'sp_child', 'sp_user']
ath_add('athlete-list-of-scoped-user', 'scoped', LIST, UNSCOPED)
assert cases[-1]['response']['data']['2']['document_pdf'] == '$document:sc_inv'

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'Payment list, payment stats and subscription payments authored sequence', 'ids': IDS, 'stripe': STRIPE,
    'category': CATEGORY, 'accounts': ACCOUNTS, 'documents': DOCUMENTS, 'associates': ASSOCIATES,
    'tutors': {A[k]: [[A[t], primary] for t, primary in v] for k, v in TUTORS.items()}, 'instructor': INSTRUCTOR, 'supplier': SUPPLIER,
    'courses': COURSES, 'carnet': CARNET, 'invoice': INVOICE, 'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS,
    'course_subscriptions': COURSE_SUBSCRIPTIONS, 'installments': INSTALLMENTS, 'holdings': HOLDINGS, 'hours': HOURS,
    'extra_users': EXTRA_USERS,
    'athlete': {'owners': ATH_OWNERS, 'group': ATH_GROUP, 'session': ATH_SESSION, 'associates': ATH_ASSOCIATES,
                'subscriptions': ATH_SUBSCRIPTIONS, 'courses': ATH_COURSES, 'course_subscriptions': ATH_COURSE_SUBSCRIPTIONS,
                'installments': ATH_INSTALLMENTS, 'carnet': ATH_CARNET, 'holdings': ATH_HOLDINGS, 'camp': ATH_CAMP,
                'documents': ATH_DOCUMENTS, 'invoices': ATH_INVOICES, 'payments': ATH_PAYMENTS},
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
