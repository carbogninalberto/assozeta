"""Author the payment approve, cancel, delete and bulk archive/delete HTTP sequence (payment-state.md).

All five are @api_view function views behind IsAuthenticated (registry:
approve and cancel ``bookeeping.payments.update``, delete and bulk delete
``bookeeping.payments.delete``, bulk archive ``bookeeping.payments.archive.update``).
Each reads ``scoped_queryset(request, SportAssociation.objects).get(user=request.user)``
(athletes and owners without a live association: 500) and looks payments up
through Payment.objects (live, X-Group-Id aware; association-scoped sessions
add the session's association).

approve: is_valid_uuid (400), request.data.get (a non-dict body is a 500),
``payment_date`` parsed with '%Y-%m-%d' (UTC midnight; anything else a 500),
then atomically the first payment by id (none: 500; another association:
PermissionDenied 'User not allowed.'). An unpaid payment becomes paid with the
sent date, else its stored date, else now; an expense also marks its first
supplier invoice paid with the payment's date and answers
{'data': {'payment': ...}}; a payment without associate, supplier and
instructor answers the same. A paid payment answers
{'data': {'msg': 'payment mark as payed.', 'payment': ...}}. Payment.save()
fills a null category and description and is audited (changed fields, indexed
directly). Unpaid income payments with a party run the paid flow (see the
paid flow section below).

cancel: no UUID check (a malformed id is a 500), same lookup; an unpaid
payment is 400 {'error': 'Payment not paid.'}; a paid one is serialized
before the save (the response still says paid), then saved unpaid.
A paid invoiced payment is serialized after the save (the in-memory invoice
already cancelled, its stored meta unchanged); its invoice is saved cancelled
with meta = the PaymentSerializer values (str() of the nested user and
associate dicts) and print_document_invoice(invoice, authorization) is
published (apply_async).

delete: is_valid_uuid, then the payment of the association (missing,
foreign, deleted, other group: 404 {'msg': 'Payment not found.'});
payment.delete() runs Payment's pre_delete signal (the payment's live
group-aware installments are deleted; each one's pre_delete deletes its
still unpaid payment first), clears SET_NULL references and through rows and
audits P / installments / P. An invoiced payment first gets its invoice
saved cancelled with the same meta, then print_document_invoice runs eagerly
(.apply: one GET to CURRENT_HOST's /document/invoice/<id>), then the delete.

bulk archive / bulk delete: ``'payment_ids' in data.keys()`` (missing or a
non-dict body: 500), ``payment_id__in`` (None not iterable, a string iterated
by character, members through UUIDField.to_python: integers and booleans are
integer UUIDs, None dropped, anything else a 500), then bulk_update of
archived / deleted on the matching live rows: no signals, no audit.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path
import uuid

OUTPUT = Path(__file__).with_name('payment_state_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{985001 + n:012d}' for n in range(300))
SERVER = '<stable-server-error-page>'
CATEGORY = next(ids)
CATEGORY_NAME = 'Pagamenti fixture'
DOCUMENT = {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta è.pdf', 'filepath': 'docs/ricevuta.pdf', 'size': 4096,
            'created': '2026-02-03T08:00:00Z'}
OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}
ASSOCIATE = {'id': next(ids), 'first': 'Mario', 'last': 'Rossi', 'sex': 'M', 'tax': 'RSSMRA90E10H501Z', 'born': '1990-05-10',
             'email': 'mario@example.test'}
SUBSCRIPTION = {'id': next(ids), 'created': '2026-08-01T10:00:00Z'}
INSTRUCTOR = {'id': next(ids), 'first_name': 'Anna è', 'last_name': 'Bianchi', 'email': 'anna@example.test', 'tax_code': 'BNCNNA80A41H501X',
              'default_hourly_billing': '12.50'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore è', 'vat_number': '01234567890', 'email': 'fornitore@example.test'}
COURSE = {'id': next(ids), 'title': 'Corso pagamenti'}
CARNET = {'id': next(ids), 'title': 'Carnet pagamenti'}


def payment(subject=2, amount='10.00', paid=False, *, association=OWN, expense=False, associate=False, user=False, supplier=False,
            instructor=False, group=None, deleted=False, archived=False, category=CATEGORY, created='2026-10-01T08:00:00Z', paid_on=None,
            description='Quota', meta=None, attachment=False, meta_categories=None, linked=False, invoice=None):
    return {'invoice': invoice, 'id': next(ids), 'association': association, 'subject': subject, 'amount': amount, 'paid': paid, 'expense': expense,
            'associate': associate, 'user': user, 'supplier': supplier, 'instructor': instructor, 'group': group, 'deleted': deleted,
            'archived': archived, 'category': category, 'created': created, 'payment_date': paid_on, 'description': description,
            'meta': meta, 'attachment': attachment, 'meta_categories': meta_categories, 'linked': linked}


PAYMENTS = {
    # approve
    'ap_plain': payment(),
    'ap_dated': payment(amount='12.00', meta={'note': 'è'}),
    'ap_stored': payment(paid_on='2026-05-06T07:08:09.123456Z'),
    'ap_defaults': payment(subject=1, category=None, description=None),
    'ap_supplier': payment(amount='30.00', expense=True, supplier=True, user=True, description='Fattura fornitore'),
    'ap_instructor': payment(amount='40.00', expense=True, instructor=True, description='Compenso'),
    'ap_paid': payment(subject=0, amount='25.00', paid=True, associate=True, user=True, paid_on='2026-04-01T00:00:00Z', attachment=True),
    'ap_collab': payment(),
    'ap_admin': payment(),
    'ap_group': payment(group='own_group'),
    'ap_form': payment(),
    'ap_transfer': payment(subject=3, description=None),
    'ap_removed': payment(deleted=True),
    # cancel
    'ca_plain': payment(paid=True, paid_on='2026-03-01T09:00:00Z'),
    'ca_uncategorized': payment(paid=True, category=None, paid_on='2026-03-02T09:00:00Z'),
    'ca_instructor': payment(amount='40.00', paid=True, expense=True, instructor=True, paid_on='2026-03-03T09:00:00Z', description='Compenso'),
    'ca_supplier': payment(amount='30.00', paid=True, expense=True, supplier=True, paid_on='2026-03-04T09:00:00Z', description='Spesa'),
    'ca_unpaid': payment(),
    'ca_collab': payment(paid=True, paid_on='2026-03-05T09:00:00Z'),
    'ca_admin': payment(paid=True, paid_on='2026-03-06T09:00:00Z'),
    'ca_transfer': payment(subject=3, paid=True, description=None),
    'ca_removed': payment(paid=True, deleted=True),
    'ca_invoiced': payment(subject=0, amount='25.00', paid=True, associate=True, user=True, paid_on='2026-03-10T09:00:00Z', invoice='v2pay3_cancel'),
    'ca_invoiced_plain': payment(amount='14.00', paid=True, paid_on='2026-03-11T09:30:00.250000Z', description='Quota libera',
                                 invoice='v2pay3_cancel_plain'),
    'ca_invoiced_unpaid': payment(subject=0, associate=True, invoice='v2pay3_cancel_unpaid'),
    # delete
    'de_plain': payment(attachment=True),
    'de_installments': payment(amount='60.00'),
    'de_paid': payment(amount='20.00', paid=True, paid_on='2026-02-01T00:00:00Z'),
    'de_group': payment(group='own_group'),
    'de_collab': payment(),
    'de_admin': payment(),
    'de_removed': payment(deleted=True),
    'de_invoiced': payment(subject=0, amount='27.00', paid=True, associate=True, user=True, paid_on='2026-02-12T00:00:00Z', invoice='v2pay3_delete'),
    'de_invoiced_collab': payment(amount='13.00', invoice='v2pay3_delete_collab'),
    # bulk archive
    'ba_a': payment(), 'ba_b': payment(paid=True), 'ba_stored': payment(archived=True), 'ba_group': payment(group='own_group'),
    'ba_plain': payment(), 'ba_collab': payment(), 'ba_removed': payment(deleted=True),
    # bulk delete
    'bd_a': payment(), 'bd_installment': payment(), 'bd_group': payment(group='own_group'), 'bd_plain': payment(),
    'bd_collab': payment(), 'bd_admin': payment(),
    'abroad': payment(association=FOREIGN, paid=True, paid_on='2026-01-01T00:00:00Z'),
    # paid flow (unpaid income payments with a party)
    'pf_other': payment(subject=0, amount='15.00', associate=True, user=True),
    'pf_old': payment(subject=0, amount='20.00', associate=True),
    'pf_subscription': payment(subject=1, amount='30.00', associate=True,
                               meta_categories=[{'name': 'Corso', 'amount': '5.50'}, {'name': 'Gratis', 'amount': 0}, {'amount': 2}]),
    'pf_course': payment(amount='50.00', associate=True, linked=True),
    'pf_installment': payment(amount='18.00', associate=True, linked=True),
    'pf_carnet': payment(amount='35.00', associate=True, linked=True),
    'pf_course_none': payment(amount='11.00', associate=True),
    'pf_supplier': payment(subject=0, amount='12.00', supplier=True, user=True),
    'pf_instructor': payment(subject=0, amount='40.00', instructor=True),
    'pf_collab': payment(subject=0, amount='16.00', associate=True),
    'pf_admin': payment(subject=0, amount='17.00', associate=True),
    'pf_group': payment(amount='19.00', associate=True, group='own_group'),
    'pf_form': payment(subject=0, amount='21.00', associate=True),
    'pf_scoped': payment(amount='22.00', associate=True, linked=True),
    'pf_bad_meta': payment(subject=1, amount='23.00', associate=True, meta_categories=[{'amount': 'abc'}]),
    'pf_carnet_null': payment(amount='24.00', associate=True),
}
SUPPLIER_INVOICES = {
    'si_approve': {'id': next(ids), 'payment': 'ap_supplier', 'identifier': 'FT-1', 'amount': '30.00'},
    'si_linked': {'id': next(ids), 'payment': 'de_plain', 'identifier': 'FT-2', 'amount': '10.00'},
    'si_paid_flow': {'id': next(ids), 'payment': 'pf_supplier', 'identifier': 'FT-3', 'amount': '12.00'},
}
COURSE_SUBSCRIPTIONS = {
    'cs_linked': {'id': next(ids), 'payment': 'de_plain', 'memberships': ['de_plain']},
    'cs_installments': {'id': next(ids), 'payment': None, 'memberships': []},
    'cs_pf': {'id': next(ids), 'payment': 'pf_course', 'memberships': []},
    'cs_pf_parent': {'id': next(ids), 'payment': None, 'memberships': []},
    'cs_pf_carnet': {'id': next(ids), 'payment': None, 'memberships': []},
    'cs_pf_scoped': {'id': next(ids), 'payment': 'pf_scoped', 'memberships': []},
    'cs_pf_null': {'id': next(ids), 'payment': None, 'memberships': []},
}
INSTALLMENTS = {
    'i1': {'id': next(ids), 'payment': 'de_installments', 'deleted': False, 'group': None},
    'i2': {'id': next(ids), 'payment': 'de_installments', 'deleted': False, 'group': None},
    'i_deleted': {'id': next(ids), 'payment': 'de_installments', 'deleted': True, 'group': None},
    'i_paid': {'id': next(ids), 'payment': 'de_paid', 'deleted': False, 'group': None},
    'ig_in': {'id': next(ids), 'payment': 'de_group', 'deleted': False, 'group': 'own_group'},
    'ig_out': {'id': next(ids), 'payment': 'de_group', 'deleted': False, 'group': None},
    'i_bulk': {'id': next(ids), 'payment': 'bd_installment', 'deleted': False, 'group': None},
}
for number, row in enumerate(INSTALLMENTS.values(), start=1):
    row.update(cs='cs_installments', number=number)
INSTALLMENTS['i_pf'] = {'id': next(ids), 'payment': 'pf_installment', 'deleted': False, 'group': None, 'cs': 'cs_pf_parent', 'number': 1}
HOLDINGS = {'h_linked': {'id': next(ids), 'payment': 'de_plain'},
            'h_pf': {'id': next(ids), 'payment': 'pf_carnet', 'subscription': True, 'courses': ['cs_pf_carnet']},
            'h_pf_null': {'id': next(ids), 'payment': 'pf_carnet_null', 'subscription': True, 'carnet': False, 'courses': ['cs_pf_null']}}
FLAG = {'id': next(ids), 'association': next(ids), 'username': 'payment-state-flag', 'email': 'payment-state-flag@example.test',
        'first': 'Flavia', 'last': 'Fiscale', 'denomination': 'Associazione flag è', 'starting_number': 40}
FLAG_ASSOCIATE = {'id': next(ids), 'first': 'Giulia', 'last': 'Verdi'}
PAYMENTS['pf_flag'] = payment(subject=0, amount='26.00', associate='flag', association=FLAG['association'], category=None,
                              description='Quota flag')


def invoice_row(label, number):
    row = PAYMENTS[label]
    return {'id': next(ids), 'payment': label, 'number': number, 'created': '2026-02-01T10:00:00Z', 'membership_fee': '0.00',
            'activity_fee': row['amount'], 'description': f'Ricevuta {number}'}


# Archived: the paid flow's invoice numbering skips them.
INVOICES = {key: invoice_row(label, number) for number, (key, label) in enumerate(
    ((row['invoice'], label) for label, row in PAYMENTS.items() if row['invoice']), start=901)}
STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=()):
    case = {'name': f'payment-state-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'prints': list(prints)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# --- rendering -------------------------------------------------------------

def associate():
    row = ASSOCIATE
    return {'associate_id': row['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': row['born'],
            'email': row['email'], 'sex': row['sex'], 'first_name': row['first'], 'last_name': row['last'], 'is_minor': False,
            'sport_association': OWN, 'tax_code': row['tax'], 'user': OWNER, 'phone': None, 'family': None, 'family_role': None}


def instructor():
    row = INSTRUCTOR
    return {'instructor_id': row['id'], 'user': OWNER, 'first_name': row['first_name'], 'last_name': row['last_name'], 'email': row['email'],
            'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row['tax_code'], 'address_city': None, 'address': None,
            'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore', 'phone': None, 'phone_2': None,
            'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None, 'is_volunteer': False, 'stipulated_contract_in': None,
            'default_hourly_billing': row['default_hourly_billing'], 'default_percentage_billing': '0.00', 'documents': []}


def supplier():
    row = SUPPLIER
    return {'supplier_id': row['id'], 'name': row['name'], 'address': None, 'tax_code': None, 'sport_association': OWN,
            'vat_number': row['vat_number'], 'type': 'supplier', 'email': row['email'], 'phone_number': None, 'city': None, 'cap': None,
            'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}


def flag_associate():
    row = FLAG_ASSOCIATE
    return {'associate_id': row['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': '1991-02-03',
            'email': None, 'sex': 'F', 'first_name': row['first'], 'last_name': row['last'], 'is_minor': False,
            'sport_association': FLAG['association'], 'tax_code': None, 'user': FLAG['id'], 'phone': None, 'family': None, 'family_role': None}


def payment_item(label, invoice=None):
    """PaymentSerializer of the payment's current authored state."""
    row = STATE[label]
    attachments = [{'document_id': DOCUMENT['id'], 'creation_date': DOCUMENT['created'], 'filepath': DOCUMENT['filepath'],
                    'filename': DOCUMENT['filename'], 'file_size_bytes': DOCUMENT['size'], 'token': DOCUMENT['token']}] if row['attachment'] else []
    category = row['category']
    names = {None: None, CATEGORY: CATEGORY_NAME, '$default_category': '$default_category_name'}
    # get_subscription_id: subject 0 looks the associate's oldest live subscription up; the authored
    # subject 1 and 2 payments have no linked subscription, installment, course subscription or carnet.
    subscription = SUBSCRIPTION['id'] if (row['subject'] == 0 and row['associate'] is True) or row['linked'] else None
    person = (flag_associate() if row['associate'] == 'flag' else associate()) if row['associate'] else None
    return {'payment_id': row['id'], 'user': OWNER_USER if row['user'] else None, 'associate': person,
            'subscription_id': subscription, 'instructor_id': INSTRUCTOR['id'] if row['instructor'] else None,
            'instructor': instructor() if row['instructor'] else None, 'supplier': supplier() if row['supplier'] else None,
            'supplier_id': SUPPLIER['id'] if row['supplier'] else None, 'custom_account_type': None, 'custom_account_name': None,
            'invoice': invoice if invoice is not None else invoice_item(row['invoice']), 'attachments': attachments, 'payment_category_name': names[category], 'type': 'default',
            'subject': row['subject'], 'amount': row['amount'], 'creation_date': row['created'], 'payment_date': row['payment_date'],
            'paid': row['paid'], 'expense': row['expense'], 'description': row['description'],
            'meta_payment_categories': row['meta_categories'],
            'payment_intent_id': None, 'archived': row['archived'], 'invoice_fiscal_code': None, 'meta': row['meta'], 'notes': None,
            'deleted': row['deleted'], 'course': None, 'payment_category': category, 'sport_association': row['association'],
            'custom_accounts': None, 'signature': None, 'group': IDS[row['group']] if row['group'] else None}


