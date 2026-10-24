"""Pillow JPEG pixel decoder oracle, separate from lazy format/metadata opening."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_jpeg import vectors as encoder_vectors
    cases = []

    def add(name, data, expected_size=None, expected_mode=None, invalid=False, encode_invalid=False):
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                width, height = image.size
                mode = image.mode
                comment = image.info.get("comment")
                pixels = image.tobytes()
                jpeg = io.BytesIO()
                try: image.save(jpeg, format="JPEG")
                except OSError:
                    assert encode_invalid, f'{name}: unexpected encoding failure'
                else: assert not encode_invalid, f'{name}: expected encoding failure changed'
        except (OSError, SyntaxError, ValueError):
            assert invalid, f'{name}: expected valid JPEG rejected by Pillow'
            cases.append(dict(name=name, data=base64.b64encode(data).decode(), invalid=True))
            return
        assert not invalid, f'{name}: explicit malformed JPEG expectation changed'
        assert (width, height) == expected_size and mode == expected_mode
        cases.append(dict(name=name, data=base64.b64encode(data).decode(), invalid=False,
            width=width, height=height, mode=mode, encode_invalid=encode_invalid, comment=base64.b64encode(comment).decode() if comment is not None else None, expected=base64.b64encode(pixels).decode(), expected_jpeg=base64.b64encode(jpeg.getvalue()).decode()))

    original = encoder_vectors()
    for vector in original:
        mode = vector['mode'] if vector['mode'] in ('L', 'CMYK') else 'RGB'
        add(vector['name'], base64.b64decode(vector['expected']), (vector['width'], vector['height']), mode)
    for mode, channels in [('L', 1), ('RGB', 3), ('CMYK', 4)]:
        raw = bytes(((i * 97) ^ (i // 11 * 23)) & 255 for i in range(31 * 33 * channels))
        image = Image.frombytes(mode, (31, 33), raw)
        for quality in (5, 80, 100):
            for sampling in (0, 1, 2):
                for progressive in (False, True):
                    for optimize in (False, True):
                        out = io.BytesIO()
                        image.save(out, format='JPEG', quality=quality, subsampling=sampling, progressive=progressive, optimize=optimize)
                        add(f'{mode} q{quality} s{sampling} p{progressive} o{optimize}', out.getvalue(), (31, 33), mode)
    raw = bytes(((i * 97) ^ (i // 11 * 23)) & 255 for i in range(64 * 65 * 3))
    image = Image.frombytes('RGB', (64, 65), raw)
    exif = Image.Exif(); exif[274] = 6
    for name, options in [
        ('EXIF orientation ignored', {'exif': exif}),
        ('ICC bytes do not transform colors', {'icc_profile': b'synthetic ICC bytes'}),
        ('comment preserved', {'comment': b'synthetic comment'}),
        ('empty comment preserved', {'comment': b''}),
        ('binary comment preserved', {'comment': b'\x00\xff\x80\n'}),
        ('comment buffer boundary preserved', {'comment': b'c' * 65511}),
        ('RGB components', {'keep_rgb': True}),
        ('RGB progressive', {'keep_rgb': True, 'progressive': True}),
        ('restart blocks', {'restart_marker_blocks': 1}),
        ('restart rows', {'restart_marker_rows': 1}),
    ]:
        out = io.BytesIO(); image.save(out, format='JPEG', **options)
        if name.startswith('restart'):
            assert any(bytes([255, i]) in out.getvalue() for i in range(208, 216))
        add(name, out.getvalue(), (64, 65), 'RGB')
    cmyk = next(v for v in original if v['name'] == 'CMYK 17x13 noise')
    cmyk_data = base64.b64decode(cmyk['expected'])
    assert cmyk_data[2:4] == b'\xff\xee' and cmyk_data[6:11] == b'Adobe'
    app_end = 4 + int.from_bytes(cmyk_data[4:6], 'big')
    add('CMYK without Adobe marker', cmyk_data[:2] + cmyk_data[app_end:], (17, 13), 'CMYK')
    ycck = bytearray(cmyk_data); ycck[17] = 2
    add('YCCK transform marker', bytes(ycck), (17, 13), 'CMYK')
    base = next(v for v in original if v['name'] == 'RGB 17x13 noise')
    data = base64.b64decode(base['expected'])
    add('trailing garbage', data + b'ignored after first image', (17, 13), 'RGB')
    add('concatenated JPEGs', data + cmyk_data, (17, 13), 'RGB')
    for name, changed in [('early EOI 10', data[:-10] + b'\xff\xd9'), ('early EOI 100', data[:-100] + b'\xff\xd9'),
            ('fill markers', data[:2] + b'\xff' * 4 + data[2:]), ('escaped marker junk', data[:2] + b'\xff\x00' + data[2:])]:
        add(name, changed, (17, 13), 'RGB')
    def com(payload): return b'\xff\xfe' + (len(payload)+2).to_bytes(2,'big') + payload
    add('last pre-scan comment wins', data[:2] + com(b'first') + com(b'last') + data[2:], (17, 13), 'RGB')
    add('late comment does not replace initial comment', data[:2] + com(b'first') + data[2:-2] + com(b'late') + data[-2:], (17, 13), 'RGB')
    add('RGB long comment preserved', data[:2] + com(b'c' * 65512) + data[2:], (17, 13), 'RGB')
    add('CMYK comment boundary preserved', cmyk_data[:2] + com(b'c' * 65513) + cmyk_data[2:], (17, 13), 'CMYK')
    add('CMYK long comment preserved', cmyk_data[:2] + com(b'c' * 65514) + cmyk_data[2:], (17, 13), 'CMYK')
    add('RGB maximum comment preserved',data[:2]+com(b'c'*65533)+data[2:],(17,13),'RGB')
    add('CMYK maximum comment preserved',cmyk_data[:2]+com(b'c'*65533)+cmyk_data[2:],(17,13),'CMYK')
    wide=Image.new('RGB',(16400,1),(20,40,60));out=io.BytesIO();wide.save(out,format='JPEG',comment=b'c'*65533)
    add('wide image enlarges comment buffer',out.getvalue(),(16400,1),'RGB')
    add('not JPEG' , b'not JPEG', invalid=True)
    add('leading junk is rejected', b'junk' + data, invalid=True)
    # Every prefix of these noisy baseline/progressive streams lacks a complete
    # entropy-coded image. Each rejection is an explicit expectation, not an
    # output silently copied from the oracle.
    for progressive in (False, True):
        raw = bytes((i * 73) & 255 for i in range(17 * 13 * 3))
        image = Image.frombytes('RGB', (17, 13), raw)
        out = io.BytesIO(); image.save(out, format='JPEG', progressive=progressive)
        complete = out.getvalue()
        for end in range(len(complete)):
            add(f'truncated p{progressive} at {end}', complete[:end], invalid=True)
    return cases


def run(work, log, tool_env, django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    codec = work / 'decode-image-codec'; build_codec(codec, log, tool_env)
    fault = work / 'decode-fault-codec'; build_fault_codec(fault, log, tool_env)
    cases = vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri = 'opaque,' + case['data']
                expected = uri if case['invalid'] or case.get('encode_invalid') else 'data:image/jpeg;base64,' + case['expected_jpeg']
                assert django_helper(uri) == expected, f"{case['name']}: actual Django helper differs"
        print(f'Django helper: {len(cases)} JPEG output/fallback cases passed', flush=True)
    path = work / 'login-jpeg-decode.json'
    path.write_text(json.dumps(cases)); path.chmod(0o600)
    valid = sum(not case['invalid'] for case in cases)
    print(f'JPEG decode oracle: {valid} exact pixel cases, {len(cases)-valid} explicit invalid streams', flush=True)
    try:
        subprocess.run(['go', 'test', '-race', '-tags=imageintegration', './pkg/storage/loginimage', '-run', '^TestJPEGDecode'],
            cwd=ROOT / 'BE_GO', check=True, timeout=180,
            env={**tool_env, 'ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE': str(path), 'ASSOZETA_LOGIN_JPEG_CODEC': str(codec), 'ASSOZETA_LOGIN_JPEG_FAULT_CODEC': str(fault)})
    finally:
        path.unlink(missing_ok=True); codec.unlink(missing_ok=True); fault.unlink(missing_ok=True)
        Path(str(fault) + ".started").unlink(missing_ok=True)
    return len(cases)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-decode-', dir='/tmp') as directory:
        run(Path(directory), None, tool_env)
