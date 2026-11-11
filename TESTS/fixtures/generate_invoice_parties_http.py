"""Author the customer and supplier invoice list, add and update HTTP sequence (invoice-parties.md).

Django semantics pinned here, in each view's order:
- invoice-customers/list: request.user.sport_association first (athletes
  403 "User is not a sport association", owners without one 500). Then each
  of the association's CustomerInvoices without a PDF, by -creation_date
  then id, gets its XML regenerated and saved (an auditlog update of `xml`)
  and a synchronous print task (GET CURRENT_HOST/document/einvoice/<id>)
  with the caller's Authorization header; the seed has two of the own
  association and one of the foreign association, which stays as seeded.
  Then every invoice of the association by -creation_date with
  CustomerInvoiceSerializer (fields '__all__' plus the PDF token).
- invoice-customers/add: request.user.sport_association, then the view writes
  into request.data (a list, scalar or form body is a 500), defaults missing
  or null dates to today, converts both dates with strptime('%Y-%m-%d') (a
  500 otherwise) and validates CustomerInvoiceSerializer: field rules, the
  tenant-scoped relations (another tenant's payment is DRF's generic 403),
  UniqueTogetherValidator and validate() (a contact is required; the number
  and fiscal year must be new). A valid invoice is saved
  and turned into XML and a PDF: outside this sequence.
- invoice-customers/<uid>/update: is_valid_uuid, the impersonation-scoped
  association (.first(): none is a bare Exception, 500) and the association's
  invoice (none, 500). paid and transmitted are assigned raw ('in' on a list
  or a string tests membership) and Model.save() runs: auditlog's pre_save
  diff compares str() values, so the entry is written before
  BooleanField.to_python or the NOT NULL constraint rejects the value (500).
- invoice-suppliers/list: the scoped association .get() (500 for athletes),
  query[generalSearch] (a dd/mm/YYYY date becomes ISO, a float searches the
  amount, other text searches notes, identifier, dates and the supplier name),
  sort[field]/sort[sort] and KTDatatablePagination; data is keyed by index.
- invoice-suppliers/add: request.user.sport_association, the bank account
  (default or the association's named one), the live group-aware supplier, the
  "Pagamento Fornitore" category (the association's, else the shared one),
  then PaymentEntrySerializer and InvoiceSuppliersSerializer validation; the
  payment is created and saved, then the invoice is created and saved. Requests
  are not atomic: a failing invoice insert keeps the payment.
- invoice-suppliers/<uid>/update: is_valid_uuid, the scoped association and
  invoice (500), the partial serializer, the invoice save, then the linked
  payment gets the raw paid value and the current time (a missing key or a
  rejected value fails after the invoice save).
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('invoice_parties_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
_ids = iter(f'a1000000-0000-4000-8000-{846100 + n:012d}' for n in range(80))
UNKNOWN = 'a1000000-0000-4000-8000-000000846999'
SERVER = '<stable-server-error-page>'


def uid():
    return next(_ids)


def document(name):
    return {'document_id': uid(), 'creation_date': '2026-09-01T08:00:00Z', 'filepath': f'private/{name}', 'filename': name,
            'file_size_bytes': 2048, 'token': uid()}


DOCUMENTS = {label: document(f'{label}.pdf') for label in ('pdf_one', 'pdf_two', 'pdf_foreign', 'attachment', 'supplier_pdf')}


def supplier(association, name, **values):
    row = {'supplier_id': uid(), 'name': name, 'address': 'Via Roma 1', 'tax_code': None, 'sport_association': association,
           'vat_number': '01234567890', 'type': 'supplier', 'email': None, 'phone_number': None, 'city': 'Torino', 'cap': '10100',
           'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}
    row.update(values)
    return row


SUPPLIERS = {'uno': supplier(OWN, 'Fornitore Uno'), 'beta': supplier(OWN, 'Beta Srl', email='beta@example.test'),
             'gone': supplier(OWN, 'Cancellato', deleted=True), 'abroad': supplier(FOREIGN, 'Estero')}


def account(association, name, kind, editable):
    return {'custom_account_id': uid(), 'enabled': True, 'name': name, 'initial_balance': '0.00', 'account_type': kind,
            'account_code': name.upper(), 'sport_association': association, 'editable': editable}


ACCOUNTS = {'bank': account(OWN, 'Banca', 2, False), 'cash': account(OWN, 'Cassa', 1, True), 'abroad': account(FOREIGN, 'Estero', 2, False)}


def category(association, name, deleted=False):
    return {'payment_category_id': uid(), 'name': name, 'creation_date': '2026-01-01T00:00:00Z', 'sport_association': association,
            'expense': True, 'tax_deductible': False, 'archived': False, 'type': 0, 'vat_management': None, 'deleted': deleted}


CATEGORIES = {'own': category(OWN, 'pagamento FORNITORE'), 'own_deleted': category(OWN, 'Pagamento Fornitore', True),
              'abroad': category(FOREIGN, 'Pagamento Fornitore')}


def payment(association, supplier_label, **values):
    row = {'payment_id': uid(), 'type': 'transfer', 'subject': 0, 'amount': '120.50', 'creation_date': '2026-10-01T09:00:00Z',
           'payment_date': None, 'paid': False, 'expense': True, 'description': 'Fattura fornitore A', 'payment_category': CATEGORIES['own']['payment_category_id'],
           'meta_payment_categories': None, 'invoice': None, 'user': OWNER, 'associate': None,
           'supplier': SUPPLIERS[supplier_label]['supplier_id'], 'instructor': None, 'sport_association': association,
           'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': {'source': 'seed'}, 'notes': None,
           'custom_accounts': ACCOUNTS['bank']['custom_account_id'], 'signature': None, 'deleted': False, 'group': None, 'course': None}
    row.update(values)
    return row


PAYMENTS = {'a': payment(OWN, 'uno'),
            'b': payment(OWN, 'beta', amount='75.00', paid=True, payment_date='2026-10-10T12:30:00.250000Z', description='Fattura fornitore B',
                         custom_accounts=ACCOUNTS['cash']['custom_account_id'], meta=None, notes='saldo'),
            'abroad': payment(FOREIGN, 'abroad', custom_accounts=ACCOUNTS['abroad']['custom_account_id'],
                              payment_category=CATEGORIES['abroad']['payment_category_id'], user=None),
            # Payment.save() branches on the linked payment: a NULL description is derived from the subject,
            # and 'quota corso' takes meta['description']. In the foreign association (its owner updates them),
            # so the own association's lists stay as authored.
            'd': payment(FOREIGN, 'abroad', subject=1, description=None, amount='30.00', user=None,
                         custom_accounts=ACCOUNTS['abroad']['custom_account_id'], payment_category=CATEGORIES['abroad']['payment_category_id']),
            'e': payment(FOREIGN, 'abroad', subject=2, description='QUOTA corso', amount='45.00', meta={'description': 'Saldo corso estivo'},
                         user=None, custom_accounts=ACCOUNTS['abroad']['custom_account_id'],
                         payment_category=CATEGORIES['abroad']['payment_category_id'])}
ATTACHMENTS = [('a', 'attachment')]


def supplier_invoice(association, identifier, **values):
    row = {'invoice_supplier_id': uid(), 'invoice_identifier': identifier, 'amount': '120.50', 'payment_date': None,
           'expire_date': '2026-12-01T00:00:00Z', 'sport_association': association, 'payment': None, 'paid': False, 'notes': None,
           'document_pdf': None, 'supplier': None}
    row.update(values)
    return row


SUPPLIER_INVOICES = {
    'a': supplier_invoice(OWN, 'FA-001', payment=PAYMENTS['a']['payment_id'], supplier=SUPPLIERS['uno']['supplier_id'], notes='nota interna',
                          document_pdf=DOCUMENTS['supplier_pdf']['document_id']),
    'b': supplier_invoice(OWN, 'FB-002', amount='75.00', expire_date='2026-11-15T00:00:00Z', paid=True,
                          payment_date='2026-10-10T12:30:00.250000Z', payment=PAYMENTS['b']['payment_id'], supplier=SUPPLIERS['beta']['supplier_id'],
                          notes='finanza'),
    'c': supplier_invoice(OWN, 'FC-003 è', amount='1.50', expire_date=None),
    'abroad': supplier_invoice(FOREIGN, 'FX-900', payment=PAYMENTS['abroad']['payment_id'], supplier=SUPPLIERS['abroad']['supplier_id']),
    'd': supplier_invoice(FOREIGN, 'FD-004', amount='30.00', payment=PAYMENTS['d']['payment_id'], supplier=SUPPLIERS['abroad']['supplier_id']),
    'e': supplier_invoice(FOREIGN, 'FE-005', amount='45.00', payment=PAYMENTS['e']['payment_id'], supplier=SUPPLIERS['abroad']['supplier_id']),
}

TEXT = ['assignor_vat_number', 'assignor_tax_code', 'assignor_denomination', 'assignor_address', 'assignor_postal_code',
        'assignor_city', 'assignor_province', 'assignor_contact_email', 'assignor_contact_phone', 'transferor_vat_number',
        'transferor_tax_code', 'transferor_denomination', 'transferor_address', 'transferor_postal_code', 'transferor_city',
        'transferor_province', 'transferor_contact_email', 'causal', 'payment_condition', 'payment_modality', 'xml']


def customer_invoice(association, number, created, pdf):
    row = {'customer_invoice_id': uid(), 'prefix': 'FPR', 'number': number, 'fiscal_year': 2026, 'assignor_prefix_vat_number': 'IT',
           'assignor_fiscal_regime': 'RF01', 'assignor_country': 'IT', 'assignor_fax': None, 'transferor_prefix_vat_number': 'IT',
           'transferor_fiscal_regime': 'RF01', 'transferor_country': 'IT', 'transferor_contact_phone': '', 'transferor_fax': None,
           'e_invoice': True, 'country_transmitter_prefix': 'IT', 'id_transmitter': 'ABC1234', 'transmitting_format': 'FPR12',
           'document_type': 'TD01', 'currency': 'EUR', 'transmitting_date': '2026-10-02',
           'lines': [{'description': 'riga à', 'quantity': 1, 'unit_price': 100.5, 'vat': 22}], 'payment_total_amount': '122.61',
           'payment_expiry_date': '2026-12-31', 'pdf': DOCUMENTS[pdf]['document_id'], 'json': {'b': [True], 'a': 'x'},
           'sport_association': association, 'payment': None, 'creation_date': created, 'paid': False, 'transmitted': False}
    for index, name in enumerate(TEXT):
        row[name] = f'{name[:2]}{index}'
    row.update(assignor_province='RM', transferor_province='MI', payment_condition='TP02', payment_modality='MP05',
               transferor_contact_email='cliente@example.test')
    return row


CUSTOMER_INVOICES = {'one': customer_invoice(OWN, 1, '2026-10-05T09:00:00Z', 'pdf_one'),
                     'two': customer_invoice(OWN, 2, '2026-10-07T10:15:30.500000Z', 'pdf_two'),
                     'abroad': customer_invoice(FOREIGN, 3, '2026-10-06T09:00:00Z', 'pdf_foreign')}
CUSTOMER_INVOICES['two'].update(paid=True, payment=PAYMENTS['a']['payment_id'])
# Printing pending (no PDF): every list request regenerates its XML and prints it.
CUSTOMER_INVOICES['pending'] = customer_invoice(FOREIGN, 4, '2026-10-08T09:00:00Z', 'pdf_one')
CUSTOMER_INVOICES['pending'].update(
    pdf=None, xml=None, assignor_vat_number='01234567890', assignor_tax_code='01234567890', assignor_denomination='ASD Estero & Co',
    assignor_address='Via Roma 1', assignor_postal_code='10100', assignor_city='Torino', assignor_province='TO',
    assignor_contact_email='estero@example.test', assignor_contact_phone='0111234567', transferor_vat_number=None,
    transferor_tax_code='RSSMRA80A01L219X', transferor_denomination='Mario Rossi', transferor_address='Via Po 2',
    transferor_postal_code='10123', transferor_city='Torino', transferor_province='TO', transferor_contact_email='mario.pec@example.test',
    causal='Quota & servizi', lines=[{'description': 'Quota annuale', 'quantity': 1, 'unit_of_measure': 'unita', 'unit_price': 100.0,
                                      'vat': 22.0}])
# The association's own invoices without a PDF: regenerated and printed by its list, newest first.
for label, number, created in (('pending_own', 5, '2026-10-09T08:00:00Z'), ('pending_own_old', 6, '2026-10-01T08:00:00Z')):
    CUSTOMER_INVOICES[label] = {**copy.deepcopy(CUSTOMER_INVOICES['pending']), 'customer_invoice_id': uid(), 'number': number,
                                'creation_date': created, 'sport_association': OWN, 'assignor_denomination': 'ASD Casa & Co'}
# Payments PaymentSerializer nests more for, and Payment.save() branches, in the foreign association (its owner lists and
# updates them): an associate, instructor and invoice; a deleted payment (the manager auditlog reads does not see it); a
# course payment without a description (its course label); no description and no category; an account transfer.
_more = iter(f'a1000000-0000-4000-8000-{846700 + n:012d}' for n in range(40))
FOREIGN_OWNER = IDS['foreign_owner']
ASSOCIATES = {'socio': {'associate_id': next(_more), 'first_name': 'Mario', 'last_name': 'Rossi', 'sex': 'M', 'tax_code': 'RSSMRA90E10H501Z',
                        'born_date': '1990-05-10', 'email': 'mario@example.test', 'is_minor': False, 'sport_association': FOREIGN}}
INSTRUCTORS = {'docente': {'instructor_id': next(_more), 'user': FOREIGN_OWNER, 'first_name': 'Anna è', 'last_name': 'Bianchi',
                           'email': 'anna@example.test', 'tax_code': 'BNCNNA80A41H501X', 'default_hourly_billing': '12.50'}}
INVOICES = {'ricevuta': {'invoice_id': next(_more), 'sport_association': FOREIGN, 'creation_date': '2026-02-01T10:00:00Z',
                         'membership_fee': '0.00', 'activity_fee': '30.00', 'description': 'Ricevuta 901', 'number': 901, 'archived': True}}


def foreign_payment(label, identifier, **values):
    PAYMENTS[label] = payment(FOREIGN, 'abroad', **{'user': None, 'custom_accounts': ACCOUNTS['abroad']['custom_account_id'],
                                                    'payment_category': CATEGORIES['abroad']['payment_category_id'],
                                                    'payment_id': next(_more), **values})
    SUPPLIER_INVOICES[label] = supplier_invoice(FOREIGN, identifier, payment=PAYMENTS[label]['payment_id'],
                                                supplier=SUPPLIERS['abroad']['supplier_id'], invoice_supplier_id=next(_more))


foreign_payment('f', 'FF-006', associate=ASSOCIATES['socio']['associate_id'], instructor=INSTRUCTORS['docente']['instructor_id'],
                invoice=INVOICES['ricevuta']['invoice_id'], description='Con socio')
foreign_payment('g', 'FG-007', deleted=True, description='Cancellato')
foreign_payment('h', 'FH-008', subject=2, description=None, course={'label': 'Corso base'})
foreign_payment('i', 'FI-009', description=None, payment_category=None)
foreign_payment('j', 'FJ-010', subject=3, description=None)
SEED_PAYMENTS, SEED_SUPPLIER_INVOICES, SEED_CUSTOMER_INVOICES = (copy.deepcopy(PAYMENTS), copy.deepcopy(SUPPLIER_INVOICES),
                                                                 copy.deepcopy(CUSTOMER_INVOICES))

# --- serializer renderings (authored from the serializers, not copied from a run) ---

OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}


def render_supplier(label):
    row = SUPPLIERS[label]
    return dict(row)


def render_document(label):
    return dict(DOCUMENTS[label])


def render_associate(label):
    """AssociateSerializer (user_serializers): an associate without an account answers its association's owner as user."""
    row = ASSOCIATES[label]
    return {'associate_id': row['associate_id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
            'born_date': row['born_date'], 'email': row['email'], 'sex': row['sex'], 'first_name': row['first_name'],
            'last_name': row['last_name'], 'is_minor': False, 'sport_association': row['sport_association'], 'tax_code': row['tax_code'],
            'user': FOREIGN_OWNER, 'phone': None, 'family': None, 'family_role': None}


def render_instructor(label):
    row = INSTRUCTORS[label]
    return {'instructor_id': row['instructor_id'], 'user': row['user'], 'first_name': row['first_name'], 'last_name': row['last_name'],
            'email': row['email'], 'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row['tax_code'],
            'address_city': None, 'address': None, 'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore',
            'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None,
            'is_volunteer': False, 'stipulated_contract_in': None, 'default_hourly_billing': row['default_hourly_billing'],
            'default_percentage_billing': '0.00', 'documents': []}


def render_invoice(label):
    row = INVOICES[label]
    return {'invoice_id': row['invoice_id'], 'document_token': None, 'creation_date': row['creation_date'],
            'membership_fee': row['membership_fee'], 'activity_fee': row['activity_fee'], 'meta_payment_categories': None,
            'description': row['description'], 'number': row['number'], 'archived': row['archived'], 'cancelled': False, 'meta': None,
            'imported_from_associami': False, 'deleted': False, 'document_pdf': None, 'sport_association': row['sport_association'],
            'selected_tutor': None, 'group': None, 'attached_documents': []}


def find(table, key, value):
    return next((label for label, row in table.items() if row[key] == value), None)


def render_payment(label):
    row = PAYMENTS[label]
    supplier_row = next(s for s in SUPPLIERS.values() if s['supplier_id'] == row['supplier'])
    account_row = next(a for a in ACCOUNTS.values() if a['custom_account_id'] == row['custom_accounts'])
    category_row = next((c for c in CATEGORIES.values() if c['payment_category_id'] == row['payment_category']), {'name': None})
    person, teacher, bill = (find(ASSOCIATES, 'associate_id', row['associate']), find(INSTRUCTORS, 'instructor_id', row['instructor']),
                             find(INVOICES, 'invoice_id', row['invoice']))
    return {'payment_id': row['payment_id'], 'user': OWNER_USER if row['user'] == OWNER else None,
            'associate': render_associate(person) if person else None,
            # subject OTHER: the associate's first unarchived subscription (none exist); the authored subscription and
            # course payments have no subscription, installment, course subscription or carnet.
            'subscription_id': None, 'instructor_id': row['instructor'], 'instructor': render_instructor(teacher) if teacher else None,
            'supplier': dict(supplier_row),
            'supplier_id': supplier_row['supplier_id'], 'custom_account_type': account_row['account_type'],
            'custom_account_name': account_row['name'], 'invoice': render_invoice(bill) if bill else None,
            'attachments': [render_document(doc) for pay, doc in ATTACHMENTS if pay == label],
            'payment_category_name': category_row['name'], 'type': row['type'], 'subject': row['subject'], 'amount': row['amount'],
            'creation_date': row['creation_date'], 'payment_date': row['payment_date'], 'paid': row['paid'], 'expense': row['expense'],
            'description': row['description'], 'meta_payment_categories': row['meta_payment_categories'],
            'payment_intent_id': row['payment_intent_id'], 'archived': row['archived'], 'invoice_fiscal_code': row['invoice_fiscal_code'],
            'meta': row['meta'], 'notes': row['notes'], 'deleted': row['deleted'], 'course': row['course'],
            'payment_category': row['payment_category'], 'sport_association': row['sport_association'],
            'custom_accounts': row['custom_accounts'], 'signature': None, 'group': None}


def render_supplier_invoice(label):
    row = SUPPLIER_INVOICES[label]
    pay = next((key for key, value in PAYMENTS.items() if value['payment_id'] == row['payment']), None)
    sup = next((key for key, value in SUPPLIERS.items() if value['supplier_id'] == row['supplier']), None)
    doc = next((key for key, value in DOCUMENTS.items() if value['document_id'] == row['document_pdf']), None)
    return {'invoice_supplier_id': row['invoice_supplier_id'], 'payment': render_payment(pay) if pay else None,
            'payment_id': row['payment'], 'document_token': DOCUMENTS[doc]['token'] if doc else None, 'notes': row['notes'],
            'supplier': render_supplier(sup) if sup else None, 'invoice_identifier': row['invoice_identifier'], 'amount': row['amount'],
            'payment_date': row['payment_date'], 'expire_date': row['expire_date'], 'paid': row['paid'],
            'sport_association': row['sport_association'], 'document_pdf': row['document_pdf']}


def render_customer_invoice(label):
    row = dict(CUSTOMER_INVOICES[label])
    if row['pdf'] is None:
        # Regenerated by the list before it is serialized: '$xml' is the stored XML (the adapter checks the row).
        return {**row, 'xml': '$xml', 'pdf_token': None}
    doc = next(key for key, value in DOCUMENTS.items() if value['document_id'] == row['pdf'])
    return {**row, 'pdf_token': DOCUMENTS[doc]['token']}


cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=()):
    """prints: the customer invoices whose eager print_document_customer_invoice request the case makes, in order."""
    case = {'name': f'invoice-parties-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect,
            'prints': [{'path': f'/document/einvoice/{CUSTOMER_INVOICES[label]["customer_invoice_id"]}',
                        'reply': {'status': 200, 'body': 'printed'}} for label in prints]}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
