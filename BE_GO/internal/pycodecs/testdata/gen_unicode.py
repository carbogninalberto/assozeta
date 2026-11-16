#!/usr/bin/env python3
"""Generate the W1b tables and corpus for BE_GO/internal/pycodecs.

Run with the reference CPython 3.13 interpreter, isolated:

    $PY -I BE_GO/internal/pycodecs/testdata/gen_unicode.py
    $PY -I BE_GO/internal/pycodecs/testdata/gen_unicode.py --check

It writes:

  BE_GO/internal/pycodecs/unicode_names.bin.gz
      gzip of "HEX NAME\\n" lines: every non-derived canonical character
      name from unicodedata plus every formal name alias. The derived
      Hangul/CJK/Tangut names are computed by the Go decoder instead of
      being stored.

  BE_GO/internal/pycodecs/testdata/corpus_unicode.json.gz
      at least 2000 inputs per codec with the result of
      codecs.decode(data, codec, errors) for strict, replace,
      surrogateescape and ignore (only the non-strict handlers for idna,
      whose codec rejects strict differently in this port).

Expected values always come from the reference Python. --check regenerates
both files in memory and exits non-zero if a committed file differs.
"""

import codecs
import gzip
import io
import json
import os
import random
import sys
import unicodedata
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
NAMES_FILE = os.path.join(PKG, "unicode_names.bin.gz")
CORPUS_FILE = os.path.join(HERE, "corpus_unicode.json.gz")

SEED = 0x1B_2026_1116
TARGET = 2200
ALL_HANDLERS = ("strict", "replace", "surrogateescape", "ignore")
IDNA_HANDLERS = ("replace", "surrogateescape", "ignore")

# ---------------------------------------------------------------------------
# Formal name aliases (Unicode 15.1.0 NameAliases.txt), embedded so that
# --check works without the Unicode data files. Every entry is verified
# against the reference unicodedata below.

