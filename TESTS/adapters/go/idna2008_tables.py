"""Generate the Go tables that BE_GO/internal/idna2008 needs to reproduce Python's idna.encode().

Run with the pinned interpreter, from anywhere:

  python -I TESTS/adapters/go/idna2008_tables.py

The file written here (BE_GO/internal/idna2008/tables.go) carries, as sorted inclusive code point
ranges:

  * the IDNA 2008 classes PVALID, CONTEXTJ and CONTEXTO plus the joining types and the five scripts
    that idna.core reads, taken from this interpreter's idna.idnadata;
  * every unicodedata property idna.core reads, taken from this interpreter's unicodedata: the bidi
    class of every code point (the empty class matters: it is what makes check_bidi report an
    unknown direction), whether the general category starts with M, whether the canonical combining
    class is 9 (the class idna.core calls a virama), and whether unicodedata.name() raises for the
    code point;
  * the NFC data check_nfc needs: the canonical combining class of every code point that has one,
    the full canonical decomposition of every code point that has one (Hangul syllables are
    decomposed arithmetically instead), and the canonical composition pairs outside Hangul.

NFC is tabulated here rather than taken from golang.org/x/text/unicode/norm because that package,
whose tables come from an older Unicode edition, composes a supplementary-plane starter as if only
its low 16 bits were left: NFC("\\U00030064\\u0327") has to stay U+30064 U+0327 but x/text/norm
returns U+1E11. The full canonical decomposition plus composition is small, exact, and comes from
the same unicodedata as the interpreter's normalize().
"""
import subprocess
import unicodedata
from pathlib import Path

from idna import idnadata

OUTPUT = Path(__file__).resolve().parents[3] / 'BE_GO/internal/idna2008/tables.go'
MAX_CODE_POINT = 0x110000

BIDI_CLASSES = {
    '': 'bidiUnknown',
    'L': 'bidiL',
    'R': 'bidiR',
    'AL': 'bidiAL',
    'AN': 'bidiAN',
    'EN': 'bidiEN',
    'ES': 'bidiES',
    'CS': 'bidiCS',
    'ET': 'bidiET',
    'ON': 'bidiON',
    'BN': 'bidiBN',
    'NSM': 'bidiNSM',
}
BIDI_OTHER = 'bidiOther'
BIDI_ORDER = ('L', 'R', 'AL', 'AN', 'EN', 'ES', 'CS', 'ET', 'ON', 'BN', 'NSM')
JOINING_TYPES = {'C': 'joiningC', 'D': 'joiningD', 'L': 'joiningL', 'R': 'joiningR', 'T': 'joiningT'}
JOINING_ORDER = ('C', 'D', 'L', 'R', 'T')
SCRIPTS = ('Greek', 'Hebrew', 'Hiragana', 'Katakana', 'Han')
CODEPOINT_CLASSES = ('PVALID', 'CONTEXTJ', 'CONTEXTO')
HANGUL_FIRST = 0xAC00
HANGUL_COUNT = 11172


def merge(ranges):
    """Sort and merge overlapping or adjacent inclusive ranges."""
    merged = []
    for low, high in sorted(ranges):
        assert low <= high, (low, high)
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def decode_idnadata(encoded):
    """idnadata stores (start << 32 | end) with an exclusive end; return inclusive ranges."""
    return merge([(run >> 32, (run & 0xFFFFFFFF) - 1) for run in encoded])


def transitions(values):
    """Collapse a per code point value list into (first, last, value) inclusive runs."""
    runs = []
    start = 0
    previous = values[0]
    for code in range(1, len(values)):
        if values[code] != previous:
            runs.append((start, code - 1, previous))
            start = code
            previous = values[code]
    runs.append((start, len(values) - 1, previous))
    return runs


def contains(table, code):
    """intranges_contain on an inclusive range list, for the self-checks below."""
    low, high = 0, len(table) - 1
    while low <= high:
        middle = (low + high) // 2
        if code < table[middle][0]:
            high = middle - 1
        elif code > table[middle][1]:
            low = middle + 1
        else:
            return True
    return False


def go_runes(name, comment, table):
    lines = [f'// {comment}', f'var {name} = []runeRange{{']
    lines += [f'\t{{0x{low:X}, 0x{high:X}}},' for low, high in table]
    return lines + ['}', '']


def go_bidi(comment, runs):
    lines = [f'// {comment}', 'var bidiTable = []bidiRange{']
    lines += [f'\t{{0x{low:X}, 0x{high:X}, {BIDI_CLASSES.get(value, BIDI_OTHER)}}},'
              for low, high, value in runs]
    return lines + ['}', '']


