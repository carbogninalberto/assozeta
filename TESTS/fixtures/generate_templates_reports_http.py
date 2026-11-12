"""Author the templates/<pk>/update and saved-reports/<pk>/run HTTP sequence (templates-reports.md).

templates/<pk>/update is SportAssociationModuleTemplatesViewSet.update: get_object
on the association queryset (bare 404 for an unparseable pk, model 404 otherwise),
then ModelSerializer(fields='__all__', partial=True) and save(). The primary key
is read-only: a key in the body is ignored, the row the path names is saved in
place and the response shows that row; the related sport_association goes through
the tenant scope (another tenant is DRF's generic 403). Audit: changed fields
only, no entry when nothing changed.

saved-reports/<pk>/run is SavedReportViewSet.run: the report is resolved by id,
association and creator; the stored params are merged with request.data
['overrides'] outside the try (TypeError: HTML 500) and passed as keyword
arguments with the association id to the MCP tool function. Any exception
inside the tool is the JSON 500; an 'error' key is a 400; otherwise
{'data': result}. The query engine (QueryBuilder) scopes by the tenant and
soft-delete filters, applies .distinct(), validates fields (unknown ones are
skipped, blacklisted ones dropped), coerces string filter values for
date/datetime/integer/decimal fields, and serializes values (UUID str,
isoformat, Decimal as float, html.unescape on text with '&', Italian choice
labels). Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('templates_reports_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{930001 + n:012d}' for n in range(400))
SERVER = '<stable-server-error-page>'
FAILED = {'error': "Errore durante l'esecuzione del report."}


def template(association, name, *, body='<p>Corpo</p>', footer=True, header=True, custom=False, custom_footer=None, custom_header=None):
    return {'id': next(ids), 'association': association, 'name': name, 'template': body, 'footer': footer, 'header': header,
            'custom_footer_header': custom, 'custom_footer': custom_footer, 'custom_header': custom_header}


TEMPLATES = {
    'main': template(OWN, 'Modulo base', custom_header='testa'),
    'form': template(OWN, 'Modulo form', body='x', custom_footer='piede'),
    'source': template(OWN, 'Origine', body='<p>o</p>', footer=False),
    'remove': template(OWN, 'Da eliminare', custom_footer='f'),
    'foreign': template(FOREIGN, 'Estero'),
}
TAGS = {
    'calcio': {'id': next(ids), 'association': OWN, 'name': 'Calcio &amp; Basket', 'created': '2026-09-01T10:00:00+00:00'},
    'nuoto': {'id': next(ids), 'association': OWN, 'name': 'Nuoto', 'created': '2026-09-15T08:30:00.125000+00:00'},
    'agonistico': {'id': next(ids), 'association': OWN, 'name': 'nuoto agonistico', 'created': '2026-10-01T00:00:00+00:00'},
    'foreign': {'id': next(ids), 'association': FOREIGN, 'name': 'Nuoto estero', 'created': '2026-09-20T00:00:00+00:00'},
}


def category(association, name, kind, *, expense=False, deductible=False, archived=False, deleted=False, created):
    return {'id': next(ids), 'association': association, 'name': name, 'type': kind, 'expense': expense, 'tax_deductible': deductible,
            'archived': archived, 'deleted': deleted, 'created': created}


CATEGORIES = {
    'quote': category(OWN, 'Quote', 1, deductible=True, created='2026-01-10T09:00:00+00:00'),
    'affitto': category(OWN, 'Affitto', 2, expense=True, deleted=True, created='2026-02-01T09:00:00+00:00'),
    'vecchia': category(OWN, 'Vecchia', 1, archived=True, created='2025-05-01T09:00:00+00:00'),
    'sponsor': category(OWN, 'Sponsor', 3, created='2026-03-01T09:00:00+00:00'),
    'eventi': category(OWN, 'Eventi', 1, expense=True, created='2026-04-01T09:00:00+00:00'),
    'foreign': category(FOREIGN, 'Estera', 1, created='2026-01-01T09:00:00+00:00'),
}


def carnet(association, title, fee, lessons, *, description=None, public=True, meta=None, created):
    return {'id': next(ids), 'association': association, 'title': title, 'description': description, 'fee': fee, 'lessons': lessons,
            'public': public, 'meta': meta, 'created': created}


CARNETS = {
    'dieci': carnet(OWN, 'Dieci ingressi', '80.00', 10, description='Pacchetto', meta={'colore': 'blu'}, created='2026-05-01T10:00:00+00:00'),
    'cinque': carnet(OWN, 'Cinque', '45.50', 5, public=False, created='2026-06-01T10:00:00+00:00'),
    'vuoto': carnet(OWN, None, '0.00', 0, created='2026-07-01T10:00:00+00:00'),
    'foreign': carnet(FOREIGN, 'Estero', '99.00', 3, created='2026-06-15T10:00:00+00:00'),
}

# Attendance data of get_attendance_matrix: people, courses, registries and days.
PEOPLE = {key: {'id': next(ids), 'subscription': next(ids), 'first': first, 'last': last} for key, (first, last) in {
    'rossi': ('Mario', 'Rossi'), 'bianchi': ('Anna', 'Bianchi'), 'verdi': ('Luca', 'verdi'), 'deluca': ('Ugo', 'de Luca'),
    'acca': ('Zoe', 'ÀCCA')}.items()}
# A second group of the association: three courses whose sheet names collide (two titles equal in their first 31
# characters, and one equal to the numbered name of the second), with texts shaped like ISO dates and times.
GROUPS = {'gym': {'id': next(ids), 'association': OWN, 'name': 'Palestra'}}
GYM = GROUPS['gym']['id']
LONG_COURSE = 'Ginnastica dolce per gli adulti'
assert len(LONG_COURSE) == 31
COURSES = {key: {'id': next(ids), 'association': association, 'title': title, 'deleted': deleted, 'group': group,
                 'description': description, 'calendar': calendar}
           for key, (association, title, deleted, group, description, calendar) in {
    'nuoto': (OWN, 'Nuoto', False, None, None, None), 'calcio': (OWN, 'Calcio a 5', False, IDS['own_group'], None, None),
    'baby': (OWN, 'Nuoto baby', False, None, None, None), 'tennis': (OWN, 'Tennis', False, None, None, None),
    'old': (OWN, 'Vecchio corso', True, None, None, None), 'foreign': (FOREIGN, 'Nuoto estero', False, None, None, None),
    'gx': (OWN, LONG_COURSE[:27] + ' (2)', False, GYM, '2026-W03-4T10:30:00', '2026-01-15T10:30:00,5+01:00'),
    'ga': (OWN, LONG_COURSE + ' A', False, GYM, '2026-01-15T10:30:00.+01:00', '2026-01-15T10300012'),
    'gb': (OWN, LONG_COURSE + ' B', False, GYM, '2026-02-30T10:30:00', '2026-01-15T10:30:00Z'),
    # A course without a title.
    'senza': (OWN, None, False, None, None, None)}.items()}
ENROLLED = {key: {'id': next(ids), 'course': course, 'person': person, 'deleted': deleted} for key, (course, person, deleted) in {
    'n_rossi': ('nuoto', 'rossi', False), 'n_bianchi': ('nuoto', 'bianchi', False), 'n_verdi': ('nuoto', 'verdi', False),
    'n_acca': ('nuoto', 'acca', True), 'n_none': ('nuoto', None, False), 'c_rossi': ('calcio', 'rossi', False),
    'c_deluca': ('calcio', 'deluca', False), 'c_acca': ('calcio', 'acca', False), 'b_rossi': ('baby', 'rossi', False),
    'o_rossi': ('old', 'rossi', False), 'f_rossi': ('foreign', 'rossi', False), 'x_rossi': ('gx', 'rossi', False),
    'x_bianchi': ('gx', 'bianchi', False), 'a_verdi': ('ga', 'verdi', False), 'b_deluca': ('gb', 'deluca', False),
    's_bianchi': ('senza', 'bianchi', False)}.items()}
E = {key: value['id'] for key, value in ENROLLED.items()}
REGISTRIES = {key: {'id': next(ids), 'course': key} for key in ('nuoto', 'calcio', 'baby', 'old', 'foreign', 'gx', 'ga', 'gb', 'senza')}
DAYS = {key: {'id': next(ids), 'registry': registry, 'date': date, 'attendees': attendees} for key, (registry, date, attendees) in {
    'n1': ('nuoto', '2026-09-01T18:00:00+00:00', [{'course_subscription_id': E['n_rossi']}, {'course_subscription_id': E['n_verdi']}]),
    'n2': ('nuoto', '2026-09-08T23:30:00+00:00', [E['n_bianchi'], E['n_acca']]),
    'n3': ('nuoto', '2026-09-15T18:00:00+00:00', None),
    'c1': ('calcio', '2026-09-01T17:00:00+00:00', [{'course_subscription_id': E['c_rossi']}, {'course_subscription_id': E['c_deluca']}]),
    'c2': ('calcio', '2026-10-06T17:00:00+00:00', [{'course_subscription_id': E['c_acca']}, {'other': 1}]),
    'o1': ('old', '2026-09-01T18:00:00+00:00', []), 'f1': ('foreign', '2026-09-01T18:00:00+00:00', []),
    'x1': ('gx', '2026-11-03T18:00:00+00:00', [{'course_subscription_id': E['x_rossi']}]),
    'x2': ('gx', '2026-11-10T18:00:00+00:00', [{'course_subscription_id': E['x_rossi']}, {'course_subscription_id': E['x_bianchi']}]),
    'a1': ('ga', '2026-11-04T18:00:00+00:00', [{'course_subscription_id': E['a_verdi']}]),
    'b1': ('gb', '2026-11-05T18:00:00+00:00', []),
    's1': ('senza', '2026-11-06T18:00:00+00:00', [{'course_subscription_id': E['s_bianchi']}])}.items()}

REPORTS = {}


def report(label, tool, params, *, association=OWN, creator='owner'):
    REPORTS[label] = {'id': next(ids), 'association': association, 'creator': creator, 'name': f'Report {label}', 'description': '',
                      'tool': tool, 'params': params, 'ui': {}, 'created': '2026-10-01T08:00:00+00:00', 'updated': '2026-10-02T08:00:00+00:00'}
    return REPORTS[label]['id']


cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, effect=None, headers=None):
    case = {'name': f'templates-reports-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect or {'kind': 'none'}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# ---------------------------------------------------------------------------
# templates/<pk>/update
# ---------------------------------------------------------------------------

FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')
UPDATE = lambda key: f'/templates/{TEMPLATES[key]["id"] if key in TEMPLATES else key}/update'
state = {key: dict(value) for key, value in TEMPLATES.items()}


def serialized(row):
    return {'sport_association_module_templates_id': row['id'], 'sport_association': row['association'], **{f: row[f] for f in FIELDS}}


def text(value):
    return 'None' if value is None else str(value)


def updated(label, actor, values):
    """The expected row after the save and auditlog's changed-field entry (None: no entry)."""
    before = state[label]
    after = {**before, **values}
    changes = {f: [text(before[f]), text(after[f])] for f in FIELDS if before[f] != after[f]}
    state[label] = after
    return {'kind': 'template-update', 'label': label, 'id': before['id'], 'row': after, 'actor': actor, 'changes': changes or None}


