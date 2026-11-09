"""Author the invoice-views HTTP sequence (invoice-views.md).

The invoice HTML views of BE/docmanager/views/printing_views.py that the
renderer service prints (@api_view(['GET']), IsAuthenticated |
HasBypassAuthorizationHeader):

- document/invoice/<uid>/view/: the scoped, live, group-aware invoice and its
  one scoped, live, group-aware payment (none or two: 500); the payment type in
  Italian; an OTHER payment's description from generate_invoice_description;
  stamp_free; a COURSE payment without a category gets an in-memory category
  named get_course_carnet_name(); the meta payment categories dict (lowercased
  names, "(n)" for repeats, the main category's float remainder); a cancelled
  invoice's style and footer; the associate's tutor (selected tutor, else the
  main tutor) and is_minor; get_subscription, the first membership (of the
  subscription, or without one when there is none), their periods; the plan
  block (its try/except stops at the first exception); the association's
  invoice template; header, footer and extra text through filter_mentions
  (a blank extra text becomes None); hide_category_name and override_label;
  custom_data with is_multiple.
- document/einvoice/<uid>/view/: CustomerInvoice.objects.get(pk=uid) (no scope,
  no group) and generate_invoice_html (a38's FatturaPA model, its validation and
  VAT summary); every exception is the 500 page.

Every invoice HTML expectation is an authored template context (the
filter_mentions output written out by hand) that the contract renders with
Django's own template engine; '$today' is the UTC date as dd/mm/YYYY. Every
einvoice expectation is the authored CustomerInvoice row, which the contract
passes (unsaved, never read from the database) to generate_invoice_html.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('invoice_views_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
HTML = '<html>'
EINVOICE = '<einvoice>'


def uid(n):
    return f'a1000000-0000-4000-8000-{986000 + n:012d}'


UNKNOWN = uid(999)


# --- tagged template values (rendered by the contract with Django) ----------------------------
def s(v):
    return {'t': 'str', 'v': v}


def b(v):
    return {'t': 'bool', 'v': v}


def i(v):
    return {'t': 'int', 'v': v}


def flt(v):
    return {'t': 'float', 'v': v}


def dec(v):
    return {'t': 'decimal', 'v': v}


def d(v):
    return {'t': 'date', 'v': v} if v else NONE


def dt(v):
    return {'t': 'datetime', 'v': v} if v else NONE


def dct(*pairs):
    return {'t': 'dict', 'v': [[k, v] for k, v in pairs]}


def obj(**attrs):
    return {'t': 'obj', 'v': [[k, v] for k, v in attrs.items()]}


def call(v):
    return {'t': 'call', 'v': v}


def opt(v):
    return NONE if v is None else s(v)


NONE = {'t': 'none'}

# --- fixture rows ------------------------------------------------------------------------------
M = lambda key: f'<span class="mention" data-type="mention" key="{key}">@x</span>'
ALPHA_HEADER = (f'<p class="intestazione">Spett.le {M("sport_association.denomination")} per {M("associate.first_name")} '
                f'(tutore {M("main_tutor.last_name")}) - @dataodierna</p>')


def alpha_header(first='', tutor_last=''):
    return f'<p class="intestazione">Spett.le ASD Alfa &amp; Figli per {first} (tutore {tutor_last}) - $today</p>'


ALPHA_FOOTER = f'<p>Corsi: {M("other.courses_list")}</p>'


def alpha_footer(courses='', cancelled=False):
    text = f'<p>Corsi: {courses}</p>'
    return text + '<br/><br/><b>RICEVUTA ANNULLATA (non è più valida)</b>' if cancelled else text


ALPHA_EXTRA = f'<p>Nota per {M("main_tutor.first_name")} &amp; soci</p>'


def alpha_extra(tutor_first=''):
    return f'<p>Nota per {tutor_first} &amp; soci</p>'


ASSOCIATIONS = {
    'alpha': {'user': uid(1), 'id': uid(2), 'email': 'alfa-ricevute@example.test', 'denomination': 'ASD Alfa & Figli',
              'tax_code': '01234567890', 'logo': 'data:image/png;base64,QUJD', 'document_header': ALPHA_HEADER,
              'invoice_footer': ALPHA_FOOTER, 'extra_text_invoices': ALPHA_EXTRA, 'invoice_template': None,
              'subscription_fee_plans': [{'id': 'p1', 'name': 'Piano Base'}, {'id': 2, 'name': 'Piano Due'}],
              'membership_fee_plans': [{'name': 'senza id'}, {'id': 'm1', 'name': 'Tessera Annuale'}],
              'enumerate_invoices': True, 'hide_category_name': False, 'default_category': None},
    'classic': {'user': uid(3), 'id': uid(4), 'email': 'classico-ricevute@example.test', 'denomination': 'Club Classico',
                'tax_code': '09876543210', 'logo': None, 'document_header': None, 'invoice_footer': None,
                'extra_text_invoices': '<p><br></p>  ', 'invoice_template': 'invoice_classic.html', 'subscription_fee_plans': [],
                'membership_fee_plans': [], 'enumerate_invoices': False, 'hide_category_name': True, 'default_category': 'quota'},
    'fallback': {'user': uid(5), 'id': uid(6), 'email': 'ripiego-ricevute@example.test', 'denomination': 'Ripiego <srl>',
                 'tax_code': '22222222222', 'logo': '', 'document_header': '<p><br></p>', 'invoice_footer': '',
                 'extra_text_invoices': '', 'invoice_template': 'altro.html', 'subscription_fee_plans': ['x'],
                 'membership_fee_plans': [{'id': 'm1', 'name': 'Mai letta'}], 'enumerate_invoices': False,
                 'hide_category_name': False, 'default_category': None},
}
CATEGORIES = {
    'quota': {'id': uid(10), 'name': 'Quota Associativa', 'tax_deductible': True, 'deleted': False},
    'kit': {'id': uid(11), 'name': 'Kit & Maglia', 'tax_deductible': False, 'deleted': False},
    'kitdup': {'id': uid(12), 'name': 'KIT & maglia', 'tax_deductible': True, 'deleted': False},
    'corsi': {'id': uid(13), 'name': 'Corsi', 'tax_deductible': True, 'deleted': True},
    'empty': {'id': uid(14), 'name': '', 'tax_deductible': False, 'deleted': False},
}
ASSOCIATES = {
    'mario': {'id': uid(20), 'first': 'Mario', 'last': 'Rossi', 'tax_code': 'RSSMRA80A01L219X', 'sex': 'M', 'born': '1980-01-01',
              'born_city': 'Torino', 'address': 'Via Po 2', 'city': 'Torino', 'cap': '10123', 'phone': '+39 333 0000000',
              'email': 'mario@example.test', 'is_minor': False},
    'giulia': {'id': uid(21), 'first': 'Giulia', 'last': 'Bianchi', 'tax_code': 'bncgli15h55l219k', 'sex': 'F', 'born': '2015-06-15',
               'born_city': 'Asti', 'address': None, 'city': 'Asti', 'cap': None, 'phone': None, 'email': 'giulia@example.test',
               'is_minor': False},
    'paolo': {'id': uid(22), 'first': 'Paolo', 'last': 'Bianchi', 'tax_code': 'BNCPLA75C03L219Z', 'sex': 'M', 'born': '1975-03-03',
              'born_city': 'Straße', 'address': 'Corso Francia 9', 'city': 'Torino', 'cap': '10138', 'phone': '011 123',
              'email': None, 'is_minor': False},
    'luca': {'id': uid(23), 'first': 'Luca', 'last': 'Verdi', 'tax_code': None, 'sex': 'A', 'born': '2016-02-02', 'born_city': None,
             'address': None, 'city': None, 'cap': None, 'phone': None, 'email': None, 'is_minor': True},
}
TUTORS = [('giulia', 'paolo', True)]
SUPPLIERS = {'acme': {'id': uid(25), 'name': 'ACME <srl>', 'address': 'Via X 1', 'tax_code': 'IT123'}}
INSTRUCTORS = {'anna': {'id': uid(26), 'first': 'Anna', 'last': "D'Amico"}}
COURSES = {'nuoto': {'id': uid(27), 'title': 'Nuoto & Acqua'}, 'yoga': {'id': uid(28), 'title': 'Yoga'},
           'untitled': {'id': uid(29), 'title': None}}
CARNETS = {'dieci': {'id': uid(30), 'title': 'Carnet 10 ingressi'}}
SUBSCRIPTIONS = {
    'subplan': {'id': uid(40), 'associate': 'giulia', 'type': 1, 'start': '2025-09-01', 'end': '2026-08-31',
                'meta': {'plan_id': 'p1', 'membership_plan_id': 'm1'},
                'custom_data': {'type_of_associate': 'asd-o-ssd', 'denomination': 'Scuola & co', 'city': 'Torino', 'province': 'to',
                                'address': 'Via Scuola 3', 'cap': '10100', 'country': 'it', 'tax_code': None, 'email': 'scuola@example.test'},
                'association': 'alpha', 'group': None},
    'subnamed': {'id': uid(41), 'associate': 'mario', 'type': 2, 'start': None, 'end': '2027-06-30',
                 'meta': {'plan_id': 2, 'plan_name': 'Scelto', 'membership_plan_name': None},
                 'custom_data': {'is_multiple': 1, 'type_of_associate': 'altro', 'denomination': 'Gruppo'},
                 'association': 'alpha', 'group': None},
    'subnoplan': {'id': uid(42), 'associate': 'luca', 'type': 3, 'start': None, 'end': None, 'meta': {'membership_plan_id': 'm1'},
                  'custom_data': None, 'association': 'alpha', 'group': None},
    'coursesub': {'id': uid(43), 'associate': 'luca', 'type': 1, 'start': '2026-01-10', 'end': '2026-06-30', 'meta': None,
                  'custom_data': {}, 'association': 'alpha', 'group': None},
    'listcustom': {'id': uid(44), 'associate': 'mario', 'type': 1, 'start': None, 'end': None, 'meta': None, 'custom_data': [1],
                   'association': 'alpha', 'group': None},
    'untitled': {'id': uid(45), 'associate': 'mario', 'type': 1, 'start': None, 'end': None, 'meta': None, 'custom_data': None,
                 'association': 'alpha', 'group': None},
    'badplans': {'id': uid(46), 'associate': 'mario', 'type': 2, 'start': '2026-02-01', 'end': '2026-02-28',
                 'meta': {'plan_id': 'zz', 'membership_plan_id': 'm1'}, 'custom_data': {'is_multiple': False},
                 'association': 'fallback', 'group': None},
}
# (id, subscription or None, start, end): a membership without a subscription is what filter(subscription=None) finds.
MEMBERSHIPS = [(uid(50), 'subplan', '2026-01-01', '2026-12-31'), (uid(51), None, '2024-01-01', '2024-12-31'),
               (uid(52), 'subnamed', '2026-09-01', '2027-08-31')]


def payment(key, invoice, **fields):
    row = {'id': uid(100 + key), 'invoice': invoice, 'association': 'alpha', 'type': 'cash', 'subject': 0, 'description': None,
           'notes': None, 'date': '2026-03-04T10:00:00+00:00', 'category': None, 'meta_categories': None, 'associate': None,
           'supplier': None, 'instructor': None, 'course': None, 'meta': None, 'deleted': False, 'group': None}
    row.update(fields)
    return row


def invoice(key, **fields):
    row = {'id': uid(200 + key), 'association': 'alpha', 'activity': '10.00', 'membership': '0.00', 'description': 'Salvata',
           'number': 7, 'cancelled': False, 'selected_tutor': None, 'deleted': False, 'group': None}
    row.update(fields)
    return row


INVOICES = {
    'supplier': invoice(1),
    'instructor': invoice(2, number=None, cancelled=True, activity='0.00', membership='0.00'),
    'nobody': invoice(3, activity='1234.56', membership='0.01'),
    'otherassoc': invoice(4, activity='12.00', membership='3.50', selected_tutor='paolo', description=None),
    'subscription': invoice(5, activity='30.00', membership='20.00'),
    'course': invoice(6, activity='25.00'),
    'coursemeta': invoice(7),
    'courselabel': invoice(8, association='classic'),
    'installment': invoice(9),
    'carnet': invoice(10, number=0),
    'fallback': invoice(11, association='fallback', activity='0.00', membership='5.00'),
    'carnetmissing': invoice(12),
    'badtype': invoice(13),
    'strsum': invoice(14),
    'missingcat': invoice(15),
    'metadict': invoice(16),
    'nopayment': invoice(17),
    'twopayments': invoice(18),
    'deletedpayment': invoice(19),
    'deleted': invoice(20, deleted=True),
    'group': invoice(21, group='own_group'),
    'listcustom': invoice(22),
    'untitled': invoice(23),
    'badplans': invoice(24, association='fallback'),
}
PAYMENTS = [
    payment(1, 'supplier', supplier='acme', category='kit', notes='Nota 1\nNota <2>'),
    payment(2, 'instructor', type='transfer', instructor='anna', description='', date=None),
    payment(3, 'nobody', type='pos', category='empty', description='Rimborso <spese>'),
    payment(4, 'otherassoc', type='online', associate='mario', category='quota',
            meta_categories=[{'payment_category_id': uid(11), 'amount': 2.5}, {'payment_category_id': uid(12), 'amount': 3}]),
    payment(5, 'subscription', type='sepa-transfer', subject=1, associate='giulia', category='quota',
            meta_categories=[{'payment_category_id': uid(11), 'amount': 5}, {'payment_category_id': uid(11), 'amount': -1}]),
    payment(6, 'course', type='stripe', subject=2, associate='luca', meta_categories=[{'payment_category_id': uid(13), 'amount': 1.1}]),
    payment(7, 'coursemeta', subject=2, category='kit', meta={'description': 'Corso estivo'}),
    payment(8, 'courselabel', association='classic', subject=2, course={'label': 'Pilates'}, description='Lunedì'),
    payment(9, 'installment', subject=2, associate='mario', category='kit'),
    payment(10, 'carnet', type='default', subject=2, associate='luca'),
    payment(11, 'fallback', association='fallback', associate='mario', description='X & Y'),
    payment(12, 'carnetmissing', subject=2),
    payment(13, 'badtype', type='bitcoin'),
    payment(14, 'strsum', category='kit', meta_categories=[{'payment_category_id': uid(11), 'amount': '3'}]),
    payment(15, 'missingcat', meta_categories=[{'payment_category_id': UNKNOWN, 'amount': 1}]),
    payment(16, 'metadict', meta_categories={'payment_category_id': uid(11)}),
    payment(17, 'twopayments'),
    payment(18, 'twopayments', description='seconda'),
    payment(19, 'deletedpayment', description='viva'),
    payment(20, 'deletedpayment', deleted=True),
    payment(21, 'deleted'),
    payment(22, 'group', group='own_group', description='gruppo'),
    payment(23, 'listcustom', subject=1, associate='mario'),
    payment(24, 'untitled', subject=1, associate='mario'),
    payment(25, 'badplans', association='fallback', subject=1, associate='mario'),
]
# Subscriptions linked by payment (Subscription.payment), course subscriptions, installments, carnet subscriptions.
SUBSCRIPTION_PAYMENTS = {'subplan': uid(105), 'listcustom': uid(123), 'untitled': uid(124), 'badplans': uid(125)}
COURSE_SUBSCRIPTIONS = [  # (id, course, subscription, payment, deleted)
    (uid(60), 'nuoto', 'coursesub', uid(106), False), (uid(61), 'yoga', None, uid(107), False),
    (uid(62), 'nuoto', 'subnamed', None, False), (uid(63), 'untitled', 'untitled', None, False),
    (uid(64), 'yoga', 'untitled', None, False)]
INSTALLMENTS = [(uid(65), uid(62), uid(109))]  # (id, course subscription, payment)
CARNET_SUBSCRIPTIONS = [(uid(66), 'dieci', 'subnoplan', uid(110)), (uid(67), None, None, uid(112))]  # (id, carnet, subscription, payment)

cases = []
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_HIDDEN = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
INVALID_JWT = {'detail': 'Given token not valid for any token type', 'code': 'token_not_valid',
               'messages': [{'token_class': 'AccessToken', 'token_type': 'access', 'message': 'Token is invalid'}]}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}


def add(name, actor, path, status, response, *, headers=None, html=None, einvoice=None, method_='GET', language='en'):
    case = {'name': f'invoice-views-{name}', 'actor': actor, 'method': method_, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'content_language': language}
    if html is not None:
        case['html'] = html
    if einvoice is not None:
        case['einvoice'] = einvoice
    cases.append(case)


def common(name, path, title):
    add(f'{name}-anonymous', 'anonymous', path, 401, ANONYMOUS)
    add(f'{name}-invalid-bearer', 'anonymous', path, 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'})
    add(f'{name}-admin-without-session', 'admin', path, 403, ADMIN)
    add(f'{name}-restricted-collaborator', 'restricted_collaborator', path, 403, unmapped(path.split('?')[0]))
    add(f'{name}-put-not-allowed', 'owner', path, 405, method('PUT'), method_='PUT')
    add(f'{name}-post-not-allowed', 'owner', path, 405, method('POST'), method_='POST')
    add(f'{name}-options', 'owner', path, 200, {'name': title, 'description': '', 'renders': ['application/json'], 'parses': PARSES},
        method_='OPTIONS')
    add(f'{name}-document-token', 'anonymous', path + '?token=' + UNKNOWN, 401, ANONYMOUS)
    add(f'{name}-bypass-invalid', 'anonymous', path, 401, ANONYMOUS, headers={'Authorization': 'not-the-token'})
    add(f'{name}-bypass-non-ascii', 'anonymous', path, 500, SERVER, headers={'Authorization': 'tokèn'})


# --- template objects --------------------------------------------------------------------------
def full_name(first, last):
    return f'{first} {last}'.strip()


def person(label):
    row = ASSOCIATES[label]
    return dict(first_name=opt(row['first']), last_name=opt(row['last']), tax_code=opt(row['tax_code']), sex=s(row['sex']),
                born_date=d(row['born']), born_city=opt(row['born_city']), address=opt(row['address']), address_city=opt(row['city']),
                address_cap=opt(row['cap']), phone=opt(row['phone']), email=opt(row['email']),
                get_full_name=call(s(full_name(str(row['first']), str(row['last'])))))


def associate_value(label, tutor):
    """payment.associate with the view's in-memory tutor and is_minor."""
    attrs = person(label)
    attrs['tutor'] = obj(**person(tutor)) if tutor else NONE
    attrs['is_minor'] = b(True if tutor else ASSOCIATES[label]['is_minor'])
    return obj(**attrs)


