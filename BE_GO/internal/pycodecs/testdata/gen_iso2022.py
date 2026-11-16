"""Writes the CPython 3.13 tables and corpus of the stateful ISO-2022 and HZ
decoders of package pycodecs.

Run with the reference interpreter, from anywhere:

    $PY -I testdata/gen_iso2022.py           # write the files
    $PY -I testdata/gen_iso2022.py --check   # regenerate in memory, fail on a diff

It writes two files, both derived from CPython's own decoders:

  iso2022_tables.bin.gz              the designation charsets as byte pair maps
  testdata/corpus_iso2022.json.gz    the differential corpus (four error handlers)

Nothing here is written from memory: every expected code point, error span and
error reason comes from bytes.decode in the running interpreter.  A charset
table is derived by decoding every byte (pair) through a codec that designates
that charset, so the composed decoders (JIS X 0213's emulation of the 2000
standard, its combining pairs, JIS X 0208's F/W reverse solidus) are captured
exactly as CPython applies them.
"""

import codecs
import gzip
import json
import os
import random
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
BLOB = os.path.join(PKG, "iso2022_tables.bin.gz")
CORPUS = os.path.join(HERE, "corpus_iso2022.json.gz")

SEED = 20261117
CASES_PER_CODEC = 1500

# The stateful codecs, by codecs.lookup(name).name.  hz comes from
# Modules/cjkcodecs/_codecs_cn.c, the others from _codecs_iso2022.c.
HZ = "hz"
ISO2022 = [
    "iso2022_jp", "iso2022_jp_1", "iso2022_jp_2", "iso2022_jp_2004",
    "iso2022_jp_3", "iso2022_jp_ext", "iso2022_kr",
]
CODECS = [HZ] + ISO2022

# --------------------------------------------------------------------------
# The designation charsets.
#
# A charset is derived through a probe: the escape sequence that designates it
# into G0 (or, for the G2 charsets, into G2 plus a single shift), followed by
# the bytes to decode.  Only codecs that really carry the charset are used, so
# the result is the composed CPython decoder, not an underlying mapping table.
#
# width 1 charsets are indexed by one byte, width 2 charsets by two.

CHARSETS = [
    # name,              width, probe codec,         designation prefix
    ("jisx0201_r",       1, "iso2022_jp",      b"\x1b(J"),
    ("jisx0201_k",       1, "iso2022_jp_ext",  b"\x1b(I"),
    ("jisx0208",         2, "iso2022_jp",      b"\x1b$B"),
    ("jisx0212",         2, "iso2022_jp_1",    b"\x1b$(D"),
    ("jisx0213_1_2000",  2, "iso2022_jp_3",    b"\x1b$(O"),
    ("jisx0213_2_2000",  2, "iso2022_jp_3",    b"\x1b$(P"),
    ("jisx0213_1_2004",  2, "iso2022_jp_2004", b"\x1b$(Q"),
    ("jisx0213_2_2004",  2, "iso2022_jp_2004", b"\x1b$(P"),
    ("ksx1001",          2, "iso2022_kr",      b"\x1b$C"),
    ("gb2312",           2, "iso2022_jp_2",    b"\x1b$A"),
    # The two G2 charsets of iso2022_jp_2.  In G0/G1 their decoder is the
    # always-unmappable dummy; only the ESC N single shift maps them.
    ("iso8859_1",        1, None,              b"\x1b.A"),
    ("iso8859_7",        1, None,              b"\x1b.F"),
]

# The marks a charset is designated by, in _codecs_iso2022.c:
#   CHARSET_GB2312 = 'A'|0x80, CHARSET_JISX0208 = 'B'|0x80, ...
# (spelled out per codec in CODEC_DESIGNATIONS)

# An entry of a two-byte charset table that decodes to two code points.
# the designation sets and the charset it selects, with the flags of struct
# iso2022_config.  iso2022_jp_2004 and iso2022_jp_3 list the pair-only JIS X
# 0213 plane 1 designation and then the plain one; both decode identically, so
# the mark repeats.
NO_SHIFT = 1
USE_G2 = 2
USE_JISX0208_EXT = 4

