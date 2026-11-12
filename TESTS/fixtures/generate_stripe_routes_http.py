"""Author the stripe/* HTTP sequence (stripe-routes.md).

Six @api_view function views in BE/application/views/stripe_views.py, all in
the permission registry's EXCLUDED_ENDPOINTS (no registry key):

- stripe/info (GET), stripe/on-boarding and stripe/complete-on-boarding (POST)
  are IsAuthenticated and always answer 410 {'error': 'Stripe Connect
  onboarding is disabled for self-hosted direct Stripe.'}.
- stripe/pay/<payment_id> (POST, AllowAny): with ?payment_intent_id= the
  payment holding that intent is retrieved from Stripe first (succeeded with
  metadata: mark_payment_as_paid for it and for every metadata key, answer
  'payment already paid.'); then is_valid_uuid (400 ['badly formed hexadecimal UUID string']), the
  payment (404 'payment not found'), online payments (409), already paid
  (200 'paid'), the stored intent retrieved (succeeded: mark_payment_as_paid;
  InvalidRequestError: dropped; another amount: cleared and saved) and a new
  PaymentIntent created and saved. The answer is the client_secret.
- stripe/multiple-pay (POST, AllowAny): request.data.get('payments') (ids, or
  objects whose payment_id is used as the id: the rows are loaded here and
  serialized with PaymentSerializer, nothing else of an object is read), grouped by
  association; only the first group is processed: each payment is fetched
  (409 when online payments are off; a stored intent that succeeded marks it
  paid), the unpaid ones are summed into one PaymentIntent whose metadata maps
  payment id -> amount, and each is saved with the intent.
- stripe/webhook (POST, AllowAny): stripe.Webhook.construct_event with the
  configured webhook secret (400 without a body for any payload or signature
  error), then by event type; charge.succeeded marks the payments holding the
  intent (none: 400; errors are swallowed: 200).

mark_payment_as_paid marks the payment's first supplier invoice paid and saves
an unpaid payment paid now with type 'stripe' (update_fields); with an invoice
already linked nothing else happens; without one it creates one (numbered from
the association's invoices created in the current balance year), saves the
payment again with it, marks the course subscription or installment paid,
publishes print_document_invoice on commit (the request's Authorization,
DOCUMENT_BYPASS_TOKEN from the webhook) and notifies payment.user (see the paid
flow section).
Every outbound Stripe request is pinned (method, path, form body and the SDK's
headers) against the run's private Stripe stub, whose authored replies are
listed with the case.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path
from urllib.parse import quote_plus, urlencode

OUTPUT = Path(__file__).with_name('stripe_routes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{1520001 + n:012d}' for n in range(300))
# t6x1's own ids, so the cases and rows above keep their ids.
more = iter(f'a1000000-0000-4000-8000-{1529001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'
EMPTY = '<empty>'
CATEGORY = next(ids)
CATEGORY_NAME = 'Pagamenti Stripe fixture'
OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}
ASSOCIATE = {'id': next(ids), 'first': 'Mario', 'last': 'Rossi', 'sex': 'M', 'tax': 'RSSMRA90E10H501Z', 'born': '1990-05-10',
             'email': 'mario@example.test'}
SUBSCRIPTION = {'id': next(ids), 'created': '2026-08-01T10:00:00Z'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore Stripe', 'vat_number': '01234567891', 'email': 'fornitore-stripe@example.test'}
# Extra associations: one whose owner has online payments off, one without stored Stripe methods.
OFFLINE = {'user': next(ids), 'association': next(ids), 'denomination': 'Stripe offline', 'online': False, 'methods': ['card'],
           'checkout': None}
PLAIN = {'user': next(ids), 'association': next(ids), 'denomination': 'Stripe senza metodi è', 'online': True, 'methods': None,
         'checkout': 'Paga entro il 10 è'}
# t6x1: every JSON value the SDK encodes (float, nested list, a large numeric, a big int).
ENCODED_METHODS = ['card', 1.5, ['sepa_debit', None, 2.0, True], 1e+22, 12345678901234567890]
# A jsonb column keeps the numeric's value with its scale: 1e+22 (scale 0) is read back as that integer,
# so the association the SDK encodes from the database holds the integer token, not a float.
ENCODED_METHODS_READ = ['card', 1.5, ['sepa_debit', None, 2.0, True], 10000000000000000000000, 12345678901234567890]
ENCODED = {'user': next(more), 'association': next(more), 'denomination': 'Stripe metodi encodés', 'online': True,
           'methods': ENCODED_METHODS, 'checkout': None}
EXTRA_ASSOCIATIONS = {'offline': OFFLINE, 'plain': PLAIN, 'encoded': ENCODED}
DENOMINATIONS = {OWN: 'Message reads own', OFFLINE['association']: OFFLINE['denomination'], PLAIN['association']: PLAIN['denomination'],
                 ENCODED['association']: ENCODED['denomination']}
METHODS = {OWN: ['card', 'sepa_debit'], OFFLINE['association']: OFFLINE['methods'], PLAIN['association']: PLAIN['methods'],
           ENCODED['association']: ENCODED_METHODS_READ}
CHECKOUT = {OWN: None, OFFLINE['association']: None, PLAIN['association']: PLAIN['checkout'], ENCODED['association']: ENCODED['checkout']}
SECRET_KEY, WEBHOOK_SECRET, PUBLIC_KEY = 'sk_test_placeholder', 'whsec_placeholder', 'pk_test_placeholder'


def payment(amount='10.00', paid=False, *, association=OWN, subject=0, associate=True, user=True, intent=None, invoice=False,
            supplier_invoice=False, group=None, deleted=False, category=CATEGORY, description='Quota', created='2026-10-01T08:00:00Z',
            paid_on=None, meta_categories=None, kind='default', meta=None, identifier=None):
    return {'id': identifier or next(ids), 'association': association, 'subject': subject, 'amount': amount, 'paid': paid,
            'associate': associate, 'user': user, 'intent': intent, 'invoice': invoice, 'supplier_invoice': supplier_invoice, 'group': group,
            'deleted': deleted, 'category': category, 'description': description, 'created': created, 'payment_date': paid_on,
            'meta_categories': meta_categories, 'meta': meta, 'type': kind}


# Keys ascend in insertion order, so key order and physical order agree for multi-payment reads.
PAYMENTS = {
    # stripe/pay
    'pay_create_anon': payment(),
    'pay_create_owner': payment(amount='12.50'),
    'pay_create_collab': payment(amount='7.00'),
    'pay_plain_methods': payment(amount='20.00', association=PLAIN['association']),
    'pay_defaults': payment(amount='3.00', category=None),
    'pay_reuse': payment(amount='15.00', intent='pi_stub_reuse'),
    'pay_null_secret': payment(amount='15.00', intent='pi_stub_blank'),
    'pay_amount_changed': payment(amount='10.00', intent='pi_stub_old'),
    'pay_retrieve_missing': payment(amount='9.99', intent='pi_stub_gone'),
    'pay_idempotency_error': payment(intent='pi_stub_idem'),
    'pay_card_error': payment(),
    'pay_retry': payment(amount='11.00'),
    'pay_succeeded': payment(amount='30.00', intent='pi_stub_ok', invoice=True, supplier_invoice=True),
    'pay_succeeded_no_user': payment(amount='31.00', intent='pi_stub_ok_nouser', invoice=True, supplier_invoice=True, user=False),
    'pay_query_first': payment(amount='40.00', intent='pi_stub_query', invoice=True),
    'pay_query_second': payment(amount='41.00', intent='pi_stub_query', invoice=True, supplier_invoice=True),
    'pay_query_pending': payment(amount='42.00', intent='pi_stub_pending'),
    'pay_query_bad_key': payment(amount='43.00', intent='pi_stub_badkey', invoice=True),
    'pay_paid': payment(amount='5.00', paid=True, paid_on='2026-04-01T00:00:00Z', intent='pi_stub_paid'),
    'pay_offline': payment(association=OFFLINE['association']),
    'pay_deleted': payment(deleted=True),
    'pay_group': payment(group='own_group'),
    'pay_no_associate': payment(associate=False),
    'pay_quoted_intent': payment(amount='8.00', intent='pi stub/1'),
    'pay_foreign': payment(association=FOREIGN),
    # t6x1: every JSON payment_method_types value, and created intent ids that are not plain strings.
    'pay_encoded_methods': payment(amount='21.00', association=ENCODED['association'], identifier=next(more)),
    'pay_created_noid': payment(amount='22.50', identifier=next(more)),
    'pay_created_nullid': payment(amount='23.50', identifier=next(more)),
    'pay_created_numid': payment(amount='24.50', identifier=next(more)),
    # stripe/multiple-pay
    'mp_ids_a': payment(amount='10.00'),
    'mp_ids_b': payment(amount='5.50'),
    'mp_dict': payment(amount='6.00', association=PLAIN['association']),
    'mp_offline': payment(amount='6.50', association=OFFLINE['association']),
    'mp_no_associate': payment(amount='7.50', associate=False),
    'mp_succeeded': payment(amount='8.50', intent='pi_stub_mp_ok', invoice=True, supplier_invoice=True),
    'mp_two_own': payment(amount='9.50'),
    'mp_two_plain': payment(amount='2.50', association=PLAIN['association']),
    'mp_paid': payment(amount='4.00', paid=True, paid_on='2026-04-02T00:00:00Z'),
    'mp_group': payment(amount='1.50', group='own_group'),
    'mp_forged': payment(amount='3.00'),
    'mp_bare': payment(amount='3.25', association=PLAIN['association']),
    'mp_second_key': payment(amount='2.75'),
    # t6x1: id lists (a dropped null, an integer id that matches no row) and encoded methods.
    'mp_encoded_methods': payment(amount='6.75', association=ENCODED['association'], identifier=next(more)),
    'mp_null_a': payment(amount='7.25', identifier=next(more)),
    'mp_null_b': payment(amount='7.75', identifier=next(more)),
    'mp_int_valid': payment(amount='8.25', identifier=next(more)),
    'mp_reject': payment(amount='8.75', identifier=next(more)),
    # stripe/webhook
    'wh_invoiced': payment(amount='50.00', intent='pi_stub_wh', invoice=True, supplier_invoice=True),
    'wh_paid': payment(amount='51.00', paid=True, paid_on='2026-04-03T00:00:00Z', intent='pi_stub_wh_paid'),
    'wh_group': payment(amount='52.00', intent='pi_stub_wh_group', group='own_group', invoice=True, supplier_invoice=True),
    # t6x1: the two payment_intent values str() writes for a float and for an object.
    'wh_float_intent': payment(amount='53.00', intent='1.5', invoice=True, supplier_invoice=True, identifier=next(more)),
    'wh_json_intent': payment(amount='54.00', intent=json.dumps({'b': 1, 'a': 'è'}, sort_keys=True, indent=2), invoice=True,
                              supplier_invoice=True, identifier=next(more)),
    # paid flow (unpaid payments without an invoice), in the association without seeded invoices
    'pf_wh_other': payment(amount='25.00', association=PLAIN['association'], intent='pi_stub_pf_other'),
    'pf_pay_subscription': payment(amount='30.00', association=PLAIN['association'], subject=1, intent='pi_stub_pf_sub',
                                   meta_categories=[{'amount': '4.50'}, {'amount': True}, {'amount': None}]),
    'pf_wh_subscription_plain': payment(amount='12.00', association=PLAIN['association'], subject=1, intent='pi_stub_pf_sub_plain'),
    'pf_wh_course': payment(amount='60.00', association=PLAIN['association'], subject=2, intent='pi_stub_pf_course'),
    'pf_wh_installment': payment(amount='20.00', association=PLAIN['association'], subject=2, intent='pi_stub_pf_installment'),
    'pf_wh_carnet': payment(amount='33.00', association=PLAIN['association'], subject=2, intent='pi_stub_pf_carnet'),
    'pf_wh_course_none': payment(amount='14.00', association=PLAIN['association'], subject=2, intent='pi_stub_pf_none'),
    'pf_wh_no_user': payment(amount='15.00', association=PLAIN['association'], user=False, intent='pi_stub_pf_nouser'),
    'pf_pay_anonymous': payment(amount='16.00', association=PLAIN['association'], intent='pi_stub_pf_anon'),
    'pf_mp': payment(amount='17.00', association=PLAIN['association'], intent='pi_stub_pf_mp'),
    'pf_wh_group': payment(amount='18.00', association=PLAIN['association'], intent='pi_stub_pf_group', group='own_group'),
    'pf_wh_typed': payment(amount='19.00', association=PLAIN['association'], intent='pi_stub_pf_typed', kind='stripe'),
    'pf_wh_cash': payment(amount='21.00', association=PLAIN['association'], intent='pi_stub_pf_cash', kind='cash'),
    'pf_wh_defaults': payment(amount='22.00', association=PLAIN['association'], intent='pi_stub_pf_defaults', category=None, description=None),
    # Rare inputs: another spelling of the group header, fees that round on a tie, meta names that are not text.
    'pf_wh_group_spelling': payment(amount='18.50', association=PLAIN['association'], intent='pi_stub_pf_spelling', group='own_group'),
    'pf_wh_tie': payment(amount='10.00', association=PLAIN['association'], subject=1, intent='pi_stub_pf_tie',
                         meta_categories=[{'amount': '0.125'}]),
    'pf_wh_name_float': payment(amount='26.00', association=PLAIN['association'], subject=1, intent='pi_stub_pf_name_float',
                                meta={'subscription_data': {'name': 1.5}}),
    'pf_wh_name_list': payment(amount='27.00', association=PLAIN['association'], subject=1, intent='pi_stub_pf_name_list',
                               meta={'subscription_data': {'name': ['Agonisti', {'anno': None, 'ok': True}, 2]}}),
}
# The t6x1 rows that also need invoice and supplier-invoice rows, whose ids come from `more`.
T6X1_INVOICE_LABELS = ('wh_float_intent', 'wh_json_intent')
COURSE = {'id': next(ids), 'title': 'Corso Stripe'}
CARNET = {'id': next(ids), 'title': 'Carnet Stripe'}
COURSE_SUBSCRIPTIONS = {'cs_pf': {'id': next(ids), 'payment': 'pf_wh_course'}, 'cs_pf_parent': {'id': next(ids), 'payment': None}}
INSTALLMENTS = {'i_pf': {'id': next(ids), 'payment': 'pf_wh_installment', 'cs': 'cs_pf_parent'}}
HOLDINGS = {'h_pf': {'id': next(ids), 'payment': 'pf_wh_carnet'}}
INVOICES = {label: {'id': next(more) if label in T6X1_INVOICE_LABELS else next(ids), 'number': n}
            for n, label in enumerate((k for k, v in PAYMENTS.items() if v['invoice']), start=900)}
SUPPLIER_INVOICES = {label: {'id': next(more) if label in T6X1_INVOICE_LABELS else next(ids), 'identifier': f'FT-S{n}',
                             'amount': PAYMENTS[label]['amount']}
                     for n, label in enumerate(k for k, v in PAYMENTS.items() if v['supplier_invoice'])}
STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        stripe=(), signature=None):
    case = {'name': f'stripe-routes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect or nothing(), 'stripe': list(stripe)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if signature is not None:
        case['signature'] = signature
    cases.append(case)


# --- Stripe exchanges --------------------------------------------------------

def form(params):
    """stripe._encode._api_encode + urlencode, with the brackets left literal (the SDK's own body).

    A list item that is not a dict is yielded as-is, so urlencode writes
    str(item): a nested list is its Python repr. None is dropped at the
    parameter/dict level and written as 'None' inside a list.
    """
    def encode(data):
        pairs = []
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, list):
                for index, item in enumerate(value):
                    subkey = f'{key}[{index}]'
                    if isinstance(item, dict):
                        for sub, subvalue in encode(item):
                            pairs.append((f'{subkey}[{sub}]', subvalue))
                    else:
                        pairs.append((subkey, item))
            elif isinstance(value, dict):
                for sub, subvalue in encode(value):
                    pairs.append((f'{key}[{sub}]', subvalue))
            else:
                pairs.append((key, value))
        return pairs
    return urlencode(encode(params)).replace('%5B', '[').replace('%5D', ']')


def intent(identifier, amount, status='requires_payment_method', secret='$default', metadata=None):
    return {'id': identifier, 'object': 'payment_intent', 'amount': amount, 'currency': 'eur',
            'client_secret': f'{identifier}_secret_stub' if secret == '$default' else secret, 'status': status, 'metadata': metadata or {}}


def created_intent(secret, amount, *, identifier='omitted'):
    """A PaymentIntent.create reply: no id key, an id of None, or any other id value."""
    reply = {'object': 'payment_intent', 'amount': amount, 'currency': 'eur', 'client_secret': secret,
             'status': 'requires_payment_method', 'metadata': {}}
    if identifier != 'omitted':
        reply['id'] = identifier
    return reply


def exchange(method, path, body, response, *, status=200, telemetry=None, same_key_as_previous=False):
    """One authored request the server must send and the stub's reply.

    telemetry: the Request-Id of the previous answered request in the same
    HTTP request (X-Stripe-Client-Telemetry), or None for no header."""
    return {'method': method, 'path': path, 'body': body, 'status': status, 'response': response, 'telemetry': telemetry,
            'same_key_as_previous': same_key_as_previous}


def retrieve(identifier, response, *, status=200, telemetry=None):
    return exchange('GET', '/v1/payment_intents/' + quote_plus(identifier), '', response, status=status, telemetry=telemetry)


def create(params, response, *, status=200, telemetry=None, same_key_as_previous=False):
    return exchange('POST', '/v1/payment_intents', form(params), response, status=status, telemetry=telemetry,
                    same_key_as_previous=same_key_as_previous)


def error(kind, code, message, param=None):
    return {'error': {'type': kind, 'code': code, 'message': message, 'param': param}}


def pay_description(label):
    row = STATE[label]
    return (f"Associato: {ASSOCIATE['first']} {ASSOCIATE['last']} - Utente: {OWNER_USER['first_name']} {OWNER_USER['last_name']} - "
            f"Pagamento di {row['amount']} euro per {DENOMINATIONS[row['association']]}")


def cents(*labels):
    from decimal import Decimal
    return int(sum(Decimal(STATE[label]['amount']) for label in labels) * 100)


def pay_params(label):
    row = STATE[label]
    return {'amount': cents(label), 'currency': 'eur', 'description': pay_description(label),
            'payment_method_types': METHODS[row['association']]}


def multi_params(labels, methods=('card', 'sepa_debit')):
    return {'amount': cents(*labels), 'currency': 'eur',
            'description': ''.join(f"{ASSOCIATE['first'][:3]}-{ASSOCIATE['last'][:3]}-€{STATE[label]['amount']}|" for label in labels),
            'payment_method_types': list(methods), 'metadata': {STATE[label]['id']: STATE[label]['amount'] for label in labels}}


# --- rendering ---------------------------------------------------------------

def associate():
    row = ASSOCIATE
    return {'associate_id': row['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': row['born'],
            'email': row['email'], 'sex': row['sex'], 'first_name': row['first'], 'last_name': row['last'], 'is_minor': False,
            'sport_association': OWN, 'tax_code': row['tax'], 'user': OWNER, 'phone': None, 'family': None, 'family_role': None}


def payment_item(label):
    """PaymentSerializer of the payment's current authored state."""
    row = STATE[label]
    invoice = None
    if row['invoice']:
        number = INVOICES[label]
        invoice = {'invoice_id': number['id'], 'document_token': None, 'creation_date': '2026-09-01T08:00:00Z', 'membership_fee': '0.00',
                   'activity_fee': row['amount'], 'meta_payment_categories': None, 'description': 'Ricevuta Stripe', 'number': number['number'],
                   'archived': False, 'cancelled': False, 'meta': None, 'imported_from_associami': False, 'deleted': False,
                   'document_pdf': None, 'sport_association': row['association'], 'selected_tutor': None, 'group': None,
                   'attached_documents': []}
    subscription = SUBSCRIPTION['id'] if row['subject'] == 0 and row['associate'] else None
    return {'payment_id': row['id'], 'user': OWNER_USER if row['user'] else None, 'associate': associate() if row['associate'] else None,
            'subscription_id': subscription, 'instructor_id': None, 'instructor': None, 'supplier': None, 'supplier_id': None,
            'custom_account_type': None, 'custom_account_name': None, 'invoice': invoice, 'attachments': [],
            'payment_category_name': CATEGORY_NAME if row['category'] == CATEGORY else None, 'type': row['type'], 'subject': row['subject'],
            'amount': row['amount'], 'creation_date': row['created'], 'payment_date': row['payment_date'], 'paid': row['paid'],
            'expense': False, 'description': row['description'], 'meta_payment_categories': None, 'payment_intent_id': row['intent'],
            'archived': False, 'invoice_fiscal_code': None, 'meta': None, 'notes': None, 'deleted': row['deleted'], 'course': None,
            'payment_category': row['category'], 'sport_association': row['association'], 'custom_accounts': None, 'signature': None,
            'group': IDS[row['group']] if row['group'] else None}


# --- effects -----------------------------------------------------------------

def effect(*, payments=None, invoices=None, audit=()):
    """payments: {label: {'intent': value}}; invoices (supplier invoices): {label: {'paid': True, 'payment_date': '$now'}};
    audit: the new auditlog entries in order."""
    return {'payments_changed': payments or {}, 'supplier_invoices_changed': invoices or {}, 'audit': list(audit)}


def nothing():
    return effect()


def audited(model, label, changes, actor):
    association = PAYMENTS[label]['association']
    return {'model': model, 'label': label, 'action': 1, 'changes': changes, 'actor': actor, 'index': [association, 'direct']}


def set_intent(label, value, actor, *, category=False):
    """Payment.save() after payment.payment_intent_id = value (and the default category for a null one)."""
    row = STATE[label]
    changes = {'payment_intent_id': ['None' if row['intent'] is None else row['intent'], 'None' if value is None else value]}
    if category and row['category'] is None:
        changes['payment_category'] = ['None', '$default_category']
        row['category'] = '$default_category'
    row['intent'] = value
    return audited('payment', label, changes, actor)


def supplier_paid(label, actor):
    return audited('invoicesuppliers', label, {'paid': ['False', 'True'], 'payment_date': ['None', '$now']}, actor)


def stripe_marked(label, actor):
    """mark_payment_as_paid's payment.save(update_fields=['paid', 'payment_date', 'type']) of an unpaid payment
    (audited with the changed update fields only)."""
    row = STATE[label]
    changes = {'paid': ['False', 'True'], 'payment_date': ['None' if row['payment_date'] is None else row['payment_date'], '$now']}
    if row['type'] != 'stripe':
        changes['type'] = [row['type'], 'stripe']
    row.update(paid=True, payment_date='$now', type='stripe')
    return audited('payment', label, changes, actor)


def marked_state(label):
    """payments_changed entry of a payment marked paid that keeps its existing invoice."""
    return {'paid': True, 'payment_date': '$now', 'invoice': INVOICES[label]['id'], 'type': 'stripe'}


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
GONE = {'error': 'Stripe Connect onboarding is disabled for self-hosted direct Stripe.'}
OFFLINE_ANSWER = {'error': 'Online payments are not configured.'}
MARKED = {'data': {'msg': 'payment mark as payed.', 'status': 'paid'}}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
GROUP = {'X-Group-Id': IDS['own_group']}
OTHER_GROUP = {'X-Group-Id': IDS['foreign_group']}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'], 'parses': PARSES}
P = lambda label: PAYMENTS[label]['id']
PAY = lambda label: f'/stripe/pay/{P(label) if label in PAYMENTS else label}'
MULTI, WEBHOOK = '/stripe/multiple-pay', '/stripe/webhook'
INFO, ONBOARD, COMPLETE = '/stripe/info', '/stripe/on-boarding', '/stripe/complete-on-boarding'

# --- onboarding (disabled) ---------------------------------------------------

add('info-anonymous', 'anonymous', INFO, 401, ANONYMOUS, method='GET')
add('info-owner-gone', 'owner', INFO, 410, GONE, method='GET')
add('info-athlete-gone', 'athlete', INFO, 410, GONE, method='GET')
add('info-restricted-gone', 'restricted_collaborator', INFO, 410, GONE, method='GET')
add('info-admin-without-session', 'admin', INFO, 403, ADMIN, method='GET')
add('info-admin-impersonating-owner', 'admin', INFO, 410, GONE, method='GET', headers=ADMIN_OWNER)
add('info-wrong-method', 'owner', INFO, 405, method('POST'))
add('info-options', 'owner', INFO, 200, options('Stripe Info', 'This endpoint returns onboarding info\n:param request:\n:return:'),
    method='OPTIONS')
add('on-boarding-anonymous', 'anonymous', ONBOARD, 401, ANONYMOUS)
add('on-boarding-owner-gone', 'owner', ONBOARD, 410, GONE, body={'country': 'IT'})
add('on-boarding-wrong-method', 'owner', ONBOARD, 405, method('GET'), method='GET')
add('on-boarding-options', 'owner', ONBOARD, 200,
    options('Stripe On Boarding', 'This endpoint start the Stripe connection by prefilling the base info\n:param request:\n:return:'),
    method='OPTIONS')
add('complete-on-boarding-anonymous', 'anonymous', COMPLETE, 401, ANONYMOUS)
add('complete-on-boarding-collaborator-gone', 'full_collaborator', COMPLETE, 410, GONE)
add('complete-on-boarding-wrong-method', 'owner', COMPLETE, 405, method('PUT'), method='PUT')

# --- stripe/pay ----------------------------------------------------------------

add('pay-options', 'anonymous', PAY('pay_create_anon'), 200, options('Stripe Pay', 'This endpoint allows to pay by providing a payment id\n'
                                                                    ':payment_id, request:\n:return:'), method='OPTIONS')
add('pay-wrong-method', 'anonymous', PAY('pay_create_anon'), 405, method('GET'), method='GET')
# is_valid_uuid raises its own ValidationError before the view's 'not valid uuid'.
add('pay-malformed', 'anonymous', PAY('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'])
add('pay-missing-not-found', 'anonymous', PAY(IDS['missing']), 404, {'detail': 'payment not found'})
add('pay-deleted-not-found', 'anonymous', PAY('pay_deleted'), 404, {'detail': 'payment not found'})
add('pay-other-group-not-found', 'anonymous', PAY('pay_group'), 404, {'detail': 'payment not found'}, headers=OTHER_GROUP)
add('pay-offline-conflict', 'anonymous', PAY('pay_offline'), 409, OFFLINE_ANSWER)
add('pay-paid', 'anonymous', PAY('pay_paid'), 200, MARKED)
add('pay-no-associate-server-error', 'anonymous', PAY('pay_no_associate'), 500, SERVER)
add('pay-scoped-session-foreign-denied', 'owner', PAY('pay_foreign'), 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, headers=OWNER_FULL)

for name, actor, label, headers in (('pay-create-anonymous', 'anonymous', 'pay_create_anon', None),
                                    ('pay-create-owner', 'owner', 'pay_create_owner', None),
                                    ('pay-create-full-collaborator', 'full_collaborator', 'pay_create_collab', None),
                                    ('pay-create-without-stored-methods', 'anonymous', 'pay_plain_methods', None),
                                    ('pay-create-fills-default-category', 'owner', 'pay_defaults', None),
                                    ('pay-create-group', 'anonymous', 'pay_group', GROUP)):
    new = f'pi_stub_{label}'
    params = pay_params(label)
    add(name, actor, PAY(label), 200, {'data': {'client_secret': f'{new}_secret_stub'}}, headers=headers,
        stripe=[create(params, intent(new, params['amount']))],
        effect=effect(payments={label: {'intent': new}}, audit=[set_intent(label, new, actor, category=True)]))

add('pay-reuses-pending-intent', 'anonymous', PAY('pay_reuse'), 200, {'data': {'client_secret': 'pi_stub_reuse_secret_stub'}},
    stripe=[retrieve('pi_stub_reuse', intent('pi_stub_reuse', 1500))])
add('pay-blank-secret-is-null', 'anonymous', PAY('pay_null_secret'), 200, {'data': {'client_secret': None}},
    stripe=[retrieve('pi_stub_blank', intent('pi_stub_blank', 1500, secret=''))])
first = set_intent('pay_amount_changed', None, 'anonymous')
params = pay_params('pay_amount_changed')
add('pay-amount-changed-recreates', 'anonymous', PAY('pay_amount_changed'), 200, {'data': {'client_secret': 'pi_stub_new_secret_stub'}},
    stripe=[retrieve('pi_stub_old', intent('pi_stub_old', 999)),
            create(params, intent('pi_stub_new', 1000), telemetry='req_stub_1')],
    effect=effect(payments={'pay_amount_changed': {'intent': 'pi_stub_new'}},
                  audit=[first, set_intent('pay_amount_changed', 'pi_stub_new', 'anonymous')]))
params = pay_params('pay_retrieve_missing')
add('pay-missing-intent-recreates', 'anonymous', PAY('pay_retrieve_missing'), 200, {'data': {'client_secret': 'pi_stub_again_secret_stub'}},
    stripe=[retrieve('pi_stub_gone', error('invalid_request_error', 'resource_missing', "No such payment_intent: 'pi_stub_gone'", 'intent'),
                     status=404),
            create(params, intent('pi_stub_again', 999), telemetry='req_stub_1')],
    effect=effect(payments={'pay_retrieve_missing': {'intent': 'pi_stub_again'}},
                  audit=[set_intent('pay_retrieve_missing', 'pi_stub_again', 'anonymous')]))
add('pay-idempotency-error-server-error', 'anonymous', PAY('pay_idempotency_error'), 500, SERVER,
    stripe=[retrieve('pi_stub_idem', error('idempotency_error', None, 'Keys for idempotent requests can only be used once.'), status=400)])
add('pay-card-error-server-error', 'anonymous', PAY('pay_card_error'), 500, SERVER,
    stripe=[create(pay_params('pay_card_error'), error('card_error', 'card_declined', 'Your card was declined.'), status=402)])
params = pay_params('pay_retry')
add('pay-create-retries-server-error', 'owner', PAY('pay_retry'), 200, {'data': {'client_secret': 'pi_stub_retry_secret_stub'}},
    stripe=[create(params, error('api_error', None, 'Temporary failure.'), status=500),
            create(params, intent('pi_stub_retry', 1100), same_key_as_previous=True)],
    effect=effect(payments={'pay_retry': {'intent': 'pi_stub_retry'}}, audit=[set_intent('pay_retry', 'pi_stub_retry', 'owner')]))
add('pay-succeeded-marks-supplier-invoice', 'anonymous', PAY('pay_succeeded'), 200, MARKED,
    stripe=[retrieve('pi_stub_ok', intent('pi_stub_ok', 3000, status='succeeded'))],
    effect=effect(payments={'pay_succeeded': marked_state('pay_succeeded')}, invoices={'pay_succeeded': {'paid': True, 'payment_date': '$now'}},
                  audit=[supplier_paid('pay_succeeded', 'anonymous'), stripe_marked('pay_succeeded', 'anonymous')]))
add('pay-succeeded-without-user-server-error', 'owner', PAY('pay_succeeded_no_user'), 500, SERVER,
    stripe=[retrieve('pi_stub_ok_nouser', intent('pi_stub_ok_nouser', 3100, status='succeeded'))],
    effect=effect(payments={'pay_succeeded_no_user': marked_state('pay_succeeded_no_user')},
                  invoices={'pay_succeeded_no_user': {'paid': True, 'payment_date': '$now'}},
                  audit=[supplier_paid('pay_succeeded_no_user', 'owner'), stripe_marked('pay_succeeded_no_user', 'owner')]))
add('pay-query-intent-marks-metadata', 'anonymous', PAY('pay_query_first') + '?payment_intent_id=pi_stub_query', 200,
    {'data': {'msg': 'payment already paid.'}},
    stripe=[retrieve('pi_stub_query', intent('pi_stub_query', 8100, status='succeeded',
                                             metadata={P('pay_query_first'): '40.00', P('pay_query_second'): '41.00'}))],
    effect=effect(payments={'pay_query_first': marked_state('pay_query_first'), 'pay_query_second': marked_state('pay_query_second')},
                  invoices={'pay_query_second': {'paid': True, 'payment_date': '$now'}},
                  audit=[stripe_marked('pay_query_first', 'anonymous'), supplier_paid('pay_query_second', 'anonymous'),
                         stripe_marked('pay_query_second', 'anonymous')]))
add('pay-query-intent-pending-falls-through', 'anonymous', PAY('pay_query_pending') + '?payment_intent_id=pi_stub_pending', 200,
    {'data': {'client_secret': 'pi_stub_pending_secret_stub'}},
    stripe=[retrieve('pi_stub_pending', intent('pi_stub_pending', 4200, status='processing')),
            retrieve('pi_stub_pending', intent('pi_stub_pending', 4200, status='processing'), telemetry='req_stub_1')])
add('pay-query-intent-unknown-uses-path', 'anonymous', PAY('pay_paid') + '?payment_intent_id=pi_stub_nobody', 200, MARKED)
add('pay-query-intent-bad-metadata-key-server-error', 'anonymous', PAY('pay_query_bad_key') + '?payment_intent_id=pi_stub_badkey', 500, SERVER,
    stripe=[retrieve('pi_stub_badkey', intent('pi_stub_badkey', 4300, status='succeeded', metadata={'order': '43.00'}))],
    effect=effect(payments={'pay_query_bad_key': marked_state('pay_query_bad_key')}, audit=[stripe_marked('pay_query_bad_key', 'anonymous')]))
add('pay-quoted-intent-id', 'anonymous', PAY('pay_quoted_intent'), 200, {'data': {'client_secret': 'pi stub/1_secret_stub'}},
    stripe=[retrieve('pi stub/1', intent('pi stub/1', 800))])
# t6x1: every JSON value of the association's methods (float, nested list, 1e+22, big int).
new = 'pi_stub_pay_encoded_methods'
params = pay_params('pay_encoded_methods')
add('pay-encodes-python-json-methods', 'anonymous', PAY('pay_encoded_methods'), 200, {'data': {'client_secret': f'{new}_secret_stub'}},
    stripe=[create(params, intent(new, params['amount']))],
    effect=effect(payments={'pay_encoded_methods': {'intent': new}}, audit=[set_intent('pay_encoded_methods', new, 'anonymous')]))
# payment_intent.stripe_id (getattr(self, 'id')): a missing key is an AttributeError before the save.
params = pay_params('pay_created_noid')
add('pay-created-intent-without-id-server-error', 'anonymous', PAY('pay_created_noid'), 500, SERVER,
    stripe=[create(params, created_intent('pi_stub_noid_secret_stub', params['amount']))])
# stripe_id None is saved as None: the stored value does not change, so there is no audit entry.
params = pay_params('pay_created_nullid')
add('pay-created-intent-with-null-id', 'anonymous', PAY('pay_created_nullid'), 200, {'data': {'client_secret': 'pi_stub_nullid_secret_stub'}},
    stripe=[create(params, created_intent('pi_stub_nullid_secret_stub', params['amount'], identifier=None))])
# A number id is str() for the CharField.
params = pay_params('pay_created_numid')
add('pay-created-intent-with-integer-id', 'anonymous', PAY('pay_created_numid'), 200, {'data': {'client_secret': 'pi_stub_numid_secret_stub'}},
    stripe=[create(params, created_intent('pi_stub_numid_secret_stub', params['amount'], identifier=5))],
    effect=effect(payments={'pay_created_numid': {'intent': '5'}}, audit=[set_intent('pay_created_numid', '5', 'anonymous')]))

# --- stripe/multiple-pay -------------------------------------------------------

NO_PAYMENTS = {'data': {'msg': 'no payments provided.'}}
add('multiple-pay-options', 'anonymous', MULTI, 200, options('Stripe Multiple Pay'), method='OPTIONS')
add('multiple-pay-wrong-method', 'anonymous', MULTI, 405, method('GET'), method='GET')
add('multiple-pay-missing', 'anonymous', MULTI, 400, NO_PAYMENTS, body={})
add('multiple-pay-empty', 'anonymous', MULTI, 400, NO_PAYMENTS, body={'payments': []})
add('multiple-pay-empty-object', 'anonymous', MULTI, 400, NO_PAYMENTS, body={'payments': {}})
add('multiple-pay-null-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': None})
add('multiple-pay-number-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': 3})
add('multiple-pay-string-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': 'abc'})
add('multiple-pay-list-body-server-error', 'anonymous', MULTI, 500, SERVER, body=[P('mp_ids_a')])
add('multiple-pay-form-server-error', 'anonymous', MULTI, 500, SERVER, raw_body='payments=' + P('mp_ids_a'),
    content_type='application/x-www-form-urlencoded')