ALIASES = """ACK;0006
ACKNOWLEDGE;0006
ALERT;0007
ALM;061C
APC;009F
APPLICATION PROGRAM COMMAND;009F
ARABIC SMALL HIGH LIGATURE ALEF WITH YEH BARREE;0616
BACKSPACE;0008
BEL;0007
BOM;FEFF
BPH;0082
BREAK PERMITTED HERE;0082
BS;0008
BYTE ORDER MARK;FEFF
BYZANTINE MUSICAL SYMBOL FTHORA SKLIRON CHROMA VASIS;1D0C5
CAN;0018
CANCEL CHARACTER;0094
CANCEL;0018
CARRIAGE RETURN;000D
CCH;0094
CGJ;034F
CHARACTER TABULATION SET;0088
CHARACTER TABULATION WITH JUSTIFICATION;0089
CHARACTER TABULATION;0009
CONTROL SEQUENCE INTRODUCER;009B
CR;000D
CSI;009B
CUNEIFORM SIGN NU11 OVER NU11 BUR OVER BUR;122D5
CUNEIFORM SIGN NU11 TENU;122D4
DATA LINK ESCAPE;0010
DC1;0011
DC2;0012
DC3;0013
DC4;0014
DCS;0090
DEL;007F
DELETE;007F
DEVICE CONTROL FOUR;0014
DEVICE CONTROL ONE;0011
DEVICE CONTROL STRING;0090
DEVICE CONTROL THREE;0013
DEVICE CONTROL TWO;0012
DLE;0010
EM;0019
END OF GUARDED AREA;0097
END OF LINE;000A
END OF MEDIUM;0019
END OF PROTECTED AREA;0097
END OF SELECTED AREA;0087
END OF TEXT;0003
END OF TRANSMISSION BLOCK;0017
END OF TRANSMISSION;0004
ENQ;0005
ENQUIRY;0005
EOL;000A
EOM;0019
EOT;0004
EPA;0097
ESA;0087
ESC;001B
ESCAPE;001B
ETB;0017
ETX;0003
FF;000C
FILE SEPARATOR;001C
FORM FEED;000C
FS;001C
FSI;2068
FVS1;180B
FVS2;180C
FVS3;180D
FVS4;180F
GROUP SEPARATOR;001D
GS;001D
HANGUL JONGSEONG SSANGYESIEUNG;11EE
HANGUL JONGSEONG YESIEUNG-KHIEUKH;11EF
HANGUL JONGSEONG YESIEUNG-KIYEOK;11EC
HANGUL JONGSEONG YESIEUNG-SSANGKIYEOK;11ED
HENTAIGANA LETTER E-1;1B001
HIGH OCTET PRESET;0081
HOP;0081
HORIZONTAL TABULATION SET;0088
HORIZONTAL TABULATION WITH JUSTIFICATION;0089
HORIZONTAL TABULATION;0009
HT;0009
HTJ;0089
HTS;0088
IND;0084
INDEX;0084
INFORMATION SEPARATOR FOUR;001C
INFORMATION SEPARATOR ONE;001F
INFORMATION SEPARATOR THREE;001D
INFORMATION SEPARATOR TWO;001E
KANNADA LETTER LLLA;0CDE
LAO LETTER FO FAY;0E9F
LAO LETTER FO FON;0E9D
LAO LETTER LO;0EA5
LAO LETTER RO;0EA3
LATIN CAPITAL LETTER GHA;01A2
LATIN SMALL LETTER GHA;01A3
LEFTWARDS TRIANGLE-HEADED ARROW WITH DOUBLE VERTICAL STROKE;2B7A
LF;000A
LINE FEED;000A
LINE TABULATION SET;008A
LINE TABULATION;000B
LOCKING-SHIFT ONE;000E
LOCKING-SHIFT ZERO;000F
LRE;202A
LRI;2066
LRM;200E
LRO;202D
MEDEFAIDRIN CAPITAL LETTER H;16E56
MEDEFAIDRIN CAPITAL LETTER NG;16E57
MEDEFAIDRIN SMALL LETTER H;16E76
MEDEFAIDRIN SMALL LETTER NG;16E77
MESSAGE WAITING;0095
MICR DASH SYMBOL;2449
MICR ON US SYMBOL;2448
MMSP;205F
MVS;180E
MW;0095
MYANMAR LETTER KHAMTI LLA;AA6E
NAK;0015
NBH;0083
NBSP;00A0
NEGATIVE ACKNOWLEDGE;0015
NEL;0085
NEW LINE;000A
NEXT LINE;0085
NL;000A
NNBSP;202F
NO BREAK HERE;0083
NUL;0000
NULL;0000
OPERATING SYSTEM COMMAND;009D
OSC;009D
PAD;0080
PADDING CHARACTER;0080
PARTIAL LINE BACKWARD;008C
PARTIAL LINE DOWN;008B
PARTIAL LINE FORWARD;008B
PARTIAL LINE UP;008C
PDF;202C
PDI;2069
PLD;008B
PLU;008C
PM;009E
PRESENTATION FORM FOR VERTICAL RIGHT WHITE LENTICULAR BRACKET;FE18
PRIVACY MESSAGE;009E
PRIVATE USE ONE;0091
PRIVATE USE TWO;0092
PRIVATE USE-1;0091
PRIVATE USE-2;0092
PU1;0091
PU2;0092
RECORD SEPARATOR;001E
REVERSE INDEX;008D
REVERSE LINE FEED;008D
RI;008D
RIGHTWARDS TRIANGLE-HEADED ARROW WITH DOUBLE VERTICAL STROKE;2B7C
RLE;202B
RLI;2067
RLM;200F
RLO;202E
RS;001E
SCI;009A
SET TRANSMIT STATE;0093
SGC;0099
SHIFT IN;000F
SHIFT OUT;000E
SHY;00AD
SI;000F
SINGLE CHARACTER INTRODUCER;009A
SINGLE GRAPHIC CHARACTER INTRODUCER;0099
SINGLE SHIFT THREE;008F
SINGLE SHIFT TWO;008E
SINGLE-SHIFT-2;008E
SINGLE-SHIFT-3;008F
SO;000E
SOH;0001
SOS;0098
SP;0020
SPA;0096
SS2;008E
SS3;008F
SSA;0086
ST;009C
START OF GUARDED AREA;0096
START OF HEADING;0001
START OF PROTECTED AREA;0096
START OF SELECTED AREA;0086
START OF STRING;0098
START OF TEXT;0002
STRING TERMINATOR;009C
STS;0093
STX;0002
SUB;001A
SUBSTITUTE;001A
SUNDANESE LETTER ARCHAIC I;1BBD
SYN;0016
SYNCHRONOUS IDLE;0016
SYRIAC SUBLINEAR COLON SKEWED LEFT;0709
TAB;0009
TIBETAN MARK BKA- SHOG GI MGO RGYAN;0FD0
UNIT SEPARATOR;001F
US;001F
VERTICAL TABULATION SET;008A
VERTICAL TABULATION;000B
VS100;E0153
VS101;E0154
VS102;E0155
VS103;E0156
VS104;E0157
VS105;E0158
VS106;E0159
VS107;E015A
VS108;E015B
VS109;E015C
VS10;FE09
VS110;E015D
VS111;E015E
VS112;E015F
VS113;E0160
VS114;E0161
VS115;E0162
VS116;E0163
VS117;E0164
VS118;E0165
VS119;E0166
VS11;FE0A
VS120;E0167
VS121;E0168
VS122;E0169
VS123;E016A
VS124;E016B
VS125;E016C
VS126;E016D
VS127;E016E
VS128;E016F
VS129;E0170
VS12;FE0B
VS130;E0171
VS131;E0172
VS132;E0173
VS133;E0174
VS134;E0175
VS135;E0176
VS136;E0177
VS137;E0178
VS138;E0179
VS139;E017A
VS13;FE0C
VS140;E017B
VS141;E017C
VS142;E017D
VS143;E017E
VS144;E017F
VS145;E0180
VS146;E0181
VS147;E0182
VS148;E0183
VS149;E0184
VS14;FE0D
VS150;E0185
VS151;E0186
VS152;E0187
VS153;E0188
VS154;E0189
VS155;E018A
VS156;E018B
VS157;E018C
VS158;E018D
VS159;E018E
VS15;FE0E
VS160;E018F
VS161;E0190
VS162;E0191
VS163;E0192
VS164;E0193
VS165;E0194
VS166;E0195
VS167;E0196
VS168;E0197
VS169;E0198
VS16;FE0F
VS170;E0199
VS171;E019A
VS172;E019B
VS173;E019C
VS174;E019D
VS175;E019E
VS176;E019F
VS177;E01A0
VS178;E01A1
VS179;E01A2
VS17;E0100
VS180;E01A3
VS181;E01A4
VS182;E01A5
VS183;E01A6
VS184;E01A7
VS185;E01A8
VS186;E01A9
VS187;E01AA
VS188;E01AB
VS189;E01AC
VS18;E0101
VS190;E01AD
VS191;E01AE
VS192;E01AF
VS193;E01B0
VS194;E01B1
VS195;E01B2
VS196;E01B3
VS197;E01B4
VS198;E01B5
VS199;E01B6
VS19;E0102
VS1;FE00
VS200;E01B7
VS201;E01B8
VS202;E01B9
VS203;E01BA
VS204;E01BB
VS205;E01BC
VS206;E01BD
VS207;E01BE
VS208;E01BF
VS209;E01C0
VS20;E0103
VS210;E01C1
VS211;E01C2
VS212;E01C3
VS213;E01C4
VS214;E01C5
VS215;E01C6
VS216;E01C7
VS217;E01C8
VS218;E01C9
VS219;E01CA
VS21;E0104
VS220;E01CB
VS221;E01CC
VS222;E01CD
VS223;E01CE
VS224;E01CF
VS225;E01D0
VS226;E01D1
VS227;E01D2
VS228;E01D3
VS229;E01D4
VS22;E0105
VS230;E01D5
VS231;E01D6
VS232;E01D7
VS233;E01D8
VS234;E01D9
VS235;E01DA
VS236;E01DB
VS237;E01DC
VS238;E01DD
VS239;E01DE
VS23;E0106
VS240;E01DF
VS241;E01E0
VS242;E01E1
VS243;E01E2
VS244;E01E3
VS245;E01E4
VS246;E01E5
VS247;E01E6
VS248;E01E7
VS249;E01E8
VS24;E0107
VS250;E01E9
VS251;E01EA
VS252;E01EB
VS253;E01EC
VS254;E01ED
VS255;E01EE
VS256;E01EF
VS25;E0108
VS26;E0109
VS27;E010A
VS28;E010B
VS29;E010C
VS2;FE01
VS30;E010D
VS31;E010E
VS32;E010F
VS33;E0110
VS34;E0111
VS35;E0112
VS36;E0113
VS37;E0114
VS38;E0115
VS39;E0116
VS3;FE02
VS40;E0117
VS41;E0118
VS42;E0119
VS43;E011A
VS44;E011B
VS45;E011C
VS46;E011D
VS47;E011E
VS48;E011F
VS49;E0120
VS4;FE03
VS50;E0121
VS51;E0122
VS52;E0123
VS53;E0124
VS54;E0125
VS55;E0126
VS56;E0127
VS57;E0128
VS58;E0129
VS59;E012A
VS5;FE04
VS60;E012B
VS61;E012C
VS62;E012D
VS63;E012E
VS64;E012F
VS65;E0130
VS66;E0131
VS67;E0132
VS68;E0133
VS69;E0134
VS6;FE05
VS70;E0135
VS71;E0136
VS72;E0137
VS73;E0138
VS74;E0139
VS75;E013A
VS76;E013B
VS77;E013C
VS78;E013D
VS79;E013E
VS7;FE06
VS80;E013F
VS81;E0140
VS82;E0141
VS83;E0142
VS84;E0143
VS85;E0144
VS86;E0145
VS87;E0146
VS88;E0147
VS89;E0148
VS8;FE07
VS90;E0149
VS91;E014A
VS92;E014B
VS93;E014C
VS94;E014D
VS95;E014E
VS96;E014F
VS97;E0150
VS98;E0151
VS99;E0152
VS9;FE08
VT;000B
VTS;008A
WEIERSTRASS ELLIPTIC FUNCTION;2118
WJ;2060
YI SYLLABLE ITERATION MARK;A015
ZWJ;200D
ZWNBSP;FEFF
ZWNJ;200C
ZWSP;200B"""

