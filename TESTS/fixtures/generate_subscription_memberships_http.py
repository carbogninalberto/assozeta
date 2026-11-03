"""Author the subscription/memberships list, add, update and delete HTTP sequence (subscription-memberships.md).

SubscriptionMembershipViewSet (ModelViewSet, SubscriptionMembershipSerializer)
is mapped as_view({'get': 'list'}), ({'post': 'add'}), ({'patch': 'update'})
and ({'delete': 'delete'}); the class's own delete() answers DELETE on every
route (no pk: get_object() fails, 500) and the GET route also allows HEAD.

get_queryset() is SportAssociation.objects.get(user=request.user) (a 500 for
athletes and owners without an association) filtered by the optional
``subscription`` and ``associate_id`` query parameters (a value the UUID
column rejects is a 500); update and delete go through get_object() on that
queryset, so the query parameters also narrow the pk lookup (otherwise, and
for a malformed pk, 404). list renders {'data': [...]} unordered.

add first assigns request.data['sport_association'] from the User
sport_association property: athletes get PermissionDenied('User is not a
sport association') (403), owners without an association a 500, and a list
or string body or a form body (an immutable QueryDict) a 500; a client value
never wins.
The serializer validates a writable subscription_membership_id
(UniqueValidator), tenant-scoped sport_association / associate (soft-delete,
group-aware) / payment (soft-delete, group-aware) relations, the DateTimeField
creation_date, DateFields (DD/MM/YYYY input), nullable CharFields (membership_number defaults to
0 and may be blank), DecimalField(9, 2) price and a write-only documents_data
list of dicts; ``subscription`` is not a serializer field and is ignored.
paid is payment.paid (None without a payment). The serializer saves before
handling documents_data, so a document without its keys (400) or with bad
base64 (500) keeps the saved row; successful uploads write to storage and
are outside this sequence. auditlog records the create, the changed fields
and the delete (document links removed, documents kept). Run with
``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_memberships_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{650001 + n:012d}' for n in range(60))
DOCUMENTS = {'d1': {'id': next(ids), 'token': next(ids), 'filename': 'tessera è.pdf', 'filepath': 'docs/tessera.pdf', 'size': 4096,
                    'created': '2026-09-01T08:00:00.500000Z'},
             'd2': {'id': next(ids), 'token': next(ids), 'filename': 'ricevuta.pdf', 'filepath': None, 'size': None,
                    'created': '2026-09-02T08:00:00Z'}}
ASSOCIATES = {'mario': {'id': next(ids), 'association': OWN, 'group': None, 'deleted': False},
              'grouped': {'id': next(ids), 'association': OWN, 'group': 'own_group', 'deleted': False},
              'removed': {'id': next(ids), 'association': OWN, 'group': None, 'deleted': True},
              'abroad': {'id': next(ids), 'association': FOREIGN, 'group': None, 'deleted': False}}
SUBSCRIPTIONS = {'sub_a': {'id': next(ids), 'association': OWN, 'associate': 'mario'},
                 'sub_b': {'id': next(ids), 'association': OWN, 'associate': 'mario'}}
PAYMENTS = {'paid': {'id': next(ids), 'association': OWN, 'paid': True, 'group': None, 'deleted': False},
            'unpaid': {'id': next(ids), 'association': OWN, 'paid': False, 'group': None, 'deleted': False},
            'grouped_pay': {'id': next(ids), 'association': OWN, 'paid': False, 'group': 'own_group', 'deleted': False},
            'removed_pay': {'id': next(ids), 'association': OWN, 'paid': True, 'group': None, 'deleted': True},
            'abroad_pay': {'id': next(ids), 'association': FOREIGN, 'paid': False, 'group': None, 'deleted': False}}
A, P = (lambda label: ASSOCIATES[label]['id']), (lambda label: PAYMENTS[label]['id'])
MEMBERSHIPS = {
    'm1': {'id': next(ids), 'sport_association': OWN, 'subscription': 'sub_a', 'associate': A('mario'), 'payment': P('paid'),
           'creation_date': '2026-09-01T08:00:00.250000Z', 'start_date': '2025-09-01', 'end_date': '2026-08-31',
           'membership_type': 'Annuale', 'membership_number': '12', 'description': 'Tessera è', 'price': '25.00', 'documents': ['d1']},
    'm2': {'id': next(ids), 'sport_association': OWN, 'subscription': 'sub_b', 'associate': None, 'payment': P('unpaid'),
           'creation_date': '2026-09-02T08:00:00Z', 'start_date': '2026-01-01', 'end_date': None,
           'membership_type': None, 'membership_number': '0', 'description': '', 'price': None, 'documents': ['d2']},
    'm3': {'id': next(ids), 'sport_association': OWN, 'subscription': None, 'associate': A('grouped'), 'payment': None,
           'creation_date': '2026-09-03T08:00:00Z', 'start_date': None, 'end_date': None,
           'membership_type': None, 'membership_number': None, 'description': None, 'price': None, 'documents': []},
    # A soft-deleted payment still renders: the forward relation uses the base manager.
    'm4': {'id': next(ids), 'sport_association': OWN, 'subscription': 'sub_a', 'associate': A('mario'), 'payment': P('removed_pay'),
           'creation_date': '2026-09-04T08:00:00Z', 'start_date': '2024-09-01', 'end_date': '2025-08-31',
           'membership_type': 'Ridotta', 'membership_number': '3', 'description': None, 'price': '0.50', 'documents': ['d1']},
    'abroad': {'id': next(ids), 'sport_association': FOREIGN, 'subscription': None, 'associate': A('abroad'), 'payment': None,
               'creation_date': '2026-09-05T08:00:00Z', 'start_date': None, 'end_date': None,
               'membership_type': 'Estero', 'membership_number': '1', 'description': None, 'price': '1.00', 'documents': []},
}
UNKNOWN = 'a1000000-0000-4000-8000-000000659999'
NEW_ID = 'a1000000-0000-4000-8000-000000659001'
SERVER = '<stable-server-error-page>'
STATE = copy.deepcopy(MEMBERSHIPS)
PAID = {row['id']: row['paid'] for row in PAYMENTS.values()}
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'subscription-memberships-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def document(label):
    row = DOCUMENTS[label]
    return {'document_id': row['id'], 'creation_date': row['created'], 'filepath': row['filepath'], 'filename': row['filename'],
            'file_size_bytes': row['size'], 'token': row['token']}


def item(row, ident=None):
    return {'subscription_membership_id': ident or row['id'], 'sport_association': row['sport_association'],
            'associate_id': row['associate'], 'associate': row['associate'], 'creation_date': row['creation_date'],
            'start_date': row['start_date'], 'end_date': row['end_date'], 'membership_type': row['membership_type'],
            'membership_number': row['membership_number'], 'description': row['description'], 'price': row['price'],
            'paid': PAID[row['payment']] if row['payment'] else None, 'payment': row['payment'],
            'attached_membership_documents': [document(d) for d in row['documents']]}


STORED = ('subscription', 'sport_association', 'associate', 'payment', 'creation_date', 'start_date', 'end_date', 'membership_type',
          'membership_number', 'description', 'price')


def stored(row):
    """Model columns of the row; subscription as its id."""
    values = {key: row[key] for key in STORED}
    values['subscription'] = SUBSCRIPTIONS[row['subscription']]['id'] if row['subscription'] else None
    return values


def created(actor, fields, ident='$new'):
    row = {'sport_association': OWN, 'subscription': None, 'associate': None, 'payment': None, 'creation_date': '$now',
           'start_date': None, 'end_date': None, 'membership_type': None, 'membership_number': '0', 'description': None,
           'price': None, 'documents': [], **fields}
    return item(row, ident), {'kind': 'add', 'actor': actor, 'id': ident, 'stored': stored(row)}


def audit_text(key, value):
    """auditlog's str(): DateTimeFields as naive UTC, None as 'None'."""
    if value is None:
        return 'None'
    if key == 'creation_date':
        return value.replace('T', ' ').replace('Z', '')
    return str(value)