def invoice_html(key, *, description, category_name=None, associate=None, tutor=None, course_name=None, categories=None,
                 stamp_free=False, sub_type=None, sub_period='', membership_period='', plans=('-', '-', False, False), custom=None,
                 override=False, header='', footer='', extra=None, template='invoice.html', language='en', uid_value=None):
    inv = INVOICES[key]
    pay = next(p for p in PAYMENTS if p['invoice'] == key and not p['deleted'])
    a = ASSOCIATIONS[inv['association']]
    supplier = SUPPLIERS[pay['supplier']] if pay['supplier'] else None
    payment_value = obj(
        payment_category=obj(name=s(category_name)) if category_name is not None else NONE,
        description=opt(pay['description']), associate=associate_value(associate, tutor) if associate else NONE,
        supplier=obj(name=s(supplier['name']), address=opt(supplier['address']), tax_code=opt(supplier['tax_code'])) if supplier else NONE,
        payment_date=dt(pay['date']), notes=opt(pay['notes']), subject=i(pay['subject']))
    invoice_value = obj(sport_association=obj(logo=opt(a['logo']), denomination=s(a['denomination'])),
                        number=i(inv['number']) if inv['number'] is not None else NONE, description=description,
                        activity_fee=dec(inv['activity']), membership_fee=dec(inv['membership']))
    from decimal import Decimal
    total = Decimal(inv['activity']) + Decimal(inv['membership'])
    plan_name, membership_name, got_plan, got_membership = plans
    return {'template': 'document/application/' + template, 'language': language, 'context': {
        'uid': s(uid_value or inv['id']), 'extra_text_invoices': extra if isinstance(extra, dict) else opt(extra),
        'total_amount': dec(str(total)), 'body_style': s('opacity:0.7;text-decoration: line-through;' if inv['cancelled'] else ''),
        'payment': payment_value, 'course_name': opt(course_name), 'enumerate_invoices': b(a['enumerate_invoices']),
        'payment_categories': categories if categories is not None else NONE, 'custom_data': custom if custom is not None else NONE,
        'stamp_free': b(stamp_free), 'invoice': invoice_value,
        'payment_method': s({'cash': 'Contanti', 'transfer': 'Bonifico', 'online': 'Online', 'sepa-transfer': 'Bonifico SEPA',
                             'stripe': 'Stripe', 'pos': 'POS', 'default': 'Non specificato'}[pay['type']]),
        'sub': obj(type=i(sub_type)) if sub_type is not None else NONE, 'sub_memebership': NONE, 'sub_period': s(sub_period),
        'sub_membership_period': s(membership_period), 'override_label': b(override),
        'got_plan_name': plan_name if isinstance(plan_name, dict) else s(plan_name),
        'got_membership_plan_name': membership_name if isinstance(membership_name, dict) else s(membership_name),
        'got_plan': b(got_plan), 'got_membership_plan': b(got_membership), 'header': s(header), 'footer': s(footer)}}