# ---------------------------------------------------------------------------
# Derived names (the prefix branches of CPython's _getcode).

HANGUL_JAMO = [
    ("G", "A", ""), ("GG", "AE", "G"), ("N", "YA", "GG"),
    ("D", "YAE", "GS"), ("DD", "EO", "N"), ("R", "E", "NJ"),
    ("M", "YEO", "NH"), ("B", "YE", "D"), ("BB", "O", "L"),
    ("S", "WA", "LG"), ("SS", "WAE", "LM"), ("", "OE", "LB"),
    ("J", "YO", "LS"), ("JJ", "U", "LT"), ("C", "WEO", "LP"),
    ("K", "WE", "LH"), ("T", "WI", "M"), ("P", "YU", "B"),
    ("H", "EU", "BS"), ("", "YI", "S"), ("", "I", "SS"),
    ("", "", "NG"), ("", "", "J"), ("", "", "C"),
    ("", "", "K"), ("", "", "T"), ("", "", "P"),
    ("", "", "H"),
]

DERIVED_RANGES = [
    (0x3400, 0x4DBF, 1),
    (0x4E00, 0x9FFF, 1),
    (0xAC00, 0xD7A3, 0),
    (0x17000, 0x187F7, 2),
    (0x18D00, 0x18D08, 2),
    (0x20000, 0x2A6DF, 1),
    (0x2A700, 0x2B739, 1),
    (0x2B740, 0x2B81D, 1),
    (0x2B820, 0x2CEA1, 1),
    (0x2CEB0, 0x2EBE0, 1),
    (0x2EBF0, 0x2EE5D, 1),
    (0x30000, 0x3134A, 1),
    (0x31350, 0x323AF, 1),
]

DERIVED_PREFIXES = (
    "HANGUL SYLLABLE ",
    "CJK UNIFIED IDEOGRAPH-",
    "TANGUT IDEOGRAPH-",
)


def prefix_id(cp):
    """CPython's find_prefix_id."""
    for first, last, pid in DERIVED_RANGES:
        if cp < first:
            return -1
        if cp <= last:
            return pid
    return -1


