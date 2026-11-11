"""Author the payment invoice generation and invoiced approve dispatch HTTP sequence (invoice-generate.md).

payment/<uid>/generate-invoice (POST, registry
``bookeeping.documents.invoices.create``): is_valid_uuid, then
scoped_queryset(SportAssociation).get(user=...) (athletes and owners without an
association: 500), then ``request.data.get('only_print')`` (a JSON list body is
a 500).

- only_print truthy: the first live, group-aware payment by id through
  scoped_queryset (none, or none without an invoice: 500), then
  print_document_invoice.apply_async(args=[str(invoice_id), Authorization])
  and PaymentSerializer with invoice_generating. The payment resolves by id.
- otherwise, inside transaction.atomic(): the payment (select_for_update; none:
  500; another association: 403 'User not allowed.'), subscription_id (None:
  400 {'error': "Seleziona l'associato."}; is_valid_uuid: 400 list), the
  first group-aware subscription by id (none: 500); payment.paid = True and
  payment.associate = subscription.associate; the fees (subject 1: amount
  minus the float sum of the truthy meta_payment_categories amounts, kept as
  floats in memory; otherwise membership 0.0 and activity the amount);
  InvoiceService.get_next_invoice_number (pg_advisory_xact_lock on the
  association, the balance year of now from the owner's start day/month, the
  live group-aware invoices of the association, not archived, joined to a
  payment created in the balance year, or created before it when the payment
  was; the first by -number, NULL first; max with starting_number_invoices,
  plus one); generate_invoice_description; Invoice.objects.create (raw
  X-Group-Id); with payment_date_equal_invoice_date the invoice's
  creation_date becomes the payment date and it is saved again (a NULL date
  is a 500, rolled back); payment.save(); PaymentSerializer. After the
  commit: print_document_invoice.apply_async(args=[str(invoice_id),
  Authorization]).

auditlog: the invoice create renders the in-memory float fees with str();
the second invoice save diffs them against the stored decimals.

payment/<uid>/approve (POST) of a paid invoiced payment with a truthy
generate_invoice (default True): the message and the payment with
invoice_generating, then on_commit(robust) print_document_invoice with
[str(invoice_id), Authorization, send_receipt_email (raw, default False)].
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('invoice_generate_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
GROUP_ID = IDS['own_group']
ids = iter(f'a1000000-0000-4000-8000-{995001 + n:012d}' for n in range(300))
SERVER = '<stable-server-error-page>'
DENOMINATION = 'Message reads own'
DIRECT = 'direct'
# Dates relative to the run: '$year:' is the current year, '$lastyear:' the previous one.
IN_YEAR = '$year:-10-01T08:00:00Z'
LAST_YEAR = '$lastyear:-06-01T08:00:00Z'

FLAG = {'id': next(ids), 'association': next(ids), 'denomination': 'Ricevute flag', 'first': 'Flag', 'last': 'Owner',
        'email': 'flag-owner@example.test', 'username': 'flag-owner', 'starting_number': 10}
CATEGORY = {'id': next(ids), 'name': 'Quote generate fixture'}
DOCUMENT = {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta.pdf', 'filepath': 'docs/ricevuta-gen.pdf', 'size': 2048,
            'created': '2026-02-03T08:00:00Z'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore <gen>', 'vat_number': '01234567890', 'email': 'fornitore-gen@example.test'}

ASSOCIATES = {
    'anna': {'id': next(ids), 'first': 'Anna', 'last': 'Bianchi', 'association': OWN, 'group': None},
    'bruno': {'id': next(ids), 'first': 'Bruno', 'last': "O'Neil & C", 'association': OWN, 'group': None},
    'carla': {'id': next(ids), 'first': 'Carla', 'last': 'Gialli', 'association': OWN, 'group': 'own_group'},
    'hans': {'id': next(ids), 'first': 'Hans', 'last': 'Muller', 'association': FOREIGN, 'group': None},
    'flavia': {'id': next(ids), 'first': 'Flavia', 'last': 'Rosa', 'association': FLAG['association'], 'group': None},
}

COLUMNS = ('type', 'subject', 'amount', 'creation_date', 'payment_date', 'paid', 'expense', 'description', 'payment_category_id',
           'meta_payment_categories', 'invoice_id', 'user_id', 'associate_id', 'supplier_id', 'instructor_id', 'sport_association_id',
           'payment_intent_id', 'archived', 'invoice_fiscal_code', 'meta', 'notes', 'custom_accounts_id', 'signature_id', 'deleted',
           'group_id', 'course')


def row(**values):
    base = {'type': 'default', 'subject': 0, 'amount': '10.00', 'creation_date': IN_YEAR, 'payment_date': None, 'paid': False,
            'expense': False, 'description': 'Quota', 'payment_category_id': CATEGORY['id'], 'meta_payment_categories': None,
            'invoice_id': None, 'user_id': OWNER, 'associate_id': None, 'supplier_id': None, 'instructor_id': None,
            'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
            'notes': None, 'custom_accounts_id': None, 'signature_id': None, 'deleted': False, 'group_id': None, 'course': None}
    unknown = set(values) - set(base)
    assert not unknown, unknown
    return {**base, **values}


def invoice(number, *, association=OWN, created='$year:-01-05T09:00:00Z', archived=False, deleted=False, group=None, document=False,
            membership='0.00', activity='10.00', description='Ricevuta seed'):
    return {'id': next(ids), 'number': number, 'association': association, 'created': created, 'archived': archived, 'deleted': deleted,
            'group': group, 'document': document, 'membership_fee': membership, 'activity_fee': activity, 'description': description}


INVOICES = {
    # Numbering of the own association: joined to payments created in the balance year.
    'inv_year': invoice(5),
    'inv_group': invoice(7, group=GROUP_ID),
    'inv_archived': invoice(99, archived=True),
    'inv_deleted': invoice(98, deleted=True),
    'inv_print': invoice(4, document=True, activity='25.00', description='Ricevuta è'),
    # Created before the balance year (not joined to any payment).
    'inv_old': invoice(20, created='$lastyear:-03-01T09:00:00Z'),
    'inv_foreign': invoice(70, association=FOREIGN),
    'inv_flag_null': invoice(None, association=FLAG['association']),
    'inv_flag_low': invoice(12, association=FLAG['association']),
}

PAYMENTS = {
    # Seeded invoice holders.
    'seed_year': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_year']['id'])},
    'seed_group': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_group']['id'], group_id=GROUP_ID)},
    'seed_archived': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_archived']['id'])},
    'seed_deleted': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_deleted']['id'])},
    'seed_flag_null': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_flag_null']['id'], sport_association_id=FLAG['association'],
                                              user_id=FLAG['id'], payment_category_id=None, creation_date='$year:-10-02T08:00:00Z')},
    'seed_flag_low': {'id': next(ids), **row(paid=True, invoice_id=INVOICES['inv_flag_low']['id'], sport_association_id=FLAG['association'],
                                             user_id=FLAG['id'], payment_category_id=None, creation_date='$year:-10-03T08:00:00Z')},
    # generate-invoice targets.
    'p_other': {'id': next(ids), **row(amount='25.00', description='Quota <palestra>')},
    'p_sub': {'id': next(ids), **row(subject=1, amount='30.00', meta={'subscription_data': {'name': 'Quota <A>', 'id': 'q-a'}},
                                     meta_payment_categories=[{'payment_category_id': CATEGORY['id'], 'amount': 10.5}, {'amount': 0},
                                                              {'amount': '2'}], description='Iscrizione')},
    'p_sub_float': {'id': next(ids), **row(subject=1, amount='0.30', meta_payment_categories=[{'amount': 0.1}], description='Iscrizione')},
    'p_course': {'id': next(ids), **row(subject=2, amount='40.00', description='Corso nuoto')},
    'p_install': {'id': next(ids), **row(subject=2, amount='20.00', description='Rata yoga')},
    'p_carnet': {'id': next(ids), **row(subject=2, amount='60.00', description='Carnet')},
    'p_reason': {'id': next(ids), **row(subject=2, amount='5.00', description=None, payment_category_id=None)},
    'p_supplier': {'id': next(ids), **row(subject=2, amount='12.00', description='Rimborso "spese"', supplier_id=SUPPLIER['id'])},
    'p_noparty': {'id': next(ids), **row(subject=2, amount='3.00', description='Varie', user_id=None)},
    'p_old': {'id': next(ids), **row(amount='8.00', creation_date=LAST_YEAR)},
    'p_group': {'id': next(ids), **row(amount='9.00', group_id=GROUP_ID)},
    # Rare inputs: another spelling of the group header, fees that round on a tie, meta names that are not text.
    'p_group_spelling': {'id': next(ids), **row(amount='9.00', group_id=GROUP_ID)},
    'p_tie': {'id': next(ids), **row(subject=1, amount='10.00', meta_payment_categories=[{'amount': '0.125'}], description='Iscrizione')},
    # Sums that are not finite: Invoice.objects.create fails inside the transaction, nothing is kept.
    'p_sum_inf': {'id': next(ids), **row(subject=1, amount='10.00', meta_payment_categories=[{'amount': 'inf'}], description='Iscrizione')},
    'p_sum_nan': {'id': next(ids), **row(subject=1, amount='10.00', meta_payment_categories=[{'amount': 'nan'}], description='Iscrizione')},
    'p_sum_huge': {'id': next(ids), **row(subject=1, amount='10.00', meta_payment_categories=[{'amount': '1e999'}], description='Iscrizione')},
    'p_name_float': {'id': next(ids), **row(subject=1, amount='26.00', meta={'subscription_data': {'name': 1.5}}, description='Iscrizione')},
    'p_name_list': {'id': next(ids), **row(subject=1, amount='27.00', description='Iscrizione',
                                           meta={'subscription_data': {'name': ['Agonisti', {'anno': None, 'ok': True}, 2]}})},
    'p_name_dict': {'id': next(ids), **row(subject=1, amount='28.00', description='Iscrizione',
                                           meta={'subscription_data': {'name': {'it': "l'anno \"2026\""}}})},
    'p_collab': {'id': next(ids), **row(amount='11.00')},
    'p_admin': {'id': next(ids), **row(amount='13.00', associate_id=ASSOCIATES['bruno']['id'])},
    'p_form': {'id': next(ids), **row(amount='14.00')},
    'p_cross': {'id': next(ids), **row(amount='16.00')},
    'p_transfer': {'id': next(ids), **row(subject=3, amount='17.00')},
    'p_foreign': {'id': next(ids), **row(sport_association_id=FOREIGN, user_id=IDS['foreign_owner'], payment_category_id=None)},
    'p_deleted': {'id': next(ids), **row(deleted=True)},
    'p_invoiced': {'id': next(ids), **row(paid=True, payment_date='2026-04-01T00:00:00Z', amount='25.00', invoice_id=INVOICES['inv_print']['id'],
                                          associate_id=ASSOCIATES['anna']['id'])},
    'p_foreign_invoiced': {'id': next(ids), **row(subject=2, paid=True, sport_association_id=FOREIGN, user_id=None, payment_category_id=None,
                                                  invoice_id=INVOICES['inv_foreign']['id'])},
    'f_dated': {'id': next(ids), **row(amount='15.00', payment_date='2026-03-01T10:00:00Z', sport_association_id=FLAG['association'],
                                       user_id=FLAG['id'], payment_category_id=None)},
    'f_undated': {'id': next(ids), **row(amount='15.00', sport_association_id=FLAG['association'], user_id=FLAG['id'], payment_category_id=None)},
    'f_tie': {'id': next(ids), **row(subject=1, amount='10.00', payment_date='2026-03-02T10:00:00Z', sport_association_id=FLAG['association'],
                                     user_id=FLAG['id'], payment_category_id=None, meta_payment_categories=[{'amount': '0.125'}])},
}


def subscription(associate, *, association=OWN, user=OWNER, payment=None, start=None, end=None, group=None,
                 created='$year:-01-10T08:00:00Z'):
    return {'id': next(ids), 'associate': ASSOCIATES[associate]['id'] if associate else None, 'association': association, 'user': user,
            'payment': PAYMENTS[payment]['id'] if payment else None, 'start': start, 'end': end, 'group': group, 'created': created}


SUBSCRIPTIONS = {
    'sub_anna': subscription('anna', payment='p_sub', start='2025-09-01', end='2026-08-31'),
    'sub_bruno': subscription('bruno', start='2026-01-01', end='2026-12-31'),
    'sub_none': subscription(None),
    'sub_carla': subscription('carla', group=GROUP_ID),
    'sub_hans': subscription('hans', association=FOREIGN, user=IDS['foreign_owner']),
    'sub_flavia': subscription('flavia', association=FLAG['association'], user=FLAG['id']),
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

STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'invoice-generate-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
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
GENERATE = lambda label: f'/payment/{PAYMENTS[label]["id"]}/generate-invoice'
APPROVE = lambda label: f'/payment/{PAYMENTS[label]["id"]}/approve'
SUB = lambda label: SUBSCRIPTIONS[label]['id']


def nothing(actor='owner'):
    return {'actor': actor, 'changed': {}, 'invoice': None, 'audit': [], 'celery': []}


# --- rendering ---------------------------------------------------------------------

USERS = {OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
                 'username': 'message-reads-700001', 'role': 1},
         FLAG['id']: {'first_name': FLAG['first'], 'last_name': FLAG['last'], 'email': FLAG['email'], 'phone': None,
                      'username': FLAG['username'], 'role': 1}}
ASSOCIATE_LABELS = {person['id']: label for label, person in ASSOCIATES.items()}
INVOICE_LABELS = {inv['id']: label for label, inv in INVOICES.items()}
OWNERS = {OWN: OWNER, FOREIGN: IDS['foreign_owner'], FLAG['association']: FLAG['id']}


def associate_item(label):
    person = ASSOCIATES[label]
    return {'associate_id': person['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
            'born_date': '1990-05-10', 'email': None, 'sex': 'M', 'first_name': person['first'], 'last_name': person['last'],
            'is_minor': False, 'sport_association': person['association'], 'tax_code': None, 'user': OWNERS[person['association']],
            'phone': None, 'family': None, 'family_role': None}


def supplier_item():
    return {'supplier_id': SUPPLIER['id'], 'name': SUPPLIER['name'], 'address': None, 'tax_code': None, 'sport_association': OWN,
            'vat_number': SUPPLIER['vat_number'], 'type': 'supplier', 'email': SUPPLIER['email'], 'phone_number': None, 'city': None,
            'cap': None, 'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}


def seeded_invoice_item(label):
    inv = INVOICES[label]
    return {'invoice_id': inv['id'], 'document_token': DOCUMENT['token'] if inv['document'] else None, 'creation_date': inv['created'],
            'membership_fee': inv['membership_fee'], 'activity_fee': inv['activity_fee'], 'meta_payment_categories': None,
            'description': inv['description'], 'number': inv['number'], 'archived': inv['archived'], 'cancelled': False, 'meta': None,
            'imported_from_associami': False, 'deleted': inv['deleted'], 'document_pdf': DOCUMENT['id'] if inv['document'] else None,
            'sport_association': inv['association'], 'selected_tutor': None, 'group': inv['group'], 'attached_documents': []}


def subscription_id(values, group=None):
    """PaymentSerializer.get_subscription_id (Subscription.objects and the installment manager follow X-Group-Id)."""
    subject, payment = values['subject'], values['id']
    visible = lambda sub: group is None or sub['group'] == group
    if subject == 1:
        found = [s['id'] for s in SUBSCRIPTIONS.values() if s['payment'] == payment and visible(s)]
        return found[0] if found else None
    if subject == 2:
        for inst in INSTALLMENTS.values():
            if PAYMENTS[inst['payment']]['id'] == payment and group is None:
                return SUB(COURSE_SUBSCRIPTIONS[inst['course_subscription']]['subscription'])
        for cs in COURSE_SUBSCRIPTIONS.values():
            if cs['payment'] and PAYMENTS[cs['payment']]['id'] == payment:
                return SUB(cs['subscription'])
        for carsub in CARNET_SUBSCRIPTIONS.values():
            if PAYMENTS[carsub['payment']]['id'] == payment:
                return SUB(carsub['subscription'])
        return None
    if subject == 0:
        assert values['associate_id'] is not None
        found = sorted((s['created'], s['id']) for s in SUBSCRIPTIONS.values() if s['associate'] == values['associate_id'] and visible(s))
        return found[0][1] if found else None
    return None


def payment_item(label, *, invoice=None, group=None):
    values = STATE[label]
    associate = values['associate_id']
    category = values['payment_category_id']
    names = {CATEGORY['id']: CATEGORY['name'], None: None, '$default_category': '$default_category_name'}
    if invoice is None and values['invoice_id']:
        invoice = seeded_invoice_item(INVOICE_LABELS[values['invoice_id']])
    item = {'payment_id': values['id'], 'user': USERS.get(values['user_id']),
            'associate': associate_item(ASSOCIATE_LABELS[associate]) if associate else None,
            'subscription_id': subscription_id(values, group), 'instructor_id': None, 'instructor': None,
            'supplier': supplier_item() if values['supplier_id'] else None, 'supplier_id': values['supplier_id'], 'custom_account_type': None,
            'custom_account_name': None, 'invoice': invoice, 'attachments': [], 'payment_category_name': names[category],
            'type': values['type'], 'subject': values['subject'], 'amount': values['amount'], 'creation_date': values['creation_date'],
            'payment_date': values['payment_date'], 'paid': values['paid'], 'expense': values['expense'], 'description': values['description'],
            'meta_payment_categories': values['meta_payment_categories'], 'payment_intent_id': None, 'archived': values['archived'],
            'invoice_fiscal_code': None, 'meta': values['meta'], 'notes': None, 'deleted': values['deleted'], 'course': None,
            'payment_category': category, 'sport_association': values['sport_association_id'], 'custom_accounts': None, 'signature': None,
            'group': values['group_id']}
    return item


def generating(item, msg='generated invoice.'):
    return {'data': {'msg': msg, 'payment': {**item, 'invoice_generating': True}}}


# --- generation effects --------------------------------------------------------------

def escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            .replace("'", '&#x27;'))


def full_name(label):
    person = ASSOCIATES[label]
    return f"{person['first']} {person['last']}".strip()


def json_text(value):
    return json.dumps(value, sort_keys=True)


def pyfloat(value):
    return repr(float(value))


def drf(value):
    from decimal import Decimal, ROUND_HALF_EVEN
    return str(Decimal(repr(float(value))).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN))


def column(value):
    """A float fee as the numeric(9, 2) column keeps it: nine significant digits, then rounded half away from zero."""
    from decimal import Context, Decimal, ROUND_HALF_UP
    return str(Context(prec=9).create_decimal_from_float(float(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def fees(values):
    """The view's in-memory fees: (membership, activity) as (str(), response text, column text)."""
    amount = values['amount']
    if values['subject'] == 1:
        activity = 0.0
        for item in values['meta_payment_categories'] or []:
            if item['amount']:
                activity = activity + float(str(item['amount']))
        membership = float(amount) - activity if float(amount) > 0 else None
        return (((pyfloat(membership), drf(membership), column(membership)) if membership is not None else (amount, amount, amount)),
                (pyfloat(activity), drf(activity), column(activity)))
    return ('0.0', '0.00', '0.00'), (amount, amount, amount)