# Partial validation; trimmed name, raw None, boolean strings, numbers as text.
effect = updated('main', 'owner', {'name': 'Modulo aggiornato è', 'template': None, 'footer': False, 'custom_footer': '7'})
add('update-owner', 'owner', 'PATCH', UPDATE('main'), 200, serialized(effect['row']), effect=effect,
    body={'name': '  Modulo aggiornato è ', 'template': None, 'footer': 'no', 'custom_footer': 7, 'header': True, 'ignored': 1,
          'sport_association': OWN})
# Form input: an empty value of a nullable non-blank text field is None; absent booleans stay (partial).
effect = updated('form', 'full_collaborator', {'name': 'Form nuovo', 'custom_footer': None, 'header': False, 'custom_footer_header': True})
add('update-form-full-collaborator', 'full_collaborator', 'PATCH', UPDATE('form'), 200, serialized(effect['row']), effect=effect,
    raw_body='custom_footer=&header=false&custom_footer_header=true&name=Form+nuovo', content_type='application/x-www-form-urlencoded')
effect = updated('main', 'owner', {})
add('update-own-id-ignored-no-audit', 'owner', 'PATCH', UPDATE('main'), 200, serialized(effect['row']), effect=effect,
    body={'sport_association_module_templates_id': TEMPLATES['main']['id'], 'sport_association': OWN})
# The primary key is read-only: a key in the body (an unused one, another row's) is ignored and the template the
# path names is updated in place, with at most its own changed-field audit entry.
UNUSED = next(ids)
effect = updated('source', 'owner', {'header': False})
add('update-new-id-ignored', 'owner', 'PATCH', UPDATE('source'), 200, serialized(effect['row']), effect=effect,
    body={'sport_association_module_templates_id': UNUSED, 'header': False})
effect = updated('main', 'owner', {})
add('update-other-row-id-ignored', 'owner', 'PATCH', UPDATE('main'), 200, serialized(effect['row']), effect=effect,
    body={'sport_association_module_templates_id': TEMPLATES['form']['id']})
add('update-foreign-association-denied', 'owner', 'PATCH', UPDATE('main'), 403, {'detail': 'You do not have permission to perform this action.'},
    body={'sport_association': FOREIGN, 'name': ''})
add('update-unknown-association', 'owner', 'PATCH', UPDATE('main'), 400,
    {'sport_association': [f'Invalid pk "{IDS["missing"]}" - object does not exist.']}, body={'sport_association': IDS['missing']})
add('update-invalid-fields', 'owner', 'PATCH', UPDATE('main'), 400,
    {'name': ['This field may not be blank.'], 'template': ['This field may not be blank.'], 'footer': ['This field may not be null.'],
     'custom_header': ['Not a valid string.'], 'sport_association': ['“nope” is not a valid UUID.'],
     'header': ['Must be a valid boolean.']},
    body={'name': '', 'template': '  ', 'footer': None, 'custom_header': True, 'sport_association': 'nope',
          'sport_association_module_templates_id': 'bad', 'header': 'maybe'})
add('update-null-values', 'owner', 'PATCH', UPDATE('main'), 400,
    {'name': ['This field may not be null.'], 'sport_association': ['This field may not be null.']},
    body={'name': None, 'sport_association': None, 'sport_association_module_templates_id': None, 'custom_footer': None})
add('update-too-long-name', 'owner', 'PATCH', UPDATE('main'), 400, {'name': ['Ensure this field has no more than 150 characters.']},
    body={'name': 'x' * 151})
add('update-list-body', 'owner', 'PATCH', UPDATE('main'), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    body=['name'])
add('update-malformed-pk', 'owner', 'PATCH', UPDATE('not-a-uuid'), 404, {'detail': 'Not found.'}, body={'name': 'x'})
NOT_FOUND = {'detail': 'No SportAssociationModuleTemplates matches the given query.'}
add('update-foreign-row', 'owner', 'PATCH', UPDATE('foreign'), 404, NOT_FOUND, body={'name': 'x'})
add('update-missing-row', 'owner', 'PATCH', UPDATE(IDS['missing']), 404, NOT_FOUND, body={'name': 'x'})
add('update-restricted-missing-permission', 'restricted_collaborator', 'PATCH', UPDATE('main'), 403,
    {'detail': 'Missing permission: association.templates.update'}, body={'name': 'x'})
