"""Author the carnet/add HTTP sequence from carnet-writes.md.

Builds on the carnet-assign seed. Associations only. CarnetAddSerializer
validates title, description (CharFields), lessons_number (>= 1), fee
(DecimalField(9, 2), >= 0) and an optional meta that, when truthy, must be a
dict (400 {"msg": errors}). The carnet is created (audited with no
association), then saved with the owner's association (audited again).
Every request.data['subscriptions'] item of the association's subscriptions
runs carnet_assign on the same request (responses discarded); a missing key
or an item the UUID field rejects is a 500 after the carnet was saved, and a
text value is iterated by character. Run with ``python3 -I`` after the
carnet-assign generator.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_add_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('carnet_assign_http.json').read_text()))
IDS = SOURCE['ids']
OWN = IDS['own_association']
SUBSCRIPTIONS = SOURCE['subscriptions']
UNKNOWN = 'a1000000-0000-4000-8000-000000989998'
PATH = '/carnet/add'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, status, response, *, body=None, raw_body=None, content_type=None, method='POST', headers=None, effect=None):
    case = {'name': f'carnet-add-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def carnet(title, description, lessons, fee, meta, actor, holdings=()):
    return {'actor': actor, 'carnet': {'title': title, 'description': description, 'lessons': lessons, 'fee': fee, 'meta': meta},
            'holdings': [{'subscription': label, 'account': 'bank'} for label in holdings]}


BODY = {'title': 'Carnet nuovo è', 'description': 'Dieci ingressi', 'lessons_number': 10, 'fee': '25.5'}
OK = {'msg': 'Carnet added successfully.'}
REQUIRED = ['This field is required.']
add('athlete-not-allowed', 'athlete', 403, {'msg': 'Not allowed.'}, body={**BODY, 'subscriptions': []})
add('missing-fields', 'owner', 400, {'msg': {'title': REQUIRED, 'description': REQUIRED, 'lessons_number': REQUIRED, 'fee': REQUIRED}},
    body={'subscriptions': []})
add('invalid-values', 'owner', 400, {'msg': {
    'title': ['This field may not be blank.'], 'description': ['Ensure this field has no more than 1024 characters.'],
    'lessons_number': ['Ensure this value is greater than or equal to 1.'],
    'fee': ['Ensure this value is greater than or equal to 0.0.']}},
    body={'title': '', 'description': 'x' * 1025, 'lessons_number': 0, 'fee': '-1', 'subscriptions': []})
add('fee-too-many-digits', 'owner', 400, {'msg': {'fee': ['Ensure that there are no more than 9 digits in total.']}},
    body={**BODY, 'fee': '12345678.90', 'subscriptions': []})
add('meta-not-dict', 'owner', 400, {'msg': {'meta': ['This field must be a dictionary.']}}, body={**BODY, 'meta': [1], 'subscriptions': []})
add('meta-null', 'owner', 400, {'msg': {'meta': ['This field may not be null.']}}, body={**BODY, 'meta': None, 'subscriptions': []})
add('list-body', 'owner', 400, {'msg': {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}}, body=[])
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={**BODY, 'subscriptions': []}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.carnet.create'},
    body={**BODY, 'subscriptions': []})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Carnet Add', 'description': 'Add a new carnet to the association', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('owner-without-subscriptions', 'owner', 200, OK, body={**BODY, 'subscriptions': []},
    effect=carnet('Carnet nuovo è', 'Dieci ingressi', 10, '25.50', None, 'owner'))
# Foreign and unknown subscriptions are skipped; each other one is assigned.
add('owner-assigns-subscriptions', 'owner', 200, OK,
    body={**BODY, 'title': 'Con iscrizioni', 'subscriptions': [SUBSCRIPTIONS['current']['id'], SUBSCRIPTIONS['abroad_past']['id'], UNKNOWN,
                                                                 SUBSCRIPTIONS['other_open']['id']]},
    effect=carnet('Con iscrizioni', 'Dieci ingressi', 10, '25.50', None, 'owner', ['current', 'other_open']))
add('full-collaborator-meta', 'full_collaborator', 200, OK,
    body={**BODY, 'title': 'Collaboratore', 'fee': 7, 'meta': {'b': [1, 2.5], 'a': None}, 'subscriptions': [SUBSCRIPTIONS['family_next']['id']]},
    effect=carnet('Collaboratore', 'Dieci ingressi', 10, '7.00', {'b': [1, 2.5], 'a': None}, 'full_collaborator', ['family_next']))
add('falsy-list-meta-kept', 'owner', 200, OK, body={**BODY, 'title': 'Meta vuoto', 'meta': [], 'subscriptions': []},
    effect=carnet('Meta vuoto', 'Dieci ingressi', 10, '25.50', [], 'owner'))
add('form-empty-subscriptions', 'owner', 200, OK, raw_body='title=Modulo&description=Da+form&lessons_number=3&fee=1.5&subscriptions=',
    content_type='application/x-www-form-urlencoded', effect=carnet('Modulo', 'Da form', 3, '1.50', None, 'owner'))
add('missing-subscriptions-server-error', 'owner', 500, SERVER, body={**BODY, 'title': 'Senza chiave'},
    effect=carnet('Senza chiave', 'Dieci ingressi', 10, '25.50', None, 'owner'))
add('text-subscriptions-server-error', 'owner', 500, SERVER, body={**BODY, 'title': 'Testo', 'subscriptions': 'abc'},
    effect=carnet('Testo', 'Dieci ingressi', 10, '25.50', None, 'owner'))
add('form-subscription-characters-server-error', 'owner', 500, SERVER,
    raw_body='title=Caratteri&description=Da+form&lessons_number=3&fee=1.5&subscriptions=' + SUBSCRIPTIONS['current']['id'],
    content_type='application/x-www-form-urlencoded', effect=carnet('Caratteri', 'Da form', 3, '1.50', None, 'owner'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Carnet add authored sequence with carnet, assignment and audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
