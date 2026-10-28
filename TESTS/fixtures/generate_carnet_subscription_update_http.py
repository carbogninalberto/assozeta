"""Author the carnet-subscription/<uid>/update HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed with lesson counters. The view refuses
non-association roles, requires a lessons_left key in request.data (a list
body has no keys(): 500), validates the uid (400), gets the carnet
subscription within the scope user's subscriptions (404), then compares
int(lessons_left) with meta['lessons_counter'] (Python int(): strings,
truncated floats and booleans; anything else, a missing counter or a meta
that is not a dict is a 500) and saves meta['lessons_left']. auditlog records
the JSON meta when it changes, indexed through the subscription. Run with
``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_subscription_update_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS = SOURCE['ids']
HOLDINGS = {k: dict(v) for k, v in SOURCE['carnet_subscriptions'].items()}
HOLDINGS['held']['meta'] = {'lessons_counter': 10, 'lessons_left': 9, 'lessons_registry': ['x']}
HOLDINGS['someone_else']['meta'] = {'lessons_counter': 'dieci'}
path = lambda key: f'/carnet-subscription/{HOLDINGS[key]["id"] if key in HOLDINGS else key}/update'
dumps = lambda value: json.dumps(value, sort_keys=True)
SERVER = '<stable-server-error-page>'
cases = []
state = {'held': dict(HOLDINGS['held']['meta'])}


def add(name, actor, target, status, response, *, body=None, raw_body=None, content_type=None, method='PATCH', headers=None, effect=None):
    case = {'name': f'carnet-subscription-update-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def saved(left, actor='owner'):
    old = dict(state['held'])
    new = {**old, 'lessons_left': left}
    state['held'] = new
    return {'holding': 'held', 'meta': new, 'changes': {'meta': [dumps(old), dumps(new)]} if old != new else None, 'actor': actor}


RANGE = {'msg': 'Lessons left cannot be > than lessons_number.'}
add('missing-lessons-left', 'owner', 'held', 400, {'msg': 'Missing lessons left.'}, body={'other': 1})
add('malformed-id', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'], body={'lessons_left': 1})
add('list-body-server-error', 'owner', 'held', 500, SERVER, body=[])
add('foreign-not-found', 'owner', 'abroad', 404, {'msg': 'Carnet subscription not found.'}, body={'lessons_left': 1})
add('without-subscription-not-found', 'owner', 'detached', 404, {'msg': 'Carnet subscription not found.'}, body={'lessons_left': 1})
add('above-counter', 'owner', 'held', 400, RANGE, body={'lessons_left': 11})
add('negative', 'owner', 'held', 400, RANGE, body={'lessons_left': -1})
add('text-server-error', 'owner', 'held', 500, SERVER, body={'lessons_left': 'abc'})
add('null-server-error', 'owner', 'held', 500, SERVER, body={'lessons_left': None})
add('null-meta-server-error', 'owner', 'family', 500, SERVER, body={'lessons_left': 1})
add('text-counter-server-error', 'owner', 'someone_else', 500, SERVER, body={'lessons_left': 1})
add('athlete-not-allowed', 'athlete', 'held', 403, {'msg': 'Not allowed.'}, body={'lessons_left': 1})
add('owner-scoped-athlete-denied', 'owner', 'held', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={'lessons_left': 1}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'held', 403, {'detail': 'Missing permission: association.carnet.update'},
    body={'lessons_left': 1})
add('admin-without-session', 'admin', 'held', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('anonymous', 'anonymous', 'held', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('wrong-method', 'owner', 'held', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'held', 200, {'name': 'Carnet Subscription Update', 'description': 'Set carnet subscription to enabled',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
OK = {'msg': 'Carnet subscription updated.'}
add('owner-text-value', 'owner', 'held', 200, OK, body={'lessons_left': ' 5 '}, effect=saved(5))
add('truncated-float', 'owner', 'held', 200, OK, body={'lessons_left': 7.9}, effect=saved(7))
add('same-value-no-audit', 'owner', 'held', 200, OK, body={'lessons_left': 7}, effect=saved(7))
add('boolean-is-one', 'owner', 'held', 200, OK, body={'lessons_left': True}, effect=saved(1))
add('full-collaborator-form', 'full_collaborator', 'held', 200, OK, raw_body='lessons_left=10', content_type='application/x-www-form-urlencoded',
    effect=saved(10, 'full_collaborator'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'carnet_subscriptions': HOLDINGS,
                              'scope': 'Carnet subscription update authored sequence with audit effects', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
