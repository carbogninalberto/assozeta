"""Author the document-render HTTP sequence (document-render.md).

The PDF routes of BE/docmanager/views/printing_views.py:
document/{subscription,invoice,einvoice,compensation,template,medical-appointment}/<uid>
(@api_view(['POST', 'GET'])). Each one, inside one try/except that answers
500 {'exception': str(e)}:

- resolves its row (subscription, invoice + its payment, customer invoice,
  compensation payment, module template, medical appointment) and builds the
  PDF filename from it;
- Document.objects.create(filename=...) (audited; no resolver: the actor's
  association indexes it);
- PrintingService.print_and_store_pdf: FastRender.get_pdf POSTs
  json.dumps(payload) to http://PUPPETEER_HOST:PUPPETEER_PORT/render, the
  payload naming CURRENT_HOST_PUPPETEER/<route>/<uid>/view and the request's
  Authorization header. A non-2xx answer, a content type without
  application/pdf or a body not starting with %PDF- is
  500 {'exception': 'PDF rendering failed: ...'} (the Document stays).
  Otherwise save_to_storage.delay('<timestamp>/<id>/<filename>', base64(pdf))
  and the PDF itself, 'inline' or (truthy ?download) 'attachment';
- on a 200, links the Document to the row (subscription/invoice
  document_pdf, customer invoice pdf, every InstructorHours of the payment,
  the appointment's document); the invoice route then queues the receipt
  e-mail (send_mail_async.apply_async) when send_receipt_email is
  'true'/'True'/'1' and the payment's associate has an e-mail.

Permissions: subscription IsAuthenticated; invoice and einvoice
IsAuthenticated | HasDocumentToken | HasBypassAuthorizationHeader;
compensation, template and medical-appointment IsAuthenticated |
HasDocumentToken. The template route answers 200 'Generato con successo.'
whatever the renderer said, after optional archive and subscription-file
rows from request.data['additionalData'].

The renderer is a per-run stub (the adapter's service_context): every case
names the requests it must receive and the replies it gives.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('document_render_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
PDF = '<pdf>'


def uid(n):
    return f'a1000000-0000-4000-8000-{984000 + n:012d}'


BASE_ATHLETE_ASSOCIATE = 'a1000000-0000-4000-8000-000000700101'
UNKNOWN = uid(999)

ASSOCIATIONS = {
    'latin': {'user': uid(1), 'association': uid(2), 'denomination': 'Società Sportiva Ècole'},
    'wide': {'user': uid(3), 'association': uid(4), 'denomination': 'Club – Roma'},
}
ASSOCIATION_IDS = {'own': OWN, 'foreign': FOREIGN, **{k: v['association'] for k, v in ASSOCIATIONS.items()}}
DENOMINATIONS = {'own': 'Message reads own', 'foreign': 'Message reads foreign',
                 **{k: v['denomination'] for k, v in ASSOCIATIONS.items()}}

ASSOCIATES = {
    'mario': {'id': uid(10), 'association': 'own', 'first': 'Mario', 'last': 'Rossi', 'email': 'mario.rossi@example.test', 'user': None},
    'noemail': {'id': uid(11), 'association': 'own', 'first': 'Anna', 'last': 'Verdi', 'email': '', 'user': None},
    'greek': {'id': uid(12), 'association': 'wide', 'first': 'Δημήτρης', 'last': 'Παπαδόπουλος', 'email': None, 'user': None},
    'odd': {'id': uid(13), 'association': 'own', 'first': None, 'last': ' Neri　', 'email': None, 'user': None},
}
FULL_NAMES = {'mario': 'Mario Rossi', 'noemail': 'Anna Verdi', 'greek': 'Δημήτρης Παπαδόπουλος', 'odd': 'None  Neri',
              'athlete': 'Athlete Fixture'}
ASSOCIATE_IDS = {**{k: v['id'] for k, v in ASSOCIATES.items()}, 'athlete': BASE_ATHLETE_ASSOCIATE}

DOCUMENTS = {
    'old_subscription': {'id': uid(20), 'token': uid(120), 'filename': 'old subscription.pdf'},
    'old_invoice': {'id': uid(21), 'token': uid(121), 'filename': 'old invoice.pdf'},
    'old_customer': {'id': uid(22), 'token': uid(122), 'filename': 'old customer invoice.pdf'},
    'old_medical': {'id': uid(23), 'token': uid(123), 'filename': 'old medical.pdf'},
    'shared': {'id': uid(24), 'token': uid(124), 'filename': 'shared.pdf'},
    'dup_a': {'id': uid(25), 'token': uid(125), 'filename': 'dup a.pdf'},
    'dup_b': {'id': uid(26), 'token': uid(125), 'filename': 'dup b.pdf'},
}

SUBSCRIPTIONS = {
    'plain': {'id': uid(30), 'association': 'own', 'associate': 'mario', 'user': 'owner', 'group': None, 'document': None},
    'previous': {'id': uid(31), 'association': 'own', 'associate': 'mario', 'user': 'owner', 'group': None, 'document': 'old_subscription'},
    'group': {'id': uid(32), 'association': 'own', 'associate': 'mario', 'user': 'owner', 'group': 'own_group', 'document': None},
    'athlete': {'id': uid(33), 'association': 'own', 'associate': 'athlete', 'user': 'athlete', 'group': None, 'document': None},
    'noassociate': {'id': uid(34), 'association': 'own', 'associate': None, 'user': 'owner', 'group': None, 'document': None},
    'latin': {'id': uid(35), 'association': 'latin', 'associate': 'mario', 'user': 'owner', 'group': None, 'document': None},
    'wide': {'id': uid(36), 'association': 'wide', 'associate': 'mario', 'user': 'owner', 'group': None, 'document': None},
    'greek': {'id': uid(37), 'association': 'wide', 'associate': 'greek', 'user': 'owner', 'group': None, 'document': None},
    'odd': {'id': uid(38), 'association': 'own', 'associate': 'odd', 'user': 'owner', 'group': None, 'document': None},
    'foreign': {'id': uid(39), 'association': 'foreign', 'associate': 'mario', 'user': 'foreign_owner', 'group': None, 'document': None},
}

# Invoices and their payments (label shared). payment None: the invoice has no payment.
INVOICES = {
    'plain': {'id': uid(40), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'noemail': {'id': uid(41), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'noassociate': {'id': uid(42), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'nopayment': {'id': uid(43), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'deleted': {'id': uid(44), 'association': 'own', 'deleted': True, 'group': None, 'document': None},
    'deleted_payment': {'id': uid(45), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'two': {'id': uid(46), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'nodate': {'id': uid(47), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'group': {'id': uid(48), 'association': 'own', 'deleted': False, 'group': 'own_group', 'document': None},
    'athlete': {'id': uid(49), 'association': 'own', 'deleted': False, 'group': None, 'document': None},
    'previous': {'id': uid(50), 'association': 'latin', 'deleted': False, 'group': None, 'document': 'old_invoice'},
}
PAYMENTS = {
    'plain': {'id': uid(60), 'invoice': 'plain', 'association': 'own', 'amount': '25.00', 'date': '2026-03-04T10:00:00+00:00',
              'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'noemail': {'id': uid(61), 'invoice': 'noemail', 'association': 'own', 'amount': '1234.50', 'date': '2026-03-05T23:30:00+00:00',
                'associate': 'noemail', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'noassociate': {'id': uid(62), 'invoice': 'noassociate', 'association': 'own', 'amount': '7.00', 'date': '2026-03-06T10:00:00+00:00',
                    'associate': None, 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'deleted': {'id': uid(63), 'invoice': 'deleted', 'association': 'own', 'amount': '5.00', 'date': '2026-03-07T10:00:00+00:00',
                'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'deleted_payment': {'id': uid(64), 'invoice': 'deleted_payment', 'association': 'own', 'amount': '5.00',
                        'date': '2026-03-08T10:00:00+00:00', 'associate': 'mario', 'user': None, 'instructor': None, 'deleted': True,
                        'group': None},
    'two_a': {'id': uid(65), 'invoice': 'two', 'association': 'own', 'amount': '5.00', 'date': '2026-03-09T10:00:00+00:00',
              'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'two_b': {'id': uid(66), 'invoice': 'two', 'association': 'own', 'amount': '6.00', 'date': '2026-03-09T10:00:00+00:00',
              'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'nodate': {'id': uid(67), 'invoice': 'nodate', 'association': 'own', 'amount': '5.00', 'date': None,
               'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'group': {'id': uid(68), 'invoice': 'group', 'association': 'own', 'amount': '9.90', 'date': '2026-03-10T10:00:00+00:00',
              'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': 'own_group'},
    'athlete': {'id': uid(69), 'invoice': 'athlete', 'association': 'own', 'amount': '12.00', 'date': '2026-03-11T10:00:00+00:00',
                'associate': 'athlete', 'user': 'athlete', 'instructor': None, 'deleted': False, 'group': None},
    'previous': {'id': uid(70), 'invoice': 'previous', 'association': 'latin', 'amount': '30.00', 'date': '2026-12-31T23:59:59+00:00',
                 'associate': 'mario', 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    # Compensations (instructor payments).
    'comp': {'id': uid(71), 'invoice': None, 'association': 'own', 'amount': '80.00', 'date': '2026-04-05T09:00:00+00:00',
             'associate': None, 'user': None, 'instructor': 'luca', 'deleted': False, 'group': None},
    'comp_nohours': {'id': uid(72), 'invoice': None, 'association': 'own', 'amount': '10.00', 'date': '2026-04-06T09:00:00+00:00',
                     'associate': None, 'user': None, 'instructor': 'luca', 'deleted': False, 'group': None},
    'comp_noinstructor': {'id': uid(73), 'invoice': None, 'association': 'own', 'amount': '10.00', 'date': '2026-04-07T09:00:00+00:00',
                          'associate': None, 'user': None, 'instructor': None, 'deleted': False, 'group': None},
    'comp_nodate': {'id': uid(74), 'invoice': None, 'association': 'own', 'amount': '10.00', 'date': None,
                    'associate': None, 'user': None, 'instructor': 'luca', 'deleted': False, 'group': None},
    'comp_deleted': {'id': uid(75), 'invoice': None, 'association': 'own', 'amount': '10.00', 'date': '2026-04-08T09:00:00+00:00',
                     'associate': None, 'user': None, 'instructor': 'luca', 'deleted': True, 'group': None},
    'comp_foreign': {'id': uid(76), 'invoice': None, 'association': 'foreign', 'amount': '10.00', 'date': '2026-04-09T09:00:00+00:00',
                     'associate': None, 'user': None, 'instructor': 'paolo', 'deleted': False, 'group': None},
}
INSTRUCTORS = {
    'luca': {'id': uid(80), 'user': 'owner', 'first': 'Luca', 'last': 'Bianchi'},
    'paolo': {'id': uid(81), 'user': 'foreign_owner', 'first': 'Paolo', 'last': 'Gialli'},
}
HOURS = {
    'h1': {'id': uid(85), 'instructor': 'luca', 'payment': 'comp', 'date': '2026-04-01'},
    'h2': {'id': uid(86), 'instructor': 'luca', 'payment': 'comp', 'date': '2026-04-02'},
    'h_other': {'id': uid(87), 'instructor': 'luca', 'payment': None, 'date': '2026-04-03'},
    'h_foreign': {'id': uid(88), 'instructor': 'paolo', 'payment': 'comp_foreign', 'date': '2026-04-04'},
}
CUSTOMER_INVOICES = {
    'plain': {'id': uid(90), 'association': 'own', 'prefix': 'FPR', 'number': 7, 'year': 2026, 'document': None},
    'nulls': {'id': uid(91), 'association': 'own', 'prefix': None, 'number': None, 'year': None, 'document': None},
    'previous': {'id': uid(92), 'association': 'foreign', 'prefix': 'B', 'number': 12, 'year': 2025, 'document': 'old_customer'},
}
TEMPLATES = {
    'plain': {'id': uid(100), 'association': 'own', 'name': 'Modulo privacy, v1.2'},
    'foreign': {'id': uid(101), 'association': 'foreign', 'name': 'Modulo estero'},
}
FOLDERS = {'own': 'own', 'foreign': 'foreign'}
MEDICAL = {
    'agonistica': {'id': uid(110), 'subscription': 'plain', 'region': 'agonistica', 'date': '2026-05-06', 'document': None},
    'non': {'id': uid(111), 'subscription': 'latin', 'region': 'non-agonistica', 'date': '2026-05-07', 'document': None},
    'nodate': {'id': uid(112), 'subscription': 'plain', 'region': 'agonistica', 'date': None, 'document': None},
    'badregion': {'id': uid(113), 'subscription': 'plain', 'region': 'unknown-region', 'date': '2026-05-08', 'document': None},
    'previous': {'id': uid(114), 'subscription': 'foreign', 'region': 'non-agonistica', 'date': '2026-05-09', 'document': 'old_medical'},
}

cases = []

ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_HIDDEN = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
INVALID_JWT = {'detail': 'Given token not valid for any token type', 'code': 'token_not_valid',
               'messages': [{'token_class': 'AccessToken', 'token_type': 'access', 'message': 'Token is invalid'}]}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
exception = lambda text: {'exception': text}
invalid_uuid = lambda value: exception(f"['“{value}” is not a valid UUID.']")

PDF_OK = {'status': 200, 'content_type': 'application/pdf', 'body': '$pdf'}


def reply(status, content_type, body):
    return {'status': status, 'content_type': content_type, 'body': body}


def render(route, key, authorization='$bearer', answer=None, query=''):
    """One expected renderer request and the stub's reply."""
    return {'url': f'document/{route}/{key}/view{query}', 'authorization': authorization, 'reply': answer or PDF_OK}