def derived_name(cp):
    """The name CPython accepts for a derived-range code point."""
    pid = prefix_id(cp)
    if pid == 0:
        index = cp - 0xAC00
        l = index // 588
        v = (index % 588) // 28
        t = index % 28
        return "HANGUL SYLLABLE " + HANGUL_JAMO[l][0] + HANGUL_JAMO[v][1] + HANGUL_JAMO[t][2]
    if pid == 1:
        return "CJK UNIFIED IDEOGRAPH-%04X" % cp
    if pid == 2:
        return "TANGUT IDEOGRAPH-%04X" % cp
    raise AssertionError("not a derived code point: U+%04X" % cp)


def hangul_find(s, column, count):
    """CPython's find_syllable (longest prefix match, empty entries match).

    CPython passes LCount=19, VCount=21, TCount=28; rows beyond those counts
    have no short name for that column.
    """
    pos, length = -1, -1
    for i, row in enumerate(HANGUL_JAMO[:count]):
        entry = row[column]
        if len(entry) <= length:
            continue
        if s.startswith(entry):
            pos, length = i, len(entry)
    if length < 0:
        length = 0
    return pos, length


def derived_lookup(name):
    """Python mirror of the Go derivedUnicodeName (used to check the tables)."""
    if name.startswith("HANGUL SYLLABLE "):
        rest = name[len("HANGUL SYLLABLE "):]
        l, n = hangul_find(rest, 0, 19)
        rest = rest[n:]
        v, n = hangul_find(rest, 1, 21)
        rest = rest[n:]
        t, n = hangul_find(rest, 2, 28)
        rest = rest[n:]
        if l < 0 or v < 0 or t < 0 or rest:
            return None
        return 0xAC00 + (l * 21 + v) * 28 + t
    for prefix, pid in (("CJK UNIFIED IDEOGRAPH-", 1), ("TANGUT IDEOGRAPH-", 2)):
        if name.startswith(prefix):
            digits = name[len(prefix):]
            if not 4 <= len(digits) <= 6 or digits[0] == "0":
                return None
            if any(c not in "0123456789ABCDEF" for c in digits):
                return None
            cp = int(digits, 16)
            if cp > 0x10FFFF or prefix_id(cp) != pid:
                return None
            return cp
    return None


# ---------------------------------------------------------------------------
# Building the name table


def build_name_table():
    """Return {upper-case name: code point} for every name \\N accepts."""
    warnings.simplefilter("ignore")

    names = {}
    for cp in range(0x110000):
        name = unicodedata.name(chr(cp), None)
        if name is None:
            continue
        if name.startswith(DERIVED_PREFIXES):
            # Derived names are computed by the Go decoder; verify that the
            # ranges and the name agree.
            assert derived_lookup(name.upper()) == cp, (name, cp)
            continue
        assert name == name.upper(), name
        if name in names:
            assert names[name] == cp, (name, names[name], cp)
        names[name] = cp

    for line in ALIASES.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        name, hex_cp = line.rsplit(";", 1)
        cp = int(hex_cp, 16)
        # The DAWG's alias entries and the canonical entries can collide;
        # the reference Python decides which one wins.
        decoded = ("\\N{" + name + "}").encode("ascii").decode("unicode_escape")
        assert len(decoded) == 1 and ord(decoded) == cp, (name, cp, decoded)
        assert name == name.upper(), name
        names[name] = cp

    # Check every stored name exactly the way the Go decoder will be used.
    for name, cp in names.items():
        assert name.isascii(), name
        decoded = ("\\N{" + name + "}").encode("ascii").decode("unicode_escape")
        assert len(decoded) == 1 and ord(decoded) == cp, (name, cp, decoded)

    # Check the derived branches for every code point in every range.
    for first, last, pid in DERIVED_RANGES:
        for cp in range(first, last + 1):
            name = derived_name(cp)
            assert derived_lookup(name) == cp, (name, cp)
            assert unicodedata.lookup(name) == chr(cp), name
            if pid == 0:
                assert unicodedata.name(chr(cp)) == name, (name, cp)

    # The ranges must be the complete set of derived names; the name-by-name
    # checks above compare each range with unicodedata.lookup/name.
    return names


def gzip_bytes(raw):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    return buf.getvalue()


def name_table_bytes(names):
    lines = ["%X %s\n" % (cp, name) for name, cp in sorted(names.items())]
    return gzip_bytes("".join(lines).encode("ascii"))


# ---------------------------------------------------------------------------
# Corpus construction

ASCII_PRINTABLE = [chr(c) for c in range(0x20, 0x7F)]
SURROGATES = [0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0xD83D, 0xDE00]
BOM = "\ufeff"


def rand_bytes(rng, maxlen=40, alphabet=None):
    n = rng.randint(0, maxlen)
    if alphabet is None:
        return bytes(rng.randrange(256) for _ in range(n))
    return bytes(rng.choice(alphabet) for _ in range(n))


def random_text(rng, maxlen=40, surrogates=False):
    n = rng.randint(0, maxlen)
    out = []
    for _ in range(n):
        r = rng.random()
        if surrogates and r < 0.06:
            out.append(chr(rng.choice(SURROGATES)))
        elif r < 0.50:
            out.append(rng.choice(ASCII_PRINTABLE))
        elif r < 0.70:
            out.append(chr(rng.randrange(0x80, 0x800)))
        elif r < 0.82:
            out.append(chr(rng.randrange(0x800, 0xD800)))
        elif r < 0.86:
            out.append(BOM)
        else:
            out.append(chr(rng.randrange(0x10000, 0x110000)))
    return "".join(out)


def mutate(rng, data, count=3):
    out = bytearray(data)
    for _ in range(count):
        op = rng.random()
        if op < 0.4 and out:
            out[rng.randrange(len(out))] = rng.randrange(256)
        elif op < 0.7 and out:
            del out[rng.randrange(len(out)):]
        else:
            out.insert(rng.randrange(len(out) + 1), rng.randrange(256))
    return bytes(out)


