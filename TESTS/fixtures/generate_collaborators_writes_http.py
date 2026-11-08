"""Author the collaborators/<str:uid>/update HTTP sequence (collaborators-writes.md).

collaborators_update is an @api_view(['PATCH']) function view in
collaborator_views.py with IsAuthenticated and IsAssociationUser (athletes:
DRF's generic 403, before method dispatch). In Django's order:

1. ``data = request.data`` parses the body (malformed JSON: DRF's 400);
2. is_valid_uuid(uid): 400 ["badly formed hexadecimal UUID string"];
3. User.objects (live users) .filter(connected_user=request.user,
   user_id=uid).first(); request.user is the owner for collaborators. None is
   ValidationError('Collaborator not found'): 400 ["Collaborator not found"];
4. int(data.get('collaborator_role', User.COLLABORATOR)): User.COLLABORATOR
   is 3, the custom role. A non-dict body (no .get) or a value int() rejects
   is a 500 before any write. A role outside 1, 2, 3 becomes 1 (FULL);
5. collaborator_permissions = data.get(...) for the custom role, else None;
6. both fields assigned and save(): auditlog's pre_save entry diffs the
   stored row (str() of the role, json.dumps(sort_keys=True) of the JSON, None
   as 'null'); no entry when nothing changed. The entry is committed before a
   save PostgreSQL rejects (no request transaction). The index follows the
   User resolver (the target's live association), then the actor's.
   Response: 200 {'msg': 'collaborator updated.'}.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('collaborators_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
ids = iter(f'a1000000-0000-4000-8000-{796001 + n:012d}' for n in range(50))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000796999'
OK = {'msg': 'collaborator updated.'}
NOT_FOUND = ['Collaborator not found']
dumps = lambda value: json.dumps(value, sort_keys=True)
cases = []


def user(first, *, role=3, connected='owner', collaborator_role=1, permissions=None, deleted=False):
    return {'id': next(ids), 'first': first, 'role': role, 'connected': connected, 'collaborator_role': collaborator_role,
            'permissions': permissions, 'deleted': deleted}


USERS = {
    'target': user('Bersaglio'),
    'second': user('Secondo', collaborator_role=3, permissions=['association.calendar.read']),
    # An association-role user connected to the owner that owns its own association.
    'owning': user('Titolare', role=1),
    'foreign': user('Estero', connected='foreign_owner'),
    'deleted': user('Cancellato', deleted=True),
    'loose': user('Sciolto', connected=None),
}
OWNING_ASSOCIATION = next(ids)
state = {label: {'role': row['collaborator_role'], 'permissions': row['permissions']} for label, row in USERS.items()}
path = lambda value: f'/collaborators/{value}/update'
OWN_INDEX = {'association': 'own_association', 'path': 'actor.sport_association'}


def add(name, actor, target, status, response, *, method='PATCH', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'collaborators-writes-{name}', 'actor': actor, 'method': method,
            'path': path(USERS[target]['id'] if target in USERS else target), 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def update(label, role, permissions, actor, *, index=OWN_INDEX, impersonation=None, saved=True):
    """The effect of one save: the stored values and the audit entry, if any."""
    old = state[label]
    changes = {}
    if str(old['role']) != str(role):
        changes['collaborator_role'] = [str(old['role']), str(role)]
    if dumps(old['permissions']) != dumps(permissions):
        changes['collaborator_permissions'] = [dumps(old['permissions']), dumps(permissions)]
    if saved:
        state[label] = {'role': role, 'permissions': permissions}
    audit = None
    if changes:
        audit = {'changes': changes, 'actor': actor, 'impersonation': impersonation, 'index': index}
    return {'user': label, 'role': state[label]['role'], 'permissions': state[label]['permissions'], 'audit': audit}


# Authorization runs before the view.
add('anonymous', 'anonymous', 'target', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('admin-without-session', 'admin', 'target', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('restricted-collaborator', 'restricted_collaborator', 'target', 403, {'detail': 'Missing permission: other.users.collaborators.update'},
    body={'collaborator_role': 2})
add('restricted-collaborator-wrong-method', 'restricted_collaborator', 'target', 403,
    {'detail': 'Missing permission: other.users.collaborators.update'}, method='POST', body={})
add('athlete', 'athlete', 'target', 403, {'detail': 'You do not have permission to perform this action.'}, body={'collaborator_role': 2})
add('athlete-wrong-method', 'athlete', 'target', 403, {'detail': 'You do not have permission to perform this action.'}, method='POST', body={})
add('owner-scoped-to-athlete', 'owner', 'target', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}, body={'collaborator_role': 2})
add('wrong-method', 'owner', 'target', 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('options', 'owner', 'target', 200, {'name': 'Collaborators Update', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# request.data is parsed before the uid is validated.
add('malformed-json-before-uuid', 'owner', 'not-a-uuid', 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json')
add('invalid-uuid', 'owner', 'not-a-uuid', 400, ['badly formed hexadecimal UUID string'], body={'collaborator_role': 2})
for label, target in (('unknown', UNKNOWN), ('foreign', 'foreign'), ('deleted', 'deleted'), ('unconnected', 'loose'),
                      ('owner-self', IDS['owner']), ('collaborator-of-other-scope', IDS['restricted_collaborator'])):
    actor = 'foreign_owner' if label == 'collaborator-of-other-scope' else 'owner'
    add(f'{label}-not-found', actor, target, 400, NOT_FOUND, body={'collaborator_role': 2})
add('owner-without-association-not-found', 'owner_without_association', 'target', 400, NOT_FOUND, body={'collaborator_role': 2})
# The lookup runs before data.get: a list body only raises on a match.
add('list-body-not-found', 'owner', UNKNOWN, 400, NOT_FOUND, body=[1])
add('list-body-server-error', 'owner', 'target', 500, SERVER, body=[1])
add('string-body-server-error', 'owner', 'target', 500, SERVER, body='collaborator_role')
for label, value in (('letters', 'abc'), ('null', None), ('list', []), ('dict', {}), ('decimal-string', '2.0'), ('empty-string', '')):
    add(f'role-{label}-server-error', 'owner', 'target', 500, SERVER, body={'collaborator_role': value, 'collaborator_permissions': ['x']})

# Successes, in order: each effect is computed against the running state.
add('owner-accounting-drops-permissions', 'owner', 'target', 200, OK, body={'collaborator_role': 2, 'collaborator_permissions': ['x']},
    effect=update('target', 2, None, 'owner'))
add('owner-default-custom-role', 'owner', 'target', 200, OK, body={'collaborator_permissions': ['b', 'a']},
    effect=update('target', 3, ['b', 'a'], 'owner'))
add('owner-same-values-not-audited', 'owner', 'target', 200, OK, body={'collaborator_role': 3, 'collaborator_permissions': ['b', 'a']},
    effect=update('target', 3, ['b', 'a'], 'owner'))
# int(' 3 ') is 3; json.dumps sorts keys (é after z), 1e5 is the float 100000.0.
add('owner-string-role-dict-permissions', 'owner', 'target', 200, OK,
    body={'collaborator_role': ' 3 ', 'collaborator_permissions': {'z': 1, 'é': 1e5, 'n': None, 'big': 10 ** 25}},
    effect=update('target', 3, {'z': 1, 'é': 100000.0, 'n': None, 'big': 10 ** 25}, 'owner'))
add('owner-out-of-range-role-is-full', 'owner', 'target', 200, OK, body={'collaborator_role': 99, 'collaborator_permissions': ['q']},
    effect=update('target', 1, None, 'owner'))
add('owner-boolean-role-unchanged', 'owner', 'target', 200, OK, body={'collaborator_role': True, 'collaborator_permissions': ['q']},
    effect=update('target', 1, None, 'owner'))
add('owner-float-role-truncated', 'owner', 'target', 200, OK, body={'collaborator_role': 2.9}, effect=update('target', 2, None, 'owner'))
add('owner-negative-role-is-full', 'owner', 'target', 200, OK, body={'collaborator_role': '-3'}, effect=update('target', 1, None, 'owner'))
add('owner-underscore-role', 'owner', 'target', 200, OK, body={'collaborator_role': '0_2'}, effect=update('target', 2, None, 'owner'))
# QueryDict.get returns the last value of a repeated form key.
add('owner-form-body', 'owner', 'target', 200, OK, raw_body='collaborator_role=1&collaborator_role=3&collaborator_permissions=a&collaborator_permissions=b%C3%A8',
    content_type='application/x-www-form-urlencoded', effect=update('target', 3, 'bè', 'owner'))
add('owner-explicit-null-permissions', 'owner', 'target', 200, OK, body={'collaborator_role': 3, 'collaborator_permissions': None},
    effect=update('target', 3, None, 'owner'))
add('owner-empty-body-default-role', 'owner', 'target', 200, OK, body={}, effect=update('target', 3, None, 'owner'))
add('owner-nested-permissions', 'owner', 'target', 200, OK, body={'collaborator_permissions': [{'k': [1, 2.5, True]}, 'ü 😀']},
    effect=update('target', 3, [{'k': [1, 2.5, True]}, 'ü 😀'], 'owner'))
# auditlog's pre_save entry commits before PostgreSQL rejects \u0000 in jsonb.
add('nul-permissions-server-error', 'owner', 'target', 500, SERVER,
    raw_body='{"collaborator_role": 3, "collaborator_permissions": ["\\u0000"]}', content_type='application/json',
    effect=update('target', 3, ['\x00'], 'owner', saved=False))
# Collaborators act for the owner; the acting collaborator has no association.
add('full-collaborator-updates', 'full_collaborator', 'second', 200, OK, body={'collaborator_role': 2},
    effect=update('second', 2, None, 'full_collaborator', index=None))
add('admin-impersonating-owner', 'admin', 'second', 200, OK, body={'collaborator_role': 3, 'collaborator_permissions': {'k': 'v'}},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    effect=update('second', 3, {'k': 'v'}, 'admin', index={'association': 'admin_association', 'path': 'actor.sport_association'},
                  impersonation='admin_owner'))
# The User resolver finds the target's own live association first.
add('owner-updates-association-user', 'owner', 'owning', 200, OK, body={'collaborator_role': 2},
    effect=update('owning', 2, None, 'owner', index={'association': 'owning_association', 'path': 'user.sport_association'}))
add('group-header-ignored', 'owner', 'owning', 200, OK, body={'collaborator_role': 1}, headers={'X-Group-Id': IDS['foreign_group']},
    effect=update('owning', 1, None, 'owner', index={'association': 'owning_association', 'path': 'user.sport_association'}))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Collaborator role and permission update authored sequence with User audit effects',
                              'ids': {**IDS, 'owning_association': OWNING_ASSOCIATION}, 'users': USERS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
