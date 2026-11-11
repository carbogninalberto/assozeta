"""Author the oauth2/signup, oauth2/partial-signup, oauth2/reset and oauth2/delete-account HTTP sequence (oauth2-accounts.md).

All four are @api_view(['POST']) function views in BE/application/views/auth_views.py.
signup, partial-signup and reset are AllowAny (a supplied Bearer token still
authenticates and becomes the audit actor); delete-account is IsAuthenticated.
All four paths are in EXCLUDED_ENDPOINTS and start with 'oauth2/', so restricted
collaborators and administrators without a session pass identity resolution.

partial_signup: 'email' in data.keys() (a non-dict body raises), then
UserPartial.objects.filter(email__iexact=...).first(); none: create(email=...)
(auditlog create entry; index through the actor's association), save() (no
change), send_email_text.delay(recipient_list=['info@bakney.com'], subject,
message=f"... {data['email']} ..."). Always {'msg': 'Email added.'}.

oauth2_reset_password: exactly {'email'}: users = User.objects (not deleted)
.filter(email__iexact=str(email).strip()).order_by('user_id'); per user a
token_hex(16) stored in Redis DB 2 (value user_id, ex=3600) and the reset
e-mail (render_to_string + send_mail_async.apply_async). Exactly {'token',
'password'}: r.get(token) (types redis-py rejects raise), the password regex
(re.search on a non-string raises), then User.objects.get (500 when missing),
set_password + save (no audit entry: password is excluded), r.delete and the
confirmation e-mail. Anything else 400 {'msg': 'Invalid request.'}.

oauth2_delete_account: user = request.user, or acting_user(request) for a
collaborator scope; delete_on = UTC today + 30 days; save() (auditlog update
entry only when the date changes; index user.sport_association, then the
actor's association); send_email_text.delay(...). Impersonation sessions with an
association boundary are refused by enforce_scoped_request.

oauth2_signup (non-social path): sport_association required and a bool (True
is 403); the disposable e-mail check (validate_disposable_email on the
lowercased e-mail when DEBUG is False, then the 'skrank.com' domain) - a missing
or non-string e-mail and an e-mail without '@' raise; collaboratorToken makes
the role COLLABORATOR; subscription_transfer with its token builds a queryset
(an invalid UUID raises); token+username+backend is the social path (below);
UserSerializerSignup errors 400; then, not atomically: the UserPartial with the
same e-mail (iexact) is deleted, the collaboration invite (.first() by token)
is looked up (none: 400), deleted, and checked for expiry (400; a null expiry
raises), create_user (upper-cased username, normalized e-mail, the raw password,
role, connected user and the invite's collaborator role/permissions), a
subscription transfer raises (QuerySet.save), JWT tokens and
build_login_response, the welcome e-mail, and for collaborators the
sport_association block re-serialized with requires_welcome False.
A successful response ('$signup') is verified by the runner: the tokens with
SimpleJWT and the rest against Django's build_login_response for the stored user.

oauth2_signup (social path, token + username + backend): unsupported backend
400; SocialAuthService.verify_google_token / verify_apple_token against the
run's provider stub (Google certificates, token info and user info; Apple keys)
with the run's client ids - a token the provider rejects is 401 'Invalid social
token'; has_verified_email (a non-empty string e-mail and email_verified True
or 'true') else the same 401. Live accounts with that e-mail (iexact, by
user_id): more than one or an inactive one 401 'invalid credentials'; two_fa
follows the login view's OTP check; otherwise tokens and build_login_response
for the stored account, with no write and no e-mail ('signin'). No account:
collaboratorToken consumes the invite as the password path does (not found 400,
deleted, then null expiry raises / expired 400), create_user (names from the
identity or else the body as sent, data['username'].upper(), the identity's
e-mail, no usable password, the invite's owner / role / permissions), then the
shared tail: the UserPartial of the body's e-mail is deleted, a subscription
transfer raises, tokens, response and the welcome e-mail.
body values {'$social': ...} and {'$totp': ...} are minted by the runner for each
request; case['provider'] lists the provider requests the case must make.
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('oauth_accounts_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, ATHLETE, ADMIN = IDS['owner'], IDS['athlete'], IDS['admin']
FULL, RESTRICTED, NO_ASSOCIATION = IDS['full_collaborator'], IDS['restricted_collaborator'], IDS['owner_without_association']
SERVER = '<stable-server-error-page>'
SEND_MAIL, SEND_TEXT = 'core.tasks.send_mail_async', 'send_email_text'


def uid(number):
    return f'a1000000-0000-4000-8000-{number:012d}'


def base_email(identifier):
    if identifier == IDS['admin']:
        return 'message-scopes-admin@example.test'
    return f'message-reads-{identifier[-6:]}@example.test'


# --- seeded rows --------------------------------------------------------------------------------
USERS = {
    'reset_active': {'user_id': uid(760001), 'username': 'oauth-reset-active', 'email': 'Shared.Reset@Example.test',
                     'first_name': 'Rita', 'last_name': "D'Amico <R>", 'role': 2, 'is_active': True, 'deleted': False},
    'reset_inactive': {'user_id': uid(760002), 'username': 'oauth-reset-inactive', 'email': 'shared.reset@example.test',
                       'first_name': 'Ivo', 'last_name': 'Inattivo', 'role': 2, 'is_active': False, 'deleted': False},
    'reset_deleted': {'user_id': uid(760003), 'username': 'oauth-reset-deleted', 'email': 'SHARED.RESET@example.test',
                      'first_name': 'Del', 'last_name': 'Eted', 'role': 2, 'is_active': True, 'deleted': True},
    'conflict': {'user_id': uid(760004), 'username': 'CONFLICT-USER', 'email': 'conflict@example.test',
                 'first_name': 'Con', 'last_name': 'Flict', 'role': 2, 'is_active': True, 'deleted': False},
    'social_inactive': {'user_id': uid(760005), 'username': 'OAUTH-SOCIAL-INACTIVE', 'email': 'social.inactive@example.test',
                        'first_name': 'Ina', 'last_name': 'Ttiva', 'role': 2, 'is_active': False, 'deleted': False},
    'social_otp': {'user_id': uid(760006), 'username': 'OAUTH-SOCIAL-OTP', 'email': 'social.otp@example.test',
                   'first_name': 'Otto', 'last_name': 'Codice', 'role': 2, 'is_active': True, 'deleted': False,
                   'two_fa': True, 'two_fa_secret': 'JBSWY3DPEHPK3PXP'},
    'social_deleted': {'user_id': uid(760007), 'username': 'OAUTH-SOCIAL-DELETED', 'email': 'social.deleted@example.test',
                       'first_name': 'Del', 'last_name': 'Social', 'role': 1, 'is_active': True, 'deleted': True},
}
SCHEDULED = '2026-01-05'  # owner_without_association.delete_on before its deletion request
INVITES = {
    'valid': {'collaboration_invite_id': uid(761010), 'user': OWNER, 'email': 'collab.one@example.test', 'token': 'oauth-invite-valid',
              'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 3, 'collaborator_permissions': ['association.members.read']},
    'null_token': {'collaboration_invite_id': uid(761020), 'user': OWNER, 'email': 'collab.null@example.test', 'token': None,
                   'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 1, 'collaborator_permissions': None},
    'expired': {'collaboration_invite_id': uid(761030), 'user': OWNER, 'email': 'collab.expired@example.test', 'token': 'oauth-invite-expired',
                'expiration_date': '2020-01-01T00:00:00Z', 'collaborator_role': 1, 'collaborator_permissions': None},
    'no_expiry': {'collaboration_invite_id': uid(761040), 'user': OWNER, 'email': 'collab.open@example.test', 'token': 'oauth-invite-no-expiry',
                  'expiration_date': None, 'collaborator_role': 1, 'collaborator_permissions': None},
    'no_association': {'collaboration_invite_id': uid(761050), 'user': NO_ASSOCIATION, 'email': 'collab.orphan@example.test',
                       'token': 'oauth-invite-no-association', 'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 1,
                       'collaborator_permissions': None},
    'duplicate_first': {'collaboration_invite_id': uid(761060), 'user': OWNER, 'email': 'collab.dup@example.test',
                        'token': 'oauth-invite-duplicate', 'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 2,
                        'collaborator_permissions': {'b': [1, 'x'], 'a': None}},
    'duplicate_second': {'collaboration_invite_id': uid(761070), 'user': OWNER, 'email': 'collab.dup@example.test',
                         'token': 'oauth-invite-duplicate', 'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 3,
                         'collaborator_permissions': None},
    'social_kept': {'collaboration_invite_id': uid(761080), 'user': OWNER, 'email': 'collab.kept@example.test',
                    'token': 'oauth-invite-social-kept', 'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 3,
                    'collaborator_permissions': ['association.members.read']},
    'social_expired': {'collaboration_invite_id': uid(761090), 'user': OWNER, 'email': 'collab.social.expired@example.test',
                       'token': 'oauth-invite-social-expired', 'expiration_date': '2020-01-01T00:00:00Z', 'collaborator_role': 1,
                       'collaborator_permissions': None},
    'social_valid': {'collaboration_invite_id': uid(761100), 'user': OWNER, 'email': 'collab.social@example.test',
                     'token': 'oauth-invite-social-valid', 'expiration_date': '2099-01-01T00:00:00Z', 'collaborator_role': 3,
                     'collaborator_permissions': ['association.members.read', 'association.members.update']},
}
PARTIALS = {
    'existing': {'id': 990001, 'email': 'partial.existing@example.test'},
    'athlete': {'id': 990002, 'email': 'nuovo.atleta@example.test'},
    'collaborator': {'id': 990003, 'email': 'collab.one@example.test'},
    'missing_names': {'id': 990004, 'email': 'missing.names@example.test'},
    'conflict': {'id': 990005, 'email': 'conflict.partial@example.test'},
    'transfer': {'id': 990006, 'email': 'transfer.raise@example.test'},
    'social': {'id': 990007, 'email': 'social.partial@example.test'},
}
# Redis DB 2 keys (the reset view's own client); values are user ids or text.
RESET_TOKENS = {
    'oauth-reset-valid': USERS['reset_active']['user_id'],
    'oauth-reset-weak': USERS['reset_active']['user_id'],
    'oauth-reset-deleted-user': USERS['reset_deleted']['user_id'],
    'oauth-reset-bad-id': 'not-a-user-id',
    '12345': USERS['reset_inactive']['user_id'],
    'oauth-reset-form': ATHLETE,
    'oauth-reset-nul': USERS['reset_active']['user_id'],
    'oauth-reset-surrogate': USERS['reset_active']['user_id'],
}
CARD = {'sport_association_membership_card_configuration_id': uid(762001), 'sport_association': OWN, 'emit_only_on_approval': True,
        'customized_template': None}
TRANSFER_TOKEN = 'b2000000-0000-4000-8000-000000000001'

cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        provider=None):
    case = {'name': f'oauth-accounts-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if provider is not None:
        case['provider'] = provider
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def options(name, description):
    return {'name': name, 'description': description, 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


def render(template, context):
    """render_to_string(template, context) under the request's language (the harness sends en)."""
    return {'$render': template, 'language': 'en', 'context': context}


