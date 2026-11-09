"""Rectangular compress_base64(max_size=(590, 130)) checked against Pillow and native Go.

The subscription-template view compresses the association logo with a
590x130 bound. Every expected value below is the actual Django helper's output
(Pillow 13 LANCZOS resampling and JPEG encoding), compiled unmodified from
BE/application/utils/api_utils.py. The vectors cover both limiting axes, the
1 MiB decoded-size threshold, the unchanged-size passthrough, truncation to a
zero dimension, alpha flattening and non-uniform pixels, so resampling weights
and pass order matter. Run directly with the production Python.
"""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BOUND = (590, 130)
THRESHOLD = 1 << 20


def pattern(mode, size, seed):
    """A deterministic non-uniform image: gradients plus a seeded checker."""
    from PIL import Image
    w, h = size
    channels = {'L': 1, 'RGB': 3, 'RGBA': 4}[mode]
    data = bytearray()
    for y in range(h):
        for x in range(w):
            base = (x * 255 // max(w - 1, 1) + y * 97 // max(h - 1, 1) + ((x // 7 + y // 5 + seed) % 3) * 40) & 0xff
            pixel = [base, (base * 3 + seed * 17 + y) & 0xff, (255 - base + x) & 0xff, (x * 5 + y * 3 + seed) & 0xff]
            data.extend(pixel[:channels])
    return Image.frombytes(mode, size, bytes(data))


def encode(image, fmt):
    out = io.BytesIO()
    options = {'lossless': True, 'exact': True} if fmt == 'WEBP' else {'optimize': False} if fmt == 'GIF' else {}
    image.save(out, format=fmt, **options)
    return out.getvalue()


def vectors():
    from TESTS.adapters.django.login_gif import helper
    from PIL import Image
    compress = helper()
    cases = []

    def add(name, data, expect_original=None, expect_size=None):
        uri = 'data:image/x;base64,' + base64.b64encode(data).decode()
        with contextlib.redirect_stdout(io.StringIO()):
            actual = compress(uri, max_size=BOUND)
        if expect_original is not None:
            assert (actual == uri) == expect_original, (name, 'passthrough')
        if expect_size is not None:
            out = Image.open(io.BytesIO(base64.b64decode(actual.split(',', 1)[1])))
            assert out.size == expect_size, (name, out.size, expect_size)
        cases.append(dict(name=name, uri=uri, expected=actual))

    def padded(data):
        # These containers ignore trailing bytes; the helper counts the whole
        # decoded file when choosing the resize path.
        return data + bytes(max(0, THRESHOLD + 1 - len(data)))

    sizes = [
        ((1180, 260), (590, 130)),   # both bounds exact at ratio 0.5
        ((2000, 300), (590, 88)),    # width limits
        ((300, 2000), (19, 130)),    # height limits
        ((800, 400), (260, 130)),    # height limits a wide image
        ((1000, 150), (590, 88)),
        ((640, 141), (590, 129)),    # float truncation of the height
        ((97, 31), (406, 130)),      # enlargement above the threshold
        ((5, 3), (216, 130)),
        ((590, 130), (590, 130)),
        ((3, 1000), (0, 0)),         # zero width: Django returns the original
    ]
    for (size, target), fmt, mode in zip(sizes * 3, ['PNG'] * 10 + ['BMP'] * 10 + ['WEBP'] * 10, ['RGB'] * 10 + ['L'] * 10 + ['RGB'] * 10):
        data = encode(pattern(mode, size, len(cases)), fmt)
        zero = target == (0, 0)
        add(f'{fmt} {mode} {size} above threshold', padded(data), expect_original=zero, expect_size=None if zero else target)
    for size in ((590, 130), (295, 130), (590, 65), (590, 100)):
        # Already at the computed bound and under 1 MiB: the original URI.
        add(f'PNG {size} at bound', encode(pattern('RGB', size, 1), 'PNG'), expect_original=True)
    for size in ((200, 100), (1180, 260), (600, 130), (590, 131), (10, 10)):
        # Under 1 MiB but not at the bound: re-encoded at the original size.
        add(f'PNG {size} below threshold', encode(pattern('RGB', size, 2), 'PNG'), expect_original=False, expect_size=size)
    for fmt in ('PNG', 'WEBP'):
        image = pattern('RGBA', (1300, 400), 3)
        add(f'{fmt} RGBA above threshold', padded(encode(image, fmt)), expect_size=(422, 130))
        add(f'{fmt} RGBA below threshold', encode(pattern('RGBA', (90, 40), 4), fmt), expect_size=(90, 40))
    jpeg = io.BytesIO()
    pattern('RGB', (1400, 500), 5).save(jpeg, format='JPEG', quality=95)
    add('JPEG above threshold', padded(jpeg.getvalue()), expect_size=(364, 130))
    small = io.BytesIO()
    pattern('RGB', (300, 90), 6).save(small, format='JPEG')
    add('JPEG below threshold', small.getvalue(), expect_size=(300, 90))
    add('JPEG generation', base64.b64decode(cases[-1]['expected'].split(',', 1)[1]), expect_size=(300, 90))
    gif = encode(pattern('L', (700, 300), 7).convert('P'), 'GIF')
    add('GIF above threshold', padded(gif), expect_size=(303, 130))
    for data in (b'', b'not an image', b'\x89PNG\r\n\x1a\n'):
        add(f'malformed {data!r}', data, expect_original=True)
    assert len(cases) == 50, len(cases)
    return cases


def run(work, log, tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    cases = vectors()
    codec, fault, path = work / 'template-codec', work / 'template-fault', work / 'template-logo.json'
    build_codec(codec, log, tool_env)
    build_fault_codec(fault, log, tool_env)
    path.write_text(json.dumps(cases))
    path.chmod(0o600)
    try:
        subprocess.run(['go', 'test', '-race', '-tags=imageintegration', './pkg/storage/loginimage', '-run', '^TestTemplateLogo'],
                       cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_TEMPLATE_LOGO_FIXTURE': str(path),
                       'ASSOZETA_LOGIN_JPEG_CODEC': str(codec), 'ASSOZETA_LOGIN_JPEG_FAULT_CODEC': str(fault)}, check=True, timeout=300)
    finally:
        for item in (path, codec, fault, Path(str(fault) + '.started')):
            item.unlink(missing_ok=True)
    result = dict(template_logo_cases=len(cases), bound=list(BOUND))
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'PKG_CONFIG_PATH')}
    with tempfile.TemporaryDirectory(prefix='az-template-logo-', dir='/tmp') as directory:
        run(Path(directory), None, env)