def invoice_item(key):
    """InvoiceSerializer of a seeded invoice in its current authored state (None without one)."""
    if key is None or key == '$invoice':
        return None
    row = INVOICES[key]
    return {'invoice_id': row['id'], 'document_token': None, 'creation_date': row['created'], 'membership_fee': row['membership_fee'],
            'activity_fee': row['activity_fee'], 'meta_payment_categories': None, 'description': row['description'], 'number': row['number'],
            'archived': True, 'cancelled': row.get('cancelled', False), 'meta': row.get('meta'), 'imported_from_associami': False,
            'deleted': False, 'document_pdf': None, 'sport_association': OWN, 'selected_tutor': None, 'group': None, 'attached_documents': []}


def invoice_meta(label):
    """The view's payment_obj: PaymentSerializer values, the nested user and associate as str() of their dicts
    (AssociateSerializer's sport_association and family relations are uuid.UUID objects)."""
    item = payment_item(label)
    person = item['associate']
    if person is not None:
        person = {**person, 'sport_association': uuid.UUID(person['sport_association']),
                  'family': uuid.UUID(person['family']) if person['family'] else None}
    return {'payment_id': item['payment_id'], 'user': str(item['user']), 'associate': str(person), 'subscription': None,
            'subject': item['subject'], 'amount': item['amount'], 'creation_date': item['creation_date'], 'payment_date': item['payment_date']}


