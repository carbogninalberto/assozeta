"""Write the golden data that BE_GO/internal/idna2008 must reproduce.

Run with the pinned interpreter, from anywhere:

  python -I TESTS/adapters/go/idna2008_golden.py

The JSON file it writes (BE_GO/internal/idna2008/testdata/idna2008_golden.json) holds two parts, both
produced by idna.encode(label, strict=True, std3_rules=True) as urllib3 calls it:

  * "sweep": for every block of 4096 code points, the SHA-256 of the outcomes of the three labels
    chr(cp), 'a' + chr(cp) and chr(cp) + 'a' for every code point of the block in increasing order
    (surrogates are skipped), where an outcome is the A-label text or "!" for any exception. The
    outcomes are joined with "\\n" before hashing. The Go test recomputes the same hash, so the whole
    code point space is covered without storing every outcome.

  * "cases": hand-written labels with their full outcome, one per rule of idna.core.check_label plus
    the length limits, the A-label forms and the label splitting.
"""
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

import idna
from idna import idnadata

OUTPUT = Path(__file__).resolve().parents[3] / 'BE_GO/internal/idna2008/testdata/idna2008_golden.json'
BLOCK_SIZE = 4096
MAX_CODE_POINT = 0x110000


def outcome(label):
    """The A-label text for a label, or "!" when Python raises for it."""
    try:
        return idna.encode(label, strict=True, std3_rules=True).decode('ascii')
    except BaseException:
        return '!'


