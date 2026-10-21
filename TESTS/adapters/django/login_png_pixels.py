"""Explicit PNG filter/Adam7/sample expectations, checked against Pillow pixels.

This oracle concerns compressed tile pixels, not PNG metadata/file admission.
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
import zlib

ROOT = Path(__file__).resolve().parents[3]
MODES = [(1,0,'1',1),(2,0,'L',1),(4,0,'L',1),(8,0,'L',1),(16,0,'I;16',1),
    (8,2,'RGB',3),(16,2,'RGB',3),(1,3,'P',1),(2,3,'P',1),(4,3,'P',1),(8,3,'P',1),
    (8,4,'LA',2),(16,4,'RGBA',2),(8,6,'RGBA',4),(16,6,'RGBA',4)]
PASSES = [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]


def chunk(kind, payload):
    return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))


def file_bytes(spec, compressed):
    header = struct.pack('>IIBBBBB',spec['width'],spec['height'],spec['depth'],spec['color'],0,0,int(spec['interlace']))
    palette = chunk(b'PLTE',bytes(v for i in range(256) for v in (i,i,i))) if spec['color']==3 else b''
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+palette+chunk(b'IDAT',compressed)+chunk(b'IEND',b'')


def vectors():
    from PIL import Image
    cases = []
    def add(name,spec,compressed,expected,mode,invalid=False):
        data = file_bytes(spec,compressed)
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                actual = image.convert('L').tobytes() if mode=='1' else image.tobytes()
                assert image.mode==mode and image.size==(spec['width'],spec['height']),name
        except (OSError,ValueError):
            assert invalid, name
        else:
            assert not invalid, name
            assert actual==expected, f'{name}: Pillow pixels differ from explicit samples'
        jpeg = None
        if not invalid and mode in ('1','L','RGB','RGBA'):
            normalized_mode = 'L' if mode=='1' else mode
            normalized = expected
            if mode=='RGBA':
                normalized_mode='RGB'
                normalized=bytes((expected[i+c]*expected[i+3]+255*(255-expected[i+3])+127)//255
                    for i in range(0,len(expected),4) for c in range(3))
            image=Image.frombytes(normalized_mode,(spec['width'],spec['height']),normalized)
            output=io.BytesIO();image.save(output,format='JPEG');jpeg=output.getvalue()
        cases.append(dict(name=name,spec=spec,data=base64.b64encode(compressed).decode(),
            expected=base64.b64encode(expected).decode(),mode=mode,invalid=invalid,
            jpeg=base64.b64encode(jpeg).decode() if jpeg is not None else None))

    for depth,color,mode,channels in MODES:
        for width,height in [(1,1),(3,2),(9,8),(17,13),(257,129)]:
            samples = [(i*73+i//7*19+3)%2**depth for i in range(width*height*channels)]
            if depth<8 and color==0:expected=bytes(v*255//(2**depth-1) for v in samples)
            elif depth==16 and color==0:expected=b''.join(struct.pack('<H',v) for v in samples)
            elif depth==16 and color==4:expected=bytes(v for i in range(0,len(samples),2) for v in [samples[i]>>8]*3+[samples[i+1]>>8])
            elif depth==16:expected=bytes(v>>8 for v in samples)
            else:expected=bytes(samples)
            for interlace in (False,True):
                spec=dict(width=width,height=height,depth=depth,color=color,interlace=interlace)
                for filter_type in range(5):
                    raw = bytearray()
                    for x0,y0,dx,dy in PASSES if interlace else [(0,0,1,1)]:
                        if x0>=width or y0>=height:continue
                        previous = None
                        for y in range(y0,height,dy):
                            values = [samples[(y*width+x)*channels+c] for x in range(x0,width,dx) for c in range(channels)]
                            if depth==16:row=b''.join(struct.pack('>H',v) for v in values)
                            elif depth==8:row=bytes(values)
                            else:
                                row=bytearray((len(values)*depth+7)//8)
                                for i,v in enumerate(values):row[i*depth//8]|=v<<(8-depth-i*depth%8)
                            if previous is None:previous=bytes(len(row))
                            bpp=max(1,(channels*depth+7)//8)
                            raw.append(filter_type)
                            for i,v in enumerate(row):
                                a=row[i-bpp] if i>=bpp else 0;b=previous[i];c=previous[i-bpp] if i>=bpp else 0
                                p=a+b-c
                                predictors=[0,a,b,(a+b)//2,min((a,b,c),key=lambda candidate:abs(p-candidate))]
                                raw.append((v-predictors[filter_type])%256)
                            previous=row
                    add(f'{depth}/{color} {width}x{height} Adam7={interlace} filter={filter_type}',spec,zlib.compress(raw),expected,mode)
    spec=dict(width=3,height=2,depth=8,color=0,interlace=False)
    raw=b'\0\1\2\3\0\4\5\6';compressed=zlib.compress(raw)
    assert len(compressed)==16
    for length in range(17):
        add(f'zlib prefix {length}',spec,compressed[:length],b'\1\2\3\4\5\6','L',invalid=length<11)
    for name,data,invalid in [
        ('bad adler',compressed[:-1]+bytes([compressed[-1]^1]),True),
        ('trailing bytes',compressed+b'garbage',False),
        ('extra decompressed bytes',zlib.compress(raw+b'extra'),False),
        ('short row',zlib.compress(raw[:-1]),True),
        ('empty stream',zlib.compress(b''),True),
        ('bad zlib header',b'\0\0'+compressed[2:],True),
    ]:add(name,spec,data,b'\1\2\3\4\5\6','L',invalid)
    for f in (5,6,127,255):add(f'invalid filter {f}',spec,zlib.compress(bytes([f])+raw[1:]),b'','L',True)
    # Pillow stops at the last required row. With any extra inflated byte it
    # does not reach the checksum, even when that checksum is corrupt.
    for extra in (0,1,2,8,100,65536):
        for level in (0,1,6,9):
            packed=zlib.compress(raw+bytes(extra),level)
            packed=packed[:-1]+bytes([packed[-1]^1])
            add(f'bad adler extra={extra} level={level}',spec,packed,b'\1\2\3\4\5\6','L',extra==0)
    # A valid stream end on a complete row leaves the remaining canvas black.
    add('early stream end after complete row',spec,zlib.compress(raw[:4]),b'\1\2\3\0\0\0','L')
    add('early stream end after first Adam7 row',{**spec,'interlace':True},zlib.compress(b'\0\x2a'),b'\x2a\0\0\0\0\0','L')
    assert len(cases)==803
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                data=file_bytes(case['spec'],base64.b64decode(case['data']))
                assert len(data)<=1024*1024, 'fixture crossed the resize threshold'
                uri='opaque,'+base64.b64encode(data).decode()
                expected=uri if case['jpeg'] is None else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected, case['name']
    codec=work/'png-image-codec';build_codec(codec,log,tool_env)
    path=work/'png-pixels.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPNG(Scanline|InvalidSpecifications)'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PNG_PIXELS_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec)},check=True,timeout=180)
    finally:path.unlink(missing_ok=True);codec.unlink(missing_ok=True)
    print(f'PNG pixel oracle: {len(cases)} explicit sample/filter/Adam7 and malformed-stream cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-png-pixels-',dir='/tmp') as directory:run(Path(directory),None,env)
