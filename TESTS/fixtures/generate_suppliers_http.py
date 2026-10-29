"""Author the supplier list/info HTTP sequence from suppliers.md.

Suppliers are read through the soft-delete, group-aware manager filtered by
SportAssociation.objects.get(user=request.user) (collaborators act as the
owner). The list paginates with DRF PageNumberPagination
(``pagination[page]``, ``pagination[perpage]``, default 10), searches every
text field with ``icontains`` and orders by the raw ``sort[field]`` (prefixed
with ``-`` for ``sort[sort]=desc``); without a sort the rows are unordered, so
those cases fit in one page and compare unordered. Athletes get an empty 403.
Run with ``python3 -I``.
"""
import json
from pathlib import Path
from urllib.parse import quote

OUTPUT = Path(__file__).with_name('suppliers_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{900001 + n:012d}' for n in range(40))
FIELDS = ('name', 'address', 'tax_code', 'vat_number', 'type', 'email', 'phone_number', 'city', 'cap', 'state', 'country', 'nationality', 'note')


def supplier(name, *, association=OWN, deleted=False, group=None, **values):
    row = {'id': next(ids), 'association': association, 'deleted': deleted, 'group': group, 'name': name,
           **{field: None for field in FIELDS if field != 'name'}, 'type': 'supplier', 'country': 'Italia', 'nationality': 'Italiana'}
    row.update(values)
    return row


SUPPLIERS = {
    'acqua': supplier('Acqua Srl', city='Roma', email='acqua@example.test', note='Fornitura 100%'),
    'beta': supplier('Beta è Figli', city='Milano', type='customer', address='Via Rossi 1'),
    'cavalli': supplier('Cavalli', city=None, tax_code='CVLTAX', vat_number='IT0001'),
    'delta': supplier('Delta_Sport', city='Torino', phone_number='+39 011'),
    'emme': supplier('Emme', city='Bari', state='BA', cap='70100'),
    'effe': supplier('Effe Rossi', city='Napoli'),
    'gi': supplier('Gi', city='Gela', country=None, nationality=None),
    'acca': supplier('Acca', city='Genova', type='pa'),
    'ii': supplier('Ii Ènte', city='Pisa'),
    'elle': supplier('Elle', city='Lecce'),
    'emmeemme': supplier('Emmeemme', city='Como'),
    'grouped': supplier('Gruppo Uno', city='Asti', group='own_group'),
    'gone': supplier('Rossi Cancellato', deleted=True, city='Roma'),
    'estero': supplier('Rossi Estero', association=FOREIGN, city='Roma'),
}
LIVE = [label for label, row in SUPPLIERS.items() if row['association'] == OWN and not row['deleted']]
SERVER = '<stable-server-error-page>'


def serialized(label):
    row = SUPPLIERS[label]
    return {'supplier_id': row['id'], **{field: row[field] for field in FIELDS}, 'sport_association': row['association'],
            'group': IDS[row['group']] if row['group'] else None, 'deleted': False}


def by(field, labels, desc=False):
    # PostgreSQL: NULLS LAST ascending, NULLS FIRST descending; Python sorts
    # ASCII here, matching the C-collation test database.
    present = sorted((l for l in labels if SUPPLIERS[l][field] is not None), key=lambda l: SUPPLIERS[l][field], reverse=desc)
    missing = [l for l in labels if SUPPLIERS[l][field] is None]
    return missing + present if desc else present + missing


