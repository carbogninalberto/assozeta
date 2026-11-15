"""Capture CPython's compat32 header folding for BE_GO/internal/smtpsend.

Standard library only: run with the migration interpreter, from anywhere:
    python -I smtpsend_header_golden.py [output]

Every case is folded exactly as ``MIMEMultipart().as_string()`` folds a header
(compat32, ``Header(...).encode(linesep='\\n', maxlinelen=0)``), or with an
explicit ``maxlinelen`` for the Django folding widths; the body cases pin the
boundary and record the whole message ``MIMEText`` produces. A value Python
raises on is recorded with ``folded: null`` (or ``message: null``) and the
exception class. A value with lone surrogates is recorded as code points
(``runes``), because a JSON string escape would not survive Go's decoder; a
value too large to keep is a list of repeated ``parts`` and its folded line a
digest; every other value is a plain string.

The output is ``python_header_fold.json`` next to this script;
``go test ./internal/smtpsend`` replays it.
"""
import hashlib
import json
from email import charset
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import re
import sys
import time

DEFAULT = Path(__file__).resolve().parent / 'python_header_fold.json'

SURROGATE = re.compile('[\ud800-\udfff]')


def fold(name, value, maxlinelen=0):
    if SURROGATE.search(value):
        header = Header(value, charset=charset.UNKNOWN8BIT, header_name=name)
    else:
        header = Header(value, header_name=name)
    return header.encode(linesep='\n', maxlinelen=maxlinelen)


def message_line(name, value):
    """The header line as_string() writes for name: value."""
    message = MIMEMultipart()
    message[name] = value
    text = message.as_string()
    start = text.index(name + ': ')
    end = text.index('\n\n', start)
    return text[start:end + 1]


def outcome(call):
    try:
        return call(), None
    except Exception as exc:
        return None, type(exc).__name__


def value_fields(value, parts=None):
    if parts is not None:
        return {'parts': [{'unit': unit, 'count': count} for unit, count in parts]}
    if SURROGATE.search(value):
        return {'runes': [ord(character) for character in value]}
    return {'value': value}


def text_fields(text, large):
    if not large:
        return {'folded': text}
    raw = text.encode('utf-8')
    return {'folded_sha256': hashlib.sha256(raw).hexdigest(), 'folded_chars': len(text),
            'folded_head': text[:60], 'folded_tail': text[-60:]}


def message_case(name, value, large=False, parts=None):
    line, raised = outcome(lambda: message_line(name, value))
    case = {'name': name, **value_fields(value, parts), 'raised': raised}
    case.update(text_fields(line or '', large))
    if line is None:
        case.pop('folded', None)
        case.pop('folded_sha256', None)
        case.pop('folded_chars', None)
        case.pop('folded_head', None)
        case.pop('folded_tail', None)
        case['folded'] = None
    return case


def header_case(name, value, maxlinelen, large=False, parts=None):
    folded, raised = outcome(lambda: fold(name, value, maxlinelen))
    case = {'name': name, 'maxlinelen': maxlinelen, **value_fields(value, parts), 'raised': raised}
    if folded is None:
        case['folded'] = None
    else:
        case.update(text_fields(folded, large))
    return case


FROM_USER = 'sender@example.test'


BOUNDARY = '===============0000000000000000001=='


def send_email_message(user, subject, to, html, text, boundary=BOUNDARY):
    """The MIMEMultipart send_email builds, with a pinned boundary."""
    msg = MIMEMultipart('alternative') if html else MIMEMultipart()
    msg['From'] = f'Associazione <{user}>'
    msg['To'] = ', '.join(to)
    msg['Subject'] = subject
    msg.set_boundary(boundary)
    if html:
        msg.attach(MIMEText(html, 'html', 'utf-8'))
    else:
        msg.attach(MIMEText(text, 'plain', 'utf-8'))
    return msg.as_string()


