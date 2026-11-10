"""Bake the font data and golden PNGs for BE_GO/internal/pilplaceholder.

Run it twice with Pillow 13.0.0 from the repository root, because the PNG
bytes depend on which zlib Pillow's extension module loads:

* with the migration interpreter on macOS, whose wheel bundles zlib-ng 2.3.3
  (``PIL.features.version("zlib")`` ends in ``.zlib-ng``):
  ``python -I TESTS/adapters/go/pil_placeholder_golden.py``
* inside the production image, whose manylinux wheel was compiled against
  zlib-ng but links the system ``libz.so.1`` (classic zlib 1.2.13 on Debian
  bookworm), for example::

      docker run --rm -v "$PWD":/repo -w /repo python:3.13.16-slim-bookworm sh -c \
        'pip install -q Pillow==13.0.0 && python -I TESTS/adapters/go/pil_placeholder_golden.py'

Each run writes the golden files of its own zlib (``zlibng`` or ``zlib`` in
the file name) and the font data, which must come out identical in both.
``--be-root DIR`` reads the Django sources from another checkout's ``BE``.

Nothing is imported from Django. ``generate_image_with_text`` is compiled from
its own source in ``BE/application/utils/api_utils.py`` and the two texts it is
called with are rebuilt from the f-strings at their call sites
(``BE/application/utils/subscriptions_utils.py`` and
``BE/application/views/subscriptions_views.py``), so the corpus follows those
files. Every expected PNG is ``image.save(BytesIO(), format='PNG')`` of the
function's result, as the callers do.

Outputs:

``BE_GO/internal/pilplaceholder/fontdata.bin.gz``
    What the FreeType build bundled with Pillow returns for Pillow's embedded
    default font at its default size: per glyph the hinted advance, control
    box and rendered bitmap, and the character map. Read through ctypes from
    the same shared library Pillow loads, then checked against Pillow's own
    rendering of every mapped character. The layout is documented in font.go.

``BE_GO/internal/pilplaceholder/testdata/render_golden_<zlib>.json.gz``
    Texts with the SHA-256 and length of the expected PNG (the PNG itself for
    a subset), or the exception class Pillow raises.

``BE_GO/internal/pilplaceholder/testdata/png_golden_<zlib>.json.gz``
    RGB images saved by Pillow, for the PNG encoder alone: small ones in full,
    large ones as the parameters of a pixel formula mirrored in png_test.go.

``go test ./internal/pilplaceholder/...`` requires identical bytes for all of
them.
"""
import argparse
import ast
import base64
import ctypes
import datetime
import glob
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import random
import struct
import warnings
import zoneinfo

import PIL
from PIL import Image, ImageDraw, ImageFont, features

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "BE_GO" / "internal" / "pilplaceholder"
MASK64 = (1 << 64) - 1


# --- the Django function and its call sites ---------------------------------


def function_source(path, name):
    source = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise SystemExit(f"{name} not found in {path}")


def call_site_template(path):
    """The f-string passed to generate_image_with_text, as literal parts and
    the source of each placeholder."""
    source = path.read_text(encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "generate_image_with_text"
        ):
            (argument,) = node.args
            if not isinstance(argument, ast.JoinedStr):
                raise SystemExit(f"{path}: call site is no longer an f-string")
            parts = []
            for value in argument.values:
                if isinstance(value, ast.Constant):
                    parts.append(value.value)
                else:
                    if value.conversion != -1 or value.format_spec is not None:
                        raise SystemExit(f"{path}: placeholder has a format")
                    parts.append(ast.unparse(value.value))
            found.append(parts)
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one call site, found {len(found)}")
    parts = found[0]
    holes = [i for i, part in enumerate(parts) if i % 2 == 1]
    names = [parts[i] for i in holes]
    if (
        len(names) != 3
        or "first_name" not in names[0]
        or "last_name" not in names[1]
        or not names[2].startswith("make_aware(")
    ):
        raise SystemExit(f"{path}: unexpected placeholders {names}")
    return parts


