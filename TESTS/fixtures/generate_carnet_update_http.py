"""Author the carnet/<uid>/update HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed. The fixed view (PR #28) refuses
non-association roles, validates CarnetUpdateSerializer (DRF 3.17: CharField
title max 255 and description max 1024 without blanks, IntegerField
lessons_number >= 1, DecimalField fee max_digits 9 / 2 places >= 0.0,
BooleanField public, all required) and answers 400 {"msg": errors} before the
lookup; then Carnet.get(carnet_id=uid, sport_association__user=request.user)
(404; a non-UUID is a 500) and a full-row save audited with the changed fields
only (the validated fee is quantized) and indexed directly. JSON numbers reach
DRF as Python values: 1.000 is the float 1.0. Run with ``python3 -I`` after the
carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_update_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, CARNETS = SOURCE['ids'], SOURCE['carnets']
path = lambda key: f'/carnet/{CARNETS[key]["id"] if key in CARNETS else key}/update'
SERVER = '<stable-server-error-page>'
REQUIRED = ['This field is required.']
cases = []


def add(name, actor, target, status, response, *, body=None, raw_body=None, content_type=None, method='PATCH', headers=None, effect=None):
    case = {'name': f'carnet-update-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


valid = lambda **values: {'title': 'Nuovo è', 'description': 'Dieci ingressi', 'lessons_number': 20, 'fee': 75, 'public': False, **values}
invalid = lambda **errors: {'msg': errors}
OK = {'msg': 'Carnet updated successfully.'}


def updated(label, values, changes, actor='owner'):
    return {'carnet': label, 'values': values, 'changes': changes, 'actor': actor}


add('missing-fields', 'owner', 'c1', 400, invalid(title=REQUIRED, description=REQUIRED, lessons_number=REQUIRED, fee=REQUIRED, public=REQUIRED), body={})
add('list-body', 'owner', 'c1', 400, invalid(non_field_errors=['Invalid data. Expected a dictionary, but got list.']), body=[])
add('blank-and-null', 'owner', 'c1', 400, invalid(title=['This field may not be blank.'], description=['This field may not be null.'],
    public=['This field may not be null.']), body=valid(title='   ', description=None, public=None))
add('too-long', 'owner', 'c1', 400, invalid(title=['Ensure this field has no more than 255 characters.'],
    description=['Ensure this field has no more than 1024 characters.']), body=valid(title='x' * 256, description='y' * 1025))
add('lessons-below-minimum', 'owner', 'c1', 400, invalid(lessons_number=['Ensure this value is greater than or equal to 1.']), body=valid(lessons_number=0))
add('lessons-fraction', 'owner', 'c1', 400, invalid(lessons_number=['A valid integer is required.']), body=valid(lessons_number=1.5))
add('lessons-boolean', 'owner', 'c1', 400, invalid(lessons_number=['A valid integer is required.']), body=valid(lessons_number=True))
add('fee-negative', 'owner', 'c1', 400, invalid(fee=['Ensure this value is greater than or equal to 0.0.']), body=valid(fee=-1))
add('fee-places', 'owner', 'c1', 400, invalid(fee=['Ensure that there are no more than 2 decimal places.']), body=valid(fee=1.234))
add('fee-whole-digits', 'owner', 'c1', 400, invalid(fee=['Ensure that there are no more than 7 digits before the decimal point.']),
    body=valid(fee=12345678))
add('fee-total-digits', 'owner', 'c1', 400, invalid(fee=['Ensure that there are no more than 9 digits in total.']), body=valid(fee='1234567.891'))
add('fee-text', 'owner', 'c1', 400, invalid(fee=['A valid number is required.']), body=valid(fee='abc'))
add('public-text', 'owner', 'c1', 400, invalid(public=['Must be a valid boolean.']), body=valid(public='maybe'))
add('invalid-before-lookup-on-malformed-id', 'owner', 'abc', 400, invalid(title=REQUIRED), body={k: v for k, v in valid().items() if k != 'title'})
add('foreign-not-found', 'owner', 'cf', 404, {'msg': 'Carnet not found.'}, body=valid())
add('missing-not-found', 'owner', IDS['missing'], 404, {'msg': 'Carnet not found.'}, body=valid())
add('malformed-server-error', 'owner', 'abc', 500, SERVER, body=valid())
add('athlete-not-allowed', 'athlete', 'c1', 403, {'msg': 'Not allowed.'}, body=valid())
add('owner-scoped-athlete-denied', 'owner', 'c1', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body=valid(),
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 403, {'detail': 'Missing permission: association.carnet.update'}, body=valid())
add('admin-without-session', 'admin', 'c1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body=valid())
add('anonymous', 'anonymous', 'c1', 401, {'detail': 'Authentication credentials were not provided.'}, body=valid())
add('wrong-method', 'owner', 'c1', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'c1', 200, {'name': 'Carnet Update', 'description': 'Update a carnet', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
c1 = CARNETS['c1']
add('owner-updates', 'owner', 'c1', 200, OK, body=valid(title='  Nuovo è  ', lessons_number='20.0', fee=1.000),
    effect=updated('c1', {'title': 'Nuovo è', 'description': 'Dieci ingressi', 'lessons_number': 20, 'fee': '1.00', 'public': False},
                   {'title': [c1['title'], 'Nuovo è'], 'description': [c1['description'], 'Dieci ingressi'], 'lessons_number': ['10', '20'],
                    'fee': ['50.00', '1.00'], 'public': ['True', 'False']}))
add('same-values-no-audit', 'owner', 'c1', 200, OK, body=valid(fee='1'),
    effect=updated('c1', {'title': 'Nuovo è', 'description': 'Dieci ingressi', 'lessons_number': 20, 'fee': '1.00', 'public': False}, None))
c2 = CARNETS['c2']
add('full-collaborator-form-update', 'full_collaborator', 'c2', 200, OK,
    raw_body='title=Form&description=Da+form&lessons_number=3&fee=12.5&public=true', content_type='application/x-www-form-urlencoded',
    effect=updated('c2', {'title': 'Form', 'description': 'Da form', 'lessons_number': 3, 'fee': '12.50', 'public': True},
                   {'title': ['None', 'Form'], 'description': ['None', 'Da form'], 'lessons_number': ['0', '3'], 'fee': ['0.00', '12.50'],
                    'public': ['False', 'True']}, actor='full_collaborator'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Carnet update authored sequence with audit effects',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
