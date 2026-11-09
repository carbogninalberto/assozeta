"""Author the association/impersonation/users and administration/impersonation/users HTTP sequence (impersonation-users.md).

impersonation_users lists eligible_users(actor) (application/impersonation.py):
- an administrator (no association) sees every active non-superuser athlete,
  live association owner and collaborator of a live active owner;
- an association owner who owns the self-hosted instance (is_instance_owner:
  the first InstanceConfiguration is self_hosted and its primary association
  belongs to the owner) sees every active athlete plus the owner's own
  collaborators, the owner excluded;
- any other association owner sees the athletes linked to the association by a
  live Associate row with that user, the athletes with a non-deleted
  Subscription of the association (Subscription._base_manager), and the owner's
  own collaborators, the owner excluded.
The search (query[generalSearch], else q; stripped, first 150 characters) is
split on whitespace into at most 10 words; every word must match (icontains)
one of username, email, first_name, last_name, the user's association
denomination or the connected owner's association denomination
(search_users, applied as pk__in). Rows are ordered by username then pk; each
row is describe(user, actor association).

The fixture seeds a self-hosted InstanceConfiguration whose primary
association is the owner's, so 'owner' is the instance owner and
'foreign_owner' is not. Every seeded user's username starts with
'imp-users-' so the cases search within them. Run with ``python3 -I``.
"""
import json
from pathlib import Path
from urllib.parse import urlencode

OUTPUT = Path(__file__).with_name('impersonation_users_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{1680001 + n:012d}' for n in range(50))
OWN_NAME, FOREIGN_NAME = 'Message reads own', 'Message reads foreign'
cases = []


def person(slug, first, last, *, role=2, link=None, active=True, deleted=False):
    """link: ('member', association) | ('subscriber', association, deleted) | None."""
    return {'id': next(ids), 'username': f'imp-users-{slug}', 'email': f'imp-users-{slug}@example.test', 'first_name': first,
            'last_name': last, 'role': role, 'link': link, 'active': active, 'deleted': deleted}


USERS = {
    'rossi': person('mario-rossi', 'Mario', 'Rossi', link=('member', 'foreign_association')),
    'bianchi': person('mario-bianchi', 'Mario', 'Bianchi', link=('subscriber', 'foreign_association', False)),
    'verdi': person('luca-verdi', 'Luca', 'Verdi', link=('subscriber', 'foreign_association', True)),
    'neri': person('anna-neri', 'Anna', 'Neri'),
    'gialli': person('paolo-gialli', 'Paolo', 'Gialli', link=('subscriber', 'own_association', False)),
    'inactive': person('sara-inactive', 'Sara', 'Inattiva', active=False, link=('subscriber', 'foreign_association', False)),
    'removed': person('dario-removed', 'Dario', 'Rimosso', deleted=True, link=('subscriber', 'foreign_association', False)),
    'staff': person('staff', 'Staff', 'Esterno', role=3),
}
# The Associate row the foreign association registered itself: it belongs to its owner.
OWNER_MEMBER = next(ids)
SUBSCRIPTIONS = {label: next(ids) for label, row in USERS.items() if row['link'] and row['link'][0] == 'subscriber'}
MEMBERS = {label: next(ids) for label, row in USERS.items() if row['link'] and row['link'][0] == 'member'}
ROLE = {1: 'association', 2: 'athlete', 3: 'collaborator'}


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'impersonation-users-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': None})


def row(label, association):
    user = USERS[label]
    return {'user_id': user['id'], 'username': user['username'], 'email': user['email'], 'first_name': user['first_name'],
            'last_name': user['last_name'], 'last_login': None, 'role': ROLE[user['role']], 'association': association}


