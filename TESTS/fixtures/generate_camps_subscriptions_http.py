"""Author the camps-and-retreats subscriptions HTTP sequence (camps-subscriptions.md).

Function views in BE/application/views/camp_and_retreats_views.py, each
@api_view with one method, with the helpers of
BE/application/utils/camps_and_retreats_utils.py:
- <uid>/subscriptions/list (GET): is_sport_association (403 {'error'} without
  a period), then is_valid_uuid (400 list), then the camp subscriptions of
  request.user.sport_association (a user without one: the 500 page) and the
  camp, newest first, through CampsAndRetreatsSubscriptionListInfoSerializer:
  the registration through SubscriptionBasicSerializer (Subscription.objects,
  group-aware: a miss is the 500 page), every subscription period with its
  payment (PaymentSerializer), its period (CampsAndRetreatsPeriodInfoSerializer)
  and all of the period's services, the selected periods and services, and the
  periods of any paid live group-aware payment of the registration's associate.
- <uid>/subscriptions/add (POST): no role check; inside one try:
  is_valid_uuid (400 {'error': str(ValidationError)}), request.data.copy()
  with the camp assigned (non-mapping bodies: 500 {'error': str(e)}),
  CampsAndRetreatsSubscriptionInfoSerializer validation (400; the tenant
  scope's PermissionDenied is re-raised), validated_data['subscription'] (a
  KeyError when absent: 500), _validate_camp_enrollment_scope (400 / 403),
  then atomically the subscription, one subscription period per selected
  period with its services, and one unpaid payment for all of them
  (generate_payment_for_periods: metas, notes with it_IT amounts, the float
  amount, Payment.save() defaults); 201 with serializer.data.
- <uid>/subscriptions/update (PATCH): is_valid_uuid (400 list), the role
  check, the row (404) and its camp's association (403; none: 500 {'error'}),
  request.data.get('periods', []), then (not atomic) delete_unpaid_periods,
  add_new_periods on the periods as given and generate_payment_for_periods; any
  exception answers 500 {'error': str(e)} with the earlier writes kept.
- <uid>/subscriptions/delete (DELETE): the same checks, delete_unpaid_periods,
  then the collector delete of the subscription and its remaining (paid)
  subscription periods.
Delete entries are indexed through the payment's association
(changes_json.sport_association) or the actor's own association. Run with
``python3 -I``.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).with_name('camps_subscriptions_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, ATHLETE = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['athlete']
FOREIGN_OWNER = IDS['foreign_owner']
ids = iter(f'a1000000-0000-4000-8000-{986001 + n:012d}' for n in range(90))
SERVER = '<stable-server-error-page>'
UNKNOWN, NEW_SUB = next(ids), next(ids)

FAMILY = next(ids)
ACCOUNT = {'id': next(ids), 'name': 'Banca campi', 'code': 'BC1'}
CATEGORIES = {
    'own': {'id': next(ids), 'name': 'Servizi campo', 'created': '2026-05-01T08:00:00Z', 'association': OWN},
    'foreign': {'id': next(ids), 'name': 'Servizi esteri', 'created': '2026-05-02T08:00:00Z', 'association': FOREIGN},
    # Same name as the default payment category, owned by the other association and keyed
    # below every uuid4 key: camp payments keep the own or global row.
    'foreign_default': {'id': '00000000-0000-4000-8000-000000000501', 'name': 'Entrate e proventi da attività tipiche',
                        'created': '2026-05-02T08:00:00Z', 'association': FOREIGN},
}
ASSOCIATES = {
    'mario': {'id': next(ids), 'association': OWN, 'user': None, 'first': 'Mario', 'last': 'Rossi', 'sex': 'M', 'tax': 'RSSMRA90E10H501Z',
              'born': '1990-05-10', 'email': 'mario@example.test', 'family': None, 'role': None},
    'luisa': {'id': next(ids), 'association': OWN, 'user': ATHLETE, 'first': 'Luisa', 'last': 'Bianchi', 'sex': 'F', 'tax': None,
              'born': '1985-02-02', 'email': None, 'family': FAMILY, 'role': 'madre'},
    'sibling': {'id': next(ids), 'association': OWN, 'user': None, 'first': 'Piero', 'last': 'Bianchi', 'sex': 'M', 'tax': 'BNCPRI10A01H501X',
                'born': '2010-01-01', 'email': None, 'family': FAMILY, 'role': 'figlio'},
    'hans': {'id': next(ids), 'association': FOREIGN, 'user': None, 'first': 'Hans', 'last': 'Muller', 'sex': 'M', 'tax': None,
             'born': '1980-01-01', 'email': None, 'family': None, 'role': None},
}
REGISTRATIONS = {
    'mario': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'user': OWNER, 'start': '2020-09-01', 'end': '2099-08-31',
              'created': '2026-01-10T10:00:00Z', 'archived': False, 'deleted': False, 'group': None},
    'luisa': {'id': next(ids), 'association': OWN, 'associate': 'luisa', 'user': ATHLETE, 'start': '2020-01-01', 'end': '2021-12-31',
              'created': '2026-01-11T10:00:00Z', 'archived': False, 'deleted': False, 'group': None},
    'sibling': {'id': next(ids), 'association': OWN, 'associate': 'sibling', 'user': OWNER, 'start': None, 'end': None,
                'created': '2026-01-12T10:00:00Z', 'archived': False, 'deleted': False, 'group': None},
    'archived': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'user': OWNER, 'start': None, 'end': None,
                 'created': '2026-01-13T10:00:00Z', 'archived': True, 'deleted': False, 'group': None},
    'deleted': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'user': OWNER, 'start': None, 'end': None,
                'created': '2026-01-14T10:00:00Z', 'archived': False, 'deleted': True, 'group': None},
    'grouped': {'id': next(ids), 'association': OWN, 'associate': 'sibling', 'user': OWNER, 'start': None, 'end': None,
                'created': '2026-01-15T10:00:00Z', 'archived': False, 'deleted': False, 'group': 'own_group'},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'associate': 'hans', 'user': FOREIGN_OWNER, 'start': None, 'end': None,
               'created': '2026-01-16T10:00:00Z', 'archived': False, 'deleted': False, 'group': None},
    'orphan': {'id': next(ids), 'association': OWN, 'associate': None, 'user': ATHLETE, 'start': None, 'end': None,
               'created': '2026-01-17T10:00:00Z', 'archived': False, 'deleted': False, 'group': None},
}
CAMPS = {
    'camp': {'id': next(ids), 'title': 'Campo estivo è', 'description': 'Mare e monti', 'association': OWN},
    'plain': {'id': next(ids), 'title': None, 'description': None, 'association': OWN},
    'abroad': {'id': next(ids), 'title': 'Campo estero', 'description': 'Lontano', 'association': FOREIGN},
}
PERIODS = {
    'july': {'id': next(ids), 'camp': 'camp', 'start': '2026-07-01T08:00:00Z', 'end': '2026-07-15T18:00:00Z', 'title': 'Luglio',
             'description': 'Prima settimana', 'fee': '100.00', 'max': 20, 'created': '2026-06-01T10:00:00Z'},
    'august': {'id': next(ids), 'camp': 'camp', 'start': '2026-08-01T00:00:00Z', 'end': '2026-08-10T00:00:00Z', 'title': 'Agosto',
               'description': None, 'fee': '0.00', 'max': None, 'created': '2026-06-02T10:00:00.250000Z'},
    'big': {'id': next(ids), 'camp': 'camp', 'start': '2026-06-15T07:00:00Z', 'end': '2026-09-15T07:00:00Z', 'title': 'Lungo',
            'description': 'Tutta estate', 'fee': '1234.50', 'max': 0, 'created': '2026-06-03T10:00:00Z'},
    'june': {'id': next(ids), 'camp': 'camp', 'start': '2026-06-10T07:00:00Z', 'end': '2026-06-20T07:00:00Z', 'title': 'Giugno',
             'description': 'Breve', 'fee': '55.50', 'max': 5, 'created': '2026-06-04T10:00:00Z'},
    'sept': {'id': next(ids), 'camp': 'plain', 'start': '2026-09-01T07:00:00Z', 'end': '2026-09-05T07:00:00Z', 'title': 'Settembre',
             'description': None, 'fee': '10.00', 'max': 5, 'created': '2026-06-05T10:00:00Z'},
    'abroad': {'id': next(ids), 'camp': 'abroad', 'start': '2026-07-01T07:00:00Z', 'end': '2026-07-02T07:00:00Z', 'title': 'Estero',
               'description': None, 'fee': '1.00', 'max': None, 'created': '2026-06-06T10:00:00Z'},
}
SERVICES = {
    'kayak': {'id': next(ids), 'period': 'july', 'title': 'Kayak', 'description': 'Mezza giornata', 'fee': '12.50', 'category': 'own',
              'created': '2026-06-05T09:00:00Z'},
    'umbrella': {'id': next(ids), 'period': 'july', 'title': 'Ombrellone è', 'description': None, 'fee': '5.00', 'category': 'own',
                 'created': '2026-06-07T09:00:00Z'},
    'bus': {'id': next(ids), 'period': 'july', 'title': 'Bus', 'description': None, 'fee': '30.00', 'category': None,
            'created': '2026-06-06T09:00:00.000001Z'},
    'tent': {'id': next(ids), 'period': 'big', 'title': 'Tenda', 'description': None, 'fee': '7.00', 'category': 'own',
             'created': '2026-06-08T09:00:00Z'},
    'snack': {'id': next(ids), 'period': 'august', 'title': 'Merenda', 'description': None, 'fee': '2.00', 'category': 'own',
              'created': '2026-06-09T09:00:00Z'},
    'guide': {'id': next(ids), 'period': 'sept', 'title': 'Guida', 'description': None, 'fee': '15.00', 'category': 'own',
              'created': '2026-06-10T09:00:00Z'},
    'abroad': {'id': next(ids), 'period': 'abroad', 'title': 'Estero', 'description': None, 'fee': '3.00', 'category': 'foreign',
               'created': '2026-06-11T09:00:00Z'},
}
PERIOD_LINKS = {'july': ['kayak', 'umbrella'], 'big': ['tent']}
CAMP_SUBSCRIPTIONS = {
    'mario': {'id': next(ids), 'camp': 'camp', 'registration': 'mario', 'status': 2, 'created': '2026-06-12T09:00:00Z'},
    'luisa': {'id': next(ids), 'camp': 'camp', 'registration': 'luisa', 'status': 1, 'created': '2026-06-13T09:00:00.500000Z'},
    'grouped': {'id': next(ids), 'camp': 'camp', 'registration': 'grouped', 'status': 3, 'created': '2026-06-10T09:00:00Z'},
    'bus': {'id': next(ids), 'camp': 'camp', 'registration': 'sibling', 'status': 2, 'created': '2026-06-09T09:00:00Z'},
    'nosub': {'id': next(ids), 'camp': 'plain', 'registration': None, 'status': 2, 'created': '2026-06-11T09:00:00Z'},
    'luisa_plain': {'id': next(ids), 'camp': 'plain', 'registration': 'luisa', 'status': 2, 'created': '2026-06-14T09:00:00Z'},
    'abroad': {'id': next(ids), 'camp': 'abroad', 'registration': 'abroad', 'status': 2, 'created': '2026-06-15T09:00:00Z'},
    'extra': {'id': next(ids), 'camp': 'plain', 'registration': 'sibling', 'status': 2, 'created': '2026-06-16T09:00:00Z'},
}


def meta(service, amount):
    row = SERVICES[service]
    return {'payment_category_id': CATEGORIES[row['category']]['id'], 'amount': amount, 'subject': 0, 'title': row['title'],
            'camps_and_retreats_period_service_id': row['id']}


PAYMENTS = {
    'unpaid': {'id': next(ids), 'association': OWN, 'user': OWNER, 'associate': 'mario', 'amount': '1358.50', 'paid': False, 'deleted': False,
               'description': 'Servizi campo', 'category': 'own', 'notes': 'Vecchie note', 'created': '2026-06-12T09:30:00Z',
               'meta': [meta('kayak', 12.5), meta('umbrella', 5.0), meta('tent', 7.0)]},
    'paid': {'id': next(ids), 'association': OWN, 'user': OWNER, 'associate': 'mario', 'amount': '2.00', 'paid': True, 'deleted': False,
             'description': 'Agosto', 'category': 'own', 'notes': None, 'created': '2026-06-12T09:40:00Z', 'meta': [meta('snack', 2.0)]},
    'luisa_deleted': {'id': next(ids), 'association': OWN, 'user': OWNER, 'associate': 'luisa', 'amount': '112.50', 'paid': False,
                      'deleted': True, 'description': None, 'category': None, 'notes': None, 'created': '2026-06-13T09:30:00Z', 'meta': None},
    'luisa_paid': {'id': next(ids), 'association': OWN, 'user': None, 'associate': 'luisa', 'amount': '25.00', 'paid': True, 'deleted': False,
                   'description': 'Settembre', 'category': 'own', 'notes': 'Pagato', 'created': '2026-06-14T09:30:00.000001Z',
                   'meta': []},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'user': FOREIGN_OWNER, 'associate': 'hans', 'amount': '4.00', 'paid': False,
               'deleted': False, 'description': None, 'category': 'foreign', 'notes': None, 'created': '2026-06-15T09:30:00Z', 'meta': None},
}
# A receipt generated for the paid camp payment (payment/<uid>/generate-invoice) and an instructor link (B, legacy):
# PaymentSerializer renders both nested in the list.
PAYMENTS['paid'].update(invoice=True, instructor=True)
INSTRUCTOR = {'id': next(ids), 'first_name': 'Anna è', 'last_name': 'Bianchi', 'email': 'anna@example.test',
              'tax_code': 'BNCNNA80A41H501X', 'default_hourly_billing': '12.50'}
INVOICE = {'id': next(ids), 'created': '2026-06-14T10:00:00Z', 'membership_fee': '0.00', 'activity_fee': '25.00',
           'description': 'Ricevuta campo è', 'number': 3, 'meta': {'numero': 3}}
SUBPERIODS = {
    'm_july': {'id': next(ids), 'cs': 'mario', 'period': 'july', 'payment': 'unpaid', 'services': ['kayak', 'umbrella']},
    'm_big': {'id': next(ids), 'cs': 'mario', 'period': 'big', 'payment': 'unpaid', 'services': ['tent']},
    'm_aug': {'id': next(ids), 'cs': 'mario', 'period': 'august', 'payment': 'paid', 'services': ['snack']},
    'm_june': {'id': next(ids), 'cs': 'mario', 'period': 'june', 'payment': None, 'services': []},
    'l_july': {'id': next(ids), 'cs': 'luisa', 'period': 'july', 'payment': 'luisa_deleted', 'services': ['kayak']},
    'g_aug': {'id': next(ids), 'cs': 'grouped', 'period': 'august', 'payment': None, 'services': []},
    'n_sept': {'id': next(ids), 'cs': 'nosub', 'period': 'sept', 'payment': None, 'services': ['guide']},
    'lp_sept': {'id': next(ids), 'cs': 'luisa_plain', 'period': 'sept', 'payment': 'luisa_paid', 'services': ['guide']},
    'a_abroad': {'id': next(ids), 'cs': 'abroad', 'period': 'abroad', 'payment': 'abroad', 'services': ['abroad']},
}

CSUB, SUBPERIOD, PAYMENT = 'CampsAndRetreatsSubscription', 'CampsAndRetreatsSubscriptionPeriod', 'Payment'
CS_PATH, SP_PATH = 'camps_and_retreats.sport_association', 'subscription.camps_and_retreats.sport_association'
ACTOR_PATH, CHANGES_PATH = 'actor.sport_association', 'changes_json.sport_association'
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'camps-subscriptions-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)


def drf(text):
    """DRF DateTimeField: UTC isoformat with 'Z', microseconds only when set."""
    return moment(text).isoformat().replace('+00:00', 'Z')


def naive(text):
    """auditlog's str() of the naive UTC datetime."""
    return str(moment(text).replace(tzinfo=None))


