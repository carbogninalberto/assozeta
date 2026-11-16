#!/usr/bin/env python3
"""Build testdata/corpus.json.gz: archives and CPython 3.13 zipfile outcomes.

Run with the reference interpreter:

    $PY -I BE_GO/internal/pyzipfile/testdata/gen_corpus.py
    $PY -I BE_GO/internal/pyzipfile/testdata/gen_corpus.py --check

--check regenerates the corpus and compares it with the stored file; it does
not write. The archives are built deterministically (zipfile's own writers,
zlib/bz2/lzma, and a small header builder for crafted cases).
"""

import base64
import binascii
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import struct
import sys
import tempfile
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus.json.gz")

ZIP_STORED = zipfile.ZIP_STORED
ZIP_DEFLATED = zipfile.ZIP_DEFLATED
ZIP_BZIP2 = zipfile.ZIP_BZIP2
ZIP_LZMA = zipfile.ZIP_LZMA


# --------------------------------------------------------------------------
# Deterministic member payloads
# --------------------------------------------------------------------------

def payload(kind, size=0):
    if kind == "empty":
        return b""
    if kind == "short":
        return b"hello\n"
    if kind == "json":
        return b'{"a": 1, "b": [1, 2, 3], "c": "associazione"}\n'
    if kind == "text":
        return (b"associazione socio quota rinnovo pagamento citta\n" * 4)[:size or 300]
    if kind == "zeros":
        return b"\0" * (size or 1000)
    if kind == "random":
        x = 0x12345678
        out = bytearray()
        for _ in range(size or 1000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            out.append((x >> 16) & 0xFF)
        return bytes(out)
    if kind == "runs":
        return b"A" * 300 + b"B" * 200 + b"C" * 100
    if kind == "lorem":
        base = b"associazione socio quota rinnovo pagamento citta "
        return (base * (size // len(base) + 1))[:size]
    if kind == "pattern":
        return (bytes(range(256)) * (size // 256 + 1))[:size]
    if kind == "far":
        # three copies of a deterministic pseudo-random block: deflate finds
        # matches at a long distance that cross the 4096-byte output chunks.
        base = payload("random", (size + 2) // 3)
        return (base * 3)[:size]
    if kind == "boundary":
        # exactly MIN_READ_SIZE incompressible bytes, then two copies: the
        # first match of the next read starts at the 4096-byte output
        # boundary with a distance far outside the current output buffer.
        base = payload("random", 4096)
        return (base * 3)[:size]
    raise ValueError(kind)


def zip_bytes(compression, members, comment=b""):
    """A ZIP written by zipfile with a fixed 1980 timestamp (deterministic)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = compression
            zf.writestr(info, data)
        if comment:
            zf.comment = comment
    return buf.getvalue()


def compress(method, data):
    if method == ZIP_STORED:
        return data
    if method == ZIP_DEFLATED:
        c = zlib.compressobj(6, zlib.DEFLATED, -15)
        return c.compress(data) + c.flush()
    if method == ZIP_BZIP2:
        return bz2.compress(data)
    if method == ZIP_LZMA:
        c = zipfile.LZMACompressor()
        return c.compress(data) + c.flush()
    return data


# --------------------------------------------------------------------------
# A header builder with complete control over every field
# --------------------------------------------------------------------------

class Entry:
    def __init__(self, name, data=b"", method=ZIP_STORED, flags=0,
                 crc=None, compressed=None, comp_size=None, file_size=None,
                 extra=b"", comment=b"", local_name=None, local_extra=None,
                 local_flags=None, local_method=None, local_crc=None,
                 local_comp_size=None, local_file_size=None,
                 offset=None, extract_version=None, create_version=20,
                 external_attr=0, internal_attr=0, omit_local=False,
                 omit_data=False, omit_local_name=False, raw_local_sig=None,
                 raw_cd_sig=None, disk_start=0):
        self.name = name if isinstance(name, bytes) else name.encode("utf-8")
        self.data = data
        self.method = method
        self.flags = flags
        self.crc = zlib.crc32(data) if crc is None else crc
        self.compressed = compress(method, data) if compressed is None else compressed
        self.comp_size = len(self.compressed) if comp_size is None else comp_size
        self.file_size = len(data) if file_size is None else file_size
        self.extra = extra
        self.comment = comment
        self.local_name = self.name if local_name is None else local_name
        self.local_extra = extra if local_extra is None else local_extra
        self.local_flags = flags if local_flags is None else local_flags
        self.local_method = method if local_method is None else local_method
        self.local_crc = self.crc if local_crc is None else local_crc
        self.local_comp_size = self.comp_size if local_comp_size is None else local_comp_size
        self.local_file_size = self.file_size if local_file_size is None else local_file_size
        self.offset = offset
        self.extract_version = extract_version if extract_version is not None else (
            45 if method == ZIP_BZIP2 else 63 if method == ZIP_LZMA else 20)
        self.create_version = create_version
        self.external_attr = external_attr
        self.internal_attr = internal_attr
        self.omit_local = omit_local
        self.omit_data = omit_data
        self.omit_local_name = omit_local_name
        self.raw_local_sig = raw_local_sig
        self.raw_cd_sig = raw_cd_sig
        self.disk_start = disk_start


def build_zip(entries, comment=b"", prefix=b"", eocd_extra=b"",
              eocd_override=None, cd_offset_override=None, cd_size_override=None,
              count_override=None, zip64=None, zip64_locator_only=False,
              eocd_at=None, omit_eocd=False, cd_tail=b""):
    """Assemble a ZIP file from Entry objects.

    zip64: None, "all" (classic fields 0xffff/0xffffffff plus the records),
    or "sizes" (only the per-entry size/offset fields are 0xffffffff and the
    extra carries them, classic end record stays 32 bit).
    """
    out = bytearray(prefix)
    centrals = []
    for e in entries:
        offset = len(out)
        if not e.omit_local:
            sig = b"PK\x03\x04" if e.raw_local_sig is None else e.raw_local_sig
            if e.omit_local_name:
                local_name = b""
            else:
                local_name = e.local_name
            header = struct.pack(
                "<4s2B4HL2L2H", sig, e.extract_version, 0, e.local_flags,
                e.local_method, 0, 0, e.local_crc, e.local_comp_size,
                e.local_file_size, len(local_name), len(e.local_extra))
            out += header + local_name + e.local_extra
            if not e.omit_data:
                out += e.compressed
        centrals.append((e, offset, local_name if not e.omit_local else b""))

    cd_start = len(out)
    for e, offset, _ in centrals:
        name = e.name
        sig = b"PK\x01\x02" if e.raw_cd_sig is None else e.raw_cd_sig
        ext_version = e.extract_version
        crc = e.crc
        comp_size = e.comp_size
        file_size = e.file_size
        extra = e.extra
        hdr_offset = offset if e.offset is None else e.offset
        if zip64 in ("all", "sizes"):
            if file_size >= 0xFFFFFFFF or e.file_size >= 0xFFFFFFFF:
                file_size = 0xFFFFFFFF
            if comp_size >= 0xFFFFFFFF or e.comp_size >= 0xFFFFFFFF:
                comp_size = 0xFFFFFFFF
            if hdr_offset >= 0xFFFFFFFF or e.offset is not None or zip64 == "all":
                hdr_offset = 0xFFFFFFFF
            values = b""
            if file_size == 0xFFFFFFFF:
                values += struct.pack("<Q", e.file_size)
            if comp_size == 0xFFFFFFFF:
                values += struct.pack("<Q", e.comp_size)
            if hdr_offset == 0xFFFFFFFF:
                values += struct.pack("<Q", offset if e.offset is None else e.offset)
            if values and not extra.startswith(struct.pack("<HH", 1, len(values))):
                extra = struct.pack("<HH", 1, len(values)) + values + extra
        out += struct.pack(
            "<4s4B4HL2L5H2L", sig, e.create_version, 3, ext_version, 0,
            e.flags, e.method, 0, 0, crc, comp_size, file_size,
            len(name), len(extra), len(e.comment), e.disk_start,
            e.internal_attr, e.external_attr, hdr_offset)
        out += name + extra + e.comment

    out += cd_tail
    cd_size = len(out) - cd_start
    if cd_offset_override is not None:
        cd_offset = cd_offset_override
    else:
        cd_offset = cd_start
    if cd_size_override is not None:
        cd_size = cd_size_override
    if count_override is not None:
        count = count_override
    else:
        count = len(entries)

    if omit_eocd:
        return bytes(out)

    if zip64 == "all":
        z64_offset = len(out)
        out += struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0,
                           count, count, cd_size, cd_offset)
        out += struct.pack("<4sLQL", b"PK\x06\x07", 0, z64_offset, 1)
        count = 0xFFFF
        cd_size = 0xFFFFFFFF
        cd_offset = 0xFFFFFFFF
    elif zip64_locator_only:
        out += struct.pack("<4sLQL", b"PK\x06\x07", 0, len(out) - 56, 1)

    eocd = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, count, count,
                       cd_size, cd_offset, len(comment))
    if eocd_override is not None:
        eocd = eocd_override
    if eocd_at is not None and eocd_at < len(out):
        out[eocd_at:eocd_at + len(eocd)] = eocd
    else:
        out += eocd
    out += comment + eocd_extra
    return bytes(out)


# --------------------------------------------------------------------------
# Expected outcomes from the reference Python
# --------------------------------------------------------------------------

def outcome_exception(exc):
    if isinstance(exc, zlib.error):
        # Production links stock zlib (python:3.13-slim: 1.2.13). macOS's system
        # libz adds 'invalid literal/length/distance code', which stock zlib never
        # reports: a corpus must not record it.
        assert "invalid literal/length/distance code" not in str(exc), "Apple libz message: regenerate on Linux"
        return ["zlib.error", str(exc)]
    return [type(exc).__name__, str(exc)]


def describe_archive(data, chunk=4096, max_chunks=12):
    result = {}
    fd, path = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result["is_zipfile"] = zipfile.is_zipfile(path)
        try:
            zf = zipfile.ZipFile(path, "r")
        except Exception as exc:
            result["open"] = {"error": outcome_exception(exc)}
            return result
        try:
            names = zf.namelist()
            result["open"] = {"namelist": names, "infos": {}}
            seen = []
            for name in names:
                if name not in seen:
                    seen.append(name)
            probes = []
            for probe in ["missing", "it's \"x\"\nè", ""]:
                if probe not in seen:
                    probes.append(probe)
            for name in seen + probes:
                info = None
                if name in seen:
                    info = zf.getinfo(name)
                if name in seen:
                    result["open"]["infos"][name] = {
                        "filename": info.filename,
                        "orig_filename": info.orig_filename,
                        "file_size": info.file_size,
                        "compress_size": info.compress_size,
                        "compress_type": info.compress_type,
                        "flag_bits": info.flag_bits,
                        "CRC": info.CRC,
                        "header_offset": info.header_offset,
                        "external_attr": info.external_attr,
                    }
                entry = {}
                try:
                    entry["all"] = {"ok": zf.read(name).hex()}
                except Exception as exc:
                    entry["all"] = {"error": outcome_exception(exc)}
                chunks = []
                try:
                    member = zf.open(name)
                except Exception as exc:
                    entry["open_error"] = outcome_exception(exc)
                else:
                    try:
                        for _ in range(max_chunks):
                            try:
                                piece = member.read(chunk)
                            except Exception as exc:
                                chunks.append({"error": outcome_exception(exc)})
                                break
                            chunks.append({"ok": piece.hex()})
                            if not piece:
                                break
                    finally:
                        try:
                            member.close()
                        except Exception:
                            pass
                entry["chunks"] = chunks
                result.setdefault("reads", {})[name] = entry
        finally:
            zf.close()
    finally:
        os.unlink(path)
    return result


# --------------------------------------------------------------------------
# Case construction
# --------------------------------------------------------------------------

def central_headers(data):
    """Offsets of every central directory header, in order."""
    eocd = data.rfind(b"PK\x05\x06")
    start = struct.unpack("<L", data[eocd + 16:eocd + 20])[0]
    out = []
    pos = start
    while pos + 46 <= len(data) and data[pos:pos + 4] == b"PK\x01\x02":
        out.append(pos)
        name, extra, comment = struct.unpack("<HHH", data[pos + 28:pos + 34])
        pos += 46 + name + extra + comment
    return out


def patch_central_offset(data, index, value):
    data = bytearray(data)
    pos = central_headers(data)[index]
    data[pos + 42:pos + 46] = struct.pack("<L", value)
    return data


def main_cases():
    cases = []

    def add(name, data):
        cases.append({"name": name, "data": data})

    def add_entry(name, entry, **kwargs):
        add(name, build_zip([entry], **kwargs))

    # -- valid archives from zipfile's own writer, each method -------------
    for method, label in [(ZIP_STORED, "stored"), (ZIP_DEFLATED, "deflate"),
                          (ZIP_BZIP2, "bzip2"), (ZIP_LZMA, "lzma")]:
        for kind in ["empty", "short", "json", "text", "zeros", "runs"]:
            for fname in ["a.txt", "cartella/é.txt"]:
                add("valid-%s-%s-%s" % (label, kind, fname.replace("/", "_")),
                    zip_bytes(method, [(fname, payload(kind))]))

    # random payloads (a few sizes) across methods
    for method, label in [(ZIP_STORED, "stored"), (ZIP_DEFLATED, "deflate"),
                          (ZIP_BZIP2, "bzip2"), (ZIP_LZMA, "lzma")]:
        for size in [1, 17, 300, 4095, 4096, 4097, 9000]:
            add("random-%s-%d" % (label, size),
                zip_bytes(method, [("r.bin", payload("random", size))]))

    # several members, a directory, comments (archive and member)
    add("multi-members-comment", zip_bytes(ZIP_DEFLATED, [
        ("dir/", b""), ("dir/a.json", payload("json")),
        ("dir/b.txt", payload("text"))], comment=b"archive comment"))

    # duplicate names (last wins in NameToInfo)
    add("duplicate-names", build_zip([
        Entry("dup.txt", b"first"), Entry("dup.txt", b"second"),
        Entry("other.txt", b"other")]))

    # cp437 name without the UTF-8 flag (0x82 is é in cp437)
    add_entry("cp437-name", Entry(b"caf\x82.txt", b"cp437"))
    # UTF-8 name with the flag
    add_entry("utf8-name-flag", Entry("città/è.txt".encode(), b"utf8", flags=0x800))
    # UTF-8 flag set but invalid UTF-8 bytes: constructor raises
    add_entry("utf8-flag-invalid", Entry(b"bad\xffname", b"x", flags=0x800))
    # invalid UTF-8 without the flag: cp437 maps every byte
    add_entry("cp437-all-bytes", Entry(bytes(range(1, 256)), b"x"))
    # name with a null byte (sanitised for filename, kept for orig_filename)
    add_entry("null-name", Entry(b"a\x00b.txt", b"x"))
    # names that exercise repr(): quote, double quote, newline, backslash,
    # non-printable and non-ASCII
    add_entry("repr-name-quote", Entry(b"it's \"x\".txt", b"x"))
    add_entry("repr-name-controls", Entry(b"a\nb\tc\\d\x01\x7f.txt", b"x"))
    add_entry("repr-name-unicode", Entry("città/naïve/€.txt".encode(), b"x", flags=0x800))

    # -- extra fields ------------------------------------------------------
    # Unicode path extra field (0x7075), matching CRC, version 1: renames
    raw = b"orig.txt"
    up = struct.pack("<BL", 1, zlib.crc32(raw)) + "rèname.txt".encode()
    add_entry("extra-unicode-path", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, len(up)) + up))
    # version mismatch: ignored
    up2 = struct.pack("<BL", 2, zlib.crc32(raw)) + b"ignored.txt"
    add_entry("extra-unicode-path-version", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, len(up2)) + up2))
    # CRC mismatch: ignored
    up3 = struct.pack("<BL", 1, 0xDEADBEEF) + b"ignored.txt"
    add_entry("extra-unicode-path-crc", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, len(up3)) + up3))
    # empty unicode name: warning, original kept
    up4 = struct.pack("<BL", 1, zlib.crc32(raw))
    add_entry("extra-unicode-path-empty", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, len(up4)) + up4))
    # invalid UTF-8 in the unicode path: BadZipFile
    up5 = struct.pack("<BL", 1, zlib.crc32(raw)) + b"bad\xff"
    add_entry("extra-unicode-path-invalid", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, len(up5)) + up5))
    # truncated 0x7075 payload
    add_entry("extra-unicode-path-short", Entry(raw, b"x",
               extra=struct.pack("<HH", 0x7075, 3) + b"\x01\x02\x03"))
    # unknown extra field, then a good one
    add_entry("extra-unknown", Entry(b"x.txt", b"x",
               extra=struct.pack("<HH", 0xCAFE, 2) + b"hi"))
    # corrupt extra: declared length exceeds the field
    add_entry("extra-corrupt-length", Entry(b"x.txt", b"x",
               extra=struct.pack("<HH", 0x0001, 20) + b"\0" * 4))
    # ZIP64 extra with file size 0xffffffff but no 8-byte value
    add_entry("extra-zip64-short", Entry(b"x.txt", b"x", file_size=0xFFFFFFFF,
               extra=struct.pack("<HH", 1, 0)))
    # ZIP64 extra values: file size and compress size
    data = payload("short")
    comp = compress(ZIP_DEFLATED, data)
    z64 = struct.pack("<QQ", len(data), len(comp))
    add_entry("extra-zip64-sizes", Entry(b"x.txt", data, method=ZIP_DEFLATED,
               compressed=comp, file_size=0xFFFFFFFF, comp_size=0xFFFFFFFF,
               extra=struct.pack("<HH", 1, len(z64)) + z64))

    # -- central directory damage -----------------------------------------
    base = build_zip([Entry("a.txt", b"hello"), Entry("b.txt", b"world")])
    eocd = base.rfind(b"PK\x05\x06")
    cd_start = struct.unpack("<L", base[eocd + 16:eocd + 20])[0]

    add("eocd-no-comment", base)
    add("eocd-comment", base[:eocd] + b"PK\x05\x06" + base[eocd + 4:eocd + 20] +
        struct.pack("<H", 5) + b"hello")
    add("eocd-comment-signature", build_zip([Entry("a.txt", b"x")],
        comment=b"PK\x05\x06 in the comment"))
    add("eocd-comment-short", base[:eocd] + struct.pack(
        "<4s4H2LH", b"PK\x05\x06", 0, 0, 2, 2,
        len(base[cd_start:eocd]), cd_start, 50) + b"short")
    add("eocd-bad-signature", base[:eocd] + b"PK\x05\x07" + base[eocd + 4:])
    add("no-eocd", base[:eocd])
    add("tiny-file", b"PK")
    add("empty-file", b"")
    add("not-a-zip", b"this is not a zip file at all\n")
    add("eocd-truncated-21", base[eocd:eocd + 21])
    add("eocd-entries-zero", base[:eocd + 8] + b"\0\0" + base[eocd + 10:])
    add("eocd-size-smaller", base[:eocd + 12] + struct.pack("<L", 10) + base[eocd + 16:])
    add("eocd-size-larger", base[:eocd + 12] + struct.pack("<L", 0x1000) + base[eocd + 16:])
    add("eocd-offset-wrong", base[:eocd + 16] + struct.pack("<L", 3) + base[eocd + 20:])
    add("eocd-offset-huge", base[:eocd + 16] + struct.pack("<L", 0xFFFFFFF0) + base[eocd + 20:])
    add("eocd-offset-before-file", base[:eocd + 16] + struct.pack("<L", 0x7FFFFFFF) + base[eocd + 20:])

    # central directory position/signature/truncation
    add("central-signature", base[:cd_start] + b"PK\x01\x03" + base[cd_start + 4:])
    add("central-truncated-header", build_zip([Entry("a.txt", b"hello")], cd_tail=b"x" * 20))
    add("central-truncated-header-45", build_zip([Entry("a.txt", b"hello")], cd_tail=b"x" * 45))
    add("central-bad-second-signature", build_zip([Entry("a.txt", b"hello")], cd_tail=b"x" * 46))
    add("central-empty-size", base[:eocd + 12] + struct.pack("<L", 0) + base[eocd + 16:])
    add("central-offset-zero", base[:eocd + 16] + struct.pack("<L", 0) + base[eocd + 20:])

    # extract_version above MAX_EXTRACT_VERSION
    add_entry("extract-version-64", Entry(b"x.txt", b"x", extract_version=64))
    add("extract-version-99", build_zip([Entry(b"x.txt", b"x", extract_version=99)]))

    # entry count spoofed (ignored: the loop runs on size_cd)
    add("count-spoofed-small", build_zip([Entry("a.txt", b"x")], count_override=0))
    add("count-spoofed-large", build_zip([Entry("a.txt", b"x")], count_override=5))

    # -- local header damage ----------------------------------------------
    for label, mutate in [
        ("local-bad-signature", lambda d, o: d[:o] + b"PK\x03\x05" + d[o + 4:]),
        ("local-truncated-header", lambda d, o: d[:o + 10]),
        ("local-truncated-name", lambda d, o: d[:o + 31]),
        ("local-name-mismatch", lambda d, o: d[:o + 30] + b"z" + d[o + 31:]),
        ("local-name-short", lambda d, o: d[:o + 26] + struct.pack("<H", 0) + d[o + 28:]),
        ("local-extra-length", lambda d, o: d[:o + 28] + struct.pack("<H", 999) + d[o + 30:]),
        ("local-size-zero", lambda d, o: d[:o + 22] + struct.pack("<L", 0) + d[o + 26:]),
    ]:
        one = build_zip([Entry("a.txt", b"hello")])
        off = 0
        add(label, mutate(one, off))

    # flag bits 5 and 6 (central flag_bits, checked before anything else)
    add_entry("flag-patched", Entry(b"x.txt", b"x", flags=0x20))
    add_entry("flag-strong-encryption", Entry(b"x.txt", b"x", flags=0x40))
    add_entry("flag-encrypted", Entry(b"x.txt", b"x", flags=0x1))
    add_entry("flag-encrypted-quote-name", Entry(b"a'b.txt", b"x", flags=0x1))
    add_entry("flag-data-descriptor", Entry(b"x.txt", b"hello", flags=0x8))
    add_entry("flag-utf8-local-only", Entry(b"x.txt", b"hello", flags=0, local_flags=0x800))
    add_entry("flag-utf8-central-only", Entry("città.txt".encode(), b"hello", flags=0x800,
                                              local_flags=0))

    # -- compression methods ----------------------------------------------
    add_entry("method-99", Entry(b"x.txt", b"x", method=99))
    add_entry("method-1", Entry(b"x.txt", b"x", method=1))
    add_entry("method-9", Entry(b"x.txt", b"x", method=9))
    add_entry("method-10", Entry(b"x.txt", b"x", method=10))
    add_entry("method-18", Entry(b"x.txt", b"x", method=18))
    add_entry("method-98", Entry(b"x.txt", b"x", method=98))
    add_entry("method-255", Entry(b"x.txt", b"x", method=255))
    add_entry("method-central-local-differ", Entry(b"x.txt", b"x", method=8,
               local_method=0, compressed=payload("short")))

    # -- CRC and size mismatches ------------------------------------------
    add_entry("crc-wrong", Entry(b"x.txt", b"hello", crc=0xDEADBEEF))
    add_entry("crc-wrong-quote-name", Entry(b"a'b\"c.txt", b"hello", crc=0xDEADBEEF))
    add_entry("crc-zero-nonempty", Entry(b"x.txt", b"hello", crc=0))
    add_entry("stored-size-larger", Entry(b"x.txt", b"hello", file_size=10))
    add_entry("stored-size-smaller", Entry(b"x.txt", b"hello", file_size=3))
    add_entry("stored-comp-larger", Entry(b"x.txt", b"hello", comp_size=10))
    add_entry("stored-comp-smaller", Entry(b"x.txt", b"hello", comp_size=3))
    add_entry("stored-zero-both", Entry(b"x.txt", b"", file_size=0, comp_size=0))
    add_entry("stored-file-size-zero-data", Entry(b"x.txt", b"", file_size=0,
               comp_size=5, compressed=b"hello"))
    add_entry("stored-data-zero-file-size", Entry(b"x.txt", b"hello", comp_size=0))
    add_entry("stored-crc-empty", Entry(b"x.txt", b"", crc=0))
    add_entry("stored-crc-empty-wrong", Entry(b"x.txt", b"", crc=1))

    # -- deflate corruption ------------------------------------------------
    text = payload("text", 300)
    comp = compress(ZIP_DEFLATED, text)
    add_entry("deflate-valid", Entry(b"x.txt", text, method=ZIP_DEFLATED, compressed=comp))
    add_entry("deflate-empty-stream", Entry(b"x.txt", b"", method=ZIP_DEFLATED, compressed=b""))
    add_entry("deflate-empty-valid", Entry(b"x.txt", b"", method=ZIP_DEFLATED))
    add_entry("deflate-truncated-stream", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=comp[:-1], comp_size=len(comp) - 1))
    add_entry("deflate-truncated-declared-more", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=comp[:-1], comp_size=len(comp)))
    add_entry("deflate-garbage", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=b"\xff\xff\xff\xff"))
    add_entry("deflate-block-type-3", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=b"\x07"))
    add_entry("deflate-stored-len-bad", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=b"\x01\x05\x00\x00\x00"))
    add_entry("deflate-raw-stored", Entry(b"x.txt", b"hello", method=ZIP_DEFLATED,
               compressed=b"\x01\x05\x00\xfa\xffhello"))
    add_entry("deflate-raw-stored-truncated", Entry(b"x.txt", b"hello", method=ZIP_DEFLATED,
               compressed=b"\x01\x05\x00\xfa\xffhel"))
    add_entry("deflate-zeroed", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=b"\0" * len(comp)))
    add_entry("deflate-flipped", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=bytes([comp[0] ^ 0x40]) + comp[1:]))
    add_entry("deflate-deflate-crc-wrong", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=comp, crc=0x11111111))
    add_entry("deflate-deflate-size-smaller", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=comp, file_size=10))
    add_entry("deflate-deflate-size-larger", Entry(b"x.txt", text, method=ZIP_DEFLATED,
               compressed=comp, file_size=10000))

    # corrupt at every byte of a short stream (bounded)
    short = payload("runs")
    scomp = compress(ZIP_DEFLATED, short)
    for i in range(len(scomp)):
        bad = bytearray(scomp)
        bad[i] ^= 0x5A
        add("deflate-flip-%d" % i, build_zip([
            Entry(b"x.txt", short, method=ZIP_DEFLATED, compressed=bytes(bad))]))
    # truncate at every length
    for i in range(0, len(scomp)):
        add("deflate-trunc-%d" % i, build_zip([
            Entry(b"x.txt", short, method=ZIP_DEFLATED, compressed=scomp[:i])]))

    # -- bzip2 corruption --------------------------------------------------
    bcomp = compress(ZIP_BZIP2, text)
    add_entry("bzip2-valid", Entry(b"x.txt", text, method=ZIP_BZIP2, compressed=bcomp))
    add_entry("bzip2-garbage", Entry(b"x.txt", text, method=ZIP_BZIP2, compressed=b"BZ0garbage"))
    add_entry("bzip2-truncated", Entry(b"x.txt", text, method=ZIP_BZIP2,
               compressed=bcomp[:-4], comp_size=len(bcomp) - 4))
    add_entry("bzip2-empty", Entry(b"x.txt", text, method=ZIP_BZIP2, compressed=b""))
    add_entry("bzip2-empty-valid", Entry(b"x.txt", b"", method=ZIP_BZIP2))
    for i in range(len(bcomp)):
        bad = bytearray(bcomp)
        bad[i] ^= 0xA5
        add("bzip2-flip-%d" % i, build_zip([
            Entry(b"x.txt", text, method=ZIP_BZIP2, compressed=bytes(bad))]))
    for i in range(0, len(bcomp), 2):
        add("bzip2-trunc-%d" % i, build_zip([
            Entry(b"x.txt", text, method=ZIP_BZIP2, compressed=bcomp[:i])]))

    # -- lzma corruption ---------------------------------------------------
    lcomp = compress(ZIP_LZMA, text)
    add_entry("lzma-valid", Entry(b"x.txt", text, method=ZIP_LZMA, compressed=lcomp))
    add_entry("lzma-empty-valid", Entry(b"x.txt", b"", method=ZIP_LZMA))
    add_entry("lzma-header-only", Entry(b"x.txt", text, method=ZIP_LZMA,
               compressed=lcomp[:5]))
    add_entry("lzma-short", Entry(b"x.txt", text, method=ZIP_LZMA, compressed=lcomp[:3]))
    add_entry("lzma-garbage", Entry(b"x.txt", text, method=ZIP_LZMA,
               compressed=b"\x09\x04\x00\x00garbage"))
    add_entry("lzma-bad-props", Entry(b"x.txt", text, method=ZIP_LZMA,
               compressed=b"\x09\x04\x01\x00\xff" + lcomp[5:]))
    add_entry("lzma-truncated", Entry(b"x.txt", text, method=ZIP_LZMA,
               compressed=lcomp[:-1], comp_size=len(lcomp) - 1))
    for i in range(0, len(lcomp), 3):
        bad = bytearray(lcomp)
        bad[i] ^= 0x5A
        add("lzma-flip-%d" % i, build_zip([
            Entry(b"x.txt", text, method=ZIP_LZMA, compressed=bytes(bad))]))
    for i in range(0, len(lcomp), 5):
        add("lzma-trunc-%d" % i, build_zip([
            Entry(b"x.txt", text, method=ZIP_LZMA, compressed=lcomp[:i])]))

    # -- ZIP64 -------------------------------------------------------------
    add("zip64-all", build_zip([Entry("a.txt", b"hello")], zip64="all"))
    add("zip64-sizes", build_zip([Entry("a.txt", b"hello")], zip64="sizes"))
    add("zip64-locator-only", build_zip([Entry("a.txt", b"hello")], zip64_locator_only=True))
    # locator present, record missing (points before the file)
    z = bytearray(build_zip([Entry("a.txt", b"hello")]))
    z += struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    z += struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, 36, 0, 0)
    add("zip64-locator-missing-record", bytes(z))
    # multi-disk locator
    z = bytearray(build_zip([Entry("a.txt", b"hello")], zip64="all"))
    loc = z.rfind(b"PK\x06\x07")
    z[loc + 4:loc + 8] = struct.pack("<L", 1)
    add("zip64-multi-disk", bytes(z))
    # corrupt zip64 locator: reloff beyond the record
    z = bytearray(build_zip([Entry("a.txt", b"hello")], zip64="all"))
    loc = z.rfind(b"PK\x06\x07")
    z[loc + 8:loc + 16] = struct.pack("<Q", 0xFFFFFFF0)
    add("zip64-locator-reloff", bytes(z))
    # truncated zip64 end record
    z = bytearray(build_zip([Entry("a.txt", b"hello")], zip64="all"))
    loc = z.rfind(b"PK\x06\x07")
    add("zip64-record-truncated", bytes(z[:loc - 10]))
    # corrupt zip64 record fields
    z = bytearray(build_zip([Entry("a.txt", b"hello")], zip64="all"))
    rec = z.rfind(b"PK\x06\x06")
    z[rec + 32:rec + 40] = struct.pack("<Q", 1)  # dirsize
    add("zip64-record-dirsize", bytes(z))
    z = bytearray(build_zip([Entry("a.txt", b"hello")], zip64="all"))
    rec = z.rfind(b"PK\x06\x06")
    z[rec + 40:rec + 48] = struct.pack("<Q", 3)  # diroffset
    add("zip64-record-diroffset", bytes(z))
    # ZIP64 archive appended after prepended data (the locator's reloff is
    # stale, exercising _EndRecData64's prepended-data fallback)
    add("zip64-prefix", b"PFX!" * 16 + build_zip(
        [Entry("a.txt", b"hello"), Entry("b.txt", b"world")], zip64="all"))

    # prepended data: offsets not adjusted, concat arithmetic recovers them
    plain = build_zip([Entry("a.txt", b"hello"), Entry("b.txt", b"world")])
    add("prefix-data", b"SFX" * 10 + plain)
    add("prefix-data-big", b"X" * 70000 + build_zip([Entry("a.txt", b"hello")]))

    # -- header offsets pointing anywhere ---------------------------------
    for off in [0, 1, 2, 3, 4, 29, 30, 31, 100, 0xFFFFFFF0]:
        add("header-offset-%d" % off, build_zip([
            Entry(b"a.txt", b"hello", offset=off)]))
    # two entries at the same offset (overlap warning, not an error)
    same = build_zip([Entry("a.txt", b"hello"), Entry("b.txt", b"world")])
    add("header-offset-same", build_zip([
        Entry(b"a.txt", b"hello"), Entry(b"b.txt", b"world", offset=0)]))
    # later entry pointing back into the data of an earlier one
    overlap = bytearray(build_zip([Entry("a.txt", b"hello"), Entry("b.txt", b"world")]))
    add("overlap-backwards", bytes(patch_central_offset(overlap, 1, 8)))

    # -- local/central names and data descriptors -------------------------
    # data descriptor after stored data (not read by zipfile; flag only)
    add("data-descriptor-trailing", build_zip([
        Entry(b"x.txt", b"hello", flags=0x8)]) + b"\x50\x4b\x07\x08" + b"\0" * 12)
    # the local header disagrees in flags but names match
    add_entry("local-flags-encrypted", Entry(b"x.txt", b"hello", flags=0,
               local_flags=0x1))

    # -- more structural truncations --------------------------------------
    valid = build_zip([Entry("a.txt", b"hello"), Entry("b.txt", b"world")])
    for cut in [1, 2, 4, 10, 21, 22, 23, 25, 30, 40, 46, 50, 60, 70, 80, 90, 100]:
        add("truncate-%d" % cut, valid[:cut])
    for extra in [b"\0", b"\0\0", b"PK", b"PK\x05", b"PK\x05\x06"]:
        add("trailer-%s" % binascii.hexlify(extra).decode(), valid + extra)

    # -- names with unusual bytes and duplicate getinfo -------------------
    add("names-many", build_zip([
        Entry(b"a", b"1"), Entry(b"a", b"2"), Entry(b"b", b"3"),
        Entry(b"a", b"4")]))
    add("name-slash-only", build_zip([Entry(b"/", b"x")]))
    add("name-dotdot", build_zip([Entry(b"../x", b"x")]))
    add("name-backslash", build_zip([Entry(b"a\\b", b"x")]))
    add("name-empty", build_zip([Entry(b"", b"x")]))

    # -- compressed size larger than the file ------------------------------
    one = build_zip([Entry("a.txt", b"hello")])
    eocd1 = one.rfind(b"PK\x05\x06")
    add("compress-size-beyond-eof", build_zip([
        Entry(b"a.txt", b"hello", comp_size=0x1000)]))
    add("file-size-huge-stored", build_zip([
        Entry(b"a.txt", b"hello", file_size=0x100000)]))
    add("file-size-huge-deflate", build_zip([
        Entry(b"a.txt", payload("text"), method=ZIP_DEFLATED, file_size=0x100000)]))

    # deterministic pseudo-random corruptions of a deflate stream
    rnd = 0xC0FFEE
    basezip = build_zip([Entry(b"x.txt", payload("text", 400), method=ZIP_DEFLATED)])
    cdpos = basezip.find(b"PK\x01\x02")
    local_data = 30 + 5
    for k in range(40):
        rnd = (rnd * 1103515245 + 12345) & 0x7FFFFFFF
        pos = local_data + (rnd % max(1, cdpos - local_data))
        bad = bytearray(basezip)
        bad[pos] ^= (rnd >> 8) & 0xFF or 1
        add("deflate-random-%d" % k, bytes(bad))

    # -- a deterministic, smaller version of the random sweep ---------------
    # Multi-chunk members (larger than ZipExtFile.MIN_READ_SIZE = 4096) for
    # every method, clean and corrupted. These exercise the inflate window
    # across output-buffer boundaries, the bounded decompress() calls of
    # bz2/lzma, and the bytes returned before a corrupt stream is detected.
    for method, label in [(ZIP_STORED, "stored"), (ZIP_DEFLATED, "deflate"),
                          (ZIP_BZIP2, "bzip2"), (ZIP_LZMA, "lzma")]:
        for kind, size in [("lorem", 9000), ("lorem", 20000), ("zeros", 12000),
                           ("random", 6000), ("pattern", 9000), ("far", 9000),
                           ("far", 12000), ("boundary", 12288)]:
            add("multichunk-%s-%s-%d" % (label, kind, size),
                zip_bytes(method, [("big.bin", payload(kind, size)),
                                   ("small.txt", payload("short"))]))
        # two multi-chunk members in one archive
        add("multichunk-%s-two-members" % label,
            zip_bytes(method, [("first.bin", payload("lorem", 9000)),
                               ("second.bin", payload("zeros", 7000))]))
        # member whose declared uncompressed size is larger than the stream
        add("multichunk-%s-size-larger" % label,
            build_zip([Entry(b"big.bin", payload("lorem", 9000),
                             method=method, file_size=40000)]))

    for method, label in [(ZIP_DEFLATED, "deflate"), (ZIP_BZIP2, "bzip2"),
                          (ZIP_LZMA, "lzma")]:
        for kind, plain in [("lorem", payload("lorem", 12000)),
                            ("far", payload("far", 12000)),
                            ("random", payload("random", 12000))]:
            comp = compress(method, plain)
            positions = sorted({3, len(comp) // 4, len(comp) // 2,
                                (3 * len(comp)) // 4, len(comp) - 5})
            for pos in positions:
                bad = bytearray(comp)
                bad[pos] ^= 0x5A
                add("multichunk-%s-%s-flip-%d" % (label, kind, pos), build_zip([
                    Entry(b"big.bin", plain, method=method,
                          compressed=bytes(bad))]))
            for cut in [len(comp) // 3, len(comp) // 2, len(comp) - 1]:
                add("multichunk-%s-%s-trunc-%d" % (label, kind, cut), build_zip([
                    Entry(b"big.bin", plain, method=method,
                          compressed=comp[:cut])]))

    # a stored member with a flipped data byte: only the CRC can notice
    stored_plain = payload("lorem", 9000)
    bad = bytearray(stored_plain)
    bad[5000] ^= 0x01
    add("multichunk-stored-flip-data", build_zip([
        Entry(b"big.bin", stored_plain, method=ZIP_STORED, compressed=bytes(bad))]))

    return cases


# --------------------------------------------------------------------------
# Corpus assembly
# --------------------------------------------------------------------------

def build_corpus():
    cases = main_cases()
    unique = {}
    out = []
    for case in cases:
        data = case["data"]
        digest = hashlib.sha256(data).hexdigest()[:16]
        unique[digest] = data
        out.append({"name": case["name"], "archive": digest,
                    "expect": describe_archive(data)})
    return {
        "version": 1,
        "python": sys.version.split()[0],
        "archives": {k: base64.b64encode(v).decode("ascii") for k, v in unique.items()},
        "cases": out,
    }


def write_corpus(corpus):
    raw = json.dumps(corpus, sort_keys=True, separators=(",", ":")).encode()
    with open(CORPUS, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(raw)
    return len(raw)


def main(argv):
    check = "--check" in argv[1:]
    corpus = build_corpus()
    if check:
        try:
            with gzip.open(CORPUS, "rb") as f:
                stored = json.loads(f.read())
        except FileNotFoundError:
            print("corpus missing: run without --check", file=sys.stderr)
            return 1
        if stored != corpus:
            print("corpus differs from the reference Python", file=sys.stderr)
            return 1
        print("corpus ok: %d cases, %d bytes" % (
            len(corpus["cases"]), os.path.getsize(CORPUS)))
        return 0
    size = write_corpus(corpus)
    print("wrote %s: %d cases, %d raw bytes, %d bytes on disk" % (
        CORPUS, len(corpus["cases"]), size, os.path.getsize(CORPUS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