add('multiple-pay-unknown-ids-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [IDS['missing']]})
add('multiple-pay-malformed-id-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': ['not-a-uuid']})
add('multiple-pay-object-without-id-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [{'sport_association': OWN}]})
add('multiple-pay-object-with-unknown-id-server-error', 'anonymous', MULTI, 500, SERVER,
    body={'payments': [{'payment_id': IDS['missing'], 'sport_association': OWN}]})
add('multiple-pay-object-with-null-id-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [{'payment_id': None}]})
add('multiple-pay-object-list-item-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [{'payment_id': P('mp_dict')}, 5]})
add('multiple-pay-offline-conflict', 'anonymous', MULTI, 409, OFFLINE_ANSWER,
    body={'payments': [{'payment_id': P('mp_offline'), 'sport_association': OFFLINE['association']}]})
add('multiple-pay-no-associate-server-error', 'anonymous', MULTI, 500, SERVER,
    body={'payments': [{'payment_id': P('mp_no_associate'), 'sport_association': OWN}]})
add('multiple-pay-other-group-server-error', 'anonymous', MULTI, 500, SERVER,
    body={'payments': [{'payment_id': P('mp_group'), 'sport_association': OWN}]}, headers=OTHER_GROUP)

serialized = [payment_item('mp_ids_a'), payment_item('mp_ids_b')]
params = multi_params(['mp_ids_a', 'mp_ids_b'])
add('multiple-pay-ids', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': [P('mp_ids_a'), P('mp_ids_b'), P('mp_paid')]},
    stripe=[create(params, intent('pi_stub_multi', params['amount']))],
    effect=effect(payments={'mp_ids_a': {'intent': 'pi_stub_multi'}, 'mp_ids_b': {'intent': 'pi_stub_multi'}},
                  audit=[set_intent('mp_ids_a', 'pi_stub_multi', 'anonymous'), set_intent('mp_ids_b', 'pi_stub_multi', 'anonymous')]))
# The paid payment is serialized and grouped, then left out of the unpaid sum.
CASE = cases[-1]
CASE['response']['payments'].append(payment_item('mp_paid'))
# Objects only name the payments: the answer carries the stored rows, not what the request held.
sent = [{'payment_id': P('mp_dict'), 'sport_association': PLAIN['association'], 'note': 'ignored', 'amount': 1}]
serialized = [payment_item('mp_dict')]
params = multi_params(['mp_dict'])
add('multiple-pay-objects-use-stored-rows', 'owner', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_dict_secret_stub', 'info': {'checkout_info': PLAIN['checkout']}}, 'payments': serialized},
    body={'payments': sent}, stripe=[create(params, intent('pi_stub_multi_dict', params['amount']))],
    effect=effect(payments={'mp_dict': {'intent': 'pi_stub_multi_dict'}}, audit=[set_intent('mp_dict', 'pi_stub_multi_dict', 'owner')]))
# The association and the amount an object holds are not read: the checkout text and the sum come from the stored rows.
sent = [{'payment_id': P('mp_forged'), 'sport_association': PLAIN['association'], 'amount': 99, 'paid': True}]
serialized = [payment_item('mp_forged')]
params = multi_params(['mp_forged'])
add('multiple-pay-objects-ignore-sent-association-and-amount', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_forged_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': sent}, stripe=[create(params, intent('pi_stub_multi_forged', params['amount']))],
    effect=effect(payments={'mp_forged': {'intent': 'pi_stub_multi_forged'}}, audit=[set_intent('mp_forged', 'pi_stub_multi_forged', 'anonymous')]))
sent = [{'payment_id': P('mp_bare')}]
serialized = [payment_item('mp_bare')]
params = multi_params(['mp_bare'])
add('multiple-pay-objects-with-only-an-id', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_bare_secret_stub', 'info': {'checkout_info': PLAIN['checkout']}}, 'payments': serialized},
    body={'payments': sent}, stripe=[create(params, intent('pi_stub_multi_bare', params['amount']))],
    effect=effect(payments={'mp_bare': {'intent': 'pi_stub_multi_bare'}}, audit=[set_intent('mp_bare', 'pi_stub_multi_bare', 'anonymous')]))
sent = [{'payment_id': P('mp_succeeded'), 'sport_association': OWN}]
serialized = [payment_item('mp_succeeded')]
# The marked payment is saved paid, so the unpaid set is empty: a zero-amount intent without metadata.
add('multiple-pay-succeeded-marked-then-zero-amount', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_ok_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': sent},
    stripe=[retrieve('pi_stub_mp_ok', intent('pi_stub_mp_ok', 850, status='succeeded')),
            create({'amount': 0, 'currency': 'eur', 'description': '', 'payment_method_types': ['card', 'sepa_debit'], 'metadata': {}},
                   intent('pi_stub_multi_ok', 0), telemetry='req_stub_1')],
    effect=effect(payments={'mp_succeeded': marked_state('mp_succeeded')}, invoices={'mp_succeeded': {'paid': True, 'payment_date': '$now'}},
                  audit=[supplier_paid('mp_succeeded', 'anonymous'), stripe_marked('mp_succeeded', 'anonymous')]))
sent = [{'payment_id': P('mp_two_own'), 'sport_association': OWN}, {'payment_id': P('mp_two_plain'), 'sport_association': PLAIN['association']}]
serialized = [payment_item('mp_two_own'), payment_item('mp_two_plain')]
params = multi_params(['mp_two_own'])
add('multiple-pay-first-association-only', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_two_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': sent}, stripe=[create(params, intent('pi_stub_multi_two', params['amount']))],
    effect=effect(payments={'mp_two_own': {'intent': 'pi_stub_multi_two'}}, audit=[set_intent('mp_two_own', 'pi_stub_multi_two', 'anonymous')]))
sent = [{'payment_id': P('mp_paid'), 'sport_association': OWN}]
add('multiple-pay-nothing-unpaid-zero-amount', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_zero_secret_stub', 'info': {'checkout_info': None}}, 'payments': [payment_item('mp_paid')]},
    body={'payments': sent}, stripe=[create({'amount': 0, 'currency': 'eur', 'description': '', 'payment_method_types': ['card', 'sepa_debit'],
                                             'metadata': {}}, intent('pi_stub_zero', 0))])
# t6x1: every JSON value of the association's methods (float, nested list, 1e+22, big int).
serialized = [payment_item('mp_encoded_methods')]
params = multi_params(['mp_encoded_methods'], methods=ENCODED_METHODS_READ)
add('multiple-pay-encodes-python-json-methods', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_encoded_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': [{'payment_id': P('mp_encoded_methods')}]},
    stripe=[create(params, intent('pi_stub_multi_encoded', params['amount']))],
    effect=effect(payments={'mp_encoded_methods': {'intent': 'pi_stub_multi_encoded'}},
                  audit=[set_intent('mp_encoded_methods', 'pi_stub_multi_encoded', 'anonymous')]))
# payment_id__in drops a null; the other ids are strings.
serialized = [payment_item('mp_null_a'), payment_item('mp_null_b')]
params = multi_params(['mp_null_a', 'mp_null_b'])
add('multiple-pay-ids-with-null', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_null_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': [P('mp_null_a'), None, P('mp_null_b')]},
    stripe=[create(params, intent('pi_stub_multi_null', params['amount']))],
    effect=effect(payments={'mp_null_a': {'intent': 'pi_stub_multi_null'}, 'mp_null_b': {'intent': 'pi_stub_multi_null'}},
                  audit=[set_intent('mp_null_a', 'pi_stub_multi_null', 'anonymous'), set_intent('mp_null_b', 'pi_stub_multi_null', 'anonymous')]))
# An integer id is uuid.UUID(int=...): it matches no row and the valid id is used.
serialized = [payment_item('mp_int_valid')]
params = multi_params(['mp_int_valid'])
add('multiple-pay-object-integer-and-valid-ids', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_multi_int_secret_stub', 'info': {'checkout_info': None}}, 'payments': serialized},
    body={'payments': [{'payment_id': 5}, {'payment_id': P('mp_int_valid')}]},
    stripe=[create(params, intent('pi_stub_multi_int', params['amount']))],
    effect=effect(payments={'mp_int_valid': {'intent': 'pi_stub_multi_int'}},
                  audit=[set_intent('mp_int_valid', 'pi_stub_multi_int', 'anonymous')]))
# UUIDField.to_python rejects a float, a dict where an id is expected and a negative integer: the 500 page before any request.
add('multiple-pay-object-float-id-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [{'payment_id': 1.5}]})
add('multiple-pay-string-then-float-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [P('mp_reject'), 1.5]})
add('multiple-pay-string-then-object-server-error', 'anonymous', MULTI, 500, SERVER,
    body={'payments': [P('mp_reject'), {'payment_id': P('mp_reject')}]})
add('multiple-pay-object-negative-id-server-error', 'anonymous', MULTI, 500, SERVER, body={'payments': [{'payment_id': -1}]})

# --- stripe/webhook --------------------------------------------------------------


def event(kind, obj=None):
    return json.dumps({'id': 'evt_stub', 'object': 'event', 'type': kind, 'data': {'object': obj or {'id': 'obj_stub'}}})


VALID = 't={t},v1={v1}'
JSON = 'application/json'
charge = lambda intent_id: event('charge.succeeded', {'id': 'ch_stub', 'object': 'charge', 'payment_intent': intent_id})
add('webhook-options', 'anonymous', WEBHOOK, 200, options('Stripe Webhook'), method='OPTIONS')
add('webhook-wrong-method', 'anonymous', WEBHOOK, 405, method('GET'), method='GET')
add('webhook-without-signature', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON)
add('webhook-wrong-secret', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature='t={t},v1={other}')
add('webhook-stale-timestamp', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature={'template': VALID, 'age': 400})
add('webhook-no-v1-scheme', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature='t={t},v0={v1}')
add('webhook-bad-timestamp', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature='t=soon,v1={v1}')
add('webhook-non-ascii-signature-server-error', 'anonymous', WEBHOOK, 500, SERVER, raw_body=event('checkout.session.completed'),
    content_type=JSON, signature='t={t},v1=è')
add('webhook-invalid-json', 'anonymous', WEBHOOK, 400, EMPTY, raw_body='{"type": ', content_type=JSON, signature=VALID)
# raw_latin1: the body is sent as the Latin-1 bytes of raw_body (a lone 0xff byte, not UTF-8).
add('webhook-not-utf8', 'anonymous', WEBHOOK, 400, EMPTY, raw_body='{"type": "\xff"}', content_type=JSON, signature=VALID)
cases[-1]['raw_latin1'] = True
add('webhook-checkout-completed', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature=VALID)
add('webhook-second-signature-matches', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=event('checkout.session.completed'), content_type=JSON,
    signature='t={t},v1={other},v1={v1}')
add('webhook-platform-billing-ignored', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=event('invoice.payment_succeeded'), content_type=JSON,
    signature=VALID)
add('webhook-unhandled-type', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=event('payout.paid'), content_type=JSON, signature=VALID)
add('webhook-missing-type-server-error', 'anonymous', WEBHOOK, 500, SERVER, raw_body='{"id": "evt_stub"}', content_type=JSON, signature=VALID)
add('webhook-charge-without-intent', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=event('charge.succeeded', {'id': 'ch_stub'}),
    content_type=JSON, signature=VALID)
add('webhook-charge-unknown-intent', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge('pi_stub_nobody'), content_type=JSON, signature=VALID)
add('webhook-charge-paid-payment', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_wh_paid'), content_type=JSON, signature=VALID)
add('webhook-charge-other-group', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge('pi_stub_wh_group'), content_type=JSON, signature=VALID,
    headers=OTHER_GROUP)
add('webhook-charge-marks-supplier-invoice', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_wh'), content_type=JSON,
    signature=VALID, effect=effect(payments={'wh_invoiced': marked_state('wh_invoiced')}, invoices={'wh_invoiced': {'paid': True, 'payment_date': '$now'}},
                                   audit=[supplier_paid('wh_invoiced', 'anonymous'), stripe_marked('wh_invoiced', 'anonymous')]))
add('webhook-charge-group', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_wh_group'), content_type=JSON, signature=VALID,
    headers=GROUP, effect=effect(payments={'wh_group': marked_state('wh_group')}, invoices={'wh_group': {'paid': True, 'payment_date': '$now'}},
                                 audit=[supplier_paid('wh_group', 'anonymous'), stripe_marked('wh_group', 'anonymous')]))
# t6x1: CharField.to_python is str() for a float, a list or a StripeObject.
# The two stored values below match only outside the X-Group-Id scope the other cases use.
add('webhook-charge-float-intent-unknown', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge(1.5), content_type=JSON, signature=VALID,
    headers=GROUP)
add('webhook-charge-list-intent-unknown', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge(['pi_x', None, 2.0]), content_type=JSON,
    signature=VALID)
add('webhook-charge-object-intent-unknown', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge({'b': 1, 'a': 'è'}), content_type=JSON,
    signature=VALID, headers=GROUP)
# str(list) of a list holding a StripeObject embeds an address, so no stored intent matches.
add('webhook-charge-list-object-intent-unknown', 'anonymous', WEBHOOK, 400, EMPTY, raw_body=charge(['pi_x', {'a': 1}]), content_type=JSON,
    signature=VALID)
add('webhook-charge-float-intent-marks-payment', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge(1.5), content_type=JSON, signature=VALID,
    effect=effect(payments={'wh_float_intent': marked_state('wh_float_intent')},
                  invoices={'wh_float_intent': {'paid': True, 'payment_date': '$now'}},
                  audit=[supplier_paid('wh_float_intent', 'anonymous'), stripe_marked('wh_float_intent', 'anonymous')]))
add('webhook-charge-object-intent-marks-payment', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge({'b': 1, 'a': 'è'}), content_type=JSON,
    signature=VALID, effect=effect(payments={'wh_json_intent': marked_state('wh_json_intent')},
                                   invoices={'wh_json_intent': {'paid': True, 'payment_date': '$now'}},
                                   audit=[supplier_paid('wh_json_intent', 'anonymous'), stripe_marked('wh_json_intent', 'anonymous')]))

# --- mark_payment_as_paid: paid flow -------------------------------------------------
# An unpaid payment without an invoice, reached through the webhook (request
# None: DOCUMENT_BYPASS_TOKEN), the pay route (a stored intent that succeeded)
# and multiple-pay. Atomically: fees (float() of the meta payment category
# amounts; the membership fee only recomputed with categories), number =
# max(Max('number') of the association's live, group-aware, unarchived invoices
# created in the current balance year, starting_number_invoices) + 1, the
# description, Invoice.objects.create (raw X-Group-Id), payment.save() (paid,
# payment date now, invoice), the course subscription / installment paid flag
# (a carnet subscription's save changes nothing), print_document_invoice.delay
# (published inside the block) and send_notification(payment.user,
# association_id=payment.sport_association_id).
# Since main 6b20715d: before the fees, payment.type = 'stripe' and
# payment.save(update_fields=['paid', 'payment_date', 'type']) (audited with
# the changed update fields only); the later payment.save() records the invoice
# (and Payment.save()'s defaults); print_document_invoice.delay runs in
# transaction.on_commit, so a rolled-back block publishes nothing and the
# published task finds the invoice and the paid 'stripe' payment committed
# ('committed' is what a broker observer reads from the database when the
# message arrives).

PLAIN_DENOMINATION = PLAIN['denomination']
DEFAULT_CATEGORY_NAME = 'entrate e proventi da attività tipiche'  # the migration's default category
STRIPE_NUMBERS = {'year': 0, 'group': 0}


def escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;'))


def pyfloat(value):
    return repr(float(value))


def stripe_fees(row):
    """((membership str(), stored), (activity str(), stored)) of mark_payment_as_paid."""
    from decimal import Decimal, ROUND_HALF_EVEN
    drf = lambda value: str(Decimal(repr(float(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN))
    amount = row['amount']
    if row['subject'] != 1:
        return ('0.0', '0.00'), (amount, amount)
    activity = 0.0
    membership = None
    if row['meta_categories']:
        for item in row['meta_categories']:
            if item['amount']:
                activity = activity + float(item['amount'])
        if Decimal(amount) > 0:
            membership = float(amount) - activity
    membership_pair = (pyfloat(membership), drf(membership)) if membership is not None else (amount, amount)
    return membership_pair, (pyfloat(activity), drf(activity))


def column(value):
    """A float fee as the numeric(9, 2) column keeps it: nine significant digits, then rounded half away from zero."""
    from decimal import Context, Decimal, ROUND_HALF_UP
    return str(Context(prec=9).create_decimal_from_float(float(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def paid_flow(label, actor, *, description, notified, token, course=None, installment=None, broadcast=False, group=None, rolled_back=False,
              group_sent=None):
    """(effect, celery) of mark_payment_as_paid for an unpaid payment without an invoice."""
    row = STATE[label]
    association = row['association']
    if rolled_back:
        return {**nothing(), 'celery': [], 'invoice': None, 'notification': None, 'rolled_back': True}
    key = 'group' if group else 'year'
    STRIPE_NUMBERS[key] += 1
    number = STRIPE_NUMBERS[key]
    if group:
        STRIPE_NUMBERS['year'] = max(STRIPE_NUMBERS['year'], number)
    (membership_audit, membership), (activity_audit, activity) = stripe_fees(row)
    if row['subject'] == 1:
        # Float fees are rounded by the column (the membership fee is a float only with categories and a positive amount).
        activity = column(activity_audit)
        if membership_audit != row['amount']:
            membership = column(membership_audit)
    meta = row['meta_categories']
    create = {'invoice_id': ['None', '$invoice'], 'document_pdf': ['None', 'None'], 'sport_association': ['None', association],
              'creation_date': ['None', '$now'], 'membership_fee': ['None', membership_audit], 'activity_fee': ['None', activity_audit],
              'meta_payment_categories': ['None', json.dumps(meta, sort_keys=True)], 'description': ['None', description],
              'number': ['None', str(number)], 'archived': ['None', 'False'], 'cancelled': ['None', 'False'], 'selected_tutor': ['None', 'None'],
              'meta': ['None', 'null'], 'imported_from_associami': ['None', 'False'],
              'group': ['None', group_sent or (IDS[group] if group else 'None')], 'deleted': ['None', 'False']}
    # Payment.save()'s defaults (filled by the update_fields save, written by the full save): the default
    # category, and get_course_carnet_name()'s description (the category name) for a payment without one.
    defaults = {}
    if row['category'] is None:
        defaults['payment_category'] = ['None', '$default_category']
    if row['description'] is None and row['category'] is None:
        defaults['description'] = ['None', DEFAULT_CATEGORY_NAME]
    audit = [stripe_marked(label, actor),
             {'model': 'invoice', 'label': '$invoice', 'action': 0, 'changes': create, 'actor': actor, 'index': [association, 'direct']},
             audited('payment', label, {'invoice': ['None', '$invoice'], **defaults}, actor)]
    if course:
        audit.append({'model': 'coursesubscription', 'label': course, 'action': 1, 'changes': {'paid': ['False', 'True']}, 'actor': actor,
                      'index': [association, 'course.sport_association']})
    if installment:
        audit.append({'model': 'coursesubscriptioninstallment', 'label': installment, 'action': 1, 'changes': {'paid': ['False', 'True']},
                      'actor': actor, 'index': [association, 'course_subscription.course.sport_association']})
    if broadcast:
        index = [OWN, 'actor.sport_association'] if actor == 'owner' else None
        audit += [{'model': model, 'label': '$' + model, 'action': 0, 'changes': '$created', 'actor': actor, 'index': index}
                  for model in ('notificationsbroadcast', 'usernotificationsbroadcast')]
    msg = f"Pagamento di € {row['amount'].replace('.', ',')}" + (f"per {notified}" if notified else '') + '.'
    msg += f" Pagamento dell'associato {ASSOCIATE['first']} {ASSOCIATE['last']}."
    stored = {'sport_association_id': association, 'creation_date': '$now', 'membership_fee': membership, 'activity_fee': activity,
              'meta_payment_categories': meta, 'description': description, 'number': number, 'group_id': IDS[group] if group else None}
    changed = {'paid': True, 'payment_date': '$now', 'invoice': '$invoice', 'type': 'stripe'}
    if 'payment_category' in defaults:
        changed['category'] = '$default_category'
        row['category'] = '$default_category'
    if 'description' in defaults:
        changed['description'] = defaults['description'][1]
        row['description'] = defaults['description'][1]
    result = effect(payments={label: changed}, audit=audit)
    result.update(invoice=stored, course_subscriptions_paid=[course] if course else [], installments_paid=[installment] if installment else [],
                  celery=[{'task': 'print_document_invoice', 'args': ['$invoice', token], 'kwargs': [],
                           'committed': {'invoice': True, 'paid': True, 'type': 'stripe'}}],
                  notification={'user': 'owner', 'msg': msg, 'association': association})
    return result


def other_text(reason):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(PLAIN_DENOMINATION)}</b>."


def course_text(title):
    return (f"Ricevuta di pagamento di <b>{escape(ASSOCIATE['first'] + ' ' + ASSOCIATE['last'])}</b> per il corso <b>{escape(title)}</b> "
            f"dell'associazione <b>{escape(PLAIN_DENOMINATION)}</b>.")


SUBSCRIBED = (f"Ricevuta di pagamento per l'iscrizione di <b>{escape(ASSOCIATE['first'] + ' ' + ASSOCIATE['last'])}</b> all'associazione "
              f"<b>{escape(PLAIN_DENOMINATION)}</b>.")
add('webhook-charge-creates-invoice', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_other'), content_type=JSON, signature=VALID,
    effect=paid_flow('pf_wh_other', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token='$bypass', broadcast=True))
add('pay-succeeded-creates-invoice', 'owner', PAY('pf_pay_subscription'), 200, MARKED,
    stripe=[retrieve('pi_stub_pf_sub', intent('pi_stub_pf_sub', 3000, status='succeeded'))],
    effect=paid_flow('pf_pay_subscription', 'owner', description=SUBSCRIBED, notified="l'iscrizione all'associazione sportiva",
                     token='$authorization'))
add('webhook-charge-subscription-without-categories', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_sub_plain'),
    content_type=JSON, signature=VALID,
    effect=paid_flow('pf_wh_subscription_plain', 'anonymous', description=SUBSCRIBED, notified="l'iscrizione all'associazione sportiva",
                     token='$bypass'))
add('webhook-charge-course-subscription', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_course'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_course', 'anonymous', description=course_text(COURSE['title']),
                                      notified=f'il corso "{COURSE["title"]}"', token='$bypass', course='cs_pf'))
add('webhook-charge-installment', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_installment'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_installment', 'anonymous', description=course_text(COURSE['title']),
                                      notified=f"rata del corso {COURSE['title']}", token='$bypass', installment='i_pf'))
add('webhook-charge-carnet', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_carnet'), content_type=JSON, signature=VALID,
    effect=paid_flow('pf_wh_carnet', 'anonymous', description=course_text(CARNET['title']), notified=f"carnet {CARNET['title']}", token='$bypass'))
add('webhook-charge-course-without-link', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_none'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_course_none', 'anonymous', description='Quota', notified='carnet', token='$bypass'))
# send_notification(None, ...) raises inside the block: it rolls back, its on_commit task is discarded, the view swallows it.
add('webhook-charge-without-user-rolls-back', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_nouser'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_no_user', 'anonymous', description=None, notified=None, token='$bypass', rolled_back=True))
add('pay-succeeded-anonymous-creates-invoice', 'anonymous', PAY('pf_pay_anonymous'), 200, MARKED,
    stripe=[retrieve('pi_stub_pf_anon', intent('pi_stub_pf_anon', 1600, status='succeeded'))],
    effect=paid_flow('pf_pay_anonymous', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token=None))
sent = [{'payment_id': P('pf_mp'), 'sport_association': PLAIN['association']}]
serialized = [payment_item('pf_mp')]
add('multiple-pay-succeeded-creates-invoice', 'anonymous', MULTI, 200,
    {'data': {'client_secret': 'pi_stub_pf_zero_secret_stub', 'info': {'checkout_info': PLAIN['checkout']}}, 'payments': serialized},
    body={'payments': sent},
    stripe=[retrieve('pi_stub_pf_mp', intent('pi_stub_pf_mp', 1700, status='succeeded')),
            create({'amount': 0, 'currency': 'eur', 'description': '', 'payment_method_types': ['card', 'sepa_debit'], 'metadata': {}},
                   intent('pi_stub_pf_zero', 0), telemetry='req_stub_1')],
    effect=paid_flow('pf_mp', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token=None))
add('webhook-charge-group-invoice', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_group'), content_type=JSON, signature=VALID,
    headers=GROUP, effect=paid_flow('pf_wh_group', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token='$bypass',
                                    group='own_group'))
# The header is read as a uuid in any spelling the uuid module accepts; the new invoice's audit entry keeps the text as sent.
add('webhook-charge-group-upper-case', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_spelling'), content_type=JSON,
    signature=VALID, headers={'X-Group-Id': IDS['own_group'].upper()},
    effect=paid_flow('pf_wh_group_spelling', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token='$bypass',
                     group='own_group', group_sent=IDS['own_group'].upper()))


def subscribed(name):
    return (f"Ricevuta di pagamento per l'iscrizione {escape(name)}di <b>{escape(ASSOCIATE['first'] + ' ' + ASSOCIATE['last'])}</b> "
            f"all'associazione <b>{escape(PLAIN_DENOMINATION)}</b>.")


# 9.875 and 0.125 are exact floats: the column rounds them away from zero.
add('webhook-charge-fee-tie', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_tie'), content_type=JSON, signature=VALID,
    effect=paid_flow('pf_wh_tie', 'anonymous', description=SUBSCRIBED, notified="l'iscrizione all'associazione sportiva", token='$bypass'))
# meta.subscription_data.name is formatted with str(): floats and lists as Python writes them.
add('webhook-charge-meta-name-float', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_name_float'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_name_float', 'anonymous', description=subscribed('(1.5) '),
                                      notified="l'iscrizione all'associazione sportiva", token='$bypass'))
add('webhook-charge-meta-name-list', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_name_list'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_name_list', 'anonymous', description=subscribed("(['Agonisti', {'ok': True, 'anno': None}, 2]) "),
                                      notified="l'iscrizione all'associazione sportiva", token='$bypass'))
# The update_fields save fills Payment.save()'s defaults in memory first: the invoice description reads the
# default category and the full save records it with the invoice.
add('webhook-charge-defaults-before-description', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_defaults'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_defaults', 'anonymous', description=other_text(DEFAULT_CATEGORY_NAME), notified=DEFAULT_CATEGORY_NAME,
                                      token='$bypass'))
# A payment already typed 'stripe' only records paid and payment date in the first save.
add('webhook-charge-stripe-typed-payment', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_typed'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_typed', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token='$bypass'))
add('webhook-charge-cash-payment-becomes-stripe', 'anonymous', WEBHOOK, 200, EMPTY, raw_body=charge('pi_stub_pf_cash'), content_type=JSON,
    signature=VALID, effect=paid_flow('pf_wh_cash', 'anonymous', description=other_text(CATEGORY_NAME), notified='Quota', token='$bypass'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'stripe/info, on-boarding, complete-on-boarding, pay/<payment_id>, multiple-pay, webhook', 'ids': IDS,
    'stripe': {'secret_key': SECRET_KEY, 'webhook_secret': WEBHOOK_SECRET, 'public_key': PUBLIC_KEY, 'api_version': '2025-02-24.acacia',
               'user_agent': 'Stripe/v1 PythonBindings/11.6.0'},
    'category': CATEGORY, 'category_name': CATEGORY_NAME, 'associate': ASSOCIATE, 'subscription': SUBSCRIPTION, 'supplier': SUPPLIER,
    'associations': EXTRA_ASSOCIATIONS, 'payments': PAYMENTS, 'invoices': INVOICES, 'supplier_invoices': SUPPLIER_INVOICES,
    'course': COURSE, 'carnet': CARNET, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'installments': INSTALLMENTS, 'holdings': HOLDINGS,
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
