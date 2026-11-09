"""Author the templates/<pk>/update and saved-reports/<pk>/run HTTP sequence (templates-reports.md).

templates/<pk>/update is SportAssociationModuleTemplatesViewSet.update: get_object
on the association queryset (bare 404 for an unparseable pk, model 404 otherwise),
then ModelSerializer(fields='__all__', partial=True) and save(). A new primary key
makes Model.save() insert a copy (auditlog logs a create); the related
sport_association goes through the tenant scope (another tenant is DRF's generic
403). Audit: changed fields only, no entry when nothing changed.

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
ids = iter(f'a1000000-0000-4000-8000-{930001 + n:012d}' for n in range(200))
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
add('update-unchanged-no-audit', 'owner', 'PATCH', UPDATE('main'), 200, serialized(effect['row']), effect=effect,
    body={'sport_association_module_templates_id': TEMPLATES['main']['id'], 'sport_association': OWN})
COPY = next(ids)
copy_row = {**state['source'], 'id': COPY, 'header': False}
add('update-new-id-inserts-copy', 'owner', 'PATCH', UPDATE('source'), 200, serialized(copy_row),
    body={'sport_association_module_templates_id': COPY, 'header': False},
    effect={'kind': 'template-copy', 'id': COPY, 'row': copy_row, 'actor': 'owner'})
add('update-existing-id-unique', 'owner', 'PATCH', UPDATE('main'), 400,
    {'sport_association_module_templates_id': ['sport association module templates with this sport association module templates id already exists.']},
    body={'sport_association_module_templates_id': TEMPLATES['foreign']['id']})
add('update-foreign-association-denied', 'owner', 'PATCH', UPDATE('main'), 403, {'detail': 'You do not have permission to perform this action.'},
    body={'sport_association': FOREIGN, 'name': ''})
add('update-unknown-association', 'owner', 'PATCH', UPDATE('main'), 400,
    {'sport_association': [f'Invalid pk "{IDS["missing"]}" - object does not exist.']}, body={'sport_association': IDS['missing']})
add('update-invalid-fields', 'owner', 'PATCH', UPDATE('main'), 400,
    {'name': ['This field may not be blank.'], 'template': ['This field may not be blank.'], 'footer': ['This field may not be null.'],
     'custom_header': ['Not a valid string.'], 'sport_association': ['“nope” is not a valid UUID.'],
     'sport_association_module_templates_id': ['Must be a valid UUID.'], 'header': ['Must be a valid boolean.']},
    body={'name': '', 'template': '  ', 'footer': None, 'custom_header': True, 'sport_association': 'nope',
          'sport_association_module_templates_id': 'bad', 'header': 'maybe'})
add('update-null-values', 'owner', 'PATCH', UPDATE('main'), 400,
    {'name': ['This field may not be null.'], 'sport_association': ['This field may not be null.'],
     'sport_association_module_templates_id': ['This field may not be null.']},
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


def run(name, tool, params, status, response, *, overrides=None, actor='owner'):
    label = name.replace('-', '_')
    report(label, tool, params)
    add(f'run-{name}', actor, 'POST', RUN(label), status, response, body={} if overrides is None else {'overrides': overrides})


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
# get_schema
run('schema-unknown-model', 'get_schema', {'model_name': 'Nope'}, 400, {'error': 'Model Nope not found'})

# DELETE on the run route deletes the report (the viewset's delete()).
add('run-delete-method-deletes', 'owner', 'DELETE', RUN('remove'), 204, '<empty>', effect={'kind': 'report-delete', 'label': 'remove'})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Template update and saved report run authored sequence', 'ids': IDS,
                              'seed': {'templates': TEMPLATES, 'tags': TAGS, 'categories': CATEGORIES, 'carnets': CARNETS, 'reports': REPORTS},
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