def py_result(data, codec, errors):
    """codecs.decode(...) result, in a JSON-friendly shape."""
    try:
        text = codecs.decode(data, codec, errors)
    except UnicodeDecodeError as exc:
        return {
            "kind": "decode",
            "encoding": exc.encoding,
            "start": exc.start,
            "end": exc.end,
            "reason": exc.reason,
            "text": str(exc),
        }
    except UnicodeError as exc:
        return {"kind": "unicode", "text": str(exc)}
    return {"kind": "text", "cp": [ord(c) for c in text]}


def bad_span(result):
    return result["kind"] == "decode" and (
        abs(result["start"]) > (1 << 40) or abs(result["end"]) > (1 << 40)
    )


def add_cases(corpus, codec, inputs, handlers=ALL_HANDLERS):
    # Deduplicate first, then compute every result, then pick a deterministic
    # even spread so that special cases at any position of the pool survive
    # the trim to TARGET.
    unique = []
    seen = set()
    for data in inputs:
        data = bytes(data)
        if data in seen:
            continue
        seen.add(data)
        unique.append(data)

    kept = []
    for data in unique:
        results = {h: py_result(data, codec, h) for h in handlers}
        if any(bad_span(r) for r in results.values()):
            # Only pathological punycode strings reach this; their Python
            # positions are Python big integers that DecodeError cannot hold.
            continue
        kept.append((data, results))

    if len(kept) > TARGET:
        step = len(kept) / TARGET
        kept = [kept[int(i * step)] for i in range(TARGET)]

    assert len(kept) >= 2000, (codec, len(kept))
    assert codec not in corpus
    corpus[codec] = [{"data": data.hex(), "r": results} for data, results in kept]


# --- per-codec input pools -------------------------------------------------


def valid_encoding_inputs(codec, rng, count=90, encode_errors="surrogatepass"):
    """Valid encodings, every truncation, and a few mutations."""
    out = []
    for _ in range(count):
        text = random_text(rng, 40, surrogates=True)
        try:
            data = text.encode(codec, encode_errors)
        except UnicodeEncodeError:
            data = text.encode(codec)
        out.append(data)
        for k in range(len(data) + 1):
            out.append(data[:k])
        for _ in range(2):
            out.append(mutate(rng, data))
    return out


def utf8_inputs(rng):
    out = valid_encoding_inputs("utf-8", rng)
    out += [
        b"", b"a", b"\xef\xbb\xbf", b"\xef\xbb\xbfa", b"a\xef\xbb\xbf",
        b"\xef\xbb", b"\xef", b"\x80", b"\xbf", b"\xc0\x80", b"\xc1\xbf",
        b"\xc2", b"\xc2\x80", b"\xdf\xbf", b"\xe0", b"\xe0\x80\x80",
        b"\xe0\xa0", b"\xe0\xa0\x80", b"\xed\xa0\x80", b"\xed\xbf\xbf",
        b"\xf0", b"\xf0\x80\x80\x80", b"\xf0\x90\x80", b"\xf0\x90\x80\x80",
        b"\xf4\x8f\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf5", b"\xff",
    ]
    for _ in range(700):
        out.append(rand_bytes(rng))
    biased = [0x00, 0x7F, 0x80, 0x9F, 0xBF, 0xC0, 0xC1, 0xC2, 0xDF,
              0xE0, 0xE1, 0xEC, 0xED, 0xEE, 0xEF, 0xF0, 0xF1, 0xF4, 0xF5, 0xFF]
    for _ in range(400):
        out.append(rand_bytes(rng, 40, biased))
    for data in out[:800]:
        out.append(b"\xef\xbb\xbf" + data)
    return out


def utf8_sig_inputs(rng):
    out = list(utf8_inputs(rng))
    out += [
        b"\xef\xbb\xbf\xef\xbb\xbf", b"\xef\xbb\xbf\xef\xbb",
        b"\xef\xbb\xbf\xff", b"\xef\xbb\xbf\xc2", b"\xef\xbb\xbf\xed\xa0\x80",
    ]
    return out


UTF16_SPECIAL = [
    b"", b"\xff\xfe", b"\xfe\xff", b"\xff", b"\xfe", b"\xff\xfeA",
    b"\xfe\xff\x00A", b"\xff\xfeA\x00", b"\xfe\xff\x00A\x00\x00",
    b"\x00\xd8", b"\x00\xd8A\x00", b"\x00\xd8\x00\xdc", b"\x00\xdc",
    b"\x00\xd8\x00\xd8", b"\x00\xd8\x00\xd8\x00\xdc", b"\xdc\x00",
    b"\xff\xfe\x00\xdc", b"\xff\xfe\x00\xd8", b"\xff\xfe\x00\xd8\x00\xdc",
    b"\xff\xfe\x00\xd8A\x00", b"\xfe\xff\xd8\x00", b"\xfe\xff\xd8\x00\xdc\x00",
    b"\xfe\xff\xd8\x00\x00\x41",
]


def utf16_inputs(rng, codec):
    out = valid_encoding_inputs(codec, rng, count=80)
    out += list(UTF16_SPECIAL)
    text = random_text(rng, 30, surrogates=True)
    le = text.encode("utf-16-le", "surrogatepass")
    be = text.encode("utf-16-be", "surrogatepass")
    if codec == "utf-16":
        out += [text.encode("utf-16", "surrogatepass"), b"\xff\xfe" + le,
                b"\xfe\xff" + be, le, be, b"\xff\xfe" + be, b"\xfe\xff" + le]
        for data in (b"\xff\xfe" + le, b"\xfe\xff" + be):
            for k in range(len(data) + 1):
                out.append(data[:k])
    elif codec == "utf-16-le":
        out += [le, b"\xff\xfe" + le, b"\xfe\xff" + le]
    else:
        out += [be, b"\xfe\xff" + be, b"\xff\xfe" + be]
    for _ in range(650):
        out.append(rand_bytes(rng))
    biased = [0x00, 0x01, 0x41, 0x7F, 0x80, 0xD8, 0xDC, 0xDB, 0xDF, 0xFE, 0xFF]
    for _ in range(450):
        out.append(rand_bytes(rng, 40, biased))
    return out