def page(labels, number, size, total=None):
    total = len(labels) if total is None else total
    return {'data': [serialized(l) for l in labels], 'meta': {'total': total, 'page': number, 'pages': max(1, -(-total // size)), 'perpage': size}}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, ordered=True):
    cases.append({'name': f'suppliers-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'ordered': ordered})


def q(**params):
    keys = {'page': 'pagination[page]', 'perpage': 'pagination[perpage]', 'search': 'query[generalSearch]', 'field': 'sort[field]',
            'sort': 'sort[sort]', 'all': 'all'}
    return '/supplier/list?' + '&'.join(f'{quote(keys[k])}={quote(str(v))}' for k, v in params.items())


NAMES = by('name', LIVE)
add('owner-unsorted-single-page', 'owner', q(perpage=50), 200, page(LIVE, 1, 50), ordered=False)
add('owner-sorted-second-page', 'owner', q(field='name', perpage=5, page=2), 200, page(NAMES[5:10], 2, 5, len(LIVE)))
add('owner-sorted-desc-default-size', 'owner', q(field='name', sort='desc'), 200, page(by('name', LIVE, True)[:10], 1, 10, len(LIVE)))
add('owner-sort-nulls-last', 'owner', q(field='city', perpage=50), 200, page(by('city', LIVE), 1, 50))
add('owner-sort-nulls-first-desc', 'owner', q(field='city', sort='desc', perpage=50), 200, page(by('city', LIVE, True), 1, 50))
add('owner-sort-minus-prefix', 'owner', q(field='-name', perpage=3), 200, page(by('name', LIVE, True)[:3], 1, 3, len(LIVE)))
add('owner-last-page', 'owner', q(field='name', perpage=5, page='last'), 200, page(NAMES[10:], 3, 5, len(LIVE)))
add('owner-empty-page-is-first', 'owner', q(field='name', perpage=5, page=''), 200, page(NAMES[:5], 1, 5, len(LIVE)))
add('owner-spaced-page', 'owner', q(field='name', perpage=5, page=' 2 '), 200, page(NAMES[5:10], 2, 5, len(LIVE)))
add('owner-invalid-perpage-default', 'owner', q(field='name', perpage='0'), 200, page(NAMES[:10], 1, 10, len(LIVE)))
add('owner-text-perpage-default', 'owner', q(field='name', perpage='x'), 200, page(NAMES[:10], 1, 10, len(LIVE)))
add('owner-page-zero-not-found', 'owner', q(field='name', page=0), 404, {'detail': 'Invalid page.'})
add('owner-page-past-end-not-found', 'owner', q(field='name', page=3), 404, {'detail': 'Invalid page.'})
add('owner-page-text-not-found', 'owner', q(field='name', page='due'), 404, {'detail': 'Invalid page.'})
ROSSI = [l for l in LIVE if any('ROSSI' in (SUPPLIERS[l][f] or '').upper() for f in FIELDS)]
add('owner-search-every-text-field', 'owner', q(search='rossi', field='name', perpage=50), 200, page(by('name', ROSSI), 1, 50))
add('owner-search-percent-literal', 'owner', q(search='100%', perpage=50), 200, page(['acqua'], 1, 50))
add('owner-search-underscore-literal', 'owner', q(search='a_s', perpage=50), 200, page(['delta'], 1, 50))
# icontains is UPPER(column) LIKE UPPER(pattern): non-ASCII folding follows the
# database ctype (C in the harness), so only ASCII letters change case here.
add('owner-search-accent-ascii-case', 'owner', q(search='ÈNTE', perpage=50), 200, page(['ii'], 1, 50))
add('owner-search-no-match', 'owner', q(search='zzz'), 200, page([], 1, 10))
add('owner-all-unpaginated', 'owner', q(all=1, field='nope', search='zzz'), 200, {'data': [serialized(l) for l in LIVE]}, ordered=False)
add('owner-unknown-sort-server-error', 'owner', q(field='nope'), 500, SERVER)
add('owner-double-minus-server-error', 'owner', q(field='-name', sort='desc'), 500, SERVER)
add('owner-group-filter', 'owner', q(perpage=50), 200, page(['grouped'], 1, 50), headers={'X-Group-Id': IDS['own_group']})
add('owner-invalid-group-unfiltered', 'owner', q(perpage=50), 200, page(LIVE, 1, 50), headers={'X-Group-Id': 'nope'}, ordered=False)
add('full-collaborator-owner-scope', 'full_collaborator', q(field='name', perpage=5), 200, page(NAMES[:5], 1, 5, len(LIVE)))
add('admin-impersonating-owner', 'admin', q(field='name', perpage=5), 200, page(NAMES[:5], 1, 5, len(LIVE)),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', q(), 200, page(['estero'], 1, 10))
add('owner-without-association-server-error', 'owner_without_association', q(), 500, SERVER)
add('athlete-forbidden-empty', 'athlete', q(), 403, '<empty>')
add('owner-scoped-athlete-denied', 'owner', q(), 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', q(), 403, {'detail': 'Missing permission: bookeeping.management.suppliers.read'})
add('admin-without-session', 'admin', q(), 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', q(), 401, {'detail': 'Authentication credentials were not provided.'})
add('list-wrong-method', 'owner', '/supplier/list', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('list-options', 'owner', '/supplier/list', 200, {'name': 'Supplier List', 'description': 'API endpoint to return a list of suppliers',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

info = lambda key: f'/supplier/{SUPPLIERS[key]["id"] if key in SUPPLIERS else key}/info'
NOT_FOUND = {'msg': 'Supplier not found.'}
add('info-owner', 'owner', info('beta'), 200, {'data': serialized('beta')})
add('info-full-collaborator', 'full_collaborator', info('grouped'), 200, {'data': serialized('grouped')})
add('info-foreign-not-found', 'owner', info('estero'), 404, NOT_FOUND)
add('info-deleted-not-found', 'owner', info('gone'), 404, NOT_FOUND)
add('info-missing-not-found', 'owner', info(IDS['missing']), 404, NOT_FOUND)
add('info-other-group-not-found', 'owner', info('beta'), 404, NOT_FOUND, headers={'X-Group-Id': IDS['own_group']})
# is_valid_uuid runs before the role check.
add('info-malformed', 'athlete', info('abc'), 400, ['badly formed hexadecimal UUID string'])
add('info-athlete-forbidden-empty', 'athlete', info('beta'), 403, '<empty>')
add('info-owner-without-association-server-error', 'owner_without_association', info('beta'), 500, SERVER)
add('info-restricted-missing-permission', 'restricted_collaborator', info('beta'), 403,
    {'detail': 'Missing permission: bookeeping.management.suppliers.read'})
add('info-anonymous', 'anonymous', info('beta'), 401, {'detail': 'Authentication credentials were not provided.'})
add('info-wrong-method', 'owner', info('beta'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('info-options', 'owner', info('beta'), 200, {'name': 'Supplier Info',
    'description': 'API endpoint to get detailed information about a specific supplier', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Supplier list and info authored sequence', 'ids': IDS, 'suppliers': SUPPLIERS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
