#!/usr/bin/env python3
"""Golden vectors for the slug and date grammar of BE_GO/internal/codicefiscale.

Run with the migration interpreter (python-codicefiscale 0.10.3, python-slugify
8.0.4, text-unidecode 1.3, python-dateutil 2.9.0.post0), TZ=UTC:

    TZ=UTC quality-reports/go-migration/python31316/bin/python -I \
        BE_GO/internal/codicefiscale/testdata/gen_golden.py

Writes testdata/golden.json.gz: one object per call of the real
codicefiscale.encode with datetime.now() and time.localtime() frozen at "now".
Texts are lists of code points (a str may hold lone surrogates). "result" is
the code, or "invalid" / "notmapped" as the tax code view tells exceptions apart.
"""
import datetime
import gzip
import io
import json
import random
import time
import types
import warnings
from pathlib import Path

import dateutil.parser._parser as dateutil_parser
from codicefiscale import codicefiscale as module
from slugify import slugify

assert time.tzname[0] == 'UTC', time.tzname
warnings.simplefilter('ignore')
NOW = datetime.datetime(2026, 11, 9, 13, 45, 12)


class FrozenDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(NOW.year, NOW.month, NOW.day, NOW.hour, NOW.minute, NOW.second)


shim = types.SimpleNamespace(**{name: getattr(datetime, name) for name in dir(datetime) if not name.startswith('__')})
shim.datetime = FrozenDateTime
dateutil_parser.datetime = shim
clock = types.SimpleNamespace(**{name: getattr(time, name) for name in dir(time) if not name.startswith('__')})
clock.localtime = lambda *args: time.struct_time((NOW.year, 1, 1, 0, 0, 0, 0, 1, 0))
dateutil_parser.time = clock
module.datetime = FrozenDateTime

rng = random.Random(20261111)
PIECES = [
    'a', 'e', 'i', 'o', 'u', 'b', 'c', 'd', 'r', 's', 't', 'M', 'R', 'Z', ' ', ' ', '-', "'", "''", ',', '.', '1', '2', '0', '9',
    '&', '#', ';', 'x', 'X', '&amp;', '&eacute;', '&Eacute;', '&szlig;', '&lt;', '&nbsp;', '&sup2;', '&frac12;', '&alefsym;',
    '&AMP;', '&amp', '&#65;', '&#x41;', '&#X41;', '&#39;', '&#x27;', '&#0;', '&#1637;', '&#x665;', '&#xD800;', '&#55296;',
    '&#1114111;', '&#1114112;', '&#x10FFFF;', '&#x110000;', '&#44;', '&#x2c;', '&#304;', '&#x130;', '&#8490;', '&#xfb01;',
    '&#x&#1637;;', '&#&#x35;;', '&amp;#65;', '&amp;amp;', '&#' + '9' * 30 + ';', '&#x' + 'f' * 30 + ';', '&#٣٣;', '&#x٣a;',
    'é', 'ß', 'Æ', 'ñ', 'ü', 'İ', 'ı', 'ŉ', 'ǆ', 'ﬁ', '№', '™', '€', '½', '²', 'µ', 'Å', 'K',
    'Ж', 'щ', 'ъ', 'Ω', 'λ', 'ς', 'א', 'ب', '٣', '۵', '３', '१', '毛', '泽', 'ロ', 'ー', 'マ', 'か', '한', '글', '☃', '😀', '𝐀', '𝟗',
    '́', '̈', '̧', '̣', '́' * 35, '̧̣́', '͏', '​', '­', '﻿',
    '\x00', '\x01', '\x7f', '\x85', ' ', '￿', '￾', '�', '\ud800', '\udfff', '\udc00\ud800', '\U0010ffff',
]
DATES = [
    '12 may 1990', 'May 12, 1990', '12 maggio 1990', '1990-05-12T10:00', '1990-05-12T10:00:00Z', '1990-05-12t10:00:00+02:00',
    'jan 5', '5 jan 90', 'june-5-1990', 'june 5 90', '10:30 12/05/1990', '12/05/1990 10:30pm', '12/05/1990 10pm', '12/05/1990 am',
    'Thu Sep 25 10:36:28 2003', '1990 may', 'may', 'am', 'pm', 'a', 't', 'z', '12/05/1990 UTC', '12/05/1990 CET', '12/05/1990 EST',
    '12.05.1990 +02:00', '5th of may 1990', 'may 5th 1990', '1990 5 may', '90 may 5', '5 may', 'may 1990', '31 feb 1990',
    'sept 12 1990', 'sep 12 1990', 'september 1990 12', 'tuesday 12 may 1990', 'tue 12/05/1990', '12 may 1990 at 10', '12 de mayo 1990',
    '1990 w05', '1990-W05-1', 'J1990', '12may1990', '1990may12', 'may121990', '12/may/1990', '1990/may/12', '12/1990/may',
    '10-11-12 jan', '10 jan 11', 'jan 10 11', '2010 jan 11', '10 11 jan', '31 12 jan', '1 2 3 jan', 'jan jan 1990', 'ad 1990', '1990 bc',
    '12/05/1990 h10', '12/05/1990 10h', '12/05/1990 10h30m', '12/05/1990 10 h 30', '12/05/1990 10m', '12/05/1990 10s',
    '12/05/1990 10 hours', '12/05/1990 2 minutes', '12/05/1990 gmt+3', '12/05/1990 utc-3', '12/05/1990 gmt', '12/05/1990 z',
    '1990-05-12T25:00', '1990-05-12T10:61', '12 may 1990 0.5h', '12 may 99999', '12 may 0', '0 may 1990', 'mayo', 'x', 'jan-feb-mar',
    '&#49;2/05/1990', '1&#50;/05/1990', '12/05/1990&nbsp;', '１２/０５/１９９０', '١٢/٠٥/١٩٩٠', '12 05 1990', '12–05–1990',
    '1990年5月12日', 'ⅻ/05/1990', '12/05/1990 ' + 'a' * 40, 't1990', '1990t', 'tt', '12t05t1990', '1,990 may 12', "12'may'1990",
]