def pack32(value, little=True):
    return value.to_bytes(4, "little" if little else "big")


UTF32_SPECIAL = [
    b"", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff", b"\xff\xfe",
    b"\xff\xfe\x00", b"A", b"A\x00", b"A\x00\x00", b"A\x00\x00\x00",
    b"\x00\xd8\x00\x00", b"\x00\xdc\x00\x00", b"\x00\x00\x11\x00",
    b"\x00\x00\x00\xd8", b"\xff\xfe\x00\x00\x00\xd8\x00\x00",
    b"\xff\xfe\x00\x00A\x00\x00\x00", b"\x00\x00\xfe\xff\x00\x00\x00A",
    b"\xff\xfe\x00\x00\x00\x00\x11\x00", b"\x00\x00\xfe\xff\x00\x00\xd8\x00",
]


def utf32_inputs(rng, codec):
    out = valid_encoding_inputs(codec, rng, count=80)
    out += list(UTF32_SPECIAL)
    text = random_text(rng, 30, surrogates=True)
    le = text.encode("utf-32-le", "surrogatepass")
    be = text.encode("utf-32-be", "surrogatepass")
    if codec == "utf-32":
        out += [text.encode("utf-32", "surrogatepass"), b"\xff\xfe\x00\x00" + le,
                b"\x00\x00\xfe\xff" + be, le, be, b"\xff\xfe\x00\x00" + be,
                b"\x00\x00\xfe\xff" + le]
        for data in (b"\xff\xfe\x00\x00" + le, b"\x00\x00\xfe\xff" + be):
            for k in range(len(data) + 1):
                out.append(data[:k])
    elif codec == "utf-32-le":
        out += [le, b"\xff\xfe\x00\x00" + le, b"\x00\x00\xfe\xff" + le]
    else:
        out += [be, b"\x00\x00\xfe\xff" + be, b"\xff\xfe\x00\x00" + be]
    # Explicit surrogate and out-of-range units in both orders.
    for value in (0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0x110000, 0xFFFFFFFF, 0x7FFFFFFF):
        out.append(pack32(value, codec != "utf-32-be"))
        out.append(pack32(value, codec == "utf-32-be"))
    for _ in range(650):
        out.append(rand_bytes(rng))
    biased = [0x00, 0x01, 0x41, 0x7F, 0x80, 0xD8, 0xDC, 0xFE, 0xFF]
    for _ in range(450):
        out.append(rand_bytes(rng, 40, biased))
    return out


UTF7_BASE64 = list(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)
UTF7_SPECIAL = [
    b"", b"+", b"+-", b"a+b", b"+A", b"+A-", b"+AB-", b"+ABC-", b"+ABD-",
    b"+ABE-", b"+2AA-", b"+2AA", b"+2AA!", b"+A!", b"+!", b"+!!", b"\xff",
    b"a\xffb", b"+2AA+", b"+2AA+A-", b"+2AA-+", b"+ZeVnLIqe-", b"+AGEAYgBj-",
    b"+2D3eAA-", b"\x80", b"\x7f", b"+\x80-", b"+A\x80-", b"+AA\x80",
]


def utf7_inputs(rng):
    out = valid_encoding_inputs("utf-7", rng, count=90, encode_errors="strict")
    out += list(UTF7_SPECIAL)
    prefixes = [b"", b"a", b"+", b"\xff", b"~", b"+2AA-", b"a+AA-"]
    suffixes = [b"", b"-", b"+", b"!", b"\xff", b"a", b"\x00"]
    terms = [b"", b"-", b"!", b"\\", b"\xff", b" ", b"A", b"+", b".", b"\x00", b"~"]
    for _ in range(800):
        n = rng.randint(0, 20)
        shift = "".join(rng.choice(UTF7_BASE64) for _ in range(n)).encode("ascii")
        out.append(rng.choice(prefixes) + b"+" + shift + rng.choice(terms) +
                   rng.choice(suffixes))
    for _ in range(500):
        out.append(rand_bytes(rng))
    for _ in range(300):
        out.append(rand_bytes(rng, 40, [0x2B, 0x2D, 0x41, 0x42, 0x2F, 0x30, 0x39,
                                        0x7F, 0x80, 0xFF]))
    return out


NAMED_SEQUENCES = [
    "KEYCAP DIGIT ONE",
    "LATIN SMALL LETTER A WITH ACUTE, COMBINING ACUTE ACCENT",
    "LATIN SMALL LETTER G WITH ACUTE, COMBINING ACUTE ACCENT",
    "TAMIL SYLLABLE SAI",
]

JUNK_NAMES = [
    "", "X", "UNKNOWN NAME", "LATIN SMALL LETTER A ", " LATIN SMALL LETTER A",
    "LATIN  SMALL LETTER A", "NULL NAME", "HANGUL SYLLABLE ", "HANGUL SYLLABLE Q",
    "CJK UNIFIED IDEOGRAPH-", "CJK UNIFIED IDEOGRAPH-03400",
    "CJK UNIFIED IDEOGRAPH-4E00 ", "TANGUT IDEOGRAPH-110000",
    "TANGUT IDEOGRAPH-0041", "BELL", "NUL", "LF", "CR", "BYTE ORDER MARK",
    "ZWNBSP", "FEMALE SIGN", "KEYCAP DIGIT ONE", "SNOWMAN",
]


