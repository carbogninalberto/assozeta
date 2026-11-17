#!/usr/bin/env python3
"""Writes the tables and golden corpora of the 'idna' codec for pycodecs.

Run with the reference CPython 3.13 interpreter, isolated and from anywhere:

    $PY -I testdata/gen_idna.py                # write the files
    $PY -I testdata/gen_idna.py --check        # regenerate in memory, fail on a diff
    $PY -I testdata/gen_idna.py --full DIR     # also write the full corpora to DIR

The committed corpora are a sample of the generated ones (every
SAMPLE_STRIDE-th case; the single code point tables stay whole, with the
nameprep errors of the escaped form "Invalid character '\\U...'" kept as
ranges). The Go tests read the full corpora instead when
PYCODECS_IDNA_CORPUS_DIR names the directory --full wrote.

Everything here comes from the running interpreter: the Unicode 3.2.0
normalization database (unicodedata.ucd_3_2_0), the RFC 3454 stringprep
tables, and CPython's own bytes.decode('idna') / str.encode('idna').

It writes four gzip files:

  idna_tables.json.gz                     the tables the Go port loads once
  testdata/corpus_idna_nfkc.json.gz       NFKC cases against ucd_3_2_0
  testdata/corpus_idna_nameprep.json.gz   nameprep cases (RFC 3491)
  testdata/corpus_idna_decode.json.gz     strict bytes.decode('idna') cases

The tables
-----------

nfkd
    For every code point 0..0x10FFFF the full NFKD of that one character
    under the 3.2.0 database, stored only when it differs from the
    character. "Full" means ucd_3_2_0.normalize('NFKD', chr(cp)): the Go
    port therefore needs no recursive decomposition. A character that is
    unassigned in 3.2.0 does not decompose (the normalization corrections
    of 3.2.0, e.g. U+2F868 -> U+2136A, are already in the result).

combining
    The CURRENT unicodedata.combining class, non-zero only. CPython's
    normalization code for the 3.2.0 database reorders and blocks with the
    process-wide combining table, not the 3.2.0 one; the generator checks
    this against ucd_3_2_0.normalize below (U+0859 has class 0 in 3.2.0 and
    220 now, and the 3.2.0 normalizer moves it before U+0301).

compose
    The pairs CPython composes. Candidates are the two-element canonical
    decompositions of the CURRENT unicodedata.decomposition whose first
    element has combining class 0; a candidate (a, b) -> c is kept exactly
    when ucd_3_2_0.normalize('NFC', chr(a)+chr(b)) == chr(c). The probe
    drops composition exclusions (e.g. U+0958, whose decomposition is
    present but which NFC never produces) and pairs whose first element is
    not in NFC form (the probe decomposes it first, so the pair is only
    kept if it is still used after recomposition). Hangul L+V pairs are
    candidates like any other; the (LV, T) pairs are not two-element
    decompositions, are verified directly (see hangul_pairs) and are
    composed algorithmically in Go.

b1, b2, prohibited, d1, d2, printable_prohibited
    The stringprep tables: in_table_b1, map_table_b2 (baked per character,
    only where it differs), the union of C.1.2/C.2.2/C.3..C.9 as ranges,
    D.1/D.2 as ranges, and the few prohibited characters whose Python repr
    is the character itself (the other reasons escape the code point).

The corpora
-----------

NFKC: every single code point (stored only when the result differs) plus
at least 200k random 1..8 code point strings from mixed pools (combining
marks of many classes, Hangul jamo and syllables, compatibility
characters, the 3.2.0 corrections, characters unassigned in 3.2.0,
post-3.2.0 composites, plain letters), every composition pair and every
Hangul syllable.

nameprep: every single code point (result only when it differs, or the
error) plus 100k random multi-character labels, including RTL/LTR mixes,
mapped-to-nothing characters and prohibited characters.

idna decode: at least 50k bytes.decode('idna') inputs, built from CPython's
str.encode('idna') output (plain, mixed case, trailing dot, several
labels), plus empty labels, non-ASCII bytes, broken punycode, labels that
do not round-trip, over-long labels, prohibited characters and bidi
violations. The same file carries ToASCII cases for the Go encoder.

The generator also contains a Python model of the Go implementation
(nfkc_model, nameprep_model), and fails if it disagrees with CPython on a
sample of the corpus: the design is checked before the Go port sees it.
"""