def changed(actor, label, **fields):
    """Apply a partial update to the running state; changes list auditlog's str() pairs."""
    row = STATE[label]
    changes = {key: [audit_text(key, row[key]), audit_text(key, value)] for key, value in fields.items() if row[key] != value}
    row.update(fields)
    return item(row), {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes, 'stored': stored(row)}


LIST, ADD = '/subscription/memberships/list', '/subscription/memberships/add'
one = lambda label, verb: f'/subscription/memberships/{MEMBERSHIPS[label]["id"] if label in MEMBERSHIPS else label}/{verb}'
NOT_FOUND = {'detail': 'No SubscriptionMembership matches the given query.'}
MALFORMED = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
GROUP = {'X-Group-Id': IDS['own_group']}
OPTIONS = {'name': 'Subscription Membership', 'description': '', 'renders': ['application/json'],
           'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
MISSING_KEY = {'exception': 'document key not present'}
# REST_FRAMEWORK['DATE_INPUT_FORMATS'] is ['%d/%m/%Y'] (datetime.strptime); dates render ISO.
DATE_FORMAT = 'Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'
DATETIME_FORMAT = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
missing_pk = lambda value: f'Invalid pk "{value}" - object does not exist.'
field = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    **({'label': label} if label else {}), **extra}
# DRF SimpleMetadata describes the serializer for POST, the only PUT/POST method allowed on add.
ADD_OPTIONS = {**OPTIONS, 'actions': {'POST': {
    'subscription_membership_id': field('string', 'Subscription membership id'),
    'sport_association': field('field', 'Sport association', required=True),
    'associate_id': field('string', 'Associate id', read_only=True),
    'associate': field('field', 'Associate'),
    'creation_date': field('datetime', 'Creation date'),
    'start_date': field('date', 'Start date'), 'end_date': field('date', 'End date'),
    'membership_type': field('string', 'Membership type', max_length=255),
    'membership_number': field('string', 'Membership number', max_length=50),
    'description': field('string', 'Description', max_length=255),
    'price': field('decimal', 'Price', max_digits=9, decimal_places=2),
    'paid': field('field', 'Paid', read_only=True),
    'payment': field('field', 'Payment'),
    'attached_membership_documents': field('field', 'Attached membership documents', read_only=True, child={
        'type': 'nested object', 'required': False, 'read_only': True, 'children': {
            'document_id': field('string', 'Document id'), 'creation_date': field('datetime', 'Creation date'),
            'filepath': field('string', 'Filepath', max_length=1000), 'filename': field('string', 'Filename', required=True, max_length=255),
            'file_size_bytes': field('integer', 'File size bytes', min_value=0, max_value=9223372036854775807),
            'token': field('string', 'Token')}}),
    'documents_data': field('list', 'Documents data', child={'type': 'nested object', 'required': True, 'read_only': False,
                                                              'child': {'type': 'field', 'required': True, 'read_only': False}})}}}
