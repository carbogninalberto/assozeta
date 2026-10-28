"""Author the balance-sheet accounts and transfers list HTTP sequence (balance-accounts.md).

Accounts belong to SportAssociation.objects.get(user=request.user).
CustomAccountSerializer computes current_balance as initial + transfers in -
transfers out + paid payments (expenses negative) through Payment.objects,
the soft-delete, group-aware manager, and deletable from editable, transfers
and that manager's payment count. The Decimal it returns renders as a JSON
float. Rows are ordered by editable only (ties unordered); related != 'true'
returns raw values, unordered. Transfers are unordered. Run with ``python3 -I``.
"""
import json
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('balance_accounts_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{910001 + n:012d}' for n in range(40))


def account(name, initial, *, kind=2, code='C', editable=True, enabled=True, association=OWN):
    return {'id': next(ids), 'association': association, 'name': name, 'initial': initial, 'type': kind, 'code': code,
            'editable': editable, 'enabled': enabled}


ACCOUNTS = {'bank': account('Banca è', '100.00', code='IT60X'), 'cash': account('Cassa', '0.00', kind=1, editable=False),
            'other': account('Altro', '20.00', kind=3, enabled=False), 'ghost': account('Vuoto', '7.00'),
            'grouped': account('Gruppo', '0.00'), 'abroad': account('Estero', '1.00', association=FOREIGN)}
TRANSFERS = {'t1': {'id': next(ids), 'from': 'bank', 'to': 'other', 'amount': '30.00', 'date': '2026-09-01T10:00:00Z', 'association': OWN},
             't2': {'id': next(ids), 'from': 'cash', 'to': 'bank', 'amount': '10.50', 'date': '2026-09-02T10:00:00.500000Z', 'association': OWN},
             't3': {'id': next(ids), 'from': None, 'to': 'other', 'amount': '5.00', 'date': '2026-09-03T10:00:00Z', 'association': OWN},
             'tf': {'id': next(ids), 'from': 'abroad', 'to': None, 'amount': '1.00', 'date': '2026-09-04T10:00:00Z', 'association': FOREIGN}}
PAYMENTS = {'income': {'id': next(ids), 'account': 'bank', 'amount': '50.00', 'paid': True, 'expense': False, 'deleted': False, 'group': None},
            'expense': {'id': next(ids), 'account': 'bank', 'amount': '20.25', 'paid': True, 'expense': True, 'deleted': False, 'group': None},
            'unpaid': {'id': next(ids), 'account': 'bank', 'amount': '999.00', 'paid': False, 'expense': False, 'deleted': False, 'group': None},
            'deleted': {'id': next(ids), 'account': 'ghost', 'amount': '7.00', 'paid': True, 'expense': False, 'deleted': True, 'group': None},
            'grouped': {'id': next(ids), 'account': 'grouped', 'amount': '12.00', 'paid': True, 'expense': False, 'deleted': False,
                        'group': 'own_group'}}
OWN_ACCOUNTS = [label for label, row in ACCOUNTS.items() if row['association'] == OWN]


def visible(payment, group):
    return not payment['deleted'] and (group is None or payment['group'] == group)


def balance(label, group=None):
    row = ACCOUNTS[label]
    total = Decimal(row['initial'])
    total += sum((Decimal(t['amount']) for t in TRANSFERS.values() if t['to'] == label), Decimal(0))
    total -= sum((Decimal(t['amount']) for t in TRANSFERS.values() if t['from'] == label), Decimal(0))
    for p in PAYMENTS.values():
        if p['account'] == label and p['paid'] and visible(p, group):
            total += -Decimal(p['amount']) if p['expense'] else Decimal(p['amount'])
    return float(total)


def deletable(label, group=None):
    row = ACCOUNTS[label]
    moved = any(label in (t['from'], t['to']) for t in TRANSFERS.values())
    paid = any(p['account'] == label and visible(p, group) for p in PAYMENTS.values())
    return row['editable'] and not moved and not paid


def related(label, group=None):
    row = ACCOUNTS[label]
    return {'custom_account_id': row['id'], 'enabled': row['enabled'], 'name': row['name'], 'current_balance': balance(label, group),
            'initial_balance': row['initial'], 'account_type': row['type'], 'account_code': row['code'], 'deletable': deletable(label, group),
            'editable': row['editable']}


def raw(label):
    row = ACCOUNTS[label]
    return {'custom_account_id': row['id'], 'enabled': row['enabled'], 'name': row['name'], 'initial_balance': float(Decimal(row['initial'])),
            'account_type': row['type'], 'account_code': row['code'], 'sport_association_id': row['association'], 'editable': row['editable']}