import argparse
import codecs
import gzip
import io
import json
import os
import random
import sys
import unicodedata

import stringprep
from encodings.punycode import punycode_encode

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TABLES = os.path.join(PKG, "idna_tables.json.gz")
NFKC_CORPUS = os.path.join(HERE, "corpus_idna_nfkc.json.gz")
NAMEPREP_CORPUS = os.path.join(HERE, "corpus_idna_nameprep.json.gz")
DECODE_CORPUS = os.path.join(HERE, "corpus_idna_decode.json.gz")

SEED = 20261117
MAXCP = 0x110000
UCD = unicodedata.ucd_3_2_0

NFKC_RANDOM = 210_000
NAMEPREP_RANDOM = 105_000
DECODE_RANDOM = 55_000
TOASCII_RANDOM = 2_000
MODEL_SAMPLE = 20_000
SAMPLE_STRIDE = 20

B1 = stringprep.b1_set
PROHIBITED_PREDICATES = (
    stringprep.in_table_c12, stringprep.in_table_c22, stringprep.in_table_c3,
    stringprep.in_table_c4, stringprep.in_table_c5, stringprep.in_table_c6,
    stringprep.in_table_c7, stringprep.in_table_c8, stringprep.in_table_c9,
)


def ranges(values):
    """[start, end] ranges covering the sorted code points."""
    out = []
    for value in values:
        if out and out[-1][1] + 1 == value:
            out[-1][1] = value
        else:
            out.append([value, value])
    return out


def code_points(text):
    """'cp cp ...' in upper-case hex (surrogates are just numbers)."""
    return " ".join("%X" % ord(ch) for ch in text)


def from_code_points(text):
    return "".join(chr(int(part, 16)) for part in text.split())


# ---------------------------------------------------------------------------
# tables


def hangul_pairs():
    """The Hangul LV and LVT compositions, each verified with CPython."""
    count = 0
    for lead in range(0x1100, 0x1113):
        for vowel in range(0x1161, 0x1176):
            lv = 0xAC00 + ((lead - 0x1100) * 21 + (vowel - 0x1161)) * 28
            assert UCD.normalize("NFC", chr(lead) + chr(vowel)) == chr(lv), (lead, vowel)
            count += 1
            for trail in range(0x11A8, 0x11C3):
                lvt = lv + (trail - 0x11A7)
                assert UCD.normalize("NFC", chr(lv) + chr(trail)) == chr(lvt), (lv, trail)
                count += 1
    return count


