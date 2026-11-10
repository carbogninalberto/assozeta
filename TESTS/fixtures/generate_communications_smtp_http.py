"""Author the communications/settings/smtp info, update and verify HTTP sequence (communications-smtp.md).

All three are @api_view function views with IsAuthenticated only; the paths are
not in permissions_registry.py (FULL collaborators pass, other collaborators get
the unmapped-route 403). Each view reads request.user.sport_association, a
cached_property on User: athletes raise PermissionDenied('User is not a sport
association'), collaborators resolve their connected owner, every other role
SportAssociation.objects.get(user=...) (ActiveManager: a missing or soft-deleted
association raises DoesNotExist, HTTP 500).

The configuration is CommunicationConfiguration.objects.filter(sport_association=
...).first(): the lowest primary key of the association's rows.

info (GET): with no row and is_sport_association(), creates one with port 587 and
encryption TLS (defaults: daily_email_limit 1000, daily_email_balance 0), audited
as a create without email_smtp_password; answers CommunicationConfigurationSerializer
(host, port, user, sender name, encryption, limit, balance; never the password).

update (PATCH): no row is 404 {'msg': 'info not found.'}; validates
CommunicationConfigurationPatchSerializer (partial: host/user/password/sender
CharField(255) nullable and blank, port PositiveIntegerField, encryption choice
SSL/TLS/NONE); any error is 400 {'msg': 'info not valid.'}; save() writes every
column and auditlog records the changed fields except the password (none: no
entry). Read-only and unknown keys are ignored.

verify (GET): no row is 404; otherwise verify_smtp(): smtplib connects only when
the host is truthy, so an empty host fails at the first command with
'please run connect() first'; a host with exactly one ':' and a falsy port splits
the port and int() failing raises OSError('nonnumeric port'). Every other
configuration opens a connection: SMTP_SSL, or SMTP followed by starttls() for TLS,
then login(), sendmail("<sender name or WHITELABEL_NAME> <user>", owner email, the
test message as bytes) and quit(). Success is {'verified': True, 'msg': 'Ok'}; any
exception is {'verified': False, 'msg': str(exception)}. The run's fake SMTP servers
record the client bytes; a case's setup writes the listed columns of the own row.
Run with ``python3 -I`` after the message-mutations fixture.
"""
import base64
import hmac
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('communications_smtp_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
INFO, UPDATE, VERIFY = '/communications/settings/smtp/info', '/communications/settings/smtp/update', '/communications/settings/smtp/verify'

COLUMNS = ('email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_password', 'email_sender_name', 'email_encryption',
           'daily_email_limit', 'daily_email_balance')
# Seeded in reverse key order: first() must pick the lower key, not the first inserted row.
CONFIGURATIONS = {
    'foreign_second': {'id': 'a1000000-0000-4000-8000-000000961002', 'association': FOREIGN, 'email_smtp_host': 'smtp.example.test',
                       'email_smtp_port': 465, 'email_smtp_user': 'second@example.test', 'email_smtp_password': 'placeholder-second',
                       'email_sender_name': 'Seconda', 'email_encryption': 'SSL', 'daily_email_limit': 10, 'daily_email_balance': 3},
    'foreign_first': {'id': 'a1000000-0000-4000-8000-000000961001', 'association': FOREIGN, 'email_smtp_host': None,
                      'email_smtp_port': 25, 'email_smtp_user': 'first@example.test', 'email_smtp_password': 'placeholder-first',
                      'email_sender_name': None, 'email_encryption': 'NONE', 'daily_email_limit': 500, 'daily_email_balance': 12},
    'deleted_association': {'id': 'a1000000-0000-4000-8000-000000961003', 'association': IDS['deleted_association'],
                            'email_smtp_host': None, 'email_smtp_port': 587, 'email_smtp_user': None, 'email_smtp_password': None,
                            'email_sender_name': None, 'email_encryption': 'TLS', 'daily_email_limit': 1000, 'daily_email_balance': 0},
}
OWN_DEFAULT = {'email_smtp_host': None, 'email_smtp_port': 587, 'email_smtp_user': None, 'email_smtp_password': None,
               'email_sender_name': None, 'email_encryption': 'TLS', 'daily_email_limit': 1000, 'daily_email_balance': 0}
STATE = dict(OWN_DEFAULT)
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'communications-smtp-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def info(row):
    return {key: row[key] for key in COLUMNS if key != 'email_smtp_password'}


def audit_text(value):
    return str(value)


def updated(actor, fields, *, session=None):
    """Apply a partial update to the own configuration; the audit lists changed fields but the password."""
    changes = {key: [audit_text(STATE[key]), audit_text(value)] for key, value in fields.items()
               if STATE[key] != value and key != 'email_smtp_password'}
    STATE.update(fields)
    return {'kind': 'update', 'label': 'own', 'actor': actor, 'session': session, 'row': dict(STATE),
            'audit': [{'action': 1, 'changes': changes}] if changes else []}


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ATHLETE = {'detail': 'User is not a sport association'}
NOT_FOUND = {'msg': 'info not found.'}
INVALID = {'msg': 'info not valid.'}
UPDATED = {'msg': 'info updated.'}
NO_CONNECTION = {'verified': False, 'msg': 'please run connect() first'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}


def unmapped(path):
    return {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}


# Identity and dispatch, before any configuration exists for the own association.
for route, path, method, wrong in (('info', INFO, 'GET', 'POST'), ('update', UPDATE, 'PATCH', 'GET'), ('verify', VERIFY, 'GET', 'PUT')):
    body = {} if method == 'PATCH' else None
    add(f'{route}-anonymous', 'anonymous', path, 401, ANONYMOUS, method=method, body=body)
    add(f'{route}-admin-without-session', 'admin', path, 403, ADMIN, method=method, body=body)
    add(f'{route}-restricted-unmapped', 'restricted_collaborator', path, 403, unmapped(path), method=method, body=body)
    add(f'{route}-owner-scoped-athlete-denied', 'owner', path, 403, SCOPED_DENIED, method=method, body=body, headers=SCOPED)
    add(f'{route}-athlete-not-association', 'athlete', path, 403, ATHLETE, method=method, body=body)
    add(f'{route}-owner-without-association-server-error', 'owner_without_association', path, 500, SERVER, method=method, body=body)
    add(f'{route}-deleted-association-server-error', 'deleted_association_owner', path, 500, SERVER, method=method, body=body)
    add(f'{route}-wrong-method', 'owner', path, 405, {'detail': f'Method "{wrong}" not allowed.'}, method=wrong)
    add(f'{route}-options', 'owner', path, 200, options(f'Configuration Smtp {route.title()}'), method='OPTIONS')
add('options-restricted-unmapped', 'restricted_collaborator', INFO, 403, unmapped(INFO), method='OPTIONS')

add('update-before-configuration-not-found', 'owner', UPDATE, 404, NOT_FOUND, method='PATCH', body={'email_smtp_host': 'smtp.example.test'})
add('verify-before-configuration-not-found', 'full_collaborator', VERIFY, 404, NOT_FOUND)

# info creates the own configuration (audited create, password excluded).
add('info-owner-creates-configuration', 'owner', INFO, 200, info(OWN_DEFAULT), effect={
    'kind': 'create', 'label': 'own', 'actor': 'owner', 'session': None, 'row': dict(OWN_DEFAULT), 'audit': [{'action': 0, 'changes': {
        'communication_configuration_id': ['None', '$own'], 'sport_association': ['None', OWN], 'email_smtp_port': ['None', '587'],
        'email_encryption': ['None', 'TLS'], 'daily_email_limit': ['None', '1000'], 'daily_email_balance': ['None', '0']}}]})
add('info-owner-existing', 'owner', INFO, 200, info(OWN_DEFAULT))
add('info-full-collaborator-owner-scope', 'full_collaborator', INFO, 200, info(OWN_DEFAULT))
add('info-admin-impersonating-owner', 'admin', INFO, 200, info(OWN_DEFAULT), headers=ADMIN_OWNER)
add('info-foreign-owner-lowest-key', 'foreign_owner', INFO, 200, info(CONFIGURATIONS['foreign_first']))
add('verify-owner-without-host', 'owner', VERIFY, 200, NO_CONNECTION)
add('verify-foreign-owner-lowest-key-without-host', 'foreign_owner', VERIFY, 200, NO_CONNECTION)

# Every validation error collapses into the generic 400; nothing is saved.
for label, body in (('host-too-long', {'email_smtp_host': 'h' * 256}), ('host-bool', {'email_smtp_host': True}),
                    ('user-list', {'email_smtp_user': ['x']}), ('password-object', {'email_smtp_password': {'a': 1}}),
                    ('sender-too-long', {'email_sender_name': 's' * 256}), ('port-negative', {'email_smtp_port': -1}),
                    ('port-text', {'email_smtp_port': 'abc'}), ('port-fraction', {'email_smtp_port': 25.5}),
                    ('port-too-large', {'email_smtp_port': 2147483648}), ('port-bool', {'email_smtp_port': True}),
                    ('encryption-label', {'email_encryption': 'STARTTLS'}), ('encryption-blank', {'email_encryption': ''}),
                    ('encryption-null', {'email_encryption': None}), ('encryption-lowercase', {'email_encryption': 'ssl'}),
                    ('valid-and-invalid', {'email_smtp_host': 'smtp.example.test', 'email_smtp_port': 'x'}),
                    ('host-null-character', {'email_smtp_host': 'a\x00b'}), ('list-body', [])):
    add(f'update-invalid-{label}', 'owner', UPDATE, 400, INVALID, method='PATCH', body=body)
add('update-invalid-null-body', 'owner', UPDATE, 400, INVALID, method='PATCH', raw_body='null', content_type='application/json')

add('update-empty-body-no-audit', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={}, effect=updated('owner', {}))
add('update-owner', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={
    'email_smtp_host': '  smtp.example.test  ', 'email_smtp_port': '465', 'email_smtp_user': 'mailer@example.test',
    'email_smtp_password': 'placeholder-password', 'email_sender_name': 'Associazione è', 'email_encryption': 'SSL',
    'daily_email_limit': 5, 'daily_email_balance': 99, 'communication_configuration_id': FOREIGN, 'sport_association': FOREIGN,
    'unknown': 'ignored'},
    effect=updated('owner', {'email_smtp_host': 'smtp.example.test', 'email_smtp_port': 465, 'email_smtp_user': 'mailer@example.test',
                             'email_smtp_password': 'placeholder-password', 'email_sender_name': 'Associazione è',
                             'email_encryption': 'SSL'}))
add('info-owner-after-update', 'full_collaborator', INFO, 200, info(STATE))
add('update-password-only-no-audit', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={'email_smtp_password': 'placeholder-rotated'},
    effect=updated('owner', {'email_smtp_password': 'placeholder-rotated'}))
add('update-same-values-no-audit', 'full_collaborator', UPDATE, 200, UPDATED, method='PATCH',
    body={'email_smtp_host': 'smtp.example.test', 'email_encryption': 'SSL'}, effect=updated('full_collaborator', {}))
add('update-coerced-values', 'full_collaborator', UPDATE, 200, UPDATED, method='PATCH',
    body={'email_sender_name': 5, 'email_smtp_port': 25.0, 'email_smtp_user': None, 'email_encryption': 'NONE'},
    effect=updated('full_collaborator', {'email_sender_name': '5', 'email_smtp_port': 25, 'email_smtp_user': None,
                                         'email_encryption': 'NONE'}))
add('update-admin-impersonating-owner', 'admin', UPDATE, 200, UPDATED, method='PATCH', body={'email_sender_name': 'Impersonato'},
    headers=ADMIN_OWNER, effect=updated('admin', {'email_sender_name': 'Impersonato'}, session='admin_owner'))
# HTML input: an empty value of a nullable field that allows blank stays '' (CharField), the integer becomes None.
add('update-form-blank-host-and-port', 'owner', UPDATE, 200, UPDATED, method='PATCH', raw_body='email_smtp_host=&email_smtp_port=',
    content_type='application/x-www-form-urlencoded', effect=updated('owner', {'email_smtp_host': '', 'email_smtp_port': None}))
add('verify-owner-blank-host', 'owner', VERIFY, 200, NO_CONNECTION)
add('update-host-with-text-port', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={'email_smtp_host': 'smtp.example.test:abc'},
    effect=updated('owner', {'email_smtp_host': 'smtp.example.test:abc'}))
add('verify-owner-nonnumeric-port', 'full_collaborator', VERIFY, 200, {'verified': False, 'msg': 'nonnumeric port'})
add('update-port-zero', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={'email_smtp_port': 0, 'email_encryption': 'TLS'},
    effect=updated('owner', {'email_smtp_port': 0, 'email_encryption': 'TLS'}))
add('verify-owner-port-zero-nonnumeric-port', 'owner', VERIFY, 200, {'verified': False, 'msg': 'nonnumeric port'})
add('update-host-with-empty-port', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={'email_smtp_host': 'smtp.example.test:'},
    effect=updated('owner', {'email_smtp_host': 'smtp.example.test:'}))
add('verify-owner-empty-port-nonnumeric-port', 'admin', VERIFY, 200, {'verified': False, 'msg': 'nonnumeric port'}, headers=ADMIN_OWNER)
add('update-null-host', 'owner', UPDATE, 200, UPDATED, method='PATCH', body={'email_smtp_host': None, 'email_encryption': 'SSL'},
    effect=updated('owner', {'email_smtp_host': None, 'email_encryption': 'SSL'}))
add('verify-owner-ssl-without-host', 'owner', VERIFY, 200, NO_CONNECTION)
add('info-owner-final', 'owner', INFO, 200, info(STATE))

# --- verify against the run's SMTP servers ---------------------------------------------------------
SMTP_USER, SMTP_PASSWORD = 'mailer@example.test', 'placeholder-smtp'
# The run's environment sets WHITELABEL_NAME.
WHITELABEL = 'Assozeta'
MESSAGE = (f'Subject: Email di test da {WHITELABEL}, SMTP configurato correttamente.\n\n'
           'Se hai ricevuto questa email, significa che hai configurato il server SMTP correttamente.')
VERIFIED = {'verified': True, 'msg': 'Ok'}


def failed(text):
    return {'verified': False, 'msg': text}


def plain_auth(password=SMTP_PASSWORD):
    return 'AUTH PLAIN ' + base64.b64encode(f'\0{SMTP_USER}\0{password}'.encode()).decode() + '\r\n'


def handshake(server):
    units = ['ehlo <local>\r\n']
    if server == 'starttls':
        units += ['STARTTLS\r\n', '<tls>', 'ehlo <local>\r\n']
    return units


MAIL = f'mail from:<{SMTP_USER}> size={len(MESSAGE.encode())}\r\n'
RCPT = 'rcpt to:<$owner>\r\n'
DELIVERY = [MAIL, RCPT, 'data\r\n', MESSAGE + '\r\n.\r\n', 'quit\r\n', '<quit>']


def verify(name, actor, server, response, connections, *, headers=None, opaque=0, **columns):
    """One verify case: the setup writes the connection columns; the row is otherwise untouched.
    opaque: that many connections whose bytes are not SMTP text (a TLS ClientHello) reach the server."""
    setup = {'email_smtp_host': '127.0.0.1', 'email_smtp_port': f'$port:{server}', 'email_smtp_user': SMTP_USER,
             'email_smtp_password': SMTP_PASSWORD, **columns}
    STATE.update(setup)
    smtp = {'server': server, 'connections': connections} if connections else None
    if opaque:
        smtp = {'server': server, 'opaque': opaque}
    add(name, actor, VERIFY, 200, response, headers=headers, effect={'kind': 'verify', 'row': dict(STATE), 'smtp': smtp})
    cases[-1]['setup'] = setup


verify('verify-starttls-sends-test-email', 'owner', 'starttls', VERIFIED, [handshake('starttls') + [plain_auth()] + DELIVERY],
       email_encryption='TLS', email_sender_name='Associazione è')
verify('verify-ssl-default-sender-name', 'full_collaborator', 'ssl', VERIFIED, [handshake('ssl') + [plain_auth()] + DELIVERY],
       email_encryption='SSL', email_sender_name=None)
verify('verify-plain-blank-sender-name', 'admin', 'plain', VERIFIED, [handshake('plain') + [plain_auth()] + DELIVERY],
       headers=ADMIN_OWNER, email_encryption='NONE', email_sender_name='')
# login() tries PLAIN, then LOGIN; the last reply is the exception.
verify('verify-wrong-password', 'owner', 'starttls', failed("(535, b'denied')"),
       [handshake('starttls') + [plain_auth('placeholder-wrong'),
                                 'AUTH LOGIN ' + base64.b64encode(SMTP_USER.encode()).decode() + '\r\n', '<closed>']],
       email_encryption='TLS', email_smtp_password='placeholder-wrong')
verify('verify-server-without-auth', 'owner', 'noauth', failed('SMTP AUTH extension not supported by server.'),
       [['ehlo <local>\r\n', '<closed>']], email_encryption='NONE')
verify('verify-server-without-starttls', 'owner', 'plain', failed('STARTTLS extension not supported by server.'),
       [['ehlo <local>\r\n', '<closed>']], email_encryption='TLS')
verify('verify-connection-refused', 'owner', 'closed', failed('$refused'), None, email_encryption='NONE')
verify('verify-recipient-refused', 'owner', 'norcpt', failed("{'$owner': (550, b'refused')}"),
       [['ehlo <local>\r\n', plain_auth(), MAIL, RCPT, 'rset\r\n', '<closed>']], email_encryption='NONE')
verify('verify-sender-refused', 'full_collaborator', 'nomail',
       failed(f"(550, b'sender refused', 'A.S.D. Sport & Co <{SMTP_USER}>')"),
       [['ehlo <local>\r\n', plain_auth(), MAIL, 'rset\r\n', '<closed>']], email_encryption='NONE',
       email_sender_name='A.S.D. Sport & Co')
# The server accepts and never greets: SMTP_TIMEOUT_SECONDS later the constructor's getreply() gives up.
verify('verify-server-never-greets', 'owner', 'silent', failed('Connection unexpectedly closed: timed out'), [['<closed>']],
       email_encryption='NONE')

# SSL chosen for a port that speaks plain SMTP first: the greeting arrives where the ServerHello is expected and
# str(ssl.SSLError) is OpenSSL's reason with CPython 3.13.16's call site.
verify('verify-ssl-on-starttls-port', 'owner', 'starttls',
       failed('[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)'), None, opaque=1, email_encryption='SSL')
verify('verify-ssl-on-plain-port', 'full_collaborator', 'plain',
       failed('[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)'), None, opaque=1, email_encryption='SSL')
# --- configurations smtplib reads its own way -------------------------------------------------------
B64 = lambda text: base64.b64encode(text.encode()).decode()  # noqa: E731
LOGIN_USER = f'AUTH LOGIN {B64(SMTP_USER)}\r\n'
# connect() with a falsy port takes the number after the host's only ':' (int() of the text).
verify('verify-port-in-host', 'owner', 'plain', VERIFIED, [handshake('plain') + [plain_auth()] + DELIVERY],
       email_smtp_host='127.0.0.1:{port:plain}', email_smtp_port=None, email_encryption='NONE', email_sender_name=None)
verify('verify-port-in-host-wide-digits', 'full_collaborator', 'plain', VERIFIED, [handshake('plain') + [plain_auth()] + DELIVERY],
       email_smtp_host='127.0.0.1: +{wideport:plain}　', email_smtp_port=0, email_encryption='NONE', email_sender_name=None)
verify('verify-port-in-host-ssl', 'owner', 'ssl', VERIFIED, [handshake('ssl') + [plain_auth()] + DELIVERY],
       email_smtp_host='127.0.0.1:{port:ssl}', email_smtp_port=0, email_encryption='SSL', email_sender_name=None)
verify('verify-port-in-host-starttls', 'owner', 'starttls', VERIFIED, [handshake('starttls') + [plain_auth()] + DELIVERY],
       email_smtp_host='127.0.0.1:{port:starttls}', email_smtp_port=None, email_encryption='TLS', email_sender_name=None)
# login(None, ...): AUTH PLAIN formats the missing value as the text None; the from text does the same.
verify('verify-missing-password', 'owner', 'plain', failed("(535, b'denied')"),
       [handshake('plain') + [f'AUTH PLAIN {B64(chr(0) + SMTP_USER + chr(0) + "None")}\r\n', LOGIN_USER, '<closed>']],
       email_smtp_password=None, email_encryption='NONE', email_sender_name=None)
verify('verify-missing-user', 'owner', 'plain', VERIFIED,
       [handshake('plain') + [f'AUTH PLAIN {B64(chr(0) + "None" + chr(0) + SMTP_PASSWORD)}\r\n',
                              f'mail from:<None> size={len(MESSAGE.encode())}\r\n'] + DELIVERY[1:]],
       email_smtp_user=None, email_encryption='NONE', email_sender_name=None)
verify('verify-missing-user-and-password', 'owner', 'plain', failed("(535, b'denied')"),
       [handshake('plain') + [f'AUTH PLAIN {B64(chr(0) + "None" + chr(0) + "None")}\r\n', 'AUTH LOGIN\r\n', '<closed>']],
       email_smtp_user=None, email_smtp_password=None, email_encryption='NONE', email_sender_name=None)
# Servers that challenge: CRAM-MD5 first when offered, then LOGIN with one challenge per value still to send.
NONE_ENCODE = "'NoneType' object has no attribute 'encode'"
CRAM = base64.b64encode(f'{SMTP_USER} '.encode() + hmac.new(SMTP_PASSWORD.encode(), b'<12345.67890@fake.smtp.test>', 'md5')
                        .hexdigest().encode()).decode() + '\r\n'
verify('verify-cram-md5', 'owner', 'cram', VERIFIED, [handshake('cram') + ['AUTH CRAM-MD5\r\n', CRAM] + DELIVERY],
       email_encryption='NONE', email_sender_name=None)
CRAM_NONE = base64.b64encode(b'None ' + hmac.new(SMTP_PASSWORD.encode(), b'<12345.67890@fake.smtp.test>', 'md5')
                             .hexdigest().encode()).decode() + '\r\n'
verify('verify-cram-md5-missing-user', 'owner', 'cram', VERIFIED,
       [handshake('cram') + ['AUTH CRAM-MD5\r\n', CRAM_NONE, f'mail from:<None> size={len(MESSAGE.encode())}\r\n'] + DELIVERY[1:]],
       email_smtp_user=None, email_encryption='NONE', email_sender_name=None)
verify('verify-cram-md5-missing-password', 'owner', 'cram', failed(NONE_ENCODE),
       [handshake('cram') + ['AUTH CRAM-MD5\r\n', '<closed>']], email_smtp_password=None, email_encryption='NONE',
       email_sender_name=None)
verify('verify-cram-md5-accented-password', 'owner', 'cram',
       failed("'ascii' codec can't encode characters in position 4-5: ordinal not in range(128)"),
       [handshake('cram') + ['AUTH CRAM-MD5\r\n', '<closed>']], email_smtp_password='segrèòto', email_encryption='NONE',
       email_sender_name=None)
verify('verify-login-challenges', 'full_collaborator', 'challenge', VERIFIED,
       [handshake('challenge') + [LOGIN_USER, B64(SMTP_PASSWORD) + '\r\n'] + DELIVERY], email_encryption='NONE',
       email_sender_name=None)
verify('verify-login-missing-user', 'owner', 'challenge', failed(NONE_ENCODE),
       [handshake('challenge') + ['AUTH LOGIN\r\n', '<closed>']], email_smtp_user=None, email_encryption='NONE',
       email_sender_name=None)
verify('verify-login-missing-password', 'owner', 'challenge', failed(NONE_ENCODE),
       [handshake('challenge') + [LOGIN_USER, '<closed>']], email_smtp_password=None, email_encryption='NONE',
       email_sender_name=None)
verify('verify-login-accented-user', 'owner', 'challenge',
       failed("'ascii' codec can't encode character '\\xe0' in position 4: ordinal not in range(128)"),
       [handshake('challenge') + ['<closed>']], email_smtp_user='città@example.test', email_encryption='NONE',
       email_sender_name=None)
# The from text '<sender name> <user>' goes through smtplib.quoteaddr: a name the address parser reads as a second
# address is sent whole, a comment is dropped, and a command that is not ASCII is never sent.
verify('verify-sender-name-with-comma', 'owner', 'plain', VERIFIED,
       [handshake('plain') + [plain_auth(), f'mail from:<Sport, Club <{SMTP_USER}>> size={len(MESSAGE.encode())}\r\n'] + DELIVERY[1:]],
       email_encryption='NONE', email_sender_name='Sport, Club')
verify('verify-sender-name-with-comment', 'owner', 'plain', VERIFIED, [handshake('plain') + [plain_auth()] + DELIVERY],
       email_encryption='NONE', email_sender_name='Segreteria (A.S.D.) "2024": soci')
verify('verify-sender-name-with-address', 'owner', 'plain',
       failed("'ascii' codec can't encode character '\\xe0' in position 15: ordinal not in range(128)"),
       [handshake('plain') + [plain_auth(), '<closed>']], email_encryption='NONE', email_sender_name='Città <info@example.test>')
verify('verify-sender-refused-quoted-name', 'owner', 'nomail',
       failed(f"(550, b'sender refused', 'L\\'Aquila \"Sport\" 🏐 <{SMTP_USER}>')"),
       [['ehlo <local>\r\n', plain_auth(), MAIL, 'rset\r\n', '<closed>']], email_encryption='NONE',
       email_sender_name='L\'Aquila "Sport" 🏐')

# A host name the resolver does not know: str(socket.gaierror) with the C library's EAI_NONAME code and text
# ('$unresolved'; the names are reserved ones, RFC 6761).
verify('verify-unknown-host', 'owner', 'closed', failed('$unresolved'), None, email_encryption='TLS',
       email_smtp_host='smtp.mistyped.invalid', email_smtp_port=587)
verify('verify-unknown-host-ssl', 'admin', 'closed', failed('$unresolved'), None, headers=ADMIN_OWNER,
       email_encryption='SSL', email_smtp_host='mail.mistyped.invalid', email_smtp_port=465)
verify('verify-unknown-host-no-encryption', 'owner', 'closed', failed('$unresolved'), None, email_encryption='NONE',
       email_smtp_host='smtp_x.mistyped.invalid', email_smtp_port=25)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'SMTP configuration info, update and verify authored sequence with audit effects',
                              'ids': IDS, 'smtp_password': SMTP_PASSWORD, 'configurations': CONFIGURATIONS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
