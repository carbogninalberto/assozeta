"""Author the course/list HTTP sequence (course-list.md).

course_list (@api_view(['GET', 'POST'])) pins, in Django's order:
- athletes (request.user.role): sport_association_id is required (PermissionDenied
  'Missing sport_association_id query param.'), is_valid_uuid (a DRF 400 list), then
  {'data': CourseSerializer(...)} of the association's live, group-aware ACTIVE
  courses by -creation_date, for any method;
- everyone else: SportAssociation.objects.get(user=request.user) (a 500 without one),
  the live, group-aware courses of that association by -creation_date, then
  subscription_id (Subscription.objects.get: a 500 when malformed or missing; the
  courses it has live course subscriptions to are excluded), query[generalSearch]
  (title or description icontains), query[tags] (tag ids: OR through one join, so a
  course with two listed tags appears twice; tags_and == '1' chains one join per id),
  query[status_flag] (int(); out of range matches nothing), sort[field]/sort[sort]
  (order_by(field) for 'asc', else order_by('-' + field); an unknown name is a 500);
- all == '1': {'data': [...]} (optimized == '1': values('course_id', 'title'));
- POST without all: request.data, then current_view_course over the whole filtered,
  sorted queryset (format must be pdf or excel, otherwise a 500). 'excel' is
  get_excel_base64 of Nome, Tipologia, Visibilità, Data inizio, Data fine, Data
  creazione, In evidenza, Strutture, Iscritti (live course subscriptions of the
  course), Tag, then one column per CourseTags of the association ('assegnato' when
  the course has it); {'file': <base64>, 'filename': '[<today>] Corsi filtrati -
  <denomination>.xlsx'}. The pdf file is not ported (see the doc);
- otherwise KTDatatablePagination (pagination[page], pagination[perpage]) with
  {'data': {index: course + current_year + athletes}, 'meta': {total, page, pages,
  perpage, rowIds}}; athletes are the live course subscriptions keyed by index
  (paid, creation_date and, with a subscription, subscription_id and the associate's
  first_name, last_name, born_date, sex; a subscription without an associate stops
  that course's list after paid/creation_date).
The route is a read: every case leaves the state unchanged. Run with ``python3 -I``.
"""
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

OUTPUT = Path(__file__).with_name('course_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{630001 + n:012d}' for n in range(60))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000639999'

TAGS = {'t1': {'id': next(ids), 'association': OWN, 'name': 'Agonistico è'}, 't2': {'id': next(ids), 'association': OWN, 'name': 'Base'},
        't3': {'id': next(ids), 'association': OWN, 'name': 'Inutilizzato'},
        'tf': {'id': next(ids), 'association': FOREIGN, 'name': 'Estero'}}
LOCATIONS = {'l1': {'id': next(ids), 'association': OWN, 'title': 'Piscina', 'address': 'Via 1'},
             'l2': {'id': next(ids), 'association': OWN, 'title': None, 'address': None}}
BASE = {'association': OWN, 'status': 2, 'type': 1, 'created': '2026-09-01T08:00:00Z', 'title': 'Corso', 'description': 'Testo',
        'multi_payments': False, 'fee': '10', 'events': None, 'one_fee_payment': False, 'one_fee': '0', 'google_calendar_id': None,
        'color': '#123456', 'multiple_quotes': None, 'start': None, 'end': None, 'pinned': False, 'season': False, 'frequency': 1,
        'from_subscription': False, 'day': 1, 'renewal': False, 'deleted': False, 'group': None, 'tags': [], 'locations': []}
