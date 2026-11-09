"""Author the DefaultRouter course-subscriptions read, update and delete HTTP sequence (router-stock.md).

`router.register(r'course-subscriptions', CourseSubscriptionViewSet)`. The
list route binds GET to list() (User.sport_association: athletes 403) and the
viewset's own delete() (no pk: athletes 403 from get_object()'s
User.sport_association, others 500); POST is the stock create
(router-course-subscriptions-create). The detail route binds GET to the stock
retrieve() through the viewset's get_object(): User.sport_association, the
live rows of its courses, django.shortcuts.get_object_or_404 (a pk the UUID
field rejects is a 500); PUT to update() (partial, atomic); PATCH to
partial_update() (500); DELETE to the stock destroy(), whose get_object() runs
as action 'destroy' and so finds live rows only (the delete route also finds
deleted ones), then the model's soft delete. OPTIONS describes the serializer
for POST and, when get_object() succeeds or raises an APIException, for PUT
(a pk the UUID field rejects raises a Django ValidationError: 500). The
format twins: list() and update() reject format= (500). Expectations reuse
the course-subscriptions rendering and effect helpers (their seed and
authoring helpers are loaded from generate_course_subscriptions_http.py, up
to its first case). Run with ``python3 -I``.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
SHARED = (HERE / 'generate_course_subscriptions_http.py').read_text()
namespace = {'__file__': str(HERE / 'generate_course_subscriptions_http.py'), '__name__': 'course_subscriptions_helpers'}
exec(compile(SHARED[:SHARED.index('\nYOGA = (')], 'generate_course_subscriptions_http.py', 'exec'), namespace)
globals().update({key: value for key, value in namespace.items() if not key.startswith('__') and key not in ('add', 'cases', 'OUTPUT')})
OUTPUT = HERE / 'router_course_subscriptions_http.json'
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'router-course-subscriptions-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def updated(actor, label, fields, changes, *, payments=None, index=None, sent=()):
    """A partial update: the stored row takes fields; changes are auditlog's pairs;
    sent names the datetime/JSON fields the serializer set (their in-memory types)."""
    row = STATE['cs'][label]
    row.update(fields)
    audit = [changed_entry('coursesubscription', label, index or [OWN, 'course.sport_association'], changes, sent)] if changes else []
    payment_changes = {}
    for pay, values, pay_changes in payments or ():
        STATE['payments'][pay].update(values)
        payment_changes[pay] = values
        audit.append(changed_entry('payment', pay, [OWN, 'direct'], pay_changes))
    return {'kind': 'update', 'actor': actor, 'cs_changed': {label: fields}, 'cs_deleted': [], 'installments_deleted': [],
            'payments_deleted': [], 'payments_changed': payment_changes, 'audit': audit}



ROUTER_LIST = '/course-subscriptions/'
detail = lambda label, suffix='/': f'/course-subscriptions/{CS[label]["id"] if label in CS else label}{suffix}'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path}'. Contact administrator to grant access."}
named = lambda suffix, actions=None: {**OPTIONS, 'name': f'Course Subscription {suffix}', **({'actions': actions} if actions else {})}
FIELDS = LIST_OPTIONS['actions']['POST']
YOGA = ('cs_yoga', 'cs_carnet', 'cs_nosub', 'cs_senza', 'cs_gino', 'cs_del', 'cs_del2', 'cs_del3', 'cs_del4', 'cs_del5', 'cs_bd1', 'cs_bd3')

# The list route.
add('list-anonymous', 'anonymous', ROUTER_LIST, 401, ANONYMOUS)
add('list-admin-without-session', 'admin', ROUTER_LIST, 403, ADMIN)
add('list-restricted-unmapped', 'restricted_collaborator', ROUTER_LIST, 403, unmapped('course-subscriptions'))
add('list-owner-scoped-athlete-denied', 'owner', ROUTER_LIST, 403, SCOPED_DENIED, headers=SCOPED)
add('list-owner', 'owner', ROUTER_LIST, 200, listing(*own_rows()))
add('list-course-filter', 'full_collaborator', f'{ROUTER_LIST}?course_id={C("yoga")}', 200, listing(*YOGA))
add('list-malformed-course-server-error', 'owner', f'{ROUTER_LIST}?course_id=abc', 500, SERVER)
add('list-athlete-not-association', 'athlete', ROUTER_LIST, 403, NOT_ASSOCIATION)
add('list-put-not-allowed', 'owner', ROUTER_LIST, 405, method('PUT'), method='PUT', body={})
add('list-delete-without-pk-server-error', 'owner', ROUTER_LIST, 500, SERVER, method='DELETE')
add('list-delete-athlete-not-association', 'athlete', ROUTER_LIST, 403, NOT_ASSOCIATION, method='DELETE')
add('list-options', 'owner', ROUTER_LIST, 200, named('List', {'POST': FIELDS}), method='OPTIONS')
add('list-format-json-server-error', 'owner', '/course-subscriptions.json', 500, SERVER)
add('list-format-unknown-not-found', 'owner', '/course-subscriptions.pdf', 404, {'detail': 'Not found.'})

# The detail route.
add('detail-anonymous', 'anonymous', detail('cs_yoga'), 401, ANONYMOUS)
add('detail-restricted-unmapped', 'restricted_collaborator', detail('cs_yoga'), 403, unmapped('course-subscriptions/' + CS['cs_yoga']['id']))
add('detail-owner-scoped-athlete-denied', 'owner', detail('cs_yoga'), 403, SCOPED_DENIED, headers=SCOPED)
add('retrieve-owner', 'owner', detail('cs_yoga'), 200, item('cs_yoga'))
add('retrieve-group-header', 'owner', detail('cs_yoga'), 200, item('cs_yoga', 'own_group'), headers=GROUP)
add('retrieve-member-payments', 'full_collaborator', detail('cs_member'), 200, item('cs_member'))
add('retrieve-format-json', 'owner', detail('cs_carnet', '.json'), 200, item('cs_carnet'))
add('retrieve-deleted-not-found', 'owner', detail('cs_deleted'), 404, NOT_FOUND)
add('retrieve-foreign-not-found', 'owner', detail('cs_abroad'), 404, NOT_FOUND)
add('retrieve-unknown-not-found', 'owner', detail(UNKNOWN), 404, NOT_FOUND)
add('retrieve-malformed-server-error', 'owner', detail('abc'), 500, SERVER)
add('retrieve-athlete-not-association', 'athlete', detail('cs_yoga'), 403, NOT_ASSOCIATION)
add('retrieve-owner-without-association-server-error', 'owner_without_association', detail('cs_yoga'), 500, SERVER)
add('detail-post-not-allowed', 'owner', detail('cs_yoga'), 405, method('POST'), method='POST', body={})
add('detail-options', 'owner', detail('cs_yoga'), 200, named('Instance', {'PUT': FIELDS}), method='OPTIONS')
add('detail-options-unknown-no-actions', 'owner', detail(UNKNOWN), 200, named('Instance'), method='OPTIONS')
add('detail-options-athlete-no-actions', 'athlete', detail('cs_yoga'), 200, named('Instance'), method='OPTIONS')
add('detail-options-malformed-server-error', 'owner', detail('abc'), 500, SERVER, method='OPTIONS')
add('partial-update-server-error', 'owner', detail('cs_gino'), 500, SERVER, method='PATCH', body={'paid': True})
add('update-format-json-server-error', 'owner', detail('cs_gino', '.json'), 500, SERVER, method='PUT', body={'paid': True})
add('update-deleted-not-found', 'owner', detail('cs_deleted'), 404, NOT_FOUND, method='PUT', body={'paid': True})
add('update-athlete-not-association', 'athlete', detail('cs_gino'), 403, NOT_ASSOCIATION, method='PUT', body={'paid': True})
add('update-membership-validation', 'owner', detail('cs_member'), 400,
    {'billed_frequency': ['La durata deve essere compresa tra 1 e 12 mesi.']}, method='PUT', body={'billed_frequency': 13})
effect = updated('owner', 'cs_gino', {'paid': True, 'billed_frequency': 2}, {'paid': ['False', 'True'], 'billed_frequency': ['1', '2']})
add('update-owner-put-is-partial', 'owner', detail('cs_gino'), 200, item('cs_gino'), method='PUT', body={'paid': True, 'billed_frequency': 2},
    effect=effect)
add('destroy-deleted-not-found', 'owner', detail('cs_deleted'), 404, NOT_FOUND, method='DELETE')
add('destroy-foreign-not-found', 'owner', detail('cs_abroad'), 404, NOT_FOUND, method='DELETE')
add('destroy-malformed-server-error', 'owner', detail('abc'), 500, SERVER, method='DELETE')
add('destroy-athlete-not-association', 'athlete', detail('cs_del5'), 403, NOT_ASSOCIATION, method='DELETE')
add('destroy-owner', 'owner', detail('cs_del5'), 204, '<empty>', method='DELETE', effect=soft_delete('owner', 'cs_del5'))
add('destroy-again-not-found', 'owner', detail('cs_del5'), 404, NOT_FOUND, method='DELETE')
add('destroy-format-json-installment', 'full_collaborator', detail('cs_del2', '.json'), 204, '<empty>', method='DELETE',
    effect=soft_delete('full_collaborator', 'cs_del2'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'DefaultRouter course-subscriptions list, retrieve, update and destroy routes',
                              'ids': IDS, 'category': CATEGORY, 'document': DOCUMENT, 'associates': ASSOCIATES, 'medicals': MEDICALS,
                              'subscriptions': SUBSCRIPTIONS, 'courses': COURSES, 'payments': PAYMENTS, 'course_subscriptions': CS,
                              'installments': INSTALLMENTS, 'carnets': CARNETS, 'holdings': HOLDINGS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