add('update-athlete-server-error', 'athlete', 'PATCH', UPDATE('main'), 500, SERVER, body={'name': 'x'})
add('update-deleted-association-server-error', 'deleted_association_owner', 'PATCH', UPDATE('main'), 500, SERVER, body={'name': 'x'})
add('update-anonymous', 'anonymous', 'PATCH', UPDATE('main'), 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('update-admin-without-session', 'admin', 'PATCH', UPDATE('main'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('update-wrong-method', 'owner', 'PUT', UPDATE('main'), 405, {'detail': 'Method "PUT" not allowed.'}, body={})
add('update-options', 'owner', 'OPTIONS', UPDATE('main'), 200, {'name': 'Sport Association Module Templates', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
# The viewset's delete() answers DELETE on the update route with its pk.
add('update-delete-method-deletes', 'owner', 'DELETE', UPDATE('remove'), 204, '<empty>',
    effect={'kind': 'template-delete', 'label': 'remove', 'actor': 'owner'})

# ---------------------------------------------------------------------------
# saved-reports/<pk>/run
# ---------------------------------------------------------------------------

RUN = lambda label: f'/saved-reports/{REPORTS[label]["id"] if label in REPORTS else label}/run'
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
T, C, P = TAGS, CARNETS, CATEGORIES
tag_row = lambda key: {'tag_id': T[key]['id'], 'tag_name': T[key]['name'].replace('&amp;', '&'), 'creation_date': T[key]['created']}
listing = lambda count, data, more=False: {'data': {'count': count, 'has_more': more, 'data': data}}

report('count', 'count_data', {'model_name': 'Tags'})
report('collaborator', 'count_data', {'model_name': 'Tags'}, creator='full_collaborator')
report('foreign', 'count_data', {'model_name': 'Tags'}, association=FOREIGN, creator='foreign_owner')
report('unknown', 'nope', {'model_name': 'Tags'})
report('list_params', 'count_data', [1])
report('remove', 'count_data', {'model_name': 'Tags'})

add('run-owner-count', 'owner', 'POST', RUN('count'), 200, {'data': {'count': 3}}, body={})
add('run-full-collaborator-owner-scope', 'full_collaborator', 'POST', RUN('count'), 200, {'data': {'count': 3}}, body={})
add('run-admin-impersonating-owner', 'admin', 'POST', RUN('count'), 200, {'data': {'count': 3}}, body={},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('run-foreign-owner-own-tags', 'foreign_owner', 'POST', RUN('foreign'), 200, {'data': {'count': 1}}, body={})
add('run-foreign-not-found', 'owner', 'POST', RUN('foreign'), 404, {'error': 'Report not found'}, body={})
add('run-collaborator-created-not-found', 'owner', 'POST', RUN('collaborator'), 404, {'error': 'Report not found'}, body={})
add('run-missing-not-found', 'owner', 'POST', RUN(IDS['missing']), 404, {'error': 'Report not found'}, body={})
add('run-malformed-pk-server-error', 'owner', 'POST', RUN('abc'), 500, SERVER, body={})
add('run-restricted-unmapped', 'restricted_collaborator', 'POST', RUN('count'), 403, unmapped(RUN('count')), body={})
add('run-athlete-server-error', 'athlete', 'POST', RUN('count'), 500, SERVER, body={})
add('run-anonymous', 'anonymous', 'POST', RUN('count'), 401, {'detail': 'Authentication credentials were not provided.'}, body={})
add('run-admin-without-session', 'admin', 'POST', RUN('count'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('run-wrong-method', 'owner', 'GET', RUN('count'), 405, {'detail': 'Method "GET" not allowed.'})
add('run-options', 'owner', 'OPTIONS', RUN('count'), 200, {'name': 'Saved Report', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
add('run-unknown-tool', 'owner', 'POST', RUN('unknown'), 400, {'error': 'Unknown tool: nope'}, body={})
# Merging happens outside the try: non-mapping values raise (HTML 500).
add('run-list-body-server-error', 'owner', 'POST', RUN('count'), 500, SERVER, body=[])
add('run-list-overrides-server-error', 'owner', 'POST', RUN('count'), 500, SERVER, body={'overrides': [1]})
add('run-null-overrides-server-error', 'owner', 'POST', RUN('count'), 500, SERVER, body={'overrides': None})
add('run-stored-list-params-server-error', 'owner', 'POST', RUN('list_params'), 500, SERVER, body={})
add('run-form-overrides-server-error', 'owner', 'POST', RUN('count'), 500, SERVER, raw_body='overrides=x',
    content_type='application/x-www-form-urlencoded')
add('run-form-without-overrides', 'owner', 'POST', RUN('count'), 200, {'data': {'count': 3}}, raw_body='other=1',
    content_type='application/x-www-form-urlencoded')
add('run-override-association-id-error', 'owner', 'POST', RUN('count'), 500, FAILED, body={'overrides': {'sport_association_id': OWN}})
add('run-override-model-not-whitelisted-error', 'owner', 'POST', RUN('count'), 500, FAILED, body={'overrides': {'model_name': 'User'}})
add('run-override-model-number-error', 'owner', 'POST', RUN('count'), 500, FAILED, body={'overrides': {'model_name': 5}})


def run(name, tool, params, status, response, *, overrides=None, actor='owner', group=None):
    label = name.replace('-', '_')
    report(label, tool, params)
    add(f'run-{name}', actor, 'POST', RUN(label), status, response, body={} if overrides is None else {'overrides': overrides},
        headers=None if group is None else {'X-Group-Id': group})


# query_data
run('query-missing-model-error', 'query_data', {'fields': ['tag_name']}, 500, FAILED)
run('query-tags-default-fields', 'query_data', {'model_name': 'Tags', 'order_by': ['tag_name']}, 200,
    listing(3, [tag_row('calcio'), tag_row('nuoto'), tag_row('agonistico')]))
run('query-categories-limit-offset', 'query_data', {'model_name': 'application.PaymentCategory', 'fields': ['name', 'type', 'password'],
    'order_by': ['-name'], 'limit': 2, 'offset': 1}, 200,
    listing(4, [{'name': 'Quote', 'type': 'Istituzionale'}, {'name': 'Eventi', 'type': 'Istituzionale'}], True))
# exclude() on a nullable column keeps NULL rows (Django adds IS NOT NULL inside the NOT).
run('query-carnets-exclude-nullable', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'lessons_number', 'fee'],
    'filters': {'lessons_number__gte': '0'}, 'exclude': {'title': 'Cinque'}, 'order_by': ['lessons_number']}, 200,
    listing(2, [{'title': None, 'lessons_number': 0, 'fee': 0.0}, {'title': 'Dieci ingressi', 'lessons_number': 10, 'fee': 80.0}]))
# No valid field: values() with every concrete column (attnames).
run('query-carnets-all-columns', 'query_data', {'model_name': 'Carnet', 'fields': ['bogus'], 'filters': {'public': False}}, 200,
    listing(1, [{'carnet_id': C['cinque']['id'], 'title': 'Cinque', 'description': None, 'user_id_id': None, 'sport_association_id': OWN,
                 'lessons_number': 5, 'fee': 45.5, 'creation_date': C['cinque']['created'], 'public': False, 'meta': None}]))
run('query-carnets-json-column', 'query_data', {'model_name': 'Carnet', 'fields': ['meta', 'description'], 'filters': {'lessons_number': 10}},
    200, listing(1, [{'meta': {'colore': 'blu'}, 'description': 'Pacchetto'}]))
run('query-categories-or-filters', 'query_data', {'model_name': 'PaymentCategory', 'fields': ['name'],
    'or_filters': [{'name__istartswith': 'q'}, {'type': '2'}, 'skip', {}], 'order_by': ['name']}, 200,
    listing(2, [{'name': 'Affitto'}, {'name': 'Quote'}]))
# .distinct() on values(): duplicates collapse; ordering columns join the DISTINCT.
run('query-categories-distinct-type', 'query_data', {'model_name': 'PaymentCategory', 'fields': ['type'], 'filters': {'type': 1}}, 200,
    listing(2, [{'type': 'Istituzionale'}]))
run('query-categories-distinct-with-order', 'query_data', {'model_name': 'PaymentCategory', 'fields': ['type'], 'order_by': ['name']}, 200,
    listing(4, [{'type': 'Commerciale'}, {'type': 'Istituzionale'}, {'type': 'Istituzionale'}, {'type': 3}]))
run('query-tags-overrides', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'limit': 5}, 200,
    listing(2, [{'tag_name': 'Nuoto'}], True), overrides={'filters': {'tag_name__icontains': 'NUOTO'}, 'limit': 1})
run('query-tags-datetime-filters', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__gte': '2026-09-15', 'creation_date__lt': '2026-10-01T00:00:00'}}, 200, listing(1, [{'tag_name': 'Nuoto'}]))
run('query-tags-empty-in', 'query_data', {'model_name': 'Tags', 'filters': {'tag_id__in': []}}, 200, listing(0, []))
run('query-carnets-isnull-range-decimal', 'query_data', {'model_name': 'Carnet', 'fields': ['lessons_number'],
    'filters': {'title__isnull': False, 'lessons_number__range': [1, 10], 'fee__gt': 50}, 'order_by': ['-fee']}, 200,
    listing(1, [{'lessons_number': 10}]))
run('query-tags-blacklisted-filter-dropped', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'password': 'x'},
    'order_by': ['-tag_name']}, 200, listing(3, [{'tag_name': 'nuoto agonistico'}, {'tag_name': 'Nuoto'}, {'tag_name': 'Calcio & Basket'}]))
run('query-tags-in-uuid', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'tag_id__in': [T['nuoto']['id'], T['foreign']['id']]},
    'exclude': {'tag_name__endswith': 'x'}}, 200, listing(1, [{'tag_name': 'Nuoto'}]))
run('query-unknown-filter-error', 'query_data', {'model_name': 'Tags', 'filters': {'colour': 'x'}}, 500, FAILED)
run('query-invalid-uuid-error', 'query_data', {'model_name': 'Tags', 'filters': {'tag_id': 'nope'}}, 500, FAILED)
run('query-negative-limit-error', 'query_data', {'model_name': 'Tags', 'limit': -1}, 500, FAILED)
run('query-unknown-order-error', 'query_data', {'model_name': 'Tags', 'order_by': ['colour']}, 500, FAILED)
run('query-filters-list-error', 'query_data', {'model_name': 'Tags', 'filters': ['tag_name']}, 500, FAILED)
# count_data
run('count-filters-exclude', 'count_data', {'model_name': 'PaymentCategory', 'filters': {'expense': True}, 'exclude': {'deleted': True}}, 200,
    {'data': {'count': 1}})
run('count-include-archived', 'count_data', {'model_name': 'PaymentCategory', 'include_archived': 1}, 200, {'data': {'count': 5}})
# get_field_values
run('values-category-type-labels', 'get_field_values', {'model_name': 'PaymentCategory', 'field_name': 'type'}, 200,
    {'data': {'values': ['Istituzionale', 'Commerciale', 3], 'total_distinct': 3}})
run('values-carnet-title-limit', 'get_field_values', {'model_name': 'Carnet', 'field_name': 'title', 'limit': 2}, 200,
    {'data': {'values': ['Cinque', 'Dieci ingressi'], 'total_distinct': 3}})
run('values-carnet-title-nulls-last', 'get_field_values', {'model_name': 'Carnet', 'field_name': 'title', 'limit': None}, 200,
    {'data': {'values': ['Cinque', 'Dieci ingressi', None], 'total_distinct': 3}})
run('values-tags-unescape', 'get_field_values', {'model_name': 'Tags', 'field_name': 'tag_name', 'filters': {'tag_name__contains': '&'}}, 200,
    {'data': {'values': ['Calcio & Basket'], 'total_distinct': 1}})
run('values-blacklisted-error', 'get_field_values', {'model_name': 'Tags', 'field_name': 'password'}, 500, FAILED)
run('values-missing-field-error', 'get_field_values', {'model_name': 'Tags'}, 500, FAILED)
# aggregate_data
run('aggregate-none', 'aggregate_data', {'model_name': 'Carnet'}, 400, {'error': 'No aggregations specified'})
run('aggregate-invalid', 'aggregate_data', {'model_name': 'Carnet', 'aggregations': [{'function': 'median', 'field': 'fee'},
    {'function': 'sum', 'field': 'password'}]}, 400, {'error': 'No valid aggregations'})
run('aggregate-flat', 'aggregate_data', {'model_name': 'Carnet', 'aggregations': [
    {'function': 'count', 'field': '*'}, {'function': 'sum', 'field': 'fee'}, {'function': 'avg', 'field': 'lessons_number', 'alias': 'media.lezioni'},
    {'function': 'max', 'field': 'creation_date'}, {'function': 'MIN', 'field': 'title'}, {'function': 'avg', 'field': 'fee', 'alias': 'media-quota'}]},
    200, {'data': {'count_*': 3, 'sum_fee': 125.5, 'media_lezioni': 5.0, 'max_creation_date': C['vuoto']['created'], 'min_title': 'Cinque',
                   'media_quota': 41.833333333333336}})
run('aggregate-group-type', 'aggregate_data', {'model_name': 'PaymentCategory', 'aggregations': [{'function': 'count', 'field': '*', 'alias': 'totale'},
    {'function': 'max', 'field': 'name'}], 'group_by': ['type']}, 200,
    {'data': {'data': [{'type': 'Istituzionale', 'totale': 2, 'max_name': 'Quote'}, {'type': 'Commerciale', 'totale': 1, 'max_name': 'Affitto'},
                       {'type': 3, 'totale': 1, 'max_name': 'Sponsor'}], 'count': 3}})
run('aggregate-group-invalid', 'aggregate_data', {'model_name': 'PaymentCategory', 'aggregations': [{'function': 'count', 'field': '*'}],
    'group_by': ['bogus', 'password']}, 400, {'error': 'No valid group_by fields'})
run('aggregate-sum-text-error', 'aggregate_data', {'model_name': 'Tags', 'aggregations': [{'function': 'sum', 'field': 'tag_name'}]}, 500, FAILED)
run('aggregate-empty-set', 'aggregate_data', {'model_name': 'Carnet', 'filters': {'carnet_id__in': []},
    'aggregations': [{'function': 'sum', 'field': 'fee'}, {'function': 'count', 'field': '*'}]}, 200, {'data': {'sum_fee': None, 'count_*': 0}})
run('aggregate-group-boolean-filtered', 'aggregate_data', {'model_name': 'Carnet', 'exclude': {'lessons_number': 0},
    'aggregations': [{'function': 'sum', 'field': 'lessons_number', 'alias': 'lezioni'}], 'group_by': ['public']}, 200,
    {'data': {'data': [{'public': False, 'lezioni': 5}, {'public': True, 'lezioni': 10}], 'count': 2}})
# The default managers of the group-aware models (Course here) follow the request's X-Group-Id.
COURSE_TITLES = sorted(c['title'] for c in COURSES.values() if c['association'] == OWN and not c['deleted'] and c['title'])
GYM_TITLES = sorted(c['title'] for c in COURSES.values() if c['group'] == GYM)
COURSE_QUERY = {'model_name': 'Course', 'fields': ['title'], 'order_by': ['title']}
run('query-courses-without-group', 'query_data', COURSE_QUERY, 200, listing(8, [{'title': t} for t in COURSE_TITLES] + [{'title': None}]))
run('query-courses-group-header', 'query_data', COURSE_QUERY, 200, listing(1, [{'title': 'Calcio a 5'}]), group=IDS['own_group'])
run('query-courses-second-group-header', 'query_data', COURSE_QUERY, 200, listing(3, [{'title': t} for t in GYM_TITLES]), group=GYM)
run('count-courses-group-header-archived', 'count_data', {'model_name': 'Course', 'include_archived': True}, 200, {'data': {'count': 1}},
    group=IDS['own_group'])
run('count-courses-group-header-other-spelling', 'count_data', {'model_name': 'Course'}, 200, {'data': {'count': 3}}, group=GYM.upper())
run('count-courses-group-header-no-rows', 'count_data', {'model_name': 'Course'}, 200, {'data': {'count': 0}}, group=IDS['missing'])
# A header value that is not a UUID is no filter.
run('count-courses-group-header-not-uuid', 'count_data', {'model_name': 'Course'}, 200, {'data': {'count': 8}}, group='nope')
# Tags has no group: the header changes nothing.
run('count-tags-group-header', 'count_data', {'model_name': 'Tags'}, 200, {'data': {'count': 3}}, group=GYM)
run('count-associates-group-header', 'count_data', {'model_name': 'Associate'}, 200, {'data': {'count': 0}}, group=GYM)
run('values-courses-group-header', 'get_field_values', {'model_name': 'Course', 'field_name': 'title'}, 200,
    {'data': {'values': GYM_TITLES, 'total_distinct': 3}}, group=GYM)
run('aggregate-courses-group-header', 'aggregate_data', {'model_name': 'Course', 'aggregations': [{'function': 'count', 'field': '*'}]},
    200, {'data': {'count_*': 3}}, group=GYM)
# get_schema
run('schema-unknown-model', 'get_schema', {'model_name': 'Nope'}, 400, {'error': 'Model Nope not found'})

# ---------------------------------------------------------------------------
# The file tools: export_data, export_multi_sheet, get_attendance_matrix
# ---------------------------------------------------------------------------
import sys  # noqa: E402
from email.header import Header  # noqa: E402

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def disposition(filename):
    """Django's header value: ISO-8859-1 when it fits, else one MIME word."""
    value = f'attachment; filename="{filename}"'
    try:
        value.encode('latin-1')
    except UnicodeEncodeError:
        return Header(value, 'utf-8', maxlinelen=sys.maxsize).encode()
    return value


def download(filename, kind, **content):
    return {'$file': {'type': kind, 'disposition': disposition(filename), **content}}


I = lambda n: {'int': str(n)}
F = lambda x: {'float': repr(float(x))}

run('export-missing-model-error', 'export_data', {'format': 'csv'}, 500, FAILED)
run('export-unsupported-format', 'export_data', {'model_name': 'Tags', 'format': 'doc'}, 400,
    {'error': 'Unsupported format: doc. Use: csv, xlsx, pdf'})
run('export-no-data', 'export_data', {'model_name': 'Tags', 'filters': {'tag_name': 'zzz'}, 'format': 'pdf'}, 400,
    {'error': 'No data to export', 'count': 0})
run('export-unknown-filter-error', 'export_data', {'model_name': 'Tags', 'filters': {'colour': 'x'}}, 500, FAILED)
run('export-csv-tags', 'export_data', {'model_name': 'Tags', 'fields': ['tag_name', 'creation_date'], 'order_by': ['tag_name'], 'offset': 1},
    200, download('Tags.csv', 'text/csv; charset=utf-8', csv='tag_name,creation_date\r\nCalcio & Basket,01/09/2026 10:00\r\n'
                  'Nuoto,15/09/2026 08:30\r\nnuoto agonistico,01/10/2026 00:00\r\n'))
# Every allowed field: the identifier columns are left out of the file; labels and quoting.
run('export-csv-title-labels', 'export_data', {'model_name': 'Tags', 'title': 'Elenco: "tag" <1>', 'filters': {'tag_name__icontains': 'nuoto'},
    'order_by': ['-tag_name'], 'column_labels': {'tag_name': 'Nome, tag', 'creation_date': 'Creato "il"', 'altro': 'x'}}, 200,
    download('Elenco- -tag- -1.csv', 'text/csv; charset=utf-8',
             csv='"Nome, tag","Creato ""il"""\r\nnuoto agonistico,01/10/2026 00:00\r\nNuoto,15/09/2026 08:30\r\n'))
run('export-csv-numbers-booleans', 'export_data', {'model_name': 'Carnet', 'fields': ['title', 'lessons_number', 'fee', 'public', 'description'],
    'order_by': ['lessons_number']}, 200, download('Carnet.csv', 'text/csv; charset=utf-8',
    csv='title,lessons_number,fee,public,description\r\n,0,0.0,Vero,\r\nCinque,5,45.5,Falso,\r\nDieci ingressi,10,80.0,Vero,Pacchetto\r\n'))
CARNET_ROWS = [['', I(0), F(0.0), 'Vero', ''], ['Cinque', I(5), F(45.5), 'Falso', ''], ['Dieci ingressi', I(10), F(80.0), 'Vero', 'Pacchetto']]
run('export-xlsx-carnets', 'export_data', {'model_name': 'Carnet', 'format': 'xlsx',
    'fields': ['title', 'lessons_number', 'fee', 'public', 'description'], 'order_by': ['lessons_number']}, 200,
    download('Carnet.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Carnet', 'columns': ['title', 'lessons_number', 'fee', 'public', 'description'], 'rows': CARNET_ROWS}]}))
LONG_TITLE = 'Carnet attivi 2026 con un titolo più lungo di trentuno'
run('export-xlsx-override-title-json', 'export_data', {'model_name': 'Carnet', 'title': LONG_TITLE, 'fields': ['title', 'meta'],
    'filters': {'lessons_number': 10}, 'column_labels': {'title': 'Titolo'}}, 200,
    download(LONG_TITLE + '.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': LONG_TITLE[:31], 'columns': ['Titolo', 'meta'], 'rows': [['Dieci ingressi', {'dict': [['colore', 'blu']]}]]}]}),
    overrides={'format': 'xlsx'})
