"""Author the balance-sheet/archived HTTP sequence from balance-archived.md.

Despite its name the view lists the association's balance sheets with
archived=False, newest first: the stored data, the raw creation_date (DRF's
encoder: isoformat with Z), year and draft. It appends SportAssociationSerializer
for the association, already characterized by the profile families; this
sequence checks that object's authored keys and ID, and the runner requires it
to be identical across backends. SportAssociation.objects.get(user=request.user)
runs before the role check. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('balance_archived_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{920001 + n:012d}' for n in range(10))
SHEETS = {
    'draft': {'id': next(ids), 'association': OWN, 'status': 1, 'year': 2026, 'archived': False, 'created': '2026-03-01T09:00:00.123456Z',
              'data': {'b': [1, 2.5], 'a': {'è': None}}},
    'approved': {'id': next(ids), 'association': OWN, 'status': 2, 'year': 2025, 'archived': False, 'created': '2026-01-15T09:00:00Z', 'data': None},
    'archived': {'id': next(ids), 'association': OWN, 'status': 2, 'year': 2024, 'archived': True, 'created': '2026-02-01T09:00:00Z', 'data': {}},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'status': 1, 'year': 2026, 'archived': False, 'created': '2026-02-02T09:00:00Z', 'data': []},
}
PROFILE_KEYS = sorted(['denomination', 'tax_code', 'address', 'address_cap', 'address_city', 'regulation', 'demand', 'logo', 'subscription_fee',
    'multiple_subscription_fee', 'subscription_fee_plans', 'membership_fee', 'multiple_membership_fee', 'membership_fee_plans',
    'custom_subscription_form_data', 'document_header', 'invoice_footer', 'enable_quotes_management', 'configuration', 'reviewed',
    'affiliate_code', 'sport_association_id', 'additional_sections', 'president_signature', 'stamp', 'president_first_name',
    'president_last_name', 'federation', 'enroll_number', 'sport', 'enabled_for', 'checkout_info', 'additional_fields',
    'stripe_available_methods', 'iban', 'website', 'abbreviated', 'vat_number', 'whatsapp', 'invoice_template', 'subscription_template',
    'extra_text_invoices', 'imported_from_associami', 'groups', 'show_regulation_to_members', 'show_regulation_to_both',
    'show_regulation_to_athletes', 'show_demand_to_members', 'show_demand_to_both', 'show_demand_to_athletes'])


def sheets(*labels, association=OWN):
    rows = [{'data': SHEETS[l]['data'], 'creation_date': SHEETS[l]['created'], 'year': SHEETS[l]['year'], 'draft': SHEETS[l]['status'] == 1}
            for l in labels]
    return {'balance_sheets': rows, 'sport_association': {'$profile': association}}


cases = []


def add(name, actor, status, response, *, method='GET', headers=None):
    cases.append({'name': f'balance-archived-{name}', 'actor': actor, 'method': method, 'path': '/balance-sheet/archived',
                  'headers': headers or {}, 'status': status, 'response': response})


add('owner', 'owner', 200, sheets('draft', 'approved'))
add('full-collaborator-owner-scope', 'full_collaborator', 200, sheets('draft', 'approved'))
add('admin-impersonating-owner', 'admin', 200, sheets('draft', 'approved'), headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 200, sheets('abroad', association=FOREIGN))
add('owner-without-association-server-error', 'owner_without_association', 500, '<stable-server-error-page>')
add('athlete-forbidden', 'athlete', 403, {'detail': 'You do not have permission to perform this action.'})
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: bookeeping.management.balancesheet.read'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Balance Sheet Archived', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Balance-sheet archived authored sequence', 'ids': IDS, 'sheets': SHEETS,
                              'profile_keys': PROFILE_KEYS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
