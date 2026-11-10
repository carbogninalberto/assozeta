"""Author the invoice list, archived list, bulk archive, bulk delete and delete HTTP sequence (invoice-core.md).

Django semantics pinned here, in each view's order (invoice_views.py):
- invoice/list (@api_view GET): query[invoice_id] (truthy) resolves the
  live, group-aware invoice by id (a value UUIDField rejects is a 500; none is
  404 {'error': 'Invoice not found'}) and renders {'data': {'invoice': ...}}.
  Otherwise request.user.sportassociation (else the scoped live .get(): 500),
  then Invoice.objects (live, group-aware) filter(sport_association,
  archived=query[archived] through BooleanField.to_python, payment__isnull=False
  OR meta__isnull=False) by -number, -creation_date. The payment join is a
  LEFT JOIN through the base table: one row per linked payment (soft-deleted
  ones included), no distinct(). The balance-year range of request.user is
  computed (always); query[current_year] '1' / '0' adds a second payment join
  (payment_date within / outside the range), one row per matching payment.
  query[generalSearch]: d/m/Y becomes Y-m-d; a float() (',' as '.') filters
  number__icontains OR membership_fee/activity_fee = Decimal (9 digits); else
  smart_search over Invoice fields, selected_tutor's and the payment's
  associate/supplier/instructor fields, with distinct(). sort[field]/sort[sort]
  replace the ordering. KTDatatablePagination; data is keyed by row index,
  each row InvoiceSerializer plus 'payment' (PaymentInvoiceSerializer of the
  first prefetched live, group-aware payment, else the invoice meta) and
  'no_payment', without 'meta'. Invoices without a PDF dispatch a print task
  (none listed here).
- invoice/list/archived: IsAssociationUser (athletes: DRF's generic 403), the
  scoped live association .get() (500), the association's live, group-aware
  archived invoices by -creation_date: InvoiceSerializer plus, when the first
  live, group-aware payment by key exists, 'payment' (PaymentSerializer).
- invoice-bulk/archive (POST) and invoice-bulk/delete (DELETE): 'invoice_ids'
  must be a key of request.data (else, or a list body, a 500), the scoped live
  association .get() (500), then the association's live, group-aware invoices
  with invoice_id__in. Archive: bulk_update(archived=True), no audit. Delete:
  QuerySet.delete(): M2M links, payments (any state) unlinked without audit,
  InvoiceRows then invoices deleted by key with auditlog delete entries.
- invoice/<uid>/delete (POST): is_valid_uuid, the scoped live association
  .get(), the association's live, group-aware invoice (.first(); none: 500),
  the first live, group-aware payment by key: invoice=None, paid=False and
  Payment.save() (defaults, audited), then invoice.delete() as above.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlencode

OUTPUT = Path(__file__).with_name('invoice_core_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, GROUP = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['own_group']
_ids = iter(f'a1000000-0000-4000-8000-{433001 + n:012d}' for n in range(200))
UNKNOWN = 'a1000000-0000-4000-8000-000000433999'
SERVER = '<stable-server-error-page>'
CURRENT = '$current_year'  # the adapter seeds one hour after the owner's balance-year start


def uid():
    return next(_ids)


DOCUMENTS = {label: {'document_id': uid(), 'creation_date': '2026-09-01T08:00:00Z', 'filepath': f'private/{label}.pdf',
                     'filename': f'{label}.pdf', 'file_size_bytes': 2048, 'token': uid()}
             for label in ('pdf_main', 'pdf_archive', 'attached', 'receipt')}


def associate(first, last, tax_code, born):
    return {'associate_id': uid(), 'sport_association': OWN, 'user': None, 'first_name': first, 'last_name': last, 'sex': 'M',
            'is_minor': False, 'tax_code': tax_code, 'born_date': born, 'born_city': None, 'address': None, 'address_cap': None,
            'address_city': None, 'email': None, 'phone': None, 'family': None, 'family_role': None, 'group': None, 'deleted': False,
            'creation_date': '2025-01-01T08:00:00Z'}


ASSOCIATES = {'mario': associate('Mario', 'Rossi', 'RSSMRA80A01H501U', '1980-01-01'),
              'giulia': associate('Giulia', 'Bianchi', 'BNCGLI75D41H501Z', '1975-04-01')}
SUPPLIER = {'supplier_id': uid(), 'name': 'Fornitore Uno', 'address': 'Via Roma 1', 'tax_code': None, 'sport_association': OWN,
            'vat_number': '01234567890', 'type': 'supplier', 'email': None, 'phone_number': None, 'city': 'Torino', 'cap': '10100',
            'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}
CATEGORY = {'payment_category_id': uid(), 'name': 'Quote associative', 'creation_date': '2026-01-01T00:00:00Z', 'sport_association': OWN,
            'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1, 'vat_management': None, 'deleted': False}


def invoice(number, created, **values):
    row = {'invoice_id': uid(), 'document_pdf': DOCUMENTS['pdf_main']['document_id'], 'sport_association': OWN, 'creation_date': created,
           'membership_fee': '10.00', 'activity_fee': '5.00', 'meta_payment_categories': None, 'description': None, 'number': number,
           'archived': False, 'cancelled': False, 'selected_tutor': None, 'meta': None, 'imported_from_associami': False, 'group': None,
           'deleted': False}
    row.update(values)
    return row


INVOICES = {
    # Listed (not archived, a payment link or a meta).
    'grouped': invoice(None, '2026-01-15T09:00:00Z', group=GROUP, meta={'description': 'Gruppo', 'amount': 12}),
    'single': invoice(7, '2026-02-01T09:00:00Z', description='Ricevuta singola'),
    'bulk': invoice(6, '2026-02-02T09:00:00Z', meta_payment_categories=[{'amount': 15, 'subject': 0}]),
    'mario': invoice(3, '2024-03-05T10:00:00.250000Z', membership_fee='30.00', activity_fee='20.00', description='Ricevuta Mario'),
    'supplier': invoice(2, '2023-06-15T08:30:00Z', membership_fee='3.00', activity_fee='0.00', meta={'note': 'b'}),
    'meta': invoice(1, '2022-12-01T08:00:00Z', description='Quota annuale', meta={'amount': 10, 'description': 'Quota annuale'}),
    # Not listed.
    'plain': invoice(9, '2026-04-01T09:00:00Z'),
    'gone': invoice(8, '2026-04-02T09:00:00Z', deleted=True, meta={'x': 1}),
    'abroad': invoice(4, '2026-04-03T09:00:00Z', sport_association=FOREIGN, document_pdf=None, meta={'f': 1}),
    'archived': invoice(5, '2025-12-01T09:00:00Z', archived=True, document_pdf=DOCUMENTS['pdf_archive']['document_id'],
                        selected_tutor=ASSOCIATES['giulia']['associate_id']),
    'archived_plain': invoice(11, '2026-03-01T09:00:00Z', archived=True, document_pdf=None, cancelled=True),
    'grouped_plain': invoice(12, '2026-04-04T09:00:00Z', group=GROUP),
    'bulk_plain': invoice(13, '2026-04-05T09:00:00Z'),
    'bulk_abroad': invoice(14, '2026-04-06T09:00:00Z', sport_association=FOREIGN),
    'single_plain': invoice(15, '2026-04-07T09:00:00Z'),
    'single_grouped': invoice(16, '2026-04-08T09:00:00Z', group=GROUP),
    'single_abroad': invoice(17, '2026-04-09T09:00:00Z', sport_association=FOREIGN),
}
INVOICES['supplier']['selected_tutor'] = ASSOCIATES['giulia']['associate_id']
LINKS = [('mario', 'attached'), ('single', 'attached'), ('bulk', 'attached')]


def row(invoice_label, description, quantity, price):
    return {'invoice_row_id': uid(), 'invoice': INVOICES[invoice_label]['invoice_id'], 'description': description, 'quantity': quantity,
            'unit_price': price, 'vat': '22.00', 'unit_of_measure': None, 'meta': None}


ROWS = {'single_a': row('single', 'Riga uno', 1, '10.00'), 'single_b': row('single', 'Riga due', 2, '2.50'),
        'bulk': row('bulk', None, 1, '15.00')}
ROWS['bulk']['meta'] = {'k': [1, 'à']}


def payment(invoice_label, **values):
    row = {'payment_id': uid(), 'type': 'cash', 'subject': 0, 'amount': '15.00', 'creation_date': '2024-01-01T09:00:00Z',
           'payment_date': None, 'paid': True, 'expense': False, 'description': 'Quota', 'payment_category': CATEGORY['payment_category_id'],
           'meta_payment_categories': None, 'invoice': INVOICES[invoice_label]['invoice_id'], 'user': None, 'associate': None,
           'supplier': None, 'instructor': None, 'sport_association': OWN, 'payment_intent_id': None, 'archived': False,
           'invoice_fiscal_code': None, 'meta': None, 'notes': None, 'custom_accounts': None, 'signature': None, 'deleted': False,
           'group': None, 'course': None}
    row.update(values)
    return row


MARIO = ASSOCIATES['mario']['associate_id']
PAYMENTS = {
    'mario': payment('mario', associate=MARIO, user=OWNER, amount='50.00', payment_date='2024-03-05T10:00:00Z', description='Quota Mario'),
    'mario_old': payment('mario', associate=MARIO, deleted=True, payment_date='2021-02-01T09:00:00Z'),
    'supplier': payment('supplier', subject=1, supplier=SUPPLIER['supplier_id'], payment_date='2023-06-15T08:30:00.500000Z',
                        course={'label': 'Nuoto', 'id': 3}),
    'meta': payment('meta', deleted=True, payment_date=CURRENT),
    'bulk': payment('bulk', deleted=True, payment_date='2022-01-10T00:00:00Z'),
    'single': payment('single', subject=1, user=OWNER, description=None, payment_date='2024-09-01T07:00:00Z'),
    'single_old': payment('single', deleted=True, payment_date='2020-05-05T05:05:05Z'),
    'archived': payment('archived', subject=1, associate=MARIO, user=OWNER, amount='40.00', payment_date='2024-11-20T10:00:00Z',
                        description='Quota archiviata', notes='archivio', meta={'source': 'seed'}),
    'archived_old': payment('archived', deleted=True, payment_date='2019-01-01T00:00:00Z'),
    # Two soft-deleted payments list the PDF-less archived_plain invoice twice.
    'archived_plain_a': payment('archived_plain', deleted=True, payment_date='2019-02-01T00:00:00Z'),
    'archived_plain_b': payment('archived_plain', deleted=True, payment_date='2019-03-01T00:00:00Z'),
}
ATTACHMENTS = [('archived', 'receipt')]
SEED = copy.deepcopy({'documents': DOCUMENTS, 'associates': ASSOCIATES, 'supplier': SUPPLIER, 'category': CATEGORY, 'invoices': INVOICES,
                      'links': LINKS, 'rows': ROWS, 'payments': PAYMENTS, 'attachments': ATTACHMENTS})

# --- serializer renderings (authored from the serializers, not copied from a run) ---

OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}


def render_invoice(label):
    """InvoiceSerializer: fields '__all__' (attached_documents as keys) plus document_token."""
    row = INVOICES[label]
    document = next((d for d in DOCUMENTS.values() if d['document_id'] == row['document_pdf']), None)
    return {**row, 'document_token': document['token'] if document else None,
            'attached_documents': [DOCUMENTS[doc]['document_id'] for inv, doc in LINKS if inv == label]}


def render_associate_optimized(key):
    associate_row = next(a for a in ASSOCIATES.values() if a['associate_id'] == key)
    # No tutor relations are seeded: available_tutors is empty.
    return {'associate_id': key, 'first_name': associate_row['first_name'], 'last_name': associate_row['last_name'],
            'is_minor': associate_row['is_minor'], 'available_tutors': []}


def render_associate(key):
    """user_serializers.AssociateSerializer: its declared fields; user falls back to the association owner."""
    a = next(a for a in ASSOCIATES.values() if a['associate_id'] == key)
    return {'associate_id': key, 'address': a['address'], 'address_cap': a['address_cap'], 'address_city': a['address_city'],
            'born_city': a['born_city'], 'born_date': a['born_date'], 'email': a['email'], 'sex': a['sex'], 'first_name': a['first_name'],
            'last_name': a['last_name'], 'is_minor': a['is_minor'], 'sport_association': a['sport_association'], 'tax_code': a['tax_code'],
            'user': a['user'] or OWNER, 'phone': a['phone'], 'family': a['family'], 'family_role': a['family_role']}


def render_payment_invoice(label):
    """PaymentInvoiceSerializer: no seeded subscription matches, so subscription_id is None."""
    p = PAYMENTS[label]
    return {'user': OWNER_USER if p['user'] == OWNER else None,
            'associate': render_associate_optimized(p['associate']) if p['associate'] else None,
            'supplier': dict(SUPPLIER) if p['supplier'] else None, 'subscription_id': None, 'payment_date': p['payment_date'],
            'course': p['course']}


def render_payment(label):
    """PaymentSerializer: fields '__all__' with the nested relations."""
    p = PAYMENTS[label]
    invoice_label = next(k for k, v in INVOICES.items() if v['invoice_id'] == p['invoice'])
    return {'payment_id': p['payment_id'], 'user': OWNER_USER if p['user'] == OWNER else None,
            'associate': render_associate(p['associate']) if p['associate'] else None, 'subscription_id': None,
            'instructor_id': None, 'instructor': None, 'supplier': dict(SUPPLIER) if p['supplier'] else None,
            'supplier_id': p['supplier'], 'custom_account_type': None, 'custom_account_name': None, 'invoice': render_invoice(invoice_label),
            'attachments': [dict(DOCUMENTS[doc]) for pay, doc in ATTACHMENTS if pay == label],
            'payment_category_name': CATEGORY['name'] if p['payment_category'] else None, 'type': p['type'], 'subject': p['subject'],
            'amount': p['amount'], 'creation_date': p['creation_date'], 'payment_date': p['payment_date'], 'paid': p['paid'],
            'expense': p['expense'], 'description': p['description'], 'meta_payment_categories': p['meta_payment_categories'],
            'payment_intent_id': p['payment_intent_id'], 'archived': p['archived'], 'invoice_fiscal_code': p['invoice_fiscal_code'],
            'meta': p['meta'], 'notes': p['notes'], 'deleted': p['deleted'], 'course': p['course'], 'payment_category': p['payment_category'],
            'sport_association': p['sport_association'], 'custom_accounts': p['custom_accounts'], 'signature': p['signature'],
            'group': p['group']}


# The live payment each listed invoice renders (the prefetch excludes soft-deleted payments).
LIVE = {'mario': 'mario', 'supplier': 'supplier', 'single': 'single', 'archived': 'archived'}


def list_row(label):
    item = render_invoice(label)
    if label in LIVE:
        item['payment'], item['no_payment'] = render_payment_invoice(LIVE[label]), False
    else:
        item['payment'], item['no_payment'] = INVOICES[label]['meta'], True
    del item['meta']
    return item


def page(labels, *, total=None, number=1, pages=1, perpage=10):
    return {'data': {str(i): list_row(label) for i, label in enumerate(labels)},
            'meta': {'total': len(labels) if total is None else total, 'page': number, 'pages': pages, 'perpage': perpage,
                     'rowIds': [INVOICES[label]['invoice_id'] for label in labels]}}


def archived_item(label):
    item = render_invoice(label)
    if label in LIVE:
        item['payment'] = render_payment(LIVE[label])
    return item


cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None,
        celery=()):
    case = {'name': f'invoice-core-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'celery': list(celery)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
FORM = 'application/x-www-form-urlencoded'


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def access(prefix, path, method, permission, view, wrong):
    body = None if method == 'GET' else {'invoice_ids': []}
    add(f'{prefix}-anonymous', 'anonymous', method, path, 401, ANONYMOUS, body=body)
    add(f'{prefix}-admin-without-session', 'admin', method, path, 403, ADMIN, body=body)
    add(f'{prefix}-restricted-missing-permission', 'restricted_collaborator', method, path, 403,
        {'detail': f'Missing permission: bookeeping.documents.invoices.{permission}'}, body=body)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', method, path, 403, SCOPED_DENIED, headers=SCOPED, body=body)
    add(f'{prefix}-wrong-method', 'owner', wrong, path, 405, {'detail': f'Method "{wrong}" not allowed.'})
    add(f'{prefix}-options', 'owner', 'OPTIONS', path, 200, options(view))


# ------------------------------------------------------------ invoice/list ---

IL = '/invoice/list'


def listing(**params):
    return IL + '?' + urlencode(params)


DEFAULT = ['grouped', 'single', 'single', 'bulk', 'mario', 'mario', 'supplier', 'meta']
access('list', IL, 'GET', 'read', 'Invoice List', 'POST')
add('list-athlete-server-error', 'athlete', 'GET', IL, 500, SERVER)
add('list-owner-without-association-server-error', 'owner_without_association', 'GET', IL, 500, SERVER)
# -number puts NULL first; one row per linked payment (the deleted ones too).
add('list-owner-default', 'owner', 'GET', IL, 200, page(DEFAULT))
add('list-collaborator-default', 'full_collaborator', 'GET', IL, 200, page(DEFAULT))
add('list-admin-impersonating-owner', 'admin', 'GET', IL, 200, page(DEFAULT), headers=ADMIN_OWNER)
add('list-group-header', 'owner', 'GET', IL, 200, page(['grouped']), headers={'X-Group-Id': GROUP})
add('list-rejected-group-header-ignored', 'owner', 'GET', IL, 200, page(DEFAULT), headers={'X-Group-Id': 'not-a-group'})


def print_invoice(*labels):
    """print_document_invoice.delay(invoice_id, auth_token) once per listed row without a PDF, in page order, after the loop."""
    return [{'task': 'print_document_invoice', 'args': [INVOICES[label]['invoice_id'], '$authorization'], 'kwargs': []}
            for label in labels]


# archived_plain has no PDF and two (soft-deleted) payment rows: one publish per listed row.
ARCHIVED = ['archived_plain', 'archived_plain', 'archived', 'archived']
add('list-archived-true', 'owner', 'GET', listing(**{'query[archived]': 'True'}), 200, page(ARCHIVED),
    celery=print_invoice('archived_plain', 'archived_plain'))
add('list-archived-true-collaborator', 'full_collaborator', 'GET', listing(**{'query[archived]': 'True'}), 200, page(ARCHIVED),
    celery=print_invoice('archived_plain', 'archived_plain'))
add('list-archived-true-admin-impersonating', 'admin', 'GET', listing(**{'query[archived]': 'True'}), 200, page(ARCHIVED),
    headers=ADMIN_OWNER, celery=print_invoice('archived_plain', 'archived_plain'))
add('list-archived-true-second-row', 'owner', 'GET', listing(**{'query[archived]': 'True', 'pagination[perpage]': 1, 'pagination[page]': 2}),
    200, page(['archived_plain'], total=4, number=2, pages=4, perpage=1), celery=print_invoice('archived_plain'))
add('list-archived-true-last-page', 'owner', 'GET', listing(**{'query[archived]': 'True', 'pagination[perpage]': 2, 'pagination[page]': 2}),
    200, page(['archived', 'archived'], total=4, number=2, pages=2, perpage=2))
add('list-archived-zero', 'owner', 'GET', listing(**{'query[archived]': '0'}), 200, page(DEFAULT))
add('list-archived-lowercase-server-error', 'owner', 'GET', listing(**{'query[archived]': 'true'}), 500, SERVER)
add('list-second-page', 'owner', 'GET', listing(**{'pagination[perpage]': 3, 'pagination[page]': 2}), 200,
    page(['bulk', 'mario', 'mario'], total=8, number=2, pages=3, perpage=3))
add('list-last-page', 'full_collaborator', 'GET', listing(**{'pagination[perpage]': 3, 'pagination[page]': 'last'}), 200,
    page(['supplier', 'meta'], total=8, number=3, pages=3, perpage=3))
add('list-invalid-page', 'owner', 'GET', listing(**{'pagination[page]': 4}), 404, {'detail': 'Invalid page.'})
add('list-sort-number-asc', 'owner', 'GET', listing(**{'sort[field]': 'number', 'sort[sort]': 'asc'}), 200,
    page(['meta', 'supplier', 'mario', 'mario', 'bulk', 'single', 'single', 'grouped']))
add('list-sort-creation-desc', 'owner', 'GET', listing(**{'sort[field]': 'creation_date', 'sort[sort]': 'desc'}), 200,
    page(['bulk', 'single', 'single', 'grouped', 'mario', 'mario', 'supplier', 'meta']))
add('list-sort-unknown-server-error', 'owner', 'GET', listing(**{'sort[field]': 'bogus'}), 500, SERVER)
add('list-sort-double-minus-server-error', 'owner', 'GET', listing(**{'sort[field]': '-number', 'sort[sort]': 'desc'}), 500, SERVER)
# float('3'): number icontains '3' or a fee equal to 3 (no distinct).
add('list-search-number', 'owner', 'GET', listing(**{'query[generalSearch]': '3'}), 200, page(['mario', 'mario', 'supplier']))
add('list-search-comma-fee', 'owner', 'GET', listing(**{'query[generalSearch]': '30,0'}), 200, page(['mario', 'mario']))
add('list-search-nan-server-error', 'owner', 'GET', listing(**{'query[generalSearch]': 'nan'}), 500, SERVER)
# smart_search (distinct): the payment's associate, selected_tutor, description, the supplier.
add('list-search-associate', 'owner', 'GET', listing(**{'query[generalSearch]': 'ROSSI'}), 200, page(['mario']))
add('list-search-two-words', 'owner', 'GET', listing(**{'query[generalSearch]': 'mario rossi'}), 200, page(['mario']))
add('list-search-selected-tutor', 'owner', 'GET', listing(**{'query[generalSearch]': 'bianchi'}), 200, page(['supplier']))
add('list-search-description', 'owner', 'GET', listing(**{'query[generalSearch]': 'annuale'}), 200, page(['meta']))
add('list-search-supplier', 'owner', 'GET', listing(**{'query[generalSearch]': 'fornitore'}), 200, page(['supplier']))
# A term without \w words filters nothing, but smart_search still returns distinct() rows.
add('list-search-no-words-distinct', 'owner', 'GET', listing(**{'query[generalSearch]': '!!'}), 200,
    page(['grouped', 'single', 'bulk', 'mario', 'supplier', 'meta']))
add('list-search-nothing', 'owner', 'GET', listing(**{'query[generalSearch]': 'zzzz'}), 200, page([]))
# current_year: a second payment join; the meta invoice's deleted payment is in the current balance year.
add('list-current-year', 'owner', 'GET', listing(**{'query[current_year]': '1'}), 200, page(['meta']))
add('list-previous-years', 'owner', 'GET', listing(**{'query[current_year]': '0'}), 200,
    page(['single'] * 4 + ['bulk'] + ['mario'] * 4 + ['supplier']))
add('list-current-year-other-ignored', 'owner', 'GET', listing(**{'query[current_year]': '2'}), 200, page(DEFAULT))
# query[invoice_id] resolves the invoice by id before the association.
for label in ('mario', 'abroad', 'archived'):
    add(f'list-invoice-id-{label}', 'owner', 'GET', listing(**{'query[invoice_id]': INVOICES[label]['invoice_id']}), 200,
        {'data': {'invoice': render_invoice(label)}})
add('list-invoice-id-athlete', 'athlete', 'GET', listing(**{'query[invoice_id]': INVOICES['meta']['invoice_id']}), 200,
    {'data': {'invoice': render_invoice('meta')}})
add('list-invoice-id-deleted', 'owner', 'GET', listing(**{'query[invoice_id]': INVOICES['gone']['invoice_id']}), 404,
    {'error': 'Invoice not found'})
add('list-invoice-id-unknown', 'owner', 'GET', listing(**{'query[invoice_id]': UNKNOWN}), 404, {'error': 'Invoice not found'})
add('list-invoice-id-group-hidden', 'owner', 'GET', listing(**{'query[invoice_id]': INVOICES['mario']['invoice_id']}), 404,
    {'error': 'Invoice not found'}, headers={'X-Group-Id': GROUP})
add('list-invoice-id-malformed-server-error', 'owner', 'GET', listing(**{'query[invoice_id]': 'nope'}), 500, SERVER)

# --------------------------------------------------- invoice/list/archived ---

AL = '/invoice/list/archived'
access('archived', AL, 'GET', 'archive.read', 'Invoice List Archived', 'POST')
add('archived-athlete-forbidden', 'athlete', 'GET', AL, 403, FORBIDDEN)
add('archived-owner-without-association-server-error', 'owner_without_association', 'GET', AL, 500, SERVER)
add('archived-owner', 'owner', 'GET', AL, 200, {'data': [archived_item('archived_plain'), archived_item('archived')]})
add('archived-collaborator', 'full_collaborator', 'GET', AL, 200, {'data': [archived_item('archived_plain'), archived_item('archived')]})
add('archived-group-header', 'owner', 'GET', AL, 200, {'data': []}, headers={'X-Group-Id': GROUP})

# ---------------------------------------------------------------- effects ---


def effect(actor, *, changed=None, deleted=None, audits=()):
    """changed: {model: {pk: {attname: value}}}; deleted: {model: [pk]} (invoice deletes also drop their M2M links)."""
    return {'actor': actor, 'changed': changed or {}, 'deleted': deleted or {}, 'audit': list(audits)}


def deleted_audit(model, key, index):
    """An auditlog delete entry rendered by the runner from the row before the case."""
    return {'model': model, 'key': key, 'action': 2, 'changes': None, 'index': index}


INVOICE_INDEX = [OWN, 'changes_json.sport_association']

# ------------------------------------------------------ invoice-bulk/archive ---

BA = '/invoice-bulk/archive'
access('bulk-archive', BA, 'POST', 'archive.update', 'Invoice Bulk Archive', 'GET')
add('bulk-archive-missing-key-server-error', 'owner', 'POST', BA, 500, SERVER, body={'ids': []})
add('bulk-archive-list-body-server-error', 'owner', 'POST', BA, 500, SERVER, body=[INVOICES['plain']['invoice_id']])
add('bulk-archive-athlete-server-error', 'athlete', 'POST', BA, 500, SERVER, body={'invoice_ids': []})
add('bulk-archive-owner-without-association-server-error', 'owner_without_association', 'POST', BA, 500, SERVER, body={'invoice_ids': []})
add('bulk-archive-malformed-id-server-error', 'owner', 'POST', BA, 500, SERVER, body={'invoice_ids': [INVOICES['plain']['invoice_id'], 'x']})
add('bulk-archive-string-server-error', 'owner', 'POST', BA, 500, SERVER, body={'invoice_ids': 'abc'})
ARCHIVED = {'message': 'All Invoices archived'}
add('bulk-archive-empty-string', 'owner', 'POST', BA, 200, ARCHIVED, body={'invoice_ids': ''}, effect=effect('owner'))
# A form body is a QueryDict: data['invoice_ids'] is the last value, a string.
add('bulk-archive-form-empty', 'owner', 'POST', BA, 200, ARCHIVED, raw_body='invoice_ids=x&invoice_ids=', content_type=FORM,
    effect=effect('owner'))
add('bulk-archive-form-id-server-error', 'owner', 'POST', BA, 500, SERVER, raw_body=f"invoice_ids={INVOICES['plain']['invoice_id']}",
    content_type=FORM)
add('bulk-archive-empty-list', 'owner', 'POST', BA, 200, ARCHIVED, body={'invoice_ids': [None]}, effect=effect('owner'))
add('bulk-archive-group-hidden', 'owner', 'POST', BA, 200, ARCHIVED, headers={'X-Group-Id': GROUP},
    body={'invoice_ids': [INVOICES['plain']['invoice_id']]}, effect=effect('owner'))
add('bulk-archive-owner', 'owner', 'POST', BA, 200, ARCHIVED,
    body={'invoice_ids': [INVOICES['plain']['invoice_id'], INVOICES['abroad']['invoice_id'], INVOICES['gone']['invoice_id'], UNKNOWN]},
    effect=effect('owner', changed={'invoice': {INVOICES['plain']['invoice_id']: {'archived': True}}}))
add('bulk-archive-admin-impersonating', 'admin', 'POST', BA, 200, ARCHIVED, headers=ADMIN_OWNER,
    body={'invoice_ids': {INVOICES['grouped_plain']['invoice_id']: 'key'}},
    effect=effect('admin', changed={'invoice': {INVOICES['grouped_plain']['invoice_id']: {'archived': True}}}))

# ------------------------------------------------------- invoice-bulk/delete ---

BD = '/invoice-bulk/delete'
access('bulk-delete', BD, 'DELETE', 'delete', 'Invoice Bulk Delete', 'GET')
add('bulk-delete-missing-key-server-error', 'owner', 'DELETE', BD, 500, SERVER, body={})
add('bulk-delete-athlete-server-error', 'athlete', 'DELETE', BD, 500, SERVER, body={'invoice_ids': []})
add('bulk-delete-malformed-id-server-error', 'owner', 'DELETE', BD, 500, SERVER, body={'invoice_ids': ['x']})
DELETED_ALL = {'message': 'All Invoices deleted'}
add('bulk-delete-nothing-matches', 'owner', 'DELETE', BD, 200, DELETED_ALL,
    body={'invoice_ids': [INVOICES['bulk_abroad']['invoice_id'], UNKNOWN]}, effect=effect('owner'))
# The collector deletes each model's rows in descending key order.
bulk_ids = sorted([INVOICES['bulk']['invoice_id'], INVOICES['bulk_plain']['invoice_id']], reverse=True)
# The collaborator acts as the owner; InvoiceRows entries fall back to the
# actor's association (the collaborator has none: no index row).
add('bulk-delete-collaborator', 'full_collaborator', 'DELETE', BD, 200, DELETED_ALL,
    body={'invoice_ids': [INVOICES['bulk_plain']['invoice_id'], INVOICES['bulk']['invoice_id'], INVOICES['bulk_abroad']['invoice_id']]},
    effect=effect('full_collaborator', changed={'payment': {PAYMENTS['bulk']['payment_id']: {'invoice_id': None}}},
                  deleted={'invoice': bulk_ids, 'invoicerows': [ROWS['bulk']['invoice_row_id']]},
                  audits=[deleted_audit('invoicerows', ROWS['bulk']['invoice_row_id'], None)]
                  + [deleted_audit('invoice', key, INVOICE_INDEX) for key in bulk_ids]))

# --------------------------------------------------------- invoice/<uid>/delete ---


def ID(label):
    return f'/invoice/{INVOICES[label]["invoice_id"] if label in INVOICES else label}/delete'


access('delete', ID('single'), 'POST', 'delete', 'Invoice Delete', 'GET')
add('delete-malformed', 'owner', 'POST', ID('not-a-uuid'), 400, BAD_UUID, body={})
add('delete-athlete-server-error', 'athlete', 'POST', ID('single'), 500, SERVER, body={})
add('delete-owner-without-association-server-error', 'owner_without_association', 'POST', ID('single'), 500, SERVER, body={})
add('delete-unknown-server-error', 'owner', 'POST', ID(UNKNOWN), 500, SERVER, body={})
add('delete-foreign-server-error', 'owner', 'POST', ID('single_abroad'), 500, SERVER, body={})
add('delete-soft-deleted-server-error', 'owner', 'POST', ID('gone'), 500, SERVER, body={})
add('delete-group-hidden-server-error', 'owner', 'POST', ID('single_plain'), 500, SERVER, body={}, headers={'X-Group-Id': GROUP})
DELETED = {'message': 'Invoice deleted'}
single_rows = sorted([ROWS['single_a']['invoice_row_id'], ROWS['single_b']['invoice_row_id']], reverse=True)
pay = PAYMENTS['single']['payment_id']
# Payment.save(): subject 1 without a description takes 'Iscrizione'; the
# soft-deleted payment is unlinked by the collector without an entry.
add('delete-owner-with-payment', 'owner', 'POST', ID('single'), 200, DELETED, body={},
    effect=effect('owner', changed={'payment': {pay: {'invoice_id': None, 'paid': False, 'description': 'Iscrizione'},
                                                PAYMENTS['single_old']['payment_id']: {'invoice_id': None}}},
                  deleted={'invoice': [INVOICES['single']['invoice_id']], 'invoicerows': single_rows},
                  audits=[{'model': 'payment', 'key': pay, 'action': 1, 'index': [OWN, 'direct'],
                           'changes': {'invoice': [INVOICES['single']['invoice_id'], 'None'], 'paid': ['True', 'False'],
                                       'description': ['None', 'Iscrizione']}}]
                  + [deleted_audit('invoicerows', key, [OWN, 'actor.sport_association']) for key in single_rows]
                  + [deleted_audit('invoice', INVOICES['single']['invoice_id'], INVOICE_INDEX)]))
add('delete-collaborator-without-payment', 'full_collaborator', 'POST', ID('single_plain'), 200, DELETED, body={},
    effect=effect('full_collaborator', deleted={'invoice': [INVOICES['single_plain']['invoice_id']]},
                  audits=[deleted_audit('invoice', INVOICES['single_plain']['invoice_id'], INVOICE_INDEX)]))
add('delete-group-visible', 'owner', 'POST', ID('single_grouped'), 200, DELETED, body={}, headers={'X-Group-Id': GROUP},
    effect=effect('owner', deleted={'invoice': [INVOICES['single_grouped']['invoice_id']]},
                  audits=[deleted_audit('invoice', INVOICES['single_grouped']['invoice_id'], INVOICE_INDEX)]))
add('delete-again-server-error', 'owner', 'POST', ID('single'), 500, SERVER, body={})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Invoice list, archived list, bulk archive, bulk delete and delete authored sequence',
                              'ids': IDS, **SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
