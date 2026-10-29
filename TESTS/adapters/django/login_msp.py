"""Declared Windows Paint raw/RLE layouts and lazy login file behavior."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import warnings

ROOT=Path(__file__).resolve().parents[3]


def msp(w,h,rows=None,payload=b''):
    header=bytearray(b'LinS' if rows is not None else b'DanM')+bytearray(struct.pack('<HH',w,h))+bytearray(24)
    checksum=0
    for word in struct.unpack('<16H',header):checksum^=word
    struct.pack_into('<H',header,24,checksum)
    if rows is None:return bytes(header)+payload
    return bytes(header)+struct.pack('<'+'H'*len(rows),*map(len,rows))+b''.join(rows)+payload


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(size,pixels,target):
        im=Image.frombytes('L',size,pixels).convert('1',dither=Image.Dither.NONE)
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=8,h=1,pixels=None,invalid=False,lazy=False):
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.format=='MSP' and im.mode=='1' and im.size==(w,h) and im.tile[0].offset==32,(name,im.format,im.mode,im.size,im.tile)
            expected_header=dict(width=w,height=h,mode='1',rle=data[:4]==b'LinS',headerbytes=32,pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes();assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:60],pixels and pixels[:60])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        bounds=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if not invalid else None
        target=bounds if len(data)>1024*1024 and not invalid else None
        expected=None
        if not (invalid or lazy or pixels is None or (bounds==(w,h) and target is None)):
            try:expected=jpeg((w,h),pixels,target)
            except (OSError,ValueError):pass
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode='1',lazy=lazy,keep=expected is None,jpeg=enc(expected)))
    def bits(v,n=8):return bytes(((v>>(7-i))&1)*255 for i in range(n))
    for value in range(256):
        for kind in ('raw','repeat','literal'):
            data=msp(8,1,payload=bytes([value])) if kind=='raw' else msp(8,1,[bytes([0,1,value]) if kind=='repeat' else bytes([1,value])])
            add(f'packed byte {kind}/{value}',data,pixels=bits(value))
    for n in range(256):
        add(f'repeat count {n}',msp(max(1,n)*8,1,[bytes([0,n,0xa5])]),w=max(1,n)*8,pixels=bits(0xa5)*n if n else None)
    for n in range(1,256):
        raw=bytes((x*29+7)%256 for x in range(n));pixels=b''.join(bits(v) for v in raw)
        add(f'literal count {n}',msp(n*8,1,[bytes([n])+raw]),w=n*8,pixels=pixels)
        if n>1:add(f'clipped literal operand {n}',msp((n-1)*8,1,[bytes([n])+raw[:-1]]),w=(n-1)*8,pixels=pixels[:-8])
    for w in (1,2,3,7,8,9,15,16,17,31,32,33,63,64,65,127,128,129,65535):
        count=(w+7)//8;rows=[bytes((x*19+y*71)%256 for x in range(count)) for y in range(3)]
        pixels=b''.join(b''.join(bits(v) for v in row)[:w] for row in rows)
        add(f'raw row padding {w}',msp(w,3,payload=b''.join(rows)),w=w,h=3,pixels=pixels)
        add(f'truncated raw padding {w}',msp(w,3,payload=b''.join(rows)[:-1]),w=w,h=3)
        encoded=[b''.join(bytes([len(r[x:x+255])])+r[x:x+255] for x in range(0,len(r),255)) for r in rows]
        add(f'RLE row padding {w}',msp(w,3,encoded),w=w,h=3,pixels=pixels)
    for rows,raw in [
        ([b'',b''],b'\xff\xff'),([b'\x01\x80',b'\x01\x40'],b'\x80\x40'),
        ([b'\x02\x80\x40',b'\0\0\xff'],b'\x80\x40'),([b'\0\0\xff',b'\x02\x80\x40'],b'\x80\x40'),
        ([b'\x08\x80',b'\x01\x40'],b'\x80\x40'),([b'\x02\x80\x40',b'\0'],None),
        ([b'\x02\x80\x40',b'\0\0'],None),([b'\0\x04\xff',b'\0'],None),
        ([b'\0\x04\xff',b'\x01'],b'\xff\xff'),([b'\x01',b''],None),
        ([b'',b'\x01'],None),([b'\0\0\x77',b'\0\0\x88'],None),
        ([b'\0\x03\x80',b''],b'\x80\x80'),
    ]:add(f'concatenated rows {rows!r}',msp(8,2,rows),h=2,pixels=None if raw is None else b''.join(bits(v) for v in raw))
    for tail in (b'',b'\0',b'\0\1',b'\xff'*9):
        add(f'ignored RLE tail {tail!r}',msp(8,1,[b'\x01\x80'],tail),pixels=bits(128))
        add(f'ignored raw tail {tail!r}',msp(8,1,payload=b'\x80'+tail),pixels=bits(128))
    for n in range(7):add(f'truncated rowmap/payload {n}',msp(8,2,[b'\x01\x80',b'\x01\x40'])[:32+n],h=2)
    for n in range(32):add(f'short lazy header {n}',msp(200,100)[:n],invalid=True)
    header=msp(200,100)
    for index in range(32):
        data=bytearray(header);data[index]^=1;add(f'checksum byte {index}',bytes(data),invalid=True)
    for field in (8,10,12,14,16,18,20,22,26,28,30):
        data=bytearray(msp(8,1,payload=b'\x80'));struct.pack_into('<H',data,field,0xbeef)
        checksum=0
        for v in struct.unpack('<16H',data[:32]):checksum^=v
        struct.pack_into('<H',data,24,struct.unpack_from('<H',data,24)[0]^checksum)
        add(f'ignored header field {field}',bytes(data),pixels=bits(128))
    for w,h in ((0,1),(1,0),(65535,65535),(20000,10000)):add(f'invalid dimensions {w}/{h}',msp(w,h),invalid=True)
    add('pixel warning',msp(10000,10000),w=10000,h=10000)
    for compressed in (False,True):
        data=msp(200,100,[] if compressed else None)
        add(f'lazy no pixels RLE={compressed}',data,w=200,h=100,lazy=True)
        for size in (1024*1024,1024*1024+1):
            data=msp(3,2,[b'\1\xa0',b'\1\x40']) if compressed else msp(3,2,payload=b'\xa0\x40')
            add(f'size threshold RLE={compressed}/{size}',data+b'\0'*(size-len(data)),w=3,h=2,pixels=bytes([255,0,255,0,255,0]))
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'msp-codec';build_codec(codec,log,tool_env)
    fault=work/'msp-fault';build_fault_codec(fault,log,tool_env)
    path=work/'msp.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestMSP'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_MSP_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'MSP oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-msp-',dir='/tmp') as directory:run(Path(directory),None,env)