CODEC_DESIGNATIONS = {
    "iso2022_kr": ([
        (0x43 | 0x80, "ksx1001"),
    ], 0),
    "iso2022_jp": ([
        (0x42 | 0x80, "jisx0208"), (0x4A, "jisx0201_r"),
        (0x40 | 0x80, "jisx0208"),
    ], NO_SHIFT | USE_JISX0208_EXT),
    "iso2022_jp_1": ([
        (0x42 | 0x80, "jisx0208"), (0x44 | 0x80, "jisx0212"),
        (0x4A, "jisx0201_r"), (0x40 | 0x80, "jisx0208"),
    ], NO_SHIFT | USE_JISX0208_EXT),
    "iso2022_jp_2": ([
        (0x42 | 0x80, "jisx0208"), (0x44 | 0x80, "jisx0212"),
        (0x43 | 0x80, "ksx1001"), (0x41 | 0x80, "gb2312"),
        (0x4A, "jisx0201_r"), (0x40 | 0x80, "jisx0208"),
        (0x41, "iso8859_1"), (0x46, "iso8859_7"),
    ], NO_SHIFT | USE_G2 | USE_JISX0208_EXT),
    "iso2022_jp_2004": ([
        (0x51 | 0x80, "jisx0213_1_2004"), (0x42 | 0x80, "jisx0208"),
        (0x51 | 0x80, "jisx0213_1_2004"), (0x50 | 0x80, "jisx0213_2_2004"),
    ], NO_SHIFT | USE_JISX0208_EXT),
    "iso2022_jp_3": ([
        (0x4F | 0x80, "jisx0213_1_2000"), (0x42 | 0x80, "jisx0208"),
        (0x4F | 0x80, "jisx0213_1_2000"), (0x50 | 0x80, "jisx0213_2_2000"),
    ], NO_SHIFT | USE_JISX0208_EXT),
    "iso2022_jp_ext": ([
        (0x42 | 0x80, "jisx0208"), (0x44 | 0x80, "jisx0212"),
        (0x4A, "jisx0201_r"), (0x49, "jisx0201_k"), (0x40 | 0x80, "jisx0208"),
    ], NO_SHIFT | USE_JISX0208_EXT),
}

HANDLERS = ("strict", "replace", "surrogateescape", "ignore")

