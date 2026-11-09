"""Capture what CommunicationConfiguration's SMTP path writes, for BE_GO/internal/smtpsend.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/smtpsend_golden.py``
Django is set up with ``core.settings`` read from ``selfhost/.env.example`` (no
database is opened). Each SMTP case runs a scripted local SMTP server (plain,
STARTTLS or implicit TLS with a certificate generated for this run) and calls the
actual ``CommunicationConfiguration.verify_smtp(send_email=False)`` or
``send_email(...)`` on an unsaved instance whose ``save`` only counts calls. The
server records every client byte per connection (plaintext after TLS), split into
the units it answered. The MIME cases call ``send_email`` with ``smtplib`` and
``verify_smtp`` replaced by recorders, so the captured text is the method's own
``msg.as_string()``. The address cases record ``smtplib.quoteaddr``. Everything is
written to ``BE_GO/internal/smtpsend/testdata/python_smtp.json``; ``go test
./internal/smtpsend`` replays the same server scripts against the Go client and
requires identical bytes.
"""
import base64
import datetime
import gc
import json
import os
from pathlib import Path
import random
import socket
import ssl
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/smtpsend/testdata/python_smtp.json'

EHLO_FULL = '250-fake.smtp.test greets you\r\n250-SIZE 35882577\r\n250-8BITMIME\r\n250-AUTH PLAIN LOGIN CRAM-MD5\r\n250-STARTTLS\r\n250 SMTPUTF8'


def ehlo(*features):
    lines = ['fake.smtp.test greets you', *features]
    return '\r\n'.join(('250-' if i < len(lines) - 1 else '250 ') + line for i, line in enumerate(lines))


def r(text, **flags):
    return {'reply': text, **flags}


GREET = r('220 fake.smtp.test ESMTP ready')
OK_AUTH = r('235 2.7.0 Authentication successful')
QUIT = r('221 2.0.0 Bye')


def delivery(recipients=1):
    return [r('250 2.1.0 Ok'), *[r('250 2.1.5 Ok') for _ in range(recipients)], r('354 End data with <CR><LF>.<CR><LF>'),
            r('250 2.0.0 Ok: queued'), QUIT]


CHALLENGE = base64.b64encode(b'<1896.697170952@postoffice.example.net>').decode()


def plain_login(features):
    return [GREET, r(features), OK_AUTH]


def message_args(**overrides):
    args = dict(subject='Bakney Sport | Scadenza certificato medico', body=None,
                html_body='<html><body><p>Ciao Mario,</p>\n<p>il certificato scade il 05/03/2026.</p></body></html>\n',
                recipient_list=['athlete@example.test'], reply_to=['owner@example.test'])
    args.update(overrides)
    return args


def config(**overrides):
    values = dict(email_smtp_host='127.0.0.1', email_smtp_user='sender@example.test', email_smtp_password='p4ss-word',
                  email_sender_name='ASD Esempio', email_encryption='NONE', daily_email_limit=1000, daily_email_balance=0)
    values.update(overrides)
    return values


LARGE = ''.join(f'<p>Riga {i}: {"àèìòù " * 7}</p>\n' for i in range(120))


