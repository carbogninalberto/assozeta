"""Author the payment/list/export HTTP sequence (payment-export.md).

payment_list_export (BE/application/views/payment_views.py) is an
@api_view(['GET']) function view behind IsAuthenticated (registry:
``bookeeping.payments.read``). In Django's order:

- athletes (``request.user.role``) raise (500); then the scoped
  ``SportAssociation.objects.get(user=request.user)`` (500 without a live one);
- the window: ``datetime.now()`` minus ten years up to now, or
  ``query[payment_range]`` when present: ``split(' al ')`` must give two parts
  (otherwise the unpacking ValueError is a 500, an empty value included); the
  dates parse with '%Y/%m/%d', else '%d/%m/%Y', else (a mixed pair included)
  the window is the last year up to tomorrow; the end then moves one day on and
  to 00:00 (naive datetimes in the UTC zone); both ends inclusive, on the
  payment date, or the creation date of payments without one;
- the scoped, live, ``X-Group-Id``-aware payments of the association with
  ``archived=request.GET.get('archived', False)`` (BooleanField to_python of a
  raw string: 't', 'True', '1', 'f', 'False', '0'; anything else a 500),
  ordered by ``-payment_date`` (NULLs first);
- ``export_type=petty_cash_book`` (the petty cash book) forces ``paid=True``
  and ignores ``query[expense]`` and the general search; otherwise
  ``query[paid]`` when present is ``== 'true'`` and ``query[expense]`` the
  same; ``query[payment_categories]`` and ``query[account]`` when truthy
  ('[]': none, else UUIDs, a malformed one a 500);
- ``query[generalSearch]`` (truthy): a d/m/Y date becomes Y-m-d; a float()
  (',' as '.') filters the amount (icontains of str(float) or equality) inside
  a try, so a NaN falls back to smart_search like any other text;
- the petty cash book appends, for each of the association's account
  transfers in the window, an outgoing and an incoming pseudo payment
  ('Trasferimento da <from> a <to>', subject 3);
- the rows sort stably by payment date (payments without one take
  ``timezone.now()``);
- the petty cash book's progressive balance: every account's balance up to the
  window start (initial balance, transfers to minus transfers from, the live
  group-aware paid payments of the account with a payment date in range,
  all as Decimals, then a float), moved by each paid row with an account
  (``float(amount)``, negative for expenses); unpaid rows add '-'; paid rows
  without an account add nothing;
- users (associate 'first last' with Python's None, instructor, supplier
  name, else the description) and activities (subject 0 the category name or
  'Altro', 1 'Iscrizione', 2 'Corso', 3 'Giroconto', other subjects nothing);
- the CSV (Python's csv module, excel dialect, CRLF) zips the rows with the
  users, activities and (petty cash book) the progressive balances: the
  shortest list wins and later lists shift; the petty cash book rows have no
  'Causale' value (the header has it) and read the account name directly;
- the course column: the payment's course subscriptions (any state, first by
  key) course title, else its installments (any state, group-aware, first by
  key) course subscription's course title, else the course JSON's 'label';
- ``{'data': {'file': base64, 'type': 'csv', 'filename': '[<today>] PAGAMENTI a
  <denomination>.csv'}}``;
- ``m=xlsx`` hands get_excel_base64 the column lists (zip truncates them to the
  shortest): the user (None kept), 'Pagato'/'In Attesa', the type, the dates,
  the invoice number (an int, '' without an invoice), the activities, the
  additional categories (``'payment_category_id' in mpc`` first), the category
  name or '-', the account name or '-' (the petty cash book included), the
  flow, the course (the label value as is), the amount (a Decimal), the notes
  (None kept) and for the petty cash book the progressive balances (floats or
  '-'); the response is ``{'data': {'file': base64, 'type': 'xlsx', 'filename':
  '[<today>] PAGAMENTI a <denomination>.xlsx'}}`` (the workbook is compared with
  TESTS/contracts/xlsx.py).

The expected CSVs below are computed by an independent model of those Django
semantics over the authored rows. Personal data is synthetic. Run with
``python3 -I``.
"""
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('payment_export_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
SERVER = '<stable-server-error-page>'
DENOMINATION = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}
# Letters only outside the fixed version/variant digits: numeric search words
# never match a seeded id.
_letters = iter(f'{a}{b}{c}' for a in 'abcdef' for b in 'abcdef' for c in 'abcdef')


def uid(kind):
    return f'{kind}-ffff-4fff-8fff-fffffffff{next(_letters)}'


# --- rows --------------------------------------------------------------------------------------------
CATEGORIES = {'own': {'id': uid('cafecafe'), 'name': 'Quote sociali', 'association': OWN},
              'global': {'id': uid('cafecafe'), 'name': 'Globale', 'association': None},
              'foreign': {'id': uid('cafecafe'), 'name': 'Estera', 'association': FOREIGN}}
