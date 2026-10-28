"""Author the balance-sheet/accounts/add and accounts/<uid>/update HTTP sequence (balance-accounts.md).

Builds on the accounts-list seed. IsAssociationUser refuses athletes (DRF's
generic 403). CustomAccountSerializer validates name, account_code
(CharField(100)), initial_balance (DecimalField(10, 2)), account_type (a
choice of 1-3), enabled, editable and a writable custom_account_id
(UniqueValidator); errors are DRF's 400 field map. add saves with the
owner's association; update gets the association's account (anything else,
including a malformed id, is 404 {'exception': ...}) and saves the partial
data. Both return the serialized account (current_balance as a float,
deletable through the group-aware payment manager). auditlog records the
create and the changed fields. Run with ``python3 -I`` after the
accounts-list generator.
"""
import copy
import json
from decimal import Decimal
from pathlib import Path

OUTPUT = Path(__file__).with_name('balance_account_writes_http.json')
SOURCE = copy.deepcopy(json.loads(Path(__file__).with_name('balance_accounts_http.json').read_text()))
IDS = SOURCE['ids']
ACCOUNTS, TRANSFERS, PAYMENTS = SOURCE['accounts'], SOURCE['transfers'], SOURCE['payments']
UNKNOWN = 'a1000000-0000-4000-8000-000000939999'
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'balance-account-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def balance(label, initial, group=None):
    total = Decimal(initial)
    total += sum((Decimal(t['amount']) for t in TRANSFERS.values() if t['to'] == label), Decimal(0))
    total -= sum((Decimal(t['amount']) for t in TRANSFERS.values() if t['from'] == label), Decimal(0))
    for p in PAYMENTS.values():
        if p['account'] == label and p['paid'] and not p['deleted'] and (group is None or p['group'] == group):
            total += -Decimal(p['amount']) if p['expense'] else Decimal(p['amount'])
    return float(total)


def deletable(label, editable, group=None):
    moved = any(label in (t['from'], t['to']) for t in TRANSFERS.values())
    paid = any(p['account'] == label and not p['deleted'] and (group is None or p['group'] == group) for p in PAYMENTS.values())
    return editable and not moved and not paid


def account(label, group=None, **changes):
    row = {**ACCOUNTS[label], **changes}
    return {'custom_account_id': row['id'], 'enabled': row['enabled'], 'name': row['name'], 'current_balance': balance(label, row['initial'], group),
            'initial_balance': str(Decimal(row['initial']).quantize(Decimal('0.01'))), 'account_type': row['type'], 'account_code': row['code'],
            'deletable': deletable(label, row['editable'], group), 'editable': row['editable']}


def new(name, initial, code, *, kind=1, enabled=True, editable=True, ident='$new'):
    value = str(Decimal(initial).quantize(Decimal('0.01')))
    return {'custom_account_id': ident, 'enabled': enabled, 'name': name, 'current_balance': float(Decimal(value)), 'initial_balance': value,
            'account_type': kind, 'account_code': code, 'deletable': editable, 'editable': editable}


ADD = '/balance-sheet/accounts/add'
update = lambda label: f'/balance-sheet/accounts/{ACCOUNTS[label]["id"] if label in ACCOUNTS else label}/update'
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
MISSING = {'exception': 'CustomAccounts matching query does not exist.'}
REQUIRED = ['This field is required.']
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
created = lambda actor, response: {'kind': 'add', 'actor': actor, 'account': response}
changed = lambda actor, label, changes, response: {'kind': 'update', 'actor': actor, 'label': label, 'changes': changes, 'account': response}
add('add-athlete-forbidden', 'athlete', ADD, 403, FORBIDDEN, body={'name': 'X'})
add('add-missing-fields', 'owner', ADD, 400, {'name': REQUIRED, 'initial_balance': REQUIRED, 'account_code': REQUIRED}, body={})
add('add-invalid-fields', 'owner', ADD, 400, {'name': ['Ensure this field has no more than 100 characters.'],
    'initial_balance': ['A valid number is required.'], 'account_type': ['"9" is not a valid choice.']},
    body={'name': 'x' * 101, 'initial_balance': 'abc', 'account_type': 9, 'account_code': 'C'})
add('add-too-many-digits', 'owner', ADD, 400, {'initial_balance': ['Ensure that there are no more than 10 digits in total.']},
    body={'name': 'Grande', 'initial_balance': '123456789.00', 'account_code': 'C'})
add('add-existing-id', 'owner', ADD, 400, {'custom_account_id': ['custom accounts with this custom account id already exists.']},
    body={'custom_account_id': ACCOUNTS['bank']['id'], 'name': 'Copia', 'initial_balance': '1', 'account_code': 'C'})