ESC = lambda text: (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    .replace("'", '&#x27;'))
ALPHA = ESC('ASD Alfa & Figli')
NULL_MEMBERSHIP = '2024'  # the membership row without a subscription (filter(subscription=None).first())

# --- document/invoice/<uid>/view/ --------------------------------------------------------------
INV = lambda key: f"/document/invoice/{INVOICES[key]['id']}/view/"
common('invoice', INV('supplier'), 'Document Invoice View')

SUPPLIER_HTML = invoice_html(
    'supplier', description=s(f"Ricevuta di pagamento di <b>{ESC('ACME <srl>')}</b> con causale <b>{ESC('Kit & Maglia')}</b> "
                              f"per l'associazione <b>{ALPHA}</b>."),
    category_name='Kit & Maglia', override=True, membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(),
    extra=alpha_extra())
for name, actor, headers in (('invoice-owner', 'owner', {}), ('invoice-full-collaborator', 'full_collaborator', {}),
                             ('invoice-athlete-direct', 'athlete', {}), ('invoice-admin-impersonating-owner', 'admin', ADMIN_OWNER),
                             ('invoice-bypass', 'anonymous', {'Authorization': '$bypass'}),
                             ('invoice-foreign-owner', 'foreign_owner', {})):
    add(name, actor, INV('supplier'), 200, HTML, headers=headers, html=SUPPLIER_HTML)