# Inputs every codec's corpus carries, whatever the random seed draws: the
# designation forms of the config, the line-feed and shift rules, and the
# paths a random draw rarely reaches (the G2 designations of iso2022_jp_2,
# CPython's RuntimeError for a G2 charset processg2 does not know, hz's tilde
# escapes in both modes).
SPECIAL = {
    "hz": [
        b"~", b"~~", b"~{", b"~}", b"~\n", b"~x", b"~~~", b"~{~}", b"~{~~",
        b"~{!!", b"~{!!~}", b"~{!!~~", b"~{!!~{", b"~{!!\n", b"~{!\x80",
        b"~{\x80!", b"~{!!~}\x80", b"\x80", b"~\xff", b"a~{!!~}b~\n~{!!~}c",
        b"~{!!!", b"~{~\n~}", b"~}\n~{!!", b"~{~{~{", b"~}~}",
    ],
    "iso2022_kr": [
        b"\x1b$C!!", b"\x1b$)C!!", b"\x1b$)C\x0e!!\x0fA", b"\x0e!!\x0fA",
        b"\x0e!!\n!!", b"\x0e!!\x1b$C!!", b"\x1b$C!!\n!!", b"\x1b$@!!",
        b"\x1b$B!!", b"\x1b(J!", b"\x1b$C", b"\x1b$C!", b"\x1b$)C",
        b"\x1b.C", b"\x1b.C\x1bN\x41", b"\x0e\x21\x21\x0fA",
    ],
    "iso2022_jp": [
        b"\x1b$B!!", b"\x1b$@!!", b"\x1b(J!", b"\x1b(I!", b"\x1b$(D!!",
        b"\x1b&@\x1b$B!!", b"\x1b&@", b"\x1b&@\x1b(B!!", b"\x1b(J\x5c",
        b"\x1b(J\x7e", b"\x1b(J\x7f", b"\x1b$B\x21\x40", b"\x1b.B",
        b"\x1b(B\x1b$B!!\x1b(B!!", b"\x0e!!\x0f", b"\x1b$B!!\n\x1b(B!!",
        b"\x1b$B!", b"\x1b$", b"\x1b", b"\x1b(", b"\x1b$Z!!",
    ],
    "iso2022_jp_1": [
        b"\x1b$(D!!", b"\x1b$)D!!", b"\x1b$D!!", b"\x1b$(D!", b"\x1b$(B!!",
        b"\x1b$B!!\x1b$(D!!", b"\x1b(J\x5c", b"\x1b$B\x21\x40", b"\x1b$(D\x22\x2f",
        b"\x0e\x1b$)D!!\x0f", b"\x1b$Z", b"\x1b$(Z!!",
    ],
    "iso2022_jp_2": [
        b"\x1b$A!!", b"\x1b$)A!!", b"\x1b.A\x1bN\x41", b"\x1b.A\x1bN\x80",
        b"\x1b.F\x1bN\x41", b"\x1b.F\x1bN\xd4", b"\x1b.F\x1bN\x80",
        b"\x1bN\x41", b"\x1bN\x80", b"\x1b.B\x1bN\x41", b"\x1b.B\x1bN\x80",
        b"\x1b.J\x1bNA", b"\x1b.J\x1bN\x80", b"\x1b.\xc0\x1bNA",
        b"\x1b.\xc1\x1bNA", b"\x1b.\xc3\x1bNA", b"\x1b.\xc4\x1bN\x41",
        b"\x1b.J", b"\x1b.A", b"\x1b.F", b"\x1b$C!!", b"\x1b$A!!\x1b$B!!",
        b"\x1b(I!", b"\x1b(J\x5c", b"\x1b.A\x1bN", b"\x1bA",
    ],
    "iso2022_jp_2004": [
        b"\x1b$(Q!!", b"\x1b$(P!!", b"\x1b$)Q!!", b"\x1b$Q!!", b"\x1b$P!!",
        b"\x1b$(O!!", b"\x1b$(Q\x2e\x21", b"\x1b$(Q!", b"\x1b$(Q",
        b"\x1b$(P!", b"\x1b$B!!", b"\x1b$@!!", b"\x1b(J!", b"\x1b$(D!!",
        b"\x1b&@\x1b$B!!", b"\x1b$(Q\x21\x40", b"\x1b$(Q\x7e\x7e",
    ],
    "iso2022_jp_3": [
        b"\x1b$(O!!", b"\x1b$(P!!", b"\x1b$)O!!", b"\x1b$O!!", b"\x1b$P!!",
        b"\x1b$(Q!!", b"\x1b$(O\x2e\x21", b"\x1b$(P\x7d\x3b",
        b"\x1b$(O!", b"\x1b$(O", b"\x1b$(P!", b"\x1b$B!!", b"\x1b$@!!",
        b"\x1b(J!", b"\x1b$(D!!", b"\x1b&@\x1b$B!!",
    ],
    "iso2022_jp_ext": [
        b"\x1b(I!", b"\x1b(I\x21\x5f", b"\x1b(I\x60", b"\x1b(I\x80",
        b"\x1b(I\x21", b"\x1b(J\x5c", b"\x1b$(D!!", b"\x1b$@!!",
        b"\x1b$B!!", b"\x1b(I\x21\x5f\x1b(J!", b"\x1b(I", b"\x1b$Z!",
    ],
}

# An entry of a two-byte charset table that decodes to two code points.


# --------------------------------------------------------------------------
# charset derivation