def listing(labels, association, *, page=1, perpage=25, total=None):
    """labels in username order; association: the describe() association of every row (None: per user)."""
    total = len(labels) if total is None else total
    users = [row(label, association(label) if callable(association) else association) for label in labels]
    return {'users': users, 'next_page': page + 1 if page * perpage < total else None,
            'meta': {'page': page, 'perpage': perpage, 'total': total, 'pages': max(1, (total + perpage - 1) // perpage)}}


def path(base, **params):
    return base + ('?' + urlencode(params) if params else '')


ASSOCIATION, ADMINISTRATION = '/association/impersonation/users', '/administration/impersonation/users'
OWN_DESCRIBED = {'id': OWN, 'name': OWN_NAME}
FOREIGN_DESCRIBED = {'id': FOREIGN, 'name': FOREIGN_NAME}

# --- transport -------------------------------------------------------------------
# CanManageImpersonation calls actor_association, whose own PermissionDenied precedes DRF's authentication challenge.
add('anonymous', 'anonymous', ASSOCIATION, 403, {'detail': 'Accesso non consentito.'})
add('options', 'owner', ASSOCIATION, 200, {'name': 'Impersonation Users', 'description': '', 'renders': ['application/json'],
                                            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('wrong-method', 'owner', ASSOCIATION, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')

# --- eligibility -------------------------------------------------------------------
# The self-hosted instance owner: every active athlete (linked or not, deleted subscription or not), no foreign collaborators.
add('instance-owner-every-athlete', 'owner', path(ASSOCIATION, q='imp-users'), 200,
    listing(['neri', 'verdi', 'bianchi', 'rossi', 'gialli'], OWN_DESCRIBED))
add('instance-owner-unlinked-athlete', 'owner', path(ASSOCIATION, q='anna neri'), 200, listing(['neri'], OWN_DESCRIBED))
add('instance-owner-collaborator-role', 'owner', path(ASSOCIATION, q='imp-users', role='collaborator'), 200, listing([], OWN_DESCRIBED))
# Another association owner: live members, athletes with a non-deleted subscription, own collaborators.
add('owner-members-and-subscribers', 'foreign_owner', path(ASSOCIATION, q='imp-users'), 200,
    listing(['bianchi', 'rossi', 'staff'], FOREIGN_DESCRIBED))
add('owner-athlete-role', 'foreign_owner', path(ASSOCIATION, q='imp-users', role='athlete'), 200,
    listing(['bianchi', 'rossi'], FOREIGN_DESCRIBED))
add('owner-subscriber-other-association', 'foreign_owner', path(ASSOCIATION, q='paolo'), 200, listing([], FOREIGN_DESCRIBED))
add('owner-deleted-subscription', 'foreign_owner', path(ASSOCIATION, q='verdi'), 200, listing([], FOREIGN_DESCRIBED))
add('owner-pagination', 'foreign_owner', path(ASSOCIATION, **{'q': 'imp-users', 'pagination[perpage]': 2}), 200,
    listing(['bianchi', 'rossi'], FOREIGN_DESCRIBED, perpage=2, total=3))
add('owner-pagination-second-page', 'foreign_owner', path(ASSOCIATION, **{'q': 'imp-users', 'pagination[perpage]': 2, 'page': 2}), 200,
    listing(['staff'], FOREIGN_DESCRIBED, page=2, perpage=2, total=3))

# --- search words ------------------------------------------------------------------
for name, query, labels in (
        ('two-words', 'Mario Rossi', ['rossi']),
        ('reversed-words', 'rossi  mario', ['rossi']),
        ('partial-words', 'mar ROSS', ['rossi']),
        ('shared-word', 'mario', ['bianchi', 'rossi']),
        ('unmatched-word', 'mario zzz', []),
        ('tab-separated', 'mario\trossi', ['rossi']),
        ('unit-separator', 'mario\x1frossi', ['rossi']),
        ('eleventh-word-ignored', ' '.join(['mario', 'rossi'] * 5 + ['zzz']), ['rossi']),
        ('tenth-word-applies', ' '.join(['mario', 'rossi'] * 4 + ['mario', 'zzz']), []),
        ('email-and-name', 'mario-rossi@example rossi', ['rossi']),
        ('owner-denomination', 'imp-users foreign', ['staff']),
        ('wildcard-literal', 'mario_rossi', [])):
    add('search-' + name, 'foreign_owner', path(ASSOCIATION, q=query), 200, listing(labels, FOREIGN_DESCRIBED))
add('search-general-search-parameter', 'foreign_owner', path(ASSOCIATION, **{'query[generalSearch]': 'Rossi Mario', 'q': 'zzz'}), 200,
    listing(['rossi'], FOREIGN_DESCRIBED))

# --- administrator ---------------------------------------------------------------------
admin_association = lambda label: FOREIGN_DESCRIBED if label == 'staff' else None
add('admin-every-eligible', 'admin', path(ADMINISTRATION, q='imp-users'), 200,
    listing(['neri', 'verdi', 'bianchi', 'rossi', 'gialli', 'staff'], admin_association))
add('admin-two-words', 'admin', path(ADMINISTRATION, q='gialli PAOLO'), 200, listing(['gialli'], admin_association))
add('admin-owner-denomination', 'admin', path(ADMINISTRATION, q='staff reads foreign'), 200, listing(['staff'], admin_association))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'association/impersonation/users, administration/impersonation/users',
                              'ids': IDS, 'users': USERS, 'owner_member': OWNER_MEMBER, 'subscriptions': SUBSCRIPTIONS, 'members': MEMBERS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