def cancel_invoice(label):
    """invoice.cancelled = True, invoice.meta = payment_obj, invoice.save(): (stored changes, audit entry)."""
    key = STATE[label]['invoice']
    row = INVOICES[key]
    meta = invoice_meta(label)
    row['cancelled'], row['meta'] = True, meta
    return ({key: {'cancelled': True, 'meta': meta}},
            updated('invoice', key, {'cancelled': ['False', 'True'], 'meta': ['null', json.dumps(meta, sort_keys=True)]}))


def print_request(key):
    return {'path': f"/document/invoice/{INVOICES[key]['id']}?send_receipt_email=False", 'reply': {'status': 200, 'body': 'printed'}}


# --- effects ---------------------------------------------------------------

PAYMENT_INDEX = [OWN, 'changes_json.sport_association']
DIRECT = [OWN, 'direct']


def actor_index(actor):
    """Delete entries without a sport_association resolve through the actor's own association."""
    return {'owner': [OWN, 'actor.sport_association'], 'admin': [IDS['admin_association'], 'actor.sport_association']}.get(actor)


def effect(actor, *, changed=None, deleted=(), installments=(), invoices=None, audit=()):
    return {'actor': actor, 'payments_changed': changed or {}, 'payments_deleted': list(deleted), 'installments_deleted': list(installments),
            'supplier_invoices_changed': invoices or {}, 'invoices_changed': {}, 'audit': list(audit)}


def updated(model, label, changes, index=DIRECT):
    return {'model': model, 'label': label, 'action': 1, 'changes': changes, 'index': index}


def removed(model, label, index):
    """A delete entry: every stored field of the row before the request (captured by the runner)."""
    return {'model': model, 'label': label, 'action': 2, 'changes': None, 'index': index}