ACCOUNTS = {'cash': {'id': uid('acc0cace'), 'name': 'Cassa sede', 'type': 1, 'code': 'C01', 'initial': '100.00', 'association': OWN},
            'bank': {'id': uid('acc0bace'), 'name': 'Banca, Intesa', 'type': 2, 'code': 'B01', 'initial': '1000.50',
                     'association': OWN},
            'abroad': {'id': uid('acc0aace'), 'name': 'Conto estero', 'type': 3, 'code': 'E01', 'initial': '0.00',
                       'association': FOREIGN}}
ASSOCIATE_DEFAULTS = {'first_name': None, 'last_name': None, 'tax_code': None, 'born_date': None, 'born_city': None, 'address_city': None,
                      'address': None, 'email': None, 'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None, 'notes': None,
                      'nationality': 'Italiana', 'id_number': None, 'association': OWN}


def person(created, **values):
    return {**ASSOCIATE_DEFAULTS, **values, 'id': uid('decafbad'), 'created': created}


ASSOCIATES = {
    'mario': person('2023-11-01T08:00:00Z', first_name='Mario', last_name='Rossi', tax_code='RSSMRA90E10H501Z', born_date='1990-05-10',
                    born_city='Verona', address_city='Verona', address='Via Mazzini 3', email='mario@example.test', phone='3331112222'),
    'lucia': person('2023-11-03T08:00:00Z', last_name='Verdi', email='lucia@example.test'),
}
INSTRUCTOR = {'id': uid('1eac0e00'), 'first_name': 'Giulia', 'last_name': 'Bianchi', 'email': 'giulia@example.test',
              'tax_code': 'BNCGLI85A41H501X', 'phone': '3405556666', 'address': 'Via Garibaldi 9'}
SUPPLIER = {'id': uid('5abb0ace'), 'name': 'Palestre "Riunite"', 'vat_number': '01234567890', 'email': 'fornitore@example.test',
            'address': 'Viale Europa 12', 'city': 'Verona', 'tax_code': None}
COURSES = {'swim': {'id': uid('c0a5ec0a'), 'title': 'Corso nuoto', 'deleted': False},
           'old': {'id': uid('c0a5ec0b'), 'title': 'Corso vecchio', 'deleted': True}}
INVOICE = {'id': uid('1a0c0ace'), 'created': '2024-02-10T10:05:00Z', 'number': 7}
SUBSCRIPTION = {'id': uid('5ab5c0de'), 'associate': 'mario', 'created': '2023-12-01T08:00:00Z'}

PAYMENT_DEFAULTS = {'subject': 0, 'type': 'default', 'amount': '0.00', 'paid': False, 'expense': False, 'description': None,
                    'category': None, 'meta_payment_categories': None, 'invoice': False, 'associate': None, 'supplier': False,
                    'instructor': False, 'association': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None,
                    'meta': None, 'notes': None, 'account': None, 'deleted': False, 'group': None, 'course': None, 'paid_on': None}


def payment(created, **values):
    return {**PAYMENT_DEFAULTS, **values, 'id': uid('ba1dface'), 'created': created}


