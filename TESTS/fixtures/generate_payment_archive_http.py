"""Author the payment/<uid>/archive HTTP sequence from payment-archive.md.

The view toggles Payment.archived and saves. The lookup is Payment.objects
(live, group-aware) filtered by request.user.sport_association; a miss
dereferences None (500). Payment.save() fills a null payment_category with the
first 'entrate e proventi da attività tipiche' category by key (seeded by the
migrations; ``$default_category`` binds it). Saves are audited (only changed
fields) and indexed directly to the payment's association. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('payment_archive_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{940001 + n:012d}' for n in range(20))
CATEGORY = next(ids)
PAYMENTS = {
    'open': {'id': next(ids), 'association': OWN, 'archived': False, 'category': True, 'deleted': False, 'group': None},
    'stored': {'id': next(ids), 'association': OWN, 'archived': True, 'category': True, 'deleted': False, 'group': None},
    'uncategorized': {'id': next(ids), 'association': OWN, 'archived': False, 'category': False, 'deleted': False, 'group': None},
    'grouped': {'id': next(ids), 'association': OWN, 'archived': False, 'category': True, 'deleted': False, 'group': 'own_group'},
    'removed': {'id': next(ids), 'association': OWN, 'archived': False, 'category': True, 'deleted': True, 'group': None},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'archived': False, 'category': True, 'deleted': False, 'group': None},
}
for row in PAYMENTS.values():
    row.update(subject=0, description='Quota', meta=None, course=None, carnet=None)
# Payment.save() description defaults (get_course_carnet_name) when description is None.
SAVE_DEFAULTS = {
    'other_blank': dict(subject=0, description=None, category=False),
    'subscription_blank': dict(subject=1, description=None),
    'course_carnet_meta': dict(subject=2, description=None, meta={'description': 'Dal meta'}, carnet='k1'),
    'course_carnet_title': dict(subject=2, description=None, carnet='k1'),
    'course_label': dict(subject=2, description=None, course={'label': 'Etichetta'}),
    'course_blank': dict(subject=2, description=None),
    'quota_override': dict(subject=2, description='QUOTA corso', meta={'description': 'Rata uno'}),
    'transfer_blank': dict(subject=3, description=None),
}
for label, values in SAVE_DEFAULTS.items():
    PAYMENTS[label] = {'id': next(ids), 'association': OWN, 'archived': False, 'category': True, 'deleted': False, 'group': None,
                       'subject': 0, 'description': 'Quota', 'meta': None, 'course': None, 'carnet': None, **values}
CARNET = {'id': next(ids), 'title': 'Carnet dieci'}
path = lambda key: f'/payment/{PAYMENTS[key]["id"] if key in PAYMENTS else key}/archive'
SERVER = '<stable-server-error-page>'
ARCHIVED, UNARCHIVED = {'msg': 'Payment archived.'}, {'msg': 'Payment unarchived.'}
cases = []


def add(name, actor, target, status, response, *, method='POST', headers=None, effect=None):
    cases.append({'name': f'payment-archive-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


def toggled(label, archived, changes, actor='owner', impersonation=None):
    return {'payment': label, 'archived': archived, 'changes': changes, 'actor': actor, 'impersonation': impersonation}


flip = lambda old, new: {'archived': [str(old), str(new)]}
add('owner-archives', 'owner', 'open', 200, ARCHIVED, effect=toggled('open', True, flip(False, True)))
add('owner-unarchives', 'owner', 'stored', 200, UNARCHIVED, effect=toggled('stored', False, flip(True, False)))
add('repeat-unarchives', 'owner', 'open', 200, UNARCHIVED, effect=toggled('open', False, flip(True, False)))
add('fills-default-category', 'owner', 'uncategorized', 200, ARCHIVED,
    effect=toggled('uncategorized', True, {**flip(False, True), 'payment_category': ['None', '$default_category']}))
add('full-collaborator-archives', 'full_collaborator', 'grouped', 200, ARCHIVED, effect=toggled('grouped', True, flip(False, True), 'full_collaborator'))
add('admin-impersonating-owner', 'admin', 'grouped', 200, UNARCHIVED, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    effect=toggled('grouped', False, flip(True, False), 'admin', 'admin_owner'))
add('other-blank-fills-category-and-name', 'owner', 'other_blank', 200, ARCHIVED, effect=toggled('other_blank', True,
    {**flip(False, True), 'payment_category': ['None', '$default_category'], 'description': ['None', '$default_category_name']}))
add('subscription-blank-name', 'owner', 'subscription_blank', 200, ARCHIVED,
    effect=toggled('subscription_blank', True, {**flip(False, True), 'description': ['None', 'Iscrizione']}))
add('course-carnet-meta-name', 'owner', 'course_carnet_meta', 200, ARCHIVED,
    effect=toggled('course_carnet_meta', True, {**flip(False, True), 'description': ['None', 'Dal meta']}))
add('course-carnet-title-name', 'owner', 'course_carnet_title', 200, ARCHIVED,
    effect=toggled('course_carnet_title', True, {**flip(False, True), 'description': ['None', 'Carnet dieci']}))
add('course-label-name', 'owner', 'course_label', 200, ARCHIVED,
    effect=toggled('course_label', True, {**flip(False, True), 'description': ['None', 'Etichetta, ']}))
add('course-blank-name', 'owner', 'course_blank', 200, ARCHIVED,
    effect=toggled('course_blank', True, {**flip(False, True), 'description': ['None', 'Quota Corso']}))
add('quota-meta-override', 'owner', 'quota_override', 200, ARCHIVED,
    effect=toggled('quota_override', True, {**flip(False, True), 'description': ['QUOTA corso', 'Rata uno']}))
# The account-transfer description dereferences custom_accounts (None here).
add('transfer-blank-server-error', 'owner', 'transfer_blank', 500, SERVER)
add('other-group-server-error', 'owner', 'grouped', 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']})
add('deleted-server-error', 'owner', 'removed', 500, SERVER)
add('foreign-server-error', 'owner', 'abroad', 500, SERVER)
add('missing-server-error', 'owner', IDS['missing'], 500, SERVER)
add('malformed', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'])
add('owner-without-association-server-error', 'owner_without_association', 'open', 500, SERVER)
add('athlete-not-allowed', 'athlete', 'open', 403, {'msg': 'User not allowed.'})
add('owner-scoped-athlete-denied', 'owner', 'open', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('restricted-missing-permission', 'restricted_collaborator', 'open', 403, {'detail': 'Missing permission: bookeeping.payments.archive.update'})
add('admin-without-session', 'admin', 'open', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('anonymous', 'anonymous', 'open', 401, {'detail': 'Authentication credentials were not provided.'})
add('wrong-method', 'owner', 'open', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'open', 200, {'name': 'Payment Archive', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Payment archive authored sequence with audit effects', 'ids': IDS,
                              'category': CATEGORY, 'carnet': CARNET, 'payments': PAYMENTS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
