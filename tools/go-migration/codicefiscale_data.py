#!/usr/bin/env python3
"""Generate the data files of BE_GO/internal/codicefiscale from the Python reference.

Run with the migration interpreter (python-codicefiscale 0.10.3, python-slugify 8.0.4,
text-unidecode 1.3 installed):

  quality-reports/go-migration/python31316/bin/python -I tools/go-migration/codicefiscale_data.py

Writes:
  BE_GO/internal/codicefiscale/data.txt.gz        lookup index (deterministic gzip)
  BE_GO/internal/codicefiscale/unidecode.bin.gz   text_unidecode replacements (code points 1..65535, NUL separated)
  BE_GO/internal/codicefiscale/html_entities.go   html.entities.name2codepoint and the decimal digit zeros

data.txt.gz format (UTF-8 text, tab separated, one item per line):
  codicefiscale-data v1
  records N
  CODE<TAB>CREATED<TAB>DELETED       x N (dates "YYYY-MM-DDTHH:MM:SS" or empty)
  index NAME N                       NAME in municipalities, countries, codes
  KEY<TAB>I,J,K                      x N (record indexes, in lookup order)

names.txt.gz (UTF-8 text, tab separated): one line per key of the codes index,
  CODE<TAB>NAME<TAB>NAME...          the 'name' of each option, in lookup order
(decode() resolves the birthplace by code; no code slug is a municipality or
country key).
"""
import gzip
import io
from datetime import datetime
from html.entities import name2codepoint
from pathlib import Path

import text_unidecode
from codicefiscale.codicefiscale import _DATA, _get_date

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / 'BE_GO/internal/codicefiscale'

INDEXES = ('municipalities', 'countries', 'codes')


def format_date(value):
    if not value:
        return ''
    parsed = datetime.fromisoformat(value)
    assert parsed == _get_date(value), value
    assert parsed.tzinfo is None and parsed.microsecond == 0, value
    # Go keeps None as the zero time (0001-01-01), so no real date may equal it.
    assert parsed.year > 1, value
    return (
        f'{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}'
        f'T{parsed.hour:02d}:{parsed.minute:02d}:{parsed.second:02d}'
    )


def check_field(value):
    assert '\t' not in value and '\n' not in value and ',' not in value, repr(value)
    return value


def build_data():
    records = []
    record_ids = {}
    lines = []
    index_lines = []
    for name in INDEXES:
        index = _DATA[name]
        index_lines.append(f'index {name} {len(index)}')
        for key in sorted(index):
            ids = []
            for record in index[key]:
                row = (
                    check_field(str(record['code'])),
                    format_date(record['date_created']),
                    format_date(record['date_deleted']),
                )
                if row not in record_ids:
                    record_ids[row] = len(records)
                    records.append(row)
                ids.append(str(record_ids[row]))
            index_lines.append(f'{check_field(key)}\t{",".join(ids)}')
    lines.append('codicefiscale-data v1')
    lines.append(f'records {len(records)}')
    lines.extend('\t'.join(row) for row in records)
    lines.extend(index_lines)
    payload = ('\n'.join(lines) + '\n').encode('utf-8')
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write(payload)
    return buffer.getvalue()


def build_names():
    from slugify import slugify
    codes = _DATA['codes']
    lines = []
    for key in sorted(codes):
        assert slugify(key).upper() == key, key
        assert slugify(key) not in _DATA['municipalities'] and slugify(key) not in _DATA['countries'], key
        names = [record['name'] for record in codes[key]]
        for name in names:
            assert name and '\t' not in name and '\n' not in name, repr(name)
        lines.append('\t'.join([key] + names))
    payload = ('\n'.join(lines) + '\n').encode('utf-8')
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write(payload)
    return buffer.getvalue()


def build_unidecode_table():
    """text_unidecode._replaces (code points 1..65535), NUL separated, as a deterministic gzip."""
    replaces = text_unidecode._replaces
    assert len(replaces) == 65535, len(replaces)
    for entry in replaces:
        assert entry.isascii() and '\x00' not in entry, entry
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write('\x00'.join(replaces).encode('ascii'))
    return buffer.getvalue()


def build_html_entities():
    import unicodedata
    names = sorted(name2codepoint)
    for name in names:
        assert name.isascii() and name.isalnum(), name
    zeros = [cp for cp in range(0x110000) if unicodedata.category(chr(cp)) == 'Nd' and unicodedata.digit(chr(cp)) == 0]
    for zero in zeros:
        assert [unicodedata.digit(chr(zero + n)) for n in range(10)] == list(range(10)), zero
    assert sum(unicodedata.category(chr(cp)) == 'Nd' for cp in range(0x110000)) == 10 * len(zeros)
    parts = [
        '// Code generated by tools/go-migration/codicefiscale_data.py. DO NOT EDIT.',
        '//',
        '// Source: Python 3.13 html.entities.name2codepoint (the names python-slugify',
        '// 8.0.4 substitutes in CHAR_ENTITY_PATTERN) and the zero of every run of',
        '// decimal digits (category Nd) that int() and the regex class \\d accept.',
        '',
        'package codicefiscale',
        '',
        'var htmlEntities = map[string]rune{',
    ]
    line = '\t'
    for name in names:
        item = f'"{name}": 0x{name2codepoint[name]:X}, '
        if len(line) + len(item) > 96:
            parts.append(line.rstrip())
            line = '\t'
        line += item
    parts.append(line.rstrip())
    parts.append('}')
    parts.append('')
    parts.append('var decimalZeros = [...]rune{')
    line = '\t'
    for zero in zeros:
        item = f'0x{zero:X}, '
        if len(line) + len(item) > 96:
            parts.append(line.rstrip())
            line = '\t'
        line += item
    parts.append(line.rstrip())
    parts.append('}')
    return '\n'.join(parts) + '\n'


def main():
    PKG.mkdir(parents=True, exist_ok=True)
    (PKG / 'data.txt.gz').write_bytes(build_data())
    (PKG / 'names.txt.gz').write_bytes(build_names())
    (PKG / 'unidecode.bin.gz').write_bytes(build_unidecode_table())
    (PKG / 'html_entities.go').write_text(build_html_entities(), encoding='utf-8')


if __name__ == '__main__':
    main()