def fill(parts, first, last, when):
    values = iter((first, last, str(when)))
    return "".join(part if i % 2 == 0 else next(values) for i, part in enumerate(parts))


# --- font data ---------------------------------------------------------------

c_long, c_ptr, c_int, c_uint = ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
c_short, c_ushort = ctypes.c_short, ctypes.c_ushort


class Vector(ctypes.Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


class BBox(ctypes.Structure):
    _fields_ = [("xMin", c_long), ("yMin", c_long), ("xMax", c_long), ("yMax", c_long)]


class Generic(ctypes.Structure):
    _fields_ = [("data", c_ptr), ("finalizer", c_ptr)]


class Bitmap(ctypes.Structure):
    _fields_ = [
        ("rows", c_uint),
        ("width", c_uint),
        ("pitch", c_int),
        ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
        ("num_grays", c_ushort),
        ("pixel_mode", ctypes.c_ubyte),
        ("palette_mode", ctypes.c_ubyte),
        ("palette", c_ptr),
    ]


class GlyphMetrics(ctypes.Structure):
    _fields_ = [
        (name, c_long)
        for name in (
            "width height horiBearingX horiBearingY horiAdvance "
            "vertBearingX vertBearingY vertAdvance"
        ).split()
    ]


class GlyphSlot(ctypes.Structure):
    _fields_ = [
        ("library", c_ptr),
        ("face", c_ptr),
        ("next", c_ptr),
        ("glyph_index", c_uint),
        ("generic", Generic),
        ("metrics", GlyphMetrics),
        ("linearHoriAdvance", c_long),
        ("linearVertAdvance", c_long),
        ("advance", Vector),
        ("format", c_int),
        ("bitmap", Bitmap),
        ("bitmap_left", c_int),
        ("bitmap_top", c_int),
    ]


class SizeMetrics(ctypes.Structure):
    _fields_ = [
        ("x_ppem", c_ushort),
        ("y_ppem", c_ushort),
        ("x_scale", c_long),
        ("y_scale", c_long),
        ("ascender", c_long),
        ("descender", c_long),
        ("height", c_long),
        ("max_advance", c_long),
    ]


class Size(ctypes.Structure):
    _fields_ = [("face", c_ptr), ("generic", Generic), ("metrics", SizeMetrics)]


class Face(ctypes.Structure):
    _fields_ = [
        ("num_faces", c_long),
        ("face_index", c_long),
        ("face_flags", c_long),
        ("style_flags", c_long),
        ("num_glyphs", c_long),
        ("family_name", ctypes.c_char_p),
        ("style_name", ctypes.c_char_p),
        ("num_fixed_sizes", c_int),
        ("available_sizes", c_ptr),
        ("num_charmaps", c_int),
        ("charmaps", c_ptr),
        ("generic", Generic),
        ("bbox", BBox),
        ("units_per_EM", c_ushort),
        ("ascender", c_short),
        ("descender", c_short),
        ("height", c_short),
        ("max_advance_width", c_short),
        ("max_advance_height", c_short),
        ("underline_position", c_short),
        ("underline_thickness", c_short),
        ("glyph", ctypes.POINTER(GlyphSlot)),
        ("size", ctypes.POINTER(Size)),
        ("charmap", c_ptr),
    ]


class SizeRequest(ctypes.Structure):
    _fields_ = [
        ("type", c_int),
        ("width", c_long),
        ("height", c_long),
        ("horiResolution", c_uint),
        ("vertResolution", c_uint),
    ]


FT_LOAD_DEFAULT, FT_LOAD_RENDER = 0, 4
FT_FACE_FLAG_KERNING = 1 << 6
FT_GLYPH_BBOX_PIXELS = 3
FT_PIXEL_MODE_GRAY = 2


def default_font_bytes():
    """The font ImageFont.load_default() decodes, read from its source."""
    source = Path(ImageFont.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "load_default":
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "b64decode":
                    return base64.b64decode(call.args[0].value)
    raise SystemExit("embedded default font not found in ImageFont.py")


def bundled_freetype():
    base = Path(PIL.__file__).parent
    found = glob.glob(str(base / ".dylibs" / "libfreetype*")) + glob.glob(
        str(base.parent / "pillow.libs" / "libfreetype*")
    )
    if len(found) != 1:
        raise SystemExit(f"expected one bundled FreeType library, found {found}")
    return ctypes.CDLL(found[0])


def check(error, what):
    if error:
        raise SystemExit(f"{what}: FreeType error {error}")


def bake_font(size):
    """Mirror getfont() and the FT_Load_Glyph calls of _imagingft.c."""
    data = default_font_bytes()
    ft = bundled_freetype()
    face_p = ctypes.POINTER(Face)
    ft.FT_New_Memory_Face.argtypes = [c_ptr, ctypes.c_char_p, c_long, c_long, ctypes.POINTER(face_p)]
    ft.FT_Request_Size.argtypes = [face_p, ctypes.POINTER(SizeRequest)]
    ft.FT_Get_Char_Index.argtypes = [face_p, ctypes.c_ulong]
    ft.FT_Get_Char_Index.restype = c_uint
    ft.FT_Load_Glyph.argtypes = [face_p, c_uint, ctypes.c_int32]
    ft.FT_Get_Glyph.argtypes = [ctypes.POINTER(GlyphSlot), ctypes.POINTER(c_ptr)]
    ft.FT_Glyph_Get_CBox.argtypes = [c_ptr, c_uint, ctypes.POINTER(BBox)]
    ft.FT_Glyph_Get_CBox.restype = None
    ft.FT_Done_Glyph.argtypes = [c_ptr]
    ft.FT_Done_Glyph.restype = None

    library = c_ptr()
    check(ft.FT_Init_FreeType(ctypes.byref(library)), "FT_Init_FreeType")
    major, minor, patch = c_int(), c_int(), c_int()
    ft.FT_Library_Version(library, ctypes.byref(major), ctypes.byref(minor), ctypes.byref(patch))
    version = f"{major.value}.{minor.value}.{patch.value}"
    buffer = ctypes.create_string_buffer(data, len(data))
    face = face_p()
    check(ft.FT_New_Memory_Face(library, buffer, len(data), 0, ctypes.byref(face)), "FT_New_Memory_Face")
    request = SizeRequest(0, int(size * 64), int(size * 64), 0, 0)  # FT_SIZE_REQUEST_TYPE_NOMINAL
    check(ft.FT_Request_Size(face, ctypes.byref(request)), "FT_Request_Size")
    record = face.contents
    if record.face_flags & FT_FACE_FLAG_KERNING:
        raise SystemExit("the font now has kerning FreeType applies; the Go layout has none")

    glyphs = []
    for index in range(record.num_glyphs):
        check(ft.FT_Load_Glyph(face, index, FT_LOAD_DEFAULT), "FT_Load_Glyph")
        advance = record.glyph.contents.metrics.horiAdvance
        handle = c_ptr()
        check(ft.FT_Get_Glyph(record.glyph, ctypes.byref(handle)), "FT_Get_Glyph")
        box = BBox()
        ft.FT_Glyph_Get_CBox(handle, FT_GLYPH_BBOX_PIXELS, ctypes.byref(box))
        ft.FT_Done_Glyph(handle)
        check(ft.FT_Load_Glyph(face, index, FT_LOAD_DEFAULT | FT_LOAD_RENDER), "FT_Load_Glyph")
        slot = record.glyph.contents
        bitmap = slot.bitmap
        if slot.metrics.horiAdvance != advance or advance < 0:
            raise SystemExit(f"glyph {index}: unexpected advance")
        rows = []
        if bitmap.buffer and bitmap.rows and bitmap.width:
            if bitmap.pixel_mode != FT_PIXEL_MODE_GRAY or bitmap.pitch < bitmap.width:
                raise SystemExit(f"glyph {index}: unexpected bitmap format")
            for y in range(bitmap.rows):
                start = y * bitmap.pitch
                rows.append(bytes(bitmap.buffer[start : start + bitmap.width]))
        glyphs.append(
            {
                "advance": advance,
                "left": slot.bitmap_left,
                "top": slot.bitmap_top,
                "width": bitmap.width if rows else 0,
                "rows": len(rows),
                "cbox": (box.xMin, box.xMax, box.yMin, box.yMax),
                "bitmap": b"".join(rows),
            }
        )
    cmap = {}
    for code_point in range(0x110000):
        index = ft.FT_Get_Char_Index(face, code_point)
        if index:
            cmap[code_point] = index
    ascender = record.size.contents.metrics.ascender
    return version, ascender, glyphs, cmap


def expected_mask(glyph, ascender):
    """Size, offset and bytes of font.getmask2(one character), by the rules of
    bounding_box_and_anchors and font_render."""
    pixel = lambda v: (v + 32) >> 6
    x_min, x_max, y_min, y_max = glyph["cbox"]
    left = min(0, x_min)
    right = max(0, pixel(glyph["advance"]), x_max)
    top, bottom = max(0, y_max), min(0, y_min)
    width, height = right - left, top - bottom
    offset = (left, pixel(ascender) - top)
    if width == 0 or height == 0:
        return (width, height), offset, bytes(width * height)
    mask = bytearray(width * height)
    origin_x = -min(0, glyph["left"])
    origin_y = max(0, glyph["top"])
    for y in range(glyph["rows"]):
        for x in range(glyph["width"]):
            tx = origin_x + glyph["left"] + x
            ty = origin_y - glyph["top"] + y
            if 0 <= tx < width and 0 <= ty < height:
                mask[ty * width + tx] = glyph["bitmap"][y * glyph["width"] + x]
    return (width, height), offset, bytes(mask)


def write_font(font):
    version, ascender, glyphs, cmap = bake_font(font.size)
    used = {0} | set(cmap.values())
    unmapped = next(cp for cp in range(0x100, 0x110000) if cp not in cmap)
    for code_point in [unmapped] + sorted(cmap):
        glyph = glyphs[cmap.get(code_point, 0)]
        mask, offset = font.getmask2(chr(code_point))
        got = (mask.size, tuple(offset), bytes(mask))
        if got != expected_mask(glyph, ascender):
            raise SystemExit(f"U+{code_point:04X}: baked glyph differs from Pillow's rendering")
        if font.getlength(chr(code_point)) * 64 != glyph["advance"]:
            raise SystemExit(f"U+{code_point:04X}: baked advance differs from Pillow's")
    blob = bytearray(b"PILFNT1\x00")
    blob += struct.pack("<iH", ascender, len(glyphs))
    for glyph in glyphs:
        blob += struct.pack(
            "<ihhHH4h",
            glyph["advance"],
            glyph["left"],
            glyph["top"],
            glyph["width"],
            glyph["rows"],
            *glyph["cbox"],
        )
        blob += glyph["bitmap"]
    blob += struct.pack("<I", len(cmap))
    for code_point in sorted(cmap):
        blob += struct.pack("<IH", code_point, cmap[code_point])
    target = PACKAGE / "fontdata.bin.gz"
    if target.exists() and gzip.decompress(target.read_bytes()) != bytes(blob):
        raise SystemExit(f"{target} differs from this FreeType's data; remove it to accept the change")
    write_gzip(target, bytes(blob))
    print(f"font: FreeType {version}, {len(glyphs)} glyphs ({len(used)} reachable), {len(cmap)} code points")
    return cmap


def write_gzip(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=9) as zipped:
            zipped.write(data)


def write_json(path, document):
    text = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
    write_gzip(path, text.encode("ascii"))


# --- render corpus -----------------------------------------------------------

FIRST_NAMES = [
    "Mario", "Anna", "Giulia", "Luca", "Maria Grazia", "Gian-Luca", "Niccolò", "Zoë", "José",
    "François", "Søren", "Łukasz", "Müller", "D'Angelo", "O’Brien", "Jean-Pierre", "",
    " ", "  Spazi  ", "Иван", "Αλέξανδρος", "山田", "太郎", "محمد", "שלום", "😀", "👩‍👩‍👧",
    "é", "äô", "Tab\tName", "Bell\x07", "Null\x00", "CR\rLF", "Line\nBreak",
    "\x7f", " ", "​", "﻿", "ﬁﬂ", "“Quoted”", "A…Z", "™", "j", "jj", "fj",
    "MARIO", "mario", "W" * 30, "i" * 40, "Wolfeschlegelsteinhausenbergerdorff " * 4,
    "Nome Molto Lungo " * 12, "x" * 590, "é" * 130, "0123456789", "<b>&amp;</b>", "{}%s\\n",
]
LAST_NAMES = [
    "Rossi", "Bianchi", "De Luca", "Dell'Acqua", "D’Amico", "Müller-Lüdenscheidt", "Ångström",
    "Çelik", "Nguyễn", "Ó Briain", "van der Berg", "Петров", "Παπαδόπουλος", "王", "", "\t",
    "Esposito Ferrari Colombo Romano Ricci Marino Greco Bruno Gallo Conti De Santis Mancini Costa",
    "Jr.", "III", "(test)", "#1", "a/b", "A_B", "Y" * 120, "l" * 700, "🙂🙃", "ñ", "\x1b[0m",
]


def dates():
    rome = zoneinfo.ZoneInfo("Europe/Rome")
    out = []
    for year in (1999, 2000, 2023, 2024, 2025, 2026, 2027, 2031, 2099, 9999, 1):
        for month, day in ((1, 1), (2, 28), (6, 30), (8, 15), (10, 31), (12, 31)):
            for zone in (rome, datetime.timezone.utc):
                try:
                    out.append(datetime.datetime(year, month, day, tzinfo=zone))
                except (ValueError, OverflowError):
                    pass
    out.append(datetime.datetime(2024, 2, 29, 23, 59, 59, tzinfo=rome))
    out.append(datetime.datetime(2026, 3, 29, 2, 30, tzinfo=rome))
    out.append(datetime.datetime(2026, 7, 4, 12, 0, 0, 123456, tzinfo=rome))
    out.append(datetime.datetime(2026, 7, 4, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))))
    return out