add('invoice-uppercase-uid', 'owner', INV('supplier').upper().replace('/DOCUMENT/INVOICE/', '/document/invoice/').replace('/VIEW/', '/view/'),
    200, HTML, html={**SUPPLIER_HTML, 'context': {**SUPPLIER_HTML['context'], 'uid': s(INVOICES['supplier']['id'].upper())}})
add('invoice-italian', 'owner', INV('supplier'), 200, HTML, headers={'Accept-Language': 'it'}, language='it',
    html={**SUPPLIER_HTML, 'language': 'it'})
add('invoice-instructor-cancelled', 'owner', INV('instructor'), 200, HTML, html=invoice_html(
    'instructor', description=s(f"Ricevuta di pagamento di <b>{ESC(chr(65) + 'nna D' + chr(39) + 'Amico')}</b> con causale "
                                f"<b>Pagamento</b> per l'associazione <b>{ALPHA}</b>."),
    membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(cancelled=True), extra=alpha_extra()))
add('invoice-other-without-recipient', 'owner', INV('nobody'), 200, HTML, html=invoice_html(
    'nobody', description=s(f"Ricevuta di pagamento con causale <b>{ESC('Rimborso <spese>')}</b> per l'associazione <b>{ALPHA}</b>."),
    category_name='', override=True, membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(),
    extra=alpha_extra()))
