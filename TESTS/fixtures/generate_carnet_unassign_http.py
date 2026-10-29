"""Author the carnet/<uid>/unassign/<uid_subscription> HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed plus a paid payment. The fixed view (PR #28)
refuses non-association roles (including anonymous requests: the view has no
permission class), finds the carnet subscription by id within the scope user's
subscriptions (the carnet uid is unused), deletes its payment when unpaid and
then the carnet subscription. Deleted payments and carnet subscriptions get
auditlog delete entries (payments indexed through their changes, carnet
subscriptions through the actor's association). The payment entry is checked
by selected values and must be identical across backends. Run with
``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_unassign_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS, CARNETS = SOURCE['ids'], SOURCE['carnets']
HOLDINGS = {k: dict(v) for k, v in SOURCE['carnet_subscriptions'].items()}
PAYMENTS = {**SOURCE['payments'], 'p2': {'id': 'a1000000-0000-4000-8000-000000990001', 'association': IDS['own_association'], 'user': 'owner',
                                         'amount': '30.00', 'paid': True}}
HOLDINGS['someone_else']['payment'] = 'p2'
SUBS = SOURCE['subscriptions']
dumps = lambda value: json.dumps(value, sort_keys=True)
naive = lambda text: text.rstrip('Z').replace('T', ' ')


def holding_changes(label):
    row = HOLDINGS[label]
    fk = lambda value: [value, 'None'] if value else ['None', 'None']
    # The deleted payment was cleared in memory before carnet_subscription.delete().
    payment = PAYMENTS[row['payment']]['id'] if row['payment'] and PAYMENTS[row['payment']].get('paid') else None
    return {'carnet_subscription_id': [row['id'], 'None'], 'user_id': fk(IDS[row['user']] if row['user'] else None),
            'subscription': fk(SUBS[row['subscription']]['id'] if row['subscription'] else None),
            'carnet_id': [CARNETS[row['carnet']]['id'], 'None'], 'creation_date': [naive(row['created']), 'None'],
            'payment': fk(payment), 'disabled': [str(row['disabled']), 'None'], 'meta': [dumps(row['meta']), 'None']}


def removed(label, actor_association):
    row = HOLDINGS[label]
    entries = []
    payment = PAYMENTS.get(row['payment']) if row['payment'] else None
    if payment and not payment.get('paid'):
        entries.append({'model': 'payment', 'id': payment['id'], 'values': {'payment_id': [payment['id'], 'None'],
                        'amount': [payment['amount'], 'None'], 'sport_association': [payment['association'], 'None']},
                        'index': [payment['association'], 'changes_json.sport_association']})
    entries.append({'model': 'carnetsubscription', 'id': row['id'], 'changes': holding_changes(label),
                    'index': [actor_association, 'actor.sport_association'] if actor_association else None})
    return {'holding': label, 'payment': payment['id'] if payment and not payment.get('paid') else None, 'entries': entries}


path = lambda carnet, holding: f"/carnet/{CARNETS[carnet]['id']}/unassign/{HOLDINGS[holding]['id'] if holding in HOLDINGS else holding}"
cases = []


def add(name, actor, carnet, holding, status, response, *, method='DELETE', headers=None, effect=None, actor_label=None):
    cases.append({'name': f'carnet-unassign-{name}', 'actor': actor, 'method': method, 'path': path(carnet, holding), 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


NOT_FOUND, OK = {'msg': 'Carnet not found.'}, {'msg': 'Carnet removed successfully.'}
add('foreign-not-found', 'owner', 'cf', 'abroad', 404, NOT_FOUND)
add('missing-not-found', 'owner', 'c1', IDS['missing'], 404, NOT_FOUND)
add('without-subscription-not-found', 'owner', 'c2', 'detached', 404, NOT_FOUND)
add('malformed-server-error', 'owner', 'c1', 'abc', 500, '<stable-server-error-page>')
add('athlete-not-allowed', 'athlete', 'c1', 'held', 403, {'msg': 'Not allowed.'})
add('owner-scoped-athlete-denied', 'owner', 'c1', 'held', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'c1', 'held', 403, {'detail': 'Missing permission: association.carnet.update'})
add('admin-without-session', 'admin', 'c1', 'held', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous-server-error', 'anonymous', 'c1', 'held', 500, '<stable-server-error-page>')
add('wrong-method', 'owner', 'c1', 'held', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'c1', 'held', 200, {'name': 'Carnet Unassign', 'description': 'Remove a carnet from a subscription and delete the\nunpaid payments',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('owner-removes-with-unpaid-payment', 'owner', 'c1', 'held', 200, OK, effect={**removed('held', IDS['own_association']), 'actor': 'owner'})
add('repeat-not-found', 'owner', 'c1', 'held', 404, NOT_FOUND)
add('full-collaborator-keeps-paid-payment', 'full_collaborator', 'c2', 'someone_else', 200, OK,
    effect={**removed('someone_else', None), 'actor': 'full_collaborator'})
add('carnet-uid-is-ignored', 'owner', 'c2', 'family', 200, OK, effect={**removed('family', IDS['own_association']), 'actor': 'owner'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'carnet_subscriptions': HOLDINGS, 'payments': PAYMENTS,
                              'scope': 'Carnet unassign authored sequence with audit effects', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