run('export-xlsx-sheet-name-error', 'export_data', {'model_name': 'Tags', 'format': 'xlsx', 'title': 'a/b'}, 500, FAILED)
run('export-pdf-tags', 'export_data', {'model_name': 'Tags', 'format': 'pdf', 'fields': ['tag_name', 'creation_date'],
    'filters': {'tag_name__icontains': 'nuoto'}, 'order_by': ['tag_name']}, 200,
    download('Tags.pdf', 'application/pdf', pdf={'engine': 'report_table', 'headers': ['tag_name', 'creation_date'], 'title': 'Tags',
             'rows': [['Nuoto', '15/09/2026 08:30'], ['nuoto agonistico', '01/10/2026 00:00']]}))
run('export-pdf-categories-texts', 'export_data', {'model_name': 'PaymentCategory', 'format': 'pdf', 'title': 'Categorie €',
    'fields': ['name', 'type', 'expense', 'creation_date'], 'order_by': ['name'], 'column_labels': {'name': 'Nome', 'type': 'Tipo'},
    'header_text': 'Intestazione', 'footer_text': 'ASD Aurora', 'text_before': 'Elenco delle categorie.', 'text_after': 'Fine.'}, 200,
    download('Categorie €.pdf', 'application/pdf', pdf={'engine': 'report_table', 'headers': ['Nome', 'Tipo', 'expense', 'creation_date'],
             'title': 'Intestazione', 'footer_text': 'ASD Aurora', 'text_before': 'Elenco delle categorie.', 'text_after': 'Fine.',
             'rows': [['Affitto', 'Commerciale', 'Vero', '01/02/2026 09:00'], ['Eventi', 'Istituzionale', 'Vero', '01/04/2026 09:00'],
                      ['Quote', 'Istituzionale', 'Falso', '10/01/2026 09:00'], ['Sponsor', '3', 'Falso', '01/03/2026 09:00']]}))
run('export-pdf-numbers', 'export_data', {'model_name': 'Carnet', 'format': 'pdf', 'fields': ['title', 'lessons_number', 'fee', 'public', 'description'],
    'order_by': ['lessons_number']}, 200, download('Carnet.pdf', 'application/pdf', pdf={
        'engine': 'report_table', 'headers': ['title', 'lessons_number', 'fee', 'public', 'description'], 'title': 'Carnet',
        'rows': [['', '0', '0.0', 'Vero', ''], ['Cinque', '5', '45.5', 'Falso', ''], ['Dieci ingressi', '10', '80.0', 'Vero', 'Pacchetto']]}))
