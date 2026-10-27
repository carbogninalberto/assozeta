"""Author the invoice-suppliers/stats HTTP sequence from supplier-invoice-stats.md.

Invoices carry ``expire`` offsets in days from an anchor the runner fixes
(whole seconds) at seed time, at least five days from the 30-day window
edge. Responses name expiring rows as ``$row:<label>``; the contract builds
them from the anchored seed. Totals follow Python: Decimal sums rendered by
DRF's JSONEncoder as floats, while a sum that never added an amount stays
the integer 0. Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('supplier_invoice_stats_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/invoice-suppliers/stats'
ids = iter(f'a1000000-0000-4000-8000-{800001 + n:012d}' for n in range(20))
SUPPLIER = {'id': next(ids), 'association': OWN, 'name': 'Fornitore è'}
INVOICES = {
    'own_paid': {'association': OWN, 'amount': '100.50', 'paid': True, 'expire': -5, 'identifier': 'F-1', 'supplier': True},
    'own_unpaid_soon': {'association': OWN, 'amount': '20.25', 'paid': False, 'expire': 10, 'identifier': 'F-2 à', 'supplier': True},
    'own_unpaid_past': {'association': OWN, 'amount': '5.00', 'paid': False, 'expire': -40, 'identifier': 'F-3', 'supplier': False},
    'own_unpaid_late': {'association': OWN, 'amount': '7.10', 'paid': False, 'expire': 40, 'identifier': 'F-4', 'supplier': True},
    'own_unpaid_undated': {'association': OWN, 'amount': '3.00', 'paid': False, 'expire': None, 'identifier': 'F-5', 'supplier': False},
    'foreign': {'association': FOREIGN, 'amount': '999.99', 'paid': False, 'expire': 50, 'identifier': 'X-1', 'supplier': False},
}
for row in INVOICES.values():
    row['id'] = next(ids)
OWN_STATS = {'invoices_total': 135.85, 'invoices_paid': 100.5, 'invoices_unpaid': 35.35,
             'invoices_expiring': ['$row:own_unpaid_soon', '$row:own_unpaid_past']}
cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'supplier-invoice-stats-{name}', 'actor': actor, 'method': method, 'path': PATH,
                  'headers': headers or {}, 'status': status, 'response': response})


add('owner', 'owner', 200, OWN_STATS)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWN_STATS)
add('admin-impersonating-owner', 'admin', 200, OWN_STATS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
# Nothing paid: the paid sum is still the integer 0 and the difference a Decimal.
add('foreign-owner-nothing-paid', 'foreign_owner', 200, {'invoices_total': 999.99, 'invoices_paid': 0, 'invoices_unpaid': 999.99,
    'invoices_expiring': []})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: bookeeping.documents.supplierinvoices.read'})
add('athlete-server-error', 'athlete', 500, '<stable-server-error-page>')
add('deleted-association-server-error', 'deleted_association_owner', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Invoice Suppliers Stats', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Supplier invoice stats authored sequence', 'ids': IDS,
                              'supplier': SUPPLIER, 'invoices': INVOICES, 'window_days': 30, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
