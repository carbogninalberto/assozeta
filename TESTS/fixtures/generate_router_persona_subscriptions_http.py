"""Author the DefaultRouter personas-subscriptions HTTP sequence (router-stock.md).

`router.register(r'personas-subscriptions', AssociateSubscriptionViewSet)`.
get_queryset() reads request.user.sport_association (athletes 403, owners
without an association 500): the association's live subscriptions
(group-aware) by -creation_date. The list route binds GET to the viewset's
list(request, pk=None): every subscription, KTDatatablePagination and
AssociateSubscriptionSerializer (rowIds are the associates' ids), and its
own delete() answers DELETE without a pk (get_queryset, then 500). The
detail route binds GET to the stock retrieve(), DELETE to the stock
destroy(): Subscription.delete() through Django's Collector (a hard delete;
the viewset's own delete() soft-deletes). The list() and delete() signatures
reject format= (500). POST, PUT and PATCH are the stock create, update and
partial_update over AssociateSubscriptionSerializer (persona-subs-writes.md):
create validates, then always fails in SubscriptionManager.create (500,
nothing written); update writes through Subscription.save() and tags.set().
Expectations
reuse the personas-writes seed and subscription rendering (loaded from
generate_personas_writes_http.py). Run with ``python3 -I``.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
SHARED = (HERE / 'generate_personas_writes_http.py').read_text()
namespace = {'__file__': str(HERE / 'generate_personas_writes_http.py'), '__name__': 'personas_writes_helpers'}
helpers = SHARED[:SHARED.index('# ================================================================= transport')]
helpers += SHARED[SHARED.index('def subscription(label, group=None):'):SHARED.index('# Ordered by -creation_date.')]
exec(compile(helpers, 'generate_personas_writes_http.py', 'exec'), namespace)
globals().update({key: value for key, value in namespace.items() if not key.startswith('__') and key not in ('add', 'cases', 'OUTPUT')})
OUTPUT = HERE / 'router_persona_subscriptions_http.json'
cases = []
LIST_ALLOW = ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'POST']
DETAIL_ALLOW = ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'PUT']


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, headers=None, effect=None, allow=None):
    detail = path.split('?')[0].rstrip('/').count('/') > 1
    case = {'name': f'router-persona-subscriptions-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'allow': allow or (DETAIL_ALLOW if detail else LIST_ALLOW), 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, 'application/json'
    cases.append(case)


def page(labels, **options):
    return sub_page(labels, **options)


LIST = '/personas-subscriptions/'
detail = lambda label, suffix='/': f'/personas-subscriptions/{SUB_IDS.get(label, label)}{suffix}'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
SUB_NOT_FOUND = {'detail': 'No Subscription matches the given query.'}
choices = lambda pairs: [{'value': value, 'display_name': name} for value, name in pairs]
# SimpleMetadata over AssociateSubscriptionSerializer: Meta.exclude signature_storage_key; the declared
# read-only fields first, then the model's fields in order (forward relations last).
SERIALIZER = {
    'subscription_id': meta('string', 'subscription_id'),
    **{name: meta('boolean', name, read_only=True) for name in ('active', 'is_current', 'is_next_year')},
    'current_year': meta('string', 'current_year', read_only=True),
    **{name: meta('field', name, read_only=True) for name in ('medical', 'medical_expiration_date', 'age', 'renewal_available',
                                                              'signature_present')},
    'associate': {**meta('nested object', 'associate', read_only=True), 'children': basic_associate_meta()},
    'custom_data': meta('field', 'custom_data'), 'additional_fields': meta('field', 'additional_fields'),
    'type': meta('choice', 'type', choices=choices([(1, 'associato'), (2, 'associato e tesserato'), (3, 'tesserato')])),
    'role': meta('choice', 'role', choices=choices([(1, 'Socio ordinario'), (2, 'Consigliere'), (3, 'Segretario'), (4, 'Vice Presidente'),
                                                    (5, 'Presidente'), (6, 'Socio volontario'), (7, 'Socio sostenitore'), (8, 'Tesoriere')])),
    'acceptance_date': meta('date', 'acceptance_date'), 'resignation_date': meta('date', 'resignation_date'),
    'status_flag': meta('choice', 'status_flag', choices=choices([(1, 'non firmata'), (2, 'in attesa'), (3, 'rifiutata'), (4, 'accettata'),
                                                                  (5, 'ritirata')])),
    'creation_date': meta('datetime', 'creation_date'), 'start_date': meta('date', 'start_date'), 'end_date': meta('date', 'end_date'),
    'signature_url': meta('string', 'signature_url', help_text='S3 URL for signature image', max_length=1024),
    **{name: meta('boolean', name) for name in ('archived', 'draft', 'trial')},
    'meta': meta('field', 'meta'), 'subscription_number': meta('string', 'subscription_number'),
    'subscription_type': meta('string', 'subscription_type', max_length=255), 'deleted': meta('boolean', 'deleted'),
    'competitive': meta('boolean', 'competitive'), 'notes': meta('string', 'notes'),
    'sport_association': meta('field', 'sport_association', required=True), 'supplier': meta('field', 'supplier'),
    'instructor': meta('field', 'instructor'), 'user': meta('field', 'user', required=True), 'document_pdf': meta('field', 'document_pdf'),
    'payment': meta('field', 'payment'), 'group': meta('field', 'group'), 'tags': meta('field', 'tags'),
}
named = lambda suffix, method: {**OPTIONS_SUBS, 'name': f'Associate Subscription {suffix}', 'actions': {method: SERIALIZER}}
ORDER = ['cur', 'lone_past', 'old', 'grouped', 'future']
destroyed = lambda actor, label: {'kind': 'destroy', 'actor': actor, 'subscription': SUB_IDS[label]}

add('list-anonymous', 'anonymous', 'GET', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', 'GET', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', 'GET', LIST, 403, unmapped('personas-subscriptions'))
add('list-owner-scoped-athlete-denied', 'owner', 'GET', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED_ATHLETE)
add('list-athlete-not-association', 'athlete', 'GET', LIST, 403, NOT_ASSOCIATION)
add('list-owner-without-association-server-error', 'owner_without_association', 'GET', LIST, 500, SERVER)
add('list-owner', 'owner', 'GET', LIST, 200, page(ORDER))
add('list-page-two', 'full_collaborator', 'GET', LIST + '?pagination[perpage]=2&pagination[page]=2', 200,
    page(['old', 'grouped'], number=2, size=2, total=5))
add('list-own-group', 'owner', 'GET', LIST, 200, page(['grouped'], group=OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('list-foreign-owner', 'foreign_owner', 'GET', LIST, 200, page(['hilde', 'far']))
add('list-invalid-page', 'owner', 'GET', LIST + '?pagination[page]=9', 404, {'detail': 'Invalid page.'})
add('list-put-not-allowed', 'owner', 'PUT', LIST, 405, method_not_allowed('PUT'), body={})
add('list-delete-without-pk-server-error', 'owner', 'DELETE', LIST, 500, SERVER)
add('list-delete-athlete-not-association', 'athlete', 'DELETE', LIST, 403, NOT_ASSOCIATION)
add('list-options', 'owner', 'OPTIONS', LIST, 200, named('List', 'POST'))
add('list-format-json-server-error', 'owner', 'GET', '/personas-subscriptions.json', 500, SERVER)
add('create-anonymous', 'anonymous', 'POST', LIST, 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('create-restricted-unmapped', 'restricted_collaborator', 'POST', LIST, 403, unmapped('personas-subscriptions'), body={})

add('detail-anonymous', 'anonymous', 'GET', detail('cur'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', 'GET', detail('cur'), 403, unmapped(f'personas-subscriptions/{SUB_IDS["cur"]}'))
add('retrieve-owner', 'owner', 'GET', detail('cur'), 200, subscription('cur'))
add('retrieve-renewal', 'full_collaborator', 'GET', detail('lone_past', '.json'), 200, subscription('lone_past'))
add('retrieve-own-group', 'owner', 'GET', detail('grouped'), 200, subscription('grouped', OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('retrieve-other-group-not-found', 'owner', 'GET', detail('cur'), 404, SUB_NOT_FOUND, headers={'X-Group-Id': OWN_GROUP})
add('retrieve-deleted-not-found', 'owner', 'GET', detail('gone'), 404, SUB_NOT_FOUND)
add('retrieve-foreign-not-found', 'owner', 'GET', detail('far'), 404, SUB_NOT_FOUND)
add('retrieve-malformed-not-found', 'owner', 'GET', detail('abc'), 404, NOT_FOUND)
add('retrieve-athlete-not-association', 'athlete', 'GET', detail('cur'), 403, NOT_ASSOCIATION)
add('detail-post-not-allowed', 'owner', 'POST', detail('cur'), 405, method_not_allowed('POST'), body={})
add('detail-options', 'owner', 'OPTIONS', detail('cur'), 200, named('Instance', 'PUT'))
add('update-anonymous', 'anonymous', 'PUT', detail('cur'), 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('partial-update-restricted-unmapped', 'restricted_collaborator', 'PATCH', detail('cur'), 403,
    unmapped(f'personas-subscriptions/{SUB_IDS["cur"]}'), body={})
add('destroy-deleted-not-found', 'owner', 'DELETE', detail('gone'), 404, SUB_NOT_FOUND)
add('destroy-foreign-not-found', 'owner', 'DELETE', detail('far'), 404, SUB_NOT_FOUND)
add('destroy-malformed-not-found', 'owner', 'DELETE', detail('abc'), 404, NOT_FOUND)
add('destroy-athlete-not-association', 'athlete', 'DELETE', detail('old'), 403, NOT_ASSOCIATION)
add('destroy-owner', 'owner', 'DELETE', detail('old'), 204, EMPTY, effect=destroyed('owner', 'old'))
add('destroy-again-not-found', 'owner', 'DELETE', detail('old'), 404, SUB_NOT_FOUND)
add('destroy-format-json-with-tags', 'full_collaborator', 'DELETE', detail('cur', '.json'), 204, EMPTY, effect=destroyed('full_collaborator', 'cur'))

# ---------------------------------------------------------------- writes ------------------------------------------------
# Stock CreateModelMixin.create / UpdateModelMixin.update over AssociateSubscriptionSerializer (ModelSerializer, every
# Subscription field but signature_storage_key; associate and medical are read-only). Field validation runs in
# serializer field order; tenant_scope's PermissionDenied on a related field escapes every collected error.
# create: serializer.save() -> Subscription.objects.create(**validated_data) -> SubscriptionManager.create, whose
# subscription_exists() needs associate or associate_data (never in validated_data) and raises Django's
# ValidationError: 500, nothing written. update: get_object() (get_queryset first), validation (partial for PATCH),
# setattr of each validated field, Subscription.save() (dates of None/empty start or end from the owner's settings,
# auditlog's update entry of the changed fields, indexed directly), tags.set(), then the serializer renders the
# in-memory instance.
REQUIRED, NULL = 'This field is required.', 'This field may not be null.'
DATE_FORMAT = 'Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'
DATETIME_FORMAT = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
VALID = {'sport_association': OWN, 'user': OWNER}
REQUIRED_BODY = {'sport_association': [REQUIRED], 'user': [REQUIRED]}
TAKEN = {'subscription_id': ['subscription with this subscription id already exists.']}
STATE = {}  # label -> rendered fields changed by the authored updates


def current(label, group=None):
    return {**subscription(label, group), **STATE.get(label, {})}


def updated(actor, label, columns, *, changes=None, tags=None):
    return {'kind': 'update', 'actor': actor, 'subscription': SUB_IDS[label], 'columns': columns, 'changes': changes, 'tags': tags}


add('create-owner-required', 'owner', 'POST', LIST, 400, REQUIRED_BODY, body={})
add('create-format-json-required', 'owner', 'POST', '/personas-subscriptions.json', 400, REQUIRED_BODY, body={'notes': 'x'})
add('create-not-a-dict', 'owner', 'POST', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    body=[VALID])
add('create-field-errors', 'owner', 'POST', LIST, 400, {
    'subscription_id': ['Must be a valid UUID.'], 'type': ['"9" is not a valid choice.'], 'role': ['"True" is not a valid choice.'],
    'status_flag': [NULL], 'acceptance_date': [DATE_FORMAT], 'resignation_date': [DATE_FORMAT], 'creation_date': [DATETIME_FORMAT],
    'start_date': [DATE_FORMAT], 'signature_url': ['Ensure this field has no more than 1024 characters.'],
    'archived': ['Must be a valid boolean.'], 'draft': [NULL], 'subscription_number': ['This field may not be blank.'],
    'subscription_type': ['Not a valid string.'], 'deleted': ['Must be a valid boolean.'], 'notes': ['Not a valid string.'],
    'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.'], 'supplier': ['“abc” is not a valid UUID.'],
    'instructor': ['Incorrect type. Expected pk value, received bool.'], 'user': [NULL], 'document_pdf': ['“1.5” is not a valid UUID.'],
    'payment': ['“[1]” is not a valid UUID.'], 'tags': [NULL]}, body={
    'subscription_id': 'abc', 'custom_data': {'free': True}, 'type': 9, 'role': True, 'status_flag': None, 'acceptance_date': '2026-01-31',
    'resignation_date': '31/02/2026', 'creation_date': 'yesterday', 'start_date': 20260101, 'end_date': '31/12/2026',
    'signature_url': 'u' * 1025, 'archived': 'maybe', 'draft': None, 'subscription_number': '  ', 'subscription_type': True, 'deleted': [],
    'notes': ['x'], 'sport_association': UNKNOWN, 'supplier': 'abc', 'instructor': True, 'user': None, 'document_pdf': 1.5, 'payment': [1],
    'tags': None})
add('create-subscription-id-taken', 'full_collaborator', 'POST', LIST, 400, TAKEN, body={**VALID, 'subscription_id': SUB_IDS['future']})
add('create-tags-unknown', 'owner', 'POST', LIST, 400, {'tags': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    body={**VALID, 'tags': [TAG['id'], UNKNOWN, 'abc']})
add('create-tags-not-a-list', 'owner', 'POST', LIST, 400, {'tags': ['Expected a list of items but got type "str".']},
    body={**VALID, 'tags': TAG['id']})
add('create-foreign-association-denied', 'owner', 'POST', LIST, 403, GENERIC, body={'sport_association': FOREIGN, 'user': OWNER, 'type': 9})
add('create-deleted-association-denied', 'owner', 'POST', LIST, 403, GENERIC, body={'sport_association': IDS['deleted_association'],
                                                                                    'user': OWNER})
add('create-foreign-user-denied', 'full_collaborator', 'POST', LIST, 403, GENERIC, body={**VALID, 'user': IDS['foreign_owner']})
add('create-foreign-group-denied', 'owner', 'POST', LIST, 403, GENERIC, body={**VALID, 'group': FOREIGN_GROUP})
add('create-owner-server-error', 'owner', 'POST', LIST, 500, SERVER,
    body={**VALID, 'tags': [TAG['id']], 'group': OWN_GROUP, 'notes': 'nuova', 'start_date': '01/01/2026', 'custom_data': {'a': 1}})
add('create-athlete-server-error', 'athlete', 'POST', LIST, 500, SERVER, body={'sport_association': FOREIGN, 'user': OWNER})
add('create-group-header-id-free-server-error', 'owner', 'POST', LIST, 500, SERVER, body={**VALID, 'subscription_id': SUB_IDS['future']},
    headers={'X-Group-Id': OWN_GROUP})

add('update-athlete-not-association', 'athlete', 'PUT', detail('future'), 403, NOT_ASSOCIATION, body={})
add('update-owner-without-association-server-error', 'owner_without_association', 'PUT', detail('future'), 500, SERVER, body={})
add('update-deleted-not-found', 'owner', 'PUT', detail('gone'), 404, SUB_NOT_FOUND, body=VALID)
add('update-foreign-not-found', 'owner', 'PATCH', detail('far'), 404, SUB_NOT_FOUND, body={'notes': 'x'})
add('update-malformed-not-found', 'owner', 'PATCH', detail('abc'), 404, NOT_FOUND, body={})
add('update-other-group-not-found', 'owner', 'PATCH', detail('future'), 404, SUB_NOT_FOUND, body={}, headers={'X-Group-Id': OWN_GROUP})
add('update-not-found-before-parse', 'owner', 'PATCH', detail('gone'), 404, SUB_NOT_FOUND, raw_body='{')
add('update-put-required', 'owner', 'PUT', detail('future'), 400, REQUIRED_BODY, body={'notes': 'x'})
add('update-subscription-id-taken', 'owner', 'PATCH', detail('future'), 400, TAKEN, body={'subscription_id': SUB_IDS['lone_past']})
add('update-validation', 'owner', 'PATCH', detail('future'), 400, {
    'type': [NULL], 'start_date': [DATE_FORMAT], 'tags': ['Incorrect type. Expected pk value, received bool.']},
    body={'type': None, 'start_date': '2099-01-01', 'tags': [True]})
add('update-foreign-association-denied', 'owner', 'PATCH', detail('future'), 403, GENERIC, body={'sport_association': FOREIGN, 'type': 9})
add('update-foreign-user-denied', 'owner', 'PATCH', detail('future'), 403, GENERIC, body={'user': IDS['foreign_owner']})

STATE['future'] = {'notes': 'Nota nuova', 'competitive': True, 'custom_data': {'z': 1, 'a': None}, 'tags': [TAG['id']]}
add('partial-update-owner', 'owner', 'PATCH', detail('future'), 200, current('future'), body={
    'subscription_id': SUB_IDS['future'], 'notes': 'Nota nuova', 'tags': [TAG['id']], 'competitive': 1, 'custom_data': {'z': 1, 'a': None}},
    effect=updated('owner', 'future', {'notes': 'Nota nuova', 'competitive': True, 'custom_data': {'z': 1, 'a': None}},
                   changes={'notes': ['None', 'Nota nuova'], 'competitive': ['False', 'True'], 'custom_data': ['null', '{"a": null, "z": 1}']},
                   tags=[TAG['id']]))
add('partial-update-unchanged', 'owner', 'PATCH', detail('future'), 200, current('future'), body={'notes': 'Nota nuova', 'draft': True},
    effect=updated('owner', 'future', {}))
STATE['future']['tags'] = []
add('partial-update-format-json-tags-cleared', 'full_collaborator', 'PATCH', detail('future', '.json'), 200, current('future'),
    body={'tags': [], 'payment': None, 'document_pdf': None, 'supplier': None, 'instructor': None, 'group': None},
    effect=updated('full_collaborator', 'future', {}, tags=[]))
STATE['future']['user'] = IDS['athlete']
add('partial-update-user-without-association', 'owner', 'PATCH', detail('future'), 200, current('future'), body={'user': IDS['athlete']},
    effect=updated('owner', 'future', {'user_id': IDS['athlete']}, changes={'user': [OWNER, IDS['athlete']]}))

STATE['lone_past'] = {'type': 3, 'role': 5, 'status_flag': 2, 'acceptance_date': '2018-03-15', 'subscription_type': 'Agonistica',
                      'meta': None, 'creation_date': '2024-03-01T09:30:00.250000Z', 'tags': [TAG['id']]}
add('update-format-json-collaborator', 'full_collaborator', 'PUT', detail('lone_past', '.json'), 200, current('lone_past'), body={
    **VALID, 'subscription_id': SUB_IDS['lone_past'], 'type': '3', 'role': 5, 'status_flag': 2, 'acceptance_date': '15/03/2018',
    'subscription_type': '  Agonistica  ', 'meta': None, 'creation_date': '2024-03-01T10:30:00.250000+01:00',
    'tags': [TAG['id'], TAG['id']], 'trial': 'false'},
    effect=updated('full_collaborator', 'lone_past', {'type': 3, 'role': 5, 'status_flag': 2, 'acceptance_date': '2018-03-15',
                                                      'subscription_type': 'Agonistica', 'meta': None,
                                                      'creation_date': '2024-03-01T09:30:00.250000Z'},
                   changes={'type': ['2', '3'], 'role': ['1', '5'], 'status_flag': ['1', '2'], 'acceptance_date': ['None', '2018-03-15'],
                            'subscription_type': ['None', 'Agonistica'], 'meta': ['{"n": 1}', 'null'],
                            'creation_date': ['2024-03-01 09:00:00', '2024-03-01 09:30:00.250000']},
                   tags=[TAG['id']]))
# Subscription.save(): no start/end date -> the season of creation_date (LIKE_SEASON_YEAR from 1 January).
STATE['lone_past'].update({'start_date': '2024-01-01', 'end_date': '2024-12-31', 'tags': []})
add('partial-update-dates-from-settings', 'owner', 'PATCH', detail('lone_past'), 200, current('lone_past'),
    body={'start_date': None, 'end_date': None, 'tags': []},
    effect=updated('owner', 'lone_past', {'start_date': '2024-01-01', 'end_date': '2024-12-31'},
                   changes={'start_date': ['2018-01-01', '2024-01-01'], 'end_date': ['2018-12-31', '2024-12-31']}, tags=[]))
STATE['grouped'] = {'archived': False, 'deleted': True}
add('partial-update-own-group-deleted', 'owner', 'PATCH', detail('grouped'), 200, current('grouped', OWN_GROUP),
    body={'archived': 'no', 'deleted': 'on', 'group': OWN_GROUP}, headers={'X-Group-Id': OWN_GROUP},
    effect=updated('owner', 'grouped', {'archived': False, 'deleted': True}, changes={'archived': ['True', 'False'], 'deleted': ['False', 'True']}))
add('update-deleted-by-patch-not-found', 'owner', 'PATCH', detail('grouped'), 404, SUB_NOT_FOUND, body={})

# The class's own delete() on the <pk>/list route: get_object() on the live, group-aware subscriptions of the association,
# then deleted=True and Subscription.save() (auditlog's update entry, indexed directly), 204.
LEGACY = lambda label, group=None: f'/personas-subscriptions/{SUB_IDS.get(label, label)}/list'
LEGACY_ALLOW = ['DELETE', 'GET', 'HEAD', 'OPTIONS']
add('legacy-delete-owner', 'owner', 'DELETE', LEGACY('future'), 204, EMPTY, allow=LEGACY_ALLOW,
    effect=updated('owner', 'future', {'deleted': True}, changes={'deleted': ['False', 'True']}))
add('legacy-delete-again-not-found', 'owner', 'DELETE', LEGACY('future'), 404, SUB_NOT_FOUND, allow=LEGACY_ALLOW)
add('legacy-delete-foreign-association-not-found', 'owner', 'DELETE', LEGACY('far'), 404, SUB_NOT_FOUND, allow=LEGACY_ALLOW)
add('legacy-delete-other-group-not-found', 'owner', 'DELETE', LEGACY('lone_past'), 404, SUB_NOT_FOUND,
    headers={'X-Group-Id': FOREIGN_GROUP}, allow=LEGACY_ALLOW)
add('legacy-delete-full-collaborator', 'full_collaborator', 'DELETE', LEGACY('lone_past'), 204, EMPTY, allow=LEGACY_ALLOW,
    effect=updated('full_collaborator', 'lone_past', {'deleted': True}, changes={'deleted': ['False', 'True']}))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'DefaultRouter personas-subscriptions routes with Collector delete effects',
    'ids': IDS, 'families': [F1, F2], 'associates': SEED, 'relations': RELATIONS, 'medical': MEDICAL, 'tag': TAG,
    'subscriptions': {label: {'id': SUB_IDS[label], 'associate': A[a], 'start': s, 'end': e, 'created': c, **x}
                      for label, (a, s, e, c, x) in SUBSCRIPTIONS.items()},
    'reverse': REVERSE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