BRAND = {'app_host': '$app_url', 'settings': {'WHITELABEL_NAME': '$whitelabel', 'IS_WHITELABEL': '$is_whitelabel'}}


def mail(subject, message, recipients):
    """AuthUtils: send_mail_async.apply_async(kwargs={...}) without a sport association."""
    return {'task': SEND_MAIL, 'args': [], 'kwargs': [['subject', subject], ['message', message], ['from_email', '$from_email'],
                                                     ['recipient_list', recipients], ['html_message', message],
                                                     ['fail_silently', False]]}


def text(recipients, subject, message):
    """send_email_text.delay(recipient_list=..., subject=..., message=...)."""
    return {'task': SEND_TEXT, 'args': [], 'kwargs': [['recipient_list', recipients], ['subject', subject], ['message', message]]}


METHOD_GET = {'detail': 'Method "GET" not allowed.'}
INVALID = {'msg': 'Invalid request.'}
SCOPED_ATHLETE = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OWNER_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': ATHLETE}

# --- oauth2/partial-signup ----------------------------------------------------------------------
PARTIAL = '/oauth2/partial-signup'
ADDED = {'msg': 'Email added.'}


def partial(stored, shown=None, *, actor=None, index=None):
    """A new UserPartial (stored email) with its create entry and the notification."""
    shown = stored if shown is None else shown
    return {'kind': 'partial', 'email': stored, 'actor': actor, 'index': index,
            'celery': [text(['info@bakney.com'], 'Nuovo utente parziale', f'Utente parziale con email {shown} registrato.')]}


