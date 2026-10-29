"""Explicit lazy JPEG marker expectations, not complete EXIF/MPO admission."""
import base64
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import warnings

ROOT = Path(__file__).resolve().parents[3]


def marker(code, payload=b''):
    return bytes([255, code]) + struct.pack('>H', len(payload) + 2) + payload


def frame(width, height, channels=3, precision=8, entries=True):
    return struct.pack('>BHHB', precision, height, width, channels) + (b''.join(bytes([i+1,17,0]) for i in range(channels)) if entries else b'')


def jpeg(width=200, height=100, channels=3, prefix=b'', suffix=b''):
    return b'\xff\xd8' + prefix + marker(192,frame(width,height,channels)) + suffix + marker(218)


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_jpeg import vectors as encoded_vectors
    cases=[]

    def add(name,data,valid=True,width=200,height=100,mode='RGB',comment=None,warning=False,header_bytes=None):
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter('always')
                image=Image.open(io.BytesIO(data))
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            assert not valid, f'{name}: unexpectedly rejected by Pillow'
            cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=True))
            return
        assert valid, f'{name}: unexpectedly accepted by Pillow'
        assert image.size==(width,height) and image.mode==mode, name
        assert image.info.get('comment')==comment, name
        assert any(isinstance(w.message,Image.DecompressionBombWarning) for w in captured)==warning, name
        end=len(data) if header_bytes is None else header_bytes
        assert image.fp.tell()==end, f'{name}: unexpected lazy stop'
        cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=False,width=width,height=height,mode=mode,
            comment=base64.b64encode(comment).decode() if comment is not None else None,warning=warning,header_bytes=end))
        image.close()

    for channels,mode in [(1,'L'),(3,'RGB'),(4,'CMYK')]:
        add('header-only '+mode,jpeg(channels=channels),mode=mode)
        image=Image.open(io.BytesIO(jpeg(channels=channels)))
        try:image.load()
        except OSError:pass
        else:raise AssertionError('header-only JPEG unexpectedly has pixels')
    for channels,mode in [(1,'L'),(3,'RGB'),(4,'CMYK')]:
        for suffix in (b'',b'\xff',b'junk',b'\xff\xd9'):
            add(f'EOF without scan {mode} {suffix.hex()}',jpeg(channels=channels)[:-4]+suffix,mode=mode)
    add('no component entries is accepted lazily',b'\xff\xd8'+marker(192,frame(200,100,entries=False))+marker(218))
    known=set(range(192,255))
    frame_codes=set(range(192,208))-{196,200,204}|{222}
    for code in range(256):
        payload=frame(1,1) if code in frame_codes else b''
        valid=code in known|{0,255} and code!=218
        add(f'marker {code:02x}',jpeg(prefix=marker(code,payload)),valid=valid,comment=b'' if code==254 else None)
    for size in (0,1):
        add(f'non-positive payload length {size}',b'\xff\xd8\xff\xfe'+struct.pack('>H',size)+jpeg()[2:],comment=b'')
    for precision in (0,1,7,9,12,16,255):
        add(f'precision {precision}',b'\xff\xd8'+marker(192,frame(200,100,precision=precision))+marker(218),valid=False)
    for channels in (0,2,5,255):
        add(f'channels {channels}',jpeg(channels=channels),valid=False)
    for rest in (b'x',b'xy'):
        add('partial component '+str(len(rest)),b'\xff\xd8'+marker(192,frame(200,100)+rest)+marker(218),valid=False)
    for tag,code,min_size in [(b'JFIF',224,7),(b'Adobe',238,7)]:
        for length in range(len(tag),13):
            add(f'{tag!r} length {length}',jpeg(prefix=marker(code,tag+b'\0'*(length-len(tag)))),valid=length>=min_size)
    for value in (0,15,16,32,255):
        expected=64 if value<16 else 128
        for size in (0,expected-1,expected,expected+1):
            add(f'quantization {value} size {size}',jpeg(prefix=marker(219,bytes([value])+bytes(size))),valid=size==expected)
    for suffix in (b'',b'\1',b'\1\1'):
        payload=b'ICC_PROFILE\0'+suffix
        add('ICC before SOF '+str(len(suffix)),jpeg(prefix=marker(226,payload)),valid=len(suffix)>=2)
        add('ICC after SOF '+str(len(suffix)),jpeg(suffix=marker(226,payload)))
    for suffix,valid in [(b'',True),(b'8BIM',True),(b'8BIM\x03',True),(b'8BIM\x03\xed',False),
            (b'8BIM\x03\xed\0',True),(b'8BIM\x03\xed\0\0\0\0\0\0',True)]:
        add('Photoshop truncation '+str(len(suffix)),jpeg(prefix=marker(237,b'Photoshop 3.0\0'+suffix)),valid=valid)
    for comment in (b'',b'a\0\xff',b'c'*65533):
        add('comment '+str(len(comment)),jpeg(prefix=marker(254,b'first')+marker(254,comment)),comment=comment)
    add('ignored marker junk',jpeg(prefix=b'\xff\xd8junk\xff\0ignored\xff\xff\xff\xd8'))
    add('last SOF wins',jpeg(prefix=marker(192,frame(0,0))))
    for width,height,valid,warning in [(0,1,False,False),(1,0,False,False),(10000,8947,True,False),
            (10000,8948,True,True),(10000,17895,True,True),(10000,17896,False,False),(65535,65535,False,False)]:
        add(f'dimensions {width}x{height}',jpeg(width,height),valid,width,height,warning=warning)
    encoded=encoded_vectors()
    for case in encoded:
        data=base64.b64decode(case['expected']);scan=data.index(b'\xff\xda');end=scan+2+int.from_bytes(data[scan+2:scan+4],'big')
        add('encoded '+case['name'],data,width=case['width'],height=case['height'],mode=case['mode'] if case['mode'] in ('L','CMYK') else 'RGB',header_bytes=end)
    case=next(v for v in encoded if v['name']=='RGB 17x13 noise')
    data=base64.b64decode(case['expected']);scan=data.index(b'\xff\xda');end=scan+2+int.from_bytes(data[scan+2:scan+4],'big')
    # These authored boundaries are the ends of SOF and four DHT segments.
    # EOF there (or after a single FF) now admits dimensions without pixels.
    boundaries=(177,210,393,426,609)
    assert data[158:160]==b'\xff\xc0' and end==623
    assert all(data[n:n+2] in (b'\xff\xc4',b'\xff\xda') for n in boundaries)
    for length in range(len(data)+1):
        valid=length>=end or length in boundaries or length-1 in boundaries
        add('lazy prefix '+str(length),data[:length],valid=valid,width=17,height=13,header_bytes=min(length,end))
    return cases