def probe(codec, prefix, tail, span):
    """The code points a probe designates and decodes, or None when unmapped.

    A failed probe must report exactly the bytes the charset decoder was asked
    to decode, at the offset right after the designation (span bytes).
    """
    try:
        text = (prefix + tail).decode(codec)
    except UnicodeDecodeError as e:
        if e.start != len(prefix) or e.end != len(prefix) + span:
            raise AssertionError((codec, prefix, tail, e.start, e.end, e.reason))
        return None
    return [ord(ch) for ch in text]


def derive_charset(name, width, codec, prefix):
    """The value of every byte (pair) of a charset: None, one cp or two cps."""
    table = []
    if width == 1:
        for b in range(0x20, 0x80):
            table.append(probe(codec, prefix, bytes([b]), 1))
        return table
    for c1 in range(0x20, 0x80):
        for c2 in range(256):
            table.append(probe(codec, prefix, bytes([c1, c2]), 2))
    return table


def derive_g2(name, codec, prefix):
    """The 256-entry map of a G2 charset used by the ESC N single shift."""
    table = []
    for b in range(256):
        table.append(probe(codec, prefix, b"\x1bN" + bytes([b]), 3))
    return table


def derive_charsets():
    """name -> {"width", "table", "g2"} for every charset of CHARSETS."""
    out = {}
    for name, width, codec, prefix in CHARSETS:
        if codec is None:
            # The G2 charsets have no G0/G1 decoder (CPython's is the dummy
            # that always fails); only the ESC N single shift maps them.
            table, g2 = None, derive_g2(name, "iso2022_jp_2", prefix)
        else:
            table, g2 = derive_charset(name, width, codec, prefix), None
        out[name] = {"width": width, "table": table, "g2": g2}
    return out


# --------------------------------------------------------------------------
# blob


class Writer:
    def __init__(self):
        self.out = bytearray()

    def byte(self, value):
        if not 0 <= value <= 0xFF:
            raise AssertionError(value)
        self.out.append(value)

    def uvarint(self, value):
        if value < 0:
            raise AssertionError(value)
        while True:
            b = value & 0x7F
            value >>= 7
            if value:
                self.out.append(b | 0x80)
            else:
                self.out.append(b)
                return

    def text(self, value):
        raw = value.encode()
        self.uvarint(len(raw))
        self.out += raw

    def value(self, cps):
        """One table entry: 0 unmapped, 1 a code point pair, else cp + 2."""
        if cps is None:
            self.uvarint(0)
        elif len(cps) == 1:
            self.uvarint(cps[0] + 2)
        elif len(cps) == 2:
            self.uvarint(1)
            self.uvarint(cps[0])
            self.uvarint(cps[1])
        else:
            raise AssertionError(cps)


def blob(charsets):
    """iso2022_tables.bin.gz: the charsets and the designations of each codec."""
    order = [name for name, _, _, _ in CHARSETS]
    index = {name: i for i, name in enumerate(order)}
    w = Writer()
    w.out += b"PYISO2"
    w.uvarint(len(order))
    for name in order:
        charset = charsets[name]
        w.text(name)
        w.byte(charset["width"])
        if charset["table"] is None:
            # iso8859_1/7 in G0/G1: CPython's dummy decoder, always unmappable.
            w.byte(1)
        else:
            w.byte(0)
            for cps in charset["table"]:
                w.value(cps)
        g2 = charset["g2"]
        if g2 is None:
            w.byte(0)
        else:
            # The tables are keyed by the mark the G2 designation uses; the
            # values are the composed ISO-8859 results of processg2.
            w.byte(1 if name == "iso8859_1" else 2)
            for cps in g2:
                w.value(cps)
    w.uvarint(len(CODEC_DESIGNATIONS))
    for codec in sorted(CODEC_DESIGNATIONS):
        designations, flags = CODEC_DESIGNATIONS[codec]
        w.text(codec)
        w.byte(flags)
        w.uvarint(len(designations))
        for mark, name in designations:
            w.byte(mark)
            w.uvarint(index[name])
    return gzip.compress(bytes(w.out), compresslevel=9, mtime=0)


# --------------------------------------------------------------------------
# corpus


