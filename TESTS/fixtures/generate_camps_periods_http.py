"""Author the camps-and-retreats periods and period services HTTP sequence (camps-periods.md).

Function views in BE/application/views/camp_and_retreats_views.py, each
@api_view with one method:
- periods/add (POST): is_sport_association (403 {'error'}), then
  CampsAndRetreatsPeriodSerializer (fields '__all__'; to_internal_value turns
  a '' fee or max_participants into None by assigning into request.data, which
  fails on an immutable form QueryDict; validate_fee returns float(fee)).
  Validation errors answer 400 {'error': str(ValidationError)} (the repr of
  the ErrorDetail dict); any other exception, including the tenant scope's
  PermissionDenied and the TypeError of objects.create() with the many-to-many
  services, answers 500 {'error': str(e)}. serializer.save() creates the row
  and the view saves it again: auditlog records the create from the in-memory
  values (fee as str(float)) and, when str(float) differs from the stored
  Decimal, an update of the fee.
- periods/<uid>/info (GET): is_sport_association, then is_valid_uuid (400
  list), the period (404 {'error': 'Not found'}), its camp's association
  against request.user.sport_association (403; a user without one is a 500
  {'error'}), then CampsAndRetreatsPeriodInfoSerializer.
- periods/<uid>/update (PATCH) and delete (DELETE): is_valid_uuid first, then
  the same checks; update re-raises PermissionDenied (403) and saves the
  partial serializer (many-to-many set() after the row); delete cascades to
  the period's services and subscription periods and their link rows.
- periods/services/add, update and delete: the same shapes with
  CampsAndRetreatsPeriodsServiceAddSerializer. update then walks the
  subscription periods of the service's (new) period whose payment is
  unpaid: every meta_payment_categories item of this service takes the
  serializer's fee, title and payment category and saves the payment, then
  the payment amount (metas plus the period fee) and notes are recomputed and
  saved, all after the service save (requests are not atomic).
Deletes are indexed through the actor's association (the rows are gone when
the index resolves). Run with ``python3 -I``.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).with_name('camps_periods_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{985001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'
UNKNOWN = next(ids)
NEW_PERIOD, NEW_SERVICE = next(ids), next(ids)

CATEGORIES = {
    'own': {'id': next(ids), 'name': 'Servizi campo', 'created': '2026-05-01T08:00:00Z', 'association': OWN},
    'foreign': {'id': next(ids), 'name': 'Servizi esteri', 'created': '2026-05-02T08:00:00.250000Z', 'association': FOREIGN},
}
CAMPS = {
    'camp': {'id': next(ids), 'title': 'Campo estivo è', 'description': 'Mare e monti', 'association': OWN},
    'plain': {'id': next(ids), 'title': None, 'description': None, 'association': OWN},
    'abroad': {'id': next(ids), 'title': 'Campo estero', 'description': 'Lontano', 'association': FOREIGN},
}
PERIODS = {
    'july': {'id': next(ids), 'camp': 'camp', 'start': '2026-07-01T08:00:00Z', 'end': '2026-07-15T18:00:00Z', 'title': 'Luglio',
             'description': 'Prima settimana', 'fee': '100.00', 'max': 20, 'created': '2026-06-01T10:00:00Z'},
    'august': {'id': next(ids), 'camp': 'camp', 'start': '2026-08-01T00:00:00Z', 'end': '2026-08-10T00:00:00Z', 'title': None,
               'description': None, 'fee': '0.00', 'max': None, 'created': '2026-06-02T10:00:00.250000Z'},
    'june': {'id': next(ids), 'camp': 'camp', 'start': '2026-06-10T07:00:00Z', 'end': '2026-06-20T07:00:00Z', 'title': 'Giugno',
             'description': 'Breve', 'fee': '55.50', 'max': 0, 'created': '2026-06-03T10:00:00Z'},
    'sept': {'id': next(ids), 'camp': 'plain', 'start': '2026-09-01T07:00:00Z', 'end': '2026-09-05T07:00:00Z', 'title': 'Settembre',
             'description': None, 'fee': '10.00', 'max': 5, 'created': '2026-06-04T10:00:00Z'},
    'abroad': {'id': next(ids), 'camp': 'abroad', 'start': '2026-07-01T07:00:00Z', 'end': '2026-07-02T07:00:00Z', 'title': 'Estero',
               'description': None, 'fee': '1.00', 'max': None, 'created': '2026-06-05T10:00:00Z'},
}
SERVICES = {
    'kayak': {'id': next(ids), 'period': 'july', 'title': 'Kayak', 'description': 'Mezza giornata', 'fee': '12.50', 'category': 'own',
              'created': '2026-06-05T09:00:00Z'},
    'bus': {'id': next(ids), 'period': 'july', 'title': 'Bus', 'description': None, 'fee': '30.00', 'category': None,
            'created': '2026-06-06T09:00:00.000001Z'},
    'umbrella': {'id': next(ids), 'period': 'july', 'title': 'Ombrellone', 'description': 'Spiaggia', 'fee': '5.00', 'category': 'own',
                 'created': '2026-06-07T09:00:00Z'},
    'tent': {'id': next(ids), 'period': 'june', 'title': 'Tenda', 'description': None, 'fee': '7.00', 'category': None,
             'created': '2026-06-08T09:00:00Z'},
    'guide': {'id': next(ids), 'period': 'sept', 'title': 'Guida', 'description': None, 'fee': '15.00', 'category': 'own',
              'created': '2026-06-09T09:00:00Z'},
    'snack': {'id': next(ids), 'period': 'august', 'title': 'Merenda', 'description': None, 'fee': '2.00', 'category': None,
              'created': '2026-06-10T09:00:00Z'},
    'abroad': {'id': next(ids), 'period': 'abroad', 'title': 'Estero', 'description': None, 'fee': '3.00', 'category': 'foreign',
               'created': '2026-06-11T09:00:00Z'},
}
SUBSCRIPTIONS = {'camp': {'id': next(ids), 'camp': 'camp', 'created': '2026-06-12T09:00:00Z'},
                 'plain': {'id': next(ids), 'camp': 'plain', 'created': '2026-06-12T10:00:00Z'}}


def meta(service, amount, category):
    return {'camps_and_retreats_period_service_id': SERVICES[service]['id'], 'title': SERVICES[service]['title'], 'amount': amount,
            'payment_category_id': CATEGORIES[category]['id'] if category else 'None'}


PAYMENTS = {
    'july': {'id': next(ids), 'amount': '172.50', 'paid': False, 'subject': 1, 'description': 'Iscrizione', 'category': 'own',
             'notes': 'Vecchie note', 'meta': [meta('kayak', 12.5, 'own'), meta('bus', 30, None)], 'created': '2026-06-13T09:00:00Z'},
    'paid': {'id': next(ids), 'amount': '100.00', 'paid': True, 'subject': 1, 'description': 'Pagato', 'category': 'own',
             'notes': None, 'meta': [meta('kayak', 12.5, 'own')], 'created': '2026-06-13T10:00:00Z'},
    'sept': {'id': next(ids), 'amount': '10.00', 'paid': False, 'subject': 1, 'description': 'Settembre', 'category': 'own',
             'notes': None, 'meta': None, 'created': '2026-06-13T11:00:00Z'},
}
SUBSCRIPTION_PERIODS = {
    'july': {'id': next(ids), 'period': 'july', 'subscription': 'camp', 'payment': 'july', 'services': ['kayak', 'umbrella']},
    'july_paid': {'id': next(ids), 'period': 'july', 'subscription': 'camp', 'payment': 'paid', 'services': []},
    'july_unlinked': {'id': next(ids), 'period': 'july', 'subscription': 'camp', 'payment': None, 'services': []},
    'june': {'id': next(ids), 'period': 'june', 'subscription': 'camp', 'payment': None, 'services': ['tent']},
    'sept': {'id': next(ids), 'period': 'sept', 'subscription': 'plain', 'payment': 'sept', 'services': ['guide']},
}
# A period whose only subscription period carries a soft-deleted unpaid payment (keys taken after the rows above).
PERIODS['october'] = {'id': next(ids), 'camp': 'camp', 'start': '2026-10-01T07:00:00Z', 'end': '2026-10-05T07:00:00Z', 'title': 'Ottobre',
                      'description': None, 'fee': '40.00', 'max': None, 'created': '2026-06-06T10:00:00Z'}
SERVICES['canoe'] = {'id': next(ids), 'period': 'october', 'title': 'Canoa', 'description': None, 'fee': '8.00', 'category': None,
                     'created': '2026-06-11T10:00:00Z'}
PAYMENTS['gone'] = {'id': next(ids), 'amount': '48.00', 'paid': False, 'subject': 1, 'description': 'Ottobre', 'category': 'own',
                    'notes': 'Note', 'meta': [meta('canoe', 8.0, None)], 'created': '2026-06-13T12:00:00Z', 'deleted': True}
SUBSCRIPTION_PERIODS['october'] = {'id': next(ids), 'period': 'october', 'subscription': 'camp', 'payment': 'gone', 'services': ['canoe']}
PERIOD_LINKS = {'july': ['kayak', 'umbrella'], 'june': ['tent'], 'august': ['snack']}

PERIOD, SERVICE, SUBPERIOD, PAYMENT = ('application.CampsAndRetreatsPeriod', 'application.CampsAndRetreatsPeriodsService',
                                       'application.CampsAndRetreatsSubscriptionPeriod', 'application.Payment')
PERIOD_LINK = 'application.CampsAndRetreatsPeriod_camps_and_Retreats_period_services'
SUBPERIOD_LINK = 'application.CampsAndRetreatsSubscriptionPeriod_camps_and_retreats_period_services'
PERIOD_PATH, SERVICE_PATH = 'camps_and_retreats.sport_association', 'period.camps_and_retreats.sport_association'

# Running state: the generator applies each authored success to it.
STATE = {'periods': copy.deepcopy(PERIODS), 'services': copy.deepcopy(SERVICES), 'payments': copy.deepcopy(PAYMENTS),
         'links': copy.deepcopy(PERIOD_LINKS)}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'camps-periods-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
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


REQUIRED, NULL, BLANK = ('This field is required.', 'required'), ('This field may not be null.', 'null'), ('This field may not be blank.', 'blank')
DATETIME = ('Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].', 'invalid')
NUMBER, INTEGER = ('A valid number is required.', 'invalid'), ('A valid integer is required.', 'invalid')
UUID_INVALID = ('Must be a valid UUID.', 'invalid')
NOT_STRING = ('Not a valid string.', 'invalid')


def multipart(*parts):
    """A multipart/form-data body: (name, filename or None, content) parts; returns (body, content type)."""
    boundary = 'testboundary4f2a'
    lines = []
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
        lines.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n{content}\r\n')
    return ''.join(lines) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'

missing_pk = lambda pk: (f'Invalid pk "{pk}" - object does not exist.', 'does_not_exist')
bad_uuid = lambda value: (f'“{value}” is not a valid UUID.', 'invalid')
too_long = lambda n: (f'Ensure this field has no more than {n} characters.', 'max_length')
TENANT = 'You do not have permission to perform this action.'
M2M_CREATE = ('Direct assignment to the forward side of a many-to-many set is prohibited. '
              'Use camps_and_Retreats_period_services.set() instead.')

P_ADD, S_ADD = '/camps-and-retreats/periods/add', '/camps-and-retreats/periods/services/add'


def period_path(label, verb):
    return f'/camps-and-retreats/periods/{PERIODS[label]["id"] if label in PERIODS else label}/{verb}'


def service_path(label, verb):
    return f'/camps-and-retreats/periods/services/{SERVICES[label]["id"] if label in SERVICES else label}/{verb}'


def unordered(values):
    return {'$unordered': values}


def period_data(label):
    row = STATE['periods'][label]
    return {'camps_and_retreats_period_id': row['id'], 'start_date': drf(row['start']), 'end_date': drf(row['end']),
            'title': row['title'], 'description': row['description'], 'fee': row['fee'], 'max_participants': row['max'],
            'created_at': drf(row['created']), 'camps_and_retreats': CAMPS[row['camp']]['id'],
            'camps_and_Retreats_period_services': unordered([SERVICES[s]['id'] for s in STATE['links'].get(label, [])])}


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


def period_info(label):
    row = STATE['periods'][label]
    services = sorted((s for s, value in STATE['services'].items() if value['period'] == label), key=lambda s: STATE['services'][s]['created'],
                      reverse=True)
    data = {**period_data(label), 'start_date': moment(row['start']).strftime('%d/%m/%Y'), 'end_date': moment(row['end']).strftime('%d/%m/%Y'),
            'fee': comma(row['fee']), 'services': []}
    for s in services:
        value = STATE['services'][s]
        data['services'].append({'camps_and_retreats_period_service_id': value['id'], 'payment_category_info': category_info(value['category']),
                                 'fee': comma(value['fee']), 'title': value['title'], 'description': value['description'],
                                 'created_at': drf(value['created']), 'camps_and_retreats_period': PERIODS[value['period']]['id'],
                                 'payment_category': CATEGORIES[value['category']]['id'] if value['category'] else None})
    return {'data': data}


def service_data(label):
    row = STATE['services'][label]
    return {'camps_and_retreats_period_service_id': row['id'], 'title': row['title'], 'description': row['description'], 'fee': row['fee'],
            'created_at': drf(row['created']), 'camps_and_retreats_period': PERIODS[row['period']]['id'],
            'payment_category': CATEGORIES[row['category']]['id'] if row['category'] else None}


def period_row(row):
    return {'camps_and_retreats_id': CAMPS[row['camp']]['id'], 'start_date': drf(row['start']), 'end_date': drf(row['end']),
            'title': row['title'], 'description': row['description'], 'fee': row['fee'], 'max_participants': row['max']}


def service_row(row):
    return {'camps_and_retreats_period_id': PERIODS[row['period']]['id'], 'title': row['title'], 'description': row['description'],
            'fee': row['fee'], 'payment_category_id': CATEGORIES[row['category']]['id'] if row['category'] else None}


def audit(model, key, action, changes, actor_association=None, path=None):
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'association': actor_association, 'path': path}


def stored_changes(before, after, fields):
    return {name: [before[key], after[key]] for name, key in fields if before[key] != after[key]}


def text(value):
    return 'None' if value is None else str(value)


# ---------------------------------------------------------------------------
# periods/add
# ---------------------------------------------------------------------------
OPTIONS = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
missing = lambda verb: {'detail': f'Missing permission: association.campsandretreats.{verb}'}
NOT_ASSOCIATION = {'error': 'You are not a sport association.'}
BADLY_FORMED = ['badly formed hexadecimal UUID string']
NOT_FOUND = {'error': 'Not found'}
NO_ASSOCIATION = {'error': 'SportAssociation matching query does not exist.'}
camp_id = lambda label: CAMPS[label]['id']
DATES = {'start_date': '2026-10-01T10:00:00+02:00', 'end_date': '2026-10-05'}

add('add-anonymous', 'anonymous', P_ADD, 401, {'detail': 'Authentication credentials were not provided.'}, method='POST', body={})
add('add-admin-without-session', 'admin', P_ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    method='POST', body={})
add('add-restricted-missing-permission', 'restricted_collaborator', P_ADD, 403, missing('create'), method='POST', body={})
add('add-owner-scoped-athlete-denied', 'owner', P_ADD, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    method='POST', body={}, headers=SCOPED)
add('add-wrong-method', 'owner', P_ADD, 405, {'detail': 'Method "GET" not allowed.'})
add('add-options', 'owner', P_ADD, 200, OPTIONS('Camps And Retreats Periods Add'), method='OPTIONS')
add('add-athlete-forbidden', 'athlete', P_ADD, 403, NOT_ASSOCIATION, method='POST', body={'title': ''})
add('add-missing-required', 'owner', P_ADD, 400, {'error': repr_detail([('start_date', [REQUIRED]), ('end_date', [REQUIRED]),
                                                                          ('camps_and_retreats', [REQUIRED])])}, method='POST', body={})
add('add-invalid-fields', 'owner', P_ADD, 400, {'error': repr_detail([
    ('camps_and_retreats_period_id', [UUID_INVALID]), ('start_date', [DATETIME]), ('end_date', [DATETIME]), ('title', [BLANK]),
    ('description', [too_long(2048)]), ('fee', [NUMBER]), ('max_participants', [('Ensure this value is greater than or equal to 0.', 'min_value')]),
    ('created_at', [NULL]), ('camps_and_retreats', [missing_pk(UNKNOWN)]),
    ('camps_and_Retreats_period_services', [('Expected a list of items but got type "str".', 'not_a_list')])])},
    method='POST', body={'camps_and_retreats_period_id': 'abc', 'start_date': 'luglio', 'end_date': 5, 'title': '', 'description': 'x' * 2049,
                         'fee': 'abc', 'max_participants': -1, 'created_at': None, 'camps_and_retreats': UNKNOWN,
                         'camps_and_Retreats_period_services': 'x'})
add('add-invalid-precision-and-relations', 'owner', P_ADD, 400, {'error': repr_detail([
    ('start_date', [DATETIME]), ('title', [too_long(255)]),
    ('fee', [('Ensure that there are no more than 2 decimal places.', 'max_decimal_places')]),
    ('max_participants', [('Ensure this value is less than or equal to 2147483647.', 'max_value')]),
    ('camps_and_retreats', [bad_uuid('abc')]), ('camps_and_Retreats_period_services', [missing_pk(UNKNOWN)])])},
    method='POST', body={'start_date': '2026-13-01', 'end_date': '2026-10-05T00:00:00Z', 'title': 'y' * 256, 'fee': '1.234',
                         'max_participants': 2147483648, 'camps_and_retreats': 'abc', 'camps_and_Retreats_period_services': [UNKNOWN]})
add('add-invalid-digits-and-types', 'owner', P_ADD, 400, {'error': repr_detail([
    ('title', [('Not a valid string.', 'invalid')]),
    ('fee', [('Ensure that there are no more than 7 digits before the decimal point.', 'max_whole_digits')]),
    ('max_participants', [INTEGER]), ('camps_and_retreats', [('Incorrect type. Expected pk value, received bool.', 'incorrect_type')])])},
    method='POST', body={**DATES, 'title': True, 'fee': '12345678', 'max_participants': '1.5', 'camps_and_retreats': True})
add('add-blank-fee-is-null', 'owner', P_ADD, 400, {'error': repr_detail([('fee', [NULL])])}, method='POST',
    body={**DATES, 'fee': '', 'max_participants': '', 'camps_and_retreats': camp_id('camp')})
add('add-existing-id', 'owner', P_ADD, 400, {'error': repr_detail([('camps_and_retreats_period_id', [
    ('camps and retreats period with this camps and retreats period id already exists.', 'unique')])])}, method='POST',
    body={**DATES, 'camps_and_retreats_period_id': PERIODS['july']['id'], 'camps_and_retreats': camp_id('camp')})
add('add-list-body', 'owner', P_ADD, 400, {'error': repr_detail([('non_field_errors', [
    ('Invalid data. Expected a dictionary, but got list.', 'invalid')])])}, method='POST', body=[])
add('add-null-body', 'owner', P_ADD, 400, {'error': repr_detail([('non_field_errors', [('No data provided', 'null')])])}, method='POST',
    raw_body='null', content_type='application/json')
add('add-string-body-server-error', 'owner', P_ADD, 500, {'error': "string indices must be integers, not 'str'"}, method='POST', body='fee')
add('add-number-body-server-error', 'owner', P_ADD, 500, {'error': "argument of type 'int' is not iterable"}, method='POST', body=5)
add('add-foreign-camp-server-error', 'owner', P_ADD, 500, {'error': TENANT}, method='POST',
    body={**DATES, 'title': '', 'camps_and_retreats': camp_id('abroad')})
add('add-foreign-service-server-error', 'full_collaborator', P_ADD, 500, {'error': TENANT}, method='POST',
    body={**DATES, 'camps_and_retreats': camp_id('camp'), 'camps_and_Retreats_period_services': [SERVICES['abroad']['id']]})
add('add-services-server-error', 'owner', P_ADD, 500, {'error': M2M_CREATE}, method='POST',
    body={**DATES, 'camps_and_retreats': camp_id('camp'), 'camps_and_Retreats_period_services': []})
add('add-form-blank-fee-server-error', 'owner', P_ADD, 500, {'error': 'This QueryDict instance is immutable'}, method='POST',
    raw_body='title=Form&fee=', content_type='application/x-www-form-urlencoded')
add('add-form-services-server-error', 'owner', P_ADD, 500, {'error': M2M_CREATE}, method='POST',
    raw_body=f'start_date=2026-10-01&end_date=2026-10-02&camps_and_retreats={camp_id("camp")}&title=',
    content_type='application/x-www-form-urlencoded')
add('add-form-invalid', 'owner', P_ADD, 400, {'error': repr_detail([('start_date', [DATETIME]), ('end_date', [REQUIRED]),
                                                                     ('max_participants', [INTEGER]), ('camps_and_retreats', [NULL])])},
    method='POST', raw_body='start_date=&max_participants=x&camps_and_retreats=', content_type='application/x-www-form-urlencoded')


def created_period(actor, label, values, body, *, explicit_id=None, explicit_created=None):
    """The add response and effect: fee is float(fee) in memory, so the second save
    records the fee again when str(float) differs from the stored Decimal."""
    row = {'id': explicit_id or '$new', 'created': explicit_created, **values}
    float_fee = repr(float(row['fee']))
    response = {'message': 'Added successfully', 'period': {
        'camps_and_retreats_period_id': explicit_id or '$new', 'start_date': drf(row['start']), 'end_date': drf(row['end']),
        'title': row['title'], 'description': row['description'], 'fee': row['fee'], 'max_participants': row['max'],
        'created_at': drf(explicit_created) if explicit_created else '$now', 'camps_and_retreats': CAMPS[row['camp']]['id'],
        'camps_and_Retreats_period_services': []}}
    key = explicit_id or '$new'
    changes = {'camps_and_retreats_period_id': ['None', explicit_id or '$key'], 'camps_and_retreats': ['None', CAMPS[row['camp']]['id']],
               'start_date': ['None', naive(row['start'])], 'end_date': ['None', naive(row['end'])], 'fee': ['None', float_fee],
               'created_at': ['None', naive(explicit_created) if explicit_created else '$created']}
    for name, value in (('title', row['title']), ('description', row['description']), ('max_participants', row['max'])):
        if value is not None:
            changes[name] = ['None', str(value)]
    association = CAMPS[row['camp']]['association']
    events = [audit('CampsAndRetreatsPeriod', key, 0, changes, association, PERIOD_PATH)]
    if float_fee != row['fee']:
        events.append(audit('CampsAndRetreatsPeriod', key, 1, {'fee': [row['fee'], float_fee]}, association, PERIOD_PATH))
    stored = {**period_row(row)}
    if explicit_created:
        stored['created_at'] = drf(explicit_created)
    effect = {'actor': actor, 'rows': {PERIOD: {key: stored}}, 'audit': events}
    add(label, actor, P_ADD, 201, response, method='POST', body=body, effect=effect)


created_period('owner', 'add-owner', {'camp': 'camp', 'start': '2026-10-01T08:00:00Z', 'end': '2026-10-05T00:00:00Z', 'title': 'Ottobre è',
                                      'description': 'Autunno', 'fee': '12.50', 'max': 7},
               {**DATES, 'title': ' Ottobre è ', 'description': 'Autunno', 'fee': '12.5', 'max_participants': '7',
                'camps_and_retreats': camp_id('camp'), 'ignored': 'x'})
created_period('full_collaborator', 'add-full-collaborator-defaults', {'camp': 'plain', 'start': '2026-11-01T00:00:00Z',
                                                                      'end': '2026-11-02T00:00:00Z', 'title': None, 'description': None,
                                                                      'fee': '0.00', 'max': 0},
               {'start_date': '2026-11-01T00:00:00', 'end_date': '2026-11-02T00:00:00Z', 'camps_and_retreats': camp_id('plain')})
created_period('owner', 'add-explicit-id-and-created', {'camp': 'camp', 'start': '2026-12-01T00:00:00Z', 'end': '2026-12-03T00:00:00Z',
                                                        'title': '5', 'description': None, 'fee': '12.34', 'max': None},
               {'camps_and_retreats_period_id': NEW_PERIOD, 'created_at': '2026-06-30T12:00:00.123456Z', 'start_date': '2026-12-01',
                'end_date': '2026-12-03T01:00:00+01:00', 'title': 5, 'fee': 12.34, 'max_participants': '',
                'camps_and_retreats': camp_id('camp')},
               explicit_id=NEW_PERIOD, explicit_created='2026-06-30T12:00:00.123456Z')
created_period('owner_without_association', 'add-unassociated-owner', {'camp': 'abroad', 'start': '2026-10-01T08:00:00Z',
                                                                       'end': '2026-10-05T00:00:00Z', 'title': None, 'description': None,
                                                                       'fee': '-3.00', 'max': 2},
               {**DATES, 'fee': -3, 'max_participants': 2.0, 'camps_and_retreats': camp_id('abroad')})

# ---------------------------------------------------------------------------
# periods/<uid>/info
# ---------------------------------------------------------------------------
add('info-anonymous', 'anonymous', period_path('july', 'info'), 401, {'detail': 'Authentication credentials were not provided.'})
add('info-restricted-missing-permission', 'restricted_collaborator', period_path('july', 'info'), 403, missing('read'))
add('info-wrong-method', 'owner', period_path('july', 'info'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('info-options', 'owner', period_path('july', 'info'), 200,
    OPTIONS('Camps And Retreats Periods Info', 'This method is used to get a list of all camps and retreats'), method='OPTIONS')
add('info-athlete-before-uuid', 'athlete', period_path('abc', 'info'), 403, {'error': 'Only sport associations can view camps or retreats'})
add('info-malformed', 'owner', period_path('abc', 'info'), 400, BADLY_FORMED)
add('info-unknown', 'owner', period_path(UNKNOWN, 'info'), 404, NOT_FOUND)
add('info-foreign-forbidden', 'owner', period_path('abroad', 'info'), 403, {'detail': 'You are not allowed to view this'})
add('info-unassociated-owner-server-error', 'owner_without_association', period_path('july', 'info'), 500, NO_ASSOCIATION)
add('info-owner', 'owner', period_path('july', 'info'), 200, period_info('july'))
add('info-collaborator-null-fields', 'full_collaborator', period_path('august', 'info'), 200, period_info('august'))
add('info-hex-uid', 'owner', period_path(PERIODS['sept']['id'].replace('-', '').upper(), 'info'), 200, period_info('sept'))

# ---------------------------------------------------------------------------
# periods/<uid>/update
# ---------------------------------------------------------------------------
PERIOD_FIELDS = (('camps_and_retreats', 'camp_id'), ('start_date', 'start_naive'), ('end_date', 'end_naive'), ('title', 'title_text'),
                 ('description', 'description_text'), ('fee', 'fee'), ('max_participants', 'max_text'))


def period_audit_view(row, fee=None):
    return {'camp_id': CAMPS[row['camp']]['id'], 'start_naive': naive(row['start']), 'end_naive': naive(row['end']),
            'title_text': text(row['title']), 'description_text': text(row['description']), 'fee': fee or row['fee'],
            'max_text': text(row['max'])}


def updated_period(actor, name, label, body, values, *, links=None, form=None, form_type='application/x-www-form-urlencoded'):
    """Apply a partial update; the in-memory fee is str(float(fee)) for the audit diff."""
    row = STATE['periods'][label]
    before = period_audit_view(row)
    row.update(values)
    float_fee = repr(float(values['fee'])) if 'fee' in values else None
    changes = stored_changes(before, period_audit_view(row, float_fee), PERIOD_FIELDS)
    old_links = set(STATE['links'].get(label, []))
    if links is not None:
        STATE['links'][label] = links
    effect = {'actor': actor, 'rows': {PERIOD: {row['id']: period_row(row)}}, 'audit': []}
    if changes:
        effect['audit'].append(audit('CampsAndRetreatsPeriod', row['id'], 1, changes, CAMPS[row['camp']]['association'], PERIOD_PATH))
    if links is not None:
        effect['links'] = {PERIOD_LINK: {'add': [[row['id'], SERVICES[s]['id']] for s in sorted(set(links) - old_links)],
                                         'remove': [[row['id'], SERVICES[s]['id']] for s in sorted(old_links - set(links))]}}
    response = {'message': 'Updated successfully', 'period': period_data(label)}
    if form:
        add(name, actor, period_path(label, 'update'), 200, response, method='PATCH', raw_body=form,
            content_type=form_type, effect=effect)
    else:
        add(name, actor, period_path(label, 'update'), 200, response, method='PATCH', body=body, effect=effect)


add('update-anonymous', 'anonymous', period_path('august', 'update'), 401, {'detail': 'Authentication credentials were not provided.'},
    method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', period_path('august', 'update'), 403, missing('update'),
    method='PATCH', body={})
add('update-wrong-method', 'owner', period_path('august', 'update'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('update-options', 'owner', period_path('august', 'update'), 200, OPTIONS('Camps And Retreats Periods Update'), method='OPTIONS')
add('update-malformed-before-role', 'athlete', period_path('abc', 'update'), 400, BADLY_FORMED, method='PATCH', body={})
add('update-services-segment-malformed', 'owner', '/camps-and-retreats/periods/services/update', 400, BADLY_FORMED, method='PATCH', body={})
add('update-athlete-forbidden', 'athlete', period_path('august', 'update'), 403, NOT_ASSOCIATION, method='PATCH', body={})
add('update-unknown', 'owner', period_path(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={})
add('update-foreign-forbidden', 'owner', period_path('abroad', 'update'), 403, {'detail': 'You are not allowed editing this.'},
    method='PATCH', body={'title': 'X'})
add('update-unassociated-owner-server-error', 'owner_without_association', period_path('august', 'update'), 500, NO_ASSOCIATION,
    method='PATCH', body={})
add('update-invalid', 'owner', period_path('august', 'update'), 400, {'error': repr_detail([
    ('start_date', [NULL]), ('fee', [('Ensure that there are no more than 9 digits in total.', 'max_digits')]), ('max_participants', [INTEGER]),
    ('camps_and_retreats', [NULL])])}, method='PATCH',
    body={'start_date': None, 'fee': '1234567.891', 'max_participants': 'x', 'camps_and_retreats': None})
add('update-foreign-service-forbidden', 'owner', period_path('august', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'title': '', 'camps_and_Retreats_period_services': [SERVICES['kayak']['id'], SERVICES['abroad']['id']]})
add('update-foreign-camp-forbidden', 'full_collaborator', period_path('august', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'camps_and_retreats': camp_id('abroad')})
add('update-form-blank-fee-server-error', 'owner', period_path('august', 'update'), 500, {'error': 'This QueryDict instance is immutable'},
    method='PATCH', raw_body='fee=', content_type='application/x-www-form-urlencoded')
updated_period('owner', 'update-owner', 'august', {'title': 'Agosto', 'fee': '20', 'max_participants': 15,
                                                   'start_date': '2026-08-02T09:30:00.5Z',
                                                   'camps_and_Retreats_period_services': [SERVICES['kayak']['id'], SERVICES['bus']['id']]},
               {'title': 'Agosto', 'fee': '20.00', 'max': 15, 'start': '2026-08-02T09:30:00.500000Z'}, links=['bus', 'kayak'])
updated_period('full_collaborator', 'update-same-values-no-audit', 'august',
               {'title': 'Agosto', 'camps_and_retreats': camp_id('camp'), 'camps_and_Retreats_period_services': [SERVICES['bus']['id']]},
               {'title': 'Agosto'}, links=['bus'])
updated_period('owner', 'update-same-fee-audited', 'august', {'fee': '20.00'}, {'fee': '20.00'})
updated_period('owner', 'update-move-camp-and-nulls', 'august', {'camps_and_retreats': camp_id('plain'), 'title': None,
                                                                 'max_participants': '', 'camps_and_Retreats_period_services': []},
               {'camp': 'plain', 'title': None, 'max': None}, links=[])
updated_period('owner', 'update-form', 'august', None, {'description': 'Form è', 'end': '2026-08-20T00:00:00Z', 'max': 3},
               form='description=Form+%C3%A8&end_date=2026-08-20T00%3A00%3A00Z&max_participants=3')

# A body naming a key no row has: Model.save() inserts a copy with the validated values and the period the path names stays. auditlog's
# pre_save finds no row under the new key and logs the whole row as an update (indexed through the actor), post_save logs the create.
COPY_PERIOD, COPY_SERVICE = 'a1000000-0000-4000-8000-000000985090', 'a1000000-0000-4000-8000-000000985091'


def copied_period(actor, name, label, body, values, new_id, *, links=None, status_fee=None):
    base = STATE['periods'][label]
    row = {**base, **values, 'id': new_id}
    fee = repr(float(values['fee'])) if 'fee' in values else base['fee']
    changes = {'camps_and_retreats_period_id': ['None', new_id], 'camps_and_retreats': ['None', CAMPS[row['camp']]['id']],
               'start_date': ['None', naive(row['start'])], 'end_date': ['None', naive(row['end'])], 'fee': ['None', fee],
               'created_at': ['None', naive(row['created'])]}
    for field, value in (('title', row['title']), ('description', row['description']), ('max_participants', row['max'])):
        if value is not None:
            changes[field] = ['None', str(value)]
    events = [audit('CampsAndRetreatsPeriod', new_id, 1, changes, OWN if actor == 'owner' else None,
                    'actor.sport_association' if actor == 'owner' else None),
              audit('CampsAndRetreatsPeriod', new_id, 0, changes, CAMPS[row['camp']]['association'], PERIOD_PATH)]
    effect = {'actor': actor, 'rows': {PERIOD: {new_id: period_row(row)}}, 'audit': events}
    STATE['periods']['copy_' + name], STATE['links']['copy_' + name] = row, links or []
    if links:
        effect['links'] = {PERIOD_LINK: {'add': [[new_id, SERVICES[s]['id']] for s in sorted(links)], 'remove': []}}
    response = {'message': 'Updated successfully', 'period': period_data('copy_' + name)}
    del STATE['periods']['copy_' + name], STATE['links']['copy_' + name]
    add(name, actor, period_path(label, 'update'), 200, response, method='PATCH', body=body, effect=effect)


copied_period('owner', 'update-new-key-copy', 'august', {'camps_and_retreats_period_id': COPY_PERIOD, 'title': 'Agosto bis', 'fee': '21',
                                                         'camps_and_Retreats_period_services': [SERVICES['bus']['id']]},
              {'title': 'Agosto bis', 'fee': '21.00'}, COPY_PERIOD, links=['bus'])
add('update-new-key-taken', 'owner', period_path('august', 'update'), 400, {'error': repr_detail([('camps_and_retreats_period_id', [
    ('camps and retreats period with this camps and retreats period id already exists.', 'unique')])])}, method='PATCH',
    body={'camps_and_retreats_period_id': PERIODS['july']['id']})
add('update-new-key-invalid', 'owner', period_path('august', 'update'), 400, {'error': repr_detail([('camps_and_retreats_period_id', [UUID_INVALID])])},
    method='PATCH', body={'camps_and_retreats_period_id': 'x'})
add('update-same-key-no-copy', 'owner', period_path('august', 'update'), 200,
    {'message': 'Updated successfully', 'period': period_data('august')}, method='PATCH',
    body={'camps_and_retreats_period_id': PERIODS['august']['id']},
    effect={'actor': 'owner', 'rows': {PERIOD: {PERIODS['august']['id']: period_row(STATE['periods']['august'])}}, 'audit': []})

# ---------------------------------------------------------------------------
# periods/services/add
# ---------------------------------------------------------------------------


def created_service(actor, name, values, body=None, *, form=None, explicit_id=None, explicit_created=None, default_fee=False,
                    form_type='application/x-www-form-urlencoded'):
    row = {'created': explicit_created, **values}
    key = explicit_id or '$new'
    fee_text = '0.0' if default_fee else row['fee']
    response = {'message': 'Added successfully', 'service': {
        'camps_and_retreats_period_service_id': key, 'title': row['title'], 'description': row['description'], 'fee': row['fee'],
        'created_at': drf(explicit_created) if explicit_created else '$now', 'camps_and_retreats_period': PERIODS[row['period']]['id'],
        'payment_category': CATEGORIES[row['category']]['id'] if row['category'] else None}}
    changes = {'camps_and_retreats_period_service_id': ['None', explicit_id or '$key'],
               'camps_and_retreats_period': ['None', PERIODS[row['period']]['id']], 'fee': ['None', fee_text],
               'payment_category': ['None', CATEGORIES[row['category']]['id'] if row['category'] else 'None'],
               'created_at': ['None', naive(explicit_created) if explicit_created else '$created']}
    if row['category'] is None:
        changes['payment_category'] = ['None', 'None']
    for field in ('title', 'description'):
        if row[field] is not None:
            changes[field] = ['None', row[field]]
    association = CAMPS[PERIODS[row['period']]['camp']]['association']
    events = [audit('CampsAndRetreatsPeriodsService', key, 0, changes, association, SERVICE_PATH)]
    if default_fee:
        events.append(audit('CampsAndRetreatsPeriodsService', key, 1, {'fee': ['0.00', '0.0']}, association, SERVICE_PATH))
    stored = service_row(row)
    if explicit_created:
        stored['created_at'] = drf(explicit_created)
    effect = {'actor': actor, 'rows': {SERVICE: {key: stored}}, 'audit': events}
    if form:
        add(name, actor, S_ADD, 201, response, method='POST', raw_body=form, content_type=form_type, effect=effect)
    else:
        add(name, actor, S_ADD, 201, response, method='POST', body=body, effect=effect)


period_id = lambda label: PERIODS[label]['id']
add('services-add-restricted-missing-permission', 'restricted_collaborator', S_ADD, 403, missing('create'), method='POST', body={})
add('services-add-wrong-method', 'owner', S_ADD, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('services-add-options', 'owner', S_ADD, 200, OPTIONS('Camps And Retreats Periods Services Add'), method='OPTIONS')
add('services-add-athlete-forbidden', 'athlete', S_ADD, 403, NOT_ASSOCIATION, method='POST', body={})
add('services-add-missing-period', 'owner', S_ADD, 400, {'error': repr_detail([('camps_and_retreats_period', [REQUIRED])])}, method='POST',
    body={'title': 'X'})
add('services-add-invalid', 'owner', S_ADD, 400, {'error': repr_detail([
    ('camps_and_retreats_period_service_id', [UUID_INVALID]), ('title', [BLANK]), ('fee', [NUMBER]), ('created_at', [DATETIME]),
    ('camps_and_retreats_period', [missing_pk(UNKNOWN)]), ('payment_category', [bad_uuid('abc')])])}, method='POST',
    body={'camps_and_retreats_period_service_id': 1.5, 'title': '', 'fee': '', 'created_at': 'ieri', 'camps_and_retreats_period': UNKNOWN,
          'payment_category': 'abc'})
add('services-add-existing-id', 'owner', S_ADD, 400, {'error': repr_detail([('camps_and_retreats_period_service_id', [
    ('camps and retreats periods service with this camps and retreats period service id already exists.', 'unique')])])}, method='POST',
    body={'camps_and_retreats_period_service_id': SERVICES['kayak']['id'], 'camps_and_retreats_period': period_id('sept')})
add('services-add-foreign-period-server-error', 'owner', S_ADD, 500, {'error': TENANT}, method='POST',
    body={'camps_and_retreats_period': period_id('abroad'), 'fee': 'x'})
add('services-add-foreign-category-server-error', 'owner', S_ADD, 500, {'error': TENANT}, method='POST',
    body={'camps_and_retreats_period': period_id('sept'), 'payment_category': CATEGORIES['foreign']['id']})
created_service('owner', 'services-add-owner', {'period': 'sept', 'title': 'Pranzo', 'description': 'Al sacco', 'fee': '8.50', 'category': 'own'},
                {'camps_and_retreats_period': period_id('sept'), 'title': 'Pranzo', 'description': 'Al sacco', 'fee': '8.5',
                 'payment_category': CATEGORIES['own']['id']})
created_service('full_collaborator', 'services-add-collaborator-defaults', {'period': 'sept', 'title': None, 'description': None,
                                                                            'fee': '0.00', 'category': None},
                {'camps_and_retreats_period': period_id('sept'), 'payment_category': None}, default_fee=True)
created_service('owner', 'services-add-form', {'period': 'sept', 'title': None, 'description': None, 'fee': '3.00', 'category': None},
                form=f'camps_and_retreats_period={period_id("sept")}&title=&description=&fee=3&payment_category=')
created_service('owner', 'services-add-explicit-id', {'period': 'sept', 'title': 'Cena', 'description': None, 'fee': '1234567.89',
                                                      'category': 'own'},
                {'camps_and_retreats_period_service_id': NEW_SERVICE, 'created_at': '2026-06-20T08:00:00+02:00',
                 'camps_and_retreats_period': period_id('sept'), 'title': 'Cena', 'fee': 1234567.89, 'payment_category': CATEGORIES['own']['id']},
                explicit_id=NEW_SERVICE, explicit_created='2026-06-20T06:00:00Z')
created_service('owner_without_association', 'services-add-unassociated-owner', {'period': 'abroad', 'title': None, 'description': None,
                                                                                  'fee': '3.10', 'category': 'foreign'},
                {'camps_and_retreats_period': period_id('abroad'), 'fee': '3.1', 'payment_category': CATEGORIES['foreign']['id']})

# ---------------------------------------------------------------------------
# periods/services/<uid>/update
# ---------------------------------------------------------------------------
SERVICE_FIELDS = (('camps_and_retreats_period', 'period_id'), ('title', 'title_text'), ('description', 'description_text'), ('fee', 'fee'),
                  ('payment_category', 'category_id'))


def service_audit_view(row):
    return {'period_id': PERIODS[row['period']]['id'], 'title_text': text(row['title']), 'description_text': text(row['description']),
            'fee': row['fee'], 'category_id': CATEGORIES[row['category']]['id'] if row['category'] else 'None'}


def dumps(value):
    return json.dumps(value, sort_keys=True)


def hidden_payment(payment, amount, notes):
    """auditlog reads the stored row through the soft-delete, group-aware manager, finds none and diffs the instance against None:
    every field as on a create, action update, indexed through the entry's own sport_association."""
    changes = {
        'payment_id': payment['id'], 'sport_association': OWN, 'user': 'None', 'associate': 'None', 'instructor': 'None',
        'supplier': 'None', 'invoice': 'None', 'group': 'None', 'signature': 'None', 'custom_accounts': 'None', 'payment_date': 'None',
        'payment_category': CATEGORIES[payment['category']]['id'], 'subject': str(payment['subject']), 'description': payment['description'],
        'paid': 'False', 'type': 'default', 'expense': 'False', 'archived': 'False', 'deleted': str(bool(payment.get('deleted'))), 'meta': 'null', 'course': 'null',
        'creation_date': naive(payment['created']), 'amount': amount, 'notes': notes, 'meta_payment_categories': dumps(payment['meta'])}
    return audit('Payment', payment['id'], 1, {field: ['None', value] for field, value in changes.items()}, OWN,
                 'changes_json.sport_association')


