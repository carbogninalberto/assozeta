"""Record Python's header folds and envelope quoting for BE_GO/internal/smtpsend.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/smtpsend_full_golden.py [count] [output]``
Standard library only. Each header is written the way
``MIMEMultipart().as_string()`` writes it (compat32, ``maxheaderlen=0``); a value
Python raises on is recorded with ``folded: null``. Addresses record
``smtplib.quoteaddr``; the encode cases record ``str(UnicodeEncodeError)`` of
``text.encode('ascii')`` and ``repr(text)``. The default output is
``BE_GO/internal/smtpsend/testdata/python_full.json``; ``go test
./internal/smtpsend`` replays it (``SMTPSEND_FULL_GOLDEN`` names a larger file).
"""
import json
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import random
import smtplib
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/smtpsend/testdata/python_full.json'
BREAKS = ['\n', '\r', '\r\n', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', ' ', ' ']
WORDS = ['a', 'bc', 'Oggetto', ':', ';', ',', '=?utf-8?q?x?=', 'x:y', '.', '(', ')']
SPACES = [' ', ' ', ' ', '\t', '  ', '\x1f', '\xa0', '　']
OTHER = ['è', 'Città', '中文', '😀', 'ß']


def header_value(rng):
    pool = WORDS + SPACES + BREAKS + (OTHER if rng.random() < .4 else [])
    weights = [6] * len(WORDS) + [5] * len(SPACES) + [2] * len(BREAKS) + [3] * (len(pool) - len(WORDS) - len(SPACES) - len(BREAKS))
    return ''.join(rng.choices(pool, weights=weights, k=rng.randint(0, 14)))


def fold(name, value):
    message = MIMEMultipart()
    message[name] = value
    try:
        text = message.as_string()
    except Exception:
        return None
    start = text.index(name + ': ')
    end = text.index('\n\n', start)
    return text[start:end + 1]


def address(rng):
    atoms = ['a', 'b.c', 'x', 'example.test', 'è', 'ü.it', '1.2.3.4', 'o\'n', '\xa0', '\x1f', ' ', '中']
    marks = list('()<>@,:;."[]\\ \t') + ['\r', '\n', '@', '@', '.', '"', '"']
    pool = atoms + marks
    return ''.join(rng.choices(pool, weights=[4] * len(atoms) + [1] * len(marks), k=rng.randint(0, 12)))


def encode_case(text):
    try:
        text.encode('ascii')
        error = ''
    except UnicodeEncodeError as exc:
        error = str(exc)
    return {'text': text, 'error': error, 'repr': repr(text)}


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT
    rng = random.Random(20261110)
    values = ['', 'a', 'a\nb', 'a\n b', 'a \n b', 'a\n\tb\n c', 'è\nb', 'è\n\n  b ', '\na', '\n a', 'a\n \nb', 'a\n\t\n b', 'a b ',
              'a\n b \n c ', 'x\ny: z', 'x\n y: z', 'a\x0b b', 'a\x1c\x1fb', 'a  è', ' ', 'a\n ', '"a b"@example.test, c@example.test']
    values += [header_value(rng) for _ in range(count)]
    headers = []
    for index, value in enumerate(values):
        name = ('Subject', 'To', 'From')[index % 3]
        headers.append({'name': name, 'value': value, 'folded': fold(name, value)})
    samples = ['', ' ', '<', ' <a@b>', 'a@b.c', '"a b"@example.test', '"a\\"b"@example.test', 'a@[1.2.3.4]', 'Name <a@b.c>',
               '"Na,me" <a@b.c>', 'N (c) <a@b.c>', 'a@b.c (c)', '(c) a@b.c', 'a(b)c@d.e', 'g: a@b.c, d@e.f;', '<@r.s:a@b.c>',
               'a@b.c, d@e.f', 'A & B <u@x.it>', 'A, B <u@x.it>', 'A (x <u@x.it>', 'A) <u@x.it>', 'A "q <u@x.it>', 'A \\ <u@x.it>',
               'Città: sport <u@x.it>', 'A <None>', 'A <>', 'a b@c', 'à@x.it', 'u@è.it', 'A.B <u@x.it>', 'A@B <u@x.it>',
               '\xa0 <u@x.it>', 'a\xa0.b@c', '\x1f.a@c']
    samples += [address(rng) for _ in range(count * 3)]
    addresses = [{'address': a, 'quoted': smtplib.quoteaddr(a)} for a in samples]
    texts = ['abc', 'à', 'aà', 'ab中', 'a😀b', 'aàèb', 'mail FROM:<à@x.it> size=12', 'a\tb\n\r', "a'b", 'a"b', 'a\'b"c', 'a\\b',
             '\x00\x1f\x7f\x80\x9f\xa0\xad', '  ​', 'x́', 'ß²½']
    pool = list('ab \'"\\\t\n') + ['à', '中', '😀', '\x85', '\xa0', '​', '\x7f', '\x01', '́', '²']
    texts += [''.join(rng.choices(pool, k=rng.randint(0, 8))) for _ in range(max(100, count // 3))]
    report = {'python': sys.version.split()[0], 'headers': headers, 'addresses': addresses, 'encode': [encode_case(t) for t in texts]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=1, ensure_ascii=True) + '\n')
    print(json.dumps({'headers': len(headers), 'raised': sum(1 for h in headers if h['folded'] is None),
                      'addresses': len(addresses), 'encode': len(report['encode'])}))


if __name__ == '__main__':
    main()
