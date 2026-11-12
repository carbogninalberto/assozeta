"""Author the invoice routes that run a print task in the request (invoice-sync-print.md).

Both views end with ``<task>.apply(args=[str(id), request.headers.get('authorization')])``:
an eager run of a CoordinatedTask. With DATA_RESTORE_LOCK_PATH set (the harness sets it)
the task takes the shared operation lock, and an eager call (no assozeta_published_at
header) raises StaleDataTask, swallowed by the eager result, when any completed restore
exists; otherwise its body is ``requests.get(CURRENT_HOST + path, headers={'Authorization':
token})`` with every error swallowed. The private stub at CURRENT_HOST records each request.

invoice/<uid>/update (PATCH, registry bookeeping.documents.invoices.update):
is_valid_uuid; ``data.keys()`` (a list or string body is a 500); a missing 'number' is a
500; cancelled defaults to False; payment_date through strptime('%Y-%m-%d') (any error:
None); the scoped association (.filter().first(); None: 500); the scoped, live, group-aware,
unarchived invoice of the association (None: 500); ``int(number)``, ``bool(cancelled)``;
'selected_tutor' present: the first scoped, live, group-aware associate by id within
the invoice's association (UUIDField to_python errors: 500; none: unchanged), absent: None; the first scoped, live, group-aware
payment of the invoice by pk: a truthy payment_date is assigned, payment.save() (defaults,
audited), then generate_invoice_description(payment, association); invoice.save() (audited
before the UPDATE); the print task; InvoiceSerializer of the invoice. Nothing is atomic.

invoice-customers/add (POST, registry bookeeping.documents.clientinvoices.create):
the view's date defaults and conversions, CustomerInvoiceSerializer (validation is the
invoice-parties family's), serializer.save() (create entry from the in-memory values),
invoice.save() (an update entry for what differs from the stored row: the int default 0 of
payment_total_amount, JSON values jsonb renders differently), then generate_invoice_xml and
generate_invoice_json of the in-memory instance and a third save; an exception there is
200 {'error': 'Errore durante la generazione del file XML.'} with the row kept and no task.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('invoice_sync_print_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
GROUP_ID = IDS['own_group']
ids = iter(f'a1000000-0000-4000-8000-{998001 + n:012d}' for n in range(400))
SERVER = '<stable-server-error-page>'
DENOMINATION = 'Message reads own'
DIRECT = 'direct'

CATEGORY = {'id': next(ids), 'name': 'Quote <sync> & co'}
DOCUMENT = {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta.pdf', 'filepath': 'docs/ricevuta-sync.pdf', 'size': 2048,
            'created': '2026-02-03T08:00:00Z'}
ATTACHED = {'id': next(ids), 'token': next(ids), 'filename': 'allegato.pdf', 'filepath': 'docs/allegato-sync.pdf', 'size': 1024,
            'created': '2026-02-04T08:00:00Z'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore <sync>', 'vat_number': '01234567890', 'email': 'fornitore-sync@example.test'}
INSTRUCTOR = {'id': next(ids), 'first': 'Ivo', 'last': 'Rossi & Figli'}

ASSOCIATES = {
    'anna': {'id': next(ids), 'first': 'Anna', 'last': 'Bianchi', 'association': OWN, 'group': None, 'deleted': False},
    'bruno': {'id': next(ids), 'first': 'Bruno', 'last': "O'Neil & C", 'association': OWN, 'group': None, 'deleted': False},
    'carla': {'id': next(ids), 'first': 'Carla', 'last': 'Gialli', 'association': OWN, 'group': GROUP_ID, 'deleted': False},
    'dora': {'id': next(ids), 'first': 'Dora', 'last': 'Cancellata', 'association': OWN, 'group': None, 'deleted': True},
    'hans': {'id': next(ids), 'first': 'Hans', 'last': 'Muller', 'association': FOREIGN, 'group': None, 'deleted': False},
    'greta': {'id': 'a1000000-0000-4000-8000-000000998901', 'first': 'Greta', 'last': 'Neri', 'association': FOREIGN,
              'group': GROUP_ID, 'deleted': False},
}
A = lambda label: ASSOCIATES[label]['id']


def invoice(number, *, association=OWN, archived=False, deleted=False, group=None, document=False, tutor=None, cancelled=False,
            description='Ricevuta seed', attached=False):
    return {'id': next(ids), 'number': number, 'association': association, 'created': '2026-01-05T09:00:00Z', 'archived': archived,
            'deleted': deleted, 'group': group, 'document': document, 'membership_fee': '0.00', 'activity_fee': '10.00',
            'description': description, 'tutor': tutor, 'cancelled': cancelled, 'attached': attached}


INVOICES = {
    'i_plain': invoice(1, tutor='anna', document=True, attached=True),
    'i_other': invoice(2),
    'i_sub': invoice(3),
    'i_course': invoice(4),
    'i_install': invoice(5),
    'i_carnet': invoice(6),
    'i_reason': invoice(7),
    'i_supplier': invoice(8),
    'i_instructor': invoice(9),
    'i_noparty': invoice(10),
    'i_transfer': invoice(11),
    'i_long': invoice(12),
    'i_overflow': invoice(13),
    'i_two': invoice(14),
    'i_group': invoice(15, group=GROUP_ID),
    'i_foreignpay': invoice(16),
    'i_cancelled': invoice(17, cancelled=True),
    'i_archived': invoice(90, archived=True),
    'i_deleted': invoice(91, deleted=True),
    'i_foreign': invoice(92, association=FOREIGN),
}
I = lambda label: INVOICES[label]['id']


def row(**values):
    base = {'type': 'default', 'subject': 0, 'amount': '10.00', 'creation_date': '2026-03-01T08:00:00Z', 'payment_date': None,
            'paid': True, 'expense': False, 'description': 'Quota', 'payment_category_id': CATEGORY['id'], 'meta_payment_categories': None,
            'invoice_id': None, 'user_id': OWNER, 'associate_id': None, 'supplier_id': None, 'instructor_id': None,
            'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
            'notes': None, 'custom_accounts_id': None, 'signature_id': None, 'deleted': False, 'group_id': None, 'course': None}
    unknown = set(values) - set(base)
    assert not unknown, unknown
    return {**base, **values}


LONG_REASON = 'Causale lunga ' + 'x' * 240
PAYMENTS = {
    'p_other': {'id': next(ids), **row(associate_id=A('anna'), invoice_id=I('i_other'), description='Quota <palestra>')},
    'p_sub': {'id': next(ids), **row(subject=1, associate_id=A('anna'), invoice_id=I('i_sub'), description='Iscrizione',
                                     meta={'subscription_data': {'name': 'Quota <A>', 'id': 'q-a'}},
                                     payment_date='2026-02-10T10:30:00Z')},
    'p_course': {'id': next(ids), **row(subject=2, associate_id=A('bruno'), invoice_id=I('i_course'), description='Corso nuoto')},
    'p_install': {'id': next(ids), **row(subject=2, associate_id=A('anna'), invoice_id=I('i_install'), description='Rata yoga')},
    'p_carnet': {'id': next(ids), **row(subject=2, associate_id=A('bruno'), invoice_id=I('i_carnet'), description='Carnet')},
    'p_reason': {'id': next(ids), **row(subject=2, associate_id=A('anna'), invoice_id=I('i_reason'), description=None,
                                        payment_category_id=None)},
    'p_supplier': {'id': next(ids), **row(subject=2, invoice_id=I('i_supplier'), description='Rimborso "spese"', supplier_id=SUPPLIER['id'])},
    'p_instructor': {'id': next(ids), **row(invoice_id=I('i_instructor'), description=None, instructor_id=INSTRUCTOR['id'])},
    'p_noparty': {'id': next(ids), **row(subject=2, invoice_id=I('i_noparty'), description='Varie', user_id=None)},
    'p_transfer': {'id': next(ids), **row(subject=3, associate_id=A('anna'), invoice_id=I('i_transfer'))},
    'p_long': {'id': next(ids), **row(invoice_id=I('i_long'), description=LONG_REASON)},
    # The invoice's live payments by primary key: the deleted one is skipped, 'p_two_a' sorts before 'p_two_b'.
    'p_two_deleted': {'id': 'a1000000-0000-4000-8000-000000998390', **row(invoice_id=I('i_two'), deleted=True, description='Cancellato')},
    'p_two_a': {'id': 'a1000000-0000-4000-8000-000000998391', **row(invoice_id=I('i_two'), description='Prima')},
    'p_two_b': {'id': 'a1000000-0000-4000-8000-000000998392', **row(invoice_id=I('i_two'), description='Seconda')},
    'p_nogroup': {'id': next(ids), **row(invoice_id=I('i_group'), description='Fuori gruppo')},
    'p_foreignpay': {'id': next(ids), **row(sport_association_id=FOREIGN, user_id=IDS['foreign_owner'], associate_id=A('hans'),
                                            invoice_id=I('i_foreignpay'), description='Estero')},
    'p_free': {'id': next(ids), **row(description='Pagamento libero')},
}
P = lambda label: PAYMENTS[label]['id']

# A customer invoice of the other association with the number and fiscal year the add below reuses.
CUSTOMER_INVOICES = {
    'foreign_number': {'id': 'a1000000-0000-4000-8000-000000998902', 'association': FOREIGN, 'prefix': 'FPR',
                       'number': 520, 'fiscal_year': 2031},
}


def subscription(associate, *, payment=None, start=None, end=None, group=None):
    return {'id': next(ids), 'associate': A(associate) if associate else None, 'association': OWN, 'user': OWNER,
            'payment': P(payment) if payment else None, 'start': start, 'end': end, 'group': group, 'created': '2026-01-10T08:00:00Z'}


SUBSCRIPTIONS = {
    'sub_anna': subscription('anna', payment='p_sub', start='2025-09-01', end='2026-08-31'),
    'sub_bruno': subscription('bruno', start='2026-01-01', end='2026-12-31'),
}
MEMBERSHIPS = {'mem_anna': {'id': next(ids), 'subscription': SUBSCRIPTIONS['sub_anna']['id'], 'start': '2026-01-01', 'end': '2026-12-31',
                            'type': 'FIDAL'}}
COURSES = {'nuoto': {'id': next(ids), 'title': 'Nuoto & Co'}, 'yoga': {'id': next(ids), 'title': 'Yoga'}}
COURSE_SUBSCRIPTIONS = {
    'cs_nuoto': {'id': next(ids), 'course': 'nuoto', 'subscription': 'sub_bruno', 'payment': 'p_course'},
    'cs_yoga': {'id': next(ids), 'course': 'yoga', 'subscription': 'sub_anna', 'payment': None},
}
INSTALLMENTS = {'inst_yoga': {'id': next(ids), 'course_subscription': 'cs_yoga', 'payment': 'p_install'}}
CARNETS = {'carnet': {'id': next(ids), 'title': 'Carnet <10>'}}
CARNET_SUBSCRIPTIONS = {'carsub': {'id': next(ids), 'carnet': 'carnet', 'subscription': 'sub_bruno', 'payment': 'p_carnet'}}

INVOICE_STATE = copy.deepcopy(INVOICES)
PAYMENT_STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='PATCH', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=(), restore=None):
    case = {'name': f'invoice-sync-print-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'prints': list(prints), 'restore': restore}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
GROUP = {'X-Group-Id': GROUP_ID}
OTHER_GROUP = {'X-Group-Id': IDS['foreign_group']}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
FORM = 'application/x-www-form-urlencoded'
UPDATE = lambda label: f'/invoice/{I(label)}/update'
OK = {'status': 200, 'body': 'printed'}
COMPLETED = {'state': 'completed', 'completed_at': '2026-01-01T00:00:00Z'}


def nothing(actor='owner'):
    return {'actor': actor, 'invoices': {}, 'payments': {}, 'customer': None, 'audit': []}


def print_invoice(label, reply=OK):
    return {'path': f'/document/invoice/{I(label)}?send_receipt_email=False', 'reply': reply}


def print_customer(reply=OK):
    return {'path': '/document/einvoice/$customer', 'reply': reply}


# --- rendering and effects ----------------------------------------------------------------

def escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            .replace("'", '&#x27;'))


def full_name(label):
    person = ASSOCIATES[label]
    return f"{person['first']} {person['last']}".strip()


def other(reason):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(DENOMINATION)}</b>."


def recipient(name, reason):
    return (f"Ricevuta di pagamento di <b>{escape(name)}</b> con causale <b>{escape(reason)}</b> "
            f"per l'associazione <b>{escape(DENOMINATION)}</b>.")


def course(name, title):
    return f"Ricevuta di pagamento di <b>{escape(name)}</b> per il corso <b>{escape(title)}</b> dell'associazione <b>{escape(DENOMINATION)}</b>."


def subscribed(sub_name, name):
    return f"Ricevuta di pagamento per l'iscrizione {escape(sub_name)}di <b>{escape(name)}</b> all'associazione <b>{escape(DENOMINATION)}</b>."


def invoice_item(label):
    inv = INVOICE_STATE[label]
    return {'invoice_id': inv['id'], 'document_token': DOCUMENT['token'] if inv['document'] else None, 'creation_date': inv['created'],
            'membership_fee': inv['membership_fee'], 'activity_fee': inv['activity_fee'], 'meta_payment_categories': None,
            'description': inv['description'], 'number': inv['number'], 'archived': inv['archived'], 'cancelled': inv['cancelled'],
            'meta': None, 'imported_from_associami': False, 'deleted': inv['deleted'], 'document_pdf': DOCUMENT['id'] if inv['document'] else None,
            'sport_association': inv['association'], 'selected_tutor': A(inv['tutor']) if inv['tutor'] else None, 'group': inv['group'],
            'attached_documents': [ATTACHED['id']] if inv['attached'] else []}


def naive(text):
    return 'None' if text is None else text.replace('T', ' ').replace('Z', '')


def updated(label, body, *, actor='owner', payment=None, payment_changes=None, description=None, failure=None, tutor='absent',
            headers=None, prints=None, restore=None, name=None, raw_body=None, content_type=None, parsed=None):
    """One invoice/<uid>/update reaching the invoice: mutates the state and adds the case.

    payment: the label of the payment the view saves; payment_changes: its audited
    column changes ({column: new value}); description: the generated description (None:
    unchanged); failure: 'payment' (500 after the payment save) or 'invoice' (500 after
    the invoice entry); tutor: the associate label the invoice ends with ('absent': None,
    'kept': unchanged).
    """
    inv = INVOICE_STATE[label]
    audit, payments, invoices = [], {}, {}
    if payment is not None:
        values = PAYMENT_STATE[payment]
        changes, changed = {}, {}
        for column, new in (payment_changes or {}).items():
            field = column.removesuffix('_id')
            old = values[column]
            if column == 'payment_date':
                changes[field] = [naive(old), naive(new)]
            else:
                changes[field] = ['None' if old is None else old, new]
            changed[column] = new
        if changes:
            audit.append({'model': 'payment', 'key': values['id'], 'action': 1, 'changes': changes,
                          'index': [values['sport_association_id'], DIRECT]})
        values.update(changed)
        payments[payment] = changed
    if failure == 'payment':
        add(name, actor, UPDATE(label), 500, SERVER, body=body, raw_body=raw_body, content_type=content_type or (FORM if raw_body else None), headers=headers,
            effect={**nothing(actor), 'payments': payments, 'audit': audit}, restore=restore)
        return
    number, cancelled = parsed or (body_number(body, raw_body), body_cancelled(body, raw_body))
    new = {'number': number, 'cancelled': cancelled}
    if tutor != 'kept':
        new['tutor'] = None if tutor == 'absent' else tutor
    if description is not None:
        new['description'] = description
    changes = {}
    for key, value in new.items():
        old = inv[key]
        if old == value:
            continue
        if key == 'tutor':
            changes['selected_tutor'] = [A(old) if old else 'None', A(value) if value else 'None']
        elif key == 'cancelled':
            changes['cancelled'] = [str(old), str(value)]
        else:
            changes[key] = ['None' if old is None else str(old), str(value)]
    if changes:
        audit.append({'model': 'invoice', 'key': inv['id'], 'action': 1, 'changes': changes, 'index': [inv['association'], DIRECT]})
    if failure == 'invoice':
        add(name, actor, UPDATE(label), 500, SERVER, body=body, raw_body=raw_body, content_type=content_type or (FORM if raw_body else None), headers=headers,
            effect={**nothing(actor), 'payments': payments, 'audit': audit}, restore=restore)
        return
    inv.update(new)
    invoices[label] = {'number': inv['number'], 'cancelled': inv['cancelled'], 'description': inv['description'],
                       'selected_tutor_id': A(inv['tutor']) if inv['tutor'] else None}
    add(name, actor, UPDATE(label), 200, {'message': 'Invoice updated', 'invoice': invoice_item(label)}, body=body, raw_body=raw_body,
        content_type=content_type or (FORM if raw_body else None), headers=headers,
        effect={**nothing(actor), 'invoices': invoices, 'payments': payments, 'audit': audit},
        prints=[print_invoice(label)] if prints is None else prints, restore=restore)


def body_number(body, raw_body):
    if raw_body is not None:
        return int(dict(item.split('=', 1) for item in raw_body.split('&'))['number'])
    return int(body['number'])


def body_cancelled(body, raw_body):
    if raw_body is not None:
        return bool(dict(item.split('=', 1) for item in raw_body.split('&')).get('cancelled', False))
    return bool(body.get('cancelled', False))


def multipart(*parts, boundary='invoicesyncboundary'):
    """parts: (name, filename or None, content type or None, text) as a multipart/form-data body."""
    chunks = []
    for name, filename, content_type, text in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        chunks.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'
                      + (f'Content-Type: {content_type}\r\n' if content_type else '') + f'\r\n{text}\r\n')
    return ''.join(chunks) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


# --- invoice/<uid>/update: guards -----------------------------------------------------------

PATH = UPDATE('i_plain')
add('update-anonymous', 'anonymous', PATH, 401, ANONYMOUS)
add('update-restricted-missing-permission', 'restricted_collaborator', PATH, 403,
    {'detail': 'Missing permission: bookeeping.documents.invoices.update'})
add('update-admin-without-session', 'admin', PATH, 403, ADMIN)
add('update-owner-scoped-athlete-denied', 'owner', PATH, 403, SCOPED_DENIED, headers=SCOPED)
add('update-wrong-method', 'owner', PATH, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={'number': 1})
add('update-options', 'owner', PATH, 200, {'name': 'Invoice Update', 'description': '', 'renders': ['application/json'], 'parses': PARSES},
    method='OPTIONS')
add('update-malformed-uid', 'owner', '/invoice/abc/update', 400, ['badly formed hexadecimal UUID string'], body={'number': 1},
    effect=nothing())
add('update-list-body-server-error', 'owner', PATH, 500, SERVER, body=['number'], effect=nothing())
add('update-string-body-server-error', 'owner', PATH, 500, SERVER, body='number', effect=nothing())
add('update-missing-number-server-error', 'owner', PATH, 500, SERVER, body={'cancelled': True}, effect=nothing())
add('update-athlete-server-error', 'athlete', PATH, 500, SERVER, body={'number': 1}, effect=nothing('athlete'))
add('update-owner-without-association-server-error', 'owner_without_association', PATH, 500, SERVER, body={'number': 1},
    effect=nothing('owner_without_association'))
add('update-unknown-invoice-server-error', 'owner', f'/invoice/{IDS["missing"]}/update', 500, SERVER, body={'number': 1},
    effect=nothing())
for label in ('i_archived', 'i_deleted', 'i_foreign'):
    add(f'update-{label[2:]}-invoice-server-error', 'owner', UPDATE(label), 500, SERVER, body={'number': 1}, effect=nothing())
add('update-other-group-server-error', 'owner', PATH, 500, SERVER, body={'number': 1}, headers=OTHER_GROUP, effect=nothing())
for name, value in (('text', 'x'), ('null', None), ('decimal-text', '1.5'), ('list', [1])):
    add(f'update-number-{name}-server-error', 'owner', PATH, 500, SERVER, body={'number': value}, effect=nothing())
add('update-number-infinite-server-error', 'owner', PATH, 500, SERVER, raw_body='{"number": 1e999}', content_type='application/json',
    effect=nothing())
for name, value in (('malformed', 'abc'), ('blank', ''), ('float', 1.5), ('object', {'id': 1})):
    add(f'update-tutor-{name}-server-error', 'owner', PATH, 500, SERVER, body={'number': 1, 'selected_tutor': value}, effect=nothing())

# --- invoice/<uid>/update: the invoice without a payment ----------------------------------------

updated('i_plain', {'number': '12'}, name='update-plain-text-number-clears-tutor')
updated('i_plain', {'number': 7.9, 'cancelled': 'false', 'selected_tutor': A('anna')}, tutor='anna',
        name='update-plain-float-number-string-cancelled')
for name, tutor in (('deleted', A('dora')), ('null', None), ('integer', 5)):
    updated('i_plain', {'number': 7, 'cancelled': True, 'selected_tutor': tutor}, tutor='kept', name=f'update-plain-tutor-{name}-kept')
updated('i_plain', {'number': 7, 'selected_tutor': A('hans')}, tutor='kept', name='update-plain-tutor-other-association-kept')
updated('i_plain', {'number': True, 'cancelled': 0, 'selected_tutor': A('bruno').upper()}, tutor='bruno',
        name='update-plain-bool-number-upper-tutor')
updated('i_plain', {'number': ' 8 ', 'cancelled': [], 'selected_tutor': A('bruno')}, tutor='bruno', name='update-plain-spaced-number')
updated('i_plain', None, raw_body='number=9&cancelled=', name='update-plain-form-body', actor='full_collaborator')
updated('i_plain', {'number': 10, 'cancelled': 'x', 'selected_tutor': A('anna')}, tutor='anna', name='update-plain-admin-impersonating',
        actor='admin', headers=ADMIN_OWNER)
updated('i_plain', {'number': 11}, name='update-plain-restore-completed-no-print', prints=[], restore=COMPLETED)
updated('i_plain', {'number': 12}, name='update-plain-restore-review-prints', restore={'state': 'review', 'completed_at': None})
updated('i_plain', {'number': 13}, name='update-plain-restore-completed-without-date-prints',
        restore={'state': 'completed', 'completed_at': None})
updated('i_plain', {'number': 14}, name='update-plain-print-error-swallowed',
        prints=[print_invoice('i_plain', {'status': 500, 'body': 'renderer down'})])
updated('i_plain', {'number': 15}, name='update-plain-print-redirect-followed',
        prints=[print_invoice('i_plain', {'status': 302, 'location': '/document/redirected?x=1'}),
                {'path': '/document/redirected?x=1', 'reply': OK}])
updated('i_overflow', {'number': 2147483648}, name='update-number-out-of-range-audited-server-error', failure='invoice')
updated('i_cancelled', {'number': 17, 'cancelled': None}, name='update-cancelled-null-uncancels')

# --- invoice/<uid>/update: payments and descriptions ---------------------------------------------

updated('i_other', {'number': 21, 'cancelled': 'false', 'selected_tutor': A('anna'), 'payment_date': '2026-05-06'}, tutor='anna',
        payment='p_other', payment_changes={'payment_date': '2026-05-06T00:00:00Z'}, description=other(CATEGORY['name']),
        name='update-other-subject-payment-date')
updated('i_other', {'number': 21, 'payment_date': '2026-05-06', 'selected_tutor': A('anna')}, tutor='anna', payment='p_other',
        description=other(CATEGORY['name']), name='update-other-unchanged-payment-not-audited')
# Files in the body: a file is truthy and not a number, a date or a uuid; the payment date that is a file is ignored.
FILE = ('attachment', 'a.txt', 'text/plain', 'x')
mp, ct = multipart(('number', None, None, '22'), ('cancelled', 'c.txt', 'text/plain', 'x'))
updated('i_plain', None, raw_body=mp, content_type=ct, parsed=(22, True), name='update-plain-cancelled-as-file-is-truthy')
mp, ct = multipart(('number', 'n.txt', 'text/plain', '23'))
add('update-number-as-file-server-error', 'owner', UPDATE('i_plain'), 500, SERVER, raw_body=mp, content_type=ct, effect=nothing())
mp, ct = multipart(('number', None, None, '24'), ('selected_tutor', 't.txt', 'text/plain', A('anna')))
add('update-tutor-as-file-server-error', 'owner', UPDATE('i_plain'), 500, SERVER, raw_body=mp, content_type=ct, effect=nothing())
mp, ct = multipart(('number', None, None, '25'), ('selected_tutor', None, None, A('anna')), ('payment_date', 'd.txt', 'text/plain', '2026-09-09'))
updated('i_other', None, raw_body=mp, content_type=ct, parsed=(25, False), tutor='anna', payment='p_other', description=other(CATEGORY['name']),
        name='update-other-payment-date-as-file-ignored')
SUB_NAME = '25/26 (Quota <A>)  e tesseramento, 2026 (FIDAL) '
updated('i_sub', {'number': 31, 'payment_date': 'not-a-date'}, payment='p_sub', description=subscribed(SUB_NAME, full_name('anna')),
        name='update-subscription-subject-invalid-date-ignored')
updated('i_course', {'number': 41, 'payment_date': 20260101}, payment='p_course', description=course(full_name('bruno'), 'Nuoto & Co'),
        name='update-course-subscription-number-date-ignored')
updated('i_install', {'number': 51}, payment='p_install', description=course(full_name('anna'), 'Yoga'), name='update-course-installment')
updated('i_carnet', {'number': 61}, payment='p_carnet', description=course(full_name('bruno'), CARNETS['carnet']['title']),
        name='update-carnet', actor='full_collaborator')
updated('i_reason', {'number': 71}, payment='p_reason',
        payment_changes={'payment_category_id': '$default_category', 'description': 'Quota Corso'}, description='Quota Corso',
        name='update-course-reason-save-defaults')
updated('i_supplier', {'number': 81, 'payment_date': '2026-06-01'}, payment='p_supplier',
        payment_changes={'payment_date': '2026-06-01T00:00:00Z'}, description=recipient(SUPPLIER['name'], 'Rimborso "spese"'),
        name='update-supplier-without-associate')
updated('i_instructor', {'number': 91}, payment='p_instructor', payment_changes={'description': CATEGORY['name']},
        description=recipient(f"{INSTRUCTOR['first']} {INSTRUCTOR['last']}", CATEGORY['name']), name='update-instructor-category-reason')
updated('i_noparty', {'number': 101}, payment='p_noparty', description=other('Varie'), name='update-no-party')
updated('i_two', {'number': 141}, payment='p_two_a', description=other('Prima'), name='update-first-live-payment-by-key')
updated('i_group', {'number': 151, 'selected_tutor': A('anna')}, tutor='kept', headers=GROUP,
        name='update-group-invoice-payment-and-tutor-outside-group')
updated('i_group', {'number': 152, 'selected_tutor': A('carla')}, tutor='carla', headers=GROUP,
        name='update-group-invoice-own-group-tutor')
updated('i_group', {'number': 153, 'selected_tutor': A('greta')}, tutor='kept', headers=GROUP,
        name='update-group-invoice-other-association-tutor-kept')
updated('i_foreignpay', {'number': 161, 'payment_date': '2026-07-01'}, payment='p_foreignpay',
        payment_changes={'payment_date': '2026-07-01T00:00:00Z'}, description=other(CATEGORY['name']),
        name='update-payment-resolved-by-invoice')
updated('i_transfer', {'number': 111, 'payment_date': '2026-08-01'}, payment='p_transfer',
        payment_changes={'payment_date': '2026-08-01T00:00:00Z'}, failure='payment', name='update-transfer-subject-saved-payment-server-error')
updated('i_long', {'number': 121, 'payment_date': '2026-08-02'}, payment='p_long', payment_changes={'payment_date': '2026-08-02T00:00:00Z'},
        description=other(LONG_REASON), failure='invoice', name='update-long-description-audited-server-error')

# --- invoice-customers/add ----------------------------------------------------------------------

ADD = '/invoice-customers/add'
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, method='POST')
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403,
    {'detail': 'Missing permission: bookeeping.documents.clientinvoices.create'}, method='POST')
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, method='POST')
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, method='POST', headers=SCOPED)
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, {'name': 'Invoice Customers Add', 'description': '', 'renders': ['application/json'],
                                       'parses': PARSES}, method='OPTIONS')

LINE = lambda description, quantity, unit, price, vat: {'description': description, 'quantity': quantity, 'unit_of_measure': unit,
                                                         'unit_price': price, 'vat': vat}
BASE = {'fiscal_year': 2031, 'assignor_vat_number': '01234567890', 'assignor_tax_code': '01234567890',
        'assignor_denomination': 'ASD Alfa & Figli', 'assignor_address': 'Via Roma 1', 'assignor_postal_code': '10100',
        'assignor_city': 'Torino', 'assignor_province': 'TO', 'assignor_contact_email': 'alfa@example.test',
        'assignor_contact_phone': '0111234567', 'transferor_tax_code': 'RSSMRA80A01L219X', 'transferor_denomination': 'Mario Rossi <Ditta>',
        'transferor_address': 'Via Po 2', 'transferor_postal_code': '10123', 'transferor_city': 'Torino', 'transferor_province': 'TO',
        'transferor_contact_email': 'mario.pec@example.test', 'causal': 'Quota & servizi',
        'lines': [LINE('Quota annuale', 1, 'unita', 100.0, 22.0)], 'payment_condition': 'TP02', 'payment_modality': 'MP05'}
# CustomerInvoice's concrete fields in model order with their defaults (None: no default).
MODEL_DEFAULTS = {
    'customer_invoice_id': '$customer', 'prefix': 'FPR', 'number': None, 'fiscal_year': None, 'assignor_prefix_vat_number': 'IT',
    'assignor_vat_number': None, 'assignor_tax_code': None, 'assignor_denomination': None, 'assignor_fiscal_regime': 'RF01',
    'assignor_address': '', 'assignor_postal_code': '', 'assignor_city': '', 'assignor_province': '', 'assignor_country': 'IT',
    'assignor_contact_email': None, 'assignor_contact_phone': None, 'assignor_fax': None, 'transferor_prefix_vat_number': 'IT',
    'transferor_vat_number': None, 'transferor_tax_code': None, 'transferor_denomination': None, 'transferor_fiscal_regime': 'RF01',
    'transferor_address': '', 'transferor_postal_code': '', 'transferor_city': '', 'transferor_province': '', 'transferor_country': 'IT',
    'transferor_contact_email': None, 'transferor_contact_phone': None, 'transferor_fax': None, 'e_invoice': True,
    'country_transmitter_prefix': 'IT', 'id_transmitter': None, 'transmitting_format': 'FPR12', 'document_type': 'TD01', 'currency': 'EUR',
    'transmitting_date': None, 'causal': None, 'lines': None, 'payment_condition': None, 'payment_modality': None,
    'payment_total_amount': 0, 'payment_expiry_date': None, 'xml': None, 'pdf': None, 'json': None, 'sport_association': OWN,
    'payment': None, 'creation_date': '$now', 'paid': False, 'transmitted': False,
}
RELATIONS, JSONS, DATETIMES = ('pdf', 'sport_association', 'payment'), ('lines', 'json'), ('creation_date',)


def audit_text(field, value):
    if field in JSONS:
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return str(value)
    if field in DATETIMES and value != '$now':
        return naive(value)
    return str(value)


def added(name, body, *, actor='owner', memory=None, stored=None, second=None, xml_error=False, prints=None, restore=None, headers=None):
    """A valid invoice-customers/add. memory: in-memory values besides the body (DRF
    conversions); stored: stored values that differ from them; second: the second save's
    audited changes; the third save's xml/json entry is added unless xml_error."""
    values = dict(MODEL_DEFAULTS)
    values.update({key: body[key] for key in body if key in values})
    values.update({'transmitting_date': body.get('transmitting_date') or '$today',
                   'payment_expiry_date': body.get('payment_expiry_date') or '$today'})
    values.update(memory or {})
    create = {}
    for field, value in values.items():
        if value is None:
            if field in RELATIONS or field in DATETIMES:
                create[field] = ['None', 'None']
            elif field in JSONS:
                create[field] = ['None', 'null']
            continue
        create[field] = ['None', audit_text(field, value)]
    audit = [{'model': 'customerinvoice', 'key': '$customer', 'action': 0, 'changes': create, 'index': [OWN, DIRECT]}]
    if second:
        audit.append({'model': 'customerinvoice', 'key': '$customer', 'action': 1, 'changes': second, 'index': [OWN, DIRECT]})
    saved = dict(values)
    saved.update(stored or {})
    if not xml_error:
        third = dict(second or {})
        third['xml'] = ['None' if values['xml'] is None else values['xml'], '$xml']
        third['json'] = [(second or {}).get('json', [audit_text('json', values['json'])])[0], '$json_audit']
        audit.append({'model': 'customerinvoice', 'key': '$customer', 'action': 1, 'changes': third, 'index': [OWN, DIRECT]})
        saved.update(xml='$xml', json='$json')
    effect = {**nothing(actor), 'customer': {'stored': saved, 'memory': values}, 'audit': audit}
    response = {'error': 'Errore durante la generazione del file XML.'} if xml_error else {'message': 'Invoice created'}
    if prints is None:
        prints = [] if xml_error else [print_customer()]
    add(name, actor, ADD, 200, response, method='POST', body=body, headers=headers, effect=effect, prints=prints, restore=restore)


