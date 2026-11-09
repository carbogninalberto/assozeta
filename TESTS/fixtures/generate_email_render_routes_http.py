"""Author the collaborators/add, payment/<uid>/request and invoice/<uid>/send HTTP sequence (email-render-routes.md).

All three views render an e-mail with render_to_string (email/_base.html, the
instance_email_brand tag, autoescaping, the active language's number
localization) and queue it with core.tasks.send_mail_async.apply_async(kwargs=...).
The published message is checked byte for byte: the adapter renders the
authored template and context with Django under the request's language (the
harness sends Accept-Language: en) and the run's InstanceConfiguration.

collaborators_add (@api_view POST, IsAuthenticated + IsAssociationUser):
is_sport_association, request.data, 'email' in data (400 ['Email is required']),
check_email (Django's validate_email: falsy or no '@' or over 320 characters is
400 ['Email is not valid.']; truthy non-strings without '@' membership raise),
int(data.get('collaborator_role', 3)) (a role other than 1/2/3 becomes 1),
collaborator_permissions = data.get(...) stored for every role,
CollaborationInvites.objects.create(user=request.user, email, expiration now+90d,
token=token_hex(16), ...) (auditlog create entry, indexed through the actor's
live association), .save() (no change, no entry), then request.user.sport_association
(the owner's live association; none: 500 with the invitation kept), the
collaborator e-mail and send_mail_async(subject=f'{WHITELABEL_NAME} | Invito a
collaborare', message, from_email=DEFAULT_FROM_EMAIL, recipient_list=[email],
html_message, fail_silently=False, sport_association_id=UUID).

payment_request (@api_view POST): is_valid_uuid, the scoped live association
.get() (500), the scoped live group-aware payment .first() (none or without
association: 500), another association's payment 403 'User not allowed.', a paid
payment 412. Recipients: the associate's email when not None, then
request.user.email when payment.user is not request.user; the description
('per l'iscrizione ', 'per il corso ' or '<description> '), the associate's name
(no associate: 500), the pending template, send_mail_async(..., from_email=
DEFAULT_TEAM_EMAIL, ..., reply_to=[association user email]).

invoice_send (@api_view POST): is_valid_uuid, the scoped association .get()
(500), the scoped live group-aware invoice .first() (none: 500), its first
scoped live group-aware payment by key, another association's invoice 403, no
payment 500. Email from the associate, the user, then the supplier (truthy);
none: 412 {'msg': 'Nessuna email associata.'}. No PDF or no payment date: 500.
The receipt template (amount float(membership_fee) + float(activity_fee)) and
send_mail_async(subject=f'{denomination} | Ricevuta di pagamento n. {number} -
{dd/mm/YYYY}', ...). Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('email_render_routes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
_ids = iter(f'a1000000-0000-4000-8000-{690001 + n:012d}' for n in range(300))
UNKNOWN = 'a1000000-0000-4000-8000-000000699999'
SERVER = '<stable-server-error-page>'
TASK = 'core.tasks.send_mail_async'


def uid():
    return next(_ids)


DOCUMENTS = {label: {'document_id': uid(), 'creation_date': '2026-09-01T08:00:00Z', 'filepath': f'private/{label}.pdf',
                     'filename': f'{label}.pdf', 'file_size_bytes': 2048, 'token': uid()} for label in ('pdf_a', 'pdf_b')}


def associate(first, last, email, association=OWN):
    return {'associate_id': uid(), 'sport_association': association, 'first_name': first, 'last_name': last, 'sex': 'M',
            'is_minor': False, 'email': email, 'deleted': False, 'creation_date': '2025-01-01T08:00:00Z'}


ASSOCIATES = {
    'mario': associate('Mario', 'Rossi', 'mario.rossi@example.test'),
    'luca': associate('Luca', 'Bianchi', None),
    'empty': associate('Ivo', 'Gialli', ''),
    'quoted': associate("D'Arco", '<Ünal> & Co', 'darco@example.test'),
    'hans': associate('Hans', 'Muller', 'hans@example.test', FOREIGN),
}
SUPPLIERS = {
    'mailed': {'supplier_id': uid(), 'name': 'Fornitore "Uno" & C.', 'sport_association': OWN, 'type': 'supplier',
               'email': 'fornitore@example.test', 'deleted': False},
    'silent': {'supplier_id': uid(), 'name': 'Fornitore Due', 'sport_association': OWN, 'type': 'supplier', 'email': '', 'deleted': False},
}


def invoice(number, membership='10.10', activity='0.20', **values):
    row = {'invoice_id': uid(), 'document_pdf': DOCUMENTS['pdf_a']['document_id'], 'sport_association': OWN,
           'creation_date': '2026-02-01T09:00:00Z', 'membership_fee': membership, 'activity_fee': activity, 'description': 'Quota annuale',
           'number': number, 'archived': False, 'cancelled': False, 'group': None, 'deleted': False}
    row.update(values)
    return row


INVOICES = {
    'associate': invoice(7, description='Quota\r\nannuale & <altro>\nfine'),
    'user': invoice(8, '30.00', '20.00', document_pdf=DOCUMENTS['pdf_b']['document_id']),
    'supplier': invoice(9, '0.01', '0.00'),
    'empty_email': invoice(10, '-5.00', '0.00'),
    'no_email': invoice(11),
    'null_number': invoice(None, '2.67', '0.00', description=None),
    'no_pdf': invoice(12, document_pdf=None),
    'no_payment': invoice(13),
    'no_date': invoice(14),
    'only_deleted_payment': invoice(15),
    'two_payments': invoice(16, '9999999.99', '9999999.99'),
    'grouped': invoice(17, group=OWN_GROUP),
    'deleted': invoice(18, deleted=True),
    'archived': invoice(19, archived=True),
    'abroad': invoice(20, sport_association=FOREIGN),
}


def payment(invoice_label=None, **values):
    row = {'payment_id': uid(), 'type': 'cash', 'subject': 0, 'amount': '15.00', 'creation_date': '2026-01-01T09:00:00Z',
           'payment_date': '2026-03-05T10:00:00Z', 'paid': False, 'expense': False, 'description': 'Quota',
           'invoice': INVOICES[invoice_label]['invoice_id'] if invoice_label else None, 'user': None, 'associate': None,
           'supplier': None, 'sport_association': OWN, 'archived': False, 'deleted': False, 'group': None}
    row.update(values)
    return row


A = {label: row['associate_id'] for label, row in ASSOCIATES.items()}
PAYMENTS = {
    # invoice/<uid>/send
    'i_associate': payment('associate', associate=A['mario'], user=OWNER, paid=True),
    'i_user': payment('user', associate=A['luca'], user=OWNER, paid=True, payment_date='2025-12-31T23:59:59Z'),
    'i_supplier': payment('supplier', supplier=SUPPLIERS['mailed']['supplier_id'], paid=True),
    'i_empty_email': payment('empty_email', associate=A['empty'], user=ATHLETE, paid=True),
    'i_no_email': payment('no_email', associate=A['luca'], supplier=SUPPLIERS['silent']['supplier_id'], paid=True),
    'i_null_number': payment('null_number', associate=A['quoted'], paid=True),
    'i_no_pdf': payment('no_pdf', associate=A['mario'], paid=True),
    'i_no_date': payment('no_date', associate=A['mario'], paid=True, payment_date=None),
    'i_only_deleted': payment('only_deleted_payment', associate=A['mario'], deleted=True),
    'i_first': payment('two_payments', associate=A['mario'], paid=True),
    'i_second': payment('two_payments', associate=A['quoted'], paid=True),
    'i_grouped': payment('grouped', associate=A['mario'], group=OWN_GROUP, paid=True),
    'i_archived': payment('archived', associate=A['mario'], paid=True),
    'i_abroad': payment('abroad', associate=A['hans'], sport_association=FOREIGN, paid=True),
    # payment/<uid>/request
    'p_course': payment(subject=2, amount='25.50', associate=A['mario'], user=OWNER),
    'p_subscription': payment(subject=1, amount='40.00', associate=A['mario'], user=ATHLETE),
    'p_described': payment(description="Quota <x> & 'y'", amount='3.05', associate=A['quoted'], user=OWNER),
    'p_undescribed': payment(description=None, amount='0.00', associate=A['luca'], user=None),
    'p_empty_email': payment(amount='1234567.89', associate=A['empty'], user=OWNER),
    'p_no_associate': payment(user=ATHLETE),
    'p_paid': payment(associate=A['mario'], paid=True),
    'p_deleted': payment(associate=A['mario'], deleted=True),
    'p_grouped': payment(associate=A['mario'], group=OWN_GROUP, user=OWNER),
    'p_orphan': payment(associate=A['mario'], sport_association=None),
    'p_abroad': payment(associate=A['hans'], sport_association=FOREIGN, user=IDS['foreign_owner']),
}

cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'email-render-routes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def render(template, context):
    """The message: render_to_string(template, context) under the request's language."""
    return {'$render': template, 'language': 'en', 'context': context}