def repr_detail(errors):
    """str(ValidationError(serializer.errors)): the dict repr of ErrorDetail lists."""
    return '{' + ', '.join(f'{field!r}: [' + ', '.join(f'ErrorDetail(string={message!r}, code={code!r})' for message, code in items) + ']'
                           for field, items in errors) + '}'


def repr_single(field, message, code='invalid'):
    """str(ValidationError({field: 'text'})): the dict value stays one ErrorDetail."""
    return '{' + f'{field!r}: ErrorDetail(string={message!r}, code={code!r})' + '}'


def repr_list(message, code):
    """str(ValidationError) of a field's run_validation: a list of one ErrorDetail."""
    return f'[ErrorDetail(string={message!r}, code={code!r})]'


def it_amount(value):
    """locale.format_string('%.2f', value, grouping=True) under it_IT.UTF-8."""
    whole, fraction = f'{float(value):.2f}'.split('.')
    sign = '-' if whole.startswith('-') else ''
    whole = whole.lstrip('-')
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + '.'.join(groups) + ',' + fraction


NULL, REQUIRED = ('This field may not be null.', 'null'), ('This field is required.', 'required')
UUID_INVALID = ('Must be a valid UUID.', 'invalid')
DATETIME = ('Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].', 'invalid')
missing_pk = lambda pk: (f'Invalid pk "{pk}" - object does not exist.', 'does_not_exist')
bad_uuid = lambda value: (f'“{value}” is not a valid UUID.', 'invalid')
choice = lambda value: (f'"{value}" is not a valid choice.', 'invalid_choice')
INCORRECT_BOOL = ('Incorrect type. Expected pk value, received bool.', 'incorrect_type')
UNIQUE = ('camps and retreats subscription with this camps and retreats subscription id already exists.', 'unique')
TENANT = {'detail': 'You do not have permission to perform this action.'}
OPTIONS = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}
missing = lambda verb: {'detail': f'Missing permission: association.campsandretreats.{verb}'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
NO_SESSION = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
BADLY_FORMED = ['badly formed hexadecimal UUID string']
NOT_ASSOCIATION = {'error': 'You are not a sport association.'}
NOT_FOUND = {'error': 'Not found'}
NO_ASSOCIATION = {'error': 'SportAssociation matching query does not exist.'}
FORM = 'application/x-www-form-urlencoded'
OWNER_USER = {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
              'username': 'message-reads-700001', 'role': 1}
GROUP = {'X-Group-Id': IDS['own_group']}


def path(label, verb):
    if label in CAMPS:
        label = CAMPS[label]['id']
    elif label in CAMP_SUBSCRIPTIONS:
        label = CAMP_SUBSCRIPTIONS[label]['id']
    return f'/camps-and-retreats/{label}/subscriptions/{verb}'


pid = lambda label: PERIODS[label]['id']
sid = lambda label: SERVICES[label]['id']
rid = lambda label: REGISTRATIONS[label]['id']
csid = lambda label: CAMP_SUBSCRIPTIONS[label]['id']
spid = lambda label: SUBPERIODS[label]['id']
payid = lambda label: PAYMENTS[label]['id']


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def unordered(values):
    return {'$unordered': values}


def bag(values, by):
    return {'$bag': values, '$by': by}


def category_info(label):
    if label is None:
        # PaymentCategorySerializer(None).data is get_initial().
        return {'payment_category_id': None, 'name': '', 'creation_date': None, 'sport_association': None, 'expense': False,
                'tax_deductible': False, 'archived': False, 'type': None,
                'vat_management': {'vat_management_id': None, 'vat': None, 'deduction': None}, 'deleted': False}
    row = CATEGORIES[label]
    return {'payment_category_id': row['id'], 'name': row['name'], 'creation_date': drf(row['created']), 'sport_association': row['association'],
            'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1, 'vat_management': None, 'deleted': False}


def comma(fee):
    return fee.replace('.', ',')


def service_info(label):
    """CampsAndRetreatsPeriodsServiceSerializer."""
    row = SERVICES[label]
    return {'camps_and_retreats_period_service_id': row['id'], 'payment_category_info': category_info(row['category']),
            'fee': comma(row['fee']), 'title': row['title'], 'description': row['description'], 'created_at': drf(row['created']),
            'camps_and_retreats_period': pid(row['period']),
            'payment_category': CATEGORIES[row['category']]['id'] if row['category'] else None}


def period_info(label):
    """CampsAndRetreatsPeriodInfoSerializer: services newest first."""
    row = PERIODS[label]
    services = sorted((s for s, value in SERVICES.items() if value['period'] == label), key=lambda s: SERVICES[s]['created'], reverse=True)
    return {'camps_and_retreats_period_id': row['id'], 'start_date': moment(row['start']).strftime('%d/%m/%Y'),
            'end_date': moment(row['end']).strftime('%d/%m/%Y'), 'title': row['title'], 'description': row['description'],
            'fee': comma(row['fee']), 'max_participants': row['max'], 'created_at': drf(row['created']),
            'camps_and_retreats': CAMPS[row['camp']]['id'],
            'camps_and_Retreats_period_services': unordered([sid(s) for s in PERIOD_LINKS.get(label, [])]),
            'services': [service_info(s) for s in services]}


def associate_full(label):
    """AssociateSerializer: user is the associate's user, else its association's owner."""
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None, 'born_date': row['born'],
            'email': row['email'], 'sex': row['sex'], 'first_name': row['first'], 'last_name': row['last'], 'is_minor': False,
            'sport_association': row['association'], 'tax_code': row['tax'],
            'user': row['user'] or (OWNER if row['association'] == OWN else FOREIGN_OWNER), 'phone': None, 'family': row['family'],
            'family_role': row['role']}


def payment_subscription(row):
    """PaymentSerializer.get_subscription_id for subject 0: the oldest non-archived registration of the associate."""
    candidates = [r for r in REGISTRATIONS.values() if r['associate'] == row['associate'] and not r['archived']]
    return min(candidates, key=lambda r: r['created'])['id'] if candidates else None


def instructor_item():
    row = INSTRUCTOR
    return {'instructor_id': row['id'], 'user': OWNER, 'first_name': row['first_name'], 'last_name': row['last_name'], 'email': row['email'],
            'born_date': None, 'born_city': None, 'born_province': None, 'tax_code': row['tax_code'], 'address_city': None, 'address': None,
            'address_province': None, 'civic_number': None, 'study_title': None, 'role': 'Istruttore', 'phone': None, 'phone_2': None,
            'phone_3': None, 'phone_4': None, 'draft': False, 'associated_user_id': None, 'is_volunteer': False, 'stipulated_contract_in': None,
            'default_hourly_billing': row['default_hourly_billing'], 'default_percentage_billing': '0.00', 'documents': []}


def invoice_item():
    row = INVOICE
    return {'invoice_id': row['id'], 'document_token': None, 'creation_date': row['created'], 'membership_fee': row['membership_fee'],
            'activity_fee': row['activity_fee'], 'meta_payment_categories': None, 'description': row['description'], 'number': row['number'],
            'archived': False, 'cancelled': False, 'meta': row['meta'], 'imported_from_associami': False, 'deleted': False, 'document_pdf': None,
            'sport_association': OWN, 'selected_tutor': None, 'group': None, 'attached_documents': []}


def payment_info(label):
    row = PAYMENTS[label]
    category = row['category']
    return {'payment_id': row['id'], 'user': OWNER_USER if row['user'] == OWNER else None,
            'associate': associate_full(row['associate']), 'subscription_id': payment_subscription(row),
            'instructor_id': INSTRUCTOR['id'] if row.get('instructor') else None,
            'instructor': instructor_item() if row.get('instructor') else None, 'supplier': None, 'supplier_id': None,
            'custom_account_type': None, 'custom_account_name': None, 'invoice': invoice_item() if row.get('invoice') else None,
            'attachments': [],
            'payment_category_name': CATEGORIES[category]['name'] if category else None,
            'type': 'default', 'subject': 0, 'amount': row['amount'], 'creation_date': drf(row['created']),
            'payment_date': None, 'paid': row['paid'], 'expense': False, 'description': row['description'],
            'meta_payment_categories': row['meta'], 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
            'notes': row['notes'], 'deleted': row['deleted'], 'course': None, 'payment_category': CATEGORIES[category]['id'] if category else None,
            'sport_association': row['association'], 'custom_accounts': None, 'signature': None, 'group': None}


def subscription_basic(label):
    """SubscriptionBasicSerializer: current_year is is_current (the authored dates are far from today)."""
    row = REGISTRATIONS[label]
    associate = ASSOCIATES[row['associate']]
    current = row['start'] is None or row['end'] is None or row['start'] <= '2026-11-07' <= row['end']
    return {'subscription_id': row['id'], 'current_year': current,
            'associate': {'associate_id': associate['id'], 'first_name': associate['first'], 'last_name': associate['last'],
                          'tax_code': associate['tax'], 'family': associate['family'], 'family_role': associate['role']}}


def list_item(label):
    """CampsAndRetreatsSubscriptionListInfoSerializer."""
    row = CAMP_SUBSCRIPTIONS[label]
    subperiods = [s for s, value in SUBPERIODS.items() if value['cs'] == label]
    associate = REGISTRATIONS[row['registration']]['associate']
    paid = [s for s, value in SUBPERIODS.items() if value['payment'] and PAYMENTS[value['payment']]['paid']
            and not PAYMENTS[value['payment']]['deleted'] and PAYMENTS[value['payment']]['associate'] == associate]
    return {'camps_and_retreats_subscription_id': row['id'], 'subscription': subscription_basic(row['registration']),
            'periods': bag([{'camps_and_retreats_subscription_period_id': spid(s),
                             'payment': payment_info(SUBPERIODS[s]['payment']) if SUBPERIODS[s]['payment'] else None,
                             'camps_and_retreats_period': period_info(SUBPERIODS[s]['period']),
                             'camps_and_retreats_period_services': bag([service_info(v) for v, value in SERVICES.items()
                                                                        if value['period'] == SUBPERIODS[s]['period']],
                                                                       'camps_and_retreats_period_service_id'),
                             'camps_and_retreats_subscription': row['id']} for s in subperiods], 'camps_and_retreats_subscription_period_id'),
            'selected_periods': {pid(SUBPERIODS[s]['period']): True for s in subperiods},
            'selected_services': {pid(SUBPERIODS[s]['period']): {sid(v): True for v in SUBPERIODS[s]['services']} for s in subperiods},
            'paid_periods': {pid(SUBPERIODS[s]['period']): True for s in paid},
            'status_flag': row['status'], 'created_at': drf(row['created']), 'camps_and_retreats': CAMPS[row['camp']]['id']}


def listing(*labels):
    return {'data': [list_item(label) for label in labels]}


def audit(model, key, action, changes, association=None, path=None):
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'association': association, 'path': path}