# Texts longer than a page: the paragraphs continue on the next one.
LONG_TEXT = ' '.join(['il corso di nuoto agonistico per adulti si tiene nella piscina comunale ogni settimana'] * 110)
run('export-pdf-texts-over-pages', 'export_data', {'model_name': 'Tags', 'format': 'pdf', 'fields': ['tag_name'],
    'filters': {'tag_name__icontains': 'nuoto'}, 'order_by': ['tag_name'], 'text_before': LONG_TEXT, 'text_after': LONG_TEXT}, 200,
    download('Tags.pdf', 'application/pdf', pdf={'engine': 'report_table', 'headers': ['tag_name'], 'title': 'Tags',
             'rows': [['Nuoto'], ['nuoto agonistico']], 'text_before': LONG_TEXT, 'text_after': LONG_TEXT}))

# The queries of the file tools follow the group as well.
run('export-csv-courses-group-header', 'export_data', {'model_name': 'Course', 'fields': ['title'], 'order_by': ['title']}, 200,
    download('Course.csv', 'text/csv; charset=utf-8', csv='title\r\n' + ''.join(t + '\r\n' for t in GYM_TITLES)), group=GYM)
# Texts shaped like an ISO date and time: the ones datetime.fromisoformat reads are rewritten, the others kept.
run('export-csv-iso-texts', 'export_data', {'model_name': 'Course', 'fields': ['description', 'google_calendar_id'], 'order_by': ['title']},
    200, download('Course.csv', 'text/csv; charset=utf-8', csv='description,google_calendar_id\r\n15/01/2026 10:30,15/01/2026 10:30\r\n'
                  '2026-01-15T10:30:00.+01:00,15/01/2026 10:30\r\n2026-02-30T10:30:00,15/01/2026 10:30\r\n'), group=GYM)
# format: a value that is not a str is named in the error; a list is not hashable.
for label, value, shown in (('number', 5, '5'), ('null', None, 'None'), ('boolean', True, 'True'), ('float', 1.5, '1.5')):
    run(f'export-format-{label}', 'export_data', {'model_name': 'Tags'}, 400,
        {'error': f'Unsupported format: {shown}. Use: csv, xlsx, pdf'}, overrides={'format': value})
run('export-format-list-error', 'export_data', {'model_name': 'Tags', 'format': ['csv']}, 500, FAILED)
run('export-format-dict-error', 'export_data', {'model_name': 'Tags', 'format': {'csv': 1}}, 500, FAILED)
NUOTO = {'model_name': 'Tags', 'fields': ['tag_name', 'creation_date'], 'filters': {'tag_name': 'Nuoto'}}
run('export-title-number-error', 'export_data', {**NUOTO, 'title': 5}, 500, FAILED)
run('export-title-zero-model-name', 'export_data', {**NUOTO, 'title': 0}, 200,
    download('Tags.csv', 'text/csv; charset=utf-8', csv='tag_name,creation_date\r\nNuoto,15/09/2026 08:30\r\n'))
run('export-labels-list-error', 'export_data', {**NUOTO, 'column_labels': ['tag_name']}, 500, FAILED)
run('export-csv-label-values', 'export_data', {**NUOTO, 'column_labels': {'tag_name': ['a', 'b'], 'creation_date': None}}, 200,
    download('Tags.csv', 'text/csv; charset=utf-8', csv='"[\'a\', \'b\']",\r\nNuoto,15/09/2026 08:30\r\n'))
run('export-csv-label-numbers', 'export_data', {**NUOTO, 'column_labels': {'tag_name': 1.5, 'creation_date': True}}, 200,
    download('Tags.csv', 'text/csv; charset=utf-8', csv='1.5,True\r\nNuoto,15/09/2026 08:30\r\n'))
run('export-csv-json-cell', 'export_data', {'model_name': 'Carnet', 'fields': ['title', 'meta'], 'filters': {'lessons_number': 10}}, 200,
    download('Carnet.csv', 'text/csv; charset=utf-8', csv="Dieci ingressi,{'colore': 'blu'}\r\n".join(['title,meta\r\n', ''])))
# Only identifier columns: nothing is left to write.
ONLY_IDS = {'model_name': 'Tags', 'fields': ['tag_id']}
run('export-csv-no-columns', 'export_data', ONLY_IDS, 200, download('Tags.csv', 'text/csv; charset=utf-8', csv='\r\n' * 4))
run('export-csv-no-columns-labels-list', 'export_data', {**ONLY_IDS, 'column_labels': ['x']}, 200,
    download('Tags.csv', 'text/csv; charset=utf-8', csv='\r\n' * 4))
run('export-xlsx-no-columns', 'export_data', {**ONLY_IDS, 'format': 'xlsx'}, 200,
    download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [{'title': 'Tags', 'columns': [], 'rows': [[], [], []]}]}))
# Workbook labels of other types: numbers, null and booleans are written as such; a list raises.
run('export-xlsx-label-values', 'export_data', {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': 5, 'creation_date': None}}, 200,
    download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [I(5), None], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
run('export-xlsx-label-float-boolean', 'export_data', {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': 1.5, 'creation_date': True}},
    200, download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [F(1.5), {'bool': True}], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
# Labels that are equal numbers name the same pandas columns (neither is converted, their widths follow the row count); an
# integer label of any size is a number in the header row.
run('export-xlsx-label-equal-numbers', 'export_data', {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': 1, 'creation_date': 1.0}},
    200, download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [I(1), F(1.0)], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
run('export-xlsx-label-equal-boolean', 'export_data', {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': True, 'creation_date': 1}},
    200, download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [{'bool': True}, I(1)], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
run('export-xlsx-label-big-integer', 'export_data',
    {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': 2 ** 70, 'creation_date': -2 ** 63 - 1}},
    200, download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [I(2 ** 70), I(-2 ** 63 - 1)], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
run('export-xlsx-label-unsigned-integers', 'export_data',
    {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': 2 ** 63, 'creation_date': 2 ** 63}},
    200, download('Tags.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [I(2 ** 63), I(2 ** 63)], 'rows': [['Nuoto', '15/09/2026 08:30']]}]}))
run('export-xlsx-label-list-error', 'export_data', {**NUOTO, 'format': 'xlsx', 'column_labels': {'tag_name': ['a']}}, 500, FAILED)
run('sheets-label-dict-error', 'export_multi_sheet', {'sheets': [{**NUOTO, 'column_labels': {'tag_name': {'a': 1}}}]}, 500, FAILED)
run('export-pdf-no-columns-error', 'export_data', {**ONLY_IDS, 'format': 'pdf'}, 500, FAILED)
NUOTO_PDF = {**NUOTO, 'format': 'pdf'}
NUOTO_TABLE = {'engine': 'report_table', 'headers': ['tag_name', 'creation_date'], 'title': 'Tags', 'rows': [['Nuoto', '15/09/2026 08:30']]}
run('export-pdf-label-number-error', 'export_data', {**NUOTO_PDF, 'column_labels': {'tag_name': 5}}, 500, FAILED)
run('export-pdf-header-number-error', 'export_data', {**NUOTO_PDF, 'header_text': 5}, 500, FAILED)
run('export-pdf-text-before-list-error', 'export_data', {**NUOTO_PDF, 'text_before': ['a']}, 500, FAILED)
run('export-pdf-text-after-number-error', 'export_data', {**NUOTO_PDF, 'text_after': 1.5}, 500, FAILED)
run('export-pdf-footer-number', 'export_data', {**NUOTO_PDF, 'footer_text': 7}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**NUOTO_TABLE, 'footer_text': '7'}))
run('export-pdf-footer-list', 'export_data', {**NUOTO_PDF, 'footer_text': [1, 'a', None]}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**NUOTO_TABLE, 'footer_text': "[1, 'a', None]"}))
# Labels and texts are reportlab paragraphs: '&', entities, tags and soft hyphens go through its markup parser, and a text
# longer than a page is cut where it stands, inside a long word too. Markup the parser raises on fails the export.
def image_base64(width, height, colour):
    import base64
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (width, height), colour).save(out, 'JPEG')
    return base64.b64encode(out.getvalue()).decode()


MARKUP_LABELS = {'tag_name': 'Nome & <b>tag</b>', 'creation_date': 'A < B &lt;data&gt;'}
MARKUP_TABLE = {**NUOTO_TABLE, 'headers': list(MARKUP_LABELS.values())}
run('export-pdf-markup', 'export_data', {**NUOTO_PDF, 'column_labels': MARKUP_LABELS, 'text_before': 'Sport &amp; Fun<br/>R&D <i>club</i>',
    'text_after': 'poli\xadsportiva 1<2 &euro;'}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**MARKUP_TABLE, 'text_before': 'Sport &amp; Fun<br/>R&D <i>club</i>',
                                                 'text_after': 'poli\xadsportiva 1<2 &euro;'}))
MARKUP_TEXT = ' '.join(['il <b>corso</b> di nuoto &amp; pallanuoto <i>agonistico</i> R&D per adulti della poli\xadsportiva'] * 120)
WORD_TEXT = 'prima ' * 30 + 'X' * 9000 + ' dopo'
run('export-pdf-markup-over-pages', 'export_data', {**NUOTO_PDF, 'text_before': MARKUP_TEXT, 'text_after': WORD_TEXT}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**NUOTO_TABLE, 'text_before': MARKUP_TEXT, 'text_after': WORD_TEXT}))
run('export-pdf-markup-unclosed-error', 'export_data', {**NUOTO_PDF, 'text_before': 'a <b>b'}, 500, FAILED)
run('export-pdf-label-markup-error', 'export_data', {**NUOTO_PDF, 'column_labels': {'tag_name': 'A<B'}}, 500, FAILED)

# Tags that need the fragment model: lines, fonts, sizes and colours, rises, links and inline images.
RICH_LABELS = {'tag_name': 'Nome <u>tag</u> <font color="#aa0000">rosso</font>', 'creation_date': 'Data m<sup>2</sup> <a href="https://example.test/d">link</a>'}
RICH_TABLE = {**NUOTO_TABLE, 'headers': list(RICH_LABELS.values())}
RICH_BEFORE = 'Il <u>corso</u> di <strike>nuoto</strike> <font size="14" color="#0000ff">grande</font> <a href="https://example.test/c?a=1&amp;b=2">info</a>'
RICH_AFTER = 'H<sub>2</sub>O <font bgcolor="yellow">evidenziato</font> <u kind="double" color="red">doppio</u><br/><link href="mailto:a@example.test">posta</link>'
run('export-pdf-rich-markup', 'export_data', {**NUOTO_PDF, 'column_labels': RICH_LABELS, 'text_before': RICH_BEFORE, 'text_after': RICH_AFTER}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**RICH_TABLE, 'text_before': RICH_BEFORE, 'text_after': RICH_AFTER}))
RICH_TEXT = ' '.join([RICH_BEFORE + ' ' + RICH_AFTER] * 90)
run('export-pdf-rich-markup-over-pages', 'export_data', {**NUOTO_PDF, 'text_before': RICH_TEXT, 'text_after': '<u>' + 'sottolineato ' * 500 + '</u>'}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**NUOTO_TABLE, 'text_before': RICH_TEXT, 'text_after': '<u>' + 'sottolineato ' * 500 + '</u>'}))
PIXEL = 'data:image/jpeg;base64,' + image_base64(30, 20, (200, 30, 30))
IMAGE_TEXT = 'prima <img src="%s" width="15" height="10"/> dopo <img src="%s" valign="middle"/> fine' % (PIXEL, PIXEL)
run('export-pdf-inline-image', 'export_data', {**NUOTO_PDF, 'text_before': IMAGE_TEXT, 'text_after': ' '.join([IMAGE_TEXT] * 40)}, 200,
    download('Tags.pdf', 'application/pdf', pdf={**NUOTO_TABLE, 'text_before': IMAGE_TEXT, 'text_after': ' '.join([IMAGE_TEXT] * 40)}))