def name_pool(names, rng):
    """A mix of valid names (with case variants), aliases and junk."""
    canonical = list(names.items())
    pool = []
    for _ in range(800):
        name, cp = rng.choice(canonical)
        pool.append((name, cp))
    for line in ALIASES.strip().splitlines():
        line = line.strip()
        if line:
            name, hex_cp = line.rsplit(";", 1)
            pool.append((name, int(hex_cp, 16)))
    for first, last, _pid in DERIVED_RANGES:
        for _ in range(30):
            cp = rng.randint(first, last)
            pool.append((derived_name(cp), cp))
    return pool


def vary_case(rng, name):
    r = rng.random()
    if r < 0.18:
        return name.lower()
    if r < 0.30:
        return name.swapcase()
    if r < 0.45:
        return "".join(c.lower() if rng.random() < 0.5 else c for c in name)
    return name


HEX_DIGITS = "0123456789abcdefABCDEF"


def escape_piece(rng, pool):
    kind = rng.randrange(10)
    if kind == 0:  # \xXX, possibly bad or truncated
        tail = rng.choice([
            "".join(rng.choice(HEX_DIGITS) for _ in range(2)),
            "".join(rng.choice(HEX_DIGITS) for _ in range(rng.randint(0, 1))),
            rng.choice("gGz!# "),
        ])
        return (b"\\x" + tail.encode("ascii", "replace"))
    if kind == 1:  # \uXXXX
        tail = rng.choice([
            "".join(rng.choice(HEX_DIGITS) for _ in range(4)),
            "".join(rng.choice(HEX_DIGITS) for _ in range(rng.randint(0, 3))),
            "12" + rng.choice("gGz!# "),
        ])
        return (b"\\u" + tail.encode("ascii", "replace"))
    if kind == 2:  # \UXXXXXXXX
        tail = rng.choice([
            "".join(rng.choice(HEX_DIGITS) for _ in range(8)),
            "".join(rng.choice(HEX_DIGITS) for _ in range(rng.randint(0, 7))),
            "0001F60" + rng.choice("gGz!"),
            "00110000",
            "FFFFFFFF",
        ])
        return (b"\\U" + tail.encode("ascii", "replace"))
    if kind == 3:  # \N{name}
        name, _cp = rng.choice(pool)
        return b"\\N{" + vary_case(rng, name).encode("utf-8") + b"}"
    if kind == 4:  # bad \N forms
        return rng.choice([
            b"\\N", b"\\N{", b"\\N{}", b"\\N{A", b"\\Nx", b"\\N ",
            b"\\N{" + rng.choice(JUNK_NAMES).encode() + b"}",
            b"\\N{" + rng.choice(NAMED_SEQUENCES).encode() + b"}",
        ])
    if kind == 5:  # octal
        return b"\\" + bytes(rng.choice(b"01234567") for _ in range(rng.randint(1, 4)))
    if kind == 6:  # classic escapes
        return b"\\" + bytes([rng.choice(b"\\'\"bfnrtva\n")])
    if kind == 7:  # invalid escape keeps both bytes
        return b"\\" + bytes([rng.randrange(0x21, 0x7F)])
    if kind == 8:  # literal (possibly non-ASCII) bytes
        return rand_bytes(rng, 6)
    return b"\\"


def escape_inputs(rng, names, pool):
    out = valid_encoding_inputs("unicode-escape", rng, count=70)
    out += [b"", b"\\", b"a\\", b"\\x", b"\\x1", b"\\xzz", b"\\u", b"\\u1",
            b"\\u12g", b"\\U", b"\\U00110000", b"\\UFFFFFFFF",
            b"\\U0001F600", b"\\N", b"\\N{", b"\\N{}", b"\\N{A", b"\\N{x}",
            b"\\Nx", b"\\q", b"\\777", b"\\400", b"\\0", b"\\08",
            b"\\n\\t\\r\\a\\b\\f\\v", b"\\\"\\'", b"\xe9"]
    for _ in range(1400):
        pieces = [escape_piece(rng, pool) for _ in range(rng.randint(0, 8))]
        out.append(b"".join(pieces))
    for _ in range(400):
        out.append(rand_bytes(rng))
    return out


def raw_escape_inputs(rng, pool):
    out = valid_encoding_inputs("raw-unicode-escape", rng, count=70)
    out += [b"", b"\\", b"a\\", b"\\x", b"\\xzz", b"\\u", b"\\u1", b"\\u12g",
            b"\\U", b"\\U00110000", b"\\UFFFFFFFF", b"\\U0001F600",
            b"\\N{LATIN SMALL LETTER A}", b"\\q", b"\\777", b"\xe9"]
    for _ in range(1300):
        pieces = []
        for _ in range(rng.randint(0, 8)):
            kind = rng.randrange(6)
            if kind == 0:
                tail = rng.choice([
                    "".join(rng.choice(HEX_DIGITS) for _ in range(4)),
                    "".join(rng.choice(HEX_DIGITS) for _ in range(rng.randint(0, 3))),
                    "12" + rng.choice("gGz!# "),
                ])
                pieces.append(b"\\u" + tail.encode("ascii", "replace"))
            elif kind == 1:
                tail = rng.choice([
                    "".join(rng.choice(HEX_DIGITS) for _ in range(8)),
                    "".join(rng.choice(HEX_DIGITS) for _ in range(rng.randint(0, 7))),
                    "00110000", "FFFFFFFF",
                ])
                pieces.append(b"\\U" + tail.encode("ascii", "replace"))
            elif kind == 2:
                pieces.append(b"\\x" + rand_bytes(rng, 2))
            elif kind == 3:
                pieces.append(b"\\N{" + rng.choice(pool)[0].encode() + b"}")
            elif kind == 4:
                pieces.append(b"\\" + bytes([rng.randrange(0x21, 0x7F)]))
            else:
                pieces.append(rand_bytes(rng, 6))
        out.append(b"".join(pieces))
    for _ in range(400):
        out.append(rand_bytes(rng))
    return out