def mail(subject, message, from_email, recipients, association, reply_to=None):
    kwargs = [['subject', subject], ['message', message], ['from_email', from_email], ['recipient_list', recipients],
              ['html_message', message], ['fail_silently', False], ['sport_association_id', {'uuid': association}]]
    if reply_to is not None:
        kwargs.append(['reply_to', reply_to])
    return {'task': TASK, 'args': [], 'kwargs': kwargs}


# --- collaborators/add ------------------------------------------------------------------------
INVITED = {'msg': 'User invited'}
COLLABORATOR = 'email/account/email_welcome_collaborator_message.html'


def invited(email, role, permissions=None, *, actor='owner', index=(OWN, 'actor.sport_association'), sent=True):
    """One CollaborationInvites row of the owner with its create entry; sent: the e-mail is queued."""
    message = render(COLLABORATOR, {'email': email, 'token': '$token', 'sport_association': {'denomination': '$denomination:own'},
                                    'app_host': '$app_url', 'settings': {'WHITELABEL_NAME': '$whitelabel', 'IS_WHITELABEL': '$is_whitelabel'}})
    return {'kind': 'invite', 'actor': actor, 'email': email, 'role': role, 'permissions': permissions,
            'index': list(index) if index else None,
            'celery': [mail('$whitelabel | Invito a collaborare', message, '$from_email', [email], OWN)] if sent else []}