def text(maximum):
    return ''.join(rng.choice(PIECES) for _ in range(rng.randint(1, maximum)))


def encode(last, first, sex, date, place):
    try:
        return module.encode(lastname=last, firstname=first, gender=sex, birthdate=date, birthplace=place)
    except Exception as error:
        return 'notmapped' if 'not mapped to code' in str(error) else 'invalid'


cases = []


def add(last, first, sex, date, place):
    cases.append({'last': [ord(c) for c in last], 'first': [ord(c) for c in first], 'sex': sex, 'date': [ord(c) for c in date],
                  'place': [ord(c) for c in place], 'result': encode(last, first, sex, date, place)})


for piece in PIECES:
    add('Ro' + piece + 'ssi', 'Mario', 'M', '12/05/1990', 'Roma')
    add('Rossi', piece + 'Ma' + piece + 'rio' + piece, 'F', '12/05/1990', 'Roma')
    add('Rossi', 'Mario', 'M', '12/05/1990', 'Ro' + piece + 'ma')
    add('Rossi', 'Mario', 'M', '12/05/1990', 'Roma' + piece)
    add('Rossi', 'Mario', 'M', '12' + piece + '05' + piece + '1990', 'Roma')
for date in DATES:
    add('Rossi', 'Mario', 'M', date, 'Roma')
    add('Bianchi', 'Anna', 'f', date.upper(), 'Milano')
for _ in range(1500):
    add(text(8), text(8), rng.choice('MFmf'), rng.choice(DATES) if rng.random() < 0.5 else '12/05/1990',
        rng.choice(['Roma', 'Milano', 'Francia', 'H501']) if rng.random() < 0.7 else text(6))
for _ in range(1500):
    words = rng.sample(['12', '5', '05', '1990', '90', '31', '2003', 'may', 'jan', 'sept', 'june', 'tue', 'am', 'pm', 't', 'z', 'utc',
                        '10:30', '10:30:15', '+02:00', '-0300', 'h', 'm', 's', 'of', 'at', 'and', 'a', '0', '00', '1', '13', '32', '100',
                        '0.5', '12.5', 'ad', 'bc', 'gmt', 'cet', '99', '2026', '29', 'feb', '2000', '1900'], rng.randint(1, 5))
    add('Rossi', 'Mario', 'M', rng.choice([' ', '-', '/', '.', ',', '']).join(words), 'Roma')

payload = json.dumps(cases, ensure_ascii=True, separators=(',', ':')).encode()
buffer = io.BytesIO()
with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as gz:
    gz.write(payload)
(Path(__file__).parent / 'golden.json.gz').write_bytes(buffer.getvalue())
print(len(cases), 'cases', {kind: sum(c['result'] == kind for c in cases) for kind in ('invalid', 'notmapped')})
