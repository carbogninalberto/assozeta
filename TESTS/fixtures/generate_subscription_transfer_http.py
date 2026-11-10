"""Author the subscription/<uid>/transfer HTTP sequence (subscription-transfer.md).

subscription_transfer (@api_view(['POST']), IsAuthenticated, subscriptions_views.py),
not atomic, in Django's order:
- is_valid_uuid(uid) (400 list);
- scoped_queryset(request, Subscription.objects).get(subscription_id=uid): the
  group-aware SubscriptionManager (X-Group-Id), 404 {'msg': 'Subscription not found.'};
- subscription.user != request.user: 403 {'msg': 'You are not the owner of the subscription.'}
  (collaborators act as the owner);
- SubscriptionTransfer.objects.filter(expires_at__lt=now()).update(status=EXPIRED):
  every transfer whose expiry passed becomes 4 (a queryset update: no audit);
- a PENDING transfer of the subscription: 400 with the Italian message;
- request.data.get('recipient') (a body that is not a dict is a 500):
  - a recipient equal to str(request.user.user_id): 409;
  - User.objects.filter(user_id=recipient).exclude(self).first(): a value the
    UUIDField rejects is a 500, an int is UUID(int=...), no user 400 without body;
  - no recipient: request.data.get('email') None is 400 {'msg': 'Email is required.'};
    otherwise the main tutor's (or the associate's) names, a random upper-case
    username (first 3 + last 3 letters + 8 characters) and 8-character password,
    User.objects.create_user(..., role=ATHLETE) (create entry), then
    send_password_welcome_email with request.user.sport_association (an athlete
    is a 403 after the user was created): render_to_string of
    email_welcome_password_message.html and send_mail_async.apply_async(kwargs=...);
- NotificationService.send_notification(recipient, [{'type': 'subscription', 'msg':
  "l'associazione <denomination> ha trasferito l'iscrizione <associate> al tuo account"}],
  association_id=subscription.sport_association_id);
- SubscriptionTransferCreateSerializer(...).save() (create entry), subscription.user =
  recipient and save(), associate.user and save(), get_main_tutor() (the first relation
  becomes primary when none is) and tutor.user and save(), then the transfer's status
  ACCEPTED and expires_at now() and save(); 201 with the serializer's data.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_transfer_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, ATHLETE = IDS['owner'], IDS['athlete']
ids = iter(f'a1000000-0000-4000-8000-{997001 + n:012d}' for n in range(100))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000997999'
DENOMINATION = 'Message reads own'
SUBSCRIPTION_NOT_FOUND = {'msg': 'Subscription not found.'}
NOT_OWNER = {'msg': 'You are not the owner of the subscription.'}
PENDING = {'msg': "C'è già una richiesta di trasferimento in attesa di essere approvata."}

# Associates: owner user None unless given; tutors are associates of the own association.
ASSOCIATES = {
    'adult': {'id': next(ids), 'first': 'Lino', 'last': 'Adulto', 'user': 'owner'},
    'minor': {'id': next(ids), 'first': 'Mia', 'last': 'Minore', 'user': 'owner'},
    'minor_tutor': {'id': next(ids), 'first': 'Teresa', 'last': 'Tutrice', 'user': None},
    'family': {'id': next(ids), 'first': 'Fabio', 'last': 'Famiglia', 'user': 'owner'},
    'family_tutor': {'id': next(ids), 'first': 'Gianni', 'last': 'Genitore', 'user': 'owner'},
    'pending': {'id': next(ids), 'first': 'Paolo', 'last': 'Pendente', 'user': 'owner'},
    'foreign': {'id': next(ids), 'first': 'Furio', 'last': 'Fuori', 'user': 'foreign_owner', 'association': FOREIGN},
    'athletes': {'id': next(ids), 'first': 'Ada', 'last': 'Atleta', 'user': 'athlete'},
    'form': {'id': next(ids), 'first': 'Fede', 'last': 'Form', 'user': 'owner'},
    'bare': {'id': next(ids), 'first': 'Nora', 'last': 'Nuova', 'user': None},
}
# Tutor relations: integer keys are assigned by the database (looked up by the pair).
RELATIONS = {
    # Not primary: get_main_tutor makes it primary.
    'minor': {'associate': 'minor', 'tutor': 'minor_tutor', 'primary': False},
    'family': {'associate': 'family', 'tutor': 'family_tutor', 'primary': True},
}
SUBSCRIPTIONS = {
    'adult': {'id': next(ids), 'associate': 'adult', 'user': 'owner'},
    'minor': {'id': next(ids), 'associate': 'minor', 'user': 'owner'},
    'family': {'id': next(ids), 'associate': 'family', 'user': 'owner'},
    'pending': {'id': next(ids), 'associate': 'pending', 'user': 'owner'},
    'foreign': {'id': next(ids), 'associate': 'foreign', 'user': 'foreign_owner', 'association': FOREIGN},
    'athlete_email': {'id': next(ids), 'associate': 'athletes', 'user': 'athlete'},
    'athlete_recipient': {'id': next(ids), 'associate': 'athletes', 'user': 'athlete'},
    'form': {'id': next(ids), 'associate': 'form', 'user': 'owner'},
    'bare': {'id': next(ids), 'associate': 'bare', 'user': 'owner'},
}
# Existing transfers; expires 'past' / 'future' (far from the run's clock).
TRANSFERS = {
    'pending_future': {'id': next(ids), 'subscription': 'pending', 'status': 1, 'expires': 'future', 'recipient': 'athlete'},
    'pending_past': {'id': next(ids), 'subscription': 'adult', 'status': 1, 'expires': 'past', 'recipient': 'athlete'},
    'accepted_past': {'id': next(ids), 'subscription': 'foreign', 'status': 2, 'expires': 'past', 'recipient': None},
    'rejected_future': {'id': next(ids), 'subscription': 'pending', 'status': 3, 'expires': 'future', 'recipient': None},
}
# Names outside ASCII (member forms accept any Unicode): str.upper() then NFKC of the username.
ASSOCIATES['accented'] = {'id': next(ids), 'first': 'Émile', 'last': 'Çelik', 'user': 'owner'}
ASSOCIATES['casefold'] = {'id': next(ids), 'first': 'ßöra', 'last': 'ǆukić', 'user': 'owner'}
SUBSCRIPTIONS['accented'] = {'id': next(ids), 'associate': 'accented', 'user': 'owner'}
SUBSCRIPTIONS['casefold'] = {'id': next(ids), 'associate': 'casefold', 'user': 'owner'}

cases = []
NOTIFIED = set()


def add(name, actor, uid, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'subscription-transfer-{name}', 'actor': actor, 'method': method, 'path': f'/subscription/{uid}/transfer',
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


EXPIRED = {'expire': True}


def message(associate):
    row = ASSOCIATES[associate]
    return f"l'associazione {DENOMINATION} ha trasferito l'iscrizione {row['first']} {row['last']} al tuo account"


def actor_association(actor):
    return {'owner': OWN, 'admin': IDS['admin_association']}.get(actor)


def transferred(actor, subscription, recipient, *, associate_from, tutor=None, relation=None, new_user=None, welcome=False):
    """The effect of an accepted transfer to `recipient` (a user label or '$new_user')."""
    spec = SUBSCRIPTIONS[subscription]
    associate = spec['associate']
    audit = []
    index = [actor_association(actor), 'actor.sport_association'] if actor_association(actor) else None
    if new_user is not None:
        audit.append({'model': 'user', 'action': 0, 'ref': '$new_user', 'changes': '$created', 'index': index})
    if recipient not in NOTIFIED:
        NOTIFIED.add(recipient)
        for model in ('notificationsbroadcast', 'usernotificationsbroadcast'):
            audit.append({'model': model, 'action': 0, 'ref': '$' + model, 'changes': '$created', 'app': 'notifications', 'index': index})
    audit.append({'model': 'subscriptiontransfer', 'action': 0, 'ref': '$transfer', 'changes': '$created',
                  'index': [OWN, 'subscription.sport_association']})
    audit.append({'model': 'subscription', 'action': 1, 'ref': subscription, 'changes': {'user': [IDS[spec['user']], '$recipient']},
                  'index': [OWN, 'direct']})
    if associate_from != '$recipient':
        audit.append({'model': 'associate', 'action': 1, 'ref': associate,
                      'changes': {'user': [IDS[associate_from] if associate_from else 'None', '$recipient']}, 'index': [OWN, 'direct']})
    if relation is not None:
        audit.append({'model': 'associatetutorrelation', 'action': 1, 'ref': relation, 'changes': {'is_primary': ['False', 'True']},
                      'index': [OWN, 'associate.sport_association']})
    if tutor is not None:
        audit.append({'model': 'associate', 'action': 1, 'ref': tutor['label'],
                      'changes': {'user': [IDS[tutor['from']] if tutor['from'] else 'None', '$recipient']}, 'index': [OWN, 'direct']})
    audit.append({'model': 'subscriptiontransfer', 'action': 1, 'ref': '$transfer', 'changes': '$accepted',
                  'index': [OWN, 'subscription.sport_association']})
    return {'expire': True, 'transfer': {'subscription': subscription, 'recipient': recipient, 'requester': 'owner'},
            'subscription': subscription, 'associates': [associate] + ([tutor['label']] if tutor else []),
            'relation': relation, 'new_user': new_user, 'welcome': welcome, 'audit': audit,
            'notification': {'user': recipient, 'msg': message(associate), 'association': OWN}}


def created(subscription, recipient):
    return {'subscription': SUBSCRIPTIONS[subscription]['id'], 'expires_at': '$now', 'requester': OWNER,
            'recipient': IDS[recipient] if recipient in IDS else '$new', 'token': '$new', 'status': 2}


ADULT = SUBSCRIPTIONS['adult']['id']

# --- Transport and identity ----------------------------------------------------------------------
add('options', 'owner', ADULT, 200, {'name': 'Subscription Transfer', 'description': '', 'renders': ['application/json'],
                                     'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('wrong-method', 'owner', ADULT, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('anonymous', 'anonymous', ADULT, 401, {'detail': 'Authentication credentials were not provided.'}, body={'recipient': ATHLETE})
add('restricted-missing-permission', 'restricted_collaborator', ADULT, 403,
    {'detail': 'Missing permission: association.members.update'}, body={'recipient': ATHLETE})
add('admin-without-session', 'admin', ADULT, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'recipient': ATHLETE})
add('owner-scoped-athlete-denied', 'owner', ADULT, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}, body={'recipient': ATHLETE})

# --- Lookups (nothing written) ---------------------------------------------------------------------
add('malformed-uuid', 'owner', 'not-a-uuid', 400, ['badly formed hexadecimal UUID string'], body={'recipient': ATHLETE})
add('unknown-subscription', 'owner', UNKNOWN, 404, SUBSCRIPTION_NOT_FOUND, body={'recipient': ATHLETE})
add('group-header-filters', 'owner', ADULT, 404, SUBSCRIPTION_NOT_FOUND, headers={'X-Group-Id': IDS['own_group']},
    body={'recipient': ATHLETE})
add('other-user-subscription', 'owner', SUBSCRIPTIONS['foreign']['id'], 403, NOT_OWNER, body={'recipient': ATHLETE})
add('athlete-not-owner', 'athlete', ADULT, 403, NOT_OWNER, body={'recipient': OWNER})

# --- Past the owner check: every transfer whose expiry passed becomes EXPIRED ----------------------
add('pending-transfer', 'owner', SUBSCRIPTIONS['pending']['id'], 400, PENDING, body={'recipient': ATHLETE}, effect=EXPIRED)
add('recipient-self', 'owner', ADULT, 409, {'msg': 'You cannot transfer the subscription to yourself.'}, body={'recipient': OWNER},
    effect=EXPIRED)
add('recipient-self-upper-case', 'owner', ADULT, 400, '<empty>', body={'recipient': OWNER.upper()}, effect=EXPIRED)
add('recipient-unknown', 'admin', ADULT, 400, '<empty>', headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    body={'recipient': UNKNOWN}, effect=EXPIRED)
add('recipient-integer', 'owner', ADULT, 400, '<empty>', body={'recipient': 7}, effect=EXPIRED)
add('recipient-malformed-server-error', 'owner', ADULT, 500, SERVER, body={'recipient': 'nessuno'}, effect=EXPIRED)
add('recipient-float-server-error', 'owner', ADULT, 500, SERVER, body={'recipient': 1.5}, effect=EXPIRED)
add('list-body-server-error', 'owner', ADULT, 500, SERVER, body=[ATHLETE], effect=EXPIRED)
add('email-required', 'owner', ADULT, 400, {'msg': 'Email is required.'}, body={'email': None}, effect=EXPIRED)
add('email-not-text-server-error', 'owner', ADULT, 500, SERVER, body={'email': 5}, effect=EXPIRED)

# --- Accepted transfers ------------------------------------------------------------------------------
add('to-recipient', 'owner', ADULT, 201, created('adult', 'athlete'), body={'recipient': ATHLETE},
    effect=transferred('owner', 'adult', 'athlete', associate_from='owner'))
add('collaborator-tutor-made-primary', 'full_collaborator', SUBSCRIPTIONS['minor']['id'], 201, created('minor', 'athlete'),
    body={'recipient': ATHLETE, 'email': 'ignored@example.test'},
    effect=transferred('full_collaborator', 'minor', 'athlete', associate_from='owner', relation='minor',
                       tutor={'label': 'minor_tutor', 'from': None}))
add('form-body', 'owner', SUBSCRIPTIONS['form']['id'], 201, created('form', 'athlete'), raw_body=f'recipient={UNKNOWN}&recipient={ATHLETE}',
    content_type='application/x-www-form-urlencoded', effect=transferred('owner', 'form', 'athlete', associate_from='owner'))
add('athlete-to-owner', 'athlete', SUBSCRIPTIONS['athlete_recipient']['id'], 201,
    {**created('athlete_recipient', 'owner'), 'requester': ATHLETE}, body={'recipient': OWNER},
    effect={**transferred('athlete', 'athlete_recipient', 'owner', associate_from='athlete'),
            'transfer': {'subscription': 'athlete_recipient', 'recipient': 'owner', 'requester': 'athlete'}})
# The e-mail path: a new athlete account named after the primary tutor, the welcome e-mail, then the transfer.
add('email-new-account', 'owner', SUBSCRIPTIONS['family']['id'], 201, created('family', '$new_user'),
    body={'email': 'Nuovo.Utente@Example.TEST'},
    effect=transferred('owner', 'family', '$new_user', associate_from='owner', tutor={'label': 'family_tutor', 'from': 'owner'},
                       new_user={'first_name': 'Gianni', 'last_name': 'Genitore', 'email': 'Nuovo.Utente@example.test', 'prefix': 'GIAGEN'},
                       welcome=True))
add('email-new-account-associate-names', 'full_collaborator', SUBSCRIPTIONS['bare']['id'], 201, created('bare', '$new_user'),
    body={'email': ''},
    effect={**transferred('full_collaborator', 'bare', '$new_user2', associate_from=None,
                          new_user={'first_name': 'Nora', 'last_name': 'Nuova', 'email': '', 'prefix': 'NORNUO'}, welcome=True)})
# Non-ASCII names: 'É'/'Ç' upper-case unchanged; 'ß' becomes 'SS' and 'ǆ' becomes 'Ǆ', which NFKC splits into 'DŽ'.
# The e-mail domain is lower-cased with str.lower() ('İ' becomes 'i' + U+0307).
add('email-new-account-accented-names', 'owner', SUBSCRIPTIONS['accented']['id'], 201, created('accented', '$new_user'),
    body={'email': 'Émile@Exämple.TEST'},
    effect=transferred('owner', 'accented', '$new_user3', associate_from='owner',
                       new_user={'first_name': 'Émile', 'last_name': 'Çelik', 'email': 'Émile@exämple.test', 'prefix': 'ÉMIÇEL'},
                       welcome=True))
add('email-new-account-expanding-names', 'owner', SUBSCRIPTIONS['casefold']['id'], 201, created('casefold', '$new_user'),
    body={'email': ' ǅ@İSTANBUL.Test '},
    effect=transferred('owner', 'casefold', '$new_user4', associate_from='owner',
                       new_user={'first_name': 'ßöra', 'last_name': 'ǆukić', 'email': 'ǅ@i\u0307stanbul.test', 'prefix': 'SSÖRDŽUK'},
                       welcome=True))
# An athlete has no sport_association: the account is created, then the e-mail raises PermissionDenied.
add('athlete-email-denied-after-account', 'athlete', SUBSCRIPTIONS['athlete_email']['id'], 403,
    {'detail': 'User is not a sport association'}, body={'email': 'creato@example.test'},
    effect={'expire': True, 'new_user': {'first_name': 'Ada', 'last_name': 'Atleta', 'email': 'creato@example.test', 'prefix': 'ADAATL'},
            'audit': [{'model': 'user', 'action': 0, 'ref': '$new_user', 'changes': '$created', 'index': None}]})
# The subscription now belongs to the athlete: the owner is no longer its owner.
add('transferred-no-longer-owned', 'owner', ADULT, 403, NOT_OWNER, body={'recipient': ATHLETE})

# '$new_user2' is a second new account: notify it as a fresh recipient (its own broadcast).
for case in cases:
    effect = case['effect'] or {}
    if str(effect.get('notification', {}).get('user')).startswith('$new_user'):
        effect['notification']['user'] = '$new_user'
        effect['transfer']['recipient'] = '$new_user'

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'subscription/<uid>/transfer authored sequence with row, audit, Celery and notification effects',
    'ids': IDS, 'associates': ASSOCIATES, 'relations': RELATIONS, 'subscriptions': SUBSCRIPTIONS, 'transfers': TRANSFERS,
    'denomination': DENOMINATION, 'unknown': UNKNOWN, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
