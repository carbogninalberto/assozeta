"""Writes the CPython 3.13 codec tables of package pycodecs.

Run with the reference interpreter, from anywhere:

    $PY -I testdata/gen_tables.py           # write the files
    $PY -I testdata/gen_tables.py --check   # regenerate in memory, fail on a diff

It writes three files, all derived from CPython's own decoders:

  singlebyte_gen.go              the 8-bit codecs, one 256-rune table each
  cjk_tables.bin.gz              the stateless multibyte codecs as byte tries
  testdata/corpus_tables.json.gz the differential corpus (four error handlers)

Nothing here is written from memory: every expected code point, error span and
error reason comes from bytes.decode in the running interpreter.
"""

import codecs
import encodings
import gzip
import json
import os
import pkgutil
import random
import sys
import warnings
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SINGLE_GO = os.path.join(PKG, "singlebyte_gen.go")
CJK_BIN = os.path.join(PKG, "cjk_tables.bin.gz")
CORPUS = os.path.join(HERE, "corpus_tables.json.gz")

SEED = 20261116
# Inputs per codec. The single-byte tables cover every byte already, so their
# corpus only checks the decoder loop and the handlers; the multibyte tries get
# more (the exhaustive 1-, 2- and 3-byte walk is the generator's own check).
CASES_PER_CODEC = 600
CASES_PER_SINGLE_BYTE_CODEC = 60

# The stateless multibyte codecs of _multibytecodec (Modules/cjkcodecs). They
# decode one character at a time from a position: at most three bytes, except
# gb18030's four-byte sequences and euc_kr's eight-byte KS X 1001:1998 make-up
# form, which are stored as special trie nodes.
MULTIBYTE = [
    "big5", "big5hkscs", "cp932", "cp949", "cp950",
    "euc_jis_2004", "euc_jisx0213", "euc_jp", "euc_kr", "gb18030",
    "gb2312", "gbk", "johab", "shift_jis", "shift_jis_2004", "shift_jisx0213",
]

# The 8-bit codecs of CPython 3.13.16: ascii, charmap and the code-page tables
# (cp*, iso8859-*, koi8-*, mac-*, hp-roman8, kz1048, palmos, ptcp154, tis-620).
# The generator recomputes this set from the property in single_byte_scan() and
# fails if it changes, because pyheader's charset names resolve onto this list.
EXPECTED_SINGLE_BYTE = frozenset("""
ascii charmap cp037 cp1006 cp1026 cp1125 cp1140 cp1250 cp1251 cp1252 cp1253
cp1254 cp1255 cp1256 cp1257 cp1258 cp273 cp424 cp437 cp500 cp720 cp737 cp775
cp850 cp852 cp855 cp856 cp857 cp858 cp860 cp861 cp862 cp863 cp864 cp865 cp866
cp869 cp874 cp875 hp-roman8 iso8859-1 iso8859-2 iso8859-3 iso8859-4 iso8859-5
iso8859-6 iso8859-7 iso8859-8 iso8859-9 iso8859-10 iso8859-11 iso8859-13
iso8859-14 iso8859-15 iso8859-16 koi8-r koi8-t koi8-u kz1048 mac-arabic
mac-croatian mac-cyrillic mac-farsi mac-greek mac-iceland mac-latin2 mac-roman
mac-romanian mac-turkish palmos ptcp154 tis-620
""".split())

# singleByteUndefined in the generated Go: U+FFFF is a noncharacter no CPython
# 8-bit codec maps a byte to, so it marks an undefined byte.
SENTINEL = 0xFFFF

BYTES = [bytes([b]) for b in range(256)]
HANDLERS = ("strict", "replace", "surrogateescape", "ignore")


# --------------------------------------------------------------------------
# codec discovery


def candidate_codecs():
    """codecs.lookup(name).name for every module in the encodings package."""
    names = set()
    for info in pkgutil.iter_modules(encodings.__path__):
        if info.name.startswith("_"):
            continue
        try:
            names.add(codecs.lookup(info.name).name)
        except LookupError:
            pass
    return sorted(names)