def go_joining(comment, runs):
    lines = [f'// {comment}', 'var joiningTable = []joiningRange{']
    lines += [f'\t{{0x{low:X}, 0x{high:X}, {JOINING_TYPES[value]}}},' for low, high, value in runs]
    return lines + ['}', '']


def go_combining(comment, runs):
    lines = [f'// {comment}', 'var combiningClasses = []combiningRange{']
    lines += [f'\t{{0x{low:X}, 0x{high:X}, {value}}},' for low, high, value in runs]
    return lines + ['}', '']


def go_escape(text):
    """Escape a decomposed rune sequence for a Go string literal."""
    escaped = []
    for character in text:
        code = ord(character)
        if character == '"' or character == '\\':
            escaped.append('\\' + character)
        elif code < 0x80:
            escaped.append(character)
        elif code < 0x10000:
            escaped.append(f'\\u{code:04X}')
        else:
            escaped.append(f'\\U{code:08X}')
    return ''.join(escaped)


def go_decompositions(comment, table):
    lines = [f'// {comment}', 'var nfcDecompositions = map[rune]string{']
    lines += [f'\t0x{code:X}: "{go_escape(text)}",' for code, text in sorted(table.items())]
    return lines + ['}', '']


def go_compositions(comment, table):
    lines = [f'// {comment}', 'var nfcCompositions = map[uint64]rune{']
    lines += [f'\t{key:#x}: 0x{composed:X},' for key, composed in sorted(table.items())]
    return lines + ['}', '']


def nfc_tables():
    """The canonical decomposition, combining class and composition data of this unicodedata."""
    decompositions = {}
    compositions = {}
    combining = []
    for code in range(MAX_CODE_POINT):
        combining_class = unicodedata.combining(chr(code))
        if not combining_class:
            continue
        if combining and combining[-1][1] + 1 == code and combining[-1][2] == combining_class:
            combining[-1] = (combining[-1][0], code, combining_class)
        else:
            combining.append((code, code, combining_class))

    for code in range(MAX_CODE_POINT):
        if HANGUL_FIRST <= code < HANGUL_FIRST + HANGUL_COUNT:
            continue
        decomposition = unicodedata.decomposition(chr(code))
        if not decomposition or decomposition.startswith('<'):
            continue
        normalized = unicodedata.normalize('NFD', chr(code))
        if normalized == chr(code):
            continue
        decompositions[code] = normalized
        if len(normalized) < 2:
            continue
        # The last step of composing the decomposition has to give the character back; it does not
        # for the composition exclusions, which are therefore absent from the table.
        first = unicodedata.normalize('NFC', normalized[:-1])
        if len(first) != 1:
            continue
        if unicodedata.normalize('NFC', first + normalized[-1]) == chr(code):
            key = (ord(first) << 32) | ord(normalized[-1])
            assert key not in compositions or compositions[key] == code, code
            compositions[key] = code

    # Self-checks: the merged classes and the decomposition table rebuild this unicodedata.
    index = 0
    for code in range(MAX_CODE_POINT):
        while index < len(combining) and combining[index][1] < code:
            index += 1
        found = 0
        if index < len(combining) and combining[index][0] <= code <= combining[index][1]:
            found = combining[index][2]
        assert found == unicodedata.combining(chr(code)), code
    for code, text in decompositions.items():
        assert unicodedata.normalize('NFC', text) == unicodedata.normalize('NFC', chr(code)), code
    return combining, decompositions, compositions