add('partial-options', 'anonymous', PARTIAL, 200, options('Partial Signup', ''), method='OPTIONS')
add('partial-get', 'anonymous', PARTIAL, 405, METHOD_GET, method='GET')
add('partial-no-email', 'anonymous', PARTIAL, 200, ADDED, body={'mail': 'x@example.test'})
add('partial-empty-body', 'anonymous', PARTIAL, 200, ADDED)
add('partial-existing-other-case', 'anonymous', PARTIAL, 200, ADDED, body={'email': 'Partial.EXISTING@example.test'})
add('partial-new', 'anonymous', PARTIAL, 200, ADDED, body={'email': 'Lead.One@example.test', 'name': 'ignored'},
    effect=partial('Lead.One@example.test'))
add('partial-new-again-other-case', 'anonymous', PARTIAL, 200, ADDED, body={'email': 'lead.one@EXAMPLE.test'})
add('partial-owner-actor', 'owner', PARTIAL, 200, ADDED, body={'email': 'lead.owner@example.test'},
    effect=partial('lead.owner@example.test', actor='owner', index=[OWN, 'actor.sport_association']))
add('partial-athlete-actor', 'athlete', PARTIAL, 200, ADDED, body={'email': 'lead.athlete@example.test'},
    effect=partial('lead.athlete@example.test', actor='athlete'))
add('partial-restricted-actor', 'restricted_collaborator', PARTIAL, 200, ADDED, body={'email': 'lead.restricted@example.test'},
    effect=partial('lead.restricted@example.test', actor='restricted_collaborator'))
add('partial-admin-without-session', 'admin', PARTIAL, 200, ADDED, body={'email': 'lead.admin@example.test'},
    effect=partial('lead.admin@example.test', actor='admin', index=[IDS['admin_association'], 'actor.sport_association']))
# email__iexact does not prepare its value: a non-string reaches PostgreSQL's UPPER() and raises.
add('partial-number', 'anonymous', PARTIAL, 500, SERVER, body={'email': 12345})
add('partial-bool', 'anonymous', PARTIAL, 500, SERVER, body={'email': True})
add('partial-dict', 'anonymous', PARTIAL, 500, SERVER, body={'email': {'a': [1, 'b'], 'c': None}})
add('partial-list', 'anonymous', PARTIAL, 500, SERVER, body={'email': ['x@example.test']})
add('partial-not-an-email', 'anonymous', PARTIAL, 200, ADDED, body={'email': "  not an 'email'  "},
    effect=partial("  not an 'email'  "))
add('partial-form', 'anonymous', PARTIAL, 200, ADDED, raw_body='email=first%40example.test&email=Form.Lead%40example.test',
    content_type='application/x-www-form-urlencoded', effect=partial('Form.Lead@example.test'))