C = CATEGORIES
PAYMENTS = {
    'sub': payment('2024-02-01T09:00:00Z', subject=1, type='cash', amount='50.00', paid=True, associate='mario', account='cash',
                   category='own', invoice=True, description='Iscrizione annuale', notes='riga uno\nriga due',
                   paid_on='2024-02-10T10:00:00.250000Z'),
    'boundary': payment('2024-02-28T09:00:00Z', subject=0, type='pos', amount='1.00', paid=True, account='bank',
                        description='Arrotondamento', paid_on='2024-03-01T00:00:00Z'),
    'course': payment('2024-03-02T09:00:00Z', subject=2, type='transfer', amount='120.00', associate='mario', category='own',
                      description='Corso nuoto', paid_on='2024-03-05T09:00:00Z'),
    'installment': payment('2024-04-01T09:00:00Z', subject=2, type='pos', amount='40.00', paid=True, associate='mario',
                           account='bank', description='Rata corso', paid_on='2024-04-15T09:30:00Z'),
    'carnet': payment('2024-05-02T09:00:00Z', subject=2, type='online', amount='30.00', associate='mario',
                      course={'label': 'Carnet JSON', 'n': 1.5}, paid_on='2024-05-20T08:00:00Z'),
    'other': payment('2024-06-01T09:00:00Z', subject=0, amount='15.50', paid=True, associate='lucia', notes='nota, contanti',
                     account='cash', meta={'source': 'sportello'},
                     meta_payment_categories=[{'payment_category_id': C['own']['id']}, {'payment_category_id': C['global']['id']},
                                              {'payment_category_id': C['foreign']['id']}, {'payment_category_id': 5},
                                              {'payment_category_id': None}],
                     paid_on='2024-06-03T09:00:00Z'),
    'midnight': payment('2024-06-20T09:00:00Z', subject=0, type='cash', amount='3.00', paid=True, account='cash',
                        description='Chiusura', paid_on='2024-07-01T00:00:00Z'),
    'supplier': payment('2024-07-01T09:00:00Z', subject=0, type='transfer', amount='200.00', paid=True, expense=True, supplier=True,
                        account='bank', category='own', description='Affitto palestra città', course={'title': 'senza etichetta'},
                        paid_on='2024-07-02T09:00:00Z'),
    'instructor': payment('2024-08-15T09:00:00Z', subject=0, type='cash', amount='60.00', paid=True, expense=True, instructor=True,
                          account='cash', description='Compenso agosto', course={'label': None}, paid_on='2024-08-20T09:00:00Z'),
    'giroconto': payment('2024-09-01T09:00:00Z', subject=3, amount='25.00', paid=True, expense=True, account='cash',
                         description='Giroconto cassa', course={'label': 7}, paid_on='2024-09-01T12:00:00Z'),
    'grouped': payment('2024-10-01T09:00:00Z', subject=1, amount='12.00', associate='mario', group=OWN_GROUP,
                       paid_on='2024-10-05T09:00:00Z'),
    'grouped_paid': payment('2024-10-02T09:00:00Z', subject=0, amount='8.25', paid=True, account='cash', group=OWN_GROUP,
                            description='Quota gruppo', paid_on='2024-10-06T09:00:00Z'),
    'q_a': payment('2025-01-05T09:00:00Z', subject=0, type='cash', amount='0.10', paid=True, account='cash', description='Centesimi',
                   paid_on='2025-01-10T09:00:00Z'),
    'noaccount': payment('2025-01-06T09:00:00Z', subject=0, amount='4.00', paid=True, description='Senza conto',
                         paid_on='2025-01-20T09:00:00Z'),
    'pending': payment('2025-02-01T09:00:00Z', subject=0, amount='10.00', description='Quota finanziaria', course=''),
    # archived: an unknown subject (no activity) first, then a paid row without an account between two cash rows
    'odd': payment('2024-01-10T09:00:00Z', subject=5, amount='2.00', archived=True, description='Soggetto sconosciuto',
                   paid_on='2024-01-14T09:00:00Z'),
    'archived': payment('2024-01-11T09:00:00Z', subject=0, amount='5.00', paid=True, associate='mario', archived=True, account='cash',
                        paid_on='2024-01-15T09:00:00Z'),
    'arch_noacc': payment('2024-01-12T09:00:00Z', subject=1, amount='6.00', paid=True, associate='mario', archived=True,
                          paid_on='2024-01-16T09:00:00Z'),
    'arch_late': payment('2024-01-13T09:00:00Z', subject=3, amount='7.00', paid=True, archived=True, account='cash',
                         description='Archiviato', paid_on='2024-01-17T09:00:00Z'),
    'removed': payment('2024-01-20T09:00:00Z', subject=0, amount='70.00', paid=True, associate='mario', account='cash', deleted=True,
                       paid_on='2024-01-20T10:00:00Z'),
    'abroad': payment('2024-01-25T09:00:00Z', subject=0, amount='99.00', paid=True, association=FOREIGN, account='cash',
                      description='Estero', paid_on='2024-01-25T10:00:00Z'),
}
COURSE_SUBSCRIPTIONS = {
    'swim': {'id': uid('c5c5c5c5'), 'course': 'swim', 'payment': 'course', 'deleted': False},
    'rates': {'id': uid('c5c5c5c5'), 'course': 'swim', 'payment': None, 'deleted': False},
    'old': {'id': uid('c5c5c5c5'), 'course': 'old', 'payment': None, 'deleted': True},
}
# The deleted installment has the smaller key: .first() on all_objects() picks it.
INSTALLMENTS = {
    'old': {'id': uid('11a5aaaa'), 'cs': 'old', 'payment': 'installment', 'deleted': True, 'number': 1},
    'live': {'id': uid('11a5bbbb'), 'cs': 'rates', 'payment': 'installment', 'deleted': False, 'number': 2},
}
TRANSFERS = {
    'before': {'id': uid('7a0f7a0f'), 'from': 'cash', 'to': 'bank', 'amount': '5.00', 'date': '2024-02-20T00:00:00Z', 'association': OWN},
    'foreign_in': {'id': uid('7a0f7a0f'), 'from': 'abroad', 'to': 'cash', 'amount': '7.00', 'date': '2024-02-25T00:00:00Z',
                   'association': FOREIGN},
    't1': {'id': uid('7a0f7a0f'), 'from': 'cash', 'to': 'bank', 'amount': '30.00', 'date': '2024-05-10T10:00:00Z', 'association': OWN},
    't2': {'id': uid('7a0f7a0f'), 'from': 'bank', 'to': 'cash', 'amount': '12.34', 'date': '2024-06-15T08:00:00Z', 'association': OWN},
    'dangling': {'id': uid('7a0f7a0f'), 'from': 'abroad', 'to': None, 'amount': '1.00', 'date': '2024-03-10T00:00:00Z',
                 'association': FOREIGN},
}
P = {label: row['id'] for label, row in PAYMENTS.items()}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


# --- the general search model (as payment-reads) ---------------------------------------------------
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


PERSON_FIELDS = ('first_name', 'last_name', 'tax_code', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'born_city', 'born_date',
                 'address_city', 'address', 'notes', 'nationality', 'id_number')