# The flag association's first invoice by -number has a NULL number (NULLs sort first): max(0, 10) + 1.
NUMBERS = {'own': 7, 'own_old': 20, 'own_group': 7, 'flag': 10}


def generated(label, sub_label, description, *, actor='owner', number_key='own', group=None, association=OWN, flag=False, msg=None,
              group_sent=None):
    """Mutates STATE and returns (response, effect) of a successful generation."""
    values = STATE[label]
    sub = SUBSCRIPTIONS[sub_label]
    number = NUMBERS[number_key] + 1
    if number_key in ('own', 'own_group'):
        # Every generated invoice is joined to an in-year payment of the own association.
        NUMBERS['own'] = max(NUMBERS['own'], number)
        if group:
            NUMBERS['own_group'] = number
    else:
        NUMBERS[number_key] = number
    (membership_audit, membership, membership_column), (activity_audit, activity, activity_column) = fees(values)
    group_memory = group_sent or group
    created = values['payment_date'] if flag else '$now'
    invoice = {'invoice_id': '$invoice', 'document_token': None, 'creation_date': created, 'membership_fee': membership,
               'activity_fee': activity, 'meta_payment_categories': values['meta_payment_categories'], 'description': description,
               'number': number, 'archived': False, 'cancelled': False, 'meta': None, 'imported_from_associami': False, 'deleted': False,
               'document_pdf': None, 'sport_association': association, 'selected_tutor': None, 'group': group_memory, 'attached_documents': []}
    create = {'invoice_id': ['None', '$invoice'], 'document_pdf': ['None', 'None'], 'sport_association': ['None', association],
              'creation_date': ['None', '$now'], 'membership_fee': ['None', membership_audit], 'activity_fee': ['None', activity_audit],
              'meta_payment_categories': ['None', json_text(values['meta_payment_categories'])], 'description': ['None', description],
              'number': ['None', str(number)], 'archived': ['None', 'False'], 'cancelled': ['None', 'False'], 'selected_tutor': ['None', 'None'],
              'meta': ['None', 'null'], 'imported_from_associami': ['None', 'False'], 'group': ['None', group_memory or 'None'],
              'deleted': ['None', 'False']}
    audit = [{'model': 'invoice', 'key': '$invoice', 'action': 0, 'changes': create, 'index': [association, DIRECT]}]
    stored = {'membership_fee': membership_column, 'activity_fee': activity_column, 'number': number, 'description': description, 'group_id': group,
              'creation_date': created, 'sport_association_id': association, 'meta_payment_categories': values['meta_payment_categories']}
    if flag:
        update = {'creation_date': ['$now', values['payment_date'].replace('T', ' ').replace('Z', '')]}
        if membership_column != membership_audit:
            update['membership_fee'] = [membership_column, membership_audit]
        if activity_column != activity_audit:
            update['activity_fee'] = [activity_column, activity_audit]
        if group and group_memory != group:
            # The re-save compares the stored uuid with the text the instance still holds.
            update['group'] = [group, group_memory]
        audit.append({'model': 'invoice', 'key': '$invoice', 'action': 1, 'changes': update, 'index': [association, DIRECT]})
    changes = {'paid': ['False', 'True']}
    if values['associate_id'] != sub['associate']:
        changes['associate'] = [values['associate_id'] or 'None', sub['associate'] or 'None']
    changes['invoice'] = ['None', '$invoice']
    changed = {'paid': True, 'associate_id': sub['associate'], 'invoice_id': '$invoice'}
    if values['payment_category_id'] is None:
        changes['payment_category'] = ['None', '$default_category']
        changed['payment_category_id'] = '$default_category'
    if values['description'] is None and values['subject'] == 2:
        changes['description'] = ['None', 'Quota Corso']
        changed['description'] = 'Quota Corso'
    audit.append({'model': 'payment', 'key': label, 'action': 1, 'changes': changes, 'index': [association, DIRECT]})
    values.update(changed)
    values['invoice_id'] = None
    response = generating(payment_item(label, invoice=invoice, group=group))
    values['invoice_id'] = '$invoice'
    effect = {'actor': actor, 'changed': {label: changed}, 'invoice': stored, 'audit': audit,
              'celery': [{'task': 'print_document_invoice', 'args': ['$invoice', '$authorization'], 'kwargs': [], 'args_list': True}]}
    return response, effect