FORM = 'application/x-www-form-urlencoded'


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def missing(kind, verb):
    return {'detail': f'Missing permission: bookeeping.documents.{kind}.{verb}'}


def access(prefix, path, method, kind, verb, view, wrong):
    add(f'{prefix}-anonymous', 'anonymous', method, path, 401, ANONYMOUS, body={} if method != 'GET' else None)
    add(f'{prefix}-admin-without-session', 'admin', method, path, 403, ADMIN, body={} if method != 'GET' else None)
    add(f'{prefix}-restricted-missing-permission', 'restricted_collaborator', method, path, 403, missing(kind, verb),
        body={} if method != 'GET' else None)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', method, path, 403, SCOPED_DENIED, headers=SCOPED,
        body={} if method != 'GET' else None)
    add(f'{prefix}-wrong-method', 'owner', wrong, path, 405, {'detail': f'Method "{wrong}" not allowed.'})
    add(f'{prefix}-options', 'owner', 'OPTIONS', path, 200, options(view))


# ---------------------------------------------------------------- effects ---

def audit(model, key, action, changes, *, association=OWN, path='direct'):
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'association': association, 'path': path}


def effect(actor, *, rows=None, new=None, audits=()):
    return {'actor': actor, 'rows': rows or {}, 'new': new or {}, 'audits': list(audits)}