def encodable_units(codec, charsets):
    """(text, bytes) units the codec can encode, for building valid inputs."""
    cps = set(range(0x20, 0x7F))
    for _, name in CODEC_DESIGNATIONS.get(codec, ([], 0))[0]:
        table = charsets[name]["table"]
        if table is None:
            continue
        for mapped in table:
            if mapped:
                cps.update(mapped)
    units = []
    for cp in sorted(cps):
        try:
            raw = chr(cp).encode(codec)
        except (UnicodeEncodeError, UnicodeError):
            continue
        if raw:
            units.append((chr(cp), raw))
    if not units:
        raise AssertionError(codec)
    return units


def escape_pieces(codec):
    """Escape sequences, valid and invalid, that the codec's config accepts."""
    pieces = [
        b"\x1b(B", b"\x1b)B", b"\x1b$B", b"\x1b$@", b"\x1b(J", b"\x1b(I",
        b"\x1b$(B", b"\x1b$)B", b"\x1b&D", b"\x1b&@", b"\x1b&@\x1b$B",
        b"\x1b$(D", b"\x1b$)C", b"\x1b$C", b"\x1b$A", b"\x1b.A", b"\x1b.F",
        b"\x1b$(O", b"\x1b$(P", b"\x1b$(Q", b"\x1b$Z", b"\x1b(X", b"\x1b$X",
        b"\x1b", b"\x1b$", b"\x1b$(", b"\x1b) ", b"\x1bN", b"\x1bN\x41",
        b"\x1bN\xb4", b"\x1bN\x80", b"\x1b@@@@@@@@@@@@@@@@", b"\x1b$B\x1b$B",
        b"\x0e", b"\x0f", b"\n", b"\r\n", b"\x1b\x80", b"\x1b\xc1",
        b"\x1b.\xc0", b"\x1b.\xc1", b"\x1b.J", b"\x80", b"\xff", b"\x00",
    ]
    if codec != "iso2022_kr":
        pieces.append(b"\x0e\x21\x21\x0f")
    return pieces


def make_input(rng, codec, units, tildes=False):
    """One corpus input: valid text, escape soup, mutation or random bytes."""
    kind = rng.randrange(100)
    if tildes:
        return make_hz_input(rng, units, kind)

    if kind < 45:  # valid text encoded with str.encode(codec)
        for _ in range(8):
            chars = [rng.choice(units)[0] for _ in range(rng.randint(1, 12))]
            if rng.random() < 0.4:
                chars.insert(rng.randrange(len(chars) + 1),
                             chr(rng.randrange(0x20, 0x7F)))
            text = "".join(chars)
            try:
                raw = text.encode(codec)
            except (UnicodeEncodeError, UnicodeError):
                continue
            return raw
        return rng.choice(units)[1]

    if kind < 75:  # escape soup: designations, shifts, line feeds, pairs
        out = bytearray()
        pieces = escape_pieces(codec)
        for _ in range(rng.randint(1, 7)):
            pick = rng.randrange(10)
            if pick < 6:
                out += rng.choice(pieces)
            elif pick < 8:
                out += bytes([rng.randrange(0x21, 0x7F),
                              rng.randrange(0x21, 0x7F)])
            elif pick == 8:
                out += bytes(rng.randrange(0x80, 0x100)
                             for _ in range(rng.randint(1, 3)))
            else:
                out += bytes(rng.randrange(0x20, 0x7F)
                             for _ in range(rng.randint(1, 4)))
        return bytes(out)

    if kind < 90:  # a valid encoding with a few byte mutations
        raw = bytearray(make_valid(rng, codec, units))
        for _ in range(rng.randint(1, 3)):
            op = rng.randrange(3)
            if op == 0 and raw:
                del raw[rng.randrange(len(raw))]
            elif op == 1:
                raw.insert(rng.randrange(len(raw) + 1),
                           rng.randrange(256))
            elif raw:
                raw[rng.randrange(len(raw))] = rng.randrange(256)
        return bytes(raw)

    if kind < 95:  # a random ASCII run with SI, SO and line feeds
        alphabet = b"A!\x0e\x0f\n~ $()&@\x1bN"
        return bytes(rng.choice(alphabet)
                     for _ in range(rng.randint(0, 16)))

    return bytes(rng.randrange(256) for _ in range(rng.randint(0, 20)))