def body_cases():
    """MIMEText bodies of send_email's message, with the boundary pinned."""
    bodies = [
        ('text', 'a\udc80b'), ('html', 'a\udc80b'), ('text', '\ud800'), ('html', 'Città \udcff'),
        ('html', '<p>€ 10,00</p>\r\n'), ('text', '.line\n..two\r\n'), ('text', ''),
        ('text', 'Ciao è 😀'), ('html', 'x' * 1500), ('text', '\n'),
    ]
    cases = []
    for kind, body in bodies:
        html = body if kind == 'html' else None
        text = body if kind == 'text' else None
        message, raised = outcome(lambda: send_email_message('sender@example.test', 'Bakney Sport', ['a@example.test'],
                                                             html, text))
        cases.append({'kind': kind, **value_fields(body), 'raised': raised, 'boundary': BOUNDARY,
                      **({'message': None} if message is None else {'message': message})})
    return cases


def message_cases():
    surrogates = [
        ('Subject', '\udc80'),
        ('Subject', 'a\udc80b'),
        ('Subject', '\udcff'),
        ('Subject', '\udc80\udcff'),
        ('Subject', '\ud800'),
        ('Subject', '\ud800\udc80'),
        ('Subject', 'a\ud800b'),
        ('Subject', '\udcc3\udca8'),
        ('Subject', 'Città \udcff'),
        ('Subject', 'Città \udcc3\udca8'),
        ('Subject', 'à\udc80'),
        ('Subject', 'line\udc80\nsecond'),
        ('Subject', 'line\udc80\n second'),
        ('Subject', 'line\n\udc80'),
        ('Subject', 'a\udc80b\n c\udcffd'),
        ('Subject', ' \udc80 '),
        ('Subject', '\udc80\n'),
        ('Subject', 'xxxxxxxxxx\udc80yyyyyyyyyy'),
        ('Subject', '=?utf-8?q?x?=\udc80'),
        ('Subject', 'Promemoria \udc80 evento \udcff con testo lungo e spazi'),
        ('Subject', 'tab\there \udc80'),
        ('Subject', 'trailing space \udc80 '),
        ('Subject', 'first\n\t\udc80second'),
        ('From', f'Associazione \udc80 <{FROM_USER}>'),
        ('From', f'Città \udcc3\udca8 <{FROM_USER}>'),
        ('From', f'Associazione \udcff <{FROM_USER}>'),
        ('To', 'a@example.test, \udc80@example.test'),
        ('To', 'à@example.test, \udcff@example.test'),
        ('Reply-To', 'owner\udc80@example.test'),
        ('Reply-To', 'owner@example.test, \udc80@example.test'),
    ]
    cases = [message_case(name, value) for name, value in surrogates]
    cases += [
        message_case('Subject', '\udc80' * 100),
        message_case('Subject', 'Re: \udc80 and more text with spaces \udcff'),
        message_case('Subject', 'a' * 1000001, large=True, parts=[('a', 1000001)]),
        message_case('Subject', 'a' * 1000000 + ' b', large=True, parts=[('a', 1000000), (' b', 1)]),
        message_case('Subject', 'a' * 500000 + ' ' + 'b' * 500001, large=True,
                     parts=[('a', 500000), (' ', 1), ('b', 500001)]),
        message_case('Subject', 'é' * 100001, large=True, parts=[('é', 100001)]),
        message_case('Subject', '中文 😀 ' * 20000, large=True, parts=[('中文 😀 ', 20000)]),
        # A million non-ASCII characters, one line each: CPython folds an
        # encoded line one character at a time and measures the whole line every
        # time, so a single million-character encoded line costs many minutes
        # (the split itself is covered by the 76/78 cases and the case below).
        message_case('Subject', ('è' * 20 + '\n') * 47620, large=True, parts=[('è' * 20 + '\n', 47620)]),
        # 376,000 non-ASCII characters encode to more than 1,000,000 characters,
        # so Header.encode splits them into encoded words even at maxlinelen 0.
        message_case('Subject', 'é' * 376000, large=True, parts=[('é', 376000)]),
    ]
    return cases


