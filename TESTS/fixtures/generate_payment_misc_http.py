"""Author the payment simulation, partial quotes apply, sign and invoiced approve HTTP sequence (payment-misc.md).

All four are @api_view function views behind IsAuthenticated (registry:
simulation ``bookeeping.payments.read``, apply, sign and approve
``bookeeping.payments.update``).

payment/simulation/partial-quotes (GET): ``request.user.sport_association``
(athletes: PermissionDenied 'User is not a sport association'; owners without
a live association: 500), then calculate_simulation(date, association) inside
one try/except that returns what was collected so far: the ``date`` query
value through strptime('%Y-%m-%d') (None: today), the balance year of
datetime.now() from the owner's balance_sheet_start_day/month, the active plans
(auto_assign, advanced_options and previous_subscription_fee_plan truthy and
not '', int(from_day)/int(from_month) equal to the date's day and month), the
balance-year subscriptions of the association with a payment, payment meta
and meta (group aware, not deleted or archived). For every plan, the
subscriptions whose payment meta icontains the previous plan, skipping those
with a live group-aware subscription payment of the same user, associate,
amount and plan id in its meta. Each quote renders the user's full name, the
associate's names (no associate: AttributeError, the collection stops), the
fee, subject 1, the meta and, for users other than the association owner, the
e-mail text (not sent). The subscriptions are unordered (no ORDER BY).

payment/simulation/partial-quotes-apply (GET): the same selection through
association_quote_assign; each quote is Payment.objects.create (user,
associate, amount, subject 1, association, meta; the association's default
bank account; the raw X-Group-Id; Payment.save() defaults; audited create).
A quote for a user other than the owner renders
email/payment/fee_assigned.html (the request's language) and calls
send_email_html in-process: without a CommunicationConfiguration of the
association it publishes send_mail_async (subject, message, DEFAULT_FROM_EMAIL,
the user's e-mail, html_message, fail_silently False, the association id, the
owner's e-mail as reply_to); the effect checks the published message. With a
configuration Django sends after a random delay on a Timer thread: not
authored. The response is always {'msg': 'Partial quotes applied.'}.

payment/sign (POST): ``request.data`` with exactly the keys payment_id and
signature (otherwise 400 {'msg': 'validation errors.'}; a body without
keys() is a 500), is_valid_uuid (400 list), the first live group-aware
payment by id through scoped_queryset (none: 400 ['payment not found']),
the caller's association (``request.user.sport_association``: athletes 403,
owners without one 500; a payment of another association 403 'User not
allowed.'), SignatureRequestSerializer with raise_exception (400 field errors); without a
signature to store the answer is {'status': 'success'} and nothing is written.
A signature to store creates a Signature (the scope user, or the selected
person for collaborators, with the trimmed data; audited create indexed to
the actor's own association), sets it on the payment and runs Payment.save()
(defaults, audited update indexed directly), then
print_document_compensation.apply: one GET to the CURRENT_HOST print stub
(/document/compensation/<payment_id>, the request's Authorization).

payment/<uid>/approve (POST) for payments with an invoice: InvoiceSerializer
rendering. A paid payment with a falsy ``generate_invoice`` answers the
message; an unpaid expense or a payment without associate, supplier and
instructor returns before the invoice task. A truthy ``generate_invoice`` on a
paid invoiced payment (invoice PDF task) and unpaid income payments with a
party (invoice numbering, notification, PDF task) are not ported and not
authored; the other approve paths are payment-state's.
Run with ``python3 -I``.
"""
import copy
import json
from urllib.parse import quote_plus
from pathlib import Path