def run(work,log,tool_env,django_helper=None):
    cases=vectors()
    if django_helper is not None:
        # These incomplete files are recognized, but their pixels cannot load.
        # Their 200-pixel bounds make Django return the original before loading.
        from PIL import ImageFile
        from unittest.mock import patch
        loads=[]
        original_load=ImageFile.ImageFile.load
        def observed_load(image,*args,**kwargs):
            loads.append(image.size)
            return original_load(image,*args,**kwargs)
        # Observe and delegate the real loader: equal URI output alone cannot
        # distinguish early passthrough from the helper's caught-error fallback.
        with patch.object(ImageFile.ImageFile,'load',observed_load):
            for channels in (1,3,4):
                uri='opaque,'+base64.b64encode(jpeg(channels=channels)).decode()
                assert django_helper(uri)==uri,'Django lazy passthrough output changed'
        assert loads==[],'Django attempted pixel loading on lazy passthrough'
    path=work/'jpeg-header.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    print(f'JPEG marker oracle: {len(cases)} explicit lazy-header cases',flush=True)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestJPEGMarker'],cwd=ROOT/'BE_GO',check=True,timeout=180,
            env={**tool_env,'ASSOZETA_JPEG_HEADER_FIXTURE':str(path)})
    finally:path.unlink(missing_ok=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-header-',dir='/tmp') as directory:
        run(Path(directory),None,tool_env)