CASES = [
    # Plain labels and the ASCII case: an ASCII label is validated lowercased and returned as it is.
    'a', 'ab', 'abc', 'hello', 'a1', '1a', '123', '1-2', 'a-b', 'ab-cd', 'aA', 'HELLO', 'ABC',
    'xn--bcher-kva', 'xn--BCHER-KVA', 'XN--BCHER-KVA', 'xn--bbk', 'xn---bbk',
    'xn--', 'xn---', 'xn--a', 'xn--abc', 'xn--0', 'xn--a-ecp', 'xn--a-', 'xn--a-b', 'xn--zca',
    'xn--tda', 'xn--9ca', 'xn--4ca', 'xn--mgbh0fb',
    # Hyphen positions.
    '-a', 'a-', '-', '--', 'a--b', 'ab--cd', 'abc--', 'a---b', '1--2', 'a--', 'ab--', 'a-b-c',
    # Leading combining marks and NFC failures.
    '\u0301', '\u0301a', '\u0300a', '\u094d\u0915', 'e\u0301', 'a\u0308', 'n\u0303', 'q\u0307\u0323',
    '\u1e0a\u0323', 'a\u0323\u0302', '\u1100\u1161', '\u212b', '\u2126', '\u0344', 'e\u0301a',
    '\u00e9', '\u00c9', '\u00f1', '\uac00', '\uac01', '\u00e5',
    # ZERO WIDTH JOINER and ZERO WIDTH NON-JOINER.
    '\u0915\u094d\u200d', '\u0915\u094d\u200c', '\u0915\u200d', '\u0915\u200c', '\u200d', '\u200c',
    '\u0628\u200c\u0628', '\u0628\u200c\u0644', '\u0627\u200c\u0628', '\u0628\u200c\u0627',
    '\u0628\u200c\u064b\u0628', '\u0628\u200c\u064b\u0627', '\u0628\u064b\u200c\u0628',
    '\u0640\u200c\u0628', '\u0628\u0640\u200c\u0628', '\u0628\u0640\u200d', '\u0627\u200d',
    '\u0631\u200c\u0631', '\u0631\u200d', '\u05d0\u200d', '\u0e01\u0e3a\u200c', '\u0e01\u0e3a\u200d',
    # MIDDLE DOT.
    'l\u00b7l', 'al\u00b7l', 'l\u00b7l\u00b7l', 'a\u00b7b', '\u00b7ll', 'll\u00b7', 'l\u00b7',
    'L\u00b7L', '\u00b7', 'l\u00b7ll',
    # GREEK LOWER NUMERAL SIGN (KERAIA).
    '\u03b1\u0375\u03b2', '\u0375\u03b1', '\u03b1\u0375', '\u03b1\u0375a', '\u0375', '\u0375\u03b1\u03b2',
    # HEBREW GERESH and GERSHAYIM.
    '\u05d0\u05f3', '\u05d0\u05f4', '\u05d0\u05f3\u05d1', '\u05f3\u05d0', '\u05f4\u05d0', 'a\u05f3',
    '\u05d0\u05f3a', '\u05d0\u05f4\u05d1',
    # KATAKANA MIDDLE DOT.
    '\u30a2\u30fb\u30a4', '\u30fb', '\u30fb\u30fb', '\u30a2\u30fb', '\u30fb\u30a2', 'a\u30fbb',
    '\u4e00\u30fb\u4e8c', '\u3042\u30fb\u3044', '\u30fb\u3042', '\u30a2\u30fb\u30fb\u30a4',
    # ARABIC-INDIC and EXTENDED ARABIC-INDIC DIGITS.
    '\u0660\u0661', '\u06f0\u06f1', '\u0660\u06f0', '\u06f0\u0660', '\u0660', '\u06f0', '\u06f0a',
    '\u0627\u0660\u0661', '\u0627\u06f0\u06f1', '\u0627\u0660\u06f0', '\u0627\u0660\u0628',
    '\u0628\u06f1', '\u06f0\u0660\u0661', '\u0660a', '\u06f0\u0627',
    # The Bidi Rule.
    '\u05d0\u05d1', '\u05d01', '\u05d01\u05d0', '\u05d0\u05d1\u05d2', '\u05d0\u05b8', '\u05d0\u05b8\u05d1',
    '\u05d01\u05b8', '\u05d0\u02b9', '\u05d0a', 'a\u05d0', 'a\u0627', '\u0627\u06601', '\u06271',
    '\u05d0\u0660\u0031', '\u05d0\u05be', '\u0301\u05d0', '\u05d0\u0301', '\u0660\u05d0',
    '\u05d0\u05d1\u0031', '\u05d0 \u05d1', 'a\u0301\u05d0',
    # Characters from a Unicode edition newer than the interpreter's unicodedata: idnadata accepts
    # them, but check_bidi and _combining_class report the missing properties.
    '\u05c8', '\u05c8a', 'a\u05c8', '\u05c8\u200d', '\u05c8\u200c', '\u0915\u200d\u05c8',
    # Case, sharp s, final sigma, upper case letters that IDNA 2008 disallows.
    '\u00df', 'fa\u00df', 'stra\u00dfe', 'STRASSE', 'Stra\u00dfe', '\u03c2', '\u03c3', '\u03a3',
    '\u00dc', 'A\u00fc', 'B\u00fccher', 'b\u00fccher', '\u00c4', '\u00e4', 'a\u00c4',
    # Emoji, full width letters and the other label separators (not split when strict).
    '\U0001f600', 'a\U0001f600', '\U0001f600a', '\uff41\uff42\uff43', '\uff21\uff22\uff23',
    'a\u3002b', 'a\uff0eb', 'a\uff61b', '\u3002', '\uff0e', '\uff61', 'a\u3002', '\u3002a',
    # Splitting: only U+002E separates labels, a trailing dot is kept, empty labels are refused.
    '', '.', '..', '...', 'a.', '.a', 'a.b', 'a.b.', 'a..b', 'a.b..c', 'a.b.c.d', 'a b', 'a.b c',
    '\u00fc.a', 'a.\u00fc', '\u00fc.\u00fc', 'a.\u00fc.', 'xn--tda.a', 'a.xn--tda', 'A.B', 'a.B.',
    # Length limits: 63/64 octets for a label, 253/254 for the domain, 1024 code points for the input.
    'a' * 62, 'a' * 63, 'a' * 64, 'a' * 65, '\u00fc' * 56, '\u00fc' * 57, '\u00fc' * 58,
    'a' + '\u00fc' * 55, 'a' + '\u00fc' * 56, '-' + 'a' * 62, 'a' * 62 + '-', 'a' * 63 + '.a',
    'a' * 63 + '.' + 'a' * 63 + '.' + 'a' * 63 + '.' + 'a' * 61,
    'a' * 63 + '.' + 'a' * 63 + '.' + 'a' * 63 + '.' + 'a' * 62,
    'a' * 254, 'a' * 255, 'a' * 1023, 'a' * 1024, 'a' * 1025,
    'a.' * 511 + 'a', 'a.' * 512, 'a.' * 512 + 'a', '\u00fc.' * 40,
    # Assorted rejects and accepts.
    'a!', 'a_', 'a ', 'a@', 'a/', 'a:', 'a\\', 'a~', 'a#', 'a%', 'a&', 'a*', 'a=', 'a+', 'a,',
    'a;', 'a<', 'a>', 'a?', 'a[', 'a]', 'a^', 'a`', 'a{', 'a|', 'a}', 'a"', "a'", 'a(', 'a)',
    'a\t', 'a\n', 'a\x00', 'a\x7f', '\u00ad', '\u200b', '\ufeff', '\u0130', '\u0131', '\u00b5',
    'a\u0300b', 'a\u0300\u0301', 'e\u0302', '\u1f00', '\u1f71', '\u00e0', '\u00c0', '\u0153',
    'nc', '\u0153\u0301', 'x\u0307', '\u1e9b\u0323', '\u0f73', '\u0f71\u0f72', '\u0f73\u0f72',
    # Supplementary-plane starters followed by combining marks: NFC has to leave them alone, which a
    # 16-bit truncation of the starter would not (U+30064 U+0327 is not U+1E11).
    '\U00030064\u0327', '\U00010052\u0323\uac00', '\U00020043\u05b8\u030c', '\U0003014d\u0611\u0301',
    '\U00010064\u0327', '\U0003004f\u0323\u0308', '\U0001f600\u0301', '\U0002f800\u0327',
    '\U0001d160', '\U0002f800', '\U0001d400\u0301',
    'ii', 'i\u0307', 'J\u030c', 'j\u030c', '\u01f0', 's\u0323\u0307', '\u1e69', '\u0041\u030a',
    '\u0061\u030a', '\u00e9\u0301', '\u00e9\u0316', 'a\u0316\u0301', 'a\u0301\u0316',
    # A few multi-label inputs mixing valid and invalid labels.
    'a.\u00df', '\u00df.a', '\u00fc.\u00df.\u00e9', 'a.-b', 'a.b-', 'a.' + 'b' * 64,
    'xn--tda.xn--zca', 'a.xn---bbk', 'xn---bbk.a', 'a.' + '\u05d0' + 'a',
]