def other(reason):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(DENOMINATION)}</b>."


def other_flag(reason):
    return f"Ricevuta di pagamento con causale <b>{escape(reason)}</b> per l'associazione <b>{escape(FLAG['denomination'])}</b>."


def course(name, title):
    return f"Ricevuta di pagamento di <b>{escape(name)}</b> per il corso <b>{escape(title)}</b> dell'associazione <b>{escape(DENOMINATION)}</b>."


def subscribed(sub_name, name):
    return f"Ricevuta di pagamento per l'iscrizione {escape(sub_name)}di <b>{escape(name)}</b> all'associazione <b>{escape(DENOMINATION)}</b>."


# --- generate-invoice: guards and errors ---------------------------------------------

PATH = GENERATE('p_other')
add('anonymous', 'anonymous', PATH, 401, ANONYMOUS)
add('restricted-missing-permission', 'restricted_collaborator', PATH, 403,
    {'detail': 'Missing permission: bookeeping.documents.invoices.create'})
add('admin-without-session', 'admin', PATH, 403, ADMIN)
add('owner-scoped-athlete-denied', 'owner', PATH, 403, SCOPED_DENIED, headers=SCOPED)
add('wrong-method', 'owner', PATH, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', PATH, 200, {'name': 'Payment Generate Invoice', 'description': '', 'renders': ['application/json'],
                                    'parses': PARSES}, method='OPTIONS')