COURSES = {
    'full': {**BASE, 'id': next(ids), 'type': 2, 'created': '2026-09-01T08:00:00.123456Z', 'title': 'Nuoto è',
             'description': 'Lungo\ntesto', 'multi_payments': True, 'fee': '12.5', 'events': {'days': [1, 'lun'], 'note': None},
             'one_fee_payment': True, 'one_fee': '3', 'google_calendar_id': 'cal-1', 'color': '#abcdef',
             'multiple_quotes': [{'amount': 5, 'label': 'Prima'}], 'start': '2026-09-10T07:30:00Z', 'end': '2027-06-30T18:00:00.500000Z',
             'pinned': True, 'season': True, 'frequency': None, 'from_subscription': True, 'day': 28, 'renewal': True,
             'tags': ['t1', 't2'], 'locations': ['l1', 'l2']},
    'grouped': {**BASE, 'id': next(ids), 'created': '2026-08-01T10:00:00Z', 'title': 'Gruppo', 'group': 'own_group', 'tags': ['t2']},
    # The first instant of the balance year (1 January with the default start).
    'internal': {**BASE, 'id': next(ids), 'status': 3, 'created': '2026-01-01T00:00:00Z', 'title': 'Tennis_base',
                 'description': 'Corso di nuoto', 'type': 3},
    'draft': {**BASE, 'id': next(ids), 'status': 1, 'created': '2025-12-31T23:59:59Z', 'title': 'Yoga 50%', 'description': None,
              'tags': ['t1']},
    'old': {**BASE, 'id': next(ids), 'created': '2000-03-01T12:00:00Z', 'title': None, 'description': None, 'fee': None, 'color': None},
    'gone': {**BASE, 'id': next(ids), 'created': '2026-10-01T08:00:00Z', 'title': 'Eliminato', 'deleted': True, 'tags': ['t1']},
    'abroad': {**BASE, 'id': next(ids), 'association': FOREIGN, 'created': '2026-07-01T08:00:00Z', 'title': 'Estero', 'tags': ['tf']},
    'abroad_draft': {**BASE, 'id': next(ids), 'association': FOREIGN, 'status': 1, 'created': '2026-06-01T08:00:00Z', 'title': 'Bozza'},
}
ASSOCIATES = {
    'mario': {'id': next(ids), 'association': OWN, 'first_name': 'Mario', 'last_name': 'Rossi', 'born_date': '1990-05-04', 'sex': 'M'},
    'blank': {'id': next(ids), 'association': OWN, 'first_name': None, 'last_name': None, 'born_date': None, 'sex': 'F'},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'first_name': 'Anna', 'last_name': 'Estera', 'born_date': '2001-02-03', 'sex': 'A'},
}
SUBSCRIPTIONS = {
    's1': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'group': 'own_group', 'deleted': False},
    's2': {'id': next(ids), 'association': OWN, 'associate': 'blank', 'group': None, 'deleted': False},
    'no_associate': {'id': next(ids), 'association': OWN, 'associate': None, 'group': None, 'deleted': False},
    'removed': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'group': None, 'deleted': True},
    'unused': {'id': next(ids), 'association': OWN, 'associate': 'mario', 'group': None, 'deleted': False},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'associate': 'abroad', 'group': None, 'deleted': False},
}
COURSE_SUBSCRIPTIONS = {
    'cs1': {'id': next(ids), 'course': 'full', 'subscription': 's1', 'paid': True, 'created': '2026-09-02T10:00:00Z', 'deleted': False},
    'cs2': {'id': next(ids), 'course': 'full', 'subscription': 's2', 'paid': False, 'created': '2026-09-03T10:00:00.500000Z',
            'deleted': False},
    'cs3': {'id': next(ids), 'course': 'full', 'subscription': 's1', 'paid': False, 'created': '2026-09-04T10:00:00Z', 'deleted': True},
    'cs4': {'id': next(ids), 'course': 'draft', 'subscription': None, 'paid': False, 'created': '2026-01-05T09:00:00Z', 'deleted': False},
    'cs5': {'id': next(ids), 'course': 'internal', 'subscription': 'no_associate', 'paid': True, 'created': '2026-02-05T09:00:00Z',
            'deleted': False},
    'cs6': {'id': next(ids), 'course': 'grouped', 'subscription': 'abroad', 'paid': True, 'created': '2026-08-05T09:00:00.000001Z',
            'deleted': False},
    'cs7': {'id': next(ids), 'course': 'draft', 'subscription': 's2', 'paid': True, 'created': '2026-01-06T09:00:00Z', 'deleted': True},
    'cs8': {'id': next(ids), 'course': 'old', 'subscription': 'removed', 'paid': False, 'created': '2000-03-02T09:00:00Z', 'deleted': False},
}
# Two more tags of the foreign association, inserted after the others: their
# names repeat the fixed column 'Nome' and the tag 'Estero' (duplicate columns).
TAGS['tf_nome'] = {'id': next(ids), 'association': FOREIGN, 'name': 'Nome'}
TAGS['tf_estero'] = {'id': next(ids), 'association': FOREIGN, 'name': 'Estero'}
# The M2M reads of the export have no ORDER BY: tags and locations are seeded in
# id order and each course lists them in that order, so heap order, through-row
# order and primary-key order agree (course-list.md, "Row order").
assert list(TAGS) == sorted(TAGS, key=lambda label: TAGS[label]['id'])
assert list(LOCATIONS) == sorted(LOCATIONS, key=lambda label: LOCATIONS[label]['id'])
for _row in COURSES.values():
    assert _row['tags'] == sorted(_row['tags'], key=lambda label: TAGS[label]['id'])
    assert _row['locations'] == sorted(_row['locations'], key=lambda label: LOCATIONS[label]['id'])