def changed(table, label, **values):
    """Update the running seed state and return the audit pairs (str() of old and new)."""
    row = table[label]
    pairs = {}
    for key, value in values.items():
        old = row[key]
        if old != value:
            pairs[key] = [audit_str(key, old), audit_str(key, value)]
        row[key] = value
    return pairs


def audit_str(key, value):
    if value is None:
        return 'None'
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.endswith('Z') and 'T' in value:
        date, time = value[:-1].split('T')
        return f'{date} {time}'
    return str(value)


# ------------------------------------------------------- customer list ---

CL = '/invoice-customers/list'
access('customer-list', CL, 'GET', 'clientinvoices', 'read', 'Invoice Customers List', 'POST')


OWN_PENDING = ('pending_own', 'pending_own_old')
OWN_LISTED = ['pending_own', 'two', 'one', 'pending_own_old']


def regenerated(actor):
    """The loop's effect on the association's invoices without a PDF, newest first: the XML of the stored row saved ('$xml',
    ProgressivoInvio from the request time), one auditlog update of xml (indexed directly to the association), then its
    print request. The other association's invoice without a PDF is unchanged."""
    keys = [CUSTOMER_INVOICES[label]['customer_invoice_id'] for label in OWN_PENDING]
    return effect(actor, rows={'customerinvoice': {key: {'xml': '$xml'} for key in keys}},
                  audits=[{'model': 'customerinvoice', 'key': key, 'action': 1, 'changes': {'xml': ['$before-text', '$xml']},
                           'association': OWN, 'path': 'direct'} for key in keys])


# The association is resolved first: athletes and owners without one regenerate nothing.
add('customer-list-athlete-not-association', 'athlete', 'GET', CL, 403, NOT_ASSOCIATION, effect=effect('athlete'))
add('customer-list-owner-without-association-server-error', 'owner_without_association', 'GET', CL, 500, SERVER,
    effect=effect('owner_without_association'))