PUNY_BASE64 = list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")


def punycode_inputs(rng):
    out = valid_encoding_inputs("punycode", rng, count=120, encode_errors="strict")
    out += [b"", b"-", b"a", b"a-", b"-a", b"-z", b"a-z", b"-a-", b"abc-",
            b"abc-!", b"abc-\xff", b"\xff-abc", b"\xff\xfe-", b"-999999999999999999999999",
            b"bcher-kva", b"mnchen-3ya", b"wgv71a119e", b"xn--bcher-kva"]
    for _ in range(900):
        out.append(rand_bytes(rng))
    for _ in range(900):
        n = rng.randint(0, 14)
        ext = "".join(rng.choice(PUNY_BASE64) for _ in range(n))
        if rng.random() < 0.25:
            ext = "".join(c.lower() if rng.random() < 0.5 else c for c in ext)
        base = rng.choice([
            b"", b"a", b"abc", b"hello", b"xn--", b"\xff", b"a\xffb",
            bytes(rng.randrange(0x20, 0x7F) for _ in range(rng.randint(0, 8))),
        ])
        if rng.random() < 0.8:
            out.append(base + b"-" + ext.encode("ascii"))
        else:
            out.append(base + ext.encode("ascii"))
    return out


IDNA_PIECES = [
    b"", b".", b"..", b"xn--", b"xn--bcher-kva", b"example", b"EXAMPLE",
    b"example.com", b"a.b.c.", b"\xff", b"\xff\xfe", b"x\x80y",
    b"\xc3\xbc", b"\xe2\x98\x83", b"\xf0\x9f\x98\x80",
    b"xn---", b"xn--a", b"xn--zzzz", b"a..b", b".",
]


def idna_inputs(rng):
    out = list(IDNA_PIECES)
    for _ in range(1200):
        out.append(rand_bytes(rng))
    for _ in range(900):
        out.append(random_text(rng, 30).encode("utf-8", "surrogatepass"))
    for _ in range(500):
        out.append(rng.choice(IDNA_PIECES) + rand_bytes(rng, 20) +
                   rng.choice(IDNA_PIECES))
    return out


def undefined_inputs(rng):
    out = [b"", b"a", b"\x00", b"\xff"]
    for _ in range(1500):
        out.append(rand_bytes(rng))
    for _ in range(700):
        out.append(random_text(rng, 30, surrogates=True).encode("utf-8", "surrogatepass"))
    return out


def build_corpus(names):
    rng = random.Random(SEED)
    pool = name_pool(names, rng)
    corpus = {}

    add_cases(corpus, "utf-8", utf8_inputs(rng))
    add_cases(corpus, "utf-8-sig", utf8_sig_inputs(rng))
    add_cases(corpus, "utf-16", utf16_inputs(rng, "utf-16"))
    add_cases(corpus, "utf-16-le", utf16_inputs(rng, "utf-16-le"))
    add_cases(corpus, "utf-16-be", utf16_inputs(rng, "utf-16-be"))
    add_cases(corpus, "utf-32", utf32_inputs(rng, "utf-32"))
    add_cases(corpus, "utf-32-le", utf32_inputs(rng, "utf-32-le"))
    add_cases(corpus, "utf-32-be", utf32_inputs(rng, "utf-32-be"))
    add_cases(corpus, "utf-7", utf7_inputs(rng))
    add_cases(corpus, "unicode-escape", escape_inputs(rng, names, pool))
    add_cases(corpus, "raw-unicode-escape", raw_escape_inputs(rng, pool))
    add_cases(corpus, "punycode", punycode_inputs(rng))
    add_cases(corpus, "idna", idna_inputs(rng), handlers=IDNA_HANDLERS)
    add_cases(corpus, "undefined", undefined_inputs(rng))
    return corpus


# ---------------------------------------------------------------------------


def main(argv):
    warnings.simplefilter("ignore")
    check = "--check" in argv[1:]

    names = build_name_table()
    table = name_table_bytes(names)
    corpus = build_corpus(names)
    corpus_blob = gzip_bytes(
        json.dumps(corpus, sort_keys=True, separators=(",", ":")).encode("ascii")
    )

    if check:
        stale = []
        for path, blob in ((NAMES_FILE, table), (CORPUS_FILE, corpus_blob)):
            try:
                with open(path, "rb") as f:
                    current = f.read()
            except FileNotFoundError:
                current = None
            if current != blob:
                stale.append(path)
        if stale:
            for path in stale:
                print("out of date: %s" % path, file=sys.stderr)
            return 1
        print("unicode tables are up to date")
        return 0

    with open(NAMES_FILE, "wb") as f:
        f.write(table)
    with open(CORPUS_FILE, "wb") as f:
        f.write(corpus_blob)

    print("unicode_names.bin.gz: %d names, %d bytes" % (len(names), len(table)))
    for codec in sorted(corpus):
        print("  %-20s %4d inputs" % (codec, len(corpus[codec])))
    print("corpus_unicode.json.gz: %d bytes" % len(corpus_blob))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