AMOUNT_DEFAULT = {'payment_total_amount': ['0.00', '0']}
added('add-defaults', {**BASE, 'number': 501}, memory={}, stored={'payment_total_amount': '0.00'}, second=AMOUNT_DEFAULT)
added('add-number-of-other-association-accepted', {**BASE, 'number': 520}, memory={},
      stored={'payment_total_amount': '0.00'}, second=AMOUNT_DEFAULT)
MIXED = [LINE('Corso <nuoto> & "acqua"', 2, 'ore', 19.99, 22), LINE('Libro', 3, 'pz', '0.125', '4'),
         LINE('Servizio', 1.5, 'h', 33.333, 10.0)]
EXPLICIT_ID = next(ids)
added('add-explicit-values', {**BASE, 'customer_invoice_id': EXPLICIT_ID, 'number': 502, 'prefix': 'FE', 'transmitting_date': '2026-03-04',
                              'payment_expiry_date': '2026-04-30', 'payment_total_amount': '120.5', 'paid': True, 'transmitted': 'false',
                              'e_invoice': 0, 'pdf': DOCUMENT['id'], 'payment': P('p_free'), 'creation_date': '2026-02-03T04:05:06.123456Z',
                              'lines': MIXED, 'causal': None, 'xml': '<old/>', 'json': {'big': 1e+20, 'small': 1e-05, 'n': 2},
                              'transferor_vat_number': '12345678901', 'payment_condition': 'TP01', 'payment_modality': 'MP01'},
      actor='full_collaborator',
      memory={'customer_invoice_id': EXPLICIT_ID, 'payment_total_amount': '120.50', 'transmitted': False, 'e_invoice': False},
      second={'json': ['{"big": 100000000000000000000, "n": 2, "small": 1e-05}', '{"big": 1e+20, "n": 2, "small": 1e-05}']},
      stored={'json': {'big': 100000000000000000000, 'small': 1e-05, 'n': 2}})
