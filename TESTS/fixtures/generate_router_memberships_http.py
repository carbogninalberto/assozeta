"""Author the DefaultRouter subscription/memberships HTTP sequence (router-stock.md).

`router.register(r'subscription/memberships', SubscriptionMembershipViewSet)`.
The list route binds GET to list() and POST to the stock
CreateModelMixin.create: unlike add it does not write request.data, so the
serializer validates the client's sport_association (required; another
tenant's association is DRF's 403; without a current association, athletes
and owners without one, the tenant scope does not apply), a form body is
parsed like JSON input, a list body is the non-dict 400, and perform_create's
SportAssociation.objects.get(user=request.user) (a 500 for athletes) then
replaces the association. The detail route binds GET to the stock
retrieve() (get_object() over get_queryset(), whose subscription and
associate_id query filters narrow the lookup), PUT to update() (partial),
PATCH to partial_update() (500) and DELETE to the stock destroy(). The
format twins: list() and update() reject format= (500); create(),
retrieve() and destroy() accept it. Builds on the subscription memberships
seed. Run with ``python3 -I`` after the subscription memberships generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('router_memberships_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('subscription_memberships_http.json').read_text()))
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
DOCUMENTS, ASSOCIATES, SUBSCRIPTIONS = SOURCE['documents'], SOURCE['associates'], SOURCE['subscriptions']
PAYMENTS, MEMBERSHIPS = SOURCE['payments'], SOURCE['memberships']
A, P = (lambda label: ASSOCIATES[label]['id']), (lambda label: PAYMENTS[label]['id'])
UNKNOWN = 'a1000000-0000-4000-8000-000000659998'
NEW_ID = 'a1000000-0000-4000-8000-000000659101'
SERVER = '<stable-server-error-page>'
STATE = copy.deepcopy(MEMBERSHIPS)
PAID = {row['id']: row['paid'] for row in PAYMENTS.values()}
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'router-memberships-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
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
    values = {key: row[key] for key in STORED}
    values['subscription'] = SUBSCRIPTIONS[row['subscription']]['id'] if row['subscription'] else None
    return values


def created(actor, fields, ident='$new'):
    row = {'sport_association': OWN, 'subscription': None, 'associate': None, 'payment': None, 'creation_date': '$now',
           'start_date': None, 'end_date': None, 'membership_type': None, 'membership_number': '0', 'description': None,
           'price': None, 'documents': [], **fields}
    return item(row, ident), {'kind': 'add', 'actor': actor, 'id': ident, 'stored': stored(row)}


def changed(actor, label, **fields):
    row = STATE[label]
    text = lambda value: 'None' if value is None else str(value)
    changes = {key: [text(row[key]), text(value)] for key, value in fields.items() if row[key] != value}
    row.update(fields)
    return item(row), {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes, 'stored': stored(row)}


LIST = '/subscription/memberships/'
detail = lambda label, suffix='/': f'/subscription/memberships/{MEMBERSHIPS[label]["id"] if label in MEMBERSHIPS else label}{suffix}'
NOT_FOUND = {'detail': 'No SubscriptionMembership matches the given query.'}
BARE = {'detail': 'Not found.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
method = lambda name: {'detail': f'Method "{name}" not allowed.'}
field = lambda kind, label, required=False, read_only=False, **extra: {'type': kind, 'required': required, 'read_only': read_only,
                                                                    **({'label': label} if label else {}), **extra}
SERIALIZER = {
    'subscription_membership_id': field('string', 'Subscription membership id', read_only=True),
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
                                                              'child': {'type': 'field', 'required': True, 'read_only': False}})}
OPTIONS = lambda suffix, actions=None: {'name': f'Subscription Membership {suffix}', 'description': '', 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
                                        **({'actions': actions} if actions else {})}
rows = lambda *labels: {'data': [item(STATE[label]) for label in labels]}

# The list route.
add('list-anonymous', 'anonymous', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', LIST, 403, unmapped('subscription/memberships'))
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED)
add('list-owner', 'owner', LIST, 200, rows('m1', 'm2', 'm3', 'm4'))
add('list-subscription-filter', 'full_collaborator', f'{LIST}?subscription={SUBSCRIPTIONS["sub_a"]["id"]}', 200, rows('m1', 'm4'))
add('list-malformed-filter-server-error', 'owner', f'{LIST}?associate_id=abc', 500, SERVER)
add('list-athlete-server-error', 'athlete', LIST, 500, SERVER)
add('list-patch-not-allowed', 'owner', LIST, 405, method('PATCH'), method='PATCH', body={})
add('list-delete-without-pk-server-error', 'owner', LIST, 500, SERVER, method='DELETE')
add('list-options', 'owner', LIST, 200, OPTIONS('List', {'POST': SERIALIZER}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/subscription/memberships.json', 500, SERVER)
add('list-format-unknown-not-found', 'owner', '/subscription/memberships.html', 404, BARE)
# create (stock)
add('create-association-required', 'owner', LIST, 400, {'sport_association': ['This field is required.']}, method='POST',
    body={'membership_type': 'Annuale'})
add('create-list-body', 'owner', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, method='POST',
    body=[{'sport_association': OWN}])
add('create-invalid-fields', 'owner', LIST, 400,
    {'start_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
     'price': ['A valid number is required.'], 'associate': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    method='POST', body={'sport_association': OWN, 'start_date': '2026-01-01', 'price': 'x', 'associate': UNKNOWN})
add('create-foreign-association-forbidden', 'owner', LIST, 403, FORBIDDEN, method='POST', body={'sport_association': FOREIGN})
add('create-foreign-associate-forbidden', 'owner', LIST, 403, FORBIDDEN, method='POST',
    body={'sport_association': OWN, 'associate': A('abroad')})
add('create-athlete-association-required', 'athlete', LIST, 400, {'sport_association': ['This field is required.']}, method='POST', body={})
add('create-athlete-server-error', 'athlete', LIST, 500, SERVER, method='POST', body={'sport_association': FOREIGN})
add('create-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='POST',
    body={'sport_association': FOREIGN})
response, effect = created('owner', {'membership_type': 'Annuale', 'start_date': '2026-09-01', 'price': '30.00', 'associate': A('mario'),
                                     'payment': P('paid')})
add('create-owner', 'owner', LIST, 201, response, method='POST',
    body={'sport_association': OWN, 'membership_type': 'Annuale', 'start_date': '01/09/2026', 'price': '30', 'associate': A('mario'),
          'payment': P('paid'), 'subscription': SUBSCRIPTIONS['sub_a']['id']}, effect=effect)
# subscription_membership_id is read-only: the body's key is ignored and the membership gets a new one.
response, effect = created('full_collaborator', {'description': 'Da form', 'membership_number': ''})
add('create-form-body-id-ignored', 'full_collaborator', LIST, 201, response, method='POST',
    raw_body=f'sport_association={OWN}&subscription_membership_id={NEW_ID}&description=Da+form&membership_number=',
    content_type='application/x-www-form-urlencoded', effect=effect)
response, effect = created('owner', {})
add('create-existing-id-ignored', 'owner', LIST, 201, response, method='POST',
    body={'sport_association': OWN, 'subscription_membership_id': MEMBERSHIPS['m1']['id']}, effect=effect)
response, effect = created('owner', {'membership_type': 'Formato'})
add('create-format-json', 'owner', '/subscription/memberships.json/', 201, response, method='POST',
    body={'sport_association': OWN, 'membership_type': 'Formato'}, effect=effect)
response, effect = created('owner', {'description': 'Chiavi'})
add('create-document-missing-key-keeps-row', 'owner', LIST, 400, {'exception': 'document key not present'}, method='POST',
    body={'sport_association': OWN, 'description': 'Chiavi', 'documents_data': [{'filename': 'a.pdf'}]}, effect=effect)

# The detail route.
add('detail-anonymous', 'anonymous', detail('m1'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', detail('m1'), 403, unmapped('subscription/memberships/' + MEMBERSHIPS['m1']['id']))
add('retrieve-owner', 'owner', detail('m1'), 200, item(STATE['m1']))
add('retrieve-soft-deleted-payment', 'full_collaborator', detail('m4', '.json'), 200, item(STATE['m4']))
add('retrieve-filter-narrows-not-found', 'owner', detail('m2') + f'?subscription={SUBSCRIPTIONS["sub_a"]["id"]}', 404, NOT_FOUND)
add('retrieve-filter-matches', 'owner', detail('m3') + f'?associate_id={A("grouped")}', 200, item(STATE['m3']))
add('retrieve-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND)
add('retrieve-malformed-not-found', 'owner', detail('abc'), 404, BARE)
add('retrieve-athlete-server-error', 'athlete', detail('m1'), 500, SERVER)
add('detail-post-not-allowed', 'owner', detail('m1'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('m1'), 200, OPTIONS('Instance', {'PUT': SERIALIZER}), method='OPTIONS')
add('detail-options-unknown-no-actions', 'owner', detail(UNKNOWN), 200, OPTIONS('Instance'), method='OPTIONS')
add('partial-update-server-error', 'owner', detail('m2'), 500, SERVER, method='PATCH', body={'description': 'Mai'})
add('update-format-json-server-error', 'owner', detail('m2', '.json'), 500, SERVER, method='PUT', body={'description': 'Mai'})
add('update-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND, method='PUT', body={'description': 'x'})
add('update-invalid-date', 'owner', detail('m2'), 400, {'end_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    method='PUT', body={'end_date': '2027-01-01'})
response, effect = changed('owner', 'm2', description='Aggiornata router', end_date='2026-12-31', price='12.50')
add('update-owner-put-is-partial', 'owner', detail('m2'), 200, response, method='PUT',
    body={'description': 'Aggiornata router', 'end_date': '31/12/2026', 'price': '12.5'}, effect=effect)
response, effect = changed('owner', 'm2', description='Chiave ignorata')
add('update-primary-key-ignored', 'owner', detail('m2'), 200, response, method='PUT',
    body={'subscription_membership_id': MEMBERSHIPS['m1']['id'], 'description': 'Chiave ignorata'}, effect=effect)
add('destroy-foreign-not-found', 'owner', detail('abroad'), 404, NOT_FOUND, method='DELETE')
add('destroy-filter-narrows-not-found', 'owner', detail('m1') + f'?associate_id={A("grouped")}', 404, NOT_FOUND, method='DELETE')
add('destroy-athlete-server-error', 'athlete', detail('m1'), 500, SERVER, method='DELETE')
add('destroy-owner-with-documents', 'owner', detail('m1'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'label': 'm1'})
add('destroy-again-not-found', 'owner', detail('m1'), 404, NOT_FOUND, method='DELETE')
add('destroy-format-json', 'full_collaborator', detail('m4', '.json/'), 204, '<empty>', method='DELETE',
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'label': 'm4'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'DefaultRouter subscription/memberships routes', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
