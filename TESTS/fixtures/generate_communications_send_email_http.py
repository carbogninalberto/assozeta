"""Author the communications/send/email HTTP sequence (communications-send-email.md).

configuration_send_email (BE/communications/views.py) is an @api_view(['POST'])
with IsAuthenticated only; the path is not in permissions_registry.py (FULL
collaborators pass, other collaborators get the unmapped-route 403). In order:

1. logger.info reads request.user.sport_association (athletes: 403 'User is not
   a sport association'; a missing or soft-deleted association: 500).
2. `'message_id' in data and data['message_id']`: a non-dict body raises (500).
   With a truthy message_id, Message.objects.filter(message_id=..., sport_association=...)
   .first(): a value UUIDField.to_python rejects is a 500, a miss is 404
   {'msg': 'message not found.'}; a hit assigns data['message'] and
   data['subject'] (a QueryDict body is immutable: 500).
3. EmailSerializer.is_valid: falsy email/subject/message raise
   ValidationError('<Field> is required') (a list body), email.split(',') (a
   non-string raises: 500), check_email on each piece raises
   ValidationError('Email is not valid.'), then DRF validates email
   CharField(), subject CharField(max_length=100), message CharField(max_length=10000).
4. Without message_id a Message(type EMAIL) is created (audited, indexed
   direct). One MessageTransaction per comma-separated recipient (not audited).
   Every create autocommits.
5. CommunicationConfiguration.objects.filter(sport_association=...).first()
   (None: AttributeError, 500 after the writes), then send_email(subject, body,
   recipient_list): balance >= limit is (False, 'Daily email limit reached');
   without host/port/user/password, or when verify_smtp(send_email=False)
   fails (connect, STARTTLS for TLS, login; the socket is closed without
   QUIT), send_mail_async.apply_async(kwargs=...) with DEFAULT_FROM_EMAIL's
   WHITELABEL_NAME replaced by the denomination; otherwise a MIMEMultipart with
   a base64 text/plain part is sent on a second connection, then
   daily_email_balance += 1 and save() (audited update, index direct). Any
   exception there returns (False, exception), which DRF cannot render: 500.
6. 200 {'msg': msg, 'result': result}.

Each case may set up the own association's configuration first (audit
disabled); '$port:<server>' names the run's fake SMTP servers. The effect
lists the created message (or the reused one), the transactions in creation
order, the configuration row after the case, audit entries, the Celery
messages and the SMTP transcripts per connection (EHLO name and boundary
normalized).
Run with ``python3 -I`` after the message-mutations fixture.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('communications_send_email_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
PATH = '/communications/send/email'
OWN_CONFIGURATION = 'a1000000-0000-4000-8000-000000981001'
FOREIGN_CONFIGURATION = 'a1000000-0000-4000-8000-000000981002'
SMTP_USER = 'mittente@example.test'
SMTP_PASSWORD = 'placeholder-smtp-secret'
MESSAGES = {
    'stored': {'id': 'a1000000-0000-4000-8000-000000982001', 'association': OWN, 'subject': 'Comunicazione salvata',
               'message': 'Testo salvato\nseconda riga'},
    'stored_without_subject': {'id': 'a1000000-0000-4000-8000-000000982002', 'association': OWN, 'subject': None,
                               'message': 'Senza oggetto'},
    'foreign': {'id': 'a1000000-0000-4000-8000-000000982003', 'association': FOREIGN, 'subject': 'Estera', 'message': 'Altro'},
}
CONFIGURATIONS = {
    # The foreign association's configuration is not configured for SMTP.
    'foreign': {'id': FOREIGN_CONFIGURATION, 'association': FOREIGN, 'email_smtp_host': None, 'email_smtp_port': 587,
                'email_smtp_user': None, 'email_smtp_password': None, 'email_sender_name': None, 'email_encryption': 'TLS',
                'daily_email_limit': 1000, 'daily_email_balance': 4},
}
cases = []


def add(name, actor, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        setup=None):
    case = {'name': f'communications-send-email-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'setup': setup}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
OK = {'msg': 'Ok', 'result': True}
VALID = {'email': 'socio@example.test', 'subject': 'Avviso', 'message': 'Ciao a tutti'}


def configuration(**values):
    row = {'email_smtp_host': '127.0.0.1', 'email_smtp_port': None, 'email_smtp_user': SMTP_USER, 'email_smtp_password': SMTP_PASSWORD,
           'email_sender_name': 'Associazione Prova', 'email_encryption': 'NONE', 'daily_email_limit': 1000, 'daily_email_balance': 0}
    row.update(values)
    return row


def server(name, **values):
    return configuration(email_smtp_port=f'$port:{name}', **values)


def after(row, balance=None):
    """The own configuration row after the case (ports stay tokens)."""
    row = dict(row)
    if balance is not None:
        row['daily_email_balance'] = balance
    return row


def mail_kwargs(subject, message, recipients, association='own'):
    return [['subject', subject], ['message', message], ['from_email', f'$from:{association}'], ['recipient_list', recipients],
            ['fail_silently', False], ['sport_association_id', {'uuid': OWN if association == 'own' else FOREIGN}], ['reply_to', None],
            ['skip_association_check', True]]


def published(subject, message, recipients, association='own'):
    return [{'task': 'core.tasks.send_mail_async', 'args': [], 'kwargs': mail_kwargs(subject, message, recipients, association)}]


def effect(actor, *, message=None, stored=None, recipients=(), configuration=None, balance_audit=None, celery=(), smtp=None,
           session=None, association=OWN):
    """message: (subject, text) of the created Message; stored: the reused message label."""
    return {'actor': actor, 'session': session, 'association': association, 'message': list(message) if message else None,
            'stored': stored, 'recipients': list(recipients), 'configuration': configuration, 'balance_audit': balance_audit,
            'celery': list(celery), 'smtp': smtp}


def auth_plain(password=SMTP_PASSWORD):
    return 'AUTH PLAIN ' + base64.b64encode(f'\0{SMTP_USER}\0{password}'.encode()).decode() + '\r\n'


def handshake(mode, password=SMTP_PASSWORD, *, login=True):
    units = ['ehlo <local>\r\n']
    if mode == 'starttls':
        units += ['STARTTLS\r\n', '<tls>', 'ehlo <local>\r\n']
    if login:
        units.append(auth_plain(password))
    return units


def header_value(value):
    """compat32 with an unlimited line: ASCII as is, else one UTF-8 word (base64 only when shorter)."""
    if value.isascii():
        return value
    raw = value.encode()
    qp = ''.join('_' if c == 0x20 else chr(c) if chr(c).isascii() and (chr(c).isalnum() or chr(c) in '-!*+/') else f'={c:02X}'
                 for c in raw)
    b64 = base64.b64encode(raw).decode()
    return f'=?utf-8?b?{b64}?=' if len(b64) < len(qp) else f'=?utf-8?q?{qp}?='


def mime(sender, recipients, subject, text, subject_header=None):
    """msg.as_string() of send_email's MIMEMultipart, CRLF line ends, boundary normalized.

    subject_header: the folded Subject value of a subject with line breaks (one line per source line).
    """
    from_header = f'{sender} <{SMTP_USER}>' if sender else SMTP_USER
    body = base64.encodebytes(text.encode()).decode()
    message = ('Content-Type: multipart/mixed; boundary="<boundary>"\nMIME-Version: 1.0\n'
               f'From: {header_value(from_header)}\nTo: {header_value(", ".join(recipients))}\n'
               f'Subject: {subject_header or header_value(subject)}\n\n'
               '--<boundary>\nContent-Type: text/plain; charset="utf-8"\nMIME-Version: 1.0\nContent-Transfer-Encoding: base64\n\n'
               f'{body}\n--<boundary>--\n')
    return message.replace('\n', '\r\n')


def delivery(mode, sender, recipients, subject, text, *, refused=(), subject_header=None, accepted=None):
    """accepted: how many RCPT commands are sent before one fails to encode (the connection is then dropped)."""
    units = handshake(mode) + [f'mail from:<{SMTP_USER}> size=<size>\r\n']
    units += [f'rcpt to:<{r}>\r\n' for r in recipients[:accepted]]
    if accepted is not None:
        return units + ['<closed>']
    if len(refused) == len(recipients):
        return units + ['rset\r\n', '<closed>']
    return units + ['data\r\n', mime(sender, recipients, subject, text, subject_header) + '.\r\n', 'quit\r\n', '<quit>']


def verified(mode, password=SMTP_PASSWORD):
    """verify_smtp(send_email=False): connect, (STARTTLS,) login, closed without QUIT."""
    return handshake(mode, password) + ['<closed>']


# Identity and dispatch.
add('anonymous', 'anonymous', 401, ANONYMOUS, body=VALID)
add('admin-without-session', 'admin', 403, ADMIN, body=VALID)
add('restricted-unmapped', 'restricted_collaborator', 403, {
    'detail': "Access denied: No permission mapping for endpoint 'communications/send/email'. Contact administrator to grant access."},
    body=VALID)
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body=VALID,
    headers=SCOPED)
add('athlete-not-association', 'athlete', 403, {'detail': 'User is not a sport association'}, body=VALID)
add('owner-without-association-server-error', 'owner_without_association', 500, SERVER, body=VALID)
add('deleted-association-server-error', 'deleted_association_owner', 500, SERVER, body=VALID)
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Configuration Send Email', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

# Validation: nothing is written.
add('list-body-server-error', 'owner', 500, SERVER, body=[VALID])
add('string-body-server-error', 'owner', 500, SERVER, body='message_id')
add('empty-object-email-required', 'owner', 400, ['Email is required'], body={})
add('empty-email-required', 'owner', 400, ['Email is required'], body={**VALID, 'email': ''})
add('missing-subject-required', 'owner', 400, ['Subject is required'], body={'email': 'socio@example.test', 'message': 'x'})
add('zero-subject-required', 'owner', 400, ['Subject is required'], body={**VALID, 'subject': 0})
add('null-message-required', 'owner', 400, ['Message is required'], body={**VALID, 'message': None})
add('integer-email-server-error', 'owner', 500, SERVER, body={**VALID, 'email': 5})
add('list-email-server-error', 'owner', 500, SERVER, body={**VALID, 'email': ['socio@example.test']})
add('invalid-email', 'owner', 400, ['Email is not valid.'], body={**VALID, 'email': 'non-una-email'})
add('trailing-comma-email', 'owner', 400, ['Email is not valid.'], body={**VALID, 'email': 'socio@example.test,'})
add('spaced-list-email', 'owner', 400, ['Email is not valid.'], body={**VALID, 'email': 'socio@example.test, altro@example.test'})
add('padded-email', 'owner', 400, ['Email is not valid.'], body={**VALID, 'email': ' socio@example.test'})
add('boolean-subject', 'owner', 400, {'subject': ['Not a valid string.']}, body={**VALID, 'subject': True})
add('long-subject-blank-message', 'owner', 400, {'subject': ['Ensure this field has no more than 100 characters.'],
                                                 'message': ['This field may not be blank.']},
    body={**VALID, 'subject': 's' * 101, 'message': '   '})
add('dict-message', 'owner', 400, {'message': ['Not a valid string.']}, body={**VALID, 'message': {'testo': 1}})
add('null-character-message', 'owner', 400, {'message': ['Null characters are not allowed.']}, body={**VALID, 'message': 'a\0b'})
add('long-message', 'owner', 400, {'message': ['Ensure this field has no more than 10000 characters.']},
    body={**VALID, 'message': 'm' * 10001})
add('form-invalid-email', 'owner', 400, ['Email is not valid.'], raw_body='email=x&subject=s&message=m',
    content_type='application/x-www-form-urlencoded')

# message_id lookups.
add('message-id-malformed-server-error', 'owner', 500, SERVER, body={**VALID, 'message_id': 'non-un-uuid'})
add('message-id-float-server-error', 'owner', 500, SERVER, body={**VALID, 'message_id': 1.5})
add('message-id-unknown', 'owner', 404, {'msg': 'message not found.'}, body={**VALID, 'message_id': IDS['missing']})
add('message-id-integer-unknown', 'owner', 404, {'msg': 'message not found.'}, body={**VALID, 'message_id': 5})
add('message-id-foreign', 'owner', 404, {'msg': 'message not found.'}, body={**VALID, 'message_id': MESSAGES['foreign']['id']})
add('message-id-form-immutable-server-error', 'owner', 500, SERVER,
    raw_body=f'email=socio%40example.test&message_id={MESSAGES["stored"]["id"]}', content_type='application/x-www-form-urlencoded')
add('message-id-stored-without-subject', 'owner', 400, ['Subject is required'],
    body={'email': 'socio@example.test', 'message_id': MESSAGES['stored_without_subject']['id']})

# Effects. Without a configuration the writes stay and the request fails.
add('no-configuration-server-error', 'owner', 500, SERVER, body=VALID, setup={'configuration': None},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test']))

UNCONFIGURED = configuration(email_smtp_host=None, email_smtp_port=587, email_encryption='TLS', daily_email_balance=7)
add('unconfigured-publishes', 'owner', 200, OK, body=VALID, setup={'configuration': UNCONFIGURED},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(UNCONFIGURED),
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test'])))
add('unconfigured-two-recipients', 'owner', 200, OK, body={**VALID, 'email': 'uno@example.test,due@example.test'},
    setup={'configuration': UNCONFIGURED},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['uno@example.test', 'due@example.test'],
                  configuration=after(UNCONFIGURED), celery=published('Avviso', 'Ciao a tutti', ['uno@example.test', 'due@example.test'])))
add('unconfigured-stored-message', 'owner', 200, OK,
    body={'email': 'socio@example.test', 'message_id': MESSAGES['stored']['id'], 'subject': 'ignorato'},
    setup={'configuration': UNCONFIGURED},
    effect=effect('owner', stored='stored', recipients=['socio@example.test'], configuration=after(UNCONFIGURED),
                  celery=published('Comunicazione salvata', 'Testo salvato\nseconda riga', ['socio@example.test'])))
add('unconfigured-integer-subject-padded-message', 'owner', 200, OK, body={**VALID, 'subject': 5, 'message': '  Testo  '},
    setup={'configuration': UNCONFIGURED},
    effect=effect('owner', message=('5', 'Testo'), recipients=['socio@example.test'], configuration=after(UNCONFIGURED),
                  celery=published('5', 'Testo', ['socio@example.test'])))
add('unconfigured-form-body', 'owner', 200, OK, raw_body='email=socio%40example.test&subject=Primo&subject=Modulo&message=Dal+modulo',
    content_type='application/x-www-form-urlencoded', setup={'configuration': UNCONFIGURED},
    effect=effect('owner', message=('Modulo', 'Dal modulo'), recipients=['socio@example.test'], configuration=after(UNCONFIGURED),
                  celery=published('Modulo', 'Dal modulo', ['socio@example.test'])))
add('unconfigured-ip-literal-recipient', 'full_collaborator', 200, OK, body={**VALID, 'email': 'socio@[127.0.0.1]'},
    setup={'configuration': UNCONFIGURED},
    effect=effect('full_collaborator', message=('Avviso', 'Ciao a tutti'), recipients=['socio@[127.0.0.1]'],
                  configuration=after(UNCONFIGURED), celery=published('Avviso', 'Ciao a tutti', ['socio@[127.0.0.1]'])))
add('foreign-owner-own-scope', 'foreign_owner', 200, OK, body=VALID,
    effect=effect('foreign_owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], association=FOREIGN,
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test'], 'foreign')))
LIMITED = server('plain', daily_email_limit=3, daily_email_balance=3)
add('limit-reached', 'owner', 200, {'msg': 'Daily email limit reached', 'result': False}, body=VALID, setup={'configuration': LIMITED},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(LIMITED)))
LONG = 'a' * 64 + '@' + ('b' * 63 + '.') * 3 + 'it'
add('long-recipient-server-error', 'owner', 500, SERVER, body={**VALID, 'email': LONG}, setup={'configuration': UNCONFIGURED},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), configuration=after(UNCONFIGURED)))

# SMTP deliveries: verify_smtp on one connection, the message on a second.
STARTTLS = server('starttls', email_encryption='TLS', daily_email_balance=2)
add('starttls-delivery', 'owner', 200, OK, body=VALID, setup={'configuration': STARTTLS},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(STARTTLS, 3),
                  balance_audit=['2', '3'], smtp={'server': 'starttls', 'connections': [
                      verified('starttls'), delivery('starttls', 'Associazione Prova', ['socio@example.test'], 'Avviso', 'Ciao a tutti')]}))
SSL = server('ssl', email_encryption='SSL', email_sender_name=None)
LETTER = 'Gentili soci,\nla riunione è confermata.\n' + 'Riga lunga ' * 12
add('ssl-delivery-two-recipients-unicode', 'owner', 200, OK,
    body={'email': 'uno@example.test,due@example.test', 'subject': 'Novità per i soci', 'message': LETTER},
    setup={'configuration': SSL},
    effect=effect('owner', message=('Novità per i soci', LETTER.strip()), recipients=['uno@example.test', 'due@example.test'],
                  configuration=after(SSL, 1), balance_audit=['0', '1'], smtp={'server': 'ssl', 'connections': [
                      verified('ssl'), delivery('ssl', None, ['uno@example.test', 'due@example.test'], 'Novità per i soci',
                                                LETTER.strip())]}))
PLAIN = server('plain', daily_email_balance=10)
add('plain-delivery-full-collaborator', 'full_collaborator', 200, OK, body=VALID, setup={'configuration': PLAIN},
    effect=effect('full_collaborator', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'],
                  configuration=after(PLAIN, 11), balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['socio@example.test'], 'Avviso', 'Ciao a tutti')]}))
add('plain-delivery-admin-impersonating', 'admin', 200, OK, body={**VALID, 'message_id': MESSAGES['stored']['id']},
    headers=ADMIN_OWNER, setup={'configuration': PLAIN},
    effect=effect('admin', session='admin_owner', stored='stored', recipients=['socio@example.test'], configuration=after(PLAIN, 11),
                  balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['socio@example.test'], 'Comunicazione salvata',
                                                 'Testo salvato\nseconda riga')]}))
add('plain-partially-refused', 'owner', 200, OK, body={**VALID, 'email': 'socio@example.test,rifiutato@example.test'},
    setup={'configuration': PLAIN},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test', 'rifiutato@example.test'],
                  configuration=after(PLAIN, 11), balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['socio@example.test', 'rifiutato@example.test'],
                                                 'Avviso', 'Ciao a tutti', refused=['rifiutato@example.test'])]}))
add('plain-all-refused-server-error', 'owner', 500, SERVER, body={**VALID, 'email': 'rifiutato@example.test'},
    setup={'configuration': PLAIN},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['rifiutato@example.test'], configuration=after(PLAIN),
                  smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['rifiutato@example.test'], 'Avviso', 'Ciao a tutti',
                                                 refused=['rifiutato@example.test'])]}))

# Subjects with a line break: each continuation line must start with a space or a tab, otherwise as_string() raises
# inside send_email (after the verify connection) and the view cannot render the result.
FOLDED = 'Avviso\n seconda riga'
add('subject-continuation-line-delivered', 'owner', 200, OK, body={**VALID, 'subject': FOLDED}, setup={'configuration': PLAIN},
    effect=effect('owner', message=(FOLDED, 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(PLAIN, 11),
                  balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['socio@example.test'], FOLDED, 'Ciao a tutti',
                                                 subject_header=FOLDED)]}))
BROKEN = 'Avviso\nseconda riga'
add('subject-line-break-server-error', 'owner', 500, SERVER, body={**VALID, 'subject': BROKEN}, setup={'configuration': PLAIN},
    effect=effect('owner', message=(BROKEN, 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(PLAIN),
                  smtp={'server': 'plain', 'connections': [verified('plain')]}))
ACCENTED = 'Novità\r\nper i soci \x0b fine'
add('subject-unicode-lines-delivered', 'owner', 200, OK, body={**VALID, 'subject': ACCENTED}, setup={'configuration': PLAIN},
    effect=effect('owner', message=(ACCENTED, 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(PLAIN, 11),
                  balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['socio@example.test'], ACCENTED, 'Ciao a tutti',
                                                 subject_header='=?utf-8?q?Novit=C3=A0?=\n =?utf-8?q?_per_i_soci_?=\n'
                                                                ' =?utf-8?q?_fine?=')]}))

# Recipients smtplib.quoteaddr reads through email.utils.parseaddr: a quoted local part is kept, an address literal is
# passed as written, and a recipient that is not ASCII fails to encode after the commands before it.
QUOTED = '"socio..uno"@example.test'
add('quoted-local-part-delivered', 'owner', 200, OK, body={**VALID, 'email': QUOTED}, setup={'configuration': PLAIN},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=[QUOTED], configuration=after(PLAIN, 11),
                  balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', [QUOTED], 'Avviso', 'Ciao a tutti')]}))
LITERAL = 'socio@[127.0.0.1]'
add('address-literal-delivered', 'owner', 200, OK, body={**VALID, 'email': f'{LITERAL},due@example.test'},
    setup={'configuration': PLAIN},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=[LITERAL, 'due@example.test'],
                  configuration=after(PLAIN, 11), balance_audit=['10', '11'], smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', [LITERAL, 'due@example.test'], 'Avviso',
                                                 'Ciao a tutti')]}))
IDN = 'socio@citta\u0300-2.example'
add('non-ascii-recipient-server-error', 'owner', 500, SERVER, body={**VALID, 'email': f'uno@example.test,{IDN},due@example.test'},
    setup={'configuration': PLAIN},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['uno@example.test', IDN, 'due@example.test'],
                  configuration=after(PLAIN), smtp={'server': 'plain', 'connections': [
                      verified('plain'), delivery('plain', 'Associazione Prova', ['uno@example.test', IDN, 'due@example.test'],
                                                 'Avviso', 'Ciao a tutti', accepted=1)]}))

# verify_smtp failures fall back to send_mail_async.
WRONG = server('starttls', email_encryption='TLS', email_smtp_password='placeholder-wrong')
add('wrong-password-publishes', 'owner', 200, OK, body=VALID, setup={'configuration': WRONG},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(WRONG),
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test']),
                  smtp={'server': 'starttls', 'connections': [
                      handshake('starttls', 'placeholder-wrong')
                      + ['AUTH LOGIN ' + base64.b64encode(SMTP_USER.encode()).decode() + '\r\n', '<closed>']]}))
NOAUTH = server('noauth')
add('server-without-auth-publishes', 'owner', 200, OK, body=VALID, setup={'configuration': NOAUTH},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(NOAUTH),
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test']),
                  smtp={'server': 'noauth', 'connections': [handshake('plain', login=False) + ['<closed>']]}))
NO_STARTTLS = server('noauth', email_encryption='TLS')
add('starttls-unsupported-publishes', 'owner', 200, OK, body=VALID, setup={'configuration': NO_STARTTLS},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(NO_STARTTLS),
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test']),
                  smtp={'server': 'noauth', 'connections': [handshake('plain', login=False) + ['<closed>']]}))
CLOSED = server('closed')
add('connection-refused-publishes', 'owner', 200, OK, body=VALID, setup={'configuration': CLOSED},
    effect=effect('owner', message=('Avviso', 'Ciao a tutti'), recipients=['socio@example.test'], configuration=after(CLOSED),
                  celery=published('Avviso', 'Ciao a tutti', ['socio@example.test'])))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'communications/send/email', 'ids': IDS, 'own_configuration': OWN_CONFIGURATION,
                              'smtp_password': SMTP_PASSWORD, 'messages': MESSAGES, 'configurations': CONFIGURATIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