# ---------------------------------------------------------------------------
# Payment generation (generate_payment_for_periods)
# ---------------------------------------------------------------------------

def service_meta(label):
    """get_meta_payment_categories: one item per selected service."""
    row = SERVICES[label]
    return {'payment_category_id': CATEGORIES[row['category']]['id'], 'amount': float(row['fee']), 'subject': 0, 'title': row['title'],
            'camps_and_retreats_period_service_id': row['id']}


def generated(camp, selected):
    """metas, notes and the float amount for [(period, [services])] in database order."""
    metas, notes, amount = [], '', 0
    title = CAMPS[camp]['title']
    for period, services in selected:
        row = PERIODS[period]
        items = [service_meta(s) for s in services]
        metas.extend(items)
        text = f'Iscrizione al periodo {row["title"]} del campo {title}'
        if row['description']:
            text += f'\nQuota di iscrizione: {it_amount(row["fee"])}€\n'
        for item in items:
            text += f'\n{item["title"]}: {it_amount(item["amount"])}€'
        notes += text + '\n\n'
        amount += sum(item['amount'] for item in items) + float(row['fee'])
    return metas, notes, amount


def stored_amount(value):
    return f'{value:.2f}'


def created_payment(camp, registration, selected, group=None):
    """The created payment's row and auditlog create changes."""
    metas, notes, amount = generated(camp, selected)
    associate = ASSOCIATES[REGISTRATIONS[registration]['associate']]['id']
    row = {'type': 'default', 'subject': 0, 'amount': stored_amount(amount), 'payment_date': None, 'paid': False, 'expense': False,
           'description': '$default_category_name', 'payment_category_id': '$default_category', 'meta_payment_categories': metas,
           'invoice_id': None, 'user_id': OWNER, 'associate_id': associate, 'supplier_id': None, 'instructor_id': None,
           'sport_association_id': OWN, 'payment_intent_id': None, 'archived': False, 'invoice_fiscal_code': None, 'meta': None,
           'notes': notes, 'custom_accounts_id': ACCOUNT['id'], 'signature_id': None, 'deleted': False,
           'group_id': IDS[group] if group else None, 'course': None}
    changes = {'payment_id': ['None', '$payment'], 'type': ['None', 'default'], 'subject': ['None', '0'], 'amount': ['None', repr(amount)],
               'creation_date': ['None', '$created'], 'payment_date': ['None', 'None'], 'paid': ['None', 'False'], 'expense': ['None', 'False'],
               'description': ['None', '$default_category_name'], 'payment_category': ['None', '$default_category'],
               'meta_payment_categories': ['None', json.dumps(metas, sort_keys=True)], 'invoice': ['None', 'None'], 'user': ['None', OWNER],
               'associate': ['None', associate], 'supplier': ['None', 'None'], 'instructor': ['None', 'None'],
               'sport_association': ['None', OWN], 'archived': ['None', 'False'], 'meta': ['None', 'null'], 'notes': ['None', notes],
               'custom_accounts': ['None', ACCOUNT['id']], 'signature': ['None', 'None'], 'deleted': ['None', 'False'],
               'group': ['None', IDS[group] if group else 'None'], 'course': ['None', 'null']}
    return row, changes


