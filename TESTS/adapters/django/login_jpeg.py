"""Exact JPEG encoder oracle; no file decoding or public login parity claim.

Standalone execution needs Pillow, Go, a C compiler, pkg-config and libjpeg.
All native executables and raster/JPEG fixtures live in a disposable directory.
"""
import base64
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'BE_GO/pkg/storage/loginimage/native/jpeg_encode.c'


def vectors():
    from PIL import Image
    result = []
    for mode, channels in [('L', 1), ('RGB', 3), ('RGBX', 4), ('YCbCr', 3), ('CMYK', 4)]:
        for width, height in [(1, 1), (2, 3), (7, 9), (8, 8), (15, 17), (16, 16), (17, 13), (31, 33), (129, 97)]:
            for pattern in ('black', 'white', 'gradient', 'noise'):
                count = width * height * channels
                if pattern == 'black': raw = bytes(count)
                elif pattern == 'white': raw = b'\xff' * count
                elif pattern == 'gradient': raw = bytes((i // channels * 7 + i % channels * 37) & 255 for i in range(count))
                else:
                    state = 0x13579BDF
                    noise = bytearray()
                    for _ in range(count):
                        state = (state * 1664525 + 1013904223) & 0xffffffff
                        noise.append(state >> 24)
                    raw = bytes(noise)
                image = Image.frombytes(mode, (width, height), raw)
                output = io.BytesIO(); image.save(output, format='JPEG')
                encoded = output.getvalue()
                # Independently expected format policy anchors the byte oracle.
                with Image.open(io.BytesIO(encoded)) as check:
                    assert check.size == (width, height) and check.format == 'JPEG'
                    assert check.mode == ('L' if mode == 'L' else 'CMYK' if mode == 'CMYK' else 'RGB')
                    assert check.quantization[0][:8] == [8, 6, 5, 8, 12, 20, 26, 31]
                    assert not check.info.get('progressive') and 'exif' not in check.info and 'icc_profile' not in check.info
                    sampling = [(1, 1, 1, 0)] if mode == 'L' else [(67, 1, 1, 0), (77, 1, 1, 0), (89, 1, 1, 0), (75, 1, 1, 0)] if mode == 'CMYK' else [(1, 2, 2, 0), (2, 1, 1, 1), (3, 1, 1, 1)]
                    assert check.layer == sampling
                    if mode not in ('L', 'CMYK'):
                        assert check.quantization[1][:8] == [9, 9, 12, 24, 50, 50, 50, 50]
                    if mode == 'CMYK': assert check.info['adobe_transform'] == 0
                    else: assert check.info['jfif_density'] == (1, 1) and check.info['jfif_unit'] == 0
                result.append(dict(name=f'{mode} {width}x{height} {pattern}', mode=mode, width=width, height=height,
                    pixels=base64.b64encode(raw).decode(), expected=base64.b64encode(encoded).decode()))
    return result


def build_codec(output, log, tool_env):
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'libjpeg'], text=True, env=tool_env))
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(NATIVE), str(NATIVE.with_name("jpeg_decode.c")), *flags, '-o', str(output)],
        check=True, timeout=60, env=tool_env, stdout=log, stderr=subprocess.STDOUT)


def build_fault_codec(output, log, tool_env):
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(ROOT / 'BE_GO/pkg/storage/loginimage/testdata/fault_codec.c'), '-o', str(output)],
        check=True, timeout=60, env=tool_env, stdout=log, stderr=subprocess.STDOUT)


def run(work, log, tool_env):
    from PIL import __version__, features
    version = subprocess.check_output(['pkg-config', '--modversion', 'libjpeg'], text=True, env=tool_env).strip()
    codec = work / 'image-codec'; fault = work / 'fault-codec'
    build_codec(codec, log, tool_env)
    build_fault_codec(fault, log, tool_env)
    cases = vectors(); path = work / 'login-jpeg.json'
    path.write_text(json.dumps(cases)); path.chmod(0o600)
    print(f'JPEG oracle: {len(cases)} vectors; Pillow {__version__}/libjpeg-turbo {features.version("libjpeg_turbo")}, native {version}', flush=True)
    try:
        subprocess.run(['go', 'test', '-race', '-tags=imageintegration', './pkg/storage/loginimage', '-run', '^TestJPEG(AgainstPillow|ProcessFailureAndAdmission|NativeProtocol)$'],
            cwd=ROOT / 'BE_GO', check=True, timeout=180,
            env={**tool_env, 'ASSOZETA_LOGIN_JPEG_FIXTURE': str(path), 'ASSOZETA_LOGIN_JPEG_CODEC': str(codec), 'ASSOZETA_LOGIN_JPEG_FAULT_CODEC': str(fault)})
    finally:
        path.unlink(missing_ok=True)
        codec.unlink(missing_ok=True); fault.unlink(missing_ok=True)
        Path(str(fault) + '.started').unlink(missing_ok=True)
    return len(cases)


if __name__ == '__main__':
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-', dir='/tmp') as directory:
        run(Path(directory), None, tool_env)