add('invoice-other-associate-selected-tutor', 'owner', INV('otherassoc'), 200, HTML, headers={'Accept-Language': 'it'}, language='it',
    html=invoice_html(
        'otherassoc', description=s(f"Ricevuta di pagamento con causale <b>Quota Associativa</b> per l'associazione <b>{ALPHA}</b>."),
        category_name='Quota Associativa', associate='mario', tutor='paolo', override=True, stamp_free=False,
        categories=dct(('quota associativa', flt(12.00 - (0 + 2.5 + 3))), ('kit & maglia', flt(2.5)), ('kit & maglia (1)', i(3))),
        membership_period=NULL_MEMBERSHIP, header=alpha_header('Mario', 'Bianchi'), footer=alpha_footer(),
        extra=alpha_extra('Paolo'), language='it'))
add('invoice-subscription-plans', 'owner', INV('subscription'), 200, HTML, html=invoice_html(
    'subscription', description=s('Salvata'), category_name='Quota Associativa', associate='giulia', tutor='paolo', override=True,
    stamp_free=False, categories=dct(('kit & maglia', i(5)), ('kit & maglia (1)', i(-1))), sub_type=1, sub_period='25/26',
    membership_period='2026', plans=('Piano Base', '-', True, True),
    custom=dct(('type_of_associate', s('asd-o-ssd')), ('denomination', s('Scuola & co')), ('city', s('Torino')), ('province', s('to')),
               ('address', s('Via Scuola 3')), ('cap', s('10100')), ('country', s('it')), ('tax_code', NONE),
               ('email', s('scuola@example.test')), ('is_multiple', b(False))),
    header=alpha_header('Giulia', 'Bianchi'), footer=alpha_footer(), extra=alpha_extra('Paolo')))
