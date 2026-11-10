"""Author the payment add, update and bulk add HTTP sequence (payment-writes.md).

All three are @api_view function views behind IsAuthenticated (registry:
payment/add ``bookeeping.payments.create``, payment/<uid>/update
``bookeeping.payments.update``; payment/bulk-add has no registry entry, so a
restricted collaborator gets the registry's no-mapping denial).

payment/add (POST): athletes 403 without a body; the association
(``scoped_queryset(...).get``: none is a 500); ``data['paid']`` is the user's
auto_paid_payment (the fixture owner's is False; a form body is an immutable
QueryDict and a non-dict body is not indexable: 500); ``data['creation_date']``
and ``data['payment_date']`` must be keys (500), a None or blank creation date
becomes now, a blank payment date None, and both are tried with
``strptime('%Y-%m-%d')`` (naive midnight) before PaymentEntrySerializer
validates them (ISO 8601). Errors answer 400 ``{'msg': f'validation errors:
{serializer.errors}'}`` (the repr of the ErrorDetail lists); a related object
of another association raises the tenant-scope 403 during validation.
``serializer.save()`` runs the save hook (a non-None
``meta_payment_categories`` makes amount ``float(initial amount)`` plus the
float of every meta amount), then ``Payment.objects.create`` (no user yet, so no
default account; the raw X-Group-Id is stored), Payment.save()'s category and
description defaults and the create audit entry (indexed through the actor's
association), then attachments_data (an item without document/filename keys
raises ValidationError: 400 with the payment kept). The view then sets user,
association and the complex_item party (Istruttori, Fornitori, Iscrizioni,
Persone: a failing lookup is a 500 after the create; a party of another
association is 403 'User not allowed.', the created payment kept) and saves again (audited
update, indexed directly). The response is PaymentSerializer of the in-memory
payment (a float amount quantized half-even). Paid payments (auto_paid_payment)
are not ported and not authored.

payment/<uid>/update (PATCH): is_valid_uuid (400), the association, the first
live group-aware payment by id (none or without association: 500; another
association: 403 'User not allowed.'), ``data.keys()`` (non-dict: 500), blank
dates dropped, PaymentEntrySerializer(partial) with raise_exception (400 JSON
errors), the parties (instructor and supplier always cleared; complex_item and
subscription_id), payment_intent_id cleared, the save hook, the full audited
save, then attachments_data. Response {'data': {'payment': ...}}.
An invoiced payment prints its invoice after the save. Form bodies are not
ported and not authored.

attachments_data (add and update, after the payment's save): for each item
with document and filename, b64decode(document) (invalid: 500, the payment and
earlier documents kept), Document.objects.create(filename=...) (audited create
indexed to the actor's own association; the second save changes nothing),
default_storage.save('<str(creation_date.timestamp())>/<id>/<filename>') of the
bytes and payment.attachments.add(document) (no audit entry); an item without
the keys raises ValidationError (400) after the earlier items are stored. The
response renders the new attachments (DocumentSerializer). The adapter owns a
private MinIO: each case's new objects must be exactly its authored documents.

payment/bulk-add (POST): athletes 403 without a body, the association, then
the view's own checks (400 messages), the live group-aware associates of the
association (unknown ids are reported by str(); nothing valid: 200), the
defaults computed by the view (the category among the association's own or
shared categories, the account among its own) and one atomic bulk_create (no save(), no
signals, no audit, no group). Run with ``python3 -I``.
"""
import base64
import copy
import json
from decimal import Context, Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path

OUTPUT = Path(__file__).with_name('payment_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{988001 + n:012d}' for n in range(300))
SERVER = '<stable-server-error-page>'
EMPTY = '<empty>'

CATEGORIES = {
    'own': {'id': next(ids), 'name': 'Quote fixture', 'association': OWN},
    'global': {'id': next(ids), 'name': 'Categoria globale', 'association': None},
    'foreign': {'id': next(ids), 'name': 'Categoria estera', 'association': FOREIGN},
    # Same name as Payment.save()'s default, owned by the other association and keyed
    # below every uuid4 key: the default stays the own or global row.
    'foreign_default': {'id': '00000000-0000-4000-8000-000000000501', 'name': 'Entrate e proventi da attività tipiche',
                        'association': FOREIGN},
}
ACCOUNTS = {
    'bank': {'id': next(ids), 'name': 'Banca fixture', 'type': 2, 'code': 'BNK', 'association': OWN, 'editable': False},
    'cash': {'id': next(ids), 'name': 'Cassa fixture', 'type': 1, 'code': 'CSH', 'association': OWN, 'editable': True},
    'foreign': {'id': next(ids), 'name': 'Banca estera', 'type': 2, 'code': 'FBK', 'association': FOREIGN, 'editable': False},
}
ASSOCIATES = {
    'mario': {'id': next(ids), 'first': 'Mario', 'last': 'Rossi', 'sex': 'M', 'tax': 'RSSMRA90E10H501Z', 'born': '1990-05-10',
              'email': 'mario@example.test', 'association': OWN, 'group': None, 'deleted': False},
    'luca': {'id': next(ids), 'first': 'Luca', 'last': 'Verdi è', 'sex': 'M', 'tax': 'VRDLCU91E10H501Z', 'born': '1991-05-10',
             'email': 'luca@example.test', 'association': OWN, 'group': None, 'deleted': False},
    'grouped': {'id': next(ids), 'first': 'Giulia', 'last': 'Neri', 'sex': 'F', 'tax': 'NREGLI92E50H501Z', 'born': '1992-05-10',
                'email': 'giulia@example.test', 'association': OWN, 'group': 'own_group', 'deleted': False},
    'removed': {'id': next(ids), 'first': 'Paolo', 'last': 'Bianchi', 'sex': 'M', 'tax': 'BNCPLA93E10H501Z', 'born': '1993-05-10',
                'email': 'paolo@example.test', 'association': OWN, 'group': None, 'deleted': True},
    'abroad': {'id': next(ids), 'first': 'Hans', 'last': 'Muller', 'sex': 'M', 'tax': 'MLLHNS94E10Z112Z', 'born': '1994-05-10',
               'email': 'hans@example.test', 'association': FOREIGN, 'group': None, 'deleted': False},
}
SUBSCRIPTIONS = {
    'mario': {'id': next(ids), 'associate': 'mario', 'association': OWN, 'created': '2026-08-01T10:00:00Z'},
}
INSTRUCTOR = {'id': next(ids), 'first_name': 'Anna è', 'last_name': 'Bianchi', 'email': 'anna@example.test', 'tax_code': 'BNCNNA80A41H501X',
              'default_hourly_billing': '12.50'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore è', 'vat_number': '01234567890', 'email': 'fornitore@example.test'}
# Parties of the other association (payment parties only apply to the caller's association).
FOREIGN_PARTIES = {
    'subscription': {'id': 'a1000000-0000-4000-8000-000000988301', 'associate': 'abroad', 'association': FOREIGN, 'created': '2026-08-01T10:00:00Z'},
    'instructor': {'id': 'a1000000-0000-4000-8000-000000988302', 'user': IDS['foreign_owner'], 'first_name': 'Klaus', 'last_name': 'Weber'},
    'supplier': {'id': 'a1000000-0000-4000-8000-000000988303', 'name': 'Fornitore estero', 'vat_number': '09999999999',
                 'email': 'fornitore-estero@example.test', 'association': FOREIGN},
}
OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}

COLUMNS = ('type', 'subject', 'amount', 'creation_date', 'payment_date', 'paid', 'expense', 'description', 'payment_category_id',
           'meta_payment_categories', 'invoice_id', 'user_id', 'associate_id', 'supplier_id', 'instructor_id', 'sport_association_id',
           'payment_intent_id', 'archived', 'invoice_fiscal_code', 'meta', 'notes', 'custom_accounts_id', 'signature_id', 'deleted',
           'group_id', 'course')
FOREIGN_KEYS = {'payment_category_id', 'invoice_id', 'user_id', 'associate_id', 'supplier_id', 'instructor_id', 'sport_association_id',
                'custom_accounts_id', 'signature_id', 'group_id'}
TIMES = {'creation_date', 'payment_date'}
JSONS = {'meta_payment_categories', 'meta', 'course'}
BOOLS = {'paid', 'expense', 'archived', 'deleted'}


def row(**values):
    """A payment row (snapshot columns, payment_id excluded)."""
    base = {'type': 'default', 'subject': 0, 'amount': '0.00', 'creation_date': None, 'payment_date': None, 'paid': False, 'expense': False,
            'description': None, 'payment_category_id': None, 'meta_payment_categories': None, 'invoice_id': None, 'user_id': None,
            'associate_id': None, 'supplier_id': None, 'instructor_id': None, 'sport_association_id': None, 'payment_intent_id': None,
            'archived': False, 'invoice_fiscal_code': None, 'meta': None, 'notes': None, 'custom_accounts_id': None, 'signature_id': None,
            'deleted': False, 'group_id': None, 'course': None}
    unknown = set(values) - set(base)
    assert not unknown, unknown
    return {**base, **values}


def payment(**values):
    seeded = row(**{'user_id': OWNER, 'sport_association_id': OWN, 'creation_date': '2026-10-01T08:00:00Z', 'amount': '10.00',
                    'description': 'Quota', 'payment_category_id': CATEGORIES['own']['id'], **values})
    return {'id': next(ids), **seeded}


# An invoice of two updated payments: the update prints it (print_document_invoice.apply) after the save.
INVOICE = {'id': next(ids), 'number': 41, 'created': '2026-09-01T08:00:00Z', 'membership_fee': '0.00', 'activity_fee': '10.00',
           'description': 'Ricevuta fixture'}