def probe_pair(name, a, b, cps):
    """Whether bytes([a, b]) decodes as the concatenation of bytes a and b.

    A byte the codec does not map makes the pair fail at that byte under
    errors='strict', and contributes U+FFFD ('replace') or nothing ('ignore'),
    independently of its neighbour. That is what a stateless byte table does;
    codecs with a different answer (utf-32, idna, ...) are not 8-bit tables.
    """
    pair = BYTES[a] + BYTES[b]
    dec = codecs.getdecoder(name)
    if cps[a] is not None and cps[b] is not None:
        try:
            text, consumed = dec(pair)
        except UnicodeDecodeError:
            return False
        if consumed != 2 or text != chr(cps[a]) + chr(cps[b]):
            return False
    else:
        first = 0 if cps[a] is None else 1
        try:
            dec(pair)
            return False
        except UnicodeDecodeError as e:
            if (e.start, e.end) != (first, first + 1):
                return False
        except Exception:
            return False
    want = "".join(chr(c) if c is not None else "\ufffd" for c in (cps[a], cps[b]))
    try:
        text = pair.decode(name, "replace")
    except Exception:
        return False
    if text != want:
        return False
    want = "".join(chr(c) for c in (cps[a], cps[b]) if c is not None)
    try:
        text = pair.decode(name, "ignore")
    except Exception:
        return False
    return text == want


def single_byte_scan(name):
    """The byte -> code point table of an 8-bit codec, or None.

    A codec is single-byte when every one-byte input decodes to exactly one
    character or raises UnicodeDecodeError(0, 1), and every two-byte input is
    the concatenation of the two one-byte results (see probe_pair).
    """
    dec = codecs.getdecoder(name)
    cps = []
    reasons = set()
    encodings_seen = set()
    for b in range(256):
        try:
            text, consumed = dec(BYTES[b])
        except UnicodeDecodeError as e:
            if (e.start, e.end) != (0, 1):
                return None
            cps.append(None)
            reasons.add(e.reason)
            encodings_seen.add(e.encoding)
            continue
        except Exception:
            return None
        if consumed != 1 or len(text) != 1:
            return None
        cps.append(ord(text))
    for a in range(256):
        for b in range(256):
            if not probe_pair(name, a, b, cps):
                return None
    if len(reasons) > 1:
        raise AssertionError((name, "several reasons", reasons))
    if len(encodings_seen) > 1:
        raise AssertionError((name, "several error codec names", encodings_seen))
    if SENTINEL in cps:
        raise AssertionError((name, "maps a byte to the sentinel"))
    # The error names the codec object that raised it: the charmap-based code
    # pages report "charmap", not their registry name.
    return cps, (reasons.pop() if reasons else ""), (encodings_seen.pop() if encodings_seen else "")


def single_byte_codecs():
    found = {}
    for name in candidate_codecs():
        table = single_byte_scan(name)
        if table is not None:
            found[name] = table
    if frozenset(found) != EXPECTED_SINGLE_BYTE:
        raise AssertionError(
            "8-bit codec set changed: extra %s, missing %s"
            % (sorted(frozenset(found) - EXPECTED_SINGLE_BYTE),
               sorted(EXPECTED_SINGLE_BYTE - frozenset(found))))
    return found


# --------------------------------------------------------------------------
# trie building


# CPython's multibytecodec reports exactly these two reasons for every codec;
# the generator fails if a decoder ever says anything else.
ILLEGAL = "illegal multibyte sequence"
INCOMPLETE = "incomplete multibyte sequence"


def is_gb18030_four_byte(prefix):
    return (len(prefix) == 3
            and 0x81 <= prefix[0] <= 0xFE
            and 0x30 <= prefix[1] <= 0x39
            and 0x81 <= prefix[2] <= 0xFE)