add('customer-list-owner', 'owner', 'GET', CL, 200, {'data': [render_customer_invoice(label) for label in OWN_LISTED]},
    effect=regenerated('owner'), prints=OWN_PENDING)
add('customer-list-full-collaborator', 'full_collaborator', 'GET', CL, 200,
    {'data': [render_customer_invoice(label) for label in OWN_LISTED]}, effect=regenerated('full_collaborator'),
    prints=OWN_PENDING)

# ------------------------------------------------------- supplier list ---

SL = '/invoice-suppliers/list'


def supplier_page(labels, *, total=None, page=1, pages=1, perpage=10):
    return {'data': {str(index): render_supplier_invoice(label) for index, label in enumerate(labels)},
            'meta': {'total': len(labels) if total is None else total, 'page': page, 'pages': pages, 'perpage': perpage,
                     'rowIds': [SUPPLIER_INVOICES[label]['invoice_supplier_id'] for label in labels]}}


def query(**params):
    from urllib.parse import urlencode
    return SL + '?' + urlencode(params)


access('supplier-list', SL, 'GET', 'supplierinvoices', 'read', 'Invoice Suppliers List', 'POST')
add('supplier-list-athlete-server-error', 'athlete', 'GET', SL, 500, SERVER)
add('supplier-list-owner-without-association-server-error', 'owner_without_association', 'GET', SL, 500, SERVER)
# -expire_date: PostgreSQL puts NULL first when descending.
add('supplier-list-owner-default-order', 'owner', 'GET', SL, 200, supplier_page(['c', 'a', 'b']))
add('supplier-list-collaborator-second-page', 'full_collaborator', 'GET', query(**{'pagination[perpage]': 2, 'pagination[page]': 2}), 200,
    supplier_page(['b'], total=3, page=2, pages=2, perpage=2))
add('supplier-list-invalid-page', 'owner', 'GET', query(**{'pagination[page]': 9}), 404, {'detail': 'Invalid page.'})
add('supplier-list-search-supplier-name', 'owner', 'GET', query(**{'query[generalSearch]': 'fornitore uno'}), 200, supplier_page(['a']))
add('supplier-list-search-notes', 'owner', 'GET', query(**{'query[generalSearch]': 'INTERNA'}), 200, supplier_page(['a']))
add('supplier-list-search-accent', 'owner', 'GET', query(**{'query[generalSearch]': '003 è'}), 200, supplier_page(['c']))
# float('120,5'.replace(',', '.')): amount::text contains '120.5' or equals 120.5.
add('supplier-list-search-comma-amount', 'owner', 'GET', query(**{'query[generalSearch]': '120,5'}), 200, supplier_page(['a']))
add('supplier-list-search-amount-text', 'owner', 'GET', query(**{'query[generalSearch]': ' 75 '}), 200, supplier_page(['b']))
add('supplier-list-search-amount-fraction', 'owner', 'GET', query(**{'query[generalSearch]': '1.5'}), 200, supplier_page(['c']))
# float('NaN') succeeds but the DecimalField lookup rejects a non-finite
# value inside the view's try: the text columns are searched instead.
add('supplier-list-search-nan-text', 'owner', 'GET', query(**{'query[generalSearch]': 'NaN'}), 200, supplier_page(['b']))
add('supplier-list-search-infinity-text', 'owner', 'GET', query(**{'query[generalSearch]': '-Infinity'}), 200, supplier_page([]))
# A dd/mm/YYYY date becomes 2026-12-01 and matches expire_date::text.
add('supplier-list-search-date', 'owner', 'GET', query(**{'query[generalSearch]': '1/12/2026'}), 200, supplier_page(['a']))
add('supplier-list-search-payment-date', 'owner', 'GET', query(**{'query[generalSearch]': '12:30:00.25'}), 200, supplier_page(['b']))
add('supplier-list-search-percent-literal', 'owner', 'GET', query(**{'query[generalSearch]': '%'}), 200, supplier_page([]))
add('supplier-list-sort-amount', 'owner', 'GET', query(**{'sort[field]': 'amount', 'sort[sort]': 'asc'}), 200, supplier_page(['c', 'b', 'a']))
add('supplier-list-sort-identifier-desc', 'owner', 'GET', query(**{'sort[field]': 'invoice_identifier', 'sort[sort]': 'desc'}), 200,
    supplier_page(['c', 'b', 'a']))
add('supplier-list-sort-attname', 'owner', 'GET', query(**{'sort[field]': 'expire_date', 'sort[sort]': 'other'}), 200,
    supplier_page(['b', 'a', 'c']))
add('supplier-list-sort-unknown-server-error', 'owner', 'GET', query(**{'sort[field]': 'bogus'}), 500, SERVER)
# Only the invoice's own columns order the list: relation paths, the owner account, transforms and the stored document
# answer as an unknown ordering.
for _name, _field in (('supplier-path', 'supplier__name'), ('association-path', 'sport_association__denomination'),
                      ('owner-account-path', 'sport_association__user__password'), ('owner-account-email', 'sport_association__user__email'),
                      ('payment-path', 'payment__amount'), ('document', 'document_pdf'), ('document-key', 'document_pdf_id'),
                      ('transform', 'amount__nope')):
    add('supplier-list-sort-' + _name + '-server-error', 'owner', 'GET', query(**{'sort[field]': _field}), 500, SERVER)

# The foreign association's list: payments with a subscription or course subject, an associate, instructor and invoice, and
# a deleted payment (the relation reads through the base manager).
add('supplier-list-foreign-owner-nested-payments', 'foreign_owner', 'GET', query(**{'sort[field]': 'invoice_identifier'}), 200,
    supplier_page(['d', 'e', 'f', 'g', 'h', 'i', 'j', 'abroad']))

# ------------------------------------------------------- customer add ---

CA = '/invoice-customers/add'
access('customer-add', CA, 'POST', 'clientinvoices', 'create', 'Invoice Customers Add', 'GET')
add('customer-add-athlete-not-association', 'athlete', 'POST', CA, 403, NOT_ASSOCIATION, body={})
add('customer-add-owner-without-association-server-error', 'owner_without_association', 'POST', CA, 500, SERVER, body={})
add('customer-add-list-body-server-error', 'owner', 'POST', CA, 500, SERVER, body=[])
add('customer-add-string-body-server-error', 'owner', 'POST', CA, 500, SERVER, body='invoice')
add('customer-add-form-body-server-error', 'owner', 'POST', CA, 500, SERVER, raw_body='number=4', content_type=FORM)
add('customer-add-bad-transmitting-date-server-error', 'owner', 'POST', CA, 500, SERVER, body={'transmitting_date': '02/10/2026'})
add('customer-add-number-transmitting-date-server-error', 'owner', 'POST', CA, 500, SERVER, body={'transmitting_date': 20261002})
add('customer-add-impossible-expiry-date-server-error', 'owner', 'POST', CA, 500, SERVER,
    body={'transmitting_date': '2026-1-5', 'payment_expiry_date': '2026-02-30'})
add('customer-add-field-errors', 'owner', 'POST', CA, 400, {
    'customer_invoice_id': ['Must be a valid UUID.'], 'prefix': ['This field may not be blank.'], 'number': ['A valid integer is required.'],
    'fiscal_year': ['Ensure this value is less than or equal to 2147483647.'],
    'assignor_province': ['Ensure this field has no more than 2 characters.'], 'assignor_address': ['This field may not be null.'],
    'e_invoice': ['Must be a valid boolean.'], 'causal': ['Not a valid string.'],
    'payment_condition': ['"TP09" is not a valid choice.'], 'payment_modality': ['"5" is not a valid choice.'],
    'payment_total_amount': ['Ensure that there are no more than 9 digits in total.'],
    'xml': ['This field may not be blank.'],
    'creation_date': ['Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'],
    'paid': ['This field may not be null.']},
    body={'customer_invoice_id': 'abc', 'prefix': ' ', 'number': 'x', 'fiscal_year': 2147483648, 'assignor_province': 'ROMA',
          'assignor_address': None, 'e_invoice': 'maybe', 'causal': True, 'payment_condition': 'TP09', 'payment_modality': 5,
          'payment_total_amount': '1234567.891', 'xml': '', 'creation_date': 'yesterday', 'paid': None, 'transmitting_date': None,
          'lines': {'any': ['json']}})