missing = lambda verb: {'detail': f'Missing permission: association.members.{verb}'}
rows = lambda *labels: {'data': [item(STATE[label]) for label in labels]}
OWN_ROWS = ('m1', 'm2', 'm3', 'm4')

# list
add('list-owner', 'owner', LIST, 200, rows(*OWN_ROWS), method='GET')
add('list-full-collaborator-owner-scope', 'full_collaborator', LIST, 200, rows(*OWN_ROWS), method='GET')
add('list-admin-impersonating-owner', 'admin', LIST, 200, rows(*OWN_ROWS), method='GET',
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-foreign-owner', 'foreign_owner', LIST, 200, rows('abroad'), method='GET')
add('list-group-header-not-applied', 'owner', LIST, 200, rows(*OWN_ROWS), method='GET', headers=GROUP)
add('list-subscription-filter', 'owner', f'{LIST}?subscription={SUBSCRIPTIONS["sub_a"]["id"]}', 200, rows('m1', 'm4'), method='GET')
add('list-associate-filter', 'owner', f'{LIST}?associate_id={A("grouped")}', 200, rows('m3'), method='GET')
add('list-both-filters-empty', 'owner', f'{LIST}?subscription={SUBSCRIPTIONS["sub_b"]["id"]}&associate_id={A("grouped")}', 200,
    {'data': []}, method='GET')
add('list-blank-filters-ignored', 'owner', f'{LIST}?subscription=&associate_id=', 200, rows(*OWN_ROWS), method='GET')
add('list-foreign-associate-filter-empty', 'owner', f'{LIST}?associate_id={A("abroad")}', 200, {'data': []}, method='GET')
add('list-malformed-subscription-server-error', 'owner', f'{LIST}?subscription=abc', 500, SERVER, method='GET')
add('list-malformed-associate-server-error', 'owner', f'{LIST}?associate_id=abc', 500, SERVER, method='GET')
add('list-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='GET')
add('list-athlete-server-error', 'athlete', LIST, 500, SERVER, method='GET')
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_DENIED, method='GET', headers=SCOPED)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST, 403, missing('read'), method='GET')
add('list-admin-without-session', 'admin', LIST, 403, ADMIN, method='GET')
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS, method='GET')
add('list-wrong-method', 'owner', LIST, 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('list-options', 'owner', LIST, 200, OPTIONS, method='OPTIONS')

# add
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body={})
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, missing('create'), body={'membership_type': 'X'})
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body={'membership_type': 'X'}, headers=SCOPED)
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, ADD_OPTIONS, method='OPTIONS')
add('add-route-delete-without-pk-server-error', 'owner', ADD, 500, SERVER, method='DELETE')
# User.sport_association (a cached property raising DRF's PermissionDenied for
# athletes) precedes reading the body and validation.
add('add-athlete-not-association', 'athlete', ADD, 403, {'detail': 'User is not a sport association'}, body={'membership_type': ''})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, body={})
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[])
add('add-string-body-server-error', 'owner', ADD, 500, SERVER, body='x')
add('add-form-body-server-error', 'owner', ADD, 500, SERVER, raw_body='membership_type=X', content_type='application/x-www-form-urlencoded')
add('add-invalid-fields', 'owner', ADD, 400, {
    'subscription_membership_id': ['Must be a valid UUID.'], 'creation_date': [DATETIME_FORMAT], 'start_date': [DATE_FORMAT],
    'end_date': [DATE_FORMAT], 'membership_type': ['This field may not be blank.'],
    'membership_number': ['Ensure this field has no more than 50 characters.'],
    'description': ['Ensure this field has no more than 255 characters.'], 'price': ['A valid number is required.']},
    body={'subscription_membership_id': 'abc', 'creation_date': 'ieri', 'start_date': '30/02/2026', 'end_date': 20260101,
          'membership_type': '', 'membership_number': '9' * 51, 'description': 'd' * 256, 'price': 'dieci'})