def euc_kr_makeup():
    """The KS X 1001:1998 make-up form of CPython's euc_kr decoder.

    _codecs_kr.c DECODER(euc_kr): after 0xA4 0xD4 the decoder needs six more
    bytes, 0xA4 <cho> 0xA4 <jung> 0xA4 <jong>, and composes a Hangul syllable.
    Extra input before the eighth byte is "incomplete" over everything left;
    a rejected byte is an illegal one-byte sequence. The three byte -> index
    tables below are read off the decoder, and every 8-byte combination is
    checked against the composition 0xAC00 + cho*588 + jung*28 + jong.
    """
    base = 0xAC00
    tables = [[-1] * 256, [-1] * 256, [-1] * 256]
    fields = (3, 5, 7)
    sample = [0xA4, 0xD4, 0xA4, 0xA1, 0xA4, 0xBF, 0xA4, 0xD4]
    # Which byte each of the three fields accepts, and that every other byte is
    # an illegal one-byte sequence (other fields held at a valid value).
    accepted = []
    for table, pos in zip(tables, fields):
        good = []
        for value in range(256):
            seq = bytearray(sample)
            seq[pos] = value
            result = expect_decode(bytes(seq))
            if result[0] == "text":
                good.append(value)
            elif result != ("error", 0, 1, "illegal multibyte sequence"):
                raise AssertionError((seq.hex(), result))
        accepted.append(good)
    syllables = []
    for cho in accepted[0]:
        for jung in accepted[1]:
            for jong in accepted[2]:
                seq = bytes([0xA4, 0xD4, 0xA4, cho, 0xA4, jung, 0xA4, jong])
                result = expect_decode(seq)
                if result[0] != "text" or len(result[1]) != 1:
                    raise AssertionError((seq.hex(), result))
                offset = ord(result[1]) - base
                c, rest = divmod(offset, 588)
                j, k = divmod(rest, 28)
                for table, byte, value in ((tables[0], cho, c),
                                           (tables[1], jung, j),
                                           (tables[2], jong, k)):
                    if table[byte] not in (-1, value):
                        raise AssertionError((seq.hex(), byte, value))
                    table[byte] = value
                syllables.append(seq)
    for name, table, good in zip(("cho", "jung", "jong"), tables, accepted):
        indexes = [table[b] for b in good]
        if len(set(indexes)) != len(indexes) or min(indexes) < 0:
            raise AssertionError((name, "not a bijection", indexes))
        if any(table[b] != -1 for b in range(256) if b not in good):
            raise AssertionError((name, "an index outside the accepted bytes"))
    # One wrong byte at each of the three fixed positions is an illegal
    # one-byte sequence, and each prefix shorter than eight bytes is incomplete
    # over what is left.
    for pos in (2, 4, 6):
        for value in range(256):
            seq = bytearray(sample)
            seq[pos] = value
            result = expect_decode(bytes(seq))
            if value == 0xA4:
                continue
            if result != ("error", 0, 1, "illegal multibyte sequence"):
                raise AssertionError((seq.hex(), result))
    for length in range(2, 8):
        for value in (range(256) if length > 2 else [0xD4]):
            seq = bytes(sample[:length - 1]) + bytes([value])
            result = expect_decode(seq)
            if result != ("error", 0, length, "incomplete multibyte sequence"):
                raise AssertionError((seq.hex(), result))
    return {
        "total": 8,
        "required": [(2, 0xA4), (4, 0xA4), (6, 0xA4)],
        "fields": fields,
        "base": base,
        "scale": (588, 28),
        "tables": tables,
        "syllables": syllables,
    }


def expect_decode(seq, codec="euc_kr"):
    try:
        text = seq.decode(codec)
    except UnicodeDecodeError as e:
        return ("error", e.start, e.end, e.reason)
    return ("text", text)