def build_tables():
    nfkd = []
    combining = []
    for cp in range(MAXCP):
        ch = chr(cp)
        decomposed = UCD.normalize("NFKD", ch)
        if decomposed != ch:
            nfkd.append([cp] + [ord(x) for x in decomposed])
        klass = unicodedata.combining(ch)
        if klass:
            combining.append([cp, klass])

    compose = []
    for cp in range(MAXCP):
        decomposition = unicodedata.decomposition(chr(cp))
        if not decomposition or decomposition.startswith("<"):
            continue
        parts = decomposition.split()
        if len(parts) != 2:
            continue
        first, second = int(parts[0], 16), int(parts[1], 16)
        if unicodedata.combining(chr(first)) != 0:
            continue
        if UCD.normalize("NFC", chr(first) + chr(second)) == chr(cp):
            compose.append([first, second, cp])

    b2 = []
    for cp in range(MAXCP):
        ch = chr(cp)
        mapped = stringprep.map_table_b2(ch)
        if mapped != ch:
            b2.append([cp] + [ord(x) for x in mapped])

    prohibited = [cp for cp in range(MAXCP)
                  if any(predicate(chr(cp)) for predicate in PROHIBITED_PREDICATES)]
    d1 = [cp for cp in range(MAXCP) if stringprep.in_table_d1(chr(cp))]
    d2 = [cp for cp in range(MAXCP) if stringprep.in_table_d2(chr(cp))]

    # Python's repr of a prohibited character escapes it unless the character
    # is printable in the CURRENT database. Only these sixteen are printable;
    # for the rest the reason is \xNN, \uNNNN or \UNNNNNNNN by code point.
    printable_prohibited = [cp for cp in prohibited
                            if repr(chr(cp)) == "'" + chr(cp) + "'"]
    for cp in printable_prohibited:
        assert "\x27" not in chr(cp) and "\x22" not in chr(cp)

    return {
        "version": UCD.unidata_version,
        "current_version": unicodedata.unidata_version,
        "nfkd": nfkd,
        "combining": combining,
        "compose": compose,
        "b1": ranges(sorted(B1)),
        "b2": b2,
        "prohibited": ranges(prohibited),
        "d1": ranges(d1),
        "d2": ranges(d2),
        "printable_prohibited": printable_prohibited,
    }


# ---------------------------------------------------------------------------
# the Python model of the Go implementation (checked against CPython)


class Tables:
    def __init__(self, raw):
        self.nfkd = {}
        for entry in raw["nfkd"]:
            self.nfkd[entry[0]] = entry[1:]
        self.combining = {entry[0]: entry[1] for entry in raw["combining"]}
        self.compose = {(entry[0], entry[1]): entry[2] for entry in raw["compose"]}
        self.b1 = raw["b1"]
        self.b2 = {}
        for entry in raw["b2"]:
            self.b2[entry[0]] = entry[1:]
        self.prohibited = raw["prohibited"]
        self.d1 = raw["d1"]
        self.d2 = raw["d2"]
        self.printable_prohibited = frozenset(raw["printable_prohibited"])


def in_ranges(table, cp):
    low, high = 0, len(table) - 1
    while low <= high:
        middle = (low + high) // 2
        start, end = table[middle]
        if cp < start:
            high = middle - 1
        elif cp > end:
            low = middle + 1
        else:
            return True
    return False


def hangul_compose(first, second):
    lead, vowel = ord(first), ord(second)
    if 0x1100 <= lead <= 0x1112 and 0x1161 <= vowel <= 0x1175:
        return chr(0xAC00 + ((lead - 0x1100) * 21 + (vowel - 0x1161)) * 28)
    if 0xAC00 <= lead <= 0xD7A3 and (lead - 0xAC00) % 28 == 0 and 0x11A8 <= vowel <= 0x11C2:
        return chr(lead + vowel - 0x11A7)
    return None


def nfkc_model(tables, text):
    decomposed = []
    for ch in text:
        decomposed.extend(tables.nfkd.get(ord(ch), [ord(ch)]))
    # Canonical ordering with the current combining classes: a stable
    # insertion sort inside each run of non-starters.
    for i in range(1, len(decomposed)):
        klass = tables.combining.get(decomposed[i], 0)
        if klass == 0:
            continue
        j = i
        while j > 0:
            previous = tables.combining.get(decomposed[j - 1], 0)
            if previous == 0 or previous <= klass:
                break
            decomposed[j - 1], decomposed[j] = decomposed[j], decomposed[j - 1]
            j -= 1
    # Canonical composition: CPython's sample algorithm composes each
    # character with the last starter when the last *uncomposed* character
    # has combining class 0 or a smaller class. A mark that was composed
    # into the starter no longer blocks later marks (so U+00DC U+0304 is
    # U+01D5 even though both marks have class 230).
    out = []
    starter = -1
    last_class = 0
    for cp in decomposed:
        klass = tables.combining.get(cp, 0)
        composed = None
        if starter >= 0 and (last_class == 0 or last_class < klass):
            composed = hangul_compose(chr(out[starter]), chr(cp))
            if composed is None:
                composed = tables.compose.get((out[starter], cp))
                if composed is not None:
                    composed = chr(composed)
        if composed is not None:
            out[starter] = ord(composed)
        else:
            out.append(cp)
            if klass == 0:
                starter = len(out) - 1
            last_class = klass
    return "".join(chr(cp) for cp in out)


