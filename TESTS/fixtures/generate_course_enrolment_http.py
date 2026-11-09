"""Author the course enrolment HTTP sequence (course-enrolment.md).

course-subscriptions/add (CourseSubscriptionViewSet.add, as_view({'post':
'add'}) plus the class's own delete()) is atomic: the request body (a list
means many=True) is validated by CourseSubscriptionOverviewSerializer (the
explicit course PrimaryKeyRelatedField over live courses, subscription_id a
UUIDField, the membership validate()); perform_create then creates each row
through the serializer (subscription by Subscription.objects.get, type from
the course, the body's events or the multi-payment course's future events
as payment + installment pairs) and, for default and multiple-quote courses,
runs add_course_subscription with User.sport_association (athletes: 403,
rolled back): the one-fee, course-fee or quote payment of the association
owner, linked by a save. Membership rows send membership_created, whose
receiver creates the membership payment for a positive fee. A single
athlete membership request fails on Course.objects.get(course_id=<Course>)
(500). The response renders every created row (201).

course/<uid>/overview/<uid_subscription>/add (course_overview_add, not
atomic) checks both uids, takes the caller's association by
filter().first(), the live group-aware course and subscription, refuses
another association's course (403) and then uses the subscription's
association; add_course_to_subscription creates the row (an existing live
row returns a Response the view cannot render: 500), the one-fee, quote,
installment or course-fee payment of the subscription's user and links the
last payment.

course-installment/<uid>/make-payment (course_installment_make_payment, not
atomic) resolves the live group-aware installment by id (missing: 500),
creates a payment for its amount and date for the subscription's user and
associate in the course's association and links it to the installment.
Run with ``python3 -I``.
"""
import copy
import datetime as dt
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('course_enrolment_http.json')
SUBSCRIPTION_OPTIONS = next(case['response'] for case in json.loads(Path(__file__).with_name('course_subscriptions_http.json').read_text())['cases']
                            if case['name'] == 'course-subscriptions-list-options')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, ATHLETE, FOREIGN_OWNER = IDS['owner'], IDS['athlete'], IDS['foreign_owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ids = iter(f'e0000000-0000-4000-8000-{n:012d}' for n in range(1, 400))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'e0000000-0000-4000-8000-000000999999'
CATEGORY_NAME = 'entrate e proventi da attività tipiche'
QUOTE_CATEGORY = next(ids)
# Sorts first by key: Payment.get_default_bank_account(own association) picks it.
BANK = '00000000-0000-4000-8000-0000000e0001'
cases = []

# --- seed ------------------------------------------------------------------------

ASSOCIATES = {label: {'associate_id': next(ids), 'sport_association_id': association, 'first_name': first, 'last_name': last,
                      'sex': 'M', 'tax_code': None, 'born_date': '1990-01-01', 'email': None, 'is_minor': False}
              for label, association, first, last in (('mario', OWN, 'Mario', 'Rossi'), ('luisa', OWN, 'Luisa', 'Bianchi'),
                                                      ('hans', FOREIGN, 'Hans', 'Muller'))}
SUBSCRIPTIONS = {}
for label, association, associate, user, group in (('mario', OWN, 'mario', ATHLETE, None), ('luisa', OWN, 'luisa', OWNER, None),
                                                   ('group', OWN, 'luisa', OWNER, OWN_GROUP),
                                                   ('hans', FOREIGN, 'hans', FOREIGN_OWNER, None)):
    SUBSCRIPTIONS[label] = {'subscription_id': next(ids), 'sport_association_id': association, 'associate_id': ASSOCIATES[associate]['associate_id'],
                            'user_id': user, 'group_id': group, 'start_date': '2020-09-01', 'end_date': '2099-08-31',
                            'creation_date': '2026-01-01T09:00:00Z'}


def course(title, *, association=OWN, fee='50.00', one_fee='0.00', one_fee_payment=False, multi=False, events=None, deleted=False, group=None,
           course_type=1, quotes=None):
    return {'course_id': next(ids), 'sport_association_id': association, 'status_flag': 2, 'course_type': course_type,
            'creation_date': '2026-02-01T08:00:00Z', 'title': title, 'description': 'Descrizione ' + title, 'multi_payments': multi, 'fee': fee,
            'events': events, 'one_fee_payment': one_fee_payment, 'one_fee': one_fee, 'google_calendar_id': None,
            'google_background_color': '#123456', 'multiple_quotes': quotes, 'start_date': None, 'end_date': None, 'pinned': False,
            'billed_duration_is_sport_season': False, 'billed_frequency': 1, 'billed_from_subscription_date': False,
            'billed_from_day_of_month': 1, 'auto_renewal': False, 'deleted': deleted, 'group_id': group}


EVENTS = [{'id': 0, 'amount': '20.00', 'payment_date': '10/01/2020'}, {'id': 1, 'amount': '30.00', 'payment_date': '10/01/2099'}]
QUOTES = [{'quote_id': 'q1', 'title': 'Mensile', 'amount': '25,50', 'payment_category': {'value': QUOTE_CATEGORY}},
          {'quote_id': 'q2', 'title': 'Annuale', 'amount': '200'}]
COURSES = {
    'fee': course('Nuoto'),
    'one': course('Tennis', one_fee='40.00', one_fee_payment=True),
    'multi': course('Danza', multi=True, events=EVENTS),
    'free': course('Libero', fee='0.00'),
    'quotes': course('Pilates', fee='0.00', course_type=2, quotes=QUOTES),
    'member': course('Palestra', fee='30.00', course_type=3),
    'gone': course('Rimosso', deleted=True),
    'group': course('Gruppo', fee='10.00', group=OWN_GROUP),
    'far': course('Lontano', association=FOREIGN, fee='15.00'),
    'far_member': course('Estero', association=FOREIGN, course_type=3),
    'orphan': course('Orfano', association=None),
    # overview add: one course per success so no pair repeats
    'ov_fee': course('Yoga'),
    'ov_one': course('Golf', one_fee='40.00', one_fee_payment=True),
    'ov_multi': course('Corsa', multi=True, events=EVENTS),
    'ov_quotes': course('Barre', fee='0.00', course_type=2, quotes=QUOTES),
    'ov_plain_quotes': course('Spinning', fee='0.00', course_type=2, quotes=QUOTES),
    'ov_free': course('Gratis', fee='0.00'),
    'ov_group': course('Squadra', fee='10.00', group=OWN_GROUP),
    'ov_athlete': course('Scherma', fee='12.00'),
    'ov_cross': course('Vela', fee='8.00'),
}


def payment(amount='10.00', *, paid=False, association=OWN, associate=None, user=None, meta=None, description='Quota', group=None):
    return {'payment_id': next(ids), 'type': 'default', 'subject': 2, 'amount': amount, 'creation_date': '2026-03-01T08:00:00Z',
            'payment_date': None, 'paid': paid, 'expense': False, 'description': description, 'payment_category_id': None,
            'meta_payment_categories': None, 'invoice_id': None, 'user_id': user, 'associate_id': associate, 'supplier_id': None,
            'instructor_id': None, 'sport_association_id': association, 'payment_intent_id': None, 'archived': False,
            'invoice_fiscal_code': None, 'meta': meta, 'notes': None, 'custom_accounts_id': None, 'signature_id': None, 'deleted': False,
            'group_id': group, 'course': None}


PAYMENTS = {'old': payment('30.00')}


def cs(course_label, subscription, *, multi=False, deleted=False):
    return {'course_subscription_id': next(ids), 'course_id': COURSES[course_label]['course_id'], 'paid': False,
            'creation_date': '2026-06-01T10:00:00Z', 'subscription_id': SUBSCRIPTIONS[subscription]['subscription_id'] if subscription else None,
            'payment_id': None, 'multi_payments': multi, 'one_fee_payment': False, 'archived': False, 'type': 1, 'multiple_quote': None,
            'billed_frequency': 1, 'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1, 'membership_active': True,
            'membership_fee': '0.00', 'auto_renewal': False, 'deleted': deleted}


CS = {
    'existing': cs('ov_fee', 'mario'),
    'mp': cs('multi', 'luisa', multi=True),
    'mp_far': cs('far', 'hans', multi=True),
    'mp_nosub': cs('multi', None, multi=True),
    'mp_athlete': cs('multi', 'mario', multi=True),
    'mp_gone': cs('multi', 'luisa', multi=True, deleted=True),
}


def installment(cs_label, number, *, payment_label=None, group=None, amount='30.00', deleted=False):
    return {'course_subscription_installment_id': next(ids), 'course_subscription_id': CS[cs_label]['course_subscription_id'], 'id': number,
            'amount': amount, 'paid': False, 'payment_date': '2099-01-10T00:00:00Z',
            'payment_id': PAYMENTS[payment_label]['payment_id'] if payment_label else None, 'archived': False, 'group_id': group,
            'deleted': deleted}


INSTALLMENTS = {
    'plain': installment('mp', 0),
    'linked': installment('mp', 1, payment_label='old', amount='25.50'),
    'group': installment('mp', 2, group=OWN_GROUP, amount='12.00'),
    'deleted': installment('mp', 3, deleted=True),
    'far': installment('mp_far', 0, amount='15.00'),
    'nosub': installment('mp_nosub', 0),
    'athlete': installment('mp_athlete', 0, amount='18.00'),
    'gone_row': installment('mp_gone', 0, amount='7.00'),
}
SEED = {'associates': copy.deepcopy(ASSOCIATES), 'subscriptions': copy.deepcopy(SUBSCRIPTIONS), 'locations': {}, 'tags': {},
        'courses': copy.deepcopy(COURSES), 'payments': copy.deepcopy(PAYMENTS), 'course_subscriptions': copy.deepcopy(CS),
        'installments': copy.deepcopy(INSTALLMENTS), 'registries': {}, 'days': {}, 'course_locations': [], 'course_tags': [],
        'categories': {QUOTE_CATEGORY: 'Quote fixture'}, 'bank': BANK}

# --- expectation helpers -----------------------------------------------------------

COURSE, CSM, CSI, PAYMENT = 'application.Course', 'application.CourseSubscription', 'application.CourseSubscriptionInstallment', 'application.Payment'
MEMBERSHIP_LINK = 'application.CourseSubscription_membership_payments'
MODEL_NAMES = {CSM: 'coursesubscription', CSI: 'coursesubscriptioninstallment', PAYMENT: 'payment'}
DIRECT, COURSE_INDEX, CSI_INDEX = 'direct', 'course.sport_association', 'course_subscription.course.sport_association'
cid = lambda label: COURSES[label]['course_id']
sid = lambda label: SUBSCRIPTIONS[label]['subscription_id']
aid = lambda label: ASSOCIATES[label]['associate_id']
iid = lambda label: INSTALLMENTS[label]['course_subscription_installment_id']
csid = lambda label: CS[label]['course_subscription_id']
person = {label: (row['user_id'], row['associate_id']) for label, row in SUBSCRIPTIONS.items()}


def stored(kind, **overrides):
    return {'$stored': kind, 'overrides': overrides}


def entry(model, key, action, changes, index, representation=None):
    return {'model': MODEL_NAMES[model], 'key': key, 'action': action, 'changes': changes, 'index': index, 'repr': representation}


def created_entry(model, sym, index, **overrides):
    return entry(model, sym, 0, stored('create', **overrides), index)


def py_moment(text, zone='datetime.timezone.utc'):
    value = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    parts = [value.year, value.month, value.day, value.hour, value.minute, value.second, value.microsecond]
    if parts[6] == 0:
        parts = parts[:6] if parts[5] else parts[:5]
    return f"datetime.datetime({', '.join(map(str, parts))}, tzinfo={zone})"


def py_repr(value):
    return repr(value)


def cs_repr(sym, course_label, subscription, *, payment=None, multi=False, one_fee=False, type_=1, quote=None, fee='0.0',
            billed_from=None, billed_until=None, frequency=1, day=1, renewal=False, paid=False, active=True):
    """CourseSubscription.__str__ (str(self.__dict__)) of the instance created in this request."""
    uuid = lambda value: 'None' if value is None else f"UUID('{value}')"
    zone = "zoneinfo.ZoneInfo(key='UTC')"
    fields = {'course_subscription_id': uuid(sym), 'course_id': uuid(cid(course_label)), 'paid': repr(paid),
              'creation_date': '{created:' + sym + '}', 'subscription_id': uuid(sid(subscription)), 'payment_id': uuid(payment),
              'multi_payments': repr(multi), 'one_fee_payment': repr(one_fee), 'archived': 'False', 'type': repr(type_),
              'multiple_quote': py_repr(quote), 'billed_frequency': repr(frequency),
              'billed_from': py_moment(billed_from, zone) if billed_from else 'None',
              'billed_until': py_moment(billed_until, zone) if billed_until else 'None', 'billed_from_day_of_month': repr(day),
              'membership_active': repr(active), 'membership_fee': fee if fee == '0.0' else f"Decimal('{fee}')", 'auto_renewal': repr(renewal),
              'deleted': 'False'}
    return "{'_state': <django.db.models.base.ModelState object at 0x?>, " + ', '.join(f"'{k}': {v}" for k, v in fields.items()) + '}'


def new_cs(sym, course_label, subscription, *, payment=None, multi=False, one_fee=False, type_=1, quote=None, fee='0.00', billed_from=None,
           billed_until=None, frequency=1, renewal=False):
    return {'$sym': sym, 'course_subscription_id': sym, 'course_id': cid(course_label), 'paid': False, 'creation_date': '$now',
            'subscription_id': sid(subscription), 'payment_id': payment, 'multi_payments': multi, 'one_fee_payment': one_fee, 'archived': False,
            'type': type_, 'multiple_quote': quote, 'billed_frequency': frequency, 'billed_from': billed_from, 'billed_until': billed_until,
            'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': fee, 'auto_renewal': renewal, 'deleted': False}


def new_payment(sym, amount, *, user, associate, association=OWN, created='$now', description=None, meta=None, group=None,
                category='$category', course_json=None, payment_date=None, bank=None):
    if description is None:
        # Payment.save(): get_course_carnet_name() joins the course label, else 'Quota Corso'.
        description = course_json['label'] + ', ' if course_json else 'Quota Corso'
    return {'$sym': sym, 'payment_id': sym, 'type': 'default', 'subject': 2, 'amount': amount, 'creation_date': created,
            'payment_date': payment_date, 'paid': False, 'expense': False, 'description': description, 'payment_category_id': category,
            'meta_payment_categories': None, 'invoice_id': None, 'user_id': user, 'associate_id': associate, 'supplier_id': None,
            'instructor_id': None, 'sport_association_id': association, 'payment_intent_id': None, 'archived': False,
            'invoice_fiscal_code': None, 'meta': meta, 'notes': None, 'custom_accounts_id': (bank or (BANK if association == OWN else '$bank_far')) if user else None, 'signature_id': None,
            'deleted': False, 'group_id': group, 'course': course_json}


def new_installment(sym, cs_key, number, amount, payment_sym, date, group=None):
    return {'$sym': sym, 'course_subscription_installment_id': sym, 'course_subscription_id': cs_key, 'id': number, 'amount': amount,
            'paid': False, 'payment_date': date + 'T00:00:00Z', 'payment_id': payment_sym, 'archived': False, 'group_id': group, 'deleted': False}


def effect(actor, *, rows=None, created=None, links=None, audit=()):
    return {'actor': actor, 'rows': rows or {}, 'created': created or {}, 'links': links or {}, 'audit': list(audit)}


def add(name, actor, path, status, response, *, method='POST', body=None, headers=None, result=None):
    case = {'name': f'course-enrolment-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': result}
    if body is not None:
        case['body'] = body
    cases.append(case)


def options(name, description=''):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
MALFORMED = ['badly formed hexadecimal UUID string']
NOT_ALLOWED = {'detail': 'User not allowed.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
ATHLETE_SESSION = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
GROUP = lambda key: {'X-Group-Id': IDS[key]}
missing = lambda key: {'detail': f'Missing permission: {key}'}
method_not_allowed = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
REQUIRED = ['This field is required.']

# --- rendering ----------------------------------------------------------------------

USERS = {OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
                 'username': 'message-reads-700001', 'role': 1},
         FOREIGN_OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700007@example.test', 'phone': None,
                         'username': 'message-reads-700007', 'role': 1},
         ATHLETE: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700004@example.test', 'phone': None,
                   'username': 'message-reads-700004', 'role': 2}}