ADD = '/collaborators/add'
add('add-full-role-with-permissions', 'owner', ADD, 200, INVITED,
    body={'email': 'nuovo@example.test', 'collaborator_role': 2, 'collaborator_permissions': {'b': [1, 2.5, None], 'a': 'è'}},
    effect=invited('nuovo@example.test', 2, {'b': [1, 2.5, None], 'a': 'è'}))
add('add-default-role', 'owner', ADD, 200, INVITED, body={'email': 'default@example.test'}, effect=invited('default@example.test', 3))
add('add-unknown-role-becomes-full', 'owner', ADD, 200, INVITED, body={'email': 'sette@example.test', 'collaborator_role': '7'},
    effect=invited('sette@example.test', 1))
add('add-bool-role', 'owner', ADD, 200, INVITED, body={'email': 'bool@example.test', 'collaborator_role': True,
                                                       'collaborator_permissions': ['x']},
    effect=invited('bool@example.test', 1, ['x']))
add('add-quoted-email', 'owner', ADD, 200, INVITED, body={'email': "o'brien+tag@example.test"}, effect=invited("o'brien+tag@example.test", 3))
add('add-form', 'owner', ADD, 200, INVITED, raw_body='email=form%40example.test&collaborator_role=3&collaborator_permissions=x&collaborator_permissions=%7B%7D',
    content_type='application/x-www-form-urlencoded', effect=invited('form@example.test', 3, '{}'))
add('add-full-collaborator', 'full_collaborator', ADD, 200, INVITED, body={'email': 'collab@example.test', 'collaborator_role': 1},
    effect=invited('collab@example.test', 1, actor='full_collaborator', index=None))