def char_repr(cp):
    if cp in TABLES_MODEL.printable_prohibited:
        return "'" + chr(cp) + "'"
    if cp <= 0xFF:
        return "'\\x%02x'" % cp
    if cp <= 0xFFFF:
        return "'\\u%04x'" % cp
    return "'\\U%08x'" % cp


class NameprepError(Exception):
    def __init__(self, start, end, reason):
        self.start = start
        self.end = end
        self.reason = reason


def nameprep_model(tables, label):
    mapped = []
    for ch in label:
        cp = ord(ch)
        if in_ranges(tables.b1, cp):
            continue
        mapped.extend(tables.b2.get(cp, [cp]))
    normalized = nfkc_model(tables, "".join(chr(cp) for cp in mapped))
    for i, ch in enumerate(normalized):
        if in_ranges(tables.prohibited, ord(ch)):
            raise NameprepError(i, i + 1, "Invalid character " + char_repr(ord(ch)))
    rand_al = [in_ranges(tables.d1, ord(ch)) for ch in normalized]
    if any(rand_al):
        for i, ch in enumerate(normalized):
            if in_ranges(tables.d2, ord(ch)):
                raise NameprepError(i, i + 1, "Violation of BIDI requirement 2")
        if not rand_al[0]:
            raise NameprepError(0, 1, "Violation of BIDI requirement 3")
        if not rand_al[-1]:
            raise NameprepError(len(normalized) - 1, len(normalized),
                                "Violation of BIDI requirement 3")
    return normalized


# ---------------------------------------------------------------------------
# input pools

def utf16_surrogate_repr(cp):
    return cp


