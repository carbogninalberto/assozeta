"""Author the carnet/<uid>/replace/<uid_subscription> HTTP sequence from carnet-writes.md.

Builds on the carnet-assign seed plus installments and payments of a course
subscription. Associations only. A body course_subscription_id selects a live
course subscription of the association's courses (a non-UUID is a 500); its
unpaid installments (group-aware) are deleted, each payment first: the
Payment pre_delete signal deletes the payment's installments, whose own
signal deletes the unpaid payment, so auditlog records the payment, the
installment, the payment again (the outer collector) and the installment
again (its own delete after the payment's pk was cleared). The course
subscription's unpaid payment is deleted next. carnet_assign then runs on
the same request; its response is discarded. Run with ``python3 -I`` after
the carnet-assign generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_replace_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('carnet_assign_http.json').read_text()))
IDS = SOURCE['ids']
OWN = IDS['own_association']
ids = iter(f'a1000000-0000-4000-8000-{980001 + n:012d}' for n in range(20))
for label, paid in (('p_main', False), ('p_i1', False), ('p_i2', True), ('p_other', False)):
    SOURCE['payments'][label] = {'id': next(ids), 'association': OWN, 'user': 'athlete', 'amount': '10.00', 'paid': paid}
SOURCE['course_payments'] = {'active': 'p_main'}
SOURCE['installments'] = {
    'i1': {'id': next(ids), 'course_subscription': 'active', 'number': 1, 'paid': False, 'payment': 'p_i1'},
    'i2': {'id': next(ids), 'course_subscription': 'active', 'number': 2, 'paid': True, 'payment': 'p_i2'},
    'i3': {'id': next(ids), 'course_subscription': 'active', 'number': 3, 'paid': False, 'payment': None},
    'i_other': {'id': next(ids), 'course_subscription': 'second', 'number': 1, 'paid': False, 'payment': 'p_other'},
}
CARNETS, SUBSCRIPTIONS, COURSES = SOURCE['carnets'], SOURCE['subscriptions'], SOURCE['course_subscriptions']
UNKNOWN = 'a1000000-0000-4000-8000-000000989999'
ref = lambda table, key: table[key]['id'] if key in table else key
path = lambda carnet, subscription: f'/carnet/{ref(CARNETS, carnet)}/replace/{ref(SUBSCRIPTIONS, subscription)}'
cases = []


def add(name, actor, carnet, subscription, status, response, *, body=None, raw_body=None, content_type=None, method='POST',
        headers=None, effect=None):
    case = {'name': f'carnet-replace-{name}', 'actor': actor, 'method': method, 'path': path(carnet, subscription),
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


OK = {'msg': 'Carnet added successfully.'}
SERVER = '<stable-server-error-page>'
OWN_INDEX = [OWN, 'actor.sport_association']


def created(carnet, subscription, actor, course=None):
    row, sub = CARNETS[carnet], SUBSCRIPTIONS[subscription]
    return {'carnet': carnet, 'subscription': subscription, 'actor': actor,
            'payment': {'user': sub['user'], 'associate': sub['associate'], 'amount': row['fee'], 'association': row['association'],
                        'account': 'bank' if row['association'] == OWN else None, 'group': None},
            'courses': [course] if course else [], 'lessons': row['lessons']}


def paid_away(payment):
    return ['payment', payment, [SOURCE['payments'][payment]['association'], 'changes_json.sport_association']]


def effect(actor, deleted=(), create=None, link=None):
    return {'actor': actor, 'deleted': list(deleted), 'create': create, 'link': link}


add('group-header-finds-nothing', 'owner', 'c1', 'current', 200, OK, headers={'X-Group-Id': IDS['own_group']},
    body={'course_subscription_id': COURSES['active']['id']})
# GroupAwareManager ignores a group the UUID field rejects (it catches the
# ValidationError), so the deletes run; PaymentManager.create then stores it: 500.
# A collaborator actor has no association: the installment entries are not indexed.
add('invalid-group-deletes-then-server-error', 'full_collaborator', 'c1', 'current', 500, SERVER, headers={'X-Group-Id': 'abc'},
    body={'course_subscription_id': COURSES['second']['id']},
    effect=effect('full_collaborator', [paid_away('p_other'), ['installment', 'i_other', None], paid_away('p_other'),
                                        ['installment', 'i_other', None]]))
add('malformed-course-server-error', 'owner', 'c1', 'current', 500, SERVER, body={'course_subscription_id': 'abc'})
add('athlete-not-allowed', 'athlete', 'c1', 'current', 403, {'msg': 'Not allowed.'})
add('owner-scoped-athlete-denied', 'owner', 'c1', 'current', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 'current', 403, {'detail': 'Missing permission: association.carnet.update'})
add('admin-without-session', 'admin', 'c1', 'current', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'c1', 'current', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'c1', 'current', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'c1', 'current', 200, {'name': 'Carnet Replace', 'description': 'Add a new carnet to the association',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
# The discarded carnet_assign responses: 404 carnet, 404 subscription.
add('foreign-carnet-ignored', 'owner', 'cf', 'current', 200, OK, effect=effect('owner'))
add('unknown-subscription-ignored', 'owner', 'c1', UNKNOWN, 200, OK, effect=effect('owner'))
add('with-course', 'owner', 'c1', 'current', 200, OK, body={'course_subscription_id': COURSES['active']['id']},
    effect=effect('owner', [paid_away('p_i1'), ['installment', 'i1', OWN_INDEX], paid_away('p_i1'), ['installment', 'i1', OWN_INDEX],
                            ['installment', 'i3', OWN_INDEX], paid_away('p_main')], created('c1', 'current', 'owner', 'active')))
add('repeat-with-course', 'owner', 'c1', 'current', 200, OK, body={'course_subscription_id': COURSES['active']['id']},
    effect=effect('owner', create=created('c1', 'current', 'owner', 'active')))
add('no-body', 'owner', 'c1', 'current', 200, OK, effect=effect('owner', create=created('c1', 'current', 'owner')))
add('foreign-course-ignored', 'owner', 'c1', 'current', 200, OK, body={'course_subscription_id': COURSES['abroad_cs']['id']},
    effect=effect('owner', create=created('c1', 'current', 'owner')))
add('deleted-course-ignored', 'owner', 'c2', 'other_open', 200, OK, body={'course_subscription_id': COURSES['deleted']['id']},
    effect=effect('owner', create=created('c2', 'other_open', 'owner')))
add('full-collaborator-form', 'full_collaborator', 'c1', 'current', 200, OK, raw_body='course_subscription_id=' + COURSES['second']['id'],
    content_type='application/x-www-form-urlencoded', effect=effect('full_collaborator', create=created('c1', 'current', 'full_collaborator', 'second')))
add('holding-link', 'owner', 'c1', 'current', 200, OK, body={'carnet_subscription_id': SOURCE['carnet_subscriptions']['held']['id'],
    'course_subscription_id': COURSES['second']['id']}, effect=effect('owner', link=['held', 'second']))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Carnet replace authored sequence with signal-driven deletes and assign effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
