"""Author the carnet-subscription/<uid>/enable|disable HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed. Both views refuse non-association roles, then
CarnetSubscription.get(carnet_subscription_id=uid,
subscription__sport_association__user=request.user) (404 on a miss; a non-UUID
is a 500) and save the disabled flag; auditlog records it only when it
changes, indexed through the subscription's association. Run with
``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_subscription_toggle_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, HOLDINGS = SOURCE['ids'], SOURCE['carnet_subscriptions']
path = lambda action, key: f'/carnet-subscription/{HOLDINGS[key]["id"] if key in HOLDINGS else key}/{action}'
cases = []


def add(name, actor, action, target, status, response, *, method='PATCH', headers=None, effect=None):
    cases.append({'name': f'carnet-subscription-{action}-{name}', 'actor': actor, 'method': method, 'path': path(action, target),
                  'headers': headers or {}, 'status': status, 'response': response, 'effect': effect})


def toggled(label, disabled, changed, actor='owner'):
    return {'holding': label, 'disabled': disabled, 'changes': {'disabled': [str(not disabled), str(disabled)]} if changed else None, 'actor': actor}


NOT_FOUND = {'msg': 'Carnet subscription not found.'}
for action in ('disable', 'enable'):
    add('foreign-not-found', 'owner', action, 'abroad', 404, NOT_FOUND)
    add('without-subscription-not-found', 'owner', action, 'detached', 404, NOT_FOUND)
    add('missing-not-found', 'owner', action, IDS['missing'], 404, NOT_FOUND)
    add('malformed-server-error', 'owner', action, 'abc', 500, '<stable-server-error-page>')
    add('athlete-not-allowed', 'athlete', action, 'held', 403, {'msg': 'Not allowed.'})
    add('owner-scoped-athlete-denied', 'owner', action, 'held', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
        headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
    add('restricted-missing-permission', 'restricted_collaborator', action, 'held', 403, {'detail': 'Missing permission: association.carnet.update'})
    add('admin-without-session', 'admin', action, 'held', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('anonymous', 'anonymous', action, 'held', 401, {'detail': 'Authentication credentials were not provided.'})
    add('wrong-method', 'owner', action, 'held', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
    add('options', 'owner', action, 'held', 200, {'name': f'Carnet Subscription {action.title()}',
        'description': f'Set carnet subscription to {action}d', 'renders': ['application/json'],
        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
DISABLED, ENABLED = {'msg': 'Carnet subscription disabled.'}, {'msg': 'Carnet subscription enabled.'}
add('owner', 'owner', 'disable', 'held', 200, DISABLED, effect=toggled('held', True, True))
add('repeat-no-audit', 'owner', 'disable', 'held', 200, DISABLED, effect=toggled('held', True, False))
add('owner', 'owner', 'enable', 'held', 200, ENABLED, effect=toggled('held', False, True))
add('already-enabled-no-audit', 'owner', 'enable', 'family', 200, ENABLED, effect=toggled('family', False, False))
add('full-collaborator', 'full_collaborator', 'disable', 'family', 200, DISABLED, effect=toggled('family', True, True, 'full_collaborator'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Carnet subscription enable/disable authored sequence',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