def subperiod_created(period, subscription):
    return audit(SUBPERIOD, f'$sp:{pid(period)}', 0, {'camps_and_retreats_subscription_period_id': ['None', f'$sp:{pid(period)}'],
                                                     'camps_and_retreats_period': ['None', pid(period)],
                                                     'camps_and_retreats_subscription': ['None', subscription], 'payment': ['None', 'None']},
                 OWN, SP_PATH)


def subperiod_paid(period):
    return audit(SUBPERIOD, f'$sp:{pid(period)}', 1, {'payment': ['None', '$payment']}, OWN, SP_PATH)


def effect(actor, *, created_sub=None, created_sps=(), created_payment_row=None, deleted_subs=(), deleted_sps=(), deleted_payments=(),
           events=()):
    return {'actor': actor, 'subscription': {'created': created_sub, 'deleted': list(deleted_subs)},
            'subperiods': {'created': list(created_sps), 'deleted': list(deleted_sps)},
            'payments': {'created': created_payment_row, 'deleted': list(deleted_payments)}, 'audit': list(events)}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
add('list-anonymous', 'anonymous', path('camp', 'list'), 401, ANONYMOUS)
add('list-admin-without-session', 'admin', path('camp', 'list'), 403, NO_SESSION)
add('list-restricted-missing-permission', 'restricted_collaborator', path('camp', 'list'), 403, missing('read'))
add('list-owner-scoped-athlete-denied', 'owner', path('camp', 'list'), 403, SCOPED_DENIED, headers=SCOPED)
add('list-wrong-method', 'owner', path('camp', 'list'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('list-options', 'owner', path('camp', 'list'), 200, OPTIONS('Camps And Retreats Subscriptions List'), method='OPTIONS')
add('list-athlete-before-uuid', 'athlete', path('abc', 'list'), 403, {'error': 'You are not a sport association'})
add('list-malformed', 'owner', path('abc', 'list'), 400, BADLY_FORMED)
add('list-periods-segment-malformed', 'owner', path('periods', 'list'), 400, BADLY_FORMED)
add('list-unknown-camp-empty', 'owner', path(UNKNOWN, 'list'), 200, {'data': []})
add('list-foreign-camp-empty', 'owner', path('abroad', 'list'), 200, {'data': []})
add('list-unassociated-owner-server-error', 'owner_without_association', path('camp', 'list'), 500, SERVER)
add('list-owner', 'owner', path('camp', 'list'), 200, listing('luisa', 'mario', 'grouped', 'bus'))
add('list-collaborator-hex-uid', 'full_collaborator', path(CAMPS['camp']['id'].replace('-', '').upper(), 'list'), 200,
    listing('luisa', 'mario', 'grouped', 'bus'))
add('list-malformed-group-ignored', 'owner', path('camp', 'list'), 200, listing('luisa', 'mario', 'grouped', 'bus'),
    headers={'X-Group-Id': 'abc'})
add('list-group-hides-registration-server-error', 'owner', path('camp', 'list'), 500, SERVER, headers=GROUP)
add('list-missing-registration-server-error', 'owner', path('plain', 'list'), 500, SERVER)

# ---------------------------------------------------------------------------
# add (validation, no writes)
# ---------------------------------------------------------------------------
A = lambda label: path(label, 'add')
add('add-anonymous', 'anonymous', A('camp'), 401, ANONYMOUS, method='POST', body={})
add('add-admin-without-session', 'admin', A('camp'), 403, NO_SESSION, method='POST', body={})
add('add-restricted-missing-permission', 'restricted_collaborator', A('camp'), 403, missing('create'), method='POST', body={})
add('add-owner-scoped-athlete-denied', 'owner', A('camp'), 403, SCOPED_DENIED, method='POST', body={}, headers=SCOPED)
add('add-wrong-method', 'owner', A('camp'), 405, {'detail': 'Method "GET" not allowed.'})
add('add-options', 'owner', A('camp'), 200, OPTIONS('Camps And Retreats Subscriptions Add'), method='OPTIONS')
add('add-malformed-athlete', 'athlete', A('abc'), 400, {'error': repr_list('badly formed hexadecimal UUID string', 'invalid')},
    method='POST', body={})
add('add-list-body-server-error', 'owner', A('camp'), 500, {'error': 'list indices must be integers or slices, not str'}, method='POST',
    body=[])
add('add-string-body-server-error', 'owner', A('camp'), 500, {'error': "'str' object has no attribute 'copy'"}, method='POST', body='x')
add('add-number-body-server-error', 'owner', A('camp'), 500, {'error': "'int' object has no attribute 'copy'"}, method='POST', body=5)
add('add-null-body-server-error', 'owner', A('camp'), 500, {'error': "'NoneType' object has no attribute 'copy'"}, method='POST',
    raw_body='null', content_type='application/json')
add('add-invalid-fields', 'owner', A(UNKNOWN), 400, {'error': repr_detail([
    ('camps_and_retreats_subscription_id', [UUID_INVALID]), ('status_flag', [choice(9)]), ('created_at', [DATETIME]),
    ('camps_and_retreats', [missing_pk(UNKNOWN)]), ('subscription', [bad_uuid('abc')])])},
    method='POST', body={'camps_and_retreats_subscription_id': 'abc', 'status_flag': 9, 'created_at': 'ieri', 'subscription': 'abc'})
add('add-invalid-types', 'owner', A('camp'), 400, {'error': repr_detail([
    ('camps_and_retreats_subscription_id', [UNIQUE]), ('status_flag', [choice(True)]), ('created_at', [NULL]),
    ('subscription', [INCORRECT_BOOL])])},
    method='POST', body={'camps_and_retreats_subscription_id': csid('mario'), 'status_flag': True, 'created_at': None, 'subscription': True,
                         'camps_and_retreats': CAMPS['abroad']['id']})
add('add-null-status-unknown-registration', 'owner', A('camp'), 400, {'error': repr_detail([
    ('status_flag', [NULL]), ('subscription', [missing_pk(UNKNOWN)])])}, method='POST', body={'status_flag': None, 'subscription': UNKNOWN})
add('add-group-hides-registration', 'owner', A('camp'), 400, {'error': repr_detail([('subscription', [missing_pk(rid('mario'))])])},
    method='POST', body={'subscription': rid('mario')}, headers=GROUP)
add('add-foreign-camp-tenant-denied', 'owner', A('abroad'), 403, TENANT, method='POST', body={'subscription': rid('mario')})
add('add-foreign-registration-tenant-denied', 'owner', A('camp'), 403, TENANT, method='POST', body={'subscription': rid('abroad')})
add('add-missing-registration-key-server-error', 'owner', A('camp'), 500, {'error': "'subscription'"}, method='POST',
    body={'periods': []})
add('add-null-registration', 'owner', A('camp'), 400, {'error': repr_single('subscription', 'An existing registration is required.')},
    method='POST', body={'subscription': None})
add('add-form-blank-registration', 'owner', A('camp'), 400, {'error': repr_single('subscription', 'An existing registration is required.')},
    method='POST', raw_body='subscription=&status_flag=', content_type=FORM)
REG_DENIED = {'detail': 'The registration does not belong to this camp association.'}
add('add-archived-registration-denied', 'owner', A('camp'), 403, REG_DENIED, method='POST', body={'subscription': rid('archived')})
add('add-deleted-registration-denied', 'full_collaborator', A('camp'), 403, REG_DENIED, method='POST', body={'subscription': rid('deleted')})
add('add-collaborator-foreign-registration-tenant-denied', 'full_collaborator', A('camp'), 403, TENANT, method='POST',
    body={'subscription': rid('abroad')})
add('add-athlete-foreign-registration-denied', 'athlete', A('camp'), 403, REG_DENIED, method='POST', body={'subscription': rid('abroad')})
add('add-athlete-foreign-camp-registration-denied', 'athlete', A('abroad'), 403, {'detail': 'Registration not available to this account.'},
    method='POST', body={'subscription': rid('abroad')})
add('add-unassociated-owner-denied', 'owner_without_association', A('camp'), 403, {'detail': 'Camp not available to this association.'},
    method='POST', body={'subscription': rid('mario')})
add('add-athlete-registration-denied', 'athlete', A('camp'), 403, {'detail': 'Registration not available to this account.'},
    method='POST', body={'subscription': rid('mario')})
add('add-athlete-registration-without-associate-server-error', 'athlete', A('camp'), 500,
    {'error': "'NoneType' object has no attribute 'family_id'"}, method='POST', body={'subscription': rid('orphan')})
add('add-periods-not-list', 'owner', A('camp'), 400, {'error': repr_single('periods', 'A list of periods is required.')}, method='POST',
    body={'subscription': rid('mario'), 'periods': {'camps_and_retreats_period': pid('july')}})
add('add-form-periods-not-list', 'owner', A('camp'), 400, {'error': repr_single('periods', 'A list of periods is required.')},
    method='POST', raw_body=f'subscription={rid("mario")}&periods={pid("july")}', content_type=FORM)
add('add-period-not-object', 'owner', A('camp'), 400, {'error': repr_single('periods', 'Each selected period must be an object.')},
    method='POST', body={'subscription': rid('mario'), 'periods': [pid('july')]})
add('add-period-missing-id', 'owner', A('camp'), 400, {'error': repr_list(*NULL)}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'services': []}]})
add('add-period-invalid-id', 'owner', A('camp'), 400, {'error': repr_list(*UUID_INVALID)}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': 1.5}]})
add('add-period-duplicate', 'owner', A('camp'), 400, {'error': repr_single('periods', 'A period can be selected only once.')}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july')},
                                                    {'camps_and_retreats_period': pid('july').replace('-', '').upper()}]})