add('invoice-course-in-memory-category', 'owner', INV('course'), 200, HTML, html=invoice_html(
    'course', description=s('Salvata'), category_name='Nuoto & Acqua', associate='luca', course_name='Nuoto & Acqua', override=True,
    stamp_free=True, categories=dct(('nuoto & acqua', flt(25.00 - 1.1)), ('corsi', flt(1.1))), sub_type=1, sub_period='2026',
    custom=dct(), header=alpha_header('Luca'), footer=alpha_footer(ESC('Nuoto & Acqua')), extra=alpha_extra()))
add('invoice-course-meta-description', 'owner', INV('coursemeta'), 200, HTML, html=invoice_html(
    'coursemeta', description=s('Salvata'), category_name='Kit & Maglia', course_name='Corso estivo', override=True, stamp_free=True,
    membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(), extra=alpha_extra()))
add('invoice-course-label-classic', 'owner', INV('courselabel'), 200, HTML, html=invoice_html(
    'courselabel', description=s('Salvata'), category_name='', course_name='Pilates, Lunedì', override=True, stamp_free=True,
    membership_period=NULL_MEMBERSHIP, template='invoice_classic.html'))
add('invoice-installment-subscription', 'owner', INV('installment'), 200, HTML, html=invoice_html(
    'installment', description=s('Salvata'), category_name='Kit & Maglia', associate='mario', course_name='Nuoto & Acqua',
    override=True, stamp_free=True, sub_type=2, sub_period='2027', membership_period='26/27',
    plans=('Scelto', NONE, True, False),
    custom=dct(('is_multiple', b(True)), ('type_of_associate', s('altro')), ('denomination', s('Gruppo'))),
    header=alpha_header('Mario'), footer=alpha_footer(ESC('Nuoto & Acqua')), extra=alpha_extra()))
add('invoice-carnet', 'owner', INV('carnet'), 200, HTML, html=invoice_html(
    'carnet', description=s('Salvata'), category_name='Carnet 10 ingressi', associate='luca', course_name='Carnet 10 ingressi',
    override=True, stamp_free=True, sub_type=3, plans=('-', '-', False, True), header=alpha_header('Luca'), footer=alpha_footer(),
    extra=alpha_extra()))
