#!/usr/bin/env python3
"""Generate the Pillow/reportlab answer corpus for BE_GO/internal/pilimage.

Every case is a byte string plus what Pillow 13.0.0 and reportlab 4.2.5's
ImageReader give for it:

  * Image.open(BytesIO(data)): format, mode, size, or the exception;
  * ImageReader.getRGBData(): the resulting mode and the SHA-256 of the data
    and (when present) the alpha band, or the exception;
  * ImageReader.getTransparent(): the bytes, None, or the exception.

The file is written gzip-compressed with a zero mtime so it is reproducible.
Run with the pinned CPython 3.13 oracle:

  python -I TESTS/fixtures/generate_pilimage_corpus.py [--check]
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import os
import random
import struct
import sys
import zlib

from PIL import Image

ADDRESS = "0x7f0000000000"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "BE_GO", "internal", "pilimage", "testdata", "corpus.json.gz")


# ---------------------------------------------------------------------------
# Pillow answers


def error_of(exc: BaseException) -> dict:
    text = str(exc)
    text = text.replace("at 0x", "at " + ADDRESS + "\x00")
    # Leave only the placeholder for the object address.
    out = []
    i = 0
    while i < len(text):
        if text.startswith(ADDRESS + "\x00", i):
            j = text.find(">", i)
            out.append(ADDRESS)
            i = j
            continue
        out.append(text[i])
        i += 1
    return {"class": type(exc).__name__, "text": "".join(out).replace("\x00", "")}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rgb_answer(im, x19=False) -> dict:
    """reportlab ImageReader.getRGBData(), reproduced without reportlab.

    x19 records only the resulting mode and byte count: the pixels come from a
    bundled codec library (divergence X19), so they are not compared.
    """
    try:
        mode = im.mode
        alpha_image = None
        if mode in ("LA", "RGBA"):
            im.load()
            alpha_image = im.split()[3 if mode == "RGBA" else 1]
            nm = mode[:-1]
            im = im.convert(nm)
            out_mode = nm
        elif mode not in ("L", "RGB", "CMYK"):
            if im.format == "PNG" and im.mode == "P" and "transparency" in im.info:
                im = im.convert("RGBA")
                alpha_image = im.split()[3]
                im = im.convert("RGB")
            else:
                im = im.convert("RGB")
            out_mode = "RGB"
        else:
            out_mode = mode
        data = im.tobytes()
        if x19:
            ans = {"mode": out_mode, "x19": True, "size": len(data)}
            if alpha_image is not None:
                ans["alpha_size"] = len(alpha_image.tobytes())
            return ans
        ans = {
            "mode": out_mode,
            "data": sha(data),
            "alpha": sha(alpha_image.tobytes()) if alpha_image is not None else None,
        }
        return ans
    except BaseException as exc:  # noqa: BLE001 - the corpus records exceptions
        return {"error": error_of(exc)}


def iptc_getint(value: bytes) -> int:
    """IptcImagePlugin.getint: the last four bytes big-endian."""
    return int.from_bytes(value[-4:].rjust(4, b"\x00"), "big")


def ojpeg_abbreviated(data: bytes, im) -> bool:
    """Old-style JPEG without an interchange stream in JPEGInterchangeFormat."""
    if 513 in im.tag_v2:
        return False
    offsets = im.tag_v2.get(273) or im.tag_v2.get(324) or ()
    if not isinstance(offsets, tuple):
        offsets = (offsets,)
    if not offsets:
        return True
    off = offsets[0]
    return data[off : off + 2] != b"\xff\xd8"


def jpeg_container_x19(data: bytes, im) -> bool:
    """Whether the pixels come from a JPEG stream in another container."""
    if im.format == "IPTC":
        comp = im.info.get((3, 120))
        return comp is not None and iptc_getint(comp) == 5
    if im.format == "BLP" and data[:4] == b"BLP1":
        return struct.unpack("<i", data[4:8])[0] == 0
    if im.format == "TIFF":
        return im.tag_v2.get(259) in (6, 7)
    return False



def transparent_answer(im) -> dict:
    """reportlab ImageReader.getTransparent()."""
    try:
        if "transparency" in im.info:
            transparency = im.info["transparency"] * 3
            palette = im.palette
            try:
                palette = palette.palette
            except Exception:  # noqa: BLE001 - the Python code catches broadly
                try:
                    palette = palette.data
                except Exception:  # noqa: BLE001
                    return {"value": None}
            return {"value": base64.b64encode(bytes(palette[transparency : transparency + 3])).decode()}
        return {"value": None}
    except BaseException as exc:  # noqa: BLE001
        return {"error": error_of(exc)}


def x19_kind(data: bytes) -> str | None:
    """Formats Pillow decodes with a platform codec library (divergence X19).

    The corpus records what a Pillow built without that codec answers: the
    plugin's _accept returns a message, so Image.open raises
    UnidentifiedImageError. WebP lossless (VP8L) is deterministic and is
    decoded, so only VP8/VP8X are treated this way.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and data[12:16] in (b"VP8 ", b"VP8X"):
        return "WEBP"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"mif1", b"msf1"):
        return "AVIF"
    return None


def unidentified_answer() -> dict:
    try:
        Image.open(io.BytesIO(b""))
    except BaseException as exc:  # noqa: BLE001
        return error_of(exc)
    raise AssertionError("empty data was identified")


def case(data: bytes) -> dict:
    entry = {"data": base64.b64encode(data).decode()}
    if x19_kind(data) is not None:
        entry["open"] = {"error": unidentified_answer()}
        return entry
    try:
        im = Image.open(io.BytesIO(data))
    except BaseException as exc:  # noqa: BLE001
        entry["open"] = {"error": error_of(exc)}
        return entry
    entry["open"] = {
        "format": im.format,
        "mode": im.mode,
        "width": im.size[0],
        "height": im.size[1],
    }
    if im.format == "EPS":
        # No Ghostscript on the production platform.
        entry["rgb"] = {"error": {"class": "OSError", "text": "Unable to locate Ghostscript on paths"}}
        entry["transparent"] = {"value": None}
        return entry
    if im.format == "JPEG2000":
        entry["rgb"] = {"error": {"class": "OSError", "text": "decoder JPEG2000 not available"}}
        entry["transparent"] = {"value": None}
        return entry
    if im.format == "ICNS" and b"jP  \x0d\x0a\x87\x0a" in data:
        # Divergence X19: ICNS JPEG 2000 entries answer as a Pillow built
        # without the codec (IcnsImagePlugin.enable_jpeg2k is False).
        entry["rgb"] = {"error": {"class": "ValueError",
                                  "text": "Unsupported icon subimage format (rebuild PIL with JPEG 2000 support to fix this)"}}
        entry["transparent"] = {"value": None}
        return entry
    if im.format == "TIFF" and im.tag_v2.get(259) in (34925, 50000, 50001):
        # Divergence X19: LZMA, ZSTD and WebP are optional libtiff codecs.
        # This records the answer of a build without them, which is the same
        # failure an unconfigured codec produces ("decoder error -2").
        entry["rgb"] = {"error": {"class": "OSError", "text": "decoder error -2"}}
        entry["transparent"] = {"value": None}
        return entry
    if im.mode == "LAB":
        # Divergence X19: Pillow converts LAB through ImageCms (lcms2), an
        # optional bundled library. This records the answer of a build whose
        # C conversion machinery is reached instead, which is the closest
        # result for this port.
        entry["rgb"] = {"error": {"class": "ValueError",
                                  "text": "conversion from LAB to RGB not supported"}}
        entry["transparent"] = {"value": None}
        return entry
    if im.format == "TIFF" and im.tag_v2.get(259) == 6 and ojpeg_abbreviated(data, im):
        # Divergence X19: an old-style JPEG stream whose tables live inside
        # the strip needs libjpeg's header assembly; this port answers
        # libtiff's decode failure, as a build without OJPEG reassembly does.
        entry["rgb"] = {"error": {"class": "OSError", "text": "decoder error -2"}}
        entry["transparent"] = {"value": None}
        return entry
    x19 = im.format == "JPEG" or jpeg_container_x19(data, im)
    if x19:
        # Divergence X19: the pixels come from a bundled lossy codec; only
        # the format, mode and size are compared. A stream libjpeg refuses
        # (or libjpeg decodes while Go's decoder does not) is not recorded:
        # which streams a codec accepts is part of the divergence.
        answer = rgb_answer(im, x19=True)
        if "error" not in answer:
            entry["rgb"] = answer
        entry["transparent"] = transparent_answer(im)
        return entry
    entry["rgb"] = rgb_answer(im)
    entry["transparent"] = transparent_answer(im)
    return entry


# ---------------------------------------------------------------------------
# Builders


def png_chunk(cid: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + cid
        + data
        + struct.pack(">I", zlib.crc32(cid + data) & 0xFFFFFFFF)
    )


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def pack_row(width: int, depth: int, ctype: int, values) -> bytes:
    channels = PNG_CHANNELS[ctype]
    out = bytearray()
    if depth < 8:
        acc = 0
        nbits = 0
        for v in values:
            v = int(v) & ((1 << depth) - 1)
            acc = (acc << depth) | v
            nbits += depth
            while nbits >= 8:
                out.append((acc >> (nbits - 8)) & 0xFF)
                nbits -= 8
        if nbits:
            out.append((acc << (8 - nbits)) & 0xFF)
        return bytes(out)
    for x in range(width):
        vals = values[x] if channels > 1 else (values[x],)
        for v in vals:
            if depth == 8:
                out.append(int(v) & 0xFF)
            else:
                out += struct.pack(">H", int(v) & 0xFFFF)
    return bytes(out)


ADAM7 = [
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
]


def png_raw(width: int, height: int, depth: int, ctype: int, pix, interlace=False, filters=None) -> bytes:
    out = bytearray()

    def one(x, y):
        return pix[y][x]

    if not interlace:
        for y in range(height):
            row = pack_row(width, depth, ctype, [one(x, y) for x in range(width)])
            out.append(0)
            out.extend(row)
        return bytes(out)
    for x0, y0, dx, dy in ADAM7:
        xs = list(range(x0, width, dx))
        ys = list(range(y0, height, dy))
        for y in ys:
            if not xs:
                continue
            out.append(0)
            out.extend(pack_row(len(xs), depth, ctype, [one(x, y) for x in xs]))
    return bytes(out)