def payment_effects(label, rows, events, other_group=False):
    """The view's walk over the unpaid subscription periods of the service's period."""
    service = STATE['services'][label]
    for sp in SUBSCRIPTION_PERIODS.values():
        if sp['period'] != service['period'] or sp['payment'] is None or STATE['payments'][sp['payment']]['paid']:
            continue
        payment = STATE['payments'][sp['payment']]
        association = OWN
        for item in payment['meta']:
            if item['camps_and_retreats_period_service_id'] == service['id']:
                old = dumps(payment['meta'])
                item['amount'] = float(service['fee'])
                item['title'] = service['title']
                item['payment_category_id'] = CATEGORIES[service['category']]['id'] if service['category'] else 'None'
                if payment.get('deleted') or other_group:
                    events.append(hidden_payment(payment, payment['amount'], payment['notes']))
                elif dumps(payment['meta']) != old:
                    events.append(audit('Payment', payment['id'], 1, {'meta_payment_categories': [old, dumps(payment['meta'])]}, association,
                                        'direct'))
        period = STATE['periods'][sp['period']]
        camp = CAMPS[SUBSCRIPTIONS[sp['subscription']]['camp']]
        notes = f"Iscrizione al periodo {period['title']} del campo {camp['title']}"
        notes += f"\n{camp['description']}"
        amount = 0
        notes += '\n'
        for item in payment['meta']:
            notes += f"\n{item['title']} - {item['amount']}€"
            amount += item['amount']
        amount += float(period['fee'])
        changes = {}
        if repr(amount) != payment['amount']:
            changes['amount'] = [payment['amount'], repr(amount)]
        if notes != payment['notes']:
            changes['notes'] = [text(payment['notes']), notes]
        if payment.get('deleted') or other_group:
            events.append(hidden_payment(payment, repr(amount), notes))
        elif changes:
            events.append(audit('Payment', payment['id'], 1, changes, association, 'direct'))
        payment['amount'], payment['notes'] = f'{amount:.2f}', notes
        rows.setdefault(PAYMENT, {})[payment['id']] = {'amount': payment['amount'], 'notes': notes,
                                                       'meta_payment_categories': copy.deepcopy(payment['meta'])}