def audit_moment(text):
    """auditlog's str() of a DateTimeField: naive UTC."""
    return text.replace('T', ' ').replace('Z', '') if text != '$now' else '$now'


def save(label, columns):
    """Payment.save() of the authored columns: (STATE update, audit changes)."""
    row = STATE[label]
    changes = {}
    for name, value in columns.items():
        old = row[name]
        if name == 'paid':
            changes['paid'] = [str(old), str(value)]
        elif name == 'payment_date':
            changes['payment_date'] = ['None' if old is None else audit_moment(old), audit_moment(value)]
        elif name == 'category':
            changes['payment_category'] = ['None' if old is None else old, value]
        elif name == 'description':
            changes['description'] = ['None' if old is None else old, value]
        row[name] = value
    return changes


def delete_payment(actor, label, group=None):
    """payment.delete(): the pre_delete signal deletes the live group-aware installments
    (key order); the first one's pre_delete deletes the still unpaid payment (nested
    between them); the installments are deleted in reverse key order, then the payment."""
    row = STATE[label]
    gone = sorted((k for k, i in INSTALLMENTS.items() if i['payment'] == label and not i['deleted'] and not i.get('gone')
                   and (group is None or i['group'] == group)), key=lambda k: INSTALLMENTS[k]['id'])
    audit = []
    if gone and not row['paid']:
        audit.append(removed('payment', label, PAYMENT_INDEX))
    audit += [removed('coursesubscriptioninstallment', key, actor_index(actor)) for key in reversed(gone)]
    audit.append(removed('payment', label, PAYMENT_INDEX))
    for key in gone:
        INSTALLMENTS[key]['gone'] = True
    row['gone'] = True
    return effect(actor, deleted=[label], installments=gone, audit=audit)


def nothing(actor='owner'):
    return effect(actor)


DENIED = {'detail': 'User not allowed.'}
NOT_FOUND = {'msg': 'Payment not found.'}
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
P = lambda label: PAYMENTS[label]['id'] if label in PAYMENTS else label
one = lambda label, verb: f'/payment/{P(label)}/{verb}'
APPROVE = lambda label: one(label, 'approve')
CANCEL = lambda label: one(label, 'cancel')
DELETE = lambda label: one(label, 'delete')
BULK_ARCHIVE, BULK_DELETE = '/payment-bulk/archive', '/payment-bulk/delete'
missing = lambda key: {'detail': f'Missing permission: bookeeping.payments.{key}'}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'], 'parses': PARSES}


def guards(prefix, path, verb, permission, name, description=''):
    other = 'GET'
    add(f'{prefix}-anonymous', 'anonymous', path, 401, ANONYMOUS, method=verb)
    add(f'{prefix}-restricted-missing-permission', 'restricted_collaborator', path, 403, missing(permission), method=verb)
    add(f'{prefix}-admin-without-session', 'admin', path, 403, ADMIN, method=verb)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', path, 403, SCOPED_DENIED, method=verb, headers=SCOPED)
    add(f'{prefix}-wrong-method', 'owner', path, 405, method(other), method=other)
    add(f'{prefix}-options', 'owner', path, 200, options(name, description), method='OPTIONS')


# --- approve -----------------------------------------------------------------

guards('approve', APPROVE('ap_plain'), 'POST', 'update', 'Payment Approve')
add('approve-malformed', 'owner', APPROVE('abc'), 400, MALFORMED)
add('approve-athlete-server-error', 'athlete', APPROVE('ap_plain'), 500, SERVER)
add('approve-owner-without-association-server-error', 'owner_without_association', APPROVE('ap_plain'), 500, SERVER)
add('approve-list-body-server-error', 'owner', APPROVE('ap_plain'), 500, SERVER, body=['payment_date'])
add('approve-bad-date-server-error', 'owner', APPROVE('ap_plain'), 500, SERVER, body={'payment_date': '05/01/2026'})
add('approve-blank-date-server-error', 'owner', APPROVE('ap_plain'), 500, SERVER, body={'payment_date': ''})
add('approve-number-date-server-error', 'owner', APPROVE('ap_plain'), 500, SERVER, body={'payment_date': 20260105})
add('approve-missing-server-error', 'owner', APPROVE(IDS['missing']), 500, SERVER)
add('approve-deleted-server-error', 'owner', APPROVE('ap_removed'), 500, SERVER)
add('approve-other-group-server-error', 'owner', APPROVE('ap_group'), 500, SERVER, headers=OTHER_GROUP)
add('approve-foreign-denied', 'owner', APPROVE('abroad'), 403, DENIED)
add('approve-scoped-session-foreign-server-error', 'owner', APPROVE('abroad'), 500, SERVER, headers=OWNER_FULL)
# Payment.save() raises for an account transfer without a description: the atomic block rolls back.
add('approve-transfer-without-description-server-error', 'owner', APPROVE('ap_transfer'), 500, SERVER)

changes = save('ap_plain', {'paid': True, 'payment_date': '$now'})
add('approve-unpaid-now', 'owner', APPROVE('ap_plain'), 200, {'data': {'payment': payment_item('ap_plain')}},
    effect=effect('owner', changed={'ap_plain': {'paid': True, 'payment_date': '$now'}}, audit=[updated('payment', 'ap_plain', changes)]))
