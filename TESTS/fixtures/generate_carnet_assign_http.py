"""Author the carnet/<uid>/assign/<uid_subscription> HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed plus bank accounts and course rows. The view
gets the subscription (a non-UUID is a 500; missing: 404 'Subscription not
found.'); other roles must hold it (403 'Not allowed.'). The association is
the owner's (or, for athletes, the subscription's) and must own the
subscription (404) and the carnet (404 'Carnet not found.'; a non-UUID is a
500). A course_subscription_id selects any live course subscription of the
association, not necessarily of this subscription. With a
carnet_subscription_id the existing holding (of the association; for
athletes their own) gets that course subscription, or the URL subscription's
one for course_id (404 'Course subscription not found.'), without a write
audit. Otherwise a payment and a carnet subscription are created as in the
topup, linked to the course subscription. Run with ``python3 -I`` after the
carnet-list generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_assign_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('carnet_list_http.json').read_text()))
TOPUP = json.loads(Path(__file__).with_name('carnet_subscription_topup_http.json').read_text())
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{970001 + n:012d}' for n in range(10))
SOURCE['courses']['abroad_course'] = {'id': next(ids), 'association': FOREIGN, 'title': 'Corso estero'}
SOURCE['course_subscriptions']['second'] = {'id': next(ids), 'course': 'swim', 'subscription': 'current', 'deleted': False}
SOURCE['course_subscriptions']['abroad_cs'] = {'id': next(ids), 'course': 'abroad_course', 'subscription': 'abroad_past', 'deleted': False}
CARNETS, SUBSCRIPTIONS, HOLDINGS, COURSES = SOURCE['carnets'], SOURCE['subscriptions'], SOURCE['carnet_subscriptions'], SOURCE['course_subscriptions']
UNKNOWN = 'a1000000-0000-4000-8000-000000979999'
ref = lambda table, key: table[key]['id'] if key in table else key
path = lambda carnet, subscription: f'/carnet/{ref(CARNETS, carnet)}/assign/{ref(SUBSCRIPTIONS, subscription)}'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, carnet, subscription, status, response, *, body=None, raw_body=None, content_type=None, method='POST',
        headers=None, effect=None):
    case = {'name': f'carnet-assign-{name}', 'actor': actor, 'method': method, 'path': path(carnet, subscription),
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def created(carnet, subscription, actor, account, course=None):
    row, sub = CARNETS[carnet], SUBSCRIPTIONS[subscription]
    return {'kind': 'create', 'carnet': carnet, 'subscription': subscription, 'actor': actor, 'impersonation': None,
            'payment': {'user': sub['user'], 'associate': sub['associate'], 'amount': row['fee'], 'association': row['association'],
                        'account': account, 'group': None},
            'courses': [course] if course else [], 'lessons': row['lessons']}


def linked(holding, course):
    return {'kind': 'link', 'holding': holding, 'course': course}


assigned = lambda course: {'msg': 'Carnet assigned successfully.', 'course_subscription_id': COURSES[course]['id'] if course else None}
NO_SUBSCRIPTION, NO_CARNET = {'msg': 'Subscription not found.'}, {'msg': 'Carnet not found.'}
NO_HOLDING, NO_COURSE = {'msg': 'Carnet subscription not found.'}, {'msg': 'Course subscription not found.'}
add('malformed-subscription-server-error', 'owner', 'c1', 'abc', 500, SERVER)
add('unknown-subscription', 'owner', 'c1', UNKNOWN, 404, NO_SUBSCRIPTION)
add('foreign-subscription', 'owner', 'c1', 'abroad_past', 404, NO_SUBSCRIPTION)
add('athlete-not-holder', 'athlete', 'c1', 'other_open', 403, {'msg': 'Not allowed.'})
# An athlete's association is the subscription's: the own carnet is foreign there.
add('athlete-foreign-association-carnet', 'athlete', 'c1', 'abroad_past', 404, NO_CARNET)
add('malformed-carnet-server-error', 'owner', 'abc', 'current', 500, SERVER)
add('unknown-carnet', 'owner', UNKNOWN, 'current', 404, NO_CARNET)
add('foreign-carnet', 'owner', 'cf', 'current', 404, NO_CARNET)
add('malformed-course-subscription-server-error', 'owner', 'c1', 'current', 500, SERVER, body={'course_subscription_id': 'abc'})
add('unknown-holding', 'owner', 'c1', 'current', 404, NO_HOLDING, body={'carnet_subscription_id': UNKNOWN})
add('foreign-holding', 'owner', 'c1', 'current', 404, NO_HOLDING, body={'carnet_subscription_id': HOLDINGS['abroad']['id']})
add('holding-without-course', 'owner', 'c1', 'current', 404, NO_COURSE, body={'carnet_subscription_id': HOLDINGS['held']['id']})
add('holding-deleted-course', 'owner', 'c1', 'current', 404, NO_COURSE,
    body={'carnet_subscription_id': HOLDINGS['held']['id'], 'course_subscription_id': COURSES['deleted']['id']})
add('athlete-other-holder-holding', 'athlete', 'c1', 'current', 404, NO_HOLDING,
    body={'carnet_subscription_id': HOLDINGS['someone_else']['id'], 'course_subscription_id': COURSES['second']['id']})
add('owner-scoped-athlete-denied', 'owner', 'c1', 'current', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 'current', 403, {'detail': 'Missing permission: association.carnet.update'})
add('admin-without-session', 'admin', 'c1', 'current', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'c1', 'current', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'c1', 'current', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'c1', 'current', 200, {'name': 'Carnet Assign', 'description': 'Add a new carnet to a subscription',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('holding-adds-course', 'owner', 'c1', 'current', 200, assigned('second'),
    body={'carnet_subscription_id': HOLDINGS['held']['id'], 'course_subscription_id': COURSES['second']['id']}, effect=linked('held', 'second'))
add('holding-existing-link', 'owner', 'c1', 'current', 200, assigned('active'),
    body={'carnet_subscription_id': HOLDINGS['held']['id'], 'course_subscription_id': COURSES['active']['id']}, effect=linked('held', None))
# course_id resolves against the URL subscription, not the holding's.
add('holding-course-id', 'owner', 'c1', 'current', 200, assigned('active'),
    body={'carnet_subscription_id': HOLDINGS['family']['id'], 'course_id': SOURCE['courses']['swim']['id']}, effect=linked('family', 'active'))
add('owner-creates', 'owner', 'c1', 'current', 200, assigned(None), effect=created('c1', 'current', 'owner', 'bank'))
# The course subscription only has to belong to the association.
add('owner-other-subscription-course', 'owner', 'c2', 'other_open', 200, assigned('active'),
    body={'course_subscription_id': COURSES['active']['id']}, effect=created('c2', 'other_open', 'owner', 'bank', 'active'))
add('owner-deleted-course-ignored', 'owner', 'c1', 'current', 200, assigned(None),
    body={'course_subscription_id': COURSES['deleted']['id']}, effect=created('c1', 'current', 'owner', 'bank'))
add('owner-foreign-course-ignored', 'owner', 'c1', 'family_next', 200, assigned(None),
    body={'course_subscription_id': COURSES['abroad_cs']['id']}, effect=created('c1', 'family_next', 'owner', 'bank'))
add('athlete-self-assign', 'athlete', 'c1', 'current', 200, assigned(None), effect=created('c1', 'current', 'athlete', 'bank'))
add('athlete-foreign-association', 'athlete', 'cf', 'abroad_past', 200, assigned('abroad_cs'),
    body={'course_subscription_id': COURSES['abroad_cs']['id']}, effect=created('cf', 'abroad_past', 'athlete', None, 'abroad_cs'))
add('full-collaborator-form', 'full_collaborator', 'c1', 'current', 200, assigned('second'),
    raw_body='course_subscription_id=' + COURSES['second']['id'], content_type='application/x-www-form-urlencoded',
    effect=created('c1', 'current', 'full_collaborator', 'bank', 'second'))
# Subscription.objects filters by the raw X-Group-Id.
add('group-header-hides-subscription', 'owner', 'c1', 'current', 404, NO_SUBSCRIPTION, headers={'X-Group-Id': IDS['own_group']})
# A group the UUID field rejects is ignored by GroupAwareManager (it catches the
# ValidationError): lookups are unfiltered, a link succeeds, and only
# PaymentManager.create fails storing it.
add('invalid-group-server-error', 'owner', 'c1', 'current', 500, SERVER, headers={'X-Group-Id': 'abc'})
add('invalid-group-link', 'owner', 'c1', 'current', 200, assigned('second'), headers={'X-Group-Id': 'abc'},
    body={'carnet_subscription_id': HOLDINGS['family']['id'], 'course_subscription_id': COURSES['second']['id']},
    effect=linked('family', 'second'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'accounts': TOPUP['accounts'],
                              'scope': 'Carnet assign authored sequence with created rows, course links and audit effects',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