add('add-invalid-types', 'owner', ADD, 400, {
    'creation_date': ['This field may not be null.'], 'membership_type': ['Not a valid string.'],
    'price': ['Ensure that there are no more than 9 digits in total.'], 'start_date': [DATE_FORMAT]},
    body={'creation_date': None, 'membership_type': True, 'price': '1234567.891', 'start_date': '2026-01-01', 'end_date': None})
add('add-price-places', 'owner', ADD, 400, {'price': ['Ensure that there are no more than 2 decimal places.'],
                                           'description': ['Not a valid string.']}, body={'price': 1.255, 'description': ['x']})
add('add-price-whole-digits', 'owner', ADD, 400, {'price': ['Ensure that there are no more than 7 digits before the decimal point.']},
    body={'price': '12345678'})
add('add-existing-id', 'owner', ADD, 400,
    {'subscription_membership_id': ['subscription membership with this subscription membership id already exists.']},
    body={'subscription_membership_id': MEMBERSHIPS['m1']['id']})
add('add-related-errors', 'owner', ADD, 400, {'associate': [missing_pk(UNKNOWN)], 'payment': ['“abc” is not a valid UUID.']},
    body={'associate': UNKNOWN, 'payment': 'abc'})
add('add-related-types', 'owner', ADD, 400, {'associate': ['Incorrect type. Expected pk value, received bool.'],
                                            'payment': [f'“{["x"]}” is not a valid UUID.']}, body={'associate': True, 'payment': ['x']})
add('add-soft-deleted-relations', 'owner', ADD, 400, {'associate': [missing_pk(A('removed'))], 'payment': [missing_pk(P('removed_pay'))]},
    body={'associate': A('removed'), 'payment': P('removed_pay')})
add('add-group-hides-relations', 'owner', ADD, 400, {'associate': [missing_pk(A('mario'))], 'payment': [missing_pk(P('paid'))]},
    body={'associate': A('mario'), 'payment': P('paid')}, headers=GROUP)
add('add-foreign-associate-forbidden', 'owner', ADD, 403, FORBIDDEN, body={'associate': A('abroad'), 'membership_type': ''})
add('add-foreign-payment-forbidden', 'full_collaborator', ADD, 403, FORBIDDEN,
    body={'payment': P('abroad_pay'), 'associate': UNKNOWN, 'price': 'x'})
add('add-documents-data-not-list', 'owner', ADD, 400, {'documents_data': ['Expected a list of items but got type "str".']},
    body={'documents_data': 'x'})
add('add-documents-data-item-not-dict', 'owner', ADD, 400,
    {'documents_data': {'1': ['Expected a dictionary of items but got type "int".']}}, body={'documents_data': [{}, 1]})
add('add-client-association-overwritten', 'owner', ADD, 400, {'membership_type': ['This field may not be blank.']},
    body={'membership_type': '', 'sport_association': None})
