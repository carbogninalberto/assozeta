"""Declared QOI admission, opcode samples and whole-file login outcomes."""
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


def qoi(w,h,channels=3,colorspace=0):
    return b'qoif'+struct.pack('>II',w,h)+bytes([channels,colorspace])


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target=None):
        im=Image.frombytes(mode,size,pixels)
        if mode=='RGBA':
            bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=1,h=1,channels=3,pixels=None,invalid=False,lazy=False):
        mode='RGB' if channels==3 else 'RGBA'
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.format=='QOI' and im.mode==mode and im.size==(w,h) and im.tile[0].offset==14,(name,im.format,im.mode,im.size,im.tile)
            expected_header=dict(width=w,height=h,mode=mode,dataoffset=14,headerbytes=13,pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:90],pixels and pixels[:90])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg(mode,(w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                if im.mode=='RGBA':
                    bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode=mode,lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    for channels in (3,4):
        c=channels
        for index in range(64):
            pixel=bytes(4)[:c]
            add(f'initial index {channels}/{index}',qoi(1,1,channels)+bytes([index]),channels=channels,pixels=pixel)
        add(f'initial run does not populate cache {channels}',qoi(2,1,channels)+b'\xc0\x35',w=2,channels=channels,pixels=bytes([0,0,0,255])[:c]+bytes(c))
        add(f'empty index replaces previous alpha {channels}',qoi(2,1,channels)+b'\x35\xc0',w=2,channels=channels,pixels=bytes(c*2))
        previous=bytes([1,254,255,127]);prefix=b'\xff'+previous
        for op in range(64):
            deltas=((op>>4)-2,((op>>2)&3)-2,(op&3)-2)
            value=bytes((previous[i]+deltas[i])%256 for i in range(3))+previous[3:]
            add(f'diff {channels}/{op}',qoi(2,1,channels)+prefix+bytes([64|op]),w=2,channels=channels,pixels=previous[:c]+value[:c])
        pairs={(g,second) for g in range(64) for second in (0,1,15,16,127,128,240,254,255)}|{(32,second) for second in range(256)}
        for green,second in sorted(pairs):
            deltas=(green-32+(second>>4)-8,green-32,green-32+(second&15)-8)
            value=bytes((previous[i]+deltas[i])%256 for i in range(3))+previous[3:]
            add(f'luma {channels}/{green}/{second}',qoi(2,1,channels)+prefix+bytes([128|green,second]),w=2,channels=channels,pixels=previous[:c]+value[:c])
        for n in range(1,63):
            payload=bytes([191+n]);pixel=bytes([0,0,0,255])[:c]
            add(f'initial run {channels}/{n}',qoi(n,1,channels)+payload,w=n,channels=channels,pixels=pixel*n)
            add(f'run overshoot {channels}/{n}',qoi(1,1,channels)+payload,channels=channels,pixels=pixel)
        for w,h in ((2,3),(3,2),(1,62),(62,1)):
            n=w*h;pixel=previous[:c]
            add(f'run crosses rows {channels}/{w}/{h}',qoi(w,h,channels)+prefix+bytes([190+n]),w=w,h=h,channels=channels,pixels=pixel*n)
        for alpha in (0,1,127,128,254,255):
            rgba=bytes([10,20,30,alpha]);rgb=bytes([40,50,60]);last=rgb+bytes([alpha])
            payload=b'\xff'+rgba+b'\xfe'+rgb
            add(f'RGB preserves alpha {channels}/{alpha}',qoi(2,1,channels)+payload,w=2,channels=channels,pixels=rgba[:c]+last[:c])
            index=(40*3+50*5+60*7+alpha*11)%64
            add(f'alpha participates in cache {channels}/{alpha}',qoi(3,1,channels)+payload+bytes([index]),w=3,channels=channels,pixels=rgba[:c]+last[:c]*2)
        # 64 red and opaque black have the same hash. A later RGBA alpha
        # replacement uses a different slot even when output mode is RGB.
        collision=b'\xfe\x40\0\0\x35\xff\0\0\0\0\x35\0'
        pixels=bytes([64,0,0,255])[:c]*2+bytes(4)[:c]+bytes([64,0,0,255])[:c]+bytes(4)[:c]
        add(f'cache collision and alpha {channels}',qoi(5,1,channels)+collision,w=5,channels=channels,pixels=pixels)
        for payload in (b'',b'\xfe',b'\xfe\1',b'\xfe\1\2',b'\xff',b'\xff\1',b'\xff\1\2',b'\xff\1\2\3',b'\x80',b'\xbf'):
            add(f'truncated opcode {channels}/{payload.hex()}',qoi(1,1,channels)+payload,channels=channels)
        for tail in (b'',b'\0'*7+b'\1',b'\xff',bytes(range(256)),b'not a valid footer'):
            add(f'footer ignored {channels}/{len(tail)}',qoi(1,1,channels)+b'\xc0'+tail,channels=channels,pixels=bytes([0,0,0,255])[:c])
        # Explicit nonuniform rows and the strict byte-size threshold.
        pixels=b''.join(bytes([x*31,y*71,(x+y)*23,(x+y)*47])[:c] for y in range(2) for x in range(3))
        payload=b''.join(b'\xff'+bytes([x*31,y*71,(x+y)*23,(x+y)*47]) for y in range(2) for x in range(3))
        data=qoi(3,2,channels)+payload
        for size in (len(data),1024*1024,1024*1024+1):add(f'size threshold {channels}/{size}',data+bytes(size-len(data)),w=3,h=2,channels=channels,pixels=pixels)
        for length in (13,14):add(f'lazy header {channels}/{length}',qoi(200,100,channels)[:length],w=200,h=100,channels=channels,lazy=True)
    for channels in range(256):
        pixel=bytes([10,20,30,40])[:3 if channels==3 else 4]
        add(f'channel value {channels}',qoi(1,1,channels)+b'\xff\x0a\x14\x1e\x28',channels=channels,pixels=pixel)
    for colorspace in range(256):
        add(f'colorspace ignored {colorspace}',qoi(1,1,colorspace=colorspace)+b'\xc0',pixels=bytes(3))
    for n in range(13):add(f'short header {n}',qoi(200,100)[:n],invalid=True)
    for width,height in ((0,1),(1,0),(0,0),(0xffffffff,1),(1,0xffffffff),(0xffffffff,0xffffffff)):
        add(f'invalid dimensions {width}/{height}',qoi(width,height),invalid=True)
    add('pixel warning',qoi(10000,10000),w=10000,h=10000)
    add('hard pixel ceiling',qoi(178956971,1),invalid=True)
    add('invalid magic',b'nope'+qoi(1,1)[4:],invalid=True)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'qoi-codec';build_codec(codec,log,tool_env)
    fault=work/'qoi-fault';build_fault_codec(fault,log,tool_env)
    path=work/'qoi.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestQOI'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_QOI_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'QOI oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-qoi-',dir='/tmp') as directory:run(Path(directory),None,env)
