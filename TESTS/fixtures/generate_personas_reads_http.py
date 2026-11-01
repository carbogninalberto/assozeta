"""Author the personas reads HTTP sequence (personas-reads.md).

AssociateViewSet (BE/application/views/personas_views.py) maps, with
`as_view({...})`, `personas/list` (list), `personas/all` (all),
`personas/all-tutors` (all_tutors), `personas/all-with-subscriptions`
(all_with_subscriptions) and `personas/<str:pk>/info` (info). The class is a
ModelViewSet with its own `delete()`, so every route also answers DELETE, and
the GET mapping adds HEAD: Allow is GET, HEAD, DELETE, OPTIONS. The class has no
docstring: OPTIONS is `Associate` with an empty description.

Identity, impersonation and the collaborator registry run first (the registry
maps every route but all-with-subscriptions, which restricted collaborators get
as an unmapped endpoint). `request.user.sport_association` is the User
cached_property: athletes raise DRF PermissionDenied('User is not a sport
association'); owners whose association is missing or soft-deleted raise
DoesNotExist (500).

get_queryset (list, info, and DELETE through get_object) reads the
association's live associates through the soft-delete, group-aware manager
(`X-Group-Id`; a malformed id is ignored), annotates is_tutor_annotated (an
Exists over any relation naming the row as tutor) and tutors_count
(Count('tutor_relations')), prefetches tutor_relations__tutor (base managers:
deleted tutors are listed), then:
- query[generalSearch]: smart_search -> sqlite_search: strip().lower(), \\w+
  words, each word icontains over every concrete non-relation field plus
  tutors__{first_name,last_name,tax_code,email,phone} (a second relation join:
  tutors_count then counts relation x matching-tutor rows), numeric words also
  matching get_date_variations() on born_date/creation_date, multi-word
  permutations on the name/address/city/notes fields, a relevance CASE on
  first_name/last_name/tax_code (10 iexact, 7 istartswith, 4 icontains, else
  1) ordering -relevance, and distinct();
- query[is_tutor] (non-empty) through BooleanField.to_python ('t', 'True', '1',
  'f', 'False', '0'; anything else is a ValidationError 500);
- query[type] comma tokens 'athletes'/'tutors';
- query[minAge]/query[maxAge] (present, even empty) through int() and
  today.replace(year=today.year - n) (errors are 500s);
- sort[field] + sort[sort] (both non-empty): tutors -> tutors_count, age ->
  born_date reversed, full_name -> last_name; then order_by(field or -field):
  an unknown name is a FieldError 500, '?' is random.
list paginates with KTDatatablePagination (perpage int() > 0 else 10; page ''
is 1, 'last', else int() within the pages, otherwise 404 Invalid page.) and
renders AssociateSearchSerializer with meta {total, page, pages, perpage (the
page size used), rowIds}.

all and all-tutors ignore the parameters: Associate.objects for the
association, unordered ({'data': [...]} and a bare list). all-with-subscriptions
reads the association's live, unarchived, current subscriptions (group-aware on
the subscription) whose associate is live, keyed by associate id.

info runs get_object(): the queryset above, then pk (a value the UUID field
rejects is a bare 404 Not found., a miss 'No Associate matches the given
query.'), and renders AssociateSerializer: declared fields, '__all__' model
fields, nested tutors (BasicAssociateSerializer, whose nested tutors are the M2M
manager: soft-delete and group filtered), family_members (live, group-aware),
unordered lists.

DELETE on a route without pk runs get_queryset (athletes 403) and then fails
get_object's lookup-kwarg assertion (500); DELETE on info looks the row up
like info (404s) and then writes, which this family does not author (refused in
Go). Ages and is_minor_now depend on date.today(): the contract resolves the
{'$age': born} and {'$minor': born} placeholders with Django's formula. The age
filters' expectations hold for run dates from 2026-11 to 2030-03. Personal data
is synthetic. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('personas_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
SERVER = '<stable-server-error-page>'
# Hex letters only after the fixed version/variant digits, so numeric searches
# never match a seeded id.
_letters = iter(f'{a}{b}' for a in 'abcdef' for b in 'abcdef')


def uid(kind):
    return f'{kind}-eeee-4eee-8eee-eeeeeeeeee{next(_letters)}'


FIXTURE_ASSOCIATE = 'a1000000-0000-4000-8000-000000700101'  # seeded by the shared identity fixture
F1, F2 = uid('fa111111'), uid('fa222222')
DEFAULTS = {'picture_path': None, 'first_name': None, 'last_name': None, 'sex': 'A', 'tax_code': None, 'born_date': None,
            'born_city': None, 'address_city': None, 'address': None, 'address_cap': None, 'nationality': 'Italiana',
            'nationality_residence': 'Italia', 'email': None, 'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None,
            'phone_label': None, 'phone_2_label': None, 'phone_3_label': None, 'phone_4_label': None, 'is_minor': False,
            'disabled': False, 'draft': False, 'deleted': False, 'family_role': None, 'id_number': None, 'migrated': False,
            'notes': None, 'user': None, 'association': OWN, 'group': None, 'family': None}


def person(label, n, **values):
    row = {**DEFAULTS, **values, 'id': uid('a55c1a7e'), 'created': f'2024-05-{20 + n}T07:00:00Z'}
    return label, row


ASSOCIATES = dict([
    person('anna', 1, first_name='Anna', last_name='Bassi', sex='F', tax_code='BSSNNA10C52L781X', born_date='2010-03-12',
           born_city='Gallipoli', address_city='Verona', address='Via Roma 1', address_cap='37131', email='anna.bassi@example.test',
           phone='3330000001', phone_label='mamma', is_minor=True, family=F1, family_role='figlia', id_number='CA00AA',
           notes='nuoto', picture_path='https://cdn.example.test/anna.jpg'),
    person('bruno', 2, first_name='Bruno', last_name='Bellini', sex='M', tax_code='BLLBRN80L01F205Z', born_date='1980-07-01',
           email='bruno.bellini@example.test', family=F1, family_role='padre'),
    person('carla', 3, first_name='Carla', last_name='Conti', sex='F', tax_code='CNTCRL82B42H501U', born_date='1982-02-02',
           email='carla.conti@example.test', nationality=None),
    person('dario', 4, first_name='Dario', last_name='Esposito', sex='M', tax_code='SPSDRA00A15F839K', born_date='2000-01-15',
           email='dario.esposito@example.test', group=OWN_GROUP, family=F2, draft=True),
    person('elena', 5, first_name='Elena', last_name='Ferri', sex='F', notes='100% presente!', disabled=True),
    person('galli', 6, last_name='Galli', sex='M', tax_code='GLLXXX95E05D704Y', born_date='1995-05-05', group=OWN_GROUP,
           migrated=True),
    person('ivo', 7, first_name='Ivo', last_name='Lodi', sex='M', tax_code='LDOVIO05H06D704A', born_date='2005-06-06',
           born_city='Forlì'),
    person('gino', 8, first_name='Gino', last_name='Neri', sex='M', tax_code='NREGNI70A01F205C', born_date='1970-01-01',
           deleted=True, family=F1),
    person('hugo', 9, first_name='Hugo', last_name='Esterno', association=FOREIGN, born_date='1999-09-19'),
])
# The shared fixture's associate (user: athlete); the adapter pins its born date and creation date.
BASE = {**DEFAULTS, 'id': FIXTURE_ASSOCIATE, 'first_name': 'Athlete', 'last_name': 'Fixture', 'user': IDS['athlete'],
        'born_date': '1990-09-09', 'created': '2024-05-29T07:00:00Z'}
ALL_ROWS = {**ASSOCIATES, 'fixture': BASE}
# (associate, tutor, is_primary): the through model's first FK is the M2M source.
RELATIONS = [('anna', 'bruno', True), ('anna', 'carla', False), ('bruno', 'carla', True), ('bruno', 'gino', False),
             ('carla', 'elena', False), ('dario', 'gino', True), ('gino', 'elena', False),
             ('ivo', 'bruno', False), ('ivo', 'carla', False), ('ivo', 'galli', True)]
SUBSCRIPTIONS = {
    # label: associate, start, end, extra
    'anna_current': ('anna', '2020-01-01', '2099-12-31', {'status_flag': 4, 'type': 1}),
    'bruno_archived': ('bruno', '2020-01-01', '2099-12-31', {'archived': True}),
    'carla_deleted': ('carla', '2020-01-01', '2099-12-31', {'deleted': True}),
    'dario_expired': ('dario', '2019-01-01', '2021-12-31', {}),
    'elena_future': ('elena', '2099-01-01', '2099-12-31', {}),
    'gino_current': ('gino', '2020-01-01', '2099-12-31', {}),
    'galli_grouped': ('galli', '2020-01-01', '2099-12-31', {'group': OWN_GROUP, 'status_flag': 2}),
    'ivo_open_end': ('ivo', '2020-01-01', None, {}),
    'nobody_current': (None, '2020-01-01', '2099-12-31', {}),
    'hugo_foreign': ('hugo', '2020-01-01', '2099-12-31', {'association': FOREIGN}),
}
SUBSCRIPTION_IDS = {label: uid('5b5b5b5b') for label in SUBSCRIPTIONS}
A = {label: row['id'] for label, row in ALL_ROWS.items()}
VISIBLE = ['anna', 'bruno', 'carla', 'dario', 'elena', 'galli', 'ivo', 'fixture']
TUTORS = sorted({tutor for _, tutor, _ in RELATIONS})


def live(label, group=None):
    row = ALL_ROWS[label]
    return not row['deleted'] and (group is None or row['group'] == group)


# --- serializers, authored from personas_serializers.py -----------------------------------------
def age(label):
    born = ALL_ROWS[label]['born_date']
    return {'$age': born} if born else 0


def minor(label):
    born = ALL_ROWS[label]['born_date']
    return {'$minor': born} if born else True


def search_row(label):
    """AssociateSearchSerializer; tutors are the prefetched relations (base managers)."""
    row = ALL_ROWS[label]
    tutors = [{'tutor': {'first_name': ALL_ROWS[t]['first_name'], 'last_name': ALL_ROWS[t]['last_name'], 'associate_id': A[t]}}
              for a, t, _ in RELATIONS if a == label]
    return {'associate_id': row['id'], 'first_name': row['first_name'], 'last_name': row['last_name'], 'born_date': row['born_date'],
            'is_tutor': label in TUTORS, 'age': age(label), 'notes': row['notes'], 'tutors': tutors, 'picture_path': row['picture_path']}


MODEL_FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date', 'born_city', 'address_city', 'address',
                'address_cap', 'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
                'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role',
                'id_number')


def model_fields(label):
    row = ALL_ROWS[label]
    return {**{k: row[k] for k in MODEL_FIELDS}, 'creation_date': row['created'], 'migrated': row['migrated'], 'notes': row['notes']}


def relations_fields(label):
    row = ALL_ROWS[label]
    return {'user': row['user'], 'sport_association': row['association'], 'group': row['group'], 'family': row['family']}


def full_name(label):
    row = ALL_ROWS[label]
    return f"{row['first_name']} {row['last_name']}".strip()


def m2m_tutors(label, group):
    """associate.tutors.all(): the M2M manager is SoftDeleteGroupAwareManager."""
    return [t for a, t, _ in RELATIONS if a == label and live(t, group)]


def basic_tutor(label, group):
    """BasicTutorsSerializer: '__all__' plus full_name; tutors is the M2M pk list."""
    return {'associate_id': A[label], 'full_name': full_name(label), **model_fields(label), **relations_fields(label),
            'tutors': [A[t] for t in m2m_tutors(label, group)]}


def basic_associate(label, group):
    """BasicAssociateSerializer: declared tutors nest BasicTutorsSerializer over the M2M manager."""
    return {'associate_id': A[label], 'full_name': full_name(label),
            'tutors': [basic_tutor(t, group) for t in m2m_tutors(label, group)], **model_fields(label), **relations_fields(label)}


def info(label, group=None):
    """AssociateSerializer; tutors are the prefetched relations (base manager), family_members are live and grouped."""
    row = ALL_ROWS[label]
    family = [A[other] for other in ALL_ROWS if other != label and row['family'] and ALL_ROWS[other]['family'] == row['family']
              and live(other, group)]
    tutors = [{'tutor': basic_associate(t, group), 'is_primary': primary} for a, t, primary in RELATIONS if a == label]
    return {'associate_id': row['id'], 'full_name': full_name(label), 'is_minor_now': minor(label), 'is_tutor': label in TUTORS,
            'age': age(label), 'family_members': family, 'tutors': tutors, **model_fields(label), **relations_fields(label)}


def page(labels, *, number=1, size=10, total=None, pages=None):
    total = len(labels) if total is None else total
    pages = pages or max(1, -(-total // size))
    chunk = labels[(number - 1) * size:number * size] if total == len(labels) else labels
    return {'data': [search_row(label) for label in chunk],
            'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': size, 'rowIds': [A[label] for label in chunk]}}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, ordered=False):
    cases.append({'name': f'personas-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'ordered': ordered})


LIST, ALL, TUTOR_LIST, WITH_SUBS = '/personas/list', '/personas/all', '/personas/all-tutors', '/personas/all-with-subscriptions'
info_path = lambda label: f'/personas/{A[label]}/info'
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
NO_MATCH = {'detail': 'No Associate matches the given query.'}
INVALID_PAGE = {'detail': 'Invalid page.'}
missing = lambda key: {'detail': f'Missing permission: {key}'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
OPTIONS = {'name': 'Associate', 'description': '', 'renders': ['application/json'],
           'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}

OWN_ALL = [{'associate_id': A[k], 'first_name': ALL_ROWS[k]['first_name'], 'last_name': ALL_ROWS[k]['last_name']} for k in VISIBLE]
GROUP_ALL = [{'associate_id': A[k], 'first_name': ALL_ROWS[k]['first_name'], 'last_name': ALL_ROWS[k]['last_name']}
             for k in VISIBLE if ALL_ROWS[k]['group'] == OWN_GROUP]


def with_subscriptions(labels):
    data = {}
    for label in labels:
        associate, _, _, extra = SUBSCRIPTIONS[label]
        row = ALL_ROWS[associate]
        data[row['id']] = {'subscription_id': SUBSCRIPTION_IDS[label], 'associate_id': row['id'], 'first_name': row['first_name'],
                           'last_name': row['last_name'], 'email': row['email'], 'tax_code': row['tax_code'],
                           'subscription_status': extra.get('status_flag', 1), 'type': extra.get('type', 2)}
    return {'data': data}


# --- identity, methods and OPTIONS on every route --------------------------------------------------
ROUTES = (('list', LIST, page(VISIBLE), 'association.personas.read'), ('all', ALL, {'data': OWN_ALL}, 'association.personas.read'),
          ('all-tutors', TUTOR_LIST, OWN_ALL, 'association.personas.read'),
          ('with-subs', WITH_SUBS, with_subscriptions(['anna_current', 'galli_grouped']), None),
          ('info', info_path('anna'), {**info('anna')}, 'association.personas.read'))
for kind, path, owner_body, permission in ROUTES:
    add(f'{kind}-anonymous', 'anonymous', path, 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-admin-without-session', 'admin', path, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-restricted-collaborator', 'restricted_collaborator', path, 403, missing(permission) if permission else unmapped(path))
    add(f'{kind}-owner-scoped-athlete-denied', 'owner', path, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
        headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
    add(f'{kind}-athlete', 'athlete', path, 403, NOT_ASSOCIATION)
    add(f'{kind}-owner-without-association', 'owner_without_association', path, 500, SERVER)
    add(f'{kind}-deleted-association-owner', 'deleted_association_owner', path, 500, SERVER)
    add(f'{kind}-owner', 'owner', path, 200, owner_body)
    add(f'{kind}-full-collaborator', 'full_collaborator', path, 200, owner_body)
    add(f'{kind}-admin-impersonating-owner', 'admin', path, 200, owner_body, headers=ADMIN_OWNER)
    add(f'{kind}-post', 'owner', path, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
    add(f'{kind}-patch', 'owner', path, 405, {'detail': 'Method "PATCH" not allowed.'}, method='PATCH')
    add(f'{kind}-options', 'owner', path, 200, OPTIONS, method='OPTIONS')
    if kind != 'info':
        # The viewset's delete(): get_queryset, then get_object's missing lookup kwarg.
        add(f'{kind}-delete-without-pk', 'owner', path, 500, SERVER, method='DELETE')
        add(f'{kind}-delete-athlete', 'athlete', path, 403, NOT_ASSOCIATION, method='DELETE')

# --- foreign association and groups on the unpaginated routes ---
add('list-foreign-owner', 'foreign_owner', LIST, 200, page(['hugo']))
add('all-foreign-owner', 'foreign_owner', ALL, 200, {'data': [{'associate_id': A['hugo'], 'first_name': 'Hugo', 'last_name': 'Esterno'}]})
add('all-tutors-foreign-owner', 'foreign_owner', TUTOR_LIST, 200,
    [{'associate_id': A['hugo'], 'first_name': 'Hugo', 'last_name': 'Esterno'}])
add('with-subs-foreign-owner', 'foreign_owner', WITH_SUBS, 200, with_subscriptions(['hugo_foreign']))
add('all-group', 'owner', ALL, 200, {'data': GROUP_ALL}, headers={'X-Group-Id': OWN_GROUP})
add('all-group-foreign', 'owner', ALL, 200, {'data': []}, headers={'X-Group-Id': FOREIGN_GROUP})
add('all-group-malformed-ignored', 'owner', ALL, 200, {'data': OWN_ALL}, headers={'X-Group-Id': 'abc'})
add('all-tutors-group', 'owner', TUTOR_LIST, 200, GROUP_ALL, headers={'X-Group-Id': OWN_GROUP})
add('all-ignores-parameters', 'owner', ALL + '?query[minAge]=abc&sort[field]=nope&sort[sort]=asc', 200, {'data': OWN_ALL})
add('all-tutors-ignores-parameters', 'owner', TUTOR_LIST + '?query[is_tutor]=nope', 200, OWN_ALL)
add('with-subs-group', 'owner', WITH_SUBS, 200, with_subscriptions(['galli_grouped']), headers={'X-Group-Id': OWN_GROUP})
add('with-subs-group-foreign', 'owner', WITH_SUBS, 200, {'data': {}}, headers={'X-Group-Id': FOREIGN_GROUP})
add('with-subs-group-malformed-ignored', 'owner', WITH_SUBS, 200, with_subscriptions(['anna_current', 'galli_grouped']),
    headers={'X-Group-Id': 'not-a-uuid'})
add('with-subs-delete-restricted-unmapped', 'restricted_collaborator', WITH_SUBS, 403, unmapped(WITH_SUBS), method='DELETE')

# --- list: groups, filters, sorting and pagination ---
add('list-group', 'owner', LIST, 200, page(['dario', 'galli']), headers={'X-Group-Id': OWN_GROUP})
add('list-group-foreign', 'owner', LIST, 200, page([]), headers={'X-Group-Id': FOREIGN_GROUP})
add('list-group-malformed-ignored', 'owner', LIST, 200, page(VISIBLE), headers={'X-Group-Id': '{nope}'})
NON_TUTORS = [k for k in VISIBLE if k not in TUTORS]
VISIBLE_TUTORS = [k for k in VISIBLE if k in TUTORS]
for value, labels in (('1', VISIBLE_TUTORS), ('True', VISIBLE_TUTORS), ('t', VISIBLE_TUTORS), ('0', NON_TUTORS),
                      ('False', NON_TUTORS), ('f', NON_TUTORS), ('', VISIBLE)):
    add(f'list-is-tutor-{value or "empty"}', 'owner', LIST + f'?query[is_tutor]={value}', 200, page(labels))
add('list-is-tutor-true-lowercase', 'owner', LIST + '?query[is_tutor]=true', 500, SERVER)
add('list-is-tutor-yes', 'owner', LIST + '?query[is_tutor]=yes', 500, SERVER)
for value, labels in (('tutors', VISIBLE_TUTORS), ('athletes', NON_TUTORS), ('athletes,tutors', VISIBLE), ('tutors,other', VISIBLE_TUTORS),
                      ('other', VISIBLE), ('Tutors', VISIBLE)):
    add(f'list-type-{value.replace(",", "-")}', 'owner', LIST + f'?query[type]={value}', 200, page(labels))
add('list-type-and-is-tutor', 'owner', LIST + '?query[type]=athletes&query[is_tutor]=1', 200, page([]))
add('list-min-age', 'owner', LIST + '?query[minAge]=35', 200, page(['bruno', 'carla', 'fixture']))
add('list-max-age', 'owner', LIST + '?query[maxAge]=20', 200, page(['anna']))
add('list-age-window', 'owner', LIST + '?query[minAge]=15&query[maxAge]=25', 200, page(['anna', 'ivo']))
add('list-min-age-python-int', 'owner', LIST + '?query[minAge]=%20%D9%A3%D9%A5%20', 200, page(['bruno', 'carla', 'fixture']))
add('list-min-age-negative', 'owner', LIST + '?query[minAge]=-5', 200, page([k for k in VISIBLE if k != 'elena']))
add('list-min-age-empty', 'owner', LIST + '?query[minAge]=', 500, SERVER)
add('list-min-age-text', 'owner', LIST + '?query[minAge]=abc', 500, SERVER)
add('list-max-age-out-of-range', 'owner', LIST + '?query[maxAge]=10000', 500, SERVER)
add('list-min-age-float', 'owner', LIST + '?query[minAge]=3.5', 500, SERVER)

BY_LAST = ['anna', 'bruno', 'carla', 'dario', 'elena', 'fixture', 'galli', 'ivo']
add('list-sort-last-name', 'owner', LIST + '?sort[field]=last_name&sort[sort]=asc', 200, page(BY_LAST), ordered=True)
add('list-sort-last-name-desc', 'owner', LIST + '?sort[field]=last_name&sort[sort]=desc', 200, page(BY_LAST[::-1]), ordered=True)
add('list-sort-other-direction-is-desc', 'owner', LIST + '?sort[field]=last_name&sort[sort]=up', 200, page(BY_LAST[::-1]), ordered=True)
add('list-sort-full-name', 'owner', LIST + '?sort[field]=full_name&sort[sort]=asc', 200, page(BY_LAST), ordered=True)
add('list-sort-negated-field', 'owner', LIST + '?sort[field]=-last_name&sort[sort]=asc', 200, page(BY_LAST[::-1]), ordered=True)
add('list-sort-double-negation', 'owner', LIST + '?sort[field]=-last_name&sort[sort]=desc', 500, SERVER)
add('list-sort-without-direction', 'owner', LIST + '?sort[field]=last_name', 200, page(VISIBLE))
add('list-sort-first-name-nulls-last', 'owner', LIST + '?sort[field]=first_name&sort[sort]=asc', 200,
    page(['anna', 'fixture', 'bruno', 'carla', 'dario', 'elena', 'ivo', 'galli']), ordered=True)
BY_BORN = ['bruno', 'carla', 'fixture', 'galli', 'dario', 'ivo', 'anna', 'elena']  # ascending, NULL last
add('list-sort-age-asc', 'owner', LIST + '?sort[field]=age&sort[sort]=asc', 200, page(BY_BORN[::-1]), ordered=True)
add('list-sort-age-desc', 'owner', LIST + '?sort[field]=age&sort[sort]=desc', 200, page(BY_BORN), ordered=True)
add('list-sort-age-other', 'owner', LIST + '?sort[field]=age&sort[sort]=other', 200, page(BY_BORN), ordered=True)
add('list-sort-born-date', 'owner', LIST + '?sort[field]=born_date&sort[sort]=asc', 200, page(BY_BORN), ordered=True)
add('list-sort-tutors-desc', 'owner', LIST + '?sort[field]=tutors&sort[sort]=desc&pagination[perpage]=1', 200,
    page(['ivo'], size=1, total=8, pages=8), ordered=True)
add('list-sort-tutors-count', 'owner', LIST + '?sort[field]=tutors_count&sort[sort]=desc&pagination[perpage]=1', 200,
    page(['ivo'], size=1, total=8, pages=8), ordered=True)
BY_CREATED = sorted(VISIBLE, key=lambda k: ALL_ROWS[k]['created'])
add('list-sort-creation-date', 'owner', LIST + '?sort[field]=creation_date&sort[sort]=desc', 200, page(BY_CREATED[::-1]), ordered=True)
BY_ID = sorted(VISIBLE, key=lambda k: A[k])
add('list-sort-pk', 'owner', LIST + '?sort[field]=pk&sort[sort]=asc', 200, page(BY_ID), ordered=True)
add('list-sort-associate-id-desc', 'owner', LIST + '?sort[field]=associate_id&sort[sort]=desc', 200, page(BY_ID[::-1]), ordered=True)
add('list-sort-random', 'owner', LIST + '?sort[field]=?&sort[sort]=asc', 200, page(VISIBLE))
add('list-sort-random-desc', 'owner', LIST + '?sort[field]=?&sort[sort]=desc', 500, SERVER)
add('list-sort-unknown', 'owner', LIST + '?sort[field]=nope&sort[sort]=asc', 500, SERVER)
add('list-sort-relevance-without-search', 'owner', LIST + '?sort[field]=relevance&sort[sort]=asc', 500, SERVER)
add('list-sort-dotted', 'owner', LIST + '?sort[field]=last_name.x&sort[sort]=asc', 500, SERVER)

add('list-page-two', 'owner', LIST + '?sort[field]=last_name&sort[sort]=asc&pagination[perpage]=3&pagination[page]=2', 200,
    page(BY_LAST, number=2, size=3), ordered=True)
add('list-page-last', 'owner', LIST + '?sort[field]=last_name&sort[sort]=asc&pagination[perpage]=3&pagination[page]=last', 200,
    page(BY_LAST, number=3, size=3), ordered=True)
add('list-page-empty-is-first', 'owner', LIST + '?sort[field]=last_name&sort[sort]=asc&pagination[perpage]=3&pagination[page]=', 200,
    page(BY_LAST, number=1, size=3), ordered=True)
add('list-page-python-int', 'owner', LIST + '?sort[field]=last_name&sort[sort]=asc&pagination[perpage]=%203%20&pagination[page]=+2', 200,
    page(BY_LAST, number=2, size=3), ordered=True)
add('list-page-beyond', 'owner', LIST + '?pagination[perpage]=3&pagination[page]=4', 404, INVALID_PAGE)
add('list-page-zero', 'owner', LIST + '?pagination[page]=0', 404, INVALID_PAGE)
add('list-page-text', 'owner', LIST + '?pagination[page]=abc', 404, INVALID_PAGE)
add('list-page-float', 'owner', LIST + '?pagination[page]=1.0', 404, INVALID_PAGE)
add('list-perpage-zero-default', 'owner', LIST + '?pagination[perpage]=0', 200, page(VISIBLE))
add('list-perpage-negative-default', 'owner', LIST + '?pagination[perpage]=-2', 200, page(VISIBLE))
add('list-perpage-text-default', 'owner', LIST + '?pagination[perpage]=ten', 200, page(VISIBLE))
HUGE = 10 ** 30
add('list-perpage-huge', 'owner', LIST + f'?pagination[perpage]={HUGE}', 200,
    {**page(VISIBLE), 'meta': {**page(VISIBLE)['meta'], 'perpage': HUGE}})
add('list-empty-page-one', 'owner', LIST + '?query[type]=athletes&query[is_tutor]=1&pagination[page]=1', 200, page([]))
add('list-empty-page-two', 'owner', LIST + '?query[type]=athletes&query[is_tutor]=1&pagination[page]=2', 404, INVALID_PAGE)

# --- list: generalSearch (smart_search) ---
S = LIST + '?query[generalSearch]='
add('list-search-last-name', 'owner', S + 'bassi', 200, page(['anna']), ordered=True)
add('list-search-lowercased', 'owner', S + 'BASSI', 200, page(['anna']), ordered=True)
add('list-search-relevance-order', 'owner', S + 'ferri', 200, page(['elena', 'carla']), ordered=True)
add('list-search-relevance-exact', 'owner', S + 'conti&pagination[perpage]=1', 200, page(['carla'], size=1, total=4, pages=4), ordered=True)
add('list-search-relevance-prefix', 'owner', S + 'bel&pagination[perpage]=1', 200, page(['bruno'], size=1, total=3, pages=3), ordered=True)
add('list-search-relevance-contains', 'owner', S + 'lini&pagination[perpage]=1', 200, page(['bruno'], size=1, total=3, pages=3),
    ordered=True)
add('list-search-tutor-fields-sorted', 'owner', S + 'conti&sort[field]=last_name&sort[sort]=asc', 200,
    page(['anna', 'bruno', 'carla', 'ivo']), ordered=True)
# tutors_count counts relation x matching-tutor rows: anna 2 x 2 (own field), ivo 3 x 1 (one tutor), galli 0.
add('list-search-tutors-count-join', 'owner', S + 'galli&sort[field]=tutors&sort[sort]=desc', 200, page(['anna', 'ivo', 'galli']),
    ordered=True)
add('list-search-words', 'owner', S + 'Anna%20Bassi', 200, page(['anna']), ordered=True)
add('list-search-words-punctuation', 'owner', S + 'bassi,%20anna', 200, page(['anna']), ordered=True)
# Booleans match as 'true'/'false' text: is_minor (anna), draft (dario), disabled (elena), migrated (galli).
add('list-search-boolean-text', 'owner', S + 'true', 200, page(['anna', 'dario', 'elena', 'galli']))
add('list-search-digits-in-id-and-notes', 'owner', S + '100', 200, page(['elena', 'fixture']))
add('list-search-underscore-escaped', 'owner', S + 'presente_', 200, page([]))
add('list-search-percent-only', 'owner', S + '%25', 200, page(VISIBLE))
add('list-search-blank', 'owner', S + '%20%20%20', 200, page(VISIBLE))
add('list-search-date-variation', 'owner', S + '99%2012/03/2010', 200, page(['anna']), ordered=True)
add('list-search-date-variation-short-year', 'owner', S + '99%2012.03.10', 200, page(['anna']), ordered=True)
add('list-search-accented', 'owner', S + 'FORL%C3%8C', 200, page(['ivo']), ordered=True)
add('list-search-group', 'owner', S + 'galli', 200, page(['galli']), headers={'X-Group-Id': OWN_GROUP}, ordered=True)
add('list-search-sort-relevance', 'owner', S + 'ferri&sort[field]=relevance&sort[sort]=desc', 200, page(['elena', 'carla']), ordered=True)
# DISTINCT keeps working: Django adds the RANDOM() ordering to the select list.
add('list-search-sort-random', 'owner', S + 'ferri&sort[field]=?&sort[sort]=asc', 200, page(['elena', 'carla']))
add('list-search-and-filters', 'owner', S + 'conti&query[type]=athletes&query[minAge]=15', 200, page(['anna', 'ivo']))

# --- info ---
add('info-tutor-with-deleted-tutor', 'owner', info_path('bruno'), 200, info('bruno'))
add('info-nameless', 'owner', info_path('galli'), 200, info('galli'))
add('info-three-tutors', 'owner', info_path('ivo'), 200, info('ivo'))
add('info-no-born-date', 'owner', info_path('elena'), 200, info('elena'))
add('info-deleted-tutor-and-group-filters', 'owner', info_path('dario'), 200, info('dario'))
add('info-group', 'owner', info_path('dario'), 200, info('dario', OWN_GROUP), headers={'X-Group-Id': OWN_GROUP})
add('info-group-hidden', 'owner', info_path('anna'), 404, NO_MATCH, headers={'X-Group-Id': OWN_GROUP})
add('info-group-foreign', 'owner', info_path('dario'), 404, NO_MATCH, headers={'X-Group-Id': FOREIGN_GROUP})
add('info-group-malformed-ignored', 'owner', info_path('anna'), 200, info('anna'), headers={'X-Group-Id': 'x'})
add('info-shared-fixture-associate', 'owner', info_path('fixture'), 200, info('fixture'))
add('info-deleted', 'owner', info_path('gino'), 404, NO_MATCH)
add('info-foreign', 'owner', info_path('hugo'), 404, NO_MATCH)
add('info-foreign-owner', 'foreign_owner', info_path('hugo'), 200, info('hugo'))
add('info-unknown', 'owner', '/personas/a55c1a7e-eeee-4eee-8eee-eeeeeeeeeeee/info', 404, NO_MATCH)
add('info-malformed', 'owner', '/personas/not-a-uuid/info', 404, {'detail': 'Not found.'})
add('info-uppercase-hex', 'owner', f"/personas/{A['anna'].upper().replace('-', '')}/info", 200, info('anna'))
add('info-urn', 'owner', f"/personas/urn:uuid:{A['anna']}/info", 200, info('anna'))
add('info-queryset-filter-applies', 'owner', info_path('anna') + '?query[is_tutor]=1', 404, NO_MATCH)
add('info-queryset-search-applies', 'owner', info_path('anna') + '?query[generalSearch]=bassi', 200, info('anna'))
add('info-queryset-search-misses', 'owner', info_path('anna') + '?query[generalSearch]=ferri', 404, NO_MATCH)
add('info-queryset-error', 'owner', info_path('anna') + '?query[minAge]=x', 500, SERVER)
add('info-queryset-ordering-error', 'owner', info_path('anna') + '?sort[field]=nope&sort[sort]=asc', 500, SERVER)
add('info-delete-malformed', 'owner', '/personas/not-a-uuid/info', 404, {'detail': 'Not found.'}, method='DELETE')
add('info-delete-unknown', 'owner', '/personas/a55c1a7e-eeee-4eee-8eee-eeeeeeeeeeee/info', 404, NO_MATCH, method='DELETE')
add('info-delete-deleted', 'owner', info_path('gino'), 404, NO_MATCH, method='DELETE')
add('info-delete-foreign', 'owner', info_path('hugo'), 404, NO_MATCH, method='DELETE')
add('info-delete-athlete', 'athlete', info_path('anna'), 403, NOT_ASSOCIATION, method='DELETE')
add('info-delete-queryset-error', 'owner', info_path('anna') + '?query[maxAge]=x', 500, SERVER, method='DELETE')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'Personas list, all, all-tutors, all-with-subscriptions and info authored sequence',
    'ids': IDS, 'associates': ASSOCIATES, 'fixture_associate': {'id': FIXTURE_ASSOCIATE, 'born_date': BASE['born_date'],
                                                                'created': BASE['created']},
    'families': [F1, F2], 'relations': [[A[a], A[t], primary] for a, t, primary in RELATIONS],
    'subscriptions': {SUBSCRIPTION_IDS[label]: {'associate': A[a] if a else None, 'start': start, 'end': end, **extra}
                      for label, (a, start, end, extra) in SUBSCRIPTIONS.items()},
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