def gb18030_four_byte_runs():
    """The 4-byte table of gb18030 as runs over the linear index.

    Index = ((b1-0x81)*10 + (b2-0x30))*1260 + (b3-0x81)*10 + (b4-0x30), the
    value CPython's gb18030 decoder accumulates. A run is either consecutive
    code points or a repeated error span.
    """
    dec = codecs.getdecoder("gb18030")
    runs = []
    run = None
    index = 0
    for b1 in range(0x81, 0xFF):
        for b2 in range(0x30, 0x3A):
            for b3 in range(0x81, 0xFF):
                for b4 in range(0x30, 0x3A):
                    seq = bytes([b1, b2, b3, b4])
                    try:
                        text, consumed = dec(seq)
                        if consumed != 4 or len(text) != 1:
                            raise AssertionError((seq.hex(), text, consumed))
                        kind, value = 0, ord(text)
                    except UnicodeDecodeError as e:
                        if e.start != 0 or e.end != 1 or e.reason != "illegal multibyte sequence":
                            raise AssertionError((seq.hex(), e.start, e.end, e.reason))
                        kind, value = 1, 1
                    if run is not None and run[0] == kind and (
                            (kind == 0 and value == run[2] + run[1]) or
                            (kind == 1 and value == run[3])):
                        run[1] += 1
                    else:
                        if run is not None:
                            runs.append(run)
                        run = [kind, 1, value, value]
                    index += 1
    if run is not None:
        runs.append(run)
    if index != 126 * 10 * 126 * 10:
        raise AssertionError(index)
    start = 0
    placed = []
    for kind, count, value, _last in runs:
        placed.append((kind, start, count, value))
        start += count
    runs = placed
    # A fourth byte outside 0x30-0x39 is an illegal one-byte sequence for every
    # three-byte prefix; check it on a spread of prefixes.
    for b1 in (0x81, 0x9F, 0xC0, 0xFE):
        for b2 in (0x30, 0x35, 0x39):
            for b3 in (0x81, 0x90, 0xC8, 0xFE):
                for b4 in list(range(0x00, 0x30)) + list(range(0x3A, 0x100)):
                    seq = bytes([b1, b2, b3, b4])
                    result = expect_decode(seq, "gb18030")
                    if result != ("error", 0, 1, "illegal multibyte sequence"):
                        raise AssertionError((seq.hex(), result))
    return runs


class Trie:
    """A trie of byte prefixes, built by asking CPython about every prefix.

    The decoder is called on each prefix; a successful prefix is a text leaf
    (one or two code points), an "incomplete multibyte sequence" prefix is an
    interior node whose children are the 256 continuations, and any other
    error is a leaf that reports its span and reason. Text leaves are stored
    inline in their parent, so only interior, error and the two special nodes
    become trie nodes.
    """

    def __init__(self, codec):
        self.codec = codec
        self.dec = codecs.getdecoder(codec)
        self.ids = {}
        self.nodes = []

    def intern(self, key):
        node = self.ids.get(key)
        if node is None:
            node = len(self.nodes)
            self.ids[key] = node
            self.nodes.append(key)
        return ("r", node)

    def error(self, exc, prefix):
        if exc.start != 0:
            raise AssertionError((self.codec, prefix.hex(), exc.start, exc.end))
        span = exc.end - exc.start
        if not 1 <= span <= len(prefix):
            raise AssertionError((self.codec, prefix.hex(), exc.start, exc.end))
        if exc.reason != ILLEGAL:
            raise AssertionError((self.codec, prefix.hex(), exc.reason))
        return self.intern(("e", span))

    def node(self, prefix):
        try:
            text, consumed = self.dec(prefix)
        except UnicodeDecodeError as e:
            if e.reason == INCOMPLETE:
                if e.end != len(prefix):
                    raise AssertionError((self.codec, prefix.hex(), e.end, len(prefix)))
                if self.codec == "gb18030" and is_gb18030_four_byte(prefix):
                    return self.intern(("gb4",))
                if self.codec == "euc_kr" and prefix == b"\xa4\xd4":
                    return self.intern(("makeup",))
                # gb18030's four-byte form and euc_kr's make-up form are the
                # only sequences that stay incomplete past three bytes; both
                # are special nodes above, so nothing else may go deeper.
                if len(prefix) > 3:
                    raise AssertionError((self.codec, prefix.hex(),
                                          "an interior node deeper than 3 bytes"))
                children = tuple(self.node(prefix + BYTES[b]) for b in range(256))
                return self.intern(("i", children))
            return self.error(e, prefix)
        if consumed != len(prefix):
            raise AssertionError((self.codec, prefix.hex(), consumed))
        if not 1 <= len(text) <= 2:
            raise AssertionError((self.codec, prefix.hex(), text))
        return ("t", text)

    def build(self):
        """The renumbered node list; node 0 is the root."""
        root = self.intern(("i", tuple(self.node(BYTES[b]) for b in range(256))))
        return renumber(root, self.nodes)


