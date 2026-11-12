"""Author the subscription/tags list, add, update, delete, assign and unassign HTTP sequence (subscription-tags.md).

The views read ``request.data.get('tag_name')`` (a non-dict body is a 500)
and then ``request.user.sport_association`` (athletes: PermissionDenied 'User
is not a sport association'; an owner without an association: 500) before
TagService validates. TagService raises DRF's ValidationError, which the
views render with ``str(e)`` (the repr of the detail list): add answers 400
``{'error': ...}``; update answers 400 ``{'msg': ...}`` for a missing, null
or whitespace-only (str.strip()) name and 404 for an unknown tag; delete,
assign and unassign answer 404 ``{'msg': ...}``. A malformed tag or
subscription id is the ORM's 500. list orders the association's tags by
name. add stores the CharField's str() of any other JSON value and echoes
the value as sent.

assign/unassign need the association's tag and then its subscription from
the group-aware SubscriptionManager (deleted subscriptions included). They
change the M2M link only when needed and then call Subscription.save(),
which fills missing start/end dates from the owner's season settings
(auditlog records them) and always computes the season range: a creation
year of 1 raises after the link was stored (requests are not atomic).
auditlog records the tag create, rename and delete. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_tags_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{561001 + n:012d}' for n in range(20))
TAGS = {'zeta': {'id': next(ids), 'name': 'Zeta', 'association': OWN}, 'alpha': {'id': next(ids), 'name': 'Alpha è', 'association': OWN},
        'lower': {'id': next(ids), 'name': 'bimbi', 'association': OWN}, 'abroad': {'id': next(ids), 'name': 'Estero', 'association': FOREIGN}}


def subscription(association, *, start='2026-01-01', end='2026-12-31', created='2026-02-01T09:00:00Z', deleted=False, group=None, tags=()):
    return {'id': next(ids), 'association': association, 'start': start, 'end': end, 'created': created, 'deleted': deleted,
            'group': group, 'tags': list(tags)}


SUBSCRIPTIONS = {
    'dated': subscription(OWN, tags=['zeta']),
    'undated': subscription(OWN, start=None, end=None, created='2026-03-15T10:00:00Z'),
    'pending': subscription(OWN, start=None, end=None, created='2025-11-20T23:30:00Z', tags=['zeta']),
    'removed': subscription(OWN, deleted=True),
    'grouped': subscription(OWN, group='own_group'),
    'ancient': subscription(OWN, created='0001-06-01T12:00:00Z'),
    'abroad': subscription(FOREIGN),
}
UNKNOWN = 'a1000000-0000-4000-8000-000000569998'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'subscription-tags-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ref = lambda table, key: table[key]['id'] if key in table else key
LIST, ADD = '/subscription/tags/list', '/subscription/tags/add'
tag = lambda label, verb: f'/subscription/tags/{ref(TAGS, label)}/{verb}'
link = lambda label, verb, sub: f'/subscription/tags/{ref(TAGS, label)}/{verb}/{ref(SUBSCRIPTIONS, sub)}'
# str() of rest_framework.exceptions.ValidationError('...') is the repr of its detail list.
detail = lambda text: f"[ErrorDetail(string='{text}', code='invalid')]"
REQUIRED = detail('tag_name is required')
NOT_TAG, NOT_SUB = {'msg': detail('tag not found')}, {'msg': detail('subscription not found')}
ATHLETE = {'detail': 'User is not a sport association'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
MEMBERS_READ, MEMBERS_UPDATE = ({'detail': f'Missing permission: association.members.{k}'} for k in ('read', 'update'))
options = lambda name, description: {'name': name, 'description': description, 'renders': ['application/json'],
                                     'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
DOC_NAME = ':param request: tag_name\n:return: list of tags'
DOC_LINK = 'API endpoint to update a tag for the subscriptions\n:param request: tag_id, subscription_id\n:return: list of tags'
listed = lambda *labels: {'tags': [{'tag_id': TAGS[l]['id'], 'tag_name': TAGS[l]['name']} for l in labels]}
link_effect = lambda actor, add=(), remove=(), dates=None: {'kind': 'link', 'actor': actor, 'add': [list(p) for p in add],
                                                            'remove': [list(p) for p in remove], 'dates': dates or {}}

# list
OWN_TAGS = listed('alpha', 'zeta', 'lower')  # C collation: uppercase sorts before lowercase.
add('list-owner', 'owner', LIST, 200, OWN_TAGS, method='GET')
add('list-full-collaborator-owner-scope', 'full_collaborator', LIST, 200, OWN_TAGS, method='GET')
add('list-admin-impersonating-owner', 'admin', LIST, 200, OWN_TAGS, method='GET', headers=ADMIN_OWNER)
add('list-foreign-owner', 'foreign_owner', LIST, 200, listed('abroad'), method='GET')
add('list-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='GET')
add('list-athlete-denied', 'athlete', LIST, 403, ATHLETE, method='GET')
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_DENIED, method='GET', headers=SCOPED)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST, 403, MEMBERS_READ, method='GET')
add('list-admin-without-session', 'admin', LIST, 403, ADMIN, method='GET')
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS, method='GET')
add('list-wrong-method', 'owner', LIST, 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('list-options', 'owner', LIST, 200,
    options('Subscription Tags List', 'API endpoint to get the list of tags for the subscriptions\n:param request: None\n:return: list of tags'),
    method='OPTIONS')

# add
add('add-missing-name', 'owner', ADD, 400, {'error': REQUIRED}, body={})
add('add-null-name', 'owner', ADD, 400, {'error': REQUIRED}, body={'tag_name': None})
add('add-blank-name', 'owner', ADD, 400, {'error': REQUIRED}, body={'tag_name': ' \t \x1c'})
add('add-form-empty-name', 'owner', ADD, 400, {'error': REQUIRED}, raw_body='tag_name=', content_type='application/x-www-form-urlencoded')
add('add-athlete-before-validation', 'athlete', ADD, 403, ATHLETE, body={})
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[])
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, body={'tag_name': 'X'})
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body={'tag_name': 'X'}, headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, MEMBERS_UPDATE, body={'tag_name': 'X'})
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body={})
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body={})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, options('Subscription Tags Add', 'API endpoint to add a new tag for the subscriptions\n' + DOC_NAME),
    method='OPTIONS')
add('add-owner', 'owner', ADD, 200, {'tag': {'tag_id': '$new', 'tag_name': 'Nuovo è'}}, body={'tag_name': 'Nuovo è'},
    effect={'kind': 'add', 'actor': 'owner', 'stored': 'Nuovo è'})
add('add-padded-name-kept', 'owner', ADD, 200, {'tag': {'tag_id': '$new', 'tag_name': '  spazi '}}, body={'tag_name': '  spazi '},
    effect={'kind': 'add', 'actor': 'owner', 'stored': '  spazi '})
add('add-false-name', 'full_collaborator', ADD, 200, {'tag': {'tag_id': '$new', 'tag_name': False}}, body={'tag_name': False},
    effect={'kind': 'add', 'actor': 'full_collaborator', 'stored': 'False'})
add('add-form', 'owner', ADD, 200, {'tag': {'tag_id': '$new', 'tag_name': 'Modulo'}}, raw_body='tag_name=Modulo',
    content_type='application/x-www-form-urlencoded', effect={'kind': 'add', 'actor': 'owner', 'stored': 'Modulo'})

# update
UPDATE_DOC = 'API endpoint to update a tag for the subscriptions\n' + DOC_NAME
add('update-missing-name', 'owner', tag('alpha', 'update'), 400, {'msg': REQUIRED}, method='PATCH', body={})
add('update-blank-name', 'owner', tag('alpha', 'update'), 400, {'msg': REQUIRED}, method='PATCH', body={'tag_name': '\n '})
add('update-malformed-missing-name', 'owner', tag('abc', 'update'), 400, {'msg': REQUIRED}, method='PATCH', body={'tag_name': None})
add('update-athlete-before-validation', 'athlete', tag('alpha', 'update'), 403, ATHLETE, method='PATCH', body={})
add('update-list-body-server-error', 'owner', tag('alpha', 'update'), 500, SERVER, method='PATCH', body=['tag_name'])
add('update-malformed-server-error', 'owner', tag('abc', 'update'), 500, SERVER, method='PATCH', body={'tag_name': 'X'})
add('update-unknown', 'owner', tag(UNKNOWN, 'update'), 404, NOT_TAG, method='PATCH', body={'tag_name': 'X'})
add('update-foreign', 'owner', tag('abroad', 'update'), 404, NOT_TAG, method='PATCH', body={'tag_name': 'X'})
add('update-restricted-missing-permission', 'restricted_collaborator', tag('alpha', 'update'), 403, MEMBERS_UPDATE, method='PATCH',
    body={'tag_name': 'X'})
add('update-wrong-method', 'owner', tag('alpha', 'update'), 405, {'detail': 'Method "POST" not allowed.'}, body={'tag_name': 'X'})
add('update-options', 'owner', tag('alpha', 'update'), 200, options('Subscription Tags Update', UPDATE_DOC), method='OPTIONS')
add('update-rename', 'owner', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 'Alfa'}, method='PATCH',
    body={'tag_name': 'Alfa'}, effect={'kind': 'update', 'actor': 'owner', 'tag': 'alpha', 'changes': {'tag_name': ['Alpha è', 'Alfa']}})
add('update-same-name-no-audit', 'owner', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 'Alfa'}, method='PATCH',
    body={'tag_name': 'Alfa'}, effect={'kind': 'update', 'actor': 'owner', 'tag': 'alpha', 'changes': {}})
add('update-number-name', 'full_collaborator', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 0}, method='PATCH',
    body={'tag_name': 0}, effect={'kind': 'update', 'actor': 'full_collaborator', 'tag': 'alpha', 'changes': {'tag_name': ['Alfa', '0']}})
add('update-form', 'owner', tag('alpha', 'update'), 200, {'tag_id': TAGS['alpha']['id'], 'tag_name': 'Alfa'}, method='PATCH',
    raw_body='tag_name=Alfa', content_type='application/x-www-form-urlencoded',
    effect={'kind': 'update', 'actor': 'owner', 'tag': 'alpha', 'changes': {'tag_name': ['0', 'Alfa']}})

# assign / unassign
ASSIGNED, UNASSIGNED = {'msg': 'tag assigned'}, {'msg': 'tag unassigned'}
add('assign-unknown-tag', 'owner', link(UNKNOWN, 'assign', 'dated'), 404, NOT_TAG, method='PATCH')
add('assign-foreign-tag', 'owner', link('abroad', 'assign', 'dated'), 404, NOT_TAG, method='PATCH')
add('assign-unknown-tag-before-malformed-subscription', 'owner', link(UNKNOWN, 'assign', 'abc'), 404, NOT_TAG, method='PATCH')
add('assign-malformed-tag-server-error', 'owner', link('abc', 'assign', 'dated'), 500, SERVER, method='PATCH')
add('assign-unknown-subscription', 'owner', link('alpha', 'assign', UNKNOWN), 404, NOT_SUB, method='PATCH')
add('assign-foreign-subscription', 'owner', link('alpha', 'assign', 'abroad'), 404, NOT_SUB, method='PATCH')
add('assign-malformed-subscription-server-error', 'owner', link('alpha', 'assign', 'abc'), 500, SERVER, method='PATCH')
add('assign-group-header-hides', 'owner', link('alpha', 'assign', 'grouped'), 404, NOT_SUB, method='PATCH',
    headers={'X-Group-Id': IDS['foreign_group']})
add('assign-athlete-denied', 'athlete', link('alpha', 'assign', 'dated'), 403, ATHLETE, method='PATCH')
add('assign-owner-without-association-server-error', 'owner_without_association', link('alpha', 'assign', 'dated'), 500, SERVER,
    method='PATCH')
add('assign-restricted-missing-permission', 'restricted_collaborator', link('alpha', 'assign', 'dated'), 403, MEMBERS_UPDATE, method='PATCH')
add('assign-admin-impersonating-foreign-tag', 'admin', link('abroad', 'assign', 'dated'), 404, NOT_TAG, method='PATCH',
    headers=ADMIN_OWNER)
add('assign-anonymous', 'anonymous', link('alpha', 'assign', 'dated'), 401, ANONYMOUS, method='PATCH')
add('assign-wrong-method', 'owner', link('alpha', 'assign', 'dated'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('assign-options', 'owner', link('alpha', 'assign', 'dated'), 200, options('Subscription Tags Assign', DOC_LINK), method='OPTIONS')
add('assign', 'owner', link('alpha', 'assign', 'dated'), 200, ASSIGNED, method='PATCH',
    effect=link_effect('owner', add=[('alpha', 'dated')]))
add('assign-again', 'owner', link('alpha', 'assign', 'dated'), 200, ASSIGNED, method='PATCH', effect=link_effect('owner'))
add('assign-deleted-subscription', 'owner', link('alpha', 'assign', 'removed'), 200, ASSIGNED, method='PATCH',
    effect=link_effect('owner', add=[('alpha', 'removed')]))
add('assign-grouped-subscription', 'full_collaborator', link('zeta', 'assign', 'grouped'), 200, ASSIGNED, method='PATCH',
    headers={'X-Group-Id': IDS['own_group']}, effect=link_effect('full_collaborator', add=[('zeta', 'grouped')]))
# The owner's season defaults (LIKE_SEASON_YEAR from 1 January) fill the missing dates.
add('assign-undated-fills-dates', 'full_collaborator', link('lower', 'assign', 'undated'), 200, ASSIGNED, method='PATCH',
    effect=link_effect('full_collaborator', add=[('lower', 'undated')], dates={'undated': ['2026-01-01', '2026-12-31']}))
add('assign-season-error-keeps-link', 'owner', link('alpha', 'assign', 'ancient'), 500, SERVER, method='PATCH',
    effect=link_effect('owner', add=[('alpha', 'ancient')]))
add('unassign-options', 'owner', link('zeta', 'unassign', 'dated'), 200, options('Subscription Tags Unassign', DOC_LINK), method='OPTIONS')
add('unassign-foreign-subscription', 'owner', link('zeta', 'unassign', 'abroad'), 404, NOT_SUB, method='PATCH')
add('unassign-athlete-denied', 'athlete', link('zeta', 'unassign', 'dated'), 403, ATHLETE, method='PATCH')
add('unassign', 'owner', link('zeta', 'unassign', 'dated'), 200, UNASSIGNED, method='PATCH',
    effect=link_effect('owner', remove=[('zeta', 'dated')]))
add('unassign-unlinked-keeps-dates-empty', 'owner', link('alpha', 'unassign', 'pending'), 200, UNASSIGNED, method='PATCH',
    effect=link_effect('owner'))
add('unassign-undated-fills-dates', 'owner', link('zeta', 'unassign', 'pending'), 200, UNASSIGNED, method='PATCH',
    effect=link_effect('owner', remove=[('zeta', 'pending')], dates={'pending': ['2025-01-01', '2025-12-31']}))

# delete
DELETE_DOC = 'API endpoint to delete a tag for the subscriptions\n' + DOC_NAME
add('delete-unknown', 'owner', tag(UNKNOWN, 'delete'), 404, NOT_TAG, method='DELETE')
add('delete-foreign', 'owner', tag('abroad', 'delete'), 404, NOT_TAG, method='DELETE')
add('delete-malformed-server-error', 'owner', tag('abc', 'delete'), 500, SERVER, method='DELETE')
add('delete-athlete-denied', 'athlete', tag('lower', 'delete'), 403, ATHLETE, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', tag('lower', 'delete'), 403, MEMBERS_UPDATE, method='DELETE')
add('delete-wrong-method', 'owner', tag('lower', 'delete'), 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH', body={})
add('delete-options', 'owner', tag('lower', 'delete'), 200, options('Subscription Tags Delete', DELETE_DOC), method='OPTIONS')
add('delete-linked', 'owner', tag('lower', 'delete'), 200, {'msg': 'tag deleted'}, method='DELETE',
    effect={'kind': 'delete', 'actor': 'owner', 'tag': 'lower', 'remove': [['lower', 'undated']]})
assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription tags authored sequence with link, date and audit effects',
                              'ids': IDS, 'tags': TAGS, 'subscriptions': SUBSCRIPTIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
