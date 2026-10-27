"""Author the invoice-customers/stats HTTP sequence from supplier-invoice-stats.md.

Invoices carry ``expire`` offsets in days from the runner's seed-time date
(the field is a DateField), at least two days from either window edge:
customer invoices count only due dates from today to today + 30 days. Responses name expiring rows as ``$row:<label>``; the contract builds
them from the anchored seed. Totals follow Python: Decimal sums rendered by
DRF's JSONEncoder as floats, while a sum that never added an amount stays
the integer 0. Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('customer_invoice_stats_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/invoice-customers/stats'
ids = iter(f'a1000000-0000-4000-8000-{800001 + n:012d}' for n in range(20))
ids = iter(f'a1000000-0000-4000-8000-{810001 + n:012d}' for n in range(20))
INVOICES = {
    'own_paid': {'association': OWN, 'amount': '100.50', 'paid': True, 'expire': 5, 'prefix': 'FPR', 'number': 1, 'fiscal_year': 2026},
    'own_unpaid_soon': {'association': OWN, 'amount': '20.25', 'paid': False, 'expire': 10, 'prefix': 'FPR', 'number': 2, 'fiscal_year': 2026},
    'own_unpaid_later': {'association': OWN, 'amount': '5.00', 'paid': False, 'expire': 25, 'prefix': None, 'number': None, 'fiscal_year': None},
    'own_unpaid_past': {'association': OWN, 'amount': '7.10', 'paid': False, 'expire': -5, 'prefix': 'FPR', 'number': 4, 'fiscal_year': 2025},
    'own_unpaid_far': {'association': OWN, 'amount': '3.00', 'paid': False, 'expire': 45, 'prefix': 'FPR', 'number': 5, 'fiscal_year': 2026},
    'foreign': {'association': FOREIGN, 'amount': '999.99', 'paid': False, 'expire': 12, 'prefix': 'X', 'number': 1, 'fiscal_year': 2026},
}
for row in INVOICES.values():
    row['id'] = next(ids)
OWN_STATS = {'invoices_total': 135.85, 'invoices_paid': 100.5, 'invoices_unpaid': 35.35,
             'invoices_expiring': ['$row:own_unpaid_later', '$row:own_unpaid_soon']}
cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'customer-invoice-stats-{name}', 'actor': actor, 'method': method, 'path': PATH,
                  'headers': headers or {}, 'status': status, 'response': response})


add('owner', 'owner', 200, OWN_STATS)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWN_STATS)
add('admin-impersonating-owner', 'admin', 200, OWN_STATS, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
# Nothing paid: the paid sum is still the integer 0 and the difference a Decimal.
add('foreign-owner-nothing-paid', 'foreign_owner', 200, {'invoices_total': 999.99, 'invoices_paid': 0, 'invoices_unpaid': 999.99,
    'invoices_expiring': ['$row:foreign']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: bookeeping.documents.clientinvoices.read'})
add('athlete-server-error', 'athlete', 500, '<stable-server-error-page>')
add('deleted-association-server-error', 'deleted_association_owner', 500, '<stable-server-error-page>')
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Invoice Customers Stats', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Customer invoice stats authored sequence', 'ids': IDS,
                              'invoices': INVOICES, 'window_days': 30, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