def cases():
    plain = ehlo('SIZE 1000000', 'AUTH PLAIN')
    yield 'plain size plain-auth send', config(), 'send', message_args(), [
        plain_login(plain), [*plain_login(plain), *delivery()]]
    tls1 = ehlo('STARTTLS', '8BITMIME')
    tls2 = ehlo('AUTH LOGIN', 'PIPELINING')
    login = [r('334 VXNlcm5hbWU6'), r('334 UGFzc3dvcmQ6'), OK_AUTH]
    yield 'starttls login send', config(email_encryption='TLS'), 'send', message_args(), [
        [GREET, r(tls1), r('220 2.0.0 Ready to start TLS', starttls=True), r(tls2), *login],
        [GREET, r(tls1), r('220 2.0.0 Ready to start TLS', starttls=True), r(tls2), *login, *delivery()]]
    ssl_features = ehlo('AUTH CRAM-MD5 PLAIN', 'SIZE')
    cram = [r(f'334 {CHALLENGE}'), OK_AUTH]
    yield 'implicit tls cram-md5 send', config(email_encryption='SSL'), 'send', message_args(), [
        [GREET, r(ssl_features), *cram], [GREET, r(ssl_features), *cram, *delivery()]]
    yield 'auth fallback cram then plain then login', config(), 'verify', None, [
        [GREET, r(ehlo('AUTH LOGIN PLAIN CRAM-MD5')), r(f'334 {CHALLENGE}'), r('535 5.7.8 bad'),
         r('535 5.7.8 bad'), r('334 VXNlcm5hbWU6'), r('334 UGFzc3dvcmQ6'), OK_AUTH]]
    yield 'every mechanism refused', config(), 'verify', None, [
        [GREET, r(ehlo('AUTH PLAIN LOGIN')), r('535 5.7.8 bad'), r('334 VXNlcm5hbWU6'), r('535 5.7.8 bad')]]
    yield 'plain 503 already authenticated', config(), 'verify', None, [
        [GREET, r(ehlo('AUTH PLAIN')), r('503 5.5.1 Already authenticated')]]
    yield 'no auth advertised', config(), 'verify', None, [[GREET, r(ehlo('SIZE 10'))]]
    yield 'no suitable mechanism', config(), 'verify', None, [[GREET, r(ehlo('AUTH XOAUTH2 plain'))]]
    yield 'helo fallback', config(), 'verify', None, [[GREET, r('502 5.5.2 Error: command not recognized'), r('250 fake.smtp.test')]]
    yield 'helo refused', config(), 'verify', None, [[GREET, r('502 no'), r('501 no')]]
    yield 'ehlo 2xx other than 250', config(), 'verify', None, [[GREET, r('251 odd but fine')]]
    yield 'greeting refused', config(), 'verify', None, [[r('554 5.3.2 not now')]]
    yield 'old style auth and multiline', config(), 'verify', None, [
        [GREET, r('250-fake.smtp.test\r\n250-AUTH=LOGIN\r\n250-auth=CRAM-MD5\r\n250 SIZE 99'), r(f'334 {CHALLENGE}'), OK_AUTH]]
    yield 'reply code with spaces', config(), 'verify', None, [[GREET, r(ehlo('AUTH PLAIN')), r('235\r')]]
    yield 'non-ascii password plain', config(email_smtp_password='pässword'), 'verify', None, [[GREET, r(ehlo('AUTH PLAIN'))]]
    yield 'non-ascii password login', config(email_smtp_password='pässword'), 'verify', None, [
        [GREET, r(ehlo('AUTH LOGIN')), r('334 VXNlcm5hbWU6')]]
    yield 'non-ascii password cram', config(email_smtp_password='pässword'), 'verify', None, [
        [GREET, r(ehlo('AUTH CRAM-MD5')), r(f'334 {CHALLENGE}')]]
    yield 'non-ascii user plain', config(email_smtp_user='sénder@example.test'), 'verify', None, [[GREET, r(ehlo('AUTH PLAIN'))]]
    yield 'challenge loop', config(), 'verify', None, [
        [GREET, r(ehlo('AUTH LOGIN')), *[r('334 VXNlcm5hbWU6') for _ in range(6)]]]
    yield 'unpadded challenge', config(), 'verify', None, [
        [GREET, r(ehlo('AUTH CRAM-MD5')), r('334 ' + CHALLENGE.rstrip('=')[:-1] + ' x'), OK_AUTH]]
    yield 'starttls not advertised', config(email_encryption='TLS'), 'verify', None, [[GREET, r(ehlo('AUTH PLAIN'))]]
    yield 'starttls refused', config(email_encryption='TLS'), 'verify', None, [[GREET, r(ehlo('STARTTLS')), r('454 4.7.0 TLS not available')]]
    yield 'unknown encryption is plain', config(email_encryption='OTHER'), 'verify', None, [plain_login(plain)]
    yield 'login without at sign as sender', config(email_smtp_user='apikey', email_sender_name='ASD Esempio'), 'send', message_args(), [
        plain_login(plain), [*plain_login(plain), *delivery()]]
    yield 'text body lines with dots, several recipients, utf-8 subject', config(email_sender_name='Associazione Città'), 'send', message_args(
        subject='Promemoria · événement «Été»', html_body=None, body='.prima riga\r\n..seconda\n.\nfine\n',
        recipient_list=['a@example.test', 'b@example.test', 'c@example.test'], reply_to=['x@example.test', 'y@example.test']), [
        plain_login(plain), [*plain_login(plain), *delivery(3)]]
    yield 'one recipient refused', config(email_sender_name=None), 'send', message_args(
        recipient_list=['a@example.test', 'b@example.test'], reply_to=None), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('550 5.1.1 unknown'), r('250 Ok'), r('354 go'), r('250 queued'), QUIT]]
    yield 'all recipients refused', config(), 'send', message_args(recipient_list=['a@example.test', 'b@example.test']), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('550 no'), r('553 no'), r('250 reset')]]
    yield 'recipient 421 closes', config(), 'send', message_args(recipient_list=['a@example.test', 'b@example.test']), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('421 going away')]]
    yield 'sender refused', config(), 'send', message_args(), [plain_login(plain), [*plain_login(plain), r('553 sender'), r('250 reset')]]
    yield 'sender 421 closes', config(), 'send', message_args(), [plain_login(plain), [*plain_login(plain), r('421 bye')]]
    yield 'data command refused', config(), 'send', message_args(), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('250 Ok'), r('451 later')]]
    yield 'data body refused', config(), 'send', message_args(), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('250 Ok'), r('354 go'), r('554 rejected'), r('250 reset')]]
    yield 'quit unanswered', config(), 'send', message_args(), [
        plain_login(plain), [*plain_login(plain), r('250 Ok'), r('250 Ok'), r('354 go'), r('250 queued')]]
    yield 'large body with size', config(), 'send', message_args(html_body=LARGE), [plain_login(plain), [*plain_login(plain), *delivery()]]
    yield 'non-ascii recipient', config(), 'send', message_args(recipient_list=['àtleta@example.test']), [
        plain_login(plain), [*plain_login(plain), r('250 Ok')]]
    yield 'second connection refused', config(), 'send', message_args(), [plain_login(plain), [r('421 busy')]]


