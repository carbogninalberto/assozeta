"""Author the carnet-subscription/<uid>/topup HTTP sequence from carnet-writes.md.

Builds on the carnet-list seed plus bank accounts. The view validates the
uid (400, for every role), then gets the carnet subscription: associations
within the scope user's subscriptions, athletes anywhere (scoped to the
impersonation association) but only for their own subscription (403
'Not allowed.'; no subscription is a 500); otherwise 404. It creates a
payment through PaymentManager.create (the association's first enabled,
non-editable bank account when the subscription has a user) and
Payment.save (default category, 'Quota Corso'), then a carnet subscription
with fresh lesson counters linked to the old holding's live course
subscriptions. auditlog records both creates; saving them again changes
nothing. Run with ``python3 -I`` after the carnet-list generator.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_subscription_topup_http.json')
SOURCE = json.loads(Path(__file__).with_name('carnet_list_http.json').read_text())
IDS = SOURCE['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{960001 + n:012d}' for n in range(10))
# get_default_bank_account: sport_association, editable=False, enabled=True, BANK.
ACCOUNTS = {'bank': {'id': next(ids), 'association': OWN, 'name': 'Banca', 'type': 2, 'editable': False, 'enabled': True},
            'cash': {'id': next(ids), 'association': OWN, 'name': 'Cassa', 'type': 1, 'editable': False, 'enabled': True},
            'editable_bank': {'id': next(ids), 'association': OWN, 'name': 'Altra banca', 'type': 2, 'editable': True, 'enabled': True},
            'disabled_bank': {'id': next(ids), 'association': FOREIGN, 'name': 'Chiusa', 'type': 2, 'editable': False, 'enabled': False}}
HOLDINGS = SOURCE['carnet_subscriptions']
path = lambda key: f'/carnet-subscription/{HOLDINGS[key]["id"] if key in HOLDINGS else key}/topup'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, target, status, response, *, method='POST', headers=None, effect=None):
    cases.append({'name': f'carnet-subscription-topup-{name}', 'actor': actor, 'method': method, 'path': path(target),
                  'headers': headers or {}, 'status': status, 'response': response, 'effect': effect})


def topped(holding, actor, account, impersonation=None):
    row = HOLDINGS[holding]
    carnet = SOURCE['carnets'][row['carnet']]
    subscription = SOURCE['subscriptions'][row['subscription']]
    live = [label for label in row['courses'] if not SOURCE['course_subscriptions'][label]['deleted']]
    return {'holding': holding, 'actor': actor, 'impersonation': impersonation,
            'payment': {'user': subscription['user'], 'associate': subscription['associate'], 'amount': carnet['fee'],
                        'association': carnet['association'], 'account': account},
            'courses': live, 'lessons': carnet['lessons']}


SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
NOT_FOUND = {'msg': 'Error in topping up carnet.'}
UNKNOWN = 'a1000000-0000-4000-8000-000000969999'
add('malformed-id', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('athlete-malformed-id', 'athlete', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('foreign-not-found', 'owner', 'abroad', 404, NOT_FOUND)
add('without-subscription-not-found', 'owner', 'detached', 404, NOT_FOUND)
add('athlete-unknown-not-found', 'athlete', UNKNOWN, 404, NOT_FOUND)
add('athlete-not-holder', 'athlete', 'someone_else', 403, {'msg': 'Not allowed.'})
add('athlete-without-subscription-server-error', 'athlete', 'detached', 500, SERVER)
# The association-scoped session sees the child's family subscription, which the owner holds.
add('owner-scoped-athlete-family-not-holder', 'owner', 'family', 403, {'msg': 'Not allowed.'}, headers=SCOPED)
add('owner-scoped-athlete-foreign-denied', 'owner', 'abroad', 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, headers=SCOPED)
add('restricted-missing-permission', 'restricted_collaborator', 'held', 403, {'detail': 'Missing permission: association.carnet.update'})
add('admin-without-session', 'admin', 'held', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'held', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'held', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'held', 200, {'name': 'Carnet Subscription Topup', 'description': 'Create a new carnet with the same subscription',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
OK = {'msg': 'Carnet topped up.'}
add('owner', 'owner', 'held', 200, OK, effect=topped('held', 'owner', 'bank'))
add('athlete-own', 'athlete', 'held', 200, OK, effect=topped('held', 'athlete', 'bank'))
add('full-collaborator-family', 'full_collaborator', 'family', 200, OK, effect=topped('family', 'full_collaborator', 'bank'))
add('owner-free-carnet', 'owner', 'someone_else', 200, OK, effect=topped('someone_else', 'owner', 'bank'))
# Only a disabled bank account exists abroad: no default account.
add('athlete-foreign-association', 'athlete', 'abroad', 200, OK, effect=topped('abroad', 'athlete', None))
add('owner-scoped-athlete', 'owner', 'held', 200, OK, headers=SCOPED, effect=topped('held', 'owner', 'bank', 'owner_athlete'))
add('admin-impersonating-athlete', 'admin', 'held', 200, OK, headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']},
    effect=topped('held', 'admin', 'bank', 'admin_athlete'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'}, 'accounts': ACCOUNTS,
                              'scope': 'Carnet subscription topup authored sequence with created rows and audit effects',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