run('export-pdf-rich-color-error', 'export_data', {**NUOTO_PDF, 'text_before': 'a <font color="nope">b</font>'}, 500, FAILED)
run('export-pdf-rich-underline-kind-error', 'export_data', {**NUOTO_PDF, 'text_before': 'a <u kind="quad">b</u>'}, 500, FAILED)
run('export-pdf-rich-label-size-error', 'export_data', {**NUOTO_PDF, 'column_labels': {'tag_name': '<font size="x">A</font>'}}, 500, FAILED)

run('sheets-missing-error', 'export_multi_sheet', {'filename': 'x'}, 500, FAILED)
run('sheets-none', 'export_multi_sheet', {'sheets': []}, 400, {'error': 'No sheets specified'})
run('sheets-model-required', 'export_multi_sheet', {'sheets': [{'model_name': 'Tags'}, {'title': 'x'}]}, 400,
    {'error': 'Sheet 2: model_name is required'})
run('sheets-no-data', 'export_multi_sheet', {'sheets': [{'model_name': 'Tags', 'filters': {'tag_name': 'zzz'}}]}, 400,
    {'error': 'No data to export from any sheet', 'count': 0})
run('sheets-unknown-model-error', 'export_multi_sheet', {'sheets': [{'model_name': 'Tags'}, {'model_name': 'Nope'}]}, 500, FAILED)
run('sheets-two', 'export_multi_sheet', {'sheets': [
    {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'filters': {'tag_name__icontains': 'nuoto'}},
    {'model_name': 'Carnet', 'title': 'Carnet attivi', 'fields': ['title', 'lessons_number'], 'order_by': ['lessons_number'],
     'column_labels': {'lessons_number': 'Lezioni'}},
    {'model_name': 'Tags', 'filters': {'tag_name': 'zzz'}}]}, 200,
    download('Export.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': ['tag_name'], 'rows': [['Nuoto'], ['nuoto agonistico']]},
        {'title': 'Carnet attivi', 'columns': ['title', 'Lezioni'], 'rows': [['', I(0)], ['Cinque', I(5)], ['Dieci ingressi', I(10)]]}]}))
run('sheets-repeated-titles-filename', 'export_multi_sheet', {'filename': 'Report: tag/2026', 'sheets': [
    {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'limit': 1},
    {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['-tag_name'], 'limit': 1}]}, 200,
    download('Report- tag-2026.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': ['tag_name'], 'rows': [['Calcio & Basket']]},
        {'title': 'Tags (2)', 'columns': ['tag_name'], 'rows': [['nuoto agonistico']]}]}))
# A numbered name equal to a later title: the third frame is written over the second sheet.
run('sheets-generated-name-equal', 'export_multi_sheet', {'sheets': [
    {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name']},
    {'model_name': 'Carnet', 'title': 'Tags', 'fields': ['title', 'lessons_number'], 'order_by': ['lessons_number'],
     'column_labels': {'lessons_number': 'Lezioni'}},
    {'model_name': 'Tags', 'title': 'Tags (2)', 'fields': ['tag_name'], 'filters': {'tag_name': 'Nuoto'}}]}, 200,
    download('Export.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': ['tag_name'], 'rows': [['Calcio & Basket'], ['Nuoto'], ['nuoto agonistico']]},
        {'title': 'Tags (2)', 'columns': ['title', 'Lezioni'], 'rows': [['', I(0)], ['Cinque', I(5)], ['Dieci ingressi', I(10)]]},
        {'title': 'Tags (2)', 'columns': ['tag_name'], 'rows': [['Nuoto']]}]}))
run('sheets-no-columns', 'export_multi_sheet', {'sheets': [ONLY_IDS, {'model_name': 'Carnet', 'fields': ['title'], 'order_by': ['lessons_number']}]},
    200, download('Export.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Tags', 'columns': [], 'rows': [[], [], []]},
        {'title': 'Carnet', 'columns': ['title'], 'rows': [[''], ['Cinque'], ['Dieci ingressi']]}]}))
run('sheets-text-error', 'export_multi_sheet', {'sheets': 'Tags'}, 500, FAILED)
run('sheets-number-item-error', 'export_multi_sheet', {'sheets': [{'model_name': 'Tags'}, 5]}, 500, FAILED)
run('sheets-group-header', 'export_multi_sheet', {'sheets': [{'model_name': 'Course', 'fields': ['title'], 'order_by': ['title']}]}, 200,
    download('Export.xlsx', XLSX, xlsx={'engine': 'report_sheets', 'sheets': [
        {'title': 'Course', 'columns': ['title'], 'rows': [['Calcio a 5']]}]}), group=IDS['own_group'])


def matrix(course, low=None, high=None):
    """The matrix _build_single_course_matrix answers for a seeded course."""
    days = sorted((d for d in DAYS.values() if d['registry'] == course and (low is None or d['date'][:10] >= low)
                   and (high is None or d['date'][:10] <= high)), key=lambda d: d['date'])
    names = {}
    for row in ENROLLED.values():
        if row['course'] == course and not row['deleted']:
            # An enrolment without a subscription is named by its own id.
            person = PEOPLE.get(row['person'])
            names[row['id']] = f"{person['last']} {person['first']}" if person else row['id']
    present = [{item['course_subscription_id'] if isinstance(item, dict) and 'course_subscription_id' in item else item
                for item in day['attendees'] or [] if not isinstance(item, dict) or 'course_subscription_id' in item} for day in days]
    rows = [{'name': names[key], 'attendance': ['P' if key in marks else '' for marks in present],
             'present_count': sum(key in marks for marks in present)} for key in sorted(names, key=lambda key: names[key].lower())]
    return {'course': COURSES[course]['title'], 'dates': [f"{d['date'][8:10]}/{d['date'][5:7]}/{d['date'][:4]}" for d in days], 'rows': rows}


NOTE = 'Anteprima dei primi 10 iscritti. Usa export=true per generare il file Excel completo.'


def preview(course, low=None, high=None):
    m = matrix(course, low, high)
    return {'data': {'course': m['course'], 'dates': m['dates'], 'total_enrolled': len(m['rows']), 'total_dates': len(m['dates']),
                     'preview': [{'name': r['name'], 'present_count': r['present_count'], 'total_days': len(m['dates'])} for r in m['rows']],
                     'note': NOTE}}


NO_DAYS = 'Nessuna giornata di presenza trovata per questo corso nel periodo indicato.'
CID = lambda key: COURSES[key]['id']
run('attendance-preview-course', 'get_attendance_matrix', {'course_id': CID('nuoto')}, 200, preview('nuoto'))
run('attendance-preview-dates-ignored', 'get_attendance_matrix', {'course_id': CID('nuoto'), 'date_from': 'ieri', 'date_to': 20260915}, 200,
    preview('nuoto'))
run('attendance-preview-period', 'get_attendance_matrix', {'course_name': 'CALC', 'date_from': '2026-09-02', 'date_to': '2026-12-31'}, 200,
    preview('calcio', '2026-09-02', '2026-12-31'))
# Every course with days, by title (the one without a title last).
MULTI = [matrix(key) for key in ('calcio', 'gx', 'ga', 'gb', 'nuoto', 'senza')]
GYM_MULTI = [matrix(key) for key in ('gx', 'ga', 'gb')]
run('attendance-preview-all', 'get_attendance_matrix', {}, 200, {'data': {
    'multi': True, 'courses': [{'course': m['course'], 'total_enrolled': len(m['rows']), 'total_dates': len(m['dates'])} for m in MULTI],
    'summary': {'total_courses': 6, 'total_enrolled': sum(len(m['rows']) for m in MULTI), 'total_dates': sum(len(m['dates']) for m in MULTI)},
    'note': 'Usa export=true per generare un unico file Excel con tutti i corsi (include colonna Corso).'}})