class ScriptedServer:
    """Accepts one connection per script entry and answers each received unit."""
    def __init__(self, scripts, context):
        self.scripts, self.context = scripts, context
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.transcripts = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        for script in self.scripts:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            connection.settimeout(5)
            units = []
            self.transcripts.append(units)
            try:
                self.converse(connection, script, units)
            except (OSError, ssl.SSLError) as error:
                units.append({'event': 'error', 'detail': type(error).__name__})
            finally:
                connection.close()

    def converse(self, connection, script, units):
        stream = connection
        if script and script[0].get('implicit_tls'):
            stream = self.context.wrap_socket(connection, server_side=True)
        buffer = b''
        data_mode = False
        replies = list(script)
        # The greeting is sent before reading anything.
        greeting = replies.pop(0)
        stream.sendall(greeting['reply'].encode('latin-1') + b'\r\n')
        while True:
            terminator = b'\r\n.\r\n' if data_mode else b'\n'
            while terminator not in buffer:
                chunk = stream.recv(65536)
                if not chunk:
                    if buffer:
                        units.append({'bytes': buffer.decode('latin-1')})
                    units.append({'event': 'eof'})
                    return
                buffer += chunk
            end = buffer.index(terminator) + len(terminator)
            unit, buffer = buffer[:end], buffer[end:]
            units.append({'bytes': unit.decode('latin-1')})
            if not replies:
                units.append({'event': 'closed by server'})
                return
            reply = replies.pop(0)
            stream.sendall(reply['reply'].encode('latin-1') + b'\r\n')
            data_mode = reply['reply'].startswith('354')
            if reply.get('starttls'):
                stream = self.context.wrap_socket(stream, server_side=True)
                buffer = b''

    def close(self):
        self.listener.close()
        self.thread.join(10)


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fake.smtp.test')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    cert_path, key_path = Path(directory) / 'cert.pem', Path(directory) / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def setup():
    import environ
    os.environ.clear()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='golden-only',
                      JWT_PUBLIC_KEY='golden-only', DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1', DBPORT='1',
                      WHITELABEL_NAME='Bakney Sport', IS_WHITELABEL='False')
    sys.path.insert(0, str(ROOT / 'BE'))
    import django
    django.setup()