def render_texts(templates, cmap):
    rng = random.Random(20261110)
    when = dates()
    texts = []
    # Every template with every first name and every last name at least once,
    # and every date at least once.
    for template in templates:
        for i, first in enumerate(FIRST_NAMES):
            texts.append(fill(template, first, LAST_NAMES[i % len(LAST_NAMES)], when[i % len(when)]))
        for i, last in enumerate(LAST_NAMES):
            texts.append(fill(template, FIRST_NAMES[(i * 7) % len(FIRST_NAMES)], last, when[(i * 5 + 3) % len(when)]))
        for i, moment in enumerate(when):
            texts.append(fill(template, FIRST_NAMES[i % 6], LAST_NAMES[i % 5], moment))
        for _ in range(60):
            texts.append(fill(template, rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES), rng.choice(when)))

    mapped = sorted(cmap)
    # Every mapped character alone, first on a line and between others.
    texts += [chr(cp) for cp in mapped]
    texts.append("".join(chr(cp) for cp in mapped))
    texts.append("\n".join(chr(cp) + "x" + chr(cp) for cp in mapped[:40]))
    texts += ["x" + chr(cp) + chr(cp) + "x" for cp in mapped]
    # Unmapped and unusual code points.
    for cp in list(range(0, 0x20)) + [0x7F, 0x80, 0x9F, 0xA0, 0xAD, 0xE8, 0x300, 0x301, 0x20AC, 0xFFFD, 0xFFFE, 0xFFFF, 0x10000, 0x1F600, 0xE0001, 0x10FFFF]:
        if cp != 0x0A:
            texts += [chr(cp), "a" + chr(cp) + "b"]
    texts += [
        "", "\n", "\n\n\n", " ", "   \n   ", "a", "a\n", "\na", "\r\n", "a\r\nb", "a\x0bb\x0cc", "a b c",
        "j\njj\n(j)", "A" * 100, "W" * 200, "il" * 400, "M" * 16, "\n".join(["riga"] * 5),
        "\n".join(["riga"] * 6), "\n".join(["riga"] * 7), "\n".join(f"riga {i}" for i in range(40)),
        "\n".join("Wy" * i for i in range(60)), "\n" * 300, "x\n" * 700,
    ]
    # Widths around the right edge of the canvas and of the crop.
    for n in range(80, 100):
        texts.append("W" * n)
        texts.append("M" * n + "j")
    for n in range(1, 30):
        texts.append("a" * (n * 37))
    # Lines wide enough to raise the encoder buffer above 65536 bytes, to give
    # several IDAT chunks and to slide the deflate window many times.
    texts += ["W" * 2000, "x" * 3000 + "\n" + "y" * 10, "é" * 3300, "ab " * 3000, "i" * 9000]
    texts.append("".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(20000)))
    texts.append("\n".join("".join(rng.choice("EARIOTNSLC ") for _ in range(4000)) for _ in range(12)))
    texts.append("WMW@" * 25000)
    texts.append("\n".join(["Q" * 30000] * 40))
    # Random strings.
    alphabets = [
        "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "0123456789-:+ ",
        " .,;'\"()/",
        "àèéìòùÀÈÉÌÒÙçñüöäß",
        "\t\r\x00\x1f",
        "".join(chr(cp) for cp in mapped),
        "бгджзАлександр",
        "αβγδεζ",
        "漢字かなカナ",
        "😀🎉\U0001f9d1‍\U0001f4bb",
        "̀́̈⃣",
    ]
    for _ in range(260):
        lines = []
        for _ in range(rng.choice((1, 1, 1, 2, 3, 5, 9))):
            pool = "".join(rng.sample(alphabets, rng.randint(1, 4)))
            lines.append("".join(rng.choice(pool) for _ in range(rng.choice((0, 1, 3, 12, 40, 90, 160)))))
        texts.append("\n".join(lines))
    seen, unique = set(), []
    for text in texts:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def render_corpus(generate, templates, cmap):
    def outcome(text):
        try:
            image = generate(text)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
        except Exception as error:  # recorded: Go must refuse these
            return {"text": text, "error": type(error).__name__}
        data = buffer.getvalue()
        return {"text": text, "sha256": hashlib.sha256(data).hexdigest(), "length": len(data), "_png": data}

    cases = [outcome(text) for text in render_texts(templates, cmap)]
    # Sizes at which Pillow raises instead of returning an image.
    limit = ImageFont.MAX_STRING_LENGTH
    for text in ("i" * limit, "i" * (limit + 1), "a\n" + " " * (limit + 1), "W" * 700000, "\n" * 600000 + "W" * 20):
        cases.append(outcome(text))
    kept = 0
    for index, case in enumerate(cases):
        data = case.pop("_png", None)
        # The PNG itself for the first cases of each template and a spread of
        # the rest, so a failure can be inspected pixel by pixel.
        if data is not None and len(data) < 12000 and (index % 9 == 0 or len(case["text"]) < 3):
            case["png"] = base64.b64encode(data).decode("ascii")
            kept += 1
    errors = sum(1 for case in cases if "error" in case)
    print(f"render: {len(cases)} texts, {kept} with the PNG, {errors} that raise")
    return cases