DENOMINATION = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}
cases = []


def decimal(value):
    return None if value is None else str(Decimal(value).quantize(Decimal('0.01')))


def course(label):
    """CourseSerializer(course).data; 'athletes' reads a missing attribute (allow_null: None)."""
    row = COURSES[label]
    return {'course_id': row['id'], 'athletes': None,
            'tags': [{'tag_id': TAGS[t]['id'], 'tag_name': TAGS[t]['name']} for t in row['tags']],
            'locations': [{'course_location_id': LOCATIONS[l]['id'], 'title': LOCATIONS[l]['title'], 'address': LOCATIONS[l]['address']}
                          for l in row['locations']],
            'status_flag': row['status'], 'course_type': row['type'], 'creation_date': row['created'], 'title': row['title'],
            'description': row['description'], 'multi_payments': row['multi_payments'], 'fee': decimal(row['fee']), 'events': row['events'],
            'one_fee_payment': row['one_fee_payment'], 'one_fee': decimal(row['one_fee']), 'google_calendar_id': row['google_calendar_id'],
            'google_background_color': row['color'], 'multiple_quotes': row['multiple_quotes'], 'start_date': row['start'],
            'end_date': row['end'], 'pinned': row['pinned'], 'billed_duration_is_sport_season': row['season'],
            'billed_frequency': row['frequency'], 'billed_from_subscription_date': row['from_subscription'],
            'billed_from_day_of_month': row['day'], 'auto_renewal': row['renewal'], 'deleted': row['deleted'],
            'sport_association': row['association'], 'group': IDS[row['group']] if row['group'] else None}


def athlete(label):
    """One athletes entry: paid and creation_date, then the subscription when it has one.
    A subscription without an associate raises inside the view's try: the entry keeps
    paid and creation_date only."""
    row = COURSE_SUBSCRIPTIONS[label]
    entry = {'paid': row['paid'], 'creation_date': row['created']}
    subscription = SUBSCRIPTIONS.get(row['subscription'])
    if subscription and subscription['associate']:
        associate = ASSOCIATES[subscription['associate']]
        entry['subscription'] = {'subscription_id': subscription['id'], 'associate': {
            key: associate[key] for key in ('first_name', 'last_name', 'born_date', 'sex')}}
    return entry


# Live course subscriptions per course (the view's athletes query is unordered;
# the contract compares each course's entries as a set). An association-scoped
# session (owner_full) only sees those whose subscription belongs to the association.
ATHLETES = {'full': ['cs1', 'cs2'], 'draft': ['cs4'], 'internal': ['cs5'], 'grouped': ['cs6'], 'old': ['cs8']}
SCOPED_ATHLETES = {'full': ['cs1', 'cs2'], 'internal': ['cs5'], 'old': ['cs8']}


def row(label, athletes):
    return {**course(label), 'current_year': {'$current_year': COURSES[label]['created'][:10]},
            'athletes': {str(i): athlete(cs) for i, cs in enumerate(athletes.get(label, []))}}


def page(labels, *, total=None, number=1, pages=1, perpage=10, athletes=ATHLETES):
    total = len(labels) if total is None else total
    return {'data': {str(i): row(label, athletes) for i, label in enumerate(labels)},
            'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': perpage,
                     'rowIds': [COURSES[label]['id'] for label in labels]}}


def listed(labels):
    return {'data': [course(label) for label in labels]}


def optimized(labels):
    return {'data': [{'course_id': COURSES[label]['id'], 'title': COURSES[label]['title']} for label in labels]}