def updated_service(actor, name, label, body, values, *, form=None, walk=True, headers=None, form_type='application/x-www-form-urlencoded'):
    row = STATE['services'][label]
    before = service_audit_view(row)
    row.update(values)
    changes = stored_changes(before, service_audit_view(row), SERVICE_FIELDS)
    events = []
    if changes:
        events.append(audit('CampsAndRetreatsPeriodsService', row['id'], 1, changes, CAMPS[PERIODS[row['period']]['camp']]['association'],
                            SERVICE_PATH))
    rows = {SERVICE: {row['id']: service_row(row)}}
    if walk:
        payment_effects(label, rows, events, other_group=bool(headers))
    effect = {'actor': actor, 'rows': rows, 'audit': events}
    response = {'message': 'Updated successfully', 'period': service_data(label)}
    if form:
        add(name, actor, service_path(label, 'update'), 200, response, method='PATCH', raw_body=form,
            content_type=form_type, effect=effect)
    else:
        add(name, actor, service_path(label, 'update'), 200, response, method='PATCH', body=body, headers=headers, effect=effect)


add('services-update-restricted-missing-permission', 'restricted_collaborator', service_path('kayak', 'update'), 403, missing('update'),
    method='PATCH', body={})
add('services-update-wrong-method', 'owner', service_path('kayak', 'update'), 405, {'detail': 'Method "GET" not allowed.'})
add('services-update-options', 'owner', service_path('kayak', 'update'), 200, OPTIONS('Camps And Retreats Periods Services Update'),
    method='OPTIONS')