PAYMENTS = {
    'up_plain': payment(supplier_id=SUPPLIER['id'], payment_intent_id='pi_fixture', notes='Nota'),
    'up_fields': payment(subject=2, instructor_id=INSTRUCTOR['id'], course={'label': 'Corso'}),
    'up_meta': payment(amount='5.00'),
    'up_defaults': payment(payment_category_id=None),
    'up_party': payment(),
    'up_subscription': payment(supplier_id=SUPPLIER['id']),
    'up_unknown_group': payment(associate_id=ASSOCIATES['mario']['id']),
    'up_attachment': payment(),
    'up_grouped': payment(group_id=IDS['own_group']),
    'up_collab': payment(),
    'up_admin': payment(),
    'up_transfer': payment(subject=3),
    'up_removed': payment(deleted=True),
    'up_orphan': payment(sport_association_id=None),
    'abroad': payment(sport_association_id=FOREIGN, user_id=IDS['foreign_owner'], payment_category_id=CATEGORIES['foreign']['id']),
    # The edit form posts its hidden payment_id input: the row's own key, unchanged by the save.
    'up_own_id': payment(),
    'up_own_upper': payment(),
    'up_invoiced': payment(invoice_id=INVOICE['id']),
    'up_invoiced_print_fails': payment(invoice_id=INVOICE['id']),
    'up_attach_one': payment(),
    'up_attach_partial': payment(),
    'up_attach_bad64': payment(),
}
STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=()):
    """prints: the CURRENT_HOST requests the case makes, each with the stub's reply."""
    case = {'name': f'payment-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'prints': list(prints)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# --- Python renderings -----------------------------------------------------

def naive(text):
    """auditlog's str() of a DateTimeField: naive UTC."""
    if text in ('$now',):
        return text
    return text.replace('T', ' ').replace('Z', '')


def audit_value(column, value):
    """get_field_value of one column (None means the value is None)."""
    if value is None:
        if column in FOREIGN_KEYS or column in TIMES:
            return None
        if column in JSONS:
            return 'null'
        return 'None'
    if column in TIMES:
        return naive(value)
    if column in JSONS:
        return json.dumps(value, sort_keys=True)
    if column in BOOLS:
        return str(value)
    return str(value)


def field(column):
    return column[:-3] if column in FOREIGN_KEYS else column


def created_changes(values, amount=None):
    """auditlog's create changes of the in-memory payment (amount: its str() when not the stored text)."""
    changes = {'payment_id': ['None', '$id']}
    for column in COLUMNS:
        value = audit_value(column, values[column])
        if column == 'amount' and amount is not None:
            value = amount
        if value == 'None' and column not in FOREIGN_KEYS:
            continue
        changes[field(column)] = ['None', 'None' if value is None else value]
    return changes


def updated_changes(old, new, amount=None, old_amount=None):
    """auditlog's update changes: stored row against the in-memory payment."""
    changes = {}
    for column in COLUMNS:
        before, after = audit_value(column, old[column]), audit_value(column, new[column])
        if column == 'amount':
            before = old_amount if old_amount is not None else before
            after = amount if amount is not None else after
        if before != after:
            changes[field(column)] = ['None' if before is None else before, 'None' if after is None else after]
    return changes


def float_amount(value):
    """(stored text, response text) of a float amount: Context(prec=9).create_decimal_from_float then
    numeric(9, 2) rounding (half away from zero); DRF quantizes str(float) half-even."""
    stored = Context(prec=9).create_decimal_from_float(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    shown = Decimal(repr(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
    return str(stored), str(shown)


# --- PaymentSerializer -----------------------------------------------------

def associate_item(label):
    person = ASSOCIATES[label]
    return {'associate_id': person['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': person['born'],
            'email': person['email'], 'sex': person['sex'], 'first_name': person['first'], 'last_name': person['last'], 'is_minor': False,
            'sport_association': person['association'], 'tax_code': person['tax'], 'user': OWNER, 'phone': None, 'family': None,
            'family_role': None}


def instructor_item():
    row_ = INSTRUCTOR
    return {'instructor_id': row_['id'], 'user': OWNER, 'first_name': row_['first_name'], 'last_name': row_['last_name'], 'email': row_['email'],
            'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row_['tax_code'], 'address_city': None, 'address': None,
            'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore', 'phone': None, 'phone_2': None,
            'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None, 'is_volunteer': False, 'stipulated_contract_in': None,
            'default_hourly_billing': row_['default_hourly_billing'], 'default_percentage_billing': '0.00', 'documents': []}


def supplier_item():
    row_ = SUPPLIER
    return {'supplier_id': row_['id'], 'name': row_['name'], 'address': None, 'tax_code': None, 'sport_association': OWN,
            'vat_number': row_['vat_number'], 'type': 'supplier', 'email': row_['email'], 'phone_number': None, 'city': None, 'cap': None,
            'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}


ASSOCIATE_LABELS = {person['id']: label for label, person in ASSOCIATES.items()}
CATEGORY_NAMES = {row_['id']: row_['name'] for row_ in CATEGORIES.values()}
CATEGORY_NAMES['$default_category'] = '$default_category_name'
ACCOUNT_ROWS = {row_['id']: row_ for row_ in ACCOUNTS.values()}


def payment_item(values, key, amount=None):
    """PaymentSerializer of a payment (amount: the rendered text when it differs from the stored one)."""
    associate = values['associate_id']
    subscription = None
    if values['subject'] == 0 and associate == ASSOCIATES['mario']['id']:
        # get_subscription_id: the associate's oldest live subscription.
        subscription = SUBSCRIPTIONS['mario']['id']
    account = ACCOUNT_ROWS.get(values['custom_accounts_id'])
    category = values['payment_category_id']
    return {'payment_id': key, 'user': OWNER_USER if values['user_id'] == OWNER else None,
            'associate': associate_item(ASSOCIATE_LABELS[associate]) if associate else None, 'subscription_id': subscription,
            'instructor_id': values['instructor_id'], 'instructor': instructor_item() if values['instructor_id'] else None,
            'supplier': supplier_item() if values['supplier_id'] else None, 'supplier_id': values['supplier_id'],
            'custom_account_type': account['type'] if account else None, 'custom_account_name': account['name'] if account else None,
            'invoice': invoice_item() if values['invoice_id'] else None, 'attachments': [], 'payment_category_name': CATEGORY_NAMES.get(category) if category else None,
            'type': values['type'], 'subject': values['subject'], 'amount': amount or values['amount'], 'creation_date': values['creation_date'],
            'payment_date': values['payment_date'], 'paid': values['paid'], 'expense': values['expense'], 'description': values['description'],
            'meta_payment_categories': values['meta_payment_categories'], 'payment_intent_id': values['payment_intent_id'],
            'archived': values['archived'], 'invoice_fiscal_code': values['invoice_fiscal_code'], 'meta': values['meta'], 'notes': values['notes'],
            'deleted': values['deleted'], 'course': values['course'], 'payment_category': category,
            'sport_association': values['sport_association_id'], 'custom_accounts': values['custom_accounts_id'], 'signature': None,
            'group': values['group_id']}


def invoice_item():
    """InvoiceSerializer of the fixture invoice (no PDF, no attached documents)."""
    return {'invoice_id': INVOICE['id'], 'document_token': None, 'creation_date': INVOICE['created'],
            'membership_fee': INVOICE['membership_fee'], 'activity_fee': INVOICE['activity_fee'], 'meta_payment_categories': None,
            'description': INVOICE['description'], 'number': INVOICE['number'], 'archived': False, 'cancelled': False, 'meta': None,
            'imported_from_associami': False, 'deleted': False, 'document_pdf': None, 'sport_association': OWN, 'selected_tutor': None,
            'group': None, 'attached_documents': []}


# --- effects ---------------------------------------------------------------

def effect(actor, *, created=(), changed=None, audit=()):
    return {'actor': actor, 'created': list(created), 'changed': changed or {}, 'audit': list(audit)}


def event(key, action, changes, index):
    return {'key': key, 'action': action, 'changes': changes, 'index': index}


def actor_index(actor):
    """A create entry without an association resolves through the actor's own association."""
    return {'owner': [OWN, 'actor.sport_association'], 'admin': [IDS['admin_association'], 'actor.sport_association']}.get(actor)


DIRECT = [OWN, 'direct']
DENIED = {'detail': 'User not allowed.'}
TENANT = {'detail': 'You do not have permission to perform this action.'}
MALFORMED = ['badly formed hexadecimal UUID string']
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
GROUP = {'X-Group-Id': IDS['own_group']}
OTHER_GROUP = {'X-Group-Id': IDS['foreign_group']}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
ADD, BULK = '/payment/add', '/payment/bulk-add'
UPDATE = lambda label: f'/payment/{PAYMENTS[label]["id"] if label in PAYMENTS else label}/update'
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name, description: {'name': name, 'description': description, 'renders': ['application/json'], 'parses': PARSES}
A = lambda label: ASSOCIATES[label]['id']
DOC = 'API endpoint to add a new payment'


def guards(prefix, path, verb, denial, name, description):
    add(f'{prefix}-anonymous', 'anonymous', path, 401, ANONYMOUS, method=verb)
    add(f'{prefix}-restricted-denied', 'restricted_collaborator', path, 403, denial, method=verb)
    add(f'{prefix}-admin-without-session', 'admin', path, 403, ADMIN, method=verb)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', path, 403, SCOPED_DENIED, method=verb, headers=SCOPED)
    add(f'{prefix}-wrong-method', 'owner', path, 405, method('GET'), method='GET')
    add(f'{prefix}-options', 'owner', path, 200, options(name, description), method='OPTIONS')


def errors_repr(fields):
    """f'validation errors: {serializer.errors}' for [(field, [(message, code)] or {index: [(message, code)]})]."""
    def details(items):
        return '[' + ', '.join(f'ErrorDetail(string={message!r}, code={code!r})' for message, code in items) + ']'
    parts = []
    for name, items in fields:
        if isinstance(items, dict):
            inner = ', '.join(f'{index!r}: {details(value)}' for index, value in items.items())
            parts.append(f'{name!r}: {{{inner}}}')
        else:
            parts.append(f'{name!r}: {details(items)}')
    return {'msg': 'validation errors: {' + ', '.join(parts) + '}'}


WRONG_DATE = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'

# --- payment/add -------------------------------------------------------------

guards('add', ADD, 'POST', {'detail': 'Missing permission: bookeeping.payments.create'}, 'Payment Add', DOC)
add('add-athlete-forbidden', 'athlete', ADD, 403, EMPTY, body={'creation_date': None, 'payment_date': None, 'complex_item': None})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER,
    body={'creation_date': None, 'payment_date': None, 'complex_item': None})
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=['creation_date'])
add('add-form-body-server-error', 'owner', ADD, 500, SERVER, raw_body='creation_date=2026-03-04&payment_date=&complex_item=',
    content_type='application/x-www-form-urlencoded')
add('add-without-creation-date-server-error', 'owner', ADD, 500, SERVER, body={'payment_date': None, 'complex_item': None})
add('add-without-payment-date-server-error', 'owner', ADD, 500, SERVER, body={'creation_date': None, 'complex_item': None})
add('add-validation-errors', 'owner', ADD, 400, errors_repr([
    ('type', [('"card" is not a valid choice.', 'invalid_choice')]),
    ('subject', [('"9" is not a valid choice.', 'invalid_choice')]),
    ('amount', [('A valid number is required.', 'invalid')]),
    ('description', [('This field may not be blank.', 'blank')]),
    ('payment_date', [(WRONG_DATE, 'invalid')]),
    ('creation_date', [(WRONG_DATE, 'invalid')]),
]), body={'creation_date': '2026-13-01', 'payment_date': '05/03/2026', 'amount': 'abc', 'type': 'card', 'subject': 9, 'description': '   ',
          'paid': True, 'complex_item': None}, effect=effect('owner'))
add('add-amount-decimal-places', 'owner', ADD, 400, errors_repr([('amount', [('Ensure that there are no more than 2 decimal places.',
                                                                              'max_decimal_places')])]),
    body={'creation_date': '2026-03-04', 'payment_date': None, 'amount': '10.125', 'complex_item': None}, effect=effect('owner'))
add('add-amount-whole-digits', 'owner', ADD, 400, errors_repr([('amount', [('Ensure that there are no more than 7 digits before the decimal point.',
                                                                            'max_whole_digits')])]),
    body={'creation_date': '2026-03-04', 'payment_date': None, 'amount': 12345678, 'complex_item': None}, effect=effect('owner'))
add('add-related-errors', 'owner', ADD, 400, errors_repr([
    ('expense', [('Must be a valid boolean.', 'invalid')]),
    ('payment_category', [(f'Invalid pk "{IDS["missing"]}" - object does not exist.', 'does_not_exist')]),
    ('sport_association', [('Incorrect type. Expected pk value, received bool.', 'incorrect_type')]),
    ('custom_accounts', [('“abc” is not a valid UUID.', 'invalid')]),
    ('notes', [('Not a valid string.', 'invalid')]),
    ('attachments_data', [('Expected a list of items but got type "str".', 'not_a_list')]),
]), body={'creation_date': None, 'payment_date': None, 'expense': 'maybe', 'payment_category': IDS['missing'], 'sport_association': True,
          'custom_accounts': 'abc', 'notes': ['x'], 'attachments_data': 'file', 'complex_item': None}, effect=effect('owner'))
add('add-attachment-item-error', 'owner', ADD, 400, errors_repr([
    ('attachments_data', {1: [('Expected a dictionary of items but got type "str".', 'not_a_dict')]}),
]), body={'creation_date': None, 'payment_date': None, 'attachments_data': [{'document': 'x'}, 'file'], 'complex_item': None},
    effect=effect('owner'))
add('add-foreign-category-denied', 'owner', ADD, 403, TENANT,
    body={'creation_date': None, 'payment_date': None, 'amount': 'abc', 'payment_category': CATEGORIES['foreign']['id'], 'complex_item': None},
    effect=effect('owner'))
add('add-foreign-account-denied', 'owner', ADD, 403, TENANT,
    body={'creation_date': None, 'payment_date': None, 'custom_accounts': ACCOUNTS['foreign']['id'], 'complex_item': None},
    effect=effect('owner'))
add('add-foreign-association-denied', 'owner', ADD, 403, TENANT,
    body={'creation_date': None, 'payment_date': None, 'sport_association': FOREIGN, 'complex_item': None}, effect=effect('owner'))
add('add-negative-amount-server-error', 'owner', ADD, 500, SERVER,
    body={'creation_date': None, 'payment_date': None, 'amount': '-5', 'description': 'Rimborso', 'complex_item': None}, effect=effect('owner'))
add('add-transfer-without-description-server-error', 'owner', ADD, 500, SERVER,
    body={'creation_date': None, 'payment_date': None, 'subject': 3, 'custom_accounts': ACCOUNTS['bank']['id'], 'complex_item': None},
    effect=effect('owner'))
add('add-meta-without-amount-server-error', 'owner', ADD, 500, SERVER,
    body={'creation_date': None, 'payment_date': None, 'meta_payment_categories': [], 'complex_item': None}, effect=effect('owner'))
add('add-meta-item-without-amount-server-error', 'owner', ADD, 500, SERVER,
    body={'creation_date': None, 'payment_date': None, 'amount': 5, 'meta_payment_categories': [{'title': 'x'}], 'complex_item': None},
    effect=effect('owner'))
add('add-invalid-group-header-server-error', 'owner', ADD, 500, SERVER, headers={'X-Group-Id': 'abc'},
    body={'creation_date': None, 'payment_date': None, 'complex_item': None}, effect=effect('owner'))
add('add-unknown-group-header-server-error', 'owner', ADD, 500, SERVER, headers={'X-Group-Id': IDS['missing']},
    body={'creation_date': None, 'payment_date': None, 'complex_item': None}, effect=effect('owner'))


def attachment_docs(specs, actor, payment):
    """The Document creates (audit specs), the effect's stored documents and the rendered attachments."""
    audit, stored, rendered = [], [], []
    for index, (filename, body) in enumerate(specs):
        key = f'$doc{index}'
        audit.append({'key': key, 'model': 'document', 'action': 0, 'index': actor_index(actor),
                      'changes': {'document_id': ['None', key], 'creation_date': ['None', key + '_created'], 'filename': ['None', filename],
                                  'token': ['None', key + '_token'], 'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}})
        stored.append({'filename': filename, 'body_b64': base64.b64encode(body).decode(), 'payment': payment})
        rendered.append({'document_id': f'$bind:doc{index}', 'creation_date': '$now', 'filepath': None, 'filename': filename,
                         'file_size_bytes': None, 'token': f'$bind:doc{index}_token'})
    return audit, stored, rendered


def sent_group(audit, response, stored, sent):
    """X-Group-Id in another spelling: the column stores the uuid, the in-memory payment keeps the text as sent, so
    the create entry and the response carry the text and every later save records [stored uuid, sent text]."""
    for entry in audit:
        if entry.get('model') == 'invoice':
            continue
        entry['changes']['group'] = ['None' if entry['action'] == 0 else stored, sent]
    response['group'] = sent


def added(name, actor, body, values, *, amount=None, headers=None, status=201, response=None, party=None, finish=True, documents=(),
          group_sent=None):
    """A created payment: the create entry from serializer.save(), then (finish) the view's audited save of
    user, association and party. amount: (str(float), stored, rendered) for a float amount."""
    stored_amount, shown = (amount[1], amount[2]) if amount else (None, None)
    values = dict(values)
    from_body = values.pop('sport_association_from_body', False)
    created = row(**{**values, 'amount': stored_amount or values.get('amount', '0.00')})
    first = dict(created)
    for column in ('user_id', 'sport_association_id', 'associate_id', 'instructor_id', 'supplier_id'):
        if not (column == 'sport_association_id' and from_body):
            first[column] = None
    floated = amount[0] if amount else None
    audit = [event('$id', 0, created_changes(first, floated),
                   DIRECT if first['sport_association_id'] else actor_index(actor))]
    document_audit, stored_documents, rendered = attachment_docs(documents, actor, '$id')
    audit.extend(document_audit)
    final = first
    if finish:
        final = created
        # auditlog's update entry is written in pre_save: the index resolver reads the row before the save,
        # so an unassociated payment resolves through the changes' sport_association.
        index = DIRECT if first['sport_association_id'] else [OWN, 'changes_json.sport_association']
        audit.append(event('$id', 1, updated_changes(first, created, floated, stored_amount), index))
    if response is None:
        response = {**payment_item(final, '$bind:payment', shown), 'attachments': rendered}
    if group_sent:
        sent_group(audit, response, final['group_id'], group_sent)
    add(name, actor, ADD, status, response, body=body, headers=headers,
        effect={**effect(actor, created=[final], audit=audit), 'documents': stored_documents})


DEFAULT = '$default_category'
plain = {'user_id': OWNER, 'sport_association_id': OWN, 'payment_category_id': DEFAULT}
added('add-minimal', 'owner', {'creation_date': '2026-03-04', 'payment_date': '', 'amount': '25.5', 'description': ' Quota fixture ',
                               'complex_item': None, 'generate_invoice': True},
      {**plain, 'creation_date': '2026-03-04T00:00:00Z', 'amount': '25.50', 'description': 'Quota fixture'})
added('add-now-and-default-amount', 'owner', {'creation_date': '', 'payment_date': None, 'complex_item': {}},
      {**plain, 'creation_date': '$now', 'description': '$default_category_name'}, amount=('0.0', '0.00', '0.00'))
added('add-every-field', 'owner', {
    'creation_date': '2026-03-05T10:20:30.123456+02:00', 'payment_date': '2026-3-6', 'type': 'cash', 'subject': '1', 'amount': 40,
    'expense': 'yes', 'paid': True, 'payment_category': CATEGORIES['own']['id'], 'custom_accounts': ACCOUNTS['cash']['id'],
    'notes': '  Nota è  ', 'course': {'value': 3, 'label': 'Corso X'}, 'description': None, 'attachments_data': [], 'attachments': ['x'],
    'complex_item': {'group': 'Persone', 'associate_id': A('mario')}},
    {**plain, 'creation_date': '2026-03-05T08:20:30.123456Z', 'payment_date': '2026-03-06T00:00:00Z', 'type': 'cash', 'subject': 1,
     'amount': '40.00', 'expense': True, 'payment_category_id': CATEGORIES['own']['id'], 'custom_accounts_id': ACCOUNTS['cash']['id'],
     'notes': 'Nota è', 'course': {'value': 3, 'label': 'Corso X'}, 'description': 'Iscrizione', 'associate_id': A('mario')})
# strptime's \d (the year and the day's second digit) matches decimal digits of any script; a month written with
# them is left to the serializer, which rejects it.
added('add-other-script-dates', 'owner', {'creation_date': '\uff12\u0660\uff12\u0666-03-1\u0664', 'payment_date': '2026-03-2\u096b',
                                          'amount': '6', 'description': 'Cifre', 'complex_item': None},
      {**plain, 'creation_date': '2026-03-14T00:00:00Z', 'payment_date': '2026-03-25T00:00:00Z', 'amount': '6.00', 'description': 'Cifre'})
added('add-course-label-description', 'owner', {'creation_date': '2026-03-07', 'payment_date': None, 'subject': 2, 'amount': '30.00',
                                                'course': {'label': 'Corso Y'}, 'payment_category': CATEGORIES['global']['id'],
                                                'complex_item': None},
      {**plain, 'creation_date': '2026-03-07T00:00:00Z', 'subject': 2, 'amount': '30.00', 'course': {'label': 'Corso Y'},
       'description': 'Corso Y, ', 'payment_category_id': CATEGORIES['global']['id']})
added('add-quota-corso-kept', 'owner', {'creation_date': '2026-03-08', 'payment_date': None, 'subject': 2, 'description': 'QUOTA CORSO',
                                        'amount': '1.00', 'complex_item': 0},
      {**plain, 'creation_date': '2026-03-08T00:00:00Z', 'subject': 2, 'amount': '1.00', 'description': 'QUOTA CORSO'})
total = float('10.10') + (0 + float('0.2') + float(5))
added('add-meta-float-amount', 'owner', {'creation_date': '2026-03-09', 'payment_date': None, 'amount': '10.10', 'description': 'Meta',
                                         'meta_payment_categories': [{'amount': '0.2', 'title': 'è'}, {'amount': 5, 'title': 'b'}],
                                         'complex_item': None},
      {**plain, 'creation_date': '2026-03-09T00:00:00Z', 'description': 'Meta',
       'meta_payment_categories': [{'amount': '0.2', 'title': 'è'}, {'amount': 5, 'title': 'b'}]},
      amount=(repr(total), *float_amount(total)))
tie = float('0.12') + (0 + 0.005)
added('add-meta-half-even-response', 'owner', {'creation_date': '2026-03-10', 'payment_date': None, 'amount': '0.12', 'description': 'Meta',
                                               'meta_payment_categories': [{'amount': 0.005}], 'complex_item': None},
      {**plain, 'creation_date': '2026-03-10T00:00:00Z', 'description': 'Meta', 'meta_payment_categories': [{'amount': 0.005}]},
      amount=(repr(tie), *float_amount(tie)))
added('add-instructor', 'owner', {'creation_date': '2026-03-11', 'payment_date': '2026-03-12', 'amount': '50', 'expense': True,
                                  'description': 'Compenso', 'complex_item': {'group': 'Istruttori', 'instructor_id': INSTRUCTOR['id']}},
      {**plain, 'creation_date': '2026-03-11T00:00:00Z', 'payment_date': '2026-03-12T00:00:00Z', 'amount': '50.00', 'expense': True,
       'description': 'Compenso', 'instructor_id': INSTRUCTOR['id']})
added('add-supplier', 'owner', {'creation_date': '2026-03-13', 'payment_date': None, 'amount': '60', 'expense': True, 'description': 'Spesa',
                                'complex_item': {'group': 'Fornitori', 'supplier_id': SUPPLIER['id']}},
      {**plain, 'creation_date': '2026-03-13T00:00:00Z', 'amount': '60.00', 'expense': True, 'description': 'Spesa',
       'supplier_id': SUPPLIER['id']})
added('add-subscription-associate', 'owner', {'creation_date': '2026-03-14', 'payment_date': None, 'amount': '70', 'description': 'Iscritto',
                                              'complex_item': {'group': 'Iscrizioni', 'subscription_id': SUBSCRIPTIONS['mario']['id']}},
      {**plain, 'creation_date': '2026-03-14T00:00:00Z', 'amount': '70.00', 'description': 'Iscritto', 'associate_id': A('mario')})
added('add-unknown-party-group', 'owner', {'creation_date': '2026-03-15', 'payment_date': None, 'amount': '1', 'description': 'Altro',
                                           'complex_item': {'group': 'Altro', 'associate_id': A('mario')}},
      {**plain, 'creation_date': '2026-03-15T00:00:00Z', 'amount': '1.00', 'description': 'Altro'})
added('add-own-association-in-body', 'owner', {'creation_date': '2026-03-16', 'payment_date': None, 'amount': '2', 'description': 'Diretto',
                                               'sport_association': OWN, 'complex_item': None},
      {**plain, 'creation_date': '2026-03-16T00:00:00Z', 'amount': '2.00', 'description': 'Diretto', 'sport_association_from_body': True})
added('add-group', 'owner', {'creation_date': '2026-03-17', 'payment_date': None, 'amount': '3', 'description': 'Gruppo',
                             'complex_item': {'group': 'Persone', 'associate_id': A('grouped')}},
      {**plain, 'creation_date': '2026-03-17T00:00:00Z', 'amount': '3.00', 'description': 'Gruppo', 'associate_id': A('grouped'),
       'group_id': IDS['own_group']}, headers=GROUP)
UPPER_GROUP = {'X-Group-Id': IDS['own_group'].upper()}
added('add-group-upper-case', 'owner', {'creation_date': '2026-03-17', 'payment_date': None, 'amount': '3', 'description': 'Gruppo maiuscolo',
                                        'complex_item': {'group': 'Persone', 'associate_id': A('grouped')}},
      {**plain, 'creation_date': '2026-03-17T00:00:00Z', 'amount': '3.00', 'description': 'Gruppo maiuscolo', 'associate_id': A('grouped'),
       'group_id': IDS['own_group']}, headers=UPPER_GROUP, group_sent=IDS['own_group'].upper())
added('add-full-collaborator', 'full_collaborator', {'creation_date': '2026-03-18', 'payment_date': None, 'amount': '4',
                                                     'description': 'Collaboratore', 'complex_item': None},
      {**plain, 'creation_date': '2026-03-18T00:00:00Z', 'amount': '4.00', 'description': 'Collaboratore'})
added('add-admin-impersonating-owner', 'admin', {'creation_date': '2026-03-19', 'payment_date': None, 'amount': '5',
                                                 'description': 'Amministratore', 'complex_item': None},
      {**plain, 'creation_date': '2026-03-19T00:00:00Z', 'amount': '5.00', 'description': 'Amministratore'}, headers=ADMIN_OWNER)
# Failures after serializer.save(): the created payment (no user, association or party) stays.
unfinished = {'payment_category_id': DEFAULT}
added('add-missing-associate-server-error', 'owner', {'creation_date': '2026-03-20', 'payment_date': None, 'amount': '6', 'description': 'Persa',
                                                      'complex_item': {'group': 'Persone', 'associate_id': IDS['missing']}},
      {**unfinished, 'creation_date': '2026-03-20T00:00:00Z', 'amount': '6.00', 'description': 'Persa'}, status=500, response=SERVER,
      finish=False)
added('add-deleted-associate-server-error', 'owner', {'creation_date': '2026-03-21', 'payment_date': None, 'amount': '7', 'description': 'Persa',
                                                      'complex_item': {'group': 'Persone', 'associate_id': A('removed')}},
      {**unfinished, 'creation_date': '2026-03-21T00:00:00Z', 'amount': '7.00', 'description': 'Persa'}, status=500, response=SERVER,
      finish=False)
added('add-party-without-group-server-error', 'owner', {'creation_date': '2026-03-22', 'payment_date': None, 'amount': '8', 'description': 'Persa',
                                                        'complex_item': {'associate_id': A('mario')}},
      {**unfinished, 'creation_date': '2026-03-22T00:00:00Z', 'amount': '8.00', 'description': 'Persa'}, status=500, response=SERVER,
      finish=False)
added('add-without-complex-item-server-error', 'owner', {'creation_date': '2026-03-23', 'payment_date': None, 'amount': '9',
                                                         'description': 'Persa'},
      {**unfinished, 'creation_date': '2026-03-23T00:00:00Z', 'amount': '9.00', 'description': 'Persa'}, status=500, response=SERVER,
      finish=False)
# A party of another association: 403 after serializer.save() (the created payment stays, like a failing lookup).
for group, key, party in [('Persone', 'associate_id', A('abroad')), ('Iscrizioni', 'subscription_id', FOREIGN_PARTIES['subscription']['id']),
                          ('Istruttori', 'instructor_id', FOREIGN_PARTIES['instructor']['id']),
                          ('Fornitori', 'supplier_id', FOREIGN_PARTIES['supplier']['id'])]:
    added(f'add-foreign-party-{group.lower()}-denied', 'owner', {'creation_date': '2026-03-25', 'payment_date': None, 'amount': '10',
                                                               'description': 'Estero', 'complex_item': {'group': group, key: party}},
          {**unfinished, 'creation_date': '2026-03-25T00:00:00Z', 'amount': '10.00', 'description': 'Estero'}, status=403, response=DENIED,
          finish=False)
added('add-attachment-without-keys', 'owner', {'creation_date': '2026-03-24', 'payment_date': None, 'amount': '11', 'description': 'Allegato',
                                               'attachments_data': [{'name': 'ricevuta.pdf'}], 'complex_item': None},
      {**unfinished, 'creation_date': '2026-03-24T00:00:00Z', 'amount': '11.00', 'description': 'Allegato'}, status=400,
      response={'exception': 'document key not present'}, finish=False)

# --- payment/add with auto_paid_payment ------------------------------------------
# The owner's auto_paid_payment (set by the runner only around the request, with the case's other 'owner_settings')
# makes data['paid'] True and a missing payment date now. A paid income payment with a party then runs the atomic
# invoice block: the fees from the in-memory amount, the next number by creation date
# (InvoiceService.get_next_invoice_number, use_payment_date=False), the description; without a supplier the invoice is
# created (saved again with the payment date when payment_date_equal_invoice_date), printed eagerly
# (print_document_invoice.apply) and the payment saved once more with it.

DENOMINATION = 'Message reads own'
AUTO_PAID = {'auto_paid_payment': True}
PRINT_NEW = {'path': '/document/invoice/$invoice?send_receipt_email=False', 'reply': {'status': 200, 'body': 'printed'}}
ZERO = ('0.00', '0.0')


def escape(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')


def other_text(reason):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(DENOMINATION)}</b>."


def recipient_text(recipient, reason):
    return (f"Ricevuta di pagamento di <b>{escape(recipient)}</b> con causale <b>{escape(reason)}</b> per l'associazione "
            f"<b>{escape(DENOMINATION)}</b>.")


def auto_paid(name, actor, body, values, *, invoice=None, amount=None, headers=None, settings=None, prints=None, status=201,
              group_sent=None):
    """A payment added with auto_paid_payment. invoice: number, description, membership and activity as (rendered,
    str() in memory), created ('$now' or the payment date), meta, group, stored (membership, activity as the column
    keeps them, when a tie rounds them away from the rendered text). amount: (str(float), stored, rendered).
    group_sent: the X-Group-Id text when it is not the stored spelling."""
    stored_amount, shown = (amount[1], amount[2]) if amount else (None, None)
    floated = amount[0] if amount else None
    created = row(**{**values, 'paid': True, 'amount': stored_amount or values.get('amount', '0.00')})
    first = dict(created)
    for column in ('user_id', 'sport_association_id', 'associate_id', 'instructor_id', 'supplier_id'):
        first[column] = None
    audit = [event('$id', 0, created_changes(first, floated), actor_index(actor)),
             event('$id', 1, updated_changes(first, created, floated, stored_amount), [OWN, 'changes_json.sport_association'])]
    final, stored_invoice, item = created, None, None
    party = any(created[column] for column in ('associate_id', 'supplier_id', 'instructor_id'))
    if status == 201 and party and not created['expense']:
        if invoice:
            membership, activity = invoice['membership'], invoice['activity']
            date, meta, group = invoice.get('created', '$now'), invoice.get('meta'), invoice.get('group')
            item = {'invoice_id': '$bind:invoice', 'document_token': None, 'creation_date': date, 'membership_fee': membership[0],
                    'activity_fee': activity[0], 'meta_payment_categories': meta, 'description': invoice['description'],
                    'number': invoice['number'], 'archived': False, 'cancelled': False, 'meta': None, 'imported_from_associami': False,
                    'deleted': False, 'document_pdf': None, 'sport_association': OWN, 'selected_tutor': None,
                    'group': group_sent or group, 'attached_documents': []}
            columns = invoice.get('stored', (membership[0], activity[0]))
            audit.append({'key': '$invoice', 'model': 'invoice', 'action': 0, 'index': DIRECT, 'changes': {
                'invoice_id': ['None', '$invoice'], 'document_pdf': ['None', 'None'], 'sport_association': ['None', OWN],
                'creation_date': ['None', '$now'], 'membership_fee': ['None', membership[1]], 'activity_fee': ['None', activity[1]],
                'meta_payment_categories': ['None', json.dumps(meta, sort_keys=True)], 'description': ['None', invoice['description']],
                'number': ['None', str(invoice['number'])], 'archived': ['None', 'False'], 'cancelled': ['None', 'False'],
                'selected_tutor': ['None', 'None'], 'meta': ['None', 'null'], 'imported_from_associami': ['None', 'False'],
                'group': ['None', group_sent or group or 'None'], 'deleted': ['None', 'False']}})
            if date != '$now':
                # invoice.save(): the payment date, and the float fees (and a group sent in another spelling) against the stored row.
                update = {'creation_date': ['$now', naive(date)]}
                if group_sent:
                    update['group'] = [group, group_sent]
                for column, fee, kept in (('membership_fee', membership, columns[0]), ('activity_fee', activity, columns[1])):
                    if kept != fee[1]:
                        update[column] = [kept, fee[1]]
                audit.append({'key': '$invoice', 'model': 'invoice', 'action': 1, 'index': DIRECT, 'changes': update})
            stored_invoice = {'sport_association_id': OWN, 'creation_date': date, 'membership_fee': columns[0],
                              'activity_fee': columns[1], 'meta_payment_categories': meta, 'description': invoice['description'],
                              'number': invoice['number'], 'group_id': group}
            final = {**created, 'invoice_id': '$invoice'}
            prints = [PRINT_NEW] if prints is None else prints
        # payment.save() after the block: the invoice, and a float amount against the stored decimal.
        last = updated_changes(created, final, floated, stored_amount)
        if last:
            audit.append(event('$id', 1, last, DIRECT))
    response = SERVER
    if status == 201:
        response = {**payment_item({**final, 'invoice_id': None}, '$bind:payment', shown), 'invoice': item}
        if group_sent:
            sent_group(audit, response, created['group_id'], group_sent)
    add(name, actor, ADD, status, response, body=body, headers=headers, prints=prints or (),
        effect={**effect(actor, created=[final], audit=audit), 'documents': [], 'invoice': stored_invoice})
    cases[-1]['owner_settings'] = settings or AUTO_PAID


OWN_CATEGORY = {**plain, 'payment_category_id': CATEGORIES['own']['id']}
PERSON = lambda label: {'group': 'Persone', 'associate_id': A(label)}
auto_paid('add-auto-paid-without-party', 'owner',
          {'creation_date': '2026-04-01', 'payment_date': None, 'amount': '12', 'description': 'Incasso', 'complex_item': None},
          {**plain, 'creation_date': '2026-04-01T00:00:00Z', 'payment_date': '$now', 'amount': '12.00', 'description': 'Incasso'})
auto_paid('add-auto-paid-expense-with-associate', 'owner',
          {'creation_date': '2026-04-01', 'payment_date': '2026-04-02', 'amount': '13', 'expense': True, 'description': 'Rimborso',
           'complex_item': PERSON('mario')},
          {**plain, 'creation_date': '2026-04-01T00:00:00Z', 'payment_date': '2026-04-02T00:00:00Z', 'amount': '13.00', 'expense': True,
           'description': 'Rimborso', 'associate_id': A('mario')})
auto_paid('add-auto-paid-supplier-without-invoice', 'owner',
          {'creation_date': '2026-04-01', 'payment_date': None, 'amount': '14', 'description': 'Reso',
           'complex_item': {'group': 'Fornitori', 'supplier_id': SUPPLIER['id']}},
          {**plain, 'creation_date': '2026-04-01T00:00:00Z', 'payment_date': '$now', 'amount': '14.00', 'description': 'Reso',
           'supplier_id': SUPPLIER['id']})
# The fixture invoice (number 41) belongs to payments created in the current balance year.
auto_paid('add-auto-paid-associate-invoice', 'owner',
          {'creation_date': '2026-04-01', 'payment_date': None, 'amount': '15', 'description': 'Quota auto',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('mario')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-01T00:00:00Z', 'payment_date': '$now', 'amount': '15.00', 'description': 'Quota auto',
           'associate_id': A('mario')},
          invoice={'number': 42, 'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO, 'activity': ('15.00', '15.00')})
auto_paid('add-auto-paid-instructor-invoice', 'owner',
          {'creation_date': '2026-04-02', 'payment_date': None, 'amount': '20', 'description': 'Lezione <privata>',
           'complex_item': {'group': 'Istruttori', 'instructor_id': INSTRUCTOR['id']}},
          {**plain, 'creation_date': '2026-04-02T00:00:00Z', 'payment_date': '$now', 'amount': '20.00', 'description': 'Lezione <privata>',
           'instructor_id': INSTRUCTOR['id']},
          invoice={'number': 43, 'description': recipient_text(f"{INSTRUCTOR['first_name']} {INSTRUCTOR['last_name']}", 'Lezione <privata>'),
                   'membership': ZERO, 'activity': ('20.00', '20.00')})
auto_total = float('30') + (0 + float('5'))
auto_meta = [{'amount': '5', 'title': 'Extra'}]
auto_paid('add-auto-paid-subscription-meta-fees', 'owner',
          {'creation_date': '2026-04-03', 'payment_date': None, 'subject': 1, 'amount': '30', 'description': 'Iscrizione auto',
           'meta_payment_categories': auto_meta, 'complex_item': PERSON('luca')},
          {**plain, 'creation_date': '2026-04-03T00:00:00Z', 'payment_date': '$now', 'subject': 1, 'description': 'Iscrizione auto',
           'meta_payment_categories': auto_meta, 'associate_id': A('luca')},
          amount=(repr(auto_total), *float_amount(auto_total)),
          invoice={'number': 44, 'meta': auto_meta, 'membership': ('30.00', repr(float(repr(auto_total)) - 5.0)), 'activity': ('5.00', '5.0'),
                   'description': f"Ricevuta di pagamento per l'iscrizione di <b>{escape('Luca Verdi è')}</b> all'associazione "
                                  f"<b>{escape(DENOMINATION)}</b>."})
# A creation date before the current balance year numbers after the invoices created before it (none).
auto_paid('add-auto-paid-previous-year-number', 'owner',
          {'creation_date': '2025-03-01', 'payment_date': None, 'amount': '16', 'description': 'Vecchia',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('mario')},
          {**OWN_CATEGORY, 'creation_date': '2025-03-01T00:00:00Z', 'payment_date': '$now', 'amount': '16.00', 'description': 'Vecchia',
           'associate_id': A('mario')},
          invoice={'number': 1, 'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO, 'activity': ('16.00', '16.00')})
# X-Group-Id: the invoices of that group only (none), and the invoice stores the group.
auto_paid('add-auto-paid-group-invoice', 'owner',
          {'creation_date': '2026-04-05', 'payment_date': None, 'amount': '17', 'description': 'Gruppo auto',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('grouped')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-05T00:00:00Z', 'payment_date': '$now', 'amount': '17.00', 'description': 'Gruppo auto',
           'associate_id': A('grouped'), 'group_id': IDS['own_group']}, headers=GROUP,
          invoice={'number': 1, 'group': IDS['own_group'], 'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO,
                   'activity': ('17.00', '17.00')})
# The header in another spelling: the payment and the invoice keep the text as sent in memory; the invoice is saved
# again (payment_date_equal_invoice_date) and records the stored uuid against it.
auto_paid('add-auto-paid-group-upper-case-invoice', 'owner',
          {'creation_date': '2026-04-05', 'payment_date': '2026-04-09', 'amount': '17.5', 'description': 'Gruppo maiuscolo auto',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('grouped')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-05T00:00:00Z', 'payment_date': '2026-04-09T00:00:00Z', 'amount': '17.50',
           'description': 'Gruppo maiuscolo auto', 'associate_id': A('grouped'), 'group_id': IDS['own_group']}, headers=UPPER_GROUP,
          group_sent=IDS['own_group'].upper(), settings={**AUTO_PAID, 'payment_date_equal_invoice_date': True},
          invoice={'number': 2, 'group': IDS['own_group'], 'created': '2026-04-09T00:00:00Z',
                   'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO, 'activity': ('17.50', '17.50')})
# 10.125 and 0.125 are exact floats: the columns round them away from zero, the response to the even cent; the
# invoice's second save compares the column values with the floats.
tie_total = float('10') + (0 + float('0.125'))
tie_meta = [{'amount': '0.125', 'title': 'Extra'}]
assert float_amount(tie_total) == ('10.13', '10.12')
auto_paid('add-auto-paid-fee-tie', 'owner',
          {'creation_date': '2026-04-06', 'payment_date': '2026-04-11', 'subject': 1, 'amount': '10', 'description': 'Iscrizione pari',
           'meta_payment_categories': tie_meta, 'complex_item': PERSON('luca')},
          {**plain, 'creation_date': '2026-04-06T00:00:00Z', 'payment_date': '2026-04-11T00:00:00Z', 'subject': 1,
           'description': 'Iscrizione pari', 'meta_payment_categories': tie_meta, 'associate_id': A('luca')},
          amount=(repr(tie_total), *float_amount(tie_total)),
          settings={**AUTO_PAID, 'payment_date_equal_invoice_date': True},
          invoice={'number': 45, 'meta': tie_meta, 'created': '2026-04-11T00:00:00Z',
                   'membership': ('10.00', '10.0'), 'activity': ('0.12', '0.125'), 'stored': ('10.00', '0.13'),
                   'description': f"Ricevuta di pagamento per l'iscrizione di <b>{escape('Luca Verdi è')}</b> all'associazione "
                                  f"<b>{escape(DENOMINATION)}</b>."})
auto_paid('add-auto-paid-invoice-date-and-starting-number', 'owner',
          {'creation_date': '2026-04-06', 'payment_date': '2026-04-10', 'amount': '18', 'description': 'Datata',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('mario')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-06T00:00:00Z', 'payment_date': '2026-04-10T00:00:00Z', 'amount': '18.00',
           'description': 'Datata', 'associate_id': A('mario')},
          settings={**AUTO_PAID, 'payment_date_equal_invoice_date': True, 'starting_number_invoices': 100},
          invoice={'number': 101, 'created': '2026-04-10T00:00:00Z', 'description': other_text(CATEGORIES['own']['name']),
                   'membership': ZERO, 'activity': ('18.00', '18.00')})
# The print's failure stays in the eager result.
auto_paid('add-auto-paid-print-failure-ignored', 'owner',
          {'creation_date': '2026-04-07', 'payment_date': None, 'amount': '19', 'description': 'Senza stampa',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('mario')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-07T00:00:00Z', 'payment_date': '$now', 'amount': '19.00', 'description': 'Senza stampa',
           'associate_id': A('mario')},
          prints=[{**PRINT_NEW, 'reply': {'status': 500, 'body': 'renderer down'}}],
          invoice={'number': 102, 'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO, 'activity': ('19.00', '19.00')})
auto_paid('add-auto-paid-full-collaborator', 'full_collaborator',
          {'creation_date': '2026-04-08', 'payment_date': None, 'amount': '21', 'description': 'Collaboratore auto',
           'payment_category': CATEGORIES['own']['id'], 'complex_item': PERSON('mario')},
          {**OWN_CATEGORY, 'creation_date': '2026-04-08T00:00:00Z', 'payment_date': '$now', 'amount': '21.00',
           'description': 'Collaboratore auto', 'associate_id': A('mario')},
          invoice={'number': 103, 'description': other_text(CATEGORIES['own']['name']), 'membership': ZERO, 'activity': ('21.00', '21.00')})
# An account transfer has no invoice description (KeyError inside the atomic block): the saved payment stays.
auto_paid('add-auto-paid-transfer-with-associate-server-error', 'owner',
          {'creation_date': '2026-04-09', 'payment_date': None, 'subject': 3, 'amount': '22', 'description': 'Giroconto',
           'complex_item': PERSON('mario')},
          {**plain, 'creation_date': '2026-04-09T00:00:00Z', 'payment_date': '$now', 'subject': 3, 'amount': '22.00',
           'description': 'Giroconto', 'associate_id': A('mario')}, status=500)
# The owner's balance year start is not a date (ValueError in the number lookup): the saved payment stays.
auto_paid('add-auto-paid-invalid-balance-year-server-error', 'owner',
          {'creation_date': '2026-04-09', 'payment_date': None, 'amount': '23', 'description': 'Anno',
           'complex_item': PERSON('mario')},
          {**plain, 'creation_date': '2026-04-09T00:00:00Z', 'payment_date': '$now', 'amount': '23.00', 'description': 'Anno',
           'associate_id': A('mario')}, status=500,
          settings={**AUTO_PAID, 'balance_sheet_start_day': 30, 'balance_sheet_start_month': 2})

# --- payment/<uid>/update --------------------------------------------------------

guards('update', UPDATE('up_plain'), 'PATCH', {'detail': 'Missing permission: bookeeping.payments.update'}, 'Payment Update', DOC)
add('update-malformed', 'owner', UPDATE('abc'), 400, MALFORMED, method='PATCH', body={})
add('update-athlete-server-error', 'athlete', UPDATE('up_plain'), 500, SERVER, method='PATCH', body={})
add('update-owner-without-association-server-error', 'owner_without_association', UPDATE('up_plain'), 500, SERVER, method='PATCH', body={})
add('update-missing-server-error', 'owner', UPDATE(IDS['missing']), 500, SERVER, method='PATCH', body={})
add('update-deleted-server-error', 'owner', UPDATE('up_removed'), 500, SERVER, method='PATCH', body={})
add('update-without-association-server-error', 'owner', UPDATE('up_orphan'), 500, SERVER, method='PATCH', body={})
add('update-other-group-server-error', 'owner', UPDATE('up_grouped'), 500, SERVER, method='PATCH', body={}, headers=OTHER_GROUP)
add('update-foreign-denied', 'owner', UPDATE('abroad'), 403, DENIED, method='PATCH', body={})
add('update-scoped-session-foreign-server-error', 'owner', UPDATE('abroad'), 500, SERVER, method='PATCH', body={}, headers=OWNER_FULL)
add('update-list-body-server-error', 'owner', UPDATE('up_plain'), 500, SERVER, method='PATCH', body=['amount'])
add('update-validation-errors', 'owner', UPDATE('up_plain'), 400, {
    'type': ['"x" is not a valid choice.'], 'subject': ['"True" is not a valid choice.'], 'amount': ['A valid number is required.'],
    'paid': ['Must be a valid boolean.'], 'payment_date': [WRONG_DATE], 'creation_date': ['This field may not be null.'],
    'attachments_data': {'0': ['Expected a dictionary of items but got type "int".']}},
    method='PATCH', body={'type': 'x', 'subject': True, 'amount': 'x', 'paid': 'maybe', 'payment_date': 5, 'creation_date': None,
                          'attachments_data': [3]}, effect=effect('owner'))
add('update-related-errors', 'owner', UPDATE('up_plain'), 400, {
    'description': ['This field may not be blank.'], 'payment_category': [f'Invalid pk "{IDS["missing"]}" - object does not exist.'],
    'custom_accounts': ['“1.5” is not a valid UUID.'], 'amount': ['Ensure that there are no more than 9 digits in total.']},
    method='PATCH', body={'description': '', 'payment_category': IDS['missing'], 'custom_accounts': 1.5, 'amount': '1234567.891'},
    effect=effect('owner'))
add('update-foreign-category-denied', 'owner', UPDATE('up_plain'), 403, TENANT, method='PATCH',
    body={'amount': 'x', 'payment_category': CATEGORIES['foreign']['id']}, effect=effect('owner'))
add('update-foreign-association-denied', 'owner', UPDATE('up_plain'), 403, TENANT, method='PATCH', body={'sport_association': FOREIGN},
    effect=effect('owner'))
add('update-missing-party-server-error', 'owner', UPDATE('up_plain'), 500, SERVER, method='PATCH',
    body={'complex_item': {'group': 'Istruttori', 'instructor_id': IDS['missing']}}, effect=effect('owner'))
add('update-missing-subscription-server-error', 'owner', UPDATE('up_plain'), 500, SERVER, method='PATCH',
    body={'subscription_id': IDS['missing']}, effect=effect('owner'))
# A party of another association: 403 before the save.
for group, key, party in [('Persone', 'associate_id', A('abroad')), ('Iscrizioni', 'subscription_id', FOREIGN_PARTIES['subscription']['id']),
                          ('Istruttori', 'instructor_id', FOREIGN_PARTIES['instructor']['id']),
                          ('Fornitori', 'supplier_id', FOREIGN_PARTIES['supplier']['id'])]:
    add(f'update-foreign-party-{group.lower()}-denied', 'owner', UPDATE('up_party'), 403, DENIED, method='PATCH',
        body={'complex_item': {'group': group, key: party}}, effect=effect('owner'))
add('update-foreign-subscription-id-denied', 'owner', UPDATE('up_subscription'), 403, DENIED, method='PATCH',
    body={'complex_item': {'group': 'Persone', 'associate_id': A('luca')}, 'subscription_id': FOREIGN_PARTIES['subscription']['id']},
    effect=effect('owner'))
add('update-meta-without-amount-server-error', 'owner', UPDATE('up_plain'), 500, SERVER, method='PATCH',
    body={'meta_payment_categories': []}, effect=effect('owner'))
add('update-transfer-without-description-server-error', 'owner', UPDATE('up_transfer'), 500, SERVER, method='PATCH',
    body={'description': None}, effect=effect('owner'))


def updated(name, actor, label, body, changes, *, amount=None, headers=None, status=200, response=None, prints=(), documents=()):
    """Payment.save() of the in-memory payment; amount: (str(float), stored, rendered) for a float amount."""
    old = STATE[label]
    new = {**old, **changes}
    stored, shown, floated = (amount[1], amount[2], amount[0]) if amount else (None, None, None)
    if stored:
        new['amount'] = stored
    audit_changes = updated_changes(old, new, floated)
    columns = {column: value for column, value in new.items() if column != 'id' and old[column] != value}
    STATE[label] = new
    audit = [event(label, 1, audit_changes, DIRECT)] if audit_changes else []
    document_audit, stored_documents, rendered = attachment_docs(documents, actor, label)
    audit.extend(document_audit)
    if response is None:
        response = {'data': {'payment': {**payment_item(new, old['id'], shown), 'attachments': rendered}}}
    add(name, actor, UPDATE(label), status, response, method='PATCH', body=body, headers=headers,
        effect={**effect(actor, changed={label: columns}, audit=audit), 'documents': stored_documents}, prints=prints)


updated('update-empty-body-clears-parties', 'owner', 'up_plain', {}, {'supplier_id': None, 'payment_intent_id': None})
updated('update-fields', 'owner', 'up_fields', {
    'type': 'transfer', 'subject': 0, 'amount': '99.9', 'description': 'Nuova è', 'paid': 'true', 'expense': 1,
    'payment_date': '2026-04-01', 'creation_date': '', 'notes': '', 'course': None, 'custom_accounts': ACCOUNTS['bank']['id'],
    'payment_category': CATEGORIES['global']['id'], 'unknown': 'ignored'},
    {'type': 'transfer', 'subject': 0, 'amount': '99.90', 'description': 'Nuova è', 'paid': True, 'expense': True,
     'payment_date': '2026-04-01T00:00:00Z', 'notes': '', 'course': None, 'custom_accounts_id': ACCOUNTS['bank']['id'],
     'payment_category_id': CATEGORIES['global']['id'], 'instructor_id': None})
total = float('20') + (0 + 0.1 + float('0.2'))
updated('update-meta-float-amount', 'owner', 'up_meta', {'amount': '20', 'meta_payment_categories': [{'amount': 0.1}, {'amount': '0.2'}],
                                                         'payment_date': '2026-04-02T10:00:00+02:00'},
        {'meta_payment_categories': [{'amount': 0.1}, {'amount': '0.2'}], 'payment_date': '2026-04-02T08:00:00Z'},
        amount=(repr(total), *float_amount(total)))
updated('update-defaults', 'owner', 'up_defaults', {'description': None, 'subject': '0'},
        {'payment_category_id': DEFAULT, 'description': '$default_category_name'})
updated('update-person', 'owner', 'up_party', {'complex_item': {'group': 'Persone', 'associate_id': A('luca')}}, {'associate_id': A('luca')})
updated('update-instructor', 'owner', 'up_party', {'complex_item': {'group': 'Istruttori', 'instructor_id': INSTRUCTOR['id']}},
        {'associate_id': None, 'instructor_id': INSTRUCTOR['id']})
updated('update-supplier', 'owner', 'up_party', {'complex_item': {'group': 'Fornitori', 'supplier_id': SUPPLIER['id']}},
        {'instructor_id': None, 'supplier_id': SUPPLIER['id']})
updated('update-subscription-overrides-party', 'owner', 'up_subscription',
        {'complex_item': {'group': 'Persone', 'associate_id': A('luca')}, 'subscription_id': SUBSCRIPTIONS['mario']['id']},
        {'associate_id': A('mario'), 'supplier_id': None})
updated('update-unknown-party-group-clears', 'owner', 'up_unknown_group', {'complex_item': {'group': 'Nessuno'}}, {'associate_id': None})
updated('update-group', 'owner', 'up_grouped', {'amount': '12'}, {'amount': '12.00'}, headers=GROUP)
updated('update-full-collaborator', 'full_collaborator', 'up_collab', {'amount': '13'}, {'amount': '13.00'})
updated('update-admin-impersonating-owner', 'admin', 'up_admin', {'amount': '14'}, {'amount': '14.00'}, headers=ADMIN_OWNER)
updated('update-attachment-without-keys', 'owner', 'up_attachment', {'amount': '15', 'attachments_data': [{'filename': 'x.pdf'}]},
        {'amount': '15.00'}, status=400, response={'exception': 'document key not present'})
updated('update-unchanged-no-audit', 'owner', 'up_collab', {'amount': '13.00'}, {})
# payment_id is a writable serializer field (UUID primary key with a default): the row's own key validates
# (UniqueValidator excludes the instance) and the save updates the same row.
updated('update-own-payment-id', 'owner', 'up_own_id', {'payment_id': PAYMENTS['up_own_id']['id'], 'amount': '16'}, {'amount': '16.00'})
updated('update-own-payment-id-uppercase', 'owner', 'up_own_upper',
        {'payment_id': PAYMENTS['up_own_upper']['id'].upper(), 'amount': '17', 'description': 'Modificata'},
        {'amount': '17.00', 'description': 'Modificata'})

PRINT_INVOICE = f'/document/invoice/{INVOICE["id"]}?send_receipt_email=False'
# print_document_invoice.apply(args=[invoice_id, Authorization]) after the save: a GET of CURRENT_HOST whose
# failure is kept in the eager result (the view answers normally).
updated('update-invoiced-prints-invoice', 'owner', 'up_invoiced', {'amount': '18'}, {'amount': '18.00'},
        prints=[{'path': PRINT_INVOICE, 'reply': {'status': 200, 'body': 'printed'}}])
updated('update-invoiced-print-failure-ignored', 'owner', 'up_invoiced_print_fails', {'amount': '19', 'payment_id': PAYMENTS['up_invoiced_print_fails']['id']},
        {'amount': '19.00'}, prints=[{'path': PRINT_INVOICE, 'reply': {'status': 500, 'body': 'renderer down'}}])

# attachments_data with a file (AddEditModal.svelte builds {document: base64, filename}).
PDF = b'%PDF-1.4\n% ricevuta fixture \xe8\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\nfixture'
b64 = lambda data: base64.b64encode(data).decode()
added('add-attachment', 'owner', {'creation_date': '2026-03-20', 'payment_date': None, 'amount': '12', 'description': 'Con allegato',
                                  'complex_item': None, 'attachments_data': [{'document': b64(PDF), 'filename': 'ricevuta è.pdf'}]},
      {**plain, 'creation_date': '2026-03-20T00:00:00Z', 'amount': '12.00', 'description': 'Con allegato'},
      documents=[('ricevuta è.pdf', PDF)])
updated('update-attachment', 'owner', 'up_attach_one', {'amount': '21', 'attachments_data': [{'document': b64(PNG), 'filename': 'foto.png',
                                                                                              'extra': 1}]},
        {'amount': '21.00'}, documents=[('foto.png', PNG)])
# The first item is stored, the second lacks its keys: 400 after the save and the first document.
updated('update-attachment-then-missing-keys', 'full_collaborator', 'up_attach_partial',
        {'amount': '22', 'attachments_data': [{'document': b64(PDF), 'filename': 'primo.pdf'}, {'document': b64(PDF)}]},
        {'amount': '22.00'}, status=400, response={'exception': 'document key not present'}, documents=[('primo.pdf', PDF)])
# b64decode raises binascii.Error (incorrect padding) after the save: the 500 page, no document.
updated('update-attachment-invalid-base64', 'owner', 'up_attach_bad64', {'amount': '23', 'attachments_data': [{'document': 'abc', 'filename': 'x.pdf'}]},
        {'amount': '23.00'}, status=500, response=SERVER)

# --- payment/bulk-add ------------------------------------------------------------

BULK_DOC = 'API endpoint to add multiple payments for multiple associates in a single operation.'
guards('bulk-add', BULK, 'POST', {'detail': "Access denied: No permission mapping for endpoint 'payment/bulk-add'. Contact administrator to grant access."},
       'Payment Bulk Add', BULK_DOC)
IDS_REQUIRED = {'msg': 'associate_ids is required and must be a non-empty list'}
add('bulk-add-athlete-forbidden', 'athlete', BULK, 403, EMPTY, body={'associate_ids': [A('mario')], 'amount': 1})
add('bulk-add-owner-without-association-server-error', 'owner_without_association', BULK, 500, SERVER, body={})
add('bulk-add-list-body-server-error', 'owner', BULK, 500, SERVER, body=[A('mario')])
add('bulk-add-without-ids', 'owner', BULK, 400, IDS_REQUIRED, body={'amount': 1}, effect=effect('owner'))
add('bulk-add-empty-ids', 'owner', BULK, 400, IDS_REQUIRED, body={'associate_ids': [], 'amount': 1}, effect=effect('owner'))
add('bulk-add-string-ids', 'owner', BULK, 400, IDS_REQUIRED, body={'associate_ids': A('mario'), 'amount': 1}, effect=effect('owner'))
add('bulk-add-form-body', 'owner', BULK, 400, IDS_REQUIRED, raw_body=f'associate_ids={A("mario")}&amount=1',
    content_type='application/x-www-form-urlencoded', effect=effect('owner'))
add('bulk-add-too-many-ids', 'owner', BULK, 400, {'msg': 'associate_ids cannot exceed 10,000 items'}, body={'associate_ids': [1] * 10001},
    effect=effect('owner'))
add('bulk-add-without-amount', 'owner', BULK, 400, {'msg': 'amount is required'}, body={'associate_ids': [A('mario')], 'amount': None},
    effect=effect('owner'))
add('bulk-add-negative-amount', 'owner', BULK, 400, {'msg': 'amount must be >= 0'}, body={'associate_ids': [A('mario')], 'amount': '-0.01'},
    effect=effect('owner'))
INVALID_AMOUNT = {'msg': 'amount must be a valid decimal number'}
add('bulk-add-text-amount', 'owner', BULK, 400, INVALID_AMOUNT, body={'associate_ids': [A('mario')], 'amount': '1,5'}, effect=effect('owner'))
add('bulk-add-bool-amount', 'owner', BULK, 400, INVALID_AMOUNT, body={'associate_ids': [A('mario')], 'amount': True}, effect=effect('owner'))
add('bulk-add-nan-amount', 'owner', BULK, 400, INVALID_AMOUNT, body={'associate_ids': [A('mario')], 'amount': 'NaN'}, effect=effect('owner'))
add('bulk-add-invalid-type', 'owner', BULK, 400, {'msg': "type must be one of: ['default', 'cash', 'transfer', 'online', 'sepa-transfer', 'stripe', 'pos']"},
    body={'associate_ids': [A('mario')], 'amount': 1, 'type': None}, effect=effect('owner'))
add('bulk-add-invalid-subject', 'owner', BULK, 400, {'msg': 'subject must be one of: [0, 1, 2, 3]'},
    body={'associate_ids': [A('mario')], 'amount': 1, 'subject': '1'}, effect=effect('owner'))
add('bulk-add-nested-id-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [[A('mario')]], 'amount': 1}, effect=effect('owner'))
add('bulk-add-malformed-id-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [A('mario'), 'abc'], 'amount': 1},
    effect=effect('owner'))
add('bulk-add-negative-id-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [-1], 'amount': 1}, effect=effect('owner'))


def any_order(items):
    return {'$any_order': items}


# 1 and True are one set member (the first one kept).
add('bulk-add-nothing-valid', 'owner', BULK, 200, {'data': {'created_count': 0, 'total_requested': 4,
                                                            'invalid_associate_ids': any_order([IDS['missing'], A('abroad'), A('removed'), '1'])}},
    body={'associate_ids': [IDS['missing'], A('abroad'), A('removed'), 1, True, IDS['missing']], 'amount': 1, 'expense': 'maybe'},
    effect=effect('owner'))
add('bulk-add-other-group-nothing-valid', 'owner', BULK, 200, {'data': {'created_count': 0, 'total_requested': 1,
                                                                        'invalid_associate_ids': [A('mario')]}},
    body={'associate_ids': [A('mario')], 'amount': 1}, headers=GROUP, effect=effect('owner'))


def bulk(name, actor, body, people, values, *, headers=None, invalid=(), total=None):
    rows = [row(**{'user_id': OWNER, 'sport_association_id': OWN, 'payment_category_id': DEFAULT, 'custom_accounts_id': ACCOUNTS['bank']['id'],
                   'meta_payment_categories': [], **values, 'associate_id': A(person)}) for person in people]
    response = {'data': {'created_count': len(people), 'total_requested': total if total is not None else len(people) + len(invalid),
                         'invalid_associate_ids': any_order(list(invalid))}}
    add(name, actor, BULK, 201, response, body=body, headers=headers, effect=effect(actor, created=rows))


bulk('bulk-add-owner', 'owner', {'associate_ids': [A('mario'), A('luca'), A('mario').upper(), A('abroad'), None, A('mario')], 'amount': '12.5',
                                 'type': 'cash', 'subject': 1, 'creation_date': '2026-05-01', 'course': {'value': 1}},
     ['mario', 'luca'], {'amount': '12.50', 'type': 'cash', 'subject': 1, 'description': 'Iscrizione', 'creation_date': '2026-05-01T00:00:00Z'},
     invalid=[A('mario').upper(), A('abroad'), 'None'])
bulk('bulk-add-every-field', 'owner', {'associate_ids': [A('luca')], 'amount': ' 7.005 ', 'type': 'pos', 'subject': 2.0, 'paid': True,
                                       'expense': 'f', 'creation_date': 'yesterday', 'payment_category': CATEGORIES['own']['id'],
                                       'custom_accounts': ACCOUNTS['cash']['id'], 'description': '', 'notes': 42,
                                       'meta_payment_categories': [{'amount': 1.5}]},
     ['luca'], {'amount': '7.01', 'type': 'pos', 'subject': 2, 'paid': True, 'payment_date': '$now', 'creation_date': '$now', 'description': '',
                'payment_category_id': CATEGORIES['own']['id'], 'custom_accounts_id': ACCOUNTS['cash']['id'], 'notes': '42',
                'meta_payment_categories': [{'amount': 1.5}]})
bulk('bulk-add-unmatched-references', 'owner', {'associate_ids': [A('mario')], 'amount': 0, 'subject': True, 'paid': 1, 'description': None,
                                                'payment_category': IDS['missing'], 'custom_accounts': ACCOUNTS['foreign']['id'],
                                                'payment_date': '2026-05-02T10:00:00+02:00', 'meta_payment_categories': None},
     ['mario'], {'subject': 1, 'paid': True, 'description': 'Iscrizione', 'payment_category_id': None, 'custom_accounts_id': None,
                 'payment_date': '2026-05-02T08:00:00Z', 'creation_date': '$now', 'meta_payment_categories': None})
# The category comes from the association's own or shared categories (another association's: none).
bulk('bulk-add-foreign-category', 'owner', {'associate_ids': [A('mario')], 'amount': 1, 'creation_date': '2026-05-07',
                                            'payment_category': CATEGORIES['foreign']['id']},
     ['mario'], {'amount': '1.00', 'creation_date': '2026-05-07T00:00:00Z', 'payment_category_id': None})
bulk('bulk-add-shared-category', 'owner', {'associate_ids': [A('mario')], 'amount': 2, 'creation_date': '2026-05-08',
                                           'payment_category': CATEGORIES['global']['id']},
     ['mario'], {'amount': '2.00', 'creation_date': '2026-05-08T00:00:00Z', 'payment_category_id': CATEGORIES['global']['id']})
bulk('bulk-add-group', 'owner', {'associate_ids': [A('grouped'), A('mario')], 'amount': 3, 'subject': 0, 'creation_date': '2026-5-3',
                                 'payment_date': '2026-05-04', 'paid': 'f'},
     ['grouped'], {'amount': '3.00', 'creation_date': '2026-05-03T00:00:00Z', 'payment_date': '2026-05-04T00:00:00Z'}, headers=GROUP,
     invalid=[A('mario')])
bulk('bulk-add-full-collaborator', 'full_collaborator', {'associate_ids': [A('mario')], 'amount': '4', 'subject': 2, 'creation_date': '2026-05-05'},
     ['mario'], {'amount': '4.00', 'subject': 2, 'description': 'Quota Corso', 'creation_date': '2026-05-05T00:00:00Z'})
bulk('bulk-add-admin-impersonating-owner', 'admin', {'associate_ids': [A('mario')], 'amount': '5', 'creation_date': '2026-05-06'},
     ['mario'], {'amount': '5.00', 'creation_date': '2026-05-06T00:00:00Z'}, headers=ADMIN_OWNER)
add('bulk-add-invalid-expense-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [A('mario')], 'amount': 1, 'expense': 'maybe'},
    effect=effect('owner'))
add('bulk-add-infinite-amount-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [A('mario')], 'amount': 'Infinity'},
    effect=effect('owner'))
add('bulk-add-overflow-server-error', 'owner', BULK, 500, SERVER, body={'associate_ids': [A('mario'), A('luca')], 'amount': '9999999.995'},
    effect=effect('owner'))
add('bulk-add-invalid-payment-date-server-error', 'owner', BULK, 500, SERVER,
    body={'associate_ids': [A('mario')], 'amount': 1, 'payment_date': 'domani'}, effect=effect('owner'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Payment add, update and bulk add authored sequence with payment and audit effects',
                              'ids': IDS, 'categories': CATEGORIES, 'accounts': ACCOUNTS, 'associates': ASSOCIATES,
                              'subscriptions': SUBSCRIPTIONS, 'instructor': INSTRUCTOR, 'supplier': SUPPLIER,
                              'foreign_parties': FOREIGN_PARTIES, 'payments': PAYMENTS, 'invoice': INVOICE,
                              'location': 'payment-writes-media', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
