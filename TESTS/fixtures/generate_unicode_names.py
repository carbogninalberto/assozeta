"""Write the Unicode name data of BE_GO/internal/pyunicodedata.

CPython 3.13's ``unicodedata.lookup(name)`` (Unicode 15.1.0) is the function
reportlab's ``<unichar name="..."/>`` calls. Two files come from the pinned
interpreter:

- ``names.txt.gz``: every name ``unicodedata.name`` returns, with its code
  point, and the algorithmic ``PREFIX-HEX`` families (``CJK UNIFIED
  IDEOGRAPH-``, ``TANGUT IDEOGRAPH-``) as ranges; name aliases and named
  sequences come from the UCD files of the same version, which this script
  downloads;
- ``testdata/lookup.json``: a probe corpus of names with Python's answer.

Run with the project interpreter:
``python -I TESTS/fixtures/generate_unicode_names.py`` (``--check`` compares
instead of writing).
"""
import gzip
import json
import random
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'BE_GO/internal/pyunicodedata'
DATA = PACKAGE / 'names.txt.gz'
CORPUS = PACKAGE / 'testdata/lookup.json'
CACHE = ROOT / 'tmp/ucd-15.1.0'
VERSION = '15.1.0'
UCD = 'https://www.unicode.org/Public/%s/ucd/' % VERSION

# The prefixes CPython 3.13 gives algorithmic names to besides Hangul
# syllables (Tools/unicode/makeunicodedata.py, derived_name_range_names).
# Hangul names are enumerated with the other names from unicodedata.name.
ALGORITHMIC = ('CJK UNIFIED IDEOGRAPH-', 'TANGUT IDEOGRAPH-')
MAX_NAME_LENGTH = 256
SEED = 20261117