add('malformed-uid', 'owner', '/payment/abc/generate-invoice', 400, ['badly formed hexadecimal UUID string'], effect=nothing())
add('athlete-server-error', 'athlete', PATH, 500, SERVER, body={'subscription_id': SUB('sub_anna')}, effect=nothing('athlete'))
add('owner-without-association-server-error', 'owner_without_association', PATH, 500, SERVER, body={'subscription_id': SUB('sub_anna')},
    effect=nothing('owner_without_association'))
add('list-body-server-error', 'owner', PATH, 500, SERVER, body=['subscription_id'], effect=nothing())
add('missing-payment-server-error', 'owner', f'/payment/{IDS["missing"]}/generate-invoice', 500, SERVER,
    body={'subscription_id': SUB('sub_anna')}, effect=nothing())
add('deleted-payment-server-error', 'owner', GENERATE('p_deleted'), 500, SERVER, body={'subscription_id': SUB('sub_anna')}, effect=nothing())
add('other-group-server-error', 'owner', PATH, 500, SERVER, body={'subscription_id': SUB('sub_anna')}, headers=OTHER_GROUP,
    effect=nothing())
add('foreign-payment-denied', 'owner', GENERATE('p_foreign'), 403, {'detail': 'User not allowed.'}, body={'subscription_id': SUB('sub_anna')},
    effect=nothing())