def instance(values, port, saves):
    from communications.models import CommunicationConfiguration
    item = CommunicationConfiguration(email_smtp_port=port, **values)
    item.save = lambda *a, **k: saves.append(item.daily_email_balance)
    return item


def smtp_cases(context, local_hostname):
    import io
    from contextlib import redirect_stdout
    out = []
    for name, values, call, args, scripts in cases():
        if values['email_encryption'] == 'SSL':
            for script in scripts:
                script[0] = dict(script[0], implicit_tls=True)
        server = ScriptedServer(scripts, context)
        started = time.monotonic()
        saves = []
        item = instance(values, server.port, saves)
        with redirect_stdout(io.StringIO()):
            if call == 'verify':
                result = item.verify_smtp(send_email=False)
            else:
                result = item.send_email(**args)
        # Production callers drop the returned tuple at once, which releases
        # the SMTP object and closes its socket; do the same before waiting.
        ok = result[0] is True
        error = None if ok else (result[1] if isinstance(result[1], str) else type(result[1]).__name__)
        del result
        gc.collect()
        server.close()
        print(f'{time.monotonic() - started:6.2f}s {name}', file=sys.stderr)
        assert len(server.transcripts) == len(scripts), (name, server.transcripts)
        boundary = None
        for units in server.transcripts:
            for unit in units:
                text = unit.get('bytes', '')
                if 'boundary="' in text:
                    boundary = text.split('boundary="', 1)[1].split('"', 1)[0]
        out.append({'name': name, 'config': values, 'call': call, 'args': args, 'scripts': scripts,
                    'transcripts': server.transcripts, 'ok': ok, 'error': error,
                    'saves': saves, 'boundary': boundary})
        for units in server.transcripts:
            for unit in units:
                if unit.get('bytes', '').startswith('ehlo ') or unit.get('bytes', '').startswith('helo '):
                    assert unit['bytes'][5:-2] == local_hostname, unit
    return out


FUZZ_ASCII = list('abcXYZ019 \t.,;:<>()[]"\'\\@=?_-/+!*#~%&{}|^`$')
FUZZ_OTHER = ['à', 'é', 'Ç', 'ß', 'ø', '€', '中', '文', '😀', '\x00', '\x01', '\x1b', '\x7f', '\xa0', '\u200b', '\x1f']
FUZZ_BREAKS = ['\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029']
FUZZ_ALPHABET = FUZZ_ASCII + FUZZ_OTHER + FUZZ_BREAKS


def fuzz_text(rng, maximum):
    # Line-breaking characters (outside the Go subset) in about one value in ten.
    population = FUZZ_ALPHABET if rng.random() < .1 else FUZZ_ASCII + FUZZ_OTHER
    weights = ([12] * len(FUZZ_ASCII) + [3] * len(FUZZ_OTHER) + [1] * len(FUZZ_BREAKS))[:len(population)]
    return ''.join(rng.choices(population, weights=weights, k=rng.randint(0, maximum)))