def renumber(root, nodes):
    """Pre-order renumbering, so a node's children get nearby ids."""
    order = []
    seen = {}

    def visit(ref):
        if ref[0] == "t":
            return ref
        index = ref[1]
        if index in seen:
            return ("r", seen[index])
        seen[index] = len(order)
        order.append(None)
        key = nodes[index]
        if key[0] == "i":
            order[seen[index]] = ("i", tuple(visit(child) for child in key[1]))
        else:
            order[seen[index]] = key
        return ("r", seen[index])

    visit(root)
    return order


# --------------------------------------------------------------------------
# binary format


def uvarint(value):
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def zigzag(value):
    return value << 1 if value >= 0 else ((-value) << 1) - 1


def encode_children(children, node):
    """The 256 children of an interior node, as runs over the byte values.

    Each entry covers one or more sibling bytes: a text leaf (one or two code
    points), a run of bytes whose code points are consecutive, or a reference
    to another trie node (an error leaf or a longer sequence). Code points are
    delta-encoded against the previous entry, node ids against the parent.
    """
    entries = bytearray()
    count = 0
    previous = 0
    i = 0
    while i < 256:
        child = children[i]
        if child[0] == "t" and len(child[1]) == 1:
            cp = ord(child[1])
            j = i + 1
            while j < 256 and children[j][0] == "t" and len(children[j][1]) == 1 \
                    and ord(children[j][1]) == cp + (j - i):
                j += 1
            run = j - i
            if run > 1:
                entries.append(2)
                entries += uvarint(run)
            else:
                entries.append(0)
            entries += uvarint(zigzag(cp - previous))
            previous = cp + run - 1
            i = j
        elif child[0] == "t":
            first, second = ord(child[1][0]), ord(child[1][1])
            entries.append(1)
            entries += uvarint(zigzag(first - previous))
            entries += uvarint(zigzag(second - first))
            previous = second
            i += 1
        else:
            entries.append(3)
            entries += uvarint(zigzag(child[1] - node))
            i += 1
        count += 1
    if count > 256:
        raise AssertionError(count)
    return uvarint(count) + bytes(entries)


def encode_trie(codec, nodes, gb_runs, makeup):
    body = bytearray()
    for node, key in enumerate(nodes):
        kind = key[0]
        if kind == "e":
            body.append(0)
            body += uvarint(key[1])
        elif kind == "i":
            body.append(1)
            body += encode_children(key[1], node)
        elif kind == "gb4":
            body.append(2)
            body += uvarint(len(gb_runs))
            previous_end = 0
            for run_kind, start, count, value in gb_runs:
                body.append(run_kind)
                body += uvarint(start - previous_end)
                body += uvarint(count)
                body += uvarint(value)
                previous_end = start + count
        elif kind == "makeup":
            body.append(3)
            body += uvarint(makeup["total"])
            body += uvarint(len(makeup["required"]))
            for offset, value in makeup["required"]:
                body.append(offset)
                body.append(value)
            for pos in makeup["fields"]:
                body.append(pos)
            body += uvarint(makeup["base"])
            for scale in makeup["scale"]:
                body += uvarint(scale)
            for table in makeup["tables"]:
                body += bytes(value + 1 for value in table)
        else:
            raise AssertionError(kind)
    out = bytearray()
    out += uvarint(len(codec))
    out += codec.encode()
    out += uvarint(len(nodes))
    out += body
    return bytes(out)


def cjk_blob(tries, gb_runs, makeup):
    out = bytearray(b"PYCJK1")
    out += uvarint(len(tries))
    for codec in sorted(tries):
        out += encode_trie(codec, tries[codec], gb_runs, makeup)
    return gzip.compress(bytes(out), compresslevel=9, mtime=0)


# --------------------------------------------------------------------------
# the generated Go file


def go_string(text):
    chunks = []
    for ch in text:
        cp = ord(ch)
        if ch == '"':
            chunks.append('\\"')
        elif ch == "\\":
            chunks.append("\\\\")
        elif 0x20 <= cp < 0x7F:
            chunks.append(ch)
        elif cp < 0x10000:
            chunks.append("\\u%04x" % cp)
        else:
            chunks.append("\\U%08x" % cp)
    return "".join(chunks)