def pools(tables):
    rng = random.Random(SEED)
    combining = [entry[0] for entry in tables["combining"]]
    compatibility = [entry[0] for entry in tables["nfkd"] if not 0xAC00 <= entry[0] <= 0xD7A3]
    hangul_syllables = list(range(0xAC00, 0xD7A4))
    corrections = [0x2F868, 0x2F874, 0x2F91F, 0x2F95F, 0x2F9BF]
    unassigned = [cp for cp in range(MAXCP)
                  if UCD.category(chr(cp)) == "Cn" and unicodedata.category(chr(cp)) != "Cn"]
    late_composites = [entry[2] for entry in tables["compose"]
                       if UCD.category(chr(entry[2])) == "Cn"]
    letters = ([ord(c) for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"]
               + list(range(0x410, 0x430)) + list(range(0x3B1, 0x3CA))
               + list(range(0x5D0, 0x5EB)) + list(range(0x620, 0x64B))
               + list(range(0x4E00, 0x4E40)))
    return {
        "combining": combining,
        "hangul": list(range(0x1100, 0x1200)) + rng.sample(hangul_syllables, 512),
        "compatibility": compatibility,
        "corrections": corrections,
        "unassigned": unassigned,
        "late": late_composites * 8 + [part for entry in tables["compose"]
                                       if UCD.category(chr(entry[2])) == "Cn"
                                       for part in entry[:2]],
        "letters": letters,
        "b1": sorted(B1),
        "prohibited": [cp for start, end in tables["prohibited"]
                       for cp in (start, end)],
        "rtl": list(range(0x5D0, 0x5EB)) + list(range(0x620, 0x64B)) + [0xFB1D, 0xFB50, 0x10800],
        "plain_ascii": [ord(c) for c in "0123456789abcdefghijklmnopqrstuvwxyz.-_"],
    }


def draw(rng, pool, size):
    return "".join(chr(rng.choice(pool)) for _ in range(size))


# ---------------------------------------------------------------------------
# NFKC corpus


def nfkc_corpus(tables, table_model):
    rng = random.Random(SEED)
    pool = pools(tables)
    names = list(pool)
    cases = []

    def add(text, result):
        cases.append({"i": code_points(text), "o": code_points(result)})

    # Every composition pair, alone and with a mark on either side: this
    # covers current pairs (including composites added after 3.2.0) and the
    # exclusion that is never composed.
    marks = rng.sample(pool["combining"], 8)
    for first, second, composite in tables["compose"]:
        add(chr(first) + chr(second), UCD.normalize("NFKC", chr(first) + chr(second)))
        mark = chr(rng.choice(marks))
        add(chr(first) + mark + chr(second), UCD.normalize("NFKC", chr(first) + mark + chr(second)))
        add(chr(first) + chr(second) + mark, UCD.normalize("NFKC", chr(first) + chr(second) + mark))
    # Every Hangul syllable, decomposed, and with a mark.
    for syllable in range(0xAC00, 0xD7A4):
        decomposed = UCD.normalize("NFD", chr(syllable))
        add(decomposed, UCD.normalize("NFKC", decomposed))
        mark = chr(rng.choice(marks))
        add(decomposed + mark, UCD.normalize("NFKC", decomposed + mark))

    while len(cases) < NFKC_RANDOM:
        size = rng.randint(1, 8)
        parts = []
        for _ in range(size):
            name = rng.choice(names)
            parts.append(chr(rng.choice(pool[name])))
        text = "".join(parts)
        add(text, UCD.normalize("NFKC", text))

    singles = {}
    for cp in range(MAXCP):
        result = UCD.normalize("NFKC", chr(cp))
        if result != chr(cp):
            singles[cp] = code_points(result)

    corpus = {"seed": SEED, "version": UCD.unidata_version,
              "cases": cases, "singles": singles}
    check_model(table_model, [case for case in cases[:MODEL_SAMPLE]],
                lambda text: UCD.normalize("NFKC", text), "NFKC")
    return corpus


# ---------------------------------------------------------------------------
# nameprep corpus


def nameprep_result(label):
    """The outcome of ucd_3_2_0-based nameprep: text or the error fields."""
    try:
        return {"o": code_points(nameprep_expected(label))}
    except NameprepError as error:
        return {"e": {"s": error.start, "e": error.end, "r": error.reason}}


def nameprep_expected(label):
    """encodings.idna.nameprep, with its UnicodeEncodeError unpacked."""
    import encodings.idna
    try:
        return encodings.idna.nameprep(label)
    except UnicodeEncodeError as error:
        raise NameprepError(error.start, error.end, error.reason) from None


def nameprep_corpus(tables, table_model):
    rng = random.Random(SEED + 1)
    pool = pools(tables)
    pool_names = list(pool)
    changed = {}
    errors = {}
    for cp in range(MAXCP):
        ch = chr(cp)
        try:
            result = nameprep_expected(ch)
        except NameprepError as error:
            errors[cp] = {"s": error.start, "e": error.end, "r": error.reason}
            continue
        if result != ch:
            changed[cp] = code_points(result)

    cases = []
    # A few systematic shape families: empty, ASCII space, controls, ACE
    # prefix, mapped-to-nothing, long runs.
    for text in ["", " ", "xn--abc", "\u00ad", "\u00ad\u00ad", "a\u00ad",
                 "\u180ba", "\ufeffa", "xn--bcher-kva", "A\u0301",
                 "\u0130", "\u00df", "\u1e9e", "\u03c2", "\u03a3",
                 "\u0627a", "a\u0627", "\u0627\u05d0", "\u05d0\u0301",
                 "\u0627 ", " \u0627", "\u06271", "1\u0627",
                 "\u0964\u0627", "\u0627\u0964", "\u200fa", "a\u200f"]:
        cases.append({"i": code_points(text), **nameprep_result(text)})
    while len(cases) < NAMEPREP_RANDOM:
        size = rng.randint(0, 12)
        parts = []
        for _ in range(size):
            name = rng.choice(pool_names)
            parts.append(chr(rng.choice(pool[name])))
        text = "".join(parts)
        cases.append({"i": code_points(text), **nameprep_result(text)})

    corpus = {"seed": SEED + 1, "version": UCD.unidata_version,
              "changed": changed, "errors": errors, "cases": cases}
    for case in cases[:MODEL_SAMPLE]:
        text = from_code_points(case["i"])
        try:
            want = nameprep_expected(text)
        except NameprepError as error:
            try:
                nameprep_model(TABLES_MODEL, text)
            except NameprepError as got:
                assert (got.start, got.end, got.reason) == (error.start, error.end, error.reason), \
                    (code_points(text), error.reason, got.reason)
                continue
            raise AssertionError("model accepted %r: %r" % (code_points(text), error.reason))
        got = nameprep_model(TABLES_MODEL, text)
        assert got == want, (code_points(text), code_points(got), code_points(want))
    return corpus


# ---------------------------------------------------------------------------
# idna decode corpus


def decode_outcome(data):
    try:
        return {"o": code_points(data.decode("idna"))}
    except UnicodeDecodeError as error:
        return {"e": {"s": error.start, "e": error.end, "r": error.reason,
                      "c": type(error).__name__}}


def ascii_labels(rng, count):
    """Random Unicode labels CPython's str.encode('idna') accepts."""
    pool = POOLS
    out = []
    while len(out) < count:
        size = rng.randint(1, 10)
        name = rng.choice(["letters", "compatibility", "combining", "corrections",
                           "plain_ascii", "rtl", "late"])
        text = draw(rng, pool[name], size)
        try:
            encoded = text.encode("idna")
        except UnicodeError:
            continue
        if not encoded:
            continue
        out.append(encoded)
    return out


def mixed_case(rng, data):
    return bytes(byte - 32 if byte >= ord("a") and byte <= ord("z") and rng.random() < 0.5 else byte
                 for byte in data)


def punycode_label(text):
    return b"xn--" + punycode_encode(text)


def decode_corpus(tables, table_model):
    rng = random.Random(SEED + 2)
    cases = []

    def add(data):
        cases.append({"i": data.hex(), **decode_outcome(data)})

    labels = ascii_labels(rng, DECODE_RANDOM // 3)
    for label in labels:
        add(label)
        add(mixed_case(rng, label))
        add(label.upper())
        add(label + b".")
        add(label + b"." + rng.choice(labels))
        add(rng.choice(labels) + b"." + label)
    for _ in range(DECODE_RANDOM // 6):
        first, second = rng.choice(labels), rng.choice(labels)
        add(first + b"." + second + b".")
    for text in [b"", b".", b"..", b"...", b"a..b", b".a", b"a.", b"xn--", b"xn--.",
                 b"xn--.xn--", b"xn--.a", b".xn--", b"XN--BCHER-KVA", b"Xn--BcHeR-kVa",
                 b"xn--bcher-kva", b"xn--bcher-kva.", b"xn--bcher-kva.xn--bcher-kva",
                 b"x\xff", b"\xff", b"a\xffb", b"\x80", b"xn--\xff", b"xn--a\xff",
                 b"xn--0", b"xn--a", b"xn--abc-", b"xn--zca", b"xn--tda",
                 b"xn--bcher-kva\x00", b"xn--bcher-kva ", b" xn--bcher-kva"]:
        add(text)
    # Non-ASCII bytes in every position of a valid label.
    for label in rng.sample(labels, 200):
        position = rng.randrange(len(label) + 1)
        add(label[:position] + bytes([rng.randrange(0x80, 0x100)]) + label[position:])
    # Broken punycode.
    alphabet = b"abcdefghijklmnopqrstuvwxyz0123456789"
    for _ in range(DECODE_RANDOM // 8):
        size = rng.randint(0, 24)
        add(b"xn--" + bytes(rng.choice(alphabet) for _ in range(size)))
        add(b"xn--" + bytes(rng.choice(alphabet) for _ in range(size)) + b"-")
    # Labels whose decoded text does not round-trip: an ASCII text encoded as
    # punycode re-encodes to plain ASCII, never to an ACE label.
    for _ in range(DECODE_RANDOM // 8):
        text = draw(rng, POOLS["plain_ascii"], rng.randint(1, 20))
        add(punycode_label(text))
        add(mixed_case(rng, punycode_label(text)))
    # Prohibited characters, bidi violations and the ACE prefix in the decoded
    # text: the punycode is written directly since nameprep refuses them.
    for cp in [0x80, 0xA0, 0x2028, 0x2029, 0xE000, 0xFDD0, 0xFFFE, 0xFFFF,
               0xD800, 0xDFFF, 0xFFF9, 0xFFFB, 0xFFFC, 0x2FF0, 0x200E, 0x200F,
               0xE0001, 0xE0020, 0xE007F, 0x180E, 0x0340, 0x0341]:
        add(punycode_label(chr(cp)))
        add(punycode_label("a" + chr(cp)))
        add(punycode_label(chr(cp) + "a"))
    for text in ["xn--abc", "xn--", "xn--xn--", "Xn--abc", "xn--a-b"]:
        add(punycode_label(text))
    for text in ["\u05d0a", "a\u05d0", "\u05d0\u0301", "\u0627\u0031",
                 "\u05d0 ", " \u05d0", "\u0627\u05d0", "a\u0627",
                 "\u05d0a\u05d0", "\u0627\u0964", "\u05d01", "1\u05d0"]:
        add(punycode_label(text))
    # Over-long labels: 63 is the ToASCII limit, 1024 the ToUnicode limit.
    for text in ["a" * 62, "a" * 63, "a" * 64, "a" * 70, "a" * 1023, "a" * 1024,
                 "a" * 1025, "\u00e9" * 40, "\u00e9" * 60, "\u00e9" * 100]:
        add(punycode_label(text))
    for data in [b"a" * 63, b"a" * 64, b"a" * 1023, b"a" * 1024, b"a" * 1025,
                 b"xn--" + b"a" * 1019, b"xn--" + b"a" * 1020, b"xn--" + b"a" * 1021]:
        add(data)
    # Random bytes.
    for _ in range(DECODE_RANDOM // 10):
        add(bytes(rng.randrange(256) for _ in range(rng.randint(0, 10))))

    while len(cases) < DECODE_RANDOM:
        add(rng.choice(labels))

    toascii = []
    for text in ["a", "ab", "xn--abc", "XN--ABC", "\u00ad",
                 "\u00e9", "b\u00fccher", "a" * 63, "a" * 64, "a" * 65,
                 "\u00e9" * 30, "\u00e9" * 60, "\u00df", "\u0130", "\u03c2",
                 "\u05d0", "a\u05d0", "\u05d0a", "\u0627\u0031", "\u0627\u0964",
                 "\u200ea", "a\u200e", "\u0340", "\ufffc", "\U000e0001",
                 "\u00a0", " ", "a b", "a\u0301", "\u1e9b\u0323",
                 "xn--bcher-kva", "\u2460", "\uff21\uff22"] + \
            [draw(rng, POOLS["letters"], rng.randint(1, 8)) for _ in range(TOASCII_RANDOM)]:
        try:
            encoded = text.encode("idna")
        except UnicodeEncodeError as error:
            toascii.append({"i": code_points(text),
                            "e": {"s": error.start, "e": error.end, "r": error.reason}})
        except UnicodeError as error:
            toascii.append({"i": code_points(text),
                            "e": {"s": 0, "e": 0, "r": str(error)}})
        else:
            toascii.append({"i": code_points(text), "b": encoded.hex()})

    corpus = {"seed": SEED + 2, "cases": cases, "toascii": toascii}
    return corpus


# ---------------------------------------------------------------------------


def check_model(table_model, cases, oracle, name):
    for case in cases:
        text = from_code_points(case["i"])
        want = oracle(text)
        got = nfkc_model(table_model, text)
        assert got == want, "%s model: %r -> %r, want %r" % (
            name, code_points(text), code_points(got), code_points(want))


def escaped_repr(cp):
    """repr(chr(cp)) for a character repr() escapes."""
    if cp < 0x100:
        return "'\\x%02x'" % cp
    if cp < 0x10000:
        return "'\\u%04x'" % cp
    return "'\\U%08x'" % cp


def committed_nameprep(corpus):
    """The nameprep corpus with the escaped single code point errors as ranges."""
    errors, ranges = {}, []
    for cp in sorted(corpus["errors"]):
        error = corpus["errors"][cp]
        if error == {"s": 0, "e": 1, "r": "Invalid character " + escaped_repr(cp)} \
                and repr(chr(cp)) == escaped_repr(cp):
            if ranges and ranges[-1][1] == cp - 1:
                ranges[-1][1] = cp
            else:
                ranges.append([cp, cp])
        else:
            errors[cp] = error
    return {**corpus, "errors": errors, "error_ranges": ranges,
            "sample": SAMPLE_STRIDE, "cases": corpus["cases"][::SAMPLE_STRIDE]}


def committed(corpus):
    """Every SAMPLE_STRIDE-th case of a corpus."""
    return {**corpus, "sample": SAMPLE_STRIDE, "cases": corpus["cases"][::SAMPLE_STRIDE]}


def gzip_bytes(data):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as archive:
        archive.write(data)
    return stream.getvalue()


def encode_json(value):
    return gzip_bytes(json.dumps(value, separators=(",", ":")).encode())


TABLES_MODEL = None
POOLS = None


def main():
    global TABLES_MODEL, POOLS
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="regenerate in memory and fail if a committed file differs")
    parser.add_argument("--full", metavar="DIR",
                        help="also write the full (unsampled) corpora to DIR")
    arguments = parser.parse_args()

    tables = build_tables()
    TABLES_MODEL = Tables(tables)
    POOLS = pools(tables)
    assert hangul_pairs() == 11172

    nfkc = nfkc_corpus(tables, TABLES_MODEL)
    nameprep = nameprep_corpus(tables, TABLES_MODEL)
    decode = decode_corpus(tables, TABLES_MODEL)

    files = {
        TABLES: encode_json(tables),
        NFKC_CORPUS: encode_json(committed(nfkc)),
        NAMEPREP_CORPUS: encode_json(committed_nameprep(nameprep)),
        DECODE_CORPUS: encode_json(committed(decode)),
    }
    if arguments.full:
        os.makedirs(arguments.full, exist_ok=True)
        for path, corpus in ((NFKC_CORPUS, nfkc), (NAMEPREP_CORPUS, nameprep), (DECODE_CORPUS, decode)):
            with open(os.path.join(arguments.full, os.path.basename(path)), "wb") as target:
                target.write(encode_json(corpus))
    status = 0
    for path, data in files.items():
        if arguments.check:
            try:
                with open(path, "rb") as existing:
                    current = existing.read()
            except OSError:
                current = None
            if current != data:
                print("out of date:", os.path.relpath(path))
                status = 1
        else:
            with open(path, "wb") as target:
                target.write(data)
    if not arguments.check:
        for path, data in files.items():
            print("%8d bytes  %s" % (len(data), os.path.relpath(path)))
        print("nfkd %d, combining %d, compose %d, b2 %d, prohibited %d ranges"
              % (len(tables["nfkd"]), len(tables["combining"]), len(tables["compose"]),
                 len(tables["b2"]), len(tables["prohibited"])))
        print("corpora: nfkc %d (+%d singles), nameprep %d (+%d changed, %d errors), decode %d"
              % (len(nfkc["cases"]), len(nfkc["singles"]), len(nameprep["cases"]),
                 len(nameprep["changed"]), len(nameprep["errors"]), len(decode["cases"])))
    return status


if __name__ == "__main__":
    sys.exit(main())