add('services-update-malformed', 'owner', service_path('abc', 'update'), 400, BADLY_FORMED, method='PATCH', body={})
add('services-update-athlete-forbidden', 'athlete', service_path('kayak', 'update'), 403, NOT_ASSOCIATION, method='PATCH', body={})
add('services-update-unknown', 'owner', service_path(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={})
add('services-update-foreign-forbidden', 'owner', service_path('abroad', 'update'), 403, {'detail': 'You are not allowed editing this.'},
    method='PATCH', body={})
add('services-update-unassociated-owner-server-error', 'owner_without_association', service_path('kayak', 'update'), 500, NO_ASSOCIATION,
    method='PATCH', body={})
add('services-update-invalid', 'owner', service_path('kayak', 'update'), 400, {'error': repr_detail([
    ('title', [too_long(255)]), ('fee', [NULL]), ('camps_and_retreats_period', [NULL]),
    ('payment_category', [('Incorrect type. Expected pk value, received bool.', 'incorrect_type')])])}, method='PATCH',
    body={'title': 'z' * 256, 'fee': None, 'camps_and_retreats_period': None, 'payment_category': False})
add('services-update-foreign-category-forbidden', 'owner', service_path('kayak', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'payment_category': CATEGORIES['foreign']['id']})
add('services-update-foreign-period-forbidden', 'full_collaborator', service_path('kayak', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'camps_and_retreats_period': period_id('abroad'), 'fee': 'x'})
updated_service('owner', 'services-update-owner-payments', 'kayak', {'fee': '15', 'title': 'Kayak doppio'},
                {'fee': '15.00', 'title': 'Kayak doppio'})
updated_service('full_collaborator', 'services-update-collaborator-payments', 'bus', {'description': 'Andata', 'payment_category': None},
                {'description': 'Andata'})
updated_service('owner', 'services-update-move-period-recomputes', 'snack', {'camps_and_retreats_period': period_id('july')},
                {'period': 'july'})
updated_service('owner', 'services-update-form', 'kayak', None, {'fee': '16.00', 'category': None},
                form='fee=16&payment_category=')
updated_service('owner', 'services-update-multipart-with-file', 'kayak', None, {'title': 'Kayak con allegato'},
                form=multipart(('title', None, 'Kayak con allegato'), ('allegato', 'a.txt', 'dati'))[0], form_type=multipart()[1])
add('services-update-multipart-file-as-fee', 'owner', service_path('kayak', 'update'), 400, {'error': repr_detail([('fee', [NUMBER])])},
    method='PATCH', raw_body=multipart(('fee', 'f.txt', '3'))[0], content_type=multipart()[1])
# The walk also saves a soft-deleted payment: one update entry per save, each with every field against None.
updated_service('owner', 'services-update-deleted-payment', 'canoe', {'fee': '9.50', 'title': 'Canoa doppia'},
                {'fee': '9.50', 'title': 'Canoa doppia'})
# With X-Group-Id the payment manager is group-aware: a payment outside that group is saved and audited the same way.
updated_service('owner', 'services-update-other-group-payment', 'umbrella', {'fee': '6.00'}, {'fee': '6.00'},
                headers={'X-Group-Id': IDS['own_group']})
def copied_service(actor, name, label, body, values, new_id):
    base = STATE['services'][label]
    row = {**base, **values, 'id': new_id}
    changes = {'camps_and_retreats_period_service_id': ['None', new_id], 'camps_and_retreats_period': ['None', PERIODS[row['period']]['id']],
               'fee': ['None', row['fee']], 'payment_category': ['None', CATEGORIES[row['category']]['id'] if row['category'] else 'None'],
               'created_at': ['None', naive(row['created'])]}
    for field in ('title', 'description'):
        if row[field] is not None:
            changes[field] = ['None', row[field]]
    events = [audit('CampsAndRetreatsPeriodsService', new_id, 1, changes, OWN if actor == 'owner' else None,
                    'actor.sport_association' if actor == 'owner' else None),
              audit('CampsAndRetreatsPeriodsService', new_id, 0, changes, CAMPS[PERIODS[row['period']]['camp']]['association'], SERVICE_PATH)]
    rows = {SERVICE: {new_id: service_row(row)}}
    # The walk reads the copy's key: no meta entry names it, the notes and amounts are recomputed.
    STATE['services']['copy_' + name] = row
    payment_effects('copy_' + name, rows, events)
    response = {'message': 'Updated successfully', 'period': service_data('copy_' + name)}
    del STATE['services']['copy_' + name]
    add(name, actor, service_path(label, 'update'), 200, response, method='PATCH', body=body, effect={'actor': actor, 'rows': rows, 'audit': events})


copied_service('owner', 'services-update-new-key-copy', 'bus', {'camps_and_retreats_period_service_id': COPY_SERVICE, 'title': 'Corriera',
                                                                'fee': '4'},
               {'title': 'Corriera', 'fee': '4.00'}, COPY_SERVICE)
add('services-update-new-key-taken', 'owner', service_path('bus', 'update'), 400, {'error': repr_detail([(
    'camps_and_retreats_period_service_id', [('camps and retreats periods service with this camps and retreats period service id already exists.',
                                              'unique')])])}, method='PATCH',
    body={'camps_and_retreats_period_service_id': SERVICES['kayak']['id']})

# A fee that pushes a payment's recomputed amount past numeric(9,2): the meta saves before it stay, the failing save is audited
# (auditlog writes before the UPDATE) and then raises; later payments are not walked.
def overflow_service(actor, name, label, fee):
    row = STATE['services'][label]
    before = service_audit_view(row)
    row.update({'fee': fee})
    events = [audit('CampsAndRetreatsPeriodsService', row['id'], 1, stored_changes(before, service_audit_view(row), SERVICE_FIELDS),
                    CAMPS[PERIODS[row['period']]['camp']]['association'], SERVICE_PATH)]
    rows = {SERVICE: {row['id']: service_row(row)}}
    service = STATE['services'][label]
    for sp in SUBSCRIPTION_PERIODS.values():
        if sp['period'] != service['period'] or sp['payment'] is None or STATE['payments'][sp['payment']]['paid']:
            continue
        payment = STATE['payments'][sp['payment']]
        for item in payment['meta']:
            if item['camps_and_retreats_period_service_id'] == service['id']:
                old = dumps(payment['meta'])
                item['amount'] = float(service['fee'])
                item['title'] = service['title']
                item['payment_category_id'] = CATEGORIES[service['category']]['id'] if service['category'] else 'None'
                if dumps(payment['meta']) != old:
                    events.append(audit('Payment', payment['id'], 1, {'meta_payment_categories': [old, dumps(payment['meta'])]}, OWN, 'direct'))
        period = STATE['periods'][sp['period']]
        camp = CAMPS[SUBSCRIPTIONS[sp['subscription']]['camp']]
        notes = f"Iscrizione al periodo {period['title']} del campo {camp['title']}"
        notes += f"\n{camp['description']}"
        amount = 0
        notes += '\n'
        for item in payment['meta']:
            notes += f"\n{item['title']} - {item['amount']}€"
            amount += item['amount']
        amount += float(period['fee'])
        changes = {}
        if repr(amount) != payment['amount']:
            changes['amount'] = [payment['amount'], repr(amount)]
        if notes != payment['notes']:
            changes['notes'] = [text(payment['notes']), notes]
        if changes:
            events.append(audit('Payment', payment['id'], 1, changes, OWN, 'direct'))
        rows.setdefault(PAYMENT, {})[payment['id']] = {'meta_payment_categories': copy.deepcopy(payment['meta'])}
        if amount >= 10 ** 7:
            break
        payment['amount'], payment['notes'] = f'{amount:.2f}', notes
        rows[PAYMENT][payment['id']].update({'amount': payment['amount'], 'notes': notes})
    add(name, actor, service_path(label, 'update'), 500, {'error': 'numeric field overflow\nDETAIL:  A field with precision 9, scale 2 must round to an absolute value less than 10^7.\n'},
        method='PATCH', body={'fee': fee}, effect={'actor': actor, 'rows': rows, 'audit': events})


overflow_service('owner', 'services-update-amount-overflow', 'kayak', '9999999.99')

# The walk raises on the payment without metas after the service was saved.
guide = STATE['services']['guide']
guide_before = service_audit_view(guide)
guide['title'] = 'Guida alpina'
add('services-update-null-metas-server-error', 'owner', service_path('guide', 'update'), 500, {'error': "'NoneType' object is not iterable"},
    method='PATCH', body={'title': 'Guida alpina'},
    effect={'actor': 'owner', 'rows': {SERVICE: {guide['id']: service_row(guide)}},
            'audit': [audit('CampsAndRetreatsPeriodsService', guide['id'], 1, stored_changes(guide_before, service_audit_view(guide), SERVICE_FIELDS),
                            OWN, SERVICE_PATH)]})

# ---------------------------------------------------------------------------
# periods/services/<uid>/delete
# ---------------------------------------------------------------------------
add('services-delete-restricted-missing-permission', 'restricted_collaborator', service_path('umbrella', 'delete'), 403, missing('delete'),
    method='DELETE')
add('services-delete-wrong-method', 'owner', service_path('umbrella', 'delete'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST',
    body={})
add('services-delete-options', 'owner', service_path('umbrella', 'delete'), 200, OPTIONS('Camps And Retreats Periods Services Delete'),
    method='OPTIONS')
add('services-delete-malformed', 'owner', service_path('abc', 'delete'), 400, BADLY_FORMED, method='DELETE')
add('services-delete-athlete-forbidden', 'athlete', service_path('umbrella', 'delete'), 403, NOT_ASSOCIATION, method='DELETE')
add('services-delete-foreign-forbidden', 'owner', service_path('abroad', 'delete'), 403, {'detail': 'You are not allowed deleting this.'},
    method='DELETE')
add('services-delete-unassociated-owner-server-error', 'owner_without_association', service_path('umbrella', 'delete'), 500, NO_ASSOCIATION,
    method='DELETE')
DELETED = {'message': 'Deleted successfully'}
add('services-delete-owner', 'owner', service_path('umbrella', 'delete'), 200, DELETED, method='DELETE',
    effect={'actor': 'owner', 'rows': {SERVICE: {SERVICES['umbrella']['id']: None}},
            'links': {PERIOD_LINK: {'add': [], 'remove': [[PERIODS['july']['id'], SERVICES['umbrella']['id']]]},
                      SUBPERIOD_LINK: {'add': [], 'remove': [[SUBSCRIPTION_PERIODS['july']['id'], SERVICES['umbrella']['id']]]}},
            'audit': [audit('CampsAndRetreatsPeriodsService', SERVICES['umbrella']['id'], 2, '$deleted', OWN, 'actor.sport_association')]})
del STATE['services']['umbrella']
STATE['links']['july'].remove('umbrella')
add('services-delete-again-not-found', 'owner', service_path('umbrella', 'delete'), 404, NOT_FOUND, method='DELETE')

# ---------------------------------------------------------------------------
# form bodies: repeated services keys (getlist) and multipart bodies with file parts (a mutable copy)
# ---------------------------------------------------------------------------
SVC = lambda label: SERVICES[label]['id']
FORM_DATES = f'start_date=2026-10-01&end_date=2026-10-05&camps_and_retreats={camp_id("camp")}'
add('add-form-services-list-server-error', 'owner', P_ADD, 500, {'error': M2M_CREATE}, method='POST',
    raw_body=f'{FORM_DATES}&camps_and_Retreats_period_services={SVC("kayak")}&camps_and_Retreats_period_services={SVC("bus")}',
    content_type='application/x-www-form-urlencoded')
add('add-form-services-foreign-server-error', 'owner', P_ADD, 500, {'error': TENANT}, method='POST',
    raw_body=f'{FORM_DATES}&camps_and_Retreats_period_services={SVC("kayak")}&camps_and_Retreats_period_services={SVC("abroad")}',
    content_type='application/x-www-form-urlencoded')
add('add-form-services-invalid-item', 'owner', P_ADD, 400, {'error': repr_detail([('camps_and_Retreats_period_services', [bad_uuid('x')])])},
    method='POST', raw_body=f'{FORM_DATES}&camps_and_Retreats_period_services={SVC("kayak")}&camps_and_Retreats_period_services=x',
    content_type='application/x-www-form-urlencoded')
MP_DATES = (('start_date', None, '2026-10-01'), ('end_date', None, '2026-10-05'), ('camps_and_retreats', None, camp_id('camp')))
add('add-multipart-file-server-error', 'owner', P_ADD, 500, {'error': M2M_CREATE}, method='POST',
    raw_body=multipart(*MP_DATES, ('allegato', 'a.txt', 'dati'))[0], content_type=multipart()[1])
add('add-multipart-file-blank-fee-is-null', 'owner', P_ADD, 400, {'error': repr_detail([('fee', [NULL])])}, method='POST',
    raw_body=multipart(*MP_DATES, ('fee', None, ''), ('allegato', 'a.txt', 'dati'))[0], content_type=multipart()[1])
add('add-multipart-file-as-title', 'full_collaborator', P_ADD, 400, {'error': repr_detail([('title', [NOT_STRING])])}, method='POST',
    raw_body=multipart(*MP_DATES, ('title', 'titolo.txt', 'x'))[0], content_type=multipart()[1])
add('add-multipart-file-as-date', 'owner', P_ADD, 400, {'error': repr_detail([('start_date', [DATETIME])])}, method='POST',
    raw_body=multipart(('start_date', 'inizio.txt', 'x'), ('end_date', None, '2026-10-05'), ('camps_and_retreats', None, camp_id('camp')))[0],
    content_type=multipart()[1])
add('add-multipart-without-file-blank-fee-server-error', 'owner', P_ADD, 500, {'error': 'This QueryDict instance is immutable'}, method='POST',
    raw_body=multipart(*MP_DATES, ('fee', None, ''))[0], content_type=multipart()[1])
add('update-multipart-file-blank-fee-is-null', 'owner', period_path('august', 'update'), 400, {'error': repr_detail([('fee', [NULL])])},
    method='PATCH', raw_body=multipart(('fee', None, ''), ('allegato', 'a.txt', 'dati'))[0], content_type=multipart()[1])
add('update-multipart-file-as-max-participants', 'owner', period_path('august', 'update'), 400,
    {'error': repr_detail([('max_participants', [INTEGER])])}, method='PATCH',
    raw_body=multipart(('max_participants', 'n.txt', '3'))[0], content_type=multipart()[1])
updated_period('owner', 'update-multipart-with-file', 'august', None, {'title': 'Agosto con allegato'},
               form=multipart(('title', None, 'Agosto con allegato'), ('allegato', 'a.txt', 'dati'))[0], form_type=multipart()[1])
updated_period('owner', 'update-form-services-repeated', 'august', None, {},
               form=f'camps_and_Retreats_period_services={SVC("kayak")}&camps_and_Retreats_period_services={SVC("bus")}',
               links=['bus', 'kayak'])
updated_period('owner', 'update-form-services-single', 'august', None, {},
               form=f'camps_and_Retreats_period_services={SVC("kayak")}', links=['kayak'])
updated_period('owner', 'update-form-services-none-partial', 'august', None, {'title': 'Agosto due'}, form='title=Agosto+due')
updated_period('owner', 'update-services-cleared-again', 'august', {'camps_and_Retreats_period_services': []}, {}, links=[])
add('services-add-multipart-file-as-fee', 'owner', S_ADD, 400, {'error': repr_detail([('fee', [NUMBER])])}, method='POST',
    raw_body=multipart(('camps_and_retreats_period', None, period_id('sept')), ('fee', 'f.txt', '3'))[0], content_type=multipart()[1])
created_service('owner', 'services-add-multipart-with-file', {'period': 'sept', 'title': 'Con allegato', 'description': None, 'fee': '4.00',
                                                              'category': None},
                form=multipart(('camps_and_retreats_period', None, period_id('sept')), ('title', None, 'Con allegato'), ('fee', None, '4'),
                               ('payment_category', None, ''), ('allegato', 'a.txt', 'dati'))[0], form_type=multipart()[1])

# ---------------------------------------------------------------------------
# periods/<uid>/delete
# ---------------------------------------------------------------------------
add('delete-restricted-missing-permission', 'restricted_collaborator', period_path('june', 'delete'), 403, missing('delete'), method='DELETE')
add('delete-wrong-method', 'owner', period_path('june', 'delete'), 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH', body={})
add('delete-options', 'owner', period_path('june', 'delete'), 200, OPTIONS('Camps And Retreats Periods Delete'), method='OPTIONS')
add('delete-malformed', 'athlete', period_path('abc', 'delete'), 400, BADLY_FORMED, method='DELETE')
add('delete-athlete-forbidden', 'athlete', period_path('june', 'delete'), 403, NOT_ASSOCIATION, method='DELETE')
add('delete-unknown', 'owner', period_path(UNKNOWN, 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-foreign-forbidden', 'owner', period_path('abroad', 'delete'), 403, {'detail': 'You are not allowed deleting this.'}, method='DELETE')
add('delete-unassociated-owner-server-error', 'owner_without_association', period_path('june', 'delete'), 500, NO_ASSOCIATION, method='DELETE')
add('delete-owner-cascade', 'owner', period_path('june', 'delete'), 200, DELETED, method='DELETE',
    effect={'actor': 'owner', 'rows': {PERIOD: {PERIODS['june']['id']: None}, SERVICE: {SERVICES['tent']['id']: None},
                                       SUBPERIOD: {SUBSCRIPTION_PERIODS['june']['id']: None}},
            'links': {PERIOD_LINK: {'add': [], 'remove': [[PERIODS['june']['id'], SERVICES['tent']['id']]]},
                      SUBPERIOD_LINK: {'add': [], 'remove': [[SUBSCRIPTION_PERIODS['june']['id'], SERVICES['tent']['id']]]}},
            'audit': [audit('CampsAndRetreatsPeriodsService', SERVICES['tent']['id'], 2, '$deleted', OWN, 'actor.sport_association'),
                      audit('CampsAndRetreatsSubscriptionPeriod', SUBSCRIPTION_PERIODS['june']['id'], 2, '$deleted', OWN, 'actor.sport_association'),
                      audit('CampsAndRetreatsPeriod', PERIODS['june']['id'], 2, '$deleted', OWN, 'actor.sport_association')]})
add('delete-collaborator-unindexed', 'full_collaborator', period_path('august', 'delete'), 200, DELETED, method='DELETE',
    effect={'actor': 'full_collaborator', 'rows': {PERIOD: {PERIODS['august']['id']: None}},
            'audit': [audit('CampsAndRetreatsPeriod', PERIODS['august']['id'], 2, '$deleted', None, None)]})
add('delete-again-not-found', 'owner', period_path('june', 'delete'), 404, NOT_FOUND, method='DELETE')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Camps and retreats periods and period services authored sequence with audit effects',
                              'ids': IDS, 'categories': CATEGORIES, 'camps': CAMPS, 'periods': PERIODS, 'services': SERVICES,
                              'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'subscription_periods': SUBSCRIPTION_PERIODS,
                              'period_links': PERIOD_LINKS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