SELECT = {'error': "Seleziona l'associato."}
add('no-subscription', 'owner', PATH, 400, SELECT, effect=nothing())
add('null-subscription', 'owner', PATH, 400, SELECT, body={'subscription_id': None, 'only_print': False}, effect=nothing())
add('malformed-subscription', 'owner', PATH, 400, ['badly formed hexadecimal UUID string'], body={'subscription_id': 'abc'},
    effect=nothing())
add('blank-subscription', 'owner', PATH, 400, ['badly formed hexadecimal UUID string'], body={'subscription_id': ''}, effect=nothing())
add('number-subscription', 'owner', PATH, 400, ["'int' object has no attribute 'replace'"], body={'subscription_id': 5}, effect=nothing())
add('missing-subscription-server-error', 'owner', PATH, 500, SERVER, body={'subscription_id': IDS['missing']}, effect=nothing())
add('group-hidden-subscription-server-error', 'owner', GENERATE('p_group'), 500, SERVER, body={'subscription_id': SUB('sub_anna')},
    headers=GROUP, effect=nothing())
add('transfer-subject-server-error', 'owner', GENERATE('p_transfer'), 500, SERVER, body={'subscription_id': SUB('sub_anna')},
    effect=nothing())
add('flag-null-payment-date-server-error', 'flag_owner', GENERATE('f_undated'), 500, SERVER, body={'subscription_id': SUB('sub_flavia')},
    effect=nothing('flag_owner'))