def add(name, actor, path, status, response, *, method='GET', headers=None, body=None, raw_body=None, content_type=None,
        renders=(), effect=None, disposition=None):
    case = {'name': f'document-render-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'renders': list(renders), 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if disposition is not None:
        case['disposition'] = disposition
    cases.append(case)


def effect(filename, *, actor, celery=True, links=(), creates=(), mail=None):
    """filename: the new Document; links: (model, label) rows whose FK becomes it;
    creates: archive/subscriptionfile rows; mail: the receipt e-mail spec."""
    return {'document': filename, 'actor': actor, 'storage_task': celery, 'links': [list(item) for item in links],
            'creates': [dict(item) for item in creates], 'mail': mail}


def common(route, name, key):
    path = f'/document/{route}/{key}'
    add(f'{name}-anonymous', 'anonymous', path, 401, ANONYMOUS)
    add(f'{name}-invalid-bearer', 'anonymous', path, 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'})
    add(f'{name}-admin-without-session', 'admin', path, 403, ADMIN)
    add(f'{name}-restricted-collaborator', 'restricted_collaborator', path, 403, unmapped(path))
    add(f'{name}-put-not-allowed', 'owner', path, 405, method('PUT'), method='PUT')
    title = ' '.join(word.capitalize() for word in ('document_' + route.replace('-', '_')).split('_'))
    add(f'{name}-options', 'owner', path, 200, {'name': title, 'description': '', 'renders': ['application/json'], 'parses': PARSES},
        method='OPTIONS')


S, I, E, C, T, M = (lambda k: SUBSCRIPTIONS[k]['id'], lambda k: INVOICES[k]['id'], lambda k: CUSTOMER_INVOICES[k]['id'],
                    lambda k: PAYMENTS[k]['id'], lambda k: TEMPLATES[k]['id'], lambda k: MEDICAL[k]['id'])


def subscription_name(key):
    row = SUBSCRIPTIONS[key]
    return f"[{DENOMINATIONS[row['association']]}] Modulo di Iscrizione {FULL_NAMES[row['associate']]}.pdf"


# --- document/subscription/<uid> (IsAuthenticated) --------------------------------------------------
common('subscription', 'subscription', S('plain'))
add('subscription-document-token', 'anonymous', f"/document/subscription/{S('plain')}?token={DOCUMENTS['shared']['token']}", 401,
    ANONYMOUS)
add('compensation-scoped-athlete-denied', 'owner', f"/document/compensation/{C('comp')}", 403, SCOPED_DENIED, headers=SCOPED)
for name, actor, key, extra in (
        ('subscription-owner', 'owner', 'plain', {}),
        ('subscription-full-collaborator', 'full_collaborator', 'odd', {}),
        ('subscription-athlete-direct', 'athlete', 'athlete', {}),
        ('subscription-owner-foreign-row', 'owner', 'foreign', {}),
        ('subscription-admin-impersonating-owner', 'admin', 'plain', ADMIN_OWNER),
        ('subscription-group-own', 'owner', 'group', {'X-Group-Id': IDS['own_group']}),
        ('subscription-group-malformed', 'owner', 'group', {'X-Group-Id': 'not-a-group'}),
        ('subscription-scoped-athlete', 'owner', 'athlete', SCOPED),
        ('subscription-latin-denomination', 'owner', 'latin', {}),
        ('subscription-mime-quoted', 'owner', 'wide', {}),
        ('subscription-mime-base64', 'owner', 'greek', {})):
    who = {'full_collaborator': 'full_collaborator', 'athlete': 'athlete', 'admin': 'admin'}.get(actor, 'owner')
    add(name, actor, f'/document/subscription/{S(key)}', 200, PDF, headers=extra, renders=[render('subscription', S(key))],
        effect=effect(subscription_name(key), actor=who, links=[('subscription', key)]),
        disposition='inline; filename=' + subscription_name(key))
add('subscription-post-download', 'owner', f"/document/subscription/{S('previous')}?download=1", 200, PDF, method='POST',
    body={'ignored': True}, renders=[render('subscription', S('previous'))],
    effect=effect(subscription_name('previous'), actor='owner', links=[('subscription', 'previous')]),
    disposition='attachment; filename=' + subscription_name('previous'))
add('subscription-download-empty', 'owner', f"/document/subscription/{S('plain')}?download=", 200, PDF,
    renders=[render('subscription', S('plain'))], effect=effect(subscription_name('plain'), actor='owner', links=[('subscription', 'plain')]),
    disposition='inline; filename=' + subscription_name('plain'))
add('subscription-uppercase-uid', 'owner', f"/document/subscription/{S('plain').upper()}", 200, PDF,
    renders=[render('subscription', S('plain').upper())],
    effect=effect(subscription_name('plain'), actor='owner', links=[('subscription', 'plain')]),
    disposition='inline; filename=' + subscription_name('plain'))
add('subscription-unknown', 'owner', f'/document/subscription/{UNKNOWN}', 500, exception('Subscription matching query does not exist.'))
add('subscription-invalid-uuid', 'owner', '/document/subscription/not-a-uuid', 500, invalid_uuid('not-a-uuid'))
add('subscription-invalid-uuid-quote', 'owner', "/document/subscription/it's", 500,
    exception('["“it\'s” is not a valid UUID."]'))
add('subscription-group-other', 'owner', f"/document/subscription/{S('group')}", 500, exception('Subscription matching query does not exist.'),
    headers={'X-Group-Id': IDS['foreign_group']})
add('subscription-scoped-athlete-other', 'owner', f"/document/subscription/{S('plain')}", 403, SCOPED_HIDDEN, headers=SCOPED)
add('subscription-without-associate', 'owner', f"/document/subscription/{S('noassociate')}", 500,
    exception("'NoneType' object has no attribute 'get_full_name'"))
failure = lambda text: exception('PDF rendering failed: ' + text)
add('subscription-renderer-error', 'owner', f"/document/subscription/{S('plain')}", 500,
    failure('Renderer returned HTTP 500: renderer exploded'),
    renders=[render('subscription', S('plain'), answer=reply(500, 'text/plain; charset=utf-8', 'renderer exploded'))],
    effect=effect(subscription_name('plain'), actor='owner', celery=False))
long_text = 'è' * 300 + 'x' * 300
add('subscription-renderer-error-truncated', 'owner', f"/document/subscription/{S('plain')}", 500,
    failure('Renderer returned HTTP 502: ' + long_text[:500]),
    renders=[render('subscription', S('plain'), answer=reply(502, 'text/plain; charset=utf-8', long_text))],
    effect=effect(subscription_name('plain'), actor='owner', celery=False))
add('subscription-renderer-redirect-status', 'owner', f"/document/subscription/{S('plain')}", 500,
    failure('Renderer returned HTTP 304: '),
    renders=[render('subscription', S('plain'), answer=reply(304, 'application/pdf', ''))],
    effect=effect(subscription_name('plain'), actor='owner', celery=False))
add('subscription-renderer-html', 'owner', f"/document/subscription/{S('plain')}", 500,
    failure('Renderer returned invalid PDF data (content-type: text/html; charset=utf-8)'),
    renders=[render('subscription', S('plain'), answer=reply(200, 'text/html; charset=utf-8', '%PDF-not really'))],
    effect=effect(subscription_name('plain'), actor='owner', celery=False))
add('subscription-renderer-not-pdf', 'owner', f"/document/subscription/{S('plain')}", 500,
    failure('Renderer returned invalid PDF data (content-type: application/pdf)'),
    renders=[render('subscription', S('plain'), answer=reply(200, 'application/pdf', 'PDF-1.7 without the percent'))],
    effect=effect(subscription_name('plain'), actor='owner', celery=False))
add('subscription-renderer-created', 'owner', f"/document/subscription/{S('plain')}", 200, PDF,
    renders=[render('subscription', S('plain'), answer={**PDF_OK, 'status': 201})],
    effect=effect(subscription_name('plain'), actor='owner', links=[('subscription', 'plain')]),
    disposition='inline; filename=' + subscription_name('plain'))

# --- document/invoice/<uid> (IsAuthenticated | HasDocumentToken | HasBypassAuthorizationHeader) -----


def invoice_name(key, payment=None):
    payment = PAYMENTS[payment or key]
    return f"[{DENOMINATIONS[INVOICES[key]['association']]}] Ricevuta {payment['date'][:10]}.pdf"


def mail(key):
    payment = PAYMENTS[key]
    day = payment['date'][:10]
    short = f'{day[8:10]}/{day[5:7]}/{day[2:4]}'
    amount = payment['amount'].replace(',', ' ').replace('.', ',')
    return {'subject': f'Ricevuta di pagamento {short}',
            'message': f"Gentile {FULL_NAMES[payment['associate']]},\n"
                       f"abbiamo il piacere di inviarle la ricevuta di pagamento di {amount} € del {short}.\n\n"
                       "Ecco il link per scaricare la ricevuta:\n $app_url/api/document/retrieve/$document?download=false&token=$token\n\n"
                       "Cordiali saluti,\n"
                       f"{DENOMINATIONS[INVOICES[payment['invoice']]['association']]}",
            'recipient': ASSOCIATES[payment['associate']]['email']}


def invoice_ok(name, actor, key, *, who='owner', query='', headers=None, authorization='$bearer', email=False, method='GET', body=None,
               raw_body=None, content_type=None, attachment=False):
    add(name, actor, f'/document/invoice/{I(key)}{query}', 200, PDF, headers=headers, method=method, body=body, raw_body=raw_body,
        content_type=content_type, renders=[render('invoice', I(key), authorization)],
        effect=effect(invoice_name(key), actor=who, links=[('invoice', key)], mail=mail(key) if email else None),
        disposition=('attachment' if attachment else 'inline') + '; filename=' + invoice_name(key))


common('invoice', 'invoice', I('plain'))
invoice_ok('invoice-owner', 'owner', 'plain')
invoice_ok('invoice-owner-receipt-email', 'owner', 'plain', query='?send_receipt_email=true', email=True)
invoice_ok('invoice-receipt-email-capital', 'owner', 'plain', query='?send_receipt_email=True&download=yes', email=True, attachment=True)
invoice_ok('invoice-receipt-email-yes', 'owner', 'plain', query='?send_receipt_email=yes')
invoice_ok('invoice-receipt-email-form', 'owner', 'plain', method='POST', raw_body='send_receipt_email=1',
           content_type='application/x-www-form-urlencoded', email=True)
invoice_ok('invoice-receipt-email-empty-query-form', 'owner', 'plain', query='?send_receipt_email=', method='POST',
           raw_body='send_receipt_email=true', content_type='application/x-www-form-urlencoded', email=True)
invoice_ok('invoice-receipt-email-false-query-form', 'owner', 'plain', query='?send_receipt_email=false', method='POST',
           raw_body='send_receipt_email=true', content_type='application/x-www-form-urlencoded')
invoice_ok('invoice-receipt-email-json-body', 'owner', 'plain', method='POST', body={'send_receipt_email': 'true'})
invoice_ok('invoice-receipt-email-without-address', 'owner', 'noemail', query='?send_receipt_email=1')
invoice_ok('invoice-receipt-email-without-associate', 'owner', 'noassociate', query='?send_receipt_email=1')
invoice_ok('invoice-previous-document', 'full_collaborator', 'previous', who='full_collaborator', query='?send_receipt_email=1', email=True)
invoice_ok('invoice-bypass-header', 'anonymous', 'plain', who=None, headers={'Authorization': '$bypass'}, authorization='$bypass',
           query='?send_receipt_email=True', email=True)
invoice_ok('invoice-document-token', 'anonymous', 'plain', who=None, query=f"?token={DOCUMENTS['shared']['token']}", authorization=None)
invoice_ok('invoice-document-token-hex', 'anonymous', 'plain', who=None,
           query=f"?token={DOCUMENTS['shared']['token'].replace('-', '').upper()}", authorization=None)
invoice_ok('invoice-group-own', 'owner', 'group', headers={'X-Group-Id': IDS['own_group']})
invoice_ok('invoice-scoped-athlete', 'owner', 'athlete', headers=SCOPED)
invoice_ok('invoice-athlete-direct', 'athlete', 'plain', who='athlete')
add('invoice-document-token-unknown', 'anonymous', f"/document/invoice/{I('plain')}?token={UNKNOWN}", 401, ANONYMOUS)
add('invoice-document-token-garbage', 'anonymous', f"/document/invoice/{I('plain')}?token=garbage", 500, SERVER)
add('invoice-document-token-empty', 'anonymous', f"/document/invoice/{I('plain')}?token=", 500, SERVER)
add('invoice-document-token-duplicated', 'anonymous', f"/document/invoice/{I('plain')}?token={DOCUMENTS['dup_a']['token']}", 500, SERVER)
add('invoice-bypass-wrong', 'anonymous', f"/document/invoice/{I('plain')}", 401, ANONYMOUS, headers={'Authorization': 'not-the-token'})
add('invoice-unknown', 'owner', f'/document/invoice/{UNKNOWN}', 404, exception('Invoice not found'))
add('invoice-deleted', 'owner', f"/document/invoice/{I('deleted')}", 404, exception('Invoice not found'))
add('invoice-group-other', 'owner', f"/document/invoice/{I('group')}", 404, exception('Invoice not found'),
    headers={'X-Group-Id': IDS['foreign_group']})
add('invoice-scoped-athlete-other', 'owner', f"/document/invoice/{I('plain')}", 403, SCOPED_HIDDEN, headers=SCOPED)
add('invoice-invalid-uuid', 'owner', '/document/invoice/abc', 500, invalid_uuid('abc'))
add('invoice-without-payment', 'owner', f"/document/invoice/{I('nopayment')}", 500, exception('Payment matching query does not exist.'))
add('invoice-deleted-payment', 'owner', f"/document/invoice/{I('deleted_payment')}", 500, exception('Payment matching query does not exist.'))
add('invoice-two-payments', 'owner', f"/document/invoice/{I('two')}", 500, exception('get() returned more than one Payment -- it returned 2!'))
add('invoice-payment-without-date', 'owner', f"/document/invoice/{I('nodate')}", 500, exception("'NoneType' object has no attribute 'date'"))
add('invoice-renderer-error', 'owner', f"/document/invoice/{I('plain')}?send_receipt_email=true", 500,
    failure('Renderer returned HTTP 503: busy'),
    renders=[render('invoice', I('plain'), answer=reply(503, 'text/plain; charset=utf-8', 'busy'))],
    effect=effect(invoice_name('plain'), actor='owner', celery=False))

# --- document/einvoice/<uid> (IsAuthenticated | HasDocumentToken | HasBypassAuthorizationHeader) ----


def customer_name(key):
    row = CUSTOMER_INVOICES[key]
    text = lambda value: 'None' if value is None else str(value)
    return f"[{DENOMINATIONS[row['association']]}] Fattura {text(row['prefix'])} {text(row['number'])}/{text(row['year'])}.pdf"


common('einvoice', 'einvoice', E('plain'))
for name, actor, key, who, headers, authorization in (
        ('einvoice-owner', 'owner', 'plain', 'owner', {}, '$bearer'),
        ('einvoice-null-numbering', 'owner', 'nulls', 'owner', {'X-Group-Id': IDS['foreign_group']}, '$bearer'),
        ('einvoice-previous-foreign', 'owner', 'previous', 'owner', {}, '$bearer'),
        ('einvoice-bypass-header', 'anonymous', 'plain', None, {'Authorization': '$bypass'}, '$bypass')):
    add(name, actor, f'/document/einvoice/{E(key)}', 200, PDF, headers=headers, renders=[render('einvoice', E(key), authorization)],
        effect=effect(customer_name(key), actor=who, links=[('customerinvoice', key)]), disposition='inline; filename=' + customer_name(key))
add('einvoice-unknown', 'owner', f'/document/einvoice/{UNKNOWN}', 500, exception('CustomerInvoice matching query does not exist.'))
add('einvoice-invalid-uuid', 'owner', '/document/einvoice/zz', 500, invalid_uuid('zz'))
add('einvoice-renderer-error', 'owner', f"/document/einvoice/{E('plain')}", 500,
    failure('Renderer returned invalid PDF data (content-type: )'),
    renders=[render('einvoice', E('plain'), answer=reply(200, '', '%PDF-1.7 untyped'))],
    effect=effect(customer_name('plain'), actor='owner', celery=False))

# --- document/compensation/<uid> (IsAuthenticated | HasDocumentToken) --------------------------------


def compensation_name(key):
    row = PAYMENTS[key]
    instructor = INSTRUCTORS[row['instructor']]
    return f"[{DENOMINATIONS[row['association']]}] Compenso per {instructor['first']} {instructor['last']} del {row['date'][:10]}.pdf"


common('compensation', 'compensation', C('comp'))
add('compensation-owner', 'owner', f"/document/compensation/{C('comp')}", 200, PDF, renders=[render('compensation', C('comp'))],
    effect=effect(compensation_name('comp'), actor='owner', links=[('instructorhours', 'h1'), ('instructorhours', 'h2')]),
    disposition='inline; filename=' + compensation_name('comp'))
add('compensation-without-hours', 'owner', f"/document/compensation/{C('comp_nohours')}?download=true", 200, PDF, method='POST',
    renders=[render('compensation', C('comp_nohours'))], effect=effect(compensation_name('comp_nohours'), actor='owner'),
    disposition='attachment; filename=' + compensation_name('comp_nohours'))
add('compensation-foreign-row', 'owner', f"/document/compensation/{C('comp_foreign')}", 200, PDF,
    renders=[render('compensation', C('comp_foreign'))],
    effect=effect(compensation_name('comp_foreign'), actor='owner', links=[('instructorhours', 'h_foreign')]),
    disposition='inline; filename=' + compensation_name('comp_foreign'))
add('compensation-document-token', 'anonymous', f"/document/compensation/{C('comp')}?token={DOCUMENTS['shared']['token']}", 200, PDF,
    renders=[render('compensation', C('comp'), None)],
    effect=effect(compensation_name('comp'), actor=None, links=[('instructorhours', 'h1'), ('instructorhours', 'h2')]),
    disposition='inline; filename=' + compensation_name('comp'))
add('compensation-bypass-header', 'anonymous', f"/document/compensation/{C('comp')}", 401, ANONYMOUS, headers={'Authorization': '$bypass'})
add('compensation-unknown', 'owner', f'/document/compensation/{UNKNOWN}', 404, exception('Compensation not found'))
add('compensation-deleted', 'owner', f"/document/compensation/{C('comp_deleted')}", 404, exception('Compensation not found'))
add('compensation-invalid-uuid', 'owner', '/document/compensation/x-y', 500, invalid_uuid('x-y'))
add('compensation-without-instructor', 'owner', f"/document/compensation/{C('comp_noinstructor')}", 500,
    exception("'NoneType' object has no attribute 'first_name'"))
add('compensation-without-date', 'owner', f"/document/compensation/{C('comp_nodate')}", 500,
    exception("'NoneType' object has no attribute 'date'"))
add('compensation-renderer-error', 'owner', f"/document/compensation/{C('comp')}", 500,
    failure('Renderer returned HTTP 400: {"error": "bad url"}'),
    renders=[render('compensation', C('comp'), answer=reply(400, 'application/json', '{"error": "bad url"}'))],
    effect=effect(compensation_name('comp'), actor='owner', celery=False))

# --- document/template/<uid> (IsAuthenticated | HasDocumentToken) ------------------------------------
TEMPLATE_NAME = '[Message reads own] Modulo privacy  v1 2 del $today.pdf'
DONE = {'msg': 'Generato con successo.'}
archive = lambda folder: {'model': 'sportassociationdocumentsarchive', 'folder': folder}
subscription_file = lambda key: {'model': 'subscriptionfile', 'subscription': key}

common('template', 'template', T('plain'))
add('template-owner', 'owner', f"/document/template/{T('plain')}", 200, DONE, renders=[render('template', T('plain'), query='?')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-full-collaborator-additional-data', 'full_collaborator', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': '$folder:own', 'subscription': S('plain')}},
    renders=[render('template', T('plain'), query=f"?subscription_id={S('plain')}&")],
    effect=effect(TEMPLATE_NAME, actor='full_collaborator', creates=[archive('own'), subscription_file('plain')]))
add('template-folder-text-id', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': '$folder-text:foreign'}}, renders=[render('template', T('plain'), query='?')],
    effect=effect(TEMPLATE_NAME, actor='owner', creates=[archive('foreign')]))
add('template-folder-unknown', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': 987654321, 'other': 1}}, renders=[render('template', T('plain'), query='?')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-folder-invalid', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': 'abc'}}, renders=[render('template', T('plain'), query='?')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-folder-null', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': None}}, renders=[render('template', T('plain'), query='?')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-subscription-unknown', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'subscription': UNKNOWN}}, renders=[render('template', T('plain'), query=f'?subscription_id={UNKNOWN}&')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-subscription-invalid', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'subscription': 'not-a-uuid'}}, renders=[render('template', T('plain'), query='?subscription_id=not-a-uuid&')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-subscription-null', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'subscription': None}}, renders=[render('template', T('plain'), query='?subscription_id=None&')],
    effect=effect(TEMPLATE_NAME, actor='owner'))