add('invoice-fallback-template', 'owner', INV('fallback'), 200, HTML, html=invoice_html(
    'fallback', description=s(f"Ricevuta di pagamento con causale <b>{ESC('X & Y')}</b> per l'associazione <b>{ESC('Ripiego <srl>')}</b>."),
    associate='mario', membership_period=NULL_MEMBERSHIP, header='<p><br/></p>', footer='', extra=''))
add('invoice-plans-stop-at-exception', 'owner', INV('badplans'), 200, HTML, html=invoice_html(
    'badplans', description=s('Salvata'), associate='mario', stamp_free=True, sub_type=2, sub_period='2026',
    plans=('-', '-', True, True), custom=dct(('is_multiple', b(False))), header='<p><br/></p>', footer='', extra=''))
add('invoice-deleted-second-payment', 'owner', INV('deletedpayment'), 200, HTML, html=invoice_html(
    'deletedpayment', description=s(f"Ricevuta di pagamento con causale <b>viva</b> per l'associazione <b>{ALPHA}</b>."),
    membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(), extra=alpha_extra()))
GROUP_HTML = invoice_html('group', description=s(f"Ricevuta di pagamento con causale <b>gruppo</b> per l'associazione <b>{ALPHA}</b>."),
                          membership_period=NULL_MEMBERSHIP, header=alpha_header(), footer=alpha_footer(), extra=alpha_extra())