# --- only_print ------------------------------------------------------------------------

PRINT = lambda label: [{'task': 'print_document_invoice', 'args': [PAYMENTS[label]['invoice_id'], '$authorization'], 'kwargs': [], 'args_list': True}]
add('only-print-without-invoice-server-error', 'owner', PATH, 500, SERVER, body={'only_print': True}, effect=nothing())
add('only-print-missing-payment-server-error', 'owner', f'/payment/{IDS["missing"]}/generate-invoice', 500, SERVER,
    body={'only_print': 1}, effect=nothing())
add('only-print', 'owner', GENERATE('p_invoiced'), 200, generating(payment_item('p_invoiced')), body={'only_print': True},
    effect={**nothing(), 'celery': PRINT('p_invoiced')})
add('only-print-form-string', 'full_collaborator', GENERATE('p_invoiced'), 200, generating(payment_item('p_invoiced')),
    raw_body='only_print=false', content_type=FORM, effect={**nothing('full_collaborator'), 'celery': PRINT('p_invoiced')})
add('only-print-foreign-payment', 'owner', GENERATE('p_foreign_invoiced'), 200, generating(payment_item('p_foreign_invoiced')),
    body={'only_print': 'yes'}, effect={**nothing(), 'celery': PRINT('p_foreign_invoiced')})

# --- generation ---------------------------------------------------------------------------

response, effect = generated('p_other', 'sub_bruno', other(CATEGORY['name']))
add('other-subject', 'owner', GENERATE('p_other'), 200, response, body={'subscription_id': SUB('sub_bruno'), 'only_print': False},
    effect=effect)