add('add-period-other-camp-denied', 'owner', A('camp'), 403, {'detail': 'Period does not belong to the selected camp.'}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july')}, {'camps_and_retreats_period': pid('sept')}]})
add('add-period-integer-id-denied', 'owner', A('camp'), 403, {'detail': 'Period does not belong to the selected camp.'}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': True}]})
add('add-services-not-list', 'owner', A('camp'), 400, {'error': repr_single('services', 'A list of services is required.')}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'), 'services': None}]})
add('add-service-not-object', 'owner', A('camp'), 400, {'error': repr_single('services', 'Each selected service must be an object.')},
    method='POST', body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'), 'services': [sid('kayak')]}]})
add('add-service-invalid-id', 'owner', A('camp'), 400, {'error': repr_list(*UUID_INVALID)}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'),
                                                     'services': [{'camps_and_retreats_period_service_id': 'kayak'}]}]})
add('add-service-duplicate', 'owner', A('camp'), 400, {'error': repr_single('services', 'A service can be selected only once.')},
    method='POST', body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'), 'services': [
        {'camps_and_retreats_period_service_id': sid('kayak')}, {'camps_and_retreats_period_service_id': '{' + sid('kayak') + '}'}]}]})
add('add-service-other-period-denied', 'owner', A('camp'), 403, {'detail': 'Service does not belong to the selected period.'},
    method='POST', body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'),
                                                                    'services': [{'camps_and_retreats_period_service_id': sid('tent')}]}]})
