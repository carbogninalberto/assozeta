"""Author the carnet/list HTTP sequence from carnet-list.md.

Associations list their own carnets through CarnetListSerializer (unordered:
the queryset has no ordering). Athletes list carnet subscriptions they hold
directly (user_id) or through a subscription whose associate is theirs,
ordered -creation_date, through CarnetSubscriptionSerializer: the declared
``carnet_id`` UUIDField receives the Carnet instance and renders its str()
("Carnet object (<uuid>)"); the course-subscription relation uses the
soft-delete manager; ``current_year`` compares the subscription dates with
the UTC date. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('carnet_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
PATH = '/carnet/list'
ids = iter(f'a1000000-0000-4000-8000-{880001 + n:012d}' for n in range(40))
CARNETS = {
    'c1': {'id': next(ids), 'association': OWN, 'title': 'Carnet è', 'description': '10 ingressi', 'lessons': 10, 'fee': '50.00',
           'public': True, 'meta': {'b': [1, 2.5], 'a': None}, 'created': '2026-09-01T08:00:00.250000Z'},
    'c2': {'id': next(ids), 'association': OWN, 'title': None, 'description': None, 'lessons': 0, 'fee': '0.00',
           'public': False, 'meta': None, 'created': '2026-09-02T08:00:00Z'},
    'cf': {'id': next(ids), 'association': FOREIGN, 'title': 'Estero', 'description': '', 'lessons': 5, 'fee': '7.50',
           'public': True, 'meta': {}, 'created': '2026-09-03T08:00:00Z'},
}
ASSOCIATES = {
    'mine': {'id': next(ids), 'association': OWN, 'user': 'athlete', 'first_name': 'Atleta è', 'last_name': 'Rossi', 'born_date': '1990-05-05',
             'email': 'atleta@example.test', 'sex': 'F', 'tax_code': 'RSSTLT90E45H501X', 'address': 'Via 1', 'address_cap': '00100',
             'address_city': 'Roma', 'born_city': 'Roma', 'is_minor': False, 'phone': '+39 1', 'family_role': 'madre'},
    'child': {'id': next(ids), 'association': OWN, 'user': 'athlete', 'first_name': 'Figlio', 'last_name': 'Rossi', 'born_date': None,
              'email': None, 'sex': 'M', 'tax_code': None, 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
              'is_minor': True, 'phone': None, 'family_role': None},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'user': 'athlete', 'first_name': 'Atleta', 'last_name': 'Estero', 'born_date': '1991-01-01',
               'email': None, 'sex': 'A', 'tax_code': None, 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
               'is_minor': False, 'phone': None, 'family_role': None},
    # No user: AssociateSerializer.get_user falls back to the association owner.
    'other': {'id': next(ids), 'association': OWN, 'user': None, 'first_name': 'Altro', 'last_name': 'Socio', 'born_date': None,
              'email': None, 'sex': 'A', 'tax_code': None, 'address': None, 'address_cap': None, 'address_city': None, 'born_city': None,
              'is_minor': False, 'phone': None, 'family_role': None},
}
SUBSCRIPTIONS = {
    'current': {'id': next(ids), 'association': OWN, 'user': 'athlete', 'associate': 'mine', 'start': '2000-01-01', 'end': '2099-12-31',
                'year': 'corrente'},
    # Held by the owner, but its associate belongs to the athlete.
    'family_next': {'id': next(ids), 'association': OWN, 'user': 'owner', 'associate': 'child', 'start': '2098-01-01', 'end': '2099-01-01',
                    'year': 'successivo'},
    'abroad_past': {'id': next(ids), 'association': FOREIGN, 'user': 'athlete', 'associate': 'abroad', 'start': '2000-01-01', 'end': '2001-01-01',
                    'year': 'precedente'},
    'other_open': {'id': next(ids), 'association': OWN, 'user': 'owner', 'associate': 'other', 'start': None, 'end': None, 'year': 'corrente'},
}
COURSES = {'swim': {'id': next(ids), 'association': OWN, 'title': 'Nuoto è'}}
COURSE_SUBSCRIPTIONS = {'active': {'id': next(ids), 'course': 'swim', 'subscription': 'current', 'deleted': False},
                        'deleted': {'id': next(ids), 'course': 'swim', 'subscription': 'current', 'deleted': True}}
PAYMENTS = {'p1': {'id': next(ids), 'association': OWN, 'user': 'athlete', 'amount': '50.00'}}
CARNET_SUBSCRIPTIONS = {
    'held': {'id': next(ids), 'carnet': 'c1', 'user': 'athlete', 'subscription': 'current', 'courses': ['active', 'deleted'],
             'payment': 'p1', 'disabled': False, 'meta': {'lessons_left': 9, 'lessons_registry': ['x']}, 'created': '2026-10-03T09:30:00.125000Z'},
    'family': {'id': next(ids), 'carnet': 'c1', 'user': None, 'subscription': 'family_next', 'courses': [], 'payment': None, 'disabled': False,
               'meta': None, 'created': '2026-10-02T09:30:00Z'},
    'abroad': {'id': next(ids), 'carnet': 'cf', 'user': 'athlete', 'subscription': 'abroad_past', 'courses': [], 'payment': None,
               'disabled': False, 'meta': {}, 'created': '2026-10-04T09:30:00Z'},
    'detached': {'id': next(ids), 'carnet': 'c2', 'user': 'athlete', 'subscription': None, 'courses': [], 'payment': None, 'disabled': True,
                 'meta': {'lessons_left': 0}, 'created': '2026-10-01T09:30:00Z'},
    'someone_else': {'id': next(ids), 'carnet': 'c2', 'user': 'owner', 'subscription': 'other_open', 'courses': [], 'payment': None,
                     'disabled': False, 'meta': None, 'created': '2026-10-05T09:30:00Z'},
}
SERVER = '<stable-server-error-page>'


def carnet(label):
    row = CARNETS[label]
    return {'carnet_id': row['id'], 'title': row['title'], 'description': row['description'], 'lessons_number': row['lessons'],
            'creation_date': row['created'], 'fee': row['fee'], 'public': row['public'], 'meta': row['meta']}


def associate(label):
    row = ASSOCIATES[label]
    owner = IDS['owner'] if row['association'] == OWN else IDS['foreign_owner']
    return {'associate_id': row['id'], **{k: row[k] for k in ('address', 'address_cap', 'address_city', 'born_city', 'born_date', 'email',
                                                              'sex', 'first_name', 'last_name', 'is_minor')},
            'sport_association': row['association'], 'tax_code': row['tax_code'], 'user': IDS[row['user']] if row['user'] else owner,
            'phone': row['phone'], 'family': None, 'family_role': row['family_role']}


def held(label):
    row = CARNET_SUBSCRIPTIONS[label]
    linked = [COURSE_SUBSCRIPTIONS[c] for c in row['courses'] if not COURSE_SUBSCRIPTIONS[c]['deleted']]
    subscription = None
    if row['subscription']:
        sub = SUBSCRIPTIONS[row['subscription']]
        subscription = {'subscription_id': sub['id'], 'associate': associate(sub['associate']), 'current_year': sub['year']}
    return {'carnet_subscription_id': row['id'], 'user_id': IDS[row['user']] if row['user'] else None,
            'carnet': {'carnet_id': CARNETS[row['carnet']]['id'], 'title': CARNETS[row['carnet']]['title']},
            'subscription': subscription, 'course_subscription': [c['id'] for c in linked],
            'course': [{'course_id': COURSES[c['course']]['id'], 'course_subscription_id': c['id'], 'course_title': COURSES[c['course']]['title']}
                       for c in linked],
            'carnet_id': f'Carnet object ({CARNETS[row["carnet"]]["id"]})', 'creation_date': row['created'],
            'payment': PAYMENTS[row['payment']]['id'] if row['payment'] else None, 'meta': row['meta']}


cases = []


def add(name, actor, status, response, *, method='GET', headers=None, ordered=True):
    cases.append({'name': f'carnet-list-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
                  'status': status, 'response': response, 'ordered': ordered})


OWNER_LIST = {'data': [carnet('c1'), carnet('c2')]}
ATHLETE_LIST = {'data': [held('abroad'), held('held'), held('family'), held('detached')]}
add('owner', 'owner', 200, OWNER_LIST, ordered=False)
add('full-collaborator-owner-scope', 'full_collaborator', 200, OWNER_LIST, ordered=False)
add('admin-impersonating-owner', 'admin', 200, OWNER_LIST, ordered=False,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('foreign-owner', 'foreign_owner', 200, {'data': [carnet('cf')]}, ordered=False)
add('athlete', 'athlete', 200, ATHLETE_LIST)
add('admin-impersonating-athlete', 'admin', 200, ATHLETE_LIST, headers={'X-Impersonation-Id': '$session:admin_athlete', 'User-Id': IDS['athlete']})
# The owner-scoped session keeps the association's carnets on the athlete's own subscriptions.
add('owner-scoped-athlete', 'owner', 200, {'data': [held('held'), held('family')]},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.carnet.read'})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', 200, {'name': 'Carnet List', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Carnet list authored sequence', 'ids': IDS, 'carnets': CARNETS,
                              'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS, 'courses': COURSES,
                              'course_subscriptions': COURSE_SUBSCRIPTIONS, 'payments': PAYMENTS,
                              'carnet_subscriptions': CARNET_SUBSCRIPTIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