sub_name = '25/26 (Quota <A>)  e tesseramento, 2026 (FIDAL) '
response, effect = generated('p_sub', 'sub_anna', subscribed(sub_name, full_name('anna')))
add('subscription-subject-categories', 'owner', GENERATE('p_sub'), 200, response, body={'subscription_id': SUB('sub_anna').upper()},
    effect=effect)
response, effect = generated('p_sub_float', 'sub_bruno', subscribed('', full_name('bruno')))
add('subscription-subject-float-fees', 'owner', GENERATE('p_sub_float'), 200, response, body={'subscription_id': SUB('sub_bruno')},
    effect=effect)
response, effect = generated('p_course', 'sub_anna', course(full_name('anna'), COURSES['nuoto']['title']))
add('course-subscription', 'owner', GENERATE('p_course'), 200, response, body={'subscription_id': SUB('sub_anna')}, effect=effect)
response, effect = generated('p_install', 'sub_bruno', course(full_name('bruno'), COURSES['yoga']['title']))
add('course-installment', 'owner', GENERATE('p_install'), 200, response, body={'subscription_id': SUB('sub_bruno')}, effect=effect)
response, effect = generated('p_carnet', 'sub_anna', course(full_name('anna'), CARNETS['carnet']['title']))
add('carnet', 'owner', GENERATE('p_carnet'), 200, response, body={'subscription_id': SUB('sub_anna')}, effect=effect)
response, effect = generated('p_reason', 'sub_anna', 'Pagamento')
add('course-reason-fallback-save-defaults', 'owner', GENERATE('p_reason'), 200, response, body={'subscription_id': SUB('sub_anna')},
    effect=effect)
response, effect = generated('p_supplier', 'sub_none',
                             f"Ricevuta di pagamento di <b>{escape(SUPPLIER['name'])}</b> con causale <b>{escape('Rimborso \"spese\"')}</b> "
                             f"per l'associazione <b>{escape(DENOMINATION)}</b>.")
add('supplier-without-associate', 'owner', GENERATE('p_supplier'), 200, response, body={'subscription_id': SUB('sub_none')}, effect=effect)
response, effect = generated('p_noparty', 'sub_none', other('Varie'))
add('no-party', 'owner', GENERATE('p_noparty'), 200, response, body={'subscription_id': SUB('sub_none')}, effect=effect)
response, effect = generated('p_old', 'sub_bruno', other(CATEGORY['name']), number_key='own_old')
add('payment-before-balance-year', 'owner', GENERATE('p_old'), 200, response, body={'subscription_id': SUB('sub_bruno')}, effect=effect)
response, effect = generated('p_group', 'sub_carla', other(CATEGORY['name']), number_key='own_group', group=GROUP_ID)
add('group-numbering', 'owner', GENERATE('p_group'), 200, response, body={'subscription_id': SUB('sub_carla')}, headers=GROUP, effect=effect)
# The header is read as a uuid in any spelling the uuid module accepts; the new invoice renders it as sent.
response, effect = generated('p_group_spelling', 'sub_carla', other(CATEGORY['name']), number_key='own_group', group=GROUP_ID,
                             group_sent=GROUP_ID.upper())
add('group-header-upper-case', 'owner', GENERATE('p_group_spelling'), 200, response, body={'subscription_id': SUB('sub_carla')},
    headers={'X-Group-Id': GROUP_ID.upper()}, effect=effect)
# 9.875 and 0.125 are exact floats: the column rounds them away from zero, the response to the even cent.
response, effect = generated('p_tie', 'sub_bruno', subscribed('', full_name('bruno')))
assert (column(9.875), column(0.125), drf(0.125)) == ('9.88', '0.13', '0.12')
add('subscription-subject-fee-tie', 'owner', GENERATE('p_tie'), 200, response, body={'subscription_id': SUB('sub_bruno')}, effect=effect)
for key, label in (('p_sum_inf', 'infinite'), ('p_sum_nan', 'not-a-number'), ('p_sum_huge', 'overflowing')):
    add(f'meta-sum-{label}-server-error', 'owner', GENERATE(key), 500, SERVER, body={'subscription_id': SUB('sub_bruno')}, effect=nothing())
# meta.subscription_data.name is formatted with str(): floats, lists and dicts as Python writes them.
for key, label, text in (('p_name_float', 'float', '(1.5) '), ('p_name_list', 'list', "(['Agonisti', {'ok': True, 'anno': None}, 2]) "),
                         ('p_name_dict', 'dict', '''({'it': 'l\\'anno "2026"'}) ''')):
    response, effect = generated(key, 'sub_bruno', subscribed(text, full_name('bruno')))
    add(f'meta-name-{label}', 'owner', GENERATE(key), 200, response, body={'subscription_id': SUB('sub_bruno')}, effect=effect)