added('add-xml-error-keeps-row', {**BASE, 'number': 503, 'transferor_contact_email': None, 'id_transmitter': 'ABC1234'},
      stored={'payment_total_amount': '0.00'}, second=AMOUNT_DEFAULT, xml_error=True)
added('add-restore-completed-no-print', {**BASE, 'number': 504, 'payment_total_amount': 10}, memory={'payment_total_amount': '10.00'},
      prints=[], restore=COMPLETED)
added('add-admin-impersonating-print-error', {**BASE, 'number': 505, 'payment_total_amount': '0', 'transmitting_date': None},
      actor='admin', headers=ADMIN_OWNER, memory={'payment_total_amount': '0.00'},
      prints=[print_customer({'status': 500, 'body': 'renderer down'})])
add('add-number-taken', 'owner', ADD, 400, {'non_field_errors': ['Invoice already exists!']}, method='POST',
    body={**BASE, 'number': 501, 'prefix': 'FX'}, effect=nothing())

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'invoice/<uid>/update and invoice-customers/add with their eager print tasks '
                                                            '(outbound GET to CURRENT_HOST, restore gate), payment, invoice, customer '
                                                            'invoice and audit effects',
                              'ids': IDS, 'category': CATEGORY, 'documents': {'pdf': DOCUMENT, 'attached': ATTACHED},
                              'supplier': SUPPLIER, 'instructor': INSTRUCTOR, 'associates': ASSOCIATES, 'invoices': INVOICES,
                              'payments': PAYMENTS, 'customer_invoices': CUSTOMER_INVOICES, 'subscriptions': SUBSCRIPTIONS,
                              'memberships': MEMBERSHIPS, 'courses': COURSES,
                              'course_subscriptions': COURSE_SUBSCRIPTIONS, 'installments': INSTALLMENTS, 'carnets': CARNETS,
                              'carnet_subscriptions': CARNET_SUBSCRIPTIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