def singlebyte_go(tables):
    out = []
    out.append("// Code generated by testdata/gen_tables.py from CPython 3.13.16 codecs; DO NOT EDIT.")
    out.append("")
    out.append("package pycodecs")
    out.append("")
    out.append("// singleByteUndefined marks a byte an 8-bit codec does not map. CPython raises")
    out.append("// UnicodeDecodeError(start, start+1, reason) for it; U+FFFF is a noncharacter")
    out.append("// no codec maps a byte to.")
    out.append("const singleByteUndefined = '\\uffff'")
    out.append("")
    out.append("// singleByteCodec is a CPython 8-bit codec: each byte maps to at most one code")
    out.append("// point, with no decoder state, so decoding is a byte-by-byte table lookup.")
    out.append("type singleByteCodec struct {")
    out.append("\ttable    []rune // 256 code points, singleByteUndefined where there is none")
    out.append("\treason   string // UnicodeDecodeError reason for an undefined byte")
    out.append("\tencoding string // name the error reports: the codec object that raised it")
    out.append("}")
    out.append("")
    out.append("// decode looks up every byte on its own. An undefined byte is reported as a")
    out.append("// one-byte error span and decoding resumes after it.")
    out.append("func (c *singleByteCodec) decode(data []byte, sink *Sink) error {")
    out.append("\tif c.encoding != \"\" {")
    out.append("\t\tsink.SetCodec(c.encoding)")
    out.append("\t}")
    out.append("\tfor i, b := range data {")
    out.append("\t\tr := c.table[b]")
    out.append("\t\tif r == singleByteUndefined {")
    out.append("\t\t\tif _, err := sink.Fail(i, i+1, c.reason); err != nil {")
    out.append("\t\t\t\treturn err")
    out.append("\t\t\t}")
    out.append("\t\t\tcontinue")
    out.append("\t\t}")
    out.append("\t\tsink.Out = append(sink.Out, r)")
    out.append("\t}")
    out.append("\treturn nil")
    out.append("}")
    out.append("")
    out.append("func init() {")
    out.append("\tfor name, codec := range singleByteCodecs {")
    out.append("\t\tregister(name, codec.decode)")
    out.append("\t}")
    out.append("}")
    out.append("")
    out.append("// singleByteCodecs is every CPython 8-bit codec, by codecs.lookup(name).name.")
    out.append("var singleByteCodecs = map[string]*singleByteCodec{")
    for name in sorted(tables):
        cps, reason, encoding = tables[name]
        text = "".join(chr(cp) if cp is not None else chr(SENTINEL) for cp in cps)
        out.append('\t"%s": {' % name)
        out.append("\t\ttable: []rune(")
        # 32 runes per line; gofmt leaves the call alone, so no alignment pass.
        runes = [go_string(ch) for ch in text]
        line = ""
        for i, piece in enumerate(runes):
            line += piece
            if (i + 1) % 32 == 0 or i == len(runes) - 1:
                last = i == len(runes) - 1
                # gofmt indents continuations of a call one tab further in.
                indent = "\t\t\t" if i < 32 else "\t\t\t\t"
                out.append('%s"%s"%s' % (indent, line, "" if last else " +"))
                line = ""
        out[-1] = out[-1] + "),"
        out.append('\t\treason:   "%s",' % reason.replace("\\", "\\\\").replace('"', '\\"'))
        out.append('\t\tencoding: "%s",' % encoding)
        out.append("\t},")
    out.append("}")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# corpus


def encoded_pool(codec, single, tries, makeup):
    """(code point, encoding) pairs of characters the codec can encode."""
    if single:
        cps = [cp for cp in single[0] if cp is not None]
    else:
        cps = list(range(0x20, 0x7F))
        for key in tries[codec]:
            if key[0] == "i":
                for child in key[1]:
                    if child[0] == "t":
                        cps.extend(ord(ch) for ch in child[1])
        if codec == "euc_kr" and makeup is not None:
            cps.extend(ord(seq.decode("euc_kr")) for seq in makeup["syllables"])
    pool = []
    seen = set()
    for cp in cps:
        if cp in seen:
            continue
        seen.add(cp)
        try:
            raw = chr(cp).encode(codec)
        except (UnicodeEncodeError, UnicodeError):
            continue
        if raw:
            pool.append((cp, raw))
    if not pool:
        raise AssertionError((codec, "no encodable code point"))
    return pool


