"""Declared avatar-upload image policy, checked against Django and native Go.

This exercises the 512-pixel helper mode; it does not claim profile HTTP parity
or complete Pillow codec coverage. All fixtures and native tools are disposable.
"""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_gif import helper
    from TESTS.adapters.django.login_bmp import bitmap
    from TESTS.adapters.django.login_cur import cur
    from TESTS.adapters.django.login_gbr import gbr
    from TESTS.adapters.django.login_msp import msp
    from TESTS.adapters.django.login_pcd import pcd
    from TESTS.adapters.django.login_pixar import pixar
    compress = helper()
    cases = []

    def add(name, data, mode='L', size=(3, 2), color=73, target=None, original=False, gap=False):
        uri = 'opaque,' + base64.b64encode(data).decode()
        if original:
            expected = uri
        else:
            # Expected source colors and output dimensions are authored below,
            # independently of compress_base64. Pillow supplies JPEG encoding.
            im = Image.new(mode, target or size, color)
            if mode == 'RGBA':
                bg = Image.new('RGB', im.size, 'white')
                bg.paste(im, mask=im.getchannel('A'))
                im = bg
            out = io.BytesIO()
            im.save(out, format='JPEG')
            expected = 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()
        with contextlib.redirect_stdout(io.StringIO()):
            actual = compress(uri, max_size=(512, 512))
        assert actual == expected, (name, 'Django disagrees with declared upload policy')
        cases.append(dict(name=name, uri=uri, expected=expected, gap=gap))

    files = []
    for fmt in ('JPEG', 'PNG', 'BMP', 'PPM', 'PCX', 'TGA', 'SGI', 'GIF', 'WEBP', 'ICO'):
        mode = 'RGB' if fmt in ('WEBP', 'ICO') else 'L'
        size = (16, 16) if fmt == 'ICO' else (3, 2)
        im = Image.new(mode, size, (73, 73, 73) if mode == 'RGB' else 73)
        out = io.BytesIO()
        im.save(out, format=fmt, **({'lossless': True} if fmt == 'WEBP' else {'optimize': False} if fmt == 'GIF' else {}))
        files.append((fmt, out.getvalue(), mode, size, (73, 73, 73) if mode == 'RGB' else 73))
    files.extend([
        ('QOI', b'qoif' + struct.pack('>II', 3, 2) + bytes([3, 0]) + b'\xfe\x49\x49\x49' * 6 + bytes(8), 'RGB', (3, 2), (73, 73, 73)),
        ('GBR', gbr(3, 2) + b'\x49' * 6, 'L', (3, 2), 73),
        ('MSP', msp(3, 2, payload=b'\xe0\xe0'), '1', (3, 2), 255),
        ('SUN', struct.pack('>8I', 0x59a66a95, 3, 2, 8, 8, 1, 0, 0) + b'\x49' * 8, 'L', (3, 2), 73),
        ('XBM', b'#define sample_width 3\n#define sample_height 2\nstatic char sample_bits[] = {\n0x07,0x07};', '1', (3, 2), 255),
        ('CUR', cur([(bitmap(3, 4, 32, b'\x49\x49\x49\xff' * 6, dib=True), 3, 2)]), 'RGB', (3, 2), (73, 73, 73)),
        ('PCD', pcd(), 'RGB', (768, 512), (99, 99, 99)),
        ('PIXAR', pixar(3, 2, b'\x49' * 18), 'RGB', (3, 2), (73, 73, 73)),
    ])
    for fmt, data, mode, size, color in files:
        add(fmt + ' below threshold', data, mode, size, color)
        # These containers ignore trailing bytes; the helper counts the complete
        # decoded file, including those bytes, when selecting resize behavior.
        padded = data + bytes((1 << 20) + 1 - len(data))
        target = (512, 512) if fmt == 'ICO' else (512, 341)
        add(fmt + ' above threshold', padded, mode, size, color, target=target)
    for size, target, count, original in (
        ((3, 2), (3, 2), 1 << 20, False),
        ((512, 256), (512, 256), None, True),
        ((200, 100), (200, 100), None, False),
        ((1024, 512), (1024, 512), None, False),
        ((1, 1000000), (1, 1000000), (1 << 20) + 1, True),
    ):
        data = f'P5\n{size[0]} {size[1]}\n255\n'.encode() + b'I' * (size[0] * size[1])
        if count:
            data += bytes(count - len(data))
        add(f'PPM boundary {size} {count}', data, size=size, target=target, original=original)
    for fmt in ('PNG', 'TGA', 'WEBP'):
        im = Image.new('RGBA', (3, 2), (10, 20, 30, 127))
        out = io.BytesIO()
        im.save(out, format=fmt, **({'lossless': True, 'exact': True} if fmt == 'WEBP' else {}))
        add(fmt + ' alpha', out.getvalue(), 'RGBA', color=(10, 20, 30, 127))
    for data in (b'', b'plain bytes', b'P6', b'GIF89a', b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n'):
        add(f'malformed {data!r}', data, original=True)
    for fmt in ('TIFF', 'AVIF'):
        out = io.BytesIO()
        Image.new('RGB', (3, 2), (73, 73, 73)).save(out, format=fmt, **({'quality': 100, 'subsampling': '4:4:4'} if fmt == 'AVIF' else {}))
        add('unimplemented ' + fmt, out.getvalue(), 'RGB', color=(73, 73, 73), gap=True)
    assert len(cases) == 52
    return cases


def run(work, log, tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    cases = vectors()
    codec, fault, path = work / 'profile-codec', work / 'profile-fault', work / 'profile-images.json'
    build_codec(codec, log, tool_env)
    build_fault_codec(fault, log, tool_env)
    path.write_text(json.dumps(cases))
    path.chmod(0o600)
    try:
        subprocess.run(['go', 'test', '-race', '-tags=imageintegration', './pkg/storage/loginimage', '-run', '^TestProfileImage'],
                       cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_PROFILE_IMAGE_FIXTURE': str(path),
                       'ASSOZETA_LOGIN_JPEG_CODEC': str(codec), 'ASSOZETA_LOGIN_JPEG_FAULT_CODEC': str(fault)}, check=True, timeout=180)
    finally:
        for item in (path, codec, fault, Path(str(fault) + '.started')):
            item.unlink(missing_ok=True)
    result = dict(profile_image_cases=50, profile_image_open_gaps=['TIFF', 'AVIF'], complete_image_parity=False, gaps_exhaustive=False)
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-profile-images-', dir='/tmp') as directory:
        run(Path(directory), None, env)