def add(name, actor, query, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None):
    path = '/course/list' + ('?' + urlencode(query) if query else '')
    case = {'name': f'course-list-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


tag = lambda *labels: ','.join(TAGS[label]['id'] for label in labels)
sub = lambda label: SUBSCRIPTIONS[label]['id']
ALL = ['full', 'grouped', 'internal', 'draft', 'old']
ACTIVE = ['full', 'grouped', 'old']
INVALID_PAGE = {'detail': 'Invalid page.'}
group = lambda label: {'X-Group-Id': IDS[label]}

# Identity and transport.
add('owner-default', 'owner', None, 200, page(ALL))
add('full-collaborator', 'full_collaborator', None, 200, page(ALL))
add('admin-impersonating-owner', 'admin', None, 200, page(ALL), headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('owner-session-scopes-athletes', 'owner', None, 200, page(ALL, athletes=SCOPED_ATHLETES),
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})
add('foreign-owner', 'foreign_owner', None, 200, page(['abroad', 'abroad_draft']))
add('owner-without-association', 'owner_without_association', None, 500, SERVER)
add('deleted-association-owner', 'deleted_association_owner', None, 500, SERVER)
add('restricted-missing-permission', 'restricted_collaborator', None, 403, {'detail': 'Missing permission: association.courses.read'})
add('restricted-post-missing-permission', 'restricted_collaborator', None, 403, {'detail': 'Missing permission: association.courses.read'},
    method='POST', body={})
add('admin-without-session', 'admin', None, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', None, 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', None, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('options', 'owner', None, 200, {'name': 'Course List', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# Group-aware manager.
add('group-own', 'owner', None, 200, page(['grouped']), headers=group('own_group'))
add('group-foreign', 'owner', None, 200, page([]), headers=group('foreign_group'))
add('group-malformed-ignored', 'owner', None, 200, page(ALL), headers={'X-Group-Id': 'abc'})

# query[generalSearch]: title or description icontains, LIKE wildcards escaped.
add('search-title-or-description', 'owner', {'query[generalSearch]': 'NUOTO'}, 200, page(['full', 'internal']))
add('search-percent-literal', 'owner', {'query[generalSearch]': '%'}, 200, page(['draft']))
add('search-underscore-literal', 'owner', {'query[generalSearch]': '_'}, 200, page(['internal']))
add('search-percent-suffix', 'owner', {'query[generalSearch]': '50%'}, 200, page(['draft']))
add('search-empty-ignored', 'owner', {'query[generalSearch]': ''}, 200, page(ALL))
add('search-no-match', 'owner', {'query[generalSearch]': 'zzz'}, 200, page([]))

# query[tags] / query[tags_and].
add('tags-one', 'owner', {'query[tags]': tag('t1')}, 200, page(['full', 'draft']))
add('tags-or-duplicates', 'owner', {'query[tags]': tag('t1', 't2')}, 200, page(['full', 'full', 'grouped', 'draft']))
add('tags-or-duplicates-page', 'owner', {'query[tags]': tag('t1', 't2'), 'pagination[perpage]': '1', 'pagination[page]': '2'}, 200,
    page(['full'], total=4, number=2, pages=4, perpage=1))
add('tags-and', 'owner', {'query[tags]': tag('t1', 't2'), 'query[tags_and]': '1'}, 200, page(['full']))
add('tags-and-repeated', 'owner', {'query[tags]': tag('t1', 't1'), 'query[tags_and]': '1'}, 200, page(['full', 'draft']))
add('tags-and-not-one-is-or', 'owner', {'query[tags]': tag('t1', 't2'), 'query[tags_and]': 'true'}, 200,
    page(['full', 'full', 'grouped', 'draft']))
add('tags-foreign', 'owner', {'query[tags]': tag('tf')}, 200, page([]))
add('tags-unused', 'owner', {'query[tags]': tag('t3')}, 200, page([]))
add('tags-braced-uppercase', 'owner', {'query[tags]': '{' + TAGS['t1']['id'].upper() + '}'}, 200, page(['full', 'draft']))
add('tags-malformed', 'owner', {'query[tags]': 'abc'}, 500, SERVER)
add('tags-trailing-comma', 'owner', {'query[tags]': tag('t1') + ','}, 500, SERVER)
add('tags-and-malformed', 'owner', {'query[tags]': tag('t1') + ',abc', 'query[tags_and]': '1'}, 500, SERVER)
add('tags-empty-ignored', 'owner', {'query[tags]': ''}, 200, page(ALL))

# query[status_flag]: int(); out of the PositiveSmallIntegerField range matches nothing.
add('status-draft', 'owner', {'query[status_flag]': '1'}, 200, page(['draft']))
add('status-internal', 'owner', {'query[status_flag]': '3'}, 200, page(['internal']))
add('status-spaces', 'owner', {'query[status_flag]': ' 2 '}, 200, page(ACTIVE))
add('status-zero', 'owner', {'query[status_flag]': '0'}, 200, page([]))
add('status-overflow', 'owner', {'query[status_flag]': '40000'}, 200, page([]))
add('status-negative', 'owner', {'query[status_flag]': '-1'}, 200, page([]))
add('status-not-a-number', 'owner', {'query[status_flag]': 'abc'}, 500, SERVER)
add('status-decimal', 'owner', {'query[status_flag]': '2.0'}, 500, SERVER)

# subscription_id: courses with a live course subscription of that subscription are excluded.
add('subscription-excludes', 'owner', {'subscription_id': sub('s2')}, 200, page(['grouped', 'internal', 'draft', 'old']))
add('subscription-foreign-excludes', 'owner', {'subscription_id': sub('abroad')}, 200, page(['full', 'internal', 'draft', 'old']))
add('subscription-deleted-still-found', 'owner', {'subscription_id': sub('removed')}, 200, page(['full', 'grouped', 'internal', 'draft']))
add('subscription-unused', 'owner', {'subscription_id': sub('unused')}, 200, page(ALL))
add('subscription-unknown', 'owner', {'subscription_id': UNKNOWN}, 500, SERVER)
add('subscription-malformed', 'owner', {'subscription_id': 'abc'}, 500, SERVER)
add('subscription-group-own', 'owner', {'subscription_id': sub('s1')}, 200, page(['grouped']), headers=group('own_group'))
add('subscription-group-hidden', 'owner', {'subscription_id': sub('s1')}, 500, SERVER, headers=group('foreign_group'))
add('subscription-scoped-session-foreign', 'owner', {'subscription_id': sub('abroad')}, 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})

# sort[field] / sort[sort]: order_by(field) for 'asc', otherwise order_by('-' + field).
by_id = sorted(ALL, key=lambda label: COURSES[label]['id'])
add('sort-title-asc', 'owner', {'sort[field]': 'title', 'sort[sort]': 'asc'}, 200, page(['grouped', 'full', 'internal', 'draft', 'old']))
add('sort-title-desc', 'owner', {'sort[field]': 'title', 'sort[sort]': 'desc'}, 200, page(['old', 'draft', 'internal', 'full', 'grouped']))
add('sort-other-direction-is-desc', 'owner', {'sort[field]': 'title', 'sort[sort]': 'ASC'}, 200,
    page(['old', 'draft', 'internal', 'full', 'grouped']))
add('sort-minus-field-asc', 'owner', {'sort[field]': '-title', 'sort[sort]': 'asc'}, 200, page(['old', 'draft', 'internal', 'full', 'grouped']))
add('sort-creation-asc', 'owner', {'sort[field]': 'creation_date', 'sort[sort]': 'asc'}, 200,
    page(['old', 'draft', 'internal', 'grouped', 'full']))
add('sort-pk-desc', 'owner', {'sort[field]': 'pk', 'sort[sort]': 'desc'}, 200, page(by_id[::-1]))
add('sort-course-id-asc', 'owner', {'sort[field]': 'course_id', 'sort[sort]': 'asc'}, 200, page(by_id))
add('sort-relation-attname', 'owner', {'sort[field]': 'sport_association_id', 'sort[sort]': 'asc', 'query[status_flag]': '1'}, 200,
    page(['draft']))
add('sort-random', 'owner', {'sort[field]': '?', 'sort[sort]': 'asc', 'query[status_flag]': '3'}, 200, page(['internal']))
add('sort-without-direction-ignored', 'owner', {'sort[field]': 'title'}, 200, page(ALL))
add('sort-empty-direction-ignored', 'owner', {'sort[field]': 'title', 'sort[sort]': ''}, 200, page(ALL))
add('sort-unknown-field', 'owner', {'sort[field]': 'nope', 'sort[sort]': 'asc'}, 500, SERVER)
add('sort-double-minus', 'owner', {'sort[field]': '-title', 'sort[sort]': 'desc'}, 500, SERVER)
add('sort-random-desc', 'owner', {'sort[field]': '?', 'sort[sort]': 'desc'}, 500, SERVER)
add('sort-unknown-transform', 'owner', {'sort[field]': 'title__nope', 'sort[sort]': 'asc'}, 500, SERVER)
# An unknown first name fails in order_by() itself; names past a known field
# (transforms, relations) are only compiled with the rows, after the page checks.
add('sort-unknown-field-before-page', 'owner', {'sort[field]': 'nope', 'sort[sort]': 'asc', 'pagination[page]': '0'}, 500, SERVER)
add('sort-unknown-transform-after-page', 'owner', {'sort[field]': 'title__nope', 'sort[sort]': 'asc', 'pagination[page]': '0'}, 404,
    INVALID_PAGE)
add('sort-relation-after-page', 'owner', {'sort[field]': 'tags', 'sort[sort]': 'asc', 'pagination[page]': 'abc'}, 404, INVALID_PAGE)
add('sort-unknown-transform-all', 'owner', {'sort[field]': 'title__nope', 'sort[sort]': 'asc', 'all': '1'}, 500, SERVER)

# KTDatatablePagination.
add('page-size', 'owner', {'pagination[perpage]': '2'}, 200, page(['full', 'grouped'], total=5, pages=3, perpage=2))
add('page-two', 'owner', {'pagination[perpage]': '2', 'pagination[page]': ' 2 '}, 200,
    page(['internal', 'draft'], total=5, number=2, pages=3, perpage=2))
add('page-last', 'owner', {'pagination[perpage]': '2', 'pagination[page]': 'last'}, 200, page(['old'], total=5, number=3, pages=3, perpage=2))
add('page-zero', 'owner', {'pagination[page]': '0'}, 404, INVALID_PAGE)
add('page-zero-italian', 'owner', {'pagination[page]': '0'}, 404, {'detail': 'Pagina non valida.'}, headers={'Accept-Language': 'it'})
add('page-beyond', 'owner', {'pagination[perpage]': '2', 'pagination[page]': '4'}, 404, INVALID_PAGE)
add('page-not-a-number', 'owner', {'pagination[page]': 'abc'}, 404, INVALID_PAGE)
add('page-empty-is-first', 'owner', {'pagination[page]': ''}, 200, page(ALL))
add('page-size-zero-default', 'owner', {'pagination[perpage]': '0'}, 200, page(ALL))
add('page-size-negative-default', 'owner', {'pagination[perpage]': '-3'}, 200, page(ALL))
add('page-size-text-default', 'owner', {'pagination[perpage]': 'abc'}, 200, page(ALL))
add('page-empty-result-second', 'owner', {'query[generalSearch]': 'zzz', 'pagination[page]': '2'}, 404, INVALID_PAGE)

# all / optimized.
add('all', 'owner', {'all': '1'}, 200, listed(ALL))
add('all-optimized', 'owner', {'all': '1', 'optimized': '1'}, 200, optimized(ALL))
add('all-optimized-duplicates', 'owner', {'all': '1', 'optimized': '1', 'query[tags]': tag('t1', 't2')}, 200,
    optimized(['full', 'full', 'grouped', 'draft']))
add('all-sorted-filtered', 'owner', {'all': '1', 'sort[field]': 'title', 'sort[sort]': 'asc', 'query[generalSearch]': 'o'}, 200,
    listed(['grouped', 'full', 'internal', 'draft']))
add('all-ignores-page', 'owner', {'all': '1', 'pagination[page]': '99'}, 200, listed(ALL))
add('all-not-one-paginates', 'owner', {'all': 'true'}, 200, page(ALL))
add('optimized-alone-paginates', 'owner', {'optimized': '1'}, 200, page(ALL))

# POST: all == '1' answers like GET; otherwise request.data then current_view_course.
add('post-all', 'owner', {'all': '1'}, 200, listed(ALL), method='POST', body={'format': 'pdf'})
add('post-all-optimized-unparsed-body', 'owner', {'all': '1', 'optimized': '1'}, 200, optimized(ALL), method='POST',
    raw_body='{', content_type='application/json')
add('post-empty-body', 'owner', None, 500, SERVER, method='POST', body={})
add('post-unknown-format', 'owner', None, 500, SERVER, method='POST', body={'format': 'csv'})
add('post-list-body', 'owner', None, 500, SERVER, method='POST', body=['pdf'])
add('post-form-unknown-format', 'owner', None, 500, SERVER, method='POST', raw_body='format=xls',
    content_type='application/x-www-form-urlencoded')
add('post-malformed-json', 'owner', None, 400, {'detail': 'JSON parse error - Expecting value: line 1 column 1 (char 0)'}, method='POST',
    raw_body='nope', content_type='application/json')
add('post-filter-error-before-body', 'owner', {'query[status_flag]': 'abc'}, 500, SERVER, method='POST', raw_body='nope',
    content_type='application/json')
add('post-ignores-invalid-page', 'owner', {'pagination[page]': '0'}, 500, SERVER, method='POST', body={})

# POST excel: current_view_course over the whole queryset (pagination ignored).
TYPE_LABEL = {1: 'Default', 2: 'Quote multiple', 3: 'Abbonamento'}  # get_course_type_label, else 'Sconosciuto'
STATUS_LABEL = {1: 'Non visibile', 2: 'Visibile'}  # get_status_label, else 'Sconosciuto'
EXCEL = {'format': 'excel'}


def ddmmyyyy(value):
    """strftime('%d/%m/%Y') of a UTC datetime, '-' for None."""
    return '-' if value is None else f'{value[8:10]}/{value[5:7]}/{value[:4]}'


def enrolled(label):
    """CourseSubscription.objects.filter(course_id=...).count(): live rows, any subscription."""
    return sum(1 for row in COURSE_SUBSCRIPTIONS.values() if row['course'] == label and not row['deleted'])


def export(labels, association=OWN):
    """{'file': get_excel_base64(*rows, columns=columns), 'filename': ...} of current_view_course."""
    rows = [COURSES[label] for label in labels]
    tags = [label for label, tag in TAGS.items() if tag['association'] == association]  # seed (heap) order
    columns = ['Nome', 'Tipologia', 'Visibilità', 'Data inizio', 'Data fine', 'Data creazione', 'In evidenza', 'Strutture',
               'Iscritti', 'Tag'] + [TAGS[label]['name'] for label in tags]
    data = [[row['title'] for row in rows],
            [TYPE_LABEL.get(row['type'], 'Sconosciuto') for row in rows],
            [STATUS_LABEL.get(row['status'], 'Sconosciuto') for row in rows],
            [ddmmyyyy(row['start']) for row in rows],
            [ddmmyyyy(row['end']) for row in rows],
            [ddmmyyyy(row['created']) for row in rows],
            ['In evidenza' if row['pinned'] else '-' for row in rows],
            [', '.join(str(LOCATIONS[l]['title']) for l in row['locations']) for row in rows],
            [{'int': str(enrolled(label))} for label in labels],
            [', '.join(TAGS[t]['name'] for t in row['tags']) for row in rows]]
    data += [['assegnato' if tag in row['tags'] else '' for row in rows] for tag in tags]
    return {'file': {'$xlsx': {'engine': 'excel_utils', 'columns': columns, 'data': data}},
            'filename': f'[$today] Corsi filtrati - {DENOMINATION[association]}.xlsx'}


add('excel-owner', 'owner', None, 200, export(ALL), method='POST', body=EXCEL)
add('excel-full-collaborator', 'full_collaborator', None, 200, export(ALL), method='POST', body=EXCEL)
add('excel-admin-impersonating-owner', 'admin', None, 200, export(ALL), method='POST', body=EXCEL,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('excel-owner-session', 'owner', None, 200, export(ALL), method='POST', body=EXCEL,
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']})
add('excel-foreign-owner-duplicate-columns', 'foreign_owner', None, 200, export(['abroad', 'abroad_draft'], FOREIGN), method='POST',
    body=EXCEL)
add('excel-form-body', 'owner', None, 200, export(ALL), method='POST', raw_body='format=excel',
    content_type='application/x-www-form-urlencoded')
add('excel-form-last-format', 'owner', None, 200, export(ALL), method='POST', raw_body='format=pdf&format=excel',
    content_type='application/x-www-form-urlencoded')
add('excel-extra-fields-ignored', 'owner', None, 200, export(ALL), method='POST', body={'format': 'excel', 'type': 'pdf', 'all': '1'})
add('excel-ignores-pagination', 'owner', {'pagination[perpage]': '1', 'pagination[page]': '0'}, 200, export(ALL), method='POST',
    body=EXCEL)
add('excel-group-own', 'owner', None, 200, export(['grouped']), method='POST', body=EXCEL, headers=group('own_group'))
add('excel-group-foreign-empty', 'owner', None, 200, export([]), method='POST', body=EXCEL, headers=group('foreign_group'))
add('excel-search', 'owner', {'query[generalSearch]': 'NUOTO'}, 200, export(['full', 'internal']), method='POST', body=EXCEL)
add('excel-search-empty', 'owner', {'query[generalSearch]': 'zzz'}, 200, export([]), method='POST', body=EXCEL)
add('excel-tags-or-duplicates', 'owner', {'query[tags]': tag('t1', 't2')}, 200, export(['full', 'full', 'grouped', 'draft']),
    method='POST', body=EXCEL)
add('excel-tags-and', 'owner', {'query[tags]': tag('t1', 't2'), 'query[tags_and]': '1'}, 200, export(['full']), method='POST',
    body=EXCEL)
add('excel-status-draft', 'owner', {'query[status_flag]': '1'}, 200, export(['draft']), method='POST', body=EXCEL)
add('excel-status-overflow', 'owner', {'query[status_flag]': '40000'}, 200, export([]), method='POST', body=EXCEL)
add('excel-subscription-excludes', 'owner', {'subscription_id': sub('s2')}, 200, export(['grouped', 'internal', 'draft', 'old']),
    method='POST', body=EXCEL)
add('excel-sort-title-asc', 'owner', {'sort[field]': 'title', 'sort[sort]': 'asc'}, 200,
    export(['grouped', 'full', 'internal', 'draft', 'old']), method='POST', body=EXCEL)
add('excel-sort-creation-asc', 'owner', {'sort[field]': 'creation_date', 'sort[sort]': 'asc'}, 200,
    export(['old', 'draft', 'internal', 'grouped', 'full']), method='POST', body=EXCEL)
add('excel-sort-random-one-row', 'owner', {'sort[field]': '?', 'sort[sort]': 'asc', 'query[status_flag]': '3'}, 200,
    export(['internal']), method='POST', body=EXCEL)
add('excel-sort-unknown-field', 'owner', {'sort[field]': 'nope', 'sort[sort]': 'asc'}, 500, SERVER, method='POST', body=EXCEL)
# A name past a known field is compiled when current_view_course iterates the
# rows: an invalid one raises there (a 500).
add('excel-sort-unknown-transform', 'owner', {'sort[field]': 'title__nope', 'sort[sort]': 'asc'}, 500, SERVER, method='POST',
    body=EXCEL)
add('excel-format-case-sensitive', 'owner', None, 500, SERVER, method='POST', body={'format': 'EXCEL'})
add('excel-format-list', 'owner', None, 500, SERVER, method='POST', body={'format': ['excel']})
add('excel-string-body', 'owner', None, 500, SERVER, method='POST', raw_body='"excel"', content_type='application/json')
add('excel-restricted-collaborator', 'restricted_collaborator', None, 403, {'detail': 'Missing permission: association.courses.read'},
    method='POST', body=EXCEL)
add('excel-athlete-lists', 'athlete', {'sport_association_id': OWN}, 200, listed(ACTIVE), method='POST', body=EXCEL)
add('excel-owner-without-association', 'owner_without_association', None, 500, SERVER, method='POST', body=EXCEL)

# Athletes.
athlete_session = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
add('athlete-missing-association', 'athlete', None, 403, {'detail': 'Missing sport_association_id query param.'})
add('athlete-malformed-association', 'athlete', {'sport_association_id': 'abc'}, 400, ['badly formed hexadecimal UUID string'])
add('athlete-empty-association', 'athlete', {'sport_association_id': ''}, 400, ['badly formed hexadecimal UUID string'])
add('athlete-own', 'athlete', {'sport_association_id': OWN}, 200, listed(ACTIVE))
add('athlete-braced-uppercase', 'athlete', {'sport_association_id': '{' + OWN.upper() + '}'}, 200, listed(ACTIVE))
add('athlete-foreign', 'athlete', {'sport_association_id': FOREIGN}, 200, listed(['abroad']))
add('athlete-unknown', 'athlete', {'sport_association_id': UNKNOWN}, 200, listed([]))
add('athlete-group', 'athlete', {'sport_association_id': OWN}, 200, listed(['grouped']), headers=group('own_group'))
add('athlete-owner-parameters-ignored', 'athlete', {'sport_association_id': OWN, 'query[status_flag]': 'abc', 'all': '1',
    'sort[field]': 'nope', 'sort[sort]': 'asc'}, 200, listed(ACTIVE))
add('athlete-post', 'athlete', {'sport_association_id': OWN}, 200, listed(ACTIVE), method='POST', body={})
add('admin-impersonating-athlete', 'admin', {'sport_association_id': OWN}, 200, listed(ACTIVE),
    headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']})
add('owner-scoped-athlete-own', 'owner', {'sport_association_id': OWN}, 200, listed(ACTIVE), headers=athlete_session)
add('owner-scoped-athlete-foreign', 'owner', {'sport_association_id': FOREIGN}, 403,
    {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, headers=athlete_session)
add('owner-scoped-athlete-missing', 'owner', None, 403, {'detail': 'Missing sport_association_id query param.'}, headers=athlete_session)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Course list authored sequence', 'ids': IDS, 'tags': TAGS,
                              'locations': LOCATIONS, 'courses': COURSES, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS,
                              'course_subscriptions': COURSE_SUBSCRIPTIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