add('partial-null', 'anonymous', PARTIAL, 500, SERVER, body={'email': None})
add('partial-too-long', 'anonymous', PARTIAL, 500, SERVER, body={'email': 'l' * 243 + '@example.test'})
add('partial-list-body', 'anonymous', PARTIAL, 500, SERVER, body=['email'])
add('partial-malformed-json', 'anonymous', PARTIAL, 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json')
add('partial-owner-scoped-to-athlete', 'owner', PARTIAL, 403, SCOPED_ATHLETE, body={'email': 'scoped@example.test'}, headers=OWNER_ATHLETE)

# --- oauth2/reset -------------------------------------------------------------------------------
RESET = '/oauth2/reset'
SENT = {'msg': 'Email sent to the user if the email exists.'}
RESET_TEMPLATE = 'email/account/email_reset_message.html'
CONFIRM_TEMPLATE = 'email/account/email_confirm_reset_message.html'
RESET_DESCRIPTION = 'API endpoint to reset password of a user.\n:param request:\n:return:'


def person(user):
    """first/last name, username and e-mail of a seeded or base user."""
    if user in USERS:
        row = USERS[user]
        return row['user_id'], row['first_name'], row['last_name'], row['username'], row['email']
    identifier = IDS[user]
    return identifier, 'Message', 'Fixture', f'message-reads-{identifier[-6:]}', base_email(identifier)


def requested(*users):
    """One Redis token and one reset e-mail per user, in user_id order; '$token:<n>' is the n-th new key."""
    ordered = sorted(users, key=lambda user: person(user)[0])
    messages = []
    for index, user in enumerate(ordered):
        identifier, first, last, username, email = person(user)
        context = {'first_name': first, 'last_name': last, 'username': username, 'token': f'$token:{index}', **BRAND}
        messages.append(mail('$whitelabel | Reset Password', render(RESET_TEMPLATE, context), [email]))
    return {'kind': 'reset-request', 'users': [person(user)[0] for user in ordered], 'celery': messages}


def reset(user, token, password):
    """The password of user becomes password (Django's default hasher), token is deleted, confirmation e-mail."""
    identifier, first, last, _, email = person(user)
    context = {'first_name': first, 'last_name': last, **BRAND}
    return {'kind': 'reset', 'user': identifier, 'token': token, 'password': password,
            'celery': [mail('$whitelabel | Conferma Nuova Password', render(CONFIRM_TEMPLATE, context), [email])]}


add('reset-options', 'anonymous', RESET, 200, options('Oauth2 Reset Password', RESET_DESCRIPTION), method='OPTIONS')
add('reset-get', 'anonymous', RESET, 405, METHOD_GET, method='GET')
add('reset-empty', 'anonymous', RESET, 400, INVALID, body={})
add('reset-email-and-more', 'anonymous', RESET, 400, INVALID, body={'email': base_email(ATHLETE), 'x': 1})
add('reset-token-only', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-valid'})
add('reset-three-keys', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-valid', 'password': 'Valid-Pass1!', 'x': 1})
add('reset-unknown-email', 'anonymous', RESET, 200, SENT, body={'email': 'nobody@example.test'})
add('reset-number-email', 'anonymous', RESET, 200, SENT, body={'email': 42})
add('reset-athlete-email', 'anonymous', RESET, 200, SENT, body={'email': '  ' + base_email(ATHLETE).upper() + '\n'},
    effect=requested('athlete'))
add('reset-shared-email', 'anonymous', RESET, 200, SENT, body={'email': 'shared.RESET@example.test'},
    effect=requested('reset_active', 'reset_inactive'))
add('reset-owner-actor', 'owner', RESET, 200, SENT, body={'email': base_email(OWNER)}, effect=requested('owner'))
add('reset-form-email', 'anonymous', RESET, 200, SENT, raw_body='email=' + base_email(FULL).replace('@', '%40'),
    content_type='application/x-www-form-urlencoded', effect=requested('full_collaborator'))
add('reset-list-body', 'anonymous', RESET, 500, SERVER, body=['email'])
add('reset-unknown-token', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-unknown', 'password': 'Valid-Pass1!'})
add('reset-weak-password', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-weak', 'password': 'Valid-Pass!'})
add('reset-password-newline-inside', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-weak', 'password': 'Valid\n-Pass1!xx'})
add('reset-password-short', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-weak', 'password': 'Va-Pass1!'})
add('reset-password-number', 'anonymous', RESET, 500, SERVER, body={'token': 'oauth-reset-unknown', 'password': 1234567890})
add('reset-token-null', 'anonymous', RESET, 500, SERVER, body={'token': None, 'password': 'Valid-Pass1!'})
add('reset-token-bool', 'anonymous', RESET, 500, SERVER, body={'token': True, 'password': 'Valid-Pass1!'})
add('reset-token-list', 'anonymous', RESET, 500, SERVER, body={'token': ['oauth-reset-valid'], 'password': 'Valid-Pass1!'})
add('reset-deleted-user', 'anonymous', RESET, 500, SERVER, body={'token': 'oauth-reset-deleted-user', 'password': 'Valid-Pass1!'})
add('reset-bad-user-id', 'anonymous', RESET, 500, SERVER, body={'token': 'oauth-reset-bad-id', 'password': 'Valid-Pass1!'})
add('reset-valid', 'anonymous', RESET, 200, {'msg': 'Password reset.'},
    body={'token': 'oauth-reset-valid', 'password': 'Nuova-Password1'}, effect=reset('reset_active', 'oauth-reset-valid', 'Nuova-Password1'))
add('reset-valid-again', 'anonymous', RESET, 400, INVALID, body={'token': 'oauth-reset-valid', 'password': 'Nuova-Password1'})
add('reset-trailing-newline-password', 'anonymous', RESET, 200, {'msg': 'Password reset.'},
    body={'password': 'Altra.Password9\n', 'token': 12345}, effect=reset('reset_inactive', '12345', 'Altra.Password9\n'))
add('reset-form-token', 'anonymous', RESET, 200, {'msg': 'Password reset.'},
    raw_body='token=oauth-reset-form&password=Form*Password7', content_type='application/x-www-form-urlencoded',
    effect=reset('athlete', 'oauth-reset-form', 'Form*Password7'))
# A NUL is an ordinary character for the hasher; a lone surrogate cannot be encoded (nothing is written, the token stays).
add('reset-password-with-nul', 'anonymous', RESET, 200, {'msg': 'Password reset.'},
    body={'token': 'oauth-reset-nul', 'password': 'Valid-Pass1!\u0000x'},
    effect=reset('reset_active', 'oauth-reset-nul', 'Valid-Pass1!\u0000x'))
add('reset-password-with-lone-surrogate', 'anonymous', RESET, 500, SERVER,
    raw_body='{"token": "oauth-reset-surrogate", "password": "Valid-Pass1!\\ud800x"}', content_type='application/json')
add('reset-owner-scoped-to-athlete', 'owner', RESET, 403, SCOPED_ATHLETE, body={'email': 'x@example.test'}, headers=OWNER_ATHLETE)

# --- oauth2/delete-account ----------------------------------------------------------------------
DELETE = '/oauth2/delete-account'
SCHEDULED_MSG = {'msg': 'Account will be deleted in 30 days.'}
DELETION_TEXT = ("La tua richiesta di eliminazione dell'account è stata presa in carico correttamente. "
                 "L'account verrà eliminato il giorno $deletion_date.")


def deletion(user, *, actor, before=None, changed=True, index=None, session=None):
    """user.delete_on becomes UTC today + 30 days ('$deletion_date' is it as dd/mm/YYYY); an update entry
    by actor when the date changes; the confirmation text to the user's e-mail."""
    identifier = IDS[user]
    return {'kind': 'delete', 'user': identifier, 'before': before, 'changed': changed, 'actor': actor, 'index': index,
            'session': session, 'celery': [text([base_email(identifier)], '$whitelabel | Eliminazione account', DELETION_TEXT)]}


add('delete-anonymous', 'anonymous', DELETE, 401, {'detail': 'Authentication credentials were not provided.'})
add('delete-options', 'owner', DELETE, 200, options('Oauth2 Delete Account', ''), method='OPTIONS')
add('delete-get', 'owner', DELETE, 405, METHOD_GET, method='GET')
add('delete-owner-scoped-to-athlete', 'owner', DELETE, 403, SCOPED_ATHLETE, headers=OWNER_ATHLETE)
add('delete-owner-scoped-to-collaborator', 'owner', DELETE, 403, {'detail': 'Torna al tuo account per gestire le credenziali.'},
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': FULL})
add('delete-athlete', 'athlete', DELETE, 200, SCHEDULED_MSG, body={'ignored': True}, effect=deletion('athlete', actor='athlete'))
add('delete-athlete-again', 'athlete', DELETE, 200, SCHEDULED_MSG, effect=deletion('athlete', actor='athlete', changed=False))
add('delete-admin-impersonating-collaborator', 'admin', DELETE, 200, SCHEDULED_MSG,
    headers={'X-Impersonation-Id': '$session:admin_full', 'User-Id': FULL},
    effect=deletion('full_collaborator', actor='admin', session='admin_full', index=[IDS['admin_association'], 'actor.sport_association']))
add('delete-full-collaborator', 'full_collaborator', DELETE, 200, SCHEDULED_MSG,
    effect=deletion('full_collaborator', actor='full_collaborator', changed=False))
add('delete-restricted-collaborator', 'restricted_collaborator', DELETE, 200, SCHEDULED_MSG,
    effect=deletion('restricted_collaborator', actor='restricted_collaborator'))
add('delete-admin-impersonating-owner', 'admin', DELETE, 200, SCHEDULED_MSG,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=deletion('owner', actor='admin', session='admin_owner', index=[OWN, 'user.sport_association']))
add('delete-admin-without-session', 'admin', DELETE, 200, SCHEDULED_MSG,
    effect=deletion('admin', actor='admin', index=[IDS['admin_association'], 'user.sport_association']))
add('delete-owner-without-association', 'owner_without_association', DELETE, 200, SCHEDULED_MSG,
    effect=deletion('owner_without_association', actor='owner_without_association', before=SCHEDULED))

# --- oauth2/signup ------------------------------------------------------------------------------
SIGNUP = '/oauth2/signup'
NASTY = {'msg': 'You are nasty 🖕( •_• )🖕'}
SIGNUP_DESCRIPTION = ('API endpoint to sign up a user by oauth2 cap\n:param request: data: {first_name, last_name, username, email, '
                      'password} or {token, user}\n:return: token, refresh token')
WELCOME = 'email/account/email_welcome_message.html'


def body(number, **values):
    data = {'sport_association': False, 'first_name': 'Nome', 'last_name': 'Cognome', 'email': f'persona{number}@example.test',
            'username': f'persona-{number}', 'password': 'Valid-Pass1!'}
    data.update(values)
    return {key: value for key, value in data.items() if value is not ...}


def details(**errors):
    return {'msg': 'Invalid request.', 'details': errors}


def created(*, first='Nome', last='Cognome', username, email, password='Valid-Pass1!', role=2, connected=None,
            collaborator_role=1, permissions=None, actor=None, index=None, partial=None, invite=None, response=True, social=False):
    """create_user's row with its create entry (the validated phone is not passed: it stays None);
    partial / invite: the seeded rows deleted before it (social: the invite before it, the partial after it;
    password None: no usable password).
    response: tokens, build_login_response and the welcome e-mail follow."""
    welcome = render(WELCOME, {'first_name': first, 'last_name': last, **BRAND})
    return {'kind': 'signup', 'social': social, 'user': {'first_name': first, 'last_name': last, 'username': username, 'email': email, 'phone': None,
                                       'role': role, 'connected_user_id': connected, 'collaborator_role': collaborator_role,
                                       'collaborator_permissions': permissions},
            'password': password, 'actor': actor, 'index': index, 'partial': partial, 'invite': invite, 'response': response,
            'celery': [mail('$whitelabel | Benvenuto', welcome, [email.lower()])] if response else []}


def removed(*, partial=None, invite=None):
    """Only the deletions that precede a failure."""
    return {'kind': 'removed', 'partial': partial, 'invite': invite}


add('signup-options', 'anonymous', SIGNUP, 200, options('Oauth2 Signup', SIGNUP_DESCRIPTION), method='OPTIONS')
add('signup-get', 'anonymous', SIGNUP, 405, METHOD_GET, method='GET')
add('signup-empty', 'anonymous', SIGNUP, 400, details(sport_association=['This field is required.']))
add('signup-association-string', 'anonymous', SIGNUP, 400, details(sport_association=['Must be a boolean.']),
    body=body(1, sport_association='false'))
add('signup-association-number', 'anonymous', SIGNUP, 400, details(sport_association=['Must be a boolean.']),
    body=body(1, sport_association=0))
add('signup-form', 'anonymous', SIGNUP, 400, details(sport_association=['Must be a boolean.']),
    raw_body='sport_association=false&email=persona1%40example.test', content_type='application/x-www-form-urlencoded')
add('signup-association-true', 'anonymous', SIGNUP, 403,
    {'msg': 'Invalid request.', 'details': {'sport_association': ['Association accounts can only be created during instance setup.']}},
    body=body(1, sport_association=True))
add('signup-list-body', 'anonymous', SIGNUP, 500, SERVER, body=[{'sport_association': False}])
add('signup-missing-email', 'anonymous', SIGNUP, 500, SERVER, body=body(1, email=...))
add('signup-email-number', 'anonymous', SIGNUP, 500, SERVER, body=body(1, email=5))
add('signup-email-without-at', 'anonymous', SIGNUP, 500, SERVER, body=body(1, email='persona1.example.test'))
add('signup-disposable', 'anonymous', SIGNUP, 400, NASTY, body=body(1, email='persona1@YopMail.com'))
add('signup-disposable-subdomain', 'anonymous', SIGNUP, 400, NASTY, body=body(1, email='persona1@mail.yopmail.com'))
add('signup-disposable-wildcard', 'anonymous', SIGNUP, 400, NASTY, body=body(1, email='persona1@mailcatch.example'))
add('signup-disposable-custom', 'anonymous', SIGNUP, 400, NASTY, body=body(1, email='persona1@golee.it'))
# `raise ValidationError` without a message raises TypeError instead of the caught ValidationError.
add('signup-skrank', 'anonymous', SIGNUP, 500, SERVER, body=body(1, email='persona1@Skrank.com'))
add('signup-skrank-second-part', 'anonymous', SIGNUP, 500, SERVER, body=body(1, email='persona1@skrank.com@x'))
add('signup-unsupported-backend', 'anonymous', SIGNUP, 400, {'error': 'Unsupported backend: facebook'},
    body=body(1, token='social-token', backend='facebook'))
add('signup-unsupported-backend-list', 'anonymous', SIGNUP, 400, {'error': "Unsupported backend: ['google']"},
    body=body(1, token='social-token', backend=['google']))
add('signup-google-rejected-token', 'anonymous', SIGNUP, 401, {'error': 'Invalid social token'},
    body=body(1, token='social-token', backend='google-oauth2'), provider=['google-certs', 'google-tokeninfo'])
add('signup-apple-rejected-token', 'anonymous', SIGNUP, 401, {'error': 'Invalid social token'},
    body=body(1, token='social-token', backend='apple'), provider=['apple-keys'])
add('signup-missing-fields', 'anonymous', SIGNUP, 400,
    details(username=['This field is required.'], password=['This field is required.']), body=body(1, username=..., password=...))
add('signup-invalid-fields', 'anonymous', SIGNUP, 400,
    details(first_name=['Not a valid string.'], last_name=['Ensure this field has no more than 150 characters.'],
            email=['Enter a valid email address.'], phone=['Ensure this field has no more than 20 characters.'],
            username=['Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.'],
            password=['This value does not match the required pattern.']),
    body=body(1, first_name=True, last_name='c' * 151, email='persona1@b@c.com', phone='1' * 21, username='bad name!',
              password='weakpassword'))
add('signup-null-and-blank', 'anonymous', SIGNUP, 400,
    details(first_name=['This field may not be null.'], username=['This field may not be blank.'],
            password=['This field may not be blank.']),
    body=body(1, first_name=None, username='   ', password=' '))
add('signup-username-taken', 'anonymous', SIGNUP, 400, details(username=['A user with that username already exists.']),
    body=body(1, username='message-reads-700001'))
add('signup-username-long-invalid', 'anonymous', SIGNUP, 400,
    details(username=['Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.',
                      'Ensure this field has no more than 150 characters.']),
    body=body(1, username='!' * 151))
add('signup-password-trailing-text', 'anonymous', SIGNUP, 400, details(password=['This value does not match the required pattern.']),
    body=body(1, password='Valid-Pass1!\nsecond line'))
add('signup-invalid-transfer-token', 'anonymous', SIGNUP, 500, SERVER,
    body=body(1, subscription_transfer=True, subscription_transfer_token='not-a-uuid'))
add('signup-invalid-transfer-token-unused', 'anonymous', SIGNUP, 500, SERVER,
    body=body(1, subscription_transfer=False, subscription_transfer_token='not-a-uuid'))
add('signup-collaborator-token-not-found', 'anonymous', SIGNUP, 400, {'msg': 'Collaboration invite not found.'},
    body=body(2, collaboratorToken='oauth-invite-unknown'))
add('signup-missing-names-after-partial', 'anonymous', SIGNUP, 500, SERVER,
    body=body(3, email='Missing.Names@example.test', first_name=..., last_name=...),
    effect=removed(partial='missing_names'))
add('signup-username-case-conflict', 'anonymous', SIGNUP, 500, SERVER,
    body=body(4, email='conflict.partial@example.test', username='conflict-user'), effect=removed(partial='conflict'))
add('signup-collaborator-expired', 'anonymous', SIGNUP, 400, {'msg': 'Collaboration invite expired.'},
    body=body(5, email='collab.expired@example.test', collaboratorToken='oauth-invite-expired'), effect=removed(invite='expired'))
add('signup-collaborator-no-expiry', 'anonymous', SIGNUP, 500, SERVER,
    body=body(6, email='collab.open@example.test', collaboratorToken='oauth-invite-no-expiry'), effect=removed(invite='no_expiry'))
add('signup-athlete', 'anonymous', SIGNUP, 200, '$signup',
    body=body(7, first_name=' Mario ', last_name="D'Arco <it>", email='Nuovo.Atleta@Example.TEST', username='nuovo-atleta',
              password='  Valid-Pass1!  ', phone=None, unknown='ignored'),
    effect=created(first='Mario', last="D'Arco <it>", username='NUOVO-ATLETA', email='Nuovo.Atleta@example.test',
                   password='  Valid-Pass1!  ', partial='athlete'))
# Usernames are Unicode: UnicodeUsernameValidator accepts every letter and number, the stored name is str.upper()
# (U+00DF becomes SS); a NUL fails the pattern and the null-character check (DRF's qs_exists swallows the driver's refusal).
add('signup-unicode-username', 'anonymous', SIGNUP, 200, '$signup', body=body(31, username='Èlia_ß.é'),
    effect=created(username='ÈLIA_SS.É', email='persona31@example.test'))
add('signup-unicode-username-invalid-character', 'anonymous', SIGNUP, 400,
    details(username=['Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.']),
    body=body(32, username='Éli☃'))
add('signup-username-with-nul', 'anonymous', SIGNUP, 400,
    details(username=['Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.',
                      'Null characters are not allowed.']),
    body=body(33, username='abc\u0000def'))
add('signup-athlete-username-taken-now', 'anonymous', SIGNUP, 400, details(username=['A user with that username already exists.']),
    body=body(8, username='NUOVO-ATLETA'))
add('signup-athlete-owner-actor', 'owner', SIGNUP, 200, '$signup',
    body=body(9, phone='+39 012', first_name=12, last_name=1.5),
    effect=created(first='12', last='1.5', username='PERSONA-9', email='persona9@example.test', actor='owner',
                   index=[OWN, 'actor.sport_association']))
add('signup-athlete-admin-without-session', 'admin', SIGNUP, 200, '$signup', body=body(10, subscription_transfer=False,
                                                                                         subscription_transfer_token=TRANSFER_TOKEN),
    effect=created(username='PERSONA-10', email='persona10@example.test', actor='admin',
                   index=[IDS['admin_association'], 'actor.sport_association']))
add('signup-athlete-transfer-without-token', 'restricted_collaborator', SIGNUP, 200, '$signup',
    body=body(11, subscription_transfer=True),
    effect=created(username='PERSONA-11', email='persona11@example.test', actor='restricted_collaborator'))
add('signup-transfer-raises', 'anonymous', SIGNUP, 500, SERVER,
    body=body(12, email='transfer.raise@example.test', subscription_transfer=True, subscription_transfer_token=TRANSFER_TOKEN),
    effect=created(username='PERSONA-12', email='transfer.raise@example.test', partial='transfer', response=False))
add('signup-collaborator', 'anonymous', SIGNUP, 200, '$signup',
    body=body(13, email='Collab.One@example.test', collaboratorToken='oauth-invite-valid'),
    effect=created(username='PERSONA-13', email='Collab.One@example.test', role=3, connected=OWNER, collaborator_role=3,
                   permissions=['association.members.read'], partial='collaborator', invite='valid'))
add('signup-collaborator-token-used', 'anonymous', SIGNUP, 400, {'msg': 'Collaboration invite not found.'},
    body=body(14, collaboratorToken='oauth-invite-valid'))
add('signup-collaborator-duplicate-token', 'anonymous', SIGNUP, 200, '$signup',
    body=body(15, collaboratorToken='oauth-invite-duplicate'),
    effect=created(username='PERSONA-15', email='persona15@example.test', role=3, connected=OWNER, collaborator_role=2,
                   permissions={'b': [1, 'x'], 'a': None}, invite='duplicate_first'))
add('signup-collaborator-null-token', 'anonymous', SIGNUP, 200, '$signup',
    body=body(16, collaboratorToken=None),
    effect=created(username='PERSONA-16', email='persona16@example.test', role=3, connected=OWNER, collaborator_role=1,
                   invite='null_token'))
add('signup-collaborator-owner-without-association', 'anonymous', SIGNUP, 500, SERVER,
    body=body(17, collaboratorToken='oauth-invite-no-association'),
    effect=created(username='PERSONA-17', email='persona17@example.test', role=3, connected=NO_ASSOCIATION, collaborator_role=1,
                   invite='no_association', response=False))
add('signup-owner-scoped-to-athlete', 'owner', SIGNUP, 403, SCOPED_ATHLETE, body=body(18), headers=OWNER_ATHLETE)

# --- oauth2/signup, social path -----------------------------------------------------------------
SOCIAL_TOKEN = {'error': 'Invalid social token'}
CREDENTIALS = {'error': 'invalid credentials'}
GOOGLE, APPLE = ['google-certs'], ['apple-keys']
GOOGLE_FALLBACK = ['google-certs', 'google-tokeninfo']
ACCESS_TOKEN = 'oauth-social-access'  # the stub's token info and user info know it (user info carries no email_verified)
ACCESS = {'token': ACCESS_TOKEN, 'email': 'social.access@example.test'}


def identity(provider, email=..., verified=True, **options):
    """A provider ID token signed by the run's stub key: e-mail, email_verified and any other claim;
    options expired / wrong_signature / audience make the provider reject it."""
    claims = {'email': email, 'email_verified': verified}
    claims.update(options.pop('claims', {}))
    return {'$social': {'provider': provider, 'claims': {key: value for key, value in claims.items() if value is not ...},
                        **options}}


def social(number, token, backend='google-oauth2', **values):
    return body(number, **{'password': ..., 'token': token, 'backend': backend, **values})


def signin(user):
    """Tokens and build_login_response for the stored account; nothing is written or sent."""
    return {'kind': 'signin', 'user': person(user)[0]}


def sadd(name, status, response, data, calls, *, actor='anonymous', effect=None):
    add('signup-social-' + name, actor, SIGNUP, status, response, body=data, effect=effect, provider=calls)


# The provider rejects the token.
sadd('google-wrong-signature', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', wrong_signature=True)),
     GOOGLE_FALLBACK)
sadd('google-expired', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', expired=True)), GOOGLE_FALLBACK)
sadd('google-other-client', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', audience='other-client')),
     GOOGLE_FALLBACK)
sadd('apple-wrong-signature', 401, SOCIAL_TOKEN,
     social(20, identity('apple', 'persona20@example.test', 'true', wrong_signature=True), 'apple-id'), APPLE)
sadd('apple-expired', 401, SOCIAL_TOKEN, social(20, identity('apple', 'persona20@example.test', 'true', expired=True), 'apple'), APPLE)
sadd('apple-other-client', 401, SOCIAL_TOKEN,
     social(20, identity('apple', 'persona20@example.test', 'true', audience='other-client'), 'apple'), APPLE)
# The e-mail checks run before the provider is asked.
add('signup-social-disposable-body-email', 'anonymous', SIGNUP, 400, NASTY,
    body=social(20, identity('google', 'persona20@example.test'), email='persona20@yopmail.com'), provider=[])
# The identity's e-mail must be a verified one.
sadd('google-unverified', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', False)), GOOGLE)
sadd('google-verification-missing', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', ...)), GOOGLE)
sadd('google-verification-null', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', None)), GOOGLE)
sadd('google-verification-text-false', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', 'false')), GOOGLE)
sadd('google-verification-number', 401, SOCIAL_TOKEN, social(20, identity('google', 'persona20@example.test', 1)), GOOGLE)
sadd('google-email-missing', 401, SOCIAL_TOKEN, social(20, identity('google', ...)), GOOGLE)
sadd('google-email-empty', 401, SOCIAL_TOKEN, social(20, identity('google', '')), GOOGLE)
sadd('google-email-number', 401, SOCIAL_TOKEN, social(20, identity('google', 17)), GOOGLE)
sadd('google-access-token-without-verification', 401, SOCIAL_TOKEN, social(20, ACCESS_TOKEN, 'google'),
     ['google-certs', 'google-tokeninfo', 'google-userinfo'])
sadd('apple-unverified-text', 401, SOCIAL_TOKEN, social(20, identity('apple', 'persona20@example.test', 'false'), 'apple-id'), APPLE)
sadd('apple-unverified', 401, SOCIAL_TOKEN, social(20, identity('apple', 'persona20@example.test', False), 'apple-id'), APPLE)
sadd('apple-verification-missing', 401, SOCIAL_TOKEN, social(20, identity('apple', 'persona20@example.test', ...), 'apple'), APPLE)
sadd('apple-verification-other-case', 401, SOCIAL_TOKEN, social(20, identity('apple', 'persona20@example.test', 'True'), 'apple'),
     APPLE)
sadd('google-unverified-existing-owner', 401, SOCIAL_TOKEN, social(20, identity('google', base_email(OWNER), False)), GOOGLE)
sadd('google-unverified-existing-owner-with-invite', 401, SOCIAL_TOKEN,
     social(20, identity('google', base_email(OWNER).upper(), False), collaboratorToken='oauth-invite-social-kept'), GOOGLE)
sadd('apple-unverified-existing-athlete', 401, SOCIAL_TOKEN,
     social(20, identity('apple', base_email(ATHLETE), 'false'), 'apple', collaboratorToken='oauth-invite-social-kept'), APPLE)
# An account with the verified e-mail signs in as it is.
sadd('google-existing-owner', 200, '$signup', social(20, identity('google', base_email(OWNER))), GOOGLE, effect=signin('owner'))
sadd('google-existing-owner-with-invite', 200, '$signup',
     social(20, identity('google', base_email(OWNER).upper(), claims={'given_name': 'Altro', 'family_name': 'Nome'}),
            'google-identity', collaboratorToken='oauth-invite-social-kept', first_name='x', last_name='y', password='Valid-Pass1!'),
     GOOGLE, effect=signin('owner'))
sadd('google-existing-owner-unknown-invite', 200, '$signup',
     social(20, identity('google', base_email(OWNER)), 'google', collaboratorToken='oauth-invite-unknown'), GOOGLE,
     effect=signin('owner'))
sadd('apple-existing-athlete', 200, '$signup', social(20, identity('apple', base_email(ATHLETE), 'true'), 'apple-id'), APPLE,
     effect=signin('athlete'))
sadd('apple-existing-collaborator', 200, '$signup', social(20, identity('apple', base_email(FULL), True), 'apple'), APPLE,
     effect=signin('full_collaborator'))
sadd('google-existing-owner-bearer', 200, '$signup', social(20, identity('google', base_email(ATHLETE))), GOOGLE, actor='owner',
     effect=signin('athlete'))
sadd('google-existing-inactive', 401, CREDENTIALS, social(20, identity('google', 'Social.Inactive@example.test')), GOOGLE)
sadd('google-existing-shared-email', 401, CREDENTIALS, social(20, identity('google', 'shared.reset@example.test')), GOOGLE)
sadd('google-existing-otp-required', 401, {'msg': 'OTP code required.'}, social(20, identity('google', 'social.otp@example.test')),
     GOOGLE)
sadd('google-existing-otp-short', 401, {'msg': 'OTP code required.'},
     social(20, identity('google', 'social.otp@example.test'), otp='12345'), GOOGLE)
sadd('google-existing-otp-number', 500, SERVER, social(20, identity('google', 'social.otp@example.test'), otp=123456), GOOGLE)
sadd('google-existing-otp-wrong', 401, {'msg': 'OTP code not valid.'},
     social(20, identity('google', 'social.otp@example.test'), otp='abcdef'), GOOGLE)
sadd('apple-existing-otp-valid', 200, '$signup',
     social(20, identity('apple', 'social.otp@example.test', 'true'), 'apple', otp={'$totp': 'social_otp'}), APPLE,
     effect=signin('social_otp'))
# No account: the collaborator invite, then the new account.
sadd('collaborator-invite-not-found', 400, {'msg': 'Collaboration invite not found.'},
     social(21, identity('google', 'persona21@example.test'), collaboratorToken='oauth-invite-unknown'), GOOGLE)
sadd('collaborator-invite-expired', 400, {'msg': 'Collaboration invite expired.'},
     social(21, identity('google', 'persona21@example.test'), collaboratorToken='oauth-invite-social-expired'), GOOGLE,
     effect=removed(invite='social_expired'))
sadd('username-number', 500, SERVER, social(21, identity('google', 'persona21@example.test'), username=21), GOOGLE)
sadd('username-conflict', 500, SERVER, social(21, identity('google', 'persona21@example.test'), username='conflict-user'), GOOGLE)
# A name that is not a string is stored, mailed and answered as its str(); None fails the insert.
sadd('name-null', 500, SERVER, social(21, identity('google', 'persona21@example.test'), first_name=None), GOOGLE)
sadd('name-too-long-list', 500, SERVER, social(21, identity('google', 'persona21@example.test'), last_name=['x' * 150]), GOOGLE)
sadd('names-not-strings', 200, '$signup',
     social(40, identity('google', 'persona40@example.test'), first_name=['a', 1, None, {'k': "it's <b>"}], last_name=12), GOOGLE,
     effect=created(first="['a', 1, None, {'k': \"it's <b>\"}]", last='12', username='PERSONA-40', email='persona40@example.test',
                    password=None, social=True))
sadd('names-boolean-and-object', 200, '$signup',
     social(41, identity('google', 'persona41@example.test', claims={'given_name': True, 'family_name': 0}), last_name={'a': False}),
     GOOGLE, effect=created(first='True', last="{'a': False}", username='PERSONA-41', email='persona41@example.test', password=None,
                            social=True))
sadd('google-athlete', 200, '$signup',
     social(22, identity('google', 'Social.New@Example.TEST', claims={'given_name': 'Zoë', 'family_name': "D'Arco <it>"}),
            email='Social.Partial@example.test', username='social-nuovo', first_name='ignored', last_name='ignored', unknown=1),
     GOOGLE, effect=created(first='Zoë', last="D'Arco <it>", username='SOCIAL-NUOVO', email='Social.New@example.test', password=None,
                            partial='social', social=True))
sadd('google-athlete-again', 200, '$signup', social(22, identity('google', 'social.new@example.test')), GOOGLE,
     effect={'kind': 'signin', 'user': '$created:oauth-accounts-signup-social-google-athlete'})
sadd('google-names-from-body', 200, '$signup',
     social(23, identity('google', 'persona23@example.test', claims={'given_name': '', 'family_name': None}), 'google',
            first_name=' Body ', last_name='Sent'),
     GOOGLE, effect=created(first=' Body ', last='Sent', username='PERSONA-23', email='persona23@example.test', password=None,
                            social=True))
sadd('apple-text-claim', 200, '$signup',
     social(24, identity('apple', 'persona24@example.test', 'true'), 'apple-id', first_name=..., last_name=...), APPLE,
     effect=created(first='', last='', username='PERSONA-24', email='persona24@example.test', password=None, social=True))
sadd('apple-boolean-claim-owner-bearer', 200, '$signup', social(25, identity('apple', 'persona25@example.test', True), 'apple'), APPLE,
     actor='owner', effect=created(username='PERSONA-25', email='persona25@example.test', password=None, social=True, actor='owner',
                                   index=[OWN, 'actor.sport_association']))
sadd('apple-private-relay', 200, '$signup',
     social(26, identity('apple', 'r4nd0mrelay@privaterelay.appleid.com', 'true', claims={'is_private_email': 'true'}), 'apple-id'),
     APPLE, effect=created(username='PERSONA-26', email='r4nd0mrelay@privaterelay.appleid.com', password=None, social=True))
sadd('deleted-account-email', 200, '$signup', social(27, identity('google', 'social.deleted@example.test')), GOOGLE,
     effect=created(username='PERSONA-27', email='social.deleted@example.test', password=None, social=True))
sadd('collaborator', 200, '$signup',
     social(28, identity('google', 'collab.social@example.test', claims={'given_name': 'Colla', 'family_name': 'Boratore'}),
            collaboratorToken='oauth-invite-social-valid'),
     GOOGLE, effect=created(first='Colla', last='Boratore', username='PERSONA-28', email='collab.social@example.test', password=None,
                            role=3, connected=OWNER, collaborator_role=3,
                            permissions=['association.members.read', 'association.members.update'], invite='social_valid', social=True))
sadd('collaborator-invite-used', 400, {'msg': 'Collaboration invite not found.'},
     social(29, identity('google', 'persona29@example.test'), collaboratorToken='oauth-invite-social-valid'), GOOGLE)
sadd('transfer-raises', 500, SERVER,
     social(30, identity('google', 'persona30@example.test'), subscription_transfer=True, subscription_transfer_token=TRANSFER_TOKEN),
     GOOGLE, effect=created(username='PERSONA-30', email='persona30@example.test', password=None, social=True, response=False))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'oauth2 signup, partial signup, password reset and account deletion',
                              'ids': IDS, 'users': USERS, 'scheduled': SCHEDULED, 'invites': INVITES, 'partials': PARTIALS,
                              'reset_tokens': RESET_TOKENS, 'card': CARD, 'access': ACCESS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