add('add-service-without-category-server-error', 'owner', A('camp'), 500,
    {'error': "'NoneType' object has no attribute 'payment_category_id'"}, method='POST',
    body={'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('july'),
                                                     'services': [{'camps_and_retreats_period_service_id': sid('bus')}]}]})

# ---------------------------------------------------------------------------
# add (writes)
# ---------------------------------------------------------------------------


def created(name, actor, camp, registration, selected, body, *, status=2, explicit_id=None, created_at=None, group=None, form=None):
    """One add: the subscription, one subscription period per selected period, one payment when any."""
    sub_key = explicit_id or '$sub'
    payment_row = changes = None
    if selected:
        payment_row, changes = created_payment(camp, registration, selected, group)
    sub_row = {'camps_and_retreats_id': CAMPS[camp]['id'], 'status_flag': status, 'subscription_id': rid(registration),
               'created_at': drf(created_at) if created_at else '$now'}
    if explicit_id:
        sub_row['camps_and_retreats_subscription_id'] = explicit_id
    sub_changes = {'camps_and_retreats_subscription_id': ['None', sub_key], 'camps_and_retreats': ['None', CAMPS[camp]['id']],
                   'status_flag': ['None', str(status)], 'subscription': ['None', rid(registration)],
                   'created_at': ['None', naive(created_at) if created_at else '$created']}
    events = [audit(CSUB, sub_key, 0, sub_changes, OWN, CS_PATH)]
    sps = []
    for period, services in selected:
        sps.append({'period': pid(period), 'subscription': sub_key, 'payment': '$payment', 'services': [sid(s) for s in services]})
        events.append(subperiod_created(period, sub_key))
    if selected:
        events.append(audit(PAYMENT, '$payment', 0, changes, OWN, 'direct'))
        events.extend(subperiod_paid(period) for period, _ in selected)
    response = {'message': 'Added successfully', 'subscription': {
        'camps_and_retreats_subscription_id': '$bind:sub', 'status_flag': status, 'created_at': drf(created_at) if created_at else '$now',
        'camps_and_retreats': CAMPS[camp]['id'], 'subscription': rid(registration),
        'periods': bag([{'camps_and_retreats_subscription_period_id': f'$bind:sp:{pid(period)}', 'camps_and_retreats_period': pid(period),
                         'camps_and_retreats_period_services': unordered([sid(s) for s in services]),
                         'camps_and_retreats_subscription': '$bind:sub', 'payment': '$bind:payment'} for period, services in selected],
                       'camps_and_retreats_period')}}
    if explicit_id:
        response['subscription']['camps_and_retreats_subscription_id'] = explicit_id
        for item in response['subscription']['periods']['$bag']:
            item['camps_and_retreats_subscription'] = explicit_id
    result = effect(actor, created_sub=sub_row, created_sps=sps, created_payment_row=payment_row, events=events)
    if form:
        add(name, actor, A(camp), 201, response, method='POST', raw_body=form, content_type=FORM, headers=GROUP if group else None,
            effect=result)
    else:
        add(name, actor, A(camp), 201, response, method='POST', body=body, headers=GROUP if group else None, effect=result)


period_body = lambda period, *services: {'camps_and_retreats_period': pid(period),
                                         'services': [{'camps_and_retreats_period_service_id': sid(s)} for s in services]}
created('add-owner-two-periods', 'owner', 'camp', 'mario', [('july', ['kayak', 'umbrella']), ('big', ['tent'])],
        {'subscription': rid('mario'), 'status_flag': 1, 'ignored': True,
         'periods': [period_body('july', 'kayak', 'umbrella'),
                     {'camps_and_retreats_period': pid('big').replace('-', '').upper(),
                      'services': [{'camps_and_retreats_period_service_id': sid('tent').upper()}]}]}, status=1)