response, effect = created('owner', {'associate': A('mario'), 'payment': P('unpaid'), 'creation_date': '2026-09-10T08:00:00Z',
                                     'start_date': '2026-09-01', 'end_date': '2027-08-31', 'membership_type': 'Annuale è',
                                     'membership_number': '42', 'description': 'Nuova', 'price': '7.50'})
add('add-owner', 'owner', ADD, 201, response, body={
    'associate': A('mario'), 'payment': P('unpaid'), 'creation_date': '2026-09-10T10:00:00+02:00', 'start_date': '01/09/2026',
    'end_date': '31/08/2027', 'membership_type': ' Annuale è ', 'membership_number': 42, 'description': 'Nuova', 'price': '7.5',
    'sport_association': FOREIGN, 'subscription': SUBSCRIPTIONS['sub_a']['id'], 'paid': True, 'associate_id': A('abroad')}, effect=effect)
response, effect = created('full_collaborator', {})
add('add-full-collaborator-empty', 'full_collaborator', ADD, 201, response, body={}, effect=effect)
response, effect = created('owner', {'start_date': '2026-01-05', 'membership_number': '', 'price': '12.00',
                                     'creation_date': '2026-09-11T08:30:00.123456Z'}, NEW_ID)
add('add-explicit-id-short-date', 'owner', ADD, 201, response, body={
    'subscription_membership_id': NEW_ID.upper(), 'start_date': ' 5/1/2026', 'membership_number': '', 'price': 12,
    'creation_date': '2026-09-11 08:30:00.123456', 'associate': '', 'payment': None, 'membership_type': None}, effect=effect)
response, effect = created('full_collaborator', {'associate': A('grouped'), 'payment': P('grouped_pay')})
add('add-group-relations', 'full_collaborator', ADD, 201, response, body={'associate': A('grouped'), 'payment': P('grouped_pay')},
    headers=GROUP, effect=effect)
response, effect = created('owner', {'membership_type': 'Senza chiavi'})
add('add-document-missing-key-keeps-membership', 'owner', ADD, 400, MISSING_KEY,
    body={'membership_type': 'Senza chiavi', 'documents_data': [{'filename': 'a.pdf'}, {'document': 'QQ==', 'filename': 'b.pdf'}]},
    effect=effect)
response, effect = created('owner', {'membership_type': 'Base64'})
add('add-document-bad-base64-keeps-membership', 'owner', ADD, 500, SERVER,
    body={'membership_type': 'Base64', 'documents_data': [{'document': 'abc', 'filename': 'a.pdf'}]}, effect=effect)

# update
add('update-anonymous', 'anonymous', one('m1', 'update'), 401, ANONYMOUS, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', one('m1', 'update'), 403, missing('update'), method='PATCH',
    body={'membership_type': 'X'})