def mime_cases():
    """send_email's own as_string(), captured by replacing the transport."""
    import communications.models as models
    captured = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def sendmail(self, sender, recipients, message):
            captured.append(message)

        def quit(self):
            pass

    rng = random.Random(20261109)
    samples = [
        dict(name='ASD Esempio', user='sender@example.test', subject='Bakney Sport | Nuovo certificato medico',
             to=['a@example.test'], reply=['o@example.test'], html='<p>x</p>', text=None),
        dict(name='', user='sender@example.test', subject='', to=['a@example.test'], reply=None, html=None, text=''),
        dict(name=None, user='u@x', subject='a' * 300, to=['a@example.test'] * 5, reply=[], html=None, text='testo'),
        dict(name='Città', user='u@x', subject='Promemoria evento «Pallavolo»', to=['à@x.it'], reply=['r@x'],
             html='<p>€ 10,00</p>\r\n', text=None),
        dict(name='  spaced\tname ', user='u@x', subject=' leading and trailing ', to=['a@x'], reply=['a@x', 'b@x'],
             html=None, text='.line\n..two\r\n'),
        dict(name='Emoji 😀 club', user='u@x', subject='中文' * 40, to=['a@x'], reply=None, html='x' * 1500, text=None),
        dict(name='x', user='u@x', subject='=?utf-8?q?not_encoded?=', to=['a@x'], reply=None, html=None, text='\x00\x7f'),
        # Equal base64 and quoted-printable lengths (16): SHORTEST keeps 'q'.
        dict(name='éabcdefghi', user='u@x', subject='éabcdefghij', to=['a@x'], reply=None, html='<p>', text=None),
    ]
    for _ in range(250):
        samples.append(dict(name=fuzz_text(rng, 30) if rng.random() < .8 else None, user='u@x', subject=fuzz_text(rng, 90),
                            to=[fuzz_text(rng, 20) for _ in range(rng.randint(1, 3))],
                            reply=[fuzz_text(rng, 20) for _ in range(rng.randint(0, 2))] if rng.random() < .7 else None,
                            html=fuzz_text(rng, 200) if rng.random() < .6 else None, text=fuzz_text(rng, 200)))
    out = []
    saved = models.smtplib.SMTP, models.smtplib.SMTP_SSL, models.CommunicationConfiguration.verify_smtp
    models.smtplib.SMTP = models.smtplib.SMTP_SSL = Recorder
    models.CommunicationConfiguration.verify_smtp = lambda self, send_email=True: (True, 'Ok')
    try:
        for sample in samples:
            captured.clear()
            saves = []
            item = instance(config(email_sender_name=sample['name'], email_smtp_user=sample['user']), 25, saves)
            result = item.send_email(subject=sample['subject'], body=sample['text'], html_body=sample['html'],
                                     recipient_list=sample['to'], reply_to=sample['reply'])
            out.append({'input': sample, 'ok': result[0] is True, 'message': captured[0] if captured else None})
    finally:
        models.smtplib.SMTP, models.smtplib.SMTP_SSL, models.CommunicationConfiguration.verify_smtp = saved
    return out


def address_cases():
    import smtplib
    rng = random.Random(7)
    alphabet = list("abcXYZ0189.!#$%&'*+/=?^_`{|}~-") + ['à', 'é', 'ß', 'ø', 'Ç', 'ñ', 'ü']
    others = list(' \t"(),:;<>@[\\]') + ['中', '😀', '\x00']
    samples = ['a@b.c', 'à@x.it', "o'n@x.it", '.a..b.@.c.', 'x', '', 'a@b@c', '<a@b>', 'a b@c', '"q"@x', 'a@[1.2.3.4]',
               'a(b)@c', 'a,b@c', 'A.B+c@ex-ample.co']
    for _ in range(1500):
        pool = alphabet if rng.random() < .7 else alphabet + others
        local = ''.join(rng.choices(pool, k=rng.randint(1, 8)))
        domain = ''.join(rng.choices(pool, k=rng.randint(1, 8)))
        samples.append(local + '@' + domain)
    # Logins without '@' (for example 'apikey') become the MAIL FROM address.
    samples += ['apikey', 'user.name', '.', '..', 'a.', '.a', "o'brien", 'àlias']
    for _ in range(500):
        pool = alphabet if rng.random() < .7 else alphabet + others
        samples.append(''.join(rng.choices(pool, k=rng.randint(1, 12))))
    return [{'address': a, 'quoted': smtplib.quoteaddr(a)} for a in samples]


def main():
    setup()
    import smtplib
    probe = smtplib.SMTP()
    fqdn = socket.getfqdn()
    local_hostname = fqdn if '.' in fqdn else None
    if local_hostname is None:
        try:
            local_hostname = '[%s]' % socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            local_hostname = '[127.0.0.1]'
    del probe
    with tempfile.TemporaryDirectory() as directory:
        context = certificate(directory)
        smtp = smtp_cases(context, local_hostname)
    report = {'python': sys.version.split()[0], 'hostname': socket.gethostname(), 'local_hostname': local_hostname,
              'smtp': smtp, 'mime': mime_cases(), 'addresses': address_cases()}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=1, ensure_ascii=True) + '\n')
    print(json.dumps({'smtp': len(smtp), 'mime': len(report['mime']), 'addresses': len(report['addresses']),
                      'local_hostname': local_hostname}))


if __name__ == '__main__':
    main()