run('attendance-course-missing', 'get_attendance_matrix', {'course_id': IDS['missing']}, 400,
    {'error': f'Corso non trovato con ID: {IDS["missing"]}'})
run('attendance-course-deleted', 'get_attendance_matrix', {'course_id': CID('old'), 'export': True}, 400,
    {'error': f'Corso non trovato con ID: {CID("old")}'})
run('attendance-course-foreign', 'get_attendance_matrix', {'course_id': CID('foreign')}, 400,
    {'error': f'Corso non trovato con ID: {CID("foreign")}'})
run('attendance-course-invalid-error', 'get_attendance_matrix', {'course_id': 'nope'}, 500, FAILED)
run('attendance-name-none', 'get_attendance_matrix', {'course_name': 'zzz'}, 400, {'error': 'Nessun corso trovato con nome: zzz'})
run('attendance-no-registry', 'get_attendance_matrix', {'course_id': CID('tennis'), 'export': True}, 400, {'error': NO_DAYS, 'course': 'Tennis'})
run('attendance-no-days', 'get_attendance_matrix', {'course_id': CID('baby')}, 400, {'error': NO_DAYS, 'course': 'Nuoto baby'})
run('attendance-no-days-in-period', 'get_attendance_matrix', {'course_id': CID('nuoto'), 'date_from': '2027-01-01'}, 400,
    {'error': NO_DAYS, 'course': 'Nuoto'})
run('attendance-none-in-period', 'get_attendance_matrix', {'date_to': '2020-01-01', 'export': True}, 400,
    {'error': 'Nessun dato di presenza trovato per nessun corso nel periodo indicato.'})
run('attendance-export-xlsx-course', 'get_attendance_matrix', {'course_name': 'calc', 'export': True}, 200,
    download('Presenze - Calcio a 5.xlsx', XLSX, xlsx={'engine': 'attendance', 'mode': 'single', 'courses': [matrix('calcio')]}))
run('attendance-export-pdf-course-period', 'get_attendance_matrix', {'course_id': CID('nuoto'), 'export': 1, 'format': 'pdf',
    'date_from': '2026-09-02', 'date_to': '2026-09-15'}, 200,
    download('Presenze - Nuoto.pdf', 'application/pdf', pdf={'engine': 'attendance', 'courses': [matrix('nuoto', '2026-09-02', '2026-09-15')]}))
run('attendance-export-xlsx-all', 'get_attendance_matrix', {'export': True, 'format': 'csv'}, 200,
    download('Presenze - tutti i corsi.xlsx', XLSX, xlsx={'engine': 'attendance', 'mode': 'flat', 'courses': MULTI}))
run('attendance-export-xlsx-sheets', 'get_attendance_matrix', {'export': True, 'multi_sheet': True}, 200,
    download('Presenze - tutti i corsi.xlsx', XLSX, xlsx={'engine': 'attendance', 'mode': 'sheets', 'courses': MULTI}))
# The heading of a course without a title cannot be written.
run('attendance-export-pdf-all-untitled-error', 'get_attendance_matrix', {'export': True, 'format': 'pdf', 'multi_sheet': True}, 500, FAILED)
run('attendance-export-pdf-all', 'get_attendance_matrix', {'export': True, 'format': 'pdf', 'multi_sheet': True}, 200,
    download('Presenze - tutti i corsi.pdf', 'application/pdf', pdf={'engine': 'attendance', 'courses': GYM_MULTI}), group=GYM)
# The third sheet's numbered name is the first one's: its frame is written over that sheet.
run('attendance-export-xlsx-sheets-same-name', 'get_attendance_matrix', {'export': True, 'multi_sheet': True}, 200,
    download('Presenze - tutti i corsi.xlsx', XLSX, xlsx={'engine': 'attendance', 'mode': 'sheets', 'courses': GYM_MULTI}), group=GYM)
run('attendance-untitled-preview', 'get_attendance_matrix', {'course_id': CID('senza')}, 200, preview('senza'))
run('attendance-untitled-export-xlsx-error', 'get_attendance_matrix', {'course_id': CID('senza'), 'export': True}, 500, FAILED)
run('attendance-untitled-export-pdf', 'get_attendance_matrix', {'course_id': CID('senza'), 'export': True, 'format': 'pdf'}, 200,
    download('Presenze - None.pdf', 'application/pdf', pdf={'engine': 'attendance', 'courses': [matrix('senza')]}))
# Course.objects follows the request's group: only the course of that group is left.
report('attendance_group', 'get_attendance_matrix', {})
add('run-attendance-group-header', 'owner', 'POST', RUN('attendance_group'), 200, preview('calcio'), body={},
    headers={'X-Group-Id': IDS['own_group']})
add('run-attendance-group-header-export', 'owner', 'POST', RUN('attendance_group'), 200,
    download('Presenze - Calcio a 5.pdf', 'application/pdf', pdf={'engine': 'attendance', 'courses': [matrix('calcio')]}),
    body={'overrides': {'export': True, 'format': 'pdf'}}, headers={'X-Group-Id': IDS['own_group']})

# DELETE on the run route deletes the report (the viewset's delete()).
add('run-delete-method-deletes', 'owner', 'DELETE', RUN('remove'), 204, '<empty>', effect={'kind': 'report-delete', 'label': 'remove'})

# === t9rep: paths through relations that are not listed ===
# A path with a segment that is not listed for the model it is reached on is dropped: a field item is skipped
# (nothing left -> every column), a filter/exclude key is dropped, an or_filters key is dropped from its dict
# (an emptied dict adds nothing to the OR), an order_by or joins item is dropped, a group_by item is skipped and
# an aggregation is skipped; get_field_values raises (the fixed 500). A listed path keeps working.
TENNIS = {'model_name': 'Course', 'fields': ['title'], 'filters': {'title': 'Tennis'}}
run('query-course-unlisted-relation-field', 'query_data',
    {'model_name': 'Course', 'fields': ['title', 'sport_association__user__email'], 'filters': {'title': 'Tennis'}}, 200,
    listing(1, [{'title': 'Tennis'}]))
run('query-course-unlisted-relation-filter-dropped', 'query_data',
    {'model_name': 'Course', 'fields': ['title'],
     'filters': {'title': 'Tennis', 'sport_association__user__password__startswith': 'zzz'}}, 200,
    listing(1, [{'title': 'Tennis'}]))
run('query-course-unlisted-relation-exclude-dropped', 'query_data',
    {**TENNIS, 'exclude': {'sport_association__user__is_superuser': False}}, 200, listing(1, [{'title': 'Tennis'}]))
run('query-course-unlisted-relation-or-filter-dropped', 'query_data',
    {**TENNIS, 'or_filters': [{'title': 'Tennis'}, {'sport_association__user__username': 'x'}]}, 200,
    listing(1, [{'title': 'Tennis'}]))
run('query-course-unlisted-relation-order-dropped', 'query_data',
    {'model_name': 'Course', 'fields': ['title'], 'filters': {'title__in': ['Tennis', 'Nuoto', 'Calcio a 5']},
     'order_by': ['sport_association__user__email', '-title']}, 200,
    listing(3, [{'title': 'Tennis'}, {'title': 'Nuoto'}, {'title': 'Calcio a 5'}]))
run('query-course-unlisted-relation-join-dropped', 'query_data',
    {**TENNIS, 'joins': ['sport_association__user']}, 200, listing(1, [{'title': 'Tennis'}]))
run('query-course-listed-relation-field', 'query_data',
    {'model_name': 'Course', 'fields': ['title', 'group__name'], 'filters': {'title': COURSES['gx']['title']}}, 200,
    listing(1, [{'title': COURSES['gx']['title'], 'group__name': GROUPS['gym']['name']}]))
run('query-carnet-unlisted-relation-field', 'query_data',
    {'model_name': 'Carnet', 'fields': ['title', 'user_id'], 'filters': {'lessons_number': 10}}, 200,
    listing(1, [{'title': 'Dieci ingressi'}]))
run('query-tags-unlisted-relation-field', 'query_data',
    {'model_name': 'Tags', 'fields': ['tag_name', 'sport_association__user_id'], 'filters': {'tag_name': 'Nuoto'}}, 200,
    listing(1, [{'tag_name': 'Nuoto'}]))
run('values-course-unlisted-relation-error', 'get_field_values',
    {'model_name': 'Course', 'field_name': 'sport_association__user__email'}, 500, FAILED)
run('aggregate-course-unlisted-relation-field-error', 'aggregate_data',
    {'model_name': 'Course', 'aggregations': [{'function': 'max', 'field': 'sport_association__user__last_login'}]}, 400,
    {'error': 'No valid aggregations'})
run('aggregate-course-unlisted-relation-group-by-error', 'aggregate_data',
    {'model_name': 'Course', 'aggregations': [{'function': 'count', 'field': '*'}],
     'group_by': ['sport_association__user__email']}, 400, {'error': 'No valid group_by fields'})

# === t9rep: lookups, values, JSON, exclude and limits ===
# Lookups: regular expressions on text, pattern lookups compare against the column's text, a UUID
# against the nil UUID, an unsupported lookup name and a malformed regular expression.
run('query-tags-regex', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'tag_name__regex': '^Nuoto$'},
    'order_by': ['tag_name']}, 200, listing(1, [{'tag_name': 'Nuoto'}]))
run('query-tags-iregex', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'tag_name__iregex': '^nuoto'},
    'order_by': ['tag_name']}, 200, listing(2, [{'tag_name': 'Nuoto'}, {'tag_name': 'nuoto agonistico'}]))