def make_valid(rng, codec, units):
    """A byte string of the codec's own encoding of some text, or random units."""
    for _ in range(8):
        chars = [rng.choice(units)[0] for _ in range(rng.randint(1, 10))]
        try:
            return "".join(chars).encode(codec)
        except (UnicodeEncodeError, UnicodeError):
            continue
    out = bytearray()
    for _ in range(rng.randint(1, 4)):
        out += rng.choice(units)[1]
    return bytes(out)


def make_hz_input(rng, units, kind):
    gb_units = [raw for _, raw in units if len(raw) > 1]

    def gb_pair():
        if gb_units:
            return rng.choice(gb_units)
        return bytes([rng.randrange(0x21, 0x7F), rng.randrange(0x21, 0x7F)])

    if kind < 45:  # valid text encoded with str.encode('hz')
        for _ in range(8):
            chars = [rng.choice(units)[0] for _ in range(rng.randint(1, 12))]
            try:
                return "".join(chars).encode("hz")
            except (UnicodeEncodeError, UnicodeError):
                continue
        return b"~{" + gb_pair() + b"~}"

    if kind < 80:  # tilde soup
        pieces = [b"~{", b"~}", b"~~", b"~\n", b"~", b"~x", b"~{~{",
                  b"~}~}", b"~~~", b"~\r\n", b"~{\x80", b"\x80~{"]
        out = bytearray()
        for _ in range(rng.randint(1, 6)):
            pick = rng.randrange(10)
            if pick < 5:
                out += rng.choice(pieces)
            elif pick < 7:
                out += gb_pair()
            elif pick < 9:
                out += bytes(rng.randrange(0x20, 0x7F)
                             for _ in range(rng.randint(1, 4)))
            else:
                out += bytes(rng.randrange(0x80, 0x100)
                             for _ in range(rng.randint(1, 2)))
        return bytes(out)

    if kind < 92:  # a valid encoding with a few byte mutations
        raw = bytearray(make_valid_hz(rng, units))
        for _ in range(rng.randint(1, 3)):
            op = rng.randrange(3)
            if op == 0 and raw:
                del raw[rng.randrange(len(raw))]
            elif op == 1:
                raw.insert(rng.randrange(len(raw) + 1), rng.randrange(256))
            elif raw:
                raw[rng.randrange(len(raw))] = rng.randrange(256)
        return bytes(raw)

    return bytes(rng.choice(b"~{}\n !A\x7f") for _ in range(rng.randint(0, 16)))


def make_valid_hz(rng, units):
    for _ in range(8):
        chars = [rng.choice(units)[0] for _ in range(rng.randint(1, 10))]
        try:
            return "".join(chars).encode("hz")
        except (UnicodeEncodeError, UnicodeError):
            continue
    return b"~{" + rng.choice(units)[1] + b"~}"


# The codes that decode to two code points (JIS X 0213's combining pairs) are
# rare in random text, so every codec's corpus carries them explicitly, as the
# designation plus the byte pair the generator derived them from.
CHARSET_PREFIX = {name: prefix for name, _, _, prefix in CHARSETS}