add('customer-add-related-errors', 'owner', 'POST', CA, 400, {
    'payment': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'pdf': ['“abc” is not a valid UUID.'],
    'number': ['Ensure this value is greater than or equal to -2147483648.'], 'payment_total_amount': ['A valid number is required.']},
    body={'payment': UNKNOWN, 'pdf': 'abc', 'number': -2147483649, 'payment_total_amount': 'dieci'})
add('customer-add-foreign-payment-forbidden', 'owner', 'POST', CA, 403, FORBIDDEN,
    body={'prefix': '', 'payment': PAYMENTS['abroad']['payment_id']})
add('customer-add-existing-id', 'owner', 'POST', CA, 400,
    {'customer_invoice_id': ['customer invoice with this customer invoice id already exists.']},
    body={'customer_invoice_id': CUSTOMER_INVOICES['abroad']['customer_invoice_id'], 'number': 40, 'id_transmitter': 'X'})
add('customer-add-unique-together', 'full_collaborator', 'POST', CA, 400,
    {'non_field_errors': ['The fields prefix, number, fiscal_year, sport_association must make a unique set.']},
    body={'number': 1, 'fiscal_year': '2026', 'id_transmitter': 'X'})
add('customer-add-contact-required', 'owner', 'POST', CA, 400,
    {'non_field_errors': ['Either transferor_contact_email or id_transmitter must be provided!']},
    body={'number': 41, 'fiscal_year': 2026, 'transferor_contact_email': None, 'payment': PAYMENTS['a']['payment_id']})
add('customer-add-number-taken-elsewhere', 'owner', 'POST', CA, 400, {'non_field_errors': ['Invoice already exists!']},
    body={'number': 3, 'fiscal_year': 2026, 'transferor_contact_email': 'x@example.test', 'pdf': DOCUMENTS['pdf_one']['document_id']})

# ------------------------------------------------------- customer update ---

CU = lambda label: f'/invoice-customers/{CUSTOMER_INVOICES[label]["customer_invoice_id"] if label in CUSTOMER_INVOICES else label}/update'
CUSTOMER_FIELDS = ('paid', 'transmitted')
access('customer-update', CU('one'), 'PATCH', 'clientinvoices', 'update', 'Invoice Customers Update', 'POST')
add('customer-update-malformed', 'owner', 'PATCH', CU('not-a-uuid'), 400, BAD_UUID, body={})
add('customer-update-athlete-server-error', 'athlete', 'PATCH', CU('one'), 500, SERVER, body={'paid': True})
add('customer-update-owner-without-association-server-error', 'owner_without_association', 'PATCH', CU('one'), 500, SERVER, body={})
add('customer-update-unknown-server-error', 'owner', 'PATCH', CU(UNKNOWN), 500, SERVER, body={'paid': True})
add('customer-update-foreign-server-error', 'owner', 'PATCH', CU('abroad'), 500, SERVER, body={'paid': True})


def customer_update(name, actor, label, status, response, body=None, *, values=None, saved=True, **extra):
    key = CUSTOMER_INVOICES[label]['customer_invoice_id']
    pairs = {}
    if values:
        before = {k: CUSTOMER_INVOICES[label][k] for k in values}
        pairs = changed(CUSTOMER_INVOICES, label, **values)
        if not saved:
            CUSTOMER_INVOICES[label].update(before)
    stored = {k: CUSTOMER_INVOICES[label][k] for k in CUSTOMER_FIELDS}
    audits = [audit('customerinvoice', key, 1, pairs)] if pairs else []
    add(name, actor, 'PATCH', CU(label), status, response, body=body,
        effect=effect(actor, rows={'customerinvoice': {key: stored}}, audits=audits), **extra)


UPDATED = {'message': 'Invoice updated'}
customer_update('customer-update-owner-paid', 'owner', 'one', 200, UPDATED, {'paid': True, 'other': 'ignored'}, values={'paid': True})
# 'True' is a valid boolean and equals the stored value's str(): no entry for paid.
customer_update('customer-update-collaborator-transmitted-text', 'full_collaborator', 'one', 200, UPDATED,
                {'paid': 'True', 'transmitted': 'True'}, values={'transmitted': True})
CUSTOMER_INVOICES['one']['transmitted'] = True
# The diff compares str(1) with 'True'; to_python stores True again.
key_one = CUSTOMER_INVOICES['one']['customer_invoice_id']
add('customer-update-integer-paid-audited', 'owner', 'PATCH', CU('one'), 200, UPDATED, body={'paid': 1},
    effect=effect('owner', rows={'customerinvoice': {key_one: {'paid': True, 'transmitted': True}}},
                  audits=[audit('customerinvoice', key_one, 1, {'paid': ['True', '1']})]))
add('customer-update-invalid-boolean-audited-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, body={'paid': 'yes'},
    effect=effect('owner', rows={'customerinvoice': {key_one: {'paid': True, 'transmitted': True}}},
                  audits=[audit('customerinvoice', key_one, 1, {'paid': ['True', 'yes']})]))
add('customer-update-null-paid-audited-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, body={'transmitted': None},
    effect=effect('owner', rows={'customerinvoice': {key_one: {'paid': True, 'transmitted': True}}},
                  audits=[audit('customerinvoice', key_one, 1, {'transmitted': ['True', 'None']})]))
def multipart(*parts, boundary='invoicepartiesboundary'):
    """parts: (name, filename or None, content type or None, text) as a multipart/form-data body."""
    chunks = []
    for name, filename, content_type, text in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        chunks.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'
                      + (f'Content-Type: {content_type}\r\n' if content_type else '') + f'\r\n{text}\r\n')
    return ''.join(chunks) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


# A file in the body is str() of its name in the entry, and not a boolean for the column.
mp, ct = multipart(('paid', 'stato.txt', 'text/plain', 'x'))
add('customer-update-paid-as-file-audited-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, raw_body=mp, content_type=ct,
    effect=effect('owner', rows={'customerinvoice': {key_one: {'paid': True, 'transmitted': True}}},
                  audits=[audit('customerinvoice', key_one, 1, {'paid': ['True', 'stato.txt']})]))
