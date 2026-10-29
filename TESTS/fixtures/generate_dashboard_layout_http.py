"""Author the statistic/dashboard/layout HTTP sequence from dashboard-layout.md.

Effects name the user whose ``dashboard_layout`` changes, its stored value
and, when auditlog sees a change, the exact update entry: JSON rendered as
json.dumps(sort_keys=True) with ASCII escaping, None as ``null``, the stored
value re-read from jsonb (numeric keeps its scale) on the next write. Index paths follow the User
resolver (user.sport_association) and its actor fallback. Order matters.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('dashboard_layout_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
PATH = '/statistic/dashboard/layout'
OK = {'data': 'dashboard layout updated'}
SERVER = '<stable-server-error-page>'
dumps = lambda value: json.dumps(value, sort_keys=True)
cases = []


def add(name, actor, status, response, *, body=None, raw_body=None, content_type=None, method='POST', headers=None, effect=None):
    case = {'name': f'dashboard-layout-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def changed(user, value, old, new, actor, *, index=None, impersonation=None):
    """index: association label and path, or None when nothing resolves."""
    return {'user': user, 'value': value, 'audit': {'changes': {'dashboard_layout': [old, new]}, 'actor': actor,
            'impersonation': impersonation, 'index': index}}


OWN_INDEX = {'association': 'own_association', 'path': 'user.sport_association'}
LAYOUT = {'widgets': [{'id': 'iscritti', 'x': 1.50, 'w': [1, None, True]}], 'é': 'ü 😀', 'big': 10 ** 25}
add('owner-sets', 'owner', 200, OK, body={'dashboard_layout': LAYOUT},
    effect=changed('owner', LAYOUT, 'null', dumps(LAYOUT), 'owner', index=OWN_INDEX))
reordered = {'big': 10 ** 25, 'é': 'ü 😀', 'widgets': [{'w': [1, None, True], 'x': 1.5, 'id': 'iscritti'}]}
# Same value in another key order: no audit entry and no stored change.
add('owner-reordered-same', 'owner', 200, OK, body={'dashboard_layout': reordered}, effect={'user': 'owner', 'value': LAYOUT, 'audit': None})
# Python sends 1e5 as 100000.0; jsonb numeric keeps that scale, so the stored
# value reads back as the same float and an identical write is not audited.
add('owner-float-exponent', 'owner', 200, OK, body={'dashboard_layout': {'e': 1e5}},
    effect=changed('owner', {'e': 100000.0}, dumps(LAYOUT), '{"e": 100000.0}', 'owner', index=OWN_INDEX))
add('owner-float-exponent-again', 'owner', 200, OK, body={'dashboard_layout': {'e': 1e5}},
    effect={'user': 'owner', 'value': {'e': 100000.0}, 'audit': None})
add('owner-explicit-null', 'owner', 200, OK, body={'dashboard_layout': None},
    effect=changed('owner', None, '{"e": 100000.0}', 'null', 'owner', index=OWN_INDEX))
add('owner-missing-key-keeps-null', 'owner', 200, OK, body={'altro': 1}, effect={'user': 'owner', 'value': None, 'audit': None})
add('owner-form-string', 'owner', 200, OK, raw_body='dashboard_layout=a&dashboard_layout=b%C3%A8', content_type='application/x-www-form-urlencoded',
    effect=changed('owner', 'bè', 'null', '"b\\u00e8"', 'owner', index=OWN_INDEX))
for label, body in (('list-body', [1]), ('string-body', 'dashboard_layout'), ('number-body', 3)):
    add(f'{label}-server-error', 'owner', 500, SERVER, body=body)
add('null-body-server-error', 'owner', 500, SERVER, raw_body='null', content_type='application/json')
# auditlog logs updates on pre_save: the entry commits (no request
# transaction) before PostgreSQL rejects the jsonb, so the layout is unchanged.
add('lone-surrogate-server-error', 'owner', 500, SERVER, raw_body='{"dashboard_layout": {"t": "\\ud800"}}', content_type='application/json',
    effect=changed('owner', 'bè', '"b\\u00e8"', '{"t": "\\ud800"}', 'owner', index=OWN_INDEX))
# Collaborators write their own layout; neither the collaborator nor the
# acting collaborator has an association, so no index row is created.
add('full-collaborator-own-layout', 'full_collaborator', 200, OK, body={'dashboard_layout': ['c']},
    effect=changed('full_collaborator', ['c'], 'null', '["c"]', 'full_collaborator'))
add('admin-impersonating-owner', 'admin', 200, OK, body={'dashboard_layout': {'da': 'admin'}},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    effect=changed('owner', {'da': 'admin'}, '"b\\u00e8"', '{"da": "admin"}', 'admin', index=OWN_INDEX, impersonation='admin_owner'))
add('restricted-collaborator-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.dashboard.read'},
    body={'dashboard_layout': 1})
add('athlete-server-error', 'athlete', 500, SERVER, body={'dashboard_layout': 1})
add('deleted-association-server-error', 'deleted_association_owner', 500, SERVER, body={'dashboard_layout': 1})
add('owner-without-association-server-error', 'owner_without_association', 500, SERVER, body={'dashboard_layout': 1})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'dashboard_layout': 1})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body={'dashboard_layout': 1})
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Statistic Dashboard Layout', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Dashboard layout authored sequence with User audit effects',
                              'ids': IDS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
