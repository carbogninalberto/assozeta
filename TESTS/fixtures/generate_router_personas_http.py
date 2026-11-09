"""Author the DefaultRouter personas HTTP sequence (router-stock.md).

`router.register(r'personas', AssociateViewSet)`. Every action reads
request.user.sport_association (athletes 403, owners without an association
500). The list route binds GET to list() (KTDatatablePagination) and POST to
the stock CreateModelMixin.create: request.data is not popped, so
AssociateSerializer validates tutors_data as its ListField and any dict
body; perform_create then reads the association (after validation, unlike
add) and save() runs ModelSerializer.create, where a tutors_data key reaches
Associate() as a keyword (TypeError: 500, nothing written); without it the
create is add's with no tutors (201). The viewset's own delete() answers
DELETE on the list route (athletes 403, others 500). The detail route binds
GET to the stock retrieve() (get_object() through get_queryset: live,
group-aware, the query parameters; AssociateSerializer, as info), PUT to
update() (partial), PATCH to partial_update() (500) and DELETE to the stock
destroy(): Associate.delete(), a hard delete through Django's Collector (the
tutor relations cascade), not the viewset's soft delete. Format twins:
list() and update() reject format= (500). HTML form input on create is not
ported (refused). Expectations reuse the personas-writes seed and authoring
helpers (loaded from generate_personas_writes_http.py up to its first case).
Run with ``python3 -I``.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
SHARED = (HERE / 'generate_personas_writes_http.py').read_text()
namespace = {'__file__': str(HERE / 'generate_personas_writes_http.py'), '__name__': 'personas_writes_helpers'}
exec(compile(SHARED[:SHARED.index('# ================================================================= transport')],
             'generate_personas_writes_http.py', 'exec'), namespace)
globals().update({key: value for key, value in namespace.items() if not key.startswith('__') and key not in ('add', 'cases', 'OUTPUT')})
OUTPUT = HERE / 'router_personas_http.json'
cases = []
LIST_ALLOW = ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'POST']
DETAIL_ALLOW = ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'PUT']


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None):
    detail = path.split('?')[0].rstrip('/').count('/') > 1
    case = {'name': f'router-personas-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'allow': DETAIL_ALLOW if detail else LIST_ALLOW,
            'effect': effect if isinstance(effect, dict) or effect is None else effect.document()}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def destroyed(actor, ref):
    """Associate.delete(): the tutor relations naming it (either side) cascade first, then the row."""
    relations = sorted((label for label, r in WORLD['rel'].items() if ref in (r['associate'], r['tutor'])),
                       key=lambda label: WORLD['rel'][label]['id'])
    for label in relations:
        WORLD['rel'].pop(label)
    WORLD['assoc'][ref] = {**WORLD['assoc'][ref], 'deleted': 'hard'}
    actor_index = [ACTOR_ASSOCIATION[actor], 'actor.sport_association'] if ACTOR_ASSOCIATION[actor] else None
    return {'kind': 'destroy', 'actor': actor, 'associate': ref, 'relations': relations, 'relation_index': actor_index,
            'associate_index': [row_of(ref)['association'], 'changes_json.sport_association']}


LIST = '/personas/'
detail = lambda ref, suffix='/': f'/personas/{A.get(ref, ref)}{suffix}'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
named = lambda suffix, actions=None: {**OPTIONS, 'name': f'Associate {suffix}', **({'actions': actions} if actions else {})}
EMPTY_PAGE = {'data': [], 'meta': {'total': 0, 'page': 1, 'pages': 1, 'perpage': 10, 'rowIds': []}}

# The list route.
add('list-anonymous', 'anonymous', 'GET', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-admin-without-session', 'admin', 'GET', LIST, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('list-restricted-unmapped', 'restricted_collaborator', 'GET', LIST, 403, unmapped('personas'))
add('list-owner-scoped-athlete-denied', 'owner', 'GET', LIST, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers=SCOPED_ATHLETE)
add('list-athlete-not-association', 'athlete', 'GET', LIST, 403, NOT_ASSOCIATION)
add('list-owner-without-association-server-error', 'owner_without_association', 'GET', LIST, 500, SERVER)
add('list-owner-search-empty', 'owner', 'GET', LIST + '?query[generalSearch]=Zqxjkw', 200, EMPTY_PAGE)
add('list-bad-age-filter-server-error', 'owner', 'GET', LIST + '?query[minAge]=x', 500, SERVER)
add('list-put-not-allowed', 'owner', 'PUT', LIST, 405, method_not_allowed('PUT'), body={})
add('list-delete-without-pk-server-error', 'owner', 'DELETE', LIST, 500, SERVER)
add('list-delete-athlete-not-association', 'athlete', 'DELETE', LIST, 403, NOT_ASSOCIATION)
add('list-options', 'owner', 'OPTIONS', LIST, 200, named('List', ACTIONS['POST'] and {'POST': ACTIONS['POST']}))
add('list-format-json-server-error', 'owner', 'GET', '/personas.json', 500, SERVER)
# create (stock)
add('create-list-body', 'owner', 'POST', LIST, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    body=[{'first_name': 'X'}])
# to_internal_value starts with data.copy(): a string has none (500); a list copies, then fails the dict check.
add('create-string-body-server-error', 'owner', 'POST', LIST, 500, SERVER, body='x')
add('create-validation', 'owner', 'POST', LIST, 400, {'sex': ['"X" is not a valid choice.'], 'email': ['Enter a valid email address.'],
                                                       'tutors_data': ['Expected a list of items but got type "str".']},
    body={'sex': 'X', 'email': 'nope', 'tutors_data': 'x'})
add('create-foreign-association', 'owner', 'POST', LIST, 403, GENERIC, body={'sport_association': FOREIGN})
add('create-athlete-validation-first', 'athlete', 'POST', LIST, 400, {'email': ['Enter a valid email address.']}, body={'email': 'nope'})
add('create-athlete-not-association', 'athlete', 'POST', LIST, 403, NOT_ASSOCIATION, body={'first_name': 'Atleta'})
add('create-owner-without-association-server-error', 'owner_without_association', 'POST', LIST, 500, SERVER, body={'first_name': 'X'})
add('create-tutors-data-server-error', 'owner', 'POST', LIST, 500, SERVER, body={'first_name': 'Con', 'tutors_data': []})
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Router', last_name='Nuovo', sex='F', born_date='2010-03-04', email='router@example.test')
add('create-owner', 'owner', 'POST', LIST, 201, info(ref),
    body={'first_name': 'Router', 'last_name': 'Nuovo', 'sex': 'F', 'born_date': '04/03/2010', 'email': 'router@example.test',
          'associate_id': UNKNOWN, 'tutors': []}, effect=effect)
effect = Effect('full_collaborator')
ref = effect.create(user=OWNER, first_name='Formato')
add('create-format-json', 'full_collaborator', 'POST', '/personas.json/', 201, info(ref), body={'first_name': 'Formato'}, effect=effect)

# The detail route.
add('detail-anonymous', 'anonymous', 'GET', detail('enzo'), 401, {'detail': 'Authentication credentials were not provided.'})
add('detail-restricted-unmapped', 'restricted_collaborator', 'GET', detail('enzo'), 403, unmapped(f'personas/{A["enzo"]}'))
add('retrieve-owner', 'owner', 'GET', detail('upd'), 200, info('upd'))
add('retrieve-own-group', 'owner', 'GET', detail('dora'), 200, info('dora', OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('retrieve-format-json', 'full_collaborator', 'GET', detail('enzo', '.json'), 200, info('enzo'))
add('retrieve-other-group-not-found', 'owner', 'GET', detail('dora'), 404, NO_MATCH, headers={'X-Group-Id': FOREIGN_GROUP})
add('retrieve-filtered-out', 'owner', 'GET', detail('enzo') + '?query[type]=tutors', 404, NO_MATCH)
add('retrieve-soft-deleted-not-found', 'owner', 'GET', detail('ghost'), 404, NO_MATCH)
add('retrieve-foreign-not-found', 'owner', 'GET', detail('abroad'), 404, NO_MATCH)
add('retrieve-malformed-not-found', 'owner', 'GET', detail('abc'), 404, NOT_FOUND)
add('retrieve-athlete-not-association', 'athlete', 'GET', detail('enzo'), 403, NOT_ASSOCIATION)
add('detail-post-not-allowed', 'owner', 'POST', detail('enzo'), 405, method_not_allowed('POST'), body={})
add('detail-options', 'owner', 'OPTIONS', detail('enzo'), 200, named('Instance', {'PUT': ACTIONS['POST']}))
add('detail-options-athlete-no-actions', 'athlete', 'OPTIONS', detail('enzo'), 200, named('Instance'))
add('detail-options-malformed-no-actions', 'owner', 'OPTIONS', detail('abc'), 200, named('Instance'))
add('partial-update-server-error', 'owner', 'PATCH', detail('collab_upd'), 500, SERVER, body={'notes': 'Mai'})
add('update-format-json-server-error', 'owner', 'PUT', detail('collab_upd', '.json'), 500, SERVER, body={'notes': 'Mai'})
add('update-foreign-not-found', 'owner', 'PUT', detail('abroad'), 404, NO_MATCH, body={'notes': 'x'})
add('update-tutors-data-dict', 'owner', 'PUT', detail('collab_upd'), 400, {'tutors_data': ['Expected a list of items but got type "dict".']},
    body={'tutors_data': {}})
effect = Effect('full_collaborator')
effect.save('collab_upd', phone='3330002222', sex='M')
add('update-full-collaborator-put-is-partial', 'full_collaborator', 'PUT', detail('collab_upd'), 200, info('collab_upd'),
    body={'phone': '3330002222', 'sex': 'M'}, effect=effect)
add('destroy-foreign-not-found', 'owner', 'DELETE', detail('abroad'), 404, NO_MATCH)
add('destroy-soft-deleted-not-found', 'owner', 'DELETE', detail('ghost'), 404, NO_MATCH)
add('destroy-malformed-not-found', 'owner', 'DELETE', detail('abc'), 404, NOT_FOUND)
add('destroy-athlete-not-association', 'athlete', 'DELETE', detail('enzo'), 403, NOT_ASSOCIATION)
add('destroy-owner-hard-delete-with-relation', 'owner', 'DELETE', detail('wipe'), 204, EMPTY, effect=destroyed('owner', 'wipe'))
add('destroy-again-not-found', 'owner', 'DELETE', detail('wipe'), 404, NO_MATCH)
add('destroy-format-json-tutor-side', 'full_collaborator', 'DELETE', detail('swap_t1', '.json'), 204, EMPTY,
    effect=destroyed('full_collaborator', 'swap_t1'))
add('destroy-plain', 'owner', 'DELETE', detail('enzo'), 204, EMPTY, effect=destroyed('owner', 'enzo'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'DefaultRouter personas routes with associate, tutor relation and audit effects',
    'ids': IDS, 'families': [F1, F2], 'associates': SEED, 'relations': RELATIONS, 'medical': MEDICAL, 'tag': TAG,
    'subscriptions': {label: {'id': SUB_IDS[label], 'associate': A[a], 'start': s, 'end': e, 'created': c, **x}
                      for label, (a, s, e, c, x) in SUBSCRIPTIONS.items()},
    'reverse': REVERSE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