add('approve-paid-answers-message', 'owner', APPROVE('ap_plain'), 200,
    {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ap_plain')}}, body={'payment_date': '2020-01-01'}, effect=nothing())
changes = save('ap_dated', {'paid': True, 'payment_date': '2026-03-04T00:00:00Z'})
add('approve-sent-date', 'owner', APPROVE('ap_dated'), 200, {'data': {'payment': payment_item('ap_dated')}},
    body={'payment_date': '2026-03-04', 'generate_invoice': True, 'send_receipt_email': True},
    effect=effect('owner', changed={'ap_dated': {'paid': True, 'payment_date': '2026-03-04T00:00:00Z'}},
                  audit=[updated('payment', 'ap_dated', changes)]))
changes = save('ap_stored', {'paid': True})
add('approve-keeps-stored-date', 'owner', APPROVE('ap_stored'), 200, {'data': {'payment': payment_item('ap_stored')}},
    body={'payment_date': None}, effect=effect('owner', changed={'ap_stored': {'paid': True}}, audit=[updated('payment', 'ap_stored', changes)]))
changes = save('ap_defaults', {'paid': True, 'payment_date': '2026-07-08T00:00:00Z', 'category': '$default_category', 'description': 'Iscrizione'})
add('approve-save-defaults', 'owner', APPROVE('ap_defaults'), 200, {'data': {'payment': payment_item('ap_defaults')}},
    body={'payment_date': '2026-7-8'},
    effect=effect('owner', changed={'ap_defaults': {'paid': True, 'payment_date': '2026-07-08T00:00:00Z', 'category': '$default_category',
                                                    'description': 'Iscrizione'}}, audit=[updated('payment', 'ap_defaults', changes)]))
changes = save('ap_supplier', {'paid': True, 'payment_date': '2026-03-05T00:00:00Z'})
add('approve-expense-pays-supplier-invoice', 'owner', APPROVE('ap_supplier'), 200, {'data': {'payment': payment_item('ap_supplier')}},
    body={'payment_date': '2026-03-05'},
    effect=effect('owner', changed={'ap_supplier': {'paid': True, 'payment_date': '2026-03-05T00:00:00Z'}},
                  invoices={'si_approve': {'paid': True, 'payment_date': '2026-03-05T00:00:00Z'}},
                  audit=[updated('payment', 'ap_supplier', changes),
                         updated('invoicesuppliers', 'si_approve', {'paid': ['False', 'True'], 'payment_date': ['None', '2026-03-05 00:00:00']})]))
changes = save('ap_instructor', {'paid': True, 'payment_date': '$now'})
add('approve-expense-instructor-now', 'owner', APPROVE('ap_instructor'), 200, {'data': {'payment': payment_item('ap_instructor')}},
    effect=effect('owner', changed={'ap_instructor': {'paid': True, 'payment_date': '$now'}}, audit=[updated('payment', 'ap_instructor', changes)]))
add('approve-paid-with-party', 'owner', APPROVE('ap_paid'), 200, {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ap_paid')}},
    effect=nothing())
changes = save('ap_collab', {'paid': True, 'payment_date': '2026-03-06T00:00:00Z'})
add('approve-full-collaborator', 'full_collaborator', APPROVE('ap_collab'), 200, {'data': {'payment': payment_item('ap_collab')}},
    body={'payment_date': '2026-03-06'}, effect=effect('full_collaborator', changed={'ap_collab': {'paid': True, 'payment_date': '2026-03-06T00:00:00Z'}},
                                                       audit=[updated('payment', 'ap_collab', changes)]))
changes = save('ap_admin', {'paid': True, 'payment_date': '2026-03-07T00:00:00Z'})
add('approve-admin-impersonating-owner', 'admin', APPROVE('ap_admin'), 200, {'data': {'payment': payment_item('ap_admin')}},
    body={'payment_date': '2026-03-07'}, headers=ADMIN_OWNER,
    effect=effect('admin', changed={'ap_admin': {'paid': True, 'payment_date': '2026-03-07T00:00:00Z'}}, audit=[updated('payment', 'ap_admin', changes)]))
changes = save('ap_group', {'paid': True, 'payment_date': '2026-03-08T00:00:00Z'})
add('approve-group', 'owner', APPROVE('ap_group'), 200, {'data': {'payment': payment_item('ap_group')}}, body={'payment_date': '2026-03-08'},
    headers=GROUP, effect=effect('owner', changed={'ap_group': {'paid': True, 'payment_date': '2026-03-08T00:00:00Z'}},
                                 audit=[updated('payment', 'ap_group', changes)]))
changes = save('ap_form', {'paid': True, 'payment_date': '2026-03-09T00:00:00Z'})
add('approve-form-body', 'owner', APPROVE('ap_form'), 200, {'data': {'payment': payment_item('ap_form')}},
    raw_body='payment_date=2026-03-09&generate_invoice=false', content_type='application/x-www-form-urlencoded',
    effect=effect('owner', changed={'ap_form': {'paid': True, 'payment_date': '2026-03-09T00:00:00Z'}}, audit=[updated('payment', 'ap_form', changes)]))

# --- approve: paid flow --------------------------------------------------------
# An unpaid income payment with an associate, supplier or instructor: the fees
# (subscription payments sum their meta payment categories as floats), the next
# invoice number by payment date (InvoiceService, use_payment_date=True: the
# association's live, group-aware, unarchived, uncancelled invoices joined to a
# payment dated in / before the current balance year), the description
# (generate_invoice_description), Invoice.objects.create (associate or supplier
# only) and invoice.save() (creation_date = payment date for owners with
# payment_date_equal_invoice_date; the float fees differ from the stored
# decimals), payment.save(), the first supplier invoice paid now, the course
# subscription paid flags (subject 2: the payment's course subscription, else
# the first installment's course subscription, else the first carnet
# subscription's course subscriptions), then send_notification(request.user):
# validate_broadcast's rows, the Redis list item and the Channels push. The
# response carries the message; print_document_invoice runs on commit when the
# payment has an invoice and generate_invoice is truthy.

DENOMINATION = 'Message reads own'
NUMBERS = {'year': 0, 'old': 0, 'group': 0, 'flag': FLAG['starting_number']}
ACTOR_INDEX = {'owner': [OWN, 'actor.sport_association'], 'admin': [IDS['admin_association'], 'actor.sport_association'],
               'flag_owner': [FLAG['association'], 'actor.sport_association'], 'full_collaborator': None}


def escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;'))


def pyfloat(value):
    return repr(float(value))


def drf(value):
    from decimal import Decimal, ROUND_HALF_EVEN
    return str(Decimal(repr(float(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN))


def paid_fees(row):
    """((membership str(), stored), (activity str(), stored)) of the approve view."""
    amount = row['amount']
    if row['subject'] == 1:
        activity = 0.0
        for item in row['meta_categories'] or []:
            if item['amount']:
                activity = activity + float(str(item['amount']))
        membership = float(amount) - activity if float(amount) > 0 else None
        return ((pyfloat(membership), drf(membership)) if membership is not None else (amount, amount)), (pyfloat(activity), drf(activity))
    return ('0.0', '0.00'), (amount, amount)


def person_name(row):
    if row['associate'] == 'flag':
        return f"{FLAG_ASSOCIATE['first']} {FLAG_ASSOCIATE['last']}"
    return f"{ASSOCIATE['first']} {ASSOCIATE['last']}"


def paid_flow(name, label, *, description, notified, actor='owner', body=None, raw_body=None, content_type=None, headers=None, sent=None,
              number_key='year', generate=True, receipt=False, broadcast=False, courses=(), supplier_invoice=None, group=None):
    row = STATE[label]
    association = row['association']
    flag = association == FLAG['association']
    payment_date = sent or row['payment_date'] or '$now'
    (membership_audit, membership), (activity_audit, activity) = paid_fees(row)
    creates = bool(row['associate'] or row['supplier'])
    audit, invoice, stored = [], None, None
    if creates:
        if number_key == 'group':
            NUMBERS['group'] += 1
            number = NUMBERS['group']
            NUMBERS['year'] = max(NUMBERS['year'], number)
        else:
            NUMBERS[number_key] += 1
            number = NUMBERS[number_key]
        created = payment_date if flag else '$now'
        meta = row['meta_categories']
        invoice = {'invoice_id': '$invoice', 'document_token': None, 'creation_date': created, 'membership_fee': membership,
                   'activity_fee': activity, 'meta_payment_categories': meta, 'description': description, 'number': number, 'archived': False,
                   'cancelled': False, 'meta': None, 'imported_from_associami': False, 'deleted': False, 'document_pdf': None,
                   'sport_association': association, 'selected_tutor': None, 'group': IDS[group] if group else None, 'attached_documents': []}
        create = {'invoice_id': ['None', '$invoice'], 'document_pdf': ['None', 'None'], 'sport_association': ['None', association],
                  'creation_date': ['None', '$now'], 'membership_fee': ['None', membership_audit], 'activity_fee': ['None', activity_audit],
                  'meta_payment_categories': ['None', json.dumps(meta, sort_keys=True)],
                  'description': ['None', description], 'number': ['None', str(number)], 'archived': ['None', 'False'],
                  'cancelled': ['None', 'False'], 'selected_tutor': ['None', 'None'], 'meta': ['None', 'null'],
                  'imported_from_associami': ['None', 'False'], 'group': ['None', IDS[group] if group else 'None'], 'deleted': ['None', 'False']}
        audit.append({'model': 'invoice', 'label': '$invoice', 'action': 0, 'changes': create, 'index': [association, 'direct']})
        update = {}
        if flag:
            update['creation_date'] = ['$now', audit_moment(payment_date)]
        if membership != membership_audit:
            update['membership_fee'] = [membership, membership_audit]
        if activity != activity_audit:
            update['activity_fee'] = [activity, activity_audit]
        if update:
            audit.append({'model': 'invoice', 'label': '$invoice', 'action': 1, 'changes': update, 'index': [association, 'direct']})
        stored = {'sport_association_id': association, 'creation_date': created, 'membership_fee': membership, 'activity_fee': activity,
                  'meta_payment_categories': meta, 'description': description, 'number': number, 'group_id': IDS[group] if group else None}
    columns = {'paid': True, 'payment_date': payment_date}
    if creates:
        columns['invoice'] = '$invoice'
    if row['category'] is None:
        columns['category'] = '$default_category'
    changes = save(label, {k: v for k, v in columns.items() if k != 'invoice'})
    if creates:
        changes['invoice'] = ['None', '$invoice']
    audit.append(updated('payment', label, changes, [association, 'direct']))
    invoices = {}
    if supplier_invoice:
        invoices[supplier_invoice] = {'paid': True, 'payment_date': '$now'}
        audit.append(updated('invoicesuppliers', supplier_invoice, {'paid': ['False', 'True'], 'payment_date': ['None', '$now']}))
    for course in courses:
        audit.append(updated('coursesubscription', course, {'paid': ['False', 'True']}, [OWN, 'course.sport_association']))
    if broadcast:
        audit += [{'model': model, 'label': '$' + model, 'action': 0, 'changes': '$created', 'index': ACTOR_INDEX[actor]}
                  for model in ('notificationsbroadcast', 'usernotificationsbroadcast')]
    msg = f"Pagamento di € {row['amount'].replace('.', ',')}" + (f"per {notified}" if notified else '') + '.'
    if row['associate']:
        msg += f" Pagamento dell'associato {person_name(row)}."
    elif row['user']:
        msg += f" Pagamento dell'utente {OWNER_USER['first_name']} {OWNER_USER['last_name']}."
    recipient = 'flag_owner' if flag else 'owner'
    item = payment_item(label, invoice)
    row['invoice'] = '$invoice' if creates else None
    dispatch = creates and generate
    if dispatch:
        item['invoice_generating'] = True
    celery = [{'task': 'print_document_invoice', 'args': ['$invoice', '$authorization', receipt], 'kwargs': [], 'args_list': True}] if dispatch else []
    result = effect(actor, changed={label: columns}, invoices=invoices, audit=audit)
    result.update(invoice=stored, course_subscriptions_paid=list(courses), celery=celery,
                  notification={'user': recipient, 'msg': msg, 'association': association})
    add(name, actor if actor != 'flag_owner' else 'flag_owner', APPROVE(label), 200, {'data': {'msg': 'payment mark as payed.', 'payment': item}},
        body=body, raw_body=raw_body, content_type=content_type, headers=headers, effect=result)


def other_text(reason, denomination=DENOMINATION):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(denomination)}</b>."


def course_text(title):
    return (f"Ricevuta di pagamento di <b>{escape(person_name({'associate': True}))}</b> per il corso <b>{escape(title)}</b> "
            f"dell'associazione <b>{escape(DENOMINATION)}</b>.")


paid_flow('approve-paid-flow-other-now', 'pf_other', description=other_text(CATEGORY_NAME), notified='Quota', broadcast=True)
paid_flow('approve-paid-flow-old-date-no-print', 'pf_old', description=other_text(CATEGORY_NAME), notified='Quota', sent='2020-02-03T00:00:00Z',
          number_key='old', generate=False, body={'payment_date': '2020-02-03', 'generate_invoice': False})
paid_flow('approve-paid-flow-subscription-fees', 'pf_subscription', notified="l'iscrizione all'associazione sportiva", receipt=True,
          description=f"Ricevuta di pagamento per l'iscrizione di <b>{escape(person_name({'associate': True}))}</b> all'associazione "
                      f"<b>{escape(DENOMINATION)}</b>.", body={'send_receipt_email': True})
paid_flow('approve-paid-flow-course', 'pf_course', description=course_text(COURSE['title']), notified=f'il corso "{COURSE["title"]}"',
          courses=['cs_pf'])
paid_flow('approve-paid-flow-installment-course', 'pf_installment', description=course_text(COURSE['title']),
          notified=f'il corso "{COURSE["title"]}"', courses=['cs_pf_parent'])
paid_flow('approve-paid-flow-carnet', 'pf_carnet', description=course_text(CARNET['title']), notified=f"carnet {CARNET['title']}",
          courses=['cs_pf_carnet'])
paid_flow('approve-paid-flow-course-without-link', 'pf_course_none', description='Quota', notified='il corso')
paid_flow('approve-paid-flow-supplier', 'pf_supplier', notified='Quota', supplier_invoice='si_paid_flow',
          description=f"Ricevuta di pagamento di <b>{escape(SUPPLIER['name'])}</b> con causale <b>Quota</b> per l'associazione "
                      f"<b>{escape(DENOMINATION)}</b>.")
paid_flow('approve-paid-flow-instructor-without-invoice', 'pf_instructor', description=None, notified='Quota')
paid_flow('approve-paid-flow-full-collaborator', 'pf_collab', actor='full_collaborator', description=other_text(CATEGORY_NAME), notified='Quota')
paid_flow('approve-paid-flow-admin-impersonating-owner', 'pf_admin', actor='admin', headers=ADMIN_OWNER, description=other_text(CATEGORY_NAME),
          notified='Quota')
paid_flow('approve-paid-flow-group', 'pf_group', headers=GROUP, number_key='group', group='own_group', description='Quota', notified='il corso')
paid_flow('approve-paid-flow-form-body', 'pf_form', description=other_text(CATEGORY_NAME), notified='Quota', sent='2020-02-04T00:00:00Z',
          number_key='old', receipt='yes', raw_body='payment_date=2020-02-04&send_receipt_email=yes&generate_invoice=1',
          content_type='application/x-www-form-urlencoded')
paid_flow('approve-paid-flow-flag-owner-invoice-date', 'pf_flag', actor='flag_owner', description=other_text('Quota flag', FLAG['denomination']),
          notified='Quota flag', sent='2021-03-04T00:00:00Z', number_key='flag', broadcast=True, body={'payment_date': '2021-03-04'})
paid_flow('approve-paid-flow-scoped-session', 'pf_scoped', headers=OWNER_FULL, description=course_text(COURSE['title']),
          notified=f'il corso "{COURSE["title"]}"', courses=['cs_pf_scoped'])
# float(str('abc')) raises before any write; a carnet subscription without a carnet fails in the description: rolled back.
add('approve-paid-flow-bad-meta-amount-server-error', 'owner', APPROVE('pf_bad_meta'), 500, SERVER)
add('approve-paid-flow-carnet-without-carnet-server-error', 'owner', APPROVE('pf_carnet_null'), 500, SERVER)

# --- cancel ------------------------------------------------------------------

guards('cancel', CANCEL('ca_plain'), 'POST', 'update', 'Payment Cancel')
add('cancel-malformed-server-error', 'owner', CANCEL('abc'), 500, SERVER)
add('cancel-athlete-server-error', 'athlete', CANCEL('ca_plain'), 500, SERVER)
add('cancel-owner-without-association-server-error', 'owner_without_association', CANCEL('ca_plain'), 500, SERVER)
add('cancel-missing-server-error', 'owner', CANCEL(IDS['missing']), 500, SERVER)
add('cancel-deleted-server-error', 'owner', CANCEL('ca_removed'), 500, SERVER)
add('cancel-foreign-denied', 'owner', CANCEL('abroad'), 403, DENIED)
add('cancel-scoped-session-foreign-server-error', 'owner', CANCEL('abroad'), 500, SERVER, headers=OWNER_FULL)
add('cancel-unpaid', 'owner', CANCEL('ca_unpaid'), 400, {'error': 'Payment not paid.'}, effect=nothing())
add('cancel-transfer-without-description-server-error', 'owner', CANCEL('ca_transfer'), 500, SERVER)


def cancel(name, actor, label, *, headers=None, extra=None):
    """The response is the payment serialized before the save."""
    response = {'data': {'msg': 'payment cancelled.', 'payment': payment_item(label)}}
    columns = {'paid': False, **(extra or {})}
    changes = save(label, columns)
    add(name, actor, CANCEL(label), 200, response, headers=headers,
        effect=effect(actor, changed={label: columns}, audit=[updated('payment', label, changes)]))


cancel('cancel-paid', 'owner', 'ca_plain')
add('cancel-again-unpaid', 'owner', CANCEL('ca_plain'), 400, {'error': 'Payment not paid.'}, effect=nothing())
cancel('cancel-fills-category-after-serializing', 'owner', 'ca_uncategorized', extra={'category': '$default_category'})
cancel('cancel-expense-instructor', 'owner', 'ca_instructor')
cancel('cancel-expense-supplier', 'owner', 'ca_supplier')
cancel('cancel-full-collaborator', 'full_collaborator', 'ca_collab')
cancel('cancel-admin-impersonating-owner', 'admin', 'ca_admin', headers=ADMIN_OWNER)
cancel('cancel-after-approve', 'owner', 'ap_dated')
add('cancel-invoiced-unpaid', 'owner', CANCEL('ca_invoiced_unpaid'), 400, {'error': 'Payment not paid.'}, effect=nothing())


def cancel_invoiced(name, actor, label, *, headers=None):
    """The response is the payment serialized after the save, its invoice cancelled in memory with the stored meta."""
    key = STATE[label]['invoice']
    changes = save(label, {'paid': False})
    item = payment_item(label, {**invoice_item(key), 'cancelled': True})
    invoices, entry = cancel_invoice(label)
    result = effect(actor, changed={label: {'paid': False}}, audit=[updated('payment', label, changes), entry])
    result.update(invoices_changed=invoices, celery=[{'task': 'print_document_invoice', 'args': [INVOICES[key]['id'], '$authorization'],
                                                      'kwargs': [], 'args_list': True}])
    add(name, actor, CANCEL(label), 200, {'data': {'msg': 'payment cancelled.', 'payment': item}}, headers=headers, effect=result)


cancel_invoiced('cancel-invoiced-publishes-print', 'owner', 'ca_invoiced')
cancel_invoiced('cancel-invoiced-without-party-full-collaborator', 'full_collaborator', 'ca_invoiced_plain')
add('cancel-invoiced-again-unpaid', 'owner', CANCEL('ca_invoiced'), 400, {'error': 'Payment not paid.'}, effect=nothing())

# --- delete ------------------------------------------------------------------

guards('delete', DELETE('de_plain'), 'DELETE', 'delete', 'Payment Delete', 'API endpoint to add a new payment')
add('delete-malformed', 'owner', DELETE('abc'), 400, MALFORMED, method='DELETE')
add('delete-athlete-server-error', 'athlete', DELETE('de_plain'), 500, SERVER, method='DELETE')
add('delete-owner-without-association-server-error', 'owner_without_association', DELETE('de_plain'), 500, SERVER, method='DELETE')
add('delete-missing-not-found', 'owner', DELETE(IDS['missing']), 404, NOT_FOUND, method='DELETE')
add('delete-foreign-not-found', 'owner', DELETE('abroad'), 404, NOT_FOUND, method='DELETE')
add('delete-deleted-not-found', 'owner', DELETE('de_removed'), 404, NOT_FOUND, method='DELETE')
add('delete-other-group-not-found', 'owner', DELETE('de_group'), 404, NOT_FOUND, method='DELETE', headers=OTHER_GROUP)
DELETED = {'data': {'msg': 'payment deleted.'}}
add('delete-clears-references', 'owner', DELETE('de_plain'), 200, DELETED, method='DELETE', effect=delete_payment('owner', 'de_plain'))
add('delete-unpaid-installments', 'owner', DELETE('de_installments'), 200, DELETED, method='DELETE',
    effect=delete_payment('owner', 'de_installments'))
add('delete-paid-installment', 'owner', DELETE('de_paid'), 200, DELETED, method='DELETE', effect=delete_payment('owner', 'de_paid'))
add('delete-group-installments', 'owner', DELETE('de_group'), 200, DELETED, method='DELETE', headers=GROUP,
    effect=delete_payment('owner', 'de_group', 'own_group'))
add('delete-full-collaborator', 'full_collaborator', DELETE('de_collab'), 200, DELETED, method='DELETE',
    effect=delete_payment('full_collaborator', 'de_collab'))
add('delete-admin-impersonating-owner', 'admin', DELETE('de_admin'), 200, DELETED, method='DELETE', headers=ADMIN_OWNER,
    effect=delete_payment('admin', 'de_admin'))
add('delete-again-not-found', 'owner', DELETE('de_plain'), 404, NOT_FOUND, method='DELETE')


def delete_invoiced(name, actor, label, headers=None):
    key = STATE[label]['invoice']
    invoices, entry = cancel_invoice(label)
    result = delete_payment(actor, label)
    result.update(invoices_changed=invoices, audit=[entry, *result['audit']])
    add(name, actor, DELETE(label), 200, DELETED, method='DELETE', headers=headers, effect=result, prints=[print_request(key)])


delete_invoiced('delete-invoiced-prints-before-delete', 'owner', 'de_invoiced')
delete_invoiced('delete-invoiced-full-collaborator', 'full_collaborator', 'de_invoiced_collab')
add('approve-hard-deleted-server-error', 'owner', APPROVE('de_paid'), 500, SERVER)


# --- bulk archive / bulk delete ----------------------------------------------

def bulk(prefix, path, column, message, permission, name):
    guards(prefix, path, 'POST', permission, name)
    add(f'{prefix}-athlete-server-error', 'athlete', path, 500, SERVER, body={'payment_ids': []})
    add(f'{prefix}-owner-without-association-server-error', 'owner_without_association', path, 500, SERVER, body={'payment_ids': []})
    add(f'{prefix}-missing-key-server-error', 'owner', path, 500, SERVER, body={})
    add(f'{prefix}-no-body-server-error', 'owner', path, 500, SERVER)
    add(f'{prefix}-list-body-server-error', 'owner', path, 500, SERVER, body=['payment_ids'])
    add(f'{prefix}-null-ids-server-error', 'owner', path, 500, SERVER, body={'payment_ids': None})
    add(f'{prefix}-number-ids-server-error', 'owner', path, 500, SERVER, body={'payment_ids': 7})
    add(f'{prefix}-malformed-id-server-error', 'owner', path, 500, SERVER, body={'payment_ids': [PAYMENTS[f'{prefix[:1]}{prefix[5]}_a']['id'], 'abc']})
    add(f'{prefix}-float-id-server-error', 'owner', path, 500, SERVER, body={'payment_ids': [1.5]})
    add(f'{prefix}-negative-id-server-error', 'owner', path, 500, SERVER, body={'payment_ids': [-1]})
    add(f'{prefix}-nested-id-server-error', 'owner', path, 500, SERVER, body={'payment_ids': [[PAYMENTS['abroad']['id']]]})
    add(f'{prefix}-string-ids-server-error', 'owner', path, 500, SERVER, body={'payment_ids': PAYMENTS['abroad']['id']})
    add(f'{prefix}-form-id-server-error', 'owner', path, 500, SERVER, raw_body=f'payment_ids={PAYMENTS["abroad"]["id"]}',
        content_type='application/x-www-form-urlencoded')
    ok = {'message': message}
    add(f'{prefix}-empty-string', 'owner', path, 200, ok, body={'payment_ids': ''}, effect=nothing())
    add(f'{prefix}-integer-ids', 'owner', path, 200, ok, body={'payment_ids': [1, 0, True, None]}, effect=nothing())
    add(f'{prefix}-dict-ids', 'owner', path, 200, ok, body={'payment_ids': {}}, effect=nothing())

    def marked(name, actor, labels, ids_, headers=None):
        changed = {label: {column: True} for label in labels}
        for label in labels:
            STATE[label][column] = True
        add(f'{prefix}-{name}', actor, path, 200, ok, body={'payment_ids': ids_}, headers=headers, effect=effect(actor, changed=changed))
    return marked


marked = bulk('bulk-archive', BULK_ARCHIVE, 'archived', 'All payments archived', 'archive.update', 'Payment Bulk Archive')
marked('owner', 'owner', ['ba_a', 'ba_b'],
       [P('ba_a'), P('ba_b').upper(), P('ba_stored'), P('abroad'), P('ba_removed'), IDS['missing'], None, P('ba_a')])
marked('group', 'owner', ['ba_group'], [P('ba_group'), P('ba_plain')], GROUP)
marked('foreign-ignored-in-scoped-session', 'owner', [], [P('abroad')], OWNER_FULL)
marked('full-collaborator', 'full_collaborator', ['ba_collab'], [P('ba_collab')])
marked('admin-impersonating-owner', 'admin', ['ba_plain'], [P('ba_plain')], ADMIN_OWNER)
marked = bulk('bulk-delete', BULK_DELETE, 'deleted', 'All payments deleted', 'delete', 'Payment Bulk Delete')
marked('owner', 'owner', ['bd_a', 'bd_installment'], [P('bd_a'), P('bd_installment'), P('abroad'), P('de_removed'), IDS['missing']])
marked('group', 'owner', ['bd_group'], [P('bd_group'), P('bd_plain')], GROUP)
marked('full-collaborator', 'full_collaborator', ['bd_collab'], [P('bd_collab')])
marked('admin-impersonating-owner', 'admin', ['bd_admin'], [P('bd_admin')], ADMIN_OWNER)
add('bulk-delete-again-ignored', 'owner', BULK_DELETE, 200, {'message': 'All payments deleted'}, body={'payment_ids': [P('bd_a')]},
    effect=nothing())
add('approve-bulk-deleted-server-error', 'owner', APPROVE('bd_a'), 500, SERVER)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Payment approve, cancel, delete and bulk archive/delete authored sequence with '
                                                            'payment, installment, supplier invoice, reference and audit effects',
                              'ids': IDS, 'category': CATEGORY, 'category_name': CATEGORY_NAME, 'document': DOCUMENT, 'associate': ASSOCIATE,
                              'subscription': SUBSCRIPTION, 'instructor': INSTRUCTOR, 'supplier': SUPPLIER, 'course': COURSE, 'carnet': CARNET,
                              'payments': PAYMENTS,
                              'invoices': {k: {f: v for f, v in row.items() if f not in ('cancelled', 'meta')} for k, row in INVOICES.items()},
                              'supplier_invoices': SUPPLIER_INVOICES, 'course_subscriptions': COURSE_SUBSCRIPTIONS,
                              'installments': {k: {key: v for key, v in row.items() if key != 'gone'} for k, row in INSTALLMENTS.items()},
                              'holdings': HOLDINGS, 'flag_owner': FLAG, 'flag_associate': FLAG_ASSOCIATE, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