add('template-subscription-foreign', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'subscription': S('foreign')}}, renders=[render('template', T('plain'), query=f"?subscription_id={S('foreign')}&")],
    effect=effect(TEMPLATE_NAME, actor='owner', creates=[subscription_file('foreign')]))
add('template-scoped-athlete-denied', 'owner', f"/document/template/{T('plain')}", 403, SCOPED_DENIED, headers=SCOPED)
add('template-renderer-error', 'owner', f"/document/template/{T('plain')}", 200, DONE, method='POST',
    body={'additionalData': {'parent': '$folder:own'}},
    renders=[render('template', T('plain'), query='?', answer=reply(500, 'text/plain', 'down'))],
    effect=effect(TEMPLATE_NAME, actor='owner', celery=False, creates=[archive('own')]))
add('template-foreign', 'owner', f"/document/template/{T('foreign')}", 404, exception('Template not found'))
add('template-unknown', 'owner', f'/document/template/{UNKNOWN}', 404, exception('Template not found'))
add('template-invalid-uuid', 'owner', '/document/template/nope', 500, invalid_uuid('nope'))
add('template-athlete', 'athlete', f"/document/template/{T('plain')}", 500, exception('User is not a sport association'))
add('template-owner-without-association', 'owner_without_association', f"/document/template/{T('plain')}", 500,
    exception('SportAssociation matching query does not exist.'))