# --- PNG encoder corpus --------------------------------------------------------


def mix(seed, index):
    z = (index + seed * 0x9E3779B97F4A7C15) & MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return z ^ (z >> 31)


def formula_pixels(width, height, seed, run, period, mask):
    """Mirrored by formulaPixels in png_test.go."""
    row_bytes = width * 3
    rows = {}
    out = bytearray()
    for y in range(height):
        key = y % period
        if key not in rows:
            rows[key] = bytes(mix(seed, key * row_bytes + x // run) & mask for x in range(row_bytes))
        out += rows[key]
    return bytes(out)


def png_corpus():
    rng = random.Random(13)
    cases = []

    def save(width, height, pixels):
        buffer = io.BytesIO()
        Image.frombytes("RGB", (width, height), pixels).save(buffer, format="PNG")
        return buffer.getvalue()

    for width, height in ((1, 1), (1, 7), (2, 2), (3, 1), (5, 4), (16, 16), (31, 9), (64, 40), (100, 30)):
        for kind in range(4):
            if kind == 0:
                pixels = bytes(width * height * 3)
            elif kind == 1:
                pixels = bytes(rng.randrange(256) for _ in range(width * height * 3))
            elif kind == 2:
                pixels = bytes((x * 5 + y * 3 + c * 40) & 0xFF for y in range(height) for x in range(width) for c in range(3))
            else:
                pixels = bytes(rng.choice((0, 0, 0, 255, 128, 37)) for _ in range(width * height * 3))
            cases.append({"png": base64.b64encode(save(width, height, pixels)).decode("ascii")})
    formulas = [
        (300, 200, 1, 1, 1 << 30, 0xFF),  # incompressible: stored blocks, several IDAT chunks
        (300, 200, 2, 1, 1 << 30, 0x0F),
        (300, 200, 3, 1, 1 << 30, 0x01),
        (640, 480, 4, 7, 1 << 30, 0xFF),
        (640, 480, 5, 40, 3, 0xFF),
        (640, 480, 6, 3, 17, 0x3F),
        (1000, 300, 7, 300, 1 << 30, 0xFF),
        (1000, 300, 8, 2, 50, 0x07),
        (17000, 12, 9, 5, 1 << 30, 0xFF),  # bufsize = 4 * width
        (17000, 40, 10, 1, 1 << 30, 0x03),
        (30000, 9, 11, 1, 1 << 30, 0xFF),
        (40000, 6, 12, 90, 2, 0xFF),
        (2000, 700, 13, 11, 1 << 30, 0x1F),
        (333, 3000, 14, 4, 250, 0xFF),
        (4096, 64, 15, 1, 1 << 30, 0x7F),
        (21845, 30, 16, 2, 1 << 30, 0xFF),
        (128, 128, 17, 1, 1 << 30, 0xFF),
        (800, 600, 18, 1000, 1, 0xFF),
        (1500, 1500, 19, 1, 1 << 30, 0x01),
        (1500, 1000, 20, 6, 1 << 30, 0x0F),
    ]
    for width, height, seed, run, period, mask in formulas:
        data = save(width, height, formula_pixels(width, height, seed, run, period, mask))
        cases.append(
            {
                "width": width, "height": height, "seed": seed, "run": run, "period": period, "mask": mask,
                "sha256": hashlib.sha256(data).hexdigest(), "length": len(data),
            }
        )
    print(f"png: {len(cases)} images")
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--be-root", type=Path, default=ROOT / "BE")
    arguments = parser.parse_args()
    be = arguments.be_root
    if PIL.__version__ != "13.0.0":
        raise SystemExit(f"Pillow 13.0.0 required, found {PIL.__version__}")
    warnings.simplefilter("ignore", Image.DecompressionBombWarning)

    namespace = {"Image": Image, "ImageDraw": ImageDraw, "ImageFont": ImageFont}
    exec(function_source(be / "application" / "utils" / "api_utils.py", "generate_image_with_text"), namespace)
    generate = namespace["generate_image_with_text"]
    templates = [
        call_site_template(be / "application" / "utils" / "subscriptions_utils.py"),
        call_site_template(be / "application" / "views" / "subscriptions_views.py"),
    ]
    try:
        ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        pass
    else:
        raise SystemExit("DejaVuSans is installed here; production falls back to load_default()")
    font = ImageFont.load_default()
    if not isinstance(font, ImageFont.FreeTypeFont) or font.layout_engine != ImageFont.Layout.BASIC:
        raise SystemExit("load_default() is not the FreeType font with basic layout")

    zlib_version = features.version("zlib")
    variant = "zlibng" if zlib_version.endswith(".zlib-ng") else "zlib"
    print(f"zlib: {zlib_version} -> {variant}")
    cmap = write_font(font)
    for name, cases in (("render", render_corpus(generate, templates, cmap)), ("png", png_corpus())):
        write_json(PACKAGE / "testdata" / f"{name}_golden_{variant}.json.gz", {"zlib": zlib_version, "cases": cases})


if __name__ == "__main__":
    main()
