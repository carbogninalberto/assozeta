"""Author the modules read HTTP sequence (modules-reads.md).

Three @api_view(['GET']) IsAuthenticated views of modules_views.py:
- modules_list: request.user.sport_association (athletes are PermissionDenied
  'User is not a sport association'; an owner without an active association is
  a 500), then {'data': ModulesSerializer(modules, many=True).data}: the
  association's modules, unordered, each with every Module field, the computed
  'enabled' (always_active, or start_date <= today (UTC) <= end_date) and the
  nested SportAssociationSerializer (its 50 fields, authored exactly).
- modules_overview: request.user.sport_association first, then
  Module.objects.filter(module_id=<str>, sport_association=...).first(): a
  string the UUIDField rejects is a Django ValidationError (500); a miss is
  404 {'error': 'module not found'}; otherwise ModulesSerializerWithResponses:
  the module (sport_association as its key) and every ModuleResponses row of
  the module (unordered) with its unordered attachments and attachments_docs.
- modules_check_link: query_params['custom_link'] (missing: KeyError, 500;
  the last value of a repeated key; QueryDict decoding), more than 255
  characters is 400; a module with exactly that link in ANY association (no
  tenant filter, case- and whitespace-sensitive) is 400 unless the 'module'
  header equals its module_id as str(uuid); otherwise 200 {'valid': True}.
  request.user is never read: athletes and owners without an association pass.

modules/<custom_link>/info (AllowAny) is not part of this family. Every case
is a read: the complete state must be unchanged. Run with ``python3 -I``.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name('modules_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, DELETED = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{641001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'


def default_additional_sections():
    """SportAssociation.additional_sections' default: utils.DEFAULT_ADDITIONAL_SECTIONS (a literal)."""
    tree = ast.parse((ROOT / 'BE/application/models/utils.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == ['DEFAULT_ADDITIONAL_SECTIONS']:
            return ast.literal_eval(node.value)
    raise SystemExit('DEFAULT_ADDITIONAL_SECTIONS not found')


# The base fixture seeds both associations with only user, denomination and
# tax_code; every other SportAssociationSerializer field is the model default.
# Owners joined at seed time (< 5 days ago), so 'reviewed' is True.
ASSOCIATIONS = {
    OWN: {'denomination': 'Message reads own', 'tax_code': '70000000001', 'groups': [('Scope own group', IDS['own_group'])]},
    FOREIGN: {'denomination': 'Message reads foreign', 'tax_code': '70000000002', 'groups': [('Scope foreign group', IDS['foreign_group'])]},
}
SECTIONS = default_additional_sections()


def profile(association):
    row = ASSOCIATIONS[association]
    return {'sport_association_id': association, 'denomination': row['denomination'], 'tax_code': row['tax_code'], 'address': None,
            'address_cap': None, 'address_city': None, 'regulation': None, 'demand': None, 'logo': None, 'subscription_fee': '0.00',
            'multiple_subscription_fee': False, 'subscription_fee_plans': [], 'membership_fee': None, 'multiple_membership_fee': False,
            'membership_fee_plans': [], 'custom_subscription_form_data': None, 'document_header': None, 'invoice_footer': None,
            'enable_quotes_management': True,
            'configuration': {'mandatory_phone': False, 'mandatory_email': False, 'mandatory_signature': False},
            'reviewed': True, 'affiliate_code': None, 'additional_sections': SECTIONS, 'president_signature': None, 'stamp': None,
            'president_first_name': None, 'president_last_name': None, 'federation': None, 'enroll_number': None, 'sport': None,
            'enabled_for': ['associate', 'associate-membership', 'membership'], 'checkout_info': None, 'additional_fields': None,
            'stripe_available_methods': ['card', 'sepa_debit'], 'iban': None, 'website': None, 'abbreviated': None, 'vat_number': None,
            'whatsapp': None, 'invoice_template': 'invoice.html', 'subscription_template': 'subscription.html',
            'extra_text_invoices': None, 'imported_from_associami': False,
            'groups': [{'label': label, 'value': value} for label, value in row['groups']],
            'show_regulation_to_members': True, 'show_regulation_to_both': True, 'show_regulation_to_athletes': True,
            'show_demand_to_members': True, 'show_demand_to_both': True, 'show_demand_to_athletes': True}


LONG_LINK = 'è' * 255
BASE = {'association': OWN, 'link': None, 'created': '2026-09-01T08:00:00Z', 'title': 'Modulo', 'require_approval': False,
        'start': None, 'end': None, 'always_active': False, 'max_responses': None, 'queue_mode': False, 'only_users': False,
        'payment_required': False, 'payment_data': {}, 'response_message': None, 'allow_attachments': False, 'elements': {}}
# 'enabled' is authored per row: the dates are far enough from today (UTC).
MODULES = {
    'full': {**BASE, 'id': next(ids), 'link': 'iscrizioni è', 'created': '2026-09-01T08:00:00.123456Z', 'title': 'Iscrizioni è',
             'require_approval': True, 'start': '2026-01-01', 'end': '2026-01-31', 'always_active': True, 'max_responses': 50,
             'queue_mode': True, 'only_users': True, 'payment_required': True,
             'payment_data': {'amount': 12.5, 'methods': ['card'], 'note': None}, 'response_message': 'Grazie\nè',
             'allow_attachments': True, 'elements': [{'type': 'text', 'label': 'Nome', 'required': True}, 3, 'x'], 'enabled': True},
    'window': {**BASE, 'id': next(ids), 'link': 'finestra', 'title': 'Finestra', 'start': '2000-01-01', 'end': '2999-12-31',
               'max_responses': 0, 'enabled': True},
    'expired': {**BASE, 'id': next(ids), 'link': 'scaduto', 'title': 'Scaduto', 'start': '2000-01-01', 'end': '2000-12-31', 'enabled': False},
    'future': {**BASE, 'id': next(ids), 'link': 'futuro', 'title': 'Futuro', 'start': '2999-01-01', 'end': '2999-12-31', 'enabled': False},
    'open-start': {**BASE, 'id': next(ids), 'link': 'senza-inizio', 'title': None, 'end': '2999-12-31', 'enabled': False},
    'bare': {**BASE, 'id': next(ids), 'title': None, 'enabled': False},
    'blank-link': {**BASE, 'id': next(ids), 'link': '', 'title': 'Vuoto', 'payment_data': [], 'elements': 'testo', 'enabled': False},
    'upper': {**BASE, 'id': next(ids), 'link': 'Maiuscolo', 'title': 'Maiuscolo', 'enabled': False},
    'long': {**BASE, 'id': next(ids), 'link': LONG_LINK, 'title': 'Lungo', 'enabled': False},
    # Unreachable tenants: a soft-deleted association and no association.
    'abroad': {**BASE, 'id': next(ids), 'association': DELETED, 'link': 'estero', 'title': 'Estero', 'always_active': True, 'enabled': True},
    'orphan': {**BASE, 'id': next(ids), 'association': None, 'link': 'orfano', 'title': 'Orfano', 'enabled': False},
}
OWN_MODULES = [label for label, row in MODULES.items() if row['association'] == OWN]
DOCUMENTS = {'d1': {'id': next(ids), 'filename': 'certificato è.pdf', 'token': next(ids)},
             'd2': {'id': next(ids), 'filename': 'foto.png', 'token': next(ids)},
             'd3': {'id': next(ids), 'filename': 'estero.pdf', 'token': next(ids)}}
RESPONSES = {
    'r1': {'id': next(ids), 'module': 'full', 'created': '2026-09-02T10:00:00.500000Z', 'number': 1, 'queue': None,
           'response': {'nome': 'Anna è', 'eta': 12, 'consensi': [True, None]}, 'approved': True, 'user': 'athlete',
           'attachments': ['d1', 'd2']},
    'r2': {'id': next(ids), 'module': 'full', 'created': '2026-09-03T10:00:00Z', 'number': None, 'queue': 1,
           'response': {}, 'approved': False, 'user': None, 'attachments': []},
    'r3': {'id': next(ids), 'module': 'full', 'created': '2026-09-04T10:00:00Z', 'number': 2, 'queue': 2,
           'response': ['lista', 1], 'approved': False, 'user': 'owner', 'attachments': ['d2']},
    'r4': {'id': next(ids), 'module': 'abroad', 'created': '2026-09-04T10:00:00Z', 'number': 1, 'queue': None,
           'response': {'nome': 'Estero'}, 'approved': True, 'user': None, 'attachments': ['d3']},
}
UNKNOWN = 'a1000000-0000-4000-8000-000000999995'
cases = []


def module(label, *, nested):
    row = MODULES[label]
    data = {'module_id': row['id'], 'enabled': row['enabled'], 'custom_link': row['link'], 'creation_date': row['created'],
            'title': row['title'], 'require_approval': row['require_approval'], 'start_date': row['start'], 'end_date': row['end'],
            'always_active': row['always_active'], 'max_responses': row['max_responses'], 'queue_mode': row['queue_mode'],
            'only_users': row['only_users'], 'payment_required': row['payment_required'], 'payment_data': row['payment_data'],
            'response_message': row['response_message'], 'allow_attachments': row['allow_attachments'], 'elements': row['elements']}
    if nested:
        data['sport_association'] = profile(row['association'])
    else:
        data['sport_association'] = row['association']
        data['responses'] = [response(key) for key, item in RESPONSES.items() if item['module'] == label]
    return data


def response(key):
    row = RESPONSES[key]
    return {'module_response_id': row['id'], 'module': MODULES[row['module']]['id'], 'creation_date': row['created'],
            'progressive_response_number': row['number'], 'queue_position': row['queue'], 'response': row['response'],
            'approved': row['approved'], 'user': IDS[row['user']] if row['user'] else None, 'payment': None,
            'attachments': [DOCUMENTS[d]['id'] for d in row['attachments']],
            'attachments_docs': [{'document_id': DOCUMENTS[d]['id'], 'filename': DOCUMENTS[d]['filename'], 'token': DOCUMENTS[d]['token']}
                                 for d in row['attachments']]}


def add(name, actor, path, status, response_body, *, method='GET', headers=None):
    cases.append({'name': f'modules-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response_body})


OPTIONS = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
PERMISSION = {'detail': 'Missing permission: association.modules.read'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
SCOPED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NO_SESSION = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
OWNER_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}

# modules/list
LIST = '/modules/list'
own_list = {'data': [module(label, nested=True) for label in OWN_MODULES]}
add('list-owner', 'owner', LIST, 200, own_list)
add('list-full-collaborator', 'full_collaborator', LIST, 200, own_list)
add('list-admin-impersonating-owner', 'admin', LIST, 200, own_list, headers=ADMIN_OWNER)
add('list-query-ignored', 'owner', LIST + '?page=2&ordering=-title&search=x', 200, own_list)
add('list-foreign-group-header-ignored', 'owner', LIST, 200, own_list, headers={'X-Group-Id': IDS['foreign_group']})
add('list-malformed-group-header-ignored', 'owner', LIST, 200, own_list, headers={'X-Group-Id': 'abc'})
add('list-foreign-owner-empty', 'foreign_owner', LIST, 200, {'data': []})
add('list-athlete-denied', 'athlete', LIST, 403, NOT_ASSOCIATION)
add('list-owner-without-association', 'owner_without_association', LIST, 500, SERVER)
add('list-deleted-association-owner', 'deleted_association_owner', LIST, 500, SERVER)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST, 403, PERMISSION)
add('list-restricted-post-missing-permission', 'restricted_collaborator', LIST, 403, PERMISSION, method='POST')
add('list-owner-scoped-athlete', 'owner', LIST, 403, SCOPED, headers=OWNER_ATHLETE)
add('list-admin-without-session', 'admin', LIST, 403, NO_SESSION)
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS)
add('list-wrong-method', 'owner', LIST, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('list-delete-method', 'owner', LIST, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('list-options', 'owner', LIST, 200, OPTIONS('Modules List'), method='OPTIONS')

# modules/<module_id>/overview
overview = lambda key: f'/modules/{key}/overview'
found = lambda label: {'data': module(label, nested=False)}
NOT_FOUND = {'error': 'module not found'}
full_id = MODULES['full']['id']
add('overview-full', 'owner', overview(full_id), 200, found('full'))
add('overview-bare-no-responses', 'owner', overview(MODULES['bare']['id']), 200, found('bare'))
add('overview-json-scalars', 'owner', overview(MODULES['blank-link']['id']), 200, found('blank-link'))
add('overview-full-collaborator', 'full_collaborator', overview(full_id), 200, found('full'))
add('overview-admin-impersonating-owner', 'admin', overview(full_id), 200, found('full'), headers=ADMIN_OWNER)
# UUIDField.to_python is uuid.UUID(hex=...): case, hyphens and urn prefixes.
add('overview-uppercase-id', 'owner', overview(full_id.upper()), 200, found('full'))
add('overview-hex-id', 'owner', overview(full_id.replace('-', '')), 200, found('full'))
add('overview-urn-id', 'owner', overview('urn:uuid:' + full_id), 200, found('full'))
add('overview-foreign-owner', 'foreign_owner', overview(full_id), 404, NOT_FOUND)
add('overview-deleted-association-module', 'owner', overview(MODULES['abroad']['id']), 404, NOT_FOUND)
add('overview-orphan-module', 'owner', overview(MODULES['orphan']['id']), 404, NOT_FOUND)
add('overview-unknown', 'owner', overview(UNKNOWN), 404, NOT_FOUND)
add('overview-malformed', 'owner', overview('abc'), 500, SERVER)
add('overview-route-word', 'owner', overview('add'), 500, SERVER)
add('overview-foreign-group-header-ignored', 'owner', overview(full_id), 200, found('full'), headers={'X-Group-Id': IDS['foreign_group']})
add('overview-athlete-denied', 'athlete', overview(full_id), 403, NOT_ASSOCIATION)
# request.user.sport_association is evaluated before the UUID lookup.
add('overview-athlete-malformed-denied', 'athlete', overview('abc'), 403, NOT_ASSOCIATION)
add('overview-owner-without-association', 'owner_without_association', overview(full_id), 500, SERVER)
add('overview-deleted-association-owner', 'deleted_association_owner', overview(full_id), 500, SERVER)
add('overview-restricted-missing-permission', 'restricted_collaborator', overview(full_id), 403, PERMISSION)
add('overview-owner-scoped-athlete', 'owner', overview(full_id), 403, SCOPED, headers=OWNER_ATHLETE)
add('overview-admin-without-session', 'admin', overview(full_id), 403, NO_SESSION)
add('overview-anonymous', 'anonymous', overview(full_id), 401, ANONYMOUS)
add('overview-wrong-method', 'owner', overview(full_id), 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('overview-options', 'owner', overview(full_id), 200, OPTIONS('Modules Overview'), method='OPTIONS')

# modules/check-link
check = lambda query: '/modules/check-link' + query
VALID, TAKEN = {'valid': True}, {'valid': False, 'exception': 'invalid payload.'}
add('check-free', 'owner', check('?custom_link=libero'), 200, VALID)
add('check-taken', 'owner', check('?custom_link=finestra'), 400, TAKEN)
add('check-taken-own-module-header', 'owner', check('?custom_link=finestra'), 200, VALID, headers={'module': MODULES['window']['id']})
add('check-taken-other-module-header', 'owner', check('?custom_link=finestra'), 400, TAKEN, headers={'module': full_id})
add('check-taken-uppercase-header', 'owner', check('?custom_link=finestra'), 400, TAKEN,
    headers={'module': MODULES['window']['id'].upper()})
add('check-taken-unknown-header', 'owner', check('?custom_link=finestra'), 400, TAKEN, headers={'module': 'abc'})
add('check-free-with-header', 'owner', check('?custom_link=libero'), 200, VALID, headers={'module': full_id})
add('check-percent-plus-decoding', 'owner', check('?custom_link=iscrizioni+%C3%A8'), 400, TAKEN)
add('check-deleted-association-link', 'owner', check('?custom_link=estero'), 400, TAKEN)
add('check-orphan-link', 'owner', check('?custom_link=orfano'), 400, TAKEN)
add('check-case-sensitive', 'owner', check('?custom_link=maiuscolo'), 200, VALID)
add('check-exact-case', 'owner', check('?custom_link=Maiuscolo'), 400, TAKEN)
add('check-trailing-space', 'owner', check('?custom_link=finestra%20'), 200, VALID)
add('check-empty-matches-blank-link', 'owner', check('?custom_link='), 400, TAKEN)
add('check-bare-key-matches-blank-link', 'owner', check('?custom_link'), 400, TAKEN)
add('check-repeated-last-wins', 'owner', check('?custom_link=libero&custom_link=finestra'), 400, TAKEN)
add('check-repeated-last-free', 'owner', check('?custom_link=finestra&custom_link=libero'), 200, VALID)
add('check-long-taken', 'owner', check('?custom_link=' + '%C3%A8' * 255), 400, TAKEN)
add('check-long-own-header', 'owner', check('?custom_link=' + '%C3%A8' * 255), 200, VALID, headers={'module': MODULES['long']['id']})
add('check-too-long', 'owner', check('?custom_link=' + '%C3%A8' * 256), 400, TAKEN, headers={'module': MODULES['long']['id']})
add('check-too-long-ascii', 'owner', check('?custom_link=' + 'a' * 256), 400, TAKEN)
add('check-invalid-utf8-replaced', 'owner', check('?custom_link=%FF%FE'), 200, VALID)
add('check-malformed-escape-kept', 'owner', check('?custom_link=%zz%'), 200, VALID)
add('check-nul-server-error', 'owner', check('?custom_link=a%00b'), 500, SERVER)
add('check-missing-parameter', 'owner', check(''), 500, SERVER)
add('check-other-parameter-only', 'owner', check('?link=finestra'), 500, SERVER)
add('check-athlete-allowed', 'athlete', check('?custom_link=finestra'), 400, TAKEN)
add('check-athlete-free', 'athlete', check('?custom_link=libero'), 200, VALID)
add('check-owner-without-association', 'owner_without_association', check('?custom_link=libero'), 200, VALID)
add('check-deleted-association-owner', 'deleted_association_owner', check('?custom_link=finestra'), 400, TAKEN)
add('check-foreign-owner-sees-other-tenant', 'foreign_owner', check('?custom_link=finestra'), 400, TAKEN)
add('check-full-collaborator', 'full_collaborator', check('?custom_link=libero'), 200, VALID)
add('check-restricted-missing-permission', 'restricted_collaborator', check('?custom_link=libero'), 403, PERMISSION)
add('check-owner-scoped-athlete', 'owner', check('?custom_link=libero'), 403, SCOPED, headers=OWNER_ATHLETE)
add('check-admin-without-session', 'admin', check('?custom_link=libero'), 403, NO_SESSION)
add('check-anonymous', 'anonymous', check('?custom_link=libero'), 401, ANONYMOUS)
add('check-wrong-method', 'owner', check('?custom_link=libero'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('check-options', 'owner', check(''), 200, OPTIONS('Modules Check Link'), method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Modules reads authored sequence', 'ids': IDS, 'modules': MODULES,
                              'documents': DOCUMENTS, 'responses': RESPONSES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