def make_input(rng, pool, codec):
    target = rng.randrange(0, 41)
    lead = [raw[0] for _, raw in pool if len(raw) > 1] or [raw[0] for _, raw in pool]
    out = bytearray()
    while len(out) < target:
        kind = rng.randrange(7)
        if kind == 0:
            for _ in range(rng.randint(1, 3)):
                out += pool[rng.randrange(len(pool))][1]
        elif kind == 1:
            out += bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))
        elif kind == 2:
            raw = pool[rng.randrange(len(pool))][1]
            if len(raw) > 1:
                out += raw[:rng.randrange(len(raw))]
            else:
                out += raw
        elif kind == 3:
            out.append(rng.choice(lead))
            out += bytes([rng.choice(b"A0 ~\x00\x7f")])
        elif kind == 4:
            out += bytes(rng.randrange(0x80, 0x100) for _ in range(rng.randint(1, 3)))
        elif kind == 5 and codec == "gb18030":
            seq = bytes([rng.randrange(0x81, 0xFF), rng.randrange(0x30, 0x3A),
                         rng.randrange(0x81, 0xFF), rng.randrange(0x30, 0x3A)])
            out += seq[:rng.randint(1, 4)]
        else:
            out += bytes(rng.randrange(0x20, 0x7F) for _ in range(rng.randint(1, 3)))
    return bytes(out[:target])


def corpus_result(codec, data, handler):
    try:
        text = data.decode(codec, handler)
    except UnicodeDecodeError as e:
        return {"e": {"start": e.start, "end": e.end, "reason": e.reason, "text": str(e)}}
    return {"t": [ord(ch) for ch in text]}


def corpus(tables, tries, makeup):
    """The differential corpus: inputs, and the results of four error handlers.

    A result is {"t": [code points]} or {"e": {start, end, reason, text}} where
    text is CPython's str(UnicodeDecodeError). Identical results (the same text,
    or the same error) are stored once in "pool" and referenced by index, which
    keeps the file small: a valid input has the same text under every handler.
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
    for codec in sorted(set(tables) | set(tries)):
        rng = random.Random(SEED ^ zlib.crc32(codec.encode()))
        encoded = encoded_pool(codec, tables.get(codec), tries, makeup)
        cases = []
        for _ in range(CASES_PER_SINGLE_BYTE_CODEC if codec in tables else CASES_PER_CODEC):
            data = make_input(rng, encoded, codec)
            case = {"in": data.hex()}
            for handler, key in zip(HANDLERS, ("s", "r", "se", "g")):
                case[key] = result(codec, data, handler)
            cases.append(case)
        out["codecs"][codec] = cases
    return gzip.compress(json.dumps(out, separators=(",", ":")).encode(), compresslevel=9, mtime=0)


# --------------------------------------------------------------------------


def main(argv):
    if len(argv) > 1 and argv[1] != "--check":
        print("usage: gen_tables.py [--check]", file=sys.stderr)
        return 2
    check = len(argv) > 1
    warnings.simplefilter("ignore", DeprecationWarning)

    tables = single_byte_codecs()
    gb_runs = gb18030_four_byte_runs()
    makeup = euc_kr_makeup()
    tries = {}
    for codec in MULTIBYTE:
        if codecs.lookup(codec).name != codec:
            raise AssertionError(codec)
        tries[codec] = Trie(codec).build()
    blob = cjk_blob(tries, gb_runs, makeup)
    corpus_bytes = corpus(tables, tries, makeup)
    go_bytes = singlebyte_go(tables).encode()

    outputs = [(SINGLE_GO, go_bytes), (CJK_BIN, blob), (CORPUS, corpus_bytes)]
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
    print("%-28s %8d bytes, %d codecs" % ("singlebyte_gen.go", len(go_bytes), len(tables)))
    print("%-28s %8d bytes, %d codecs" % ("cjk_tables.bin.gz", len(blob), len(tries)))
    print("%-28s %8d bytes, %d codecs, %d cases each"
          % ("corpus_tables.json.gz", len(corpus_bytes), len(tables) + len(tries), CASES_PER_CODEC))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
