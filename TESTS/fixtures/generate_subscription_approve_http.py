"""Author the subscription/<uid>/approve HTTP sequence (subscription-approve.md).

subscription_approve (@api_view(['POST']), subscriptions_views.py):
is_valid_uuid (400 list), SportAssociation.objects.get(user=...) (athletes and
owners without an association: 500), the group-aware Subscription.objects row
(soft-deleted rows are found) or {'msg': 'Subscription not found.'} (404),
another association's row PermissionDenied('User not allowed.'), an accepted
row PermissionDenied('User cannot change the status of the subscription.').
Otherwise status_flag=ACCEPTED and acceptance_date=timezone.now() (auditlog
renders the in-memory datetime; the DateField stores its UTC date). A row
without a signature takes signature_storage_key and signature_url from the
newest (creation_date) other ACCEPTED/PENDING subscription of the same
associate (associate=None matches rows without an associate) with either
column not null, through the same scoped group-aware manager, but only when
that row has_signature (a non-empty value). Subscription.save() fills missing
dates. Then the subscription_approved signal publishes
check_workflows_trigger.delay(trigger_type='subscription_approved',
sport_association_id=<association UUID>, subscription_id=<subscription UUID>)
right after the save has committed (autocommit, no on_commit). Every case
checks the Celery messages published on the broker: exactly one on success,
none otherwise. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_approve_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{640001 + n:012d}' for n in range(100))
SERVER = '<stable-server-error-page>'

ASSOCIATES = {name: {'id': next(ids), 'association': OWN, 'first': name.title(), 'last': 'Fixture'}
              for name in ('mario', 'luisa', 'gino', 'paolo', 'carla', 'anna', 'dario', 'elena', 'franco')}
ASSOCIATES['hans'] = {'id': next(ids), 'association': FOREIGN, 'first': 'Hans', 'last': 'Muller'}
SIGNED_URL = 'https://storage.example.test/signatures/firma-nuova.png'


def subscription(associate='mario', **fields):
    row = {'id': next(ids), 'association': OWN, 'associate': associate, 'status': 2, 'acceptance': None, 'start': '2026-01-01',
           'end': '2026-12-31', 'created': '2026-01-10T09:00:00Z', 'group': None, 'archived': False, 'deleted': False,
           'signature_key': None, 'signature_url': None}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'pending': subscription(),
    'dated': subscription('franco', status=1, acceptance='2026-01-15'),
    'accepted': subscription('anna', status=4, acceptance='2026-02-01'),
    'undated': subscription('anna', status=1, start=None, end=None, created='2026-03-10T09:00:00Z'),
    'rejected': subscription('anna', status=3),
    'grouped': subscription('carla', group='own_group'),
    'flagged': subscription('dario', deleted=True),
    'admin': subscription('mario'),
    'abroad': subscription('hans', association=FOREIGN),
    # Signature reuse (associate gino): the newest accepted/pending row with a signature column wins.
    'sig_target': subscription('gino', created='2026-04-01T09:00:00Z'),
    'sig_old': subscription('gino', status=4, acceptance='2025-01-05', created='2025-01-01T09:00:00Z', signature_key='signatures/firma-vecchia.png'),
    'sig_new': subscription('gino', status=2, created='2025-06-01T09:00:00Z', signature_url=SIGNED_URL),
    'sig_rejected': subscription('gino', status=3, created='2025-12-01T09:00:00Z', signature_key='signatures/firma-respinta.png'),
    # associate paolo: the newest candidate has an empty key, so nothing is copied.
    'blank_target': subscription('paolo', created='2026-04-01T09:00:00Z'),
    'blank_new': subscription('paolo', status=4, acceptance='2025-07-01', created='2025-07-01T09:00:00Z', signature_key=''),
    'blank_old': subscription('paolo', status=4, acceptance='2025-02-01', created='2025-02-01T09:00:00Z', signature_key='signatures/firma-paolo.png'),
    # A row with its own signature keeps it (no lookup).
    'own_signed': subscription('elena', created='2026-04-02T09:00:00Z', signature_key='signatures/firma-propria.png'),
    'own_other': subscription('elena', status=4, acceptance='2025-01-01', created='2025-01-01T09:00:00Z', signature_url=SIGNED_URL),
    # Group-scoped lookup: carla's signed row has no group, invisible under X-Group-Id.
    'group_target': subscription('carla', group='own_group', created='2026-04-01T09:00:00Z'),
    'group_source': subscription('carla', status=4, acceptance='2025-03-01', created='2025-03-01T09:00:00Z', signature_key='signatures/firma-carla.png'),
    # associate=None: the lookup matches rows without an associate.
    'none_target': subscription(None, created='2026-04-01T09:00:00Z'),
    'none_source': subscription(None, association=FOREIGN, status=4, acceptance='2025-04-01', created='2025-04-01T09:00:00Z',
                                signature_key='signatures/firma-senza-socio.png', signature_url=SIGNED_URL),
    # A soft-deleted candidate is visible to Subscription.objects.
    'deleted_target': subscription('luisa', created='2026-04-01T09:00:00Z'),
    'deleted_source': subscription('luisa', status=4, acceptance='2025-05-01', created='2025-05-01T09:00:00Z', deleted=True,
                                   signature_key='signatures/firma-luisa.png'),
}
UNKNOWN = 'a1000000-0000-4000-8000-000000649999'
cases = []


def add(name, actor, path, status, response, *, method='POST', headers=None, effect=None):
    cases.append({'name': f'subscription-approve-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


def route(label):
    return f'/subscription/{SUBSCRIPTIONS[label]["id"] if label in SUBSCRIPTIONS else label}/approve'


def approved(label, actor='owner', changes=None, columns=None):
    """One audited save of the subscription (acceptance '$now': the request's timezone.now()) and one published task."""
    row = SUBSCRIPTIONS[label]
    audit = {'status_flag': [str(row['status']), '4'], 'acceptance_date': [row['acceptance'] or 'None', '$now'], **(changes or {})}
    return {'actor': actor, 'subscription': label, 'columns': {'status_flag': 4, 'acceptance_date': '$today', **(columns or {})},
            'audit': audit, 'index': [row['association'], 'direct'],
            'celery': [{'task': 'check_workflows_trigger', 'args': [],
                        'kwargs': [['trigger_type', 'subscription_approved'], ['sport_association_id', {'uuid': row['association']}],
                                   ['subscription_id', {'uuid': row['id']}]]}]}


APPROVED = {'data': {'msg': 'subscription approved.'}}
NOT_FOUND = {'msg': 'Subscription not found.'}
DENIED_STATUS = {'detail': 'User cannot change the status of the subscription.'}

add('pending', 'owner', route('pending'), 200, APPROVED, effect=approved('pending'))
add('again-denied', 'owner', route('pending'), 403, DENIED_STATUS)
add('replaces-acceptance', 'owner', route('dated'), 200, APPROVED, effect=approved('dated'))
add('accepted-denied', 'owner', route('accepted'), 403, DENIED_STATUS)
add('rejected', 'owner', route('rejected'), 200, APPROVED, effect=approved('rejected'))
# Subscription.save() fills the missing dates from the owner's season (1 January, LIKE_SEASON_YEAR).
add('fills-dates', 'owner', route('undated'), 200, APPROVED, effect=approved(
    'undated', changes={'start_date': ['None', '2026-01-01'], 'end_date': ['None', '2026-12-31']},
    columns={'start_date': '2026-01-01', 'end_date': '2026-12-31'}))
add('other-group-not-found', 'owner', route('grouped'), 404, NOT_FOUND, headers={'X-Group-Id': IDS['foreign_group']})
add('group-full-collaborator', 'full_collaborator', route('grouped'), 200, APPROVED, headers={'X-Group-Id': IDS['own_group']},
    effect=approved('grouped', 'full_collaborator'))
add('soft-deleted-row', 'owner', route('flagged'), 200, APPROVED, effect=approved('flagged'))
add('admin-impersonating-owner', 'admin', route('admin'), 200, APPROVED,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}, effect=approved('admin', 'admin'))
add('foreign-denied', 'owner', route('abroad'), 403, {'detail': 'User not allowed.'})
add('missing', 'owner', route(UNKNOWN), 404, NOT_FOUND)
add('malformed', 'owner', route('abc'), 400, ['badly formed hexadecimal UUID string'])
add('copies-signature', 'owner', route('sig_target'), 200, APPROVED, effect=approved(
    'sig_target', changes={'signature_url': ['None', SIGNED_URL]}, columns={'signature_url': SIGNED_URL}))