add('template-document-token', 'anonymous', f"/document/template/{T('plain')}?token={DOCUMENTS['shared']['token']}", 500,
    exception("'AnonymousUser' object has no attribute 'sport_association'"))

# --- document/medical-appointment/<uid> (IsAuthenticated | HasDocumentToken) ------------------------
REGIONS = {'agonistica': 'Visita Agonistica', 'non-agonistica': 'Visita Non Agonistica'}


def medical_name(key):
    row = MEDICAL[key]
    association = SUBSCRIPTIONS[row['subscription']]['association']
    return f"[{DENOMINATIONS[association]}] {REGIONS[row['region']]} del {row['date'] or 'None'}.pdf"


common('medical-appointment', 'medical', M('agonistica'))
for name, actor, key, who, query, authorization in (
        ('medical-owner', 'owner', 'agonistica', 'owner', '', '$bearer'),
        ('medical-non-agonistica', 'owner', 'non', 'owner', '', '$bearer'),
        ('medical-without-date', 'owner', 'nodate', 'owner', '', '$bearer'),
        ('medical-previous-foreign', 'owner', 'previous', 'owner', '', '$bearer'),
        ('medical-document-token', 'anonymous', 'agonistica', None, f"?token={DOCUMENTS['shared']['token']}", None)):
    add(name, actor, f'/document/medical-appointment/{M(key)}{query}', 200, PDF, renders=[render('medical-appointment', M(key), authorization)],
        effect=effect(medical_name(key), actor=who, links=[('medicalappointments', key)]), disposition='inline; filename=' + medical_name(key))