ACCOUNTS = {None: (None, None), BANK: (2, 'Banca fixture'), '$bank_far': ('$bank_far_type', '$bank_far_name')}


def jsonb(value):
    """A JSON value as read back from jsonb (object keys by length, then bytes)."""
    if isinstance(value, dict):
        return {k: jsonb(value[k]) for k in sorted(value, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    return value


def associate(label):
    row = ASSOCIATES[label]
    return {'associate_id': row['associate_id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
            'born_date': row['born_date'], 'email': None, 'sex': 'M', 'first_name': row['first_name'], 'last_name': row['last_name'],
            'is_minor': False, 'sport_association': row['sport_association_id'], 'tax_code': None,
            'user': OWNER if row['sport_association_id'] == OWN else FOREIGN_OWNER, 'phone': None, 'family': None, 'family_role': None}


def associate_label(associate_id):
    return next(label for label, row in ASSOCIATES.items() if row['associate_id'] == associate_id)


def subscription(label):
    row = SUBSCRIPTIONS[label]
    return {'subscription_id': row['subscription_id'], 'associate': associate(associate_label(row['associate_id'])), 'current_year': 'corrente'}


def medical_label(label):
    return f'<span class="text-warning font-weight-boldest">Mancante da {{missing:{SUBSCRIPTIONS[label]["creation_date"]}}}</span>'


def payment_item(row, *, subscription_id=None):
    """PaymentSerializer of a payment row authored with new_payment."""
    user = row['user_id']
    return {'payment_id': row['payment_id'], 'user': USERS.get(user), 'associate': associate(associate_label(row['associate_id'])),
            'subscription_id': subscription_id, 'instructor_id': None, 'instructor': None, 'supplier': None, 'supplier_id': None,
            'custom_account_type': ACCOUNTS[row['custom_accounts_id']][0], 'custom_account_name': ACCOUNTS[row['custom_accounts_id']][1],
            'invoice': None,
            'attachments': [], 'payment_category_name': '$category_name' if row['payment_category_id'] == '$category' else 'Quote fixture',
            'type': 'default', 'subject': 2, 'amount': row['amount'], 'creation_date': row['creation_date'], 'payment_date': row['payment_date'],
            'paid': False, 'expense': False, 'description': row['description'], 'meta_payment_categories': None, 'payment_intent_id': None,
            'archived': False, 'invoice_fiscal_code': None, 'meta': row['meta'], 'notes': None, 'deleted': False, 'course': row['course'],
            'payment_category': row['payment_category_id'], 'sport_association': row['sport_association_id'],
            'custom_accounts': row['custom_accounts_id'], 'signature': None, 'group': row['group_id']}


def overview_item(row, subscription_label, *, installments=(), payments=(), creation='$now'):
    """CourseSubscriptionOverviewSerializer of a created row (new_cs)."""
    return {'course_subscription_id': row['course_subscription_id'], 'creation_date': creation, 'paid': False,
            'subscription': subscription(subscription_label), 'multi_payments': row['multi_payments'], 'multiple_quote': row['multiple_quote'],
            'one_fee_payment': row['one_fee_payment'], 'installments': list(installments), 'course': row['course_id'], 'type': row['type'],
            'billed_frequency': row['billed_frequency'], 'billed_from': row['billed_from'], 'billed_until': row['billed_until'],
            'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': row['membership_fee'],
            'auto_renewal': row['auto_renewal'], 'membership_payments': list(payments), 'medical_label': medical_label(subscription_label),
            'carnets': []}


def installment_item(row):
    return {'course_subscription_installment_id': row['course_subscription_installment_id'], 'id': row['id'], 'amount': row['amount'],
            'payment_date': row['payment_date'][:10], 'paid': False}


def course_item(label):
    """CourseSerializer of a seeded course (course-overview.md)."""
    row = COURSES[label]
    return {'course_id': row['course_id'], 'athletes': None, 'tags': [], 'locations': [], 'status_flag': 2, 'course_type': row['course_type'],
            'creation_date': row['creation_date'], 'title': row['title'], 'description': row['description'],
            'multi_payments': row['multi_payments'], 'fee': row['fee'], 'events': row['events'], 'one_fee_payment': row['one_fee_payment'],
            'one_fee': row['one_fee'], 'google_calendar_id': None, 'google_background_color': '#123456', 'multiple_quotes': row['multiple_quotes'],
            'start_date': None, 'end_date': None, 'pinned': False, 'billed_duration_is_sport_season': False, 'billed_frequency': 1,
            'billed_from_subscription_date': False, 'billed_from_day_of_month': 1, 'auto_renewal': False, 'deleted': False,
            'sport_association': row['sport_association_id'], 'group': row['group_id']}


def simple_cs(row, course_label, payment=None):
    """CourseSubscriptionSerializer ('__all__', nested course) of the in-memory row."""
    return {'course_subscription_id': row['course_subscription_id'], 'course': course_item(course_label), 'paid': False, 'creation_date': '$now',
            'subscription': row['subscription_id'], 'payment': payment, 'multi_payments': row['multi_payments'], 'one_fee_payment': False,
            'archived': False, 'type': 1, 'multiple_quote': row['multiple_quote'], 'billed_frequency': 1, 'billed_from': None,
            'billed_until': None, 'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': '0.00', 'auto_renewal': False,
            'deleted': False, 'membership_payments': []}


def event_date(text):
    return dt.datetime.strptime(text, '%d/%m/%Y').date()


# --- course-installment/<uid>/make-payment ---------------------------------------------

MP_PATH = lambda label: f'/course-installment/{iid(label) if label in INSTALLMENTS else label}/make-payment'
add('make-payment-anonymous', 'anonymous', MP_PATH('plain'), 401, ANONYMOUS)
add('make-payment-admin-without-session', 'admin', MP_PATH('plain'), 403, ADMIN)
add('make-payment-restricted-missing-permission', 'restricted_collaborator', MP_PATH('plain'), 403, missing('association.courses.update'))
add('make-payment-wrong-method', 'owner', MP_PATH('plain'), 405, method_not_allowed('GET'), method='GET')
add('make-payment-options', 'owner', MP_PATH('plain'), 200, options('Course Installment Make Payment'), method='OPTIONS')
add('make-payment-malformed', 'owner', MP_PATH('abc'), 400, MALFORMED)
add('make-payment-unknown', 'owner', MP_PATH(UNKNOWN), 500, SERVER)
add('make-payment-deleted-installment', 'owner', MP_PATH('deleted'), 500, SERVER)
add('make-payment-group-header-hides', 'owner', MP_PATH('group'), 500, SERVER, headers=GROUP('foreign_group'))
add('make-payment-without-subscription', 'owner', MP_PATH('nosub'), 500, SERVER)


def make_payment(name, actor, label, *, sym, headers=None, group=None):
    row = INSTALLMENTS[label]
    cs_row = next(item for item in CS.values() if item['course_subscription_id'] == row['course_subscription_id'])
    sub_label = next(key for key, value in SUBSCRIPTIONS.items() if value['subscription_id'] == cs_row['subscription_id'])
    course_row = next(item for item in COURSES.values() if item['course_id'] == cs_row['course_id'])
    association = course_row['sport_association_id']
    user, associate_id = person[sub_label]
    created = new_payment(sym, row['amount'], user=user, associate=associate_id, association=association, created=row['payment_date'],
                          group=group)
    audit = [created_entry(PAYMENT, sym, [association, DIRECT]),
             entry(CSI, iid(label), 1, {'payment': [row['payment_id'] or 'None', sym]}, [association, CSI_INDEX])]
    response = {'data': payment_item(created, subscription_id=sid(sub_label))}
    add(name, actor, MP_PATH(label), 200, response, headers=headers,
        result=effect(actor, rows={CSI: {iid(label): {'payment_id': sym}}}, created={PAYMENT: [created]}, audit=audit))


make_payment('make-payment-owner', 'owner', 'plain', sym='@mp1')
# The installment's previous payment stays; the new one replaces the link.
make_payment('make-payment-replaces-link', 'full_collaborator', 'linked', sym='@mp2')
make_payment('make-payment-grouped', 'owner', 'group', sym='@mp3', headers=GROUP('own_group'), group=OWN_GROUP)
make_payment('make-payment-athlete', 'athlete', 'athlete', sym='@mp4')
# The installment resolves by id; the payment takes the course's association.
make_payment('make-payment-course-association', 'owner', 'far', sym='@mp6')
make_payment('make-payment-soft-deleted-row', 'owner', 'gone_row', sym='@mp7')
add('make-payment-owner-scoped-athlete-hidden', 'owner', MP_PATH('plain'), 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, headers=ATHLETE_SESSION)

# --- course/<uid>/overview/<uid_subscription>/add --------------------------------------

OV_DOC = ('API endpoint to add a new list of users to a course\n:param request: data: {}\n:param uid: course_id\n'
          ':param uid_subscription: subscription_id\n:return: token, refresh token')
oa = lambda course_label, sub: f'/course/{cid(course_label) if course_label in COURSES else course_label}/overview/' \
                               f'{sid(sub) if sub in SUBSCRIPTIONS else sub}/add'
add('overview-add-anonymous', 'anonymous', oa('ov_fee', 'luisa'), 401, ANONYMOUS, body={})
add('overview-add-admin-without-session', 'admin', oa('ov_fee', 'luisa'), 403, ADMIN, body={})
add('overview-add-restricted-missing-permission', 'restricted_collaborator', oa('ov_fee', 'luisa'), 403, missing('association.courses.update'),
    body={})
add('overview-add-wrong-method', 'owner', oa('ov_fee', 'luisa'), 405, method_not_allowed('GET'), method='GET')
add('overview-add-options', 'owner', oa('ov_fee', 'luisa'), 200, options('Course Overview Add', OV_DOC), method='OPTIONS')
add('overview-add-malformed-course', 'owner', oa('abc', 'luisa'), 400, MALFORMED, body={})
add('overview-add-malformed-subscription', 'owner', oa('ov_fee', 'abc'), 400, MALFORMED, body={})
add('overview-add-unknown-course', 'owner', oa(UNKNOWN, 'luisa'), 500, SERVER, body={})
add('overview-add-deleted-course', 'owner', oa('gone', 'luisa'), 500, SERVER, body={})
add('overview-add-group-header-hides-course', 'owner', oa('ov_group', 'group'), 500, SERVER, body={}, headers=GROUP('foreign_group'))
add('overview-add-other-association-course', 'owner', oa('far', 'hans'), 403, NOT_ALLOWED, body={})
add('overview-add-course-without-association', 'owner', oa('orphan', 'luisa'), 500, SERVER, body={})
add('overview-add-unknown-subscription', 'owner', oa('ov_fee', UNKNOWN), 500, SERVER, body={})
add('overview-add-existing-row', 'owner', oa('ov_fee', 'mario'), 500, SERVER, body={})
add('overview-add-owner-scoped-athlete', 'owner', oa('ov_fee', 'luisa'), 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, body={}, headers=ATHLETE_SESSION)


def course_json(label):
    return {'label': COURSES[label]['title'], 'value': cid(label)}


def overview_add(name, actor, course_label, sub, *, sym, body=None, headers=None, group=None, kind='fee'):
    """add_course_to_subscription's row, payment(s) and saves for the authored kind."""
    association = SUBSCRIPTIONS[sub]['sport_association_id']
    user, associate_id = person[sub]
    course_row = COURSES[course_label]
    course_association = course_row['sport_association_id']
    cs_sym, pay = '@' + sym, '@' + sym + 'p'
    created, audit, payment_json, rows_payment = {}, [], None, None
    quote = jsonb(QUOTES[0]) if kind == 'quote' else None
    row = new_cs(cs_sym, course_label, sub, quote=quote, multi=kind == 'multi')
    multi_override = {'multi_payments': ['None', 'False']} if kind == 'multi' else {}
    audit.append(entry(CSM, cs_sym, 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None'], **multi_override),
                       [course_association, COURSE_INDEX], cs_repr(cs_sym, course_label, sub, quote=quote)))
    pays = []
    if kind == 'fee':
        pays.append(new_payment(pay, course_row['fee'], user=user, associate=associate_id, association=association, group=group,
                                course_json=course_json(course_label)))
        audit.append(created_entry(PAYMENT, pay, [association, DIRECT]))
        payment_json = {'payment_id': pay, 'amount': float(course_row['fee']), 'paid': False, 'creation_date': '$now'}
    elif kind == 'one':
        pays.append(new_payment(pay, course_row['one_fee'], user=user, associate=associate_id, association=association, group=group,
                                course_json=course_json(course_label), meta={'one_fee': 'true'}))
        audit.append(created_entry(PAYMENT, pay, [association, DIRECT]))
        payment_json = {'payment_id': pay, 'amount': float(course_row['one_fee']), 'paid': False, 'creation_date': '$now'}
    elif kind == 'quote':
        pays.append(new_payment(pay, '25.50', user=user, associate=associate_id, association=association, group=group,
                                course_json=course_json(course_label), meta={'multiple_quote': QUOTES[0]}, category=QUOTE_CATEGORY))
        audit += [created_entry(PAYMENT, pay, [association, DIRECT], amount=['None', '25.5']),
                  entry(PAYMENT, pay, 1, {'amount': ['25.50', '25.5']}, [association, DIRECT])]
        payment_json = {'payment_id': pay, 'amount': 25.5, 'paid': False, 'creation_date': '$now'}
    elif kind == 'multi':
        audit.append(entry(CSM, cs_sym, 1, {'multi_payments': ['False', 'True'], 'membership_fee': ['0.00', '0.0']},
                           [course_association, COURSE_INDEX], cs_repr(cs_sym, course_label, sub, multi=True)))
        event = EVENTS[1]
        date = event_date(event['payment_date']).isoformat()
        pays.append(new_payment(pay, event['amount'], user=user, associate=associate_id, association=association, group=group,
                                course_json=course_json(course_label), created=date + 'T00:00:00Z'))
        created[CSI] = [new_installment('@' + sym + 'i', cs_sym, event['id'], event['amount'], pay, date, group=group)]
        audit += [created_entry(PAYMENT, pay, [association, DIRECT]), created_entry(CSI, '@' + sym + 'i', [course_association, CSI_INDEX])]
        payment_json = {'payment_id': pay, 'amount': float(event['amount']), 'paid': False, 'creation_date': date}
    if pays:
        row['payment_id'] = pay
        audit.append(entry(CSM, cs_sym, 1, {'payment': ['None', pay], 'membership_fee': ['0.00', '0.0']}, [course_association, COURSE_INDEX],
                           cs_repr(cs_sym, course_label, sub, quote=quote, multi=kind == 'multi', payment=pay)))
    created[CSM] = [row]
    if pays:
        created[PAYMENT] = pays
    response = {'data': {'msg': 'associate added.', 'payment': payment_json, 'course_subscription': simple_cs(row, course_label, row['payment_id'])}}
    add(name, actor, oa(course_label, sub), 200, response, body=body if body is not None else {}, headers=headers,
        result=effect(actor, created=created, audit=audit))


overview_add('overview-add-course-fee', 'owner', 'ov_fee', 'luisa', sym='oa1')
overview_add('overview-add-one-fee', 'full_collaborator', 'ov_one', 'luisa', sym='oa2', body={'one_fee_payment': True}, kind='one')
# Instalments: only the future event (no full_installments_plan); the last payment is linked.
overview_add('overview-add-installments', 'owner', 'ov_multi', 'luisa', sym='oa3', kind='multi')
overview_add('overview-add-quote', 'owner', 'ov_quotes', 'luisa', sym='oa4', body={'multiple_quote': {'value': 'q1'}}, kind='quote')
# Without multiple_quote the first quote is only written into the request data: no payment.
overview_add('overview-add-quote-not-selected', 'owner', 'ov_plain_quotes', 'luisa', sym='oa5', kind='none')
# Not atomic: an unknown quote stays the raw selection; the payment then fails after the row exists.
raw_quote = {'value': 'zz'}
add('overview-add-unknown-quote-fails', 'owner', oa('ov_quotes', 'mario'), 500, SERVER, body={'multiple_quote': raw_quote}, result=effect(
    'owner', created={CSM: [new_cs('@oa12', 'ov_quotes', 'mario', quote=raw_quote)]}, audit=[
        entry(CSM, '@oa12', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@oa12', 'ov_quotes', 'mario', quote=raw_quote))]))
overview_add('overview-add-free-course', 'owner', 'ov_free', 'luisa', sym='oa6', kind='none')
overview_add('overview-add-grouped', 'owner', 'ov_group', 'group', sym='oa7', headers=GROUP('own_group'), group=OWN_GROUP)
# Athletes and owners without an association use the subscription's association.
overview_add('overview-add-athlete', 'athlete', 'ov_athlete', 'mario', sym='oa8')
overview_add('overview-add-owner-without-association', 'owner_without_association', 'ov_free', 'mario', sym='oa9', kind='none')
overview_add('overview-add-subscription-association', 'owner', 'ov_cross', 'hans', sym='oa10')

# --- course-subscriptions/add ---------------------------------------------------------

CA = '/course-subscriptions/add'
add('add-anonymous', 'anonymous', CA, 401, ANONYMOUS, body={})
add('add-admin-without-session', 'admin', CA, 403, ADMIN, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', CA, 403, missing('association.courses.create'), body={})
add('add-owner-scoped-athlete-denied', 'owner', CA, 403, SCOPED, body={}, headers=ATHLETE_SESSION)
add('add-wrong-method', 'owner', CA, 405, method_not_allowed('GET'), method='GET')
add('add-options', 'owner', CA, 200, SUBSCRIPTION_OPTIONS, method='OPTIONS')
add('add-delete-without-pk', 'owner', CA, 500, SERVER, method='DELETE')
add('add-delete-athlete', 'athlete', CA, 403, NOT_ASSOCIATION, method='DELETE')
add('add-empty-body', 'owner', CA, 400, {'course': REQUIRED, 'subscription_id': REQUIRED}, body={})
add('add-field-errors', 'owner', CA, 400, {
    'course': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'subscription_id': ['Must be a valid UUID.'],
    'paid': ['Must be a valid boolean.'], 'billed_frequency': ['Ensure this value is less than or equal to 32767.'],
    'membership_fee': ['A valid number is required.']},
    body={'course': UNKNOWN, 'subscription_id': 'abc', 'paid': 'maybe', 'billed_frequency': 40000, 'membership_fee': 'x'})
add('add-deleted-course', 'owner', CA, 400, {'course': [f'Invalid pk "{cid("gone")}" - object does not exist.']},
    body={'course': cid('gone'), 'subscription_id': sid('luisa')})
add('add-text-body', 'owner', CA, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got str.']}, body='x')
add('add-list-item-errors', 'owner', CA, 400, [{}, {'course': REQUIRED, 'subscription_id': REQUIRED}],
    body=[{'course': cid('fee'), 'subscription_id': sid('luisa')}, {}])
add('add-empty-list', 'owner', CA, 201, [], body=[], result=effect('owner'))
add('add-duplicate-key', 'owner', CA, 400, {'course_subscription_id': ['course subscription with this course subscription id already exists.']},
    body={'course_subscription_id': csid('mp'), 'course': cid('fee'), 'subscription_id': sid('luisa')})
add('add-unknown-subscription', 'owner', CA, 500, SERVER, body={'course': cid('fee'), 'subscription_id': UNKNOWN})
add('add-group-header-hides-subscription', 'owner', CA, 500, SERVER, body={'course': cid('fee'), 'subscription_id': sid('luisa')},
    headers=GROUP('own_group'))
add('add-membership-errors', 'owner', CA, 400, {
    'subscription_id': ['Seleziona un tesserato della stessa associazione del corso.'], 'billed_from': ['Inserisci una data di inizio valida.'],
    'billed_until': ['Inserisci una data di fine valida.'], 'billed_frequency': ['La durata deve essere compresa tra 1 e 12 mesi.'],
    'membership_fee': ['La quota deve essere un importo maggiore o uguale a zero.']},
    body={'course': cid('member'), 'subscription_id': sid('hans'), 'billed_frequency': 0, 'membership_fee': '-1'})
add('add-membership-other-association', 'owner', CA, 400, {
    'course': ['Il corso non appartiene alla tua associazione.'], 'subscription_id': ['Seleziona un tesserato della stessa associazione del corso.'],
    'billed_until': ['La data di fine non può precedere la data di inizio.']},
    body={'course': cid('far_member'), 'subscription_id': sid('luisa'), 'billed_from': '2026-03-01T00:00:00Z', 'billed_until': '2026-02-01'})
add('add-event-errors', 'owner', CA, 400, {'id': ['Ensure this value is less than or equal to 30.'], 'amount': REQUIRED, 'payment_date': REQUIRED},
    body={'course': cid('free'), 'subscription_id': sid('luisa'), 'events': [{'id': 31}]})
# User.sport_association after the row exists: rolled back.
add('add-athlete-denied', 'athlete', CA, 403, NOT_ASSOCIATION, body={'course': cid('fee'), 'subscription_id': sid('mario')})
add('add-athlete-membership-server-error', 'athlete', CA, 500, SERVER, body={'course': cid('member'), 'subscription_id': sid('mario')})
add('add-owner-without-association', 'owner_without_association', CA, 500, SERVER, body={'course': cid('fee'), 'subscription_id': sid('luisa')})
add('add-owner-without-association-membership', 'owner_without_association', CA, 500, SERVER,
    body={'course': cid('member'), 'subscription_id': sid('luisa')})


def today(fmt):
    return '{today:' + fmt + '}'


def fee_meta(label, text, **extra):
    return {**extra, 'description': text, 'course_id': cid(label), 'course_title': COURSES[label]['title'],
            'course_type': str(COURSES[label]['course_type']), 'payment_date': today('%Y-%m-%d')}


def link_save(cs_sym, label, sub, pay, association, **memory):
    return entry(CSM, cs_sym, 1, {'payment': ['None', pay], 'membership_fee': ['0.00', '0.0']}, [association, COURSE_INDEX],
                 cs_repr(cs_sym, label, sub, payment=pay, **memory))


# Default course with a fee: the association owner's payment, then the link save.
actor = 'owner'
row = new_cs('@ca1', 'fee', 'luisa', payment='@ca1p')
text = f"Quota corso del {today('%d/%m/%y')} Nuoto"
pay = new_payment('@ca1p', '50.00', user=OWNER, associate=aid('luisa'), meta=fee_meta('fee', text),
                  course_json={'label': 'Nuoto', 'value': cid('fee')})
add('add-course-fee', actor, CA, 201, [overview_item(row, 'luisa')], body={'course': cid('fee'), 'subscription_id': sid('luisa'), 'ignored': 1},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, audit=[
        entry(CSM, '@ca1', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca1', 'fee', 'luisa')),
        created_entry(PAYMENT, '@ca1p', [OWN, DIRECT]), link_save('@ca1', 'fee', 'luisa', '@ca1p', OWN)]))
# One fee: the body's one_fee_payment stays on the row; the payment belongs to the owner.
actor = 'full_collaborator'
row = new_cs('@ca2', 'one', 'mario', payment='@ca2p', one_fee=True)
text = "Pagamento in un'unica soluzione per Tennis"
pay = new_payment('@ca2p', '40.00', user=OWNER, associate=aid('mario'), meta=fee_meta('one', text, one_fee='true'),
                  course_json={'label': 'Tennis', 'value': cid('one')})
add('add-one-fee', actor, CA, 201, [overview_item(row, 'mario')], body={'course': cid('one'), 'subscription_id': sid('mario'), 'one_fee_payment': True},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, audit=[
        entry(CSM, '@ca2', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca2', 'one', 'mario', one_fee=True)),
        created_entry(PAYMENT, '@ca2p', [OWN, DIRECT]), link_save('@ca2', 'one', 'mario', '@ca2p', OWN, one_fee=True)]))


def rata(label, event, cs_sym):
    date = event_date(event['payment_date'])
    text = f"Rata n.{event['id'] + 1} del {date.strftime('%d/%m/%y')} ({COURSES[label]['title']})"
    return text, {'description': text, 'course_id': cid(label), 'course_title': COURSES[label]['title'], 'course_subscription_id': cs_sym,
                  'payment_date': date.isoformat()}


# A multi-payment course: the serializer creates the future events' installments, then the row is flagged.
actor = 'owner'
row = new_cs('@ca3', 'multi', 'luisa', multi=True)
text, meta = rata('multi', EVENTS[1], '@ca3')
pay = new_payment('@ca3p', '30.00', user=OWNER, associate=aid('luisa'), description=text, meta=meta, created='2099-01-10T00:00:00Z')
inst = new_installment('@ca3i', '@ca3', 1, '30.00', '@ca3p', '2099-01-10')
add('add-installment-course', actor, CA, 201, [overview_item(row, 'luisa', installments=[installment_item(inst)])],
    body={'course': cid('multi'), 'subscription_id': sid('luisa')},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay], CSI: [inst]}, audit=[
        entry(CSM, '@ca3', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None'], multi_payments=['None', 'False']),
              [OWN, COURSE_INDEX], cs_repr('@ca3', 'multi', 'luisa')),
        created_entry(PAYMENT, '@ca3p', [OWN, DIRECT]), created_entry(CSI, '@ca3i', [OWN, CSI_INDEX]),
        entry(CSM, '@ca3', 1, {'multi_payments': ['False', 'True'], 'membership_fee': ['0.00', '0.0']}, [OWN, COURSE_INDEX],
              cs_repr('@ca3', 'multi', 'luisa', multi=True))]))
# The body's events: each becomes a payment and an installment (grouped by X-Group-Id).
actor = 'owner'
row = new_cs('@ca4', 'free', 'group')
body_events = [{'id': 2, 'amount': '15', 'payment_date': '01/02/2099'}, {'id': 0, 'amount': 7.5, 'payment_date': '01/03/2099'}]
created_pays, created_insts, audit = [], [], [entry(CSM, '@ca4', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']),
                                                    [OWN, COURSE_INDEX], cs_repr('@ca4', 'free', 'group'))]
for n, (event, amount) in enumerate(zip(body_events, ('15.00', '7.50'))):
    text, meta = rata('free', event, '@ca4')
    date = event_date(event['payment_date']).isoformat()
    created_pays.append(new_payment(f'@ca4p{n}', amount, user=OWNER, associate=aid('luisa'), description=text, meta=meta, created=date + 'T00:00:00Z',
                                    group=OWN_GROUP))
    created_insts.append(new_installment(f'@ca4i{n}', '@ca4', event['id'], amount, f'@ca4p{n}', date, group=OWN_GROUP))
    audit += [created_entry(PAYMENT, f'@ca4p{n}', [OWN, DIRECT]), created_entry(CSI, f'@ca4i{n}', [OWN, CSI_INDEX])]
add('add-body-events-grouped', actor, CA, 201, [overview_item(row, 'group', installments=[installment_item(i) for i in created_insts])],
    headers=GROUP('own_group'), body={'course': cid('free'), 'subscription_id': sid('group'), 'events': body_events},
    result=effect(actor, created={CSM: [row], PAYMENT: created_pays, CSI: created_insts}, audit=audit))
# A multiple-quote course: the quote's amount (a float), category and description.
actor = 'owner'
quote = {'quote_id': 'q1', 'title': 'Mensile', 'amount': '25,50', 'payment_category': {'value': QUOTE_CATEGORY}}
row = new_cs('@ca5', 'quotes', 'luisa', payment='@ca5p', type_=2, quote=quote)
text = 'Quota Mensile a Pilates'
pay = new_payment('@ca5p', '25.50', user=OWNER, associate=aid('luisa'), category=QUOTE_CATEGORY,
                  meta={'multiple_quote': quote, 'description': text, 'course_id': cid('quotes'), 'course_title': 'Pilates', 'course_type': '2'},
                  course_json={'label': 'Pilates', 'value': cid('quotes')})
add('add-quote', actor, CA, 201, [overview_item(row, 'luisa')], body={'course': cid('quotes'), 'subscription_id': sid('luisa'), 'multiple_quote': quote},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, audit=[
        entry(CSM, '@ca5', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca5', 'quotes', 'luisa', type_=2, quote=quote)),
        created_entry(PAYMENT, '@ca5p', [OWN, DIRECT], amount=['None', '25.5']),
        link_save('@ca5', 'quotes', 'luisa', '@ca5p', OWN, type_=2, quote=quote)]))
# A quote category the UUID column rejects is logged and skipped: the default category.
quote = {'quote_id': 'q9', 'title': 'Prova', 'amount': '10', 'payment_category': {'value': 'nope'}}
row = new_cs('@ca13', 'quotes', 'mario', payment='@ca13p', type_=2, quote=quote)
text = 'Quota Prova a Pilates'
pay = new_payment('@ca13p', '10.00', user=OWNER, associate=aid('mario'),
                  meta={'multiple_quote': quote, 'description': text, 'course_id': cid('quotes'), 'course_title': 'Pilates', 'course_type': '2'},
                  course_json={'label': 'Pilates', 'value': cid('quotes')})
add('add-quote-invalid-category', actor, CA, 201, [overview_item(row, 'mario')],
    body={'course': cid('quotes'), 'subscription_id': sid('mario'), 'multiple_quote': quote},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, audit=[
        entry(CSM, '@ca13', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca13', 'quotes', 'mario', type_=2, quote=quote)),
        created_entry(PAYMENT, '@ca13p', [OWN, DIRECT], amount=['None', '10.0']),
        link_save('@ca13', 'quotes', 'mario', '@ca13p', OWN, type_=2, quote=quote)]))
row = new_cs('@ca6', 'quotes', 'mario', type_=2)
add('add-quote-not-selected', actor, CA, 201, [overview_item(row, 'mario')], body={'course': cid('quotes'), 'subscription_id': sid('mario')},
    result=effect(actor, created={CSM: [row]}, audit=[
        entry(CSM, '@ca6', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca6', 'quotes', 'mario', type_=2))]))
# Membership: validate(), then membership_created creates the membership payment for a positive fee.
actor = 'owner'
MEMBER_BODY = {'course': cid('member'), 'subscription_id': sid('luisa'), 'billed_from': '2026-01-01T00:00:00Z', 'billed_until': '2026-02-01',
               'membership_fee': '30', 'billed_frequency': 1, 'auto_renewal': True}
row = new_cs('@ca7', 'member', 'luisa', type_=3, fee='30.00', billed_from='2026-01-01T00:00:00Z', billed_until='2026-02-01T00:00:00Z', renewal=True)
text = 'Abbonamento: Palestra dal 01/01/2026 al 01/02/2026'
pay = new_payment('@ca7p', '30.00', user=OWNER, associate=aid('luisa'), description=text, created='2026-01-01T00:00:00Z',
                  payment_date='2026-01-01T00:00:00Z',
                  meta={'description': text, 'course_id': cid('member'), 'course_title': 'Palestra', 'course_subscription_id': '@ca7',
                        'billed_from': '2026-01-01', 'billed_until': '2026-02-01', 'amount': '30.00'})
add('add-membership', actor, CA, 201, [overview_item(row, 'luisa', payments=[payment_item(pay)])], body=MEMBER_BODY,
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, links={MEMBERSHIP_LINK: {'add': [['@ca7', '@ca7p']]}}, audit=[
        entry(CSM, '@ca7', 0, stored('create', payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca7', 'member', 'luisa', type_=3, fee='30.00', billed_from='2026-01-01T00:00:00Z',
                      billed_until='2026-02-01T00:00:00Z', renewal=True)),
        created_entry(PAYMENT, '@ca7p', [OWN, DIRECT])]))
row = new_cs('@ca8', 'member', 'mario', type_=3, fee='0.00', billed_from='2026-03-01T00:00:00Z', billed_until='2026-04-01T00:00:00Z')
add('add-membership-without-fee', actor, CA, 201, [overview_item(row, 'mario')],
    body={'course': cid('member'), 'subscription_id': sid('mario'), 'billed_from': '2026-03-01T00:00:00Z', 'billed_until': '2026-04-01T00:00:00Z',
          'membership_fee': '0'},
    result=effect(actor, created={CSM: [row]}, audit=[
        entry(CSM, '@ca8', 0, stored('create', payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca8', 'member', 'mario', type_=3, fee='0.00', billed_from='2026-03-01T00:00:00Z',
                      billed_until='2026-04-01T00:00:00Z'))]))
# many=True: one row per item, in order.
actor = 'owner'
first = new_cs('@ca9', 'free', 'luisa')
second = new_cs('@ca10', 'free', 'mario')
add('add-list', actor, CA, 201, [overview_item(first, 'luisa'), overview_item(second, 'mario')],
    body=[{'course': cid('free'), 'subscription_id': sid('luisa')}, {'course': cid('free'), 'subscription_id': sid('mario')}],
    result=effect(actor, created={CSM: [first, second]}, audit=[
        entry(CSM, '@ca9', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca9', 'free', 'luisa')),
        entry(CSM, '@ca10', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca10', 'free', 'mario'))]))
# Athletes in many=True mode: memberships are created without the self-service defaults.
actor = 'athlete'
row = new_cs('@ca11', 'member', 'mario', type_=3)
add('add-athlete-membership-list', actor, CA, 201, [overview_item(row, 'mario')], body=[{'course': cid('member'), 'subscription_id': sid('mario')}],
    result=effect(actor, created={CSM: [row]}, audit=[
        entry(CSM, '@ca11', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [OWN, COURSE_INDEX],
              cs_repr('@ca11', 'member', 'mario', type_=3))]))
add('add-athlete-list-denied', 'athlete', CA, 403, NOT_ASSOCIATION, body=[{'course': cid('free'), 'subscription_id': sid('mario')}])
# The course resolves among the live courses; the payment uses the caller's association.
actor = 'owner'
row = new_cs('@ca12', 'far', 'luisa', payment='@ca12p')
text = f"Quota corso del {today('%d/%m/%y')} Lontano"
pay = new_payment('@ca12p', '15.00', user=OWNER, associate=aid('luisa'), meta=fee_meta('far', text),
                  course_json={'label': 'Lontano', 'value': cid('far')})
add('add-course-of-other-association', actor, CA, 201, [overview_item(row, 'luisa')], body={'course': cid('far'), 'subscription_id': sid('luisa')},
    result=effect(actor, created={CSM: [row], PAYMENT: [pay]}, audit=[
        entry(CSM, '@ca12', 0, stored('create', membership_fee=['None', '0.0'], payment=['None', 'None']), [FOREIGN, COURSE_INDEX],
              cs_repr('@ca12', 'far', 'luisa')),
        created_entry(PAYMENT, '@ca12p', [OWN, DIRECT]), link_save('@ca12', 'far', 'luisa', '@ca12p', FOREIGN)]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course enrolment (course-subscriptions add, course-overview add, installment '
                              'payment) authored sequence with row, link, audit and index effects', 'ids': IDS, 'unknown': UNKNOWN,
                              'category_name': CATEGORY_NAME, **SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
