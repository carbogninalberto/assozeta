"""Declared GIMP brush headers, raw samples, comments and login file policy."""
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


def gbr(w,h,depth=1,version=1,comment=b'',size=None,spacing=0,magic=b'GIMP'):
    fixed=20 if version==1 else 28
    return struct.pack('>5I',fixed+len(comment) if size is None else size,version,w,h,depth)+(magic+struct.pack('>I',spacing) if version!=1 else b'')+comment


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,comment,target):
        im=Image.frombytes(mode,size,pixels)
        if mode=='RGBA':
            white=Image.new('RGB',im.size,'white');white.paste(im,mask=im.getchannel('A'));im=white
        else:im.info['comment']=comment
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=1,h=1,depth=1,version=1,pixels=None,invalid=False,lazy=False):
        mode='L' if depth==1 else 'RGBA';fixed=20 if version==1 else 28
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            declared=struct.unpack_from('>I',data)[0]-fixed
            consumed=len(data)-fixed if declared<0 else min(declared,len(data)-fixed)
            comment=data[fixed:fixed+max(0,consumed-1)]
            offset=fixed+consumed
            assert im.format=='GBR' and im.mode==mode and im.size==(w,h) and im.fp.tell()==offset and im.info['comment']==comment,(name,im.format,im.size,im.info)
            expected_header=dict(width=w,height=h,depth=depth,version=version,mode=mode,headerbytes=offset,dataoffset=offset,commentoffset=fixed,commentbytes=max(0,consumed-1),pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.tobytes();assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:60],pixels and pixels[:60])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        bounds=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if not invalid else None
        expected=None
        if not (invalid or lazy or pixels is None or (bounds==(w,h) and target is None)):
            try:expected=jpeg(mode,(w,h),pixels,comment,target)
            except (OSError,ValueError):pass
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                if im.mode=='RGBA':
                    white=Image.new('RGB',im.size,'white');white.paste(im,mask=im.getchannel('A'));im=white
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode=mode,lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    for version in (1,2):
        for depth in (1,4):
            for w in (1,2,3,7,8,17,33,63,129,199,200,201):
                pixels=bytes((x*37+19)%256 for x in range(w*3*depth))
                data=gbr(w,3,depth,version,b'brush\0')+pixels
                add(f'layout version={version} depth={depth} width={w}',data,w=w,h=3,depth=depth,version=version,pixels=pixels)
                add(f'truncated pixels version={version} depth={depth} width={w}',data[:-1],w=w,h=3,depth=depth,version=version)
            for value in range(256):
                pixels=bytes([value])*depth
                add(f'sample {version}/{depth}/{value}',gbr(1,1,depth,version)+pixels,version=version,depth=depth,pixels=pixels)
            for comment in (b'',b'\0',b'abc\0',b'no terminator',b'\xff\0\x80\0',bytes(range(256))):
                pixels=b'\x71'*depth
                add(f'comment {version}/{depth}/{comment[:12]!r}',gbr(1,1,depth,version,comment)+pixels,version=version,depth=depth,pixels=pixels)
            for n in (65511,65512,65513,65533,65534):
                pixels=b'\x31'*depth
                add(f'comment save boundary {version}/{depth}/{n}',gbr(1,1,depth,version,b'x'*n+b'\0')+pixels,version=version,depth=depth,pixels=pixels)
            fixed=20 if version==1 else 28
            for declared in (20,21,24,27,28,29,32,0xffffffff):
                data=gbr(200,100,depth,version,b'partial',size=declared)
                add(f'lazy declared header size {version}/{depth}/{declared}',data,w=200,h=100,version=version,depth=depth,lazy=True)
            for n in range(fixed):add(f'short fixed header {version}/{depth}/{n}',gbr(200,100,depth,version)[:n],invalid=True)
    for size in (0,1,19):add(f'header minimum {size}',gbr(200,100,size=size),invalid=True)
    for version in (0,3,4,255,0xffffffff):add(f'unsupported version {version}',gbr(200,100,version=version),invalid=True)
    for depth in (0,2,3,5,8,18,24,32,0xffffffff):add(f'unsupported depth {depth}',gbr(200,100,depth),invalid=True)
    for w,h in ((0,1),(1,0),(178956971,1),(0xffffffff,0xffffffff)):add(f'invalid dimensions {w}/{h}',gbr(w,h),invalid=True)
    for spacing in (0,1,10,1000,0xffffffff):add(f'ignored spacing {spacing}',gbr(1,1,version=2,spacing=spacing)+b'\x72',version=2,pixels=b'\x72')
    for magic in (b'gimp',b'GIMX',b'\0'*4,b'\xff'*4):add(f'invalid magic {magic!r}',gbr(200,100,version=2,magic=magic),invalid=True)
    add('pixel warning',gbr(10000,10000),w=10000,h=10000)
    for version in (1,2):
        for depth in (1,4):
            add(f'lazy no pixels {version}/{depth}',gbr(200,100,depth,version),w=200,h=100,depth=depth,version=version,lazy=True)
            for size in (1024*1024,1024*1024+1):
                pixels=bytes((x*29)%256 for x in range(6*depth));data=gbr(3,2,depth,version,b'keep unless RGBA\0')+pixels
                add(f'size threshold {version}/{depth}/{size}',data+b'\0'*(size-len(data)),w=3,h=2,depth=depth,version=version,pixels=pixels)
    for n in (65511,65512,65515,65516,65533):
        pixels=b'\x6d'*16385
        add(f'wide JPEG comment buffer {n}',gbr(16385,1,comment=b'x'*n+b'\0')+pixels,w=16385,pixels=pixels)
    # Image.open tries GBR before SUN, despite this SUN magic.
    add('SUN signature recognized as GBR',struct.pack('>8I',0x59a66a95,1,1,8,1,1,1,0)+b'\x63',w=1,h=8)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'gbr-codec';build_codec(codec,log,tool_env)
    fault=work/'gbr-fault';build_fault_codec(fault,log,tool_env)
    path=work/'gbr.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestGBR'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_GBR_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'GBR oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-gbr-',dir='/tmp') as directory:run(Path(directory),None,env)
