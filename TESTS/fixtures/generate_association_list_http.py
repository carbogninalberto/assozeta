"""Author the sport-associations/list HTTP sequence from association-list.md.

The runner pins every active association's serialized user, association,
onboarding and billing values to the ones authored here, so complete
expected rows are known before any backend runs. Rows are unordered (the
view iterates an unordered queryset). Run with ``python3 -I`` and commit.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('association_list_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
PATH = '/sport-associations/list'
PLAN = 'a1000000-0000-4000-8000-000000780001'

ASSOCIATIONS = {
    'own_association': {'owner': 'owner', 'association': {'denomination': 'Società sportiva è', 'tax_code': '01234567890',
        'address': 'Via Roma 1', 'address_cap': '00100', 'address_city': 'Roma', 'notes': 'Note interne'},
        'user': {'email': 'list-owner@example.test', 'username': 'list-owner', 'first_name': 'Maria', 'last_name': 'Rossi',
                 'phone': '+39 000', 'lead_sport_association_role': 'presidente', 'lead_sport_association_size': '50-100',
                 'lead_sport_market_channel': 'web', 'date_joined': '2026-01-02T03:04:05.678901Z',
                 'last_login': '2026-10-20T08:00:00Z'}},
    'foreign_association': {'owner': 'foreign_owner', 'association': {'denomination': 'Estera', 'tax_code': '09876543210',
        'address': None, 'address_cap': None, 'address_city': None, 'notes': None},
        'user': {'email': 'list-foreign@example.test', 'username': 'list-foreign', 'first_name': '', 'last_name': '',
                 'phone': None, 'lead_sport_association_role': None, 'lead_sport_association_size': None,
                 'lead_sport_market_channel': None, 'date_joined': '2025-05-06T07:08:09Z', 'last_login': None}},
    'admin_association': {'owner': 'admin', 'association': {'denomination': 'Amministrazione', 'tax_code': '71000000001',
        'address': 'Piazza 2', 'address_cap': '20100', 'address_city': 'Milano', 'notes': ''},
        'user': {'email': 'list-admin@example.test', 'username': 'list-admin', 'first_name': 'Ada', 'last_name': 'Admin',
                 'phone': '', 'lead_sport_association_role': '', 'lead_sport_association_size': None,
                 'lead_sport_market_channel': None, 'date_joined': '2024-02-29T12:00:00.000001Z', 'last_login': '2026-10-27T00:00:00.5Z'}},
}
# own: one onboarding row; foreign: two (MultipleObjectsReturned -> None); admin: none.
ONBOARDING = [
    {'id': 'a1000000-0000-4000-8000-000000780011', 'user': 'owner', 'show': False, 'create_membership': True, 'view_membership': False,
     'approve_payment': True, 'download_invoice': False, 'view_collaborators': True, 'view_settings': False},
    {'id': 'a1000000-0000-4000-8000-000000780012', 'user': 'foreign_owner'},
    {'id': 'a1000000-0000-4000-8000-000000780013', 'user': 'foreign_owner'},
]
# .first() orders by primary key: the owner's lower-ID subscription is listed.
BILLING = [
    {'id': 'a1000000-0000-4000-8000-000000780021', 'user': 'owner', 'auto_renewal': False, 'renewal_type': 2,
     'ends_on': '2026-12-31T23:59:59.123456Z', 'plan': PLAN},
    {'id': 'a1000000-0000-4000-8000-000000780022', 'user': 'owner', 'auto_renewal': True, 'renewal_type': 1,
     'ends_on': None, 'plan': None},
    {'id': 'a1000000-0000-4000-8000-000000780023', 'user': 'admin', 'auto_renewal': True, 'renewal_type': 1,
     'ends_on': None, 'plan': None},
]
ONBOARDING_DEFAULTS = {'show': True, 'create_membership': False, 'view_membership': False, 'approve_payment': False,
                       'download_invoice': False, 'view_collaborators': False, 'view_settings': False}


def drf_datetime(text):
    """DRF output for an aware UTC datetime: isoformat() (six fractional
    digits whenever microseconds are nonzero) with +00:00 as Z."""
    if text is None:
        return None
    from datetime import datetime
    return datetime.fromisoformat(text.replace('Z', '+00:00')).isoformat().replace('+00:00', 'Z')


def row(label):
    spec = ASSOCIATIONS[label]
    owner, user, association = spec['owner'], spec['user'], spec['association']
    onboarding = [o for o in ONBOARDING if o['user'] == owner]
    billing = sorted((b for b in BILLING if b['user'] == owner), key=lambda b: b['id'])
    plain = lambda value: None if value is None else str(value)
    result = {'date_joined': drf_datetime(user['date_joined']), 'user_id': IDS[owner], 'email': user['email'], 'username': user['username'],
              'first_name': user['first_name'], 'last_name': user['last_name'], 'phone': plain(user['phone']),
              'role': plain(user['lead_sport_association_role']), 'size': plain(user['lead_sport_association_size']),
              'denomination': association['denomination'], 'tax_code': association['tax_code'], 'address': association['address'],
              'address_cap': association['address_cap'], 'address_city': association['address_city'],
              'market': plain(user['lead_sport_market_channel']), 'last_login': drf_datetime(user['last_login']), 'notes': association['notes'],
              'sport_association_id': IDS[label], 'billing_subscription': None, 'user_onboarding': None}
    if len(onboarding) == 1:
        values = {**ONBOARDING_DEFAULTS, **{k: v for k, v in onboarding[0].items() if k not in ('id', 'user')}}
        result['user_onboarding'] = {'users_onboarding_id': onboarding[0]['id'], **values, 'user': IDS[owner]}
    if billing:
        first = billing[0]
        # A SerializerMethodField dict is rendered by DRF's JSONEncoder:
        # aware UTC datetimes as isoformat() with Z, microseconds kept.
        result['billing_subscription'] = {'auto_renewal': first['auto_renewal'], 'renewal_type': first['renewal_type'],
                                          'ends_on': drf_datetime(first['ends_on']), 'billing_plan': first['plan']}
    return result


cases = []


def add(name, actor, status, response, *, method='GET', headers=None, unordered=False):
    cases.append({'name': f'association-list-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
                  'status': status, 'response': response, 'unordered': unordered})


add('admin', 'admin', 200, [row(label) for label in ASSOCIATIONS], unordered=True)
add('admin-impersonating-owner', 'admin', 401, '<empty>', headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
for actor in ('owner', 'full_collaborator', 'restricted_collaborator', 'athlete', 'owner_without_association'):
    add(f'{actor.replace("_", "-")}-not-superuser', actor, 401, '<empty>')
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'admin', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'admin', 200, {'name': 'Sport Association List', 'description': 'List all sport associations',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Superuser association list authored sequence', 'ids': IDS, 'plan': PLAN,
                              'associations': ASSOCIATIONS, 'onboarding': ONBOARDING, 'billing': BILLING, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