def filter_row(kind: int, row: bytes, prev, bpp: int) -> bytes:
    if prev is None:
        prev = bytes(len(row))
    out = bytearray([kind])
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i] if i < len(prev) else 0
        c = prev[i - bpp] if i >= bpp and i - bpp < len(prev) else 0
        if kind == 0:
            v = x
        elif kind == 1:
            v = x - a
        elif kind == 2:
            v = x - b
        elif kind == 3:
            v = x - (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            v = x - pr
        out.append(v & 0xFF)
    return bytes(out)


def build_png(
    width,
    height,
    depth,
    ctype,
    pix,
    plte=None,
    trns=None,
    interlace=False,
    pre_chunks=(),
    post_chunks=(),
    idat_parts=1,
    raw=None,
    filter_types=(0,),
) -> bytes:
    out = bytearray(PNG_MAGIC)
    out += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, 1 if interlace else 0))
    for cid, data in pre_chunks:
        out += png_chunk(cid, data)
    if plte is not None:
        out += png_chunk(b"PLTE", plte)
    if trns is not None:
        out += png_chunk(b"tRNS", trns)
    for cid, data in post_chunks:
        out += png_chunk(cid, data)
    if raw is None:
        if len(filter_types) > 1:
            # Encode row by row with the requested predictors.
            rawdata = bytearray()
            prev = None
            channels = PNG_CHANNELS[ctype]
            bpp = max(1, depth * channels // 8)
            for y in range(height):
                row = pack_row(width, depth, ctype, [pix[y][x] for x in range(width)])
                f = filter_types[y % len(filter_types)]
                rawdata += filter_row(f, row, prev, bpp)
                prev = row
            raw = bytes(rawdata)
        else:
            raw = png_raw(width, height, depth, ctype, pix, interlace)
    z = zlib.compress(raw)
    n = max(1, idat_parts)
    step = (len(z) + n - 1) // n
    for i in range(0, len(z), step):
        out += png_chunk(b"IDAT", z[i : i + step])
    out += png_chunk(b"IEND", b"")
    return bytes(out)


def sample_pix(width, height, depth, ctype, seed=0, palette=None):
    rnd = random.Random(seed)
    maxv = (1 << depth) - 1 if depth < 16 else 65535
    pix = []
    for y in range(height):
        row = []
        for x in range(width):
            if palette is not None:
                row.append(rnd.randrange(palette))
            elif ctype == 0 or ctype == 3:
                row.append((x * 3 + y * 5 + seed) % (maxv + 1) if depth < 16 else (x * 257 + y * 1234 + seed) % 65536)
            else:
                ch = PNG_CHANNELS[ctype]
                if depth == 8:
                    row.append(tuple(((x * 7 + y * 11 + c * 40 + seed) % 256) for c in range(ch)))
                else:
                    row.append(tuple(((x * 1000 + y * 2000 + c * 5000 + seed) % 65536) for c in range(ch)))
        pix.append(row)
    return pix


def palette_bytes(n, step=11):
    return bytes(((i * step + c * 40) % 256) for i in range(n) for c in range(3))


# GIF ----------------------------------------------------------------------


def lzw_encode(indices, min_code_size):
    clear = 1 << min_code_size
    end = clear + 1
    table = {(i,): i for i in range(clear)}
    next_code = end + 1
    code_size = min_code_size + 1
    out = bytearray()
    bitbuf = 0
    bitcount = 0

    def emit(code):
        nonlocal bitbuf, bitcount
        bitbuf |= code << bitcount
        bitcount += code_size
        while bitcount >= 8:
            out.append(bitbuf & 0xFF)
            bitbuf >>= 8
            bitcount -= 8

    emit(clear)
    w = ()
    for k in indices:
        wk = w + (k,)
        if wk in table:
            w = wk
            continue
        emit(table[w])
        if next_code < 4096:
            table[wk] = next_code
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            emit(clear)
            table = {(i,): i for i in range(clear)}
            next_code = end + 1
            code_size = min_code_size + 1
        w = (k,)
    if w:
        emit(table[w])
    emit(end)
    if bitcount:
        out.append(bitbuf & 0xFF)
    return bytes(out)


def gif_subs(data):
    out = bytearray()
    for i in range(0, len(data), 255):
        blk = data[i : i + 255]
        out.append(len(blk))
        out += blk
    out.append(0)
    return bytes(out)


def interlace_rows(height):
    order = []
    for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
        order.extend(range(start, height, step))
    return order


def build_gif(
    width,
    height,
    indices,
    palette,
    version=b"GIF89a",
    gct_bits=None,
    local_palette=None,
    local_bits=None,
    transparency=None,
    interlace=False,
    background=0,
    comment=None,
    loop=None,
    no_gct=False,
) -> bytes:
    ncol = 1 << gct_bits if gct_bits else max(2, (len(palette) + 2) // 3)
    if gct_bits is not None:
        ncol = 1 << gct_bits
    flags = 0
    gct = b""
    if not no_gct:
        bits = gct_bits if gct_bits is not None else max(1, (ncol - 1).bit_length())
        gct = bytes(palette) + b"\x00" * (3 * (1 << bits) - len(palette))
        flags = 0x80 | 0x70 | (bits - 1)
    out = bytearray(version)
    out += struct.pack("<HHBBB", width, height, flags, background, 0)
    out += gct
    if comment:
        out += b"!\xfe" + gif_subs(comment)
    if loop is not None:
        out += b"!\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    if transparency is not None:
        out += b"!\xf9\x04\x01" + struct.pack("<H", 10) + bytes([transparency, 0])
    rows = interlace_rows(height) if interlace else list(range(height))
    seq = []
    for y in rows:
        seq.extend(indices[y * width : (y + 1) * width])
    lflags = 0x40 if interlace else 0
    lp = b""
    if local_palette is not None:
        lb = local_bits if local_bits is not None else max(1, (max(2, (len(local_palette) + 2) // 3) - 1).bit_length())
        lp = bytes(local_palette) + b"\x00" * (3 * (1 << lb) - len(local_palette))
        lflags |= 0x80 | (lb - 1)
    out += b"," + struct.pack("<HHHHB", 0, 0, width, height, lflags)
    out += lp
    mcs = max(2, (local_bits if local_palette is not None else gct_bits if gct_bits else max(1, (ncol - 1).bit_length())))
    mcs = min(11, max(2, mcs))
    out.append(mcs)
    out += gif_subs(lzw_encode(seq, mcs))
    out += b";"
    return bytes(out)


# BMP ----------------------------------------------------------------------


def bmp_file(header: bytes, palette: bytes, pixels: bytes, offset=None, magic=b"BM") -> bytes:
    off = 14 + len(header) + len(palette) if offset is None else offset
    return magic + struct.pack("<III", off + len(pixels), 0, off) + header + palette + pixels


def bmp_header40(w, h, bits, comp=0, colors=0, planes=1, size_image=0, masks=b"", extra=b"") -> bytes:
    return struct.pack("<IiiHHIIiiII", 40, w, h, planes, bits, comp, size_image, 2835, 2835, colors, 0) + masks + extra


def bmp_header12(w, h, planes, bits) -> bytes:
    return struct.pack("<IHHHH", 12, w, h, planes, bits)


def pad4(b: bytes) -> bytes:
    return b + b"\x00" * ((-len(b)) % 4)


def bmp_palette(entries, pad=4) -> bytes:
    out = bytearray()
    for rgb in entries:
        r, g, b = rgb
        out += bytes([b, g, r])
        if pad == 4:
            out += b"\x00"
    return bytes(out)


# PPM ----------------------------------------------------------------------


def ppm_plain(magic: bytes, width, height, maxval, values, comments=False) -> bytes:
    out = bytearray(magic + b"\n")
    if comments:
        out += b"# generated\n"
    out += b"%d %d\n" % (width, height)
    if maxval is not None:
        out += b"%d\n" % maxval
    line = []
    for i, v in enumerate(values):
        line.append(b"%d" % v)
        if len(line) == (3 if magic == b"P3" else 1):
            out += b" ".join(line) + b"\n"
            line = []
    if line:
        out += b" ".join(line) + b"\n"
    return bytes(out)


# Input collection

INPUTS: list[bytes] = []
SEEN: set[bytes] = set()


def add(data: bytes):
    if data not in SEEN:
        SEEN.add(data)
        INPUTS.append(data)


def add_mutations(data: bytes, cap=14):
    n = len(data)
    if n == 0:
        return
    offsets = set()
    for i in range(min(n, 12)):
        offsets.add(i)
    for frac in (13, 20, 33, 40, 50, 61, 71, 80, 90, 98):
        offsets.add(n * frac // 100)
    offsets.update([n // 3, n // 2, 2 * n // 3, n - 10, n - 5, n - 3, n - 2, n - 1])
    offs = sorted(o for o in offsets if 0 <= o < n)
    picks = offs[:cap]
    for o in picks:
        add(data[:o])
    flips = sorted({0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, n // 2, n - 2, n - 1})
    for o in flips[:10]:
        if 0 <= o < n:
            b = bytearray(data)
            b[o] ^= 0xFF
            add(bytes(b))
    add(data + b"\x00" * 4)
    add(data + b"trailing junk")
    if n > 2:
        add(data[: n // 2] + data[n // 2 + 1 :])


def base_png():
    # Pillow-written images: every mode and save option it supports.
    sizes = [(1, 1), (3, 2), (5, 3), (17, 5), (32, 9)]
    for w, h in sizes[:3]:
        for mode in ("1", "L", "LA", "RGB", "RGBA", "I;16"):
            im = Image.new(mode, (w, h))
            if mode == "I;16":
                im.putdata([(x * 257 + y * 1234) % 65536 for y in range(h) for x in range(w)])
            elif mode in ("LA", "RGBA"):
                n = 2 if mode == "LA" else 4
                im.putdata([tuple(((x * 37 + y * 53 + c * 61) % 256) for c in range(n)) for y in range(h) for x in range(w)])
            else:
                im.putdata([(x * 37 + y * 53) % (2 if mode == "1" else 256) for y in range(h) for x in range(w)])
            for interlace in (False, True):
                for optimize in (False, True):
                    b = io.BytesIO()
                    im.save(b, "PNG", optimize=optimize, interlace=interlace)
                    add(b.getvalue())
    # Palette images with palette sizes from 2 to 256.
    for w, h in ((5, 3), (17, 5)):
        for n in (2, 3, 4, 5, 16, 17, 255, 256):
            im = Image.new("P", (w, h))
            im.putpalette(palette_bytes(n))
            im.putdata([(x * 3 + y * 7) % n for y in range(h) for x in range(w)])
            for kw in ({}, {"optimize": True}, {"transparency": 0}, {"transparency": n - 1}, {"interlace": True}):
                b = io.BytesIO()
                im.save(b, "PNG", **kw)
                add(b.getvalue())
    # Low bit depth L images written through Pillow's bits option.
    for bits in (1, 2, 4):
        im = Image.new("L", (8, 3))
        im.putdata([((x + y) % (1 << bits)) * (255 // ((1 << bits) - 1)) for y in range(3) for x in range(8)])
        for optimize in (False, True):
            b = io.BytesIO()
            im.save(b, "PNG", bits=bits, optimize=optimize)
            add(b.getvalue())
    # Truecolour / alpha transparency options.
    im = Image.new("RGB", (5, 3))
    im.putdata([((x * 41) % 256, (y * 67) % 256, (x * y * 13) % 256) for y in range(3) for x in range(5)])
    for kw in ({}, {"transparency": (0, 0, 0)}, {"interlace": True}):
        b = io.BytesIO()
        im.save(b, "PNG", **kw)
        add(b.getvalue())
    im = Image.new("LA", (5, 3))
    im.putdata([(x * 51, y * 81) for y in range(3) for x in range(5)])
    b = io.BytesIO()
    im.save(b, "PNG")
    add(b.getvalue())
    im = Image.new("L", (5, 3))
    im.putdata([(x * 51 + y) % 256 for y in range(3) for x in range(5)])
    b = io.BytesIO()
    im.save(b, "PNG", transparency=7)
    add(b.getvalue())


def base_png_hand():
    # Every valid colour type/bit depth combination.
    for ctype, depths in ((0, (1, 2, 4, 8, 16)), (2, (8, 16)), (3, (1, 2, 4, 8)), (4, (8, 16)), (6, (8, 16))):
        for depth in depths:
            for w, h in ((1, 1), (3, 2), (5, 4), (8, 3)):
                if ctype == 3:
                    n = min(1 << depth, 256)
                    pix = sample_pix(w, h, depth, ctype, seed=w * 7 + h, palette=n)
                    plte = palette_bytes(n)
                    add(build_png(w, h, depth, ctype, pix, plte=plte))
                    add(build_png(w, h, depth, ctype, pix, plte=plte, interlace=True))
                else:
                    pix = sample_pix(w, h, depth, ctype, seed=w * 7 + h + depth)
                    add(build_png(w, h, depth, ctype, pix))
                    add(build_png(w, h, depth, ctype, pix, interlace=True))
    # tRNS for every colour type.
    for ctype, depth, trns in (
        (0, 1, b"\x00\x00"),
        (0, 4, b"\x00\x05"),
        (0, 8, b"\x00\x07"),
        (0, 16, b"\x00\x2a"),
        (2, 8, b"\x00\x01\x00\x02\x00\x03"),
        (2, 16, b"\x00\x01\x00\x02\x00\x03"),
        (3, 8, b"\x00"),
        (3, 8, b"\x00\xff"),
        (3, 8, b"\x80\x40\x20"),
        (3, 8, b"\xff\xff\x00\xff\xff"),
        (3, 4, b"\x00"),
        (3, 2, b"\x01\x02"),
    ):
        pix = sample_pix(5, 3, depth, ctype, seed=3, palette=1 << depth if ctype == 3 else None)
        plte = palette_bytes(1 << depth) if ctype == 3 else None
        add(build_png(5, 3, depth, ctype, pix, plte=plte, trns=trns))
    # APNG first frame: with fcTL before IDAT (bbox) and IDAT as default image.
    for bbox in ((0, 0, 4, 3), (1, 1, 3, 2)):
        x0, y0, x1, y1 = bbox
        fw, fh = x1 - x0, y1 - y0
        raw = b"".join(
            bytes([0]) + b"".join(bytes((10 + y, 20 + x, 30)) for x in range(fw)) for y in range(fh)
        )
        out = bytearray(PNG_MAGIC)
        out += png_chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
        out += png_chunk(b"acTL", struct.pack(">II", 2, 0))
        out += png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, fw, fh, x0, y0, 1, 10, 0, 0))
        out += png_chunk(b"IDAT", zlib.compress(raw))
        out += png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 1, fw, fh, x0, y0, 1, 10, 0, 0))
        out += png_chunk(b"fdAT", struct.pack(">I", 2) + zlib.compress(raw))
        out += png_chunk(b"IEND", b"")
        add(bytes(out))
        # Default image variant: IDAT before the first fcTL.
        out = bytearray(PNG_MAGIC)
        out += png_chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
        out += png_chunk(b"acTL", struct.pack(">II", 1, 0))
        full = b"".join(bytes([0]) + bytes((10 + y, 20, 30)) * 4 for y in range(3))
        out += png_chunk(b"IDAT", zlib.compress(full))
        out += png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 4, 3, 0, 0, 1, 10, 0, 0))
        out += png_chunk(b"IEND", b"")
        add(bytes(out))
    # Multiple IDAT chunks, ancillary chunks, filters.
    pix = sample_pix(16, 9, 8, 2, seed=5)
    add(build_png(16, 9, 8, 2, pix, idat_parts=7))
    add(
        build_png(
            16,
            9,
            8,
            2,
            pix,
            pre_chunks=(
                (b"gAMA", struct.pack(">I", 45455)),
                (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
                (b"sRGB", b"\x00"),
                (b"tEXt", b"Comment\x00hello"),
                (b"zTXt", b"Title\x00\x00" + zlib.compress(b"compressed text")),
                (b"iTXt", b"Author\x00\x00\x00en\x00\x00Someone"),
                (b"eXIf", b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00"),
            ),
            post_chunks=((b"tEXt", b"After\x00idat"),),
        )
    )
    for filters in ((0, 1, 2, 3, 4), (4, 4, 4, 4), (1, 2, 1, 2)):
        add(build_png(7, 6, 8, 6, sample_pix(7, 6, 8, 6, seed=9), filter_types=filters))
    # Odd palette lengths and a missing palette.
    add(build_png(3, 1, 8, 3, [[0, 1, 2]], plte=b"\x01\x02\x03\x04"))
    add(build_png(3, 1, 8, 3, [[0, 1, 2]], plte=b""))
    add(build_png(3, 1, 8, 3, [[0, 1, 2]]))
    add(build_png(3, 1, 8, 3, [[0, 1, 2]], plte=palette_bytes(300)))


def base_gif():
    for w, h in ((1, 1), (4, 2), (5, 3), (17, 5), (32, 9)):
        for n in (2, 3, 4, 5, 16, 17, 255, 256):
            im = Image.new("P", (w, h))
            im.putpalette(palette_bytes(n))
            im.putdata([(x * 3 + y * 7) % n for y in range(h) for x in range(w)])
            for kw in ({}, {"optimize": True}, {"interlace": True}):
                b = io.BytesIO()
                im.save(b, "GIF", **kw)
                add(b.getvalue())
            if n > 1:
                b = io.BytesIO()
                im.save(b, "GIF", transparency=0)
                add(b.getvalue())
    for mode in ("L", "1", "RGB", "RGBA"):
        for w, h in ((3, 2), (9, 5)):
            im = Image.new(mode, (w, h))
            if mode in ("L", "1"):
                im.putdata([(x * 37 + y * 53) % (2 if mode == "1" else 256) for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x * 71) % 256) for y in range(h) for x in range(w)])
            for kw in ({}, {"interlace": True}, {"optimize": True}):
                b = io.BytesIO()
                im.save(b, "GIF", **kw)
                add(b.getvalue())
    # Hand-built: grayscale palette (Palette not needed -> L), local palette,
    # no palette, comments, loop, transparency.
    pal = palette_bytes(4)
    idx = [0, 1, 2, 3, 1, 0] * 3
    add(build_gif(6, 3, idx, pal, gct_bits=2))
    add(build_gif(6, 3, idx, pal, gct_bits=2, interlace=True))
    add(build_gif(6, 3, idx, pal, gct_bits=2, transparency=2, comment=b"hello", loop=3))
    add(build_gif(6, 3, idx, pal, gct_bits=2, local_palette=palette_bytes(8), local_bits=3))
    add(build_gif(6, 3, idx, pal, no_gct=True))
    gray256 = bytes([i for i in range(256) for _ in range(3)])
    add(build_gif(6, 3, [0, 1, 0, 255, 128, 64] * 3, gray256, gct_bits=8))
    add(build_gif(6, 3, idx, pal, gct_bits=2, version=b"GIF87a"))
    add(build_gif(3, 2, [0, 1, 2, 3, 4, 5], palette_bytes(8), gct_bits=3, transparency=5))


def base_bmp():
    for mode in ("1", "L", "P", "RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (17, 5), (32, 9)):
            im = Image.new(mode, (w, h))
            if mode == "1":
                im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
            elif mode == "L":
                im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
            elif mode == "P":
                im.putpalette(palette_bytes(16))
                im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x * 71 + y) % 256) for y in range(h) for x in range(w)])
            b = io.BytesIO()
            im.save(b, "BMP")
            add(b.getvalue())
    # Hand-built variants.
    def build(hdr, pal, data, **kw):
        add(bmp_file(hdr, pal, data, **kw))

    palbw = bmp_palette([(0, 0, 0), (255, 255, 255)])
    pal16 = bmp_palette([(i * 16, i * 8, i * 4) for i in range(16)])
    palgray16 = bmp_palette([(i * 17, i * 17, i * 17) for i in range(16)])
    pal256 = bmp_palette([(i, (i * 7) % 256, (i * 13) % 256) for i in range(256)])
    palgray256 = bmp_palette([(i, i, i) for i in range(256)])
    for w, h in ((1, 1), (3, 2), (9, 3), (17, 4)):
        build(bmp_header40(w, h, 1, colors=2), palbw, pad4(b"\xaa" * ((w + 7) // 8)) * h)
        build(bmp_header40(w, h, 1, colors=2), bmp_palette([(0, 0, 0), (16, 32, 48)]), pad4(b"\xaa" * ((w + 7) // 8)) * h)
        build(bmp_header40(w, h, 4, colors=16), palgray16, pad4(b"\x12\x34" * ((w + 3) // 4)) * h)
        build(bmp_header40(w, h, 4, colors=16), pal16, pad4(b"\x12\x34" * ((w + 3) // 4)) * h)
        build(bmp_header40(w, h, 8, colors=256), palgray256, pad4(bytes((i * 3) % 256 for i in range(w))) * h)
        build(bmp_header40(w, h, 8, colors=256), pal256, pad4(bytes((i * 5) % 256 for i in range(w))) * h)
        build(bmp_header40(w, h, 8, colors=0), pal256, pad4(bytes((i * 5) % 256 for i in range(w))) * h)
        build(bmp_header40(w, h, 16), b"", (struct.pack("<HH", 0x7FFF, 0x0000) * ((w + 1) // 2)) * h)
        build(bmp_header40(w, h, 24), b"", pad4(b"\x0a\x14\x1e" * w) * h)
        build(bmp_header40(w, h, 32), b"", (struct.pack("<I", 0x000A141E) * w) * h)
        build(bmp_header40(w, -h, 24), b"", pad4(b"\x0a\x14\x1e" * w) * h)
        build(bmp_header40(w, h, 32, comp=3, masks=struct.pack("<III", 0x000000FF, 0x0000FF00, 0x00FF0000)),
              b"", (struct.pack("<I", 0x000A141E) * w) * h)
        build(bmp_header40(w, h, 32, comp=3, masks=struct.pack("<IIII", 0xFF000000, 0xFF0000, 0xFF00, 0xFF)),
              b"", (struct.pack("<I", 0x400A141E) * w) * h)
        build(bmp_header40(w, h, 16, comp=3, masks=struct.pack("<III", 0xF800, 0x7E0, 0x1F)),
              b"", (struct.pack("<H", 0x7FFF) * w) * h)
        build(bmp_header40(w, h, 16, comp=3, masks=struct.pack("<III", 0x7C00, 0x3E0, 0x1F)),
              b"", (struct.pack("<H", 0x7FFF) * w) * h)
        build(bmp_header12(w, h, 1, 1), bmp_palette([(0, 0, 0), (255, 255, 255)], pad=3),
              (b"\xaa" * ((w + 7) // 8) + b"\x00" * 3) * h)
        build(bmp_header12(w, h, 1, 24), b"", pad4(b"\x0a\x14\x1e" * w) * h)
        build(bmp_header12(w, h, 1, 8), bmp_palette([(i, i, i) for i in range(16)], pad=3),
              pad4(bytes(range(w))) * h)
        hdr56 = bmp_header40(w, h, 32, comp=3, masks=struct.pack("<IIII", 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000))
        build(hdr56, b"", (struct.pack("<I", 0x400A141E) * w) * h)
        hdr108 = bmp_header40(w, h, 24) + struct.pack("<IIII", 0x00FF0000, 0x0000FF00, 0x000000FF, 0) + b"\x00" * 40
        build(hdr108, b"", pad4(b"\x0a\x14\x1e" * w) * h)
        hdr124 = hdr108 + struct.pack("<IIII", 0, 0, 0, 0)
        build(hdr124, b"", pad4(b"\x0a\x14\x1e" * w) * h)
        # DIB.
        add(bmp_header40(w, h, 24) + pad4(b"\x0a\x14\x1e" * w) * h)
        add(bmp_header12(w, h, 1, 24) + pad4(b"\x0a\x14\x1e" * w) * h)
        add(bmp_header40(w, h, 8, colors=256) + pal256 + pad4(bytes(range(w))) * h)
    # RLE8 and RLE4, valid and ending early/overflowing.
    palrle = bmp_palette([(i, (i * 3) % 256, (i * 7) % 256) for i in range(256)])
    rle8_rows = bytes([4, 0, 1, 2, 3, 0, 0, 3, 1, 0, 0, 0, 1])
    build(bmp_header40(4, 2, 8, comp=1, colors=256), palrle, rle8_rows)
    build(bmp_header40(4, 2, 8, comp=1, colors=256), palrle, bytes([4, 0, 1, 2, 3, 0, 1]))
    build(bmp_header40(4, 2, 8, comp=1, colors=256), palrle, bytes([4, 0, 1, 2, 3, 0, 0, 2, 0, 0, 1, 0, 2, 4, 5, 0, 0, 0, 1]))
    build(bmp_header40(4, 2, 8, comp=1, colors=256), palrle, bytes([9, 1, 0, 0, 0, 1]))
    build(bmp_header40(4, 2, 4, comp=2, colors=16), bmp_palette([(i, i, i) for i in range(16)]),
          bytes([4, 0x01, 0, 0, 2, 0x23, 0, 0, 0, 1]))
    build(bmp_header40(4, 2, 4, comp=2, colors=16), bmp_palette([(i, i, i) for i in range(16)]),
          bytes([4, 0x01, 0, 2, 0x12, 0x34, 0, 1]))
    build(bmp_header40(4, 2, 4, comp=2, colors=16), bmp_palette([(i, i, i) for i in range(16)]),
          bytes([0, 2, 2, 1, 3, 1, 0, 1]))


def base_ppm():
    for w, h in ((1, 1), (3, 2), (17, 5), (32, 9)):
        for mode in ("1", "L", "RGB", "I;16", "F"):
            im = Image.new(mode, (w, h))
            if mode == "1":
                im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
            elif mode == "L":
                im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            elif mode == "I;16":
                im.putdata([(x * 257 + y * 1234) % 65536 for y in range(h) for x in range(w)])
            else:
                im.putdata([float((x * 3 + y) % 7) / 2 for y in range(h) for x in range(w)])
            b = io.BytesIO()
            im.save(b, "PPM")
            add(b.getvalue())
    # ASCII variants with comments and maxval scaling.
    for maxval in (1, 2, 3, 7, 15, 100, 255, 256, 1000, 65535):
        vals = [0, 1, maxval // 2, maxval]
        add(ppm_plain(b"P2", 2, 2, maxval, vals, comments=True))
        vals3 = []
        for v in (0, 1, maxval // 3, maxval // 2, maxval):
            vals3.extend((v, min(maxval, v + 1), maxval - v))
        add(ppm_plain(b"P3", 2, 1, maxval, vals3[:6], comments=True))
    for maxval in (100, 255, 1000, 65535):
        add(b"P5\n3 2\n%d\n" % maxval + bytes([0, 1, 2, 3, 4, 5]))
        add(b"P5\n2 1\n%d\n" % maxval + struct.pack(">HH", 0, maxval))
        add(b"P6\n2 1\n%d\n" % maxval + bytes([10, 20, 30, 40, 50, 60]))
        add(b"P6\n1 1\n%d\n" % maxval + struct.pack(">HHH", 1, 2, 3))
    add(ppm_plain(b"P1", 3, 2, None, [1, 0, 1, 0, 1, 0]))
    add(b"P4\n3 2\n\xa0\x40")
    add(b"P4\n8 1\n\xaa")
    add(ppm_plain(b"P1", 3, 2, None, [1, 0, 1, 0, 1, 0], comments=True))
    # PAM-style extensions Pillow's PPM plugin reads.
    add(b"PyP\n2 1\n255\n\x00\x01")
    add(b"PyRGBA\n1 1\n255\n\x0a\x14\x1e\x28")
    add(b"PyCMYK\n1 1\n255\n\x01\x02\x03\x04")
    add(b"P0CMYK\n1 1\n255\n\x01\x02\x03\x04")
    add(b"Pf\n2 1\n-1.0\n" + struct.pack(">ff", 1.5, 2.5))
    add(b"Pf\n3 1\n1.0\n" + struct.pack(">fff", 0.0, 1.5, 300.0))


def base_jpeg():
    for mode in ("L", "RGB", "CMYK"):
        for w, h in ((1, 1), (3, 2), (17, 5)):
            im = Image.new(mode, (w, h))
            if mode == "L":
                im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x + y) % 256) for y in range(h) for x in range(w)])
            for kw in ({}, {"progressive": True, "quality": 50}, {"quality": 95, "subsampling": 0}):
                b = io.BytesIO()
                im.save(b, "JPEG", **kw)
                add(b.getvalue())
    # Hand-built headers: 16-bit layers, bad layer count, Adobe APP14, EOI-only.
    def sof(layers, bits=8, w=3, h=2, marker=0xFFC0, adobe=None):
        body = struct.pack(">BHHB", bits, h, w, layers) + bytes(range(1, layers + 1)) + b"\x11\x00"
        seg = struct.pack(">H", len(body) + 2) + body
        app = b""
        if adobe is not None:
            app = b"\xff\xee" + struct.pack(">H", 14) + b"Adobe" + struct.pack(">HHB", 100, 0, adobe) + b"\x00"
        return b"\xff\xd8" + app + bytes([marker >> 8, marker & 0xFF]) + seg + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\xff\xd9"

    for layers in (1, 3, 4):
        for marker in (0xFFC0, 0xFFC1, 0xFFC2, 0xFFC9):
            add(sof(layers, marker=marker))
    for adobe in (0, 1, 2):
        add(sof(3, adobe=adobe))
    add(sof(3, bits=12))
    add(sof(2))
    add(sof(5))
    add(b"\xff\xd8\xff\xd9")
    add(b"\xff\xd8\xff")
    add(b"\xff\xd8")


def add_all_prefixes(data: bytes, limit=140):
    if len(data) > limit:
        return
    for n in range(len(data)):
        add(data[:n])


def add_crc_mutations(data: bytes):
    # Flip a byte in each chunk's CRC and once in its payload.
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        cid = data[pos + 4 : pos + 8]
        payload = pos + 8
        crc = payload + length
        for where in (crc, payload + length // 2):
            if where < len(data):
                b = bytearray(data)
                b[where] ^= 0xFF
                add(bytes(b))
        pos = crc + 4


def base_more_cases():
    # Explicit CRC failures and every truncation of small files.
    sample = build_png(
        5,
        3,
        8,
        3,
        sample_pix(5, 3, 8, 3, seed=11, palette=4),
        plte=palette_bytes(4),
        trns=b"\x00\xff",
        pre_chunks=((b"tEXt", b"k\x00v"), (b"gAMA", struct.pack(">I", 45455))),
        idat_parts=3,
    )
    add_crc_mutations(sample)
    add_all_prefixes(sample)
    im = Image.new("P", (4, 2))
    im.putpalette(palette_bytes(8))
    im.putdata([0, 1, 2, 3, 4, 5, 6, 7])
    b = io.BytesIO()
    im.save(b, "GIF", transparency=2)
    gif = b.getvalue()
    add_all_prefixes(gif)
    bmp = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(bmp, "BMP")
    add_all_prefixes(bmp.getvalue())
    # Palettes one entry past Pillow's 256-colour limit.
    for n in (256, 257, 300):
        pal = bmp_palette([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(n)])
        add(bmp_file(bmp_header40(2, 1, 8, colors=n), pal, b"\x00\x01"))
    for n in (767, 768, 769, 770, 771, 772, 900):
        add(build_png(3, 1, 8, 3, [[0, 1, 2]], plte=bytes((i * 7) % 256 for i in range(n))))
    # Two-frame GIF with disposal and background (frame one must match).
    im0 = Image.new("P", (6, 4))
    im0.putpalette(palette_bytes(8))
    im0.putdata([(x + y) % 8 for y in range(4) for x in range(6)])
    im1 = Image.new("P", (6, 4))
    im1.putpalette(palette_bytes(8))
    im1.putdata([(x * y) % 8 for y in range(4) for x in range(6)])
    b = io.BytesIO()
    im0.save(b, "GIF", save_all=True, append_images=[im1], duration=[10, 20], loop=0,
             disposal=[2, 3], background=3, transparency=1)
    add(b.getvalue())
    # JPEG headers claiming decompression bombs.
    def jpeg_sof(w, h, layers=3):
        body = struct.pack(">BHHB", 8, h, w, layers) + bytes(range(1, layers + 1)) + b"\x11\x00"
        return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(body) + 2) + body + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xff\xd9"

    for w, h in ((20000, 20000), (50000, 50000)):
        add(jpeg_sof(w, h))


def base_other_signatures():
    sigs = {
        "AVIF": b"\x00\x00\x00\x18ftypavif",
        "BLP": b"BLP1",
        "BUFR": b"BUFR",
        "CUR": b"\x00\x00\x02\x00",
        "PCX": b"\x0a\x05",
        "DCX": struct.pack("<I", 0x3ADE68B1),
        "DDS": b"DDS ",
        "EPS": b"%!PS",
        "FITS": b"SIMPLE",
        "FLI": b"\x00\x00\x00\x00" + struct.pack("<H", 0xAF11) + b"\x00" * 8 + struct.pack("<H", 0),
        "FTEX": b"FTEX",
        "GBR": struct.pack("<I", 20) + struct.pack("<I", 1),
        "GRIB": b"GRIB\x00\x00\x00\x01",
        "HDF5": b"\x89HDF\r\n\x1a\n",
        "JPEG2000": b"\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a",
        "ICNS": b"icns",
        "ICO": b"\x00\x00\x01\x00",
        "IM": b"Image Type: 1\n",
        "IMT": b"width 1\nheight 1\npixel n8\n\x0c",
        "IPTC": b"\x1c\x02\x05\x00\x02hi\x1c\x03\x3c\x00\x02\x01\x00\x1c\x03\x14\x00\x02\x00\x01\x1c\x03\x1e\x00\x02\x00\x01\x1c\x03\x78\x00\x02\x00\x01\x1c\x08\x0a\x00\x02\x00\x00",
        "MCIDAS": b"\x00\x00\x00\x00\x00\x00\x00\x04",
        "MPEG": b"\x00\x00\x01\xb3",
        "TIFF": b"II*\x00",
        "MSP": b"DanM",
        "PCD": b"\x00" * 2048 + b"PCD_",
        "PIXAR": b"\x80\xe8\x00\x00",
        "PSD": b"8BPS",
        "QOI": b"qoif",
        "SGI": struct.pack(">H", 474),
        "SPIDER": b"\x00" * 92,
        "SUN": struct.pack(">I", 0x59A66A95),
        "TGA": bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 2, 0, 24, 0]),
        "WEBP": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
        "WMF": b"\xd7\xcd\xc6\x9a\x00\x00",
        "XBM": b"#define x_width 8\n",
        "XPM": b"/* XPM */",
        "XVTHUMB": b"P7 332",
    }
    for name, sig in sigs.items():
        for junk in (b"", b"\x00" * 64, bytes(range(64, 128)), b"\xff" * 40):
            add(sig + junk)
    # A minimal EPS that Pillow identifies.
    add(b"%!PS-Adobe-3.0\n%%BoundingBox: 0 0 10 10\n%%EndComments\n")
    add(b"%!PS-Adobe-3.0\n%%BoundingBox: 0 0 10 10\n100 100 moveto\n")


def dxt_dds(w, h, fmt):
    im = Image.new("RGB", (w, h))
    im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y * 11) % 256) for y in range(h) for x in range(w)])
    b = io.BytesIO()
    im.save(b, "DDS", pixel_format=fmt)
    return b.getvalue()


def base_more_formats():
    # QOI: Pillow writer plus hand-built op streams.
    def qoi(w, h, ch, body, cs=0):
        return b"qoif" + struct.pack(">II", w, h) + bytes([ch, cs]) + body

    for mode in ("RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (9, 5)):
            im = Image.new(mode, (w, h))
            if mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x * 71) % 256) for y in range(h) for x in range(w)])
            b = io.BytesIO()
            im.save(b, "QOI")
            add(b.getvalue())
    add(qoi(4, 1, 4, b"\xfe\x0a\x14\x1e\xfe\x28\x32\x3c\xff\x46\x50\x5a\x80\xc0"))
    add(qoi(4, 1, 3, b"\xfe\x0a\x14\x1e\xfe\x28\x32\x3c\xff\x46\x50\x5a\x80"))
    add(qoi(4, 1, 4, b"\x00\x40\x01\x80\x02\xc0\x03\x64\x40"))
    add(qoi(4, 1, 4, b"\xfd\x05\x06\x07\x08\x09"))
    add(qoi(2, 2, 4, b"\xff\x01\x02\x03\x04\xff\x05\x06\x07\x08\xff\x09\x0a\x0b\x0c", cs=1))
    add(qoi(1, 1, 4, b""))
    add(qoi(1, 1, 4, b"\xff\x01\x02"))

    # XBM: Pillow writer and hand-built headers.
    for w, h in ((1, 1), (8, 2), (9, 3), (17, 5)):
        im = Image.new("1", (w, h))
        im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
        b = io.BytesIO()
        im.save(b, "XBM")
        add(b.getvalue())
    add(b"#define x_width 8\n#define x_height 2\nstatic unsigned char x_bits[] = {\n0xff, 0x00};\n")
    add(b"#define x_width 4\n#define x_height 2\nstatic unsigned char x_bits[] = {0x0f,0xf0};\n")
    add(b"/* comment */\n#define x_width 8\n#define x_height 1\n#define x_x_hot 3\n#define x_y_hot 1\nstatic char x_bits[] = {\n0xaa};\n")
    add(b"#define x_width 8\nstatic char x_bits[] = {\n0xaa};\n")
    add(b"#define x_width 8\n#define x_height 2\nstatic char x_bits[] = {\n0xaa};\n")

    # XPM: hand-built palettes, transparency and large palettes.
    def xpm(w, h, colors, rows, extra=""):
        out = b"/* XPM */\nstatic char *x[] = {\n"
        out += ('"%d %d %d %d",\n' % (w, h, len(colors), len(colors[0][0]))).encode()
        for key, col in colors:
            out += ('"%s c %s",\n' % (key, col)).encode()
        for r in rows:
            out += ('"%s",\n' % r).encode()
        out += b"};\n" + extra.encode()
        return out

    add(xpm(2, 2, [("0", "#000000"), ("1", "#FFFFFF")], ["01", "10"]))
    add(xpm(2, 2, [("0", "#000000"), ("1", "None")], ["01", "10"]))
    add(xpm(2, 2, [("aa", "#102030"), ("bb", "#405060")], ["aabb", "bbaa"]))
    add(xpm(2, 1, [("0", "#000"), ("1", "#fff")], ["01"]))
    add(xpm(1, 1, [("0", "red")], ["0"]))
    add(b'/* XPM */\nstatic char *x[] = {\n"2 2 2 1",\n"0 c #000000"\n};\n')
    big = [(("%03d" % i), "#%02x%02x%02x" % (i % 256, (i * 3) % 256, (i * 7) % 256)) for i in range(300)]
    add(xpm(2, 1, big, ["000001"]))
    bigt = [(("%03d" % i), "None" if i == 5 else "#%02x%02x%02x" % (i % 256, 5, 7)) for i in range(300)]
    add(xpm(1, 1, bigt, ["005"]))

    # XV thumbnail: RGB332 palette and raw indices.
    add(b"P7 332\n# comment\n2 2 255\n" + bytes([0, 1, 127, 255]))
    add(b"P7 332\n1 1\n" + b"\x10")
    add(b"P7 332\n")
    add(b"P7 332\n# c\n")

    # IMT: text header with the Ctrl-L image separator.
    add(b"width 2\nheight 2\npixel n8\n\x0c" + bytes([1, 2, 3, 4]))
    add(b"* comment\nwidth 1\nheight 1\npixel n8\n\x0c\x07")
    add(b"width 1\nheight 1\n\x0c\x07")
    add(b"width 3\nheight 1\npixel n8\x0c" + bytes([9, 8, 7]))

    # GBR: version 1 and 2 brushes, L and RGBA.
    def gbr(version, w, h, depth, comment, spacing=0):
        hs = 20 + len(comment) + 1 if version == 1 else 28 + len(comment) + 1
        out = struct.pack(">5I", hs, version, w, h, depth)
        if version == 2:
            out += b"GIMP" + struct.pack(">I", spacing)
        out += comment + b"\x00"
        bands = 1 if depth == 1 else 4
        return out + bytes(((i * 13 + 1) % 256) for i in range(w * h * bands))

    for v in (1, 2):
        for depth in (1, 4):
            add(gbr(v, 3, 2, depth, b"hi"))
    add(gbr(1, 1, 1, 1, b""))
    add(b"FTEX")

    # FTEX: uncompressed and DXT1 textures.
    def ftex(w, h, fmt, data, where=48):
        out = b"FTEX" + struct.pack("<i2i2i", 0, w, h, 1, 1) + struct.pack("<2i", fmt, where)
        out = out.ljust(where, b"\x00")
        return out + struct.pack("<i", len(data)) + data

    add(ftex(2, 2, 1, bytes(range(12))))
    dds_block = dxt_dds(4, 4, "DXT1")
    add(ftex(4, 4, 0, dds_block[128:]))
    add(ftex(1, 1, 1, b"\x01\x02\x03"))
    add(ftex(2, 2, 1, b"\x01\x02"))

    # MSP: version 1 writer plus hand-built version 2 RLE.
    for w, h in ((1, 1), (8, 2), (9, 3), (17, 5)):
        im = Image.new("1", (w, h))
        im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
        b = io.BytesIO()
        im.save(b, "MSP")
        add(b.getvalue())

    def msp_v2(w, h, rows):
        header = [0] * 16
        header[0], header[1] = 0x694C, 0x536E  # "LinS"
        header[2], header[3] = w, h
        header[4], header[5] = 1, 1
        header[6], header[7] = 1, 1
        checksum = 0
        for v in header:
            checksum ^= v
        header[12] = checksum
        out = b"".join(struct.pack("<H", v) for v in header)
        out += b"".join(struct.pack("<H", len(r)) for r in rows)
        for r in rows:
            out += r
        return out

    add(msp_v2(8, 2, [bytes([0, 8, 0xAA]), bytes([0x04, 0x0F])]))
    add(msp_v2(8, 1, [b""]))
    add(msp_v2(8, 2, [bytes([0, 4, 0xFF])]))
    add(msp_v2(8, 1, [bytes([0x08, 1, 2, 3, 4, 5, 6, 7, 8])]))

    # MCIDAS: 8, 16 and 32 bit areas.
    def mcid(w, h, bpp, data, extra=0, stride_extra=0):
        m = bytearray(256)
        m[0:8] = b"\x00\x00\x00\x00\x00\x00\x00\x04"
        struct.pack_into("!i", m, 32, h)
        struct.pack_into("!i", m, 36, w)
        struct.pack_into("!i", m, 40, bpp)
        struct.pack_into("!i", m, 52, 0)
        struct.pack_into("!i", m, 56, extra)
        struct.pack_into("!i", m, 132, 256 + extra)
        return bytes(m) + b"\x00" * extra + data

    for bpp in (1, 2, 4):
        add(mcid(2, 2, bpp, bytes((i * 17) % 256 for i in range(2 * 2 * bpp))))
    add(mcid(1, 1, 1, b"\x2a"))

    # PIXAR: 1024-byte header and dumped RGB.
    def pixar(w, h, mode=(14, 2), data=None):
        hdr = bytearray(1024)
        struct.pack_into("<H", hdr, 416, h)
        struct.pack_into("<H", hdr, 418, w)
        struct.pack_into("<H", hdr, 424, mode[0])
        struct.pack_into("<H", hdr, 426, mode[1])
        if data is None:
            data = bytes((i * 7) % 256 for i in range(w * h * 3))
        return bytes(hdr) + data

    add(pixar(2, 2))
    add(pixar(1, 1))
    add(pixar(2, 1, mode=(15, 2)))

    # SUN raster: raw depths and RLE.
    def sun(w, h, depth, ftype, data, pal=b"", ptype=0):
        return struct.pack(">8I", 0x59A66A95, w, h, depth, len(data), ftype, ptype, len(pal)) + pal + data

    for depth in (1, 4, 8, 24, 32):
        for ftype in ((0, 3) if depth in (24, 32) else (0,)):
            stride = ((w * depth + 15) // 16) * 2
            data = bytes(((i * 29 + 3) % 256) for i in range(stride * 3))
            add(sun(3, 3, depth, ftype, data))
    add(sun(2, 1, 8, 0, bytes([0, 1]), pal=bytes(range(24)), ptype=1))
    add(sun(2, 1, 4, 0, bytes([0x12]), pal=bytes(range(24)), ptype=1))
    add(sun(2, 1, 24, 0, bytes([10, 20, 30, 40, 50, 60])))
    add(sun(2, 1, 32, 0, bytes([10, 20, 30, 40, 50, 60, 70, 80])))
    add(sun(4, 1, 8, 2, b"\x80\x03\x11\x01\x22\x33\x44\x55"))
    add(sun(8, 1, 8, 2, b"\x03\x11\x22\x33\x44"))
    add(sun(4, 1, 8, 2, b"\x80\x00\xaa\xaa"))
    add(sun(4, 1, 8, 2, b"\x80\x10\xaa"))
    add(sun(2, 1, 24, 2, b"\x01\x11\x02\x22\x03\x33"))
    add(sun(1, 1, 4, 2, b"\x01\x05"))
    add(sun(2, 1, 8, 4, bytes([1, 2])))
    add(sun(2, 1, 8, 5, bytes([1, 2])))

    # SGI: writer for 8-bit, hand-built 16-bit and RLE.
    for mode in ("L", "RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (9, 3)):
            im = Image.new(mode, (w, h))
            bands = len(mode)
            if mode == "L":
                im.putdata([(x * 37 + y * 5) % 256 for y in range(h) for x in range(w)])
            else:
                im.putdata([tuple((x * 37 + y * 5 + c * 61) % 256 for c in range(bands)) for y in range(h) for x in range(w)])
            for bpc in (1, 2):
                b = io.BytesIO()
                im.save(b, "SGI", bpc=bpc)
                add(b.getvalue())

    def sgi(w, h, z, bpc, comp, body):
        hdr = bytearray(512)
        struct.pack_into(">H", hdr, 0, 474)
        hdr[2] = comp
        hdr[3] = bpc
        struct.pack_into(">HHHH", hdr, 4, 3 if z > 1 else 2, w, h, z)
        return bytes(hdr) + body

    def sgi_rle(w, h, z, channels, rows):
        # rows[chan][y] = packets
        tab = b""
        data = b""
        offsets = []
        for chan in range(z):
            for y in range(h):
                offsets.append(512 + 8 * z * h + len(data))
                data += rows[chan][y]
        lens = [len(rows[c][y]) for c in range(z) for y in range(h)]
        tab = b"".join(struct.pack(">I", o) for o in offsets) + b"".join(struct.pack(">I", l) for l in lens)
        return sgi(w, h, z, 1, 1, tab + data)

    add(sgi_rle(2, 2, 1, 1, [[bytes([0x02, 1, 2]), bytes([0x80, 9])]]))
    add(sgi_rle(2, 1, 3, 3, [[bytes([0x02, 10, 20])], [bytes([0x82, 30, 40])], [bytes([0x02, 50, 60])]]))
    add(sgi(2, 1, 1, 2, 0, bytes([0x12, 0x34, 0xab, 0xcd])))
    add(sgi(2, 1, 3, 2, 0, bytes([0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6])))
    add(sgi(2, 1, 1, 1, 2, bytes([1, 2])))
    add(sgi(2, 1, 1, 1, 1, b""))

    # PCX / DCX.
    for mode in ("1", "L", "P", "RGB"):
        for w, h in ((1, 1), (3, 2), (9, 3)):
            im = Image.new(mode, (w, h))
            if mode == "P":
                im.putpalette(palette_bytes(16))
                im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([(x * 37 + y * 53) % (2 if mode == "1" else 256) for y in range(h) for x in range(w)])
            b = io.BytesIO()
            im.save(b, "PCX")
            pcx = b.getvalue()
            add(pcx)
            add(struct.pack("<I", 0x3ADE68B1) + struct.pack("<II", 8 + 4, 0) + pcx)
    for planes in (2, 4):
        hdr = bytearray(128)
        hdr[0] = 10
        hdr[1] = 5
        hdr[3] = 1
        hdr[65] = planes
        struct.pack_into("<HHHH", hdr, 4, 0, 0, 7, 1)
        struct.pack_into("<H", hdr, 66, 1)
        struct.pack_into("<48B", hdr, 16, *([0, 0, 0] * 16))
        body = bytes([0xAA, 0x55, 0x0F, 0xF0, 0x00])
        add(bytes(hdr) + body)
    hdr = bytearray(128)
    hdr[0] = 10
    hdr[1] = 5
    hdr[3] = 8
    hdr[65] = 1
    struct.pack_into("<HHHH", hdr, 4, 0, 0, 3, 0)
    struct.pack_into("<H", hdr, 66, 4)
    add(bytes(hdr) + bytes([0xC4, 9, 1, 2, 3]))

    # TGA: every mode Pillow writes, raw and RLE, plus 16-bit files.
    for mode in ("1", "L", "LA", "P", "RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (9, 3)):
            im = Image.new(mode, (w, h))
            bands = len(mode)
            if mode == "P":
                im.putpalette(palette_bytes(16))
                im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
            elif mode in ("L", "1"):
                im.putdata([(x * 37 + y * 53) % (2 if mode == "1" else 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([tuple((x * 37 + y * 53 + c * 61) % 256 for c in range(bands)) for y in range(h) for x in range(w)])
            for kw in ({}, {"rle": True}, {"orientation": 1}):
                b = io.BytesIO()
                try:
                    im.save(b, "TGA", **kw)
                except Exception:
                    continue
                add(b.getvalue())
    add(b"\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x02\x00\x18\x00" + bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 1, 2, 3]))
    add(b"\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x02\x00\x10\x00" + bytes([0x00, 0x7C, 0xFF, 0x1F, 0x55, 0x7A, 0x0B, 0x30]))
    add(b"\x01\x01\x01\x00\x00\x00\x00\x10\x00\x00\x00\x00\x02\x00\x01\x00\x18\x00Z" + bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 1, 2, 3]))
    add(b"\x00\x01\x01\x00\x00\x02\x00\x00\x10\x00\x00\x00\x02\x00\x01\x00\x18\x00" + bytes(range(48)))
    add(b"\x00\x01\x01\x00\x00\x02\x00\x00\x10\x00\x00\x00\x02\x00\x01\x00\x18\x00" + bytes(range(48)) + bytes([0, 0, 0, 0, 0, 0, 0, 0]))


def be16(v):
    return struct.pack(">H", v)


def be32(v):
    return struct.pack(">I", v)


def packbits(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        # find a run of at least 3 equal bytes
        run = 1
        while i + run < n and data[i + run] == data[i] and run < 128:
            run += 1
        if run >= 3:
            out.append(257 - run)
            out.append(data[i])
            i += run
            continue
        start = i
        while i < n and i - start < 128:
            if i + 2 < n and data[i] == data[i + 1] == data[i + 2]:
                break
            i += 1
        lit = data[start:i]
        if not lit:
            lit = data[start : start + 1]
            i = start + 1
        out.append(len(lit) - 1)
        out += lit
    return bytes(out)


def build_psd(w, h, mode_id, bits, channels, planes, compression=0, palette=None):
    out = b"8BPS" + be16(1) + b"\x00" * 6 + be16(channels) + be32(h) + be32(w) + be16(bits) + be16(mode_id)
    if palette is not None:
        out += be32(768) + palette
    else:
        out += be32(0)
    out += be32(0)  # image resources
    out += be32(0)  # layer and mask info
    if compression == 0:
        out += be16(0) + b"".join(planes)
    else:
        if bits == 8:
            rowbytes = w
        else:
            rowbytes = (w + 7) // 8
        counts = b""
        body = b""
        for plane in planes:
            for y in range(h):
                enc = packbits(plane[y * rowbytes : (y + 1) * rowbytes])
                counts += be16(len(enc))
                body += enc
        out += be16(1) + counts + body
    return out


def blp2(w, h, encoding, alpha, alpha_encoding, palette, pixels):
    header = b"BLP2" + struct.pack("<i", 1) + bytes([encoding, alpha, alpha_encoding, 0])
    header += struct.pack("<II", w, h)
    offset = 20 + 16 * 4 * 2 + len(palette)
    offsets = [offset] + [0] * 15
    lengths = [len(pixels)] + [0] * 15
    return header + struct.pack("<16I", *offsets) + struct.pack("<16I", *lengths) + palette + pixels


def blp1(w, h, alpha, palette, pixels):
    header = b"BLP1" + struct.pack("<i", 1) + struct.pack("<I", 1 if alpha else 0) + struct.pack("<II", w, h)
    header += struct.pack("<i", 5) + struct.pack("<i", 0)
    offset = 28 + 16 * 4 * 2 + len(palette)
    offsets = [offset] + [0] * 15
    lengths = [len(pixels)] + [0] * 15
    return header + struct.pack("<16I", *offsets) + struct.pack("<16I", *lengths) + palette + pixels


def dds_header(w, h, pfflags, fourcc, bitcount, masks, dx10=None):
    flags = 0x1 | 0x2 | 0x4 | 0x1000
    out = b"DDS " + struct.pack("<I", 124) + struct.pack("<I", flags) + struct.pack("<I", h) + struct.pack("<I", w)
    out += struct.pack("<3I", 0, 0, 0)
    out += struct.pack("<11I", *([0] * 11))
    out += struct.pack("<4I", 32, pfflags, int.from_bytes(fourcc, "little"), bitcount)
    out += struct.pack("<4I", *masks)
    out += struct.pack("<5I", 0, 0, 0, 0, 0)
    if dx10 is not None:
        out += struct.pack("<5I", dx10, 3, 0, 1, 0)
    return out


def ico_frame_bmp(w, h, bpp, pixels, palette=b""):
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, bpp, 0, 0, 0, 0, 0, 0)
    pad = 4
    rows = b""
    if bpp == 32:
        rows = b"".join(b"".join(struct.pack("<4B", *px) for px in row) for row in pixels)
    elif bpp == 24:
        rows = b"".join(b"".join(bytes([px[2], px[1], px[0]]) for px in row) + b"\x00" * ((-3 * w) % 4) for row in pixels)
    else:
        rows = pixels
    and_row = ((w + 31) // 32) * 4
    return hdr + palette + rows + b"\x00" * (and_row * h)


def build_ico(entries, cur=False):
    out = b"\x00\x00\x02\x00" if cur else b"\x00\x00\x01\x00"
    out += struct.pack("<H", len(entries))
    offset = 6 + 16 * len(entries)
    body = b""
    for w, h, bpp, data, hotx, hoty in entries:
        out += bytes([w if w < 256 else 0, h if h < 256 else 0, 0, 0])
        out += struct.pack("<HH", hotx if cur else 1, bpp)
        out += struct.pack("<II", len(data), offset)
        offset += len(data)
        body += data
    return out + body


def build_icns(blocks):
    out = b"icns" + b"\x00\x00\x00\x00"
    for sig, data in blocks:
        out += sig + struct.pack(">I", 8 + len(data)) + data
    return out[:4] + struct.pack(">I", len(out)) + out[8:]


def icns_rle_band(width, height, value):
    import math

    size = width * height
    out = bytearray()
    left = size
    while left > 0:
        n = min(130, left)
        out.append(0x80 | 0)  # run marker with blocksize computed below
        out[-1] = (n - 1) + 126  # blocksize = byte_int - 125
        out.append(value)
        left -= n
    return bytes(out)


def base_psd_blp_dds():
    # PSD composites: raw and packbits for each deterministic mode.
    for w, h in ((1, 1), (3, 2), (9, 5)):
        n = w * h
        rgb = [((x * 37) % 256, (y * 53) % 256, (x * y * 11) % 256) for y in range(h) for x in range(w)]
        # RGB, raw and packbits.
        planes = [bytes(p[c] for p in rgb) for c in range(3)]
        add(build_psd(w, h, 3, 8, 3, planes, 0))
        add(build_psd(w, h, 3, 8, 3, planes, 1))
        # RGBA (4 channels).
        planes4 = planes + [bytes((p[0] + p[1] + p[2]) % 256 for p in rgb)]
        add(build_psd(w, h, 3, 8, 4, planes4, 0))
        add(build_psd(w, h, 3, 8, 4, planes4, 1))
        # CMYK with inverted channel data.
        cmyk = [bytes(((x + y + c) * 29) % 256 for y in range(h) for x in range(w)) for c in range(4)]
        add(build_psd(w, h, 4, 8, 4, cmyk, 0))
        add(build_psd(w, h, 4, 8, 4, cmyk, 1))
        # Grayscale.
        lum = [bytes((x * 41 + y * 7) % 256 for y in range(h) for x in range(w))]
        add(build_psd(w, h, 1, 8, 1, lum, 0))
        add(build_psd(w, h, 1, 8, 1, lum, 1))
        # Palette image with a 768-byte palette.
        pal = bytes(((i // 3) * 3) % 256 for i in range(768))
        idx = bytes((x + y) % 16 for y in range(h) for x in range(w))
        add(build_psd(w, h, 2, 8, 1, [idx], 0, palette=pal))
        # Bitmap 1-bit, packed rows.
        rowbytes = (w + 7) // 8
        bits = b""
        for y in range(h):
            row = bytearray(rowbytes)
            for x in range(w):
                if (x + y) % 2:
                    row[x // 8] |= 0x80 >> (x % 8)
            bits += bytes(row)
        add(build_psd(w, h, 0, 1, 1, [bits], 0))
        add(build_psd(w, h, 0, 1, 1, [bits], 1))
    # Extra channels and a corrupt mode.
    add(build_psd(2, 1, 3, 8, 5, [b"\x01\x02", b"\x03\x04", b"\x05\x06", b"\x07\x08"]))
    add(build_psd(2, 1, 3, 8, 2, [b"\x01\x02"]))
    add(build_psd(2, 1, 3, 8, 3, [b"\x01\x02", b"\x03\x04", b"\x05\x06"])[:40])
    add(b"8BPS" + be16(1) + b"\x00" * 6 + be16(3) + be32(2) + be32(2) + be16(12) + be16(3))
    add(build_psd(2, 1, 3, 8, 3, [b"\x01\x02", b"\x03\x04", b"\x05\x06"], 2))

    # BLP: the writer for palette images, hand-built DXT and uncompressed.
    for version in ("BLP1", "BLP2"):
        for w, h in ((1, 1), (3, 2), (9, 5)):
            im = Image.new("P", (w, h))
            im.putpalette(palette_bytes(64))
            im.putdata([(x * 3 + y * 7) % 64 for y in range(h) for x in range(w)])
            b = io.BytesIO()
            im.save(b, "BLP", blp_version=version)
            add(b.getvalue())
    pal = bytes((i * 5) % 256 for i in range(1024))
    idx = bytes([0, 1, 2, 3, 4, 5, 6, 7])
    add(blp2(4, 2, 1, 0, 0, pal, idx))
    add(blp2(4, 2, 1, 1, 0, pal, idx))
    add(blp2(4, 2, 1, 1, 0, bytes(1024), bytes([0] * 8)))
    add(blp1(4, 2, 0, pal, idx))
    add(blp1(4, 2, 1, pal, idx))
    for fmt in ("DXT1", "DXT3", "DXT5"):
        dds = dxt_dds(4, 4, fmt)
        blocks = dds[128:]
        enc = {"DXT1": 0, "DXT3": 1, "DXT5": 7}[fmt]
        alpha = 0 if fmt == "DXT1" else 1
        add(blp2(4, 4, 2, alpha, enc, bytes(1024), blocks))
    add(blp2(4, 4, 2, 0, 2, bytes(1024), b"\x00" * 8))
    add(blp2(4, 4, 3, 0, 0, bytes(1024), idx))
    add(b"BLP2" + struct.pack("<i", 1) + bytes([1, 0, 0, 0]) + struct.pack("<II", 2, 2))

    # DDS: the writer for raw and DXT formats, plus DX10 and palette headers.
    for mode in ("L", "LA", "RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (7, 5)):
            im = Image.new(mode, (w, h))
            bands = len(mode)
            if mode == "L":
                im.putdata([(x * 37 + y * 5) % 256 for y in range(h) for x in range(w)])
            else:
                im.putdata([tuple((x * 37 + y * 5 + c * 61) % 256 for c in range(bands)) for y in range(h) for x in range(w)])
            b = io.BytesIO()
            try:
                im.save(b, "DDS")
            except Exception:
                continue
            add(b.getvalue())
    for fmt in ("DXT1", "DXT3", "DXT5"):
        for w, h in ((4, 4), (7, 5), (1, 1)):
            add(dxt_dds(w, h, fmt))
    # DX10 BC1, BC5 and RGBA8.
    dxt1 = dxt_dds(4, 4, "DXT1")[128:]
    add(dds_header(4, 4, 0x4, b"DX10", 0, (0, 0, 0, 0), dx10=71) + dxt1)
    add(dds_header(4, 4, 0x4, b"DX10", 0, (0, 0, 0, 0), dx10=83) + bytes(range(16)))
    add(dds_header(2, 2, 0x4, b"DX10", 0, (0, 0, 0, 0), dx10=28) + bytes(range(16)))
    # ATI1 / BC5S fourcc.
    add(dds_header(4, 4, 0x4, b"ATI1", 0, (0, 0, 0, 0)) + bytes(range(8)))
    add(dds_header(4, 4, 0x4, b"ATI2", 0, (0, 0, 0, 0)) + bytes(range(16)))
    add(dds_header(4, 4, 0x4, b"BC5S", 0, (0, 0, 0, 0)) + bytes(range(16)))
    # Luminance 8 and 16-bit alpha.
    add(dds_header(2, 2, 0x20000, b"\x00\x00\x00\x00", 8, (0, 0, 0, 0)) + bytes(range(4)))
    add(dds_header(2, 2, 0x20001, b"\x00\x00\x00\x00", 16, (0, 0, 0, 0)) + bytes(range(8)))
    # Palette indexed: the plugin reads data at offset 0.
    add(dds_header(2, 2, 0x20, b"\x00\x00\x00\x00", 8, (0, 0, 0, 0)) + bytes(range(16)))
    # Unimplemented pixel format and header errors.
    add(dds_header(2, 2, 0x4, b"ABCD", 0, (0, 0, 0, 0)))
    add(dds_header(2, 2, 0x0, b"\x00\x00\x00\x00", 0, (0, 0, 0, 0)))
    add(b"DDS " + struct.pack("<I", 120))
    add(b"DDS " + struct.pack("<I", 124))

    # ICO: writer (BMP and PNG frames) and hand-built frames.
    for fmt in ({}, {"bitmap_format": "bmp"}):
        for mode in ("RGB", "RGBA", "P", "L"):
            for w, h in ((16, 16), (32, 32)):
                im = Image.new(mode, (w, h))
                if mode == "P":
                    im.putpalette(palette_bytes(16))
                    im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
                elif mode in ("RGB", "RGBA"):
                    bands = len(mode)
                    im.putdata([tuple((x * 37 + y * 53 + c * 61) % 256 for c in range(bands)) for y in range(h) for x in range(w)])
                else:
                    im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
                b = io.BytesIO()
                try:
                    im.save(b, "ICO", sizes=[(w, h)], **fmt)
                except Exception:
                    continue
                add(b.getvalue())
    rgba = [[(x * 37 % 256, y * 53 % 256, (x * y * 11) % 256, (x * 71) % 256) for x in range(4)] for y in range(4)]
    add(build_ico([(4, 4, 32, ico_frame_bmp(4, 4, 32, rgba), 0, 0)]))
    pal = bmp_palette([(i * 16, i * 8, i * 4) for i in range(16)])
    idx = bytes([(x + y) % 16 for y in range(4) for x in range(4)])
    add(build_ico([(4, 4, 8, ico_frame_bmp(4, 4, 8, pad4(idx * 1), pal), 0, 0)]))
    add(build_ico([(2, 2, 24, ico_frame_bmp(2, 2, 24, [[(10, 20, 30), (40, 50, 60)], [(70, 80, 90), (1, 2, 3)]]), 0, 0)]))
    png1 = build_png(4, 4, 8, 6, sample_pix(4, 4, 8, 6, seed=3))
    add(build_ico([(4, 4, 32, png1, 0, 0)]))
    add(build_ico([(16, 16, 32, png1, 0, 0), (4, 4, 32, ico_frame_bmp(4, 4, 32, rgba), 0, 0)]))
    # CUR: masked 1-bit and 32-bit frames.
    add(build_ico([(4, 4, 32, ico_frame_bmp(4, 4, 32, rgba), 2, 1)], cur=True))
    mask = bytes([0xAA, 0x55]) + bytes([0x0F, 0xF0])
    dib = bmp_header40(8, 8, 1, colors=2) + bmp_palette([(0, 0, 0), (255, 255, 255)]) + bytes([0xAA, 0x00]) + bytes([0xAA, 0x00]) + mask
    add(build_ico([(8, 8, 1, dib, 0, 0)], cur=True))

    # ICNS: writer output, raw 32-bit resources, PNG and JPEG 2000 entries.
    for mode in ("RGB", "RGBA"):
        # A constant source keeps the writer's resized PNG entries tiny.
        im = Image.new(mode, (1, 1), tuple((10 + 30 * c) % 256 for c in range(len(mode))))
        b = io.BytesIO()
        try:
            im.save(b, "ICNS")
        except Exception:
            continue
        add(b.getvalue())
    rgb = bytes((i * 7) % 256 for i in range(128 * 128 * 3))
    alpha = bytes((i * 3) % 256 for i in range(128 * 128))
    add(build_icns([(b"it32", b"\x00\x00\x00\x00" + rgb), (b"t8mk", alpha)]))
    rgb32 = bytes((i * 5) % 256 for i in range(32 * 32 * 3))
    alpha32 = bytes((i * 11) % 256 for i in range(32 * 32))
    add(build_icns([(b"il32", rgb32), (b"l8mk", alpha32)]))
    rgb16 = bytes((i * 13) % 256 for i in range(16 * 16 * 3))
    alpha16 = bytes((i * 17) % 256 for i in range(16 * 16))
    add(build_icns([(b"is32", rgb16), (b"s8mk", alpha16)]))
    png16 = build_png(16, 16, 8, 6, sample_pix(16, 16, 8, 6, seed=9))
    add(build_icns([(b"icp4", png16)]))
    # it32 compressed with the packbits-like RLE.
    band = icns_rle_band(128, 128, 0x40)
    add(build_icns([(b"it32", b"\x00\x00\x00\x00" + band * 3), (b"t8mk", alpha)]))
    jp2 = io.BytesIO()
    im = Image.new("RGB", (16, 16), (10, 20, 30))
    try:
        im.save(jp2, "JPEG2000")
        add(build_icns([(b"icp4", jp2.getvalue())]))
    except Exception:
        pass
    add(b"icns" + struct.pack(">I", 16) + b"TOC " + struct.pack(">I", 16))
    add(b"icns" + struct.pack(">I", 24) + b"abcd" + struct.pack(">I", 0))


def fits_card(key: bytes, value: bytes) -> bytes:
    return (key.ljust(8) + b"= " + value.ljust(20) + b" / comment").ljust(80, b" ")


def fits_file(cards, data, pad=2880):
    out = b"".join(cards).ljust(pad, b" ")
    return out + data


def base_rest_formats():
    # FITS: every BITPIX mode, 1D and 2D, plus a gzip-compressed image.
    for bitpix in (8, 16, 32, -32, -64):
        nbytes = abs(bitpix) // 8
        for w, h in ((1, 1), (3, 2), (5, 3)):
            cards = [
                fits_card(b"SIMPLE", b"T"),
                fits_card(b"BITPIX", str(bitpix).encode()),
                fits_card(b"NAXIS", b"2"),
                fits_card(b"NAXIS1", str(w).encode()),
                fits_card(b"NAXIS2", str(h).encode()),
                fits_card(b"END", b""),
            ]
            if bitpix == 8:
                data = bytes((x * 37 + y * 11) % 256 for y in range(h) for x in range(w))
            elif bitpix == 16:
                data = b"".join(struct.pack(">H", (x * 257 + y * 1234) % 65536) for y in range(h) for x in range(w))
            elif bitpix == 32:
                data = b"".join(struct.pack(">i", (x * 100003 - y * 70001)) for y in range(h) for x in range(w))
            elif bitpix == -32:
                data = b"".join(struct.pack(">f", (x * 1.5 - y * 2.25)) for y in range(h) for x in range(w))
            else:
                data = b"".join(struct.pack(">d", (x * 1.5 - y * 2.25)) for y in range(h) for x in range(w))
            add(fits_file(cards, data))
    # 1D image.
    cards = [fits_card(b"SIMPLE", b"T"), fits_card(b"BITPIX", b"8"),
             fits_card(b"NAXIS", b"1"), fits_card(b"NAXIS1", b"4"), fits_card(b"END", b"")]
    add(fits_file(cards, bytes([1, 2, 3, 4])))
    # Gzip-compressed FITS.
    import gzip as _gzip

    for w, h, bitpix in ((3, 2, 32), (2, 2, -32)):
        rows = b""
        for y in range(h):
            for x in range(w):
                if bitpix == 32:
                    rows += struct.pack(">i", x * 100 + y * 7)
                else:
                    rows += struct.pack(">f", x * 0.5 + y)
        cards = [
            fits_card(b"SIMPLE", b"T"),
            fits_card(b"XTENSION", b"'BINTABLE'"),
            fits_card(b"BITPIX", b"8"),
            fits_card(b"NAXIS", b"2"),
            fits_card(b"NAXIS1", str(4 * w).encode()),
            fits_card(b"NAXIS2", str(h).encode()),
            fits_card(b"ZIMAGE", b"T"),
            fits_card(b"ZBITPIX", str(bitpix).encode()),
            fits_card(b"ZNAXIS", b"2"),
            fits_card(b"ZNAXIS1", str(w).encode()),
            fits_card(b"ZNAXIS2", str(h).encode()),
            fits_card(b"ZCMPTYPE", b"'GZIP_1  '"),
            fits_card(b"END", b""),
        ]
        table = bytes(4 * w * h)
        add(fits_file(cards, table + _gzip.compress(rows, mtime=0)))
    add(b"SIMPLE  = T")
    add(b"SIMPLE  = F")

    # SPIDER: Pillow writer (little-endian) and a big-endian header.
    for w, h in ((1, 1), (3, 2), (5, 3)):
        im = Image.new("F", (w, h))
        im.putdata([float((x * 3 - y * 2) % 11) / 2 for y in range(h) for x in range(w)])
        b = io.BytesIO()
        im.save(b, "SPIDER")
        add(b.getvalue())
    vals = [0.0] * 27
    vals[1] = 2.0
    vals[2] = 2.0
    vals[4] = 1.0
    vals[11] = 2.0
    vals[12] = 1.0
    vals[21] = 1.0
    vals[22] = 1.0
    hdr = struct.pack(">27f", *vals)
    add(hdr.ljust(92, b"\x00") + struct.pack(">4f", 1.0, 2.0, 3.0, 4.0))
    vals2 = list(vals)
    vals2[4] = 3.0
    add(struct.pack(">27f", *vals2).ljust(92, b"\x00") + struct.pack(">4f", 1.0, 2.0, 3.0, 4.0))

    # IM: Pillow writer for its modes and hand-built headers.
    for mode in ("1", "L", "LA", "P", "I", "I;16", "I;16L", "I;16B", "F", "RGB", "RGBA", "CMYK", "YCbCr"):
        for w, h in ((1, 1), (3, 2), (5, 3)):
            if mode == "YCbCr":
                rgbim = Image.new("RGB", (w, h))
                rgbim.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
                im = rgbim.convert("YCbCr")
            else:
                im = Image.new(mode, (w, h))
            bands = len(mode) if mode not in ("I;16", "I;16L", "I;16B") else 1
            if mode == "P":
                im.putpalette(palette_bytes(16))
                im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
            elif mode == "1":
                im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
            elif mode in ("I", "I;16", "I;16L", "I;16B"):
                im.putdata([(x * 257 + y * 1234) % 65536 if mode != "I" else x * 1000 - y * 700 for y in range(h) for x in range(w)])
            elif mode == "F":
                im.putdata([(x * 1.5 - y * 2.25) for y in range(h) for x in range(w)])
            elif mode == "YCbCr":
                pass
            elif mode in ("LA", "RGBA", "CMYK"):
                im.putdata([tuple((x * 37 + y * 53 + c * 61) % 256 for c in range(len(mode))) for y in range(h) for x in range(w)])
            elif mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
            b = io.BytesIO()
            try:
                im.save(b, "IM")
            except Exception:
                continue
            add(b.getvalue())
    add(b"Image type: RGB image\r\nImage size (x*y): 2*1\r\n\0" + b"\0" * (511 - 51) + b"\x1a" + bytes([1, 2, 3, 4, 5, 6]))
    add(b"Image type: Greyscale image\r\nImage size (x*y): 2*1\r\n\0" + b"\0" * (511 - 59) + b"\x1a" + bytes([9, 8]))
    add(b"Image type: Greyscale image\r\nImage size (x*y): 2*1\r\nLut: 1\r\n\0" + b"\0" * 400 + b"\x1a" + bytes(range(256)) * 3 + bytes([0, 1]))

    # FLI/FLC: one frame with a palette chunk and a COPY chunk.
    def fli(w, h, pal=None, pixels=None, color256=True):
        header = bytearray(128)
        frame = bytearray()
        chunks = []
        if pal is not None:
            pdata = struct.pack("<H", 1) + bytes([0, 0]) + pal
            chunks.append(struct.pack("<IH", 6 + len(pdata), 4) + pdata)
        if pixels is not None:
            chunks.append(struct.pack("<IH", 6 + len(pixels), 16) + pixels)
        body = b"".join(chunks)
        frame = struct.pack("<IHH", 16 + len(body), 0xF1FA, len(chunks)) + b"\x00" * 8 + body
        struct.pack_into("<I", header, 0, len(header) + len(frame))
        struct.pack_into("<H", header, 4, 0xAF12)
        struct.pack_into("<H", header, 6, 1)
        struct.pack_into("<H", header, 8, w)
        struct.pack_into("<H", header, 10, h)
        struct.pack_into("<H", header, 12, 8)
        struct.pack_into("<H", header, 14, 0)
        struct.pack_into("<I", header, 16, 100)
        return bytes(header) + frame

    pal = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120] + [0] * (256 * 3 - 12))
    add(fli(2, 2, pal=pal, pixels=bytes([0, 1, 2, 3])))
    add(fli(3, 2, pal=pal[::-1], pixels=bytes(range(6))))
    add(fli(2, 2, pal=None, pixels=bytes([5, 6, 7, 8])))
    add(fli(2, 2, pal=None, pixels=None))

    # WebP lossless (VP8L): Pillow's writer with several methods.
    for mode in ("RGB", "RGBA"):
        for w, h in ((1, 1), (3, 2), (9, 5), (32, 32)):
            im = Image.new(mode, (w, h))
            bands = len(mode)
            if mode == "RGB":
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y * 11) % 256) for y in range(h) for x in range(w)])
            else:
                im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y * 11) % 256, (x * 71 + y * 13) % 256) for y in range(h) for x in range(w)])
            for kw in ({"lossless": True}, {"lossless": True, "method": 0},
                       {"lossless": True, "method": 3}, {"lossless": True, "quality": 50},
                       {"lossless": True, "exact": True}):
                b = io.BytesIO()
                try:
                    im.save(b, "WEBP", **kw)
                except Exception:
                    continue
                add(b.getvalue())
    # A flat image exercises the predictor/color transforms differently.
    im = Image.new("RGBA", (16, 16), (10, 20, 30, 40))
    b = io.BytesIO()
    im.save(b, "WEBP", lossless=True)
    add(b.getvalue())

    # PCD: the fixed 768x512 preview at offset 96*2048.
    def pcd(orientation=0):
        head = bytearray(2048)
        dir_ = bytearray(1539)
        dir_[0:4] = b"PCD_"
        dir_[1538] = orientation
        body = bytes(((i * 13 + 7) % 256) for i in range(589824))
        return bytes(head) + bytes(dir_) + b"\x00" * 8 + body

    add(pcd(0))
    add(pcd(1))
    add(pcd(3))


def tiff_lzw_encode(data: bytes) -> bytes:
    table = {bytes([i]): i for i in range(256)}
    next_code = 258
    width = 9
    out = bytearray()
    bits = 0
    nbits = 0

    def emit(code):
        nonlocal bits, nbits
        bits = (bits << width) | code
        nbits += width
        while nbits >= 8:
            out.append((bits >> (nbits - 8)) & 0xFF)
            nbits -= 8
        bits &= (1 << nbits) - 1

    emit(256)
    w = b""
    for ch in data:
        wk = w + bytes([ch])
        if wk in table:
            w = wk
            continue
        emit(table[w])
        if next_code < 4096:
            table[wk] = next_code
            next_code += 1
            # libtiff's encoder starts the next code one entry later than the
            # TIFF 6.0 "early change" text; its decoder reads that stream.
            if next_code == (1 << width) and width < 12:
                width += 1
        w = bytes([ch])
    if w:
        emit(table[w])
    emit(257)
    if nbits:
        out.append((bits << (8 - nbits)) & 0xFF)
    return bytes(out)


def tiff_predict(data: bytes, w: int, h: int, spp: int, bps: int) -> bytes:
    out = bytearray()
    step = spp * (bps // 8)
    for y in range(h):
        row = data[y * w * step : (y + 1) * w * step]
        row = bytearray(row)
        for x in range(len(row) - 1, step - 1, -1):
            row[x] = (row[x] - row[x - step]) & 0xFF
        out += row
    return bytes(out)


def _tiff_encode(chunks, compression):
    if compression == 1:
        return [bytes(c) for c in chunks]
    if compression == 32773:
        return [packbits(c) for c in chunks]
    if compression == 5:
        return [tiff_lzw_encode(c) for c in chunks]
    if compression in (8, 32946):
        return [zlib.compress(c) for c in chunks]
    return [bytes(c) for c in chunks]


def build_tiff(order, w, h, photo, bps, spp, pixels, compression=1, rows_per_strip=None,
               planar=1, colormap=None, extras=None, sampleformat=None, fillorder=1,
               predictor=None, orientation=None, tile=None, planes=None, strips=None,
               extra_tags=None):
    bo = "<" if order == b"II" else ">"
    compression = {"raw": 1, "packbits": 32773, "tiff_lzw": 5, "lzw": 5,
                   "tiff_adobe_deflate": 8, "deflate": 32946, "jpeg": 7}.get(compression, compression)
    bytes_per_sample = max(1, bps // 8)
    step = spp * bytes_per_sample
    rps = rows_per_strip or h
    if planes is None:
        planes = [pixels]
    chunks = []
    if strips is not None:
        chunks = list(strips)
    elif planar == 1:
        for y0 in range(0, h, rps):
            nrows = min(rps, h - y0)
            chunks.append(pixels[y0 * w * step : (y0 + nrows) * w * step])
    else:
        for plane in planes:
            for y0 in range(0, h, rps):
                nrows = min(rps, h - y0)
                chunks.append(plane[y0 * w * bytes_per_sample : (y0 + nrows) * w * bytes_per_sample])
    if strips is not None:
        encoded = list(strips)
    elif predictor == 2:
        chunks = [tiff_predict(c, w, len(c) // (w * step), spp, bps) for c in chunks]
        encoded = _tiff_encode(chunks, compression)
    else:
        encoded = _tiff_encode(chunks, compression)

    tags = {256: (4, [w]), 257: (4, [h]), 258: (3, [bps] * max(1, spp)),
            259: (3, [compression]), 262: (3, [photo]), 277: (3, [spp]),
            278: (4, [rps]), 284: (3, [planar]), 266: (3, [fillorder])}
    if extra_tags:
        for tag, (typ, values) in extra_tags.items():
            tags[tag] = (typ, values)
    if colormap is not None:
        tags[320] = (3, colormap)
    if extras is not None:
        tags[338] = (3, extras)
    if sampleformat is not None:
        tags[339] = (3, sampleformat)
    if predictor is not None:
        tags[317] = (3, [predictor])
    if orientation is not None:
        tags[274] = (3, [orientation])
    if tile is not None:
        tw, th = tile
        ntx = (w + tw - 1) // tw
        nty = (h + th - 1) // th
        tilechunks = []
        for ty in range(nty):
            for tx in range(ntx):
                rows = []
                for y in range(ty * th, min((ty + 1) * th, h)):
                    rows.append(pixels[y * w * step : (y + 1) * w * step])
                tilechunks.append(b"".join(rows))
        encoded = tilechunks
        tags[322] = (4, [tw])
        tags[323] = (4, [th])
        tags[324] = "offsets"
        tags[325] = (4, [len(c) for c in encoded])
    else:
        tags[273] = "offsets"
        tags[279] = (4, [len(c) for c in encoded])

    def pack_values(t, values):
        if t == 3:
            return b"".join(struct.pack(bo + "H", v & 0xFFFF) for v in values)
        if t == 4:
            return b"".join(struct.pack(bo + "I", v & 0xFFFFFFFF) for v in values)
        if t == 1:
            return bytes(v & 0xFF for v in values)
        raise ValueError("unsupported tag type")

    keys = sorted(tags)
    data_off = 8 + 2 + len(keys) * 12 + 4
    offsets_size = 4 * len(encoded)
    entries = []
    for tag in keys:
        spec = tags[tag]
        if spec == "offsets":
            entries.append([tag, 4, len(encoded), None, None])
            continue
        t, values = spec
        payload = pack_values(t, values)
        entries.append([tag, t, len(values), payload, None])
    # Every payload larger than four bytes (and the offset array) goes into the
    # external area in tag order.
    cursor = data_off
    for e in entries:
        if e[3] is None:
            if offsets_size > 4:
                e[4] = cursor
                cursor += offsets_size
                if cursor % 2:
                    cursor += 1
        elif len(e[3]) > 4:
            e[4] = cursor
            cursor += len(e[3])
            if cursor % 2:
                cursor += 1
    strips_start = cursor
    offsets = []
    pos = strips_start
    for c in encoded:
        offsets.append(pos)
        pos += len(c)
        if pos % 2:
            pos += 1
    raw_offsets = b"".join(struct.pack(bo + "I", o) for o in offsets)
    assembled = bytearray(cursor - data_off)
    for e in entries:
        if e[3] is None:
            e[3] = raw_offsets
        if e[4] is not None:
            assembled[e[4] - data_off : e[4] - data_off + len(e[3])] = e[3]
    out = bytearray(order + struct.pack(bo + "H", 42) + struct.pack(bo + "I", 8))
    out += struct.pack(bo + "H", len(entries))
    for tag, t, n, payload, ext in entries:
        if ext is None:
            out += struct.pack(bo + "HHI", tag, t, n) + payload.ljust(4, b"\x00")
        else:
            out += struct.pack(bo + "HHI", tag, t, n) + struct.pack(bo + "I", ext)
    out += struct.pack(bo + "I", 0)
    out += assembled
    for c in encoded:
        out += c
        if len(out) % 2:
            out += b"\x00"
    return bytes(out)


def base_tiff():
    def pix8(w, h, spp):
        return bytes(((x * 37 + y * 53 + c * 61) % 256 for y in range(h) for x in range(w) for c in range(spp)))

    def pix16(w, h):
        return b"".join(struct.pack("<H" if True else ">H", (x * 257 + y * 1234) % 65536) for y in range(h) for x in range(w))

    for order in (b"II", b"MM"):
        for photo, bps, spp in ((1, 8, 1), (1, 1, 1), (1, 4, 1), (2, 8, 3), (5, 8, 4), (6, 8, 3)):
            for w, h in ((1, 1), (3, 2), (9, 5)):
                step = spp * (bps // 8)
                if bps == 1:
                    rowbytes = (w + 7) // 8
                    raw = bytes((x * 3 + y) % 256 for y in range(h) for x in range(rowbytes))
                elif bps == 4:
                    rowbytes = (w + 1) // 2
                    raw = bytes((x * 5 + y * 7) % 256 for y in range(h) for x in range(rowbytes))
                else:
                    raw = bytes(((x * 37 + y * 53 + c * 61) % 256 for y in range(h) for x in range(w) for c in range(spp)))
                add(build_tiff(order, w, h, photo, bps, spp, raw))
                add(build_tiff(order, w, h, photo, bps, spp, raw, rows_per_strip=1))
                if photo != 6:
                    for comp in ("packbits", "tiff_lzw", "tiff_adobe_deflate"):
                        add(build_tiff(order, w, h, photo, bps, spp, raw, compression=comp))
        # Grayscale 16-bit and RGB 16-bit.
        for w, h in ((3, 2), (5, 3)):
            raw16 = b"".join(struct.pack(("<" if order == b"II" else ">") + "H", (x * 257 + y * 1234) % 65536) for y in range(h) for x in range(w))
            add(build_tiff(order, w, h, 1, 16, 1, raw16))
            rawrgb16 = b"".join(struct.pack(("<" if order == b"II" else ">") + "H", (x * 257 + y * 1234 + c * 5000) % 65536) for y in range(h) for x in range(w) for c in range(3))
            add(build_tiff(order, w, h, 2, 16, 3, rawrgb16))
            rawf = b"".join(struct.pack(("<" if order == b"II" else ">") + "f", x * 1.5 - y * 2.25) for y in range(h) for x in range(w))
            add(build_tiff(order, w, h, 1, 32, 1, rawf, sampleformat=[3]))
            rawi = b"".join(struct.pack(("<" if order == b"II" else ">") + "i", x * 1000 - y * 700) for y in range(h) for x in range(w))
            add(build_tiff(order, w, h, 1, 32, 1, rawi, sampleformat=[2]))
        # miniswhite and fillorder 2.
        raw = bytes(((x * 37 + y * 53) % 256 for y in range(4) for x in range(3)))
        add(build_tiff(order, 3, 4, 0, 8, 1, raw))
        add(build_tiff(order, 3, 4, 1, 8, 1, raw, fillorder=2))
        add(build_tiff(order, 3, 4, 0, 8, 1, raw, fillorder=2))
    # Palette.
    for bits in (1, 2, 4, 8):
        n = 1 << bits
        cmap = []
        for i in range(n):
            cmap.extend([(i * 257) % 65536, (i * 700) % 65536, (i * 1300) % 65536])
        w, h = 4, 2
        rowbytes = (w * bits + 7) // 8
        raw = bytes((x * 3 + y * 5) % 256 for y in range(h) for x in range(rowbytes))
        add(build_tiff(b"II", w, h, 3, bits, 1, raw, colormap=cmap))
    # Alpha variants.
    for extras in ([0], [1], [2]):
        raw = pix8(4, 3, 4)
        add(build_tiff(b"II", 4, 3, 2, 8, 4, raw, extras=extras))
    # Planar configuration 2 (raw).
    w, h = 4, 3
    planes = [pix8(w, h, 4)[c::4] for c in range(3)]
    add(build_tiff(b"II", w, h, 2, 8, 3, b"", planar=2, planes=planes))
    # Tiled, raw.
    add(build_tiff(b"II", 32, 32, 2, 8, 3, pix8(32, 32, 3), tile=(16, 16)))
    add(build_tiff(b"II", 16, 16, 1, 8, 1, pix8(16, 16, 1), tile=(16, 16)))
    # Predictor 2 with LZW and deflate.
    for comp in ("tiff_lzw", "tiff_adobe_deflate"):
        add(build_tiff(b"II", 5, 4, 1, 8, 1, pix8(5, 4, 1), compression=comp, predictor=2))
        add(build_tiff(b"II", 5, 4, 2, 8, 3, pix8(5, 4, 3), compression=comp, predictor=2))
    # Orientation variants.
    for o in range(1, 9):
        add(build_tiff(b"II", 4, 3, 1, 8, 1, pix8(4, 3, 1), orientation=o))
        add(build_tiff(b"II", 4, 3, 2, 8, 3, pix8(4, 3, 3), orientation=o))
    # Pillow's writer for the modes and compressions it supports.
    for mode in ("1", "L", "P", "RGB", "RGBA", "CMYK", "I;16"):
        for comp in (None, "packbits", "tiff_lzw", "tiff_adobe_deflate"):
            for w, h in ((1, 1), (4, 3), (16, 9)):
                im = Image.new(mode, (w, h))
                if mode == "P":
                    im.putpalette(palette_bytes(16))
                    im.putdata([(x * 3 + y * 7) % 16 for y in range(h) for x in range(w)])
                elif mode == "1":
                    im.putdata([(x + y) % 2 for y in range(h) for x in range(w)])
                elif mode == "I;16":
                    im.putdata([(x * 257 + y * 1234) % 65536 for y in range(h) for x in range(w)])
                elif mode in ("RGBA", "CMYK"):
                    im.putdata([tuple((x * 37 + y * 53 + c * 61) % 256 for c in range(len(mode))) for y in range(h) for x in range(w)])
                elif mode == "RGB":
                    im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(h) for x in range(w)])
                else:
                    im.putdata([(x * 37 + y * 53) % 256 for y in range(h) for x in range(w)])
                b = io.BytesIO()
                kw = {} if comp is None else {"compression": comp}
                try:
                    im.save(b, "TIFF", **kw)
                except Exception:
                    continue
                add(b.getvalue())
    # JPEG-in-TIFF.
    im = Image.new("RGB", (4, 3), (10, 20, 30))
    b = io.BytesIO()
    im.save(b, "TIFF", compression="jpeg")
    add(b.getvalue())


def tiff_file_strips(data):
    """The strip payloads of a TIFF Pillow can open."""
    t = Image.open(io.BytesIO(data))
    offs = t.tag_v2.get(273) or ()
    counts = t.tag_v2.get(279) or ()
    return [data[o : o + c] for o, c in zip(offs, counts)]


def bits_to_bytes(bits):
    bits += "0" * ((-len(bits)) % 8)
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def reverse_bits(data):
    return bytes(int(format(b, "08b")[::-1], 2) for b in data)


WHITE_TERM = {0: "00110101", 1: "000111", 2: "0111", 3: "1000", 4: "1011", 5: "1100",
              6: "1110", 7: "1111", 8: "10011", 9: "10100", 10: "00111", 11: "01000",
              12: "001000", 13: "000011", 14: "110100", 15: "110101", 16: "101010",
              17: "101011"}
BLACK_TERM = {0: "0000110111", 1: "010", 2: "11", 3: "10", 4: "011", 5: "0011",
              6: "0010", 7: "00011", 8: "000101", 9: "000100", 10: "0000100",
              11: "0000101", 12: "0000111", 13: "00000100", 14: "00000111",
              15: "000011000", 16: "0000010111", 17: "0000011000"}


def g3_2d(w, h, first_black=0, later_black=None):
    """A Group 3 stream with 2D rows: EOL plus the 1D/2D tag bit per row."""
    eol = "000000000001"
    bits = ""
    for y in range(h):
        bits += eol
        if y == 0 and later_black is None:
            bits += "1"  # 1D line
            bits += WHITE_TERM[first_black] + BLACK_TERM[1] + WHITE_TERM[w - first_black - 1]
        elif later_black is not None and y == 1:
            bits += "0"  # 2D line, horizontal mode
            x = later_black
            bits += "001"  # H
            bits += WHITE_TERM[x] + BLACK_TERM[w - x]
        else:
            bits += "0"  # 2D line
            bits += "1"  # V0 copies the reference line
    return bits_to_bytes(bits)


def ycbcr_packed(w, h, y0, hsub, vsub):
    """Packed 8-bit YCbCr clumps for rows y0..y0+h-1."""
    out = bytearray()
    blocks = (w + hsub - 1) // hsub
    for gy in range((h + vsub - 1) // vsub):
        for bx in range(blocks):
            for j in range(vsub):
                for i in range(hsub):
                    x = min(bx * hsub + i, w - 1)
                    y = min(y0 + gy * vsub + j, y0 + h - 1)
                    out.append(((x * 23 + y * 47) % 256))
            out.append((128 + bx * 7 + gy * 3) % 256)
            out.append((90 + bx * 5 + gy * 11) % 256)
    return bytes(out)


def base_tiff_remaining():
    # CCITT fax: the Pillow writer encodes valid streams for mode 1, and the
    # same bytes are rebuilt with min-is-white, FillOrder 2 and 2-D options.
    for comp_name in ("tiff_ccitt", "group3", "group4", "tiff_raw_16"):
        for w, h in ((1, 1), (3, 2), (8, 2), (9, 3), (17, 5), (32, 4)):
            im = Image.new("1", (w, h))
            im.putdata([(x * 3 + y) % 2 for y in range(h) for x in range(w)])
            b = io.BytesIO()
            try:
                im.save(b, "TIFF", compression=comp_name)
            except Exception:
                continue
            saved = b.getvalue()
            streams = tiff_file_strips(saved)
            comp = Image.open(io.BytesIO(saved)).tag_v2[259]
            for photo in (0, 1):
                for fill in (1, 2):
                    strips = [reverse_bits(s) if fill == 2 else s for s in streams]
                    add(build_tiff(b"II", w, h, photo, 1, 1, b"", compression=comp,
                                   strips=strips, fillorder=fill))
    # Group 3 with T4Options 2-D coding, hand-built.
    for w, h in ((4, 3), (8, 4), (9, 5)):
        for later in (None, 1, 2):
            stream = g3_2d(w, h, 0, later)
            add(build_tiff(b"II", w, h, 1, 1, 1, b"", compression=3, strips=[stream],
                           extra_tags={292: (4, [1])}))
            add(build_tiff(b"II", w, h, 0, 1, 1, b"", compression=3, strips=[reverse_bits(stream)],
                           fillorder=2, extra_tags={292: (4, [1])}))
    # Compressed YCbCr: every subsampling libtiff's put routines accept.
    for hsub, vsub in ((1, 1), (2, 1), (2, 2), (1, 2), (4, 1), (4, 2), (4, 4)):
        for w, h in ((1, 1), (3, 2), (4, 4), (5, 3), (9, 5), (16, 9)):
            if w < hsub or h < vsub:
                continue
            for comp in (32773, 5, 8):
                for rps in (None, 1, 2, 3, h):
                    rows_per_strip = rps or h
                    strips = []
                    for y0 in range(0, h, rows_per_strip):
                        nrows = min(rows_per_strip, h - y0)
                        packed = ycbcr_packed(w, nrows, y0, hsub, vsub)
                        strips.append(_tiff_encode([packed], comp)[0])
                    add(build_tiff(b"II", w, h, 6, 8, 3, b"", compression=comp,
                                   strips=strips, rows_per_strip=rows_per_strip,
                                   extra_tags={530: (3, [hsub, vsub])}))
    # Compressed strips with PlanarConfiguration 2: libtiff's separate-plane
    # unpackers read one band per plane.
    def pix8(w, h, spp):
        return bytes(((x * 37 + y * 53 + c * 61) % 256 for y in range(h) for x in range(w) for c in range(spp)))

    for comp in (32773, 5, 8):
        for w, h in ((1, 1), (4, 3), (9, 5)):
            planes = [pix8(w, h, 4)[c::4] for c in range(3)]
            for rps in (None, 1, 2):
                add(build_tiff(b"II", w, h, 2, 8, 3, b"", compression=comp, planar=2,
                               planes=planes, rows_per_strip=rps))
            planes4 = [pix8(w, h, 4)[c::4] for c in range(4)]
            add(build_tiff(b"II", w, h, 2, 8, 4, b"", compression=comp, planar=2,
                           planes=planes4, extras=[2]))
            add(build_tiff(b"II", w, h, 5, 8, 4, b"", compression=comp, planar=2, planes=planes4))
            planes16 = [b"".join(struct.pack("<H", (x * 257 + y * 1234 + c * 5000) % 65536)
                                 for y in range(h) for x in range(w)) for c in range(3)]
            add(build_tiff(b"II", w, h, 2, 16, 3, b"", compression=comp, planar=2, planes=planes16))
            # A 16x16 tile per plane.
            add(build_tiff(b"II", 16, 16, 2, 8, 3, pix8(16, 16, 3), compression=comp,
                           planar=2, planes=[pix8(16, 16, 3)[c::3] for c in range(3)], tile=(16, 16)))

    # ThunderScan (compression 32809) decodes 4-bit samples.
    def thunder_rows(width, rows, mode, seed=0):
        out = bytearray()
        for y in range(rows):
            n = 0
            while n < width:
                if mode == "raw":
                    out.append(0xC0 | ((n + y + seed) & 0xF))
                    n += 1
                elif mode == "delta2":
                    out.append(0x40 | 0x24 | ((seed + y) & 3))
                    n += 3
                elif mode == "delta3":
                    out.append(0x80 | 0x08 | ((seed + y) & 7))
                    n += 2
                elif mode == "run":
                    left = width - n
                    out.append(min(left, 60))
                    n = width
                else:  # mixed codes
                    out.append(0xC0 | ((n * 3 + y) & 0xF))
                    n += 1
                    if n < width:
                        out.append(0x40 | 0x24)
                        n += 3
                    if n < width:
                        out.append(0x80 | 0x08)
                        n += 2
        return bytes(out)

    for w, h in ((1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (9, 5)):
        for mode in ("raw", "delta2", "delta3", "run", "mixed"):
            strips = [thunder_rows(w, h, mode)]
            add(build_tiff(b"II", w, h, 1, 4, 1, b"", compression=32809, strips=strips))
            add(build_tiff(b"II", w, h, 1, 4, 1, b"", compression=32809,
                           strips=[thunder_rows(w, 1, mode) for _ in range(h)], rows_per_strip=1))
        # Truncated and short strips.
        add(build_tiff(b"II", w, h, 1, 4, 1, b"", compression=32809, strips=[b"\xC1\xC2"]))
        add(build_tiff(b"II", w, h, 1, 4, 1, b"", compression=32809, strips=[bytes(range(64))]))
    # The libtiff codecs Pillow cannot decode through its plugin, and the
    # optional codecs this build lacks (X19).
    for comp in (34676, 34677, 34925, 50000, 50001):
        for photo, bps in ((1, 8), (1, 4), (2, 8), (32844, 16), (32845, 16)):
            add(build_tiff(b"II", 4, 3, photo, bps, 1 if photo in (1, 32844) else 3,
                           bytes(range(48)), compression=comp))
    # LAB pixel data: Pillow has no LAB-to-RGB conversion.
    for order in (b"II", b"MM"):
        for w, h in ((1, 1), (3, 2), (5, 4)):
            raw = bytes(((x * 37 + y * 53 + c * 61) % 256 for y in range(h) for x in range(w) for c in range(3)))
            add(build_tiff(order, w, h, 8, 8, 3, raw))
    for w, h in ((2, 2), (4, 3)):
        planes = [bytes(((x * 37 + y * 53 + c * 61) % 256 for y in range(h) for x in range(w))) for c in range(3)]
        add(build_psd(w, h, 9, 8, 3, planes, 0))
        add(build_psd(w, h, 9, 8, 4, planes + [bytes(w * h)], 0))

    # JPEG-compressed data in other containers: all compared by shape only.
    im = Image.new("RGB", (16, 9))
    im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(9) for x in range(16)])
    for kw in ({}, {"quality": 50}, {"quality": 95}):
        b = io.BytesIO()
        im.save(b, "TIFF", compression="jpeg", **kw)
        add(b.getvalue())
    # New-style JPEG with several strips.
    b = io.BytesIO()
    im.save(b, "TIFF", compression="jpeg", strip_size=64)
    add(b.getvalue())
    # Old-style JPEG (libtiff's sample files).
    for name in ("ojpeg_single_strip_no_rowsperstrip.tiff", "ojpeg_zackthecat_subsamp22_single_strip.tiff"):
        path = os.path.join(ROOT, "tmp", "tiffsamples", name)
        try:
            with open(path, "rb") as fh:
                add(fh.read())
        except FileNotFoundError:
            continue
    # IPTC with JPEG data: grayscale decodes, colour data fails the merge.
    def iptc(jpg, layers, component, band=None, w=4, h=3):
        out = bytearray()

        def field(tag, payload):
            out.extend(bytes([0x1C, tag[0], tag[1]]) + struct.pack(">H", len(payload)) + payload)

        field((3, 60), bytes([layers, component]))
        field((3, 20), struct.pack(">H", w))
        field((3, 30), struct.pack(">H", h))
        if band is not None:
            field((3, 65), struct.pack(">H", band))
        field((3, 120), struct.pack(">H", 5))
        field((8, 10), jpg)
        return bytes(out)

    for mode, layers, component in (("L", 1, 0), ("RGB", 3, 1), ("CMYK", 4, 1)):
        jm = Image.new(mode, (4, 3))
        if mode == "L":
            jm.putdata([(x * 37 + y * 53) % 256 for y in range(3) for x in range(4)])
        elif mode == "RGB":
            jm.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(3) for x in range(4)])
        else:
            jm.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x + y) % 256) for y in range(3) for x in range(4)])
        b = io.BytesIO()
        jm.save(b, "JPEG")
        for band in (None, 0, 1):
            add(iptc(b.getvalue(), layers, component, band))
    # BLP1 with a JPEG stream as the first mip level.
    def blp1_jpeg(stream, alpha):
        offsets = [4 + len(stream)]
        out = bytearray(b"BLP1")
        out += struct.pack("<iI", 0, 1 if alpha else 0)
        out += struct.pack("<II", 4, 3)
        out += struct.pack("<ii", 0, 1)
        out += struct.pack("<16I", *offsets, *([0] * 15))
        out += struct.pack("<16I", 0, *([0] * 15))
        out += struct.pack("<I", len(stream)) + stream
        return bytes(out)

    for mode in ("L", "RGB", "CMYK"):
        jm = Image.new(mode, (4, 3))
        if mode == "L":
            jm.putdata([(x * 37 + y * 53) % 256 for y in range(3) for x in range(4)])
        elif mode == "RGB":
            jm.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(3) for x in range(4)])
        else:
            jm.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256, (x + y) % 256) for y in range(3) for x in range(4)])
        b = io.BytesIO()
        jm.save(b, "JPEG")
        for alpha in (0, 1):
            add(blp1_jpeg(b.getvalue(), alpha))

    # DDS BC6H (unsigned and signed) and BC7: Pillow's bcn decoder.
    def blocks(seed, n):
        rnd = random.Random(seed)
        return bytes(rnd.randrange(256) for _ in range(n))

    for dxgi in (95, 96, 98, 99):
        for w, h in ((4, 4), (8, 4), (7, 5), (1, 1), (3, 2)):
            n = ((w + 3) // 4) * ((h + 3) // 4) * 16
            add(dds_header(w, h, 0x4, b"DX10", 0, (0, 0, 0, 0), dx10=dxgi) + blocks(dxgi * 31 + w * 7 + h, n))
    # A few edge encodings: zero block, all-ones block, mode 0 BC7.
    for dxgi in (95, 96, 98):
        for pattern in (bytes(16), b"\xff" * 16, bytes([0x80]) + bytes(15), bytes([0x40]) + bytes(15)):
            add(dds_header(4, 4, 0x4, b"DX10", 0, (0, 0, 0, 0), dx10=dxgi) + pattern)


def base_undecoded_valid():
    # Formats this package identifies but does not decode: Pillow-written files
    # where the writer is available, and minimal hand-built headers that its
    # _open accepts.
    for fmt in ("TIFF", "WEBP", "JPEG2000", "AVIF"):
        im = Image.new("RGB", (3, 2))
        im.putdata([((x * 37) % 256, (y * 53) % 256, (x * y) % 256) for y in range(2) for x in range(3)])
        for kw in ({}, {"quality": 50}):
            b = io.BytesIO()
            try:
                im.save(b, fmt, **kw)
            except Exception:
                continue
            add(b.getvalue())
    # BLP1.
    add(b"BLP1" + struct.pack("<III", 0, 0, 2) + struct.pack("<II", 2, 2) + struct.pack("<i", 1) + b"\x00" * 4 + b"\x00" * 16)
    # CUR: one entry pointing at a 24-bit bitmap header.
    bmp = bmp_header40(2, 4, 24) + pad4(b"\x0a\x14\x1e" * 2) * 4
    entry = bytes([2, 2, 0, 0]) + struct.pack("<HHII", 0, 0, len(bmp), 22)
    add(b"\x00\x00\x02\x00" + struct.pack("<H", 1) + entry + bmp)
    # PCX: 8-bit, one plane, version 5.
    hdr = bytearray(128)
    hdr[0] = 10
    hdr[1] = 5
    hdr[3] = 8
    hdr[65] = 1
    struct.pack_into("<HHHH", hdr, 4, 0, 0, 1, 1)
    struct.pack_into("<H", hdr, 66, 2)
    add(bytes(hdr) + b"\x00\x00")
    # DCX: a directory with one PCX.
    pcx = bytes(hdr) + b"\x00\x00"
    add(struct.pack("<I", 0x3ADE68B1) + struct.pack("<II", 8, 0) + pcx)
    # DDS: uncompressed 24-bit.
    header = bytearray(120)
    struct.pack_into("<3I", header, 0, 0x1007, 2, 2)
    struct.pack_into("<4I", header, 68, 32, 0x40, 0, 24)
    struct.pack_into("<3I", header, 84, 0xFF0000, 0xFF00, 0xFF)
    add(b"DDS " + struct.pack("<I", 124) + bytes(header) + (b"\x0a\x14\x1e" * 2) * 2)
    # FITS: one axis, 2x2 8-bit.
    def card(key, value):
        return (key.ljust(8) + b"= " + value.ljust(20) + b" / comment").ljust(80, b" ")
    fits = b"".join([
        card(b"SIMPLE", b"T"),
        card(b"BITPIX", b"8"),
        card(b"NAXIS", b"2"),
        card(b"NAXIS1", b"2"),
        card(b"NAXIS2", b"2"),
        card(b"END", b""),
    ])
    fits = fits.ljust(2880, b" ")
    add(fits + bytes(range(4)))
    # FTEX: uncompressed RGB.
    ft = b"FTEX" + struct.pack("<i2i2i", 0, 2, 2, 1, 1) + struct.pack("<2i", 1, 24) + struct.pack("<i", 12) + b"\x0a\x14\x1e" * 4
    add(ft)
    # GBR: version 1, 2x2, RGBA.
    add(struct.pack(">5I", 20, 1, 2, 2, 4) + b"\x00" * 16)
    # ICNS with a PNG icon resource.
    png1 = build_png(1, 1, 8, 6, [[(10, 20, 30, 255)]])
    block = b"ic07" + struct.pack(">I", 8 + len(png1)) + png1
    add(b"icns" + struct.pack(">I", 8 + len(block)) + block)
    # ICO with a PNG image.
    entry = bytes([1, 1, 0, 0]) + struct.pack("<HHII", 1, 32, len(png1), 22)
    add(b"\x00\x00\x01\x00" + struct.pack("<H", 1) + entry + png1)
    # IMT: the minimal text header.
    add(b"width 1\nheight 1\npixel n8\n\x0c" + b"\x00")
    # MCIDAS: one 256-byte directory.
    m = bytearray(256)
    m[0:8] = b"\x00\x00\x00\x00\x00\x00\x00\x04"
    struct.pack_into("!i", m, 32, 2)   # w[9] height
    struct.pack_into("!i", m, 36, 2)   # w[10] width
    struct.pack_into("!i", m, 40, 1)   # w[11] bytes per pixel
    add(bytes(m) + b"\x00" * 8)
    # MPEG: 2x2 video sequence header.
    bits = 0
    nbits = 0
    out = bytearray()

    def put(v, n):
        nonlocal bits, nbits
        bits = (bits << n) | v
        nbits += n
        while nbits >= 8:
            out.append((bits >> (nbits - 8)) & 0xFF)
            nbits -= 8

    put(0x1B3, 32)
    put(2, 12)
    put(2, 12)
    put(0, 4)
    if nbits:
        out.append((bits << (8 - nbits)) & 0xFF)
    add(bytes(out))
    # MSP: a DanM header with a valid checksum.
    words = [0] * 16
    words[0] = 0x6144
    words[1] = 0x4D6E
    words[2] = 2
    words[3] = 2
    checksum = 0
    for v in words:
        checksum ^= v
    words[15] = checksum
    add(b"".join(struct.pack("<H", v) for v in words) + b"\x00" * 8)
    # PCD: photo CD directory at 2048.
    add(b"\x00" * 2048 + b"PCD_" + b"\x00" * 1600)
    # PIXAR: 1024-byte header.
    pix = bytearray(1024)
    struct.pack_into("<H", pix, 416, 2)
    struct.pack_into("<H", pix, 418, 2)
    struct.pack_into("<H", pix, 424, 14)
    struct.pack_into("<H", pix, 426, 2)
    add(bytes(pix) + b"\x00" * 32)
    # PSD: 3x2 RGB.
    psd = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 3, 2, 3, 8, 3)
    psd += struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">H", 0)
    add(psd + b"\x0a\x14\x1e" * 6)
    # QOI header.
    add(b"qoif" + struct.pack(">II", 2, 2) + bytes([3, 0]) + b"\x00" * 8)
    # SGI: 2x2 RGB.
    sgi = bytearray(512)
    struct.pack_into(">H", sgi, 0, 474)
    sgi[2] = 0
    sgi[3] = 1
    struct.pack_into(">HHHH", sgi, 4, 3, 2, 2, 3)
    add(bytes(sgi) + b"\x00" * 16)
    # SPIDER: a regular 2D image.
    vals = [0.0] * 27
    vals[1] = 2.0   # NSAM
    vals[2] = 2.0   # NROW
    vals[4] = 1.0   # IFORM
    vals[11] = 2.0  # NSLICE / width
    vals[12] = 1.0  # LABREC
    vals[21] = 1.0  # LABBYT
    vals[22] = 1.0  # LENBYT
    add(struct.pack(">27f", *vals) + b"\x00" * 16)
    # SUN: 2x2 8-bit.
    add(struct.pack(">8I", 0x59A66A95, 2, 2, 8, 4, 0, 0, 0) + b"\x00" * 4)
    # WMF: a placeable header and a standard header.
    wmf = b"\xd7\xcd\xc6\x9a" + struct.pack("<H", 0) + struct.pack("<4h", 0, 0, 1440, 1440)
    wmf += struct.pack("<H", 1440) + b"\x00" * 4 + struct.pack("<H", 0)
    wmf += b"\x01\x00\x09\x00" + b"\x00" * 12
    add(wmf)
    # XBM.
    add(b"#define x_width 2\n#define x_height 2\nstatic unsigned char x_bits[] = {\n0x01,0x02};\n")
    # XPM.
    add(b"/* XPM */\nstatic char *x[] = {\n\"2 2 2 1\",\n\"0 c #000000\",\n\"1 c #FFFFFF\",\n\"01\",\n\"10\"};\n")
    # XV thumbnail.
    add(b"P7 332\n# comment\n2 2 255\n" + b"\x00" * 8)


def base_bombs():
    # 1x1 images whose header declares a decompression bomb.
    for w, h in ((20000, 20000), (50000, 50000)):
        add(build_png(1, 1, 8, 0, [[0]], raw=b"\x00\x00", pre_chunks=()) .replace(
            struct.pack(">II", 1, 1), struct.pack(">II", w, h), 1))
        # A GIF logical screen of that size with no image data.
        add(b"GIF89a" + struct.pack("<HH", min(w, 65535), min(h, 65535)) + b"\x00\x00\x00;")
        # A BMP with a huge declared size.
        add(bmp_file(bmp_header40(w, h, 24), b"", b"\x00" * 3))
        # A PPM with a huge declared size.
        add(b"P6\n%d %d\n255\n\x00\x00\x00" % (w, h))
    add(b"")


def random_cases():
    rnd = random.Random(20261117)
    for n in (1, 2, 3, 4, 5, 8, 13, 16, 17, 32, 64, 100, 255, 1000):
        for _ in range(4):
            add(bytes(rnd.randrange(256) for _ in range(n)))
    for _ in range(40):
        add(bytes(rnd.randrange(256) for _ in range(rnd.randrange(20, 400))))
    for b in (b"\x00", b"\x00" * 5, b"\xff" * 16, b"\n", b"\r\n"):
        add(b)


# ---------------------------------------------------------------------------


def build_corpus() -> list[dict]:
    base_png()
    base_png_hand()
    base_gif()
    base_bmp()
    base_ppm()
    base_jpeg()
    base_more_formats()
    base_psd_blp_dds()
    base_rest_formats()
    base_tiff()
    base_tiff_remaining()
    base_other_signatures()
    base_undecoded_valid()
    base_more_cases()
    base_bombs()
    random_cases()
    # Mutations of representative files.
    rnd = random.Random(4242)
    base_count = len(INPUTS)

    for i in range(0, base_count, max(1, base_count // 90)):
        # Mutated directories exercise Pillow's and libtiff's own recovery
        # behaviour, which the port follows for the tags and strip data it can
        # read.
        add_mutations(INPUTS[i])
    for _ in range(30):
        data = INPUTS[rnd.randrange(base_count)]
        if len(data) > 4:
            cut = rnd.randrange(1, len(data))
            add(data[:cut])
    return [case(data) for data in INPUTS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify the checked-in corpus instead of writing it")
    ap.add_argument("--output", default=OUT)
    args = ap.parse_args()
    cases = build_corpus()
    doc = {"pillow": "13.0.0", "cases": cases}
    raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
    blob = gzip.compress(raw, mtime=0)
    if args.check:
        try:
            with open(args.output, "rb") as fh:
                have = fh.read()
        except FileNotFoundError:
            print("missing " + args.output, file=sys.stderr)
            return 1
        if have != blob:
            print("corpus differs from " + args.output, file=sys.stderr)
            return 1
        print("%d cases, %d bytes: up to date" % (len(cases), len(blob)))
        return 0
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "wb") as fh:
        fh.write(blob)
    print("%d cases, %d bytes -> %s" % (len(cases), len(blob), args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