add('add-list-body', 'owner', ADD, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, body=[])
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    body={'name': 'X'}, headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, {'detail': 'Missing permission: bookeeping.management.accounts.create'},
    body={'name': 'X'})
add('add-admin-without-session', 'admin', ADD, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('add-anonymous', 'anonymous', ADD, 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, {'name': 'Balance Sheet Accounts Add', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('add-minimal', 'owner', ADD, 200, new('Nuovo è', '10.5', 'X1'), body={'name': 'Nuovo è', 'initial_balance': '10.5', 'account_code': 'X1'},
    effect=created('owner', new('Nuovo è', '10.5', 'X1')))
FIXED = 'a1000000-0000-4000-8000-000000930001'
add('add-full-collaborator-explicit', 'full_collaborator', ADD, 200, new('Banca 2', 7, 'IT7', kind=2, enabled=False, editable=False, ident=FIXED),
    body={'custom_account_id': FIXED, 'name': 'Banca 2', 'initial_balance': 7, 'account_code': 'IT7', 'account_type': '2', 'enabled': False,
          'editable': False},
    effect=created('full_collaborator', new('Banca 2', 7, 'IT7', kind=2, enabled=False, editable=False, ident=FIXED)))
# BooleanField.default_empty_html: a form without enabled/editable sends False.
add('add-form', 'owner', ADD, 200, new('Modulo', '0', 'F', enabled=False, editable=False), raw_body='name=Modulo&initial_balance=0&account_code=F',
    content_type='application/x-www-form-urlencoded', effect=created('owner', new('Modulo', '0', 'F', enabled=False, editable=False)))
add('update-malformed-not-found', 'owner', update('abc'), 404, MISSING, method='PATCH', body={'name': 'X'})
add('update-unknown-not-found', 'owner', update(UNKNOWN), 404, MISSING, method='PATCH', body={'name': 'X'})
add('update-foreign-not-found', 'owner', update('abroad'), 404, MISSING, method='PATCH', body={'name': 'X'})
add('update-athlete-forbidden', 'athlete', update('bank'), 403, FORBIDDEN, method='PATCH', body={'name': 'X'})
add('update-invalid-balance', 'owner', update('bank'), 400, {'initial_balance': ['A valid number is required.']}, method='PATCH',
    body={'initial_balance': 'abc'})
add('update-blank-code', 'owner', update('bank'), 400, {'account_code': ['This field may not be blank.']}, method='PATCH', body={'account_code': ' '})
add('update-restricted-missing-permission', 'restricted_collaborator', update('bank'), 403,
    {'detail': 'Missing permission: bookeeping.management.accounts.update'}, method='PATCH', body={'name': 'X'})
add('update-wrong-method', 'owner', update('bank'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('update-options', 'owner', update('bank'), 200, {'name': 'Balance Sheet Accounts Update', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('update-name', 'owner', update('bank'), 200, account('bank', name='Banca nuova'), method='PATCH', body={'name': 'Banca nuova'},
    effect=changed('owner', 'bank', {'name': ['Banca è', 'Banca nuova']}, account('bank', name='Banca nuova')))
add('update-same-value-no-audit', 'owner', update('bank'), 200, account('bank', name='Banca nuova'), method='PATCH', body={'name': 'Banca nuova'},
    effect=changed('owner', 'bank', {}, account('bank', name='Banca nuova')))
add('update-non-editable-allowed', 'full_collaborator', update('cash'), 200, account('cash', enabled=False, type=3), method='PATCH',
    body={'enabled': False, 'account_type': 3}, effect=changed('full_collaborator', 'cash', {'enabled': ['True', 'False'], 'account_type': ['1', '3']},
                                                              account('cash', enabled=False, type=3)))
add('update-initial-balance', 'owner', update('ghost'), 200, account('ghost', initial='8.25'), method='PATCH', body={'initial_balance': '8.25'},
    effect=changed('owner', 'ghost', {'initial_balance': ['7.00', '8.25']}, account('ghost', initial='8.25')))
add('update-group-header-balance', 'owner', update('grouped'), 200, account('grouped', group='foreign_group', code='G'), method='PATCH',
    body={'account_code': 'G'}, headers={'X-Group-Id': IDS['foreign_group']},
    effect=changed('owner', 'grouped', {'account_code': ['C', 'G']}, account('grouped', group='foreign_group', code='G')))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({**{k: v for k, v in SOURCE.items() if k != 'cases'},
                              'scope': 'Balance-sheet account add and update authored sequence with audit effects', 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