add('customer-update-list-with-key-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, body=['paid'])
customer_update('customer-update-list-without-key', 'owner', 'one', 200, UPDATED, ['transmit', 1])
customer_update('customer-update-string-without-key', 'owner', 'one', 200, UPDATED, 'transmit')
add('customer-update-string-with-key-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, body='unpaid')
add('customer-update-number-body-server-error', 'owner', 'PATCH', CU('one'), 500, SERVER, body=5)
customer_update('customer-update-form', 'owner', 'two', 200, UPDATED, None, values={'paid': False},
                raw_body='paid=False&transmitted=0', content_type=FORM)
key_two = CUSTOMER_INVOICES['two']['customer_invoice_id']
cases[-1]['effect']['audits'] = [audit('customerinvoice', key_two, 1, {'paid': ['True', 'False'], 'transmitted': ['False', '0']})]
customer_update('customer-update-empty-body', 'owner', 'two', 200, UPDATED, {})

# ------------------------------------------------------- supplier add ---

SA = '/invoice-suppliers/add'
access('supplier-add', SA, 'POST', 'supplierinvoices', 'create', 'Invoice Suppliers Add', 'GET')
NO_ACCOUNT = {'error': 'Nessun conto bancario attivo trovato.'}
NO_SUPPLIER = {'error': 'Fornitore non trovato.'}
CREATED = {'message': 'Invoice created'}
VALID = {'custom_accounts': None, 'supplier_id': SUPPLIERS['uno']['supplier_id'], 'payment_date': '2026-10-20T10:00:00Z', 'paid': True,
         'amount': '50.5', 'invoice_identifier': 'FN-100', 'expire_date': '2026-12-31T00:00:00Z', 'notes': 'nuova'}
DATETIME = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
add('supplier-add-athlete-not-association', 'athlete', 'POST', SA, 403, NOT_ASSOCIATION, body=VALID)
add('supplier-add-owner-without-association-server-error', 'owner_without_association', 'POST', SA, 500, SERVER, body=VALID)
add('supplier-add-list-body-server-error', 'owner', 'POST', SA, 500, SERVER, body=[VALID])
add('supplier-add-form-body-server-error', 'owner', 'POST', SA, 500, SERVER, raw_body='custom_accounts=&amount=1', content_type=FORM)
add('supplier-add-missing-account-key-server-error', 'owner', 'POST', SA, 500, SERVER, body={'supplier_id': SUPPLIERS['uno']['supplier_id']})
add('supplier-add-unknown-account', 'owner', 'POST', SA, 400, NO_ACCOUNT, body={**VALID, 'custom_accounts': UNKNOWN})
add('supplier-add-foreign-account', 'owner', 'POST', SA, 400, NO_ACCOUNT, body={**VALID, 'custom_accounts': ACCOUNTS['abroad']['custom_account_id']})
add('supplier-add-integer-account', 'owner', 'POST', SA, 400, NO_ACCOUNT, body={**VALID, 'custom_accounts': 7})
add('supplier-add-malformed-account-server-error', 'owner', 'POST', SA, 500, SERVER, body={**VALID, 'custom_accounts': 'abc'})
add('supplier-add-unknown-supplier', 'owner', 'POST', SA, 400, NO_SUPPLIER, body={**VALID, 'supplier_id': UNKNOWN})
add('supplier-add-deleted-supplier', 'owner', 'POST', SA, 400, NO_SUPPLIER, body={**VALID, 'supplier_id': SUPPLIERS['gone']['supplier_id']})
add('supplier-add-foreign-supplier', 'owner', 'POST', SA, 400, NO_SUPPLIER, body={**VALID, 'supplier_id': SUPPLIERS['abroad']['supplier_id']})
add('supplier-add-group-hides-supplier', 'owner', 'POST', SA, 400, NO_SUPPLIER, body=VALID, headers={'X-Group-Id': IDS['own_group']})
add('supplier-add-missing-supplier-key-server-error', 'owner', 'POST', SA, 500, SERVER, body={'custom_accounts': None})
add('supplier-add-missing-payment-date-server-error', 'owner', 'POST', SA, 500, SERVER,
    body={k: v for k, v in VALID.items() if k != 'payment_date'})
add('supplier-add-missing-amount-server-error', 'owner', 'POST', SA, 500, SERVER, body={k: v for k, v in VALID.items() if k != 'amount'})
add('supplier-add-missing-paid-server-error', 'owner', 'POST', SA, 500, SERVER, body={k: v for k, v in VALID.items() if k != 'paid'})
add('supplier-add-payment-errors', 'owner', 'POST', SA, 400, {
    'amount': ['A valid number is required.'], 'paid': ['Must be a valid boolean.'], 'payment_date': [DATETIME], 'creation_date': [DATETIME]},
    body={**VALID, 'amount': 'abc', 'paid': 'maybe', 'payment_date': 'bad', 'invoice_identifier': ''})
add('supplier-add-payment-amount-null', 'owner', 'POST', SA, 400, {'amount': ['This field may not be null.'], 'creation_date': [DATETIME]},
    body={**VALID, 'amount': None, 'paid': 0, 'payment_date': 'bad'})
add('supplier-add-invoice-errors', 'owner', 'POST', SA, 400, {
    'invoice_identifier': ['This field may not be blank.'], 'expire_date': [DATETIME], 'supplier': ['This field may not be null.'],
    'payment': {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']},
    'document_pdf': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'paid': ['Must be a valid boolean.']},
    body={**VALID, 'paid': '', 'payment_date': None, 'invoice_identifier': ' ', 'expire_date': 'x', 'supplier': None,
          'payment': 'x', 'document_pdf': UNKNOWN})
add('supplier-add-invoice-required', 'owner', 'POST', SA, 400, {'invoice_identifier': ['This field is required.'],
                                                                'paid': ['This field may not be null.']},
    body={k: v for k, v in {**VALID, 'paid': None}.items() if k != 'invoice_identifier'})
add('supplier-add-existing-invoice-id', 'owner', 'POST', SA, 400,
    {'invoice_supplier_id': ['invoice suppliers with this invoice supplier id already exists.']},
    body={**VALID, 'invoice_supplier_id': SUPPLIER_INVOICES['abroad']['invoice_supplier_id']})
add('supplier-add-negative-amount-server-error', 'owner', 'POST', SA, 500, SERVER, body={**VALID, 'amount': '-5'})
add('supplier-add-malformed-group-server-error', 'owner', 'POST', SA, 500, SERVER, body=VALID, headers={'X-Group-Id': 'zzz'})


def new_payment(account, amount, paid, payment_date, creation, supplier_label):
    return {'type': 'default', 'subject': 0, 'amount': amount, 'creation_date': creation, 'payment_date': payment_date, 'paid': paid,
            'expense': True, 'description': '$description:' + SUPPLIERS[supplier_label]['name'],
            'payment_category_id': CATEGORIES['own']['payment_category_id'], 'meta_payment_categories': None, 'invoice_id': None,
            'user_id': OWNER, 'associate_id': None, 'supplier_id': SUPPLIERS[supplier_label]['supplier_id'], 'instructor_id': None,
            'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
            'notes': None, 'custom_accounts_id': ACCOUNTS[account]['custom_account_id'], 'signature_id': None, 'deleted': False,
            'group_id': None, 'course': None}


def payment_audits(actor_association, supplier_label):
    # Payment.objects.create runs before user, association and supplier are
    # set: the create entry has them None and resolves through the actor.
    return [audit('payment', '$payment', 0, {'$created': ['user', 'sport_association', 'supplier']}, association=actor_association,
                  path='actor.sport_association' if actor_association else None),
            # pre_save: the index resolves the stored row, which has no association yet.
            audit('payment', '$payment', 1, {'user': ['None', OWNER], 'sport_association': ['None', OWN],
                                             'supplier': ['None', SUPPLIERS[supplier_label]['supplier_id']]},
                  path='changes_json.sport_association')]


def new_invoice(identifier, amount, paid, payment_date, expire, notes, supplier_label, invoice_id='$invoice'):
    return {'invoice_supplier_id': invoice_id, 'invoice_identifier': identifier, 'amount': amount, 'payment_date': payment_date,
            'expire_date': expire, 'sport_association_id': OWN, 'payment_id': '$payment', 'paid': paid, 'notes': notes,
            'document_pdf_id': None, 'supplier_id': SUPPLIERS[supplier_label]['supplier_id']}


def invoice_audits(supplier_label):
    return [audit('invoicesuppliers', '$invoice', 0, {'$created': ['payment', 'supplier']}),
            audit('invoicesuppliers', '$invoice', 1, {'payment': ['None', '$payment'], 'supplier': ['None', SUPPLIERS[supplier_label]['supplier_id']]})]


add('supplier-add-owner', 'owner', 'POST', SA, 200, CREATED, body=VALID, effect=effect('owner', new={
    'payment': new_payment('bank', '50.50', True, '2026-10-20T10:00:00Z', '2026-10-20T10:00:00Z', 'uno'),
    'invoicesuppliers': new_invoice('FN-100', '50.50', True, '2026-10-20T10:00:00Z', '2026-12-31T00:00:00Z', 'nuova', 'uno')},
    audits=payment_audits(OWN, 'uno') + invoice_audits('uno')))
add('supplier-add-collaborator-unpaid-now', 'full_collaborator', 'POST', SA, 200, CREATED,
    body={'custom_accounts': ACCOUNTS['cash']['custom_account_id'], 'supplier_id': SUPPLIERS['beta']['supplier_id'], 'payment_date': None,
          'paid': 0, 'amount': 9, 'invoice_identifier': 9, 'invoice_supplier_id': 'a1000000-0000-4000-8000-000000846900',
          'payment': None, 'payment_id': None, 'notes': '', 'expire_date': None, 'document_pdf': DOCUMENTS['supplier_pdf']['document_id']},
    effect=effect('full_collaborator', new={
        'payment': new_payment('cash', '9.00', False, None, '$now', 'beta'),
        'invoicesuppliers': {**new_invoice('9', '9.00', False, '$now', None, '', 'beta', 'a1000000-0000-4000-8000-000000846900'),
                             'document_pdf_id': DOCUMENTS['supplier_pdf']['document_id']}},
        audits=payment_audits(None, 'beta') + invoice_audits('beta')))
add('supplier-add-long-notes-keeps-payment-server-error', 'owner', 'POST', SA, 500, SERVER,
    body={**VALID, 'paid': 'false', 'notes': 'n' * 256}, effect=effect('owner', new={
        # 'false' is truthy for the view, so payment_date is kept, while BooleanField reads False.
        'payment': new_payment('bank', '50.50', False, '2026-10-20T10:00:00Z', '2026-10-20T10:00:00Z', 'uno')},
        audits=payment_audits(OWN, 'uno')))

# payment_id is a CharField: create() stores it before the view links the new payment. '' is stored as NULL and audited as
# sent; other text must be the UUID of a stored payment (a 500 with the new payment kept otherwise).
BRACED = '{' + PAYMENTS['a']['payment_id'].upper() + '}'
for _name, _text, _stored in (('blank', '', 'None'), ('other-spelling', BRACED, PAYMENTS['a']['payment_id'])):
    _audits = invoice_audits('uno')
    _audits[0]['changes']['$override'] = {'payment': ['None', _text]}
    _audits[1]['changes']['payment'][0] = _stored
    add(f'supplier-add-payment-id-{_name}', 'owner', 'POST', SA, 200, CREATED,
        body={**VALID, 'invoice_identifier': 'FP-' + _name, 'payment_id': _text}, effect=effect('owner', new={
            'payment': new_payment('bank', '50.50', True, '2026-10-20T10:00:00Z', '2026-10-20T10:00:00Z', 'uno'),
            'invoicesuppliers': new_invoice('FP-' + _name, '50.50', True, '2026-10-20T10:00:00Z', '2026-12-31T00:00:00Z', 'nuova', 'uno')},
            audits=payment_audits(OWN, 'uno') + _audits))
for _name, _text in (('not-a-uuid', 'zzz'), ('unknown', UNKNOWN)):
    add(f'supplier-add-payment-id-{_name}-keeps-payment-server-error', 'owner', 'POST', SA, 500, SERVER,
        body={**VALID, 'payment_id': _text}, effect=effect('owner', new={
            'payment': new_payment('bank', '50.50', True, '2026-10-20T10:00:00Z', '2026-10-20T10:00:00Z', 'uno')},
            audits=payment_audits(OWN, 'uno')))

# A multipart body with a file part is a mutable copy: the view reads it as a form (every value is text, never None).
FORM_VALID = [('custom_accounts', None, None, ACCOUNTS['bank']['custom_account_id']), ('supplier_id', None, None, SUPPLIERS['uno']['supplier_id']),
              ('payment_date', None, None, '2026-10-21T10:00:00Z'), ('paid', None, None, 'true'), ('amount', None, None, '7.25'),
              ('invoice_identifier', None, None, 'FM-300')]
mp, ct = multipart(*FORM_VALID, ('allegato', 'fattura.txt', 'text/plain', 'x'))
add('supplier-add-multipart-with-file', 'owner', 'POST', SA, 200, CREATED, raw_body=mp, content_type=ct, effect=effect('owner', new={
    'payment': new_payment('bank', '7.25', True, '2026-10-21T10:00:00Z', '2026-10-21T10:00:00Z', 'uno'),
    'invoicesuppliers': new_invoice('FM-300', '7.25', True, '2026-10-21T10:00:00Z', None, None, 'uno')},
    audits=payment_audits(OWN, 'uno') + invoice_audits('uno')))
mp, ct = multipart(*FORM_VALID[1:], ('allegato', 'fattura.txt', 'text/plain', 'x'))
add('supplier-add-multipart-with-file-missing-account-server-error', 'owner', 'POST', SA, 500, SERVER, raw_body=mp, content_type=ct)
mp, ct = multipart(*FORM_VALID, ('notes', 'nota.txt', 'text/plain', 'x'))
add('supplier-add-multipart-file-as-notes', 'owner', 'POST', SA, 400, {'notes': ['Not a valid string.']}, raw_body=mp, content_type=ct)
mp, ct = multipart(*[part for part in FORM_VALID if part[0] != 'custom_accounts'], ('custom_accounts', None, None, ''),
                   ('allegato', 'fattura.txt', 'text/plain', 'x'))
add('supplier-add-multipart-blank-account-server-error', 'owner', 'POST', SA, 500, SERVER, raw_body=mp, content_type=ct)

# ------------------------------------------------------- supplier update ---

SU = lambda label: f'/invoice-suppliers/{SUPPLIER_INVOICES[label]["invoice_supplier_id"] if label in SUPPLIER_INVOICES else label}/update'
INVOICE_FIELDS = ('invoice_identifier', 'amount', 'payment_date', 'expire_date', 'payment', 'paid', 'notes', 'document_pdf', 'supplier')
PAYMENT_FIELDS = ('paid', 'payment_date')
access('supplier-update', SU('c'), 'PATCH', 'supplierinvoices', 'update', 'Invoice Suppliers Update', 'POST')
add('supplier-update-malformed', 'owner', 'PATCH', SU('nope'), 400, BAD_UUID, body={})
add('supplier-update-athlete-server-error', 'athlete', 'PATCH', SU('c'), 500, SERVER, body={})
add('supplier-update-owner-without-association-server-error', 'owner_without_association', 'PATCH', SU('c'), 500, SERVER, body={})
add('supplier-update-unknown-server-error', 'owner', 'PATCH', SU(UNKNOWN), 500, SERVER, body={})
add('supplier-update-foreign-server-error', 'owner', 'PATCH', SU('abroad'), 500, SERVER, body={'paid': True})
add('supplier-update-field-errors', 'owner', 'PATCH', SU('a'), 400, {
    'amount': ['A valid number is required.'], 'invoice_identifier': ['This field may not be blank.'],
    'supplier': ['This field may not be null.'], 'paid': ['Must be a valid boolean.'], 'payment_date': [DATETIME],
    'payment_id': ['Not a valid string.'], 'invoice_supplier_id': ['Must be a valid UUID.']},
    body={'amount': 'x', 'invoice_identifier': '', 'supplier': None, 'paid': 'maybe', 'payment_date': 5, 'payment_id': False,
          'invoice_supplier_id': 'abc'})
add('supplier-update-foreign-association-forbidden', 'owner', 'PATCH', SU('a'), 403, FORBIDDEN, body={'sport_association': FOREIGN, 'amount': 'x'})
add('supplier-update-unknown-document', 'owner', 'PATCH', SU('a'), 400, {'document_pdf': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
                                                                       'payment': {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}},
    body={'document_pdf': UNKNOWN, 'payment': []})


def supplier_update(name, actor, label, status, response, body=None, *, invoice=None, payment=None, now=False, saved=True,
                    payment_saved=True, payment_derived=None, association=OWN, **extra):
    row = SUPPLIER_INVOICES[label]
    key = row['invoice_supplier_id']
    audits, rows = [], {}
    pairs = changed(SUPPLIER_INVOICES, label, **(invoice or {}))
    if not saved:
        raise ValueError('use supplier_update_audit_only')
    if pairs:
        audits.append(audit('invoicesuppliers', key, 1, pairs, association=association))
    rows['invoicesuppliers'] = {key: {f: SUPPLIER_INVOICES[label][f] for f in INVOICE_FIELDS}}
    if payment is not None:
        pay = next(k for k, v in PAYMENTS.items() if v['payment_id'] == row['payment'])
        pkey = PAYMENTS[pay]['payment_id']
        old = dict(PAYMENTS[pay])
        pay_pairs = {}
        if payment['paid_str'] != audit_str('paid', old['paid']):
            pay_pairs['paid'] = [audit_str('paid', old['paid']), payment['paid_str']]
        previous = old['payment_date']
        pay_pairs['payment_date'] = ['$before' if previous == '$now' else audit_str('payment_date', previous), '$now']
        # Fields Payment.save() derives (description), as [old, new] stored values.
        for field, value in (payment_derived or {}).items():
            pay_pairs[field] = [audit_str(field, old[field]), audit_str(field, value)]
        audits.append(audit('payment', pkey, 1, pay_pairs, association=association))
        if payment_saved:
            PAYMENTS[pay].update(paid=payment['paid'], payment_date='$now', **(payment_derived or {}))
        rows['payment'] = {pkey: {f: PAYMENTS[pay][f] for f in PAYMENT_FIELDS}}
    add(name, actor, 'PATCH', SU(label), status, response, body=body, effect=effect(actor, rows=rows, audits=audits), **extra)


supplier_update('supplier-update-without-payment', 'owner', 'c', 200, UPDATED,
                {'invoice_identifier': ' FC-003 bis ', 'amount': 2, 'notes': 'prima nota', 'expire_date': '2027-01-31T10:00:00+01:00',
                 'invoice_supplier_id': SUPPLIER_INVOICES['c']['invoice_supplier_id'], 'sport_association': OWN},
                invoice={'invoice_identifier': 'FC-003 bis', 'amount': '2.00', 'notes': 'prima nota', 'expire_date': '2027-01-31T09:00:00Z'})
supplier_update('supplier-update-same-values-no-audit', 'full_collaborator', 'c', 200, UPDATED, {'amount': '2.0', 'notes': 'prima nota'})
supplier_update('supplier-update-paid-updates-payment', 'owner', 'a', 200, UPDATED, {'paid': True},
                invoice={'paid': True}, payment={'paid': True, 'paid_str': 'True'})
supplier_update('supplier-update-collaborator-payment-text', 'full_collaborator', 'a', 200, UPDATED, {'paid': 'True', 'document_pdf': None},
                invoice={'document_pdf': None}, payment={'paid': True, 'paid_str': 'True'})
supplier_update('supplier-update-missing-paid-after-save-server-error', 'owner', 'a', 500, SERVER, {'notes': 'cambio'},
                invoice={'notes': 'cambio'})
supplier_update('supplier-update-rejected-payment-paid-server-error', 'owner', 'b', 500, SERVER, {'paid': 'no'},
                invoice={'paid': False}, payment={'paid': True, 'paid_str': 'no'}, payment_saved=False)
supplier_update('supplier-update-unlink-payment', 'owner', 'b', 200, UPDATED, {'payment': None, 'payment_id': None},
                invoice={'payment': None})
supplier_update('supplier-update-payment-description-derived', 'foreign_owner', 'd', 200, UPDATED, {'paid': True},
                invoice={'paid': True}, payment={'paid': True, 'paid_str': 'True'}, payment_derived={'description': 'Iscrizione'},
                association=FOREIGN)
supplier_update('supplier-update-payment-quota-meta-description', 'foreign_owner', 'e', 200, UPDATED, {'paid': True},
                invoice={'paid': True}, payment={'paid': True, 'paid_str': 'True'},
                payment_derived={'description': 'Saldo corso estivo'}, association=FOREIGN)
supplier_update('supplier-update-form', 'owner', 'c', 200, UPDATED, None, invoice={'invoice_identifier': 'Modulo', 'paid': True},
                raw_body='invoice_identifier=Modulo&paid=on', content_type=FORM)
# notes has no serializer max_length: the pre_save entry is written, then the
# 255-character column rejects the update.
key_c = SUPPLIER_INVOICES['c']['invoice_supplier_id']
add('supplier-update-long-notes-audited-server-error', 'owner', 'PATCH', SU('c'), 500, SERVER, body={'notes': 'n' * 256},
    effect=effect('owner', rows={'invoicesuppliers': {key_c: {f: SUPPLIER_INVOICES['c'][f] for f in INVOICE_FIELDS}}},
                  audits=[audit('invoicesuppliers', key_c, 1, {'notes': ['prima nota', 'n' * 256]})]))

# payment_id on update: the text is audited as sent, then '' is stored as NULL and the payment access raises; text that is
# not a UUID, or the UUID of no payment, fails the save after the entry.
for _name, _text in (('blank', ''), ('not-a-uuid', 'zzz'), ('unknown', UNKNOWN)):
    add(f'supplier-update-payment-id-{_name}-audited-server-error', 'owner', 'PATCH', SU('c'), 500, SERVER, body={'payment_id': _text},
        effect=effect('owner', rows={'invoicesuppliers': {key_c: {f: SUPPLIER_INVOICES['c'][f] for f in INVOICE_FIELDS}}},
                      audits=[audit('invoicesuppliers', key_c, 1, {'payment': ['None', _text]})]))
# The association's own payment in another spelling: linked, then saved with the raw paid value.
supplier_update('supplier-update-payment-id-links-payment', 'owner', 'c', 200, UPDATED,
                {'payment_id': PAYMENTS['b']['payment_id'].upper(), 'paid': False},
                invoice={'payment': PAYMENTS['b']['payment_id'], 'paid': False}, payment={'paid': False, 'paid_str': 'False'})
cases[-1]['effect']['audits'][0]['changes']['payment'][1] = PAYMENTS['b']['payment_id'].upper()


def hidden_payment_update(name, actor, label, body, association, **extra):
    """A linked payment the live, group-aware manager does not see: auditlog diffs the saved instance against None."""
    key, pkey = SUPPLIER_INVOICES[label]['invoice_supplier_id'], PAYMENTS[label]['payment_id']
    pairs = changed(SUPPLIER_INVOICES, label, paid=body['paid'])
    PAYMENTS[label].update(paid=body['paid'], payment_date='$now')
    add(name, actor, 'PATCH', SU(label), 200, UPDATED, body=body, effect=effect(actor, rows={
        'invoicesuppliers': {key: {f: SUPPLIER_INVOICES[label][f] for f in INVOICE_FIELDS}},
        'payment': {pkey: {f: PAYMENTS[label][f] for f in PAYMENT_FIELDS}}},
        audits=([audit('invoicesuppliers', key, 1, pairs, association=association)] if pairs else [])
        # The index does not find the row either: it resolves through the entry's changes.
        + [audit('payment', pkey, 1, {'$created': []}, association=association, path='changes_json.sport_association')]), **extra)


hidden_payment_update('supplier-update-deleted-payment-audited-whole', 'foreign_owner', 'g', {'paid': True}, FOREIGN)
hidden_payment_update('supplier-update-group-hides-payment-audited-whole', 'owner', 'a', {'paid': False}, OWN,
                      headers={'X-Group-Id': IDS['own_group']})
# Payment.save() without a description: a course payment takes its course label, a payment without a category takes the
# default category and its name, an account transfer raises after the invoice save.
supplier_update('supplier-update-payment-course-label-description', 'foreign_owner', 'h', 200, UPDATED, {'paid': True},
                invoice={'paid': True}, payment={'paid': True, 'paid_str': 'True'}, payment_derived={'description': 'Corso base, '},
                association=FOREIGN)
supplier_update('supplier-update-payment-without-category', 'foreign_owner', 'i', 200, UPDATED, {'paid': True},
                invoice={'paid': True}, payment={'paid': True, 'paid_str': 'True'},
                payment_derived={'payment_category': '$default_category', 'description': '$default_category_name'}, association=FOREIGN)
key_j = SUPPLIER_INVOICES['j']['invoice_supplier_id']
pairs_j = changed(SUPPLIER_INVOICES, 'j', paid=True)
add('supplier-update-payment-transfer-description-server-error', 'foreign_owner', 'PATCH', SU('j'), 500, SERVER, body={'paid': True},
    effect=effect('foreign_owner', rows={'invoicesuppliers': {key_j: {f: SUPPLIER_INVOICES['j'][f] for f in INVOICE_FIELDS}}},
                  audits=[audit('invoicesuppliers', key_j, 1, pairs_j, association=FOREIGN)]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Customer and supplier invoice list, add and update authored sequence',
                              'ids': IDS, 'documents': DOCUMENTS, 'suppliers': SUPPLIERS, 'accounts': ACCOUNTS, 'categories': CATEGORIES,
                              'associates': ASSOCIATES, 'instructors': INSTRUCTORS, 'invoices': INVOICES,
                              'payments': SEED_PAYMENTS, 'attachments': ATTACHMENTS, 'supplier_invoices': SEED_SUPPLIER_INVOICES,
                              'customer_invoices': SEED_CUSTOMER_INVOICES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