add('update-wrong-method', 'owner', one('m1', 'update'), 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('update-options', 'owner', one('m1', 'update'), 200, OPTIONS, method='OPTIONS')
add('update-malformed-not-found', 'owner', one('abc', 'update'), 404, MALFORMED, method='PATCH', body={'membership_type': 'X'})
add('update-unknown-not-found', 'owner', one(UNKNOWN, 'update'), 404, NOT_FOUND, method='PATCH', body={'membership_type': 'X'})
add('update-foreign-not-found', 'owner', one('abroad', 'update'), 404, NOT_FOUND, method='PATCH', body={'membership_type': 'X'})
add('update-query-filter-not-found', 'owner', one('m1', 'update') + f'?subscription={SUBSCRIPTIONS["sub_b"]["id"]}', 404, NOT_FOUND,
    method='PATCH', body={'membership_type': 'X'})
add('update-malformed-query-server-error', 'owner', one('m1', 'update') + '?associate_id=abc', 500, SERVER, method='PATCH',
    body={'membership_type': 'X'})
add('update-athlete-server-error', 'athlete', one('m1', 'update'), 500, SERVER, method='PATCH', body={'membership_type': 'X'})
add('update-owner-without-association-server-error', 'owner_without_association', one('m1', 'update'), 500, SERVER, method='PATCH',
    body={})
add('update-invalid-fields', 'owner', one('m1', 'update'), 400, {
    'sport_association': ['This field may not be null.'], 'membership_type': ['This field may not be blank.'],
    'creation_date': [DATETIME_FORMAT], 'end_date': [DATE_FORMAT]},
    method='PATCH', body={'sport_association': None, 'membership_type': '', 'creation_date': 5, 'end_date': '29/02/2026', 'price': ''})
add('update-unknown-association', 'owner', one('m1', 'update'), 400, {'sport_association': [missing_pk(UNKNOWN)]}, method='PATCH',
    body={'sport_association': UNKNOWN})
add('update-existing-id', 'owner', one('m1', 'update'), 400,
    {'subscription_membership_id': ['subscription membership with this subscription membership id already exists.']},
    method='PATCH', body={'subscription_membership_id': MEMBERSHIPS['m2']['id']})
add('update-foreign-association-forbidden', 'owner', one('m1', 'update'), 403, FORBIDDEN, method='PATCH',
    body={'sport_association': FOREIGN, 'price': 'x'})
add('update-foreign-associate-forbidden', 'full_collaborator', one('m1', 'update'), 403, FORBIDDEN, method='PATCH',
    body={'associate': A('abroad')})
response, effect = changed('owner', 'm1', membership_type='Mensile', price='30.00', start_date='2025-10-01', associate=None,
                           payment=P('unpaid'), creation_date='2026-10-01T10:00:00Z')
add('update-owner', 'owner', one('m1', 'update'), 200, response, method='PATCH', body={
    'membership_type': 'Mensile', 'price': '30', 'start_date': '1/10/2025', 'associate': '', 'payment': P('unpaid'),
    'creation_date': '2026-10-01T12:00:00+02:00', 'subscription': None, 'documents_data': []}, effect=effect)
response, effect = changed('full_collaborator', 'm1')
add('update-same-values-no-audit', 'full_collaborator', one('m1', 'update') + f'?subscription={SUBSCRIPTIONS["sub_a"]["id"]}', 200,
    response, method='PATCH', body={'membership_type': 'Mensile', 'price': 30.0, 'sport_association': OWN,
                                    'subscription_membership_id': MEMBERSHIPS['m1']['id'], 'creation_date': '2026-10-01T10:00:00Z'},
    effect=effect)
response, effect = changed('owner', 'm2', membership_type=None, price=None, start_date=None, associate=A('mario'), membership_number='')
add('update-form-blank-values', 'owner', one('m2', 'update'), 200, response, method='PATCH',
    raw_body=f'membership_type=&price=&start_date=&associate={A("mario")}&membership_number=',
    content_type='application/x-www-form-urlencoded', effect=effect)
response, effect = changed('owner', 'm3', payment=P('grouped_pay'), end_date='2027-01-31')
add('update-group-payment', 'owner', one('m3', 'update'), 200, response, method='PATCH',
    body={'payment': P('grouped_pay'), 'end_date': '31/01/2027'}, headers=GROUP, effect=effect)
# DecimalField.validate_empty_values: a blank allow_null decimal is None.
response, effect = changed('owner', 'm4', description='Nota', price=None)
add('update-document-missing-key-keeps-change', 'owner', one('m4', 'update'), 400, MISSING_KEY, method='PATCH',
    body={'description': 'Nota', 'price': ' ', 'documents_data': [{'document': ''}]}, effect=effect)

# delete
add('delete-restricted-missing-permission', 'restricted_collaborator', one('m2', 'delete'), 403, missing('delete'), method='DELETE')
add('delete-wrong-method', 'owner', one('m2', 'delete'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('delete-options', 'owner', one('m2', 'delete'), 200, OPTIONS, method='OPTIONS')
add('delete-malformed-not-found', 'owner', one('abc', 'delete'), 404, MALFORMED, method='DELETE')
add('delete-foreign-not-found', 'owner', one('abroad', 'delete'), 404, NOT_FOUND, method='DELETE')
add('delete-query-filter-not-found', 'owner', one('m2', 'delete') + f'?associate_id={A("grouped")}', 404, NOT_FOUND, method='DELETE')
add('delete-athlete-server-error', 'athlete', one('m2', 'delete'), 500, SERVER, method='DELETE')
add('delete-owner-with-document', 'owner', one('m2', 'delete'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'label': 'm2'})
add('delete-again-not-found', 'owner', one('m2', 'delete'), 404, NOT_FOUND, method='DELETE')
# m1 shares d1 with m4: only m1's link goes.
add('update-route-delete-deletes', 'full_collaborator', one('m1', 'update'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'label': 'm1'})
add('list-after-writes', 'owner', f'{LIST}?subscription={SUBSCRIPTIONS["sub_a"]["id"]}', 200, rows('m4'), method='GET')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription memberships list, add, update and delete authored sequence with audit effects',
                              'ids': IDS, 'documents': DOCUMENTS, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS,
                              'payments': PAYMENTS, 'memberships': MEMBERSHIPS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