add('blank-signature-not-copied', 'owner', route('blank_target'), 200, APPROVED, effect=approved('blank_target'))
add('own-signature-kept', 'owner', route('own_signed'), 200, APPROVED, effect=approved('own_signed'))
add('group-scoped-signature', 'full_collaborator', route('group_target'), 200, APPROVED, headers={'X-Group-Id': IDS['own_group']},
    effect=approved('group_target', 'full_collaborator'))
add('without-associate-signature', 'owner', route('none_target'), 200, APPROVED, effect=approved(
    'none_target', changes={'signature_storage_key': ['None', 'signatures/firma-senza-socio.png'], 'signature_url': ['None', SIGNED_URL]},
    columns={'signature_storage_key': 'signatures/firma-senza-socio.png', 'signature_url': SIGNED_URL}))
add('signature-source-soft-deleted', 'owner', route('deleted_target'), 200, APPROVED, effect=approved(
    'deleted_target', changes={'signature_storage_key': ['None', 'signatures/firma-luisa.png']},
    columns={'signature_storage_key': 'signatures/firma-luisa.png'}))
add('athlete-server-error', 'athlete', route('pending'), 500, SERVER)
add('owner-without-association-server-error', 'owner_without_association', route('pending'), 500, SERVER)
add('owner-scoped-athlete-denied', 'owner', route('pending'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', route('pending'), 403,
    {'detail': 'Missing permission: association.members.update'})
add('admin-without-session', 'admin', route('pending'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', route('pending'), 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', route('pending'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', route('pending'), 200, {'name': 'Subscription Approve', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription approve authored sequence with audit and Celery publish effects',
                              'ids': IDS, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