def join_row(row):
    """The search's joined row (no tutor relations are seeded)."""
    base = {'p.payment_id': row['id'], 'p.type': row['type'], 'p.subject': str(row['subject']), 'p.amount': row['amount'],
            'p.creation_date': ts_text(row['created']), 'p.payment_date': ts_text(row['paid_on']), 'p.paid': text(row['paid']),
            'p.expense': text(row['expense']), 'p.description': row['description'],
            'p.meta_payment_categories': None if row['meta_payment_categories'] is None else jsonb_text(row['meta_payment_categories']),
            'p.payment_intent_id': row['payment_intent_id'], 'p.archived': text(row['archived']),
            'p.invoice_fiscal_code': row['invoice_fiscal_code'], 'p.meta': None if row['meta'] is None else jsonb_text(row['meta']),
            'p.notes': row['notes'], 'p.deleted': text(row['deleted']), 'p.course': None if row['course'] is None else jsonb_text(row['course'])}
    if row['supplier']:
        base.update({f'supplier.{k}': SUPPLIER[k] for k in ('name', 'email', 'address', 'city', 'tax_code', 'vat_number')})
    if row['instructor']:
        base.update({f'instructor.{k}': INSTRUCTOR[k] for k in ('first_name', 'last_name', 'email', 'phone', 'tax_code', 'address')})
    if row['associate']:
        base.update({f'associate.{k}': ASSOCIATES[row['associate']][k] for k in PERSON_FIELDS})
    return base


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


def searched(labels, raw):
    try:
        term = datetime.strptime(raw, '%d/%m/%Y').strftime('%Y-%m-%d')
    except ValueError:
        term = raw
    try:
        number = float(term.replace(',', '.'))
    except ValueError:
        number = None
    if number is not None and number == number:
        exact = Context(prec=9).create_decimal_from_float(number)
        return [k for k in labels if repr(number).upper() in PAYMENTS[k]['amount'].upper() or Decimal(PAYMENTS[k]['amount']) == exact]
    return [k for k in labels if smart_match(join_row(PAYMENTS[k]), term)]


# --- the export model ------------------------------------------------------------------------------
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def window(start, end):
    """The parsed range: start at 00:00, the end one day on at 00:00."""
    return moment(start + 'T00:00:00Z'), moment(end + 'T00:00:00Z') + timedelta(days=1)


def listed(*, association=OWN, archived=False, group=None, span=None, paid=None, expense=None, category=None, account=None,
           search=None):
    """The queryset in database order (-payment_date, NULLs first; the authored dates are unique)."""
    out = []
    for label, row in PAYMENTS.items():
        if row['association'] != association or row['deleted'] or row['archived'] != archived:
            continue
        if group is not None and row['group'] != group:
            continue
        if span is not None:
            when = moment(row['paid_on'] or row['created'])
            if not span[0] <= when <= span[1]:
                continue
        if paid is not None and row['paid'] != paid:
            continue
        if expense is not None and row['expense'] != expense:
            continue
        if category == '[]' and row['category'] is not None:
            continue
        if category not in (None, '[]') and (row['category'] is None or C[row['category']]['id'] not in category):
            continue
        if account == '[]' and row['account'] is not None:
            continue
        if account not in (None, '[]') and (row['account'] is None or ACCOUNTS[row['account']]['id'] != account):
            continue
        out.append(label)
    if search is not None:
        out = searched(out, search)
    nulls = [k for k in out if PAYMENTS[k]['paid_on'] is None]
    assert len(nulls) <= 1, 'the database order of NULL payment dates is not authored'
    dated = sorted((k for k in out if PAYMENTS[k]['paid_on']), key=lambda k: moment(PAYMENTS[k]['paid_on']), reverse=True)
    return nulls + dated


def balance(account, start, group):
    """CustomAccountSerializer.get_current_balance_from(1970-01-01, start) as float."""
    acc = ACCOUNTS[account]
    total = Decimal(acc['initial'])
    for row in TRANSFERS.values():
        if EPOCH <= moment(row['date']) <= start:
            if row['to'] == account:
                total += Decimal(row['amount'])
            if row['from'] == account:
                total -= Decimal(row['amount'])
    for row in PAYMENTS.values():
        if row['deleted'] or not row['paid'] or row['account'] != account or row['paid_on'] is None:
            continue
        if group is not None and row['group'] != group:
            continue
        if EPOCH <= moment(row['paid_on']) <= start:
            total += -Decimal(row['amount']) if row['expense'] else Decimal(row['amount'])
    return float(total)


def course_title(label, group):
    linked = sorted((c for c in COURSE_SUBSCRIPTIONS.values() if c['payment'] == label), key=lambda c: c['id'])
    if linked:
        return COURSES[linked[0]['course']]['title']
    rates = sorted((i for i in INSTALLMENTS.values() if i['payment'] == label and group is None), key=lambda i: i['id'])
    if rates:
        return COURSES[COURSE_SUBSCRIPTIONS[rates[0]['cs']]['course']]['title']
    course = PAYMENTS[label]['course']
    if course is not None and course != '' and 'label' in course:
        return course['label']
    return ''


