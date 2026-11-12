"""Declared SUN Raster layouts, byte RLE and lazy login file policy."""
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


def sun(w,h,depth=8,kind=1,palette=b'',ptype=1,plen=None,length=0):
    return struct.pack('>8I',0x59a66a95,w,h,depth,length,kind,ptype,len(palette) if plen is None else plen)+palette


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target):
        im=Image.frombytes('L' if mode=='1' else mode,size,pixels)
        if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=1,h=1,depth=8,kind=1,plen=0,pixels=None,invalid=False,lazy=False):
        mode={1:'1',4:'L',8:'L',24:'RGB',32:'RGB'}.get(depth,'')
        raw={1:'1;I',4:'L;4',8:'L',24:'RGB' if kind==3 else 'BGR',32:'RGBX' if kind==3 else 'BGRX'}.get(depth,'')
        if plen and mode=='L':mode='P';raw=raw.replace('L','P')
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.format=='SUN' and im.mode==mode and im.size==(w,h) and im.tile[0].offset==32+plen,(name,im.mode,im.size,im.tile)
            actual_palette=min(plen,max(0,len(data)-32))
            expected_header=dict(width=w,height=h,depth=depth,filetype=kind,stride=((w*depth+15)//16)*2,dataoffset=32+plen,headerbytes=32+actual_palette,mode=mode,rawmode=raw,haspalette=bool(plen),invalidpalette=bool(plen) and (mode!='P' or actual_palette//3>256),pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:80],pixels and pixels[:80])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        bounds=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if not invalid else None
        expected=None if invalid or lazy or pixels is None or mode=='P' or (bounds==(w,h) and target is None) else jpeg(mode,(w,h),pixels,target)
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
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode=mode,lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    def samples(row,w,d,kind,palette=False):
        if d==1:return bytes(255-((row[x//8]>>(7-x%8))&1)*255 for x in range(w))
        if d==4:return bytes(((row[x//2]>>(4 if x%2==0 else 0))&15)*(1 if palette else 17) for x in range(w))
        if d==8:return row[:w]
        return bytes(row[x*(d//8)+c] for x in range(w) for c in ((0,1,2) if kind==3 else (2,1,0)))
    def literal(raw):return raw.replace(b'\x80',b'\x80\x00')
    for d in (1,4,8,24,32):
        for kind in range(6):
            for w in (1,2,3,7,8,9,15,16,17,31,32,33):
                n=(w*d+7)//8;stride=((w*d+15)//16)*2
                rows=[bytes((x*29+y*47+128)%256 for x in range(n)) for y in range(3)]
                payload=literal(b''.join(rows)) if kind==2 else b''.join(r+b'\x99'*(stride-n) for r in rows[:-1])+rows[-1]
                pixels=b''.join(samples(r,w,d,kind) for r in rows)
                add(f'layout depth={d} type={kind} width={w}',sun(w,3,d,kind)+payload,w=w,h=3,depth=d,kind=kind,pixels=pixels)
                add(f'truncated layout depth={d} type={kind} width={w}',sun(w,3,d,kind)+payload[:-1],w=w,h=3,depth=d,kind=kind)
    for v in range(256):
        payload=literal(bytes([v]))
        add(f'literal value {v}',sun(1,1,8,2)+payload,kind=2,pixels=bytes([v]))
    for count in range(1,256):
        v=(count*23)%256
        add(f'run count {count}',sun(count+1,1,8,2)+bytes([128,count,v]),w=count+1,kind=2,pixels=bytes([v])*(count+1))
    for d in (1,4,8,24,32):
        for w in (1,3,7,17):
            n=(w*d+7)//8
            add(f'run crosses rows depth={d} width={w}',sun(w,3,d,2)+b'\x80\xff\x96',w=w,h=3,depth=d,kind=2,pixels=samples(b'\x96'*n,w,d,2)*3)
    for payload,pixels in [(b'',None),(b'\x80',None),(b'\x80\x01',None),(b'\x80\x00',b'\x80'),(b'\x80\xff\x27',b'\x27'),(b'\x19\x80',b'\x19'),(b'\x19\x80\x01',b'\x19')]:
        add(f'RLE tail {payload!r}',sun(1,1,8,2)+payload,kind=2,pixels=pixels)
    for boundary in (65534,65535,65536):
        for packet,decoded in ((b'\x80\x00',b'\x80'),(b'\x80\xff\x80',b'\x80'*256)):
            tail=257*257-boundary-len(decoded)
            payload=b'\x07'*boundary+packet+b'\x09'*tail
            pixels=b'\x07'*boundary+decoded+b'\x09'*tail
            add(f'RLE read boundary {boundary}/{len(packet)}',sun(257,257,8,2)+payload,w=257,h=257,kind=2,pixels=pixels)
            add(f'RLE truncated at boundary {boundary}/{len(packet)}',sun(257,257,8,2)+b'\x07'*boundary+packet[:-1],w=257,h=257,kind=2)
    for d in (1,4,8,24,32):
        for n in (1,2,3,6,768,769,770,771,1024):
            for kind in (1,2):
                raw=b'\xa5'*((3*d+7)//8)
                valid=d in (4,8) and n<771
                add(f'palette depth={d} length={n} type={kind}',sun(3,1,d,kind,b'\x10'*n)+(literal(raw) if kind==2 else raw),w=3,depth=d,kind=kind,plen=n,pixels=samples(raw,3,d,kind,True) if valid else None)
        for actual in (0,1,2,770,771):
            add(f'lazy partial palette depth={d} actual={actual}',sun(200,100,d,palette=b'\x01'*actual,plen=1024),w=200,h=100,depth=d,plen=1024,lazy=True)
    for kind in (6,255,65535,0xffffffff):add(f'unsupported type {kind}',sun(200,100,kind=kind),invalid=True)
    for d in (0,2,3,7,15,16,25,48,0xffffffff):add(f'unsupported depth {d}',sun(200,100,d),invalid=True)
    for ptype in (0,2,3,0xffffffff):
        add(f'no palette ignores type {ptype}',sun(1,1,ptype=ptype)+b'\x47',pixels=b'\x47')
        add(f'palette rejects type {ptype}',sun(200,100,palette=b'\0',ptype=ptype),invalid=True)
    for plen in (1025,65536,0xffffffff):add(f'palette bound {plen}',sun(200,100,plen=plen),invalid=True)
    for size in range(32):add(f'partial header {size}',sun(200,100)[:size],invalid=True)
    for length in (0,1,42,0xffffffff):add(f'ignored data length {length}',sun(3,1,length=length)+b'\x63'*3,w=3,pixels=b'\x63'*3)
    for w,h in ((0,1),(1,0),(178956971,1),(0xffffffff,0xffffffff)):add(f'invalid dimensions {w}/{h}',sun(w,h),invalid=True)
    add('pixel warning',sun(10000,10000),w=10000,h=10000)
    add('lazy without pixels',sun(200,100),w=200,h=100,lazy=True)
    for size in (1024*1024,1024*1024+1):
        data=sun(3,2)+b'\x10\x40\x90\0\x20\x50\xa0'
        add(f'file size threshold {size}',data+b'\0'*(size-len(data)),w=3,h=2,pixels=bytes([16,64,144,32,80,160]))
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'sun-codec';build_codec(codec,log,tool_env)
    fault=work/'sun-fault';build_fault_codec(fault,log,tool_env)
    path=work/'sun.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestSUN'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_SUN_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'SUN oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-sun-',dir='/tmp') as directory:run(Path(directory),None,env)