def pair_inputs(codec, charsets):
    out = []
    for _, name in CODEC_DESIGNATIONS.get(codec, ([], 0))[0]:
        if charsets[name]["width"] != 2 or charsets[name]["table"] is None:
            continue
        prefix = CHARSET_PREFIX[name]
        for index, cps in enumerate(charsets[name]["table"]):
            if cps is None or len(cps) != 2:
                continue
            out.append(prefix + bytes([0x20 + index // 256, index % 256]))
    return out


def truncations(rng, codec, units):
    """Every prefix of a few representative encodings, cut at every byte."""
    texts = []
    for _ in range(6):
        chars = [rng.choice(units)[0] for _ in range(rng.randint(2, 6))]
        texts.append("".join(chars))
    if codec == "hz":
        texts.append("\u3000\u3000~abc")
    else:
        texts.append("\u3000A\u3000")
        texts.append("\u3042\u3044A")
    out = []
    for text in texts:
        try:
            raw = text.encode(codec)
        except (UnicodeEncodeError, UnicodeError):
            continue
        for cut in range(len(raw) + 1):
            out.append(raw[:cut])
    return out


def corpus_result(codec, data, handler):
    try:
        text = data.decode(codec, handler)
    except UnicodeDecodeError as e:
        return {"e": {"start": e.start, "end": e.end, "reason": e.reason,
                      "text": str(e)}}
    except (RuntimeError, ValueError) as e:
        return {"x": {"type": type(e).__name__, "text": str(e)}}
    return {"t": [ord(ch) for ch in text]}


def corpus(charsets):
    """The differential corpus: inputs, and the results of four error handlers.

    A result is {"t": [code points]}, {"e": {start, end, reason, text}} where
    text is CPython's str(UnicodeDecodeError), or {"x": {type, text}} for an
    exception CPython raises whatever the handler (iso2022_jp_2's G2 single
    shift reports RuntimeError("internal codec error") for a designation its
    processg2 does not know).  Identical results are stored once in "pool" and
    referenced by index, which keeps the file small.
    """
    pool = []
    pool_index = {}

    def result(codec, data, handler):
        decoded = corpus_result(codec, data, handler)
        key = json.dumps(decoded, sort_keys=True, separators=(",", ":"))
        index = pool_index.get(key)
        if index is None:
            index = len(pool)
            pool_index[key] = index
            pool.append(decoded)
        return index

    out = {"seed": SEED, "cases": CASES_PER_CODEC, "pool": pool, "codecs": {}}
    for codec in CODECS:
        if codecs.lookup(codec).name != codec:
            raise AssertionError(codec)
        rng = random.Random(SEED ^ zlib.crc32(codec.encode()))
        units = encodable_units(codec, charsets)
        inputs = []
        seen = set()

        def add(data):
            if data not in seen:
                seen.add(data)
                inputs.append(data)

        for data in SPECIAL[codec]:
            add(data)
        for data in pair_inputs(codec, charsets):
            add(data)
        for data in truncations(rng, codec, units):
            add(data)
        while len(inputs) < CASES_PER_CODEC:
            add(make_input(rng, codec, units, tildes=(codec == "hz")))
        cases = []
        for data in inputs:
            case = {"in": data.hex()}
            for handler, key in zip(HANDLERS, ("s", "r", "se", "g")):
                case[key] = result(codec, data, handler)
            cases.append(case)
        out["codecs"][codec] = cases
    return gzip.compress(json.dumps(out, separators=(",", ":")).encode(),
                         compresslevel=9, mtime=0)


# --------------------------------------------------------------------------


def main(argv):
    if len(argv) > 1 and argv[1] != "--check":
        print("usage: gen_iso2022.py [--check]", file=sys.stderr)
        return 2
    check = len(argv) > 1

    charsets = derive_charsets()
    blob_bytes = blob(charsets)
    corpus_bytes = corpus(charsets)

    outputs = [(BLOB, blob_bytes), (CORPUS, corpus_bytes)]
    if check:
        differs = []
        for path, data in outputs:
            try:
                with open(path, "rb") as f:
                    if f.read() != data:
                        differs.append(path)
            except FileNotFoundError:
                differs.append(path)
        for path in differs:
            print("differs: %s" % os.path.relpath(path, PKG))
        if differs:
            return 1
        print("--check: all generated files match")
        return 0
    for path, data in outputs:
        with open(path, "wb") as f:
            f.write(data)
    print("%-28s %8d bytes, %d charsets" % ("iso2022_tables.bin.gz",
                                            len(blob_bytes), len(CHARSETS)))
    print("%-28s %8d bytes, %d codecs, %d cases each"
          % ("corpus_iso2022.json.gz", len(corpus_bytes), len(CODECS),
             CASES_PER_CODEC))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