OUTPUT = Path(__file__).with_name('payment_misc_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
ids = iter(f'a1000000-0000-4000-8000-{993001 + n:012d}' for n in range(300))
SERVER = '<stable-server-error-page>'
DENOMINATION = 'Message reads own'

PLANS_OWNER = {'id': next(ids), 'association': next(ids), 'denomination': 'Piani fixture', 'first': 'Piani', 'last': 'Fixture',
               'email': 'piani-fixture@example.test', 'username': 'piani-fixture'}
CATEGORY = {'id': next(ids), 'name': 'Quote misc fixture'}
BANK = {'id': next(ids), 'name': 'Banca misc', 'code': 'BMX'}
DOCUMENT = {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta.pdf', 'filepath': 'docs/ricevuta.pdf', 'size': 2048,
            'created': '2026-02-03T08:00:00Z'}
ATTACHED = {'id': next(ids), 'token': next(ids), 'filename': 'allegato.pdf', 'filepath': 'docs/allegato.pdf', 'size': 1024,
            'created': '2026-02-04T08:00:00Z'}
SUPPLIER = {'id': next(ids), 'name': 'Fornitore misc', 'vat_number': '09876543210', 'email': 'fornitore-misc@example.test'}

ASSOCIATES = {
    'mario': {'id': next(ids), 'first': 'Mario', 'last': 'Rossi', 'association': OWN, 'group': None},
    'luca': {'id': next(ids), 'first': 'Luca', 'last': 'Verdi è', 'association': OWN, 'group': None},
    'giulia': {'id': next(ids), 'first': 'Giulia', 'last': 'Neri', 'association': OWN, 'group': None},
    'paolo': {'id': next(ids), 'first': 'Paolo', 'last': 'Bianchi', 'association': OWN, 'group': 'own_group'},
    'sara': {'id': next(ids), 'first': 'Sara', 'last': 'Blu', 'association': OWN, 'group': None},
    'hans': {'id': next(ids), 'first': 'Hans', 'last': 'Muller', 'association': FOREIGN, 'group': None},
    'nina': {'id': next(ids), 'first': 'Nina', 'last': 'Rùsso', 'association': PLANS_OWNER['association'], 'group': None},
}

# Subscription fee plans as stored in SportAssociation.subscription_fee_plans.
PLAN_B = {'id': 'plan-b', 'name': 'Quota B è', 'subscription_fee': '30', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'plan-a', 'from_day': '15', 'from_month': 3, 'to_day': 14, 'to_month': '6'}
PLAN_C = {'id': 'plan-c', 'name': 'Quota C', 'subscription_fee': 12.5, 'auto_assign': 1, 'advanced_options': 'yes',
          'previous_subscription_fee_plan': 'PLAN-X', 'from_day': 15.9, 'from_month': ' 3 ', 'to_day': True, 'to_month': 12}
PLAN_D = {'id': 'plan-d', 'name': 'Quota D', 'subscription_fee': '25.50', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'plan-q', 'from_day': 1, 'from_month': 4, 'to_day': 30, 'to_month': 4}
PLAN_E = {'id': 'plan-e', 'name': 'Quota E', 'subscription_fee': 7.25, 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-e', 'from_day': 1, 'from_month': 5, 'to_day': 31, 'to_month': 5}
# A member's own account (the athlete) holds the previous plan: the apply e-mails the member.
PLAN_G = {'id': 'plan-g', 'name': 'Quota G è', 'subscription_fee': '15', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-g', 'from_day': 1, 'from_month': 6, 'to_day': 30, 'to_month': 6}
# int() reads decimal digits of any script, with surrounding spaces: August 1st to 31st.
PLAN_H = {'id': 'plan-h', 'name': 'Quota H', 'subscription_fee': '8', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-h', 'from_day': '\uff11', 'from_month': ' \u0668 ', 'to_day': '\u0663\u0661', 'to_month': 8}
OWN_PLANS = [
    PLAN_B, PLAN_C,
    {'id': 'plan-off', 'name': 'Fuori data', 'subscription_fee': '1', 'auto_assign': True, 'advanced_options': True,
     'previous_subscription_fee_plan': 'plan-a', 'from_day': 16, 'from_month': 3, 'to_day': 1, 'to_month': 1},
    # Skipped before int(from_day): manual, blank or missing previous plan, falsy options.
    {'id': 'plan-manual', 'auto_assign': False, 'advanced_options': True, 'previous_subscription_fee_plan': 'plan-a', 'from_day': 'x'},
    {'id': 'plan-blank', 'auto_assign': True, 'advanced_options': True, 'previous_subscription_fee_plan': '', 'from_day': 'x'},
    {'id': 'plan-first', 'auto_assign': True, 'advanced_options': True, 'from_day': 'x'},
    {'id': 'plan-zero', 'auto_assign': True, 'advanced_options': 0, 'previous_subscription_fee_plan': 'plan-a', 'from_day': 'x'},
    {'id': 'plan-empty', 'auto_assign': 'x', 'advanced_options': [], 'previous_subscription_fee_plan': 'plan-a', 'from_day': 'x'},
    PLAN_D, PLAN_E, PLAN_G, PLAN_H,
]
PLAN_F = {'id': 'plan-f', 'name': 'Quota F', 'subscription_fee': '9', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-f', 'from_day': 15, 'from_month': 3, 'to_day': 1, 'to_month': 4}
# int('abc') raises inside the try: nothing is collected.
FOREIGN_PLANS = [PLAN_F, {'id': 'plan-bad', 'auto_assign': True, 'advanced_options': True, 'previous_subscription_fee_plan': 'prev-f',
                          'from_day': 'abc', 'from_month': 3}]
PLAN_Z = {'id': 'plan-z', 'name': 'Quota Z', 'subscription_fee': '5', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-z', 'from_day': 15, 'from_month': 3, 'to_day': 15, 'to_month': 4}
# The second association has a CommunicationConfiguration during its July apply (SMTP case); its member has her own account.
PLANS_MEMBER = {'id': next(ids), 'email': 'nina-piani@example.test', 'username': 'nina-piani', 'first': 'Nina', 'last': 'Rùsso'}
PLAN_Y = {'id': 'plan-y', 'name': 'Quota Y', 'subscription_fee': '11', 'auto_assign': True, 'advanced_options': True,
          'previous_subscription_fee_plan': 'prev-y', 'from_day': 1, 'from_month': 7, 'to_day': 31, 'to_month': 7}
PLANS = {OWN: OWN_PLANS, FOREIGN: FOREIGN_PLANS, PLANS_OWNER['association']: [PLAN_Z, PLAN_Y]}


def previous(plan_id):
    return {'subscription_data': {'id': plan_id, 'name': 'Quota precedente'}}


def subscription(associate, plan, *, association=OWN, user=OWNER, created='in_year', deleted=False, archived=False, meta=True,
                 payment_meta=True, payment=True, group=None):
    return {'id': next(ids), 'payment': next(ids) if payment else None, 'association': association, 'user': user,
            'associate': ASSOCIATES[associate]['id'] if associate else None, 'created': created, 'deleted': deleted, 'archived': archived,
            'meta': {'plan_id': plan} if meta else None, 'payment_meta': previous(plan) if payment_meta else None, 'group': group}


SUBSCRIPTIONS = {
    's1': subscription('mario', 'plan-a'),
    's2': subscription('luca', 'plan-a', user=ATHLETE),
    's3': subscription('giulia', 'plan-x'),
    's_existing': subscription('sara', 'plan-a'),
    's_group': subscription('paolo', 'plan-a', group='own_group'),
    's_old': subscription('mario', 'plan-a', created='last_year'),
    's_deleted': subscription('giulia', 'plan-a', deleted=True),
    's_archived': subscription('giulia', 'plan-a', archived=True),
    's_no_meta': subscription('giulia', 'plan-a', meta=False),
    's_payment_no_meta': subscription('giulia', 'plan-a', payment_meta=False),
    's_no_payment': subscription('giulia', 'plan-a', payment=False),
    's4': subscription('mario', 'plan-q'),
    's5': subscription('mario', 'plan-q'),
    's6': subscription('luca', 'prev-e', group='own_group'),
    's7': subscription('giulia', 'prev-e'),
    's_foreign': subscription('hans', 'prev-f', association=FOREIGN, user=IDS['foreign_owner']),
    's_foreign_plan_a': subscription('hans', 'plan-a', association=FOREIGN, user=IDS['foreign_owner']),
    's_z': subscription(None, 'prev-z', association=PLANS_OWNER['association'], user=PLANS_OWNER['id']),
    's8': subscription('luca', 'prev-g', user=ATHLETE),
    's9': subscription('paolo', 'prev-h', group='own_group'),
    's_y': subscription('nina', 'prev-y', association=PLANS_OWNER['association'], user=PLANS_MEMBER['id']),
}

# Payments: the subscriptions' previous-plan payments are seeded by the runner from SUBSCRIPTIONS.
COLUMNS = ('type', 'subject', 'amount', 'creation_date', 'payment_date', 'paid', 'expense', 'description', 'payment_category_id',
           'meta_payment_categories', 'invoice_id', 'user_id', 'associate_id', 'supplier_id', 'instructor_id', 'sport_association_id',
           'payment_intent_id', 'archived', 'invoice_fiscal_code', 'meta', 'notes', 'custom_accounts_id', 'signature_id', 'deleted',
           'group_id', 'course')
FOREIGN_KEYS = {'payment_category_id', 'invoice_id', 'user_id', 'associate_id', 'supplier_id', 'instructor_id', 'sport_association_id',
                'custom_accounts_id', 'signature_id', 'group_id'}
TIMES = {'creation_date', 'payment_date'}
JSONS = {'meta_payment_categories', 'meta', 'course'}


def row(**values):
    base = {'type': 'default', 'subject': 0, 'amount': '10.00', 'creation_date': '2026-10-01T08:00:00Z', 'payment_date': None, 'paid': False,
            'expense': False, 'description': 'Quota', 'payment_category_id': CATEGORY['id'], 'meta_payment_categories': None,
            'invoice_id': None, 'user_id': OWNER, 'associate_id': None, 'supplier_id': None, 'instructor_id': None,
            'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
            'notes': None, 'custom_accounts_id': None, 'signature_id': None, 'deleted': False, 'group_id': None, 'course': None}
    unknown = set(values) - set(base)
    assert not unknown, unknown
    return {**base, **values}


INVOICES = {
    'inv_paid': {'id': next(ids), 'number': 7, 'document': True, 'attached': True, 'membership_fee': '0.00', 'activity_fee': '25.00',
                 'description': 'Ricevuta è', 'meta_payment_categories': [{'amount': 5}], 'created': '2026-04-01T09:00:00Z'},
    'inv_expense': {'id': next(ids), 'number': None, 'document': False, 'attached': False, 'membership_fee': '0.00', 'activity_fee': '30.00',
                    'description': None, 'meta_payment_categories': None, 'created': '2026-04-02T09:00:00Z'},
    'inv_plain': {'id': next(ids), 'number': 8, 'document': False, 'attached': False, 'membership_fee': '1.50', 'activity_fee': '0.00',
                  'description': 'Ricevuta', 'meta_payment_categories': None, 'created': '2026-04-03T09:00:00.250000Z'},
}
PAYMENTS = {
    # The existing quote that skips s_existing for plan-b.
    'existing_quote': {'id': next(ids), **row(subject=1, amount='30.00', associate_id=ASSOCIATES['sara']['id'],
                                              meta={'subscription_data': {'id': 'plan-b'}}, description='Iscrizione')},
    # approve
    'ai_paid': {'id': next(ids), **row(subject=0, amount='25.00', paid=True, payment_date='2026-04-01T00:00:00Z', invoice_id=INVOICES['inv_paid']['id'],
                                       associate_id=ASSOCIATES['sara']['id'])},
    'ai_expense': {'id': next(ids), **row(subject=2, amount='30.00', expense=True, supplier_id=SUPPLIER['id'], invoice_id=INVOICES['inv_expense']['id'],
                                          description='Fattura misc')},
    'ai_plain': {'id': next(ids), **row(subject=2, amount='1.50', invoice_id=INVOICES['inv_plain']['id'], user_id=None)},
    # sign
    'sign_own': {'id': next(ids), **row()},
    'sign_group': {'id': next(ids), **row(group_id=IDS['own_group'])},
    'sign_removed': {'id': next(ids), **row(deleted=True)},
    'sign_abroad': {'id': next(ids), **row(sport_association_id=FOREIGN, user_id=IDS['foreign_owner'], payment_category_id=None)},
    'sign_uncategorized': {'id': 'a1000000-0000-4000-8000-000000993301', **row(payment_category_id=None)},
}
STATE = copy.deepcopy(PAYMENTS)
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        prints=()):
    case = {'name': f'payment-misc-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'prints': list(prints)}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


DENIED_ATHLETE = {'detail': 'User is not a sport association'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
GROUP = {'X-Group-Id': IDS['own_group']}
OTHER_GROUP = {'X-Group-Id': IDS['foreign_group']}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
missing = lambda key: {'detail': f'Missing permission: bookeeping.payments.{key}'}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'], 'parses': PARSES}
SIMULATE, APPLY, SIGN = '/payment/simulation/partial-quotes', '/payment/simulation/partial-quotes-apply', '/payment/sign'
APPROVE = lambda label: f'/payment/{PAYMENTS[label]["id"]}/approve'


def guards(prefix, path, verb, other, permission, name):
    add(f'{prefix}-anonymous', 'anonymous', path, 401, ANONYMOUS, method=verb)
    add(f'{prefix}-restricted-missing-permission', 'restricted_collaborator', path, 403, missing(permission), method=verb)
    add(f'{prefix}-admin-without-session', 'admin', path, 403, ADMIN, method=verb)
    add(f'{prefix}-owner-scoped-athlete-denied', 'owner', path, 403, SCOPED_DENIED, method=verb, headers=SCOPED)
    add(f'{prefix}-wrong-method', 'owner', path, 405, method(other), method=other)
    add(f'{prefix}-options', 'owner', path, 200, options(name), method='OPTIONS')


def effect(actor='owner', *, created=(), changed=None, audit=()):
    return {'actor': actor, 'created': list(created), 'changed': changed or {}, 'audit': list(audit)}


# --- simulation ----------------------------------------------------------------

def considered(plan):
    """calculate_simulation's active plan: int() of the day/month fields."""
    as_int = lambda value: int(value.strip()) if isinstance(value, str) else int(value)
    return {'from_day': as_int(plan['from_day']), 'from_month': as_int(plan['from_month']), 'to_day': as_int(plan['to_day']),
            'to_month': as_int(plan['to_month']), 'previous_subscription_fee_plan': plan['previous_subscription_fee_plan'],
            'name': plan['name'], 'subscription_fee': plan['subscription_fee'], 'id': plan['id']}


def message(associate, plan):
    person = ASSOCIATES[associate]
    return ('\n                            <p>Ciao ' + person['first'] + ' ' + person['last'] + ',</p>\n'
            '                            <p>Ti è stata assegnata la quota associativa "' + plan['name'] + '" con importo '
            + str(plan['subscription_fee']) + ' €.</p>\n                            <br>\n'
            '                            <p>Per maggiori informazioni, visita il tuo profilo su $whitelabel.</p>\n    \n'
            '                            <p>Cordialmente, <br />\n                             ' + DENOMINATION + '</p>\n                        ')


def quote(label, plan):
    sub = SUBSCRIPTIONS[label]
    associate = next(key for key, person in ASSOCIATES.items() if person['id'] == sub['associate'])
    person = ASSOCIATES[associate]
    return {'user': 'Message Fixture', 'associate': {'first_name': person['first'], 'last_name': person['last']},
            'amount': plan['subscription_fee'], 'subject': 1,
            'meta': {'subscription_data': {'subscription_fee': plan['subscription_fee'], 'name': plan['name'], 'id': plan['id']}},
            'message': message(associate, plan) if sub['user'] != OWNER else None}


def simulation(plans=(), quotes=()):
    return {'subscription_plans_considered': [considered(plan) for plan in plans], 'quotes_to_emit': list(quotes)}


EMPTY = simulation()
MARCH = simulation([PLAN_B, PLAN_C], [quote('s1', PLAN_B), quote('s2', PLAN_B), quote('s_group', PLAN_B), quote('s3', PLAN_C)])

guards('simulation', SIMULATE + '?date=2026-03-15', 'GET', 'POST', 'read', 'Payment Simulation Partial Quotes')
add('simulation-athlete-denied', 'athlete', SIMULATE + '?date=2026-03-15', 403, DENIED_ATHLETE)
add('simulation-owner-without-association-server-error', 'owner_without_association', SIMULATE + '?date=2026-03-15', 500, SERVER)
add('simulation-march', 'owner', SIMULATE + '?date=2026-03-15', 200, MARCH)
add('simulation-one-digit-month', 'owner', SIMULATE + '?date=2026-3-15', 200, MARCH)
add('simulation-last-date-value', 'owner', SIMULATE + '?date=bad&date=2026-03-15', 200, MARCH)
# strptime's \d (the year and the day's second digit) matches decimal digits of any script.
add('simulation-other-script-digits', 'owner', SIMULATE + '?date=' + quote_plus('\uff12\u0660\uff12\u0666-03-1\u096b'), 200, MARCH)
add('simulation-other-script-month-empty', 'owner', SIMULATE + '?date=' + quote_plus('2026-0\uff13-15'), 200, EMPTY)
add('simulation-august-other-script-plan', 'owner', SIMULATE + '?date=2026-08-01', 200, simulation([PLAN_H], [quote('s9', PLAN_H)]))
add('simulation-other-format-empty', 'owner', SIMULATE + '?date=15/03/2026', 200, EMPTY)
add('simulation-blank-date-empty', 'owner', SIMULATE + '?date=', 200, EMPTY)
add('simulation-invalid-day-empty', 'owner', SIMULATE + '?date=2026-02-30', 200, EMPTY)
add('simulation-no-active-plan', 'owner', SIMULATE + '?date=2026-03-17', 200, simulation())
add('simulation-group', 'owner', SIMULATE + '?date=2026-03-15', 200, simulation([PLAN_B, PLAN_C], [quote('s_group', PLAN_B)]), headers=GROUP)
add('simulation-other-group', 'owner', SIMULATE + '?date=2026-03-15', 200, simulation([PLAN_B, PLAN_C]), headers=OTHER_GROUP)
add('simulation-malformed-group-ignored', 'owner', SIMULATE + '?date=2026-03-15', 200, MARCH, headers={'X-Group-Id': 'abc'})
add('simulation-full-collaborator', 'full_collaborator', SIMULATE + '?date=2026-03-15', 200, MARCH)
add('simulation-admin-impersonating-owner', 'admin', SIMULATE + '?date=2026-03-15', 200, MARCH, headers=ADMIN_OWNER)
add('simulation-plan-error-empty', 'foreign_owner', SIMULATE + '?date=2026-03-15', 200, EMPTY)
add('simulation-missing-associate-stops', 'plans_owner', SIMULATE + '?date=2026-03-15', 200, simulation([PLAN_Z]))
add('simulation-april-before-apply', 'owner', SIMULATE + '?date=2026-04-01', 200, simulation([PLAN_D], [quote('s4', PLAN_D), quote('s5', PLAN_D)]))
add('simulation-may-before-apply', 'owner', SIMULATE + '?date=2026-05-01', 200, simulation([PLAN_E], [quote('s6', PLAN_E), quote('s7', PLAN_E)]))

# --- apply -----------------------------------------------------------------------

APPLIED = {'msg': 'Partial quotes applied.'}
DIRECT = 'direct'


def audit_value(column, value):
    if value is None:
        if column in FOREIGN_KEYS or column in TIMES:
            return None
        if column in JSONS:
            return 'null'
        return 'None'
    if column in TIMES:
        return value if value == '$now' else value.replace('T', ' ').replace('Z', '')
    if column in JSONS:
        return json.dumps(value, sort_keys=True)
    return str(value)


def field(column):
    return column[:-3] if column in FOREIGN_KEYS else column


def created_changes(values, amount):
    changes = {'payment_id': ['None', '$id']}
    for column in COLUMNS:
        value = amount if column == 'amount' else audit_value(column, values[column])
        if value == 'None' and column not in FOREIGN_KEYS:
            continue
        changes[field(column)] = ['None', 'None' if value is None else value]
    return changes


def quote_row(associate, plan, stored_amount, *, association=OWN, user=OWNER, account=BANK['id'], group=None):
    values = row(subject=1, amount=stored_amount, creation_date='$now', description='Iscrizione', payment_category_id='$default_category',
                 user_id=user, associate_id=ASSOCIATES[associate]['id'] if associate else None, sport_association_id=association,
                 meta={'subscription_data': {'subscription_fee': plan['subscription_fee'], 'name': plan['name'], 'id': plan['id']}},
                 custom_accounts_id=account, group_id=group)
    return values


def created(actor, values, amount, association=OWN):
    return effect(actor, created=[values], audit=[{'key': '$id', 'action': 0, 'changes': created_changes(values, amount),
                                                   'index': [association, DIRECT]}])


guards('apply', APPLY + '?date=2026-04-01', 'GET', 'POST', 'update', 'Payment Simulation Partial Quotes Apply')
add('apply-athlete-denied', 'athlete', APPLY + '?date=2026-04-01', 403, DENIED_ATHLETE, effect=effect())
add('apply-owner-without-association-server-error', 'owner_without_association', APPLY + '?date=2026-04-01', 500, SERVER)
add('apply-other-format-nothing', 'owner', APPLY + '?date=01/04/2026', 200, APPLIED, effect=effect())
add('apply-no-active-plan-nothing', 'owner', APPLY + '?date=2026-03-17', 200, APPLIED, effect=effect())
add('apply-plan-error-nothing', 'foreign_owner', APPLY + '?date=2026-03-15', 200, APPLIED, effect=effect('foreign_owner'))
add('apply-other-group-nothing', 'owner', APPLY + '?date=2026-04-01', 200, APPLIED, headers=OTHER_GROUP, effect=effect())
# s4 and s5 share user, associate, plan and amount: the second sees the first's payment.
add('apply-april-creates-one', 'owner', APPLY + '?date=2026-04-01', 200, APPLIED,
    effect=created('owner', quote_row('mario', PLAN_D, '25.50'), '25.50'))
add('apply-april-again-nothing', 'owner', APPLY + '?date=2026-04-01', 200, APPLIED, effect=effect())
add('apply-admin-impersonating-owner-nothing', 'admin', APPLY + '?date=2026-04-01', 200, APPLIED, headers=ADMIN_OWNER, effect=effect('admin'))
add('simulation-april-after-apply', 'owner', SIMULATE + '?date=2026-04-01', 200, simulation([PLAN_D]))
add('apply-may-group', 'owner', APPLY + '?date=2026-05-01', 200, APPLIED, headers=GROUP,
    effect=created('owner', quote_row('luca', PLAN_E, '7.25', group=IDS['own_group']), '7.25'))
# Without the header the group's quote is found and skipped; s7 is created.
add('apply-may-full-collaborator', 'full_collaborator', APPLY + '?date=2026-05-01', 200, APPLIED,
    effect=created('full_collaborator', quote_row('giulia', PLAN_E, '7.25'), '7.25'))
# The header in another spelling: the column stores the uuid, the create entry keeps the text as sent.
UPPER_GROUP = {'X-Group-Id': IDS['own_group'].upper()}
august = created('owner', quote_row('paolo', PLAN_H, '8.00', group=IDS['own_group']), '8')
august['audit'][0]['changes']['group'] = ['None', IDS['own_group'].upper()]
add('apply-august-group-upper-case', 'owner', APPLY + '?date=2026-08-01', 200, APPLIED, headers=UPPER_GROUP, effect=august)
add('apply-august-again-nothing', 'owner', APPLY + '?date=2026-08-01', 200, APPLIED, headers=UPPER_GROUP, effect=effect())
add('simulation-may-after-apply', 'owner', SIMULATE + '?date=2026-05-01', 200, simulation([PLAN_E]))
# The owner's own subscription without associate: created (no e-mail text is rendered for the owner).
add('apply-missing-associate-created', 'plans_owner', APPLY + '?date=2026-03-15', 200, APPLIED,
    effect=created('plans_owner', quote_row(None, PLAN_Z, '5.00', association=PLANS_OWNER['association'], user=PLANS_OWNER['id'],
                                            account=None), '5', PLANS_OWNER['association']))
add('apply-missing-associate-again-nothing', 'plans_owner', APPLY + '?date=2026-03-15', 200, APPLIED, effect=effect('plans_owner'))
TASK = 'core.tasks.send_mail_async'


def fee_mail(associate, plan, recipient):
    person = ASSOCIATES[associate]
    message = {'$render': 'email/payment/fee_assigned.html', 'language': 'en',
               'context': {'recipient_name': person['first'] + ' ' + person['last'], 'fee_name': plan['name'],
                           'amount': plan['subscription_fee'], 'application_name': '$whitelabel', 'association_name': DENOMINATION}}
    subject = f"{DENOMINATION} | Nuova quota associativa '{plan['name']}' assegnata"
    return {'task': TASK, 'args': [], 'kwargs': [['subject', subject], ['message', message], ['from_email', '$from_email'],
                                                 ['recipient_list', [recipient]], ['html_message', message], ['fail_silently', False],
                                                 ['sport_association_id', {'uuid': OWN}], ['reply_to', ['$email:owner']]]}


add('simulation-june-member-account', 'owner', SIMULATE + '?date=2026-06-01', 200, simulation([PLAN_G], [quote('s8', PLAN_G)]))
# The member's own account: the quote is created and the member is e-mailed (send_mail_async).
add('apply-june-member-account-mail', 'full_collaborator', APPLY + '?date=2026-06-01', 200, APPLIED,
    effect={**created('full_collaborator', quote_row('luca', PLAN_G, '15.00', user=ATHLETE), '15'),
            'celery': [fee_mail('luca', PLAN_G, '$email:athlete')]})
add('apply-june-again-nothing', 'owner', APPLY + '?date=2026-06-01', 200, APPLIED, effect=effect())

# With a CommunicationConfiguration (SMTP, no encryption) of the association, send_email_html loads it and sends from a
# Timer thread after random.randint(1, 30) seconds (patched to 1 for the run; Go's delay is pinned to 1 s):
# verify_smtp(send_email=False), then one delivery and daily_email_balance + 1 (Model.save, audited outside the request:
# no actor or cid). No Celery message. The runner creates the configuration for the request, waits for the
# delivery and the balance, then removes it (audit disabled).
SMTP_CONFIG = {'id': next(ids), 'user': 'mailer-piani@example.test', 'sender': 'Piani SMTP', 'limit': 50, 'balance': 0}


def smtp_mail(associate, plan, recipient):
    person = ASSOCIATES[associate]
    return {'from': f"{SMTP_CONFIG['sender']} <{SMTP_CONFIG['user']}>", 'to': recipient, 'reply_to': PLANS_OWNER['email'],
            'subject': f"{PLANS_OWNER['denomination']} | Nuova quota associativa '{plan['name']}' assegnata",
            'html': {'$render': 'email/payment/fee_assigned.html', 'language': 'en',
                     'context': {'recipient_name': person['first'] + ' ' + person['last'], 'fee_name': plan['name'],
                                 'amount': plan['subscription_fee'], 'application_name': '$whitelabel',
                                 'association_name': PLANS_OWNER['denomination']}}}


result = created('plans_owner', quote_row('nina', PLAN_Y, '11.00', association=PLANS_OWNER['association'], user=PLANS_MEMBER['id'], account=None), '11',
                 PLANS_OWNER['association'])
result['smtp_config_audit'] = {'changes': {'daily_email_balance': ['0', '1']}, 'index': [PLANS_OWNER['association'], DIRECT]}
add('apply-july-member-account-configured-smtp', 'plans_owner', APPLY + '?date=2026-07-01', 200, APPLIED, effect=result)
cases[-1]['smtp'] = {'deliveries': [smtp_mail('nina', PLAN_Y, PLANS_MEMBER['email'])], 'balance': 1}
add('apply-july-again-nothing', 'plans_owner', APPLY + '?date=2026-07-01', 200, APPLIED, effect=effect('plans_owner'))

# --- sign ------------------------------------------------------------------------

VALIDATION = {'msg': 'validation errors.'}
SUCCESS = {'status': 'success'}
OWN_ID = PAYMENTS['sign_own']['id']
sign = lambda payment_id, signature: {'payment_id': payment_id, 'signature': signature}
UNSIGNED = {'there_is_signature': False, 'data': None}

guards('sign', SIGN, 'POST', 'GET', 'update', 'Payment Sign')
add('sign-no-body', 'owner', SIGN, 400, VALIDATION, method='POST')
add('sign-only-payment', 'owner', SIGN, 400, VALIDATION, method='POST', body={'payment_id': OWN_ID})
add('sign-extra-key', 'owner', SIGN, 400, VALIDATION, method='POST', body={**sign(OWN_ID, UNSIGNED), 'other': 1})
add('sign-list-body-server-error', 'owner', SIGN, 500, SERVER, method='POST', body=['payment_id', 'signature'])
add('sign-malformed-id', 'owner', SIGN, 400, ['badly formed hexadecimal UUID string'], method='POST', body=sign('abc', 'x'))
add('sign-number-id', 'owner', SIGN, 400, ["'int' object has no attribute 'replace'"], method='POST', body=sign(5, UNSIGNED))
add('sign-null-id', 'owner', SIGN, 400, ['one of the hex, bytes, bytes_le, fields, or int arguments must be given'], method='POST',
    body=sign(None, UNSIGNED))
add('sign-missing-payment', 'owner', SIGN, 400, ['payment not found'], method='POST', body=sign(IDS['missing'], 'x'))
add('sign-deleted-payment', 'owner', SIGN, 400, ['payment not found'], method='POST', body=sign(PAYMENTS['sign_removed']['id'], UNSIGNED))
add('sign-other-group', 'owner', SIGN, 400, ['payment not found'], method='POST', body=sign(PAYMENTS['sign_group']['id'], UNSIGNED),
    headers=OTHER_GROUP)
# enforce_scoped_request checks the body's payment_id against the session's association first.
add('sign-scoped-session-foreign-denied', 'owner', SIGN, 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    method='POST', body=sign(PAYMENTS['sign_abroad']['id'], UNSIGNED),
    headers=OWNER_FULL)
add('sign-string-signature', 'owner', SIGN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']}, method='POST',
    body=sign(OWN_ID, 'x'))
add('sign-list-signature', 'owner', SIGN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST',
    body=sign(OWN_ID, []))
add('sign-null-signature', 'owner', SIGN, 400, {'non_field_errors': ['No data provided']}, method='POST', body=sign(OWN_ID, None))
add('sign-empty-signature', 'owner', SIGN, 400, {'data': ['This field is required.']}, method='POST', body=sign(OWN_ID, {}))
add('sign-field-errors', 'owner', SIGN, 400, {'there_is_signature': ['Must be a valid boolean.'], 'data': ['Not a valid string.']},
    method='POST', body=sign(OWN_ID, {'there_is_signature': 'maybe', 'data': True}))
add('sign-null-flag', 'owner', SIGN, 400, {'there_is_signature': ['This field may not be null.']}, method='POST',
    body=sign(OWN_ID, {'there_is_signature': None, 'data': 'x'}))
add('sign-required-signature', 'owner', SIGN, 400, {'data': ['La firma è obbligatoria.']}, method='POST',
    body=sign(OWN_ID, {'there_is_signature': True, 'data': ''}))
add('sign-required-blank-signature', 'owner', SIGN, 400, {'data': ['La firma è obbligatoria.']}, method='POST',
    body=sign(OWN_ID, {'there_is_signature': 'yes', 'data': '   '}))
add('sign-required-null-signature', 'owner', SIGN, 400, {'data': ['La firma è obbligatoria.']}, method='POST',
    body=sign(OWN_ID, {'there_is_signature': 1, 'data': None}))
add('sign-form-body', 'owner', SIGN, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']}, method='POST',
    raw_body=f'payment_id={OWN_ID}&signature=x', content_type='application/x-www-form-urlencoded')
add('sign-unsigned', 'owner', SIGN, 200, SUCCESS, method='POST', body=sign(OWN_ID, UNSIGNED), effect=effect())
add('sign-default-flag', 'owner', SIGN, 200, SUCCESS, method='POST', body=sign(OWN_ID.upper(), {'data': 'abc'}), effect=effect())
add('sign-false-strings', 'owner', SIGN, 200, SUCCESS, method='POST', body=sign(OWN_ID, {'there_is_signature': 'off', 'data': 12}),
    effect=effect())
add('sign-group', 'owner', SIGN, 200, SUCCESS, method='POST', body=sign(PAYMENTS['sign_group']['id'], UNSIGNED), headers=GROUP, effect=effect())
# Payment signing only applies to the caller's association (checked after the lookup, before the signature).
NOT_ALLOWED = {'detail': 'User not allowed.'}
add('sign-foreign-payment', 'owner', SIGN, 403, NOT_ALLOWED, method='POST', body=sign(PAYMENTS['sign_abroad']['id'], UNSIGNED))
add('sign-foreign-payment-with-signature', 'owner', SIGN, 403, NOT_ALLOWED, method='POST',
    body=sign(PAYMENTS['sign_abroad']['id'], {'there_is_signature': True, 'data': 'firma'}))
add('sign-foreign-payment-invalid-signature', 'full_collaborator', SIGN, 403, NOT_ALLOWED, method='POST',
    body=sign(PAYMENTS['sign_abroad']['id'], 'x'))
add('sign-athlete', 'athlete', SIGN, 403, DENIED_ATHLETE, method='POST', body=sign(OWN_ID, UNSIGNED))
add('sign-athlete-missing-payment', 'athlete', SIGN, 400, ['payment not found'], method='POST', body=sign(IDS['missing'], UNSIGNED))
add('sign-owner-without-association-server-error', 'owner_without_association', SIGN, 500, SERVER, method='POST', body=sign(OWN_ID, UNSIGNED))
add('sign-full-collaborator', 'full_collaborator', SIGN, 200, SUCCESS, method='POST', body=sign(OWN_ID, {'there_is_signature': 0, 'data': ''}),
    effect=effect('full_collaborator'))


def signed(actor, label, user, data, *, index, payment_index, extra=None, prints=True):
    """A stored signature: the Signature create, the payment's save (signature plus Payment.save() defaults) and the print."""
    changes = {'signature': ['None', '$signature'], **(extra or {})}
    columns = {'signature_id': '$signature', **{field_column(k): v[1] for k, v in (extra or {}).items()}}
    STATE[label]['signature_id'] = '$signature'
    return {**effect(actor, changed={label: columns}, audit=[
        {'key': '$signature', 'model': 'signature', 'action': 0,
         'changes': {'signature_id': ['None', '$signature'], 'user': ['None', user], 'signature': ['None', data]}, 'index': index},
        {'key': label, 'action': 1, 'changes': changes, 'index': payment_index}]),
        'signatures': [{'user': user, 'signature': data}]}


def field_column(name):
    return name + '_id' if name + '_id' in FOREIGN_KEYS else name


def compensation_print(label):
    return [{'path': f"/document/compensation/{PAYMENTS[label]['id']}", 'reply': {'status': 200, 'body': 'ok'}}]


# A signature to store: Signature row, payment save, print_document_compensation.apply.
add('sign-stores-signature', 'owner', SIGN, 200, SUCCESS, method='POST',
    body=sign(OWN_ID, {'there_is_signature': True, 'data': '  data:image/png;base64,iVBORw0KGgo=  '}),
    effect=signed('owner', 'sign_own', OWNER, 'data:image/png;base64,iVBORw0KGgo=', index=[OWN, 'actor.sport_association'],
                  payment_index=[OWN, DIRECT]), prints=compensation_print('sign_own'))
# A collaborator signs as the selected person; the payment without a category gets the default one on save.
add('sign-collaborator-stores-signature-default-category', 'full_collaborator', SIGN, 200, SUCCESS, method='POST',
    body=sign(PAYMENTS['sign_uncategorized']['id'], {'there_is_signature': 'true', 'data': 'firma'}),
    effect=signed('full_collaborator', 'sign_uncategorized', IDS['full_collaborator'], 'firma', index=None, payment_index=[OWN, DIRECT],
                  extra={'payment_category': ['None', '$default_category']}), prints=compensation_print('sign_uncategorized'))

# --- approve with an invoice -----------------------------------------------------

OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}


def associate_item(label):
    person = ASSOCIATES[label]
    return {'associate_id': person['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
            'born_date': '1990-05-10', 'email': None, 'sex': 'M', 'first_name': person['first'], 'last_name': person['last'],
            'is_minor': False, 'sport_association': person['association'], 'tax_code': None, 'user': OWNER, 'phone': None, 'family': None,
            'family_role': None}


def supplier_item():
    return {'supplier_id': SUPPLIER['id'], 'name': SUPPLIER['name'], 'address': None, 'tax_code': None, 'sport_association': OWN,
            'vat_number': SUPPLIER['vat_number'], 'type': 'supplier', 'email': SUPPLIER['email'], 'phone_number': None, 'city': None,
            'cap': None, 'state': None, 'country': 'Italia', 'nationality': 'Italiana', 'note': None, 'group': None, 'deleted': False}


def invoice_item(label):
    inv = INVOICES[label]
    return {'invoice_id': inv['id'], 'document_token': DOCUMENT['token'] if inv['document'] else None, 'creation_date': inv['created'],
            'membership_fee': inv['membership_fee'], 'activity_fee': inv['activity_fee'],
            'meta_payment_categories': inv['meta_payment_categories'], 'description': inv['description'], 'number': inv['number'],
            'archived': False, 'cancelled': False, 'meta': None, 'imported_from_associami': False, 'deleted': False,
            'document_pdf': DOCUMENT['id'] if inv['document'] else None, 'sport_association': OWN, 'selected_tutor': None, 'group': None,
            'attached_documents': [ATTACHED['id']] if inv['attached'] else []}


INVOICE_LABELS = {inv['id']: label for label, inv in INVOICES.items()}
ASSOCIATE_LABELS = {person['id']: label for label, person in ASSOCIATES.items()}


def payment_item(label):
    values = STATE[label]
    associate = values['associate_id']
    category = values['payment_category_id']
    # get_subscription_id: subject 0 is the associate's oldest unarchived subscription (Sara has one).
    subscription = SUBSCRIPTIONS['s_existing']['id'] if values['subject'] == 0 and associate == ASSOCIATES['sara']['id'] else None
    return {'payment_id': values['id'], 'user': OWNER_USER if values['user_id'] == OWNER else None,
            'associate': associate_item(ASSOCIATE_LABELS[associate]) if associate else None, 'subscription_id': subscription,
            'instructor_id': None, 'instructor': None, 'supplier': supplier_item() if values['supplier_id'] else None,
            'supplier_id': values['supplier_id'], 'custom_account_type': None, 'custom_account_name': None,
            'invoice': invoice_item(INVOICE_LABELS[values['invoice_id']]) if values['invoice_id'] else None, 'attachments': [],
            'payment_category_name': {CATEGORY['id']: CATEGORY['name'], None: None}[category], 'type': values['type'],
            'subject': values['subject'], 'amount': values['amount'], 'creation_date': values['creation_date'],
            'payment_date': values['payment_date'], 'paid': values['paid'], 'expense': values['expense'], 'description': values['description'],
            'meta_payment_categories': values['meta_payment_categories'], 'payment_intent_id': None, 'archived': values['archived'],
            'invoice_fiscal_code': None, 'meta': values['meta'], 'notes': None, 'deleted': values['deleted'], 'course': None,
            'payment_category': category, 'sport_association': values['sport_association_id'], 'custom_accounts': None, 'signature': None,
            'group': values['group_id']}


def approve_save(label, columns):
    values = STATE[label]
    changes = {}
    for column, value in columns.items():
        before, after = audit_value(column, values[column]), audit_value(column, value)
        changes[field(column)] = ['None' if before is None else before, after]
        values[column] = value
    return changes


add('approve-invoice-paid-without-invoice-task', 'owner', APPROVE('ai_paid'), 200,
    {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ai_paid')}}, method='POST', body={'generate_invoice': False},
    effect=effect())
add('approve-invoice-paid-zero-flag', 'owner', APPROVE('ai_paid'), 200,
    {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ai_paid')}}, method='POST',
    body={'generate_invoice': 0, 'payment_date': '2020-01-01'}, effect=effect())
add('approve-invoice-paid-blank-form-flag', 'owner', APPROVE('ai_paid'), 200,
    {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ai_paid')}}, method='POST',
    raw_body='generate_invoice=', content_type='application/x-www-form-urlencoded', effect=effect())
changes = approve_save('ai_expense', {'paid': True, 'payment_date': '2026-03-10T00:00:00Z'})
add('approve-invoice-expense', 'owner', APPROVE('ai_expense'), 200, {'data': {'payment': payment_item('ai_expense')}}, method='POST',
    body={'payment_date': '2026-03-10'},
    effect=effect(changed={'ai_expense': {'paid': True, 'payment_date': '2026-03-10T00:00:00Z'}},
                  audit=[{'key': 'ai_expense', 'action': 1, 'changes': changes, 'index': [OWN, DIRECT]}]))
add('approve-invoice-expense-now-paid', 'owner', APPROVE('ai_expense'), 200,
    {'data': {'msg': 'payment mark as payed.', 'payment': payment_item('ai_expense')}}, method='POST', body={'generate_invoice': None},
    effect=effect())
changes = approve_save('ai_plain', {'paid': True, 'payment_date': '$now'})
add('approve-invoice-without-party', 'full_collaborator', APPROVE('ai_plain'), 200, {'data': {'payment': payment_item('ai_plain')}},
    method='POST', body={},
    effect=effect('full_collaborator', changed={'ai_plain': {'paid': True, 'payment_date': '$now'}},
                  audit=[{'key': 'ai_plain', 'action': 1, 'changes': changes, 'index': [OWN, DIRECT]}]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'smtp_config': SMTP_CONFIG, 'matrix_version': 1, 'scope': 'Payment partial quotes simulation and apply, payment sign and invoiced approve '
                                                            'authored sequence with payment and audit effects',
                              'ids': IDS, 'plans_owner': PLANS_OWNER, 'plans_member': PLANS_MEMBER, 'category': CATEGORY, 'bank': BANK, 'document': DOCUMENT,
                              'attached': ATTACHED, 'supplier': SUPPLIER, 'associates': ASSOCIATES,
                              'plans': {key: value for key, value in PLANS.items()}, 'subscriptions': SUBSCRIPTIONS, 'invoices': INVOICES,
                              'payments': PAYMENTS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
