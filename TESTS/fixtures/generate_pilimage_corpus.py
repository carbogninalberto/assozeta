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


def rgb_answer(im) -> dict:
    """reportlab ImageReader.getRGBData(), reproduced without reportlab."""
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
        ans = {
            "mode": out_mode,
            "data": sha(data),
            "alpha": sha(alpha_image.tobytes()) if alpha_image is not None else None,
        }
        return ans
    except BaseException as exc:  # noqa: BLE001 - the corpus records exceptions
        return {"error": error_of(exc)}


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


DECODED = {"PNG", "GIF", "BMP", "DIB", "PPM", "JPEG"}


def case(data: bytes) -> dict:
    entry = {"data": base64.b64encode(data).decode()}
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
    if im.format in DECODED:
        # Only these formats are decoded. For JPEG the writer embeds the JPEG
        # bytes and RGBData reports ErrFormatNotPorted, so the oracle answer is
        # not used either; recording it would make the generator invoke
        # external decoders.
        if im.format != "JPEG":
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
    base_other_signatures()
    base_undecoded_valid()
    base_more_cases()
    base_bombs()
    random_cases()
    # Mutations of representative files.
    rnd = random.Random(4242)
    base_count = len(INPUTS)
    for i in range(0, base_count, max(1, base_count // 90)):
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
