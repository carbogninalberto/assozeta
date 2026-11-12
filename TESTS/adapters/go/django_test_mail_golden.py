"""Golden: the admin e-mail test message as Django 5.2 renders it, and its SMTP envelope.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/django_test_mail_golden.py``
Each case is ``formataddr((sender_name, from_email))`` followed by
``EmailMessage(subject, body, value, [recipient]).message()`` flattened with
``as_bytes(linesep='\\r\\n')``; the Date and Message-ID are fixed. The envelope
is ``smtplib.quoteaddr(sanitize_address(...))`` exactly as the SMTP backend
sends it. A non-ASCII from_email makes formataddr raise; the probe catches the
exception after opening the connection and answers the generic failure.
Writes BE_GO/internal/smtpsend/testdata/django_test_mail.json. No network, no
database.
"""
import django
import json
import sys
import itertools
import smtplib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/smtpsend/testdata/django_test_mail.json'

from django.conf import settings

settings.configure(USE_I18N=False, DEFAULT_CHARSET='utf-8', EMAIL_USE_LOCALTIME=False)
django.setup()
from email.utils import formataddr  # noqa: E402
from django.core.mail import EmailMessage  # noqa: E402
from django.core.mail.message import sanitize_address  # noqa: E402
from django.core.validators import EmailValidator  # noqa: E402
from django.core.exceptions import ValidationError  # noqa: E402
import django.core.mail.message as dm  # noqa: E402

dm.formatdate = lambda *a, **k: 'Wed, 11 Nov 2026 10:00:00 -0000'
dm.make_msgid = lambda domain=None: '<177000000000.1.2@host.example>'
SUBJECT, BODY = 'Assozeta — verifica email', 'Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email.'
names = ['', 'Club', 'Società Sportiva', 'A "quoted" name', 'Doe, John', 'back\\slash', 'ASD (Roma)', ' spazi  doppi ',
         'Associazione Sportiva Dilettantistica Polisportiva Comunale di San Giovanni in Persiceto e dintorni AA',
         'Associazione Sportiva Dilettantistica Polisportiva Città di San Giovanni in Persiceto e dintorni ÀÈ', 'tab\there', 'ÀÈÌ' * 33, 'x' * 100,
         'a@b', 'punto.e:virgola;', '<angolari>', 'è']
froms = ['noreply@example.com', '"no reply"@example.com', 'info@exämple.com', 'INFO@Example.COM', 'a' * 64 + '@' + '.'.join(['b' * 60] * 3) + '.example.com',
         '"a,b"@example.com', 'ſender@example.com', '"quo\\"ted"@example.com', 'x@[127.0.0.1]', 'a+b@sub.münchen.example']
tos = ['a@example.com', '"a b"@example.com', 'x@bücher.example', 'A.B@Example.COM', 'c' * 64 + '@' + '.'.join(['d' * 60] * 3) + '.example.org',
       '"a,b"@example.com', '"a@b"@example.com', 'Kelvin@example.com'.replace('K', 'K'), 'x@[IPv6:::1]', '"semi;colon"@example.com',
       '"' + 'w w ' * 15 + 'w"@example.com', 'ö@example.com', '"ö"@example.com', 'a@xn--bcher-kva.example', 'a@' + 'é' * 20 + '.example']
# The instance fixture's 100-character sender name, with the addresses of
# email-test-send-long-sender-name.
FIXTURE_NAME = 'Associazione Sportiva Dilettantistica Polisportiva Comunale di San Giovanni in Persiceto e dintorni.'
valid = EmailValidator()
def ok(address, limit=254):
    try:
        valid(address); return len(address) <= limit
    except ValidationError:
        return False
out = []
def one(name, sender, to):
    row = {'sender_name': name, 'from_email': sender, 'recipient': to}
    try:
        value = formataddr((name, sender))
        row['formataddr'] = value
        message = EmailMessage(SUBJECT, BODY, value, [to])
        row['message'] = message.message().as_bytes(linesep='\r\n').decode('latin-1')
        row['envelope_from'] = sanitize_address(message.from_email, message.encoding)
        row['envelope_to'] = [sanitize_address(a, message.encoding) for a in message.recipients()]
        row['mail_from'] = smtplib.quoteaddr(row['envelope_from'])
        row['rcpt_to'] = [smtplib.quoteaddr(a) for a in row['envelope_to']]
    except Exception as e:
        row['error'] = f'{type(e).__name__}: {e}'
    out.append(row)
skipped = []
for sender in froms:
    if not ok(sender): skipped.append(('from', sender))
for to in tos:
    if not ok(to): skipped.append(('to', to))
vf, vt = [f for f in froms if ok(f)], [t for t in tos if ok(t)]
for name in names: one(name, vf[0], vt[0])
for sender in vf: one('Club', sender, vt[0]); one('', sender, vt[0]); one('Società Sportiva', sender, vt[0])
for to in vt: one('Club', vf[0], to)
for name, sender, to in itertools.product(names[8:10] + names[11:12], vf[1:5], vt[1:5]): one(name, sender, to)
one(FIXTURE_NAME, 'mittente@example.test', 'destinatario@example.test')
OUTPUT.write_text(json.dumps({'django': django.get_version(), 'python': sys.version.split()[0], 'subject': SUBJECT, 'body': BODY,
                              'rejected_by_email_field': skipped, 'cases': out}, ensure_ascii=True, indent=0) + '\n')
print(len(out), 'cases', len([r for r in out if 'error' in r]), 'errors; rejected:', skipped, file=sys.stderr)
for r in out:
    if 'error' in r: print('ERR', r['sender_name'][:20], r['from_email'][:30], r['recipient'][:30], r['error'][:150])
