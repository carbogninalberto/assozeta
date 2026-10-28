"""Author the balance-sheet/accounts-transfer add, update and delete HTTP sequence (balance-accounts.md).

Builds on the accounts-list seed. IsAssociationUser refuses athletes (DRF's
generic 403). CustomAccountTransferSerializer validates a writable
custom_account_transfer_id (UniqueValidator), custom_account_from and
custom_account_to (nullable relations whose queryset is the association's
accounts: anything else does not exist), amount (DecimalField(10, 2)) and
date (DateTimeField, TIME_ZONE UTC, default now). add and update save with
the owner's association and return the serialized transfer; update and
delete get the association's transfer (otherwise, including a malformed id,
404 {'exception': ...}). auditlog records the create, the changed fields and
the delete. Run with ``python3 -I`` after the accounts-list generator.
"""
import copy
import json
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('balance_transfer_writes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('balance_accounts_http.json').read_text()))
IDS = SOURCE['ids']
OWN = IDS['own_association']
ACCOUNTS, TRANSFERS = SOURCE['accounts'], SOURCE['transfers']
UNKNOWN = 'a1000000-0000-4000-8000-000000949999'
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'balance-transfer-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


acc = lambda label: ACCOUNTS[label]['id'] if label else None


def item(source, target, amount, date, ident='$new'):
    return {'custom_account_transfer_id': ident, 'custom_account_from': acc(source), 'custom_account_to': acc(target),
            'amount': str(Decimal(amount).quantize(Decimal('0.01'))), 'date': date, 'sport_association': OWN}


def existing(label, **changes):
    row = {**TRANSFERS[label], **changes}
    return item(row['from'], row['to'], row['amount'], row['date'], row['id'])


ADD = '/balance-sheet/accounts-transfer/add'
one = lambda label, verb: f'/balance-sheet/accounts-transfer/{TRANSFERS[label]["id"] if label in TRANSFERS else label}/{verb}'
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
MISSING = {'exception': 'CustomAccountsTransfer matching query does not exist.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
DATE_ERROR = 'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
missing_pk = lambda value: [f'Invalid pk "{value}" - object does not exist.']
created = lambda actor, response: {'kind': 'add', 'actor': actor, 'transfer': response}
changed = lambda actor, label, changes, response: {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes, 'transfer': response}
deleted = lambda actor, label: {'kind': 'delete', 'actor': actor, 'label': label}
add('add-athlete-forbidden', 'athlete', ADD, 403, FORBIDDEN, body={'amount': '1'})
add('add-missing-amount', 'owner', ADD, 400, {'amount': ['This field is required.']}, body={})
add('add-invalid-fields', 'owner', ADD, 400, {'custom_account_from': missing_pk(ACCOUNTS['abroad']['id']), 'amount': ['A valid number is required.'],
    'date': [DATE_ERROR]}, body={'custom_account_from': ACCOUNTS['abroad']['id'], 'amount': 'abc', 'date': 'nope'})
add('add-unknown-and-malformed-accounts', 'owner', ADD, 400, {'custom_account_from': missing_pk(UNKNOWN),
    'custom_account_to': ['“abc” is not a valid UUID.']}, body={'custom_account_from': UNKNOWN, 'custom_account_to': 'abc', 'amount': '1'})
add('add-existing-id', 'owner', ADD, 400,
    {'custom_account_transfer_id': ['custom accounts transfer with this custom account transfer id already exists.']},
    body={'custom_account_transfer_id': TRANSFERS['t1']['id'], 'amount': '1'})
add('add-list-body', 'owner', ADD, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, body=[])
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={'amount': '1'}, headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403,
    {'detail': 'Missing permission: bookeeping.management.accountstransfers.create'}, body={'amount': '1'})
add('add-admin-without-session', 'admin', ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('add-anonymous', 'anonymous', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, {'name': 'Balance Sheet Accounts Transfer Add', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('add-owner', 'owner', ADD, 200, item('bank', 'cash', '12.5', '2026-10-01T09:00:00.250000Z'),
    body={'custom_account_from': ACCOUNTS['bank']['id'], 'custom_account_to': ACCOUNTS['cash']['id'], 'amount': '12.5',
          'date': '2026-10-01T09:00:00.250Z'}, effect=created('owner', item('bank', 'cash', '12.5', '2026-10-01T09:00:00.250000Z')))
add('add-full-collaborator-default-date', 'full_collaborator', ADD, 200, item(None, 'other', 3, '$now'),
    body={'custom_account_to': ACCOUNTS['other']['id'], 'amount': 3}, effect=created('full_collaborator', item(None, 'other', 3, '$now')))
add('add-form-naive-date', 'owner', ADD, 200, item('cash', None, '0.5', '2026-09-05T10:00:00Z'),
    raw_body=f'custom_account_from={ACCOUNTS["cash"]["id"]}&amount=0.5&date=2026-09-05T10:00:00', content_type='application/x-www-form-urlencoded',
    effect=created('owner', item('cash', None, '0.5', '2026-09-05T10:00:00Z')))
add('update-malformed-not-found', 'owner', one('abc', 'update'), 404, MISSING, method='PATCH', body={'amount': '1'})
add('update-foreign-not-found', 'owner', one('tf', 'update'), 404, MISSING, method='PATCH', body={'amount': '1'})
add('update-athlete-forbidden', 'athlete', one('t1', 'update'), 403, FORBIDDEN, method='PATCH', body={'amount': '1'})
add('update-foreign-account', 'owner', one('t1', 'update'), 400, {'custom_account_to': missing_pk(ACCOUNTS['abroad']['id'])}, method='PATCH',
    body={'custom_account_to': ACCOUNTS['abroad']['id']})
add('update-restricted-missing-permission', 'restricted_collaborator', one('t1', 'update'), 403,
    {'detail': 'Missing permission: bookeeping.management.accountstransfers.update'}, method='PATCH', body={'amount': '1'})
add('update-options', 'owner', one('t1', 'update'), 200, {'name': 'Balance Sheet Accounts Transfer Update', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('update-amount', 'owner', one('t1', 'update'), 200, existing('t1', amount='31'), method='PATCH', body={'amount': '31'},
    effect=changed('owner', 't1', {'amount': ['30.00', '31.00']}, existing('t1', amount='31')))
add('update-same-value-no-audit', 'owner', one('t1', 'update'), 200, existing('t1', amount='31'), method='PATCH', body={'amount': '31.00'},
    effect=changed('owner', 't1', {}, existing('t1', amount='31')))
add('update-clear-and-move', 'full_collaborator', one('t3', 'update'), 200, existing('t3', to=None, date='2026-09-30T12:00:00Z', **{'from': 'ghost'}),
    method='PATCH', body={'custom_account_to': None, 'custom_account_from': ACCOUNTS['ghost']['id'], 'date': '2026-09-30T14:00:00+02:00'},
    effect=changed('full_collaborator', 't3', {'custom_account_from': ['None', ACCOUNTS['ghost']['id']],
                                               'custom_account_to': [ACCOUNTS['other']['id'], 'None'],
                                               'date': ['2026-09-03 10:00:00', '2026-09-30 12:00:00']},
                   existing('t3', to=None, date='2026-09-30T12:00:00Z', **{'from': 'ghost'})))
add('delete-malformed-not-found', 'owner', one('abc', 'delete'), 404, MISSING, method='DELETE')
add('delete-foreign-not-found', 'owner', one('tf', 'delete'), 404, MISSING, method='DELETE')
add('delete-athlete-forbidden', 'athlete', one('t2', 'delete'), 403, FORBIDDEN, method='DELETE')
add('delete-restricted-missing-permission', 'restricted_collaborator', one('t2', 'delete'), 403,
    {'detail': 'Missing permission: bookeeping.management.accountstransfers.delete'}, method='DELETE')
add('delete-options', 'owner', one('t2', 'delete'), 200, {'name': 'Balance Sheet Accounts Transfer Delete', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('delete-owner', 'owner', one('t2', 'delete'), 200, {'msg': 'bank accounts deleted'}, method='DELETE', effect=deleted('owner', 't2'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Balance-sheet account transfer add, update and delete authored sequence with audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