add('invoice-group-own', 'owner', INV('group'), 200, HTML, headers={'X-Group-Id': IDS['own_group']}, html=GROUP_HTML)
add('invoice-group-malformed', 'owner', INV('group'), 200, HTML, headers={'X-Group-Id': 'not-a-group'}, html=GROUP_HTML)
add('invoice-group-other', 'owner', INV('group'), 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']})
add('invoice-group-filters-supplier', 'owner', INV('supplier'), 500, SERVER, headers={'X-Group-Id': IDS['own_group']})
add('invoice-carnet-without-carnet', 'owner', INV('carnetmissing'), 500, SERVER)
add('invoice-unknown-payment-type', 'owner', INV('badtype'), 500, SERVER)
add('invoice-meta-string-amount', 'owner', INV('strsum'), 500, SERVER)
add('invoice-meta-unknown-category', 'owner', INV('missingcat'), 500, SERVER)
add('invoice-meta-not-a-list', 'owner', INV('metadict'), 500, SERVER)
add('invoice-without-payment', 'owner', INV('nopayment'), 500, SERVER)
add('invoice-two-payments', 'owner', INV('twopayments'), 500, SERVER)
add('invoice-deleted', 'owner', INV('deleted'), 500, SERVER)
add('invoice-custom-data-list', 'owner', INV('listcustom'), 500, SERVER)
add('invoice-course-without-title', 'owner', INV('untitled'), 500, SERVER)
add('invoice-unknown', 'owner', f'/document/invoice/{UNKNOWN}/view/', 500, SERVER)
add('invoice-invalid-uuid', 'owner', '/document/invoice/not-a-uuid/view/', 500, SERVER)
add('invoice-scoped-athlete-hidden', 'owner', INV('supplier'), 403, SCOPED_HIDDEN, headers=SCOPED)

# --- document/einvoice/<uid>/view/ -------------------------------------------------------------
LINE = lambda description, quantity, unit, price, vat: {'description': description, 'quantity': quantity, 'unit_of_measure': unit,
                                                         'unit_price': price, 'vat': vat}


def customer(n, **fields):
    row = {'id': uid(300 + n), 'association': 'alpha', 'prefix': 'FPR', 'number': n, 'fiscal_year': 2026,
           'assignor_prefix_vat_number': 'IT', 'assignor_vat_number': '01234567890', 'assignor_tax_code': '01234567890',
           'assignor_denomination': 'ASD Alfa & Figli', 'assignor_fiscal_regime': 'RF01', 'assignor_address': 'Via Roma 1',
           'assignor_postal_code': '10100', 'assignor_city': 'Torino', 'assignor_province': 'TO', 'assignor_country': 'IT',
           'assignor_contact_email': 'alfa@example.test', 'assignor_contact_phone': '0111234567', 'assignor_fax': None,
           'transferor_prefix_vat_number': 'IT', 'transferor_vat_number': None, 'transferor_tax_code': 'RSSMRA80A01L219X',
           'transferor_denomination': 'Mario Rossi <Ditta>', 'transferor_fiscal_regime': 'RF01', 'transferor_address': 'Via Po 2',
           'transferor_postal_code': '10123', 'transferor_city': 'Torino', 'transferor_province': 'TO', 'transferor_country': 'IT',
           'transferor_contact_email': 'mario.pec@example.test', 'transferor_contact_phone': None, 'transferor_fax': None,
           'country_transmitter_prefix': 'IT', 'id_transmitter': None, 'transmitting_format': 'FPR12', 'document_type': 'TD01',
           'currency': 'EUR', 'transmitting_date': '2026-03-04', 'causal': 'Quota & servizi',
           'lines': [LINE('Quota annuale', 1, 'unita', 100.0, 22.0)], 'payment_condition': 'TP02', 'payment_modality': 'MP05',
           'payment_expiry_date': '2026-04-30'}
    row.update(fields)
    return row


CUSTOMER_INVOICES = {
    'basic': customer(1),
    'mixed': customer(2, lines=[LINE('Corso <nuoto> & "acqua"', 2, 'ore', 19.99, 22), LINE('Libro', 3, 'pz', '0.125', '4'),
                                LINE('Servizio', 1.5, 'h', 33.333, 10.0), LINE('Altro', 1, 'unita', 0.005, None)],
                       assignor_vat_number='09876543210', transferor_vat_number='12345678901', causal=None,
                       payment_condition='TP01', payment_modality='MP01'),
    'foreign': customer(3, association='foreign', prefix=None, transferor_denomination='Cliente Estero'),
    'rounding': customer(4, lines=[LINE('Arrotondamento', 3, 'pz', 2.675, 22), LINE('Metà', 1, 'pz', '0.005', 22)]),
    'nopec': customer(5, transferor_contact_email=None, id_transmitter='ABC1234'),
    'novat': customer(6, assignor_vat_number=None),
    'zerovat': customer(7, lines=[LINE('Esente', 1, 'pz', 10, '0')]),
    'zerovatint': customer(12, lines=[LINE('Aliquota zero intera', 1, 'pz', 10, 0)]),
    'missingkey': customer(8, lines=[{'description': 'Senza prezzo', 'quantity': 1, 'unit_of_measure': 'pz', 'vat': 22}]),
    'nolines': customer(9, lines=None),
    'badprovince': customer(10, transferor_province=''),
    'longdescription': customer(11, lines=[LINE('x' * 1001, 1, 'pz', 1, 22)]),
}
EIN = lambda key: f"/document/einvoice/{CUSTOMER_INVOICES[key]['id']}/view/"
common('einvoice', EIN('basic'), 'Document Einvoice View')
for name, actor, key, headers in (('einvoice-owner', 'owner', 'basic', {}), ('einvoice-athlete', 'athlete', 'basic', {}),
                                  ('einvoice-bypass', 'anonymous', 'basic', {'Authorization': '$bypass'}),
                                  ('einvoice-admin-impersonating-owner', 'admin', 'basic', ADMIN_OWNER),
                                  ('einvoice-mixed-rates', 'owner', 'mixed', {}),
                                  ('einvoice-other-association', 'owner', 'foreign', {}),
                                  ('einvoice-group-header-ignored', 'owner', 'basic', {'X-Group-Id': IDS['foreign_group']}),
                                  ('einvoice-half-up-rounding', 'full_collaborator', 'rounding', {}),
                                  ('einvoice-falsy-vat-defaults', 'owner', 'zerovatint', {})):
    add(name, actor, EIN(key), 200, EINVOICE, einvoice=key, headers=headers)
add('einvoice-italian', 'owner', EIN('basic'), 200, EINVOICE, einvoice='basic', headers={'Accept-Language': 'it'}, language='it')
for name, key in (('einvoice-without-pec', 'nopec'), ('einvoice-assignor-without-vat', 'novat'), ('einvoice-zero-vat', 'zerovat'),
                  ('einvoice-line-without-price', 'missingkey'), ('einvoice-without-lines', 'nolines'),
                  ('einvoice-empty-province', 'badprovince'), ('einvoice-long-description', 'longdescription')):
    add(name, 'owner', EIN(key), 500, SERVER)
add('einvoice-unknown', 'owner', f'/document/einvoice/{UNKNOWN}/view/', 500, SERVER)
add('einvoice-invalid-uuid', 'owner', '/document/einvoice/12/view/', 500, SERVER)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'invoice HTML views',
    'ids': IDS, 'associations': ASSOCIATIONS, 'categories': CATEGORIES, 'associates': ASSOCIATES, 'tutors': TUTORS,
    'suppliers': SUPPLIERS, 'instructors': INSTRUCTORS, 'courses': COURSES, 'carnets': CARNETS, 'subscriptions': SUBSCRIPTIONS,
    'memberships': MEMBERSHIPS, 'invoices': INVOICES, 'payments': PAYMENTS, 'subscription_payments': SUBSCRIPTION_PAYMENTS,
    'course_subscriptions': COURSE_SUBSCRIPTIONS, 'installments': INSTALLMENTS, 'carnet_subscriptions': CARNET_SUBSCRIPTIONS,
    'customer_invoices': CUSTOMER_INVOICES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
