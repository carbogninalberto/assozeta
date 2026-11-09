"""Capture Django's EmailMultiAlternatives bytes for the Go renderer's golden test.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/django_mail_golden.py``
Each case is built like core.tasks.custom_send_mail (EmailMultiAlternatives
with reply_to and an optional text/html alternative) and flattened with
message().as_bytes(linesep='\\r\\n'). Date, Message-ID and the MIME boundary
are fixed so the bytes are reproducible. Cases marked ``unsupported`` are
outside the Go subset: RenderDjangoMessage must refuse them.
Writes BE_GO/internal/smtpsend/testdata/django_mail.json.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/smtpsend/testdata/django_mail.json'
BOUNDARY = '===============1234567890123456789=='
MESSAGE_ID = '<179000000000.4242.1234567890123456789@golden.example.test>'
DATE = 'Mon, 09 Nov 2026 08:43:54 -0000'
FROM = 'Assozeta Fixture <noreply@example.test>'

CASES = [
    {'name': 'plain-ascii', 'subject': 'Conferma', 'body': 'Ciao\nok\n', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'plain-utf8', 'subject': 'Conferma iscrizione', 'body': 'Ciao,\nla tua iscrizione è confermata.\n', 'from': FROM,
     'to': ['a@example.test']},
    {'name': 'plain-no-trailing-newline', 'subject': 'S', 'body': 'riga', 'from': 'noreply@example.test', 'to': ['a@example.test']},
    {'name': 'crlf-and-lone-cr', 'subject': 'S', 'body': 'uno\r\ndue\rtre\n\nquattro', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'empty-body', 'subject': 'S', 'body': '', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'html-alternative', 'subject': 'Promemoria: quota già scaduta', 'body': 'Testo semplice',
     'html': '<p>Quota <b>già</b> scaduta</p>', 'from': 'Il Team di Assozeta Fixture <noreply@example.test>',
     'to': ['uno@example.test', 'due@example.test'], 'reply_to': ['owner@example.test']},
    {'name': 'html-only', 'subject': 'Solo HTML', 'body': None, 'html': '<p>ok</p>\n', 'from': 'Società Tre <noreply@example.test>',
     'to': ['a@example.test']},
    {'name': 'html-ascii-two-reply-to', 'subject': 'Reply', 'body': 'x', 'html': '<i>y</i>', 'from': FROM, 'to': ['a@example.test'],
     'reply_to': ['r1@example.test', 'r2@example.test']},
    {'name': 'long-to-list', 'subject': 'Molti destinatari', 'body': 'x', 'from': FROM,
     'to': [f'destinatario.numero{i}@example.test' for i in range(6)]},
    {'name': 'subject-70', 'subject': 'Abcdefghij ' * 6 + 'abcd', 'body': 'x', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'subject-76', 'subject': ('Avviso importante per tutti i soci della associazione sportiva Uno Due Tre' + ' Quattro')[:76], 'body': 'x',
     'from': FROM, 'to': ['a@example.test']},
    {'name': 'subject-77', 'subject': ('Avviso importante per tutti i soci della associazione sportiva Uno Due Tre' + ' Quattro')[:77], 'body': 'x',
     'from': FROM, 'to': ['a@example.test'], 'unsupported': True},
    {'name': 'subject-utf8-short', 'subject': 'Città', 'body': 'x', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'subject-utf8-base64', 'subject': 'àèìòù€€€', 'body': 'x', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'subject-utf8-limit', 'subject': 'Il certificato medico è in scadenza tra 30 gg', 'body': 'x', 'from': FROM,
     'to': ['a@example.test']},
    {'name': 'subject-utf8-long', 'subject': 'Il tuo certificato medico è in scadenza: rinnovalo prima della prossima lezione',
     'body': 'x', 'from': FROM, 'to': ['a@example.test'], 'unsupported': True},
    {'name': 'subject-double-space', 'subject': 'Due  spazi', 'body': 'x', 'from': FROM, 'to': ['a@example.test'], 'unsupported': True},
    {'name': 'from-quoted-name', 'subject': 'S', 'body': 'x', 'from': '"Cinque, SMTP" <noreply@example.test>', 'to': ['a@example.test'],
     'unsupported': True},
    {'name': 'from-long-name', 'subject': 'S', 'body': 'x',
     'from': 'Associazione Sportiva Dilettantistica Polisportiva Unione Di Prova <noreply@example.test>', 'to': ['a@example.test']},
    {'name': 'line-998', 'subject': 'S', 'body': 'a' * 998 + '\n', 'from': FROM, 'to': ['a@example.test']},
    {'name': 'line-999', 'subject': 'S', 'body': 'a' * 999 + '\n', 'from': FROM, 'to': ['a@example.test'], 'unsupported': True},
    {'name': 'boundary-in-body', 'subject': 'S', 'body': '--' + BOUNDARY + '\n', 'html': '<p>x</p>', 'from': FROM,
     'to': ['a@example.test']},
    {'name': 'display-name-recipient', 'subject': 'S', 'body': 'x', 'from': FROM, 'to': ['Mario <m@example.test>'], 'unsupported': True},
    {'name': 'utf8-recipient', 'subject': 'S', 'body': 'x', 'from': FROM, 'to': ['çağla@example.test'], 'unsupported': True},
]


def main():
    sys.path.insert(0, str(ROOT / 'BE'))
    from django.conf import settings
    settings.configure(DEFAULT_CHARSET='utf-8', EMAIL_USE_LOCALTIME=False, DEFAULT_FROM_EMAIL=FROM)
    import email.generator
    from django.core.mail import EmailMultiAlternatives, message as django_message
    django_message.formatdate = lambda localtime=False: DATE
    django_message.make_msgid = lambda domain=None: MESSAGE_ID
    original = email.generator.Generator._make_boundary.__func__

    def fixed(cls, text=None):
        # Python's own collision loop on the fixed base.
        import random
        state = random.getstate()
        try:
            random.randrange = lambda *_: 1234567890123456789
            return original(cls, text)
        finally:
            del random.randrange
            random.setstate(state)
    email.generator.Generator._make_boundary = classmethod(fixed)
    results = []
    for case in CASES:
        mail = EmailMultiAlternatives(case['subject'], case['body'], case['from'], case['to'], reply_to=case.get('reply_to'))
        if case.get('html'):
            mail.attach_alternative(case['html'], 'text/html')
        raw = mail.message().as_bytes(linesep='\r\n')
        results.append({**case, 'bytes': raw.decode('latin-1')})
    OUTPUT.write_text(json.dumps({'date': DATE, 'message_id': MESSAGE_ID, 'boundary': BOUNDARY, 'cases': results},
                                 ensure_ascii=False, indent=1) + '\n')
    print(len(results), 'messages ->', OUTPUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