def item(label, group):
    row = PAYMENTS[label]
    names = {c['id']: c['name'] for c in C.values() if c['association'] in (OWN, None)}
    meta = row['meta_payment_categories']
    if row['associate']:
        who = ASSOCIATES[row['associate']]
        user = f"{who['first_name']} {who['last_name']}"
    elif row['instructor']:
        user = f"{INSTRUCTOR['first_name']} {INSTRUCTOR['last_name']}"
    elif row['supplier']:
        user = SUPPLIER['name']
    else:
        user = row['description']
    activity = {0: C[row['category']]['name'] if row['category'] else 'Altro', 1: 'Iscrizione', 2: 'Corso', 3: 'Giroconto'}.get(
        row['subject'])
    return {'user': user, 'activity': activity, 'paid': row['paid'], 'expense': row['expense'], 'type': row['type'],
            'created': moment(row['created']), 'date': moment(row['paid_on']), 'invoice': INVOICE['number'] if row['invoice'] else '',
            'meta': ','.join(names[m['payment_category_id']] for m in meta if m['payment_category_id'] in names) if meta else '',
            'category': C[row['category']]['name'] if row['category'] else '-', 'account': row['account'],
            'course': course_title(label, group), 'amount': row['amount'], 'notes': row['notes']}


def transfer_items(association, span):
    out = []
    for row in TRANSFERS.values():
        if row['association'] != association or not span[0] <= moment(row['date']) <= span[1]:
            continue
        assert row['from'] and row['to'], 'a transfer without accounts is a 500'
        text_ = f"Trasferimento da {ACCOUNTS[row['from']]['name']} a {ACCOUNTS[row['to']]['name']}"
        for expense, account in ((True, row['from']), (False, row['to'])):
            out.append({'user': text_, 'activity': 'Giroconto', 'paid': True, 'expense': expense, 'type': 'default',
                        'created': moment(row['date']), 'date': moment(row['date']), 'invoice': '', 'meta': '', 'category': '-',
                        'account': account, 'course': '', 'amount': row['amount'], 'notes': None})
    return out


HEADER = ['Informazioni intestatario', 'Stato', 'Modalità di pagamento', 'Data creazione', 'Data pagamento', 'Numero ricevuta',
          'Attività', 'Causali aggiuntive', 'Causale', 'Conto', 'Tipo', 'Corso', 'Importo', 'Note']
NOW = datetime(2100, 1, 1, tzinfo=timezone.utc)  # every authored date is in the past


def export_csv(labels, *, petty=False, span=None, group=None, association=OWN):
    """The CSV of payment_list_export over the payments in database order."""
    items = [item(label, group) for label in labels]
    if petty:
        items += transfer_items(association, span)
    items.sort(key=lambda entry: entry['date'] or NOW)
    progressive = []
    if petty:
        balances = {name: balance(name, span[0], group) for name in ACCOUNTS}
        for entry in items:
            if entry['paid']:
                if entry['account']:
                    balances[entry['account']] = balances[entry['account']] + float(entry['amount']) * (-1 if entry['expense'] else 1)
                    progressive.append(balances[entry['account']])
            else:
                progressive.append('-')
    activities = [entry['activity'] for entry in items if entry['activity'] is not None]
    users = [entry['user'] for entry in items]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER + (['Saldo progressivo'] if petty else []))
    rows = []
    for entry, user, activity, *rest in zip(items, users, activities, *([progressive] if petty else [])):
        account = ACCOUNTS[entry['account']]['name'] if entry['account'] else None
        if petty:
            assert account is not None, 'a petty cash row without an account is a 500'
        values = [user, 'Pagato' if entry['paid'] else 'In Attesa', '-' if entry['type'] == 'default' else entry['type'],
                  entry['created'].strftime('%Y-%m-%d'), entry['date'].strftime('%Y-%m-%d') if entry['date'] else '',
                  entry['invoice'], activity, entry['meta']]
        if not petty:
            values.append(entry['category'])
        values += [account if petty else account or '-', 'Uscita' if entry['expense'] else 'Entrata', entry['course'], entry['amount'],
                   entry['notes']]
        rows.append(values + rest)
    writer.writerows(rows)
    return out.getvalue()


def typed(value):
    """A cell value as the xlsx spec's typed JSON."""
    if isinstance(value, bool):
        return {'bool': value}
    if isinstance(value, int):
        return {'int': str(value)}
    if isinstance(value, float):
        return {'float': repr(value)}
    if isinstance(value, Decimal):
        return {'decimal': str(value)}
    return value


