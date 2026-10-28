"""Author the carnet-subscription/list HTTP sequence from carnet-list.md.

Builds on the carnet-list seed with lesson counters and a renewal
subscription sharing the associate. Athletes must name one of their own
subscriptions (403 'Not allowed.'); associations without subscription_id
fail (500, unbound variable). The subscription (of the association for
owners; 404 'Subscription not found.') selects the carnet subscriptions of
that subscription or of any subscription of the same associate, in the
same association, unordered. Each row reads the carnet, the live course
links, the payment (or the carnet fee) and meta['lessons_counter'] and
meta['lessons_left'] (a meta without them is a 500). Datetimes render as
UTC isoformat with microseconds when set and Z (Django confirmed). Run with ``python3 -I`` after the carnet-list
generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_subscription_list_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('carnet_list_http.json').read_text()))
IDS = SOURCE['ids']
OWN = IDS['own_association']
ids = iter(f'a1000000-0000-4000-8000-{990001 + n:012d}' for n in range(10))
SOURCE['subscriptions']['renewal'] = {'id': next(ids), 'association': OWN, 'user': 'athlete', 'associate': 'mine', 'start': '2100-01-01',
                                     'end': '2100-12-31', 'year': 'successivo'}
HOLDINGS = SOURCE['carnet_subscriptions']
HOLDINGS['held']['meta'] = {'lessons_counter': 10, 'lessons_left': 9, 'lessons_registry': ['x']}
HOLDINGS['someone_else']['meta'] = {'lessons_counter': 0, 'lessons_left': 0}
HOLDINGS['abroad']['meta'] = {'lessons_counter': 5, 'lessons_left': 'tre'}
HOLDINGS['renewed'] = {'id': next(ids), 'carnet': 'c2', 'user': 'athlete', 'subscription': 'renewal', 'courses': [], 'payment': None,
                       'disabled': True, 'meta': {'lessons_counter': 3, 'lessons_left': 3.5}, 'created': '2026-10-06T09:30:00Z'}
CARNETS, SUBSCRIPTIONS, COURSES = SOURCE['carnets'], SOURCE['subscriptions'], SOURCE['course_subscriptions']
UNKNOWN = 'a1000000-0000-4000-8000-000000999999'
SERVER = '<stable-server-error-page>'


def drf_moment(text):
    """The response encoder's UTC isoformat: microseconds when set, Z."""
    if text is None:
        return None
    head, _, tail = text.removesuffix('Z').partition('.')
    return head + ('.' + (tail + '000000')[:6] if tail else '') + 'Z'


def row(label):
    holding = HOLDINGS[label]
    carnet = CARNETS[holding['carnet']]
    payment = SOURCE['payments'][holding['payment']] if holding['payment'] else None
    live = [COURSES[c] for c in holding['courses'] if not COURSES[c]['deleted']]
    return {'carnet_subscription_id': holding['id'], 'carnet_id': carnet['id'], 'title': carnet['title'], 'creation_date': drf_moment(holding['created']),
            'subscription_id': SUBSCRIPTIONS[holding['subscription']]['id'], 'disabled': holding['disabled'],
            'payment': {'payment_id': payment['id'] if payment else None, 'amount': payment['amount'] if payment else carnet['fee'],
                        'creation_date': '2026-10-01T08:00:00Z' if payment else None},
            'course': [{'course_id': SOURCE['courses'][c['course']]['id'], 'course_title': SOURCE['courses'][c['course']]['title'],
                        'course_subscription_id': c['id']} for c in live],
            'lessons_counter': holding['meta']['lessons_counter'], 'lessons_left': holding['meta']['lessons_left']}


cases = []


def add(name, actor, subscription, status, response, *, method='GET', headers=None, query=None):
    path = '/carnet-subscription/list'
    if query is not None:
        path += '?' + query
    elif subscription is not None:
        path += '?subscription_id=' + (SUBSCRIPTIONS[subscription]['id'] if subscription in SUBSCRIPTIONS else subscription)
    cases.append({'name': f'carnet-subscription-list-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'ordered': False})


NOT_ALLOWED, NOT_FOUND = {'msg': 'Not allowed.'}, {'msg': 'Subscription not found.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
add('owner-current-and-same-associate', 'owner', 'current', 200, {'data': [row('held'), row('renewed')]})
add('owner-renewal-same-associate', 'owner', 'renewal', 200, {'data': [row('held'), row('renewed')]})
add('owner-without-payment', 'owner', 'other_open', 200, {'data': [row('someone_else')]})
add('full-collaborator-owner-scope', 'full_collaborator', 'current', 200, {'data': [row('held'), row('renewed')]})
add('owner-null-meta-server-error', 'owner', 'family_next', 500, SERVER)
add('owner-without-subscription-id-server-error', 'owner', None, 500, SERVER)
add('owner-malformed-server-error', 'owner', 'abc', 500, SERVER)
add('owner-foreign-not-found', 'owner', 'abroad_past', 404, NOT_FOUND)
add('owner-unknown-not-found', 'owner', UNKNOWN, 404, NOT_FOUND)
add('athlete-own', 'athlete', 'current', 200, {'data': [row('held'), row('renewed')]})
# Membership compares the canonical text: another spelling of the id is refused.
add('athlete-uppercase-not-allowed', 'athlete', None, 403, NOT_ALLOWED, query='subscription_id=' + SUBSCRIPTIONS['current']['id'].upper())
add('athlete-foreign-association', 'athlete', 'abroad_past', 200, {'data': [row('abroad')]})
add('athlete-other-holder-not-allowed', 'athlete', 'family_next', 403, NOT_ALLOWED)
add('athlete-without-subscription-id', 'athlete', None, 403, NOT_ALLOWED)
add('admin-impersonating-athlete', 'admin', 'current', 200, {'data': [row('held'), row('renewed')]},
    headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']})
add('owner-scoped-athlete', 'owner', 'current', 200, {'data': [row('held'), row('renewed')]}, headers=SCOPED)
add('owner-scoped-athlete-foreign-denied', 'owner', 'abroad_past', 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers=SCOPED)
add('restricted-missing-permission', 'restricted_collaborator', 'current', 403, {'detail': 'Missing permission: association.carnet.read'})
add('admin-without-session', 'admin', 'current', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'current', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'current', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 'current', 200, {'name': 'Carnet Subscription List', 'description': 'Set carnet subscription to disabled',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Carnet subscription list authored sequence', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