# A decimal filter value is coerced to a float, so the pattern is '45.5%' against the column's text '45.50'.
run('query-carnets-fee-contains', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'fee'], 'filters': {'fee__contains': '45.5'},
    'order_by': ['title']}, 200, listing(1, [{'title': 'Cinque', 'fee': 45.5}]))
run('query-carnets-fee-startswith', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'fee'],
    'filters': {'fee__startswith': '45.5'}, 'order_by': ['title']}, 200, listing(1, [{'title': 'Cinque', 'fee': 45.5}]))
# An integer column is compared as text too: '10' and '0' both contain '0'.
run('query-carnets-lessons-contains', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'lessons_number'],
    'filters': {'lessons_number__contains': '0'}, 'order_by': ['lessons_number']}, 200,
    listing(2, [{'title': None, 'lessons_number': 0}, {'title': 'Dieci ingressi', 'lessons_number': 10}]))
run('query-tags-uuid-gt-nil', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'tag_id__gt': '00000000-0000-0000-0000-000000000000'}, 'order_by': ['tag_name']}, 200,
    listing(3, [{'tag_name': 'Calcio & Basket'}, {'tag_name': 'Nuoto'}, {'tag_name': 'nuoto agonistico'}]))
run('query-categories-unsupported-lookup-error', 'query_data', {'model_name': 'PaymentCategory', 'filters': {'name__nope': 'x'}}, 500, FAILED)
run('query-tags-malformed-regex-error', 'query_data', {'model_name': 'Tags', 'filters': {'tag_name__regex': '('}}, 500, FAILED)
# Date and time transforms on the seeded UTC creation dates: 10:00, 08:30.125, 00:00 on 1/15 September and 1 October 2026.
run('query-tags-minute-transform', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__minute': 0}, 'order_by': ['tag_name']}, 200,
    listing(2, [{'tag_name': 'Calcio & Basket'}, {'tag_name': 'nuoto agonistico'}]))
run('query-tags-time-transform', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__time': '10:00:00'}, 'order_by': ['tag_name']}, 200, listing(1, [{'tag_name': 'Calcio & Basket'}]))
# 1 October 2026 is a Thursday (ISO week day 4).
run('query-tags-iso-week-day-transform', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__iso_week_day': 4}, 'order_by': ['tag_name']}, 200, listing(1, [{'tag_name': 'nuoto agonistico'}]))
run('query-tags-date-year-transform', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__date__year': 2026}, 'order_by': ['tag_name']}, 200,
    listing(3, [{'tag_name': 'Calcio & Basket'}, {'tag_name': 'Nuoto'}, {'tag_name': 'nuoto agonistico'}]))
run('query-tags-time-field', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name', 'creation_date__time'],
    'filters': {'tag_name__startswith': 'Calcio'}}, 200, listing(1, [{'tag_name': 'Calcio & Basket', 'creation_date__time': '10:00:00'}]))
# Minutes 0 (Calcio, nuoto agonistico) and 30 (Nuoto), grouped in that order.
run('aggregate-tags-group-minute', 'aggregate_data', {'model_name': 'Tags', 'aggregations': [{'function': 'count', 'field': '*'}],
    'group_by': ['creation_date__minute']}, 200,
    {'data': {'data': [{'creation_date__minute': 0, 'count_*': 2}, {'creation_date__minute': 30, 'count_*': 1}], 'count': 2}})
run('query-tags-year-zero-error', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'],
    'filters': {'creation_date__year': 0}}, 500, FAILED)
# Filter values: a string for `in` (its characters), a one-item range, an integer outside the column
# range (always false), a float and non-ASCII digits for an integer, an integer for a UUID, a basic-form
# date and an ISO week date.
run('query-tags-in-string', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'tag_name__in': 'Nuoto'},
    'order_by': ['tag_name']}, 200, listing(0, []))
run('query-carnets-range-one-item-error', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'filters': {'lessons_number__range': [5]}}, 500, FAILED)
run('query-carnets-huge-integer-empty', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'filters': {'lessons_number': 99999999999999999999}, 'order_by': ['title']}, 200, listing(0, []))
# int(5.9) and int('٥') are 5.
run('query-carnets-float-for-integer', 'query_data', {'model_name': 'Carnet', 'fields': ['title'], 'filters': {'lessons_number': 5.9},
    'order_by': ['title']}, 200, listing(1, [{'title': 'Cinque'}]))
run('query-carnets-non-ascii-digits', 'query_data', {'model_name': 'Carnet', 'fields': ['title'], 'filters': {'lessons_number': '٥'},
    'order_by': ['title']}, 200, listing(1, [{'title': 'Cinque'}]))
run('query-tags-uuid-from-integer', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'tag_id': 5},
    'order_by': ['tag_name']}, 200, listing(0, []))
# 20261001 and 2026-W40-4 are both 1 October 2026 at midnight, the creation date of nuoto agonistico.
run('query-tags-basic-date', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'creation_date': '20261001'},
    'order_by': ['tag_name']}, 200, listing(1, [{'tag_name': 'nuoto agonistico'}]))
run('query-tags-week-date', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'filters': {'creation_date': '2026-W40-4'},
    'order_by': ['tag_name']}, 200, listing(1, [{'tag_name': 'nuoto agonistico'}]))
# JSON: the dieci carnet's meta is {'colore': 'blu'}; the others have no such key or a null meta.
run('query-carnets-meta-contains', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'meta'],
    'filters': {'meta__contains': {'colore': 'blu'}}, 'order_by': ['title']}, 200,
    listing(1, [{'title': 'Dieci ingressi', 'meta': {'colore': 'blu'}}]))
run('query-carnets-meta-has-keys', 'query_data', {'model_name': 'Carnet', 'fields': ['title', 'meta'],
    'filters': {'meta__has_keys': ['colore']}, 'order_by': ['title']}, 200,
    listing(1, [{'title': 'Dieci ingressi', 'meta': {'colore': 'blu'}}]))
run('query-carnets-meta-key-icontains', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'filters': {'meta__colore__icontains': 'BL'}, 'order_by': ['title']}, 200, listing(1, [{'title': 'Dieci ingressi'}]))
run('query-carnets-meta-key-isnull', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'filters': {'meta__colore__isnull': True}, 'order_by': ['title']}, 200, listing(2, [{'title': 'Cinque'}, {'title': None}]))
run('query-carnets-meta-missing-key', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'filters': {'meta__manca__icontains': 'x'}, 'order_by': ['title']}, 200, listing(0, []))
# Values on a key transform: one row per carnet, ordered by title (the null title last).
run('query-carnets-meta-key-field', 'query_data', {'model_name': 'Carnet', 'fields': ['meta__colore'], 'order_by': ['title']}, 200,
    listing(3, [{'meta__colore': None}, {'meta__colore': 'blu'}, {'meta__colore': None}]))
# exclude across a many-valued relation (a reverse foreign key, and a path through it).
run('query-courses-exclude-registry-status', 'query_data', {'model_name': 'Course', 'fields': ['title'],
    'exclude': {'attendanceregistry__status': 2}, 'order_by': ['title']}, 200, listing(1, [{'title': 'Tennis'}]))
# Only the baby's registry and tennis (no registry) have no days titled 'Lezione'.
run('query-courses-exclude-registry-day', 'query_data', {'model_name': 'Course', 'fields': ['title'],
    'exclude': {'attendanceregistry__attendanceday__title': 'Lezione'}, 'order_by': ['title']}, 200,
    listing(2, [{'title': 'Nuoto baby'}, {'title': 'Tennis'}]))
run('count-courses-exclude-paid-enrolments', 'count_data', {'model_name': 'Course', 'exclude': {'coursesubscription__paid': True}}, 200,
    {'data': {'count': 8}})
# One enrolment (nuoto's acca) is deleted.
run('count-courses-exclude-deleted-enrolments', 'count_data', {'model_name': 'Course', 'exclude': {'coursesubscription__deleted': True}},
    200, {'data': {'count': 7}})
# Limits and offsets as other Python numbers, repeated names, and joins items that are not strings.
run('query-tags-limit-boolean', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'limit': True},
    200, listing(3, [{'tag_name': 'Calcio & Basket'}], True))
run('query-tags-limit-float', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'limit': 1.5},
    200, listing(3, [{'tag_name': 'Calcio & Basket'}], True))
run('query-tags-offset-string-error', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'offset': '1'},
    500, FAILED)
run('query-tags-fields-repeated', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name', 'tag_name'], 'order_by': ['tag_name']}, 200,
    listing(3, [{'tag_name': 'Calcio & Basket'}, {'tag_name': 'Nuoto'}, {'tag_name': 'nuoto agonistico'}]))
run('query-tags-joins-number-error', 'query_data', {'model_name': 'Tags', 'fields': ['tag_name'], 'order_by': ['tag_name'], 'joins': [5]},
    500, FAILED)
# An integer outside the column range inside or_filters is dropped from the OR; a negated one excludes nothing.
run('query-carnets-or-filters-huge-integer', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'or_filters': [{'lessons_number': 99999999999999999999}, {'title': 'Cinque'}], 'order_by': ['title']}, 200,
    listing(1, [{'title': 'Cinque'}]))
run('query-carnets-exclude-huge-integer', 'query_data', {'model_name': 'Carnet', 'fields': ['title'],
    'exclude': {'lessons_number': 99999999999999999999}, 'order_by': ['lessons_number']}, 200,
    listing(3, [{'title': None}, {'title': 'Cinque'}, {'title': 'Dieci ingressi'}]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Template update and saved report run authored sequence', 'ids': IDS,
                              'seed': {'templates': TEMPLATES, 'tags': TAGS, 'categories': CATEGORIES, 'carnets': CARNETS, 'reports': REPORTS,
                                       'people': PEOPLE, 'groups': GROUPS, 'courses': COURSES, 'enrolled': ENROLLED, 'registries': REGISTRIES, 'days': DAYS},
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
