"""Complete JPEG file-stage policy, separate from the remaining image formats."""
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
    from TESTS.adapters.django.login_jpeg_header import jpeg, marker
    cases = []

    def add(name, data, size, mode, keep=False, invalid=False):
        encoded = None
        if not keep:
            try:
                with Image.open(io.BytesIO(data)) as image:
                    result = image.resize(size, Image.Resampling.LANCZOS)
                    assert result.size == size and result.mode == mode, name
                    out = io.BytesIO(); result.save(out, format='JPEG'); encoded = out.getvalue()
            except (OSError, ValueError):
                assert invalid, name
            else:
                assert not invalid, name
        cases.append(dict(name=name, data=base64.b64encode(data).decode(), keep=keep or invalid,
            expected=base64.b64encode(encoded).decode() if encoded is not None else None))

    # Explicit expected dimensions, independent of the implementation's ratio.
    for mode, channels in [('L', 1), ('RGB', 3), ('CMYK', 4)]:
        for size, target, same in [((200,100),(200,100),True), ((100,200),(100,200),True),
                ((201,101),(200,100),False), ((17,13),(200,152),False),
                ((289,100),(199,69),False), ((1,400),(0,200),False), ((400,1),(200,0),False)]:
            raw = bytes((i*73)&255 for i in range(size[0]*size[1]*channels))
            image = Image.frombytes(mode, size, raw)
            out = io.BytesIO(); image.save(out, format='JPEG', comment=b'preserved through resize')
            data = out.getvalue()
            for length in (len(data), 1024*1024, 1024*1024+1):
                padded = data + bytes(length-len(data))
                over = length > 1024*1024
                add(f'{mode} {size} bytes {length}', padded, target if over else size, mode,
                    keep=same and not over, invalid=over and 0 in target)
        add('header-only passthrough '+mode, jpeg(channels=channels), (200,100), mode, keep=True)
        add('header-only decode failure '+mode, jpeg(17,13,channels), (17,13), mode, invalid=True)
        data = jpeg(channels=channels)
        add('header-only over threshold '+mode, data+bytes(1024*1024+1-len(data)), (200,100), mode, invalid=True)
    for channels,mode in [(1,'L'),(3,'RGB'),(4,'CMYK')]:
        for suffix in (b'',b'\xff',b'junk',b'\xff\xd9'):
            for size,keep in [((200,100),True),((17,13),False)]:
                data=jpeg(*size,channels)[:-4]+suffix
                add(f'EOF without scan {mode} {suffix.hex()} {size}',data,size,mode,keep=keep,invalid=not keep)
    # Pillow retains metadata preceding an EOI/SOI tables-only boundary.
    image = Image.new('RGB',(17,13),(20,40,60)); out = io.BytesIO(); image.save(out,format='JPEG')
    data = b'\xff\xd8'+marker(254,b'tables-only comment')+b'\xff\xd9'+out.getvalue()
    add('comment from tables-only prefix', data, (17,13), 'RGB')
    assert len(cases) == 97
    return cases


def run(work, log, tool_env, django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    from TESTS.adapters.django.login_jpeg_metadata import vectors as metadata_vectors
    from TESTS.adapters.django.login_jpeg_decode import vectors as decode_vectors
    cases = vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri = 'opaque,'+case['data']
                expected = uri if case['keep'] else 'data:image/jpeg;base64,'+case['expected']
                assert django_helper(uri) == expected, case['name']
    codec = work/'file-codec'; build_codec(codec, log, tool_env)
    fault = work/'file-fault-codec'; build_fault_codec(fault, log, tool_env)
    paths = []
    env = {**tool_env, 'ASSOZETA_LOGIN_JPEG_CODEC': str(codec), 'ASSOZETA_LOGIN_JPEG_FAULT_CODEC': str(fault)}
    try:
        for name, variable, values in [('file','ASSOZETA_JPEG_FILE_FIXTURE',cases),
                ('metadata','ASSOZETA_JPEG_METADATA_FIXTURE',metadata_vectors()),
                ('decode','ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE',decode_vectors())]:
            path = work/('file-'+name+'.json'); path.write_text(json.dumps(values)); path.chmod(0o600)
            paths.append(path); env[variable] = str(path)
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestJPEGFile'],
            cwd=ROOT/'BE_GO', check=True, timeout=180, env=env)
    finally:
        for path in paths: path.unlink(missing_ok=True)
        codec.unlink(missing_ok=True); fault.unlink(missing_ok=True)
        Path(str(fault)+".started").unlink(missing_ok=True)
    print(f'JPEG file pipeline: {len(cases)} size/lazy/fallback cases plus 236 metadata and 1870 decoder cases', flush=True)
    return len(cases)


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    tool_env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-file-',dir='/tmp') as directory:
        run(Path(directory),None,tool_env)