def sweep_blocks():
    blocks = []
    for start in range(0, MAX_CODE_POINT, BLOCK_SIZE):
        outcomes = []
        for cp in range(start, min(start + BLOCK_SIZE, MAX_CODE_POINT)):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            character = chr(cp)
            outcomes.append(outcome(character))
            outcomes.append(outcome('a' + character))
            outcomes.append(outcome(character + 'a'))
        blocks.append({
            'start': start,
            'sha256': hashlib.sha256('\n'.join(outcomes).encode('utf-8')).hexdigest(),
        })
    return blocks


def main():
    cases = []
    seen = set()
    for label in CASES:
        if label in seen:
            raise SystemExit(f'duplicate case {label!r}')
        seen.add(label)
        cases.append({'label': label, 'outcome': outcome(label)})
    if len(cases) < 250:
        raise SystemExit(f'only {len(cases)} cases')

    payload = {
        'generated_by': 'TESTS/adapters/go/idna2008_golden.py',
        'python': sys.version.split()[0],
        'unicode': unicodedata.unidata_version,
        'idna': '3.20',
        'idnadata': idnadata.__version__,
        'block_size': BLOCK_SIZE,
        'sweep': sweep_blocks(),
        'cases': cases,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('w', encoding='ascii') as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=1)
        handle.write('\n')
    errors = sum(1 for case in cases if case['outcome'] == '!')
    print(f'wrote {OUTPUT} with {len(payload["sweep"])} sweep blocks and {len(cases)} cases '
          f'({errors} of them errors)')


if __name__ == '__main__':
    main()