def header_cases():
    varied = [
        '', ' ', '  ', 'a', 'a b', 'a  b', ' a', 'a ', ' a ', 'a\tb', 'a\t\tb', 'a \t b',
        'Promemoria: evento, promemoria evento', 'a;b;c', 'a,b,c', 'a; b, c d',
        'x' * 79, 'x' * 80, 'x' * 160, ('parola ' * 20).strip(), ('parola ' * 20),
        'Città', 'Città ' * 12, 'à' * 40, 'è' * 60, '中文' * 30, '😀' * 20,
        'mixed à and ascii words here and there ' * 2,
        'Oggetto: "Promemoria, evento"; nota',
        'a' * 60 + ' ' + 'b' * 60,
        ('parola;' * 15), ('parola,' * 15), ('a ' * 50), ('a\tb ' * 30),
        'trailing spaces   ', '   leading spaces', '\tleading tab',
        'semi;tab\tand,comma mix that folds somewhere',
        'Città: sport <u@x.it>', 'A & B <u@x.it>', 'verylongwordwithoutanybreakcharacteratallreallylong',
        '=?utf-8?q?already_encoded?= text',
    ]
    cases = []
    for maxlinelen in (76, 78):
        for index, value in enumerate(varied):
            name = ('Subject', 'To', 'From', 'Reply-To')[index % 4]
            cases.append(header_case(name, value, maxlinelen))
    for maxlinelen in (76, 78):
        for value in ('\udc80' * 40, 'a\udc80b\n c\udcffd', '\ud800', '\udc80'):
            cases.append(header_case('Subject', value, maxlinelen))
    cases.append(header_case('Subject', 'a' * 1000001, 0, large=True, parts=[('a', 1000001)]))
    cases.append(header_case('Subject', 'a' * 1000000 + ' b', 0, large=True, parts=[('a', 1000000), (' b', 1)]))
    cases.append(header_case('Subject', 'é' * 100001, 0, large=True, parts=[('é', 100001)]))
    return cases


def chunk_cases():
    """Header.append with explicit charsets, folded by the same encode()."""
    groups = [
        [('Città', 'utf-8'), ('sport', None)],
        [('ascii', None), ('Città', 'utf-8')],
        [('Città', 'utf-8'), ('è', 'utf-8')],
        [('uno', None), ('due', None)],
        [('Città', 'utf-8'), (' ', 'utf-8'), ('club', 'utf-8')],
        [('a', None), ('\udc80', 'unknown-8bit')],
        [('Città', 'utf-8'), ('\udc80', 'unknown-8bit'), ('b', None)],
        [('plain text that is long enough to fold at seventy six columns', None), ('Città', 'utf-8')],
        [('x' * 70, None), ('y' * 20, 'utf-8')],
        [('', None), ('Città', 'utf-8')],
    ]
    cases = []
    for maxlinelen in (0, 76):
        for index, group in enumerate(groups):
            header = Header(header_name='Subject')
            for text, name in group:
                header.append(text, None if name is None else charset.Charset(name))
            folded, raised = outcome(lambda: header.encode(linesep='\n', maxlinelen=maxlinelen))
            chunks = []
            for text, name in group:
                chunk = {'charset': name or 'us-ascii', **value_fields(text)}
                chunks.append(chunk)
            cases.append({'maxlinelen': maxlinelen, 'chunks': chunks, 'raised': raised,
                          **({'folded': None} if folded is None else {'folded': folded})})
    return cases


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    started = time.monotonic()
    report = {'python': sys.version.split()[0],
              'generator': 'smtpsend_header_golden.py (beside this file)',
              'message': message_cases(), 'header': header_cases(), 'chunks': chunk_cases(),
              'body': body_cases()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    raised = sum(1 for case in report['message'] + report['header'] + report['chunks'] + report['body']
                 if case['raised'] is not None)
    print(json.dumps({'message': len(report['message']), 'header': len(report['header']),
                      'chunks': len(report['chunks']), 'body': len(report['body']), 'raised': raised,
                      'seconds': round(time.monotonic() - started, 1)}))


if __name__ == '__main__':
    main()