created('add-owner-without-periods', 'owner', 'plain', 'sibling', [], {'subscription': rid('sibling'), 'camps_and_retreats': UNKNOWN})
created('add-collaborator-explicit-values', 'full_collaborator', 'camp', 'mario', [('august', [])],
        {'camps_and_retreats_subscription_id': NEW_SUB, 'created_at': '2026-05-01T10:00:00.123456+02:00', 'status_flag': '3',
         'subscription': rid('mario'), 'periods': [{'camps_and_retreats_period': pid('august')}]},
        status=3, explicit_id=NEW_SUB, created_at='2026-05-01T08:00:00.123456Z')
created('add-athlete-own-registration', 'athlete', 'camp', 'luisa', [('june', [])],
        {'subscription': rid('luisa'), 'periods': [period_body('june')]})
created('add-athlete-family-registration', 'athlete', 'camp', 'sibling', [], {'subscription': rid('sibling'), 'periods': []})
created('add-owner-group-payment', 'owner', 'camp', 'grouped', [('july', ['kayak'])],
        {'subscription': rid('grouped'), 'periods': [period_body('july', 'kayak')]}, group='own_group')
created('add-owner-form', 'owner', 'plain', 'mario', [], None, form=f'subscription={rid("mario")}&status_flag=2')
add('add-existing-explicit-id', 'owner', A('camp'), 400, {'error': repr_detail([('camps_and_retreats_subscription_id', [UNIQUE])])},
    method='POST', body={'camps_and_retreats_subscription_id': NEW_SUB, 'subscription': rid('mario')})

# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
U = lambda label: path(csid(label) if label in CAMP_SUBSCRIPTIONS else label, 'update')
OLD_SP = lambda cs, period: f'@sp:{csid(cs)}:{pid(period)}'
OLD_PAY = lambda cs, period: f'@pay:{csid(cs)}:{pid(period)}'
UPDATED = {'message': 'Updated successfully'}
add('update-anonymous', 'anonymous', U('mario'), 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', U('mario'), 403, missing('update'), method='PATCH', body={})
add('update-owner-scoped-athlete-denied', 'owner', U('mario'), 403, SCOPED_DENIED, method='PATCH', body={}, headers=SCOPED)
add('update-wrong-method', 'owner', U('mario'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('update-options', 'owner', U('mario'), 200, OPTIONS('Camps And Retreats Subscriptions Update'), method='OPTIONS')
add('update-malformed-athlete', 'athlete', U('abc'), 400, BADLY_FORMED, method='PATCH', body={})
add('update-athlete-forbidden', 'athlete', U('mario'), 403, NOT_ASSOCIATION, method='PATCH', body={})
add('update-unknown', 'owner', U(UNKNOWN), 404, NOT_FOUND, method='PATCH', body={})
add('update-foreign-forbidden', 'owner', U('abroad'), 403, {'detail': 'You are not allowed deleting this.'}, method='PATCH', body={})
add('update-unassociated-owner-server-error', 'owner_without_association', U('mario'), 500, NO_ASSOCIATION, method='PATCH', body={})
add('update-list-body-server-error', 'owner', U('mario'), 500, {'error': "'list' object has no attribute 'get'"}, method='PATCH', body=[])
add('update-string-body-server-error', 'owner', U('mario'), 500, {'error': "'str' object has no attribute 'get'"}, method='PATCH',
    body='periods')
add('update-null-body-server-error', 'owner', U('mario'), 500, {'error': "'NoneType' object has no attribute 'get'"}, method='PATCH',
    raw_body='null', content_type='application/json')
add('update-unknown-period-server-error', 'owner', U('luisa_plain'), 500, {'error': 'CampsAndRetreatsPeriod matching query does not exist.'},
    method='PATCH', body={'periods': [{'camps_and_retreats_period': UNKNOWN}]})
add('update-invalid-period-server-error', 'owner', U('luisa_plain'), 500, {'error': "['“abc” is not a valid UUID.']"}, method='PATCH',
    body={'periods': [{'camps_and_retreats_period': 'abc'}]})
add('update-period-missing-key-server-error', 'owner', U('luisa_plain'), 500, {'error': "'camps_and_retreats_period'"}, method='PATCH',
    body={'periods': [{'services': []}]})
add('update-period-not-iterable-server-error', 'owner', U('luisa_plain'), 500, {'error': "'int' object is not iterable"}, method='PATCH',
    body={'periods': 5})
add('update-paid-period-skipped', 'owner', U('luisa_plain'), 200, UPDATED, method='PATCH',
    body={'periods': [{'camps_and_retreats_period': pid('sept'), 'services': [{'camps_and_retreats_period_service_id': sid('guide')}]}]},
    effect=effect('owner'))

# Unpaid periods go (the shared payment once), the paid august stays and is skipped, july is added and paid.
metas_row, metas_changes = created_payment('camp', 'mario', [('july', ['kayak'])])
add('update-owner-replace-periods', 'owner', U('mario'), 200, UPDATED, method='PATCH',
    body={'periods': [period_body('july', 'kayak'), {'camps_and_retreats_period': pid('august')}]},
    effect=effect('owner', created_sps=[{'period': pid('july'), 'subscription': csid('mario'), 'payment': '$payment', 'services': [sid('kayak')]}],
                  created_payment_row=metas_row, deleted_payments=[payid('unpaid')],
                  deleted_sps=[spid('m_july'), spid('m_big'), spid('m_june')],
                  events=[audit(PAYMENT, payid('unpaid'), 2, '$deleted', OWN, CHANGES_PATH),
                          audit(SUBPERIOD, spid('m_july'), 2, '$deleted', OWN, ACTOR_PATH),
                          audit(SUBPERIOD, spid('m_big'), 2, '$deleted', OWN, ACTOR_PATH),
                          audit(SUBPERIOD, spid('m_june'), 2, '$deleted', OWN, ACTOR_PATH),
                          subperiod_created('july', csid('mario')), audit(PAYMENT, '$payment', 0, metas_changes, OWN, 'direct'),
                          subperiod_paid('july')]))
add('update-collaborator-deleted-payment', 'full_collaborator', U('luisa'), 200, UPDATED, method='PATCH', body={},
    effect=effect('full_collaborator', deleted_payments=[payid('luisa_deleted')], deleted_sps=[spid('l_july')],
                  events=[audit(PAYMENT, payid('luisa_deleted'), 2, '$deleted', OWN, CHANGES_PATH),
                          audit(SUBPERIOD, spid('l_july'), 2, '$deleted')]))
add('update-missing-registration-server-error', 'owner', U('nosub'), 500, {'error': "'NoneType' object has no attribute 'associate'"},
    method='PATCH', body={'periods': [period_body('sept', 'guide')]},
    effect=effect('owner', deleted_sps=[spid('n_sept')],
                  created_sps=[{'period': pid('sept'), 'subscription': csid('nosub'), 'payment': None, 'services': [sid('guide')]}],
                  events=[audit(SUBPERIOD, spid('n_sept'), 2, '$deleted', OWN, ACTOR_PATH),
                          subperiod_created('sept', csid('nosub'))]))
add('update-string-periods-server-error', 'owner', U('grouped'), 500, {'error': "string indices must be integers, not 'str'"},
    method='PATCH', body={'periods': 'x'},
    effect=effect('owner', deleted_sps=[spid('g_aug')], events=[audit(SUBPERIOD, spid('g_aug'), 2, '$deleted', OWN, ACTOR_PATH)]))
add('update-service-without-category-server-error', 'owner', U('bus'), 500,
    {'error': "'NoneType' object has no attribute 'payment_category_id'"}, method='PATCH', body={'periods': [period_body('june', 'bus')]},
    effect=effect('owner', created_sps=[{'period': pid('june'), 'subscription': csid('bus'), 'payment': None, 'services': [sid('bus')]}],
                  events=[subperiod_created('june', csid('bus'))]))
add('update-invalid-service-server-error', 'owner', U('bus'), 500, {'error': "['“zz” is not a valid UUID.']"}, method='PATCH',
    body={'periods': [{'camps_and_retreats_period': pid('july'), 'services': [{'camps_and_retreats_period_service_id': 'zz'}]}]},
    effect=effect('owner', deleted_sps=[OLD_SP('bus', 'june')],
                  created_sps=[{'period': pid('july'), 'subscription': csid('bus'), 'payment': None, 'services': []}],
                  events=[audit(SUBPERIOD, OLD_SP('bus', 'june'), 2, '$deleted', OWN, ACTOR_PATH),
                          subperiod_created('july', csid('bus'))]))
add('update-dict-periods-server-error', 'owner', U('extra'), 500, {'error': "string indices must be integers, not 'str'"}, method='PATCH',
    body={'periods': {'a': 1}}, effect=effect('owner'))
add('update-null-period-server-error', 'owner', U('extra'), 500, {'error': 'CampsAndRetreatsPeriod matching query does not exist.'},
    method='PATCH', body={'periods': [{'camps_and_retreats_period': None}]}, effect=effect('owner'))
add('update-services-not-iterable-server-error', 'owner', U('extra'), 500, {'error': "'int' object is not iterable"}, method='PATCH',
    body={'periods': [{'camps_and_retreats_period': pid('sept'), 'services': 5}]},
    effect=effect('owner', created_sps=[{'period': pid('sept'), 'subscription': csid('extra'), 'payment': None, 'services': []}],
                  events=[subperiod_created('sept', csid('extra'))]))
add('update-service-missing-key-server-error', 'owner', U('extra'), 500, {'error': "'camps_and_retreats_period_service_id'"},
    method='PATCH', body={'periods': [{'camps_and_retreats_period': pid('sept'),
                                       'services': [{'camps_and_retreats_period_service_id': 'zz'}, {}]}]},
    effect=effect('owner', deleted_sps=[OLD_SP('extra', 'sept')],
                  created_sps=[{'period': pid('sept'), 'subscription': csid('extra'), 'payment': None, 'services': []}],
                  events=[audit(SUBPERIOD, OLD_SP('extra', 'sept'), 2, '$deleted', OWN, ACTOR_PATH),
                          subperiod_created('sept', csid('extra'))]))
extra_row, extra_changes = created_payment('plain', 'sibling', [('sept', ['guide'])])
add('update-null-service-skipped-untitled-camp', 'owner', U('extra'), 200, UPDATED, method='PATCH',
    body={'periods': [{'camps_and_retreats_period': pid('sept'), 'services': [
        {'camps_and_retreats_period_service_id': None}, {'camps_and_retreats_period_service_id': sid('guide').upper()}]}]},
    effect=effect('owner', deleted_sps=[OLD_SP('extra', 'sept')],
                  created_sps=[{'period': pid('sept'), 'subscription': csid('extra'), 'payment': '$payment', 'services': [sid('guide')]}],
                  created_payment_row=extra_row,
                  events=[audit(SUBPERIOD, OLD_SP('extra', 'sept'), 2, '$deleted', OWN, ACTOR_PATH),
                          subperiod_created('sept', csid('extra')), audit(PAYMENT, '$payment', 0, extra_changes, OWN, 'direct'),
                          subperiod_paid('sept')]))
add('update-form-without-periods', 'owner', U('bus'), 200, UPDATED, method='PATCH', raw_body='note=1', content_type=FORM,
    effect=effect('owner', deleted_sps=[OLD_SP('bus', 'july')], events=[audit(SUBPERIOD, OLD_SP('bus', 'july'), 2, '$deleted', OWN, ACTOR_PATH)]))

# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------
D = lambda label: path(csid(label) if label in CAMP_SUBSCRIPTIONS else label, 'delete')
DELETED = {'message': 'Deleted successfully'}
add('delete-anonymous', 'anonymous', D('mario'), 401, ANONYMOUS, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', D('mario'), 403, missing('delete'), method='DELETE')
add('delete-owner-scoped-athlete-denied', 'owner', D('mario'), 403, SCOPED_DENIED, method='DELETE', headers=SCOPED)
add('delete-wrong-method', 'owner', D('mario'), 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH', body={})
add('delete-options', 'owner', D('mario'), 200, OPTIONS('Camps And Retreats Subscriptions Delete'), method='OPTIONS')
add('delete-malformed-athlete', 'athlete', D('abc'), 400, BADLY_FORMED, method='DELETE')
add('delete-athlete-forbidden', 'athlete', D('mario'), 403, NOT_ASSOCIATION, method='DELETE')
add('delete-unknown', 'owner', D(UNKNOWN), 404, NOT_FOUND, method='DELETE')
add('delete-foreign-forbidden', 'owner', D('abroad'), 403, {'detail': 'You are not allowed deleting this.'}, method='DELETE')
add('delete-unassociated-owner-server-error', 'owner_without_association', D('mario'), 500, NO_ASSOCIATION, method='DELETE')
add('delete-owner-unpaid-and-paid', 'owner', D('mario'), 200, DELETED, method='DELETE',
    effect=effect('owner', deleted_subs=[csid('mario')], deleted_payments=[OLD_PAY('mario', 'july')],
                  deleted_sps=[OLD_SP('mario', 'july'), spid('m_aug')],
                  events=[audit(PAYMENT, OLD_PAY('mario', 'july'), 2, '$deleted', OWN, CHANGES_PATH),
                          audit(SUBPERIOD, OLD_SP('mario', 'july'), 2, '$deleted', OWN, ACTOR_PATH),
                          audit(SUBPERIOD, spid('m_aug'), 2, '$deleted', OWN, ACTOR_PATH),
                          audit(CSUB, csid('mario'), 2, '$deleted', OWN, ACTOR_PATH)]))
add('delete-collaborator-paid-cascade', 'full_collaborator', D('luisa_plain'), 200, DELETED, method='DELETE',
    effect=effect('full_collaborator', deleted_subs=[csid('luisa_plain')], deleted_sps=[spid('lp_sept')],
                  events=[audit(SUBPERIOD, spid('lp_sept'), 2, '$deleted'), audit(CSUB, csid('luisa_plain'), 2, '$deleted')]))
add('delete-hex-uid-empty', 'owner', D(csid('luisa').replace('-', '')), 200, DELETED, method='DELETE',
    effect=effect('owner', deleted_subs=[csid('luisa')], events=[audit(CSUB, csid('luisa'), 2, '$deleted', OWN, ACTOR_PATH)]))
add('delete-again-not-found', 'owner', D('mario'), 404, NOT_FOUND, method='DELETE')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Camps and retreats subscriptions authored sequence with audit effects',
                              'ids': IDS, 'family': FAMILY, 'account': ACCOUNT, 'categories': CATEGORIES, 'associates': ASSOCIATES,
                              'registrations': REGISTRATIONS, 'camps': CAMPS, 'periods': PERIODS, 'services': SERVICES,
                              'period_links': PERIOD_LINKS, 'camp_subscriptions': CAMP_SUBSCRIPTIONS, 'payments': PAYMENTS,
                              'instructor': INSTRUCTOR, 'invoice': INVOICE,
                              'subscription_periods': SUBPERIODS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