def export_xlsx(labels, *, petty=False, span=None, group=None, association=OWN):
    """The m=xlsx workbook spec: get_excel_base64's column lists."""
    items = [item(label, group) for label in labels]
    if petty:
        items += transfer_items(association, span)
    items.sort(key=lambda entry: entry['date'] or NOW)
    progressive = []
    if petty:
        balances = {name: balance(name, span[0], group) for name in ACCOUNTS}
        for entry in items:
            if entry['paid']:
                if entry['account']:
                    balances[entry['account']] = balances[entry['account']] + float(entry['amount']) * (-1 if entry['expense'] else 1)
                    progressive.append(balances[entry['account']])
            else:
                progressive.append('-')
    columns = [
        [entry['user'] for entry in items],
        ['Pagato' if entry['paid'] else 'In Attesa' for entry in items],
        ['-' if entry['type'] == 'default' else entry['type'] for entry in items],
        [entry['created'].strftime('%Y-%m-%d') for entry in items],
        [entry['date'].strftime('%Y-%m-%d') if entry['date'] else '' for entry in items],
        [entry['invoice'] for entry in items],
        [entry['activity'] for entry in items if entry['activity'] is not None],
        [entry['meta'] for entry in items],
        [entry['category'] for entry in items],
        [ACCOUNTS[entry['account']]['name'] if entry['account'] else '-' for entry in items],
        ['Uscita' if entry['expense'] else 'Entrata' for entry in items],
        [entry['course'] for entry in items],
        [Decimal(entry['amount']) for entry in items],
        [entry['notes'] for entry in items],
    ] + ([progressive] if petty else [])
    header = HEADER + (['Saldo progressivo'] if petty else [])
    return {'$xlsx': {'engine': 'excel_utils', 'columns': header, 'data': [[typed(value) for value in column] for column in columns]}}


def xbody(spec, association=OWN):
    return {'data': {'file': spec, 'type': 'xlsx', 'filename': f'[$today] PAGAMENTI a {DENOMINATION[association]}.xlsx'}}


def body(csv_text, association=OWN):
    return {'data': {'file': {'$csv': csv_text}, 'type': 'csv',
                     'filename': f'[$today] PAGAMENTI a {DENOMINATION[association]}.csv'}}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'payment-export-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response})


EXPORT = '/payment/list/export'
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
PETTY = EXPORT + '?export_type=petty_cash_book'
DEFAULT = body(export_csv(listed()))

