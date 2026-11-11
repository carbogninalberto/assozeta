"""Author the carnet/<uid>/unassign/<uid_subscription> HTTP sequence from carnet-writes.md.

Builds on the carnet-replace seed (courses, installments, payments) plus a paid payment. The fixed view (PR #28)
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
SOURCE = json.loads(Path(__file__).with_name('carnet_replace_http.json').read_text())
IDS, CARNETS = SOURCE['ids'], SOURCE['carnets']
HOLDINGS = {k: dict(v) for k, v in SOURCE['carnet_subscriptions'].items()}
PAYMENTS = {**SOURCE['payments'], 'p2': {'id': 'a1000000-0000-4000-8000-000000990001', 'association': IDS['own_association'], 'user': 'owner',
                                         'amount': '30.00', 'paid': True}}
HOLDINGS['someone_else']['payment'] = 'p2'
# A payment shared by two live installments of a course subscription.
PAYMENTS['p_dual'] = {'id': 'a1000000-0000-4000-8000-000000990002', 'association': IDS['own_association'], 'user': 'athlete', 'amount': '10.00',
                      'paid': False}
INSTALLMENTS = {**SOURCE['installments'],
                'i_d1': {'id': 'a1000000-0000-4000-8000-000000990003', 'course_subscription': 'second', 'number': 7, 'paid': False, 'payment': 'p_dual'},
                'i_d2': {'id': 'a1000000-0000-4000-8000-000000990004', 'course_subscription': 'second', 'number': 8, 'paid': False, 'payment': 'p_dual'}}
# Holdings whose unpaid payment is the payment of live installments (the payment's pre_delete signal deletes them).
for label, number, payment in (('cascade_one', 5, 'p_i1'), ('cascade_two', 6, 'p_dual')):
    HOLDINGS[label] = {**HOLDINGS['family'], 'id': f'a1000000-0000-4000-8000-00000099010{number}', 'payment': payment,
                       'created': f'2026-10-0{number}T09:30:00Z'}
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
    gone = payment and not payment.get('paid')

    def payment_entry():
        return {'model': 'payment', 'id': payment['id'], 'values': {'payment_id': [payment['id'], 'None'],
                'amount': [payment['amount'], 'None'], 'sport_association': [payment['association'], 'None']},
                'index': [payment['association'], 'changes_json.sport_association']}
    installments = sorted((i for i in INSTALLMENTS.values() if gone and i['payment'] == row['payment']), key=lambda i: i['id'])
    if gone:
        # payment.delete(): the signal deletes the payment's installments first; the first one deletes the payment (audited),
        # each installment is audited, then the outer delete audits the payment again.
        if installments:
            entries.append(payment_entry())
            entries.extend({'model': 'coursesubscriptioninstallment', 'id': i['id'], 'rendered': True,
                            'index': [actor_association, 'actor.sport_association'] if actor_association else None} for i in installments)
        entries.append(payment_entry())
    entries.append({'model': 'carnetsubscription', 'id': row['id'], 'changes': holding_changes(label),
                    'index': [actor_association, 'actor.sport_association'] if actor_association else None})
    return {'holding': label, 'payment': payment['id'] if gone else None, 'installments': [i['id'] for i in installments], 'entries': entries}


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
# Installments of the unpaid payment are deleted by its pre_delete signal (each audited, the payment twice).
add('owner-removes-payment-with-installment', 'owner', 'c1', 'cascade_one', 200, OK, effect={**removed('cascade_one', IDS['own_association']), 'actor': 'owner'})
add('carnet-uid-is-ignored', 'owner', 'c2', 'family', 200, OK, effect={**removed('family', IDS['own_association']), 'actor': 'owner'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'carnet_subscriptions': HOLDINGS, 'payments': PAYMENTS, 'installments': INSTALLMENTS,
                              'scope': 'Carnet unassign authored sequence with audit effects', 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
