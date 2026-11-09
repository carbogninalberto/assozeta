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
partial_update over AssociateSubscriptionSerializer; only their admission is
authored here (the writes are not ported, router-stock.md). Expectations
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


def add(name, actor, method, path, status, response, *, body=None, headers=None, effect=None):
    detail = path.split('?')[0].rstrip('/').count('/') > 1
    case = {'name': f'router-persona-subscriptions-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'allow': DETAIL_ALLOW if detail else LIST_ALLOW, 'effect': effect}
    if body is not None:
        case['body'] = body
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

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'DefaultRouter personas-subscriptions routes with Collector delete effects',
    'ids': IDS, 'families': [F1, F2], 'associates': SEED, 'relations': RELATIONS, 'medical': MEDICAL, 'tag': TAG,
    'subscriptions': {label: {'id': SUB_IDS[label], 'associate': A[a], 'start': s, 'end': e, 'created': c, **x}
                      for label, (a, s, e, c, x) in SUBSCRIPTIONS.items()},
    'reverse': REVERSE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