add('add-admin-impersonating-owner', 'admin', ADD, 200, INVITED, body={'email': 'admin@example.test'},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=invited('admin@example.test', 3, actor='admin', index=(IDS['admin_association'], 'actor.sport_association')))
add('add-owner-without-association-kept', 'owner_without_association', ADD, 500, SERVER, body={'email': 'kept@example.test'},
    effect={**invited('kept@example.test', 3, actor='owner_without_association', index=None, sent=False), 'owner': 'owner_without_association'})
add('add-missing-email', 'owner', ADD, 400, ['Email is required'], body={'collaborator_role': 1})
add('add-invalid-email', 'owner', ADD, 400, ['Email is not valid.'], body={'email': 'not-an-email'})
add('add-blank-email', 'owner', ADD, 400, ['Email is not valid.'], body={'email': ''})
add('add-null-email', 'owner', ADD, 400, ['Email is not valid.'], body={'email': None})
add('add-list-email-without-at', 'owner', ADD, 400, ['Email is not valid.'], body={'email': ['a@b.it']})
add('add-list-email-with-at', 'owner', ADD, 500, SERVER, body={'email': ['@']})
add('add-number-email', 'owner', ADD, 500, SERVER, body={'email': 5})
add('add-too-long-for-column', 'owner', ADD, 500, SERVER, body={'email': 'a' * 100 + '@' + '.'.join(['b' * 60] * 3) + '.it'})
add('add-role-not-int', 'owner', ADD, 500, SERVER, body={'email': 'x@example.test', 'collaborator_role': '2.0'})
add('add-role-null', 'owner', ADD, 500, SERVER, body={'email': 'x@example.test', 'collaborator_role': None})
add('add-permissions-nul', 'owner', ADD, 500, SERVER, body={'email': 'x@example.test', 'collaborator_permissions': {'k': 'a\u0000'}})
add('add-list-body', 'owner', ADD, 400, ['Email is required'], body=['other'])
add('add-list-body-with-key', 'owner', ADD, 500, SERVER, body=['email'])
add('add-string-body', 'owner', ADD, 500, SERVER, body='my email')
add('add-null-body', 'owner', ADD, 500, SERVER, raw_body='null', content_type='application/json')
add('add-malformed-json', 'owner', ADD, 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json')
add('add-athlete-denied', 'athlete', ADD, 403, {'detail': 'You do not have permission to perform this action.'}, body={'email': 'x@example.test'})
add('add-restricted', 'restricted_collaborator', ADD, 403, {'detail': 'Missing permission: other.users.collaborators.create'},
    body={'email': 'x@example.test'})
add('add-admin-without-session', 'admin', ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'email': 'x@example.test'})
add('add-anonymous', 'anonymous', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={'email': 'x@example.test'})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, {'name': 'Collaborators Add', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# --- payment/<uid>/request -------------------------------------------------------------------
PENDING = 'email/payment/pending.html'
SENT = {'msg': 'email sent.'}


def pid(label):
    return PAYMENTS[label]['payment_id'] if label in PAYMENTS else label


def requested(label, recipients, description, name, *, association=OWN, denomination='$denomination:own', owner='owner'):
    row = PAYMENTS[label]
    message = render(PENDING, {'recipient_name': name, 'description': description, 'amount': {'decimal': row['amount']},
                               'application_name': '$whitelabel', 'application_url': '$app_url', 'association_name': denomination,
                               'online_payments_enabled': f'$online:{owner}'})
    return {'kind': 'mail', 'celery': [mail(f'{denomination} | Pagamento in attesa', message, '$team_email', recipients, association,
                                            [f'$email:{owner}'])]}


def request(label):
    return f'/payment/{pid(label)}/request'


add('request-course', 'owner', request('p_course'), 200, SENT,
    effect=requested('p_course', ['mario.rossi@example.test'], 'per il corso ', 'Mario Rossi'))
add('request-subscription-other-user', 'owner', request('p_subscription'), 200, SENT,
    effect=requested('p_subscription', ['mario.rossi@example.test', '$email:owner'], "per l'iscrizione ", 'Mario Rossi'))
add('request-described', 'owner', request('p_described'), 200, SENT,
    effect=requested('p_described', ['darco@example.test'], "Quota <x> & 'y' ", "D'Arco <Ünal> & Co"))
add('request-undescribed-without-email', 'owner', request('p_undescribed'), 200, SENT,
    effect=requested('p_undescribed', ['$email:owner'], '', 'Luca Bianchi'))
add('request-empty-associate-email', 'owner', request('p_empty_email'), 200, SENT,
    effect=requested('p_empty_email', [''], 'Quota ', 'Ivo Gialli'))
add('request-body-ignored', 'owner', request('p_course'), 200, SENT, raw_body='{', content_type='application/json',
    effect=requested('p_course', ['mario.rossi@example.test'], 'per il corso ', 'Mario Rossi'))
add('request-full-collaborator', 'full_collaborator', request('p_course'), 200, SENT,
    effect=requested('p_course', ['mario.rossi@example.test'], 'per il corso ', 'Mario Rossi'))
add('request-admin-impersonating-owner', 'admin', request('p_subscription'), 200, SENT,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=requested('p_subscription', ['mario.rossi@example.test', '$email:owner'], "per l'iscrizione ", 'Mario Rossi'))
add('request-group-member', 'owner', request('p_grouped'), 200, SENT, headers={'X-Group-Id': OWN_GROUP},
    effect=requested('p_grouped', ['mario.rossi@example.test'], 'Quota ', 'Mario Rossi'))
add('request-other-group', 'owner', request('p_grouped'), 500, SERVER, headers={'X-Group-Id': FOREIGN_GROUP})
add('request-foreign-owner', 'foreign_owner', request('p_abroad'), 200, SENT,
    effect=requested('p_abroad', ['hans@example.test'], 'Quota ', 'Hans Muller', association=FOREIGN, denomination='$denomination:foreign',
                     owner='foreign_owner'))
add('request-no-associate', 'owner', request('p_no_associate'), 500, SERVER)
add('request-paid', 'owner', request('p_paid'), 412, {'msg': 'email not sent, payment already paid.'})
add('request-deleted', 'owner', request('p_deleted'), 500, SERVER)
add('request-missing', 'owner', request(UNKNOWN), 500, SERVER)
add('request-without-association', 'owner', request('p_orphan'), 500, SERVER)
add('request-foreign-denied', 'owner', request('p_abroad'), 403, {'detail': 'User not allowed.'})
add('request-malformed-uid', 'owner', request('abc'), 400, ['badly formed hexadecimal UUID string'])
add('request-owner-without-association', 'owner_without_association', request('p_course'), 500, SERVER)
add('request-athlete', 'athlete', request('p_course'), 500, SERVER)
add('request-restricted', 'restricted_collaborator', request('p_course'), 403, {'detail': 'Missing permission: bookeeping.payments.update'})
add('request-admin-without-session', 'admin', request('p_course'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('request-anonymous', 'anonymous', request('p_course'), 401, {'detail': 'Authentication credentials were not provided.'})
add('request-wrong-method', 'owner', request('p_course'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('request-options', 'owner', request('p_course'), 200, {'name': 'Payment Request', 'description': 'API endpoint to add a new payment',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

# --- invoice/<uid>/send ----------------------------------------------------------------------
RECEIPT = 'email/payment/payment_receipt.html'


def iid(label):
    return INVOICES[label]['invoice_id'] if label in INVOICES else label


def send(label):
    return f'/invoice/{iid(label)}/send'


def day(moment):
    return '/'.join(reversed(moment[:10].split('-')))


def sent_receipt(label, payment_label, email, name):
    row, pay = INVOICES[label], PAYMENTS[payment_label]
    token = next(d['token'] for d in DOCUMENTS.values() if d['document_id'] == row['document_pdf'])
    url = f"$app_url/api/document/retrieve/{row['invoice_id']}?download=true&token={token}"
    number = row['number']
    message = render(RECEIPT, {'recipient_name': name, 'amount': {'float_sum': [row['membership_fee'], row['activity_fee']]},
                               'receipt_number': number, 'payment_date': day(pay['payment_date']), 'description': row['description'],
                               'download_url': url, 'association_name': '$denomination:own'})
    subject = f"$denomination:own | Ricevuta di pagamento n. {number} - {day(pay['payment_date'])}"
    return {'kind': 'mail', 'celery': [mail(subject, message, '$team_email', [email], OWN, ['$email:owner'])]}


add('send-associate', 'owner', send('associate'), 200, SENT, effect=sent_receipt('associate', 'i_associate', 'mario.rossi@example.test', 'Mario Rossi'))
add('send-user', 'owner', send('user'), 200, SENT, effect=sent_receipt('user', 'i_user', '$email:owner', '$name:owner'))
add('send-supplier', 'owner', send('supplier'), 200, SENT,
    effect=sent_receipt('supplier', 'i_supplier', 'fornitore@example.test', 'Fornitore "Uno" & C.'))
add('send-empty-associate-email-user', 'owner', send('empty_email'), 200, SENT,
    effect=sent_receipt('empty_email', 'i_empty_email', '$email:athlete', '$name:athlete'))
add('send-null-number-and-description', 'owner', send('null_number'), 200, SENT,
    effect=sent_receipt('null_number', 'i_null_number', 'darco@example.test', "D'Arco <Ünal> & Co"))
add('send-first-payment-by-key', 'owner', send('two_payments'), 200, SENT,
    effect=sent_receipt('two_payments', 'i_first', 'mario.rossi@example.test', 'Mario Rossi'))
add('send-archived', 'full_collaborator', send('archived'), 200, SENT,
    effect=sent_receipt('archived', 'i_archived', 'mario.rossi@example.test', 'Mario Rossi'))
add('send-group-member', 'owner', send('grouped'), 200, SENT, headers={'X-Group-Id': OWN_GROUP},
    effect=sent_receipt('grouped', 'i_grouped', 'mario.rossi@example.test', 'Mario Rossi'))
add('send-admin-impersonating-owner', 'admin', send('associate'), 200, SENT,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=sent_receipt('associate', 'i_associate', 'mario.rossi@example.test', 'Mario Rossi'))
add('send-body-ignored', 'owner', send('supplier'), 200, SENT, raw_body='{', content_type='application/json',
    effect=sent_receipt('supplier', 'i_supplier', 'fornitore@example.test', 'Fornitore "Uno" & C.'))
add('send-no-email', 'owner', send('no_email'), 412, {'msg': 'Nessuna email associata.'})
add('send-no-pdf', 'owner', send('no_pdf'), 500, SERVER)
add('send-no-payment', 'owner', send('no_payment'), 500, SERVER)
add('send-only-deleted-payment', 'owner', send('only_deleted_payment'), 500, SERVER)
add('send-no-payment-date', 'owner', send('no_date'), 500, SERVER)
add('send-other-group', 'owner', send('grouped'), 500, SERVER, headers={'X-Group-Id': FOREIGN_GROUP})
add('send-deleted', 'owner', send('deleted'), 500, SERVER)
add('send-missing', 'owner', send(UNKNOWN), 500, SERVER)
add('send-foreign-denied', 'owner', send('abroad'), 403, {'detail': 'User not allowed.'})
add('send-malformed-uid', 'owner', send('abc'), 400, ['badly formed hexadecimal UUID string'])
add('send-owner-without-association', 'owner_without_association', send('associate'), 500, SERVER)
add('send-restricted', 'restricted_collaborator', send('associate'), 403,
    {'detail': 'Missing permission: bookeeping.documents.invoices.update'})
add('send-admin-without-session', 'admin', send('associate'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('send-anonymous', 'anonymous', send('associate'), 401, {'detail': 'Authentication credentials were not provided.'})
add('send-wrong-method', 'owner', send('associate'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('send-options', 'owner', send('associate'), 200, {'name': 'Invoice Send', 'description': 'API endpoint to add a new payment',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Rendered e-mails queued by collaborators/add, payment request and invoice send',
                              'ids': IDS, 'documents': DOCUMENTS, 'associates': ASSOCIATES, 'suppliers': SUPPLIERS, 'invoices': INVOICES,
                              'payments': PAYMENTS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