def render():
    classes = {name: decode_idnadata(idnadata.codepoint_classes[name]) for name in CODEPOINT_CLASSES}
    scripts = {name: decode_idnadata(idnadata.scripts[name]) for name in SCRIPTS}
    joining = {name: decode_idnadata(idnadata.joining_types[name]) for name in JOINING_ORDER}

    # idna.core._joining_type returns the first joining type whose ranges contain the code point.
    joining_values = []
    bidi_values = []
    marks = []
    viramas = []
    nameless = []
    for code in range(MAX_CODE_POINT):
        character = chr(code)
        joining_values.append(next((name for name in JOINING_ORDER if contains(joining[name], code)), None))
        bidi_values.append(unicodedata.bidirectional(character))
        if unicodedata.category(character)[0] == 'M':
            marks.append((code, code))
        if unicodedata.combining(character) == 9:
            viramas.append((code, code))
        if not unicodedata.name(character, ''):
            nameless.append((code, code))

    joining_runs = transitions(joining_values)
    bidi_runs = transitions(bidi_values)
    combining_runs, decompositions, compositions = nfc_tables()

    # Self-checks on the decoding and on the properties read above.
    for name in CODEPOINT_CLASSES:
        assert classes[name] == merge([(run >> 32, (run & 0xFFFFFFFF) - 1)
                                       for run in idnadata.codepoint_classes[name]]), name
    for name in SCRIPTS:
        assert scripts[name] == merge([(run >> 32, (run & 0xFFFFFFFF) - 1)
                                       for run in idnadata.scripts[name]]), name
    overlapping = sum(high - low + 1 for table in joining.values() for low, high in table)
    assert overlapping == sum(high - low + 1 for low, high in
                              merge([(low, high) for table in joining.values() for low, high in table]))
    for low, high, value in joining_runs:
        for code in (low, high):
            assert next((name for name in JOINING_ORDER if contains(joining[name], code)), None) == value
    for low, high, value in bidi_runs:
        for code in (low, high):
            assert unicodedata.bidirectional(chr(code)) == value

    lines = [
        '// Code generated by TESTS/adapters/go/idna2008_tables.py; DO NOT EDIT.',
        '',
        'package idna2008',
        '',
        "// unicodeVersion is the version of the interpreter's unicodedata.",
        f'const unicodeVersion = "{unicodedata.unidata_version}"',
        '',
        "// idnadataVersion is the version of the idna package's idnadata tables.",
        f'const idnadataVersion = "{idnadata.__version__}"',
        '',
        '// runeRange is an inclusive range of code points.',
        'type runeRange struct {',
        '\tlo, hi rune',
        '}',
        '',
        '// bidiRange is an inclusive range of code points with one bidi class.',
        'type bidiRange struct {',
        '\tlo, hi rune',
        '\tclass  bidiClass',
        '}',
        '',
        '// joiningRange is an inclusive range of code points with one joining type.',
        'type joiningRange struct {',
        '\tlo, hi rune',
        '\tjoin   joiningType',
        '}',
        '',
        '// combiningRange is an inclusive range of code points with one combining class.',
        'type combiningRange struct {',
        '\tlo, hi rune',
        '\tclass  uint8',
        '}',
        '',
        '// bidiClass is unicodedata.bidirectional reduced to the values check_bidi tells apart.',
        'type bidiClass uint8',
        '',
        'const (',
        '\tbidiUnknown bidiClass = iota // unicodedata.bidirectional() == ""',
    ]
    for name in BIDI_ORDER:
        lines.append(f'\t{BIDI_CLASSES[name]} // {name}')
    lines += [
        f'\t{BIDI_OTHER} // B, S, WS, LRE, RLE, LRO, RLO, PDF, LRI, RLI, FSI, PDI',
        ')',
        '',
        '// joiningType is idnadata.joining_types.',
        'type joiningType uint8',
        '',
        'const (',
        '\tjoiningNone joiningType = iota // not listed in idnadata.joining_types',
    ]
    for name in JOINING_ORDER:
        lines.append(f'\t{JOINING_TYPES[name]} // {name}')
    lines += [')', '']

    for name in CODEPOINT_CLASSES:
        lines += go_runes(name.lower(), f'idnadata.codepoint_classes[{name!r}].', classes[name])
    lines += go_joining('idnadata.joining_types.', [run for run in joining_runs if run[2]])
    for name in SCRIPTS:
        lines += go_runes(f'script{name}', f'idnadata.scripts[{name!r}].', scripts[name])
    lines += go_bidi('unicodedata.bidirectional().', bidi_runs)
    lines += go_runes('markRanges', "Code points whose unicodedata general category starts with 'M'.",
                      merge(marks))
    lines += go_runes('viramaRanges', 'Code points whose canonical combining class is 9.',
                      merge(viramas))
    lines += go_runes('namelessRanges', 'Code points for which unicodedata.name() raises.',
                      merge(nameless))
    lines += go_combining('unicodedata.combining() for the code points whose class is not zero.',
                          combining_runs)
    lines += go_decompositions('unicodedata.normalize("NFD") for every code point whose canonical '
                              'decomposition is not itself; Hangul syllables are decomposed '
                              'arithmetically instead.', decompositions)
    lines += go_compositions('The canonical composition pairs outside Hangul, keyed by '
                             'uint64(first)<<32 | uint64(second).', compositions)
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    text = render()
    text = subprocess.run(['gofmt'], input=text, capture_output=True, text=True, check=True).stdout
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text)
    print(f'wrote {OUTPUT}')
