"""Author the camps-and-retreats list, add, info, update and delete HTTP sequence (camps-core.md).

Function views in BE/application/views/camp_and_retreats_views.py, each
@api_view with one method:
- list (GET): is_sport_association(raise_exception=False) (403 {'error'}), then
  CampsAndRetreatsSerializer(many=True) over the camps of
  request.user.sport_association (no ordering; a user without an association
  is an unhandled DoesNotExist, the 500 page).
- add (POST): the role check, then inside try: request.data['sport_association']
  = the association pk (the association lookup runs first and fails with
  500 {'error'}; assigning into a form QueryDict, a list, a string, a number or
  None raises, 500 {'error': str(e)}), CampsAndRetreatsSerializer(data=...)
  (validation: 400 {'error': str(ValidationError)}), serializer.save() creates
  the row and the view saves it again (no second audit entry).
- <uid>/info (GET): is_valid_uuid first (400 list). Users that are not
  associations or collaborators get CampsAndRetreatsPublicInfoSerializer of any
  camp (404 {'error'} when missing); others get the camp (404), its association
  against request.user.sport_association (403 PermissionDenied re-raised; no
  association: 500 {'error'}), then CampsAndRetreatsInfoSerializer with the
  periods by start_date (CampsAndRetreatsPeriodInfoSerializer).
- <uid>/update (PATCH) and <uid>/delete (DELETE): is_valid_uuid, the role check,
  the camp and the association check, then the partial serializer save (the
  tenant scope's PermissionDenied on sport_association is re-raised: 403) or the
  collector delete of the camp, its periods, their services, the subscription
  periods of those periods and of the camp's subscriptions, the subscriptions
  and every link row of the deleted rows.
The camp's delete entry is indexed through changes_json.sport_association,
the cascaded ones through the actor's association. Run with ``python3 -I``.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).with_name('camps_core_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, DELETED_ASSOCIATION = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{987001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'
UNKNOWN = next(ids)
NEW_CAMP = next(ids)
# The message seed's associations (TESTS/adapters/django/message_reads.py).
ASSOCIATIONS = {OWN: {'denomination': 'Message reads own', 'username': 'message-reads-700001'},
                FOREIGN: {'denomination': 'Message reads foreign', 'username': 'message-reads-700007'}}

CATEGORIES = {'own': {'id': next(ids), 'name': 'Servizi campo', 'created': '2026-05-01T08:00:00Z', 'association': OWN}}
CAMPS = {
    'summer': {'id': next(ids), 'title': 'Campo estivo è', 'description': 'Mare e monti', 'association': OWN},
    'winter': {'id': next(ids), 'title': None, 'description': None, 'association': OWN},
    'spring': {'id': next(ids), 'title': 'Primavera', 'description': 'Colline', 'association': OWN},
    'autumn': {'id': next(ids), 'title': 'Autunno', 'description': None, 'association': OWN},
    'abroad': {'id': next(ids), 'title': 'Campo estero', 'description': 'Lontano', 'association': FOREIGN},
}
PERIODS = {
    'july': {'id': next(ids), 'camp': 'summer', 'start': '2026-07-01T08:00:00Z', 'end': '2026-07-15T18:00:00Z', 'title': 'Luglio',
             'description': 'Prima settimana', 'fee': '100.00', 'max': 20, 'created': '2026-06-01T10:00:00Z'},
    'june': {'id': next(ids), 'camp': 'summer', 'start': '2026-06-10T07:00:00Z', 'end': '2026-06-20T07:00:00Z', 'title': None,
             'description': None, 'fee': '55.50', 'max': None, 'created': '2026-06-03T10:00:00.250000Z'},
    'october': {'id': next(ids), 'camp': 'autumn', 'start': '2026-10-01T07:00:00Z', 'end': '2026-10-05T07:00:00Z', 'title': 'Ottobre',
                'description': None, 'fee': '10.00', 'max': 5, 'created': '2026-06-04T10:00:00Z'},
    'abroad': {'id': next(ids), 'camp': 'abroad', 'start': '2026-07-01T07:00:00Z', 'end': '2026-07-02T07:00:00Z', 'title': 'Estero',
               'description': None, 'fee': '1.00', 'max': None, 'created': '2026-06-05T10:00:00Z'},
}
SERVICES = {
    'kayak': {'id': next(ids), 'period': 'july', 'title': 'Kayak', 'description': 'Mezza giornata', 'fee': '12.50', 'category': 'own',
              'created': '2026-06-05T09:00:00Z'},
    'bus': {'id': next(ids), 'period': 'july', 'title': 'Bus', 'description': None, 'fee': '30.00', 'category': None,
            'created': '2026-06-06T09:00:00.000001Z'},
    'tent': {'id': next(ids), 'period': 'june', 'title': 'Tenda', 'description': None, 'fee': '7.00', 'category': None,
             'created': '2026-06-08T09:00:00Z'},
    'guide': {'id': next(ids), 'period': 'october', 'title': 'Guida', 'description': None, 'fee': '15.00', 'category': 'own',
              'created': '2026-06-09T09:00:00Z'},
    'abroad': {'id': next(ids), 'period': 'abroad', 'title': 'Estero', 'description': None, 'fee': '3.00', 'category': None,
               'created': '2026-06-11T09:00:00Z'},
}
SUBSCRIPTIONS = {'summer': {'id': next(ids), 'camp': 'summer', 'created': '2026-06-12T09:00:00Z'},
                 'autumn': {'id': next(ids), 'camp': 'autumn', 'created': '2026-06-12T10:00:00.500000Z'}}
PAYMENTS = {
    'july': {'id': next(ids), 'amount': '112.50', 'paid': False, 'subject': 1, 'description': 'Iscrizione', 'category': 'own',
             'notes': None, 'meta': None, 'created': '2026-06-13T09:00:00Z'},
}
SUBSCRIPTION_PERIODS = {
    'july': {'id': next(ids), 'period': 'july', 'subscription': 'summer', 'payment': 'july', 'services': ['kayak']},
    'june': {'id': next(ids), 'period': 'june', 'subscription': 'summer', 'payment': None, 'services': ['tent']},
    # A period of another camp under this camp's subscription, and the reverse.
    'cross': {'id': next(ids), 'period': 'october', 'subscription': 'summer', 'payment': None, 'services': ['guide', 'kayak']},
    'reverse': {'id': next(ids), 'period': 'june', 'subscription': 'autumn', 'payment': None, 'services': []},
    'october': {'id': next(ids), 'period': 'october', 'subscription': 'autumn', 'payment': None, 'services': ['guide']},
}
PERIOD_LINKS = {'july': ['kayak', 'bus'], 'june': ['tent'], 'october': ['guide', 'kayak']}

CAMP, PERIOD, SERVICE, SUBSCRIPTION, SUBPERIOD = (
    'application.CampsAndRetreats', 'application.CampsAndRetreatsPeriod', 'application.CampsAndRetreatsPeriodsService',
    'application.CampsAndRetreatsSubscription', 'application.CampsAndRetreatsSubscriptionPeriod')
PERIOD_LINK = 'application.CampsAndRetreatsPeriod_camps_and_Retreats_period_services'
SUBPERIOD_LINK = 'application.CampsAndRetreatsSubscriptionPeriod_camps_and_retreats_period_services'

STATE = {'camps': {label: dict(row) for label, row in CAMPS.items()}}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'camps-core-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
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


def repr_detail(errors):
    """str(ValidationError(serializer.errors)): the dict repr of ErrorDetail lists."""
    return '{' + ', '.join(f'{field!r}: [' + ', '.join(f'ErrorDetail(string={message!r}, code={code!r})' for message, code in items) + ']'
                           for field, items in errors) + '}'


NULL, BLANK = ('This field may not be null.', 'null'), ('This field may not be blank.', 'blank')
UUID_INVALID, NOT_STRING = ('Must be a valid UUID.', 'invalid'), ('Not a valid string.', 'invalid')
missing_pk = lambda pk: (f'Invalid pk "{pk}" - object does not exist.', 'does_not_exist')
bad_uuid = lambda value: (f'“{value}” is not a valid UUID.', 'invalid')
too_long = lambda n: (f'Ensure this field has no more than {n} characters.', 'max_length')
UNIQUE = ('camps and retreats with this camps and retreats id already exists.', 'unique')
TENANT = 'You do not have permission to perform this action.'
OPTIONS = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
missing = lambda verb: {'detail': f'Missing permission: association.campsandretreats.{verb}'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
NO_SESSION = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
BADLY_FORMED = ['badly formed hexadecimal UUID string']
NOT_FOUND = {'error': 'Camp or retreat not found'}
NO_ASSOCIATION = {'error': 'SportAssociation matching query does not exist.'}
DESCRIPTION = 'This method is used to get a list of all camps and retreats'
FORM = 'application/x-www-form-urlencoded'
L, A = '/camps-and-retreats/list', '/camps-and-retreats/add'


def camp_path(label, verb):
    return f'/camps-and-retreats/{CAMPS[label]["id"] if label in CAMPS else label}/{verb}'


def camp_data(label):
    row = STATE['camps'][label]
    return {'camps_and_retreats_id': row['id'], 'title': row['title'], 'description': row['description'], 'sport_association': row['association']}


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
    """CampsAndRetreatsPeriodInfoSerializer: services newest first."""
    row = PERIODS[label]
    services = sorted((s for s, value in SERVICES.items() if value['period'] == label), key=lambda s: SERVICES[s]['created'], reverse=True)
    return {'camps_and_retreats_period_id': row['id'], 'start_date': moment(row['start']).strftime('%d/%m/%Y'),
            'end_date': moment(row['end']).strftime('%d/%m/%Y'), 'title': row['title'], 'description': row['description'],
            'fee': comma(row['fee']), 'max_participants': row['max'], 'created_at': drf(row['created']),
            'camps_and_retreats': CAMPS[row['camp']]['id'],
            'camps_and_Retreats_period_services': {'$unordered': [SERVICES[s]['id'] for s in PERIOD_LINKS.get(label, [])]},
            'services': [{'camps_and_retreats_period_service_id': SERVICES[s]['id'], 'payment_category_info': category_info(SERVICES[s]['category']),
                          'fee': comma(SERVICES[s]['fee']), 'title': SERVICES[s]['title'], 'description': SERVICES[s]['description'],
                          'created_at': drf(SERVICES[s]['created']), 'camps_and_retreats_period': row['id'],
                          'payment_category': CATEGORIES[SERVICES[s]['category']]['id'] if SERVICES[s]['category'] else None}
                         for s in services]}


def camp_info(label, public=False):
    periods = sorted((p for p, value in PERIODS.items() if value['camp'] == label), key=lambda p: PERIODS[p]['start'])
    data = {**camp_data(label), 'periods': [period_info(p) for p in periods]}
    if public:
        association = STATE['camps'][label]['association']
        data['sport_association'] = {'sport_association_id': association, **ASSOCIATIONS[association]}
    return {'data': data}


def audit(model, key, action, changes, association=None, path=None):
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'association': association, 'path': path}


def text(value):
    return 'None' if value is None else str(value)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
add('list-anonymous', 'anonymous', L, 401, ANONYMOUS)
add('list-admin-without-session', 'admin', L, 403, NO_SESSION)
add('list-restricted-missing-permission', 'restricted_collaborator', L, 403, missing('read'))
add('list-owner-scoped-athlete-denied', 'owner', L, 403, SCOPED_DENIED, headers=SCOPED)
add('list-wrong-method', 'owner', L, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('list-options', 'owner', L, 200, OPTIONS('Camps And Retreats List', DESCRIPTION), method='OPTIONS')
add('list-athlete-forbidden', 'athlete', L, 403, {'error': 'Only sport associations can view camps or retreats'})
add('list-unassociated-owner-server-error', 'owner_without_association', L, 500, SERVER)
OWN_CAMPS = [label for label, row in CAMPS.items() if row['association'] == OWN]
add('list-owner', 'owner', L, 200, {'data': [camp_data(label) for label in OWN_CAMPS]})
add('list-full-collaborator', 'full_collaborator', L, 200, {'data': [camp_data(label) for label in OWN_CAMPS]})

# ---------------------------------------------------------------------------
# <uid>/info
# ---------------------------------------------------------------------------
add('info-anonymous', 'anonymous', camp_path('summer', 'info'), 401, ANONYMOUS)
add('info-restricted-missing-permission', 'restricted_collaborator', camp_path('summer', 'info'), 403, missing('read'))
add('info-owner-scoped-athlete-denied', 'owner', camp_path('summer', 'info'), 403, SCOPED_DENIED, headers=SCOPED)
add('info-wrong-method', 'owner', camp_path('summer', 'info'), 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH', body={})
add('info-options', 'owner', camp_path('summer', 'info'), 200, OPTIONS('Camps And Retreats Info', DESCRIPTION), method='OPTIONS')
add('info-athlete-malformed', 'athlete', camp_path('abc', 'info'), 400, BADLY_FORMED)
add('info-owner-malformed', 'owner', camp_path('abc', 'info'), 400, BADLY_FORMED)
# camps-and-retreats/<str:uid>/info is declared before the periods routes.
add('info-periods-segment-malformed', 'owner', '/camps-and-retreats/periods/info', 400, BADLY_FORMED)
add('info-athlete-public', 'athlete', camp_path('summer', 'info'), 200, camp_info('summer', public=True))
add('info-athlete-public-foreign', 'athlete', camp_path('abroad', 'info'), 200, camp_info('abroad', public=True))
add('info-athlete-unknown', 'athlete', camp_path(UNKNOWN, 'info'), 404, NOT_FOUND)
add('info-unknown', 'owner', camp_path(UNKNOWN, 'info'), 404, NOT_FOUND)
add('info-foreign-forbidden', 'owner', camp_path('abroad', 'info'), 403, {'detail': 'You are not allowed to view this camp or retreat'})
add('info-unassociated-owner-server-error', 'owner_without_association', camp_path('summer', 'info'), 500, NO_ASSOCIATION)
add('info-unassociated-owner-unknown', 'owner_without_association', camp_path(UNKNOWN, 'info'), 404, NOT_FOUND)
add('info-owner', 'owner', camp_path('summer', 'info'), 200, camp_info('summer'))
add('info-collaborator-empty', 'full_collaborator', camp_path('winter', 'info'), 200, camp_info('winter'))
add('info-hex-uid', 'owner', camp_path(CAMPS['autumn']['id'].replace('-', '').upper(), 'info'), 200, camp_info('autumn'))

# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------
add('add-anonymous', 'anonymous', A, 401, ANONYMOUS, method='POST', body={})
add('add-admin-without-session', 'admin', A, 403, NO_SESSION, method='POST', body={})
add('add-restricted-missing-permission', 'restricted_collaborator', A, 403, missing('create'), method='POST', body={})
add('add-owner-scoped-athlete-denied', 'owner', A, 403, SCOPED_DENIED, method='POST', body={}, headers=SCOPED)
add('add-wrong-method', 'owner', A, 405, {'detail': 'Method "GET" not allowed.'})
add('add-options', 'owner', A, 200, OPTIONS('Camps And Retreats Add', 'This method is used to add a new camp or retreat'), method='OPTIONS')
add('add-athlete-forbidden', 'athlete', A, 403, {'error': 'Only sport associations can add camps or retreats'}, method='POST',
    body={'title': ''})
add('add-unassociated-owner-server-error', 'owner_without_association', A, 500, NO_ASSOCIATION, method='POST', body={'title': 'x'})
# The association lookup is evaluated before the assignment into request.data.
add('add-form-unassociated-owner-server-error', 'owner_without_association', A, 500, NO_ASSOCIATION, method='POST',
    raw_body='title=Form', content_type=FORM)
add('add-form-server-error', 'owner', A, 500, {'error': 'This QueryDict instance is immutable'}, method='POST',
    raw_body='title=Form', content_type=FORM)
add('add-list-body-server-error', 'owner', A, 500, {'error': 'list indices must be integers or slices, not str'}, method='POST', body=[])
add('add-string-body-server-error', 'owner', A, 500, {'error': "'str' object does not support item assignment"}, method='POST',
    body='title')
add('add-number-body-server-error', 'owner', A, 500, {'error': "'int' object does not support item assignment"}, method='POST', body=5)
add('add-float-body-server-error', 'owner', A, 500, {'error': "'float' object does not support item assignment"}, method='POST',
    body=1.5)
add('add-null-body-server-error', 'owner', A, 500, {'error': "'NoneType' object does not support item assignment"}, method='POST',
    raw_body='null', content_type='application/json')
add('add-invalid-fields', 'owner', A, 400, {'error': repr_detail([('camps_and_retreats_id', [UUID_INVALID]), ('title', [BLANK]),
                                                                    ('description', [too_long(2048)])])},
    method='POST', body={'camps_and_retreats_id': 'abc', 'title': '', 'description': 'x' * 2049})
add('add-invalid-types', 'full_collaborator', A, 400, {'error': repr_detail([('camps_and_retreats_id', [UUID_INVALID]),
                                                                              ('title', [too_long(255)]), ('description', [NOT_STRING])])},
    method='POST', body={'camps_and_retreats_id': 1.5, 'title': 'y' * 256, 'description': True})
add('add-existing-id', 'owner', A, 400, {'error': repr_detail([('camps_and_retreats_id', [UNIQUE])])}, method='POST',
    body={'camps_and_retreats_id': CAMPS['winter']['id'], 'title': 'Copia'})


def created_camp(actor, name, values, body, *, explicit_id=None):
    key = explicit_id or '$new'
    response = {'message': 'Camp or retreat added successfully',
                'camp_and_retreat': {'camps_and_retreats_id': key, 'title': values['title'], 'description': values['description'],
                                     'sport_association': OWN}}
    changes = {'camps_and_retreats_id': ['None', explicit_id or '$key'], 'sport_association': ['None', OWN]}
    for field in ('title', 'description'):
        if values[field] is not None:
            changes[field] = ['None', values[field]]
    effect = {'actor': actor, 'rows': {CAMP: {key: {'title': values['title'], 'description': values['description'],
                                                    'sport_association_id': OWN}}},
              'audit': [audit('CampsAndRetreats', key, 0, changes, OWN, 'direct')]}
    add(name, actor, A, 201, response, method='POST', body=body, effect=effect)


created_camp('owner', 'add-owner', {'title': 'Campo invernale', 'description': 'Neve è'},
             {'title': ' Campo invernale ', 'description': 'Neve è', 'ignored': 'x'})
# The view overwrites a sport_association of the body with the user's own.
created_camp('full_collaborator', 'add-full-collaborator-defaults', {'title': None, 'description': None},
             {'sport_association': FOREIGN})
created_camp('owner', 'add-explicit-id', {'title': '5', 'description': None},
             {'camps_and_retreats_id': NEW_CAMP.replace('-', '').upper(), 'title': 5, 'description': None}, explicit_id=NEW_CAMP)
add('add-explicit-id-again', 'owner', A, 400, {'error': repr_detail([('camps_and_retreats_id', [UNIQUE])])}, method='POST',
    body={'camps_and_retreats_id': NEW_CAMP})

# ---------------------------------------------------------------------------
# <uid>/update
# ---------------------------------------------------------------------------
CAMP_FIELDS = ('title', 'description', 'sport_association')


def updated_camp(actor, name, label, values, *, body=None, form=None, path_id=None):
    row = STATE['camps'][label]
    before = {'title': text(row['title']), 'description': text(row['description']), 'sport_association': row['association']}
    row.update(values)
    after = {'title': text(row['title']), 'description': text(row['description']), 'sport_association': row['association']}
    changes = {field: [before[field], after[field]] for field in CAMP_FIELDS if before[field] != after[field]}
    effect = {'actor': actor, 'rows': {CAMP: {row['id']: {'title': row['title'], 'description': row['description'],
                                                          'sport_association_id': row['association']}}},
              'audit': [audit('CampsAndRetreats', row['id'], 1, changes, row['association'], 'direct')] if changes else []}
    response = {'message': 'Camp or retreat updated successfully', 'camp_and_retreat': camp_data(label)}
    path = f'/camps-and-retreats/{path_id or row["id"]}/update'
    if form is not None:
        add(name, actor, path, 200, response, method='PATCH', raw_body=form, content_type=FORM, effect=effect)
    else:
        add(name, actor, path, 200, response, method='PATCH', body=body, effect=effect)


add('update-anonymous', 'anonymous', camp_path('spring', 'update'), 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', camp_path('spring', 'update'), 403, missing('update'),
    method='PATCH', body={})
add('update-wrong-method', 'owner', camp_path('spring', 'update'), 405, {'detail': 'Method "GET" not allowed.'})
add('update-options', 'owner', camp_path('spring', 'update'), 200, OPTIONS('Camps And Retreats Update'), method='OPTIONS')
add('update-athlete-malformed', 'athlete', camp_path('abc', 'update'), 400, BADLY_FORMED, method='PATCH', body={})
add('update-periods-segment-malformed', 'owner', '/camps-and-retreats/periods/update', 400, BADLY_FORMED, method='PATCH', body={})
add('update-athlete-forbidden', 'athlete', camp_path('spring', 'update'), 403, {'error': 'Only sport associations can update camps or retreats'},
    method='PATCH', body={})
add('update-unknown', 'owner', camp_path(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={'title': 'x'})
add('update-foreign-forbidden', 'owner', camp_path('abroad', 'update'), 403, {'detail': 'You are not allowed to update this camp or retreat'},
    method='PATCH', body={'title': 'x'})
add('update-unassociated-owner-server-error', 'owner_without_association', camp_path('spring', 'update'), 500, NO_ASSOCIATION,
    method='PATCH', body={})
add('update-unassociated-owner-unknown', 'owner_without_association', camp_path(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={})
add('update-invalid-fields', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([
    ('title', [BLANK]), ('description', [too_long(2048)]), ('sport_association', [NULL])])}, method='PATCH',
    body={'title': '', 'description': 'z' * 2049, 'sport_association': None})
add('update-invalid-relations', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([
    ('camps_and_retreats_id', [UNIQUE]), ('title', [NOT_STRING]), ('sport_association', [bad_uuid('abc')])])}, method='PATCH',
    body={'camps_and_retreats_id': CAMPS['winter']['id'], 'title': False, 'sport_association': 'abc'})
add('update-unknown-association', 'full_collaborator', camp_path('spring', 'update'), 400,
    {'error': repr_detail([('sport_association', [missing_pk(UNKNOWN)])])}, method='PATCH', body={'sport_association': UNKNOWN})
add('update-bool-association', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([
    ('sport_association', [('Incorrect type. Expected pk value, received bool.', 'incorrect_type')])])}, method='PATCH',
    body={'sport_association': True})
add('update-blank-association', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([('sport_association', [NULL])])},
    method='PATCH', body={'sport_association': ''})
add('update-foreign-association-forbidden', 'owner', camp_path('spring', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'title': '', 'sport_association': FOREIGN})
add('update-deleted-association-forbidden', 'full_collaborator', camp_path('spring', 'update'), 403, {'detail': TENANT}, method='PATCH',
    body={'sport_association': DELETED_ASSOCIATION})
add('update-list-body', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([('non_field_errors', [
    ('Invalid data. Expected a dictionary, but got list.', 'invalid')])])}, method='PATCH', body=[])
add('update-string-body', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([('non_field_errors', [
    ('Invalid data. Expected a dictionary, but got str.', 'invalid')])])}, method='PATCH', body='title')
add('update-null-body', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([('non_field_errors', [('No data provided', 'null')])])},
    method='PATCH', raw_body='null', content_type='application/json')
updated_camp('owner', 'update-owner', 'spring', {'title': 'Primavera 2026', 'description': None},
             body={'title': ' Primavera 2026 ', 'description': None, 'camps_and_retreats_id': CAMPS['spring']['id'], 'ignored': 1})
updated_camp('full_collaborator', 'update-collaborator-form', 'spring', {'title': None, 'description': 'Dal modulo'},
             form='title=&description=Dal+modulo')
updated_camp('owner', 'update-own-association-unchanged', 'autumn', {}, body={'sport_association': OWN})
updated_camp('owner', 'update-hex-uid', 'winter', {'title': '12'}, body={'title': 12},
             path_id=CAMPS['winter']['id'].replace('-', '').upper())

# A camps_and_retreats_id no row has: Model.save() inserts a copy with the validated values under that key and the
# camp the path names stays. auditlog's pre_save finds no row under the new key (an update against nothing, indexed
# from its changes), then post_save logs the create (indexed directly). A key another camp has is the unique error.
COPY_CAMP = 'a1000000-0000-4000-8000-0000007a9901'
COPY_CHANGES = {'camps_and_retreats_id': ['None', COPY_CAMP], 'sport_association': ['None', OWN], 'title': ['None', 'Copia'],
                'description': ['None', 'Dal modulo']}
add('update-new-id-inserts-copy', 'owner', camp_path('spring', 'update'), 200,
    {'message': 'Camp or retreat updated successfully',
     'camp_and_retreat': {'camps_and_retreats_id': COPY_CAMP, 'title': 'Copia', 'description': 'Dal modulo', 'sport_association': OWN}},
    method='PATCH', body={'camps_and_retreats_id': COPY_CAMP, 'title': 'Copia'},
    effect={'actor': 'owner', 'rows': {CAMP: {COPY_CAMP: {'title': 'Copia', 'description': 'Dal modulo', 'sport_association_id': OWN}}},
            'audit': [audit('CampsAndRetreats', COPY_CAMP, 1, COPY_CHANGES, OWN, 'changes_json.sport_association'),
                      audit('CampsAndRetreats', COPY_CAMP, 0, COPY_CHANGES, OWN, 'direct')]})
add('update-new-id-taken', 'owner', camp_path('spring', 'update'), 400, {'error': repr_detail([('camps_and_retreats_id', [UNIQUE])])},
    method='PATCH', body={'camps_and_retreats_id': COPY_CAMP, 'title': 'Altra'})

# ---------------------------------------------------------------------------
# <uid>/delete
# ---------------------------------------------------------------------------
DELETED = {'message': 'Camp or retreat deleted successfully'}
ACTOR_PATH = 'actor.sport_association'
add('delete-anonymous', 'anonymous', camp_path('summer', 'delete'), 401, ANONYMOUS, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', camp_path('summer', 'delete'), 403, missing('delete'), method='DELETE')
add('delete-wrong-method', 'owner', camp_path('summer', 'delete'), 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH', body={})
add('delete-options', 'owner', camp_path('summer', 'delete'), 200, OPTIONS('Camps And Retreats Delete'), method='OPTIONS')
add('delete-athlete-malformed', 'athlete', camp_path('abc', 'delete'), 400, BADLY_FORMED, method='DELETE')
add('delete-athlete-forbidden', 'athlete', camp_path('summer', 'delete'), 403, {'error': 'Only sport associations can delete camps or retreats'},
    method='DELETE')
add('delete-unknown', 'owner', camp_path(UNKNOWN, 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-foreign-forbidden', 'owner', camp_path('abroad', 'delete'), 403, {'detail': 'You are not allowed to delete this camp or retreat'},
    method='DELETE')
add('delete-unassociated-owner-server-error', 'owner_without_association', camp_path('summer', 'delete'), 500, NO_ASSOCIATION, method='DELETE')
gone_services = [s for s, row in SERVICES.items() if PERIODS[row['period']]['camp'] == 'summer']
gone_subperiods = [p for p, row in SUBSCRIPTION_PERIODS.items()
                   if PERIODS[row['period']]['camp'] == 'summer' or SUBSCRIPTIONS[row['subscription']]['camp'] == 'summer']
gone_periods = [p for p, row in PERIODS.items() if row['camp'] == 'summer']
add('delete-owner-cascade', 'owner', camp_path('summer', 'delete'), 200, DELETED, method='DELETE', effect={
    'actor': 'owner',
    'rows': {CAMP: {CAMPS['summer']['id']: None}, PERIOD: {PERIODS[p]['id']: None for p in gone_periods},
             SERVICE: {SERVICES[s]['id']: None for s in gone_services}, SUBSCRIPTION: {SUBSCRIPTIONS['summer']['id']: None},
             SUBPERIOD: {SUBSCRIPTION_PERIODS[p]['id']: None for p in gone_subperiods}},
    'links': {PERIOD_LINK: {'add': [], 'remove': [[PERIODS[p]['id'], SERVICES[s]['id']] for p, linked in PERIOD_LINKS.items() for s in linked
                                                  if p in gone_periods or s in gone_services]},
              SUBPERIOD_LINK: {'add': [], 'remove': [[row['id'], SERVICES[s]['id']] for p, row in SUBSCRIPTION_PERIODS.items()
                                                     for s in row['services'] if p in gone_subperiods or s in gone_services]}},
    'audit': [audit('CampsAndRetreats', CAMPS['summer']['id'], 2, '$deleted', OWN, 'changes_json.sport_association')]
    + [audit('CampsAndRetreatsPeriod', PERIODS[p]['id'], 2, '$deleted', OWN, ACTOR_PATH) for p in gone_periods]
    + [audit('CampsAndRetreatsPeriodsService', SERVICES[s]['id'], 2, '$deleted', OWN, ACTOR_PATH) for s in gone_services]
    + [audit('CampsAndRetreatsSubscription', SUBSCRIPTIONS['summer']['id'], 2, '$deleted', OWN, ACTOR_PATH)]
    + [audit('CampsAndRetreatsSubscriptionPeriod', SUBSCRIPTION_PERIODS[p]['id'], 2, '$deleted', OWN, ACTOR_PATH) for p in gone_subperiods]})
add('delete-collaborator', 'full_collaborator', camp_path('winter', 'delete'), 200, DELETED, method='DELETE', effect={
    'actor': 'full_collaborator', 'rows': {CAMP: {CAMPS['winter']['id']: None}},
    'audit': [audit('CampsAndRetreats', CAMPS['winter']['id'], 2, '$deleted', OWN, 'changes_json.sport_association')]})
add('delete-again-not-found', 'owner', camp_path('summer', 'delete'), 404, NOT_FOUND, method='DELETE')
add('info-after-delete-not-found', 'athlete', camp_path('summer', 'info'), 404, NOT_FOUND)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Camps and retreats list, add, info, update and delete authored sequence with audit effects',
                              'ids': IDS, 'categories': CATEGORIES, 'camps': CAMPS, 'periods': PERIODS, 'services': SERVICES,
                              'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'subscription_periods': SUBSCRIPTION_PERIODS,
                              'period_links': PERIOD_LINKS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