# --- identity, methods and OPTIONS ------------------------------------------------------------------
add('anonymous', 'anonymous', EXPORT, 401, {'detail': 'Authentication credentials were not provided.'})
add('admin-without-session', 'admin', EXPORT, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('restricted-collaborator', 'restricted_collaborator', EXPORT, 403, {'detail': 'Missing permission: bookeeping.payments.read'})
add('post', 'owner', EXPORT, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('delete', 'owner', EXPORT, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('options', 'owner', EXPORT, 200, {'name': 'Payment List Export', 'description': '', 'renders': ['application/json'],
                                      'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('format-unknown', 'owner', EXPORT + '?format=xml', 404, {'detail': 'Not found.'})
add('athlete', 'athlete', EXPORT, 500, SERVER)
add('scoped-athlete', 'owner', EXPORT, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED_ATHLETE)
add('owner-without-association', 'owner_without_association', EXPORT, 500, SERVER)
add('deleted-association-owner', 'deleted_association_owner', EXPORT, 500, SERVER)

# --- the default export ------------------------------------------------------------------------------
add('owner', 'owner', EXPORT, 200, DEFAULT)
add('full-collaborator', 'full_collaborator', EXPORT, 200, DEFAULT)
add('admin-impersonating-owner', 'admin', EXPORT, 200, DEFAULT, headers=ADMIN_OWNER)
add('foreign-owner', 'foreign_owner', EXPORT, 200, body(export_csv(listed(association=FOREIGN)), FOREIGN))
add('mode-csv', 'owner', EXPORT + '?m=csv', 200, DEFAULT)
add('mode-other-is-csv', 'owner', EXPORT + '?m=pdf', 200, DEFAULT)
add('group', 'owner', EXPORT, 200, body(export_csv(listed(group=OWN_GROUP), group=OWN_GROUP)), headers={'X-Group-Id': OWN_GROUP})
add('group-foreign', 'owner', EXPORT, 200, body(export_csv([])), headers={'X-Group-Id': FOREIGN_GROUP})
add('group-malformed-ignored', 'owner', EXPORT, 200, DEFAULT, headers={'X-Group-Id': 'nope'})
# An unknown subject adds no activity: the later activities shift up and the last row is dropped.
add('archived-true', 'owner', EXPORT + '?archived=True', 200, body(export_csv(listed(archived=True))))
add('archived-one', 'owner', EXPORT + '?archived=1', 200, body(export_csv(listed(archived=True))))
add('archived-false', 'owner', EXPORT + '?archived=f', 200, DEFAULT)
add('archived-lowercase-true', 'owner', EXPORT + '?archived=true', 500, SERVER)
add('archived-empty', 'owner', EXPORT + '?archived=', 500, SERVER)

# --- filters ---
Q = EXPORT + '?'
add('paid-true', 'owner', Q + 'query[paid]=true', 200, body(export_csv(listed(paid=True))))
add('paid-other-is-false', 'owner', Q + 'query[paid]=yes', 200, body(export_csv(listed(paid=False))))
add('paid-empty-is-false', 'owner', Q + 'query[paid]=', 200, body(export_csv(listed(paid=False))))
add('expense-true', 'owner', Q + 'query[expense]=true', 200, body(export_csv(listed(expense=True))))
add('expense-empty-is-false', 'owner', Q + 'query[expense]=', 200, body(export_csv(listed(expense=False))))
add('category', 'owner', Q + 'query[payment_categories]=' + C['own']['id'], 200, body(export_csv(listed(category=[C['own']['id']]))))
add('category-list', 'owner', Q + f"query[payment_categories]={C['global']['id']},{C['own']['id']}", 200,
    body(export_csv(listed(category=[C['own']['id'], C['global']['id']]))))
add('category-none', 'owner', Q + 'query[payment_categories]=[]', 200, body(export_csv(listed(category='[]'))))
add('category-empty-ignored', 'owner', Q + 'query[payment_categories]=', 200, DEFAULT)
add('category-malformed', 'owner', Q + 'query[payment_categories]=nope', 500, SERVER)
add('account', 'owner', Q + 'query[account]=' + ACCOUNTS['bank']['id'], 200, body(export_csv(listed(account=ACCOUNTS['bank']['id']))))
add('account-none', 'owner', Q + 'query[account]=[]', 200, body(export_csv(listed(account='[]'))))
add('account-malformed', 'owner', Q + 'query[account]=1', 500, SERVER)
add('filters-combined', 'owner', Q + 'query[paid]=true&query[expense]=false&query[account]=' + ACCOUNTS['cash']['id'], 200,
    body(export_csv(listed(paid=True, expense=False, account=ACCOUNTS['cash']['id']))))

# --- payment range ---
R = EXPORT + '?query[payment_range]='
SPRING = window('2024-03-01', '2024-06-30')
add('range-ymd', 'owner', R + '2024/03/01%20al%202024/06/30', 200, body(export_csv(listed(span=SPRING))))
add('range-dmy', 'owner', R + '01/03/2024%20al%2030/06/2024', 200, body(export_csv(listed(span=SPRING))))
add('range-single-digits', 'owner', R + '2024/3/1%20al%202024/6/30', 200, body(export_csv(listed(span=SPRING))))
add('range-creation-date-without-payment-date', 'owner', R + '2025/02/01%20al%202025/02/01', 200,
    body(export_csv(listed(span=window('2025-02-01', '2025-02-01')))))
# Unparseable or mixed dates: the last year up to tomorrow (no authored payment is that recent).
add('range-fallback-last-year', 'owner', R + 'ieri%20al%20oggi', 200, body(export_csv([])))
add('range-mixed-formats-fallback', 'owner', R + '2024/03/01%20al%2030/06/2024', 200, body(export_csv([])))
add('range-without-separator', 'owner', R + '2024/03/01', 500, SERVER)
add('range-three-parts', 'owner', R + '2024/03/01%20al%202024/04/01%20al%202024/05/01', 500, SERVER)
add('range-empty', 'owner', R, 500, SERVER)
add('range-and-filter', 'owner', R + '2024/01/01%20al%202024/12/31&query[expense]=true', 200,
    body(export_csv(listed(span=window('2024-01-01', '2024-12-31'), expense=True))))

# --- general search ---
S = EXPORT + '?query[generalSearch]='
for name, raw, query in (('last-name', 'rossi', 'rossi'), ('supplier', 'riunite', 'riunite'), ('words', 'mario rossi', 'mario%20rossi'),
                         ('date-dmy', '10/02/2024', '10/02/2024'), ('nan-is-text', 'nan', 'nan'), ('amount', '50', '50'),
                         ('amount-comma', '15,5', '15,5'), ('amount-cents', '0.1', '0.1'), ('nothing', 'zzzz', 'zzzz')):
    add(f'search-{name}', 'owner', S + query, 200, body(export_csv(listed(search=raw))))
add('search-empty-ignored', 'owner', S, 200, DEFAULT)
add('search-and-range', 'owner', S + 'rossi&query[payment_range]=2024/03/01%20al%202024/06/30', 200,
    body(export_csv(listed(span=SPRING, search='rossi'))))

# --- the petty cash book ---
add('petty-spring', 'owner', PETTY + '&query[payment_range]=2024/03/01%20al%202024/06/30', 200,
    body(export_csv(listed(span=SPRING, paid=True), petty=True, span=SPRING)))
add('petty-ignores-paid-expense-search', 'owner',
    PETTY + '&query[payment_range]=2024/03/01%20al%202024/06/30&query[paid]=false&query[expense]=true&query[generalSearch]=zzzz', 200,
    body(export_csv(listed(span=SPRING, paid=True), petty=True, span=SPRING)))
add('petty-account', 'owner', PETTY + '&query[payment_range]=2024/03/01%20al%202024/06/30&query[account]=' + ACCOUNTS['cash']['id'],
    200, body(export_csv(listed(span=SPRING, paid=True, account=ACCOUNTS['cash']['id']), petty=True, span=SPRING)))
YEAR = window('2024-01-01', '2024-12-31')
add('petty-year', 'owner', PETTY + '&query[payment_range]=01/01/2024%20al%2031/12/2024', 200,
    body(export_csv(listed(span=YEAR, paid=True), petty=True, span=YEAR)))
add('petty-group', 'owner', PETTY + '&query[payment_range]=01/01/2024%20al%2031/12/2024', 200,
    body(export_csv(listed(span=YEAR, paid=True, group=OWN_GROUP), petty=True, span=YEAR, group=OWN_GROUP)),
    headers={'X-Group-Id': OWN_GROUP})
# A paid row without an account adds no progressive balance: as the last row the zip drops it.
JANUARY = window('2025-01-01', '2025-01-31')
add('petty-last-row-without-account', 'owner', PETTY + '&query[payment_range]=2025/01/01%20al%202025/01/31', 200,
    body(export_csv(listed(span=JANUARY, paid=True), petty=True, span=JANUARY)))
# Between other rows it is rendered and its account name raises.
add('petty-archived-row-without-account', 'owner', PETTY + '&archived=True', 500, SERVER)
add('petty-empty-window', 'owner', PETTY + '&query[payment_range]=2023/01/01%20al%202023/01/31', 200,
    body(export_csv([], petty=True, span=window('2023-01-01', '2023-01-31'))))
# The foreign association's transfer without a destination account raises.
add('petty-transfer-without-account', 'foreign_owner', PETTY, 500, SERVER)
add('petty-foreign-before-transfer', 'foreign_owner', PETTY + '&query[payment_range]=2024/01/01%20al%202024/03/01', 200,
    body(export_csv(listed(association=FOREIGN, span=window('2024-01-01', '2024-03-01'), paid=True), petty=True,
                    span=window('2024-01-01', '2024-03-01'), association=FOREIGN), FOREIGN))
add('petty-range-fallback', 'owner', PETTY + '&query[payment_range]=ieri%20al%20oggi', 200, '$petty-fallback')
add('export-type-other', 'owner', EXPORT + '?export_type=journal', 200, DEFAULT)

# --- m=xlsx: the get_excel_base64 workbook ---
X = EXPORT + '?m=xlsx'
add('xlsx', 'owner', X, 200, xbody(export_xlsx(listed())))
add('xlsx-collaborator', 'full_collaborator', X, 200, xbody(export_xlsx(listed())))
add('xlsx-foreign', 'foreign_owner', X, 200, xbody(export_xlsx(listed(association=FOREIGN)), FOREIGN))
add('xlsx-group', 'owner', X, 200, xbody(export_xlsx(listed(group=OWN_GROUP), group=OWN_GROUP)), headers={'X-Group-Id': OWN_GROUP})
add('xlsx-archived-shifted', 'owner', X + '&archived=1', 200, xbody(export_xlsx(listed(archived=True))))
add('xlsx-paid', 'owner', X + '&query[paid]=true', 200, xbody(export_xlsx(listed(paid=True))))
add('xlsx-search', 'owner', X + '&query[generalSearch]=rossi', 200, xbody(export_xlsx(listed(search='rossi'))))
add('xlsx-empty', 'owner', X, 200, xbody(export_xlsx([])), headers={'X-Group-Id': FOREIGN_GROUP})
add('xlsx-petty-spring', 'owner', PETTY + '&m=xlsx&query[payment_range]=2024/03/01%20al%202024/06/30', 200,
    xbody(export_xlsx(listed(span=SPRING, paid=True), petty=True, span=SPRING)))
add('xlsx-petty-year', 'owner', PETTY + '&m=xlsx&query[payment_range]=01/01/2024%20al%2031/12/2024', 200,
    xbody(export_xlsx(listed(span=YEAR, paid=True), petty=True, span=YEAR)))
# The workbook reads the account name with a '-' default: the CSV's 500 does not happen.
add('xlsx-petty-archived-row-without-account', 'owner', PETTY + '&m=xlsx&archived=True', 200, '$petty-archived')
add('xlsx-petty-last-row-without-account', 'owner', PETTY + '&m=xlsx&query[payment_range]=2025/01/01%20al%202025/01/31', 200,
    xbody(export_xlsx(listed(span=JANUARY, paid=True), petty=True, span=JANUARY)))
add('xlsx-petty-transfer-without-account', 'foreign_owner', PETTY + '&m=xlsx', 500, SERVER)
add('xlsx-athlete', 'athlete', X, 500, SERVER)
add('xlsx-range-empty', 'owner', X + '&query[payment_range]=', 500, SERVER)

# The fallback window starts a year ago: no authored transfer or payment is in it, the header only.
TEN_YEARS = (datetime.now(timezone.utc) - timedelta(days=3650), datetime.now(timezone.utc) + timedelta(days=1))
for case in cases:
    if case['response'] == '$petty-archived':
        case['response'] = xbody(export_xlsx(listed(archived=True, paid=True), petty=True, span=TEN_YEARS))
    if case['response'] == '$petty-fallback':
        out = io.StringIO()
        csv.writer(out).writerow(HEADER + ['Saldo progressivo'])
        case['response'] = body(out.getvalue())

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'Payment list export (CSV) authored sequence', 'ids': IDS, 'categories': CATEGORIES,
    'accounts': ACCOUNTS, 'associates': ASSOCIATES, 'instructor': INSTRUCTOR, 'supplier': SUPPLIER, 'courses': COURSES,
    'invoice': INVOICE, 'subscription': SUBSCRIPTION, 'payments': PAYMENTS, 'course_subscriptions': COURSE_SUBSCRIPTIONS,
    'installments': INSTALLMENTS, 'transfers': TRANSFERS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