response, effect = generated('p_collab', 'sub_anna', other(CATEGORY['name']), actor='full_collaborator')
add('full-collaborator', 'full_collaborator', GENERATE('p_collab'), 200, response, body={'subscription_id': SUB('sub_anna')}, effect=effect)
response, effect = generated('p_admin', 'sub_bruno', other(CATEGORY['name']), actor='admin')
add('admin-impersonating-owner', 'admin', GENERATE('p_admin'), 200, response, body={'subscription_id': SUB('sub_bruno')},
    headers=ADMIN_OWNER, effect=effect)
response, effect = generated('p_form', 'sub_anna', other(CATEGORY['name']))
add('form-body', 'owner', GENERATE('p_form'), 200, response, raw_body=f'subscription_id={SUB("sub_anna")}&only_print=', content_type=FORM,
    effect=effect)
response, effect = generated('p_cross', 'sub_hans', other(CATEGORY['name']))
add('subscription-resolved-by-id', 'owner', GENERATE('p_cross'), 200, response, body={'subscription_id': SUB('sub_hans')}, effect=effect)
response, effect = generated('f_dated', 'sub_flavia', other_flag('Quota'), actor='flag_owner', number_key='flag',
                             association=FLAG['association'], flag=True)
add('flag-payment-date-invoice-date', 'flag_owner', GENERATE('f_dated'), 200, response, body={'subscription_id': SUB('sub_flavia')},
    effect=effect)

# The second save of an invoice dated by its payment compares the stored (column) fees with the float fees: the tie differs from both.
NUMBERS['flag'] = 10  # the NULL number sorts first: every flag invoice is numbered 11
response, effect = generated('f_tie', 'sub_flavia',
                             f"Ricevuta di pagamento per l'iscrizione di <b>{escape(full_name('flavia'))}</b> all'associazione "
                             f"<b>{escape(FLAG['denomination'])}</b>.", actor='flag_owner', number_key='flag', association=FLAG['association'],
                             flag=True)
add('flag-payment-date-fee-tie', 'flag_owner', GENERATE('f_tie'), 200, response, body={'subscription_id': SUB('sub_flavia')}, effect=effect)

# --- approve with a truthy generate_invoice ---------------------------------------------

APPROVED = lambda label: generating(payment_item(label), 'payment mark as payed.')


def dispatch(label, receipt=False, actor='owner'):
    return {**nothing(actor), 'celery': [{'task': 'print_document_invoice',
                                          'args': [PAYMENTS[label]['invoice_id'], '$authorization', receipt], 'kwargs': [], 'args_list': True}]}


add('approve-default-flag', 'owner', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'), body={}, effect=dispatch('p_invoiced'))
add('approve-string-flag-receipt', 'owner', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'),
    body={'generate_invoice': 'false', 'send_receipt_email': True, 'payment_date': '2020-01-01'}, effect=dispatch('p_invoiced', True))
add('approve-receipt-number', 'owner', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'),
    body={'generate_invoice': 1, 'send_receipt_email': 7}, effect=dispatch('p_invoiced', 7))
add('approve-receipt-null', 'owner', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'),
    body={'generate_invoice': True, 'send_receipt_email': None}, effect=dispatch('p_invoiced', None))
add('approve-form-body', 'full_collaborator', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'),
    raw_body='generate_invoice=0&send_receipt_email=yes', content_type=FORM, effect=dispatch('p_invoiced', 'yes', 'full_collaborator'))
# send_receipt_email reaches the task as sent: floats, integers of any size, lists and objects included.
for receipt_name, receipt_value in (('float', 1.5), ('float-exponent', 1e+22), ('float-small', 0.00001), ('float-negative-zero', -0.0),
                                    ('large-integer', 123456789012345678901234567890), ('list', ['s\u00ec', 2, [None, True, 0.5]]),
                                    ('object', {'b': 1, 'a': {'c': [1.25, 'x'], 'd': None}}), ('empty-list', [])):
    add(f'approve-receipt-{receipt_name}', 'owner', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'),
        body={'send_receipt_email': receipt_value}, effect=dispatch('p_invoiced', receipt_value))
add('approve-admin-impersonating-owner', 'admin', APPROVE('p_invoiced'), 200, APPROVED('p_invoiced'), body={'send_receipt_email': False},
    headers=ADMIN_OWNER, effect=dispatch('p_invoiced', False, 'admin'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Payment invoice generation (numbering, description, invoice create, PDF task '
                                                            'dispatch) and invoiced approve dispatch with payment, invoice, audit and '
                                                            'Celery effects',
                              'ids': IDS, 'flag_owner': FLAG, 'category': CATEGORY, 'document': DOCUMENT, 'supplier': SUPPLIER,
                              'associates': ASSOCIATES, 'invoices': INVOICES, 'payments': PAYMENTS, 'subscriptions': SUBSCRIPTIONS,
                              'memberships': MEMBERSHIPS, 'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS,
                              'installments': INSTALLMENTS, 'carnets': CARNETS, 'carnet_subscriptions': CARNET_SUBSCRIPTIONS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
