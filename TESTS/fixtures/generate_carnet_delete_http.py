"""Author the carnet/<uid>/delete HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed. The fixed view (PR #28) refuses non-association
roles, then Carnet.get(carnet_id=uid, sport_association__user=request.user)
(404 on a miss; a non-UUID raises a Django ValidationError, a 500) and
deletes it: the collector cascades its carnet subscriptions (and their course
links). Every deleted row gets an auditlog delete entry rendered from its
stored values; the carnet is indexed through its changes, the subscriptions
(no association field) through the actor's association. Run with
``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_delete_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, CARNETS, HOLDINGS, SUBS, PAYMENTS = (SOURCE[k] for k in ('ids', 'carnets', 'carnet_subscriptions', 'subscriptions', 'payments'))
OWN = IDS['own_association']
dumps = lambda value: json.dumps(value, sort_keys=True)
naive = lambda text: text.rstrip('Z').replace('T', ' ')


def carnet_changes(label):
    row = CARNETS[label]
    values = {'carnet_id': row['id'], 'title': row['title'], 'description': row['description'], 'sport_association': row['association'],
              'lessons_number': str(row['lessons']), 'fee': row['fee'], 'creation_date': naive(row['created']), 'public': str(row['public'])}
    changes = {k: [v, 'None'] for k, v in values.items() if v is not None}
    changes['user_id'] = ['None', 'None']
    changes['meta'] = [dumps(row['meta']), 'None']
    return changes


def holding_changes(label):
    row = HOLDINGS[label]
    fk = lambda value: [value, 'None'] if value else ['None', 'None']
    return {'carnet_subscription_id': [row['id'], 'None'], 'user_id': fk(IDS[row['user']] if row['user'] else None),
            'subscription': fk(SUBS[row['subscription']]['id'] if row['subscription'] else None),
            'carnet_id': [CARNETS[row['carnet']]['id'], 'None'], 'creation_date': [naive(row['created']), 'None'],
            'payment': fk(PAYMENTS[row['payment']]['id'] if row['payment'] else None), 'disabled': [str(row['disabled']), 'None'],
            'meta': [dumps(row['meta']), 'None']}


def deleted(label, actor, actor_association, impersonation=None):
    holdings = [h for h, row in HOLDINGS.items() if row['carnet'] == label]
    entries = [{'model': 'carnet', 'id': CARNETS[label]['id'], 'changes': carnet_changes(label),
                'index': [CARNETS[label]['association'], 'changes_json.sport_association']}]
    entries += [{'model': 'carnetsubscription', 'id': HOLDINGS[h]['id'], 'changes': holding_changes(h),
                 'index': [actor_association, 'actor.sport_association'] if actor_association else None} for h in holdings]
    return {'carnet': label, 'holdings': holdings, 'actor': actor, 'impersonation': impersonation, 'entries': entries}


path = lambda key: f'/carnet/{CARNETS[key]["id"] if key in CARNETS else key}/delete'
cases = []


def add(name, actor, target, status, response, *, method='POST', headers=None, effect=None):
    cases.append({'name': f'carnet-delete-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


NOT_FOUND = {'msg': 'Carnet not found.'}
add('foreign-not-found', 'owner', 'cf', 404, NOT_FOUND)
add('missing-not-found', 'owner', IDS['missing'], 404, NOT_FOUND)
add('malformed-server-error', 'owner', 'abc', 500, '<stable-server-error-page>')
add('athlete-not-allowed', 'athlete', 'c1', 403, {'msg': 'Not allowed.'})
add('owner-scoped-athlete-denied', 'owner', 'c1', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 403, {'detail': 'Missing permission: association.carnet.delete'})
add('admin-without-session', 'admin', 'c1', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'c1', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'c1', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'c1', 200, {'name': 'Carnet Delete', 'description': 'Delete a carnet', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
OK = {'msg': 'Carnet deleted successfully.'}
add('owner-deletes-with-holdings', 'owner', 'c1', 200, OK, effect=deleted('c1', 'owner', OWN))
add('repeat-not-found', 'owner', 'c1', 404, NOT_FOUND)
add('full-collaborator-deletes', 'full_collaborator', 'c2', 200, OK, effect=deleted('c2', 'full_collaborator', None))
add('foreign-owner-deletes-own', 'foreign_owner', 'cf', 200, OK, effect=deleted('cf', 'foreign_owner', IDS['foreign_association']))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'scope': 'Carnet delete authored sequence with audit effects',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