def ucd(name):
    """The text of a UCD file of VERSION, downloaded once into tmp/."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.is_file():
        with urllib.request.urlopen(UCD + name, timeout=120) as response:
            path.write_bytes(response.read())
    text = path.read_text(encoding='utf-8')
    first = text.splitlines()[0]
    if VERSION not in first:
        raise SystemExit('%s is not the %s file: %r' % (name, VERSION, first))
    return text


def records(text):
    """The semicolon fields of a UCD file, comments and blank lines removed."""
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            yield [field.strip() for field in line.split(';')]


def lookup(name):
    """unicodedata.lookup(name), or None on KeyError."""
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return None


def algorithmic_ranges(prefix):
    """The contiguous ranges of code points CPython names prefix + '%04X'."""
    ranges = []
    for code in range(0x110000):
        name = '%s%04X' % (prefix, code)
        found = lookup(name)
        if found is None:
            continue
        if found != chr(code):
            raise SystemExit('%r gives %r, not %r' % (name, found, chr(code)))
        if ranges and ranges[-1][1] + 1 == code:
            ranges[-1][1] = code
        else:
            ranges.append([code, code])
    return [tuple(item) for item in ranges]


def names_data():
    """The names of the data file: (ranges by prefix, name -> value)."""
    ranges = {prefix: algorithmic_ranges(prefix) for prefix in ALGORITHMIC}
    excluded = set()
    for spans in ranges.values():
        for first, last in spans:
            excluded.update(range(first, last + 1))
    names = {}
    for code in range(0x110000):
        name = unicodedata.name(chr(code), '')
        if not name or code in excluded:
            continue
        if not name.isascii() or name != name.upper():
            raise SystemExit('%r is not an upper-case ASCII name' % name)
        if lookup(name) != chr(code):
            raise SystemExit('%r does not look up to %r' % (name, chr(code)))
        if name in names:
            raise SystemExit('%r names both %r and %r' % (name, names[name], chr(code)))
        names[name] = chr(code)

    alias_points = {}
    for field, name, _kind in records(ucd('NameAliases.txt')):
        alias_points.setdefault(name, set()).add(int(field, 16))
    for name, points in alias_points.items():
        value = lookup(name)
        if value is None or len(value) != 1 or ord(value) not in points:
            raise SystemExit('alias %r gives %r, the file has %s' % (
                name, value, ' '.join('%04X' % point for point in sorted(points))))
        if name in names:
            raise SystemExit('%r is both a name and an alias' % name)
        names[name] = value

    sequences = {}
    for name, fields in records(ucd('NamedSequences.txt')):
        sequence = ''.join(chr(int(field, 16)) for field in fields.split())
        found = lookup(name)
        if found != sequence:
            raise SystemExit('named sequence %r gives %r, the file has %r' % (name, found, sequence))
        if name in names:
            raise SystemExit('%r is both a name and a named sequence' % name)
        sequences[name] = sequence
    names.update(sequences)
    return ranges, names, sorted(alias_points), sequences


def data_text(ranges, names):
    lines = ['# pyunicodedata %s' % VERSION]
    for prefix in ALGORITHMIC:
        for first, last in ranges[prefix]:
            lines.append('A\t%s\t%X\t%X' % (prefix, first, last))
    for name in sorted(names):
        lines.append('N\t%s\t%s' % (name, ' '.join('%04X' % ord(char) for char in names[name])))
    return '\n'.join(lines) + '\n'


def random_case(rng, name):
    return ''.join(char.lower() if rng.random() < 0.5 else char.upper() if char.isalpha() else char for char in name)


def corpus(ranges, names, aliases, sequences):
    """The probe corpus: names with Python's answer (None for KeyError)."""
    rng = random.Random(SEED)
    cases, seen = [], set()

    def add(name):
        if name in seen:
            return
        seen.add(name)
        cases.append({'name': name, 'value': lookup(name)})

    real = sorted(names)
    hangul = [name for name in real if name.startswith('HANGUL SYLLABLE ')]
    other = [name for name in real if not name.startswith('HANGUL SYLLABLE ')]

    for name in ['', ' ', '\t', '\n', '\x00', 'a', 'A', '0', '-', 'EURO SIGN', 'euro sign']:
        add(name)
    # The names the paragraph fixtures and goldens use.
    for name in ['LATIN SMALL LETTER A', 'LATIN SMALL LETTER A WITH GRAVE', 'BLACK HEART SUIT', 'BYTE ORDER MARK',
                 'HANGUL SYLLABLE GAG', 'CJK UNIFIED IDEOGRAPH-4E00', 'LATIN CAPITAL LETTER A WITH MACRON AND GRAVE']:
        add(name)

    # Real names, the natural case and a random one.
    for name in rng.sample(other, 1000) + rng.sample(hangul, 200):
        add(name)
        add(random_case(rng, name))

    # Aliases of every type (correction, control, alternate, figment,
    # abbreviation) and named sequences, upper and lower case.
    for name in aliases:
        add(name)
        add(name.lower())
        add(random_case(rng, name))
    for name in sorted(sequences):
        add(name)
        add(name.lower())

    # The algorithmic families: range edges, just outside and the forms the
    # hex parser rejects or accepts in another case.
    for prefix in ALGORITHMIC:
        for first, last in ranges[prefix]:
            for code in (first, first + 1, last - 1, last, first - 1, last + 1):
                if 0 <= code <= 0x10FFFF:
                    add('%s%04X' % (prefix, code))
            add(random_case(rng, '%s%04X' % (prefix, first)))
            add('%s%04x' % (prefix, last))
            add(('%s%04X' % (prefix, first))[len(prefix):].lower())
            add(prefix + '0%04X' % first)
            add(prefix + '00%04X' % first)
            add(prefix + '%06X' % first)
            add(prefix + ('%X' % first)[-3:])
            add(prefix + '%07X' % first)
            add(prefix + 'xyz')
            add(prefix + ' %04X' % first)
            add(prefix)
            add(prefix + ' ')
    for code in (0x4DC0, 0x9FFD, 0x1FFFF, 0x3134B):
        add('CJK UNIFIED IDEOGRAPH-%04X' % code)
    for name in ['CJK COMPATIBILITY IDEOGRAPH-F900', 'CJK COMPATIBILITY IDEOGRAPH-2F800',
                 'KHITAN SMALL SCRIPT CHARACTER-18B00', 'KHITAN SMALL SCRIPT CHARACTER-18CD5',
                 'NUSHU CHARACTER-1B170', 'NUSHU CHARACTER-1B2FB',
                 'TANGUT COMPONENT-001', 'TANGUT COMPONENT-768']:
        add(name)
        add(name.lower())

    # Hangul syllables: the first, the last, every L/V/T edge and malformed
    # spellings.
    for code in (0xAC00, 0xAC01, 0xAC1C, 0xD7A3, 0xD7A2, 0xB098, 0xC544):
        add('HANGUL SYLLABLE ' + unicodedata.name(chr(code))[16:])
    for name in ['HANGUL SYLLABLE ', 'HANGUL SYLLABLE', 'HANGUL SYLLABLE  G', 'HANGUL SYLLABLE G A',
                 'HANGUL SYLLABLE GAGX', 'HANGUL SYLLABLE GX', 'hangul syllable gag', 'Hangul Syllable Gag']:
        add(name)
    for name in rng.sample(hangul, 100):
        add(random_case(rng, name))
        add(name + ' ')
        add(name.replace(' ', '  ', 1))

    # Spacing around and inside names.
    for name in ['EURO SIGN', 'LATIN SMALL LETTER A', 'HANGUL SYLLABLE GAG', 'CJK UNIFIED IDEOGRAPH-4E00']:
        add(' ' + name)
        add(name + ' ')
        add('  ' + name)
        add(name.replace(' ', '  '))
        add(name.replace(' ', '\t'))
        add(name.replace(' ', '\n'))
        add(name.replace(' ', '\r'))
        add(name.replace(' ', '\f'))
        add(name.replace(' ', '\v'))
        add('\u00a0' + name)
        add(name + '\u00a0')
        add(name + '\x00')
        add('\x00' + name)
        add(name[:len(name) // 2] + '\x00' + name[len(name) // 2:])
    add('EURO\x00SIGN')
    add('\x00' * 3)

    # Non-ASCII characters, including letters Unicode case folding would map to
    # ASCII ones (CPython only upper-cases ASCII bytes).
    for name in ['EURO SIGN', 'LATIN SMALL LETTER A', 'HANGUL SYLLABLE GAG', 'CJK UNIFIED IDEOGRAPH-4E00']:
        add(name + '\u00e9')
        add('\u00e9' + name)
        add(name + '\u20ac')
        add(name + '\U0001F600')
        add(name.replace(' ', '\u00e9'))
    add('\u0130')
    add('\u00c9URO SIGN')
    add('\u017fATIN SMALL LETTER A')
    add('LATIN SMALL LETTER \u212a')
    add('LATIN SMALL LETTER \u017f')

    # Names at and beyond the 256-byte limit of CPython's lookup.
    for length in (MAX_NAME_LENGTH - 1, MAX_NAME_LENGTH, MAX_NAME_LENGTH + 1, 1000):
        add('A' * length)
    add('LATIN SMALL LETTER ' + 'A' * 240)
    add('LATIN SMALL LETTER ' + 'A' * 250)
    add('EURO SIGN' + 'A' * 300)
    add('CJK UNIFIED IDEOGRAPH-4E00' + 'A' * 300)
    add('\u00e9' * 128)
    add('\u00e9' * 129)

    # Random junk and mutations of real names (a fixed seed).
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -'
    for _ in range(800):
        add(''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 50))))
    for _ in range(300):
        name = list(rng.choice(real))
        for _ in range(rng.randint(1, 3)):
            action = rng.randrange(4)
            if action == 0 and name:
                del name[rng.randrange(len(name))]
            elif action == 1:
                name.insert(rng.randrange(len(name) + 1), rng.choice(alphabet))
            elif action == 2 and name:
                name[rng.randrange(len(name))] = rng.choice(alphabet)
            elif len(name) > 1:
                index = rng.randrange(len(name) - 1)
                name[index], name[index + 1] = name[index + 1], name[index]
        add(''.join(name))

    if len(cases) < 3000:
        raise SystemExit('the corpus has only %d names' % len(cases))
    if not any(case['value'] is None for case in cases) or not any(case['value'] is not None for case in cases):
        raise SystemExit('the corpus is one-sided')
    return json.dumps({'unidata_version': VERSION, 'cases': cases}, ensure_ascii=True, separators=(',', ':')) + '\n'


def main():
    ranges, names, aliases, sequences = names_data()
    text = data_text(ranges, names)
    packed = gzip.compress(text.encode('ascii'), 9, mtime=0)
    if len(packed) > 400_000:
        raise SystemExit('names.txt.gz is %d bytes' % len(packed))
    outputs = {DATA: packed, CORPUS: corpus(ranges, names, aliases, sequences).encode('ascii')}
    if '--check' in sys.argv[1:]:
        stale = [str(path) for path, content in outputs.items()
                 if not path.is_file() or path.read_bytes() != content]
        if stale:
            raise SystemExit('stale: ' + ', '.join(stale))
        print('up to date')
        return
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print('%d names, %d aliases, %d sequences, %d corpus cases, %d bytes packed' % (
        len(names) - len(aliases) - len(sequences), len(aliases), len(sequences),
        outputs[CORPUS].count(b'"name"'), len(packed)))


if __name__ == '__main__':
    main()