def simple(label):
    if label is None:
        return None
    row = ACCOUNTS[label]
    return {'custom_account_id': row['id'], 'name': row['name'], 'account_type': row['type'], 'account_code': row['code']}


def transfer(label):
    row = TRANSFERS[label]
    return {'custom_account_transfer_id': row['id'], 'custom_account_from': simple(row['from']), 'custom_account_to': simple(row['to']),
            'amount': row['amount'], 'date': row['date'], 'sport_association': row['association']}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, order='id'):
    cases.append({'name': f'balance-accounts-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'order': order})


ACCOUNTS_PATH, TRANSFERS_PATH = '/balance-sheet/accounts/list', '/balance-sheet/accounts-transfer/list'
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
DENIED_ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
add('owner', 'owner', ACCOUNTS_PATH, 200, {'data': [related(l) for l in OWN_ACCOUNTS]}, order='editable')
add('owner-group-payments', 'owner', ACCOUNTS_PATH, 200, {'data': [related(l, 'own_group') for l in OWN_ACCOUNTS]}, order='editable',
    headers={'X-Group-Id': IDS['own_group']})
add('owner-related-false', 'owner', ACCOUNTS_PATH + '?related=false', 200, {'data': [raw(l) for l in OWN_ACCOUNTS]})
# Only the exact string 'true' selects the serializer.
add('owner-related-capitalized-raw', 'owner', ACCOUNTS_PATH + '?related=True', 200, {'data': [raw(l) for l in OWN_ACCOUNTS]})
add('full-collaborator-owner-scope', 'full_collaborator', ACCOUNTS_PATH, 200, {'data': [related(l) for l in OWN_ACCOUNTS]}, order='editable')
add('admin-impersonating-owner', 'admin', ACCOUNTS_PATH, 200, {'data': [related(l) for l in OWN_ACCOUNTS]}, order='editable',
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', ACCOUNTS_PATH, 200, {'data': [related('abroad')]}, order='editable')
add('owner-without-association-server-error', 'owner_without_association', ACCOUNTS_PATH, 500, '<stable-server-error-page>')
add('athlete-forbidden', 'athlete', ACCOUNTS_PATH, 403, FORBIDDEN)
add('owner-scoped-athlete-denied', 'owner', ACCOUNTS_PATH, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers=SCOPED)
add('restricted-missing-permission', 'restricted_collaborator', ACCOUNTS_PATH, 403, {'detail': 'Missing permission: bookeeping.management.accounts.read'})
add('admin-without-session', 'admin', ACCOUNTS_PATH, 403, DENIED_ADMIN)
add('anonymous', 'anonymous', ACCOUNTS_PATH, 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', ACCOUNTS_PATH, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', ACCOUNTS_PATH, 200, {'name': 'Balance Sheet Accounts List', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('transfers-owner', 'owner', TRANSFERS_PATH, 200, {'data': [transfer(l) for l in ('t1', 't2', 't3')]}, order='transfer')
add('transfers-full-collaborator', 'full_collaborator', TRANSFERS_PATH, 200, {'data': [transfer(l) for l in ('t1', 't2', 't3')]}, order='transfer')
add('transfers-foreign-owner', 'foreign_owner', TRANSFERS_PATH, 200, {'data': [transfer('tf')]}, order='transfer')
add('transfers-owner-without-association-server-error', 'owner_without_association', TRANSFERS_PATH, 500, '<stable-server-error-page>')
add('transfers-athlete-forbidden', 'athlete', TRANSFERS_PATH, 403, FORBIDDEN)
add('transfers-restricted-missing-permission', 'restricted_collaborator', TRANSFERS_PATH, 403,
    {'detail': 'Missing permission: bookeeping.management.accountstransfers.read'})
add('transfers-admin-without-session', 'admin', TRANSFERS_PATH, 403, DENIED_ADMIN)
add('transfers-anonymous', 'anonymous', TRANSFERS_PATH, 401, {'detail': 'Authentication credentials were not provided.'})
add('transfers-wrong-method', 'owner', TRANSFERS_PATH, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('transfers-options', 'owner', TRANSFERS_PATH, 200, {'name': 'Balance Sheet Accounts Transfer List', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Balance-sheet accounts and transfers list authored sequence', 'ids': IDS,
                              'accounts': ACCOUNTS, 'transfers': TRANSFERS, 'payments': PAYMENTS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