add('medical-unknown', 'owner', f'/document/medical-appointment/{UNKNOWN}', 500, exception("'NoneType' object has no attribute 'subscription'"))
add('medical-invalid-uuid', 'owner', '/document/medical-appointment/bad', 500, invalid_uuid('bad'))
add('medical-unknown-region', 'owner', f"/document/medical-appointment/{M('badregion')}", 500, exception("'unknown-region'"))
add('medical-renderer-error', 'owner', f"/document/medical-appointment/{M('agonistica')}", 500,
    failure('Renderer returned HTTP 404: '),
    renders=[render('medical-appointment', M('agonistica'), answer=reply(404, 'text/plain', ''))],
    effect=effect(medical_name('agonistica'), actor='owner', celery=False))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'document PDF routes through the renderer service',
    'ids': IDS, 'associations': ASSOCIATIONS, 'association_ids': ASSOCIATION_IDS, 'associates': ASSOCIATES, 'documents': DOCUMENTS,
    'subscriptions': SUBSCRIPTIONS, 'invoices': INVOICES, 'payments': PAYMENTS, 'instructors': INSTRUCTORS, 'hours': HOURS,
    'customer_invoices': CUSTOMER_INVOICES, 'templates': TEMPLATES, 'folders': FOLDERS, 'medical': MEDICAL, 'cases': cases},
    ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
