"""Author the invoice-suppliers/<uid>/delete and invoice-customers/<uid>/delete sequence.

Every seeded field is explicit, so the expected auditlog delete changes are
authored here: auditlog renders each non-None stored value against 'None'
(str values as-is, booleans True/False, integers and Decimals as str(),
dates ISO, datetimes as naive UTC str(), JSON as sorted ASCII json.dumps,
foreign keys as their raw id, and a null foreign key as 'None'). Deleted rows leave nothing to resolve, so the
index falls back to changes_json.sport_association. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('invoice_deletes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{820001 + n:012d}' for n in range(20))
SUPPLIER = {'id': next(ids), 'association': OWN, 'name': 'Fornitore'}


def supplier_invoice(association, **values):
    row = {'invoice_supplier_id': next(ids), 'invoice_identifier': 'F-1 è', 'amount': '20.50', 'payment_date': '2026-10-01T08:30:00.123456Z',
           'expire_date': '2026-11-01T00:00:00Z', 'sport_association': association, 'payment': None, 'paid': True, 'notes': None,
           'document_pdf': None, 'supplier': SUPPLIER['id']}
    row.update(values)
    return row


TEXT = ['assignor_vat_number', 'assignor_tax_code', 'assignor_denomination', 'assignor_address', 'assignor_postal_code',
        'assignor_city', 'assignor_province', 'assignor_contact_email', 'assignor_contact_phone', 'transferor_vat_number',
        'transferor_tax_code', 'transferor_denomination', 'transferor_address', 'transferor_postal_code', 'transferor_city',
        'transferor_province', 'transferor_contact_email', 'causal', 'payment_condition', 'payment_modality', 'xml']


def customer_invoice(association):
    row = {'customer_invoice_id': next(ids), 'prefix': 'FPR', 'number': 7, 'fiscal_year': 2026, 'assignor_prefix_vat_number': 'IT',
           'assignor_fiscal_regime': 'RF01', 'assignor_country': 'IT', 'assignor_fax': None, 'transferor_prefix_vat_number': 'IT',
           'transferor_fiscal_regime': 'RF01', 'transferor_country': 'IT', 'transferor_contact_phone': '', 'transferor_fax': None,
           'e_invoice': True, 'country_transmitter_prefix': 'IT', 'id_transmitter': None, 'transmitting_format': 'FPR12',
           'document_type': 'TD01', 'currency': 'EUR', 'transmitting_date': '2026-10-02',
           'lines': [{'z': 'riga à', 'a': 1.50, 'n': None}], 'payment_total_amount': '122.00', 'payment_expiry_date': '2026-12-31',
           'pdf': None, 'json': {'b': [True], 'a': 'x'}, 'sport_association': association, 'payment': None,
           'creation_date': '2026-10-02T09:00:00Z', 'paid': False, 'transmitted': False}
    for index, name in enumerate(TEXT):
        row[name] = f'{name[:2]}{index}'
    row['assignor_province'], row['transferor_province'] = 'RM', 'MI'
    return row


def audit_changes(row, types):
    def render(name, value):
        kind = types.get(name, 'text')
        if kind == 'bool':
            return 'True' if value else 'False'
        if kind == 'datetime':
            date, time = value.rstrip('Z').split('T')
            return f'{date} {time}'
        if kind == 'json':
            return json.dumps(value, sort_keys=True)
        return str(value)
    # A null foreign key stays None under smart_str(strings_only=True) and so
    # differs from the absent side's 'None' string: auditlog records it too.
    return {name: [render(name, value) if value is not None else 'None', 'None'] for name, value in row.items()
            if value is not None or types.get(name) == 'fk'}


SUPPLIER_TYPES = {'paid': 'bool', 'payment_date': 'datetime', 'expire_date': 'datetime', 'payment': 'fk', 'document_pdf': 'fk',
                  'supplier': 'fk', 'sport_association': 'fk'}
CUSTOMER_TYPES = {'e_invoice': 'bool', 'paid': 'bool', 'transmitted': 'bool', 'creation_date': 'datetime', 'lines': 'json', 'json': 'json',
                  'pdf': 'fk', 'payment': 'fk', 'sport_association': 'fk'}
SEED = {'own_supplier_invoice': supplier_invoice(OWN, notes='nota'), 'foreign_supplier_invoice': supplier_invoice(FOREIGN, supplier=None),
        'own_customer_invoice': customer_invoice(OWN), 'foreign_customer_invoice': customer_invoice(FOREIGN)}
SUPPLIER_PATH = lambda value: f'/invoice-suppliers/{SEED[value]["invoice_supplier_id"] if value in SEED else value}/delete'
CUSTOMER_PATH = lambda value: f'/invoice-customers/{SEED[value]["customer_invoice_id"] if value in SEED else value}/delete'
SERVER = '<stable-server-error-page>'
cases = []


def add(name, actor, method, path, status, response, *, effect=None, headers=None):
    cases.append({'name': f'invoice-deletes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


for kind, path, label, model, types, permission, view in (
        ('supplier', SUPPLIER_PATH, 'own_supplier_invoice', 'invoicesuppliers', SUPPLIER_TYPES, 'supplierinvoices', 'Invoice Suppliers Delete'),
        ('customer', CUSTOMER_PATH, 'own_customer_invoice', 'customerinvoice', CUSTOMER_TYPES, 'clientinvoices', 'Invoice Customers Delete')):
    foreign = 'foreign_' + label[4:]
    key = 'invoice_supplier_id' if kind == 'supplier' else 'customer_invoice_id'
    add(f'{kind}-malformed', 'owner', 'DELETE', path('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'])
    add(f'{kind}-foreign-server-error', 'owner', 'DELETE', path(foreign), 500, SERVER)
    add(f'{kind}-missing-server-error', 'owner', 'DELETE', path(IDS['missing']), 500, SERVER)
    add(f'{kind}-restricted-missing-permission', 'restricted_collaborator', 'DELETE', path(label), 403,
        {'detail': f'Missing permission: bookeeping.documents.{permission}.delete'})
    add(f'{kind}-athlete-server-error', 'athlete', 'DELETE', path(label), 500, SERVER)
    add(f'{kind}-admin-without-session', 'admin', 'DELETE', path(label), 403,
        {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-anonymous', 'anonymous', 'DELETE', path(label), 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-wrong-method', 'owner', 'GET', path(label), 405, {'detail': 'Method "GET" not allowed.'})
    add(f'{kind}-options', 'owner', 'OPTIONS', path(label), 200, {'name': view, 'description': '', 'renders': ['application/json'],
        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
    add(f'{kind}-owner-deletes', 'owner', 'DELETE', path(label), 200, {'message': 'Invoice deleted'},
        effect={'model': model, 'id': SEED[label][key], 'changes': audit_changes(SEED[label], types)})
    add(f'{kind}-repeat-server-error', 'owner', 'DELETE', path(label), 500, SERVER)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Supplier and customer invoice delete authored sequence with audit effects',
                              'ids': IDS, 'supplier': SUPPLIER, 'seed': SEED, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
